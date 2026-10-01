"""Voice tab – voice commands and dictation control panel.

Mounted as Workbench's "🎤 Voice" top tab. The widget is parent-agnostic
– it just needs the following contract on the parent_app object:

    - voice_command_manager: VoiceCommandManager instance
    - voice_listener: ContinuousVoiceListener or None
    - load_dictation_settings() -> dict
    - _toggle_alwayson_listening() -> None
    - _save_voice_settings(model, duration, lang, enabled) -> None
    - _reset_voice_commands() -> None
    - _check_ahk_installed() -> str
    - _open_voice_scripts_folder() -> None

Pre-v1.10.4 this widget also lived inside Supervertaler Sidekick (the
retired floating-assistant window); the Always-On status used to be
pushed via parent_app._floating_assistant._voice_widget. After v1.10.4
the in-Workbench widget is located via parent_app._voice_top_widget
(see _update_alwayson_ui in Supervertaler.py).

Historically named "AutoFingers"; the internal name was simplified to
"Voice" in v1.9.491. The persisted layout key reads legacy
``autofingers_layout`` settings as a one-time fallback so existing users
keep their splitter / column widths.
"""
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QSpinBox, QGroupBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QScrollArea, QFrame, QMessageBox, QSplitter, QMenu,
    QPlainTextEdit,
)

from modules.styled_widgets import (
    CheckmarkCheckBox, PurpleCheckmarkCheckBox, HelpButton,
)
from modules.voice_command_dialog import VoiceCommandEditDialog
from modules.help_system import Topics as HelpTopics, set_topic as set_help_topic


_TYPE_LABELS = {
    "internal": "Command",
    "keystroke": "Keystroke",
    "ahk_script": "AHK Script",
    "ahk_inline": "AHK Inline",
}

_DISABLED_COLOUR = QColor(170, 170, 170)

# Column indices
_COL_ENABLED  = 0
_COL_PHRASE   = 1
_COL_ALIASES  = 2
_COL_TYPE     = 3
_COL_ACTION   = 4
_COL_CATEGORY = 5


