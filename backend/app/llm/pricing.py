"""Decimal-safe cost accounting (Sprint P5).

Pricing is entirely configuration-driven (settings.llm_pricing_json) -
there is no hardcoded business-critical price constant anywhere in this
module. A provider/model pair with no configured entry prices as None,
never a fabricated value - see the sprint spec's "unknown pricing
returns null, not a fabricated value".
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.core.config import Settings

_PER_1K = Decimal(1000)


def _price_key(provider: str, model: str) -> str:
    return f"{provider}:{model}"


def estimate_cost_usd(
    settings: Settings,
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> Decimal | None:
    """Returns None if no pricing entry exists for provider:model, or if
    the configured entry is malformed - never guesses. All arithmetic is
    Decimal so cost figures never pick up float rounding error."""
    entry = settings.llm_pricing_map.get(_price_key(provider, model))
    if entry is None:
        return None
    try:
        input_per_1k = Decimal(entry["input_per_1k"])
        output_per_1k = Decimal(entry["output_per_1k"])
    except (KeyError, InvalidOperation):
        return None

    input_cost = (Decimal(input_tokens) / _PER_1K) * input_per_1k
    output_cost = (Decimal(output_tokens) / _PER_1K) * output_per_1k
    return input_cost + output_cost
