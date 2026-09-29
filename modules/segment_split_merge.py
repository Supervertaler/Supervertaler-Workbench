"""Segment split & merge logic for Supervertaler Workbench.

Pure, UI-free helpers that operate on a list of Segment-like objects (anything
with ``id``, ``source``, ``target``, ``status``, ``comments`` and the structural
fields ``paragraph_id``, ``file_id``, ``is_table_cell``, ``table_info``,
``category``, ``okapi_tu_id``, ``locked``). They mutate the list in place.

Segments are cloned with ``copy.deepcopy`` so this module never needs to import
the Segment dataclass (which lives in Supervertaler.py).

Eligibility note: the caller is responsible for only invoking these on projects
whose segmentation Supervertaler owns (monolingual DOCX / Okapi / TXT-MD). For
bilingual CAT round-trip formats (sdlxliff, memoQ / Trados / Phrase / Déjà Vu
bilingual, PO) the external tool owns the segment slots, so split/merge must be
disabled — see ``structure_editable`` below.
"""
from __future__ import annotations

import copy
from typing import Callable, List, Tuple


# Status used for the freshly-created right-hand part of a split.
NOT_STARTED = "not_started"

# Least-complete-wins ordering for the merged segment's status.
_STATUS_RANK = {
    "not_started": 0,
    "pretranslated": 1,
    "tm_fuzzy": 1,
    "draft": 2,
    "tm_100": 2,
    "confirmed": 3,
    "proofread": 4,
    "approved": 5,
    "rejected": 2,
}

# Project source-path attributes that mark a bilingual CAT origin (external tool
# owns the segmentation → structural edits are not allowed).
_BILINGUAL_ORIGIN_ATTRS = (
    "trados_source_path",
    "memoq_source_path",
    "mqxliff_source_path",
    "cafetran_source_path",
    "sdlppx_source_path",
    "dejavu_source_path",
    "po_source_path",
)


def structure_editable(project) -> bool:
    """True when the project's segmentation is owned by Supervertaler.

    Monolingual DOCX / Okapi (IDML, HTML, PPTX, XLSX, …) / TXT-MD imports and
    brand-new projects are editable. Bilingual CAT imports are not.
    """
    if project is None:
        return False
    for attr in _BILINGUAL_ORIGIN_ATTRS:
        if getattr(project, attr, None):
            return False
    if getattr(project, "sdlxliff_source_paths", None):
        return False
    return True


def deletable(project) -> bool:
    """True when whole segments may be DELETED outright (not just split/merged).

    Narrower than ``structure_editable``: only paste / plain-text (TXT-MD) /
    Start-Empty projects, which have no structured or bilingual round-trip.
    Split and merge keep all the content, so they're fine on a DOCX / Okapi
    project; deleting *removes* content, which would leave a gap in the
    merged-back export — so DOCX (``original_docx_path``) and every Okapi
    round-trip (``import_engine == "okapi"``) are excluded here too, on top of
    the bilingual-CAT origins already ruled out by ``structure_editable``.
    Plain TXT/MD is fine: its export just re-writes the lines from the segments.
    """
    if not structure_editable(project):
        return False
    if getattr(project, "import_engine", "") == "okapi":
        return False
    if getattr(project, "original_docx_path", None):
        return False
    return True


def split_source_text(text: str, segment_text: Callable[[str], List[str]]) -> List[Tuple[int, str]]:
    """Break pasted source text into ``(paragraph, sentence)`` pairs (issue #173).

    Each non-blank line is one paragraph, numbered from 1 in order; blank lines
    only separate paragraphs. ``segment_text`` is the project's sentence
    segmenter (``SimpleSegmenter().segment_text`` or the Markdown one), so
    added text is split exactly as it would have been at project creation.
    Paragraph numbers matter because plain-text export writes one line per
    paragraph: without them, added text would be glued onto the last line.
    """
    pairs: List[Tuple[int, str]] = []
    paragraph = 0
    for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line.strip():
            continue
        paragraph += 1
        for sentence in segment_text(line.strip()):
            if sentence and sentence.strip():
                pairs.append((paragraph, sentence.strip()))
    return pairs


def delete_segments(segments: List, indices) -> int:
    """Delete the segments at ``indices`` from the list in place, skipping any
    locked segment. Deletes high-index-first so earlier indices stay valid.
    Returns the number removed. Caller should renumber ids afterwards.
    """
    to_del = sorted(
        {i for i in indices
         if 0 <= i < len(segments) and not getattr(segments[i], "locked", False)},
        reverse=True,
    )
    for i in to_del:
        del segments[i]
    return len(to_del)


def _join_text(a: str, b: str) -> str:
    """Concatenate two fragments, inserting a single space only when neither
    side already provides whitespace at the boundary (mirrors how the Okapi
    merge-export reassembles sentence segments)."""
    a = a or ""
    b = b or ""
    if not a:
        return b
    if not b:
        return a
    if a[-1].isspace() or b[0].isspace():
        return a + b
    return a + " " + b


def _join_with(a: str, b: str, separator) -> str:
    """Like ``_join_text``, but with the separator the segmenter recorded
    between the two segments (``join_before``, issue #191) when it is known –
    a custom rule may have split where there was no space at all."""
    if separator is None:
        return _join_text(a, b)
    a = a or ""
    b = b or ""
    if not a or not b:
        return a or b
    return a + separator + b


def can_split(segment, offset: int) -> bool:
    """A split is valid strictly inside the source text of an unlocked segment."""
    if getattr(segment, "locked", False):
        return False
    src = segment.source or ""
    return 0 < offset < len(src)


