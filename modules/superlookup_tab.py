"""The Superlookup tab, its search worker and the hotkey processes it starts
(stopped on exit). Moved unchanged from Supervertaler.py.
"""

import atexit
import json
import os
import pyperclip
import re
import subprocess
import sys
import threading
import time
import traceback
from PyQt6.QtCore import (
    QObject,
    QRunnable,
    QThreadPool,
    QTimer,
    QUrl,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QFont,
    QKeySequence,
    QPalette,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from modules.find_replace_qt import HistoryComboBox
from modules.platform_helpers import IS_MACOS, IS_WINDOWS
from modules.shortcut_display import format_shortcut_for_display
from modules.styled_widgets import CheckmarkCheckBox, CheckmarkRadioButton
from modules.superlookup import SuperlookupEngine
from modules.ui_scale import scaled_pt
from pathlib import Path

# The program folder (where Supervertaler.py is). This file lives in modules/,
# so paths that used to be next to Supervertaler.py are built from here.
_APP_DIR = Path(os.path.abspath(__file__)).parent.parent


# Global variables to track hotkey processes for atexit cleanup
_ahk_process = None


_hotkey_manager = None

HOTKEY_SCRIPT_NAME = "supervertaler_hotkeys.ahk"


def stop_leftover_hotkey_scripts() -> int:
    """End AutoHotkey processes running our hotkey script (e.g. left by a crash).

    Only processes with the script's name on their command line: the user's own
    AutoHotkey scripts run in AutoHotkey.exe too, and must be left alone (this
    used to end every AutoHotkey.exe on the PC). Returns how many were ended.
    """
    import psutil
    ended = 0
    for proc in psutil.process_iter(['cmdline']):
        try:
            cmdline = ' '.join(proc.info.get('cmdline') or [])
            if HOTKEY_SCRIPT_NAME in cmdline.lower():
                proc.kill()
                ended += 1
        except (psutil.Error, OSError):
            pass  # gone already, or not ours to end
    return ended


def cleanup_hotkey_processes():
    """Cleanup function to stop hotkey processes on exit"""
    global _ahk_process, _hotkey_manager
    if _hotkey_manager:
        try:
            _hotkey_manager.stop()
            print("[Hotkeys] pynput hotkey manager stopped on exit")
        except Exception:
            pass
    if _ahk_process:
        try:
            _ahk_process.terminate()
            _ahk_process.wait(timeout=1)
            print("[Hotkeys] AHK process terminated on exit")
        except Exception:
            try:
                _ahk_process.kill()
                print("[Hotkeys] AHK process killed on exit")
            except Exception:
                pass


# Register cleanup function to run on Python exit
atexit.register(cleanup_hotkey_processes)


class _SearchTermHighlighter(QSyntaxHighlighter):
    """Highlights occurrences of a search term with a yellow background.

    Uses the same pattern as the main grid's TagHighlighter – operates on
    plain text in the QTextEdit, so native text selection and copy work
    perfectly without any HTML parsing overhead.
    """

    def __init__(self, document, search_term=""):
        super().__init__(document)
        self._search_term = search_term or ""
        from PyQt6.QtGui import QTextCharFormat, QColor
        self._format = QTextCharFormat()
        self._format.setBackground(QColor("#FFFF00"))  # Yellow
        self._format.setForeground(QColor("#000000"))  # Black

    def set_search_term(self, term):
        self._search_term = term or ""
        self.rehighlight()

    def highlightBlock(self, text):
        if not self._search_term:
            return
        # Case-insensitive search
        needle = self._search_term.lower()
        haystack = text.lower()
        start = 0
        while True:
            idx = haystack.find(needle, start)
            if idx < 0:
                break
            self.setFormat(idx, len(needle), self._format)
            start = idx + len(needle)


class _NumericTableWidgetItem(QTableWidgetItem):
    """QTableWidgetItem that sorts numerically rather than lexically.

    v1.10.173. Default Qt comparison falls back to text, so column values
    like "100" and "2" sort as ['100', '2'] (lexical). Used for the
    Terms count column on the Termbases tab and the Entries column on the
    TMs tab — both are integers shown as strings.
    """

    def __lt__(self, other):
        try:
            return float(self.text()) < float(other.text())
        except (ValueError, TypeError):
            return super().__lt__(other)


# Fully-transparent brush for SuperLookup result-table sort-key items. The
# visible text in those cells is drawn by a _ReadOnlyHtmlCell overlaid via
# setCellWidget; the QTableWidgetItem underneath exists only to give the
# column a sort key. On Qt 6.9+ the view paints that item text through the
# transparent overlay (issue #221), so we colour it transparent — invisible
# but still sortable. A QBrush with QColor(0,0,0,0) suppresses the paint.
_TRANSPARENT_SORT_KEY = QBrush(QColor(0, 0, 0, 0))


class _ReadOnlyHtmlCell(QTextEdit):
    """Read-only QTextEdit for Superlookup results table cells.

    Uses plain text + a QSyntaxHighlighter for search term highlighting –
    the same pattern the main Supervertaler grid uses. This gives us full
    native text selection, right-click copy, and mouse word selection,
    without the rendering issues that QLabel cell widgets have.
    """

    def __init__(self, text="", search_term="", parent=None, bold=False):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAcceptRichText(False)
        self.setPlainText(text)
        self.setFrameStyle(0)  # No border
        if bold:
            f = self.font()
            f.setBold(True)
            self.setFont(f)

        # Allow text selection but not tab-taking focus
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )

        # Make inactive selections stay visible (same as main grid)
        from PyQt6.QtGui import QPalette
        palette = self.palette()
        palette.setColor(QPalette.ColorGroup.Active,
                         QPalette.ColorRole.Highlight, QColor("#D0E7FF"))
        palette.setColor(QPalette.ColorGroup.Active,
                         QPalette.ColorRole.HighlightedText, QColor("black"))
        palette.setColor(QPalette.ColorGroup.Inactive,
                         QPalette.ColorRole.Highlight, QColor("#D0E7FF"))
        palette.setColor(QPalette.ColorGroup.Inactive,
                         QPalette.ColorRole.HighlightedText, QColor("black"))
        self.setPalette(palette)

        # Compact styling – no border, no scroll bars
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("""
            QTextEdit {
                border: none; padding: 1px 4px; background: transparent;
            }
            QTextEdit::selection {
                background-color: #D0E7FF; color: black;
            }
        """)

        # Zero document margin for compact layout
        self.document().setDocumentMargin(1)

        # Apply search term highlighting
        if search_term:
            self._highlighter = _SearchTermHighlighter(self.document(), search_term)
        else:
            self._highlighter = None


class _SuperLookupSearchSignals(QObject):
    """Signals emitted by the SuperLookup background search worker.

    A separate QObject because QRunnable is not a QObject and so cannot have
    signals of its own.
    """
    tm_ready = pyqtSignal(list)        # list of TM result dicts
    termbase_ready = pyqtSignal(list)  # list of LookupResult
    error = pyqtSignal(str)
    finished = pyqtSignal()


class _SuperLookupSearchWorker(QRunnable):
    """Run TM concordance + termbase search off the Qt main thread.

    Both searches share a single thread-local sqlite3 connection obtained via
    DatabaseManager.get_reader_connection(). The connection is released at the
    end of run() so QThreadPool worker reuse doesn't accumulate connections.

    Cancellation: callers set self.cancel_event when a newer search begins.
    The flag is checked at coarse-grained checkpoints between phases — we
    don't try to interrupt a SQLite query mid-execution. If the flag is set
    after a phase completes, we just don't emit that phase's signal so the UI
    keeps showing whatever the newer worker produces.
    """

    def __init__(self, tab, text, search_direction, from_lang, to_lang,
                 selected_tm_ids, selected_tb_ids, tm_search_disabled):
        super().__init__()
        self.setAutoDelete(False)  # we manage lifetime via tab._active_search_worker
        self.tab = tab
        self.text = text
        self.search_direction = search_direction
        self.from_lang = from_lang
        self.to_lang = to_lang
        self.selected_tm_ids = selected_tm_ids
        # v1.10.169: pre-computed on the main thread before the worker runs,
        # so the worker doesn't have to call the main-thread sqlite cursor.
        self.selected_tb_ids = selected_tb_ids
        self.tm_search_disabled = tm_search_disabled
        self.signals = _SuperLookupSearchSignals()
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    @pyqtSlot()
    def run(self):
        db_manager = self.tab.db_manager
        conn = None
        try:
            if db_manager is None:
                return
            if self.cancel_event.is_set():
                return

            try:
                conn = db_manager.get_reader_connection()
            except Exception as e:
                print(f"[SuperLookupWorker] Failed to open reader connection: {e}")
                self.signals.error.emit(str(e))
                return

            # --- TM search ---
            tm_results = []
            if not self.tm_search_disabled and self.tab.tm_database and self.tab.engine:
                try:
                    tm_results = self.tab.engine.search_tm(
                        self.text,
                        direction=self.search_direction,
                        source_lang=self.from_lang,
                        target_lang=self.to_lang,
                        connection=conn,
                    )
                except Exception as e:
                    print(f"[SuperLookupWorker] TM search error: {e}")

            if self.cancel_event.is_set():
                return
            self.signals.tm_ready.emit(tm_results)

            # --- Termbase search ---
            tb_results = []
            try:
                tb_results = self.tab.search_termbases(
                    self.text,
                    source_lang=self.from_lang,
                    target_lang=self.to_lang,
                    connection=conn,
                    selected_tb_ids=self.selected_tb_ids,
                )
            except Exception as e:
                print(f"[SuperLookupWorker] Termbase search error: {e}")

            if self.cancel_event.is_set():
                return
            self.signals.termbase_ready.emit(tb_results)
        finally:
            try:
                if conn is not None and db_manager is not None:
                    db_manager.close_reader_connection()
            except Exception:
                pass
            self.signals.finished.emit()


