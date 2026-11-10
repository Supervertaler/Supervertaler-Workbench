"""
Phrase (Memsource) MXLIFF handler
=================================

Reads and writes ``.mxliff`` files: the XLIFF 1.2 dialect Phrase TMS hands
out for offline work. Phrase's own data sits in attributes and elements of
its ``m:`` namespace (``http://www.memsource.com/mxlf/2.0``):

- each ``<trans-unit>`` carries ``m:confirmed`` ("0"/"1"), ``m:locked``
  ("true"/"false"), ``m:score``, ``m:trans-origin`` ("tm", "mt", ...) and
  timestamps/user ids;
- ``<alt-trans>`` siblings hold the MT and TM suggestions, and
  ``<m:latest-trans-props>`` the provenance of the current target;
- the ``<header>`` holds an in-context preview skeleton as CDATA, which can be
  hundreds of kilobytes.

Inline formatting is *text* in MXLIFF, not XLIFF elements: ``{b>bold<b}``,
``{i>...<i}``, ``{1}``, ``{2>...<2}``. Segments keep these tags as written, as
the Phrase bilingual DOCX import does, and they go back unchanged.

Export does not re-serialise the XML. It splices new ``<target>`` contents
(and ``m:confirmed``) into the original text of the file, unit by unit, so
everything Workbench did not change - the CDATA skeleton, ``alt-trans``,
attribute order, quoting, whitespace - stays byte-for-byte as Phrase wrote
it. A file exported without changes is identical to the original.

Units whose source or target contain XLIFF inline *elements* (which Phrase
does not normally write) are imported read-only and never rewritten.
"""

from __future__ import annotations

import html
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from lxml import etree

XLIFF_NS = 'urn:oasis:names:tc:xliff:document:1.2'
MEMSOURCE_NS_PREFIX = 'http://www.memsource.com/mxlf/'
KNOWN_MEMSOURCE_NS = 'http://www.memsource.com/mxlf/2.0'

# Workbench statuses that mean "confirmed" for Phrase (m:confirmed="1")
CONFIRMED_STATUSES = frozenset({'confirmed', 'proofread', 'approved'})

# CDATA, comments and processing instructions are blanked out before the raw
# text is scanned for <trans-unit>s, so a "<trans-unit" inside the preview
# skeleton can never be mistaken for a real one.
_OPAQUE_RE = re.compile(r'<!\[CDATA\[.*?\]\]>|<!--.*?-->|<\?.*?\?>', re.S)
# A start tag; attribute values may contain ">" but not "<"
_ATTRS = r'(?:[^>"\']|"[^"]*"|\'[^\']*\')*'
_UNIT_START_RE = re.compile(r'<trans-unit\b' + _ATTRS + r'>')
_TARGET_AFTER_SOURCE_RE = re.compile(
    r'</source>(\s*)(<target\b(' + _ATTRS + r')>)')
_ID_ATTR_RE = re.compile(r'(?<![\w:.-])id\s*=\s*("([^"]*)"|\'([^\']*)\')')
# Characters XML 1.0 does not allow at all
_ILLEGAL_XML_RE = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]')


class MxliffError(Exception):
    """The file cannot be read, or cannot safely be written back."""


@dataclass
class MxliffUnit:
    id: str
    source: str
    target: str
    confirmed: bool
    locked: bool
    status: str                 # Workbench status key
    match_percent: Optional[int]
    origin: str                 # m:trans-origin ("tm", "mt", ...)
    editable: bool = True       # False when the unit holds XLIFF inline elements
    # Offsets into the original text, filled by the raw scan
    start_tag: Tuple[int, int] = (0, 0)
    end: int = 0


