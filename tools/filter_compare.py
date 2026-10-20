#!/usr/bin/env python3
"""Compare how Trados Studio, memoQ and Supervertaler Workbench extract the
same source documents, and report where Workbench does something different.

WHY THIS EXISTS
---------------
Workbench reads DOCX, XLSX, PPTX, IDML and HTML through the Okapi sidecar.
To find out where that falls short, we look at what Trados and memoQ make of
the same files - their SDLXLIFF and MQXLIFF output - rather than at their
code. For every source file the tool lines up the words each program
extracted and reports three kinds of difference:

  * text one program offers for translation and the other leaves out
    (hidden text, text boxes, footnotes, fields, headers, comments ...)
  * places where one program starts a new segment and the other doesn't
  * formatting one program keeps as a tag and the other drops, and
    placeholder tags (pictures, footnote references, tabs) that are
    present in one and missing from the other

Each difference is counted by how many segments it affects, so the top of
the report is where Workbench's handling differs most often.

USAGE
-----
    python tools/filter_compare.py "D:\\path\\to\\folder"
        Every DOCX/XLSX/PPTX/IDML/HTML file in the folder (and below) is
        paired with the SDLXLIFF / MQXLIFF whose name starts with the
        source file's name, e.g. "Spec.docx" with "Spec.docx.sdlxliff" and
        "Spec.docx_eng-GB.mqxliff". In a Trados project folder, point it at
        the project folder: the segmented SDLXLIFF in the target-language
        folder (e.g. nl-NL) is preferred over the unsegmented source copy.

    python tools/filter_compare.py Spec.docx --trados Spec.docx.sdlxliff
                                             --memoq Spec.docx_eng-GB.mqxliff

    --out PATH          where to write the HTML report
                        (default: filter-compare-report.html in the folder)
    --source-lang CODE  source language for Workbench's segmentation rules,
                        when the SDLXLIFF/MQXLIFF doesn't say (default: en)

Workbench's side is extracted exactly as an import does: the same Okapi
call, with Workbench's default import options (DEFAULT_IMPORT_OPTIONS), and
the same skipping of empty segments. Trados's and memoQ's files are read
with Workbench's own SDLXLIFF and MQXLIFF readers. Tracked deletions in an
SDLXLIFF are left out, matching Workbench's "accept tracked changes" default.

Exit status: 0 when a report was written (problems with individual files
are listed in it), 2 when nothing could be compared at all.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from modules.okapi_import_options import (  # noqa: E402
    DEFAULT_IMPORT_OPTIONS, okapi_sidecar_options, okapi_subdoc_category,
)

# Source formats Workbench imports through Okapi. XLIFF, PO and TMX also go
# through Okapi but are bilingual already, so there is nothing to compare.
SOURCE_EXTS = {'.docx', '.xlsx', '.pptx', '.idml', '.html', '.htm'}

# Examples kept per finding per file. Counts are always complete.
MAX_EXAMPLES = 5

# A stretch of text with no word unique to it on both sides is handed to
# difflib only when it is small; beyond this many token pairs it is reported
# as "could not line up" instead of risking a minutes-long comparison.
SMALL_REGION = 1_000_000

WORKBENCH, TRADOS, MEMOQ = 'Workbench', 'Trados', 'memoQ'


# ═══════════════════════════════════════════════════════════════════
#  Tool-neutral model of one program's extraction
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Tag:
    kind: str            # 'pair' (formatting around text) or 'single' (placeholder)
    labels: frozenset    # pair: {'bold', 'italic', ...}; single: {'picture'} etc.
    start: int           # character offsets into Seg.text
    end: int


@dataclass
class Seg:
    text: str
    tags: List[Tag] = field(default_factory=list)
    where: str = 'body'  # Workbench only: body, header, footer, comment, ...


@dataclass
class Extraction:
    tool: str
    path: str
    segments: List[Seg]
    source_lang: str = ''
    target_lang: str = ''


# ═══════════════════════════════════════════════════════════════════
#  Inline markup → plain text + tags
# ═══════════════════════════════════════════════════════════════════

def _local(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


def _walk_inline(elem: ET.Element, pair_labels, single_labels) -> Seg:
    """Flatten an XLIFF inline fragment into text plus tags.

    Handles <g> (paired, wraps content), <bpt>/<ept> and <bx>/<ex>
    (paired by id), <x>/<ph>/<it> (placeholders, content is code not text)
    and <mrk> (transparent, except Trados tracked deletions).
    """
    parts: List[str] = []
    tags: List[Tag] = []
    open_: List[Tuple[str, int, frozenset]] = []   # (id, start, labels)
    length = 0

    def add_text(t: Optional[str]):
        nonlocal length
        if t:
            parts.append(t)
            length += len(t)

    def close(tag_id: str):
        for k in range(len(open_) - 1, -1, -1):
            if open_[k][0] == tag_id:
                _, start, labels = open_.pop(k)
                tags.append(Tag('pair', labels, start, length))
                return
        # Closing code whose opening sits in an earlier segment: it
        # formats everything from the start of this one.
        tags.append(Tag('pair', pair_labels(tag_id, None), 0, length))

    def walk(el: ET.Element):
        add_text(el.text)
        for child in el:
            name = _local(child.tag)
            if name == 'g':
                start = length
                walk(child)
                tags.append(Tag('pair', pair_labels(child.get('id', ''), child), start, length))
            elif name in ('bpt', 'bx'):
                # XLIFF links <bx>/<ex> through rid when it is present.
                open_.append((child.get('rid') or child.get('id', ''), length,
                              pair_labels(child.get('id', ''), child)))
            elif name in ('ept', 'ex'):
                close(child.get('rid') or child.get('id', ''))
            elif name in ('x', 'ph', 'it'):
                tags.append(Tag('single', single_labels(child.get('id', ''), child), length, length))
            elif name == 'mrk' and child.get('mtype') == 'x-sdl-deleted':
                pass
            else:
                walk(child)
            add_text(child.tail)

    walk(elem)
    for _, start, labels in open_:      # opened here, closed in a later segment
        tags.append(Tag('pair', labels, start, length))
    return Seg(''.join(parts), tags)


def _parse_fragment(xml: str) -> ET.Element:
    """Parse an inline-XML fragment (text and elements, no single root).
    Elements serialised by ElementTree carry their own namespace
    declarations, so wrapping them in a plain root is enough."""
    return ET.fromstring(f'<fragment>{xml}</fragment>')


_PLACEHOLDER_NAMES = (
    ('fn', 'footnote reference'), ('footnote', 'footnote reference'),
    ('endnote', 'endnote reference'), ('picture', 'picture'),
    ('image', 'picture'), ('img', 'picture'), ('tab', 'tab'),
    ('br', 'line break'), ('field', 'field'), ('fld', 'field'),
)


def _placeholder_label(name: str) -> frozenset:
    n = (name or '').strip().lower()
    for prefix, label in _PLACEHOLDER_NAMES:
        if n.startswith(prefix):
            return frozenset({label})
    if n and not n.isdigit():
        return frozenset({n})
    return frozenset({'placeholder'})


# ── Workbench (Okapi sidecar output) ────────────────────────────────

_WB_TAG = re.compile(r'<(/?)(sup|sub|cf|g\d+|b|i|u|s)(?:\s[^<>]*)?>|<x\d+/>')
_WB_LABELS = {
    'b': 'bold', 'i': 'italic', 'u': 'underline', 's': 'strikethrough',
    'sup': 'superscript', 'sub': 'subscript', 'cf': 'colour',
}


def parse_workbench_tagged(tagged: str) -> Seg:
    """Split the sidecar's sourceWithTags ("<b>bold</b> and <x3/>") into
    plain text and tags. <gN> is an Okapi code Workbench has no name for."""
    parts: List[str] = []
    tags: List[Tag] = []
    open_: List[Tuple[str, int]] = []
    length = 0
    pos = 0
    for m in _WB_TAG.finditer(tagged):
        chunk = tagged[pos:m.start()]
        parts.append(chunk)
        length += len(chunk)
        pos = m.end()
        closing, name = m.group(1), m.group(2)
        if name is None:                                  # <xN/>
            tags.append(Tag('single', frozenset({'placeholder'}), length, length))
            continue
        label = frozenset({_WB_LABELS.get(name, 'other')})
        if not closing:
            open_.append((name, length))
            continue
        for k in range(len(open_) - 1, -1, -1):
            if open_[k][0] == name:
                _, start = open_.pop(k)
                tags.append(Tag('pair', label, start, length))
                break
        else:
            tags.append(Tag('pair', label, 0, length))
    chunk = tagged[pos:]
    parts.append(chunk)
    length += len(chunk)
    for name, start in open_:
        tags.append(Tag('pair', frozenset({_WB_LABELS.get(name, 'other')}), start, length))
    return Seg(''.join(parts), tags)


def read_workbench(sidecar, path: str, source_lang: str, target_lang: str) -> Extraction:
    """Extract a source file the way Workbench's import does."""
    result = sidecar.extract(
        path,
        source_lang=source_lang,
        target_lang=target_lang,
        segment=True,
        options=okapi_sidecar_options(dict(DEFAULT_IMPORT_OPTIONS)),
    )
    segments = []
    for raw in result.get('segments', []):
        text = (raw.get('source') or '').strip()
        if not text:
            continue
        tagged = (raw.get('sourceWithTags') or '').strip()
        seg = parse_workbench_tagged(tagged) if tagged else Seg(text)
        seg.where = okapi_subdoc_category(raw.get('subDocument') or '') or 'body'
        segments.append(seg)
    return Extraction(WORKBENCH, path, segments, source_lang, target_lang)


