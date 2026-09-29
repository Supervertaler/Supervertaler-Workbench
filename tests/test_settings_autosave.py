"""Settings pages save themselves a moment after a user change (issue #214)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtGui import QFocusEvent
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QLabel, QMessageBox,
                             QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from modules import settings_autosave


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def make_page():
    page = QWidget()
    layout = QVBoxLayout(page)
    page.check = QCheckBox("Option")
    page.combo = QComboBox()
    page.combo.addItems(["one", "two"])
    page.text = QPlainTextEdit()
    page.action = QPushButton("Open folder")
    page.save = QPushButton("💾 Save")
    page.save.setProperty("settings_save_button", True)
    page.written = []

    def save():
        page.written.append((page.check.isChecked(), page.combo.currentText(), page.text.toPlainText()))
        QMessageBox.information(page, "Settings Saved", "Saved.")   # must not pop up

    page.save.clicked.connect(save)
    for w in (page.check, page.combo, page.text, page.action, page.save):
        layout.addWidget(w)
    return page


def test_a_user_change_saves_without_a_popup(app, monkeypatch):
    popups = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: popups.append(a)))
    page = make_page()
    notes = []
    [saver] = settings_autosave.install([page], notify=notes.append)
    page.check.click()
    assert saver.pending
    saver.flush()
    assert page.written == [(True, "one", "")]
    assert popups == [] and notes == ["✓ Settings saved"]


def test_programmatic_changes_and_plain_actions_do_not_save(app):
    page = make_page()
    [saver] = settings_autosave.install([page])
    page.check.setChecked(True)          # e.g. a project being loaded
    page.combo.setCurrentIndex(1)
    assert not saver.pending
    page.check.setChecked(False)
    page.combo.setCurrentIndex(0)
    page.action.click()                  # does something, changes no value
    saver.flush()
    assert page.written == []


def test_typing_is_saved_when_the_field_loses_focus(app):
    page = make_page()
    [saver] = settings_autosave.install([page])
    page.text.setPlainText("draft prompt")
    saver.schedule(delay_ms=10_000)      # as if typed: a long debounce is pending
    QApplication.sendEvent(page.text, QFocusEvent(QEvent.Type.FocusOut))
    assert page.written == [(False, "one", "draft prompt")]
    assert not saver.pending


def test_the_save_button_makes_way_for_a_note(app):
    page = make_page()
    settings_autosave.install([page])
    assert page.save.isHidden()
    notes = [w for w in page.findChildren(QLabel) if w.text() == settings_autosave.NOTE_TEXT]
    assert len(notes) == 1 and page.layout().indexOf(notes[0]) == page.layout().indexOf(page.save) - 1
