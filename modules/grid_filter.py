"""
Grid Filter Matching
====================

Interprets the text typed into the grid's Source / Target filter boxes.

Plain text filters as it always has: a case-insensitive substring match. Text
written between slashes, such as ``/pump\\s+hous/``, is a case-insensitive
regular expression instead (one of the requests in issue #208). A slash
syntax rather than a checkbox because the filter boxes are shared widgets that
the grid re-parents between layouts, so the pattern has to carry its own mode.

An invalid pattern is matched as plain text instead (slashes included, so it
usually matches nothing) and ``error`` says why, for the caller to report.
Until the closing slash is typed the text is plain text too, so the mode never
changes behind the user's back.
"""

import re
from typing import List, Optional, Tuple


class GridFilter:
    """A compiled filter-box value: ``matches(text)`` and ``spans(text)``."""

    def __init__(self, text: str):
        self.text = text or ""
        self.is_regex = False
        self.error: Optional[str] = None
        self._regex = None
        body = self.text
        if len(body) >= 3 and body.startswith("/") and body.endswith("/"):
            try:
                self._regex = re.compile(body[1:-1], re.IGNORECASE)
                self.is_regex = True
            except re.error as e:
                self.error = str(e)
        self._needle = self.text.lower()

    def __bool__(self) -> bool:
        return bool(self.text)

    def matches(self, text: str) -> bool:
        """True if ``text`` passes this filter (an empty filter passes all)."""
        if not self.text:
            return True
        if self._regex is not None:
            return self._regex.search(text or "") is not None
        return self._needle in (text or "").lower()

    def spans(self, text: str) -> List[Tuple[int, int]]:
        """``(start, end)`` of each match in ``text``, for highlighting.
        Zero-length regex matches (e.g. ``/^/``) are skipped: nothing to paint."""
        if not self.text or not text:
            return []
        if self._regex is not None:
            return [m.span() for m in self._regex.finditer(text) if m.end() > m.start()]
        spans = []
        haystack = text.lower()
        pos = haystack.find(self._needle)
        while pos != -1:
            spans.append((pos, pos + len(self._needle)))
            pos = haystack.find(self._needle, pos + len(self._needle))
        return spans
