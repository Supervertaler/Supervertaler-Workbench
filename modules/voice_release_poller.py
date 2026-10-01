"""Detect key-release for the push-to-talk hotkey via GetAsyncKeyState polling.

Press detection stays on RegisterHotKey (a kernel-level hotkey table; no
keyboard hook is installed, so AHK and other hook-using apps are unaffected).
For release detection – which RegisterHotKey doesn't provide – we spawn a
short-lived poll thread on press, watch GetAsyncKeyState for the bound keys
every ~20 ms, and fire a release signal as soon as any required key is no
longer held.

Why polling and not a low-level keyboard hook (the pynput route we tried
earlier): a Python WH_KEYBOARD_LL hook in a heavy PyQt app contends with the
GIL on every callback, can exceed Windows' LowLevelHooksTimeout (default
300 ms), and disrupts AHK's hook chain – which caused AHK's synthetic
Ctrl+C to hang in Workbench's selection-grab path. Polling installs no hook,
so the chain stays clean.

A few Windows quirks are handled here rather than at the call sites:

  - **AltGr synthesises Ctrl.** Pressing the right Alt key on intl/NL
    keyboards fires both VK_LCONTROL and VK_RMENU. A Ctrl-modified
    binding would mis-fire every time the user types an AltGr-composed
    character. When VK_RMENU (0xA5) is down we strip VK_LCONTROL from
    the effective held-set for matching.

  - **Generic + specific modifier VKs.** Windows reports BOTH the
    generic Ctrl (0x11) and the specific LCtrl/RCtrl (0xA2/0xA3) when
    either physical Ctrl is pressed. We collapse both forms to a single
    canonical token at parse time, so a binding stored as "CTRL+SPACE"
    matches whether the user pressed left or right Ctrl.

  - **Mouse buttons.** VK_LBUTTON (0x01), VK_RBUTTON (0x02), VK_MBUTTON
    (0x04) are also reported by GetAsyncKeyState. We only check vks
    that are actually part of the active binding, so mouse activity
    doesn't perturb the release detection.

On macOS (issue #188) the same polling runs on CoreGraphics' key state
(``CGEventSourceKeyState`` / ``CGEventSourceFlagsState``) – again no event
tap and no keyboard hook, and nothing that touches the Text Services
Manager, which macOS 26 only allows on the main thread. Reading key state
needs the Input Monitoring permission; without it the poller stays off and
push-to-talk falls back to press-to-start, press-to-stop.

On Linux the poller is a no-op.
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal


IS_WINDOWS = sys.platform == 'win32'
IS_MACOS = sys.platform == 'darwin'
POLLING_SUPPORTED = IS_WINDOWS or IS_MACOS

if IS_WINDOWS:
    _user32 = ctypes.windll.user32
    _user32.GetAsyncKeyState.restype = wintypes.SHORT
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]

    def _is_down(vk: int) -> bool:
        return (_user32.GetAsyncKeyState(vk) & 0x8000) != 0
else:
    def _is_down(vk: int) -> bool:  # pragma: no cover
        return False


# ---------------------------------------------------------------------------
# macOS: CoreGraphics key state
# ---------------------------------------------------------------------------
# Qt's Mac convention, as in platform_helpers._MacNSEventHotkey: a stored
# "Ctrl" is ⌘, "Meta" is ⌃. The masks are CGEventFlags (= NSEvent flags).
MAC_SHIFT, MAC_CONTROL, MAC_OPTION, MAC_COMMAND = 0x20000, 0x40000, 0x80000, 0x100000
_MAC_MODIFIERS = {
    'CTRL': MAC_COMMAND, 'CONTROL': MAC_COMMAND, 'CMD': MAC_COMMAND, 'WIN': MAC_COMMAND,
    'META': MAC_CONTROL, 'ALT': MAC_OPTION, 'OPTION': MAC_OPTION, 'SHIFT': MAC_SHIFT,
}
# Virtual keycodes (Carbon kVK_*) of the US layout. For letters on other
# layouts the keycode of the key that actually fired the hotkey is used
# instead (KeyReleasePoller.set_trigger_keycode).
_MAC_KEYCODES = {
    **dict(zip('ASDFHGZXCV', (0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09))),
    **dict(zip('BQWERYT', (0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11))),
    **dict(zip('OUIPLJKNM', (0x1F, 0x20, 0x22, 0x23, 0x25, 0x26, 0x28, 0x2D, 0x2E))),
    **dict(zip('1234567890', (0x12, 0x13, 0x14, 0x15, 0x17, 0x16, 0x1A, 0x1C, 0x19, 0x1D))),
    'SPACE': 0x31, 'TAB': 0x30, 'RETURN': 0x24, 'ENTER': 0x24, 'ESC': 0x35, 'ESCAPE': 0x35,
    'BACKSPACE': 0x33, 'DELETE': 0x75, 'DEL': 0x75, 'HOME': 0x73, 'END': 0x77,
    'PAGEUP': 0x74, 'PAGEDOWN': 0x79, 'LEFT': 0x7B, 'RIGHT': 0x7C, 'DOWN': 0x7D, 'UP': 0x7E,
    **dict(zip((f'F{i}' for i in range(1, 21)),
               (0x7A, 0x78, 0x63, 0x76, 0x60, 0x61, 0x62, 0x64, 0x65, 0x6D,
                0x67, 0x6F, 0x69, 0x6B, 0x71, 0x6A, 0x40, 0x4F, 0x50, 0x5A))),
}
_HID_STATE = 1  # kCGEventSourceStateHIDSystemState: the physical keyboard

_cg = None
if IS_MACOS:
    try:
        _cg = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        _cg.CGEventSourceKeyState.restype = ctypes.c_bool
        _cg.CGEventSourceKeyState.argtypes = [ctypes.c_int32, ctypes.c_uint16]
        _cg.CGEventSourceFlagsState.restype = ctypes.c_uint64
        _cg.CGEventSourceFlagsState.argtypes = [ctypes.c_int32]
    except Exception:  # pragma: no cover
        _cg = None


def _mac_key_down(keycode: int) -> bool:  # pragma: no cover - macOS only
    return bool(_cg is not None and _cg.CGEventSourceKeyState(_HID_STATE, keycode))


def _mac_flags() -> int:  # pragma: no cover - macOS only
    return int(_cg.CGEventSourceFlagsState(_HID_STATE)) if _cg is not None else 0


def mac_listen_access() -> Optional[bool]:  # pragma: no cover - macOS only
    """Whether Input Monitoring is granted (CGPreflightListenEventAccess,
    macOS 10.15+); None when it can't be determined."""
    if _cg is None:
        return None
    try:
        fn = _cg.CGPreflightListenEventAccess
        fn.restype = ctypes.c_bool
        fn.argtypes = []
        return bool(fn())
    except AttributeError:
        return None


