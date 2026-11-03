"""Gemini 4 Argon is offered and costed (v1.10.375).

Supervertaler for Trados and for memoQ have offered it since release 203;
Workbench's lists and price list lacked it. The model ID and prices are
the plugins' shared core's (LlmModels.cs, pricing.json, TokenEstimator.cs;
pinned there in GeminiPricingTests): $4 / $20 per million tokens from
1 Dec 2026 ($2 / $10 until 30 Nov, so costs shown until then err high),
and cache reads at 0.05x the input rate. The Gemini default is unchanged.
"""

from __future__ import annotations

import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from modules.llm_clients import LLMClient
from modules.llm_pricing import compute_actual_cost, estimate_cost

ARGON = "gemini-4-argon"
M = 1_000_000


@pytest.mark.parametrize("model, regular, read, output, expected", [
    (ARGON, M, 0, 0, 4.00),                 # input $4/MTok
    (ARGON, 0, 0, M, 20.00),                # output $20/MTok
    (ARGON, 0, M, 0, 0.20),                 # cache read, 0.05x
    ("google/" + ARGON, 0, M, 0, 0.20),     # the same through OpenRouter
    ("gemini-3.1-pro-preview", 0, M, 0, 0.50),   # Gemini 3 keeps 0.25x
])
def test_argon_costs_match_the_plugins(model, regular, read, output, expected):
    provider = "openrouter" if model.startswith("google/") else "gemini"
    cost = compute_actual_cost(provider, model, regular, read, 0, output)
    assert cost == pytest.approx(expected), f"{model}: {cost} != {expected}"


def test_argon_is_priced_directly_and_through_openrouter():
    assert estimate_cost("gemini", ARGON, M, M) == pytest.approx(24.00)
    assert estimate_cost("openrouter", "google/" + ARGON, M, M) == pytest.approx(24.00)


def test_argon_takes_images_and_is_sent_a_temperature():
    assert LLMClient.model_supports_vision("gemini", ARGON)
    # Trados sends Argon 0.3 too (checked 29 Oct 2026).
    assert LLMClient(api_key="x", provider="gemini", model=ARGON).temperature == 0.3


def test_the_gemini_default_is_unchanged():
    assert LLMClient.DEFAULT_MODELS["gemini"] == "gemini-3.1-flash-lite"


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


# Each place Workbench lists Gemini models, found by the code that builds it.
GEMINI_LISTS = [
    ("Settings → AI", "Supervertaler.py", r"gemini_combo\.addItems\(\[(.*?)\]\)"),
    ("Settings → QuickTrans", "Supervertaler.py",
     r'\("gemini", "Gemini \(Google AI\)", "gemini", \[(.*?)\]\),'),
    ("AI Assistant model menu", os.path.join("modules", "chat_view_widget.py"),
     r'\("gemini", "Google", \{(.*?)\}\),'),
    ("PDF Rescue", os.path.join("modules", "pdf_rescue_Qt.py"),
     r'--- Gemini \(Google\) ---"\)\s*self\.model_combo\.addItems\(\[(.*?)\]\)'),
]


@pytest.mark.parametrize("where, path, pattern", GEMINI_LISTS, ids=[w for w, *_ in GEMINI_LISTS])
def test_every_gemini_list_offers_argon(where, path, pattern):
    blocks = re.findall(pattern, read(path), re.S)
    assert len(blocks) == 1, f"{where}: the list was not found"
    assert re.search(rf"[\"']{ARGON}[\"' ]", blocks[0]), f"{where} lacks {ARGON}"


def test_settings_still_recommends_flash_lite_first():
    block = re.search(r"gemini_combo\.addItems\(\[(.*?)\]\)", read("Supervertaler.py"), re.S).group(1)
    first = re.search(r'"([^"]+)"', block).group(1)
    assert first.startswith("gemini-3.1-flash-lite (Recommended")
