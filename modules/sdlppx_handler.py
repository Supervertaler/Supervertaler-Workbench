"""
Trados Studio Package Handler (SDLPPX/SDLRPX)

This module handles the import and export of Trados Studio project packages.
SDLPPX = Project Package (sent to translator)
SDLRPX = Return Package (sent back to PM)

Package Structure:
- .sdlppx/.sdlrpx = ZIP archive containing:
  - *.sdlproj = XML project file with settings
  - {source-lang}/*.sdlxliff = Bilingual XLIFF files
  - {target-lang}/*.sdlxliff = Target language files (may be copies)
  - Reports/ = Analysis reports (optional)

SDLXLIFF Format:
- XLIFF 1.2 with SDL namespace extensions
- <g> tags for inline formatting
- <x> tags for standalone elements
- <mrk mtype="seg"> for segment boundaries
- sdl:conf attribute for confirmation status

Author: Supervertaler
"""

import os
import re
import uuid
import zipfile
import shutil
import tempfile
import traceback
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from xml.etree import ElementTree as ET
from copy import deepcopy

# Namespaces used in SDLXLIFF
NAMESPACES = {
    'xliff': 'urn:oasis:names:tc:xliff:document:1.2',
    'sdl': 'http://sdl.com/FileTypes/SdlXliff/1.0',
    'xsi': 'http://www.w3.org/2001/XMLSchema-instance'
}

# Register namespaces for proper output
for prefix, uri in NAMESPACES.items():
    ET.register_namespace(prefix if prefix != 'xliff' else '', uri)

# XLIFF namespace URI (for creating elements)
XLIFF_NS = NAMESPACES['xliff']

# Regex for Supervertaler inline tag markers:
#   <ID>...</ID>  = paired tag (maps to <g id="ID">...</g>)
#   <ID/>         = standalone tag (maps to <x id="ID"/>)
# Tag IDs can be numeric (e.g. "14") or alphanumeric (e.g. "qSuperscript").
_TAG_ID = r'[A-Za-z0-9_]+'
_PAIRED_TAG_RE = re.compile(rf'<({_TAG_ID})>(.*?)</\1>', re.DOTALL)
_STANDALONE_TAG_RE = re.compile(rf'<({_TAG_ID})/>')


@dataclass
class SDLSegment:
    """Represents a segment from an SDLXLIFF file"""
    segment_id: str  # Unique ID within file
    trans_unit_id: str  # Parent trans-unit ID
    source_text: str  # Plain text (tags converted to markers)
    target_text: str  # Plain text translation
    source_xml: str  # Original XML with tags
    target_xml: str  # Target XML with tags
    status: str  # not_translated, draft, translated, etc.
    match_percent: int = 0  # TM match percentage
    origin: str = ""  # mt, tm, document-match, etc.
    text_match: str = ""  # SourceAndTarget = CM, Source = 100%
    locked: bool = False
    file_path: str = ""  # Source SDLXLIFF file
    modified: bool = False  # True when user translates in current session
    comments: list = field(default_factory=list)  # Trados comments: [{"id", "text", "user", "date", "severity"}]


def _deferred_xml_property(name: str) -> property:
    """``source_xml``/``target_xml`` may be given as a zero-argument callable,
    which is called – and its result kept – the first time the value is read.

    Serialising every segment's XML with ElementTree was a quarter of the time
    it takes to parse a package (issue #185), and nothing reads it during an
    import, so the parser hands over callables instead of strings.
    """
    attr = '_' + name

    def fget(self):
        value = self.__dict__.get(attr, "")
        if callable(value):
            value = value()
            self.__dict__[attr] = value
        return value

    def fset(self, value):
        self.__dict__[attr] = value

    return property(fget, fset)


SDLSegment.source_xml = _deferred_xml_property('source_xml')
SDLSegment.target_xml = _deferred_xml_property('target_xml')


@dataclass
class SDLXLIFFFile:
    """Represents an SDLXLIFF file within a package"""
    file_path: str  # Path within package
    original_name: str  # Original document name
    source_lang: str
    target_lang: str
    segments: List[SDLSegment] = field(default_factory=list)
    
    # Store the parsed XML for modification
    tree: Any = None
    root: Any = None


@dataclass 
class TradosPackage:
    """Represents a Trados Studio project package"""
    package_path: str
    package_type: str  # 'sdlppx' or 'sdlrpx'
    project_name: str
    source_lang: str
    target_lang: str
    created_at: str
    created_by: str
    
    # Files in the package
    xliff_files: List[SDLXLIFFFile] = field(default_factory=list)
    
    # Extracted location
    extract_dir: str = ""


