"""Dark theme: inline light stylesheets are made readable (issue #78)."""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from modules.dark_style_adapter import adapt_stylesheet, apply_to_tree, is_dark_theme
from modules.theme_manager import ThemeManager

DARK = ThemeManager.PREDEFINED_THEMES["Dark"]
LIGHT = ThemeManager.PREDEFINED_THEMES["Light (Default)"]


def lightness(hex_color):
    return QColor(hex_color).lightnessF()


def colours(css, prop):
    m = re.search(rf"(?<![\w-]){prop}\s*:\s*([^;}}]+)", css)
    return m.group(1).strip()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_theme_detection(app):
    assert is_dark_theme(DARK) and not is_dark_theme(LIGHT)


def test_pale_backgrounds_become_dark_and_dark_text_light(app):
    css = ("QWidget { background-color: #f5f5f5; border-right: 1px solid #ddd; }"
           "QLabel { color: #333; background: white; }")
    out = adapt_stylesheet(css, DARK)
    assert colours(out, "background-color") == QColor(DARK.window_bg).name()
    assert colours(out, "background") == QColor(DARK.base).name()
    assert lightness(colours(out, "color")) > 0.8
    assert "#505050" in out  # the light border became the theme border


def test_tinted_info_boxes_keep_their_hue(app):
    out = adapt_stylesheet("background-color: #e3f2fd; color: #1565C0;", DARK)
    bg, fg = QColor(colours(out, "background-color")), QColor(colours(out, "color"))
    assert bg.lightnessF() < 0.25 and 190 <= bg.hslHue() <= 220      # dark blue box
    assert fg.lightnessF() > 0.6 and 190 <= fg.hslHue() <= 220       # light blue text


def test_buttons_and_already_dark_colours_are_left_alone(app):
    css = ("QPushButton { background-color: #FF9800; color: white; border: none; }"
           "QPushButton:hover { background-color: #2196F3; }"
           "QFrame { background-color: #4CAF50; } QLabel { color: #E0E0E0; }")
    assert adapt_stylesheet(css, DARK) == css


def test_pastel_button_backgrounds_become_dark_tints(app):
    # The amber Replace buttons: light theme text on #FFE082 was unreadable
    out = adapt_stylesheet("QPushButton { background-color: #FFE082; font-weight: bold; }"
                           "QPushButton:hover { background-color: #FFCC33; }", DARK)
    normal, hover = re.findall(r"background-color:\s*(#[0-9a-f]{6})", out)
    assert QColor(normal).lightnessF() < 0.25 and 35 <= QColor(normal).hslHue() <= 55
    assert QColor(hover).lightnessF() < 0.25 and normal != hover


def test_rgba_named_and_non_colour_values(app):
    out = adapt_stylesheet("background: rgba(255, 255, 255, 128); border-radius: 4px; "
                           "font-size: 9pt; color: black;", DARK)
    assert "rgba(" in out and "border-radius: 4px" in out and "font-size: 9pt" in out
    assert colours(out, "color") == QColor(DARK.text).name()


def test_widgets_are_adapted_and_restored(app):
    root = QWidget()
    label = QLabel("x", root)
    label.setStyleSheet("background-color: #fff8e1; color: #333;")
    apply_to_tree(root, DARK)
    assert "#fff8e1" not in label.styleSheet()
    apply_to_tree(root, DARK)  # idempotent
    apply_to_tree(root, LIGHT)
    assert label.styleSheet() == "background-color: #fff8e1; color: #333;"
    # A stylesheet set by the app after adapting is adapted afresh
    apply_to_tree(root, DARK)
    label.setStyleSheet("background: #e8f5e9;")
    apply_to_tree(root, DARK)
    assert "#e8f5e9" not in label.styleSheet()
