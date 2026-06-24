"""Tool allowlist permission policy."""

from __future__ import annotations

from collections.abc import Collection

from .policy import PermissionDecision


def check_tool_allowlist(
    tool_name: str,
    allowed_tools: Collection[str] | None,
) -> PermissionDecision:
    if allowed_tools is None:
        return PermissionDecision("allow")
    if tool_name in allowed_tools:
        return PermissionDecision("allow")
    return PermissionDecision("deny", f"Tool is not allowed in this run: {tool_name}", code="action_denied")


__all__ = ["check_tool_allowlist"]