class SDLXLIFFParser:
    """
    Parser for SDLXLIFF files (Trados bilingual XLIFF format).
    Handles the SDL-specific extensions to standard XLIFF.
    """
    
    # Tag pattern for SDL inline tags
    TAG_PATTERN = re.compile(r'<(g|x|bx|ex|ph|it|mrk)\s[^>]*>|</(g|x|bx|ex|ph|it|mrk)>')
    
    def __init__(self, log_callback=None):
        self.log = log_callback or print
    
    def parse_file(self, file_path: str) -> Optional[SDLXLIFFFile]:
        """
        Parse an SDLXLIFF file and extract segments.
        
        Args:
            file_path: Path to the SDLXLIFF file
            
        Returns:
            SDLXLIFFFile object with parsed segments
        """
        try:
            tree = ET.parse(file_path)
            root = tree.getroot()
            
            # Get file element
            file_elem = root.find('xliff:file', NAMESPACES)
            if file_elem is None:
                # Try without namespace
                file_elem = root.find('file')
            
            if file_elem is None:
                self.log(f"ERROR: No <file> element found in {file_path}")
                return None
            
            original = file_elem.get('original', Path(file_path).stem)
            source_lang = file_elem.get('source-language', 'en')
            target_lang = file_elem.get('target-language', '')
            
            xliff_file = SDLXLIFFFile(
                file_path=file_path,
                original_name=original,
                source_lang=source_lang,
                target_lang=target_lang,
                tree=tree,
                root=root
            )
            
            # Parse comment definitions from doc-info header
            self._comment_defs = {}
            sdl_ns = '{' + NAMESPACES['sdl'] + '}'
            for doc_info in root.iter(f'{sdl_ns}doc-info'):
                for cmt_def in doc_info.iter(f'{sdl_ns}cmt-def'):
                    cmt_id = cmt_def.get('id', '')
                    if not cmt_id:
                        continue
                    for comment_el in cmt_def.iter(f'{sdl_ns}Comment'):
                        self._comment_defs[cmt_id] = {
                            'id': cmt_id,
                            'text': (comment_el.text or '').strip(),
                            'user': comment_el.get('user', ''),
                            'date': comment_el.get('date', ''),
                            'severity': comment_el.get('severity', 'Low'),
                        }
            if self._comment_defs:
                self.log(f"  Found {len(self._comment_defs)} Trados comment(s)")

            # Find all trans-units
            body = file_elem.find('xliff:body', NAMESPACES)
            if body is None:
                body = file_elem.find('body')

            if body is None:
                self.log(f"ERROR: No <body> element found in {file_path}")
                return xliff_file

            # Process trans-units (may be in groups)
            trans_units = body.findall('.//xliff:trans-unit', NAMESPACES)
            if not trans_units:
                trans_units = body.findall('.//trans-unit')
            
            for tu in trans_units:
                segments = self._parse_trans_unit(tu, file_path)
                xliff_file.segments.extend(segments)
            
            self.log(f"Parsed {len(xliff_file.segments)} segments from {Path(file_path).name}")
            return xliff_file
            
        except Exception as e:
            self.log(f"ERROR parsing SDLXLIFF: {e}")
            traceback.print_exc()
            return None
    
    def _parse_trans_unit(self, tu: ET.Element, file_path: str) -> List[SDLSegment]:
        """Parse a trans-unit element into segments."""
        segments = []
        tu_id = tu.get('id', '')
        
        # Get source element
        source_elem = tu.find('xliff:source', NAMESPACES)
        if source_elem is None:
            source_elem = tu.find('source')
        
        # Get target element
        target_elem = tu.find('xliff:target', NAMESPACES)
        if target_elem is None:
            target_elem = tu.find('target')
        
        # Get seg-source for segmented content
        seg_source = tu.find('xliff:seg-source', NAMESPACES)
        if seg_source is None:
            seg_source = tu.find('seg-source')
        
        if source_elem is None:
            return segments
        # v1.10.147 (from Hans Lenting's Simpelvertaler fork): skip trans-units
        # whose source text is entirely empty/whitespace — they don't produce a
        # usable grid row, so generating one just creates an empty segment that
        # the user then has to filter out.
        if not ''.join(source_elem.itertext()).strip():
            return segments

        # Check if this is a segmented trans-unit (has mrk elements)
        if seg_source is not None:
            # Parse segmented content
            segments = self._parse_segmented_unit(tu, tu_id, seg_source, target_elem, file_path)
        else:
            # Single segment
            source_xml = lambda e=source_elem: self._element_to_string(e)
            source_text = self._extract_text(source_elem)

            target_xml = ""
            target_text = ""
            if target_elem is not None:
                self._current_segment_comments = []
                target_xml = lambda e=target_elem: self._element_to_string(e)
                target_text = self._extract_text(target_elem)

            # Get SDL-specific attributes
            sdl_seg = tu.find('.//sdl:seg', {'sdl': NAMESPACES['sdl']})
            status = self._get_segment_status(tu, sdl_seg)
            match_percent = self._get_match_percent(sdl_seg)
            origin = self._get_origin(sdl_seg)
            text_match = self._get_text_match(sdl_seg)
            locked = self._is_locked(tu, sdl_seg)

            # Capture any comments found during target text extraction
            segment_comments = list(getattr(self, '_current_segment_comments', []))

            segment = SDLSegment(
                segment_id=tu_id,
                trans_unit_id=tu_id,
                source_text=source_text,
                target_text=target_text,
                source_xml=source_xml,
                target_xml=target_xml,
                status=status,
                match_percent=match_percent,
                origin=origin,
                text_match=text_match,
                locked=locked,
                file_path=file_path,
                comments=segment_comments,
            )
            segments.append(segment)
        
        return segments
    
    def _parse_segmented_unit(self, tu: ET.Element, tu_id: str, 
                              seg_source: ET.Element, target_elem: ET.Element,
                              file_path: str) -> List[SDLSegment]:
        """Parse a trans-unit with segmented (mrk) content."""
        segments = []
        
        # Find all mrk elements with mtype="seg" in seg-source
        source_mrks = seg_source.findall('.//xliff:mrk[@mtype="seg"]', NAMESPACES)
        if not source_mrks:
            source_mrks = seg_source.findall('.//mrk[@mtype="seg"]')
        
        # Find corresponding target mrk elements
        target_mrks = []
        if target_elem is not None:
            target_mrks = target_elem.findall('.//xliff:mrk[@mtype="seg"]', NAMESPACES)
            if not target_mrks:
                target_mrks = target_elem.findall('.//mrk[@mtype="seg"]')
        
        # Create a map of target mrks by mid
        target_mrk_map = {mrk.get('mid'): mrk for mrk in target_mrks}
        
        # Get seg-defs for segment metadata
        seg_defs = tu.find('sdl:seg-defs', {'sdl': NAMESPACES['sdl']})
        seg_def_map = {}
        if seg_defs is not None:
            for seg in seg_defs.findall('sdl:seg', {'sdl': NAMESPACES['sdl']}):
                mid = seg.get('id')
                if mid:
                    seg_def_map[mid] = seg
        
        for source_mrk in source_mrks:
            mid = source_mrk.get('mid')
            if not mid:
                continue
            # v1.10.147 (from Hans Lenting's Simpelvertaler fork): same guard
            # as in _parse_trans_unit — drop mrk segments whose source text is
            # whitespace-only so we don't manufacture empty rows.
            if not ''.join(source_mrk.itertext()).strip():
                continue

            # Serialised only if read (see _deferred_xml_property)
            source_xml = lambda e=source_mrk: self._element_inner_xml(e)
            source_text = self._extract_text(source_mrk)
            
            target_mrk = target_mrk_map.get(mid)
            target_xml = ""
            target_text = ""
            self._current_segment_comments = []
            if target_mrk is not None:
                target_xml = lambda e=target_mrk: self._element_inner_xml(e)
                target_text = self._extract_text(target_mrk)

            # Capture any comments found during target text extraction
            segment_comments = list(getattr(self, '_current_segment_comments', []))

            # Get segment definition
            seg_def = seg_def_map.get(mid)
            status = self._get_segment_status(tu, seg_def)
            match_percent = self._get_match_percent(seg_def)
            origin = self._get_origin(seg_def)
            text_match = self._get_text_match(seg_def)
            locked = self._is_locked(tu, seg_def)

            segment = SDLSegment(
                segment_id=f"{tu_id}_{mid}",
                trans_unit_id=tu_id,
                source_text=source_text,
                target_text=target_text,
                source_xml=source_xml,
                target_xml=target_xml,
                status=status,
                match_percent=match_percent,
                origin=origin,
                text_match=text_match,
                locked=locked,
                file_path=file_path,
                comments=segment_comments,
            )
            segments.append(segment)
        
        return segments
    
    def _element_to_string(self, elem: ET.Element) -> str:
        """Convert element to string including tags."""
        return ET.tostring(elem, encoding='unicode')
    
    def _element_inner_xml(self, elem: ET.Element) -> str:
        """Get inner XML of an element (content without the element itself)."""
        result = elem.text or ""
        for child in elem:
            result += ET.tostring(child, encoding='unicode')
        return result
    
    def _extract_text(self, elem: ET.Element) -> str:
        """Extract plain text from element, converting tags to markers."""
        text_parts = []
        
        def process_element(el, depth=0):
            # Add element's text
            if el.text:
                text_parts.append(el.text)
            
            # Process children
            for child in el:
                tag_name = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                
                if tag_name == 'g':
                    # Paired tag - convert to Supervertaler format
                    tag_id = child.get('id', '')
                    text_parts.append(f'<{tag_id}>')
                    process_element(child, depth + 1)
                    text_parts.append(f'</{tag_id}>')
                elif tag_name in ('x', 'ph', 'bx', 'ex'):
                    # Standalone tag
                    tag_id = child.get('id', '')
                    text_parts.append(f'<{tag_id}/>')
                elif tag_name == 'mrk':
                    # Check for comment markers
                    mtype = child.get('mtype', '')
                    if mtype == 'x-sdl-comment':
                        sdl_ns_uri = '{' + NAMESPACES['sdl'] + '}'
                        cid = child.get(f'{sdl_ns_uri}cid', '')
                        if cid and hasattr(self, '_comment_defs') and cid in self._comment_defs:
                            if not hasattr(self, '_current_segment_comments'):
                                self._current_segment_comments = []
                            self._current_segment_comments.append(self._comment_defs[cid])
                    # Process content regardless of marker type
                    process_element(child, depth + 1)
                else:
                    # Unknown - include as-is
                    process_element(child, depth + 1)
                
                # Add tail text
                if child.tail:
                    text_parts.append(child.tail)
        
        process_element(elem)
        return ''.join(text_parts)
    
    def _get_segment_status(self, tu: ET.Element, seg_def: ET.Element) -> str:
        """Get segment status from SDL attributes."""
        if seg_def is not None:
            conf = seg_def.get('conf')
            if conf:
                status_map = {
                    'Draft': 'draft',
                    'Translated': 'translated',
                    'ApprovedTranslation': 'approved',
                    'ApprovedSignOff': 'approved',
                    'RejectedTranslation': 'rejected',
                    'RejectedSignOff': 'rejected'
                }
                return status_map.get(conf, 'not_translated')
        return 'not_translated'
    
    def _get_match_percent(self, seg_def: ET.Element) -> int:
        """Get TM match percentage."""
        if seg_def is not None:
            percent = seg_def.get('percent')
            if percent:
                try:
                    return int(percent)
                except ValueError:
                    pass
        return 0
    
    def _get_origin(self, seg_def: ET.Element) -> str:
        """Get segment origin (tm, mt, document-match, etc.)."""
        if seg_def is not None:
            origin = seg_def.get('origin')
            if origin:
                return origin.lower()
        return ""
    
    def _get_text_match(self, seg_def: ET.Element) -> str:
        """Get text-match attribute (SourceAndTarget = CM, Source = 100%)."""
        if seg_def is not None:
            text_match = seg_def.get('text-match')
            if text_match:
                return text_match
        return ""
    
    def _is_locked(self, tu: ET.Element, seg_def: ET.Element) -> bool:
        """Check if segment is locked."""
        if seg_def is not None:
            locked = seg_def.get('locked')
            if locked and locked.lower() == 'true':
                return True
        
        # Check translate attribute on trans-unit
        translate = tu.get('translate')
        if translate and translate.lower() == 'no':
            return True
        
        return False


# ─── Module-level save helpers (used by both Standalone and Package handlers) ───

def _markers_to_xml(text: str) -> str:
    """
    Convert Supervertaler marker tags in text to SDLXLIFF XML elements.

    <N>content</N>  → <g id="N">content</g>
    <N/>            → <x id="N"/>

    Also escapes XML special characters (&, <, >) in text content so the
    output is valid XML.  Marker tags are preserved as-is since they will
    be converted to proper XML elements.

    Uses the XLIFF default namespace (no prefix needed since <g> and <x>
    live in the default xliff namespace in SDLXLIFF files).
    """
    if not text:
        return text

    _tid = _TAG_ID

    # First, escape XML entities in the text portions only.
    # Split on marker tags, escape the text parts, then rejoin.
    tag_pattern = rf'(<{_tid}>|</{_tid}>|<{_tid}/>)'
    parts = re.split(tag_pattern, text)
    escaped_parts = []
    for part in parts:
        if re.match(tag_pattern, part):
            # This is a marker tag – keep as-is
            escaped_parts.append(part)
        else:
            # This is text content – escape XML entities
            part = part.replace('&', '&amp;')
            part = part.replace('<', '&lt;')
            part = part.replace('>', '&gt;')
            escaped_parts.append(part)
    result = ''.join(escaped_parts)

    # Now convert marker tags to SDLXLIFF XML elements
    if not re.search(rf'<{_tid}[>/]', result):
        return result

    # Repeatedly resolve innermost paired tags until none remain
    prev = None
    while prev != result:
        prev = result
        result = re.sub(
            rf'<({_tid})>(.*?)</\1>',
            r'<g id="\1">\2</g>',
            result,
            flags=re.DOTALL
        )

    # Convert standalone tags
    result = re.sub(rf'<({_tid})/>', r'<x id="\1"/>', result)

    return result


def _find_max_locked_id(content: str) -> int:
    """
    Find the highest 'lockedN' element id number in the file content.

    Lock elements use ids like ``id="locked1417"``. When creating new lock
    elements for target content we must generate ids that don't collide with
    existing ones.

    Returns:
        The highest N found, or 0 if none.
    """
    ids = re.findall(r'\bid="locked(\d+)"', content)
    return max((int(n) for n in ids), default=0)


def _remap_lock_xids(target_fragment: str, locked_id_counter: int
                     ) -> Tuple[str, List[Tuple[str, str, str, str]], int]:
    """
    Remap lock element ids and xids in *target_fragment* to fresh values.

    For every ``<x id="lockedN" xid="lockTU_UUID"/>`` found in the fragment:
    * A new sequential element id ``locked{counter}`` is assigned.
    * A new UUID-based ``lockTU_`` xid is generated.

    Args:
        target_fragment: XML fragment that will become the new ``<target>``
            inner content.  May contain zero or more lock elements.
        locked_id_counter: Current counter value for generating sequential
            ``locked{N}`` ids.  The caller is responsible for persisting the
            returned (incremented) counter across calls.

    Returns:
        A 3-tuple:
        * The rewritten *target_fragment* with new ids/xids.
        * A list of ``(old_xid, new_xid, old_elem_id, new_elem_id)`` tuples
          describing each remapping.
        * The updated *locked_id_counter* after all remappings.
    """
    mappings: List[Tuple[str, str, str, str]] = []

    # Pattern matches: <x id="lockedN" xid="lockTU_UUID" />
    # (attributes may appear in any order, optional whitespace before />)
    lock_elem_re = re.compile(
        r'<x\s+'
        r'(?=[^>]*\bid="(locked\d+)")'     # capture old element id
        r'(?=[^>]*\bxid="(lockTU_[^"]+)")'  # capture old xid
        r'[^>]*/>'
    )

    def _remap(m: re.Match) -> str:
        nonlocal locked_id_counter
        old_elem_id = m.group(1)  # e.g. "locked1418"
        old_xid = m.group(2)      # e.g. "lockTU_9056fa06-..."

        locked_id_counter += 1
        new_elem_id = f'locked{locked_id_counter}'
        new_xid = f'lockTU_{uuid.uuid4()}'

        mappings.append((old_xid, new_xid, old_elem_id, new_elem_id))

        # Rebuild the element with new ids
        tag = m.group(0)
        tag = tag.replace(f'id="{old_elem_id}"', f'id="{new_elem_id}"')
        tag = tag.replace(f'xid="{old_xid}"', f'xid="{new_xid}"')
        return tag

    new_fragment = lock_elem_re.sub(_remap, target_fragment)
    return new_fragment, mappings, locked_id_counter


