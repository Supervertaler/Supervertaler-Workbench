"""Voice is commands only (v1.10.374).

The built-in dictation, the hold-to-talk command key and the "pause for
external dictation" key are gone; commands are matched the way SuperVoice
in Supervertaler for Trados matches them; the defaults are distinctive;
keystroke commands no longer need AutoHotkey; the Voice tab lists what it
heard. No test opens a microphone or sends a real key.
"""

from __future__ import annotations

import json
import os
import re
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from modules import voice_commands, vosk_model_manager
from modules.voice_commands import (
    VoiceCommand, VoiceCommandManager, parse_chord, send_chord,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def manager_with(tmp_path, commands):
    m = VoiceCommandManager(user_data_path=tmp_path)
    m.commands = commands
    return m


# ── matching, as in Trados ───────────────────────────────────────────────

def cmds():
    return [
        VoiceCommand("next segment", ["go down"], "internal", "navigate_next"),
        VoiceCommand("translate segment", [], "internal", "translate_segment"),
        VoiceCommand("translate all segments", [], "internal", "batch_translate"),
        VoiceCommand("confirm", [], "internal", "confirm_segment"),
        # A one-word command of the kind users have (Michael's memoQ "Next").
        VoiceCommand("Next", [], "keystroke", "ctrl+enter"),
        VoiceCommand("switched off", [], "internal", "x", enabled=False),
    ]


@pytest.mark.parametrize("heard, expected", [
    ("next segment", "next segment"),                 # exact
    ("Next   Segment", "next segment"),               # case and spacing
    ("go down", "next segment"),                      # alias
    ("[unk] next segment [unk]", "next segment"),     # Vosk leftovers around it
    ("translate all segments", "translate all segments"),
    ("[unk] translate segment", "translate segment"),
    ("confirm", "confirm"),                           # one word, said on its own
    ("next", "Next"),
])
def test_command_is_found(tmp_path, heard, expected):
    m = manager_with(tmp_path, cmds())
    assert m.find_matching_command(heard).phrase == expected


@pytest.mark.parametrize("heard", [
    "", "   ", "[unk]",
    "next segments",            # not on a word boundary
    "segment",                  # a near miss: no guessing
    "confirmed",
    "switched off",             # disabled command
    # One-word commands never fire from inside other speech: this exact
    # fragment of dictated text came out of Vosk on 19 Aug 2026.
    "[unk] project tm next add [unk]",
    "[unk] next [unk]",
    "[unk] confirm [unk]",
])
def test_no_command_is_guessed(tmp_path, heard):
    m = manager_with(tmp_path, cmds())
    assert m.find_matching_command(heard) is None


def test_the_longest_phrase_inside_the_utterance_wins(tmp_path):
    m = manager_with(tmp_path, [
        VoiceCommand("translate", [], "internal", "short"),
        VoiceCommand("translate all segments", [], "internal", "long"),
    ])
    assert m.find_matching_command("[unk] translate all segments").action == "long"


def test_process_spoken_text_reports_all_three_outcomes(tmp_path, monkeypatch):
    m = manager_with(tmp_path, cmds())
    results = iter([True, False])
    monkeypatch.setattr(m, "execute_command", lambda c: next(results))
    assert m.process_spoken_text("confirm") == (True, "✓ confirm")
    assert m.process_spoken_text("confirm") == (True, "✗ Failed: confirm")
    assert m.process_spoken_text("hello there") == (False, "hello there")


# ── the defaults ─────────────────────────────────────────────────────────

EVERYDAY = {"next", "down", "previous", "back", "up", "beginning", "end", "done",
            "okay", "clear", "save", "open", "search", "stop", "pause",
            "translate", "dictate"}


def test_defaults_have_no_everyday_one_word_phrases():
    spoken = [p.lower() for c in VoiceCommandManager.DEFAULT_COMMANDS
              for p in [c.phrase] + c.aliases]
    assert not EVERYDAY & set(spoken)
    assert len(spoken) == len(set(spoken))          # no phrase twice
    assert not any(c.action == "start_dictation" for c in VoiceCommandManager.DEFAULT_COMMANDS)


def test_editing_a_command_never_edits_the_defaults(tmp_path):
    before = [(c.phrase, list(c.aliases), c.enabled) for c in VoiceCommandManager.DEFAULT_COMMANDS]
    m = VoiceCommandManager(user_data_path=tmp_path)
    m.commands[0].enabled = False
    m.commands[0].aliases.append("mine")
    after = [(c.phrase, list(c.aliases), c.enabled) for c in VoiceCommandManager.DEFAULT_COMMANDS]
    assert before == after


# ── saved lists from v1.10.373 ───────────────────────────────────────────

def write_old_list(tmp_path, commands):
    path = tmp_path / "workbench" / "settings" / "voice_commands.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": "1.0", "match_threshold": 0.85,
                                "commands": commands}), encoding="utf-8")
    return path


