"""
Simple Segmenter
Sentence segmentation for the text Supervertaler splits itself (plain-text and
Markdown imports, pasted text), extended with the user's segmentation rules
from Settings → Segmentation Rules (issue #191).

Segments are always slices of the input with only the whitespace at each cut
trimmed – no characters are ever dropped – and ``segment_with_separators``
returns that whitespace too, so a plain-text export can rebuild each line
exactly (a custom rule may split where there is no space at all).
"""

import re
from typing import List, Optional, Tuple

try:
    from modules.segmentation_rules import SegmentationRules, compile_rules, rule_positions
except ImportError:  # run as a script from modules/
    from segmentation_rules import SegmentationRules, compile_rules, rule_positions

# Sentence-final punctuation (and any closing quote or bracket after it),
# followed by whitespace; group 1 is the first character after the whitespace.
_TERMINATOR = re.compile(r'[.!?]+["\'\u201d\u2019\u00bb)\]]*(?=\s+(\S))')
# Characters that may open a new sentence besides a capital letter.
_OPENERS = '"\'\u201c\u2018\u201e\u00ab\u00bf\u00a1'
_LEADING_PUNCTUATION = '([{"\'\u201c\u2018\u201e\u00ab\u00bf\u00a1'


class SimpleSegmenter:
    """Rule-based sentence segmenter: the user's custom rules first, then the
    built-in sentence rules."""

    def __init__(self, rules: Optional[SegmentationRules] = None):
        # Common abbreviations: a full stop after these ends a sentence only
        # when a reasonably long sentence follows.
        self.abbreviations = {
            'mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr',
            'inc', 'ltd', 'co', 'corp', 'fig', 'figs',
            'etc', 'vs', 'e.g', 'i.e', 'cf', 'approx', 'ca',
            'no', 'nos', 'vol', 'p', 'pp', 'art', 'op'
        }
        # Titles are always followed by a name, never by a new sentence
        # ("Ms" only capitalised: lower-case "ms." is milliseconds).
        self.title_abbreviations = {'mr', 'mrs', 'dr', 'prof'}
        self.rules = rules or SegmentationRules()
        self.never_break_after = self.title_abbreviations | set(self.rules.extra_abbreviations)
        self._compiled = compile_rules(self.rules.rules)

    def segment_text(self, text: str) -> List[str]:
        """
        Segment text into sentences

        Returns: List of sentences
        """
        return [segment for segment, _ in self.segment_with_separators(text)]

    def segment_with_separators(self, text: str) -> List[Tuple[str, str]]:
        """``[(segment, whitespace before it)]`` – the first segment's
        separator is empty; joining ``separator + segment`` rebuilds the
        text apart from whitespace at its very start and end."""
        if not text or not text.strip():
            return []
        if self.rules.split_at_line_breaks:
            out: List[Tuple[str, str]] = []
            breaks = ''       # line breaks since the last segment
            for line in re.split(r'\r\n|\r|\n', text):
                for n, (segment, separator) in enumerate(self._split_line(line)):
                    out.append((segment, (breaks if out else '') if n == 0 else separator))
                    breaks = ''
                breaks += '\n'
            return out
        # Line breaks inside the text are treated as spaces.
        return self._split_line(text.replace('\r', '').replace('\n', ' '))

    # ── internals ──────────────────────────────────────────────────────

    def _split_line(self, text: str) -> List[Tuple[str, str]]:
        if not text.strip():
            return []
        decided = {}          # position → (is_break, soft)
        for pattern, is_break in self._compiled:
            for pos in rule_positions(pattern, text):
                decided.setdefault(self._settle(text, pos), (is_break, False))
        if self.rules.use_builtin_rules:
            for pos, is_break, soft in self._builtin_positions(text):
                decided.setdefault(pos, (is_break, soft))

        breaks = sorted(p for p, (is_break, _) in decided.items()
                        if is_break and 0 < p < len(text))
        kept = []
        for i, pos in enumerate(breaks):
            if decided[pos][1]:
                following = text[pos:breaks[i + 1] if i + 1 < len(breaks) else len(text)].strip()
                if len(following) < 10 or following[:1].islower():
                    continue
            kept.append(pos)

        out: List[Tuple[str, str]] = []
        pending = ''
        for start, end in zip([0] + kept, kept + [len(text)]):
            piece = text[start:end]
            core = piece.strip()
            if not core:
                pending += piece
                continue
            lead = piece[:len(piece) - len(piece.lstrip())]
            out.append((core, (pending + lead) if out else ''))
            pending = piece[len(piece.rstrip()):]
        return out

    @staticmethod
    def _settle(text: str, pos: int) -> int:
        """Move a break position left over any whitespace, so a rule that
        ends with the space and one that ends before it name the same cut."""
        while pos > 0 and text[pos - 1].isspace():
            pos -= 1
        return pos

    def _builtin_positions(self, text: str):
        """``(position, is_break, soft)`` for every candidate sentence end."""
        for m in _TERMINATOR.finditer(text):
            nxt = m.group(1)
            if not (nxt.isupper() or nxt in _OPENERS):
                continue
            if m.start() > 0 and text[m.start() - 1] in '([':
                continue          # "(?)", "(!)" – a remark, not a sentence end
            if text[m.start():m.end()].rstrip(_OPENERS + '\u201d\u2019\u00bb)]') == '.':
                k = m.start()
                while k > 0 and not text[k - 1].isspace():
                    k -= 1
                word = text[k:m.start()].lstrip(_LEADING_PUNCTUATION)
                if word.lower() in self.never_break_after or word == 'Ms':
                    yield m.end(), False, False
                    continue
                if word.lower() in self.abbreviations:
                    yield m.end(), True, True
                    continue
            yield m.end(), True, False

    def segment_paragraphs(self, paragraphs: List[str]) -> List[tuple]:
        """
        Segment a list of paragraphs, tracking which paragraph each segment belongs to
        
        Returns: List of (paragraph_index, segment_text) tuples
        """
        all_segments = []
        
        for para_idx, paragraph in enumerate(paragraphs):
            if not paragraph.strip():
                continue
                
            segments = self.segment_text(paragraph)
            for segment in segments:
                all_segments.append((para_idx, segment))
        
        return all_segments


