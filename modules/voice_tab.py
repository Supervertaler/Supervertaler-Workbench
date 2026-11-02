"""Voice tab – voice commands.

Mounted as Workbench's "🎤 Voice" top tab. The widget is parent-agnostic
– it just needs the following contract on the parent_app object:

    - voice_command_manager: VoiceCommandManager instance
    - voice_listener: ContinuousVoiceListener or None
    - load_dictation_settings() -> dict (the Voice settings; the name of
      their settings key is historical)
    - _load_unified_settings() / _save_unified_settings(settings)
    - _toggle_alwayson_listening() -> None
    - _reset_voice_commands() -> None
    - _check_ahk_installed() -> str
    - _open_voice_scripts_folder() -> None
    - shortcut_manager and open_settings_to_keyboard_shortcuts() (optional)

Commands only, as with SuperVoice in Supervertaler for Trados: one on/off
switch, speech that isn't a command is never typed, and "What I heard"
shows what each utterance did. Running text belongs to a dictation tool.

Historically named "AutoFingers"; the internal name was simplified to
"Voice" in v1.9.491. The persisted layout key reads legacy
``autofingers_layout`` settings as a one-time fallback so existing users
keep their splitter / column widths.
"""
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QGroupBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QScrollArea, QFrame, QMessageBox, QSplitter, QMenu,
    QListWidget, QListWidgetItem,
)

from modules.styled_widgets import HelpButton
from modules.voice_command_dialog import VoiceCommandEditDialog
from modules.help_system import Topics as HelpTopics, set_topic as set_help_topic


_TYPE_LABELS = {
    "internal": "Command",
    "keystroke": "Keystroke",
    "ahk_script": "AHK Script",
    "ahk_inline": "AHK Inline",
}

_DISABLED_COLOUR = QColor(170, 170, 170)
_DONE_COLOUR = QColor(46, 125, 50)
_FAILED_COLOUR = QColor(198, 40, 40)

# Column indices
_COL_ENABLED  = 0
_COL_PHRASE   = 1
_COL_ALIASES  = 2
_COL_TYPE     = 3
_COL_ACTION   = 4
_COL_CATEGORY = 5


