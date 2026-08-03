"""
Keyboard Shortcuts Settings Widget
Provides UI for viewing, editing, and managing keyboard shortcuts
"""

from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QLineEdit, QLabel, QDialog,
    QDialogButtonBox, QMessageBox, QFileDialog, QGroupBox, QCheckBox,
    QStyleOptionButton, QSplitter
)
from PyQt6.QtCore import Qt, QEvent, QPointF, QRect
from PyQt6.QtGui import QKeySequence, QKeyEvent, QFont, QPainter, QPen, QColor

from modules.shortcut_manager import ShortcutManager
from modules.shortcut_display import format_shortcut_for_display, format_shortcuts_in_text
from modules.platform_helpers import IS_WINDOWS, IS_MACOS, IS_LINUX
from modules.ui_scale import scaled_pt
from modules.styled_widgets import HelpButton
from modules.help_system import Topics as HelpTopics


from modules.styled_widgets import CheckmarkCheckBox  # noqa: E402


class KeySequenceEdit(QLineEdit):
    """Custom widget for capturing keyboard shortcuts.

    Internally stores Qt-style names (e.g. ``Ctrl+Shift+C``) on
    ``current_sequence`` — that's what the rest of the app expects. The
    displayed text is platform-formatted (``⌘⇧C`` on macOS, plain text
    elsewhere) via ``format_shortcut_for_display`` so users see the
    symbols matching the keys they actually pressed.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Press keys or click to edit...")
        self.setReadOnly(False)
        self.current_sequence = ""

    def setShortcut(self, raw_sequence: str):
        """Set the shortcut from a raw Qt-format string and refresh display."""
        self.current_sequence = raw_sequence or ""
        display = format_shortcut_for_display(raw_sequence) if raw_sequence else ""
        self.setText(display)

    def keyPressEvent(self, event: QKeyEvent):
        """Capture key press and convert to shortcut string"""
        # Ignore modifier-only presses
        if event.key() in (Qt.Key.Key_Control, Qt.Key.Key_Shift,
                          Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            return

        # Build key sequence from modifiers + key
        modifiers = event.modifiers()
        key = event.key()

        parts = []
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("Ctrl")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("Alt")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("Shift")
        if modifiers & Qt.KeyboardModifier.MetaModifier:
            parts.append("Meta")

        # Numpad keys: Qt's QKeySequence renders Num+, Num0–9, Num*, Num-,
        # Num/, Num., NumEnter all as their bare symbol/digit (so numpad +
        # comes back as "+", identical to Shift+= on the main keyboard).
        # That makes a binding to numpad+ also match main-keyboard +,
        # which is rarely what the user wants. Qt does expose the source
        # via Qt.KeyboardModifier.KeypadModifier on the event – use that
        # to prefix the stored name with "Num" so the hotkey registration
        # path can map it to VK_ADD (0x6B) instead of VK_OEM_PLUS (0xBB).
        is_keypad = bool(modifiers & Qt.KeyboardModifier.KeypadModifier)

        # Get key name
        key_name = QKeySequence(key).toString()
        if is_keypad and key_name:
            # QKeySequence may already have stripped any modifiers from
            # key_name (it usually has, for plain numpad keys); add the
            # Num prefix unconditionally so the parser can disambiguate.
            key_name = "Num" + key_name
        if key_name:
            parts.append(key_name)

        # Create shortcut string. We store the Qt-format string on
        # current_sequence and display the platform-formatted version,
        # so on macOS the user sees ⌘⇧C after pressing Cmd+Shift+C.
        if parts:
            self.setShortcut("+".join(parts))

        event.accept()

    def focusInEvent(self, event):
        """Clear on focus for new input"""
        super().focusInEvent(event)
        self.selectAll()


class ShortcutEditDialog(QDialog):
    """Dialog for editing a keyboard shortcut"""
    
    def __init__(self, shortcut_id: str, data: dict, manager: ShortcutManager, parent=None):
        super().__init__(parent)
        self.shortcut_id = shortcut_id
        self.data = data
        self.manager = manager
        
        self.setWindowTitle(f"Edit Shortcut: {format_shortcuts_in_text(data['description'])}")
        self.setMinimumWidth(500)
        
        layout = QVBoxLayout(self)
        
        # Description
        desc_label = QLabel(f"<b>Action:</b> {format_shortcuts_in_text(data['description'])}")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)
        
        # Category
        cat_label = QLabel(f"<b>Category:</b> {data['category']}")
        layout.addWidget(cat_label)
        
        # Default shortcut
        default_label = QLabel(f"<b>Default:</b> {format_shortcut_for_display(data['default'])}")
        layout.addWidget(default_label)
        
        layout.addSpacing(10)
        
        # Current shortcut input
        input_layout = QHBoxLayout()
        input_label = QLabel("New Shortcut:")
        self.shortcut_input = KeySequenceEdit()
        self.shortcut_input.setShortcut(data['current'])
        input_layout.addWidget(input_label)
        input_layout.addWidget(self.shortcut_input)
        layout.addLayout(input_layout)

        # Reset button
        reset_btn = QPushButton("Reset to Default")
        reset_btn.clicked.connect(self.reset_to_default)
        layout.addWidget(reset_btn)
        
        # Conflict warning label
        self.warning_label = QLabel("")
        self.warning_label.setStyleSheet("color: #f44336; font-weight: bold;")
        self.warning_label.setWordWrap(True)
        layout.addWidget(self.warning_label)
        
        layout.addSpacing(10)
        
        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | 
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept_shortcut)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
        # Check for conflicts when text changes
        self.shortcut_input.textChanged.connect(self.check_conflicts)
    
    def reset_to_default(self):
        """Reset to default shortcut"""
        self.shortcut_input.setShortcut(self.data['default'])

    def check_conflicts(self):
        """Check for conflicting shortcuts"""
        # Use the raw Qt-format value, not the displayed (possibly
        # ⌘-symbolised) text. Conflict detection compares storage strings.
        new_sequence = self.shortcut_input.current_sequence
        if not new_sequence:
            self.warning_label.setText("")
            return

        conflicts = self.manager.find_conflicts(self.shortcut_id, new_sequence)
        if conflicts:
            conflict_names = []
            all_shortcuts = self.manager.get_all_shortcuts()
            for conflict_id in conflicts:
                conflict_names.append(format_shortcuts_in_text(all_shortcuts[conflict_id]['description']))
            
            self.warning_label.setText(
                f"⚠️ Warning: This shortcut conflicts with:\n" + 
                "\n".join(f"  • {name}" for name in conflict_names)
            )
        else:
            self.warning_label.setText("")
    
    def accept_shortcut(self):
        """Accept the new shortcut"""
        new_sequence = self.shortcut_input.current_sequence
        
        # Check conflicts one more time
        conflicts = self.manager.find_conflicts(self.shortcut_id, new_sequence)
        if conflicts:
            reply = QMessageBox.question(
                self,
                "Conflict Detected",
                "This shortcut is already in use. Do you want to override it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.No:
                return
        
        # Set the new shortcut
        if new_sequence == self.data['default']:
            # Same as default, remove custom
            self.manager.reset_shortcut(self.shortcut_id)
        else:
            self.manager.set_shortcut(self.shortcut_id, new_sequence)
        
        self.manager.save_shortcuts()
        self.accept()


class KeyboardShortcutsWidget(QWidget):
    """Main widget for keyboard shortcuts settings"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent  # Store reference to main window
        # Use main window's shortcut manager if available, otherwise create new one
        if hasattr(parent, 'shortcut_manager'):
            self.manager = parent.shortcut_manager
        else:
            self.manager = ShortcutManager()
        self.init_ui()
        self.load_shortcuts()
    
    def init_ui(self):
        """Initialize the user interface.

        Two-column layout so the page is usable on small / laptop screens:

          ┌──────────────────────────────────────────────────────────┐
          │ Header + description                                      │
          ├──────────────────────────────┬───────────────────────────┤
          │ Search                       │ Import / Export           │
          │ ┌──────────────────────────┐ │ Global Hotkeys            │
          │ │                          │ │   • Status                │
          │ │   Shortcuts table        │ │   • AutoHotkey path       │
          │ │   (takes all available   │ │   • Restart note          │
          │ │    vertical space)       │ │                           │
          │ │                          │ │                           │
          │ └──────────────────────────┘ │                           │
          │ [Edit] [Reset] [Reset All]   │                           │
          └──────────────────────────────┴───────────────────────────┘

        The two columns sit on a horizontal QSplitter so users can drag
        the boundary if they want even more room for the table.
        """
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        # Header with a help button on the right that opens the
        # cross-platform shortcut cheatsheet on GitBook.
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header = QLabel("<h2>⌨️ Keyboard Shortcuts</h2>")
        header_row.addWidget(header)
        header_row.addStretch()
        header_row.addWidget(HelpButton(
            HelpTopics.SETTINGS_SHORTCUTS,
            tooltip="Open the keyboard shortcuts help page (incl. macOS vs Windows symbol cheatsheet)",
        ))
        layout.addLayout(header_row)

        # Description
        desc = QLabel(
            "View and customize all keyboard shortcuts. Double-click a shortcut to edit it."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #666;")
        layout.addWidget(desc)

        # Two-column splitter that holds the rest of the page.
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        layout.addWidget(splitter, 1)  # stretch=1 so it absorbs all extra height

        # ─── LEFT COLUMN: search, table, edit/reset buttons ─────────────
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        # Search/Filter
        search_layout = QHBoxLayout()
        search_label = QLabel("Search:")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter by action or shortcut...")
        self.search_input.textChanged.connect(self.filter_shortcuts)
        search_layout.addWidget(search_label)
        search_layout.addWidget(self.search_input)
        left_layout.addLayout(search_layout)

        # Shortcuts table
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Enabled", "Category", "Action", "Shortcut", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)  # Enable column sorting
        self.table.doubleClicked.connect(self.edit_selected_shortcut)

        # Style the table
        self.table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #ddd;
                gridline-color: #f0f0f0;
            }
            QTableWidget::item {
                padding: 5px;
            }
            QTableWidget::item:selected {
                background-color: #2196F3;
                color: white;
            }
        """)

        left_layout.addWidget(self.table, 1)  # stretch=1, table eats vertical space

        # Action buttons (under the table)
        button_layout = QHBoxLayout()

        edit_btn = QPushButton("✏️ Edit Selected")
        edit_btn.clicked.connect(self.edit_selected_shortcut)
        button_layout.addWidget(edit_btn)

        reset_btn = QPushButton("🔄 Reset Selected to Default")
        reset_btn.clicked.connect(self.reset_selected)
        button_layout.addWidget(reset_btn)

        reset_all_btn = QPushButton("🔄 Reset All to Defaults")
        reset_all_btn.clicked.connect(self.reset_all)
        button_layout.addWidget(reset_all_btn)

        button_layout.addStretch()
        left_layout.addLayout(button_layout)

        splitter.addWidget(left_widget)

        # ─── RIGHT COLUMN: Import/Export, Global Hotkeys ────────────────
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        # Export/Import buttons – stack vertically in the narrower column
        # so they don't get clipped, with the green "Export Cheatsheet"
        # button up top since it's the action people actually reach for.
        io_group = QGroupBox("Import/Export")
        io_layout = QVBoxLayout(io_group)
        io_layout.setSpacing(6)

        export_html_btn = QPushButton("📄 Export Cheatsheet (HTML)")
        export_html_btn.clicked.connect(self.export_html_cheatsheet)
        export_html_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        io_layout.addWidget(export_html_btn)

        export_json_btn = QPushButton("📤 Export Shortcuts (JSON)")
        export_json_btn.clicked.connect(self.export_shortcuts)
        io_layout.addWidget(export_json_btn)

        import_json_btn = QPushButton("📥 Import Shortcuts (JSON)")
        import_json_btn.clicked.connect(self.import_shortcuts)
        io_layout.addWidget(import_json_btn)

        cheatsheet_tip = QLabel(
            "💡 Exported HTML cheatsheets can be printed or saved as PDF."
        )
        cheatsheet_tip.setWordWrap(True)
        cheatsheet_tip.setStyleSheet(
            f"color: #666; font-style: italic; font-size: {scaled_pt(9):.1f}pt;")
        io_layout.addWidget(cheatsheet_tip)

        right_layout.addWidget(io_group)

        # Global Hotkeys Settings group (cross-platform)
        # Note: && is used so Qt displays a literal ampersand instead of treating
        # the next character as a mnemonic accelerator.
        hotkey_group = QGroupBox("⌨️ Global Hotkeys (Superlookup, QuickTrans && Clipboard)")
        hotkey_layout = QVBoxLayout()

        if IS_MACOS:
            sl_key = format_shortcut_for_display('Meta+Ctrl+L')   # ⌃⌘L
            qt_key = format_shortcut_for_display('Meta+Ctrl+M')   # ⌃⌘M
            qm_key = format_shortcut_for_display('Meta+Ctrl+K')   # ⌃⌘K
        else:
            sl_key = format_shortcut_for_display('Ctrl+Alt+L')
            qt_key = format_shortcut_for_display('Ctrl+Alt+Q')
            # Was Ctrl+Alt+A labelled "QuickLauncher", which was wrong twice
            # over: QuickLauncher moved to Alt+K and is no longer a global
            # hotkey at all, while Ctrl+Alt+A belonged to Voice Always-On
            # (now Ctrl+Alt+O). Clipboard is named instead - it really is global.
            qm_key = format_shortcut_for_display('Ctrl+Alt+C')
        hotkey_info = QLabel(
            f"Global hotkeys allow {sl_key} (Superlookup), "
            f"{qt_key} (QuickTrans), and "
            f"{qm_key} (Clipboard manager) to work from any application."
        )
        hotkey_info.setWordWrap(True)
        hotkey_info.setStyleSheet(
            f"color: #666; font-size: {scaled_pt(9):.1f}pt; padding: 5px;")
        hotkey_layout.addWidget(hotkey_info)

        # Current status
        hotkey_status_layout = QHBoxLayout()
        hotkey_status_layout.addWidget(QLabel("Status:"))

        hotkey_active = False
        backend_label = "unknown"
        mw = self.main_window
        if mw and hasattr(mw, 'lookup_tab') and hasattr(mw.lookup_tab, 'hotkey_registered'):
            hotkey_active = mw.lookup_tab.hotkey_registered
            if hotkey_active:
                manager = getattr(mw.lookup_tab, '_hotkey_manager', None)
                if manager is not None:
                    raw = getattr(manager, '_backend', None)
                    backend_label = {
                        'winapi': 'WinAPI',
                        'pynput': 'pynput',
                        'nsevent': 'NSEvent',
                    }.get(raw, raw or 'unknown')
                else:
                    # External AHK script path doesn't expose a manager.
                    backend_label = 'AutoHotkey'

        if hotkey_active:
            status_text = f"✅ Active (via {backend_label})"
            status_style = "font-weight: bold; color: green;"
        else:
            status_text = "❌ Not active"
            status_style = "font-weight: bold; color: #c00;"

        hotkey_status_value = QLabel(status_text)
        hotkey_status_value.setStyleSheet(status_style)
        hotkey_status_layout.addWidget(hotkey_status_value)
        hotkey_status_layout.addStretch()
        hotkey_layout.addLayout(hotkey_status_layout)

        # Platform-specific notes
        if IS_MACOS:
            mac_note = QLabel(
                "macOS: Global hotkeys require Accessibility permission on "
                "the binary that launched Python.\n"
                "  • Bundled Supervertaler.app → add Supervertaler\n"
                "  • Launched from Terminal.app → add Terminal.app\n"
                "  • Launched from iTerm2 → add iTerm2.app\n"
                "Open System Settings → Privacy & Security → Accessibility, "
                "tick the relevant app, then restart Supervertaler.\n\n"
                "Also requires the pyobjc-framework-Cocoa Python package "
                "(pip install pyobjc-framework-Cocoa)."
            )
            mac_note.setWordWrap(True)
            mac_note.setStyleSheet(
                f"color: #d97706; font-size: {scaled_pt(9):.1f}pt; padding: 5px;")
            hotkey_layout.addWidget(mac_note)
        elif IS_LINUX:
            linux_note = QLabel(
                "Linux: Global hotkeys may require /dev/input access.\n"
                "If hotkeys don't work, add your user to the 'input' group."
            )
            linux_note.setWordWrap(True)
            linux_note.setStyleSheet(
                f"color: #d97706; font-size: {scaled_pt(9):.1f}pt; padding: 5px;")
            hotkey_layout.addWidget(linux_note)

        # AutoHotkey fallback settings (Windows only)
        if IS_WINDOWS and mw:
            import os
            ahk_path_layout = QHBoxLayout()
            ahk_path_label = QLabel("AutoHotkey Path (fallback):")
            ahk_path_layout.addWidget(ahk_path_label)

            ahk_path_edit = QLineEdit()
            ahk_path_edit.setPlaceholderText("Auto-detect, or specify custom path...")
            # Read saved path from main window's general_settings
            general_settings = getattr(mw, 'general_settings', {}) if mw else {}
            saved_ahk_path = general_settings.get('autohotkey_path', '')
            ahk_path_edit.setText(saved_ahk_path)
            ahk_path_edit.setToolTip(
                "AutoHotkey is used as a fallback if pynput cannot register hotkeys.\n"
                "Leave empty to auto-detect, or specify the full path to AutoHotkey.exe."
            )
            ahk_path_layout.addWidget(ahk_path_edit, stretch=1)

            ahk_browse_btn = QPushButton("📁 Browse...")
            ahk_browse_btn.setMaximumWidth(100)
            if hasattr(mw, '_browse_autohotkey_for_settings'):
                ahk_browse_btn.clicked.connect(
                    lambda: mw._browse_autohotkey_for_settings(ahk_path_edit)
                )
            ahk_path_layout.addWidget(ahk_browse_btn)

            hotkey_layout.addLayout(ahk_path_layout)

            if hasattr(mw, '_find_autohotkey_for_settings'):
                detected_path, source = mw._find_autohotkey_for_settings()
                if detected_path:
                    detected_label = QLabel(f"💡 Detected: {detected_path}")
                    detected_label.setStyleSheet(
                        f"color: #666; font-size: {scaled_pt(9):.1f}pt;")
                    hotkey_layout.addWidget(detected_label)

            # Store reference on main window so save handler can access it
            mw.ahk_path_edit = ahk_path_edit

        restart_note = QLabel("💡 Changes apply immediately – no restart needed.")
        restart_note.setStyleSheet(
            f"color: #666; font-size: {scaled_pt(9):.1f}pt; font-style: italic;")
        hotkey_layout.addWidget(restart_note)

        hotkey_group.setLayout(hotkey_layout)
        right_layout.addWidget(hotkey_group)

        # Push everything in the right column to the top – extra vertical
        # space goes to the splitter handle area, not into stretched groups.
        right_layout.addStretch(1)

        splitter.addWidget(right_widget)

        # Initial split: roughly 70/30 in favour of the table. Users can
        # drag the handle if they want the right column slimmer or wider.
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([700, 300])

    def load_shortcuts(self):
        """Load shortcuts into the table"""
        # CRITICAL: Disable sorting during table modifications to prevent
        # items from becoming disassociated from their rows (causes vanishing text bug)
        self.table.setSortingEnabled(False)
        
        self.table.setRowCount(0)
        
        all_shortcuts = self.manager.get_all_shortcuts()
        shortcuts_by_category = self.manager.get_shortcuts_by_category()
        
        row = 0
        for category in sorted(shortcuts_by_category.keys()):
            shortcuts = shortcuts_by_category[category]
            
            for shortcut_id, data in sorted(shortcuts, key=lambda x: x[1]["description"]):
                self.table.insertRow(row)
                
                # Enabled checkbox (column 0) - using green checkmark style
                checkbox = CheckmarkCheckBox()
                checkbox.setChecked(data.get("is_enabled", True))
                checkbox.setToolTip("Enable or disable this shortcut")
                # Store shortcut_id in checkbox for reference
                checkbox.setProperty("shortcut_id", shortcut_id)
                checkbox.stateChanged.connect(self._on_enabled_changed)
                # Create a widget container to center the checkbox
                checkbox_container = QWidget()
                checkbox_layout = QHBoxLayout(checkbox_container)
                checkbox_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
                checkbox_layout.setContentsMargins(0, 0, 0, 0)
                checkbox_layout.addWidget(checkbox)
                self.table.setCellWidget(row, 0, checkbox_container)
                
                # Category (column 1)
                cat_item = QTableWidgetItem(data["category"])
                cat_item.setData(Qt.ItemDataRole.UserRole, shortcut_id)  # Store ID
                self.table.setItem(row, 1, cat_item)
                
                # Action (column 2). Prefix with 🌍 for shortcuts that also
                # register as OS-level global hotkeys (work from any app).
                action_text = format_shortcuts_in_text(data["description"])
                if data.get("global"):
                    action_text = f"🌍 {action_text}"
                action_item = QTableWidgetItem(action_text)
                if data.get("global"):
                    action_item.setToolTip(
                        "Global – also works when Supervertaler isn't the "
                        "frontmost app."
                    )
                self.table.setItem(row, 2, action_item)
                
                # Shortcut (column 3)
                shortcut_item = QTableWidgetItem(format_shortcut_for_display(data["current"]))
                shortcut_font = QFont()
                shortcut_font.setFamily("Courier New")
                shortcut_font.setBold(True)
                shortcut_item.setFont(shortcut_font)
                # Gray out if disabled
                if not data.get("is_enabled", True):
                    shortcut_item.setForeground(Qt.GlobalColor.gray)
                else:
                    shortcut_item.setForeground(Qt.GlobalColor.blue)
                self.table.setItem(row, 3, shortcut_item)
                
                # Status (column 4)
                status = "Custom" if data["is_custom"] else "Default"
                status_item = QTableWidgetItem(status)
                if data["is_custom"]:
                    status_item.setForeground(Qt.GlobalColor.darkGreen)
                    status_font = QFont()
                    status_font.setBold(True)
                    status_item.setFont(status_font)
                self.table.setItem(row, 4, status_item)
                
                row += 1
        
        # Re-enable sorting after all modifications are complete
        self.table.setSortingEnabled(True)
    
    def _on_enabled_changed(self, state):
        """Handle checkbox state change for enabling/disabling shortcuts"""
        checkbox = self.sender()
        if checkbox:
            shortcut_id = checkbox.property("shortcut_id")
            if shortcut_id:
                is_enabled = state == Qt.CheckState.Checked.value
                if is_enabled:
                    self.manager.enable_shortcut(shortcut_id)
                else:
                    self.manager.disable_shortcut(shortcut_id)
                self.manager.save_shortcuts()
                # Update the shortcut text color to indicate disabled state
                self._update_shortcut_text_color(shortcut_id, is_enabled)
                # Immediately refresh the actual shortcut enabled states in the main window
                if self.main_window and hasattr(self.main_window, 'refresh_shortcut_enabled_states'):
                    self.main_window.refresh_shortcut_enabled_states()
    
    def _update_shortcut_text_color(self, shortcut_id: str, is_enabled: bool):
        """Update the shortcut text color based on enabled state"""
        for row in range(self.table.rowCount()):
            cat_item = self.table.item(row, 1)
            if cat_item and cat_item.data(Qt.ItemDataRole.UserRole) == shortcut_id:
                shortcut_item = self.table.item(row, 3)
                if shortcut_item:
                    if is_enabled:
                        shortcut_item.setForeground(Qt.GlobalColor.blue)
                    else:
                        shortcut_item.setForeground(Qt.GlobalColor.gray)
                break
    
    def _reload_global_hotkeys_on_main_window(self):
        """Tell the main window to re-register all shortcut bindings.

        Called after any shortcut change so it takes effect immediately.
        Reloads two things:
          • OS-level global hotkeys (Superlookup, QuickTrans,
            Clipboard, push-to-talk, Always-On) via ``reload_global_hotkeys``.
          • Local QShortcut key sequences via ``refresh_shortcut_enabled_states``.
            Without the second call, in-app QShortcut objects keep firing on
            the old key until the next application restart.
        """
        mw = self.main_window
        if mw is None:
            return
        if hasattr(mw, 'reload_global_hotkeys'):
            try:
                mw.reload_global_hotkeys()
            except Exception as e:
                print(f"[KeyboardShortcuts] reload_global_hotkeys failed: {e}")
        if hasattr(mw, 'refresh_shortcut_enabled_states'):
            try:
                mw.refresh_shortcut_enabled_states()
            except Exception as e:
                print(f"[KeyboardShortcuts] refresh_shortcut_enabled_states failed: {e}")

    def filter_shortcuts(self):
        """Filter shortcuts based on search text"""
        search_text = self.search_input.text().lower()
        
        for row in range(self.table.rowCount()):
            action = self.table.item(row, 2).text().lower()
            shortcut = self.table.item(row, 3).text().lower()
            category = self.table.item(row, 1).text().lower()
            
            if search_text in action or search_text in shortcut or search_text in category:
                self.table.setRowHidden(row, False)
            else:
                self.table.setRowHidden(row, True)
    
    def edit_selected_shortcut(self):
        """Edit the selected shortcut"""
        current_row = self.table.currentRow()
        if current_row < 0:
            QMessageBox.information(self, "No Selection", "Please select a shortcut to edit.")
            return
        
        # Get shortcut ID from Category column (column 1)
        shortcut_id = self.table.item(current_row, 1).data(Qt.ItemDataRole.UserRole)
        all_shortcuts = self.manager.get_all_shortcuts()
        data = all_shortcuts[shortcut_id]
        
        # Open edit dialog
        dialog = ShortcutEditDialog(shortcut_id, data, self.manager, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.load_shortcuts()  # Reload to show changes
            self._reload_global_hotkeys_on_main_window()
            QMessageBox.information(
                self,
                "Shortcut Updated",
                "The shortcut has been updated and applied immediately."
            )
    
    def reset_selected(self):
        """Reset selected shortcut to default"""
        current_row = self.table.currentRow()
        if current_row < 0:
            QMessageBox.information(self, "No Selection", "Please select a shortcut to reset.")
            return
        
        shortcut_id = self.table.item(current_row, 1).data(Qt.ItemDataRole.UserRole)
        all_shortcuts = self.manager.get_all_shortcuts()
        data = all_shortcuts[shortcut_id]
        
        if not data["is_custom"]:
            QMessageBox.information(self, "Already Default", "This shortcut is already using its default value.")
            return
        
        reply = QMessageBox.question(
            self,
            "Reset Shortcut",
            f"Reset '{format_shortcuts_in_text(data['description'])}' to its default shortcut "
            f"({format_shortcut_for_display(data['default'])})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.manager.reset_shortcut(shortcut_id)
            self.manager.save_shortcuts()
            self.load_shortcuts()
            self._reload_global_hotkeys_on_main_window()
            QMessageBox.information(self, "Reset Complete", "Shortcut has been reset to default.")
    
    def reset_all(self):
        """Reset all shortcuts to defaults"""
        reply = QMessageBox.question(
            self,
            "Reset All Shortcuts",
            "Are you sure you want to reset ALL shortcuts to their default values?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.manager.reset_all_shortcuts()
            self.manager.save_shortcuts()
            self.load_shortcuts()
            self._reload_global_hotkeys_on_main_window()
            QMessageBox.information(self, "Reset Complete", "All shortcuts have been reset to defaults.")
    
    def export_shortcuts(self):
        """Export shortcuts to JSON file"""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Shortcuts",
            "supervertaler_shortcuts.json",
            "JSON Files (*.json)"
        )
        
        if file_path:
            try:
                self.manager.export_shortcuts(Path(file_path))
                QMessageBox.information(
                    self, 
                    "Export Successful", 
                    f"Shortcuts exported to:\n{file_path}"
                )
            except Exception as e:
                QMessageBox.critical(
                    self, 
                    "Export Failed", 
                    f"Failed to export shortcuts:\n{str(e)}"
                )
    
    def import_shortcuts(self):
        """Import shortcuts from JSON file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Shortcuts",
            "",
            "JSON Files (*.json)"
        )
        
        if file_path:
            reply = QMessageBox.question(
                self,
                "Import Shortcuts",
                "This will replace your current custom shortcuts. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                try:
                    if self.manager.import_shortcuts(Path(file_path)):
                        self.manager.save_shortcuts()
                        self.load_shortcuts()
                        self._reload_global_hotkeys_on_main_window()
                        QMessageBox.information(
                            self,
                            "Import Successful",
                            "Shortcuts imported successfully and applied immediately."
                        )
                    else:
                        QMessageBox.critical(
                            self, 
                            "Import Failed", 
                            "Invalid shortcuts file format."
                        )
                except Exception as e:
                    QMessageBox.critical(
                        self, 
                        "Import Failed", 
                        f"Failed to import shortcuts:\n{str(e)}"
                    )
    
    def export_html_cheatsheet(self):
        """Export shortcuts as HTML cheatsheet"""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export HTML Cheatsheet",
            "supervertaler_shortcuts.html",
            "HTML Files (*.html)"
        )
        
        if file_path:
            try:
                self.manager.export_html_cheatsheet(Path(file_path))
                
                reply = QMessageBox.question(
                    self,
                    "Export Successful",
                    f"HTML cheatsheet exported to:\n{file_path}\n\nWould you like to open it in your browser?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                
                if reply == QMessageBox.StandardButton.Yes:
                    import webbrowser
                    webbrowser.open(file_path)
                    
            except Exception as e:
                QMessageBox.critical(
                    self, 
                    "Export Failed", 
                    f"Failed to export cheatsheet:\n{str(e)}"
                )

