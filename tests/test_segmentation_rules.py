"""User-definable segmentation rules and the rule-based segmenter (issue #191)."""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.segmentation_rules import (Rule, SegmentationRules, build_srx, parse_abbreviations,
                                        parse_srx, rule_error, to_python_regex)
from modules.simple_segmenter import MarkdownSegmenter, SimpleSegmenter, join_segments


def squash(text):
    return re.sub(r"\s", "", text)


def test_builtin_rules_never_drop_text():
    seg = SimpleSegmenter()
    # a leading abbreviation used to vanish: "Dr." was dropped entirely
    assert seg.segment_text("Dr. Smith works here. He is nice.") == ["Dr. Smith works here.", "He is nice."]
    # "?!" stays on its sentence instead of becoming a segment of its own
    assert seg.segment_text("Really?! Yes, really.") == ["Really?!", "Yes, really."]
    for text in ("Etc. Something else happens here.", "See Fig. A for details. Next one.",
                 "Ms. Jones arrived. It took 100 ms. The end came quickly."):
        assert squash("".join(seg.segment_text(text))) == squash(text)


def test_builtin_rules_keep_their_old_decisions():
    seg = SimpleSegmenter()
    assert seg.segment_text("This is Mr. Smith. Hello.") == ["This is Mr. Smith.", "Hello."]
    assert seg.segment_text("Line one\nLine two. Three") == ["Line one Line two.", "Three"]
    assert seg.segment_text("It costs 5 EUR. 10 people came.") == ["It costs 5 EUR. 10 people came."]
    assert seg.segment_text("Łódź is big. Émile came here.") == ["Łódź is big.", "Émile came here."]
    assert seg.segment_text('He said "Stop." Then he left.') == ['He said "Stop."', "Then he left."]
    assert seg.segment_text("WHERE a = LOWER(?) OR b = 1") == ["WHERE a = LOWER(?) OR b = 1"]


def test_custom_break_and_exception_rules():
    rules = SegmentationRules(rules=[Rule(True, "<>", ""), Rule(False, r"\bnp\.", r"\s")],
                              extra_abbreviations=parse_abbreviations("itd., tzn"))
    seg = SimpleSegmenter(rules)
    text = "Cześć<>Siema<>Witam. Jak np. Tomek. Oraz itd. Koniec zdania."
    parts = seg.segment_with_separators(text)
    assert [p for p, _ in parts] == ["Cześć<>", "Siema<>", "Witam.", "Jak np. Tomek.",
                                     "Oraz itd. Koniec zdania."]
    # split where there was no space – the export must not add one
    assert join_segments(parts) == text


def test_an_exception_written_with_the_space_still_overrides_a_break():
    rules = SegmentationRules(rules=[Rule(False, r"\bNr\.\s", "")])
    assert SimpleSegmenter(rules).segment_text("See Nr. Five here.") == ["See Nr. Five here."]


def test_custom_rules_alone_and_line_breaks():
    only_custom = SegmentationRules(use_builtin_rules=False, rules=[Rule(True, "<<KON>>", "")])
    text = "I give!\\nYou're good at this!<<KON>>No!\\nJesteś w tym niezły<<KON>>"
    assert SimpleSegmenter(only_custom).segment_text(text) == [
        "I give!\\nYou're good at this!<<KON>>", "No!\\nJesteś w tym niezły<<KON>>"]

    lines = SegmentationRules(split_at_line_breaks=True, use_builtin_rules=False)
    assert SimpleSegmenter(lines).segment_with_separators("One. Two.\r\n\r\nThree\n") == [
        ("One. Two.", ""), ("Three", "\n\n")]


def test_disabled_and_invalid_rules_are_ignored():
    rules = SegmentationRules(rules=[Rule(True, "(", ""), Rule(True, ",", "", enabled=False)])
    assert SimpleSegmenter(rules).segment_text("A, b. C d.") == ["A, b.", "C d."]
    assert rule_error(Rule(True, "(", "")).startswith("Before the break:")
    assert rule_error(Rule(True, "", "")) is not None
    assert rule_error(Rule(True, r"[.?!]", r"\s\p{Lu}")) is None


