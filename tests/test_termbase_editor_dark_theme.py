"""Glossary entry editor: a synonym that stops being forbidden gets the theme's
text colour back, not black – black was unreadable on the dark theme (#78)."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QLineEdit, QListWidget, QListWidgetItem

from modules.termbase_entry_editor import TermbaseEntryEditor


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_promoting_a_synonym_resets_its_colour_to_the_theme(app):
    synonyms = QListWidget()
    primary = QLineEdit("old primary")
    item = QListWidgetItem("MEKO")
    item.setData(Qt.ItemDataRole.UserRole, {"text": "MEKO", "forbidden": False})
    item.setForeground(QColor("#d32f2f"))          # left over from a forbidden entry
    synonyms.addItem(item)
    TermbaseEntryEditor._promote_synonym_to_primary(SimpleNamespace(), synonyms, primary, item)
    assert primary.text() == "MEKO"
    assert item.data(Qt.ItemDataRole.ForegroundRole) is None     # theme colour, not black


class _MenuChoosingFirst:
    """Stands in for QMenu: 'choosing' its first action (Mark as …)."""

    def __init__(self, *args):
        self._actions = []

    def addAction(self, text):
        self._actions.append(text)
        return text

    def addSeparator(self):
        pass

    def exec(self, *args):
        return self._actions[0]


def _synonym_item(text, forbidden):
    item = QListWidgetItem(("🚫 " if forbidden else "") + text)
    item.setData(Qt.ItemDataRole.UserRole, {"text": text, "forbidden": forbidden})
    if forbidden:
        item.setForeground(QColor("#d32f2f"))
    return item


@pytest.mark.parametrize("side", ["source", "target"])
def test_marking_a_synonym_allowed_resets_its_colour_to_the_theme(app, monkeypatch, side):
    import modules.termbase_entry_editor as editor_module
    monkeypatch.setattr(editor_module, "QMenu", _MenuChoosingFirst)
    synonyms = QListWidget()
    item = _synonym_item("MEKO", forbidden=True)
    synonyms.addItem(item)
    synonyms.setCurrentItem(item)
    fake = SimpleNamespace(**{f"{side}_synonym_list": synonyms})
    getattr(TermbaseEntryEditor, f"show_{side}_synonym_context_menu")(fake, QPoint(0, 0))
    assert item.data(Qt.ItemDataRole.UserRole)["forbidden"] is False
    assert item.text() == "MEKO"
    assert item.data(Qt.ItemDataRole.ForegroundRole) is None     # theme colour, not black


@pytest.mark.parametrize("side", ["source", "target"])
def test_marking_a_synonym_forbidden_still_turns_it_red(app, monkeypatch, side):
    import modules.termbase_entry_editor as editor_module
    monkeypatch.setattr(editor_module, "QMenu", _MenuChoosingFirst)
    synonyms = QListWidget()
    item = _synonym_item("MEKO", forbidden=False)
    synonyms.addItem(item)
    synonyms.setCurrentItem(item)
    fake = SimpleNamespace(**{f"{side}_synonym_list": synonyms})
    getattr(TermbaseEntryEditor, f"show_{side}_synonym_context_menu")(fake, QPoint(0, 0))
    assert item.data(Qt.ItemDataRole.UserRole)["forbidden"] is True
    assert item.foreground().color().name() == "#d32f2f"


def test_no_synonym_is_ever_painted_fixed_black():
    # The dark-style adapter rewrites stylesheets but cannot see per-item
    # colours, so a fixed black foreground is unreadable there.
    source = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "modules", "termbase_entry_editor.py"), encoding="utf-8").read()
    assert "QColor('#000000')" not in source and 'QColor("#000000")' not in source
