"""运行时事件定义。

合并了原 runtime/events.py（RuntimeEvent/TurnResult）和
agent/events.py（AgentEvent 子类），统一为单一事件模型。

用工厂函数替代子类，类型判断用 event.type 字符串，
不需要 isinstance 判断链。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..capabilities.tools.types import ToolCall, ToolResult


@dataclass(frozen=True)
class RuntimeEvent:
    """统一运行时事件。

    所有事件共享此类型，通过 type 字段区分事件种类。
    thread_id 和 seq 为可选字段，供 SessionEventStore 使用。
    """
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    thread_id: str = ""
    seq: int = 0
    timestamp: float = field(default_factory=lambda: time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "thread_id": self.thread_id,
            "seq": self.seq,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeEvent:
        return cls(
            type=str(data.get("type", "")),
            thread_id=str(data.get("thread_id", "")),
            seq=int(data.get("seq", 0)),
            payload=dict(data.get("payload") or {}),
            timestamp=float(data.get("timestamp", time.time())),
        )


@dataclass(frozen=True)
class TurnResult:
    """一次对话轮次的汇总结果。"""
    thread_id: str
    stop_reason: str
    input_tokens: int
    output_tokens: int
    events: int
    metadata: dict[str, Any] = field(default_factory=dict)


# ─── 常用事件的工厂函数 ────────────────────────────


def AssistantTextDelta(text: str) -> RuntimeEvent:
    return RuntimeEvent(type="assistant.delta", payload={"text": text})


def ToolCallStarted(call: ToolCall) -> RuntimeEvent:
    return RuntimeEvent(type="tool.started", payload={
        "id": call.id,
        "name": call.name,
        "input": call.input,
        "provider": call.provider,
    })


def ToolCallFinished(call: ToolCall, result: ToolResult) -> RuntimeEvent:
    return RuntimeEvent(type="tool.finished", payload={
        "id": call.id,
        "name": call.name,
        "content": result.content,
        "is_error": result.is_error,
        "metadata": result.metadata,
    })


def PermissionRequested(call: ToolCall, message: str) -> RuntimeEvent:
    return RuntimeEvent(type="approval.requested", payload={
        "call_id": call.id,
        "tool_name": call.name,
        "message": message,
    })


def BudgetExceeded(reason: str) -> RuntimeEvent:
    return RuntimeEvent(type="budget.exceeded", payload={"reason": reason})


def LoopFinished(stop_reason: str) -> RuntimeEvent:
    return RuntimeEvent(type="turn.finished", payload={"stop_reason": stop_reason})
