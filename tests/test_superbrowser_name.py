"""The name on screen is "SuperBrowser"; what holds the logins keeps its name.

The Qt storage of the standalone test window is filed under the application
name, and the profiles under superbrowser_profiles: renaming either would
sign the translator out of ChatGPT, Claude and Gemini.
"""

from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def test_the_visible_name_has_a_capital_b():
    assert '"🌐 Super&Browser..."' in read("modules", "main_window", "window_shell.py")
    assert '"🌐 SuperBrowser – Supervertaler"' in read("Supervertaler.py")
    assert '"🌐 SuperBrowser - Multi-Chat AI Browser"' in read("modules", "superbrowser.py")


def test_no_old_spelling_is_left_on_screen():
    for parts in (("modules", "main_window", "window_shell.py"), ("Supervertaler.py",),
                  ("modules", "superbrowser.py")):
        text = read(*parts).replace("SuperbrowserWidget", "").replace(
            'setApplicationName("Superbrowser")', "")
        assert "Superbrowser" not in text and "Super&browser" not in text, parts


def test_what_holds_the_logins_keeps_its_name():
    module = read("modules", "superbrowser.py")
    assert 'setApplicationName("Superbrowser")' in module
    assert "superbrowser_profiles" in module
    assert "class SuperbrowserWidget(" in module