class MacChord:
    """A chord as macOS key state sees it: modifier flags plus one keycode."""
    __slots__ = ('flags', 'keycode')

    def __init__(self, flags: int, keycode: int):
        self.flags, self.keycode = flags, keycode

    def __eq__(self, other):
        return isinstance(other, MacChord) and (self.flags, self.keycode) == (other.flags, other.keycode)

    def __repr__(self):
        return f"MacChord(flags={self.flags:#x}, keycode={self.keycode:#x})"


def parse_mac_shortcut(spec: str) -> Optional[MacChord]:
    """"Ctrl+Shift+Space" → MacChord(⌘⇧, kVK_Space). None if it has no main
    key or a key without a known keycode."""
    if not spec:
        return None
    flags, keycode = 0, None
    for part in [p.strip() for p in spec.strip().split('+') if p.strip()]:
        up = part.upper()
        if up in _MAC_MODIFIERS:
            flags |= _MAC_MODIFIERS[up]
        elif up in _MAC_KEYCODES and keycode is None:
            keycode = _MAC_KEYCODES[up]
        else:
            return None
    return MacChord(flags, keycode) if keycode is not None else None


def _mac_chord_held(chord: MacChord) -> bool:
    """The chord's key is down and all its modifiers are still held."""
    return _mac_key_down(chord.keycode) and (_mac_flags() & chord.flags) == chord.flags


# ---------------------------------------------------------------------------
# Qt-style shortcut string → set of VKs
# ---------------------------------------------------------------------------
# We share parsing intent with modules.voice_hotkey_listener but with a
# slightly different output shape: this side wants the *concrete* VKs that
# RegisterHotKey would have matched, so the matching logic can compare to
# whatever GetAsyncKeyState reports. Modifiers are stored as canonical
# tokens ('CTRL', 'SHIFT', 'ALT', 'ALTGR', 'META'); main keys as ints.

