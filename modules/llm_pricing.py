"""
LLM Pricing Module for Supervertaler
=====================================

Per-model token prices, loaded from the canonical ``pricing.json`` that is
shared verbatim with Supervertaler for Trados (single source of truth).

Resolution order:
  1. ``<home>/Supervertaler/pricing.json`` — the shared user override. Edit this
     one file to re-price BOTH Supervertaler products at once.
  2. ``modules/pricing.json`` bundled with this app — the default canonical list.
  3. a tiny hardcoded table — only if both files fail to load, so cost
     estimation never crashes.

Prices are USD per 1,000,000 tokens. Cache-discount multipliers are applied
elsewhere (per provider), not here.
"""

import json
from pathlib import Path
from typing import Dict, Optional, Tuple

# Providers whose models are never billed via these list prices (local). Only
# Ollama is unconditionally free. custom_openai (self-hosted / OpenAI-compatible
# endpoints) falls through to the price list, so a custom model gains a cost
# figure as soon as its id + rate are added to pricing.json — otherwise its
# cost is reported as unknown (None), not free.
_FREE_PROVIDERS = ("ollama",)


def _load_pricing() -> Dict[str, Tuple[float, float]]:
    """Load ``model_id -> (input_per_1M, output_per_1M)`` from the canonical file.

    The shared override at ``~/Supervertaler/pricing.json`` (the default data
    root, matching the Trados plugin) wins over the bundled copy when present.
    """
    candidates = [
        Path.home() / "Supervertaler" / "pricing.json",       # shared override
        Path(__file__).resolve().parent / "pricing.json",     # bundled default
    ]
    for path in candidates:
        try:
            if path.is_file():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                table: Dict[str, Tuple[float, float]] = {}
                for model_id, p in (data.get("models") or {}).items():
                    if isinstance(p, dict) and "input" in p and "output" in p:
                        table[model_id] = (float(p["input"]), float(p["output"]))
                if table:
                    return table
        except Exception:
            continue
    # Minimal safety net — only reached if both files fail to load.
    return {
        "gpt-5.6-sol": (5.0, 30.0),
        "gpt-5.6-terra": (2.5, 15.0),
        "gpt-5.6-luna": (1.0, 6.0),
        "gpt-5.5": (5.0, 30.0),
        "claude-fable-5": (10.0, 50.0),
        "claude-opus-5": (5.0, 25.0),
        "claude-opus-4-8": (5.0, 25.0),
        "claude-sonnet-5": (3.0, 15.0),
        "claude-sonnet-4-6": (3.0, 15.0),
        "claude-haiku-4-5-20251001": (1.0, 5.0),
        "gemini-3.1-flash-lite": (0.25, 1.50),
    }


# Flat map ``model_id -> (input_per_1M, output_per_1M)``. Loaded once at import.
PRICING: Dict[str, Tuple[float, float]] = _load_pricing()


def _resolve_prices(model: str) -> Optional[Tuple[float, float]]:
    """Return ``(input_per_1M, output_per_1M)`` for a model id, or None if it is
    not in the pricing table. Tries an exact match first, then a prefix match in
    either direction (the model id may carry a date/variant suffix, or vice
    versa)."""
    prices = PRICING.get(model)
    if not prices and model:
        for known_model, p in PRICING.items():
            if model.startswith(known_model) or known_model.startswith(model):
                prices = p
                break
    return prices or None


def _cache_multipliers(model: str) -> Tuple[float, float]:
    """Per-provider cache discount multipliers for a model:
    ``(cache_read_multiplier, cache_write_multiplier)``, both relative to the
    regular input rate. Multiply the input rate by these for the effective
    cached rate. Mirrors the Trados plugin's ``TokenEstimator.GetCacheMultipliers``
    so the two products compute identical cache-aware costs.
    """
    if not model:
        return (1.0, 1.0)
    lc = model.lower()

    # Anthropic native + OpenRouter→Anthropic: 90% off reads, 25% write surcharge.
    if "claude" in lc or lc.startswith("anthropic/"):
        return (0.1, 1.25)

    # OpenAI auto-cache: 50% off cache reads, no separate cache-write surcharge.
    if lc.startswith("gpt-") or lc.startswith("openai/") or lc.startswith("o4-"):
        return (0.5, 1.0)

    # DeepSeek automatic disk caching: ~90% off cache reads.
    if lc.startswith("deepseek") or "deepseek/" in lc:
        return (0.1, 1.0)

    # Gemini 2.5+ implicit caching: 75% off cache reads.
    if (lc.startswith("gemini-2.5") or lc.startswith("gemini-3")
            or lc.startswith("google/gemini-2.5") or lc.startswith("google/gemini-3")):
        return (0.25, 1.0)

    # No documented caching for this provider/model.
    return (1.0, 1.0)


def estimate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> Optional[float]:
    """
    Estimate USD cost for a given API call.

    Returns:
        - 0.0  when tokens are 0, or for genuinely free providers (ollama, custom_openai).
        - float (> 0) computed cost for a model with a known pricing entry.
        - None when the model is not in the pricing table.
            Callers should render this as "unknown" (NOT "free") so users
            don't mistake a non-curated model for a free one.

    This treats every input token at the full input rate. For cache-aware
    accounting from a provider's real usage block, use :func:`compute_actual_cost`.
    """
    if not input_tokens and not output_tokens:
        return 0.0

    if (provider or "").lower() in _FREE_PROVIDERS:
        return 0.0

    prices = _resolve_prices(model)
    if not prices:
        return None

    input_price, output_price = prices
    cost = (input_tokens * input_price + output_tokens * output_price) / 1_000_000
    return round(cost, 4)


def compute_actual_cost(provider: str, model: str, regular_input_tokens: int,
                        cache_read_tokens: int, cache_write_tokens: int,
                        output_tokens: int) -> Optional[float]:
    """
    Compute USD cost from a provider's real usage block, applying per-provider
    cache discount multipliers (cached input billed below the full input rate).

    Mirrors the Trados plugin's ``TokenEstimator.ComputeActualCost`` so the two
    products produce identical figures for the same call.

    Returns:
        - 0.0  when all token counts are 0, or for free providers (ollama).
        - float (> 0) cache-aware computed cost for a priced model.
        - None when the model is not in the pricing table (cost unknown).
    """
    if not (regular_input_tokens or cache_read_tokens or cache_write_tokens or output_tokens):
        return 0.0

    if (provider or "").lower() in _FREE_PROVIDERS:
        return 0.0

    prices = _resolve_prices(model)
    if not prices:
        return None

    input_price, output_price = prices
    read_mul, write_mul = _cache_multipliers(model)
    cost = (
        regular_input_tokens * input_price
        + cache_read_tokens * input_price * read_mul
        + cache_write_tokens * input_price * write_mul
        + output_tokens * output_price
    ) / 1_000_000
    return round(cost, 6)
