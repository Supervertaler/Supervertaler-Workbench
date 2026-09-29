"""Inline tags are protected as single units in the target cell (issue #113)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import inline_codes, tag_protection as tp

TEXT = "Open the <b>valve</b> and press [1}Start{1] now {00108}."


def test_every_tag_form_is_found():
    spans = tp.tag_spans(TEXT)
    assert [TEXT[s:e] for s, e in spans] == ["<b>", "</b>", "[1}", "{1]", "{00108}"]
    assert tp.tag_spans("a < b and [Company] stay text") == []
    joined = "x</" + tp.WORD_JOINER + "1>"          # the display-only joiner after a tag's "/"
    assert tp.tag_spans(joined) == [(1, len(joined))]


def test_user_inline_codes_are_protected_too():
    try:
        inline_codes.set_active([{"pattern": r"%[sd]"}])
        text = "You have %d new <b>messages</b>"
        assert [text[s:e] for s, e in tp.tag_spans(text)] == ["%d", "<b>", "</b>"]
    finally:
        inline_codes.set_active([])


def test_cursor_steps_over_a_tag():
    spans = tp.tag_spans(TEXT)               # <b> is 9..12
    assert tp.snap(spans, 10, forward=True) == 12
    assert tp.snap(spans, 11, forward=False) == 9
    assert tp.snap(spans, 9, forward=True) == 9 and tp.inside(spans, 12) is None


def test_backspace_and_delete_take_the_whole_tag():
    spans = tp.tag_spans(TEXT)
    assert tp.backspace_span(spans, 12) == (9, 12)     # just after <b>
    assert tp.backspace_span(spans, 13) is None        # after the "v" of valve
    assert tp.delete_span(spans, 9) == (9, 12)         # just before <b>
    assert tp.delete_span(spans, 8) is None


def test_a_selection_never_cuts_through_a_tag():
    spans = tp.tag_spans(TEXT)
    assert tp.expand(spans, 11, 18) == (9, 21)          # "b>valv" + part of "</b>"
    assert tp.expand(spans, 13, 17) == (13, 17)         # inside the word only
    assert tp.expand(spans, 18, 11) == (9, 21)          # backwards selection