_KEY_NAME_TO_VK = {
    'SPACE': 0x20, 'TAB': 0x09, 'BACKSPACE': 0x08, 'BACK': 0x08,
    'RETURN': 0x0D, 'ENTER': 0x0D,
    'ESC': 0x1B, 'ESCAPE': 0x1B,
    'DELETE': 0x2E, 'DEL': 0x2E,
    'INSERT': 0x2D, 'INS': 0x2D,
    'HOME': 0x24, 'END': 0x23,
    'PAGEUP': 0x21, 'PAGEDOWN': 0x22,
    'LEFT': 0x25, 'UP': 0x26, 'RIGHT': 0x27, 'DOWN': 0x28,
    **{f'F{i}': 0x70 + (i - 1) for i in range(1, 25)},
    'NUM+': 0x6B, 'NUMADD': 0x6B, 'NUMPLUS': 0x6B,
    'NUMPAD+': 0x6B, 'KEYPAD+': 0x6B,
    'NUM-': 0x6D, 'NUMSUB': 0x6D, 'NUMMINUS': 0x6D,
    'NUMPAD-': 0x6D, 'KEYPAD-': 0x6D,
    'NUM*': 0x6A, 'NUMMUL': 0x6A, 'NUMPAD*': 0x6A,
    'NUM/': 0x6F, 'NUMDIV': 0x6F, 'NUMPAD/': 0x6F,
    'NUM.': 0x6E, 'NUMDOT': 0x6E, 'NUMDECIMAL': 0x6E,
    'NUMENTER': 0x0D,
    **{f'NUM{i}': 0x60 + i for i in range(10)},
    'PAUSE': 0x13, 'BREAK': 0x13,
    'CAPSLOCK': 0x14,
    'PRINTSCREEN': 0x2C, 'PRTSCR': 0x2C, 'PRINT': 0x2C,
    'SCROLLLOCK': 0x91,
}

# Sets of vks that satisfy each canonical modifier token. Used by the
# matcher to decide if a binding is still satisfied: e.g. 'CTRL' is held
# if any of {generic Ctrl, left Ctrl, right Ctrl} is down.
_MOD_VKS = {
    'CTRL':  (0x11, 0xA2, 0xA3),
    'SHIFT': (0x10, 0xA0, 0xA1),
    'ALT':   (0x12, 0xA4),
    'ALTGR': (0xA5,),
    'META':  (0x5B, 0x5C),
}


def parse_qt_shortcut(spec: str) -> Optional[frozenset]:
    """Parse a Qt-style shortcut into a frozenset of {canonical-modifier-strs, vk-ints}.

    Examples:
        "Ctrl+Shift+Space" → frozenset({'CTRL', 'SHIFT', 0x20})
        "Num+"             → frozenset({0x6B})
        "F9"               → frozenset({0x78})

    Returns ``None`` for empty / unparseable strings.
    """
    if not spec:
        return None
    import re

    # Re-attach trailing '+' to the previous token so "Num+" stays as one
    # symbol instead of splitting to ['Num', ''].
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
        if up in _KEY_NAME_TO_VK:
            tokens.append(_KEY_NAME_TO_VK[up]); continue
        if len(up) == 1:
            ch = up
            if 'A' <= ch <= 'Z':
                tokens.append(ord(ch)); continue
            if '0' <= ch <= '9':
                tokens.append(ord(ch)); continue
        return None

    return frozenset(tokens) if tokens else None


# ---------------------------------------------------------------------------
# Held-state check
# ---------------------------------------------------------------------------

def _chord_held(chord: frozenset) -> bool:
    """Return True iff every token in *chord* is currently pressed.

    Modifiers: any of the relevant L/R/generic vks counts.
    Non-modifiers: the exact vk must be down.
    AltGr quirk: if RAlt (0xA5) is down, Ctrl is treated as NOT held when
        the binding is Ctrl-modified – Windows fires the synthetic Ctrl,
        not the user, and we don't want to confuse the two.
    """
    altgr_active = _is_down(0xA5)
    for token in chord:
        if isinstance(token, str):
            if token == 'CTRL' and altgr_active:
                # Synthetic Ctrl from AltGr doesn't satisfy a real Ctrl
                # binding. Real left/right Ctrl is still possible only if
                # AltGr isn't the source of the Ctrl event; on Windows
                # there's no clean way to tell them apart at this layer,
                # so we conservatively say "no" – the same compromise the
                # pynput-listener path made.
                return False
            vks = _MOD_VKS.get(token, ())
            if not any(_is_down(vk) for vk in vks):
                return False
        else:
            if not _is_down(token):
                return False
    return True


