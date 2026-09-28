"""
Cost Estimate
=============

Up-front AI cost estimate for a batch translation, and USD/EUR display of
costs (issue #8).

The estimate mirrors how batch translation actually sends a job: the source
segments go out in batches of ``batch_size``, each batch carrying the same
system prompt (instructions, custom prompt, glossary) plus a short numbered-list
instruction block. Token counts use the same chars/4 heuristic the usage log
falls back to when a provider reports no usage, and the price comes from the
shared ``pricing.json`` via :mod:`modules.llm_pricing`, so an estimate and the
logged cost of the finished job are computed the same way.

When the system prompt is long enough for the provider to cache it (1,024
tokens on Anthropic / OpenAI / Gemini / DeepSeek), batches 2..N are priced at
the provider's cache-read rate, as they will be billed.

Costs are always computed and stored in USD. EUR is a display conversion with a
user-editable rate – there is no live exchange-rate lookup.
"""

import math
from typing import Dict, Iterable, Optional

from modules.llm_pricing import _cache_multipliers, compute_actual_cost

CHARS_PER_TOKEN = 4          # same heuristic as usage_log's estimated records
BATCH_OVERHEAD_CHARS = 700   # the numbered-list instructions added to each batch
SEGMENT_LABEL_CHARS = 5      # "123. " in front of every segment, in and out
OUTPUT_RATIO = 1.15          # translations usually run a little longer than the source
MIN_CACHEABLE_TOKENS = 1024  # shortest prompt the providers will cache

CURRENCIES = ("USD", "EUR")
DEFAULT_USD_TO_EUR = 0.86    # approximate; users set their own in the usage report


def _tokens(chars: float) -> int:
    return int(math.ceil(max(0.0, chars) / CHARS_PER_TOKEN))


def estimate_batch_translation(provider: str, model: str, sources: Iterable[str],
                               batch_size: int = 20,
                               system_prompt_chars: int = 0) -> Dict:
    """Estimate tokens and USD cost for batch-translating ``sources``.

    Returns a dict with ``segments``, ``batches``, ``input_tokens``,
    ``output_tokens`` and ``cost_usd`` (None when the model is not in the price
    list, 0.0 for local models and for an empty job).
    """
    sources = [s or "" for s in sources]
    segments = len(sources)
    if segments == 0:
        return {"segments": 0, "batches": 0, "input_tokens": 0,
                "output_tokens": 0, "cost_usd": 0.0}

    batch_size = max(1, int(batch_size or 1))
    batches = int(math.ceil(segments / batch_size))
    source_chars = sum(len(s) + SEGMENT_LABEL_CHARS for s in sources)

    user_tokens = _tokens(source_chars + batches * BATCH_OVERHEAD_CHARS)
    system_tokens = _tokens(system_prompt_chars or 0)
    output_tokens = _tokens(source_chars * OUTPUT_RATIO)

    regular, cache_read, cache_write = user_tokens, 0, 0
    read_mul, _write_mul = _cache_multipliers(model or "")
    if system_tokens >= MIN_CACHEABLE_TOKENS and batches > 1 and read_mul < 1.0:
        cache_write = system_tokens                 # first batch writes the cache
        cache_read = system_tokens * (batches - 1)  # the rest read it
    else:
        regular += system_tokens * batches

    cost = compute_actual_cost(provider, model, regular, cache_read, cache_write, output_tokens)
    return {
        "segments": segments,
        "batches": batches,
        "input_tokens": regular + cache_read + cache_write,
        "output_tokens": output_tokens,
        "cost_usd": cost,
    }


def currency_settings(general_settings: Optional[Dict]) -> tuple:
    """``(currency, usd_to_eur_rate)`` from the general settings section."""
    general_settings = general_settings or {}
    currency = str(general_settings.get("cost_currency") or "USD").upper()
    if currency not in CURRENCIES:
        currency = "USD"
    try:
        rate = float(general_settings.get("usd_to_eur_rate") or DEFAULT_USD_TO_EUR)
    except (TypeError, ValueError):
        rate = DEFAULT_USD_TO_EUR
    if rate <= 0:
        rate = DEFAULT_USD_TO_EUR
    return currency, rate


def convert(usd: float, currency: str = "USD", rate: float = DEFAULT_USD_TO_EUR) -> float:
    """USD amount in ``currency``."""
    return usd * rate if currency == "EUR" else usd


def format_cost(usd: Optional[float], currency: str = "USD",
                rate: float = DEFAULT_USD_TO_EUR, decimals: int = 2) -> str:
    """``$1.23`` / ``€1.06``; sub-cent amounts keep enough digits to show;
    ``None`` (model not in the price list) reads as ``unknown``."""
    if usd is None:
        return "unknown"
    symbol = "€" if currency == "EUR" else "$"
    value = convert(usd, currency, rate)
    if 0 < value < 10 ** -decimals:
        return f"{symbol}{value:.4f}"
    return f"{symbol}{value:.{decimals}f}"
