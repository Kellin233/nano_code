"""Token pricing helpers for budget checks and cost display."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    """Per-1M-token pricing in USD."""

    input_cache_hit: float
    input_cache_miss: float
    output: float
    label: str


FALLBACK_PRICING = ModelPricing(
    input_cache_hit=3.0,
    input_cache_miss=3.0,
    output=15.0,
    label="fallback $3/M input + $15/M output",
)

MODEL_PRICING: dict[str, ModelPricing] = {
    # DeepSeek official API pricing, per 1M tokens. Input without cache
    # breakdown is treated as cache miss by estimate_model_cost_usd().
    "deepseek-v4-flash": ModelPricing(
        input_cache_hit=0.0028,
        input_cache_miss=0.14,
        output=0.28,
        label="DeepSeek V4 Flash",
    ),
    "deepseek-v4-pro": ModelPricing(
        input_cache_hit=0.003625,
        input_cache_miss=0.435,
        output=0.87,
        label="DeepSeek V4 Pro",
    ),
    "deepseek-chat": ModelPricing(
        input_cache_hit=0.0028,
        input_cache_miss=0.14,
        output=0.28,
        label="DeepSeek Chat / V4 Flash compatibility",
    ),
    "deepseek-reasoner": ModelPricing(
        input_cache_hit=0.0028,
        input_cache_miss=0.14,
        output=0.28,
        label="DeepSeek Reasoner / V4 Flash compatibility",
    ),
}


def _canonical_model_name(model: str) -> str:
    name = model.lower().strip()
    if "deepseek-v4-pro" in name or "v4-pro" in name:
        return "deepseek-v4-pro"
    if "deepseek-v4-flash" in name or "v4-flash" in name:
        return "deepseek-v4-flash"
    if "deepseek-reasoner" in name:
        return "deepseek-reasoner"
    if "deepseek-chat" in name:
        return "deepseek-chat"
    return name


def pricing_for_model(model: str) -> ModelPricing:
    """Return pricing for a model, falling back to the previous generic estimate."""
    return MODEL_PRICING.get(_canonical_model_name(model), FALLBACK_PRICING)


def estimate_model_cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    input_cache_hit_tokens: int = 0,
    input_cache_miss_tokens: int = 0,
) -> float:
    """Estimate USD cost from token usage.

    If the API does not provide cache-hit/cache-miss input token counts, all
    input tokens are treated as cache misses. That is conservative for DeepSeek.
    """
    pricing = pricing_for_model(model)
    input_tokens = max(0, input_tokens)
    output_tokens = max(0, output_tokens)
    cache_hit = max(0, input_cache_hit_tokens)
    cache_miss = max(0, input_cache_miss_tokens)

    if cache_hit or cache_miss:
        accounted = cache_hit + cache_miss
        cache_miss += max(0, input_tokens - accounted)
    else:
        cache_miss = input_tokens

    return (
        cache_hit * pricing.input_cache_hit
        + cache_miss * pricing.input_cache_miss
        + output_tokens * pricing.output
    ) / 1_000_000