# ---------------------------------------------------------------------------
# Qt-friendly poller
# ---------------------------------------------------------------------------
class KeyReleasePoller(QObject):
    """Watches for release of a chord previously detected as pressed.

    Single instance per app. The press event is delivered by an external
    mechanism (RegisterHotKey via GlobalHotkeyManager); call ``start()``
    when that press fires. The poller spawns a daemon thread that polls
    ``GetAsyncKeyState`` for the bound chord every ``poll_ms`` and emits
    ``released`` (on the Qt main thread, via signal queuing) the moment
    any required key is no longer held.

    Idempotent: calling ``start()`` again while a poll is already running
    is ignored (the existing thread will continue to watch the chord).

    Safety: the thread auto-exits after ``max_hold_ms`` even if the user
    somehow never releases the keys, so a wedged poller can never block
    anything indefinitely.
    """

    released = pyqtSignal()

    def __init__(self,
                 poll_ms: int = 20,
                 max_hold_ms: int = 60_000,
                 parent=None):
        super().__init__(parent)
        self._poll_ms = poll_ms
        self._max_hold_ms = max_hold_ms
        self._chord: Optional[frozenset] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def set_chord(self, qt_shortcut: str) -> bool:
        """Parse and remember the chord we'll watch on the next ``start()``.

        Returns False if the string was empty or unparseable; in that
        case the poller becomes a no-op until a valid chord is set.
        """
        chord = parse_mac_shortcut(qt_shortcut) if IS_MACOS else parse_qt_shortcut(qt_shortcut)
        self._chord = chord
        return chord is not None

    def set_trigger_keycode(self, keycode: Optional[int]) -> None:
        """macOS: watch the physical key that fired the hotkey (its keycode
        from the NSEvent), which on a non-US layout may differ from the US
        keycode the shortcut string maps to."""
        if isinstance(self._chord, MacChord) and keycode is not None:
            self._chord = MacChord(self._chord.flags, int(keycode))

    def start(self) -> None:
        """Begin polling for release. No-op if already polling, no chord,
        on Linux, or on a Mac without Input Monitoring permission."""
        if not POLLING_SUPPORTED or self._chord is None:
            return
        if isinstance(self._chord, MacChord) and mac_listen_access() is False:
            # Every poll would read "not held" and stop the recording at once
            return
        if self._thread is not None and self._thread.is_alive():
            return  # Already polling for this press – let it finish.
        self._stop.clear()
        t = threading.Thread(target=self._run, daemon=True)
        self._thread = t
        t.start()

    def stop(self) -> None:
        """Best-effort halt. Cooperative; the thread checks the stop
        event every poll cycle. Safe to call from any thread."""
        self._stop.set()

    # --- internal --------------------------------------------------------

    def _run(self):
        chord = self._chord
        if chord is None:
            return
        deadline = time.monotonic() + (self._max_hold_ms / 1000)
        poll_s = self._poll_ms / 1000

        # Warm-up: between the WM_HOTKEY press and the poller actually
        # running, ~50–150 ms can elapse (start_voice_dictation pauses
        # Always-On, builds the recording thread, etc.). If the user
        # *tapped* the hotkey, by the time we poll the keys are already
        # up – we should fire release promptly so the recording doesn't
        # stay open waiting for a release that already happened. If the
        # user is *holding*, we need to first observe the chord as held
        # before treating subsequent not-held as a real release.
        #
        # Defensive: require ``MISS_THRESHOLD`` consecutive not-held
        # polls before firing release. Reading GetAsyncKeyState
        # mid-transition or a cheap-keyboard bounce can momentarily
        # report a key as up; this avoids firing a false release on a
        # single glitched poll.
        MISS_THRESHOLD = 2  # ~40 ms at 20 ms/poll
        TAP_WARMUP_MS = 250  # if never held after this, assume tap

        warm_up_deadline = time.monotonic() + (TAP_WARMUP_MS / 1000)
        held_seen = False
        miss_count = 0

        held = _mac_chord_held if isinstance(chord, MacChord) else _chord_held
        while not self._stop.is_set() and time.monotonic() < deadline:
            if held(chord):
                held_seen = True
                miss_count = 0
            else:
                if held_seen:
                    miss_count += 1
                    if miss_count >= MISS_THRESHOLD:
                        self.released.emit()
                        return
                elif time.monotonic() >= warm_up_deadline:
                    # Never observed the chord as held within the
                    # tap-warmup window: user must have tapped + already
                    # let go before our thread got CPU time. Fire so
                    # the recording (which probably has <100 ms of audio)
                    # is finalised quickly.
                    self.released.emit()
                    return
            time.sleep(poll_s)

        # Deadline reached (60 s max hold) – emit anyway so the recording
        # never gets stuck open.
        if not self._stop.is_set():
            self.released.emit()
