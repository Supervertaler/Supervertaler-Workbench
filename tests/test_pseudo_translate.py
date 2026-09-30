"""Guard the pseudo-translation transform (modules/pseudo_translate.py).

Pseudo-translation fills targets with stress-tested placeholder text so a
project can be exported and checked for layout/encoding/tag problems before
real translation. The non-negotiable invariant is that **inline tags survive
verbatim and in order** — otherwise the very tag round-trip the export test
exists to verify would be broken. These tests pin that down, plus the
length-expansion, character-substitution, and boundary-marker behaviour.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.pseudo_translate import (
    DEFAULT_CLOSE,
    DEFAULT_OPEN,
    MODE_ACCENTS,
    MODE_PLAIN,
    _TAG_RE,
    pseudo_translate_text,
)


def _tags(text: str):
    """All inline tags in order of appearance (mirrors extract_all_tags)."""
    return [m.group(0) for m in _TAG_RE.finditer(text)]


def _strip_markers(text: str) -> str:
    assert text.startswith(DEFAULT_OPEN) and text.endswith(DEFAULT_CLOSE)
    return text[len(DEFAULT_OPEN):-len(DEFAULT_CLOSE)]


# ── Tag preservation (the core invariant) ──

def test_html_formatting_tags_preserved_in_order():
    src = "The <b>quick</b> brown <i>fox</i> jumps."
    out = pseudo_translate_text(src, expansion=0.3, mode=MODE_ACCENTS)
    assert _tags(out) == ["<b>", "</b>", "<i>", "</i>"]


def test_trados_numeric_tags_preserved():
    src = "Voltage <410>rises</410> sharply."
    out = pseudo_translate_text(src, expansion=0.5, mode=MODE_ACCENTS)
    assert _tags(out) == ["<410>", "</410>"]


def test_self_closing_tags_preserved():
    # Standalone tags as SDLXLIFF/Trados imports write them, and memoQ's
    # namespaced ones. They used to be read as ordinary text, so an expanded
    # pseudo-translation could split them.
    src = "Line one<2/>line two<br/>and <mq:ch val=\"x\"/> end."
    assert _tags(src) == ["<2/>", "<br/>", '<mq:ch val="x"/>']
    out = pseudo_translate_text(src, expansion=0.5, mode=MODE_ACCENTS)
    assert _tags(out) == ["<2/>", "<br/>", '<mq:ch val="x"/>']


def test_memoq_tags_preserved():
    src = "Press [1}OK{1] to continue."
    out = pseudo_translate_text(src, expansion=0.4, mode=MODE_PLAIN)
    assert "[1}" in out and "{1]" in out


def test_tag_with_attributes_preserved_verbatim():
    src = 'See <bmk id="0" name="_Toc1">section</bmk> two.'
    out = pseudo_translate_text(src, expansion=0.3, mode=MODE_ACCENTS)
    assert '<bmk id="0" name="_Toc1">' in out
    assert "</bmk>" in out


def test_tag_text_is_not_accented():
    # The 'a' inside the attribute name must NOT become 'á'.
    src = '<span class="lead">Hi</span>'
    out = pseudo_translate_text(src, expansion=0.0, mode=MODE_ACCENTS)
    assert '<span class="lead">' in out
    assert "</span>" in out


# ── Length expansion ──

def test_expansion_grows_visible_text():
    src = "A short sentence here."
    base = len(_strip_markers(pseudo_translate_text(src, expansion=0.0, mode=MODE_PLAIN)))
    grown = len(_strip_markers(pseudo_translate_text(src, expansion=1.0, mode=MODE_PLAIN)))
    assert grown > base * 1.5  # roughly doubled, well clear of the no-expansion length


def test_zero_expansion_plain_is_just_markers_and_source():
    src = "Keep me as-is."
    out = pseudo_translate_text(src, expansion=0.0, mode=MODE_PLAIN, markers=True)
    assert _strip_markers(out) == src


def test_expansion_does_not_inflate_tags():
    src = "<b>x</b>"
    out = pseudo_translate_text(src, expansion=1.0, mode=MODE_PLAIN)
    assert _tags(out) == ["<b>", "</b>"]  # exactly one pair, not duplicated by padding


# ── Character substitution ──

def test_accents_mode_changes_letters():
    out = pseudo_translate_text("aeiou", expansion=0.0, mode=MODE_ACCENTS, markers=False)
    assert out == "áéíóú"


def test_plain_mode_leaves_letters():
    out = pseudo_translate_text("aeiou", expansion=0.0, mode=MODE_PLAIN, markers=False)
    assert out == "aeiou"


# ── Boundary markers ──

def test_markers_on_by_default():
    out = pseudo_translate_text("hello", expansion=0.0)
    assert out.startswith(DEFAULT_OPEN) and out.endswith(DEFAULT_CLOSE)


def test_markers_can_be_disabled():
    out = pseudo_translate_text("hello", expansion=0.0, mode=MODE_PLAIN, markers=False)
    assert DEFAULT_OPEN not in out and DEFAULT_CLOSE not in out


# ── Edge cases ──

def test_empty_source_stays_empty():
    assert pseudo_translate_text("", expansion=0.5) == ""
    assert pseudo_translate_text("   ", expansion=0.5) == "   "


def test_whitespace_between_tags_preserved():
    src = "<b>a</b> <i>b</i>"
    out = pseudo_translate_text(src, expansion=0.0, mode=MODE_PLAIN)
    # the single space between the </b> and <i> tags must remain a single space
    assert "</b> <i>" in out


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
