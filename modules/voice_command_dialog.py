"""Dialog for adding and editing voice commands.

Extracted from Supervertaler.py so that both the legacy Tools tab and the
Sidekick Voice tab can use it without creating a circular import (modules/
must not import from Supervertaler.py).
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent, QKeySequence
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QTextEdit, QPushButton, QWidget,
)

from modules.voice_commands import VoiceCommand
from modules.shortcut_display import format_shortcut_for_display


class KeystrokeCaptureEdit(QLineEdit):
    """Press-to-capture widget for voice-command keystrokes.

    Internally stores the shortcut in Qt's cross-platform lowercase format
    (e.g. ``ctrl+shift+s``) so the same JSON works on every OS – the
    dispatcher in ``platform_helpers.CrossPlatformKeySender`` swaps
    ``ctrl``↔``cmd`` on macOS to fire the Mac-native shortcut. The
    displayed text is platform-formatted (``⌘⇧S`` on macOS, ``Ctrl+Shift+S``
    elsewhere) via ``format_shortcut_for_display`` so users see exactly
    the keys they pressed regardless of internal storage.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Click here, then press the key combination…")
        self.setReadOnly(False)
        self.current_sequence = ""

    def setShortcut(self, raw_sequence: str):
        """Set the shortcut from a raw lowercase Qt-format string."""
        self.current_sequence = (raw_sequence or "").strip()
        # format_shortcut_for_display wants TitleCase tokens to recognise
        # them; convert "ctrl+shift+s" -> "Ctrl+Shift+S" before formatting.
        if self.current_sequence:
            display_input = "+".join(
                p.capitalize() for p in self.current_sequence.split("+")
            )
            self.setText(format_shortcut_for_display(display_input))
        else:
            self.setText("")

    def keyPressEvent(self, event: QKeyEvent):
        # Ignore lone modifiers
        if event.key() in (Qt.Key.Key_Control, Qt.Key.Key_Shift,
                           Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            return

        modifiers = event.modifiers()
        key = event.key()

        parts = []
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")
        if modifiers & Qt.KeyboardModifier.MetaModifier:
            parts.append("meta")

        key_name = QKeySequence(key).toString()
        if key_name:
            parts.append(key_name.lower())

        if parts:
            self.setShortcut("+".join(parts))
        event.accept()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.selectAll()


# Per-action-type cheat sheets shown below the Action field. Each entry
# is a short HTML snippet describing what to put in the Action field for
# that action type, plus a few canonical examples. Edited as a single
# block to keep the dialog code clean; rendered into a QLabel so the
# user gets links/bold/code styling for free.
_ACTION_CHEAT_SHEETS = {
    "internal": (
        "<b>Internal actions</b> are built into Supervertaler. Use the "
        "<b>Preset</b> dropdown above, or type one of these names directly:"
        "<ul style='margin-top:2px; margin-bottom:0;'>"
        "<li><code>navigate_next</code>, <code>navigate_previous</code>, "
        "<code>navigate_first</code>, <code>navigate_last</code></li>"
        "<li><code>confirm_segment</code>, <code>copy_source_to_target</code>, "
        "<code>clear_target</code></li>"
        "<li><code>translate_segment</code>, <code>batch_translate</code></li>"
        "<li><code>open_superlookup</code>, <code>concordance_search</code></li>"
        "<li><code>show_log</code>, <code>show_editor</code></li>"
        "<li><code>stop_listening</code></li>"
        "</ul>"
    ),
    "keystroke": (
        "<b>Keystroke</b> sends a key combination to whichever app is in the "
        "foreground (Trados, memoQ, Word, browser, etc.). It doesn't need "
        "AutoHotkey.<br><br>"
        "Click the <b>Keystroke</b> field above and press the keys you want "
        "to send – the field captures them and shows the platform-native "
        "symbols (⌘⇧⌥⌃ on macOS, Ctrl+Shift+Alt elsewhere).<br><br>"
        "Shortcuts are stored in a cross-platform format: a command "
        "captured as <code>Ctrl+S</code> on Windows fires <code>⌘S</code> "
        "on macOS automatically, so your voice commands are portable "
        "between machines."
    ),
    "ahk_inline": (
        "<b>AutoHotkey v2 code</b>, run inline against the foreground window. "
        "<i>Windows only.</i><br><br>"
        "<b>Common patterns</b>:"
        "<ul style='margin-top:2px; margin-bottom:0;'>"
        "<li><code>Send \"{Tab}\"</code> &mdash; press Tab</li>"
        "<li><code>Send \"^s\"</code> &mdash; press Ctrl+S</li>"
        "<li><code>SendText \"Hello, world\"</code> &mdash; type literal text "
        "(no key interpretation)</li>"
        "<li><code>Sleep 200</code> &mdash; pause 200&nbsp;ms</li>"
        "<li><code>WinActivate \"ahk_exe Trados.Studio.exe\"</code> &mdash; "
        "bring Trados to front</li>"
        "</ul><br>"
        "Multi-line snippets work too. Full reference at "
        "<a href='https://www.autohotkey.com/docs/v2/'>autohotkey.com/docs/v2</a>."
    ),
    "ahk_script": (
        "<b>Path to an .ahk file</b> (AutoHotkey v2 syntax). "
        "<i>Windows only.</i><br><br>"
        "<b>Examples</b>:"
        "<ul style='margin-top:2px; margin-bottom:0;'>"
        "<li><code>D:\\AHK\\confirm-segment.ahk</code></li>"
        "<li><code>C:\\Users\\you\\Documents\\AHK\\my-macro.ahk</code></li>"
        "</ul><br>"
        "Tip: keep reusable scripts in one folder, reference them by full path. "
        "Use the <b>Scripts folder</b> button under the Voice tab's command "
        "table to jump there."
    ),
}


class VoiceCommandEditDialog(QDialog):
    """Dialog for adding/editing voice commands"""

    CATEGORIES = ["navigation", "editing", "translation", "lookup", "file",
                  "view", "voice", "memoq", "trados", "custom"]
    ACTION_TYPES = [
        ("internal", "Internal Action (Supervertaler)"),
        ("keystroke", "Keystroke (e.g., ctrl+s)"),
        ("ahk_inline", "AutoHotkey Code"),
        ("ahk_script", "AutoHotkey Script File"),
    ]

    def __init__(self, parent=None, command: VoiceCommand = None):
        super().__init__(parent)
        self.command = command
        self.setup_ui()

        if command:
            self.populate_from_command(command)

    def setup_ui(self):
        self.setWindowTitle("Edit Voice Command" if self.command else "Add Voice Command")
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)

        phrase_layout = QHBoxLayout()
        phrase_layout.addWidget(QLabel("Phrase:"))
        self.phrase_edit = QLineEdit()
        self.phrase_edit.setPlaceholderText("e.g., confirm segment")
        phrase_layout.addWidget(self.phrase_edit)
        layout.addLayout(phrase_layout)

        aliases_layout = QHBoxLayout()
        aliases_layout.addWidget(QLabel("Aliases:"))
        self.aliases_edit = QLineEdit()
        self.aliases_edit.setPlaceholderText("e.g., confirm, done, okay (comma-separated)")
        aliases_layout.addWidget(self.aliases_edit)
        layout.addLayout(aliases_layout)

        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Type:"))
        self.type_combo = QComboBox()
        for value, label in self.ACTION_TYPES:
            self.type_combo.addItem(label, value)
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        type_layout.addWidget(self.type_combo)
        layout.addLayout(type_layout)

        action_layout = QVBoxLayout()
        self._action_label = QLabel("Action:")
        action_layout.addWidget(self._action_label)

        # Multi-line text editor – used for "internal" / "ahk_inline" /
        # "ahk_script" action types. For "keystroke" we hide this and
        # show the press-to-capture widget below instead, so Mac users
        # never have to type "ctrl+a" for what is actually ⌘A.
        self.action_edit = QTextEdit()
        self.action_edit.setMaximumHeight(100)
        self.action_edit.setPlaceholderText(
            "For internal: action_name\nFor AHK: Send, ^s")
        action_layout.addWidget(self.action_edit)

        # Press-to-capture widget for keystroke commands. Stored value is
        # cross-platform Qt format (lowercase: "ctrl+shift+s"); displayed
        # value is platform-native (⌘⇧S on macOS, Ctrl+Shift+S elsewhere).
        # The dispatcher in CrossPlatformKeySender handles the Mac
        # ctrl↔cmd swap so a single stored shortcut works everywhere.
        self.keystroke_edit = KeystrokeCaptureEdit()
        action_layout.addWidget(self.keystroke_edit)

        # Context-sensitive cheat sheet that updates with the Type
        # dropdown – tells the user what to put in the Action field for
        # each type (modifier syntax, special-key names, AHK examples,
        # available internal actions, etc.). The content lives in
        # _ACTION_CHEAT_SHEETS at the top of this module.
        self._cheat_sheet = QLabel()
        self._cheat_sheet.setTextFormat(Qt.TextFormat.RichText)
        self._cheat_sheet.setWordWrap(True)
        self._cheat_sheet.setOpenExternalLinks(True)
        self._cheat_sheet.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction)
        self._cheat_sheet.setStyleSheet(
            "font-size: 8pt; color: #444; background-color: #FAFAFA;"
            " border: 1px solid #DDD; border-radius: 4px; padding: 8px;"
        )
        action_layout.addWidget(self._cheat_sheet)
        layout.addLayout(action_layout)

        # The "Preset" row is wrapped in a container QWidget rather than
        # added as a bare QHBoxLayout. When the user picks a non-internal
        # action type, ``setVisible(False)`` on the container collapses
        # the entire row including its margins/spacing. Hiding individual
        # widgets in a layout would leave the layout's own spacing in
        # place, producing a phantom gap between Action and Description.
        self._internal_actions_widget = QWidget()
        ia_layout = QHBoxLayout(self._internal_actions_widget)
        ia_layout.setContentsMargins(0, 0, 0, 0)
        ia_layout.addWidget(QLabel("Preset:"))
        self.internal_combo = QComboBox()
        self.internal_combo.addItems([
            "navigate_next", "navigate_previous", "navigate_first", "navigate_last",
            "confirm_segment", "copy_source_to_target", "clear_target",
            "translate_segment", "batch_translate",
            "open_superlookup", "concordance_search",
            "show_log", "show_editor",
            "stop_listening"
        ])
        self.internal_combo.currentTextChanged.connect(lambda t: self.action_edit.setPlainText(t))
        ia_layout.addWidget(self.internal_combo)
        ia_layout.addStretch()
        layout.addWidget(self._internal_actions_widget)

        desc_layout = QHBoxLayout()
        desc_layout.addWidget(QLabel("Description:"))
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("e.g., Confirm current segment")
        desc_layout.addWidget(self.desc_edit)
        layout.addLayout(desc_layout)

        cat_layout = QHBoxLayout()
        cat_layout.addWidget(QLabel("Category:"))
        self.cat_combo = QComboBox()
        self.cat_combo.addItems(self.CATEGORIES)
        self.cat_combo.setEditable(True)
        cat_layout.addWidget(self.cat_combo)
        layout.addLayout(cat_layout)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.accept)
        save_btn.setDefault(True)
        btn_layout.addWidget(save_btn)

        layout.addLayout(btn_layout)

        self._on_type_changed()

    def _on_type_changed(self):
        """Show/hide the Preset row, the right Action editor, + refresh the cheat sheet."""
        action_type = self.type_combo.currentData()
        is_internal = action_type == "internal"
        is_keystroke = action_type == "keystroke"
        # Toggling the wrapper widget collapses the whole row including
        # its margins, so non-internal action types don't leave a
        # phantom gap between Action and Description.
        try:
            self._internal_actions_widget.setVisible(is_internal)
        except Exception:
            pass
        # Swap between the multi-line text editor (internal / ahk_*) and
        # the press-to-capture widget (keystroke). Update the section
        # label so the field meaning is unambiguous.
        try:
            self.action_edit.setVisible(not is_keystroke)
            self.keystroke_edit.setVisible(is_keystroke)
            self._action_label.setText("Keystroke:" if is_keystroke else "Action:")
        except Exception:
            pass
        # Update the contextual cheat sheet to match the new type.
        try:
            self._cheat_sheet.setText(_ACTION_CHEAT_SHEETS.get(action_type, ""))
        except Exception:
            pass

    def populate_from_command(self, cmd: VoiceCommand):
        self.phrase_edit.setText(cmd.phrase)
        self.aliases_edit.setText(", ".join(cmd.aliases))

        for i in range(self.type_combo.count()):
            if self.type_combo.itemData(i) == cmd.action_type:
                self.type_combo.setCurrentIndex(i)
                break

        if cmd.action_type == "keystroke":
            self.keystroke_edit.setShortcut(cmd.action)
            self.action_edit.setPlainText("")
        else:
            self.action_edit.setPlainText(cmd.action)
            self.keystroke_edit.setShortcut("")
        self.desc_edit.setText(cmd.description)

        idx = self.cat_combo.findText(cmd.category)
        if idx >= 0:
            self.cat_combo.setCurrentIndex(idx)
        else:
            self.cat_combo.setCurrentText(cmd.category)

    def get_command(self) -> VoiceCommand:
        aliases_text = self.aliases_edit.text().strip()
        aliases = [a.strip() for a in aliases_text.split(",") if a.strip()] if aliases_text else []

        action_type = self.type_combo.currentData()
        if action_type == "keystroke":
            action_value = self.keystroke_edit.current_sequence.strip()
        else:
            action_value = self.action_edit.toPlainText().strip()

        return VoiceCommand(
            phrase=self.phrase_edit.text().strip(),
            aliases=aliases,
            action_type=action_type,
            action=action_value,
            description=self.desc_edit.text().strip(),
            category=self.cat_combo.currentText().strip(),
            enabled=True,
        )
