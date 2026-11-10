"""Guard the Claude prices the cost figures rest on (modules/llm_pricing.py).

The same figures are pinned in Supervertaler for Trados's core tests
(ClaudePricingTests), because both products read the one canonical
pricing.json and are meant to compute identical costs. Prices as Anthropic's
pricing page gives them, checked 2026-09-28: Claude Sonnet 5.5 is $2 / $10 per
million tokens, and cache reads are 0.1x the input rate except on Claude Opus
5.5 (0.05x) and Claude Fable 5.1 and 5.2 (0.025x). Claude Fable 5.2 is $8 / $40,
checked 2026-11-10.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.llm_pricing import compute_actual_cost

M = 1_000_000


@pytest.mark.parametrize("model, regular, read, write, output, expected", [
    ("claude-sonnet-5-5", M, 0, 0, 0, 2.00),        # input $2/MTok
    ("claude-sonnet-5-5", 0, 0, 0, M, 10.00),       # output $10/MTok
    ("claude-sonnet-5-5", 0, 0, M, 0, 2.50),        # 5-minute cache write, 1.25x
    ("claude-sonnet-5-5", 0, M, 0, 0, 0.20),        # cache read, 0.1x
    ("claude-sonnet-5", M, 0, 0, M, 12.00),         # $2 / $10, no longer $3 / $15
    ("claude-opus-5", 0, M, 0, 0, 0.50),            # cache read, 0.1x
    ("claude-opus-5-5", 0, M, 0, 0, 0.20),          # cache read, 0.05x
    ("claude-opus-5-5", 0, 0, M, 0, 5.00),          # cache write, 1.25x
    ("claude-fable-5-1", 0, M, 0, 0, 0.25),         # cache read, 0.025x
    ("claude-fable-5-1", 0, 0, M, 0, 12.50),        # cache write, 1.25x
    ("claude-fable-5-2", M, 0, 0, 0, 8.00),         # input $8/MTok
    ("claude-fable-5-2", 0, 0, 0, M, 40.00),        # output $40/MTok
    ("claude-fable-5-2", 0, M, 0, 0, 0.20),         # cache read, 0.025x
    ("claude-fable-5-2", 0, 0, M, 0, 10.00),        # cache write, 1.25x
])
def test_claude_costs_match_the_published_prices(model, regular, read, write, output, expected):
    cost = compute_actual_cost("claude", model, regular, read, write, output)
    assert cost == pytest.approx(expected), f"{model}: {cost} != {expected}"


# OpenAI, as its model pages give it, checked 2026-10-07; pinned identically in
# Supervertaler for Trados's core tests (OpenAiModelTests). GPT-5.6 Sol's price
# is promotional, "available at least through November 21, 2026".
@pytest.mark.parametrize("model, regular, read, output, expected", [
    ("gpt-6-astra", M, 0, 0, 10.00),       # input $10/MTok
    ("gpt-6-astra", 0, 0, M, 50.00),       # output $50/MTok
    ("gpt-6-astra", 0, M, 0, 1.00),        # cached input, 0.1x
    ("gpt-6.1-sol", M, 0, 0, 2.00),        # input $2/MTok
    ("gpt-6.1-sol", 0, 0, M, 10.00),       # output $10/MTok
    ("gpt-6.1-sol", 0, M, 0, 0.10),        # cached input, 0.05x
    ("gpt-6-luna", M, 0, 0, 0.10),         # input $0.10/MTok
    ("gpt-6-luna", 0, 0, M, 0.50),         # output $0.50/MTok
    ("gpt-6-luna", 0, M, 0, 0.01),         # cached input, 0.1x
    ("gpt-5.6-sol", M, 0, M, 24.00),       # $4 / $20, no longer $5 / $30
    ("gpt-5.6-sol", 0, M, 0, 0.40),        # cached input, 0.1x
    ("gpt-5.6-terra", M, 0, M, 14.00),     # $2 / $12, no longer $2.50 / $15
    ("gpt-5.6-terra", 0, M, 0, 0.20),      # cached input, 0.1x
    ("gpt-5.6-luna", M, 0, M, 1.40),       # $0.20 / $1.20, no longer $1 / $6
    ("gpt-5.6-luna", 0, M, 0, 0.02),       # cached input, 0.1x
    ("gpt-5.4-mini", 0, M, 0, 0.375),      # older models keep 0.5x
])
def test_openai_costs_match_the_published_prices(model, regular, read, output, expected):
    cost = compute_actual_cost("openai", model, regular, read, 0, output)
    assert cost == pytest.approx(expected), f"{model}: {cost} != {expected}"
