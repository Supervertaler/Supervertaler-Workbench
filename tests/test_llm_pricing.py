"""Guard the Claude prices the cost figures rest on (modules/llm_pricing.py).

The same figures are pinned in Supervertaler for Trados's core tests
(ClaudePricingTests), because both products read the one canonical
pricing.json and are meant to compute identical costs. Prices as Anthropic's
pricing page gives them, checked 2026-09-28: Claude Sonnet 5.5 is $2 / $10 per
million tokens, and cache reads are 0.1x the input rate except on Claude Opus
5.5 (0.05x) and Claude Fable 5.1 (0.025x).
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
])
def test_claude_costs_match_the_published_prices(model, regular, read, write, output, expected):
    cost = compute_actual_cost("claude", model, regular, read, write, output)
    assert cost == pytest.approx(expected), f"{model}: {cost} != {expected}"
