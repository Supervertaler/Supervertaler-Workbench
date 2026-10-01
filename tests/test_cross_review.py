"""Cross-model review engine (issue #242, tier 2), with a scripted reviewer."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import cross_review as xr


def items(n, start=1):
    return [xr.Item(i, f"Bron {i}.", f"Source {i}.") for i in range(start, start + n)]


def scripted(replies, calls):
    replies = list(replies)

    def ask(system, prompt, max_tokens):
        calls.append((system, prompt))
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return (reply(prompt) if callable(reply) else reply), {"input_tokens": 100, "output_tokens": 10}
    return ask


def test_split_markers():
    text = 'De klep [is] gesloten. ⟦TC: "gesloote" corrected; [is] supplied⟧'
    assert xr.split_markers(text) == ("De klep [is] gesloten.",
                                      [("TC", '"gesloote" corrected; [is] supplied')])
    assert xr.split_markers("Plain text ") == ("Plain text ", [])
    clean, markers = xr.split_markers("A ⟦XR: wrong term⟧ B")
    assert clean == "A B" and markers == [("XR", "wrong term")]


def test_note_keys_and_origins():
    assert xr.note_key(xr.XR, "gpt-5.5") == "XR · gpt-5.5"
    assert xr.origin_of("XR · gpt-5.5") == "XR"
    assert xr.origin_of("TC · claude-sonnet-5-5") == "TC"
    assert xr.origin_of("gpt-5.5") == xr.PROOFREADING
    assert xr.origin_of("legacy") == xr.PROOFREADING


def test_parse_review_formats():
    response = """[1] PASS
