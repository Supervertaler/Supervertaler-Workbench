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
    """Tokens of ``text``; the user's own inline codes (Settings → Inline
    Codes, issue #194) are single tokens too, so ``{PKMN}`` against
    ``{PK}{MN}`` shows as one code replaced by two."""
    text = text or ""
    try:
        from modules import inline_codes
        codes = inline_codes.find_codes(text)
    except Exception:
        codes = []
    tokens: List[str] = []
    pos = 0
    for start, end, code in codes:
        tokens += _TOKEN.findall(text[pos:start])
        tokens.append(code)
        pos = end
    tokens += _TOKEN.findall(text[pos:])
    return tokens


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


def char_diff(new: str, old: str) -> List[Tuple[str, str]]:
    """Character-level ``[(kind, text)]`` for comparing two translations, e.g.
    an MT suggestion (``new``) with the TM target (``old``) – the "at a glance"
    comparison asked for in issue #208. Kinds as in :func:`word_diff`:
    ``normal`` / ``add`` (only in ``new``) / ``delete`` (only in ``old``).

    A raw character diff of two sentences is confetti, so tiny common runs
    (one or two characters, or plain whitespace) sandwiched between changes are
    folded into the change: "pompen" vs "pompje" reads as one changed ending,
    not three separate letters."""
    new, old = new or "", old or ""
    matcher = difflib.SequenceMatcher(None, new, old, autojunk=False)
    ops = [(tag, new[i1:i2], old[j1:j2]) for tag, i1, i2, j1, j2 in matcher.get_opcodes()]

    # Fold short equal runs between two changes into the change
    folded: List[List[str]] = []          # [tag, new_text, old_text]
    for idx, (tag, a, b) in enumerate(ops):
        between = 0 < idx < len(ops) - 1
        if (tag == "equal" and between and (len(a) <= 2 or not a.strip())
                and ops[idx - 1][0] != "equal" and ops[idx + 1][0] != "equal"):
            tag = "replace"
        if folded and tag != "equal" and folded[-1][0] != "equal":
            folded[-1][1] += a
            folded[-1][2] += b
        else:
            folded.append([tag, a, b])

    out: List[Tuple[str, str]] = []

    def emit(kind, text):
        if not text:
            return
        if out and out[-1][0] == kind:
            out[-1] = (kind, out[-1][1] + text)
        else:
            out.append((kind, text))

    for tag, a, b in folded:
        if tag == "equal":
            emit("normal", a)
        else:
            emit("delete", b)
            emit("add", a)
    return out


def diff_spans(parts: List[Tuple[str, str]], side: str) -> List[Tuple[int, int]]:
    """``(start, end)`` of the changed characters on one side of a diff:
    ``side="new"`` gives the ``add`` runs as offsets into the new text,
    ``side="old"`` the ``delete`` runs as offsets into the old text."""
    changed = "add" if side == "new" else "delete"
    spans, pos = [], 0
    for kind, text in parts:
        if kind == "normal":
            pos += len(text)
        elif kind == changed:
            spans.append((pos, pos + len(text)))
            pos += len(text)
    return spans
