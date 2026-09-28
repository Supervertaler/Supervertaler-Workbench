"""
Déjà Vu X3 Bilingual RTF Handler

This module handles the import and export of Déjà Vu X3 bilingual RTF files.
Déjà Vu exports bilingual tables in RTF format with a 4-column structure.

Format Structure:
- RTF file with embedded table
- 4 columns per row:
  1. Segment ID (7-digit format like 0000049)
  2. Source text with inline tags
  3. Target text (empty on export, filled on re-import)
  4. Comments (usually empty)
- Rows separated by \\row RTF control word
- Cells separated by \\cell RTF control word

Tag System:
- Inline tags: {NNNNN} format (e.g., {00108}, {00109})
- Tags appear in pairs (opening and closing)
- Tags wrap text: {00108}Vind jouw CS{00109}
- In RTF, tags are escaped: \\{00108\\}text\\{00109\\}

Critical for re-import:
- RTF structure must be preserved exactly
- Tags must be retained in translations
- Segment IDs must not be modified
"""

import re
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass


# RTF special character mappings
RTF_ESCAPE_MAP = {
    r"\'e9": "é",  # e-acute
    r"\'e8": "è",  # e-grave
    r"\'ea": "ê",  # e-circumflex
    r"\'eb": "ë",  # e-diaeresis
    r"\'e0": "à",  # a-grave
    r"\'e1": "á",  # a-acute
    r"\'e2": "â",  # a-circumflex
    r"\'e4": "ä",  # a-diaeresis
    r"\'e3": "ã",  # a-tilde
    r"\'f2": "ò",  # o-grave
    r"\'f3": "ó",  # o-acute
    r"\'f4": "ô",  # o-circumflex
    r"\'f6": "ö",  # o-diaeresis
    r"\'f5": "õ",  # o-tilde
    r"\'fa": "ú",  # u-acute
    r"\'f9": "ù",  # u-grave
    r"\'fb": "û",  # u-circumflex
    r"\'fc": "ü",  # u-diaeresis
    r"\'ec": "ì",  # i-grave
    r"\'ed": "í",  # i-acute
    r"\'ee": "î",  # i-circumflex
    r"\'ef": "ï",  # i-diaeresis
    r"\'f1": "ñ",  # n-tilde
    r"\'e7": "ç",  # c-cedilla
    r"\'df": "ß",  # German sharp s
    r"\'c9": "É",  # E-acute
    r"\'c8": "È",  # E-grave
    r"\'c0": "À",  # A-grave
    r"\'c1": "Á",  # A-acute
    r"\'d3": "Ó",  # O-acute
    r"\'da": "Ú",  # U-acute
    r"\'d1": "Ñ",  # N-tilde
    r"\'ab": "«",  # left guillemet
    r"\'bb": "»",  # right guillemet
    r"\'b0": "°",  # degree
    r"\'96": "–",  # en-dash
    r"\'97": "–",  # em-dash
    r"\'92": "'",  # right single quote
    r"\'93": """,  # left double quote
    r"\'94": """,  # right double quote
    r"\'85": "…",  # ellipsis
    r"\'a0": " ",  # non-breaking space
}

# Déjà Vu tag pattern: {NNNNN} where N is a digit
DEJAVU_TAG_PATTERN = re.compile(r'\{(\d{5})\}')