def can_merge(segments: List, idx: int) -> Tuple[bool, str]:
    """Whether segment ``idx`` may be merged with the next one.

    Mirrors Trados/memoQ: only within the same paragraph / structural context.
    Returns (ok, reason). ``reason`` is a short human-readable explanation when
    not ok (used for the disabled-action tooltip).
    """
    if idx < 0 or idx + 1 >= len(segments):
        return False, "There is no next segment to merge with."
    a = segments[idx]
    b = segments[idx + 1]
    if getattr(a, "locked", False) or getattr(b, "locked", False):
        return False, "One of the segments is locked."
    if getattr(a, "file_id", None) != getattr(b, "file_id", None):
        return False, "The next segment is in a different file."
    if getattr(a, "paragraph_id", 0) != getattr(b, "paragraph_id", 0):
        return False, "The next segment is in a different paragraph."
    if (getattr(a, "is_table_cell", False) != getattr(b, "is_table_cell", False)
            or getattr(a, "table_info", None) != getattr(b, "table_info", None)):
        return False, "The next segment is in a different table cell."
    if getattr(a, "category", "") != getattr(b, "category", ""):
        return False, "The next segment belongs to a different document part."
    ta = getattr(a, "okapi_tu_id", "") or ""
    tb = getattr(b, "okapi_tu_id", "") or ""
    if ta != tb:
        return False, "The next segment is in a different text unit."
    return True, ""


def _partition_comments_on_split(comments, offset: int):
    """Split a comment list at a source character offset.

    Source-anchored comments are routed to the left or right part (with the
    right part's offsets rebased to 0); a comment straddling the cut is clamped
    to the left part. Target-anchored and segment-level comments stay on the
    left part (which keeps the existing target).
    """
    left, right = [], []
    for c in comments or []:
        field = getattr(c, "anchor_field", "")
        start = getattr(c, "anchor_start", 0) or 0
        end = getattr(c, "anchor_end", 0) or 0
        if field == "source" and end > start:
            if end <= offset:
                left.append(c)
            elif start >= offset:
                cc = copy.deepcopy(c)
                cc.anchor_start = start - offset
                cc.anchor_end = end - offset
                right.append(cc)
            else:  # straddles the cut → clamp to the left part
                cc = copy.deepcopy(c)
                cc.anchor_end = offset
                left.append(cc)
        else:
            left.append(c)
    return left, right


def split_segment(segments: List, idx: int, offset: int) -> object:
    """Split ``segments[idx]`` at source character ``offset``.

    The left part keeps the existing target, status and (left-side) comments;
    the new right part gets the remaining source, an empty target and
    ``not_started`` status. The new segment is inserted at ``idx + 1`` and
    returned. Caller should renumber ids afterwards.
    """
    seg = segments[idx]
    src = seg.source or ""
    left_src, right_src = src[:offset], src[offset:]

    new = copy.deepcopy(seg)

    left_comments, right_comments = _partition_comments_on_split(seg.comments, offset)

    seg.source = left_src
    seg.comments = left_comments
    seg.modified = True

    new.source = right_src
    new.target = ""
    new.status = NOT_STARTED
    new.match_percent = None
    new.memoQ_status = ""
    new.comments = right_comments
    new.proofreading_notes = {}
    new.modified = True
    if hasattr(new, "join_before"):
        new.join_before = ""      # the cut keeps any whitespace inside the text

    segments.insert(idx + 1, new)
    return new


def _merge_status(a, b) -> str:
    """Least-complete status wins when merging two segments."""
    ra = _STATUS_RANK.get(getattr(a, "status", NOT_STARTED), 0)
    rb = _STATUS_RANK.get(getattr(b, "status", NOT_STARTED), 0)
    return a.status if ra <= rb else b.status


def merge_with_next(segments: List, idx: int) -> object:
    """Merge ``segments[idx]`` with ``segments[idx + 1]`` in place.

    Source and target are joined with smart spacing; the next segment's comment
    anchors are shifted to their new positions; the merged segment takes the
    least-complete status. The next segment is removed and the merged segment
    (still at ``idx``) is returned. Caller should renumber ids afterwards.
    """
    a = segments[idx]
    b = segments[idx + 1]

    separator = getattr(b, "join_before", None)
    merged_source = _join_with(a.source, b.source, separator)
    src_shift = len(merged_source) - len(a.source or "") - len(b.source or "")
    src_offset_b = len(a.source or "") + src_shift

    merged_target = _join_with(a.target, b.target, separator)
    tgt_shift = len(merged_target) - len(a.target or "") - len(b.target or "")
    tgt_offset_b = len(a.target or "") + tgt_shift

    for c in (b.comments or []):
        field = getattr(c, "anchor_field", "")
        start = getattr(c, "anchor_start", 0) or 0
        end = getattr(c, "anchor_end", 0) or 0
        if end > start:
            if field == "source":
                c.anchor_start = start + src_offset_b
                c.anchor_end = end + src_offset_b
            elif field == "target":
                c.anchor_start = start + tgt_offset_b
                c.anchor_end = end + tgt_offset_b

    a.status = _merge_status(a, b)
    a.source = merged_source
    a.target = merged_target
    a.comments = list(a.comments or []) + list(b.comments or [])
    a.match_percent = None
    a.modified = True

    del segments[idx + 1]
    return a


def renumber_ids(segments: List) -> None:
    """Renumber segment ids to a contiguous run, preserving the existing base
    (so the grid's # column stays clean after a structural edit)."""
    if not segments:
        return
    base = segments[0].id
    for i, s in enumerate(segments):
        s.id = base + i
