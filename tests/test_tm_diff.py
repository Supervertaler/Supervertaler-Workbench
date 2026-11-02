"""Match Panel TM diff: tags are compared as tokens and spacing is kept (issue #117)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.tm_diff import char_diff, diff_spans, tokenize, word_diff


def rebuild(parts, kinds):
    return "".join(text for kind, text in parts if kind in kinds)


def test_only_the_word_inside_a_tag_pair_is_marked():
    parts = word_diff("Open the <b>valve</b> slowly.", "Open the <b>tap</b> slowly.")
    assert ("delete", "tap") in parts and ("add", "valve") in parts
    assert not any("<b>" in text or "</b>" in text for kind, text in parts if kind != "normal")


def test_numbered_and_phrase_tags_survive_intact():
    parts = word_diff("Press <1>Start</1> to switch on the pump.",
                      "Press <1>Start</1> to switch off the pump.")
    assert rebuild(parts, {"normal", "delete"}) == "Press <1>Start</1> to switch off the pump."
    assert rebuild(parts, {"normal", "add"}) == "Press <1>Start</1> to switch on the pump."
    assert tokenize("Set {1>speed<1} to {2}") == ["Set", " ", "{1>", "speed", "<1}", " ", "to", " ", "{2}"]
    assert tokenize("a < b") == ["a", " ", "<", " ", "b"]
    # a lone "<1}" is not stretched into a tag up to a later ">"
    assert tokenize("x <1} y > z")[2] == "<1}"


def test_spacing_and_line_breaks_are_reproduced_exactly():
    tm = "Line one.\nLine  two, with <x id=\"3\"/> a tag."
    cur = "Line one.\nLine  three, with <x id=\"3\"/> a tag."
    parts = word_diff(cur, tm)
    assert rebuild(parts, {"normal", "delete"}) == tm
    assert rebuild(parts, {"normal", "add"}) == cur


def test_identical_and_empty_texts():
    assert word_diff("Same text.", "Same text.") == [("normal", "Same text.")]
    assert word_diff("", "") == []
    assert word_diff("New", "") == [("add", "New")]


def test_char_diff_marks_changed_characters_only():
    parts = char_diff("Open de klep langzaam.", "Open de kraan langzaam.")
    assert rebuild(parts, {"normal", "add"}) == "Open de klep langzaam."
    assert rebuild(parts, {"normal", "delete"}) == "Open de kraan langzaam."
    new = "Open de klep langzaam."
    assert [new[a:b] for a, b in diff_spans(parts, "new")] == ["lep"]


def test_char_diff_folds_tiny_common_runs_into_the_change():
    new, old = "De pompen draaien.", "De pompje draait."
    parts = char_diff(new, old)
    assert [new[a:b] for a, b in diff_spans(parts, "new")] == ["en", "en"]
    assert [old[a:b] for a, b in diff_spans(parts, "old")] == ["je", "t"]
    assert char_diff("Same.", "Same.") == [("normal", "Same.")]
    assert char_diff("Nieuw", "") == [("add", "Nieuw")]
