"""Anthropic Messages API 流式后端。

从原 AgentBackendMixin._call_anthropic_stream 提取，改为独立策略类。
不依赖 Agent 实例，所有数据通过参数传入。
"""

from __future__ import annotations

import json
import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast

import anthropic

from ..agent.types import DEFAULT_MAX_TOKENS, MAX_RETRIES, MAX_RETRY_DELAY_MS, ToolCall
from .base import Backend, BackendResponse, TokenUsage


def _usage_value(usage, *names: str) -> int:
    """Read a token usage value from Anthropic usage objects or dicts."""
    if not usage:
        return 0
    for name in names:
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0
    return 0


def _model_supports_thinking(model: str) -> bool:
    m = model.lower()
    if "claude-3-" in m or "3-5-" in m or "3-7-" in m:
        return False
    return "claude" in m and any(x in m for x in ("opus", "sonnet", "haiku"))


def _model_supports_adaptive_thinking(model: str) -> bool:
    m = model.lower()
    return "opus-4-6" in m or "sonnet-4-6" in m


def _get_max_output_tokens(model: str) -> int:
    m = model.lower()
    if "opus-4-6" in m:
        return 64000
    if "sonnet-4-6" in m:
        return 32000
    if any(x in m for x in ("opus-4", "sonnet-4", "haiku-4")):
        return 32000
    return DEFAULT_MAX_TOKENS


def _is_retryable(error: Exception) -> bool:
    msg = str(error)
    if "model_not_found" in msg or "No available channel" in msg:
        return False
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    if status in (429, 503, 529):
        return True
    return "overloaded" in msg or "ECONNRESET" in msg or "ETIMEDOUT" in msg


async def _with_retry(fn, max_retries: int = MAX_RETRIES) -> Any:
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as error:
            if attempt >= max_retries or not _is_retryable(error):
                raise
            delay = min(1000 * (2 ** attempt), MAX_RETRY_DELAY_MS) / 1000 + (hash(str(time.time())) % 1000) / 1000
            await asyncio.sleep(delay)


class AnthropicBackend(Backend):
    """Anthropic Messages API 流式后端。

    不依赖 Agent 实例——模型、系统提示词、消息历史、工具定义
    通过 call() 的参数传入。
    """

    def __init__(self, api_key: str, base_url: str | None = None, model: str = "claude-opus-4-6"):
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = anthropic.AsyncAnthropic(**kwargs)
        self.model = model

    def supports_thinking(self, model: str) -> bool:
        return _model_supports_thinking(model)

    def supports_adaptive_thinking(self, model: str) -> bool:
        return _model_supports_adaptive_thinking(model)

    def resolve_thinking_mode(self, thinking_enabled: bool) -> str:
        if not thinking_enabled:
            return "disabled"
        if not _model_supports_thinking(self.model):
            return "disabled"
        if _model_supports_adaptive_thinking(self.model):
            return "adaptive"
        return "enabled"

    async def call(
        self,
        *,
        messages: list[dict],
        system: str,
        tools: list[dict],
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
        thinking_mode: str = "disabled",
    ) -> BackendResponse:
        """流式调用 Anthropic API，返回 BackendResponse。"""

        async def _do():
            max_output = _get_max_output_tokens(self.model)
            create_params: dict[str, Any] = {
                "model": self.model,
                "max_tokens": max_output if thinking_mode != "disabled" else DEFAULT_MAX_TOKENS,
                "system": system,
                "tools": tools,
                "messages": messages,
            }

            if thinking_mode in ("adaptive", "enabled"):
                create_params["thinking"] = {"type": "enabled", "budget_tokens": max_output - 1}

            tool_blocks_by_index: dict[int, dict] = {}
            completed_tool_blocks: list[dict] = []

            async with self.client.messages.stream(**create_params) as stream:
                async for event in stream:
                    if not hasattr(event, "type"):
                        continue

                    if event.type == "content_block_start":
                        content_block = getattr(event, "content_block", None)
                        if content_block and getattr(content_block, "type", None) == "tool_use":
                            tool_blocks_by_index[event.index] = {
                                "id": content_block.id,
                                "name": content_block.name,
                                "input_json": "",
                            }

                    elif event.type == "content_block_delta":
                        delta = event.delta
                        if hasattr(delta, "text"):
                            if on_text_delta:
                                await on_text_delta(delta.text)
                        elif hasattr(delta, "partial_json"):
                            tool_block = tool_blocks_by_index.get(event.index)
                            if tool_block:
                                tool_block["input_json"] += delta.partial_json

                    elif event.type == "content_block_stop":
                        tool_block = tool_blocks_by_index.pop(event.index, None)
                        if tool_block:
                            try:
                                parsed = json.loads(tool_block["input_json"] or "{}")
                            except Exception:
                                parsed = {}
                            completed_tool_blocks.append({
                                "type": "tool_use",
                                "id": tool_block["id"],
                                "name": tool_block["name"],
                                "input": parsed,
                            })

                final_message = await stream.get_final_message()

            # thinking block 只用于展示，不进入历史
            final_message.content = [b for b in final_message.content if b.type != "thinking"]

            tool_calls = []
            for block in final_message.content:
                if block.type == "tool_use":
                    tool_calls.append(ToolCall(
                        id=block.id,
                        name=block.name,
                        input=dict(block.input) if hasattr(block.input, "items") else block.input,
                        provider="anthropic",
                    ))

            text = "".join(b.text for b in final_message.content if b.type == "text")

            return BackendResponse(
                text=text,
                tool_calls=tool_calls,
                usage=TokenUsage(
                    input_tokens=_usage_value(final_message.usage, "input_tokens", "prompt_tokens"),
                    output_tokens=_usage_value(final_message.usage, "output_tokens", "completion_tokens"),
                    input_cache_hit_tokens=_usage_value(
                        final_message.usage,
                        "prompt_cache_hit_tokens",
                        "input_cache_hit_tokens",
                        "cache_hit_input_tokens",
                        "cache_read_input_tokens",
                    ),
                    input_cache_miss_tokens=_usage_value(
                        final_message.usage,
                        "prompt_cache_miss_tokens",
                        "input_cache_miss_tokens",
                        "cache_miss_input_tokens",
                        "cache_creation_input_tokens",
                    ),
                ),
            )

        return cast(BackendResponse, await _with_retry(_do))

    @staticmethod
    def block_to_dict(block) -> dict:
        """将 Anthropic 内容块转为普通字典，便于存储和会话恢复。"""
        if block.type == "text":
            return {"type": "text", "text": block.text}
        if block.type == "tool_use":
            return {
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": dict(block.input) if hasattr(block.input, "items") else block.input,
            }
        return {"type": block.type}
