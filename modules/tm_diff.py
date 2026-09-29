"""
TM match differences
====================

The word-level comparison behind the Match Panel's "TM Source" box: the TM
source annotated with what the current segment changed (deleted words struck
through, added words underlined).

Inline tags (``<1>``, ``</b>``, ``{1}`` …) are compared as tokens of their own
(issue #117), so a tag glued to a word – ``<b>tap</b>`` → ``<b>valve</b>`` –
marks only the word as changed instead of the whole run including the tags.
Whitespace is kept as tokens too, so the original spacing is reproduced exactly
rather than rebuilt with a space between every word.
"""

import difflib
import re
from typing import List, Tuple

_TOKEN = re.compile(
    r"\n"                          # line break
    r"|[^\S\n]+"                   # other whitespace
    r"|</?[A-Za-z0-9_:.\-]+(?:\s[^<>\n]*?)?/?>"  # XML/HTML-style and numbered tags: <1>, </1>, <2/>, <b>, <x id="1"/>
    r"|\{\d+[>}]?|<?\d+\}"         # Phrase-style {1}, {1>, <1}
    r"|[^\s<{]+"                   # a run of ordinary characters
    r"|[<{]"                       # a stray bracket that starts no tag
)


def tokenize(text: str) -> List[str]:
    return _TOKEN.findall(text or "")


def word_diff(current: str, tm_source: str) -> List[Tuple[str, str]]:
    """``[(kind, text)]`` with kind ``normal`` / ``delete`` (in the TM, not in
    the current source) / ``add`` (in the current source, not in the TM)."""
    cur, tm = tokenize(current), tokenize(tm_source)
    matcher = difflib.SequenceMatcher(None, cur, tm, autojunk=False)
    out: List[Tuple[str, str]] = []

    def emit(kind, tokens):
        if not tokens:
            return
        text = "".join(tokens)
        if out and out[-1][0] == kind:
            out[-1] = (kind, out[-1][1] + text)
        else:
            out.append((kind, text))

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            emit("normal", tm[j1:j2])
        elif tag == "replace":
            emit("delete", tm[j1:j2])
            emit("add", cur[i1:i2])
        elif tag == "insert":      # only in the TM
            emit("delete", tm[j1:j2])
        elif tag == "delete":      # only in the current source
            emit("add", cur[i1:i2])
    return out