# Language code mapping (RTF uses Windows LCID codes)
RTF_LANG_CODES = {
    # Western European
    1033: "English",
    2057: "English (UK)",
    3081: "English (AU)",
    4105: "English (CA)",
    1043: "Dutch",
    2067: "Dutch (BE)",
    1031: "German",
    2055: "German (CH)",
    3079: "German (AT)",
    1036: "French",
    2060: "French (BE)",
    3084: "French (CA)",
    4108: "French (CH)",
    3082: "Spanish",
    1034: "Spanish (Traditional)",
    2058: "Spanish (MX)",
    1040: "Italian",
    2064: "Italian (CH)",
    1046: "Portuguese (BR)",
    2070: "Portuguese (PT)",
    # Nordic
    1030: "Danish",
    1035: "Finnish",
    1044: "Norwegian",
    2068: "Norwegian (Nynorsk)",
    1053: "Swedish",
    1039: "Icelandic",
    # Eastern European
    1045: "Polish",
    1029: "Czech",
    1051: "Slovak",
    1038: "Hungarian",
    1048: "Romanian",
    1026: "Bulgarian",
    1050: "Croatian",
    2074: "Serbian (Latin)",
    3098: "Serbian (Cyrillic)",
    1060: "Slovenian",
    1058: "Ukrainian",
    1049: "Russian",
    1059: "Belarusian",
    1063: "Lithuanian",
    1062: "Latvian",
    1061: "Estonian",
    # Asian
    2052: "Chinese (Simplified)",
    1028: "Chinese (Traditional)",
    1041: "Japanese",
    1042: "Korean",
    1054: "Thai",
    1066: "Vietnamese",
    1057: "Indonesian",
    1086: "Malay",
    # Middle Eastern
    1037: "Hebrew",
    1025: "Arabic",
    2049: "Arabic (Iraq)",
    1065: "Persian",
    1055: "Turkish",
    1032: "Greek",
    # Other
    1027: "Catalan",
    1069: "Basque",
    1110: "Galician",
    1024: "Neutral",  # System default
}



@dataclass
class DejaVuSegment:
    """
    Represents a Déjà Vu segment with tag information.
    """
    segment_id: str  # 7-digit ID like "0000049"
    source_text: str  # Source text with Déjà Vu tags
    target_text: str = ""  # Target text (empty on import)
    comment: str = ""  # Comment column
    row_index: int = 0  # Row index in RTF for export
    
    @property
    def tags(self) -> List[str]:
        """Extract all Déjà Vu tag numbers from source text."""
        return DEJAVU_TAG_PATTERN.findall(self.source_text)
    
    @property
    def plain_source(self) -> str:
        """Get source text without tags for translation."""
        return DEJAVU_TAG_PATTERN.sub('', self.source_text).strip()
    
    def __repr__(self):
        preview = self.source_text[:50] + "..." if len(self.source_text) > 50 else self.source_text
        return f"DejaVuSegment(id={self.segment_id}, source='{preview}')"


