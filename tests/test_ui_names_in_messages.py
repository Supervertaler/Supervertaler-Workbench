"""Messages must name menus and tabs that exist.

The "File" menu has been "Project" since v1.10.182, and the TMs and termbases
moved from a "Resources" tab to the main "💾 TMs" and "🏷️ Termbases" tabs, but
several messages still sent users to the old places.
"""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _code_lines(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            if not line.lstrip().startswith("#"):
                yield number, line


def test_no_message_points_to_a_menu_or_tab_that_is_gone():
    stale = re.compile(r"Resources\s*(→|->)|File\s*(→|->)\s*Import")
    found = [f"{path}:{n}: {line.strip()}"
             for path in ("Supervertaler.py", os.path.join("scripts", "sv_tm_diagnose.py"))
             for n, line in _code_lines(path) if stale.search(line)]
    assert found == []


def test_the_tabs_the_messages_name_exist():
    source = open(os.path.join(ROOT, "Supervertaler.py"), encoding="utf-8").read()
    assert 'self.main_tabs.addTab(tm_tab_top, "💾 TMs")' in source
    assert 'self.main_tabs.addTab(termbase_tab_top, "🏷️ Termbases")' in source
