"""
Spellcheck Manager for Supervertaler
=====================================
Provides spellchecking functionality using Hunspell dictionaries.
Supports custom word lists and project-specific dictionaries.

Features:
- Hunspell dictionary support (via cyhunspell or spylls)
- Spylls: Pure Python Hunspell (works on Windows/Python 3.12+)
- Fallback to pyspellchecker for basic checking
- Custom word lists (global and per-project)
- Integration with PyQt6 text editors
"""

import os
import re
from pathlib import Path
from typing import List, Set, Dict, Optional, Tuple

# Try to import hunspell (cyhunspell) - may fail on Windows/Python 3.12+
try:
    from hunspell import Hunspell
    HAS_HUNSPELL = True
except ImportError:
    HAS_HUNSPELL = False
    Hunspell = None

# Try spylls (pure Python Hunspell reimplementation) - works on all platforms
try:
    from spylls.hunspell import Dictionary as SpyllsDictionary
    HAS_SPYLLS = True
except ImportError:
    HAS_SPYLLS = False
    SpyllsDictionary = None

# Fallback to pyspellchecker (no regional variants like en_US vs en_GB)
SPELLCHECKER_IMPORT_ERROR = None
try:
    from spellchecker import SpellChecker
    HAS_SPELLCHECKER = True
except ImportError as e:
    HAS_SPELLCHECKER = False
    SpellChecker = None
    SPELLCHECKER_IMPORT_ERROR = str(e)


def parse_word_list(text: str) -> Tuple[List[str], int]:
    """Parse a custom-dictionary word list (issue #109).

    Accepts one word per line, as in ``custom_words.txt``, and also a Hunspell
    ``.dic`` file: its leading entry-count line is skipped and ``/FLAGS``
    suffixes are removed. Blank lines and ``#`` comments are ignored.

    Returns ``(words, duplicates)``: the unique words, lower-cased (the custom
    dictionary is case-insensitive) and sorted, plus how many duplicate lines
    were dropped.
    """
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    seen: Set[str] = set()
    duplicates = 0
    first = True
    for line in lines:
        word = line.strip()
        if not word or word.startswith('#'):
            continue
        if first:
            first = False
            if word.isdigit():
                continue  # Hunspell .dic entry count
        word = word.split('/', 1)[0].strip().lower()
        if not word:
            continue
        if word in seen:
            duplicates += 1
        else:
            seen.add(word)
    return sorted(seen), duplicates


