"""
MQXLIFF Handler Module
======================
Handles import/export of memoQ XLIFF (.mqxliff) files.

MQXLIFF is XLIFF 1.2 with memoQ extensions (the ``mq:`` namespace) for status
and other CAT metadata. What this module relies on:

- **Every ``<file>`` is read.** An export of a single document has one; an
  export of a memoQ *view* has one per document in the view (issue #110), and
  reading only the first lost every segment after it.
- **Inline codes become numbered tags.** memoQ writes formatting and other
  inline codes as ``<bpt>``/``<ept>`` pairs and ``<ph>``/``<it>``/``<x>``
  placeholders whose *content* is the native code (``{}`` or an escaped
  ``<x id="..." mq:catalogvalue="..."/>``). That content is not text: it
  used to leak into the segment. The segment text now shows the codes as the
  grid's usual tags – ``<1>``…``</1>`` for a pair, ``<2/>`` for a
  placeholder – numbered in order of appearance.
- **Targets are rebuilt from those tags on export,** each tag becoming a copy
  of the source code it stands for, so formatting and placeholders go back to
  memoQ where the translator put them.

Formatting Tag Structure:
- <bpt id="X" ctype="bold">{}</bpt>...<ept id="X">{}</ept> - Bold text
- <bpt id="X" ctype="italic">{}</bpt>...<ept id="X">{}</ept> - Italic text
- <bpt id="X" ctype="underlined">{}</bpt>...<ept id="X">{}</ept> - Underlined text
- <ph id="X">&lt;mq:ch val="..." /&gt;</ph> - Placeholder (field, special character, ...)
"""

import copy
import xml.etree.ElementTree as ET
from typing import List, Dict, Tuple, Optional
import re

XLIFF_NS = 'urn:oasis:names:tc:xliff:document:1.2'
MQ_NS = 'MQXliff'
_XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'

# Inline codes whose content is native code, not translatable text
_CODE_ELEMENTS = {'bpt', 'ept', 'ph', 'it', 'x', 'bx', 'ex'}

# A grid tag standing for an inline code: <1>, </1> or <1/>
_TAG_RE = re.compile(r'<(/?)(\d+)(/?)>')

# memoQ segment status (mq:status) → Workbench status key. Matched on the
# lower-cased value; the first entry contained in it wins.
_MEMOQ_TO_STATUS = (
    ('reviewer2confirmed', 'approved'),
    ('reviewer1confirmed', 'proofread'),
    ('proofread', 'proofread'),
    ('reviewed', 'proofread'),
    ('confirmed', 'confirmed'),          # ManuallyConfirmed, Confirmed
    ('rejected', 'rejected'),
    ('machinetranslated', 'machine_translated'),
    ('pretranslated', 'pretranslated'),
    ('assembledfromfragments', 'pretranslated'),
    ('edit', 'draft'),                   # PartiallyEdited, Editing
)

# Workbench status key → mq:status written on export
_STATUS_TO_MEMOQ = {
    'confirmed': 'ManuallyConfirmed',
    'proofread': 'Reviewer1Confirmed',
    'approved': 'Reviewer2Confirmed',
    'rejected': 'Rejected',
}
_EDITED = 'PartiallyEdited'


def _local(tag) -> str:
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else ''


def workbench_status(mq_status: str, target_text: str) -> str:
    """The Workbench status for a memoQ ``mq:status`` and target."""
    if not (target_text or '').strip():
        return 'not_started'
    lower = (mq_status or '').lower()
    for needle, key in _MEMOQ_TO_STATUS:
        if needle in lower:
            return key
    return 'pretranslated'  # a target that memoQ calls not started, or no known status


def memoq_status(status_key: str) -> str:
    """The ``mq:status`` to write for a Workbench status."""
    return _STATUS_TO_MEMOQ.get(status_key or '', _EDITED)


