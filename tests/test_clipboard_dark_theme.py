"""Clipboard tab: clip text stays readable in the dark theme (issue #78).

The dark-style adapter rewrites stylesheets, so a clip list whose rows carry a
fixed near-black per-item colour showed dark text on a dark list: only the
selected row, coloured by the stylesheet, could be read. This renders the list
the way the Clipboard tab builds it and measures the contrast of each row.
"""

from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QListWidget, QListWidgetItem

from modules.clipboard_manager_widget import ClipboardManagerWidget
from modules.dark_style_adapter import apply_to_tree
from modules.theme_manager import ThemeManager


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _luminance(c: QColor) -> float:
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())


def _contrast(a: QColor, b: QColor) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _row_contrast(lst: QListWidget, image, row: int) -> float:
    """Contrast between a row's background (its commonest pixel) and its
    most different pixel, i.e. the core of the text glyphs."""
    rect = lst.visualItemRect(lst.item(row)).translated(lst.viewport().pos())
    pixels = [QColor(image.pixel(x, y))
              for x in range(rect.left() + 2, rect.right() - 2)
              for y in range(rect.top() + 2, rect.bottom() - 2)]
    background = QColor(Counter(p.rgb() for p in pixels).most_common(1)[0][0])
    return max(_contrast(background, p) for p in pixels)


def _render_clip_list(app, theme_name, tmp_path):
    (tmp_path / "workbench" / "settings").mkdir(parents=True)
    themes = ThemeManager(tmp_path)
    themes.set_theme(theme_name)
    themes.apply_theme(app)

    lst = QListWidget()
    lst.setStyleSheet(ClipboardManagerWidget._LIST_STYLESHEET.fget(None))
    for text, pasted in (("An ordinary clip", False), ("A clip already pasted", True),
                         ("Another ordinary clip", False)):
        item = QListWidgetItem(text)
        ClipboardManagerWidget._apply_style(ClipboardManagerWidget, item, pasted)
        lst.addItem(item)
    lst.resize(400, 160)
    apply_to_tree(lst, themes.current_theme)
    lst.show()
    app.processEvents()
    return lst, lst.grab().toImage()


@pytest.mark.parametrize("theme_name", ["Dark", "Light (Default)"])
def test_clip_rows_are_readable(app, theme_name, tmp_path):
    try:
        lst, image = _render_clip_list(app, theme_name, tmp_path)
        normal = [_row_contrast(lst, image, r) for r in (0, 2)]
        pasted = _row_contrast(lst, image, 1)
        lst.close()
    finally:
        app.setStyleSheet("")
    # Ordinary clips: plainly readable. Pasted clips: deliberately dimmer
    # (greyed out), but still legible.
    assert min(normal) >= 4.5, f"{theme_name}: ordinary clip contrast {normal}"
    assert 2.0 <= pasted < min(normal), f"{theme_name}: pasted clip contrast {pasted}"
