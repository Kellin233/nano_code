"""Structured events emitted by the agent runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ...domains.tools.base import ToolCall, ToolResult


@dataclass(frozen=True)
class AssistantTextDelta:
    text: str


@dataclass(frozen=True)
class ToolCallStarted:
    call: ToolCall


@dataclass(frozen=True)
class ToolCallFinished:
    call: ToolCall
    result: ToolResult


@dataclass(frozen=True)
class PermissionRequested:
    call: ToolCall
    message: str


@dataclass(frozen=True)
class ContextCompacted:
    reason: str


@dataclass(frozen=True)
class ApiRetry:
    attempt: int
    reason: str


@dataclass(frozen=True)
class BudgetExceeded:
    reason: str


@dataclass(frozen=True)
class LoopFinished:
    stop_reason: Literal["stop", "aborted", "budget_exceeded", "error"]


AgentEvent = (
    AssistantTextDelta
    | ToolCallStarted
    | ToolCallFinished
    | PermissionRequested
    | ContextCompacted
    | ApiRetry
    | BudgetExceeded
    | LoopFinished
)
