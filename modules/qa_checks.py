"""
QA Checks
=========

Saved find-only checks – a regex "linter" for translations (issue #209).

A QA check is an ordinary Find & Replace operation with ``check_only`` set, so
checks live in F&R Sets and are shared the same way (export/import ``.svfr``).
Running them never changes anything: every match becomes a finding that the QA
dialog lists, with the segment, the check, the side and the matched text.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, List, Tuple

from modules.find_replace_qt import FindReplaceOperation, FindReplaceSet

BASIC_SET_NAME = "QA - basic checks"


@dataclass
class Finding:
    segment_index: int   # position in project.segments
    segment_id: int
    check: str           # the check's note, or its pattern
    side: str            # "source" or "target"
    text: str            # what matched
    start: int
    end: int
    context: str         # the text around the match, the match in [brackets]


def make_matcher(op: FindReplaceOperation) -> Callable[[str], Iterator[Tuple[int, int]]]:
    """``text -> (start, end) spans`` for an operation's find settings.
    Raises ``re.error`` for an invalid regular expression."""
    flags = 0 if op.case_sensitive else re.IGNORECASE
    if getattr(op, 'use_regex', False):
        pattern = re.compile(op.find_text, flags)
    elif op.match_mode == 2:  # entire segment
        def whole(text):
            same = text == op.find_text if op.case_sensitive else text.lower() == op.find_text.lower()
            if same and text:
                yield (0, len(text))
        return whole
    elif op.match_mode == 1:  # whole words
        pattern = re.compile(r'\b' + re.escape(op.find_text) + r'\b', flags)
    else:
        pattern = re.compile(re.escape(op.find_text), flags)

    def spans(text):
        for m in pattern.finditer(text):
            if m.end() > m.start():  # zero-width matches ("^", "a*") aren't findings
                yield (m.start(), m.end())
    return spans


def _context(text: str, start: int, end: int, width: int = 30) -> str:
    left = text[max(0, start - width):start]
    right = text[end:end + width]
    shown = text[start:end].replace(' ', '·')  # make stray spaces visible
    return (("…" if start > width else "") + left + "[" + shown + "]" + right
            + ("…" if end + width < len(text) else ""))


def checks_in(fr_set: FindReplaceSet) -> List[FindReplaceOperation]:
    """The enabled QA checks of a set."""
    return [op for op in fr_set.operations
            if getattr(op, 'check_only', False) and op.enabled and op.find_text]


def run_checks(segments, checks: Iterable[FindReplaceOperation]):
    """Run ``checks`` over ``segments`` (objects with id/source/target).

    Returns ``(findings, problems)`` – problems are human-readable messages for
    checks that could not run (an invalid regular expression).
    """
    findings: List[Finding] = []
    problems: List[str] = []
    compiled = []
    for op in checks:
        try:
            compiled.append((op, make_matcher(op)))
        except re.error as e:
            problems.append(f"“{op.note or op.find_text}” is not a valid regular expression: {e}")
    sides = {"source": ("source",), "target": ("target",), "both": ("source", "target")}
    for index, seg in enumerate(segments):
        for op, spans in compiled:
            for side in sides.get(op.search_in, ("target",)):
                text = getattr(seg, side, "") or ""
                if not text:
                    continue
                for start, end in spans(text):
                    findings.append(Finding(index, getattr(seg, 'id', index + 1),
                                            op.note or op.find_text, side, text[start:end],
                                            start, end, _context(text, start, end)))
    return findings, problems


def basic_qa_set() -> FindReplaceSet:
    """A starter set of common checks, all on the target side."""
    def check(pattern, note, enabled=True):
        return FindReplaceOperation(find_text=pattern, search_in="target", use_regex=True,
                                    check_only=True, enabled=enabled, note=note)
    return FindReplaceSet(name=BASIC_SET_NAME, operations=[
        check(r"(?<=\S) {2,}(?=\S)", "Double space"),
        check(r"^\s+|\s+$", "Space at the start or end", enabled=False),
        check(r" +[.,](?=\s|$)", "Space before a full stop or comma"),
        check(r"\b(\w+)\s+\1\b", "Doubled word"),
        check(r"[.,;:!?]{2,}", "Repeated punctuation (also catches “...”)", enabled=False),
        check(r"\(\s|\s\)", "Space inside brackets"),
        check(r"[\"']", "Straight quote (where curly quotes are wanted)", enabled=False),
    ])


def _file_for(sets_dir: Path, name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in name)
    return Path(sets_dir) / f"{safe}.svfr"


def load_sets(sets_dir) -> List[FindReplaceSet]:
    out = []
    for path in sorted(Path(sets_dir).glob("*.svfr")):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                out.append(FindReplaceSet.from_dict(json.load(f)))
        except Exception:
            continue
    return out


def save_set(sets_dir, fr_set: FindReplaceSet) -> Path:
    """Write a set the way the F&R Sets manager does (same file naming)."""
    Path(sets_dir).mkdir(parents=True, exist_ok=True)
    path = _file_for(sets_dir, fr_set.name)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(fr_set.to_dict(), f, ensure_ascii=False, indent=2)
    return path
