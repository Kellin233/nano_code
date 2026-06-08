"""RuntimeThread is the public turn execution entrypoint."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

from ..domains.skills import SkillInvocationResult
from .agent import Agent
from .agent.events import (
    AgentEvent,
    ApiRetry,
    AssistantTextDelta,
    BudgetExceeded,
    ContextCompacted,
    LoopFinished,
    PermissionRequested,
    ToolCallFinished,
    ToolCallStarted,
)
from ..session import ArtifactStore, SessionEventStore
from ..tui.renderer import get_renderer
from .approvals import ApprovalManager, ConfirmFn
from .capability import CapabilityContext, CapabilityManager
from .config import RuntimeConfig
from .events import RuntimeEvent, TurnResult


class RuntimeThread:
    """Own one conversation thread and expose runtime events to every client."""

    def __init__(
        self,
        config: RuntimeConfig,
        *,
        thread_id: str | None = None,
        capability_manager: CapabilityManager | None = None,
        event_store: SessionEventStore | None = None,
        artifact_store: ArtifactStore | None = None,
    ):
        self.config = config
        self.thread_id = thread_id or uuid.uuid4().hex[:8]
        if capability_manager is None:
            from ..capabilities import default_capability_manager

            capability_manager = default_capability_manager()
        self.capabilities = capability_manager
        self.event_store = event_store or SessionEventStore(self.thread_id)
        self.artifacts = artifact_store or ArtifactStore(self.thread_id)
        self.approvals = ApprovalManager()
        self._confirm_fn: ConfirmFn | None = None
        self._current_task: asyncio.Task | None = None
        self._seq = self.event_store.next_seq()
        self._initialized = False
        self._approval_events: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
        self._agent = Agent(
            permission_mode=config.permission_mode,
            model=config.model,
            api_base=config.api_base if config.use_openai else None,
            anthropic_base_url=config.anthropic_base_url if not config.use_openai else None,
            api_key=config.api_key,
            thinking=config.thinking,
            max_cost_usd=config.max_cost_usd,
            max_turns=config.max_turns,
            custom_system_prompt=config.custom_system_prompt,
            is_sub_agent=config.is_sub_agent,
            sandbox_config=config.sandbox_config,
        )
        self._agent.session_id = self.thread_id
        self._agent.set_confirm_fn(self._confirm)

    @property
    def model(self) -> str:
        return self.config.model

    @property
    def is_processing(self) -> bool:
        return self._current_task is not None and not self._current_task.done()

    async def initialize(self) -> None:
        if self._initialized:
            return
        context = CapabilityContext(thread_id=self.thread_id, config=self.config, state={})
        await self.capabilities.initialize(context)
        tools = self.capabilities.contribute_tools()
        if tools:
            self._agent._tool_registry.add_many(tools, origin="custom")
        self._initialized = True

    def set_confirm_fn(self, fn: ConfirmFn) -> None:
        self._confirm_fn = fn

    def abort(self) -> None:
        self.approvals.abort_pending()
        self._agent.abort()
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()

    async def submit(self, prompt: str) -> AsyncIterator[RuntimeEvent]:
        await self.initialize()
        self._agent._aborted = False
        user_event = self._event("user.input", {"text": prompt})
        self._record(user_event)
        yield user_event

        sentinel = object()
        runtime_events: asyncio.Queue[RuntimeEvent | object] = asyncio.Queue()

        async def produce() -> None:
            try:
                async for agent_event in self._agent._engine.submit(prompt):
                    await runtime_events.put(self._from_agent_event(agent_event))
            except asyncio.CancelledError:
                await runtime_events.put(self._event("turn.finished", {"stop_reason": "aborted"}))
            except Exception as exc:
                await runtime_events.put(self._event("runtime.error", {"message": str(exc)}))
                await runtime_events.put(self._event("turn.finished", {"stop_reason": "error"}))
            finally:
                await runtime_events.put(sentinel)

        producer = asyncio.create_task(produce())
        self._current_task = producer
        runtime_get = asyncio.create_task(runtime_events.get())
        approval_get = asyncio.create_task(self._approval_events.get())
        try:
            while True:
                done, pending = await asyncio.wait(
                    {runtime_get, approval_get},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                _ = pending
                if approval_get in done:
                    approval_event = approval_get.result()
                    self._record(approval_event)
                    yield approval_event
                    approval_get = asyncio.create_task(self._approval_events.get())
                if runtime_get in done:
                    item = runtime_get.result()
                    if item is sentinel:
                        break
                    assert isinstance(item, RuntimeEvent)
                    self._record(item)
                    yield item
                    runtime_get = asyncio.create_task(runtime_events.get())
        finally:
            if not runtime_get.done():
                runtime_get.cancel()
            if not approval_get.done():
                approval_get.cancel()
            if not producer.done():
                producer.cancel()
            await asyncio.gather(runtime_get, approval_get, producer, return_exceptions=True)
            while not self._approval_events.empty():
                approval_event = self._approval_events.get_nowait()
                self._record(approval_event)
                yield approval_event
            self._current_task = None

    async def chat(self, prompt: str) -> TurnResult:
        stop_reason = "stop"
        count = 0
        async for event in self.submit(prompt):
            count += 1
            self._render_event(event)
            if event.type == "turn.finished":
                stop_reason = str(event.payload.get("stop_reason") or stop_reason)
        get_renderer().divider()
        usage = self._agent.get_token_usage()
        return TurnResult(
            thread_id=self.thread_id,
            stop_reason=stop_reason,
            input_tokens=int(usage.get("input", 0)),
            output_tokens=int(usage.get("output", 0)),
            events=count,
        )

    async def run_once(self, prompt: str) -> dict:
        buffer: list[str] = []
        prev_in = self._agent.total_input_tokens
        prev_out = self._agent.total_output_tokens
        async for event in self.submit(prompt):
            if event.type == "assistant.delta":
                buffer.append(str(event.payload.get("text", "")))
        return {
            "text": "".join(buffer),
            "tokens": {
                "input": self._agent.total_input_tokens - prev_in,
                "output": self._agent.total_output_tokens - prev_out,
            },
        }

    async def invoke_skill(self, skill_name: str, args: str = "", invoked_by: str = "user") -> str:
        invocation: SkillInvocationResult = self._agent._skill_invocation.invoke(
            skill_name,
            args,
            invoked_by=invoked_by,
        )
        if not invocation.ok:
            return invocation.error or f"Unknown skill: {skill_name}"

        self._agent._active_skills.record(invocation)
        if invocation.context == "fork":
            result = await self._agent._run_fork_skill(invocation)
            get_renderer().assistant_delta("\n" + result + "\n")
            return result

        await self.chat(invocation.rendered_prompt)
        return invocation.rendered_prompt

    async def compact(self) -> None:
        await self._agent.compact()
        event = self._event("context.compacted", {"reason": "manual"})
        self._record(event)

    def clear_history(self) -> None:
        self._agent.clear_history()
        self._record(self._event("thread.cleared"))

    def show_cost(self) -> None:
        self._agent.show_cost()

    def restore_session(self, data: dict) -> None:
        self._agent.restore_session(data)

    async def shutdown(self) -> None:
        await self.capabilities.shutdown()
        await self._agent.shutdown()

    async def _confirm(self, message: str) -> bool:
        def emit_request(request) -> None:
            self._approval_events.put_nowait(self._event(
                "approval.requested",
                {"request_id": request.id, "call_id": request.call_id, "message": request.message},
            ))

        decision = await self.approvals.request(
            message,
            confirm_fn=self._confirm_fn,
            on_request=emit_request,
        )
        resolved = self._event(
            "approval.resolved",
            {"request_id": decision.request_id, "status": decision.status},
        )
        self._approval_events.put_nowait(resolved)
        return decision.approved

    def _event(self, event_type: str, payload: dict | None = None) -> RuntimeEvent:
        event = RuntimeEvent(
            type=event_type,
            thread_id=self.thread_id,
            seq=self._seq,
            payload=payload or {},
        )
        self._seq += 1
        return event

    def _record(self, event: RuntimeEvent) -> None:
        self.event_store.append(event)

    def _from_agent_event(self, event: AgentEvent) -> RuntimeEvent:
        if isinstance(event, AssistantTextDelta):
            return self._event("assistant.delta", {"text": event.text})
        if isinstance(event, ToolCallStarted):
            return self._event("tool.started", {
                "id": event.call.id,
                "name": event.call.name,
                "input": event.call.input,
                "provider": event.call.provider,
            })
        if isinstance(event, ToolCallFinished):
            payload = {
                "id": event.call.id,
                "name": event.call.name,
                "content": event.result.content,
                "is_error": event.result.is_error,
                "metadata": event.result.metadata,
            }
            if len(event.result.content.encode()) > 30 * 1024:
                ref = self.artifacts.write_text(f"{event.call.name}.txt", event.result.content)
                payload["artifact"] = ref
                payload["content"] = event.result.content[:4096] + "\n\n[artifact contains full result]"
            return self._event("tool.finished", payload)
        if isinstance(event, PermissionRequested):
            return self._event("approval.requested", {
                "call_id": event.call.id,
                "tool_name": event.call.name,
                "message": event.message,
            })
        if isinstance(event, BudgetExceeded):
            return self._event("budget.exceeded", {"reason": event.reason})
        if isinstance(event, ContextCompacted):
            return self._event("context.compacted", {"reason": event.reason})
        if isinstance(event, ApiRetry):
            return self._event("api.retry", {"attempt": event.attempt, "reason": event.reason})
        if isinstance(event, LoopFinished):
            return self._event("turn.finished", {"stop_reason": event.stop_reason})
        return self._event("runtime.event", {"repr": repr(event)})

    def _render_event(self, event: RuntimeEvent) -> None:
        renderer = get_renderer()
        if event.type == "user.input":
            return
        if event.type == "assistant.delta":
            renderer.assistant_delta(str(event.payload.get("text", "")))
        elif event.type == "tool.started":
            renderer.tool_call(str(event.payload.get("name", "")), event.payload.get("input") or {})
        elif event.type == "tool.finished":
            renderer.tool_result(str(event.payload.get("name", "")), str(event.payload.get("content", "")))
        elif event.type == "budget.exceeded":
            renderer.info(f"Budget exceeded: {event.payload.get('reason', '')}")
        elif event.type == "turn.finished" and event.payload.get("stop_reason") == "stop":
            renderer.cost(self._agent.total_input_tokens, self._agent.total_output_tokens)
