"""Small message and model-event types used by the core turn loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]
StopReason = Literal["stop", "tool_calls", "aborted", "budget_exceeded", "error"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: Any
    name: str | None = None
    tool_call_id: str | None = None


@dataclass(frozen=True)
class CoreToolCall:
    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)
    provider: str = "model"


@dataclass(frozen=True)
class CoreToolResult:
    call_id: str
    name: str
    content: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class AssistantMessage:
    content: Any
    tool_calls: list[CoreToolCall] = field(default_factory=list)
    provider_message: Any | None = None


@dataclass(frozen=True)
class ModelTextDelta:
    text: str


@dataclass(frozen=True)
class ModelTurnComplete:
    message: AssistantMessage
    usage: ModelUsage = field(default_factory=ModelUsage)
    stop_reason: StopReason = "stop"


ModelEvent = ModelTextDelta | ModelTurnComplete
