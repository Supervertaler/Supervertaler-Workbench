"""
Cross-model review (issue #242, tier 2)
=======================================

The translate–edit–proofread pattern with two AI models. Model A translated
the segments under the project's prompt; model B, a *different* model, checks
each translation against its source and against the same instructions and
glossary, and answers per segment with either ``PASS`` or a flag:

    [12] PASS
    [13] ⟦XR: "luminance coefficient" is a different CIE quantity → "luminance factor"⟧

A flag is a review comment, not an edit: the reviewer never changes a
translation. Workbench keeps the flags out of band, as proofreading comments
keyed ``"XR · <model>"``, so they never end up in an exported file. The XR
origin keeps them apart from the translator's own ⟦TC: …⟧ comments (see the
AutoPrompt methodology) and from ordinary AI proofreading, so they can be
filtered by origin.

No Qt and no provider code here: the reviewer is any callable
``ask(system_prompt, prompt, max_tokens) -> (text, usage)``, as in duet.py.
"""

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from modules.duet import _call_with_retry

TC = "TC"   # translator's comment, written by the model that translated
XR = "XR"   # cross-review flag, written by the reviewing model
PROOFREADING = "proofreading"
KEY_SEPARATOR = " · "

_MARKER_RE = re.compile(r"[ \t]*⟦\s*(TC|XR)\s*:\s*(.*?)\s*⟧", re.DOTALL)
_HEADER_RE = re.compile(r"^[\s>*_#-]*\[\s*(?:segment\s*)?#?\s*(\d+)\s*\]", re.IGNORECASE | re.MULTILINE)
_FLAG_RE = re.compile(r"⟦\s*XR\s*:\s*(.*?)\s*⟧", re.DOTALL)
_PASS_RE = re.compile(r"^\W*(?:PASS\b|✓)", re.IGNORECASE)
_ECHO_RE = re.compile(r"^\W*(?:source|translation|target)\s*:", re.IGNORECASE)

Ask = Callable[[str, str, int], Tuple[str, Dict]]


@dataclass
class Item:
    id: int
    source: str
    target: str


@dataclass
class ReviewResult:
    flags: Dict[int, str] = field(default_factory=dict)   # segment id → flag
    passed: List[int] = field(default_factory=list)
    missing: List[int] = field(default_factory=list)      # no answer, even after a second try
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    stopped: bool = False


SYSTEM = """You are the reviewer in a translate–edit–proofread workflow. Another AI model translated the segments below from {source_lang} to {target_lang}, following the translation instructions and glossary shown. You did not translate them: your job is to check them.

Compare every translation with its source and with those instructions, and look for:
- meaning errors, omissions and additions;
- terminology that contradicts the glossary or the instructions;
- numbers, names, references and tags that differ from the source;
- grammar and spelling errors;
- instructions in the translation prompt that the translation does not follow.

Do not flag preferences or alternative wordings that are equally correct. A translator comment in the form ⟦TC: …⟧ explains a correction the translator made deliberately: check that the correction is right, but don't flag the marker itself.

Answer with one line per segment, in the order given, and nothing else:
[N] PASS
[N] ⟦XR: what is wrong → suggested fix⟧
Use at most one ⟦XR: …⟧ per segment; join several problems with semicolons. Use the characters ⟦ and ⟧ exactly as shown."""


def split_markers(text: str) -> Tuple[str, List[Tuple[str, str]]]:
    """The text without its ⟦TC: …⟧ / ⟦XR: …⟧ markers, and the markers as
    ``(kind, body)``. Text without markers comes back unchanged."""
    text = text or ""
    markers = [(m.group(1), m.group(2).strip()) for m in _MARKER_RE.finditer(text)]
    if not markers:
        return text, []
    return _MARKER_RE.sub("", text).rstrip(), markers


def note_key(kind: str, model: str) -> str:
    """Key of a review comment in ``Segment.proofreading_notes``."""
    return f"{kind}{KEY_SEPARATOR}{model}"