# ── Trados (SDLXLIFF) ───────────────────────────────────────────────

_SDL_NS = '{http://sdl.com/FileTypes/SdlXliff/1.0}'
_CF_ATTR = re.compile(r'([\w-]+)\s*=\s*"?([^"\s>]+)"?')
_FALSEY = {'false', '0', 'none', 'normal', ''}


def _formatting_labels(props: Dict[str, str]) -> set:
    """Labels from Trados formatting properties (fmt-def values or the
    attributes of a "<cf bold=True>" display text). Keys are lower-case."""
    labels = set()
    for key, value in props.items():
        v = (value or '').strip().lower()
        if key in ('bold', 'italic', 'underline', 'strikethrough'):
            if v not in _FALSEY:
                labels.add(key)
        elif key in ('superscript', 'subscript'):
            if v not in _FALSEY:
                labels.add(key)
        elif key == 'textposition':
            if v in ('superscript', 'subscript'):
                labels.add(v)
        elif key in ('textcolor', 'color', 'colour', 'backgroundcolor', 'highlight'):
            labels.add('colour')
        elif key in ('fontname', 'font', 'fontsize', 'size'):
            labels.add('font')
        elif key == 'rstyle' and v == 'hyperlink':
            labels.add('hyperlink')
        else:
            labels.add('other')
    return labels


def _read_trados_tag_defs(root: ET.Element) -> Tuple[Dict[str, frozenset], Dict[str, frozenset]]:
    """Map SDLXLIFF tag ids to labels, from the header's tag-defs/fmt-defs."""
    fmt_defs: Dict[str, Dict[str, str]] = {}
    for fd in root.iter(f'{_SDL_NS}fmt-def'):
        fmt_defs[fd.get('id', '')] = {
            (v.get('key') or '').lower(): (v.text or '') for v in fd.iter(f'{_SDL_NS}value')
        }
    pairs: Dict[str, frozenset] = {}
    singles: Dict[str, frozenset] = {}
    for tag in root.iter(f'{_SDL_NS}tag'):
        tag_id = tag.get('id', '')
        bpt = tag.find(f'{_SDL_NS}bpt')
        if bpt is not None:
            name = (bpt.get('name') or '').lower()
            if 'hyperlink' in name or name == 'a':
                pairs[tag_id] = frozenset({'hyperlink'})
                continue
            fmt = tag.find(f'{_SDL_NS}fmt')
            props = fmt_defs.get(fmt.get('id', '')) if fmt is not None else None
            if not props:
                props = {k.lower(): v for k, v in _CF_ATTR.findall(bpt.text or '')}
            labels = _formatting_labels(props) if name == 'cf' or props else {'other'}
            pairs[tag_id] = frozenset(labels or {'other'})
            continue
        ph = tag.find(f'{_SDL_NS}ph')
        if ph is None:
            ph = tag.find(f'{_SDL_NS}st')
        if ph is not None:
            singles[tag_id] = _placeholder_label(ph.get('name') or '')
    return pairs, singles


