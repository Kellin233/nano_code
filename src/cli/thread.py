"""RuntimeThread public event-stream wrapper around AgentSession."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

from ..agent.events import RuntimeEvent, TurnResult
from ..agent.harness.approvals import ApprovalManager, ConfirmFn
from ..agent.harness.session import ArtifactStore, SessionEventStore
from ..agent.agent import RuntimeConfig
from ..tui.renderer import get_renderer
from .session import create_session


class RuntimeThread:
    def __init__(
        self,
        config: RuntimeConfig,
        *,
        thread_id: str | None = None,
        event_store: SessionEventStore | None = None,
        artifact_store: ArtifactStore | None = None,
    ):
        self.config = config
        self.thread_id = thread_id or uuid.uuid4().hex[:8]
        self.event_store = event_store or SessionEventStore(self.thread_id)
        self.artifacts = artifact_store or ArtifactStore(self.thread_id)
        self.session = create_session(config, thread_id=self.thread_id, render_events=False)
        self.approvals = ApprovalManager()
        self._confirm_fn: ConfirmFn | None = None
        self._current_task: asyncio.Task | None = None
        self._seq = self.event_store.next_seq()
        self.session.set_confirm_fn(self._confirm)

    @property
    def model(self) -> str:
        return self.config.model

    @property
    def is_processing(self) -> bool:
        return self.session.is_processing or (self._current_task is not None and not self._current_task.done())

    def set_confirm_fn(self, fn: ConfirmFn) -> None:
        self._confirm_fn = fn

    def abort(self) -> None:
        self.approvals.abort_pending()
        self.session.abort()
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()

    async def submit(self, prompt: str) -> AsyncIterator[RuntimeEvent]:
        user_event = self._make_event("user.input", {"text": prompt})
        self.event_store.append(user_event)
        yield user_event

        sentinel = object()
        runtime_events: asyncio.Queue[RuntimeEvent | object] = asyncio.Queue()

        async def produce() -> None:
            try:
                async for event in self.session.run(prompt):
                    await runtime_events.put(event)
            except asyncio.CancelledError:
                await runtime_events.put(self._make_event("turn.finished", {"stop_reason": "aborted"}))
            except Exception as exc:
                await runtime_events.put(self._make_event("runtime.error", {"message": str(exc)}))
                await runtime_events.put(self._make_event("turn.finished", {"stop_reason": "error"}))
            finally:
                await runtime_events.put(sentinel)

        producer = asyncio.create_task(produce())
        self._current_task = producer
        try:
            while True:
                item = await runtime_events.get()
                if item is sentinel:
                    break
                assert isinstance(item, RuntimeEvent)
                self.event_store.append(item)
                yield item
        finally:
            if not producer.done():
                producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
            self._current_task = None

    async def chat(self, prompt: str) -> TurnResult:
        stop_reason = "stop"
        count = 0
        async for event in self.submit(prompt):
            count += 1
            self._render_event(event)
            if event.type == "turn.finished":
                stop_reason = str(event.payload.get("stop_reason", stop_reason))

        get_renderer().divider()
        usage = self.session.agent.get_token_usage()
        return TurnResult(
            thread_id=self.thread_id,
            stop_reason=stop_reason,
            input_tokens=int(usage.get("input", 0)),
            output_tokens=int(usage.get("output", 0)),
            events=count,
        )

    async def compact(self) -> None:
        await self.session.compact()
        event = self._make_event("context.compacted", {"reason": "manual"})
        self.event_store.append(event)

    def clear_history(self) -> None:
        self.session.clear_history()
        self.event_store.append(self._make_event("thread.cleared"))

    def show_cost(self) -> None:
        self.session.show_cost()

    def restore_session(self, data: dict) -> None:
        self.session.restore_session(data)

    async def shutdown(self) -> None:
        await self.session.shutdown()

    def _make_event(self, event_type: str, payload: dict | None = None) -> RuntimeEvent:
        return RuntimeEvent(type=event_type, payload=payload or {})

    async def _confirm(self, message: str) -> bool:
        if self._confirm_fn is None:
            return False
        decision = await self.approvals.request(message, confirm_fn=self._confirm_fn)
        return decision.approved

    def _render_event(self, event: RuntimeEvent) -> None:
        renderer = get_renderer()
        event_type = event.type
        if event_type == "user.input":
            return
        if event_type == "assistant.delta":
            renderer.assistant_delta(str(event.payload.get("text", "")))
        elif event_type == "tool.started":
            renderer.tool_call(str(event.payload.get("name", "")), event.payload.get("input") or {})
        elif event_type == "tool.finished":
            renderer.tool_result(str(event.payload.get("name", "")), str(event.payload.get("content", "")))
        elif event_type == "budget.exceeded":
            renderer.info(f"Budget exceeded: {event.payload.get('reason', '')}")
        elif event_type == "turn.finished" and event.payload.get("stop_reason") == "stop":
            agent = self.session.agent
            renderer.cost(
                agent.total_input_tokens,
                agent.total_output_tokens,
                model=agent.model,
                input_cache_hit_tokens=agent.total_input_cache_hit_tokens,
                input_cache_miss_tokens=agent.total_input_cache_miss_tokens,
            )
