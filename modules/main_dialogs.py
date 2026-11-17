"""Dialogs used by the main window: theme editor, detached log, advanced
filters, scratchpad, live progress and the shared import progress dialog.
Moved unchanged from Supervertaler.py.
"""

import time
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor, QFont, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from contextlib import contextmanager
from modules.styled_widgets import CheckmarkCheckBox, CheckmarkRadioButton


class ThemeEditorDialog(QDialog):
    """Dialog for editing and managing themes"""
    
    def __init__(self, parent, theme_manager):
        super().__init__(parent)
        self.theme_manager = theme_manager
        self.setWindowTitle(self.tr("Theme Editor"))
        self.setModal(True)
        self.resize(700, 600)
        
        self.setup_ui()
        self.load_themes()
    
    def setup_ui(self):
        """Create the UI"""
        layout = QVBoxLayout(self)
        
        # Theme selection
        theme_group = QGroupBox(self.tr("Select Theme"))
        theme_layout = QHBoxLayout()
        
        self.theme_combo = QComboBox()
        self.theme_combo.currentTextChanged.connect(self.on_theme_selected)
        theme_layout.addWidget(QLabel(self.tr("Theme:")))
        theme_layout.addWidget(self.theme_combo, 1)
        
        self.apply_btn = QPushButton(self.tr("✓ Apply"))
        self.apply_btn.clicked.connect(self.apply_theme)
        theme_layout.addWidget(self.apply_btn)
        
        theme_group.setLayout(theme_layout)
        layout.addWidget(theme_group)
        
        # Color customization
        colors_group = QGroupBox(self.tr("Customize Colors"))
        colors_layout = QGridLayout()
        
        # Create color pickers for main colors
        self.color_buttons = {}
        color_configs = [
            ("window_bg", "Window Background", 0, 0),
            ("base", "Input Fields", 0, 1),
            ("button", "Buttons", 0, 2),
            ("text", "Text", 1, 0),
            ("highlight", "Highlight", 1, 1),
            ("border", "Borders", 1, 2),
            ("grid_header", "Table Headers", 2, 0),
            ("alternate_bg", "Alternate Rows", 2, 1),
            ("tm_exact", "100% TM Match", 3, 0),
            ("tm_high", "95-99% TM Match", 3, 1),
        ]
        
        for attr, label, row, col in color_configs:
            lbl = QLabel(label + ":")
            btn = QPushButton()
            btn.setMinimumHeight(30)
            btn.setProperty("color_attr", attr)
            btn.clicked.connect(lambda checked, a=attr: self.pick_color(a))
            self.color_buttons[attr] = btn
            
            colors_layout.addWidget(lbl, row * 2, col)
            colors_layout.addWidget(btn, row * 2 + 1, col)
        
        colors_group.setLayout(colors_layout)
        layout.addWidget(colors_group)
        
        # Custom theme actions
        custom_group = QGroupBox(self.tr("Custom Themes"))
        custom_layout = QHBoxLayout()
        
        save_btn = QPushButton(self.tr("💾 Save as Custom Theme"))
        save_btn.clicked.connect(self.save_custom_theme)
        custom_layout.addWidget(save_btn)
        
        delete_btn = QPushButton(self.tr("🗑️ Delete Custom Theme"))
        delete_btn.clicked.connect(self.delete_custom_theme)
        custom_layout.addWidget(delete_btn)
        
        custom_group.setLayout(custom_layout)
        layout.addWidget(custom_group)
        
        # Dialog buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | 
            QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
    
    def load_themes(self):
        """Load available themes into combo box"""
        self.theme_combo.clear()
        themes = self.theme_manager.get_all_themes()
        for theme_name in themes.keys():
            self.theme_combo.addItem(theme_name)
        
        # Select current theme
        current_idx = self.theme_combo.findText(self.theme_manager.current_theme.name)
        if current_idx >= 0:
            self.theme_combo.setCurrentIndex(current_idx)
    
    def on_theme_selected(self, theme_name):
        """When theme is selected from combo box"""
        if not theme_name:
            return
        
        theme = self.theme_manager.get_theme(theme_name)
        if theme:
            self.update_color_buttons(theme)
    
    def update_color_buttons(self, theme):
        """Update color button backgrounds"""
        for attr, btn in self.color_buttons.items():
            color = getattr(theme, attr, "#FFFFFF")
            btn.setStyleSheet(f"background-color: {color}; border: 1px solid #999;")
            btn.setText(color)
    
    def pick_color(self, attr):
        """Open color picker for an attribute"""
        from PyQt6.QtWidgets import QColorDialog
        
        theme_name = self.theme_combo.currentText()
        theme = self.theme_manager.get_theme(theme_name)
        
        if not theme:
            return
        
        current_color = QColor(getattr(theme, attr))
        color = QColorDialog.getColor(current_color, self, f"Pick {attr}")
        
        if color.isValid():
            # Update button
            hex_color = color.name()
            self.color_buttons[attr].setStyleSheet(
                f"background-color: {hex_color}; border: 1px solid #999;"
            )
            self.color_buttons[attr].setText(hex_color)
            
            # Update theme
            setattr(theme, attr, hex_color)
    
    def apply_theme(self):
        """Apply the selected theme"""
        theme_name = self.theme_combo.currentText()
        if self.theme_manager.set_theme(theme_name):
            self.theme_manager.apply_theme(QApplication.instance())

            # Call the main app's refresh method to update all UI elements
            if hasattr(self.parent(), 'refresh_theme_colors'):
                self.parent().refresh_theme_colors()

            QMessageBox.information(self, "Theme Applied",
                                   f"Theme '{theme_name}' has been applied.")
    
    def save_custom_theme(self):
        """Save current settings as a custom theme"""
        from PyQt6.QtWidgets import QInputDialog
        
        name, ok = QInputDialog.getText(self, "Save Theme", "Enter theme name:")
        if ok and name:
            # Create new theme from current settings
            theme_name = self.theme_combo.currentText()
            base_theme = self.theme_manager.get_theme(theme_name)
            
            if base_theme:
                # Create copy with new name
                from modules.theme_manager import Theme
                new_theme = Theme(
                    name=name,
                    **{k: v for k, v in base_theme.to_dict().items() if k != 'name'}
                )
                
                # Update with any modified colors
                for attr, btn in self.color_buttons.items():
                    color = btn.text()
                    if color.startswith('#'):
                        setattr(new_theme, attr, color)
                
                self.theme_manager.save_custom_theme(new_theme)
                self.load_themes()
                
                # Select the new theme
                idx = self.theme_combo.findText(name)
                if idx >= 0:
                    self.theme_combo.setCurrentIndex(idx)
                
                QMessageBox.information(self, "Theme Saved", 
                                       f"Custom theme '{name}' has been saved.")
    
    def delete_custom_theme(self):
        """Delete the selected custom theme"""
        theme_name = self.theme_combo.currentText()
        
        # Can't delete predefined themes
        from modules.theme_manager import ThemeManager
        if theme_name in ThemeManager.PREDEFINED_THEMES:
            QMessageBox.warning(self, "Cannot Delete", 
                               "Cannot delete predefined themes.")
            return
        
        reply = QMessageBox.question(self, "Delete Theme",
                                    f"Delete custom theme '{theme_name}'?",
                                    QMessageBox.StandardButton.Yes | 
                                    QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            if self.theme_manager.delete_custom_theme(theme_name):
                self.load_themes()
                QMessageBox.information(self, "Theme Deleted", 
                                       f"Theme '{theme_name}' has been deleted.")


class DetachedLogWindow(QWidget):
    """Detached log window that can be moved to another screen"""

    def __init__(self, parent):
        super().__init__()
        self.parent = parent
        self.setWindowTitle(self.tr("Supervertaler Workbench - Session Log"))
        self.setWindowIcon(self.parent.windowIcon())
        self.resize(800, 600)

        # Create layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # Top toolbar
        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)

        # Info label
        info_label = QLabel(self.tr("📋 This is a detached log window. It will update in real-time."))
        info_label.setStyleSheet("color: #666; font-style: italic;")
        toolbar_layout.addWidget(info_label)

        toolbar_layout.addStretch()

        # Re-attach button
        reattach_btn = QPushButton(self.tr("↩️ Close"))
        reattach_btn.setToolTip(self.tr("Close this detached window"))
        reattach_btn.clicked.connect(self.close)
        toolbar_layout.addWidget(reattach_btn)

        layout.addWidget(toolbar)

        # Log display
        self.log_display = QPlainTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setStyleSheet("""
            QPlainTextEdit {
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 10px;
                border: 1px solid #ccc;
            }
        """)
        layout.addWidget(self.log_display)

        # Copy existing log content. Prefer the always-built session_log_text
        # buffer; fall back to the Settings ▸ Log widget if that path ran first.
        source_widget = None
        if hasattr(parent, 'session_log_text') and parent.session_log_text:
            source_widget = parent.session_log_text
        elif hasattr(parent, 'session_log') and parent.session_log:
            source_widget = parent.session_log
        if source_widget is not None:
            self.log_display.setPlainText(source_widget.toPlainText())
            # Scroll to bottom
            scrollbar = self.log_display.verticalScrollBar()
            if scrollbar:
                scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event):
        """Handle window close"""
        # Remove from parent's list
        try:
            if self in self.parent.detached_log_windows:
                self.parent.detached_log_windows.remove(self)
        except:
            pass
        event.accept()


