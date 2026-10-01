"""Global hotkey listener with press AND release events.

The Windows ``RegisterHotKey`` API only delivers press events – there's no
WM_HOTKEY_UP – so the old global-shortcut path could never support
hold-to-talk dictation outside the Workbench editor. This module replaces
that path with a single low-level keyboard hook (via ``pynput``) that
delivers both press and release for every key on the system, then matches
them against registered chord bindings.

Architecturally this mirrors what cjpais/Handy does in Rust with rdev +
their handy-keys crate: one OS-level hook, one background thread, the
matcher fires START on the first press of a complete chord and STOP on
release of any key in that chord.

Two Windows-specific quirks are handled in the matcher rather than the
caller, since every consumer would otherwise re-implement them wrong:

  1. Auto-repeat: holding a key fires PRESS events every ~30 ms. We
     dedupe by tracking a pressed-vks set; only the *transition* into
     "chord fully held" emits START.

  2. AltGr on NL / intl layouts: pressing right Alt fires *both*
     ``ctrl_l`` and ``alt_gr`` events 1–2 ms apart (Windows synthesizes
     the Ctrl). A user-bound Ctrl-modified hotkey would mis-fire on
     every AltGr-typed character without compensation. When ``ALTGR``
     is in the pressed set we strip ``CTRL`` from the *effective* set
     used for chord matching.
"""
from __future__ import annotations

import re
import sys
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal


# ---------------------------------------------------------------------------
# VK normalization
# ---------------------------------------------------------------------------
# Modifiers come in left/right variants (and sometimes a "generic" form). For
# chord matching we collapse all variants to a single canonical token, so a
# binding stored as "CTRL+SHIFT+SPACE" matches regardless of which physical
# Ctrl or Shift the user pressed.

_VK_NORMALIZE = {
    # Ctrl: generic, left, right
    0x11: 'CTRL', 0xA2: 'CTRL', 0xA3: 'CTRL',
    # Shift: generic, left, right
    0x10: 'SHIFT', 0xA0: 'SHIFT', 0xA1: 'SHIFT',
    # Alt: generic, left
    0x12: 'ALT', 0xA4: 'ALT',
    # Right Alt = AltGr on intl layouts. Kept separate so we can strip the
    # synthetic Ctrl Windows emits alongside it (see _altgr_strip_ctrl).
    0xA5: 'ALTGR',
    # Win / Cmd / Meta: left, right
    0x5B: 'META', 0x5C: 'META',
}

# Inverse map (canonical → set of vks). Used when parsing user-facing
# strings like "Ctrl+Shift+Space" into a vk-based binding.
_CANONICAL_TO_VKS = {
    'CTRL':  {0xA2, 0xA3, 0x11},
    'SHIFT': {0xA0, 0xA1, 0x10},
    'ALT':   {0xA4, 0x12},
    'ALTGR': {0xA5},
    'META':  {0x5B, 0x5C},
}


def normalize_vk(vk: int):
    """Return the canonical token for a vk.

    Modifier vks collapse to a string token ('CTRL', 'SHIFT', 'ALT',
    'ALTGR', 'META'). All other vks pass through as integers.
    """
    return _VK_NORMALIZE.get(vk, vk)


# ---------------------------------------------------------------------------
# Qt-style shortcut string parsing
# ---------------------------------------------------------------------------
# Users configure hotkeys in shortcut_manager as Qt-flavoured strings like
# "Ctrl+Shift+Space" or "F9" or "Num+". We need to translate those into the
# vk-based tokens our matcher uses. Only the tokens that can realistically be
# chord-mapped are supported – function keys, common navigation keys, letters,
# digits, and a few numpad keys.

# Qt key name → VK code. Letters and digits are handled programmatically.
_KEY_NAME_TO_VK = {
    'SPACE': 0x20, 'TAB': 0x09, 'BACKSPACE': 0x08, 'BACK': 0x08,
    'RETURN': 0x0D, 'ENTER': 0x0D,
    'ESC': 0x1B, 'ESCAPE': 0x1B,
    'DELETE': 0x2E, 'DEL': 0x2E,
    'INSERT': 0x2D, 'INS': 0x2D,
    'HOME': 0x24, 'END': 0x23,
    'PAGEUP': 0x21, 'PAGEDOWN': 0x22,
    'LEFT': 0x25, 'UP': 0x26, 'RIGHT': 0x27, 'DOWN': 0x28,
    # Function keys F1–F24
    **{f'F{i}': 0x70 + (i - 1) for i in range(1, 25)},
    # Numpad – the Handy-style hotkey targets land here.
    'NUM+': 0x6B, 'NUMADD': 0x6B, 'NUMPLUS': 0x6B,
    'NUM-': 0x6D, 'NUMSUB': 0x6D, 'NUMMINUS': 0x6D,
    'NUM*': 0x6A, 'NUMMUL': 0x6A,
    'NUM/': 0x6F, 'NUMDIV': 0x6F,
    'NUMDOT': 0x6E, 'NUMDECIMAL': 0x6E,
    **{f'NUM{i}': 0x60 + i for i in range(10)},  # numpad 0–9 = vk 0x60..0x69
}


