"""Standalone tags like <2/> (every SDLXLIFF/Trados import writes them), <br/>
and memoQ's <mq:ch …/> were not recognised by extract_all_tags, so Insert next
tag (Ctrl+,) never offered them and the QA tag check missed a lost one.

Also: Ctrl+O was the shortcut of both Open Project and Import Document, which
Qt treats as ambiguous, so it did nothing at all.
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def S():
    S._app = QApplication.instance() or QApplication([])   # unreferenced, it is deleted at once
    saved = sys.stdout, sys.stderr
    import Supervertaler   # re-wraps stdout/stderr on Windows at import
    S._wrappers = sys.stdout, sys.stderr   # keep alive: closing them closes pytest's capture
    sys.stdout, sys.stderr = saved
    return Supervertaler


@pytest.mark.parametrize("text, tags", [
    ("Line one<2/>line two", ["<2/>"]),
    ("Press <5>Save</5> now.<6/>", ["<5>", "</5>", "<6/>"]),
    ("a<br/>b<br />c", ["<br/>", "<br />"]),
    ('Arrow <mq:ch val="→"/> here', ['<mq:ch val="→"/>']),
    ("[1}bold{1] and [2]", ["[1}", "{1]", "[2]"]),
])
def test_every_tag_form_is_recognised(S, text, tags):
    assert S.extract_all_tags(text) == tags


def test_plain_text_has_no_tags(S):
    assert S.extract_all_tags("2 < 3 and 5/2 = 2.5; <> is not a tag") == []
    assert S.extract_all_tags("") == [] and S.extract_all_tags(None) == []


def test_insert_next_tag_offers_a_standalone_tag(S):
    source = "Press <5>Save</5> now.<6/>"
    assert S.find_next_unused_tag(source, "Druk nu op <5>Opslaan</5>.") == "<6/>"
    assert S.find_next_unused_tag(source, "Druk nu op <5>Opslaan</5>.<6/>") == ""


def test_qa_reports_a_lost_standalone_tag(S):
    from modules.qa_checks import tag_findings
    seg = S.Segment(id=1, source="Line one<2/>line two", target="Regel een regel twee",
                    status="translated")
    findings = tag_findings([seg], S.extract_all_tags)
    assert [f.text for f in findings] == ["<2/>"]
    seg.target = "Regel een<2/>regel twee"
    assert tag_findings([seg], S.extract_all_tags) == []


def test_autotagger_and_extract_all_tags_agree(S):
    # Kept in step on purpose: AutoTagger already handled these forms.
    assert S._AUTOTAG_TAG_PATTERN == S._ALL_TAGS_PATTERN


def test_ctrl_o_belongs_to_open_project_only():
    source = open(os.path.join(ROOT, "Supervertaler.py"), encoding="utf-8").read()
    explicit = re.findall(r'setShortcut\(\s*["\']Ctrl\+O["\']\s*\)', source)
    assert explicit == []                                     # Open Project has it (StandardKey.Open)
    assert 'import_document_action.setShortcut("Ctrl+Shift+O")' in source
    others = re.findall(r'setShortcut\(\s*["\']Ctrl\+Shift\+O["\']\s*\)', source)
    assert len(others) == 1                                   # and nothing else uses Ctrl+Shift+O
    from modules.shortcut_manager import ShortcutManager
    defaults = {k: v["default"] for k, v in ShortcutManager.DEFAULT_SHORTCUTS.items()}
    assert defaults["file_open"] == "Ctrl+O"
    assert "Ctrl+Shift+O" not in defaults.values()
