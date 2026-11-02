"""Pseudo-translation: fill targets with deliberately stress-tested placeholder
text so a project's files can be exported and visually checked for layout,
encoding, font-coverage and tag round-trip problems BEFORE real translation
starts.

This is the pure, UI-free core (so it can be unit-tested). ``Supervertaler.py``
calls :func:`pseudo_translate_text` once per segment from the Bulk Operations
action and writes the result into each segment's target.

Three things are done to the *visible* words of a segment, all orthogonal:

1. **Tags are preserved verbatim.** Inline tags (HTML/XML, Trados/SDLXLIFF
   numeric, memoQ) are never touched or reordered — only the text between them
   is transformed. If pseudo mangled tags it would invalidate the very tag
   round-trip the export test is meant to verify.
2. **Length expansion.** The text is padded with word-like filler to the
   requested ratio (e.g. ``0.3`` → +30%) to surface overflow, clipped cells,
   reflow and truncation that identical-length copy-source can't.
3. **Character substitution.** Optionally maps letters to accented equivalents
   to exercise diacritics / encoding / font fallback.

Each transformed segment is wrapped in visible boundary markers so a dropped,
merged or misplaced segment is obvious at a glance in the exported file.
"""

from __future__ import annotations

import re

# Canonical inline-tag pattern. MUST stay in sync with
# Supervertaler.extract_all_tags() — it matches the same three tag families:
#   memoQ:           [N}   {N]   [N]
#   HTML / XML:      <tag>  </tag>  <tag/>  <tag attr="v">  (incl. hyphenated
#                    and namespaced, e.g. <mq:ch val="x"/>)
#   Trados/SDLXLIFF: <N>   </N>   <N/>
# The single capturing group lets re.split() keep the tags in the result so we
# can put them back untouched.
_TAG_RE = re.compile(
    r'(\[\d+\}|\{\d+\]|\[\d+\]|</?\d+/?>|</?[a-zA-Z][a-zA-Z0-9._:-]*(?:\s+[^>]*)?/?>)'
)

# Character mode "accents": one accented char per source char, so it stresses
# diacritics / encoding / fonts WITHOUT changing length (length is handled
# separately by the expansion step). Letters with no mapping pass through.
_ACCENT_MAP = str.maketrans({
    'a': 'á', 'e': 'é', 'i': 'í', 'o': 'ó', 'u': 'ú', 'y': 'ý',
    'n': 'ñ', 'c': 'ç', 's': 'š', 'z': 'ž', 'g': 'ğ', 'r': 'ř',
    'A': 'Á', 'E': 'É', 'I': 'Í', 'O': 'Ó', 'U': 'Ú', 'Y': 'Ý',
    'N': 'Ñ', 'C': 'Ç', 'S': 'Š', 'Z': 'Ž', 'G': 'Ğ', 'R': 'Ř',
})

# Word-like filler used to pad to the requested expansion ratio. Latin-ish
# nonsense so the output reads as "a translation" and wraps/justifies the way
# real prose would.
_FILLER = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua enim ad minim veniam"
).split()

# Default boundary markers (mathematical white square brackets, U+27E6/U+27E7):
# they don't occur in normal documents, so they're safe and unmistakable.
DEFAULT_OPEN = "⟦"
DEFAULT_CLOSE = "⟧"

# Character modes accepted by ``mode=``.
MODE_ACCENTS = "accents"
MODE_PLAIN = "plain"


def _expand_run(core: str, expansion: float) -> str:
    """Append word-like filler to ``core`` so its length grows by ~``expansion``.

    ``core`` has no leading/trailing whitespace. ``expansion`` is a ratio:
    ``0`` adds nothing, ``0.3`` adds ~30%, ``1.0`` roughly doubles the length.
    """
    if expansion <= 0 or not core:
        return core
    target_extra = round(len(core) * expansion)
    if target_extra <= 0:
        return core
    added = 0
    words = []
    i = 0
    while added < target_extra:
        word = _FILLER[i % len(_FILLER)]
        words.append(word)
        added += len(word) + 1  # +1 for the joining space
        i += 1
    return core + " " + " ".join(words)


def _pseudo_run(run: str, expansion: float, mode: str) -> str:
    """Transform one stretch of plain text (no tags), preserving surrounding
    whitespace so spacing around tags is not disturbed."""
    if not run.strip():
        return run  # pure whitespace between tags — leave it alone

    lead = run[:len(run) - len(run.lstrip())]
    trail = run[len(run.rstrip()):]
    core = run.strip()

    if mode == MODE_ACCENTS:
        core = core.translate(_ACCENT_MAP)
    # MODE_PLAIN: leave the letters as-is (markers + expansion still apply)

    core = _expand_run(core, expansion)
    return lead + core + trail


def pseudo_translate_text(
    text: str,
    expansion: float = 0.3,
    mode: str = MODE_ACCENTS,
    markers: bool = True,
    open_marker: str = DEFAULT_OPEN,
    close_marker: str = DEFAULT_CLOSE,
) -> str:
    """Return a pseudo-translation of ``text`` for export/layout testing.

    Inline tags are preserved verbatim and in order; only the visible words are
    transformed (accented and/or length-expanded) and the whole segment is
    optionally wrapped in boundary markers.

    Args:
        text: The source segment text (may contain inline tags).
        expansion: Length-growth ratio for the visible words (0 = none).
        mode: ``"accents"`` (map letters to accented forms) or ``"plain"``.
        markers: Wrap the result in visible boundary markers.
        open_marker / close_marker: Override the default ⟦ ⟧ markers.

    Returns:
        The pseudo-translated string. Empty/whitespace-only input is returned
        unchanged (so empty source stays empty, no stray markers).
    """
    if not text or not text.strip():
        return text

    # Split keeps tags (odd indices) separate from text (even indices).
    parts = _TAG_RE.split(text)
    out = []
    for idx, part in enumerate(parts):
        if idx % 2 == 1:
            out.append(part)  # a tag — never touch it
        else:
            out.append(_pseudo_run(part, expansion, mode))
    result = "".join(out)

    if markers:
        result = f"{open_marker}{result}{close_marker}"
    return result