class SuperlookupTab(QWidget):
    """
    Superlookup - System-wide translation lookup
    Works anywhere on your computer: in CAT tools, browsers, Word, any text box
    """
    
    def __init__(self, parent=None, user_data_path=None, register_hotkeys=True):
        super().__init__(parent)
        self.main_window = parent  # Store reference to main window for database access
        self.user_data_path = user_data_path  # Store user data path for web cache
        # Only the primary hidden instance (built by _setup_superlookup_hotkeys)
        # owns the OS-level global hotkeys. The visible top-tab instance and any
        # detached-window instance pass register_hotkeys=False so they don't
        # re-attempt a registration the owner already holds — that collision
        # used to log a misleading "⚠ Failed to register … (claimed by another
        # app)" warning on every launch (issue seen in v1.10.338 logs).
        self._register_hotkeys = register_hotkeys
        # The `is_sidekick` flag was removed in v1.10.10 along with the
        # Sidekick window itself. It used to gate mounting of the
        # QuickTrans settings widget as a sub-sub-tab inside Sidekick's
        # SuperLookup pane; QuickTrans settings now live directly in
        # Workbench Settings → ⚡ QuickTrans.

        print("[Superlookup] SuperlookupTab.__init__ called")
        
        # Get theme manager from main window (try parent first, then parent's parent for dialogs)
        self.theme_manager = getattr(parent, 'theme_manager', None)
        if self.theme_manager is None and parent is not None:
            # Try parent's parent (for when parent is a dialog window)
            parent_parent = getattr(parent, 'parent', lambda: None)()
            if parent_parent:
                self.theme_manager = getattr(parent_parent, 'theme_manager', None)
        print(f"[Superlookup] theme_manager: {self.theme_manager is not None}")
        
        # Import lookup engine
        try:
            from modules.superlookup import SuperlookupEngine, LookupResult
            self.SuperlookupEngine = SuperlookupEngine
            self.LookupResult = LookupResult
            print("[Superlookup] Successfully imported SuperlookupEngine")
        except ImportError as e:
            print(f"[Superlookup] IMPORT ERROR: {e}")
            QMessageBox.critical(
                self,
                "Missing Module",
                "Could not import superlookup module.\nPlease ensure modules/superlookup.py exists."
            )
            self.SuperlookupEngine = None
            self.LookupResult = None
            # Don't return - still set up hotkey even if engine import fails
        
        # Initialize engine
        self.engine = None
        self.tm_database = None
        self.termbase_mgr = None
        self.db_manager = None
        self.hotkey_registered = False
        
        # Resource selection tracking (which resources to search)
        self.enabled_tms = []  # List of TM IDs to search
        self.enabled_termbases = []  # List of termbase IDs to search
        self.search_tm_enabled = True  # Search TMs by default
        self.search_termbase_enabled = True  # Search termbases by default
        self.search_mt_enabled = False  # MT not implemented yet
        self.search_web_enabled = False  # Web resources not implemented yet
        
        # Initialize checkbox lists (even if Settings tab is removed)
        self.tm_checkboxes = []
        self.tb_checkboxes = []
        
        # Track if languages have been populated
        self._languages_populated = False
        
        # Initialize search history
        self._init_search_history()
        
        # UI setup
        self.init_ui()

        # Register global hotkey (primary instance only — see __init__ docstring)
        if self._register_hotkeys:
            self.register_global_hotkey()
    
    def keyPressEvent(self, event):
        """Handle key presses - Escape returns to Grid"""
        if event.key() == Qt.Key.Key_Escape:
            # Navigate back to Grid (tab index 0)
            if self.main_window and hasattr(self.main_window, 'main_tabs'):
                self.main_window.main_tabs.setCurrentIndex(0)  # Grid
                # Focus the target cell if a segment is selected
                if hasattr(self.main_window, 'table') and self.main_window.table:
                    current_row = self.main_window.table.currentRow()
                    if current_row >= 0:
                        target_widget = self.main_window.table.cellWidget(current_row, 3)
                        if target_widget:
                            target_widget.setFocus()
            event.accept()
        else:
            super().keyPressEvent(event)
    
    def showEvent(self, event):
        """Called when the widget becomes visible - populate languages on first show"""
        super().showEvent(event)
        if not self._languages_populated:
            # Use a short delay to ensure main window databases are ready
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(100, self._delayed_language_population)
    
    def _delayed_language_population(self):
        """Populate language dropdowns after a short delay"""
        if self._languages_populated:
            return

        # Get database connections from main window
        if self.main_window:
            if hasattr(self.main_window, 'db_manager') and self.main_window.db_manager:
                self.db_manager = self.main_window.db_manager
            if hasattr(self.main_window, 'termbase_mgr') and self.main_window.termbase_mgr:
                self.termbase_mgr = self.main_window.termbase_mgr

        # Populate if we have database access
        if self.db_manager or self.termbase_mgr:
            self.populate_language_dropdowns()
            # Also populate the Settings → Translation Memories / Termbases
            # lists so they're not empty the first time the user opens them.
            # Previously these only populated when the user clicked "Refresh
            # List" or (in the main Workbench) switched to the Settings tab,
            # which confused users into thinking no resources were being
            # searched even though the fallback logic (empty selection =
            # search all) meant everything WAS being searched.
            try:
                self.refresh_tm_list()
            except Exception as e:
                print(f"[Superlookup] refresh_tm_list failed on first show: {e}")
            try:
                self.refresh_termbase_list()
            except Exception as e:
                print(f"[Superlookup] refresh_termbase_list failed on first show: {e}")
            self._languages_populated = True
            print("[Superlookup] Languages + resource lists populated on first show")
    
    def init_ui(self):
        """Initialize the UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)  # Reduced from 10 to 5 for consistency
        
        # Header and description – shown only when embedded in the main
        # window (hidden when inside the floating assistant, where the tab
        # label already identifies the feature and vertical space is precious).
        self._header_label = QLabel(self.tr("🔍 SuperLookup"))
        self._header_label.setStyleSheet("font-size: 11pt; font-weight: bold; color: #1976D2;")
        layout.addWidget(self._header_label, 0)

        if os.name == 'nt':
            description_text = (
                f"Press {format_shortcut_for_display('Ctrl+Alt+L')} or paste text to search your TMs and Termbases."
            )
        elif IS_MACOS:
            description_text = (
                f"Press {format_shortcut_for_display('Meta+Ctrl+L')} or paste text to search your TMs and Termbases.\n"
                "Requires Accessibility permission in System Settings → Privacy & Security."
            )
        else:
            description_text = (
                f"Press {format_shortcut_for_display('Ctrl+Alt+L')} or paste text to search your TMs and Termbases.\n"
                "Requires pynput installed (pip install pynput)."
            )

        self._description_label = QLabel(description_text)
        self._description_label.setWordWrap(True)
        self._description_label.setStyleSheet("color: #666; padding: 3px 5px; background-color: #E3F2FD; border-radius: 3px;")
        layout.addWidget(self._description_label, 0)

        # Search input area - compact single-line layout with search box and controls
        search_row = QHBoxLayout()
        
        # Search label and input
        search_label = QLabel("🔍")
        search_label.setStyleSheet("font-size: 12pt;")
        search_row.addWidget(search_label)
        
        # Use HistoryComboBox for search with history dropdown
        self.source_text = HistoryComboBox()
        self.source_text.setEditable(True)
        self.source_text.lineEdit().setPlaceholderText(self.tr("Enter search term or paste text..."))
        self.source_text.setMinimumHeight(30)
        self.source_text.setMaximumHeight(35)
        self.source_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Load history into dropdown
        if hasattr(self, 'search_history') and self.search_history:
            self.source_text.set_history(self.search_history)
        # Handle Enter key to trigger search
        self.source_text.lineEdit().returnPressed.connect(self.perform_lookup)
        search_row.addWidget(self.source_text, stretch=1)
        
        # Search button
        self.search_btn = QPushButton(self.tr("🔍 Search"))
        self.search_btn.setStyleSheet("font-weight: bold; background-color: #2196F3; color: white; padding: 6px 12px; outline: none;")
        self.search_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.search_btn.clicked.connect(self.perform_lookup)
        search_row.addWidget(self.search_btn)
        
        # Clear button
        clear_btn = QPushButton(self.tr("🗑️ Clear"))
        clear_btn.setStyleSheet("padding: 6px 8px;")
        clear_btn.clicked.connect(self.clear_all)
        search_row.addWidget(clear_btn)

        # Language filters live on the same row as the search controls
        # (to the right of Search / Clear) to save vertical space. The
        # search box keeps stretch=1, so it absorbs the extra width and
        # pushes the From/To controls to the right edge of the row.
        from_label = QLabel(self.tr("From:"))
        from_label.setStyleSheet("font-weight: bold; font-size: 9pt;")
        search_row.addWidget(from_label)

        self.lang_from_combo = QComboBox()
        self.lang_from_combo.setMinimumWidth(90)
        self.lang_from_combo.setToolTip(self.tr("Filter by source language (leave as 'Any' for all)"))
        self.lang_from_combo.currentIndexChanged.connect(self._on_language_changed)
        search_row.addWidget(self.lang_from_combo)

        # Swap button
        swap_btn = QPushButton("↔")
        swap_btn.setFixedWidth(28)
        swap_btn.setToolTip(self.tr("Swap source and target languages"))
        swap_btn.clicked.connect(self.swap_language_filters)
        search_row.addWidget(swap_btn)

        to_label = QLabel(self.tr("To:"))
        to_label.setStyleSheet("font-weight: bold; font-size: 9pt;")
        search_row.addWidget(to_label)

        self.lang_to_combo = QComboBox()
        self.lang_to_combo.setMinimumWidth(90)
        self.lang_to_combo.setToolTip(self.tr("Filter by target language (leave as 'Any' for all)"))
        self.lang_to_combo.currentIndexChanged.connect(self._on_language_changed)
        search_row.addWidget(self.lang_to_combo)

        layout.addLayout(search_row)
        
        # Initialize language dropdowns with "Any" option
        self.lang_from_combo.addItem("Any", None)
        self.lang_to_combo.addItem("Any", None)
        
        # Results area (with tabs for TM, termbase, MT)
        self.results_tabs = QTabWidget()
        self.results_tabs.tabBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.results_tabs.tabBar().setDrawBase(False)
        self.results_tabs.tabBar().setExpanding(False)
        # Force tabs to fit their full text. Without this macOS clips the
        # SuperLookup sub-tabs ("QuickTrans" → "QuickT…", "Web Resources"
        # → "Web Reso…", etc.) even when there's plenty of horizontal
        # space — Qt's default ElideRight is too aggressive on Mac.
        self.results_tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
        self.results_tabs.tabBar().setUsesScrollButtons(False)
        self.results_tabs.setStyleSheet("QTabBar::tab { outline: 0; } QTabBar::tab:focus { outline: none; } QTabBar::tab:selected { border-bottom: 1px solid #2196F3; background-color: rgba(33, 150, 243, 0.08); }")
        
        # TM Results tab
        tm_tab = self.create_tm_results_tab()
        self.results_tabs.addTab(tm_tab, "📖 TMs")
        
        # Termbase Results tab
        termbase_tab = self.create_termbase_results_tab()
        self.results_tabs.addTab(termbase_tab, "📚 Termbases")
        
        # MT tab removed – handled by QuickTrans.

        # Web Resources tab
        web_tab = self.create_web_resources_tab()
        self.results_tabs.addTab(web_tab, "🌐 Web Resources")
        
        # Settings tab
        settings_tab = self.create_settings_tab()
        self.results_tabs.addTab(settings_tab, "⚙️ SuperLookup Settings")
        
        # Connect tab change for Settings refresh
        self.results_tabs.currentChanged.connect(self.on_results_tab_changed)
        
        layout.addWidget(self.results_tabs, stretch=1)
        
        # Status bar
        self.status_label = QLabel(self.tr("Ready. Select a mode and capture text to begin."))
        self.status_label.setStyleSheet("padding: 5px; border-radius: 3px;")
        layout.addWidget(self.status_label, 0)  # 0 = no stretch, stays compact
    
    def create_tm_results_tab(self):
        """Create the TM results tab with horizontal/vertical view toggle"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # View mode toggle at the top
        view_toggle_layout = QHBoxLayout()
        view_toggle_layout.addWidget(QLabel(self.tr("View:")))
        
        self.tm_view_horizontal_radio = CheckmarkRadioButton(self.tr("Horizontal (Table)"))
        self.tm_view_horizontal_radio.setChecked(True)  # Default to horizontal
        self.tm_view_horizontal_radio.setToolTip(self.tr("Source and Target side-by-side in columns"))
        self.tm_view_horizontal_radio.toggled.connect(self.toggle_tm_view_mode)
        view_toggle_layout.addWidget(self.tm_view_horizontal_radio)
        
        self.tm_view_vertical_radio = CheckmarkRadioButton(self.tr("Vertical (List)"))
        self.tm_view_vertical_radio.setToolTip(self.tr("Source above Target, like traditional concordance"))
        view_toggle_layout.addWidget(self.tm_view_vertical_radio)

        # Text size for the TM results, right here rather than in Settings
        # (issue #69). Applies to both views and is remembered.
        view_toggle_layout.addSpacing(16)
        view_toggle_layout.addWidget(QLabel(self.tr("Text size:")))
        self.tm_font_size_spin = QSpinBox()
        self.tm_font_size_spin.setRange(7, 24)
        self.tm_font_size_spin.setSuffix(" pt")
        self.tm_font_size_spin.setToolTip(self.tr("Font size of the TM results (both views)"))
        self._tm_font_size = self._load_tm_font_size()
        self.tm_font_size_spin.setValue(self._tm_font_size or 10)
        self.tm_font_size_spin.valueChanged.connect(self._on_tm_font_size_changed)
        view_toggle_layout.addWidget(self.tm_font_size_spin)

        view_toggle_layout.addStretch()
        layout.addLayout(view_toggle_layout)
        
        # Stacked widget to hold both views
        self.tm_view_stack = QStackedWidget()
        
        # === Horizontal View (Table) ===
        horizontal_widget = QWidget()
        horizontal_layout = QVBoxLayout(horizontal_widget)
        horizontal_layout.setContentsMargins(0, 0, 0, 0)
        
        self.tm_results_table = QTableWidget()
        self.tm_results_table.setColumnCount(3)
        self.tm_results_table.setHorizontalHeaderLabels(["Source", "Target", "TM"])
        # Make column headers bold (body cells are rendered non-bold by _ReadOnlyHtmlCell).
        # Use both setFont and a stylesheet so bold is honoured on every Qt style.
        _tm_hdr_font = self.tm_results_table.horizontalHeader().font()
        _tm_hdr_font.setBold(True)
        self.tm_results_table.horizontalHeader().setFont(_tm_hdr_font)
        self.tm_results_table.setStyleSheet(
            "QHeaderView::section { font-weight: bold; }"
        )
        self.tm_results_table.horizontalHeader().setStretchLastSection(False)
        self.tm_results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tm_results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tm_results_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.tm_results_table.setColumnWidth(2, 150)  # TM name column
        self.tm_results_table.verticalHeader().setVisible(False)  # Hide row numbers
        self.tm_results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tm_results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # v1.10.173: click-header-to-sort. display_tm_results brackets the
        # rebuild with setSortingEnabled(False/True) to avoid mid-build
        # re-sorts. See termbase_results_table for the same pattern.
        self.tm_results_table.setSortingEnabled(True)
        self.tm_results_table.doubleClicked.connect(self.on_tm_result_double_click)
        self.tm_results_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tm_results_table.customContextMenuRequested.connect(self._tm_table_context_menu)
        horizontal_layout.addWidget(self.tm_results_table)
        
        self.tm_view_stack.addWidget(horizontal_widget)
        
        # === Vertical View (List - like Concordance dialog) ===
        vertical_widget = QWidget()
        vertical_layout = QVBoxLayout(vertical_widget)
        vertical_layout.setContentsMargins(0, 0, 0, 0)
        
        self.tm_results_vertical = QTextEdit()
        self.tm_results_vertical.setReadOnly(True)
        self.tm_results_vertical.setFont(QFont("Segoe UI", self._tm_font_size or 10))
        self.tm_results_vertical.setStyleSheet("border: 1px solid #ddd; border-radius: 4px;")
        vertical_layout.addWidget(self.tm_results_vertical)
        
        self.tm_view_stack.addWidget(vertical_widget)
        
        layout.addWidget(self.tm_view_stack)
        
        # Action buttons
        button_layout = QHBoxLayout()

        export_btn = QPushButton(self.tr("💾 Export Results…"))
        export_btn.setToolTip(self.tr("Save all TM hits for the current search to Excel (.xlsx) or CSV"))
        export_btn.clicked.connect(self.export_tm_results)
        button_layout.addWidget(export_btn)

        button_layout.addStretch()

        copy_btn = QPushButton(self.tr("📋 Copy Target"))
        copy_btn.clicked.connect(self.copy_selected_tm_target)
        button_layout.addWidget(copy_btn)
        
        insert_btn = QPushButton(self.tr("📥 Insert Target"))
        insert_btn.setToolTip(f"Insert selected translation ({format_shortcut_for_display('Ctrl+V')})")
        insert_btn.clicked.connect(self.insert_selected_tm_target)
        button_layout.addWidget(insert_btn)
        
        layout.addLayout(button_layout)
        
        return tab
    
    def create_termbase_results_tab(self):
        """Create the termbase results tab"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # Results table with expanded columns
        self.termbase_results_table = QTableWidget()
        self.termbase_results_table.setColumnCount(5)
        self.termbase_results_table.setHorizontalHeaderLabels(["Source", "Target", "Termbase", "Domain", "Notes"])
        # Make all columns resizable (Interactive) with stretch for Source/Target
        header = self.termbase_results_table.horizontalHeader()
        # Make column headers bold (body cells are rendered non-bold by _ReadOnlyHtmlCell).
        # Use both setFont and a stylesheet so bold is honoured on every Qt style.
        _tb_hdr_font = header.font()
        _tb_hdr_font.setBold(True)
        header.setFont(_tb_hdr_font)
        self.termbase_results_table.setStyleSheet(
            "QHeaderView::section { font-weight: bold; }"
        )
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)  # All columns resizable
        header.setStretchLastSection(False)  # Don't auto-stretch last column
        # Set initial column widths
        self.termbase_results_table.setColumnWidth(0, 200)  # Source
        self.termbase_results_table.setColumnWidth(1, 200)  # Target
        self.termbase_results_table.setColumnWidth(2, 100)  # Termbase column
        self.termbase_results_table.setColumnWidth(3, 80)   # Domain column
        self.termbase_results_table.setColumnWidth(4, 100)  # Notes column
        self.termbase_results_table.verticalHeader().setVisible(False)  # Hide row numbers
        self.termbase_results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.termbase_results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # v1.10.173: click-header-to-sort on every column. Source / Target
        # cells use a setCellWidget overlay (_ReadOnlyHtmlCell) for HTML
        # highlighting, but the underlying QTableWidgetItem carries the
        # plain text — that's what Qt sorts by, and the cell widgets follow
        # their parent items across reorderings. display_termbase_results
        # brackets the rebuild with setSortingEnabled(False/True) so the
        # row inserts don't trigger live re-sorts mid-build.
        self.termbase_results_table.setSortingEnabled(True)
        self.termbase_results_table.doubleClicked.connect(lambda: self.copy_selected_termbase_target())
        
        # Right-click context menu for termbase results
        self.termbase_results_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.termbase_results_table.customContextMenuRequested.connect(self._show_termbase_result_context_menu)
        
        layout.addWidget(self.termbase_results_table)
        
        # Action buttons
        button_layout = QHBoxLayout()
        
        add_term_btn = QPushButton(self.tr("➕ Add to Termbase"))
        add_term_btn.setToolTip(self.tr("Add source text and selected translation as new term"))
        add_term_btn.setStyleSheet("background-color: #4CAF50; color: white; padding: 5px; border: none; outline: none;")
        add_term_btn.clicked.connect(self.add_to_termbase)
        button_layout.addWidget(add_term_btn)
        
        button_layout.addStretch()
        
        copy_target_btn = QPushButton(self.tr("📋 Copy Translation"))
        copy_target_btn.clicked.connect(self.copy_selected_termbase_target)
        button_layout.addWidget(copy_target_btn)
        
        layout.addLayout(button_layout)
        
        # Info label
        info = QLabel(self.tr("💡 Tip: Double-click a term to copy translation to clipboard"))
        info.setStyleSheet("color: #666; font-size: 9pt; padding: 5px;")
        layout.addWidget(info)
        
        return tab
    
    def create_mt_results_tab(self):
        """Create the MT results tab with provider status and results"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # === Provider Status Summary ===
        status_frame = QFrame()
        status_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        status_frame.setStyleSheet("QFrame { background-color: #f5f5f5; border-radius: 4px; padding: 5px; }")
        status_layout = QVBoxLayout(status_frame)
        status_layout.setContentsMargins(10, 10, 10, 10)
        
        # Header row with title and settings link
        header_row = QHBoxLayout()
        
        status_title = QLabel(self.tr("🤖 MT Providers"))
        status_title.setStyleSheet("font-weight: bold; font-size: 11pt;")
        header_row.addWidget(status_title)
        
        header_row.addStretch()
        
        # Link to Settings
        settings_link = QPushButton(self.tr("⚙️ Configure in Settings"))
        settings_link.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #1976D2;
                border: none;
                font-size: 10pt;
                text-decoration: underline;
            }
            QPushButton:hover {
                color: #1565C0;
            }
        """)
        settings_link.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_link.clicked.connect(self._open_mt_settings)
        header_row.addWidget(settings_link)
        
        status_layout.addLayout(header_row)
        
        # Provider status label (shows which are active)
        self.mt_provider_status_label = QLabel()
        self.mt_provider_status_label.setWordWrap(True)
        self.mt_provider_status_label.setStyleSheet("color: #666; font-size: 9pt; padding: 5px 0;")
        status_layout.addWidget(self.mt_provider_status_label)
        
        # Update the status display
        self._update_mt_provider_status()
        
        layout.addWidget(status_frame)
        
        # === MT Results Section ===
        results_group = QGroupBox(self.tr("📝 Translation Results"))
        results_layout = QVBoxLayout(results_group)
        
        # Results table
        self.mt_results_table = QTableWidget()
        self.mt_results_table.setColumnCount(3)
        self.mt_results_table.setHorizontalHeaderLabels(["Provider", "Translation", ""])
        self.mt_results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.mt_results_table.setColumnWidth(0, 120)
        self.mt_results_table.setColumnWidth(2, 60)
        self.mt_results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.mt_results_table.verticalHeader().setVisible(False)
        self.mt_results_table.doubleClicked.connect(self.on_mt_result_double_click)
        
        results_layout.addWidget(self.mt_results_table)
        
        # Status
        self.mt_status_label = QLabel(self.tr("Enter text and click Search to get MT translations"))
        self.mt_status_label.setStyleSheet("color: #666; font-style: italic; padding: 5px;")
        results_layout.addWidget(self.mt_status_label)
        
        layout.addWidget(results_group, stretch=1)
        
        return tab
    
    def _open_mt_settings(self):
        """Navigate to Settings → MT Settings tab"""
        if self.main_window:
            # v1.10.161: label-based on both axes.
            # Was hard-coded main_tabs.setCurrentIndex(4) +
            # settings_tabs.setCurrentIndex(3), both stale: Settings drifted
            # to 7 (so 4 landed on SuperLookup) and MT Settings drifted
            # past index 3 (so 3 landed on Voice, not MT). Label lookup
            # can't silently drift the same way.
            if hasattr(self.main_window, '_switch_main_tab'):
                self.main_window._switch_main_tab("Settings")
            if hasattr(self.main_window, '_switch_settings_subtab'):
                self.main_window._switch_settings_subtab("MT")
    
    def _update_mt_provider_status(self):
        """Update the MT provider status display"""
        # Get API keys and enabled states from main window
        api_keys = {}
        enabled_providers = {}
        
        if self.main_window:
            if hasattr(self.main_window, 'load_api_keys'):
                api_keys = self.main_window.load_api_keys()
            if hasattr(self.main_window, 'load_provider_enabled_states'):
                enabled_providers = self.main_window.load_provider_enabled_states()
        
        # Define all MT providers
        providers = [
            ("DeepL", "mt_deepl", "deepl"),
            ("Google Translate", "mt_google_translate", "google_translate"),
            ("Microsoft Translator", "mt_microsoft", "microsoft_translate"),
            ("Amazon Translate", "mt_amazon", "amazon_translate"),
            ("ModernMT", "mt_modernmt", "modernmt"),
            ("MyMemory", "mt_mymemory", None),
        ]
        
        ready_providers = []
        disabled_providers = []
        missing_key_providers = []
        
        for name, enabled_key, api_key_name in providers:
            is_enabled = enabled_providers.get(enabled_key, True)
            has_key = api_key_name is None or bool(api_keys.get(api_key_name))
            
            if has_key and is_enabled:
                ready_providers.append(name)
            elif has_key and not is_enabled:
                disabled_providers.append(name)
            else:
                missing_key_providers.append(name)
        
        # Build status text
        status_parts = []
        if ready_providers:
            status_parts.append(f"✅ Active: {', '.join(ready_providers)}")
        if disabled_providers:
            status_parts.append(f"⏸️ Disabled: {', '.join(disabled_providers)}")
        if missing_key_providers:
            status_parts.append(f"❌ No API key: {', '.join(missing_key_providers)}")
        
        if not status_parts:
            status_text = "No MT providers configured"
        else:
            status_text = "\n".join(status_parts)
        
        self.mt_provider_status_label.setText(status_text)
    
    def _perform_mt_lookup(self, text: str, source_lang: str = None, target_lang: str = None):
        """Call enabled MT providers in parallel and return results.

        Skips entirely when this SuperlookupTab instance has no MT results table
        (e.g. inside the Floating Assistant / Sidekick, where MT is handled by
        QuickTrans's own MTFetchWorker). In that case the previous implementation
        was making blocking HTTP calls and discarding the results, freezing the
        UI for up to ~30 s on every Ctrl+Alt+L press.
        """
        # Sidekick / Floating Assistant has no MT table — don't burn HTTP calls.
        if not hasattr(self, 'mt_results_table'):
            return []

        if not self.main_window:
            return []

        # Get languages from main window if not provided
        if not source_lang:
            source_lang = getattr(self.main_window, 'source_language', 'en')
        if not target_lang:
            target_lang = getattr(self.main_window, 'target_language', 'nl')

        # Get API keys and enabled providers from Settings
        api_keys = {}
        enabled_providers = {}
        if hasattr(self.main_window, 'load_api_keys'):
            api_keys = self.main_window.load_api_keys()
        if hasattr(self.main_window, 'load_provider_enabled_states'):
            enabled_providers = self.main_window.load_provider_enabled_states()

        # Define MT providers with their settings keys and API key names
        providers = [
            ("DeepL", "mt_deepl", "deepl"),
            ("Google Translate", "mt_google_translate", "google_translate"),
            ("Microsoft Translator", "mt_microsoft", "microsoft_translate"),
            ("Amazon Translate", "mt_amazon", "amazon_translate"),
            ("ModernMT", "mt_modernmt", "modernmt"),
            ("MyMemory", "mt_mymemory", None),
        ]

        # Build the list of provider call closures to dispatch in parallel.
        active_calls = []
        for provider_name, enabled_key, api_key_name in providers:
            is_enabled = enabled_providers.get(enabled_key, True)
            has_key = api_key_name is None or bool(api_keys.get(api_key_name))
            if not is_enabled or not has_key:
                continue

            mw = self.main_window
            if provider_name == "DeepL" and hasattr(mw, 'call_deepl'):
                call = lambda mw=mw, k=api_keys.get('deepl'): mw.call_deepl(text, source_lang, target_lang, k)
            elif provider_name == "Google Translate" and hasattr(mw, 'call_google_translate'):
                call = lambda mw=mw, k=api_keys.get('google_translate'): mw.call_google_translate(text, source_lang, target_lang, k)
            elif provider_name == "Microsoft Translator" and hasattr(mw, 'call_microsoft_translate'):
                call = lambda mw=mw, k=api_keys.get('microsoft_translate'): mw.call_microsoft_translate(text, source_lang, target_lang, k)
            elif provider_name == "Amazon Translate" and hasattr(mw, 'call_amazon_translate'):
                region = api_keys.get('amazon_translate_region', 'us-east-1')
                call = lambda mw=mw, k=api_keys.get('amazon_translate'), r=region: mw.call_amazon_translate(text, source_lang, target_lang, k, r)
            elif provider_name == "ModernMT" and hasattr(mw, 'call_modernmt'):
                call = lambda mw=mw, k=api_keys.get('modernmt'): mw.call_modernmt(text, source_lang, target_lang, k)
            elif provider_name == "MyMemory":
                call = lambda: self._call_mymemory(text, source_lang, target_lang)
            else:
                continue

            active_calls.append((provider_name, call))

        if not active_calls:
            return []

        # Fan out: every provider runs concurrently. Total wall-clock is now
        # max(provider_times) instead of sum(provider_times). Per-future timeout
        # caps a single misbehaving provider; the overall as_completed wait
        # caps the whole batch so the UI can never stall longer than that.
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
        import time

        per_provider_timeout = 5.0
        overall_timeout = 6.0  # small slack over per-provider so timeouts surface as errors
        results = []
        deadline = time.monotonic() + overall_timeout

        with ThreadPoolExecutor(max_workers=len(active_calls), thread_name_prefix="sv-mt") as executor:
            future_to_name = {executor.submit(call): name for name, call in active_calls}
            for future, provider_name in list(future_to_name.items()):
                remaining = max(0.0, min(per_provider_timeout, deadline - time.monotonic()))
                try:
                    translation = future.result(timeout=remaining)
                except FuturesTimeout:
                    future.cancel()
                    results.append({
                        'provider': provider_name,
                        'translation': "[Error: timed out]",
                        'is_error': True,
                    })
                    continue
                except Exception as e:
                    print(f"[Superlookup] MT error ({provider_name}): {e}")
                    results.append({
                        'provider': provider_name,
                        'translation': f"[Error: {str(e)}]",
                        'is_error': True,
                    })
                    continue

                if translation:
                    results.append({
                        'provider': provider_name,
                        'translation': translation,
                        'is_error': translation.startswith('['),
                    })

        return results
    
    def _call_mymemory(self, text: str, source_lang: str, target_lang: str) -> str:
        """Call MyMemory free translation API"""
        try:
            import requests
            
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
            
            src = lang_name_to_code.get(src_lower, src_lower.split('-')[0].split('_')[0])
            tgt = lang_name_to_code.get(tgt_lower, tgt_lower.split('-')[0].split('_')[0])
            
            url = "https://api.mymemory.translated.net/get"
            params = {
                'q': text,
                'langpair': f"{src}|{tgt}"
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            if data.get('responseStatus') == 200:
                return data['responseData']['translatedText']
            else:
                return f"[MyMemory error: {data.get('responseStatus')}]"
                
        except Exception as e:
            return f"[MyMemory error: {e}]"

    def create_web_resources_tab(self):
        """Create the Web Resources tab with vertical sidebar for different web resources"""
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Try to import QWebEngineView for embedded browser with persistent storage
        self.web_engine_available = False
        self.web_profile = None
        self.SilentWebPage = None  # Custom page class that suppresses JS console spam
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile

            # Create a custom page class that silences JavaScript console messages
            class SilentWebEnginePage(QWebEnginePage):
                """Custom QWebEnginePage that suppresses JavaScript console output.

                External websites (IATE, Juremy, Google Patents, etc.) emit tons of
                noisy console messages about CSP violations, mixed content, etc.
                These are not relevant to Supervertaler and clutter the terminal.
                """
                def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
                    # Silently ignore all JS console messages from web pages
                    pass

            # Subclassed view that adds default-browser items to the right-click
            # menu: "Open link in default browser" when the click target is a
            # hyperlink, plus an always-available "Open page in default browser".
            class SidekickWebEngineView(QWebEngineView):
                def contextMenuEvent(self, event):
                    menu = self.createStandardContextMenu()
                    try:
                        request = self.lastContextMenuRequest()
                    except AttributeError:
                        request = None
                    link_url = None
                    if request is not None:
                        try:
                            url = request.linkUrl()
                            if url is not None and not url.isEmpty():
                                link_url = QUrl(url)
                        except Exception:
                            link_url = None

                    menu.addSeparator()
                    if link_url is not None:
                        link_action = menu.addAction("Open link in default browser")
                        link_action.triggered.connect(
                            lambda _checked=False, u=link_url: QDesktopServices.openUrl(u)
                        )
                    page_url = self.url()
                    if page_url is not None and not page_url.isEmpty() and page_url.toString() != "about:blank":
                        page_action = menu.addAction("Open page in default browser")
                        page_action.triggered.connect(
                            lambda _checked=False, u=QUrl(page_url): QDesktopServices.openUrl(u)
                        )
                    menu.exec(event.globalPos())

            self.SilentWebPage = SilentWebEnginePage
            self.QWebEngineView = SidekickWebEngineView
            self.QWebEngineProfile = QWebEngineProfile
            self.web_engine_available = True
            
            # Create persistent profile for login/cookie storage
            if self.user_data_path:
                storage_path = os.path.join(str(self.user_data_path), 'workbench', 'web_cache')
            else:
                # Fallback to script directory if user_data_path not provided
                storage_path = os.path.join(str(_APP_DIR), 'user_data', 'workbench', 'web_cache')
            os.makedirs(storage_path, exist_ok=True)
            self.web_profile = QWebEngineProfile("SuperlookupProfile", self)
            self.web_profile.setPersistentStoragePath(storage_path)
            self.web_profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
            print(f"[Superlookup] QWebEngineView available - embedded browser enabled with persistent storage at {storage_path}")
        except ImportError:
            print("[Superlookup] QWebEngineView not available - external browser only")
            self.QWebEngineView = None
            self.QWebEngineProfile = None
        
        # Setting for browser mode (embedded vs external)
        self.web_browser_mode = 'embedded' if self.web_engine_available else 'external'
        
        # === Web Resources Definitions ===
        # Each resource has: name, icon, url_template with {query}, {sl} (source lang), {tl} (target lang)
        # Language codes vary per service - we'll map them in _get_web_lang_code()
        self.web_resources = [
            {
                'id': 'iate',
                'name': 'IATE',
                'icon': '🇪🇺',
                'description': 'EU terminology database',
                'url_template': 'https://iate.europa.eu/search/byUrl?term={query}&sl={sl}&tl={tl}',
                'lang_format': 'iso2',  # en, nl, de, fr
                'bidirectional': False,  # respects source/target direction
            },
            {
                'id': 'linguee',
                'name': 'Linguee',
                'icon': '📗',
                'description': 'Bilingual dictionary with context',
                'url_template': 'https://www.linguee.com/{sl_full}-{tl_full}/search?source=auto&query={query}',
                'lang_format': 'full_lower',  # dutch, english, german
                'bidirectional': True,  # searches both directions
            },
            {
                'id': 'proz',
                'name': 'ProZ.com',
                'icon': '💬',
                'description': 'Translator terminology database',
                'url_template': 'https://www.proz.com/search/?term={query}&from={sl}&to={tl}&results_per_page=25&es=1',
                'lang_format': 'iso3',  # dut, eng, ger
                'bidirectional': False,
            },
            {
                'id': 'reverso',
                'name': 'Reverso Context',
                'icon': '🔄',
                'description': 'Context-based translations',
                'url_template': 'https://context.reverso.net/translation/{sl_full}-{tl_full}/{query}',
                'lang_format': 'full_lower',  # dutch, english
                'bidirectional': False,
            },
            {
                'id': 'google',
                'name': 'Google Search',
                'icon': '🔍',
                'description': 'General web search',
                'url_template': 'https://www.google.com/search?q={query}',
                'lang_format': None,  # No language needed
                'bidirectional': True,
            },
            {
                'id': 'google_patents',
                'name': 'Google Patents',
                'icon': '📜',
                'description': 'Patent search',
                'url_template': 'https://patents.google.com/?q="{query}"',
                'lang_format': None,
                'bidirectional': True,
            },
            {
                'id': 'wikipedia_source',
                'name': 'Wikipedia (Source)',
                'icon': '📖',
                'description': 'Wikipedia in source language',
                'url_template': 'https://{sl}.wikipedia.org/w/index.php?search={query}',
                'lang_format': 'iso2',  # nl, en, de
                'bidirectional': True,
            },
            {
                'id': 'wikipedia_target',
                'name': 'Wikipedia (Target)',
                'icon': '📖',
                'description': 'Wikipedia in target language',
                'url_template': 'https://{tl}.wikipedia.org/w/index.php?search={query}',
                'lang_format': 'iso2',
                'bidirectional': True,
            },
            {
                'id': 'juremy',
                'name': 'Juremy',
                'icon': '⚖️',
                'description': 'Legal terminology database',
                'url_template': 'https://juremy.com/search?src={sl}&dst={tl}&q={query}&opts=ia&tool=iws',
                'lang_format': 'iso639_3',  # nld, eng, deu (ISO 639-3 codes)
                'bidirectional': False,
            },
            {
                'id': 'beijerterm',  # internal id kept so existing user settings still resolve
                'name': 'Beijer Dictionary',
                'icon': '📚',
                'description': 'Curated multilingual terminology database (Dutch ↔ English)',
                'url_template': 'https://beijerdictionary.com/?q={query}&from={sl}&to={tl}',
                'lang_format': 'iso2',  # nl, en — Beijerterm deep-link expects ISO 639-1
                'bidirectional': True,
            },
            {
                'id': 'acronymfinder',
                'name': 'AcronymFinder',
                'icon': '🔤',
                'description': 'Acronym and abbreviation dictionary',
                'url_template': 'https://www.acronymfinder.com/~/search/af.aspx?string=exact&Acronym={query}',
                'lang_format': None,
                'bidirectional': True,
            },
            {
                'id': 'babelnet',
                'name': 'BabelNet',
                'icon': '🌐',
                'description': 'Multilingual encyclopedic dictionary',
                'url_template': 'https://babelnet.org/search?word={query}&lang={sl_upper}&transLang={tl_upper}',
                'lang_format': 'iso2_upper',  # NL, EN, DE
                'bidirectional': False,
            },
            {
                'id': 'wiktionary_source',
                'name': 'Wiktionary (Source)',
                'icon': '📓',
                'description': 'Wiktionary in source language',
                'url_template': 'https://{sl}.wiktionary.org/wiki/{query}',
                'lang_format': 'iso2',
                'bidirectional': True,
            },
            {
                'id': 'wiktionary_target',
                'name': 'Wiktionary (Target)',
                'icon': '📓',
                'description': 'Wiktionary in target language',
                'url_template': 'https://{tl}.wiktionary.org/wiki/{query}',
                'lang_format': 'iso2',
                'bidirectional': True,
            },
            {
                'id': 'github_code',
                'name': 'GitHub Code (all)',
                'icon': '💻',
                'description': 'Search code across all GitHub repositories',
                'url_template': 'https://github.com/search?q={query}&type=code',
                'lang_format': None,
                'bidirectional': True,
            },
            {
                'id': 'opus_corpus',
                'name': 'OPUS Corpus',
                'icon': '📚',
                'description': 'Search 58B parallel sentences from OPUS parallel corpora',
                'url_template': 'https://opus.nlpl.eu/bin/opuscqp.pl?corpus={opus_corpus};lang={sl};cqp={query};align={tl}',
                'lang_format': 'iso2',
                'bidirectional': False,
                'has_corpus_selector': True,  # Special flag for OPUS
            },
        ]
        
        # OPUS corpus options for the selector
        self.opus_corpora = [
            ('DGT', 'DGT - EU Translation Memory (1.1B pairs)'),
            ('Europarl', 'Europarl - EU Parliament (186M pairs)'),
            ('OpenSubtitles', 'OpenSubtitles - Movies/TV (20B pairs)'),
            ('EMEA', 'EMEA - Medicines Agency (243M pairs)'),
            ('EUbookshop', 'EUbookshop - EU Publications (279M pairs)'),
            ('JRC-Acquis', 'JRC-Acquis - EU Legislation (147M pairs)'),
            ('ECB', 'ECB - Central Bank (15M pairs)'),
            ('TED2020', 'TED2020 - TED Talks (143M pairs)'),
            ('WikiMatrix', 'WikiMatrix - Wikipedia (127M pairs)'),
            ('GlobalVoices', 'GlobalVoices - News (7.3M pairs)'),
            ('Tatoeba', 'Tatoeba - Example Sentences (8.7M pairs)'),
        ]
        self.current_opus_corpus = 'DGT'  # Default corpus
        
        # === Use QSplitter for resizable sidebar ===
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # === Left sidebar with vertical tabs ===
        sidebar = QWidget()
        sidebar.setMinimumWidth(120)
        sidebar.setMaximumWidth(250)
        sidebar.setStyleSheet("""
            QWidget {
                background-color: #f5f5f5;
                border-right: 1px solid #ddd;
            }
        """)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(5, 5, 5, 5)
        sidebar_layout.setSpacing(2)

        # Sidebar header
        sidebar_header = QLabel(self.tr("🌐 Resources"))
        sidebar_header.setStyleSheet(
            f"font-weight: bold; font-size: {scaled_pt(10):.1f}pt; padding: 5px; color: #1976D2;"
        )
        sidebar_layout.addWidget(sidebar_header)

        # Scrollable area for resource buttons – ensures all resources are
        # accessible even on smaller screens / when the panel is short.
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        scroll_content = QWidget()
        scroll_content.setStyleSheet("background: transparent;")
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(2)

        # Resource buttons list
        self.web_resource_buttons = []
        self.web_resource_button_group = QButtonGroup(self)
        self.web_resource_button_group.setExclusive(True)

        # The user's own lookup sites go below the built-in ones (#208)
        self._builtin_web_resource_count = len(self.web_resources)
        self.web_resources.extend(self._load_custom_web_resources())
        self._web_resource_scroll_layout = scroll_layout
        self._rebuild_web_resource_buttons()

        scroll_area.setWidget(scroll_content)
        sidebar_layout.addWidget(scroll_area, stretch=1)

        custom_btn = QPushButton(self.tr("⚙ Custom Resources…"))
        custom_btn.setToolTip(self.tr("Add your own lookup sites to this list"))
        custom_btn.setStyleSheet(f"""
            QPushButton {{
                text-align: left;
                padding: 4px 8px;
                border: 1px dashed #bbb;
                border-radius: 4px;
                background-color: transparent;
                color: #555;
                font-size: {scaled_pt(8.5):.1f}pt;
            }}
            QPushButton:hover {{
                background-color: #e3e3e3;
            }}
        """)
        custom_btn.clicked.connect(self._manage_custom_web_resources)
        sidebar_layout.addWidget(custom_btn)
        
        # "Search All" button - pre-loads all resources
        search_all_btn = QPushButton(self.tr("🔎 Search All"))
        search_all_btn.setToolTip(self.tr("Search all web resources at once (embedded mode)"))
        search_all_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 8px;
                background-color: #FF9800;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: {scaled_pt(9):.1f}pt;
                outline: none;
            }}
            QPushButton:hover {{
                background-color: #F57C00;
            }}
            QPushButton:focus {{
                outline: none;
                border: none;
            }}
        """)
        search_all_btn.clicked.connect(lambda: self._perform_web_search(search_all=True))
        sidebar_layout.addWidget(search_all_btn)
        
        # Browser mode toggle
        mode_label = QLabel(self.tr("Mode:"))
        mode_label.setStyleSheet(f"font-size: {scaled_pt(8):.1f}pt; color: #666; padding-top: 5px;")
        sidebar_layout.addWidget(mode_label)
        
        self.web_mode_embedded_radio = CheckmarkRadioButton(self.tr("Embedded"))
        self.web_mode_embedded_radio.setToolTip(self.tr("Show results inside Supervertaler"))
        self.web_mode_embedded_radio.setChecked(self.web_browser_mode == 'embedded')
        self.web_mode_embedded_radio.setEnabled(self.web_engine_available)
        self.web_mode_embedded_radio.toggled.connect(self._on_web_mode_changed)
        sidebar_layout.addWidget(self.web_mode_embedded_radio)
        
        self.web_mode_external_radio = CheckmarkRadioButton(self.tr("External"))
        self.web_mode_external_radio.setToolTip(self.tr("Open results in default browser"))
        self.web_mode_external_radio.setChecked(self.web_browser_mode == 'external')
        self.web_mode_external_radio.toggled.connect(self._on_web_mode_changed)
        sidebar_layout.addWidget(self.web_mode_external_radio)
        
        # "Open in Browser" button at bottom of sidebar
        self.web_open_external_btn = QPushButton(self.tr("🌍 Open in Browser"))
        self.web_open_external_btn.setToolTip(self.tr("Open current page in your default web browser"))
        self.web_open_external_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 8px;
                min-height: 20px;
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: {scaled_pt(9):.1f}pt;
                margin-top: 5px;
                outline: none;
            }}
            QPushButton:hover {{
                background-color: #45a049;
            }}
            QPushButton:focus {{
                outline: none;
                border: none;
            }}
        """)
        self.web_open_external_btn.clicked.connect(self._open_web_resource_external)
        sidebar_layout.addWidget(self.web_open_external_btn)
        
        splitter.addWidget(sidebar)
        
        # === Main content area ===
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(10, 10, 10, 10)
        content_layout.setSpacing(5)
        
        # Info label showing current language direction (no separate search bar - uses main Superlookup search)
        self.web_lang_info_label = QLabel(self.tr("Languages: Any → Any  •  Click Search above or select a resource"))
        self.web_lang_info_label.setStyleSheet(
            f"color: #666; font-size: {scaled_pt(9):.1f}pt; padding: 3px 0;"
        )
        content_layout.addWidget(self.web_lang_info_label)
        
        # OPUS corpus selector (hidden by default, shown when OPUS is selected)
        self.opus_selector_widget = QWidget()
        opus_selector_layout = QHBoxLayout(self.opus_selector_widget)
        opus_selector_layout.setContentsMargins(0, 5, 0, 5)
        opus_selector_layout.setSpacing(8)
        
        opus_label = QLabel(self.tr("📚 Corpus:"))
        opus_label.setStyleSheet("font-weight: bold; color: #1976D2;")
        opus_selector_layout.addWidget(opus_label)
        
        self.opus_corpus_combo = QComboBox()
        self.opus_corpus_combo.setMinimumWidth(280)
        self.opus_corpus_combo.setStyleSheet("""
            QComboBox {
                padding: 5px 10px;
                border: 1px solid #1976D2;
                border-radius: 4px;
                background: white;
            }
            QComboBox:hover {
                border-color: #1565C0;
            }
            QComboBox::drop-down {
                border: none;
                padding-right: 8px;
            }
        """)
        for corpus_id, corpus_name in self.opus_corpora:
            self.opus_corpus_combo.addItem(corpus_name, corpus_id)
        self.opus_corpus_combo.currentIndexChanged.connect(self._on_opus_corpus_changed)
        opus_selector_layout.addWidget(self.opus_corpus_combo)
        
        opus_selector_layout.addStretch()
        self.opus_selector_widget.hide()  # Hidden by default
        content_layout.addWidget(self.opus_selector_widget)
        
        # Web view container - stacked widget for embedded views per resource
        self.web_view_stack = QStackedWidget()
        
        # LAZY LOADING: Don't create all web views upfront - create them on demand
        # This dramatically improves startup performance and memory usage
        # Each QWebEngineView spawns a Chromium process, so 14 views = 14 processes
        self.web_views = {}  # Will be populated lazily when resources are selected
        # Each web view is wrapped in a container widget that adds a find-in-page bar.
        # The stack stores containers, but self.web_views still holds the raw views
        # so existing setUrl() / page() callers keep working unchanged.
        self.web_view_containers = {}
        
        # Fallback view for external mode or when web engine not available
        self.web_results_view = QTextEdit()
        self.web_results_view.setReadOnly(True)
        self.web_results_view.setStyleSheet("""
            QTextEdit {
                border: 1px solid #ddd;
                border-radius: 4px;
                padding: 10px;
                font-size: 11pt;
            }
        """)
        self._show_web_welcome_message()
        self.web_view_stack.addWidget(self.web_results_view)
        
        content_layout.addWidget(self.web_view_stack, stretch=1)
        
        splitter.addWidget(content_widget)
        splitter.setStretchFactor(0, 0)  # Sidebar doesn't stretch
        splitter.setStretchFactor(1, 1)  # Content area stretches
        splitter.setSizes([150, 600])    # Initial sizes
        
        layout.addWidget(splitter, stretch=1)
        
        # Track current resource and last search URL
        self.current_web_resource_index = 0
        self.last_web_search_url = None
        self.last_web_search_query = None
        
        # Select first resource by default
        if self.web_resource_buttons:
            self.web_resource_buttons[0].setChecked(True)
        
        # Show correct view based on mode
        self._update_web_view_for_mode()
        
        # Update enabled state based on settings
        self.search_web_enabled = True  # Now implemented!
        
        return tab
    
    def _web_resource_button(self, resource, index):
        btn = QPushButton(f"{resource['icon']} {resource['name']}")
        btn.setCheckable(True)
        btn.setToolTip(resource['description'])
        btn.setStyleSheet(f"""
            QPushButton {{
                text-align: left;
                padding: 5px 8px;
                border: none;
                border-radius: 4px;
                background-color: transparent;
                font-size: {scaled_pt(9):.1f}pt;
                outline: none;
            }}
            QPushButton:hover {{
                background-color: #e3e3e3;
            }}
            QPushButton:checked {{
                background-color: #2196F3;
                color: white;
                font-weight: bold;
            }}
            QPushButton:focus {{
                outline: none;
                border: none;
            }}
        """)
        btn.clicked.connect(lambda checked, idx=index: self._on_web_resource_selected(idx))
        return btn

    def _rebuild_web_resource_buttons(self):
        """(Re)create the sidebar buttons from self.web_resources."""
        layout = self._web_resource_scroll_layout
        for btn in self.web_resource_buttons:
            self.web_resource_button_group.removeButton(btn)
            btn.deleteLater()
        self.web_resource_buttons = []
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for i, resource in enumerate(self.web_resources):
            btn = self._web_resource_button(resource, i)
            self.web_resource_button_group.addButton(btn, i)
            self.web_resource_buttons.append(btn)
            layout.addWidget(btn)
        layout.addStretch()

    def _settings_window(self):
        """The main window, which owns the settings – also from the detached
        SuperLookup window, whose direct parent is that window."""
        w = self.main_window
        while w is not None and not hasattr(w, 'save_general_settings'):
            w = w.parent() if hasattr(w, 'parent') else None
        return w

    def _load_custom_web_resources(self):
        from modules import custom_web_resources as cwr
        try:
            mw = self._settings_window()
            if mw and hasattr(mw, 'load_general_settings'):
                return cwr.to_resources((mw.load_general_settings() or {}).get(cwr.SETTINGS_KEY))
        except Exception as e:
            print(f"[Superlookup] Could not load custom web resources: {e}")
        return []

    def _manage_custom_web_resources(self):
        """Add, edit or remove the user's own web resources (#208)."""
        from modules import custom_web_resources as cwr
        mw = self._settings_window()
        if not (mw and hasattr(mw, 'load_general_settings') and hasattr(mw, 'save_general_settings')):
            return
        settings = mw.load_general_settings() or {}
        dialog = cwr.CustomWebResourcesDialog(settings.get(cwr.SETTINGS_KEY), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        entries = dialog.entries()
        settings = mw.load_general_settings() or {}
        settings[cwr.SETTINGS_KEY] = entries
        mw.save_general_settings(settings)

        # Every open SuperLookup (docked tab, top-level tab, detached window)
        custom = cwr.to_resources(entries)
        tabs = [t for t in mw.findChildren(SuperlookupTab) if hasattr(t, 'web_resource_buttons')]
        if self not in tabs:
            tabs.append(self)
        for tab in tabs:
            tab._apply_custom_web_resources(custom)
        if hasattr(mw, 'log'):
            mw.log(f"🌐 Custom web resources saved: {len(custom)}")

    def _apply_custom_web_resources(self, custom):
        """Replace the custom part of the resource list and rebuild the sidebar."""
        current = self.web_resources[self.current_web_resource_index]['id'] \
            if 0 <= self.current_web_resource_index < len(self.web_resources) else None
        # Drop the browser views of resources that were removed
        keep = {r['id'] for r in custom}
        for old in self.web_resources[self._builtin_web_resource_count:]:
            if old['id'] not in keep and old['id'] in self.web_views:
                container = self.web_view_containers.pop(old['id'], None)
                self.web_views.pop(old['id'], None)
                if container is not None:
                    self.web_view_stack.removeWidget(container)
                    container.deleteLater()
        del self.web_resources[self._builtin_web_resource_count:]
        self.web_resources.extend(custom)
        self._rebuild_web_resource_buttons()

        ids = [r['id'] for r in self.web_resources]
        index = ids.index(current) if current in ids else 0
        if self.web_resource_buttons:
            self.web_resource_buttons[index].setChecked(True)
            self._on_web_resource_selected(index)

    def _show_web_welcome_message(self):
        """Show welcome message in the web results view"""
        mode_info = "embedded browser" if self.web_browser_mode == 'embedded' else "your default web browser"
        self.web_results_view.setHtml(f"""
            <div style="text-align: center; padding: 40px; color: #999;">
                <h2>🌐 Web Resources</h2>
                <p>Select a resource from the sidebar and enter a search term.</p>
                <p>Results will open in <b>{mode_info}</b>.</p>
                <p style="font-size: 10pt; margin-top: 20px;">
                    <b>Tip:</b> The search respects the language direction from the From/To dropdowns above.
                </p>
                <p style="font-size: 9pt; color: #aaa; margin-top: 10px;">
                    Use the mode toggle in the sidebar to switch between embedded and external browser.
                </p>
            </div>
        """)
    
    def _create_web_view_for_resource(self, resource):
        """Lazily create a web view for a specific resource (on-demand creation)
        
        This improves performance by not creating all 14+ QWebEngineView instances at startup.
        Each QWebEngineView spawns its own Chromium process, so lazy loading saves significant
        memory and startup time.
        """
        if not self.web_engine_available:
            return
            
        if resource['id'] in self.web_views:
            return  # Already created
            
        web_view = self.QWebEngineView()

        # Use persistent profile with silent page (suppresses JS console spam)
        if self.web_profile and self.SilentWebPage:
            page = self.SilentWebPage(self.web_profile, web_view)
            web_view.setPage(page)

        web_view.setUrl(QUrl("about:blank"))

        # Wrap the view in a container that adds a Ctrl+F find-in-page toolbar.
        # The container is what gets added to the stack; self.web_views still
        # maps to the raw view so existing setUrl()/page() callers are unchanged.
        container = self._create_web_view_container(web_view)

        self.web_view_stack.addWidget(container)
        self.web_views[resource['id']] = web_view
        self.web_view_containers[resource['id']] = container

        print(f"[Superlookup] Created web view for {resource['name']} (lazy load)")

    def _create_web_view_container(self, web_view):
        """Wrap a QWebEngineView in a container with a hidden find-in-page bar.

        The bar toggles via Ctrl+F (when the container has focus) and Esc.
        Enter / Shift+Enter cycle next/previous match. Match count and a
        case-sensitive toggle live on the bar itself.
        """
        from PyQt6.QtGui import QShortcut, QKeySequence
        from PyQt6.QtWebEngineCore import QWebEnginePage

        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(web_view, stretch=1)

        # --- Find bar ---
        find_bar = QWidget()
        find_bar.setStyleSheet("""
            QWidget#sl_find_bar { background-color: #f5f5f5; border-top: 1px solid #ddd; }
            QLineEdit { border: 1px solid #bbb; border-radius: 3px; padding: 3px 6px; background: white; }
            QPushButton { padding: 2px 8px; border: 1px solid transparent; border-radius: 3px; background: transparent; }
            QPushButton:hover { background: #e3e3e3; border: 1px solid #ccc; }
            QLabel { color: #666; font-size: 9pt; }
        """)
        find_bar.setObjectName("sl_find_bar")
        h = QHBoxLayout(find_bar)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(4)

        h.addWidget(QLabel(self.tr("Find:")))
        find_input = QLineEdit()
        find_input.setPlaceholderText(self.tr("Find on page…"))
        find_input.setMinimumWidth(220)
        h.addWidget(find_input, stretch=1)

        status = QLabel("")
        status.setMinimumWidth(80)
        h.addWidget(status)

        prev_btn = QPushButton("▲")
        prev_btn.setToolTip(self.tr("Previous match (Shift+Enter)"))
        prev_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        h.addWidget(prev_btn)

        next_btn = QPushButton("▼")
        next_btn.setToolTip(self.tr("Next match (Enter)"))
        next_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        h.addWidget(next_btn)

        case_check = QCheckBox(self.tr("Match case"))
        case_check.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        h.addWidget(case_check)

        clear_btn = QPushButton("✕")
        clear_btn.setToolTip(self.tr("Clear (Esc)"))
        clear_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        h.addWidget(clear_btn)

        # Bar is permanently visible so users discover it without needing Ctrl+F.
        v.addWidget(find_bar)

        # --- Behaviour ---
        def _do_find(backward=False):
            text = find_input.text()
            flags = QWebEnginePage.FindFlag(0)
            if backward:
                flags |= QWebEnginePage.FindFlag.FindBackward
            if case_check.isChecked():
                flags |= QWebEnginePage.FindFlag.FindCaseSensitively

            if not text:
                web_view.findText("")
                status.setText("")
                find_input.setStyleSheet("")
                return

            def _cb(result):
                try:
                    total = result.numberOfMatches()
                    active = result.activeMatch()
                except Exception:
                    return
                if total == 0:
                    status.setText(self.tr("No matches"))
                    find_input.setStyleSheet("background: #fde7e9;")
                else:
                    status.setText(f"{active} of {total}")
                    find_input.setStyleSheet("")

            try:
                web_view.findText(text, flags, _cb)
            except TypeError:
                # Older Qt without callback overload
                web_view.findText(text, flags)
                status.setText("")

        def focus_find_bar():
            """Focus the find input (bar is always visible)."""
            find_input.setFocus()
            find_input.selectAll()
            if find_input.text():
                _do_find()

        def clear_find():
            """Clear the search and return focus to the page."""
            find_input.clear()
            web_view.findText("")
            status.setText("")
            find_input.setStyleSheet("")
            web_view.setFocus()

        find_input.textChanged.connect(lambda _t: _do_find(backward=False))
        find_input.returnPressed.connect(lambda: _do_find(backward=False))
        prev_btn.clicked.connect(lambda: _do_find(backward=True))
        next_btn.clicked.connect(lambda: _do_find(backward=False))
        case_check.toggled.connect(lambda _c: _do_find(backward=False))
        clear_btn.clicked.connect(clear_find)

        # Ctrl+F focuses the input (bar is always visible).
        find_shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Find), container)
        find_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        find_shortcut.activated.connect(focus_find_bar)

        # Esc clears the search and returns focus to the page.
        esc_shortcut = QShortcut(QKeySequence("Escape"), find_input)
        esc_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        esc_shortcut.activated.connect(clear_find)

        # Shift+Enter for previous match while the input has focus.
        shift_enter = QShortcut(QKeySequence("Shift+Return"), find_input)
        shift_enter.setContext(Qt.ShortcutContext.WidgetShortcut)
        shift_enter.activated.connect(lambda: _do_find(backward=True))
        shift_enter_pad = QShortcut(QKeySequence("Shift+Enter"), find_input)
        shift_enter_pad.setContext(Qt.ShortcutContext.WidgetShortcut)
        shift_enter_pad.activated.connect(lambda: _do_find(backward=True))

        # Stash references on the container so they aren't garbage-collected
        # and so callers can introspect (e.g. tests).
        container.web_view = web_view
        container.find_bar = find_bar
        container.find_input = find_input
        container.focus_find_bar = focus_find_bar
        container.clear_find = clear_find

        return container

    def _get_web_view_index(self, resource_id):
        """Get the stack index for a web view container by resource ID"""
        if resource_id in self.web_view_containers:
            return self.web_view_stack.indexOf(self.web_view_containers[resource_id])
        return -1
    
    def _on_web_mode_changed(self, checked):
        """Handle browser mode toggle"""
        if self.web_mode_embedded_radio.isChecked():
            self.web_browser_mode = 'embedded'
        else:
            self.web_browser_mode = 'external'
        
        self._update_web_view_for_mode()
        self._show_web_welcome_message()
        print(f"[Superlookup] Web browser mode changed to: {self.web_browser_mode}")
    
    def _update_web_view_for_mode(self):
        """Update the view stack based on current mode"""
        if self.web_browser_mode == 'embedded' and self.web_engine_available:
            # Show embedded view for current resource (create lazily if needed)
            resource = self.web_resources[self.current_web_resource_index]
            if resource['id'] not in self.web_views:
                self._create_web_view_for_resource(resource)
            idx = self._get_web_view_index(resource['id'])
            if idx >= 0:
                self.web_view_stack.setCurrentIndex(idx)
        else:
            # Show text view (for external mode or fallback)
            self.web_view_stack.setCurrentIndex(self.web_view_stack.indexOf(self.web_results_view))
    
    def _on_web_resource_selected(self, index):
        """Handle web resource selection from sidebar"""
        self.current_web_resource_index = index
        resource = self.web_resources[index]
        
        # Show/hide OPUS corpus selector based on selected resource
        if hasattr(self, 'opus_selector_widget'):
            if resource.get('has_corpus_selector'):
                self.opus_selector_widget.show()
            else:
                self.opus_selector_widget.hide()
        
        # Update view based on mode
        if self.web_browser_mode == 'embedded' and self.web_engine_available:
            # LAZY LOADING: Create web view for this resource if it doesn't exist yet
            if resource['id'] not in self.web_views:
                self._create_web_view_for_resource(resource)
            
            # Switch to this resource's embedded web view
            idx = self._get_web_view_index(resource['id'])
            if idx >= 0:
                self.web_view_stack.setCurrentIndex(idx)
                
                # If we have a cached search query, perform search for this resource
                if self.last_web_search_query:
                    url = self._build_web_search_url(resource, self.last_web_search_query)
                    if url:
                        self.web_views[resource['id']].setUrl(QUrl(url))
        else:
            # External mode - show info
            import html as _html
            self.web_results_view.setHtml(f"""
                <div style="text-align: center; padding: 40px;">
                    <h2>{resource['icon']} {_html.escape(resource['name'])}</h2>
                    <p style="color: #666;">{_html.escape(resource['description'])}</p>
                    <p style="margin-top: 20px;">Enter a search term above and press Search.</p>
                    <p style="font-size: 9pt; color: #aaa;">Results will open in your default browser.</p>
                </div>
            """)
        
        # Update language info
        self._update_web_lang_info()
    
    def _update_web_lang_info(self):
        """Update the language direction info label for web resources"""
        from_lang = self.lang_from_combo.currentData() if hasattr(self, 'lang_from_combo') else None
        to_lang = self.lang_to_combo.currentData() if hasattr(self, 'lang_to_combo') else None
        
        from_display = self.lang_from_combo.currentText() if from_lang else "Any"
        to_display = self.lang_to_combo.currentText() if to_lang else "Any"
        
        if hasattr(self, 'web_lang_info_label'):
            self.web_lang_info_label.setText(f"Languages: {from_display} → {to_display}")
    
    def _schedule_lazy_web_views(self, query: str, resources, idx: int):
        """Defer-create / setUrl one QWebEngineView per event-loop tick.

        Recursively schedules itself for the next index until all resources
        are processed. Yielding to the event loop between each web-view
        creation keeps the main thread responsive and stops Windows DWM
        from ghosting the Sidekick window during the lookup.

        Cancellation: if a newer search has fired (self._web_search_query
        differs from the query this chain was started with), abort.
        """
        if getattr(self, '_web_search_query', None) != query:
            return
        if idx >= len(resources):
            return
        resource = resources[idx]
        try:
            url = self._build_web_search_url(resource, query)
            if url:
                if resource['id'] not in self.web_views:
                    self._create_web_view_for_resource(resource)
                if resource['id'] in self.web_views:
                    self.web_views[resource['id']].setUrl(QUrl(url))
        except Exception as e:
            print(f"[SuperLookup] Lazy web view error for {resource.get('name', '?')}: {e}")
        QTimer.singleShot(0, lambda: self._schedule_lazy_web_views(query, resources, idx + 1))

    def _perform_web_search(self, search_all: bool = False, silent: bool = False):
        """Perform web search with the selected resource(s)

        Args:
            search_all: If True, search all enabled resources (for embedded mode)
            silent: If True, do NOT touch self.status_label. Used when called from
                perform_lookup, where the central status line is owned by the
                background search worker (so its "✓ Found N results" message
                isn't clobbered by a "Searching all web resources for X" set
                from this synchronous code path).
        """
        # Use the main Superlookup source text field
        query = self.source_text.currentText().strip()
        if not query:
            if not silent:
                self.status_label.setText(self.tr("Please enter a search term in the Source Text field above"))
            return
        
        # Cache the query for when user switches between resources
        self.last_web_search_query = query
        
        if self.web_browser_mode == 'embedded' and self.web_engine_available:
            # Embedded mode - load in web views
            if search_all:
                # IMPORTANT: each QWebEngineView creation is 100-500 ms on Windows.
                # Doing 16+ of them in a synchronous loop blocks the main thread
                # for 3-8 s, which exceeds Windows DWM's "Not Responding"
                # threshold (~5 s). DWM then ghosts the Sidekick window – it
                # loses Z-order and apps like Trados Studio (aggressive about
                # foreground reclaim) immediately pop in front, making Sidekick
                # appear to "disappear" while the search is running.
                #
                # Mitigation: do the currently-visible resource synchronously
                # (so the user sees results in the active sub-tab right away),
                # then schedule the rest one-per-event-loop-tick via QTimer.
                # Total wall-clock is similar but the main thread keeps yielding
                # to the event loop, so the UI stays responsive and DWM doesn't
                # mark the app as Not Responding.
                self._web_search_query = query  # used by deferred ticks for cancellation

                current_idx = self.current_web_resource_index
                current_resource = (self.web_resources[current_idx]
                                    if 0 <= current_idx < len(self.web_resources)
                                    else None)

                # 1. Currently-visible resource: create + setUrl immediately so
                #    the active sub-tab has results without delay.
                if current_resource:
                    try:
                        url = self._build_web_search_url(current_resource, query)
                        if url:
                            if current_resource['id'] not in self.web_views:
                                self._create_web_view_for_resource(current_resource)
                            if current_resource['id'] in self.web_views:
                                self.web_views[current_resource['id']].setUrl(QUrl(url))
                                view_idx = self._get_web_view_index(current_resource['id'])
                                if view_idx >= 0:
                                    self.web_view_stack.setCurrentIndex(view_idx)
                    except Exception as e:
                        print(f"[SuperLookup] Web search (current resource) error: {e}")

                # 2. Other resources: schedule lazily, one per event-loop tick.
                other_resources = [r for i, r in enumerate(self.web_resources) if i != current_idx]
                if other_resources:
                    QTimer.singleShot(0, lambda: self._schedule_lazy_web_views(query, other_resources, 0))

                if not silent:
                    self.status_label.setText(f"Searching all web resources for '{query}'")
            else:
                # Search current resource only
                resource = self.web_resources[self.current_web_resource_index]
                url = self._build_web_search_url(resource, query)
                if url:
                    self.last_web_search_url = url
                    # Create view lazily if needed
                    if resource['id'] not in self.web_views:
                        self._create_web_view_for_resource(resource)
                    if resource['id'] in self.web_views:
                        self.web_views[resource['id']].setUrl(QUrl(url))
                        # Show this resource's view
                        idx = self._get_web_view_index(resource['id'])
                        if idx >= 0:
                            self.web_view_stack.setCurrentIndex(idx)
                    if not silent:
                        self.status_label.setText(f"Loaded {resource['name']} search")
        else:
            # External mode - open in browser
            resource = self.web_resources[self.current_web_resource_index]
            url = self._build_web_search_url(resource, query)
            
            if url:
                self.last_web_search_url = url
                
                # Show the URL that will be opened. Escaped: the search
                # term is whatever was typed or captured, and a "<" in it
                # would otherwise be read as markup.
                import html as _html
                self.web_results_view.setHtml(f"""
                    <div style="padding: 20px;">
                        <h3>{_html.escape(resource['icon'])} {_html.escape(resource['name'])}</h3>
                        <p><b>Search term:</b> {_html.escape(query)}</p>
                        <p><b>Opening URL:</b></p>
                        <p style="word-wrap: break-word; color: #1976D2;">
                            <a href="{_html.escape(url)}">{_html.escape(url)}</a>
                        </p>
                        <hr style="margin: 20px 0; border: none; border-top: 1px solid #ddd;">
                        <p style="color: #666; font-size: 10pt;">
                            Click "Open in Browser" to open again, or select another resource.
                        </p>
                    </div>
                """)
                
                # Open in default browser
                QDesktopServices.openUrl(QUrl(url))
                if not silent:
                    self.status_label.setText(f"Opened {resource['name']} search in browser")
    
    def search_all_web_resources(self, query: str):
        """Search all web resources with the given query (called from Superlookup integration)
        
        Note: This method now uses the source_text field directly, so the query param
        should match what's already in source_text.
        """
        if query:
            # Source text should already be set, just trigger the search
            self._perform_web_search(search_all=True)
    
    def _build_web_search_url(self, resource, query):
        """Build the search URL for a web resource with proper language codes"""
        import urllib.parse

        # Get language settings (may be a list of variants)
        from_lang = self.lang_from_combo.currentData() if hasattr(self, 'lang_from_combo') else None
        to_lang = self.lang_to_combo.currentData() if hasattr(self, 'lang_to_combo') else None

        # If we got a list of variants, use the first one for web URLs
        if isinstance(from_lang, list) and from_lang:
            from_lang = from_lang[0]
        if isinstance(to_lang, list) and to_lang:
            to_lang = to_lang[0]

        # If the From/To combos are set to "Any" (or not yet populated), fall
        # back to the main window's project language pair – the same pair
        # QuickTrans uses. This is what the user configured as their default
        # in Settings → Language Pair (e.g. Dutch → English), so resources
        # like IATE / Linguee / ProZ / BabelNet fire with the right codes
        # instead of the hard-coded English → Dutch fallback.
        if not from_lang or not to_lang:
            mw = self.main_window
            if mw is not None:
                # Prefer current_project pair if a project is loaded; otherwise
                # use the app-wide default.
                proj = getattr(mw, 'current_project', None)
                if proj is not None:
                    if not from_lang:
                        from_lang = getattr(proj, 'source_lang', None) or getattr(mw, 'source_language', None)
                    if not to_lang:
                        to_lang = getattr(proj, 'target_lang', None) or getattr(mw, 'target_language', None)
                else:
                    if not from_lang:
                        from_lang = getattr(mw, 'source_language', None)
                    if not to_lang:
                        to_lang = getattr(mw, 'target_language', None)

        # Final safety net – if everything above failed, default to English-Dutch
        if not from_lang:
            from_lang = 'en'
        if not to_lang:
            to_lang = 'nl'
        
        # URL-encode the query
        encoded_query = urllib.parse.quote(query)
        
        # Get language codes in the format needed by this resource
        lang_format = resource.get('lang_format')
        sl = self._get_web_lang_code(from_lang, lang_format)
        tl = self._get_web_lang_code(to_lang, lang_format)
        sl_full = self._get_web_lang_code(from_lang, 'full_lower')
        tl_full = self._get_web_lang_code(to_lang, 'full_lower')
        sl_upper = self._get_web_lang_code(from_lang, 'iso2').upper()
        tl_upper = self._get_web_lang_code(to_lang, 'iso2').upper()

        # Linguee uses a canonical language-pair slug: English is always
        # first in any English↔X pair (e.g. /english-dutch/, never
        # /dutch-english/), and source=auto handles the query direction.
        # Non-canonical URLs redirect/error – particularly badly inside the
        # embedded QWebEngineView. Swap sl_full/tl_full for this resource
        # when one side is English and it isn't already first. For non-
        # English pairs (e.g. French↔German), fall back to alphabetical
        # ordering – this mirrors Linguee's actual slug scheme.
        if resource.get('id') == 'linguee' and sl_full and tl_full:
            if 'english' in (sl_full, tl_full):
                if sl_full != 'english':
                    sl_full, tl_full = tl_full, sl_full
            elif sl_full > tl_full:
                sl_full, tl_full = tl_full, sl_full
        
        # Build URL from template
        url = resource['url_template']
        url = url.replace('{query}', encoded_query)
        url = url.replace('{sl}', sl)
        url = url.replace('{tl}', tl)
        url = url.replace('{sl_full}', sl_full)
        url = url.replace('{tl_full}', tl_full)
        url = url.replace('{sl_upper}', sl_upper)
        url = url.replace('{tl_upper}', tl_upper)
        
        # Handle OPUS corpus placeholder
        if '{opus_corpus}' in url and hasattr(self, 'current_opus_corpus'):
            url = url.replace('{opus_corpus}', self.current_opus_corpus)
        
        return url
    
    def _on_opus_corpus_changed(self, index):
        """Handle OPUS corpus selection change"""
        if hasattr(self, 'opus_corpus_combo'):
            self.current_opus_corpus = self.opus_corpus_combo.currentData()
            # If we have a search query, refresh the OPUS view
            if self.last_web_search_query and hasattr(self, 'web_views'):
                resource = self.web_resources[self.current_web_resource_index]
                if resource.get('has_corpus_selector') and resource['id'] in self.web_views:
                    url = self._build_web_search_url(resource, self.last_web_search_query)
                    self.web_views[resource['id']].setUrl(QUrl(url))
    
    def _get_web_lang_code(self, lang_code, format_type):
        """Convert language code to the format needed by different web services"""
        if not lang_code or not format_type:
            return ''
        
        # Normalize input - handle full language names or codes
        lang_lower = lang_code.lower()
        
        # Language code mappings
        # ISO-2 codes (2-letter): en, nl, de, fr, es, etc.
        iso2_map = {
            'english': 'en', 'en': 'en', 'en-us': 'en', 'en-gb': 'en', 'en-au': 'en',
            'dutch': 'nl', 'nl': 'nl', 'nl-nl': 'nl', 'nl-be': 'nl', 'nederlands': 'nl',
            'german': 'de', 'de': 'de', 'de-de': 'de', 'de-at': 'de', 'de-ch': 'de', 'deutsch': 'de',
            'french': 'fr', 'fr': 'fr', 'fr-fr': 'fr', 'fr-be': 'fr', 'fr-ca': 'fr', 'français': 'fr',
            'spanish': 'es', 'es': 'es', 'es-es': 'es', 'es-mx': 'es', 'español': 'es',
            'italian': 'it', 'it': 'it', 'it-it': 'it', 'italiano': 'it',
            'portuguese': 'pt', 'pt': 'pt', 'pt-pt': 'pt', 'pt-br': 'pt', 'português': 'pt',
            'polish': 'pl', 'pl': 'pl', 'polski': 'pl',
            'russian': 'ru', 'ru': 'ru', 'русский': 'ru',
            'chinese': 'zh', 'zh': 'zh', 'zh-cn': 'zh', 'zh-tw': 'zh', '中文': 'zh',
            'japanese': 'ja', 'ja': 'ja', '日本語': 'ja',
            'korean': 'ko', 'ko': 'ko', '한국어': 'ko',
            'arabic': 'ar', 'ar': 'ar', 'العربية': 'ar',
            'swedish': 'sv', 'sv': 'sv', 'svenska': 'sv',
            'danish': 'da', 'da': 'da', 'dansk': 'da',
            'norwegian': 'no', 'no': 'no', 'nb': 'no', 'nn': 'no', 'norsk': 'no',
            'finnish': 'fi', 'fi': 'fi', 'suomi': 'fi',
            'czech': 'cs', 'cs': 'cs', 'čeština': 'cs',
            'hungarian': 'hu', 'hu': 'hu', 'magyar': 'hu',
            'romanian': 'ro', 'ro': 'ro', 'română': 'ro',
            'greek': 'el', 'el': 'el', 'ελληνικά': 'el',
            'turkish': 'tr', 'tr': 'tr', 'türkçe': 'tr',
            'ukrainian': 'uk', 'uk': 'uk', 'українська': 'uk',
            'bulgarian': 'bg', 'bg': 'bg', 'български': 'bg',
        }
        
        # ISO-3 codes (3-letter for ProZ): dut, eng, ger, fre, spa, etc. (ISO 639-2/B bibliographic)
        iso3_map = {
            'english': 'eng', 'en': 'eng', 'en-us': 'eng', 'en-gb': 'eng',
            'dutch': 'dut', 'nl': 'dut', 'nl-nl': 'dut', 'nl-be': 'dut', 'nederlands': 'dut',
            'german': 'ger', 'de': 'ger', 'de-de': 'ger', 'deutsch': 'ger',
            'french': 'fre', 'fr': 'fre', 'fr-fr': 'fre', 'français': 'fre',
            'spanish': 'spa', 'es': 'spa', 'es-es': 'spa', 'español': 'spa',
            'italian': 'ita', 'it': 'ita', 'it-it': 'ita', 'italiano': 'ita',
            'portuguese': 'por', 'pt': 'por', 'pt-pt': 'por', 'português': 'por',
            'polish': 'pol', 'pl': 'pol', 'polski': 'pol',
            'russian': 'rus', 'ru': 'rus', 'русский': 'rus',
            'chinese': 'chi', 'zh': 'chi', 'zh-cn': 'chi', '中文': 'chi',
            'japanese': 'jpn', 'ja': 'jpn', '日本語': 'jpn',
            'korean': 'kor', 'ko': 'kor', '한국어': 'kor',
            'arabic': 'ara', 'ar': 'ara', 'العربية': 'ara',
            'swedish': 'swe', 'sv': 'swe', 'svenska': 'swe',
            'danish': 'dan', 'da': 'dan', 'dansk': 'dan',
            'norwegian': 'nor', 'no': 'nor', 'norsk': 'nor',
            'finnish': 'fin', 'fi': 'fin', 'suomi': 'fin',
            'czech': 'cze', 'cs': 'cze', 'čeština': 'cze',
            'hungarian': 'hun', 'hu': 'hun', 'magyar': 'hun',
            'romanian': 'rum', 'ro': 'rum', 'română': 'rum',
            'greek': 'gre', 'el': 'gre', 'ελληνικά': 'gre',
            'turkish': 'tur', 'tr': 'tur', 'türkçe': 'tur',
            'ukrainian': 'ukr', 'uk': 'ukr', 'українська': 'ukr',
            'bulgarian': 'bul', 'bg': 'bul', 'български': 'bul',
        }
        
        # ISO 639-3 codes (for Juremy): nld, eng, deu, fra, spa, etc.
        iso639_3_map = {
            'english': 'eng', 'en': 'eng', 'en-us': 'eng', 'en-gb': 'eng',
            'dutch': 'nld', 'nl': 'nld', 'nl-nl': 'nld', 'nl-be': 'nld', 'nederlands': 'nld',
            'german': 'deu', 'de': 'deu', 'de-de': 'deu', 'deutsch': 'deu',
            'french': 'fra', 'fr': 'fra', 'fr-fr': 'fra', 'français': 'fra',
            'spanish': 'spa', 'es': 'spa', 'es-es': 'spa', 'español': 'spa',
            'italian': 'ita', 'it': 'ita', 'it-it': 'ita', 'italiano': 'ita',
            'portuguese': 'por', 'pt': 'por', 'pt-pt': 'por', 'português': 'por',
            'polish': 'pol', 'pl': 'pol', 'polski': 'pol',
            'russian': 'rus', 'ru': 'rus', 'русский': 'rus',
        }
        
        # Full language names (lowercase for URL slugs)
        full_lower_map = {
            'english': 'english', 'en': 'english', 'en-us': 'english', 'en-gb': 'english',
            'dutch': 'dutch', 'nl': 'dutch', 'nl-nl': 'dutch', 'nl-be': 'dutch', 'nederlands': 'dutch',
            'german': 'german', 'de': 'german', 'de-de': 'german', 'deutsch': 'german',
            'french': 'french', 'fr': 'french', 'fr-fr': 'french', 'français': 'french',
            'spanish': 'spanish', 'es': 'spanish', 'es-es': 'spanish', 'español': 'spanish',
            'italian': 'italian', 'it': 'italian', 'it-it': 'italian', 'italiano': 'italian',
            'portuguese': 'portuguese', 'pt': 'portuguese', 'pt-pt': 'portuguese', 'português': 'portuguese',
            'polish': 'polish', 'pl': 'polish', 'polski': 'polish',
            'russian': 'russian', 'ru': 'russian', 'русский': 'russian',
            'chinese': 'chinese', 'zh': 'chinese', 'zh-cn': 'chinese', '中文': 'chinese',
            'japanese': 'japanese', 'ja': 'japanese', '日本語': 'japanese',
            'korean': 'korean', 'ko': 'korean', '한국어': 'korean',
            'arabic': 'arabic', 'ar': 'arabic', 'العربية': 'arabic',
            'swedish': 'swedish', 'sv': 'swedish', 'svenska': 'swedish',
            'danish': 'danish', 'da': 'danish', 'dansk': 'danish',
            'norwegian': 'norwegian', 'no': 'norwegian', 'norsk': 'norwegian',
            'finnish': 'finnish', 'fi': 'finnish', 'suomi': 'finnish',
            'czech': 'czech', 'cs': 'czech', 'čeština': 'czech',
            'hungarian': 'hungarian', 'hu': 'hungarian', 'magyar': 'hungarian',
            'romanian': 'romanian', 'ro': 'romanian', 'română': 'romanian',
            'greek': 'greek', 'el': 'greek', 'ελληνικά': 'greek',
            'turkish': 'turkish', 'tr': 'turkish', 'türkçe': 'turkish',
            'ukrainian': 'ukrainian', 'uk': 'ukrainian', 'українська': 'ukrainian',
            'bulgarian': 'bulgarian', 'bg': 'bulgarian', 'български': 'bulgarian',
        }
        
        # Select the appropriate mapping
        if format_type == 'iso2':
            return iso2_map.get(lang_lower, lang_lower[:2] if len(lang_lower) >= 2 else 'en')
        elif format_type == 'iso3':
            return iso3_map.get(lang_lower, 'eng')
        elif format_type == 'iso639_3':
            return iso639_3_map.get(lang_lower, 'eng')
        elif format_type == 'full_lower':
            return full_lower_map.get(lang_lower, 'english')
        else:
            return lang_lower
    
    def _open_web_resource_external(self):
        """Open the last search URL in the default browser"""
        if self.last_web_search_url:
            QDesktopServices.openUrl(QUrl(self.last_web_search_url))
        else:
            # If no search has been performed, build URL with current input from source_text
            query = self.source_text.currentText().strip()
            if query:
                self._perform_web_search()
            else:
                self.status_label.setText(self.tr("Enter a search term in the Source Text field first"))
    
    # Setting key + valid values for the Ctrl+Alt+L landing sub-tab.
    SUPERLOOKUP_LANDING_TAB_KEY = 'superlookup_landing_tab'
    SUPERLOOKUP_LANDING_TAB_DEFAULT = 'termbases'
    SUPERLOOKUP_LANDING_TAB_LABELS = {
        'quicktrans': 'QuickTrans',
        'tms': 'TMs',
        'termbases': 'Termbases',
        'webresources': 'Web Resources',
    }

    def create_settings_tab(self):
        """Create the Settings tab with sub-tabs for each resource type"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)

        # Header
        header = QLabel(self.tr("⚙️ Search Resources Configuration"))
        header.setStyleSheet("font-size: 14pt; font-weight: bold; color: #1976D2;")
        layout.addWidget(header, 0)

        # Description
        desc = QLabel(
            self.tr("Configure which resources to search. Each resource type has its own tab below.")
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #666; padding: 5px 0;")
        layout.addWidget(desc, 0)

        # Ctrl+Alt+L landing-tab preference. Lives above the sub-sub-tabs so
        # it's visible regardless of which resource sub-tab is selected.
        # We use a QFrame + QLabel rather than a QGroupBox to dodge the
        # theme_manager's QGroupBox styling, and CheckmarkRadioButton rather
        # than plain QRadioButton because the theme strips QRadioButton's
        # native checked-state indicator (see theme_manager.py:475-486).
        landing_frame = QFrame()
        landing_frame.setFrameShape(QFrame.Shape.NoFrame)
        landing_outer = QVBoxLayout(landing_frame)
        landing_outer.setContentsMargins(0, 5, 0, 5)
        landing_outer.setSpacing(2)

        landing_label = QLabel(self.tr("Ctrl+Alt+L lands on:"))
        landing_label.setStyleSheet("font-weight: bold;")
        landing_outer.addWidget(landing_label)

        landing_row = QHBoxLayout()
        landing_row.setContentsMargins(0, 0, 0, 0)
        landing_row.setSpacing(15)

        self._landing_tab_buttons = {}
        self._landing_tab_button_group = QButtonGroup(landing_frame)
        self._landing_tab_button_group.setExclusive(True)
        current_pref = self._load_superlookup_landing_pref()
        for value, label in self.SUPERLOOKUP_LANDING_TAB_LABELS.items():
            rb = CheckmarkRadioButton(label)
            rb.setChecked(value == current_pref)
            rb.toggled.connect(
                lambda checked, v=value: self._on_landing_pref_changed(v, checked)
            )
            self._landing_tab_buttons[value] = rb
            self._landing_tab_button_group.addButton(rb)
            landing_row.addWidget(rb)
        landing_row.addStretch()
        landing_outer.addLayout(landing_row)
        layout.addWidget(landing_frame, 0)

        # v1.10.168: Removed the per-SuperLookup Termbase + TM checkbox
        # sub-tabs. Selection now lives in one place per resource: the
        # 🔍 SuperLookup column of the main TMs and Termbases tabs (since
        # v1.10.247 independent of Read). A short note below explains
        # where users should go.
        resource_info = QLabel(
            "<b>Translation Memories &amp; Termbases:</b> SuperLookup searches every TM and "
            "termbase ticked in the <b>🔍 SuperLookup</b> column of the main <b>TMs</b> and "
            "<b>Termbases</b> tabs – whether or not it is also ticked <b>Read</b> for the "
            "current project. To include or exclude a resource from SuperLookup, tick or "
            "untick it in that column."
        )
        resource_info.setWordWrap(True)
        resource_info.setTextFormat(Qt.TextFormat.RichText)
        resource_info.setStyleSheet(
            "color: #444; padding: 12px; background-color: #FFF8E1; "
            "border: 1px solid #FFE082; border-radius: 4px;"
        )
        layout.addWidget(resource_info, 0)

        # Create sub-tabs for each resource type
        self.settings_subtabs = QTabWidget()
        self.settings_subtabs.tabBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.settings_subtabs.tabBar().setDrawBase(False)
        self.settings_subtabs.tabBar().setExpanding(False)

        # Note: MT Settings removed - now in main Settings → MT Settings

        # Web Settings sub-tab
        web_settings_tab = self.create_web_settings_subtab()
        self.settings_subtabs.addTab(web_settings_tab, "🌐 Web Resources")

        # QuickTrans Settings used to be mounted here as a sub-sub-tab
        # when this SuperlookupTab lived inside Sidekick (is_sidekick=
        # True). With Sidekick retired in v1.10.4 / v1.10.10, the
        # QuickTrans settings widget moved to Workbench Settings →
        # ⚡ QuickTrans where it's the sole, canonical mount point.
        # Keeping it out of the SuperLookup tab also keeps
        # _mtql_checkboxes / _mtql_llm_combos on the main window
        # unambiguous (single source of truth).

        layout.addWidget(self.settings_subtabs, stretch=1)

        return tab

    def _load_superlookup_landing_pref(self) -> str:
        """Read the user's preferred Ctrl+Alt+L landing sub-tab from settings."""
        try:
            mw = self.main_window
            if mw and hasattr(mw, 'load_general_settings'):
                settings = mw.load_general_settings() or {}
                value = settings.get(self.SUPERLOOKUP_LANDING_TAB_KEY)
                if value in self.SUPERLOOKUP_LANDING_TAB_LABELS:
                    return value
        except Exception as e:
            print(f"[Superlookup] Could not load landing tab pref: {e}")
        return self.SUPERLOOKUP_LANDING_TAB_DEFAULT

    def _on_landing_pref_changed(self, value: str, checked: bool):
        """Persist the new Ctrl+Alt+L landing sub-tab preference."""
        if not checked:
            return  # only react to the newly-selected button
        try:
            mw = self.main_window
            if mw and hasattr(mw, 'load_general_settings') and hasattr(mw, 'save_general_settings'):
                settings = mw.load_general_settings() or {}
                settings[self.SUPERLOOKUP_LANDING_TAB_KEY] = value
                mw.save_general_settings(settings)
        except Exception as e:
            print(f"[Superlookup] Could not save landing tab pref: {e}")

    # v1.10.168: create_tm_settings_subtab / create_termbase_settings_subtab
    # were removed. Selection now lives on the main TMs / Termbases tabs
    # (Read column). The SuperLookup Settings tab still exists (Web
    # Resources sub-tab + Ctrl+Alt+L landing preference), but no longer
    # hosts a per-resource checkbox list.
    
    def create_mt_settings_subtab(self):
        """Create MT settings sub-tab"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        
        # Info section
        info = QLabel(
            "Machine Translation integration is coming soon.\n"
            "This will allow SuperLookup to query DeepL, Google Translate, and other MT services."
        )
        info.setWordWrap(True)
        info.setStyleSheet("padding: 5px; border-radius: 3px;")
        layout.addWidget(info, 0)
        
        # Enable checkbox (disabled for now)
        self.mt_search_checkbox = CheckmarkCheckBox(self.tr("✓ Enable Machine Translation (Coming Soon)"))
        self.mt_search_checkbox.setChecked(False)
        self.mt_search_checkbox.setEnabled(False)
        self.mt_search_checkbox.setStyleSheet("font-weight: bold; font-size: 11pt; color: #999; padding: 10px 0;")
        layout.addWidget(self.mt_search_checkbox, 0)
        
        # Placeholder content
        placeholder = QLabel(self.tr("🚧 Under Construction\n\nComing soon:\n• DeepL integration\n• Google Translate\n• Microsoft Translator\n• Amazon Translate\n• ModernMT"))
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: #999; padding: 40px; font-size: 11pt;")
        layout.addWidget(placeholder, stretch=1)
        
        return tab
    
    def create_web_settings_subtab(self):
        """Create Web Resources settings sub-tab"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        
        # Info section
        info = QLabel(
            "All web resources appear in the Web Resources tab by default. "
            "Uncheck any you want to hide. "
            "Uncheck every resource to disable web search entirely."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #666; padding: 5px; background-color: #E8F5E9; border-radius: 3px;")
        layout.addWidget(info, 0)

        # Available resources list
        resources_label = QLabel(self.tr("Available Web Resources:"))
        resources_label.setStyleSheet("font-weight: bold; padding-top: 10px;")
        layout.addWidget(resources_label, 0)
        
        # Scroll area for resource checkboxes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.StyledPanel)
        
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(10, 10, 10, 10)
        scroll_layout.setSpacing(5)
        
        # Add checkboxes for each resource (all enabled by default)
        self.web_resource_checkboxes = []
        default_resources = [
            ('🇪🇺 IATE', 'EU terminology database - supports language direction'),
            ('📗 Linguee', 'Bilingual dictionary with context sentences'),
            ('💬 ProZ.com', 'Translator terminology database - supports language direction'),
            ('🔄 Reverso Context', 'Context-based translations with examples'),
            ('🔍 Google Search', 'General web search'),
            ('📜 Google Patents', 'Search patents for terminology'),
            ('📖 Wikipedia (Source)', 'Wikipedia in source language'),
            ('📖 Wikipedia (Target)', 'Wikipedia in target language'),
            ('⚖️ Juremy', 'Legal terminology database'),
            ('📚 Beijer Dictionary', 'Dutch-English terminology database (1.9M+ entries)'),
            ('🔤 AcronymFinder', 'Acronym and abbreviation dictionary'),
            ('🌐 BabelNet', 'Multilingual encyclopedic dictionary'),
            ('📓 Wiktionary (Source)', 'Wiktionary in source language'),
            ('📓 Wiktionary (Target)', 'Wiktionary in target language'),
        ]
        
        for i, (name, desc) in enumerate(default_resources):
            cb = CheckmarkCheckBox(name)
            cb.setChecked(True)
            cb.setToolTip(desc)
            # Connect to update sidebar visibility when checkbox changes
            cb.stateChanged.connect(lambda state, idx=i: self._on_web_resource_checkbox_changed(idx, state))
            scroll_layout.addWidget(cb)
            self.web_resource_checkboxes.append(cb)
        
        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll, stretch=1)
        
        # Future: Add custom URL section
        future_label = QLabel(self.tr("💡 Tip: More web resources can be added in future updates"))
        future_label.setStyleSheet("color: #666; font-size: 9pt; font-style: italic; padding: 5px 0;")
        layout.addWidget(future_label, 0)
        
        return tab
    
    def on_results_tab_changed(self, index):
        """Handle results tab change.

        Lazy-activation: each sub-tab's work is fired here when (and only
        when) the user navigates to it.

        - Settings tab: refresh resource lists.
        - Web Resources tab: fire any pending web search (deferred from
          perform_lookup so we don't pay the QWebEngineView creation cost
          when the user never opens this tab).
        - QuickTrans tab: fire any pending MT fan-out (deferred so we don't
          spawn N provider HTTP calls when the user never opens this tab,
          and don't momentarily create the hidden WindowStaysOnTopHint
          dialog that the MTQuickPopup constructor would otherwise create —
          which disturbs Z-order and pushes Sidekick behind Trados).

        Look up tabs dynamically by title so this keeps working if more
        tabs are added / removed later.
        """
        settings_index = -1
        web_index = -1
        quicktrans_index = -1
        for i in range(self.results_tabs.count()):
            label = self.results_tabs.tabText(i)
            if settings_index < 0 and "Settings" in label:
                settings_index = i
            if web_index < 0 and "Web Resources" in label:
                web_index = i
            if quicktrans_index < 0 and "QuickTrans" in label:
                quicktrans_index = i

        if index == settings_index and settings_index >= 0:
            # v1.10.168: per-SuperLookup TM + Termbase checkbox lists
            # were removed; selection now mirrors the main TMs / Termbases
            # tabs' Read flags. Just refresh language dropdowns here.
            print("[Superlookup] Settings tab viewed - refreshing language dropdowns")
            self.populate_language_dropdowns()

        if index == web_index and web_index >= 0:
            if getattr(self, '_web_search_pending', False):
                self._web_search_pending = False
                if hasattr(self, 'web_browser_mode') and self.web_browser_mode == 'embedded' \
                   and hasattr(self, 'web_engine_available') and self.web_engine_available:
                    try:
                        self._perform_web_search(search_all=True, silent=True)
                    except Exception as e:
                        print(f"[SuperLookup] Deferred web search error: {e}")
                elif hasattr(self, 'web_browser_mode') and self.web_browser_mode == 'external':
                    try:
                        self._perform_web_search(search_all=False, silent=True)
                    except Exception as e:
                        print(f"[SuperLookup] Deferred web search (external) error: {e}")

        # NOTE (v1.10.10): the QuickTrans-sub-tab deferred dispatch
        # path was removed when Sidekick was retired. It only fired
        # for SuperLookup widgets hosted inside Sidekick (is_sidekick
        # =True), which mounted QuickTrans as a sub-sub-tab. The
        # in-Workbench SuperLookup tab doesn't host QuickTrans
        # internally – QuickTrans now lives directly in Workbench
        # Settings → ⚡ QuickTrans – so there's nothing to defer.
    
    def on_tm_search_toggled(self, state):
        """Handle TM search checkbox toggle"""
        self.search_tm_enabled = (state == Qt.CheckState.Checked.value)
        print(f"[Superlookup] TM search {'enabled' if self.search_tm_enabled else 'disabled'}")
    
    def on_termbase_search_toggled(self, state):
        """Handle termbase search checkbox toggle"""
        self.search_termbase_enabled = (state == Qt.CheckState.Checked.value)
        print(f"[Superlookup] Termbase search {'enabled' if self.search_termbase_enabled else 'disabled'}")
    
    def _on_web_resource_checkbox_changed(self, index: int, state: int):
        """Handle web resource checkbox change - show/hide corresponding sidebar button"""
        is_checked = (state == Qt.CheckState.Checked.value)
        if hasattr(self, 'web_resource_buttons') and index < len(self.web_resource_buttons):
            self.web_resource_buttons[index].setVisible(is_checked)
            print(f"[Superlookup] Web resource {index} {'shown' if is_checked else 'hidden'}")
            
            # If the hidden resource was selected, select the first visible one
            if not is_checked and hasattr(self, 'current_web_resource_index') and self.current_web_resource_index == index:
                for i, btn in enumerate(self.web_resource_buttons):
                    if btn.isVisible():
                        self._on_web_resource_selected(i)
                        btn.setChecked(True)
                        break
    
    def refresh_tm_list(self):
        """v1.10.168: no-op shim. The per-SuperLookup TM checkbox list was
        removed; selection now lives on the main TMs tab's Read column.
        Kept as a stub so any stale caller (e.g. from older session state)
        doesn't AttributeError."""
        pass

    def refresh_termbase_list(self):
        """v1.10.168: no-op shim — see refresh_tm_list."""
        pass
    
    def get_selected_tm_ids(self):
        """Get list of TM IDs that SuperLookup should search.

        v1.10.247: gated on the dedicated **SuperLookup** column on the
        TMs tab (translation_memories.superlookup_enabled) — NOT the Read
        flag. v1.10.168 had tied SuperLookup inclusion to the Read flag
        (tm_activation.is_active), which meant you couldn't keep only a
        couple of TMs Read during a project while still having SuperLookup
        search all of them. The SuperLookup column is now the single,
        independent switch for SuperLookup inclusion (global per TM, no
        project context). Returns an empty list when no TM is enabled,
        which signals search code to skip the TM query entirely.
        """
        mw = self.main_window
        if mw is None or not hasattr(mw, 'tm_metadata_mgr') or mw.tm_metadata_mgr is None:
            return []

        selected_ids = []
        try:
            for tm in (mw.tm_metadata_mgr.get_all_tms() or []):
                slug = tm.get('tm_id')      # string id stored on translation_units rows
                # SuperLookup inclusion is the only gate here. Default True
                # (included) for rows whose flag predates the column.
                if not tm.get('superlookup_enabled', True):
                    continue
                # IMPORTANT: return the STRING slug, not the numeric registry
                # id. The TM search filters on translation_units.tm_id, which
                # holds the slug ('BEIJER', 'patents', …). Passing the numeric
                # id makes the `tm_id IN (...)` clause match nothing, so TM
                # search silently returned zero results for everyone.
                # No numeric-id fallback: a slug-less TM cannot match any
                # translation_units row, so skip it loudly rather than
                # silently poisoning the filter with a numeric id.
                if slug is not None:
                    selected_ids.append(slug)
                else:
                    print(f"[Superlookup] get_selected_tm_ids: SuperLookup-enabled TM "
                          f"id={tm.get('id')!r} has no string tm_id slug; skipping "
                          f"(cannot match TM rows).")
        except Exception as e:
            print(f"[Superlookup] get_selected_tm_ids: enumeration failed: {e}")
        return selected_ids
    
    def get_search_direction(self):
        """Get the current search direction setting.
        
        Always returns 'both' for maximum search coverage.
        Smart language swapping ensures results are always shown in correct order.
        """
        return 'both'
    
    def _on_language_changed(self):
        """Handle language dropdown change - update Web Resources info"""
        # Update the web resources language info label
        self._update_web_lang_info()
    
    def get_language_filters(self):
        """Get the current language filter settings.
        
        Returns:
            tuple: (from_lang, to_lang) - None means 'Any'
        """
        from_lang = None
        to_lang = None
        
        if hasattr(self, 'lang_from_combo'):
            from_lang = self.lang_from_combo.currentData()
        if hasattr(self, 'lang_to_combo'):
            to_lang = self.lang_to_combo.currentData()
        
        return (from_lang, to_lang)
    
    def swap_language_filters(self):
        """Swap the From and To language selections"""
        if hasattr(self, 'lang_from_combo') and hasattr(self, 'lang_to_combo'):
            from_idx = self.lang_from_combo.currentIndex()
            to_idx = self.lang_to_combo.currentIndex()
            self.lang_from_combo.setCurrentIndex(to_idx)
            self.lang_to_combo.setCurrentIndex(from_idx)
    
    def populate_language_dropdowns(self):
        """Populate language dropdowns with BASE languages only (not variants).
        
        Groups all variants (en, en-US, en-GB, English, etc.) into a single "English" entry.
        When searching, all variants will be matched.
        """
        all_languages = set()  # All language codes/names found in database
        
        # Ensure we have db_manager from main window
        db_manager = self.db_manager
        if not db_manager and self.main_window and hasattr(self.main_window, 'db_manager'):
            db_manager = self.main_window.db_manager
        
        # Ensure we have termbase_mgr from main window
        termbase_mgr = self.termbase_mgr
        if not termbase_mgr and self.main_window and hasattr(self.main_window, 'termbase_mgr'):
            termbase_mgr = self.main_window.termbase_mgr
        
        # Get languages from TMs via database
        if db_manager:
            try:
                db_manager.cursor.execute("""
                    SELECT DISTINCT source_lang FROM translation_units WHERE source_lang IS NOT NULL AND source_lang != ''
                    UNION
                    SELECT DISTINCT target_lang FROM translation_units WHERE target_lang IS NOT NULL AND target_lang != ''
                """)
                for row in db_manager.cursor.fetchall():
                    if row[0]:
                        all_languages.add(row[0])
            except Exception:
                pass  # Silent failure for language population
        
        # Get languages from termbases
        if termbase_mgr:
            try:
                all_termbases = termbase_mgr.get_all_termbases()
                for tb in all_termbases:
                    if tb.get('source_lang'):
                        all_languages.add(tb['source_lang'])
                    if tb.get('target_lang'):
                        all_languages.add(tb['target_lang'])
            except Exception:
                pass  # Silent failure for language population
        
        # Group languages by their base language name
        # E.g., "en", "en-US", "en-GB", "English" all map to "English"
        base_lang_variants = {}  # base_name -> set of all variant codes
        
        for lang in all_languages:
            base_name = self._get_base_language_name(lang)
            if base_name not in base_lang_variants:
                base_lang_variants[base_name] = set()
            base_lang_variants[base_name].add(lang)
        
        # Store the mapping for use in searches
        self._language_variants_map = base_lang_variants
        
        # Sort base languages alphabetically
        sorted_base_langs = sorted(base_lang_variants.keys(), key=str.lower)
        
        # Clear existing items except "Any"
        while self.lang_from_combo.count() > 1:
            self.lang_from_combo.removeItem(1)
        while self.lang_to_combo.count() > 1:
            self.lang_to_combo.removeItem(1)
        
        # Add base languages only (store list of all variants as data)
        for base_name in sorted_base_langs:
            variants = list(base_lang_variants[base_name])
            # Store variants list as the data for this item
            self.lang_from_combo.addItem(base_name, variants)
            self.lang_to_combo.addItem(base_name, variants)

        # v1.10.173: auto-default the From / To dropdowns to the project's
        # language pair (if loaded). Previously they stayed on "Any" which
        # was surprising — users would change the language pair in
        # Settings and then expect SuperLookup to follow. Now it does.
        # Honour any existing user override: if the current dropdown
        # selection is already a non-"Any" base name AND that name exists
        # in the new list, leave it alone.
        try:
            mw = self.main_window
            if mw is not None:
                proj_src_base = self._get_base_language_name(
                    getattr(mw, 'source_language', None) or '')
                proj_tgt_base = self._get_base_language_name(
                    getattr(mw, 'target_language', None) or '')
                # Only auto-set when the dropdown is currently on "Any"
                # (index 0) — preserves any deliberate user override.
                if self.lang_from_combo.currentIndex() == 0 and proj_src_base:
                    idx = self.lang_from_combo.findText(proj_src_base)
                    if idx >= 0:
                        self.lang_from_combo.setCurrentIndex(idx)
                if self.lang_to_combo.currentIndex() == 0 and proj_tgt_base:
                    idx = self.lang_to_combo.findText(proj_tgt_base)
                    if idx >= 0:
                        self.lang_to_combo.setCurrentIndex(idx)
        except Exception as e:
            print(f"[Superlookup] populate_language_dropdowns: auto-default failed: {e}")

    def _get_base_language_name(self, lang_code):
        """Extract the base language name from any language code or name.
        
        Maps all variants to a single base name:
        - "en", "en-US", "en-GB", "English", "english" → "English"
        - "nl", "nl-NL", "nl-BE", "Dutch", "Nederlands" → "Dutch"
        """
        # Base language names mapping
        base_names = {
            'en': 'English', 'english': 'English',
            'nl': 'Dutch', 'dutch': 'Dutch', 'nederlands': 'Dutch',
            'de': 'German', 'german': 'German', 'deutsch': 'German',
            'fr': 'French', 'french': 'French', 'français': 'French', 'francais': 'French',
            'es': 'Spanish', 'spanish': 'Spanish', 'español': 'Spanish', 'espanol': 'Spanish',
            'it': 'Italian', 'italian': 'Italian', 'italiano': 'Italian',
            'pt': 'Portuguese', 'portuguese': 'Portuguese', 'português': 'Portuguese', 'portugues': 'Portuguese',
            'zh': 'Chinese', 'chinese': 'Chinese',
            'ja': 'Japanese', 'japanese': 'Japanese',
            'ko': 'Korean', 'korean': 'Korean',
            'ru': 'Russian', 'russian': 'Russian',
            'pl': 'Polish', 'polish': 'Polish', 'polski': 'Polish',
            'cs': 'Czech', 'czech': 'Czech',
            'da': 'Danish', 'danish': 'Danish', 'dansk': 'Danish',
            'sv': 'Swedish', 'swedish': 'Swedish', 'svenska': 'Swedish',
            'no': 'Norwegian', 'nb': 'Norwegian', 'nn': 'Norwegian', 'norwegian': 'Norwegian', 'norsk': 'Norwegian',
            'fi': 'Finnish', 'finnish': 'Finnish', 'suomi': 'Finnish',
            'ar': 'Arabic', 'arabic': 'Arabic',
            'he': 'Hebrew', 'hebrew': 'Hebrew',
            'tr': 'Turkish', 'turkish': 'Turkish',
            'el': 'Greek', 'greek': 'Greek',
            'hu': 'Hungarian', 'hungarian': 'Hungarian', 'magyar': 'Hungarian',
            'ro': 'Romanian', 'romanian': 'Romanian',
            'bg': 'Bulgarian', 'bulgarian': 'Bulgarian',
            'uk': 'Ukrainian', 'ukrainian': 'Ukrainian',
            'vi': 'Vietnamese', 'vietnamese': 'Vietnamese',
            'th': 'Thai', 'thai': 'Thai',
            'id': 'Indonesian', 'indonesian': 'Indonesian',
            'ms': 'Malay', 'malay': 'Malay',
            'sk': 'Slovak', 'slovak': 'Slovak',
            'sl': 'Slovenian', 'slovenian': 'Slovenian',
            'hr': 'Croatian', 'croatian': 'Croatian',
            'sr': 'Serbian', 'serbian': 'Serbian',
            'et': 'Estonian', 'estonian': 'Estonian',
            'lv': 'Latvian', 'latvian': 'Latvian',
            'lt': 'Lithuanian', 'lithuanian': 'Lithuanian',
            'ca': 'Catalan', 'catalan': 'Catalan',
            'ga': 'Irish', 'irish': 'Irish',
            'mt': 'Maltese', 'maltese': 'Maltese',
        }
        
        if not lang_code:
            return 'Unknown'
        
        # Normalize: lowercase for lookup
        lang_lower = lang_code.lower().strip()
        
        # Get base code (before hyphen or underscore)
        # Handles: en-US, en_GB, nl-NL, etc.
        base_code = lang_lower.split('-')[0].split('_')[0]
        
        # First try exact match
        if lang_lower in base_names:
            return base_names[lang_lower]
        
        # Then try base code
        if base_code in base_names:
            return base_names[base_code]
        
        # Unknown language - capitalize first letter
        return lang_code.capitalize() if lang_code else 'Unknown'
    
    def get_selected_termbase_ids(self):
        """Get list of termbase IDs that SuperLookup should search.

        v1.10.247: gated on the dedicated **SuperLookup** column on the
        Termbases tab (termbases.superlookup_enabled) — NOT the Read flag.
        See get_selected_tm_ids for the rationale: SuperLookup inclusion is
        now independent of Read, so you can keep just a few termbases Read
        during a project while SuperLookup still searches them all. Global
        per termbase, no project context.
        """
        mw = self.main_window
        if mw is None or not hasattr(mw, 'termbase_mgr') or mw.termbase_mgr is None:
            return []

        selected_ids = []
        try:
            for tb in (mw.termbase_mgr.get_all_termbases() or []):
                tb_id = tb.get('id')
                if tb_id is None:
                    continue
                # SuperLookup inclusion is the only gate. Default True
                # (included) for rows whose flag predates the column.
                if tb.get('superlookup_enabled', True):
                    selected_ids.append(tb_id)
        except Exception as e:
            print(f"[Superlookup] get_selected_termbase_ids: enumeration failed: {e}")
        return selected_ids
    
    # v1.10.168: check_all_tms / uncheck_all_tms / check_all_tbs /
    # uncheck_all_tbs were Select All / Clear All buttons on the now-
    # removed TM + Termbase sub-tabs of SuperLookup Settings. Removed
    # outright — the buttons that called them are gone. Bulk select /
    # clear of Read flags can be added to the main TMs / Termbases tabs
    # in a future iteration if users ask for it.
    
    def __del__(self):
        """Destructor - cleanup AHK process when widget is destroyed"""
        try:
            self.unregister_global_hotkey()
        except:
            pass
    
    def capture_text(self):
        """
        Capture text from the active application (triggered by global hotkey).
        """
        if not self.SuperlookupEngine:
            return
        
        # Initialize engine if needed
        if not self.engine:
            self.engine = self.SuperlookupEngine(mode='universal')
            
            # Set TM database if available
            if self.tm_database:
                self.engine.set_tm_database(self.tm_database)
        
        self.status_label.setText(self.tr("⏳ Capturing text..."))
        QApplication.processEvents()
        
        # Capture text
        text = self.engine.capture_text()
        
        if text:
            self.source_text.setCurrentText(text)
            self.status_label.setText(f"✓ Captured {len(text)} characters. Searching...")
            # Auto-search after capture
            self.perform_lookup()
        else:
            self.status_label.setText(self.tr("✗ No text captured. Try again."))
    
    def perform_lookup(self):
        """Perform lookup on the source text.

        TM and termbase searches run on a background QRunnable so the UI stays
        responsive even with very large termbases. Results stream back via
        signals (tm_ready / termbase_ready / finished). MT and web-search
        stay on the main thread for now — MT is fast (early-outs in Sidekick,
        parallel via ThreadPoolExecutor in main window), web-search is slow
        but its cost is QWebEngineView creation, not SQLite.
        """
        text = self.source_text.currentText().strip()

        if not text:
            self.status_label.setText(self.tr("⚠️ No text to search. Enter or capture text first."))
            return

        # Add to search history
        self._add_to_search_history(text)

        # Always ensure we have the latest databases from main window
        if self.main_window:
            if hasattr(self.main_window, 'tm_database') and self.main_window.tm_database:
                self.tm_database = self.main_window.tm_database
            if hasattr(self.main_window, 'termbase_mgr') and self.main_window.termbase_mgr:
                self.termbase_mgr = self.main_window.termbase_mgr
            if hasattr(self.main_window, 'db_manager') and self.main_window.db_manager:
                self.db_manager = self.main_window.db_manager

        # Populate language dropdowns if not yet populated (fallback)
        if not self._languages_populated and self.db_manager:
            self.populate_language_dropdowns()
            self._languages_populated = True

        if not self.engine:
            # Initialize engine
            self.engine = SuperlookupEngine(mode='universal')

        # Always update the engine with current tm_database
        if self.tm_database and self.engine:
            self.engine.set_tm_database(self.tm_database)

        self.status_label.setText(self.tr("🔍 Searching..."))

        # Cancel any in-flight search so its results don't overwrite ours when
        # they arrive late. Disconnect signals first so even if the worker
        # emits after we've moved on, the slots aren't called.
        prev = getattr(self, '_active_search_worker', None)
        if prev is not None:
            try:
                prev.cancel()
            except Exception:
                pass
            for sig in (prev.signals.tm_ready, prev.signals.termbase_ready,
                        prev.signals.finished, prev.signals.error):
                try:
                    sig.disconnect()
                except Exception:
                    pass

        # v1.10.168: TM + termbase selection both mirror the main TMs /
        # Termbases tabs' Read flag (tm_metadata.is_active /
        # termbase_activation.is_active). Compute BOTH lists here on the
        # main thread — calling them from the worker thread would touch
        # the main-thread sqlite cursor and trip Python's connection-
        # affinity check ("SQLite objects created in a thread can only
        # be used in that same thread").
        selected_tm_ids = self.get_selected_tm_ids()
        selected_tb_ids = self.get_selected_termbase_ids()
        search_direction = self.get_search_direction()
        from_lang, to_lang = self.get_language_filters()
        tm_search_disabled = not selected_tm_ids

        if self.engine:
            self.engine.set_enabled_tm_ids(selected_tm_ids if selected_tm_ids else None)

        # Clear panels immediately so the user doesn't see stale data while
        # the new search is in flight.
        try:
            self.display_tm_results([])
        except Exception:
            pass
        try:
            self.display_termbase_results([])
        except Exception:
            pass

        # Track the latest counts so _on_search_finished can update the status.
        self._search_in_flight_tm = []
        self._search_in_flight_termbase = []

        # Dispatch the heavy DB work to a worker thread.
        worker = _SuperLookupSearchWorker(
            tab=self,
            text=text,
            search_direction=search_direction,
            from_lang=from_lang,
            to_lang=to_lang,
            selected_tm_ids=selected_tm_ids,
            selected_tb_ids=selected_tb_ids,
            tm_search_disabled=tm_search_disabled,
        )
        worker.signals.tm_ready.connect(self._on_search_tm_ready)
        worker.signals.termbase_ready.connect(self._on_search_termbase_ready)
        worker.signals.finished.connect(self._on_search_finished)
        self._active_search_worker = worker
        QThreadPool.globalInstance().start(worker)

        # MT still runs synchronously on the main thread: _perform_mt_lookup
        # early-outs in the Sidekick context (no mt_results_table) and is
        # parallelised via ThreadPoolExecutor otherwise, so it doesn't block
        # significantly. Cheap compared to TM/termbase SQL.
        try:
            mt_results = self._perform_mt_lookup(text, from_lang, to_lang)
            self.display_mt_results(mt_results)
        except Exception as e:
            print(f"[SuperLookup] MT lookup error: {e}")

        # Web Resources – DEFERRED. We do NOT fire _perform_web_search here.
        # Creating 16+ QWebEngineViews per search is heavy (each spawns a
        # Chromium subprocess, touches OS-level windowing, and the cumulative
        # effect on slower machines disrupts Sidekick's Z-order — Trados
        # Studio aggressively reclaims foreground when our process is busy
        # creating subprocesses, and Sidekick ends up behind Trados even
        # though it was never hidden in Qt's view of the world).
        #
        # Instead, we just remember that a web search is pending. When (and
        # only when) the user actually navigates to the Web Resources sub-tab,
        # on_results_tab_changed fires the search. If the user never opens
        # the Web Resources tab — and most users using SuperLookup as a
        # termbase/TM lookup don't — we never pay the cost at all.
        any_web_checked = True
        if hasattr(self, 'web_resource_checkboxes') and self.web_resource_checkboxes:
            any_web_checked = any(cb.isChecked() for cb in self.web_resource_checkboxes)

        if hasattr(self, 'web_browser_mode') and any_web_checked:
            self._web_search_pending = True
            # If the user is already viewing the Web Resources sub-tab, fire
            # the web search now. on_results_tab_changed only fires the
            # deferred search on a tab *change*, so a re-search from the Web
            # Resources tab would otherwise leave the web views stuck on the
            # previous query.
            web_index = -1
            if hasattr(self, 'results_tabs'):
                for i in range(self.results_tabs.count()):
                    if "Web Resources" in self.results_tabs.tabText(i):
                        web_index = i
                        break
            if web_index >= 0 and self.results_tabs.currentIndex() == web_index:
                self._web_search_pending = False
                try:
                    if self.web_browser_mode == 'embedded' and getattr(self, 'web_engine_available', False):
                        self._perform_web_search(search_all=True, silent=True)
                    elif self.web_browser_mode == 'external':
                        self._perform_web_search(search_all=False, silent=True)
                except Exception as e:
                    print(f"[SuperLookup] Active-tab web search error: {e}")
        else:
            self._web_search_pending = False

    def _on_search_tm_ready(self, results):
        """Slot: TM concordance results have arrived from the worker."""
        self._search_in_flight_tm = results or []
        try:
            self.display_tm_results(self._search_in_flight_tm)
        except Exception as e:
            print(f"[SuperLookup] display_tm_results error: {e}")

    def _on_search_termbase_ready(self, results):
        """Slot: termbase results have arrived from the worker."""
        self._search_in_flight_termbase = results or []
        try:
            self.display_termbase_results(self._search_in_flight_termbase)
        except Exception as e:
            print(f"[SuperLookup] display_termbase_results error: {e}")

    def _on_search_finished(self):
        """Slot: worker done – update the status line with final counts."""
        tm_count = len(getattr(self, '_search_in_flight_tm', []) or [])
        tb_count = len(getattr(self, '_search_in_flight_termbase', []) or [])

        parts = []
        if tm_count:
            parts.append(f"TM: {tm_count}")
        if tb_count:
            parts.append(f"Termbase: {tb_count}")

        total = tm_count + tb_count
        if parts:
            self.status_label.setText(f"✓ Found {total} results ({', '.join(parts)})")
        else:
            self.status_label.setText(self.tr("No results found"))

        # Drop the worker reference so it can be garbage-collected.
        self._active_search_worker = None
    
    def search_with_query(self, query: str, switch_to_vertical: bool = True, 
                          source_lang: str = None, target_lang: str = None):
        """
        Public method to trigger a search from outside (e.g., from Ctrl+K).
        
        Args:
            query: Text to search for
            switch_to_vertical: If True, switches to vertical (list) view mode
            source_lang: Optional source language code to set
            target_lang: Optional target language code to set
        """
        # Set the query text (this is used by all tabs including Web Resources)
        self.source_text.setCurrentText(query)
        
        # Reset language dropdowns to "Any" (index 0) for unrestricted search by default
        # This ensures we always start with "From Any to Any"
        if hasattr(self, 'lang_from_combo'):
            self.lang_from_combo.setCurrentIndex(0)  # "Any"
        if hasattr(self, 'lang_to_combo'):
            self.lang_to_combo.setCurrentIndex(0)  # "Any"
        
        # Optionally switch to vertical view (traditional concordance layout)
        if switch_to_vertical and hasattr(self, 'tm_view_vertical_radio'):
            self.tm_view_vertical_radio.setChecked(True)
        
        # Switch to TM Matches tab
        if hasattr(self, 'results_tabs'):
            self.results_tabs.setCurrentIndex(0)  # TM Matches is first tab
        
        # Perform the lookup (TM, Termbase, MT)
        self.perform_lookup()
        
        # Pre-load all web resources in embedded mode
        if hasattr(self, 'web_browser_mode') and self.web_browser_mode == 'embedded':
            if hasattr(self, 'search_all_web_resources'):
                self.search_all_web_resources(query)
    
    def set_project_languages(self, source_lang: str = None, target_lang: str = None):
        """
        Set the language dropdowns to match the project's source and target languages.
        Called when a project is loaded to auto-configure Superlookup.
        
        Args:
            source_lang: Source language name or code (e.g., "Dutch", "nl")
            target_lang: Target language name or code (e.g., "English", "en")
        """
        # Make sure language dropdowns are populated first
        if not self._languages_populated:
            self.populate_language_dropdowns()
            self._languages_populated = True
        
        # Set the source language (From dropdown)
        if source_lang and hasattr(self, 'lang_from_combo'):
            self._set_language_combo(self.lang_from_combo, source_lang)
        
        # Set the target language (To dropdown)
        if target_lang and hasattr(self, 'lang_to_combo'):
            self._set_language_combo(self.lang_to_combo, target_lang)
        
        # Update web resources language info label if available
        if hasattr(self, '_on_language_changed'):
            self._on_language_changed()
    
    def _set_language_combo(self, combo, lang_code):
        """Set a language combo box to a specific language code"""
        if not combo or not lang_code:
            return
        
        # Try to find the language in the combo
        lang_lower = lang_code.lower()
        for i in range(combo.count()):
            item_data = combo.itemData(i)  # This is now a list of variants
            item_text = combo.itemText(i).lower()
            
            # Match by data (list of language variants) or text (display name)
            if item_data and isinstance(item_data, list):
                # Check if the lang_code matches any variant in the list
                if any(variant.lower() == lang_lower for variant in item_data):
                    combo.setCurrentIndex(i)
                    return
            elif item_data and isinstance(item_data, str) and item_data.lower() == lang_lower:
                # Legacy support for string data
                combo.setCurrentIndex(i)
                return
            if lang_lower in item_text:
                combo.setCurrentIndex(i)
                return
    
    def toggle_tm_view_mode(self, checked):
        """Toggle between horizontal (table) and vertical (list) view modes"""
        if self.tm_view_horizontal_radio.isChecked():
            self.tm_view_stack.setCurrentIndex(0)  # Horizontal/Table view
        else:
            self.tm_view_stack.setCurrentIndex(1)  # Vertical/List view
        
        # Re-display results if we have any stored
        if hasattr(self, '_current_tm_results') and self._current_tm_results:
            self.display_tm_results(self._current_tm_results)
    
    def display_tm_results(self, results):
        """Display TM results in both horizontal (table) and vertical (list) views"""
        # Store results for view toggling
        self._current_tm_results = results

        # v1.10.173: bracket the rebuild with sort-off so insertRow() +
        # setItem() don't trigger a live re-sort after every cell write
        # (which would shuffle row indices mid-loop and detach cell
        # widgets from their items). Sort state is restored at the end.
        _prev_tm_results_sorting = self.tm_results_table.isSortingEnabled()
        self.tm_results_table.setSortingEnabled(False)

        # Get search term for highlighting
        search_text = self.source_text.currentText().strip().lower()

        # Get language names from main window, normalised to base language
        # names (e.g. 'en-US' → 'English') and stripped of any "(variant)"
        # suffix so the column headers stay compact.
        source_lang_name = "Source"
        target_lang_name = "Target"
        if self.main_window:
            raw_src = getattr(self.main_window, 'source_language', None)
            raw_tgt = getattr(self.main_window, 'target_language', None)
            source_lang_name = self._lang_base_name(raw_src) or "Source"
            target_lang_name = self._lang_base_name(raw_tgt) or "Target"

        # Update column headers with the actual language names
        self.tm_results_table.setHorizontalHeaderLabels(
            [source_lang_name, target_lang_name, "TM"])

        # === Update Horizontal View (Table) ===
        self.tm_results_table.setRowCount(0)
        
        for result in results:
            row = self.tm_results_table.rowCount()
            self.tm_results_table.insertRow(row)

            # Source – QTextEdit cell widget with highlighting + text selection.
            # The underlying QTableWidgetItem carries the plain text purely as
            # the click-to-sort key (v1.10.173); the visible text comes from the
            # transparent _ReadOnlyHtmlCell overlaid via setCellWidget. On Qt
            # 6.9+ the view paints the item text *through* the transparent
            # overlay, producing a doubled / offset duplicate (issue #221) — so
            # we render the sort-key text invisible while keeping it sortable.
            source_item = QTableWidgetItem(result.source)
            source_item.setFlags(Qt.ItemFlag.NoItemFlags)
            source_item.setForeground(_TRANSPARENT_SORT_KEY)
            self.tm_results_table.setItem(row, 0, source_item)
            source_editor = _ReadOnlyHtmlCell(
                result.source, search_term=search_text,
                parent=self.tm_results_table, bold=False)
            self._apply_tm_cell_font(source_editor)
            self.tm_results_table.setCellWidget(row, 0, source_editor)

            # Target – same pattern (see note above re: #221 transparent sort key)
            target_item = QTableWidgetItem(result.target)
            target_item.setFlags(Qt.ItemFlag.NoItemFlags)
            target_item.setForeground(_TRANSPARENT_SORT_KEY)
            self.tm_results_table.setItem(row, 1, target_item)
            target_editor = _ReadOnlyHtmlCell(
                result.target, search_term=search_text,
                parent=self.tm_results_table, bold=False)
            self._apply_tm_cell_font(target_editor)
            self.tm_results_table.setCellWidget(row, 1, target_editor)

            # TM name
            tm_name = result.metadata.get('tm_name', 'Unknown')
            tm_item = QTableWidgetItem(tm_name)
            tm_item.setToolTip(tm_name)  # Full name on hover
            # Store metadata for right-click "Open in TM browser"
            tm_item.setData(Qt.ItemDataRole.UserRole, {
                'tm_id': result.metadata.get('tm_id'),
                'tm_name': tm_name,
                'source': result.source,
                'target': result.target,
            })
            self.tm_results_table.setItem(row, 2, tm_item)

        # Resize rows to fit the actual rendered QTextEdit content
        self._resize_tm_result_rows()
        
        # === Update Vertical View (List) ===
        # Use theme colors for HTML content (with fallback for missing theme_manager)
        if self.theme_manager:
            theme = self.theme_manager.current_theme
            text_color = theme.text
            # Use darker backgrounds for TM results to improve readability
            bg_even = theme.window_bg  # Darker than base
            bg_odd = theme.alternate_bg
            border_color = theme.border
            source_label_color = theme.tm_source_label
            target_label_color = theme.tm_target_label
        else:
            # Fallback colors for light theme
            text_color = "#000000"
            bg_even = "#FFFFFF"
            bg_odd = "#EBEBEB"
            border_color = "#CCCCCC"
            source_label_color = "#0066CC"
            target_label_color = "#006600"

        import html as _html
        html = f"<h3 style='color: {text_color}; margin-bottom: 15px;'>Found {len(results)} TM matches for '<span style='color: {source_label_color};'>{_html.escape(search_text)}</span>'</h3>"

        for idx, result in enumerate(results, 1):
            source = result.source
            target = result.target
            tm_name = _html.escape(str(result.metadata.get('tm_name', 'Unknown')))

            # Highlight search term
            highlighted_source = self._highlight_search_term(source, search_text)
            highlighted_target = self._highlight_search_term(target, search_text)

            # Alternating background colors from theme (using darker colors)
            bg_color = bg_even if idx % 2 == 0 else bg_odd

            html += f"""
            <div style='background-color: {bg_color}; padding: 10px 8px; margin: 0; color: {text_color};'>
                <div style='color: {text_color}; font-size: 11px; margin-bottom: 6px;'>
                    #{idx} - TM: <b>{tm_name}</b>
                </div>
                <div style='margin-bottom: 4px; color: {text_color};'>
                    <b style='color: {source_label_color};'>{source_lang_name}:</b> {highlighted_source}
                </div>
                <div style='color: {text_color};'>
                    <b style='color: {target_label_color};'>{target_lang_name}:</b> {highlighted_target}
                </div>
            </div>
            <hr style='border: none; border-top: 1px solid {border_color}; margin: 0;'>
            """
        
        self.tm_results_vertical.setHtml(html)

        # v1.10.173: restore sortability now that the rebuild is done.
        self.tm_results_table.setSortingEnabled(_prev_tm_results_sorting)

    # ── TM results text size (issue #69) ─────────────────────────────
    _TM_FONT_SIZE_KEY = 'superlookup_tm_font_size'

    def _load_tm_font_size(self):
        """Saved TM-results point size, or None to keep the default look."""
        try:
            mw = self.main_window
            if mw and hasattr(mw, 'load_general_settings'):
                value = (mw.load_general_settings() or {}).get(self._TM_FONT_SIZE_KEY)
                if value:
                    return max(7, min(24, int(value)))
        except Exception as e:
            print(f"[Superlookup] Could not load TM text size: {e}")
        return None

    def _on_tm_font_size_changed(self, size: int):
        self._tm_font_size = size
        self.tm_results_vertical.setFont(QFont("Segoe UI", size))
        for row in range(self.tm_results_table.rowCount()):
            for col in (0, 1):
                widget = self.tm_results_table.cellWidget(row, col)
                if widget is not None:
                    self._apply_tm_cell_font(widget)
        self._resize_tm_result_rows()
        try:
            mw = self.main_window
            if mw and hasattr(mw, 'load_general_settings') and hasattr(mw, 'save_general_settings'):
                settings = mw.load_general_settings() or {}
                settings[self._TM_FONT_SIZE_KEY] = size
                mw.save_general_settings(settings)
        except Exception as e:
            print(f"[Superlookup] Could not save TM text size: {e}")

    def _apply_tm_cell_font(self, widget):
        """Give a TM result cell the chosen size (no-op until one is chosen)."""
        if getattr(self, '_tm_font_size', None):
            font = widget.font()
            font.setPointSize(self._tm_font_size)
            widget.setFont(font)

    def _resize_tm_result_rows(self):
        # The row-height cap grows with the text so larger sizes aren't clipped.
        size = getattr(self, '_tm_font_size', None) or 10
        self._resize_html_cell_rows(self.tm_results_table, cols_with_html=(0, 1),
                                    min_height=24, max_height=max(120, 12 * size))

    def _lang_base_name(self, lang):
        """Return the base-language display name for a code or display string.

        Handles ISO codes ('en', 'en-US') via the main window's
        _normalize_language_code mapping, and strips any trailing
        "(variant)" suffix from display strings like "English (US)".
        Returns None when the input is empty.
        """
        if not lang:
            return None
        name = lang
        # Prefer the main window's normalisation if available – it maps
        # 'en'/'en-US'/'en-GB' all to 'English', etc.
        try:
            if self.main_window and hasattr(self.main_window, '_normalize_language_code'):
                name = self.main_window._normalize_language_code(lang)
        except Exception:
            pass
        # Strip trailing " (…)" if present (e.g. "English (US)" → "English")
        if name and '(' in name:
            name = name.split('(', 1)[0].strip()
        return name or None

    def _highlight_search_term(self, text, search_term):
        """Highlight search term in text with theme-aware background.

        Case-insensitive highlighting that preserves original case.
        """
        import re
        import html as _html
        # The result is HTML, so the text is always escaped, also when
        # there is nothing to highlight.
        if not text:
            return ''
        if not search_term:
            return _html.escape(text)

        # Use theme colors for highlight (with fallback for missing theme_manager)
        if self.theme_manager:
            theme = self.theme_manager.current_theme
            highlight_bg = theme.tm_highlight_bg
            highlight_text = theme.tm_highlight_text
        else:
            # Fallback colors - yellow highlight
            highlight_bg = "#FFFF00"
            highlight_text = "#000000"

        # Case-insensitive, preserving the original case. Matched in the
        # plain text and escaped piece by piece, so a term with "&" or "<"
        # is still found.
        pattern = re.compile(re.escape(search_term), re.IGNORECASE)
        pieces = []
        last = 0
        for m in pattern.finditer(text):
            pieces.append(_html.escape(text[last:m.start()]))
            pieces.append(
                f'<span style="background-color: {highlight_bg}; color: {highlight_text}; '
                f'padding: 1px 2px;">{_html.escape(m.group(0))}</span>')
            last = m.end()
        pieces.append(_html.escape(text[last:]))
        return ''.join(pieces)
    
    def _resize_html_cell_rows(self, table, cols_with_html=(), min_height=24, max_height=120):
        """Resize table rows based on the actual rendered height of the
        QTextEdit cell widgets in the specified columns.

        Qt's built-in resizeRowsToContents() measures QTableWidgetItem text,
        not cell widgets, so it gives wrong results when cells use
        setCellWidget(). This helper walks every row, asks each QTextEdit
        cell widget for the height its content needs at the current column
        width, and sets the row to the tallest value within the bounds.
        """
        for row in range(table.rowCount()):
            needed = min_height
            for col in cols_with_html:
                widget = table.cellWidget(row, col)
                if widget is None:
                    continue
                # Force layout at the current cell width so wrapping is applied
                width = table.columnWidth(col) - 8  # account for padding
                if width < 40:
                    width = 40
                doc = widget.document()
                doc.setTextWidth(width)
                # Add a few px of vertical padding so descenders aren't clipped
                h = int(doc.size().height()) + 4
                if h > needed:
                    needed = h
            if needed < min_height:
                needed = min_height
            if needed > max_height:
                needed = max_height
            table.setRowHeight(row, needed)

    def display_termbase_results(self, results):
        """Display termbase results with search term highlighting and metadata"""
        # v1.10.173: bracket the rebuild with sort-off so insertRow() +
        # setItem() don't trigger a live re-sort after every cell write
        # (which would shuffle row indices mid-loop and detach cell
        # widgets from their items). Sort state is restored at the end.
        _prev_tb_results_sorting = self.termbase_results_table.isSortingEnabled()
        self.termbase_results_table.setSortingEnabled(False)
        self.termbase_results_table.setRowCount(0)

        # Get search term for highlighting
        search_text = self.source_text.currentText().strip().lower()

        # Update column headers with the current project's base language names
        # (e.g. 'en-US' → 'English'). Falls back to "Source"/"Target" if no
        # project is loaded or the language is unknown.
        src_header = "Source"
        tgt_header = "Target"
        if self.main_window:
            raw_src = getattr(self.main_window, 'source_language', None)
            raw_tgt = getattr(self.main_window, 'target_language', None)
            src_header = self._lang_base_name(raw_src) or "Source"
            tgt_header = self._lang_base_name(raw_tgt) or "Target"
        self.termbase_results_table.setHorizontalHeaderLabels(
            [src_header, tgt_header, "Termbase", "Domain", "Notes"])
        
        # Filter duplicates: if same source→target exists in multiple glossaries,
        # only keep the one from the project glossary (ranking=1) or first seen
        filtered_results = []
        seen_pairs = {}  # (source_lower, target_lower) -> (result, ranking)

        for result in results:
            source_lower = result.source.lower()
            target_lower = result.target.lower()
            pair_key = (source_lower, target_lower)

            metadata = result.metadata or {}
            ranking = metadata.get('ranking') or 99

            if pair_key in seen_pairs:
                existing_result, existing_ranking = seen_pairs[pair_key]
                if ranking < existing_ranking:
                    seen_pairs[pair_key] = (result, ranking)
            else:
                seen_pairs[pair_key] = (result, ranking)

        # Convert filtered dict back to list
        filtered_results = [result for result, _ in seen_pairs.values()]
        
        for result in filtered_results:
            row = self.termbase_results_table.rowCount()
            self.termbase_results_table.insertRow(row)
            
            # Get metadata
            metadata = result.metadata or {}
            termbase_name = metadata.get('termbase', '')
            domain = metadata.get('domain', '')
            notes = metadata.get('notes', '')
            
            # Source term – QTextEdit with syntax highlighter for native text
            # selection. Transparent sort-key item to avoid Qt 6.9+ text
            # doubling through the overlay (issue #221 — see display_tm_results).
            source_item = QTableWidgetItem(result.source)
            source_item.setFlags(Qt.ItemFlag.NoItemFlags)
            source_item.setForeground(_TRANSPARENT_SORT_KEY)
            self.termbase_results_table.setItem(row, 0, source_item)
            source_editor = _ReadOnlyHtmlCell(
                result.source, search_term=search_text,
                parent=self.termbase_results_table, bold=False)
            self.termbase_results_table.setCellWidget(row, 0, source_editor)

            # Target term – same pattern (see #221 note above)
            target_item = QTableWidgetItem(result.target)
            target_item.setFlags(Qt.ItemFlag.NoItemFlags)
            target_item.setForeground(_TRANSPARENT_SORT_KEY)
            self.termbase_results_table.setItem(row, 1, target_item)
            target_editor = _ReadOnlyHtmlCell(
                result.target, search_term=search_text,
                parent=self.termbase_results_table, bold=False)
            self.termbase_results_table.setCellWidget(row, 1, target_editor)
            
            # Termbase name
            tb_item = QTableWidgetItem(termbase_name)
            tb_item.setToolTip(termbase_name)
            self.termbase_results_table.setItem(row, 2, tb_item)
            
            # Domain
            domain_item = QTableWidgetItem(domain)
            domain_item.setToolTip(domain)
            self.termbase_results_table.setItem(row, 3, domain_item)
            
            # Notes (truncated display)
            notes_display = notes[:30] + '...' if len(notes) > 30 else notes
            notes_item = QTableWidgetItem(notes_display)
            notes_item.setToolTip(notes)  # Full notes on hover
            self.termbase_results_table.setItem(row, 4, notes_item)
            
            # Store metadata in first column item for right-click navigation
            source_editor.setProperty('termbase_id', metadata.get('termbase_id'))
            source_editor.setProperty('source_term', result.source)
            source_editor.setProperty('termbase_name', termbase_name)

        # Resize rows to fit the actual rendered QTextEdit content – short
        # entries get short rows, long entries wrap to taller rows.
        self._resize_html_cell_rows(self.termbase_results_table,
                                    cols_with_html=(0, 1),
                                    min_height=24, max_height=120)

        # v1.10.173: restore sortability now that the rebuild is done.
        self.termbase_results_table.setSortingEnabled(_prev_tb_results_sorting)

    def _show_termbase_result_context_menu(self, position):
        """Show context menu for termbase results with option to navigate to term"""
        # Get the row at click position
        row = self.termbase_results_table.rowAt(position.y())
        if row < 0:
            return
        
        # Get the source label widget which has the metadata
        source_widget = self.termbase_results_table.cellWidget(row, 0)
        if not source_widget:
            return
        
        termbase_id = source_widget.property('termbase_id')
        source_term = source_widget.property('source_term')
        termbase_name = source_widget.property('termbase_name')
        
        # Create context menu
        menu = QMenu(self.termbase_results_table)
        
        # Copy actions
        copy_target_action = menu.addAction("📋 Copy Translation")
        copy_source_action = menu.addAction("📋 Copy Source Term")
        
        menu.addSeparator()
        
        # Navigate to termbase action
        if termbase_id and termbase_name:
            edit_action = menu.addAction(f"✏️ Edit in Termbase: {termbase_name}")
            edit_action.setData({'termbase_id': termbase_id, 'source_term': source_term, 'termbase_name': termbase_name})
        else:
            edit_action = None
        
        # Show menu and handle selection
        action = menu.exec(self.termbase_results_table.mapToGlobal(position))
        
        if action == copy_target_action:
            self.copy_selected_termbase_target()
        elif action == copy_source_action:
            if source_term:
                QApplication.clipboard().setText(source_term)
                self.status_label.setText(f"Copied source term: {source_term}")
        elif action == edit_action and edit_action:
            data = action.data()
            self._navigate_to_termbase_entry(data['termbase_id'], data['source_term'])
    
    def _navigate_to_termbase_entry(self, termbase_id, source_term):
        """Navigate to a specific termbase entry in Project resources > Termbases tab"""
        try:
            # Navigate to Project resources > Termbases
            if hasattr(self, 'main_window') and self.main_window:
                main = self.main_window

                # Bring the main Workbench window to the foreground
                if main.isMinimized():
                    main.showNormal()
                main.show()
                main.raise_()
                main.activateWindow()
                import sys
                if sys.platform == 'win32':
                    try:
                        import ctypes
                        hwnd = int(main.winId())
                        fg = ctypes.windll.user32.GetForegroundWindow()
                        fg_thread = ctypes.windll.user32.GetWindowThreadProcessId(fg, None)
                        our_thread = ctypes.windll.kernel32.GetCurrentThreadId()
                        if fg_thread != our_thread:
                            ctypes.windll.user32.AttachThreadInput(fg_thread, our_thread, True)
                            ctypes.windll.user32.SetForegroundWindow(hwnd)
                            ctypes.windll.user32.AttachThreadInput(fg_thread, our_thread, False)
                        else:
                            ctypes.windll.user32.SetForegroundWindow(hwnd)
                    except Exception:
                        pass

                # Switch to the Termbases tab (index 2 in the post-v1.9.422
                # top-level layout: Editor/TMs/Termbases/AI/Tools/Settings).
                # Termbases used to be a sub-tab under "Resources" – it was
                # promoted to a peer of Editor/AI/Tools/Settings in v1.9.422,
                # so this nav doesn't need to drill through resources_tabs
                # any more.
                if hasattr(main, 'main_tabs'):
                    for i in range(main.main_tabs.count()):
                        if 'Termbases' in main.main_tabs.tabText(i):
                            main.main_tabs.setCurrentIndex(i)
                            break
                
                # Find and select the termbase in the list
                if hasattr(main, 'termbase_table'):
                    for row in range(main.termbase_table.rowCount()):
                        name_item = main.termbase_table.item(row, 1)  # Name column stores ID in UserRole
                        if name_item and name_item.data(Qt.ItemDataRole.UserRole) == termbase_id:
                            main.termbase_table.selectRow(row)
                            # Trigger loading of terms for this termbase
                            if hasattr(main, '_on_termbase_selected'):
                                main._on_termbase_selected()
                            break
                
                # Use the filter box to find the specific term (works with pagination)
                if hasattr(main, 'terms_search_box') and source_term:
                    from PyQt6.QtCore import QTimer
                    def set_filter_and_select():
                        # Set the filter to the source term - this will trigger server-side search
                        main.terms_search_box.setText(source_term)
                        # After filter is applied, select the first matching row
                        QTimer.singleShot(300, lambda: self._select_first_term_in_table(main))
                    QTimer.singleShot(200, set_filter_and_select)
                
                self.status_label.setText(f"Navigated to termbase entry: {source_term}")

        except Exception as e:
            print(f"[Superlookup] Error navigating to termbase: {e}")
            self.status_label.setText(f"Error navigating to termbase: {e}")
    
    def _select_first_term_in_table(self, main):
        """Select the first term in the filtered termbase terms table"""
        try:
            if hasattr(main, 'termbase_terms_table'):
                table = main.termbase_terms_table
                if table.rowCount() > 0:
                    table.selectRow(0)
                    item = table.item(0, 0)
                    if item:
                        table.scrollToItem(item)
        except Exception as e:
            print(f"[Superlookup] Error selecting term: {e}")

    def display_mt_results(self, results):
        """Display MT results in the table"""
        # The MT tab was removed from the Superlookup UI (MT is now handled by
        # QuickTrans). When this SuperlookupTab instance was built without the
        # MT tab (e.g. inside the Floating Assistant), mt_results_table does
        # not exist – silently skip so perform_lookup() can continue to the
        # web-resource search that follows.
        if not hasattr(self, 'mt_results_table'):
            return
        self.mt_results_table.setRowCount(0)
        
        if results:
            success_count = 0
            error_count = 0
            
            for result in results:
                row = self.mt_results_table.rowCount()
                self.mt_results_table.insertRow(row)
                
                # Check if this is an error result
                is_error = result.get('is_error', False) if isinstance(result, dict) else False
                
                # Provider name
                provider = result.get('provider', 'MT') if isinstance(result, dict) else getattr(result, 'provider', 'MT')
                provider_item = QTableWidgetItem(provider)
                if is_error:
                    provider_item.setForeground(QColor("#F44336"))  # Red for errors
                    error_count += 1
                else:
                    provider_item.setForeground(QColor("#1976d2"))  # Blue for success
                    success_count += 1
                self.mt_results_table.setItem(row, 0, provider_item)
                
                # Translation text (or error message)
                translation = result.get('translation', '') if isinstance(result, dict) else getattr(result, 'target', '')
                trans_item = QTableWidgetItem(translation)
                trans_item.setToolTip(translation)
                if is_error:
                    trans_item.setForeground(QColor("#F44336"))  # Red text for errors
                    trans_item.setFont(QFont(trans_item.font().family(), -1, -1, True))  # Italic
                self.mt_results_table.setItem(row, 1, trans_item)
                
                # Copy button (only for successful translations)
                if not is_error:
                    copy_btn = QPushButton("📋")
                    copy_btn.setFixedSize(30, 24)
                    copy_btn.setToolTip(self.tr("Copy translation"))
                    copy_btn.clicked.connect(lambda checked, t=translation: self._copy_mt_result(t))
                    self.mt_results_table.setCellWidget(row, 2, copy_btn)
            
            # Update status with breakdown
            if error_count > 0:
                self.mt_status_label.setText(f"✓ Got {success_count} translation(s), ⚠ {error_count} error(s)")
                self.mt_status_label.setStyleSheet("color: #FF9800; font-weight: bold;")
            else:
                self.mt_status_label.setText(f"✓ Got {success_count} translation(s)")
                self.mt_status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
        else:
            self.mt_status_label.setText(self.tr("No MT results. Check provider settings."))
            self.mt_status_label.setStyleSheet("color: #666; font-style: italic;")
    
    def _copy_mt_result(self, text):
        """Copy MT result to clipboard"""
        import pyperclip
        pyperclip.copy(text)
        self.status_label.setText(f"✓ Copied: {text[:50]}...")
    
    def on_mt_result_double_click(self, index):
        """Handle double-click on MT result to copy"""
        row = index.row()
        trans_item = self.mt_results_table.item(row, 1)
        if trans_item:
            self._copy_mt_result(trans_item.text())
    
    def _tm_table_context_menu(self, pos):
        """Right-click context menu for TM results table."""
        row = self.tm_results_table.rowAt(pos.y())
        if row < 0:
            return

        source_item = self.tm_results_table.item(row, 0)
        target_item = self.tm_results_table.item(row, 1)
        tm_item = self.tm_results_table.item(row, 2)
        source_text = source_item.text() if source_item else ""
        target_text = target_item.text() if target_item else ""

        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self.tm_results_table)

        if source_text:
            copy_source = menu.addAction("Copy source")
            copy_source.triggered.connect(
                lambda: (pyperclip.copy(source_text),
                         self.status_label.setText(f"\u2713 Copied source: {source_text[:50]}...")))

        if target_text:
            copy_target = menu.addAction("Copy target")
            copy_target.triggered.connect(
                lambda: (pyperclip.copy(target_text),
                         self.status_label.setText(f"\u2713 Copied target: {target_text[:50]}...")))

        if source_text and target_text:
            copy_both = menu.addAction("Copy source \u2192 target")
            copy_both.triggered.connect(
                lambda: (pyperclip.copy(f"{source_text}\t{target_text}"),
                         self.status_label.setText(self.tr("\u2713 Copied source \u2192 target"))))

        # Open in TM Browser for editing
        tm_meta = tm_item.data(Qt.ItemDataRole.UserRole) if tm_item else None
        if tm_meta and tm_meta.get('tm_id'):
            menu.addSeparator()
            open_action = menu.addAction("\u270F Open in TM browser")
            open_action.triggered.connect(
                lambda: self._open_tm_entry_in_browser(tm_meta))

        menu.exec(self.tm_results_table.viewport().mapToGlobal(pos))

    def _open_tm_entry_in_browser(self, tm_meta):
        """Open the TM Manager's Concordance tab pre-filled with this entry's
        source text, so the user can quickly find and edit the matching segment.
        """
        try:
            mw = self.main_window or self.window()
            if not mw or not hasattr(mw, 'show_tm_manager_tab'):
                return
            # Bring the main Workbench window to the foreground
            if mw.isMinimized():
                mw.showNormal()
            mw.show()
            mw.raise_()
            mw.activateWindow()
            import sys
            if sys.platform == 'win32':
                try:
                    import ctypes
                    hwnd = int(mw.winId())
                    fg = ctypes.windll.user32.GetForegroundWindow()
                    fg_thread = ctypes.windll.user32.GetWindowThreadProcessId(fg, None)
                    our_thread = ctypes.windll.kernel32.GetCurrentThreadId()
                    if fg_thread != our_thread:
                        ctypes.windll.user32.AttachThreadInput(fg_thread, our_thread, True)
                        ctypes.windll.user32.SetForegroundWindow(hwnd)
                        ctypes.windll.user32.AttachThreadInput(fg_thread, our_thread, False)
                    else:
                        ctypes.windll.user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass

            # Open TM Manager's Concordance tab pre-filled with the source text
            source_text = tm_meta.get('source', '')
            mw.show_tm_manager_tab(1, initial_concordance_query=source_text)
            self.status_label.setText(
                f"\u2713 Opened TM Manager concordance for \"{source_text[:50]}\""
            )
        except Exception as e:
            self.status_label.setText(f"\u26A0 Could not open TM browser: {e}")

    def on_tm_result_double_click(self, index):
        """Handle double-click on TM result"""
        self.copy_selected_tm_target()

    def copy_selected_tm_target(self):
        """Copy selected TM target to clipboard"""
        selected = self.tm_results_table.selectedItems()
        if selected:
            row = selected[0].row()
            target_item = self.tm_results_table.item(row, 1)
            if target_item:
                pyperclip.copy(target_item.text())
                self.status_label.setText(f"✓ Copied to clipboard: {target_item.text()[:50]}...")

    def insert_selected_tm_target(self):
        """Insert selected TM target into active application"""
        selected = self.tm_results_table.selectedItems()
        if selected:
            row = selected[0].row()
            target_item = self.tm_results_table.item(row, 1)
            if target_item:
                pyperclip.copy(target_item.text())
                self.status_label.setText(
                    f"✓ Copied to clipboard. Press {format_shortcut_for_display('Ctrl+V')} to paste."
                )
                # Could auto-paste here if we wanted to be aggressive
                # pyautogui.hotkey('ctrl', 'v')

    def export_tm_results(self):
        """Export every TM hit for the current search to .xlsx or .csv (issue #170).

        Rows come from the table rather than _current_tm_results so the file
        follows whatever column sort the user has applied."""
        rows = []
        for row in range(self.tm_results_table.rowCount()):
            tm_item = self.tm_results_table.item(row, 2)
            data = tm_item.data(Qt.ItemDataRole.UserRole) if tm_item else None
            if data:
                rows.append(data)
        if not rows:
            QMessageBox.information(self, self.tr("Export Results"),
                                    self.tr("There are no TM results to export. Run a search first."))
            return

        query = self.source_text.currentText().strip()
        safe_query = re.sub(r'[^\w\- ]+', '', query).strip().replace(' ', '_')[:40]
        default_name = f"concordance_{safe_query}.xlsx" if safe_query else "concordance.xlsx"
        start_dir = str(Path.home())
        project_path = getattr(self.main_window, 'project_file_path', None) if self.main_window else None
        if project_path:
            start_dir = str(Path(project_path).parent)

        path, selected_filter = QFileDialog.getSaveFileName(
            self, self.tr("Export Concordance Results"),
            str(Path(start_dir) / default_name),
            "Excel Workbook (*.xlsx);;CSV (comma-separated) (*.csv)")
        if not path:
            return
        wanted_ext = '.csv' if 'csv' in selected_filter.lower() else '.xlsx'
        if Path(path).suffix.lower() not in ('.xlsx', '.csv'):
            path += wanted_ext

        headers = [self.tm_results_table.horizontalHeaderItem(c).text()
                   if self.tm_results_table.horizontalHeaderItem(c) else ''
                   for c in range(2)]
        try:
            from modules.concordance_export import export_concordance_results
            count = export_concordance_results(rows, path, query=query,
                                               source_label=headers[0],
                                               target_label=headers[1])
        except Exception as e:
            QMessageBox.warning(self, self.tr("Export Results"),
                                self.tr("Could not export the results:") + f"\n\n{e}")
            return
        self.status_label.setText(f"✓ Exported {count} TM result(s) to {Path(path).name}")

    def _init_search_history(self):
        """Initialize search history from file."""
        self.search_history = []
        self._search_history_file = None
        
        # Get user_data path from main window
        if self.main_window and hasattr(self.main_window, 'user_data_path'):
            from pathlib import Path
            user_data = Path(self.main_window.user_data_path)
            # Beside the other settings files in workbench/settings/. It used to
            # be read and written in <data>/settings/, the folder those files
            # were moved out of; a history still there is read once and then
            # saved in the right place.
            self._search_history_file = user_data / "workbench" / "settings" / "superlookup_history.json"
            history_file = self._search_history_file
            legacy_file = user_data / "settings" / "superlookup_history.json"
            if not history_file.exists() and legacy_file.exists():
                history_file = legacy_file

            # Load existing history
            if history_file.exists():
                try:
                    import json
                    with open(history_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        self.search_history = data.get('searches', [])[:20]
                except Exception:
                    pass
    
    def _add_to_search_history(self, text: str):
        """Add a search term to history."""
        if not text or not text.strip():
            return
        
        text = text.strip()
        # Remove if exists, add to front
        if text in self.search_history:
            self.search_history.remove(text)
        self.search_history.insert(0, text)
        self.search_history = self.search_history[:20]  # Keep max 20
        
        # Update dropdown
        if hasattr(self, 'source_text') and hasattr(self.source_text, 'set_history'):
            self.source_text.set_history(self.search_history)
        
        # Save to file
        self._save_search_history()
    
    def _save_search_history(self):
        """Save search history to file."""
        if not self._search_history_file:
            return
        
        try:
            import json
            self._search_history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._search_history_file, 'w', encoding='utf-8') as f:
                json.dump({'searches': self.search_history[:20]}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    
    def clear_all(self):
        """Clear all text and results"""
        self.source_text.clear()
        self.tm_results_table.setRowCount(0)
        self.termbase_results_table.setRowCount(0)
        self.status_label.setText(self.tr("Cleared. Ready for new lookup."))
    
    def _normalize_language_code(self, lang: str) -> str:
        """Normalize a language code/name to a base ISO code for comparison.

        Delegates to modules.language_codes (the single authority): full
        names ('Dutch') and regional variants ('nl-BE') all collapse to the
        base code ('nl')."""
        from modules import language_codes as _lc
        return _lc.base_code(lang)
    
    # Hard cap on termbase results to keep the UI responsive when very large
    # termbases are loaded (otherwise we'd build thousands of QTableWidgetItems
    # synchronously and freeze on render).
    SUPERLOOKUP_TERMBASE_RESULT_CAP = 500

    def search_termbases(self, text, source_lang=None, target_lang=None, connection=None,
                         selected_tb_ids=None):
        """Search Supervertaler termbases for matching terms.

        Uses Superlookup's OWN checkbox selections (independent from Resources > Termbases).
        Respects the search direction setting (source only, target only, or both).

        Args:
            text: Text to search for
            source_lang: Filter by source language - can be a string OR a list of variants (None = any)
            target_lang: Filter by target language - can be a string OR a list of variants (None = any)
            connection: Optional sqlite3.Connection for thread-safe access (used by the
                SuperLookup background search worker). When None, the main-thread
                cursor is used via termbase_mgr.
        """
        results = []
        
        # Normalize language filters to lists of normalized codes for consistent matching
        # Handle both single strings and lists of variants
        if source_lang:
            if isinstance(source_lang, list):
                source_langs_norm = [self._normalize_language_code(lang) for lang in source_lang if lang]
            else:
                source_langs_norm = [self._normalize_language_code(source_lang)]
        else:
            source_langs_norm = None
        
        if target_lang:
            if isinstance(target_lang, list):
                target_langs_norm = [self._normalize_language_code(lang) for lang in target_lang if lang]
            else:
                target_langs_norm = [self._normalize_language_code(target_lang)]
        else:
            target_langs_norm = None
        
        print(f"[DEBUG search_termbases] Called with text='{text[:30]}...', source_lang='{source_lang}' (norm: {source_langs_norm}), target_lang='{target_lang}' (norm: {target_langs_norm})", flush=True)
        print(f"[DEBUG search_termbases] termbase_mgr: {self.termbase_mgr is not None}, db_manager: {self.db_manager is not None}, main_window: {self.main_window is not None}", flush=True)
        
        if not self.termbase_mgr or not self.db_manager or not self.main_window:
            print(f"[DEBUG search_termbases] Early return - missing managers!", flush=True)
            return results
        
        try:
            # Get search direction
            direction = self.get_search_direction()
            
            # v1.10.247: termbase selection follows the dedicated
            # Termbases-tab SuperLookup column (termbases.superlookup_enabled),
            # independent of the Read flag — so a termbase can be searched by
            # SuperLookup whether or not it's Read-active for the project. The
            # search reads terms straight from the DB (get_terms below), NOT
            # the Read-gated in-memory matching index, so Read-off termbases
            # are searched fine. v1.10.169: selected_tb_ids is pre-computed on
            # the main thread by perform_lookup() and threaded through to here,
            # so this method can run safely from the search worker thread
            # (sqlite cursor affinity). If the caller didn't precompute (e.g.
            # an older call site), fall back to computing now.
            if selected_tb_ids is None:
                selected_tb_ids = self.get_selected_termbase_ids()
            if not selected_tb_ids:
                print("[DEBUG search_termbases] No termbase is SuperLookup-enabled – skipping termbase search.")
                return results

            all_termbases = self.termbase_mgr.get_all_termbases(connection=connection)
            termbases_to_search = [tb for tb in all_termbases if tb['id'] in selected_tb_ids]
            
            text_lower = text.lower()
            
            # Search each selected termbase
            for termbase in termbases_to_search:
                termbase_id = termbase['id']
                
                # Check termbase language filters - normalize for comparison
                tb_source_lang = termbase.get('source_lang', '')
                tb_target_lang = termbase.get('target_lang', '')
                tb_source_norm = self._normalize_language_code(tb_source_lang)
                tb_target_norm = self._normalize_language_code(tb_target_lang)
                
                print(f"[DEBUG search_termbases] Checking TB '{termbase['name']}': source='{tb_source_lang}' (norm: '{tb_source_norm}'), target='{tb_target_lang}' (norm: '{tb_target_norm}')")
                
                # Skip termbases whose language PAIR doesn't match the search.
                # DIRECTION-AGNOSTIC: the term matching below is bidirectional
                # (it searches both source_term and target_term and swaps as
                # needed), so a termbase stored in the REVERSE orientation must
                # still be searched — e.g. the PATENTS termbase is declared
                # en→nl, but a Dutch→English lookup must still find its terms.
                # We therefore accept the termbase if its declared languages
                # line up with the From/To pair in EITHER direction. A side with
                # no declared TB language is never used to exclude.
                src_ok_fwd = (not source_langs_norm) or (not tb_source_norm) or (tb_source_norm in source_langs_norm)
                tgt_ok_fwd = (not target_langs_norm) or (not tb_target_norm) or (tb_target_norm in target_langs_norm)
                src_ok_rev = (not target_langs_norm) or (not tb_source_norm) or (tb_source_norm in target_langs_norm)
                tgt_ok_rev = (not source_langs_norm) or (not tb_target_norm) or (tb_target_norm in source_langs_norm)
                if not ((src_ok_fwd and tgt_ok_fwd) or (src_ok_rev and tgt_ok_rev)):
                    print(f"[DEBUG search_termbases] Skipping '{termbase['name']}' - language pair mismatch: TB=({tb_source_norm},{tb_target_norm}) search=src{source_langs_norm}/tgt{target_langs_norm}")
                    continue
                
                print(f"[DEBUG search_termbases] Searching in '{termbase['name']}'")
                terms = self.termbase_mgr.get_terms(termbase_id, connection=connection)

                # v1.10.172: decide whether to swap source/target in the
                # display result using the PROJECT'S language pair
                # (main_window.source_language / target_language) — not
                # the SuperLookup From/To dropdowns. The column headers
                # come from the project's language pair too, so they need
                # to be the same source of truth. v1.10.171 used the
                # SuperLookup From/To dropdowns instead, which produced
                # the wrong swap decision when the dropdowns didn't match
                # the project direction (e.g. project NL→EN but
                # dropdowns left at the default EN→NL).
                proj_src_norm = ''
                proj_tgt_norm = ''
                try:
                    mw = self.main_window
                    if mw is not None:
                        raw_proj_src = getattr(mw, 'source_language', None)
                        raw_proj_tgt = getattr(mw, 'target_language', None)
                        if raw_proj_src:
                            proj_src_norm = self._normalize_language_code(raw_proj_src) or ''
                        if raw_proj_tgt:
                            proj_tgt_norm = self._normalize_language_code(raw_proj_tgt) or ''
                except Exception:
                    pass

                tb_reversed = bool(
                    proj_src_norm and proj_tgt_norm
                    and tb_source_norm and tb_target_norm
                    and tb_source_norm == proj_tgt_norm
                    and tb_target_norm == proj_src_norm
                )

                for term in terms:
                    source_term = term.get('source_term', '').lower()
                    target_term = term.get('target_term', '').lower()
                    target_term_original = term.get('target_term', '')
                    source_term_original = term.get('source_term', '')
                    
                    # Also check term-level language if available (normalize for comparison)
                    term_source_lang = term.get('source_lang', '')
                    term_target_lang = term.get('target_lang', '')
                    term_source_norm = self._normalize_language_code(term_source_lang) if term_source_lang else ''
                    term_target_norm = self._normalize_language_code(term_target_lang) if term_target_lang else ''
                    
                    # Check if term-level languages match any of the filter variants
                    if source_langs_norm and term_source_norm and term_source_norm not in source_langs_norm:
                        continue
                    if target_langs_norm and term_target_norm and term_target_norm not in target_langs_norm:
                        continue
                    
                    match_found = False
                    
                    # Minimum length for substring matching to avoid spurious single-char matches
                    # If search text is longer than 3 chars, require term to be at least 3 chars for substring match
                    min_term_len = 3 if len(text_lower) > 3 else 1
                    
                    # Check based on search direction
                    if direction == 'source':
                        # Search source only
                        if source_term and len(source_term) >= min_term_len:
                            if source_term in text_lower or text_lower in source_term:
                                match_found = True
                    elif direction == 'target':
                        # Search target only
                        if target_term and len(target_term) >= min_term_len:
                            if target_term in text_lower or text_lower in target_term:
                                match_found = True
                    else:
                        # Both directions (bidirectional)
                        if source_term and len(source_term) >= min_term_len:
                            if source_term in text_lower or text_lower in source_term:
                                match_found = True
                        if target_term and len(target_term) >= min_term_len:
                            if target_term in text_lower or text_lower in target_term:
                                match_found = True
                    
                    if match_found:
                        # v1.10.171: swap source/target when the termbase
                        # is oriented backwards relative to the project
                        # (e.g. EN→NL termbase in an NL→EN project), so
                        # the cell content lines up with the column header.
                        if tb_reversed:
                            display_source = target_term_original
                            display_target = source_term_original
                        else:
                            display_source = source_term_original
                            display_target = target_term_original

                        # Create LookupResult with full metadata
                        from modules.superlookup import LookupResult
                        results.append(LookupResult(
                            source=display_source,
                            target=display_target,
                            match_percent=100,
                            source_type='termbase',
                            metadata={
                                'termbase': termbase['name'],
                                'termbase_id': termbase_id,
                                'domain': term.get('domain', ''),
                                'notes': term.get('notes', ''),
                                'project': term.get('project', ''),
                                'client': term.get('client', ''),
                                'forbidden': term.get('forbidden', False)
                            }
                        ))
                        if len(results) >= self.SUPERLOOKUP_TERMBASE_RESULT_CAP:
                            break
                if len(results) >= self.SUPERLOOKUP_TERMBASE_RESULT_CAP:
                    print(f"[search_termbases] Hit result cap of {self.SUPERLOOKUP_TERMBASE_RESULT_CAP}; stopping early.")
                    break

            # Remove duplicates
            seen = set()
            unique_results = []
            for result in results:
                key = (result.source.lower(), result.target.lower())
                if key not in seen:
                    seen.add(key)
                    unique_results.append(result)
            
            return unique_results
            
        except Exception as e:
            if hasattr(self.main_window, 'log'):
                self.main_window.log(f"✗ Error searching termbases: {e}")
            return results
    
    def copy_selected_termbase_target(self):
        """Copy selected termbase translation to clipboard"""
        selected = self.termbase_results_table.selectedItems()
        if selected:
            row = selected[0].row()
            target_item = self.termbase_results_table.item(row, 1)
            if target_item:
                pyperclip.copy(target_item.text())
                self.status_label.setText(f"✓ Copied to clipboard: {target_item.text()[:50]}...")
    
    def add_to_termbase(self):
        """Add source text and selected translation as new term to termbase"""
        # Get source text
        source_text = self.source_text.currentText().strip()
        if not source_text:
            QMessageBox.warning(self, "No Source Text", "Please enter or capture source text first.")
            return
        
        # Get selected termbase result if any
        selected = self.termbase_results_table.selectedItems()
        target_text = ""
        if selected:
            row = selected[0].row()
            target_item = self.termbase_results_table.item(row, 1)
            if target_item:
                target_text = target_item.text()
        
        if not self.termbase_mgr or not self.db_manager or not self.main_window:
            QMessageBox.warning(self, "Database Not Available", "Termbase database is not initialized.")
            return
        
        # Show dialog to add term
        self.show_add_term_dialog(source_text, target_text)
    
    def show_add_term_dialog(self, source_text, target_text=""):
        """Show dialog to add new term to glossary"""
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Add Term to Termbase"))
        dialog.setMinimumWidth(500)
        
        layout = QVBoxLayout(dialog)
        
        # Source term
        layout.addWidget(QLabel(self.tr("Source Term:")))
        source_edit = QLineEdit(source_text)
        layout.addWidget(source_edit)
        
        # Target term
        layout.addWidget(QLabel(self.tr("Target Translation:")))
        target_edit = QLineEdit(target_text)
        layout.addWidget(target_edit)
        
        # Termbase selection
        layout.addWidget(QLabel(self.tr("Select Termbase:")))
        termbase_combo = QComboBox()
        
        # Get writable termbases
        try:
            project_id = None
            if hasattr(self.main_window, 'current_project') and self.main_window.current_project:
                project_id = self.main_window.current_project.id if hasattr(self.main_window.current_project, 'id') else None
            
            all_termbases = self.termbase_mgr.get_all_termbases()
            writable_termbases = [tb for tb in all_termbases if not tb.get('read_only', True)]
            
            if not writable_termbases:
                QMessageBox.warning(self, "No Writable Termbases",
                    "No writable termbases found. Please enable Write for at least one termbase on the 🏷️ Termbases tab.")
                return
            
            for tb in writable_termbases:
                termbase_combo.addItem(tb['name'], tb['id'])
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not load termbases: {e}")
            return
        
        layout.addWidget(termbase_combo)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(dialog.reject)
        button_layout.addWidget(cancel_btn)
        
        add_btn = QPushButton(self.tr("Add Term"))
        add_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 8px; border: none; outline: none;")
        add_btn.clicked.connect(dialog.accept)
        button_layout.addWidget(add_btn)
        
        layout.addLayout(button_layout)
        
        # Show dialog
        if dialog.exec() == QDialog.DialogCode.Accepted:
            source = source_edit.text().strip()
            target = target_edit.text().strip()
            termbase_id = termbase_combo.currentData()
            
            if not source or not target:
                QMessageBox.warning(self, "Missing Information", "Both source and target terms are required.")
                return
            
            # Add term to termbase
            try:
                term_id = self.termbase_mgr.add_term(
                    termbase_id=termbase_id,
                    source_term=source,
                    target_term=target,
                    domain="",
                    notes=f"Added via Superlookup",
                    project="",
                    client=""
                )
                
                if term_id is None:
                    # Duplicate detected
                    if hasattr(self.main_window, '_play_sound_effect'):
                        self.main_window._play_sound_effect('glossary_term_duplicate')

                    try:
                        if hasattr(self.main_window, 'statusBar') and self.main_window.statusBar():
                            self.main_window.statusBar().showMessage("⚠ Duplicate termbase entry (not added)", 3500)
                    except Exception:
                        pass

                    QMessageBox.warning(self, "Duplicate Term", 
                        f"This term already exists in {termbase_combo.currentText()}:\n\n{source} → {target}\n\nDuplicate terms are not allowed.")
                    return
                
                if hasattr(self.main_window, 'log'):
                    self.main_window.log(f"✓ Added term to {termbase_combo.currentText()}: {source} → {target}")

                if hasattr(self.main_window, '_play_sound_effect'):
                    self.main_window._play_sound_effect('glossary_term_added')

                try:
                    if hasattr(self.main_window, 'statusBar') and self.main_window.statusBar():
                        self.main_window.statusBar().showMessage(
                            f"✓ Added termbase entry: {source} → {target} (to {termbase_combo.currentText()})",
                            3500,
                        )
                except Exception:
                    pass
                
                QMessageBox.information(self, "Term Added", 
                    f"Successfully added term:\n\n{source} → {target}\n\nto termbase: {termbase_combo.currentText()}")
                
                # Refresh termbase results
                self.perform_lookup()
                
            except Exception as e:
                if hasattr(self.main_window, '_play_sound_effect'):
                    self.main_window._play_sound_effect('glossary_term_error')

                try:
                    if hasattr(self.main_window, 'statusBar') and self.main_window.statusBar():
                        self.main_window.statusBar().showMessage("❌ Error adding termbase entry (see log)", 3500)
                except Exception:
                    pass

                QMessageBox.critical(self, "Error", f"Failed to add term:\n{str(e)}")
    
    def set_tm_database(self, tm_db):
        """Set TM database (called from main window)"""
        self.tm_database = tm_db
        if self.engine:
            self.engine.set_tm_database(tm_db)
    
    def _find_autohotkey_executable(self):
        """Find AutoHotkey executable, checking saved path first
        
        Returns tuple: (exe_path, source) where source is 'saved', 'detected', or None
        """
        # First, check if user has a saved custom path. (Read through
        # load_general_settings: the main window has no general_settings
        # attribute, so the old attribute lookup never found a saved path.)
        if self.main_window and hasattr(self.main_window, 'load_general_settings'):
            saved_path = (self.main_window.load_general_settings() or {}).get('autohotkey_path', '')
            if saved_path and os.path.exists(saved_path):
                print(f"[Superlookup] Using saved AutoHotkey path: {saved_path}")
                return saved_path, 'saved'
        
        # Standard installation paths
        username = os.environ.get('USERNAME', '')
        ahk_paths = [
            r"C:\Program Files\AutoHotkey\v2\AutoHotkey.exe",
            r"C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe",
            r"C:\Program Files\AutoHotkey\AutoHotkey.exe",
            r"C:\Program Files (x86)\AutoHotkey\AutoHotkey.exe",
            fr"C:\Users\{username}\AppData\Local\Programs\AutoHotkey\AutoHotkey.exe",
            # v1 paths
            r"C:\Program Files\AutoHotkey\v1.1\AutoHotkeyU64.exe",
            r"C:\Program Files\AutoHotkey\v1.1\AutoHotkeyU32.exe",
        ]
        
        for path in ahk_paths:
            if os.path.exists(path):
                print(f"[Superlookup] Detected AutoHotkey at: {path}")
                return path, 'detected'
        
        return None, None
    
    def _show_autohotkey_setup_dialog(self):
        """Show dialog to help user install or locate AutoHotkey"""
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("AutoHotkey Setup - SuperLookup"))
        dialog.setMinimumWidth(500)
        
        layout = QVBoxLayout(dialog)
        
        # Header
        header = QLabel(self.tr("⌨️ AutoHotkey Required for Global Hotkey"))
        header.setStyleSheet("font-size: 14px; font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(header)
        
        # Explanation
        explanation = QLabel(
            f"SuperLookup uses AutoHotkey to provide a global hotkey ({format_shortcut_for_display('Ctrl+Alt+L')}) "
            "that works from any application.\n\n"
            "Without AutoHotkey, SuperLookup will still work, but only when "
            "Supervertaler is the active window."
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet("margin-bottom: 15px;")
        layout.addWidget(explanation)
        
        # Options group
        options_group = QGroupBox(self.tr("Choose an option:"))
        options_layout = QVBoxLayout(options_group)
        
        # Option 1: Download AutoHotkey
        download_btn = QPushButton(self.tr("📥 Download AutoHotkey (Recommended)"))
        download_btn.setStyleSheet("padding: 8px; font-size: 11px;")
        download_btn.setToolTip(self.tr("Opens the AutoHotkey download page in your browser"))
        download_btn.clicked.connect(lambda: self._open_ahk_download())
        options_layout.addWidget(download_btn)
        
        # Option 2: Browse for existing installation
        browse_layout = QHBoxLayout()
        browse_btn = QPushButton(self.tr("📁 Browse for AutoHotkey.exe"))
        browse_btn.setStyleSheet("padding: 8px; font-size: 11px;")
        browse_btn.setToolTip(self.tr("Locate AutoHotkey.exe if you have it installed in a custom location"))
        browse_btn.clicked.connect(lambda: self._browse_for_autohotkey(dialog))
        browse_layout.addWidget(browse_btn)
        options_layout.addLayout(browse_layout)
        
        # Option 3: Skip
        skip_btn = QPushButton(self.tr("⏭️ Skip (Use SuperLookup without global hotkey)"))
        skip_btn.setStyleSheet("padding: 8px; font-size: 11px; color: #666;")
        skip_btn.clicked.connect(dialog.reject)
        options_layout.addWidget(skip_btn)
        
        layout.addWidget(options_group)
        
        # Status label (for showing selected path)
        self._ahk_setup_status = QLabel("")
        self._ahk_setup_status.setStyleSheet("color: green; margin-top: 10px;")
        self._ahk_setup_status.setWordWrap(True)
        layout.addWidget(self._ahk_setup_status)
        
        # Note about restart
        note = QLabel(
            self.tr("💡 Note: After installing AutoHotkey, restart Supervertaler to enable the global hotkey.")
        )
        note.setStyleSheet("color: #666; font-size: 10px; margin-top: 15px;")
        note.setWordWrap(True)
        layout.addWidget(note)
        
        # Don't show again checkbox
        dont_show_cb = CheckmarkCheckBox(self.tr("Do not show this dialog again"))
        dont_show_cb.setStyleSheet("margin-top: 10px;")
        layout.addWidget(dont_show_cb)
        
        # Close button
        close_btn = QPushButton(self.tr("Close"))
        def on_close():
            # Save preference if checkbox is checked
            if dont_show_cb.isChecked():
                if self.main_window and hasattr(self.main_window, 'load_general_settings'):
                    settings = self.main_window.load_general_settings() or {}
                    settings['hide_autohotkey_dialog'] = True
                    self.main_window.save_general_settings(settings)
            dialog.accept()
        close_btn.clicked.connect(on_close)
        layout.addWidget(close_btn)
        
        dialog.exec()
    
    def _open_ahk_download(self):
        """Open AutoHotkey download page"""
        import webbrowser
        webbrowser.open("https://www.autohotkey.com/download/")
        QMessageBox.information(
            self,
            "Download AutoHotkey",
            "The AutoHotkey download page has been opened in your browser.\n\n"
            "Download and install AutoHotkey v2.x, then restart Supervertaler."
        )
    
    def _browse_for_autohotkey(self, parent_dialog):
        """Let user browse for AutoHotkey executable"""
        file_path, _ = QFileDialog.getOpenFileName(
            parent_dialog,
            "Locate AutoHotkey Executable",
            "C:\\Program Files",
            "Executable Files (*.exe);;All Files (*.*)"
        )
        
        if file_path:
            # Verify it looks like AutoHotkey
            if 'autohotkey' not in file_path.lower():
                result = QMessageBox.question(
                    parent_dialog,
                    "Confirm Selection",
                    f"The selected file doesn't appear to be AutoHotkey:\n{file_path}\n\n"
                    "Are you sure you want to use this file?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if result != QMessageBox.StandardButton.Yes:
                    return
            
            # Save the path
            if self.main_window and hasattr(self.main_window, 'load_general_settings'):
                settings = self.main_window.load_general_settings() or {}
                settings['autohotkey_path'] = file_path
                self.main_window.save_general_settings(settings)
                print(f"[Superlookup] Saved AutoHotkey path: {file_path}")
            
            self._ahk_setup_status.setText(f"✓ Saved: {file_path}\n\nRestart Supervertaler to use this path.")
            
            QMessageBox.information(
                parent_dialog,
                "Path Saved",
                f"AutoHotkey path saved:\n{file_path}\n\n"
                "Please restart Supervertaler to enable the global hotkey."
            )
    
    def register_global_hotkey(self):
        """Register global hotkeys for Superlookup, QuickTrans, Sidekick
        and the Voice Always-On toggle.

        Idempotent: if a manager from a previous registration is still
        running, it is stopped first. That makes this safe to call from
        ``reload_global_hotkeys`` after the user changes a binding in
        Settings \u2192 Keyboard Shortcuts.

        Strategy:
        1. Try WinAPI RegisterHotKey / pynput GlobalHotKeys (cross-platform)
        2. On Windows, fall back to AHK external script if that fails
        3. On macOS/Linux, if pynput fails, hotkeys are unavailable

        Shortcut keys are read from the ShortcutManager (customizable in Settings).
        """
        global _ahk_process, _hotkey_manager

        # Stop any previously-running manager so re-registration applies a
        # clean set of bindings instead of stacking new keys on top of old.
        prev_manager = getattr(self, '_hotkey_manager', None)
        if prev_manager is not None:
            try:
                prev_manager.stop()
            except Exception:
                pass
        self._using_pynput = False
        self._hotkey_manager = None

        # Read customizable shortcuts from ShortcutManager. Each action used
        # to have a separate `global_*` ID; that's been merged into a single
        # entry per action with `global: True`. Legacy IDs still resolve via
        # ShortcutManager._LEGACY_IDS, so old saved settings keep working.
        sm = getattr(self.main_window, 'shortcut_manager', None) if self.main_window else None
        if sm:
            sl_shortcut = sm.get_shortcut('tools_universal_lookup').lower()
            qt_shortcut = sm.get_shortcut('mt_quick_lookup').lower()
            sk_shortcut = sm.get_shortcut('sidekick_open').lower()
            ao_shortcut = sm.get_shortcut('voice_alwayson_toggle').lower()

            # Honour the per-shortcut enabled flag and the `global` field:
            # if disabled, or if the entry is no longer flagged global, skip
            # OS-level registration. (In-app QShortcut binding is still set
            # up by the regular create_shortcut path.)
            def _skip(sid):
                return (not sm.is_enabled(sid)) or (not sm.is_global(sid))
            if _skip('tools_universal_lookup'):       sl_shortcut = ''
            if _skip('mt_quick_lookup'):              qt_shortcut = ''
            if _skip('voice_alwayson_toggle'):  ao_shortcut = ''
        else:
            sl_shortcut = 'ctrl+alt+l'
            qt_shortcut = 'ctrl+alt+q'
            ao_shortcut = 'ctrl+alt+o'

        # No platform-specific rewrite needed: GlobalHotkeyManager's macOS
        # NSEvent backend now follows Qt's Mac convention internally
        # (stored "Ctrl" → ⌘ Cmd, "Alt" → ⌥ Option, "Meta" → ⌃ Control),
        # so the same shortcut string fires on the SAME physical keystroke
        # whether triggered via QShortcut in-app or via the global hotkey
        # outside the app. The previous alt→cmd rewrite was a workaround
        # for an older NSEvent parser that took modifier names literally
        # and produced different physical chords for local vs global; that
        # parser was fixed in modules/platform_helpers.py:_MacNSEventHotkey._parse.

        # Logging helper: mirrors to the main window's log as well.
        def _log(m):
            print(m, flush=True)
            if self.main_window and hasattr(self.main_window, 'log'):
                self.main_window.log(m)

        # --- Attempt 1: WinAPI / pynput (cross-platform) ---
        import sys as _sys
        try:
            from modules.platform_helpers import GlobalHotkeyManager, CrossPlatformKeySender
            manager = GlobalHotkeyManager()
            _log(f"[Global Hotkeys] Manager available: {manager.is_available}")
            if manager.is_available:
                # Skip empty shortcuts (action disabled or `global` flag off)
                _bindings = [
                    (sl_shortcut, self._on_pynput_superlookup),
                    (qt_shortcut, self._on_pynput_quicktrans),
                    (ao_shortcut, self._on_pynput_alwayson_toggle),
                ]
                _to_register = [(s, cb) for s, cb in _bindings if s]
                if not _to_register:
                    _log("[Global Hotkeys] Nothing to register (all global shortcuts disabled or unset).")
                    self.hotkey_registered = False
                    return
                for s, cb in _to_register:
                    manager.register(s, cb)
                _log(f"[Global Hotkeys] Registering: {', '.join(s for s, _ in _to_register)}")
                started = manager.start()
                _log(f"[Global Hotkeys] Started: {started}")
                if started:
                    self._hotkey_manager = manager
                    _hotkey_manager = manager  # global for atexit cleanup
                    self._using_pynput = True
                    self.hotkey_registered = True
                    failed = getattr(manager, 'failed_hotkeys', [])
                    if failed:
                        _log(f"\u26A0 [Global Hotkeys] Failed to register: {', '.join(failed)} (claimed by another app)")
                    ok_keys = [s for s, _ in _to_register if s not in failed]
                    _log(f"\u2328 [Global Hotkeys] Registered via {manager._backend}: {', '.join(ok_keys)}")
                    return
                else:
                    _log("[Global Hotkeys] manager.start() returned False")
        except Exception as e:
            _log(f"[Global Hotkeys] Registration failed: {e}")
            import traceback
            traceback.print_exc()
            if IS_MACOS:
                _log("[Global Hotkeys] macOS: grant Accessibility permission "
                     "to whichever binary launched Python (Terminal.app / "
                     "iTerm2.app for terminal launches, Supervertaler.app "
                     "for the bundled app) in System Settings → Privacy & "
                     "Security → Accessibility, then restart Supervertaler.")

        # --- Attempt 2: AHK external script (Windows only) ---
        if IS_WINDOWS:
            self._register_hotkey_external_script()
        else:
            self.hotkey_registered = False

    def _on_pynput_superlookup(self):
        """Called from pynput background thread when Ctrl+Alt+L is pressed.

        IMPORTANT: Do NO work here – pynput callbacks on macOS crash if they
        touch pyperclip, pynput Controller, or Qt objects.  Just signal the
        Qt main thread and return immediately.
        """
        try:
            from PyQt6.QtCore import QMetaObject, Qt as QtConst
            QMetaObject.invokeMethod(
                self, "_handle_superlookup_hotkey",
                QtConst.ConnectionType.QueuedConnection,
            )
        except Exception as e:
            print(f"[Superlookup] Error signaling main thread: {e}")

    def _on_pynput_quicktrans(self):
        """Called from pynput background thread when Ctrl+Alt+Q is pressed.

        IMPORTANT: Do NO work here – see _on_pynput_superlookup docstring.
        """
        try:
            from PyQt6.QtCore import QMetaObject, Qt as QtConst
            QMetaObject.invokeMethod(
                self, "_handle_quicktrans_hotkey",
                QtConst.ConnectionType.QueuedConnection,
            )
        except Exception as e:
            print(f"[QuickTrans] Error signaling main thread: {e}")

    def _on_pynput_alwayson_toggle(self):
        """Voice Always-On toggle hotkey – fires on the pynput
        background thread.

        IMPORTANT: Do NO work here – see _on_pynput_superlookup docstring.
        """
        try:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, self._handle_alwayson_toggle_hotkey)
        except Exception as e:
            print(f"[Voice] Error signaling main thread: {e}")

    @pyqtSlot()
    def _handle_alwayson_toggle_hotkey(self):
        """Runs on Qt main thread – toggle Always-On listening on/off.

        Listening stays on until pressed again. Visual feedback: the
        system tray icon appears when Always-On is active, plus the
        grid toolbar button and the Voice tab update via
        _update_alwayson_ui."""
        try:
            mw = self.main_window or self.window()
            if mw and hasattr(mw, '_toggle_alwayson_listening'):
                mw._toggle_alwayson_listening()
            else:
                print("[Voice] Workbench unavailable for Always-On toggle")
        except Exception as e:
            print(f"[Voice] Error in Always-On toggle handler: {e}")

    def _try_ahk_library_method(self):
        """Try to register hotkey using ahk Python library
        
        Returns True if successful, False otherwise
        """
        try:
            from ahk import AHK
            print("[Superlookup] ahk library available, attempting to use it...")
            
            # Find AutoHotkey executable using shared function
            ahk_exe, source = self._find_autohotkey_executable()
            
            # Create AHK instance (with executable path if found)
            if ahk_exe:
                print(f"[Superlookup] Using AutoHotkey at: {ahk_exe} (source: {source})")
                self._ahk = AHK(executable_path=ahk_exe)
            else:
                # Let it try to find AHK on PATH (may fail)
                self._ahk = AHK()
            print(f"[Superlookup] AHK instance created: {self._ahk}")
            
            # Define hotkey callback
            def on_hotkey():
                """Called when Ctrl+Alt+L is pressed"""
                print("[Superlookup] Hotkey triggered via ahk library!")
                try:
                    # Copy selection to clipboard
                    self._ahk.send('^c')  # Ctrl+C
                    time.sleep(0.2)  # Give clipboard time to update
                    
                    # Get clipboard text
                    text = pyperclip.paste()
                    
                    # Activate Supervertaler window
                    try:
                        self._ahk.win_activate('Supervertaler')
                    except Exception as e:
                        print(f"[Superlookup] win_activate error (non-critical): {e}")
                    
                    # Trigger lookup in main thread
                    if text:
                        # Use QTimer to call from main thread
                        QTimer.singleShot(0, lambda: self.on_ahk_capture(text))
                        
                except Exception as e:
                    print(f"[Superlookup] Error in hotkey callback: {e}")
            
            # Register the hotkey
            self._ahk.add_hotkey('^!l', callback=on_hotkey)  # Ctrl+Alt+L
            self._ahk.start_hotkeys()
            
            print("[Superlookup] ✓ Hotkey registered via ahk library: Ctrl+Alt+L")
            self.hotkey_registered = True
            self._using_ahk_library = True
            return True
            
        except ImportError:
            print("[Superlookup] ahk library not installed (pip install ahk)")
            return False
        except Exception as e:
            print(f"[Superlookup] ahk library method failed: {e}")
            # Clean up on failure
            if hasattr(self, '_ahk'):
                try:
                    self._ahk.stop_hotkeys()
                except:
                    pass
                self._ahk = None
            return False
    
    def _register_hotkey_external_script(self):
        """Register hotkey using external AHK script (fallback method)"""
        global _ahk_process
        self._using_ahk_library = False
        try:
            # End a copy of our script still running from before, so its
            # hotkeys don't fire twice. Never other AutoHotkey scripts.
            if os.name == 'nt':
                try:
                    stop_leftover_hotkey_scripts()
                except Exception as e:
                    print(f"[Hotkeys] Could not check for a leftover hotkey script: {e}")
            
            # Find AutoHotkey executable using shared function
            ahk_exe, source = self._find_autohotkey_executable()
            
            if not ahk_exe:
                print("[Hotkeys] AutoHotkey not found.")
                print("[Hotkeys] Global hotkeys (Ctrl+Alt+L, Shift+Shift) will not be available.")
                self.hotkey_registered = False
                # Show setup dialog (deferred to avoid blocking startup) - unless user opted out
                if self.main_window and hasattr(self.main_window, 'load_general_settings'):
                    if not (self.main_window.load_general_settings() or {}).get('hide_autohotkey_dialog', False):
                        QTimer.singleShot(2000, self._show_autohotkey_setup_dialog)
                else:
                    QTimer.singleShot(2000, self._show_autohotkey_setup_dialog)
                return
            
            print(f"[Hotkeys] Found AutoHotkey at: {ahk_exe} (source: {source})")
            
            ahk_script = _APP_DIR / HOTKEY_SCRIPT_NAME
            print(f"[Hotkeys] Looking for script at: {ahk_script}")
            print(f"[Hotkeys] Script exists: {ahk_script.exists()}")
            
            if ahk_script.exists():
                # Start AHK script in background (hidden)
                self.ahk_process = subprocess.Popen([ahk_exe, str(ahk_script)],
                                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                # Store in global variable for atexit cleanup
                _ahk_process = self.ahk_process
                print(f"[Hotkeys] AHK hotkeys registered (Ctrl+Alt+L, Shift+Shift)")
                
                # Start file watcher
                self.start_file_watcher()
                self.hotkey_registered = True
            else:
                print(f"[Hotkeys] AHK script not found: {ahk_script}")
                self.hotkey_registered = False
        except Exception as e:
            print(f"[Hotkeys] Could not start AHK hotkeys: {e}")
            self.hotkey_registered = False
    
    def start_file_watcher(self):
        """Watch for signal files from AHK (Superlookup and MT Quick Lookup)"""
        self.signal_file = _APP_DIR / "lookup_signal.txt"
        self.capture_file = _APP_DIR / "temp_capture.txt"
        self.mt_lookup_signal_file = _APP_DIR / "mt_lookup_signal.txt"

        print(f"[Superlookup] File watcher started, watching: {self.signal_file}")
        print(f"[QuickTrans] File watcher started, watching: {self.mt_lookup_signal_file}")
        
        # Create timer to check for signal file
        self.file_check_timer = QTimer()
        self.file_check_timer.timeout.connect(self.check_for_signal)
        self.file_check_timer.start(100)  # Check every 100ms
    
    def check_for_signal(self):
        """Check if AHK wrote a signal file"""
        # Check for Superlookup signal
        if self.signal_file.exists():
            print(f"[Superlookup] Signal file detected!")
            try:
                # Delete signal file
                self.signal_file.unlink()
                print(f"[Superlookup] Signal file deleted")

                # Get text from clipboard (AHK already copied it)
                time.sleep(0.1)  # Give clipboard a moment
                text = pyperclip.paste()

                # Trigger lookup
                if text:
                    self.on_ahk_capture(text)
            except Exception as e:
                print(f"[Superlookup] Error reading capture: {e}")

        # Check for MT Quick Lookup signal
        if hasattr(self, 'mt_lookup_signal_file') and self.mt_lookup_signal_file.exists():
            print(f"[QuickTrans] Signal file detected!")
            try:
                # Small delay to let AHK finish writing/close the file
                time.sleep(0.05)

                # Delete signal file with retry for file lock
                for attempt in range(3):
                    try:
                        self.mt_lookup_signal_file.unlink()
                        print(f"[QuickTrans] Signal file deleted")
                        break
                    except PermissionError:
                        if attempt < 2:
                            time.sleep(0.05)
                        else:
                            raise

                # Get text from clipboard (AHK already copied it)
                time.sleep(0.1)  # Give clipboard a moment
                text = pyperclip.paste()

                # Trigger MT Quick Lookup
                if text:
                    self.on_ahk_mt_lookup_capture(text)
            except Exception as e:
                print(f"[QuickTrans] Error reading capture: {e}")
    
    @pyqtSlot()
    def _handle_superlookup_hotkey(self):
        """Runs on Qt main thread after the global hotkey fires.

        Sends Cmd+C / Ctrl+C to the foreground app, waits for the clipboard
        to update, then dispatches to Superlookup.  Everything here runs on
        the main thread, avoiding the macOS crash caused by calling pyperclip
        or pynput from a background thread.
        """
        try:
            from modules.platform_helpers import CrossPlatformKeySender
            sender = CrossPlatformKeySender()
            sender.send_copy()
            # Read clipboard after a short delay (give OS time to process Cmd+C)
            QTimer.singleShot(250, self._read_clipboard_for_superlookup)
        except Exception as e:
            print(f"[Superlookup] Error in hotkey handler: {e}")

    def _read_clipboard_for_superlookup(self):
        """Read clipboard and dispatch to SuperLookup.

        v1.10.1 (Phase 2 of issue #199): the hotkey now bring Workbench
        forward and switches to its SuperLookup top tab, instead of
        opening Sidekick. Sidekick still works for users who summon it
        manually via Ctrl+Alt+K. Empty clipboard still triggers the
        navigation – without text we skip the auto-search and let the
        user type into the search field manually, which is the same
        visible-feedback contract Sidekick had.
        """
        text = pyperclip.paste() or ""

        mw = self.main_window
        # New path: route to Workbench's SuperLookup top tab.
        if mw and hasattr(mw, 'open_workbench_to_superlookup'):
            try:
                mw.open_workbench_to_superlookup(text)
                return
            except Exception as e:
                print(f"[Superlookup] Workbench top-tab route failed: {e}")
                # Last-ditch fallback: main-window AHK-capture handler.
                if text:
                    self.on_ahk_capture(text)

    @pyqtSlot()
    def _handle_quicktrans_hotkey(self):
        """Runs on Qt main thread after the QuickTrans global hotkey fires."""
        try:
            # If the user is working inside Workbench itself (e.g. the cursor
            # is in a target cell), skip the clipboard round-trip entirely and
            # use the in-app popup. show_mt_quick_popup() already does the
            # right thing: translate the current selection if there is one,
            # otherwise fall back to the whole source segment. The clipboard
            # flow below would instead copy nothing and reuse stale clipboard
            # text when nothing is selected – which is the bug the user hit.
            #
            # NOTE: this handler lives on SuperlookupTab, not the main window,
            # so show_mt_quick_popup() must be called on self.main_window.
            from PyQt6.QtWidgets import QApplication
            main_window = getattr(self, 'main_window', None) or self.window()
            if (QApplication.activeWindow() is not None
                    and main_window is not None
                    and hasattr(main_window, 'show_mt_quick_popup')):
                main_window.show_mt_quick_popup()
                return

            # Otherwise the hotkey came from another application (browser,
            # Trados, etc.). Capture that foreground window BEFORE we do
            # anything, then copy the user's selection to the clipboard.
            from modules.platform_helpers import CrossPlatformKeySender, get_foreground_window
            self._quicktrans_source_window = get_foreground_window()
            print(f"[QuickTrans] Captured source window: {self._quicktrans_source_window}")

            sender = CrossPlatformKeySender()
            sender.send_copy()
            QTimer.singleShot(250, self._read_clipboard_for_quicktrans)
        except Exception as e:
            print(f"[QuickTrans] Error in hotkey handler: {e}")

    def _read_clipboard_for_quicktrans(self):
        """Read clipboard and show the QuickTrans popup.

        Pre-v1.10.4 this opened Sidekick's QuickTrans sub-tab. With
        Sidekick retired, QuickTrans uses the standalone always-on-
        top popup (the same one the editor's right-click → QuickTrans
        triggers). `_quicktrans_source_window` is still captured by
        the hotkey handler so the popup's paste-back flow has the
        original app to return to.
        """
        text = pyperclip.paste() or ""
        if text:
            self.on_ahk_mt_lookup_capture(text)

    def on_ahk_capture(self, text):
        """Handle text captured by AHK"""
        try:
            print(f"[Superlookup] on_ahk_capture called with text: {text[:50]}...")

            # Bring Supervertaler to foreground using platform-native method
            # On Windows: AttachThreadInput + SetForegroundWindow (reliable)
            # On macOS: osascript
            # On Linux: wmctrl / xdotool
            from modules.platform_helpers import activate_window_by_title

            main_window = self.window()
            if main_window:
                # Restore if minimized first (Qt side)
                if main_window.isMinimized():
                    main_window.showNormal()
                elif main_window.isHidden():
                    main_window.show()

                # Use native API to bring window to foreground
                # This uses the window title, which always contains "Supervertaler"
                activate_window_by_title("Supervertaler")

                # Qt side: raise + activate as fallback
                main_window.raise_()
                main_window.activateWindow()

            # Longer delay to allow all window focus events to settle
            # Window activation triggers focus restoration which takes time
            QTimer.singleShot(250, lambda: self.show_superlookup(text))

        except Exception as e:
            print(f"[Superlookup] Error handling capture: {e}")

    def on_ahk_mt_lookup_capture(self, text):
        """Handle MT Quick Lookup text captured by AHK (Ctrl+Alt+Q)"""
        try:
            print(f"[QuickTrans] on_ahk_mt_lookup_capture called with text: {text[:50]}...")

            # Show popup directly without bringing Supervertaler to foreground
            # The popup has WindowStaysOnTopHint so it will appear over any app
            QTimer.singleShot(100, lambda: self.show_mt_quick_lookup_from_ahk(text))

        except Exception as e:
            print(f"[QuickTrans] Error handling capture: {e}")

    def show_mt_quick_lookup_from_ahk(self, text):
        """Show MT Quick Lookup popup with text from AHK capture"""
        try:
            print(f"[QuickTrans] show_mt_quick_lookup_from_ahk called with text: {text[:50]}...")

            # Get main window reference for settings access
            main_window = self.main_window
            if not main_window:
                main_window = self.window()

            if not main_window:
                print("[QuickTrans] ERROR: Could not find main window")
                return

            # Resolve direction the same way as the in-app popup: sticky manual
            # override (chosen in the popup's language selector) → else per-text
            # auto-detect → else project pair.
            if hasattr(main_window, 'resolve_quicktrans_direction'):
                source_lang, target_lang = main_window.resolve_quicktrans_direction(text)
            else:
                source_lang = getattr(main_window, 'source_language', 'English')
                target_lang = getattr(main_window, 'target_language', 'Dutch')

            # Import and show MT Quick Lookup popup
            from modules.quicktrans import MTQuickPopup

            # Create popup without Qt parent so it can appear independently over any app
            # Pass main_window as parent_app for API access
            popup = MTQuickPopup(
                parent_app=main_window,
                source_text=text,
                source_lang=source_lang,
                target_lang=target_lang,
                parent=None,  # No Qt parent - allows independent window
                external_mode=True  # Tool window: no taskbar flash
            )

            # Connect signal: when user selects a translation, copy to clipboard
            # and paste at cursor in the external app (browser, Trados, etc.)
            popup.translation_selected.connect(
                lambda translation: self._paste_translation_to_external_app(translation)
            )

            # Store reference to prevent garbage collection
            self._ahk_mt_popup = popup

            # Show popup on top – Tool window type prevents the main
            # Supervertaler icon from flashing in the taskbar
            popup.show()
            popup.raise_()
            popup.activateWindow()
            print(f"[QuickTrans] Popup shown for text: {text[:30]}...")

        except Exception as e:
            print(f"[QuickTrans] Error showing popup: {e}")
            import traceback
            traceback.print_exc()

    def show_supervertaler_assistant(self, initial_text=None, external_mode=False):
        """Navigate to the Supervertaler Sidekick (AI Assistant tab) and optionally insert text.

        Instead of a separate floating window, this reuses the existing AI Assistant
        inside the AI tab. For external mode, it also brings Supervertaler
        to the foreground first.
        """
        try:
            # Resolve main window
            main_window = self.main_window if hasattr(self, 'main_window') and self.main_window else self
            if hasattr(main_window, 'window') and main_window is not main_window.window():
                main_window = main_window.window()

            # If called from external app, bring Supervertaler to front
            if external_mode:
                main_window.show()
                main_window.raise_()
                main_window.activateWindow()

            # v1.10.161: label-based. Was hard-coded setCurrentIndex(3)
            # with stale comment about "post-v1.9.422 layout" — currently
            # correct (AI is still at index 3) but fragile. The comment
            # also still mentioned a "Tools" tab that no longer exists.
            if hasattr(main_window, '_switch_main_tab'):
                main_window._switch_main_tab("AI")
            elif hasattr(main_window, 'main_tabs'):
                main_window.main_tabs.setCurrentIndex(3)  # last-resort fallback

            # Switch to Supervertaler Sidekick sub-tab and insert text
            if hasattr(main_window, 'prompt_manager_qt') and main_window.prompt_manager_qt:
                main_window.prompt_manager_qt.receive_text_for_assistant(
                    initial_text or "", from_external=external_mode
                )

            print(f"[Assistant] Navigated to Supervertaler Sidekick (external={external_mode})")

        except Exception as e:
            print(f"[Assistant] Error navigating to assistant: {e}")
            import traceback
            traceback.print_exc()

    def _paste_translation_to_external_app(self, translation: str):
        """Copy translation to clipboard and paste at cursor in the external app.

        Called when the user selects a translation in the QuickTrans popup that
        was invoked via the global hotkey (Ctrl+Alt+Q) from another application.
        """
        try:
            print(f"[QuickTrans] Pasting translation to external app: {translation[:50]}...")

            # 1. Copy translation to clipboard
            pyperclip.copy(translation)
            print(f"[QuickTrans] Translation copied to clipboard")

            # 2. Re-activate the original app window (browser, Trados, etc.)
            #    that was captured when the hotkey was pressed
            source_window = getattr(self, '_quicktrans_source_window', None)
            if source_window:
                from modules.platform_helpers import activate_foreground_window
                activate_foreground_window(source_window)
                print(f"[QuickTrans] Re-activated source window: {source_window}")

            # 3. After a short delay (let OS process the focus switch),
            #    send Ctrl+V / Cmd+V to paste at cursor
            def _do_paste():
                try:
                    from modules.platform_helpers import CrossPlatformKeySender
                    sender = CrossPlatformKeySender()
                    sender.send_paste()
                    print(f"[QuickTrans] Ctrl+V sent to external app")
                except Exception as e:
                    print(f"[QuickTrans] Error sending paste: {e}")

            # 500ms delay: let the OS complete the focus switch before
            # sending Ctrl+V via AHK to the now-foreground app
            QTimer.singleShot(500, _do_paste)

        except Exception as e:
            print(f"[QuickTrans] Error pasting translation: {e}")

    # show_quicklauncher_external, _show_quicklauncher_external_legacy,
    # and _run_external_quicklauncher_prompt were removed in v1.10.10.
    # They were the Sidekick-era hotkey landing methods plus a QMenu
    # fallback popup. With Sidekick retired, the Ctrl+Alt+K external
    # QuickLauncher hotkey is unregistered and these handlers are
    # unreachable. The right-click QuickLauncher menu inside the
    # editor (which surfaces the same prompts) remains, served by
    # run_grid_quicklauncher_prompt on the main window.

    def set_compact_mode(self, compact=True):
        """Hide header and description to save vertical space (used in floating assistant)."""
        if hasattr(self, '_header_label'):
            self._header_label.setVisible(not compact)
        if hasattr(self, '_description_label'):
            self._description_label.setVisible(not compact)

    def show_superlookup(self, text):
        """Show Superlookup with pre-filled text"""
        try:
            print(f"[Superlookup] show_superlookup called with text: {text[:50]}...")
            
            # Get main window reference
            main_window = self.main_window
            if not main_window:
                main_window = self.window()
            
            print(f"[Superlookup] Main window found: {main_window is not None}")
            print(f"[Superlookup] Main window type: {type(main_window).__name__}")
            print(f"[Superlookup] Has main_tabs: {hasattr(main_window, 'main_tabs')}")
            
            # v1.10.161: label-based. Was hard-coded setCurrentIndex(4)
            # with a stale comment referring to a "Tools" tab that no
            # longer exists — the old "Tools" tab was retired and
            # SuperLookup got promoted to its own top-level tab. The old
            # code happened to land on SuperLookup *by coincidence*
            # because SuperLookup is now where Tools used to be (index
            # 4), but that's no comfort if the tab list shuffles again.
            if hasattr(main_window, '_switch_main_tab'):
                if main_window._switch_main_tab("SuperLookup"):
                    print(f"[Superlookup] Switched to SuperLookup tab via label lookup")
                else:
                    print(f"[Superlookup] WARNING: SuperLookup tab not found")
            elif hasattr(main_window, 'main_tabs'):
                main_window.main_tabs.setCurrentIndex(4)  # last-resort fallback
                print(f"[Superlookup] Fell back to hard-coded index 4")
            else:
                print(f"[Superlookup] WARNING: Main window has no main_tabs attribute!")
            QApplication.processEvents()  # Force GUI update
            
            # Switch to Superlookup within modules_tabs
            if hasattr(main_window, 'modules_tabs'):
                print(f"[Superlookup] Current modules_tab index: {main_window.modules_tabs.currentIndex()}")
                for i in range(main_window.modules_tabs.count()):
                    if "superlookup" in main_window.modules_tabs.tabText(i).lower():
                        main_window.modules_tabs.setCurrentIndex(i)
                        print(f"[Superlookup] Switched to Superlookup tab (index {i})")
                        QApplication.processEvents()  # Force GUI update
                        break
            
            # Delay text input and lookup to ensure tab is fully loaded
            QTimer.singleShot(100, lambda: self._fill_and_search(text))
                    
        except Exception as e:
            print(f"[Superlookup] Error showing lookup: {e}")
    
    def _fill_and_search(self, text):
        """Fill in text and trigger search (called after tab switching completes)"""
        try:
            print(f"[Superlookup] _fill_and_search called")
            # Fill in text and trigger lookup
            if hasattr(self, 'source_text'):
                self.source_text.setCurrentText(text)
                print(f"[Superlookup] Text filled in source_text field")
                # Trigger lookup by calling perform_lookup directly
                self.perform_lookup()
                print(f"[Superlookup] perform_lookup() called")
            else:
                print(f"[Superlookup] ERROR: source_text widget not found!")
        except Exception as e:
            print(f"[Superlookup] Error in _fill_and_search: {e}")
            import traceback
            traceback.print_exc()
