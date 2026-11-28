"""Edit TM Entry / Delete TM Entry from a match (Nov 2026).

1. The dialog locked the source. It can now be edited, as the TM always could
   store it.
2. The main window keeps the matches it looked up per segment
   (translation_matches_cache). An edit or a delete never told it, so going
   back to a segment showed the entry as it was before. The stale entries are
   now dropped (all of them after a source edit) and looked up again.
3. A delete of an entry that was no longer there said "Deleted".

A real TM database in a temp folder; the window and the message boxes are
stand-ins.
"""

from __future__ import annotations

import os
import sys
import threading
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox, QTextEdit

from modules import tm_entry_actions
from modules.database_manager import DatabaseManager
from modules.tm_metadata_manager import TMMetadataManager
from modules.translation_results_panel import TranslationMatch

PUMP = ("The pump runs.", "De pomp draait.")
VALVE = ("Close the valve.", "Sluit de klep.")


def tm_match(tm_id, source, target, reverse=False):
    return TranslationMatch(source=source, target=target, relevance=100,
                            metadata={'tm_id': tm_id, 'tm_name': 'Client', 'reverse_match': reverse},
                            match_type='TM', compare_source=source, provider_code='TM')


@pytest.fixture
def setup(tmp_path, monkeypatch):
    db = DatabaseManager(db_path=str(tmp_path / "sv.db"), log_callback=lambda *_: None)
    db.connect()
    tms = TMMetadataManager(db, lambda *_: None)
    ids = []
    for name in ("Client", "Other"):
        db_id = tms.create_tm(name, name.lower(), "en", "nl")
        ids.append(next(t['tm_id'] for t in tms.get_all_tms() if t['id'] == db_id))
    tm, other = ids
    db.add_translation_unit(*PUMP, "en", "nl", tm_id=tm)
    db.add_translation_unit(*VALVE, "en", "nl", tm_id=tm)
    db.add_translation_unit(*PUMP, "en", "nl", tm_id=other)

    window = types.SimpleNamespace(
        db_manager=db, tm_manager=None, logged=[],
        translation_matches_cache_lock=threading.Lock(),
        _last_selected_row=7, _instant_tm_guard=("seg", "src"),
        # 1 and 2 show the pump entry, 3 the valve, 4 the pump from another TM,
        # 5 the pump found in the other direction, 6 has no TM match.
        translation_matches_cache={
            1: {"TM": [tm_match(tm, *PUMP)], "Termbases": []},
            2: {"TM": [tm_match(tm, *VALVE), tm_match(tm, *PUMP)]},
            3: {"TM": [tm_match(tm, *VALVE)]},
            4: {"TM": [tm_match(other, *PUMP)]},
            5: {"TM": [tm_match(tm, PUMP[1], PUMP[0], reverse=True)]},
            6: {"TM": [], "Termbases": ["term"]},
        })
    window.log = window.logged.append

    said = types.SimpleNamespace(boxes=[], answer=QMessageBox.StandardButton.Yes, edit=None)

    class Box:
        StandardButton = QMessageBox.StandardButton
        warning = staticmethod(lambda parent, title, text, *a: said.boxes.append(("warning", title)))
        critical = staticmethod(lambda parent, title, text, *a: said.boxes.append(("critical", title)))

        @staticmethod
        def question(parent, title, text, *a):
            said.boxes.append(("question", title))
            return said.answer

    monkeypatch.setattr(tm_entry_actions, "QMessageBox", Box)
    monkeypatch.setattr(tm_entry_actions, "ask_for_edit", lambda *a: said.edit)
    yield types.SimpleNamespace(db=db, tm=tm, other=other, window=window, said=said)
    db.close()


def exact(s, source, tm=None):
    hit = s.db.get_exact_match(source, tm_ids=[tm or s.tm])
    return hit and hit['target_text']


def test_a_target_edit_reaches_the_tm_and_drops_only_that_entrys_cache(setup):
    s = setup
    s.said.edit = (PUMP[0], "De pomp loopt.")
    assert tm_entry_actions.edit_tm_entry(s.window, s.tm, "Client", *PUMP) == (PUMP[0], "De pomp loopt.")
    assert exact(s, PUMP[0]) == "De pomp loopt."
    # The valve, the other TM's pump and the segment without a TM match stay cached.
    assert sorted(s.window.translation_matches_cache) == [3, 4, 6]
    assert s.window._last_selected_row is None and s.window._instant_tm_guard is None
    assert exact(s, PUMP[0], s.other) == PUMP[1]          # the other TM's entry is untouched
    assert s.said.boxes == [] and s.window.logged