def read_trados(path: str) -> Extraction:
    from modules.sdlppx_handler import SDLXLIFFParser
    xf = SDLXLIFFParser(log_callback=lambda *_: None).parse_file(path)
    if xf is None:
        raise ValueError("Workbench's SDLXLIFF reader could not read this file")
    pairs, singles = _read_trados_tag_defs(xf.root)
    pair_labels = lambda tag_id, _el: pairs.get(tag_id, frozenset({'other'}))     # noqa: E731
    single_labels = lambda tag_id, _el: singles.get(tag_id, frozenset({'placeholder'}))  # noqa: E731
    segments = []
    for s in xf.segments:
        seg = _walk_inline(_parse_fragment(s.source_xml), pair_labels, single_labels)
        if seg.text.strip():
            segments.append(seg)
    if not segments:
        raise ValueError(
            "no segments found. Trados hasn't split this file into segments - "
            "use the SDLXLIFF from the target-language folder (e.g. nl-NL) instead")
    return Extraction(TRADOS, path, segments, xf.source_lang, xf.target_lang)


# ── memoQ (MQXLIFF) ─────────────────────────────────────────────────

def _memoq_pair_labels(_tag_id, el) -> frozenset:
    ctype = ((el.get('ctype') if el is not None else '') or '').lower()
    for key, label in (('bold', 'bold'), ('italic', 'italic'), ('underl', 'underline'),
                       ('superscript', 'superscript'), ('subscript', 'subscript'),
                       ('strike', 'strikethrough'), ('link', 'hyperlink')):
        if key in ctype:
            return frozenset({label})
    return frozenset({'other'})


def _memoq_single_labels(_tag_id, el) -> frozenset:
    return _placeholder_label((el.get('ctype') or '').replace('x-', ''))


def read_memoq(path: str) -> Extraction:
    from modules.mqxliff_handler import MQXLIFFHandler
    handler = MQXLIFFHandler()
    if not handler.load(path):
        raise ValueError("Workbench's MQXLIFF reader could not read this file")
    segments = []
    for fs in handler.extract_source_segments():
        seg = _walk_inline(ET.fromstring(fs.formatted_xml), _memoq_pair_labels, _memoq_single_labels)
        if seg.text.strip():
            segments.append(seg)
    if not segments:
        raise ValueError("no segments found in this MQXLIFF")
    return Extraction(MEMOQ, path, segments, handler.source_lang or '', handler.target_lang or '')


# ═══════════════════════════════════════════════════════════════════
#  Tokens and alignment
# ═══════════════════════════════════════════════════════════════════

_TOKEN = re.compile(r'\w+|[^\w\s]')
_SEP = '\x1f'   # joins the two halves of a split's description


@dataclass
class Tokens:
    words: List[str]
    seg: List[int]       # segment index of each token
    start: List[int]     # character offsets within that segment
    end: List[int]
    first: List[int]     # first token index of each segment (-1 if none)
    fmt: List[frozenset]  # formatting labels covering each token
    ph: List[List[str]]  # placeholder labels attached to each token


def tokenize(ex: Extraction) -> Tokens:
    words, seg_of, starts, ends, first, fmt, ph = [], [], [], [], [], [], []
    for k, seg in enumerate(ex.segments):
        base = len(words)
        first.append(base)
        for m in _TOKEN.finditer(seg.text):
            words.append(m.group())
            seg_of.append(k)
            starts.append(m.start())
            ends.append(m.end())
            fmt.append(frozenset())
            ph.append([])
        n = len(words) - base
        if n == 0:
            first[-1] = -1
            continue
        for tag in seg.tags:
            if tag.kind == 'pair':
                if tag.end <= tag.start:
                    continue
                # Every token the tag touches, so formatting inside a word
                # (the subscript 4 in "CH4") still counts.
                t = bisect_right(ends, tag.start, base, base + n)
                while t < base + n and starts[t] < tag.end:
                    fmt[t] = fmt[t] | tag.labels
                    t += 1
            else:
                # Attach a placeholder to the token it precedes, or to the
                # segment's last token when it comes at the end.
                t = bisect_left(starts, tag.start, base, base + n)
                if t >= base + n:
                    t = base + n - 1
                ph[t].extend(sorted(tag.labels))
    return Tokens(words, seg_of, starts, ends, first, fmt, ph)


