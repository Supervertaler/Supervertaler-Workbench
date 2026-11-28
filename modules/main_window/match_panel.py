"""The Match Panel and the Compare Panel beside the grid: showing TM and MT
matches, editing TM entries from there, zoom, and inserting a match.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import re
import time
import traceback
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFont,
    QTextCharFormat,
    QTextCursor,
)
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from modules import inline_codes as _inline_codes
from modules.help_system import Topics as HelpTopics, set_topic as set_help_topic
from modules.shortcut_display import format_shortcut_for_display
from modules.statuses import STATUSES, get_status
from modules.tag_text import strip_outer_wrapping_tags
from modules.termlens_widget import TermLensWidget


class MatchPanelMixin:
    def create_assistance_panel(self):
        """Create the assistance panel widget (Translation Results) with compact resizeable design"""
        from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTabWidget
        from modules.translation_results_panel import TranslationResultsPanel
        
        tabs = QTabWidget()
        tabs.tabBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tabs.tabBar().setDrawBase(False)
        tabs.tabBar().setExpanding(False)
        tabs.setStyleSheet("QTabBar::tab { outline: 0; } QTabBar::tab:focus { outline: none; } QTabBar::tab:selected { border-bottom: 1px solid #2196F3; background-color: rgba(33, 150, 243, 0.08); }")
        tabs.setTabPosition(QTabWidget.TabPosition.South)  # Tabs at bottom for easy access
        
        # Get match limits from settings
        general_settings = self.load_general_settings()
        match_limits = general_settings.get('match_limits', {
            "LLM": 3,
            "MT": 3,
            "TM": 5,
            "Termbases": 10
        })
        
        try:
            # Create translation results panel with dynamic font sizing
            results_panel = TranslationResultsPanel(tabs, parent_app=self)
            results_panel.match_inserted.connect(self.on_match_inserted)
            tabs.addTab(results_panel, "🔍 Translation Results")
            
            # Store reference so we can update it when segments are selected
            if not hasattr(self, 'results_panels'):
                self.results_panels = []
            self.results_panels.append(results_panel)
        except ImportError as e:
            print(f"Warning: Could not import TranslationResultsPanel: {e}")
            placeholder = QWidget()
            tabs.addTab(placeholder, "🔍 Translation Results")
        
        # Tab 2: Segment Editor
        editor_widget = QWidget()
        editor_layout = QVBoxLayout(editor_widget)
        editor_layout.setContentsMargins(10, 10, 10, 10)
        
        # Segment info header
        info_layout = QHBoxLayout()
        tab_seg_info = QLabel(self.tr("Select a segment to edit"))
        tab_seg_info.setStyleSheet("font-weight: bold; font-size: 11pt;")
        info_layout.addWidget(tab_seg_info, stretch=1)

        # Status selector
        from modules.statuses import STATUSES
        status_label = QLabel(self.tr("Status:"))
        tab_status_combo = QComboBox()
        tab_status_combo.setMinimumWidth(130)  # Ensure full status text is visible
        for status_key in STATUSES.keys():
            definition = get_status(status_key)
            tab_status_combo.addItem(definition.label, status_key)
        tab_status_combo.currentIndexChanged.connect(self.on_tab_status_combo_changed)
        info_layout.addWidget(status_label)
        info_layout.addWidget(tab_status_combo)
        editor_layout.addLayout(info_layout)
        
        # Source text (read-only)
        source_label = QLabel(self.tr("Source:"))
        source_label.setStyleSheet("font-weight: bold;")
        editor_layout.addWidget(source_label)
        tab_source_editor = QTextEdit()
        tab_source_editor.setReadOnly(True)
        tab_source_editor.setMaximumHeight(100)
        tab_source_editor.setStyleSheet("")
        editor_layout.addWidget(tab_source_editor)
        
        # Target text (editable)
        target_label = QLabel(self.tr("Target:"))
        target_label.setStyleSheet("font-weight: bold;")
        editor_layout.addWidget(target_label)
        tab_target_editor = QTextEdit()
        tab_target_editor.setMaximumHeight(100)
        tab_target_editor.textChanged.connect(self.on_tab_target_change)
        editor_layout.addWidget(tab_target_editor)
        
        # Store references to THIS panel's widgets so we can update them
        editor_widget.seg_info_label = tab_seg_info
        editor_widget.source_editor = tab_source_editor
        editor_widget.target_editor = tab_target_editor
        editor_widget.status_combo = tab_status_combo
        
        # Action buttons
        button_layout = QHBoxLayout()
        copy_btn = QPushButton(self.tr("📋 Copy Source → Target"))
        copy_btn.clicked.connect(self.copy_source_to_tab_target)
        clear_btn = QPushButton(self.tr("🗑️ Clear Target"))
        clear_btn.clicked.connect(self.clear_tab_target)

        save_btn = QPushButton(self.tr("💾 Save"))
        save_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 3px 5px;")
        save_btn.clicked.connect(self.save_tab_segment)
        save_next_btn = QPushButton(f"✓ Confirm && Next ({format_shortcut_for_display('Ctrl+Enter')})")
        save_next_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 3px 5px;")
        save_next_btn.clicked.connect(self.confirm_selected_or_next)

        button_layout.addWidget(copy_btn)
        button_layout.addWidget(clear_btn)
        button_layout.addStretch()
        button_layout.addWidget(save_btn)
        button_layout.addWidget(save_next_btn)
        editor_layout.addLayout(button_layout)
        
        editor_layout.addStretch()
        tabs.addTab(editor_widget, "📝 Segment Editor")
        
        # Tab 3: Comments Editor
        notes_widget = QWidget()
        notes_layout = QVBoxLayout(notes_widget)
        notes_layout.setContentsMargins(10, 10, 10, 10)
        
        notes_header = QLabel(self.tr("Segment Comments"))
        notes_header.setStyleSheet("font-weight: bold; font-size: 11pt;")
        notes_layout.addWidget(notes_header)
        
        tab_notes_editor = QTextEdit()
        tab_notes_editor.setPlaceholderText("Add comments for this segment...\n\n"
                                            "Comments are saved per segment and can include:\n"
                                            "• Translation context\n"
                                            "• Client preferences\n"
                                            "• Terminology decisions\n"
                                            "• Questions or clarifications needed")
        tab_notes_editor.textChanged.connect(self.on_tab_notes_change)
        notes_layout.addWidget(tab_notes_editor)
        
        # Store reference to THIS panel's comments editor
        notes_widget.notes_editor = tab_notes_editor
        
        notes_button_layout = QHBoxLayout()
        save_notes_btn = QPushButton(self.tr("💾 Save Comments"))
        save_notes_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        save_notes_btn.clicked.connect(self.save_tab_notes)
        notes_button_layout.addStretch()
        notes_button_layout.addWidget(save_notes_btn)
        notes_layout.addLayout(notes_button_layout)
        
        tabs.addTab(notes_widget, "📋 Comments")
        
        # Store references to tab widgets for updates
        tabs.editor_widget = editor_widget
        tabs.notes_widget = notes_widget
        
        # Keep track of all tabbed panels
        if not hasattr(self, 'tabbed_panels'):
            self.tabbed_panels = []
        self.tabbed_panels.append(tabs)
        
        return tabs

    def on_match_selected(self, match):
        """Handle match selection from TranslationResultsPanel"""
        # This will be called when user selects a match in the panel
        # Can be used for highlighting, compare boxes, etc.
        pass

    def on_match_inserted(self, match_text: str):
        """
        Handle match insertion (user pressed Ctrl+number or Spacebar in match pane)
        Insert the match text at the cursor position in the currently selected target cell
        WITHOUT confirming the segment or moving to next
        """
        try:
            if not self.current_project or not self.table:
                return
            
            # Get current row
            row = self.table.currentRow()
            col = self.table.currentColumn()
            
            if row >= 0:
                # CRITICAL: Get segment by ID from grid, not by row index!
                id_item = self.table.item(row, 0)
                if not id_item:
                    self.log(f"⚠️ No segment ID found at row {row}")
                    return
                
                try:
                    segment_id = int(id_item.text())
                except (ValueError, AttributeError):
                    self.log(f"⚠️ Could not parse segment ID from row {row}")
                    return
                
                # Find segment by ID
                segment = next((seg for seg in self.current_project.segments if seg.id == segment_id), None)
                if not segment:
                    self.log(f"⚠️ Could not find segment with ID {segment_id}")
                    return
                
                # Get the target cell widget (EditableGridTextEditor)
                target_widget = self.table.cellWidget(row, 3)  # Column 3 is target
                
                if target_widget and isinstance(target_widget, QTextEdit):
                    # Capture pre-edit state for undo
                    _undo_old_target = segment.target
                    _undo_old_status = segment.status
                    # Insert text at cursor position
                    cursor = target_widget.textCursor()
                    cursor.insertText(match_text)

                    # Update the segment data
                    new_target = target_widget.toPlainText()
                    # v1.9.306: Strip invisible markers before saving to segment data
                    if hasattr(self, 'reverse_invisible_replacements'):
                        new_target = self.reverse_invisible_replacements(new_target)
                    segment.target = new_target
                    try:
                        self.record_undo_state(segment.id, _undo_old_target, new_target, _undo_old_status, _undo_old_status)
                    except Exception:
                        pass  # never let undo failure break the actual operation

                    # Set focus back to the target editor
                    target_widget.setFocus()

                    self.log(f"✓ Match inserted into segment {segment.id} at cursor position")
                elif col == 3:
                    # Fallback: If no widget exists, create one or set text directly
                    _undo_old_target = segment.target
                    _undo_old_status = segment.status
                    segment.target = match_text
                    try:
                        self.record_undo_state(segment.id, _undo_old_target, match_text, _undo_old_status, _undo_old_status)
                    except Exception:
                        pass  # never let undo failure break the actual operation

                    # Try to update via cellWidget first
                    if target_widget:
                        target_widget.setPlainText(match_text)

                    self.log(f"✓ Match inserted into segment {row + 1}")
                else:
                    self.log(f"⚠ Please click on the target cell first to insert match")
            
        except Exception as e:
            self.log(f"Error inserting match: {e}")
            import traceback
            traceback.print_exc()

    def _create_compare_panel(self) -> QWidget:
        """Create the Compare Panel tab with 4 comparison boxes:
        Current Source, Machine Translation, TM Source, TM Target
        With navigation arrows for multiple matches.
        """
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        
        # Get theme colors
        if hasattr(self, 'theme_manager') and self.theme_manager:
            theme = self.theme_manager.current_theme
            # Check if theme is dark by checking the theme name or a dark attribute
            is_dark = getattr(theme, 'is_dark', 'dark' in theme.name.lower())
            border_color = theme.border
            title_color = theme.text_disabled
            text_color = theme.text
            # Use theme-appropriate colors for boxes
            box1_bg = theme.panel_info if hasattr(theme, 'panel_info') else ("#2d3748" if is_dark else "#e3f2fd")
            box2_bg = theme.panel_warning if hasattr(theme, 'panel_warning') else ("#3d3520" if is_dark else "#fff3cd")
            box3_bg = theme.panel_success if hasattr(theme, 'panel_success') else ("#1e3a2f" if is_dark else "#d4edda")
            box4_bg = theme.panel_neutral if hasattr(theme, 'panel_neutral') else ("#2d2d3d" if is_dark else "#e8e8f0")
        else:
            is_dark = False
            border_color = "#ddd"
            title_color = "#666"
            text_color = "#333"
            box1_bg = "#e3f2fd"
            box2_bg = "#fff3cd"
            box3_bg = "#d4edda"
            box4_bg = "#e8e8f0"
        
        # Header with segment info
        header_layout = QHBoxLayout()
        self.compare_panel_segment_label = QLabel(self.tr("Segment: -"))
        self.compare_panel_segment_label.setStyleSheet(f"font-weight: bold; font-size: 11px; color: {title_color};")
        header_layout.addWidget(self.compare_panel_segment_label)
        header_layout.addStretch()
        layout.addLayout(header_layout)
        
        # Store text edits and navigation state for theme updates
        self.compare_panel_text_edits = []
        self.compare_panel_mt_matches = []  # List of MT matches
        self.compare_panel_tm_matches = []  # List of TM matches
        self.compare_panel_mt_index = 0     # Current MT match index
        self.compare_panel_tm_index = 0     # Current TM match index
        
        # Box 1: Current Source (blue-ish) - no navigation needed
        box1_container, self.compare_panel_current_source, _, _ = self._create_compare_panel_box(
            "📝 Current Source", box1_bg, text_color, border_color, has_navigation=False)
        layout.addWidget(box1_container, 1)
        
        # Box 2: Machine Translation (purple-ish) - with navigation
        box2_container, self.compare_panel_mt, self.compare_panel_mt_nav_label, mt_nav_btns = self._create_compare_panel_box(
            "🤖 MT", box4_bg, text_color, border_color, has_navigation=True, shortcut_badge_text="0", shortcut_badge_tooltip="Alt+0")
        if mt_nav_btns:
            mt_nav_btns[0].clicked.connect(lambda: self._compare_panel_nav_mt(-1))
            mt_nav_btns[1].clicked.connect(lambda: self._compare_panel_nav_mt(1))
        layout.addWidget(box2_container, 1)
        
        # Box 3: TM Source (yellow-ish) - with navigation
        box3_container, self.compare_panel_tm_source, self.compare_panel_tm_nav_label, tm_nav_btns = self._create_compare_panel_box(
            "📚 TM Source", box2_bg, text_color, border_color, has_navigation=True)
        if tm_nav_btns:
            tm_nav_btns[0].clicked.connect(lambda: self._compare_panel_nav_tm(-1))
            tm_nav_btns[1].clicked.connect(lambda: self._compare_panel_nav_tm(1))
        layout.addWidget(box3_container, 1)
        
        # Box 4: TM Target (green-ish) - syncs with TM Source navigation
        box4_container, self.compare_panel_tm_target, self.compare_panel_tm_target_label, _ = self._create_compare_panel_box(
            "✅ TM Target", box3_bg, text_color, border_color, has_navigation=False, show_metadata_label=True,
            shortcut_badge_text="00", shortcut_badge_tooltip="Alt+0,0")
        layout.addWidget(box4_container, 1)
        
        return widget

    def _create_match_panel(self) -> QWidget:
        """Create Match Panel with TermLens + TM Source/Target boxes."""
        widget = QWidget()
        main_layout = QVBoxLayout(widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Initialize compare_panel_text_edits if not exists (needed for _create_compare_panel_box)
        if not hasattr(self, 'compare_panel_text_edits'):
            self.compare_panel_text_edits = []
        
        # Vertical splitter: TermLens (top) | TM boxes (bottom)
        splitter = QSplitter(Qt.Orientation.Vertical)
        
        # TOP: Container with header + TermLens
        termlens_container = QWidget()
        termlens_layout = QVBoxLayout(termlens_container)
        termlens_layout.setContentsMargins(4, 4, 4, 0)
        termlens_layout.setSpacing(2)
        
        # TermLens header row: label (left) + tip (right)
        if hasattr(self, 'theme_manager') and self.theme_manager:
            header_color = self.theme_manager.current_theme.text_disabled
        else:
            header_color = "#666"

        termlens_header_row = QHBoxLayout()
        termlens_header_row.setContentsMargins(0, 0, 0, 0)
        termlens_header_row.setSpacing(0)

        termlens_header = QLabel(self.tr("📖 TermLens"))
        termlens_header.setStyleSheet(f"font-weight: bold; font-size: 9pt; color: {header_color};")
        termlens_header_row.addWidget(termlens_header)

        termlens_header_row.addStretch()

        termlens_tip = QLabel(self.tr("💡 Tip: Press F5 to refresh if matches are missing"))
        termlens_tip.setStyleSheet(f"font-size: 9pt; color: {header_color}; font-style: italic;")
        termlens_header_row.addWidget(termlens_tip)

        termlens_layout.addLayout(termlens_header_row)
        
        # Third TermLens instance for Match Panel
        self.termlens_widget_match = TermLensWidget(self, db_manager=self.db_manager, log_callback=self.log, theme_manager=self.theme_manager)
        set_help_topic(self.termlens_widget_match, HelpTopics.GLOSSARY_TERMLENS)
        # Connect TermLens signals
        self.termlens_widget_match.term_insert_requested.connect(self.insert_termlens_text)
        self.termlens_widget_match.edit_entry_requested.connect(self._on_termlens_edit_entry)
        self.termlens_widget_match.delete_entry_requested.connect(self._on_termlens_delete_entry)
        # v1.10.68: refresh button on the Match-panel TermLens too —
        # both instances share the same backing index, so the handler
        # is the same.
        self.termlens_widget_match.refresh_requested.connect(self._on_termlens_refresh_requested)
        self.termlens_widget_match.font_size_changed.connect(self._on_termlens_font_size_changed)
        # Apply font settings
        font_settings = self.load_general_settings()
        termlens_family = font_settings.get('termlens_font_family', 'Segoe UI')
        termlens_size = font_settings.get('termlens_font_size', 10)
        termlens_bold = font_settings.get('termlens_font_bold', False)
        self.termlens_widget_match.set_font_settings(termlens_family, termlens_size, termlens_bold)
        termlens_layout.addWidget(self.termlens_widget_match)

        # Wrap the TermLens section + a QuickTrans panel in a tab pair, so the
        # user can have TermLens below the grid and QuickTrans here (or vice
        # versa). Mirrors the under-grid TermLens | QuickTrans dock.
        from PyQt6.QtWidgets import QTabWidget as _QTabWidget
        self.match_top_tabs = _QTabWidget()
        self.match_top_tabs.addTab(termlens_container, "📖 TermLens")
        try:
            from modules.quicktrans import QuickTransPanel
            self.quicktrans_panel_match = QuickTransPanel(self)
            self.quicktrans_panel_match.translation_selected.connect(self._insert_quicktrans_translation)
            self.match_top_tabs.addTab(self.quicktrans_panel_match, "⚡ QuickTrans")
            self.match_top_tabs.currentChanged.connect(self._on_match_top_tab_changed)
        except Exception as e:
            self.quicktrans_panel_match = None
            self.log(f"Could not create Match Panel QuickTrans panel: {e}")

        splitter.addWidget(self.match_top_tabs)
        
        # BOTTOM: Container for TM Source + TM Target boxes
        # v1.9.307: Store references for layout switching (horizontal ↔ vertical)
        # Default layout matches self.match_panel_tm_vertical (True = vertical)
        self._tm_container = QWidget()
        if getattr(self, 'match_panel_tm_vertical', True):
            self._tm_layout = QVBoxLayout(self._tm_container)
        else:
            self._tm_layout = QHBoxLayout(self._tm_container)
        self._tm_layout.setContentsMargins(0, 0, 0, 0)
        self._tm_layout.setSpacing(0)

        # Get theme-aware colors for TM boxes (same as Compare Panel)
        if hasattr(self, 'theme_manager') and self.theme_manager:
            theme = self.theme_manager.current_theme
            is_dark = getattr(theme, 'is_dark', 'dark' in theme.name.lower())
            border_color = theme.border
            text_color = theme.text
            # Green background - theme-appropriate shade
            tm_box_bg = theme.panel_success if hasattr(theme, 'panel_success') else ("#1e3a2f" if is_dark else "#d4edda")
        else:
            tm_box_bg = "#d4edda"    # Green (light mode)
            text_color = "#333"
            border_color = "#ddd"

        # TM Source box (GREEN, with navigation)
        self.match_panel_tm_matches = []  # Separate match list
        self.match_panel_tm_index = 0     # Separate navigation index

        self._tm_source_container, self.match_panel_tm_source, self.match_panel_tm_nav_label, tm_nav_btns = self._create_compare_panel_box(
            "📚 TM Source", tm_box_bg, text_color, border_color, has_navigation=True,
            add_fuzzy_fixer_button=True)
        if tm_nav_btns:
            tm_nav_btns[0].clicked.connect(lambda: self._match_panel_nav_tm(-1))
            tm_nav_btns[1].clicked.connect(lambda: self._match_panel_nav_tm(1))
        # Right-click context menu for TM Source
        self.match_panel_tm_source.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.match_panel_tm_source.customContextMenuRequested.connect(self._match_panel_tm_context_menu)
        self._tm_layout.addWidget(self._tm_source_container, 1)

        # TM Target box (GREEN, with metadata and shortcut badge)
        self._tm_target_container, self.match_panel_tm_target, self.match_panel_tm_target_label, _ = self._create_compare_panel_box(
            "✅ TM Target", tm_box_bg, text_color, border_color, has_navigation=False, show_metadata_label=True,
            shortcut_badge_text="0", shortcut_badge_tooltip="Alt+0")
        # Right-click context menu for TM Target
        self.match_panel_tm_target.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.match_panel_tm_target.customContextMenuRequested.connect(self._match_panel_tm_context_menu)
        self._tm_layout.addWidget(self._tm_target_container, 1)

        # Force stylesheet on the tm_container itself (the parent widget holding both boxes)
        self._tm_container.setStyleSheet("background-color: transparent;")

        splitter.addWidget(self._tm_container)

        # Set initial splitter sizes (60% TermLens, 40% TM boxes)
        splitter.setSizes([600, 400])

        main_layout.addWidget(splitter)


        return widget

    def _toggle_match_panel_tm_layout(self, save=True):
        """Toggle TM Source / TM Target panes between horizontal (side-by-side)
        and vertical (stacked) layout in the Match Panel.

        v1.9.307: Accessible via right-click context menu and View > Match Panel menu.

        Args:
            save: If True, persist the preference to general_settings.json.
                  Set to False during startup restore to avoid redundant writes.
        """
        from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout

        if not hasattr(self, '_tm_container') or not hasattr(self, '_tm_source_container'):
            return

        # Remove widgets from current layout (keeps widget alive)
        self._tm_layout.removeWidget(self._tm_source_container)
        self._tm_layout.removeWidget(self._tm_target_container)

        # Delete the old layout by orphaning it onto a temporary widget
        QWidget().setLayout(self._tm_container.layout())

        # Flip the orientation
        self.match_panel_tm_vertical = not self.match_panel_tm_vertical

        # Create new layout with the desired orientation
        if self.match_panel_tm_vertical:
            self._tm_layout = QVBoxLayout(self._tm_container)
        else:
            self._tm_layout = QHBoxLayout(self._tm_container)
        self._tm_layout.setContentsMargins(0, 0, 0, 0)
        self._tm_layout.setSpacing(0)

        # Re-add widgets in the same order
        self._tm_layout.addWidget(self._tm_source_container, 1)
        self._tm_layout.addWidget(self._tm_target_container, 1)

        # Sync View menu checkbox
        if hasattr(self, 'match_panel_vertical_action'):
            self.match_panel_vertical_action.setChecked(self.match_panel_tm_vertical)

        # Persist preference
        if save:
            try:
                settings = self._load_general_settings_from_file()
                settings['match_panel_tm_vertical'] = self.match_panel_tm_vertical
                self.save_general_settings(settings)
            except Exception:
                pass

    def _match_panel_nav_tm(self, direction: int):
        """Navigate TM matches in Match Panel (-1 = prev, 1 = next)"""
        if not self.match_panel_tm_matches:
            return
        
        new_index = self.match_panel_tm_index + direction
        if 0 <= new_index < len(self.match_panel_tm_matches):
            self.match_panel_tm_index = new_index
            self._update_match_panel_tm_display()

    def _update_match_panel_tm_display(self):
        """Update Match Panel TM Source and TM Target display with current match"""
        if not self.match_panel_tm_matches:
            self.match_panel_tm_source.setPlainText("(No TM match)")
            self.match_panel_tm_target.setPlainText("(No TM match)")
            if hasattr(self, 'match_panel_tm_nav_label') and self.match_panel_tm_nav_label:
                self.match_panel_tm_nav_label.setText("(0/0)")
            if hasattr(self, 'match_panel_tm_target_label') and self.match_panel_tm_target_label:
                self.match_panel_tm_target_label.setText("")
            self._update_fuzzy_fixer_btn_state(None)
            return
        
        match = self.match_panel_tm_matches[self.match_panel_tm_index]
        total = len(self.match_panel_tm_matches)
        idx = self.match_panel_tm_index + 1

        # Support both dict and TranslationMatch objects
        if isinstance(match, dict):
            tm_source_text = match.get('source', '')
            target_text = match.get('target', '')
            tm_name = match.get('tm_name', 'Unknown TM')
            match_pct = match.get('match_pct', 0)
            reverse_match = match.get('reverse_match', False)
        else:
            tm_source_text = getattr(match, 'compare_source', '') or getattr(match, 'source', '')
            target_text = getattr(match, 'target', '')
            md = match.metadata if hasattr(match, 'metadata') and match.metadata else {}
            tm_name = md.get('tm_name', 'Unknown TM')
            match_pct = getattr(match, 'relevance', 0)
            reverse_match = bool(md.get('reverse_match', False))

        # Update navigation label
        if hasattr(self, 'match_panel_tm_nav_label') and self.match_panel_tm_nav_label:
            nav_html = f"(<span style='font-size:8px'>{idx}/{total}</span>)"
            self.match_panel_tm_nav_label.setText(nav_html)

        # Enable the FuzzyFixer button only when this match is in the configured range
        self._update_fuzzy_fixer_btn_state(match_pct)

        # Update TM Source text with diff highlighting
        # Strip any stale invisible markers from TM data before processing
        current_source = getattr(self, 'match_panel_current_source', '')
        clean_tm_source = tm_source_text
        if clean_tm_source and hasattr(self, 'reverse_invisible_replacements'):
            clean_tm_source = self.reverse_invisible_replacements(clean_tm_source)
        if clean_tm_source and current_source:
            self._set_compare_panel_text_with_diff(self.match_panel_tm_source, current_source, clean_tm_source)
        else:
            # Apply invisible replacements for display (e.g. ↵ for line breaks)
            tm_src_display = clean_tm_source or "(No TM match)"
            if clean_tm_source and hasattr(self, 'apply_invisible_replacements'):
                tm_src_display = self.apply_invisible_replacements(tm_src_display)
            self.match_panel_tm_source.setPlainText(tm_src_display)

        # A match that differs only in its inline codes gets the current
        # source's codes ({PK}{MN} → {PKMN}; issue #194)
        if target_text and current_source and tm_source_text:
            adapted = _inline_codes.adapt_codes(tm_source_text, target_text, current_source)
            if adapted is not None:
                target_text = adapted

        # Update TM Target text (no diff highlighting needed for target)
        # Strip any stale invisible markers first (in case TM data was saved with markers),
        # then re-apply current invisible display settings for clean rendering
        target_display = target_text if target_text else ""
        if target_display and hasattr(self, 'reverse_invisible_replacements'):
            target_display = self.reverse_invisible_replacements(target_display)
        if target_display and hasattr(self, 'apply_invisible_replacements'):
            target_display = self.apply_invisible_replacements(target_display)
        self.match_panel_tm_target.setPlainText(target_display)

        # Update metadata label (TM name, percentage, optional reverse-match chip)
        if hasattr(self, 'match_panel_tm_target_label') and self.match_panel_tm_target_label:
            # v1.10.50: append a small "⇄ reversed" chip when the match came
            # from a TM whose source/target languages are the inverse of this
            # project's. The chip uses a muted amber colour so it reads as
            # "informational, not alarming." Tooltip on the label spells it
            # out for users who don't recognise the symbol.
            reverse_chip = ""
            if reverse_match:
                reverse_chip = (
                    " <span style='font-size:8px; color:#b45309; "
                    "background-color:rgba(245,158,11,0.18); "
                    "padding:1px 5px; border-radius:6px;'>⇄ reversed</span>"
                )
                # v1.10.52: wrap tooltip text in HTML with explicit colours
                # so it survives Windows-11 OS-level tooltip rendering, which
                # in dark mode ignores both the per-widget QToolTip stylesheet
                # and the QPalette ToolTipBase/Text roles. Inline HTML in the
                # tooltip text itself is always honoured.
                self.match_panel_tm_target_label.setToolTip(
                    "<div style='background-color:#1f2937; color:#f9fafb; "
                    "padding:6px 8px; max-width:360px;'>"
                    "This match comes from a TM whose source/target languages "
                    "are the inverse of this project. The match is from the "
                    "TM's target column; Workbench has swapped fields so it "
                    "displays normally."
                    "</div>"
                )
            else:
                self.match_panel_tm_target_label.setToolTip("")
            metadata_html = (
                f"<span style='font-size:10px'>{tm_name}</span> "
                f"(<span style='font-size:8px'>{match_pct}%</span>)"
                f"{reverse_chip}"
            )
            self.match_panel_tm_target_label.setText(metadata_html)

    def _match_panel_tm_context_menu(self, pos):
        """Right-click context menu for the TM Source / TM Target boxes in the Match Panel."""
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QAction

        sender = self.sender()
        menu = sender.createStandardContextMenu()

        # Fix blurry context menu rendering (high-DPI / transparent background inheritance)
        menu.setStyleSheet("""
            QMenu {
                background-color: palette(window);
                border: 1px solid palette(mid);
            }
            QMenu::item {
                padding: 4px 20px;
            }
            QMenu::item:selected {
                background-color: palette(highlight);
                color: palette(highlighted-text);
            }
            QMenu::separator {
                height: 1px;
                background: palette(mid);
                margin: 4px 8px;
            }
        """)

        # v1.9.307: Layout toggle – always available (even with no TM matches)
        menu.addSeparator()
        is_vertical = getattr(self, 'match_panel_tm_vertical', True)
        layout_action = QAction("↕ Stack TM Source / Target Vertically", menu)
        layout_action.setCheckable(True)
        layout_action.setChecked(is_vertical)
        layout_action.triggered.connect(lambda _checked: self._toggle_match_panel_tm_layout())
        menu.addAction(layout_action)

        if not self.match_panel_tm_matches:
            menu.exec(sender.mapToGlobal(pos))
            return

        match = self.match_panel_tm_matches[self.match_panel_tm_index]

        # Support both dict and TranslationMatch objects
        if isinstance(match, dict):
            tm_id = match.get('tm_id', '')
            tm_name = match.get('tm_name', '') or tm_id or 'TM'
        else:
            # TranslationMatch object
            tm_id = match.metadata.get('tm_id', '') if hasattr(match, 'metadata') and match.metadata else ''
            tm_name = match.metadata.get('tm_name', '') if hasattr(match, 'metadata') and match.metadata else ''
            tm_name = tm_name or tm_id or 'TM'

        menu.addSeparator()
        edit_action = QAction(f"✏️ Edit TM Entry ({tm_name})", menu)
        edit_action.triggered.connect(self._match_panel_edit_tm_entry)
        if not tm_id:
            edit_action.setEnabled(False)
            edit_action.setText(self.tr("✏️ Edit TM Entry (no TM ID available)"))
        menu.addAction(edit_action)

        delete_action = QAction(f"🗑️ Delete TM Entry ({tm_name})", menu)
        delete_action.triggered.connect(self._match_panel_delete_tm_entry)
        if not tm_id:
            delete_action.setEnabled(False)
            delete_action.setText(self.tr("🗑️ Delete TM Entry (no TM ID available)"))
        menu.addAction(delete_action)

        menu.exec(sender.mapToGlobal(pos))

    def _get_match_panel_tm_fields(self, match):
        """Extract tm_id, tm_name, source, target from a Match Panel match (dict or TranslationMatch)."""
        if isinstance(match, dict):
            return (match.get('tm_id', ''),
                    match.get('tm_name', '') or match.get('tm_id', '') or 'TM',
                    match.get('source', ''),
                    match.get('target', ''))
        else:
            md = match.metadata if hasattr(match, 'metadata') and match.metadata else {}
            return (md.get('tm_id', ''),
                    md.get('tm_name', '') or md.get('tm_id', '') or 'TM',
                    getattr(match, 'source', ''),
                    getattr(match, 'target', ''))

    def _match_panel_edit_tm_entry(self):
        """Edit the TM entry currently displayed in the Match Panel."""
        if not self.match_panel_tm_matches:
            return

        from modules.tm_entry_actions import edit_tm_entry

        match = self.match_panel_tm_matches[self.match_panel_tm_index]
        tm_id, tm_name, old_source, old_target = self._get_match_panel_tm_fields(match)
        edited = edit_tm_entry(self, tm_id, tm_name, old_source, old_target,
                               reverse=self._match_panel_is_reverse(match))
        if edited is None:
            return

        # Show the change at once; the cache was dropped, so the next visit
        # to the segment looks the entry up again.
        if isinstance(match, dict):
            match['source'], match['target'] = edited
        else:
            match.source, match.target = edited
            match.compare_source = edited[0]
        self._update_match_panel_tm_display()

    @staticmethod
    def _match_panel_is_reverse(match) -> bool:
        """True when the match shows a TM entry found in the other direction."""
        if isinstance(match, dict):
            return bool(match.get('reverse_match', False))
        return bool((getattr(match, 'metadata', None) or {}).get('reverse_match', False))

    def _match_panel_delete_tm_entry(self):
        """Delete the TM entry currently displayed in the Match Panel."""
        if not self.match_panel_tm_matches:
            return

        from modules.tm_entry_actions import delete_tm_entry

        match = self.match_panel_tm_matches[self.match_panel_tm_index]
        tm_id, tm_name, source_text, target_text = self._get_match_panel_tm_fields(match)
        if not delete_tm_entry(self, tm_id, tm_name, source_text, target_text,
                               reverse=self._match_panel_is_reverse(match)):
            return

        # Remove from the in-memory list and update display
        self.match_panel_tm_matches.pop(self.match_panel_tm_index)
        # Clamp index in case we deleted the last item
        if self.match_panel_tm_index >= len(self.match_panel_tm_matches):
            self.match_panel_tm_index = max(0, len(self.match_panel_tm_matches) - 1)
        self._update_match_panel_tm_display()

    def _create_compare_panel_box(self, label: str, bg_color: str, text_color: str, border_color: str,
                                   has_navigation: bool = False, show_metadata_label: bool = False,
                                   shortcut_badge_text: str = None, shortcut_badge_tooltip: str = None,
                                   add_fuzzy_fixer_button: bool = False) -> tuple:
        """Create a single comparison box for the Compare Panel
        
        Returns: (container, text_edit, nav_label, nav_buttons) where nav_buttons is [prev_btn, next_btn] or None
        """
        container = QFrame()
        container.setStyleSheet(f"""
            QFrame {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 4px;
            }}
        """)
        
        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(6, 4, 6, 6)
        main_layout.setSpacing(2)
        
        # Header row with label, navigation, and metadata
        header_layout = QHBoxLayout()
        header_layout.setSpacing(4)
        
        # Title label
        title_label = QLabel(label)
        title_label.setStyleSheet(f"font-weight: bold; font-size: 9pt; color: {text_color}; background: transparent; border: none;")
        header_layout.addWidget(title_label)

        # Optional shortcut badge (TermLens-style blue bubble)
        if shortcut_badge_text:
            badge_width = 14 if len(shortcut_badge_text) == 1 else 20
            badge_label = QLabel(shortcut_badge_text)
            badge_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge_label.setFixedSize(badge_width, 14)
            badge_label.setStyleSheet("""
                QLabel {
                    background-color: #1976D2;
                    color: white;
                    border-radius: 7px;
                    font-size: 9px;
                    font-weight: bold;
                    padding: 0px;
                }
            """)
            if shortcut_badge_tooltip:
                badge_label.setToolTip(
                    f"Press {format_shortcut_for_display(shortcut_badge_tooltip)} to insert"
                )
            header_layout.addWidget(badge_label)

        # Optional FuzzyFixer button (only the Match Panel TM Source box uses this)
        if add_fuzzy_fixer_button:
            from PyQt6.QtWidgets import QPushButton as _QPushButton
            ff_btn = _QPushButton("🔧 FuzzyFixer")
            ff_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            ff_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            ff_btn.setToolTip(self.tr(
                "FuzzyFixer: ask the AI to adapt this fuzzy TM match to the "
                f"current source ({format_shortcut_for_display('Ctrl+Alt+F')})"))
            ff_btn.setStyleSheet("""
                QPushButton {
                    font-size: 8pt;
                    font-weight: bold;
                    padding: 1px 8px;
                    background-color: #FF7043;
                    color: white;
                    border: none;
                    border-radius: 3px;
                }
                QPushButton:hover { background-color: #F4511E; }
                QPushButton:disabled { background-color: #BDBDBD; color: #EEEEEE; }
                QPushButton:focus { outline: none; }
            """)
            ff_btn.clicked.connect(self.fuzzy_fix_current_segment)
            ff_btn.setEnabled(False)  # Enabled once an in-range match is shown
            self.match_panel_fuzzy_fixer_btn = ff_btn
            header_layout.addWidget(ff_btn)

        # Add stretch to push navigation controls to the right (aligned with scrollbar)
        header_layout.addStretch()

        nav_label = None
        nav_buttons = None

        if has_navigation or show_metadata_label:
            # Navigation: (1/3) ◄ ► provider_name • 95%
            nav_label = QLabel("(0/0)")
            nav_label.setStyleSheet(f"font-size: 9pt; color: {text_color}; background: transparent; border: none;")
            # Store color so later rich-text updates can preserve theme color
            nav_label._compare_text_color = text_color
            header_layout.addWidget(nav_label)

        if has_navigation:
            # Detect theme for arrow color
            is_dark_theme = hasattr(self, 'theme_manager') and self.theme_manager and 'dark' in self.theme_manager.current_theme.name.lower()
            arrow_color = "#FFFFFF" if is_dark_theme else "#333333"

            # Create clickable label class with theme update capability
            from PyQt6.QtCore import pyqtSignal

            class ClickableArrow(QLabel):
                clicked = pyqtSignal()

                def __init__(self, arrow_symbol, parent=None):
                    """arrow_symbol: '◀' or '▶'"""
                    self.arrow_symbol = arrow_symbol
                    super().__init__("", parent)
                    self.setCursor(Qt.CursorShape.PointingHandCursor)

                def set_color(self, color):
                    """Update arrow color for current theme"""
                    self.setStyleSheet(f"""
                        QLabel {{
                            color: {color};
                            background: transparent;
                            border: none;
                            font-size: 11px;
                            font-weight: bold;
                        }}
                    """)
                    self.setText(self.arrow_symbol)

                def mousePressEvent(self, event):
                    self.clicked.emit()
                    super().mousePressEvent(event)

            # Prev arrow - using ◀
            prev_btn = ClickableArrow("◀")
            prev_btn.set_color(arrow_color)
            prev_btn.setFixedSize(16, 16)
            prev_btn.setAlignment(Qt.AlignmentFlag.AlignCenter)
            header_layout.addWidget(prev_btn)

            # Next arrow - using ▶
            next_btn = ClickableArrow("▶")
            next_btn.set_color(arrow_color)
            next_btn.setFixedSize(16, 16)
            next_btn.setAlignment(Qt.AlignmentFlag.AlignCenter)
            header_layout.addWidget(next_btn)

            # Store reference for theme updates
            if not hasattr(self, 'theme_aware_arrows'):
                self.theme_aware_arrows = []
            self.theme_aware_arrows.extend([prev_btn, next_btn])

            nav_buttons = [prev_btn, next_btn]

        main_layout.addLayout(header_layout)
        
        # Text area
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        _mp_font_size = type(self).match_panel_font_size  # Use live value so saved zoom is respected
        text_edit.setStyleSheet(f"""
            QTextEdit {{
                background-color: transparent;
                border: none;
                font-size: {_mp_font_size}px;
                padding: 2px;
                color: {text_color};
            }}
        """)
        # Also set document default font so rich-text content (cursor-inserted diffs) respects size
        from PyQt6.QtGui import QFont as _QFont
        _doc_font = _QFont()
        if type(self).match_panel_font_family:
            _doc_font.setFamily(type(self).match_panel_font_family)
        _doc_font.setPointSize(_mp_font_size)
        _doc_font.setBold(type(self).match_panel_font_bold)
        text_edit.document().setDefaultFont(_doc_font)
        text_edit.setMinimumHeight(50)
        main_layout.addWidget(text_edit, 1)
        
        # Track for font updates and theming
        self.compare_panel_text_edits.append((text_edit, title_label, container, bg_color))
        
        return container, text_edit, nav_label, nav_buttons

    def _compare_panel_nav_mt(self, direction: int):
        """Navigate MT matches in Compare Panel (-1 = prev, 1 = next)"""
        if not self.compare_panel_mt_matches:
            return
        
        new_index = self.compare_panel_mt_index + direction
        if 0 <= new_index < len(self.compare_panel_mt_matches):
            self.compare_panel_mt_index = new_index
            self._update_compare_panel_mt_display()

    def _compare_panel_nav_tm(self, direction: int):
        """Navigate TM matches in Compare Panel (-1 = prev, 1 = next)"""
        if not self.compare_panel_tm_matches:
            return
        
        new_index = self.compare_panel_tm_index + direction
        if 0 <= new_index < len(self.compare_panel_tm_matches):
            self.compare_panel_tm_index = new_index
            self._update_compare_panel_tm_display()

    def _update_compare_panel_mt_display(self):
        """Update MT section display with current match"""
        if not self.compare_panel_mt_matches:
            self.compare_panel_mt.setPlainText("(No MT available)")
            if hasattr(self, 'compare_panel_mt_nav_label') and self.compare_panel_mt_nav_label:
                self.compare_panel_mt_nav_label.setText("(0/0)")
            return
        
        match = self.compare_panel_mt_matches[self.compare_panel_mt_index]
        total = len(self.compare_panel_mt_matches)
        
        # Update navigation label with emphasis:
        # - Provider name: larger (readability)
        try:
            import html
            provider_escaped = html.escape(str(match.get('provider', 'MT')))
        except Exception:
            provider_escaped = str(match.get('provider', 'MT'))

        idx = self.compare_panel_mt_index + 1
        nav_html = (
            f"(<span style='font-size:8px'>{idx}/{total}</span>) "
            f"<span style='font-size:10px'>{provider_escaped}</span>"
        )

        if hasattr(self, 'compare_panel_mt_nav_label') and self.compare_panel_mt_nav_label:
            # Avoid a fixed font-size stylesheet overriding rich text emphasis, but keep theme color
            label_color = getattr(self.compare_panel_mt_nav_label, '_compare_text_color', None)
            if label_color:
                self.compare_panel_mt_nav_label.setStyleSheet(
                    f"color: {label_color}; background: transparent; border: none;"
                )
            else:
                self.compare_panel_mt_nav_label.setStyleSheet("background: transparent; border: none;")
            self.compare_panel_mt_nav_label.setText(nav_html)
        
        # Update text
        self.compare_panel_mt.setPlainText(match.get('translation', ''))

    def _update_compare_panel_tm_display(self):
        """Update TM Source and TM Target display with current match"""
        if not self.compare_panel_tm_matches:
            self.compare_panel_tm_source.setPlainText("(No TM match)")
            self.compare_panel_tm_target.setPlainText("(No TM match)")
            if hasattr(self, 'compare_panel_tm_nav_label') and self.compare_panel_tm_nav_label:
                self.compare_panel_tm_nav_label.setText("(0/0)")
            if hasattr(self, 'compare_panel_tm_target_label') and self.compare_panel_tm_target_label:
                self.compare_panel_tm_target_label.setText("(0/0)")
            return
        
        match = self.compare_panel_tm_matches[self.compare_panel_tm_index]
        total = len(self.compare_panel_tm_matches)

        # Get metadata
        tm_name = match.get('tm_name', 'TM')
        match_pct = match.get('match_pct', 0)
        reverse_match = bool(match.get('reverse_match', False))

        # Update metadata labels with emphasis:
        # - TM name: larger
        # - Match %: larger + bold
        try:
            import html
            tm_name_escaped = html.escape(str(tm_name))
        except Exception:
            tm_name_escaped = str(tm_name)

        idx = self.compare_panel_tm_index + 1
        try:
            match_pct_display = int(match_pct)
        except Exception:
            match_pct_display = match_pct

        # v1.10.50: optional "⇄ reversed" chip when the match came from a TM
        # whose source/target languages are the inverse of this project's.
        reverse_chip = ""
        if reverse_match:
            reverse_chip = (
                " <span style='font-size:8px; color:#b45309; "
                "background-color:rgba(245,158,11,0.18); "
                "padding:1px 5px; border-radius:6px;'>⇄ reversed</span>"
            )

        nav_html = (
            f"(<span style='font-size:8px'>{idx}/{total}</span>) "
            f"<span style='font-size:10px'>{tm_name_escaped}</span> "
            f"<span style='font-size:8px'>•</span> "
            f"<span style='font-size:10px; font-weight:700'>{match_pct_display}%</span>"
            f"{reverse_chip}"
        )

        # Tooltip on both labels (set whether or not chip is present so we
        # clear the tooltip when navigating away from a reversed match).
        # v1.10.52: wrap in HTML with explicit colours so the tooltip stays
        # readable under Windows-11 dark-mode tooltip rendering, which
        # ignores both QToolTip stylesheet and ToolTipBase/Text palette.
        reverse_tooltip = (
            "<div style='background-color:#1f2937; color:#f9fafb; "
            "padding:6px 8px; max-width:360px;'>"
            "This match comes from a TM whose source/target languages are "
            "the inverse of this project. The match is from the TM's "
            "target column; Workbench has swapped fields so it displays "
            "normally."
            "</div>"
        ) if reverse_match else ""

        if hasattr(self, 'compare_panel_tm_nav_label') and self.compare_panel_tm_nav_label:
            # Avoid a fixed font-size stylesheet overriding rich text emphasis, but keep theme color
            label_color = getattr(self.compare_panel_tm_nav_label, '_compare_text_color', None)
            if label_color:
                self.compare_panel_tm_nav_label.setStyleSheet(f"color: {label_color}; background: transparent; border: none;")
            else:
                self.compare_panel_tm_nav_label.setStyleSheet("background: transparent; border: none;")
            self.compare_panel_tm_nav_label.setText(nav_html)
            self.compare_panel_tm_nav_label.setToolTip(reverse_tooltip)

        if hasattr(self, 'compare_panel_tm_target_label') and self.compare_panel_tm_target_label:
            label_color = getattr(self.compare_panel_tm_target_label, '_compare_text_color', None)
            if label_color:
                self.compare_panel_tm_target_label.setStyleSheet(f"color: {label_color}; background: transparent; border: none;")
            else:
                self.compare_panel_tm_target_label.setStyleSheet("background: transparent; border: none;")
            self.compare_panel_tm_target_label.setText(nav_html)
            self.compare_panel_tm_target_label.setToolTip(reverse_tooltip)
        
        # Update TM Source with diff highlighting
        # Strip any stale invisible markers from TM data before processing
        current_source = self.compare_panel_current_source.toPlainText()
        tm_source = match.get('source', '')
        if tm_source and hasattr(self, 'reverse_invisible_replacements'):
            tm_source = self.reverse_invisible_replacements(tm_source)
        if tm_source and current_source:
            self._set_compare_panel_text_with_diff(self.compare_panel_tm_source, current_source, tm_source)
        else:
            # Apply invisible replacements for display (e.g. ↵ for line breaks)
            cp_src_display = tm_source or "(No TM match)"
            if tm_source and hasattr(self, 'apply_invisible_replacements'):
                cp_src_display = self.apply_invisible_replacements(cp_src_display)
            self.compare_panel_tm_source.setPlainText(cp_src_display)

        # Update TM Target – strip stale markers, then re-apply current settings
        tm_target_text = match.get('target', '')
        if tm_target_text and hasattr(self, 'reverse_invisible_replacements'):
            tm_target_text = self.reverse_invisible_replacements(tm_target_text)
        if tm_target_text and hasattr(self, 'apply_invisible_replacements'):
            tm_target_text = self.apply_invisible_replacements(tm_target_text)
        self.compare_panel_tm_target.setPlainText(tm_target_text)

    def set_compare_panel_matches(self, segment_id: int, current_source: str, 
                                   tm_matches: list = None, mt_matches: list = None):
        """Set all matches for Match Panel (and Compare Panel if it exists)
        
        Args:
            segment_id: Current segment ID
            current_source: The source text of the current segment
            tm_matches: List of dicts with keys: source, target, tm_name, match_pct
            mt_matches: List of dicts with keys: translation, provider
        """
        # Always update Match Panel with TM matches
        self.match_panel_tm_matches = tm_matches or []
        self.match_panel_tm_index = 0
        self.match_panel_current_source = current_source  # Store for diff highlighting
        self._update_match_panel_tm_display()
        
        # Update Compare Panel if it exists (legacy support)
        if hasattr(self, 'compare_panel_current_source') and self.compare_panel_current_source:
            # Update segment label
            if hasattr(self, 'compare_panel_segment_label'):
                self.compare_panel_segment_label.setText(f"Segment {segment_id + 1}")
            
            # Update Current Source
            self.compare_panel_current_source.setPlainText(current_source)
            
            # Store matches and reset indices
            self.compare_panel_tm_matches = tm_matches or []
            self.compare_panel_mt_matches = mt_matches or []
            self.compare_panel_tm_index = 0
            self.compare_panel_mt_index = 0
            
            # Update displays
            self._update_compare_panel_mt_display()
            self._update_compare_panel_tm_display()

    def update_compare_panel(self, segment_id: int, current_source: str, 
                              tm_source: str = "", tm_target: str = "", 
                              mt_translation: str = "", tm_match_percent: int = 0,
                              tm_name: str = "TM", mt_provider: str = "MT"):
        """Update the Match Panel with new data for the current segment (legacy single-match method)
        
        This is the legacy method that accepts single TM/MT matches. For multiple matches,
        use set_compare_panel_matches() instead.
        """
        # Convert to the new format and use set_compare_panel_matches
        tm_matches = []
        mt_matches = []
        
        if tm_source or tm_target:
            tm_matches = [{
                'source': tm_source,
                'target': tm_target,
                'tm_name': tm_name,
                'match_pct': tm_match_percent
            }]
        
        if mt_translation:
            mt_matches = [{
                'translation': mt_translation,
                'provider': mt_provider
            }]
        
        self.set_compare_panel_matches(segment_id, current_source, tm_matches, mt_matches)

    def _set_compare_panel_text_with_diff(self, text_edit: QTextEdit, current: str, tm_source: str):
        """
        Set text with diff highlighting using memoQ-style track changes view:
        - Normal text: identical to current
        - Red underline: text added in current (insertion)
        - Red strikethrough: text removed from TM (deletion)

        This displays the TM source annotated with tracked changes.
        Newline characters (\n) are preserved as actual line breaks in the display.
        """
        import difflib
        import re

        text_edit.clear()
        cursor = text_edit.textCursor()

        # Create formatters - memoQ track changes style
        normal_format = QTextCharFormat()

        # Red strikethrough for deletions (text in TM but not in current)
        delete_format = QTextCharFormat()
        delete_format.setForeground(QColor("#cc0000"))  # Red text
        delete_format.setFontStrikeOut(True)

        # Red underline for insertions (text in current but not in TM)
        add_format = QTextCharFormat()
        add_format.setForeground(QColor("#cc0000"))  # Red text
        add_format.setFontUnderline(True)

        # Word-level diff in which inline tags are tokens of their own and the
        # original spacing is kept (modules/tm_diff.py, issue #117). Before,
        # "<b>tap</b>" → "<b>valve</b>" marked the tags as changed too.
        from modules.tm_diff import word_diff
        show_linebreaks = (hasattr(self, 'invisible_display_settings')
                           and self.invisible_display_settings.get('linebreaks', False))
        formats = {'normal': normal_format, 'delete': delete_format, 'add': add_format}
        previous = None
        for kind, text in word_diff(current, tm_source):
            fmt = formats[kind]
            # Keep a struck-through word and its replacement apart
            if (previous is not None and previous[0] == 'delete' and kind == 'add'
                    and not previous[1][-1:].isspace() and not text[:1].isspace()):
                cursor.insertText(' ', normal_format)
            for n, line in enumerate(text.split('\n')):
                if n:
                    # Show ↵ marker if linebreaks invisibles are enabled
                    if show_linebreaks:
                        cursor.insertText('↵', fmt)
                    cursor.insertBlock()
                if line:
                    cursor.insertText(line, fmt)
            previous = (kind, text)

        text_edit.setTextCursor(cursor)

    def _refresh_compare_panel_theme(self):
        """Refresh Match Panel TM box colors based on current theme"""
        if not hasattr(self, 'compare_panel_text_edits'):
            return
        
        # Get theme colors
        if hasattr(self, 'theme_manager') and self.theme_manager:
            theme = self.theme_manager.current_theme
            # Check if theme is dark by checking the theme name or a dark attribute
            is_dark = getattr(theme, 'is_dark', 'dark' in theme.name.lower())
            border_color = theme.border
            text_color = theme.text
            # Use theme-appropriate colors for Match Panel boxes (both green)
            panel_success = theme.panel_success if hasattr(theme, 'panel_success') else ("#1e3a2f" if is_dark else "#d4edda")
            box_colors = [
                panel_success,  # 0: Match Panel - TM Source (green)
                panel_success,  # 1: Match Panel - TM Target (green)
            ]
        else:
            return
        
        # Update each box
        for i, (text_edit, label_widget, container, _) in enumerate(self.compare_panel_text_edits):
            bg_color = box_colors[i] if i < len(box_colors) else box_colors[-1]
            
            container.setStyleSheet(f"""
                QFrame {{
                    background-color: {bg_color};
                    border: 1px solid {border_color};
                    border-radius: 4px;
                }}
            """)
            
            label_widget.setStyleSheet(f"font-weight: bold; font-size: 9px; color: {text_color}; background: transparent; border: none;")
            
            text_edit.setStyleSheet(f"""
                QTextEdit {{
                    background-color: transparent;
                    border: none;
                    font-size: 10px;
                    padding: 2px;
                    color: {text_color};
                }}
            """)

    def results_pane_zoom_in(self):
        """Increase font size in translation results pane"""
        if hasattr(self, 'results_panels'):
            for panel in self.results_panels:
                if hasattr(panel, 'zoom_in'):
                    panel.zoom_in()
            # Save font sizes to preferences
            self.save_current_font_sizes()

    def results_pane_zoom_out(self):
        """Decrease font size in translation results pane"""
        if hasattr(self, 'results_panels'):
            for panel in self.results_panels:
                if hasattr(panel, 'zoom_out'):
                    panel.zoom_out()
            # Save font sizes to preferences
            self.save_current_font_sizes()

    def results_pane_zoom_reset(self):
        """Reset font size in translation results pane to default"""
        if hasattr(self, 'results_panels'):
            for panel in self.results_panels:
                if hasattr(panel, 'reset_zoom'):
                    panel.reset_zoom()
            # Save font sizes to preferences
            self.save_current_font_sizes()

    def match_panel_zoom_in(self):
        """Increase font size in Match Panel TM boxes"""
        type(self).match_panel_font_size = min(18, type(self).match_panel_font_size + 1)
        self._apply_match_panel_font_size()
        self.save_current_font_sizes()
        self.log(f"Match Panel font size: {type(self).match_panel_font_size}pt")

    def match_panel_zoom_out(self):
        """Decrease font size in Match Panel TM boxes"""
        type(self).match_panel_font_size = max(7, type(self).match_panel_font_size - 1)
        self._apply_match_panel_font_size()
        self.save_current_font_sizes()
        self.log(f"Match Panel font size: {type(self).match_panel_font_size}pt")

    def match_panel_zoom_reset(self):
        """Reset font size in Match Panel TM boxes to default"""
        type(self).match_panel_font_size = 10
        self._apply_match_panel_font_size()
        self.save_current_font_sizes()
        self.log("Match Panel font size reset to 10pt")

    def _apply_match_panel_font_size(self):
        """Apply current font settings to Match Panel TM Source/Target text edits.

        Both the QTextEdit stylesheet AND the document default font must be updated:
        - The stylesheet controls plain-text rendering.
        - The document default font controls rich-text content inserted via QTextCursor
          (e.g. diff-highlighted segments), which ignores the stylesheet font-size.
        """
        if not hasattr(self, 'compare_panel_text_edits'):
            return

        font_size = type(self).match_panel_font_size
        font_family = type(self).match_panel_font_family
        font_bold = type(self).match_panel_font_bold

        # Build a QFont for the document default font
        from PyQt6.QtGui import QFont
        doc_font = QFont()
        if font_family:
            doc_font.setFamily(font_family)
        doc_font.setPointSize(font_size)
        doc_font.setBold(font_bold)

        for item in self.compare_panel_text_edits:
            text_edit = item[0]  # First element is the QTextEdit
            if not (text_edit and hasattr(text_edit, 'setStyleSheet')):
                continue

            # 1. Update stylesheet (for plain-text rendering)
            import re
            current_style = text_edit.styleSheet()
            new_style = re.sub(r'font-size:\s*\d+px;?', f'font-size: {font_size}px;', current_style)
            if 'font-size' not in new_style:
                new_style += f" font-size: {font_size}px;"
            text_edit.setStyleSheet(new_style)

            # 2. Update document default font (for rich-text / cursor-inserted content)
            text_edit.document().setDefaultFont(doc_font)

    def select_previous_match(self):
        """Cycle to previous match (Ctrl+Up)

        Context-aware behavior:
        - If Compare Panel tab is active: navigate MT/TM list depending on focused box
        - If Translation Results tab is active: delegate to visible results panel
        - Otherwise: do nothing (avoid acting on hidden panels)
        """
        mode = self._get_active_match_shortcut_mode()
        if mode == 'compare':
            self._compare_panel_nav_active_box(-1)
            return
        if mode == 'results':
            for panel in self._iter_visible_results_panels():
                try:
                    if hasattr(panel, 'select_previous_match'):
                        panel.select_previous_match()
                        break
                except Exception as e:
                    self.log(f"Error selecting previous match: {e}")
            return

    def select_next_match(self):
        """Cycle to next match (Ctrl+Down)

        Context-aware behavior:
        - If Compare Panel tab is active: navigate MT/TM list depending on focused box
        - If Translation Results tab is active: delegate to visible results panel
        - Otherwise: do nothing (avoid acting on hidden panels)
        """
        mode = self._get_active_match_shortcut_mode()
        if mode == 'compare':
            self._compare_panel_nav_active_box(1)
            return
        if mode == 'results':
            for panel in self._iter_visible_results_panels():
                try:
                    if hasattr(panel, 'select_next_match'):
                        panel.select_next_match()
                        break
                except Exception as e:
                    self.log(f"Error selecting next match: {e}")
            return

    def insert_match_by_number(self, match_number: int):
        """Insert match by number (Ctrl+1-9)

        Context-aware behavior:
        - If a QuickTrans panel is visible and has a result N: insert it
        - If Compare Panel tab is active: insert from Compare Panel MT/TM list depending on focused box
        - If Translation Results tab is active: delegate to visible results panel
        - Otherwise: do nothing (avoid acting on hidden panels)
        """
        # QuickTrans takes priority when one of its docked panels is visible, so
        # Ctrl+1..9 inserts the numbered QuickTrans result while you translate.
        qt_panel = self._active_quicktrans_panel()
        if qt_panel is not None:
            try:
                if qt_panel.insert_match_by_number(match_number):
                    return
            except Exception as e:
                self.log(f"Error inserting QuickTrans result #{match_number}: {e}")

        mode = self._get_active_match_shortcut_mode()
        if mode == 'compare':
            try:
                self._insert_compare_panel_match_by_number(match_number)
            except Exception as e:
                self.log(f"Error inserting Compare Panel match #{match_number}: {e}")
            return
        if mode == 'results':
            for panel in self._iter_visible_results_panels():
                try:
                    if hasattr(panel, 'insert_match_by_number'):
                        if panel.insert_match_by_number(match_number):
                            break
                except Exception as e:
                    self.log(f"Error inserting match #{match_number}: {e}")
            return

    def _get_current_target_widget(self):
        """Get the current target cell text widget"""
        if hasattr(self, 'table') and self.table:
            current_row = self.table.currentRow()
            if current_row >= 0:
                return self.table.cellWidget(current_row, 3)  # Column 3 = Target
        return None

    def insert_selected_match(self):
        """Insert currently selected match (Ctrl+Space)

        Context-aware behavior:
        - If Compare Panel tab is active: insert current MT translation or TM target depending on focused box
        - If Translation Results tab is active: delegate to visible results panel
        - Otherwise: do nothing (avoid acting on hidden panels)
        """
        mode = self._get_active_match_shortcut_mode()
        if mode == 'compare':
            try:
                self._insert_compare_panel_current_match()
            except Exception as e:
                self.log(f"Error inserting Compare Panel selected match: {e}")
            return
        if mode == 'results':
            for panel in self._iter_visible_results_panels():
                try:
                    if hasattr(panel, 'insert_selected_match'):
                        if panel.insert_selected_match():
                            break
                except Exception as e:
                    self.log(f"Error inserting selected match: {e}")
            return

    def _get_active_match_shortcut_mode(self) -> str:
        """Return active shortcut context for match shortcuts.

        Returns:
            'match' if Match Panel tab is active,
            'results' if Translation Results tab is active,
            '' otherwise.
        """
        if hasattr(self, 'right_tabs') and self._widget_is_alive(self.right_tabs):
            try:
                current = self.right_tabs.currentWidget()
            except Exception:
                current = None
            if current is not None:
                if hasattr(self, 'match_panel_widget') and self.match_panel_widget and current is self.match_panel_widget:
                    return 'match'
                if hasattr(self, 'translation_results_panel') and self.translation_results_panel and current is self.translation_results_panel:
                    return 'results'
        return ''

    def _active_quicktrans_panel(self):
        """Return the QuickTrans docked panel currently visible to the user
        (its tab is selected and the dock is shown), or None. Checked first by
        the Ctrl+1..9 match shortcuts so they insert QuickTrans results while
        translating. There are up to two panels (under-grid and Match Panel)."""
        for p in (getattr(self, 'quicktrans_panel', None),
                  getattr(self, 'quicktrans_panel_match', None)):
            try:
                if p is not None and p.isVisible():
                    return p
            except Exception:
                continue
        return None

    def _iter_visible_results_panels(self):
        """Yield results panels that are visible to the user (tab-selected)."""
        if not hasattr(self, 'results_panels') or not self.results_panels:
            return
        for panel in self.results_panels:
            try:
                if panel and hasattr(panel, 'isVisible') and panel.isVisible():
                    yield panel
            except Exception:
                continue

    def _compare_panel_nav_active_box(self, direction: int):
        """Navigate Compare Panel matches based on which box is focused."""
        if not hasattr(self, 'compare_panel') or not self.compare_panel:
            return
        if not hasattr(self, 'right_tabs') or not self._widget_is_alive(self.right_tabs):
            return
        if self.right_tabs.currentWidget() is not self.compare_panel:
            return

        focus_widget = QApplication.focusWidget()
        if hasattr(self, 'compare_panel_mt') and focus_widget is self.compare_panel_mt:
            self._compare_panel_nav_mt(direction)
            return
        if hasattr(self, 'compare_panel_tm_source') and focus_widget is self.compare_panel_tm_source:
            self._compare_panel_nav_tm(direction)
            return
        if hasattr(self, 'compare_panel_tm_target') and focus_widget is self.compare_panel_tm_target:
            self._compare_panel_nav_tm(direction)
            return

        # Default: TM navigation (more "match-like" than MT)
        self._compare_panel_nav_tm(direction)

    def _insert_compare_panel_current_match(self):
        """Insert currently shown Compare Panel text based on focused box."""
        if not hasattr(self, 'compare_panel') or not self.compare_panel:
            return
        if not hasattr(self, 'right_tabs') or not self._widget_is_alive(self.right_tabs):
            return
        if self.right_tabs.currentWidget() is not self.compare_panel:
            return

        focus_widget = QApplication.focusWidget()
        if hasattr(self, 'compare_panel_mt') and focus_widget is self.compare_panel_mt:
            text = self.compare_panel_mt.toPlainText().strip()
            if text and not text.startswith('('):
                self.on_match_inserted(text)
            return

        # Default to inserting TM target
        if hasattr(self, 'compare_panel_tm_target'):
            text = self.compare_panel_tm_target.toPlainText().strip()
            if text and not text.startswith('('):
                self.on_match_inserted(text)

    def _handle_compare_panel_alt0_shortcut(self):
        """Handle Alt+0 insertion from Match Panel.

        Alt+0 inserts TM Target from Match Panel (works regardless of which right panel tab is selected).
        """
        # Always try to insert TM Target from Match Panel if available
        # (No longer requires Match Panel tab to be selected)
        if hasattr(self, 'match_panel_tm_target'):
            text = self.match_panel_tm_target.toPlainText().strip()
            if text and not text.startswith('('):
                self._replace_current_target_segment_text(text, source_label='TM Target (Match Panel)')
                return

        # Legacy Compare Panel support (not currently used)
        mode = self._get_active_match_shortcut_mode()
        if mode != 'compare':
            return

        import time
        current_time = time.time()
        double_tap_threshold = 0.3  # 300ms window
        key_num = 0

        if (hasattr(self, '_compare_panel_last_key') and
            self._compare_panel_last_key == key_num and
            hasattr(self, '_compare_panel_last_time') and
            (current_time - self._compare_panel_last_time) < double_tap_threshold):
            # Double-tap: undo MT insert, then insert TM Target
            current_widget = self._get_current_target_widget()
            if current_widget and hasattr(current_widget, 'undo'):
                current_widget.undo()
            self._compare_panel_last_key = None
            self._compare_panel_last_time = 0
            if hasattr(self, 'compare_panel_tm_target'):
                text = self.compare_panel_tm_target.toPlainText().strip()
                if text and not text.startswith('('):
                    self._replace_current_target_segment_text(text, source_label='TM Target (Compare Panel)')
            return

        # Single tap: insert MT
        self._compare_panel_last_key = key_num
        self._compare_panel_last_time = current_time
        if hasattr(self, 'compare_panel_mt'):
            text = self.compare_panel_mt.toPlainText().strip()
            if text and not text.startswith('('):
                self._replace_current_target_segment_text(text, source_label='MT (Compare Panel)')

    def _replace_current_target_segment_text(self, new_text: str, source_label: str = ""):
        """Replace the entire current target segment text (single undo step).

        This is used for Compare Panel quick-insert shortcuts like Alt+0 / Alt+0,0,
        where the user expects a full replacement (not insertion at cursor).
        """
        try:
            if not self.current_project or not self.table:
                return

            row = self.table.currentRow()
            if row < 0:
                return

            id_item = self.table.item(row, 0)
            if not id_item:
                self.log(f"⚠️ No segment ID found at row {row}")
                return

            try:
                segment_id = int(id_item.text())
            except (ValueError, AttributeError):
                self.log(f"⚠️ Could not parse segment ID from row {row}")
                return

            segment = next((seg for seg in self.current_project.segments if seg.id == segment_id), None)
            if not segment:
                self.log(f"⚠️ Could not find segment with ID {segment_id}")
                return

            target_widget = self.table.cellWidget(row, 3)
            if target_widget and isinstance(target_widget, QTextEdit):
                from PyQt6.QtGui import QTextCursor

                # Strip outer wrapping tags for display if setting is enabled
                display_text = new_text
                if self.hide_outer_wrapping_tags:
                    display_text, _ = strip_outer_wrapping_tags(display_text)

                cursor = target_widget.textCursor()
                cursor.beginEditBlock()
                cursor.select(QTextCursor.SelectionType.Document)
                cursor.insertText(display_text)
                cursor.endEditBlock()

                # Store full text (with tags) in segment, but use displayed text if tags are hidden
                _undo_old_target = segment.target
                _undo_old_status = segment.status
                segment.target = new_text  # Keep full text with tags
                try:
                    self.record_undo_state(segment.id, _undo_old_target, new_text, _undo_old_status, _undo_old_status)
                except Exception:
                    pass  # never let undo failure break the actual operation
                target_widget.setFocus()
                target_widget.moveCursor(QTextCursor.MoveOperation.End)

                label = f" ({source_label})" if source_label else ""
                self.log(f"✓ Replaced target text in segment {segment.id}{label}")
                self._play_sound_effect('match_inserted')
                return

            # Fallback: no editor widget, update data and (if possible) update cell
            segment.target = new_text
            if target_widget and hasattr(target_widget, 'setPlainText'):
                # Strip outer wrapping tags if setting is enabled
                display_text = new_text
                if self.hide_outer_wrapping_tags:
                    display_text, _ = strip_outer_wrapping_tags(display_text)
                target_widget.setPlainText(display_text)
            label = f" ({source_label})" if source_label else ""
            self.log(f"✓ Replaced target text in segment {segment_id}{label}")
            self._play_sound_effect('match_inserted')

        except Exception as e:
            self.log(f"Error replacing target text: {e}")
            import traceback
            traceback.print_exc()

    def insert_compare_panel_mt(self):
        """Insert the currently shown Compare Panel MT translation (default: Alt+M)."""
        if self._get_active_match_shortcut_mode() != 'compare':
            return
        self._insert_compare_panel_mt_current()

    def insert_compare_panel_tm_target(self):
        """Insert the currently shown Compare Panel TM target (default: Alt+T)."""
        if self._get_active_match_shortcut_mode() != 'compare':
            return
        self._insert_compare_panel_tm_target_current()

    def _insert_compare_panel_mt_current(self):
        if not hasattr(self, 'compare_panel_mt'):
            return
        text = self.compare_panel_mt.toPlainText().strip()
        if text and not text.startswith('('):
            self.on_match_inserted(text)

    def _insert_compare_panel_tm_target_current(self):
        if not hasattr(self, 'compare_panel_tm_target'):
            return
        text = self.compare_panel_tm_target.toPlainText().strip()
        if text and not text.startswith('('):
            self.on_match_inserted(text)

    def _insert_compare_panel_match_by_number(self, match_number: int):
        """Insert Compare Panel content by number (Ctrl+1-9 while Compare Panel is active).

        Only two of these make sense for the Compare Panel:
        - 1 = MT (current)
        - 2 = TM Target (current)
        """
        if match_number not in (1, 2):
            return
        if not hasattr(self, 'compare_panel') or not self.compare_panel:
            return
        if not hasattr(self, 'right_tabs') or not self._widget_is_alive(self.right_tabs):
            return
        if self.right_tabs.currentWidget() is not self.compare_panel:
            return

        if match_number == 1:
            self._insert_compare_panel_mt_current()
            return
        if match_number == 2:
            self._insert_compare_panel_tm_target_current()
            return