def test_a_source_edit_moves_the_match_and_clears_the_whole_cache(setup):
    s = setup
    s.said.edit = ("The pump is running.", PUMP[1])
    assert tm_entry_actions.edit_tm_entry(s.window, s.tm, "Client", *PUMP) == s.said.edit
    assert exact(s, PUMP[0]) is None
    assert exact(s, "The pump is running.") == PUMP[1]
    # Any segment may now match the entry differently (a fuzzy one too).
    assert s.window.translation_matches_cache == {}
    assert [r['source_text'] for r in s.db.concordance_search("running", tm_ids=[s.tm])] == ["The pump is running."]


def test_a_reversed_match_is_saved_the_right_way_round(setup):
    s = setup
    # Shown swapped: Dutch as the source. Editing what is shown as the target
    # changes the entry's source in the TM.
    s.said.edit = (PUMP[1], "The pump is running.")
    assert tm_entry_actions.edit_tm_entry(s.window, s.tm, "Client", PUMP[1], PUMP[0], reverse=True)
    assert exact(s, "The pump is running.") == PUMP[1]
    assert not {1, 2, 5} & set(s.window.translation_matches_cache)


@pytest.mark.parametrize("edit", [None, PUMP, (PUMP[0] + " ", PUMP[1])])
def test_cancel_or_no_change_saves_nothing(setup, edit):
    s = setup
    s.said.edit = edit and (edit[0].strip(), edit[1].strip())
    assert tm_entry_actions.edit_tm_entry(s.window, s.tm, "Client", *PUMP) is None
    assert exact(s, PUMP[0]) == PUMP[1]
    assert len(s.window.translation_matches_cache) == 6 and s.window._last_selected_row == 7


def test_an_entry_that_is_gone_is_reported_and_looked_up_again(setup):
    s = setup
    s.db.delete_entry(s.tm, *PUMP)                        # changed elsewhere meanwhile
    s.said.edit = (PUMP[0], "De pomp loopt.")
    assert tm_entry_actions.edit_tm_entry(s.window, s.tm, "Client", *PUMP) is None
    assert s.said.boxes == [("warning", "Not Found")]
    assert exact(s, PUMP[0]) is None                      # nothing was written
    assert sorted(s.window.translation_matches_cache) == [3, 4, 6]   # the stale ones go
    assert s.window.logged == []


def test_a_failed_save_is_reported_and_changes_nothing(setup):
    s = setup

    def locked(*a):
        raise RuntimeError("database is locked")
    s.window.tm_manager = types.SimpleNamespace(update_entry=locked, delete_entry=locked)
    s.said.edit = (PUMP[0], "De pomp loopt.")
    assert tm_entry_actions.edit_tm_entry(s.window, s.tm, "Client", *PUMP) is None
    assert tm_entry_actions.delete_tm_entry(s.window, s.tm, "Client", *PUMP) is False
    assert [b for b in s.said.boxes if b[0] == "critical"] == [("critical", "Error")] * 2
    assert exact(s, PUMP[0]) == PUMP[1]
    assert len(s.window.translation_matches_cache) == 6 and s.window.logged == []


def test_no_tm_id_or_no_tm_is_refused(setup):
    s = setup
    assert tm_entry_actions.edit_tm_entry(s.window, "", "TM", *PUMP) is None
    assert tm_entry_actions.delete_tm_entry(s.window, "", "TM", *PUMP) is False
    s.window.db_manager = None
    s.said.edit = (PUMP[0], "De pomp loopt.")
    assert tm_entry_actions.edit_tm_entry(s.window, s.tm, "Client", *PUMP) is None
    assert tm_entry_actions.delete_tm_entry(s.window, s.tm, "Client", *PUMP) is False
    assert [kind for kind, _ in s.said.boxes] == ["warning", "warning", "critical", "question", "critical"]
    assert len(s.window.translation_matches_cache) == 6


