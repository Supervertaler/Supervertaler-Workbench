"""Grid Source/Target filter boxes: plain text and /regex/ (issue #208).

Plain text must keep behaving exactly as before (case-insensitive substring),
because that is what every existing user types.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.grid_filter import GridFilter


def test_plain_text_is_case_insensitive_substring():
    f = GridFilter("Pump")
    assert not f.is_regex
    assert f.matches("The pump housing")
    assert not f.matches("The valve")
    assert f.spans("pump, PUMP") == [(0, 4), (6, 10)]


def test_empty_filter_matches_everything():
    f = GridFilter("")
    assert not f
    assert f.matches("anything") and f.matches("")
    assert f.spans("anything") == []


def test_slashes_make_a_case_insensitive_regex():
    f = GridFilter(r"/pump\s+hous(ing|e)/")
    assert f.is_regex and f.error is None
    assert f.matches("Pump  Housing")
    assert f.matches("pump house")
    assert not f.matches("pumphousing")
    assert f.spans("a PUMP house") == [(2, 12)]


def test_regex_anchors_and_alternation():
    assert GridFilter("/^fig/").matches("Figure 3")
    assert not GridFilter("/^fig/").matches("See figure 3")
    assert GridFilter(r"/\d{4}-\d{2}/").matches("Filed 2026-09")


def test_zero_length_matches_are_not_highlighted():
    f = GridFilter("/^/")
    assert f.matches("text")
    assert f.spans("text") == []


def test_invalid_regex_falls_back_to_plain_text_and_reports():
    f = GridFilter("/pump(/")
    assert not f.is_regex
    assert f.error
    assert not f.matches("pump housing")
    assert f.matches("literally /pump(/ here")


def test_unfinished_pattern_is_plain_text():
    f = GridFilter("/pump")
    assert not f.is_regex and f.error is None
    assert f.matches("see /pump for details")
    assert not f.matches("pump")


def test_lone_slashes_are_plain_text():
    assert not GridFilter("/").is_regex
    assert not GridFilter("//").is_regex
    assert GridFilter("//").matches("http://example.com")
