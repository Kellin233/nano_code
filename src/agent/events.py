"""运行时事件定义。

合并了原 runtime/events.py（RuntimeEvent/TurnResult）和
agent/events.py（AgentEvent 子类），统一为单一事件模型。

用工厂函数替代子类，类型判断用 event.type 字符串，
不需要 isinstance 判断链。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .types import RuntimeEvent, ToolCall, ToolResult


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


def PermissionRequested(
    call: ToolCall,
    message: str,
    *,
    requires_explicit_confirmation: bool = False,
) -> RuntimeEvent:
    return RuntimeEvent(type="approval.requested", payload={
        "call_id": call.id,
        "tool_name": call.name,
        "message": message,
        "requires_explicit_confirmation": requires_explicit_confirmation,
    })


def BudgetExceeded(reason: str) -> RuntimeEvent:
    return RuntimeEvent(type="budget.exceeded", payload={"reason": reason})


def LoopFinished(stop_reason: str) -> RuntimeEvent:
    return RuntimeEvent(type="turn.finished", payload={"stop_reason": stop_reason})
