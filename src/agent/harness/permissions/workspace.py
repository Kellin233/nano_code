"""Workspace and protected path checks for file tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PathAction = Literal["allow", "confirm", "deny"]
PathConfirmReason = Literal["protected", "workspace_boundary", ""]
WRITE_TOOL_NAMES = {"write_file", "edit_file"}


@dataclass(frozen=True)
class PathDecision:
    action: PathAction
    message: str = ""
    reason: PathConfirmReason = ""  # 区分两种 confirm：protected（安全）vs workspace_boundary（typo）


PROTECTED_NAMES = {
    ".mcp.json",
    ".env",
    "id_rsa",
    "id_ed25519",
    "known_hosts",
    "authorized_keys",
}


def _resolve_user_path(raw: str, cwd: Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_protected(path: Path, workspace: Path) -> bool:
    parts = set(path.parts)
    if ".git" in parts:
        return True
    if path.name in PROTECTED_NAMES or path.name.startswith(".env."):
        return True
    claude_settings = workspace / ".claude" / "settings.json"
    try:
        if path == claude_settings.resolve():
            return True
    except OSError:
        pass
    return False


def check_path_policy(tool_name: str, inp: dict, cwd: Path | None = None) -> PathDecision:
    raw = inp.get("file_path")
    if not raw:
        return PathDecision("allow")

    workspace = (cwd or Path.cwd()).resolve()
    path = _resolve_user_path(str(raw), workspace)

    outside_workspace = not _is_relative_to(path, workspace)
    if outside_workspace and tool_name in WRITE_TOOL_NAMES:
        return PathDecision("deny", f"path outside workspace: {path}", reason="workspace_boundary")

    if _is_protected(path, workspace):
        if tool_name in WRITE_TOOL_NAMES:
            return PathDecision("confirm", f"protected path: {path}", reason="protected")
        if tool_name == "read_file":
            return PathDecision("confirm", f"read protected path: {path}", reason="protected")

    if outside_workspace:
        return PathDecision("confirm", f"path outside workspace: {path}", reason="workspace_boundary")

    return PathDecision("allow")
