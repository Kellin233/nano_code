"""Provider-neutral agent loop.

The loop owns the LLM/tool state machine, but all capabilities are injected
by AgentSession. It does not import tools, hooks, Runtime Management, cli, tui, or
provider implementations.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .events import (
    AssistantTextDelta,
    BudgetExceeded,
    LoopFinished,
    RuntimeEvent,
    ToolCallFinished,
    ToolCallStarted,
)
from .types import ConversationHistory, ToolCall, ToolResult

ExecuteTools = Callable[[list[ToolCall]], Awaitable[tuple[list[RuntimeEvent], list[tuple[ToolCall, ToolResult]]]]]
CommitConversation = Callable[[str], Awaitable[None] | None]
PrepareContext = Callable[[], Awaitable["PreparedContext"]]


@dataclass(frozen=True)
class PreparedContext:
    conversation: ConversationHistory
    changed: bool = False
    reason: str = ""


class AgentLoop:
    """Backend-neutral LLM/tool loop."""

    def __init__(
        self,
        agent,
        backend: Any,
        *,
        execute_tools: ExecuteTools,
        prepare_context_for_provider: PrepareContext | None = None,
        apply_user_prompt_hooks: Callable[[str], Awaitable[str]] | None = None,
        run_stop_hook: Callable[[str], Awaitable[bool]] | None = None,
        commit_conversation: CommitConversation | None = None,
    ):
        self.agent = agent
        self.backend = backend
        self.execute_tools = execute_tools
        self.prepare_context_for_provider = prepare_context_for_provider
        self.apply_user_prompt_hooks = apply_user_prompt_hooks
        self.run_stop_hook = run_stop_hook
        self.commit_conversation = commit_conversation
        self._agent_started = False

    @property
    def use_openai(self) -> bool:
        return bool(self.agent.use_openai)

    async def run(self, user_message: str) -> AsyncIterator[RuntimeEvent]:
        agent = self.agent
        agent.reset_abort()

        if not self._agent_started:
            self._agent_started = True
            await agent.emit(agent._on_agent_start, RuntimeEvent("agent.start", {"session_id": agent.session_id}))

        await agent.emit(agent._on_turn_start, RuntimeEvent("turn.start", {"text": user_message}))

        try:
            prompt = user_message
            if self.apply_user_prompt_hooks is not None:
                prompt = await self.apply_user_prompt_hooks(user_message)

            agent.inject_startup_context()
            agent.prepare_initial_attachments()
            agent.flush_pending_attachments()
            agent.add_user_message(prompt)
            await self._commit_conversation("user_accepted")

            await agent.ensure_mcp_initialized()

            while True:
                if agent.aborted:
                    yield LoopFinished("aborted")
                    return

                request_conversation = agent.conversation
                if self.prepare_context_for_provider is not None:
                    prepared = await self.prepare_context_for_provider()
                    request_conversation = prepared.conversation
                    if prepared.changed:
                        await self._commit_conversation(prepared.reason or "context_prepared")

                thinking_mode = self.backend.resolve_thinking_mode(agent.thinking)
                try:
                    text_events: asyncio.Queue[RuntimeEvent] = asyncio.Queue()

                    async def on_text_delta(text: str, queue: asyncio.Queue[RuntimeEvent] = text_events) -> None:
                        await queue.put(AssistantTextDelta(text))

                    call_task = asyncio.create_task(
                        self.backend.call(
                            conversation=request_conversation,
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
                agent.record_usage(
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                    input_cache_hit_tokens=response.usage.input_cache_hit_tokens,
                    input_cache_miss_tokens=response.usage.input_cache_miss_tokens,
                )

                self._append_assistant_message(response)
                await self._commit_conversation("assistant_final")

                if not response.tool_calls:
                    if self.run_stop_hook is not None and await self.run_stop_hook(response.text):
                        await self._commit_conversation("stop_hook_context")
                        continue
                    yield LoopFinished("stop")
                    return

                agent.current_turns += 1
                budget = agent.budget_exceeded()
                if budget["exceeded"]:
                    yield BudgetExceeded(budget["reason"])
                    yield LoopFinished("budget_exceeded")
                    return

                for call in response.tool_calls:
                    yield ToolCallStarted(call)

                events, results = await self.execute_tools(response.tool_calls)
                for event in events:
                    yield event

                for call, result in results:
                    yield ToolCallFinished(call, result)

                self._append_tool_results(results)
                for _, result in results:
                    for msg in getattr(result, "extra_messages", []):
                        content = msg.get("content")
                        if content:
                            agent.append_user_context(content)

                agent.flush_pending_attachments()
                await self._commit_conversation("tool_results")
        finally:
            await agent.emit(agent._on_turn_end, RuntimeEvent("turn.end", {"text": user_message}))

    def _append_assistant_message(self, response) -> None:
        self.agent.add_assistant_message(response.text or "", response.tool_calls)

    def _append_tool_results(self, results: list[tuple[ToolCall, ToolResult]]) -> None:
        self.agent.add_tool_results(results)

    async def _commit_conversation(self, reason: str) -> None:
        if self.commit_conversation is None:
            return
        result = self.commit_conversation(reason)
        if hasattr(result, "__await__"):
            await result  # type: ignore[misc]