def parse_qt_shortcut(spec: str) -> Optional[frozenset]:
    """Parse a Qt-style shortcut string into a frozenset of canonical tokens.

    Examples:
        "Ctrl+Shift+Space" → frozenset({'CTRL', 'SHIFT', 0x20})
        "F9"               → frozenset({0x78})
        "Num+"             → frozenset({0x6B})

    Returns ``None`` if the string is empty or cannot be parsed.
    """
    if not spec:
        return None

    # Split on '+', then re-attach a trailing '+' to the preceding token –
    # otherwise "Num+" splits to ['Num', ''] and loses the symbol that
    # disambiguates "numpad plus" from "numpad" alone. Same trick for
    # "Numpad+" / "Keypad+" written that way by some users.
    parts = [p.strip() for p in spec.strip().split('+')]
    while len(parts) >= 2 and parts[-1] == '':
        parts.pop()
        if parts:
            parts[-1] = parts[-1] + '+'

    tokens = []
    for part in parts:
        if not part:
            continue
        up = part.upper()

        # Modifier?
        if up in ('CTRL', 'CONTROL'):
            tokens.append('CTRL'); continue
        if up == 'SHIFT':
            tokens.append('SHIFT'); continue
        if up == 'ALT':
            tokens.append('ALT'); continue
        if up == 'ALTGR':
            tokens.append('ALTGR'); continue
        if up in ('META', 'WIN', 'CMD'):
            tokens.append('META'); continue

        # Named key?
        if up in _KEY_NAME_TO_VK:
            tokens.append(_KEY_NAME_TO_VK[up]); continue

        # Single letter / digit?
        if len(up) == 1:
            ch = up
            if 'A' <= ch <= 'Z':
                tokens.append(ord(ch))  # VK_A..VK_Z = 0x41..0x5A
                continue
            if '0' <= ch <= '9':
                tokens.append(ord(ch))  # VK_0..VK_9 = 0x30..0x39
                continue

        # Unknown token – bail out. Caller falls back to a no-op binding
        # rather than registering something nonsensical.
        return None

    if not tokens:
        return None
    return frozenset(tokens)


# ---------------------------------------------------------------------------
# Recorded ("captured") chords — including media / odd keys
# ---------------------------------------------------------------------------
# `parse_qt_shortcut` only understands typed names. Keys like media
# fast-forward can't be typed, so the UI lets the user *record* one: the
# global hook captures the raw vk(s) and we store/describe them by code.
# Serialized form is "CTRL+VK176" (modifiers by name, keys as VK<int>), kept
# deliberately distinct from Qt-style strings so both round-trip cleanly.

# Friendly display labels for vks the typed parser can't name.
_VK_FRIENDLY = {
    0xB0: 'Media Next / Fast-Forward', 0xB1: 'Media Prev / Rewind',
    0xB2: 'Media Stop', 0xB3: 'Media Play/Pause',
    0xAD: 'Volume Mute', 0xAE: 'Volume Down', 0xAF: 'Volume Up',
    0xB4: 'Launch Mail', 0xB5: 'Select Media',
    0xB6: 'Launch App 1', 0xB7: 'Launch App 2',
    0x20: 'Space', 0x09: 'Tab', 0x08: 'Backspace',
    0x0D: 'Enter', 0x1B: 'Esc', 0x2E: 'Delete', 0x2D: 'Insert',
    0x24: 'Home', 0x23: 'End', 0x21: 'PageUp', 0x22: 'PageDown',
    0x25: 'Left', 0x26: 'Up', 0x27: 'Right', 0x28: 'Down',
    0x6B: 'Num +', 0x6D: 'Num -', 0x6A: 'Num *', 0x6F: 'Num /', 0x6E: 'Num .',
    **{0x70 + i: f'F{i + 1}' for i in range(24)},   # F1–F24
    **{0x60 + i: f'Num {i}' for i in range(10)},     # numpad 0–9
}

_MODIFIER_ORDER = ['CTRL', 'ALT', 'ALTGR', 'SHIFT', 'META']


def vk_label(vk: int) -> str:
    """Human-readable label for a single vk (for the settings UI)."""
    if vk in _VK_FRIENDLY:
        return _VK_FRIENDLY[vk]
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:  # A–Z, 0–9
        return chr(vk)
    return f'Key 0x{vk:02X}'


