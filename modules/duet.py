"""
Duet: two AI models review a translation prompt together (issue #242, tier 1)
==============================================================================

Bouncing a prompt between two different models catches problems neither finds
alone – but two models left to chat politely agree far too soon. The protocol
here (from the ``duet.py`` prototype) keeps them honest:

- every turn must check the other model's claims against the attached material
  and quote the evidence before accepting them;
- every turn keeps a numbered **OPEN ISSUES** register, and an issue is closed
  only by stating its resolution;
- every turn ends with exactly one line, ``VERDICT: CONTINUE`` or
  ``VERDICT: AGREED``; AGREED is allowed only with an empty register;
- consensus is both models saying AGREED one after the other. A synthesis turn
  then writes the deliverable between ``===FINAL DELIVERABLE===`` and
  ``===END FINAL DELIVERABLE===``, followed by an *Unresolved (for the user to
  decide)* section;
- at the round limit the synthesis is forced, and the disagreements are listed
  under Unresolved instead of being resolved.

The engine has no Qt and no provider code: a participant is any callable
``ask(system_prompt, prompt, max_tokens) -> (text, usage)``, so Workbench,
the MCP server or the Trados side can drive it with their own LLM clients.
The transcript is written to disk after every turn, so a crash loses nothing.
"""

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

START_MARKER = "===FINAL DELIVERABLE==="
END_MARKER = "===END FINAL DELIVERABLE==="
UNRESOLVED_HEADING = "Unresolved (for the user to decide)"

_VERDICT_RE = re.compile(r"^\W*VERDICT\s*:\s*(CONTINUE|AGREED)\b", re.IGNORECASE | re.MULTILINE)

Ask = Callable[[str, str, int], Tuple[str, Dict]]


@dataclass
class Participant:
    label: str          # e.g. "Claude (claude-sonnet-5-5)"
    provider: str       # e.g. "claude"
    model: str
    ask: Ask


@dataclass
class Turn:
    round: int
    speaker: str        # "A", "B" or "synthesis"
    label: str
    text: str
    verdict: Optional[str] = None
    usage: Dict = field(default_factory=dict)


@dataclass
class DuetResult:
    turns: List[Turn]
    consensus: bool
    rounds: int
    deliverable: str
    unresolved: str
    transcript_path: Optional[str]
    stopped: bool = False


PROTOCOL = f"""You are one of two AI reviewers working together on the artefact below. The other reviewer is a different AI model. A human translator will read the whole exchange and decide.

Rules for every turn:
1. Check every claim the other reviewer made against the attached material before you accept it, and quote the evidence. Reject what the material does not support.
2. Raise substantive problems only: errors, ambiguities, missing instructions, contradictions, things that would make translations worse. No praise, no restating.
3. Keep a numbered register headed "OPEN ISSUES". Carry over every unresolved issue, add new ones, and close an issue only by writing its resolution next to it.
4. Never change terminology that the attached TM pairs or glossary confirm, unless you quote evidence that it is wrong.
5. End your turn with exactly one line, the last line: "VERDICT: CONTINUE" or "VERDICT: AGREED". You may only say AGREED when your OPEN ISSUES register is empty, and only after at least three substantive problems have been raised and resolved in this review."""

SYNTHESIS = f"""You now write the final result of this review.

Write the complete improved artefact – not a list of changes – between a line containing only {START_MARKER} and a line containing only {END_MARKER}.
After the end marker, add a section headed "## {UNRESOLVED_HEADING}" listing every point the reviewers did not agree on, or "None." if there are none. Do not add a VERDICT line."""

FORCED = ("The round limit has been reached without agreement. Keep every change both "
          "reviewers accepted, and list every remaining disagreement under Unresolved.")


def parse_verdict(text: str) -> Optional[str]:
    """``"AGREED"``, ``"CONTINUE"`` or None – the last VERDICT line counts."""
    found = _VERDICT_RE.findall(text or "")
    return found[-1].upper() if found else None