**[2]** ⟦XR: "luminance coefficient" is a different CIE quantity → "luminance factor"⟧
[3]
Source: Bron 3.
Translation: Source 3.
PASS
- [4] FLAG: number 6 should be 5
[5] ⟦XR: first line
continues here⟧
[99] PASS
[6]"""
    got = xr.parse_review(response, [1, 2, 3, 4, 5, 6])
    assert got[1] == ("pass", "")
    assert got[2] == ("flag", '"luminance coefficient" is a different CIE quantity → "luminance factor"')
    assert got[3] == ("pass", "")
    assert got[4] == ("flag", "number 6 should be 5")
    assert got[5] == ("flag", "first line continues here")
    assert 6 not in got and 99 not in got


def test_batches_respect_count_and_size():
    its = items(45)
    assert [len(b) for b in xr.make_batches(its, 20)] == [20, 20, 5]
    big = [xr.Item(1, "x" * 9000, "y" * 2000), xr.Item(2, "a", "b"), xr.Item(3, "x" * 9000, "")]
    assert [len(b) for b in xr.make_batches(big, 20, 12000)] == [2, 1]


def test_prompt_carries_instructions_terms_and_segments():
    prompt = xr.build_prompt(items(2), "Use British spelling.",
                             [{"source_term": "klep", "target_term": "valve"},
                              {"source_term": "kraan", "target_term": "tap", "forbidden": True}])
    assert "Use British spelling." in prompt
    assert "- klep → valve" in prompt and "- kraan → ⚠️ DO NOT USE: tap" in prompt
    assert "[2]\nSource: Bron 2.\nTranslation: Source 2." in prompt
    assert "(None beyond" in xr.build_prompt(items(1))


def test_run_review_collects_flags_and_retries_skipped_segments():
    calls, seen = [], []

    def first(prompt):
        return "[1] PASS\n[2] ⟦XR: wrong⟧\n[4] PASS"          # skips 3
    replies = [first, "[5] PASS", "[3] ⟦XR: omission⟧"]
    result = xr.run_review(items(5), scripted(replies, calls), source_lang="Dutch", target_lang="English",
                           max_segments=4, on_batch=lambda a, d, t: seen.append((dict(a), d, t)))
    assert result.flags == {2: "wrong", 3: "omission"}
    assert sorted(result.passed) == [1, 4, 5] and result.missing == []
    assert result.calls == 3 and result.input_tokens == 300
    assert "from Dutch to English" in calls[0][0]
    assert "[3]" in calls[2][1] and "[1]" not in calls[2][1]       # second try: only the skipped one
    assert [d for _a, d, _t in seen] == [3, 4, 5] and seen[-1][2] == 5


def test_run_review_missing_after_second_try_and_term_filter():
    calls = []
    keep = lambda terms, text: [t for t in terms if t["source_term"] in text]
    terms = [{"source_term": "Bron 1", "target_term": "S1"}, {"source_term": "Bron 9", "target_term": "S9"}]
    result = xr.run_review(items(2), scripted(["[1] PASS", "nothing useful"], calls),
                           source_lang="nl", target_lang="en", terms=terms, filter_terms=keep)
    assert result.passed == [1] and result.missing == [2]
    assert "Bron 1 → S1" in calls[0][1] and "Bron 9" not in calls[0][1]


def test_stop_and_retry_backoff():
    slept = []
    result = xr.run_review(items(3), scripted([RuntimeError("503"), "[1] PASS\n[2] PASS"], []),
                           source_lang="nl", target_lang="en", max_segments=2,
                           sleep=slept.append, should_stop=lambda: bool(slept))
    # the stop check runs before each batch: the first batch ran (after one retry), the second didn't
    assert slept == [15.0] and result.stopped and result.passed == [1, 2]
    with pytest.raises(RuntimeError):
        xr.run_review(items(1), scripted([RuntimeError("x")] * 3, []), source_lang="nl",
                      target_lang="en", sleep=lambda s: None)


def test_estimate_and_report(tmp_path):
    its = items(30)
    est = xr.estimate(its, "x" * 4000, 0, max_segments=20)
    assert est["calls"] == 2 and est["output_tokens"] == 900
    assert est["input_tokens"] > 2 * 4000 // 4
    result = xr.ReviewResult(flags={2: "wrong ⟦term⟧"}, passed=[1], missing=[3])
    path = tmp_path / "xr.md"
    xr.write_report(str(path), result, its[:3], reviewer="OpenAI (gpt-5.5)",
                    translator="Claude (claude-sonnet-5-5)", prompt_name="Patents NL-EN", project_name="BRANTS")
    text = path.read_text(encoding="utf-8")
    assert "Reviewed by:** OpenAI (gpt-5.5)" in text and "1 flagged, 1 passed, 1 not answered" in text
    assert "### Segment 2" in text and "- **⟦XR⟧** wrong ⟦term⟧" in text and "## Not answered" in text


def test_dialog_helpers():
    from types import SimpleNamespace as NS
    from modules import cross_review_dialog as xd
    lib = NS(active_primary_prompt="Translate patents.", attached_prompts=["House style.", ""],
             active_primary_prompt_path="[EXTERNAL] C:/prompts/Patents NL-EN.md")
    name, text = xd.project_instructions(NS(prompt_manager_qt=NS(library=lib)))
    assert name == "Patents NL-EN + 1 attached" and text == "Translate patents.\n\nHouse style."
    assert xd.project_instructions(NS()) == ("", "")

    segs = {1: NS(id=1, proofreading_notes={"gpt-5.5": "old proofreading"}),
            2: NS(id=2, proofreading_notes={"XR · m": "stale flag"}),
            3: NS(id=3, proofreading_notes=None)}
    changed = xd.apply_answers(segs, {1: ("flag", "wrong"), 2: ("pass", ""), 3: ("pass", ""), 9: ("flag", "x")}, "XR · m")
    assert [s.id for s in changed] == [1, 2]
    assert segs[1].proofreading_notes == {"gpt-5.5": "old proofreading", "XR · m": "wrong"}
    assert segs[2].proofreading_notes == {} and segs[3].proofreading_notes is None

    class Mgr:
        def get_active_termbase_ids(self, pid):
            return [4]

        def get_terms(self, tb_id):
            return [{"source_term": "klep", "target_term": "valve"},
                    {"source_term": "kraan", "target_term": "tap", "forbidden": 1},
                    {"source_term": " ", "target_term": "x"}]
    terms = xd.project_terms(NS(termbase_mgr=Mgr(), current_project=NS(id="p1")))
    assert terms == [{"source_term": "klep", "target_term": "valve", "forbidden": False},
                     {"source_term": "kraan", "target_term": "tap", "forbidden": True}]
