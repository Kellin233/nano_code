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
    CONTEXT_WINDOW_MARGIN,
    DEFAULT_MAX_TOKENS,
    MAX_RETRIES,
    MAX_RETRY_DELAY_MS,
    ToolCall,
    ToolDef,
    ToolResult,
)

# ─── 类型别名 ──────────────────────────────────

PermissionMode = Literal["default", "acceptEdits", "bypassPermissions", "dontAsk"]
PermissionAction = Literal["allow", "deny", "confirm"]
ToolOrigin = Literal["builtin", "mcp", "custom", "extension"]

# ─── 数据结构 ──────────────────────────────────


@dataclass(frozen=True)
class PermissionDecision:
    action: PermissionAction
    message: str = ""


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
    read_file_state: dict[str, float]
    sandbox_manager: Any | None = None
    mcp_manager: Any | None = None
    agent: Any | None = None

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

MAX_RESULT_CHARS = 50000
DEFAULT_MAX_RESULT_CHARS = 200000  # 对标 Claude Code 50K，按 nanoCode 模型窗口可到 200K
TOOL_RESULT_CHAR_LIMITS: dict[str, int] = {
    "grep_search": 20000,   # 搜索结果容易爆炸，收紧
    "run_shell": 80000,     # shell 输出有时需要更多
    # read_file 等不在此列表 → 走 DEFAULT_MAX_RESULT_CHARS
}

# ─── 上下文压缩常量 ──────────────────────────────

BUDGET_UTILIZATION_THRESHOLD = 0.5
BUDGET_HIGH = 15000
BUDGET_MEDIUM = 30000
BUDGET_HIGH_UTILIZATION = 0.7
SNIP_THRESHOLD = 0.60
MICROCOMPACT_IDLE_S = 5 * 60
KEEP_RECENT_RESULTS = 3
COMPACT_SUMMARY_MAX_TOKENS = 2048
COMPACT_UTILIZATION_THRESHOLD = 0.85

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

# ─── 记忆系统 ──────────────────────────────────

MAX_INDEX_LINES = 200
MAX_INDEX_BYTES = 50 * 1024