def test_delete_removes_the_entry_and_its_cached_matches(setup):
    s = setup
    assert tm_entry_actions.delete_tm_entry(s.window, s.tm, "Client", *PUMP) is True
    assert exact(s, PUMP[0]) is None and exact(s, VALVE[0]) == VALVE[1]
    assert exact(s, PUMP[0], s.other) == PUMP[1]
    assert sorted(s.window.translation_matches_cache) == [3, 4, 6]
    assert s.db.concordance_search("pump", tm_ids=[s.tm]) == []


def test_delete_answered_no_deletes_nothing(setup):
    s = setup
    s.said.answer = QMessageBox.StandardButton.No
    assert tm_entry_actions.delete_tm_entry(s.window, s.tm, "Client", *PUMP) is False
    assert exact(s, PUMP[0]) == PUMP[1] and len(s.window.translation_matches_cache) == 6


def test_deleting_an_entry_that_is_gone_says_so(setup):
    s = setup
    s.db.delete_entry(s.tm, *PUMP)
    assert tm_entry_actions.delete_tm_entry(s.window, s.tm, "Client", *PUMP) is False
    assert s.said.boxes[-1] == ("warning", "Not Found") and s.window.logged == []
    assert sorted(s.window.translation_matches_cache) == [3, 4, 6]


def test_a_window_without_a_cache_still_saves(setup):
    s = setup
    bare = types.SimpleNamespace(db_manager=s.db)
    s.said.edit = (PUMP[0], "De pomp loopt.")
    assert tm_entry_actions.edit_tm_entry(bare, s.tm, "Client", *PUMP)
    assert exact(s, PUMP[0]) == "De pomp loopt."


# --- the dialog itself -------------------------------------------------------

@pytest.fixture
def dialog(monkeypatch):
    """Run the real dialog: `fill` types into it and presses OK or Cancel."""
    app = QApplication.instance() or QApplication([])
    seen = types.SimpleNamespace(warnings=[], fill=None, app=app)
    monkeypatch.setattr(tm_entry_actions.QMessageBox, "warning",
                        staticmethod(lambda parent, title, text, *a: seen.warnings.append(title)))

    def run(dlg):
        source, target = dlg.findChildren(QTextEdit)
        seen.read_only = (source.isReadOnly(), target.isReadOnly())
        seen.shown = (source.toPlainText(), target.toPlainText())
        button = seen.fill(source, target)
        dlg.findChild(QDialogButtonBox).button(button).click()
        return dlg.result()

    monkeypatch.setattr(tm_entry_actions.QDialog, "exec", run)
    return seen


OK, CANCEL = QDialogButtonBox.StandardButton.Ok, QDialogButtonBox.StandardButton.Cancel


def test_the_source_can_be_edited(dialog):
    def fill(source, target):
        source.setPlainText("The pump is running. ")
        target.setPlainText("De pomp loopt.")
        return OK
    dialog.fill = fill
    assert tm_entry_actions.ask_for_edit(None, "Client", *PUMP) == ("The pump is running.", "De pomp loopt.")
    assert dialog.read_only == (False, False) and dialog.shown == PUMP


def test_cancel_returns_nothing(dialog):
    dialog.fill = lambda source, target: (target.setPlainText("x"), CANCEL)[1]
    assert tm_entry_actions.ask_for_edit(None, "Client", *PUMP) is None


@pytest.mark.parametrize("which", [0, 1])
def test_an_empty_source_or_target_is_refused(dialog, which):
    dialog.fill = lambda *edits: (edits[which].setPlainText("  "), OK)[1]
    assert tm_entry_actions.ask_for_edit(None, "Client", *PUMP) is None
    assert dialog.warnings == ["Cannot Save"]


def test_the_three_menus_use_the_shared_dialog():
    # Each had its own copy with a read-only source.
    for rel in (("modules", "main_window", "match_panel.py"), ("modules", "translation_results_panel.py")):
        with open(os.path.join(ROOT, *rel), encoding="utf-8") as f:
            text = f.read()
        assert "Source (read-only)" not in text and ".update_entry(" not in text, rel
        assert "edit_tm_entry(" in text and "delete_tm_entry(" in text, rel
