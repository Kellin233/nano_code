"""Unified permission policy for tool calls."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .rules import reset_permission_cache, rule_decision
from .shell import check_shell_safety, is_dangerous
from .workspace import check_path_policy

PermissionMode = Literal["default", "acceptEdits", "bypassPermissions", "dontAsk"]
PermissionAction = Literal["allow", "deny", "confirm"]


@dataclass(frozen=True)
class PermissionDecision:
    action: PermissionAction
    message: str = ""
    code: str = ""
    requires_explicit_confirmation: bool = False


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
    metadata: Any | None = None,
    cwd: Path | None = None,
) -> PermissionDecision:
    path_decision = check_path_policy(tool_name, inp, cwd)
    if path_decision.action == "deny":
        code = "outside_workspace" if path_decision.reason == "workspace_boundary" else "action_denied"
        return PermissionDecision("deny", path_decision.message, code=code)
    if path_decision.action == "confirm":
        if mode == "dontAsk":
            return PermissionDecision("deny", f"Auto-denied (dontAsk mode): {path_decision.message}", code="action_denied")
        # workspace_boundary：写入类工具已在 path policy 中硬拒绝；读类路径在 yolo 下保持可自纠错。
        # protected：即使是 yolo 模式，写入 .git/.env/SSH key 也必须确认
        if mode == "bypassPermissions" and path_decision.reason == "workspace_boundary":
            return PermissionDecision("allow")
        return PermissionDecision(
            "confirm",
            path_decision.message,
            code="protected_path" if path_decision.reason == "protected" else "",
            requires_explicit_confirmation=path_decision.reason == "protected",
        )

    rules = rule_decision(tool_name, inp, cwd=cwd)
    if rules == "deny":
        return PermissionDecision("deny", f"Denied by permission rule for {tool_name}", code="action_denied")

    if mode == "bypassPermissions":
        return PermissionDecision("allow")

    if rules == "allow":
        return PermissionDecision("allow")

    read_only = bool(getattr(metadata, "read_only", False)) if metadata is not None else False
    edit_tool = bool(getattr(metadata, "edit_tool", False)) if metadata is not None else False

    if read_only:
        return PermissionDecision("allow")

    if mode == "acceptEdits" and edit_tool:
        return PermissionDecision("allow")

    if tool_name == "run_shell":
        safety = check_shell_safety(inp.get("command", ""))
        if safety.level == "deny":
            return PermissionDecision("deny", safety.reason, code="action_denied")
        if safety.level == "confirm":
            command = inp.get("command", "")
            if mode == "dontAsk":
                return PermissionDecision("deny", f"Auto-denied (dontAsk mode): {command}", code="action_denied")
            return PermissionDecision("confirm", command)

    file_path = str(inp.get("file_path", ""))
    target_path = _resolve_path(file_path, cwd) if file_path else None

    if tool_name == "write_file" and target_path is not None and not target_path.exists():
        message = f"write new file: {inp.get('file_path', '')}"
        if mode == "dontAsk":
            return PermissionDecision("deny", f"Auto-denied (dontAsk mode): {message}", code="action_denied")
        return PermissionDecision("confirm", message)

    if tool_name == "edit_file" and target_path is not None and not target_path.exists():
        message = f"edit non-existent file: {inp.get('file_path', '')}"
        if mode == "dontAsk":
            return PermissionDecision("deny", f"Auto-denied (dontAsk mode): {message}", code="action_denied")
        return PermissionDecision("confirm", message)

    if tool_name == "write_file" and target_path is not None:
        message = f"write file: {inp.get('file_path', '')}"
        if mode == "dontAsk":
            return PermissionDecision("deny", f"Auto-denied (dontAsk mode): {message}", code="action_denied")
        return PermissionDecision("confirm", message)

    if tool_name == "edit_file" and target_path is not None:
        message = f"edit file: {inp.get('file_path', '')}"
        if mode == "dontAsk":
            return PermissionDecision("deny", f"Auto-denied (dontAsk mode): {message}", code="action_denied")
        return PermissionDecision("confirm", message)

    return PermissionDecision("allow")


__all__ = ["PermissionDecision", "PermissionMode", "check_permission", "reset_permission_cache", "is_dangerous"]
