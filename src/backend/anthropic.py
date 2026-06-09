"""Anthropic Messages API 流式后端。

从原 AgentBackendMixin._call_anthropic_stream 提取，改为独立策略类。
不依赖 Agent 实例，所有数据通过参数传入。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

import anthropic

from ..capabilities.tools.types import DEFAULT_MAX_TOKENS, ToolCall
from ..models import (
    get_max_output_tokens,
    model_supports_adaptive_thinking,
    model_supports_thinking,
    with_retry,
)
from .base import Backend, BackendResponse, TokenUsage


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
        return model_supports_thinking(model)

    def supports_adaptive_thinking(self, model: str) -> bool:
        return model_supports_adaptive_thinking(model)

    def resolve_thinking_mode(self, thinking_enabled: bool) -> str:
        if not thinking_enabled:
            return "disabled"
        if not model_supports_thinking(self.model):
            return "disabled"
        if model_supports_adaptive_thinking(self.model):
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
            max_output = get_max_output_tokens(self.model)
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
                    input_tokens=final_message.usage.input_tokens,
                    output_tokens=final_message.usage.output_tokens,
                ),
            )

        return cast(BackendResponse, await with_retry(_do))

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

