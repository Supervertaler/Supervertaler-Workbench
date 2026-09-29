"""
Inline codes (issue #194)
=========================

User-defined patterns for placeholders and codes in plain-text and game files –
``{playerName}``, ``%s``, ``%1$d``, ``\\n``, ``<color=#ff0000>`` and so on –
which no upstream CAT tool has tagged. Once defined in Settings → Inline Codes,
anything matching is treated like an inline tag:

- highlighted in the grid like tags;
- offered by "Insert next tag" (Ctrl+,) until the target has them all;
- checked by QA → Run QA Checks (missing from / not in the source);
- listed in the AI translation prompt as codes to keep exactly as written;
- carried over from the current source when a TM match differs only in its
  codes (``adapt_codes``).

The active patterns live in this module (``set_active``), so the grid
highlighter and the tag helpers in the main window read the same set.
"""

import re
from collections import Counter
from typing import Dict, List, Optional, Pattern, Tuple

SETTINGS_KEY = "custom_inline_codes"

# (label, pattern, example) – offered on the settings page as one-click additions.
PRESETS: List[Tuple[str, str, str]] = [
    ("{placeholder}", r"\{[^{}\s]+\}", "Hello {playerName}!"),
    ("%s, %d, %1$s (printf)", r"%(?:\d+\$)?[-+ 0#]*\d*(?:\.\d+)?[sdifuxXeEgGc@]", "%d items for %s"),
    ("%NAME%", r"%[A-Za-z_][A-Za-z0-9_]*%", "Welcome, %USER%"),
    ("\\n \\t (escaped line breaks)", r"\\[nrt]", "Line one\\nLine two"),
    ("<color=…> game tags", r"</?[A-Za-z]+=[^<>]*>|</[A-Za-z]+>", "<color=#ff0000>Red</color>"),
    ("$VARIABLE$", r"\$[A-Za-z_][A-Za-z0-9_]*\$", "Costs $PRICE$"),
    ("${var} and {{var}}", r"\$\{[^{}]+\}|\{\{[^{}]+\}\}", "Hi {{name}}, ${count} new"),
    ("[[double brackets]]", r"\[\[[^\[\]]+\]\]", "Press [[KEY_JUMP]]"),
]

_active: Optional[Pattern] = None


def normalise(entries) -> List[Dict]:
    """Stored entries → ``[{'pattern', 'enabled', 'comment'}]`` (blank
    patterns dropped)."""
    out = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        pattern = str(entry.get("pattern") or "")
        if not pattern.strip():
            continue
        out.append({"pattern": pattern, "enabled": bool(entry.get("enabled", True)),
                    "comment": str(entry.get("comment") or "")})
    return out


def pattern_error(pattern: str) -> Optional[str]:
    if not (pattern or "").strip():
        return "Enter a pattern."
    try:
        compiled = re.compile(pattern)
    except re.error as e:
        return e.msg
    if compiled.match(""):
        return "The pattern also matches empty text."
    return None


def compile_codes(entries) -> Optional[Pattern]:
    """One pattern for all enabled, valid entries (earlier entries win where
    two could match at the same place), or None."""
    parts = [f"(?:{e['pattern']})" for e in normalise(entries)
             if e["enabled"] and not pattern_error(e["pattern"])]
    return re.compile("|".join(parts)) if parts else None


def set_active(entries) -> Optional[Pattern]:
    global _active
    _active = compile_codes(entries)
    return _active


def active_pattern() -> Optional[Pattern]:
    return _active


def find_codes(text: str, pattern: Optional[Pattern] = None) -> List[Tuple[int, int, str]]:
    """``[(start, end, code)]`` in order of appearance."""
    pattern = _active if pattern is None else pattern
    if pattern is None or not text:
        return []
    return [(m.start(), m.end(), m.group(0)) for m in pattern.finditer(text) if m.end() > m.start()]


def codes_in(text: str, pattern: Optional[Pattern] = None) -> List[str]:
    return [code for _, _, code in find_codes(text, pattern)]


def mismatches(source: str, target: str, pattern: Optional[Pattern] = None) -> Tuple[List[str], List[str]]:
    """``(missing from the target, not in the source)`` – each code listed
    as often as it is short or surplus."""
    src, tgt = Counter(codes_in(source, pattern)), Counter(codes_in(target, pattern))
    missing = list((src - tgt).elements())
    extra = list((tgt - src).elements())
    return missing, extra


def prompt_note(sources: List[str], pattern: Optional[Pattern] = None, limit: int = 40) -> str:
    """An instruction for the AI listing the codes in these source texts,
    or "" when there are none."""
    seen: List[str] = []
    for text in sources:
        for code in codes_in(text, pattern):
            if code not in seen:
                seen.append(code)
    if not seen:
        return ""
    listed = ", ".join(seen[:limit]) + (", …" if len(seen) > limit else "")
    return ("Inline codes: the source contains placeholders/codes that must appear in the "
            "translation exactly as written – never translate, respell, split, merge or drop "
            f"them, though they may move to fit the word order: {listed}")


def _runs(text: str, pattern: Optional[Pattern]) -> List[Tuple[int, int]]:
    """Spans of adjacent codes – ``{PK}{MN}`` is one run of two codes."""
    runs: List[Tuple[int, int]] = []
    for start, end, _ in find_codes(text, pattern):
        if runs and runs[-1][1] == start:
            runs[-1] = (runs[-1][0], end)
        else:
            runs.append((start, end))
    return runs


def _skeleton(text: str, runs: List[Tuple[int, int]]) -> str:
    out, pos = [], 0
    for start, end in runs:
        out.append(text[pos:start])
        out.append("\x00")
        pos = end
    out.append(text[pos:])
    return "".join(out)


def adapt_codes(tm_source: str, tm_target: str, current_source: str,
                pattern: Optional[Pattern] = None) -> Optional[str]:
    """When a TM entry differs from the current source only in its codes
    (``{PK}{MN} can't be the same.`` against ``{PKMN} can't be the same.``),
    return the TM target with the current source's codes put in; otherwise
    None. Runs of adjacent codes are matched in order, so a run may change
    how many codes it has."""
    pattern = _active if pattern is None else pattern
    if pattern is None or not tm_target:
        return None
    tm_runs, cur_runs = _runs(tm_source, pattern), _runs(current_source, pattern)
    if not tm_runs or len(tm_runs) != len(cur_runs):
        return None
    if _skeleton(tm_source, tm_runs) != _skeleton(current_source, cur_runs):
        return None
    mapping: Dict[str, str] = {}
    for (ts, te), (cs, ce) in zip(tm_runs, cur_runs):
        old, new = tm_source[ts:te], current_source[cs:ce]
        if mapping.get(old, new) != new:
            return None          # the same TM code maps to two different ones
        mapping[old] = new
    if all(old == new for old, new in mapping.items()):
        return None
    target_runs = _runs(tm_target, pattern)
    out, pos = [], 0
    for start, end in target_runs:
        run = tm_target[start:end]
        if run not in mapping:
            return None          # a code in the target we cannot account for
        out.append(tm_target[pos:start])
        out.append(mapping[run])
        pos = end
    out.append(tm_target[pos:])
    return "".join(out)
