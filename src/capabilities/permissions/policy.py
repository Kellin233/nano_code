"""Unified permission policy for tool calls."""

from __future__ import annotations

from pathlib import Path

from ..tools.builtin import EDIT_TOOL_NAMES, READ_TOOL_NAMES
from ..tools.types import PermissionDecision, PermissionMode, ToolMetadata
from .rules import reset_permission_cache, rule_decision
from .shell import check_shell_safety, is_dangerous
from .workspace import check_path_policy


def _resolve_path(raw: str, cwd: Path | None) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return (cwd or Path.cwd()) / path


def check_permission(
    tool_name: str,
    inp: dict,
    *,
    mode: PermissionMode = "default",
    metadata: ToolMetadata | None = None,
    cwd: Path | None = None,
) -> PermissionDecision:
    path_decision = check_path_policy(tool_name, inp, cwd)
    if path_decision.action == "deny":
        return PermissionDecision("deny", path_decision.message)
    if path_decision.action == "confirm":
        if mode == "dontAsk":
            return PermissionDecision("deny", f"Auto-denied (dontAsk mode): {path_decision.message}")
        return PermissionDecision("confirm", path_decision.message)

    rules = rule_decision(tool_name, inp)
    if rules == "deny":
        return PermissionDecision("deny", f"Denied by permission rule for {tool_name}")

    if mode == "bypassPermissions":
        return PermissionDecision("allow")

    if rules == "allow":
        return PermissionDecision("allow")

    read_only = metadata.read_only if metadata is not None else tool_name in READ_TOOL_NAMES
    edit_tool = metadata.edit_tool if metadata is not None else tool_name in EDIT_TOOL_NAMES

    if read_only:
        return PermissionDecision("allow")

    if mode == "acceptEdits" and edit_tool:
        return PermissionDecision("allow")

    if tool_name == "run_shell":
        safety = check_shell_safety(inp.get("command", ""))
        if safety.level == "deny":
            return PermissionDecision("deny", safety.reason)
        if safety.level == "confirm":
            command = inp.get("command", "")
            if mode == "dontAsk":
                return PermissionDecision("deny", f"Auto-denied (dontAsk mode): {command}")
            return PermissionDecision("confirm", command)

    file_path = str(inp.get("file_path", ""))
    target_path = _resolve_path(file_path, cwd) if file_path else None

    if tool_name == "write_file" and target_path is not None and not target_path.exists():
        message = f"write new file: {inp.get('file_path', '')}"
        if mode == "dontAsk":
            return PermissionDecision("deny", f"Auto-denied (dontAsk mode): {message}")
        return PermissionDecision("confirm", message)

    if tool_name == "edit_file" and target_path is not None and not target_path.exists():
        message = f"edit non-existent file: {inp.get('file_path', '')}"
        if mode == "dontAsk":
            return PermissionDecision("deny", f"Auto-denied (dontAsk mode): {message}")
        return PermissionDecision("confirm", message)

    return PermissionDecision("allow")


__all__ = ["check_permission", "reset_permission_cache", "is_dangerous"]
