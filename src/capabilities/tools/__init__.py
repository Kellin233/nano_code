"""工具系统 — 内置工具 + 注册中心 + 执行管线。"""

from __future__ import annotations

from ..permissions import check_permission, reset_permission_cache
from .builtin import builtin_tool_definitions
from .registry import ToolRegistry
from .runtime import ToolRuntime, execute_builtin_tool
from .types import (
    FunctionTool,
    PermissionDecision,
    PermissionMode,
    ToolCall,
    ToolContext,
    ToolDef,
    ToolMetadata,
    ToolOrigin,
    ToolResult,
    ValidationResult,
)

__all__ = [
    "FunctionTool",
    "PermissionDecision",
    "PermissionMode",
    "ToolCall",
    "ToolContext",
    "ToolDef",
    "ToolMetadata",
    "ToolOrigin",
    "ToolResult",
    "ToolRuntime",
    "ToolRegistry",
    "ValidationResult",
    "builtin_tool_definitions",
    "check_permission",
    "execute_builtin_tool",
    "reset_permission_cache",
]
