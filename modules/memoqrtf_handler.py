r"""
memoQ Bilingual RTF Handler

This module handles the import and export of memoQ bilingual RTF files.
memoQ exports bilingual tables in RTF format with a 5-column structure.

Format Structure:
- RTF file with embedded table
- 5 columns per row:
  1. Segment ID (number + GUID on separate lines)
  2. Source text with inline formatting
  3. Target text (empty on export, filled on re-import)
  4. Comments
  5. Status (Not started, Edited, Confirmed, etc.)
- Rows separated by \row RTF control word
- Cells separated by \cell RTF control word

Formatting markers:
- Bold: \b ... \b0
- Italic: \i ... \i0
- Underline: \ul ... \ul0

This handler is designed to be compatible with the memoQ DOCX bilingual handler,
sharing the same 5-column structure and workflow.
"""

import re
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass


# RTF special character mappings (hex escapes)
RTF_ESCAPE_MAP = {
    r"\'e9": "é", r"\'e8": "è", r"\'ea": "ê", r"\'eb": "ë",
    r"\'e0": "à", r"\'e1": "á", r"\'e2": "â", r"\'e4": "ä", r"\'e3": "ã",
    r"\'f2": "ò", r"\'f3": "ó", r"\'f4": "ô", r"\'f6": "ö", r"\'f5": "õ",
    r"\'fa": "ú", r"\'f9": "ù", r"\'fb": "û", r"\'fc": "ü",
    r"\'ec": "ì", r"\'ed": "í", r"\'ee": "î", r"\'ef": "ï",
    r"\'f1": "ñ", r"\'e7": "ç", r"\'df": "ß",
    r"\'c9": "É", r"\'c8": "È", r"\'c0": "À", r"\'c1": "Á",
    r"\'d3": "Ó", r"\'da": "Ú", r"\'d1": "Ñ",
    r"\'ab": "«", r"\'bb": "»", r"\'b0": "°",
    r"\'96": "–", r"\'97": "–",
    r"\'92": "'", r"\'93": """, r"\'94": """,
    r"\'85": "…", r"\'a0": " ",
    r"\'91": "'", r"\'95": "•",
    r"\'b7": "·", r"\'d7": "×", r"\'f7": "÷",
}

# Language code mapping (RTF uses Windows LCID codes)
RTF_LANG_CODES = {
    1033: "en", 2057: "en", 3081: "en", 4105: "en",  # English variants
    1043: "nl", 2067: "nl",  # Dutch
    1031: "de", 2055: "de", 3079: "de",  # German
    1036: "fr", 2060: "fr", 3084: "fr", 4108: "fr",  # French
    3082: "es", 1034: "es", 2058: "es",  # Spanish
    1040: "it", 2064: "it",  # Italian
    1046: "pt", 2070: "pt",  # Portuguese
    1030: "da", 1035: "fi", 1044: "no", 2068: "no", 1053: "sv",  # Nordic
    1045: "pl", 1029: "cs", 1051: "sk", 1038: "hu",  # Central European
    1048: "ro", 1026: "bg", 1050: "hr", 2074: "sr", 3098: "sr",  # SE European
    1060: "sl", 1058: "uk", 1049: "ru", 1059: "be",  # Eastern European
    1063: "lt", 1062: "lv", 1061: "et",  # Baltic
    2052: "zh", 1028: "zh", 1041: "ja", 1042: "ko",  # Asian
    1037: "he", 1025: "ar", 2049: "ar", 1065: "fa", 1055: "tr", 1032: "el",  # Middle Eastern
}

# Language name to code mapping
LANG_NAME_MAP = {
    'english': 'en', 'dutch': 'nl', 'german': 'de', 'french': 'fr',
    'spanish': 'es', 'italian': 'it', 'portuguese': 'pt', 'polish': 'pl',
    'czech': 'cs', 'slovak': 'sk', 'hungarian': 'hu', 'romanian': 'ro',
    'bulgarian': 'bg', 'greek': 'el', 'russian': 'ru', 'ukrainian': 'uk',
    'swedish': 'sv', 'danish': 'da', 'finnish': 'fi', 'norwegian': 'no',
    'japanese': 'ja', 'chinese': 'zh', 'korean': 'ko', 'arabic': 'ar',
    'turkish': 'tr', 'hebrew': 'he', 'belgium': 'nl', 'flemish': 'nl',
    'brazilian': 'pt', 'states': 'en', 'kingdom': 'en',
}


