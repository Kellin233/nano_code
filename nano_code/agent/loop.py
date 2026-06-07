"""Event-driven agent loop."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator

from ..hooks import HookInput
from ..tools.base import ToolCall, ToolContext, ToolResult
from ..tools.runtime import ToolRuntime
from .events import (
    AgentEvent,
    AssistantTextDelta,
    BudgetExceeded,
    LoopFinished,
    ToolCallFinished,
    ToolCallStarted,
)


class AgentLoop:
    def __init__(self, agent):
        self.agent = agent

    async def run(self, user_message: str) -> AsyncIterator[AgentEvent]:
        if self.agent.use_openai:
            async for event in self._run_openai(user_message):
                yield event
            return
        async for event in self._run_anthropic(user_message):
            yield event

    async def _run_anthropic(self, user_message: str) -> AsyncIterator[AgentEvent]:
        agent = self.agent
        agent._inject_startup_context_once()
        agent._prepare_initial_context_attachments()
        agent._flush_pending_context_attachments()
        agent._anthropic_messages.append({"role": "user", "content": user_message})
        await agent._check_and_compact()
        memory_prefetch = agent._start_memory_prefetch(user_message)

        while True:
            if agent._aborted:
                yield LoopFinished("aborted")
                return

            agent._run_compression_pipeline()
            agent._consume_memory_prefetch(memory_prefetch)

            queue: asyncio.Queue[AgentEvent] = asyncio.Queue()

            def _text(text: str) -> None:
                queue.put_nowait(AssistantTextDelta(text))

            task = asyncio.create_task(agent._call_anthropic_stream(on_text_delta=_text, on_thinking_delta=_text))
            while not task.done() or not queue.empty():
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    continue
            response = await task

            agent.last_api_call_time = __import__("time").time()
            agent.total_input_tokens += response.usage.input_tokens
            agent.total_output_tokens += response.usage.output_tokens
            agent.last_input_token_count = response.usage.input_tokens

            content = [agent._block_to_dict(block) for block in response.content]
            agent._anthropic_messages.append({"role": "assistant", "content": content})

            tool_uses = [block for block in response.content if block.type == "tool_use"]
            if not tool_uses:
                if await self._stop_blocked(_assistant_text_from_anthropic(content)):
                    continue
                yield LoopFinished("stop")
                return

            agent.current_turns += 1
            budget = agent._check_budget()
            if budget["exceeded"]:
                yield BudgetExceeded(budget["reason"])
                yield LoopFinished("budget_exceeded")
                return

            calls = [
                ToolCall(
                    id=tool_use.id,
                    name=tool_use.name,
                    input=dict(tool_use.input) if hasattr(tool_use.input, "items") else tool_use.input,
                    provider="anthropic",
                )
                for tool_use in tool_uses
            ]
            for call in calls:
                yield ToolCallStarted(call)
            events, results = await self._execute_tools(calls)
            for event in events:
                yield event
            tool_results = [
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": result.content,
                    **({"is_error": True} if result.is_error else {}),
                }
                for call, result in results
            ]
            if tool_results:
                agent._anthropic_messages.append({"role": "user", "content": tool_results})
            self._append_extra_context(results)
            agent._flush_pending_context_attachments()

    async def _run_openai(self, user_message: str) -> AsyncIterator[AgentEvent]:
        agent = self.agent
        agent._inject_startup_context_once()
        agent._prepare_initial_context_attachments()
        agent._flush_pending_context_attachments()
        agent._openai_messages.append({"role": "user", "content": user_message})
        await agent._check_and_compact()
        memory_prefetch = agent._start_memory_prefetch(user_message)

        while True:
            if agent._aborted:
                yield LoopFinished("aborted")
                return

            agent._run_compression_pipeline()
            agent._consume_memory_prefetch(memory_prefetch)

            queue: asyncio.Queue[AgentEvent] = asyncio.Queue()

            def _text(text: str) -> None:
                queue.put_nowait(AssistantTextDelta(text))

            task = asyncio.create_task(agent._call_openai_stream(on_text_delta=_text))
            while not task.done() or not queue.empty():
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    continue
            response = await task

            agent.last_api_call_time = __import__("time").time()
            if response.get("usage"):
                agent.total_input_tokens += response["usage"]["prompt_tokens"]
                agent.total_output_tokens += response["usage"]["completion_tokens"]
                agent.last_input_token_count = response["usage"]["prompt_tokens"]

            choice = response.get("choices", [{}])[0] if response.get("choices") else {}
            message = choice.get("message", {})
            agent._openai_messages.append(message)

            tool_calls = message.get("tool_calls")
            if not tool_calls:
                if await self._stop_blocked(message.get("content") or ""):
                    continue
                yield LoopFinished("stop")
                return

            agent.current_turns += 1
            budget = agent._check_budget()
            if budget["exceeded"]:
                yield BudgetExceeded(budget["reason"])
                yield LoopFinished("budget_exceeded")
                return

            calls: list[ToolCall] = []
            for tool_call in tool_calls:
                if tool_call.get("type") != "function":
                    continue
                try:
                    inp = json.loads(tool_call["function"]["arguments"])
                except Exception:
                    inp = {}
                calls.append(ToolCall(
                    id=tool_call["id"],
                    name=tool_call["function"]["name"],
                    input=inp,
                    provider="openai",
                ))

            for call in calls:
                yield ToolCallStarted(call)
            events, results = await self._execute_tools(calls)
            for event in events:
                yield event
            for call, result in results:
                agent._openai_messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result.content,
                })
            self._append_extra_context(results)
            agent._flush_pending_context_attachments()

    async def _execute_tools(self, calls: list[ToolCall]) -> tuple[list[AgentEvent], list[tuple[ToolCall, ToolResult]]]:
        agent = self.agent
        events: list[AgentEvent] = []

        runtime_events: list[AgentEvent] = []

        async def _capture(event: AgentEvent) -> None:
            runtime_events.append(event)

        runtime = ToolRuntime(
            agent._tool_registry,
            permission_mode=agent.permission_mode,
            confirm_fn=agent._confirm_dangerous,
            confirmed=agent._confirmed_paths,
            hooks=agent._hook_manager,
            event_callback=_capture,
        )
        ctx = ToolContext(
            cwd=Path.cwd(),
            session_id=agent.session_id,
            read_file_state=agent._read_file_state,
            sandbox_manager=agent._sandbox_manager,
            mcp_manager=agent._mcp_manager,
            agent=agent,
        )
        results = await runtime.execute_many(calls, ctx)
        events.extend(runtime_events)
        for call, result in results:
            events.append(ToolCallFinished(call, result))
        return events, results

    async def _stop_blocked(self, last_text: str) -> bool:
        agent = self.agent
        outputs = await agent._hook_manager.run(
            "Stop",
            HookInput(
                event="Stop",
                session_id=agent.session_id,
                cwd=str(Path.cwd()),
                last_assistant_text=last_text,
            ),
        )
        blocked = False
        for output in outputs:
            if output.action == "deny":
                agent._append_user_context(output.reason or output.error or "Stop hook requested continuation.")
                blocked = True
            elif output.action == "append_context" and output.content:
                agent._append_user_context(output.content)
                blocked = True
        return blocked

    def _append_extra_context(self, results: list[tuple[ToolCall, object]]) -> None:
        for _, result in results:
            for message in getattr(result, "extra_messages", []):
                content = message.get("content")
                if content:
                    self.agent._append_user_context(content)

def _assistant_text_from_anthropic(content: list[dict]) -> str:
    return "".join(block.get("text", "") for block in content if block.get("type") == "text")
