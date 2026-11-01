"""Clipboard Manager: the main switch and the Menu-only layout.

With "Capture clipboard history" off, the tab keeps only its Menu column
(Snippets, Special Characters, Text Conversions, QuickLauncher Prompts). With
"Use the Clipboard Manager" off, the main window hides the tab - but a widget
built before that must stop reading the clipboard too.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from modules.clipboard_manager_widget import ClipboardManagerWidget


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _StubApp:
    """Just enough of the main window for the widget: its settings."""

    def __init__(self, **privacy):
        self.privacy = privacy

    def load_clipboard_privacy_settings(self):
        return dict(self.privacy)


def _make(app, **privacy):
    stub = _StubApp(**privacy)
    widget = ClipboardManagerWidget(stub)
    widget.resize(900, 400)
    widget.show()
    app.processEvents()
    return stub, widget


def test_capture_off_leaves_only_the_menu(app):
    stub, widget = _make(app, capture_enabled=False)
    try:
        assert widget._text_col.isHidden()
        assert widget._image_col.isHidden()
        assert not widget._action_tree.isHidden()
        assert widget._capture_state_label.isVisible()
        assert widget._hint_label.text() == widget._HINT_MENU_ONLY

        # Switched back on in Settings: the history columns return live.
        stub.privacy['capture_enabled'] = True
        widget.refresh_privacy_settings()
        app.processEvents()
        assert not widget._text_col.isHidden()
        assert not widget._image_col.isHidden()
        assert not widget._capture_state_label.isVisible()
        assert widget._hint_label.text() == widget._HINT_HISTORY
    finally:
        widget.deleteLater()


def test_default_layout_is_unchanged(app):
    # No saved settings at all - an install that never opened the page.
    _, widget = _make(app)
    try:
        assert not widget._text_col.isHidden()
        assert not widget._image_col.isHidden()
        assert not widget._capture_state_label.isVisible()
    finally:
        widget.deleteLater()


@pytest.mark.parametrize("manager, capture, expected", [
    (True,  True,  1),
    (True,  False, 0),
    (False, True,  0),
    (False, False, 0),
])
def test_clipboard_is_read_only_when_both_switches_are_on(app, manager, capture, expected):
    _, widget = _make(app, manager_enabled=manager, capture_enabled=capture)
    try:
        QApplication.clipboard().setText(f"clip {manager} {capture}")
        app.processEvents()
        assert widget._text_list.count() == expected
    finally:
        widget.deleteLater()


def test_switching_the_manager_off_stops_a_live_widget(app):
    stub, widget = _make(app)
    try:
        QApplication.clipboard().setText("before")
        app.processEvents()
        assert widget._text_list.count() == 1

        stub.privacy['manager_enabled'] = False
        widget.refresh_privacy_settings()
        QApplication.clipboard().setText("after")
        app.processEvents()
        assert widget._text_list.count() == 1
    finally:
        widget.deleteLater()


def test_summon_lands_on_the_menu_when_history_is_hidden(app):
    _, widget = _make(app, capture_enabled=False)
    try:
        widget.activateWindow()
        widget.focus_default_column()
        app.processEvents()
        assert QApplication.focusWidget() is widget._action_tree
    finally:
        widget.deleteLater()
