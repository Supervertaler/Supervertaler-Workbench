"""   
Supervertaler
=============
The Ultimate Translation Workbench.
Modern PyQt6 interface with specialised modules to handle any problem.
Framework: PyQt6

This is the modern edition of Supervertaler using PyQt6 framework.
For the classic tkinter edition, see Supervertaler_tkinter.py

Key Features:
- Complete Translation Matching: Termbase + TM + MT + Multi-LLM
- Project Termbases: Dedicated terminology per project with automatic extraction
- Voice: hands-free voice commands (offline, Vosk)
- Superimage: Extract images from DOCX files with preview
- Google Cloud Translation API integration
- Multi-LLM Support: OpenAI GPT, Claude, Google Gemini
- 2-Layer Prompt Architecture (System + Custom Prompts) with AI Assistant
- AI Assistant with conversational interface for document analysis
- Superlookup with global hotkey (Ctrl+Alt+L)
- Detachable Log window for multi-monitor setups
- Modern theme system (6 themes + custom editor)
- memoQ bilingual DOCX import/export
- Bilingual Table export/import for review workflow
- SQLite-based translation memory with FTS5 search
- Professional TMX editor
- Spellcheck integration with Hunspell and custom dictionary support

Author: Michael Beijer
License: MIT
"""

# Version Information – read from pyproject.toml (single source of truth)
def _read_version():
    """Read version from pyproject.toml, importlib.metadata, or hardcoded fallback."""
    import os as _os
    # 1. Try pyproject.toml (works in dev mode and PyInstaller builds that bundle it)
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # Python < 3.11
        _toml_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "pyproject.toml")
        with open(_toml_path, "rb") as f:
            _data = tomllib.load(f)
        return _data["project"]["version"]
    except Exception:
        pass
    # 2. Try importlib.metadata (works for pip install)
    try:
        from importlib.metadata import version
        return version("supervertaler")
    except Exception:
        pass
    # 3. Last-resort hardcoded fallback
    return "1.10.313"

__version__ = _read_version()
__phase__ = "0.9"
__release_date__ = "2026-05-12"
__edition__ = "Qt"

# --- macOS Finder crash logging (v1.9.275) ---
# When launched via Finder, stdout/stderr go nowhere; write to Desktop log.
import sys as _sys
import os as _os
if getattr(_sys, 'frozen', False) and _sys.platform == 'darwin':
    _crash_log = _os.path.expanduser('~/Desktop/supervertaler_crash.log')
    try:
        with open(_crash_log, 'w') as _f:
            _f.write(f"Launch time: {__import__('datetime').datetime.now()}\n")
            _f.write(f"Version: {__version__}\n")
            _f.write(f"CWD: {_os.getcwd()}\n")
            _f.write(f"sys.executable: {_sys.executable}\n")
            _f.write(f"sys._MEIPASS: {getattr(_sys, '_MEIPASS', 'N/A')}\n")
            _f.write(f"__file__: {__file__}\n")
            _f.write(f"LANG: {_os.environ.get('LANG', 'NOT SET')}\n")
            _f.write(f"LC_CTYPE: {_os.environ.get('LC_CTYPE', 'NOT SET')}\n")
            _f.write("--- Starting app ---\n")
    except Exception:
        pass
del _sys, _os
# --- end crash logging ---

import sys
import json
import os
import platform
import subprocess
import atexit
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple, Callable
from dataclasses import dataclass, asdict, field
from datetime import datetime
from modules.shortcut_display import format_shortcut_for_display
from modules.platform_helpers import IS_WINDOWS, IS_MACOS, IS_LINUX, open_file, open_folder, get_hidden_subprocess_flags
from modules.ui_scale import scaled_pt


from modules.language_combos import _import_language_pairs, _select_lang_in_combo  # moved there, unchanged


from modules.model_combo import _select_saved_model  # moved there, unchanged


def get_resource_path(relative_path: str) -> Path:
    """Get absolute path to resource, works for dev and for PyInstaller bundled app."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # Running as compiled executable (PyInstaller)
        base_path = Path(sys._MEIPASS)
    else:
        # Running in development
        base_path = Path(__file__).parent
    return base_path / relative_path


from modules.user_data_location import (  # moved there, unchanged
    get_config_pointer_path, get_default_user_data_path, save_user_data_path,
    load_user_data_path_from_config, get_user_data_path, needs_first_run_data_dialog,
)

import threading
import time  # For delays in Superlookup
import re

# Fix encoding for Windows console (UTF-8 support)
# Only set if stdout/stderr exist (they're None in PyInstaller --windowed mode)
if sys.platform == 'win32':
    import io
    if sys.stdout is not None and hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    if sys.stderr is not None and hasattr(sys.stderr, 'buffer'):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# External dependencies
import pyperclip  # For clipboard operations in Superlookup
from modules.superlookup import SuperlookupEngine  # Superlookup engine
from modules.pseudo_translate_dialog import run_pseudo_translation  # Pseudo-translation export test (dialog + apply)
from modules.project_assets import write_project_file, read_backup, bundle_round_trip_sources, restore_round_trip_sources, is_in_source_dir, TM_SUBDIR  # Safe saves, round-trip sources, tm/ (issue #228)
from modules.project_assets import bundle_source, resolve_source_path, ensure_target_dir, nest_in_own_folder  # Project-folder model (issue #228)
from modules.voice_commands import VoiceCommandManager, VoiceCommand, ContinuousVoiceListener  # Voice commands (Talon-style)
from modules import inline_codes as _inline_codes  # User-defined inline codes / placeholders (issue #194)
from modules.voice_command_dialog import VoiceCommandEditDialog  # Voice command edit dialog
from modules.styled_widgets import CheckmarkCheckBox, TealCheckmarkCheckBox, CheckmarkRadioButton
from modules.statuses import (
    STATUSES,
    DEFAULT_STATUS,
    StatusDefinition,
    get_status,
    match_memoq_status,
    compose_memoq_status,
)
from modules import file_dialog_helper as fdh  # File dialog helper with last directory memory
from modules.spellcheck_manager import SpellcheckManager, get_spellcheck_manager  # Spellcheck with Hunspell
from modules.find_replace_qt import (
    FindReplaceHistory,
    FindReplaceOperation,
    FindReplaceSet,
    FindReplaceSetsManager,
    HistoryComboBox,
)  # F&R History and Sets
from modules.shortcut_manager import ShortcutManager  # Keyboard shortcut management
from modules.termlens_widget import TermLensWidget  # TermLens widget for glossary display
from modules.help_system import Topics as HelpTopics, install as install_help_system, set_topic as set_help_topic, open_help


STATUS_ORDER = [
    "not_started",
    "pretranslated",
    "draft",
    "confirmed",
    "proofread",
    "approved",
    "rejected",
]

STATUS_CYCLE = STATUS_ORDER

from modules.statuses import TRANSLATABLE_STATUSES  # moved there, unchanged

# Check for PyQt6 and offer to install if missing
try:
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QTableWidget, QTableWidgetItem, QTableWidgetSelectionRange, QHeaderView, QMenuBar, QMenu,
        QFileDialog, QMessageBox, QToolBar, QLabel, QComboBox,
        QPushButton, QSpinBox, QDoubleSpinBox, QSplitter, QTextEdit, QStatusBar,
        QStyledItemDelegate, QInputDialog, QDialog, QLineEdit, QRadioButton,
        QButtonGroup, QDialogButtonBox, QTabWidget, QGroupBox, QGridLayout, QCheckBox,
        QProgressBar, QProgressDialog, QFormLayout, QTabBar, QPlainTextEdit, QAbstractItemDelegate,
        QFrame, QListWidget, QListWidgetItem, QStackedWidget, QTreeWidget, QTreeWidgetItem,
        QScrollArea, QSizePolicy, QSlider, QToolButton, QAbstractItemView
    )
    from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal, pyqtSlot, QObject, QUrl, QThread, QRunnable, QThreadPool
    from PyQt6.QtGui import QFont, QAction, QKeySequence, QIcon, QTextOption, QColor, QDesktopServices, QTextCharFormat, QTextCursor, QBrush, QSyntaxHighlighter, QPalette, QTextBlockFormat, QCursor, QFontMetrics
    from PyQt6.QtWidgets import QStyleOptionViewItem, QStyle
    from PyQt6.QtCore import QRectF
    from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
    from PyQt6 import sip
except ImportError:
    print("PyQt6 not found. Installing...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "PyQt6"])
    print("✓ PyQt6 installed. Please restart the application.")
    sys.exit(0)


# ============================================================================
# PRIVATE DATA PROTECTION SYSTEM
# ============================================================================

# Check for .supervertaler.local file to enable private features (for developers only)
# This ensures that private data (API keys, personal projects, etc.) stays in user_data_private/
# which is .gitignored, preventing accidental upload to GitHub
ENABLE_PRIVATE_FEATURES = os.path.exists(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".supervertaler.local")
)
if ENABLE_PRIVATE_FEATURES:
    print("[DEV MODE] Private features enabled (.supervertaler.local found)")
    print("[DEV MODE] Using 'user_data_private/' folder (git-ignored)")
else:
    print("[USER MODE] Using 'user_data/' folder")


# ============================================================================
# GLOBAL AHK PROCESS CLEANUP
# ============================================================================

from modules.superlookup_tab import (  # moved out of this file, unchanged
    _ahk_process,
    _hotkey_manager,
    cleanup_hotkey_processes,
    _SearchTermHighlighter,
    _NumericTableWidgetItem,
    _TRANSPARENT_SORT_KEY,
    _ReadOnlyHtmlCell,
    _SuperLookupSearchSignals,
    _SuperLookupSearchWorker,
    SuperlookupTab,
)




# ============================================================================
# ============================================================================
# DATA MODELS
# ============================================================================

# ============================================================================
# INLINE FORMATTING TAG UTILITIES
# ============================================================================

from modules.tag_text import (  # moved out of this file, unchanged
    runs_to_tagged_text,
    strip_formatting_tags,
    strip_outer_wrapping_tags,
    get_docx_language_code,
    set_docx_language,
    has_formatting_tags,
    apply_formatting_tags,
    _PHRASE_TAG_RE,
    _PHRASE_MARKER_STYLE,
    has_phrase_tags,
    phrase_tags_to_html,
    _phrase_tag_names,
    wysiwyg_runs_to_phrase_text,
    get_formatted_html_display,
    tagged_text_to_runs,
    extract_memoq_tags,
    extract_html_tags,
    compact_tags,
    WORD_JOINER,
    _TAG_LINEBREAK_PROTECT_RE,
    protect_tags_from_linebreak,
    expand_compact_tags,
    _ALL_TAGS_PATTERN,
    extract_all_tags,
    _AUTOTAG_TAG_PATTERN,
    autotag_extract_tags,
    strip_all_tags,
    _normalize_ws_for_compare,
    _WORD_COMPARE_FOLD,
    _normalize_for_word_compare,
    _reinsert_tags_into_target,
    _classify_tag,
    _tags_well_formed,
    validate_tag_transfer,
    place_tags_via_llm,
    count_pipe_symbols,
    get_next_pipe_count_needed,
    get_tag_pair,
    find_next_unused_tag,
    get_wrapping_tag_pair,
    get_html_wrapping_tag_pair,
    strip_invisible_markers,
)




























# ============================================================================
# MEMOQ TAG UTILITIES (for tag insertion shortcuts)
# ============================================================================






















































from modules.segment_model import (  # moved out of this file, unchanged
    Comment,
    Segment,
    Project,
)


# Okapi file-type import options (shared with tools/filter_compare.py)
from modules.okapi_import_options import (  # noqa: E402
    DEFAULT_IMPORT_OPTIONS, okapi_sidecar_options, okapi_subdoc_category,
)


from modules.okapi_import_options import IMPORT_OPTION_GROUPS  # moved there, unchanged






# ============================================================================
# CUSTOM DELEGATES AND EDITORS
# ============================================================================


from modules.grid_editors import (  # moved out of this file, unchanged
    _cleaned_modifiers,
    _QuitEventFilter,
    _CtrlReturnEventFilter,
    _GridArrowKeyEventFilter,
    _WheelGuard,
    _LoneCtrlEventFilter,
    GridTableEventFilter,
    _anchored_comments_at,
    _segment_comments_for_row,
    _format_comment_tooltip,
    _add_open_comment_actions,
    GridTextEditor,
    _WORD_INTERNAL_PUNCT,
    _is_edge_trim_char,
    _is_cjk_char,
    _contains_cjk,
    _canon_path,
    select_word_under_cursor,
    select_whole_segment,
    is_triple_click,
    _GridStyleBatch,
    ReadOnlyGridTextEditor,
    TagHighlighter,
    EditableGridTextEditor,
    SearchHighlightDelegate,
    ClickableHighlightLabel,
    TermbaseHighlightWidget,
    WordWrapDelegate,
)
























































# ============================================================================
# THEME EDITOR DIALOG
# ============================================================================

from modules.main_dialogs import (  # moved out of this file, unchanged
    ThemeEditorDialog,
    DetachedLogWindow,
    AdvancedFiltersDialog,
    ScratchpadDialog,
    LiveProgressDialog,
    _ImportProgressDialog,
)


# ============================================================================
# DETACHED LOG WINDOW
# ============================================================================



# ============================================================================
# TERM METADATA DIALOG (legacy alias)
# ============================================================================
#
# The historical ``TermMetadataDialog`` class has been merged into the unified
# :class:`TermbaseEntryEditor` (``modules/termbase_entry_editor.py``), which now
# handles both the add-term path (this alias) and the edit-term path used
# elsewhere in the app. The alias below keeps the old name working for any
# stale references, internal or external.
from modules.termbase_entry_editor import TermbaseEntryEditor as _TermbaseEntryEditor
TermMetadataDialog = _TermbaseEntryEditor






# ============================================================================
# BATCH TRANSLATION WORKER & PROGRESS DIALOG
# ============================================================================

from modules.translation_workers import (  # moved out of this file, unchanged
    TMSearchWorker,
    PreTranslationWorker,
    ProofreadWorker,
    GlossaryExtractionWorker,
)








# ============================================================================
# IMPORT PROGRESS DIALOG (shared helper)
# ============================================================================
# v1.10.150: centralises the boilerplate every import_* method used to copy
# around a QProgressDialog. Historically each path reinvented this, with
# inconsistent results — three were correct, four created a dialog that
# closed before the slow grid-load phase (so the user saw "Not Responding"
# for ~30 s), and eleven had no dialog at all. This wrapper makes correct
# behaviour the default: the dialog spans the entire pipeline, every phase
# pumps the Qt event loop so the OS never marks the app unresponsive, and
# the helper exposes callbacks shaped for both the parser-style protocol
# (stage/current/total/message → continue_bool) and load_segments_to_grid
# (rows_done, total).
# ============================================================================



# ============================================================================
# AI GLOSSARY EXTRACTION WORKER
# ============================================================================



# ============================================================================
# MAIN WINDOW
# ============================================================================

from modules.main_window.termbases import TermbasesMixin  # moved out of this class, unchanged


from modules.main_window.translation_memories import TranslationMemoriesMixin  # moved out of this class, unchanged


from modules.main_window.ai_translation import AITranslationMixin  # moved out of this class, unchanged


from modules.main_window.projects import ProjectsMixin  # moved out of this class, unchanged


from modules.main_window.settings import SettingsMixin  # moved out of this class, unchanged


from modules.main_window.docx_export import DocxExportMixin  # moved out of this class, unchanged


from modules.main_window.okapi import OkapiMixin  # moved out of this class, unchanged


from modules.main_window.document_import import DocumentImportMixin  # moved out of this class, unchanged


from modules.main_window.plain_text import PlainTextMixin  # moved out of this class, unchanged


from modules.main_window.review_table import ReviewTableMixin  # moved out of this class, unchanged


from modules.main_window.multifile import MultifileMixin  # moved out of this class, unchanged


from modules.main_window.memoq import MemoQMixin  # moved out of this class, unchanged


from modules.main_window.trados import TradosMixin  # moved out of this class, unchanged


from modules.main_window.phrase import PhraseMixin  # moved out of this class, unchanged


from modules.main_window.cafetran import CafeTranMixin  # moved out of this class, unchanged


from modules.main_window.dejavu import DejaVuMixin  # moved out of this class, unchanged


from modules.main_window.po import PoMixin  # moved out of this class, unchanged


from modules.main_window.import_export_common import ImportExportCommonMixin  # moved out of this class, unchanged


from modules.main_window.bilingual_text import BilingualTextMixin  # moved out of this class, unchanged


from modules.main_window.segment_editing import SegmentEditingMixin  # moved out of this class, unchanged


from modules.main_window.grid_view import GridViewMixin  # moved out of this class, unchanged


from modules.main_window.grid_navigation import GridNavigationMixin  # moved out of this class, unchanged


from modules.main_window.find_replace import FindReplaceMixin  # moved out of this class, unchanged


from modules.main_window.filters import FiltersMixin  # moved out of this class, unchanged


from modules.main_window.comments import CommentsMixin  # moved out of this class, unchanged


from modules.main_window.match_panel import MatchPanelMixin  # moved out of this class, unchanged


from modules.main_window.preview import PreviewMixin  # moved out of this class, unchanged


class SupervertalerQt(PreviewMixin, MatchPanelMixin, CommentsMixin, FiltersMixin, FindReplaceMixin, GridNavigationMixin, GridViewMixin, SegmentEditingMixin, BilingualTextMixin, ImportExportCommonMixin, PoMixin, DejaVuMixin, CafeTranMixin, PhraseMixin, TradosMixin, MemoQMixin, MultifileMixin, ReviewTableMixin, PlainTextMixin, DocumentImportMixin, OkapiMixin, DocxExportMixin, SettingsMixin, ProjectsMixin, AITranslationMixin, TranslationMemoriesMixin, TermbasesMixin, QMainWindow):
    """Main application window"""
    
    MAX_RECENT_PROJECTS = 10  # Maximum number of recent projects to track
    
    # Signal for thread-safe logging (background threads emit, main thread handles)
    _log_signal = pyqtSignal(str)
    
    # Signal for proactive highlighting - prefetch worker emits, main thread applies highlighting
    # Args: segment_id (int), termbase_matches (dict as JSON string for thread safety)
    _proactive_highlight_signal = pyqtSignal(int, str)
    
    def __init__(self):
        super().__init__()
        
        # Connect thread-safe log signal (must be done first for logging to work from threads)
        self._log_signal.connect(self._log_to_ui)
        
        # Connect proactive highlighting signal (prefetch worker emits, main thread highlights)
        self._proactive_highlight_signal.connect(self._apply_proactive_highlighting)
        
        # Application state
        self.current_project: Optional[Project] = None
        self.project_file_path: Optional[str] = None
        self.project_modified = False
        
        # memoQ bilingual DOCX import tracking
        self.memoq_source_file = None
        
        # UI Configuration
        self.default_font_family = "Calibri"
        self.default_font_size = 11
        
        # Application settings
        self.allow_replace_in_source = False  # Safety: don't allow replace in source by default
        # Consolidated auto-fill setting (replaces the old auto_propagate_exact_matches
        # + auto_insert_100_percent_matches pair). When you select an empty segment
        # that has a 100% TM match, its translation is filled in automatically.
        self.auto_fill_100_matches = True  # Auto-fill empty segments with 100% TM matches
        self.auto_fill_confirm = False  # Mark auto-filled segments as confirmed (else draft)
        # Old attrs kept for back-compat with any lingering references; the live
        # code paths now read self.auto_fill_100_matches.
        self.auto_propagate_exact_matches = True
        self.auto_insert_100_percent_matches = True
        self.auto_confirm_100_percent_matches = False  # Auto-confirm 100% matches when navigating with Ctrl+Enter
        self.auto_confirm_overwrite_existing = False  # Allow auto-confirm to overwrite existing target content
        # Auto-propagate confirmed translations to identical (same-source) segments
        # — the memoQ/Trados-style propagate-on-confirm behaviour.
        self.auto_propagate_on_confirm = True  # Propagate confirmed target to identical segments
        self.auto_propagate_confirm = False  # Mark propagated segments as confirmed (else draft)
        self.auto_propagate_overwrite = False  # Overwrite existing translations when propagating
        self.tm_save_mode = 'latest'  # 'all' = keep all translations with timestamps, 'latest' = only keep most recent (DEFAULT)
        
        # Tab position setting
        self.tabs_above_grid = False  # Whether to show TermLens/Session Log tabs above grid
        
        # Right panel visibility settings
        self.show_translation_results_pane = False  # Show Translation Results tab (hidden by default for new users)
        # Note: Match Panel is always visible (no toggle needed)
        
        # TM and Termbase matching are always on. (The old global on/off
        # toggle was removed — to skip matching, simply don't activate any
        # TMs/termbases.) These flags stay True; the internal match-logic
        # guards that read them are now effectively always-true.
        self.enable_tm_matching = True
        self.enable_termbase_matching = True
        self.enable_mt_matching = True  # Machine Translation enabled
        self.enable_llm_matching = False  # LLM Translation enabled (DISABLED by default - too slow for navigation!)
        self.enable_termbase_grid_highlighting = True  # Highlight termbase matches in source cells

        # Termbase display settings
        self.termbase_hide_shorter_matches = False  # Hide shorter terms included in longer ones

        # Invisible character display settings
        self.showing_invisible_spaces = False  # Track whether spaces are shown as middle dots
        self.invisible_char_color = '#999999'  # Light gray color for invisible characters
        self.invisible_display_settings = {
            'spaces': False,
            'tabs': True,
            'nbsp': True,
            'linebreaks': True
        }
        # Seed the highlighter's NBSP-shading flag from the startup settings so real
        # non-breaking spaces are shaded as soon as the first grid renders.
        TagHighlighter._show_nbsp = self.invisible_display_settings.get('nbsp', False)

        # Grid row color settings (memoQ-style alternating row colors)
        self.enable_alternating_row_colors = True  # Enable alternating row colors by default
        self.even_row_color = '#FFFFFF'  # White for even rows
        self.odd_row_color = '#F0F0F0'  # Light gray for odd rows

        # Hide outer wrapping tags in grid display (e.g. <li-o>...</li-o>)
        self.hide_outer_wrapping_tags = False  # Disabled by default

        # Termbase highlight style settings
        self.termbase_highlight_style = 'semibold'  # 'background', 'dotted', or 'semibold'
        self.termbase_dotted_color = '#808080'  # Medium gray for dotted underline (more visible)
        
        # Focus border settings for target cells
        self.focus_border_color = '#f1b79a'  # Peach/salmon
        self.focus_border_thickness = 2  # 2px
        
        # Sound effects settings
        self.enable_sound_effects = False  # Sound effects disabled by default

        # Debug mode settings (for troubleshooting performance issues)
        self.debug_mode_enabled = False  # Enables verbose debug logging
        self.debug_auto_export = False  # Auto-export debug logs to file
        self.debug_log_buffer = []  # Buffer for debug logs (for export)
        
        # Precision scroll settings (for fine-tuned grid navigation)
        self.precision_scroll_divisor = 3  # Divide row height by this (higher = finer increments)
        self.auto_center_active_segment = True  # Auto-scroll to keep active segment centered (default ON)
        
        # Translation service availability flags (would be set from config/API keys)
        self.google_translate_enabled = True  # For demo purposes
        self.deepl_enabled = False  # Not implemented yet
        self.openai_enabled = True  # For demo purposes
        self.claude_enabled = True  # For demo purposes
        
        # Timer for delayed lookup (cancel if user moves to another segment)
        self.lookup_timer = None
        self.current_lookup_segment_id = None
        
        # Termbase cache for performance optimization
        # Maps segment ID → {term: translation} dictionary
        self.termbase_cache = {}
        self.termbase_cache_lock = threading.Lock()  # Thread-safe cache access
        self.termbase_batch_worker_thread = None  # Background worker thread
        self.termbase_batch_stop_event = threading.Event()  # Signal to stop background worker

        # In-memory termbase index for instant lookups (v1.9.182)
        # Loaded once on project load, contains ALL terms from activated termbases
        # Structure: list of term dicts with pre-compiled regex patterns
        self.termbase_index = []
        self.termbase_index_lock = threading.Lock()
        # v1.10.153: distinguishes "no terms" from "not yet built". Without
        # this flag, find_termbase_matches_in_source treats an empty index
        # as "fall back to the per-word SQL query path" — fine when the
        # index genuinely hasn't been built, disastrous (2-34 s per cell
        # click) when every termbase is intentionally deactivated for the
        # current project. Set to True at the end of _build_termbase_index
        # so a deliberate empty index returns {} instantly.
        self._termbase_index_built = False
        
        # TM/MT/LLM prefetch cache for instant segment switching (like memoQ)
        # Maps segment ID → {"TM": [...], "MT": [...], "LLM": [...]}
        self.translation_matches_cache = {}
        self.translation_matches_cache_lock = threading.Lock()
        self.prefetch_worker_thread = None
        self.prefetch_stop_event = threading.Event()
        self.prefetch_queue = []  # List of segment IDs to prefetch
        
        # Idle prefetch: prefetch next segments while user is thinking/typing
        self.idle_prefetch_timer = None  # QTimer for triggering prefetch after typing pause
        self.idle_prefetch_delay_ms = 1500  # Start prefetch 1.5s after user stops typing
        
        # Cache kill switch for performance testing
        # When True, all caches are bypassed - direct lookups every time
        self.disable_all_caches = False  # v1.9.183: Default to False (caches ENABLED)
        
        # Undo/Redo stack for grid edits
        self.undo_stack = []  # List of (segment_id, old_target, new_target, old_status, new_status)
        self.redo_stack = []  # List of undone actions that can be redone
        self.max_undo_levels = 100  # Maximum number of undo levels to keep
        
        # Global language settings (defaults)
        self.source_language = "English"
        self.target_language = "Dutch"

        # Target editor signal suppression (prevents load-time churn)
        self._suppress_target_change_handlers = False
        # v1.10.229: Same idea for source editors — set True when we're
        # programmatically rewriting source cell contents so the
        # "Replace in Source Text" handler doesn't fire a spurious save.
        self._suppress_source_change_handlers = False
        # v1.10.230: AutoCorrect-while-typing engine (issue #213).
        # The engine itself is stateless; per-user enable/disable + per-rule
        # overrides live in the unified settings JSON and are loaded by
        # _load_autocorrect_settings() during settings hydration below.
        from modules.autocorrect import AutoCorrectEngine
        self._autocorrect_engine = AutoCorrectEngine()
        self.autocorrect_enabled: bool = True
        self.autocorrect_rule_overrides: Dict[str, bool] = {}
        self.warning_banners: Dict[str, QWidget] = {}
        
        # Superlookup detached window
        self.lookup_detached_window = None
        
        # ============================================================================
        # USER DATA PATH INITIALIZATION
        # ============================================================================
        # ALL users (EXE, pip, dev) now use a unified system:
        # - Default location: ~/Supervertaler/ (visible in home folder)
        # - User can choose custom location on first run
        # - Config pointer stores their choice at standard config location
        # - Auto-recovery if pointer is deleted but data exists at default location
        from modules.database_manager import DatabaseManager
        
        # Check if this is first run BEFORE getting the path
        # (so we know whether to show the data location dialog later)
        self._needs_data_location_dialog = needs_first_run_data_dialog()

        # Resolve the user data path via the unified pointer system for ALL
        # users (dev, pip, EXE). The dev branch used to hardcode this to
        # ~/Supervertaler and skip the pointer entirely, which made
        # Settings -> Change... silently revert on restart in dev mode
        # (the change saved to the pointer but startup never read it).
        # The unified path also auto-recovers if the default location has
        # content but no pointer exists, so a fresh dev install still does
        # the right thing without a special case.
        self.user_data_path = get_user_data_path()

        # Configure the persistent token-usage ledger (metadata only; on by
        # default, matching the Trados plugin). Writes to
        # <user_data>/workbench/usage/usage-YYYY-MM.jsonl.
        self.monthly_budget_usd = 0.0
        try:
            from modules import usage_log
            _enabled = True
            try:
                _gp = self.load_general_settings()
                _enabled = bool(_gp.get('persist_usage_log', True))
                self.monthly_budget_usd = float(_gp.get('monthly_budget_usd', 0.0) or 0.0)
            except Exception:
                pass
            usage_log.configure(self.user_data_path, __version__, enabled=_enabled)
            # Calls made outside a batch (Ctrl+T, chat, AutoTagger) still count
            # towards the open project in the per-project cost report (#8).
            usage_log.set_default_context(self._usage_log_default_context)
        except Exception:
            pass

        # Ensure user_data directory exists (creates empty folder if missing)
        # BUT only if we're not going to show the dialog (which will create the chosen folder)
        if not self._needs_data_location_dialog:
            self.user_data_path.mkdir(parents=True, exist_ok=True)
        
        print(f"[Data Paths] User data: {self.user_data_path}")

        # Migrate old settings files to unified settings/settings.json (one-time)
        if not self._needs_data_location_dialog:
            self._migrate_settings_to_unified()
            self._migrate_to_workbench_layout()
            self._prepare_api_keys()

        # Load the saved source/target language pair NOW, before any UI is
        # built. The Language Pair combo boxes read self.source_language /
        # self.target_language when they're created; if the load happens
        # later, the combos show the hard-coded defaults and the next
        # dropdown change will save the stale UI values back to disk.
        # Spellcheck initialization stays in load_language_settings(),
        # which runs later after spellcheck_manager and log are ready.
        if not self._needs_data_location_dialog:
            self._load_language_pair_from_disk()

        # Database Manager for Termbases
        self.db_manager = DatabaseManager(
            db_path=str(self.user_data_path / "resources" / "supervertaler.db"),
            log_callback=self.log
        )
        # Only connect if we're not showing the dialog (which will create the folder)
        # If dialog is needed, we'll connect after user chooses location
        if not self._needs_data_location_dialog:
            self.db_manager.connect()
        
        # TM Database - Initialize early so Superlookup works without a project loaded
        from modules.translation_memory import TMDatabase
        self.tm_database = TMDatabase(
            source_lang=None,  # Will be set when project is loaded
            target_lang=None,  # Will be set when project is loaded
            db_path=str(self.user_data_path / "resources" / "supervertaler.db"),
            log_callback=self.log
        )
        
        # TM Metadata Manager - needed for TM list in Superlookup
        from modules.tm_metadata_manager import TMMetadataManager
        self.tm_metadata_mgr = TMMetadataManager(self.db_manager, self.log)

        # External TM live-sync timer. Attached Trados .sdltm files (and any
        # future external TM sources) are kept in step with their source by
        # checking the file mtime every few seconds and pulling any new or
        # modified TUs via incremental delta sync. The mtime check itself
        # is essentially free; the SQL work only runs on actual changes.
        from PyQt6.QtCore import QTimer
        self._external_tm_sync_timer = QTimer(self)
        self._external_tm_sync_timer.setInterval(5000)  # 5s — feels live without being spammy
        self._external_tm_sync_timer.timeout.connect(self._sync_external_tms)
        self._external_tm_sync_timer.start()

        # Termbase Manager - needed for glossary AI injection
        from modules.termbase_manager import TermbaseManager
        self.termbase_mgr = TermbaseManager(self.db_manager, self.log)

        # Spellcheck Manager for target language spell checking
        self.spellcheck_manager = get_spellcheck_manager(str(self.user_data_path))
        # Note: spellcheck_enabled will be loaded from preferences later in _load_spellcheck_settings()
        # For now set to False, it gets updated when UI is created
        self.spellcheck_enabled = False
        # Set up the shared spellcheck manager for TagHighlighter instances
        TagHighlighter.set_spellcheck_manager(self.spellcheck_manager)
        TagHighlighter.set_spellcheck_enabled(self.spellcheck_enabled)
        
        # Find & Replace History Manager
        self.fr_history = FindReplaceHistory(str(self.user_data_path))
        
        # Shortcut Manager for keyboard shortcuts (including enable/disable)
        self.shortcut_manager = ShortcutManager(Path(self.user_data_path) / "workbench" / "settings" / "shortcuts.json")
        
        # Voice Command Manager for Talon-style voice commands
        self.voice_command_manager = VoiceCommandManager(self.user_data_path, main_window=self)
        
        # Continuous Voice Listener (always-on mode) - initialized on demand
        self.voice_listener = None  # Will be ContinuousVoiceListener when enabled
        
        # Figure Context Manager for multimodal AI translation
        from modules.figure_context_manager import FigureContextManager
        self.figure_context = FigureContextManager(self)
        
        # Theme Manager
        from modules.theme_manager import ThemeManager
        self.theme_manager = None  # Will be initialized after UI setup
        
        self.recent_projects_file = self.user_data_path / "workbench" / "settings" / "recent_projects.json"

        # Apply the theme's application stylesheet BEFORE building the UI, so
        # each widget is styled once as it is created. Applied afterwards it
        # re-styled every widget in the window – three seconds of a cold start
        # (issue #203). self.theme_manager stays None during init_ui, as before.
        theme_manager = ThemeManager(self.user_data_path)
        # Apply saved global UI font scale
        theme_manager.font_scale = self._get_global_ui_font_scale()
        theme_manager.apply_theme(QApplication.instance())

        # Initialize UI
        self.init_ui()
        
        self.theme_manager = theme_manager

        # Dark themes: rewrite the fixed light colours in widgets' own
        # stylesheets – now, and for dialogs and lazily built tabs as they
        # appear (issue #78). No effect in light themes.
        try:
            from modules.dark_style_adapter import DarkStyleAdapter
            self._dark_style_adapter = DarkStyleAdapter(
                lambda: self.theme_manager.current_theme if getattr(self, 'theme_manager', None) else None,
                self)
            QApplication.instance().installEventFilter(self._dark_style_adapter)
            self._dark_style_adapter.refresh_all()
        except Exception as e:
            print(f"[Theme] dark style adapter unavailable: {e}")
        
        # Update widgets that were created before theme_manager existed
        if hasattr(self, 'termlens_widget') and self.termlens_widget:
            self.termlens_widget.theme_manager = self.theme_manager
            if hasattr(self.termlens_widget, 'apply_theme'):
                self.termlens_widget.apply_theme()

        # Also update the Match Panel TermLens (right panel)
        if hasattr(self, 'termlens_widget_match') and self.termlens_widget_match:
            self.termlens_widget_match.theme_manager = self.theme_manager
            if hasattr(self.termlens_widget_match, 'apply_theme'):
                self.termlens_widget_match.apply_theme()

        if hasattr(self, 'translation_results_panel') and self.translation_results_panel:
            self.translation_results_panel.theme_manager = self.theme_manager
            # Also update class-level theme_manager for CompactMatchItem
            from modules.translation_results_panel import CompactMatchItem
            CompactMatchItem.theme_manager = self.theme_manager
            if hasattr(self.translation_results_panel, 'apply_theme'):
                self.translation_results_panel.apply_theme()
        
        # Schedule theme refresh after UI is fully initialized
        # This ensures all widgets are properly themed at startup
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(100, self.refresh_theme_colors)
        
        self.log(f"Welcome to Supervertaler Workbench v{__version__}")
        self.log("Supervertaler: The Ultimate Translation Workbench.")

        # Bring up the Voice tray icon now (in its inactive/grey
        # state) so the system tray slot is allocated up-front. Allocating
        # it on first Always-On activation would cause a one-time bounce
        # of neighbouring tray icons; doing it at startup folds that into
        # normal app launch instead.
        self._ensure_alwayson_tray_icon()
        # Note: the Workbench tray icon itself is set up later by
        # _setup_tray_icon(), called from main() after construction.

        # Load general settings (including auto-propagation)
        self.load_general_settings()
        
        # Load language settings
        self.load_language_settings()
        
        # Restore last project if enabled in settings
        self.restore_last_project_if_enabled()
        
        # Auto-open log window if enabled in settings
        general_settings = self.load_general_settings()
        if general_settings.get('auto_open_log', False):
            # Use QTimer to open log window after UI fully initializes
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(500, self.detach_log_window)  # 500ms delay to ensure UI is ready

        # Usage statistics – opt-in dialog (once) and anonymous ping
        self._init_usage_statistics()

        # Initialize auto backup timer
        self.auto_backup_timer = None
        self.last_backup_time = None
        self.restart_auto_backup_timer()

        # Apply proxy env vars for Gemini before any API calls are made
        self._apply_gemini_proxy()

        # Initialize Okapi sidecar (local file-processing service)
        self.okapi_sidecar = None
        self._init_okapi_sidecar_deferred()

        # Load font sizes from preferences (after UI is fully initialized)
        QApplication.instance().processEvents()  # Allow UI to finish initializing
        self.load_font_sizes_from_preferences()
        
        # Restore under-grid dock (TermLens / QuickTrans) visibility, position
        # and which tab was active.
        if hasattr(self, 'bottom_tabs'):
            self._restoring_dock = True
            try:
                termlens_visible = general_settings.get('termlens_under_grid_visible', False)
                above = general_settings.get('tabs_above_grid', False)
                self.tabs_above_grid = above
                if termlens_visible:
                    self.set_termlens_position('above' if above else 'below', save=False)
                else:
                    self.set_termlens_position(None, save=False)
                # Restore the active tab choice.
                active_tab = general_settings.get('bottom_dock_active_tab', 'termlens')
                if active_tab == 'quicktrans' and getattr(self, 'quicktrans_panel', None) is not None:
                    self.bottom_tabs.setCurrentWidget(self.quicktrans_panel)
                self._sync_bottom_dock_menu()
                # Restore the Match Panel top tab (TermLens / QuickTrans).
                match_tab = general_settings.get('match_top_active_tab', 'termlens')
                if (match_tab == 'quicktrans' and hasattr(self, 'match_top_tabs')
                        and getattr(self, 'quicktrans_panel_match', None) is not None):
                    self.match_top_tabs.setCurrentWidget(self.quicktrans_panel_match)
            finally:
                self._restoring_dock = False

        # First-run check - show unified setup wizard
        if self._needs_data_location_dialog or not general_settings.get('first_run_completed', False):
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(300, lambda: self._show_setup_wizard(is_first_run=True))
    
    def _show_data_location_dialog(self):
        """Show dialog to let user choose their data folder location on first run."""
        try:
            from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                                         QPushButton, QLineEdit, QFileDialog, QDialogButtonBox)
            
            dialog = QDialog(self)
            dialog.setWindowTitle(self.tr("Choose Data Folder Location"))
            dialog.setMinimumWidth(550)
            dialog.setModal(True)
            
            layout = QVBoxLayout(dialog)
            layout.setSpacing(15)
            
            # Title
            title_label = QLabel(self.tr("<h2>📁 Choose Your Data Folder</h2>"))
            layout.addWidget(title_label)
            
            # Explanation
            msg_label = QLabel(
                "Supervertaler stores your data in a folder of your choice, which contains things like:<br><br>"
                "• API keys<br>"
                "• Translation memories<br>"
                "• Termbases<br>"
                "• Prompts<br>"
                "• Settings<br><br>"
                "Choose a location that's easy to find and backup.<br>"
                "You can change this later in Settings → General."
            )
            msg_label.setWordWrap(True)
            layout.addWidget(msg_label)
            
            # Path input with browse button
            path_layout = QHBoxLayout()
            
            path_edit = QLineEdit()
            default_path = get_default_user_data_path()
            path_edit.setText(str(default_path))
            path_edit.setMinimumWidth(350)
            path_layout.addWidget(path_edit)
            
            browse_btn = QPushButton(self.tr("Browse..."))
            def browse_folder():
                folder = QFileDialog.getExistingDirectory(
                    dialog, 
                    "Choose Data Folder",
                    str(Path.home())
                )
                if folder:
                    # Append "Supervertaler" if user didn't include it
                    folder_path = Path(folder)
                    if folder_path.name != "Supervertaler":
                        folder_path = folder_path / "Supervertaler"
                    path_edit.setText(str(folder_path))
            
            browse_btn.clicked.connect(browse_folder)
            path_layout.addWidget(browse_btn)
            
            layout.addLayout(path_layout)
            
            # Tip
            tip_label = QLabel(
                "💡 <b>Tip:</b> The default location is in your home folder, "
                "making it easy to find and backup."
            )
            tip_label.setWordWrap(True)
            tip_label.setStyleSheet("color: #666;")
            layout.addWidget(tip_label)
            
            # Buttons
            button_layout = QHBoxLayout()
            
            default_btn = QPushButton(self.tr("Use Default"))
            default_btn.clicked.connect(lambda: path_edit.setText(str(default_path)))
            button_layout.addWidget(default_btn)
            
            button_layout.addStretch()
            
            ok_btn = QPushButton(self.tr("OK"))
            ok_btn.setDefault(True)
            ok_btn.clicked.connect(dialog.accept)
            button_layout.addWidget(ok_btn)
            
            layout.addLayout(button_layout)
            
            # Show dialog
            if dialog.exec() == QDialog.DialogCode.Accepted:
                chosen_path = Path(path_edit.text())
                
                # Create the folder
                chosen_path.mkdir(parents=True, exist_ok=True)
                
                # Save the choice to config pointer
                save_user_data_path(chosen_path)
                
                # Update our path if different from what we initialized with
                if chosen_path != self.user_data_path:
                    self.user_data_path = chosen_path
                    # Re-initialize managers with new path
                    self._reinitialize_with_new_data_path()
                else:
                    # Same path, but we still need to connect database (it was deferred)
                    if hasattr(self, 'db_manager') and self.db_manager and not self.db_manager.connection:
                        self.db_manager.connect()
                
                self.log(f"📁 Data folder set to: {chosen_path}")
                
                # Now show the features welcome dialog
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(300, self._show_first_run_welcome)
            else:
                # User cancelled - use default anyway
                default_path.mkdir(parents=True, exist_ok=True)
                save_user_data_path(default_path)
                self.log(f"📁 Using default data folder: {default_path}")
                
        except Exception as e:
            self.log(f"⚠️ Data location dialog error: {e}")
            import traceback
            traceback.print_exc()
    
    def _reinitialize_with_new_data_path(self):
        """Re-initialize managers after user changes data path."""
        try:
            # Migrate settings if needed for the new data path
            self._migrate_settings_to_unified()
            self._migrate_to_workbench_layout()

            # Close existing database connection
            if hasattr(self, 'db_manager') and self.db_manager:
                self.db_manager.close()
            
            # Re-initialize database manager
            from modules.database_manager import DatabaseManager
            self.db_manager = DatabaseManager(
                db_path=str(self.user_data_path / "resources" / "supervertaler.db"),
                log_callback=self.log
            )
            self.db_manager.connect()
            
            # Re-initialize TM database
            from modules.translation_memory import TMDatabase
            self.tm_database = TMDatabase(
                source_lang=None,
                target_lang=None,
                db_path=str(self.user_data_path / "resources" / "supervertaler.db"),
                log_callback=self.log
            )
            
            # Re-initialize TM metadata manager
            from modules.tm_metadata_manager import TMMetadataManager
            self.tm_metadata_mgr = TMMetadataManager(self.db_manager, self.log)
            
            # Update other managers
            self.spellcheck_manager = get_spellcheck_manager(str(self.user_data_path))
            self.fr_history = FindReplaceHistory(str(self.user_data_path))
            self.shortcut_manager = ShortcutManager(Path(self.user_data_path) / "workbench" / "settings" / "shortcuts.json")
            self.voice_command_manager = VoiceCommandManager(self.user_data_path, main_window=self)
            
            # Update theme manager
            from modules.theme_manager import ThemeManager
            self.theme_manager = ThemeManager(self.user_data_path)
            # Apply saved global UI font scale
            saved_font_scale = self._get_global_ui_font_scale()
            self.theme_manager.font_scale = saved_font_scale
            self.theme_manager.apply_theme(QApplication.instance())
            
            # Update recent projects file path
            self.recent_projects_file = self.user_data_path / "workbench" / "settings" / "recent_projects.json"
            
            self.log(f"✅ Re-initialized all managers with new data path")
            
        except Exception as e:
            self.log(f"⚠️ Error re-initializing managers: {e}")
            import traceback
            traceback.print_exc()
    
    def _show_first_run_welcome(self):
        """No-op since v1.9.474.

        This used to pop up an intro dialog about Supervertaler's modular
        feature architecture and route the user to Settings → Features.
        Both the modular-extras system and the Features tab were retired
        when every feature module became a core dependency. The setup
        wizard handles the data-folder confirmation; nothing else needs
        to nag the user on first run. Kept as a stub so any pending
        QTimer.singleShot() callers don't blow up.
        """
        try:
            settings = self.load_general_settings()
            settings['first_run_completed'] = True
            self.save_general_settings(settings)
        except Exception:
            pass

    def _show_setup_wizard(self, is_first_run: bool = False):
        """
        Show unified setup wizard that combines data folder selection and features intro.

        Args:
            is_first_run: If True, this is an automatic first-run trigger. If False, user
                          manually invoked from menu (skip data folder if already configured).
        """
        try:
            from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                         QPushButton, QLineEdit, QFileDialog, QStackedWidget,
                                         QWidget, QFrame, QCheckBox)
            from PyQt6.QtCore import Qt

            dialog = QDialog(self)
            dialog.setWindowTitle(self.tr("Supervertaler Workbench Setup Wizard"))
            dialog.setMinimumWidth(600)
            dialog.setMinimumHeight(450)
            dialog.setModal(True)

            main_layout = QVBoxLayout(dialog)
            main_layout.setSpacing(15)
            main_layout.setContentsMargins(20, 20, 20, 20)

            # Stacked widget for wizard pages
            stacked = QStackedWidget()

            # Determine if we need to show data folder page
            show_data_folder_page = is_first_run and self._needs_data_location_dialog

            # ==================== PAGE 1: Data Folder Selection ====================
            page1 = QWidget()
            page1_layout = QVBoxLayout(page1)
            page1_layout.setSpacing(15)

            # Step indicator
            step1_indicator = QLabel(self.tr("<span style='color: #888;'>Step 1 of 2</span>"))
            page1_layout.addWidget(step1_indicator)

            # Title
            page1_title = QLabel(self.tr("<h2>📁 Choose Your Data Folder</h2>"))
            page1_layout.addWidget(page1_title)

            # Explanation
            page1_msg = QLabel(
                "Supervertaler stores your data in a folder of your choice:<br><br>"
                "• <b>API keys</b> – Your LLM provider credentials<br>"
                "• <b>Translation memories</b> – Reusable translation pairs<br>"
                "• <b>Termbases</b> – Terminology databases<br>"
                "• <b>Prompts</b> – Custom AI prompts<br>"
                "• <b>Settings</b> – Application configuration<br><br>"
                "Choose a location that's easy to find and backup."
            )
            page1_msg.setWordWrap(True)
            page1_layout.addWidget(page1_msg)

            # Path input with browse button
            path_layout = QHBoxLayout()
            path_edit = QLineEdit()
            default_path = get_default_user_data_path()
            path_edit.setText(str(default_path))
            path_edit.setMinimumWidth(350)
            path_layout.addWidget(path_edit)

            browse_btn = QPushButton(self.tr("Browse..."))
            def browse_folder():
                folder = QFileDialog.getExistingDirectory(
                    dialog,
                    "Choose Data Folder",
                    str(Path.home())
                )
                if folder:
                    folder_path = Path(folder)
                    if folder_path.name != "Supervertaler":
                        folder_path = folder_path / "Supervertaler"
                    path_edit.setText(str(folder_path))

            browse_btn.clicked.connect(browse_folder)
            path_layout.addWidget(browse_btn)
            page1_layout.addLayout(path_layout)

            # Tip
            page1_tip = QLabel(
                "💡 <b>Tip:</b> The default location is in your home folder, "
                "making it easy to find and backup."
            )
            page1_tip.setWordWrap(True)
            page1_tip.setStyleSheet("color: #666;")
            page1_layout.addWidget(page1_tip)

            page1_layout.addStretch()
            stacked.addWidget(page1)

            # ==================== PAGE 2: Features Introduction ====================
            page2 = QWidget()
            page2_layout = QVBoxLayout(page2)
            page2_layout.setSpacing(15)

            # Step indicator
            step2_label = "Step 2 of 2" if show_data_folder_page else "Setup"
            step2_indicator = QLabel(f"<span style='color: #888;'>{step2_label}</span>")
            page2_layout.addWidget(step2_indicator)

            # Data folder info (shown when skipping page 1)
            if not show_data_folder_page:
                from PyQt6.QtGui import QDesktopServices
                from PyQt6.QtCore import QUrl

                data_folder_path = str(self.user_data_path)
                data_folder_info = QLabel(
                    f"<b>📁 Data Folder:</b> <a href='file:///{data_folder_path}' "
                    f"style='color: #3b82f6;'>{data_folder_path}</a><br>"
                    "<span style='color: #666; font-size: 0.9em;'>"
                    "Your settings, TMs, termbases and prompts are stored here. "
                    "Change in Settings → General.</span>"
                )
                data_folder_info.setWordWrap(True)
                data_folder_info.setTextFormat(Qt.TextFormat.RichText)
                data_folder_info.setOpenExternalLinks(False)  # Handle clicks ourselves
                data_folder_info.linkActivated.connect(
                    lambda url: QDesktopServices.openUrl(QUrl.fromLocalFile(data_folder_path))
                )
                data_folder_info.setStyleSheet(
                    "background: #f0f4ff; padding: 12px; border-radius: 6px; "
                    "border-left: 4px solid #3b82f6; margin-bottom: 10px;"
                )
                page2_layout.addWidget(data_folder_info)

            # Title
            page2_title = QLabel(self.tr("<h2>✨ You're all set!</h2>"))
            page2_layout.addWidget(page2_title)

            # Message – kept deliberately short. Earlier versions of this
            # page explained Supervertaler's modular-features architecture
            # and pointed users at Settings → Features. Both are gone in
            # v1.9.474: every feature module that used to be optional is
            # now a core dependency, so there is nothing for the user to
            # install or toggle.
            page2_msg = QLabel(
                "Your data folder is ready and Supervertaler is set up.<br><br>"
                "Have a look around the <b>Editor</b> tab to start translating, "
                "or visit <b>Settings</b> to configure API keys, prompts, theme, "
                "and more."
            )
            page2_msg.setWordWrap(True)
            page2_msg.setTextFormat(Qt.TextFormat.RichText)
            page2_layout.addWidget(page2_msg)

            # Checkbox
            dont_show_checkbox = CheckmarkCheckBox(self.tr("Don't show this wizard on startup"))
            dont_show_checkbox.setChecked(True)
            page2_layout.addWidget(dont_show_checkbox)

            page2_layout.addStretch()
            stacked.addWidget(page2)

            main_layout.addWidget(stacked)

            # ==================== Navigation Buttons ====================
            nav_layout = QHBoxLayout()

            back_btn = QPushButton(self.tr("← Back"))
            back_btn.setVisible(False)  # Hidden on first page

            next_btn = QPushButton(self.tr("Next →"))
            finish_btn = QPushButton(self.tr("Finish"))
            finish_btn.setVisible(False)
            finish_btn.setDefault(True)

            # Use Default button (only on page 1)
            default_btn = QPushButton(self.tr("Use Default"))
            default_btn.clicked.connect(lambda: path_edit.setText(str(default_path)))

            nav_layout.addWidget(default_btn)
            nav_layout.addStretch()
            nav_layout.addWidget(back_btn)
            nav_layout.addWidget(next_btn)
            nav_layout.addWidget(finish_btn)

            main_layout.addLayout(nav_layout)

            # Track chosen path for later
            chosen_path_holder = [None]

            def go_to_page(page_index):
                stacked.setCurrentIndex(page_index)
                if page_index == 0:
                    back_btn.setVisible(False)
                    next_btn.setVisible(True)
                    finish_btn.setVisible(False)
                    default_btn.setVisible(True)
                else:
                    back_btn.setVisible(show_data_folder_page)
                    next_btn.setVisible(False)
                    finish_btn.setVisible(True)
                    default_btn.setVisible(False)

            def on_next():
                # Save the data folder choice
                chosen_path = Path(path_edit.text())
                chosen_path_holder[0] = chosen_path

                # Create the folder and save config
                chosen_path.mkdir(parents=True, exist_ok=True)
                save_user_data_path(chosen_path)

                # Update our path if different
                if chosen_path != self.user_data_path:
                    self.user_data_path = chosen_path
                    self._reinitialize_with_new_data_path()
                else:
                    if hasattr(self, 'db_manager') and self.db_manager and not self.db_manager.connection:
                        self.db_manager.connect()

                self.log(f"📁 Data folder set to: {chosen_path}")
                go_to_page(1)

            def on_back():
                go_to_page(0)

            def on_finish():
                # Save first_run preference
                if dont_show_checkbox.isChecked():
                    settings = self.load_general_settings()
                    settings['first_run_completed'] = True
                    self.save_general_settings(settings)
                    self.log("✅ Setup wizard completed (won't show again on startup)")
                else:
                    self.log("✅ Setup wizard shown (will show again next time)")

                dialog.accept()

            back_btn.clicked.connect(on_back)
            next_btn.clicked.connect(on_next)
            finish_btn.clicked.connect(on_finish)

            # Start on appropriate page
            if show_data_folder_page:
                go_to_page(0)
            else:
                # Skip to features page if data folder already configured
                go_to_page(1)
                step2_indicator.setText(self.tr("<span style='color: #888;'>Supervertaler Setup</span>"))

            dialog.exec()

        except Exception as e:
            self.log(f"⚠️ Setup wizard error: {e}")
            import traceback
            traceback.print_exc()

    def init_ui(self):
        """Initialize the user interface"""
        # Build window title with dev mode indicator
        title = f"Supervertaler Workbench v{__version__}"
        if ENABLE_PRIVATE_FEATURES:
            title += " [🛠️ DEV MODE]"
        self.setWindowTitle(title)
        self.setGeometry(100, 100, 1400, 800)

        # Set application icon
        from PyQt6.QtGui import QIcon
        icon_path = get_resource_path("assets/icon.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # Ensure window can be resized (no minimum size constraint)
        self.setMinimumSize(400, 300)  # Very small minimum to allow resizing
        
        # Create menu bar (ribbon removed - using traditional menus)
        self.create_menus()
        
        # Ribbon removed - all functionality moved to menu bar
        # self.create_ribbon()
        
        # Create main layout
        self.create_main_layout()
        
        # Create status bar with progress indicators
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")
        
        # Add permanent progress widgets to status bar (right side)
        self._setup_progress_indicators()

        # Setup global shortcuts
        self.setup_global_shortcuts()

        # Install context-sensitive help (F1 opens relevant GitBook page)
        install_help_system(self)

    def setup_global_shortcuts(self):
        """Setup application-wide keyboard shortcuts"""
        from PyQt6.QtGui import QShortcut
        
        # Store all shortcuts with their IDs and default key sequences for enable/disable management
        self.global_shortcuts = {}  # shortcut_id -> QShortcut
        self.global_shortcut_keys = {}  # shortcut_id -> current key sequence string (from manager)
        
        def create_shortcut(shortcut_id: str, default_key: str, handler):
            """Create a shortcut, using custom key from manager if set, respecting enabled state"""
            # Get the actual key sequence (custom or default) from the manager
            key_sequence = self.shortcut_manager.get_shortcut(shortcut_id)
            # Important: an empty string is a valid "unassigned" default.
            # Only fall back to the passed default_key if the shortcut ID is unknown to the manager.
            if key_sequence == "":
                default_ids = getattr(self.shortcut_manager, 'DEFAULT_SHORTCUTS', {})
                custom_ids = getattr(self.shortcut_manager, 'custom_shortcuts', {})
                if shortcut_id not in default_ids and shortcut_id not in custom_ids:
                    key_sequence = default_key
            
            shortcut = QShortcut(QKeySequence(key_sequence), self)
            shortcut.activated.connect(handler)
            # Store the current key sequence for re-enabling later
            self.global_shortcut_keys[shortcut_id] = key_sequence
            # If disabled, clear the key sequence to fully release the key combination
            if not self.shortcut_manager.is_enabled(shortcut_id):
                shortcut.setKey(QKeySequence())  # Clear key to release combination
            self.global_shortcuts[shortcut_id] = shortcut
            return shortcut

        # Segment split / merge (Trados / memoQ style). Window-level QShortcuts
        # fire even with a cell editor focused (QTextEdit doesn't consume
        # Ctrl+Alt combos). Configurable in Settings → Keyboard Shortcuts.
        create_shortcut("editor_split_segment", "Ctrl+Alt+S", self.split_current_segment)
        create_shortcut("editor_merge_segment", "Ctrl+Alt+M", self.merge_current_segment)
        
        # Ctrl+Up/Down - Segment navigation is handled by _GridArrowKeyEventFilter
        # (QTableWidget intercepts vertical arrow keys before QShortcuts can fire)
        # Legacy match cycling (Translation Results panel) has been removed.
        
        # Ctrl+1 through Ctrl+9 - Insert match by number
        self.match_shortcuts = []
        for i in range(1, 10):
            shortcut_id = f"match_insert_{i}"
            default_key = f"Ctrl+{i}"
            key_sequence = self.shortcut_manager.get_shortcut(shortcut_id)
            # Respect intentionally blank defaults (unassigned shortcuts)
            if key_sequence == "":
                default_ids = getattr(self.shortcut_manager, 'DEFAULT_SHORTCUTS', {})
                custom_ids = getattr(self.shortcut_manager, 'custom_shortcuts', {})
                if shortcut_id not in default_ids and shortcut_id not in custom_ids:
                    key_sequence = default_key
            shortcut = QShortcut(QKeySequence(key_sequence), self)
            shortcut.activated.connect(lambda num=i: self.insert_match_by_number(num))
            self.global_shortcut_keys[shortcut_id] = key_sequence
            if not self.shortcut_manager.is_enabled(shortcut_id):
                shortcut.setKey(QKeySequence())  # Clear key to release combination
            self.global_shortcuts[shortcut_id] = shortcut
            self.match_shortcuts.append(shortcut)

        # Compare Panel insertion shortcut
        # Alt+0 inserts MT; double-tap Alt+0,0 inserts TM Target.
        self._compare_panel_last_key = None
        self._compare_panel_last_time = 0
        create_shortcut("compare_insert_alt0", "Alt+0", self._handle_compare_panel_alt0_shortcut)

        # Compare Panel navigation shortcuts
        # MT prev/next: Ctrl+Alt+Left / Ctrl+Alt+Right
        # TM prev/next: Ctrl+Alt+Up / Ctrl+Alt+Down
        create_shortcut(
            "compare_nav_mt_prev",
            "Ctrl+Alt+Left",
            lambda: self._compare_panel_nav_mt(-1) if self._get_active_match_shortcut_mode() == 'compare' else None,
        )
        create_shortcut(
            "compare_nav_mt_next",
            "Ctrl+Alt+Right",
            lambda: self._compare_panel_nav_mt(1) if self._get_active_match_shortcut_mode() == 'compare' else None,
        )
        create_shortcut(
            "compare_nav_tm_prev",
            "Ctrl+Alt+Up",
            lambda: self._compare_panel_nav_tm(-1) if self._get_active_match_shortcut_mode() == 'compare' else None,
        )
        create_shortcut(
            "compare_nav_tm_next",
            "Ctrl+Alt+Down",
            lambda: self._compare_panel_nav_tm(1) if self._get_active_match_shortcut_mode() == 'compare' else None,
        )
        
        # Alt+0 through Alt+9 - Insert term from TermLens by number
        # Supports double-tap for terms 11-20 (00, 11, 22, ..., 99)
        self.termlens_shortcuts = []
        self._termlens_last_key = None  # Track last key for double-tap detection
        self._termlens_last_time = 0    # Track timing for double-tap
        
        # Double-tap Shift detection for context menu
        self._shift_last_press_time = 0
        self._shift_double_tap_threshold = 0.35  # 350ms window
        
        for i in range(0, 10):  # 0-9
            shortcut_id = f"termlens_insert_{i}"
            default_key = f"Alt+{i}"
            key_sequence = self.shortcut_manager.get_shortcut(shortcut_id)
            # Respect intentionally blank defaults (unassigned shortcuts)
            if key_sequence == "":
                default_ids = getattr(self.shortcut_manager, 'DEFAULT_SHORTCUTS', {})
                custom_ids = getattr(self.shortcut_manager, 'custom_shortcuts', {})
                if shortcut_id not in default_ids and shortcut_id not in custom_ids:
                    key_sequence = default_key
            shortcut = QShortcut(QKeySequence(key_sequence), self)
            shortcut.activated.connect(lambda num=i: self._handle_termlens_shortcut(num))
            self.global_shortcut_keys[shortcut_id] = key_sequence
            if not self.shortcut_manager.is_enabled(shortcut_id):
                shortcut.setKey(QKeySequence())  # Clear key to release combination
            self.global_shortcuts[shortcut_id] = shortcut
            self.termlens_shortcuts.append(shortcut)
        
        # Ctrl+Space - Insert currently selected match
        create_shortcut("match_insert_selected_ctrl", "Ctrl+Space", self.insert_selected_match)

        # Ctrl+Alt+P - Toggle the right panel to the Document Preview and back
        create_shortcut("toggle_preview", "Ctrl+Alt+P", self._toggle_preview_panel)
        
        # Ctrl+Up/Down - Navigate to previous/next segment
        # Handled by _GridArrowKeyEventFilter (QTableWidget eats vertical arrow keys
        # before QShortcuts can fire, so QShortcut-based registration doesn't work)
        
        # Ctrl+Home/End - Navigate to first/last segment
        create_shortcut("segment_go_to_top", "Ctrl+Home", self.go_to_first_segment)
        create_shortcut("segment_go_to_bottom", "Ctrl+End", self.go_to_last_segment)
        
        # Ctrl+Enter - Confirm segment(s) and go to next unconfirmed
        # If multiple segments selected: confirm all selected
        # If single segment: confirm and go to next unconfirmed
        ctrl_enter_shortcut = create_shortcut("editor_save_and_next", "Ctrl+Return", self.confirm_selected_or_next)
        # CRITICAL: Use ApplicationShortcut context so it works even when focus is in QTextEdit widgets
        ctrl_enter_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)

        # Workaround: ensure Ctrl+Return (main keyboard) is caught even if swallowed before QShortcut
        # (Observed: Ctrl+Enter on numpad works, Ctrl+Return does not when focus is in source cell.)
        if not hasattr(self, '_ctrl_return_event_filter'):
            from PyQt6.QtWidgets import QApplication
            self._ctrl_return_event_filter = _CtrlReturnEventFilter(self)
            QApplication.instance().installEventFilter(self._ctrl_return_event_filter)
        
        # Alt+Up/Down (glossary) and Ctrl+Up/Down (segment nav) in grid cells.
        # QAbstractItemView intercepts vertical arrow keys at the viewport level,
        # preventing them from reaching the cell widget's keyPressEvent/event().
        # This app-level filter sits above the viewport and dispatches them directly.
        if not hasattr(self, '_grid_arrow_key_event_filter'):
            from PyQt6.QtWidgets import QApplication
            self._grid_arrow_key_event_filter = _GridArrowKeyEventFilter(self)
            QApplication.instance().installEventFilter(self._grid_arrow_key_event_filter)

        # v1.10.187: double-tap-Shift → context menu detector REMOVED.
        # Originally an AutoHotkey replacement (cross-platform shim);
        # accumulated false-positive fixes in v1.10.154 (shifted-character
        # typing), v1.10.185 (Shift+Click chords) and v1.10.186 (any
        # non-Shift modifier still held at Shift release) and still
        # tripped during normal term-pair selection workflows. Native
        # Shift+F10 (Windows/Linux) and the Menu key on most keyboards
        # already open the context menu of the focused widget — exactly
        # what the gesture did — so we drop the custom event filter
        # entirely rather than playing whack-a-mole with edge cases.
        # The _DoubleTapShiftEventFilter class is also removed from the
        # module-level definitions just above.

        # Ctrl+Shift+Enter - Always confirm all selected segments
        create_shortcut("editor_confirm_selected", "Ctrl+Shift+Return", self.confirm_selected_segments)
        
        # Note: Ctrl+Shift+S (Copy source to target) is handled in EditableGridTextEditor.keyPressEvent
        
        # Ctrl+K - Concordance Search
        create_shortcut("tools_concordance_search", "Ctrl+K", self.show_concordance_search)
        
        # Ctrl+Shift+F - Filter on selected text / Clear filter (toggle)
        create_shortcut("filter_selected_text", "Ctrl+Shift+F", self.filter_on_selected_text)
        
        # Ctrl+Shift+H – Toggle Tag View. (Was Ctrl+Alt+T before v1.9.476;
        # moved to free that combo for "Add term to glossary" which matches
        # the Trados plugin's shortcut. Ctrl+Shift+H is Trados Studio's own
        # default for the equivalent tag-display toggle.)
        create_shortcut("view_toggle_tags", "Ctrl+Shift+H", self._toggle_tag_view_via_shortcut)
        
        # Page Up/Down - Navigate pagination pages
        create_shortcut("page_prev", "PgUp", self.go_to_prev_page)
        create_shortcut("page_next", "PgDown", self.go_to_next_page)
        
        # Shift+Page Up/Down - Select range of segments
        create_shortcut("select_range_up", "Shift+PgUp", self.select_range_page_up)
        create_shortcut("select_range_down", "Shift+PgDown", self.select_range_page_down)
        
        # Ctrl+G - Go to segment
        create_shortcut("edit_goto", "Ctrl+G", self.show_goto_dialog)

        # Ctrl+Shift+V - Update project from pasted bilingual text (issue #248).
        # In-app only: never make this global (see CLAUDE.md pitfall 6).
        create_shortcut("file_update_from_pasted_text", "Ctrl+Shift+V",
                        self.import_bilingual_text_from_paste)
        
        # F5 - Force refresh matches (clear all caches and re-search)
        create_shortcut("tools_force_refresh", "F5", self.force_refresh_matches)
        
        # Ctrl+Shift+1 - Quick add term to Project Glossary
        create_shortcut("editor_quick_add_priority_1", "Ctrl+Shift+1", lambda: self._quick_add_term_with_priority("project"))

        # Ctrl+Shift+2 - Quick add term to Background Glossary
        create_shortcut("editor_quick_add_priority_2", "Ctrl+Shift+2", lambda: self._quick_add_term_with_priority("background"))

        # Alt+Up/Down - Quick add term pair to Project/Background glossary
        # Handled by _GridArrowKeyEventFilter (QTableWidget eats vertical arrow keys
        # before QShortcuts can fire, so QShortcut-based registration doesn't work)
        
        # Alt+D - Add word at cursor to dictionary
        create_shortcut("editor_add_to_dictionary", "Alt+D", self.add_word_to_dictionary_shortcut)

        # Ctrl+M — Create a comment anchored to the currently-selected
        # text in the source or target cell. If there's no selection,
        # the comment is segment-level. Mirrors the Trados/memoQ
        # workflow exactly.
        # v1.10.61 history: originally Ctrl+Shift+M because the
        # QuickTrans menu items had "(Ctrl+M)" labels (stale display
        # text from an earlier version where Ctrl+M *was* bound). The
        # labels were misleading — no setShortcut() was ever wired, and
        # QuickTrans is in fact triggered via the global Ctrl+Alt+Q
        # pynput hotkey. v1.10.61 reclaimed Ctrl+M for comments.
        create_shortcut("editor_add_comment", "Ctrl+M", self.add_comment_from_selection)
        
        # Ctrl+Q - Open QuickLauncher directly
        create_shortcut("editor_open_quicklauncher", "Ctrl+Q", self.open_quicklauncher)

        # The next three actions used to have only an OS-level global hotkey
        # entry. After merging local + global into one shortcut per action,
        # they need an in-app QShortcut binding too so the same key works
        # whether or not Supervertaler is frontmost. The global side is
        # registered separately by SuperlookupTab.register_global_hotkey.
        create_shortcut(
            "tools_universal_lookup", "Ctrl+Alt+L",
            lambda: self.lookup_tab._handle_superlookup_hotkey()
                if hasattr(self, 'lookup_tab') else None,
        )
        create_shortcut("sidekick_open", "Alt+K", self.open_quicklauncher)
        create_shortcut(
            "voice_alwayson_toggle", "Ctrl+Alt+O",
            self._toggle_alwayson_listening,
        )

        # Lone Ctrl tap – Term Insert Popup (memoQ-style glossary + NT insert list).
        # Implemented as an app-level event filter rather than a QShortcut because
        # QShortcut cannot bind to a bare modifier key.
        if not hasattr(self, '_lone_ctrl_event_filter'):
            self._lone_ctrl_event_filter = _LoneCtrlEventFilter(self)
            QApplication.instance().installEventFilter(self._lone_ctrl_event_filter)

        # v1.10.97 — Ctrl+Shift tap event filter removed. Originally
        # added in v1.10.90 to give TermPicker an ergonomic
        # modifier-chord trigger; turned out to clash with AltGr (which
        # emits Ctrl+Alt key events on Windows for users on Dutch /
        # German / other layouts) and to flood the session log with
        # diagnostic chatter for every chord attempt. User feedback:
        # "can we remove the Ctrl+Shift shortcut and just use
        # Ctrl+Shift+B?" The QShortcut below remains as the canonical
        # TermPicker trigger; the lone-Ctrl tap above remains the
        # canonical TermLens-popup trigger.

        # Ctrl+Shift+Q - MT Quick Lookup (GT4T-style popup)
        mt_quick_shortcut = create_shortcut("mt_quick_lookup", "Ctrl+Shift+Q", self.show_mt_quick_popup)
        # Use ApplicationShortcut context so it works even when focus is in QTextEdit widgets
        mt_quick_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)

        # Ctrl+Shift+P - TermPicker (v1.10.87 — Trados parity, renamed v1.10.205).
        # Modal tabular dialog listing all matches for the current segment
        # with synonym expand/collapse + digit quick-pick. Sibling surface to
        # TermLens: same data (termbase matches), different ergonomics —
        # TermLens shows matches inline in context, TermPicker offers a
        # keyboard-driven flat list for quick insertion.
        # v1.10.89: was switched from Ctrl+Shift+P to Ctrl+Shift+B because
        # Scratchpad held Ctrl+Shift+P at the time.
        # v1.10.206: reclaimed Ctrl+Shift+P for TermPicker, matching the
        # Trados plugin exactly (P = Picker, VS Code-style command-palette
        # convention). Scratchpad simultaneously moved to Alt+S.
        term_picker_shortcut = create_shortcut("term_picker", "Ctrl+Shift+P", self.show_term_picker_dialog)
        term_picker_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
    
    def open_quicklauncher(self):
        """Ctrl+Q / Alt+K: pop up the QuickLauncher prompt menu for the
        current segment, the same menu available from the grid right-click.

        Each configured QuickLauncher prompt is listed as a submenu offering
        Run (show response), Run and replace selection, and Copy to clipboard.
        The menu acts on the focused source/target editor, falling back to the
        current row's target editor.
        """
        from PyQt6.QtWidgets import QMenu, QMessageBox
        from PyQt6.QtGui import QCursor

        try:
            # Decide which editor the prompt should act on
            origin = QApplication.focusWidget()
            if not isinstance(origin, QTextEdit):
                origin = None
            if origin is None and hasattr(self, 'table') and self.table:
                row = self.table.currentRow()
                if row is not None and row >= 0:
                    cell = self.table.cellWidget(row, 3)  # target column
                    if isinstance(cell, QTextEdit):
                        origin = cell
            if not isinstance(origin, QTextEdit):
                QMessageBox.information(self, "QuickLauncher", "Select a segment in the grid first.")
                return

            # Gather configured QuickLauncher prompts
            quicklauncher_items = []
            if hasattr(self, 'prompt_manager_qt') and self.prompt_manager_qt:
                lib = getattr(self.prompt_manager_qt, 'library', None)
                if lib and hasattr(lib, 'get_quicklauncher_grid_prompts'):
                    quicklauncher_items = lib.get_quicklauncher_grid_prompts() or []

            menu = QMenu(self)
            if not quicklauncher_items:
                act = menu.addAction("No QuickLauncher prompts configured")
                act.setEnabled(False)
            else:
                for rel_path, label in sorted(quicklauncher_items, key=lambda x: (x[1] or x[0]).lower()):
                    prompt_menu = menu.addMenu(label or rel_path)
                    run_show = prompt_menu.addAction("▶ Run (show response)…")
                    run_show.triggered.connect(
                        lambda checked=False, p=rel_path: self.run_grid_quicklauncher_prompt(p, origin_widget=origin, behavior="show")
                    )
                    run_replace = prompt_menu.addAction("↺ Run and replace target selection")
                    run_replace.triggered.connect(
                        lambda checked=False, p=rel_path: self.run_grid_quicklauncher_prompt(p, origin_widget=origin, behavior="replace")
                    )
                    copy_clip = prompt_menu.addAction("📋 Copy to clipboard")
                    copy_clip.triggered.connect(
                        lambda checked=False, p=rel_path: self.run_grid_quicklauncher_prompt(p, origin_widget=origin, behavior="clipboard")
                    )

            menu.exec(QCursor.pos())

        except Exception as e:
            self.log(f"❌ Error opening QuickLauncher: {e}")

    def show_supervertaler_assistant(self, initial_text=None, external_mode=False):
        """Navigate to the Supervertaler Sidekick (AI Assistant tab) and optionally insert text.

        Instead of a separate floating window, this reuses the existing AI Assistant
        inside the AI tab. For external mode, it also brings Supervertaler
        to the foreground first.
        """
        try:
            # If called from external app, bring Supervertaler to front
            if external_mode:
                self.show()
                self.raise_()
                self.activateWindow()

            # Switch to Grid tab (index 0) so the right panel is visible
            if hasattr(self, 'main_tabs'):
                self.main_tabs.setCurrentIndex(0)

            # Switch the right panel to the AI Assistant tab
            if hasattr(self, 'right_tabs') and hasattr(self, '_assistant_tab_index'):
                self.right_tabs.setCurrentIndex(self._assistant_tab_index)

            # Insert text into the assistant chat input
            if hasattr(self, 'prompt_manager_qt') and self.prompt_manager_qt:
                self.prompt_manager_qt.receive_text_for_assistant(
                    initial_text or "", from_external=external_mode
                )

            print(f"[Assistant] Navigated to Supervertaler Sidekick (external={external_mode})")

        except Exception as e:
            print(f"[Assistant] Error navigating to assistant: {e}")
            import traceback
            traceback.print_exc()

    def _quicktrans_base_pair(self):
        """The project's (source, target) language pair for QuickTrans, before
        any auto-detect or manual override. Prefers the open project's pair,
        falling back to the app defaults."""
        proj = getattr(self, 'current_project', None)
        if proj and getattr(proj, 'source_lang', None) and getattr(proj, 'target_lang', None):
            return (proj.source_lang, proj.target_lang)
        return (getattr(self, 'source_language', 'English'),
                getattr(self, 'target_language', 'Dutch'))

    def set_quicktrans_direction_override(self, src, tgt):
        """Record a manual QuickTrans direction chosen in the popup's language
        selector. It's tagged with the base pair it was set against, so it stays
        sticky for later popups but is automatically dropped once the project /
        default pair is changed elsewhere (Settings → Language Pair, project
        switch, etc.)."""
        self._quicktrans_lang_override = {
            'src': src, 'tgt': tgt, 'base': self._quicktrans_base_pair(),
        }

    def resolve_quicktrans_direction(self, text):
        """Resolve the (source, target) direction for a QuickTrans request.

        Priority: a sticky manual override (if still valid for the current base
        pair) → otherwise per-text auto-detection → otherwise the base pair.
        """
        base = self._quicktrans_base_pair()
        override = getattr(self, '_quicktrans_lang_override', None)
        if override and override.get('base') == base:
            return override['src'], override['tgt']
        # Stale (pair changed elsewhere) or none → forget it and auto-detect.
        if override:
            self._quicktrans_lang_override = None
        try:
            from modules.lang_detect import resolve_direction
            return resolve_direction(text, base[0], base[1])
        except Exception as e:
            try:
                self.log(f"QuickTrans language auto-detect skipped: {e}")
            except Exception:
                pass
            return base

    def refresh_shortcut_enabled_states(self):
        """Refresh enabled/disabled states and key bindings of all global shortcuts from shortcut manager.
        
        When disabled: clears the key sequence to fully release the key combination.
        When enabled: restores the key sequence from the manager (custom or default).
        """
        if not hasattr(self, 'global_shortcuts'):
            return
        for shortcut_id, shortcut in self.global_shortcuts.items():
            is_enabled = self.shortcut_manager.is_enabled(shortcut_id)
            if is_enabled:
                # Get the current key sequence from manager (may have been customized)
                key_sequence = self.shortcut_manager.get_shortcut(shortcut_id)
                if key_sequence:
                    self.global_shortcut_keys[shortcut_id] = key_sequence
                    shortcut.setKey(QKeySequence(key_sequence))
            else:
                # Clear the key to release the combination for other uses
                shortcut.setKey(QKeySequence())

    def _setup_progress_indicators(self):
        """Setup permanent progress indicator widgets in the status bar"""
        from PyQt6.QtWidgets import QLabel, QFrame

        # Create a container frame for all progress info
        progress_frame = QFrame()
        progress_layout = QHBoxLayout(progress_frame)
        progress_layout.setContentsMargins(0, 0, 10, 0)
        progress_layout.setSpacing(20)

        # LLM Provider/Model indicator (leftmost in the permanent area)
        self.llm_indicator_label = QLabel("")
        self.llm_indicator_label.setStyleSheet("color: #888; font-size: 11px;")
        self.llm_indicator_label.setToolTip(self.tr("Current LLM provider and model"))
        progress_layout.addWidget(self.llm_indicator_label)
        
        # Update LLM indicator on startup
        self._update_llm_indicator()

        # Words translated label
        self.progress_words_label = QLabel(self.tr("Words: --"))
        self.progress_words_label.setStyleSheet("color: #555; font-size: 11px;")
        self.progress_words_label.setToolTip(self.tr("Words with translation / Total words (percentage)"))
        progress_layout.addWidget(self.progress_words_label)

        # Segments confirmed label
        # The confirmed percentage can be measured two ways: by source word
        # count (default) or by segment count. Word-based progress is steadier
        # on short documents, where confirming a single long segment swings the
        # segment percentage a lot. Click the label to switch (also available
        # as a checkbox under View Settings); the choice persists in the "ui"
        # settings section as ``confirmed_progress_basis``.
        self._confirmed_progress_basis = "words"
        try:
            _basis = self._load_settings_section("ui").get("confirmed_progress_basis")
            if _basis in ("segments", "words"):
                self._confirmed_progress_basis = _basis
        except Exception:
            pass
        self.progress_confirmed_label = QLabel(self.tr("Confirmed: --"))
        self.progress_confirmed_label.setStyleSheet("color: #555; font-size: 11px;")
        self.progress_confirmed_label.setToolTip(self.tr("Confirmed segments / Total segments (percentage)\nClick to switch between segment- and word-based percentage"))
        self.progress_confirmed_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.progress_confirmed_label.mousePressEvent = lambda e: self._show_confirmed_basis_menu()
        progress_layout.addWidget(self.progress_confirmed_label)

        # Remaining segments label
        self.progress_remaining_label = QLabel(self.tr("Remaining: --"))
        self.progress_remaining_label.setStyleSheet("color: #555; font-size: 11px;")
        self.progress_remaining_label.setToolTip(self.tr("Segments still requiring work"))
        progress_layout.addWidget(self.progress_remaining_label)
        
        # Files indicator (for multi-file projects)
        self.progress_files_label = QLabel("")
        self.progress_files_label.setStyleSheet("color: #2196F3; font-size: 11px;")
        self.progress_files_label.setToolTip(self.tr("Files in multi-file project (click for details)"))
        self.progress_files_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.progress_files_label.mousePressEvent = lambda e: self.show_project_info_dialog(initial_tab=1)
        self.progress_files_label.hide()  # Hidden by default, shown for multi-file projects
        progress_layout.addWidget(self.progress_files_label)
        
        # Always-on voice indicator
        self.alwayson_indicator_label = QLabel("")
        self.alwayson_indicator_label.setStyleSheet("font-size: 11px; font-weight: bold;")
        self.alwayson_indicator_label.setToolTip(self.tr("Always-on voice listening status\nClick to toggle"))
        self.alwayson_indicator_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.alwayson_indicator_label.mousePressEvent = lambda e: self._toggle_alwayson_from_statusbar()
        self.alwayson_indicator_label.hide()  # Hidden until enabled
        progress_layout.addWidget(self.alwayson_indicator_label)

        # Add as permanent widget (stays on right side)
        self.status_bar.addPermanentWidget(progress_frame)
        
        # Initialize Ollama keep-warm timer
        self.ollama_keepwarm_timer = None
        self._setup_ollama_keepwarm()
        self._apply_ollama_timeout_setting()

    def _usage_log_default_context(self):
        """Attribution for AI calls made outside a batch run (usage log, #8)."""
        proj = getattr(self, 'current_project', None)
        if not proj:
            return {}
        return {'project': getattr(proj, 'name', None),
                'src_lang': getattr(proj, 'source_lang', None),
                'tgt_lang': getattr(proj, 'target_lang', None)}

    def update_progress_stats(self):
        """Update the progress indicator labels in the status bar"""
        try:
            if not self.current_project or not self.current_project.segments:
                self.progress_words_label.setText(self.tr("Words: --"))
                self.progress_confirmed_label.setText(self.tr("Confirmed: --"))
                self.progress_remaining_label.setText(self.tr("Remaining: --"))
                if hasattr(self, 'progress_files_label'):
                    self.progress_files_label.hide()
                return

            segments = self.current_project.segments
            total_segments = len(segments)

            # Count words in source text for each segment
            total_words = 0
            translated_words = 0
            confirmed_count = 0
            confirmed_words = 0
            remaining_count = 0

            # Statuses that indicate "done" (confirmed or higher)
            confirmed_statuses = {'confirmed', 'proofread', 'approved'}
            # Statuses that need work
            unfinished_statuses = {'not_started', 'pretranslated', 'rejected', 'tm_100', 'tm_fuzzy'}

            for segment in segments:
                # Count words in source (simple split on whitespace)
                source_words = len(segment.source.split()) if segment.source else 0
                total_words += source_words

                # If segment has a non-empty translation, count those words as translated
                if segment.target and segment.target.strip():
                    translated_words += source_words

                # Count confirmed (or higher status)
                if segment.status in confirmed_statuses:
                    confirmed_count += 1
                    confirmed_words += source_words

                # Count remaining (not_started, pretranslated, or rejected)
                if segment.status in unfinished_statuses:
                    remaining_count += 1

            # Calculate percentages
            word_percent = (translated_words / total_words * 100) if total_words > 0 else 0
            # The confirmed percentage honours the user's chosen basis (segment
            # count vs source word count) – see the status-bar label setup.
            if getattr(self, '_confirmed_progress_basis', 'words') == 'words':
                confirmed_percent = (confirmed_words / total_words * 100) if total_words > 0 else 0
                confirmed_text = f"Confirmed: {confirmed_words}/{total_words} words ({confirmed_percent:.0f}%)"
                confirmed_tip = self.tr("Confirmed words / Total words (percentage)\nClick to switch to segment-based percentage")
            else:
                confirmed_percent = (confirmed_count / total_segments * 100) if total_segments > 0 else 0
                confirmed_text = f"Confirmed: {confirmed_count}/{total_segments} ({confirmed_percent:.0f}%)"
                confirmed_tip = self.tr("Confirmed segments / Total segments (percentage)\nClick to switch to word-based percentage")

            # Update labels with color-coding based on progress
            word_color = self._get_progress_color(word_percent)
            confirmed_color = self._get_progress_color(confirmed_percent)
            remaining_color = "#c00" if remaining_count > 0 else "#080"

            self.progress_words_label.setText(f"Words: {translated_words}/{total_words} ({word_percent:.0f}%)")
            self.progress_words_label.setStyleSheet(f"color: {word_color}; font-size: 11px;")

            self.progress_confirmed_label.setText(confirmed_text)
            self.progress_confirmed_label.setToolTip(confirmed_tip)
            self.progress_confirmed_label.setStyleSheet(f"color: {confirmed_color}; font-size: 11px;")

            self.progress_remaining_label.setText(f"Remaining: {remaining_count}")
            self.progress_remaining_label.setStyleSheet(f"color: {remaining_color}; font-size: 11px;")
            
            # Multi-file project indicator
            if hasattr(self, 'progress_files_label'):
                is_multifile = getattr(self.current_project, 'is_multifile', False)
                files = getattr(self.current_project, 'files', [])
                
                if is_multifile and files:
                    # Count completed files – one pass over the segments, not
                    # one per file (300 files × 17,000 segments, issue #185).
                    per_file = {}  # file_id → [segments, confirmed]
                    for s in segments:
                        counts = per_file.setdefault(getattr(s, 'file_id', None), [0, 0])
                        counts[0] += 1
                        if s.status in confirmed_statuses:
                            counts[1] += 1
                    completed_files = 0
                    for file_info in files:
                        total, confirmed = per_file.get(file_info['id'], (0, 0))
                        if total and confirmed == total:
                            completed_files += 1
                    
                    self.progress_files_label.setText(f"📁 Files: {completed_files}/{len(files)}")
                    self.progress_files_label.setToolTip(
                        f"Multi-file project: {len(files)} files\n"
                        f"Completed: {completed_files} files\n\n"
                        "Click for detailed file progress"
                    )
                    self.progress_files_label.show()
                else:
                    self.progress_files_label.hide()

        except Exception as e:
            self.log(f"⚠️ Error updating progress stats: {e}")

    def _get_progress_color(self, percent: float) -> str:
        """Get color for progress percentage: red < 50%, orange < 80%, green >= 80%"""
        if percent < 50:
            return "#c00"  # Red
        elif percent < 80:
            return "#c60"  # Orange
        else:
            return "#080"  # Green

    def _show_confirmed_basis_menu(self):
        """Pop a small menu to switch the confirmed percentage between
        segment-count and word-count basis (triggered by clicking the
        status-bar Confirmed label)."""
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QAction, QActionGroup, QCursor

        menu = QMenu(self)
        group = QActionGroup(menu)
        group.setExclusive(True)

        current = getattr(self, '_confirmed_progress_basis', 'words')
        seg_action = QAction(self.tr("Percentage by segment count"), self)
        seg_action.setCheckable(True)
        seg_action.setChecked(current == "segments")
        seg_action.triggered.connect(lambda: self._set_confirmed_progress_basis("segments"))
        group.addAction(seg_action)
        menu.addAction(seg_action)

        word_action = QAction(self.tr("Percentage by word count"), self)
        word_action.setCheckable(True)
        word_action.setChecked(current == "words")
        word_action.triggered.connect(lambda: self._set_confirmed_progress_basis("words"))
        group.addAction(word_action)
        menu.addAction(word_action)

        menu.exec(QCursor.pos())

    def _set_confirmed_progress_basis(self, basis: str):
        """Persist the confirmed-percentage basis and refresh the status bar.

        Single source of truth for both the status-bar label click and the
        View Settings checkbox; keeps the checkbox in sync if it exists."""
        if basis not in ("segments", "words"):
            return
        if getattr(self, '_confirmed_progress_basis', 'words') == basis:
            return
        self._confirmed_progress_basis = basis
        try:
            ui = self._load_settings_section("ui")
            ui["confirmed_progress_basis"] = basis
            self._save_settings_section("ui", ui)
        except Exception as e:
            self.log(f"⚠ Could not save confirmed-progress preference: {e}")
        # Keep the View Settings checkbox in step (block its signal so we don't
        # loop back into this method).
        cb = getattr(self, '_confirmed_words_checkbox', None)
        if cb is not None:
            cb.blockSignals(True)
            cb.setChecked(basis == "words")
            cb.blockSignals(False)
        self.update_progress_stats()

    def create_menus(self):
        """Create application menus"""
        menubar = self.menuBar()
        
        # v1.10.182: renamed from "&File" to "&Project" — every action
        # in this menu acts on the *project* (new / open / save / import
        # source docs into the project / export the translated project),
        # not on bare files. "&Project" matches the user's mental model.
        # Variable name kept as file_menu to minimise patch surface.
        file_menu = menubar.addMenu(self.tr("&Project"))

        new_action = QAction(self.tr("&New Project"), self)
        new_action.setShortcut(QKeySequence.StandardKey.New)
        new_action.triggered.connect(self.new_project)
        file_menu.addAction(new_action)

        open_action = QAction(self.tr("&Open Project..."), self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_project)
        file_menu.addAction(open_action)

        # Recent projects submenu
        self.recent_menu = file_menu.addMenu(self.tr("Open &Recent"))
        self.update_recent_menu()

        file_menu.addSeparator()

        save_action = QAction(self.tr("&Save"), self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self.save_project)
        file_menu.addAction(save_action)

        save_as_action = QAction(self.tr("Save &As..."), self)
        # No keyboard shortcut - Ctrl+Shift+S is used for Copy Source to Target in editor
        save_as_action.triggered.connect(self.save_project_as)
        file_menu.addAction(save_as_action)

        file_menu.addSeparator()

        close_action = QAction(self.tr("&Close Project"), self)
        close_action.triggered.connect(self.close_project)
        file_menu.addAction(close_action)

        file_menu.addSeparator()

        # Import/Export submenu
        import_menu = file_menu.addMenu(self.tr("&Import"))
        
        # v1.10.53: collapsed the previous two-item split (DOCX vs "Other
        # format via Okapi") into one. Every supported format goes through
        # the Okapi sidecar regardless – the split was a stale UX leftover
        # from when there used to be two engines. The file picker accepts
        # DOCX, IDML, HTML, XLIFF, PO, XLSX, and PPTX with an "All
        # supported formats" default filter.
        import_document_action = QAction(self.tr("&Import Document..."), self)
        import_document_action.setToolTip(self.tr(
            "Import a Word document (DOCX), Adobe InDesign markup (IDML), "
            "HTML, XLIFF, PO, Excel (XLSX), or PowerPoint (PPTX). Extracted "
            "via the Okapi sidecar; round-trips back to the original format "
            "on export."
        ))
        import_document_action.triggered.connect(self.import_document)
        # Not Ctrl+O: that is Open Project (QKeySequence.StandardKey.Open), and
        # two menu actions on one key are ambiguous to Qt, so neither fired.
        import_document_action.setShortcut("Ctrl+Shift+O")
        import_menu.addAction(import_document_action)

        import_txt_action = QAction(self.tr("&Text / Markdown File (TXT, MD)..."), self)
        import_txt_action.triggered.connect(self.import_simple_txt)
        import_menu.addAction(import_txt_action)

        import_menu.addSeparator()

        # Multi-file folder import
        import_folder_action = QAction(self.tr("📁 &Folder (Multiple Files)..."), self)
        import_folder_action.triggered.connect(self.import_folder_multifile)
        import_menu.addAction(import_folder_action)

        import_menu.addSeparator()  # Separate monolingual from bilingual tools

        # memoQ submenu - group all memoQ imports together
        memoq_submenu = import_menu.addMenu(self.tr("&memoQ"))

        import_memoq_action = QAction(self.tr("Bilingual Table (&DOCX)..."), self)
        import_memoq_action.triggered.connect(self.import_memoq_bilingual)
        memoq_submenu.addAction(import_memoq_action)

        import_memoq_rtf_action = QAction(self.tr("Bilingual Table (&RTF)..."), self)
        import_memoq_rtf_action.triggered.connect(self.import_memoq_rtf)
        memoq_submenu.addAction(import_memoq_rtf_action)

        import_memoq_xliff_action = QAction(self.tr("&XLIFF (.mqxliff)..."), self)
        import_memoq_xliff_action.triggered.connect(self.import_memoq_xliff)
        memoq_submenu.addAction(import_memoq_xliff_action)

        # CafeTran submenu - group all CafeTran imports together
        cafetran_submenu = import_menu.addMenu(self.tr("&CafeTran"))
        import_cafetran_action = QAction(self.tr("Bilingual &Table (DOCX)..."), self)
        import_cafetran_action.triggered.connect(self.import_cafetran_bilingual)
        cafetran_submenu.addAction(import_cafetran_action)

        # Trados submenu - group all Trados imports together
        trados_submenu = import_menu.addMenu(self.tr("&Trados Studio"))

        import_trados_bilingual_action = QAction(self.tr("Bilingual &Review (DOCX)..."), self)
        import_trados_bilingual_action.triggered.connect(self.import_trados_bilingual)
        trados_submenu.addAction(import_trados_bilingual_action)

        import_sdlxliff_action = QAction(self.tr("Bilingual &XLIFF (.sdlxliff)..."), self)
        import_sdlxliff_action.triggered.connect(self.import_standalone_sdlxliff)
        trados_submenu.addAction(import_sdlxliff_action)

        import_sdlxliff_folder_action = QAction(self.tr("Bilingual XLIFF &Folder (.sdlxliff)..."), self)
        import_sdlxliff_folder_action.triggered.connect(self.import_sdlxliff_folder)
        trados_submenu.addAction(import_sdlxliff_folder_action)

        import_sdlppx_action = QAction(self.tr("&Package (SDLPPX)..."), self)
        import_sdlppx_action.triggered.connect(self.import_sdlppx_package)
        trados_submenu.addAction(import_sdlppx_action)

        # Phrase (Memsource) submenu
        phrase_submenu = import_menu.addMenu(self.tr("&Phrase (Memsource)"))
        import_phrase_bilingual_action = QAction(self.tr("&Bilingual (DOCX)..."), self)
        import_phrase_bilingual_action.triggered.connect(self.import_phrase_bilingual)
        phrase_submenu.addAction(import_phrase_bilingual_action)
        import_phrase_mxliff_action = QAction(self.tr("&MXLIFF (.mxliff)..."), self)
        import_phrase_mxliff_action.setToolTip(self.tr(
            "Import a Phrase MXLIFF file, with its translations, confirmed and locked segments"))
        import_phrase_mxliff_action.triggered.connect(self.import_phrase_mxliff)
        phrase_submenu.addAction(import_phrase_mxliff_action)

        # Déjà Vu X3 submenu
        dejavu_submenu = import_menu.addMenu(self.tr("&Déjà Vu X3"))
        import_dejavu_action = QAction(self.tr("&Bilingual (RTF)..."), self)
        import_dejavu_action.triggered.connect(self.import_dejavu_bilingual)
        dejavu_submenu.addAction(import_dejavu_action)

        # GNU gettext .po / .pot import (Linux/Django/WordPress l10n)
        import_po_action = QAction(self.tr("&GNU gettext (.po / .pot)..."), self)
        import_po_action.setToolTip(self.tr("Import a GNU gettext message catalogue (Linux/Django/WordPress software localisation)"))
        import_po_action.triggered.connect(self.import_po_file)
        import_menu.addAction(import_po_action)

        import_menu.addSeparator()

        # v1.10.231: the two Supervertaler-native re-importable round-trip
        # formats, grouped to mirror the Export → 🔁 Supervertaler Re-importable
        # submenu. Both update the SAME open project.
        reimport_submenu = import_menu.addMenu(self.tr("🔁 Supervertaler &Re-importable"))

        import_review_table_action = QAction(self.tr("Bilingual &Table (DOCX) - Update Project..."), self)
        import_review_table_action.triggered.connect(self.import_review_table)
        import_review_table_action.setToolTip(self.tr(
            "Re-import an edited Supervertaler bilingual Word table to update this project's "
            "translations and comments."))
        reimport_submenu.addAction(import_review_table_action)

        import_bilingual_md_action = QAction(self.tr("Bilingual Te&xt (AI-friendly) - Update Project..."), self)
        import_bilingual_md_action.triggered.connect(self.import_bilingual_markdown)
        import_bilingual_md_action.setToolTip(self.tr(
            "Re-import an edited [SEGMENT NNNN] text file (with its .svexport.json "
            "sidecar) to update the current project's translations and comments."))
        reimport_submenu.addAction(import_bilingual_md_action)

        # Issue #248: same update, from pasted text instead of a file. The key
        # is only shown here; the QShortcut in setup_global_shortcuts fires it.
        _paste_key = self.shortcut_manager.get_shortcut("file_update_from_pasted_text")
        import_pasted_text_action = QAction(
            self.tr("Bilingual Text (AI-friendly) - Update from &Pasted Text...")
            + (f"\t{format_shortcut_for_display(_paste_key)}" if _paste_key else ""), self)
        import_pasted_text_action.triggered.connect(self.import_bilingual_text_from_paste)
        import_pasted_text_action.setToolTip(self.tr(
            "Paste edited [SEGMENT NNNN] text – for example an AI chat's reply – to update "
            "the current project's translations, comments and statuses. No file needed."))
        reimport_submenu.addAction(import_pasted_text_action)

        # Help link at the foot of the Import submenu. Imports happen straight
        # from a menu (no dialog), so the standard set_help_topic "?" badge
        # has nowhere to live – this entry is the closest substitute.
        import_menu.addSeparator()
        import_help_action = QAction(self.tr("❓ Supported file formats (online help)..."), self)
        import_help_action.setToolTip(self.tr("Open the Supported File Formats reference in your browser"))
        import_help_action.triggered.connect(lambda: open_help(HelpTopics.IMPORT_FORMATS))
        import_menu.addAction(import_help_action)

        export_menu = file_menu.addMenu(self.tr("&Export"))

        # v1.10.53: collapsed the previous two-item split ("Target Only
        # (DOCX)" vs "Original format via Okapi") into one. The dispatcher
        # picks the right back-end based on the project's import format –
        # DOCX/unknown-origin projects go through the DOCX exporter (which
        # is itself Okapi-aware with a python-docx fallback); non-DOCX
        # Okapi-origin projects (IDML, HTML, …) round-trip via the Okapi
        # merge endpoint. Output file extension defaults to the original's.
        export_document_action = QAction(self.tr("&Export Translated Document..."), self)
        export_document_action.setToolTip(self.tr(
            "Export the translation back to a document. Output format is "
            "the same as the original source (DOCX → DOCX, IDML → IDML, "
            "etc.). Uses the Okapi sidecar to preserve formatting."
        ))
        export_document_action.triggered.connect(self.export_document)
        export_menu.addAction(export_document_action)

        export_txt_action = QAction(self.tr("Simple &Text File - Translated (TXT)..."), self)
        export_txt_action.triggered.connect(self.export_simple_txt)
        export_menu.addAction(export_txt_action)

        export_menu.addSeparator()

        # memoQ submenu - group all memoQ exports together
        memoq_export_submenu = export_menu.addMenu(self.tr("&memoQ"))

        export_memoq_action = QAction(self.tr("Bilingual Table - Translated (&DOCX)..."), self)
        export_memoq_action.triggered.connect(self.export_memoq_bilingual)
        memoq_export_submenu.addAction(export_memoq_action)

        export_memoq_rtf_action = QAction(self.tr("Bilingual Table - Translated (&RTF)..."), self)
        export_memoq_rtf_action.triggered.connect(self.export_memoq_rtf)
        memoq_export_submenu.addAction(export_memoq_rtf_action)

        export_memoq_xliff_action = QAction(self.tr("&XLIFF - Translated (.mqxliff)..."), self)
        export_memoq_xliff_action.triggered.connect(self.export_memoq_xliff)
        memoq_export_submenu.addAction(export_memoq_xliff_action)

        # CafeTran submenu - group all CafeTran exports together
        cafetran_export_submenu = export_menu.addMenu(self.tr("&CafeTran"))
        export_cafetran_action = QAction(self.tr("Bilingual &Table - Translated (DOCX)..."), self)
        export_cafetran_action.triggered.connect(self.export_cafetran_bilingual)
        cafetran_export_submenu.addAction(export_cafetran_action)

        # Trados submenu - group all Trados exports together
        trados_export_submenu = export_menu.addMenu(self.tr("&Trados Studio"))

        export_trados_bilingual_action = QAction(self.tr("Bilingual &Review - Translated (DOCX)..."), self)
        export_trados_bilingual_action.triggered.connect(self.export_trados_bilingual)
        trados_export_submenu.addAction(export_trados_bilingual_action)

        export_sdlxliff_action = QAction(self.tr("Bilingual &XLIFF - Translated (.sdlxliff)..."), self)
        export_sdlxliff_action.triggered.connect(self.export_standalone_sdlxliff)
        trados_export_submenu.addAction(export_sdlxliff_action)

        export_sdlrpx_action = QAction(self.tr("Return &Package (SDLRPX)..."), self)
        export_sdlrpx_action.triggered.connect(self.export_sdlrpx_package)
        trados_export_submenu.addAction(export_sdlrpx_action)

        # Phrase (Memsource) submenu
        phrase_export_submenu = export_menu.addMenu(self.tr("&Phrase (Memsource)"))
        export_phrase_bilingual_action = QAction(self.tr("&Bilingual - Translated (DOCX)..."), self)
        export_phrase_bilingual_action.triggered.connect(self.export_phrase_bilingual)
        phrase_export_submenu.addAction(export_phrase_bilingual_action)
        export_phrase_mxliff_action = QAction(self.tr("&MXLIFF - Translated (.mxliff)..."), self)
        export_phrase_mxliff_action.setToolTip(self.tr(
            "Write the translations back into the Phrase MXLIFF, ready to upload to Phrase"))
        export_phrase_mxliff_action.triggered.connect(self.export_phrase_mxliff)
        phrase_export_submenu.addAction(export_phrase_mxliff_action)

        # GNU gettext .po / .pot export (Linux/Django/WordPress l10n)
        export_po_action = QAction(self.tr("&GNU gettext - Translated (.po)..."), self)
        export_po_action.setToolTip(self.tr("Export the project back to a GNU gettext .po catalogue, preserving comments, references and flags"))
        export_po_action.triggered.connect(self.export_po_file)
        export_menu.addAction(export_po_action)

        # Déjà Vu X3 submenu
        dejavu_export_submenu = export_menu.addMenu(self.tr("&Déjà Vu X3"))
        export_dejavu_action = QAction(self.tr("&Bilingual - Translated (RTF)..."), self)
        export_dejavu_action.triggered.connect(self.export_dejavu_bilingual)
        dejavu_export_submenu.addAction(export_dejavu_action)

        export_menu.addSeparator()

        # Multi-file folder export
        export_folder_action = QAction(self.tr("📁 &Folder (Multiple Files)..."), self)
        export_folder_action.triggered.connect(self.export_folder_multifile)
        export_folder_action.setToolTip(self.tr("Export multi-file project to folder with separate files"))
        export_menu.addAction(export_folder_action)

        export_menu.addSeparator()

        # Relocate source folder for multi-file projects
        relocate_source_action = QAction(self.tr("🔗 &Relocate Source Folder..."), self)
        relocate_source_action.triggered.connect(self.relocate_source_folder)
        relocate_source_action.setToolTip(self.tr("Repoint to moved/renamed source folder for multi-file project"))
        export_menu.addAction(relocate_source_action)

        export_menu.addSeparator()

        # v1.10.231: the two Supervertaler-native re-importable round-trip
        # formats, grouped together. Both update the SAME open project on
        # re-import. "Table" = Word DOCX; "Text" = AI-friendly plain text.
        reimport_export_submenu = export_menu.addMenu(self.tr("🔁 Supervertaler &Re-importable"))

        export_reimport_table_action = QAction(self.tr("Bilingual &Table (DOCX)..."), self)
        export_reimport_table_action.triggered.connect(self.export_review_table_with_tags)
        export_reimport_table_action.setToolTip(self.tr(
            "Export a re-importable Word bilingual table (inline tags shown as literal text). "
            "Edit the Target/Comments columns, then re-import to update this project."))
        reimport_export_submenu.addAction(export_reimport_table_action)

        export_reimport_text_action = QAction(self.tr("Bilingual Te&xt (AI-friendly)..."), self)
        export_reimport_text_action.triggered.connect(self.export_bilingual_markdown)
        export_reimport_text_action.setToolTip(self.tr(
            "Export a re-importable [SEGMENT NNNN] text file with a .svexport.json sidecar. "
            "Edit the target lines (or have an LLM do it), then re-import to update this project."))
        reimport_export_submenu.addAction(export_reimport_text_action)

        export_menu.addSeparator()

        export_grid_action = QAction(self.tr("TMX from &Grid (all segments)..."), self)
        export_grid_action.triggered.connect(self.export_tmx_from_grid)
        export_menu.addAction(export_grid_action)

        export_selected_action = QAction(self.tr("TMX from &Selected Segments..."), self)
        export_selected_action.triggered.connect(self.export_tmx_from_selected)
        export_menu.addAction(export_selected_action)

        export_tm_action = QAction(self.tr("TMX from &TM(s) for Current Project..."), self)
        export_tm_action.triggered.connect(self.export_tmx_from_tm_database)
        export_menu.addAction(export_tm_action)

        # Issue #52: every TM and termbase in the database, as open files.
        export_backup_action = QAction(self.tr("📦 &Back Up All TMs && Termbases..."), self)
        export_backup_action.setToolTip(self.tr(
            "Write every translation memory (TMX) and termbase (TSV, including "
            "non-translatables) in the database to a folder of your choice"))
        export_backup_action.triggered.connect(self.backup_all_resources)
        export_menu.addAction(export_backup_action)

        # Help link at the foot of the Export submenu (mirrors Import).
        export_menu.addSeparator()
        export_help_action = QAction(self.tr("❓ Supported file formats (online help)..."), self)
        export_help_action.setToolTip(self.tr("Open the Supported File Formats reference in your browser"))
        export_help_action.triggered.connect(lambda: open_help(HelpTopics.IMPORT_FORMATS))
        export_menu.addAction(export_help_action)

        file_menu.addSeparator()

        # Project Info
        project_info_action = QAction(self.tr("📋 Project &Info..."), self)
        project_info_action.triggered.connect(self.show_project_info_dialog)
        file_menu.addAction(project_info_action)

        file_menu.addSeparator()

        exit_action = QAction(self.tr("E&xit"), self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        # v1.10.278: route Exit / ⌘Q (and, on macOS, the role-merged
        # "Quit Supervertaler" app-menu item this action becomes) through
        # the real-quit path. Connecting to self.close() goes straight into
        # closeEvent with _really_quit still False, so when close_to_tray is
        # enabled the app merely *hides* — leaving macOS users unable to quit
        # at all except by force-killing. _tray_quit sets _really_quit first
        # so closeEvent terminates instead of backgrounding to the tray.
        exit_action.triggered.connect(self._tray_quit)
        file_menu.addAction(exit_action)

        # Edit Menu
        edit_menu = menubar.addMenu(self.tr("&Edit"))

        self.undo_action = QAction(self.tr("&Undo"), self)
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.undo_action.triggered.connect(self.undo_action_handler)
        self.undo_action.setEnabled(False)
        edit_menu.addAction(self.undo_action)

        self.redo_action = QAction(self.tr("&Redo"), self)
        self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        self.redo_action.triggered.connect(self.redo_action_handler)
        self.redo_action.setEnabled(False)
        edit_menu.addAction(self.redo_action)

        edit_menu.addSeparator()

        find_action = QAction(self.tr("&Find..."), self)
        find_action.setShortcut(QKeySequence.StandardKey.Find)
        find_action.triggered.connect(self.show_find_replace_dialog)
        edit_menu.addAction(find_action)

        replace_action = QAction(self.tr("&Replace..."), self)
        replace_action.setShortcut(QKeySequence.StandardKey.Replace)
        replace_action.triggered.connect(self.show_find_replace_dialog)
        edit_menu.addAction(replace_action)
        
        edit_menu.addSeparator()
        
        # v1.10.235: wrap the label in self.tr() so it's both translated at
        # runtime AND picked up by tools/extract_strings.py. Previously the
        # whole label was a bare f-string, so the string never reached the
        # translation template and couldn't be localised (reported by a
        # contributor). The shortcut hint stays appended via the tab trick.
        goto_action = QAction(f"{self.tr('&Go to Segment...')}\t{format_shortcut_for_display('Ctrl+G')}", self)
        goto_action.triggered.connect(self.show_goto_dialog)
        edit_menu.addAction(goto_action)

        edit_menu.addSeparator()

        # 🗑 Delete Segment(s) — removes the selected source segment(s). Only
        # meaningful for paste / plain-text / Start-Empty projects; on DOCX /
        # Okapi / bilingual projects delete_current_segments() explains why it's
        # unavailable rather than corrupting the round-trip export.
        delete_seg_action = QAction(self.tr("🗑 &Delete Segment(s)"), self)
        delete_seg_action.setToolTip(self.tr(
            "Delete the selected segment(s). Available for pasted / plain-text / "
            "Start-Empty projects (not DOCX / Okapi / bilingual round-trip)."))
        delete_seg_action.triggered.connect(self.delete_current_segments)
        edit_menu.addAction(delete_seg_action)

        # ➕ Add Source Text (issue #173) – the counterpart of Delete Segment(s),
        # available on the same projects: paste a few more sentences into an
        # existing project instead of creating and importing a TXT file.
        add_source_action = QAction(self.tr("➕ &Add Source Text..."), self)
        add_source_action.setToolTip(self.tr(
            "Paste or type more source text; it is split into sentences and added "
            "as new segments at the end. Available for pasted / plain-text / "
            "Start-Empty projects (not DOCX / Okapi / bilingual round-trip)."))
        add_source_action.triggered.connect(self.add_source_text_to_project)
        edit_menu.addAction(add_source_action)

        # Translate — first-class top-level menu (v1.10.292). Groups the single
        # "Translate Current Segment" command with all the Batch Translate
        # variants (and Proofread) in one obvious place; this stuff was
        # previously split awkwardly under Edit. Created here, before the Bulk
        # Operations menu, so it lands right after Edit in the menubar. Uses the
        # Alt+R accelerator because Alt+T already belongs to the Tools menu.
        self.translate_menu = menubar.addMenu(self.tr("T&ranslate"))
        translate_top_menu = self.translate_menu

        translate_action = QAction(self.tr("🌐 &Translate Current Segment"), self)
        translate_action.setShortcut("Ctrl+T")
        translate_action.triggered.connect(self.translate_current_segment)
        translate_top_menu.addAction(translate_action)

        fuzzy_fixer_action = QAction(self.tr("🔧 &Fuzzy Fix Current Segment"), self)
        fuzzy_fixer_action.setShortcut("Ctrl+Alt+F")
        fuzzy_fixer_action.setToolTip(self.tr("Ask the AI to adapt the selected fuzzy TM match to the current source"))
        fuzzy_fixer_action.triggered.connect(self.fuzzy_fix_current_segment)
        translate_top_menu.addAction(fuzzy_fixer_action)

        autotagger_action = QAction(self.tr("🏷️ Auto-&tag Current Segment"), self)
        autotagger_action.setShortcut("Ctrl+Alt+G")
        autotagger_action.setToolTip(self.tr("Ask the AI to place the source segment's inline tags into the current translation"))
        autotagger_action.triggered.connect(self.autotag_current_segment)
        translate_top_menu.addAction(autotagger_action)

        translate_top_menu.addSeparator()

        translate_menu = translate_top_menu.addMenu(self.tr("Batch &Translate"))

        translate_selected_not_started_action = QAction(self.tr("Translate selected not-started segments"), self)
        translate_selected_not_started_action.triggered.connect(
            lambda checked=False: self.translate_multiple_segments("selected_not_started")
        )
        translate_menu.addAction(translate_selected_not_started_action)

        translate_all_not_started_action = QAction(self.tr("Translate all not-started segments"), self)
        translate_all_not_started_action.triggered.connect(
            lambda checked=False: self.translate_multiple_segments("all_not_started")
        )
        translate_menu.addAction(translate_all_not_started_action)

        translate_all_pretranslated_action = QAction(self.tr("Translate all pre-translated segments"), self)
        translate_all_pretranslated_action.triggered.connect(
            lambda checked=False: self.translate_multiple_segments("all_pretranslated")
        )
        translate_menu.addAction(translate_all_pretranslated_action)

        translate_pending_action = QAction(self.tr("Translate all not-started && pre-translated"), self)
        translate_pending_action.setShortcut("Ctrl+Shift+T")
        translate_pending_action.triggered.connect(
            lambda checked=False: self.translate_multiple_segments("all_not_started_pretranslated")
        )
        translate_menu.addAction(translate_pending_action)

        translate_translatable_action = QAction(self.tr("Translate all translatable segments"), self)
        translate_translatable_action.triggered.connect(
            lambda checked=False: self.translate_multiple_segments("all_translatable")
        )
        translate_menu.addAction(translate_translatable_action)

        translate_all_segments_action = QAction(self.tr("Translate all segments (all statuses)"), self)
        translate_all_segments_action.triggered.connect(
            lambda checked=False: self.translate_multiple_segments("all_segments")
        )
        translate_menu.addAction(translate_all_segments_action)

        translate_menu.addSeparator()

        # NEW: Translate only empty segments (for re-running failed batches)
        translate_empty_action = QAction(self.tr("Translate all empty segments"), self)
        translate_empty_action.setToolTip(self.tr("Translate segments with empty target (useful after partial batch translation)"))
        translate_empty_action.triggered.connect(
            lambda checked=False: self.translate_multiple_segments("all_empty")
        )
        translate_menu.addAction(translate_empty_action)

        translate_filtered_action = QAction(self.tr("Translate all filtered segments"), self)
        translate_filtered_action.setToolTip(self.tr("Translate only segments currently visible after filtering"))
        translate_filtered_action.triggered.connect(
            lambda checked=False: self.translate_multiple_segments("filtered_segments")
        )
        translate_menu.addAction(translate_filtered_action)

        # (Proofreading moved to its own top-level QA menu in v1.10.327 —
        # issue #234. See the QA menu built just before the View menu.)

        # Bulk Operations — promoted to a top-level menubar menu in v1.10.145
        # (was a submenu under Edit). It's a sizeable, distinct category of
        # project-wide segment operations, so it gets first-class placement
        # between Edit and View for discoverability (e.g. "Update Active TM(s)"
        # was previously three levels deep). Created here (before the View menu
        # is added) so it lands right after Edit in the menubar.
        self.bulk_menu = menubar.addMenu(self.tr("&Bulk Operations"))
        self.bulk_menu.aboutToShow.connect(self._update_bulk_menu_label)
        bulk_menu = self.bulk_menu

        confirm_selected_action = QAction(self.tr("✅ &Confirm Selected Segments"), self)
        confirm_selected_action.setShortcut("Ctrl+Shift+Return")
        confirm_selected_action.setToolTip(
            f"Confirm all selected segments ({format_shortcut_for_display('Ctrl+Shift+Enter')})"
        )
        confirm_selected_action.triggered.connect(self.confirm_selected_segments_from_menu)
        bulk_menu.addAction(confirm_selected_action)

        # Change Status submenu
        status_submenu = bulk_menu.addMenu(self.tr("🏷️ Change &Status"))
        user_statuses = [
            ("not_started", self.tr("❌ &Not started")),
            ("pretranslated", self.tr("🤖 &Pre-translated")),
            ("draft", self.tr("✏️ &Draft")),
            ("confirmed", self.tr("✔ &Confirmed")),
            ("proofread", self.tr("🟪 Proo&fread")),
            ("approved", self.tr("⭐ &Approved")),
            ("rejected", self.tr("🚫 Re&jected")),
        ]
        for status_key, label in user_statuses:
            action = QAction(label, self)
            action.triggered.connect(lambda checked, s=status_key: self.change_status_selected(s, from_menu=True))
            status_submenu.addAction(action)

        clear_translations_action = QAction(self.tr("🗑️ &Clear Translations"), self)
        clear_translations_action.setToolTip(self.tr("Clear translations for selected segments"))
        clear_translations_action.triggered.connect(self.clear_selected_translations_from_menu)
        bulk_menu.addAction(clear_translations_action)

        copy_source_to_target_action = QAction(self.tr("📋 Copy &Source to Target"), self)
        copy_source_to_target_action.setToolTip(self.tr("Copy source text to target for selected/filtered segments"))
        copy_source_to_target_action.triggered.connect(self.copy_source_to_target_bulk)
        bulk_menu.addAction(copy_source_to_target_action)

        autotag_bulk_action = QAction(self.tr("🏷️ &Auto-tag Segments (fix inline tags with AI)…"), self)
        autotag_bulk_action.setToolTip(self.tr("Use the AI to place each segment's source inline tags into its existing translation (selected segments, or all if none selected)"))
        autotag_bulk_action.triggered.connect(self.autotag_segments_bulk)
        bulk_menu.addAction(autotag_bulk_action)

        copy_nontrans_action = QAction(self.tr("🔢 Copy Source to Target (&No Letters)"), self)
        copy_nontrans_action.setToolTip(self.tr("Copy source to target for segments containing no letters (numbers, codes, punctuation only) and mark as Translated"))
        copy_nontrans_action.triggered.connect(self.copy_source_to_target_non_translatable_bulk)
        bulk_menu.addAction(copy_nontrans_action)

        send_to_tm_action = QAction(self.tr("💾 &Update Active TM(s)..."), self)
        send_to_tm_action.setToolTip(self.tr(
            "Send/update confirmed (and other selected-status) segments into a "
            "writable Translation Memory. With overwrite on, same-source entries "
            "are replaced so the TM keeps only the latest translation. "
            "(Formerly 'Send Segments to TM'.)"
        ))
        send_to_tm_action.triggered.connect(self.send_segments_to_tm_dialog)
        bulk_menu.addAction(send_to_tm_action)

        bulk_menu.addSeparator()

        pseudo_translate_action = QAction(self.tr("🧪 &Pseudo-translate (Export Test)..."), self)
        pseudo_translate_action.setToolTip(self.tr(
            "Fill targets with stress-tested placeholder text (length-expanded, "
            "accented, tag-preserving) so you can export and check the document "
            "for layout/encoding problems before translating. Reversible via Undo."))
        pseudo_translate_action.triggered.connect(self.pseudo_translate_bulk)
        bulk_menu.addAction(pseudo_translate_action)

        clean_tags_action = QAction(self.tr("🧹 Clean &Tags..."), self)
        clean_tags_action.setToolTip(self.tr("Remove formatting tags from selected segments"))
        clean_tags_action.triggered.connect(self.show_clean_tags_dialog)
        bulk_menu.addAction(clean_tags_action)

        edit_menu.addSeparator()

        # Superlookup
        superlookup_action = QAction(self.tr("🔍 &SuperLookup..."), self)
        if IS_MACOS:
            superlookup_action.setShortcut("Meta+Ctrl+L")  # Ctrl+Cmd+L on Mac
        else:
            superlookup_action.setShortcut("Ctrl+Alt+L")
        # Tab indices: Grid=0, Project resources=1, Tools=2, Settings=3
        superlookup_action.triggered.connect(lambda: self._go_to_superlookup() if hasattr(self, 'main_tabs') else None)  # Navigate to Superlookup
        edit_menu.addAction(superlookup_action)
        
        # QA Menu — quality assurance. Today it holds AI Proofreading; it's
        # designed as the umbrella for future traditional CAT-tool QA checks
        # (tags, numbers, terminology, consistency) and spelling/grammar
        # (LanguageTool). (issue #234)
        self.qa_menu = menubar.addMenu(self.tr("&QA"))

        # Saved find-only checks – a regex linter for translations (#209)
        qa_checks_action = QAction(self.tr("🔎 Run QA &Checks..."), self)
        qa_checks_action.setToolTip(self.tr(
            "Run saved find-only checks (double spaces, doubled words, your own "
            "patterns) over the project and list what they find"))
        qa_checks_action.triggered.connect(self.show_qa_checks_dialog)
        self.qa_menu.addAction(qa_checks_action)

        # Grammar / spelling / style through a LanguageTool server (#233)
        languagetool_action = QAction(self.tr("📝 Check with &LanguageTool..."), self)
        languagetool_action.setToolTip(self.tr(
            "Check the target text for grammar, spelling and style with LanguageTool "
            "(the free public API or your own LanguageTool server)"))
        languagetool_action.triggered.connect(self.show_languagetool_dialog)
        self.qa_menu.addAction(languagetool_action)

        proofreading_submenu = self.qa_menu.addMenu(self.tr("✅ &Proofreading"))

        proofread_action = QAction(self.tr("✅ &Proofread Translation..."), self)
        proofread_action.setToolTip(self.tr("Use AI to proofread and verify translation quality"))
        proofread_action.triggered.connect(self.show_proofread_dialog)
        proofreading_submenu.addAction(proofread_action)

        # (The separate "Proofreading Results" pop-up window was removed in
        # v1.10.327 — issue #234 follow-up. It's superseded by the Proofreading
        # comments tab, which lists every result with working navigation and
        # per-entry delete. The old dialog's row double-click navigation was
        # also broken. A proper "export proofreading report" can live here later.)

        proofreading_submenu.addSeparator()

        delete_all_pc_action = QAction(self.tr("🗑️ &Delete All Proofreading Comments"), self)
        delete_all_pc_action.setToolTip(self.tr("Remove every AI proofreading comment from the project"))
        delete_all_pc_action.triggered.connect(self.delete_all_proofreading_comments)
        proofreading_submenu.addAction(delete_all_pc_action)

        # View Menu
        view_menu = menubar.addMenu(self.tr("&View"))

        # Navigation submenu
        nav_menu = view_menu.addMenu(self.tr("📑 &Navigate To"))

        # v1.10.161: navigation actions now look the target tab up by
        # label substring instead of hard-coding the index. Previously the
        # five nav actions had five different stale-index time bombs;
        # the Settings one had already exploded (was landing on
        # SuperLookup since the v1.10 tab insertions).
        go_editor_action = QAction(self.tr("📝 &Grid"), self)
        go_editor_action.triggered.connect(lambda: self._switch_main_tab("Editor"))
        nav_menu.addAction(go_editor_action)

        go_tms_action = QAction(self.tr("💾 &TMs"), self)
        go_tms_action.triggered.connect(lambda: self._switch_main_tab("TMs"))
        nav_menu.addAction(go_tms_action)

        go_termbases_action = QAction(self.tr("🏷️ Term&bases"), self)
        go_termbases_action.triggered.connect(lambda: self._switch_main_tab("Termbases"))
        nav_menu.addAction(go_termbases_action)

        go_prompt_manager_action = QAction(self.tr("⚡ &QuickLauncher"), self)
        go_prompt_manager_action.triggered.connect(lambda: self._switch_main_tab("AI"))
        nav_menu.addAction(go_prompt_manager_action)

        # Tools-tab navigation entry retired in v1.9.467: PDF Rescue and TMX
        # Editor now open in their own windows from the Tools menu instead.

        go_settings_action = QAction(self.tr("⚙️ &Settings"), self)
        # v1.10.233: on macOS Qt auto-detects any "Settings"-titled action,
        # promotes it to the application-menu Preferences slot, and binds it to
        # the system ⌘, — which then steals ⌘, from "Insert next tag" (Ctrl+,
        # → Cmd+, on Mac). Force NoRole so the action stays an ordinary menu
        # item and ⌘, flows through to the editor. Reported by a user.
        go_settings_action.setMenuRole(QAction.MenuRole.NoRole)
        # v1.10.161: label-based lookup (was setCurrentIndex(4), which started
        # landing on SuperLookup when SuperLookup/Clipboard/Voice were
        # inserted between AI and Settings).
        go_settings_action.triggered.connect(lambda: self._switch_main_tab("Settings"))
        nav_menu.addAction(go_settings_action)

        view_menu.addSeparator()

        # Grid Text section
        grid_zoom_menu = view_menu.addMenu(self.tr("📊 &Grid Text Zoom"))

        grid_zoom_in = QAction(self.tr("Grid Zoom &In"), self)
        grid_zoom_in.setShortcut("Ctrl+=")
        grid_zoom_in.triggered.connect(self.zoom_in)
        grid_zoom_menu.addAction(grid_zoom_in)

        grid_zoom_out = QAction(self.tr("Grid Zoom &Out"), self)
        grid_zoom_out.setShortcut(QKeySequence.StandardKey.ZoomOut)
        grid_zoom_out.triggered.connect(self.zoom_out)
        grid_zoom_menu.addAction(grid_zoom_out)

        # Note: Do not add separate Ctrl++/Ctrl+- actions here.
        # QKeySequence.StandardKey.ZoomIn/ZoomOut already maps to these keys on many layouts,
        # and duplicates cause: "QAction::event: Ambiguous shortcut overload".
        grid_zoom_menu.addSeparator()

        grid_font_family_menu = grid_zoom_menu.addMenu(self.tr("Grid Font &Family"))
        font_families = ["Calibri", "Segoe UI", "Arial", "Consolas", "Verdana",
                        "Times New Roman", "Georgia", "Courier New"]
        for font_name in font_families:
            font_action = QAction(font_name, self)
            font_action.triggered.connect(lambda checked, f=font_name: self.set_font_family(f))
            grid_font_family_menu.addAction(font_action)

        view_menu.addSeparator()

        # Match Panel zoom section
        match_panel_zoom_menu = view_menu.addMenu(self.tr("🔍 &Match Panel"))

        match_panel_zoom_in_action = QAction(self.tr("Match Panel Zoom &In"), self)
        match_panel_zoom_in_action.setShortcut("Ctrl+Alt+=")
        match_panel_zoom_in_action.triggered.connect(self.match_panel_zoom_in)
        match_panel_zoom_menu.addAction(match_panel_zoom_in_action)

        match_panel_zoom_out_action = QAction(self.tr("Match Panel Zoom &Out"), self)
        match_panel_zoom_out_action.setShortcut("Ctrl+Alt+-")
        match_panel_zoom_out_action.triggered.connect(self.match_panel_zoom_out)
        match_panel_zoom_menu.addAction(match_panel_zoom_out_action)

        match_panel_zoom_reset_action = QAction(self.tr("Match Panel Zoom &Reset"), self)
        match_panel_zoom_reset_action.triggered.connect(self.match_panel_zoom_reset)
        match_panel_zoom_menu.addAction(match_panel_zoom_reset_action)

        match_panel_zoom_menu.addSeparator()

        # v1.9.307: TM pane layout toggle (horizontal ↔ vertical)
        self.match_panel_vertical_action = QAction(self.tr("Stack TM Panes &Vertically"), self)
        self.match_panel_vertical_action.setCheckable(True)
        self.match_panel_vertical_action.setChecked(getattr(self, 'match_panel_tm_vertical', True))
        self.match_panel_vertical_action.triggered.connect(lambda _checked: self._toggle_match_panel_tm_layout())
        match_panel_zoom_menu.addAction(self.match_panel_vertical_action)

        view_menu.addSeparator()

        # Under-grid panel placement. The dock holds the TermLens and
        # QuickTrans tabs; this submenu controls only WHERE the dock sits
        # (the tab bar in the dock chooses which of the two is shown).
        from PyQt6.QtGui import QActionGroup
        underdock_menu = view_menu.addMenu(self.tr("📑 TermLens / QuickTrans panel"))
        self.underdock_action_group = QActionGroup(self)
        self.underdock_action_group.setExclusive(True)

        self.underdock_hide_action = QAction(self.tr("Hide"), self)
        self.underdock_hide_action.setCheckable(True)
        self.underdock_hide_action.setToolTip(self.tr("Hide the under-grid panel"))
        self.underdock_hide_action.triggered.connect(lambda: self.set_termlens_position(None))

        self.underdock_above_action = QAction(self.tr("Show above grid"), self)
        self.underdock_above_action.setCheckable(True)
        self.underdock_above_action.setToolTip(self.tr("Dock the panel above the translation grid"))
        self.underdock_above_action.triggered.connect(lambda: self.set_termlens_position('above'))

        self.underdock_below_action = QAction(self.tr("Show below grid"), self)
        self.underdock_below_action.setCheckable(True)
        self.underdock_below_action.setToolTip(self.tr("Dock the panel below the translation grid"))
        self.underdock_below_action.triggered.connect(lambda: self.set_termlens_position('below'))

        for _a in (self.underdock_hide_action, self.underdock_above_action, self.underdock_below_action):
            self.underdock_action_group.addAction(_a)
            underdock_menu.addAction(_a)
        self.underdock_hide_action.setChecked(True)

        view_menu.addSeparator()

        auto_resize_action = QAction(self.tr("📐 &Auto-Resize Rows"), self)
        auto_resize_action.triggered.connect(self.auto_resize_rows)
        auto_resize_action.setToolTip(self.tr("Automatically resize all rows to fit content"))
        view_menu.addAction(auto_resize_action)

        view_menu.addSeparator()

        # (Proofreading Results moved to the QA menu in v1.10.327 — issue #234.)

        theme_action = QAction(self.tr("🎨 &Theme Editor..."), self)
        theme_action.triggered.connect(self.show_theme_editor)
        view_menu.addAction(theme_action)

        # Tools Menu
        tools_menu = menubar.addMenu(self.tr("&Tools"))

        # Tools (open in their own windows; no embedded Tools tab anymore)
        pdf_rescue_action = QAction(self.tr("📄 &PDF Rescue..."), self)
        pdf_rescue_action.triggered.connect(self.open_pdf_rescue_window)
        tools_menu.addAction(pdf_rescue_action)

        superlookup_action = QAction(f"🔍 Super&lookup ({format_shortcut_for_display('Ctrl+K')})...", self)
        # Note: Actual Ctrl+K shortcut handled by QShortcut in setup_global_shortcuts()
        # which calls show_concordance_search() for proper selection capture
        superlookup_action.triggered.connect(self.show_concordance_search)
        tools_menu.addAction(superlookup_action)

        superbrowser_action = QAction(self.tr("🌐 Super&browser..."), self)
        superbrowser_action.setToolTip(self.tr(
            "ChatGPT, Claude, and Gemini side by side in one window, with "
            "persistent logins – compare answers without switching tabs"))
        superbrowser_action.triggered.connect(self.open_superbrowser_window)
        tools_menu.addAction(superbrowser_action)

        tmx_editor_action = QAction(self.tr("✏️ T&MX Editor..."), self)
        tmx_editor_action.triggered.connect(self.open_tmx_editor_window)
        tools_menu.addAction(tmx_editor_action)

        statistics_action = QAction(self.tr("📊 &Statistics (Analyse Against TM)..."), self)
        statistics_action.setToolTip(self.tr(
            "Analyse the current project against your translation memories and "
            "see a Trados/memoQ-style match breakdown (repetitions, 100%, fuzzy "
            "bands, no-match) to estimate how much work the document represents."))
        statistics_action.triggered.connect(self.show_statistics_dialog)
        tools_menu.addAction(statistics_action)

        quick_count_action = QAction(self.tr("⚡ &Quick Count (pick files, no project)..."), self)
        quick_count_action.setToolTip(self.tr(
            "Quickly count one or more files (DOCX, Trados .sdlxliff, memoQ .mqxliff, "
            "and other formats) against your translation memories — without creating "
            "a project. For when you just want a fast word count / TM analysis."))
        quick_count_action.triggered.connect(self.show_quick_statistics_dialog)
        tools_menu.addAction(quick_count_action)

        tools_menu.addSeparator()

        image_extractor_action = QAction(self.tr("🖼️ &Image Extractor (Superimage)..."), self)
        image_extractor_action.triggered.connect(self.show_image_extractor_from_tools)
        image_extractor_action.setToolTip(self.tr("Extract images from DOCX files"))
        tools_menu.addAction(image_extractor_action)

        # v1.10.206: moved from Ctrl+Shift+P → Alt+S to free Ctrl+Shift+P for
        # TermPicker (matching the Trados plugin's "P = Picker" convention).
        # Alt+S is collision-free against top-level menu mnemonics (&Project,
        # &Edit, &Bulk, &View, &Tools, &Help — none owns S) and other in-app
        # Alt+letter shortcuts (currently only Alt+D and Alt+K are bound).
        scratchpad_action = QAction(self.tr("📝 Scratch&pad..."), self)
        scratchpad_action.setShortcut("Alt+S")
        scratchpad_action.triggered.connect(self.show_scratchpad)
        scratchpad_action.setToolTip(self.tr("Private notes for this project (never exported to CAT tools)"))
        tools_menu.addAction(scratchpad_action)

        log_window_action = QAction(self.tr("📋 &Log Window..."), self)
        log_window_action.triggered.connect(self.detach_log_window)
        log_window_action.setToolTip(self.tr("Open the session log in a separate window that can be moved to another screen"))
        tools_menu.addAction(log_window_action)

        usage_report_action = QAction(self.tr("💰 &Token Usage && Costs..."), self)
        usage_report_action.setToolTip(self.tr("Token usage and cost totals from the usage log, with CSV/Excel export"))

        def _open_usage_report():
            try:
                from modules.usage_report_dialog import UsageReportDialog
                budget = float(getattr(self, 'monthly_budget_usd', 0.0) or 0.0)
                currency, rate = self._cost_display_settings()

                def _save_currency(new_currency, new_rate):
                    general = self.load_general_settings()
                    general['cost_currency'] = new_currency
                    general['usd_to_eur_rate'] = new_rate
                    self.save_general_settings(general)

                UsageReportDialog(self, budget=budget, currency=currency, rate=rate,
                                  on_currency_changed=_save_currency).exec()
            except Exception as e:
                print(f"[Usage report] failed to open: {e}")

        usage_report_action.triggered.connect(lambda: _open_usage_report())
        tools_menu.addAction(usage_report_action)

        tools_menu.addSeparator()

        settings_action = QAction(self.tr("&Settings..."), self)
        # v1.10.233: keep ⌘, free for "Insert next tag" on macOS (see note on
        # go_settings_action) — stop Qt promoting this to the Preferences slot.
        settings_action.setMenuRole(QAction.MenuRole.NoRole)
        settings_action.triggered.connect(lambda: self._go_to_settings_tab())
        tools_menu.addAction(settings_action)

        # Help Menu
        help_menu = menubar.addMenu(self.tr("&Help"))

        # Documentation links (GitHub URLs for universal access)
        # Removed internal manual link – documentation migrated to GitBook

        # Single Help-menu entry → the Workbench documentation home. The old
        # "Context Help" entry was removed (it merely opened the docs home, not
        # an actual context page, and duplicated this item). Context-sensitive
        # help is unaffected: the global F1 event filter (install_help_system)
        # still opens the page for whichever panel is focused.
        superdocs_action = QAction(self.tr("Supervertaler Workbench Help"), self)
        superdocs_action.setToolTip(self.tr("Open the Workbench documentation in your browser (press F1 for help on the current panel)"))
        superdocs_action.triggered.connect(lambda: self._open_url("https://docs.supervertaler.com/workbench/"))
        help_menu.addAction(superdocs_action)

        setup_wizard_action = QAction(self.tr("🚀 Setup Wizard..."), self)
        setup_wizard_action.setToolTip(self.tr("Run the initial setup wizard (data folder location, features overview)"))
        setup_wizard_action.triggered.connect(lambda: self._show_setup_wizard(is_first_run=False))
        help_menu.addAction(setup_wizard_action)

        # A ready-made project to try things out on (#147)
        sample_project_action = QAction(self.tr("🎓 Open Sample Project"), self)
        sample_project_action.setToolTip(self.tr(
            "Open a small English → Dutch project with a sample TM and glossary, to see "
            "TermLens, TM matches and the other panels in action"))
        sample_project_action.triggered.connect(self.open_sample_project)
        help_menu.addAction(sample_project_action)

        help_menu.addSeparator()

        shortcuts_action = QAction(self.tr("⌨️ Keyboard Shortcuts"), self)
        shortcuts_action.triggered.connect(lambda: open_help(HelpTopics.KEYBOARD_SHORTCUTS))
        help_menu.addAction(shortcuts_action)

        changelog_action = QAction(self.tr("📝 Changelog"), self)
        changelog_action.triggered.connect(lambda: self._open_url("https://github.com/Supervertaler/Supervertaler-Workbench/blob/main/CHANGELOG.md"))
        help_menu.addAction(changelog_action)

        update_check_action = QAction(self.tr("🔄 Check for Updates..."), self)
        update_check_action.setToolTip(self.tr("Check whether you are running the latest Supervertaler release"))
        update_check_action.triggered.connect(self.check_for_updates)
        help_menu.addAction(update_check_action)

        copy_version_info_action = QAction(self.tr("📋 Copy Version Info"), self)
        copy_version_info_action.setToolTip(self.tr("Copy version and system info to clipboard (useful for support)"))
        copy_version_info_action.triggered.connect(self.copy_version_info_to_clipboard)
        help_menu.addAction(copy_version_info_action)

        # Diagnostic log – so users can send us error output even when they
        # launched Supervertaler without a terminal window (silent / .exe /
        # gui-scripts entry point).
        open_log_action = QAction(self.tr("📄 Open Diagnostic Log"), self)
        open_log_action.setToolTip(self.tr(
            "Open the log file containing startup output and errors – useful when reporting issues"
        ))
        open_log_action.triggered.connect(self.open_diagnostic_log)
        help_menu.addAction(open_log_action)

        open_log_folder_action = QAction(self.tr("📁 Open Log Folder"), self)
        open_log_folder_action.setToolTip(self.tr("Reveal the folder that contains the diagnostic log files"))
        open_log_folder_action.triggered.connect(self.open_diagnostic_log_folder)
        help_menu.addAction(open_log_folder_action)

        help_menu.addSeparator()

        forum_action = QAction(self.tr("💬 Community Discussions"), self)
        forum_action.setToolTip(self.tr("Ask questions, request features and share tips in GitHub Discussions"))
        forum_action.triggered.connect(lambda: self._open_url("https://github.com/orgs/Supervertaler/discussions"))
        help_menu.addAction(forum_action)

        github_action = QAction(self.tr("🔗 GitHub Repository"), self)
        github_action.triggered.connect(lambda: self._open_url("https://github.com/Supervertaler/Supervertaler-Workbench"))
        help_menu.addAction(github_action)

        help_menu.addSeparator()

        # AutoHotkey setup (Windows only)
        if os.name == 'nt':
            ahk_setup_action = QAction(self.tr("⌨️ Setup AutoHotkey (Global Hotkey)"), self)
            ahk_setup_action.setToolTip(
                f"Configure AutoHotkey for SuperLookup global hotkey ({format_shortcut_for_display('Ctrl+Alt+L')})"
            )
            ahk_setup_action.triggered.connect(self._show_ahk_setup_from_menu)
            help_menu.addAction(ahk_setup_action)
            help_menu.addSeparator()

        about_action = QAction(self.tr("ℹ️ About"), self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def create_quick_access_toolbar(self):
        """Create Quick Access Toolbar above ribbon"""
        from PyQt6.QtWidgets import QToolBar, QWidget, QHBoxLayout
        from PyQt6.QtCore import QSize
        
        qat = QToolBar("Quick Access")
        qat.setMovable(False)
        qat.setFloatable(False)
        qat.setIconSize(QSize(20, 20))
        qat.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        qat.setMaximumHeight(28)
        qat.setMinimumWidth(170)  # Fixed width to align ribbon tabs
        qat.setMaximumWidth(170)
        
        # Styling for compact appearance
        qat.setStyleSheet("""
            QToolBar {
                background: transparent;
                border: none;
                spacing: 2px;
                padding: 2px;
            }
            QToolButton {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 3px;
                padding: 2px;
                margin: 0px;
                font-size: 14pt;
            }
            QToolButton:hover {
                background: rgba(255, 255, 255, 0.1);
                border: 1px solid rgba(255, 255, 255, 0.2);
            }
            QToolButton:pressed {
                background: rgba(0, 0, 0, 0.1);
            }
        """)
        
        # Empty toolbar - all buttons removed, minimize moved to ribbon
        # Add empty separator just to maintain spacing
        qat.addSeparator()
        
        return qat
    
    def create_ribbon(self):
        """
        Create modern ribbon interface - DISABLED
        Ribbon functionality has been moved to traditional menu bar.
        This code is kept for potential future use.
        """
        # Ribbon removed - all actions are now in the menu bar
        # Keeping this method for potential future re-enablement
        return
        
        # from modules.ribbon_widget import RibbonWidget, RibbonTab, RibbonGroup, RibbonButton
        # 
        # # Create ribbon widget
        # self.ribbon = RibbonWidget(self)
        # 
        # # Connect ribbon actions to methods
        # self.ribbon.action_triggered.connect(self.handle_ribbon_action)
        
        # Ribbon code commented out - all functionality moved to menu bar
        # HOME TAB - Project management and navigation
        # home_tab = RibbonTab()
        # project_group = RibbonGroup("Project")
        # project_group.add_button(self.ribbon.create_button("New", "📄", "new", "New project (Ctrl+N)"))
        # ... (all ribbon tab creation code commented out)
        # All actions are now available in the traditional menu bar
    
    def create_ribbon_toolbar(self):
        """Create a toolbar to hold the ribbon - DISABLED"""
        # Ribbon removed - using traditional menu bar instead
        # This method kept for potential future use
        return None
    
    def toggle_ribbon_minimized(self, minimized: bool):
        """Toggle ribbon between full and minimized (tabs-only) mode - DISABLED"""
        # Ribbon removed - no action needed
        self.ribbon_minimized = minimized  # Store state for backwards compatibility
        pass
    
    def show_ribbon_temporarily(self, index: int):
        """Show ribbon temporarily when tab clicked in minimized mode - DISABLED"""
        # Ribbon removed - no action needed
        pass
    
    def on_main_tab_changed(self, index: int):
        """Handle main tab change"""
        # Ribbon removed - menu bar remains constant across all tabs
        # No context switching needed
        pass
    
    def create_main_layout(self):
        """Create main application layout with all-tab interface"""
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(5, 0, 5, 5)  # Reduced top margin to minimize space below menu bar
        
        # ===== SIMPLIFIED TAB-BASED UI =====
        # Single tab widget with all functionality
        from modules.unified_prompt_manager_qt import UnifiedPromptManagerQt
        
        # Create main tab widget
        self.main_tabs = QTabWidget()
        self.main_tabs.tabBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.main_tabs.tabBar().setDrawBase(False)
        self.main_tabs.tabBar().setExpanding(False)
        self.main_tabs.setStyleSheet("""
            QTabBar::tab { padding: 8px 15px; outline: 0; }
            QTabBar::tab:focus { outline: none; }
            QTabBar::tab:selected { 
                border-bottom: 1px solid #2196F3; 
                background-color: rgba(33, 150, 243, 0.08);
            }
        """)
        
        # ===== 1. GRID TAB =====
        # Contains the translation grid
        grid_widget = self.create_grid_view_widget_for_home()
        self.main_tabs.addTab(grid_widget, "📝 Editor")
        
        # ===== 2. TMs TAB =====
        # Promoted to a top-level tab in v1.9.422 (was previously nested
        # under "Resources" together with Termbases). Exposing TMs and
        # Termbases as siblings of Editor / AI / Tools / Settings makes
        # them reachable in one click instead of two and gives each one
        # a permanent slot on the tab bar.
        tm_tab_top = self.create_translation_memories_tab()
        set_help_topic(tm_tab_top, HelpTopics.TM_BASICS)
        self.main_tabs.addTab(tm_tab_top, "💾 TMs")

        # ===== 3. TERMBASES TAB =====
        # Promoted to a top-level tab in v1.9.422 (see TMs comment above).
        termbase_tab_top = self.create_termbases_tab()
        set_help_topic(termbase_tab_top, HelpTopics.GLOSSARY_BASICS)
        self.main_tabs.addTab(termbase_tab_top, "🏷️ Termbases")

        # Backward-compat: a few helpers (Sidekick "Go to glossary",
        # external nav code) still expect self.resources_tabs to exist.
        # The old wrapper QTabWidget is gone, so point this attribute at
        # main_tabs itself; callers that did
        # resources_tabs.setCurrentIndex(<index of Termbases inside the
        # old wrapper>) need updating to navigate via main_tabs directly.
        self.resources_tabs = self.main_tabs
        
        # ===== 3. AI TAB =====
        # Houses everything that exists purely for the AI translation flow:
        # the Prompt Manager, Variables reference, Assistant chat view, and
        # Image Context (figures the AI sees with each segment). Image
        # Context lived under Resources before v1.9.395 – moved here in
        # v1.9.395 because it isn't a translation aid like TMs/termbases;
        # it's an AI input and only the AI flow ever reads it.
        # UnifiedPromptManagerQt builds its own inner QTabWidget with
        # Prompt Manager / Variables / Assistant; rather than wrapping that
        # in another outer tab strip (which produced an awkward
        # double-"Prompt Manager" label in v1.9.395), we add the Image
        # Context pane as a fourth peer tab on the inner strip directly.
        from modules.unified_prompt_manager_qt import UnifiedPromptManagerQt
        prompt_widget = QWidget()
        self.prompt_manager_qt = UnifiedPromptManagerQt(self, standalone=False)
        self.prompt_manager_qt.create_tab(prompt_widget)
        set_help_topic(prompt_widget, HelpTopics.AI_PROMPT_MANAGER)

        # v1.10.176: Image Context is no longer a separate AI sub-tab.
        # It's now embedded inside the Prompt Manager tab as page 1 of
        # the right-hand QStackedWidget (page 0 is the Prompt Editor).
        # Section 4 on the left has an "Open ▸" button that switches
        # the right panel to the Image Context viewer.
        if hasattr(self.prompt_manager_qt, 'sub_tabs'):
            self.ai_subtabs = self.prompt_manager_qt.sub_tabs
            image_context_widget = self.create_reference_images_tab()
            if hasattr(self.prompt_manager_qt, 'set_image_context_widget'):
                self.prompt_manager_qt.set_image_context_widget(image_context_widget)
        else:
            self.ai_subtabs = None

        self.main_tabs.addTab(prompt_widget, "✨ AI")

        # Add AI Assistant to the right panel at position 1 (right after Match Panel)
        if hasattr(self, 'right_tabs') and hasattr(self.prompt_manager_qt, 'assistant_tab'):
            self.right_tabs.insertTab(1, self.prompt_manager_qt.assistant_tab, "💬 Chat")
            self._assistant_tab_index = 1
            # Bump stored indices that were shifted by the insert
            if hasattr(self, '_preview_tab_index'):
                self._preview_tab_index += 1

        # Sidekick (Floating Assistant) was retired in v1.10.4 and its
        # tabs moved into Workbench itself. The Clipboard tab was
        # removed again in v1.10.374: the AutoHotkey Supervertaler
        # Sidekick is the clipboard tool.
        #   Chat        → right_tabs "💬 Chat"
        #   Voice       → main_tabs "🎤 Voice"
        #   SuperLookup → main_tabs "🔍 SuperLookup"
        # v1.10.10 finished the cleanup: floating_assistant.py is
        # deleted and no code path references _floating_assistant
        # any more.

        # Supervertaler Bridge server – inverse of the Trados-side bridge.
        # Lets the Trados plugin POST a QuickLauncher prompt here for
        # the Workbench Chat panel to run. Only the on-disk handshake
        # filename (sidekick-bridge.json) keeps the old "sidekick" name,
        # because Core/SupervertalerBridge.cs on the Trados side looks up
        # the handshake by that exact name; renaming it would break the
        # bridge for mismatched product versions. The consumer here is
        # the in-Workbench Chat tab (FloatingAssistant was retired in
        # v1.10.4). See modules/supervertaler_bridge_server.py.
        self._bridge_server = None
        try:
            from modules.supervertaler_bridge_server import SupervertalerBridgeServer
            self._bridge_server = SupervertalerBridgeServer(self)
            self._bridge_server.run_prompt_requested.connect(
                self._on_bridge_prompt_request
            )
            self._bridge_server.start()
            QApplication.instance().aboutToQuit.connect(self._bridge_server.stop)
        except Exception as e:
            self.log(f"⚠ Trados bridge server failed to start: {e}")

        # Keep backward compatibility reference
        self.document_views_widget = self.main_tabs
        
        # The "Tools" main tab was retired in v1.9.467: PDF Rescue and TMX Editor
        # now open in their own windows from the Tools menu, so the embedded
        # tab no longer pulls its weight. SuperlookupTab still needs to be
        # constructed though – its __init__ registers the global Ctrl+Alt+L /
        # Ctrl+Alt+Q / Alt+K hotkeys – so do that here as a side-effect.
        self._setup_superlookup_hotkeys()

        # ============================================================
        # NEW TOP-LEVEL TABS (Phase 1 of the Sidekick → Workbench
        # migration, issue #199). SuperLookup and Voice
        # are added here as Workbench top tabs so the user has a
        # single-window app instead of toggling between Workbench and
        # the always-on-top Sidekick.
        #
        # Lazy construction: each tab starts as an empty placeholder
        # QWidget. The first time the user activates the tab,
        # _on_main_tab_changed calls _ensure_*_top_tab which builds
        # the real widget into the placeholder. This keeps cold start
        # fast – SuperLookup in particular brings up TM / termbase /
        # MT / web-resource state and is too heavy to construct
        # eagerly on every launch.
        #
        # During Phases 1-3 Sidekick continues to exist with its own
        # copies of these widgets. Both surfaces work in parallel.
        # Phase 4 retires Sidekick and removes the duplication. The
        # hotkey-side-effect SuperLookup instance from
        # _setup_superlookup_hotkeys above stays during this phase –
        # it's what registers the global hotkeys; the visible top tab
        # is a separate instance.
        # ============================================================
        self._superlookup_top_widget = None
        self._voice_top_widget = None

        from PyQt6.QtWidgets import QWidget as _QW
        superlookup_placeholder = _QW()
        self.main_tabs.addTab(superlookup_placeholder, "🔍 SuperLookup")
        self.superlookup_tab_index = self.main_tabs.count() - 1

        voice_placeholder = _QW()
        self.main_tabs.addTab(voice_placeholder, "🎤 Voice")
        self.voice_tab_index = self.main_tabs.count() - 1

        # 4. SETTINGS
        settings_tab = self.create_settings_tab()
        self.main_tabs.addTab(settings_tab, "⚙️ Settings")
        self.settings_tab_index = self.main_tabs.count() - 1
        
        # Set startup tab to Grid (index 0)
        self.main_tabs.setCurrentIndex(0)

        main_layout.addWidget(self.main_tabs)

        # v1.10.17: Esc-to-tray on the "quick lookup" top tabs.
        # When the user is on SuperLookup / Voice –
        # which they typically reach via global hotkeys and treat as
        # popup utilities rather than full-screen work surfaces –
        # pressing Esc hides Workbench to the system tray, matching
        # the dismissal flow that retired Sidekick used to have.
        # On Editor / TMs / Termbases / AI / Settings the binding
        # is a no-op so Esc retains its natural editor / dialog /
        # combo-box behaviour. Implementation is a single
        # window-level QShortcut with a current-tab check at fire
        # time, rather than per-tab WidgetShortcut bindings, so the
        # binding survives lazy tab construction (the tabs are
        # placeholder widgets at this point – the real widgets are
        # built on first activation by _ensure_*_top_tab).
        try:
            from PyQt6.QtGui import QShortcut, QKeySequence
            self._esc_to_tray_shortcut = QShortcut(
                QKeySequence(Qt.Key.Key_Escape), self
            )
            self._esc_to_tray_shortcut.activated.connect(
                self._on_esc_quick_lookup_dismiss
            )
            # v1.10.344: if ANY other window-context shortcut in this
            # window ever binds Escape, Qt declares the keypress
            # ambiguous and fires activatedAmbiguously on ALL of them
            # instead of activated on any – i.e. Esc silently does
            # nothing. Route the ambiguous signal to the same handler
            # (with a console note so the conflict is discoverable)
            # so the dismiss can never be nulled by a future conflict.
            def _esc_ambiguous():
                print("[Workbench] Escape shortcut fired AMBIGUOUSLY – "
                      "another window-level Esc shortcut exists; "
                      "dismissing anyway.")
                self._on_esc_quick_lookup_dismiss()
            self._esc_to_tray_shortcut.activatedAmbiguously.connect(
                _esc_ambiguous
            )
        except Exception as e:
            print(f"[Workbench] Esc-to-tray binding failed: {e}")

        # Connect tab changes to handle view refreshes
        self.main_tabs.currentChanged.connect(self._on_main_tab_changed)

        # Warm up the new top tabs ~2.5 s after launch (Phase 1 + 2 of
        # issue #199). They're lazily constructed on first activation,
        # but the first-press race – global hotkey fires → lazy build
        # takes ~1 s → Workbench foreground-grab arrives too late to
        # win against the source app – means the *very* first
        # Ctrl+Alt+L / Ctrl+Alt+C after startup can silently fall on
        # the floor. Pre-warming a few seconds after the main window
        # paints sidesteps this without paying the cost at startup.
        # 2 500 ms gives the window time to paint, the Okapi sidecar
        # to hand over, and the database to settle – same value
        # Sidekick uses for its own lazy-tab warm-up.
        try:
            QTimer.singleShot(2500, self._warm_up_top_tabs)
        except Exception:
            pass

    
    def _create_placeholder_tab(self, title: str, description: str) -> QWidget:
        """Create a simple placeholder tab"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        
        header = QLabel(title)
        header.setStyleSheet("font-size: 14px; font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(header)
        
        placeholder = QLabel(description)
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(placeholder, stretch=1)
        
        return tab
    
    
    def open_superbrowser_window(self):
        """Open Superbrowser (the multi-chat AI browser) in its own window.

        Removed in v1.9.385's simplification series, restored in v1.10.365
        because a user asked after it. Its old home - the Tools tab - no
        longer exists, so it now follows the same open-in-own-window pattern
        as the other tools; the widget itself returned from git unchanged,
        and any workbench/superbrowser_profiles/ folder left behind by the
        removal (deliberately not deleted then) brings old logins back.
        """
        from PyQt6.QtCore import Qt
        from modules.superbrowser import SuperbrowserWidget

        existing = getattr(self, '_superbrowser_window', None)
        if existing is not None:
            try:
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return
            except RuntimeError:
                # Window was destroyed without the destroyed-signal callback
                # firing (rare). Fall through and create a new one.
                self._superbrowser_window = None

        window = QWidget()
        window.setWindowFlags(Qt.WindowType.Window)
        window.setWindowTitle(self.tr("🌐 Superbrowser – Supervertaler"))
        # Three browser columns want width; height matches the TMX Editor.
        window.resize(1500, 850)

        layout = QVBoxLayout(window)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(SuperbrowserWidget(parent=window, user_data_path=self.user_data_path), 1)

        self._superbrowser_window = window
        # Drop the reference when the window closes so a subsequent click
        # opens a fresh instance instead of reusing a deleted Qt object.
        window.destroyed.connect(lambda *a: setattr(self, '_superbrowser_window', None))
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        window.show()

    def show_statistics_dialog(self):
        """Open the Statistics dialog to analyse the open project against TMs.

        Mirrors the analysis step of Trados Studio / memoQ: scans the current
        project's segments against one or more translation memories and reports
        a match breakdown (repetitions, 100%, fuzzy bands, no-match) so the
        translator can gauge how much work the document represents.
        """
        if not getattr(self, 'current_project', None) or not getattr(self.current_project, 'segments', None):
            QMessageBox.information(
                self, self.tr("Statistics"),
                self.tr("Open a project with segments first, then run Statistics."))
            return

        try:
            from modules.statistics_dialog_qt import StatisticsDialog

            # Build TM choices (real names + entry counts) from the metadata manager,
            # falling back to the database manager's distinct-tm_id list.
            tm_choices = []
            try:
                tm_choices = [
                    {'tm_id': tm['tm_id'], 'name': tm.get('name') or tm['tm_id'],
                     'entry_count': tm.get('entry_count', 0)}
                    for tm in self.tm_metadata_mgr.get_all_tms()
                ]
            except Exception:
                try:
                    tm_choices = [
                        {'tm_id': tm['tm_id'], 'name': tm.get('name') or tm['tm_id'],
                         'entry_count': tm.get('entry_count', 0)}
                        for tm in self.db_manager.get_all_tms()
                    ]
                except Exception:
                    tm_choices = []

            # Pre-select the project's currently activated TMs.
            preselected = []
            try:
                if hasattr(self.current_project, 'tm_settings') and self.current_project.tm_settings:
                    preselected = list(self.current_project.tm_settings.get('activated_tm_ids', []) or [])
            except Exception:
                preselected = []

            dialog = StatisticsDialog(
                parent=self,
                db_manager=self.db_manager,
                segments=self.current_project.segments,
                source_lang=self.current_project.source_lang,
                target_lang=self.current_project.target_lang,
                tm_choices=tm_choices,
                preselected_tm_ids=preselected,
                project_name=getattr(self.current_project, 'name', '') or '',
            )
            dialog.exec()
        except Exception as e:
            self.log(f"Error opening Statistics: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, self.tr("Error"), f"Failed to open Statistics:\n{str(e)}")

    def _quick_count_lang_pair(self):
        """Source/target language codes to use for a project-free count: the
        open project's pair if any, else the last-used import pair."""
        proj = getattr(self, 'current_project', None)
        if proj is not None and getattr(proj, 'source_lang', None):
            return (proj.source_lang or 'en', proj.target_lang or 'nl')
        try:
            gs = self.load_general_settings()
            return (gs.get('last_import_source_lang', 'en') or 'en',
                    gs.get('last_import_target_lang', 'nl') or 'nl')
        except Exception:
            return ('en', 'nl')

    def _quick_extract_sources(self, file_path, src_lang, tgt_lang):
        """Extract a list of source-text strings from one file for a quick count.

        Supports DOCX and the other Okapi formats (sentence-segmented like a
        normal import), plus Trados .sdlxliff and memoQ .mqxliff. Raises
        ValueError for an unsupported extension; the caller handles per-file
        errors so one bad file doesn't sink the rest.
        """
        ext = os.path.splitext(file_path)[1].lower()
        okapi_exts = {'.docx', '.xlsx', '.pptx', '.html', '.htm',
                      '.xliff', '.xlf', '.idml', '.po'}
        if ext in okapi_exts:
            if not self._ensure_okapi_sidecar():
                raise RuntimeError("The Okapi sidecar is not available.")
            result = self.okapi_sidecar.extract(
                file_path, source_lang=src_lang, target_lang=tgt_lang, segment=True,
                options=okapi_sidecar_options(self.get_effective_import_options()),
            )
            return [s.get('source', '').strip()
                    for s in result.get('segments', [])
                    if (s.get('source') or '').strip()]
        if ext == '.sdlxliff':
            from modules.sdlppx_handler import StandaloneSDLXLIFFHandler
            h = StandaloneSDLXLIFFHandler()
            h.load([file_path])
            return [s.source_text for s in h.get_all_segments()
                    if getattr(s, 'source_text', '').strip()]
        if ext == '.mqxliff':
            from modules.mqxliff_handler import MQXLIFFHandler
            h = MQXLIFFHandler()
            h.load(file_path)
            return [s.plain_text for s in h.extract_source_segments()
                    if getattr(s, 'plain_text', '').strip()]
        raise ValueError(f"Unsupported file type: {ext or '(none)'}")

    def show_quick_statistics_dialog(self):
        """Quick Count: pick one or more files (no project needed), extract their
        source segments, then open the Statistics dialog to count them against
        the selected TMs."""
        from types import SimpleNamespace
        from PyQt6.QtWidgets import QProgressDialog

        formats = [
            ("Word Document", "*.docx"),
            ("Trados bilingual", "*.sdlxliff"),
            ("memoQ bilingual", "*.mqxliff"),
            ("Adobe InDesign Markup", "*.idml"),
            ("HTML", "*.html *.htm"),
            ("XLIFF 1.2", "*.xliff *.xlf"),
            ("PO (gettext)", "*.po"),
            ("Microsoft Excel", "*.xlsx"),
            ("Microsoft PowerPoint", "*.pptx"),
        ]
        all_patterns = " ".join(p for _, p in formats)
        filter_parts = [f"All supported formats ({all_patterns})"]
        filter_parts += [f"{n} ({p})" for n, p in formats]
        filter_parts.append("All Files (*.*)")

        paths, _ = QFileDialog.getOpenFileNames(
            self, self.tr("Quick Count – select file(s)"), "", ";;".join(filter_parts))
        if not paths:
            return

        src_lang, tgt_lang = self._quick_count_lang_pair()

        progress = QProgressDialog(
            self.tr("Reading files…"), None, 0, len(paths), self)
        progress.setWindowTitle(self.tr("Quick Count"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        QApplication.processEvents()

        segments = []
        errors = []
        for i, path in enumerate(paths):
            progress.setValue(i)
            progress.setLabelText(self.tr("Reading: ") + os.path.basename(path))
            QApplication.processEvents()
            try:
                sources = self._quick_extract_sources(path, src_lang, tgt_lang)
                _fname = os.path.basename(path)
                segments.extend(SimpleNamespace(source=s, file_name=_fname) for s in sources)
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
                self.log(f"⚠ Quick Count could not read {os.path.basename(path)}: {e}")
        progress.setValue(len(paths))
        progress.close()

        if errors:
            QMessageBox.warning(
                self, self.tr("Quick Count"),
                self.tr("Some files could not be read:\n\n") + "\n".join(errors))

        if not segments:
            QMessageBox.information(
                self, self.tr("Quick Count"),
                self.tr("No countable source segments were found in the selected file(s)."))
            return

        scope = (os.path.basename(paths[0]) if len(paths) == 1
                 else f"{len(paths)} files")
        try:
            from modules.statistics_dialog_qt import StatisticsDialog
            dialog = StatisticsDialog(
                parent=self,
                db_manager=self.db_manager,
                segments=segments,
                source_lang=src_lang,
                target_lang=tgt_lang,
                tm_choices=self._all_tm_choices(),
                preselected_tm_ids=[],
                project_name=scope,
            )
            dialog.exec()
        except Exception as e:
            self.log(f"Error opening Quick Count: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, self.tr("Error"), f"Failed to open Quick Count:\n{str(e)}")

    def create_reference_images_tab(self) -> QWidget:
        """Create the Image Context tab - Load images as visual context for AI translation"""
        from modules.image_extractor import ImageExtractor

        tab = QWidget()
        main_layout = QVBoxLayout(tab)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(5)

        # v1.10.176: the widget now lives inside the Prompt Manager tab
        # as page 1 of a right-hand QStackedWidget (page 0 = Prompt
        # Editor). Header is now a single compact toolbar row containing
        # the title AND a "← Back to Prompt Editor" button that swaps
        # the stack back. The wordy multi-line description has been
        # demoted to a tooltip on the title — saves a chunk of vertical
        # space the user was rightly complaining about.

        # ── Single-line compact header (title + tooltip + back btn) ──
        from PyQt6.QtWidgets import QSizePolicy
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 4)
        header_row.setSpacing(8)

        back_btn = QPushButton(self.tr("← Back to Prompt Editor"))
        back_btn.setToolTip(self.tr("Switch the right panel back to the Prompt Editor"))
        back_btn.setStyleSheet(
            "QPushButton { padding: 2px 10px; border-radius: 3px; "
            "background-color: #ECEFF1; color: #333; } "
            "QPushButton:hover { background-color: #CFD8DC; } "
            "QPushButton:focus { outline: none; }"
        )
        # Late-binding lambda so prompt_manager_qt doesn't have to exist
        # at the moment this lambda is wired up. Resolution happens on click.
        back_btn.clicked.connect(
            lambda: hasattr(self, 'prompt_manager_qt') and self.prompt_manager_qt
            and self.prompt_manager_qt.show_prompt_editor_view()
        )
        header_row.addWidget(back_btn)

        header_title = QLabel(self.tr("🎯 Image Context for AI Translation"))
        header_title.setStyleSheet("font-size: 13px; font-weight: bold; color: #2c3e50;")
        header_title.setToolTip(
            "Pass figure images to the AI alongside your translation. When a segment "
            "references 'Figure 1' or 'see fig 2A', the AI sees the image and translates "
            "technical descriptions and part references more accurately."
        )
        header_title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        header_row.addWidget(header_title)
        header_row.addStretch()

        # v1.10.180: contextual help button — opens the Image Context help
        # page directly at the "How loaded images reach the AI" section,
        # so the user lands on the practical "what does this actually do"
        # explanation instead of having to scroll past the where-to-find-it
        # preamble.
        try:
            from modules.styled_widgets import HelpButton
            image_help_btn = HelpButton(
                "workbench/ai-translation/image-context/#how-loaded-images-reach-the-ai",
                tooltip="Open the Image Context help page",
            )
            header_row.addWidget(image_help_btn)
        except Exception as e:
            self.log(f"[Image Context] Could not add help button: {e}")

        main_layout.addLayout(header_row)

        # ── Row 1: input files for extraction + DOCX list ─────────
        inputs_row = QHBoxLayout()

        add_file_btn = QPushButton(self.tr("📄 Add DOCX"))
        add_file_btn.clicked.connect(self._on_add_docx_file_for_extraction)
        add_file_btn.setMaximumWidth(110)
        add_file_btn.setToolTip(
            "Add a single DOCX file to the extraction queue below.\n"
            "Use this when you want to pull images OUT of a Word document.\n"
            "Click '🖼️ Extract Images' once the queue is ready."
        )
        inputs_row.addWidget(add_file_btn)

        add_folder_btn = QPushButton(self.tr("📁 Add Folder"))
        add_folder_btn.clicked.connect(self._on_add_docx_folder_for_extraction)
        add_folder_btn.setMaximumWidth(110)
        add_folder_btn.setToolTip(
            "Add EVERY DOCX file in a folder to the extraction queue below.\n"
            "Useful for batch-extracting images from many Word documents at once.\n"
            "Click '🖼️ Extract Images' once the queue is ready.\n\n"
            "Note: this is for adding *Word documents* to extract from.\n"
            "If you already have an Images folder ready, use '📁 Load Folder' instead."
        )
        inputs_row.addWidget(add_folder_btn)

        clear_list_btn = QPushButton("🗑️")
        clear_list_btn.clicked.connect(lambda: self.image_extractor_file_list.clear())
        clear_list_btn.setMaximumWidth(40)
        clear_list_btn.setToolTip(self.tr("Clear DOCX input list"))
        inputs_row.addWidget(clear_list_btn)

        self.image_extractor_file_list = QListWidget()
        self.image_extractor_file_list.setMaximumHeight(40)
        self.image_extractor_file_list.setStyleSheet("font-size: 9px;")
        inputs_row.addWidget(self.image_extractor_file_list, 1)

        main_layout.addLayout(inputs_row)

        # ── Row 2: extraction settings + all action buttons ───────
        # All four buttons (Extract, Load Folder, Clear loaded) live on
        # the same row as the extraction settings — one integrated
        # toolbar instead of the v1.10.174 two-section split.
        action_row = QHBoxLayout()

        self.image_extractor_auto_folder = CheckmarkCheckBox(self.tr("Auto-folder"))
        self.image_extractor_auto_folder.setChecked(False)
        self.image_extractor_auto_folder.setToolTip(self.tr("Create an 'Images' folder next to each DOCX file"))
        self.image_extractor_auto_folder.toggled.connect(self._on_auto_folder_toggled)
        action_row.addWidget(self.image_extractor_auto_folder)

        self.image_extractor_output_dir = QLineEdit()
        self.image_extractor_output_dir.setPlaceholderText(self.tr("Output directory…"))
        self.image_extractor_output_dir.setMaximumWidth(220)
        action_row.addWidget(self.image_extractor_output_dir)

        browse_btn = QPushButton(self.tr("Browse…"))
        browse_btn.clicked.connect(self._on_browse_output_dir_for_extraction)
        browse_btn.setMaximumWidth(80)
        action_row.addWidget(browse_btn)

        action_row.addWidget(QLabel(self.tr("Prefix:")))
        self.image_extractor_prefix = QLineEdit("Fig.")
        self.image_extractor_prefix.setMaximumWidth(60)
        action_row.addWidget(self.image_extractor_prefix)

        # v1.10.192: opt-in vision-AI fallback for images that the
        # text-pattern detector can't label. When ticked, each
        # unlabelled image (after the v1.10.190 pattern + Caption-style
        # scan) is sent to the active vision model with surrounding
        # text and asked to identify its label. Off by default — a
        # confirmation dialog with cost estimate appears before each
        # extraction run.
        self.image_extractor_ai_label = CheckmarkCheckBox(self.tr("🤖 AI label"))
        self.image_extractor_ai_label.setChecked(False)
        self.image_extractor_ai_label.setToolTip(
            "Use AI to label images that the text-pattern detector can't.\n"
            "Useful for documents that don't follow the standard label\n"
            "vocabularies (Figure / FIG. / Table / Diagram / Plate / Photo /\n"
            "Scheme / Exhibit) — marketing copy, blog posts, foreign-language\n"
            "documents, photo essays, cookbooks, etc.\n\n"
            "Requirements:\n"
            "  • Vision-capable model in Settings (Claude Sonnet/Opus 4.x,\n"
            "    GPT-4o or newer, Gemini)\n"
            "  • Internet connection\n\n"
            "Cost: ~$0.005–$0.02 per AI-labelled image depending on provider.\n"
            "Only used for images the free text-pattern detector missed —\n"
            "documents with conventional labels (patents, academic papers)\n"
            "use the cheaper path and never trigger an API call.\n\n"
            "A confirmation dialog shows the estimated cost before any\n"
            "API call is made."
        )
        action_row.addWidget(self.image_extractor_ai_label)

        # Extract button — primary action for the DOCX path.
        extract_btn = QPushButton(self.tr("🖼️ Extract Images"))
        extract_btn.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                font-weight: bold;
                font-size: 12px;
                padding: 6px 14px;
                border-radius: 3px;
            }
            QPushButton:hover { background-color: #2980b9; }
            QPushButton:focus { outline: none; }
        """)
        extract_btn.clicked.connect(self._on_extract_images)
        extract_btn.setToolTip(
            "Extract images from the DOCX files listed above.\n"
            "Images are saved to the Output directory (or an 'Images' folder\n"
            "next to each DOCX when 'Auto-folder' is ticked) and the resulting\n"
            "folder is automatically loaded as AI context for figure-aware\n"
            "translation."
        )
        action_row.addWidget(extract_btn)

        # Visual separator between the "extract from DOCX" path and
        # the "load a pre-existing folder" path.
        sep_label = QLabel(self.tr("  or  "))
        sep_label.setStyleSheet("color: #999; font-style: italic;")
        action_row.addWidget(sep_label)

        load_context_btn = QPushButton(self.tr("📁 Load Folder"))
        load_context_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                padding: 6px 14px;
                border-radius: 3px;
                border: none;
                outline: none;
            }
            QPushButton:hover { background-color: #45a049; }
            QPushButton:focus { outline: none; border: none; }
        """)
        load_context_btn.setToolTip(
            "Load images from a folder that already contains image files\n"
            "(e.g. one you extracted in a previous session, or a folder\n"
            "you assembled by hand). Skips the DOCX extraction step.\n\n"
            "Note: '📁 Add Folder' (above) queues a folder of *Word documents*\n"
            "to extract from. This button loads a folder of *images* directly."
        )
        load_context_btn.clicked.connect(self._on_load_image_context_folder)
        action_row.addWidget(load_context_btn)

        action_row.addStretch()

        main_layout.addLayout(action_row)

        # ── Row 3: live status of what's currently loaded for AI ──
        # One thin row instead of v1.10.174's full section card.
        loaded_row = QHBoxLayout()
        loaded_row.setContentsMargins(0, 4, 0, 4)

        loaded_caption = QLabel(self.tr("Loaded for AI:"))
        loaded_caption.setStyleSheet("font-size: 11px; color: #555; font-weight: bold;")
        loaded_row.addWidget(loaded_caption)

        self.image_context_status_label = QLabel(self.tr("No images loaded"))
        self.image_context_status_label.setStyleSheet("color: #999; font-size: 11px;")
        loaded_row.addWidget(self.image_context_status_label, 1)

        clear_context_btn = QPushButton(self.tr("🗑️ Clear loaded"))
        clear_context_btn.clicked.connect(self._on_clear_image_context)
        clear_context_btn.setMaximumWidth(110)
        loaded_row.addWidget(clear_context_btn)

        main_layout.addLayout(loaded_row)

        # ── Results & preview ────────────────────────────────────
        # Main horizontal splitter (left: operations, right: preview)
        results_splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Left panel: Status and file list
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        status_label = QLabel(self.tr("📋 Results"))
        status_label.setStyleSheet("font-weight: bold; font-size: 9px; color: #666;")
        left_layout.addWidget(status_label)
        
        # Status text area (resizable)
        self.image_extractor_status = QTextEdit()
        self.image_extractor_status.setReadOnly(True)
        self.image_extractor_status.setMinimumHeight(50)
        self.image_extractor_status.setStyleSheet("font-size: 9px;")
        self.image_extractor_status.setPlaceholderText(self.tr("Extraction status..."))
        left_layout.addWidget(self.image_extractor_status)
        
        # Extracted files list
        files_label = QLabel(self.tr("📂 Extracted Files (click to preview)"))
        files_label.setStyleSheet("font-weight: bold; font-size: 9px; color: #666; margin-top: 3px;")
        left_layout.addWidget(files_label)
        
        self.image_extractor_files_list = QListWidget()
        self.image_extractor_files_list.itemClicked.connect(self._on_file_list_item_clicked)
        self.image_extractor_files_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ccc;
                font-size: 9px;
            }
            QListWidget::item {
                padding: 3px;
                border-bottom: 1px solid #eee;
            }
            QListWidget::item:selected {
                background-color: #e3f2fd;
                color: black;
            }
        """)
        left_layout.addWidget(self.image_extractor_files_list)
        
        results_splitter.addWidget(left_widget)
        
        # Right panel: Full-height image preview
        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        
        preview_label = QLabel(self.tr("🖼️ Image Preview"))
        preview_label.setStyleSheet("font-weight: bold; font-size: 9px; color: #666;")
        preview_layout.addWidget(preview_label)
        
        # Large image display area with scroll
        preview_scroll = QScrollArea()
        preview_scroll.setWidgetResizable(True)
        preview_scroll.setStyleSheet("QScrollArea { border: 1px solid #ccc; }")
        preview_scroll.setMinimumWidth(300)
        
        self.image_extractor_preview = QLabel()
        self.image_extractor_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_extractor_preview.setStyleSheet("padding: 10px;")
        self.image_extractor_preview.setText(self.tr("No image selected\n\nClick on a file in the list to preview\nor\nExtract images to see them here"))
        self.image_extractor_preview.setWordWrap(True)
        
        preview_scroll.setWidget(self.image_extractor_preview)
        preview_layout.addWidget(preview_scroll)
        
        # Preview navigation buttons at bottom
        nav_layout = QHBoxLayout()
        
        self.preview_prev_btn = QPushButton(self.tr("◀ Previous"))
        self.preview_prev_btn.clicked.connect(self._on_preview_prev)
        self.preview_prev_btn.setEnabled(False)
        nav_layout.addWidget(self.preview_prev_btn)
        
        self.preview_image_label = QLabel(self.tr("No images"))
        self.preview_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_image_label.setStyleSheet("font-size: 8px; color: #666;")
        nav_layout.addWidget(self.preview_image_label)
        
        self.preview_next_btn = QPushButton(self.tr("Next ▶"))
        self.preview_next_btn.clicked.connect(self._on_preview_next)
        self.preview_next_btn.setEnabled(False)
        nav_layout.addWidget(self.preview_next_btn)
        
        preview_layout.addLayout(nav_layout)
        
        results_splitter.addWidget(preview_widget)
        
        # Set initial splitter sizes (40% left, 60% right)
        results_splitter.setSizes([400, 600])

        # v1.10.176: explicit stretch=1 so the splitter claims every
        # pixel of vertical space the labels + toolbar don't need.
        # Previously the QVBoxLayout was distributing extra space across
        # the labels too, which left ridiculous gaps between the title,
        # description, and toolbar (per the user's red-arrow screenshot).
        main_layout.addWidget(results_splitter, 1)
        
        # Initialize extractor and preview state
        self.image_extractor = ImageExtractor()
        self.extracted_image_files = []
        self.current_preview_index = 0
        
        return tab
    
    def open_pdf_rescue_window(self):
        """Open PDF Rescue in a standalone window. Reuses an existing window
        if already open."""
        from PyQt6.QtCore import Qt
        from modules.pdf_rescue_Qt import PDFRescueQt

        existing = getattr(self, '_pdf_rescue_window', None)
        if existing is not None:
            try:
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return
            except RuntimeError:
                self._pdf_rescue_window = None

        window = QWidget()
        window.setWindowFlags(Qt.WindowType.Window)
        window.setWindowTitle(self.tr("🔍 PDF Rescue – Supervertaler"))
        window.resize(1100, 800)

        # PDFRescueQt builds its own header / layout into the supplied widget.
        self.pdf_rescue_qt = PDFRescueQt(self, standalone=False)
        self.pdf_rescue_qt.create_tab(window)

        self._pdf_rescue_window = window
        window.destroyed.connect(lambda *a: setattr(self, '_pdf_rescue_window', None))
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        window.show()
    
    # ═══════════════════════════════════════════════════════════════════════════
    # Image Extractor Helper Methods
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _on_add_docx_file_for_extraction(self):
        """Add a single DOCX file to the extraction list"""
        file_path, _ = fdh.get_open_file_name(
            self,
            "Select DOCX File",
            "Word Documents (*.docx)"
        )
        
        if file_path:
            # Avoid duplicates
            items = [self.image_extractor_file_list.item(i).text() 
                    for i in range(self.image_extractor_file_list.count())]
            if file_path not in items:
                self.image_extractor_file_list.addItem(file_path)
    
    def _on_add_docx_folder_for_extraction(self):
        """Add all DOCX files from a folder to the extraction list"""
        folder_path = fdh.get_existing_directory(
            self,
            "Select Folder Containing DOCX Files"
        )
        
        if folder_path:
            import glob
            docx_files = glob.glob(os.path.join(folder_path, "*.docx"))
            
            # Get existing items to avoid duplicates
            items = [self.image_extractor_file_list.item(i).text() 
                    for i in range(self.image_extractor_file_list.count())]
            
            added = 0
            for docx_file in docx_files:
                if docx_file not in items:
                    self.image_extractor_file_list.addItem(docx_file)
                    added += 1
            
            if added > 0:
                self.image_extractor_status.append(f"✅ Added {added} DOCX file(s) from folder")
    
    def _on_browse_output_dir_for_extraction(self):
        """Browse for output directory"""
        folder_path = fdh.get_existing_directory(
            self,
            "Select Output Directory for Extracted Images"
        )
        
        if folder_path:
            self.image_extractor_output_dir.setText(folder_path)
    
    def _on_auto_folder_toggled(self, checked):
        """Handle auto-folder checkbox toggle"""
        # Disable/enable output directory selection based on auto-folder
        self.image_extractor_output_dir.setEnabled(not checked)
        
        if checked:
            self.image_extractor_output_dir.setPlaceholderText(self.tr("Auto: 'Images' folder next to each DOCX"))
        else:
            self.image_extractor_output_dir.setPlaceholderText(self.tr("Choose output directory..."))
    
    def _on_file_list_item_clicked(self, item):
        """Handle click on extracted file list item"""
        if not item:
            return
        
        # Get the file path stored in the item
        file_path = item.data(Qt.ItemDataRole.UserRole)
        
        if file_path and file_path in self.extracted_image_files:
            # Update current index and preview
            self.current_preview_index = self.extracted_image_files.index(file_path)
            self._update_preview()
    
    # ────────────────────────────────────────────────────────────────
    # v1.10.192: vision-AI fallback for the Image Extractor
    # ────────────────────────────────────────────────────────────────

    def _on_extract_images(self):
        """Extract images from all DOCX files in the list"""
        # Validate inputs
        if self.image_extractor_file_list.count() == 0:
            QMessageBox.warning(
                self,
                "No Files",
                "Please add at least one DOCX file to extract images from."
            )
            return
        
        # Check if using auto-folder mode
        use_auto_folder = self.image_extractor_auto_folder.isChecked()
        
        if not use_auto_folder:
            output_dir = self.image_extractor_output_dir.text().strip()
            if not output_dir:
                QMessageBox.warning(
                    self,
                    "No Output Directory",
                    "Please select an output directory for the extracted images."
                )
                return
        
        prefix = self.image_extractor_prefix.text().strip()
        if not prefix:
            prefix = "Fig."

        # Get list of files
        docx_files = [self.image_extractor_file_list.item(i).text()
                     for i in range(self.image_extractor_file_list.count())]

        # v1.10.192: build the optional AI-label-fallback callback. If
        # the user ticked the checkbox we peek at all DOCXes first to
        # see how many images would actually need the AI step, then
        # show a confirmation dialog with an estimated cost before any
        # API call is made.
        ai_label_fn = self._build_image_extract_ai_callback(docx_files)
        if ai_label_fn is False:
            # User cancelled at the confirmation dialog — abort the
            # entire extraction (they were expecting AI labels and
            # we'd surprise them with sequential numbering otherwise).
            return

        # Clear status, file list, and preview
        self.image_extractor_status.clear()
        self.image_extractor_status.append("🔄 Starting image extraction...\n")
        self.image_extractor_files_list.clear()
        self.extracted_image_files = []
        self.current_preview_index = 0
        self.image_extractor_preview.setText(self.tr("No image selected\n\nClick on a file in the list to preview\nor\nExtract images to see them here"))
        QApplication.processEvents()
        
        try:
            total_count = 0
            all_extracted_files = []
            
            if use_auto_folder:
                # Extract to "Images" folder next to each DOCX
                self.image_extractor_status.append("📁 Mode: Auto-folder (Images subfolder per DOCX)\n")
                
                for docx_file in docx_files:
                    docx_dir = os.path.dirname(docx_file)
                    docx_name = os.path.splitext(os.path.basename(docx_file))[0]
                    auto_output_dir = os.path.join(docx_dir, "Images")
                    
                    self.image_extractor_status.append(f"📄 Processing: {os.path.basename(docx_file)}")
                    
                    count, files = self.image_extractor.extract_images_from_docx(
                        docx_file,
                        auto_output_dir,
                        prefix,
                        ai_label_fn=ai_label_fn,
                    )
                    
                    total_count += count
                    all_extracted_files.extend(files)
                    
                    if count > 0:
                        self.image_extractor_status.append(f"   ✅ {count} image(s) → {auto_output_dir}")
                    else:
                        self.image_extractor_status.append(f"   ⚠️  No images found")
                    
                    QApplication.processEvents()
                
                output_msg = f"Images saved in 'Images' subfolders next to each DOCX file"
            else:
                # Extract all to single directory
                output_dir = self.image_extractor_output_dir.text().strip()
                self.image_extractor_status.append(f"📁 Output directory: {output_dir}\n")
                
                total_count, all_extracted_files = self.image_extractor.extract_from_multiple_docx(
                    docx_files,
                    output_dir,
                    prefix,
                    ai_label_fn=ai_label_fn,
                )
                
                output_msg = output_dir
            
            # Show results
            self.image_extractor_status.append(f"\n✅ Successfully extracted {total_count} images!")
            
            if all_extracted_files:
                # Store files for preview
                self.extracted_image_files = all_extracted_files
                self.current_preview_index = 0
                
                # Populate file list
                self.image_extractor_files_list.clear()
                for i, file_path in enumerate(all_extracted_files, 1):
                    item = QListWidgetItem(f"{i}. {os.path.basename(file_path)}")
                    item.setData(Qt.ItemDataRole.UserRole, file_path)  # Store full path
                    item.setToolTip(file_path)  # Show full path on hover
                    self.image_extractor_files_list.addItem(item)
                
                # Enable preview buttons
                if len(self.extracted_image_files) > 0:
                    self.preview_prev_btn.setEnabled(len(self.extracted_image_files) > 1)
                    self.preview_next_btn.setEnabled(len(self.extracted_image_files) > 1)

                    # Select and show first image
                    self.image_extractor_files_list.setCurrentRow(0)
                    self._update_preview()

                # v1.10.174: auto-load the freshly-extracted folder as
                # Image Context for AI. The whole point of extracting
                # is usually to use them in the AI translation pipeline,
                # so doing this in one step removes the second click
                # from the common workflow. Users who want to load a
                # DIFFERENT folder can still use the "Load Images Folder"
                # button to override.
                try:
                    auto_load_folder = self._guess_extracted_folder(all_extracted_files)
                    if auto_load_folder:
                        self._load_image_context_from_path(auto_load_folder, source="just-extracted")
                except Exception as e:
                    self.log(f"[Image Context] Auto-load after extraction failed: {e}")

            # Success message
            QMessageBox.information(
                self,
                "Extraction Complete",
                f"Successfully extracted {total_count} images!\n\n{output_msg}"
            )
            
        except Exception as e:
            self.image_extractor_status.append(f"\n❌ Error: {str(e)}")
            QMessageBox.critical(
                self,
                "Extraction Error",
                f"An error occurred during extraction:\n{str(e)}"
            )
    
    def _update_preview(self):
        """Update the image preview with the current image"""
        if not self.extracted_image_files or self.current_preview_index >= len(self.extracted_image_files):
            self.image_extractor_preview.setText(self.tr("No image selected\n\nClick on a file in the list to preview\nor\nExtract images to see them here"))
            self.preview_image_label.setText(self.tr("No images"))
            return
        
        image_path = self.extracted_image_files[self.current_preview_index]
        
        # Update list selection to match current preview
        if hasattr(self, 'image_extractor_files_list'):
            self.image_extractor_files_list.setCurrentRow(self.current_preview_index)
        
        try:
            from PyQt6.QtGui import QPixmap
            
            pixmap = QPixmap(image_path)
            
            if not pixmap.isNull():
                # Get the scroll area size for better scaling
                scroll_area = self.image_extractor_preview.parent()
                if scroll_area and hasattr(scroll_area, 'viewport'):
                    viewport_size = scroll_area.viewport().size()
                    max_width = viewport_size.width() - 40  # Account for padding
                    max_height = viewport_size.height() - 40
                else:
                    # Fallback to larger default size
                    max_width = 800
                    max_height = 600
                
                # Scale image to fit preview area while maintaining aspect ratio
                scaled_pixmap = pixmap.scaled(
                    max_width, max_height,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                
                self.image_extractor_preview.setPixmap(scaled_pixmap)
                
                # Update label with size info
                self.preview_image_label.setText(
                    f"Image {self.current_preview_index + 1} of {len(self.extracted_image_files)}: "
                    f"{os.path.basename(image_path)} ({pixmap.width()}×{pixmap.height()}px)"
                )
            else:
                self.image_extractor_preview.setText(f"Failed to load image:\n{os.path.basename(image_path)}")
                
        except Exception as e:
            self.image_extractor_preview.setText(f"Error loading image:\n{str(e)}")
    
    def _on_preview_prev(self):
        """Show previous image in preview"""
        if self.extracted_image_files and self.current_preview_index > 0:
            self.current_preview_index -= 1
            self._update_preview()
    
    def _on_preview_next(self):
        """Show next image in preview"""
        if self.extracted_image_files and self.current_preview_index < len(self.extracted_image_files) - 1:
            self.current_preview_index += 1
            self._update_preview()

    # === IMAGE CONTEXT METHODS ===
    
    def _on_load_image_context_folder(self):
        """Load a folder of figure images for AI translation context"""
        from PyQt6.QtWidgets import QFileDialog

        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Folder with Figure Images",
            "",
            QFileDialog.Option.ShowDirsOnly
        )

        if folder:
            self._load_image_context_from_path(folder, source="user-picked")

    def _guess_extracted_folder(self, extracted_files):
        """v1.10.174: from a list of paths returned by the extractor,
        infer the single 'Images' folder they live in. Returns the
        folder path if all files share one parent directory; None
        otherwise (e.g. when the user did 'Folder' → multiple DOCXes
        with Auto-folder OFF, files might be spread across dirs).
        Used by the post-extract auto-load workflow."""
        if not extracted_files:
            return None
        parents = {os.path.dirname(p) for p in extracted_files if p}
        if len(parents) == 1:
            only = parents.pop()
            if only and os.path.isdir(only):
                return only
        return None

    def _load_image_context_from_path(self, folder, source="user-picked"):
        """Shared implementation for loading an Images folder. Used by
        the manual "Load Images Folder" button AND the post-extract
        auto-load. The `source` parameter only affects the toast / log
        wording: "user-picked" shows a confirmation dialog; "just-
        extracted" is silent (the extraction already showed its own
        success popup, no need for a second one)."""
        if not folder:
            return
        try:
            count = self.figure_context.load_from_folder(folder)

            if count > 0:
                self.image_context_status_label.setText(
                    f"✅ {count} image{'s' if count != 1 else ''} loaded from: {os.path.basename(folder)}"
                )
                self.image_context_status_label.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 11px; padding: 5px;")
                self.image_context_status_label.setToolTip(folder)
                origin = "just-extracted folder" if source == "just-extracted" else folder
                self.log(f"[Image Context] Loaded {count} images from {origin}")

                # v1.10.177: also populate the preview list + image preview
                # pane so the user can actually SEE what they just loaded.
                # Previously only _on_extract_images() did this, so loading
                # an existing folder left the preview area blank.
                try:
                    image_exts = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.tif', '.webp'}
                    discovered = []
                    for name in sorted(os.listdir(folder)):
                        full = os.path.join(folder, name)
                        if os.path.isfile(full) and os.path.splitext(name)[1].lower() in image_exts:
                            discovered.append(full)

                    if discovered and hasattr(self, 'image_extractor_files_list'):
                        self.extracted_image_files = discovered
                        self.current_preview_index = 0

                        self.image_extractor_files_list.clear()
                        for i, file_path in enumerate(discovered, 1):
                            item = QListWidgetItem(f"{i}. {os.path.basename(file_path)}")
                            item.setData(Qt.ItemDataRole.UserRole, file_path)
                            item.setToolTip(file_path)
                            self.image_extractor_files_list.addItem(item)

                        if hasattr(self, 'preview_prev_btn'):
                            self.preview_prev_btn.setEnabled(len(discovered) > 1)
                        if hasattr(self, 'preview_next_btn'):
                            self.preview_next_btn.setEnabled(len(discovered) > 1)

                        self.image_extractor_files_list.setCurrentRow(0)
                        self._update_preview()

                        if hasattr(self, 'image_extractor_status'):
                            self.image_extractor_status.append(
                                f"📁 Loaded {len(discovered)} image{'s' if len(discovered) != 1 else ''} "
                                f"from: {folder}"
                            )
                except Exception as preview_err:
                    self.log(f"[Image Context] Could not populate preview list: {preview_err}")

                # Update Prompt Manager display
                if hasattr(self, 'prompt_manager_qt') and self.prompt_manager_qt:
                    self.prompt_manager_qt.update_image_context_display()

                # No popup on auto-load — the Extraction Complete dialog
                # the user just clicked through already confirms success.
                if source == "user-picked":
                    QMessageBox.information(
                        self,
                        "Images Loaded",
                        f"Successfully loaded {count} figure image{'s' if count != 1 else ''}.\n\n"
                        f"These images will automatically be included with AI translations when the text "
                        f"references figures (e.g., 'Figure 1', 'see fig 2A')."
                    )
            else:
                self.image_context_status_label.setText(self.tr("⚠️ No valid images found in folder"))
                self.image_context_status_label.setStyleSheet("color: #FF9800; font-size: 11px; padding: 5px;")
                if source == "user-picked":
                    QMessageBox.warning(
                        self,
                        "No Images Found",
                        "The selected folder does not contain any valid image files.\n\n"
                        "Supported formats: .png, .jpg, .jpeg, .gif, .bmp, .tiff\n"
                        "Filename examples: 'Figure 1.png', 'fig2a.jpg', 'Fig. 3-B.png'"
                    )
        except Exception as e:
            self.image_context_status_label.setText(f"❌ Error loading images")
            self.image_context_status_label.setStyleSheet("color: #F44336; font-size: 11px; padding: 5px;")
            self.log(f"[Image Context] Error loading images: {e}")
            if source == "user-picked":
                QMessageBox.critical(
                    self,
                    "Error Loading Images",
                    f"Failed to load images from folder:\n\n{str(e)}"
                )
    
    def _on_clear_image_context(self):
        """Clear all loaded image context"""
        if self.figure_context.has_images():
            reply = QMessageBox.question(
                self,
                "Clear Image Context",
                f"Clear {self.figure_context.get_image_count()} loaded image{'s' if self.figure_context.get_image_count() != 1 else ''}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                self.figure_context.clear()
                self.image_context_status_label.setText(self.tr("No images loaded"))
                self.image_context_status_label.setStyleSheet("color: #999; font-size: 11px; padding: 5px;")
                self.log("[Image Context] Cleared all images")
                
                # Update Prompt Manager display
                if hasattr(self, 'prompt_manager_qt') and self.prompt_manager_qt:
                    self.prompt_manager_qt.update_image_context_display()
        else:
            QMessageBox.information(
                self,
                "No Images Loaded",
                "There are no images currently loaded."
            )

    def create_superdocs_tab(self) -> QWidget:
        """Create the Help tab - Online Documentation Viewer"""
        # The embedded docs viewer was removed in favor of online documentation.
        placeholder = QWidget()
        layout = QVBoxLayout(placeholder)
        label = QLabel(self.tr("📚 Supervertaler Help is now available online.\n\nVisit https://docs.supervertaler.com/workbench/ to view the documentation."))
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(label)
        return placeholder

    def create_log_tab(self) -> QWidget:
        """Create the Log tab - Session Log"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Top toolbar with detach button
        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)

        detach_btn = QPushButton(self.tr("🪟 Detach Log Window"))
        detach_btn.setToolTip(self.tr("Open log in a separate window that can be moved to another screen"))
        detach_btn.clicked.connect(self.detach_log_window)
        toolbar_layout.addWidget(detach_btn)

        clear_btn = QPushButton(self.tr("🗑️ Clear Log"))
        clear_btn.setToolTip(self.tr("Clear all log messages"))
        clear_btn.clicked.connect(self.clear_log)
        toolbar_layout.addWidget(clear_btn)

        toolbar_layout.addStretch()
        layout.addWidget(toolbar)

        # Log display area
        self.session_log = QPlainTextEdit()
        self.session_log.setReadOnly(True)
        self.session_log.setStyleSheet("""
            QPlainTextEdit {
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 9px;
                border: 1px solid #ccc;
            }
        """)
        layout.addWidget(self.session_log)

        # Initialize with welcome message
        self.session_log.setPlainText("Session Log - Ready\n" + "="*50 + "\n")

        # List to track detached log windows
        self.detached_log_windows = []

        return tab

    def detach_log_window(self):
        """Create a detached log window"""
        # The list is normally created in create_log_tab() (Settings ▸ Log),
        # but this entry point can fire before Settings has been built, so
        # initialise on demand.
        if not hasattr(self, 'detached_log_windows'):
            self.detached_log_windows = []
        detached_window = DetachedLogWindow(self)
        detached_window.show()
        detached_window.raise_()
        detached_window.activateWindow()
        self.detached_log_windows.append(detached_window)
        self.log("🪟 Log window detached")

    def clear_log(self):
        """Clear the log"""
        reply = QMessageBox.question(
            self,
            "Clear Log",
            "Are you sure you want to clear the log?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.session_log.setPlainText("Session Log - Ready\n" + "="*50 + "\n")
            # Also clear detached windows
            for window in self.detached_log_windows:
                if window and not window.isHidden():
                    window.log_display.setPlainText("Session Log - Ready\n" + "="*50 + "\n")

    def _on_main_tab_changed(self, index: int):
        """Handle main tab changes (Grid/Project resources/Tools/Settings)"""
        try:
            # Lazy-construct the v1.10.0 SuperLookup / Voice
            # top tabs on first activation. Each helper is idempotent –
            # safe to call again on subsequent activations (it short-
            # circuits if already built). Keeps cold start fast by
            # deferring the heavy SuperLookup / Voice widget construction
            # until the user actually visits the tab.
            if hasattr(self, 'superlookup_tab_index') and index == self.superlookup_tab_index:
                self._ensure_superlookup_top_tab()
            elif hasattr(self, 'voice_tab_index') and index == self.voice_tab_index:
                self._ensure_voice_top_tab()

            if index == 0:  # Grid
                # Grid refreshes automatically when segments change
                pass
            elif index == 1:  # Project resources
                # Refresh AI Assistant LLM client when switching to Project resources tab
                # Prompts tab is inside Project resources, so refresh LLM settings when going there
                if hasattr(self, 'prompt_manager_qt'):
                    llm_settings = self.load_llm_settings()
                    self.current_provider = llm_settings.get('provider', 'openai')
                    provider_key = f"{self.current_provider}_model"
                    self.current_model = llm_settings.get(provider_key)
                    # Reinitialize AI Assistant's LLM client
                    self.prompt_manager_qt._init_llm_client()
        except Exception as e:
            self.log(f"⚠️ Error switching main tabs: {e}")
            import traceback
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Phase 1 (issue #199): lazy constructors for the new Workbench
    # top tabs. Sidekick continues to embed its own copies of these
    # widgets during Phases 1-3; duplicate construction is accepted
    # until Phase 4 retires Sidekick.
    # ------------------------------------------------------------------

    def _warm_up_top_tabs(self):
        """Build the new SuperLookup / Voice top tabs in the
        background a few seconds after launch.

        Each ``_ensure_*_top_tab`` is idempotent – calling it here just
        forces eager construction before the user's first hotkey press
        instead of paying the cost on that press. Wrapped in
        per-helper try/except so a failure in one doesn't skip the
        others.

        Also kicks off an AHK pre-warm so the very first synthetic
        keystroke (SuperLookup's copy, a voice command) doesn't lose
        it to AHK's cold-spawn latency (Windows + AV scan can stretch the
        first ``AutoHotkey64.exe`` launch to 1–2 s; subsequent
        launches hit a hot disk cache and run in ~150 ms).
        """
        for helper in ('_ensure_superlookup_top_tab',
                       '_ensure_voice_top_tab'):
            try:
                fn = getattr(self, helper, None)
                if callable(fn):
                    fn()
            except Exception as e:
                self.log(f"⚠ Warm-up of {helper} failed (lazy path still works): {e}")
        try:
            self._prewarm_ahk()
        except Exception as e:
            self.log(f"⚠ AHK pre-warm failed (first paste may be slow): {e}")

    def _prewarm_ahk(self):
        """Spawn AHK once with a no-op script so the binary is in disk
        cache and the first real Ctrl+V / Ctrl+C from
        :class:`CrossPlatformKeySender` doesn't take an extra second
        on cold disk. No-op when AHK isn't installed or we're not on
        Windows. Runs in a background thread so it doesn't block the
        warm-up timer.
        """
        import sys as _sys
        if _sys.platform != 'win32':
            return
        import threading

        def _do_prewarm():
            try:
                from modules.platform_helpers import CrossPlatformKeySender
                ahk = CrossPlatformKeySender()._find_ahk()
                if not ahk:
                    return
                import subprocess as _sp
                import tempfile as _tf
                # Empty script body; just exits. AHK's spin-up cost
                # is paid here so the first user-triggered paste is fast.
                with _tf.NamedTemporaryFile(
                        mode='w', suffix='.ahk', delete=False,
                        encoding='utf-8') as f:
                    f.write('ExitApp\n')
                    tmp = f.name
                try:
                    _sp.run([ahk, '/ErrorStdOut', tmp],
                            timeout=10,
                            creationflags=getattr(_sp, 'CREATE_NO_WINDOW', 0))
                finally:
                    import os as _os
                    try:
                        _os.unlink(tmp)
                    except OSError:
                        pass
            except Exception as e:
                print(f"[AHK pre-warm] {e}")

        t = threading.Thread(target=_do_prewarm, daemon=True,
                             name="AHK-prewarm")
        t.start()

    def _ensure_superlookup_top_tab(self):
        """Build the SuperLookup top tab the first time the user opens it.

        Heavy: SuperlookupTab.__init__ brings up TM / termbase / MT and
        web-resource state. Deferring until first activation keeps cold
        start fast. The hotkey-side-effect SuperLookup instance from
        _setup_superlookup_hotkeys already owns the global hotkeys; this
        separate instance powers the visible top tab and passes
        register_hotkeys=False so it does NOT re-attempt registration
        (which used to fail with a misleading "claimed by another app"
        warning against our own owner instance).
        """
        if self._superlookup_top_widget is not None:
            return
        try:
            widget = SuperlookupTab(
                self,
                user_data_path=getattr(self, 'user_data_path', None),
                register_hotkeys=False,
            )
            self._superlookup_top_widget = widget
            placeholder = self.main_tabs.widget(self.superlookup_tab_index)
            layout = QVBoxLayout(placeholder)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(widget)
        except Exception as e:
            self.log(f"⚠ Could not build SuperLookup top tab: {e}")
            import traceback
            traceback.print_exc()

    def _ensure_voice_top_tab(self):
        """Build the Voice top tab on first activation.

        The widget (modules.voice_tab.VoiceTab) reads parent_app
        properties (shortcut_manager, voice_listener,
        load_dictation_settings).
        """
        if self._voice_top_widget is not None:
            return
        try:
            from modules.voice_tab import VoiceTab
            widget = VoiceTab(self)
            self._voice_top_widget = widget
            placeholder = self.main_tabs.widget(self.voice_tab_index)
            layout = QVBoxLayout(placeholder)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(widget)
        except Exception as e:
            self.log(f"⚠ Could not build Voice top tab: {e}")
            import traceback
            traceback.print_exc()
    
    def detach_superlookup(self):
        """Detach Superlookup into a separate window for second screen use"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QPushButton
        
        try:
            # If already detached, just show the window
            if hasattr(self, 'lookup_detached_window') and self.lookup_detached_window and self.lookup_detached_window.isVisible():
                self.lookup_detached_window.raise_()
                self.lookup_detached_window.activateWindow()
                return
            
            # Create detached window
            self.lookup_detached_window = QDialog(self)
            self.lookup_detached_window.setWindowTitle(self.tr("🔍 SuperLookup - Supervertaler Workbench"))
            self.lookup_detached_window.setMinimumSize(600, 700)
            self.lookup_detached_window.resize(700, 800)
            
            # Set window flags to ensure it appears as a proper window
            self.lookup_detached_window.setWindowFlags(
                Qt.WindowType.Window | 
                Qt.WindowType.WindowCloseButtonHint | 
                Qt.WindowType.WindowMinimizeButtonHint |
                Qt.WindowType.WindowMaximizeButtonHint
            )
            
            # Position window to the right of main window, or center on same screen if no space
            main_geometry = self.geometry()
            
            # Get the screen that contains the main window (handles multi-monitor setups)
            app = QApplication.instance()
            screen = app.screenAt(main_geometry.center())
            if not screen:
                # Fallback to primary screen
                screen = app.primaryScreen()
            
            screen_geometry = screen.geometry()
            
            # Try to place to the right of main window
            new_x = main_geometry.right() + 20
            new_y = main_geometry.top()
            
            # If window would go off-screen to the right, center it on the main window's screen
            if new_x + 700 > screen_geometry.right():
                # Center horizontally on screen, place above main window
                new_x = screen_geometry.left() + (screen_geometry.width() - 700) // 2
                new_y = max(screen_geometry.top() + 50, main_geometry.top() - 100)
                
            # Ensure window stays on screen
            new_x = max(screen_geometry.left() + 10, min(new_x, screen_geometry.right() - 710))
            new_y = max(screen_geometry.top() + 10, min(new_y, screen_geometry.bottom() - 810))
            
            self.lookup_detached_window.move(int(new_x), int(new_y))
            
            # Make sure window is raised and activated - use QTimer to ensure it's after window is shown
            QTimer.singleShot(100, lambda: (
                self.lookup_detached_window.raise_(),
                self.lookup_detached_window.activateWindow(),
                self.lookup_detached_window.setFocus()
            ))
            
            layout = QVBoxLayout(self.lookup_detached_window)
            layout.setContentsMargins(10, 10, 10, 10)
            layout.setSpacing(5)
            
            # Header with reattach button
            header_layout = QVBoxLayout()
            
            header_title = QLabel(self.tr("🔍 SuperLookup"))
            header_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #333;")
            header_layout.addWidget(header_title)
            
            button_layout = QVBoxLayout()
            reattach_btn = QPushButton(self.tr("📥 Attach to Main Window"))
            reattach_btn.setToolTip(self.tr("Re-attach SuperLookup to the Home tab"))
            reattach_btn.setStyleSheet("font-size: 9pt; padding: 4px 12px; max-width: 200px;")
            reattach_btn.clicked.connect(self.reattach_superlookup)
            button_layout.addWidget(reattach_btn, alignment=Qt.AlignmentFlag.AlignRight)
            header_layout.addLayout(button_layout)
            
            layout.addLayout(header_layout)
            
            # Create new Superlookup instance for detached window
            # Or move the existing one - better to create new to avoid widget parenting issues.
            # register_hotkeys=False: the hidden owner instance (self.lookup_tab)
            # already holds the global hotkeys; a detached instance re-registering
            # would just collide with it and log a spurious warning.
            detached_lookup = SuperlookupTab(
                self.lookup_detached_window,
                user_data_path=self.user_data_path,
                register_hotkeys=False,
            )
            
            # Explicitly copy theme_manager reference
            detached_lookup.theme_manager = self.theme_manager
            
            # Copy TM database reference if available
            if hasattr(self, 'tm_database') and self.tm_database:
                detached_lookup.tm_database = self.tm_database
                if detached_lookup.engine:
                    detached_lookup.engine.set_tm_database(self.tm_database)
            
            # Copy home lookup state if it exists
            if hasattr(self, 'home_lookup_widget') and self.home_lookup_widget:
                # Copy source text
                source_text = self.home_lookup_widget.source_text.toPlainText()
                detached_lookup.source_text.setPlainText(source_text)
                
                # Copy TM database reference
                if hasattr(self.home_lookup_widget, 'tm_database'):
                    detached_lookup.tm_database = self.home_lookup_widget.tm_database
            
            layout.addWidget(detached_lookup, stretch=1)
            
            # Store reference for cleanup
            self.lookup_detached_widget = detached_lookup
            
            # Handle window close
            def on_close():
                self.lookup_detached_window = None
                self.lookup_detached_widget = None
            
            self.lookup_detached_window.finished.connect(on_close)
            
            # Show window (non-modal)
            self.lookup_detached_window.setWindowModality(Qt.WindowModality.NonModal)
            self.lookup_detached_window.show()
            self.log("Superlookup detached to separate window")
        except Exception as e:
            import traceback
            error_msg = f"Error detaching Superlookup: {str(e)}\n{traceback.format_exc()}"
            self.log(error_msg)
            QMessageBox.warning(self, "Error", f"Could not detach Superlookup:\n{str(e)}")
    
    def reattach_superlookup(self):
        """Re-attach Superlookup to the Home tab"""
        if not self.lookup_detached_window:
            return
        
        # Copy state back to home widget if it exists
        if (hasattr(self, 'home_lookup_widget') and self.home_lookup_widget and 
            hasattr(self, 'lookup_detached_widget') and self.lookup_detached_widget):
            # Copy source text
            source_text = self.lookup_detached_widget.source_text.toPlainText()
            self.home_lookup_widget.source_text.setPlainText(source_text)
        
        # Close detached window
        self.lookup_detached_window.close()
        self.lookup_detached_window = None
        if hasattr(self, 'lookup_detached_widget'):
            self.lookup_detached_widget = None
        
        self.log("Superlookup re-attached to Home tab")
    
    # create_resources_tab() was removed in v1.9.422.
    # TMs and Termbases used to share a wrapper "Resources" tab on the
    # main_tabs strip with their own inner QTabWidget (the resources_tabs
    # widget). Both have been promoted to peers of Editor / AI / Tools /
    # Settings on main_tabs directly – construction now calls
    # create_translation_memories_tab() and create_termbases_tab()
    # directly. The wrapper widget and its method are gone.

    def _setup_superlookup_hotkeys(self):
        """Construct the (hidden) SuperlookupTab so its __init__ registers
        the global hotkeys (Ctrl+Alt+L, Ctrl+Alt+Q, Alt+K).

        Pre-v1.9.467 this lived inside create_specialised_tools_tab(),
        which also built the embedded Tools-tab UI. The Tools tab has
        been retired; the SuperlookupTab construction stays because the
        global hotkey registration happens as a side-effect of __init__.

        The Voice tab itself is built separately (_ensure_voice_top_tab).
        """
        lookup_tab = SuperlookupTab(self, user_data_path=self.user_data_path)
        lookup_tab.hide()  # Never shown – purely for hotkey side effects
        self.lookup_tab = lookup_tab

        if hasattr(lookup_tab, 'hotkey_registered') and lookup_tab.hotkey_registered:
            failed = getattr(getattr(lookup_tab, '_hotkey_manager', None), 'failed_hotkeys', [])
            if failed:
                self.log(f"⚠ Global hotkeys: some failed to register: {', '.join(failed)}")
            else:
                self.log("⌨ Global hotkeys registered (Ctrl+Alt+L, Ctrl+Alt+Q, Alt+K)")
        else:
            self.log("⚠ Global hotkeys NOT registered – check console for errors")
    
    def _show_language_variant_dialog(self, compat_info: dict) -> str:
        """Show dialog for handling language variant mismatch. Returns 'import_strip', 'create_new', or 'cancel'"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QRadioButton, QButtonGroup
        
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Language Variant Detected"))
        dialog.setMinimumWidth(500)
        
        layout = QVBoxLayout(dialog)
        
        # Explanation
        info_text = (
            f"<h3>⚠️ Language Variant Mismatch</h3>"
            f"<p>The TMX file uses language variants that don't exactly match your TM:</p>"
            f"<table style='margin: 10px 0;'>"
            f"<tr><td><b>TMX languages:</b></td><td>{compat_info['tmx_source']} → {compat_info['tmx_target']}</td></tr>"
            f"<tr><td><b>Your TM languages:</b></td><td>{compat_info['target_source']} → {compat_info['target_target']}</td></tr>"
            f"</table>"
            f"<p>How would you like to proceed?</p>"
        )
        
        info_label = QLabel(info_text)
        info_label.setWordWrap(True)
        info_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info_label)
        
        # Radio button group
        button_group = QButtonGroup(dialog)
        
        option1 = CheckmarkRadioButton(f"Import into existing TM (strip variants: {compat_info['tmx_source']},{compat_info['tmx_target']} → {compat_info['target_source']},{compat_info['target_target']})")
        option1.setChecked(True)
        option1.setToolTip(self.tr("Import translations by matching base languages, ignoring regional variants"))
        button_group.addButton(option1, 1)
        layout.addWidget(option1)
        
        option2 = CheckmarkRadioButton(f"Create new TM with variant languages ({compat_info['tmx_source']}, {compat_info['tmx_target']})")
        option2.setToolTip(self.tr("Create a separate TM preserving the exact language variants from the TMX"))
        button_group.addButton(option2, 2)
        layout.addWidget(option2)
        
        layout.addSpacing(20)
        
        # Buttons
        from PyQt6.QtWidgets import QHBoxLayout
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)
        
        ok_btn = QPushButton(self.tr("Continue"))
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(ok_btn)
        
        layout.addLayout(btn_layout)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if option1.isChecked():
                return 'import_strip'
            elif option2.isChecked():
                return 'create_new'
        
        return 'cancel'

    # Friendly names for the formatting tags users recognise; anything else
    # (Okapi structural codes like g1/x1) is reported under its own name.
    _TAG_FRIENDLY_NAMES = {
        'b': 'bold', 'strong': 'bold',
        'i': 'italic', 'em': 'italic',
        'u': 'underline',
        'cf': 'character formatting',
        'sub': 'subscript', 'sup': 'superscript',
    }

    # Matches CAT inline formatting tags: <b>, </b>, <i>, <cf …>, <g1>,
    # </g1>, <x1/>, <bpt …>, etc. A literal "<" in body text (e.g. "a < b",
    # "<=") isn't a tag because it isn't immediately followed by an optional
    # "/" and a letter, so it is left intact.
    _INLINE_TAG_RE = re.compile(r'</?[A-Za-z][^<>]*?/?>')

    # ──────────────────────────────────────────────────────────────────
    #  Post-export word-count safeguard
    # ──────────────────────────────────────────────────────────────────
    # After writing a translated DOCX we count the words actually present
    # in the file and compare against the words we expected to write (the
    # sum over segments of the target text, falling back to source for
    # untranslated segments). A large shortfall means text was silently
    # dropped on the round-trip — exactly the failure mode where a
    # multi-segment Okapi text unit lost its 2nd+ sentences. This is a
    # coarse, tolerance-based check: tags, numbers and punctuation are
    # counted identically on both sides, so a real drop (e.g. 72 missing
    # segments) shows up as a clear ratio shortfall while incidental
    # differences stay within tolerance.

    # Warn when the exported file has fewer than this fraction of the
    # expected words. Overridable via settings.json → "export" section.
    EXPORT_WORD_COUNT_DEFAULT_THRESHOLD = 0.95
    
    def backup_all_resources(self):
        """Back up every TM (TMX) and termbase (TSV) in the database (issue #52)."""
        if not getattr(self, 'db_manager', None) or not getattr(self, 'tm_metadata_mgr', None) \
                or not getattr(self, 'termbase_mgr', None):
            QMessageBox.warning(self, self.tr("Back Up Resources"),
                                self.tr("The resource database is not available."))
            return
        folder = QFileDialog.getExistingDirectory(
            self, self.tr("Choose a folder for the backup"), str(Path.home()))
        if not folder:
            return

        from PyQt6.QtWidgets import QProgressDialog
        from modules.resource_backup import backup_all_resources
        progress = QProgressDialog(self.tr("Backing up resources…"), None, 0, 0, self)
        progress.setWindowTitle(self.tr("Back Up Resources"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        def step(message):
            progress.setLabelText(f"Backing up {message}…")
            QApplication.processEvents()

        try:
            summary = backup_all_resources(self.db_manager, self.tm_metadata_mgr,
                                           self.termbase_mgr, folder,
                                           tool_version=__version__, progress=step)
        except Exception as e:
            progress.close()
            self.log(f"✗ Resource backup failed: {e}")
            QMessageBox.critical(self, self.tr("Back Up Resources"),
                                 f"The backup failed:\n\n{e}")
            return
        progress.close()

        tus = sum(n for _name, _file, n in summary['tms'])
        terms = sum(t[2] for t in summary['termbases'])
        self.log(f"✓ Backed up {len(summary['tms'])} TMs ({tus:,} entries) and "
                 f"{len(summary['termbases'])} termbases ({terms:,} terms) to {summary['folder']}")
        text = (f"Backed up {len(summary['tms'])} TM(s) with {tus:,} entries and "
                f"{len(summary['termbases'])} termbase(s) with {terms:,} terms to:\n\n"
                f"{summary['folder']}")
        if summary['errors']:
            text += "\n\nProblems:\n" + "\n".join(summary['errors'][:10])
        box = QMessageBox(QMessageBox.Icon.Warning if summary['errors'] else QMessageBox.Icon.Information,
                          self.tr("Back Up Resources"), text, parent=self)
        open_btn = box.addButton(self.tr("Open Folder"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(summary['folder']))
    
    def create_segmentation_rules_tab(self):
        """Settings → Segmentation Rules (issue #191): line-break and built-in
        rule switches, extra abbreviations, and SRX-style custom break /
        exception rules with SRX import/export and a live test box."""
        from modules.segmentation_rules_widget import SegmentationRulesWidget
        widget = SegmentationRulesWidget(self._load_segmentation_rules, self._save_segmentation_rules)
        set_help_topic(widget, HelpTopics.SETTINGS_SEGMENTATION)  # F1 opens its help page
        return widget

    def _load_segmentation_rules(self):
        from modules.segmentation_rules import SETTINGS_KEY, SegmentationRules
        try:
            return SegmentationRules.from_dict(self.load_general_settings().get(SETTINGS_KEY))
        except Exception as e:
            self.log(f"⚠ Could not read the segmentation rules: {e}")
            return SegmentationRules()

    def _save_segmentation_rules(self, rules):
        from modules.segmentation_rules import SETTINGS_KEY
        settings = self.load_general_settings()
        settings[SETTINGS_KEY] = rules.to_dict()
        self.save_general_settings(settings)

    def _create_inline_codes_tab(self):
        """Settings → Inline Codes (issue #194): user-defined placeholder
        patterns treated like inline tags. Also activates the saved patterns,
        as the Settings pages are built at start-up."""
        from modules.inline_codes_widget import InlineCodesWidget
        entries = self._load_inline_codes()
        _inline_codes.set_active(entries)
        widget = InlineCodesWidget(lambda: entries, self._save_inline_codes)
        set_help_topic(widget, HelpTopics.SETTINGS_INLINE_CODES)  # F1 opens its help page
        return widget

    def _load_inline_codes(self) -> list:
        try:
            return _inline_codes.normalise(self.load_general_settings().get(_inline_codes.SETTINGS_KEY))
        except Exception as e:
            self.log(f"⚠ Could not read the inline code patterns: {e}")
            return []

    def _save_inline_codes(self, entries: list):
        settings = self.load_general_settings()
        settings[_inline_codes.SETTINGS_KEY] = entries
        self.save_general_settings(settings)
        _inline_codes.set_active(entries)
        # Re-colour the grid with the new patterns (source column 2, target column 3)
        table = getattr(self, 'table', None)
        if table is not None:
            for row in range(table.rowCount()):
                for col in (2, 3):
                    widget = table.cellWidget(row, col)
                    highlighter = getattr(widget, 'highlighter', None)
                    if highlighter is not None:
                        highlighter.rehighlight()

    def _make_sentence_segmenter(self, markdown: bool = False):
        """The sentence segmenter for text Supervertaler splits itself, with
        the user's rules from Settings → Segmentation Rules."""
        from modules.simple_segmenter import MarkdownSegmenter, SimpleSegmenter
        return (MarkdownSegmenter if markdown else SimpleSegmenter)(self._load_segmentation_rules())

    def _detect_and_merge_synonyms(self, source_text: str, target_text: str,
                                   target_termbases: list,
                                   source_synonyms: list = None,
                                   target_synonyms: list = None):
        """Trados-parity "Similar Term Found" check across the given termbases.

        Detects entries that share the new term's source (but a different
        target) or its target (but a different source) and, if any are found,
        prompts the user to fold the new term into the existing entry as a
        synonym rather than create a near-duplicate.

        Returns ``(decision, merged_tb_ids)``:
          * ``'cancel'``     – user aborted; caller must add nothing.
          * ``'keep_both'``  – no conflict, or user chose to keep both; caller
                               inserts normally into every termbase.
          * ``'merged'``     – the new term (and any supplied synonyms) were
                               added as synonyms to the matched entries in
                               ``merged_tb_ids``; caller must skip those
                               termbases and insert only into the rest.

        When there are no conflicts the method returns ``('keep_both', set())``
        WITHOUT showing any dialog, so the quick-add paths stay silent and fast
        in the common case.
        """
        if not target_termbases or not getattr(self, 'termbase_mgr', None):
            return ('keep_both', set())

        # Gather matches across all destination termbases, orienting each
        # term to its termbase's storage direction first (same as add_term).
        all_matches = []
        for tb in target_termbases:
            src_term, tgt_term, _sl, _tl = self._orient_term_for_termbase(
                source_text, target_text, tb)
            tb_was_swapped = (src_term != source_text)
            for row in self.termbase_mgr.find_merge_matches(
                    tb['id'], src_term, tgt_term):
                all_matches.append({
                    'term_id': row['term_id'],
                    'source_term': row['source_term'],
                    'target_term': row['target_term'],
                    'match_type': row['match_type'],
                    'termbase_id': tb['id'],
                    'termbase_name': tb.get('name', ''),
                    'tb_was_swapped': tb_was_swapped,
                    'oriented_src': src_term,
                    'oriented_tgt': tgt_term,
                })

        if not all_matches:
            return ('keep_both', set())

        from modules.merge_prompt_dialog import MergePromptDialog
        dlg = MergePromptDialog(self, all_matches, source_text, target_text)
        dlg.exec()
        choice = dlg.choice

        if choice == MergePromptDialog.CANCEL:
            return ('cancel', set())
        if choice == MergePromptDialog.KEEP_BOTH:
            return ('keep_both', set())

        # SYNONYM or EDIT: fold the new term into each matched entry. The
        # language tag passed to add_synonym is relative to termbase storage,
        # so when the termbase is reverse of the project the project-source
        # text belongs in the target column and vice versa.
        merged_ids = set()
        for m in all_matches:
            try:
                if m['match_type'] == 'source':
                    ok = self.termbase_mgr.add_synonym(
                        m['term_id'], m['oriented_tgt'], language='target')
                else:
                    ok = self.termbase_mgr.add_synonym(
                        m['term_id'], m['oriented_src'], language='source')
                if ok:
                    self.log(
                        f"  ✓ Merged as synonym into '{m['termbase_name']}' "
                        f"(entry #{m['term_id']})")
                # Also fold in any synonyms the user typed in the add dialog.
                syn_source_lang = 'target' if m['tb_was_swapped'] else 'source'
                syn_target_lang = 'source' if m['tb_was_swapped'] else 'target'
                for syn in (source_synonyms or []):
                    self.termbase_mgr.add_synonym(
                        m['term_id'], syn['text'], language=syn_source_lang,
                        display_order=syn.get('order', 0),
                        forbidden=syn.get('forbidden', False))
                for syn in (target_synonyms or []):
                    self.termbase_mgr.add_synonym(
                        m['term_id'], syn['text'], language=syn_target_lang,
                        display_order=syn.get('order', 0),
                        forbidden=syn.get('forbidden', False))
                merged_ids.add(m['termbase_id'])
            except Exception as e:
                self.log(
                    f"  ✗ Failed to merge synonym into "
                    f"'{m['termbase_name']}': {e}")

        # "Add & Edit" – open the term entry editor on the first matched entry.
        if choice == MergePromptDialog.EDIT and all_matches:
            first = all_matches[0]
            try:
                from modules.termbase_entry_editor import TermbaseEntryEditor
                editor = TermbaseEntryEditor(
                    self,
                    db_manager=self.db_manager,
                    termbase_id=first['termbase_id'],
                    term_id=first['term_id'],
                )
                editor.exec()
            except Exception as e:
                self.log(f"✗ Could not open term entry editor for review: {e}")

        return ('merged', merged_ids)
    
    # ------------------------------------------------------------------
    # Termbase DB file watcher (v1.10.69)
    # ------------------------------------------------------------------
    # Background:
    # Workbench's TermLens / source-cell highlighting is driven by an
    # in-memory ``termbase_index`` built once per project load (the
    # v1.9.182 perf optimisation that took 365s → <1s for 349
    # segments). When the same SQLite database is also open in the
    # Supervertaler for Trados plugin and the user deletes / adds
    # terms there, Workbench's index goes stale — TermLens keeps
    # showing deleted pills and misses newly-added ones until the
    # user presses the v1.10.68 🔄 refresh button.
    #
    # This block makes that refresh **automatic** via QFileSystemWatcher.
    # Design choices that matter for the "don't misfire" requirement
    # the user called out explicitly:
    #
    #  1. **Debounce**: any DB-file modification restarts a single-
    #     shot 2-second timer. The rebuild only runs once the file
    #     has been quiet for 2 s, so a batch of own-writes (e.g. an
    #     INSERT followed by SELECT-verify followed by activation-
    #     table update) collapses into one rebuild.
    #
    #  2. **Snapshot gating**: even after debounce fires, we don't
    #     blindly rebuild. We snapshot the termbases / termbase_terms
    #     state (row counts + MAX(id) + MAX(modified_date)) at the
    #     end of every ``_build_termbase_index`` call, and at fire
    #     time we re-snapshot and compare. If nothing changed since
    #     the last rebuild — e.g. the file mtime ticked because of a
    #     TM write or a project metadata save, not a termbase edit —
    #     we skip. So routine non-termbase DB writes don't cause a
    #     visible TermLens flicker.
    #
    #  3. **Own-write integration**: every code path that modifies
    #     termbases already calls ``_post_termbase_delete_refresh``
    #     (add / delete / quick-add etc.), which in turn calls
    #     ``_build_termbase_index`` which updates the snapshot. So
    #     own-writes update the snapshot synchronously; when the
    #     watcher debounce later fires for that same write, the
    #     snapshot comparison sees no change and skips. Zero
    #     spurious rebuilds for own-writes.
    #
    #  4. **Defensive**: setup is wrapped in try/except. If the
    #     QFileSystemWatcher fails (network drive, OneDrive, exotic
    #     filesystem), the failure is logged and the manual 🔄
    #     button still works.
    #
    # Wired in by ``_setup_termbase_db_watcher`` which is called once
    # the db_manager + current_project are available (from the
    # project-load chain at the bottom of ``_load_project_data``).

    def add_word_to_dictionary_shortcut(self):
        """Add word at cursor position to custom dictionary (Alt+D shortcut)
        
        Finds the misspelled word at the current cursor position and adds it to the
        custom dictionary. Works when focus is in a grid target cell.
        """
        # Get currently focused widget
        focused_widget = QApplication.focusWidget()
        
        # Check if we're in an editable grid cell
        if not isinstance(focused_widget, EditableGridTextEditor):
            self.statusBar().showMessage(
                f"{format_shortcut_for_display('Alt+D')}: Place cursor on a misspelled word in the target cell first",
                3000,
            )
            return
        
        # Get cursor position
        cursor = focused_widget.textCursor()
        
        # Try to find misspelled word at cursor
        word_info = focused_widget._get_misspelled_word_at_cursor(cursor)
        
        if word_info[0] is None:
            # No misspelled word found at cursor
            self.statusBar().showMessage("No misspelled word at cursor position", 3000)
            return
        
        word, start_pos, end_pos = word_info
        
        # Add to dictionary
        focused_widget._add_to_dictionary(word)
        
        # Status message already shown by _add_to_dictionary

    def add_text_to_non_translatables(self, text: str):
        """Add the selected source-cell text as a non-translatable termbase entry.

        Stores the text on a writable activated termbase with
        ``is_nontranslatable = 1``, mirroring the Trados plugin's
        approach. Picks the project termbase if one is set; otherwise
        the first writable activated termbase. Falls back to a clear
        error message rather than silently doing nothing.
        """
        if not text or not text.strip():
            QMessageBox.warning(self, "No Text", "Please select text before adding to non-translatables.")
            return

        text = text.strip()

        if not hasattr(self, 'current_project') or not self.current_project:
            QMessageBox.warning(self, "No project", "Open a project first.")
            return

        if not hasattr(self, 'termbase_mgr') or not self.termbase_mgr:
            QMessageBox.critical(self, "Error", "Termbase manager not initialised.")
            return

        project_id = self.current_project.id if hasattr(self.current_project, 'id') else None
        if not project_id:
            QMessageBox.warning(self, "No project ID", "Save the project before adding non-translatables.")
            return

        # Pick a target termbase: project termbase first, otherwise first
        # writable activated termbase.
        target_termbase = None
        all_termbases = self.termbase_mgr.get_all_termbases()
        for tb in all_termbases:
            if not self.termbase_mgr.is_termbase_active(tb['id'], project_id):
                continue
            if tb.get('read_only', True):
                continue
            priority = self.termbase_mgr.get_termbase_priority(tb['id'], project_id)
            if priority == 1:
                target_termbase = tb
                break
        if target_termbase is None:
            for tb in all_termbases:
                if not self.termbase_mgr.is_termbase_active(tb['id'], project_id):
                    continue
                if not tb.get('read_only', True):
                    target_termbase = tb
                    break

        if target_termbase is None:
            QMessageBox.warning(
                self,
                "No writable termbase",
                "Activate at least one writable termbase for this project "
                "before marking text as non-translatable.\n\n"
                "On the 🏷️ Termbases tab, tick the Read and Write boxes."
            )
            return

        # Use language codes that match the project so the index can
        # orient correctly when the termbase is reverse-direction.
        source_lang = self.current_project.source_lang if self.current_project else None
        target_lang = self.current_project.target_lang if self.current_project else None
        source_lang_code = self._convert_language_to_code(source_lang) if source_lang else None
        target_lang_code = self._convert_language_to_code(target_lang) if target_lang else None

        try:
            term_id = self.termbase_mgr.add_term(
                termbase_id=target_termbase['id'],
                source_term=text,
                target_term=text,  # NT: target = source (Trados convention)
                source_lang=source_lang_code,
                target_lang=target_lang_code,
                notes="Added as non-translatable from grid",
                is_nontranslatable=True,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add non-translatable: {e}")
            return

        if term_id is None:
            self.statusBar().showMessage(
                f"⚠ '{text}' is already in '{target_termbase['name']}'", 3000
            )
            return

        self._play_sound_effect('glossary_term_added') if hasattr(self, '_play_sound_effect') else None
        self.log(f"🚫 Added non-translatable to '{target_termbase['name']}': {text}")
        self.statusBar().showMessage(
            f"🚫 Added non-translatable: '{text}' → {target_termbase['name']}", 3000
        )

        # Rebuild the in-memory term index so the new NT highlights immediately
        # in the current segment and onward, rather than waiting for next reload.
        try:
            self._build_termbase_index()
        except Exception as e:
            self.log(f"⚠ Could not rebuild termbase index: {e}")

        # Refresh highlighting for the current segment.
        if hasattr(self, 'table') and self.table is not None:
            current_row = self.table.currentRow()
            if current_row >= 0 and self.current_project and current_row < len(self.current_project.segments):
                segment = self.current_project.segments[current_row]

                # Refresh the source cell highlighting
                source_widget = self.table.cellWidget(current_row, 2)
                if source_widget and hasattr(source_widget, 'highlight_termbase_matches'):
                    source_text = segment.source  # v1.9.306: use clean segment text, not display text
                    nt_matches = self.find_nt_matches_in_source(source_text)
                    if nt_matches and hasattr(source_widget, 'highlight_non_translatables'):
                        source_widget.highlight_non_translatables(nt_matches)
                
                # Trigger panel refresh
                self._last_selected_row = -1
                self.on_cell_selected(current_row, self.table.currentColumn(), -1, -1)
    
    # ========================================================================
    # TRANSLATION MEMORY HELPER METHODS
    # ========================================================================
    
    # ========================================================================
    # SETTINGS TAB
    # ========================================================================
    
    # Fixed content width for every settings tab. Content is left-anchored
    # against the sidebar; anything past this width sits as breathing room
    # on the right. 1000 px is roughly the content area of the Trados
    # Studio Options dialog and keeps label–control rows comfortable
    # without feeling oversized on wide monitors.
    SETTINGS_CONTENT_MAX_WIDTH = 1000

    def _wrap_in_scroll(self, widget):
        """Wrap a settings-tab widget in a scroll area with consistent layout.

        Three things in one place so every settings tab gets them for free:

        1. **Wheel guard** – prevents hovered sliders / spinboxes / combo
           boxes from silently absorbing mouse-wheel events meant for the
           scroll area. See ``_WheelGuard``.
        2. **Left-anchor + max-width cap** – wraps ``widget`` in a horizontal
           layout with the content on the left and a stretch on the right,
           capped at ``SETTINGS_CONTENT_MAX_WIDTH`` px. Stops settings rows
           from sprawling across ultra-wide monitors while avoiding the
           "centred in a sea of grey" look that bare centring produces.
        3. **Scroll area** – the obvious one.
        """
        from PyQt6.QtWidgets import QSlider, QAbstractSpinBox, QComboBox

        # Left-anchor + width cap. Skip if the caller already wrapped its
        # own content (legacy: AI Settings used to do this inline). We
        # detect that by checking whether `widget` itself already has a
        # QHBoxLayout with a stretch – a heuristic that's good enough for
        # the small number of legacy callers.
        host = widget
        if not getattr(widget, '_settings_left_anchored', False):
            host = QWidget()
            outer = QHBoxLayout(host)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)
            # Pin both min and max to the same width so every tab claims
            # exactly the same column regardless of how wide its longest
            # internal row would naturally make it. Without the min, a tab
            # with short rows would sit narrower than its neighbours, and
            # clicking between tabs would feel jumpy.
            widget.setMinimumWidth(self.SETTINGS_CONTENT_MAX_WIDTH)
            widget.setMaximumWidth(self.SETTINGS_CONTENT_MAX_WIDTH)
            outer.addWidget(widget, 0)
            outer.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(host)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        # Wheel guard: one instance per scroll area, attached so it's not GC'd.
        if not hasattr(self, '_settings_wheel_guards'):
            self._settings_wheel_guards = []
        guard = _WheelGuard(scroll)
        self._settings_wheel_guards.append(guard)
        for cls in (QSlider, QAbstractSpinBox, QComboBox):
            for w in widget.findChildren(cls):
                w.installEventFilter(guard)
                # Demote WheelFocus → StrongFocus so wheel doesn't take focus.
                w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        return scroll

    def _update_tools_sidebar_theme(self):
        """Update the tools sidebar styling based on the current theme."""
        if not hasattr(self, 'modules_tabs'):
            return
        from modules.settings_sidebar import SettingsSidebar
        if not isinstance(self.modules_tabs, SettingsSidebar):
            return
        is_dark = (hasattr(self, 'theme_manager') and self.theme_manager
                   and 'dark' in self.theme_manager.current_theme.name.lower())
        if is_dark:
            self.modules_tabs.apply_dark_style()
        else:
            self.modules_tabs._apply_style()

    def _update_resources_sidebar_theme(self):
        """No-op: resources tab now uses standard QTabWidget (themed automatically)."""
        pass

    # _create_quicktrans_stub_tab and _open_quicktrans_in_sidekick
    # were removed in v1.10.10. Pre-retirement they advertised that
    # QuickTrans settings lived inside Sidekick and provided a button
    # to navigate there; with Sidekick gone the real settings widget
    # is now mounted directly in Workbench Settings → QuickTrans by
    # _create_workbench_settings_tab (TAB 5), via the canonical
    # _create_mt_quick_lookup_settings_tab() factory.

    def _populate_voice_commands_table(self):
        """Refresh the Voice command table in Sidekick (if open).

        Called whenever the underlying voice_command_manager has changed
        (commands added, edited, removed, or reset).
        """
        if not hasattr(self, 'voice_command_manager'):
            return
        # Refresh the in-Workbench Voice top tab's command table if
        # it's been built (it's lazy-constructed on first activation).
        # Pre-v1.10.4 this also refreshed Sidekick's voice tab; with
        # Sidekick retired only the top tab needs poking.
        voice_widget = getattr(self, '_voice_top_widget', None)
        if voice_widget is not None and hasattr(voice_widget, '_populate_table'):
            try:
                voice_widget._populate_table()
            except Exception:
                pass

    def _reset_voice_commands(self):
        """Reset voice commands to defaults"""
        reply = QMessageBox.question(
            self, "Reset Commands",
            "Reset all voice commands to defaults?\n\nThis will remove any custom commands you've added.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.voice_command_manager.commands = self.voice_command_manager._default_commands()
            self.voice_command_manager.save_commands()
            self._populate_voice_commands_table()
            QMessageBox.information(self, "Reset Complete", "Voice commands have been reset to defaults.")

    def _check_ahk_installed(self) -> str:
        """Check if AutoHotkey is installed"""
        ahk_exe = self.voice_command_manager._find_ahk_executable() if hasattr(self, 'voice_command_manager') else None
        if ahk_exe:
            return "✅ AutoHotkey detected"
        else:
            return "⚠️ AutoHotkey not found - install from autohotkey.com for system commands"

    def _open_voice_scripts_folder(self):
        """Open the voice scripts folder"""
        import subprocess
        scripts_folder = self.user_data_path / "workbench" / "voice_scripts"
        scripts_folder.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(['explorer', str(scripts_folder)])

    def _toggle_alwayson_listening(self):
        """Toggle always-on voice listening mode"""
        if self.voice_listener and self.voice_listener.is_listening:
            # Stop listening
            self.voice_listener.stop()
            self.voice_listener = None
            self._update_alwayson_ui("stopped")
            self.log("🔇 Always-on listening stopped")
            self.status_bar.showMessage("🔇 Always-on listening stopped", 3000)
        else:
            # Start listening
            try:
                # The voice settings still live under the historical
                # ``dictation_settings`` key in the unified settings file.
                voice_settings = self.load_dictation_settings()
                self.voice_listener = ContinuousVoiceListener(
                    command_manager=self.voice_command_manager,
                    user_data_path=str(self.user_data_path) if hasattr(self, 'user_data_path') else None,
                    mic_device=voice_settings.get('mic_device'),
                )

                # Set sensitivity from persisted settings (the Voice tab
                # writes this key directly via _set_voice_settings).
                sensitivity = voice_settings.get('alwayson_sensitivity', 'medium')
                if sensitivity in ('low', 'medium', 'high'):
                    self.voice_listener.set_sensitivity(sensitivity)

                # Connect signals
                self.voice_listener.speech_detected.connect(self._on_alwayson_speech)
                self.voice_listener.command_detected.connect(self._on_alwayson_command)
                self.voice_listener.no_command_matched.connect(self._on_alwayson_no_match)
                self.voice_listener.status_update.connect(self._on_alwayson_status)
                self.voice_listener.error_occurred.connect(self._on_alwayson_error)
                self.voice_listener.vad_status_changed.connect(self._on_alwayson_vad_status)
                self.voice_listener.listening_started.connect(lambda: self._update_alwayson_ui("listening"))
                self.voice_listener.listening_stopped.connect(lambda: self._update_alwayson_ui("stopped"))
                self.voice_listener.stopped_unexpectedly.connect(self._on_alwayson_listener_died)

                self.voice_listener.start()
                self.log("🎧 Always-on listening started (Vosk – offline, commands only)")

            except Exception as e:
                import traceback
                self.log(f"❌ Failed to start always-on listening: {e}")
                self.log(traceback.format_exc())
                QMessageBox.critical(self, "Always-On Error", f"Failed to start always-on listening:\n\n{e}")

    def _update_alwayson_ui(self, status: str):
        """Update the always-on UI elements"""
        # Update settings panel UI (if visible)
        if hasattr(self, 'alwayson_status_label'):
            if status == "listening" or status == "waiting":
                self.alwayson_status_label.setText(self.tr("🟢 Listening for speech..."))
                self.alwayson_status_label.setStyleSheet("font-size: 9pt; padding: 5px; color: #2E7D32;")
                self.alwayson_toggle_btn.setText(self.tr("⏹️ Stop Always-On Listening"))
                self.alwayson_toggle_btn.setStyleSheet("padding: 8px 15px; background-color: #FFCDD2;")
            elif status == "recording":
                self.alwayson_status_label.setText(self.tr("🔴 Recording..."))
                self.alwayson_status_label.setStyleSheet("font-size: 9pt; padding: 5px; color: #C62828;")
            elif status == "processing":
                self.alwayson_status_label.setText(self.tr("⏳ Processing..."))
                self.alwayson_status_label.setStyleSheet("font-size: 9pt; padding: 5px; color: #F57C00;")
            else:  # stopped or other
                self.alwayson_status_label.setText(self.tr("⚪ Not active"))
                self.alwayson_status_label.setStyleSheet("font-size: 9pt; padding: 5px;")
                self.alwayson_toggle_btn.setText(self.tr("▶️ Start Always-On Listening"))
                self.alwayson_toggle_btn.setStyleSheet("padding: 8px 15px;")
        
        # Update status bar indicator (always visible when active)
        if hasattr(self, 'alwayson_indicator_label'):
            if status == "listening" or status == "waiting":
                self.alwayson_indicator_label.setText(self.tr("🎤 ALWAYS-ON"))
                self.alwayson_indicator_label.setStyleSheet("font-size: 11px; font-weight: bold; color: #2E7D32; background-color: #C8E6C9; padding: 2px 6px; border-radius: 3px;")
                self.alwayson_indicator_label.setToolTip(self.tr("Always-on voice listening ACTIVE\nClick to stop"))
                self.alwayson_indicator_label.show()
            elif status == "recording":
                self.alwayson_indicator_label.setText(self.tr("🔴 REC"))
                self.alwayson_indicator_label.setStyleSheet("font-size: 11px; font-weight: bold; color: white; background-color: #C62828; padding: 2px 6px; border-radius: 3px;")
                self.alwayson_indicator_label.setToolTip(self.tr("Recording speech..."))
                self.alwayson_indicator_label.show()
            elif status == "processing":
                self.alwayson_indicator_label.setText("⏳ ...")
                self.alwayson_indicator_label.setStyleSheet("font-size: 11px; font-weight: bold; color: #F57C00; background-color: #FFF3E0; padding: 2px 6px; border-radius: 3px;")
                self.alwayson_indicator_label.setToolTip(self.tr("Processing speech..."))
                self.alwayson_indicator_label.show()
            else:  # stopped or other
                self.alwayson_indicator_label.hide()
        
        # Update grid toolbar button (if exists)
        if hasattr(self, 'grid_alwayson_btn'):
            if status == "listening" or status == "waiting":
                self.grid_alwayson_btn.setText(self.tr("🎧 Always-On: ON"))
                self.grid_alwayson_btn.setChecked(True)
                self.grid_alwayson_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #2E7D32;
                        color: white;
                        font-weight: bold;
                        padding: 3px 5px;
                        border-radius: 3px;
                    }
                    QPushButton:checked {
                        background-color: #2E7D32;
                    }
                """)
            elif status == "recording":
                self.grid_alwayson_btn.setText(self.tr("🔴 REC"))
                self.grid_alwayson_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #C62828;
                        color: white;
                        font-weight: bold;
                        padding: 3px 5px;
                        border-radius: 3px;
                    }
                    QPushButton:checked {
                        background-color: #C62828;
                    }
                """)
            elif status == "processing":
                self.grid_alwayson_btn.setText("⏳ ...")
                self.grid_alwayson_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #F57C00;
                        color: white;
                        font-weight: bold;
                        padding: 3px 5px;
                        border-radius: 3px;
                    }
                    QPushButton:checked {
                        background-color: #F57C00;
                    }
                """)
            else:  # stopped or other
                self.grid_alwayson_btn.setText(self.tr("🎧 Always-On: OFF"))
                self.grid_alwayson_btn.setChecked(False)
                self.grid_alwayson_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #757575;
                        color: white;
                        font-weight: bold;
                        padding: 3px 5px;
                        border-radius: 3px;
                    }
                    QPushButton:checked {
                        background-color: #2E7D32;
                    }
                """)

        # Update the in-Workbench Voice top tab's status indicator if
        # it has been built (lazy on first activation). Pre-v1.10.4
        # this also poked Sidekick's voice widget; Sidekick is gone.
        voice_widget = getattr(self, '_voice_top_widget', None)
        if voice_widget is not None and hasattr(voice_widget, 'set_alwayson_status'):
            try:
                voice_widget.set_alwayson_status(status)
            except Exception:
                pass

        # System tray icon – visible only while Always-On is active.
        # Gives the user a persistent "the mic is hot" signal even when
        # Workbench is hidden.
        self._update_alwayson_tray_icon(status)

    @staticmethod
    def _draw_mic_icon(color):
        """Draw a small microphone glyph as a QIcon, in the given QColor.

        Used by the Always-On tray indicator. Two visual states share the
        same shape but differ in colour: grey when Always-On is off, red
        when it's actively listening. Drawing programmatically avoids
        bundling an extra asset and keeps colour-swaps trivial.
        """
        from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QBrush
        size = 32
        pix = QPixmap(size, size)
        pix.fill(QColor(0, 0, 0, 0))
        p = QPainter(pix)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            qcol = QColor(color) if not isinstance(color, QColor) else color
            stroke = max(1.5, size * 0.06)

            # Mic body – vertical capsule centred horizontally
            body_w = size * 0.42
            body_h = size * 0.50
            body_x = (size - body_w) / 2
            body_y = size * 0.10
            p.setBrush(QBrush(qcol))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(
                int(body_x), int(body_y), int(body_w), int(body_h),
                int(body_w / 2), int(body_w / 2),
            )

            # U-shaped pickup arc cradling the body
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(qcol, stroke, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            arc_x = size * 0.16
            arc_y = size * 0.30
            arc_w = size * 0.68
            arc_h = size * 0.50
            # Qt uses 1/16ths of a degree; 0° is at 3 o'clock, going CCW.
            p.drawArc(int(arc_x), int(arc_y), int(arc_w), int(arc_h),
                      200 * 16, 140 * 16)

            # Stand (vertical) + base (horizontal)
            cx = size / 2
            p.drawLine(int(cx), int(size * 0.80), int(cx), int(size * 0.90))
            p.drawLine(int(cx - size * 0.18), int(size * 0.90),
                       int(cx + size * 0.18), int(size * 0.90))
        finally:
            p.end()
        return QIcon(pix)

    def _ensure_alwayson_tray_icon(self):
        """Lazily create the QSystemTrayIcon used as a global Always-On
        toggle and visual state indicator.

        A small microphone glyph drawn programmatically – grey when
        Always-On is off, red when actively listening. Convention follows
        OBS / Zoom / Discord (grey = ready/off, red = live).

        The icon is created once and stays visible for the lifetime of
        the Workbench window. Showing/hiding a tray slot causes Windows
        to reflow the tray (the "icons bounce" effect), so the slot
        stays allocated and only the pixmap, tooltip, and menu wording
        change as state changes.

        Single-click toggles Always-On. Right-click shows a small menu
        with the same toggle plus an "Open Voice in Sidekick"
        shortcut.
        """
        if hasattr(self, '_alwayson_tray_icon'):
            return
        from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
        from PyQt6.QtGui import QColor

        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._alwayson_tray_icon = None
            return

        # Material grey 600 (off) and red 800 (live).
        self._alwayson_tray_icon_normal = self._draw_mic_icon(QColor(0x75, 0x75, 0x75))
        self._alwayson_tray_icon_red = self._draw_mic_icon(QColor(0xC6, 0x28, 0x28))

        tray = QSystemTrayIcon(self._alwayson_tray_icon_normal, self)
        tray.setToolTip(self.tr("Voice – Always-On is OFF. Click to start."))

        menu = QMenu(self)
        toggle_action = menu.addAction("▶ Start Always-On")
        toggle_action.triggered.connect(self._toggle_alwayson_listening)
        menu.addSeparator()
        open_action = menu.addAction("🎤 Open Voice")
        open_action.triggered.connect(self._open_voice_in_workbench)
        tray.setContextMenu(menu)
        self._alwayson_tray_toggle_action = toggle_action

        tray.activated.connect(self._on_alwayson_tray_activated)
        self._alwayson_tray_icon = tray
        tray.show()

    def _on_alwayson_tray_activated(self, reason):
        """Tray-icon click handler – single-click toggles Always-On."""
        from PyQt6.QtWidgets import QSystemTrayIcon
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_alwayson_listening()

    def _update_alwayson_tray_icon(self, status: str):
        """Switch the tray icon between inactive/active visuals based on
        the current listener status. The icon stays visible at all times
        – only the pixmap, tooltip, and menu wording change."""
        self._ensure_alwayson_tray_icon()
        tray = getattr(self, '_alwayson_tray_icon', None)
        if tray is None:
            return

        active = status in ("listening", "waiting", "recording", "processing")

        # Skip every tray API call when the active/inactive state hasn't changed.
        # setIcon() and setToolTip() both trigger a Windows notification-area
        # repaint that briefly hides the icon. The VAD cycle emits three status
        # changes per spoken command (recording → processing → listening), all of
        # which map to the same "active" state, so we can safely ignore them.
        if getattr(self, '_alwayson_tray_active', None) == active:
            return
        self._alwayson_tray_active = active

        tray.setIcon(
            self._alwayson_tray_icon_red if active
            else self._alwayson_tray_icon_normal
        )
        tray.setToolTip(
            "Voice – Always-On listening. Click to stop." if active
            else "Voice – Always-On is OFF. Click to start."
        )
        action = getattr(self, '_alwayson_tray_toggle_action', None)
        if action is not None:
            action.setText("⏹ Stop Always-On" if active else "▶ Start Always-On")

    def _on_alwayson_speech(self, text: str):
        """Handle raw speech from always-on listener"""
        self.log(f"🎤 Heard: {text}")

    def _on_alwayson_command(self, heard: str, result: str):
        """A command ran (or failed to): log it, flash it in the status
        bar and list it under "What I heard" on the Voice tab."""
        self.log(f"🎤 Command: {heard} → {result}")
        self.status_bar.showMessage(f"🎤 {result}", 3000)
        self._add_to_voice_heard_list(heard, result, ok=not result.startswith("✗"))

    def _on_alwayson_no_match(self, text: str):
        """Speech from the always-on listener that matched no command.

        It is reported and never typed. Voice only runs commands;
        running text belongs to a dictation tool (Wispr Flow, Windows
        voice typing). Up to v1.10.373 such speech was typed into the
        active app, so Vosk's leftovers ("[unk] project tm next add
        [unk]") landed in whatever window had focus.
        """
        self.log(f"💬 No command matched, not typed: {text!r}")
        self.status_bar.showMessage(f"🎤 No command matched: “{text}”", 3000)
        self._add_to_voice_heard_list(text, "no command matched", ok=None)

    def _add_to_voice_heard_list(self, heard: str, outcome: str, ok):
        """Feed the Voice tab's "What I heard" list, if the tab is built."""
        voice_widget = getattr(self, '_voice_top_widget', None)
        if voice_widget is not None:
            voice_widget.add_heard(heard, outcome, ok)

    def _on_alwayson_status(self, message: str):
        """Handle status updates from always-on listener"""
        self.log(message)

    def _on_alwayson_error(self, error: str):
        """Handle errors from always-on listener"""
        self.log(f"❌ Always-on error: {error}")
        QMessageBox.warning(self, "Always-On Error", f"Voice listener error:\n\n{error}")

    def _on_alwayson_listener_died(self):
        """The listener stopped without being asked to; it has already
        reported why through _on_alwayson_error and set the UI to
        stopped through listening_stopped."""
        self.log("🔇 Always-on listener stopped unexpectedly")

    def _on_alwayson_vad_status(self, status: str):
        """Handle VAD status changes (loading/waiting/recording/processing)."""
        self._update_alwayson_ui(status)

    def _toggle_alwayson_from_statusbar(self):
        """Toggle always-on listening when clicking the status bar indicator"""
        self._toggle_alwayson_listening()

    def _toggle_alwayson_from_grid_btn(self, checked: bool, btn: QPushButton):
        """Toggle always-on listening from the grid toolbar button"""
        self._toggle_alwayson_listening()
        # Button state will be updated by _update_alwayson_ui

    def _open_voice_in_workbench(self):
        """Open Workbench's Voice top tab from a Settings link / tray menu.

        Method was renamed from _open_voice_in_sidekick in v1.10.10
        when Sidekick was retired. The two existing call sites
        (Voice settings link and Always-On tray menu) were updated
        to match.
        """
        try:
            if hasattr(self, '_ensure_voice_top_tab'):
                self._ensure_voice_top_tab()
            self._bring_workbench_forward()
            if hasattr(self, 'voice_tab_index') and hasattr(self, 'main_tabs'):
                self.main_tabs.setCurrentIndex(self.voice_tab_index)
        except Exception as e:
            QMessageBox.warning(self, "Could not open Voice", str(e))

    def _bring_workbench_forward(self):
        """Bring Workbench to the foreground.

        On Windows the bare show/raise/activateWindow trio isn't
        enough to win the foreground race against another app the
        user has been working in (Trados is a known-aggressive
        host). We escalate through the full documented
        foreground-grab chain: synthetic Alt-key press to satisfy
        SetForegroundWindow's "Alt pressed" exception, then
        AttachThreadInput dance, then BringWindowToTop +
        SetForegroundWindow + SwitchToThisWindow. See v1.10.9
        changelog for the full history of this fix.
        """
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

        import sys
        if sys.platform == 'win32':
            # v1.10.9: belt + braces + suspenders. Three earlier
            # versions (v1.10.6 deferred the button click, v1.10.7
            # reordered ensure vs. bring, v1.10.8 split work across
            # event-loop turns) still left the first Ctrl+Alt+C from
            # a heavy host like Trados painting Workbench *behind*
            # Trados despite the SetForegroundWindow call returning
            # success. That's classic Windows foreground-stealing
            # prevention: the OS accepts the call but quietly refuses
            # to actually bring our window forward, leaving it
            # flashing in the taskbar with no visible activation.
            #
            # The canonical fix is the "Alt key" trick. Windows
            # documents an exception in the foreground-allowed rules
            # (SetForegroundWindow MSDN page): if the Alt key is
            # currently pressed at the moment SetForegroundWindow is
            # called, the OS grants the request unconditionally.
            # By synthesising a brief Alt down + Alt up *immediately
            # before* the call, we satisfy that rule even when our
            # process has no other claim to foreground rights. The
            # Alt key is released within microseconds so the user
            # never sees side effects (no menu activation etc.).
            #
            # Plus three additional hammers, applied in order:
            #   - BringWindowToTop() forces Z-order to the top of
            #     the same-thread-group stack.
            #   - SetForegroundWindow() requests the actual
            #     foreground change. With Alt held + thread input
            #     attached, the OS now accepts it.
            #   - SwitchToThisWindow() is a deprecated-but-functional
            #     API that bypasses some foreground rules entirely;
            #     used as a last-resort hammer for stubborn cases
            #     (Trados being one).
            try:
                import ctypes
                user32 = ctypes.windll.user32
                kernel32 = ctypes.windll.kernel32
                hwnd = int(self.winId())

                # Step 1: Synthetic Alt + F24 chord to satisfy
                # SetForegroundWindow's "Alt key pressed" exception
                # without triggering Qt's QMenuBar menu-activation.
                #
                # v1.10.9 used a bare Alt down/up here. That works
                # for the foreground grab, but Windows + Qt also
                # interpret a *naked* Alt tap (Alt down → Alt up
                # with nothing in between) as "user wants to
                # activate the menu bar". Even though the synthetic
                # tap was meant for the originating foreground app,
                # the AttachThreadInput dance below shares input
                # queues, so the Alt event leaks into Workbench's
                # input stream just as we become foreground. Qt's
                # QMenuBar then activates File-menu navigation and
                # the user's arrow keys steer the menu instead of
                # the Clipboard list / SuperLookup results
                # (v1.10.14 tried to fix this post-hoc by calling
                # menuBar().setActiveAction(None) and setFocus()
                # on the target widget, but that ran *before* Qt
                # processed the queued Alt event, so it cleared
                # state that hadn't been set yet).
                #
                # v1.10.15 inserts a synthetic F24 between the Alt
                # down and Alt up. VK_F24 (0x87) is a defined VK
                # code that no app binds to – it exists in the
                # Windows VK enum mostly for keyboards with extended
                # function-key rows that nobody ships any more. Qt's
                # QMenuBar treats Alt+anything as a chord, not a
                # naked Alt tap, and skips menu activation. The
                # synthetic F24 itself reaches no listener (nothing
                # binds it). The Alt key still satisfies
                # SetForegroundWindow's exception because it's
                # pressed at the moment SetForegroundWindow is
                # called.
                VK_MENU = 0x12
                VK_F24 = 0x87
                KEYEVENTF_KEYUP = 0x0002
                user32.keybd_event(VK_MENU, 0, 0, 0)               # Alt down
                user32.keybd_event(VK_F24, 0, 0, 0)                # F24 down (chord)
                user32.keybd_event(VK_F24, 0, KEYEVENTF_KEYUP, 0)  # F24 up
                user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0) # Alt up

                # Step 2: AttachThreadInput dance.
                fg = user32.GetForegroundWindow()
                fg_thread = user32.GetWindowThreadProcessId(fg, None)
                our_thread = kernel32.GetCurrentThreadId()
                attached = False
                if fg and fg_thread and fg_thread != our_thread:
                    attached = bool(
                        user32.AttachThreadInput(fg_thread, our_thread, True)
                    )

                # Step 3: Force Z-order to top, then request
                # foreground, then the deprecated-but-effective
                # SwitchToThisWindow hammer.
                try:
                    user32.BringWindowToTop(hwnd)
                    user32.SetForegroundWindow(hwnd)
                    # SwitchToThisWindow(hwnd, fAltTab=True) – second
                    # arg makes the call behave like an Alt+Tab
                    # switch which is more reliable than the bare
                    # foreground-request path.
                    user32.SwitchToThisWindow(hwnd, True)
                finally:
                    if attached:
                        user32.AttachThreadInput(fg_thread, our_thread, False)
            except Exception as e:
                print(f"[Workbench] foreground escalation failed: {e}")

        # v1.10.14: the synthetic Alt-tap above gets the foreground
        # grant we need, but it's also what Windows interprets as
        # "user pressed Alt to activate the menu bar". Even though
        # we sent the Alt to the *originating* foreground (Trados),
        # AttachThreadInput shares input queues for the duration, so
        # the Alt event leaks into Workbench's input stream just as
        # we become foreground. Qt's QMenuBar then activates File-
        # menu navigation mode and the user's arrow keys steer the
        # menu instead of the Clipboard list / SuperLookup results.
        # Dismiss the leaked menu state explicitly so we land in the
        # expected "neutral" focus state.
        self._dismiss_menu_activation()

        # v1.10.344: VERIFY the grab actually took, a beat later. All
        # the hammers above can still lose: BringWindowToTop /
        # SwitchToThisWindow change the Z-ORDER (the window looks
        # summoned) while Windows quietly refuses the KEYBOARD-focus
        # transfer. In that state mouse clicks work (clicking
        # self-activates) but Esc / arrow keys go to the app the user
        # came from – experienced as "Esc does absolutely nothing".
        # Verification + one hardened retry converts that silent
        # failure into either a working window or a logged warning.
        try:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(120, self._verify_foreground_grab)
        except Exception:
            pass

    def _verify_foreground_grab(self):
        """~120 ms after a summon: confirm Workbench really owns the
        keyboard focus; if not, retry via the hardened
        ``activate_foreground_window`` (AttachThreadInput + ALT-nudge,
        verified, up to 3 attempts) and log honestly if Windows still
        refuses – so a focus-less summon is never a silent mystery."""
        import sys
        if sys.platform != 'win32':
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = int(self.winId())
            if user32.GetForegroundWindow() == hwnd:
                self._repair_null_keyboard_focus(user32, hwnd)
                return
            from modules.platform_helpers import activate_foreground_window
            if activate_foreground_window(hwnd):
                print("[Workbench] foreground grab needed a verified retry "
                      "(2nd attempt succeeded)")
                self._repair_null_keyboard_focus(user32, hwnd)
                return
        except Exception as e:
            print(f"[Workbench] foreground verification failed: {e}")
            return
        msg = ("⚠ Windows refused to hand Workbench keyboard focus after "
               "the summon – Esc / arrow keys may still act on the "
               "previous app. Click once inside Workbench to fix.")
        print(f"[Workbench] {msg}")
        try:
            self.log(msg)
        except Exception:
            pass

    def _repair_null_keyboard_focus(self, user32, hwnd):
        """Repair the NULL-keyboard-focus state AttachThreadInput can
        leave behind (v1.10.347, the "Esc does nothing when Workbench
        runs elevated" fix).

        The foreground-grab dance attaches our input queue to the old
        foreground thread's and detaches again. A documented side
        effect: the detach can leave OUR queue's raw focus window NULL
        – the window received WM_ACTIVATE (Qt reports active), it IS
        the foreground window, mouse clicks work, but the OS has no
        focus HWND to deliver WM_KEYDOWN to, so every keystroke
        (Esc, arrows, Enter) silently evaporates before Qt sees it.
        Observed specifically when Workbench runs elevated (matching
        an elevated Trados); regular runs take a luckier path.

        GetFocus()/SetFocus operate on the CALLING thread's queue, and
        this runs on the Qt GUI thread that owns the window – exactly
        the thread whose queue needs the repair.
        """
        try:
            if user32.GetFocus():
                return  # some window of ours has focus – healthy
            from ctypes import wintypes
            user32.SetFocus.argtypes = [wintypes.HWND]
            user32.SetFocus(hwnd)
            try:
                self.log("[Focus] keyboard focus was NULL after the "
                         "foreground grab – repaired via SetFocus")
            except Exception:
                pass
        except Exception as e:
            print(f"[Workbench] NULL-focus repair failed: {e}")

    def _dismiss_menu_activation(self):
        """Clear any leaked menu-bar activation after a foreground
        grab. Called from _bring_workbench_forward(); safe to call
        when no menu is active (no-op in that case). The continuation
        callback (`_continue_superlookup`)
        also explicitly setFocus() on its target widget, which
        belt-and-braces clears the menu-activation indirectly – but
        calling setActiveAction(None) here makes the cleanup
        deterministic instead of relying on focus-change side
        effects."""
        try:
            mb = self.menuBar() if hasattr(self, 'menuBar') else None
            if mb is not None:
                mb.setActiveAction(None)
        except Exception:
            pass

    def open_workbench_to_superlookup(self, text: str = ""):
        """Bring Workbench forward, switch to the SuperLookup top tab,
        and optionally seed the search field with ``text``.

        Wired to Ctrl+Alt+L in v1.10.1 (Phase 2 of issue #199). Replaces
        the legacy ``Sidekick.show_superlookup(text)`` route for the
        global hotkey path; Sidekick's own copy still works for users
        who summon Sidekick via Ctrl+Alt+K.
        """
        try:
            # v1.10.7: bring Workbench forward *before* the lazy-tab
            # ensure.
            # v1.10.8: that wasn't enough on a cold first run. Even
            # with bring-forward called first, the SetForegroundWindow
            # call only *queues* WM_ACTIVATE / WM_PAINT for our
            # window – Workbench doesn't visibly become active until
            # Qt's event loop processes those messages. If we then
            # synchronously call _ensure_superlookup_top_tab() and
            # block the GUI thread for 1–2s building the widget, the
            # queued activation events sit waiting; meanwhile Windows
            # may decide to re-evaluate foreground or simply never
            # repaint Workbench as the active window because Qt hasn't
            # acknowledged the WM_ACTIVATE. The user keeps seeing
            # their source app the whole time.
            #
            # Fix: split the work across two event-loop turns.
            #   Turn 1 (now):   _bring_workbench_forward()
            #   Turn 2 (next):  ensure + setCurrentIndex + seed/click
            # QTimer.singleShot(0, …) schedules the continuation for
            # the next iteration of Qt's main loop, between which the
            # activation/paint events get processed and Workbench
            # actually appears on top before we start blocking the
            # GUI thread on widget construction.
            self._bring_workbench_forward()
            from PyQt6.QtCore import QTimer

            def _continue_superlookup():
                try:
                    if hasattr(self, '_ensure_superlookup_top_tab'):
                        self._ensure_superlookup_top_tab()
                    if hasattr(self, 'superlookup_tab_index') and hasattr(self, 'main_tabs'):
                        self.main_tabs.setCurrentIndex(self.superlookup_tab_index)
                    widget = getattr(self, '_superlookup_top_widget', None)
                    if widget is not None:
                        # v1.10.14: explicit focus on the search field
                        # (or the tab root if the search field isn't
                        # available) so keyboard navigation lands here
                        # instead of leaking into the menu bar after
                        # the foreground-grab Alt-tap.
                        try:
                            if hasattr(widget, 'source_text') and widget.source_text is not None:
                                widget.source_text.setFocus()
                            else:
                                widget.setFocus()
                        except Exception:
                            pass
                    if widget is not None and text:
                        try:
                            if hasattr(widget, 'source_text'):
                                widget.source_text.setEditText(text)
                            if hasattr(widget, 'search_btn'):
                                # Still defer the click by another
                                # 150ms past the ensure so the search
                                # doesn't peg the GUI thread before
                                # the tab finishes painting.
                                QTimer.singleShot(150, widget.search_btn.click)
                        except Exception as inner:
                            self.log(f"⚠ Could not seed SuperLookup search: {inner}")
                except Exception as inner:
                    self.log(f"⚠ Could not finish SuperLookup open: {inner}")

            QTimer.singleShot(0, _continue_superlookup)
        except Exception as e:
            self.log(f"⚠ Could not open SuperLookup top tab: {e}")
            import traceback
            traceback.print_exc()

    def reload_global_hotkeys(self):
        """Re-register the global hotkey set with current ShortcutManager values.

        Called after the user changes a ``global_*`` shortcut in
        Settings → Keyboard Shortcuts. Without this call, the OS-level
        hotkey listener keeps firing on the *old* key until the next
        application restart, because pynput / WinAPI registrations don't
        track ShortcutManager state. Idempotent – ``register_global_hotkey``
        stops any previously running manager before binding fresh keys.
        """
        lookup_tab = getattr(self, 'lookup_tab', None)
        if lookup_tab is None or not hasattr(lookup_tab, 'register_global_hotkey'):
            return
        try:
            lookup_tab.register_global_hotkey()
        except Exception as e:
            self.log(f"⚠ Could not reload global hotkeys: {e}")

    def _update_status_bar_fonts(self, scale_percent: int):
        """Update status bar label fonts based on scale percentage"""
        base_size = int(9 * scale_percent / 100)
        small_size = max(7, base_size)
        style = f"font-size: {small_size}pt;"

        # Update all status bar labels if they exist
        for attr_name in ['segment_count_label', 'file_label', 'tm_status_label',
                          'termbase_status_label', 'source_lang_label', 'target_lang_label']:
            if hasattr(self, attr_name):
                label = getattr(self, attr_name)
                if label is not None:
                    label.setStyleSheet(style)

    def _update_main_tabs_fonts(self, scale_percent: int):
        """Update main tab bar and settings sidebar fonts based on scale percentage"""
        base_size = int(10 * scale_percent / 100)
        if hasattr(self, 'main_tabs') and self.main_tabs is not None:
            self.main_tabs.tabBar().setStyleSheet(f"font-size: {base_size}pt;")
        # Also scale the sidebar list fonts (Settings + Tools)
        from modules.settings_sidebar import SettingsSidebar
        if hasattr(self, 'settings_tabs') and isinstance(self.settings_tabs, SettingsSidebar):
            self.settings_tabs.set_font_size(base_size)
        if hasattr(self, 'modules_tabs') and isinstance(self.modules_tabs, SettingsSidebar):
            self.modules_tabs.set_font_size(base_size)

    def _get_global_ui_font_scale(self) -> int:
        """Get the current global UI font scale percentage"""
        general_settings = self.load_general_settings()
        # Check new key first, fall back to old key for migration
        return general_settings.get('global_ui_font_scale',
                                    general_settings.get('settings_ui_font_scale', 100))

    def _get_ui_chrome_compact(self) -> bool:
        """True when Compact UI chrome is enabled (tighter menu bar and tab strip padding).

        Default changed from False → True in v1.9.386: compact is now the shipped look.
        Users who have explicitly set it False keep that setting; only users who have
        never touched it get the new default on next launch.
        """
        general_settings = self.load_general_settings()
        return bool(general_settings.get('compact_ui_chrome', True))

    def _apply_ui_chrome_compactness(self, compact: bool, persist: bool = True):
        """Apply or remove compact-chrome styling on menu bar, main tabs, and right tabs.

        Saves the new state to general settings by default; pass persist=False for
        non-interactive re-application on startup.
        """
        compact = bool(compact)

        if persist:
            general_settings = self.load_general_settings()
            general_settings['compact_ui_chrome'] = compact
            self.save_general_settings(general_settings)

        main_tab_padding = "3px 12px" if compact else "8px 15px"
        right_tab_padding_rule = (
            f"QTabBar::tab {{ padding: 3px 10px; outline: 0; }}"
            if compact
            else "QTabBar::tab { outline: 0; }"
        )
        menu_item_padding = "2px 8px" if compact else ""

        if hasattr(self, 'main_tabs') and self.main_tabs is not None:
            self.main_tabs.setStyleSheet(
                f"QTabBar::tab {{ padding: {main_tab_padding}; outline: 0; }}\n"
                "QTabBar::tab:focus { outline: none; }\n"
                "QTabBar::tab:selected {\n"
                "    border-bottom: 1px solid #2196F3;\n"
                "    background-color: rgba(33, 150, 243, 0.08);\n"
                "}"
            )

        if hasattr(self, 'right_tabs') and self.right_tabs is not None:
            self.right_tabs.setStyleSheet(
                f"{right_tab_padding_rule} "
                "QTabBar::tab:focus { outline: none; } "
                "QTabBar::tab:selected { border-bottom: 1px solid #2196F3; "
                "background-color: rgba(33, 150, 243, 0.08); }"
            )

        menu_bar = self.menuBar() if hasattr(self, 'menuBar') else None
        if menu_bar is not None:
            menu_bar.setStyleSheet(
                f"QMenuBar::item {{ padding: {menu_item_padding}; }}" if menu_item_padding else ""
            )

        try:
            self.log(f"✓ UI chrome: {'compact' if compact else 'default'}")
        except Exception:
            pass
    
    # Pagination methods
    # Documents at or below this segment count open with every segment shown
    # ("All"); larger ones fall back to PAGE_FALLBACK_SIZE per page so the
    # initial widget-construction cost stays bounded. As of v1.10.147 the
    # grid is page-aware and only builds full QTextEdit widgets for rows on
    # the current page (off-page rows get cheap placeholders and are
    # populated lazily on navigation), so this also controls how many heavy
    # widgets get constructed at load time — not just how many rows are
    # visible. Lowered in v1.10.148 from 2000/500 → 1000/200 after profiling
    # showed that "All" mode on a 479-segment SDLXLIFF spent ~27 s in the
    # per-row widget-creation loop and another ~9 s re-applying fonts. Users
    # who prefer the single-page UX on bigger projects can still switch via
    # the "Per page" selector.
    PAGE_AUTO_ALL_THRESHOLD = 1000
    PAGE_FALLBACK_SIZE = 200

    # ------------------------------------------------------------------
    # v1.10.152: idle-time next-page prefetch
    # ------------------------------------------------------------------
    # After every pagination change we schedule the *following* page to be
    # populated quietly in the background, in small batches separated by
    # QTimer hops so the main thread (typing, AI calls, scrolling) stays
    # responsive. Qt requires widget creation on the main thread, so a real
    # worker thread can't help here — but yielding between micro-batches
    # via QTimer.singleShot gives the same user-visible effect: the next
    # page is ready by the time the user asks for it.
    #
    # The generation counter invalidates an in-flight prefetch as soon as
    # any new pagination/filter/project event would change "what's next" —
    # so we never populate stale rows or fight with a foreground populate.
    # ------------------------------------------------------------------

    BACKGROUND_POPULATE_BATCH_SIZE = 10  # rows per batch
    BACKGROUND_POPULATE_BATCH_DELAY_MS = 50  # gap between batches
    BACKGROUND_POPULATE_INITIAL_DELAY_MS = 750  # wait before first batch
    
    def _widget_is_alive(self, widget: Optional[QWidget]) -> bool:
        """Return True if the underlying Qt object still exists."""
        if widget is None:
            return False
        try:
            return not sip.isdeleted(widget)
        except RuntimeError:
            return False

    # --------------------------------------------------------------------
    # v1.10.161: label-based tab lookup
    # --------------------------------------------------------------------
    # Several places in the codebase used to navigate to tabs by hard-coded
    # integer index (e.g. ``main_tabs.setCurrentIndex(4)`` with a comment
    # claiming that was the Settings tab). Every time a new top-level tab
    # was inserted between AI and Settings (SuperLookup, Clipboard
    # Manager, Voice all arrived between v1.9 and v1.10), the index in the
    # *comment* stayed correct while the *code* silently started landing
    # on whatever tab now occupied that slot — at the time of writing this
    # audit, all the "Settings" hard-codes were silently landing on
    # SuperLookup, and the "MT Settings sub-tab=3" hard-code was landing
    # on Voice (MT had moved to index 5).
    #
    # These helpers replace integer lookups with label-substring lookups,
    # so future tab insertions can never silently re-break navigation.
    # Returns True on success so callers can detect a missing tab.
    # --------------------------------------------------------------------

    def _switch_main_tab(self, label_contains: str) -> bool:
        """Switch the top-level tab bar to the first tab whose label
        contains ``label_contains`` (case-sensitive substring match).
        Returns True on success, False if no matching tab was found.
        """
        if not hasattr(self, 'main_tabs') or self.main_tabs is None:
            return False
        for i in range(self.main_tabs.count()):
            if label_contains in self.main_tabs.tabText(i):
                self.main_tabs.setCurrentIndex(i)
                return True
        return False

    def _get_line_edit_text(self, attr_name: str) -> str:
        """Safely fetch text from a shared QLineEdit, handling deleted widgets."""
        widget = getattr(self, attr_name, None)
        if not self._widget_is_alive(widget):
            setattr(self, attr_name, None)
            return ""
        return widget.text()

    def _ensure_shared_filter(self, attr_name: str, placeholder: str,
                               on_change: Optional[Callable] = None,
                               on_return: Optional[Callable] = None) -> QLineEdit:
        """Create (or recreate) shared filter widgets that may be destroyed during layout swaps."""
        widget = getattr(self, attr_name, None)
        if not self._widget_is_alive(widget):
            widget = QLineEdit()
            if on_change is not None:
                widget.textChanged.connect(on_change)
            if on_return is not None:
                widget.returnPressed.connect(on_return)
            setattr(self, attr_name, widget)
        widget.setPlaceholderText(placeholder)
        widget.setToolTip(
            "Shows segments containing this text (not case-sensitive).\n"
            "Put it between slashes for a regular expression, e.g. /pump\\s+hous(ing|e)/")
        return widget

    def _ensure_primary_filters_ready(self):
        """Make sure the grid filters exist before programmatic use."""
        self.source_filter = self._ensure_shared_filter(
            'source_filter',
            "Type to filter source segments...",
            on_change=self.apply_filters,
            on_return=self.apply_filters,
        )
        self.target_filter = self._ensure_shared_filter(
            'target_filter',
            "Type to filter target segments...",
            on_change=self.apply_filters,
            on_return=self.apply_filters,
        )

    def _build_warning_banner(self, key: str) -> QWidget:
        banner = QWidget()
        banner.setObjectName(f"{key}_warning_banner")
        banner.setStyleSheet(
            "background-color: #dc2626; color: white; padding: 8px; border-radius: 4px;"
        )
        warning_layout = QHBoxLayout(banner)
        warning_layout.setContentsMargins(10, 5, 10, 5)

        warning_icon = QLabel("⚠️")
        warning_icon.setStyleSheet("font-size: 16px; background: transparent;")
        warning_layout.addWidget(warning_icon)

        warning_text = QLabel(
            "<b>WARNING:</b> Replace in Source Text is ENABLED. "
            "This allows modifying your original source segments. Use with extreme caution!"
        )
        warning_text.setStyleSheet("background: transparent; font-weight: bold;")
        warning_text.setWordWrap(True)
        warning_layout.addWidget(warning_text, stretch=1)

        settings_link = QPushButton(self.tr("⚙️ Disable in Options"))
        settings_link.setStyleSheet(
            "background-color: rgba(255, 255, 255, 0.2); color: white; "
            "border: 1px solid white; padding: 4px 12px; border-radius: 3px; font-weight: bold;"
        )
        settings_link.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_link.clicked.connect(self._go_to_settings_tab)
        warning_layout.addWidget(settings_link)

        return banner

    def _get_warning_banner(self, key: str) -> QWidget:
        """Return a per-host warning banner widget, recreating it if needed."""
        banner = self.warning_banners.get(key)
        if not self._widget_is_alive(banner):
            banner = self._build_warning_banner(key)
            self.warning_banners[key] = banner
        elif banner.parent() is not None:
            banner.setParent(None)
        return banner
        
        # Precision scroll buttons removed (user preference)
        # self.add_precision_scroll_buttons()
    
    # ========================================================================
    # PROJECT MANAGEMENT
    # ========================================================================
    
    # ────────────────────────────────────────────────────────────────────
    #  System tray integration (cross-platform via QSystemTrayIcon).
    #
    #  Three preferences live in settings.json under the "ui" section:
    #    close_to_tray             – clicking the window's X hides instead of quits
    #    start_minimized_to_tray   – launch with no visible window (tray only)
    #    close_to_tray_hint_shown  – internal: have we shown the first-time hint?
    #
    #  Toggleable from the tray icon's right-click menu, no Settings-dialog
    #  trip needed. On platforms with no system tray (e.g. headless Linux)
    #  the tray simply stays None and these features are no-ops.
    # ────────────────────────────────────────────────────────────────────

    def _setup_tray_icon(self):
        """Create the system-tray icon. Called once from main() after __init__."""
        from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
        from PyQt6.QtGui import QAction, QIcon

        self._tray_icon = None
        self._really_quit = False  # Set by tray "Quit" so closeEvent doesn't hide

        # v1.10.238: route an explicit quit (macOS "Quit Supervertaler" / ⌘Q,
        # or system shutdown) through _really_quit so it terminates instead of
        # backgrounding to the tray. The window X / red close button does NOT
        # send QEvent.Quit, so it still backgrounds as designed.
        if not hasattr(self, "_quit_event_filter"):
            self._quit_event_filter = _QuitEventFilter(self)
            QApplication.instance().installEventFilter(self._quit_event_filter)

        if not QSystemTrayIcon.isSystemTrayAvailable():
            print("[Tray] System tray unavailable on this platform – skipping")
            return

        # Prefer the high-DPI PNG family from assets/ – those scale much
        # better in the Windows tray than icon.ico (which collapses to a
        # blurry 16-px frame). Fall back to icon.ico, then the window
        # icon, so older installs without the PNGs still get *something*.
        icon = None
        for candidate in ("icon_24.png", "icon_16x16.png", "icon_128.png", "icon.ico"):
            icon_path = get_resource_path(f"assets/{candidate}")
            if icon_path.exists():
                icon = QIcon(str(icon_path))
                break
        if icon is None or icon.isNull():
            icon = self.windowIcon()

        self._tray_icon = QSystemTrayIcon(icon, self)
        self._tray_icon.setToolTip(self.tr("Supervertaler Workbench"))

        prefs = self._load_settings_section("ui")
        menu = QMenu()

        show_action = QAction("Show Workbench", self)
        show_action.triggered.connect(self._tray_show_window)
        menu.addAction(show_action)

        # Quick tab-jump entries (v1.10.2 of issue #199). Mirror the
        # global hotkeys: Ctrl+Alt+L = SuperLookup, etc. Each entry shows Workbench (if hidden) and
        # switches main_tabs to the target tab. Indices are looked up
        # lazily via getattr so the menu still builds even if a tab
        # hasn't been added (e.g. an older build with fewer top tabs).
        def _make_jumper(attr_name):
            def _jump():
                idx = getattr(self, attr_name, None)
                if idx is None:
                    return
                self._tray_show_window()
                if hasattr(self, 'main_tabs'):
                    try:
                        self.main_tabs.setCurrentIndex(idx)
                    except Exception:
                        pass
            return _jump

        jump_lookup = QAction("Open SuperLookup", self)
        jump_lookup.triggered.connect(_make_jumper('superlookup_tab_index'))
        menu.addAction(jump_lookup)

        jump_voice = QAction("Open Voice", self)
        jump_voice.triggered.connect(_make_jumper('voice_tab_index'))
        menu.addAction(jump_voice)

        jump_settings = QAction("Open Settings", self)
        jump_settings.setMenuRole(QAction.MenuRole.NoRole)  # v1.10.233: keep ⌘, off Preferences (Mac)
        jump_settings.triggered.connect(_make_jumper('settings_tab_index'))
        menu.addAction(jump_settings)

        menu.addSeparator()

        self._tray_close_to_tray_action = QAction("Close to tray (instead of quit)", self)
        self._tray_close_to_tray_action.setCheckable(True)
        self._tray_close_to_tray_action.setChecked(bool(prefs.get("close_to_tray", False)))
        self._tray_close_to_tray_action.toggled.connect(self._on_toggle_close_to_tray)
        menu.addAction(self._tray_close_to_tray_action)

        self._tray_start_minimized_action = QAction("Start minimized to tray", self)
        self._tray_start_minimized_action.setCheckable(True)
        self._tray_start_minimized_action.setChecked(bool(prefs.get("start_minimized_to_tray", False)))
        self._tray_start_minimized_action.toggled.connect(self._on_toggle_start_minimized)
        menu.addAction(self._tray_start_minimized_action)

        # "Start with computer" – registers an HKCU Run entry on Windows,
        # ~/Library/LaunchAgents plist on macOS, ~/.config/autostart .desktop
        # on Linux. No elevation needed; the auto-started instance uses the
        # GUI Python variant so no terminal window appears at boot.
        try:
            from modules import autostart as _autostart_mod
            self._autostart_mod = _autostart_mod
            if _autostart_mod.is_supported():
                self._tray_autostart_action = QAction("Start with computer", self)
                self._tray_autostart_action.setCheckable(True)
                self._tray_autostart_action.setChecked(_autostart_mod.is_enabled())
                self._tray_autostart_action.toggled.connect(self._on_toggle_autostart)
                menu.addAction(self._tray_autostart_action)
        except Exception as e:
            print(f"[Tray] Autostart menu item not available: {e}")
            self._autostart_mod = None

        menu.addSeparator()

        quit_action = QAction("Quit Supervertaler", self)
        quit_action.triggered.connect(self._tray_quit)
        menu.addAction(quit_action)

        self._tray_icon.setContextMenu(menu)
        self._tray_icon.activated.connect(self._on_tray_activated)
        self._tray_icon.show()

        # Tell Qt not to quit when all windows are closed – we manage lifecycle
        # via the tray "Quit" item. Otherwise hiding the only window via close
        # would terminate the app on some platforms.
        QApplication.instance().setQuitOnLastWindowClosed(False)

    def _on_tray_activated(self, reason):
        """Left-click the tray icon → toggle window visibility (Windows/Linux convention)."""
        from PyQt6.QtWidgets import QSystemTrayIcon
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self._tray_show_window()

    def _tray_show_window(self):
        """Restore, raise, and focus the main window from the tray."""
        self.show()
        # Clear the minimized state if Qt set it during hide.
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def _on_esc_quick_lookup_dismiss(self):
        """Dismiss Workbench when Esc is pressed on a "quick lookup"
        top tab.

        Per-tab dismissal action:

        - **SuperLookup / Voice**: hide to the system tray (requires a
          tray icon – without one, hiding would strand the user in an
          invisible window, so those tabs fail closed).

        Gate that always applies: the current tab is one of
        SuperLookup / Voice. On Editor / TMs / Termbases /
        AI / Settings, Esc keeps its natural editor / dialog /
        combo-box semantics (cancel-edit, close-popup, etc.).

        Per-tab text-input gate (v1.10.19 refinement):

        - **SuperLookup**: always hide regardless of focus. The user
          observed (correctly) that SuperLookup is a single-purpose
          query surface – there's no state to preserve, the search
          field re-seeds on the next summon, and the only thing Esc
          could reasonably mean here is "get me out of here". So we
          skip the focus check and dismiss unconditionally.
        - **Voice**: skip the dismiss if a text-input
          widget has focus (QLineEdit / QTextEdit / QPlainTextEdit /
          QAbstractSpinBox / editable QComboBox), to preserve
          standard Qt-app conventions (Esc clears the field, closes
          a popup, etc.) when the user might plausibly be typing
          something they don't want to lose. Once focus is on the
          list / tree / button / empty area, Esc dismisses to tray.

        Wired in v1.10.17, made focus-aware in v1.10.18, made
        always-hide-on-SuperLookup in v1.10.19.
        """
        if not hasattr(self, 'main_tabs'):
            return
        has_tray = getattr(self, '_tray_icon', None) is not None

        superlookup_idx = getattr(self, 'superlookup_tab_index', None)
        voice_idx = getattr(self, 'voice_tab_index', None)
        current = self.main_tabs.currentIndex()

        # SuperLookup: unconditional hide (tray required).
        if superlookup_idx is not None and current == superlookup_idx:
            if has_tray:
                self.hide()
            return

        # Voice: focus-aware hide.
        if voice_idx is None or current != voice_idx:
            # Some other tab – not a quick-lookup surface, do nothing.
            return

        try:
            from PyQt6.QtWidgets import (
                QLineEdit, QTextEdit, QPlainTextEdit, QComboBox,
                QAbstractSpinBox,
            )
            focused = QApplication.focusWidget()
            if focused is not None:
                text_input_types = (
                    QLineEdit, QTextEdit, QPlainTextEdit,
                    QAbstractSpinBox,
                )
                if isinstance(focused, text_input_types):
                    self.log(f"[Esc] ignored – text input focused "
                             f"({type(focused).__name__})")
                    return
                if isinstance(focused, QComboBox) and focused.isEditable():
                    self.log("[Esc] ignored – editable combo focused")
                    return
                # Walk up: a QLineEdit nested inside a custom
                # composite widget should also count as a text input.
                w = focused.parent()
                hops = 0
                while w is not None and hops < 5:
                    if isinstance(w, text_input_types):
                        return
                    w = w.parent()
                    hops += 1
        except Exception:
            # If the introspection fails for any reason, fall
            # through to dismiss – safer than silently doing nothing.
            pass

        # Voice: hide to tray, as before.
        if has_tray:
            self.hide()

    def keyPressEvent(self, event):
        """Esc fallback for the quick-lookup dismiss (v1.10.345).

        The primary binding is the window-level Escape QShortcut. A
        plain-Esc KeyPress only ever bubbles up to the main window's
        keyPressEvent when the shortcut system did NOT consume it –
        i.e. exactly the failure mode reported as "Esc does nothing".
        Catching it here makes the dismiss immune to whatever ate the
        shortcut activation, and the console line records that the
        fallback (not the shortcut) fired, so the two paths can be
        told apart in the field.
        """
        try:
            if (event.key() == Qt.Key.Key_Escape
                    and not event.modifiers()
                    and hasattr(self, 'main_tabs')):
                current = self.main_tabs.currentIndex()
                quick = (getattr(self, 'superlookup_tab_index', None),
                         getattr(self, 'voice_tab_index', None))
                if current in quick:
                    try:
                        self.log("[Esc] shortcut did not fire – "
                                 "dismissing via keyPressEvent fallback")
                    except Exception:
                        pass
                    self._on_esc_quick_lookup_dismiss()
                    event.accept()
                    return
        except Exception as e:
            print(f"[Workbench] Esc keyPressEvent fallback failed: {e}")
        super().keyPressEvent(event)

    def _on_toggle_close_to_tray(self, checked: bool):
        prefs = self._load_settings_section("ui")
        prefs["close_to_tray"] = bool(checked)
        self._save_settings_section("ui", prefs)

    def _on_toggle_start_minimized(self, checked: bool):
        prefs = self._load_settings_section("ui")
        prefs["start_minimized_to_tray"] = bool(checked)
        self._save_settings_section("ui", prefs)

    def _on_toggle_autostart(self, checked: bool):
        """Register or unregister Supervertaler with the OS autostart facility."""
        mod = getattr(self, "_autostart_mod", None)
        if mod is None:
            return
        ok = mod.enable() if checked else mod.disable()
        # Re-sync the checkbox with reality – if the registry / plist write
        # failed silently, don't leave the menu showing a stale state.
        actual = mod.is_enabled()
        if actual != checked:
            self._tray_autostart_action.blockSignals(True)
            self._tray_autostart_action.setChecked(actual)
            self._tray_autostart_action.blockSignals(False)
        if ok and getattr(self, "_tray_icon", None) is not None and checked:
            try:
                from PyQt6.QtWidgets import QSystemTrayIcon
                self._tray_icon.showMessage(
                    "Supervertaler",
                    "Will start automatically with your computer. "
                    "Tip: enable 'Start minimized to tray' to skip the window at boot.",
                    QSystemTrayIcon.MessageIcon.Information,
                    4000,
                )
            except Exception:
                pass

    def _tray_quit(self):
        """Real quit from tray menu – bypasses the close-to-tray hide."""
        self._really_quit = True
        # closeEvent does the cleanup and quits. close() returns False when
        # the user cancels (unsaved changes → Cancel); quitting anyway would
        # throw their work away.
        if self.close():
            QApplication.instance().quit()

    def closeEvent(self, event):
        """Close the main window: hide to the tray if the user chose that,
        otherwise confirm, clean up and quit the whole application.

        Issue #255: this class used to define closeEvent twice. Python keeps
        only the last definition, so the tray handling, hotkey cleanup and
        explicit quit() here never ran – and because _setup_tray_icon turns
        off quit-on-last-window-closed, the window's X hid the window but
        left the process running. Keep this the only closeEvent in the
        class (tests/test_main_window_close.py checks).
        """
        # Close-to-tray: hide instead of quit, unless the tray menu explicitly
        # asked for a real quit. Skipped if the tray icon failed to initialise.
        if (not getattr(self, "_really_quit", False)
                and getattr(self, "_tray_icon", None) is not None):
            prefs = self._load_settings_section("ui")
            if prefs.get("close_to_tray", False):
                event.ignore()
                self.hide()
                if not prefs.get("close_to_tray_hint_shown", False):
                    try:
                        from PyQt6.QtWidgets import QSystemTrayIcon
                        self._tray_icon.showMessage(
                            "Supervertaler is still running",
                            "Click the tray icon to bring it back, or right-click "
                            "for Quit.",
                            QSystemTrayIcon.MessageIcon.Information,
                            5000,
                        )
                    except Exception:
                        pass
                    prefs["close_to_tray_hint_shown"] = True
                    self._save_settings_section("ui", prefs)
                return

        # Check for unsaved project changes
        if getattr(self, 'project_modified', False):
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "You have unsaved changes. Do you want to save before closing?",
                QMessageBox.StandardButton.Save |
                QMessageBox.StandardButton.Discard |
                QMessageBox.StandardButton.Cancel
            )

            if reply == QMessageBox.StandardButton.Save:
                self.save_project()
            # A cancelled Save As (or a failed save) leaves project_modified
            # set: stay open rather than lose the work.
            if reply == QMessageBox.StandardButton.Cancel or (
                    reply == QMessageBox.StandardButton.Save and self.project_modified):
                event.ignore()
                self._really_quit = False
                return

        try:
            if hasattr(self, 'lookup_tab'):
                # Clean up pynput hotkey manager (cross-platform)
                if hasattr(self.lookup_tab, '_hotkey_manager') and self.lookup_tab._hotkey_manager:
                    try:
                        self.lookup_tab._hotkey_manager.stop()
                        print("[Superlookup] pynput hotkey manager stopped")
                    except Exception as e:
                        print(f"[Superlookup] Error stopping pynput: {e}")

                # Clean up ahk library if used (Windows fallback)
                if hasattr(self.lookup_tab, '_using_ahk_library') and self.lookup_tab._using_ahk_library:
                    if hasattr(self.lookup_tab, '_ahk') and self.lookup_tab._ahk:
                        try:
                            self.lookup_tab._ahk.stop_hotkeys()
                            print("[Superlookup] ahk library hotkeys stopped")
                        except Exception as e:
                            print(f"[Superlookup] Error stopping ahk library: {e}")

                # Terminate external AutoHotkey process if running (Windows fallback)
                if hasattr(self.lookup_tab, 'ahk_process') and self.lookup_tab.ahk_process:
                    try:
                        self.lookup_tab.ahk_process.terminate()
                        self.lookup_tab.ahk_process.wait(timeout=2)
                        print("[Superlookup] AHK process terminated")
                    except Exception:
                        try:
                            self.lookup_tab.ahk_process.kill()
                        except Exception:
                            pass
        except Exception as e:
            print(f"[Superlookup] Error during hotkey cleanup: {e}")

        # Each step guards itself, so one failing can't skip the quit below.
        self._stop_okapi_sidecar()
        self._forget_round_trip_state()  # removes an open Trados package's temporary folder
        self._cleanup_web_views()
        self._close_detached_log_windows()

        # Accept the close event.
        event.accept()
        # Because _setup_tray_icon flipped setQuitOnLastWindowClosed to False
        # (so dialogs / hidden windows wouldn't terminate the app), we need
        # to ask Qt to quit explicitly – otherwise closing the main window
        # would leave a headless process behind. Unconditional: closing the
        # main window means quit, even if some other top-level window is open.
        QApplication.instance().quit()

    # The round-trip state of an open project: the file it was imported from
    # and the handler that writes the translation back into it, per format.
    _ROUND_TRIP_STATE = ('trados_source_file', 'trados_handler', 'memoq_source_file',
                         'memoq_rtf_source_file', 'memoq_rtf_handler', 'mqxliff_source_file',
                         'mqxliff_handler', 'mxliff_source_file', 'cafetran_source_file', 'cafetran_handler',
                         'phrase_source_file', 'phrase_handler', 'po_source_file', 'po_handler',
                         'dejavu_source_file', 'dejavu_handler', 'sdlppx_source_file',
                         'sdlppx_handler', 'sdlxliff_source_files', 'sdlxliff_handler')

    def _load_synonyms_bulk(self):
        """Load every synonym from ``termbase_synonyms`` in one query,
        return as ``{term_id: {'source': [text, …], 'target': [text, …]}}``.

        v1.10.73 (Tier 2). Called by ``_build_termbase_index`` once per
        rebuild so per-term synonym lookups during search are pure
        dict access — no N+1 query storm. Synonyms are filtered to
        non-forbidden entries (Trados does the same in
        ``BulkLoadTargetSynonyms``) and ordered by ``display_order``
        when present so the "preferred synonym first" convention
        users set in the Edit Termbase Entry dialog is preserved
        through to the TermLens display.

        Defensive about schema variation: the ``forbidden`` and
        ``display_order`` columns were added in later schema
        migrations, so we check for their presence via
        ``PRAGMA table_info`` (mirrors the pattern in
        ``modules/termbase_entry_editor.load_synonyms``). Older
        databases — or in-memory test fixtures that haven't run
        every migration — still work, just without the
        forbidden-filter or the display-order sort.

        Returns ``{}`` on any error so the caller can carry on with
        an empty synonym map instead of failing the whole index
        build over a synonym query hiccup. The TermLens display just
        won't show synonyms in that case — same as today.
        """
        result = {}
        try:
            if not hasattr(self, 'db_manager') or self.db_manager is None:
                return result
            cur = self.db_manager.cursor
            cur.execute("PRAGMA table_info(termbase_synonyms)")
            cols = [r[1] for r in cur.fetchall()]
            has_forbidden = 'forbidden' in cols
            has_display_order = 'display_order' in cols

            where_clause = "WHERE COALESCE(forbidden, 0) = 0" if has_forbidden else ""
            order_clause = ("ORDER BY term_id, COALESCE(display_order, 0), id"
                            if has_display_order
                            else "ORDER BY term_id, id")
            cur.execute(
                f"SELECT term_id, synonym_text, language "
                f"FROM termbase_synonyms {where_clause} {order_clause}"
            )
            for term_id, syn_text, language in cur.fetchall():
                if not term_id or not syn_text:
                    continue
                bucket = result.setdefault(term_id, {'source': [], 'target': []})
                if language == 'source':
                    bucket['source'].append(syn_text)
                elif language == 'target':
                    bucket['target'].append(syn_text)
                # Anything else (legacy 'both', NULL, etc.) is silently
                # ignored — schema only documents 'source' and 'target'.
        except Exception as e:
            try:
                self.log(f"⚠️ Bulk synonym load failed: {e}")
            except Exception:
                pass
        return result
    
    def _trigger_idle_prefetch(self, current_row: int):
        """
        Trigger prefetch for the next few segments while user is idle.
        Called after user stops typing (debounced) - uses their thinking time productively.
        
        This makes Ctrl+Enter feel INSTANT because matches are already cached.
        Also triggers PROACTIVE HIGHLIGHTING for upcoming segments with glossary matches.
        """
        import json

        if not self.current_project or current_row < 0:
            return
        
        try:
            # Prefetch next 5 segments (enough for fast workflow, not too many to waste resources)
            next_segment_ids = []
            already_cached_ids = []
            start_idx = current_row + 1
            end_idx = min(start_idx + 5, len(self.current_project.segments))

            for seg in self.current_project.segments[start_idx:end_idx]:
                # Check if already cached
                with self.translation_matches_cache_lock:
                    if seg.id not in self.translation_matches_cache:
                        next_segment_ids.append(seg.id)
                    else:
                        already_cached_ids.append(seg.id)

            # For already-cached segments, trigger proactive highlighting immediately
            # This handles the case where segments were cached earlier but not highlighted
            for seg_id in already_cached_ids:
                try:
                    with self.termbase_cache_lock:
                        termbase_raw = self.termbase_cache.get(seg_id, {})
                    if termbase_raw:
                        termbase_json = json.dumps(termbase_raw)
                        # Apply highlighting on main thread (we're already on main thread here)
                        self._apply_proactive_highlighting(seg_id, termbase_json)
                except Exception:
                    pass  # Silent failure for proactive highlighting
            
            if next_segment_ids:
                # Start prefetch in background (silent, no logging)
                self._start_prefetch_worker(next_segment_ids)
        except Exception:
            pass  # Silent failure - prefetch is optimization, not critical
    
    def _start_prefetch_worker(self, segment_ids):
        """
        Start background thread to prefetch TM/MT/LLM matches for given segments.
        This enables instant switching between segments without waiting for match lookups.
        """
        if not segment_ids:
            return
        
        # 🧪 EXPERIMENTAL: Skip prefetch if cache kill switch is enabled
        if getattr(self, 'disable_all_caches', False):
            return
        
        # Stop any existing worker thread
        self.prefetch_stop_event.set()
        if self.prefetch_worker_thread and self.prefetch_worker_thread.is_alive():
            self.prefetch_worker_thread.join(timeout=2)
        
        # Reset stop event for new worker
        self.prefetch_stop_event.clear()
        
        # Start new background worker thread (silent - no logging)
        self.prefetch_worker_thread = threading.Thread(
            target=self._prefetch_worker_run,
            args=(segment_ids,),
            daemon=True  # Daemon thread - won't prevent program exit
        )
        self.prefetch_worker_thread.start()
    
    def _prefetch_worker_run(self, segment_ids):
        """
        Background worker: prefetch TM/MT/LLM matches for given segments.
        Runs in separate thread to avoid blocking UI.
        
        Creates its own database connection for thread-safe termbase lookups.
        Also emits signal to apply proactive highlighting on the main thread.
        """
        import sqlite3
        import json
        
        # Create thread-local database connection for termbase searches
        thread_db_cursor = None
        thread_db_connection = None
        try:
            if hasattr(self, 'db_manager') and self.db_manager and hasattr(self.db_manager, 'db_path'):
                thread_db_connection = sqlite3.connect(self.db_manager.db_path, timeout=15)
                thread_db_connection.row_factory = sqlite3.Row
                thread_db_connection.execute("PRAGMA journal_mode=WAL")
                thread_db_cursor = thread_db_connection.cursor()
        except Exception:
            pass  # Continue without thread-local connection - will use cache only

        # Active TM ids for this project (computed once, thread-safely, on our own
        # cursor). Lets the prefetch cache carry exact (100%) TM matches so the
        # match panel shows them instantly instead of "(No TM match)". Mirrors
        # TMMetadataManager.get_active_tm_ids() but runs on the worker connection.
        active_tm_ids = None
        try:
            pid = getattr(self.current_project, 'id', None) if self.current_project else None
            if pid and thread_db_cursor is not None:
                thread_db_cursor.execute(
                    "SELECT DISTINCT tm.tm_id FROM translation_memories tm "
                    "INNER JOIN tm_activation ta ON tm.id = ta.tm_db_id "
                    "WHERE ta.project_id = ? AND ta.is_active = 1",
                    (pid,),
                )
                active_tm_ids = [r[0] for r in thread_db_cursor.fetchall()]
        except Exception:
            active_tm_ids = None
        
        try:
            for idx, segment_id in enumerate(segment_ids):
                # Check stop signal
                if self.prefetch_stop_event.is_set():
                    return
                
                # Check if already cached
                with self.translation_matches_cache_lock:
                    if segment_id in self.translation_matches_cache:
                        continue  # Already cached, skip
                
                # Find segment
                segment = None
                if self.current_project:
                    for seg in self.current_project.segments:
                        if seg.id == segment_id:
                            segment = seg
                            break
                
                if not segment:
                    continue
                
                # Fetch TM/termbase matches (pass cursor for thread-safe termbase lookups)
                matches = self._fetch_all_matches_for_segment(segment, thread_db_cursor, tm_ids=active_tm_ids)

                # Count matches for logging and proactive highlighting
                tm_count = len(matches.get("TM", []))
                tb_count = len(matches.get("Termbases", []))
                mt_count = len(matches.get("MT", []))
                llm_count = len(matches.get("LLM", []))
                total_matches = tm_count + tb_count + mt_count + llm_count
                
                # Only cache results if we found something
                # Don't cache empty results - let main thread do fresh lookup
                if total_matches > 0:
                    with self.translation_matches_cache_lock:
                        self.translation_matches_cache[segment_id] = matches

                # PROACTIVE HIGHLIGHTING: Emit signal to apply highlighting on main thread
                # This makes upcoming segments show their glossary matches immediately
                if tb_count > 0:
                    try:
                        # Extract raw termbase matches from cache for highlighting
                        with self.termbase_cache_lock:
                            termbase_raw = self.termbase_cache.get(segment_id, {})

                        if termbase_raw:
                            # Convert to JSON for thread-safe signal transfer
                            termbase_json = json.dumps(termbase_raw)
                            # Emit signal - will be handled on main thread
                            self._proactive_highlight_signal.emit(segment_id, termbase_json)
                    except Exception:
                        pass  # Silent fail for proactive highlighting
            
        except Exception as e:
            self.log(f"Error in prefetch worker: {e}")
        
        finally:
            # Close thread-local database connection
            if thread_db_cursor:
                try:
                    thread_db_cursor.close()
                except:
                    pass
            if thread_db_connection:
                try:
                    thread_db_connection.close()
                except:
                    pass
    
    def _fetch_all_matches_for_segment(self, segment, thread_db_cursor=None, tm_ids=None):
        """
        Fetch TM, MT, and LLM matches for a single segment.
        Used by prefetch worker. Returns matches_dict with all match types.

        Args:
            segment: The segment to fetch matches for
            thread_db_cursor: Optional thread-local database cursor for termbase searches.
                              If provided and segment not in termbase_cache, will do direct lookup.
            tm_ids: Active TM ids for the project. When provided (with a cursor), the
                    exact (100%) TM match is looked up here so it is cached and shown
                    instantly; fuzzy TM still streams in on-demand via TMSearchWorker.
        """
        from modules.translation_results_panel import TranslationMatch
        
        matches_dict = {
            "LLM": [],
            "NT": [],
            "MT": [],
            "TM": [],
            "Termbases": [],
            "NonTrans": []  # Non-translatables
        }
        
        # Get project languages
        source_lang = getattr(self.current_project, 'source_lang', None) if self.current_project else None
        target_lang = getattr(self.current_project, 'target_lang', None) if self.current_project else None
        
        if not source_lang or not target_lang:
            return matches_dict
        
        # Convert language names to codes
        source_lang_code = self._convert_language_to_code(source_lang)
        target_lang_code = self._convert_language_to_code(target_lang)
        
        # 1. TM matches — cheap exact (100%) lookup on the worker's own cursor.
        # Previously skipped entirely ("not thread-safe"), which meant the prefetch
        # cached every segment with TM=[]. Since patent segments almost always have
        # termbase hits, those TM-less entries were cached as "complete" and the
        # match panel then showed "(No TM match)" even for segments that DO have a
        # 100% match. Looking the exact match up here (read-only, touch=False, uses
        # the indexed source_hash) makes the cache correct. Fuzzy TM still streams
        # in on-demand via TMSearchWorker on navigation.
        if self.enable_tm_matching and thread_db_cursor is not None and tm_ids:
            try:
                from modules.database_manager import DatabaseManager
                _dbm = DatabaseManager.__new__(DatabaseManager)
                _dbm.connection = thread_db_cursor.connection
                _dbm.cursor = thread_db_cursor
                _dbm.log = lambda _m: None
                exact = _dbm.get_exact_match(
                    source=segment.source,
                    tm_ids=tm_ids,
                    source_lang=source_lang_code,
                    target_lang=target_lang_code,
                    touch=False,
                )
                if exact:
                    tm_meta = getattr(self.tm_database, 'tm_metadata', {}) or {}
                    _tmid = exact.get('tm_id', '')
                    tm_name = tm_meta.get(_tmid, {}).get('name', _tmid) if isinstance(tm_meta, dict) else _tmid
                    matches_dict["TM"].append(TranslationMatch(
                        source=exact.get('source_text', ''),
                        target=exact.get('target_text', ''),
                        relevance=100,
                        metadata={
                            'tm_name': tm_name,
                            'tm_id': _tmid,
                            'reverse_match': bool(exact.get('reverse_match', False)),
                        },
                        match_type='TM',
                        compare_source=exact.get('source_text', ''),
                        provider_code='TM',
                    ))
            except Exception:
                pass  # On-demand worker will still fetch TM on navigation

        # 2. MT matches (if enabled)
        if self.enable_mt_matching:
            # Use the same MT fetching logic as the main search
            # For now, we'll skip MT in prefetch to avoid rate limiting
            # MT will still be fetched on-demand when user clicks
            pass
        
        # 3. LLM matches (if enabled)
        if self.enable_llm_matching:
            # Skip LLM in prefetch - too expensive and slow
            # LLM will still be fetched on-demand when user clicks
            pass
        
        # 4. Termbase matches - try cache first, then direct lookup if cursor provided
        termbase_matches_raw = None
        
        with self.termbase_cache_lock:
            if segment.id in self.termbase_cache:
                termbase_matches_raw = self.termbase_cache[segment.id]
        
        # If not in cache, match against the in-memory termbase index.
        #
        # v1.10.232: switched from the legacy per-word _search_termbases_thread_safe
        # (LIKE %word% + LIMIT 30, no ORDER BY) to the same _search_termbase_in_memory
        # that the interactive path and TermPicker use. The old path silently dropped
        # matches when a common word (e.g. "fractie"/"fraction" in a patent termbase)
        # appeared in >30 term entries — SQLite truncated the per-word result set
        # arbitrarily, so a longer phrase like "non-ferrometalen fractie" could be
        # cut while its shorter sibling "ferrometalen fractie" survived. TermLens
        # (fed from this prefetch cache) then showed the wrong/shorter term while
        # TermPicker (which falls back to the in-memory matcher) showed the correct
        # one. The in-memory index scans every term with no cap, so all surfaces now
        # agree. It reads self.termbase_index under termbase_index_lock, so it's
        # thread-safe to call from this worker; no DB cursor needed.
        if termbase_matches_raw is None:
            try:
                termbase_matches_raw = self._deduplicate_termbase_matches(
                    self._search_termbase_in_memory(segment.source)
                )
                # Also populate the termbase cache for future use
                if termbase_matches_raw:
                    with self.termbase_cache_lock:
                        self.termbase_cache[segment.id] = termbase_matches_raw
            except Exception:
                pass  # Silently continue
        
        # Convert raw termbase matches to TranslationMatch objects
        if termbase_matches_raw:
            for term_id, match_info in termbase_matches_raw.items():
                # Extract source term, translation, ranking, and other metadata from match_info
                if isinstance(match_info, dict):
                    source_term = match_info.get('source', '')
                    target_term = match_info.get('translation', '')
                    ranking = match_info.get('ranking', None)
                    forbidden = match_info.get('forbidden', False)
                    is_project_termbase = match_info.get('is_project_termbase', False)
                    termbase_name = match_info.get('termbase_name', 'Default')
                else:
                    # Backward compatibility: if just string (shouldn't happen with new code)
                    source_term = str(term_id)
                    target_term = match_info
                    ranking = None
                    forbidden = False
                    is_project_termbase = False
                    termbase_name = 'Default'

                match_obj = TranslationMatch(
                    source=source_term,
                    target=target_term,
                    relevance=95,
                    metadata={
                        'termbase_name': termbase_name,
                        'ranking': ranking,
                        'forbidden': forbidden,
                        'is_project_termbase': is_project_termbase,
                        'term_id': match_info.get('term_id') if isinstance(match_info, dict) else None,
                        'termbase_id': match_info.get('termbase_id') if isinstance(match_info, dict) else None,
                        'domain': match_info.get('domain', '') if isinstance(match_info, dict) else '',
                        'notes': match_info.get('notes', '') if isinstance(match_info, dict) else '',
                        'project': match_info.get('project', '') if isinstance(match_info, dict) else '',
                        'client': match_info.get('client', '') if isinstance(match_info, dict) else '',
                        'target_synonyms': match_info.get('target_synonyms', []) if isinstance(match_info, dict) else []
                    },
                    match_type='Termbase',
                    compare_source=source_term,
                    provider_code='TB'
                )
                matches_dict["Termbases"].append(match_obj)
        
        return matches_dict
    
    def stop_prefetch_worker(self):
        """Stop the background prefetch worker gracefully"""
        if self.prefetch_worker_thread and self.prefetch_worker_thread.is_alive():
            self.log("⏹️  Stopping prefetch worker...")
            self.prefetch_stop_event.set()
            self.prefetch_worker_thread.join(timeout=2)
            self.log("✓ Prefetch worker stopped")
    
    def invalidate_translation_cache(self, smart_invalidation=True):
        """
        Invalidate prefetch cache when TM/Termbase is updated.
        
        Args:
            smart_invalidation: If True, only clear future segments (keeps already-seen segments cached).
                               If False, clear entire cache.
        
        Smart mode: When translator saves segment N, only clears cache for segments > N.
        This way, going backwards still uses cache, but going forward gets fresh TM matches.
        """
        with self.translation_matches_cache_lock:
            if not smart_invalidation:
                # Full invalidation (e.g., termbase import, bulk TM import)
                cache_size = len(self.translation_matches_cache)
                if cache_size > 0:
                    self.translation_matches_cache.clear()
                    self.log(f"🔄 Full cache invalidated ({cache_size} segments cleared)")
            else:
                # Smart invalidation: Only clear segments ahead of current position
                if hasattr(self, 'table') and self.table:
                    current_row = self.table.currentRow()
                    if current_row >= 0 and self.current_project:
                        # Clear cache for all segments AFTER current position
                        segments_to_clear = []
                        for seg in self.current_project.segments[current_row + 1:]:
                            if seg.id in self.translation_matches_cache:
                                segments_to_clear.append(seg.id)
                        
                        for seg_id in segments_to_clear:
                            del self.translation_matches_cache[seg_id]
                        
                        if segments_to_clear:
                            self.log(f"🔄 Smart cache: cleared {len(segments_to_clear)} future segments (keeping past segments cached)")
                else:
                    # Fallback: full clear if we can't determine position
                    cache_size = len(self.translation_matches_cache)
                    if cache_size > 0:
                        self.translation_matches_cache.clear()
                        self.log(f"🔄 Cache invalidated ({cache_size} segments)")
    
    # ========================================================================
    # MEMOQ BILINGUAL DOCX IMPORT/EXPORT
    # ========================================================================
    
    # Formats other than DOCX that we route through Okapi extract → merge.
    # DOCX has its own dedicated menu entry; TMX is for TM import (separate
    # path); everything else the sidecar reports as supported lands here.
    OKAPI_OTHER_FORMATS = [
        ("Adobe InDesign Markup", "*.idml"),
        ("HTML", "*.html *.htm"),
        ("XLIFF 1.2", "*.xliff *.xlf"),
        ("PO (gettext)", "*.po"),
        ("Microsoft Excel", "*.xlsx"),
        ("Microsoft PowerPoint", "*.pptx"),
    ]

    # All formats import_document accepts. DOCX is listed first because it's
    # the most common case; the rest come from OKAPI_OTHER_FORMATS so this
    # list and the file filter stay in lockstep when new formats are added.
    @property
    def IMPORT_DOCUMENT_FORMATS(self):
        return [("Word Document", "*.docx")] + self.OKAPI_OTHER_FORMATS
    
    # ========================================================================
    # SIMPLE TEXT FILE IMPORT/EXPORT
    # ========================================================================
    
    # ========================================================================
    # MULTI-FILE FOLDER IMPORT
    # ========================================================================
    
    # ========================================================================
    # MEMOQ XLIFF IMPORT/EXPORT
    # ========================================================================
    
    # ========================================================================
    # GNU GETTEXT (.po / .pot) IMPORT/EXPORT
    # ========================================================================

    # ========================================================================
    # CAFETRAN BILINGUAL DOCX IMPORT/EXPORT
    # ========================================================================

    def get_translator_name(self) -> str:
        """Return the user-configured translator name, falling back to system username."""
        settings = self.load_general_settings()
        name = settings.get('translator_name', '').strip()
        if name:
            return name
        return os.environ.get('USERNAME', os.environ.get('USER', 'user'))

    # ─── Standalone SDLXLIFF import/export ────────────────────────────────────

    # ========================================================================
    # PHRASE (MEMSOURCE) MXLIFF IMPORT/EXPORT
    # ========================================================================

    # ────────────────────────────────────────────────────────────────────
    # Bilingual Re-importable Text (AI-friendly, Bracketed) — export + re-import
    # v1.10.231. Ports the Trados plugin's "[SEGMENT NNNN]" round-trip: a plain
    # text file (editable target lines) + a .svexport.json sidecar that maps the
    # edited file back to THIS open project. Called "Text" (not Markdown)
    # because the segment blocks rely on preserved line breaks. The method names
    # keep the historical "markdown" wording as an internal detail. See
    # modules/bilingual_markdown_handler.py for the format + diff engine.
    # ────────────────────────────────────────────────────────────────────
    def export_bilingual_markdown(self):
        """Export segments as AI-friendly Bracketed Text + .svexport.json sidecar.

        Round-trips back into the SAME open project via import_bilingual_markdown().
        """
        try:
            if not self.current_project or not self.current_project.segments:
                QMessageBox.warning(self, "No Project",
                                    "Please open a project with segments first.")
                return

            from modules.bilingual_markdown_handler import (
                MdExportSegment, build_markdown, build_sidecar, write_export,
            )
            from modules.statuses import STATUSES, get_status
            from datetime import datetime, timezone

            segments = self._segments_in_document_order()
            # Flush any pending grid edits into the segment objects first, so the
            # export matches exactly what's on screen.
            try:
                self._sync_grid_targets_to_segments(segments)
            except Exception:
                pass

            # ── Options dialog ──────────────────────────────────────────────
            dialog = QDialog(self)
            dialog.setWindowTitle(self.tr("Export Bilingual Text (AI-friendly)"))
            dialog.setMinimumWidth(480)
            d_layout = QVBoxLayout(dialog)

            info = QLabel(
                "Export a bilingual text file in the AI-friendly "
                "<b>[SEGMENT NNNN]</b> format, with a <code>.svexport.json</code> "
                "sidecar written alongside it.<br><br>"
                "Edit the target lines in any text editor or with an LLM, then "
                "re-import via <b>Project → Import → 🔁 Supervertaler Re-importable → "
                "Bilingual Text (AI-friendly) - Update Project…</b> to update this project."
            )
            info.setTextFormat(Qt.TextFormat.RichText)
            info.setWordWrap(True)
            d_layout.addWidget(info)
            d_layout.addSpacing(8)

            include_locked_cb = CheckmarkCheckBox(self.tr("Include locked segments"))
            include_locked_cb.setChecked(True)
            d_layout.addWidget(include_locked_cb)

            # Status filter — only the statuses actually present in the project.
            present_keys = []
            seen = set()
            for s in segments:
                k = get_status(s.status).key
                if k not in seen:
                    seen.add(k)
                    present_keys.append(k)
            status_group = QGroupBox(self.tr("Include statuses"))
            sg_layout = QVBoxLayout(status_group)
            status_cbs = {}
            for k in present_keys:
                lbl = STATUSES[k].label if k in STATUSES else k
                cb = CheckmarkCheckBox(lbl)
                cb.setChecked(True)
                sg_layout.addWidget(cb)
                status_cbs[k] = cb
            d_layout.addWidget(status_group)

            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self.tr("Save to File..."))
            # Issue #248: skip the file entirely – copy the text for an AI chat
            # and bring the reply back with Update from Pasted Text.
            copy_mode = {"on": False}
            copy_btn = buttons.addButton(self.tr("📋 Copy to Clipboard"),
                                         QDialogButtonBox.ButtonRole.AcceptRole)
            copy_btn.setToolTip(self.tr(
                "Copy the text instead of saving a file. Paste it into an AI chat, "
                "then bring the edited reply back with Update from Pasted Text."))
            copy_btn.clicked.connect(lambda: copy_mode.update(on=True))
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            d_layout.addWidget(buttons)

            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            to_clipboard = copy_mode["on"]

            include_locked = include_locked_cb.isChecked()
            selected_keys = {k for k, cb in status_cbs.items() if cb.isChecked()}
            status_filter = None if selected_keys == set(present_keys) else selected_keys

            # ── Build the export list ──────────────────────────────────────
            export_segs = []
            number = 0
            for seg in segments:
                key = get_status(seg.status).key
                if not include_locked and getattr(seg, 'locked', False):
                    continue
                if status_filter is not None and key not in status_filter:
                    continue
                number += 1
                label = STATUSES[key].label if key in STATUSES else key
                export_segs.append(MdExportSegment(
                    number=number,
                    segment_id=seg.id,
                    source=seg.source or "",
                    target=seg.target or "",
                    status_key=key,
                    status_label=label,
                    locked=bool(getattr(seg, 'locked', False)),
                    file_name=getattr(seg, 'file_name', '') or "",
                    comment=getattr(seg, 'notes', '') or "",
                ))

            if not export_segs:
                QMessageBox.warning(self, "No Segments",
                                    "No segments match the selected options.")
                return

            # ── Save path ──────────────────────────────────────────────────
            file_path = ""
            if not to_clipboard:
                default_name = (self.current_project.name or "project").replace(" ", "_") + "_bilingual.txt"
                file_path, _ = QFileDialog.getSaveFileName(
                    self, "Export Bilingual Text", default_name,
                    "Text Files (*.txt);;All Files (*.*)")
                if not file_path:
                    return
                if not file_path.lower().endswith('.txt'):
                    file_path += '.txt'

            src_disp = self.current_project.source_lang or "Source"
            tgt_disp = self.current_project.target_lang or "Target"

            # A representative source file name for the header/sidecar.
            source_file_name = ""
            for s in export_segs:
                if s.file_name:
                    source_file_name = s.file_name
                    break
            if not source_file_name:
                source_file_name = self.current_project.name or ""

            multi_file = len({s.file_name for s in export_segs if s.file_name}) > 1

            # The workflow statuses a proofreader/LLM may set (the auto-assigned
            # TM/MT match statuses aren't meant to be set by hand).
            _status_choice_keys = ["not_started", "draft", "confirmed",
                                   "proofread", "approved", "rejected"]
            status_choices = [STATUSES[k].label for k in _status_choice_keys
                              if k in STATUSES]

            md_text = build_markdown(
                export_segs,
                project_name=self.current_project.name or "Untitled",
                source_file_name=source_file_name,
                source_lang_display=src_disp,
                target_lang_display=tgt_disp,
                tool_version=__version__,
                multi_file=multi_file,
                status_choices=status_choices,
            )
            sidecar = build_sidecar(
                export_segs,
                project_name=self.current_project.name or "Untitled",
                source_file_name=source_file_name,
                source_language=src_disp,
                target_language=tgt_disp,
                tool_version=__version__,
                export_file_path=file_path,
                timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            # Remembered for this session so Update from Pasted Text can map the
            # AI's reply back exactly, even where a source occurs more than once.
            self._bilingual_text_session_sidecar = (id(self.current_project), sidecar)

            if to_clipboard:
                QApplication.clipboard().setText(md_text)
                paste_key = format_shortcut_for_display(
                    self.shortcut_manager.get_shortcut("file_update_from_pasted_text") or "Ctrl+Shift+V")
                self.log(f"✓ Copied {len(export_segs)} segments to the clipboard as bilingual text")
                QMessageBox.information(
                    self, "Copied to Clipboard",
                    f"Copied {len(export_segs)} segment(s) to the clipboard.\n\n"
                    "Paste them into your AI chat. When you have the edited reply, "
                    "copy it and press " + paste_key + " (Project → Import → "
                    "🔁 Supervertaler Re-importable → Bilingual Text (AI-friendly) - "
                    "Update from Pasted Text…) to update this project.")
                return

            side_path = write_export(file_path, md_text, sidecar)

            self.log(f"✓ Exported {len(export_segs)} segments to bilingual text: "
                     f"{os.path.basename(file_path)} (+ sidecar)")
            QMessageBox.information(
                self, "Export Complete",
                f"Exported {len(export_segs)} segment(s) to:\n{os.path.basename(file_path)}\n\n"
                f"Sidecar: {os.path.basename(side_path)}\n\n"
                "Edit the target lines, then re-import via Project → Import → "
                "🔁 Supervertaler Re-importable → Bilingual Text (AI-friendly) - "
                "Update Project… to update this project.")

        except Exception as e:
            QMessageBox.critical(self, "Export Error",
                                 f"Failed to export bilingual text:\n\n{str(e)}")
            self.log(f"✗ Bilingual text export failed: {str(e)}")
            import traceback
            traceback.print_exc()
    
    # ========================================================================
    # GRID MANAGEMENT
    # ========================================================================
    
    # ---------------------------------------------------------------------
    # Page-aware grid loading (v1.10.147 — based on Hans Lenting's
    # "Simpelvertaler" fork). Instead of constructing ~1200 full QTextEdit
    # widgets up-front on project open, only build them for rows on the
    # current page; off-page rows get a cheap QTableWidgetItem placeholder
    # and the heavy widgets are installed lazily by the pagination handler
    # when they become visible. Major initial-load speedup on large
    # SDLXLIFF projects; scrolling/typing behaviour is unchanged.
    # ---------------------------------------------------------------------

    # =========================================================================
    # COMPARE PANEL TAB
    # =========================================================================

    # =========================================================================
    # DOCUMENT PREVIEW TAB
    # =========================================================================

    # ── Preview text size (issue #70) ────────────────────────────────
    PREVIEW_ZOOM_STEPS = (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.25, 1.4, 1.6, 1.8, 2.0, 2.5)
        
        # Re-render preview to update highlighting
        # (In a full implementation, we'd just update the highlight without full re-render)

    # Comment-type colours shared by the Status-column indicator, the comment
    # panels and their legends (issue #234). SC = amber, PC = purple.
    COMMENT_SC_BG = "rgba(255, 152, 0, 0.35)"   # amber  – Segment Comments
    COMMENT_PC_BG = "rgba(156, 39, 176, 0.32)"  # purple – Proofreading Comments

    def eventFilter(self, obj, event):
        """Handle tooltip events for status labels using custom tooltip widget."""
        from PyQt6.QtCore import QEvent

        tooltip_text = obj.property("status_tooltip") if hasattr(obj, 'property') else None

        if tooltip_text:
            if event.type() == QEvent.Type.ToolTip:
                # Append the segment's comment summary (stored on the parent
                # status widget) so hovering the Status column reveals comments,
                # including segment-level ones that have no highlighted range.
                display_text = tooltip_text
                try:
                    parent = obj.parentWidget()
                    ctip = parent.property("comment_tooltip") if parent is not None else None
                    if ctip:
                        display_text = f"{tooltip_text}\n{ctip}"
                except Exception:
                    pass
                # Show our custom tooltip instead of Qt's
                tooltip = self._get_custom_tooltip()
                tooltip.setText(display_text)
                tooltip.adjustSize()
                # Position near the cursor
                pos = event.globalPos()
                tooltip.move(pos.x() + 10, pos.y() + 10)
                tooltip.show()
                return True  # Consume the event to prevent Qt's tooltip
            elif event.type() == QEvent.Type.Leave:
                # Hide custom tooltip when mouse leaves
                if hasattr(self, '_custom_tooltip') and self._custom_tooltip:
                    self._custom_tooltip.hide()

        return super().eventFilter(obj, event)

    def _sync_bottom_dock_menu(self):
        """Keep the 'Under-grid panel' radio items (Hide / Above / Below) in
        sync with the dock's current visibility and position. The TermLens vs
        QuickTrans choice is made via the dock's tab bar, not the menu."""
        if not hasattr(self, 'bottom_tabs') or not hasattr(self, 'underdock_hide_action'):
            return
        try:
            visible = self.bottom_tabs.isVisible()
        except Exception:
            return
        above = visible and getattr(self, 'tabs_above_grid', False)
        below = visible and not getattr(self, 'tabs_above_grid', False)
        # setChecked does not emit triggered(), so this won't recurse.
        self.underdock_above_action.setChecked(above)
        self.underdock_below_action.setChecked(below)
        self.underdock_hide_action.setChecked(not visible)

    def _on_bottom_tab_changed(self, _idx):
        """Tab switched in the under-grid dock: remember which tab the user
        prefers (unless we're restoring at startup)."""
        if not getattr(self, '_restoring_dock', False):
            self._save_bottom_dock_active_tab()

    def _save_bottom_dock_active_tab(self):
        if not hasattr(self, 'bottom_tabs'):
            return
        try:
            cur = self.bottom_tabs.currentWidget()
            name = 'quicktrans' if cur is getattr(self, 'quicktrans_panel', None) else 'termlens'
            s = self.load_general_settings()
            s['bottom_dock_active_tab'] = name
            self.save_general_settings(s)
        except Exception:
            pass

    def _on_match_top_tab_changed(self, _idx):
        """Match Panel top tab switched (TermLens / QuickTrans): remember the
        choice (unless we're restoring at startup)."""
        if getattr(self, '_restoring_dock', False):
            return
        try:
            cur = self.match_top_tabs.currentWidget()
            name = 'quicktrans' if cur is getattr(self, 'quicktrans_panel_match', None) else 'termlens'
            s = self.load_general_settings()
            s['match_top_active_tab'] = name
            self.save_general_settings(s)
        except Exception:
            pass

    # Selection tint colour for the source/target cell widgets in the current
    # row. Matches the QTableWidget::item:selected QSS rule so the tint is
    # consistent across columns 0/1 (table items) and 2/3 (cell widgets).
    # Pale Trados-style icy blue – clearly visible against alternating row
    # tints without overpowering the cell content (termbase pills, tag
    # markers, search highlights).
    SELECTION_TINT = "#CFE5F5"
    
    # =========================================================================
    # MATCH PANEL ZOOM METHODS
    # =========================================================================
    
    # Class variables for Match Panel font (TM Source/Target boxes)
    match_panel_font_size = 10    # Default font size
    match_panel_font_family = ''  # Empty = system default; set from settings on startup
    match_panel_font_bold = False  # Default not bold

    # ═══════════════════════════════════════════════════════════════════════
    # Unified Settings Infrastructure
    # All settings are stored in settings/settings.json with top-level sections:
    #   "api_keys", "general", "ui", "features"
    # ═══════════════════════════════════════════════════════════════════════

    def _migrate_settings_to_unified(self):
        """One-time migration from old settings files to unified settings/settings.json"""
        settings_dir = self._get_settings_dir()
        unified_file = settings_dir / "settings.json"

        # Skip if already migrated
        if unified_file.exists():
            return

        print("[Settings] Migrating to unified settings/settings.json ...")
        settings_dir.mkdir(parents=True, exist_ok=True)
        unified = {"api_keys": {}, "general": {}, "ui": {}, "features": {}}

        # 1. Migrate general_settings.json
        old_general = self.user_data_path / "general_settings.json"
        if old_general.exists():
            try:
                with open(old_general, 'r', encoding='utf-8') as f:
                    unified["general"] = json.load(f)
                old_general.rename(old_general.with_suffix('.json.migrated'))
            except Exception as e:
                print(f"[Settings] Warning: could not migrate general_settings.json: {e}")

        # 2. Migrate ui_preferences.json
        old_prefs = self.user_data_path / "ui_preferences.json"
        if old_prefs.exists():
            try:
                with open(old_prefs, 'r', encoding='utf-8') as f:
                    prefs = json.load(f)
                # Remove the nested general_settings (it's a stale duplicate of general_settings.json)
                prefs.pop('general_settings', None)
                unified["ui"] = prefs
                old_prefs.rename(old_prefs.with_suffix('.json.migrated'))
            except Exception as e:
                print(f"[Settings] Warning: could not migrate ui_preferences.json: {e}")

        # 3. Migrate feature_settings.json
        old_features = self.user_data_path / "feature_settings.json"
        if old_features.exists():
            try:
                with open(old_features, 'r', encoding='utf-8') as f:
                    unified["features"] = json.load(f)
                old_features.rename(old_features.with_suffix('.json.migrated'))
            except Exception as e:
                print(f"[Settings] Warning: could not migrate feature_settings.json: {e}")

        # 4. Migrate api_keys.txt
        api_keys_file = None
        for candidate in [self.user_data_path / "api_keys.txt",
                          Path(__file__).parent / "user_data_private" / "api_keys.txt"]:
            if candidate.exists():
                api_keys_file = candidate
                break

        if api_keys_file:
            try:
                api_keys = {}
                with open(api_keys_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and '=' in line and not line.startswith('#'):
                            key, value = line.split('=', 1)
                            api_keys[key.strip().lower()] = value.strip()
                # Migrate legacy 'google' key to canonical 'gemini' key
                if api_keys.get('google') and not api_keys.get('gemini'):
                    api_keys['gemini'] = api_keys['google']
                unified["api_keys"] = api_keys
                api_keys_file.rename(api_keys_file.with_suffix('.txt.migrated'))
            except Exception as e:
                print(f"[Settings] Warning: could not migrate api_keys.txt: {e}")

        # 5. Relocate satellite JSON files to settings/ subfolder
        import shutil
        for filename in ['find_replace_history.json', 'superlookup_history.json',
                         'recent_projects.json',
                         'themes.json', 'shortcuts.json', 'voice_commands.json']:
            old_path = self.user_data_path / filename
            new_path = settings_dir / filename
            if old_path.exists() and not new_path.exists():
                try:
                    shutil.move(str(old_path), str(new_path))
                except Exception as e:
                    print(f"[Settings] Warning: could not move {filename}: {e}")

        # 6. Write unified settings
        try:
            with open(unified_file, 'w', encoding='utf-8') as f:
                json.dump(unified, f, indent=2, ensure_ascii=False)
            print(f"[Settings] Migration complete: {unified_file}")
        except Exception as e:
            print(f"[Settings] Error writing unified settings: {e}")
    
    def _init_usage_statistics(self):
        """Show opt-in dialog (once) and send anonymous usage ping if opted in."""
        try:
            from modules.usage_statistics import (
                has_been_asked, show_opt_in_dialog, set_opted_in, send_ping
            )
            settings_path = self._get_unified_settings_path()

            # Show opt-in dialog once (first launch or after update that adds this)
            if not has_been_asked(settings_path):
                opted_in = show_opt_in_dialog(self)
                set_opted_in(settings_path, opted_in)
                if opted_in:
                    self.log("📊 Usage statistics enabled – thank you!")
                else:
                    self.log("📊 Usage statistics declined – no data will be sent.")

            # Send anonymous ping in background (only if opted in)
            send_ping(settings_path, __version__)

        except Exception:
            # Never let stats code block startup
            pass

    # --- Non-translatable (number/code) helpers ---
    # Compiled once at class definition time. Matches text that contains ONLY
    # digits, whitespace, and punctuation – no Unicode letters of any script.
    # The character class covers common numeric/code content:
    #   digits, whitespace, hyphen/dash, period, slash, comma, semicolons,
    #   colon, parentheses, plus, equals, asterisk, hash, percent, degree,
    #   currency symbols (€$£¥), ampersand, at-sign, exclamation, question,
    #   square brackets, curly braces, pipe, tilde, caret, underscore, backslash.
    # Future locale-aware conversion belongs in _transform_non_translatable only.
    _NON_TRANSLATABLE_RE = re.compile(
        r'^[\d\s\-\.\/,;:()+=*#%°€$£¥&@!\?\[\]\{\}|~^_\\\'\"]+$'
    )

    @staticmethod
    def _is_non_translatable(source_text: str) -> bool:
        """Return True if source contains only numbers, codes, and punctuation (no letters)."""
        if not source_text or not source_text.strip():
            return False
        return bool(SupervertalerQt._NON_TRANSLATABLE_RE.match(source_text.strip()))

    @staticmethod
    def _transform_non_translatable(source_text: str, source_lang: str = None, target_lang: str = None) -> str:
        """Return the target text for a non-translatable segment.

        Currently returns source verbatim. This is the designed extension point
        for future locale-aware number/date conversion (e.g. decimal comma ↔ period).
        When that feature is added, only this method changes; callers and UI stay the same.
        """
        return source_text
    
    # ========================================================================
    # PROOFREADING SYSTEM
    # ========================================================================
    
    def _restore_active_style_guide(self, guide_name: str, language: str):
        """Restore an active style guide by name and language
        
        NOTE: Temporarily disabled for unified prompt library migration.
        TODO: Implement session restoration for unified library (save/restore active prompts)
        """
        if not hasattr(self, 'prompt_manager_qt') or not self.prompt_manager_qt:
            return
        
        # TODO: Adapt this for unified prompt library
        # The new system uses multi-attach, so we'd need to:
        # 1. Find the style guide in library.prompts
        # 2. Attach it using library.attach_prompt(path)
        # For now, skip restoration - user can re-select prompts
        self.log(f"⚠ Style guide restoration not yet implemented in unified library: '{guide_name}' ({language})")

    # ========================================================================
    # TRANSLATION MEMORY
    # ========================================================================
    
    def _initialize_spellcheck_for_target_language(self, target_lang: str):
        """Initialize spellcheck for the given target language after import/load"""
        if not target_lang:
            return
        
        try:
            if hasattr(self, 'spellcheck_manager') and self.spellcheck_manager:
                if self.spellcheck_manager.set_language(target_lang):
                    self.log(f"✓ Spellcheck initialized for target language: {target_lang}")
                else:
                    self.log(f"⚠ Spellcheck dictionary not available for {target_lang}")
        except Exception as e:
            self.log(f"⚠ Could not initialize spellcheck: {e}")
    
    def create_diff_html(self, current_text: str, tm_text: str) -> str:
        """Create HTML with visual diff highlighting between current and TM source
        
        Shows changes inline like memoQ:
        - Red strikethrough for deleted text (in TM but not in current)
        - Green underline + bold for added text (in current but not in TM)
        - Sequential display: deletions then additions in order
        """
        from difflib import SequenceMatcher
        
        s = SequenceMatcher(None, tm_text, current_text)
        html_parts = []
        
        for tag, i1, i2, j1, j2 in s.get_opcodes():
            tm_chunk = tm_text[i1:i2]
            current_chunk = current_text[j1:j2]
            
            if tag == 'equal':
                # Matching text - show normally
                html_parts.append(tm_chunk)
            elif tag == 'delete':
                # Text removed from TM - show with red strikethrough (like memoQ deletion)
                html_parts.append(f'<span style="color: #dc2626; text-decoration: line-through;">{tm_chunk}</span>')
            elif tag == 'insert':
                # Text added in current - show with green underline + bold (like memoQ insertion)
                html_parts.append(f'<span style="color: #22c55e; text-decoration: underline; font-weight: bold;">{current_chunk}</span>')
            elif tag == 'replace':
                # Text changed - show deletion (strikethrough) followed by insertion (underline)
                # This matches memoQ's behavior: old text strikethrough, new text underlined
                html_parts.append(f'<span style="color: #dc2626; text-decoration: line-through;">{tm_chunk}</span>')
                html_parts.append(' ')  # Add space between deletion and insertion
                html_parts.append(f'<span style="color: #22c55e; text-decoration: underline; font-weight: bold;">{current_chunk}</span>')
        
        return ''.join(html_parts)
    
    # ========================================================================
    # TERMBASE HIGHLIGHTING & MATCHING
    # ========================================================================
    
    def _convert_language_to_code(self, language: str) -> str:
        """Convert full language name to language code for termbase search"""
        language_map = {
            'Dutch': 'nl',
            'English': 'en', 
            'French': 'fr',
            'German': 'de',
            'Spanish': 'es',
            'Italian': 'it',
            'Portuguese': 'pt',
            'Russian': 'ru',
            'Chinese': 'zh',
            'Japanese': 'ja',
            'Korean': 'ko',
            'Arabic': 'ar'
        }
        return language_map.get(language, language.lower() if language else None)
    
    # ========================================================================
    # NAVIGATION & SEARCH
    # ========================================================================

    def show_qa_checks_dialog(self):
        """QA → Run QA Checks (issue #209). Non-modal, so findings can be fixed
        in the grid while the list stays open."""
        from modules.qa_checks_dialog import QAChecksDialog
        dlg = getattr(self, '_qa_checks_dialog', None)
        if dlg is None:
            def _reload_fr_sets():
                mgr = getattr(self, 'fr_sets_manager', None)
                if mgr is not None:
                    try:
                        mgr.reload_sets()
                    except RuntimeError:
                        pass  # its dialog has been closed and deleted
            dlg = QAChecksDialog(
                self, Path(self.user_data_path) / "find_replace_sets",
                get_segments=lambda: self.current_project.segments if self.current_project else [],
                navigate=self._navigate_to_segment_by_id,
                on_sets_changed=_reload_fr_sets,
                extract_tags=extract_all_tags)
            set_help_topic(dlg, HelpTopics.QA_CHECKS)  # F1 opens its help page
            self._qa_checks_dialog = dlg
        else:
            dlg.refresh_sets()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def show_languagetool_dialog(self):
        """QA → Check with LanguageTool (issue #233). Non-modal."""
        from modules.languagetool_dialog import LanguageToolDialog
        dlg = getattr(self, '_languagetool_dialog', None)
        if dlg is None:
            def _proxies():
                url = self._get_proxy_url() if hasattr(self, '_get_proxy_url') else None
                return {'http': url, 'https': url} if url else None
            dlg = LanguageToolDialog(
                self,
                get_segments=lambda: self.current_project.segments if self.current_project else [],
                get_target_language=lambda: getattr(self.current_project, 'target_lang', '') if self.current_project else '',
                navigate=self._navigate_to_segment_by_id,
                apply_target=self._set_target_from_tool,
                load_settings=self.load_general_settings,
                save_settings=self.save_general_settings,
                get_proxies=_proxies)
            set_help_topic(dlg, HelpTopics.QA_LANGUAGETOOL)  # F1 opens its help page
            self._languagetool_dialog = dlg
        else:
            from modules.languagetool_client import lt_language
            if self.current_project:
                dlg.language_edit.setText(lt_language(getattr(self.current_project, 'target_lang', '')))
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _set_target_from_tool(self, segment_index, new_text):
        """Replace a segment's target on behalf of a checking tool (LanguageTool
        suggestion): undoable with Ctrl+Z, grid cell updated in place."""
        if not self.current_project or not 0 <= segment_index < len(self.current_project.segments):
            return
        segment = self.current_project.segments[segment_index]
        old_target, old_status = segment.target, segment.status
        if new_text == old_target:
            return
        segment.target = new_text
        self.record_undo_state(segment.id, old_target, new_text, old_status, old_status)
        widget = self.table.cellWidget(segment_index, 3) if hasattr(self, 'table') else None
        if widget is not None and hasattr(widget, 'setPlainText'):
            display_text = new_text
            if getattr(self, 'hide_outer_wrapping_tags', False):
                display_text, _ = strip_outer_wrapping_tags(display_text)
            widget.blockSignals(True)
            widget.setPlainText(display_text)
            widget.blockSignals(False)
        self.project_modified = True
        self.update_window_title()
    
    def force_refresh_matches(self):
        """Force refresh all glossary and TM matches for current segment.

        Bound to F5 and to the TermLens 🔄 button (v1.10.72: collapsed
        into a single code path). Does:

        1. **Smart index rebuild** — compares a fresh snapshot of the
           termbase tables against the snapshot captured at the end of
           the last ``_build_termbase_index()`` call (same gating the
           v1.10.69 file watcher uses). If anything changed in
           ``termbases`` or ``termbase_terms`` (count, max id, max
           modified_date), rebuilds the in-memory index from disk so
           cross-process edits (typically from the Supervertaler for
           Trados plugin sharing the same SQLite database) are picked
           up. If nothing changed, skips the rebuild — keeping the
           common-case F5 fast (<100 ms) for "I just want to redraw".

        2. **Cache clear** — drops the per-segment termbase match cache
           and the per-segment translation-matches cache for the
           current segment.

        3. **Fresh re-search** — termbases (via the in-memory index),
           TMs (via SQL), NT lists.

        4. **Full redraw** — TermLens widgets (both instances) and the
           Translation Results panel.

        Before v1.10.72 this was the "F5-only" path and a separate
        ``_post_termbase_delete_refresh`` did step 1 unconditionally.
        Having two paths was confusing — users didn't know which to
        press — and the lighter F5 path occasionally left the TermLens
        widget under-refreshed after term edits (the v1.10.71 issue).
        Collapsing into one path with smart gating fixes both: F5 and
        🔄 do the same thing, and the snapshot decides whether the
        rebuild step actually runs.
        """
        if not self.current_project:
            self.statusBar().showMessage("No project open", 3000)
            return

        # v1.10.72: smart index rebuild — only if the termbase tables
        # actually changed since we last rebuilt. The snapshot is
        # updated at the end of every ``_build_termbase_index()``
        # call, so own-writes that ran through a full rebuild don't
        # re-trigger here. Cross-process writes (Trados plugin) and
        # own-writes via the optimisation path that bypassed the
        # rebuild will both have shifted the snapshot, and the
        # rebuild fires.
        try:
            fresh_snap = self._snapshot_termbase_db_state()
            prev_snap = getattr(self, '_termbase_db_last_snapshot', None)
            if fresh_snap is None or prev_snap is None or fresh_snap != prev_snap:
                if prev_snap is not None and fresh_snap is not None:
                    self.log(
                        f"🔄 Termbase DB changed since last rebuild — "
                        f"rebuilding index "
                        f"(termbases:{prev_snap[0]}→{fresh_snap[0]}, "
                        f"terms:{prev_snap[2]}→{fresh_snap[2]})"
                    )
                else:
                    self.log("🔄 Termbase index snapshot missing — rebuilding")
                self._build_termbase_index()
                # _build_termbase_index updates _termbase_db_last_snapshot
                # so subsequent F5 / 🔄 presses skip the rebuild step.
        except Exception as e:
            self.log(f"⚠️ Smart index-rebuild check failed: {e}")
        
        # Get current segment
        current_row = self.table.currentRow()
        if current_row < 0:
            self.statusBar().showMessage("No segment selected", 3000)
            return
        
        # Get segment ID from the grid cell
        id_item = self.table.item(current_row, 0)
        if not id_item:
            self.statusBar().showMessage("Could not find segment", 3000)
            return
        
        try:
            segment_id = int(id_item.text())
        except (ValueError, AttributeError):
            self.statusBar().showMessage("Invalid segment ID", 3000)
            return
        
        # Find the segment
        segment = next((seg for seg in self.current_project.segments if seg.id == segment_id), None)
        if not segment:
            self.statusBar().showMessage(f"Segment {segment_id} not found", 3000)
            return
        
        self.log(f"🔄 Force refresh: Clearing all caches for segment {segment_id}...")
        
        # 1. Clear termbase cache for this segment (and all segments to be safe)
        with self.termbase_cache_lock:
            self.termbase_cache.clear()
            self.log("   ✓ Termbase cache cleared")
        
        # 2. Clear translation matches cache for this segment
        with self.translation_matches_cache_lock:
            if segment_id in self.translation_matches_cache:
                del self.translation_matches_cache[segment_id]
            self.log("   ✓ Translation matches cache cleared for this segment")
        
        # 3. Force fresh termbase search
        self.log("   🔍 Searching termbases...")
        termbase_matches = self.find_termbase_matches_in_source(segment.source)
        tb_count = len(termbase_matches) if termbase_matches else 0
        self.log(f"   ✓ Found {tb_count} termbase matches")
        
        # 4. Force fresh TM search. The previous block searched via self.tm_manager
        #    and a tm_settings['tms']/read shape that DON'T EXIST in this app, so F5
        #    always found 0 TM matches — and step 7b below then CLEARED the Match
        #    Panel. Do it properly: an exact (100%) lookup now (instant), with
        #    fuzzy/MT/LLM streaming in via the async worker scheduled at the end.
        self.log("   🔍 Searching translation memories...")
        tm_matches = []
        try:
            dbm = getattr(self, 'db_manager', None) or getattr(getattr(self, 'tm_database', None), 'db', None)
            tm_ids = None
            if getattr(self.current_project, 'tm_settings', None):
                tm_ids = self.current_project.tm_settings.get('activated_tm_ids')
            if dbm is not None and not (isinstance(tm_ids, list) and len(tm_ids) == 0):
                search_source = segment.source
                if self.hide_outer_wrapping_tags:
                    search_source, _ = strip_outer_wrapping_tags(search_source)
                exact = dbm.get_exact_match(
                    source=search_source, tm_ids=tm_ids,
                    source_lang=getattr(getattr(self, 'tm_database', None), 'source_lang', None),
                    target_lang=getattr(getattr(self, 'tm_database', None), 'target_lang', None),
                    touch=False)
                if exact:
                    _meta = getattr(self.tm_database, 'tm_metadata', {}) or {}
                    _tid = exact.get('tm_id', '')
                    tm_matches = [{
                        'source': exact.get('source_text', ''),
                        'target': exact.get('target_text', ''),
                        'similarity': 1.0,
                        'tm_name': _meta.get(_tid, {}).get('name', _tid) if isinstance(_meta, dict) else _tid,
                        'tm_id': _tid,
                        'reverse_match': bool(exact.get('reverse_match', False)),
                    }]
        except Exception as e:
            self.log(f"   ⚠️ TM search error: {e}")

        tm_count = len(tm_matches) if tm_matches else 0
        self.log(f"   ✓ Found {tm_count} exact TM match(es); fuzzy/MT/LLM will stream in")
        
        # 5. Get NT matches (fresh)
        nt_matches = self.find_nt_matches_in_source(segment.source)
        nt_count = len(nt_matches) if nt_matches else 0
        
        # 6. Update TermLens with fresh results
        if (hasattr(self, 'termlens_widget') and self.termlens_widget) or (hasattr(self, 'termlens_widget_match') and self.termlens_widget_match):
            try:
                # Convert termbase matches dict to list format for termlens
                # v1.10.73 (Tier 1 + Tier 2): forward all metadata
                # fields and source synonyms in addition to the
                # target_synonyms field that was already here. Brings
                # this builder into line with the two other call sites
                # so the TermBlock tooltip + alternative-translation
                # rendering have the same data regardless of which
                # refresh path got us here.
                tb_list = []
                for term_id, match_info in termbase_matches.items():
                    tb_list.append({
                        'source_term': match_info.get('source', ''),
                        'target_term': match_info.get('translation', ''),
                        'termbase_name': match_info.get('termbase_name', ''),
                        'ranking': match_info.get('ranking', 0),
                        'is_project_termbase': match_info.get('is_project_termbase', False),
                        'source_synonyms': match_info.get('source_synonyms', []),
                        'target_synonyms': match_info.get('target_synonyms', []),
                        'term_id': term_id,
                        'termbase_id': match_info.get('termbase_id'),
                        'notes': match_info.get('notes', ''),
                        'definition': match_info.get('definition', ''),
                        'domain': match_info.get('domain', ''),
                        'url': match_info.get('url', ''),
                        'source_abbreviation': match_info.get('source_abbreviation', ''),
                        'target_abbreviation': match_info.get('target_abbreviation', ''),
                        # v1.10.75 (Tier 3) flags
                        'forbidden': match_info.get('forbidden', False),
                        'is_nontranslatable': match_info.get('is_nontranslatable', False),
                        # v1.10.75 (Tier 3c) abbreviation marker
                        'matched_via_abbreviation': match_info.get('matched_via_abbreviation', False),
                    })
                # Get status hint for termbase activation
                status_hint = self._get_termbase_status_hint()

                # Update both TermLens widgets
                self._update_both_termlens(segment.source, tb_list, nt_matches, status_hint)
                self.log("   ✓ TermLens updated")
            except Exception as e:
                self.log(f"   ⚠️ TermLens update error: {e}")
        
        # 7. Update Translation Results panel with fresh results
        if hasattr(self, 'results_panels'):
            from modules.translation_results_panel import TranslationMatch
            
            # Build matches dict for panel
            matches = {
                "TM": [],
                "Termbases": [],
                "MT": [],
                "LLM": []
            }
            
            # Add TM matches
            for tm_match in tm_matches:
                matches["TM"].append(TranslationMatch(
                    source=tm_match.get('source', ''),
                    target=tm_match.get('target', ''),
                    relevance=tm_match.get('similarity', 0) * 100,
                    match_type="TM",
                    metadata={
                        'tm_name': tm_match.get('tm_name', 'Unknown TM'),
                    }
                ))
            
            # Add termbase matches
            for term_id, match_info in termbase_matches.items():
                matches["Termbases"].append(TranslationMatch(
                    source=match_info.get('source', ''),
                    target=match_info.get('translation', ''),
                    relevance=100,  # Termbase matches are always exact
                    match_type="Termbase",
                    metadata={
                        'termbase_name': match_info.get('termbase_name', ''),
                        'ranking': match_info.get('ranking', 0),
                        'is_project_termbase': match_info.get('is_project_termbase', False),
                        'notes': match_info.get('notes', ''),
                        'term_id': term_id,
                        'termbase_id': match_info.get('termbase_id')
                    }
                ))
            
            for panel in self.results_panels:
                try:
                    panel.clear()
                    panel.set_matches(matches)
                except Exception as e:
                    self.log(f"   ⚠️ Panel update error: {e}")
            self.log("   ✓ Translation Results panel updated")
        
        # 7b. Update Match Panel with TM matches (must use TranslationMatch objects for correct display)
        from modules.translation_results_panel import TranslationMatch
        tm_matches_for_panel = []
        for tm_match in tm_matches:
            tm_matches_for_panel.append(TranslationMatch(
                source=tm_match.get('source', ''),
                target=tm_match.get('target', ''),
                relevance=int(tm_match.get('similarity', 0) * 100),
                metadata={
                    'tm_name': tm_match.get('tm_name', 'TM'),
                    'tm_id': tm_match.get('tm_id', ''),
                },
                match_type='TM',
                compare_source=tm_match.get('source', ''),
                provider_code='TM'
            ))
        self.set_compare_panel_matches(segment_id, segment.source, tm_matches_for_panel, [])
        self.log("   ✓ Match Panel updated")
        
        # 8. Re-apply termbase highlighting in the grid source cell
        try:
            source_widget = self.table.cellWidget(current_row, 2)
            if source_widget and hasattr(source_widget, 'highlight_termbase_matches'):
                source_widget.highlight_termbase_matches(termbase_matches)
                self.log("   ✓ Grid highlighting updated")
            else:
                self.log("   ⚠️ Could not find source widget for highlighting")
        except Exception as e:
            self.log(f"   ⚠️ Highlighting error: {e}")
        
        # Fuzzy/MT/LLM stream in asynchronously (the exact match is already shown).
        try:
            self._schedule_mt_and_llm_matches(segment)
        except Exception:
            pass

        # Show status
        total_matches = tb_count + tm_count + nt_count
        self.statusBar().showMessage(f"🔄 Refreshed: {tb_count} termbase, {tm_count} TM, {nt_count} NT matches", 5000)
        self.log(f"🔄 Force refresh complete: {total_matches} total matches for segment {segment_id}")

    # ========================================================================
    # SPELLCHECK METHODS
    # ========================================================================

    def _update_spellcheck_button_style(self):
        """Update spellcheck button style based on enabled state"""
        if hasattr(self, 'spellcheck_btn'):
            if self.spellcheck_enabled:
                self.spellcheck_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 3px 5px;")
            else:
                self.spellcheck_btn.setStyleSheet("background-color: #9E9E9E; color: white; font-weight: bold; padding: 3px 5px;")

    def _toggle_spellcheck(self, checked=None):
        """Toggle spellcheck on/off"""
        self.spellcheck_enabled = not self.spellcheck_enabled
        TagHighlighter.set_spellcheck_enabled(self.spellcheck_enabled)
        
        # Update UI
        self._update_spellcheck_button_style()
        if hasattr(self, 'spellcheck_btn'):
            self.spellcheck_btn.setChecked(self.spellcheck_enabled)
        if hasattr(self, 'spellcheck_toggle_action'):
            self.spellcheck_toggle_action.setChecked(self.spellcheck_enabled)
        
        # Use project's target language if available, otherwise fall back to app preference
        spellcheck_lang = self.current_project.target_lang if self.current_project else self.target_language
        
        # Initialize spellcheck for target language if enabling
        if self.spellcheck_enabled:
            if self.spellcheck_manager.set_language(spellcheck_lang):
                self.log(f"✓ Spellcheck enabled for {spellcheck_lang} (backend: {self.spellcheck_manager.get_backend_info()})")
            else:
                self.log(f"⚠ Spellcheck dictionary not available for {spellcheck_lang}")
                # Disable spellcheck since we couldn't initialize
                self.spellcheck_enabled = False
                TagHighlighter.set_spellcheck_enabled(False)
                self._update_spellcheck_button_style()
                if hasattr(self, 'spellcheck_btn'):
                    self.spellcheck_btn.setChecked(False)
                if hasattr(self, 'spellcheck_toggle_action'):
                    self.spellcheck_toggle_action.setChecked(False)
                # Show warning with diagnostics
                diag = self.spellcheck_manager.get_diagnostics()
                QMessageBox.warning(
                    self, "Spellcheck Unavailable",
                    f"Could not initialize spellcheck for '{spellcheck_lang}'.\n\n"
                    f"Backends:\n"
                    f"• Hunspell: {'Available' if diag['hunspell_available'] else 'Not installed'}\n"
                    f"• pyspellchecker: {'Available' if diag['pyspellchecker_available'] else 'Not installed'}\n\n"
                    f"Try opening Spellcheck Info (right-click menu) for more details."
                )
        else:
            self.log("✗ Spellcheck disabled")
        
        # Save setting
        self._save_spellcheck_settings()
        
        # Refresh grid to show/hide underlines
        self._refresh_all_highlighters()

    def _toggle_spellcheck_from_button(self, checked):
        """Handle button click (separate from menu action to avoid double-toggle)"""
        # The button click already toggles the checked state, so we need to sync
        self.spellcheck_enabled = checked
        TagHighlighter.set_spellcheck_enabled(self.spellcheck_enabled)
        
        # Update menu action
        if hasattr(self, 'spellcheck_toggle_action'):
            self.spellcheck_toggle_action.setChecked(self.spellcheck_enabled)
        
        # Same logic as _toggle_spellcheck
        self._update_spellcheck_button_style()
        
        # Use project's target language if available, otherwise fall back to app preference
        spellcheck_lang = self.current_project.target_lang if self.current_project else self.target_language
        
        if self.spellcheck_enabled:
            if self.spellcheck_manager.set_language(spellcheck_lang):
                self.log(f"✓ Spellcheck enabled for {spellcheck_lang} (backend: {self.spellcheck_manager.get_backend_info()})")
            else:
                self.log(f"⚠ Spellcheck could not be initialized for {spellcheck_lang}")
                # Disable spellcheck since we couldn't initialize
                self.spellcheck_enabled = False
                TagHighlighter.set_spellcheck_enabled(False)
                self._update_spellcheck_button_style()
                if hasattr(self, 'spellcheck_btn'):
                    self.spellcheck_btn.setChecked(False)
                if hasattr(self, 'spellcheck_toggle_action'):
                    self.spellcheck_toggle_action.setChecked(False)
                # Show warning
                diag = self.spellcheck_manager.get_diagnostics()
                QMessageBox.warning(
                    self, "Spellcheck Unavailable",
                    f"Could not initialize spellcheck for '{spellcheck_lang}'.\n\n"
                    f"Backends:\n"
                    f"• Hunspell: {'Available' if diag['hunspell_available'] else 'Not installed'}\n"
                    f"• pyspellchecker: {'Available' if diag['pyspellchecker_available'] else 'Not installed'}\n\n"
                    f"Try opening Spellcheck Info for more details."
                )
        else:
            self.log("✗ Spellcheck disabled")
        
        self._save_spellcheck_settings()
        self._refresh_all_highlighters()

    def _refresh_all_highlighters(self):
        """Refresh syntax highlighters in VISIBLE rows only (performance optimization)
        
        Instead of refreshing all 3000+ rows, we only refresh the rows currently
        visible in the viewport. This makes add-to-dictionary/ignore instant.
        Other rows will get updated when they become visible (on navigation).
        """
        if not hasattr(self, 'table') or not self.table:
            return
        
        # Get visible row range from viewport
        viewport = self.table.viewport()
        if not viewport:
            return
            
        # Find first and last visible rows
        first_visible_row = self.table.rowAt(0)
        last_visible_row = self.table.rowAt(viewport.height())
        
        # Handle edge cases
        if first_visible_row < 0:
            first_visible_row = 0
        if last_visible_row < 0 or last_visible_row >= self.table.rowCount():
            last_visible_row = self.table.rowCount() - 1
            
        # Add small buffer for smoother experience
        first_visible_row = max(0, first_visible_row - 2)
        last_visible_row = min(self.table.rowCount() - 1, last_visible_row + 2)
        
        # Only refresh visible rows
        for row in range(first_visible_row, last_visible_row + 1):
            # Target column (3) has editable text editor with spellcheck
            target_widget = self.table.cellWidget(row, 3)
            if target_widget and hasattr(target_widget, 'highlighter'):
                target_widget.highlighter.rehighlight()

    def _open_custom_dictionary_dialog(self):
        """Manage the spellchecker's custom dictionary (issue #109): edit the
        word list directly, import (plain lists or Hunspell .dic), export,
        and sort / de-duplicate."""
        from modules.spellcheck_manager import parse_word_list

        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Custom Dictionary"))
        dialog.setMinimumSize(460, 540)
        layout = QVBoxLayout(dialog)

        info_label = QLabel(self.tr(
            "Words in the custom dictionary are never marked as spelling errors. "
            "One word per line; case does not matter. Click Save to keep your changes."))
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        tools = QHBoxLayout()
        import_btn = QPushButton(self.tr("📥 Import..."))
        import_btn.setToolTip(self.tr(
            "Add words from a text file (one per line) or a Hunspell .dic file"))
        export_btn = QPushButton(self.tr("📤 Export..."))
        export_btn.setToolTip(self.tr("Save the word list as a text file"))
        tidy_btn = QPushButton(self.tr("🔤 Sort && remove duplicates"))
        folder_btn = QPushButton(self.tr("📂 Open folder"))
        folder_btn.setToolTip(str(self.spellcheck_manager.custom_words_file))
        for b in (import_btn, export_btn, tidy_btn, folder_btn):
            tools.addWidget(b)
        tools.addStretch()
        layout.addLayout(tools)

        editor = QPlainTextEdit()
        editor.setPlaceholderText(self.tr("Enter custom words here, one per line..."))
        editor.setPlainText('\n'.join(self.spellcheck_manager.get_custom_words()))
        layout.addWidget(editor)
        self.custom_dict_editor = editor

        count_label = QLabel()
        layout.addWidget(count_label)

        def update_count():
            words, dupes = parse_word_list(editor.toPlainText())
            text = f"{len(words)} word{'s' if len(words) != 1 else ''} in dictionary"
            if dupes:
                text += f" ({dupes} duplicate{'s' if dupes != 1 else ''} will be merged)"
            count_label.setText(text)

        editor.textChanged.connect(update_count)
        update_count()

        def tidy():
            words, _ = parse_word_list(editor.toPlainText())
            editor.setPlainText('\n'.join(words))

        def import_words():
            path, _ = QFileDialog.getOpenFileName(
                dialog, self.tr("Import Words"), "",
                "Word lists (*.txt *.dic);;All Files (*.*)")
            if not path:
                return
            try:
                with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
                    incoming, _ = parse_word_list(f.read())
            except Exception as e:
                QMessageBox.warning(dialog, self.tr("Import Words"), f"Could not read the file:\n\n{e}")
                return
            current, _ = parse_word_list(editor.toPlainText())
            added = sorted(set(incoming) - set(current))
            editor.setPlainText('\n'.join(sorted(set(current) | set(incoming))))
            QMessageBox.information(
                dialog, self.tr("Import Words"),
                f"{len(added)} new word(s) added from {os.path.basename(path)} "
                f"({len(incoming) - len(added)} already present). Click Save to keep them.")

        def export_words():
            path, _ = QFileDialog.getSaveFileName(
                dialog, self.tr("Export Words"), "custom_words.txt", "Text Files (*.txt)")
            if not path:
                return
            words, _ = parse_word_list(editor.toPlainText())
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(words) + ('\n' if words else ''))
            except Exception as e:
                QMessageBox.warning(dialog, self.tr("Export Words"), f"Could not write the file:\n\n{e}")
                return
            self.log(f"✓ Exported {len(words)} custom dictionary words to {os.path.basename(path)}")

        def open_folder():
            QDesktopServices.openUrl(QUrl.fromLocalFile(
                str(self.spellcheck_manager.dictionaries_path)))

        tidy_btn.clicked.connect(tidy)
        import_btn.clicked.connect(import_words)
        export_btn.clicked.connect(export_words)
        folder_btn.clicked.connect(open_folder)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        save_btn = QPushButton(self.tr("💾 Save"))
        save_btn.clicked.connect(lambda: self._save_custom_dictionary(dialog))
        button_layout.addWidget(save_btn)
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(dialog.reject)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)

        dialog.exec()

    def _save_custom_dictionary(self, dialog):
        """Save custom dictionary from dialog (one file write, however many words)."""
        from modules.spellcheck_manager import parse_word_list
        words, _ = parse_word_list(self.custom_dict_editor.toPlainText())
        count = self.spellcheck_manager.set_custom_words(words)
        self.log(f"✓ Custom dictionary saved with {count} words")
        self._refresh_all_highlighters()
        dialog.accept()

    def _show_spellcheck_info(self):
        """Show information about spellcheck configuration with clickable links"""
        backend = self.spellcheck_manager.get_backend_info()
        language = self.spellcheck_manager.get_current_language() or "Not set"
        available_languages = self.spellcheck_manager.get_available_languages()
        available = ', '.join(available_languages) or "None"
        custom_count = len(self.spellcheck_manager.get_custom_words())
        dict_path = str(self.spellcheck_manager.dictionaries_path)
        diag = self.spellcheck_manager.get_diagnostics()
        is_hunspell = "Hunspell" in backend
        
        # Create dialog with two-column horizontal layout
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Spellcheck Info"))
        dialog.setMinimumWidth(700)
        dialog.setMaximumHeight(520)
        
        main_layout = QVBoxLayout(dialog)
        main_layout.setSpacing(10)
        
        # ═══════════════ Top Row: Status + Language + Backend ═══════════════
        top_row = QHBoxLayout()
        
        status_text = "✓ Enabled" if self.spellcheck_enabled else "✗ Disabled"
        status_color = "green" if self.spellcheck_enabled else "red"
        status_label = QLabel(f"<b style='color: {status_color};'>{status_text}</b>")
        top_row.addWidget(status_label)
        top_row.addSpacing(20)
        
        top_row.addWidget(QLabel(self.tr("Language:")))
        lang_combo = QComboBox()
        lang_combo.setMinimumWidth(100)
        lang_combo.addItems(available_languages)
        if language in available_languages:
            lang_combo.setCurrentText(language)
        top_row.addWidget(lang_combo)
        top_row.addSpacing(20)
        
        backend_icon = "📚" if is_hunspell else "🐍"
        backend_label = QLabel(f"{backend_icon} {backend}")
        top_row.addWidget(backend_label)
        top_row.addStretch()
        main_layout.addLayout(top_row)
        
        def on_language_changed(new_lang):
            if self.spellcheck_manager.set_language(new_lang):
                self.log(f"✓ Spellcheck language changed to: {new_lang}")
                new_backend = self.spellcheck_manager.get_backend_info()
                is_hunspell_new = "Hunspell" in new_backend
                backend_icon_new = "📚" if is_hunspell_new else "🐍"
                backend_label.setText(f"{backend_icon_new} {new_backend}")
                if self.spellcheck_enabled:
                    self._refresh_all_highlighters()
            else:
                self.log(f"⚠ Could not set spellcheck language to: {new_lang}")
        lang_combo.currentTextChanged.connect(on_language_changed)
        
        # ═══════════════ Two-Column Layout ═══════════════
        columns = QHBoxLayout()
        columns.setSpacing(15)
        
        # ─────────── LEFT COLUMN ───────────
        left_col = QVBoxLayout()
        left_col.setSpacing(8)
        
        # How It Works - determine which backend is active
        backend_lower = backend.lower()
        is_spylls = 'spylls' in backend_lower
        is_pyspellchecker = 'pyspellchecker' in backend_lower or 'built-in' in backend_lower
        
        # How It Works
        how_group = QGroupBox(self.tr("How Spellcheck Works"))
        how_layout = QVBoxLayout(how_group)
        how_layout.setSpacing(4)
        
        # Build the explanation with active backend highlighted
        spylls_style = "background-color: #90EE90; padding: 2px;" if is_spylls else ""
        pyspell_style = "background-color: #90EE90; padding: 2px;" if is_pyspellchecker else ""
        hunspell_style = "background-color: #90EE90; padding: 2px;" if is_hunspell and not is_spylls else ""
        
        how_label = QLabel(
            f"<div style='{pyspell_style}'>"
            "🐍 <b>Built-in (pyspellchecker)</b><br>"
            "&nbsp;&nbsp;&nbsp;EN, NL, DE, FR, ES, PT, IT, RU<br>"
            "&nbsp;&nbsp;&nbsp;<i>Works out of the box!</i></div><br>"
            f"<div style='{spylls_style}'>"
            "📗 <b>Spylls (pure Python Hunspell)</b><br>"
            "&nbsp;&nbsp;&nbsp;Bundled: EN, RU, SV<br>"
            "&nbsp;&nbsp;&nbsp;+ Any .dic/.aff files you add</div><br>"
            f"<div style='{hunspell_style}'>"
            "📚 <b>Hunspell (native C library)</b><br>"
            "&nbsp;&nbsp;&nbsp;Any language with .dic/.aff files<br>"
            "&nbsp;&nbsp;&nbsp;<i>Requires cyhunspell (Linux/Mac)</i></div><br>"
            "<b>⚡ Auto-switching:</b> Best available backend<br>"
            "is used automatically based on dictionaries."
        )
        how_label.setWordWrap(True)
        how_layout.addWidget(how_label)
        left_col.addWidget(how_group)
        
        # Available Languages
        lang_group = QGroupBox(self.tr("Available Languages"))
        lang_layout = QVBoxLayout(lang_group)
        lang_list = QLabel(available)
        lang_list.setWordWrap(True)
        lang_layout.addWidget(lang_list)
        left_col.addWidget(lang_group)
        
        # Custom Dictionary
        custom_group = QGroupBox(self.tr("Custom Dictionary"))
        custom_layout = QVBoxLayout(custom_group)
        custom_layout.setSpacing(4)
        custom_layout.addWidget(QLabel(f"<b>{custom_count}</b> words in custom_words.txt"))
        custom_folder = str(self.spellcheck_manager.dictionaries_path)
        custom_btn = QPushButton(self.tr("📁 Open Folder"))
        custom_btn.clicked.connect(lambda: self._open_folder_in_explorer(custom_folder))
        custom_layout.addWidget(custom_btn)
        left_col.addWidget(custom_group)
        
        left_col.addStretch()
        columns.addLayout(left_col)
        
        # ─────────── RIGHT COLUMN ───────────
        right_col = QVBoxLayout()
        right_col.setSpacing(8)
        
        # Add More Dictionaries
        hunspell_group = QGroupBox(self.tr("Add More Dictionaries"))
        hunspell_layout = QVBoxLayout(hunspell_group)
        hunspell_layout.setSpacing(4)
        
        hunspell_layout.addWidget(QLabel(self.tr("<b>To add more languages:</b>")))
        hunspell_layout.addWidget(QLabel(self.tr("1. Download .dic + .aff files for your language")))
        hunspell_layout.addWidget(QLabel(self.tr("2. Place them in the dictionaries folder")))
        hunspell_layout.addWidget(QLabel(self.tr("3. Restart Supervertaler")))
        
        hunspell_layout.addWidget(QLabel(""))
        hunspell_layout.addWidget(QLabel(self.tr("<b>Download sources:</b>")))
        
        memoq_label = QLabel(self.tr('🌐 <a href="https://hunspell.memoq.com/">hunspell.memoq.com</a> – 70+ languages'))
        memoq_label.setOpenExternalLinks(True)
        hunspell_layout.addWidget(memoq_label)
        
        github_label = QLabel(self.tr('🌐 <a href="https://github.com/wooorm/dictionaries/tree/main/dictionaries">GitHub: wooorm/dictionaries</a> – 92+ languages'))
        github_label.setOpenExternalLinks(True)
        hunspell_layout.addWidget(github_label)
        
        libreoffice_label = QLabel(self.tr('🌐 <a href="https://extensions.libreoffice.org/?Tags%5B%5D=50">LibreOffice Extensions</a> – .oxt → .zip'))
        libreoffice_label.setOpenExternalLinks(True)
        hunspell_layout.addWidget(libreoffice_label)
        
        dict_btn = QPushButton(self.tr("📁 Open Dictionaries Folder"))
        dict_btn.clicked.connect(lambda: self._open_folder_in_explorer(dict_path))
        hunspell_layout.addWidget(dict_btn)
        
        # Note about Spylls bundled dictionaries location
        spylls_note = QLabel(
            "<small><i>💡 Spylls bundled dicts are in its pip package.<br>"
            "Add your own .dic/.aff files to the folder above.</i></small>"
        )
        spylls_note.setWordWrap(True)
        hunspell_layout.addWidget(spylls_note)
        
        right_col.addWidget(hunspell_group)
        
        # Diagnostics
        diag_group = QGroupBox(self.tr("🔧 Diagnostics"))
        diag_layout = QVBoxLayout(diag_group)
        diag_layout.setSpacing(2)
        
        diag_text = (
            f"<table cellspacing='2'>"
            f"<tr><td>Hunspell:</td><td>{'✓' if diag.get('hunspell_available') else '✗'} avail, {'✓' if diag.get('hunspell_initialized') else '✗'} init</td></tr>"
            f"<tr><td>Spylls:</td><td>{'✓' if diag.get('spylls_available') else '✗'} avail, {'✓' if diag.get('spylls_initialized') else '✗'} init</td></tr>"
            f"<tr><td>pyspellchecker:</td><td>{'✓' if diag.get('pyspellchecker_available') else '✗'} avail, {'✓' if diag.get('pyspellchecker_initialized') else '✗'} init</td></tr>"
            f"<tr><td>Custom words:</td><td>{diag.get('custom_words_count', 0)}</td></tr>"
            f"<tr><td>Session ignored:</td><td>{diag.get('ignored_words_count', 0)}</td></tr>"
            f"<tr><td>Cache size:</td><td>{diag.get('cache_size', 0)}</td></tr>"
        )
        if diag.get('pyspellchecker_word_count'):
            diag_text += f"<tr><td>Dict words:</td><td>{diag['pyspellchecker_word_count']:,}</td></tr>"
        diag_text += "</table>"
        
        diag_label = QLabel(diag_text)
        diag_label.setTextFormat(Qt.TextFormat.RichText)
        diag_layout.addWidget(diag_label)
        
        # Show warning only if NO backend is initialized
        any_initialized = diag.get('hunspell_initialized') or diag.get('spylls_initialized') or diag.get('pyspellchecker_initialized')
        if not any_initialized:
            warn_label = QLabel(self.tr("<span style='color: orange;'>⚠️ Not initialized - try changing language</span>"))
            diag_layout.addWidget(warn_label)
        
        right_col.addWidget(diag_group)
        
        # Project Links (for technical users)
        links_group = QGroupBox(self.tr("📖 Project Links"))
        links_layout = QVBoxLayout(links_group)
        links_layout.setSpacing(2)
        
        pyspell_link = QLabel(self.tr('<a href="https://github.com/barrust/pyspellchecker">pyspellchecker</a> – Built-in word frequency spellcheck'))
        pyspell_link.setOpenExternalLinks(True)
        links_layout.addWidget(pyspell_link)
        
        spylls_link = QLabel(self.tr('<a href="https://github.com/zverok/spylls">spylls</a> – Pure Python Hunspell implementation'))
        spylls_link.setOpenExternalLinks(True)
        links_layout.addWidget(spylls_link)
        
        hunspell_link = QLabel(self.tr('<a href="http://hunspell.github.io/">Hunspell</a> – Original C/C++ spellcheck library'))
        hunspell_link.setOpenExternalLinks(True)
        links_layout.addWidget(hunspell_link)
        
        right_col.addWidget(links_group)
        
        right_col.addStretch()
        columns.addLayout(right_col)
        
        main_layout.addLayout(columns, stretch=1)
        
        # ═══════════════ OK Button ═══════════════
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        ok_btn = QPushButton(self.tr("OK"))
        ok_btn.clicked.connect(dialog.accept)
        ok_btn.setDefault(True)
        button_layout.addWidget(ok_btn)
        main_layout.addLayout(button_layout)
        
        dialog.exec()

    def _open_folder_in_explorer(self, path: str):
        """Open a folder or file location in the system file explorer"""
        from pathlib import Path

        path_obj = Path(path)

        # If it's a file, open the containing folder
        if path_obj.is_file():
            folder = path_obj.parent
        else:
            folder = path_obj

        # Create folder if it doesn't exist
        folder.mkdir(parents=True, exist_ok=True)

        open_file(str(folder))

    # ========================================================================
    # TABBED SEGMENT EDITOR METHODS (for Grid view)
    # ========================================================================
    
    # ──────────────────────────────────────────────────────────────
    # Comment-anchor visual indicators in editor cells (v1.10.57 Phase C)
    # ──────────────────────────────────────────────────────────────

    # Amber background for ranges anchored by a Comment. Mirrors the
    # default Word-comment highlight colour so the in-app cell matches
    # what users will see in the exported DOCX (Phase E).
    _COMMENT_ANCHOR_BG_QCOLOR = None  # Lazily built — needs Qt module
    
    # ========================================================================
    # GRID-BASED EDITING HANDLERS (for toolbar below grid)
    # ========================================================================
    
    # ========================================================================
    # KEYBOARD NAVIGATION SHORTCUTS
    # ========================================================================

    def _play_sound_effect(self, event: str):
        """Play a minimalist UI sound effect (currently uses OS beep).

        Event is informational for future expansion.
        """
        try:
            # Prefer cached in-memory value if available (avoids repeated file reads)
            enabled = getattr(self, 'enable_sound_effects', None)
            if enabled is None:
                settings = self.load_general_settings() if hasattr(self, 'load_general_settings') else {}
                enabled = settings.get('enable_sound_effects', False)

            if not enabled:
                return

            # Per-event mapping (defaults provided by general settings defaults)
            sound_map = getattr(self, 'sound_effects_map', None)
            if sound_map is None:
                settings = self.load_general_settings() if hasattr(self, 'load_general_settings') else {}
                sound_map = settings.get('sound_effects_map', {}) if isinstance(settings, dict) else {}

            sound_key = None
            if isinstance(sound_map, dict):
                sound_key = sound_map.get(event)

            # Windows: MessageBeep tends to be more audible than QApplication.beep()
            if os.name == 'nt':
                try:
                    import winsound

                    if sound_key == 'none':
                        return

                    wav_key_to_path = {
                        'windows_proximity_wav': r"C:\\Windows\\Media\\Windows Proximity Notification.wav",
                        'windows_restore_wav': r"C:\\Windows\\Media\\Windows Restore.wav",
                        'windows_navigation_start_wav': r"C:\\Windows\\Media\\Windows Navigation Start.wav",
                        'speech_disambiguation_wav': r"C:\\Windows\\Media\\Speech Disambiguation.wav",
                        'windows_startup_wav': r"C:\\Windows\\Media\\Windows Startup.wav",
                    }
                    if sound_key in wav_key_to_path:
                        wav_path = wav_key_to_path.get(sound_key)
                        try:
                            if wav_path and os.path.exists(wav_path):
                                winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                                return
                        except Exception:
                            # Fall back to a beep below
                            pass

                    sound_to_mb = {
                        None: winsound.MB_OK,
                        'ok': winsound.MB_OK,
                        'asterisk': winsound.MB_ICONASTERISK,
                        'exclamation': winsound.MB_ICONEXCLAMATION,
                        'hand': winsound.MB_ICONHAND,
                        'question': winsound.MB_ICONQUESTION,
                    }
                    winsound.MessageBeep(sound_to_mb.get(sound_key, winsound.MB_OK))
                    return
                except Exception:
                    pass

            QApplication.beep()
        except Exception:
            # Never allow sound effects to break normal workflows
            return

    def update_tab_segment_editor(self, segment_id: int, source_text: str, target_text: str,
                                   status: str = "untranslated", notes: str = ""):
        """Update the tab segment editor with current segment data"""
        # CRITICAL FIX: Update segment ID BEFORE setting text to prevent cross-contamination
        # When setPlainText() triggers textChanged, it needs to update the CORRECT segment
        self.tab_current_segment_id = segment_id
        
        # Update ALL tabbed panels (grid view and list view)
        if hasattr(self, 'tabbed_panels'):
            for panel in self.tabbed_panels:
                try:
                    # Update segment editor tab
                    if hasattr(panel, 'editor_widget'):
                        editor = panel.editor_widget
                        editor.seg_info_label.setText(f"Segment {segment_id}")
                        editor.source_editor.setPlainText(source_text)
                        editor.target_editor.setPlainText(target_text)
                        idx = editor.status_combo.findData(status)
                        if idx >= 0:
                            editor.status_combo.blockSignals(True)
                            editor.status_combo.setCurrentIndex(idx)
                            editor.status_combo.blockSignals(False)
                    
                    # Update notes tab
                    if hasattr(panel, 'notes_widget'):
                        panel.notes_widget.notes_editor.setPlainText(notes)
                except Exception as e:
                    self.log(f"Error updating tabbed panel: {e}")
        
        # NOTE: TermLens is updated AFTER termbase search completes in _on_cell_selected_full
        # Do NOT update TermLens here - the cache may not be populated yet
    
    # ========================================================================
    # UTILITY
    # ========================================================================
    
    def update_window_title(self):
        """Update window title with project name and modified state"""
        title = f"Supervertaler Workbench v{__version__}"
        if ENABLE_PRIVATE_FEATURES:
            title += " [🛠️ DEV MODE]"
        if self.current_project:
            title += f" - {self.current_project.name}"
            if self.project_modified:
                title += " *"
        self.setWindowTitle(title)
    
    def log(self, message: str):
        """Log message to status bar and session log (thread-safe)"""
        # Always safe to print to console
        print(f"[LOG] {message}")
        
        # Check if we're in the main thread - Qt widgets can only be modified from main thread
        import threading
        
        is_main_thread = threading.current_thread() == threading.main_thread()
        
        if not is_main_thread:
            # If we're in a background thread, emit signal which will be handled on main thread
            # pyqtSignal with default connection is thread-safe and queues to main event loop
            try:
                self._log_signal.emit(message)
            except Exception:
                pass  # Silently fail if signal emission fails
            return
        
        # Main thread - safe to access widgets directly
        self._log_to_ui(message)
    
    def _log_to_ui(self, message: str):
        """Internal method to log to UI widgets - must be called from main thread only"""
        if hasattr(self, 'status_bar'):
            self.status_bar.showMessage(message)
        
        # Add to debug buffer if debug mode is enabled
        if hasattr(self, 'debug_mode_enabled') and self.debug_mode_enabled:
            if not hasattr(self, 'debug_log_buffer'):
                self.debug_log_buffer = []
            from datetime import datetime
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.debug_log_buffer.append(f"[{timestamp}] {message}")
            # Keep buffer size reasonable (max 10,000 entries)
            if len(self.debug_log_buffer) > 10000:
                self.debug_log_buffer = self.debug_log_buffer[-10000:]

        # Also append to session log tab if it exists
        if hasattr(self, 'session_log_text') and self.session_log_text:
            from datetime import datetime
            from PyQt6.QtGui import QTextCursor
            timestamp = datetime.now().strftime("%H:%M:%S")
            formatted_message = f"[{timestamp}] {message}"
            try:
                # Append without newline (appendPlainText adds it automatically)
                self.session_log_text.appendPlainText(formatted_message)
                # Force scroll to bottom by moving cursor to end
                self.session_log_text.moveCursor(QTextCursor.MoveOperation.End)
                self.session_log_text.ensureCursorVisible()
            except Exception:
                pass  # Silently fail if widget not ready

        # Also append to Settings tab session log if it exists (separate widget)
        if hasattr(self, 'session_log') and self.session_log:
            from datetime import datetime
            from PyQt6.QtGui import QTextCursor
            timestamp = datetime.now().strftime("%H:%M:%S")
            formatted_message = f"[{timestamp}] {message}"
            try:
                self.session_log.appendPlainText(formatted_message)
                self.session_log.moveCursor(QTextCursor.MoveOperation.End)
                self.session_log.ensureCursorVisible()
            except Exception:
                pass  # Silently fail if widget not ready

        # Also send to detached log windows
        if hasattr(self, 'detached_log_windows'):
            from datetime import datetime
            from PyQt6.QtGui import QTextCursor
            timestamp = datetime.now().strftime("%H:%M:%S")
            formatted_message = f"[{timestamp}] {message}"
            for window in self.detached_log_windows[:]:  # Copy list to avoid modification during iteration
                try:
                    if window and not window.isHidden():
                        window.log_display.appendPlainText(formatted_message)
                        # Force scroll to bottom by moving cursor to end
                        window.log_display.moveCursor(QTextCursor.MoveOperation.End)
                        window.log_display.ensureCursorVisible()
                    else:
                        # Remove closed windows
                        self.detached_log_windows.remove(window)
                except:
                    # Remove invalid windows
                    try:
                        self.detached_log_windows.remove(window)
                    except:
                        pass
    
    def _go_to_superlookup(self, query=None, source_lang=None, target_lang=None,
                           switch_to_vertical=True):
        """Open SuperLookup with an optional pre-filled query.

        Pre-v1.10.4 this routed through Sidekick (the floating
        assistant). Sidekick was retired in v1.10.4 and SuperLookup
        now lives as a Workbench top tab; v1.10.10 finished the
        rewire so this method targets the in-Workbench widget
        directly.
        """
        if hasattr(self, 'open_workbench_to_superlookup'):
            # Bring the SuperLookup tab forward without seeding text
            # via the usual hotkey path (which auto-fires the search);
            # we want to seed using search_with_query so we can pass
            # the language pair too.
            self.open_workbench_to_superlookup("")
        else:
            self.log("⚠ SuperLookup tab not available")
            return

        widget = getattr(self, '_superlookup_top_widget', None)
        if widget is not None and query and hasattr(widget, 'search_with_query'):
            widget.search_with_query(
                query,
                switch_to_vertical=switch_to_vertical,
                source_lang=source_lang,
                target_lang=target_lang,
            )

    # -----------------------------------------------------------------------
    # PROXY SETTINGS
    # -----------------------------------------------------------------------

    def _get_proxy_url(self) -> Optional[str]:
        """
        Return a fully-formed proxy URL string for use with requests/httpx, or None
        if the proxy is disabled or not configured.

        Format:  http://[user:pass@]host:port
        """
        try:
            ps = self.load_proxy_settings()
            if not ps.get('enabled'):
                return None
            host = ps.get('host', '').strip()
            port = ps.get('port', 8080)
            if not host:
                return None
            username = ps.get('username', '').strip()
            password = ps.get('password', '').strip()
            if username:
                from urllib.parse import quote
                creds = f"{quote(username, safe='')}:{quote(password, safe='')}@"
            else:
                creds = ''
            return f"http://{creds}{host}:{port}"
        except Exception:
            return None

    def _get_proxy_dict(self) -> Optional[Dict[str, str]]:
        """
        Return a requests-style proxies dict {"http": ..., "https": ...}, or None.
        Used by MT service calls that use the requests library.
        """
        url = self._get_proxy_url()
        if not url:
            return None
        return {"http": url, "https": url}

    def _get_active_custom_profile(self, settings=None):
        """Get the active custom_openai profile dict (endpoint, model, api_key).

        Returns dict with keys: name, endpoint, model, api_key – or None if no profile configured.
        Falls back to legacy single-field config for backward compatibility.
        """
        if settings is None:
            settings = self.load_llm_settings()
        profiles = settings.get('custom_openai_profiles', [])
        active_name = settings.get('custom_openai_active_profile', '')

        # Find active profile by name
        for p in profiles:
            if p.get('name') == active_name:
                return p

        # Fallback to first profile
        if profiles:
            return profiles[0]

        # Legacy fallback: old single-field config (pre-profiles)
        endpoint = settings.get('custom_openai_endpoint', '')
        model = settings.get('custom_openai_model', '')
        if endpoint:
            return {'name': 'Custom Endpoint', 'endpoint': endpoint, 'model': model, 'api_key': ''}

        return None
    
    def update_warning_banner(self):
        """Show/hide warning banner based on allow_replace_in_source setting"""
        if not hasattr(self, 'warning_banners'):
            return

        stale_keys = []
        for key, banner in self.warning_banners.items():
            if not self._widget_is_alive(banner):
                stale_keys.append(key)
                continue
            if self.allow_replace_in_source:
                banner.show()
            else:
                banner.hide()

        for key in stale_keys:
            self.warning_banners.pop(key, None)
        
        # Update the grid delegate to reflect the new setting
        self.update_grid_delegate()
    
    def show_about(self):
        import_layout = QVBoxLayout(import_tab)
        
        import_label = QLabel(self.tr("<h3>Import TMX File</h3>"))
        import_layout.addWidget(import_label)
        
        import_info = QLabel(
            "Import translation memories from TMX files. "
            "Entries will be added to the database for the current language pair."
        )
        import_info.setWordWrap(True)
        import_layout.addWidget(import_info)
        
        # File selection
        file_group = QGroupBox(self.tr("TMX File"))
        file_layout = QHBoxLayout()
        
        file_path_label = QLabel(self.tr("No file selected"))
        file_path_label.setStyleSheet("color: #6b7280;")
        file_layout.addWidget(file_path_label, 1)
        
        selected_file = [None]  # Use list to make it mutable in nested function
        
        def browse_tmx():
            file_path, _ = QFileDialog.getOpenFileName(
                dialog,
                "Select TMX File",
                str(self.user_data_path / "resources"),
                "TMX Files (*.tmx);;All Files (*.*)"
            )
            if file_path:
                selected_file[0] = file_path
                file_path_label.setText(file_path)
                file_path_label.setStyleSheet("color: #22c55e;")
        
        browse_btn = QPushButton(self.tr("📁 Browse..."))
        browse_btn.clicked.connect(browse_tmx)
        file_layout.addWidget(browse_btn)
        
        file_group.setLayout(file_layout)
        import_layout.addWidget(file_group)
        
        # Import button
        def import_tmx():
            if not selected_file[0]:
                QMessageBox.warning(dialog, "No File", "Please select a TMX file first.")
                return
            
            if not self.tm_database:
                QMessageBox.warning(dialog, "No Database", "TM database not initialized.")
                return
            
            try:
                # Import TMX file
                tm_id, count = self.tm_database.load_tmx_file(
                    selected_file[0],
                    self.current_project.source_lang,
                    self.current_project.target_lang
                )
                
                QMessageBox.information(
                    dialog,
                    "Import Successful",
                    f"Imported {count} translation entries from TMX file!"
                )
                self.log(f"Imported {count} entries from {selected_file[0]}")
                
                # Clear selection
                selected_file[0] = None
                file_path_label.setText(self.tr("No file selected"))
                file_path_label.setStyleSheet("color: #6b7280;")
                
            except Exception as e:
                QMessageBox.critical(dialog, "Import Error", f"Failed to import TMX file:\n{e}")
        
        import_btn = QPushButton(self.tr("📥 Import TMX File"))
        import_btn.clicked.connect(import_tmx)
        import_btn.setStyleSheet("background-color: #3b82f6; color: white; padding: 10px; font-weight: bold; border: none; outline: none;")
        import_layout.addWidget(import_btn)
        
        import_layout.addStretch()
        tabs.addTab(import_tab, "📥 Import TMX")
        
        # ===== Tab 4: Browse Entries =====
        browse_tab = QWidget()
        browse_layout = QVBoxLayout(browse_tab)
        
        browse_label = QLabel(self.tr("<h3>Browse TM Entries</h3>"))
        browse_layout.addWidget(browse_label)
        
        # Search box
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel(self.tr("🔍 Search:")))
        search_input = QLineEdit()
        search_input.setPlaceholderText(self.tr("Enter search term..."))
        search_layout.addWidget(search_input, 1)
        browse_layout.addLayout(search_layout)
        
        # Results table
        results_table = QTableWidget()
        results_table.setColumnCount(3)
        results_table.setHorizontalHeaderLabels(["Source", "Target", "Match %"])
        results_table.horizontalHeader().setStretchLastSection(False)
        results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        results_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        results_table.setColumnWidth(2, 80)
        results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        browse_layout.addWidget(results_table)
        
        def search_tm():
            search_term = search_input.text().strip()
            if not search_term or not self.tm_database:
                results_table.setRowCount(0)
                return
            
            # Get activated TM IDs for current project
            tm_ids = None
            if hasattr(self, 'tm_metadata_mgr') and self.tm_metadata_mgr and self.current_project:
                project_id = self.current_project.id if hasattr(self.current_project, 'id') else None
                if project_id:
                    tm_ids = self.tm_metadata_mgr.get_active_tm_ids(project_id)
            
            try:
                matches = self.tm_database.search_all(search_term, tm_ids=tm_ids, max_matches=50)
                
                results_table.setRowCount(len(matches))
                for i, match in enumerate(matches):
                    source_item = QTableWidgetItem(match.get('source', ''))
                    target_item = QTableWidgetItem(match.get('target', ''))
                    match_pct_item = QTableWidgetItem(f"{match.get('match_pct', 0)}%")
                    
                    # Color code by match percentage
                    match_pct = match.get('match_pct', 0)
                    if match_pct == 100:
                        color = "#22c55e"
                    elif match_pct >= 95:
                        color = "#3b82f6"
                    elif match_pct >= 85:
                        color = "#f59e0b"
                    else:
                        color = "#ef4444"
                    
                    match_pct_item.setForeground(QColor(color))
                    match_pct_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    
                    results_table.setItem(i, 0, source_item)
                    results_table.setItem(i, 1, target_item)
                    results_table.setItem(i, 2, match_pct_item)
                
            except Exception as e:
                QMessageBox.critical(dialog, "Search Error", f"Failed to search TM: {e}")
        
        search_input.returnPressed.connect(search_tm)
        search_btn = QPushButton(self.tr("🔍 Search"))
        search_btn.clicked.connect(search_tm)
        search_layout.addWidget(search_btn)
        
        browse_layout.addWidget(QLabel(self.tr("<i>Tip: Press Enter to search</i>")))
        
        tabs.addTab(browse_tab, "🔍 Browse")
        
        # Add tabs to dialog
        layout.addWidget(tabs)
        
        # Close button
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        
        dialog.exec()
    
    def _open_url(self, url):
        """Open a URL in system default browser"""
        import webbrowser
        try:
            webbrowser.open(url)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not open URL: {e}")

    def _open_superdocs_tab(self):
        """Open the online Supervertaler Help in the user's browser."""
        try:
            # Prefer opening the published online docs
            self._open_url("https://docs.supervertaler.com/workbench/")
        except Exception:
            QMessageBox.information(
                self,
                "Supervertaler Help",
                "Supervertaler Help is available online at https://docs.supervertaler.com/workbench/"
            )

    def check_for_updates(self):
        """Check GitHub for the latest Supervertaler release and report whether the user is up to date."""
        # Temporary simplified behavior: QtNetwork HTTPS update checks can fail/hang on some Windows
        # environments (seen as QNetworkReply "Unknown error"). Until this is revisited, open the
        # GitHub releases page directly.
        try:
            self._open_url("https://github.com/Supervertaler/Supervertaler-Workbench/releases/latest")
        except Exception as e:
            QMessageBox.warning(self, "Check for Updates", f"Could not open releases page: {e}")
        return

        if getattr(self, "_update_check_in_progress", False):
            QMessageBox.information(self, "Check for Updates", "An update check is already in progress.")
            return

        self._update_check_in_progress = True
        self._update_check_user_canceled = False
        self._update_check_last_stage = "start"
        self._update_check_any_message_shown = False
        self._update_check_python_fallback_started = False

        def _log_update(msg: str):
            # Always emit something to the terminal in dev mode.
            try:
                print(f"[UPDATE] {msg}", flush=True)
            except Exception:
                pass
            # Also use the app log if available.
            try:
                self.log(f"[Update] {msg}")
            except Exception:
                pass

        _log_update("Checking for updates...")

        progress = QProgressDialog("Checking for updates...", "Cancel", 0, 0, self)
        progress.setWindowTitle(self.tr("Check for Updates"))
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setMinimumDuration(0)
        # Prevent the dialog from auto-disappearing when range is (0, 0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()

        try:
            progress.destroyed.connect(lambda *_: _log_update("Progress dialog destroyed"))
        except Exception:
            pass

        # Keep references so Qt objects aren't GC'd mid-request
        self._update_check_progress = progress

        if not hasattr(self, "_update_check_net_mgr") or self._update_check_net_mgr is None:
            self._update_check_net_mgr = QNetworkAccessManager(self)

        api_url = QUrl("https://api.github.com/repos/Supervertaler/Supervertaler-Workbench/releases/latest")
        fallback_url = QUrl("https://github.com/Supervertaler/Supervertaler-Workbench/releases/latest")
        timeout_ms = 15000

        self._update_check_fallback_attempted = False
        # Monotonic request id so stale replies/timeouts can't close/delete the active attempt.
        self._update_check_request_id = int(getattr(self, "_update_check_request_id", 0)) + 1
        active_request_id = self._update_check_request_id

        def is_active_request() -> bool:
            try:
                return bool(getattr(self, "_update_check_in_progress", False)) and int(getattr(self, "_update_check_request_id", 0)) == active_request_id
            except Exception:
                return False

        def abort_current_reply():
            try:
                r = getattr(self, "_update_check_reply", None)
                if r and not r.isFinished():
                    r.abort()
            except Exception:
                pass

        def start_request(kind: str):
            """Start either the API or fallback request."""
            if kind not in {"api", "fallback"}:
                kind = "api"

            self._update_check_last_stage = f"request:{kind}"
            _log_update(f"Starting {kind} request")

            abort_current_reply()

            if kind == "fallback":
                try:
                    progress.setLabelText("Checking for updates (fallback)...")
                except Exception:
                    pass
                url = fallback_url
            else:
                try:
                    progress.setLabelText("Checking for updates...")
                except Exception:
                    pass
                url = api_url

            request = QNetworkRequest(url)
            request.setRawHeader(b"User-Agent", f"Supervertaler/{__version__}".encode("utf-8", errors="ignore"))
            if kind == "api":
                request.setRawHeader(b"Accept", b"application/vnd.github+json")

            r = self._update_check_net_mgr.get(request)
            r.setProperty("sv_update_check_kind", kind)
            r.setProperty("sv_update_check_request_id", active_request_id)
            self._update_check_reply = r

            # Helpful when QtNetwork TLS is misconfigured on Windows.
            try:
                r.sslErrors.connect(
                    lambda errs, rr=r: _log_update(
                        "SSL errors: " + "; ".join(getattr(e, "errorString", lambda: str(e))() for e in errs)
                    )
                )
            except Exception:
                pass

            # Restart timeout for each attempt
            try:
                if hasattr(self, "_update_check_timer") and self._update_check_timer:
                    self._update_check_timer.stop()
            except Exception:
                pass
            timeout_timer.start(timeout_ms)

            r.finished.connect(lambda rr=r: on_finished(rr))

        def finish():
            try:
                try:
                    if hasattr(self, "_update_check_timer") and self._update_check_timer:
                        self._update_check_timer.stop()
                except Exception:
                    pass
                try:
                    progress.close()
                except Exception:
                    pass
            finally:
                # If we got here without showing *any* result/error dialog and the user didn't cancel,
                # surface a minimal explanation so it never feels like a no-op.
                try:
                    unexpected = (not bool(getattr(self, "_update_check_user_canceled", False))) and (not bool(getattr(self, "_update_check_any_message_shown", False)))
                    if unexpected:
                        stage = str(getattr(self, "_update_check_last_stage", "(unknown)"))
                        QMessageBox.warning(
                            self,
                            "Check for Updates",
                            "The update check ended unexpectedly.\n\n"
                            f"Stage: {stage}\n\n"
                            "This is usually caused by a network/SSL issue or a blocked request.",
                        )
                except Exception:
                    pass

                self._update_check_in_progress = False
                try:
                    self._update_check_user_canceled = False
                except Exception:
                    pass
                try:
                    if hasattr(self, "_update_check_reply") and self._update_check_reply:
                        self._update_check_reply.deleteLater()
                except Exception:
                    pass
                self._update_check_reply = None
                self._update_check_timer = None
                self._update_check_progress = None

                try:
                    self._update_check_python_fallback_started = False
                except Exception:
                    pass

                try:
                    self._update_check_fallback_attempted = False
                except Exception:
                    pass

                try:
                    self._update_check_last_stage = "finished"
                except Exception:
                    pass

        def show_offline_error(details: str):
            try:
                self._update_check_any_message_shown = True
                self._update_check_last_stage = "offline_error"
            except Exception:
                pass
            msg = (
                "Could not check for updates (offline or GitHub is unavailable).\n\n"
                f"Details: {details}\n\n"
                "Open the releases page in your browser?"
            )
            choice = QMessageBox.question(
                self,
                "Check for Updates",
                msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if choice == QMessageBox.StandardButton.Yes:
                self._open_url("https://github.com/Supervertaler/Supervertaler-Workbench/releases")

        def start_python_fallback(reason: str = ""):
            """Fallback that uses Python's HTTPS stack (urllib) instead of QtNetwork."""
            try:
                if bool(getattr(self, "_update_check_python_fallback_started", False)):
                    _log_update("Python fallback already started; ignoring")
                    return
                self._update_check_python_fallback_started = True
            except Exception:
                pass

            # Stop Qt timeout timer now that we're using Python's stack.
            try:
                if hasattr(self, "_update_check_timer") and self._update_check_timer:
                    self._update_check_timer.stop()
            except Exception:
                pass

            # Abort any in-flight Qt request to avoid late signals.
            abort_current_reply()

            try:
                self._update_check_last_stage = "python_fallback"
            except Exception:
                pass

            reason = (reason or "").strip()
            if reason:
                _log_update(f"Trying Python fallback ({reason})")
            else:
                _log_update("Trying Python fallback")

            try:
                progress.setLabelText("Checking for updates (Python fallback)...")
            except Exception:
                pass

            import threading

            def worker():
                try:
                    info = self._fetch_latest_release_info_from_github(timeout_seconds=6)
                    version = (info.get("version") or "") if isinstance(info, dict) else ""
                    url = (info.get("url") or "") if isinstance(info, dict) else ""

                    def on_ok():
                        if not is_active_request():
                            return
                        if bool(getattr(self, "_update_check_user_canceled", False)):
                            finish()
                            return
                        handle_latest(version, url)
                        finish()

                    QTimer.singleShot(0, on_ok)
                except Exception as e:

                    def on_err():
                        if not is_active_request():
                            return
                        if bool(getattr(self, "_update_check_user_canceled", False)):
                            finish()
                            return
                        show_offline_error(str(e))
                        finish()

                    QTimer.singleShot(0, on_err)

            threading.Thread(target=worker, daemon=True).start()

        def on_timeout():
            # Ignore stale timers from previous attempts.
            try:
                if int(getattr(self, "_update_check_request_id", 0)) != active_request_id:
                    return
            except Exception:
                return

            # If Python fallback is already running, don't start anything else.
            try:
                if bool(getattr(self, "_update_check_python_fallback_started", False)):
                    return
            except Exception:
                return

            kind = "api"
            try:
                r = getattr(self, "_update_check_reply", None)
                if r:
                    k = r.property("sv_update_check_kind")
                    kind = str(k) if k else "api"
            except Exception:
                kind = "api"

            if kind == "api" and not getattr(self, "_update_check_fallback_attempted", False):
                self._update_check_fallback_attempted = True
                start_request("fallback")
                return

            try:
                abort_current_reply()
            finally:
                try:
                    self._update_check_last_stage = "timeout"
                except Exception:
                    pass
                # Try Python fallback first; if it fails it will show the offline dialog.
                start_python_fallback(f"timeout after {timeout_ms // 1000}s")

        timeout_timer = QTimer(self)
        timeout_timer.setSingleShot(True)
        timeout_timer.timeout.connect(on_timeout)
        timeout_timer.start(timeout_ms)
        self._update_check_timer = timeout_timer

        def on_cancel():
            try:
                self._update_check_user_canceled = True
            except Exception:
                pass
            try:
                self._update_check_last_stage = "canceled"
            except Exception:
                pass
            _log_update("User canceled update check")
            abort_current_reply()
            finish()

        progress.canceled.connect(on_cancel)

        def handle_latest(latest_version_text: str, latest_url: str):
            try:
                self._update_check_any_message_shown = True
                self._update_check_last_stage = "result"
            except Exception:
                pass
            latest_version_text = (latest_version_text or "").strip()
            latest_url = (latest_url or "").strip() or "https://github.com/Supervertaler/Supervertaler-Workbench/releases"

            current = self._normalize_version_tuple(__version__)
            latest = self._normalize_version_tuple(latest_version_text)

            if not latest:
                QMessageBox.information(
                    self,
                    "Check for Updates",
                    "Could not determine the latest version.\n\nYou can check releases on GitHub.",
                )
                self._open_url(latest_url)
                return

            current_text = self._format_version_for_display(__version__)
            latest_text = self._format_version_for_display(latest_version_text)

            if current < latest:
                msg = (
                    f"You are running {current_text}.\n"
                    f"The latest version is {latest_text}.\n\n"
                    "Open the latest release page?"
                )
                choice = QMessageBox.question(
                    self,
                    "Update Available",
                    msg,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if choice == QMessageBox.StandardButton.Yes:
                    self._open_url(latest_url)
            elif current == latest:
                QMessageBox.information(
                    self,
                    "Up to Date",
                    f"You are up to date.\n\nCurrent: {current_text}\nLatest: {latest_text}",
                )
            else:
                QMessageBox.information(
                    self,
                    "Newer Than Latest",
                    f"You are running {current_text}, which is newer than the latest published release ({latest_text}).",
                )

        def on_finished(reply_obj):
            should_finish = True
            # Stale reply (e.g., API reply finishing after we already started fallback).
            try:
                rid = reply_obj.property("sv_update_check_request_id")
                if rid is not None and int(rid) != active_request_id:
                    try:
                        reply_obj.deleteLater()
                    except Exception:
                        pass
                    return
            except Exception:
                # If we can't read the property, play it safe: only proceed if it's the active reply.
                pass

            try:
                if reply_obj is not getattr(self, "_update_check_reply", None):
                    try:
                        reply_obj.deleteLater()
                    except Exception:
                        pass
                    return
            except Exception:
                pass

            try:
                kind = "api"
                try:
                    k = reply_obj.property("sv_update_check_kind")
                    kind = str(k) if k else "api"
                except Exception:
                    kind = "api"

                # If aborted/cancelled/errored, show a useful message
                if reply_obj.error():
                    err = reply_obj.errorString() or "Network error"
                    try:
                        err_code = int(reply_obj.error())
                    except Exception:
                        err_code = -1
                    # Only treat real user cancel/abort as silent. Unexpected aborts should be surfaced.
                    if reply_obj.error() == QNetworkReply.NetworkError.OperationCanceledError:
                        _log_update("Network request aborted (OperationCanceledError)")
                        if not bool(getattr(self, "_update_check_user_canceled", False)):
                            show_offline_error("Update check was aborted unexpectedly.")
                        return

                    _log_update(f"Network error ({kind}) [{err_code}]: {err}")

                    # API can be blocked on some networks; fall back to github.com
                    if kind == "api" and not getattr(self, "_update_check_fallback_attempted", False):
                        self._update_check_fallback_attempted = True
                        # Do not finish here; the fallback attempt becomes the active request.
                        start_request("fallback")
                        should_finish = False
                        return

                    # QtNetwork HTTPS can fail on some systems (often reported as "Unknown error").
                    # Try Python's HTTPS stack before giving up.
                    start_python_fallback(f"qt_network_error {err_code}")
                    should_finish = False
                    return

                if kind == "fallback":
                    # Try to read the redirect location (preferred) because /releases/latest usually 302's.
                    latest_url_text = "https://github.com/Supervertaler/Supervertaler-Workbench/releases"
                    latest_version_text = ""

                    try:
                        status = reply_obj.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
                        status_code = int(status) if status is not None else 0
                    except Exception:
                        status_code = 0

                    try:
                        location = bytes(reply_obj.rawHeader(b"Location")).decode("utf-8", errors="replace").strip()
                    except Exception:
                        location = ""

                    if status_code in {301, 302, 303, 307, 308} and location:
                        if location.startswith("/"):
                            latest_url_text = "https://github.com" + location
                        else:
                            latest_url_text = location

                        m = re.search(r"v?\d+(?:\.\d+)+", location)
                        if m:
                            latest_version_text = m.group(0)
                    else:
                        # No redirect header; fall back to parsing the HTML.
                        raw = bytes(reply_obj.readAll()).decode("utf-8", errors="replace")
                        m = re.search(r"Release\s+v?(\d+(?:\.\d+)+)", raw, re.IGNORECASE)
                        if m:
                            latest_version_text = m.group(1)

                    handle_latest(latest_version_text, latest_url_text)
                    return

                # API JSON path
                raw = bytes(reply_obj.readAll())
                data = json.loads(raw.decode("utf-8", errors="replace"))
                tag = (data.get("tag_name") or "").strip()
                name = (data.get("name") or "").strip()
                latest_version_text = tag or name
                latest_url = (data.get("html_url") or "").strip() or "https://github.com/Supervertaler/Supervertaler-Workbench/releases"

                handle_latest(latest_version_text, latest_url)
            except Exception as e:
                _log_update(f"Exception while parsing update info: {e}")
                show_offline_error(str(e))
            finally:
                if should_finish:
                    finish()

        start_request("api")

    def _fetch_latest_release_info_from_github(self, timeout_seconds: int = 6) -> dict:
        """Return {version, url} for the latest GitHub release, best-effort.

        Uses the public GitHub API. No auth required.
        """
        import urllib.request

        api_url = "https://api.github.com/repos/Supervertaler/Supervertaler-Workbench/releases/latest"
        req = urllib.request.Request(
            api_url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"Supervertaler/{__version__}",
            },
        )

        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))

        tag = (data.get("tag_name") or "").strip()
        name = (data.get("name") or "").strip()
        version = tag or name
        url = (data.get("html_url") or "").strip() or "https://github.com/Supervertaler/Supervertaler-Workbench/releases"

        return {"version": version, "url": url}

    def _normalize_version_tuple(self, version_text: str) -> tuple:
        """Convert a version like 'v1.9.100' to a comparable tuple of ints."""
        if not version_text:
            return ()
        text = str(version_text).strip()
        if text.lower().startswith("v"):
            text = text[1:]

        parts = [int(p) for p in re.findall(r"\d+", text)]
        return tuple(parts)

    def _format_version_for_display(self, version_text: str) -> str:
        """Return a user-friendly version string like 'v1.9.100'."""
        if not version_text:
            return "(unknown)"
        text = str(version_text).strip()
        if not text.lower().startswith("v"):
            return f"v{text}"
        return text

    def _build_version_info_text(self) -> str:
        """Build a support-friendly version/system info block for clipboard."""
        import platform
        from PyQt6.QtCore import QT_VERSION_STR, PYQT_VERSION_STR

        lines = []
        lines.append(f"Supervertaler {self._format_version_for_display(__version__)}")
        try:
            lines.append(f"Release date: {__release_date__}")
        except Exception:
            pass
        try:
            lines.append(f"Edition: {__edition__} (phase {__phase__})")
        except Exception:
            pass

        lines.append("")
        lines.append(f"OS: {platform.platform()}")
        lines.append(f"Architecture: {platform.machine()}")
        lines.append(f"Python: {platform.python_version()} ({platform.python_implementation()})")
        lines.append(f"Qt: {QT_VERSION_STR}")
        lines.append(f"PyQt: {PYQT_VERSION_STR}")
        lines.append(f"Frozen (PyInstaller): {bool(getattr(sys, 'frozen', False))}")
        lines.append(f"Executable: {sys.executable}")

        # Diagnostic log location – makes support easier: users paste this
        # block, and the path to attach is right there in the output.
        try:
            log_path = self._get_diagnostic_log_path()
            if log_path is not None:
                lines.append("")
                lines.append(f"Diagnostic log: {log_path}")
                lines.append(
                    "  (Help \u2192 Open Diagnostic Log – attach this file when reporting issues)"
                )
        except Exception:
            pass

        return "\n".join(lines)

    def copy_version_info_to_clipboard(self):
        """Copy version info to clipboard for easy sharing in support requests."""
        try:
            text = self._build_version_info_text()
            clipboard = QApplication.clipboard()
            clipboard.setText(text)
            QMessageBox.information(
                self,
                "Version Info Copied",
                "Version info has been copied to your clipboard.\n\n"
                "Tip: Paste it into an email or GitHub issue.",
            )
        except Exception as e:
            QMessageBox.warning(self, "Copy Failed", f"Could not copy version info: {e}")

    def _get_diagnostic_log_path(self):
        """Return the Path to the active diagnostic log, or None if unknown.

        Preferred source is sys._supervertaler_log_path (set by
        _setup_diagnostic_log in main()). If that's missing for any reason
        we compute the expected default location so the menu action still
        works – the folder might be empty but at least the user sees where
        to look.
        """
        try:
            path = getattr(sys, '_supervertaler_log_path', None)
            if path is not None:
                return Path(path)
        except Exception:
            pass
        try:
            return Path(self.user_data_path) / "workbench" / "logs" / "supervertaler.log"
        except Exception:
            return None

    def open_diagnostic_log(self):
        """Open the diagnostic log file in the system's default text editor."""
        log_path = self._get_diagnostic_log_path()
        if log_path is None:
            QMessageBox.warning(
                self, "Log Unavailable",
                "The diagnostic log path could not be determined."
            )
            return
        if not log_path.exists():
            QMessageBox.information(
                self, "No Log Yet",
                f"No diagnostic log has been written yet.\n\nExpected location:\n{log_path}"
            )
            return
        # QDesktopServices with a file:// URL opens the OS default handler
        # for .log (Notepad on Windows, TextEdit on macOS, etc.).
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_path)))

    def open_diagnostic_log_folder(self):
        """Reveal the folder that contains the diagnostic log files."""
        log_path = self._get_diagnostic_log_path()
        log_dir = log_path.parent if log_path is not None else None
        if log_dir is None:
            QMessageBox.warning(
                self, "Log Folder Unavailable",
                "The diagnostic log folder could not be determined."
            )
            return
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir)))
    
    def _show_ahk_setup_from_menu(self):
        """Show AutoHotkey setup dialog from Help menu"""
        if hasattr(self, 'lookup_tab') and self.lookup_tab:
            self.lookup_tab._show_autohotkey_setup_dialog()
        else:
            QMessageBox.warning(
                self,
                "SuperLookup Not Available",
                "SuperLookup tab is not available. Please restart the application."
            )

    def show_about(self):
        """Show about dialog with clickable website link"""
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("About Supervertaler Workbench"))
        dialog.setMinimumWidth(400)
        
        layout = QVBoxLayout(dialog)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Title
        title = QLabel(f"<h2>Supervertaler Workbench v{__version__}</h2>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Description
        desc = QLabel(self.tr("<p><b>AI-enhanced tool for translators & writers</b></p>"))
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(desc)

        # Links section
        links_label = QLabel(
            '<p style="text-align: center;">'
            '<b>Project:</b> <a href="https://supervertaler.com/" style="color: #1976D2; text-decoration: none;">supervertaler.com</a><br>'
            '<b>Author:</b> <a href="https://beijer.uk/" style="color: #1976D2; text-decoration: none;">beijer.uk</a>'
            '</p>'
        )
        links_label.setOpenExternalLinks(True)
        links_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(links_label)

        # Additional info
        info = QLabel(
            "<p style='text-align: center;'><b>License:</b> MIT</p>"
            "<hr>"
            "<p style='text-align: center; color: #666; font-size: 9pt;'>"
            "Built with PyQt6 • Supports OpenAI, Claude, Gemini & Ollama"
            "</p>"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Buttons
        btn_row = QHBoxLayout()
        copy_btn = QPushButton(self.tr("📋 Copy Version Info"))
        copy_btn.setToolTip(self.tr("Copy version and system info to clipboard"))
        copy_btn.clicked.connect(self.copy_version_info_to_clipboard)
        btn_row.addWidget(copy_btn)

        update_btn = QPushButton(self.tr("🔄 Check for Updates..."))
        update_btn.setToolTip(self.tr("Check whether you are running the latest Supervertaler release"))
        update_btn.clicked.connect(self.check_for_updates)
        btn_row.addWidget(update_btn)

        btn_row.addStretch(1)

        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

        dialog.exec()
    
    # closeEvent lives with the tray code (search "def closeEvent"); a second
    # definition here used to shadow it and stop the app quitting (#255).

    def _cleanup_web_views(self):
        """Clean up WebEngine views - DISABLED to prevent crash"""
        # WebEngine cleanup has been disabled because it was causing Python crashes
        # on program exit. Qt will handle WebEngine cleanup automatically, though
        # you may see a "Release of profile requested" warning which is harmless.
        print("[WebEngine Cleanup] Skipping manual cleanup - letting Qt handle it")
        pass
    
    def _close_detached_log_windows(self):
        """Close all detached log windows when main window closes"""
        try:
            if hasattr(self, 'detached_log_windows'):
                for log_window in list(self.detached_log_windows):
                    try:
                        log_window.close()
                    except:
                        pass
                self.detached_log_windows.clear()
        except:
            pass

    # ── Okapi Sidecar management ─────────────────────────────────

    # =========================================================================
    # LLM TRANSLATION INTEGRATION
    # =========================================================================

    # =========================================================================
    # FUZZY FIXER
    # =========================================================================

    def fuzzy_fix_current_segment(self, *args):
        """FuzzyFixer: adapt the currently selected fuzzy TM match to the
        current source using the AI (Match Panel button / Ctrl+Alt+F).

        Reads the match shown in the Match Panel TM Source/Target boxes, checks
        it falls within the configured % range, then delegates to
        translate_current_segment() with the match injected into the prompt.
        """
        if not self.current_project:
            QMessageBox.warning(self, "No Project", "Open a project first.")
            return

        matches = getattr(self, 'match_panel_tm_matches', None)
        if not matches:
            QMessageBox.information(
                self, "FuzzyFixer",
                "No TM match is shown for this segment, so there is nothing to fix.\n\n"
                "Select a segment that has a fuzzy TM match in the Match Panel.")
            return

        idx = getattr(self, 'match_panel_tm_index', 0)
        if idx < 0 or idx >= len(matches):
            idx = 0
        match = matches[idx]

        # Support both dict and TranslationMatch objects (mirrors _update_match_panel_tm_display)
        if isinstance(match, dict):
            tm_source = match.get('source', '')
            tm_target = match.get('target', '')
            tm_name = match.get('tm_name', '') or 'TM'
            match_pct = match.get('match_pct', 0)
        else:
            tm_source = getattr(match, 'compare_source', '') or getattr(match, 'source', '')
            tm_target = getattr(match, 'target', '')
            md = match.metadata if hasattr(match, 'metadata') and match.metadata else {}
            tm_name = md.get('tm_name', '') or 'TM'
            match_pct = getattr(match, 'relevance', 0)

        try:
            match_pct_int = int(round(float(match_pct or 0)))
        except (TypeError, ValueError):
            match_pct_int = 0

        if not (tm_target or '').strip():
            QMessageBox.information(self, "FuzzyFixer",
                                    "The selected TM match has no target text to adapt.")
            return

        min_pct = int(getattr(self, 'fuzzy_fixer_min_pct', 75))
        max_pct = int(getattr(self, 'fuzzy_fixer_max_pct', 99))
        if match_pct_int > max_pct:
            QMessageBox.information(
                self, "FuzzyFixer",
                f"This is a {match_pct_int}% match. FuzzyFixer only adapts matches "
                f"between {min_pct}% and {max_pct}% – exact matches need no fixing.")
            return
        if match_pct_int < min_pct:
            QMessageBox.information(
                self, "FuzzyFixer",
                f"This is only a {match_pct_int}% match, below the FuzzyFixer minimum "
                f"of {min_pct}%. Adjust the range in Settings → AI Settings if needed.")
            return

        fuzzy_match = {
            'source': tm_source,
            'target': tm_target,
            'match_pct': match_pct_int,
            'tm_name': tm_name,
        }
        self.translate_current_segment(fuzzy_match=fuzzy_match)

    def _show_fuzzy_fix_diff(self, tm_target: str, new_target: str):
        """Render a track-changes view (AI output vs original TM target) in the
        Match Panel TM Target box, so the user sees what the FuzzyFixer changed.

        Transient: it is replaced the next time the TM display refreshes.
        """
        if not tm_target or not new_target:
            return
        if not (hasattr(self, 'match_panel_tm_target') and self.match_panel_tm_target):
            return
        try:
            # Reuse the existing track-changes renderer: 'current' is the new
            # target, 'tm_source' is the original target being annotated.
            self._set_compare_panel_text_with_diff(self.match_panel_tm_target, new_target, tm_target)
        except Exception:
            self.match_panel_tm_target.setPlainText(new_target)

    def _update_fuzzy_fixer_btn_state(self, match_pct):
        """Enable the Match Panel FuzzyFixer button only when the current match
        percentage is within the configured range (None = no match)."""
        btn = getattr(self, 'match_panel_fuzzy_fixer_btn', None)
        if btn is None:
            return
        enabled = False
        if match_pct is not None:
            try:
                pct = int(round(float(match_pct or 0)))
                min_pct = int(getattr(self, 'fuzzy_fixer_min_pct', 75))
                max_pct = int(getattr(self, 'fuzzy_fixer_max_pct', 99))
                enabled = min_pct <= pct <= max_pct
            except (TypeError, ValueError):
                enabled = False
        btn.setEnabled(enabled)

    # =========================================================================
    # AUTOTAGGER
    # =========================================================================

    def autotag_current_segment(self, *args):
        """AutoTagger: place the source segment's inline tags into the current
        target translation using the AI (toolbar button / menu / Ctrl+Alt+G).

        The translation wording is never changed — only tags are inserted. The
        result is validated (same tags as source, words unchanged, well-formed)
        before it is written; on failure the target is left untouched.
        """
        current_row = self.table.currentRow()
        if current_row < 0 or not self.current_project:
            QMessageBox.warning(self, "No Selection", "Please select a segment first.")
            return
        segment = self.current_project.segments[current_row]

        if not autotag_extract_tags(segment.source):
            QMessageBox.information(self, "AutoTagger",
                                    "The source segment has no inline tags, so there's nothing to place.")
            return
        if not (segment.target or "").strip():
            QMessageBox.information(self, "AutoTagger",
                                    "Translate this segment first — AutoTagger places tags into an existing translation.")
            return

        clean_target = strip_all_tags(segment.target)

        # Resolve provider/model/keys (same approach as translate_current_segment)
        settings = self.load_llm_settings()
        provider = settings.get('provider', 'openai')
        model = self._resolve_provider_model(settings, provider, 'gpt-6.1-sol')
        if provider == 'ollama':
            api_keys = {'ollama': 'not-needed'}
        else:
            api_keys = self.load_api_keys() or {}
            has_key = (provider in api_keys
                       or (provider == 'gemini' and 'google' in api_keys)
                       or provider == 'custom_openai')
            if not has_key:
                reply = QMessageBox.question(
                    self, f"{provider.title()} API Key Missing",
                    f"{self._api_key_problem(provider) or f'No API key set for {provider.title()}.'} Configure it now?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.Yes:
                    self._go_to_settings_tab("AI Settings")
                return

        try:
            client = self.create_llm_client(provider, model, api_keys, settings)
        except Exception as e:
            QMessageBox.critical(self, "AutoTagger", f"Could not initialise the AI client:\n\n{e}")
            return

        src_lang = self.current_project.source_lang
        tgt_lang = self.current_project.target_lang
        self.status_bar.showMessage(f"🏷️ AutoTagger placing tags in segment #{segment.id}...")
        QApplication.processEvents()
        try:
            new_target, reason = place_tags_via_llm(
                client, self.prompt_manager_qt, segment.source, clean_target, src_lang, tgt_lang)
        except Exception as e:
            self.log(f"✗ AutoTagger error: {e}")
            QMessageBox.critical(self, "AutoTagger", f"AutoTagger failed:\n\n{e}")
            self.status_bar.showMessage("AutoTagger failed", 3000)
            return

        if not new_target:
            self.log(f"⚠ AutoTagger could not tag #{segment.id}: {reason}")
            QMessageBox.warning(self, "AutoTagger",
                                "Couldn't place the tags reliably, so the target was left unchanged.\n\n"
                                f"Reason: {reason}")
            self.status_bar.showMessage("AutoTagger: target left unchanged", 3000)
            return

        if new_target == segment.target:
            self.status_bar.showMessage("🏷️ AutoTagger: tags already correct", 3000)
            return

        _undo_old_target = segment.target
        segment.target = new_target
        try:
            self.record_undo_state(segment.id, _undo_old_target, new_target, segment.status, segment.status)
        except Exception:
            pass

        # Push the DISPLAY form into the cell, not the raw text: the grid renders
        # target cells through compact_tags + apply_invisible_replacements at load
        # time, so writing raw text here left the new content rendered differently
        # from every other cell — most visibly a tab inside a tag (memoQ's
        # <mq:ch val="\t" />) showing as a bare gap in the target while the source
        # cell showed the → marker, which reads as "the tag lost its content".
        target_widget = self.table.cellWidget(current_row, 3)
        display_target = new_target
        try:
            if target_widget is not None and getattr(target_widget, '_compact_tag_map', None) is not None:
                display_target = compact_tags(display_target, target_widget._compact_tag_map)
            display_target = self.apply_invisible_replacements(display_target)
        except Exception:
            display_target = new_target   # display nicety only; never block the write
        if target_widget and isinstance(target_widget, EditableGridTextEditor):
            target_widget.setPlainText(display_target)
        else:
            self.table.setItem(current_row, 3, QTableWidgetItem(display_target))
        self.update_status_icon(current_row, segment.status)
        self._auto_resize_single_row(current_row)
        self.project_modified = True
        self.update_window_title()
        n = len(autotag_extract_tags(segment.source))
        self.log(f"🏷️ AutoTagger placed {n} tag(s) in #{segment.id}")
        self.status_bar.showMessage(f"🏷️ AutoTagger placed {n} tag(s) in segment #{segment.id}", 3000)

    # =========================================================================
    # GRID QUICKLAUNCHER
    # =========================================================================

    def _quicklauncher_get_selection_text(self, widget: QTextEdit) -> str:
        """Get selected text (or full text) from a QTextEdit, normalized for LLMs."""
        cursor = widget.textCursor()
        text = cursor.selectedText() if cursor.hasSelection() else widget.toPlainText()
        text = (text or "").replace('\u2029', '\n')

        # Reverse invisible-character display markers if present
        try:
            if hasattr(self, 'reverse_invisible_replacements'):
                text = self.reverse_invisible_replacements(text)
        except Exception:
            pass

        return text.strip()
    
    def _build_quicklauncher_document_context(self, mode: str = "both") -> str:
        """Build document context for QuickLauncher prompts.
        
        Args:
            mode: One of:
                - "both": Include both source and target text (for proofreading)
                - "source": Include only source text (for translation/terminology questions)
                - "target": Include only target text (for consistency/style analysis)
        
        Returns a formatted string with segments from the project for context.
        Uses the 'quicklauncher_context_percent' setting (default: 50% of total segments).
        """
        if not hasattr(self, 'current_project') or not self.current_project or not self.current_project.segments:
            return "(No project context available)"
        
        try:
            # Get settings
            general_prefs = self.load_general_settings()
            context_percent = general_prefs.get('quicklauncher_context_percent',
                                               general_prefs.get('quickmenu_context_percent', 50))  # Default: 50%
            max_context_segments = general_prefs.get('quicklauncher_context_max',
                                                     general_prefs.get('quickmenu_context_max', 100))  # Safety limit
            
            # Calculate how many segments to include
            total_segments = len(self.current_project.segments)
            num_segments = min(
                int(total_segments * context_percent / 100),
                max_context_segments
            )
            
            if num_segments == 0:
                return "(Document context disabled)"
            
            # Get segments (from start of document)
            context_segments = self.current_project.segments[:num_segments]
            
            # Format segments based on mode
            context_parts = []
            mode_labels = {"both": "SOURCE + TARGET", "source": "SOURCE ONLY", "target": "TARGET ONLY"}
            context_parts.append(f"=== DOCUMENT CONTEXT ({mode_labels.get(mode, 'UNKNOWN')}) ===")
            context_parts.append(f"(Showing {num_segments} of {total_segments} segments - {context_percent}%)")
            context_parts.append("")
            
            for seg in context_segments:
                if mode == "both":
                    # Source + Target
                    context_parts.append(f"[{seg.id}] {seg.source}")
                    if seg.target and seg.target.strip():
                        context_parts.append(f"    → {seg.target}")
                elif mode == "source":
                    # Source only
                    context_parts.append(f"[{seg.id}] {seg.source}")
                elif mode == "target":
                    # Target only (skip empty targets)
                    if seg.target and seg.target.strip():
                        context_parts.append(f"[{seg.id}] {seg.target}")
                context_parts.append("")  # Blank line between segments
            
            return "\n".join(context_parts)
            
        except Exception as e:
            return f"(Error building document context: {e})"

    def _quicklauncher_show_result_dialog(self, title: str, output_text: str, apply_callback=None):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QDialogButtonBox, QPushButton, QApplication

        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumSize(700, 450)
        layout = QVBoxLayout(dlg)

        out = QTextEdit()
        out.setReadOnly(True)
        out.setMarkdown(output_text or "")
        layout.addWidget(out)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_copy = QPushButton(self.tr("Copy"))
        buttons.addButton(btn_copy, QDialogButtonBox.ButtonRole.ActionRole)
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(output_text or ""))

        if apply_callback is not None:
            btn_apply = QPushButton(self.tr("Replace"))
            buttons.addButton(btn_apply, QDialogButtonBox.ButtonRole.AcceptRole)
            btn_apply.clicked.connect(lambda: (apply_callback(), dlg.accept()))

        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        dlg.exec()

    def _get_selected_segments_with_rows(self) -> List[Tuple[int, Segment]]:
        """Return selected segments across active views as (row_index, segment)."""
        if not self.current_project:
            return []

        selected_ids = set()

        try:
            for seg in self.get_selected_segments_from_grid():
                if hasattr(seg, 'id'):
                    selected_ids.add(seg.id)
        except Exception:
            pass

        try:
            for seg in self.get_selected_segments_from_list():
                if hasattr(seg, 'id'):
                    selected_ids.add(seg.id)
        except Exception:
            pass

        if not selected_ids:
            return []

        segments_with_rows: List[Tuple[int, Segment]] = []
        for idx, seg in enumerate(self.current_project.segments):
            if seg.id in selected_ids:
                segments_with_rows.append((idx, seg))

        return segments_with_rows

    def _get_filtered_segments_with_rows(self) -> List[Tuple[int, Segment]]:
        """Return currently visible/filtered segments as (row_index, segment)."""
        if not self.current_project:
            return []

        # Check if table is currently filtered
        if not hasattr(self, 'table') or self.table.rowCount() == 0:
            return []

        visible_segments: List[Tuple[int, Segment]] = []
        
        # Iterate through visible rows in the table
        for visual_row in range(self.table.rowCount()):
            if not self.table.isRowHidden(visual_row):
                # Get the segment from this row
                # The visual row index corresponds to the segment index in the project
                if visual_row < len(self.current_project.segments):
                    seg = self.current_project.segments[visual_row]
                    visible_segments.append((visual_row, seg))

        return visible_segments

    def autotag_segments_bulk(self):
        """Bulk Operations: (re)place inline tags on already-translated segments
        with the AI. Operates on the selected segments, or all segments if none
        are selected. Only touches segments that have source tags AND a target;
        wording is never changed. One AI call per segment; one Undo reverts all."""
        if not self.current_project:
            QMessageBox.warning(self, "No Project", "Open a project first.")
            return

        segs = self._get_selected_segments_with_rows()
        scope_desc = "selected"
        if not segs:
            segs = [(i, s) for i, s in enumerate(self.current_project.segments)]
            scope_desc = "all"

        targets = [(i, s) for (i, s) in segs
                   if autotag_extract_tags(s.source) and (s.target or "").strip()]
        if not targets:
            QMessageBox.information(
                self, "AutoTagger",
                f"None of the {scope_desc} segments have both inline tags in the "
                "source and a translation to tag.")
            return

        reply = QMessageBox.question(
            self, "AutoTagger",
            f"Run AutoTagger on {len(targets)} segment(s) ({scope_desc} scope)?\n\n"
            "Each segment's source tags are re-placed into its translation by the "
            "AI (one AI call per segment). Wording is never changed; only tags move.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Resolve LLM provider/model/keys
        settings = self.load_llm_settings()
        provider = settings.get('provider', 'openai')
        model = self._resolve_provider_model(settings, provider, 'gpt-6.1-sol')
        if provider == 'ollama':
            api_keys = {'ollama': 'not-needed'}
        else:
            api_keys = self.load_api_keys() or {}
            has_key = (provider in api_keys
                       or (provider == 'gemini' and 'google' in api_keys)
                       or provider == 'custom_openai')
            if not has_key:
                r = QMessageBox.question(
                    self, f"{provider.title()} API Key Missing",
                    f"{self._api_key_problem(provider) or f'No API key set for {provider.title()}.'} Configure it now?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if r == QMessageBox.StandardButton.Yes:
                    self._go_to_settings_tab("AI Settings")
                return

        custom_base_url = None
        custom_api_key = None
        if provider == 'custom_openai':
            profile = self._get_active_custom_profile(settings)
            if profile:
                custom_base_url = profile.get('endpoint') or None
                pk = (profile.get('api_key') or '').strip()
                if pk:
                    custom_api_key = pk

        worker = PreTranslationWorker(
            self, targets, 'LLM', provider, model,
            prompt_manager=self.prompt_manager_qt if hasattr(self, 'prompt_manager_qt') else None,
            base_url=custom_base_url, custom_api_key=custom_api_key,
            http_proxy=self._get_proxy_url() if provider != 'gemini' else None,
            autotag_only=True,
        )
        dialog = LiveProgressDialog(self, len(targets), f"AutoTagger ({provider} | {model})")
        sc = {'s': 0, 'e': 0}

        def on_progress(current, total, message, success, elapsed_time):
            if success:
                sc['s'] += 1
            else:
                sc['e'] += 1
            dialog.add_console_line(message, success)
            dialog.update_progress(current, total, elapsed_time, sc['s'], sc['e'])
            if success and current <= len(targets):
                row_index, segment = targets[current - 1]
                if row_index < self.table.rowCount():
                    display_text = segment.target
                    if self.hide_outer_wrapping_tags:
                        display_text, _ = strip_outer_wrapping_tags(display_text)
                    tw = self.table.cellWidget(row_index, 3)
                    if tw and isinstance(tw, EditableGridTextEditor):
                        tw.setPlainText(display_text)
                    else:
                        self.table.setItem(row_index, 3, QTableWidgetItem(display_text))
                    self.update_status_icon(row_index, segment.status)

        def on_complete(final_success, final_error):
            dialog.show_completion_message(final_success, final_error)
            self.project_modified = True
            self.update_window_title()
            self.auto_resize_rows()
            self.update_progress_stats()
            try:
                entries = getattr(worker, 'undo_entries', None) or []
                if entries:
                    self.record_undo_states_batch(entries)
            except Exception as e:
                self.log(f"⚠ Undo recording failed for AutoTagger batch: {e}")
            self.log(f"🏷️ AutoTagger batch complete: {final_success} tagged, {final_error} skipped/failed")

        worker.progress_update.connect(on_progress)
        worker.translation_complete.connect(on_complete)
        dialog.rejected.connect(worker.cancel)
        worker.start()
        dialog.exec()
        worker.wait()
    
    def call_modernmt(self, text: str, source_lang: str, target_lang: str, api_key: str = None) -> str:
        """Call ModernMT API"""
        try:
            import requests

            api_keys = self.load_api_keys()
            if not api_key:
                api_key = api_keys.get("modernmt")

            if not api_key:
                return f"[{self._api_key_problem('modernmt') or 'ModernMT requires API key'}]"

            # Map full language names to ISO codes
            lang_name_to_code = {
                'english': 'en', 'dutch': 'nl', 'german': 'de', 'french': 'fr',
                'spanish': 'es', 'italian': 'it', 'portuguese': 'pt', 'russian': 'ru',
                'chinese': 'zh', 'japanese': 'ja', 'korean': 'ko', 'arabic': 'ar',
                'polish': 'pl', 'swedish': 'sv', 'norwegian': 'no', 'danish': 'da',
                'finnish': 'fi', 'greek': 'el', 'turkish': 'tr', 'czech': 'cs',
                'hungarian': 'hu', 'romanian': 'ro', 'bulgarian': 'bg', 'ukrainian': 'uk',
            }

            # Convert language - try name mapping first, then code extraction
            src_lower = source_lang.lower().strip()
            tgt_lower = target_lang.lower().strip()

            src_code = lang_name_to_code.get(src_lower, src_lower.split('-')[0].split('_')[0])
            tgt_code = lang_name_to_code.get(tgt_lower, tgt_lower.split('-')[0].split('_')[0])
            
            # ModernMT API endpoint
            url = "https://api.modernmt.com/translate"
            headers = {
                'MMT-ApiKey': api_key,
                'Content-Type': 'application/json'
            }
            data = {
                'q': text,
                'source': src_code,
                'target': tgt_code
            }
            
            response = requests.post(url, headers=headers, json=data, timeout=10, proxies=self._get_proxy_dict())
            response.raise_for_status()

            result = response.json()
            return result['data']['translation']

        except ImportError:
            return "[ModernMT requires: pip install requests]"
        except Exception as e:
            return f"[ModernMT error: {str(e)}]"
    
    def call_mymemory(self, text: str, source_lang: str, target_lang: str, api_key: str = None) -> str:
        """Call MyMemory Translation API (free tier available, simple REST API)"""
        try:
            import requests

            # MyMemory is free, but API key provides higher limits
            api_keys = self.load_api_keys()
            api_key = api_key or api_keys.get("mymemory")  # Optional, works without key

            # Map full language names to ISO codes
            lang_name_to_code = {
                'english': 'en', 'dutch': 'nl', 'german': 'de', 'french': 'fr',
                'spanish': 'es', 'italian': 'it', 'portuguese': 'pt', 'russian': 'ru',
                'chinese': 'zh', 'japanese': 'ja', 'korean': 'ko', 'arabic': 'ar',
                'polish': 'pl', 'swedish': 'sv', 'norwegian': 'no', 'danish': 'da',
                'finnish': 'fi', 'greek': 'el', 'turkish': 'tr', 'czech': 'cs',
                'hungarian': 'hu', 'romanian': 'ro', 'bulgarian': 'bg', 'ukrainian': 'uk',
                'hebrew': 'he', 'thai': 'th', 'vietnamese': 'vi', 'indonesian': 'id',
                'malay': 'ms', 'hindi': 'hi', 'bengali': 'bn', 'tamil': 'ta',
            }

            # Convert language - try name mapping first, then code extraction
            src_lower = source_lang.lower().strip()
            tgt_lower = target_lang.lower().strip()

            src_code = lang_name_to_code.get(src_lower, src_lower.split('-')[0].split('_')[0])
            tgt_code = lang_name_to_code.get(tgt_lower, tgt_lower.split('-')[0].split('_')[0])
            
            # MyMemory API endpoint
            url = "https://api.mymemory.translated.net/get"
            params = {
                'q': text,
                'langpair': f"{src_code}|{tgt_code}"
            }
            if api_key:
                params['key'] = api_key
            
            response = requests.get(url, params=params, timeout=10, proxies=self._get_proxy_dict())
            response.raise_for_status()

            result = response.json()
            if result.get('responseStatus') == 200:
                return result['responseData']['translatedText']
            else:
                return f"[MyMemory error: {result.get('responseDetails', 'Unknown error')}]"
            
        except ImportError:
            return "[MyMemory requires: pip install requests]"
        except Exception as e:
            return f"[MyMemory error: {str(e)}]"

    def show_image_extractor_from_tools(self):
        """Switch the user to the Image Context pane (now under AI in v1.9.395+)."""
        if not hasattr(self, 'main_tabs'):
            return
        # Find the AI main tab by label and select it.
        for i in range(self.main_tabs.count()):
            if "AI" in self.main_tabs.tabText(i):
                self.main_tabs.setCurrentIndex(i)
                break
        # Then select the Image Context sub-tab inside the AI tab.
        if hasattr(self, 'ai_subtabs'):
            for i in range(self.ai_subtabs.count()):
                if "Image Context" in self.ai_subtabs.tabText(i):
                    self.ai_subtabs.setCurrentIndex(i)
                    break
    
    def show_scratchpad(self):
        """Show Scratchpad dialog for private translator notes.
        
        Notes are saved with the project (.svproj) and never exported to CAT tools.
        """
        if not hasattr(self, 'current_project') or self.current_project is None:
            QMessageBox.warning(
                self,
                "No Project Open",
                "Please open or create a project first before using the Scratchpad."
            )
            return
        
        # Get current notes from project
        current_notes = getattr(self.current_project, 'scratchpad_notes', '') or ''
        
        dialog = ScratchpadDialog(self, current_notes)
        if dialog.exec():
            # Save the notes back to the project
            new_notes = dialog.get_notes()
            self.current_project.scratchpad_notes = new_notes
            
            # Mark project as modified so it will be saved
            self.current_project.modified = datetime.now().isoformat()
            
            # Update status bar / log
            if new_notes:
                note_preview = new_notes[:50] + "..." if len(new_notes) > 50 else new_notes
                self.log(f"📝 Scratchpad notes updated: {note_preview.replace(chr(10), ' ')}")
            else:
                self.log("📝 Scratchpad notes cleared")
    
    def show_theme_editor(self):
        """Show Theme Editor dialog"""
        dialog = ThemeEditorDialog(self, self.theme_manager)
        if dialog.exec():
            # Theme may have been changed, reapply
            self.theme_manager.apply_theme(QApplication.instance())
            # Refresh all theme-dependent UI elements
            self.refresh_theme_colors()

    def refresh_theme_colors(self):
        """Refresh all theme-dependent UI elements when theme changes"""
        theme = self.theme_manager.current_theme
        # Clear row color cache to force refresh with new theme colors
        # Remove all theme-specific caches
        for attr in list(vars(self).keys()):
            if attr.startswith('_row_color_cache_'):
                delattr(self, attr)
        
        # Reapply alternating row colors with new theme
        if hasattr(self, 'apply_alternating_row_colors'):
            self.apply_alternating_row_colors()

        # Update navigation arrow colors based on theme
        if hasattr(self, 'theme_aware_arrows'):
            is_dark = theme.name == "Dark"
            arrow_color = "#FFFFFF" if is_dark else "#333333"
            for arrow in self.theme_aware_arrows:
                if hasattr(arrow, 'set_color'):
                    arrow.set_color(arrow_color)

        # Refresh segment numbers color
        if hasattr(self, 'table') and self.table:
            # Determine segment number color based on theme
            is_dark_theme = theme.name == "Dark"
            segment_num_color = theme.text if is_dark_theme else "black"

            for row in range(self.table.rowCount()):
                # Keep UI responsive during large grid updates
                if row % 50 == 0:
                    QApplication.processEvents()
                id_item = self.table.item(row, 0)
                if id_item:
                    # Don't change currently highlighted row (orange background)
                    if id_item.background().color().name() != "#ffa500":
                        id_item.setForeground(QColor(segment_num_color))
        
        # Refresh TranslationResultsPanel
        if hasattr(self, 'translation_results_panel') and self.translation_results_panel:
            if hasattr(self.translation_results_panel, 'apply_theme'):
                self.translation_results_panel.apply_theme()
        
        # Refresh Compare Panel colors
        if hasattr(self, 'compare_panel_text_edits') and self.compare_panel_text_edits:
            try:
                self._refresh_compare_panel_theme()
            except Exception as e:
                print(f"Error refreshing Compare Panel theme: {e}")
                import traceback
                traceback.print_exc()
        
        # Refresh TermLensWidget
        if hasattr(self, 'termlens_widget') and self.termlens_widget:
            if hasattr(self.termlens_widget, 'apply_theme'):
                self.termlens_widget.apply_theme()

        # Also refresh Match Panel TermLens (right panel)
        if hasattr(self, 'termlens_widget_match') and self.termlens_widget_match:
            if hasattr(self.termlens_widget_match, 'apply_theme'):
                self.termlens_widget_match.apply_theme()

        # Refresh sidebar themes (Settings + Tools + Resources)
        self._update_settings_sidebar_theme()
        self._update_tools_sidebar_theme()
        self._update_resources_sidebar_theme()

        # Inline light stylesheets: adapt for a dark theme, restore for a light one (#78)
        if getattr(self, '_dark_style_adapter', None) is not None:
            self._dark_style_adapter.refresh_all()

    def show_file_progress_dialog(self):
        """Show file progress - redirects to Project Info dialog, File Progress tab."""
        self.show_project_info_dialog(initial_tab=1)
    
    def _add_overall_progress_section(self, layout):
        """Add overall progress section to a layout (for File Progress dialog)."""
        from PyQt6.QtWidgets import QGroupBox, QGridLayout, QLabel, QProgressBar
        
        progress_group = QGroupBox(self.tr("Overall Progress"))
        grid = QGridLayout(progress_group)
        
        if not self.current_project or not self.current_project.segments:
            grid.addWidget(QLabel(self.tr("No segments in project")), 0, 0)
            layout.addWidget(progress_group)
            return
        
        segments = self.current_project.segments
        total_segs = len(segments)
        
        # Calculate stats
        total_words = sum(len(s.source.split()) for s in segments if s.source)
        translated_segs = sum(1 for s in segments if s.target and s.target.strip())
        translated_words = sum(len(s.source.split()) for s in segments if s.target and s.target.strip())
        
        confirmed_statuses = {'confirmed', 'proofread', 'approved'}
        confirmed_segs = sum(1 for s in segments if s.status in confirmed_statuses)
        
        trans_percent = (translated_segs / total_segs * 100) if total_segs > 0 else 0
        conf_percent = (confirmed_segs / total_segs * 100) if total_segs > 0 else 0
        word_percent = (translated_words / total_words * 100) if total_words > 0 else 0
        
        # Row 1: Segments
        grid.addWidget(QLabel(self.tr("<b>Segments:</b>")), 0, 0)
        grid.addWidget(QLabel(f"{translated_segs} / {total_segs} translated ({trans_percent:.1f}%)"), 0, 1)
        
        seg_progress = QProgressBar()
        seg_progress.setValue(int(trans_percent))
        grid.addWidget(seg_progress, 0, 2)
        
        # Row 2: Words
        grid.addWidget(QLabel(self.tr("<b>Words:</b>")), 1, 0)
        grid.addWidget(QLabel(f"{translated_words} / {total_words} ({word_percent:.1f}%)"), 1, 1)
        
        word_progress = QProgressBar()
        word_progress.setValue(int(word_percent))
        grid.addWidget(word_progress, 1, 2)
        
        # Row 3: Confirmed
        grid.addWidget(QLabel(self.tr("<b>Confirmed:</b>")), 2, 0)
        grid.addWidget(QLabel(f"{confirmed_segs} / {total_segs} ({conf_percent:.1f}%)"), 2, 1)
        
        conf_progress = QProgressBar()
        conf_progress.setValue(int(conf_percent))
        conf_progress.setStyleSheet("QProgressBar::chunk { background-color: #4CAF50; }")
        grid.addWidget(conf_progress, 2, 2)
        
        layout.addWidget(progress_group)

    def get_themed_button_style(self, button_type: str, extra_styles: str = "") -> str:
        """
        Get a theme-aware button style.

        Args:
            button_type: Type of button - 'success', 'info', 'warning', 'danger', 'neutral', 'purple'
            extra_styles: Additional CSS styles to append (e.g., "padding: 10px;")

        Returns:
            CSS stylesheet string for the button
        """
        theme = self.theme_manager.current_theme

        # Map button type to theme color
        color_map = {
            'success': theme.button_success,
            'info': theme.button_info,
            'warning': theme.button_warning,
            'danger': theme.button_danger,
            'neutral': theme.button_neutral,
            'purple': theme.button_purple,
        }

        bg_color = color_map.get(button_type, theme.button)
        return f"background-color: {bg_color}; color: white; font-weight: bold; {extra_styles}"

    def get_themed_panel_style(self, panel_type: str, extra_styles: str = "") -> str:
        """
        Get a theme-aware panel/info box style.

        Args:
            panel_type: Type of panel - 'info', 'warning', 'neutral', 'preview', 'accent'
            extra_styles: Additional CSS styles to append

        Returns:
            CSS stylesheet string for the panel
        """
        theme = self.theme_manager.current_theme

        # Map panel type to theme color
        color_map = {
            'info': theme.panel_info,
            'warning': theme.panel_warning,
            'neutral': theme.panel_neutral,
            'preview': theme.panel_preview,
            'accent': theme.panel_accent,
        }

        bg_color = color_map.get(panel_type, theme.window_bg)
        # Use appropriate text color based on panel type
        text_color = theme.text if panel_type != 'warning' else theme.text

        return f"background-color: {bg_color}; color: {text_color}; {extra_styles}"

    def _REMOVED_add_mt_and_llm_matches_progressive(self, segment, source_lang, target_lang, source_lang_code, target_lang_code):
        """REMOVED - kept for reference only, will be deleted in future cleanup."""
        return  # Dead code below - keeping temporarily for reference

        from modules.translation_results_panel import TranslationMatch

        # MT matches (usually fast ~0.5s each)
        if self.enable_mt_matching:
            api_keys = self.load_api_keys()
            enabled_providers = self.load_provider_enabled_states()
            
            # 1. Google Translate
            if enabled_providers.get('mt_google_translate', True) and api_keys.get('google_translate'):
                try:
                    from modules.llm_clients import get_google_translation
                    
                    google_result = get_google_translation(
                        segment.source, 
                        source_lang_code or 'auto',
                        target_lang_code or 'en'
                    )
                    
                    if google_result and google_result.get('translation'):
                        match = TranslationMatch(
                            source=segment.source,
                            target=google_result['translation'],
                            relevance=95,
                            metadata=google_result.get('metadata', {}),
                            match_type="MT",
                            compare_source=segment.source,
                            provider_code='GT'
                        )
                        # Show MT match immediately (deduplicated by panel)
                        mt_dict = {"MT": [match]}
                        if hasattr(self, 'results_panels') and self.results_panels:
                            for panel in self.results_panels:
                                try:
                                    panel.add_matches(mt_dict)
                                except Exception as e:
                                    self.log(f"Error adding Google Translate match: {e}")
                        
                        # 🔄 Update Compare Panel with MT result
                        if hasattr(self, 'compare_panel_mt'):
                            # Add to MT matches list for navigation
                            if not hasattr(self, '_compare_panel_mt_matches'):
                                self._compare_panel_mt_matches = []
                            self._compare_panel_mt_matches.append({
                                'translation': google_result['translation'],
                                'provider': 'Google Translate'
                            })
                            self.set_compare_panel_matches(
                                segment.id,
                                segment.source,
                                tm_matches=getattr(self, '_compare_panel_tm_matches', []),
                                mt_matches=self._compare_panel_mt_matches
                            )
                except Exception as e:
                    self.log(f"⚠ Error getting Google Translate: {e}")
            
            # 2. DeepL
            if enabled_providers.get('mt_deepl', True) and api_keys.get('deepl'):
                try:
                    translation = self.call_deepl(segment.source, source_lang, target_lang, api_keys.get('deepl'))
                    if translation and not translation.startswith('['):  # Skip error messages
                        match = TranslationMatch(
                            source=segment.source,
                            target=translation,
                            relevance=94,
                            metadata={'provider': 'DeepL'},
                            match_type="MT",
                            compare_source=segment.source,
                            provider_code='DL'
                        )
                        mt_dict = {"MT": [match]}
                        if hasattr(self, 'results_panels') and self.results_panels:
                            for panel in self.results_panels:
                                try:
                                    panel.add_matches(mt_dict)
                                except Exception as e:
                                    self.log(f"Error adding DeepL match: {e}")
                        
                        # Add to Compare Panel MT matches
                        if hasattr(self, 'compare_panel_mt'):
                            if not hasattr(self, '_compare_panel_mt_matches'):
                                self._compare_panel_mt_matches = []
                            self._compare_panel_mt_matches.append({
                                'translation': translation,
                                'provider': 'DeepL'
                            })
                            self.set_compare_panel_matches(
                                segment.id,
                                segment.source,
                                tm_matches=getattr(self, '_compare_panel_tm_matches', []),
                                mt_matches=self._compare_panel_mt_matches
                            )
                except Exception as e:
                    self.log(f"⚠ Error getting DeepL: {e}")
            
            # 3. Amazon Translate
            if enabled_providers.get('mt_amazon', True) and api_keys.get('amazon_translate'):
                try:
                    region = api_keys.get('amazon_translate_region', 'us-east-1')
                    translation = self.call_amazon_translate(segment.source, source_lang, target_lang, api_keys.get('amazon_translate'), region)
                    if translation and not translation.startswith('['):  # Skip error messages
                        match = TranslationMatch(
                            source=segment.source,
                            target=translation,
                            relevance=93,
                            metadata={'provider': 'Amazon Translate'},
                            match_type="MT",
                            compare_source=segment.source,
                            provider_code='AT'
                        )
                        mt_dict = {"MT": [match]}
                        if hasattr(self, 'results_panels') and self.results_panels:
                            for panel in self.results_panels:
                                try:
                                    panel.add_matches(mt_dict)
                                except Exception as e:
                                    self.log(f"Error adding Amazon Translate match: {e}")
                        
                        # Add to Compare Panel MT matches
                        if hasattr(self, 'compare_panel_mt'):
                            if not hasattr(self, '_compare_panel_mt_matches'):
                                self._compare_panel_mt_matches = []
                            self._compare_panel_mt_matches.append({
                                'translation': translation,
                                'provider': 'Amazon Translate'
                            })
                            self.set_compare_panel_matches(
                                segment.id,
                                segment.source,
                                tm_matches=getattr(self, '_compare_panel_tm_matches', []),
                                mt_matches=self._compare_panel_mt_matches
                            )
                except Exception as e:
                    self.log(f"⚠ Error getting Amazon Translate: {e}")
            
            # 4. MyMemory (free, works without API key or with email as key)
            if enabled_providers.get('mt_mymemory', True):
                try:
                    mymemory_key = api_keys.get('mymemory', '')
                    # MyMemory works without a key, key is optional (email for more requests)
                    translation = self.call_mymemory(segment.source, source_lang, target_lang, mymemory_key if mymemory_key and mymemory_key != 'xxx' else None)
                    if translation and not translation.startswith('['):  # Skip error messages
                        match = TranslationMatch(
                            source=segment.source,
                            target=translation,
                            relevance=85,
                            metadata={'provider': 'MyMemory (Free)'},
                            match_type="MT",
                            compare_source=segment.source,
                            provider_code='MM'
                        )
                        mt_dict = {"MT": [match]}
                        if hasattr(self, 'results_panels') and self.results_panels:
                            for panel in self.results_panels:
                                try:
                                    panel.add_matches(mt_dict)
                                except Exception as e:
                                    self.log(f"Error adding MyMemory match: {e}")
                        
                        # Add to Compare Panel MT matches
                        if hasattr(self, 'compare_panel_mt'):
                            if not hasattr(self, '_compare_panel_mt_matches'):
                                self._compare_panel_mt_matches = []
                            self._compare_panel_mt_matches.append({
                                'translation': translation,
                                'provider': 'MyMemory'
                            })
                            self.set_compare_panel_matches(
                                segment.id,
                                segment.source,
                                tm_matches=getattr(self, '_compare_panel_tm_matches', []),
                                mt_matches=self._compare_panel_mt_matches
                            )
                except Exception as e:
                    self.log(f"⚠ Error getting MyMemory: {e}")
        
        # LLM matches (slower ~1-3s each)
        if self.enable_llm_matching:
            try:
                api_keys = self.load_api_keys()
                if not api_keys:
                    return
                
                settings = self.load_llm_settings()
                
                # OpenAI
                if 'openai' in api_keys:
                    try:
                        self.log("🧠 PROGRESSIVE LLM: Attempting OpenAI translation...")
                        from modules.llm_clients import LLMClient
                        
                        openai_model = settings.get('openai_model', 'gpt-6.1-sol')
                        client = LLMClient(
                            api_key=api_keys['openai'],
                            provider='openai',
                            model=openai_model,
                            http_proxy=self._get_proxy_url()
                        )

                        translation = client.translate(
                            text=segment.source,
                            source_lang=source_lang_code or 'nl',
                            target_lang=target_lang_code or 'en'
                        )

                        if translation and translation.strip():
                            match = TranslationMatch(
                                source=segment.source,
                                target=translation.strip(),
                                relevance=88,
                                metadata={'model': openai_model, 'provider': 'OpenAI'},
                                match_type="LLM",
                                compare_source=segment.source,
                                provider_code='OA'
                            )
                            # Show OpenAI match immediately
                            llm_dict = {"LLM": [match]}
                            self.log(f"🧠 PROGRESSIVE LLM: Showing OpenAI match")
                            if hasattr(self, 'results_panels') and self.results_panels:
                                for panel in self.results_panels:
                                    try:
                                        panel.add_matches(llm_dict)
                                    except Exception as e:
                                        self.log(f"Error adding OpenAI match: {e}")
                    except Exception as e:
                        self.log(f"⚠ Error getting OpenAI translation: {e}")
                
                # Claude
                if 'anthropic' in api_keys:
                    try:
                        self.log("🧠 PROGRESSIVE LLM: Attempting Claude translation...")
                        from modules.llm_clients import LLMClient
                        
                        claude_model = settings.get('anthropic_model', 'claude-3-5-sonnet-20241022')
                        client = LLMClient(
                            api_key=api_keys['anthropic'],
                            provider='anthropic',
                            model=claude_model,
                            http_proxy=self._get_proxy_url()
                        )
                        
                        translation = client.translate(
                            text=segment.source,
                            source_lang=source_lang_code or 'nl',
                            target_lang=target_lang_code or 'en'
                        )
                        
                        if translation and translation.strip():
                            match = TranslationMatch(
                                source=segment.source,
                                target=translation.strip(),
                                relevance=88,
                                metadata={'model': claude_model, 'provider': 'Claude'},
                                match_type="LLM",
                                compare_source=segment.source,
                                provider_code='CL'
                            )
                            # Show Claude match immediately
                            llm_dict = {"LLM": [match]}
                            self.log(f"🧠 PROGRESSIVE LLM: Showing Claude match")
                            if hasattr(self, 'results_panels') and self.results_panels:
                                for panel in self.results_panels:
                                    try:
                                        panel.add_matches(llm_dict)
                                    except Exception as e:
                                        self.log(f"Error adding Claude match: {e}")
                    except Exception as e:
                        self.log(f"⚠ Error getting Claude translation: {e}")
                        
            except Exception as e:
                self.log(f"⚠ Error in LLM matching: {e}")


# ============================================================================
# SUPERLOOKUP TAB
# ============================================================================


















# ============================================================================
# AUTOFINGERS DIALOG
# ============================================================================



from modules.colour_checkboxes import (  # moved out of this file, unchanged
    PinkCheckmarkCheckBox,
    BlueCheckmarkCheckBox,
    OrangeCheckmarkCheckBox,
    CustomRadioButton,
)








# ============================================================================
# APPLICATION ENTRY POINT
# ============================================================================

def _setup_diagnostic_log():
    """Tee stdout + stderr into a rolling diagnostic log file.

    Runs at the very start of main() so all startup output is captured.
    Critical for silent launches (pythonw / Windows GUI subsystem) where
    stdout/stderr would otherwise go to a null device – users have no
    console to copy from, but they can point us at a file.

    Path: <user_data_path>/workbench/logs/supervertaler.log
    Rotation: once the log exceeds ~2 MB it is renamed to
    supervertaler-previous.log (overwriting any older copy). Keeps at most
    2 files, ~4 MB total, so the disk footprint is negligible.

    The tee preserves the original stdout/stderr, so developers running
    from `run.cmd` or `supervertaler-debug` still see everything in the
    console AND get a copy in the file.

    Returns the Path of the active log file, or None on failure.
    """
    import io
    from datetime import datetime

    try:
        log_dir = get_user_data_path() / "workbench" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "supervertaler.log"
        prev_path = log_dir / "supervertaler-previous.log"

        # Rotate if the existing log is too large (~2 MB).
        try:
            if log_path.exists() and log_path.stat().st_size > 2 * 1024 * 1024:
                if prev_path.exists():
                    prev_path.unlink()
                log_path.rename(prev_path)
        except Exception:
            pass  # Non-fatal – keep going with append mode

        # Open in line-buffered append mode so a hard crash still leaves a
        # usable log on disk (nothing stuck in a Python-side buffer).
        log_file = open(log_path, 'a', encoding='utf-8', errors='replace', buffering=1)
        log_file.write(
            f"\n{'=' * 70}\n"
            f"Supervertaler session started {datetime.now().isoformat(timespec='seconds')}\n"
            f"Python: {sys.version.split()[0]} on {sys.platform}\n"
            f"{'=' * 70}\n"
        )
        log_file.flush()

        class _Tee:
            """Duplicate writes to the original stream + the log file.

            `original` may be None when running under pythonw (the Windows
            GUI-subsystem Python has no stdout/stderr attached at all);
            we guard every forward so we never hit AttributeError in that
            case.
            """

            def __init__(self, original, log_handle):
                self._orig = original
                self._log = log_handle

            def write(self, text):
                if self._orig is not None:
                    try:
                        self._orig.write(text)
                    except Exception:
                        pass
                try:
                    self._log.write(text)
                except Exception:
                    pass

            def flush(self):
                if self._orig is not None:
                    try:
                        self._orig.flush()
                    except Exception:
                        pass
                try:
                    self._log.flush()
                except Exception:
                    pass

            def isatty(self):
                if self._orig is None:
                    return False
                try:
                    return self._orig.isatty()
                except Exception:
                    return False

            def fileno(self):
                # Some libraries expect a real fd; expose the log file's fd
                # as a best-effort fallback when the original is detached.
                if self._orig is not None and hasattr(self._orig, 'fileno'):
                    try:
                        return self._orig.fileno()
                    except (OSError, io.UnsupportedOperation):
                        pass
                return self._log.fileno()

        sys.stdout = _Tee(sys.stdout, log_file)
        sys.stderr = _Tee(sys.stderr, log_file)
        # Stash the path so UI actions ("Open Diagnostic Log") can find it.
        sys._supervertaler_log_path = log_path
        return log_path
    except Exception as e:
        # Never prevent the app from launching because logging failed.
        try:
            sys.__stderr__.write(f"[Diagnostic log setup failed: {e}]\n")
        except Exception:
            pass
        return None


def _install_log_hooks():
    """Route extra output channels into the (already teed) stdout/stderr.

    Without this, four sources of debug info disappear under pythonw /
    the built .exe (no console attached):

      * threading.excepthook  – uncaught exceptions in worker threads
      * Qt message handler    – qWarning / qCritical, layout warnings, GL errors
      * stdlib logging        – modules/* use ``logging.getLogger(__name__)``
      * Python warnings       – DeprecationWarning, ResourceWarning, ...

    Everything they emit is written via ``print`` / ``sys.stderr``, which
    _setup_diagnostic_log has already teed to supervertaler.log. So after
    this runs, the in-app diagnostic log is a true superset of what the
    dev terminal would have shown.
    """
    import logging
    import threading
    import traceback

    # 1. Thread exception hook (Python 3.8+). Without it, a crashing
    #    worker thread vanishes silently – only the main thread is
    #    covered by sys.excepthook.
    def _thread_excepthook(args):
        if args.exc_type is SystemExit:
            return
        thread_name = args.thread.name if args.thread else "<unknown>"
        try:
            sys.stderr.write(f"\n[THREAD-EXCEPTION] in thread {thread_name!r}:\n")
            traceback.print_exception(
                args.exc_type, args.exc_value, args.exc_traceback,
                file=sys.stderr,
            )
        except Exception:
            pass
    try:
        threading.excepthook = _thread_excepthook
    except Exception:
        pass

    # 2. Qt message handler – surface qWarning / qCritical / layout
    #    warnings that Qt would otherwise print to a console we don't have.
    try:
        from PyQt6.QtCore import qInstallMessageHandler, QtMsgType
        _qt_severity = {
            QtMsgType.QtDebugMsg:    "QT-DEBUG",
            QtMsgType.QtInfoMsg:     "QT-INFO",
            QtMsgType.QtWarningMsg:  "QT-WARN",
            QtMsgType.QtCriticalMsg: "QT-CRIT",
            QtMsgType.QtFatalMsg:    "QT-FATAL",
        }
        def _qt_message_handler(mode, context, message):
            tag = _qt_severity.get(mode, "QT-???")
            try:
                print(f"[{tag}] {message}")
            except Exception:
                pass
        qInstallMessageHandler(_qt_message_handler)
    except Exception:
        pass

    # 3. Stdlib logging → stdout (teed). Modules under modules/* use
    #    logging.getLogger(__name__); without a configured handler those
    #    messages are dropped silently. Bump root to INFO only if the
    #    user/library hasn't already configured something stricter.
    try:
        root = logging.getLogger()
        already_attached = any(
            isinstance(h, logging.StreamHandler)
            and getattr(h, "stream", None) is sys.stdout
            for h in root.handlers
        )
        if not already_attached:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter(
                "[%(levelname)s] %(name)s: %(message)s"
            ))
            root.addHandler(handler)
        if root.level == logging.WARNING:
            root.setLevel(logging.INFO)
        # Route warnings.warn(...) through the logging system too.
        logging.captureWarnings(True)
    except Exception:
        pass


def main():
    """Application entry point"""
    # Headless batch-offload mode for the Supervertaler-for-Trados large-file
    # offload (#42 / Workbench#230). Pure Python – no Qt/GUI, no window – so it
    # must run before the diagnostic-log redirect or any QApplication setup.
    if "--batch" in sys.argv or "--translate-sdlxliff" in sys.argv:
        from modules.batch_offload import cli_main
        raise SystemExit(cli_main(sys.argv[1:]))

    # Redirect stdout/stderr to a rolling log file FIRST – so even crashes
    # during QApplication import or QWebEngine init get captured.
    _setup_diagnostic_log()
    # Then route Qt / threads / stdlib-logging / warnings into the same tee.
    _install_log_hooks()

    # Install global exception handler to catch unhandled exceptions
    import traceback

    def global_exception_handler(exc_type, exc_value, exc_traceback):
        """Global exception handler - prints full traceback before crashing"""
        print("\n" + "="*60)
        print("🚨 UNHANDLED EXCEPTION - FULL TRACEBACK:")
        print("="*60)
        traceback.print_exception(exc_type, exc_value, exc_traceback)
        print("="*60 + "\n")
        # Call the default handler
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    sys.excepthook = global_exception_handler
    
    # Suppress Chromium/QtWebEngine verbose error output (cache errors, GPU warnings, JS console)
    # These are harmless but alarming to users - websites emit tons of CSP/permissions noise
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-logging --log-level=3 --enable-logging=stderr --v=-1"
    
    # Suppress Qt WebEngine's "js:" console output (CSP violations, permissions policy warnings)
    # These come from embedded web pages and are not relevant to Supervertaler
    os.environ["QT_LOGGING_RULES"] = "*.debug=false;qt.webenginecontext.debug=false;js=false"
    
    # Filter stderr to suppress noisy Chromium JS console messages
    # These "js:" prefixed messages come from embedded web pages and alarm users
    class StderrFilter:
        """Filter that suppresses Chromium's JS console output from stderr"""
        def __init__(self, original_stderr):
            self.original = original_stderr
            # Patterns to suppress (from embedded web pages)
            self.suppress_prefixes = (
                'js:', 'js: Error with Permissions-Policy', 'js: Refused to load',
                'js: Document-Policy', 'js: Listener added', 'js: No ID or name',
                'js: [Report Only]', 'js: Unrecognized feature'
            )
        
        def write(self, text):
            # Suppress lines starting with JS console prefixes
            if text.strip() and any(text.strip().startswith(prefix) for prefix in self.suppress_prefixes):
                return  # Silently discard
            self.original.write(text)
        
        def flush(self):
            self.original.flush()
        
        def fileno(self):
            return self.original.fileno()
        
        def isatty(self):
            return self.original.isatty() if hasattr(self.original, 'isatty') else False
    
    sys.stderr = StderrFilter(sys.stderr)
    
    # Linux-specific: Avoid memory access violations from native libraries
    # ChromaDB and Hunspell can crash on some Linux configurations
    if sys.platform == 'linux':
        # Disable threading optimizations that can cause crashes in native code
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        # Use safer malloc implementation if available
        os.environ.setdefault("MALLOC_CHECK_", "0")
    
    # Set OpenGL context sharing before creating QApplication (required for QtWebEngine)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    
    app = QApplication(sys.argv)
    app.setApplicationName("Supervertaler")
    app.setOrganizationName("Supervertaler")

    # ─── v1.10.207: install UI translator BEFORE constructing any widgets ───
    # Reads the locale from settings.json (general.ui_locale, default "en").
    # English is the source language so we skip installation entirely when
    # the user is on English – Qt's tr() then short-circuits to the source.
    # We use get_user_data_path() (module-level) instead of going through
    # SupervertalerQt because the window doesn't exist yet; the settings
    # file layout is owned by SupervertalerQt._load_unified_settings.
    try:
        import json as _json_for_locale
        _settings_path = get_user_data_path() / "workbench" / "settings" / "settings.json"
        _ui_locale_setting = "system"
        if _settings_path.exists():
            try:
                with open(_settings_path, "r", encoding="utf-8") as _sf:
                    _all_settings = _json_for_locale.load(_sf)
                _ui_locale_setting = _all_settings.get("general", {}).get("ui_locale", "system")
            except Exception:
                _ui_locale_setting = "system"

        from modules.i18n import (
            install_translator_for_locale,
            resolve_locale,
        )
        _active_locale = resolve_locale(_ui_locale_setting)
        if _active_locale and _active_locale.lower() != "en":
            _installed = install_translator_for_locale(app, _active_locale)
            if _installed is not None:
                print(
                    f"[i18n] Loaded {_installed.entry_count()} translation entries "
                    f"for locale '{_active_locale}'"
                )
            else:
                print(
                    f"[i18n] No translation file available for locale "
                    f"'{_active_locale}' – falling back to English"
                )
    except Exception as _i18n_err:
        # Never let a translator-loading hiccup prevent the app from starting.
        print(f"[i18n] Translator init failed (continuing in English): {_i18n_err}")
    
    # Global stylesheet to remove ugly focus rectangles (dotted outlines)
    # from buttons, tool buttons, tabs, and tree/list views.
    # Note: QToolTip styling is handled by theme_manager.apply_theme()
    app.setStyleSheet("""
        QPushButton:focus {
            outline: none;
            border: none;
        }
        QToolButton:focus {
            outline: none;
            border: none;
        }
        QTabBar::tab {
            outline: 0;
        }
        QTabBar::tab:focus {
            outline: none;
        }
        /* Left-align every QTabWidget's tab bar.
           Belt-and-braces with the QProxyStyle SH_TabBar_Alignment
           override below — the proxy-style path is the load-bearing
           one because theme_manager.apply_theme() resets the app
           stylesheet (wiping any per-stylesheet rule), and per-widget
           setStyleSheet calls shadow the app stylesheet for that
           widget. Style hints are CSS-independent and survive both. */
        QTabWidget::tab-bar {
            alignment: left;
        }
        QTreeView::item:focus, QListView::item:focus, QTableView::item:focus {
            outline: none;
        }
        QAbstractItemView {
            outline: 0;
        }
    """)

    # macOS: use Fusion style instead of native QMacStyle.
    # QMacStyle ignores QTabBar.setExpanding(False) (tabs always centered/expanded)
    # and renders QComboBox popup items blank. Fusion respects all Qt properties
    # and the app's custom stylesheets still apply on top.
    if sys.platform == 'darwin':
        app.setStyle("Fusion")

    # Suppress dotted focus rectangles app-wide. Native styles (notably the
    # Windows Vista style) draw PE_FrameFocusRect at the QStyle level, which
    # bypasses CSS – `outline: none` does nothing against it. A QProxyStyle
    # that intercepts that single primitive removes the rectangle from every
    # tab, button, list item, and tree item without touching individual
    # widgets.
    from PyQt6.QtWidgets import QProxyStyle, QStyle

    class _NoFocusRectStyle(QProxyStyle):
        def drawPrimitive(self, element, option, painter, widget=None):
            if element == QStyle.PrimitiveElement.PE_FrameFocusRect:
                return
            super().drawPrimitive(element, option, painter, widget)

        def styleHint(self, hint, option=None, widget=None, returnData=None):
            # Force left-alignment on every QTabBar in the app.
            # QMacStyle returns AlignCenter for SH_TabBar_Alignment, and
            # even on Fusion we can't rely on the app stylesheet rule
            # (`QTabWidget::tab-bar { alignment: left }`) because
            # theme_manager.apply_theme() replaces the app stylesheet
            # wholesale, and individual QTabWidgets routinely set their
            # own per-widget stylesheet which shadows the app rule for
            # that widget. Style hints are queried by the widget at paint
            # time and are not affected by stylesheets, so this override
            # is the only path that reliably reaches every QTabBar
            # regardless of theme / per-widget overrides.
            if hint == QStyle.StyleHint.SH_TabBar_Alignment:
                return int(Qt.AlignmentFlag.AlignLeft)
            return super().styleHint(hint, option, widget, returnData)

    app.setStyle(_NoFocusRectStyle(app.style()))

    # Set Windows AppUserModelID for taskbar icon grouping (Windows 7+)
    if sys.platform == 'win32':
        try:
            import ctypes
            myappid = 'com.michaelbeijer.supervertaler.1.9.27'
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception:
            pass  # Fail silently if not on Windows or ctypes unavailable

    # Suppress Windows' modal "There is no disk in drive X:" / "The system
    # cannot find the drive specified" pop-ups that appear when *anything*
    # in-process probes a removed/unmounted drive (recent files, plugin
    # discovery, file dialogs). They block the UI thread on a system-modal
    # dialog and there's nothing useful the user can do about them.
    if sys.platform == 'win32':
        try:
            import ctypes
            SEM_FAILCRITICALERRORS = 0x0001
            SEM_NOGPFAULTERRORBOX  = 0x0002
            SEM_NOOPENFILEERRORBOX = 0x8000
            ctypes.windll.kernel32.SetErrorMode(
                SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX
            )
        except Exception:
            pass

    window = SupervertalerQt()

    # Decide visibility BEFORE creating the tray icon, so the main window
    # is fully realised on screen first – some Windows configs render a
    # cold-started QMainWindow incorrectly when a tray icon is installed
    # ahead of the first show().
    ui_prefs = window._load_settings_section("ui")
    start_minimized = bool(ui_prefs.get("start_minimized_to_tray"))
    if not start_minimized:
        window.show()

    # System-tray icon + close-to-tray / start-minimized handling.
    # _setup_tray_icon also flips QApplication.setQuitOnLastWindowClosed(False)
    # if a tray is available, so hiding the main window won't terminate the app.
    window._setup_tray_icon()

    sys.exit(app.exec())


if __name__ == '__main__':
    # Wrap main() in crash handler for macOS Finder launches (stdout/stderr go nowhere)
    if getattr(sys, 'frozen', False) and sys.platform == 'darwin':
        try:
            main()
        except Exception:
            import traceback
            _crash_log = os.path.expanduser('~/Desktop/supervertaler_crash.log')
            try:
                with open(_crash_log, 'a') as _f:
                    _f.write('\n--- CRASH ---\n')
                    traceback.print_exc(file=_f)
            except Exception:
                pass
            raise
    else:
        main()
