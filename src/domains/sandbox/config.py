"""Configuration helpers for sandbox execution."""

from __future__ import annotations

import os
import platform
from argparse import Namespace
from pathlib import Path
from typing import Any

from .types import (
    NetworkMode,
    SandboxBackendName,
    SandboxConfig,
    SandboxProfile,
    WorkspaceMode,
)

VALID_PROFILES = {
    "workspace",
    "read-only",
    "local",
    "danger-full-access",
    "microsandbox",
    "microsandbox-dev",
    "microsandbox-safe",
    "microsandbox-strict",
}
VALID_NETWORK_MODES = {"none", "default"}


def _default_profile() -> SandboxProfile:
    if platform.system().lower() == "linux":
        return "workspace"
    return "local"


def _profile(value: str | None, *, readonly_workspace: bool = False) -> SandboxProfile:
    raw = (value or _default_profile()).strip()
    if raw not in VALID_PROFILES:
        raise ValueError(f"invalid sandbox profile: {raw}")
    if raw == "microsandbox":
        return "microsandbox-safe" if readonly_workspace else "microsandbox-dev"
    return raw  # type: ignore[return-value]


def _network_mode(value: str | None) -> NetworkMode:
    raw = (value or "none").strip()
    if raw not in VALID_NETWORK_MODES:
        raise ValueError(f"invalid sandbox network mode: {raw}")
    return raw  # type: ignore[return-value]


def _positive_int(value: Any, *, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer") from None
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed


def _csv_env(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _as_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item))
    return (str(value),)


def resolve_profile(
    profile: SandboxProfile,
    *,
    network_mode: NetworkMode | None = None,
    readonly_workspace: bool = False,
    allow_fallback_to_local: bool = False,
    image: str = "python:3.12",
    cpus: int = 2,
    memory_mib: int = 2048,
    workspace_host_path: Path | None = None,
    forwarded_env: tuple[str, ...] = (),
    extra_writable_roots: tuple[Path, ...] = (),
) -> SandboxConfig:
    backend: SandboxBackendName
    workspace_mode: WorkspaceMode
    default_network: NetworkMode = "none"
    fail_if_unavailable = False

    if profile == "workspace":
        backend = "bwrap"
        workspace_mode = "workspace-write"
    elif profile == "read-only":
        backend = "bwrap"
        workspace_mode = "read-only"
    elif profile == "local":
        backend = "local"
        workspace_mode = "full-access"
        default_network = "default"
    elif profile == "danger-full-access":
        backend = "local"
        workspace_mode = "full-access"
        default_network = "default"
    elif profile == "microsandbox-dev":
        backend = "microsandbox"
        workspace_mode = "workspace-write"
        fail_if_unavailable = True
    elif profile in ("microsandbox-safe", "microsandbox-strict"):
        backend = "microsandbox"
        workspace_mode = "read-only"
        fail_if_unavailable = True
    else:
        raise ValueError(f"invalid sandbox profile: {profile}")

    if readonly_workspace and backend != "local":
        workspace_mode = "read-only"

    if profile == "microsandbox-strict":
        allow_fallback_to_local = False

    return SandboxConfig(
        profile=profile,
        backend=backend,
        workspace_host_path=workspace_host_path or Path.cwd(),
        workspace_mode=workspace_mode,
        network_mode=network_mode or default_network,
        fail_if_unavailable=fail_if_unavailable,
        allow_fallback_to_local=allow_fallback_to_local,
        image=image,
        cpus=cpus,
        memory_mib=memory_mib,
        forwarded_env=forwarded_env,
        extra_writable_roots=extra_writable_roots,
    )


def build_sandbox_config(args: Namespace | None = None) -> SandboxConfig:
    args = args or Namespace()
    readonly_workspace = bool(getattr(args, "sandbox_readonly_workspace", False))
    profile = _profile(
        getattr(args, "sandbox", None) or os.environ.get("NANO_CODE_SANDBOX"),
        readonly_workspace=readonly_workspace,
    )
    image = (
        getattr(args, "sandbox_image", None)
        or os.environ.get("NANO_CODE_SANDBOX_IMAGE")
        or "python:3.12"
    )
    memory = _positive_int(
        getattr(args, "sandbox_memory", None)
        or os.environ.get("NANO_CODE_SANDBOX_MEMORY")
        or 2048,
        name="sandbox memory",
    )
    cpus = _positive_int(
        getattr(args, "sandbox_cpus", None)
        or os.environ.get("NANO_CODE_SANDBOX_CPUS")
        or 2,
        name="sandbox cpus",
    )
    raw_network = (
        getattr(args, "sandbox_network", None)
        or os.environ.get("NANO_CODE_SANDBOX_NETWORK")
    )
    network_mode = _network_mode(raw_network) if raw_network else None
    if getattr(args, "sandbox_no_network", False):
        network_mode = "none"

    env_from_args = _as_tuple(getattr(args, "sandbox_env", None))
    env_from_env = _csv_env(os.environ.get("NANO_CODE_SANDBOX_ENV"))
    forwarded_env = tuple(dict.fromkeys((*env_from_env, *env_from_args)))

    extra_write_args = _as_tuple(getattr(args, "sandbox_extra_write", None))
    extra_writable_roots = tuple(Path(item).expanduser().resolve() for item in extra_write_args)

    allow_fallback = bool(
        getattr(args, "sandbox_allow_local_fallback", False)
        or os.environ.get("NANO_CODE_SANDBOX_ALLOW_LOCAL_FALLBACK") == "1"
    )

    return resolve_profile(
        profile,
        network_mode=network_mode,
        readonly_workspace=readonly_workspace,
        allow_fallback_to_local=allow_fallback,
        image=image,
        cpus=cpus,
        memory_mib=memory,
        workspace_host_path=Path.cwd(),
        forwarded_env=forwarded_env,
        extra_writable_roots=extra_writable_roots,
    )


__all__ = ["build_sandbox_config", "resolve_profile"]
