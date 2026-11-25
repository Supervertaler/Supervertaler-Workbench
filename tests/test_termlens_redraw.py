"""TermLens is redrawn in about half the time (Nov 2026).

Every move to a segment rebuilds TermLens, in both panels. Two things made
that slow. A word without a term, which is most of a segment, was three
widgets with a stylesheet each; it is now one painted widget of the same
size. And blocks added to a container on screen were shown one by one, with
Qt laying out all of them again after each (32 words: 32 passes); the
container is now hidden while it is rebuilt and laid out once.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget
from PyQt6.QtGui import QFont

import modules.termlens_widget as tw

SOURCE = ("In een uitvoeringsvorm omvat het bovenwerk een schacht en een zool, waarbij de schacht "
          "is voorzien van een sluiting die zich uitstrekt van de neus tot aan de hiel van de schoen.")
TERMS = [{"source": word, "translation": f"t{i}", "term_id": i, "termbase_id": 7,
          "termbase_name": "Patents", "ranking": i % 2}
         for i, word in enumerate(["uitvoeringsvorm", "bovenwerk", "schacht", "zool"])]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def lens(app):
    widget = tw.TermLensWidget(log_callback=lambda *a: None)
    widget.resize(900, 300)
    widget.show()
    QApplication.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()


def blocks(widget):
    layout = widget.terms_layout
    return [layout.itemAt(i).widget() for i in range(layout.count())]


def old_plain_block(text, family, size, bold):
    """A word without a term as it was built before: line, word, dot."""
    block = QWidget()
    layout = QVBoxLayout(block)
    layout.setContentsMargins(1, 0, 1, 1)
    layout.setSpacing(0)
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet("background-color: #CCCCCC; border: none;")
    layout.addWidget(line)
    word = QLabel(text)
    font = QFont(family)
    font.setPointSize(size)
    font.setBold(bold)
    word.setFont(font)
    word.setAlignment(Qt.AlignmentFlag.AlignCenter)
    word.setStyleSheet("QLabel { color: #333; padding: 1px 3px; background-color: transparent; border: none; }")
    layout.addWidget(word)
    dot = QLabel("·")
    dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
    dot.setStyleSheet("color: #ddd; font-size: 8px;")
    layout.addWidget(dot)
    return block


# --- a word without a term ------------------------------------------------

def test_a_word_without_a_term_has_no_child_widgets(app):
    block = tw.TermBlock("schacht", [], None)
    assert block.findChildren(QWidget) == [] and block.layout() is None
    assert block.source_label is None


def test_a_word_with_a_term_is_built_as_before(app):
    block = tw.TermBlock("schacht", [{"target_term": "shaft", "term_id": 1, "termbase_id": 7}], None)
    assert isinstance(block.source_label, QLabel) and block.source_label.text() == "schacht"
    assert block.target_container is not None


@pytest.mark.parametrize("family,size,bold", [("Segoe UI", 10, False), ("Segoe UI", 14, True), ("Arial", 7, False)])
@pytest.mark.parametrize("text", ["een", "uitvoeringsvorm,", "(typisch)", "W", "kg/l", "i"])
def test_the_painted_word_is_the_size_the_three_widgets_were(app, text, family, size, bold):
    new = tw.TermBlock(text, [], None, font_size=size, font_family=family, font_bold=bold)
    old = old_plain_block(text, family, size, bold)
    assert new.sizeHint() == old.sizeHint()
    assert new.minimumSizeHint() == new.sizeHint()


def test_the_painted_word_draws_something(app):
    block = tw.TermBlock("schacht", [], None)
    block.resize(block.sizeHint())
    image = block.grab().toImage()
    colours = {image.pixel(x, y) for x in range(image.width()) for y in range(image.height())}
    assert len(colours) > 2                       # line, word, dot on a background


def test_the_dark_theme_gives_a_word_its_light_colours(app):
    dark = SimpleNamespace(current_theme=SimpleNamespace(name="Dark"))
    block = tw.TermBlock("schacht", [], None, theme_manager=dark)
    assert [c.name() for c in block._plain_colors] == ["#555555", "#ffffff", "#666666"]
    assert block.bg_color == "#2A2A2A"


# --- the redraw -----------------------------------------------------------

def layout_passes(monkeypatch, widget, source, terms):
    """How often all the blocks are placed while one redraw settles."""
    passes = []
    real = tw.FlowLayout.doLayout

    def counting(self, rect, test_only):
        if not test_only:
            passes.append(len(self.itemList))
        return real(self, rect, test_only)

    monkeypatch.setattr(tw.FlowLayout, "doLayout", counting)
    widget.update_with_matches(source, terms, [])
    QApplication.processEvents()
    return passes


def test_a_redraw_lays_the_blocks_out_once_not_once_per_word(lens, monkeypatch):
    passes = layout_passes(monkeypatch, lens, SOURCE, TERMS)
    assert len(blocks(lens)) == 32
    assert len(passes) <= 3                       # it was 32


def test_a_segment_ten_times_as_long_is_still_laid_out_once(lens, monkeypatch):
    passes = layout_passes(monkeypatch, lens, " ".join([SOURCE] * 10), TERMS)
    assert len(blocks(lens)) == 320
    assert len(passes) <= 3


def test_the_container_is_on_screen_again_after_a_redraw(lens):
    lens.update_with_matches(SOURCE, TERMS, [])
    assert lens.terms_container.isVisible()
    assert lens.info_label.text() == "✓ Found 5 terms in 32 words"     # "schacht" is there twice


@pytest.mark.parametrize("source,label", [("", "No segment selected"), ("   ", "No segment selected")])
def test_the_container_is_back_when_there_is_nothing_to_show(lens, source, label):
    lens.update_with_matches(SOURCE, TERMS, [])
    lens.update_with_matches(source, [], [])
    assert lens.terms_container.isVisible() and blocks(lens) == []
    assert lens.info_label.text() == label


def test_the_container_is_back_when_building_fails(lens, monkeypatch):
    def broken(self, text, matches):
        raise RuntimeError("tokenizer broke")

    monkeypatch.setattr(tw.TermLensWidget, "tokenize_with_multiword_terms", broken)
    with pytest.raises(RuntimeError):
        lens.update_with_matches(SOURCE, TERMS, [])
    assert lens.terms_container.isVisible()       # not left hidden for good
    monkeypatch.undo()
    lens.update_with_matches(SOURCE, TERMS, [])   # and the next segment draws
    assert len(blocks(lens)) == 32


def test_a_hidden_panel_still_waits_and_draws_when_shown(lens):
    lens.hide()
    lens.update_with_matches(SOURCE, TERMS, [])
    assert blocks(lens) == [] and lens._pending_update is not None
    lens.show()
    QApplication.processEvents()
    assert len(blocks(lens)) == 32 and lens._pending_update is None


# --- what the rest of the app reads from the blocks -----------------------

def test_shortcut_numbers_and_the_popups_list_are_unchanged(lens):
    lens.update_with_matches(SOURCE, TERMS, [])
    assert lens.shortcut_terms == {1: "t0", 2: "t1", 3: "t2", 4: "t3"}
    with_terms = lens.get_term_blocks(only_with_matches=True)
    assert [b.source_text for b in with_terms] == ["uitvoeringsvorm", "bovenwerk", "schacht", "zool,", "schacht"]
    assert len(lens.get_term_blocks()) == 32


def test_a_new_font_resizes_the_painted_words_too(lens):
    lens.update_with_matches(SOURCE, TERMS, [])
    plain = next(b for b in blocks(lens) if not b.translations)
    before = plain.sizeHint()
    lens.set_font_settings("Segoe UI", 16, True)
    after = plain.sizeHint()
    assert after.width() > before.width() and after.height() > before.height()
    with_term = next(b for b in blocks(lens) if b.translations)
    assert with_term.source_label.font().pointSize() == 16
