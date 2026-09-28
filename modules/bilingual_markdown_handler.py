"""Bilingual *Re-importable Text* handler — AI-friendly bracketed export/import.

Added in v1.10.231. Ports the "[SEGMENT NNNN]" round-trip from the Supervertaler
for Trados plugin to the Workbench.

**Why "Text", not "Markdown".** The header has a couple of decorative touches,
but the substance — the segment blocks — is a plain labelled-line format whose
meaning depends on line breaks being preserved. A Markdown renderer collapses
single newlines into spaces, which would destroy the structure. AI agents read
the *raw* characters (newlines included) when you paste the file into a chat, so
plain text is both safe and maximally readable; calling it Markdown would only
invite a renderer to mangle it. The user-facing name is therefore "Re-importable
Text" with a ``.txt`` extension. (The module/function names keep the historical
``markdown`` wording as an internal implementation detail.)

The export produces TWO files:

1. A human/AI-readable text file (``…​.txt``) — one block per segment:

       [SEGMENT 0001]
       EN: The <b>quick</b> fox {1}
       NL: De <b>snelle</b> vos {1}
       Status: Draft

   A proofreader or an LLM edits the target lines, then the file is
   re-imported into the *same open project*.

2. A ``.svexport.json`` **sidecar** next to it (``<md path> + ".svexport.json"``)
   that records, per segment: the bracket number, the Workbench ``Segment.id``,
   a short SHA-256 source hash (tamper detection), the status, the source file
   name, and the lock flag. The sidecar is the source of truth for matching the
   edited Markdown back to the right live segments and for validating that the
   source text wasn't altered.

Design notes / deliberate differences from the Trados original
--------------------------------------------------------------
* **Tags stay as literal text.** Workbench stores inline tags as literal
  substrings inside ``source``/``target`` (``<b>``, ``{1}``, ``[1}``, ``<92>``,
  …) — there is no structured tag model like Trados' ``ISegment``. So we write
  the segment text verbatim. Cosmetic formatting tags (``<b> <i> <u> <bi> <sub>
  <sup>``) may be freely added/removed by the editor; *structural* tags
  (numbered placeholders, memoQ/Trados/DéjàVu tags) are counted and a mismatch
  blocks the segment under strict mode — mirroring the Trados tag-integrity gate.
* **Source line breaks are shown as ``[newline]`` (same as the target).** A hard
  break inside the source — e.g. a two-line subtitle cue — is written as the
  literal ``[newline]`` token so the source stays on one physical line while its
  original line structure stays visible (handy for spotting a target that is
  missing a break the source has). The source is read-only and never written
  back, so it is not decoded; the source hash is computed over this written
  (``[newline]``-encoded) form, so tamper detection round-trips. Files exported
  before this used a space-flattened source, and their own sidecar hashes match
  that form, so they still re-import unchanged.
* **Targets are single-line; in-target breaks use a ``[newline]`` token.** On
  export, a hard line break inside the target (e.g. a two-line subtitle) is
  written as the literal token ``[newline]`` so every segment field stays on one
  physical line — the most robust shape for AI agents (a bare ``\n`` invites the
  model to expand it back into a real break; a bracketed word reads as a
  preserve-verbatim placeholder, like the inline tags, and is not caught by the
  tag-integrity regex). On import the token is decoded back to ``\n``. For
  backward compatibility the parser ALSO still accepts a genuinely multi-line
  target (everything up to the ``Status:``/blank line), so files exported before
  this change re-import unchanged.
* **Status round-trips.** The Trados build parsed the ``Status:`` line but never
  applied it. Here it is applied: if the editor changed the status line it wins;
  otherwise a segment whose target changed is marked ``draft``.

The module is intentionally free of any Qt or Supervertaler imports so it can be
unit-tested in isolation; the calling code in ``Supervertaler.py`` owns all the
dialog/grid work and the status vocabulary mapping.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Tuple


EXPORT_VERSION = "1.0"
SIDECAR_SUFFIX = ".svexport.json"
LOCK_PREFIX = "🔒 "  # U+1F512 + space, prepended to a locked segment's Status line
FILE_HEADER_PREFIX = "## 📄 File: "  # U+1F4C4
# Inline marker for a hard line break inside a target, so the target stays on one
# physical line on export (decoded back to "\n" on import). A bracketed word is
# preferred over a bare "\n" because LLM agents tend to expand "\n" into a real
# newline when editing, whereas a placeholder token is left verbatim.
NEWLINE_TOKEN = "[newline]"


# ---------------------------------------------------------------------------
# Language code helper
# ---------------------------------------------------------------------------

# Display-name → 2-letter uppercase code, mirroring the Trados renderer's table.
_LANG_NAME_TO_CODE = {
    "english": "EN", "dutch": "NL", "nederlands": "NL", "german": "DE",
    "deutsch": "DE", "french": "FR", "français": "FR", "francais": "FR",
    "spanish": "ES", "español": "ES", "espanol": "ES", "italian": "IT",
    "italiano": "IT", "portuguese": "PT", "português": "PT", "portugues": "PT",
    "polish": "PL", "polski": "PL", "russian": "RU", "русский": "RU",
    "czech": "CS", "slovak": "SK", "slovenian": "SL", "croatian": "HR",
    "danish": "DA", "swedish": "SV", "norwegian": "NO", "finnish": "FI",
    "hungarian": "HU", "romanian": "RO", "turkish": "TR", "ukrainian": "UK",
    "greek": "EL", "japanese": "JA", "chinese": "ZH", "korean": "KO",
    "arabic": "AR", "hebrew": "HE",
}


def lang_to_code(language: Optional[str]) -> str:
    """Derive a short uppercase language code from a display name or BCP-47 code.

    Mirrors the Trados renderer: try a name lookup on the base name (before any
    " (variant)" or region subtag); otherwise fall back to the first two
    characters uppercased.
    """
    if not language:
        return "??"
    # Delegate to the single language authority so the name↔code mapping is
    # shared with the rest of the app; preserve this function's contract
    # (uppercase 2-letter code, "??" when unresolvable).
    try:
        from modules import language_codes as _lc
    except ImportError:
        import language_codes as _lc
    return (_lc.base_code(language) or "").upper() or "??"


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def hash_prefix(text: str) -> str:
    """Short source hash for tamper detection: first 16 hex chars of SHA-256.

    Matches the Trados ``BilingualExporter.HashPrefix`` (8 bytes = 16 hex chars).
    """
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Tag classification (for the integrity gate)
# ---------------------------------------------------------------------------

# Any tag-like token across the dialects Workbench segment text may contain:
#   <...>            HTML/XML or Trados numeric (<b>, <92>, </92>, <li>, …)
#   [1}  {1]  [1]    memoQ open / close / standalone
#   {1} {/1} {1/}    compact-tag numbered placeholders
#   {NNNNN}          DéjàVu 5-digit, or a named {placeholder}
_TAG_TOKEN_RE = re.compile(
    r"(<[^>\n]+>|\[\d+\}|\{\d+\]|\[\d+\]|\{/?\d+/?\}|\{[A-Za-z0-9_]+\})"
)

# Cosmetic formatting tags that may be freely added/removed by the editor —
# excluded from the structural integrity count.
_COSMETIC_TAG_RE = re.compile(
    r"^</?(?:b|i|u|bi|sub|sup|em|strong|s)>$", re.IGNORECASE
)


def iter_tag_tokens(text: str) -> List[str]:
    """Return every tag-like token in *text* (any dialect), in order."""
    if not text:
        return []
    return _TAG_TOKEN_RE.findall(text)


def structural_tag_count(text: str) -> int:
    """Count *structural* (non-cosmetic) tags — the ones that must be preserved.

    Cosmetic formatting tags (<b>/<i>/<u>/<bi>/<sub>/<sup>/<em>/<strong>/<s>)
    are excluded, mirroring the Trados rule that lets the editor freely add or
    drop bold/italic/underline while still guarding structural placeholders.
    """
    return sum(1 for t in iter_tag_tokens(text) if not _COSMETIC_TAG_RE.match(t))


# ---------------------------------------------------------------------------
# Export data model
# ---------------------------------------------------------------------------

@dataclass
class MdExportSegment:
    """One segment to export, in the order it should appear."""
    number: int          # 1-based bracket number ([SEGMENT NNNN])
    segment_id: int      # Workbench Segment.id (stable match key)
    source: str
    target: str
    status_key: str = ""        # Workbench status key (stored in sidecar)
    status_label: str = ""      # Display label for the Status: line ("" omits it)
    locked: bool = False
    file_name: str = ""         # for multi-file projects
    comment: str = ""           # segment comment text (Workbench seg.notes); "" omits the Comment: line


def _encode_breaks(text: str) -> str:
    """Render a (possibly multi-line) value on ONE physical line, writing each
    hard line break as the literal ``[newline]`` token. Shared by source and
    target so every segment field stays on a single line."""
    if not text:
        return ""
    return _normalise_newlines(text).replace("\n", NEWLINE_TOKEN)


def _normalise_newlines(text: str) -> str:
    if not text:
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def build_markdown(
    segments: List[MdExportSegment],
    *,
    project_name: str,
    source_file_name: str,
    source_lang_display: str,
    target_lang_display: str,
    tool_version: str,
    source_code: Optional[str] = None,
    target_code: Optional[str] = None,
    multi_file: bool = False,
    status_choices: Optional[List[str]] = None,
) -> str:
    """Build the Bracketed Markdown document text (UTF-8, ``\\n`` newlines).

    ``status_choices`` is an optional list of human-readable status labels the
    editor may set the ``Status:`` line to; it's listed in the header note.
    """
    src_code = (source_code or lang_to_code(source_lang_display) or "SRC")
    tgt_code = (target_code or lang_to_code(target_lang_display) or "TGT")

    total = len(segments)
    max_num = max((s.number for s in segments), default=1)
    pad = max(4, len(str(max_num)))

    rule = "=" * 72

    lines: List[str] = []
    # ── Header / preamble (plain text — see module docstring on why this is
    #    NOT Markdown: the segment blocks rely on preserved line breaks, which
    #    a Markdown renderer would collapse). ────────────────────────────────
    lines.append(rule)
    lines.append("  SUPERVERTALER RE-IMPORTABLE TEXT")
    lines.append(rule)
    lines.append(f"  Project:      {project_name}")
    lines.append(f"  Source file:  {source_file_name}")
    lines.append(f"  Languages:    {source_lang_display} -> {target_lang_display}")
    lines.append(f"  Segments:     {total}")
    lines.append(f"  Tool:         Supervertaler Workbench {tool_version}")
    lines.append("")
    lines.append("  HOW TO EDIT THIS FILE")
    lines.append(f"  - Do not change the [SEGMENT N] markers or the {src_code}: source lines.")
    lines.append(f"    (A [newline] in a {src_code}: source marks a break in the original,")
    lines.append(f"    read-only source — e.g. a two-line subtitle.)")
    lines.append(f"  - Edit the {tgt_code}: target text freely, but keep it on ONE line;")
    lines.append(f"    write the literal token [newline] where a line break is needed")
    lines.append(f"    (e.g. to split a subtitle into two lines).")
    lines.append("  - Edit or add a comment on the Comment: line (leave it blank if none).")
    if status_choices:
        lines.append(f"  - You may set Status: to one of: {', '.join(status_choices)}.")
    lines.append("  - Then re-import into Supervertaler to update the project.")
    lines.append(rule)
    lines.append("")

    # ── Segment blocks ─────────────────────────────────────────────────────
    last_file = None
    for seg in segments:
        if multi_file and seg.file_name and seg.file_name != last_file:
            lines.append(f"{FILE_HEADER_PREFIX}{seg.file_name}")
            lines.append("")
            last_file = seg.file_name

        lines.append(f"[SEGMENT {seg.number:0{pad}d}]")
        # Both source and target stay on ONE physical line: hard breaks become
        # [newline] tokens. The target is decoded back to "\n" on import; the
        # source is read-only reference. See module docstring.
        lines.append(f"{src_code}: {_encode_breaks(seg.source)}")
        lines.append(f"{tgt_code}: {_encode_breaks(seg.target)}")
        if seg.status_label:
            status_text = seg.status_label
            if seg.locked:
                status_text = LOCK_PREFIX + status_text
            lines.append(f"Status: {status_text}")
        # Always emit a Comment: line — empty when there's no comment — so the
        # editor can see the field exists and add one. May be multi-line.
        comment_text = _normalise_newlines(seg.comment or "")
        lines.append((f"Comment: {comment_text}").rstrip())
        lines.append("")  # blank line terminates the block

    return "\n".join(lines) + "\n"


def build_sidecar(
    segments: List[MdExportSegment],
    *,
    project_name: str,
    source_file_name: str,
    source_language: str,
    target_language: str,
    tool_version: str,
    export_file_path: str,
    timestamp_utc: str,
) -> dict:
    """Build the ``.svexport.json`` sidecar manifest as a plain dict."""
    seg_objs = []
    for seg in segments:
        seg_objs.append({
            "number": seg.number,
            "segment_id": seg.segment_id,
            "source_hash": hash_prefix(_encode_breaks(seg.source)),
            "status": seg.status_key or "",
            "source_file_name": seg.file_name or "",
            "is_locked": bool(seg.locked),
        })
    return {
        "version": EXPORT_VERSION,
        "project_name": project_name,
        "source_file_name": source_file_name,
        "source_language": source_language,
        "target_language": target_language,
        "export_timestamp_utc": timestamp_utc,
        "format": "text",
        "layout": "Bracketed",
        "tool_version": tool_version,
        "export_file_path": export_file_path,
        "segments": seg_objs,
    }


def sidecar_path_for(md_path: str) -> str:
    """The sidecar path for a given Markdown export path."""
    return md_path + SIDECAR_SUFFIX


def write_export(md_path: str, md_text: str, sidecar: dict) -> str:
    """Write the ``.md`` and its ``.svexport.json`` sidecar (UTF-8, no BOM, \\n).

    Returns the sidecar path.
    """
    with open(md_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(md_text)
    side_path = sidecar_path_for(md_path)
    payload = json.dumps(sidecar, ensure_ascii=False, indent=2)
    with open(side_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(payload + "\n")
    return side_path


# ---------------------------------------------------------------------------
# Import: parsing
# ---------------------------------------------------------------------------

_SEGMENT_ANCHOR_RE = re.compile(r"^\[SEGMENT\s+(\d+)\]\s*$")
_LANG_LINE_RE = re.compile(r"^([A-Za-z]{2,3}):[ \t]?(.*)$")
_STATUS_LINE_RE = re.compile(r"^Status:\s*(.*)$")
_COMMENT_LINE_RE = re.compile(r"^Comment:[ \t]?(.*)$")
_FILE_HEADER_RE = re.compile(r"^##\s")


def _terminates_target(line: str) -> bool:
    """A line that ends multi-line *target* capture."""
    return (
        line.strip() == ""
        or _STATUS_LINE_RE.match(line) is not None
        or _COMMENT_LINE_RE.match(line) is not None
        or _SEGMENT_ANCHOR_RE.match(line) is not None
        or _FILE_HEADER_RE.match(line) is not None
    )


def _terminates_comment(line: str) -> bool:
    """A line that ends multi-line *comment* capture."""
    return (
        line.strip() == ""
        or _STATUS_LINE_RE.match(line) is not None
        or _SEGMENT_ANCHOR_RE.match(line) is not None
        or _FILE_HEADER_RE.match(line) is not None
    )


@dataclass
class ParsedMdSegment:
    number: int
    source: str
    target: str
    status_label: str = ""
    comment: str = ""


def looks_like_bracketed_markdown(text: str) -> bool:
    """Quick sniff: does the text contain at least one ``[SEGMENT N]`` anchor?"""
    if not text:
        return False
    return bool(re.search(r"^\[SEGMENT\s+\d+\]\s*$", text, re.MULTILINE))


def parse_markdown(md_text: str) -> List[ParsedMdSegment]:
    """Parse a Bracketed Markdown document into segments.

    Rules (robust against multi-line targets and proofreader edits):
      * Blocks run from one ``[SEGMENT N]`` anchor to the next anchor / file
        header / end of file.
      * The first ``CODE:`` line (code != "Status") is the source (single line).
      * The next ``CODE:`` line begins the target; the target is that line's
        remainder plus all following lines up to a ``Status:`` line, a blank
        line, the next anchor, or a file header.
      * ``Status:`` line (optional) supplies the status label (🔒 prefix stripped).
    """
    text = _normalise_newlines(md_text)
    lines = text.split("\n")

    # Locate anchors.
    anchors: List[tuple] = []  # (line_index, number)
    for i, line in enumerate(lines):
        m = _SEGMENT_ANCHOR_RE.match(line)
        if m:
            anchors.append((i, int(m.group(1))))

    results: List[ParsedMdSegment] = []
    for idx, (start, number) in enumerate(anchors):
        end = anchors[idx + 1][0] if idx + 1 < len(anchors) else len(lines)
        block = lines[start + 1:end]
        results.append(_parse_block(number, block))
    return results


def _parse_block(number: int, block: List[str]) -> ParsedMdSegment:
    source = ""
    target = ""
    status_label = ""
    comment = ""

    def _is_lang_line(line: str):
        if _STATUS_LINE_RE.match(line) or _COMMENT_LINE_RE.match(line):
            return None
        m = _LANG_LINE_RE.match(line)
        if m and m.group(1).lower() not in ("status", "comment"):
            return m
        return None

    # Find the source line (first CODE: line).
    src_idx = None
    for i, line in enumerate(block):
        m = _is_lang_line(line)
        if m:
            source = m.group(2)
            src_idx = i
            break

    # Find the target line (next CODE: line after the source); target may be
    # multi-line, ending at a Status:/Comment:/blank/anchor/file-header line.
    tgt_idx = None
    first_remainder = ""
    if src_idx is not None:
        for i in range(src_idx + 1, len(block)):
            m = _is_lang_line(block[i])
            if m:
                tgt_idx = i
                first_remainder = m.group(2)
                break

    if tgt_idx is not None:
        target_lines: List[str] = []
        if first_remainder:
            target_lines.append(first_remainder)
        for i in range(tgt_idx + 1, len(block)):
            if _terminates_target(block[i]):
                break
            target_lines.append(block[i])
        # Decode the inline line-break token back to a real newline. Old files
        # with a genuinely multi-line target have no token, so this is a no-op
        # for them — keeping the parser backward-compatible.
        target = "\n".join(target_lines).replace(NEWLINE_TOKEN, "\n").strip()

    # Status line (first one in the block).
    for line in block:
        m = _STATUS_LINE_RE.match(line)
        if m:
            status_label = m.group(1).strip()
            if status_label.startswith(LOCK_PREFIX):
                status_label = status_label[len(LOCK_PREFIX):].strip()
            elif status_label.startswith("🔒"):
                status_label = status_label[1:].strip()
            break

    # Comment block (first Comment: line; may be multi-line).
    for i, line in enumerate(block):
        m = _COMMENT_LINE_RE.match(line)
        if m:
            comment_lines: List[str] = []
            if m.group(1):
                comment_lines.append(m.group(1))
            for j in range(i + 1, len(block)):
                if _terminates_comment(block[j]):
                    break
                comment_lines.append(block[j])
            comment = "\n".join(comment_lines).strip()
            break

    return ParsedMdSegment(
        number=number, source=source, target=target,
        status_label=status_label, comment=comment,
    )


def load_sidecar(md_or_sidecar_path: str) -> Optional[dict]:
    """Load the sidecar for a Markdown path (or a direct sidecar path).

    Returns the parsed dict, or ``None`` if the file is missing/unreadable.
    """
    path = md_or_sidecar_path
    if not path.endswith(SIDECAR_SUFFIX):
        path = sidecar_path_for(md_or_sidecar_path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Import: diffing
# ---------------------------------------------------------------------------

# Change kinds.
KIND_CHANGED = "changed"
KIND_UNCHANGED = "unchanged"
KIND_MISSING = "missing"            # no matching live segment
KIND_SOURCE_MISMATCH = "source_mismatch"  # source hash differs (tampered)
KIND_TAG_MISMATCH = "tag_mismatch"  # structural tag count differs
KIND_LOCKED = "locked"             # target changed but segment is locked


@dataclass
class CurrentSeg:
    """Lightweight live-segment snapshot the diff engine works against."""
    id: int
    number: int       # 1-based position at the time of diffing
    source: str
    target: str
    status_key: str
    locked: bool = False
    comment: str = ""   # current joined comment text (Workbench seg.notes)


@dataclass
class ImportDiff:
    number: int
    segment_id: Optional[int]
    kind: str
    # ``None`` for a field means "leave it unchanged"; a string (incl. "") means
    # "apply this value". So a comment-only edit has new_target=None.
    new_target: Optional[str] = None
    new_status_key: Optional[str] = None
    new_comment: Optional[str] = None
    note: str = ""


def _normalise_for_compare(text: str) -> str:
    """CRLF→LF, per-line right-trim, overall trim (matches Trados compare)."""
    t = _normalise_newlines(text)
    t = "\n".join(line.rstrip() for line in t.split("\n"))
    return t.strip()


def _source_key(text: str) -> str:
    """Whitespace-insensitive comparison key for a source line.

    ``[newline]`` tokens and real line breaks count as whitespace, so a source
    matches whether it was written with tokens (current exports), flattened to
    spaces (pre-token exports) or re-wrapped by an AI chat."""
    t = _normalise_newlines(text or "").replace(NEWLINE_TOKEN, " ")
    return re.sub(r"\s+", " ", t).strip()


RowMatch = Tuple[Optional[int], Optional[str]]  # (live segment id, export-time status key)


def match_rows_by_source(
    parsed: List[ParsedMdSegment],
    current_segments: List[CurrentSeg],
    hint_sidecar: Optional[dict] = None,
) -> List[RowMatch]:
    """Find the live segment for each parsed row from its *source text*.

    Used when there is no sidecar file – text pasted back from an AI chat
    (issue #248). The ``[SEGMENT N]`` number alone can't be trusted then: an
    export filtered by status numbers its blocks 1..n regardless of where they
    sit in the project, and the AI may reorder or drop blocks. The source line
    can: it is read-only by contract, so a row whose source matches no live
    segment is simply reported as missing rather than written somewhere wrong.

    For each row, in order of preference:
      1. the ``hint_sidecar`` entry for its number (the last export or copy of
         this project in this session), if that segment's source still matches;
      2. the live segment at position ``number``, if its source matches;
      3. the next unused segment with a matching source after the previous
         match, else the first unused one anywhere – so repeated sources are
         paired up in document order.

    Returns one ``(segment_id, export_status_key)`` per row (same order as
    ``parsed``); ``segment_id`` is ``None`` when nothing matches. The status is
    only known when the hint sidecar supplied the match.
    """
    keys = [_source_key(c.source) for c in current_segments]
    index_by_id = {c.id: i for i, c in enumerate(current_segments)}
    positions: Dict[str, List[int]] = {}
    for i, k in enumerate(keys):
        if k:
            positions.setdefault(k, []).append(i)

    hint_by_number: Dict[int, dict] = {}
    if hint_sidecar and isinstance(hint_sidecar.get("segments"), list):
        for entry in hint_sidecar["segments"]:
            n = _as_int(entry.get("number")) if isinstance(entry, dict) else None
            if n is not None:
                hint_by_number[n] = entry

    used = set()
    last = -1
    matches: List[RowMatch] = []
    for row in parsed:
        key = _source_key(row.source)
        idx = None
        status = None
        if key:
            entry = hint_by_number.get(row.number)
            if entry is not None:
                i = index_by_id.get(_as_int(entry.get("segment_id")))
                if i is not None and i not in used and keys[i] == key:
                    idx = i
                    status = str(entry.get("status") or "") or None
            if idx is None:
                i = row.number - 1
                if 0 <= i < len(keys) and i not in used and keys[i] == key:
                    idx = i
            if idx is None:
                free = [i for i in positions.get(key, ()) if i not in used]
                after = [i for i in free if i > last]
                idx = (after or free or [None])[0]
        if idx is None:
            matches.append((None, None))
            continue
        used.add(idx)
        last = idx
        matches.append((current_segments[idx].id, status))
    return matches


def build_import_diffs(
    parsed: List[ParsedMdSegment],
    sidecar: Optional[dict],
    current_segments: List[CurrentSeg],
    *,
    status_label_to_key: Dict[str, str],
    strict_tags: bool = True,
    check_source: bool = True,
    row_matches: Optional[List[RowMatch]] = None,
) -> List[ImportDiff]:
    """Match parsed Markdown segments to live segments and classify each.

    Matching priority for each parsed row (by its ``[SEGMENT N]`` number):
      1. sidecar entry for that number → its ``segment_id`` → live segment by id
      2. fall back to live segment at position ``number`` (1-based)

    Args:
        parsed: segments parsed from the edited Markdown.
        sidecar: the loaded ``.svexport.json`` dict, or ``None`` (position match).
        current_segments: live snapshots (id, number, source, target, status_key, locked).
        status_label_to_key: maps a Status-line label back to a Workbench key.
        strict_tags: if True, a structural tag-count mismatch is flagged and not
            applied; if False it is applied with a warning note.
        check_source: if True, a source-hash mismatch is flagged (tamper guard).
        row_matches: explicit matches from :func:`match_rows_by_source`, one per
            parsed row. When given, ``sidecar`` is ignored and there is no
            position fallback: an unmatched row is reported as missing.
    """
    by_id = {c.id: c for c in current_segments}
    by_number = {c.number: c for c in current_segments}

    sidecar_by_number: Dict[int, dict] = {}
    if sidecar and isinstance(sidecar.get("segments"), list):
        for entry in sidecar["segments"]:
            try:
                sidecar_by_number[int(entry["number"])] = entry
            except (KeyError, ValueError, TypeError):
                continue

    diffs: List[ImportDiff] = []
    for row_index, row in enumerate(parsed):
        cur: Optional[CurrentSeg] = None
        if row_matches is not None:
            seg_id, export_status = row_matches[row_index]
            # Source was the match key, so there is no hash to check; only the
            # export-time status (known when a session sidecar supplied it).
            side = {"status": export_status} if export_status else None
            if seg_id is not None:
                cur = by_id.get(seg_id)
            if cur is None:
                diffs.append(ImportDiff(
                    row.number, None, KIND_MISSING,
                    note="Source text not found in the open project."))
                continue
        else:
            side = sidecar_by_number.get(row.number)
            if side is not None and side.get("segment_id") is not None:
                cur = by_id.get(_as_int(side.get("segment_id")))
            if cur is None:
                cur = by_number.get(row.number)

        if cur is None:
            diffs.append(ImportDiff(row.number, None, KIND_MISSING,
                                    note="No matching segment in the open project."))
            continue

        # Source tamper check (only when the sidecar supplies a hash and the row
        # kept a non-empty source).
        if check_source and side is not None and side.get("source_hash") and row.source.strip():
            if hash_prefix(row.source) != str(side.get("source_hash")):
                diffs.append(ImportDiff(row.number, cur.id, KIND_SOURCE_MISMATCH,
                                        note="Source text was modified — skipped."))
                continue

        new_target = _normalise_newlines(row.target)
        target_changed = (
            _normalise_for_compare(new_target) != _normalise_for_compare(cur.target))

        new_comment = _normalise_newlines(row.comment)
        comment_changed = (
            _normalise_for_compare(new_comment) != _normalise_for_compare(cur.comment))

        # Resolve status: a deliberate Status-line change wins; otherwise a
        # changed target is marked 'draft'.
        new_status_key = _resolve_status(
            row, side, cur, target_changed, status_label_to_key)
        status_changed = bool(new_status_key) and new_status_key != cur.status_key

        if not (target_changed or comment_changed or status_changed):
            diffs.append(ImportDiff(row.number, cur.id, KIND_UNCHANGED))
            continue

        # Target-specific guards (structural-tag integrity + lock). A failed
        # guard skips the WHOLE segment — target, comment and status — so a
        # suspect block is never half-applied.
        tag_note = ""
        if target_changed:
            src_struct = structural_tag_count(cur.source)
            tgt_struct = structural_tag_count(new_target)
            if src_struct != tgt_struct:
                if strict_tags:
                    diffs.append(ImportDiff(
                        row.number, cur.id, KIND_TAG_MISMATCH,
                        note=f"Structural tag count differs (source {src_struct}, "
                             f"target {tgt_struct}) — skipped (strict mode)."))
                    continue
                tag_note = (f"Applied despite tag-count mismatch (source "
                            f"{src_struct}, target {tgt_struct}).")
            if cur.locked:
                diffs.append(ImportDiff(
                    row.number, cur.id, KIND_LOCKED,
                    note="Segment is locked — skipped."))
                continue

        notes = []
        if tag_note:
            notes.append(tag_note)
        if comment_changed and not target_changed and not status_changed:
            notes.append("comment only")

        # Carry only the fields that actually changed (None = leave untouched).
        diffs.append(ImportDiff(
            row.number, cur.id, KIND_CHANGED,
            new_target=new_target if target_changed else None,
            new_comment=new_comment if comment_changed else None,
            new_status_key=new_status_key if status_changed else None,
            note="; ".join(notes)))

    return diffs


def _resolve_status(
    row: ParsedMdSegment,
    side: Optional[dict],
    cur: CurrentSeg,
    target_changed: bool,
    status_label_to_key: Dict[str, str],
) -> Optional[str]:
    """Decide the status to apply for a row.

    * If the Status line maps to a known key and differs from the status the
      sidecar recorded at export, the editor changed it deliberately → use it.
    * Else if the target changed → 'draft' (it now has an unconfirmed translation).
    * Else keep the current status.
    """
    parsed_key = None
    if row.status_label:
        parsed_key = status_label_to_key.get(row.status_label.strip().lower())
    sidecar_key = (str(side.get("status")) if side and side.get("status") else None)

    if parsed_key is not None and parsed_key != sidecar_key:
        return parsed_key
    if target_changed:
        return "draft"
    return cur.status_key


def _as_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # A small round-trip sanity check.
    segs = [
        MdExportSegment(1, 101, "The <b>quick</b> fox {1}", "De <b>snelle</b> vos {1}",
                        status_key="confirmed", status_label="Confirmed"),
        MdExportSegment(2, 102, "Hello world", "",
                        status_key="not_started", status_label="Not started"),
        MdExportSegment(3, 103, "Line one\nline two", "Regel een\nregel twee",
                        status_key="draft", status_label="Draft", locked=True),
    ]
    md = build_markdown(
        segs, project_name="Demo", source_file_name="demo.docx",
        source_lang_display="English", target_lang_display="Dutch",
        tool_version="1.10.231",
    )
    print(md)
    print("=" * 60)

    parsed = parse_markdown(md)
    assert len(parsed) == 3, parsed
    assert parsed[0].source == "The <b>quick</b> fox {1}", repr(parsed[0].source)
    assert parsed[0].target == "De <b>snelle</b> vos {1}", repr(parsed[0].target)
    assert parsed[0].status_label == "Confirmed", parsed[0].status_label
    assert parsed[1].target == "", repr(parsed[1].target)
    assert parsed[2].target == "Regel een\nregel twee", repr(parsed[2].target)
    # Locked status line had the 🔒 prefix stripped.
    assert parsed[2].status_label == "Draft", parsed[2].status_label
    print("parse OK")

    # New single-line encoding: the in-target break is written as [newline] and
    # the target sits on ONE physical line in the exported text.
    assert "Regel een[newline]regel twee" in md, md
    assert "Regel een\nregel twee" not in md, "target must be single-line on export"
    # Backward compatibility: a genuinely multi-line target (old-style file)
    # still parses, with the break preserved as a real newline.
    legacy = ("[SEGMENT 0001]\n"
              "EN: Hello world\n"
              "NL: Regel een\nregel twee\n"
              "Status: Draft\nComment:\n")
    lp = parse_markdown(legacy)
    assert lp[0].target == "Regel een\nregel twee", repr(lp[0].target)
    # Source breaks are now ALSO shown as [newline] (read-only reference); the
    # sidecar hash is computed over that same encoded form.
    assert "EN: Line one[newline]line two" in md, md          # EN is the source here
    assert parsed[2].source == "Line one[newline]line two", repr(parsed[2].source)
    _sc = build_sidecar(segs, project_name="D", source_file_name="d",
                        source_language="English", target_language="Dutch",
                        tool_version="x", export_file_path="d",
                        timestamp_utc="2026-06-08T00:00:00Z")
    assert hash_prefix(parsed[2].source) == _sc["segments"][2]["source_hash"], \
        "source hash must cover the [newline]-encoded source"
    print("newline-token + backward-compat OK")

    # Sidecar + diffing.
    sidecar = build_sidecar(
        segs, project_name="Demo", source_file_name="demo.docx",
        source_language="English", target_language="Dutch",
        tool_version="1.10.231", export_file_path="demo.md",
        timestamp_utc="2026-05-31T00:00:00Z",
    )
    label_to_key = {
        "not started": "not_started", "draft": "draft", "confirmed": "confirmed",
        "approved": "approved", "rejected": "rejected",
    }
    # Simulate the user editing segment 2's target and dropping a structural tag in 1.
    edited = parse_markdown(md.replace("NL: ", "NL: Hallo wereld", 1)  # not used; build manually below
                            ) if False else None

    current = [
        CurrentSeg(101, 1, "The <b>quick</b> fox {1}", "De <b>snelle</b> vos {1}", "confirmed"),
        CurrentSeg(102, 2, "Hello world", "", "not_started"),
        CurrentSeg(103, 3, "Line one line two", "Regel een\nregel twee", "draft", locked=True),
    ]
    # Edit: fill segment 2's target.
    parsed[1].target = "Hallo wereld"
    diffs = build_import_diffs(parsed, sidecar, current,
                               status_label_to_key=label_to_key)
    kinds = {d.number: d.kind for d in diffs}
    assert kinds[1] == KIND_UNCHANGED, kinds
    assert kinds[2] == KIND_CHANGED, kinds
    assert diffs[1].new_target == "Hallo wereld"
    assert diffs[1].new_status_key == "draft", diffs[1].new_status_key
    print("diff OK")

    # Tag mismatch: drop the {1} structural tag from segment 1's target.
    parsed[0].target = "De snelle vos"   # {1} removed
    diffs2 = build_import_diffs(parsed, sidecar, current,
                                status_label_to_key=label_to_key, strict_tags=True)
    assert diffs2[0].kind == KIND_TAG_MISMATCH, diffs2[0]
    diffs3 = build_import_diffs(parsed, sidecar, current,
                                status_label_to_key=label_to_key, strict_tags=False)
    assert diffs3[0].kind == KIND_CHANGED, diffs3[0]
    print("tag-integrity OK")

    # Comment awareness: export a comment, round-trip it, and detect a
    # comment-only edit.
    csegs = [
        MdExportSegment(1, 201, "Source one", "Target one",
                        status_key="draft", status_label="Draft",
                        comment="Check this term"),
        MdExportSegment(2, 202, "Source two", "Target two",
                        status_key="draft", status_label="Draft",
                        comment="Line A\nLine B"),
    ]
    cmd = build_markdown(csegs, project_name="C", source_file_name="c.docx",
                         source_lang_display="English", target_lang_display="Dutch",
                         tool_version="1.10.231")
    assert "Comment: Check this term" in cmd, cmd
    cparsed = parse_markdown(cmd)
    assert cparsed[0].comment == "Check this term", repr(cparsed[0].comment)
    assert cparsed[1].comment == "Line A\nLine B", repr(cparsed[1].comment)
    csidecar = build_sidecar(csegs, project_name="C", source_file_name="c.docx",
                             source_language="English", target_language="Dutch",
                             tool_version="1.10.231", export_file_path="c.md",
                             timestamp_utc="2026-05-31T00:00:00Z")
    ccur = [
        CurrentSeg(201, 1, "Source one", "Target one", "draft", comment="Check this term"),
        CurrentSeg(202, 2, "Source two", "Target two", "draft", comment="Line A\nLine B"),
    ]
    # Edit ONLY the comment of segment 1 (target untouched).
    cparsed[0].comment = "Checked — fine"
    cdiffs = build_import_diffs(cparsed, csidecar, ccur, status_label_to_key=label_to_key)
    cby = {d.number: d for d in cdiffs}
    assert cby[1].kind == KIND_CHANGED, cby[1]
    assert cby[1].new_comment == "Checked — fine", cby[1].new_comment
    assert cby[1].new_target is None, "target must not change on a comment-only edit"
    assert cby[1].new_status_key is None, "status must not change on a comment-only edit"
    assert cby[2].kind == KIND_UNCHANGED, cby[2]
    print("comment round-trip OK")

    print("\nAll self-tests passed.")
