"""Back up every TM and termbase to open files (issue #52).

Runs against a real (temporary) Supervertaler database, so the TMX is checked
for being well-formed XML with each entry's own languages, and the termbase
TSV is re-imported to prove non-translatables and forbidden terms survive.
"""

from __future__ import annotations

import os
import sys
from xml.etree import ElementTree

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from modules.database_manager import DatabaseManager
from modules.resource_backup import backup_all_resources, safe_file_name
from modules.termbase_import_export import TermbaseImporter
from modules.termbase_manager import TermbaseManager
from modules.tm_metadata_manager import TMMetadataManager

XML_LANG = '{http://www.w3.org/XML/1998/namespace}lang'


@pytest.fixture
def db(tmp_path):
    manager = DatabaseManager(db_path=str(tmp_path / "sv.db"), log_callback=lambda *_: None)
    manager.connect()
    yield manager
    manager.close()


def test_backup_writes_tmx_and_tsv_that_round_trip(db, tmp_path):
    tms = TMMetadataManager(db, lambda *_: None)
    tbs = TermbaseManager(db, lambda *_: None)

    tm_db_id = tms.create_tm("Client / Pumps", "client_pumps", source_lang="en", target_lang="nl")
    tm_id = next(t['tm_id'] for t in tms.get_all_tms() if t['id'] == tm_db_id)
    db.add_translation_unit("Pump & <valve>", "Pomp & <klep>", "en", "nl", tm_id=tm_id)
    db.add_translation_unit("Het huis", "The house", "nl", "en", tm_id=tm_id)  # reverse direction
    db.add_translation_unit("Bad\x01char", "Slecht\x01teken", "en", "nl", tm_id=tm_id)
    tms.create_tm("Empty TM", "empty_tm", source_lang="en", target_lang="de")

    tb = tbs.create_termbase("Client glossary", source_lang="en", target_lang="nl")
    tbs.add_term(tb, "pump housing", "pomphuis")
    tbs.add_term(tb, "Supervertaler", "Supervertaler", is_nontranslatable=True)
    tbs.add_term(tb, "bolt", "bout", forbidden=True)

    summary = backup_all_resources(db, tms, tbs, str(tmp_path / "out"), tool_version="test")
    assert summary['errors'] == []
    assert sorted((n, c) for n, _f, c in summary['tms']) == [("Client / Pumps", 3), ("Empty TM", 0)]

    folder = summary['folder']
    tmx_path = os.path.join(folder, "TMs", "Client _ Pumps.tmx")
    root = ElementTree.parse(tmx_path).getroot()  # well-formed despite \x01 and &<>
    tus = root.findall('.//tu')
    pairs = [tuple((tuv.get(XML_LANG), tuv.findtext('seg')) for tuv in tu.findall('tuv')) for tu in tus]
    assert pairs[0] == (("en", "Pump & <valve>"), ("nl", "Pomp & <klep>"))
    assert pairs[1] == (("nl", "Het huis"), ("en", "The house"))
    assert pairs[2] == (("en", "Badchar"), ("nl", "Slechtteken"))
    assert all(tu.get('creationdate', '').endswith('Z') for tu in tus)
    assert os.path.exists(os.path.join(folder, "TMs", "Empty TM.tmx"))

    readme = open(os.path.join(folder, "README.txt"), encoding="utf-8").read()
    assert "3 entries" in readme and "1 non-translatable" in readme

    # Restore the termbase from its TSV into a fresh termbase.
    restored = tbs.create_termbase("Restored", source_lang="en", target_lang="nl")
    result = TermbaseImporter(db, tbs).import_tsv(
        os.path.join(folder, "Termbases", "Client glossary.tsv"), restored)
    assert result.success and result.imported_count == 3 and result.error_count == 0
    terms = {t['source_term']: t for t in tbs.get_terms(restored)}
    assert terms["Supervertaler"]['is_nontranslatable'] is True
    assert terms["bolt"]['forbidden']
    assert terms["pump housing"]['target_term'] == "pomphuis"
    assert terms["pump housing"]['is_nontranslatable'] is False


def test_backup_tmx_reimports_through_the_tms_tab_import(db, tmp_path):
    """The backup TMX goes back in through the same code path as the TMs
    tab's Import TMX (create the TM, then _load_tmx_into_db)."""
    from modules.translation_memory import TMDatabase

    tms = TMMetadataManager(db, lambda *_: None)
    tbs = TermbaseManager(db, lambda *_: None)
    tm_db_id = tms.create_tm("Pumps", "pumps", source_lang="en", target_lang="nl")
    tm_id = next(t['tm_id'] for t in tms.get_all_tms() if t['id'] == tm_db_id)
    db.add_translation_unit("Pump & <valve>", "Pomp & <klep>", "en", "nl", tm_id=tm_id)
    db.add_translation_unit("The house", "Het huis", "en", "nl", tm_id=tm_id)
    summary = backup_all_resources(db, tms, tbs, str(tmp_path / "out"))

    target = TMDatabase(source_lang="en", target_lang="nl",
                        db_path=str(tmp_path / "restore.db"), log_callback=lambda *_: None)
    restore_mgr = TMMetadataManager(target.db, lambda *_: None)
    new_db_id = restore_mgr.create_tm("Restored", "restored", source_lang="en", target_lang="nl")
    new_tm_id = next(t['tm_id'] for t in restore_mgr.get_all_tms() if t['id'] == new_db_id)
    count = target._load_tmx_into_db(os.path.join(summary['folder'], "TMs", "Pumps.tmx"),
                                     "en", "nl", new_tm_id)
    rows = target.db.cursor.execute(
        "SELECT source_text, target_text FROM translation_units WHERE tm_id = ? ORDER BY id",
        (new_tm_id,)).fetchall()
    assert count == 2
    assert [tuple(r) for r in rows] == [("Pump & <valve>", "Pomp & <klep>"), ("The house", "Het huis")]


def test_safe_file_names_are_unique_and_legal():
    taken = set()
    assert safe_file_name('A/B: "c"?', taken) == "A_B_ _c_"
    assert safe_file_name("glossary", taken) == "glossary"
    assert safe_file_name("Glossary", taken) == "Glossary (2)"
    assert safe_file_name("", taken) == "untitled"
