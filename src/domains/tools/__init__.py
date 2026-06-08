"""Stable public API for the tool package."""

from __future__ import annotations

from .base import FunctionTool, ToolCall, ToolContext, ToolResult, ValidationResult
from .definitions import builtin_tool_definitions
from .permissions import check_permission, reset_permission_cache
from .registry import ToolRegistry
from .runtime import ToolRuntime, execute_builtin_tool
from .types import PermissionDecision, PermissionMode, ToolDef, ToolMetadata

__all__ = [
    "FunctionTool",
    "PermissionDecision",
    "PermissionMode",
    "ToolCall",
    "ToolContext",
    "ToolDef",
    "ToolMetadata",
    "ToolResult",
    "ToolRuntime",
    "ToolRegistry",
    "ValidationResult",
    "builtin_tool_definitions",
    "check_permission",
    "execute_builtin_tool",
    "reset_permission_cache",
]
