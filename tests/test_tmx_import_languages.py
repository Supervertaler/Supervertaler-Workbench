"""TMX import: language direction and TM names (issue #105).

The language dialog used to default to the alphabetical order of the TMX's
languages (so en-GB/de-DE came out as de-DE → en-GB), and importing a TMX under
a name already in use failed with a bare "Failed to create TM metadata".
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from modules.database_manager import DatabaseManager
from modules.tm_metadata_manager import TMMetadataManager
from modules.translation_memory import TMDatabase

TMX = '''<?xml version="1.0" encoding="UTF-8"?>
<tmx version="1.4"><header srclang="{src}" datatype="plaintext" segtype="sentence"
 adminlang="en" o-tmf="x" creationtool="t" creationtoolversion="1"/>
<body><tu><tuv xml:lang="en-GB"><seg>Pump</seg></tuv><tuv xml:lang="de-DE"><seg>Pumpe</seg></tuv></tu></body></tmx>'''


@pytest.mark.parametrize("src, expected", [("en-GB", "en-GB"), ("*all*", None), ("", None)])
def test_header_source_language(tmp_path, src, expected):
    path = tmp_path / "t.tmx"
    path.write_text(TMX.format(src=src), encoding="utf-8")
    assert TMDatabase.detect_tmx_source_language(str(path)) == expected


def test_header_source_language_survives_a_broken_file(tmp_path):
    path = tmp_path / "bad.tmx"
    path.write_text("<tmx><header", encoding="utf-8")
    assert TMDatabase.detect_tmx_source_language(str(path)) is None
    assert TMDatabase.detect_tmx_source_language(str(tmp_path / "missing.tmx")) is None


def test_names_are_checked_like_ids(tmp_path):
    db = DatabaseManager(db_path=str(tmp_path / "sv.db"), log_callback=lambda *_: None)
    db.connect()
    try:
        tms = TMMetadataManager(db, lambda *_: None)
        assert not tms.tm_name_exists("Client TM")
        assert tms.create_tm("Client TM", "client_tm", "en", "de")
        assert tms.tm_name_exists("Client TM")
        # The id is made unique on its own, but a taken name still fails –
        # which is why the import dialog now checks the name first.
        assert tms.create_tm("Client TM", "client_tm", "en", "de") is None
        assert tms.create_tm("Client TM (2)", "client_tm", "en", "de")
        assert {t['tm_id'] for t in tms.get_all_tms()} >= {"client_tm", "client_tm_2"}
    finally:
        db.close()
