"""Lightweight shared types for the tool system."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ToolDef = dict[str, Any]
PermissionMode = Literal["default", "acceptEdits", "bypassPermissions", "dontAsk"]
PermissionAction = Literal["allow", "deny", "confirm"]
ToolOrigin = Literal["builtin", "mcp", "custom"]


@dataclass(frozen=True)
class PermissionDecision:
    action: PermissionAction
    message: str = ""

    def as_dict(self) -> dict[str, str]:
        result = {"action": self.action}
        if self.message:
            result["message"] = self.message
        return result


@dataclass
class ToolMetadata:
    name: str
    origin: ToolOrigin = "builtin"
    deferred: bool = False
    concurrency_safe: bool = False
    read_only: bool = False
    edit_tool: bool = False
    raw: dict[str, Any] = field(default_factory=dict)
