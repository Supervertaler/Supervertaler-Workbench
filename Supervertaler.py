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


from modules.main_window.main_tabs import MainTabsMixin  # moved out of this class, unchanged


from modules.main_window.voice import VoiceMixin  # moved out of this class, unchanged


from modules.main_window.window_focus import WindowFocusMixin  # moved out of this class, unchanged


from modules.main_window.spellcheck import SpellcheckMixin  # moved out of this class, unchanged


from modules.main_window.segment_tools import SegmentToolsMixin  # moved out of this class, unchanged


from modules.main_window.match_prefetch import MatchPrefetchMixin  # moved out of this class, unchanged


from modules.main_window.reference_images import ReferenceImagesMixin  # moved out of this class, unchanged


from modules.main_window.statistics import StatisticsMixin  # moved out of this class, unchanged


from modules.main_window.first_run import FirstRunMixin  # moved out of this class, unchanged


from modules.main_window.mt_engines import MTEnginesMixin  # moved out of this class, unchanged


from modules.main_window.window_shell import WindowShellMixin  # moved out of this class, unchanged


from modules.main_window.tray import TrayMixin  # moved out of this class, unchanged


class SupervertalerQt(TrayMixin, WindowShellMixin, MTEnginesMixin, FirstRunMixin, StatisticsMixin, ReferenceImagesMixin, MatchPrefetchMixin, SegmentToolsMixin, SpellcheckMixin, WindowFocusMixin, VoiceMixin, MainTabsMixin, PreviewMixin, MatchPanelMixin, CommentsMixin, FiltersMixin, FindReplaceMixin, GridNavigationMixin, GridViewMixin, SegmentEditingMixin, BilingualTextMixin, ImportExportCommonMixin, PoMixin, DejaVuMixin, CafeTranMixin, PhraseMixin, TradosMixin, MemoQMixin, MultifileMixin, ReviewTableMixin, PlainTextMixin, DocumentImportMixin, OkapiMixin, DocxExportMixin, SettingsMixin, ProjectsMixin, AITranslationMixin, TranslationMemoriesMixin, TermbasesMixin, QMainWindow):
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
        # Goes up each time the index is replaced. The look-ahead worker reads
        # it before it searches and drops its result if it has moved on: the
        # result was worked out from terms that are no longer the terms.
        self.termbase_index_generation = 0
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
    
    
    def open_superbrowser_window(self):
        """Open SuperBrowser (the multi-chat AI browser) in its own window.

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
        window.setWindowTitle(self.tr("🌐 SuperBrowser – Supervertaler"))
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
    
    # ═══════════════════════════════════════════════════════════════════════════
    # Image Extractor Helper Methods
    # ═══════════════════════════════════════════════════════════════════════════
    
    # ────────────────────────────────────────────────────────────────
    # v1.10.192: vision-AI fallback for the Image Extractor
    # ────────────────────────────────────────────────────────────────

    # === IMAGE CONTEXT METHODS ===

    # ------------------------------------------------------------------
    # Phase 1 (issue #199): lazy constructors for the new Workbench
    # top tabs. Sidekick continues to embed its own copies of these
    # widgets during Phases 1-3; duplicate construction is accepted
    # until Phase 4 retires Sidekick.
    # ------------------------------------------------------------------
    
    # create_resources_tab() was removed in v1.9.422.
    # TMs and Termbases used to share a wrapper "Resources" tab on the
    # main_tabs strip with their own inner QTabWidget (the resources_tabs
    # widget). Both have been promoted to peers of Editor / AI / Tools /
    # Settings on main_tabs directly – construction now calls
    # create_translation_memories_tab() and create_termbases_tab()
    # directly. The wrapper widget and its method are gone.
    
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
                                   target_synonyms: list = None,
                                   in_termbase_direction: bool = False):
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
          * ``'failed'``     – the user chose to merge, but no synonym could be
                               saved (the database was locked, say); caller
                               must add nothing and say so. Before Nov 2026
                               this still said ``'merged'``, so the term
                               silently went nowhere.

        ``in_termbase_direction``: the terms are already in the termbase's own
        direction (the Termbases tab's Add Term dialog is labelled with the
        termbase's languages), so they must not be turned round to match the
        project.

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
            if in_termbase_direction:
                src_term, tgt_term = source_text, target_text
            else:
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
        failed_ids = set()
        for m in all_matches:
            try:
                if m['match_type'] == 'source':
                    ok = self.termbase_mgr.add_synonym(
                        m['term_id'], m['oriented_tgt'], language='target')
                else:
                    ok = self.termbase_mgr.add_synonym(
                        m['term_id'], m['oriented_src'], language='source')
                if not ok:
                    failed_ids.add(m['termbase_id'])
                    self.log(
                        f"  ✗ Could not save the synonym in '{m['termbase_name']}' "
                        f"(entry #{m['term_id']}); the database may be locked")
                    continue
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
                failed_ids.add(m['termbase_id'])
                self.log(
                    f"  ✗ Failed to merge synonym into "
                    f"'{m['termbase_name']}': {e}")

        if not merged_ids:
            return ('failed', set())
        saved_ids = set(merged_ids)
        # With several termbases, one whose synonym could not be saved still
        # counts as handled: the user chose a synonym, not a fresh entry. Its
        # failure is in the log.
        merged_ids |= failed_ids

        # "Add & Edit" – open the term entry editor on the first entry that
        # took the synonym.
        if choice == MergePromptDialog.EDIT:
            first = next(m for m in all_matches if m['termbase_id'] in saved_ids)
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

    # _create_quicktrans_stub_tab and _open_quicktrans_in_sidekick
    # were removed in v1.10.10. Pre-retirement they advertised that
    # QuickTrans settings lived inside Sidekick and provided a button
    # to navigate there; with Sidekick gone the real settings widget
    # is now mounted directly in Workbench Settings → QuickTrans by
    # _create_workbench_settings_tab (TAB 5), via the canonical
    # _create_mt_quick_lookup_settings_tab() factory.
        # Button state will be updated by _update_alwayson_ui
    
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

    # ========================================================================
    # SPELLCHECK METHODS
    # ========================================================================

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
        # Every project open/close passes through here: keep the bridge's
        # handshake naming the open project (see supervertaler_bridge_server).
        bridge = getattr(self, '_bridge_server', None)
        if bridge is not None:
            bridge.note_state(self.current_project.name if self.current_project else "", __version__)
    
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

    # ── Okapi Sidecar management ─────────────────────────────────

    # =========================================================================
    # LLM TRANSLATION INTEGRATION
    # =========================================================================

    # =========================================================================
    # FUZZY FIXER
    # =========================================================================

    # =========================================================================
    # AUTOTAGGER
    # =========================================================================

    # =========================================================================
    # GRID QUICKLAUNCHER
    # =========================================================================

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
