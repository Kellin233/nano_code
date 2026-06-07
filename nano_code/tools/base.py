"""Core tool contract used by the agent runtime."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from .types import ToolDef, ToolOrigin


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]
    provider: str = "model"


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    extra_messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    message: str = ""
    updated_input: dict[str, Any] | None = None


@dataclass
class ToolContext:
    cwd: Path
    session_id: str
    read_file_state: dict[str, float]
    sandbox_manager: Any | None = None
    mcp_manager: Any | None = None
    agent: Any | None = None


class Tool(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]
    origin: ToolOrigin
    deferred: bool

    def to_definition(self) -> ToolDef:
        ...

    def is_read_only(self, inp: dict[str, Any]) -> bool:
        ...

    def is_edit_tool(self, inp: dict[str, Any]) -> bool:
        ...

    def is_concurrency_safe(self, inp: dict[str, Any]) -> bool:
        ...

    async def validate(self, inp: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        ...

    async def call(self, inp: dict[str, Any], ctx: ToolContext) -> ToolResult:
        ...


ToolCallFn = Callable[[dict[str, Any], ToolContext], ToolResult | str | Awaitable[ToolResult | str]]


class FunctionTool:
    """Small adapter for existing function-based tools."""

    def __init__(
        self,
        definition: ToolDef,
        call_fn: ToolCallFn,
        *,
        origin: ToolOrigin = "builtin",
        deferred: bool = False,
        read_only: bool | Callable[[dict[str, Any]], bool] = False,
        edit_tool: bool | Callable[[dict[str, Any]], bool] = False,
        concurrency_safe: bool | Callable[[dict[str, Any]], bool] = False,
    ):
        self.name = str(definition["name"])
        self.description = str(definition.get("description", ""))
        self.input_schema = dict(definition.get("input_schema") or {"type": "object"})
        self.origin = origin
        self.deferred = deferred
        self._definition = dict(definition)
        self._call_fn = call_fn
        self._read_only = read_only
        self._edit_tool = edit_tool
        self._concurrency_safe = concurrency_safe

    def to_definition(self) -> ToolDef:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def is_read_only(self, inp: dict[str, Any]) -> bool:
        return self._read_only(inp) if callable(self._read_only) else bool(self._read_only)

    def is_edit_tool(self, inp: dict[str, Any]) -> bool:
        return self._edit_tool(inp) if callable(self._edit_tool) else bool(self._edit_tool)

    def is_concurrency_safe(self, inp: dict[str, Any]) -> bool:
        value = self._concurrency_safe(inp) if callable(self._concurrency_safe) else self._concurrency_safe
        return bool(value)

    async def validate(self, inp: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        _ = ctx
        if not isinstance(inp, dict):
            return ValidationResult(False, "tool input must be an object")
        required = self.input_schema.get("required") or []
        for key in required:
            if key not in inp:
                return ValidationResult(False, f"missing required field: {key}")
        return ValidationResult(True)

    async def call(self, inp: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            value = self._call_fn(inp, ctx)
            if inspect.isawaitable(value):
                value = await value
            if isinstance(value, ToolResult):
                return value
            return ToolResult(str(value), is_error=str(value).startswith(("Error", "Warning")))
        except Exception as exc:
            return ToolResult(f"Error executing tool {self.name}: {exc}", is_error=True)