OLD_LIST = [
    {"phrase": "Next", "aliases": ["next segment"], "action_type": "keystroke",
     "action": "ctrl+enter", "description": "Confirm segment in memoQ",
     "category": "trados", "enabled": True},
    {"phrase": "start dictation", "aliases": ["dictate", "voice input"],
     "action_type": "internal", "action": "start_dictation",
     "description": "Start voice dictation mode", "category": "dictation", "enabled": False},
    {"phrase": "previous segment", "aliases": ["previous", "back", "up"],
     "action_type": "internal", "action": "navigate_previous",
     "description": "Move to previous segment", "category": "navigation", "enabled": False},
]


def test_an_old_list_loads_without_dictation_and_keeps_everything_else(tmp_path):
    path = write_old_list(tmp_path, OLD_LIST)
    m = VoiceCommandManager(user_data_path=tmp_path)

    assert [c.phrase for c in m.commands] == ["Next", "previous segment"]
    # The user's own commands and aliases are untouched, one-word ones included.
    assert m.commands[0].aliases == ["next segment"]
    assert m.commands[1].aliases == ["previous", "back", "up"]
    assert m.commands[1].enabled is False
    # Defaults the user deleted are not brought back.
    assert not any(c.action == "show_log" for c in m.commands)

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["defaults_version"] == VoiceCommandManager.DEFAULTS_VERSION
    assert "match_threshold" not in saved
    assert [c["phrase"] for c in saved["commands"]] == ["Next", "previous segment"]


def test_a_broken_list_falls_back_to_the_defaults(tmp_path):
    path = tmp_path / "workbench" / "settings" / "voice_commands.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    m = VoiceCommandManager(user_data_path=tmp_path)
    assert len(m.commands) == len(VoiceCommandManager.DEFAULT_COMMANDS)


def test_newer_defaults_reach_a_customised_list_once(tmp_path, monkeypatch):
    monkeypatch.setattr(VoiceCommandManager, "DEFAULTS_VERSION", 3)
    monkeypatch.setattr(VoiceCommandManager, "_DEFAULTS_SINCE",
                        {"show log": 3, "show editor": 3, "lookup": 3})
    write_old_list(tmp_path, OLD_LIST + [
        # covers "show editor" by phrase, "lookup" by action
        {"phrase": "mine", "aliases": ["show editor"], "action_type": "keystroke", "action": "f2"},
        {"phrase": "find it", "aliases": [], "action_type": "internal", "action": "open_superlookup"},
    ])
    m = VoiceCommandManager(user_data_path=tmp_path)
    added = [c.phrase for c in m.commands[4:]]
    assert added == ["show log"]

    # Loading again doesn't add it twice.
    m2 = VoiceCommandManager(user_data_path=tmp_path)
    assert [c.phrase for c in m2.commands].count("show log") == 1


def test_import_skips_retired_commands(tmp_path):
    src = tmp_path / "export.json"
    src.write_text(json.dumps({"commands": OLD_LIST}), encoding="utf-8")
    m = manager_with(tmp_path, [])
    m.import_commands(src, merge=False)
    assert [c.phrase for c in m.commands] == ["Next", "previous segment"]


# ── keystrokes without AutoHotkey ────────────────────────────────────────

def test_the_stored_key_formats_are_understood():
    from pynput.keyboard import Key, KeyCode
    # Every keystroke format found in a real voice_commands.json.
    expected = {
        "F6": [Key.f6], "del": [Key.delete], "tab": [Key.tab],
        "ctrl+a": [Key.ctrl, KeyCode.from_char("a")],
        "alt+down": [Key.alt, Key.down], "alt+up": [Key.alt, Key.up],
        "alt+shift+t": [Key.alt, Key.shift, KeyCode.from_char("t")],
        "ctrl+enter": [Key.ctrl, Key.enter], "ctrl+home": [Key.ctrl, Key.home],
        "meta+pgdown": [Key.cmd, Key.page_down], "ctrl++": [Key.ctrl, KeyCode.from_char("+")],
        "+": [KeyCode.from_char("+")],
    }
    for chord, keys in expected.items():
        assert parse_chord(chord) == keys, chord


@pytest.mark.parametrize("bad", ["", "ctrl+", "ctrl+banana", "ctrl++a", "+ctrl"])
def test_a_bad_key_name_fails_loudly(bad):
    with pytest.raises(ValueError):
        parse_chord(bad)


class FakeController:
    def __init__(self, fail_on=None):
        self.events, self.fail_on = [], fail_on

    def press(self, key):
        if key == self.fail_on:
            raise OSError("blocked")
        self.events.append(("down", key))

    def release(self, key):
        self.events.append(("up", key))


