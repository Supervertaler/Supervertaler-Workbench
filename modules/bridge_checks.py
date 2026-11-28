"""
Read-only checks over a whole project, for the Supervertaler Bridge
===================================================================

The checks behind the bridge tools check_tags, check_numbers, check_nbsp and
find_inconsistencies (modules/bridge_tools.py). They find; they change
nothing.

Every function takes a list of ``Row`` – a plain copy of what a check needs
from each segment, taken on the GUI thread – and no window, no Qt, no
database. So they run on the bridge's own thread and a large project cannot
freeze Workbench while it is checked.

Scale: each check is one pass over the rows; find_inconsistencies keeps one
dictionary entry per distinct source. Tested at 50,000 segments.

The results follow the Trados tools of the same names, so an assistant's
review routine works in both products:

* numbers compare as digits only, so 1.234,56 matches 1,234.56;
* a non-breaking space is U+00A0 alone – the narrow one is another character,
  and folding them together would hide the substitution this check is for;
* inconsistencies compare text with tags removed and spacing collapsed.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, NamedTuple

from modules import qa_checks
from modules.tag_text import extract_all_tags, strip_all_tags

DEFAULT_ISSUES = 50
MAX_ISSUES = 500
EXCERPT = 160  # characters of source and target shown per issue

NBSP = " "

# A number with its decimal and thousands separators: full stop, comma, space,
# non-breaking space, narrow non-breaking space. Mirrors ExtractNumbers in the
# Trados plugin (AiAssistantViewPart.cs).
_NUMBER = re.compile(r"\d+(?:[.,   ]\d+)*")
_SEPARATORS = re.compile(r"[.,   ]")
_SPACES = re.compile(r"\s+")


class Row(NamedTuple):
    id: int
    source: str
    target: str
    status: str
    file: str


def _excerpt(text: str) -> str:
    return text if len(text) <= EXCERPT else text[:EXCERPT] + "…"


def _issue(row: Row, detail: str) -> Dict[str, Any]:
    issue: Dict[str, Any] = {
        "id": row.id,
        "status": row.status,
        "detail": detail,
        "source": _excerpt(row.source),
        "target": _excerpt(row.target),
    }
    if row.file:
        issue["file"] = row.file
    return issue


def numbers_in(text: str) -> List[str]:
    """The numbers in a text as digit strings. Tags are removed first: the 1 in
    <t1> is not a number in the sentence."""
    return [_SEPARATORS.sub("", m.group(0)) for m in _NUMBER.finditer(strip_all_tags(text or ""))]


def _number_detail(row: Row):
    source, target = numbers_in(row.source), numbers_in(row.target)
    if sorted(source) != sorted(target):
        return f"source numbers [{', '.join(source)}] vs target [{', '.join(target)}]"
    return None


def _nbsp_detail(row: Row):
    source, target = row.source.count(NBSP), row.target.count(NBSP)
    if target < source:
        return f"source has {source} non-breaking space(s), target has {target}"
    return None


def _tag_details(rows: List[Row]) -> Dict[int, str]:
    """{position in rows: what differs}, from Workbench's own tag check, so
    this and Tools > QA Checks can never disagree."""
    lost: Dict[int, Counter] = {}
    gained: Dict[int, Counter] = {}
    for finding in qa_checks.tag_findings(rows, extract_all_tags):
        side = lost if finding.check == qa_checks.TAG_CHECK_MISSING else gained
        side.setdefault(finding.segment_index, Counter())[finding.text] += 1

    def listed(counter: Counter) -> str:
        return ", ".join(tag if n == 1 else f"{tag} ×{n}" for tag, n in counter.items())

    details = {}
    for index in sorted(set(lost) | set(gained)):
        parts = []
        if index in lost:
            parts.append("missing from the target: " + listed(lost[index]))
        if index in gained:
            parts.append("in the target but not in the source: " + listed(gained[index]))
        details[index] = "; ".join(parts)
    return details


_NOTES = {
    "numbers": "All numbers in translated segments match between source and target.",
    "tags": "The tags in every translated segment match its source.",
    "nbsp": "No translated segment has fewer non-breaking spaces than its source.",
}


def qa_check(rows: List[Row], check: str, limit: int) -> Dict[str, Any]:
    """Run one check over the translated rows. ``check`` is numbers, tags or nbsp."""
    translated = [(index, row) for index, row in enumerate(rows) if row.target.strip()]

    if check == "tags":
        details = _tag_details(rows)
        found = [(row, details[index]) for index, row in translated if index in details]
    else:
        detail_of = _number_detail if check == "numbers" else _nbsp_detail
        found = [(row, detail) for row, detail in ((row, detail_of(row)) for _, row in translated) if detail]

    result: Dict[str, Any] = {
        "ok": True,
        "check": check,
        "segmentsChecked": len(translated),
        "issuesFound": len(found),
        "truncated": len(found) > limit,
        "issues": [_issue(row, detail) for row, detail in found[:limit]],
    }
    if not found:
        result["note"] = (_NOTES[check] if translated
                          else "No segment has a translation yet, so there was nothing to check.")
    return result


def _plain(text: str) -> str:
    return _SPACES.sub(" ", strip_all_tags(text or "")).strip()


def inconsistencies(rows: List[Row], limit: int, offset: int) -> Dict[str, Any]:
    """Repeated sources (tags and spacing aside) whose translations differ."""
    groups: Dict[str, List[Row]] = {}
    for row in rows:
        if not row.target.strip():
            continue
        key = _plain(row.source)
        if key:
            groups.setdefault(key, []).append(row)

    inconsistent = [(key, members) for key, members in groups.items()
                    if len({_plain(m.target) for m in members}) > 1]
    page = inconsistent[offset:offset + limit]
    truncated = offset + len(page) < len(inconsistent)

    result: Dict[str, Any] = {
        "ok": True,
        "groupsFound": len(inconsistent),
        "offset": offset,
        "returned": len(page),
        "truncated": truncated,
        "groups": [{
            "source": _excerpt(key),
            "translations": len({_plain(m.target) for m in members}),
            "occurrences": [
                {k: v for k, v in (("id", m.id), ("target", _excerpt(m.target)),
                                   ("status", m.status), ("file", m.file)) if v != ""}
                for m in members
            ],
        } for key, members in page],
    }
    if truncated:
        result["note"] = f"More groups follow: call again with offset={offset + len(page)}."
    elif not inconsistent:
        result["note"] = "No repeated source segment has been translated in more than one way."
    return result
