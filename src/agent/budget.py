"""Token pricing and provider-neutral token estimation helpers."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from .types import (
    ConversationBlock,
    ConversationHistory,
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


ESTIMATED_CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD_TOKENS = 4
TOOL_USE_OVERHEAD_TOKENS = 4
TOOL_RESULT_OVERHEAD_TOKENS = 4


@dataclass(frozen=True)
class ModelPricing:
    """Per-1M-token pricing in USD."""

    input_cache_hit: float
    input_cache_miss: float
    output: float
    label: str


def estimate_text_tokens(text: str) -> int:
    """Estimate tokens for plain text with NanoCode's provider-neutral heuristic."""
    if not text:
        return 0
    return max(1, math.ceil(len(str(text)) / ESTIMATED_CHARS_PER_TOKEN))


def estimate_block_tokens(block: ConversationBlock) -> int:
    """Estimate tokens for one canonical conversation block."""
    if isinstance(block, TextBlock):
        return estimate_text_tokens(block.text)
    if isinstance(block, ToolUseBlock):
        payload = block.name + json.dumps(block.input, ensure_ascii=False, sort_keys=True)
        return TOOL_USE_OVERHEAD_TOKENS + estimate_text_tokens(payload)
    if isinstance(block, ToolResultBlock):
        payload = block.content
        if block.tool_name:
            payload = f"{block.tool_name}\n{payload}"
        return TOOL_RESULT_OVERHEAD_TOKENS + estimate_text_tokens(payload)
    return 0


def estimate_message_tokens(message: ConversationMessage) -> int:
    """Estimate tokens for one canonical conversation message."""
    total = MESSAGE_OVERHEAD_TOKENS + estimate_text_tokens(message.role)
    total += sum(estimate_block_tokens(block) for block in message.content)
    if message.metadata:
        total += estimate_text_tokens(json.dumps(message.metadata, ensure_ascii=False, sort_keys=True))
    return max(1, total)


def estimate_messages_tokens(messages: list[ConversationMessage]) -> int:
    """Estimate tokens for a list of canonical conversation messages."""
    return sum(estimate_message_tokens(message) for message in messages)


def estimate_conversation_tokens(history: ConversationHistory) -> int:
    """Estimate tokens for a canonical conversation history."""
    return estimate_messages_tokens(history.messages)


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
