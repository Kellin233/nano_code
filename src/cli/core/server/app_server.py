"""Protocol-facing server application."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

from ....agent.models import DEFAULT_MODEL
from ..protocol.messages import (
    APPROVAL_RESOLVE,
    SESSION_LIST,
    THREAD_ABORT,
    THREAD_COMPACT,
    THREAD_CREATE,
    THREAD_RESUME,
    THREAD_SUBMIT,
    ProtocolError,
    ProtocolRequest,
    ProtocolResponse,
)
from ....agent.harness.approvals import ApprovalDecision
from ...thread import RuntimeThread
from ....agent.agent import RuntimeConfig
from ....agent.harness.session import list_sessions, load_session


class NanoCodeServer:
    def __init__(self):
        self.threads: dict[str, RuntimeThread] = {}

    async def handle(self, request: ProtocolRequest) -> AsyncIterator[dict[str, Any]]:
        if request.method == THREAD_CREATE:
            yield self._response(request.id, self._thread_create(request.params))
            return
        if request.method == THREAD_RESUME:
            yield self._response(request.id, self._thread_resume(request.params))
            return
        if request.method == THREAD_SUBMIT:
            async for message in self._thread_submit(request):
                yield message
            return
        if request.method == THREAD_ABORT:
            yield self._response(request.id, self._thread_abort(request.params))
            return
        if request.method == THREAD_COMPACT:
            yield self._response(request.id, await self._thread_compact(request.params))
            return
        if request.method == APPROVAL_RESOLVE:
            yield self._response(request.id, self._approval_resolve(request.params))
            return
        if request.method == SESSION_LIST:
            yield self._response(request.id, {"sessions": list_sessions()})
            return
        raise ProtocolError("method_not_found", f"unsupported method: {request.method}")

    def _thread_create(self, params: dict[str, Any]) -> dict[str, Any]:
        config = self._config(params.get("config") or params)
        thread = RuntimeThread(config)
        self.threads[thread.thread_id] = thread
        return {"thread_id": thread.thread_id}

    def _thread_resume(self, params: dict[str, Any]) -> dict[str, Any]:
        thread_id = str(params.get("thread_id") or "")
        if not thread_id:
            raise ProtocolError("invalid_params", "thread_id is required")
        config = self._config(params.get("config") or {})
        thread = RuntimeThread(config, thread_id=thread_id)
        session = load_session(thread_id)
        if session:
            thread.restore_session({
                "anthropicMessages": session.get("anthropicMessages"),
                "openaiMessages": session.get("openaiMessages"),
            })
        self.threads[thread_id] = thread
        return {"thread_id": thread_id, "resumed": bool(session)}

    async def _thread_submit(self, request: ProtocolRequest) -> AsyncIterator[dict[str, Any]]:
        params = request.params
        thread = self._get_thread(str(params.get("thread_id") or ""))
        prompt = str(params.get("prompt") or "")
        if not prompt:
            raise ProtocolError("invalid_params", "prompt is required")
        count = 0
        stop_reason = "stop"
        async for event in thread.submit(prompt):
            count += 1
            if event.type == "turn.finished":
                stop_reason = str(event.payload.get("stop_reason") or stop_reason)
            yield {"method": "runtime.event", "params": event.to_dict()}
        yield self._response(request.id, {
            "thread_id": thread.thread_id,
            "events": count,
            "stop_reason": stop_reason,
        })

    def _thread_abort(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = self._get_thread(str(params.get("thread_id") or ""))
        thread.abort()
        return {"thread_id": thread.thread_id, "aborted": True}

    async def _thread_compact(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = self._get_thread(str(params.get("thread_id") or ""))
        await thread.compact()
        return {"thread_id": thread.thread_id, "compacted": True}

    def _approval_resolve(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = self._get_thread(str(params.get("thread_id") or ""))
        request_id = str(params.get("request_id") or "")
        approved = bool(params.get("approved"))
        remember = bool(params.get("remember"))
        if not request_id:
            raise ProtocolError("invalid_params", "request_id is required")
        resolved = thread.approvals.resolve(ApprovalDecision(
            request_id=request_id,
            status="approved" if approved else "denied",
            remember=remember,
        ))
        return {"thread_id": thread.thread_id, "resolved": resolved}

    def _get_thread(self, thread_id: str) -> RuntimeThread:
        if not thread_id or thread_id not in self.threads:
            raise ProtocolError("thread_not_found", f"unknown thread: {thread_id}")
        return self.threads[thread_id]

    def _config(self, params: dict[str, Any]) -> RuntimeConfig:
        provider = "openai" if params.get("api_base") or params.get("provider") == "openai" else "anthropic"
        api_key = params.get("api_key")
        if not api_key:
            api_key = os.environ.get("OPENAI_API_KEY") if provider == "openai" else os.environ.get("ANTHROPIC_API_KEY")
        return RuntimeConfig(
            model=str(params.get("model") or os.environ.get("NANO_CODE_MODEL") or DEFAULT_MODEL),
            provider=provider,
            api_base=params.get("api_base") if provider == "openai" else None,
            anthropic_base_url=params.get("anthropic_base_url") if provider == "anthropic" else None,
            api_key=api_key,
            thinking=bool(params.get("thinking")),
            permission_mode=str(params.get("permission_mode") or "default"),
            max_cost_usd=params.get("max_cost_usd"),
            max_turns=params.get("max_turns"),
        )

    def _response(self, request_id: str | int | None, result: dict[str, Any]) -> dict[str, Any]:
        return ProtocolResponse(id=request_id, result=result).to_message()