class DejaVuRTFHandler:
    """
    Handler for Déjà Vu X3 bilingual RTF files.
    
    This class provides methods to:
    - Load and parse Déjà Vu bilingual RTF files
    - Extract source segments with tag markers
    - Update target segments with translations
    - Save modified files ready for re-import to Déjà Vu
    """
    
    def __init__(self):
        self.raw_rtf: str = ""  # Original RTF content
        self.segments: List[DejaVuSegment] = []
        self.file_path: Optional[str] = None
        self.source_lang: str = "Dutch"
        self.target_lang: str = "Spanish"
        self.languages_detected: bool = False  # False: the two above are placeholders
        self._cell_positions: List[Tuple[int, int, int, int]] = []  # (row_idx, seg_id_start, source_start, source_end, target_start, target_end)
    
    def load(self, file_path: str) -> bool:
        """
        Load a Déjà Vu bilingual RTF file.
        
        Args:
            file_path: Path to the Déjà Vu bilingual RTF file
            
        Returns:
            bool: True if loaded successfully, False otherwise
        """
        try:
            self.file_path = file_path
            
            # Read RTF content
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                self.raw_rtf = f.read()
            
            # Detect languages from RTF
            self._detect_languages()
            
            # Parse segments
            self._parse_segments()
            
            print(f"Successfully loaded Deja Vu RTF: {file_path}")
            print(f"Languages: {self.source_lang} -> {self.target_lang}")
            print(f"Total segments: {len(self.segments)}")
            
            return True
            
        except Exception as e:
            print(f"ERROR loading Deja Vu RTF: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _detect_languages(self):
        """Detect source and target languages from RTF content."""
        # Look for language codes in RTF header or content
        # Pattern: \langNNNN or \langnpNNNN
        lang_matches = re.findall(r'\\lang(?:np)?(\d+)', self.raw_rtf)
        
        if lang_matches:
            # Count occurrences to find the main languages
            from collections import Counter
            code_counts = Counter(int(m) for m in lang_matches)
            
            # Get the two most common language codes (excluding 1024 which is neutral)
            main_codes = [code for code, _ in code_counts.most_common() if code != 1024]
            
            if len(main_codes) >= 2:
                # In Déjà Vu bilingual, typically source appears less than target
                # because target column may have formatting placeholders
                code1, code2 = main_codes[0], main_codes[1]
                
                # Map codes to languages
                lang1 = RTF_LANG_CODES.get(code1, f"Unknown ({code1})")
                lang2 = RTF_LANG_CODES.get(code2, f"Unknown ({code2})")
                
                # Heuristic: the less frequent one is likely source (content)
                # the more frequent one is target (includes empty cell formatting)
                count1 = code_counts[code1]
                count2 = code_counts[code2]
                
                if count1 > count2:
                    # code1 is more frequent, likely target
                    self.source_lang = lang2
                    self.target_lang = lang1
                else:
                    self.source_lang = lang1
                    self.target_lang = lang2
                self.languages_detected = True
                    
            elif len(main_codes) == 1:
                code = main_codes[0]
                self.source_lang = RTF_LANG_CODES.get(code, f"Unknown ({code})")
    
    def detect_language_pair(self) -> Tuple[Optional[str], Optional[str]]:
        """``(source, target)`` as language codes, or ``None`` for a side that
        was not found (issue #200). Unlike ``source_lang`` / ``target_lang``,
        this never returns the Dutch → Spanish placeholders. The pair comes from
        a frequency heuristic, so callers should let the user confirm it."""
        if not self.languages_detected:
            return (None, None)
        from modules.bilingual_lang_detect import pair_or_partial
        from modules import language_codes as _lc

        def code(name):
            if not name or name.startswith("Unknown"):
                return None
            base = _lc.base_code(name)
            return base if base and _lc.iso_to_english_name(base) else None

        return pair_or_partial(code(self.source_lang), code(self.target_lang))

    def _decode_rtf_text(self, text: str) -> str:
        """Decode RTF escape sequences to plain text."""
        result = text
        
        # Replace RTF special character codes
        for rtf_code, char in RTF_ESCAPE_MAP.items():
            result = result.replace(rtf_code, char)
        
        # Handle Unicode escapes (\uNNNNN?)
        def replace_unicode(match):
            code = int(match.group(1))
            if code < 0:
                code = 65536 + code  # Handle negative values
            return chr(code)
        
        result = re.sub(r'\\u(-?\d+)\?', replace_unicode, result)
        
        # RTF special-character control symbols
        result = result.replace('\\~', '\u00a0')   # non-breaking space
        result = result.replace('\\-', '\u00ad')   # optional (soft) hyphen
        result = result.replace('\\_', '\u2011')   # non-breaking hyphen

        # Unescape RTF special characters
        result = result.replace(r'\{', '{')
        result = result.replace(r'\}', '}')
        result = result.replace(r'\\', '\\')

        # Remove RTF control words that might remain (but keep content)
        # Be careful not to remove too much
        result = re.sub(r'\\[a-z]+\d*\s?', '', result)
        
        # Clean up multiple spaces
        result = re.sub(r'  +', ' ', result)
        
        return result.strip()
    
    def _parse_segments(self):
        """Parse RTF content to extract segments."""
        self.segments = []
        
        # RTF table structure uses \cell to separate cells and \row to end rows
        # We need to find table rows containing segment data
        
        # Split by \row to get table rows
        # But \row appears with various suffixes, so be flexible
        row_pattern = re.compile(r'\\row\b')
        
        # Find all content between table cells
        # Pattern: look for 7-digit segment ID followed by cell marker, then source, target, comment
        
        # More robust approach: extract text between \cell markers
        # Each row has: ID \cell Source \cell Target \cell Comment \cell
        
        # Find the actual table content (between table start and end)
        # The table rows follow the pattern with segment IDs like 0000049
        
        segment_pattern = re.compile(
            r'(\d{7})'  # Segment ID (7 digits)
            r'[^\\]*\\cell\s*\}'  # After ID, find \cell
            r'[^}]*\{[^}]*'  # Skip formatting
            r'([^\\]*(?:\\[^c][^\\]*)*)'  # Source text (until next \cell)
            r'\\cell\s*'  # Cell separator
            r'(.*?)'  # Target text (empty or filled)
            r'\\cell\s*'  # Cell separator
            r'(.*?)'  # Comment
            r'\\cell',  # Final cell separator
            re.DOTALL
        )
        
        # Simpler approach: find all occurrences of segment IDs followed by cell content
        # Pattern: find 7-digit numbers that look like segment IDs
        
        # Look for the actual segment pattern in RTF
        # The content shows: 0000172}...source text...\cell \cell \cell
        # This means: ID, source, empty target, empty comment
        
        # Let's use a different approach - find segments by looking for the pattern
        # of 7-digit ID followed by \cell, then text, then \cell \cell \cell
        
        # Extract raw cell content using simpler pattern
        # Split by \row to get rows first
        
        # Find all text that appears between RTF formatting codes
        # after a 7-digit segment ID
        
        # Working pattern based on RTF structure observed:
        # - ID appears as just digits after formatting codes
        # - Then \cell (end of ID cell)
        # - Then source content with embedded formatting and text
        # - Then \cell (end of source cell)
        # - Then \cell (end of empty target cell)
        # - Then \cell (end of empty comment cell)
        
        # Simplified extraction: find all 7-digit segment IDs and the text that follows
        current_pos = 0
        rtf = self.raw_rtf
        
        # Find segment ID pattern in RTF context
        # Looking for pattern like: ...insrsid9000367 0000172}... (ID followed by })
        id_pattern = re.compile(r'(?:insrsid\d+\s+)(\d{7})\}')
        
        for match in id_pattern.finditer(rtf):
            segment_id = match.group(1)
            start_pos = match.end()
            
            # Find the source text - it's between the ID and the next \cell markers
            # The pattern after ID is: {formatting}\cell }{formatting}source text}...
            
            # Look for text content in the next cell (source cell)
            # Skip to after first \cell (end of ID cell)
            cell_pattern = re.compile(r'\\cell\s*')
            cell_match = cell_pattern.search(rtf, start_pos)
            
            if not cell_match:
                continue
            
            source_start = cell_match.end()
            
            # Find the next \cell (end of source cell)
            # But we need to extract the actual text content, not RTF codes
            
            # Find the next 3 \cell markers (source, target, comment)
            cells_remaining = 3
            search_pos = source_start
            cell_positions = []
            
            for _ in range(cells_remaining):
                cell_match = cell_pattern.search(rtf, search_pos)
                if cell_match:
                    cell_positions.append((search_pos, cell_match.start()))
                    search_pos = cell_match.end()
            
            if len(cell_positions) >= 3:
                # Extract source text from first cell region
                source_region = rtf[cell_positions[0][0]:cell_positions[0][1]]
                source_text = self._extract_text_from_rtf_region(source_region)
                
                # Extract target text from second cell region (usually empty)
                target_region = rtf[cell_positions[1][0]:cell_positions[1][1]]
                target_text = self._extract_text_from_rtf_region(target_region)
                
                # Extract comment from third cell region
                comment_region = rtf[cell_positions[2][0]:cell_positions[2][1]]
                comment_text = self._extract_text_from_rtf_region(comment_region)
                
                if source_text:  # Only add if we have source text
                    segment = DejaVuSegment(
                        segment_id=segment_id,
                        source_text=source_text,
                        target_text=target_text,
                        comment=comment_text,
                        row_index=len(self.segments)
                    )
                    self.segments.append(segment)
    
    def _extract_text_from_rtf_region(self, region: str) -> str:
        """Extract plain text from an RTF region."""
        # Remove nested braces and their contents (formatting groups)
        # but keep the actual text content
        
        result = []
        depth = 0
        i = 0
        text_buffer = []
        
        while i < len(region):
            char = region[i]
            
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
            elif char == '\\':
                # Handle escape sequences
                if i + 1 < len(region):
                    next_char = region[i + 1]
                    if next_char == '{':
                        text_buffer.append('{')
                        i += 2
                        continue
                    elif next_char == '}':
                        text_buffer.append('}')
                        i += 2
                        continue
                    elif next_char == '\\':
                        text_buffer.append('\\')
                        i += 2
                        continue
                    elif next_char == "'":
                        # Hex character code
                        if i + 3 < len(region):
                            hex_code = region[i:i+4]
                            if hex_code in RTF_ESCAPE_MAP:
                                text_buffer.append(RTF_ESCAPE_MAP[hex_code])
                                i += 4
                                continue
                    # Skip control word
                    j = i + 1
                    while j < len(region) and (region[j].isalpha() or region[j].isdigit() or region[j] == '-'):
                        j += 1
                    if j < len(region) and region[j] == ' ':
                        j += 1  # Skip trailing space
                    i = j
                    continue
            elif depth == 0 or True:  # Collect text at any depth
                # Only collect if not whitespace after control word
                if char not in '{}\\\r\n':
                    text_buffer.append(char)
            
            i += 1
        
        text = ''.join(text_buffer)
        
        # Clean up: remove excessive whitespace
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()
        
        return text
    
    def extract_source_segments(self) -> List[DejaVuSegment]:
        """
        Extract all source segments from the Déjà Vu bilingual RTF.
        
        Returns:
            list: List of DejaVuSegment objects
        """
        return self.segments.copy()
    
    def get_source_texts(self) -> List[str]:
        """Get list of source texts for translation."""
        return [seg.source_text for seg in self.segments]
    
    def get_target_texts(self) -> List[str]:
        """Get list of target texts (may be empty)."""
        return [seg.target_text for seg in self.segments]
    
    def update_translations(self, translations: Dict[str, str]) -> int:
        """
        Update target segments with translations.
        
        Args:
            translations: Dict mapping segment_id to translated text
            
        Returns:
            int: Number of segments updated
        """
        updated_count = 0
        
        for segment in self.segments:
            if segment.segment_id in translations:
                segment.target_text = translations[segment.segment_id]
                updated_count += 1
        
        print(f"Updated {updated_count} target segments")
        return updated_count
    
    def update_translations_by_index(self, translations: Dict[int, str]) -> int:
        """
        Update target segments with translations by row index.
        
        Args:
            translations: Dict mapping row_index to translated text
            
        Returns:
            int: Number of segments updated
        """
        updated_count = 0
        
        for segment in self.segments:
            if segment.row_index in translations:
                segment.target_text = translations[segment.row_index]
                updated_count += 1
        
        print(f"Updated {updated_count} target segments by index")
        return updated_count
    
    def save(self, output_path: str) -> bool:
        """
        Save the RTF file with updated translations.
        
        This method modifies the RTF by inserting translations into
        the target column cells while preserving the RTF structure.
        
        Args:
            output_path: Path for the output RTF file
            
        Returns:
            bool: True if saved successfully, False otherwise
        """
        try:
            # Create translation map
            translation_map = {seg.segment_id: seg.target_text for seg in self.segments if seg.target_text}
            
            if not translation_map:
                print("WARNING: No translations to save")
                # Still save the file as-is
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(self.raw_rtf)
                return True
            
            # Modify RTF to insert translations
            modified_rtf = self._insert_translations(translation_map)
            
            # Save modified RTF
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(modified_rtf)
            
            print(f"Saved Déjà Vu RTF to: {output_path}")
            return True
            
        except Exception as e:
            print(f"ERROR saving Déjà Vu RTF: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _encode_text_for_rtf(self, text: str) -> str:
        """Encode text for RTF format."""
        result = []
        
        for char in text:
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
                result.append('\\par ')
            elif char == '\r':
                pass  # Skip carriage returns
            else:
                result.append(char)
        
        return ''.join(result)
    
    def _insert_translations(self, translations: Dict[str, str]) -> str:
        """
        Insert translations into the RTF content.
        
        This finds the target cell for each segment and inserts the translation.
        The Déjà Vu RTF format has empty target cells that look like:
        \\cell \\cell (two consecutive \\cell markers with nothing between)
        
        We insert plain text just before the third \\cell marker.
        """
        rtf = self.raw_rtf
        
        # Pattern to find segment rows by their 7-digit ID
        id_pattern = re.compile(r'(?:insrsid\d+\s+)(\d{7})\}')
        cell_pattern = re.compile(r'\\cell\s*')
        
        # Collect modifications (position, replacement_text)
        modifications = []
        
        for match in id_pattern.finditer(rtf):
            segment_id = match.group(1)
            
            if segment_id not in translations:
                continue
            
            translation = translations[segment_id]
            if not translation:
                continue
            
            start_pos = match.end()
            
            # Find cells after the ID:
            # Cell 1: end of ID cell
            # Cell 2: end of source cell  
            # Cell 3: end of target cell (we insert BEFORE this)
            
            cell1 = cell_pattern.search(rtf, start_pos)
            if not cell1:
                continue
            
            cell2 = cell_pattern.search(rtf, cell1.end())
            if not cell2:
                continue
            
            cell3 = cell_pattern.search(rtf, cell2.end())
            if not cell3:
                continue
            
            # Insert position is right after cell2 (before cell3)
            insert_pos = cell2.end()
            
            # Encode the translation for RTF
            encoded_translation = self._encode_text_for_rtf(translation)
            
            # Get target language code for RTF
            target_lang_code = self._get_rtf_lang_code(self.target_lang) or 3082
            
            # Build simple RTF-formatted text
            # Format: {formatting}text{} - properly balanced braces
            replacement = (
                f'{{\\rtlch\\fcs1 \\af37 \\ltrch\\fcs0 '
                f'\\f37\\lang{target_lang_code}\\langfe{target_lang_code}'
                f'\\langnp{target_lang_code} {encoded_translation}}}'
            )
            
            modifications.append((insert_pos, replacement))
        
        # Apply modifications from end to start to preserve positions
        modifications.sort(key=lambda x: x[0], reverse=True)
        
        for insert_pos, replacement in modifications:
            rtf = rtf[:insert_pos] + replacement + rtf[insert_pos:]
        
        return rtf
    
    def _get_rtf_lang_code(self, lang_name: str) -> Optional[int]:
        """Get RTF language code from language name."""
        for code, name in RTF_LANG_CODES.items():
            if name.lower() == lang_name.lower():
                return code
        # No exact name ("Portuguese", or a code such as "pt"): take the first
        # variant of the same language rather than falling back to Spanish.
        from modules import language_codes as _lc
        base = _lc.base_code(lang_name)
        if base:
            for code, name in RTF_LANG_CODES.items():
                if _lc.base_code(name) == base:
                    return code
        return None
    
    def get_segment_by_id(self, segment_id: str) -> Optional[DejaVuSegment]:
        """Get a segment by its ID."""
        for segment in self.segments:
            if segment.segment_id == segment_id:
                return segment
        return None
    
    def get_segment_count(self) -> int:
        """Get the number of segments."""
        return len(self.segments)
    
    def has_translations(self) -> bool:
        """Check if any segments have translations."""
        return any(seg.target_text for seg in self.segments)


def extract_dejavu_tags(text: str) -> List[str]:
    """
    Extract Déjà Vu tag numbers from text.
    
    Args:
        text: Text containing Déjà Vu tags
        
    Returns:
        List of tag numbers (5-digit strings)
    """
    return DEJAVU_TAG_PATTERN.findall(text)


def strip_dejavu_tags(text: str) -> str:
    """
    Remove Déjà Vu tags from text.
    
    Args:
        text: Text containing Déjà Vu tags
        
    Returns:
        Text with tags removed
    """
    return DEJAVU_TAG_PATTERN.sub('', text).strip()


def validate_dejavu_tags(source: str, target: str) -> Tuple[bool, List[str]]:
    """
    Validate that target contains all tags from source.
    
    Args:
        source: Source text with tags
        target: Target text that should contain same tags
        
    Returns:
        Tuple of (is_valid, list of missing tags)
    """
    source_tags = set(extract_dejavu_tags(source))
    target_tags = set(extract_dejavu_tags(target))
    
    missing = source_tags - target_tags
    extra = target_tags - source_tags
    
    is_valid = len(missing) == 0 and len(extra) == 0
    
    issues = []
    if missing:
        issues.extend([f"Missing: {{{t}}}" for t in missing])
    if extra:
        issues.extend([f"Extra: {{{t}}}" for t in extra])
    
    return is_valid, issues


# Test function
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python dejavurtf_handler.py <path_to_rtf>")
        sys.exit(1)
    
    handler = DejaVuRTFHandler()
    if handler.load(sys.argv[1]):
        segments = handler.extract_source_segments()
        print(f"\nExtracted {len(segments)} segments:")
        for i, seg in enumerate(segments[:10]):  # Show first 10
            print(f"  [{seg.segment_id}] {seg.source_text[:60]}...")
        
        if len(segments) > 10:
            print(f"  ... and {len(segments) - 10} more segments")