def _lis(pairs: Sequence[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Longest chain of pairs (sorted by first item) increasing in the second."""
    tails: List[int] = []
    tails_at: List[int] = []
    prev = [-1] * len(pairs)
    for k, (_, j) in enumerate(pairs):
        pos = bisect_left(tails, j)
        if pos == len(tails):
            tails.append(j)
            tails_at.append(k)
        else:
            tails[pos] = j
            tails_at[pos] = k
        prev[k] = tails_at[pos - 1] if pos else -1
    chain = []
    k = tails_at[-1] if tails_at else -1
    while k >= 0:
        chain.append(pairs[k])
        k = prev[k]
    chain.reverse()
    return chain


def align(a: Sequence[str], b: Sequence[str]) -> Tuple[List[int], List[Tuple[int, int, int, int]]]:
    """Line up two token sequences. Returns a2b (index in b of each token of
    a, or -1) and the regions that were too large and too repetitive to line
    up. Patience-style: words that occur exactly once on both sides anchor
    the alignment, then each gap is aligned the same way on its own, so the
    work is roughly proportional to the length, not its square."""
    a2b = [-1] * len(a)
    given_up: List[Tuple[int, int, int, int]] = []
    stack = [(0, len(a), 0, len(b))]
    while stack:
        alo, ahi, blo, bhi = stack.pop()
        while alo < ahi and blo < bhi and a[alo] == b[blo]:
            a2b[alo] = blo
            alo += 1
            blo += 1
        while alo < ahi and blo < bhi and a[ahi - 1] == b[bhi - 1]:
            ahi -= 1
            bhi -= 1
            a2b[ahi] = bhi
        if alo >= ahi or blo >= bhi:
            continue
        count_a = Counter(a[alo:ahi])
        count_b = Counter(b[blo:bhi])
        pos_b = {b[j]: j for j in range(blo, bhi)
                 if count_b[b[j]] == 1 and count_a.get(b[j]) == 1}
        anchors = [(i, pos_b[a[i]]) for i in range(alo, ahi) if a[i] in pos_b]
        if anchors:
            prev_i, prev_j = alo - 1, blo - 1
            for i, j in _lis(anchors):
                a2b[i] = j
                stack.append((prev_i + 1, i, prev_j + 1, j))
                prev_i, prev_j = i, j
            stack.append((prev_i + 1, ahi, prev_j + 1, bhi))
        elif (ahi - alo) * (bhi - blo) <= SMALL_REGION:
            sm = SequenceMatcher(None, a[alo:ahi], b[blo:bhi], autojunk=False)
            for blk in sm.get_matching_blocks():
                for k in range(blk.size):
                    a2b[alo + blk.a + k] = blo + blk.b + k
        else:
            given_up.append((alo, ahi, blo, bhi))
    return a2b, given_up


def slide_gaps(a: Sequence[str], b: Sequence[str], a2b: List[int],
               a_starts: set, b_starts: set) -> List[int]:
    """Move each one-sided gap to a segment boundary where the words allow.

    A gap is often ambiguous: with "... a tag ." on one side and "... a tag .
    Footnote text ... segment ." on the other, the extra run can be read as
    ". Footnote ... segment" or as "Footnote ... segment .". Diff picks one
    arbitrarily; we prefer the reading that starts (or ends) at a segment
    boundary, which is how a translator sees it and lets moved text be
    recognised as the same run on both sides. a_starts/b_starts are the
    first token index of every segment on each side.
    """
    m = [(i, j) for i, j in enumerate(a2b) if j >= 0]
    for k in range(len(m) - 1):
        (i, j), (i2, j2) = m[k], m[k + 1]
        if i2 == i + 1 and j2 > j + 1:          # words only in b
            seq, lo, hi, starts, side = b, j + 1, j2, b_starts, 1
        elif j2 == j + 1 and i2 > i + 1:        # words only in a
            seq, lo, hi, starts, side = a, i + 1, i2, a_starts, 0
        else:
            continue
        # How far may the run [lo, hi) shift? Each step needs the word it
        # gives up to equal the word it takes over, and the matched word it
        # trades places with to sit in an unbroken matched stretch.
        right = 0
        while (k + 1 + right < len(m) and hi + right < len(seq)
               and m[k + 1 + right][side] == hi + right
               and m[k + 1 + right][1 - side] == m[k + 1][1 - side] + right
               and seq[lo + right] == seq[hi + right]):
            right += 1
        left = 0
        while (k - left >= 0 and lo - 1 - left >= 0
               and m[k - left][side] == lo - 1 - left
               and m[k - left][1 - side] == m[k][1 - side] - left
               and seq[lo - 1 - left] == seq[hi - 1 - left]):
            left += 1
        if not right and not left:
            continue

        def score(off):
            return ((lo + off) in starts) * 2 + ((hi + off) in starts), -abs(off)
        best = max(range(-left, right + 1), key=score)
        if best > 0:
            for t in range(best):
                p = list(m[k + 1 + t])
                p[side] = lo + t
                m[k + 1 + t] = tuple(p)
        elif best < 0:
            for t in range(-best):
                p = list(m[k - t])
                p[side] = hi - 1 - t
                m[k - t] = tuple(p)
    out = [-1] * len(a2b)
    for i, j in m:
        out[i] = j
    return out


# ═══════════════════════════════════════════════════════════════════
#  Differences
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Finding:
    category: str
    detail: str
    segments: set = field(default_factory=set)   # affected segments ('ref'|'wb', index)
    count: int = 0                                 # instances
    examples: List[Tuple[str, str]] = field(default_factory=list)   # (ref html, wb html)

    def add(self, seg_key, ref_html: str, wb_html: str):
        self.segments.add(seg_key)
        self.count += 1
        if len(self.examples) < MAX_EXAMPLES:
            self.examples.append((ref_html, wb_html))


def describe(ref: str, category: str, detail: str) -> str:
    """One plain-English line per kind of difference."""
    if category == 'only_ref':
        return f"Text {ref} offers for translation that Workbench leaves out"
    if category == 'only_wb':
        where = f" (from the {detail})" if detail and detail != 'body' else ''
        return f"Text Workbench offers for translation that {ref} leaves out{where}"
    if category == 'differs':
        return f"Text that {ref} and Workbench extract differently"
    if category == 'moved':
        return f"The same text, but {ref} and Workbench put it in a different order"
    if category == 'unaligned':
        return "Long stretches the tool could not line up - compare these by hand"
    if category in ('ref_splits', 'wb_splits'):
        before, after = detail.split(_SEP, 1)
        who, other = (ref, 'Workbench') if category == 'ref_splits' else ('Workbench', ref)
        return (f"{who} starts a new segment after {before}, before {after}; "
                f"{other} keeps it in one segment")
    if category == 'fmt_missing':
        return f"{detail.capitalize()} that {ref} keeps as a tag but Workbench drops"
    if category == 'fmt_extra':
        return f"{detail.capitalize()} tag in Workbench where {ref} has none"
    if category == 'ph_missing':
        return f"{ref} has a placeholder tag ({detail}) that Workbench doesn't"
    if category == 'ph_extra':
        return f"Workbench has a placeholder tag that {ref} doesn't"
    return f"{category} {detail}"


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def _snippet(text: str, start: int, end: int, marker: str = '', width: int = 70) -> str:
    """Segment text with [start, end) highlighted (or a marker at start)."""
    left = text[max(0, start - width):start]
    right = text[end:end + width]
    out = ('…' if start > width else '') + _esc(left)
    out += f'<span class="split">{marker}</span>' if marker else f'<mark>{_esc(text[start:end])}</mark>'
    return out + _esc(right) + ('…' if end + width < len(text) else '')


def _span_html(ex: Extraction, tk: Tokens, i0: int, i1: int) -> str:
    """Highlight tokens i0..i1 (inclusive), within the first segment."""
    k = tk.seg[i0]
    last = i0
    while last + 1 <= i1 and tk.seg[last + 1] == k:
        last += 1
    out = _snippet(ex.segments[k].text, tk.start[i0], tk.end[last])
    extra = tk.seg[i1] - k
    if extra:
        out += f' <span class="more">(and {extra} more segment{"s" if extra > 1 else ""})</span>'
    return out


def _point_html(ex: Extraction, tk: Tokens, after: int) -> str:
    """Show where text would sit: just after token `after` (or before 0)."""
    if not tk.words:
        return '<span class="more">(nothing extracted)</span>'
    if after < 0:
        return _snippet(ex.segments[tk.seg[0]].text, tk.start[0], tk.start[0], '‸')
    k = tk.seg[after]
    return _snippet(ex.segments[k].text, tk.end[after], tk.end[after], '‸')


def _boundary_detail(tk: Tokens, i: int) -> str:
    """Describe the place between token i and i+1, for grouping splits."""
    w, nxt = tk.words[i], tk.words[i + 1]
    head = tk.words[tk.first[tk.seg[i]]:i + 1]
    if (len(head) <= 4 and any(t.isdigit() for t in head)
            and all(t.isdigit() or t in '.)(' or (len(t) == 1 and t.isalpha()) for t in head)):
        before = 'a list number such as “1.”'
    elif w == '.' and i > 0 and tk.words[i - 1].isalpha() and len(tk.words[i - 1]) <= 4 \
            and tk.seg[i - 1] == tk.seg[i]:
        before = f'“{tk.words[i - 1]}.”'
    elif not w[0].isalnum():
        before = f'“{w}”'
    elif w.isdigit():
        before = 'a number'
    else:
        before = 'a word'
    if nxt[0].isdigit():
        after = 'a number'
    elif nxt[0].isupper():
        after = 'a capitalised word'
    elif nxt[0].isalpha():
        after = 'a lowercase word'
    else:
        after = f'“{nxt}”'
    return f'{before}{_SEP}{after}'


def compare(ref: Extraction, wb: Extraction) -> Tuple[Dict[Tuple[str, str], Finding], List[str]]:
    """All differences between a reference tool's extraction and Workbench's."""
    ta, tb = tokenize(ref), tokenize(wb)
    a2b, given_up = align(ta.words, tb.words)
    a2b = slide_gaps(ta.words, tb.words, a2b,
                     {t for t in ta.first if t >= 0}, {t for t in tb.first if t >= 0})
    findings: Dict[Tuple[str, str], Finding] = {}
    notes: List[str] = []

    def finding(category: str, detail: str = '') -> Finding:
        return findings.setdefault((category, detail), Finding(category, detail))

    # ── Text present on one side only ────────────────────────────
    given_up_starts = sorted(g[0] for g in given_up)
    ref_only, wb_only = [], []     # (i0, i1, after_j) / (j0, j1, after_i)
    prev_i = prev_j = -1
    matched = [(i, j) for i, j in enumerate(a2b) if j >= 0]
    for i, j in matched + [(len(ta.words), len(tb.words))]:
        gi = (prev_i + 1, i)
        gj = (prev_j + 1, j)
        if gi[0] < gi[1] or gj[0] < gj[1]:
            k = bisect_left(given_up_starts, gi[0])
            if gi[0] < gi[1] and k < len(given_up_starts) and given_up_starts[k] < gi[1]:
                f = finding('unaligned')
                f.add(('ref', ta.seg[gi[0]]), _span_html(ref, ta, gi[0], gi[1] - 1),
                      _span_html(wb, tb, gj[0], gj[1] - 1) if gj[0] < gj[1] else '')
            elif gi[0] < gi[1] and gj[0] < gj[1]:
                finding('differs').add(('ref', ta.seg[gi[0]]),
                                       _span_html(ref, ta, gi[0], gi[1] - 1),
                                       _span_html(wb, tb, gj[0], gj[1] - 1))
            elif gi[0] < gi[1]:
                ref_only.append((gi[0], gi[1] - 1, prev_j))
            else:
                wb_only.append((gj[0], gj[1] - 1, prev_i))
        prev_i, prev_j = i, j

    # A run missing on one side and extra on the other with the same words
    # is the same text in a different place (e.g. headers listed first).
    def run_key(words, r):
        return tuple(words[r[0]:r[1] + 1])
    wb_runs: Dict[tuple, List] = {}
    for r in wb_only:
        if r[1] - r[0] >= 2:
            wb_runs.setdefault(run_key(tb.words, r), []).append(r)
    moved: Counter = Counter()
    for r in ref_only:
        key = run_key(ta.words, r)
        if r[1] - r[0] >= 2 and len(wb_runs.get(key, ())) > moved[key]:
            w = wb_runs[key][moved[key]]
            moved[key] += 1
            finding('moved').add(('ref', ta.seg[r[0]]), _span_html(ref, ta, r[0], r[1]),
                                 _span_html(wb, tb, w[0], w[1]))
        else:
            finding('only_ref').add(('ref', ta.seg[r[0]]), _span_html(ref, ta, r[0], r[1]),
                                    _point_html(wb, tb, r[2]))
    for r in wb_only:
        key = run_key(tb.words, r)
        if moved[key] > 0:
            moved[key] -= 1
            continue
        where = wb.segments[tb.seg[r[0]]].where
        finding('only_wb', where).add(('wb', tb.seg[r[0]]), _point_html(ref, ta, r[2]),
                                      _span_html(wb, tb, r[0], r[1]))

    # ── Segment boundaries ───────────────────────────────────────
    for i in range(len(ta.words) - 1):
        j = a2b[i]
        if j < 0 or a2b[i + 1] != j + 1:
            continue
        split_a = ta.seg[i] != ta.seg[i + 1]
        split_b = tb.seg[j] != tb.seg[j + 1]
        if split_a == split_b:
            continue
        if split_a:
            ka = ta.seg[i]
            ref_html = (_snippet(ref.segments[ka].text, ta.end[i], ta.end[i], '‖')
                        + ' ' + _esc(ref.segments[ta.seg[i + 1]].text[:70]))
            kb = tb.seg[j]
            wb_html = _snippet(wb.segments[kb].text, tb.start[j], tb.end[j + 1])
            finding('ref_splits', _boundary_detail(ta, i)).add(('ref', ka), ref_html, wb_html)
        else:
            kb = tb.seg[j]
            wb_html = (_snippet(wb.segments[kb].text, tb.end[j], tb.end[j], '‖')
                       + ' ' + _esc(wb.segments[tb.seg[j + 1]].text[:70]))
            ka = ta.seg[i]
            ref_html = _snippet(ref.segments[ka].text, ta.start[i], ta.end[i + 1])
            finding('wb_splits', _boundary_detail(tb, j)).add(('ref', ka), ref_html, wb_html)

    # ── Formatting carried as tags ───────────────────────────────
    active: Dict[Tuple[str, str], List[int]] = {}    # key -> [i0, i_last, j0, j_last]

    def close_run(key):
        i0, i_last, j0, j_last = active.pop(key)
        category = 'fmt_missing' if key[0] == 'missing' else 'fmt_extra'
        finding(category, key[1]).add(('ref', ta.seg[i0]), _span_html(ref, ta, i0, i_last),
                                      _span_html(wb, tb, j0, j_last))

    last_i = last_j = -2
    for i, j in matched:
        cur = {('missing', lab) for lab in ta.fmt[i] - tb.fmt[j]}
        cur |= {('extra', lab) for lab in tb.fmt[j] - ta.fmt[i]}
        contiguous = i == last_i + 1 and j == last_j + 1
        for key in list(active):
            if key not in cur or not contiguous:
                close_run(key)
        for key in cur:
            if key in active:
                active[key][1], active[key][3] = i, j
            else:
                active[key] = [i, i, j, j]
        last_i, last_j = i, j
    for key in list(active):
        close_run(key)

    # ── Placeholder tags ─────────────────────────────────────────
    used: Dict[int, int] = Counter()
    for i, j in matched:
        for label in ta.ph[i]:
            for cand in (j, j - 1, j + 1):
                if 0 <= cand < len(tb.words) and len(tb.ph[cand]) > used[cand]:
                    used[cand] += 1
                    break
            else:
                finding('ph_missing', label).add(
                    ('ref', ta.seg[i]), _span_html(ref, ta, i, i), _span_html(wb, tb, j, j))
    b2a = {j: i for i, j in matched}
    for j, labels in enumerate(tb.ph):
        spare = len(labels) - used[j]
        if spare > 0 and j in b2a:
            i = b2a[j]
            for _ in range(spare):
                finding('ph_extra').add(('ref', ta.seg[i]), _span_html(ref, ta, i, i),
                                        _span_html(wb, tb, j, j))

    words_a = sum(1 for w in ta.words if w[0].isalnum())
    words_b = sum(1 for w in tb.words if w[0].isalnum())
    if min(words_a, words_b) < 0.5 * max(words_a, words_b):
        notes.append(f"{ref} has {words_a} words and Workbench {words_b}: the {ref} file may "
                     f"not come from this source document (or only from part of it).")
    if given_up:
        notes.append(f"{len(given_up)} stretch(es) of text were too long and repetitive "
                     f"to line up word by word; they are listed under 'could not line up'.")
    return findings, notes


# ═══════════════════════════════════════════════════════════════════
#  Pairing source files with Trados / memoQ output
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Job:
    source: str
    trados: Optional[str] = None
    memoq: Optional[str] = None


def _is_segmented_sdlxliff(path: str) -> bool:
    try:
        with open(path, 'rb') as f:
            return b'mtype="seg"' in f.read()
    except OSError:
        return False


def _pick(source: Path, candidates: List[Path], prefer_segmented: bool) -> Optional[str]:
    name = source.name.lower()
    matches = [c for c in candidates if c.name.lower().startswith(name)]
    if not matches:
        return None

    def score(c: Path):
        exact = c.stem.lower() == name or c.name.lower().startswith(name + '_')
        seg = _is_segmented_sdlxliff(str(c)) if prefer_segmented else True
        try:
            mtime = c.stat().st_mtime
        except OSError:
            mtime = 0
        return (seg, exact, mtime)
    return str(max(matches, key=score))


def find_jobs(folder: Path) -> List[Job]:
    sources, sdl, mq = [], [], []
    for dirpath, _dirs, files in os.walk(folder):
        for fn in files:
            if fn.startswith('~$'):
                continue
            p = Path(dirpath) / fn
            ext = p.suffix.lower()
            if ext in SOURCE_EXTS:
                sources.append(p)
            elif ext == '.sdlxliff':
                sdl.append(p)
            elif ext == '.mqxliff':
                mq.append(p)
    sources.sort(key=lambda p: str(p).lower())
    return [Job(str(s), _pick(s, sdl, True), _pick(s, mq, False)) for s in sources]


# ═══════════════════════════════════════════════════════════════════
#  Running the comparison
# ═══════════════════════════════════════════════════════════════════

@dataclass
class FileResult:
    job: Job
    extractions: Dict[str, Extraction] = field(default_factory=dict)
    findings: Dict[str, Dict[Tuple[str, str], Finding]] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def _reason(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text if len(text) < 400 else text[:400] + '…'


def run_job(job: Job, sidecar, source_lang: Optional[str]) -> FileResult:
    """Compare one source file. Never raises: problems go into .errors."""
    res = FileResult(job)
    if not job.trados and not job.memoq:
        res.errors.append("No Trados (.sdlxliff) or memoQ (.mqxliff) version found, so it was skipped.")
        return res
    for tool, path, reader in ((TRADOS, job.trados, read_trados), (MEMOQ, job.memoq, read_memoq)):
        if not path:
            continue
        try:
            res.extractions[tool] = reader(path)
        except Exception as exc:     # one unreadable file must not stop the run
            res.errors.append(f"{tool} file could not be read ({path}): {_reason(exc)}")
    if not res.extractions:
        return res

    refs = list(res.extractions.values())
    src = source_lang or next((e.source_lang for e in refs if e.source_lang), '') or 'en'
    trg = next((e.target_lang for e in refs if e.target_lang), '') or 'en'
    try:
        if not sidecar.is_running() and not sidecar.start():
            raise RuntimeError("the Okapi sidecar is not running and could not be restarted")
        res.extractions[WORKBENCH] = read_workbench(sidecar, job.source, src, trg)
    except Exception as exc:
        res.errors.append(f"Workbench (Okapi) could not extract the source file: {_reason(exc)}")
        return res

    for tool in (TRADOS, MEMOQ):
        if tool in res.extractions:
            try:
                res.findings[tool], notes = compare(res.extractions[tool], res.extractions[WORKBENCH])
                res.notes.extend(f"{tool}: {n}" for n in notes)
            except Exception as exc:
                res.errors.append(f"Comparing with {tool} failed: {_reason(exc)}")
    return res


def _stats(ex: Extraction) -> Tuple[int, int, int, int]:
    words = sum(len(re.findall(r'\w+', s.text)) for s in ex.segments)
    pairs = sum(1 for s in ex.segments for t in s.tags if t.kind == 'pair')
    singles = sum(1 for s in ex.segments for t in s.tags if t.kind == 'single')
    return len(ex.segments), words, pairs, singles


# ═══════════════════════════════════════════════════════════════════
#  HTML report
# ═══════════════════════════════════════════════════════════════════

_CSS = """
:root { --bg:#fff; --fg:#1d1d1f; --muted:#6b6b70; --line:#e2e2e6; --card:#f7f7f9;
        --mark:#ffe58a; --split:#d22; --bad:#b3261e; --ok:#1b7f3b; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#17171a; --fg:#ececf0; --muted:#a0a0a8; --line:#33333a; --card:#212126;
          --mark:#7a6210; --split:#ff6b6b; --bad:#ff8a80; --ok:#7bd88f; } }
body { background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, Segoe UI, sans-serif;
       max-width:1100px; margin:24px auto; padding:0 16px; }
h1 { font-size:24px; margin-bottom:4px; } h2 { margin-top:36px; font-size:19px; }
h3 { font-size:16px; margin:22px 0 6px; }
.muted, .more { color:var(--muted); font-size:13px; }
table { border-collapse:collapse; width:100%; margin:8px 0; }
th, td { border-bottom:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }
th { font-weight:600; font-size:13px; color:var(--muted); }
td.n { text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums; }
td.files { white-space:nowrap; }
mark { background:var(--mark); color:inherit; padding:0 1px; border-radius:2px; }
.split { color:var(--split); font-weight:700; padding:0 2px; }
.ex { background:var(--card); border:1px solid var(--line); border-radius:6px; padding:8px 10px; margin:6px 0; }
.ex div { margin:2px 0; } .who { display:inline-block; min-width:92px; color:var(--muted); font-size:13px; }
.err { color:var(--bad); } .badge { font-size:12px; color:var(--ok); white-space:nowrap; }
details { margin:4px 0 12px; } summary { cursor:pointer; }
"""


def _ranked(findings: Dict[Tuple[str, str], Finding]) -> List[Finding]:
    return sorted(findings.values(), key=lambda f: (-len(f.segments), -f.count, f.category, f.detail))


def render_report(results: List[FileResult], started: float) -> str:
    out = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           '<title>Filter comparison</title>', f'<style>{_CSS}</style></head><body>',
           '<h1>Filter comparison: Workbench vs Trados and memoQ</h1>',
           f'<p class="muted">{time.strftime("%Y-%m-%d %H:%M")} · {len(results)} source file(s) · '
           f'took {time.time() - started:.0f} s. Workbench was run with its default import options: '
           + _esc(', '.join(f'{k}={v}' for k, v in DEFAULT_IMPORT_OPTIONS.items()
                            if k.startswith('word_'))) + '.</p>']

    # Overall ranking
    overall: Dict[Tuple[str, str, str], List] = {}
    for idx, r in enumerate(results):
        for tool, fs in r.findings.items():
            for f in fs.values():
                row = overall.setdefault((tool, f.category, f.detail), [0, 0, []])
                row[0] += len(f.segments)
                row[1] += f.count
                row[2].append(idx)
    out.append('<h2>What differs most, across all files</h2>')
    if overall:
        out.append('<p class="muted">Ranked by the number of segments affected. '
                   '“Both” means Trados and memoQ agree with each other and differ from Workbench, '
                   'which makes Workbench the odd one out.</p>')
        out.append('<table><tr><th>Difference</th><th>Segments</th><th>Times</th><th>Files</th></tr>')
        for (tool, cat, det), (segs, count, files) in sorted(
                overall.items(), key=lambda kv: (-kv[1][0], -kv[1][1], kv[0])):
            other = MEMOQ if tool == TRADOS else TRADOS
            both = (other, cat, det) in overall and cat not in ('unaligned', 'moved')
            links = ' '.join(f'<a href="#f{i}">{i + 1}</a>' for i in sorted(set(files)))
            badge = ' <span class="badge">both</span>' if both else ''
            out.append(f'<tr><td>{_esc(describe(tool, cat, det))}{badge}</td>'
                       f'<td class="n">{segs}</td><td class="n">{count}</td><td class="files">{links}</td></tr>')
        out.append('</table>')
    else:
        out.append('<p>No differences found (or nothing could be compared - see below).</p>')

    # Summary table
    out.append('<h2>Files</h2><table><tr><th>#</th><th>File</th><th>Workbench</th>'
               '<th>Trados</th><th>memoQ</th><th>Status</th></tr>')
    for idx, r in enumerate(results):
        cells = []
        for tool in (WORKBENCH, TRADOS, MEMOQ):
            ex = r.extractions.get(tool)
            cells.append(f'{_stats(ex)[0]} segments' if ex else '—')
        status = (f'<span class="err">{len(r.errors)} problem(s)</span>' if r.errors
                  else f'{sum(f.count for fs in r.findings.values() for f in fs.values())} differences')
        out.append(f'<tr><td class="n"><a href="#f{idx}">{idx + 1}</a></td><td>{_esc(r.job.source)}</td>'
                   + ''.join(f'<td>{c}</td>' for c in cells) + f'<td>{status}</td></tr>')
    out.append('</table>')

    # Per file
    for idx, r in enumerate(results):
        out.append(f'<h2 id="f{idx}">{idx + 1}. {_esc(Path(r.job.source).name)}</h2>')
        out.append(f'<p class="muted">{_esc(r.job.source)}</p>')
        for e in r.errors:
            out.append(f'<p class="err">{_esc(e)}</p>')
        for n in r.notes:
            out.append(f'<p class="muted">{_esc(n)}</p>')
        if r.extractions:
            out.append('<table><tr><th>Program</th><th>File read</th><th>Segments</th><th>Words</th>'
                       '<th>Formatting tags</th><th>Placeholder tags</th></tr>')
            for tool in (WORKBENCH, TRADOS, MEMOQ):
                ex = r.extractions.get(tool)
                if ex:
                    s, w, p, ph = _stats(ex)
                    out.append(f'<tr><td>{tool}</td><td class="muted">{_esc(ex.path)}</td>'
                               f'<td class="n">{s}</td><td class="n">{w}</td>'
                               f'<td class="n">{p}</td><td class="n">{ph}</td></tr>')
            out.append('</table>')
        for tool, fs in r.findings.items():
            out.append(f'<h3>Compared with {tool}</h3>')
            if not fs:
                out.append(f'<p>No differences: Workbench and {tool} extracted the same text, '
                           'segments and tags.</p>')
                continue
            for f in _ranked(fs):
                out.append(f'<details><summary>{_esc(describe(tool, f.category, f.detail))} '
                           f'<span class="muted">- {len(f.segments)} segment(s), {f.count} time(s)</span>'
                           '</summary>')
                for ref_html, wb_html in f.examples:
                    out.append('<div class="ex">')
                    if ref_html:
                        out.append(f'<div><span class="who">{tool}</span>{ref_html}</div>')
                    if wb_html:
                        out.append(f'<div><span class="who">Workbench</span>{wb_html}</div>')
                    out.append('</div>')
                if f.count > len(f.examples):
                    out.append(f'<p class="more">Showing {len(f.examples)} of {f.count}.</p>')
                out.append('</details>')
    out.append('</body></html>')
    return '\n'.join(out)


# ═══════════════════════════════════════════════════════════════════
#  Command line
# ═══════════════════════════════════════════════════════════════════

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('path', help='a source file, or a folder to search for source files')
    ap.add_argument('--trados', help='the SDLXLIFF Trados made from the source file')
    ap.add_argument('--memoq', help='the MQXLIFF memoQ made from the source file')
    ap.add_argument('--out', help='where to write the HTML report')
    ap.add_argument('--source-lang', help='source language, if the SDLXLIFF/MQXLIFF does not say')
    args = ap.parse_args(argv)

    target = Path(args.path).resolve()
    if target.is_dir():
        if args.trados or args.memoq:
            ap.error('--trados/--memoq go with a single source file, not a folder')
        jobs = find_jobs(target)
        out_path = Path(args.out) if args.out else target / 'filter-compare-report.html'
    elif target.is_file():
        found = find_jobs(target.parent) if not (args.trados or args.memoq) else []
        auto = next((j for j in found if Path(j.source) == target), Job(str(target)))
        jobs = [Job(str(target),
                    str(Path(args.trados).resolve()) if args.trados else auto.trados,
                    str(Path(args.memoq).resolve()) if args.memoq else auto.memoq)]
        out_path = Path(args.out) if args.out else target.parent / 'filter-compare-report.html'
    else:
        print(f"Not found: {target}")
        return 2
    if not jobs:
        print(f"No source files ({', '.join(sorted(SOURCE_EXTS))}) found in {target}")
        return 2

    from modules.okapi_sidecar import OkapiSidecar
    sidecar = OkapiSidecar()
    started = time.time()
    try:
        if not sidecar.start():
            print("The Okapi sidecar (Workbench's file-filter engine) could not be started, "
                  "so there is nothing to compare Trados and memoQ against.\n"
                  f"Looked in: {sidecar.sidecar_dir}")
            return 2
        results = []
        for n, job in enumerate(jobs, 1):
            print(f"[{n}/{len(jobs)}] {job.source}", flush=True)
            res = run_job(job, sidecar, args.source_lang)
            for e in res.errors:
                print(f"    problem: {e}")
            for tool, fs in res.findings.items():
                print(f"    vs {tool}: {sum(f.count for f in fs.values())} differences")
            results.append(res)
    finally:
        sidecar.stop()

    out_path = out_path.resolve()
    out_path.write_text(render_report(results, started), encoding='utf-8')
    print(f"\nReport: {out_path}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