class VoiceTab(QWidget):
    """Voice commands + dictation control panel."""

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
            "🎤 <b>Voice</b> – commands and dictation.<br>"
            "Toggle Always-On to listen continuously, or hold your "
            "dictation hotkey (<b>Ctrl+Shift+Space</b> by default, "
            "rebindable in Settings → Keyboard Shortcuts) to dictate."
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

        # ---- Left panel: settings (scrollable) -----------------------
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_body = QWidget()
        left_layout = QVBoxLayout(left_body)
        left_layout.setContentsMargins(10, 10, 6, 10)
        left_layout.setSpacing(10)
        left_scroll.setWidget(left_body)
        self._splitter.addWidget(left_scroll)

        # The recognition_engine setting is hard-pinned to 'vosk' for
        # Always-On in v1.9.493 onward – Always-On's sole job is voice
        # command recognition. Free-form dictation lives entirely on
        # the push-to-talk path (the user's choice of offline / online
        # is exposed in the Push-to-Talk group below). The dropdown for
        # Always-On engine is gone; users who had a non-Vosk engine
        # saved get migrated to Vosk on next save, with their
        # previously-chosen Whisper engine kept as the push-to-talk
        # backend so nothing they actually relied on disappears.
        self._engine_combo = None  # not displayed; kept as None so
        # _sync_engine_dependent_widgets / save paths can detect absence

        # Migrate a legacy Whisper-as-Always-On engine setting into the
        # push-to-talk engine slot so the user keeps their preferred
        # dictation backend even though Always-On is now Vosk-only.
        legacy_eng = settings.get('recognition_engine', 'vosk')
        if legacy_eng in ('faster_whisper', 'local', 'api'):
            existing_ptt = settings.get('pushtotalk_engine')
            if not existing_ptt or existing_ptt == 'auto':
                self._set_dictation_keys(pushtotalk_engine=(
                    'api' if legacy_eng == 'api' else 'faster_whisper'))
            self._set_dictation_keys(recognition_engine='vosk')

        # --- Microphone (top-level – applies to both surfaces) ---
        # Both Always-On (Vosk) and push-to-talk (faster-whisper) record
        # from the same physical mic, so this is a single setting that
        # governs all voice input. Stored as the device *name* so it
        # survives across sessions (device indices shuffle when USB
        # devices are added / removed). Lookup happens at record time
        # via mic_devices.resolve_device_index – if the saved device is
        # gone, sounddevice silently falls back to the OS default.
        from modules.mic_devices import list_input_devices, DEFAULT_SENTINEL
        mic_row = QHBoxLayout()
        mic_row.addWidget(QLabel("Microphone:"))
        self._mic_combo = QComboBox()
        self._mic_combo.addItem(
            "System default (currently selected in Windows)",
            DEFAULT_SENTINEL,
        )
        for name in list_input_devices():
            self._mic_combo.addItem(name, name)
        # Restore saved selection if the device is still attached;
        # otherwise leave the combo on "System default" (index 0).
        saved_mic = settings.get('mic_device', DEFAULT_SENTINEL)
        for i in range(self._mic_combo.count()):
            if self._mic_combo.itemData(i) == saved_mic:
                self._mic_combo.setCurrentIndex(i)
                break
        self._mic_combo.currentIndexChanged.connect(self._on_mic_changed)
        mic_row.addWidget(self._mic_combo, stretch=1)
        # Wrap in a tiny widget so we can give it left/right margins
        # consistent with the group boxes below it.
        mic_widget = QWidget()
        mic_widget.setLayout(mic_row)
        left_layout.addWidget(mic_widget)

        # --- Voice commands (Always-On Vosk listener) ---
        alwayson_group = QGroupBox("🎤 Voice commands (Always-On listening)")
        ao_layout = QVBoxLayout()

        ao_status_row = QHBoxLayout()
        self._status_label = QLabel("⚪ Not active")
        self._status_label.setStyleSheet("font-size: 9pt; padding: 4px;")
        ao_status_row.addWidget(self._status_label)
        ao_status_row.addStretch()
        self._toggle_btn = QPushButton("▶️ Start Always-On")
        self._toggle_btn.setStyleSheet("padding: 6px 12px;")
        self._toggle_btn.clicked.connect(self._toggle_alwayson)
        ao_status_row.addWidget(self._toggle_btn)
        ao_layout.addLayout(ao_status_row)

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
        ao_layout.addLayout(sens_row)

        # v1.10.194: parallel hotkey-display row for the v1.10.193
        # command-PTT chord. Mirrors the dictation-hotkey row below
        # (read-only label + "Change in Settings…" link) so the user
        # can see which hotkey holds command listening open without
        # diving into Settings. Refreshed on every showEvent via
        # _refresh_hotkey_label so a rebind picked up between two
        # Sidekick opens is reflected immediately.
        cmd_ptt_row = QHBoxLayout()
        cmd_ptt_row.addWidget(QLabel("Push-to-talk hotkey:"))
        self._cmd_ptt_hotkey_label = QLabel("Ctrl+Alt+V")
        self._cmd_ptt_hotkey_label.setTextFormat(Qt.TextFormat.RichText)
        self._cmd_ptt_hotkey_label.setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace; "
            "font-size: 9pt; padding: 2px 6px; "
            "background-color: #F5F5F5; border: 1px solid #DDD; border-radius: 3px;"
        )
        self._cmd_ptt_hotkey_label.setToolTip(
            "Hold this hotkey to temporarily activate the command "
            "listener — without leaving Always-On enabled all the time. "
            "Useful when an external dictation app (Wispr Flow etc.) needs "
            "the microphone the rest of the time."
        )
        cmd_ptt_row.addWidget(self._cmd_ptt_hotkey_label)
        cmd_ptt_row.addStretch()
        cmd_ptt_hint = QLabel(
            "<a href='change-cmd-ptt-hotkey' style='color: #1976D2;'>"
            "Change in Settings → Keyboard Shortcuts</a>"
        )
        cmd_ptt_hint.setTextFormat(Qt.TextFormat.RichText)
        cmd_ptt_hint.setStyleSheet("font-size: 8pt;")
        cmd_ptt_hint.linkActivated.connect(
            lambda _: self._open_keyboard_shortcuts_settings()
        )
        cmd_ptt_row.addWidget(cmd_ptt_hint)
        ao_layout.addLayout(cmd_ptt_row)

        cmd_ptt_info = QLabel(
            "Hold the hotkey to listen for voice commands; release to stop. "
            "Always-On stays off the rest of the time so other dictation "
            "apps (Wispr Flow, Dragon, etc.) have the microphone to themselves."
        )
        cmd_ptt_info.setWordWrap(True)
        cmd_ptt_info.setStyleSheet("font-size: 8pt; color: #666;")
        ao_layout.addWidget(cmd_ptt_info)

        # "Listen for commands only" checkbox has been removed in v1.9.493:
        # Always-On now always uses Vosk, which is grammar-constrained
        # ("commands only" is its only mode). Kept as a hidden attribute
        # so _sync_engine_dependent_widgets and the save path can no-op
        # cleanly without raising AttributeError.
        self._commands_only_cb = None
        self._ao_api_tip = None

        ao_focus_hint = QLabel(
            "ℹ️ Always-On uses <b>Vosk</b> for voice command recognition – "
            "offline, free, ~zero CPU. It only ever fires commands; "
            "free-text dictation lives on the push-to-talk path below. "
            "Commands and dictation both go to whichever app is in the "
            "foreground when you speak."
        )
        ao_focus_hint.setTextFormat(Qt.TextFormat.RichText)
        ao_focus_hint.setWordWrap(True)
        ao_focus_hint.setStyleSheet(
            "font-size: 8pt; color: #555; background-color: #FFF8E1;"
            " border: 1px solid #FFE082; border-radius: 4px; padding: 6px;"
        )
        ao_layout.addWidget(ao_focus_hint)
        alwayson_group.setLayout(ao_layout)
        left_layout.addWidget(alwayson_group)

        # --- Pause Always-On for external dictation ---
        # A user-RECORDED global hotkey (works with odd keys like media
        # fast-forward) that pauses Always-On while an external dictation
        # tool holds the mic. Hold = paused while held; Toggle = press to
        # pause / press to resume. Recorded, not typed, via the global hook.
        pause_group = QGroupBox("⏸️ Pause Always-On for external dictation")
        pause_layout = QVBoxLayout()

        pk_row = QHBoxLayout()
        pk_row.addWidget(QLabel("Hotkey:"))
        self._voice_pause_label = QLabel("Not set")
        self._voice_pause_label.setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace; "
            "font-size: 9pt; padding: 2px 6px; "
            "background-color: #F5F5F5; border: 1px solid #DDD; border-radius: 3px;"
        )
        # Share with the app so capture/clear handlers can update it directly.
        try:
            self._parent_app._voice_pause_hotkey_label = self._voice_pause_label
        except Exception:
            pass
        pk_row.addWidget(self._voice_pause_label)
        self._voice_pause_record_btn = QPushButton("⏺ Record key")
        self._voice_pause_record_btn.setToolTip(
            "Click, then press the key you use to start your external "
            "dictation tool (e.g. the media fast-forward key)."
        )
        self._voice_pause_record_btn.clicked.connect(self._on_record_pause_hotkey)
        if sys.platform == 'darwin':
            # Recording a key needs the global keyboard listener, which macOS
            # 26 doesn't allow off the main thread (#188)
            self._voice_pause_record_btn.setEnabled(False)
            self._voice_pause_record_btn.setToolTip(
                "Not available on macOS yet: recording a key needs a keyboard "
                "listener that macOS doesn't allow.")
        pk_row.addWidget(self._voice_pause_record_btn)
        clear_pause_btn = QPushButton("Clear")
        clear_pause_btn.clicked.connect(self._on_clear_pause_hotkey)
        pk_row.addWidget(clear_pause_btn)
        pk_row.addStretch()
        pause_layout.addLayout(pk_row)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        self._voice_pause_mode_combo = QComboBox()
        self._voice_pause_mode_combo.addItem("Hold (pause while held)", "hold")
        self._voice_pause_mode_combo.addItem("Toggle (press to pause / resume)", "toggle")
        self._voice_pause_mode_combo.currentIndexChanged.connect(self._on_pause_mode_changed)
        mode_row.addWidget(self._voice_pause_mode_combo)
        mode_row.addStretch()
        pause_layout.addLayout(mode_row)

        pause_info = QLabel(
            "Record the key your external dictation tool uses — media keys work too. "
            "In <b>Hold</b> mode Always-On pauses only while you hold the key and resumes "
            "when you release it (pair with hold-to-talk tools like Wispr&nbsp;Flow). "
            "Use <b>Toggle</b> if your tool starts/stops on a single tap."
        )
        pause_info.setTextFormat(Qt.TextFormat.RichText)
        pause_info.setWordWrap(True)
        pause_info.setStyleSheet("font-size: 8pt; color: #666;")
        pause_layout.addWidget(pause_info)

        pause_group.setLayout(pause_layout)
        left_layout.addWidget(pause_group)

        # --- Dictation (push-to-talk faster-whisper) ---
        # Engine is hard-pinned to faster-whisper here in v1.9.493+: the
        # OpenAI API option was removed because local models cover the
        # use case fine and the API choice was adding noise without
        # value. The Whisper Model controls (model size, max duration,
        # language) live INSIDE this group now rather than as a separate
        # section, since they only affect this dictation path.
        ptt_group = QGroupBox("🗣️ Dictation (push-to-talk)")
        ptt_layout = QVBoxLayout()

        # Show the current hotkey (read from the user's shortcut_manager
        # binding). Read-only display – rebinding happens in
        # Settings → Keyboard Shortcuts, where the capture widget can
        # handle numpad keys etc. We refresh this label on every showEvent
        # so the user sees their current binding without having to
        # reopen Sidekick.
        hk_row = QHBoxLayout()
        hk_row.addWidget(QLabel("Hotkey:"))
        self._hotkey_label = QLabel("Ctrl+Shift+Space")
        self._hotkey_label.setTextFormat(Qt.TextFormat.RichText)
        self._hotkey_label.setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace; "
            "font-size: 9pt; padding: 2px 6px; "
            "background-color: #F5F5F5; border: 1px solid #DDD; border-radius: 3px;"
        )
        hk_row.addWidget(self._hotkey_label)
        hk_row.addStretch()
        hk_hint = QLabel(
            "<a href='change-hotkey' style='color: #1976D2;'>Change in Settings → Keyboard Shortcuts</a>"
        )
        hk_hint.setTextFormat(Qt.TextFormat.RichText)
        hk_hint.setStyleSheet("font-size: 8pt;")
        hk_hint.linkActivated.connect(lambda _: self._open_keyboard_shortcuts_settings())
        hk_row.addWidget(hk_hint)
        ptt_layout.addLayout(hk_row)

        ptt_info = QLabel(
            "Hold the hotkey to dictate, release to transcribe. "
            "Works inside the Workbench grid and in any other app on "
            "your computer. Try binding numpad <b>+</b> (or any other "
            "key) for one-finger dictation."
        )
        ptt_info.setTextFormat(Qt.TextFormat.RichText)
        ptt_info.setWordWrap(True)
        ptt_info.setStyleSheet("font-size: 8pt; color: #666;")
        ptt_layout.addWidget(ptt_info)

        ptt_row = QHBoxLayout()
        ptt_row.addWidget(QLabel("Mode:"))
        self._ptt_combo = QComboBox()
        self._ptt_combo.addItem(
            "Hold-to-talk (hold the hotkey, release to stop) – recommended", "hold")
        self._ptt_combo.addItem(
            "Toggle (press the hotkey to start, press again to stop)", "toggle")
        saved_ptt = settings.get('pushtotalk_mode', 'hold')
        for i in range(self._ptt_combo.count()):
            if self._ptt_combo.itemData(i) == saved_ptt:
                self._ptt_combo.setCurrentIndex(i)
                break
        self._ptt_combo.currentIndexChanged.connect(self._on_ptt_changed)
        ptt_row.addWidget(self._ptt_combo, stretch=1)
        ptt_layout.addLayout(ptt_row)

        # Hard-pin push-to-talk to faster-whisper. If the user previously
        # had pushtotalk_engine='api', force it back to faster_whisper so
        # the rest of the app's routing reads a sensible value. The UI
        # widget is gone; advanced users who really want the API can hand-
        # edit dictation_settings.json (the backend still honours 'api').
        if settings.get('pushtotalk_engine') != 'faster_whisper':
            self._set_dictation_keys(pushtotalk_engine='faster_whisper')
        self._ptt_engine_combo = None  # widget removed in v1.9.493

        # Whisper Model controls live inside this group now, since
        # they only affect this dictation path (Always-On is Vosk).
        # Separator label tells the user what model means.
        model_info = QLabel(
            "<b>Whisper model</b> – larger = more accurate but slower to "
            "load and transcribe.<br>"
            "<span style='color:#888;'>"
            "tiny ~75 MB · base ~142 MB (recommended) · small ~466 MB · "
            "medium ~1.5 GB · large ~2.9 GB</span>"
        )
        model_info.setTextFormat(Qt.TextFormat.RichText)
        model_info.setStyleSheet(
            "font-size: 8pt; color: #555; padding-top: 6px;")
        model_info.setWordWrap(True)
        ptt_layout.addWidget(model_info)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Model:"))
        self._model_combo = QComboBox()
        self._model_combo.addItems(["tiny", "base", "small", "medium", "large"])
        self._model_combo.setCurrentText(settings.get('model', 'base'))
        model_row.addWidget(self._model_combo)
        model_row.addSpacing(12)
        model_row.addWidget(QLabel("Max:"))
        self._duration_spin = QSpinBox()
        self._duration_spin.setMinimum(3)
        self._duration_spin.setMaximum(60)
        self._duration_spin.setValue(settings.get('max_duration', 10))
        self._duration_spin.setSuffix(" sec")
        model_row.addWidget(self._duration_spin)
        ptt_layout.addLayout(model_row)

        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("Language:"))
        self._lang_combo = QComboBox()
        # "Auto-detect" (top) lets Whisper analyse each utterance and pick
        # the language itself – useful when you regularly dictate in more
        # than one language without wanting to flip this setting. Needs
        # ~1 s of speech to be reliable; very short utterances can
        # mis-detect, so explicit language is still recommended when you
        # know you'll only ever dictate in one language for a session.
        # "Auto (use project target language)" is the legacy behaviour –
        # reads the language from the Workbench project so a single-
        # language project doesn't have to be told what it is twice.
        self._lang_combo.addItems([
            "Auto-detect (Whisper picks per utterance)",
            "Auto (use project target language)",
            "English", "Dutch", "German", "French", "Spanish",
            "Italian", "Portuguese", "Polish", "Russian",
            "Chinese", "Japanese", "Korean",
        ])
        self._lang_combo.setCurrentText(
            settings.get('language', 'Auto (use project target language)'))
        lang_row.addWidget(self._lang_combo, stretch=1)
        ptt_layout.addLayout(lang_row)

        # The separate Whisper Model group is gone in v1.9.493 – its
        # controls are above. _whisper_group kept as None for the
        # _sync_engine_dependent_widgets back-compat path.
        self._whisper_group = None

        ptt_group.setLayout(ptt_layout)
        left_layout.addWidget(ptt_group)

        # =================================================
        # Dictation vocabulary (v1.10.26) — Whisper biasing
        # =================================================
        # Whisper's decoder accepts an ``initial_prompt`` string that
        # biases transcription toward the vocabulary it contains. We
        # always seed it with the Supervertaler ecosystem terms (see
        # modules.voice_vocabulary.DEFAULT_VOCABULARY) so brand names
        # don't mistranscribe out of the box. This UI lets the user
        # extend that with their own custom terms, add post-process
        # replacements for stubborn cases, and opt into pulling
        # vocabulary from the active project's termbase.
        vocab_group = QGroupBox("📚 Dictation vocabulary")
        vocab_layout = QVBoxLayout()

        vocab_info = QLabel(
            "Help Whisper transcribe brand names and technical terms "
            "correctly. Built-in defaults already cover Supervertaler, "
            "Trados, memoQ, OpenAI, etc. – add your own below."
        )
        vocab_info.setWordWrap(True)
        vocab_info.setStyleSheet("font-size: 8pt; color: #666;")
        vocab_layout.addWidget(vocab_info)

        # Custom dictionary textarea: comma- or newline-separated.
        vocab_layout.addWidget(QLabel("<b>Custom dictionary</b>"))
        vocab_dict_hint = QLabel(
            "Brand / technical terms Whisper should recognise. Comma- or "
            "newline-separated. Examples: client names, product names, "
            "industry jargon."
        )
        vocab_dict_hint.setWordWrap(True)
        vocab_dict_hint.setStyleSheet("font-size: 8pt; color: #888;")
        vocab_layout.addWidget(vocab_dict_hint)
        self._vocab_dict_edit = QPlainTextEdit()
        self._vocab_dict_edit.setPlaceholderText(
            "e.g. Acme Corp, Beijerterm, polyurethane, embodiment"
        )
        self._vocab_dict_edit.setFixedHeight(70)
        vocab_layout.addWidget(self._vocab_dict_edit)

        # "Bias from your termbases" checkbox. v1.10.28 reworked this:
        # the original wording said "active project's termbase
        # (source-language entries)" which had three things wrong
        # with it – it silently did nothing without a project open,
        # the "active" was ambiguous (project Read/Write toggles), and
        # it pulled source-side terms when translators dictate the
        # target side. Now uses each termbase's own 🎤 Voice flag
        # (Termbase Manager → 🎤 Voice column, opt-in as of v1.10.29)
        # and pulls target-language terms.
        # v1.10.30: switched from green CheckmarkCheckBox to purple
        # PurpleCheckmarkCheckBox so this toggle visually matches the
        # 🎤 Voice column in Termbase Manager (same feature, same
        # colour) and reads as a more deliberate UI element than the
        # bare-green default did.
        self._vocab_use_termbase_cb = PurpleCheckmarkCheckBox(
            "Also bias from your termbases (target-language terms)"
        )
        self._vocab_use_termbase_cb.setStyleSheet("font-size: 9pt;")
        self._vocab_use_termbase_cb.setToolTip(
            "Append target-language terms from every termbase you have "
            "ticked 🎤 Voice in the Termbase Manager. Opt-in: no termbase "
            "biases dictation by default – pick a few in the Termbase "
            "Manager that match what you're translating. Works whether "
            "or not you have a project open (the per-termbase flag is "
            "independent of project context). Capped at 200 terms per "
            "dictation to stay within Whisper's prompt budget."
        )
        # v1.10.31: on tick, navigate to the 🏷️ Termbases top tab so
        # the user can immediately pick which termbases to opt in
        # (the per-termbase 🎤 Voice flag is the thing that actually
        # contributes terms – this top-level toggle is the master
        # enable). Only fires on the rising edge: unticking doesn't
        # navigate anywhere.
        self._vocab_use_termbase_cb.toggled.connect(
            self._on_vocab_use_termbase_toggled
        )
        vocab_layout.addWidget(self._vocab_use_termbase_cb)

        # Replacements table.
        vocab_layout.addSpacing(6)
        vocab_layout.addWidget(QLabel("<b>Replacements</b>"))
        repl_hint = QLabel(
            "Fix specific mistranscriptions: enter what Whisper hears, "
            "then what you actually meant. Common defaults are applied "
            "automatically (e.g. \"supervertile\" → Supervertaler)."
        )
        repl_hint.setWordWrap(True)
        repl_hint.setStyleSheet("font-size: 8pt; color: #888;")
        vocab_layout.addWidget(repl_hint)

        self._vocab_repl_table = QTableWidget(0, 2)
        self._vocab_repl_table.setHorizontalHeaderLabels(["Heard", "Meant"])
        self._vocab_repl_table.horizontalHeader().setStretchLastSection(True)
        self._vocab_repl_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._vocab_repl_table.verticalHeader().setVisible(False)
        self._vocab_repl_table.setFixedHeight(110)
        self._vocab_repl_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        vocab_layout.addWidget(self._vocab_repl_table)

        repl_btn_row = QHBoxLayout()
        repl_add_btn = QPushButton("➕ Add row")
        repl_add_btn.clicked.connect(self._vocab_add_repl_row)
        repl_btn_row.addWidget(repl_add_btn)
        repl_del_btn = QPushButton("➖ Remove selected")
        repl_del_btn.clicked.connect(self._vocab_remove_repl_row)
        repl_btn_row.addWidget(repl_del_btn)
        repl_btn_row.addStretch()
        vocab_layout.addLayout(repl_btn_row)

        vocab_group.setLayout(vocab_layout)
        left_layout.addWidget(vocab_group)

        # Populate from saved settings.
        self._vocab_load_into_ui()

        # AutoHotkey Integration
        ahk_group = QGroupBox("⌨️ AutoHotkey Integration")
        ahk_layout = QVBoxLayout()
        ahk_info = QLabel(
            "Voice commands can trigger AutoHotkey scripts for system-level "
            "automation across Trados, memoQ, Word, and other apps."
        )
        ahk_info.setWordWrap(True)
        ahk_info.setStyleSheet("font-size: 8pt; color: #666;")
        ahk_layout.addWidget(ahk_info)
        ahk_row = QHBoxLayout()
        self._ahk_status_label = QLabel(self._ahk_status_text())
        self._ahk_status_label.setStyleSheet("font-size: 8pt;")
        ahk_row.addWidget(self._ahk_status_label)
        ahk_row.addStretch()
        scripts_btn = QPushButton("📂 Open Scripts Folder")
        scripts_btn.clicked.connect(self._open_ahk_folder)
        ahk_row.addWidget(scripts_btn)
        ahk_layout.addLayout(ahk_row)
        ahk_group.setLayout(ahk_layout)
        left_layout.addWidget(ahk_group)

        # Save button
        save_btn = QPushButton("💾 Save Voice Settings")
        save_btn.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold;"
            " padding: 8px; border: none;"
        )
        save_btn.clicked.connect(self._save_settings)
        left_layout.addWidget(save_btn)

        left_layout.addStretch()

        # Now that every engine-dependent widget has been instantiated,
        # run a final sync so the OpenAI tip / push-to-talk engine label
        # / commands-only checkbox visibility match the saved engine.
        # The earlier sync call (line ~185) only had access to a subset
        # of these widgets because Push-to-Talk and the Whisper group
        # hadn't been built yet.
        self._sync_engine_dependent_widgets()

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
            "Say a phrase to execute its action. If no command matches, the "
            "spoken text is inserted as dictation. Double-click a row to edit."
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

        self._splitter.setStretchFactor(0, 2)
        self._splitter.setStretchFactor(1, 3)

    # ------------------------------------------------------------------
    # Layout persistence
    # ------------------------------------------------------------------

    def _save_layout(self):
        self._set_dictation_keys(voice_layout={
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
        self._refresh_voice_pause_ui()

    # ── Pause-Always-On hotkey (record-a-key) ───────────────────────────

    def _on_record_pause_hotkey(self):
        fn = getattr(self._parent_app, '_begin_voice_pause_capture', None)
        if callable(fn):
            self._voice_pause_label.setText("Press a key…")
            fn()

    def _on_clear_pause_hotkey(self):
        fn = getattr(self._parent_app, '_clear_voice_pause_hotkey', None)
        if callable(fn):
            fn()
        self._voice_pause_label.setText("Not set")

    def _on_pause_mode_changed(self, idx: int):
        mode = self._voice_pause_mode_combo.itemData(idx) or 'hold'
        self._set_dictation_keys(voice_pause_mode=mode)

    def _refresh_voice_pause_ui(self):
        """Sync the pause-hotkey label + mode dropdown from saved settings."""
        loader = getattr(self._parent_app, 'load_dictation_settings', None)
        ds = loader() if callable(loader) else {}
        spec = ds.get('voice_pause_hotkey') or ''
        try:
            from modules.voice_hotkey_listener import parse_serialized_chord, describe_chord
            chord = parse_serialized_chord(spec) if spec else None
            self._voice_pause_label.setText(describe_chord(chord) if chord else "Not set")
        except Exception:
            self._voice_pause_label.setText(spec or "Not set")
        combo = getattr(self, '_voice_pause_mode_combo', None)
        if combo is not None:
            i = combo.findData(ds.get('voice_pause_mode', 'hold'))
            if i >= 0:
                combo.blockSignals(True)
                combo.setCurrentIndex(i)
                combo.blockSignals(False)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

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
        else:
            self._status_label.setText("⚪ Not active")
            self._status_label.setStyleSheet(
                "font-size: 9pt; padding: 4px;")
            self._toggle_btn.setText("▶️ Start Always-On")
            self._toggle_btn.setStyleSheet("padding: 6px 12px;")

    # ------------------------------------------------------------------
    # Settings handlers
    # ------------------------------------------------------------------

    def _set_dictation_keys(self, **keys):
        """Update one or more keys under prefs['ui']['dictation_settings'] in
        the unified settings JSON, preserving any unrelated keys."""
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

    def _on_engine_changed(self, idx: int):
        # Vestigial – the Always-On engine dropdown is gone in v1.9.493
        # (Always-On is always Vosk). Kept as a no-op so any straggling
        # currentIndexChanged signal from old wiring doesn't crash.
        return

    def _sync_commands_only_for_engine(self):
        """Back-compat shim – delegates to the new unified sync method."""
        self._sync_engine_dependent_widgets()

    def _sync_engine_dependent_widgets(self):
        """No-op kept as a safe call site.

        v1.9.493 removed both engine dropdowns (Always-On is always Vosk;
        push-to-talk is always faster-whisper). The Whisper Model
        controls are always relevant now – nothing needs syncing. The
        method stays so older code paths that call it (init, the
        legacy _sync_commands_only_for_engine shim) don't raise.
        """
        return

    def _on_mic_changed(self, idx: int):
        """Persist the user's microphone choice.

        Stored as the device name (or the DEFAULT_SENTINEL string) so the
        next session re-binds to the same physical mic even if its index
        has shifted due to other USB devices being attached. The engines
        resolve name → index at record time via mic_devices.resolve_device_index.
        """
        try:
            saved = self._mic_combo.itemData(idx)
            self._set_dictation_keys(mic_device=saved)
        except Exception:
            pass

    def _on_sensitivity_changed(self, idx: int):
        sensitivity = ['low', 'medium', 'high'][idx]
        self._set_dictation_keys(alwayson_sensitivity=sensitivity)
        # Live-apply if a listener is currently running.
        listener = getattr(self._parent_app, 'voice_listener', None)
        if listener is not None and hasattr(listener, 'set_sensitivity'):
            try:
                listener.set_sensitivity(sensitivity)
            except Exception:
                pass

    def _on_ptt_changed(self, idx: int):
        mode = self._ptt_combo.itemData(idx) or 'toggle'
        self._set_dictation_keys(pushtotalk_mode=mode)

    def _on_ptt_engine_changed(self, idx: int):
        """Persist the user's push-to-talk engine choice and refresh
        the Whisper Model group's enabled state (which depends on it)."""
        engine = self._ptt_engine_combo.itemData(idx) or 'faster_whisper'
        self._set_dictation_keys(pushtotalk_engine=engine)
        self._sync_engine_dependent_widgets()

    def _refresh_hotkey_label(self):
        """Update the read-only hotkey displays from shortcut_manager.

        Called on showEvent so the user always sees their current
        bindings without having to dismiss + re-summon Sidekick after
        rebinding in Settings → Keyboard Shortcuts.

        v1.10.194: also refreshes the command-PTT chord label (added
        alongside the dictate label in the Voice-commands group).
        """
        try:
            sm = getattr(self._parent_app, 'shortcut_manager', None)
            if sm is None:
                return

            try:
                from modules.shortcut_display import format_shortcut_for_display
            except Exception:
                format_shortcut_for_display = lambda s: s  # noqa: E731

            # Dictation push-to-talk
            try:
                shortcut = sm.get_shortcut('voice_dictate') or 'Ctrl+Shift+Space'
                self._hotkey_label.setText(format_shortcut_for_display(shortcut))
            except Exception:
                pass

            # v1.10.194: command push-to-talk (the new v1.10.193 chord).
            # Defensive — older settings files may not have this binding,
            # in which case fall back to the documented default.
            try:
                cmd_shortcut = sm.get_shortcut('voice_command_ptt') or 'Ctrl+Alt+V'
                if hasattr(self, '_cmd_ptt_hotkey_label'):
                    self._cmd_ptt_hotkey_label.setText(
                        format_shortcut_for_display(cmd_shortcut)
                    )
            except Exception:
                pass
        except Exception:
            pass

    def _open_keyboard_shortcuts_settings(self):
        """Bounce the user to Settings → Keyboard Shortcuts to rebind.

        Sidekick lives in a floating window; Settings lives in the main
        Workbench. We open the main window's Settings tab and select
        Keyboard Shortcuts. Falls back to a help-message if the main
        app doesn't expose the entry point.
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
                "Rebind dictation hotkey",
                "Open the main Workbench window → <b>Settings</b> → "
                "<b>Keyboard Shortcuts</b>. Find "
                "<b>Voice dictation / push-to-talk</b> in the "
                "<b>Special</b> category, click the shortcut field, "
                "and press the key combo you want (numpad + works).",
            )
        except Exception:
            pass

    def _on_commands_only_toggled(self, checked: bool):
        self._set_dictation_keys(alwayson_commands_only=bool(checked))

    def _save_settings(self):
        ok = self._set_dictation_keys(
            model=self._model_combo.currentText(),
            max_duration=self._duration_spin.value(),
            language=self._lang_combo.currentText(),
        )
        # v1.10.26: also persist the dictation-vocabulary settings.
        # Saved alongside the standard dictation settings so the user
        # gets a single "Save" button for the whole Voice tab.
        vocab_ok = self._vocab_save_from_ui()
        if ok and vocab_ok:
            QMessageBox.information(
                self, "Voice Settings", "Settings saved.")
        else:
            QMessageBox.warning(
                self, "Voice Settings",
                "Couldn't save settings – check the log for details.")

    # -----------------------------------------------------------------
    # Dictation-vocabulary helpers (v1.10.26).
    #
    # Storage shape: parent_app.load_voice_vocabulary_settings() /
    # parent_app.save_voice_vocabulary_settings() handle the JSON
    # round-trip. These methods just translate between that shape and
    # the UI widgets (textarea + table + checkbox).
    # -----------------------------------------------------------------

    def _vocab_load_into_ui(self):
        """Populate the vocabulary UI widgets from saved settings.
        Called once after _build_ui; also re-runnable if the user
        ever wants a Refresh / Reset hook in the future.
        """
        try:
            loader = getattr(self._parent_app, 'load_voice_vocabulary_settings', None)
            if not callable(loader):
                return
            settings = loader() or {}
            terms = settings.get('custom_terms') or []
            replacements = settings.get('replacements') or []
            use_tb = bool(settings.get('use_termbase', True))

            # Custom-dictionary textarea: one term per line.
            self._vocab_dict_edit.setPlainText("\n".join(terms))
            # Termbase-bias checkbox. blockSignals around setChecked so
            # the navigate-on-tick handler installed in _build_ui
            # doesn't spuriously fire and yank the user to the
            # Termbases tab at app start-up (or every time the Voice
            # tab is re-built).
            self._vocab_use_termbase_cb.blockSignals(True)
            self._vocab_use_termbase_cb.setChecked(use_tb)
            self._vocab_use_termbase_cb.blockSignals(False)
            # Replacements table.
            self._vocab_repl_table.setRowCount(0)
            for entry in replacements:
                if not isinstance(entry, dict):
                    continue
                heard = str(entry.get('heard', '')).strip()
                meant = str(entry.get('meant', '')).strip()
                if not heard or not meant:
                    continue
                row = self._vocab_repl_table.rowCount()
                self._vocab_repl_table.insertRow(row)
                self._vocab_repl_table.setItem(row, 0, QTableWidgetItem(heard))
                self._vocab_repl_table.setItem(row, 1, QTableWidgetItem(meant))
        except Exception as e:
            print(f"[VoiceTab vocab] load into UI failed: {e!r}")

    def _vocab_save_from_ui(self) -> bool:
        """Persist the vocabulary UI widget state back to settings.
        Returns True on success, False if anything went wrong (the
        caller can decide whether that's a hard error or a soft
        warning).
        """
        try:
            saver = getattr(self._parent_app, 'save_voice_vocabulary_settings', None)
            if not callable(saver):
                return True  # Older parent app – treat as no-op success.

            # Custom terms: split on commas + newlines, drop blanks.
            raw = self._vocab_dict_edit.toPlainText()
            terms = []
            seen = set()
            for chunk in raw.replace(',', '\n').splitlines():
                t = chunk.strip()
                if not t:
                    continue
                key = t.lower()
                if key in seen:
                    continue
                seen.add(key)
                terms.append(t)

            # Replacements table → list of dicts.
            replacements = []
            for row in range(self._vocab_repl_table.rowCount()):
                heard_item = self._vocab_repl_table.item(row, 0)
                meant_item = self._vocab_repl_table.item(row, 1)
                heard = (heard_item.text() if heard_item else '').strip()
                meant = (meant_item.text() if meant_item else '').strip()
                if heard and meant:
                    replacements.append({'heard': heard, 'meant': meant})

            use_tb = self._vocab_use_termbase_cb.isChecked()

            saver(terms, replacements, use_tb)
            return True
        except Exception as e:
            print(f"[VoiceTab vocab] save from UI failed: {e!r}")
            return False

    def _on_vocab_use_termbase_toggled(self, checked: bool):
        """Rising-edge handler: when the user ticks "Also bias from
        your termbases", jump to the 🏷️ Termbases top tab so they
        can immediately pick which termbases to opt in via the
        per-termbase 🎤 Voice column. Unticking is a no-op (no
        navigation) because there's nowhere obvious for the user
        to land in that case.

        We also persist the new state immediately rather than waiting
        for the user to click Save Voice Settings, because navigating
        away from the Voice tab without saving would otherwise lose
        the very change that triggered the navigation. The save
        round-trips through the parent app's
        ``save_voice_vocabulary_settings`` which writes the full
        ``voice_vocabulary`` section – we just call our existing
        ``_vocab_save_from_ui()`` to do that without duplicating the
        widget-to-dict translation.
        """
        if not checked:
            return  # Untick: stay put.

        # Persist the new state right now so the user doesn't have
        # to remember to click Save after navigating away.
        try:
            self._vocab_save_from_ui()
        except Exception as e:
            print(f"[VoiceTab vocab] save-on-tick error: {e!r}")

        # Navigate. The handler returns False if the Termbases tab
        # isn't present – tolerate that silently rather than show an
        # error, since the user already got the value they wanted
        # (the checkbox stays ticked, settings saved).
        try:
            opener = getattr(self._parent_app, 'open_termbases_tab', None)
            if callable(opener):
                opener()
        except Exception as e:
            print(f"[VoiceTab vocab] navigate-to-Termbases error: {e!r}")

    def _vocab_add_repl_row(self):
        """Append an empty row to the replacements table and put the
        cursor in the first cell so the user can start typing
        immediately."""
        row = self._vocab_repl_table.rowCount()
        self._vocab_repl_table.insertRow(row)
        self._vocab_repl_table.setItem(row, 0, QTableWidgetItem(""))
        self._vocab_repl_table.setItem(row, 1, QTableWidgetItem(""))
        self._vocab_repl_table.editItem(self._vocab_repl_table.item(row, 0))

    def _vocab_remove_repl_row(self):
        """Remove all selected rows from the replacements table.
        Iterates back-to-front so row indices stay valid as we delete."""
        rows = sorted(
            {idx.row() for idx in self._vocab_repl_table.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            self._vocab_repl_table.removeRow(row)

    def _open_ahk_folder(self):
        fn = getattr(self._parent_app, '_open_voice_scripts_folder', None)
        if callable(fn):
            fn()
