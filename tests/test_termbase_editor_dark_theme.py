"""Glossary entry editor: a synonym that stops being forbidden gets the theme's
text colour back, not black – black was unreadable on the dark theme (#78)."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import Qt
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
