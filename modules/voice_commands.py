"""
Voice commands for Supervertaler Workbench.

Vosk listens in grammar mode: it can only hear the phrases in the
command list (or "[unk]"), so speech that is not a command is ignored
and never typed. Running text is left to a dictation tool (Wispr Flow,
Windows voice typing), as with SuperVoice in Supervertaler for Trados.

Command types: internal (a Workbench action), keystroke (a key chord
sent to the program in front), and AutoHotkey code (ahk_inline,
ahk_script; Windows only, needs AutoHotkey v2).
"""

import json
import os
import re
import sys
import subprocess
from pathlib import Path
from typing import Optional, Dict, List, Callable, Tuple
from dataclasses import dataclass, field, replace
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QWidget
from modules.shortcut_display import format_shortcut_for_display
from modules.platform_helpers import IS_WINDOWS, get_hidden_subprocess_flags


# Key names as stored by the command dialog (Qt's lowercase format, e.g.
# "ctrl+shift+s", "alt+down", "f6", "del") → pynput key names.
_PYNPUT_KEYS = {
    'ctrl': 'ctrl', 'control': 'ctrl', 'alt': 'alt', 'shift': 'shift',
    'meta': 'cmd', 'win': 'cmd', 'windows': 'cmd', 'cmd': 'cmd',
    'enter': 'enter', 'return': 'enter', 'tab': 'tab',
    'esc': 'esc', 'escape': 'esc', 'space': 'space', 'backspace': 'backspace',
    'del': 'delete', 'delete': 'delete', 'ins': 'insert', 'insert': 'insert',
    'home': 'home', 'end': 'end',
    'pgup': 'page_up', 'pageup': 'page_up', 'page_up': 'page_up',
    'pgdown': 'page_down', 'pgdn': 'page_down', 'pagedown': 'page_down', 'page_down': 'page_down',
    'up': 'up', 'down': 'down', 'left': 'left', 'right': 'right',
    **{f'f{n}': f'f{n}' for n in range(1, 21)},
}


def parse_chord(keystroke: str) -> list:
    """Turn "ctrl+shift+s" into pynput keys, in press order.

    Raises ValueError for an empty chord or a key name it doesn't know,
    so a typo in a command fails loudly instead of sending something else.
    """
    from pynput.keyboard import Key, KeyCode
    text = (keystroke or '').strip().lower()
    if text == '+':                       # the plus key on its own
        parts = ['+']
    elif text.endswith('++'):             # "ctrl++" means Ctrl and "+"
        parts = text[:-2].split('+') + ['+']
    else:
        parts = text.split('+')
    parts = [p.strip() for p in parts]
    keys = []
    for part in parts:
        if not part:
            raise ValueError(f"empty key in {keystroke!r}")
        if part in _PYNPUT_KEYS:
            keys.append(getattr(Key, _PYNPUT_KEYS[part]))
        elif len(part) == 1:
            keys.append(KeyCode.from_char(part))
        else:
            raise ValueError(f"unknown key {part!r} in {keystroke!r}")
    return keys


def send_chord(keystroke: str, controller=None) -> None:
    """Press a key chord in the program in front, then release it in
    reverse order (pynput's controller uses SendInput on Windows)."""
    keys = parse_chord(keystroke)
    if controller is None:
        from pynput.keyboard import Controller
        controller = Controller()
    pressed = []
    try:
        for key in keys:
            controller.press(key)
            pressed.append(key)
    finally:
        for key in reversed(pressed):
            controller.release(key)


@dataclass
class VoiceCommand:
    """Represents a single voice command"""
    phrase: str  # The spoken phrase (e.g., "confirm segment")
    aliases: List[str] = field(default_factory=list)  # Alternative phrases
    action_type: str = "internal"  # "internal", "keystroke", "ahk_script", "ahk_inline"
    action: str = ""  # Action to execute
    description: str = ""  # Human-readable description
    category: str = "general"  # Category for organization
    enabled: bool = True


