"""The Voice tab lists microphones only when it is shown (issue #255).

Listing devices initialises PortAudio, which opens every audio device on the
system; done during the start-up warm-up (tab built hidden), it stopped other
apps' music playback at every launch.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from modules import mic_devices
from modules.voice_tab import VoiceTab


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def devices(monkeypatch):
    """Fake device list; records every listing."""
    state = SimpleNamespace(names=["Headset Microphone", "USB Mic"], calls=0)

    def fake_list():
        state.calls += 1
        return list(state.names)

    monkeypatch.setattr(mic_devices, "list_input_devices", fake_list)
    return state


def make_parent(saved_mic=None):
    settings = {"ui": {"dictation_settings": {}}}
    if saved_mic is not None:
        settings["ui"]["dictation_settings"]["mic_device"] = saved_mic
    parent = SimpleNamespace(
        load_dictation_settings=lambda: dict(settings["ui"]["dictation_settings"]),
        _load_unified_settings=lambda: settings,
        _save_unified_settings=lambda s: None,     # settings dict is updated in place
        voice_command_manager=None,
        voice_listener=None,
    )
    return parent, settings["ui"]["dictation_settings"]


def mic_items(tab):
    return [tab._mic_combo.itemData(i) for i in range(tab._mic_combo.count())]


def test_building_the_tab_hidden_does_not_touch_audio_devices(app, devices):
    tab = VoiceTab(make_parent()[0])        # what the start-up warm-up does
    app.processEvents()
    assert devices.calls == 0
    assert mic_items(tab) == [mic_devices.DEFAULT_SENTINEL]


def test_showing_the_tab_lists_devices_once_and_restores_the_saved_mic(app, devices):
    parent, saved = make_parent(saved_mic="USB Mic")
    tab = VoiceTab(parent)
    tab.show()
    app.processEvents()
    assert devices.calls == 1
    assert mic_items(tab) == [mic_devices.DEFAULT_SENTINEL, "Headset Microphone", "USB Mic"]
    assert tab._mic_combo.currentData() == "USB Mic"
    assert saved["mic_device"] == "USB Mic"

    tab.hide()
    tab.show()
    app.processEvents()
    assert devices.calls == 1               # not re-listed on every show


def test_a_missing_saved_mic_is_left_alone_not_overwritten(app, devices):
    devices.names = []                      # unplugged, or PortAudio unavailable
    parent, saved = make_parent(saved_mic="USB Mic")
    tab = VoiceTab(parent)
    tab.show()
    app.processEvents()
    assert tab._mic_combo.currentData() == mic_devices.DEFAULT_SENTINEL
    assert saved["mic_device"] == "USB Mic"   # survives for when it's plugged back in


def test_a_user_choice_is_still_saved(app, devices):
    parent, saved = make_parent()
    tab = VoiceTab(parent)
    tab.show()
    app.processEvents()
    tab._mic_combo.setCurrentIndex(tab._mic_combo.findData("Headset Microphone"))
    assert saved["mic_device"] == "Headset Microphone"