def tagged_content(element: ET.Element) -> Tuple[str, Dict[int, Dict]]:
    """Text of a ``<source>``/``<target>`` with inline codes as numbered tags.

    Returns the text and a table ``{number: entry}`` describing what each tag
    stands for: ``{'kind': 'pair', 'open': bpt, 'close': ept or None}``,
    ``{'kind': 'single', 'elem': ...}`` or ``{'kind': 'container', 'elem': ...}``
    (``<g>``, ``<mrk>`` and other elements with text inside them).
    """
    parts: List[str] = []
    table: Dict[int, Dict] = {}
    open_pairs: Dict[str, int] = {}
    counter = [0]

    def number() -> int:
        counter[0] += 1
        return counter[0]

    def walk(elem):
        if elem.text:
            parts.append(elem.text)
        for child in elem:
            name = _local(child.tag)
            if name == 'bpt':
                n = number()
                table[n] = {'kind': 'pair', 'open': child, 'close': None}
                open_pairs[child.get('rid') or child.get('id') or f'#{n}'] = n
                parts.append(f'<{n}>')
            elif name == 'ept':
                n = open_pairs.pop(child.get('rid') or child.get('id') or '', None)
                if n is not None:
                    table[n]['close'] = child
                    parts.append(f'</{n}>')
                else:  # its opening code is in another segment
                    n = number()
                    table[n] = {'kind': 'single', 'elem': child}
                    parts.append(f'<{n}/>')
            elif name in _CODE_ELEMENTS or not name:
                n = number()
                table[n] = {'kind': 'single', 'elem': child}
                parts.append(f'<{n}/>')
            else:
                n = number()
                table[n] = {'kind': 'container', 'elem': child}
                if child.text or len(child):
                    parts.append(f'<{n}>')
                    walk(child)
                    parts.append(f'</{n}>')
                else:
                    parts.append(f'<{n}/>')
            if child.tail:
                parts.append(child.tail)

    walk(element)
    return ''.join(parts), table


def plain_text(tagged: str) -> str:
    """Segment text without its inline-code tags."""
    return _TAG_RE.sub('', tagged or '')


def _clone(elem: ET.Element) -> ET.Element:
    clone = copy.deepcopy(elem)
    clone.tail = None
    return clone


def _append_text(parent: ET.Element, text: str):
    if not text:
        return
    if len(parent):
        last = parent[-1]
        last.tail = (last.tail or '') + text
    else:
        parent.text = (parent.text or '') + text


def fill_target(source_elem: ET.Element, target_elem: ET.Element, translation: str):
    """Write ``translation`` into ``target_elem``, turning its tags back into
    copies of the source's inline codes.

    Tags the source doesn't have are dropped; a code pair whose closing tag
    is missing is closed at the end. A translation without any tags keeps the
    source's codes only when the source text sits in one piece around them
    (e.g. a bold segment, or a placeholder in front of the text); otherwise
    it is written as plain text and memoQ's tag check reports the missing
    codes.
    """
    _, table = tagged_content(source_elem)

    for child in list(target_elem):
        target_elem.remove(child)
    target_elem.text = None
    space = source_elem.get(_XML_SPACE)
    if space:
        target_elem.set(_XML_SPACE, space)

    if table and not _TAG_RE.search(translation or ''):
        if _fill_single_slot(source_elem, target_elem, translation):
            return
        table = {}

    stack = [target_elem]
    stack_numbers: List[Optional[int]] = [None]
    used, closed = set(), set()
    pos = 0
    for m in _TAG_RE.finditer(translation or ''):
        _append_text(stack[-1], translation[pos:m.start()])
        pos = m.end()
        closing, n, self_closing = m.group(1) == '/', int(m.group(2)), m.group(3) == '/'
        entry = table.get(n)
        if entry is None:
            continue
        kind = entry['kind']
        if kind == 'pair':
            if closing:
                if entry['close'] is not None and n not in closed:
                    stack[-1].append(_clone(entry['close']))
                    closed.add(n)
            elif not self_closing and n not in used:
                stack[-1].append(_clone(entry['open']))
                used.add(n)
        elif kind == 'single':
            if n not in used:
                stack[-1].append(_clone(entry['elem']))
                used.add(n)
        else:  # container
            if closing:
                if n in stack_numbers:
                    while stack_numbers[-1] != n:
                        stack.pop()
                        stack_numbers.pop()
                    stack.pop()
                    stack_numbers.pop()
            elif n not in used:
                used.add(n)
                shell = ET.Element(entry['elem'].tag, dict(entry['elem'].attrib))
                stack[-1].append(shell)
                if not self_closing:
                    stack.append(shell)
                    stack_numbers.append(n)
    _append_text(stack[-1], (translation or '')[pos:])

    for n in sorted(used):
        entry = table[n]
        if entry['kind'] == 'pair' and entry['close'] is not None and n not in closed:
            target_elem.append(_clone(entry['close']))


