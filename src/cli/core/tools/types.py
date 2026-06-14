"""工具系统的数据模型与常量。

合并了原 types.py（ToolDef/ ToolMetadata/ ToolOrigin/ PermissionMode）、
base.py（ToolCall/ ToolContext/ ToolResult/ FunctionTool）、
constants.py（全部工具相关常量）。

变更原因：
  - 改工具 schema 约定 → 改 ToolDef/ ToolMetadata
  - 改工具调用/结果的结构 → 改 ToolCall/ ToolContext/ ToolResult
  - 调优压缩/执行参数 → 改对应常量
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from ....agent.types import (
    ToolCall,
    ToolDef,
    ToolResult,
)

__all__ = [
    "DEFAULT_MAX_RESULT_CHARS",
    "DEFAULT_SHELL_TIMEOUT_MS",
    "FunctionTool",
    "TOOL_RESULT_CHAR_LIMITS",
    "TOOL_RESULT_PREVIEW_CHARS",
    "PermissionAction",
    "PermissionDecision",
    "PermissionMode",
    "Tool",
    "ToolCall",
    "ToolCallFn",
    "ToolContext",
    "ToolDef",
    "ToolMetadata",
    "ToolOrigin",
    "ToolResult",
    "ValidationResult",
]

# ─── 类型别名 ──────────────────────────────────

PermissionMode = Literal["default", "acceptEdits", "bypassPermissions", "dontAsk"]
PermissionAction = Literal["allow", "deny", "confirm"]
ToolOrigin = Literal["builtin", "mcp", "custom", "extension"]

# ─── 数据结构 ──────────────────────────────────


@dataclass(frozen=True)
class PermissionDecision:
    action: PermissionAction
    message: str = ""
    code: str = ""


@dataclass
class ToolMetadata:
    name: str
    origin: ToolOrigin = "builtin"
    deferred: bool = False
    concurrency_safe: bool = False
    read_only: bool = False
    edit_tool: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    message: str = ""
    updated_input: dict[str, Any] | None = None


@dataclass
class ToolContext:
    cwd: Path
    session_id: str
    sandbox_manager: Any | None = None
    mcp_manager: Any | None = None
    execute_agent_tool: Callable[[dict], Awaitable[str]] | None = None
    execute_skill_tool: Callable[[dict], Awaitable[str]] | None = None
    execute_tool_search: Callable[[dict], str] | None = None

# ─── Tool 协议 ──────────────────────────────────


class Tool(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]
    origin: ToolOrigin
    deferred: bool

    def to_definition(self) -> ToolDef: ...
    def is_read_only(self, inp: dict[str, Any]) -> bool: ...
    def is_edit_tool(self, inp: dict[str, Any]) -> bool: ...
    def is_concurrency_safe(self, inp: dict[str, Any]) -> bool: ...

    async def validate(self, inp: dict[str, Any], ctx: ToolContext) -> ValidationResult: ...
    async def call(self, inp: dict[str, Any], ctx: ToolContext) -> ToolResult: ...


ToolCallFn = Callable[[dict[str, Any], ToolContext], ToolResult | str | Awaitable[ToolResult | str]]


class FunctionTool:
    """基于函数的工具适配器。"""

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
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}

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


# ─── 工具执行常量 ──────────────────────────────

DEFAULT_MAX_RESULT_CHARS = 50_000
TOOL_RESULT_PREVIEW_CHARS = 2_000
TOOL_RESULT_CHAR_LIMITS: dict[str, int] = {
    "grep_search": 20_000,
    "list_files": 20_000,
}

# ─── Shell 与搜索常量 ──────────────────────────

DEFAULT_SHELL_TIMEOUT_MS = 30000
DEFAULT_FETCH_MAX_LENGTH = 50000

# ─── API 常量 ──────────────────────────────────

# ─── MCP / Hook ────────────────────────────────

MCP_REFRESH_DEBOUNCE_S = 0.2
DEFAULT_HOOK_TIMEOUT_MS = 3000

# ─── 文件列表 ──────────────────────────────────

MAX_LIST_FILES_RESULTS = 200
MAX_GREP_RESULTS = 100
MAX_GREP_MATCHES = 200