def _insert_lock_tus(content: str,
                     xid_mappings: List[Tuple[str, str, str, str]]) -> str:
    """
    Insert new lock TU ``<trans-unit>`` elements for remapped xids.

    For each ``(old_xid, new_xid, …)`` in *xid_mappings*, find the original
    lock TU (whose ``id`` equals *old_xid*), clone it with ``id=new_xid``,
    and insert the clone immediately before the original.  This matches the
    layout that Trados Studio produces.

    Args:
        content: Full SDLXLIFF file content.
        xid_mappings: List of ``(old_xid, new_xid, old_elem_id, new_elem_id)``
            tuples as returned by :func:`_remap_lock_xids`.

    Returns:
        Modified file content with the new lock TU trans-units inserted.
    """
    if not xid_mappings:
        return content

    for old_xid, new_xid, _old_eid, _new_eid in xid_mappings:
        # Find the original lock TU.  The id attribute may not be the first
        # attribute, so use a flexible regex.
        esc_old = re.escape(old_xid)
        lock_tu_re = re.compile(
            rf'<trans-unit\s+(?=[^>]*\bid="{esc_old}")[^>]*>.*?</trans-unit>',
            re.DOTALL
        )
        m = lock_tu_re.search(content)
        if not m:
            continue

        original_tu = m.group(0)
        # Clone with new id
        new_tu = original_tu.replace(f'id="{old_xid}"', f'id="{new_xid}"', 1)

        # Insert the clone immediately before the original
        content = content[:m.start()] + new_tu + content[m.start():]

    return content


def _replace_target_content(content: str, xliff_file: SDLXLIFFFile,
                            segment_map: Dict[str, 'SDLSegment']) -> str:
    """
    Replace <mrk> content inside <target> elements with translated text.

    Strategy: find each <trans-unit>, locate its <target> block, then
    replace <mrk mtype="seg" mid="N"> content within it.

    Handles several Trados SDLXLIFF structures:
    1. Standard: <target> has <mrk mtype="seg" mid="N"> – replace content
    2. Empty target: <target> exists but has no <mrk> tags – rebuild from seg-source
    3. Missing target: no <target> element – create one from seg-source structure
    4. Unsegmented: no mrk tags at all – use tu_id as segment_id

    Also handles lock TU xid remapping: when building new target content from
    seg-source for partial-lock segments, lock element xids are remapped to
    fresh UUIDs so each context (source, seg-source, target) has unique
    lock TU references.  Corresponding lock TU trans-units are inserted into
    the file after all per-TU replacements.

    The mrk regex handles any attribute order (mtype before mid or vice versa)
    since XML does not guarantee attribute ordering.
    """
    # Find the highest existing locked element id for sequential numbering
    locked_id_counter = _find_max_locked_id(content)
    # Accumulate lock TU xid mappings across all trans-units
    all_xid_mappings: List[Tuple[str, str, str, str]] = []

    def _get_seg_source_lock_xids(tu_block: str, mid: str) -> Dict[str, str]:
        """
        Extract lock element id → xid mappings from the seg-source mrk
        with the given mid.

        Returns a dict like ``{'locked1418': 'lockTU_9056fa06-...'}``
        so that when we produce target content via _markers_to_xml()
        (which loses the xid), we can re-attach the correct xid reference
        and then remap it to a fresh UUID.
        """
        # Find the seg-source mrk for this mid
        seg_source_m = re.search(
            r'<seg-source[^>]*>(.*?)</seg-source>', tu_block, re.DOTALL)
        if not seg_source_m:
            return {}

        seg_source = seg_source_m.group(1)
        # Find the mrk with this mid
        mrk_re = re.compile(
            r'<mrk\s+'
            r'(?=[^>]*\bmtype="seg")'
            r'(?=[^>]*\bmid="' + re.escape(mid) + r'")'
            r'[^>]*>(.*?)</mrk>',
            re.DOTALL
        )
        mrk_m = mrk_re.search(seg_source)
        if not mrk_m:
            return {}

        mrk_content = mrk_m.group(1)
        # Extract all lock element id→xid pairs
        result = {}
        for lock_m in re.finditer(
                r'<x\s+[^>]*?\bid="(locked\d+)"[^>]*?\bxid="(lockTU_[^"]+)"[^>]*/>', mrk_content):
            result[lock_m.group(1)] = lock_m.group(2)
        # Also handle reversed attribute order
        for lock_m in re.finditer(
                r'<x\s+[^>]*?\bxid="(lockTU_[^"]+)"[^>]*?\bid="(locked\d+)"[^>]*/>', mrk_content):
            result[lock_m.group(2)] = lock_m.group(1)
        return result

    def _restore_and_remap_lock_xids(new_content: str, seg_source_xids: Dict[str, str]) -> str:
        """
        After _markers_to_xml(), lock elements look like ``<x id="locked1418"/>``
        (missing xid).  This function re-attaches the xid from the seg-source
        and remaps both to fresh values for the target context.
        """
        nonlocal locked_id_counter

        if not seg_source_xids:
            return new_content

        for old_elem_id, old_xid in seg_source_xids.items():
            # Find <x id="lockedN"/> (without xid) in new_content
            pattern = rf'<x\s+id="{re.escape(old_elem_id)}"(\s*)/>'
            m = re.search(pattern, new_content)
            if not m:
                continue

            # Generate new ids
            locked_id_counter += 1
            new_elem_id = f'locked{locked_id_counter}'
            new_xid = f'lockTU_{uuid.uuid4()}'

            all_xid_mappings.append((old_xid, new_xid, old_elem_id, new_elem_id))

            # Replace with full lock element including new xid
            new_elem = f'<x id="{new_elem_id}" xid="{new_xid}"/>'
            new_content = new_content[:m.start()] + new_elem + new_content[m.end():]

        return new_content

    def _replace_tu_target(tu_match):
        nonlocal locked_id_counter
        tu_block = tu_match.group(0)
        tu_id_m = re.search(r'<trans-unit\s+[^>]*?id="([^"]+)"', tu_block)
        if not tu_id_m:
            return tu_block
        tu_id = tu_id_m.group(1)

        # Skip lock TUs themselves
        if tu_id.startswith('lockTU_'):
            return tu_block

        # Collect all translations for segments in this TU
        tu_translations = {}
        for sid, seg in segment_map.items():
            if seg.target_text and (sid.startswith(f"{tu_id}_") or sid == tu_id):
                tu_translations[sid] = seg
        if not tu_translations:
            return tu_block

        # Find <target>...</target> within this TU
        target_m = re.search(r'(<target[^>]*>)(.*?)(</target>)', tu_block, re.DOTALL)

        if not target_m:
            # No <target> element – create one by cloning seg-source structure
            result = _build_target_from_seg_source(
                tu_block, tu_id, segment_map, locked_id_counter)
            if result is None:
                return tu_block
            new_target, mappings, locked_id_counter = result
            all_xid_mappings.extend(mappings)
            # Insert <target> after </seg-source> or </source>
            insert_after = '</seg-source>'
            insert_pos = tu_block.find(insert_after)
            if insert_pos == -1:
                insert_after = '</source>'
                insert_pos = tu_block.find(insert_after)
            if insert_pos != -1:
                insert_pos += len(insert_after)
                return tu_block[:insert_pos] + new_target + tu_block[insert_pos:]
            return tu_block

        target_open = target_m.group(1)
        target_inner = target_m.group(2)
        target_close = target_m.group(3)

        # Replace each <mrk mtype="seg" mid="N">...</mrk> in the target.
        # Match any <mrk> tag that contains both mtype="seg" and mid="N"
        # regardless of attribute order (XML doesn't guarantee order).
        replaced_count = 0

        def _replace_mrk(mrk_match):
            nonlocal replaced_count
            mrk_open = mrk_match.group(1)   # full opening tag (including > or />)
            mrk_content = mrk_match.group(2)  # content between tags
            # Extract mid value from the opening tag
            mid_m = re.search(r'\bmid="(\d+)"', mrk_open)
            if not mid_m:
                return mrk_match.group(0)
            mid = mid_m.group(1)

            segment_id = f"{tu_id}_{mid}"
            segment = segment_map.get(segment_id)
            if segment and segment.target_text:
                new_content = _markers_to_xml(segment.target_text)
                if 'xid=' in mrk_content:
                    # Already-translated segment whose target mrk already has
                    # lock xids.  _markers_to_xml() strips xids, so we must
                    # re-attach them from the existing target content.
                    existing_xids: Dict[str, str] = {}
                    for lm in re.finditer(
                            r'<x\s+[^>]*?\bid="(locked\d+)"[^>]*?\bxid="(lockTU_[^"]+)"[^>]*/>', mrk_content):
                        existing_xids[lm.group(1)] = lm.group(2)
                    for lm in re.finditer(
                            r'<x\s+[^>]*?\bxid="(lockTU_[^"]+)"[^>]*?\bid="(locked\d+)"[^>]*/>', mrk_content):
                        existing_xids[lm.group(2)] = lm.group(1)
                    # Re-attach xids to the bare <x id="lockedN"/> tags
                    for eid, xid_val in existing_xids.items():
                        bare_pattern = rf'<x\s+id="{re.escape(eid)}"(\s*)/>'
                        new_content = re.sub(
                            bare_pattern,
                            f'<x id="{eid}" xid="{xid_val}"/>',
                            new_content
                        )
                elif re.search(r'<x\s+id="locked\d+"', new_content):
                    # Target mrk was empty but translated text has lock
                    # markers – restore xids from seg-source and remap.
                    seg_xids = _get_seg_source_lock_xids(tu_block, mid)
                    if seg_xids:
                        new_content = _restore_and_remap_lock_xids(
                            new_content, seg_xids)
                replaced_count += 1
                return f'{mrk_open}{new_content}</mrk>'
            return mrk_match.group(0)

        def _replace_mrk_selfclose(mrk_match):
            """Replace self-closing <mrk mtype="seg" mid="N" /> with translated content."""
            nonlocal replaced_count
            full_tag = mrk_match.group(0)  # e.g. '<mrk mtype="seg" mid="50" />'
            mid_m = re.search(r'\bmid="(\d+)"', full_tag)
            if not mid_m:
                return full_tag
            mid = mid_m.group(1)

            segment_id = f"{tu_id}_{mid}"
            segment = segment_map.get(segment_id)
            if segment and segment.target_text:
                new_content = _markers_to_xml(segment.target_text)
                # Self-closing mrk means the target was empty.  If the
                # translated text references lock elements (e.g. <x id="locked1418"/>)
                # we must restore the xid from seg-source and remap to new values.
                seg_xids = _get_seg_source_lock_xids(tu_block, mid)
                if seg_xids:
                    new_content = _restore_and_remap_lock_xids(
                        new_content, seg_xids)
                replaced_count += 1
                # Build a proper opening tag by removing the self-close slash
                open_tag = re.sub(r'\s*/?>$', '>', full_tag)
                return f'{open_tag}{new_content}</mrk>'
            return full_tag

        # Flexible mrk pattern: match <mrk ...> where both mtype="seg" and
        # mid="N" appear as attributes (in any order), then content, then </mrk>.
        # Lookaheads ensure both attributes are present without assuming order.
        # The negative lookbehind (?<!/) before > ensures we do NOT match
        # self-closing tags like <mrk ... /> (those are handled in pass 2).
        mrk_pattern = (
            r'(<mrk\s+'                  # opening <mrk + space
            r'(?=[^>]*\bmtype="seg")'    # lookahead: mtype="seg" present
            r'(?=[^>]*\bmid="\d+")'      # lookahead: mid="N" present
            r'[^>]*(?<!/)>)'             # consume all attributes + > (not />)
            r'(.*?)'                     # content (non-greedy)
            r'(</mrk>)'                  # closing tag
        )

        new_target_inner = re.sub(
            mrk_pattern,
            _replace_mrk,
            target_inner,
            flags=re.DOTALL
        )

        # Second pass: handle self-closing <mrk mtype="seg" mid="N" /> tags.
        # Trados uses these for empty/untranslated segments. The first pass only
        # matches the <mrk ...>content</mrk> form, so self-closing tags are skipped.
        mrk_selfclose_pattern = (
            r'<mrk\s+'                   # opening <mrk + space
            r'(?=[^>]*\bmtype="seg")'    # lookahead: mtype="seg" present
            r'(?=[^>]*\bmid="\d+")'      # lookahead: mid="N" present
            r'[^>]*?'                    # attributes (non-greedy)
            r'\s*/>'                     # self-closing />
        )

        new_target_inner = re.sub(
            mrk_selfclose_pattern,
            _replace_mrk_selfclose,
            new_target_inner,
            flags=re.DOTALL
        )

        if replaced_count == 0:
            # No mrk replacements happened – either:
            # a) target has no <mrk mtype="seg"> tags at all, or
            # b) segment_ids didn't match

            # First: check for unsegmented TU (segment_id = tu_id, no _mid suffix)
            segment = segment_map.get(tu_id)
            if segment and segment.target_text:
                new_content = _markers_to_xml(segment.target_text)
                new_target_inner = new_content
            else:
                # Second: target has no mrk tags but translations exist with _mid suffix.
                # Rebuild target content from seg-source structure with translations.
                result = _build_target_from_seg_source(
                    tu_block, tu_id, segment_map, locked_id_counter)
                if result is not None:
                    rebuilt, mappings, locked_id_counter = result
                    all_xid_mappings.extend(mappings)
                    # Extract inner content from the rebuilt <target>...</target>
                    rebuilt_m = re.match(r'<target[^>]*>(.*)</target>', rebuilt, re.DOTALL)
                    if rebuilt_m:
                        new_target_inner = rebuilt_m.group(1)

        new_target = f'{target_open}{new_target_inner}{target_close}'
        return tu_block[:target_m.start()] + new_target + tu_block[target_m.end():]

    # Process each trans-unit
    content = re.sub(
        r'<trans-unit\s[^>]*>.*?</trans-unit>',
        _replace_tu_target,
        content,
        flags=re.DOTALL
    )

    # Insert new lock TU trans-units for all remapped xids
    if all_xid_mappings:
        content = _insert_lock_tus(content, all_xid_mappings)

    return content


