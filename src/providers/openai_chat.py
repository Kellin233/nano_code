"""OpenAI-compatible chat completions provider adapter."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import openai

from ..runtime.agent.models import _to_openai_tools
from ..core import AssistantMessage, CoreToolCall, Message, ModelEvent, ModelTextDelta, ModelTurnComplete, ModelUsage
from .base import ProviderConfig


class OpenAIChatProvider:
    def __init__(self, config: ProviderConfig, client: openai.AsyncOpenAI | None = None):
        self.config = config
        kwargs: dict[str, Any] = {}
        if config.api_key:
            kwargs["api_key"] = config.api_key
        if config.base_url:
            kwargs["base_url"] = config.base_url
        self.client = client or openai.AsyncOpenAI(**kwargs)

    async def stream_turn(self, messages: list[Message]) -> AsyncIterator[ModelEvent]:
        stream = await self.client.chat.completions.create(
            model=self.config.model,
            messages=self._messages(messages),
            tools=_to_openai_tools(list(self.config.tools)),
            stream=True,
            stream_options={"include_usage": True},
        )
        content = ""
        usage = ModelUsage()
        finish_reason = "stop"
        tool_calls: dict[int, dict[str, str]] = {}

        async for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = ModelUsage(
                    input_tokens=int(chunk.usage.prompt_tokens or 0),
                    output_tokens=int(chunk.usage.completion_tokens or 0),
                )
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if getattr(delta, "content", None):
                content += delta.content
                yield ModelTextDelta(delta.content)
            for call_delta in getattr(delta, "tool_calls", None) or []:
                item = tool_calls.setdefault(
                    call_delta.index,
                    {"id": call_delta.id or "", "name": "", "arguments": ""},
                )
                if call_delta.id:
                    item["id"] = call_delta.id
                if call_delta.function:
                    if call_delta.function.name:
                        item["name"] = call_delta.function.name
                    if call_delta.function.arguments:
                        item["arguments"] += call_delta.function.arguments
            if choice.finish_reason:
                finish_reason = choice.finish_reason

        calls: list[CoreToolCall] = []
        provider_calls: list[dict[str, Any]] = []
        for _, raw in sorted(tool_calls.items()):
            try:
                inp = json.loads(raw["arguments"] or "{}")
            except Exception:
                inp = {}
            calls.append(CoreToolCall(
                id=raw["id"],
                name=raw["name"],
                input=inp if isinstance(inp, dict) else {},
                provider="openai",
            ))
            provider_calls.append({
                "id": raw["id"],
                "type": "function",
                "function": {"name": raw["name"], "arguments": raw["arguments"]},
            })

        message = {
            "role": "assistant",
            "content": content or None,
            "tool_calls": provider_calls or None,
        }
        yield ModelTurnComplete(
            message=AssistantMessage(content=message, tool_calls=calls, provider_message=message),
            usage=usage,
            stop_reason="tool_calls" if calls else ("stop" if finish_reason != "length" else "budget_exceeded"),
        )

    def _messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if self.config.system_prompt:
            result.append({"role": "system", "content": self.config.system_prompt})
        for message in messages:
            if message.role == "system":
                result.append({"role": "system", "content": str(message.content)})
            elif message.role == "tool":
                result.append({
                    "role": "tool",
                    "tool_call_id": message.tool_call_id or "",
                    "content": str(message.content),
                })
            else:
                result.append({"role": message.role, "content": message.content})
        return result
