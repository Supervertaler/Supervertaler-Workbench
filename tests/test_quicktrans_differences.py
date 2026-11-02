"""QuickTrans marks where each MT result differs from the top one (#208).

Only the label changes: a click still inserts the engine's exact translation.
Results arrive one by one, and every arrival refreshes the marks, so a row is
compared again only when the top result changes; long results are not
compared at all (a character diff of 3,000 characters takes half a second).
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from modules import quicktrans as qt


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _item(text, provider="DeepL", error=False):
    return qt.MTSuggestionItem(1, qt.MTSuggestion(provider, provider[:2].upper(), text, error))


def test_the_differing_characters_are_marked(app):
    top = qt.MTSuggestion("Google Translate", "GT", "Open de kraan langzaam.")
    item = _item("Open de klep langzaam.")
    item.set_reference(top)
    label = item.text_label
    assert label.textFormat() == Qt.TextFormat.RichText
    assert "<span style='background-color" in label.text() and ">lep</span>" in label.text()
    assert "Google Translate" in label.toolTip()
    assert item.suggestion.translation == "Open de klep langzaam."      # what a click inserts


def test_markup_in_a_translation_is_shown_not_rendered(app):
    item = _item("Druk op <b>Start</b>  nu & klaar")
    assert item.text_label.textFormat() == Qt.TextFormat.PlainText
    item.set_reference(qt.MTSuggestion("Google Translate", "GT", "Druk op <b>Start</b>  & klaar"))
    text = item.text_label.text()
    assert "&lt;b&gt;Start&lt;/b&gt;" in text and "&amp;" in text
    assert "white-space: pre-wrap" in text                             # double spaces kept


def test_errors_and_switching_off_show_plain_text(app):
    top = qt.MTSuggestion("Google Translate", "GT", "Open de kraan.")
    error = _item("[DeepL error: 403]", error=True)
    error.set_reference(top)
    assert error.text_label.textFormat() == Qt.TextFormat.PlainText
    item = _item("Open de klep.")
    item.set_reference(top)
    item.set_reference(None)                                           # Δ switched off
    assert item.text_label.textFormat() == Qt.TextFormat.PlainText
    assert item.text_label.text() == "Open de klep." and item.text_label.toolTip() == ""
    assert _item("x").set_reference(qt.MTSuggestion("G", "GT", "y", True)) is None  # error reference


def test_a_long_result_is_not_compared(app, monkeypatch):
    calls = []
    monkeypatch.setattr("modules.tm_diff.char_diff", lambda *a: calls.append(a) or [])
    long_text = "woord " * 300
    item = _item(long_text)
    item.set_reference(qt.MTSuggestion("Google Translate", "GT", long_text + "!"))
    assert calls == []
    assert item.text_label.textFormat() == Qt.TextFormat.PlainText
    assert "Too long" in item.text_label.toolTip()


class _Panel(qt.QuickTransProviderMixin, QWidget):
    def __init__(self, show=True):
        QWidget.__init__(self)
        self.results_layout = QVBoxLayout(self)
        self.saved = {"quicktrans_show_differences": show}
        self.parent_app = self

    def load_general_settings(self):
        return dict(self.saved)

    def save_general_settings(self, settings):
        self.saved = dict(settings)


def test_rows_arriving_one_by_one_are_each_compared_once(app, monkeypatch):
    from modules import tm_diff
    calls = []
    real = tm_diff.char_diff
    monkeypatch.setattr(tm_diff, "char_diff", lambda a, b: calls.append(a) or real(a, b))
    panel = _Panel()
    for name, text in (("Google Translate", "Open de kraan."), ("DeepL", "Open de klep."),
                       ("Microsoft", "Open het ventiel."), ("ModernMT", "Draai de kraan open.")):
        panel.results_layout.addWidget(_item(text, name))
        panel._refresh_differences()
    assert sorted(calls) == sorted(["Open de klep.", "Open het ventiel.", "Draai de kraan open."])
    first = panel.results_layout.itemAt(0).widget()
    assert first.text_label.textFormat() == Qt.TextFormat.PlainText  # the reference itself


def test_the_reference_is_the_first_usable_result(app):
    panel = _Panel()
    for item in (_item("[error]", "Google Translate", error=True), _item("   ", "Microsoft"),
                 _item("Open de kraan.", "DeepL"), _item("Open de klep.", "ModernMT")):
        panel.results_layout.addWidget(item)
    panel._refresh_differences()
    last = panel.results_layout.itemAt(3).widget()
    assert "DeepL" in last.text_label.toolTip()


def test_the_delta_button_is_remembered_and_switches_the_marks(app):
    panel = _Panel(show=True)
    btn = panel._make_differences_button("", 22)
    assert btn.isChecked()
    panel.results_layout.addWidget(_item("Open de kraan.", "DeepL"))
    other = _item("Open de klep.", "ModernMT")
    panel.results_layout.addWidget(other)
    panel._refresh_differences()
    assert other.text_label.textFormat() == Qt.TextFormat.RichText
    btn.setChecked(False)
    assert panel.saved["quicktrans_show_differences"] is False
    assert other.text_label.textFormat() == Qt.TextFormat.PlainText
    assert _Panel(show=False)._make_differences_button("", 22).isChecked() is False


def test_settings_that_cannot_be_read_or_saved_keep_it_on(app):
    class Broken(_Panel):
        def load_general_settings(self):
            raise OSError("settings file locked")

        def save_general_settings(self, settings):
            raise OSError("settings file locked")

    panel = Broken()
    assert panel._show_differences() is True
    panel._set_show_differences(False)                                  # no exception