def describe_chord(chord) -> str:
    """Pretty label for a chord, e.g. frozenset({'CTRL', 176}) → 'Ctrl + Media Next / Fast-Forward'."""
    if not chord:
        return ''
    mods = [m.title() for m in _MODIFIER_ORDER if m in chord]
    keys = [vk_label(k) for k in sorted(t for t in chord if isinstance(t, int))]
    return ' + '.join(mods + keys)


def serialize_chord(chord) -> str:
    """Chord → storable string, e.g. frozenset({'CTRL', 176}) → 'CTRL+VK176'."""
    if not chord:
        return ''
    mods = [m for m in _MODIFIER_ORDER if m in chord]
    keys = [f'VK{k}' for k in sorted(t for t in chord if isinstance(t, int))]
    return '+'.join(mods + keys)


def parse_serialized_chord(spec: str):
    """Inverse of `serialize_chord`. Falls back to `parse_qt_shortcut` if the
    string isn't our VK form, so typed shortcuts ("Ctrl+Shift+Space") still work."""
    if not spec:
        return None
    tokens = set()
    for part in spec.split('+'):
        p = part.strip()
        if not p:
            continue
        up = p.upper()
        if up in ('CTRL', 'SHIFT', 'ALT', 'ALTGR', 'META'):
            tokens.add(up)
        elif up.startswith('VK') and up[2:].isdigit():
            tokens.add(int(up[2:]))
        else:
            # Not our serialized form — treat the whole spec as Qt-style.
            return parse_qt_shortcut(spec)
    return frozenset(tokens) if tokens else None


# ---------------------------------------------------------------------------
# Matcher – pure logic, no Qt, no pynput
# ---------------------------------------------------------------------------
class HotkeyMatcher:
    """Match keyboard press/release events against registered chord bindings.

    The matcher is stateful: it tracks the set of currently-held canonical
    tokens and emits transitions via the ``on_chord_pressed`` /
    ``on_chord_released`` callbacks.

    Press semantics: a binding fires "pressed" exactly once when all of its
    tokens have become held (auto-repeat presses are ignored). It fires
    "released" exactly once when any of its tokens is released after a
    press fired.
    """

    def __init__(self, on_chord_pressed=None, on_chord_released=None):
        self._on_pressed = on_chord_pressed or (lambda binding_id: None)
        self._on_released = on_chord_released or (lambda binding_id: None)
        self._bindings: dict[str, frozenset] = {}
        self._pressed: set = set()         # canonical tokens currently held
        self._held: set[str] = set()       # binding ids currently "pressed"

    def register(self, binding_id: str, chord: frozenset) -> None:
        """Register a chord under a binding id. Replaces any prior binding."""
        self._bindings[binding_id] = chord
        # If a re-registration narrows the chord such that it's already
        # satisfied by the current pressed set, suppress an accidental
        # immediate fire – clear any "held" state for this id.
        self._held.discard(binding_id)

    def unregister(self, binding_id: str) -> None:
        self._bindings.pop(binding_id, None)
        self._held.discard(binding_id)

    def _effective_pressed(self) -> set:
        """Pressed set with the Windows AltGr → Ctrl synthesis stripped.

        Windows fires ``ctrl_l`` alongside ``alt_gr`` whenever the right Alt
        key is pressed on intl layouts. Without compensation, any
        Ctrl-modified binding would mis-fire every time the user types an
        AltGr-composed character. When AltGr is held we treat Ctrl as not
        held *for chord matching*.
        """
        if 'ALTGR' in self._pressed:
            return self._pressed - {'CTRL'}
        return self._pressed

    def on_press(self, vk: int) -> None:
        token = normalize_vk(vk)
        if token in self._pressed:
            # Auto-repeat – Windows fires PRESS every ~30 ms while a key is
            # held. We've already seen the original press; no transition.
            return
        self._pressed.add(token)

        effective = self._effective_pressed()
        for binding_id, chord in self._bindings.items():
            if binding_id in self._held:
                continue
            if chord.issubset(effective):
                self._held.add(binding_id)
                self._on_pressed(binding_id)

    def on_release(self, vk: int) -> None:
        token = normalize_vk(vk)
        # Update pressed set BEFORE evaluating – the release semantic is
        # "any required token left the held set", so the post-release state
        # is what determines whether a binding is still satisfied.
        self._pressed.discard(token)
        effective = self._effective_pressed()

        for binding_id in list(self._held):
            chord = self._bindings.get(binding_id)
            if chord is None or not chord.issubset(effective):
                self._held.discard(binding_id)
                self._on_released(binding_id)


