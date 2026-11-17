"""The files that make up the main program, for tests that search its source.

Supervertaler.py is being split up (Nov 2026): the main window's methods move,
one file per job, into modules/main_window/, and other classes into modules/.
A test that searched Supervertaler.py alone would quietly check less and less,
so such tests search all of these instead.
"""

from __future__ import annotations

import glob
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Moved out of Supervertaler.py in step 1 of the split.
_MOVED_MODULES = [
    "tag_text.py", "segment_model.py", "grid_editors.py", "main_dialogs.py",
    "translation_workers.py", "superlookup_tab.py", "colour_checkboxes.py",
]


def app_files() -> list[str]:
    """Paths relative to the repository root."""
    files = ["Supervertaler.py"]
    files += [os.path.join("modules", name) for name in _MOVED_MODULES]
    files += sorted(os.path.relpath(p, ROOT) for p in
                    glob.glob(os.path.join(ROOT, "modules", "main_window", "*.py")))
    return files


def app_source() -> str:
    parts = []
    for rel in app_files():
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            parts.append(f.read())
    return "\n".join(parts)