class VoiceTab(QWidget):
    """Voice commands: listening switch, "What I heard", command table."""

    # "What I heard" keeps this many utterances, in memory only.
    HEARD_LIMIT = 50

    def __init__(self, parent_app, parent=None):
        super().__init__(parent)
        self._parent_app = parent_app
        self._build_ui()
        self._restore_layout()
        # Connect persistence signals after restore so restore doesn't trigger saves
        self._splitter.splitterMoved.connect(lambda pos, idx: self._save_layout())
        self._table.horizontalHeader().sectionResized.connect(
            lambda idx, old, new: self._save_layout())
        # Initial population
        self._populate_table()
        self._sync_alwayson_from_listener()
        set_help_topic(self, HelpTopics.VOICE)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        settings = self._load_settings()

        # --- Header (full width) ---------------------------------------
        # Wrap header text + a "?" help button in one row so the button
        # sits at the top-right of the tab, matching the Trados plugin's
        # context-sensitive help convention.
        header_widget = QWidget()
        header_widget.setStyleSheet(
            "background-color: #E3F2FD;"
        )
        header_row = QHBoxLayout(header_widget)
        header_row.setContentsMargins(8, 4, 8, 4)
        header = QLabel(
            "🎤 <b>Voice</b> – voice commands.<br>"
            "Switch listening on, then say a command. Speech that isn't a "
            "command is ignored; for running text, use a dictation tool."
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setWordWrap(True)
        header.setStyleSheet("font-size: 9pt; color: #444;")
        header_row.addWidget(header, stretch=1)
        header_row.addWidget(
            HelpButton(HelpTopics.VOICE,
                       tooltip="Open Voice help"),
            alignment=Qt.AlignmentFlag.AlignTop,
        )
        outer.addWidget(header_widget)

        # --- Main horizontal splitter ----------------------------------
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(4)
        self._splitter.setChildrenCollapsible(False)
        outer.addWidget(self._splitter, 1)

        # ---- Left panel: listening + what was heard (scrollable) ------
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_body = QWidget()
        left_layout = QVBoxLayout(left_body)
        left_layout.setContentsMargins(10, 10, 6, 10)
        left_layout.setSpacing(10)
        left_scroll.setWidget(left_body)
        self._splitter.addWidget(left_scroll)

        # --- Microphone ---
        # Stored as the device *name* so it survives across sessions
        # (device indices shuffle when USB devices are added / removed).
        # Lookup happens at record time via mic_devices.resolve_device_index
        # – if the saved device is gone, sounddevice silently falls back to
        # the OS default. The device list itself is filled in on first
        # show – see _populate_mic_combo for why.
        from modules.mic_devices import DEFAULT_SENTINEL
        mic_row = QHBoxLayout()
        mic_row.addWidget(QLabel("Microphone:"))
        self._mic_combo = QComboBox()
        self._mic_combo.addItem(
            "System default (currently selected in Windows)",
            DEFAULT_SENTINEL,
        )
        self._mic_devices_listed = False
        self._mic_combo.currentIndexChanged.connect(self._on_mic_changed)
        mic_row.addWidget(self._mic_combo, stretch=1)
        # Wrap in a tiny widget so we can give it left/right margins
        # consistent with the group boxes below it.
        mic_widget = QWidget()
        mic_widget.setLayout(mic_row)
        left_layout.addWidget(mic_widget)

        # --- Voice commands: the one on/off switch ---
        commands_group = QGroupBox("🎤 Voice commands")
        commands_layout = QVBoxLayout()

        status_row = QHBoxLayout()
        self._status_label = QLabel("⚪ Not active")
        self._status_label.setStyleSheet("font-size: 9pt; padding: 4px;")
        status_row.addWidget(self._status_label)
        status_row.addStretch()
        self._toggle_btn = QPushButton("▶️ Start Always-On")
        self._toggle_btn.setStyleSheet("padding: 6px 12px;")
        self._toggle_btn.clicked.connect(self._toggle_alwayson)
        status_row.addWidget(self._toggle_btn)
        commands_layout.addLayout(status_row)

        # Read-only display of the on/off hotkey; rebinding happens in
        # Settings → Keyboard Shortcuts. Refreshed on every showEvent.
        hotkey_row = QHBoxLayout()
        hotkey_row.addWidget(QLabel("On/off hotkey:"))
        self._hotkey_label = QLabel("Ctrl+Alt+O")
        self._hotkey_label.setTextFormat(Qt.TextFormat.RichText)
        self._hotkey_label.setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace; "
            "font-size: 9pt; padding: 2px 6px; "
            "background-color: #F5F5F5; border: 1px solid #DDD; border-radius: 3px;"
        )
        hotkey_row.addWidget(self._hotkey_label)
        hotkey_row.addStretch()
        hotkey_hint = QLabel(
            "<a href='change-hotkey' style='color: #1976D2;'>"
            "Change in Settings → Keyboard Shortcuts</a>"
        )
        hotkey_hint.setTextFormat(Qt.TextFormat.RichText)
        hotkey_hint.setStyleSheet("font-size: 8pt;")
        hotkey_hint.linkActivated.connect(
            lambda _: self._open_keyboard_shortcuts_settings())
        hotkey_row.addWidget(hotkey_hint)
        commands_layout.addLayout(hotkey_row)

        sens_row = QHBoxLayout()
        sens_row.addWidget(QLabel("Mic sensitivity:"))
        self._sensitivity_combo = QComboBox()
        self._sensitivity_combo.addItems([
            "Low (noisy)", "Medium (normal)", "High (quiet)",
        ])
        saved_sensitivity = settings.get('alwayson_sensitivity', 'medium')
        self._sensitivity_combo.setCurrentIndex(
            {'low': 0, 'medium': 1, 'high': 2}.get(saved_sensitivity, 1))
        self._sensitivity_combo.currentIndexChanged.connect(self._on_sensitivity_changed)
        sens_row.addWidget(self._sensitivity_combo, stretch=1)
        commands_layout.addLayout(sens_row)

        commands_info = QLabel(
            "ℹ️ Commands are spoken in <b>English</b>. Speech that isn't a "
            "command is ignored – it is never typed. For running text, use "
            "a dictation tool such as Windows voice typing (Win+H), Wispr "
            "Flow or Dragon; voice commands keep listening alongside it. "
            "Commands go to whichever program is in front when you speak."
        )
        commands_info.setTextFormat(Qt.TextFormat.RichText)
        commands_info.setWordWrap(True)
        commands_info.setStyleSheet(
            "font-size: 8pt; color: #555; background-color: #FFF8E1;"
            " border: 1px solid #FFE082; border-radius: 4px; padding: 6px;"
        )
        commands_layout.addWidget(commands_info)
        commands_group.setLayout(commands_layout)
        left_layout.addWidget(commands_group)

        # --- What I heard ---
        heard_group = QGroupBox("👂 What I heard")
        heard_layout = QVBoxLayout()
        self._heard_list = QListWidget()
        self._heard_list.setMinimumHeight(160)
        self._heard_list.setStyleSheet("font-size: 9pt;")
        heard_layout.addWidget(self._heard_list, 1)
        heard_hint = QLabel(
            f"The last {self.HEARD_LIMIT} things Voice heard while "
            "listening, newest first, and what happened. Cleared when "
            "Workbench closes."
        )
        heard_hint.setWordWrap(True)
        heard_hint.setStyleSheet("font-size: 8pt; color: #666;")
        heard_layout.addWidget(heard_hint)
        heard_group.setLayout(heard_layout)
        left_layout.addWidget(heard_group, 1)

        # ---- Right panel: voice commands -----------------------------
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(6, 10, 10, 10)
        right_layout.setSpacing(6)
        self._splitter.addWidget(right_widget)

        cmd_label = QLabel("🗣️ <b>Voice Commands</b>")
        cmd_label.setTextFormat(Qt.TextFormat.RichText)
        cmd_label.setStyleSheet("font-size: 10pt; padding: 2px 0;")
        right_layout.addWidget(cmd_label)

        cmd_info = QLabel(
            "Say a phrase to run its action. Double-click a row to edit. "
            "Keep phrases distinctive – two or three words, not an everyday "
            "word like \"next\": Voice keeps listening while you dictate "
            "with another tool, so a common word could fire mid-sentence."
        )
        cmd_info.setStyleSheet("font-size: 8pt; color: #666;")
        cmd_info.setWordWrap(True)
        right_layout.addWidget(cmd_info)

        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["", "Phrase", "Aliases", "Type", "Action", "Category"]
        )
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(_COL_ENABLED,  QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(_COL_ENABLED, 28)
        for col in range(_COL_PHRASE, _COL_CATEGORY + 1):
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(_COL_PHRASE,   160)
        self._table.setColumnWidth(_COL_ALIASES,  200)
        self._table.setColumnWidth(_COL_TYPE,      80)
        self._table.setColumnWidth(_COL_ACTION,   220)
        self._table.setColumnWidth(_COL_CATEGORY,  80)
        hh.setStretchLastSection(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.cellDoubleClicked.connect(self._on_row_double_clicked)
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        right_layout.addWidget(self._table, 1)

        cmd_btn_row = QHBoxLayout()
        add_btn = QPushButton("➕ Add")
        add_btn.clicked.connect(self._add_command)
        cmd_btn_row.addWidget(add_btn)
        edit_btn = QPushButton("✏️ Edit")
        edit_btn.clicked.connect(self._edit_command)
        cmd_btn_row.addWidget(edit_btn)
        remove_btn = QPushButton("🗑️ Remove")
        remove_btn.clicked.connect(self._remove_command)
        cmd_btn_row.addWidget(remove_btn)
        cmd_btn_row.addStretch()
        reset_btn = QPushButton("🔄 Reset")
        reset_btn.clicked.connect(self._reset_commands)
        cmd_btn_row.addWidget(reset_btn)
        right_layout.addLayout(cmd_btn_row)

        # AutoHotkey only matters for the AHK Inline / AHK Script types;
        # Keystroke commands don't need it.
        ahk_row = QHBoxLayout()
        ahk_caption = QLabel("AutoHotkey (for AHK commands):")
        ahk_caption.setStyleSheet("font-size: 8pt; color: #666;")
        ahk_row.addWidget(ahk_caption)
        self._ahk_status_label = QLabel(self._ahk_status_text())
        self._ahk_status_label.setStyleSheet("font-size: 8pt;")
        ahk_row.addWidget(self._ahk_status_label)
        ahk_row.addStretch()
        scripts_btn = QPushButton("📂 Scripts folder")
        scripts_btn.clicked.connect(self._open_ahk_folder)
        ahk_row.addWidget(scripts_btn)
        right_layout.addLayout(ahk_row)

        self._splitter.setStretchFactor(0, 2)
        self._splitter.setStretchFactor(1, 3)

    # ------------------------------------------------------------------
    # Layout persistence
    # ------------------------------------------------------------------

    def _save_layout(self):
        self._set_voice_settings(voice_layout={
            'splitter': self._splitter.sizes(),
            'columns': [
                self._table.columnWidth(col)
                for col in range(_COL_PHRASE, _COL_CATEGORY + 1)
            ],
        })

    def _restore_layout(self):
        settings = self._load_settings()
        # v1.9.491: settings key was renamed from autofingers_layout → voice_layout.
        # Fall back to the legacy key for existing users so they keep their layout.
        layout = settings.get('voice_layout') or settings.get('autofingers_layout') or {}
        sizes = layout.get('splitter')
        if sizes and len(sizes) == 2:
            self._splitter.setSizes(sizes)
        widths = layout.get('columns')
        if widths and len(widths) == (_COL_CATEGORY - _COL_PHRASE + 1):
            for i, w in enumerate(widths):
                self._table.setColumnWidth(_COL_PHRASE + i, w)

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _load_settings(self) -> dict:
        loader = getattr(self._parent_app, 'load_dictation_settings', None)
        if callable(loader):
            try:
                return loader() or {}
            except Exception:
                return {}
        return {}

    def _ahk_status_text(self) -> str:
        check = getattr(self._parent_app, '_check_ahk_installed', None)
        if callable(check):
            try:
                return check()
            except Exception:
                return ""
        return ""

    def refresh(self):
        """Re-read voice commands and Always-On state from the parent app."""
        self._populate_table()
        self._sync_alwayson_from_listener()
        self._ahk_status_label.setText(self._ahk_status_text())
        self._refresh_hotkey_label()

    def showEvent(self, event):
        super().showEvent(event)
        self._populate_mic_combo()
        self.refresh()

    def _populate_mic_combo(self):
        """Fill the microphone dropdown, once, when the tab is first shown.

        Listing devices means initialising PortAudio, which opens every
        audio device on the system to read its capabilities. Workbench
        builds this tab hidden a few seconds after launch (the top-tab
        warm-up), and doing the listing then stopped other apps' music
        playback at every start-up (issue #255). Now it only happens once
        the user actually opens the Voice tab.
        """
        if self._mic_devices_listed:
            return
        self._mic_devices_listed = True
        from modules.mic_devices import list_input_devices, DEFAULT_SENTINEL
        saved_mic = self._load_settings().get('mic_device', DEFAULT_SENTINEL)
        # Signals off: filling the list and restoring the saved choice is
        # not a user change, and must never overwrite the saved mic.
        self._mic_combo.blockSignals(True)
        try:
            for name in list_input_devices():
                self._mic_combo.addItem(name, name)
            # Restore saved selection if the device is still attached;
            # otherwise leave the combo on "System default" (index 0).
            i = self._mic_combo.findData(saved_mic)
            if i >= 0:
                self._mic_combo.setCurrentIndex(i)
        finally:
            self._mic_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # Voice commands CRUD
    # ------------------------------------------------------------------

    def _voice_command_manager(self):
        return getattr(self._parent_app, 'voice_command_manager', None)

    def _populate_table(self):
        mgr = self._voice_command_manager()
        was_sorting = self._table.isSortingEnabled()
        self._table.setSortingEnabled(False)
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        if mgr is None:
            self._table.blockSignals(False)
            self._table.setSortingEnabled(was_sorting)
            return
        for cmd in mgr.commands:
            row = self._table.rowCount()
            self._table.insertRow(row)

            chk = QTableWidgetItem()
            chk.setFlags(
                Qt.ItemFlag.ItemIsEnabled |
                Qt.ItemFlag.ItemIsUserCheckable |
                Qt.ItemFlag.ItemIsSelectable
            )
            chk.setCheckState(
                Qt.CheckState.Checked if cmd.enabled else Qt.CheckState.Unchecked)
            self._table.setItem(row, _COL_ENABLED, chk)

            self._table.setItem(row, _COL_PHRASE, QTableWidgetItem(cmd.phrase))
            self._table.setItem(row, _COL_ALIASES, QTableWidgetItem(
                ", ".join(cmd.aliases) if cmd.aliases else ""))
            self._table.setItem(row, _COL_TYPE, QTableWidgetItem(
                _TYPE_LABELS.get(cmd.action_type, cmd.action_type)))
            self._table.setItem(row, _COL_ACTION, QTableWidgetItem(
                cmd.description or cmd.action))
            self._table.setItem(row, _COL_CATEGORY, QTableWidgetItem(cmd.category))

            if not cmd.enabled:
                self._set_row_colour(row, _DISABLED_COLOUR)

        self._table.blockSignals(False)
        self._table.setSortingEnabled(was_sorting)

    def _set_row_colour(self, row: int, colour):
        for col in range(_COL_PHRASE, _COL_CATEGORY + 1):
            item = self._table.item(row, col)
            if item is None:
                continue
            if colour is None:
                item.setData(Qt.ItemDataRole.ForegroundRole, None)
            else:
                item.setForeground(colour)

    def _on_item_changed(self, item):
        if item.column() != _COL_ENABLED:
            return
        mgr = self._voice_command_manager()
        if mgr is None:
            return
        row = item.row()
        phrase_item = self._table.item(row, _COL_PHRASE)
        if phrase_item is None:
            return
        phrase = phrase_item.text()
        cmd = next((c for c in mgr.commands if c.phrase == phrase), None)
        if cmd is None:
            return
        enabled = item.checkState() == Qt.CheckState.Checked
        cmd.enabled = enabled
        mgr.save_commands()
        self._table.blockSignals(True)
        self._set_row_colour(row, None if enabled else _DISABLED_COLOUR)
        self._table.blockSignals(False)

    def _on_header_clicked(self, col: int):
        if col != _COL_ENABLED:
            return
        mgr = self._voice_command_manager()
        if mgr is None:
            return
        # Enable all if any are disabled; disable all if all are enabled.
        target = any(not cmd.enabled for cmd in mgr.commands)
        self._set_rows_enabled(list(range(self._table.rowCount())), target)

    def _on_context_menu(self, pos):
        rows = self._selected_rows()
        if not rows:
            return
        menu = QMenu(self)
        act_enable  = menu.addAction("✅ Activate")
        act_disable = menu.addAction("⬜ Deactivate")
        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        if action == act_enable:
            self._set_rows_enabled(rows, True)
        elif action == act_disable:
            self._set_rows_enabled(rows, False)

    def _selected_rows(self):
        return sorted({item.row() for item in self._table.selectedItems()})

    def _set_rows_enabled(self, rows: list, enabled: bool):
        mgr = self._voice_command_manager()
        if mgr is None:
            return
        changed = False
        self._table.blockSignals(True)
        for row in rows:
            phrase_item = self._table.item(row, _COL_PHRASE)
            if phrase_item is None:
                continue
            cmd = next(
                (c for c in mgr.commands if c.phrase == phrase_item.text()), None)
            if cmd is None:
                continue
            cmd.enabled = enabled
            chk = self._table.item(row, _COL_ENABLED)
            if chk:
                chk.setCheckState(
                    Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
            self._set_row_colour(row, None if enabled else _DISABLED_COLOUR)
            changed = True
        self._table.blockSignals(False)
        if changed:
            mgr.save_commands()

    def _on_row_double_clicked(self, row: int, col: int):
        self._edit_command_at_row(row)

    def _add_command(self):
        mgr = self._voice_command_manager()
        if mgr is None:
            return
        dialog = VoiceCommandEditDialog(self)
        if dialog.exec():
            mgr.add_command(dialog.get_command())
            self._broadcast_table_refresh()

    def _edit_command(self):
        selected = self._table.selectedItems()
        if not selected:
            QMessageBox.information(
                self, "Edit Command", "Please select a command to edit.")
            return
        self._edit_command_at_row(selected[0].row())

    def _edit_command_at_row(self, row: int):
        mgr = self._voice_command_manager()
        if mgr is None:
            return
        phrase_item = self._table.item(row, _COL_PHRASE)
        if phrase_item is None:
            return
        phrase = phrase_item.text()
        cmd = next((c for c in mgr.commands if c.phrase == phrase), None)
        if cmd is None:
            return
        dialog = VoiceCommandEditDialog(self, cmd)
        if dialog.exec():
            mgr.remove_command(phrase)
            mgr.add_command(dialog.get_command())
            self._broadcast_table_refresh()

    def _remove_command(self):
        mgr = self._voice_command_manager()
        if mgr is None:
            return
        selected = self._table.selectedItems()
        if not selected:
            QMessageBox.information(
                self, "Remove Command", "Please select a command to remove.")
            return
        row = selected[0].row()
        phrase_item = self._table.item(row, _COL_PHRASE)
        if phrase_item is None:
            return
        phrase = phrase_item.text()
        confirm = QMessageBox.question(
            self, "Remove Command",
            f"Remove voice command '{phrase}'?",
        )
        if confirm == QMessageBox.StandardButton.Yes:
            mgr.remove_command(phrase)
            self._broadcast_table_refresh()

    def _reset_commands(self):
        reset_fn = getattr(self._parent_app, '_reset_voice_commands', None)
        if callable(reset_fn):
            reset_fn()
        self._broadcast_table_refresh()

    def _broadcast_table_refresh(self):
        """Refresh this tab via the parent app's central entry point so any
        future surfaces tied into _populate_voice_commands_table also update."""
        broadcast = getattr(
            self._parent_app, '_populate_voice_commands_table', None)
        if callable(broadcast):
            try:
                broadcast()
                return
            except Exception:
                pass
        # Fallback: refresh just our own table.
        self._populate_table()

    # ------------------------------------------------------------------
    # Always-On
    # ------------------------------------------------------------------

    def _toggle_alwayson(self):
        toggle = getattr(self._parent_app, '_toggle_alwayson_listening', None)
        if callable(toggle):
            toggle()

    def _sync_alwayson_from_listener(self):
        """Read live state from parent_app.voice_listener and reflect it."""
        listener = getattr(self._parent_app, 'voice_listener', None)
        if listener is not None and getattr(listener, 'is_listening', False):
            self.set_alwayson_status("listening")
        else:
            self.set_alwayson_status("stopped")

    def set_alwayson_status(self, status: str):
        """Update the Always-On UI for an externally-driven status change.

        Called from Supervertaler._update_alwayson_ui so this tab stays in
        sync with the grid toolbar button and the legacy settings panel.
        """
        if status in ("listening", "waiting"):
            self._status_label.setText("🟢 Listening for speech…")
            self._status_label.setStyleSheet(
                "font-size: 9pt; padding: 4px; color: #2E7D32;")
            self._toggle_btn.setText("⏹️ Stop Always-On")
            self._toggle_btn.setStyleSheet(
                "padding: 6px 12px; background-color: #FFCDD2;")
        elif status == "recording":
            self._status_label.setText("🔴 Recording…")
            self._status_label.setStyleSheet(
                "font-size: 9pt; padding: 4px; color: #C62828;")
        elif status == "processing":
            self._status_label.setText("⏳ Processing…")
            self._status_label.setStyleSheet(
                "font-size: 9pt; padding: 4px; color: #F57C00;")
        elif status == "loading":
            # Running, but Vosk is still loading (or downloading its
            # model, the first time). The button stops it, so it must
            # not offer "Start".
            self._status_label.setText("⏳ Starting…")
            self._status_label.setStyleSheet(
                "font-size: 9pt; padding: 4px; color: #F57C00;")
            self._toggle_btn.setText("⏹️ Stop Always-On")
            self._toggle_btn.setStyleSheet(
                "padding: 6px 12px; background-color: #FFCDD2;")
        else:
            self._status_label.setText("⚪ Not active")
            self._status_label.setStyleSheet(
                "font-size: 9pt; padding: 4px;")
            self._toggle_btn.setText("▶️ Start Always-On")
            self._toggle_btn.setStyleSheet("padding: 6px 12px;")

    # ------------------------------------------------------------------
    # Settings handlers
    # ------------------------------------------------------------------

    def _set_voice_settings(self, **keys):
        """Update keys under prefs['ui']['dictation_settings'] – the Voice
        settings, under their historical key name – in the unified
        settings JSON, preserving any unrelated keys."""
        load = getattr(self._parent_app, '_load_unified_settings', None)
        save = getattr(self._parent_app, '_save_unified_settings', None)
        if not callable(load) or not callable(save):
            return False
        try:
            all_settings = load()
            prefs = all_settings.setdefault('ui', {})
            ds = prefs.setdefault('dictation_settings', {})
            ds.update(keys)
            save(all_settings)
            return True
        except Exception:
            return False

    def _on_mic_changed(self, idx: int):
        """Persist the user's microphone choice.

        Stored as the device name (or the DEFAULT_SENTINEL string) so the
        next session re-binds to the same physical mic even if its index
        has shifted due to other USB devices being attached. The listener
        resolves name → index at record time via mic_devices.resolve_device_index.
        """
        try:
            saved = self._mic_combo.itemData(idx)
            self._set_voice_settings(mic_device=saved)
        except Exception:
            pass

    def _on_sensitivity_changed(self, idx: int):
        sensitivity = ['low', 'medium', 'high'][idx]
        self._set_voice_settings(alwayson_sensitivity=sensitivity)
        # Live-apply if a listener is currently running.
        listener = getattr(self._parent_app, 'voice_listener', None)
        if listener is not None and hasattr(listener, 'set_sensitivity'):
            try:
                listener.set_sensitivity(sensitivity)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # What I heard
    # ------------------------------------------------------------------

    def add_heard(self, heard: str, outcome: str, ok):
        """Add one utterance to "What I heard", newest first.

        ``ok`` is True when a command ran, False when it failed and None
        when no command matched. Only HEARD_LIMIT entries are kept.
        """
        item = QListWidgetItem(f"{datetime.now():%H:%M:%S}   “{heard}” → {outcome}")
        colour = {True: _DONE_COLOUR, False: _FAILED_COLOUR}.get(ok, _DISABLED_COLOUR)
        item.setForeground(colour)
        self._heard_list.insertItem(0, item)
        while self._heard_list.count() > self.HEARD_LIMIT:
            self._heard_list.takeItem(self._heard_list.count() - 1)

    def _refresh_hotkey_label(self):
        """Show the current on/off hotkey from shortcut_manager, so a
        rebinding in Settings → Keyboard Shortcuts shows up here."""
        sm = getattr(self._parent_app, 'shortcut_manager', None)
        if sm is None:
            return
        try:
            from modules.shortcut_display import format_shortcut_for_display
            shortcut = sm.get_shortcut('voice_alwayson_toggle') or 'Ctrl+Alt+O'
            self._hotkey_label.setText(format_shortcut_for_display(shortcut))
        except Exception:
            pass

    def _open_keyboard_shortcuts_settings(self):
        """Open Settings → Keyboard Shortcuts to rebind the on/off hotkey.

        Falls back to a help message if the main app doesn't expose the
        entry point.
        """
        mw = self._parent_app
        opener = getattr(mw, 'open_settings_to_keyboard_shortcuts', None)
        if callable(opener):
            try:
                opener()
                return
            except Exception:
                pass
        # Fallback: tell the user where to look manually.
        try:
            QMessageBox.information(
                self,
                "Rebind the Voice hotkey",
                "Open the main Workbench window → <b>Settings</b> → "
                "<b>Keyboard Shortcuts</b>. Find "
                "<b>Voice Always-On (toggle)</b> in the "
                "<b>Special</b> category, click the shortcut field, "
                "and press the key combo you want.",
            )
        except Exception:
            pass

    def _open_ahk_folder(self):
        fn = getattr(self._parent_app, '_open_voice_scripts_folder', None)
        if callable(fn):
            fn()
