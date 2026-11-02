"""Always-On voice commands: honest status, nothing typed (v1.10.374).

1. When the listener thread dies on its own – in every Windows build up to
   v1.10.373 `from vosk import Model` failed with "[WinError 2] ...
   _internal\\vosk" – the listener used to keep ``is_listening = True``,
   so the Voice tab kept showing "Listening for speech…" and the next
   toggle "stopped" a listener that wasn't running.
2. Speech that matched no command used to be typed into the active app.
3. The build specs must collect vosk, or its DLLs don't ship.

No test opens a microphone: ``sounddevice`` is replaced by a stand-in
(importing the real one initialises PortAudio, which can stop other apps'
audio, issue #255), and ``vosk`` by stand-ins that fail or succeed.
"""

from __future__ import annotations

import ast
import os
import sys
import threading
import time
import types
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from modules import vosk_model_manager
from modules.voice_commands import ContinuousVoiceListener, VoiceCommandManager


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump_until(app, condition, timeout=10.0):
    """Run the Qt event loop until condition() holds (queued cross-thread
    signals are only delivered while events are processed)."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    return True


@pytest.fixture
def no_microphone(monkeypatch):
    """A sounddevice stand-in whose input stream records nothing."""
    sd = types.ModuleType("sounddevice")

    class InputStream:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    sd.InputStream = InputStream
    monkeypatch.setitem(sys.modules, "sounddevice", sd)


@pytest.fixture
def vosk_dll_folder_missing(monkeypatch, tmp_path):
    """A vosk package that fails to import the way the frozen app did."""
    pkg = tmp_path / "fake_site" / "vosk"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        "raise FileNotFoundError(2, 'The system cannot find the file specified',"
        " r'C:\\\\app\\\\_internal\\\\vosk')\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path / "fake_site"))
    monkeypatch.delitem(sys.modules, "vosk", raising=False)


@pytest.fixture
def working_vosk(monkeypatch, tmp_path):
    """A vosk stand-in that loads, plus an 'installed' model folder."""
    vosk = types.ModuleType("vosk")
    vosk.Model = lambda path: SimpleNamespace(path=path)
    vosk.KaldiRecognizer = lambda model, rate, grammar: SimpleNamespace(grammar=grammar)
    monkeypatch.setitem(sys.modules, "vosk", vosk)
    monkeypatch.setattr(vosk_model_manager, "get_model_path",
                        lambda key, user_data: tmp_path)


class Recorder:
    """Counts what a listener tells the outside world, and on which thread
    stopped_unexpectedly arrived."""

    def __init__(self, listener):
        self.started = self.stopped = self.unexpected = 0
        self.errors, self.vad = [], []
        self.unexpected_on_main_thread = []
        listener.listening_started.connect(self._started)
        listener.listening_stopped.connect(self._stopped)
        listener.stopped_unexpectedly.connect(self._unexpected)
        listener.error_occurred.connect(self.errors.append)
        listener.vad_status_changed.connect(self.vad.append)

    def _started(self):
        self.started += 1

    def _stopped(self):
        self.stopped += 1

    def _unexpected(self):
        self.unexpected += 1
        self.unexpected_on_main_thread.append(
            threading.current_thread() is threading.main_thread())


def make_listener(tmp_path):
    manager = VoiceCommandManager(user_data_path=tmp_path)
    return ContinuousVoiceListener(manager, engine="vosk",
                                   user_data_path=str(tmp_path))


# ── 1. a listener whose thread dies stops claiming to listen ─────────────

def test_listener_that_cannot_load_vosk_reports_itself_stopped(
        app, tmp_path, no_microphone, vosk_dll_folder_missing):
    listener = make_listener(tmp_path)
    rec = Recorder(listener)

    listener.start()
    assert listener.is_listening and rec.started == 1

    assert pump_until(app, lambda: rec.unexpected == 1)
    assert listener.is_listening is False
    assert listener._thread is None
    assert rec.stopped == 1
    assert rec.unexpected_on_main_thread == [True]      # queued, not on the worker
    assert any("WinError 2" in e or "cannot find the file" in e for e in rec.errors)
    # The command manager no longer feeds a dead listener.
    assert listener.command_manager.receivers(
        listener.command_manager.commands_changed) == 0


def test_vosk_not_installed_is_reported_the_same_way(app, tmp_path, no_microphone,
                                                     monkeypatch):
    monkeypatch.setitem(sys.modules, "vosk", None)       # → ImportError
    listener = make_listener(tmp_path)
    rec = Recorder(listener)

    listener.start()
    assert pump_until(app, lambda: rec.unexpected == 1)
    assert listener.is_listening is False
    assert any("Vosk is not installed" in e for e in rec.errors)


def test_starting_again_after_a_crash_really_starts(app, tmp_path, no_microphone,
                                                    vosk_dll_folder_missing):
    # Before the fix the first toggle after a crash only "stopped" the dead
    # listener. Twenty crash/restart cycles: one stop and one "unexpected"
    # each, never more, and the listener is never left claiming to run.
    listener = make_listener(tmp_path)
    rec = Recorder(listener)
    for cycle in range(1, 21):
        listener.start()
        assert listener.is_listening
        assert pump_until(app, lambda: rec.unexpected == cycle)
        assert listener.is_listening is False
    assert (rec.started, rec.stopped, rec.unexpected) == (20, 20, 20)


def test_a_normal_stop_is_not_reported_as_unexpected(app, tmp_path, no_microphone,
                                                     working_vosk):
    listener = make_listener(tmp_path)
    rec = Recorder(listener)

    listener.start()
    assert pump_until(app, lambda: "waiting" in rec.vad)   # running, mic "open"
    worker = listener._thread
    listener.stop()
    worker._thread.join(10)
    assert not worker._thread.is_alive()
    app.processEvents()
    for _ in range(20):
        app.processEvents()
        time.sleep(0.01)

    assert rec.stopped == 1 and rec.unexpected == 0
    assert listener.is_listening is False


def test_an_old_thread_ending_after_a_restart_leaves_the_new_one_alone(
        app, tmp_path, no_microphone, working_vosk):
    listener = make_listener(tmp_path)
    rec = Recorder(listener)

    listener.start()
    assert pump_until(app, lambda: "waiting" in rec.vad)
    old = listener._thread
    listener.stop()
    listener.start()                       # restart before the old one has ended
    new = listener._thread
    old._thread.join(10)
    for _ in range(20):
        app.processEvents()
        time.sleep(0.01)

    assert listener.is_listening is True and listener._thread is new
    assert rec.unexpected == 0
    listener.stop()
    new._thread.join(10)


def test_thread_ended_is_idempotent_and_ignores_strangers(app, tmp_path):
    listener = make_listener(tmp_path)
    rec = Recorder(listener)
    current, stranger = SimpleNamespace(stop=lambda: None), object()
    listener.is_listening, listener._thread = True, current

    listener._on_thread_ended(stranger)
    assert listener.is_listening and rec.unexpected == 0

    listener._on_thread_ended(current)
    listener._on_thread_ended(current)
    assert listener.is_listening is False
    assert (rec.stopped, rec.unexpected) == (1, 1)


# ── the Voice tab reflects it ────────────────────────────────────────────

def make_tab(listener):
    from modules.voice_tab import VoiceTab
    settings = {"ui": {"dictation_settings": {}}}
    parent = SimpleNamespace(
        load_dictation_settings=lambda: dict(settings["ui"]["dictation_settings"]),
        _load_unified_settings=lambda: settings,
        _save_unified_settings=lambda s: None,
        voice_command_manager=None,
        voice_listener=listener,
    )
    return VoiceTab(parent)


def test_voice_tab_shows_not_active_after_the_listener_died(
        app, tmp_path, no_microphone, vosk_dll_folder_missing):
    # Only signals that v1.10.373 already had, so this fails on the old
    # code for the reported reason (the label), not a missing attribute.
    listener = make_listener(tmp_path)
    errors, vad = [], []
    listener.error_occurred.connect(errors.append)
    listener.vad_status_changed.connect(vad.append)
    listener.start()
    assert pump_until(app, lambda: errors and "stopped" in vad)
    for _ in range(20):                     # let anything queued after it land
        app.processEvents()
        time.sleep(0.01)

    tab = make_tab(listener)
    tab._sync_alwayson_from_listener()      # what every refresh() does
    assert "Not active" in tab._status_label.text()
    assert "Start" in tab._toggle_btn.text()


def test_voice_tab_while_vosk_loads_offers_stop_not_start(app):
    tab = make_tab(None)
    tab.set_alwayson_status("loading")
    assert "Starting" in tab._status_label.text()
    assert "Stop" in tab._toggle_btn.text()


# ── 2. the main window never types unmatched speech ──────────────────────

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
        self.messages, self.cleared = [], 0

    def showMessage(self, text, timeout=0):
        self.messages.append(text)

    def clearMessage(self):
        self.cleared += 1


@pytest.mark.parametrize("ptt_owned", [False, True])
def test_unmatched_speech_is_reported_not_typed(S, ptt_owned):
    typed, log = [], []
    window = SimpleNamespace(
        _voice_command_ptt_owned=ptt_owned,
        # The pre-1.10.374 default that let the text through:
        load_dictation_settings=lambda: {"alwayson_commands_only": False},
        _insert_dictated_text=typed.append,
        log=log.append,
        status_bar=StatusBar(),
    )
    S.SupervertalerQt._on_alwayson_dictation(window, "[unk] project tm next add [unk]")

    assert typed == []
    assert any("not typed" in line for line in log)
    assert any("No command matched" in m for m in window.status_bar.messages)


def make_ptt_window(S, manager):
    """A window mid-way through a command push-to-talk hold."""
    toggles = []
    window = SimpleNamespace(
        _voice_command_ptt_owned=True,
        _voice_command_ptt_pending_stop=True,
        _voice_command_ptt_last_vad_state="recording",
        voice_command_manager=manager,
        voice_listener=SimpleNamespace(is_listening=False),
        _toggle_alwayson_listening=lambda: toggles.append("toggle"),
        log=lambda *a: None,
        status_bar=StatusBar(),
    )
    window._do_voice_command_ptt_stop = (
        lambda: S.SupervertalerQt._do_voice_command_ptt_stop(window))
    return window, toggles


def test_listener_death_during_a_push_to_talk_hold_ends_the_hold(S, tmp_path,
                                                                 monkeypatch):
    manager = VoiceCommandManager(user_data_path=tmp_path)
    sent = []
    monkeypatch.setattr(manager, "_execute_command_real", sent.append)
    manager.set_defer_keystrokes(True)            # what the PTT press does
    keystroke = next(c for c in manager.commands if c.action_type == "keystroke")
    manager.execute_command(keystroke)            # queued during the hold
    assert sent == []

    window, toggles = make_ptt_window(S, manager)
    S.SupervertalerQt._on_alwayson_listener_died(window)

    assert window._voice_command_ptt_owned is False
    assert window._voice_command_ptt_pending_stop is False
    assert window.status_bar.cleared == 1         # "Listening for command…" gone
    assert manager._defer_keystrokes is False     # later commands aren't held back
    assert sent == [keystroke]                    # the queued one went out once

    # Releasing the key afterwards, or the 1.5 s stop timer firing, must
    # not start a listener again or send anything twice.
    S.SupervertalerQt._on_voice_command_ptt_release(window)
    S.SupervertalerQt._do_voice_command_ptt_stop(window)
    assert toggles == [] and sent == [keystroke]


def test_listener_death_without_push_to_talk_is_harmless(S):
    window = SimpleNamespace(voice_command_manager=None, log=lambda *a: None,
                             status_bar=StatusBar())
    S.SupervertalerQt._on_alwayson_listener_died(window)
    assert window._voice_command_ptt_owned is False


def test_main_window_listens_for_the_listener_dying():
    with open(os.path.join(ROOT, "Supervertaler.py"), encoding="utf-8") as f:
        source = f.read()
    assert "stopped_unexpectedly.connect(self._on_alwayson_listener_died)" in source


# ── 3. the build specs ship vosk's DLLs ──────────────────────────────────

@pytest.mark.parametrize("spec", ["Supervertaler.spec", "Supervertaler_macOS.spec"])
def test_build_spec_collects_vosk(spec):
    with open(os.path.join(ROOT, spec), encoding="utf-8-sig") as f:
        tree = ast.parse(f.read())
    collected = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple)
                and any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "collect_all"
                        for c in ast.walk(node))):
            collected |= {e.value for e in node.iter.elts if isinstance(e, ast.Constant)}
    assert "vosk" in collected
