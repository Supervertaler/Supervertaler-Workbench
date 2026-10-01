"""macOS global hotkeys (issue #188), with a fake AppKit and fake
CoreGraphics key state – this runs on any platform."""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import platform_helpers as ph
from modules import voice_release_poller as vrp

SHIFT, CONTROL, OPTION, COMMAND = 1 << 17, 1 << 18, 1 << 19, 1 << 20


class FakeEvent:
    def __init__(self, flags, keycode, chars, chars_ignoring=None, new_api=True):
        self._flags, self._keycode, self._chars = flags, keycode, chars
        self._ignoring = chars if chars_ignoring is None else chars_ignoring
        self._new_api = new_api

    def modifierFlags(self):
        return self._flags | 0x100  # device-dependent bits are masked off

    def keyCode(self):
        return self._keycode

    def charactersByApplyingModifiers_(self, mods):
        if not self._new_api:
            raise AttributeError("before macOS 10.15")
        return self._chars

    def charactersIgnoringModifiers(self):
        return self._ignoring


@pytest.fixture
def appkit(monkeypatch):
    installed = {}

    class NSEvent:
        @staticmethod
        def addGlobalMonitorForEventsMatchingMask_handler_(mask, handler):
            installed["handler"] = handler
            return object()

        @staticmethod
        def removeMonitor_(monitor):
            installed["removed"] = True

    mod = types.ModuleType("AppKit")
    mod.NSEvent = NSEvent
    mod.NSEventMaskKeyDown = 1 << 10
    mod.NSEventModifierFlagShift, mod.NSEventModifierFlagControl = SHIFT, CONTROL
    mod.NSEventModifierFlagOption, mod.NSEventModifierFlagCommand = OPTION, COMMAND
    monkeypatch.setitem(sys.modules, "AppKit", mod)
    return installed


def test_nsevent_matching(appkit, monkeypatch):
    monkeypatch.setattr(ph, "mac_accessibility_trusted", lambda prompt=False: True)
    fired = []
    hk = ph._MacNSEventHotkey()
    for sc in ("ctrl+alt+l", "ctrl+shift+1", "meta+f9", "ctrl+alt+q"):
        assert hk.register(sc, lambda sc=sc: fired.append(sc))
    assert not hk.register("f9", lambda: None)            # no modifier: refused
    assert hk.start()
    press = appkit["handler"]

    press(FakeEvent(COMMAND | OPTION, 0x25, "l"))         # ⌘⌥L
    assert fired == ["ctrl+alt+l"] and hk.last_keycode == 0x25
    press(FakeEvent(COMMAND | SHIFT, 0x12, "1", "!"))     # ⌘⇧1 reads "1", not "!"
    press(FakeEvent(CONTROL, 0x65, ""))              # ⌃F9
    assert fired[1:] == ["ctrl+shift+1", "meta+f9"]
    press(FakeEvent(COMMAND | OPTION, 0x25, "д"))          # Russian layout: the L key
    assert fired[-1] == "ctrl+alt+l"
    n = len(fired)
    press(FakeEvent(COMMAND | OPTION, 0x0C, "a"))          # AZERTY: Q key types "a" – not ⌘⌥Q
    press(FakeEvent(COMMAND, 0x25, "l"))                   # wrong modifiers
    press(FakeEvent(COMMAND | SHIFT, 0x12, "1", "!", new_api=False))  # old macOS: "!" ≠ "1"
    assert len(fired) == n
    hk.stop()
    assert appkit["removed"]


def test_manager_reports_missing_permission(appkit, monkeypatch):
    monkeypatch.setattr(ph, "IS_WINDOWS", False)
    monkeypatch.setattr(ph, "IS_MACOS", True)
    monkeypatch.setattr(ph, "mac_accessibility_trusted", lambda prompt=False: False)
    m = ph.GlobalHotkeyManager()
    m.register("ctrl+alt+l", lambda: None)
    m.register("ctrl+alt+f99", lambda: None)               # not a key: reported, not fatal
    assert m.start() and m._backend == "nsevent"
    assert m.permission_missing and m.failed_hotkeys == ["ctrl+alt+f99"]
    appkit["handler"](FakeEvent(COMMAND | OPTION, 0x25, "l"))
    assert m.last_trigger_keycode() == 0x25
    monkeypatch.setattr(ph, "mac_accessibility_trusted", lambda prompt=False: True)
    m2 = ph.GlobalHotkeyManager()
    m2.register("ctrl+alt+l", lambda: None)
    assert m2.start() and not m2.permission_missing


def test_mac_event_key():
    assert ph.mac_event_key("L", 0x25) == "l"
    assert ph.mac_event_key("λ", 0x25) == "l"              # Greek
    assert ph.mac_event_key("", 0x7A) == ""     # F1 stays F1
    assert ph.mac_event_key("", 0x25) == "l"
    assert ph.mac_accessibility_trusted() in (None, True, False)


def test_parse_mac_shortcut():
    P = vrp.parse_mac_shortcut
    assert P("Ctrl+Shift+Space") == vrp.MacChord(COMMAND | SHIFT, 0x31)   # Qt Ctrl = ⌘
    assert P("Ctrl+Alt+V") == vrp.MacChord(COMMAND | OPTION, 0x09)
    assert P("Meta+F9") == vrp.MacChord(CONTROL, 0x65)                  # Qt Meta = ⌃
    assert P("Ctrl+Alt+5") == vrp.MacChord(COMMAND | OPTION, 0x17)
    assert P("Ctrl+Shift") is None and P("Ctrl+Num+") is None and P("") is None


def test_release_poller_on_macos(monkeypatch):
    monkeypatch.setattr(vrp, "IS_MACOS", True)
    monkeypatch.setattr(vrp, "POLLING_SUPPORTED", True)
    monkeypatch.setattr(vrp.time, "sleep", lambda s: None)
    state = {"down": set(), "flags": 0, "reads": 0}

    def key_down(kc):
        state["reads"] += 1
        if state["reads"] == 4:                  # the user lets go of the key
            state["down"].clear()
        return kc in state["down"]
    monkeypatch.setattr(vrp, "_mac_key_down", key_down)
    monkeypatch.setattr(vrp, "_mac_flags", lambda: state["flags"])

    p = vrp.KeyReleasePoller()
    assert p.set_chord("Ctrl+Alt+L")
    p.set_trigger_keycode(0x2E)                  # the key that fired, on this layout
    assert p._chord == vrp.MacChord(COMMAND | OPTION, 0x2E)
    released = []
    p.released.connect(lambda: released.append(True))
    state["down"], state["flags"] = {0x2E}, COMMAND | OPTION | 0x100
    p._run()
    assert released == [True] and state["reads"] == 5   # held 3×, then 2 misses

    # letting go of a modifier also counts as a release
    state.update(down={0x2E}, reads=-100)
    monkeypatch.setattr(vrp, "_mac_flags",
                        lambda: COMMAND | OPTION if state["reads"] < -97 else COMMAND)
    released.clear()
    p._run()
    assert released == [True] and state["reads"] == -96   # held 2×, then 2 misses

    # without Input Monitoring the poller stays off (it would stop at once)
    monkeypatch.setattr(vrp, "mac_listen_access", lambda: False)
    started = []
    monkeypatch.setattr(vrp.threading, "Thread", lambda **kw: started.append(kw))
    p.start()
    assert started == []


def test_pynput_listener_not_started_on_macos(monkeypatch):
    from modules import voice_hotkey_listener as vhl
    monkeypatch.setattr(vhl.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "pynput", None)        # importing it would fail the test
    listener = vhl.GlobalHotkeyListener()
    assert listener.start() is False