class AdvancedFiltersDialog(QDialog):
    """Dialog for advanced filtering options"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
    
    def setup_ui(self):
        self.setWindowTitle(self.tr("Advanced Filters"))
        self.setMinimumWidth(600)
        self.setMinimumHeight(500)
        layout = QVBoxLayout(self)
        
        # Create scroll area for filters
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(15)
        
        # Match Rate Filter
        match_group = QGroupBox(self.tr("Match Rate (%)"))
        match_layout = QHBoxLayout()
        
        self.match_rate_check = CheckmarkCheckBox(self.tr("Enable"))
        match_layout.addWidget(self.match_rate_check)
        
        match_layout.addWidget(QLabel(self.tr("From:")))
        self.match_min_spin = QSpinBox()
        self.match_min_spin.setRange(0, 102)
        self.match_min_spin.setValue(0)
        self.match_min_spin.setSuffix("%")
        match_layout.addWidget(self.match_min_spin)
        
        match_layout.addWidget(QLabel(self.tr("To:")))
        self.match_max_spin = QSpinBox()
        self.match_max_spin.setRange(0, 102)
        self.match_max_spin.setValue(102)
        self.match_max_spin.setSuffix("%")
        match_layout.addWidget(self.match_max_spin)
        match_layout.addStretch()
        
        match_group.setLayout(match_layout)
        content_layout.addWidget(match_group)
        
        # Row Status Filter
        status_group = QGroupBox(self.tr("Row Status"))
        status_layout = QVBoxLayout()
        
        self.status_not_started = CheckmarkCheckBox(self.tr("Not started"))
        self.status_edited = CheckmarkCheckBox(self.tr("Edited"))
        self.status_pretranslated = CheckmarkCheckBox(self.tr("Pre-translated"))
        self.status_translated = CheckmarkCheckBox(self.tr("Translated"))
        self.status_confirmed = CheckmarkCheckBox(self.tr("Confirmed"))
        self.status_draft = CheckmarkCheckBox(self.tr("Draft"))

        status_layout.addWidget(self.status_not_started)
        status_layout.addWidget(self.status_edited)
        status_layout.addWidget(self.status_pretranslated)
        status_layout.addWidget(self.status_translated)
        status_layout.addWidget(self.status_confirmed)
        status_layout.addWidget(self.status_draft)

        # Match Origin statuses
        match_origin_label = QLabel(self.tr("Match Origin"))
        match_origin_label.setStyleSheet("font-weight: bold; color: #666; margin-top: 8px; margin-bottom: 2px;")
        status_layout.addWidget(match_origin_label)

        self.status_pm = CheckmarkCheckBox(self.tr("PM (102%)"))
        self.status_cm = CheckmarkCheckBox(self.tr("CM (101%)"))
        self.status_tm_100 = CheckmarkCheckBox(self.tr("TM 100%"))
        self.status_tm_fuzzy = CheckmarkCheckBox(self.tr("TM Fuzzy"))
        self.status_repetition = CheckmarkCheckBox(self.tr("Repetition"))
        self.status_mt = CheckmarkCheckBox(self.tr("MT"))

        status_layout.addWidget(self.status_pm)
        status_layout.addWidget(self.status_cm)
        status_layout.addWidget(self.status_tm_100)
        status_layout.addWidget(self.status_tm_fuzzy)
        status_layout.addWidget(self.status_repetition)
        status_layout.addWidget(self.status_mt)

        status_group.setLayout(status_layout)
        content_layout.addWidget(status_group)
        
        # Locked/Unlocked Filter
        locked_group = QGroupBox(self.tr("Locked Status"))
        locked_layout = QVBoxLayout()
        
        self.locked_both = CheckmarkRadioButton(self.tr("Both locked and unlocked rows"))
        self.locked_only = CheckmarkRadioButton(self.tr("Only locked rows"))
        self.locked_unlocked_only = CheckmarkRadioButton(self.tr("Only unlocked rows"))
        self.locked_both.setChecked(True)
        
        locked_layout.addWidget(self.locked_both)
        locked_layout.addWidget(self.locked_only)
        locked_layout.addWidget(self.locked_unlocked_only)
        
        locked_group.setLayout(locked_layout)
        content_layout.addWidget(locked_group)
        
        # Other Properties
        other_group = QGroupBox(self.tr("Other Properties"))
        other_layout = QVBoxLayout()
        
        self.has_comments_check = CheckmarkCheckBox(self.tr("Has comments/notes"))
        self.has_proofreading_check = CheckmarkCheckBox(self.tr("Has proofreading issues"))
        self.repetitions_check = CheckmarkCheckBox(self.tr("Repetitions only"))
        self.auto_propagated_check = CheckmarkCheckBox(self.tr("Auto-propagated"))
        
        other_layout.addWidget(self.has_comments_check)
        other_layout.addWidget(self.has_proofreading_check)
        other_layout.addWidget(self.repetitions_check)
        other_layout.addWidget(self.auto_propagated_check)
        
        other_group.setLayout(other_layout)
        content_layout.addWidget(other_group)
        
        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        reset_btn = QPushButton(self.tr("Reset All"))
        reset_btn.clicked.connect(self.reset_filters)
        button_layout.addWidget(reset_btn)
        
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        apply_btn = QPushButton(self.tr("Apply Filters"))
        apply_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 5px 15px; border: none; outline: none;")
        apply_btn.clicked.connect(self.accept)
        apply_btn.setDefault(True)
        button_layout.addWidget(apply_btn)
        
        layout.addLayout(button_layout)
    
    def reset_filters(self):
        """Reset all filters to default"""
        self.match_rate_check.setChecked(False)
        self.match_min_spin.setValue(0)
        self.match_max_spin.setValue(102)

        self.status_not_started.setChecked(False)
        self.status_edited.setChecked(False)
        self.status_pretranslated.setChecked(False)
        self.status_translated.setChecked(False)
        self.status_confirmed.setChecked(False)
        self.status_draft.setChecked(False)
        self.status_pm.setChecked(False)
        self.status_cm.setChecked(False)
        self.status_tm_100.setChecked(False)
        self.status_tm_fuzzy.setChecked(False)
        self.status_repetition.setChecked(False)
        self.status_mt.setChecked(False)
        
        self.locked_both.setChecked(True)
        
        self.has_comments_check.setChecked(False)
        self.has_proofreading_check.setChecked(False)
        self.repetitions_check.setChecked(False)
        self.auto_propagated_check.setChecked(False)
    
    def get_filters(self):
        """Return dictionary of filter settings"""
        filters = {}
        
        # Match rate
        filters['match_rate_enabled'] = self.match_rate_check.isChecked()
        filters['match_rate_min'] = self.match_min_spin.value()
        filters['match_rate_max'] = self.match_max_spin.value()
        
        # Row status
        row_status = []
        if self.status_not_started.isChecked():
            row_status.append('not_started')
        if self.status_edited.isChecked():
            row_status.append('edited')
        if self.status_pretranslated.isChecked():
            row_status.append('pretranslated')
        if self.status_translated.isChecked():
            row_status.append('draft')
        if self.status_confirmed.isChecked():
            row_status.append('confirmed')
        if self.status_draft.isChecked():
            row_status.append('draft')
        if self.status_pm.isChecked():
            row_status.append('pm')
        if self.status_cm.isChecked():
            row_status.append('cm')
        if self.status_tm_100.isChecked():
            row_status.append('tm_100')
        if self.status_tm_fuzzy.isChecked():
            row_status.append('tm_fuzzy')
        if self.status_repetition.isChecked():
            row_status.append('repetition')
        if self.status_mt.isChecked():
            row_status.append('machine_translated')
        filters['row_status'] = row_status
        
        # Locked filter
        if self.locked_only.isChecked():
            filters['locked_filter'] = 'locked'
        elif self.locked_unlocked_only.isChecked():
            filters['locked_filter'] = 'unlocked'
        else:
            filters['locked_filter'] = None
        
        # Other properties
        filters['has_comments'] = self.has_comments_check.isChecked()
        filters['has_proofreading'] = self.has_proofreading_check.isChecked()
        filters['repetitions_only'] = self.repetitions_check.isChecked()
        filters['auto_propagated'] = self.auto_propagated_check.isChecked()
        
        return filters


class ScratchpadDialog(QDialog):
    """Dialog for private translator notes (scratchpad).
    
    Notes are stored only in the .svproj file and never exported to CAT tools.
    """
    
    def __init__(self, parent=None, notes_text: str = ""):
        super().__init__(parent)
        self.notes_text = notes_text
        self.setup_ui()
    
    def setup_ui(self):
        self.setWindowTitle(self.tr("📝 Scratchpad - Private Notes"))
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        self.resize(600, 450)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        
        # Info label
        info_label = QLabel(
            "📝 <b>Private notes for this project</b><br>"
            "<small>These notes are saved with your project (.svproj) file and are <b>never</b> "
            "exported to CAT tools or shared with clients.</small>"
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; padding: 5px; background: #f9f9f9; border-radius: 4px;")
        layout.addWidget(info_label)
        
        # Notes text area
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlainText(self.notes_text)
        self.notes_edit.setPlaceholderText(
            "Use this scratchpad for your private notes during translation:\n\n"
            "• Terminology decisions\n"
            "• Client preferences\n"
            "• Research findings\n"
            "• Questions to ask\n"
            "• Reminders for yourself"
        )
        # Nice monospace-ish font for notes
        font = self.notes_edit.font()
        font.setFamily("Consolas, Courier New, monospace")
        font.setPointSize(10)
        self.notes_edit.setFont(font)
        layout.addWidget(self.notes_edit, 1)  # Stretch to fill
        
        # Button row
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        save_btn = QPushButton(self.tr("💾 Save Notes"))
        save_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 6px 16px; border: none;")
        save_btn.clicked.connect(self.accept)
        save_btn.setDefault(True)
        button_layout.addWidget(save_btn)
        
        layout.addLayout(button_layout)
    
    def get_notes(self) -> str:
        """Return the current notes text."""
        return self.notes_edit.toPlainText()


class LiveProgressDialog(QDialog):
    """Progress dialog with live console output."""
    
    def __init__(self, parent, total_segments, provider_info):
        super().__init__(parent)
        self.total_segments = total_segments
        self.provider_info = provider_info
        self.start_time = time.time()
        self.segment_times = []  # Track processing times for estimation
        
        self.setWindowTitle(self.tr("Batch Translation Progress"))
        self.setMinimumSize(800, 600)
        
        # Main layout
        layout = QVBoxLayout(self)
        
        # Header
        header_label = QLabel(f"<h3>🚀 Translating {total_segments} segment{'s' if total_segments != 1 else ''}</h3>")
        layout.addWidget(header_label)
        
        # Provider info
        provider_label = QLabel(provider_info)
        provider_label.setStyleSheet("color: #666; padding: 5px 0;")
        layout.addWidget(provider_label)
        
        # Progress info section
        info_layout = QHBoxLayout()
        self.progress_label = QLabel("0/0 (0%)")
        self.time_label = QLabel(self.tr("Elapsed: 0:00 | Remaining: --:--"))
        self.speed_label = QLabel(self.tr("Speed: -- seg/min"))
        info_layout.addWidget(self.progress_label)
        info_layout.addStretch()
        info_layout.addWidget(self.time_label)
        info_layout.addWidget(self.speed_label)
        layout.addLayout(info_layout)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(total_segments)
        layout.addWidget(self.progress_bar)
        
        # Console output
        console_label = QLabel(self.tr("<b>Console Output:</b>"))
        layout.addWidget(console_label)
        
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setFont(QFont("Consolas", 9))
        self.console.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: 1px solid #3c3c3c;
            }
        """)
        layout.addWidget(self.console)
        
        # Statistics
        self.stats_label = QLabel(self.tr("✓ Success: 0  |  ✗ Errors: 0"))
        self.stats_label.setStyleSheet("padding: 5px 0; font-weight: bold;")
        layout.addWidget(self.stats_label)
        
        # Button section
        button_layout = QHBoxLayout()
        self.cancel_btn = QPushButton(self.tr("Cancel"))
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addStretch()
        button_layout.addWidget(self.cancel_btn)
        layout.addLayout(button_layout)
        
        # Timer for elapsed time updates
        self.timer = QTimer()
        self.timer.timeout.connect(self._update_time_display)
        self.timer.start(1000)  # Update every second
        
        # Initial console message
        self.add_console_line(f"Starting batch translation: {total_segments} segments", True)
        self.add_console_line(provider_info, True)
        self.add_console_line("-" * 80, True)
    
    def add_console_line(self, message, is_success=True):
        """Add a line to the console with color coding."""
        cursor = self.console.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        
        # Set text color
        fmt = cursor.charFormat()
        if is_success:
            fmt.setForeground(QColor("#4ec9b0"))  # Greenish (VS Code style)
        else:
            fmt.setForeground(QColor("#f48771"))  # Reddish (VS Code style)
        
        cursor.setCharFormat(fmt)
        cursor.insertText(message + "\n")
        
        # Auto-scroll to bottom
        self.console.setTextCursor(cursor)
        self.console.ensureCursorVisible()
    
    def update_progress(self, current, total, elapsed_seconds, success_count, error_count):
        """Update progress bar and labels."""
        self.progress_bar.setValue(current)
        
        # Update progress label
        percent = int((current / total) * 100) if total > 0 else 0
        self.progress_label.setText(f"{current}/{total} ({percent}%)")
        
        # Update statistics
        self.stats_label.setText(f"✓ Success: {success_count}  |  ✗ Errors: {error_count}")
        
        # Track timing
        if elapsed_seconds > 0:
            self.segment_times.append(elapsed_seconds)
        
        # Calculate speed and estimate remaining time
        if len(self.segment_times) > 0:
            avg_time = sum(self.segment_times) / len(self.segment_times)
            speed = 60 / avg_time if avg_time > 0 else 0
            remaining_segments = total - current
            remaining_seconds = remaining_segments * avg_time
            
            # Update labels
            self.speed_label.setText(f"Speed: {speed:.1f} seg/min")
            
            remaining_str = self._format_time(remaining_seconds)
            elapsed_str = self._format_time(time.time() - self.start_time)
            self.time_label.setText(f"Elapsed: {elapsed_str} | Remaining: {remaining_str}")
    
    def _update_time_display(self):
        """Update elapsed time display (called every second)."""
        if self.progress_bar.value() < self.total_segments:
            elapsed = time.time() - self.start_time
            current_text = self.time_label.text()
            # Update only elapsed portion
            if " | " in current_text:
                remaining_part = current_text.split(" | ")[1]
                self.time_label.setText(f"Elapsed: {self._format_time(elapsed)} | {remaining_part}")
    
    @staticmethod
    def _format_time(seconds):
        """Format seconds as MM:SS."""
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}:{secs:02d}"
    
    def show_completion_message(self, success_count, error_count):
        """Show completion summary in console."""
        self.add_console_line("", True)  # Blank line
        self.add_console_line("=" * 80, True)
        self.add_console_line(f"Translation Complete!", True)
        self.add_console_line(f"✓ Success: {success_count}", True)
        if error_count > 0:
            self.add_console_line(f"✗ Errors: {error_count}", False)
        total_time = time.time() - self.start_time
        self.add_console_line(f"Total time: {self._format_time(total_time)}", True)
        self.add_console_line("=" * 80, True)
        
        # Change button to "Close"
        self.cancel_btn.setText(self.tr("Close"))


