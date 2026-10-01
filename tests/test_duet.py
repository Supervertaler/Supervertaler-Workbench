"""Duet review engine (issue #242, tier 1), with scripted fake models."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import duet


def scripted(label, replies, calls):
    replies = list(replies)

    def ask(system, prompt, max_tokens):
        calls.append((label, prompt, max_tokens))
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, {"input_tokens": len(prompt) // 4, "output_tokens": len(reply) // 4}
    return duet.Participant(label, "fake", label.lower(), ask)


FINAL = (f"Here it is.\n{duet.START_MARKER}\nTranslate patents into Dutch.\n"
         f"Use 'luminantiefactor' for luminance factor.\n{duet.END_MARKER}\n\n"
         f"## {duet.UNRESOLVED_HEADING}\n- Whether to keep British spelling.\n")


def test_verdict_and_deliverable_parsing():
    assert duet.parse_verdict("text\nVERDICT: AGREED") == "AGREED"
    assert duet.parse_verdict("VERDICT: CONTINUE\n...\n**VERDICT: agreed**") == "AGREED"
    assert duet.parse_verdict("no verdict here") is None
    deliverable, unresolved = duet.extract_deliverable(FINAL)
    assert deliverable.startswith("Translate patents") and "luminantiefactor" in deliverable
    assert unresolved == "- Whether to keep British spelling."
    assert duet.extract_deliverable("just the prompt") == ("just the prompt", "")


def test_consensus_ends_the_debate_and_the_synthesis_runs(tmp_path):
    calls = []
    a = scripted("Claude", ["1. term wrong\nVERDICT: CONTINUE", "Fixed.\nVERDICT: AGREED", FINAL], calls)
    b = scripted("GPT", ["Confirmed, quoting TM.\nVERDICT: CONTINUE", "OPEN ISSUES: none\nVERDICT: AGREED"], calls)
    path = tmp_path / "duet.md"
    seen = []
    result = duet.run_duet(a, b, duet.build_brief("Patent", "Translate.", "TM: x"),
                           max_rounds=4, transcript_path=str(path), on_turn=seen.append)
    assert result.consensus and result.rounds == 2
    assert [c[0] for c in calls] == ["Claude", "GPT", "Claude", "GPT", "Claude"]
    assert [t.speaker for t in seen] == ["A", "B", "A", "B", "synthesis"]
    assert "luminantiefactor" in result.deliverable
    assert "British" in result.unresolved
    text = path.read_text(encoding="utf-8")
    assert "Round 2 – GPT" in text and "Synthesis – Claude (consensus)" in text
    # every turn sees the review so far, and the synthesis gets the instruction
    assert "Round 1 – Claude" in calls[1][1] and duet.START_MARKER in calls[-1][1]


def test_round_limit_forces_the_synthesis(tmp_path):
    calls = []
    a = scripted("A1", ["x\nVERDICT: CONTINUE", "y\nVERDICT: AGREED"], calls)
    b = scripted("B1", ["x\nVERDICT: CONTINUE", "y\nVERDICT: CONTINUE", FINAL], calls)
    result = duet.run_duet(a, b, "brief", max_rounds=2, opener="A", synthesiser="B",
                           transcript_path=str(tmp_path / "t.md"))
    assert not result.consensus and result.rounds == 2
    assert calls[-1][0] == "B1" and duet.FORCED in calls[-1][1]
    assert "round limit reached" in (tmp_path / "t.md").read_text(encoding="utf-8")


def test_opener_b_and_agreed_from_one_side_only_is_not_consensus():
    calls = []
    a = scripted("A1", ["VERDICT: CONTINUE", FINAL], calls)
    b = scripted("B1", ["VERDICT: AGREED"], calls)
    result = duet.run_duet(a, b, "brief", max_rounds=1, opener="B")
    assert [c[0] for c in calls] == ["B1", "A1", "A1"]
    assert not result.consensus


def test_retries_with_backoff_then_gives_up():
    calls, slept, retried = [], [], []
    a = scripted("A1", [RuntimeError("503"), RuntimeError("503"), "ok\nVERDICT: AGREED", FINAL], calls)
    b = scripted("B1", ["VERDICT: AGREED"], calls)
    result = duet.run_duet(a, b, "brief", max_rounds=1, sleep=slept.append,
                           on_retry=lambda n, e: retried.append(n))
    assert slept == [15.0, 30.0] and retried == [1, 2] and result.consensus
    a = scripted("A1", [RuntimeError("down")] * 3, [])
    with pytest.raises(RuntimeError):
        duet.run_duet(a, b, "brief", max_rounds=1, sleep=lambda s: None)


def test_stop_and_transcript_written_after_every_turn(tmp_path):
    path = tmp_path / "t.md"
    sizes = []
    a = scripted("A1", ["first\nVERDICT: CONTINUE"], [])
    b = scripted("B1", [], [])
    result = duet.run_duet(a, b, "brief", transcript_path=str(path),
                           on_turn=lambda t: sizes.append(path.stat().st_size),
                           should_stop=lambda: len(sizes) >= 1)
    assert result.stopped and result.deliverable == "" and sizes[0] > 0
    assert "Stopped by the user" in path.read_text(encoding="utf-8")


def test_utf8_markers_survive(tmp_path):
    marker = "⟦TC: luminantiecoëfficiënt?⟧"
    a = scripted("A1", [f"{marker}\nVERDICT: AGREED", FINAL.replace("Dutch", marker)], [])
    b = scripted("B1", ["VERDICT: AGREED"], [])
    result = duet.run_duet(a, b, "brief", transcript_path=str(tmp_path / "t.md"))
    assert marker in (tmp_path / "t.md").read_text(encoding="utf-8") and marker in result.deliverable


def test_cost_estimate_grows_with_rounds():
    a = duet.Participant("A", "claude", "m-a", None)
    b = duet.Participant("B", "openai", "m-b", None)
    price = lambda prov, model, i, o: (i + 2 * o) / 1e6
    two = duet.estimate_cost(a, b, 25000, 2, 4000, price=price)
    four = duet.estimate_cost(a, b, 25000, 4, 4000, price=price)
    assert two["turns"] == 5 and four["turns"] == 9
    assert four["input_tokens"] > 2 * two["input_tokens"] * 0.9
    assert four["cost"] > two["cost"] > 0
    assert duet.estimate_cost(a, b, 25000, 2, 4000, price=lambda *x: None)["cost"] is None


def test_next_version_name():
    assert duet.next_version_name("Patent EN-NL", []) == "Patent EN-NL (duet v2)"
    assert duet.next_version_name("Patent EN-NL", ["patent en-nl (duet v2)"]) == "Patent EN-NL (duet v3)"
    assert duet.next_version_name("Patent EN-NL (duet v3)", []) == "Patent EN-NL (duet v4)"


def test_dialog_helpers():
    from types import SimpleNamespace as NS
    from modules import duet_dialog as dd
    assert [k for k, _ in dd.available_providers({"claude": "k", "google": "g"}, {"llm_ollama": False})] == [
        "claude", "gemini", "custom_openai"]
    proj = NS(name="BRANTS", source_lang="Dutch", target_lang="English")
    segs = [NS(source="Een inrichting."), NS(source="  "), NS(source="De luminantiecoëfficiënt.")]
    text = dd.build_material(proj, segs, 30, [("inrichting", "device")], [("luminantiefactor", "luminance factor")])
    assert "Source language: Dutch" in text and "2. De luminantiecoëfficiënt." in text
    assert "- inrichting  →  device" in text and "- luminantiefactor = luminance factor" in text
    assert "shortened" in dd.build_material(proj, segs * 2000, 6000, max_chars=500)
    assert dd.count_points("None.") == 0 and dd.count_points("- a\n- b\n3. c") == 3
    assert dd.count_points("Whether to keep it") == 1
    assert dd._yaml_safe('He said "x"\nand y') == "He said 'x' and y"


def test_segment_arbitration(tmp_path):
    brief = duet.build_segment_brief(
        "The luminance factor is 0.8.", "De luminantiecoëfficiënt is 0,8.",
        source_lang="English", target_lang="Dutch", question="XR · gpt-5.5: wrong CIE quantity",
        context=[("Previous.", "Vorige."), ("Next.", "")], instructions="Patent style.",
        terms=[("luminance factor", "luminantiefactor")],
        tm=[("The luminance factor is 0.7.", "De luminantiefactor is 0,7.", 92)])
    assert "from English to Dutch" in brief and "wrong CIE quantity" in brief
    assert "- Next.  →  (not translated)" in brief and "- 92%: The luminance factor is 0.7." in brief
    assert "- luminance factor = luminantiefactor" in brief and "Patent style." in brief
    assert brief.rstrip().endswith("De luminantiecoëfficiënt is 0,8.")
    calls = []
    final = (f"{duet.START_MARKER}\nDe luminantiefactor is 0,8.\n{duet.END_MARKER}\n\n"
             f"## {duet.UNRESOLVED_HEADING}\nNone.")
    a = scripted("A1", ["1. wrong quantity\nVERDICT: CONTINUE", "VERDICT: AGREED"], calls)
    b = scripted("B1", ["Agreed, TM 92% confirms.\nVERDICT: AGREED", final], calls)
    systems = []
    for p in (a, b):
        inner = p.ask
        p.ask = (lambda inner: lambda s, pr, m: (systems.append(s), inner(s, pr, m))[1])(inner)
    path = tmp_path / "arb.md"
    result = duet.run_duet(a, b, brief, max_rounds=3, max_tokens=1500, synthesiser="B",
                           transcript_path=str(path), protocol=duet.ARBITRATION_PROTOCOL,
                           synthesis=duet.ARBITRATION_SYNTHESIS, title="Arbitration of segment 7",
                           synthesis_tokens=2000)
    # A says CONTINUE, B AGREED, A AGREED → consensus in round 2; B writes the result
    assert result.consensus and result.deliverable == "De luminantiefactor is 0,8."
    assert set(systems) == {duet.ARBITRATION_PROTOCOL}
    assert calls[-1][0] == "B1" and calls[-1][2] == 2000 and duet.ARBITRATION_SYNTHESIS in calls[-1][1]
    assert path.read_text(encoding="utf-8").startswith("# Arbitration of segment 7 – ")
    assert "three substantive problems" not in duet.ARBITRATION_PROTOCOL