def _fill_single_slot(source_elem: ET.Element, target_elem: ET.Element, translation: str) -> bool:
    """Copy the source's codes around ``translation`` when all the source text
    is in one text node between them. Returns False when it isn't."""
    children = list(source_elem)
    if any(_local(c.tag) not in _CODE_ELEMENTS for c in children):
        return False
    slots = [i for i, text in enumerate([source_elem.text] + [c.tail for c in children])
             if text and text.strip()]
    if len(slots) != 1:
        return False
    slot = slots[0]
    target_elem.text = translation if slot == 0 else source_elem.text
    for i, child in enumerate(children, start=1):
        clone = _clone(child)
        clone.tail = translation if i == slot else child.tail
        target_elem.append(clone)
    return True


class FormattedSegment:
    """Represents a segment with inline formatting information."""
    
    def __init__(self, segment_id: str, plain_text: str, formatted_xml: str):
        """
        Initialize a formatted segment.
        
        Args:
            segment_id: Unique identifier for the segment (trans-unit id)
            plain_text: Plain text without any formatting tags
            formatted_xml: XML string with formatting tags preserved
        """
        self.id = segment_id
        self.plain_text = plain_text
        self.formatted_xml = formatted_xml
        self.formatting_tags = self._extract_formatting_tags(formatted_xml)
    
    def _extract_formatting_tags(self, xml_str: str) -> List[Dict]:
        """Extract formatting tag information from XML string."""
        tags = []
        # Match bpt tags with ctype attribute
        bpt_pattern = r'<bpt\s+id="(\d+)"\s+(?:rid="(\d+)"\s+)?ctype="([^"]+)">[^<]*</bpt>'
        for match in re.finditer(bpt_pattern, xml_str):
            tag_id = match.group(1)
            ctype = match.group(3)
            tags.append({
                'id': tag_id,
                'type': ctype,
                'is_bpt': True
            })
        return tags
    
    def __repr__(self):
        return f"FormattedSegment(id={self.id}, text='{self.plain_text[:50]}...', tags={len(self.formatting_tags)})"