# ---------------------------------------------------------------------------
# Qt wrapper – owns the pynput listener thread, emits signals
# ---------------------------------------------------------------------------
class GlobalHotkeyListener(QObject):
    """pynput-backed global hotkey listener with press AND release signals.

    Lives for the lifetime of the app; one instance per process. Owns a
    daemon ``pynput.keyboard.Listener`` thread that runs the OS-level
    keyboard hook. Events are pushed through ``HotkeyMatcher`` and surfaced
    as Qt signals on the main thread via ``pyqtSignal``'s built-in
    cross-thread queueing.

    Failure modes:
        - pynput not installed / import fails → ``start()`` returns False
          and ``available`` stays False. Caller falls back to the legacy
          ``GlobalHotkeyManager`` path.
        - Security software blocks the low-level hook → the listener thread
          fails to start; ``start()`` catches and logs.
    """

    chord_pressed = pyqtSignal(str)   # binding_id
    chord_released = pyqtSignal(str)  # binding_id
    captured = pyqtSignal(object)     # frozenset chord, from record-a-key mode

    def __init__(self, parent=None):
        super().__init__(parent)
        self._matcher = HotkeyMatcher(
            on_chord_pressed=self._emit_pressed,
            on_chord_released=self._emit_released,
        )
        self._listener = None
        self._available = False
        self._capturing = False
        self._capture_pressed: set = set()

    # --- public API -------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._available

    def register(self, binding_id: str, qt_shortcut: str) -> bool:
        """Register a binding. Returns True if the string parsed cleanly."""
        chord = parse_qt_shortcut(qt_shortcut)
        if chord is None:
            return False
        self._matcher.register(binding_id, chord)
        return True

    def register_serialized(self, binding_id: str, spec: str) -> bool:
        """Register a binding from a serialized chord ('CTRL+VK176') or a
        typed Qt shortcut. Returns True if it parsed."""
        chord = parse_serialized_chord(spec)
        if chord is None:
            return False
        self._matcher.register(binding_id, chord)
        return True

    def unregister(self, binding_id: str) -> None:
        self._matcher.unregister(binding_id)

    def begin_capture(self) -> None:
        """Enter record-a-key mode: the next non-modifier press (plus any
        modifiers held with it) is emitted via the ``captured`` signal and
        capture ends. Keys are NOT passed to the matcher while capturing."""
        self._capturing = True
        self._capture_pressed = set()

    def cancel_capture(self) -> None:
        self._capturing = False
        self._capture_pressed = set()

    def start(self) -> bool:
        """Start the listener thread. Returns True on success."""
        if sys.platform == 'darwin':
            # pynput's macOS listener calls the Text Services Manager from its
            # own thread as soon as it starts, and macOS 26 aborts the whole
            # app for that (issue #188). Global hotkeys on macOS go through
            # the NSEvent monitor in platform_helpers instead.
            print("[GlobalHotkeyListener] Not started on macOS: pynput's keyboard "
                  "listener crashes the app on macOS 26 (#188).", flush=True)
            return False
        try:
            from pynput import keyboard
        except Exception:
            return False
        try:
            self._listener = keyboard.Listener(
                on_press=self._on_pynput_press,
                on_release=self._on_pynput_release,
            )
            self._listener.daemon = True
            self._listener.start()
            self._available = True
            return True
        except Exception as e:
            print(f"[GlobalHotkeyListener] start() failed: {e}", flush=True)
            self._listener = None
            self._available = False
            return False

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        self._available = False

    # --- pynput → matcher glue -------------------------------------------

    def _on_pynput_press(self, key):
        vk = self._vk_for(key)
        if vk is None:
            return
        if self._capturing:
            token = normalize_vk(vk)
            self._capture_pressed.add(token)
            # A non-modifier (integer) token is the "primary" key and ends
            # capture; bare modifiers just accumulate until one arrives.
            if isinstance(token, int):
                chord = frozenset(self._capture_pressed)
                self._capturing = False
                self._capture_pressed = set()
                self.captured.emit(chord)
            return
        self._matcher.on_press(vk)

    def _on_pynput_release(self, key):
        if self._capturing:
            return
        vk = self._vk_for(key)
        if vk is not None:
            self._matcher.on_release(vk)

    @staticmethod
    def _vk_for(key) -> Optional[int]:
        """Extract the platform vk from a pynput Key or KeyCode.

        ``Key`` instances (special keys: ctrl_l, shift_r, f9, …) expose vk
        via ``key.value.vk``. ``KeyCode`` instances (regular character
        keys) have ``key.vk`` directly. Falling back through both shapes
        is cheap and removes a class-check from the call sites.
        """
        vk = getattr(key, 'vk', None)
        if vk is not None:
            return vk
        value = getattr(key, 'value', None)
        if value is not None:
            return getattr(value, 'vk', None)
        return None

    # --- matcher → Qt signals --------------------------------------------

    def _emit_pressed(self, binding_id: str) -> None:
        self.chord_pressed.emit(binding_id)

    def _emit_released(self, binding_id: str) -> None:
        self.chord_released.emit(binding_id)
