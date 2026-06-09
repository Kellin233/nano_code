"""RuntimeThread 是公开的 turn 执行入口。

适配重构后的 Agent（纯状态容器）+ AgentLoop（后端无关循环）架构。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

from ..session import ArtifactStore, SessionEventStore
from ..tui.renderer import get_renderer
from .agent import Agent, RuntimeConfig
from .approvals import ApprovalManager, ConfirmFn
from .events import RuntimeEvent, TurnResult
from .loop import AgentLoop


class RuntimeThread:
    """持有一次对话线程，对外暴露 RuntimeEvent 事件流。

    这是 server 模式和 CLI 的公共入口。
    """

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

        # Event store 和 artifact store
        self.event_store = event_store or SessionEventStore(self.thread_id)
        self.artifacts = artifact_store or ArtifactStore(self.thread_id)

        # Agent + Backend + Loop
        self._agent = Agent(config)
        self._agent.session_id = self.thread_id

        from ..backend import create_backend
        self._backend = create_backend(
            provider=config.provider,
            api_key=config.api_key or "",  # type: ignore[arg-type]
            model=config.model,
            api_base=config.api_base,
            anthropic_base_url=config.anthropic_base_url,
        )
        self._loop = AgentLoop(self._agent, self._backend)

        # Approvals
        self.approvals = ApprovalManager()
        self._confirm_fn: ConfirmFn | None = None
        self._current_task: asyncio.Task | None = None
        self._seq = self.event_store.next_seq()

        # 确认回调
        self._agent.set_confirm_fn(self._confirm)

    @property
    def model(self) -> str:
        return self.config.model

    @property
    def is_processing(self) -> bool:
        return self._current_task is not None and not self._current_task.done()

    def set_confirm_fn(self, fn: ConfirmFn) -> None:
        self._confirm_fn = fn

    def abort(self) -> None:
        self.approvals.abort_pending()
        self._agent.abort()
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()

    async def submit(self, prompt: str) -> AsyncIterator[RuntimeEvent]:
        """提交用户消息，产出 RuntimeEvent 流。"""
        self._agent._aborted = False

        user_event = self._make_event("user.input", {"text": prompt})
        self.event_store.append(user_event)
        yield user_event

        sentinel = object()
        runtime_events: asyncio.Queue[RuntimeEvent | object] = asyncio.Queue()
        approval_events: asyncio.Queue[RuntimeEvent] = asyncio.Queue()

        async def produce() -> None:
            try:
                async for event in self._loop.run(prompt):
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
        runtime_get = asyncio.create_task(runtime_events.get())
        approval_get = asyncio.create_task(approval_events.get())

        try:
            while True:
                done, pending = await asyncio.wait(
                    {runtime_get, approval_get},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                _ = pending
                if approval_get in done:
                    approval_event = approval_get.result()
                    self.event_store.append(approval_event)
                    yield approval_event
                    approval_get = asyncio.create_task(approval_events.get())
                if runtime_get in done:
                    item = runtime_get.result()
                    if item is sentinel:
                        break
                    assert isinstance(item, RuntimeEvent)
                    self.event_store.append(item)
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
            while not approval_events.empty():
                approval_event = approval_events.get_nowait()
                self.event_store.append(approval_event)
                yield approval_event
            self._current_task = None

    async def chat(self, prompt: str) -> TurnResult:
        """一次性对话（TUI 模式用）。"""
        stop_reason = "stop"
        count = 0
        input_tokens = 0
        output_tokens = 0

        async for event in self.submit(prompt):
            count += 1
            self._render_event(event)
            if event.type == "turn.finished":
                stop_reason = str(event.payload.get("stop_reason", stop_reason))
                input_tokens = int(event.payload.get("input_tokens", 0))
                output_tokens = int(event.payload.get("output_tokens", 0))

        get_renderer().divider()
        usage = self._agent.get_token_usage()
        return TurnResult(
            thread_id=self.thread_id,
            stop_reason=stop_reason,
            input_tokens=int(usage.get("input", input_tokens)),
            output_tokens=int(usage.get("output", output_tokens)),
            events=count,
        )

    async def compact(self) -> None:
        from .compressor import Compressor
        await Compressor(self._agent).compact_conversation()
        event = self._make_event("context.compacted", {"reason": "manual"})
        self.event_store.append(event)

    def clear_history(self) -> None:
        self._agent.clear_history()
        self.event_store.append(self._make_event("thread.cleared"))

    def show_cost(self) -> None:
        self._agent.show_cost()

    def restore_session(self, data: dict) -> None:
        self._agent.restore_session(data)

    async def shutdown(self) -> None:
        await self._agent.shutdown()

    # ─── 内部 ────────────────────────────────────

    def _make_event(self, event_type: str, payload: dict | None = None) -> RuntimeEvent:
        event = RuntimeEvent(
            type=event_type,
            payload=payload or {},
        )
        return event

    async def _confirm(self, message: str) -> bool:
        decision = await self.approvals.request(
            message,
            confirm_fn=self._confirm_fn,
        )
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
            renderer.cost(self._agent.total_input_tokens, self._agent.total_output_tokens)