class MQXLIFFHandler:
    """Handler for parsing and generating memoQ XLIFF files."""
    
    # Namespaces used in MQXLIFF files
    NAMESPACES = {
        'xliff': XLIFF_NS,
        'mq': MQ_NS
    }
    
    def __init__(self):
        """Initialize the MQXLIFF handler."""
        self.tree = None
        self.root = None
        self.file_element = None
        self.body_element = None
        self.source_lang = None
        self.target_lang = None
        
    def load(self, file_path: str) -> bool:
        """
        Load and parse an MQXLIFF file.
        
        Args:
            file_path: Path to the .mqxliff file
            
        Returns:
            True if loaded successfully, False otherwise
        """
        try:
            # Register namespaces for proper parsing
            for prefix, uri in self.NAMESPACES.items():
                ET.register_namespace(prefix, uri)
            
            self.tree = ET.parse(file_path)
            self.root = self.tree.getroot()
            
            # The language pair comes from the first <file> that declares it
            files = self._elements('file')
            self.file_element = files[0] if files else None
            for file_elem in files:
                if file_elem.get('source-language') and file_elem.get('target-language'):
                    self.file_element = file_elem
                    break
            if self.file_element is not None:
                self.source_lang = self.file_element.get('source-language', 'unknown')
                self.target_lang = self.file_element.get('target-language', 'unknown')
            
            # First <body>, kept for callers that test whether the file has one
            bodies = self._elements('body')
            self.body_element = bodies[0] if bodies else None
            
            return True
        except Exception as e:
            print(f"[MQXLIFF] Error loading file: {e}")
            return False

    def _elements(self, name: str) -> List[ET.Element]:
        """All ``name`` elements in document order, with or without namespace."""
        if self.root is None:
            return []
        found = list(self.root.iter(f'{{{XLIFF_NS}}}{name}'))
        return found or list(self.root.iter(name))

    def _child(self, elem: ET.Element, name: str) -> Optional[ET.Element]:
        found = elem.find(f'{{{XLIFF_NS}}}{name}')
        return found if found is not None else elem.find(name)

    def _translatable_units(self) -> List[Tuple[ET.Element, ET.Element]]:
        """``(file, trans-unit)`` for every segment, across all ``<file>``s.

        Auxiliary units memoQ adds for e.g. hyperlink addresses
        (``mq:nosplitjoin="true"``) are left out, on import and export alike,
        so the n-th segment always goes back to the n-th unit.
        """
        units = []
        files = self._elements('file') or ([self.root] if self.root is not None else [])
        for file_elem in files:
            found = list(file_elem.iter(f'{{{XLIFF_NS}}}trans-unit')) or list(file_elem.iter('trans-unit'))
            for unit in found:
                if unit.get(f'{{{MQ_NS}}}nosplitjoin', 'false') == 'true':
                    continue
                units.append((file_elem, unit))
        return units
    
    def extract_source_segments(self) -> List[FormattedSegment]:
        """
        Extract all source segments from the MQXLIFF file.
        
        Returns:
            List of FormattedSegment objects containing source text and formatting
        """
        segments = []
        for _file, trans_unit in self._translatable_units():
            source_elem = self._child(trans_unit, 'source')
            if source_elem is None:
                continue
            formatted_xml = ET.tostring(source_elem, encoding='unicode', method='xml')
            tagged, _ = tagged_content(source_elem)
            segments.append(FormattedSegment(trans_unit.get('id', 'unknown'),
                                             plain_text(tagged), formatted_xml))
        return segments

    def extract_bilingual_segments(self) -> List[Dict]:
        """
        Extract all source AND target segments from the MQXLIFF file.
        Used for importing pretranslated mqxliff files.

        Returns:
            List of dicts with 'id', 'source', 'target' (inline codes as
            numbered tags), 'status' (a Workbench status key), 'mq_status',
            'match_percent', 'locked' and 'file' (the document's name).
        """
        segments = []
        for file_elem, trans_unit in self._translatable_units():
            source_elem = self._child(trans_unit, 'source')
            target_elem = self._child(trans_unit, 'target')
            source_text = tagged_content(source_elem)[0] if source_elem is not None else ""
            target_text = tagged_content(target_elem)[0] if target_elem is not None else ""

            mq_status = trans_unit.get(f'{{{MQ_NS}}}status', '')
            mq_percent = None
            try:
                mq_percent = int(trans_unit.get(f'{{{MQ_NS}}}percent', ''))
            except ValueError:
                pass
            locked = (trans_unit.get(f'{{{MQ_NS}}}locked', '').lower() in ('locked', 'true', '1')
                      or trans_unit.get('translate') == 'no')

            segments.append({
                'id': trans_unit.get('id', 'unknown'),
                'source': source_text,
                'target': target_text,
                'status': workbench_status(mq_status, target_text),
                'mq_status': mq_status,
                'match_percent': mq_percent,
                'locked': locked,
                'file': file_elem.get('original', ''),
            })
        return segments

    def update_target_segments(self, translations: List[str],
                               statuses: Optional[List[str]] = None) -> int:
        """
        Write translations into the targets, in segment order.

        ``translations[i]`` (and ``statuses[i]``, a Workbench status key) belong
        to the i-th segment as returned by :meth:`extract_bilingual_segments`.
        Empty translations leave their unit as memoQ wrote it. A unit whose
        target and status are unchanged is not touched either, so memoQ keeps
        its own status (and history) for it. Without ``statuses`` every
        translated unit is marked confirmed.
            
        Returns:
            Number of units changed
        """
        changed = 0
        for i, (_file, trans_unit) in enumerate(self._translatable_units()):
            if i >= len(translations):
                break
            translation = (translations[i] or '').strip()
            if not translation:
                continue
            source_elem = self._child(trans_unit, 'source')
            if source_elem is None:
                continue
            target_elem = self._child(trans_unit, 'target')
            if target_elem is None:
                target_elem = ET.Element(source_elem.tag.replace('source', 'target'))
                trans_unit.insert(list(trans_unit).index(source_elem) + 1, target_elem)

            status_key = statuses[i] if statuses and i < len(statuses) else 'confirmed'
            current, _ = tagged_content(target_elem)
            old_status = trans_unit.get(f'{{{MQ_NS}}}status', '')
            text_changed = current.strip() != translation
            status_changed = workbench_status(old_status, current) != status_key
            if not text_changed and not status_changed:
                continue
            if text_changed:
                fill_target(source_elem, target_elem, translation)
            if status_changed or text_changed:
                trans_unit.set(f'{{{MQ_NS}}}status', memoq_status(status_key))
            changed += 1
        return changed

    
    def save(self, output_path: str) -> bool:
        """
        Save the modified MQXLIFF file with proper namespace handling.
        
        Args:
            output_path: Path where to save the file
            
        Returns:
            True if saved successfully, False otherwise
        """
        try:
            if self.tree is None:
                return False
            
            # Register namespaces to avoid namespace prefix issues
            # This ensures the default namespace is used correctly
            ET.register_namespace('', 'urn:oasis:names:tc:xliff:document:1.2')
            ET.register_namespace('mq', 'MQXliff')
            ET.register_namespace('xsi', 'http://www.w3.org/2001/XMLSchema-instance')
            
            # Write with XML declaration and UTF-8 encoding
            self.tree.write(output_path, encoding='utf-8', xml_declaration=True, method='xml')
            
            # Post-process to fix namespace issues that ElementTree might create
            # Read the file and ensure proper structure
            self._fix_namespace_prefixes(output_path)
            
            return True
        except Exception as e:
            print(f"[MQXLIFF] Error saving file: {e}")
            return False
    
    def _fix_namespace_prefixes(self, file_path: str):
        """
        Fix namespace prefix issues in the saved file.
        ElementTree sometimes adds unwanted prefixes. This method ensures
        the file matches the expected MQXLIFF format.
        
        Args:
            file_path: Path to the file to fix
        """
        try:
            # Read the file
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Fix common ElementTree namespace issues
            # Replace xliff:xliff with xliff (default namespace)
            content = content.replace('<xliff:xliff ', '<xliff ')
            content = content.replace('</xliff:xliff>', '</xliff>')
            content = content.replace('xmlns:xliff="urn:oasis:names:tc:xliff:document:1.2"',
                                    'xmlns="urn:oasis:names:tc:xliff:document:1.2"')
            
            # Remove xliff: prefixes from standard XLIFF elements
            # but keep mq: prefixes for memoQ extensions
            for tag in ['file', 'header', 'tool', 'body', 'trans-unit', 'source', 'target', 
                       'context-group', 'context', 'bpt', 'ept', 'ph', 'it', 'x']:
                content = content.replace(f'<xliff:{tag} ', f'<{tag} ')
                content = content.replace(f'<xliff:{tag}>', f'<{tag}>')
                content = content.replace(f'</xliff:{tag}>', f'</{tag}>')
            
            # Write back the corrected content
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
                
        except Exception as e:
            print(f"[MQXLIFF] Warning: Could not fix namespace prefixes: {e}")
            # Non-fatal - file might still work
    
    def get_segment_count(self) -> int:
        """Get the number of translatable segments (excluding auxiliary segments)."""
        return len(self._translatable_units())