def extract_deliverable(text: str) -> Tuple[str, str]:
    """``(deliverable, unresolved)`` from a synthesis turn.

    Without the markers the whole text is the deliverable (better than losing
    it); without an Unresolved section, ``unresolved`` is empty.
    """
    text = text or ""
    start = text.find(START_MARKER)
    if start < 0:
        return text.strip(), ""
    body_start = start + len(START_MARKER)
    end = text.find(END_MARKER, body_start)
    deliverable = (text[body_start:end] if end >= 0 else text[body_start:]).strip()
    rest = text[end + len(END_MARKER):] if end >= 0 else ""
    m = re.search(r"^#*\s*" + re.escape(UNRESOLVED_HEADING) + r"\s*:?\s*$", rest,
                  re.IGNORECASE | re.MULTILINE)
    unresolved = rest[m.end():].strip() if m else rest.strip()
    return deliverable, unresolved


def build_brief(artefact_name: str, artefact: str, material: str, task: str = "") -> str:
    """The opening message both reviewers see: task, material and artefact."""
    task = task or ("Review this translation prompt and improve it, so that an AI "
                    "translating this project with it produces better translations.")
    parts = [f"# Task\n{task}"]
    if material.strip():
        parts.append(f"# Attached material\n{material.strip()}")
    parts.append(f"# The artefact: {artefact_name}\n\n{artefact.strip()}")
    return "\n\n".join(parts)


def _turn_prompt(brief: str, turns: List[Turn], label: str) -> str:
    out = [brief, "\n# Review so far"]
    if not turns:
        out.append("(none yet – you open the review)")
    for t in turns:
        out.append(f"\n## Round {t.round} – {t.label}\n\n{t.text.strip()}")
    out.append(f"\n# Your turn ({label})")
    return "\n".join(out)


class Transcript:
    """Markdown transcript, written to disk after every turn."""

    def __init__(self, path: Optional[str], header: str):
        self.path = path
        self._parts = [header]
        self._flush()

    def add(self, text: str):
        self._parts.append(text)
        self._flush()

    def _flush(self):
        if not self.path:
            return
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n\n".join(self._parts).rstrip() + "\n")


def _call_with_retry(ask: Ask, system: str, prompt: str, max_tokens: int,
                     attempts: int, backoff: float, sleep: Callable[[float], None],
                     on_retry: Optional[Callable[[int, Exception], None]]) -> Tuple[str, Dict]:
    for attempt in range(1, attempts + 1):
        try:
            text, usage = ask(system, prompt, max_tokens)
            return text or "", usage or {}
        except Exception as exc:
            if attempt == attempts:
                raise
            if on_retry:
                on_retry(attempt, exc)
            sleep(backoff * attempt)
    raise RuntimeError("unreachable")


def run_duet(a: Participant, b: Participant, brief: str, *,
             max_rounds: int = 4, max_tokens: int = 4000,
             opener: str = "A", synthesiser: str = "A",
             transcript_path: Optional[str] = None,
             on_turn: Optional[Callable[[Turn], None]] = None,
             should_stop: Optional[Callable[[], bool]] = None,
             attempts: int = 3, backoff: float = 15.0,
             sleep: Callable[[float], None] = time.sleep,
             on_retry: Optional[Callable[[int, Exception], None]] = None) -> DuetResult:
    """Run the review and return its result. See the module docstring."""
    speakers = {"A": a, "B": b}
    order = ["A", "B"] if opener.upper() != "B" else ["B", "A"]
    header = "\n".join([
        f"# Duet review – {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"- **Model A:** {a.label}",
        f"- **Model B:** {b.label}",
        f"- **Opens:** {speakers[order[0]].label}",
        f"- **Synthesises:** {speakers[synthesiser.upper() if synthesiser.upper() in speakers else 'A'].label}",
        f"- **Round limit:** {max_rounds}; at most {max_tokens} output tokens per turn",
        "",
        "<details><summary>Brief sent to both models</summary>",
        "",
        brief,
        "",
        "</details>",
    ])
    transcript = Transcript(transcript_path, header)
    turns: List[Turn] = []
    consensus, stopped = False, False
    last_verdicts: List[Tuple[str, Optional[str]]] = []

    rounds_done = 0
    for rnd in range(1, max_rounds + 1):
        for key in order:
            if should_stop and should_stop():
                stopped = True
                break
            who = speakers[key]
            text, usage = _call_with_retry(
                who.ask, PROTOCOL, _turn_prompt(brief, turns, f"{who.label}, reviewer {key}"),
                max_tokens, attempts, backoff, sleep, on_retry)
            turn = Turn(rnd, key, who.label, text, parse_verdict(text), usage)
            turns.append(turn)
            transcript.add(f"## Round {rnd} – {who.label}\n\n{text.strip()}"
                           + (f"\n\n<sub>{_usage_line(usage)}</sub>" if usage else ""))
            if on_turn:
                on_turn(turn)
            last_verdicts.append((key, turn.verdict))
            # Consensus: both reviewers AGREED, one right after the other
            if (len(last_verdicts) >= 2 and last_verdicts[-1][1] == "AGREED"
                    and last_verdicts[-2][1] == "AGREED"
                    and last_verdicts[-1][0] != last_verdicts[-2][0]):
                consensus = True
                break
        rounds_done = rnd
        if consensus or stopped:
            break

    if stopped:
        transcript.add("_Stopped by the user before the synthesis._")
        return DuetResult(turns, False, rounds_done, "", "", transcript_path, stopped=True)

    synth_key = synthesiser.upper() if synthesiser.upper() in speakers else "A"
    who = speakers[synth_key]
    instruction = SYNTHESIS if consensus else SYNTHESIS + "\n\n" + FORCED
    prompt = _turn_prompt(brief, turns, f"{who.label}, synthesis") + "\n\n" + instruction
    text, usage = _call_with_retry(who.ask, PROTOCOL, prompt, max(max_tokens, 8000),
                                   attempts, backoff, sleep, on_retry)
    deliverable, unresolved = extract_deliverable(text)
    turn = Turn(rounds_done, "synthesis", who.label, text, None, usage)
    turns.append(turn)
    transcript.add(f"## Synthesis – {who.label}"
                   f" ({'consensus' if consensus else 'round limit reached, no consensus'})"
                   f"\n\n{text.strip()}")
    if on_turn:
        on_turn(turn)
    return DuetResult(turns, consensus, rounds_done, deliverable, unresolved, transcript_path)


