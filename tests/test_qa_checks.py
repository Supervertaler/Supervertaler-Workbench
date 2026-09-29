"""QA checks: saved find-only F&R operations (issue #209)."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace as Seg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.find_replace_qt import FindReplaceOperation, FindReplaceSet
from modules.qa_checks import (
    BASIC_SET_NAME, basic_qa_set, checks_in, load_sets, run_checks, save_set,
)

SEGMENTS = [
    Seg(id=1, source="The pump runs.", target="De pomp  draait ."),
    Seg(id=2, source="Check the the valve.", target="Controleer de de klep."),
    Seg(id=3, source="Done", target="Klaar"),
]


def op(find, **kw):
    kw.setdefault("check_only", True)
    return FindReplaceOperation(find_text=find, **kw)


def test_basic_set_finds_the_usual_suspects():
    findings, problems = run_checks(SEGMENTS, checks_in(basic_qa_set()))
    assert problems == []
    got = {(f.segment_id, f.check) for f in findings}
    assert (1, "Double space") in got
    assert (1, "Space before a full stop or comma") in got
    assert (2, "Doubled word") in got
    assert all(f.side == "target" for f in findings)
    assert not any(f.segment_id == 3 for f in findings)
    doubled = next(f for f in findings if f.check == "Doubled word")
    assert doubled.text == "de de" and "[de·de]" in doubled.context


def test_only_enabled_check_operations_run():
    fr_set = FindReplaceSet("Mixed", [
        op("pomp"), op("klep", enabled=False), FindReplaceOperation(find_text="draait", replace_text="loopt"),
    ])
    assert [o.find_text for o in checks_in(fr_set)] == ["pomp"]


def test_sides_modes_and_bad_patterns():
    findings, problems = run_checks(SEGMENTS, [
        op("the", search_in="source", match_mode=1, note="the"),
        op("klaar", match_mode=2, note="entire"),
        op("^", use_regex=True, note="zero-width"),
        op("(", use_regex=True, note="broken"),
    ])
    assert [(f.segment_id, f.side) for f in findings if f.check == "the"] == [
        (1, "source"), (2, "source"), (2, "source")]
    assert [f.segment_id for f in findings if f.check == "entire"] == [3]
    assert not any(f.check == "zero-width" for f in findings)
    assert len(problems) == 1 and "broken" in problems[0]


def test_sets_round_trip_through_the_folder(tmp_path):
    path = save_set(tmp_path, basic_qa_set())
    assert path.name == f"{BASIC_SET_NAME}.svfr"
    (loaded,) = load_sets(tmp_path)
    assert loaded.name == BASIC_SET_NAME
    assert all(o.check_only and o.note for o in loaded.operations)
    assert [o.enabled for o in loaded.operations] == [o.enabled for o in basic_qa_set().operations]


def test_old_sets_without_the_new_fields_still_load():
    old = FindReplaceOperation.from_dict({"find_text": "a", "replace_text": "b"})
    assert old.check_only is False and old.note == ""
