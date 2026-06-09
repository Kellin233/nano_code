"""主对话循环 — 后端无关的事件驱动循环。

通过 Backend 接口调用模型，不区分 Anthropic / OpenAI 差异。
消除了原 agent/loop.py 中 _run_anthropic 和 _run_openai 的重复代码。

流程：
  用户输入 → 注入上下文 → 记忆召回 → [模型调用 → 解析响应 → 执行工具] × N
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator

from ..backend.base import Backend
from ..capabilities.hooks.types import HookInput
from ..capabilities.tools.runtime import ToolRuntime
from ..capabilities.tools.types import ToolCall, ToolContext, ToolResult
from .events import (
    AssistantTextDelta,
    BudgetExceeded,
    LoopFinished,
    RuntimeEvent,
    ToolCallFinished,
    ToolCallStarted,
)


class AgentLoop:
    """后端无关的主对话循环。

    通过 Backend 接口调用模型，不区分 Anthropic / OpenAI。
    从 Agent 状态容器读取数据，产出 RuntimeEvent 流。
    """

    def __init__(self, agent, backend: Backend):
        self.agent = agent
        self.backend = backend

    @property
    def use_openai(self) -> bool:
        return bool(self.agent.config.use_openai)

    async def run(self, user_message: str) -> AsyncIterator[RuntimeEvent]:
        """执行一次完整对话轮次，产出 RuntimeEvent 流。"""
        agent = self.agent

        # 1. 注入启动上下文（仅首次）
        agent.inject_startup_context()

        # 2. 准备初始附件（仅首次）
        agent.prepare_initial_attachments()

        # 3. 刷新挂起的附件
        agent.flush_pending_attachments()

        # 4. 添加用户消息
        agent.add_user_message(user_message)

        # 5. 确保 MCP 初始化
        await agent.ensure_mcp_initialized()

        # 6. 检查是否需要 compact
        await self._check_and_compact()

        # 7. 用户 prompt hooks
        prompt = await self._apply_user_prompt_hooks(user_message)

        # 8. 启动记忆预取
        memory_prefetch = agent.start_memory_prefetch(user_message)

        # 9. 主循环
        while True:
            if agent.aborted:
                yield LoopFinished("aborted")
                return

            # 压缩流水线
            self._run_compression_pipeline()

            # 消费记忆预取
            agent.consume_memory_prefetch(memory_prefetch)

            # 调用模型
            thinking_mode = self.backend.resolve_thinking_mode(agent.thinking)
            try:
                text_events: asyncio.Queue[RuntimeEvent] = asyncio.Queue()

                async def on_text_delta(text: str, queue: asyncio.Queue[RuntimeEvent] = text_events) -> None:
                    await queue.put(AssistantTextDelta(text))

                call_task = asyncio.create_task(
                    self.backend.call(
                        messages=agent.messages,
                        system=agent.system_prompt,
                        tools=agent.tool_definitions(),
                        on_text_delta=on_text_delta,
                        thinking_mode=thinking_mode,
                    )
                )
                agent._current_task = call_task

                while not call_task.done():
                    try:
                        event = await asyncio.wait_for(text_events.get(), timeout=0.05)
                    except asyncio.TimeoutError:
                        if agent.aborted:
                            call_task.cancel()
                            break
                        continue
                    yield event
                    if agent.aborted:
                        call_task.cancel()
                        break

                while not text_events.empty():
                    yield text_events.get_nowait()

                if agent.aborted:
                    call_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await call_task
                    yield LoopFinished("aborted")
                    return

                response = await call_task
            except Exception as exc:
                yield RuntimeEvent(type="runtime.error", payload={"message": str(exc)})
                yield LoopFinished("error")
                return
            finally:
                if getattr(agent, "_current_task", None) is locals().get("call_task"):
                    agent._current_task = None

            agent.last_api_call_time = time.time()
            agent.record_usage(response.usage.input_tokens, response.usage.output_tokens)

            # 添加 assistant 消息
            self._append_assistant_message(response)

            # 没有工具调用 → 检查 Stop hook
            if not response.tool_calls:
                if await self._run_stop_hook(response.text):
                    continue
                yield LoopFinished("stop")
                return

            # 预算检查
            agent.current_turns += 1
            budget = agent.budget_exceeded()
            if budget["exceeded"]:
                yield BudgetExceeded(budget["reason"])
                yield LoopFinished("budget_exceeded")
                return

            # 发出 ToolCallStarted 事件
            for call in response.tool_calls:
                yield ToolCallStarted(call)

            # 执行工具
            events, results = await self._execute_tools(response.tool_calls)
            for event in events:
                yield event

            # 发出 ToolCallFinished 事件
            for call, result in results:
                yield ToolCallFinished(call, result)

            # 追加工具结果到消息历史
            self._append_tool_results(results)

            # 追加额外上下文（如 PostToolUse hook 输出）
            for _, result in results:
                for msg in getattr(result, "extra_messages", []):
                    content = msg.get("content")
                    if content:
                        agent.append_user_context(content)

            # 刷新附件
            agent.flush_pending_attachments()

    def _append_assistant_message(self, response) -> None:
        """将 BackendResponse 的内容追加到消息历史。"""
        if self.use_openai:
            # OpenAI 格式
            tool_calls = None
            if response.tool_calls:
                tool_calls = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": ""},
                    }
                    for tc in response.tool_calls
                ]
            msg: dict = {"role": "assistant", "content": response.text or None}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            self.agent._openai_messages.append(msg)
        else:
            # Anthropic 格式
            content: list[dict] = []
            if response.text:
                content.append({"type": "text", "text": response.text})
            for tc in response.tool_calls:
                content.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.input,
                })
            self.agent._anthropic_messages.append({"role": "assistant", "content": content})

    def _append_tool_results(self, results: list[tuple]) -> None:
        """将工具执行结果追加到消息历史。"""
        if self.use_openai:
            for call, result in results:
                self.agent._openai_messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result.content,
                })
        else:
            tool_results = []
            for call, result in results:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": result.content,
                    **({"is_error": True} if result.is_error else {}),
                })
            if tool_results:
                self.agent._anthropic_messages.append({"role": "user", "content": tool_results})

    async def _execute_tools(
        self, calls: list[ToolCall]
    ) -> tuple[list[RuntimeEvent], list[tuple[ToolCall, ToolResult]]]:
        """执行工具管线：验证 → 权限 → 执行 → 后处理。"""
        agent = self.agent
        events: list[RuntimeEvent] = []

        async def capture(event_obj) -> None:
            events.append(event_obj)

        runtime = ToolRuntime(
            agent._tool_registry,
            permission_mode=agent.permission_mode,
            confirm_fn=agent._confirm_dangerous,
            confirmed=agent._confirmed_paths,
            hooks=agent._hook_manager,
            event_callback=capture,
        )

        ctx = ToolContext(
            cwd=agent.config.workspace,
            session_id=agent.session_id,
            read_file_state=agent._read_file_state,
            sandbox_manager=agent._sandbox_manager,
            mcp_manager=agent._mcp_manager,
            agent=agent,
        )

        return events, await runtime.execute_many(calls, ctx)

    # ─── 压缩 ─────────────────────────────────────

    def _run_compression_pipeline(self) -> None:
        """三层压缩流水线。委托给 Compressor 模块。"""
        from .compressor import Compressor
        Compressor(self.agent).run_pipeline()

    async def _check_and_compact(self) -> None:
        """上下文窗口接近上限时执行 compact。"""
        from ..capabilities.tools.types import COMPACT_UTILIZATION_THRESHOLD
        from ..tui.renderer import get_renderer

        if self.agent.last_input_token_count > self.agent.effective_window * COMPACT_UTILIZATION_THRESHOLD:
            get_renderer().info("Context window filling up, compacting conversation...")
            from .compressor import Compressor
            await Compressor(self.agent).compact_conversation()

    # ─── Hooks ────────────────────────────────────

    async def _apply_user_prompt_hooks(self, user_message: str) -> str:
        agent = self.agent
        hook_input = HookInput(
            event="UserPromptSubmit",
            session_id=agent.session_id,
            cwd=str(agent.config.workspace),
            prompt=user_message,
        )
        prompt = user_message
        for output in await agent._hook_manager.run("UserPromptSubmit", hook_input):
            if output.action == "deny":
                reason = output.reason or output.error or "User prompt denied by hook."
                return f"[UserPromptSubmit hook blocked the original prompt]\n{reason}"
            if output.action == "append_context" and output.content:
                prompt += "\n\n" + output.content
            if output.action == "modify" and output.updated_input and "prompt" in output.updated_input:
                prompt = str(output.updated_input["prompt"])
        return prompt

    async def _run_stop_hook(self, last_text: str) -> bool:
        """执行 Stop hook。返回 True 表示 hook 要求继续对话。"""
        agent = self.agent
        outputs = await agent._hook_manager.run(
            "Stop",
            HookInput(
                event="Stop",
                session_id=agent.session_id,
                cwd=str(agent.config.workspace),
                last_assistant_text=last_text,
            ),
        )
        blocked = False
        for output in outputs:
            if output.action == "deny":
                agent.append_user_context(output.reason or output.error or "Stop hook requested continuation.")
                blocked = True
            elif output.action == "append_context" and output.content:
                agent.append_user_context(output.content)
                blocked = True
        return blocked