def test_java_unicode_properties_are_translated():
    upper = re.compile(to_python_regex(r"\p{Lu}"))
    assert upper.fullmatch("Ł") and not upper.fullmatch("ł")
    assert re.compile(to_python_regex(r"[\p{Ll}\d]+")).fullmatch("żółw9")
    assert re.compile(to_python_regex(r"\P{L}")).fullmatch("7")
    assert to_python_regex(r"[]a]") == r"[\]a]"


def test_srx_round_trip_and_import():
    rules = [Rule(True, "<>", "", comment="game delimiter"), Rule(False, r"\bnp\.", r"\s")]
    parsed = parse_srx(build_srx(rules))
    assert list(parsed) == ["Supervertaler"]
    assert [(r.is_break, r.before, r.after) for r in parsed["Supervertaler"]] == [
        (True, "<>", ""), (False, r"\bnp\.", r"\s")]

    omegat = """<?xml version="1.0" encoding="UTF-8"?>
<srx xmlns="http://www.lisa.org/srx20" version="2.0"><header segmentsubflows="yes" cascade="yes"/>
<body><languagerules>
<languagerule languagerulename="Polish"><rule break="no"><beforebreak>\\bnp\\.</beforebreak><afterbreak>\\s</afterbreak></rule></languagerule>
<languagerule languagerulename="Default"><rule break="yes"><beforebreak>[\\.\\?!]+</beforebreak><afterbreak>\\s+\\p{Lu}</afterbreak></rule></languagerule>
</languagerules></body></srx>"""
    parsed = parse_srx(omegat)
    assert list(parsed) == ["Polish", "Default"]
    seg = SimpleSegmenter(SegmentationRules(use_builtin_rules=False,
                                            rules=parsed["Polish"] + parsed["Default"]))
    assert seg.segment_text("To jest np. Ala. Źle się stało.") == ["To jest np. Ala.", "Źle się stało."]


def test_settings_round_trip():
    rules = SegmentationRules(True, False, ["np"], [Rule(False, "a", "b", enabled=False, comment="c")])
    assert SegmentationRules.from_dict(rules.to_dict()) == rules
    assert SegmentationRules.from_dict(None) == SegmentationRules()
    assert SegmentationRules.from_dict({"extra_abbreviations": "np., itd"}).extra_abbreviations == ["np", "itd"]


def test_markdown_placeholders_nested_in_links_are_restored():
    text = "See [`docs/guide.md`](docs/guide.md) for more. Then read on."
    assert MarkdownSegmenter().segment_text(text) == [
        "See [`docs/guide.md`](docs/guide.md) for more.", "Then read on."]


def test_join_segments_falls_back_to_a_space():
    assert join_segments([("A.", ""), ("B.", None), ("", " "), ("C.", "")]) == "A. B.C."


def test_split_and_merge_keep_the_recorded_separator():
    from dataclasses import dataclass, field

    from modules.segment_split_merge import merge_with_next, split_segment

    @dataclass
    class Seg:
        source: str
        target: str = ""
        join_before: object = None
        status: str = "not_started"
        comments: list = field(default_factory=list)
        proofreading_notes: dict = field(default_factory=dict)
        match_percent: object = None
        memoQ_status: str = ""
        modified: bool = False
        locked: bool = False

    segs = [Seg("Cześć<>", "Hi<>", ""), Seg("Siema<>", "Hey<>", "")]
    merge_with_next(segs, 0)
    assert (segs[0].source, segs[0].target) == ("Cześć<>Siema<>", "Hi<>Hey<>")

    segs = [Seg("Hello world", join_before=" ")]
    split_segment(segs, 0, 6)
    assert join_segments([(s.source, s.join_before) for s in segs]) == "Hello world"
