"""The main window's frame: building the layout, the menus, the ribbon and
quick-access toolbar, keyboard shortcuts, the progress bar, the warning
banner, the bottom dock, fonts and themes, and the log.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import os
import threading
import traceback
from PyQt6 import sip
from PyQt6.QtCore import QSize, QTimer, Qt
from PyQt6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QKeySequence,
    QTextCursor,
)
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from datetime import datetime
from modules.grid_editors import (
    _CtrlReturnEventFilter,
    _GridArrowKeyEventFilter,
    _LoneCtrlEventFilter,
    _WheelGuard,
)
from modules.help_system import Topics as HelpTopics, open_help, set_topic as set_help_topic
from modules.main_dialogs import ScratchpadDialog, ThemeEditorDialog
from modules.platform_helpers import IS_MACOS
from modules.shortcut_display import format_shortcut_for_display
from typing import Callable, Optional


class WindowShellMixin:
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

    def _widget_is_alive(self, widget: Optional[QWidget]) -> bool:
        """Return True if the underlying Qt object still exists."""
        if widget is None:
            return False
        try:
            return not sip.isdeleted(widget)
        except RuntimeError:
            return False

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
