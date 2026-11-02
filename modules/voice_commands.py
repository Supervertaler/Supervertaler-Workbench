"""
Voice Commands Module for Supervertaler
Talon-style voice command system with 3 tiers:
- Tier 1: In-app commands (Python/PyQt6)
- Tier 2: System commands (AutoHotkey scripts)
- Tier 3: Dictation fallback (insert as text)
"""

import json
import os
import re
import sys
import subprocess
from pathlib import Path
from typing import Optional, Dict, List, Callable, Tuple
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QWidget
from modules.shortcut_display import format_shortcut_for_display
from modules.platform_helpers import IS_WINDOWS, get_hidden_subprocess_flags, hide_subprocess_console_windows


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
    
    def matches(self, spoken_text: str, threshold: float = 0.85) -> Tuple[bool, float]:
        """
        Check if spoken text matches this command.
        Returns (is_match, confidence_score)
        """
        spoken_lower = spoken_text.lower().strip()
        
        # Check exact matches first
        all_phrases = [self.phrase.lower()] + [a.lower() for a in self.aliases]
        for phrase in all_phrases:
            if spoken_lower == phrase:
                return (True, 1.0)
        
        # Check fuzzy matches
        best_score = 0.0
        for phrase in all_phrases:
            # Use SequenceMatcher for fuzzy matching
            score = SequenceMatcher(None, spoken_lower, phrase).ratio()
            best_score = max(best_score, score)
            
            # Also check if spoken text contains the phrase
            if phrase in spoken_lower or spoken_lower in phrase:
                # Boost score for partial matches
                length_ratio = min(len(phrase), len(spoken_lower)) / max(len(phrase), len(spoken_lower))
                best_score = max(best_score, 0.9 * length_ratio)
        
        return (best_score >= threshold, best_score)


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
    
    # Default commands
    DEFAULT_COMMANDS = [
        # Navigation
        VoiceCommand("next segment", ["next", "down"], "internal", "navigate_next", 
                    "Move to next segment", "navigation"),
        VoiceCommand("previous segment", ["previous", "back", "up"], "internal", "navigate_previous",
                    "Move to previous segment", "navigation"),
        VoiceCommand("first segment", ["go to start", "beginning"], "internal", "navigate_first",
                    "Jump to first segment", "navigation"),
        VoiceCommand("last segment", ["go to end", "end"], "internal", "navigate_last",
                    "Jump to last segment", "navigation"),
        
        # Segment actions
        VoiceCommand("confirm", ["confirm segment", "done", "okay"], "internal", "confirm_segment",
                    "Confirm current segment", "editing"),
        VoiceCommand("copy source", ["copy from source", "source to target"], "internal", "copy_source_to_target",
                    "Copy source text to target", "editing"),
        VoiceCommand("clear target", ["clear", "delete target"], "internal", "clear_target",
                    "Clear target text", "editing"),
        VoiceCommand("undo", [], "keystroke", "ctrl+z",
                    "Undo last action", "editing"),
        VoiceCommand("redo", [], "keystroke", "ctrl+y",
                    "Redo last action", "editing"),
        
        # Translation
        VoiceCommand("translate", ["translate segment", "translate this"], "internal", "translate_segment",
                    "AI translate current segment", "translation"),
        VoiceCommand("translate all", ["batch translate"], "internal", "batch_translate",
                    "Translate all segments", "translation"),
        
        # Lookup & Search
        VoiceCommand("lookup", ["super lookup", "search"], "internal", "open_superlookup",
                    f"Open Superlookup ({format_shortcut_for_display('Ctrl+K')})", "lookup"),
        VoiceCommand("concordance", ["search memory", "search TM"], "internal", "concordance_search",
                    "Open concordance search", "lookup"),
        
        # File operations
        VoiceCommand("save project", ["save"], "keystroke", "ctrl+s",
                    "Save current project", "file"),
        VoiceCommand("open project", ["open"], "keystroke", "ctrl+o",
                    "Open project", "file"),
        
        # View
        VoiceCommand("show log", ["open log", "log tab"], "internal", "show_log",
                    "Show log panel", "view"),
        VoiceCommand("show editor", ["editor tab", "go to editor"], "internal", "show_editor",
                    "Show editor panel", "view"),
        
        # Dictation control
        VoiceCommand("start dictation", ["dictate", "voice input"], "internal", "start_dictation",
                    "Start voice dictation mode", "dictation"),
        VoiceCommand("stop listening", ["stop", "pause"], "internal", "stop_listening",
                    "Stop voice recognition", "dictation"),
        
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
    
    def __init__(self, user_data_path: Path, main_window=None):
        super().__init__()
        self.user_data_path = user_data_path
        self.main_window = main_window
        self.commands: List[VoiceCommand] = []
        self.commands_file = user_data_path / "workbench" / "settings" / "voice_commands.json"
        self.ahk_script_dir = user_data_path / "workbench" / "voice_scripts"
        self.match_threshold = 0.85  # Minimum similarity for fuzzy matching

        # Internal action handlers (mapped to main_window methods)
        self.internal_handlers: Dict[str, Callable] = {}

        # v1.10.197: deferred-keystroke mode for the push-to-talk-for-
        # commands flow. While ``_defer_keystrokes`` is True, any
        # keystroke / AHK command goes onto ``_deferred_queue`` instead
        # of being sent immediately. When the flag flips back to False
        # (via ``set_defer_keystrokes(False)``) the queue is drained.
        # Internal-action commands (which call Python methods directly
        # rather than synthesising keystrokes) execute immediately
        # regardless of this flag — they don't suffer from the
        # "user is still holding Ctrl+Alt" interference that keystroke
        # commands hit during a PTT hold.
        self._defer_keystrokes: bool = False
        self._deferred_queue: List = []
        
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
            
            # Dictation
            "start_dictation": lambda: main_window.start_voice_dictation() if hasattr(main_window, 'start_voice_dictation') else self._log_missing('start_voice_dictation'),
            "stop_listening": lambda: self._stop_voice_recognition(),
        }

    def _log_missing(self, method_name: str):
        """Log when a method is missing from main_window"""
        print(f"⚠️ Voice command: Method '{method_name}' not found on main window")
        if self.main_window and hasattr(self.main_window, 'log'):
            self.main_window.log(f"⚠️ Voice command: Method '{method_name}' not found")

    def _show_tab(self, main_window, tab_name: str):
        """Helper to switch to a specific tab"""
        if hasattr(main_window, 'main_tabs'):
            for i in range(main_window.main_tabs.count()):
                if tab_name.lower() in main_window.main_tabs.tabText(i).lower():
                    main_window.main_tabs.setCurrentIndex(i)
                    return
    
    def _stop_voice_recognition(self):
        """Stop the voice recognition system"""
        if self.main_window and hasattr(self.main_window, 'voice_command_listener'):
            listener = self.main_window.voice_command_listener
            if listener and hasattr(listener, 'stop'):
                listener.stop()
    
    def load_commands(self):
        """Load commands from JSON file, or create defaults"""
        if self.commands_file.exists():
            try:
                with open(self.commands_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                self.commands = []
                self.match_threshold = data.get('match_threshold', 0.85)
                
                for cmd_data in data.get('commands', []):
                    self.commands.append(VoiceCommand(
                        phrase=cmd_data['phrase'],
                        aliases=cmd_data.get('aliases', []),
                        action_type=cmd_data.get('action_type', 'internal'),
                        action=cmd_data.get('action', ''),
                        description=cmd_data.get('description', ''),
                        category=cmd_data.get('category', 'general'),
                        enabled=cmd_data.get('enabled', True)
                    ))
                
                return
            except Exception as e:
                print(f"Error loading voice commands: {e}")
        
        # Use defaults
        self.commands = self.DEFAULT_COMMANDS.copy()
        self.save_commands()
    
    def save_commands(self):
        """Save commands to JSON file"""
        data = {
            'version': '1.0',
            'match_threshold': self.match_threshold,
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
    
    def find_matching_command(self, spoken_text: str) -> Optional[Tuple[VoiceCommand, float]]:
        """
        Find the best matching command for spoken text.
        Returns (command, confidence) or None if no match.
        """
        spoken_text = spoken_text.strip()
        if not spoken_text:
            return None
        
        best_match = None
        best_score = 0.0
        
        for cmd in self.commands:
            if not cmd.enabled:
                continue
            
            is_match, score = cmd.matches(spoken_text, self.match_threshold)
            if is_match and score > best_score:
                best_match = cmd
                best_score = score
        
        if best_match:
            return (best_match, best_score)
        return None
    
    # ── v1.10.197: deferred-keystroke control (for command PTT) ──

    def set_defer_keystrokes(self, on: bool) -> None:
        """Toggle the keystroke-deferral mode.

        While ``on=True``, keystroke / AHK voice commands are queued
        rather than executed — used by the command push-to-talk path
        so the synthetic Ctrl+A doesn't collide with the user's still-
        held Ctrl+Alt+V modifier keys. When called with ``on=False``,
        the queue is drained immediately (callers wanting a delay
        should schedule the call via QTimer.singleShot).

        Internal-action commands (Python method calls) are unaffected
        by this flag — they execute immediately in both modes.
        """
        was_on = self._defer_keystrokes
        self._defer_keystrokes = bool(on)
        if was_on and not self._defer_keystrokes:
            self._drain_deferred_queue()

    def _drain_deferred_queue(self) -> None:
        """Execute everything queued during a deferral window, in order."""
        queue = self._deferred_queue
        self._deferred_queue = []
        for command in queue:
            try:
                self._execute_command_real(command)
            except Exception as e:
                if self.main_window and hasattr(self.main_window, 'log'):
                    self.main_window.log(
                        f"❌ Deferred voice command failed ({command.phrase!r}): {e}"
                    )

    def execute_command(self, command: VoiceCommand) -> bool:
        """Execute a voice command. Returns True on success.

        v1.10.197: when ``_defer_keystrokes`` is on, keystroke / AHK
        commands are queued for later execution and this returns True
        (the queueing was successful). Internal commands run
        immediately regardless of the flag — they don't synthesise
        keystrokes so they're safe to fire even with modifier keys
        held physically by the user.
        """
        if self._defer_keystrokes and command.action_type in (
            "keystroke", "ahk_inline", "ahk_script"
        ):
            self._deferred_queue.append(command)
            if self.main_window and hasattr(self.main_window, 'log'):
                self.main_window.log(
                    f"🎙️ Voice command queued for after PTT release: "
                    f"{command.phrase!r}"
                )
            return True
        return self._execute_command_real(command)

    def _execute_command_real(self, command: VoiceCommand) -> bool:
        """Actual execution path. Split from :meth:`execute_command` in
        v1.10.197 so deferred commands can re-enter the same logic
        without going back through the defer-check."""
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

        On Windows, converts the keystroke string (e.g. ``ctrl+alt+p``) to
        AHK ``Send`` syntax and runs it via ``_run_ahk_code`` – the same
        proven path used by ``ahk_inline`` voice commands.

        On other platforms, delegates to ``CrossPlatformKeySender``.
        """
        if IS_WINDOWS:
            ahk_keys = self._convert_to_ahk_keys(command.action)
            ahk_code = f'SendInput "{ahk_keys}"'
            if self.main_window and hasattr(self.main_window, 'log'):
                self.main_window.log(
                    f"⌨️ Keystroke: Sending '{command.action}' to foreground window")
            return self._run_ahk_code(ahk_code, command)

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
    
    def _convert_to_ahk_keys(self, keystroke: str) -> str:
        """Convert keystroke string to AHK Send format"""
        # Map modifier names to AHK symbols
        modifiers = {
            'ctrl': '^',
            'control': '^',
            'alt': '!',
            'shift': '+',
            'win': '#',
            'windows': '#'
        }
        
        # Special key names
        special_keys = {
            'enter': '{Enter}',
            'return': '{Enter}',
            'tab': '{Tab}',
            'escape': '{Esc}',
            'esc': '{Esc}',
            'space': '{Space}',
            'backspace': '{Backspace}',
            'delete': '{Delete}',
            'del': '{Delete}',
            'insert': '{Insert}',
            'ins': '{Insert}',
            'home': '{Home}',
            'end': '{End}',
            'pageup': '{PgUp}',
            'pgup': '{PgUp}',
            'pagedown': '{PgDn}',
            'pgdn': '{PgDn}',
            'up': '{Up}',
            'down': '{Down}',
            'left': '{Left}',
            'right': '{Right}',
            'f1': '{F1}', 'f2': '{F2}', 'f3': '{F3}', 'f4': '{F4}',
            'f5': '{F5}', 'f6': '{F6}', 'f7': '{F7}', 'f8': '{F8}',
            'f9': '{F9}', 'f10': '{F10}', 'f11': '{F11}', 'f12': '{F12}',
        }
        
        parts = keystroke.lower().replace(' ', '').split('+')
        result = ''
        
        for part in parts:
            if part in modifiers:
                result += modifiers[part]
            elif part in special_keys:
                result += special_keys[part]
            else:
                # Regular key
                result += part
        
        return result
    
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
        """
        Process spoken text - try to match command, return success status and message.
        Returns (was_command, message_or_text)
        - If command matched: (True, "Command executed: ...")
        - If no match: (False, original_spoken_text) for dictation fallback
        """
        match_result = self.find_matching_command(spoken_text)
        
        if match_result:
            command, confidence = match_result
            success = self.execute_command(command)
            if success:
                return (True, f"✓ {command.phrase} ({confidence:.0%})")
            else:
                return (True, f"✗ Failed: {command.phrase}")
        
        # No command matched - return text for dictation
        self.command_not_found.emit(spoken_text)
        return (False, spoken_text)
    
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
            'match_threshold': self.match_threshold,
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
        
        imported_commands = []
        for cmd_data in data.get('commands', []):
            imported_commands.append(VoiceCommand(
                phrase=cmd_data['phrase'],
                aliases=cmd_data.get('aliases', []),
                action_type=cmd_data.get('action_type', 'internal'),
                action=cmd_data.get('action', ''),
                description=cmd_data.get('description', ''),
                category=cmd_data.get('category', 'general'),
                enabled=cmd_data.get('enabled', True)
            ))
        
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
    Continuous voice listening with Voice Activity Detection (VAD).
    
    How it works:
    1. Continuously monitors microphone audio levels
    2. When speech is detected (audio above threshold), starts recording
    3. When silence is detected (audio below threshold for X ms), stops recording
    4. Sends recording to Whisper for transcription
    5. Processes result (command or dictation)
    6. Repeats
    
    This eliminates the need to press F9 twice - just speak and it listens.
    """
    
    # Signals
    listening_started = pyqtSignal()
    listening_stopped = pyqtSignal()
    # The listener thread ended without stop() being called (Vosk failed
    # to load, the microphone went away). Emitted after listening_stopped.
    stopped_unexpectedly = pyqtSignal()
    speech_detected = pyqtSignal(str)  # Raw transcribed text
    command_detected = pyqtSignal(str, str)  # (phrase, result)
    text_for_dictation = pyqtSignal(str)  # Text that didn't match any command
    status_update = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    vad_status_changed = pyqtSignal(str)  # "listening", "recording", "processing"
    
    # Recognition engine identifiers. ``'vosk'`` is the default and is
    # purpose-built for command recognition (fixed-vocabulary, ~30 ms
    # latency, free, no cloud round-trip). ``'faster_whisper'`` covers
    # the running-text dictation case. ``'api'`` uses OpenAI Whisper's
    # cloud endpoint for users who prefer it.
    ENGINE_VOSK = "vosk"
    ENGINE_FASTER_WHISPER = "faster_whisper"
    ENGINE_API = "api"

    def __init__(self, command_manager: VoiceCommandManager,
                 model_name: str = "base",
                 language: str = "auto",
                 engine: str = ENGINE_VOSK,
                 api_key: str = None,
                 user_data_path=None,
                 vosk_model_key: str = None,
                 mic_device: str = None,
                 initial_prompt: str = None,
                 replacements: list = None):
        super().__init__()
        self.command_manager = command_manager
        self.model_name = model_name
        self.language = None if language == "auto" else language
        # Vocabulary biasing (v1.10.26). Passed straight through to
        # whichever Whisper backend the dictation path picks (cloud
        # API or local faster-whisper). Both surfaces accept it; Vosk
        # ignores it because Vosk runs in grammar mode and only ever
        # transcribes phrases from its pre-built command list. See
        # modules.voice_vocabulary for the builder + post-processor.
        self.initial_prompt = initial_prompt
        self.replacements = replacements
        # Backward-compat: legacy callers may still pass ``use_api=True``;
        # we map that to the API engine if ``engine`` was left at default.
        # New callers should pass the engine string directly.
        if engine not in (self.ENGINE_VOSK, self.ENGINE_FASTER_WHISPER, self.ENGINE_API):
            engine = self.ENGINE_VOSK
        self.engine = engine
        self.api_key = api_key
        self.user_data_path = user_data_path  # required for Vosk model storage
        self.vosk_model_key = vosk_model_key  # auto-resolved from language if None
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
        # Pause flag, set by pause()/resume(). When true, the audio
        # callback drops incoming chunks instead of forwarding them to
        # the recognizer. Used to mute the always-on listener while
        # push-to-talk dictation has the mic.
        self._paused = False
        self._thread = None
        self._whisper_model = None  # Cached Whisper model

    @property
    def use_api(self) -> bool:
        """Backward-compat shim for older code that asks ``use_api``."""
        return self.engine == self.ENGINE_API
        
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
        # Always-On to teach the recogniser a new phrase. The handler is
        # a no-op for non-Vosk engines.
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
        between transcriptions. No-op outside the Vosk engine and when
        the listener isn't actively running."""
        if not self.is_listening:
            return
        if self.engine != self.ENGINE_VOSK:
            return
        if self._thread is None:
            return
        try:
            self._thread._needs_grammar_rebuild = True
        except Exception:
            pass

    def pause(self):
        """Mute the listener temporarily without tearing down the thread.

        Used when push-to-talk dictation needs sole access to the mic
        for a few seconds. Audio chunks captured during the pause are
        discarded by the callback rather than queued for transcription,
        so a) the user's dictation isn't interpreted as a half-heard
        command, and b) we don't waste CPU running Vosk on Whisper-
        bound speech.
        """
        self._paused = True

    def resume(self):
        """Re-enable the listener after a :meth:`pause`."""
        self._paused = False

    @property
    def is_paused(self) -> bool:
        return self._paused
    
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
            # Pass to dictation
            self.text_for_dictation.emit(text)


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
        self._model = None  # Cached whisper / Vosk recognizer
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

            engine = self.listener.engine

            if engine == ContinuousVoiceListener.ENGINE_API and self.listener.api_key:
                self.status_update.emit("🎤 Using OpenAI Whisper API (fast & accurate)")
                self._model = None

            elif engine == ContinuousVoiceListener.ENGINE_VOSK:
                self.status_update.emit("🎤 Loading Vosk recognizer (commands-only, free)...")
                self.vad_status.emit("loading")
                try:
                    from vosk import Model as VoskModel, KaldiRecognizer
                except ImportError:
                    self.error_occurred.emit(
                        "Vosk is not installed.\n\n"
                        "Re-install Supervertaler:\n"
                        "  pip install --upgrade supervertaler\n\n"
                        "Or switch the Voice engine to 'OpenAI Whisper API' / "
                        "'faster-whisper' in the meantime."
                    )
                    self._running = False
                    return

                # Resolve which Vosk model to use. If the caller didn't pin a
                # specific model, pick one based on the listener's language hint.
                from modules.vosk_model_manager import (
                    DEFAULT_MODEL_KEY, get_model_path,
                    download_and_extract, pick_model_for_language,
                )
                model_key = (self.listener.vosk_model_key
                             or pick_model_for_language(self.listener.language)
                             or DEFAULT_MODEL_KEY)

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

            else:
                # faster_whisper path (default for running-text dictation).
                self.status_update.emit("🎤 Loading faster-whisper model...")
                self.vad_status.emit("loading")
                try:
                    from faster_whisper import WhisperModel
                except ImportError:
                    self.error_occurred.emit(
                        "faster-whisper is not installed.\n\n"
                        "Re-install Supervertaler:\n"
                        "  pip install --upgrade supervertaler\n\n"
                        "Or switch the Voice engine to 'Vosk' or "
                        "'OpenAI Whisper API'."
                    )
                    self._running = False
                    return
                self._model = WhisperModel(
                    self.listener.model_name,
                    device="cpu",
                    compute_type="int8",
                )

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

                # Paused: drop incoming audio so push-to-talk dictation has
                # the mic to itself. Reset the in-flight recording state so
                # we don't resume mid-utterance with a stale buffer.
                if self.listener._paused:
                    if is_recording:
                        is_recording = False
                        audio_buffer = []
                        silence_start = None
                        self.vad_status.emit("waiting")
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
            
            # Transcribe using whichever engine is active.
            engine = self.listener.engine
            if engine == ContinuousVoiceListener.ENGINE_API and self.listener.api_key:
                text = self._transcribe_with_api(temp_path)
            elif engine == ContinuousVoiceListener.ENGINE_VOSK:
                text = self._transcribe_with_vosk(temp_path)
            else:
                text = self._transcribe_with_local(temp_path)
            
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

    def _transcribe_with_api(self, audio_path: str) -> str:
        """Transcribe using OpenAI Whisper API - much more accurate"""
        try:
            from openai import OpenAI
            
            client = OpenAI(api_key=self.listener.api_key)
            
            with open(audio_path, "rb") as audio_file:
                # Use whisper-1 model (OpenAI's hosted Whisper)
                kwargs = {"model": "whisper-1", "file": audio_file}

                # Add language hint if specified
                if self.listener.language:
                    kwargs["language"] = self.listener.language

                # v1.10.26: vocabulary biasing. Passed as the
                # ``prompt`` field; biases the decoder toward known
                # brand / technical terms.
                if getattr(self.listener, 'initial_prompt', None):
                    kwargs["prompt"] = self.listener.initial_prompt

                response = client.audio.transcriptions.create(**kwargs)

            return self._post_process_transcript(response.text.strip())

        except Exception as e:
            self.error_occurred.emit(f"OpenAI API error: {e}")
            return ""

    def _transcribe_with_local(self, audio_path: str) -> str:
        """Transcribe using local faster-whisper model.

        Returns a generator of segments + an info object. We just
        concatenate the segment texts to get the full transcription.
        Wrapped in ``hide_subprocess_console_windows`` defensively – the
        CTranslate2 backend doesn't shell out to ffmpeg, but the wrapper
        is a no-op when nothing spawns a process so it costs nothing.
        """
        try:
            with hide_subprocess_console_windows():
                lang = self.listener.language or None
                transcribe_kwargs = dict(
                    language=lang,
                    beam_size=5,
                    vad_filter=False,  # we run our own amplitude VAD upstream
                )
                # v1.10.26: vocabulary biasing. Same effect as the
                # cloud API – tokenises the prompt into the decoder's
                # preceding-context so words appearing in the prompt
                # get a small probability boost.
                if getattr(self.listener, 'initial_prompt', None):
                    transcribe_kwargs["initial_prompt"] = self.listener.initial_prompt
                segments, _info = self._model.transcribe(
                    audio_path, **transcribe_kwargs
                )
                # segments is a generator – iterate to materialise the output.
                text = "".join(seg.text for seg in segments)
            return self._post_process_transcript(text.strip())
        except Exception as e:
            self.error_occurred.emit(f"Local transcription error: {e}")
            return ""

    def _post_process_transcript(self, text: str) -> str:
        """Apply the default + user-supplied replacement table
        (modules.voice_vocabulary) to a freshly-transcribed string.

        Wrapped in try/except so a buggy user replacement entry
        can't bring down the listener – worst case we return the
        raw text. v1.10.26.
        """
        if not text:
            return text
        try:
            from modules.voice_vocabulary import post_process_transcript
            return post_process_transcript(
                text, getattr(self.listener, 'replacements', None)
            )
        except Exception as e:
            print(f"[VoiceListener] replacement post-process error: {e}")
            return text

    def _maybe_rebuild_vosk_grammar(self):
        """Replace the active KaldiRecognizer with one built from the
        current command list, when the ``_needs_grammar_rebuild`` flag
        has been set by an external mutation.

        Runs only when (a) the flag is set, (b) the engine is Vosk, and
        (c) we have a cached VoskModel object to attach the new
        recognizer to. Always clears the flag, even on failure, so a
        broken rebuild doesn't loop forever – the next legitimate
        change will retry.
        """
        # Always clear the flag up-front, before any heavy work, so a
        # second mutation arriving mid-rebuild still queues a third
        # rebuild deterministically.
        self._needs_grammar_rebuild = False

        if self.listener.engine != ContinuousVoiceListener.ENGINE_VOSK:
            return
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


# Legacy class for backwards compatibility
class _ListenerThread(_VADListenerThread):
    """Legacy alias for _VADListenerThread"""
    pass