def test_a_chord_is_pressed_then_released_in_reverse():
    from pynput.keyboard import Key, KeyCode
    c = FakeController()
    send_chord("ctrl+shift+s", controller=c)
    s_key = KeyCode.from_char("s")
    assert c.events == [("down", Key.ctrl), ("down", Key.shift), ("down", s_key),
                        ("up", s_key), ("up", Key.shift), ("up", Key.ctrl)]


def test_modifiers_are_released_even_when_a_key_fails():
    from pynput.keyboard import Key, KeyCode
    c = FakeController(fail_on=KeyCode.from_char("s"))
    with pytest.raises(OSError):
        send_chord("ctrl+shift+s", controller=c)
    assert c.events == [("down", Key.ctrl), ("down", Key.shift),
                        ("up", Key.shift), ("up", Key.ctrl)]   # nothing left held


def test_keystroke_commands_bypass_autohotkey_on_windows(tmp_path, monkeypatch):
    m = manager_with(tmp_path, [])
    sent, errors = [], []
    monkeypatch.setattr(voice_commands, "IS_WINDOWS", True)
    monkeypatch.setattr(voice_commands, "send_chord", sent.append)
    monkeypatch.setattr(m, "_run_ahk_code", lambda *a: pytest.fail("AutoHotkey used"))
    m.error_occurred.connect(errors.append)
    assert m.execute_command(VoiceCommand("undo", [], "keystroke", "ctrl+z")) is True
    assert sent == ["ctrl+z"] and errors == []


def test_a_failing_keystroke_command_reports_why(tmp_path, monkeypatch):
    m = manager_with(tmp_path, [])
    errors = []
    monkeypatch.setattr(voice_commands, "IS_WINDOWS", True)
    m.error_occurred.connect(errors.append)
    assert m.execute_command(VoiceCommand("oops", [], "keystroke", "ctrl+banana")) is False
    assert errors and "banana" in errors[0]


# ── the listener and the model ───────────────────────────────────────────

def test_the_listener_has_no_dictation_left():
    listener_api = set(dir(voice_commands.ContinuousVoiceListener))
    assert not {"pause", "resume", "text_for_dictation", "ENGINE_FASTER_WHISPER",
                "ENGINE_API", "use_api"} & listener_api
    assert "no_command_matched" in listener_api


def test_only_the_english_model_is_offered():
    assert list(vosk_model_manager.MODELS) == ["small-en-us"]
    assert not hasattr(vosk_model_manager, "pick_model_for_language")


# ── shortcuts, packages, modules ─────────────────────────────────────────

def test_the_dictation_and_hold_to_talk_shortcuts_are_gone():
    from modules.shortcut_manager import ShortcutManager
    ids = ShortcutManager.DEFAULT_SHORTCUTS
    assert "voice_dictate" not in ids and "voice_command_ptt" not in ids
    assert ids["voice_alwayson_toggle"]["default"] == "Ctrl+Alt+O"


def test_nothing_imports_the_dictation_stack():
    pattern = re.compile(
        r"^\s*(from|import)\s+(faster_whisper|ctranslate2|modules\.(voice_dictation|"
        r"voice_dictation_lite|voice_vocabulary|dictation_toast|voice_release_poller|"
        r"voice_hotkey_listener))\b", re.M)
    offenders = []
    for folder in (ROOT, os.path.join(ROOT, "modules")):
        for name in os.listdir(folder):
            if name.endswith(".py"):
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    if pattern.search(f.read()):
                        offenders.append(name)
    assert offenders == []
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        assert '"faster-whisper' not in f.read()     # as a dependency


# ── "What I heard" ───────────────────────────────────────────────────────

def make_tab():
    from modules.voice_tab import VoiceTab
    settings = {"ui": {"dictation_settings": {}}}
    parent = SimpleNamespace(
        load_dictation_settings=lambda: dict(settings["ui"]["dictation_settings"]),
        _load_unified_settings=lambda: settings,
        _save_unified_settings=lambda s: None,
        voice_command_manager=None,
        voice_listener=None,
    )
    return VoiceTab(parent)


def test_what_i_heard_is_newest_first_and_capped(app):
    tab = make_tab()
    for i in range(tab.HEARD_LIMIT * 2 + 7):        # a long session
        tab.add_heard(f"phrase {i}", "no command matched", None)
    tab.add_heard("next segment", "✓ Move to next segment", True)
    assert tab._heard_list.count() == tab.HEARD_LIMIT
    assert "“next segment” → ✓ Move to next segment" in tab._heard_list.item(0).text()
    assert "phrase 0" not in tab._heard_list.item(tab.HEARD_LIMIT - 1).text()


def test_the_voice_tab_has_no_dictation_controls(app):
    tab = make_tab()
    for gone in ("_model_combo", "_lang_combo", "_duration_spin", "_ptt_combo",
                 "_vocab_dict_edit", "_voice_pause_label", "_cmd_ptt_hotkey_label"):
        assert not hasattr(tab, gone), gone