class VoiceCommandManager(QObject):
    """
    Manages voice commands - matching spoken text to actions and executing them.
    """
    
    # Signals
    command_executed = pyqtSignal(str, str)  # (command_phrase, result_message)
    command_not_found = pyqtSignal(str)  # spoken_text that didn't match
    error_occurred = pyqtSignal(str)  # error message
    # Emitted whenever the command list is mutated and persisted via
    # save_commands(). Listeners (notably ContinuousVoiceListener under
    # the Vosk engine) can hook this to rebuild their grammar without
    # the user having to restart Always-On.
    commands_changed = pyqtSignal()
    
    # Default commands. Phrases are distinctive on purpose: the listener
    # keeps running while you dictate with another tool, so an everyday
    # word ("next", "okay", "save") would fire in the middle of a sentence.
    DEFAULT_COMMANDS = [
        # Navigation
        VoiceCommand("next segment", [], "internal", "navigate_next",
                    "Move to next segment", "navigation"),
        VoiceCommand("previous segment", [], "internal", "navigate_previous",
                    "Move to previous segment", "navigation"),
        VoiceCommand("first segment", ["go to start"], "internal", "navigate_first",
                    "Jump to first segment", "navigation"),
        VoiceCommand("last segment", ["go to end"], "internal", "navigate_last",
                    "Jump to last segment", "navigation"),

        # Segment actions
        VoiceCommand("confirm", ["confirm segment"], "internal", "confirm_segment",
                    "Confirm current segment", "editing"),
        VoiceCommand("copy source", ["copy from source", "source to target"], "internal", "copy_source_to_target",
                    "Copy source text to target", "editing"),
        VoiceCommand("clear target", ["delete target"], "internal", "clear_target",
                    "Clear target text", "editing"),
        VoiceCommand("undo", [], "keystroke", "ctrl+z",
                    "Undo last action", "editing"),
        VoiceCommand("redo", [], "keystroke", "ctrl+y",
                    "Redo last action", "editing"),

        # Translation
        VoiceCommand("translate segment", ["translate this"], "internal", "translate_segment",
                    "AI translate current segment", "translation"),
        VoiceCommand("translate all segments", ["batch translate"], "internal", "batch_translate",
                    "Translate all segments", "translation"),

        # Lookup & Search
        VoiceCommand("lookup", ["super lookup"], "internal", "open_superlookup",
                    f"Open Superlookup ({format_shortcut_for_display('Ctrl+K')})", "lookup"),
        VoiceCommand("concordance", ["search memory", "search TM"], "internal", "concordance_search",
                    "Open concordance search", "lookup"),

        # File operations
        VoiceCommand("save project", [], "keystroke", "ctrl+s",
                    "Save current project", "file"),
        VoiceCommand("open project", [], "keystroke", "ctrl+o",
                    "Open project", "file"),

        # View
        VoiceCommand("show log", ["open log"], "internal", "show_log",
                    "Show log panel", "view"),
        VoiceCommand("show editor", ["editor tab", "go to editor"], "internal", "show_editor",
                    "Show editor panel", "view"),

        # Listening
        VoiceCommand("stop listening", [], "internal", "stop_listening",
                    "Stop voice recognition", "voice"),

        # memoQ-specific (AHK v2)
        VoiceCommand("termbase", ["add term", "add to termbase"], "ahk_inline",
                    'Send "!{Down}"',  # Alt+Down
                    "Add term pair to memoQ termbase", "memoq"),
        VoiceCommand("tag next", ["next tag", "insert tag"], "ahk_inline",
                    'Send "^{PgDn}"\nSleep 100\nSend "{F9}"\nSleep 100\nSend "^{Enter}"',
                    "Go to end, insert next tag, confirm", "memoq"),
        VoiceCommand("confirm memoQ", ["confirm memo"], "ahk_inline",
                    'Send "^{Enter}"',
                    "Confirm segment in memoQ", "memoq"),

        # Trados-specific (AHK v2)
        VoiceCommand("confirm trados", ["confirm studio"], "ahk_inline",
                    'Send "^{Enter}"',
                    "Confirm segment in Trados Studio", "trados"),
    ]

    # Bumped whenever DEFAULT_COMMANDS gains a command that existing,
    # customised lists should receive. Version 2 (v1.10.374) only made the
    # defaults distinctive and dropped "start dictation".
    DEFAULTS_VERSION = 2
    # Default phrase -> the DEFAULTS_VERSION that introduced it. Defaults
    # not listed date from version 1. On load, a list saved at an older
    # version gets the newer defaults it lacks, as SuperVoice in Trados
    # does – never ones the user deleted earlier, and never edits to theirs.
    _DEFAULTS_SINCE: Dict[str, int] = {}
    # Actions that no longer exist; commands using them are dropped on load.
    _RETIRED_INTERNAL_ACTIONS = {"start_dictation"}
    
    def __init__(self, user_data_path: Path, main_window=None):
        super().__init__()
        self.user_data_path = user_data_path
        self.main_window = main_window
        self.commands: List[VoiceCommand] = []
        self.commands_file = user_data_path / "workbench" / "settings" / "voice_commands.json"
        self.ahk_script_dir = user_data_path / "workbench" / "voice_scripts"
        
        # Ensure directories exist
        self.ahk_script_dir.mkdir(parents=True, exist_ok=True)
        
        # Load commands
        self.load_commands()
        
        # Register internal handlers if main_window provided
        if main_window:
            self.register_main_window_handlers(main_window)
    
    def register_main_window_handlers(self, main_window):
        """Register handlers that call main window methods"""
        self.main_window = main_window
        
        self.internal_handlers = {
            # Navigation - using correct method names from Supervertaler.py
            "navigate_next": lambda: main_window.go_to_next_segment() if hasattr(main_window, 'go_to_next_segment') else self._log_missing('go_to_next_segment'),
            "navigate_previous": lambda: main_window.go_to_previous_segment() if hasattr(main_window, 'go_to_previous_segment') else self._log_missing('go_to_previous_segment'),
            "navigate_first": lambda: main_window.go_to_first_segment() if hasattr(main_window, 'go_to_first_segment') else self._log_missing('go_to_first_segment'),
            "navigate_last": lambda: main_window.go_to_last_segment() if hasattr(main_window, 'go_to_last_segment') else self._log_missing('go_to_last_segment'),
            
            # Editing - confirm_and_next_unconfirmed is the Enter key behavior
            "confirm_segment": lambda: main_window.confirm_and_next_unconfirmed() if hasattr(main_window, 'confirm_and_next_unconfirmed') else self._log_missing('confirm_and_next_unconfirmed'),
            "copy_source_to_target": lambda: main_window.copy_source_to_grid_target() if hasattr(main_window, 'copy_source_to_grid_target') else self._log_missing('copy_source_to_grid_target'),
            "clear_target": lambda: main_window.clear_grid_target() if hasattr(main_window, 'clear_grid_target') else self._log_missing('clear_grid_target'),
            
            # Translation
            "translate_segment": lambda: main_window.translate_current_segment() if hasattr(main_window, 'translate_current_segment') else self._log_missing('translate_current_segment'),
            "batch_translate": lambda: main_window.translate_batch() if hasattr(main_window, 'translate_batch') else self._log_missing('translate_batch'),
            
            # Lookup
            "open_superlookup": lambda: main_window._go_to_superlookup() if hasattr(main_window, '_go_to_superlookup') else self._log_missing('_go_to_superlookup'),
            "concordance_search": lambda: main_window.show_concordance_search() if hasattr(main_window, 'show_concordance_search') else self._log_missing('show_concordance_search'),
            
            # View
            # The Log is no longer a tab (v1.10.x moved it to a detached
            # window), so switch tabs would silently no-op — open the detached
            # Log window instead.
            "show_log": lambda: main_window.detach_log_window() if hasattr(main_window, 'detach_log_window') else self._log_missing('detach_log_window'),
            "show_editor": lambda: self._show_tab(main_window, "Editor"),
            
            # Listening
            "stop_listening": lambda: self._stop_voice_recognition(),
        }
        # Keystroke / AutoHotkey failures ("AutoHotkey not found", an
        # unknown key name) are reported through error_occurred.
        if hasattr(main_window, 'log'):
            self.error_occurred.connect(
                lambda message: main_window.log(f"❌ Voice command: {message}"))

    def _log_missing(self, method_name: str):
        """Log when a method is missing from main_window. Returns False,
        so the command is reported as failed, not done."""
        print(f"⚠️ Voice command: Method '{method_name}' not found on main window")
        if self.main_window and hasattr(self.main_window, 'log'):
            self.main_window.log(f"⚠️ Voice command: Method '{method_name}' not found")
        return False

    def _show_tab(self, main_window, tab_name: str):
        """Helper to switch to a specific tab"""
        if hasattr(main_window, 'main_tabs'):
            for i in range(main_window.main_tabs.count()):
                if tab_name.lower() in main_window.main_tabs.tabText(i).lower():
                    main_window.main_tabs.setCurrentIndex(i)
                    return
    
    def _stop_voice_recognition(self):
        """Switch Always-On off the way its button, the tray icon and
        Ctrl+Alt+O do, so the Voice tab, the tray and the status bar all
        show it stopped. Returns False when nothing is listening: the
        toggle would *start* the listener then.

        Up to v1.10.374 this looked for ``voice_command_listener``, which
        never existed (it is ``voice_listener``), so "stop listening"
        did nothing and was still listed with a ✓.
        """
        listener = getattr(self.main_window, 'voice_listener', None)
        if listener is None or not listener.is_listening:
            return False
        self.main_window._toggle_alwayson_listening()
        return True
    
    @classmethod
    def _default_commands(cls) -> List[VoiceCommand]:
        """Fresh copies, so editing a command never edits the defaults."""
        return [replace(c, aliases=list(c.aliases)) for c in cls.DEFAULT_COMMANDS]

    @classmethod
    def _command_from_dict(cls, cmd_data: dict) -> Optional[VoiceCommand]:
        """A saved command, or None if it uses a retired action."""
        command = VoiceCommand(
            phrase=cmd_data['phrase'],
            aliases=list(cmd_data.get('aliases', [])),
            action_type=cmd_data.get('action_type', 'internal'),
            action=cmd_data.get('action', ''),
            description=cmd_data.get('description', ''),
            category=cmd_data.get('category', 'general'),
            enabled=cmd_data.get('enabled', True)
        )
        if command.action_type == "internal" and command.action in cls._RETIRED_INTERNAL_ACTIONS:
            return None
        return command

    def load_commands(self):
        """Load commands from JSON file, or create defaults"""
        if self.commands_file.exists():
            try:
                with open(self.commands_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                saved = data.get('commands', [])
                self.commands = [c for c in map(self._command_from_dict, saved) if c]
                saved_version = data.get('defaults_version', 1)
                if saved_version < self.DEFAULTS_VERSION or len(self.commands) != len(saved):
                    self._add_newer_defaults(saved_version)
                    self.save_commands()
                return
            except Exception as e:
                print(f"Error loading voice commands: {e}")

        # Use defaults
        self.commands = self._default_commands()
        self.save_commands()

    def _add_newer_defaults(self, saved_version: int):
        """Append defaults introduced after ``saved_version`` that the
        list doesn't already cover, by phrase or by action."""
        known_phrases = {p.lower() for c in self.commands for p in [c.phrase] + c.aliases}
        known_actions = {(c.action_type, c.action) for c in self.commands}
        for command in self._default_commands():
            if self._DEFAULTS_SINCE.get(command.phrase, 1) <= saved_version:
                continue
            if command.phrase.lower() in known_phrases:
                continue
            if (command.action_type, command.action) in known_actions:
                continue
            self.commands.append(command)
    
    def save_commands(self):
        """Save commands to JSON file"""
        data = {
            'version': '1.0',
            'defaults_version': self.DEFAULTS_VERSION,
            'commands': [
                {
                    'phrase': cmd.phrase,
                    'aliases': cmd.aliases,
                    'action_type': cmd.action_type,
                    'action': cmd.action,
                    'description': cmd.description,
                    'category': cmd.category,
                    'enabled': cmd.enabled
                }
                for cmd in self.commands
            ]
        }
        
        try:
            with open(self.commands_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.error_occurred.emit(f"Failed to save voice commands: {e}")
        # Signal listeners (e.g. a running Vosk recogniser) that the
        # command set changed – they can rebuild their grammar without
        # the user having to stop and restart Always-On.
        try:
            self.commands_changed.emit()
        except Exception:
            pass
    
    def find_matching_command(self, spoken_text: str) -> Optional[VoiceCommand]:
        """The command for what was heard, matched much as SuperVoice in
        Trados does. No guessing at near misses: those used to fire the
        wrong command.

        1. The exact phrase or alias, ignoring case.
        2. Otherwise the longest *multi-word* phrase found inside the
           utterance on whole-word boundaries, after dropping Vosk's
           "[unk]" markers for speech outside the grammar.

        A one-word phrase only ever fires as the whole utterance. The
        listener keeps running while the user dictates with another tool,
        and Vosk turns a dictated sentence into fragments such as
        "[unk] project tm next add [unk]" – which must not fire a "next"
        command.
        """
        heard_words = spoken_text.lower().split()
        heard = " ".join(heard_words)
        if not heard:
            return None
        phrases = [
            (" ".join(p.lower().split()), cmd)
            for cmd in self.commands if cmd.enabled
            for p in [cmd.phrase] + cmd.aliases
        ]
        for phrase, cmd in phrases:
            if phrase == heard:
                return cmd
        padded = " " + " ".join(w for w in heard_words if w != "[unk]") + " "
        best = None
        for phrase, cmd in phrases:
            if (" " in phrase and f" {phrase} " in padded
                    and (best is None or len(phrase) > len(best[0]))):
                best = (phrase, cmd)
        return best[1] if best else None

    def execute_command(self, command: VoiceCommand) -> bool:
        """Execute a voice command. Returns True on success."""
        try:
            if command.action_type == "internal":
                return self._execute_internal(command)
            elif command.action_type == "keystroke":
                return self._execute_keystroke(command)
            elif command.action_type == "ahk_script":
                return self._execute_ahk_script(command)
            elif command.action_type == "ahk_inline":
                return self._execute_ahk_inline(command)
            else:
                self.error_occurred.emit(f"Unknown action type: {command.action_type}")
                return False
        except Exception as e:
            import traceback
            self.error_occurred.emit(f"Error executing '{command.phrase}': {e}\n{traceback.format_exc()}")
            return False
    
    def _execute_internal(self, command: VoiceCommand) -> bool:
        """Execute an internal Python action"""
        handler = self.internal_handlers.get(command.action)
        if handler:
            try:
                result = handler()
                # A handler returns False when it could do nothing (no
                # listener to stop, a missing method). Other results,
                # None included, mean it ran.
                if result is False:
                    if self.main_window and hasattr(self.main_window, 'log'):
                        self.main_window.log(f"⚠️ Voice command did nothing: {command.phrase} → {command.action}")
                    return False
                # Log success to main window if available
                if self.main_window and hasattr(self.main_window, 'log'):
                    self.main_window.log(f"✓ Voice command executed: {command.phrase} → {command.action}")
                self.command_executed.emit(command.phrase, f"✓ {command.description}")
                return True
            except Exception as e:
                import traceback
                error_msg = f"Error in handler for '{command.phrase}': {e}"
                if self.main_window and hasattr(self.main_window, 'log'):
                    self.main_window.log(f"❌ {error_msg}")
                    self.main_window.log(traceback.format_exc())
                self.error_occurred.emit(error_msg)
                return False
        else:
            error_msg = f"No handler for internal action: {command.action}"
            if self.main_window and hasattr(self.main_window, 'log'):
                self.main_window.log(f"❌ {error_msg}")
                self.main_window.log(f"   Available handlers: {list(self.internal_handlers.keys())}")
            self.error_occurred.emit(error_msg)
            return False
    
    def _execute_keystroke(self, command: VoiceCommand) -> bool:
        """Execute a keystroke command.

        On Windows the chord (e.g. ``ctrl+alt+p``) is sent with pynput's
        keyboard controller (SendInput), so it works without AutoHotkey.
        Up to v1.10.373 it went through AutoHotkey and failed with
        "AutoHotkey not found" for anyone who didn't have it.

        On other platforms, delegates to ``CrossPlatformKeySender``.
        """
        if IS_WINDOWS:
            try:
                send_chord(command.action)
            except Exception as e:
                self.error_occurred.emit(f"Couldn't send {command.action!r}: {e}")
                return False
            if self.main_window and hasattr(self.main_window, 'log'):
                self.main_window.log(
                    f"⌨️ Keystroke: Sent '{command.action}' to foreground window")
            self.command_executed.emit(command.phrase, f"✓ {command.description}")
            return True

        # macOS / Linux: use CrossPlatformKeySender
        try:
            from modules.platform_helpers import CrossPlatformKeySender
            sender = CrossPlatformKeySender()
            if sender.is_available:
                sender.send_keystroke(command.action)
                self.command_executed.emit(command.phrase, f"✓ {command.description}")
                return True
        except Exception as e:
            if self.main_window and hasattr(self.main_window, 'log'):
                self.main_window.log(f"[Keystroke] Error: {e}")

        self.error_occurred.emit("Keystroke sending not available")
        return False
    
    def _execute_ahk_script(self, command: VoiceCommand) -> bool:
        """Execute a saved AHK script file (Windows only)"""
        if not IS_WINDOWS:
            self.error_occurred.emit("AHK scripts are only available on Windows")
            return False
        script_path = self.ahk_script_dir / f"{command.action}.ahk"
        if not script_path.exists():
            self.error_occurred.emit(f"AHK script not found: {script_path}")
            return False
        
        try:
            # Find AutoHotkey executable
            ahk_exe = self._find_ahk_executable()
            if not ahk_exe:
                self.error_occurred.emit("AutoHotkey not found. Please install AutoHotkey v2.")
                return False
            
            subprocess.Popen([ahk_exe, str(script_path)],
                           **get_hidden_subprocess_flags())
            self.command_executed.emit(command.phrase, f"✓ {command.description}")
            return True
        except Exception as e:
            self.error_occurred.emit(f"Failed to run AHK script: {e}")
            return False
    
    def _execute_ahk_inline(self, command: VoiceCommand) -> bool:
        """Execute inline AHK code (Windows only)"""
        if not IS_WINDOWS:
            self.error_occurred.emit("Inline AHK code is only available on Windows")
            return False
        return self._run_ahk_code(command.action, command)
    
    @staticmethod
    def _ahk_v1_to_v2(code: str) -> str:
        """Best-effort conversion of AHK v1 command syntax to v2.

        AHK v2 uses function-call syntax: ``Send "keys"`` instead of
        ``Send, keys``.  This handles the most common commands found in
        voice-command inline scripts (Send, Sleep, Click, etc.).
        """
        import re
        lines = code.split('\n')
        converted = []
        for line in lines:
            stripped = line.strip()
            # Send, keys  →  Send "keys"
            m = re.match(r'^(Send(?:Input|Play|Event)?)\s*,\s*(.+)$', stripped, re.IGNORECASE)
            if m:
                converted.append(f'{m.group(1)} "{m.group(2)}"')
                continue
            # Sleep, ms  →  Sleep ms
            m = re.match(r'^(Sleep)\s*,\s*(\d+)$', stripped, re.IGNORECASE)
            if m:
                converted.append(f'{m.group(1)} {m.group(2)}')
                continue
            # Click, ...  →  Click "..."
            m = re.match(r'^(Click)\s*,\s*(.+)$', stripped, re.IGNORECASE)
            if m:
                converted.append(f'{m.group(1)} "{m.group(2)}"')
                continue
            converted.append(line)
        return '\n'.join(converted)

    def _run_ahk_code(self, ahk_code: str, command: VoiceCommand) -> bool:
        """Run arbitrary AHK code"""
        try:
            ahk_exe = self._find_ahk_executable()
            if not ahk_exe:
                self.error_occurred.emit("AutoHotkey not found. Please install AutoHotkey v2.")
                return False

            # Create temporary script
            temp_script = self.ahk_script_dir / "_temp_voice_cmd.ahk"

            # Convert any v1-style commands to v2 syntax
            ahk_code = self._ahk_v1_to_v2(ahk_code)

            # Wrap code in AHK v2 format
            full_script = f"""#Requires AutoHotkey v2.0
#NoTrayIcon
#SingleInstance Force
{ahk_code}
ExitApp
"""
            
            with open(temp_script, 'w', encoding='utf-8') as f:
                f.write(full_script)
            
            # Run script
            subprocess.Popen([ahk_exe, str(temp_script)],
                           **get_hidden_subprocess_flags())
            
            self.command_executed.emit(command.phrase, f"✓ {command.description}")
            return True
            
        except Exception as e:
            self.error_occurred.emit(f"Failed to run AHK code: {e}")
            return False
    
    def _find_ahk_executable(self) -> Optional[str]:
        """Find AutoHotkey v2 executable"""
        # Common installation paths
        possible_paths = [
            r"C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe",
            r"C:\Program Files\AutoHotkey\v2\AutoHotkey32.exe",
            r"C:\Program Files\AutoHotkey\AutoHotkey.exe",
            r"C:\Program Files (x86)\AutoHotkey\AutoHotkey.exe",
        ]
        
        # Check PATH first
        import shutil
        ahk_in_path = shutil.which("AutoHotkey64") or shutil.which("AutoHotkey")
        if ahk_in_path:
            return ahk_in_path
        
        # Check common locations
        for path in possible_paths:
            if os.path.exists(path):
                return path
        
        return None
    
    def process_spoken_text(self, spoken_text: str) -> Tuple[bool, str]:
        """Run the command for what was heard.

        Returns (True, "✓ description") or (True, "✗ Failed: phrase") when
        a command matched, (False, spoken_text) when none did.
        """
        command = self.find_matching_command(spoken_text)
        if command is None:
            self.command_not_found.emit(spoken_text)
            return (False, spoken_text)
        if self.execute_command(command):
            return (True, f"✓ {command.description or command.phrase}")
        return (True, f"✗ Failed: {command.phrase}")
    
    def add_command(self, command: VoiceCommand):
        """Add a new command"""
        self.commands.append(command)
        self.save_commands()
    
    def remove_command(self, phrase: str):
        """Remove a command by phrase"""
        self.commands = [c for c in self.commands if c.phrase != phrase]
        self.save_commands()
    
    def get_commands_by_category(self) -> Dict[str, List[VoiceCommand]]:
        """Get commands organized by category"""
        categories: Dict[str, List[VoiceCommand]] = {}
        for cmd in self.commands:
            if cmd.category not in categories:
                categories[cmd.category] = []
            categories[cmd.category].append(cmd)
        return categories
    
    def export_commands(self, filepath: Path):
        """Export commands to a file"""
        data = {
            'version': '1.0',
            'commands': [
                {
                    'phrase': cmd.phrase,
                    'aliases': cmd.aliases,
                    'action_type': cmd.action_type,
                    'action': cmd.action,
                    'description': cmd.description,
                    'category': cmd.category,
                    'enabled': cmd.enabled
                }
                for cmd in self.commands
            ]
        }
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def import_commands(self, filepath: Path, merge: bool = True):
        """Import commands from a file"""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        imported_commands = [
            c for c in map(self._command_from_dict, data.get('commands', [])) if c
        ]
        
        if merge:
            # Add imported commands, skip duplicates
            existing_phrases = {c.phrase for c in self.commands}
            for cmd in imported_commands:
                if cmd.phrase not in existing_phrases:
                    self.commands.append(cmd)
        else:
            # Replace all commands
            self.commands = imported_commands
        
        self.save_commands()


class ContinuousVoiceListener(QObject):
    """
    Listens for voice commands until stopped.

    1. Monitors microphone audio levels (amplitude VAD).
    2. Records an utterance from speech onset until a short silence.
    3. Vosk, in grammar mode, turns it into a command phrase or "[unk]".
    4. A matched command runs; anything else is reported, never typed.
    """

    # Signals
    listening_started = pyqtSignal()
    listening_stopped = pyqtSignal()
    # The listener thread ended without stop() being called (Vosk failed
    # to load, the microphone went away). Emitted after listening_stopped.
    stopped_unexpectedly = pyqtSignal()
    speech_detected = pyqtSignal(str)  # Raw transcribed text
    command_detected = pyqtSignal(str, str)  # (heard, result)
    no_command_matched = pyqtSignal(str)  # heard, matched no command
    status_update = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    vad_status_changed = pyqtSignal(str)  # "loading", "waiting", "recording", "processing", "stopped"

    def __init__(self, command_manager: VoiceCommandManager,
                 user_data_path=None,
                 mic_device: str = None):
        super().__init__()
        self.command_manager = command_manager
        self.user_data_path = user_data_path  # where the Vosk model lives
        # Saved device *name* from the Voice tab's Microphone dropdown
        # (None ⇒ OS default). Resolved to a sounddevice index inside
        # the audio thread via modules.mic_devices, so we always pick up
        # the *current* device mapping rather than a stale index.
        self.mic_device = mic_device

        # VAD settings
        self.speech_threshold = 0.02  # RMS threshold to detect speech (adjustable)
        self.silence_duration = 0.8  # Seconds of silence before stopping recording
        self.min_speech_duration = 0.3  # Minimum speech duration to process
        self.max_speech_duration = 15.0  # Maximum recording duration
        self.is_listening = False
        self._thread = None
        
    def start(self):
        """Start continuous listening"""
        if self.is_listening:
            return

        self.is_listening = True
        self._thread = _VADListenerThread(self)
        self._thread.transcription_ready.connect(self._on_transcription)
        self._thread.status_update.connect(self.status_update.emit)
        self._thread.error_occurred.connect(self.error_occurred.emit)
        self._thread.vad_status.connect(self.vad_status_changed.emit)
        self._thread.ended.connect(self._on_thread_ended)
        self._thread.start()
        # Hot-reload Vosk's grammar when the user adds / edits / removes
        # / disables a command, so they don't have to stop and restart
        # Always-On to teach the recogniser a new phrase.
        try:
            self.command_manager.commands_changed.connect(
                self._on_commands_changed)
        except Exception:
            pass
        self.listening_started.emit()
    
    def stop(self):
        """Stop continuous listening"""
        self.is_listening = False
        try:
            self.command_manager.commands_changed.disconnect(
                self._on_commands_changed)
        except (TypeError, RuntimeError):
            # Not connected (e.g. listener never reached start) – ignore.
            pass
        if self._thread:
            self._thread.stop()
            self._thread = None
        self.listening_stopped.emit()

    def _on_thread_ended(self, thread):
        """The listener thread's run loop has returned (delivered on the
        GUI thread). After stop() or a restart this is an old thread and
        is ignored. If it is still the current one, it died on its own,
        and the listener must stop claiming to listen: the Voice tab
        reads ``is_listening`` to paint its status, and the next toggle
        would otherwise "stop" a listener that isn't running."""
        if thread is not self._thread or not self.is_listening:
            return
        self.stop()
        self.stopped_unexpectedly.emit()

    def _on_commands_changed(self):
        """Slot for ``VoiceCommandManager.commands_changed``.

        Sets a flag on the worker thread so it picks up the new grammar
        between transcriptions. No-op when the listener isn't running."""
        if not self.is_listening:
            return
        if self._thread is None:
            return
        try:
            self._thread._needs_grammar_rebuild = True
        except Exception:
            pass

    def set_sensitivity(self, level: str):
        """
        Set microphone sensitivity level.
        - "low": Requires loud speech (noisy environment)
        - "medium": Normal sensitivity
        - "high": Picks up quiet speech (quiet environment)
        """
        thresholds = {
            "low": 0.04,
            "medium": 0.02,
            "high": 0.01
        }
        self.speech_threshold = thresholds.get(level, 0.02)
    
    def _on_transcription(self, text: str):
        """Handle transcribed speech"""
        self.speech_detected.emit(text)
        
        # Try to match as command
        was_command, result = self.command_manager.process_spoken_text(text)
        
        if was_command:
            self.command_detected.emit(text, result)
        else:
            self.no_command_matched.emit(text)


class _VADListenerThread(QObject):
    """
    Voice Activity Detection listener thread.
    Uses amplitude-based VAD to detect speech start/end.
    """
    
    transcription_ready = pyqtSignal(str)
    status_update = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    vad_status = pyqtSignal(str)  # "waiting", "recording", "processing"
    ended = pyqtSignal(object)  # self, when _run returns for any reason

    def __init__(self, listener: ContinuousVoiceListener):
        super().__init__()
        self.listener = listener
        self._running = False
        self._thread = None
        self._model = None  # Vosk KaldiRecognizer
        # Set externally (cross-thread) when the user mutates the
        # command list. The transcription worker checks this between
        # clips and rebuilds the Vosk grammar without restarting the
        # whole listener. Safe to flip from any thread because Python
        # bool assignment is atomic under the GIL.
        self._needs_grammar_rebuild = False
        # Stash for the heavyweight VoskModel so we don't have to
        # reload it from disk each time the grammar changes – we just
        # build a new lightweight KaldiRecognizer from the same model.
        self._vosk_model_obj = None
    
    def start(self):
        """Start the listener thread"""
        import threading
        
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
    
    def stop(self):
        """Stop the listener thread"""
        self._running = False
    
    def _run(self):
        """Main VAD listening loop"""
        import queue as _queue
        import threading as _threading
        try:
            import sounddevice as sd
            import numpy as np
            import time

            sample_rate = 16000
            chunk_samples = int(0.1 * sample_rate)  # 100 ms chunks for VAD

            speech_threshold = self.listener.speech_threshold
            silence_duration = self.listener.silence_duration
            min_speech_duration = self.listener.min_speech_duration
            max_speech_duration = self.listener.max_speech_duration

            self.status_update.emit("🎤 Loading Vosk recognizer (commands only, free)...")
            self.vad_status.emit("loading")
            try:
                from vosk import Model as VoskModel, KaldiRecognizer
            except ImportError:
                self.error_occurred.emit(
                    "Vosk is not installed.\n\n"
                    "Re-install Supervertaler:\n"
                    "  pip install --upgrade supervertaler"
                )
                self._running = False
                return

            # Commands are English, so the model is always the English
            # one, whatever the project's languages (as in Trados).
            from modules.vosk_model_manager import (
                DEFAULT_MODEL_KEY, get_model_path, download_and_extract,
            )
            model_key = DEFAULT_MODEL_KEY

            user_data = self.listener.user_data_path
            if not user_data:
                self.error_occurred.emit(
                    "Vosk needs a user-data path to install the model into. "
                    "This is a configuration bug – please report it."
                )
                self._running = False
                return

            model_dir = get_model_path(model_key, user_data)
            if model_dir is None:
                # First-time fetch. Stream the ZIP, extract, then load.
                self.status_update.emit(
                    f"📥 Downloading Vosk model '{model_key}' (~40 MB, one-time)..."
                )

                def _vosk_progress(done, total):
                    if total > 0:
                        mb_d = done // (1024 * 1024)
                        mb_t = total // (1024 * 1024)
                        self.status_update.emit(
                            f"📥 Vosk model: {mb_d} / {mb_t} MB"
                        )

                model_dir = download_and_extract(
                    model_key, user_data, progress_callback=_vosk_progress)
                if model_dir is None:
                    self.error_occurred.emit(
                        "Failed to download the Vosk model. Check your internet "
                        "connection and try again."
                    )
                    self._running = False
                    return

            # Build a phrase grammar from the user's active commands so
            # Vosk biases its recogniser toward those phrases. Free-form
            # speech that doesn't match anything in the grammar still
            # gets classified, just much faster (and "[unk]" returned).
            command_phrases = self._collect_vosk_grammar()

            vmodel = VoskModel(str(model_dir))
            # KaldiRecognizer accepts (model, sample_rate, grammar_json).
            # Grammar is a JSON-encoded list of permitted phrases; we
            # always include "[unk]" as the catch-all for non-command
            # speech so out-of-grammar utterances don't error out.
            import json as _json
            grammar = _json.dumps(command_phrases + ["[unk]"])
            self._model = KaldiRecognizer(vmodel, 16000, grammar)
            # Stash the underlying VoskModel so it isn't GC'd before
            # the recognizer is done with it.
            self._vosk_model_obj = vmodel

            self.status_update.emit("🎤 Always-on listening active (waiting for speech...)")
            self.vad_status.emit("waiting")

            # Captured audio clips are handed off here; the transcription
            # worker consumes them so the audio callback never blocks.
            audio_queue = _queue.Queue()

            audio_buffer = []
            is_recording = False
            silence_start = None
            speech_start = None

            def audio_callback(indata, frames, time_info, status):
                """Lightweight VAD callback – never calls transcription directly."""
                nonlocal audio_buffer, is_recording, silence_start, speech_start

                if not self._running:
                    return

                rms = np.sqrt(np.mean(indata**2))
                is_speech = rms > speech_threshold

                if is_speech:
                    if not is_recording:
                        is_recording = True
                        speech_start = time.time()
                        audio_buffer = []
                        self.vad_status.emit("recording")
                        self.status_update.emit("🔴 Recording...")
                    silence_start = None
                    audio_buffer.append(indata.copy())

                    if time.time() - speech_start > max_speech_duration:
                        is_recording = False
                        audio_queue.put(list(audio_buffer))
                        audio_buffer = []
                        self.vad_status.emit("waiting")

                else:
                    if is_recording:
                        audio_buffer.append(indata.copy())
                        if silence_start is None:
                            silence_start = time.time()
                        if time.time() - silence_start > silence_duration:
                            speech_duration = time.time() - speech_start
                            is_recording = False
                            if speech_duration >= min_speech_duration:
                                audio_queue.put(list(audio_buffer))
                            else:
                                self.status_update.emit("🎤 (too short, ignored)")
                            audio_buffer = []
                            silence_start = None
                            self.vad_status.emit("waiting")
                            self.status_update.emit("🎤 Listening...")

            def transcription_worker():
                """Dedicated thread: transcribes queued clips without touching the stream."""
                while self._running or not audio_queue.empty():
                    # Rebuild Vosk grammar between clips if the user
                    # added / edited / removed a command since the last
                    # transcription. Cheap (<100 ms) and only runs when
                    # the flag has been flipped on by ContinuousVoice
                    # Listener._on_commands_changed.
                    if self._needs_grammar_rebuild:
                        self._maybe_rebuild_vosk_grammar()
                    try:
                        captured = audio_queue.get(timeout=0.5)
                    except _queue.Empty:
                        continue
                    self._process_audio(captured, sample_rate)

            worker = _threading.Thread(target=transcription_worker, daemon=True)
            worker.start()

            # Resolve the user's saved mic-device name to a current
            # sounddevice index. ``None`` → OS default. Done here (in
            # the audio thread, just before opening the stream) so we
            # honour the live device mapping – mics plugged in after
            # Workbench launched are picked up without a restart.
            #
            # extra_settings: for WASAPI devices, enable auto sample-
            # rate conversion. Without it, WASAPI shared mode rejects
            # our 16 kHz request because it forces the OS mixer rate
            # (typically 48 kHz). MME and the default-fallback don't
            # need this.
            try:
                from modules.mic_devices import (
                    resolve_device_index, wasapi_autoconvert_settings,
                )
                device_idx = resolve_device_index(self.mic_device)
                extra = wasapi_autoconvert_settings(device_idx)
            except Exception:
                device_idx = None
                extra = None

            # Keep the InputStream open for the full session so the OS mic
            # indicator never flickers – transcription now happens off-thread.
            with sd.InputStream(
                samplerate=sample_rate,
                channels=1,
                dtype='float32',
                blocksize=chunk_samples,
                callback=audio_callback,
                device=device_idx,
                extra_settings=extra,
            ):
                while self._running:
                    time.sleep(0.1)

            worker.join(timeout=5.0)

        except Exception as e:
            import traceback
            self.error_occurred.emit(f"Listener error: {e}\n{traceback.format_exc()}")
        finally:
            self.vad_status.emit("stopped")
            self.status_update.emit("🔇 Stopped listening")
            self.ended.emit(self)

    def _process_audio(self, audio_buffer: list, sample_rate: int):
        """Process recorded audio - save to file and transcribe"""
        try:
            import numpy as np
            import tempfile
            import wave
            import os
            
            self.vad_status.emit("processing")
            self.status_update.emit("⏳ Transcribing...")
            
            # Concatenate audio chunks
            if not audio_buffer:
                return
            
            audio_data = np.concatenate(audio_buffer, axis=0)
            
            # Convert to int16
            audio_int16 = np.int16(audio_data * 32767)
            
            # Save to temp file
            temp_dir = tempfile.gettempdir()
            temp_path = os.path.join(temp_dir, f"sv_vad_{os.getpid()}.wav")
            
            with wave.open(temp_path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(audio_int16.tobytes())
            
            text = self._transcribe_with_vosk(temp_path)
            
            # Clean up
            try:
                os.unlink(temp_path)
            except:
                pass
            
            # Emit result
            if text:
                self.transcription_ready.emit(text)
                
        except Exception as e:
            import traceback
            self.error_occurred.emit(f"Processing error: {e}\n{traceback.format_exc()}")

    def _maybe_rebuild_vosk_grammar(self):
        """Replace the active KaldiRecognizer with one built from the
        current command list, when the ``_needs_grammar_rebuild`` flag
        has been set by an external mutation.

        Runs only when (a) the flag is set and (b) we have a cached
        VoskModel object to attach the new recognizer to. Always clears the flag, even on failure, so a
        broken rebuild doesn't loop forever – the next legitimate
        change will retry.
        """
        # Always clear the flag up-front, before any heavy work, so a
        # second mutation arriving mid-rebuild still queues a third
        # rebuild deterministically.
        self._needs_grammar_rebuild = False

        if self._vosk_model_obj is None:
            return

        try:
            from vosk import KaldiRecognizer
            import json as _json

            phrases = self._collect_vosk_grammar()
            grammar = _json.dumps(phrases + ["[unk]"])
            new_recognizer = KaldiRecognizer(self._vosk_model_obj, 16000, grammar)
            # Atomic swap of the recognizer reference. Python's GIL
            # guarantees this single-name reassignment is observed as
            # all-or-nothing by the audio-process thread.
            self._model = new_recognizer
            self.status_update.emit(
                f"🔄 Vosk grammar refreshed ({len(phrases)} phrase"
                + ("s" if len(phrases) != 1 else "") + ")"
            )
        except Exception as e:
            self.error_occurred.emit(f"Vosk grammar rebuild failed: {e}")

    def _collect_vosk_grammar(self) -> List[str]:
        """Return the list of phrases Vosk should bias its recogniser
        toward – i.e. the user's currently-active voice commands.

        Vosk's grammar mode dramatically improves both accuracy and speed
        when the recognition target is a fixed-vocabulary command set.
        Each command's primary phrase plus all its aliases is included.
        We always append ``"[unk]"`` outside this method so non-command
        speech can be classified as unknown rather than misrecognised as
        a command.
        """
        try:
            phrases: List[str] = []
            for cmd in (self.listener.command_manager.commands or []):
                if not getattr(cmd, "enabled", True):
                    continue
                if cmd.phrase:
                    phrases.append(cmd.phrase.lower().strip())
                for alias in (cmd.aliases or []):
                    if alias:
                        phrases.append(alias.lower().strip())
            # Deduplicate while preserving order.
            seen = set()
            out = []
            for p in phrases:
                if p and p not in seen:
                    seen.add(p)
                    out.append(p)
            return out or ["yes", "no"]  # always provide *something*
        except Exception:
            return ["yes", "no"]

    def _transcribe_with_vosk(self, audio_path: str) -> str:
        """Transcribe a recorded utterance using a Vosk KaldiRecognizer.

        The recognizer was built in :meth:`_run` with a JSON grammar
        derived from the user's active command phrases, so the result
        is biased toward known commands and out-of-grammar speech is
        returned as ``[unk]`` (which we filter out).

        Vosk works on raw 16-bit PCM audio at 16 kHz mono, which matches
        the WAV file we already wrote to disk. We feed the bytes in
        chunks rather than loading the whole file at once – this scales
        to longer utterances without spiking memory.
        """
        try:
            import wave
            recognizer = self._model
            if recognizer is None:
                return ""
            recognizer.Reset()  # clear any state from the previous utterance

            with wave.open(audio_path, "rb") as wf:
                # The recognizer expects mono 16-bit PCM; sounddevice gives
                # us float32, but our _process_audio step already converted
                # to int16 before writing the WAV.
                while True:
                    data = wf.readframes(4000)
                    if len(data) == 0:
                        break
                    recognizer.AcceptWaveform(data)

            import json as _json
            result = _json.loads(recognizer.FinalResult())
            text = (result.get("text") or "").strip()
            # Vosk emits "[unk]" (sometimes wrapped) when the speech didn't
            # match anything in our grammar. Treat that as silence.
            if text in ("[unk]", "unk", ""):
                return ""
            return text
        except Exception as e:
            self.error_occurred.emit(f"Vosk transcription error: {e}")
            return ""

