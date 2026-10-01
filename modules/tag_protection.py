"""
Tag protection (issue #113)
===========================

Inline tags in the target cell – ``<b>``, ``</1>``, ``[2}``, ``{3}``, Déjà Vu
``{00108}`` and the user's own inline codes (Settings → Inline Codes) – are
treated as single units, the way memoQ and Trados handle them, so a slip of
the keyboard cannot leave half a tag behind:

- the cursor never rests inside a tag (it steps over it);
- Backspace just after a tag, or Delete just before it, removes the whole tag;
- typing, pasting or cutting over a selection that cuts through a tag takes
  the whole tag with it.

``TAG_RE`` is also the pattern the grid highlighter colours, so what is
protected is exactly what is shown as a tag.
"""

import re
from typing import List, Optional, Tuple

try:
    from modules import inline_codes
except ImportError:  # run as a script from modules/
    import inline_codes

SETTINGS_KEY = "protect_tags_in_target"

WORD_JOINER = "⁠"   # display-only joiner Supervertaler puts inside tags to stop them wrapping
_wj = WORD_JOINER + "?"

# Every inline tag form Supervertaler shows as a tag. Opening memoQ content
# tags ``[tag …]`` must have attributes, so plain ``[Company]`` placeholders
# are not tags.
TAG_PATTERNS = [
    r"</?" + _wj + r"[a-zA-Z][a-zA-Z0-9-]*/?" + _wj + r"(?:\s[^>]*)?>",  # HTML/XML
    r"</?" + _wj + r"\d+>",                        # Trados numeric: <1>, </1>
    r"\{/?" + _wj + r"\d+/?" + _wj + r"\}",        # compact placeholders: {1}, {/1}, {1/}
    r"\[\d+[}\]]",                                 # memoQ numeric: [1}, [1]
    r"\{\d+[}\]]",                                 # memoQ numeric: {1}, {1]
    r"\[[^}\]]+\}",                                # memoQ mixed: [anything}
    r"\{[^\[\]]+\]",                               # memoQ mixed: {anything]
    r"\[[a-zA-Z][^}\]]*\s[^}\]]*\]",               # memoQ content: [tag attr…]
    r"\{[a-zA-Z][a-zA-Z0-9_-]*\}",                 # memoQ closing: {uicontrol}
    r"\{\d{5}\}",                                  # Déjà Vu: {00108}
]
TAG_RE = re.compile("|".join(TAG_PATTERNS))

Span = Tuple[int, int]


def tag_spans(text: str) -> List[Span]:
    """Non-overlapping ``(start, end)`` of every tag and inline code, in order."""
    spans = [(m.start(), m.end()) for m in TAG_RE.finditer(text or "") if m.end() > m.start()]
    for start, end, _ in inline_codes.find_codes(text or ""):
        if not any(s < end and start < e for s, e in spans):
            spans.append((start, end))
    return sorted(spans)


def inside(spans: List[Span], pos: int) -> Optional[Span]:
    """The tag ``pos`` falls strictly inside, if any."""
    for s, e in spans:
        if s < pos < e:
            return (s, e)
    return None


def snap(spans: List[Span], pos: int, forward: bool) -> int:
    """``pos`` moved out of any tag – to its end when moving forward, else its start."""
    span = inside(spans, pos)
    if span is None:
        return pos
    return span[1] if forward else span[0]


def backspace_span(spans: List[Span], pos: int) -> Optional[Span]:
    """The whole tag Backspace at ``pos`` should remove (one ending at, or around, ``pos``)."""
    for s, e in spans:
        if e == pos or s < pos < e:
            return (s, e)
    return None


def delete_span(spans: List[Span], pos: int) -> Optional[Span]:
    """The whole tag Delete at ``pos`` should remove (one starting at, or around, ``pos``)."""
    for s, e in spans:
        if s == pos or s < pos < e:
            return (s, e)
    return None


def expand(spans: List[Span], start: int, end: int) -> Span:
    """Widen a selection so it covers every tag it touches whole."""
    if start > end:
        start, end = end, start
    for s, e in spans:
        if s < start < e:
            start = s
        if s < end < e:
            end = e
    return start, end