def _build_target_from_seg_source(tu_block: str, tu_id: str,
                                   segment_map: Dict[str, 'SDLSegment'],
                                   locked_id_counter: int = 0
                                   ) -> Optional[Tuple[str, List[Tuple[str, str, str, str]], int]]:
    """
    Build a <target> element by cloning the <seg-source> structure and
    replacing each <mrk mtype="seg" mid="N"> content with translations.

    Lock elements (``<x id="lockedN" xid="lockTU_..."/>``) in the cloned
    seg-source content are remapped to fresh xids so that the target context
    has its own unique lock TU references.

    Args:
        tu_block: The full trans-unit XML text.
        tu_id: The trans-unit id.
        segment_map: Segment id → SDLSegment mapping.
        locked_id_counter: Current counter for generating sequential locked
            element ids.

    Returns:
        A 3-tuple ``(target_xml, xid_mappings, updated_counter)`` or ``None``
        if no translations are available for this TU.
    """
    xid_mappings: List[Tuple[str, str, str, str]] = []

    # Extract seg-source inner content
    seg_source_m = re.search(r'<seg-source[^>]*>(.*?)</seg-source>', tu_block, re.DOTALL)
    if not seg_source_m:
        # No seg-source – try <source> as fallback for unsegmented TUs
        source_m = re.search(r'<source[^>]*>(.*?)</source>', tu_block, re.DOTALL)
        if source_m:
            segment = segment_map.get(tu_id)
            if segment and segment.target_text:
                new_content = _markers_to_xml(segment.target_text)
                return f'<target>{new_content}</target>', [], locked_id_counter
        return None

    seg_source_inner = seg_source_m.group(1)
    any_replaced = False

    def _replace_seg_source_mrk(mrk_match):
        nonlocal any_replaced
        mrk_open = mrk_match.group(1)
        mid_m = re.search(r'\bmid="(\d+)"', mrk_open)
        if not mid_m:
            return mrk_match.group(0)
        mid = mid_m.group(1)
        segment_id = f"{tu_id}_{mid}"
        segment = segment_map.get(segment_id)
        if segment and segment.target_text:
            new_content = _markers_to_xml(segment.target_text)
            any_replaced = True
            return f'{mrk_open}{new_content}</mrk>'
        return mrk_match.group(0)

    mrk_pattern = (
        r'(<mrk\s+'
        r'(?=[^>]*\bmtype="seg")'
        r'(?=[^>]*\bmid="\d+")'
        r'[^>]*(?<!/)>)'             # consume all attributes + > (not />)
        r'(.*?)'
        r'(</mrk>)'
    )

    target_inner = re.sub(mrk_pattern, _replace_seg_source_mrk,
                          seg_source_inner, flags=re.DOTALL)

    if not any_replaced:
        return None

    # Remap any lock element xids in the target content so the target
    # context has its own unique lock TU references.
    if 'xid="lockTU_' in target_inner:
        target_inner, xid_mappings, locked_id_counter = _remap_lock_xids(
            target_inner, locked_id_counter)

    return f'<target>{target_inner}</target>', xid_mappings, locked_id_counter


def _replace_seg_attributes(content: str, xliff_file: SDLXLIFFFile,
                            segment_map: Dict[str, 'SDLSegment']) -> str:
    """
    Update conf and origin attributes on <sdl:seg> elements.

    For translated segments: set conf="Translated", origin="interactive",
    and remove stale TM/MT attributes (origin-system, percent, text-match).
    """
    def _replace_seg(seg_match):
        seg_text = seg_match.group(0)
        seg_id = seg_match.group(1)

        # Try to find this segment in any TU
        # seg IDs in sdl:seg-defs correspond to mrk mid values
        # We need to find which TU this belongs to by looking at context
        # But since we're doing global replacement, we check all possible TU+seg combos
        matching_segment = None
        for sid, seg in segment_map.items():
            if sid.endswith(f'_{seg_id}'):
                matching_segment = seg
                break

        if not matching_segment or not matching_segment.target_text:
            return seg_text

        # Map internal status to Trados conf value
        # Trados "Translated" = confirmed, "ApprovedTranslation" = reviewer-approved
        _status_to_conf = {
            'draft': 'Draft',
            'translated': 'Translated',
            'confirmed': 'Translated',
            'approved': 'ApprovedTranslation',
            'proofread': 'ApprovedTranslation',
            'rejected': 'RejectedTranslation',
        }
        new_conf = _status_to_conf.get((matching_segment.status or '').lower())
        if new_conf:
            # Update conf – replace existing or add if missing
            # (applies to ALL translated segments, including TM matches)
            if 'conf="' in seg_text:
                seg_text = re.sub(r'conf="[^"]*"', f'conf="{new_conf}"', seg_text)
            else:
                seg_text = seg_text.replace('<sdl:seg ', f'<sdl:seg conf="{new_conf}" ', 1)

            # Only update origin/percent/text-match for segments the user
            # translated in this session. Leave TM-matched segments untouched.
            if matching_segment.modified:
                # Update origin to interactive – replace existing or add if missing
                if 'origin="' in seg_text:
                    seg_text = re.sub(r'origin="[^"]*"', 'origin="interactive"', seg_text)
                else:
                    seg_text = seg_text.replace('<sdl:seg ', '<sdl:seg origin="interactive" ', 1)

                # Remove stale TM/MT attributes
                seg_text = re.sub(r'\s+origin-system="[^"]*"', '', seg_text)
                seg_text = re.sub(r'\s+percent="[^"]*"', '', seg_text)
                seg_text = re.sub(r'\s+text-match="[^"]*"', '', seg_text)

        return seg_text

    # Match <sdl:seg> elements with id attribute in any position
    content = re.sub(
        r'<sdl:seg\s+(?=[^>]*\bid="(\d+)")[^>]*(?:/>|>)',
        _replace_seg,
        content
    )
    return content


