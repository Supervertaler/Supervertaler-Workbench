"""Find & Replace in the project's TMs (issue #68), and the shared replacer."""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from modules.database_manager import DatabaseManager
from modules.tm_metadata_manager import TMMetadataManager
from modules.tm_replace import (
    MATCH_ENTIRE_SEGMENT, MATCH_WHOLE_WORDS, make_replacer, replace_in_tms,
)


def test_plain_and_case_following_replacement():
    r = make_replacer("pump", "valve")
    assert r("Pump the PUMP pump") == "valve the valve valve"
    r = make_replacer("pump", "valve", auto_case=True)
    assert r("Pump the PUMP pump") == "Valve the VALVE valve"
    r = make_replacer("pump", "valve", case_sensitive=True)
    assert r("Pump the pump") == "Pump the valve"


def test_whole_words_leaves_longer_words_alone():
    # Before, Replace All found whole words but replaced inside words too.
    r = make_replacer("pump", "valve", match_mode=MATCH_WHOLE_WORDS)
    assert r("pump and pumphouse") == "valve and pumphouse"


def test_entire_segment_regex_count_and_literal_backslashes():
    assert make_replacer("Pump", "Valve", match_mode=MATCH_ENTIRE_SEGMENT)("pump") == "Valve"
    assert make_replacer("Pump", "Valve", match_mode=MATCH_ENTIRE_SEGMENT)("pumps") == "pumps"
    assert make_replacer(r"(\d+)\.(\d)", r"\1,\2", use_regex=True)("3.5 and 10.25") == "3,5 and 10,25"
    assert make_replacer("a", "b", count=1)("a a") == "b a"
    assert make_replacer("x", r"C:\new")("x") == r"C:\new"
    with pytest.raises(re.error):
        make_replacer("(", "x", use_regex=True)


@pytest.fixture
def tm(tmp_path):
    db = DatabaseManager(db_path=str(tmp_path / "sv.db"), log_callback=lambda *_: None)
    db.connect()
    tms = TMMetadataManager(db, lambda *_: None)
    ids = {}
    for name in ("Client", "Other"):
        db_id = tms.create_tm(name, name.lower(), "en", "nl")
        ids[name] = next(t['tm_id'] for t in tms.get_all_tms() if t['id'] == db_id)
    db.add_translation_unit("The pump runs.", "De pomp draait.", "en", "nl", tm_id=ids["Client"])
    db.add_translation_unit("Pump housing", "Pomphuis", "en", "nl", tm_id=ids["Client"])
    db.add_translation_unit("Check the pump.", "Controleer de pomp.", "en", "nl", tm_id=ids["Client"])
    db.add_translation_unit("Check the pump.", "Controleer de klep.", "en", "nl", tm_id=ids["Client"])
    db.add_translation_unit("The pump runs.", "De pomp draait.", "en", "nl", tm_id=ids["Other"])
    yield db, ids
    db.close()


def _targets(db, tm_id):
    return sorted(r[0] for r in db.cursor.execute(
        "SELECT target_text FROM translation_units WHERE tm_id = ?", (tm_id,)).fetchall())


def test_dry_run_counts_without_writing(tm):
    db, ids = tm
    replacer = make_replacer("pomp", "klep", match_mode=MATCH_WHOLE_WORDS, auto_case=True)
    result = replace_in_tms(db, [ids["Client"]], replacer)
    assert result['changed'] == 2 and result['per_tm'] == {ids["Client"]: 2}
    assert ("De pomp draait.", "De klep draait.") in result['examples']
    assert "De pomp draait." in _targets(db, ids["Client"])


def test_apply_updates_only_the_chosen_tm_and_merges_duplicates(tm):
    db, ids = tm
    replacer = make_replacer("pomp", "klep", match_mode=MATCH_WHOLE_WORDS)
    result = replace_in_tms(db, [ids["Client"]], replacer, apply=True)
    assert result['changed'] == 2 and result['merged'] == 1
    # "Controleer de pomp." became a duplicate of "Controleer de klep." and was merged;
    # "Pomphuis" is not the whole word "pomp" and is untouched.
    assert _targets(db, ids["Client"]) == ["Controleer de klep.", "De klep draait.", "Pomphuis"]
    assert _targets(db, ids["Other"]) == ["De pomp draait."]
    # Hashes follow the text, so exact matching still finds the entry.
    match = db.get_exact_match("The pump runs.", tm_ids=[ids["Client"]])
    assert match and match['target_text'] == "De klep draait."


def test_source_side_updates_the_source_hash(tm):
    db, ids = tm
    replace_in_tms(db, [ids["Other"]], make_replacer("pump", "valve"),
                   in_source=True, in_target=False, apply=True)
    assert db.get_exact_match("The valve runs.", tm_ids=[ids["Other"]])
    assert not db.get_exact_match("The pump runs.", tm_ids=[ids["Other"]])


def test_nothing_selected_does_nothing(tm):
    db, ids = tm
    assert replace_in_tms(db, [], make_replacer("pomp", "klep"), apply=True)['changed'] == 0
    assert replace_in_tms(db, [ids["Client"]], make_replacer("pomp", "klep"),
                          in_target=False, apply=True)['changed'] == 0


def test_editing_an_entry_keeps_it_an_exact_match(tm):
    # update_entry() (TM editor, match panel) used to hash the lower-cased
    # source, so an edited "The pump runs." was never found again.
    db, ids = tm
    assert db.update_entry(ids["Other"], "The pump runs.", "De pomp draait.",
                           "The pump runs.", "De pomp loopt.")
    match = db.get_exact_match("The pump runs.", tm_ids=[ids["Other"]])
    assert match and match['target_text'] == "De pomp loopt."
