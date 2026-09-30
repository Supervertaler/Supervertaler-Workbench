"""
Fragment matches from the TM (issue #193)
=========================================

A fuzzy match compares whole segments, so it finds nothing when the TM and
the document are cut up differently:

- the TM holds ``Which heading do you want to read?``, but the document has
  it as two segments, ``Which heading`` and ``do you want to read?``;
- or the other way round: the TM holds ``Close the valve.`` and the segment
  is ``Close the valve. Then open the tap.``

A *fragment match* is a TM entry whose source contains the whole segment
(``in_tm``), or whose whole source occurs in the segment (``in_segment``),
word for word. The Match Panel shows it with the TM's translation of the whole
entry, marked as a fragment; the translator takes the part they need.

Words are compared case-insensitively, ignoring punctuation and inline tags,
and must form an unbroken run. A fragment has at least two words – single
words are what glossaries are for.
"""

import re
from typing import Dict, Iterable, List, Optional, Sequence

MIN_WORDS = 2
IN_TM = "in_tm"            # the segment is part of the TM entry's source
IN_SEGMENT = "in_segment"  # the TM entry's source is part of the segment

_TAG = re.compile(r"<[^>]+>")
_WORD = re.compile(r"\w+", re.UNICODE)


def words(text: str) -> List[str]:
    """The words of ``text``, lower-cased, without tags and punctuation."""
    return [w.casefold() for w in _WORD.findall(_TAG.sub(" ", text or ""))]


def find_run(needle: Sequence[str], haystack: Sequence[str]) -> int:
    """Index where ``needle`` occurs as an unbroken run in ``haystack``, or -1."""
    n = len(needle)
    if not n or n > len(haystack):
        return -1
    first = needle[0]
    for i in range(len(haystack) - n + 1):
        if haystack[i] == first and list(haystack[i:i + n]) == list(needle):
            return i
    return -1


def classify(segment_words: Sequence[str], tm_words: Sequence[str]) -> Optional[str]:
    """``IN_TM``, ``IN_SEGMENT`` or None (not a fragment match)."""
    if MIN_WORDS <= len(segment_words) < len(tm_words):
        return IN_TM if find_run(segment_words, tm_words) >= 0 else None
    if MIN_WORDS <= len(tm_words) < len(segment_words):
        return IN_SEGMENT if find_run(tm_words, segment_words) >= 0 else None
    return None


def pick(segment: str, candidates: Iterable[Dict], max_results: int = 3,
         exclude_sources: Iterable[str] = ()) -> List[Dict]:
    """The fragment matches among ``candidates`` (TM rows with ``source_text``),
    best first.

    Each result is a copy of its row with ``fragment`` (``IN_TM``/``IN_SEGMENT``),
    and ``similarity``/``match_pct`` set to the share of the longer text that
    the shorter one covers, in words – so the closest fit comes first and the
    percentage says how much of the TM entry (or segment) is involved.
    """
    seg_words = words(segment)
    if len(seg_words) < MIN_WORDS:
        return []
    seen = set(exclude_sources)
    results = []
    for row in candidates:
        source = row.get("source_text") or ""
        if source in seen:
            continue
        tm_words = words(source)
        kind = classify(seg_words, tm_words)
        if not kind:
            continue
        seen.add(source)
        coverage = min(len(seg_words), len(tm_words)) / max(len(seg_words), len(tm_words))
        match = dict(row)
        match.update(fragment=kind, similarity=coverage, match_pct=int(round(coverage * 100)))
        results.append(match)
    results.sort(key=lambda m: m["similarity"], reverse=True)
    return results[:max_results]


# The chip shown next to a fragment match's percentage (solid, so it reads on
# light and dark themes alike), and its tooltip (explicit colours: Windows 11
# dark mode ignores tooltip stylesheets)
CHIP_HTML = ("<span style='font-size:8px; color:#ffffff; background-color:#0e7490; "
             "padding:1px 5px; border-radius:6px;'>&nbsp;✂ fragment&nbsp;</span>")


def tooltip_html(kind: str) -> str:
    return ("<div style='background-color:#1f2937; color:#f9fafb; padding:6px 8px; "
            "max-width:360px;'>" + describe(kind) + "</div>")


def describe(kind: str) -> str:
    """One line for a tooltip, saying what the fragment match is."""
    if kind == IN_TM:
        return ("This segment is part of a longer sentence in the TM. The TM's whole "
                "sentence and its translation are shown: use the part you need.")
    return ("This TM sentence is part of the current segment. Its translation "
            "covers only that part of the segment.")
