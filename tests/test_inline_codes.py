"""User-defined inline codes / placeholders treated like tags (issue #194)."""

from __future__ import annotations

import os
import re
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import inline_codes
from modules.qa_checks import TAG_CHECK_EXTRA, TAG_CHECK_MISSING, tag_findings

BRACES = [{"pattern": r"\{[^{}\s]+\}", "enabled": True, "comment": ""}]


def test_every_preset_is_valid_and_finds_its_example():
    for label, pattern, example in inline_codes.PRESETS:
        assert inline_codes.pattern_error(pattern) is None, label
        assert inline_codes.codes_in(example, re.compile(pattern)), label


def test_disabled_invalid_and_empty_matching_patterns_are_left_out():
    entries = [{"pattern": "(", "enabled": True}, {"pattern": "x*", "enabled": True},
               {"pattern": "%s", "enabled": False}, {"pattern": r"\$\w+\$", "enabled": True}]
    pattern = inline_codes.compile_codes(entries)
    assert inline_codes.codes_in("%s costs $PRICE$", pattern) == ["$PRICE$"]
    assert inline_codes.pattern_error("x*") and inline_codes.pattern_error("(")
    assert inline_codes.compile_codes([]) is None


def test_missing_and_extra_codes():
    pattern = inline_codes.compile_codes(BRACES + [{"pattern": "%[sd]"}])
    missing, extra = inline_codes.mismatches("{name} has %d items, {name}!", "{naam} heeft %d items", pattern)
    assert missing == ["{name}", "{name}"] and extra == ["{naam}"]


def test_a_tm_match_gets_the_codes_of_the_current_source():
    pattern = inline_codes.compile_codes(BRACES)
    # the example from #189: the TM has {PK}{MN}, the file has {PKMN}
    assert inline_codes.adapt_codes("{PK}{MN} can't be the same.", "{PK}{MN} muszą być różne.",
                                    "{PKMN} can't be the same.", pattern) == "{PKMN} muszą być różne."
    # codes moved in the translation are followed
    assert inline_codes.adapt_codes("Give {a} to {b}.", "Geef {b} {a}.", "Give {x} to {y}.",
                                    pattern) == "Geef {y} {x}."


def test_no_adaptation_when_more_than_the_codes_differ():
    pattern = inline_codes.compile_codes(BRACES)
    assert inline_codes.adapt_codes("{A} can't be the same.", "{A} x", "{B} must be the same.", pattern) is None
    assert inline_codes.adapt_codes("{A} is here.", "{A} jest tu.", "{A} is here.", pattern) is None   # nothing to do
    assert inline_codes.adapt_codes("{A} is here.", "{Z} jest tu.", "{B} is here.", pattern) is None   # unknown code
    assert inline_codes.adapt_codes("No codes.", "Brak.", "No codes.", pattern) is None
    assert inline_codes.adapt_codes("{A} x", "{A} y", "{B} x", None) is None


def test_prompt_note_lists_each_code_once():
    pattern = inline_codes.compile_codes(BRACES)
    note = inline_codes.prompt_note(["Hi {name}!", "Bye {name}, see {place}."], pattern)
    assert "{name}, {place}" in note and note.count("{name}") == 1
    assert inline_codes.prompt_note(["Plain text."], pattern) == ""


def test_active_patterns_are_shared():
    try:
        inline_codes.set_active(BRACES)
        assert inline_codes.codes_in("Hello {player}") == ["{player}"]
    finally:
        inline_codes.set_active([])
    assert inline_codes.codes_in("Hello {player}") == []


def test_qa_lists_lost_and_invented_codes():
    pattern = inline_codes.compile_codes(BRACES)
    segments = [SimpleNamespace(id=1, source="{PKMN} can't be the same.", target="{PK} muszą być różne."),
                SimpleNamespace(id=2, source="{a} and {a}", target="{a} i {a}"),
                SimpleNamespace(id=3, source="{b} untranslated", target="")]
    findings = tag_findings(segments, lambda text: inline_codes.codes_in(text, pattern))
    assert [(f.segment_id, f.check, f.text, f.side) for f in findings] == [
        (1, TAG_CHECK_MISSING, "{PKMN}", "source"), (1, TAG_CHECK_EXTRA, "{PK}", "target")]
    assert findings[1].context.startswith("[{PK}]")


def test_tm_diff_treats_codes_as_single_tokens():
    from modules.tm_diff import tokenize, word_diff
    try:
        inline_codes.set_active(BRACES)
        assert tokenize("{PKMN} can't") == ["{PKMN}", " ", "can't"]
        parts = word_diff("{PKMN} can't be the same.", "{PK}{MN} can't be the same.")
        assert parts[:2] == [("delete", "{PK}{MN}"), ("add", "{PKMN}")]
    finally:
        inline_codes.set_active([])
