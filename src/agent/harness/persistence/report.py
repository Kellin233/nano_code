"""Trace normalization and run report helpers."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ...types import RuntimeEvent
from .task_state import TaskState

RUN_ARTIFACT_SCHEMA_VERSION = 1


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunMetrics:
    assistant_delta_events: int = 0
    runtime_error_count: int = 0
    tool_error_count: int = 0
    approval_request_count: int = 0
    tool_name_counts: Counter[str] = field(default_factory=Counter)
    budget_exceeded_reason: str = ""
    runtime_error: str = ""

    def observe(self, event: RuntimeEvent) -> None:
        if event.type == "assistant.delta":
            self.assistant_delta_events += 1
        elif event.type == "runtime.error":
            self.runtime_error_count += 1
            self.runtime_error = str(event.payload.get("message", ""))
        elif event.type == "approval.requested":
            self.approval_request_count += 1
        elif event.type == "budget.exceeded":
            self.budget_exceeded_reason = str(event.payload.get("reason", ""))
        elif event.type == "tool.finished":
            name = str(event.payload.get("name", ""))
            if name:
                self.tool_name_counts[name] += 1
            if bool(event.payload.get("is_error")):
                self.tool_error_count += 1

    def to_dict(self) -> dict:
        return {
            "assistant_delta_events": self.assistant_delta_events,
            "runtime_error_count": self.runtime_error_count,
            "tool_error_count": self.tool_error_count,
            "approval_request_count": self.approval_request_count,
            "tool_name_counts": dict(sorted(self.tool_name_counts.items())),
            "budget_exceeded_reason": self.budget_exceeded_reason,
            "runtime_error": self.runtime_error,
        }


EVENT_NAME_BY_TYPE = {
    "assistant.delta": "assistant_delta",
    "tool.started": "tool_started",
    "tool.finished": "tool_executed",
    "approval.requested": "approval_requested",
    "budget.exceeded": "budget_exceeded",
    "runtime.error": "runtime_error",
    "turn.finished": "run_finished",
}


def trace_event(task_state: TaskState, event: str, payload: dict[str, Any] | None = None) -> dict:
    return {
        "event": event,
        "created_at": now_iso(),
        "run_id": task_state.run_id,
        "task_id": task_state.task_id,
        **(payload or {}),
    }


def runtime_event_to_trace(task_state: TaskState, event: RuntimeEvent) -> dict:
    payload = dict(event.payload or {})
    trace = trace_event(
        task_state,
        EVENT_NAME_BY_TYPE.get(event.type, event.type.replace(".", "_")),
        {
            "type": event.type,
            "payload": payload,
        },
    )
    name = payload.get("name") or payload.get("tool_name")
    if name:
        trace["name"] = str(name)
    if "is_error" in payload:
        trace["is_error"] = bool(payload.get("is_error"))
    if event.type == "turn.finished":
        trace["status"] = task_state.status
        trace["stop_reason"] = str(payload.get("stop_reason", ""))
    return trace


def build_report(
    task_state: TaskState,
    *,
    runtime: dict,
    usage: dict,
    metrics: RunMetrics,
    started_at: str,
    finished_at: str,
    duration_ms: int,
) -> dict:
    return {
        "schema_version": RUN_ARTIFACT_SCHEMA_VERSION,
        "run_id": task_state.run_id,
        "task_id": task_state.task_id,
        "status": task_state.status,
        "stop_reason": task_state.stop_reason,
        "final_answer": task_state.final_answer,
        "tool_steps": task_state.tool_steps,
        "attempts": task_state.attempts,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
        "runtime": runtime,
        "usage": usage,
        "metrics": metrics.to_dict(),
    }