@dataclass
class MxliffDocument:
    path: str
    text: str
    units: List[MxliffUnit]
    source_lang: str
    target_lang: str
    ns: str
    prefix: str
    warnings: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: str) -> 'MxliffDocument':
        try:
            with open(path, 'rb') as fh:
                raw = fh.read()
        except OSError as e:
            raise MxliffError(f"Could not read the file: {e}") from e
        if not raw.strip():
            raise MxliffError("The file is empty.")
        return cls.from_bytes(raw, path)

    @classmethod
    def from_bytes(cls, raw: bytes, path: str = '') -> 'MxliffDocument':
        parser = etree.XMLParser(resolve_entities=False, no_network=True,
                                 load_dtd=False, huge_tree=True)
        try:
            root = etree.fromstring(raw, parser)
        except etree.XMLSyntaxError as e:
            raise MxliffError(f"The file is not well-formed XML "
                              f"(line {e.lineno}): {e.msg}") from e

        if root.tag != f'{{{XLIFF_NS}}}xliff':
            raise MxliffError("This is not an XLIFF 1.2 file "
                              f"(root element: {etree.QName(root).localname}).")
        if root.get('version', '1.2') != '1.2':
            raise MxliffError(f"Unsupported XLIFF version {root.get('version')!r}; "
                              "Phrase MXLIFF is XLIFF 1.2.")

        ns = prefix = None
        for pfx, uri in (root.nsmap or {}).items():
            if pfx and uri and uri.startswith(MEMSOURCE_NS_PREFIX):
                ns, prefix = uri, pfx
                break
        if ns is None:
            raise MxliffError(
                "This XLIFF file has no Phrase (Memsource) namespace, so it is "
                "not an MXLIFF. Use File > Import > Import Document... for "
                "generic XLIFF.")

        warnings = []
        if ns != KNOWN_MEMSOURCE_NS:
            warnings.append(
                f"This file uses Phrase namespace {ns}; Workbench knows "
                f"{KNOWN_MEMSOURCE_NS}. Translations and the confirmed flag "
                "are written the same way, but check the file in Phrase after "
                "uploading.")

        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError as e:
            raise MxliffError("Only UTF-8 MXLIFF files are supported "
                              f"(byte {e.start} is not UTF-8).") from e

        m = lambda name: f'{{{ns}}}{name}'
        files = root.findall(f'{{{XLIFF_NS}}}file')
        source_lang = target_lang = ''
        for f in files:
            if f.get('source-language') and f.get('target-language'):
                source_lang, target_lang = f.get('source-language'), f.get('target-language')
                break

        units: List[MxliffUnit] = []
        for tu in root.iter(f'{{{XLIFF_NS}}}trans-unit'):
            src = tu.find(f'{{{XLIFF_NS}}}source')
            tgt = tu.find(f'{{{XLIFF_NS}}}target')
            editable = (src is not None and len(src) == 0
                        and (tgt is None or len(tgt) == 0))
            source = ''.join(src.itertext()) if src is not None else ''
            target = ''.join(tgt.itertext()) if tgt is not None else ''
            confirmed = tu.get(m('confirmed'), '0') == '1'
            locked = (tu.get(m('locked'), 'false').lower() == 'true'
                      or tu.get('translate') == 'no' or not editable)
            origin = tu.get(m('trans-origin'), '') or ''
            percent = None
            try:
                percent = int(round(float(tu.get(m('score'), '')) * 100))
            except ValueError:
                pass
            units.append(MxliffUnit(
                id=tu.get('id', ''), source=source, target=target,
                confirmed=confirmed, locked=locked,
                status=_workbench_status(target, confirmed, origin),
                match_percent=percent, origin=origin, editable=editable))

        if not units:
            raise MxliffError("No segments (trans-units) found in the file.")
        if any(not u.editable for u in units):
            warnings.append(
                f"{sum(not u.editable for u in units)} segment(s) contain XLIFF "
                "inline markup Workbench does not edit; they are locked and "
                "will go back to Phrase unchanged.")

        doc = cls(path=path, text=text, units=units, source_lang=source_lang,
                  target_lang=target_lang, ns=ns, prefix=prefix, warnings=warnings)
        doc._locate_units()
        return doc

    def _locate_units(self):
        """Find each unit's start tag and end in the original text."""
        masked = _OPAQUE_RE.sub(lambda mo: ' ' * len(mo.group()), self.text)
        pos, found = 0, []
        while True:
            mo = _UNIT_START_RE.search(masked, pos)
            if not mo:
                break
            if mo.group().endswith('/>'):
                end = mo.end()
            else:
                close = masked.find('</trans-unit>', mo.end())
                if close < 0:
                    raise MxliffError("A <trans-unit> is not closed.")
                end = close + len('</trans-unit>')
            found.append((mo.start(), mo.end(), end))
            pos = end
        if len(found) != len(self.units):
            raise MxliffError(
                f"The file's structure is not understood ({len(found)} units "
                f"found in the text, {len(self.units)} parsed).")
        for unit, (s, e, end) in zip(self.units, found):
            mo = _ID_ATTR_RE.search(self.text, s, e)
            raw_id = html.unescape(mo.group(2) if mo and mo.group(2) is not None
                                   else (mo.group(3) if mo else ''))
            if raw_id != unit.id:
                raise MxliffError(f"The file's structure is not understood "
                                  f"(unit {unit.id!r} found as {raw_id!r}).")
            unit.start_tag, unit.end = (s, e), end

    # ---------------------------------------------------------------- export
    def render(self, translations: Sequence[str],
               statuses: Optional[Sequence[str]] = None,
               sources: Optional[Sequence[str]] = None) -> Tuple[str, int]:
        """The file text with ``translations`` written in, and how many units changed.

        ``translations[i]``/``statuses[i]`` belong to ``units[i]``. With
        ``sources`` (the project's source texts) every one is checked against
        the file first, so translations can never land in the wrong unit.
        Empty translations, locked units and units whose text and confirmed
        flag are unchanged are left exactly as they are.
        """
        n = len(self.units)
        if len(translations) != n:
            raise MxliffError(
                f"The project has {len(translations)} segments but the MXLIFF has "
                f"{n}. Was it split or joined, or is this a different file?")
        if sources is not None:
            for i, (unit, src) in enumerate(zip(self.units, sources), start=1):
                if (src or '').strip() != unit.source.strip():
                    raise MxliffError(
                        f"Segment {i}'s source text does not match the MXLIFF "
                        "file. Is this the file the project was imported from?")

        edits = []   # (start, end, replacement), in file order
        changed = 0
        for i, unit in enumerate(self.units):
            if unit.locked or not unit.editable:
                continue
            translation = _ILLEGAL_XML_RE.sub('', translations[i] or '')
            if not translation.strip():
                continue
            status = statuses[i] if statuses is not None else 'confirmed'
            confirmed = status in CONFIRMED_STATUSES
            text_changed = translation != unit.target
            if not text_changed and confirmed == unit.confirmed:
                continue
            changed += 1
            s, e = unit.start_tag
            start_tag = self.text[s:e]
            if confirmed != unit.confirmed:
                edits.append((s, e, self._set_confirmed(start_tag, confirmed)))
            if text_changed:
                edits.append(self._target_edit(unit, translation))

        parts, pos = [], 0
        for s, e, rep in edits:
            parts.append(self.text[pos:s])
            parts.append(rep)
            pos = e
        parts.append(self.text[pos:])
        return ''.join(parts), changed

    def save(self, output_path: str, translations: Sequence[str],
             statuses: Optional[Sequence[str]] = None,
             sources: Optional[Sequence[str]] = None) -> int:
        """Write the translated file to ``output_path``. Returns units changed.

        The result is checked to be well-formed before it is written, and is
        written to a temporary file first, so a failure never leaves a
        half-written or broken file behind (nor overwrites a good one).
        """
        text, changed = self.render(translations, statuses, sources)
        data = text.encode('utf-8')
        try:
            etree.fromstring(data, etree.XMLParser(resolve_entities=False,
                                                   no_network=True, huge_tree=True))
        except etree.XMLSyntaxError as e:   # a bug here, not in the user's file
            raise MxliffError(f"Internal error: the exported file would not be "
                              f"well-formed ({e.msg}); nothing was written.") from e
        folder = os.path.dirname(os.path.abspath(output_path))
        fd, tmp = tempfile.mkstemp(prefix='.mxliff-', suffix='.tmp', dir=folder)
        try:
            with os.fdopen(fd, 'wb') as fh:
                fh.write(data)
            os.replace(tmp, output_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return changed

    def _set_confirmed(self, start_tag: str, confirmed: bool) -> str:
        value = '1' if confirmed else '0'
        attr = f'{self.prefix}:confirmed'
        new, count = re.subn(r'(\s' + re.escape(attr) + r'\s*=\s*)("[^"]*"|\'[^\']*\')',
                             lambda mo: f'{mo.group(1)}"{value}"', start_tag, count=1)
        if count:
            return new
        return start_tag[:-1].rstrip() + f' {attr}="{value}">'

    def _target_edit(self, unit: MxliffUnit, translation: str) -> Tuple[int, int, str]:
        s_end, end = unit.start_tag[1], unit.end
        body = self.text[s_end:end]
        escaped = (translation.replace('&', '&amp;').replace('<', '&lt;')
                   .replace('>', '&gt;'))
        # Only the <target> right after the unit's own (first) </source>: one
        # further on may belong to an <alt-trans>.
        src_close = body.find('</source>')
        if src_close < 0:
            raise MxliffError(f"Unit {unit.id!r} has no <source>.")
        mo = _TARGET_AFTER_SOURCE_RE.match(body, src_close)
        if mo:
            tag = mo.group(2)
            attrs = mo.group(3)
            if tag.endswith('/>'):
                t_start, t_end = s_end + mo.start(2), s_end + mo.end(2)
                open_tag = '<target' + attrs.rstrip().rstrip('/') + '>'
            else:
                t_start = s_end + mo.start(2)
                close = self.text.find('</target>', s_end + mo.end(2), end)
                if close < 0:
                    raise MxliffError(f"Unit {unit.id!r} has an unclosed <target>.")
                t_end = close + len('</target>')
                open_tag = tag
            return t_start, t_end, f'{open_tag}{escaped}</target>'
        at =s_end + src_close + len('</source>')
        return at, at, f'\n<target>{escaped}</target>'


def _workbench_status(target: str, confirmed: bool, origin: str) -> str:
    if not target.strip():
        return 'not_started'
    if confirmed:
        return 'confirmed'
    if origin == 'mt':
        return 'machine_translated'
    if origin == 'tm':
        return 'pretranslated'
    return 'draft'


def looks_like_mxliff(path: str) -> bool:
    """Cheap check on the first few KB: XLIFF with the Phrase namespace."""
    try:
        with open(path, 'rb') as fh:
            head = fh.read(4096)
    except OSError:
        return False
    return b'<xliff' in head and MEMSOURCE_NS_PREFIX.encode() in head