class SpellcheckManager:
    """
    Manages spellchecking for Supervertaler.
    
    Supports:
    - Hunspell dictionaries (.dic/.aff files)
    - Custom word lists
    - Per-project dictionaries
    """
    
    # Map language codes to display names with variants
    CODE_TO_DISPLAY = {
        'en_US': 'English (US)',
        'en_GB': 'English (GB)',
        'en_AU': 'English (AU)',
        'en_CA': 'English (CA)',
        'en_ZA': 'English (ZA)',
        'nl_NL': 'Dutch (NL)',
        'nl_BE': 'Dutch (BE)',
        'de_DE': 'German (DE)',
        'de_AT': 'German (AT)',
        'de_CH': 'German (CH)',
        'fr_FR': 'French (FR)',
        'fr_CA': 'French (CA)',
        'fr_BE': 'French (BE)',
        'fr_CH': 'French (CH)',
        'es_ES': 'Spanish (ES)',
        'es_MX': 'Spanish (MX)',
        'es_AR': 'Spanish (AR)',
        'pt_PT': 'Portuguese (PT)',
        'pt_BR': 'Portuguese (BR)',
        'it_IT': 'Italian',
        'pl_PL': 'Polish',
        'ru_RU': 'Russian',
        'sv_SE': 'Swedish',
        'da_DK': 'Danish',
        'nb_NO': 'Norwegian (Bokmål)',
        'nn_NO': 'Norwegian (Nynorsk)',
        'fi_FI': 'Finnish',
        'cs_CZ': 'Czech',
        'sk_SK': 'Slovak',
        'hu_HU': 'Hungarian',
        'ro_RO': 'Romanian',
        'bg_BG': 'Bulgarian',
        'uk_UA': 'Ukrainian',
        'el_GR': 'Greek',
        'tr_TR': 'Turkish',
        'zh_CN': 'Chinese (Simplified)',
        'zh_TW': 'Chinese (Traditional)',
        'ja_JP': 'Japanese',
        'ko_KR': 'Korean',
    }
    
    # Reverse mapping: display name to code
    DISPLAY_TO_CODE = {v: k for k, v in CODE_TO_DISPLAY.items()}
    
    # Legacy mapping for project files that use simple names like "English"
    LANGUAGE_MAP = {
        'English': 'en_US',
        'Dutch': 'nl_NL',
        'German': 'de_DE',
        'French': 'fr_FR',
        'Spanish': 'es_ES',
        'Italian': 'it_IT',
        'Portuguese': 'pt_PT',
        'Polish': 'pl_PL',
        'Russian': 'ru_RU',
        'Chinese': 'zh_CN',
        'Japanese': 'ja_JP',
        'Korean': 'ko_KR',
    }
    
    # Short code mappings (for project files that store "nl" instead of "Dutch")
    SHORT_CODE_MAP = {
        'en': 'en_US',
        'nl': 'nl_NL',
        'de': 'de_DE',
        'fr': 'fr_FR',
        'es': 'es_ES',
        'it': 'it_IT',
        'pt': 'pt_PT',
        'pl': 'pl_PL',
        'ru': 'ru_RU',
        'zh': 'zh_CN',
        'ja': 'ja_JP',
        'ko': 'ko_KR',
    }
    
    # Reverse mapping (legacy)
    CODE_TO_LANGUAGE = {v: k for k, v in LANGUAGE_MAP.items()}
    
    def __init__(self, user_data_path: str = None):
        """
        Initialize the spellcheck manager.
        
        Args:
            user_data_path: Path to user data directory for custom dictionaries
        """
        self.user_data_path = Path(user_data_path) if user_data_path else Path("user_data")
        self.dictionaries_path = self.user_data_path / "workbench" / "dictionaries"
        self.custom_words_file = self.dictionaries_path / "custom_words.txt"
        
        # Ensure directories exist
        self.dictionaries_path.mkdir(parents=True, exist_ok=True)
        
        # Current spell checker instance
        self._hunspell: Optional[Hunspell] = None
        self._spylls = None  # SpyllsDictionary instance
        self._spellchecker: Optional[SpellChecker] = None
        self._current_language: Optional[str] = None
        self._backend: str = "none"  # Track which backend is active
        
        # Custom words (global)
        self._custom_words: Set[str] = set()
        self._load_custom_words()
        
        # Session-only ignored words
        self._ignored_words: Set[str] = set()
        
        # Cache for word check results
        self._word_cache: Dict[str, bool] = {}
        
        # Enabled state
        self.enabled = True
        
        # Safety flag - if spellcheck crashes, disable permanently for session
        self._crash_detected = False
        
    def _load_custom_words(self):
        """Load custom words from file"""
        self._custom_words.clear()
        if self.custom_words_file.exists():
            try:
                with open(self.custom_words_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        word = line.strip()
                        if word and not word.startswith('#'):
                            self._custom_words.add(word.lower())
            except Exception as e:
                print(f"Error loading custom words: {e}")
    
    def _save_custom_words(self):
        """Save custom words to file"""
        try:
            with open(self.custom_words_file, 'w', encoding='utf-8') as f:
                f.write("# Supervertaler Custom Dictionary\n")
                f.write("# Add words that should not be marked as spelling errors\n\n")
                for word in sorted(self._custom_words):
                    f.write(f"{word}\n")
        except Exception as e:
            print(f"Error saving custom words: {e}")
    
    def set_language(self, language: str) -> bool:
        """
        Set the spellcheck language.
        
        Args:
            language: Language display name (e.g., "English (US)", "English (GB)"),
                      simple name (e.g., "English", "Dutch"), short code (e.g., "nl", "en"),
                      or full code (e.g., "en_US", "nl_NL")
            
        Returns:
            True if language was set successfully
        """
        # Convert language name to code if needed
        lang_code = None
        
        # First try display name with variant (English (US) -> en_US)
        lang_code = self.DISPLAY_TO_CODE.get(language)
        
        # Then try legacy full name map (English -> en_US)
        if not lang_code:
            lang_code = self.LANGUAGE_MAP.get(language)
        
        # Then try short code map (nl -> nl_NL)
        if not lang_code:
            lang_code = self.SHORT_CODE_MAP.get(language.lower() if language else '')
        
        # Fall back to using the input directly (might be en_US already)
        if not lang_code:
            lang_code = language
        
        if lang_code == self._current_language:
            return True  # Already set
        
        # Clear cache when changing language
        self._word_cache.clear()
        
        # Try Hunspell first (cyhunspell - may not work on Windows/Py3.12)
        if HAS_HUNSPELL:
            if self._try_hunspell(lang_code):
                self._current_language = lang_code
                self._spylls = None
                self._spellchecker = None
                self._backend = "hunspell"
                return True
        
        # Try spylls (pure Python Hunspell - works everywhere, supports regional variants)
        if HAS_SPYLLS:
            if self._try_spylls(lang_code):
                self._current_language = lang_code
                self._hunspell = None
                self._spellchecker = None
                self._backend = "spylls"
                return True
        
        # Fallback to pyspellchecker (no regional variants)
        if HAS_SPELLCHECKER:
            if self._try_spellchecker(lang_code):
                self._current_language = lang_code
                self._hunspell = None
                self._spylls = None
                self._backend = "pyspellchecker"
                return True
        
        return False
    
    def _try_spylls(self, lang_code: str) -> bool:
        """Try to initialize spylls (pure Python Hunspell) with the given language"""
        try:
            # Check for dictionary files in user_data/dictionaries (and subdirectories)
            dic_file = None
            aff_file = None
            
            # First check root folder
            root_dic = self.dictionaries_path / f"{lang_code}.dic"
            root_aff = self.dictionaries_path / f"{lang_code}.aff"
            if root_dic.exists() and root_aff.exists():
                dic_file = root_dic
                aff_file = root_aff
            else:
                # Search in subdirectories (e.g., dictionaries/en/en_GB.dic)
                for found_dic in self.dictionaries_path.glob(f"**/{lang_code}.dic"):
                    found_aff = found_dic.with_suffix('.aff')
                    if found_aff.exists():
                        dic_file = found_dic
                        aff_file = found_aff
                        break
            
            if dic_file and aff_file:
                # Load from local dictionaries folder
                self._spylls = SpyllsDictionary.from_files(str(dic_file.with_suffix('')))
                return True
            else:
                # Try loading from spylls' built-in dictionaries (if any)
                # spylls.hunspell.Dictionary.from_files expects a base path without extension
                try:
                    self._spylls = SpyllsDictionary.from_files(lang_code)
                    return True
                except Exception:
                    return False
        except Exception as e:
            print(f"Spylls initialization failed for {lang_code}: {e}")
            return False
    
    def _try_hunspell(self, lang_code: str) -> bool:
        """Try to initialize Hunspell with the given language"""
        try:
            # Check for dictionary files in user_data/dictionaries
            dic_file = self.dictionaries_path / f"{lang_code}.dic"
            aff_file = self.dictionaries_path / f"{lang_code}.aff"
            
            hunspell_obj = None
            if dic_file.exists() and aff_file.exists():
                hunspell_obj = Hunspell(lang_code, hunspell_data_dir=str(self.dictionaries_path))
            else:
                # Try system dictionaries
                try:
                    hunspell_obj = Hunspell(lang_code)
                except Exception:
                    return False
            
            if hunspell_obj:
                # CRITICAL: Test the spell checker with a simple word to catch potential crashes early
                # Some Hunspell configurations on Linux can crash on first use
                try:
                    hunspell_obj.spell("test")
                    self._hunspell = hunspell_obj
                    return True
                except Exception as e:
                    print(f"Hunspell test spell failed for {lang_code}: {e}")
                    return False
            
            return False
        except Exception as e:
            print(f"Hunspell initialization failed for {lang_code}: {e}")
            return False
    
    def _try_spellchecker(self, lang_code: str) -> bool:
        """Try to initialize pyspellchecker with the given language"""
        try:
            # pyspellchecker uses 2-letter codes
            short_code = lang_code.split('_')[0].lower()
            
            # Check if language is supported
            # pyspellchecker supports: en, es, de, fr, pt, nl, it, ru, ar, eu, lv
            supported = ['en', 'es', 'de', 'fr', 'pt', 'nl', 'it', 'ru', 'ar', 'eu', 'lv']
            
            target_lang = short_code if short_code in supported else 'en'
            
            # Create the spellchecker instance
            self._spellchecker = SpellChecker(language=target_lang)
            
            # Verify it's actually working by testing a common word
            # Use a simple spell check instead of checking word_frequency length
            # (word_frequency is a WordFrequency object that doesn't support len())
            try:
                test_result = self._spellchecker.known(['the', 'test'])
                if not test_result:
                    print(f"SpellChecker: Dictionary appears empty for {target_lang}")
                    self._spellchecker = None
                    return False
            except Exception:
                # If known() fails, the spellchecker is likely broken
                self._spellchecker = None
                return False
            
            return True
        except Exception as e:
            print(f"SpellChecker initialization failed for {lang_code}: {e}")
            self._spellchecker = None
            return False
    
    def check_word(self, word: str) -> bool:
        """
        Check if a word is spelled correctly.
        
        Args:
            word: The word to check
            
        Returns:
            True if the word is correct, False if misspelled
        """
        # If a crash was detected earlier, always return True (don't attempt spellcheck)
        if self._crash_detected:
            return True
        
        if not self.enabled:
            return True
        
        if not word or len(word) < 2:
            return True
        
        # Normalize word
        word_lower = word.lower()
        
        # Check cache
        if word_lower in self._word_cache:
            return self._word_cache[word_lower]
        
        # Check custom words
        if word_lower in self._custom_words:
            self._word_cache[word_lower] = True
            return True
        
        # Check ignored words (session only)
        if word_lower in self._ignored_words:
            self._word_cache[word_lower] = True
            return True
        
        # Skip if it looks like a number, tag, or special text
        if self._should_skip_word(word):
            self._word_cache[word_lower] = True
            return True
        
        # Check with spell checker
        is_correct = False
        
        if self._hunspell:
            try:
                is_correct = self._hunspell.spell(word)
            except Exception as e:
                # If Hunspell crashes, disable for the session
                print(f"Hunspell spell check error: {e}")
                self._crash_detected = True
                self.enabled = False
                is_correct = True  # Fail open
        elif self._spylls:
            try:
                is_correct = self._spylls.lookup(word)
            except Exception as e:
                print(f"Spylls spell check error: {e}")
                is_correct = True
        elif self._spellchecker:
            try:
                # pyspellchecker returns None for known words
                is_correct = word_lower in self._spellchecker
            except Exception as e:
                print(f"pyspellchecker error: {e}")
                is_correct = True
        else:
            is_correct = True  # No spell checker available
        
        self._word_cache[word_lower] = is_correct
        return is_correct
    
    def _should_skip_word(self, word: str) -> bool:
        """Check if a word should be skipped (numbers, tags, etc.)"""
        # Skip numbers
        if re.match(r'^[\d.,]+$', word):
            return True
        
        # Skip words with numbers mixed in (like serial numbers)
        if re.search(r'\d', word):
            return True
        
        # Skip single characters
        if len(word) < 2:
            return True
        
        # Skip ALL CAPS (likely acronyms)
        if word.isupper() and len(word) <= 5:
            return True
        
        # Skip HTML/XML-like tags
        if word.startswith('<') or word.endswith('>'):
            return True
        
        # Skip words starting with special characters
        if word[0] in '@#$%&':
            return True
        
        return False
    
    def get_suggestions(self, word: str, max_suggestions: int = 5) -> List[str]:
        """
        Get spelling suggestions for a misspelled word.
        
        Args:
            word: The misspelled word
            max_suggestions: Maximum number of suggestions to return
            
        Returns:
            List of suggested corrections
        """
        # Skip suggestions for very long words - spylls can hang for 30+ seconds
        # on long Dutch compound words like "gegevensverwerking" (18 chars)
        if len(word) > 12:
            return []
        
        if self._hunspell:
            try:
                suggestions = self._hunspell.suggest(word)
                return suggestions[:max_suggestions]
            except Exception:
                return []
        elif self._spylls:
            try:
                suggestions = list(self._spylls.suggest(word))
                return suggestions[:max_suggestions]
            except Exception:
                return []
        elif self._spellchecker:
            try:
                # Get candidates sorted by likelihood
                candidates = self._spellchecker.candidates(word.lower())
                if candidates:
                    return list(candidates)[:max_suggestions]
            except Exception:
                return []
        
        return []
    
    def add_to_dictionary(self, word: str):
        """
        Add a word to the custom dictionary (persistent).
        
        Args:
            word: The word to add
        """
        word_lower = word.lower()
        self._custom_words.add(word_lower)
        self._word_cache[word_lower] = True
        self._save_custom_words()
        
        # Also add to Hunspell session if available
        if self._hunspell:
            try:
                self._hunspell.add(word)
            except Exception:
                pass
    
    def ignore_word(self, word: str):
        """
        Ignore a word for the current session only.
        
        Args:
            word: The word to ignore
        """
        word_lower = word.lower()
        self._ignored_words.add(word_lower)
        self._word_cache[word_lower] = True
    
    def remove_from_dictionary(self, word: str):
        """
        Remove a word from the custom dictionary.
        
        Args:
            word: The word to remove
        """
        word_lower = word.lower()
        self._custom_words.discard(word_lower)
        self._word_cache.pop(word_lower, None)
        self._save_custom_words()
    
    def get_custom_words(self) -> List[str]:
        """Get all custom dictionary words"""
        return sorted(self._custom_words)

    def set_custom_words(self, words) -> int:
        """Replace the whole custom dictionary in one write (issue #109).
        Returns the number of words stored."""
        self._custom_words = {w.strip().lower() for w in words
                              if w and w.strip() and not w.strip().startswith('#')}
        self._word_cache.clear()
        self._save_custom_words()
        if self._hunspell:
            for word in self._custom_words:
                try:
                    self._hunspell.add(word)
                except Exception:
                    pass
        return len(self._custom_words)
    
    def check_text(self, text: str) -> List[Tuple[int, int, str]]:
        """
        Check text and return list of misspelled words with positions.
        
        Args:
            text: The text to check
            
        Returns:
            List of (start_pos, end_pos, word) tuples for misspelled words
        """
        if not self.enabled or not text:
            return []
        
        misspelled = []
        
        # Find all words with their positions
        # This regex finds word boundaries properly
        word_pattern = re.compile(r'\b([a-zA-ZÀ-ÿ]+)\b', re.UNICODE)
        
        for match in word_pattern.finditer(text):
            word = match.group(1)
            if not self.check_word(word):
                start = match.start(1)
                end = match.end(1)
                misspelled.append((start, end, word))
        
        return misspelled
    
    def get_available_languages(self) -> List[str]:
        """Get list of available dictionary languages with variants (e.g., 'English (US)', 'English (GB)')"""
        available = []
        
        # Check user dictionaries - look in dictionaries folder AND subdirectories
        if self.dictionaries_path.exists():
            # Check root folder
            for dic_file in self.dictionaries_path.glob("*.dic"):
                lang_code = dic_file.stem  # e.g., "en_US", "en_GB"
                # Skip hyphenation dictionaries
                if lang_code.startswith('hyph_'):
                    continue
                display_name = self.CODE_TO_DISPLAY.get(lang_code, lang_code)
                if display_name not in available:
                    available.append(display_name)
            
            # Also check subdirectories (e.g., dictionaries/en/en_US.dic)
            for dic_file in self.dictionaries_path.glob("**/*.dic"):
                lang_code = dic_file.stem  # e.g., "en_US", "en_GB"
                # Skip hyphenation dictionaries
                if lang_code.startswith('hyph_'):
                    continue
                display_name = self.CODE_TO_DISPLAY.get(lang_code, lang_code)
                if display_name not in available:
                    available.append(display_name)
        
        # Check spylls bundled dictionaries
        if HAS_SPYLLS:
            try:
                import spylls.hunspell
                import glob
                spylls_path = os.path.dirname(spylls.hunspell.__file__)
                bundled_dics = glob.glob(os.path.join(spylls_path, 'data', '**', '*.dic'), recursive=True)
                for dic_path in bundled_dics:
                    lang_code = os.path.basename(dic_path).replace('.dic', '')
                    display_name = self.CODE_TO_DISPLAY.get(lang_code, lang_code)
                    if display_name not in available:
                        available.append(display_name)
            except Exception:
                pass
        
        # Add pyspellchecker languages if available (these don't have regional variants)
        if HAS_SPELLCHECKER:
            pyspell_langs = [
                ('en_US', 'English (US)'),  # pyspellchecker uses US English
                ('es_ES', 'Spanish (ES)'),
                ('de_DE', 'German (DE)'),
                ('fr_FR', 'French (FR)'),
                ('pt_PT', 'Portuguese (PT)'),
                ('nl_NL', 'Dutch (NL)'),
                ('it_IT', 'Italian'),
                ('ru_RU', 'Russian'),
            ]
            for code, name in pyspell_langs:
                if name not in available:
                    available.append(name)
        
        return sorted(available)
    
    def get_current_language(self) -> Optional[str]:
        """Get the current spellcheck language as display name (e.g., 'English (US)')"""
        if self._current_language:
            # First try the new variant-aware mapping
            display = self.CODE_TO_DISPLAY.get(self._current_language)
            if display:
                return display
            # Fall back to legacy mapping
            return self.CODE_TO_LANGUAGE.get(self._current_language, self._current_language)
        return None
    
    def clear_cache(self):
        """Clear the word check cache"""
        self._word_cache.clear()
    
    def is_available(self) -> bool:
        """Check if spellchecking is available"""
        return HAS_HUNSPELL or HAS_SPELLCHECKER
    
    def is_ready(self) -> bool:
        """Check if spellchecking is initialized and ready to use"""
        return self._hunspell is not None or self._spylls is not None or self._spellchecker is not None
    
    def get_backend_info(self) -> str:
        """Get information about the spellcheck backend"""
        if self._hunspell:
            return f"Hunspell ({self._current_language})"
        elif self._spylls:
            return f"Spylls/Hunspell ({self._current_language})"
        elif self._spellchecker:
            return f"pyspellchecker ({self._current_language})"
        elif HAS_HUNSPELL:
            return "Hunspell (not initialized - call set_language first)"
        elif HAS_SPYLLS:
            return "Spylls (not initialized - call set_language first)"
        elif HAS_SPELLCHECKER:
            return "pyspellchecker (not initialized - call set_language first)"
        else:
            return "No spellcheck backend available"
    
    def get_diagnostics(self) -> dict:
        """Get diagnostic information about the spellcheck system"""
        info = {
            'hunspell_available': HAS_HUNSPELL,
            'spylls_available': HAS_SPYLLS,
            'pyspellchecker_available': HAS_SPELLCHECKER,
            'pyspellchecker_import_error': SPELLCHECKER_IMPORT_ERROR,
            'hunspell_initialized': self._hunspell is not None,
            'spylls_initialized': self._spylls is not None,
            'pyspellchecker_initialized': self._spellchecker is not None,
            'current_language': self._current_language,
            'backend': self._backend,
            'enabled': self.enabled,
            'custom_words_count': len(self._custom_words),
            'ignored_words_count': len(self._ignored_words),
            'cache_size': len(self._word_cache),
            'dictionaries_path': str(self.dictionaries_path),
        }
        
        # Check if pyspellchecker word frequency data is available
        if self._spellchecker and hasattr(self._spellchecker, 'word_frequency'):
            # WordFrequency doesn't support len(), use alternative method
            try:
                # Try to get count via the keys() method if available
                wf = self._spellchecker.word_frequency
                if hasattr(wf, 'keys'):
                    info['pyspellchecker_word_count'] = len(list(wf.keys())[:1000])  # Sample size
                else:
                    info['pyspellchecker_word_count'] = "available"
            except:
                info['pyspellchecker_word_count'] = "available"
        
        return info


# Singleton instance
_spellcheck_manager: Optional[SpellcheckManager] = None


def get_spellcheck_manager(user_data_path: str = None) -> SpellcheckManager:
    """Get or create the global spellcheck manager instance"""
    global _spellcheck_manager
    if _spellcheck_manager is None:
        _spellcheck_manager = SpellcheckManager(user_data_path)
    return _spellcheck_manager