def _strip_comment_markers(content: str) -> str:
    """Remove existing <mrk mtype="x-sdl-comment" ...>CONTENT</mrk>, keeping CONTENT.

    Also removes any orphaned <cmt-def> entries from the header.
    """
    # Strip inline comment markers from body
    content = re.sub(
        r'<mrk\s+(?=[^>]*\bmtype="x-sdl-comment")[^>]*>(.*?)</mrk>',
        r'\1', content, flags=re.DOTALL
    )
    # Remove orphaned <cmt-def> blocks from header (they all reference stripped markers)
    content = re.sub(
        r'<cmt-def\s+id="[^"]*">\s*<Comments>.*?</Comments>\s*</cmt-def>\s*',
        '', content, flags=re.DOTALL
    )
    # Clean up empty <cmt-defs></cmt-defs> if all entries were removed
    content = re.sub(r'<cmt-defs>\s*</cmt-defs>', '', content)
    return content


def _insert_comment_defs(content: str, seg_to_cmt: Dict[str, Tuple[str, str]],
                         log_callback=None, username: str = "user") -> str:
    """Insert <cmt-def> entries into the doc-info header.

    Args:
        content: Raw SDLXLIFF file content
        seg_to_cmt: Dict mapping segment_id to (cmt_uuid, comment_text)
        username: Author name for comments
    """
    if not seg_to_cmt:
        return content

    now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.0000000+00:00')
    # Escape username for XML attribute
    safe_user = (username
                 .replace('&', '&amp;')
                 .replace('<', '&lt;')
                 .replace('>', '&gt;')
                 .replace('"', '&quot;'))

    cmt_defs_xml_parts = []
    for seg_id, (cmt_id, comment_text) in seg_to_cmt.items():
        # Escape XML entities
        safe_text = (comment_text
                     .replace('&', '&amp;')
                     .replace('<', '&lt;')
                     .replace('>', '&gt;')
                     .replace('"', '&quot;'))
        cmt_defs_xml_parts.append(
            f'<cmt-def id="{cmt_id}">'
            f'<Comments>'
            f'<Comment severity="Low" user="{safe_user}" '
            f'date="{now}" version="1.0">{safe_text}</Comment>'
            f'</Comments>'
            f'</cmt-def>'
        )

    cmt_defs_block = ''.join(cmt_defs_xml_parts)

    # Insert the <cmt-defs> so every comment marker has a matching definition.
    # NB: use str.replace / a lambda (not a regex replacement string) because
    # cmt_defs_block can contain backslashes or "\1"-like sequences from the
    # comment text, which re.sub would mis-interpret as group references.
    if '<cmt-defs>' in content:
        content = content.replace('</cmt-defs>', cmt_defs_block + '</cmt-defs>', 1)
    elif '</doc-info>' in content:
        # Existing doc-info – add <cmt-defs> just before it closes.
        content = content.replace(
            '</doc-info>', '<cmt-defs>' + cmt_defs_block + '</cmt-defs></doc-info>', 1)
    else:
        # No <doc-info> at all (some source SDLXLIFFs omit it). Create one right
        # after the <xliff …> open tag, exactly where Trados puts it — otherwise
        # the comment markers we add below would reference undefined comments and
        # Trados throws "Object reference not set to an instance of an object"
        # when opening the return package.
        doc_info = ('<doc-info xmlns="http://sdl.com/FileTypes/SdlXliff/1.0">'
                    '<cmt-defs>' + cmt_defs_block + '</cmt-defs></doc-info>')
        content = re.sub(r'(<xliff\b[^>]*>)', lambda m: m.group(1) + doc_info,
                         content, count=1)
    return content


def _wrap_targets_with_comments(content: str, seg_to_cmt: Dict[str, Tuple[str, str]],
                                log_callback=None) -> str:
    """Wrap target segment content with <mrk mtype="x-sdl-comment"> markers.

    For each segment, finds the matching <mrk mtype="seg" mid="N"> inside
    the correct trans-unit's <target> and wraps its content.

    Args:
        content: Raw SDLXLIFF content (after target replacement)
        seg_to_cmt: Dict mapping segment_id to (cmt_uuid, comment_text)
    """
    if not seg_to_cmt:
        return content

    log = log_callback or (lambda msg: None)

    # Group by trans-unit ID for efficient processing
    tu_comments = {}  # tu_id -> [(mid, cmt_id)]
    for seg_id, (cmt_id, _comment_text) in seg_to_cmt.items():
        parts = seg_id.rsplit('_', 1)
        if len(parts) == 2:
            tu_id, mid = parts
        else:
            tu_id = seg_id
            mid = None
        tu_comments.setdefault(tu_id, []).append((mid, cmt_id))

    wrapped_count = 0

    def _process_tu(tu_match):
        nonlocal wrapped_count
        tu_block = tu_match.group(0)

        # Extract trans-unit ID
        tu_id_m = re.search(r'<trans-unit\s+[^>]*?id="([^"]+)"', tu_block)
        if not tu_id_m:
            return tu_block
        tu_id = tu_id_m.group(1)

        if tu_id not in tu_comments:
            return tu_block

        # Find <target>...</target>
        target_m = re.search(r'(<target[^>]*>)(.*?)(</target>)', tu_block, re.DOTALL)
        if not target_m:
            return tu_block

        target_open = target_m.group(1)
        target_inner = target_m.group(2)
        target_close = target_m.group(3)

        for mid, cmt_id in tu_comments[tu_id]:
            if mid is not None:
                # Wrap content of <mrk mtype="seg" mid="N">CONTENT</mrk>
                mrk_pattern = (
                    r'(<mrk\s+'
                    r'(?=[^>]*\bmtype="seg")'
                    r'(?=[^>]*\bmid="' + re.escape(mid) + r'")'
                    r'[^>]*(?<!/)>)'
                    r'(.*?)'
                    r'(</mrk>)'
                )

                def _wrap_mrk(m):
                    nonlocal wrapped_count
                    wrapped_count += 1
                    return (m.group(1) +
                            f'<mrk mtype="x-sdl-comment" sdl:cid="{cmt_id}">' +
                            m.group(2) + '</mrk>' + m.group(3))

                target_inner = re.sub(mrk_pattern, _wrap_mrk, target_inner,
                                      flags=re.DOTALL, count=1)
            else:
                # Unsegmented TU – wrap entire target content
                wrapped_count += 1
                target_inner = (f'<mrk mtype="x-sdl-comment" sdl:cid="{cmt_id}">' +
                                target_inner + '</mrk>')

        new_target = target_open + target_inner + target_close
        return tu_block[:target_m.start()] + new_target + tu_block[target_m.end():]

    content = re.sub(
        r'<trans-unit\s[^>]*>.*?</trans-unit>',
        _process_tu, content, flags=re.DOTALL
    )

    log(f"  Wrapped {wrapped_count} segment(s) with comment markers")
    return content


def _save_sdlxliff_file(xliff_file: SDLXLIFFFile, output_path: str,
                         segment_comments: Dict[str, str] = None,
                         log_callback=None, username: str = "user") -> bool:
    """
    Save a single SDLXLIFF file using text-based replacement.

    Reads the original source file as raw bytes (preserving BOM),
    applies regex replacements for translated content and status
    attributes, then writes to output_path.

    Args:
        xliff_file: Parsed SDLXLIFF file with updated segments
        output_path: Path to write the output file
        log_callback: Optional logging function

    Returns:
        True if saved successfully
    """
    log = log_callback or (lambda msg: None)

    if not xliff_file.file_path:
        return False

    try:
        # Build segment map for quick lookup
        segment_map = {s.segment_id: s for s in xliff_file.segments}
        translated_count = sum(1 for s in xliff_file.segments if s.target_text)
        log(f"  {Path(xliff_file.file_path).name}: {len(segment_map)} segments, "
            f"{translated_count} with translations")

        # Read the original file as raw bytes to preserve BOM
        source_path = Path(xliff_file.file_path)
        raw_bytes = source_path.read_bytes()

        # Detect and preserve BOM
        bom = b''
        if raw_bytes.startswith(b'\xef\xbb\xbf'):
            bom = b'\xef\xbb\xbf'
            raw_bytes = raw_bytes[3:]

        content = raw_bytes.decode('utf-8')

        # Strip existing comment markers FIRST – nested <mrk mtype="x-sdl-comment">
        # inside <mrk mtype="seg"> would break the lazy regex in _replace_target_content
        content = _strip_comment_markers(content)

        # Apply text-based replacements
        original_content = content
        content = _replace_target_content(content, xliff_file, segment_map)
        content = _replace_seg_attributes(content, xliff_file, segment_map)

        # Insert new comments (if any)
        if segment_comments:
            seg_to_cmt = {}
            for seg_id, comment_text in segment_comments.items():
                if comment_text.strip():
                    seg_to_cmt[seg_id] = (str(uuid.uuid4()), comment_text)
            if seg_to_cmt:
                content = _insert_comment_defs(content, seg_to_cmt, log, username=username)
                content = _wrap_targets_with_comments(content, seg_to_cmt, log)
                log(f"  Exported {len(seg_to_cmt)} comment(s) to SDLXLIFF (author: {username})")

        if content == original_content and translated_count > 0:
            log(f"  WARNING: File content unchanged after replacement! "
                f"({translated_count} translations may not have been inserted)")

        # Write to output path with original BOM
        out = Path(output_path)
        out.write_bytes(bom + content.encode('utf-8'))
        log(f"  Saved: {out.name}")
        return True
    except Exception as e:
        log(f"  Error saving {xliff_file.file_path}: {e}")
        return False


# ─── Standalone SDLXLIFF Handler ───────────────────────────────────────────────

