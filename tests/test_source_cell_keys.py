"""Source-cell keys with invisible characters shown (Nov 2026).

ReadOnlyGridTextEditor (the source cell) defined keyPressEvent twice. The
second, a four-line "do the default", silently replaced the first, so two
things in it never ran: copying put the "·" space markers on the clipboard
instead of spaces, and Ctrl+Left/Right treated "·" as part of a word. Every
other key in that first copy is handled by event() or the application-wide
filters, so only these two were kept. Two more methods were defined twice
in the same class; the copies that never ran are gone too.
"""

from __future__ import annotations

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget

from modules.grid_editors import ReadOnlyGridTextEditor

SPACE = "·​"          # how a space is shown when invisibles are on


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


class FakeMainWindow(QWidget):
    """Just what the source cell asks its main window for."""
    invisible_display_settings = {"spaces": True}

    def go_to_first_segment(self):          # how the cell recognises its main window
        pass

    @staticmethod
    def reverse_invisible_replacements(text):
        return text.replace(SPACE, " ")


@pytest.fixture
def cell(app):
    mw = FakeMainWindow()
    editor = ReadOnlyGridTextEditor(f"the{SPACE}red{SPACE}valve", parent=mw, row=0)
    editor.show()
    yield editor
    mw.close()


def test_copy_puts_real_spaces_on_the_clipboard(cell):
    cell.selectAll()
    QApplication.clipboard().clear()
    QTest.keyClick(cell, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == "the red valve"


def test_ctrl_right_jumps_a_whole_word_over_the_space_markers(cell):
    c = cell.textCursor(); c.setPosition(0); cell.setTextCursor(c)
    QTest.keyClick(cell, Qt.Key.Key_Right, Qt.KeyboardModifier.ControlModifier)
    assert cell.textCursor().position() == len("the")
    QTest.keyClick(cell, Qt.Key.Key_Right, Qt.KeyboardModifier.ControlModifier)
    assert cell.textCursor().position() == len(f"the{SPACE}red")


def test_ctrl_left_jumps_back_to_the_start_of_the_word(cell):
    end = len(cell.toPlainText())
    c = cell.textCursor(); c.setPosition(end); cell.setTextCursor(c)
    QTest.keyClick(cell, Qt.Key.Key_Left, Qt.KeyboardModifier.ControlModifier)
    assert cell.textCursor().position() == len(f"the{SPACE}red{SPACE}")


def test_with_invisibles_off_copy_is_left_to_qt(cell):
    FakeMainWindow.invisible_display_settings = {"spaces": False}
    try:
        cell.setPlainText("plain text")
        cell.selectAll()
        QTest.keyClick(cell, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        assert QApplication.clipboard().text() == "plain text"
    finally:
        FakeMainWindow.invisible_display_settings = {"spaces": True}


def test_no_grid_editor_defines_a_method_twice():
    # A second definition silently replaces the first (the #255 bug, and this one).
    tree = ast.parse(open(os.path.join(ROOT, "modules", "grid_editors.py"), encoding="utf-8").read())
    twice = {}
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        names = [f.name for f in cls.body if isinstance(f, ast.FunctionDef)]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            twice[cls.name] = dupes
    assert twice == {}
