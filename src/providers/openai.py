"""OpenAI Chat Completions 兼容流式后端。

从原 AgentBackendMixin._call_openai_stream 提取，改为独立策略类。
不依赖 Agent 实例，所有数据通过参数传入。
"""

from __future__ import annotations

import json
import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast

import openai

from ..agent.types import MAX_RETRIES, MAX_RETRY_DELAY_MS, ToolCall, ToolDef
from .base import Backend, BackendResponse, TokenUsage


def _usage_value(usage, *names: str) -> int:
    """Read a token usage value from OpenAI usage objects or dicts."""
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


def _to_openai_tools(tools: list[ToolDef]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


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


class OpenAIBackend(Backend):
    """OpenAI Chat Completions 兼容流式后端。

    不依赖 Agent 实例——模型、消息历史、工具定义
    通过 call() 的参数传入。
    """

    def __init__(self, api_key: str, base_url: str, model: str):
        self.client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    def supports_thinking(self, model: str) -> bool:
        return _model_supports_thinking(model)

    def supports_adaptive_thinking(self, model: str) -> bool:
        return _model_supports_adaptive_thinking(model)

    async def call(
        self,
        *,
        messages: list[dict],
        system: str,
        tools: list[dict],
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
        thinking_mode: str = "disabled",
    ) -> BackendResponse:
        """流式调用 OpenAI API，返回 BackendResponse。"""

        async def _do():
            stream = await self.client.chat.completions.create(
                model=self.model,
                tools=_to_openai_tools(tools),
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
            )

            content = ""
            tool_calls_map: dict[int, dict] = {}
            usage = None

            async for chunk in stream:
                if chunk.usage:
                    usage = chunk.usage

                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if delta and delta.content:
                    if on_text_delta:
                        await on_text_delta(delta.content)
                    content += delta.content

                if delta and delta.tool_calls:
                    for tool_call in delta.tool_calls:
                        existing = tool_calls_map.get(tool_call.index)
                        if existing:
                            if tool_call.function and tool_call.function.arguments:
                                existing["arguments"] += tool_call.function.arguments
                        else:
                            tool_calls_map[tool_call.index] = {
                                "id": tool_call.id or "",
                                "name": (tool_call.function.name if tool_call.function else "") or "",
                                "arguments": (tool_call.function.arguments if tool_call.function else "") or "",
                            }

            assembled_tool_calls: list[ToolCall] = []
            if tool_calls_map:
                for idx in sorted(tool_calls_map):
                    tc = tool_calls_map[idx]
                    try:
                        inp = json.loads(tc["arguments"])
                    except Exception:
                        inp = {}
                    assembled_tool_calls.append(ToolCall(
                        id=tc["id"],
                        name=tc["name"],
                        input=inp,
                        provider="openai",
                    ))

            return BackendResponse(
                text=content,
                tool_calls=assembled_tool_calls,
                usage=TokenUsage(
                    input_tokens=_usage_value(usage, "prompt_tokens", "input_tokens"),
                    output_tokens=_usage_value(usage, "completion_tokens", "output_tokens"),
                    input_cache_hit_tokens=_usage_value(
                        usage,
                        "prompt_cache_hit_tokens",
                        "input_cache_hit_tokens",
                        "cache_hit_input_tokens",
                    ),
                    input_cache_miss_tokens=_usage_value(
                        usage,
                        "prompt_cache_miss_tokens",
                        "input_cache_miss_tokens",
                        "cache_miss_input_tokens",
                    ),
                ),
            )

        return cast(BackendResponse, await _with_retry(_do))
