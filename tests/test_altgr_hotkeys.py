"""AltGr must not trigger Ctrl+Alt global hotkeys (issue #243).

Windows reports AltGr as Ctrl+Alt, so a RegisterHotKey on Ctrl+Alt+L also
fires on AltGr+L and swallowed the Polish "ł". The decision whether to type the
character instead is tested here against a fake keyboard; the Windows calls
behind the real one can only run on Windows.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from modules import platform_helpers as ph

CTRL_ALT = ph._MOD_CONTROL | ph._MOD_ALT
VK_L = 0x4C


class FakeKeyboard:
    def __init__(self, right_alt=True, text="ł", fail=False):
        self.right_alt, self.text, self.fail = right_alt, text, fail

    def right_alt_down(self):
        if self.fail:
            raise OSError("no keyboard")
        return self.right_alt

    def altgr_text(self, vk):
        return self.text


def test_altgr_on_a_polish_layout_types_the_letter():
    assert ph.altgr_character(CTRL_ALT, VK_L, FakeKeyboard(text="ł")) == "ł"
    assert ph.altgr_character(CTRL_ALT | ph._MOD_NOREPEAT, VK_L, FakeKeyboard(text="@")) == "@"


@pytest.mark.parametrize("mods, keyboard", [
    (CTRL_ALT, FakeKeyboard(right_alt=False)),           # left Ctrl + left Alt: the hotkey
    (CTRL_ALT, FakeKeyboard(text="")),                   # AltGr types nothing here (US layout)
    (CTRL_ALT, FakeKeyboard(text="\x0c")),               # a control character is not text
    (CTRL_ALT, FakeKeyboard(text=" ")),
    (CTRL_ALT, FakeKeyboard(text="´e")),                 # dead-key residue, not one character
    (CTRL_ALT | ph._MOD_SHIFT, FakeKeyboard()),          # AltGr never adds Shift to the chord
    (ph._MOD_ALT, FakeKeyboard()),                       # Alt alone is not AltGr
    (ph._MOD_CONTROL | ph._MOD_WIN, FakeKeyboard()),
    (CTRL_ALT, FakeKeyboard(fail=True)),                 # any failure: run the hotkey as before
])
def test_everything_else_still_runs_the_hotkey(mods, keyboard):
    assert ph.altgr_character(mods, VK_L, keyboard) is None


def test_unknown_key_and_non_windows_are_left_alone():
    assert ph.altgr_character(CTRL_ALT, None, FakeKeyboard()) is None
    if not ph.IS_WINDOWS:
        assert ph.altgr_character(CTRL_ALT, VK_L) is None
        assert ph.send_unicode_text("ł") is False


def test_hotkey_parser_gives_the_altgr_chord_for_ctrl_alt_bindings():
    mods, vk = ph.GlobalHotkeyManager._parse_shortcut_winapi("Ctrl+Alt+L")
    assert ph.is_altgr_chord(mods) and vk == VK_L
    mods, _ = ph.GlobalHotkeyManager._parse_shortcut_winapi("Ctrl+Shift+Space")
    assert not ph.is_altgr_chord(mods)
