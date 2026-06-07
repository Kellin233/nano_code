"""Shared types for sandbox-backed command execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SandboxBackendName = Literal["local", "bwrap", "microsandbox"]
SandboxProfile = Literal[
    "workspace",
    "read-only",
    "local",
    "danger-full-access",
    "microsandbox-dev",
    "microsandbox-safe",
    "microsandbox-strict",
]
NetworkMode = Literal["none", "default"]
WorkspaceMode = Literal["read-only", "workspace-write", "full-access"]


@dataclass(frozen=True)
class SandboxConfig:
    profile: SandboxProfile = "workspace"
    backend: SandboxBackendName = "bwrap"
    workspace_host_path: Path | None = None
    workspace_guest_path: str = "/workspace"
    workspace_mode: WorkspaceMode = "workspace-write"
    network_mode: NetworkMode = "none"
    fail_if_unavailable: bool = False
    allow_fallback_to_local: bool = False

    # microsandbox only
    image: str = "python:3.12"
    cpus: int = 2
    memory_mib: int = 2048
    startup_timeout_s: float = 30.0
    command_timeout_s: float = 30.0

    # local/bwrap policy
    protected_paths: tuple[str, ...] = (".git", ".env", ".env.*", ".codex", ".claude")
    extra_writable_roots: tuple[Path, ...] = ()
    forwarded_env: tuple[str, ...] = ()

    def resolved_workspace_host_path(self) -> Path:
        return (self.workspace_host_path or Path.cwd()).resolve()

    @property
    def workspace_writable(self) -> bool:
        return self.workspace_mode in ("workspace-write", "full-access")


@dataclass(frozen=True)
class CommandResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False
    backend_name: str = "local"
    error: str = ""

    def to_tool_output(self, timeout_ms: int) -> str:
        if self.timed_out:
            return f"Command timed out after {timeout_ms}ms"
        if self.error:
            return f"Error: {self.error}"
        if self.exit_code != 0:
            stdout = f"\nStdout: {self.stdout}" if self.stdout else ""
            stderr = f"\nStderr: {self.stderr}" if self.stderr else ""
            return f"Command failed (exit code {self.exit_code}){stdout}{stderr}"
        return self.stdout or "(no output)"


def text_or_empty(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
