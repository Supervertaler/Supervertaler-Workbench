"""TermLens pop-up (lone Ctrl tap): Up edits the highlighted entry like E, Down
shows its info like I, Left and Right alone cycle."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from modules.termlens_popup import TermLensPopup
from modules.termlens_widget import TermPopup

MATCHES = [
    {"source_term": "fietslamp", "target_term": "bicycle lamp", "term_id": 11, "termbase_id": 7},
    {"source_term": "behuizing", "target_term": "housing", "term_id": 12, "termbase_id": 7},
    {"source_term": "lichtbron", "target_term": "light source", "term_id": 13, "termbase_id": 7},
]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def popup(app):
    p = TermLensPopup("Fietslamp met een behuizing en een lichtbron", MATCHES, [])
    p.edits = []
    p.edit_requested.connect(lambda term_id, termbase_id: p.edits.append((term_id, termbase_id)))
    p.show()
    app.processEvents()
    yield p
    TermPopup.get_instance().hide()
    if not sip.isdeleted(p):                   # closing deletes it (WA_DeleteOnClose)
        p.close()
    app.processEvents()


def press(widget, key, modifiers=Qt.KeyboardModifier.NoModifier, auto_repeat=False):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key, modifiers, "", auto_repeat))


def info_visible():
    return TermPopup.get_instance().isVisible()


def test_the_first_chip_is_highlighted_when_the_popup_opens(popup):
    # The chips render only once the pop-up is visible; before, nothing was
    # highlighted and Enter / E / I / Up / Down did nothing until an arrow press.
    assert popup._current_index == 0


def test_left_and_right_cycle_up_and_down_do_not(popup):
    press(popup, Qt.Key.Key_Right)
    assert popup._current_index == 1
    press(popup, Qt.Key.Key_Left)
    press(popup, Qt.Key.Key_Left)
    assert popup._current_index == 2          # wraps
    press(popup, Qt.Key.Key_Down)
    press(popup, Qt.Key.Key_Down)
    assert popup._current_index == 2


def test_up_edits_the_highlighted_entry_like_e(app, popup):
    press(popup, Qt.Key.Key_Right)
    press(popup, Qt.Key.Key_Up)
    assert not popup.isVisible()
    QTest.qWait(20)
    assert popup.edits == [(12, 7)]


def test_down_toggles_the_info_like_i(popup):
    assert not info_visible()
    press(popup, Qt.Key.Key_Down)
    assert info_visible()
    assert popup.isVisible()                   # the pop-up stays open
    press(popup, Qt.Key.Key_Down)
    assert not info_visible()
    press(popup, Qt.Key.Key_I)
    assert info_visible()
    press(popup, Qt.Key.Key_Down)
    assert not info_visible()


def test_a_held_down_down_key_does_not_flicker_the_info(popup):
    press(popup, Qt.Key.Key_Down)
    for _ in range(5):
        press(popup, Qt.Key.Key_Down, auto_repeat=True)
    assert info_visible()


def test_numpad_arrows_count_as_plain_arrows(popup):
    press(popup, Qt.Key.Key_Down, Qt.KeyboardModifier.KeypadModifier)
    assert info_visible()


def test_up_with_a_modifier_does_not_edit(app, popup):
    # Same rule as E: with Shift, Ctrl or Alt held it is "another key", which closes the pop-up.
    press(popup, Qt.Key.Key_Up, Qt.KeyboardModifier.ShiftModifier)
    assert not popup.isVisible()
    QTest.qWait(20)
    assert popup.edits == []


def test_tab_cycles_and_keeps_the_arrows_in_the_popup(popup):
    # Tab used to move focus into the inner scroll area, which then took the arrow keys.
    QTest.keyClick(popup, Qt.Key.Key_Tab)
    assert popup._current_index == 1
    assert QApplication.focusWidget() is popup
    QTest.keyClick(popup, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
    assert popup._current_index == 0
    QTest.keyClick(popup, Qt.Key.Key_Down)
    assert info_visible()


def test_the_footer_shows_the_new_keys(popup):
    hint = popup._hint_label.text()
    assert "← → cycle" in hint
    assert "↑/E edit" in hint
    assert "↓/I info" in hint