def _usage_line(usage: Dict) -> str:
    inp = usage.get("input_tokens") or usage.get("prompt_tokens")
    out = usage.get("output_tokens") or usage.get("completion_tokens")
    return f"{inp or '?'} tokens in, {out or '?'} out"


def estimate_tokens(brief_chars: int, max_rounds: int, max_tokens: int,
                    avg_output_tokens: int = 1200) -> Tuple[List[int], List[int]]:
    """Rough input and output tokens per turn, A and B alternating, plus the
    synthesis. The whole review so far is re-sent every turn, so input grows
    with every turn – the cost grows roughly with the square of the rounds."""
    out_per_turn = min(avg_output_tokens, max_tokens)
    base = (brief_chars + len(PROTOCOL)) // 4 + 50
    inputs, outputs = [], []
    for i in range(2 * max_rounds):
        inputs.append(base + i * out_per_turn)
        outputs.append(out_per_turn)
    inputs.append(base + 2 * max_rounds * out_per_turn + len(SYNTHESIS) // 4)
    outputs.append(max(out_per_turn, brief_chars // 4))
    return inputs, outputs


def estimate_cost(a: Participant, b: Participant, brief_chars: int, max_rounds: int,
                  max_tokens: int, opener: str = "A", synthesiser: str = "A",
                  price: Optional[Callable[[str, str, int, int], Optional[float]]] = None) -> Dict:
    """Tokens and (when ``price`` knows the models) USD cost of a full review
    that runs to the round limit – the worst case."""
    inputs, outputs = estimate_tokens(brief_chars, max_rounds, max_tokens)
    order = [a, b] if opener.upper() != "B" else [b, a]
    who = [order[i % 2] for i in range(2 * max_rounds)] + [a if synthesiser.upper() != "B" else b]
    total, known = 0.0, True
    for p, i, o in zip(who, inputs, outputs):
        cost = price(p.provider, p.model, i, o) if price else None
        if cost is None:
            known = False
        else:
            total += cost
    return {"turns": len(who), "input_tokens": sum(inputs), "output_tokens": sum(outputs),
            "cost": total if known else None, "brief_chars": brief_chars}


def next_version_name(stem: str, existing: List[str]) -> str:
    """``"Patent prompt (duet v2)"``, or the next free number. A name that
    already ends in "(duet vN)" continues that numbering."""
    m = re.match(r"^(.*?)\s*\(duet v(\d+)\)$", stem.strip())
    base, start = (m.group(1), int(m.group(2)) + 1) if m else (stem.strip(), 2)
    taken = {e.lower() for e in existing}
    n = start
    while f"{base} (duet v{n})".lower() in taken:
        n += 1
    return f"{base} (duet v{n})"