def test_mqxliff_handler():
    """Test function to verify MQXLIFF handler functionality."""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python mqxliff_handler.py <path_to_mqxliff_file>")
        return
    
    file_path = sys.argv[1]
    
    print(f"Testing MQXLIFF Handler with: {file_path}")
    print("=" * 60)
    
    handler = MQXLIFFHandler()
    
    # Load file
    if not handler.load(file_path):
        print("Failed to load file!")
        return
    
    print(f"✓ File loaded successfully")
    print(f"  Source language: {handler.source_lang}")
    print(f"  Target language: {handler.target_lang}")
    print(f"  Segment count: {handler.get_segment_count()}")
    print()
    
    # Extract segments
    segments = handler.extract_source_segments()
    print(f"✓ Extracted {len(segments)} segments")
    print()
    
    # Display first 5 segments
    print("First 5 segments:")
    for i, seg in enumerate(segments[:5], 1):
        print(f"\n  Segment {i} (ID: {seg.id}):")
        print(f"    Plain text: {seg.plain_text}")
        if seg.formatting_tags:
            print(f"    Formatting: {seg.formatting_tags}")
    
    # Test update (with dummy translations)
    print("\n" + "=" * 60)
    print("Testing update with dummy translations...")
    dummy_translations = [f"TRANSLATED: {seg.plain_text}" for seg in segments]
    updated_count = handler.update_target_segments(dummy_translations)
    print(f"✓ Updated {updated_count} target segments")
    
    # Save test output
    output_path = file_path.replace('.mqxliff', '_test_output.mqxliff')
    if handler.save(output_path):
        print(f"✓ Saved test output to: {output_path}")
    else:
        print("✗ Failed to save output")


if __name__ == "__main__":
    test_mqxliff_handler()
