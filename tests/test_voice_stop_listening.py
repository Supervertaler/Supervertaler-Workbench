"""Saying "stop listening" switches Always-On off (v1.10.375).

From v1.9.50 to v1.10.374 the command looked for the main window's
``voice_command_listener``, which never existed (it is ``voice_listener``):
nothing stopped, and "What I heard" still showed a ✓. It now goes through
the main window's own toggle, so the Voice tab, the tray and the status
bar all show Always-On stopped, and it is not reported as done when
nothing was listening.

No test opens a microphone: the listener is only marked as listening,
never started.
"""

from __future__ import annotations

import os
import re
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from modules.voice_commands import ContinuousVoiceListener, VoiceCommandManager


@pytest.fixture(scope="module")
def S():
    QApplication.instance() or QApplication([])
    saved = sys.stdout, sys.stderr
    import Supervertaler   # re-wraps stdout/stderr on Windows at import
    S._wrappers = sys.stdout, sys.stderr   # keep alive: closing them closes pytest's capture
    sys.stdout, sys.stderr = saved
    return Supervertaler


class StatusBar:
    def __init__(self):
        self.messages = []

    def showMessage(self, text, timeout=0):
        self.messages.append(text)


def make_window(manager, listener, toggle=None):
    """A main-window stand-in whose Always-On toggle is ``toggle(window)``."""
    ui, log = [], []
    window = SimpleNamespace(voice_listener=listener, log=log.append,
                             status_bar=StatusBar(), _update_alwayson_ui=ui.append)
    window._toggle_alwayson_listening = lambda: toggle(window)
    manager.register_main_window_handlers(window)
    return window, ui, log


def test_stop_listening_switches_always_on_off(S, tmp_path):
    manager = VoiceCommandManager(user_data_path=tmp_path)    # the defaults
    listener = ContinuousVoiceListener(manager, user_data_path=str(tmp_path))
    listener.is_listening = True
    # The main window's real toggle: the one the button, the tray icon
    # and Ctrl+Alt+O use.
    window, ui, log = make_window(manager, listener,
                                  S.SupervertalerQt._toggle_alwayson_listening)
    heard = []
    listener.command_detected.connect(lambda h, r: heard.append((h, r)))

    listener._on_transcription("stop listening")    # what Vosk hands over

    assert listener.is_listening is False
    assert window.voice_listener is None            # as after the button
    assert ui == ["stopped"]                        # Voice tab, tray, status bar
    assert "🔇 Always-on listening stopped" in window.status_bar.messages
    assert heard == [("stop listening", "✓ Stop voice recognition")]


@pytest.mark.parametrize("running", [None, "stopped"])
def test_stop_listening_with_nothing_listening_is_not_reported_as_done(tmp_path, running):
    manager = VoiceCommandManager(user_data_path=tmp_path)
    listener = None
    if running == "stopped":
        listener = ContinuousVoiceListener(manager, user_data_path=str(tmp_path))
    toggles = []
    window, ui, log = make_window(manager, listener, toggles.append)

    assert manager.process_spoken_text("stop listening") == (True, "✗ Failed: stop listening")
    assert toggles == []        # the toggle would have *started* Always-On
    assert any("did nothing" in line for line in log)


def test_a_command_whose_method_is_missing_is_not_reported_as_done(tmp_path):
    manager = VoiceCommandManager(user_data_path=tmp_path)
    make_window(manager, None)      # no go_to_next_segment on this window
    assert manager.process_spoken_text("next segment") == (True, "✗ Failed: next segment")


def test_every_main_window_name_the_commands_use_exists():
    # The original bug was a name that didn't exist. Catch the next one.
    with open(os.path.join(ROOT, "modules", "voice_commands.py"), encoding="utf-8") as f:
        commands_src = f.read()
    with open(os.path.join(ROOT, "Supervertaler.py"), encoding="utf-8") as f:
        app_src = f.read()
    names = set(re.findall(r"(?:hasattr|getattr)\((?:self\.)?main_window, '(\w+)'", commands_src))
    names |= set(re.findall(r"self\.main_window\.(\w+)\(", commands_src))
    assert {"voice_listener", "_toggle_alwayson_listening", "go_to_next_segment"} <= names
    missing = [n for n in sorted(names)
               if not re.search(rf"def {n}\(|self\.{n} = ", app_src)]
    assert missing == []
