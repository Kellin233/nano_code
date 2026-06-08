"""Anthropic streaming provider adapter."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from ..runtime.agent.models import _get_max_output_tokens, _model_supports_adaptive_thinking, _model_supports_thinking
from ..core import AssistantMessage, CoreToolCall, Message, ModelEvent, ModelTextDelta, ModelTurnComplete, ModelUsage
from .base import ProviderConfig


class AnthropicProvider:
    def __init__(self, config: ProviderConfig, client: anthropic.AsyncAnthropic | None = None):
        self.config = config
        kwargs: dict[str, Any] = {}
        if config.api_key:
            kwargs["api_key"] = config.api_key
        if config.base_url:
            kwargs["base_url"] = config.base_url
        self.client = client or anthropic.AsyncAnthropic(**kwargs)

    async def stream_turn(self, messages: list[Message]) -> AsyncIterator[ModelEvent]:
        tool_blocks: dict[int, dict[str, str]] = {}
        create_params: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self._max_tokens(),
            "system": self.config.system_prompt,
            "tools": list(self.config.tools),
            "messages": [self._to_provider_message(message) for message in messages if message.role != "system"],
        }
        thinking = self._thinking_mode()
        if thinking != "disabled":
            create_params["thinking"] = {"type": "enabled", "budget_tokens": max(_get_max_output_tokens(self.config.model) - 1, 1024)}

        async with self.client.messages.stream(**create_params) as stream:
            async for event in stream:
                if not hasattr(event, "type"):
                    continue
                if event.type == "content_block_start":
                    block = getattr(event, "content_block", None)
                    if getattr(block, "type", None) == "tool_use":
                        tool_blocks[event.index] = {"id": block.id, "name": block.name, "input_json": ""}
                elif event.type == "content_block_delta":
                    delta = event.delta
                    if hasattr(delta, "text"):
                        yield ModelTextDelta(delta.text)
                    elif hasattr(delta, "partial_json") and event.index in tool_blocks:
                        tool_blocks[event.index]["input_json"] += delta.partial_json

            final = await stream.get_final_message()

        content = [self._block_to_dict(block) for block in final.content if getattr(block, "type", "") != "thinking"]
        calls: list[CoreToolCall] = []
        for block in content:
            if block.get("type") != "tool_use":
                continue
            inp = block.get("input")
            calls.append(CoreToolCall(
                id=str(block.get("id", "")),
                name=str(block.get("name", "")),
                input=inp if isinstance(inp, dict) else {},
                provider="anthropic",
            ))
        usage = getattr(final, "usage", None)
        yield ModelTurnComplete(
            message=AssistantMessage(content=content, tool_calls=calls, provider_message=final),
            usage=ModelUsage(
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            ),
            stop_reason="tool_calls" if calls else "stop",
        )

    def _thinking_mode(self) -> str:
        if not self.config.thinking or not _model_supports_thinking(self.config.model):
            return "disabled"
        if _model_supports_adaptive_thinking(self.config.model):
            return "adaptive"
        return "enabled"

    def _max_tokens(self) -> int:
        if self._thinking_mode() == "disabled":
            return 16384
        return _get_max_output_tokens(self.config.model)

    def _to_provider_message(self, message: Message) -> dict[str, Any]:
        if message.role == "tool":
            return {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id or "",
                    "content": str(message.content),
                }],
            }
        return {"role": message.role, "content": message.content}

    def _block_to_dict(self, block: Any) -> dict[str, Any]:
        if block.type == "text":
            return {"type": "text", "text": block.text}
        if block.type == "tool_use":
            raw_input = block.input
            if isinstance(raw_input, str):
                try:
                    raw_input = json.loads(raw_input)
                except Exception:
                    raw_input = {}
            return {
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": dict(raw_input) if hasattr(raw_input, "items") else {},
            }
        return {"type": block.type}
