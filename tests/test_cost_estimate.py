"""Per-job AI cost: up-front estimate, EUR display and per-project totals (issue #8)."""

from __future__ import annotations

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from modules import usage_log
from modules.cost_estimate import (
    MIN_CACHEABLE_TOKENS, currency_settings, estimate_batch_translation, format_cost,
)
from modules.llm_pricing import compute_actual_cost


def test_estimate_counts_batches_and_tokens():
    sources = ["x" * 395] * 45  # 400 chars each with the "N. " label
    est = estimate_batch_translation("openai", "gpt-4.1", sources, batch_size=20,
                                     system_prompt_chars=2000)
    assert est["segments"] == 45 and est["batches"] == 3
    # 18,000 source chars + 3 × 700 overhead, plus a 500-token system prompt per batch
    assert est["input_tokens"] == (18000 + 2100) // 4 + 500 * 3
    assert est["output_tokens"] == 5175
    assert est["cost_usd"] == pytest.approx(
        (est["input_tokens"] * 2.0 + est["output_tokens"] * 8.0) / 1_000_000)


def test_long_system_prompt_is_priced_as_cached_after_the_first_batch():
    sources = ["y" * 95] * 100
    system_chars = MIN_CACHEABLE_TOKENS * 4 * 3  # 3,072 tokens
    est = estimate_batch_translation("claude", "claude-sonnet-4-6", sources, batch_size=20,
                                     system_prompt_chars=system_chars)
    system_tokens = system_chars // 4
    user_tokens = (100 * 100 + 5 * 700) // 4
    expected = compute_actual_cost("claude", "claude-sonnet-4-6", user_tokens,
                                   system_tokens * 4, system_tokens, est["output_tokens"])
    assert est["cost_usd"] == pytest.approx(expected)
    full_price = compute_actual_cost("claude", "claude-sonnet-4-6", user_tokens + system_tokens * 5,
                                     0, 0, est["output_tokens"])
    assert est["cost_usd"] < full_price


def test_one_call_per_segment_repeats_the_system_prompt():
    sources = ["z" * 45] * 10
    batched = estimate_batch_translation("openai", "gpt-4.1", sources, 20, 800)
    single = estimate_batch_translation("openai", "gpt-4.1", sources, 1, 800)
    assert single["batches"] == 10
    assert single["input_tokens"] > batched["input_tokens"]


def test_unknown_free_and_empty_jobs():
    assert estimate_batch_translation("openai", "no-such-model", ["a"])["cost_usd"] is None
    assert estimate_batch_translation("ollama", "llama3", ["a"] * 5)["cost_usd"] == 0.0
    assert estimate_batch_translation("openai", "gpt-4.1", [])["batches"] == 0


def test_currency_display():
    assert currency_settings({}) == ("USD", 0.86)
    assert currency_settings({"cost_currency": "eur", "usd_to_eur_rate": 0.9}) == ("EUR", 0.9)
    assert currency_settings({"cost_currency": "GBP", "usd_to_eur_rate": "x"}) == ("USD", 0.86)
    assert format_cost(1.5) == "$1.50"
    assert format_cost(2.0, "EUR", 0.9) == "€1.80"
    assert format_cost(0.004) == "$0.0040"
    assert format_cost(None, "EUR") == "unknown"
    assert format_cost(0.0) == "$0.00"


@pytest.fixture
def ledger(tmp_path):
    usage_log.configure(tmp_path, "test", enabled=True)
    yield
    usage_log.set_default_context(None)
    usage_log.clear_context()
    usage_log.configure(None)


def test_calls_outside_a_batch_count_towards_the_open_project(ledger):
    usage_log.set_default_context(lambda: {"project": "Pumps", "src_lang": "en", "tgt_lang": "nl"})
    usage_log.record("openai", "gpt-4.1", {"input_tokens": 1000, "output_tokens": 500}, task="Chat")
    # An explicit batch context still wins over the open project.
    with usage_log.UsageContext(task="BatchTranslate", project="Valves"):
        usage_log.record("openai", "gpt-4.1", {"input_tokens": 2000, "output_tokens": 1000})
    usage_log.record("openai", "not-priced", {"input_tokens": 10, "output_tokens": 10})

    now = datetime.datetime.now(datetime.timezone.utc)
    records = usage_log.load(now - datetime.timedelta(hours=1), now + datetime.timedelta(hours=1))
    assert [(r["project"], r["src_lang"]) for r in records] == [
        ("Pumps", "en"), ("Valves", ""), ("Pumps", "en")]

    pumps = usage_log.project_totals("Pumps")
    assert pumps["calls"] == 2 and pumps["unpriced"] == 1
    assert pumps["cost_usd"] == pytest.approx((1000 * 2.0 + 500 * 8.0) / 1_000_000)
    assert usage_log.project_totals("Valves")["calls"] == 1
    assert usage_log.project_totals("")["calls"] == 0


def test_a_failing_default_context_never_breaks_logging(ledger):
    def boom():
        raise RuntimeError("no project")
    usage_log.set_default_context(boom)
    usage_log.record("openai", "gpt-4.1", {"input_tokens": 1, "output_tokens": 1})
    now = datetime.datetime.now(datetime.timezone.utc)
    assert len(usage_log.load(now - datetime.timedelta(hours=1), now)) == 1