def origin_of(key: str) -> str:
    """``TC``, ``XR`` or ``proofreading`` (plain AI proofreading, keyed by the
    model name alone)."""
    for kind in (TC, XR):
        if (key or "").startswith(kind + KEY_SEPARATOR):
            return kind
    return PROOFREADING


def make_batches(items: Sequence[Item], max_segments: int = 20, max_chars: int = 12000) -> List[List[Item]]:
    """Consecutive batches of at most ``max_segments`` segments and (unless a
    single segment is longer) ``max_chars`` characters of source + target."""
    batches, current, size = [], [], 0
    for item in items:
        n = len(item.source) + len(item.target)
        if current and (len(current) >= max_segments or size + n > max_chars):
            batches.append(current)
            current, size = [], 0
        current.append(item)
        size += n
    if current:
        batches.append(current)
    return batches


def _terms_block(terms: Sequence[Dict]) -> str:
    lines = []
    for t in terms or []:
        s, tt = (t.get("source_term") or "").strip(), (t.get("target_term") or "").strip()
        if s and tt:
            lines.append(f"- {s} → ⚠️ DO NOT USE: {tt}" if t.get("forbidden") else f"- {s} → {tt}")
    return "\n".join(lines)


def build_prompt(batch: Sequence[Item], instructions: str = "", terms: Sequence[Dict] = ()) -> str:
    parts = ["# Translation instructions the translator followed\n"
             + (instructions.strip() or "(None beyond a faithful, fluent translation.)")]
    glossary = _terms_block(terms)
    if glossary:
        parts.append("# Glossary (approved terms)\n" + glossary)
    segs = [f"[{it.id}]\nSource: {it.source.strip()}\nTranslation: {it.target.strip()}" for it in batch]
    parts.append("# Segments to review\n\n" + "\n\n".join(segs))
    return "\n\n".join(parts)


def parse_review(response: str, ids: Sequence[int]) -> Dict[int, Tuple[str, str]]:
    """``{id: ("pass", "") or ("flag", text)}`` for the requested ids the
    reviewer answered. An answer without a ⟦XR⟧ marker that isn't PASS still
    counts as a flag (models don't always keep to the format); ids without
    an answer are left out."""
    wanted = {int(i) for i in ids}
    heads = [(m.start(), m.end(), int(m.group(1))) for m in _HEADER_RE.finditer(response or "")]
    out: Dict[int, Tuple[str, str]] = {}
    for n, (_start, end, seg_id) in enumerate(heads):
        if seg_id not in wanted or seg_id in out:
            continue
        chunk = response[end:heads[n + 1][0] if n + 1 < len(heads) else len(response)]
        flag = _FLAG_RE.search(chunk)
        if flag:
            out[seg_id] = ("flag", re.sub(r"\s+", " ", flag.group(1)).strip())
            continue
        # Without a marker: drop echoed Source/Translation lines, then PASS or a flag
        lines = [l.strip() for l in chunk.splitlines()
                 if l.strip() and not _ECHO_RE.match(l)]
        if not lines:
            continue
        if any(_PASS_RE.match(l) for l in lines):
            out[seg_id] = ("pass", "")
        else:
            text = re.sub(r"^\W*(?:FLAG|ISSUE)\s*:?\s*", "", " ".join(lines), flags=re.IGNORECASE)
            out[seg_id] = ("flag", re.sub(r"\s+", " ", text).strip())
    return out


