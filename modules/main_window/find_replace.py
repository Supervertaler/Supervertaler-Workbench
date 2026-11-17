"""Find and Replace (including saved sets of operations), Go To, and
searching the segments.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import re
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
)
from modules.find_replace_qt import (
    FindReplaceOperation,
    FindReplaceSet,
    FindReplaceSetsManager,
    HistoryComboBox,
)
from modules.help_system import Topics as HelpTopics, open_help, set_topic as set_help_topic
from modules.styled_widgets import CheckmarkCheckBox, CheckmarkRadioButton
from modules.tag_text import strip_outer_wrapping_tags


class FindReplaceMixin:
    def show_find_replace_dialog(self):
        """Show unified Find & Replace dialog (Ctrl+F and Ctrl+H both open same dialog)
        Features:
        - History dropdowns for recent search/replace terms (last 20)
        - F&R Sets panel for batch operations
        - Pre-fills selected text from source (Find) or target (Replace)
        - Filters grid to show only matching segments
        """
        if not self.current_project or not self.current_project.segments:
            QMessageBox.information(self, "No Project", "Please open a project first.")
            return
        
        from PyQt6.QtWidgets import QCheckBox, QGroupBox, QToolButton, QFrame, QSplitter
        
        # Get selected text from source or target cells to pre-fill the Find field
        # Selected text always goes to Find What, never to Replace With
        prefill_find = ""
        
        current_row = self.table.currentRow() if hasattr(self, 'table') and self.table else -1
        if current_row >= 0:
            # Check target cell first (most common case - user is editing target)
            target_widget = self.table.cellWidget(current_row, 3)
            if target_widget and hasattr(target_widget, 'textCursor'):
                target_selection = target_widget.textCursor().selectedText().strip()
                if target_selection:
                    prefill_find = target_selection
                    self.log(f"🔍 Pre-filling Find with target selection: '{target_selection}'")
            
            # If no target selection, check source cell
            if not prefill_find:
                source_widget = self.table.cellWidget(current_row, 2)
                if source_widget and hasattr(source_widget, 'textCursor'):
                    source_selection = source_widget.textCursor().selectedText().strip()
                    if source_selection:
                        prefill_find = source_selection
                        self.log(f"🔍 Pre-filling Find with source selection: '{source_selection}'")
        
        # Disable background lookups while find/replace is active
        self.find_replace_active = True
        self.log("🔍 Find/Replace active - background lookups disabled")
        
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Find and Replace"))
        dialog.setMinimumWidth(700)
        set_help_topic(dialog, HelpTopics.FIND_REPLACE)

        # Re-enable lookups when dialog closes
        def on_dialog_closed():
            self.find_replace_active = False
            self.log("✓ Find/Replace closed - background lookups re-enabled")
        
        dialog.finished.connect(on_dialog_closed)
        
        # Main vertical layout 
        main_v_layout = QVBoxLayout(dialog)
        main_v_layout.setSpacing(8)

        # Contextual help "?" in the top-right corner – opens the online
        # Find & Replace help page (same target as F1 within this dialog).
        help_row = QHBoxLayout()
        help_row.addStretch()
        fr_help_btn = QPushButton("?")
        fr_help_btn.setFixedSize(22, 22)
        fr_help_btn.setToolTip(self.tr("Open Find & Replace help (online)"))
        fr_help_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fr_help_btn.clicked.connect(lambda: open_help(HelpTopics.FIND_REPLACE))
        help_row.addWidget(fr_help_btn)
        main_v_layout.addLayout(help_row)

        # Row 1: Find what (with history dropdown)
        find_layout = QHBoxLayout()
        find_label = QLabel(self.tr("Find what:"))
        find_label.setFixedWidth(75)
        find_layout.addWidget(find_label)
        self.find_input = HistoryComboBox()
        self.find_input.set_history(self.fr_history.find_history)
        self.find_input.setText(prefill_find)
        find_layout.addWidget(self.find_input, stretch=1)
        main_v_layout.addLayout(find_layout)
        
        # Row 2: Replace with (with history dropdown)
        replace_layout = QHBoxLayout()
        replace_label = QLabel(self.tr("Replace with:"))
        replace_label.setFixedWidth(75)
        replace_layout.addWidget(replace_label)
        self.replace_input = HistoryComboBox()
        self.replace_input.set_history(self.fr_history.replace_history)
        # Replace field always starts empty - user fills in what they want to replace with
        replace_layout.addWidget(self.replace_input, stretch=1)
        main_v_layout.addLayout(replace_layout)
        
        # Row 3: Options - all inline in one horizontal row
        options_layout = QHBoxLayout()
        options_layout.setSpacing(15)
        
        # Search in label + checkboxes
        options_layout.addWidget(QLabel(self.tr("Search in:")))
        self.search_source_cb = CheckmarkCheckBox(self.tr("Source"))
        self.search_target_cb = CheckmarkCheckBox(self.tr("Target"))
        self.search_target_cb.setChecked(True)  # Default to target
        options_layout.addWidget(self.search_source_cb)
        options_layout.addWidget(self.search_target_cb)
        
        # Separator
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setFrameShadow(QFrame.Shadow.Sunken)
        options_layout.addWidget(sep1)
        
        # Match label + radio buttons
        options_layout.addWidget(QLabel(self.tr("Match:")))
        self.match_group = QButtonGroup(dialog)
        # The app's global stylesheet styles the unchecked radio indicator but
        # not the :checked state, so a bare QRadioButton renders an empty circle
        # that never visibly selects (looks "unclickable"). Give each radio an
        # explicit indicator stylesheet with a working :checked fill so they read
        # as standard radios – without the off-centre hand-painted dot of
        # CheckmarkRadioButton.
        _radio_qss = (
            "QRadioButton { font-size: 9pt; spacing: 6px; }"
            "QRadioButton::indicator { width: 14px; height: 14px;"
            " border: 2px solid #999; border-radius: 9px; background-color: white; }"
            "QRadioButton::indicator:checked { background-color: #4CAF50;"
            " border-color: #4CAF50; }"
            "QRadioButton::indicator:hover { border-color: #666; }"
            "QRadioButton::indicator:checked:hover { background-color: #45a049;"
            " border-color: #45a049; }"
            # Greyed-out look while disabled (when Regex is on).
            "QRadioButton:disabled { color: #aaa; }"
            "QRadioButton::indicator:disabled { border-color: #ccc;"
            " background-color: #f0f0f0; }"
            "QRadioButton::indicator:checked:disabled { background-color: #cfe8cf;"
            " border-color: #cfe8cf; }"
        )
        match_anything = QRadioButton(self.tr("Anything"))
        match_anything.setStyleSheet(_radio_qss)
        match_anything.setChecked(True)
        match_whole_words = QRadioButton(self.tr("Whole words"))
        match_whole_words.setStyleSheet(_radio_qss)
        match_entire = QRadioButton(self.tr("Entire segment"))
        match_entire.setStyleSheet(_radio_qss)
        
        self.match_group.addButton(match_anything, 0)
        self.match_group.addButton(match_whole_words, 1)
        self.match_group.addButton(match_entire, 2)
        
        options_layout.addWidget(match_anything)
        options_layout.addWidget(match_whole_words)
        options_layout.addWidget(match_entire)
        
        # Separator
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        options_layout.addWidget(sep2)
        
        # Case sensitive checkbox
        self.case_sensitive_cb = CheckmarkCheckBox(self.tr("Case sensitive"))
        options_layout.addWidget(self.case_sensitive_cb)

        # Auto-adjust case checkbox
        self.auto_case_cb = CheckmarkCheckBox(self.tr("Auto-adjust case"))
        self.auto_case_cb.setToolTip(
            "When replacing, automatically adjust the replacement text to match\n"
            "the case pattern of the matched text.\n\n"
            "Recognised patterns:\n"
            "  ALL CAPS  → replacement is uppercased\n"
            "  all lower → replacement is lowercased\n"
            "  Title Case → replacement is title-cased\n"
            "  Sentence case → first letter uppercased\n\n"
            "Has no effect when 'Case sensitive' is also checked."
        )
        options_layout.addWidget(self.auto_case_cb)

        # Regex toggle. When on, the find term is a regular expression; the
        # Match radios and Auto-adjust case don't apply, so they're greyed out
        # (the matching code also ignores them – the regex branch runs first
        # everywhere). The radios now render correctly, so disabling greys them
        # cleanly and re-enables when Regex is unticked.
        self.regex_cb = CheckmarkCheckBox(self.tr("Regex"))
        self.regex_cb.setToolTip(
            "Treat the Find field as a regular expression (Python re syntax).\n"
            "The Replace field supports backreferences: \\1, \\2, \\g<name>.\n\n"
            "When on, the Match options (Whole words / Entire segment) and\n"
            "Auto-adjust case are ignored."
        )

        def _on_regex_toggled(checked):
            for _b in self.match_group.buttons():
                _b.setEnabled(not checked)
            self.auto_case_cb.setEnabled(not checked)

        self.regex_cb.toggled.connect(_on_regex_toggled)
        options_layout.addWidget(self.regex_cb)

        # Separator before the status-handling option
        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.VLine)
        sep3.setFrameShadow(QFrame.Shadow.Sunken)
        options_layout.addWidget(sep3)

        # "Reset edited to Draft" – on by default. When a replace changes a
        # target segment that was Confirmed/Proofread/Approved, drop it back to
        # Draft so the segments Find & Replace touched are easy to spot and
        # re-check. Single source of truth is self.fr_demote_to_draft (read by
        # all replace paths); persisted in the "ui" settings section.
        if getattr(self, 'fr_demote_to_draft', None) is None:
            try:
                _demote_default = self._load_settings_section("ui").get("fr_demote_edited_to_draft", True)
            except Exception:
                _demote_default = True
            self.fr_demote_to_draft = bool(_demote_default)
        self.fr_demote_cb = CheckmarkCheckBox(self.tr("Reset edited to Draft"))
        self.fr_demote_cb.setChecked(bool(self.fr_demote_to_draft))
        self.fr_demote_cb.setToolTip(self.tr(
            "When a replace changes a target segment, reset its status to Draft if it\n"
            "was Confirmed, Proofread or Approved. This makes the segments that Find &\n"
            "Replace touched easy to spot and re-check. Undo (Ctrl+Z) restores the\n"
            "original text and status together.\n"
            "Applies to Replace this, Replace all and F&R Sets batch runs."
        ))
        self.fr_demote_cb.toggled.connect(lambda checked: self._set_fr_demote_to_draft(checked))
        options_layout.addWidget(self.fr_demote_cb)

        # Also change the project's writable TMs (issue #68). Deliberately not
        # remembered: it edits TMs, which Ctrl+Z cannot undo.
        self.fr_also_tm_cb = CheckmarkCheckBox(self.tr("Also in writable TMs"))
        self.fr_also_tm_cb.setToolTip(self.tr(
            "Replace all also makes the same change in the translation memories this\n"
            "project writes to (the TMs with Write ticked), so the old wording stops\n"
            "coming back as TM matches. You are shown how many TM entries will change\n"
            "before anything is done. TM changes cannot be undone with Ctrl+Z.\n"
            "Only Replace all does this, not Replace this."
        ))
        options_layout.addWidget(self.fr_also_tm_cb)

        options_layout.addStretch()
        main_v_layout.addLayout(options_layout)
        
        # Row 4: Action buttons - horizontal row, grouped to reduce
        # mis-clicks.  Find/Highlight buttons live to the left of a
        # visible separator; the destructive Replace buttons sit on
        # the right with a distinct background colour.
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(8)

        # Find / Highlight cluster (non-destructive)
        find_next_btn = QPushButton(self.tr("Find next"))
        find_next_btn.clicked.connect(lambda: self._fr_find_next())
        buttons_layout.addWidget(find_next_btn)

        find_all_btn = QPushButton(self.tr("Find all"))
        find_all_btn.clicked.connect(lambda: self._fr_find_all())
        buttons_layout.addWidget(find_all_btn)

        highlight_all_btn = QPushButton(self.tr("Highlight all"))
        highlight_all_btn.clicked.connect(lambda: self.highlight_all_matches())
        buttons_layout.addWidget(highlight_all_btn)

        clear_highlight_btn = QPushButton(self.tr("Clear highlights"))
        clear_highlight_btn.clicked.connect(lambda: self.clear_search_highlights())
        buttons_layout.addWidget(clear_highlight_btn)

        # Visual separator before the destructive Replace cluster
        fr_sep = QFrame()
        fr_sep.setFrameShape(QFrame.Shape.VLine)
        fr_sep.setFrameShadow(QFrame.Shadow.Sunken)
        buttons_layout.addWidget(fr_sep)

        # Replace cluster (destructive — distinct amber background so
        # a click here is visually unambiguous)
        replace_btn_style = (
            "QPushButton { background-color: #FFE082; font-weight: bold; }"
            "QPushButton:hover { background-color: #FFCC33; }"
        )
        replace_this_btn = QPushButton(self.tr("Replace this"))
        replace_this_btn.setStyleSheet(replace_btn_style)
        replace_this_btn.clicked.connect(lambda: self.replace_current_match())
        buttons_layout.addWidget(replace_this_btn)

        replace_all_btn = QPushButton(self.tr("Replace all"))
        replace_all_btn.setStyleSheet(replace_btn_style)
        replace_all_btn.clicked.connect(lambda: self._fr_replace_all())
        buttons_layout.addWidget(replace_all_btn)

        buttons_layout.addStretch()

        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(dialog.close)
        buttons_layout.addWidget(close_btn)

        # Keyboard shortcuts: Enter in the Find field triggers Find next;
        # Enter in the Replace field triggers Replace all (which already
        # shows a confirmation dialog before actually replacing).  Both
        # use lineEdit() because HistoryComboBox is a QComboBox.
        self.find_input.lineEdit().returnPressed.connect(self._fr_find_next)
        self.replace_input.lineEdit().returnPressed.connect(self._fr_replace_all)

        main_v_layout.addLayout(buttons_layout)
        
        # Horizontal separator line
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        main_v_layout.addWidget(separator)
        
        # F&R Sets collapsible panel
        fr_sets_frame = QFrame()
        fr_sets_layout = QVBoxLayout(fr_sets_frame)
        fr_sets_layout.setContentsMargins(0, 5, 0, 0)
        
        # Toggle button for collapsible panel
        toggle_layout = QHBoxLayout()
        self.fr_sets_toggle = QToolButton()
        self.fr_sets_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.fr_sets_toggle.setCheckable(True)
        self.fr_sets_toggle.setText(self.tr("  F&&R Sets: Save and Run Multiple Find and Replace Operations"))
        self.fr_sets_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.fr_sets_toggle.setStyleSheet("QToolButton { font-weight: bold; border: none; }")
        toggle_layout.addWidget(self.fr_sets_toggle)
        
        # Add Current to Set button (always visible)
        add_to_set_btn = QPushButton(self.tr("+ Add Current to Set"))
        add_to_set_btn.setToolTip(self.tr("Add current Find/Replace to the selected set"))
        add_to_set_btn.clicked.connect(lambda: self._fr_add_to_set())
        toggle_layout.addWidget(add_to_set_btn)
        
        toggle_layout.addStretch()
        fr_sets_layout.addLayout(toggle_layout)
        
        # F&R Sets Manager widget (collapsible content)
        self.fr_sets_manager = FindReplaceSetsManager(str(self.user_data_path), parent=dialog)
        self.fr_sets_manager.setVisible(False)  # Collapsed by default
        self.fr_sets_manager.operation_selected.connect(self._fr_load_operation)
        self.fr_sets_manager.set_selected.connect(self._fr_run_set_batch)
        fr_sets_layout.addWidget(self.fr_sets_manager)
        
        # Toggle visibility
        def toggle_sets_panel(checked):
            self.fr_sets_manager.setVisible(checked)
            self.fr_sets_toggle.setArrowType(
                Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
            )
            # Resize dialog to fit content
            if checked:
                dialog.setMinimumHeight(500)
            else:
                dialog.setMinimumHeight(0)
                dialog.adjustSize()
        
        self.fr_sets_toggle.toggled.connect(toggle_sets_panel)
        
        main_v_layout.addWidget(fr_sets_frame)
        
        # Store dialog reference and show
        self.find_replace_dialog = dialog
        self.current_match_index = -1
        self.find_matches = []
        # Track which cells currently have search-term highlights so a
        # new search can clear just those, instead of forcing a full
        # load_segments_to_grid() rebuild (which py-spy showed was ~64%
        # of MainThread CPU during batch find).
        self._search_highlighted_cells: set = set()

        dialog.show()

    def _fr_find_next(self):
        """Wrapper for find_next_match that saves to history."""
        find_text = self.find_input.text()
        if find_text:
            self.fr_history.add_find(find_text)
            self.find_input.set_history(self.fr_history.find_history)
        self.find_next_match()

    def _fr_find_all(self):
        """Wrapper for find_all_matches that saves to history."""
        find_text = self.find_input.text()
        if find_text:
            self.fr_history.add_find(find_text)
            self.find_input.set_history(self.fr_history.find_history)
        self.find_all_matches()

    def _fr_replace_all(self):
        """Wrapper for replace_all_matches that saves to history."""
        find_text = self.find_input.text()
        replace_text = self.replace_input.text()
        if find_text:
            self.fr_history.add_operation(find_text, replace_text)
            self.find_input.set_history(self.fr_history.find_history)
            self.replace_input.set_history(self.fr_history.replace_history)
        self.replace_all_matches()

    def _fr_add_to_set(self):
        """Add current Find/Replace values to the selected F&R Set."""
        find_text = self.find_input.text()
        replace_text = self.replace_input.text()
        
        if not find_text:
            QMessageBox.information(self.find_replace_dialog, "Add to Set", "Please enter a Find term first.")
            return
        
        # Build operation from current dialog settings
        search_in = "both" if (self.search_source_cb.isChecked() and self.search_target_cb.isChecked()) else (
            "source" if self.search_source_cb.isChecked() else "target"
        )
        
        op = FindReplaceOperation(
            find_text=find_text,
            replace_text=replace_text,
            search_in=search_in,
            match_mode=self.match_group.checkedId(),
            case_sensitive=self.case_sensitive_cb.isChecked(),
            enabled=True,
            auto_case=self.auto_case_cb.isChecked(),
            use_regex=self.regex_cb.isChecked(),
        )
        
        self.fr_sets_manager.add_current_operation_to_set(op)
        self.log(f"✅ Added F&R operation to set: '{find_text}' → '{replace_text}'")

    def _fr_load_operation(self, op: FindReplaceOperation):
        """Load an operation from a set into the dialog fields."""
        self.find_input.setText(op.find_text)
        self.replace_input.setText(op.replace_text)
        
        # Set search options
        if op.search_in == "both":
            self.search_source_cb.setChecked(True)
            self.search_target_cb.setChecked(True)
        elif op.search_in == "source":
            self.search_source_cb.setChecked(True)
            self.search_target_cb.setChecked(False)
        else:  # target
            self.search_source_cb.setChecked(False)
            self.search_target_cb.setChecked(True)
        
        # Set match mode
        btn = self.match_group.button(op.match_mode)
        if btn:
            btn.setChecked(True)
        
        self.case_sensitive_cb.setChecked(op.case_sensitive)
        self.auto_case_cb.setChecked(getattr(op, 'auto_case', False))
        self.regex_cb.setChecked(getattr(op, 'use_regex', False))

    def _fr_compile_regex(self, find_text, case_sensitive):
        """Compile a user-entered regex. On error, show a dialog and return None."""
        import re
        try:
            return re.compile(find_text, 0 if case_sensitive else re.IGNORECASE)
        except re.error as e:
            QMessageBox.warning(
                self.find_replace_dialog, "Invalid regular expression",
                f"The Find pattern is not a valid regular expression:\n\n{e}")
            return None

    def _fr_status_after_edit(self, old_status: str) -> str:
        """Status a segment should carry after a Find & Replace edit changed it.

        When the "Reset edited to Draft" option is on (the default), a finished
        status (Confirmed / Proofread / Approved) drops to ``draft`` so the
        translator can see exactly which segments Find & Replace touched.
        Otherwise the status is left unchanged."""
        if getattr(self, 'fr_demote_to_draft', True) and old_status in ("confirmed", "proofread", "approved"):
            return "draft"
        return old_status

    def _set_fr_demote_to_draft(self, enabled: bool):
        """Persist the demote-on-edit preference (live-saved from the F&R dialog
        checkbox). Single source of truth for all three replace paths."""
        self.fr_demote_to_draft = bool(enabled)
        try:
            ui = self._load_settings_section("ui")
            ui["fr_demote_edited_to_draft"] = bool(enabled)
            self._save_settings_section("ui", ui)
        except Exception as e:
            self.log(f"⚠ Could not save F&R demote preference: {e}")

    def _fr_run_set_batch(self, fr_set: FindReplaceSet):
        """Run all enabled operations in a F&R Set as a batch (optimized for speed)."""
        # QA checks are find-only (#209) – never run them as replacements, or
        # their empty "Replace with" would delete every match.
        enabled_ops = [op for op in fr_set.operations if op.enabled and op.find_text
                       and not getattr(op, 'check_only', False)]
        
        if not enabled_ops:
            QMessageBox.information(self.find_replace_dialog, "Run All", "No enabled operations with find text.")
            return

        # Flag operations with an empty "Replace with" – Run All would DELETE
        # every match for those, which is a common surprise (e.g. someone saved
        # a "find" expecting it to be harmless).
        deletion_ops = [op for op in enabled_ops if not op.replace_text]
        warn = ""
        if deletion_ops:
            listed = "\n".join(f"  • '{op.find_text}' → (nothing)" for op in deletion_ops[:8])
            more = "" if len(deletion_ops) <= 8 else f"\n  …and {len(deletion_ops) - 8} more"
            warn = (
                f"\n\n⚠ {len(deletion_ops)} operation(s) have an empty \"Replace with\" "
                f"and will DELETE all their matches:\n{listed}{more}"
            )

        # Confirm – default to No when any operation would delete text.
        default_btn = (QMessageBox.StandardButton.No if deletion_ops
                       else QMessageBox.StandardButton.Yes)
        reply = QMessageBox.question(
            self.find_replace_dialog,
            "Run Batch Replace",
            f"Run {len(enabled_ops)} replace operation(s) from '{fr_set.name}'?\n\n"
            "This will replace all occurrences in target segments." + warn,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            default_btn,
        )

        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # OPTIMIZATION: Disable UI updates during batch processing
        self.table.setUpdatesEnabled(False)
        
        try:
            # Run each operation
            total_replaced = 0
            for op in enabled_ops:
                count = self._execute_single_fr_operation(op)
                total_replaced += count
                self.log(f"  '{op.find_text}' → '{op.replace_text}': {count} replacement(s)")
            
            self.project_modified = True
            self.update_window_title()
            
        finally:
            # OPTIMIZATION: Re-enable UI updates and refresh only target column cells
            self.table.setUpdatesEnabled(True)
            # Update all target cells in-place (batch operations can affect many segments)
            _demote = getattr(self, 'fr_demote_to_draft', True)
            for row in range(len(self.current_project.segments)):
                segment = self.current_project.segments[row]
                target_widget = self.table.cellWidget(row, 3)
                if target_widget and hasattr(target_widget, 'setPlainText'):
                    target_widget.blockSignals(True)
                    # Strip outer wrapping tags if setting is enabled
                    display_text = segment.target
                    if self.hide_outer_wrapping_tags:
                        display_text, _ = strip_outer_wrapping_tags(display_text)
                    target_widget.setPlainText(display_text)
                    target_widget.blockSignals(False)
                # Statuses may have been demoted to Draft during the batch.
                if _demote:
                    self._update_status_cell(row, segment)
            self.table.viewport().update()
            self.update_progress_stats()
        
        QMessageBox.information(
            self.find_replace_dialog,
            "Batch Complete",
            f"Completed {len(enabled_ops)} operation(s).\nTotal replacements: {total_replaced}"
        )

    def _execute_single_fr_operation(self, op: FindReplaceOperation) -> int:
        """Execute a single F&R operation on all segments (optimized). Returns replacement count."""
        import re
        count = 0

        # Regex operations: pre-compile once (skip the literal substring
        # pre-filter below, which doesn't apply to patterns). An invalid
        # pattern is logged and the operation skipped.
        regex_pattern = None
        if getattr(op, 'use_regex', False):
            try:
                regex_pattern = re.compile(op.find_text, 0 if op.case_sensitive else re.IGNORECASE)
            except re.error as e:
                self.log(f"  ⚠ Skipped invalid regex '{op.find_text}': {e}")
                return 0

        # OPTIMIZATION: Pre-filter segments - only check segments that might contain the text
        # Quick case-insensitive check to skip segments that definitely don't match
        search_text_lower = op.find_text.lower() if (not op.case_sensitive and regex_pattern is None) else None
        
        for segment in self.current_project.segments:
            texts_to_check = []
            
            # Pre-filter: skip segments that can't possibly match (literal
            # substring check – not applicable to regex patterns).
            if regex_pattern is None and not op.case_sensitive:
                # Quick check: does the segment contain the search text at all?
                skip_segment = True
                if op.search_in in ("source", "both") and self.allow_replace_in_source:
                    if search_text_lower in segment.source.lower():
                        skip_segment = False
                if op.search_in in ("target", "both"):
                    if search_text_lower in segment.target.lower():
                        skip_segment = False
                if skip_segment:
                    continue
            
            if op.search_in in ("source", "both") and self.allow_replace_in_source:
                texts_to_check.append(("source", segment.source))
            if op.search_in in ("target", "both"):
                texts_to_check.append(("target", segment.target))
            
            auto_case = getattr(op, 'auto_case', False)

            for field_name, old_text in texts_to_check:
                if regex_pattern is not None:
                    try:
                        new_text = regex_pattern.sub(op.replace_text, old_text)
                    except re.error as e:
                        self.log(f"  ⚠ Regex replacement failed for '{op.find_text}': {e}")
                        return count
                elif op.match_mode == 2:  # Entire segment
                    if op.case_sensitive:
                        if old_text == op.find_text:
                            new_text = op.replace_text
                        else:
                            new_text = old_text
                    else:
                        if old_text.lower() == op.find_text.lower():
                            new_text = self._apply_case_pattern(old_text, op.replace_text) if auto_case else op.replace_text
                        else:
                            new_text = old_text
                elif op.match_mode == 1:  # Whole words
                    pattern = r'\b' + re.escape(op.find_text) + r'\b'
                    if op.case_sensitive:
                        new_text = re.sub(pattern, op.replace_text, old_text)
                    elif auto_case:
                        def _ww_repl(m, _rt=op.replace_text):
                            return self._apply_case_pattern(m.group(0), _rt)
                        new_text = re.sub(pattern, _ww_repl, old_text, flags=re.IGNORECASE)
                    else:
                        new_text = re.sub(pattern, op.replace_text, old_text, flags=re.IGNORECASE)
                else:  # Anything
                    if op.case_sensitive:
                        new_text = old_text.replace(op.find_text, op.replace_text)
                    elif auto_case:
                        pattern = re.escape(op.find_text)
                        def _any_repl(m, _rt=op.replace_text):
                            return self._apply_case_pattern(m.group(0), _rt)
                        new_text = re.sub(pattern, _any_repl, old_text, flags=re.IGNORECASE)
                    else:
                        pattern = re.escape(op.find_text)
                        new_text = re.sub(pattern, op.replace_text, old_text, flags=re.IGNORECASE)
                
                if new_text != old_text:
                    count += 1
                    if field_name == "source":
                        segment.source = new_text
                    else:
                        _undo_old_status = segment.status
                        _new_status = self._fr_status_after_edit(_undo_old_status)
                        segment.target = new_text
                        if _new_status != _undo_old_status:
                            segment.status = _new_status
                        try:
                            self.record_undo_state(segment.id, old_text, new_text, _undo_old_status, _new_status)
                        except Exception:
                            pass  # never let undo failure break the actual operation

        return count

    def find_next_match(self):
        """Find next occurrence of search term, filtering grid to show only matching rows"""
        find_text = self.find_input.text()
        if not find_text:
            return

        search_source = self.search_source_cb.isChecked()
        search_target = self.search_target_cb.isChecked()
        case_sensitive = self.case_sensitive_cb.isChecked()
        match_mode = self.match_group.checkedId()
        use_regex = self.regex_cb.isChecked()

        if use_regex and self._fr_compile_regex(find_text, case_sensitive) is None:
            return

        # Find all matches if not already done
        if not self.find_matches:
            self.find_all_matches_internal(find_text, search_source, search_target, case_sensitive, match_mode, use_regex)

            if self.find_matches:
                # Clear stale highlights from any previous search, then
                # apply the row-visibility filter.  We deliberately
                # avoid load_segments_to_grid() here — py-spy showed it
                # was ~64% of MainThread CPU just to toggle visibility.
                self._clear_search_highlights_in_cells(self._search_highlighted_cells)
                self._search_highlighted_cells = set()

                matching_rows = set(row for row, col in self.find_matches)
                segments = self.current_project.segments
                self.table.setUpdatesEnabled(False)
                try:
                    for row in range(len(segments)):
                        hidden = row not in matching_rows or not segments[row].source.strip()
                        self.table.setRowHidden(row, hidden)
                finally:
                    self.table.setUpdatesEnabled(True)

        if not self.find_matches:
            QMessageBox.information(self.find_replace_dialog, "Find", "No matches found.")
            return

        # Move to next match
        self.current_match_index = (self.current_match_index + 1) % len(self.find_matches)
        row, col = self.find_matches[self.current_match_index]

        # Highlight the current match
        segment = self.current_project.segments[row]
        text = segment.source if col == 2 else segment.target
        self.highlight_search_term(row, col, text, find_text)
        self._search_highlighted_cells.add((row, col))

        # Navigate to the match
        self.table.setCurrentCell(row, col)
        self.table.scrollToItem(self.table.item(row, col))
        self.log(f"Match {self.current_match_index + 1} of {len(self.find_matches)}")

    def find_all_matches(self):
        """Find and highlight all matches, filtering grid to show only matching rows"""
        find_text = self.find_input.text()
        if not find_text:
            return
        
        search_source = self.search_source_cb.isChecked()
        search_target = self.search_target_cb.isChecked()
        case_sensitive = self.case_sensitive_cb.isChecked()
        match_mode = self.match_group.checkedId()
        use_regex = self.regex_cb.isChecked()

        if use_regex and self._fr_compile_regex(find_text, case_sensitive) is None:
            return

        self.find_all_matches_internal(find_text, search_source, search_target, case_sensitive, match_mode, use_regex)

        if self.find_matches:
            # Clear stale highlights from any previous search, then
            # apply the row-visibility filter — without the
            # load_segments_to_grid() rebuild that previously dominated
            # this code path.
            self._clear_search_highlights_in_cells(self._search_highlighted_cells)
            self._search_highlighted_cells = set()

            matching_rows = set(row for row, col in self.find_matches)
            segments = self.current_project.segments
            self.table.setUpdatesEnabled(False)
            try:
                for row in range(len(segments)):
                    hidden = row not in matching_rows or not segments[row].source.strip()
                    self.table.setRowHidden(row, hidden)

                # Highlight all matches with yellow
                for row, col in self.find_matches:
                    segment = self.current_project.segments[row]
                    text = segment.source if col == 2 else segment.target
                    self.highlight_search_term(row, col, text, find_text)
                    self._search_highlighted_cells.add((row, col))
            finally:
                self.table.setUpdatesEnabled(True)

            # Jump to first match
            first_row, first_col = self.find_matches[0]
            self.table.setCurrentCell(first_row, first_col)
            self.table.scrollToItem(self.table.item(first_row, first_col))
            self.current_match_index = 0

            self.log(f"Found {len(self.find_matches)} match(es) in {len(matching_rows)} segment(s)")
        else:
            QMessageBox.information(self.find_replace_dialog, "Find", "No matches found.")

    def find_all_matches_internal(self, find_text, search_source, search_target, case_sensitive, match_mode, use_regex=False):
        """Internal method to find all matches"""
        import re

        self.find_matches = []

        for row, segment in enumerate(self.current_project.segments):
            texts_to_search = []
            if search_source:
                texts_to_search.append((segment.source, 2))
            if search_target:
                texts_to_search.append((segment.target, 3))

            for text, col in texts_to_search:
                if self.text_matches(text, find_text, case_sensitive, match_mode, use_regex):
                    self.find_matches.append((row, col))

    def text_matches(self, text, find_text, case_sensitive, match_mode, use_regex=False):
        """Check if text matches search criteria"""
        import re

        if use_regex:
            try:
                return bool(re.search(find_text, text, 0 if case_sensitive else re.IGNORECASE))
            except re.error:
                return False

        if match_mode == 2:  # Entire segment
            if case_sensitive:
                return text == find_text
            else:
                return text.lower() == find_text.lower()
        
        elif match_mode == 1:  # Whole words
            if case_sensitive:
                pattern = r'\b' + re.escape(find_text) + r'\b'
                return bool(re.search(pattern, text))
            else:
                pattern = r'\b' + re.escape(find_text) + r'\b'
                return bool(re.search(pattern, text, re.IGNORECASE))
        
        else:  # Anything (0)
            if case_sensitive:
                return find_text in text
            else:
                return find_text.lower() in text.lower()

    def _clear_search_highlights_in_cells(self, cells):
        """Clear yellow search-term highlights from each (row, col) cell.

        Used by find/replace to drop stale highlights from a previous
        search without forcing a full load_segments_to_grid() rebuild.
        Cells whose widget has been replaced since the highlight was
        applied are silently skipped.
        """
        if not cells:
            return
        from PyQt6.QtGui import QTextCursor, QTextCharFormat
        for row, col in cells:
            widget = self.table.cellWidget(row, col)
            if not widget or not hasattr(widget, 'document'):
                continue
            cursor = widget.textCursor()
            cursor.select(QTextCursor.SelectionType.Document)
            cursor.setCharFormat(QTextCharFormat())
            cursor.clearSelection()

    def highlight_search_term(self, row, col, text, find_text):
        """Highlight only the search term within the cell with yellow background.
        Uses the same system as filter highlighting - directly highlights within QTextEdit widget."""
        import re
        from PyQt6.QtGui import QTextCursor, QTextCharFormat, QColor
        
        if not text or not find_text:
            return
        
        # Get the cell widget (QTextEdit)
        widget = self.table.cellWidget(row, col)
        if not widget or not hasattr(widget, 'document'):
            return
        
        case_sensitive = self.case_sensitive_cb.isChecked()
        match_mode = self.match_group.checkedId()
        use_regex = self.regex_cb.isChecked()

        # Clear any existing highlights first
        cursor = widget.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        clear_format = QTextCharFormat()
        cursor.setCharFormat(clear_format)
        cursor.clearSelection()
        
        # Create yellow highlight format
        highlight_format = QTextCharFormat()
        highlight_format.setBackground(QColor("#FFFF00"))  # Yellow background
        
        # Get document text
        document = widget.document()
        cursor = QTextCursor(document)
        full_text = document.toPlainText()
        
        if use_regex:
            # Highlight every regex match span.
            try:
                _flags = 0 if case_sensitive else re.IGNORECASE
                for _m in re.finditer(find_text, full_text, _flags):
                    if _m.start() == _m.end():
                        continue  # skip zero-width matches (e.g. "a*")
                    cursor.setPosition(_m.start())
                    cursor.movePosition(QTextCursor.MoveOperation.Right,
                                        QTextCursor.MoveMode.KeepAnchor, _m.end() - _m.start())
                    cursor.mergeCharFormat(highlight_format)
            except re.error:
                pass
        elif match_mode == 2:  # Entire segment
            # Highlight entire text
            cursor.select(QTextCursor.SelectionType.Document)
            cursor.mergeCharFormat(highlight_format)
        elif match_mode == 1:  # Whole words
            # Find whole word matches using regex
            if case_sensitive:
                pattern = r'\b' + re.escape(find_text) + r'\b'
                matches = re.finditer(pattern, full_text)
            else:
                pattern = r'\b' + re.escape(find_text) + r'\b'
                matches = re.finditer(pattern, full_text, re.IGNORECASE)
            
            for match in matches:
                cursor.setPosition(match.start())
                cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, len(match.group()))
                cursor.mergeCharFormat(highlight_format)
        else:  # Anything (0)
            # Find all occurrences
            if case_sensitive:
                search_text = find_text
                text_to_search = full_text
            else:
                search_text = find_text.lower()
                text_to_search = full_text.lower()
            
            pos = 0
            while True:
                pos = text_to_search.find(search_text, pos)
                if pos == -1:
                    break
                
                cursor.setPosition(pos)
                cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, len(find_text))
                cursor.mergeCharFormat(highlight_format)
                pos += len(find_text)

    def replace_current_match(self):
        """Replace the currently selected match"""
        if self.current_match_index < 0 or self.current_match_index >= len(self.find_matches):
            QMessageBox.information(self.find_replace_dialog, "Replace", "No match selected. Use 'Find next' first.")
            return
        
        find_text = self.find_input.text()
        replace_text = self.replace_input.text()
        row, col = self.find_matches[self.current_match_index]
        
        # Safety check: prevent replacing in source if disabled
        if col == 2 and not self.allow_replace_in_source:  # Source column
            QMessageBox.warning(
                self.find_replace_dialog,
                "Replace in Source Disabled",
                "Replacing in source text is disabled for safety.\n\n"
                "To enable this feature, go to Settings > General and check 'Allow Replace in Source Text'."
            )
            return
        
        segment = self.current_project.segments[row]
        case_sensitive = self.case_sensitive_cb.isChecked()
        auto_case = self.auto_case_cb.isChecked()
        match_mode = self.match_group.checkedId()
        use_regex = self.regex_cb.isChecked()

        # Determine which field to update
        if col == 2:  # Source column
            field_text = segment.source
        else:  # col == 3, Target column
            field_text = segment.target

        import re
        from modules.tm_replace import make_replacer
        try:
            new_text = make_replacer(find_text, replace_text, case_sensitive=case_sensitive,
                                     auto_case=auto_case, match_mode=match_mode,
                                     use_regex=use_regex, count=1)(field_text)
        except re.error as _e:
            QMessageBox.warning(
                self.find_replace_dialog, "Invalid regular expression",
                f"The pattern or replacement is not valid:\n\n{_e}")
            return

        # Update the appropriate field
        if col == 2:
            segment.source = new_text
        else:
            old_status = segment.status
            old_target = segment.target
            segment.target = new_text
            # Optionally reset a finished segment to Draft so the change shows.
            new_status = self._fr_status_after_edit(old_status)
            if new_status != old_status:
                segment.status = new_status
                self._update_status_cell(row, segment)
                self.update_progress_stats()
            # Make a single "Replace this" undoable (text and status together).
            self.record_undo_state(segment.id, old_target, new_text, old_status, new_status)

        # Update table
        item = self.table.item(row, col)
        if item:
            item.setText(segment.target)

        self.project_modified = True
        self.update_window_title()
        
        # Refresh matches
        self.find_matches = []
        self.find_next_match()

    def replace_all_matches(self):
        """Replace all matches in target segments (optimized for speed)"""
        find_text = self.find_input.text()
        replace_text = self.replace_input.text()
        
        if not find_text:
            return
        
        search_source = self.search_source_cb.isChecked()
        search_target = self.search_target_cb.isChecked()
        case_sensitive = self.case_sensitive_cb.isChecked()
        auto_case = self.auto_case_cb.isChecked()
        match_mode = self.match_group.checkedId()
        use_regex = self.regex_cb.isChecked()

        if use_regex and self._fr_compile_regex(find_text, case_sensitive) is None:
            return

        # Safety check: warn if trying to replace in source when disabled
        if search_source and not self.allow_replace_in_source:
            reply = QMessageBox.warning(
                self.find_replace_dialog,
                "Replace in Source Disabled",
                "Replacing in source text is disabled for safety.\n\n"
                "Only target text will be replaced.\n\n"
                "To enable replacing in source, go to Settings > General and check 'Allow Replace in Source Text'.",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return
            # Continue with target only
            search_source = False
            if not search_target:
                return
        
        # Find all matches
        self.find_all_matches_internal(find_text, search_source, search_target, case_sensitive, match_mode, use_regex)

        import re
        from modules.tm_replace import make_replacer, replace_in_tms
        replacer = make_replacer(find_text, replace_text, case_sensitive=case_sensitive,
                                 auto_case=auto_case, match_mode=match_mode, use_regex=use_regex)

        # The writable TMs too, if asked (#68): counted now, changed after the
        # project, and only once the user has seen the numbers.
        try:
            tm_plan = self._fr_plan_tm_replace(replacer, search_source, search_target)
        except re.error as _e:  # e.g. a backreference to a group the pattern lacks
            QMessageBox.warning(
                self.find_replace_dialog, "Invalid regular expression",
                f"The pattern or replacement is not valid:\n\n{_e}")
            return
        tm_changes = tm_plan['result']['changed'] if tm_plan else 0

        if not self.find_matches and not tm_changes:
            QMessageBox.information(
                self.find_replace_dialog, "Replace All",
                "No matches found." + (" (None in the writable TMs either.)" if tm_plan else ""))
            return
        
        # Count matches by column
        source_count = sum(1 for _, col in self.find_matches if col == 2)
        target_count = sum(1 for _, col in self.find_matches if col == 3)
        
        # Filter out source matches if not allowed
        if not self.allow_replace_in_source:
            self.find_matches = [(row, col) for row, col in self.find_matches if col == 3]
            if not self.find_matches and not tm_changes:
                QMessageBox.information(self.find_replace_dialog, "Replace All", "No matches found in Target column.")
                return
        
        # Build confirmation message
        msg_parts = []
        if source_count > 0 and self.allow_replace_in_source:
            msg_parts.append(f"{source_count} in source")
        if target_count > 0:
            msg_parts.append(f"{target_count} in target")
        
        if self.find_matches:
            confirmation_msg = f"Replace {len(self.find_matches)} occurrence(s) of '{find_text}' with '{replace_text}'?\n\n"
        else:
            confirmation_msg = f"Replace '{find_text}' with '{replace_text}'?\n\nThere are no matches in the project itself."
        if msg_parts:
            confirmation_msg += f"({', '.join(msg_parts)})"
        
        # Extra warning if replacing in source
        if source_count > 0 and self.allow_replace_in_source:
            confirmation_msg += "\n\n⚠️ WARNING: This will modify source text!"

        if tm_plan:
            if tm_changes:
                examples = "\n".join(f"   {old}  →  {new}" for old, new in tm_plan['result']['examples'][:3])
                confirmation_msg += (
                    f"\n\nAlso in the writable TMs ({', '.join(tm_plan['names'])}): "
                    f"{tm_changes} entr{'y' if tm_changes == 1 else 'ies'} will change, e.g.:\n{examples}\n\n"
                    "⚠️ TM changes cannot be undone with Ctrl+Z.")
            else:
                confirmation_msg += "\n\n(No matching entries in the writable TMs.)"
        
        # Confirm
        reply = QMessageBox.question(
            self.find_replace_dialog,
            "Replace All",
            confirmation_msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return

        # For regex, validate the replacement template against a real match
        # before mutating anything (catches bad backreferences like \9 with no
        # group 9, which only error during substitution, not at compile time).
        if use_regex and self.find_matches:
            import re as _re
            try:
                _r0, _c0 = self.find_matches[0]
                _seg0 = self.current_project.segments[_r0]
                _sample = _seg0.source if _c0 == 2 else _seg0.target
                _re.sub(find_text, replace_text, _sample,
                        flags=0 if case_sensitive else _re.IGNORECASE)
            except _re.error as _e:
                QMessageBox.warning(
                    self.find_replace_dialog, "Invalid regular expression",
                    f"The pattern or replacement is not valid:\n\n{_e}")
                return

        # OPTIMIZATION: Disable UI updates during batch replacement
        self.table.setUpdatesEnabled(False)
        
        try:
            # Perform replacements
            replaced_count = 0
            updated_rows = set()  # Track which rows need UI updates
            
            for row, col in self.find_matches:
                segment = self.current_project.segments[row]
                
                # Get the appropriate field
                if col == 2:  # Source
                    old_text = segment.source
                else:  # col == 3, Target
                    old_text = segment.target
                
                # Perform replacement (whole words now stay whole words:
                # this used to replace inside longer words as well)
                new_text = replacer(old_text)

                if new_text != old_text:
                    replaced_count += 1
                    updated_rows.add(row)

                    # Update the appropriate field
                    if col == 2:
                        segment.source = new_text
                    else:
                        old_target = segment.target
                        old_status = segment.status
                        segment.target = new_text
                        # Optionally reset a finished segment to Draft so the
                        # change is visible, and refresh its status cell.
                        new_status = self._fr_status_after_edit(old_status)
                        if new_status != old_status:
                            segment.status = new_status
                            self._update_status_cell(row, segment)
                        # Record undo state for find/replace operation
                        self.record_undo_state(segment.id, old_target, new_text, old_status, new_status)
                    
                    # OPTIMIZATION: Update only the affected cell widget in-place
                    cell_widget = self.table.cellWidget(row, col)
                    if cell_widget and hasattr(cell_widget, 'setPlainText'):
                        cell_widget.blockSignals(True)
                        # Strip outer wrapping tags if setting is enabled
                        display_text = new_text
                        if self.hide_outer_wrapping_tags:
                            display_text, _ = strip_outer_wrapping_tags(display_text)
                        cell_widget.setPlainText(display_text)
                        cell_widget.blockSignals(False)
            
            self.project_modified = True
            self.update_window_title()
            # Confirmed/Draft counts may have changed if segments were demoted.
            self.update_progress_stats()

            # Clear matches
            self.find_matches = []
            
        finally:
            # OPTIMIZATION: Re-enable UI updates without full grid reload
            self.table.setUpdatesEnabled(True)
            # Trigger a repaint of updated rows only
            for row in updated_rows:
                self.table.viewport().update()
        
        summary = f"Replaced {replaced_count} occurrence(s)."
        if tm_changes:
            try:
                applied = replace_in_tms(self.db_manager, tm_plan['tm_ids'], replacer,
                                         in_source=tm_plan['in_source'],
                                         in_target=tm_plan['in_target'], apply=True)
                summary += f"\n\nTM entries changed: {applied['changed']}"
                if applied['merged']:
                    summary += (f" ({applied['merged']} became identical to an existing "
                                f"entry and were merged into it)")
                self.log(f"✓ Replaced '{find_text}' in {applied['changed']} TM entr"
                         f"{'y' if applied['changed'] == 1 else 'ies'} ({', '.join(tm_plan['names'])})")
                self._clear_caches_after_import()  # stale matches would still show the old text
            except Exception as e:
                summary += f"\n\n⚠️ The TMs could not be changed: {e}"
                self.log(f"✗ TM replace failed: {e}")
        QMessageBox.information(self.find_replace_dialog, "Replace All", summary)
        self.log(f"✓ Replaced {replaced_count} occurrence(s) of '{find_text}'")

    def _fr_plan_tm_replace(self, replacer, search_source, search_target):
        """Dry run of Replace all on the project's writable TMs (#68), or None
        when "Also in writable TMs" is off."""
        cb = getattr(self, 'fr_also_tm_cb', None)
        if cb is None or not cb.isChecked():
            return None
        from modules.tm_replace import replace_in_tms
        mgr = getattr(self, 'tm_metadata_mgr', None)
        project_id = getattr(self.current_project, 'id', None) if self.current_project else None
        tm_ids = mgr.get_writable_tm_ids(project_id) if (mgr and project_id) else []
        names = []
        for tm_id in tm_ids:
            info = mgr.get_tm_by_tm_id(tm_id) or {}
            names.append(info.get('name') or tm_id)
        in_source = bool(search_source and self.allow_replace_in_source)
        result = replace_in_tms(self.db_manager, tm_ids, replacer,
                                in_source=in_source, in_target=bool(search_target))
        if not tm_ids:
            self.log("ℹ️ Also in writable TMs: this project has no TM with Write ticked")
        return {'tm_ids': tm_ids, 'names': names or ["none"], 'result': result,
                'in_source': in_source, 'in_target': bool(search_target)}

    def highlight_all_matches(self):
        """Highlight all matches in the grid (without filtering rows)"""
        find_text = self.find_input.text()
        if not find_text:
            return
        
        search_source = self.search_source_cb.isChecked()
        search_target = self.search_target_cb.isChecked()
        case_sensitive = self.case_sensitive_cb.isChecked()
        match_mode = self.match_group.checkedId()
        
        # Find all matches first
        self.find_all_matches_internal(find_text, search_source, search_target, case_sensitive, match_mode)
        
        if self.find_matches:
            # Clear any stale highlights, then apply fresh ones in a
            # single repaint pass.  No grid reload needed.
            self._clear_search_highlights_in_cells(self._search_highlighted_cells)
            self._search_highlighted_cells = set()

            self.table.setUpdatesEnabled(False)
            try:
                for row, col in self.find_matches:
                    segment = self.current_project.segments[row]
                    text = segment.source if col == 2 else segment.target
                    self.highlight_search_term(row, col, text, find_text)
                    self._search_highlighted_cells.add((row, col))
            finally:
                self.table.setUpdatesEnabled(True)

            self.log(f"Highlighted {len(self.find_matches)} match(es)")
        else:
            QMessageBox.information(self.find_replace_dialog, "Highlight All", "No matches found.")

    def clear_search_highlights(self):
        """Clear all search highlights and unhide all rows (for Find & Replace dialog)"""
        # Targeted highlight clear — no full grid rebuild.
        self._clear_search_highlights_in_cells(self._search_highlighted_cells)
        self._search_highlighted_cells = set()

        # Unhide all rows (empty structural segments stay hidden via pagination)
        self._apply_pagination_to_grid()

        self.find_matches = []
        self.current_match_index = -1
        self.log("Search highlights cleared")

    def show_goto_dialog(self):
        """Show minimal dialog to jump to a specific segment - just type and press Enter"""
        if not self.current_project or not self.current_project.segments:
            QMessageBox.information(self, "No Project", "Please open a project first.")
            return
        
        max_segment = len(self.current_project.segments)
        
        # Create a minimal dialog
        from PyQt6.QtWidgets import QDialog, QLineEdit, QHBoxLayout
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QIntValidator
        
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Go to Segment"))
        dialog.setFixedWidth(250)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        
        layout = QHBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        
        label = QLabel(f"Segment (1-{max_segment}):")
        layout.addWidget(label)
        
        input_field = QLineEdit()
        input_field.setValidator(QIntValidator(1, max_segment))
        input_field.setPlaceholderText(str(self.table.currentRow() + 1))
        input_field.setFixedWidth(80)
        input_field.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(input_field)
        
        # Store reference for the handler
        self._goto_dialog = dialog
        self._goto_input = input_field
        self._goto_max = max_segment
        
        # Press Enter to jump directly
        def on_return_pressed():
            text = self._goto_input.text().strip()
            if text:
                try:
                    segment_num = int(text)
                    if 1 <= segment_num <= self._goto_max:
                        self._goto_dialog.accept()
                except ValueError:
                    pass
        
        input_field.returnPressed.connect(on_return_pressed)
        
        # Focus the input field
        input_field.setFocus()
        
        # Show dialog and handle result
        if dialog.exec() == QDialog.DialogCode.Accepted:
            text = input_field.text().strip()
            if text:
                try:
                    segment_num = int(text)
                    row = segment_num - 1
                    
                    # If pagination is active, switch to the correct page first
                    if hasattr(self, 'page_size_combo') and self.page_size_combo.currentText() != "All":
                        page_size = int(self.page_size_combo.currentText())
                        target_page = (row // page_size) + 1
                        # Set the page input and trigger navigation
                        if hasattr(self, 'page_number_input'):
                            self.page_number_input.setText(str(target_page))
                            self.go_to_page()
                    
                    # Select the cell
                    self.table.setCurrentCell(row, 3)  # Jump to Target column
                    self.table.scrollToItem(self.table.item(row, 0), QTableWidget.ScrollHint.PositionAtCenter)
                    
                    # Focus the target cell widget and place cursor inside
                    target_widget = self.table.cellWidget(row, 3)
                    if target_widget and isinstance(target_widget, QTextEdit):
                        target_widget.setFocus()
                        cursor = target_widget.textCursor()
                        cursor.movePosition(cursor.MoveOperation.End)
                        target_widget.setTextCursor(cursor)
                    
                    self.log(f"Jumped to segment {segment_num}")
                except ValueError:
                    pass

    def show_search_dialog(self):
        """Show enhanced search dialog with source/target options"""
        if not self.current_project or not self.current_project.segments:
            QMessageBox.information(self, "No Project", "Please open a project first.")
            return
        
        from PyQt6.QtWidgets import QDialog, QLineEdit, QRadioButton, QButtonGroup, QDialogButtonBox
        
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Find in Segments"))
        dialog.setMinimumWidth(400)
        
        layout = QVBoxLayout(dialog)
        
        # Search text input
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel(self.tr("Search for:")))
        search_input = QLineEdit()
        search_layout.addWidget(search_input)
        layout.addLayout(search_layout)
        
        # Search scope options
        scope_label = QLabel(self.tr("Search in:"))
        layout.addWidget(scope_label)
        
        radio_layout = QHBoxLayout()
        search_scope_group = QButtonGroup(dialog)
        
        both_radio = CheckmarkRadioButton(self.tr("Both Source and Target"))
        both_radio.setChecked(True)
        source_radio = CheckmarkRadioButton(self.tr("Source only"))
        target_radio = CheckmarkRadioButton(self.tr("Target only"))
        
        search_scope_group.addButton(both_radio, 0)
        search_scope_group.addButton(source_radio, 1)
        search_scope_group.addButton(target_radio, 2)
        
        radio_layout.addWidget(both_radio)
        radio_layout.addWidget(source_radio)
        radio_layout.addWidget(target_radio)
        layout.addLayout(radio_layout)
        
        # Dialog buttons
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)
        
        # Show dialog and process
        if dialog.exec() == QDialog.DialogCode.Accepted:
            search_text = search_input.text()
            if search_text:
                scope = search_scope_group.checkedId()
                self.search_segments(search_text, scope)

    def search_segments(self, search_text: str, scope: int = 0):
        """Search for text in segments and highlight ONLY the exact search term
        
        Args:
            search_text: Text to search for
            scope: 0=both, 1=source only, 2=target only
        """
        if not self.current_project:
            return
        
        self._ensure_primary_filters_ready()

        # Populate filter boxes based on scope
        if scope == 0:  # Both
            self.source_filter.setText(search_text)
            self.target_filter.setText(search_text)
        elif scope == 1:  # Source only
            self.source_filter.setText(search_text)
            self.target_filter.clear()
        elif scope == 2:  # Target only
            self.source_filter.clear()
            self.target_filter.setText(search_text)
        
        # The filter boxes will trigger apply_filters() automatically
        # which handles highlighting and hiding
        
        # Find first match and jump to it
        search_lower = search_text.lower()
        for row, segment in enumerate(self.current_project.segments):
            source_match = scope != 2 and search_lower in segment.source.lower()
            target_match = scope != 1 and search_lower in segment.target.lower()
            
            if source_match or target_match:
                self.table.setCurrentCell(row, 3)
                self.table.scrollToItem(self.table.item(row, 3))
                break

    def _highlight_text_in_widget(self, row: int, col: int, search_term):
        """Highlight search term within a QTextEdit cell widget.

        ``search_term`` is plain text or a modules.grid_filter.GridFilter (whose
        spans also cover /regex/ filters).
        
        Since source/target cells use setCellWidget() with QTextEdit editors,
        the delegate's paint() method is bypassed. We must highlight the text
        directly within the widget using QTextCursor and QTextCharFormat.
        
        NOTE: This method only ADDS yellow highlights - it does not clear existing
        formatting. Use _clear_filter_highlights_in_widget() to remove highlights.
        """
        widget = self.table.cellWidget(row, col)
        if not widget or not hasattr(widget, 'document'):
            return
        
        # Create yellow highlight format
        highlight_format = QTextCharFormat()
        highlight_format.setBackground(QColor("#FFFF00"))  # Yellow background
        
        # Find and highlight all occurrences (case-insensitive)
        from modules.grid_filter import GridFilter
        spec = search_term if isinstance(search_term, GridFilter) else GridFilter(search_term)
        document = widget.document()
        cursor = QTextCursor(document)
        for start, end in spec.spans(document.toPlainText()):
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            cursor.mergeCharFormat(highlight_format)