@dataclass
class MemoQSegment:
    """Represents a memoQ segment with all metadata."""
    segment_id: int  # Numeric segment ID (1, 2, 3...)
    guid: str  # GUID from memoQ
    source_text: str  # Source text (may contain formatting tags)
    target_text: str = ""  # Target text
    comment: str = ""  # Comment column
    status: str = "Not started"  # Status column
    row_index: int = 0  # Row index in RTF for export

    # Store raw RTF cell content for preserving formatting on export
    raw_source_cell: str = ""
    raw_target_cell: str = ""

    def __repr__(self):
        preview = self.source_text[:50] + "..." if len(self.source_text) > 50 else self.source_text
        return f"MemoQSegment(id={self.segment_id}, source='{preview}')"


class MemoQRTFHandler:
    """
    Handler for memoQ bilingual RTF files.

    This class provides methods to:
    - Load and parse memoQ bilingual RTF files
    - Extract source segments with formatting
    - Update target segments with translations
    - Save modified files ready for re-import to memoQ
    """

    def __init__(self):
        self.raw_rtf: str = ""  # Original RTF content
        self.segments: List[MemoQSegment] = []
        self.file_path: Optional[str] = None
        self.source_lang: str = "nl"
        self.target_lang: str = "en"
        self.languages_detected: bool = False  # False: the two above are placeholders
        self.file_header: str = ""  # First row metadata
        self.rtf_header: str = ""  # RTF header up to first row
        self.preserve_formatting: bool = True  # Extract formatting tags from source

        # Store cell positions for precise insertion during export
        self._row_positions: List[Dict] = []

    def load(self, file_path: str) -> bool:
        """
        Load a memoQ bilingual RTF file.

        Args:
            file_path: Path to the memoQ bilingual RTF file

        Returns:
            bool: True if loaded successfully, False otherwise
        """
        try:
            self.file_path = file_path

            # Try different encodings
            for encoding in ['utf-8', 'cp1252', 'latin-1', 'cp1250']:
                try:
                    with open(file_path, 'r', encoding=encoding, errors='replace') as f:
                        self.raw_rtf = f.read()
                    break
                except UnicodeDecodeError:
                    continue

            # Detect languages from header row
            self._detect_languages()

            # Parse segments
            self._parse_segments()

            print(f"Successfully loaded memoQ RTF: {file_path}")
            print(f"Languages: {self.source_lang} -> {self.target_lang}")
            print(f"Total segments: {len(self.segments)}")

            return True

        except Exception as e:
            print(f"ERROR loading memoQ RTF: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _detect_languages(self):
        """Detect source and target languages from RTF header row."""
        # Look for language names in the header row (row 2)
        # Pattern: Dutch (Belgium), English (United States), etc.

        # Find bold header cells with language names
        # Pattern matches: {\rtlch...\b Dutch (Belgium)\cell }
        header_pattern = re.compile(
            r'\\b\s+([A-Za-z]+(?:\s*\([^)]+\))?)\s*\\cell',
            re.IGNORECASE
        )

        matches = header_pattern.findall(self.raw_rtf)

        # Look for language headers (skip "ID", "Comment", "Status")
        lang_headers = []
        for match in matches:
            text = match.strip().lower()
            # Skip non-language headers
            if text in ('id', 'comment', 'status'):
                continue
            # Check if it looks like a language (contains known language names)
            for lang_name in LANG_NAME_MAP.keys():
                if lang_name in text:
                    lang_headers.append(text)
                    break

        # First language header is source, second is target
        if len(lang_headers) >= 2:
            for lang_name, lang_code in LANG_NAME_MAP.items():
                if lang_name in lang_headers[0]:
                    self.source_lang = lang_code
                    break
            for lang_name, lang_code in LANG_NAME_MAP.items():
                if lang_name in lang_headers[1]:
                    self.target_lang = lang_code
                    break
            self.languages_detected = True

    def _decode_rtf_text(self, text: str) -> str:
        """Decode RTF escape sequences to plain text."""
        result = text

        # Replace RTF hex character codes
        for rtf_code, char in RTF_ESCAPE_MAP.items():
            result = result.replace(rtf_code, char)

        # Handle Unicode escapes: \\uNNNN? or \\uc0\\uNNNN
        def replace_unicode(match):
            code = int(match.group(1))
            if code < 0:
                code = 65536 + code  # Handle negative values (RTF convention)
            try:
                return chr(code)
            except (ValueError, OverflowError):
                return ''

        # Pattern for \\uc0\\uNNNN or \\uNNNN?
        result = re.sub(r'\\uc0\\u(-?\d+)', replace_unicode, result)
        result = re.sub(r'\\u(-?\d+)\?', replace_unicode, result)
        result = re.sub(r'\\u(-?\d+) ', replace_unicode, result)

        # RTF special-character control symbols
        result = result.replace('\\~', '\u00a0')   # non-breaking space
        result = result.replace('\\-', '\u00ad')   # optional (soft) hyphen
        result = result.replace('\\_', '\u2011')   # non-breaking hyphen

        # Unescape RTF special characters
        result = result.replace(r'\{', '{')
        result = result.replace(r'\}', '}')
        result = result.replace(r'\\', '\\')

        # Handle line breaks
        result = result.replace(r'\line', '\n')
        result = result.replace(r'\par', '\n')

        return result

    def _extract_cell_text(self, cell_content: str, preserve_formatting: bool = False) -> str:
        """
        Extract plain text from RTF cell content.

        Args:
            cell_content: Raw RTF cell content
            preserve_formatting: If True, convert RTF formatting to HTML-like tags
        """
        # Remove outer braces if present
        content = cell_content.strip()
        if content.startswith('{') and content.endswith('}'):
            content = content[1:-1]

        # Flatten mqInternal inline tag groups: {\noproof\cs99\f0\fs20\cf13 [1]} → [1]
        # These nested brace groups contain memoQ inline tag markers like [1], [1\}, \{2]
        # We extract just the tag content (with escaped braces decoded) and keep it inline.
        def _flatten_mqinternal(m):
            tag_content = m.group(1)
            # Decode escaped braces within tag content: \{ → {, \} → }
            tag_content = tag_content.replace('\\{', '{').replace('\\}', '}')
            return tag_content
        content = re.sub(
            r'\{\\noproof\\cs99[^}]*?\s((?:[^\{}]|\\[{}])+)\}',
            _flatten_mqinternal,
            content
        )

        # Convert RTF subscript/superscript brace groups to <sub>/<sup> tags:
        # {\sub V} → <sub>V</sub>, {\super 2} → <sup>2</sup>
        content = re.sub(r'\{\\sub\s+((?:[^{}]|\\[{}])*)\}', r'<sub>\1</sub>', content)
        content = re.sub(r'\{\\super\s+((?:[^{}]|\\[{}])*)\}', r'<sup>\1</sup>', content)

        # Remove RTF formatting preamble
        content = re.sub(r'\\rtlch\\fcs\d+\s*', '', content)
        content = re.sub(r'\\ltrch\\fcs\d+\s*', '', content)
        content = re.sub(r'\\af?\d+\s*', '', content)
        content = re.sub(r'\\lang\d+\s*', '', content)
        content = re.sub(r'\\langfe\d+\s*', '', content)
        content = re.sub(r'\\langnp\d+\s*', '', content)
        content = re.sub(r'\\noproof\s*', '', content)
        content = re.sub(r'\\fs\d+\s*', '', content)
        content = re.sub(r'\\f\d+\s*', '', content)
        content = re.sub(r'\\cf\d+\s*', '', content)

        if preserve_formatting:
            # Convert RTF formatting markers directly to HTML tags.
            # Process end markers FIRST so \b doesn't match the start of \b0.
            content = re.sub(r'\\b0\s?', '</b>', content)
            content = re.sub(r'\\i0\s?', '</i>', content)
            content = re.sub(r'\\ul0\s?', '</u>', content)
            # Start markers: consume delimiter space, or match before next control word
            content = re.sub(r'\\b(?:\s|(?=\\))', '<b>', content)
            content = re.sub(r'\\i(?:\s|(?=\\))', '<i>', content)
            content = re.sub(r'\\ul(?:\s|(?=\\))', '<u>', content)

            # Clean up empty tags
            content = re.sub(r'<b></b>', '', content)
            content = re.sub(r'<i></i>', '', content)
            content = re.sub(r'<u></u>', '', content)
        else:
            # Just remove formatting codes
            content = re.sub(r'\\b0?\s*', '', content)
            content = re.sub(r'\\i0?\s*', '', content)
            content = re.sub(r'\\ul0?\s*', '', content)

        # Replace RTF character control words with actual characters
        # Must happen BEFORE the generic control word strip below.
        # Use \s? to consume only the single RTF delimiter space, not content spaces.
        content = re.sub(r'\\ldblquote\s?', '\u201c', content)  # left double quote
        content = re.sub(r'\\rdblquote\s?', '\u201d', content)  # right double quote
        content = re.sub(r'\\lquote\s?', '\u2018', content)     # left single quote
        content = re.sub(r'\\rquote\s?', '\u2019', content)     # right single quote
        content = re.sub(r'\\emdash\s?', '\u2014', content)     # em dash
        content = re.sub(r'\\endash\s?', '\u2013', content)     # en dash
        content = re.sub(r'\\bullet\s?', '\u2022', content)     # bullet
        content = re.sub(r'\\line\s?', '\n', content)           # line break
        content = re.sub(r'\\tab\s?', '\t', content)            # tab

        # RTF special-character control symbols (single non-alpha char after backslash)
        content = content.replace('\\~', '\u00a0')               # non-breaking space
        content = content.replace('\\-', '\u00ad')               # optional (soft) hyphen
        content = content.replace('\\_', '\u2011')               # non-breaking hyphen

        # Decode Unicode escapes BEFORE generic strip (e.g. \uc0\u8220 → ")
        # The generic strip below would otherwise eat \uc0 and \u8220 as control words.
        def _replace_unicode(match):
            code = int(match.group(1))
            if code < 0:
                code = 65536 + code
            try:
                return chr(code)
            except (ValueError, OverflowError):
                return ''
        content = re.sub(r'\\uc0\\u(-?\d+)\s?', _replace_unicode, content)
        content = re.sub(r'\\u(-?\d+)\?', _replace_unicode, content)
        content = re.sub(r'\\u(-?\d+) ', _replace_unicode, content)

        # Decode RTF hex escapes before generic strip (e.g. \'93 → ")
        for rtf_code, char in RTF_ESCAPE_MAP.items():
            content = content.replace(rtf_code, char)

        # Remove remaining control words
        content = re.sub(r'\\[a-z]+\d*\s*', '', content)

        # Decode remaining special characters (brace/backslash unescaping)
        content = content.replace(r'\{', '{')
        content = content.replace(r'\}', '}')
        content = content.replace('\\\\', '\\')

        # Remove \cell marker
        content = re.sub(r'\\cell\s*', '', content)

        # Clean up whitespace
        content = re.sub(r'\s+', ' ', content)
        content = content.strip()

        return content

    def _parse_segments(self):
        """Parse RTF content to extract segments."""
        self.segments = []
        self._row_positions = []

        rtf = self.raw_rtf

        # Split into rows using \row marker
        # Each row contains 5 cells separated by \cell

        # Find all row blocks
        # Pattern: content between row definitions and \row
        # Brace-aware cell matching that handles:
        #   - Nested brace groups: {\noproof\cs99\f0\fs20\cf13 [1]}
        #   - Escaped braces at any level: \{ and \} (RTF literal brace escapes)
        # The inner group (?:...) matches: non-brace chars | escaped braces | nested {groups}
        _cell = r'(\{\\rtlch\\fcs1(?:[^{}]|\\[{}]|\{(?:[^{}]|\\[{}])*\})*?\\cell\s*\})'
        _row_pat = (
            _cell + r'\s*'   # Cell 1: ID
            + _cell + r'\s*'   # Cell 2: Source
            + _cell + r'\s*'   # Cell 3: Target
            + _cell + r'\s*'   # Cell 4: Comment
            + _cell + r'\s*'   # Cell 5: Status
            + r'\\row'
        )
        row_pattern = re.compile(_row_pat, re.DOTALL)

        # Find the header row to skip it
        header_found = False
        row_index = 0

        for match in row_pattern.finditer(rtf):
            id_cell = match.group(1)
            source_cell = match.group(2)
            target_cell = match.group(3)
            comment_cell = match.group(4)
            status_cell = match.group(5)

            # Extract ID text
            id_text = self._extract_cell_text(id_cell)

            # Skip header row (contains "ID" text)
            if 'ID' in id_text and not id_text.replace('ID', '').strip().isdigit():
                header_found = True
                continue

            # Skip metadata row (first row, contains file info)
            if not header_found:
                continue

            # Parse segment ID and GUID
            # Format: "1 \n GUID" or just "1"
            id_parts = id_text.split()
            if not id_parts:
                continue

            try:
                segment_id = int(id_parts[0])
            except ValueError:
                continue

            # Extract GUID if present (usually on second line)
            guid = ""
            if len(id_parts) > 1:
                # GUID is usually the last part that looks like a GUID
                for part in id_parts[1:]:
                    if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', part, re.I):
                        guid = part
                        break

            # Extract text from cells
            source_text = self._extract_cell_text(source_cell, preserve_formatting=self.preserve_formatting)
            target_text = self._extract_cell_text(target_cell)
            comment_text = self._extract_cell_text(comment_cell)
            status_text = self._extract_cell_text(status_cell)

            # Create segment
            segment = MemoQSegment(
                segment_id=segment_id,
                guid=guid,
                source_text=source_text,
                target_text=target_text,
                comment=comment_text,
                status=status_text,
                row_index=row_index,
                raw_source_cell=source_cell,
                raw_target_cell=target_cell
            )

            self.segments.append(segment)

            # Store position info for export
            self._row_positions.append({
                'match_start': match.start(),
                'match_end': match.end(),
                'target_cell_start': match.start(3),
                'target_cell_end': match.end(3),
                'status_cell_start': match.start(5),
                'status_cell_end': match.end(5),
            })

            row_index += 1

    def get_source_texts(self) -> List[str]:
        """Get list of source texts for translation."""
        return [seg.source_text for seg in self.segments]

    def get_target_texts(self) -> List[str]:
        """Get list of target texts."""
        return [seg.target_text for seg in self.segments]

    def get_segment_count(self) -> int:
        """Get the number of segments."""
        return len(self.segments)

    def update_translation(self, index: int, translation: str) -> bool:
        """Update a single segment's translation."""
        if 0 <= index < len(self.segments):
            self.segments[index].target_text = translation
            return True
        return False

    def update_translations(self, translations: Dict[int, str]) -> int:
        """
        Update target segments with translations by index.

        Args:
            translations: Dict mapping segment index to translated text

        Returns:
            int: Number of segments updated
        """
        updated_count = 0
        for idx, translation in translations.items():
            if self.update_translation(idx, translation):
                updated_count += 1
        return updated_count

    def _encode_text_for_rtf(self, text: str) -> str:
        """Encode text for RTF format, converting HTML formatting tags to RTF."""
        # First, convert HTML-style formatting tags to RTF control words.
        # These tags come from _extract_cell_text(preserve_formatting=True)
        # which converts \b → <b>, \b0 → </b>, etc. during import.
        # We must convert them back to RTF before character-level encoding.
        text = text.replace('<b>', '\\b ')
        text = text.replace('</b>', '\\b0 ')
        text = text.replace('<i>', '\\i ')
        text = text.replace('</i>', '\\i0 ')
        text = text.replace('<u>', '\\ul ')
        text = text.replace('</u>', '\\ul0 ')

        # Subscript/superscript use brace-scoped groups: {\sub text} / {\super text}
        # Use placeholders so the braces don't get escaped in the loop below.
        text = text.replace('<sub>', '\x00SUB_OPEN\x00')
        text = text.replace('</sub>', '\x00SUB_CLOSE\x00')
        text = text.replace('<sup>', '\x00SUP_OPEN\x00')
        text = text.replace('</sup>', '\x00SUP_CLOSE\x00')

        result = []
        i = 0
        while i < len(text):
            # Skip RTF control words we just inserted (don't re-encode their backslashes)
            if text[i] == '\\' and i + 1 < len(text) and text[i + 1].isalpha():
                # This is an RTF control word – pass it through verbatim
                j = i + 1
                while j < len(text) and text[j].isalpha():
                    j += 1
                # Include optional trailing digit(s) (e.g. \b0, \ul0)
                while j < len(text) and text[j].isdigit():
                    j += 1
                # Include the delimiter space if present
                if j < len(text) and text[j] == ' ':
                    j += 1
                result.append(text[i:j])
                i = j
                continue

            char = text[i]
            code = ord(char)
            if code > 127:
                # Non-ASCII: use Unicode escape
                result.append(f'\\u{code}?')
            elif char == '{':
                result.append('\\{')
            elif char == '}':
                result.append('\\}')
            elif char == '\\':
                result.append('\\\\')
            elif char == '\n':
                result.append('\\line ')
            elif char == '\r':
                pass  # Skip carriage returns
            else:
                result.append(char)
            i += 1

        encoded = ''.join(result)

        # Restore subscript/superscript placeholders as RTF brace groups
        encoded = encoded.replace('\x00SUB_OPEN\x00', '{\\sub ')
        encoded = encoded.replace('\x00SUB_CLOSE\x00', '}')
        encoded = encoded.replace('\x00SUP_OPEN\x00', '{\\super ')
        encoded = encoded.replace('\x00SUP_CLOSE\x00', '}')

        return encoded

    def save(self, output_path: str) -> bool:
        """
        Save the RTF file with updated translations.

        Args:
            output_path: Path for the output RTF file

        Returns:
            bool: True if saved successfully, False otherwise
        """
        try:
            if not self.segments:
                print("WARNING: No segments to save")
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(self.raw_rtf)
                return True

            # Build modified RTF by replacing target cells
            modified_rtf = self.raw_rtf

            # Process in reverse order to maintain positions
            for i in range(len(self.segments) - 1, -1, -1):
                segment = self.segments[i]
                if i >= len(self._row_positions):
                    continue

                pos_info = self._row_positions[i]

                if segment.target_text:
                    # Encode translation for RTF
                    encoded_translation = self._encode_text_for_rtf(segment.target_text)

                    # Get target language code
                    target_lang_code = 1033  # Default English
                    for code, lang in RTF_LANG_CODES.items():
                        if lang == self.target_lang:
                            target_lang_code = code
                            break

                    # Build new target cell
                    new_target_cell = (
                        f'{{\\rtlch\\fcs1 \\ltrch\\fcs0\\lang{target_lang_code} '
                        f'{encoded_translation}\\cell }}'
                    )

                    # Replace target cell
                    modified_rtf = (
                        modified_rtf[:pos_info['target_cell_start']] +
                        new_target_cell +
                        modified_rtf[pos_info['target_cell_end']:]
                    )

            # Save modified RTF
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(modified_rtf)

            print(f"Saved memoQ RTF to: {output_path}")
            return True

        except Exception as e:
            print(f"ERROR saving memoQ RTF: {e}")
            import traceback
            traceback.print_exc()
            return False

    def has_translations(self) -> bool:
        """Check if any segments have translations."""
        return any(seg.target_text for seg in self.segments)


def is_memoq_rtf(file_path: str) -> bool:
    """
    Check if a file is a memoQ bilingual RTF file.

    Args:
        file_path: Path to the RTF file

    Returns:
        bool: True if it's a memoQ bilingual RTF
    """
    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read(5000)  # Read first 5KB

        # Check for memoQ markers
        # memoQ RTF contains "CAUTION: Do not change segment ID" or similar
        # and has the characteristic 5-column structure

        if 'CAUTION:' in content or 'Important!' in content:
            if 'segment ID' in content.lower() or 'source text' in content.lower():
                return True

        # Check for memoQ version string pattern
        if re.search(r'V\d+\.\d+\.\d+\s+MQ\d+', content):
            return True

        # Check for characteristic header pattern
        if re.search(r'\\b\s+ID\\cell.*?\\b\s+.*?\\cell.*?\\b\s+.*?\\cell.*?Comment\\cell.*?Status\\cell', content, re.DOTALL):
            return True

        return False

    except Exception:
        return False


# Test function
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python memoqrtf_handler.py <path_to_rtf>")
        sys.exit(1)

    handler = MemoQRTFHandler()
    if handler.load(sys.argv[1]):
        print(f"\nExtracted {len(handler.segments)} segments:")
        for seg in handler.segments[:10]:
            print(f"  [{seg.segment_id}] {seg.source_text[:60]}...")

        if len(handler.segments) > 10:
            print(f"  ... and {len(handler.segments) - 10} more segments")
