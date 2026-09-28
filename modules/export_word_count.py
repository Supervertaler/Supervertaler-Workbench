"""
Export Word Count
=================

Reads a freshly exported translation back and counts the words it contains,
so Supervertaler can warn when an export looks as if it silently dropped text
(the post-export safeguard added in v1.10.254 for DOCX; issue #219 extends it
to the other formats the Okapi merge produces).

Every counter errs on the side of counting MORE: the caller only warns when the
file holds clearly fewer words than the segments, so over-counting can only
make the check less sensitive, while under-counting would raise false alarms.
That is why, for example, HTML ``alt``/``title`` text is counted (Okapi
translates it) and an untranslated XLIFF or PO unit counts its source.

``count_exported_words`` returns ``None`` for formats it has no counter for, or
when the file cannot be read; the caller then skips the check.
"""

import html
import re
import zipfile
from typing import Callable, Dict, List, Optional
from xml.etree import ElementTree


def count_words(text: str) -> int:
    """Whitespace-delimited word tokens, ignoring inline/markup tags (<b>,
    <cf …>, <x1/>, {1}, [1}, …) so a segment's target text and the text read
    back from a file are counted the same way."""
    if not text:
        return 0
    text = re.sub(r'<[^>]+>', ' ', text)          # angle-bracket tags
    text = re.sub(r'[{\[]\d+[}\]]', ' ', text)    # {1} / [1} placeholders
    return len([t for t in text.split() if t])


_XML_ENTITIES = (('&lt;', '<'), ('&gt;', '>'), ('&quot;', '"'), ('&apos;', "'"), ('&amp;', '&'))


def _unescape_xml(text: str) -> str:
    for entity, char in _XML_ENTITIES:
        text = text.replace(entity, char)
    return text


def _zip_element_text(path: str, part_pattern: str, element_pattern: str) -> str:
    """Joined text of every ``element_pattern`` match in the zip parts whose
    names match ``part_pattern`` (OOXML / IDML packages)."""
    chunks: List[str] = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if re.match(part_pattern, name):
                xml = z.read(name).decode('utf-8', 'replace')
                chunks.extend(re.findall(element_pattern, xml, flags=re.DOTALL))
    return _unescape_xml(' '.join(chunks))


def _count_docx(path: str) -> int:
    # Body, headers, footers, foot/endnotes – everything that can be a
    # translatable segment – but not review comments.
    return count_words(_zip_element_text(
        path, r'word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$',
        r'<w:t\b[^>]*>(.*?)</w:t>'))


def _count_pptx(path: str) -> int:
    # All DrawingML text in the package: slides, layouts/masters, notes,
    # charts and SmartArt all use <a:t>.
    return count_words(_zip_element_text(path, r'ppt/.+\.xml$', r'<a:t\b[^>]*>(.*?)</a:t>'))


def _count_xlsx(path: str) -> int:
    # Shared strings and inline strings (<t>), plus shape / chart text (<a:t>).
    return count_words(_zip_element_text(path, r'xl/.+\.xml$', r'<(?:a:)?t\b[^>]*>(.*?)</(?:a:)?t>'))


def _count_idml(path: str) -> int:
    return count_words(_zip_element_text(path, r'Stories/.+\.xml$', r'<Content\b[^>]*>(.*?)</Content>'))


_HTML_TEXT_ATTRS = ('alt', 'title', 'placeholder', 'summary', 'label', 'content', 'value')


def _count_html(path: str) -> int:
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        markup = f.read()
    markup = re.sub(r'<!--.*?-->', ' ', markup, flags=re.DOTALL)
    markup = re.sub(r'<(script|style)\b.*?</\1\s*>', ' ', markup, flags=re.DOTALL | re.IGNORECASE)
    attrs = re.findall(r'\b(?:%s)\s*=\s*("[^"]*"|\'[^\']*\')' % '|'.join(_HTML_TEXT_ATTRS),
                       markup, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', markup)
    return count_words(html.unescape(' '.join([text] + [a[1:-1] for a in attrs])))


def _local(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


def _text_of(element) -> str:
    return ' '.join(element.itertext()) if element is not None else ''


def _count_xliff(path: str) -> int:
    """Target text of each unit, or its source where the target is empty
    (XLIFF 1.2 <trans-unit>, 2.x <segment>)."""
    root = ElementTree.parse(path).getroot()
    total = 0
    for unit in root.iter():
        if _local(unit.tag) not in ('trans-unit', 'segment'):
            continue
        source = target = None
        for child in unit:
            name = _local(child.tag)
            if name == 'source':
                source = child
            elif name == 'target':
                target = child
        target_text = _text_of(target)
        total += count_words(target_text if target_text.strip() else _text_of(source))
    return total


def _count_po(path: str) -> int:
    """msgstr of each entry, or its msgid where untranslated (plurals included)."""
    from modules.po_handler import POHandler
    handler = POHandler()
    if not handler.load(path):
        raise ValueError("not a readable .po file")
    total = 0
    for seg in handler.extract_bilingual_segments():
        target = seg.get('target') or ''
        total += count_words(target if target.strip() else seg.get('source') or '')
    return total


_COUNTERS: Dict[str, Callable[[str], int]] = {
    '.docx': _count_docx,
    '.pptx': _count_pptx,
    '.xlsx': _count_xlsx,
    '.idml': _count_idml,
    '.html': _count_html,
    '.htm': _count_html,
    '.xhtml': _count_html,
    '.xlf': _count_xliff,
    '.xliff': _count_xliff,
    '.po': _count_po,
}


def supported(path: str) -> bool:
    return bool(path) and any(path.lower().endswith(ext) for ext in _COUNTERS)


def count_exported_words(path: str) -> Optional[int]:
    """Words in the exported file at ``path``, or None when its format has no
    counter. Raises on an unreadable file of a supported format, so the
    caller can report why the check was skipped."""
    if not path:
        return None
    lower = path.lower()
    for ext, counter in _COUNTERS.items():
        if lower.endswith(ext):
            return counter(path)
    return None
