"""Drifted numbered inline tags in AI output (issue #226).

The repair must restore every drift variant of a tag the source contains, and
must never touch anything else – the reason the issue asked for caution.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from modules.tag_repair import repair_drifted_tags

SOURCE = "Press <1>Start</1> and wait <2/> seconds."


@pytest.mark.parametrize("drifted", [
    "Druk op < 1>Start</ 1 > en wacht <2 /> seconden.",
    "Druk op <1 >Start< /1> en wacht < 2/> seconden.",
    "Druk op &lt;1&gt;Start&lt;/1&gt; en wacht &lt;2/&gt; seconden.",
    "Druk op &lt; 1 &gt;Start&lt; / 1 &gt; en wacht &lt;2 / &gt; seconden.",
])
def test_drift_variants_are_restored(drifted):
    assert repair_drifted_tags(SOURCE, drifted) == "Druk op <1>Start</1> en wacht <2/> seconden."


def test_canonical_output_is_unchanged():
    good = "Druk op <1>Start</1> en wacht <2/> seconden."
    assert repair_drifted_tags(SOURCE, good) == good


def test_tags_not_in_the_source_are_left_alone():
    # <3> is not in the source; <2> exists only as <2/>, so a paired <2> stays.
    text = "Druk < 3>op</ 3> <2>nu</2>."
    assert repair_drifted_tags(SOURCE, text) == text


def test_ordinary_angle_brackets_are_not_touched():
    source = "Keep it <1>below</1> 5 mm."
    text = "Houd het <1>onder</1> < 5 mm en x<3 y>2."
    assert repair_drifted_tags(source, text) == text


def test_sources_without_numbered_tags_are_a_no_op():
    assert repair_drifted_tags("No tags here.", "Geen < 1> tags.") == "Geen < 1> tags."
    assert repair_drifted_tags("", "x") == "x"
    assert repair_drifted_tags("<1>a</1>", "") == ""
    assert repair_drifted_tags("<1>a</1>", None) is None


def test_empty_pairs_are_not_invented_away():
    # Can't know which words <1> should wrap; the tag check reports it instead.
    assert repair_drifted_tags(SOURCE, "<1></1> Start <2/>") == "<1></1> Start <2/>"