def run_review(items: Sequence[Item], ask: Ask, *, source_lang: str, target_lang: str,
               instructions: str = "", terms: Sequence[Dict] = (),
               filter_terms: Optional[Callable[[List[Dict], str], List[Dict]]] = None,
               max_segments: int = 20, max_chars: int = 12000, max_tokens: int = 4000,
               on_batch: Optional[Callable[[Dict[int, Tuple[str, str]], int, int], None]] = None,
               should_stop: Optional[Callable[[], bool]] = None,
               attempts: int = 3, backoff: float = 15.0,
               sleep: Callable[[float], None] = time.sleep,
               on_retry: Optional[Callable[[int, Exception], None]] = None) -> ReviewResult:
    """Review ``items`` batch by batch. ``on_batch(answers, done, total)`` is
    called after every batch, so a stopped or failed review keeps what it got.
    Segments a batch's answer skipped get one more try, in batches of their own.
    ``filter_terms(terms, text)`` keeps the glossary terms relevant to a batch
    (default: all of them)."""
    system = SYSTEM.format(source_lang=source_lang or "the source language",
                           target_lang=target_lang or "the target language")
    result = ReviewResult()
    total = len(items)
    done = 0

    def review(batches: List[List[Item]], final: bool) -> List[Item]:
        nonlocal done
        skipped: List[Item] = []
        for batch in batches:
            if should_stop and should_stop():
                result.stopped = True
                return []
            text_for_terms = "\n".join(it.source for it in batch)
            batch_terms = filter_terms(list(terms), text_for_terms) if (filter_terms and terms) else list(terms or [])
            prompt = build_prompt(batch, instructions, batch_terms)
            text, usage = _call_with_retry(ask, system, prompt, max_tokens, attempts, backoff, sleep, on_retry)
            result.calls += 1
            result.input_tokens += int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            result.output_tokens += int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            answers = parse_review(text, [it.id for it in batch])
            for seg_id, (verdict, flag) in answers.items():
                if verdict == "flag":
                    result.flags[seg_id] = flag
                else:
                    result.passed.append(seg_id)
            unanswered = [it for it in batch if it.id not in answers]
            if final:
                result.missing.extend(it.id for it in unanswered)
                done += len(batch)
            else:
                skipped.extend(unanswered)
                done += len(batch) - len(unanswered)
            if on_batch:
                on_batch(answers, done, total)
        return skipped

    skipped = review(make_batches(items, max_segments, max_chars), final=False)
    if skipped and not result.stopped:
        review(make_batches(skipped, max(1, max_segments // 4), max_chars), final=True)
    return result


def estimate(items: Sequence[Item], instructions: str = "", terms_chars: int = 0,
             max_segments: int = 20, max_chars: int = 12000) -> Dict:
    """Calls and tokens of a review (about 4 characters per token). Most
    answers are a short PASS line; a flag is a sentence or two."""
    batches = make_batches(items, max_segments, max_chars)
    fixed = len(SYSTEM) + len(instructions or "") + min(terms_chars, 6000) + 200
    chars_in = sum(fixed + sum(len(it.source) + len(it.target) + 40 for it in b) for b in batches)
    return {"calls": len(batches), "input_tokens": chars_in // 4,
            "output_tokens": sum(len(b) for b in batches) * 30}


def write_report(path: str, result: ReviewResult, items: Sequence[Item], *, reviewer: str,
                 translator: str = "", prompt_name: str = "", project_name: str = "") -> None:
    """A Markdown record of the review: who reviewed what, and every flag."""
    by_id = {it.id: it for it in items}
    lines = [f"# Cross-model review – {datetime.now():%Y-%m-%d %H:%M}", ""]
    if project_name:
        lines.append(f"- **Project:** {project_name}")
    if translator:
        lines.append(f"- **Translated by:** {translator}")
    lines.append(f"- **Reviewed by:** {reviewer}")
    lines.append(f"- **Translation prompt:** {prompt_name or '(none)'}")
    lines.append(f"- **Segments:** {len(items)} – {len(result.flags)} flagged, {len(result.passed)} passed"
                 + (f", {len(result.missing)} not answered" if result.missing else "")
                 + (" (stopped before the end)" if result.stopped else ""))
    lines.append("")
    lines.append("## Flags" if result.flags else "No flags.")
    for seg_id in sorted(result.flags):
        it = by_id.get(seg_id)
        lines += ["", f"### Segment {seg_id}"]
        if it:
            lines += [f"- **Source:** {it.source.strip()}", f"- **Translation:** {it.target.strip()}"]
        lines.append(f"- **⟦XR⟧** {result.flags[seg_id]}")
    if result.missing:
        lines += ["", "## Not answered", "", ", ".join(str(i) for i in sorted(result.missing))]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines).rstrip() + "\n")