class _ImportProgressDialog:
    """Context-managed progress dialog spanning a full import pipeline.

    Usage::

        with _ImportProgressDialog(self, "Importing SDLXLIFF",
                                    initial_label=f"Opening {name}…",
                                    initial_total=len(file_paths)) as prog:
            handler.load(file_paths, progress_callback=prog.parse_callback)
            if prog.cancelled():
                return
            with prog.suspended():
                reply = QMessageBox.question(self, …)
            prog.set_phase(f"Loading {n} segments into grid…", total=n)
            self.load_segments_to_grid(progress_callback=prog.grid_callback)
            prog.set_phase("Finalising import…")
            …
    """

    def __init__(self, parent, title: str = "Importing",
                 initial_label: str = "Working…",
                 initial_total: int = 1):
        self._parent = parent
        self._title = title
        self._initial_label = initial_label
        self._initial_total = max(int(initial_total), 1)
        self._dialog = None

    def __enter__(self):
        self._dialog = QProgressDialog(
            self._initial_label, "Cancel", 0, self._initial_total, self._parent
        )
        self._dialog.setWindowTitle(self._title)
        self._dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self._dialog.setMinimumDuration(0)
        self._dialog.setAutoClose(False)
        self._dialog.setAutoReset(False)
        self._dialog.setValue(0)
        QApplication.processEvents()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._dialog is not None:
            try:
                self._dialog.close()
            except Exception:
                pass
            self._dialog = None
        return False  # propagate exceptions

    # --- state ----------------------------------------------------------------

    def cancelled(self) -> bool:
        return self._dialog is not None and self._dialog.wasCanceled()

    # --- phase control --------------------------------------------------------

    def set_phase(self, label: str, total: int = 0) -> None:
        """Begin a new phase. ``total=0`` switches to an indeterminate (busy)
        bar; any positive total sets a determinate range with progress 0."""
        if self._dialog is None:
            return
        if total > 0:
            self._dialog.setRange(0, int(total))
            self._dialog.setValue(0)
        else:
            self._dialog.setRange(0, 0)  # busy/indeterminate
        self._dialog.setLabelText(label)
        QApplication.processEvents()

    def update(self, value: int, total: int = None, label: str = None) -> bool:
        """Update progress. Returns ``False`` if the user has cancelled."""
        if self._dialog is None:
            return True
        if self.cancelled():
            return False
        if total is not None and total > 0:
            self._dialog.setRange(0, int(total))
        self._dialog.setValue(int(value))
        if label is not None:
            self._dialog.setLabelText(label)
        QApplication.processEvents()
        return not self.cancelled()

    # --- ready-made callbacks for common producers ---------------------------

    def parse_callback(self, stage: str, current: int, total: int,
                       message: str) -> bool:
        """Callback matching the (stage, current, total, message) → continue
        protocol used by StandaloneSDLXLIFFHandler.load and friends."""
        if self._dialog is None:
            return False
        if self.cancelled():
            return False
        self._dialog.setRange(0, max(int(total), 1))
        self._dialog.setValue(int(current))
        label = f"{message} ({current}/{total})" if total else message
        self._dialog.setLabelText(label)
        QApplication.processEvents()
        return not self.cancelled()

    def grid_callback(self, rows_done: int, total: int) -> None:
        """Callback matching the (rows_done, total) protocol used by
        ``SupervertalerQt.load_segments_to_grid``."""
        if self._dialog is None:
            return
        if self.cancelled():
            return
        # Repaint at most ten times a second (issue #185): each repaint pumps
        # the event loop, and doing it every few rows added seconds to a big load.
        now = time.monotonic()
        if rows_done < total and now - getattr(self, '_last_grid_repaint', 0.0) < 0.1:
            return
        self._last_grid_repaint = now
        self._dialog.setValue(int(rows_done))
        self._dialog.setLabelText(
            f"Loading segments into grid… ({rows_done:,}/{total:,})"
        )
        QApplication.processEvents()

    # --- suspend (for nested modal dialogs) ----------------------------------

    @contextmanager
    def suspended(self):
        """Temporarily hide the dialog (e.g. for a ``QMessageBox.question``
        the user must answer mid-import) and re-show it on exit."""
        if self._dialog is not None:
            self._dialog.hide()
        try:
            yield
        finally:
            if self._dialog is not None:
                self._dialog.show()
                QApplication.processEvents()
