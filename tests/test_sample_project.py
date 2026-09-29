"""Help → Open Sample Project (issue #147): resources are created once and
produce the matches the sample is meant to show off."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import sample_project as sp
from modules.database_manager import DatabaseManager
from modules.termbase_manager import TermbaseManager
from modules.tm_metadata_manager import TMMetadataManager


def test_sample_resources_are_created_once_and_match(tmp_path):
    db = DatabaseManager(db_path=str(tmp_path / "sv.db"), log_callback=lambda *_: None)
    db.connect()
    try:
        tms = TMMetadataManager(db, lambda *_: None)
        tbs = TermbaseManager(db, lambda *_: None)
        first = sp.ensure_resources(db, tms, tbs)
        second = sp.ensure_resources(db, tms, tbs)
        assert first == second
        count = db.cursor.execute("SELECT COUNT(*) FROM translation_units WHERE tm_id = ?",
                                  (sp.TM_ID,)).fetchone()[0]
        assert count == len(sp.TM_ENTRIES)
        assert len(tbs.get_terms(first[1])) == len(sp.GLOSSARY)
        assert tms.get_active_tm_ids(sp.PROJECT_ID) == [sp.TM_ID]
        assert tbs.get_project_termbase(sp.PROJECT_ID)['name'] == sp.GLOSSARY_NAME

        sources = {i + 1: s for i, (s, _t, _st) in enumerate(sp.SEGMENTS)}
        exact = db.search_all(sources[3], tm_ids=[sp.TM_ID])
        assert exact and exact[0]['match_pct'] == 100
        for seg in (4, 6, 10, 11, 12):  # fuzzy matches with visible differences
            match = db.search_all(sources[seg], tm_ids=[sp.TM_ID])
            assert match and 75 <= match[0]['match_pct'] < 100, seg
        terms = tbs.get_terms(first[1])
        assert any(t['forbidden'] for t in terms)
        assert any(t['is_nontranslatable'] for t in terms)
    finally:
        db.close()
