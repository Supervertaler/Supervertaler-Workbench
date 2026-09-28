"""Updating a project from pasted AI-friendly bilingual text (issue #248).

Pasted text has no .svexport.json sidecar, so rows are matched to live
segments by their (read-only) source line. The number in [SEGMENT N] alone is
not trustworthy: a status-filtered export numbers its blocks 1..n wherever they
sit in the project. These tests pin down that nothing is ever written to a
segment whose source doesn't match.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.bilingual_markdown_handler import (
    KIND_CHANGED,
    KIND_MISSING,
    KIND_UNCHANGED,
    CurrentSeg,
    MdExportSegment,
    build_import_diffs,
    build_markdown,
    build_sidecar,
    match_rows_by_source,
    parse_markdown,
)

LABELS = {"not started": "not_started", "draft": "draft", "confirmed": "confirmed"}


def _project():
    rows = [
        ("Hello", "Cześć", "confirmed"),
        ("The pump", "", "not_started"),
        ("Hello", "", "not_started"),
        ("Two\nlines", "", "not_started"),
        ("Goodbye", "Do widzenia", "confirmed"),
    ]
    return [CurrentSeg(id=100 + i, number=i + 1, source=s, target=t, status_key=k)
            for i, (s, t, k) in enumerate(rows)]


def _export(current, keep):
    """Export the segments at the given 0-based positions, numbered 1..n."""
    segs = [MdExportSegment(number=n, segment_id=current[i].id, source=current[i].source,
                            target=current[i].target, status_key=current[i].status_key,
                            status_label=current[i].status_key.replace("_", " ").title())
            for n, i in enumerate(keep, 1)]
    text = build_markdown(segs, project_name="P", source_file_name="f.docx",
                          source_lang_display="English", target_lang_display="Polish",
                          tool_version="test")
    sidecar = build_sidecar(segs, project_name="P", source_file_name="f.docx",
                            source_language="English", target_language="Polish",
                            tool_version="test", export_file_path="", timestamp_utc="")
    return text, sidecar


def _edit(text, old_line, new_line):
    assert old_line in text
    return text.replace(old_line, new_line, 1)


def test_filtered_export_matches_by_source_not_number():
    cur = _project()
    # Only the not-started segments (positions 1, 2, 3) → numbered 1, 2, 3.
    text, _ = _export(cur, [1, 2, 3])
    text = _edit(text, "EN: The pump\nPL: ", "EN: The pump\nPL: Pompa")
    text = _edit(text, "EN: Hello\nPL: ", "EN: Hello\nPL: Witam")
    parsed = parse_markdown(text)

    matches = match_rows_by_source(parsed, cur)
    assert [m[0] for m in matches] == [101, 102, 103]

    diffs = build_import_diffs(parsed, None, cur, status_label_to_key=LABELS,
                               row_matches=matches)
    changed = {d.segment_id: d.new_target for d in diffs if d.kind == KIND_CHANGED}
    # Position matching would have written "Pompa" into segment 1 ("Hello").
    assert changed == {101: "Pompa", 102: "Witam"}


def test_repeated_source_pairs_up_in_document_order():
    cur = _project()
    text, _ = _export(cur, [0, 2])  # both "Hello" rows, numbered 1 and 2
    parsed = parse_markdown(text)
    assert [m[0] for m in match_rows_by_source(parsed, cur)] == [100, 102]


def test_reordered_blocks_still_find_their_segments():
    cur = _project()
    text, _ = _export(cur, [0, 1, 2, 3, 4])
    blocks = text.split("\n\n")
    header, segs = blocks[0], [b for b in blocks[1:] if b.strip()]
    shuffled = header + "\n\n" + "\n\n".join(reversed(segs)) + "\n"
    parsed = parse_markdown(shuffled)
    assert [m[0] for m in match_rows_by_source(parsed, cur)] == [104, 103, 102, 101, 100]


def test_edited_source_is_reported_missing_never_written():
    cur = _project()
    text, _ = _export(cur, [1])
    text = _edit(text, "EN: The pump\nPL: ", "EN: The big pump\nPL: Pompa")
    parsed = parse_markdown(text)
    matches = match_rows_by_source(parsed, cur)
    assert matches == [(None, None)]
    diffs = build_import_diffs(parsed, None, cur, status_label_to_key=LABELS,
                               row_matches=matches)
    assert [d.kind for d in diffs] == [KIND_MISSING]


def test_source_line_breaks_and_rewrapping_are_tolerated():
    cur = _project()
    text, _ = _export(cur, [3])
    assert "EN: Two[newline]lines" in text
    # An AI chat that turned the token into a space still matches.
    parsed = parse_markdown(text.replace("Two[newline]lines", "Two  lines"))
    assert match_rows_by_source(parsed, cur)[0][0] == 103


def test_session_sidecar_hint_wins_and_supplies_export_status():
    cur = _project()
    text, sidecar = _export(cur, [2])  # the SECOND "Hello", numbered 1
    parsed = parse_markdown(text)
    # Without the hint, number 1 → position 0, which is also "Hello".
    assert match_rows_by_source(parsed, cur)[0][0] == 100
    assert match_rows_by_source(parsed, cur, sidecar) == [(102, "not_started")]


def test_stale_hint_is_ignored_when_source_no_longer_matches():
    cur = _project()
    _, sidecar = _export(cur, [1])  # hint says number 1 → segment 101 ("The pump")
    text, _ = _export(cur, [4])     # but the pasted text is "Goodbye", numbered 1
    parsed = parse_markdown(text)
    assert match_rows_by_source(parsed, cur, sidecar)[0][0] == 104


def test_unchanged_paste_changes_nothing():
    cur = _project()
    text, _ = _export(cur, [0, 1, 2, 3, 4])
    parsed = parse_markdown(text)
    diffs = build_import_diffs(parsed, None, cur, status_label_to_key=LABELS,
                               row_matches=match_rows_by_source(parsed, cur))
    assert {d.kind for d in diffs} == {KIND_UNCHANGED}


def test_file_import_path_is_unchanged():
    """Without row_matches, the sidecar/position behaviour stays as it was."""
    cur = _project()
    text, sidecar = _export(cur, [1])
    text = _edit(text, "EN: The pump\nPL: ", "EN: The pump\nPL: Pompa")
    parsed = parse_markdown(text)
    diffs = build_import_diffs(parsed, sidecar, cur, status_label_to_key=LABELS)
    assert [(d.segment_id, d.new_target) for d in diffs] == [(101, "Pompa")]