def join_segments(parts: List[Tuple[str, Optional[str]]]) -> str:
    """Rebuild one line from ``[(text, separator before it)]`` for export.
    Empty texts are skipped; an unknown separator (``None``, e.g. a project
    created before separators were recorded) becomes a single space."""
    line = ''
    for text, separator in parts:
        if not text or not text.strip():
            continue
        if line:
            line += ' ' if separator is None else separator
        line += text
    return line


class MarkdownSegmenter(SimpleSegmenter):
    """Markdown-aware sentence segmenter.

    Protects markdown constructs (links, images, inline code, code spans,
    reference-style links, HTML tags) from being incorrectly split by the
    sentence boundary detector, then restores them after splitting.
    """

    # Patterns ordered from most specific to least specific to avoid
    # partial matches.  Each pattern is compiled once at class level.
    _MD_PATTERNS = [
        # Fenced code blocks (``` ... ```) – should not appear mid-line but
        # protect just in case (non-greedy across backticks)
        re.compile(r'```.*?```', re.DOTALL),
        # Inline code spans with double backticks (`` ... ``)
        re.compile(r'``[^`]+``'),
        # Inline code spans with single backticks (` ... `)
        re.compile(r'`[^`]+`'),
        # Images: ![alt](url "optional title")
        re.compile(r'!\[[^\]]*\]\([^)]+\)'),
        # Inline links: [text](url "optional title")
        re.compile(r'\[[^\]]*\]\([^)]+\)'),
        # Reference-style links/images: [text][ref] or ![alt][ref]
        re.compile(r'!?\[[^\]]*\]\[[^\]]*\]'),
        # Autolinks: <https://...> or <user@example.com>
        re.compile(r'<(?:https?://[^>]+|[^>]+@[^>]+)>'),
        # Bare URLs (http/https) – common in markdown even without angle brackets
        re.compile(r'https?://\S+'),
        # HTML tags: <tag attr="val"> or </tag> or <br/> etc.
        re.compile(r'</?[a-zA-Z][a-zA-Z0-9]*(?:\s+[^>]*)?>'),
    ]

    def segment_with_separators(self, text: str) -> list:
        """Segment text into sentences, protecting markdown constructs."""
        if not text or not text.strip():
            return []

        # Phase 1: Replace markdown constructs with placeholders
        placeholders = {}
        protected = text

        def _make_placeholder(match):
            idx = len(placeholders)
            key = f'\x00MD{idx}\x00'
            placeholders[key] = match.group(0)
            return key

        for pattern in self._MD_PATTERNS:
            protected = pattern.sub(_make_placeholder, protected)

        # Phase 2: Run normal sentence segmentation on protected text
        sentences = super().segment_with_separators(protected)

        # Phase 3: Restore placeholders in each sentence – newest first, as a
        # later construct can contain an earlier placeholder: [`code`](url)
        restored = []
        for sentence, separator in sentences:
            for key, original in reversed(list(placeholders.items())):
                sentence = sentence.replace(key, original)
            restored.append((sentence, separator))

        return restored


# Quick test
if __name__ == "__main__":
    print("=== SimpleSegmenter ===")
    segmenter = SimpleSegmenter()

    test_text = """
    This is a test sentence. This is another sentence!
    Dr. Smith works at Inc. Corp. The company has many employees.
    What about questions? They work too. And exclamations!
    """

    segments = segmenter.segment_text(test_text)
    print(f"Found {len(segments)} segments:")
    for i, seg in enumerate(segments, 1):
        print(f"  {i}. {seg}")

    print("\n=== MarkdownSegmenter ===")
    md_segmenter = MarkdownSegmenter()

    md_tests = [
        "See [the docs](https://example.com/page.html) for details. This is the next sentence.",
        "Use `str.split()` to tokenize. Then call `re.match()` to validate. Finally return the result.",
        "Check the ![logo](img/logo.png) image. It should render correctly.",
        "Visit https://example.com/path.html for more info. The site has good docs.",
        "Read the <a href=\"https://example.com\">documentation</a> first. Then try the examples.",
    ]

    for test in md_tests:
        print(f"\n  Input: {test}")
        segments = md_segmenter.segment_text(test)
        for i, seg in enumerate(segments, 1):
            print(f"    {i}. {seg}")
