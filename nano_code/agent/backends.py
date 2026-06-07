"""Model backend adapters.

This module only talks to Anthropic/OpenAI-compatible streaming APIs and
assembles provider-native final messages. The event loop, tool execution,
permission checks, hooks, UI rendering, and session persistence live outside
the backend adapter.
"""

from __future__ import annotations

import json
from typing import Any

from .models import _get_max_output_tokens, _to_openai_tools, _with_retry
from ..ui import stop_spinner


class AgentBackendMixin:
    """Streaming API adapters shared by the event-driven agent loop."""

    # ─── Anthropic 后端 ─────────────────────────────────

    @staticmethod
    def _block_to_dict(block) -> dict:
        """将 Anthropic 内容块转为普通字典，便于存储和会话恢复。"""
        if block.type == "text":
            return {"type": "text", "text": block.text}
        if block.type == "tool_use":
            return {"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input) if hasattr(block.input, "items") else block.input}
        return {"type": block.type}

    async def _call_anthropic_stream(
        self,
        on_tool_block_complete=None,
        on_text_delta=None,
        on_thinking_delta=None,
    ):
        """流式调用 Anthropic API，并在工具参数完整时回调给主循环。"""

        async def _do():
            max_output = _get_max_output_tokens(self.model)
            create_params: dict[str, Any] = {
                "model": self.model,
                "max_tokens": max_output if self._thinking_mode != "disabled" else 16384,
                "system": self._system_prompt,
                "tools": self._current_tool_definitions(),
                "messages": self._anthropic_messages,
            }

            if self._thinking_mode in ("adaptive", "enabled"):
                create_params["thinking"] = {"type": "enabled", "budget_tokens": max_output - 1}

            first_text = True
            tool_blocks_by_index: dict[int, dict] = {}

            async with self._anthropic_client.messages.stream(**create_params) as stream:
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
                            if first_text:
                                if on_text_delta:
                                    on_text_delta("\n")
                                else:
                                    stop_spinner()
                                    self._emit_text("\n")
                                first_text = False
                            if on_text_delta:
                                on_text_delta(delta.text)
                            else:
                                self._emit_text(delta.text)
                        elif hasattr(delta, "thinking"):
                            if first_text:
                                if on_thinking_delta:
                                    on_thinking_delta("\n  [thinking] ")
                                else:
                                    stop_spinner()
                                    self._emit_text("\n  [thinking] ")
                                first_text = False
                            if on_thinking_delta:
                                on_thinking_delta(delta.thinking)
                            else:
                                self._emit_text(delta.thinking)
                        elif hasattr(delta, "partial_json"):
                            tool_block = tool_blocks_by_index.get(event.index)
                            if tool_block:
                                tool_block["input_json"] += delta.partial_json

                    elif event.type == "content_block_stop":
                        tool_block = tool_blocks_by_index.pop(event.index, None)
                        if tool_block and on_tool_block_complete:
                            try:
                                parsed = json.loads(tool_block["input_json"] or "{}")
                            except Exception:
                                parsed = {}
                            on_tool_block_complete({
                                "type": "tool_use",
                                "id": tool_block["id"],
                                "name": tool_block["name"],
                                "input": parsed,
                            })

                final_message = await stream.get_final_message()

            # thinking block 只用于展示，不进入历史，避免后续 API 不接受。
            final_message.content = [b for b in final_message.content if b.type != "thinking"]
            return final_message

        return await _with_retry(_do)

    # ─── OpenAI 兼容后端 ───────────────────────────────

    async def _call_openai_stream(self, on_text_delta=None) -> dict:
        async def _do():
            stream = await self._openai_client.chat.completions.create(
                model=self.model,
                tools=_to_openai_tools(self._current_tool_definitions()),
                messages=self._openai_messages,
                stream=True,
                stream_options={"include_usage": True},
            )

            content = ""
            first_text = True
            tool_calls: dict[int, dict] = {}
            finish_reason = ""
            usage = None

            async for chunk in stream:
                if chunk.usage:
                    usage = {
                        "prompt_tokens": chunk.usage.prompt_tokens,
                        "completion_tokens": chunk.usage.completion_tokens,
                    }

                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if delta and delta.content:
                    if first_text:
                        if on_text_delta:
                            on_text_delta("\n")
                        else:
                            stop_spinner()
                            self._emit_text("\n")
                        first_text = False
                    if on_text_delta:
                        on_text_delta(delta.content)
                    else:
                        self._emit_text(delta.content)
                    content += delta.content

                if delta and delta.tool_calls:
                    for tool_call in delta.tool_calls:
                        existing = tool_calls.get(tool_call.index)
                        if existing:
                            if tool_call.function and tool_call.function.arguments:
                                existing["arguments"] += tool_call.function.arguments
                        else:
                            tool_calls[tool_call.index] = {
                                "id": tool_call.id or "",
                                "name": (tool_call.function.name if tool_call.function else "") or "",
                                "arguments": (tool_call.function.arguments if tool_call.function else "") or "",
                            }

                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason

            assembled = None
            if tool_calls:
                assembled = [
                    {"id": tool_call["id"], "type": "function", "function": {"name": tool_call["name"], "arguments": tool_call["arguments"]}}
                    for _, tool_call in sorted(tool_calls.items())
                ]

            return {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": content or None,
                        "tool_calls": assembled,
                    },
                    "finish_reason": finish_reason or "stop",
                }],
                "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0},
            }

        return await _with_retry(_do)