class StandaloneSDLXLIFFHandler:
    """
    Handler for standalone .sdlxliff files (without a Trados package wrapper).

    Supports loading one or more .sdlxliff files, extracting segments,
    updating translations, and saving back with text-based replacement
    (preserving BOM, XML formatting, and namespaces).
    """

    def __init__(self, log_callback=None):
        self.log = log_callback or print
        self.parser = SDLXLIFFParser(log_callback)
        self.xliff_files: List[SDLXLIFFFile] = []
        self.source_paths: List[str] = []

    def load(self, file_paths: List[str], progress_callback=None) -> bool:
        """
        Load one or more .sdlxliff files.

        Validates that all files share the same language pair.

        If progress_callback is supplied it's called as
        callback('parse', current, total, filename) before each file is parsed
        and once more with current==total when parsing is complete.
        Returning False from the callback cancels the load; this function
        returns False in that case (and self.xliff_files is left empty).

        Returns:
            True if at least one file loaded successfully
        """
        self.xliff_files = []
        self.source_paths = []

        total = len(file_paths)
        cancelled = False
        for idx, file_path in enumerate(file_paths):
            if progress_callback:
                if progress_callback('parse', idx, total, Path(file_path).name) is False:
                    self.log("SDLXLIFF load cancelled by caller")
                    cancelled = True
                    break
            try:
                xliff_file = self.parser.parse_file(file_path)
                if xliff_file and xliff_file.segments:
                    self.xliff_files.append(xliff_file)
                    self.source_paths.append(file_path)
                    self.log(f"  Loaded: {Path(file_path).name} ({len(xliff_file.segments)} segments)")
                else:
                    self.log(f"  Warning: No segments found in {Path(file_path).name}")
            except Exception as e:
                self.log(f"  Error loading {Path(file_path).name}: {e}")
                traceback.print_exc()

        if cancelled:
            self.xliff_files = []
            self.source_paths = []
            return False

        if progress_callback:
            if progress_callback('parse', total, total, "Parsing complete") is False:
                self.xliff_files = []
                self.source_paths = []
                return False

        if not self.xliff_files:
            return False

        # Validate language consistency across files
        if len(self.xliff_files) > 1:
            ref_src = self.xliff_files[0].source_lang
            ref_tgt = self.xliff_files[0].target_lang
            for xf in self.xliff_files[1:]:
                if xf.source_lang != ref_src or xf.target_lang != ref_tgt:
                    self.log(f"  Warning: Language mismatch in {Path(xf.file_path).name} "
                             f"({xf.source_lang}→{xf.target_lang} vs {ref_src}→{ref_tgt})")

        total = sum(len(xf.segments) for xf in self.xliff_files)
        self.log(f"Loaded {len(self.xliff_files)} file(s), {total} segments total")
        return True

    def get_all_segments(self) -> List[SDLSegment]:
        """Get all segments from all loaded files as a flat list."""
        segments = []
        for xliff_file in self.xliff_files:
            segments.extend(xliff_file.segments)
        return segments

    def get_source_lang(self) -> str:
        """Return source language from first loaded file."""
        return self.xliff_files[0].source_lang if self.xliff_files else ''

    def get_target_lang(self) -> str:
        """Return target language from first loaded file."""
        return self.xliff_files[0].target_lang if self.xliff_files else ''

    def update_translations(self, translations: Dict[str, str],
                            statuses: Optional[Dict[str, str]] = None) -> int:
        """
        Batch update translations by segment_id → target_text.

        ``statuses`` optionally maps segment_id → the project's status key
        ('confirmed', 'approved', 'draft', …) so the export carries the real
        confirmation level through to the Trados ``conf`` attribute. Defaults
        to 'draft' when a segment's status isn't supplied.

        Returns:
            Number of segments updated
        """
        statuses = statuses or {}
        count = 0
        for xliff_file in self.xliff_files:
            for segment in xliff_file.segments:
                if segment.segment_id in translations:
                    new_text = translations[segment.segment_id]
                    if segment.target_text != new_text:
                        segment.modified = True
                    segment.target_text = new_text
                    segment.status = statuses.get(segment.segment_id, 'draft')
                    count += 1
        return count

    def save_file(self, xliff_file: SDLXLIFFFile, output_path: str) -> bool:
        """Save a single SDLXLIFF file to the given path."""
        comments = getattr(self, 'segment_comments', None)
        user = getattr(self, 'username', 'user')
        return _save_sdlxliff_file(xliff_file, output_path, comments, self.log, username=user)

    def save_all(self, output_dir: str) -> List[str]:
        """
        Save all modified SDLXLIFF files to output_dir with '_translated' suffix.

        Returns:
            List of saved file paths
        """
        saved = []
        for xliff_file in self.xliff_files:
            stem = Path(xliff_file.file_path).stem
            ext = Path(xliff_file.file_path).suffix
            output_path = str(Path(output_dir) / f"{stem}_translated{ext}")
            if self.save_file(xliff_file, output_path):
                saved.append(output_path)
        return saved


# ─── Trados Package Handler ────────────────────────────────────────────────────

