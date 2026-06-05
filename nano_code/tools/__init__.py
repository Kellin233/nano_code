"""Stable public API for the tool package."""

from __future__ import annotations

from .definitions import builtin_tool_definitions
from .permissions import check_permission, reset_permission_cache
from .registry import ToolRegistry
from .runtime import execute_builtin_tool
from .types import PermissionDecision, PermissionMode, ToolDef, ToolMetadata

__all__ = [
    "PermissionDecision",
    "PermissionMode",
    "ToolDef",
    "ToolMetadata",
    "ToolRegistry",
    "builtin_tool_definitions",
    "check_permission",
    "execute_builtin_tool",
    "reset_permission_cache",
]
