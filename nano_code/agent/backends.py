"""Agent 的模型后端循环。

本模块负责“如何和模型 API 对话”。它不决定 Agent 有哪些状态，也不实现
工具本身，而是把一次用户请求变成多轮模型调用：

1. 把用户消息加入对应后端的消息历史。
2. 请求模型并流式打印文本。
3. 解析模型返回的 tool call。
4. 调用 `tools_runtime.py` 执行工具。
5. 把 tool result 写回消息历史，继续下一轮。

这里同时维护 Anthropic 和 OpenAI-compatible 两种协议，因为它们的工具消息格式
不同。Anthropic 使用 `tool_use/tool_result` 内容块；OpenAI 使用
`assistant.tool_calls` 和 `role=tool` 消息。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from .models import _get_max_output_tokens, _to_openai_tools, _with_retry
from ..tools import check_permission
from ..ui import (
    print_cost,
    print_info,
    print_tool_call,
    print_tool_result,
    start_spinner,
    stop_spinner,
)


class AgentBackendMixin:
    """给 `Agent` 增加 Anthropic / OpenAI-compatible 后端循环。

    依赖 `Agent` 上的状态：
    `_anthropic_client`、`_openai_client`、两套消息历史、token 计数、
    `_thinking_mode`、`permission_mode`、`_confirmed_paths`。

    调用其他 mixin 提供的能力：
    上下文压缩来自 `AgentContextMixin`，工具执行来自 `AgentToolRuntimeMixin`。
    """

    # ─── Anthropic 后端 ─────────────────────────────────

    async def _chat_anthropic(self, user_message: str) -> None:
        self._anthropic_messages.append({"role": "user", "content": user_message})
        # 只在回合边界自动压缩，此时最后一条消息一定是普通用户文本。
        await self._check_and_compact()

        memory_prefetch = self._start_memory_prefetch(user_message)

        while True:
            if self._aborted:
                break

            self._run_compression_pipeline()
            self._consume_memory_prefetch(memory_prefetch)

            if not self.is_sub_agent:
                start_spinner()

            # Anthropic 流式事件能在 tool_use block 完成时提前启动只读工具。
            early_executions: dict[str, asyncio.Task] = {}

            def _on_tool_block(block: dict):
                if self._tool_registry.is_concurrency_safe(block["name"], block["input"]):
                    perm = check_permission(
                        block["name"],
                        block["input"],
                        mode=self.permission_mode,
                        metadata=self._tool_registry.metadata_for(block["name"]),
                    )
                    if perm.action == "allow":
                        task = asyncio.create_task(self._execute_tool_call(block["name"], block["input"]))
                        early_executions[block["id"]] = task

            response = await self._call_anthropic_stream(on_tool_block_complete=_on_tool_block)

            if not self.is_sub_agent:
                stop_spinner()

            self.last_api_call_time = time.time()
            self.total_input_tokens += response.usage.input_tokens
            self.total_output_tokens += response.usage.output_tokens
            self.last_input_token_count = response.usage.input_tokens

            tool_uses = [b for b in response.content if b.type == "tool_use"]

            self._anthropic_messages.append({
                "role": "assistant",
                "content": [self._block_to_dict(b) for b in response.content],
            })

            if not tool_uses:
                if not self.is_sub_agent:
                    print_cost(self.total_input_tokens, self.total_output_tokens)
                break

            self.current_turns += 1
            budget = self._check_budget()
            if budget["exceeded"]:
                print_info(f"Budget exceeded: {budget['reason']}")
                break

            tool_results: list[dict] = []
            for tool_use in tool_uses:
                if self._aborted:
                    break
                inp = dict(tool_use.input) if hasattr(tool_use.input, "items") else tool_use.input
                print_tool_call(tool_use.name, inp)

                early_task = early_executions.get(tool_use.id)
                if early_task:
                    raw = await early_task
                    res = self._persist_large_result(tool_use.name, raw)
                    print_tool_result(tool_use.name, res)
                    tool_results.append({"type": "tool_result", "tool_use_id": tool_use.id, "content": res})
                    continue

                perm = check_permission(
                    tool_use.name,
                    inp,
                    mode=self.permission_mode,
                    metadata=self._tool_registry.metadata_for(tool_use.name),
                )
                if perm.action == "deny":
                    print_info(f"Denied: {perm.message}")
                    tool_results.append({"type": "tool_result", "tool_use_id": tool_use.id, "content": f"Action denied: {perm.message}"})
                    continue
                if perm.action == "confirm" and perm.message and perm.message not in self._confirmed_paths:
                    confirmed = await self._confirm_dangerous(perm.message)
                    if not confirmed:
                        tool_results.append({"type": "tool_result", "tool_use_id": tool_use.id, "content": "User denied this action."})
                        continue
                    self._confirmed_paths.add(perm.message)

                raw = await self._execute_tool_call(tool_use.name, inp)
                res = self._persist_large_result(tool_use.name, raw)
                print_tool_result(tool_use.name, res)

                tool_results.append({"type": "tool_result", "tool_use_id": tool_use.id, "content": res})

            if tool_results:
                self._anthropic_messages.append({"role": "user", "content": tool_results})

    @staticmethod
    def _block_to_dict(block) -> dict:
        """将 Anthropic 内容块转为普通字典，便于存储和会话恢复。"""
        if block.type == "text":
            return {"type": "text", "text": block.text}
        if block.type == "tool_use":
            return {"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input) if hasattr(block.input, "items") else block.input}
        return {"type": block.type}

    async def _call_anthropic_stream(self, on_tool_block_complete=None):
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
                                stop_spinner()
                                self._emit_text("\n")
                                first_text = False
                            self._emit_text(delta.text)
                        elif hasattr(delta, "thinking"):
                            if first_text:
                                stop_spinner()
                                self._emit_text("\n  [thinking] ")
                                first_text = False
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

    async def _chat_openai(self, user_message: str) -> None:
        self._openai_messages.append({"role": "user", "content": user_message})
        await self._check_and_compact()

        memory_prefetch = self._start_memory_prefetch(user_message)

        while True:
            if self._aborted:
                break

            self._run_compression_pipeline()
            self._consume_memory_prefetch(memory_prefetch)

            if not self.is_sub_agent:
                start_spinner()

            response = await self._call_openai_stream()

            if not self.is_sub_agent:
                stop_spinner()

            self.last_api_call_time = time.time()

            if response.get("usage"):
                self.total_input_tokens += response["usage"]["prompt_tokens"]
                self.total_output_tokens += response["usage"]["completion_tokens"]
                self.last_input_token_count = response["usage"]["prompt_tokens"]

            choice = response.get("choices", [{}])[0] if response.get("choices") else {}
            message = choice.get("message", {})

            self._openai_messages.append(message)

            tool_calls = message.get("tool_calls")
            if not tool_calls:
                if not self.is_sub_agent:
                    print_cost(self.total_input_tokens, self.total_output_tokens)
                break

            self.current_turns += 1
            budget = self._check_budget()
            if budget["exceeded"]:
                print_info(f"Budget exceeded: {budget['reason']}")
                break

            # 阶段 1：解析并做权限检查（串行），让确认提示顺序稳定。
            oai_checked: list[dict] = []
            for tool_call in tool_calls:
                if self._aborted:
                    break
                if tool_call.get("type") != "function":
                    continue
                fn_name = tool_call["function"]["name"]
                try:
                    inp = json.loads(tool_call["function"]["arguments"])
                except Exception:
                    inp = {}

                print_tool_call(fn_name, inp)

                perm = check_permission(
                    fn_name,
                    inp,
                    mode=self.permission_mode,
                    metadata=self._tool_registry.metadata_for(fn_name),
                )
                if perm.action == "deny":
                    print_info(f"Denied: {perm.message}")
                    oai_checked.append({"tc": tool_call, "fn": fn_name, "inp": inp, "allowed": False, "result": f"Action denied: {perm.message}"})
                    continue
                if perm.action == "confirm" and perm.message and perm.message not in self._confirmed_paths:
                    confirmed = await self._confirm_dangerous(perm.message)
                    if not confirmed:
                        oai_checked.append({"tc": tool_call, "fn": fn_name, "inp": inp, "allowed": False, "result": "User denied this action."})
                        continue
                    self._confirmed_paths.add(perm.message)
                oai_checked.append({"tc": tool_call, "fn": fn_name, "inp": inp, "allowed": True})

            # 阶段 2：连续的只读安全工具并行执行，其余保持串行。
            oai_batches: list[dict] = []
            for checked in oai_checked:
                safe = checked["allowed"] and self._tool_registry.is_concurrency_safe(checked["fn"], checked["inp"])
                if safe and oai_batches and oai_batches[-1]["concurrent"]:
                    oai_batches[-1]["items"].append(checked)
                else:
                    oai_batches.append({"concurrent": safe, "items": [checked]})

            for batch in oai_batches:
                if self._aborted:
                    break

                if batch["concurrent"]:

                    async def _run_oai_safe(checked_item: dict) -> tuple[dict, str]:
                        raw = await self._execute_tool_call(checked_item["fn"], checked_item["inp"])
                        res = self._persist_large_result(checked_item["fn"], raw)
                        print_tool_result(checked_item["fn"], res)
                        return checked_item, res

                    results = await asyncio.gather(*[_run_oai_safe(checked) for checked in batch["items"]])
                    for checked_item, res in results:
                        self._openai_messages.append({"role": "tool", "tool_call_id": checked_item["tc"]["id"], "content": res})
                else:
                    for checked in batch["items"]:
                        if not checked["allowed"]:
                            self._openai_messages.append({"role": "tool", "tool_call_id": checked["tc"]["id"], "content": checked["result"]})
                            continue
                        raw = await self._execute_tool_call(checked["fn"], checked["inp"])
                        res = self._persist_large_result(checked["fn"], raw)
                        print_tool_result(checked["fn"], res)

                        self._openai_messages.append({"role": "tool", "tool_call_id": checked["tc"]["id"], "content": res})

    async def _call_openai_stream(self) -> dict:
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
                        stop_spinner()
                        self._emit_text("\n")
                        first_text = False
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