class TradosPackageHandler:
    """
    Handler for Trados Studio project packages (SDLPPX/SDLRPX).
    
    This class provides methods to:
    - Extract and parse SDLPPX packages
    - Import segments into Supervertaler projects
    - Update translations in SDLXLIFF files
    - Create return packages (SDLRPX)
    """
    
    def __init__(self, log_callback=None):
        self.log = log_callback or print
        self.parser = SDLXLIFFParser(log_callback)
        self.package: Optional[TradosPackage] = None
        self.extract_dir: Optional[str] = None
    
    def load_package(self, package_path: str, extract_dir: str = None,
                     progress_callback=None) -> Optional[TradosPackage]:
        """
        Load and extract a Trados package.

        Args:
            package_path: Path to .sdlppx or .sdlrpx file
            extract_dir: Directory to extract to (temp if not specified)
            progress_callback: Optional callable(stage: str, current: int, total: int,
                message: str) -> bool. Return False to cancel loading. Called during
                extraction and SDLXLIFF parsing so large packages don't look frozen.

        Returns:
            TradosPackage object with parsed content, or None on error/cancel.
        """
        try:
            package_path = Path(package_path)

            if not package_path.exists():
                self.log(f"ERROR: Package not found: {package_path}")
                return None

            # Determine package type
            ext = package_path.suffix.lower()
            if ext not in ['.sdlppx', '.sdlrpx']:
                self.log(f"ERROR: Not a Trados package: {ext}")
                return None

            package_type = 'sdlppx' if ext == '.sdlppx' else 'sdlrpx'

            # Create extraction directory
            if extract_dir:
                self.extract_dir = Path(extract_dir)
            else:
                self.extract_dir = Path(tempfile.mkdtemp(prefix='sdlppx_'))

            self.extract_dir.mkdir(parents=True, exist_ok=True)

            # Extract the ZIP
            self.log(f"Extracting {package_path.name}...")
            if progress_callback:
                if progress_callback('extract', 0, 1,
                                     f"Extracting {package_path.name}...") is False:
                    return None
            with zipfile.ZipFile(package_path, 'r') as zf:
                zf.extractall(self.extract_dir)
            if progress_callback:
                if progress_callback('extract', 1, 1, "Extraction complete") is False:
                    return None

            # Find and parse the project file
            project_file = None
            for f in self.extract_dir.glob('*.sdlproj'):
                project_file = f
                break

            if not project_file:
                self.log("ERROR: No .sdlproj file found in package")
                return None

            # Parse project file
            project_info = self._parse_project_file(project_file)

            # Create package object
            self.package = TradosPackage(
                package_path=str(package_path),
                package_type=package_type,
                project_name=project_info.get('name', package_path.stem),
                source_lang=project_info.get('source_lang', 'en'),
                target_lang=project_info.get('target_lang', ''),
                created_at=project_info.get('created_at', ''),
                created_by=project_info.get('created_by', ''),
                extract_dir=str(self.extract_dir)
            )

            # Find and parse SDLXLIFF files
            if self._load_xliff_files(progress_callback=progress_callback) is False:
                return None
            
            total_segments = sum(len(f.segments) for f in self.package.xliff_files)
            self.log(f"Loaded package: {self.package.project_name}")
            self.log(f"  Languages: {self.package.source_lang} -> {self.package.target_lang}")
            self.log(f"  Files: {len(self.package.xliff_files)}")
            self.log(f"  Segments: {total_segments}")
            
            return self.package
            
        except Exception as e:
            self.log(f"ERROR loading package: {e}")
            traceback.print_exc()
            return None
    
    def _parse_project_file(self, project_file: Path) -> Dict:
        """Parse the .sdlproj XML file for project metadata."""
        info = {}
        
        try:
            tree = ET.parse(project_file)
            root = tree.getroot()
            
            # Project name (from filename or attribute)
            info['name'] = project_file.stem.split('-')[0] if '-' in project_file.stem else project_file.stem
            
            # Package metadata
            info['created_at'] = root.get('PackageCreatedAt', '')
            info['created_by'] = root.get('PackageCreatedBy', '')
            
            # Language directions
            lang_dir = root.find('.//LanguageDirection')
            if lang_dir is not None:
                info['source_lang'] = lang_dir.get('SourceLanguageCode', 'en')
                info['target_lang'] = lang_dir.get('TargetLanguageCode', '')
            
        except Exception as e:
            self.log(f"Warning: Could not parse project file: {e}")
        
        return info
    
    def _load_xliff_files(self, progress_callback=None):
        """Find and load SDLXLIFF files from the TARGET language folder only.

        Trados packages contain SDLXLIFF files in both source and target language
        folders. We only want to load from the target folder (e.g., nl-nl/) since
        that's where the translator works.

        If progress_callback is supplied it's called as
        callback('parse', current, total, filename) after each file is parsed.
        Returning False from the callback cancels the load; this function then
        returns False. Returns None on success.
        """
        if not self.package or not self.extract_dir:
            return None

        extract_path = Path(self.extract_dir)
        target_lang = self.package.target_lang.lower()

        # Resolve which folder holds the target-language SDLXLIFFs
        target_folder = extract_path / target_lang
        if not target_folder.exists():
            # Fallback: find folder by matching language code patterns (nl-NL, nl_NL, ...)
            self.log(f"Target folder '{target_lang}' not found, searching alternatives...")
            target_folder = None
            source_lang = self.package.source_lang.lower()
            for folder in extract_path.iterdir():
                if not folder.is_dir():
                    continue
                folder_lower = folder.name.lower().replace('_', '-')
                if folder_lower == target_lang or folder_lower.startswith(target_lang.split('-')[0]):
                    if folder_lower == source_lang or folder_lower.startswith(source_lang.split('-')[0]):
                        continue
                    target_folder = folder
                    break
            if target_folder is None:
                self.log(f"Warning: Could not find target language folder for {target_lang}")
                return None

        self.log(f"Loading SDLXLIFF files from folder: {target_folder.name}/")

        # Collect paths first so we can report a meaningful total
        xliff_paths = list(target_folder.rglob('*.sdlxliff'))
        total = len(xliff_paths)

        for idx, xliff_path in enumerate(xliff_paths):
            if progress_callback:
                if progress_callback('parse', idx, total, xliff_path.name) is False:
                    self.log("SDLXLIFF parse cancelled by caller")
                    return False
            xliff_file = self.parser.parse_file(str(xliff_path))
            if xliff_file:
                self.package.xliff_files.append(xliff_file)

        if progress_callback:
            if progress_callback('parse', total, total, "Parsing complete") is False:
                return False
        return None
    
    def get_all_segments(self) -> List[SDLSegment]:
        """Get all segments from all files in the package."""
        if not self.package:
            return []
        
        segments = []
        for xliff_file in self.package.xliff_files:
            segments.extend(xliff_file.segments)
        
        return segments
    
    def update_segment(self, segment_id: str, target_text: str, status: str = 'draft') -> bool:
        """
        Update a segment's translation.
        
        Args:
            segment_id: The segment ID to update
            target_text: New target text
            status: New status (translated, approved, etc.)
            
        Returns:
            True if updated successfully
        """
        if not self.package:
            return False
        
        for xliff_file in self.package.xliff_files:
            for segment in xliff_file.segments:
                if segment.segment_id == segment_id:
                    # Only flag as modified if the target text actually
                    # changed (skip TM matches re-written with same text).
                    if segment.target_text != target_text:
                        segment.modified = True
                    segment.target_text = target_text
                    segment.status = status
                    return True
        
        return False
    
    def update_translations(self, translations: Dict[str, str],
                            statuses: Optional[Dict[str, str]] = None) -> int:
        """
        Batch update translations.
        
        Args:
            translations: Dict mapping segment_id to target_text
            
        Returns:
            Number of segments updated
        """
        statuses = statuses or {}
        count = 0
        for segment_id, target_text in translations.items():
            if self.update_segment(segment_id, target_text,
                                   statuses.get(segment_id, 'draft')):
                count += 1
        return count
    
    def save_xliff_files(self) -> bool:
        """
        Save all modified SDLXLIFF files using text-based replacement.

        Instead of round-tripping through ElementTree.write() (which mangles
        BOM, XML declaration quotes, namespace prefixes, and whitespace), we
        read the original file as raw text and do targeted regex replacements
        for <target> content and sdl:seg attributes. This preserves the
        original file byte-for-byte except for the changed segments.

        Returns:
            True if all files saved successfully
        """
        if not self.package:
            return False

        self.log("Saving SDLXLIFF files...")

        for xliff_file in self.package.xliff_files:
            if not xliff_file.file_path:
                continue

            # Build segment map for quick lookup
            segment_map = {s.segment_id: s for s in xliff_file.segments}
            translated_count = sum(1 for s in xliff_file.segments if s.target_text)
            self.log(f"  {Path(xliff_file.file_path).name}: {len(segment_map)} segments, {translated_count} with translations")

            # Read the original file as raw bytes to preserve BOM
            file_path = Path(xliff_file.file_path)
            if not file_path.exists():
                self.log(f"  WARNING: File not found: {file_path}")
                continue
            raw_bytes = file_path.read_bytes()

            # Detect and preserve BOM
            bom = b''
            if raw_bytes.startswith(b'\xef\xbb\xbf'):
                bom = b'\xef\xbb\xbf'
                raw_bytes = raw_bytes[3:]

            content = raw_bytes.decode('utf-8')

            # Strip existing comment markers FIRST – nested <mrk mtype="x-sdl-comment">
            # inside <mrk mtype="seg"> would break the lazy regex in _replace_target_content
            content = _strip_comment_markers(content)

            # Apply text-based replacements
            original_content = content
            content = self._replace_target_content(content, xliff_file, segment_map)
            content = self._replace_seg_attributes(content, xliff_file, segment_map)

            # Apply comment export if available
            comments = getattr(self, 'segment_comments', None)
            if comments:
                seg_to_cmt = {}
                for seg_id, comment_text in comments.items():
                    if comment_text.strip():
                        seg_to_cmt[seg_id] = (str(uuid.uuid4()), comment_text)
                if seg_to_cmt:
                    user = getattr(self, 'username', 'user')
                    content = _insert_comment_defs(content, seg_to_cmt, self.log, username=user)
                    content = _wrap_targets_with_comments(content, seg_to_cmt, self.log)
                    self.log(f"  Exported {len(seg_to_cmt)} comment(s) to SDLXLIFF (author: {user})")

            # Verify that changes were actually made
            if content == original_content and translated_count > 0:
                self.log(f"  WARNING: File content unchanged after replacement! "
                         f"({translated_count} translations may not have been inserted)")
                # Log sample segment IDs for diagnosis
                sample = list(segment_map.keys())[:3]
                self.log(f"    Segment ID samples: {sample}")

            # Write back with original BOM
            file_path.write_bytes(bom + content.encode('utf-8'))
            self.log(f"  Saved: {file_path.name}")

        return True

    def _markers_to_xml(self, text: str) -> str:
        """Delegate to module-level function (backward compatibility)."""
        return _markers_to_xml(text)

    def _replace_target_content(self, content: str, xliff_file: SDLXLIFFFile,
                                segment_map: Dict[str, 'SDLSegment']) -> str:
        """Delegate to module-level function (backward compatibility)."""
        return _replace_target_content(content, xliff_file, segment_map)

    def _replace_seg_attributes(self, content: str, xliff_file: SDLXLIFFFile,
                                segment_map: Dict[str, 'SDLSegment']) -> str:
        """Delegate to module-level function (backward compatibility)."""
        return _replace_seg_attributes(content, xliff_file, segment_map)

    def _update_xliff_tree(self, xliff_file: SDLXLIFFFile):
        """Update the XML tree with segment translations."""
        # Build segment map for quick lookup
        segment_map = {s.segment_id: s for s in xliff_file.segments}
        
        root = xliff_file.root
        
        # Find all trans-units
        for tu in root.findall('.//xliff:trans-unit', NAMESPACES):
            tu_id = tu.get('id', '')
            
            # Get target element (create if missing)
            target_elem = tu.find('xliff:target', NAMESPACES)
            if target_elem is None:
                target_elem = tu.find('target')
            
            # Check for segmented content
            seg_source = tu.find('xliff:seg-source', NAMESPACES)
            if seg_source is None:
                seg_source = tu.find('seg-source')
            
            if seg_source is not None:
                # Update segmented content
                self._update_segmented_target(tu, target_elem, segment_map)
            else:
                # Single segment
                segment = segment_map.get(tu_id)
                if segment and target_elem is not None:
                    # Update target text
                    self._set_element_text(target_elem, segment.target_text)
            
            # Update segment confirmation status in sdl:seg-defs
            self._update_segment_status(tu, segment_map, tu_id)
    
    def _update_segmented_target(self, tu: ET.Element, target_elem: ET.Element, 
                                  segment_map: Dict[str, SDLSegment]):
        """Update segmented target content with translations."""
        if target_elem is None:
            return
        
        tu_id = tu.get('id', '')
        
        # Find all target mrk elements
        target_mrks = target_elem.findall('.//xliff:mrk[@mtype="seg"]', NAMESPACES)
        if not target_mrks:
            target_mrks = target_elem.findall('.//mrk[@mtype="seg"]')
        
        for mrk in target_mrks:
            mid = mrk.get('mid')
            if mid:
                segment_id = f"{tu_id}_{mid}"
                segment = segment_map.get(segment_id)
                if segment:
                    # Update the mrk element text
                    self._set_element_text(mrk, segment.target_text)
    
    def _update_segment_status(self, tu: ET.Element, segment_map: Dict[str, SDLSegment], tu_id: str):
        """
        Update segment confirmation status in sdl:seg-defs.
        
        Changes the conf attribute from 'Draft' to 'Translated' for segments
        that have been translated in Supervertaler.
        """
        # Status mapping from internal to SDL format
        # Trados terminology: "Translated" = confirmed by translator,
        # "ApprovedTranslation" = approved by reviewer
        status_to_conf = {
            'draft': 'Draft',
            'confirmed': 'Translated',          # Confirmed = Trados "Translated"
            'approved': 'ApprovedTranslation',
            'proofread': 'ApprovedTranslation',
            'rejected': 'RejectedTranslation',
            'not_translated': 'Draft',
            'not_started': 'Draft',
        }

        # Find sdl:seg-defs within this trans-unit (try with namespace first)
        seg_defs = tu.find('.//sdl:seg-defs', {'sdl': NAMESPACES['sdl']})
        if seg_defs is None:
            seg_defs = tu.find('.//{%s}seg-defs' % NAMESPACES['sdl'])
        if seg_defs is None:
            # Try without namespace
            for child in tu:
                if child.tag.endswith('seg-defs'):
                    seg_defs = child
                    break

        if seg_defs is None:
            return

        # Update each seg element
        for seg_elem in seg_defs:
            if not seg_elem.tag.endswith('seg'):
                continue

            seg_id = seg_elem.get('id', '')

            # Build segment_id to look up in our map
            # For segmented content: tu_id_seg_id
            # For single segment: tu_id
            segment = segment_map.get(f"{tu_id}_{seg_id}")
            if not segment:
                segment = segment_map.get(tu_id)

            if segment:
                # Get the new conf value based on segment status
                new_conf = status_to_conf.get(segment.status, 'Translated')

                # Update the conf attribute
                current_conf = seg_elem.get('conf', '')
                if current_conf != new_conf:
                    seg_elem.set('conf', new_conf)

                # Update origin to 'interactive' only for segments the user
                # translated in this session. Leave TM-matched segments untouched.
                if (new_conf in ('Translated', 'ApprovedTranslation')
                        and segment.target_text and segment.modified):
                    seg_elem.set('origin', 'interactive')
                    # Remove stale TM/MT match attributes
                    for attr in ('origin-system', 'percent', 'text-match'):
                        if attr in seg_elem.attrib:
                            del seg_elem.attrib[attr]

    def _set_element_text(self, elem: ET.Element, text: str):
        """
        Set element text, converting Supervertaler marker tags back to SDLXLIFF
        XML elements.

        Marker format (from import):
          <N>content</N>  → <g id="N">content</g>  (paired formatting tag)
          <N/>            → <x id="N"/>             (standalone tag)
        """
        # Clear existing children (we rebuild from marker text)
        for child in list(elem):
            elem.remove(child)
        elem.text = None

        if not text:
            elem.text = ''
            return

        # Check if text contains any marker tags at all (fast path)
        if not re.search(r'<[A-Za-z0-9_]+[>/]', text):
            elem.text = text
            return

        # Resolve paired tags from innermost outward by repeatedly
        # replacing the innermost match until none remain
        self._build_element_content(elem, text)

    def _build_element_content(self, parent: ET.Element, text: str):
        """
        Parse marker text and build mixed XML content on parent element.

        Handles nested paired tags by resolving innermost first.
        E.g. "before <14>177</14>Lu after" becomes:
          parent.text = "before "
          <g id="14"> with text "177" and tail "Lu after"
        """
        g_tag = f'{{{XLIFF_NS}}}g'
        x_tag = f'{{{XLIFF_NS}}}x'

        # Tokenize: split text into plain-text and tag tokens
        # Pattern matches: <ID>  </ID>  <ID/>  (numeric or alphanumeric IDs)
        _tid = _TAG_ID
        token_re = re.compile(rf'(<{_tid}>|</{_tid}>|<{_tid}/>)')
        tokens = token_re.split(text)

        # Build a tree using a stack approach
        # Each stack frame is (element, tag_id or None for root)
        stack = [(parent, None)]

        for token in tokens:
            if not token:
                continue

            # Opening paired tag: <ID>
            m_open = re.fullmatch(rf'<({_tid})>', token)
            if m_open:
                tag_id = m_open.group(1)
                g_elem = ET.SubElement(stack[-1][0], g_tag)
                g_elem.set('id', tag_id)
                stack.append((g_elem, tag_id))
                continue

            # Closing paired tag: </ID>
            m_close = re.fullmatch(rf'</({_tid})>', token)
            if m_close:
                tag_id = m_close.group(1)
                # Pop matching frame (or ignore if mismatched)
                if len(stack) > 1 and stack[-1][1] == tag_id:
                    stack.pop()
                continue

            # Standalone tag: <ID/>
            m_standalone = re.fullmatch(rf'<({_tid})/>',  token)
            if m_standalone:
                tag_id = m_standalone.group(1)
                x_elem = ET.SubElement(stack[-1][0], x_tag)
                x_elem.set('id', tag_id)
                continue

            # Plain text – append to the current element
            current_elem = stack[-1][0]
            children = list(current_elem)
            if children:
                # Append as tail of the last child
                last_child = children[-1]
                last_child.tail = (last_child.tail or '') + token
            else:
                # Append to element's own text
                current_elem.text = (current_elem.text or '') + token

    def create_return_package(self, output_path: str = None) -> Optional[str]:
        """
        Create a return package (SDLRPX) with translations.

        Args:
            output_path: Path for the return package (auto-generated if not specified)

        Returns:
            Path to the created package
        """
        if not self.package or not self.extract_dir:
            self.log("ERROR: No package loaded")
            return None

        try:
            # Save all XLIFF files first
            self.save_xliff_files()

            # Update .sdlproj for return package
            self._update_project_file_for_return()

            # Generate output path if not specified
            if not output_path:
                original = Path(self.package.package_path)
                output_path = original.parent / f"{original.stem}_translated.sdlrpx"

            output_path = Path(output_path)
            target_lang = self.package.target_lang.lower()
            source_lang = self.package.source_lang.lower()

            # Create the return package (ZIP)
            # Include: .sdlproj + source lang SDLXLIFF (unchanged) + target lang SDLXLIFF
            # Exclude: Reports/, File Types/, and other non-essential files
            self.log(f"Creating return package: {output_path.name}")

            with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                extract_path = Path(self.extract_dir)
                for file_path in extract_path.rglob('*'):
                    if not file_path.is_file():
                        continue
                    rel_path = file_path.relative_to(extract_path)
                    parts = rel_path.parts

                    # Include .sdlproj files at root level
                    # Strip target language suffix (e.g. _nl-NL) from the
                    # filename to match the Trados Studio convention.
                    if len(parts) == 1 and file_path.suffix.lower() == '.sdlproj':
                        proj_name = file_path.name
                        tgt = self.package.target_lang  # e.g. "nl-NL"
                        if tgt and f'_{tgt}' in proj_name:
                            proj_name = proj_name.replace(f'_{tgt}', '')
                        zf.write(file_path, proj_name)
                        continue

                    # Include files in source language folder (unchanged)
                    if parts and parts[0].lower() == source_lang:
                        zf.write(file_path, rel_path)
                        continue

                    # Include files in target language folder
                    if parts and parts[0].lower() == target_lang:
                        zf.write(file_path, rel_path)
                        continue

                    # Skip everything else (Reports/, File Types/, etc.)

            self.log(f"Created return package: {output_path}")
            return str(output_path)

        except Exception as e:
            self.log(f"ERROR creating return package: {e}")
            traceback.print_exc()
            return None

    def _update_project_file_for_return(self):
        """
        Modify the .sdlproj XML for a return package.

        Uses regex-based string replacement to preserve exact XML formatting
        while changing key attributes that Trados Studio expects in a return package.
        """
        proj_files = list(Path(self.extract_dir).glob('*.sdlproj'))
        if not proj_files:
            self.log("Warning: No .sdlproj found to update")
            return

        proj_path = proj_files[0]
        try:
            content = proj_path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            content = proj_path.read_text(encoding='utf-8-sig')

        now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.0000000Z')
        username = getattr(self, 'username', '') or os.environ.get('USERNAME', os.environ.get('USER', 'user'))

        # 1. PackageType → ReturnPackage
        content = content.replace(
            'PackageType="ProjectPackage"',
            'PackageType="ReturnPackage"'
        )

        # 2. Update PackageCreatedAt timestamp
        content = re.sub(
            r'PackageCreatedAt="[^"]*"',
            f'PackageCreatedAt="{now}"',
            content
        )

        # 3. Update PackageCreatedBy only (not other CreatedBy attributes!)
        content = re.sub(
            r'PackageCreatedBy="[^"]*"',
            f'PackageCreatedBy="{username}"',
            content
        )

        # 4. ConfirmationStatistics: move Draft counts to Translated
        def _swap_draft_to_translated(match):
            block = match.group(0)
            draft_m = re.search(r'<Draft\s+([^/]*)/>', block)
            translated_m = re.search(r'<Translated\s+([^/]*)/>', block)
            if draft_m and translated_m:
                draft_attrs = draft_m.group(1).strip()
                block = re.sub(
                    r'<Draft\s+[^/]*/>',
                    '<Draft Words="0" Characters="0" Segments="0" Placeables="0" Tags="0" />',
                    block
                )
                block = re.sub(
                    r'<Translated\s+[^/]*/>',
                    f'<Translated {draft_attrs}/>',
                    block
                )
            return block

        content = re.sub(
            r'<ConfirmationStatistics[^>]*>.*?</ConfirmationStatistics>',
            _swap_draft_to_translated,
            content,
            flags=re.DOTALL
        )

        # 5. ManualTask: mark as completed
        def _complete_manual_task(match):
            block = match.group(0)
            block = re.sub(r'PercentComplete="\d+"', 'PercentComplete="100"', block)
            block = re.sub(r'Status="[^"]*"', 'Status="Completed"', block)
            # Add CompletedAt if not present
            if 'CompletedAt=' not in block:
                block = re.sub(
                    r'(<ManualTask\s[^>]*?)(>)',
                    rf'\1 CompletedAt="{now}"\2',
                    block
                )
            # Mark TaskFile(s) as completed
            block = block.replace('Completed="false"', 'Completed="true"')
            return block

        content = re.sub(
            r'<ManualTask\s.*?</ManualTask>',
            _complete_manual_task,
            content,
            flags=re.DOTALL
        )

        # 6. Remove AutomaticTask sections (not needed in return package)
        content = re.sub(
            r'\s*<AutomaticTask\s.*?</AutomaticTask>',
            '',
            content,
            flags=re.DOTALL
        )

        # 7. Remove TermbaseConfiguration section (not needed in return package)
        content = re.sub(
            r'\s*<TermbaseConfiguration[^>]*>.*?</TermbaseConfiguration>',
            '',
            content,
            flags=re.DOTALL
        )

        proj_path.write_text(content, encoding='utf-8')
        self.log(f"  Updated .sdlproj: PackageType=ReturnPackage, CreatedBy={username}")
    
    def cleanup(self):
        """Clean up extracted files."""
        if self.extract_dir and Path(self.extract_dir).exists():
            try:
                shutil.rmtree(self.extract_dir)
                self.log("Cleaned up extracted files")
            except Exception as e:
                self.log(f"Warning: Could not clean up: {e}")


def detect_trados_package_type(file_path: str) -> Optional[str]:
    """
    Detect if a file is a Trados package and return its type.
    
    Returns:
        'sdlppx', 'sdlrpx', or None if not a Trados package
    """
    path = Path(file_path)
    ext = path.suffix.lower()
    
    if ext == '.sdlppx':
        return 'sdlppx'
    elif ext == '.sdlrpx':
        return 'sdlrpx'
    
    # Check if it's a ZIP with SDLXLIFF files
    if ext == '.zip':
        try:
            with zipfile.ZipFile(file_path, 'r') as zf:
                names = zf.namelist()
                if any(n.endswith('.sdlxliff') for n in names):
                    if any(n.endswith('.sdlproj') for n in names):
                        return 'sdlppx'  # Assume project package
        except:
            pass
    
    return None
