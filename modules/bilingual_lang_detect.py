"""
Bilingual Language Detection
============================

Reads the language pair out of bilingual table files that don't state it in
so many words (issue #200). Phrase names its languages in a header row; a
Trados review DOCX and a CafeTran bilingual DOCX do not, but Word stores a
language on every run of text (``<w:lang w:val="de-DE"/>``), and the tools
that write these files tag the source column with the source language and the
target column with the target language, so the proofing tools work.

The columns are counted separately. Counting the whole document (as the Phrase
fallback does) cannot tell which language is which; per column it can. When
both columns turn out to carry the same language – a file that simply has the
author's default proofing language everywhere – neither is trusted.

Everything here is best-effort: it returns ``None`` for a side it cannot read,
never raises, and callers always let the user confirm what was found.
"""

import re
from collections import Counter
from typing import Optional, Tuple

from modules import language_codes as _lc

_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

# A language code, not a word: "de", "de-DE", "zh-Hant-TW", "nld", "pt_BR".
_CODE = re.compile(r'^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$')

# Word keeps up to three languages per run: w:val for Latin/Cyrillic/Greek
# text, w:eastAsia for CJK and w:bidi for right-to-left scripts. Which one
# applies depends on the characters in the cell.
_CJK = re.compile(r'[぀-ヿ㐀-鿿가-힯豈-﫿]')
_RTL = re.compile(r'[֐-ࣿיִ-﷿ﹰ-﻿]')


def known_code(value) -> Optional[str]:
    """``value`` as a canonical BCP-47 code when it is a language code
    Workbench knows ('de-de' → 'de-DE'), else None. Words are rejected even
    when their first letters happen to be a code ('Notes' is not Norwegian)."""
    s = str(value or '').strip()
    if not _CODE.match(s):
        return None
    code = _lc.canonical(s)
    return code if code and _lc.iso_to_english_name(code) else None


def header_language(text) -> Optional[str]:
    """A column header that is a language code or a language name
    ('nl-NL', 'Dutch', 'Dutch (Belgium)'), as a canonical code; else None."""
    s = str(text or '').strip()
    if not s:
        return None
    code = known_code(s)
    if code:
        return code
    name = s.split('(')[0].strip().lower()
    if name and name in _lc._NAME_TO_ISO:
        code = _lc.canonical(s)
        return code if code and _lc.english_name(code) else None
    return None


def _cell_languages(tc) -> Counter:
    text = ''.join(t.text or '' for t in tc.iter(_W + 't'))
    attr = 'val'
    if _CJK.search(text):
        attr = 'eastAsia'
    elif _RTL.search(text):
        attr = 'bidi'
    counts = Counter()
    for lang in tc.iter(_W + 'lang'):
        code = known_code(lang.get(_W + attr))
        if code:
            counts[code] += 1
    return counts


def _winner(counts: Counter) -> Optional[str]:
    return counts.most_common(1)[0][0] if counts else None


def docx_column_languages(table, source_col: int, target_col: int,
                          header_rows: int = 1,
                          max_rows: int = 500) -> Tuple[Optional[str], Optional[str]]:
    """``(source, target)`` codes from the ``w:lang`` tags in two columns of a
    python-docx table. Either side may be None; both are None when the two
    columns carry the same language."""
    try:
        source, target = Counter(), Counter()
        for row in table.rows[header_rows:header_rows + max_rows]:
            cells = row._tr.findall(_W + 'tc')  # raw cells: no span duplicates
            if len(cells) <= max(source_col, target_col):
                continue
            source += _cell_languages(cells[source_col])
            target += _cell_languages(cells[target_col])
        src, tgt = _winner(source), _winner(target)
        if src and tgt and _lc.same_language(src, tgt):
            return (None, None)
        return (src, tgt)
    except Exception as e:
        print(f"[bilingual_lang_detect] column language detection failed: {e}")
        return (None, None)


def pair_or_partial(src: Optional[str], tgt: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Drop a pair that names the same language twice (nothing to trust)."""
    if src and tgt and _lc.same_language(src, tgt):
        return (None, None)
    return (src, tgt)
