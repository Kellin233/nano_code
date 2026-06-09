"""Session-scoped sandbox backend manager."""

from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath

from ...logging_config import get_logger
from .backend import LocalBackend, SandboxBackend
from .types import SandboxConfig

logger = get_logger("sandbox.manager")


class SandboxManager:
    def __init__(
        self,
        config: SandboxConfig | None = None,
        *,
        session_id: str = "",
        backend: SandboxBackend | None = None,
    ):
        self.config = config or SandboxConfig()
        self.session_id = session_id
        self._backend = backend
        self._started = False
        self._startup_error = ""
        self._lock = asyncio.Lock()

    @property
    def backend_name(self) -> str:
        return self._backend.name if self._backend else self.config.backend

    def _build_backend(self) -> SandboxBackend:
        if self.config.backend == "local":
            return LocalBackend()
        if self.config.backend == "bwrap":
            from .bwrap_backend import BwrapBackend

            return BwrapBackend(self.config)
        if self.config.backend == "microsandbox":
            from .microsandbox_backend import MicrosandboxBackend

            return MicrosandboxBackend(self.config, session_id=self.session_id)
        raise ValueError(f"invalid sandbox backend: {self.config.backend}")

    def host_path_to_guest_path(self, path: str | Path | None = None) -> str:
        host_path = Path(path or Path.cwd()).resolve()
        workspace = self.config.resolved_workspace_host_path()
        try:
            rel = host_path.relative_to(workspace)
        except ValueError:
            raise ValueError(f"cwd is outside sandbox workspace: {host_path}") from None

        guest_base = PurePosixPath(self.config.workspace_guest_path)
        if str(rel) == ".":
            return str(guest_base)
        return str(guest_base.joinpath(*rel.parts))

    def _resolve_workspace_cwd(self, path: str | Path | None = None) -> Path:
        host_path = Path(path or Path.cwd()).resolve()
        workspace = self.config.resolved_workspace_host_path()
        try:
            host_path.relative_to(workspace)
        except ValueError:
            raise ValueError(f"cwd is outside sandbox workspace: {host_path}") from None
        return host_path

    async def _ensure_started(self) -> SandboxBackend:
        async with self._lock:
            if self._backend is None:
                self._backend = self._build_backend()
            if not self._started:
                if not await self._backend.is_available():
                    if self._can_fallback_to_local(self._backend.name):
                        logger.warning("%s unavailable; falling back to local", self._backend.name)
                        self._startup_error = (
                            f"{self._backend.name} unavailable; explicitly falling back to local"
                        )
                        self._backend = LocalBackend()
                    else:
                        raise RuntimeError(self._unavailable_message(self._backend.name))
                await self._backend.start()
                self._started = True
            return self._backend

    def _can_fallback_to_local(self, backend_name: str) -> bool:
        if backend_name == "local":
            return False
        if self.config.profile == "microsandbox-strict":
            return False
        return self.config.allow_fallback_to_local

    def _unavailable_message(self, backend_name: str) -> str:
        if backend_name == "bwrap":
            return (
                "bubblewrap is not available. Install `bubblewrap` to use the "
                f"{self.config.profile!r} sandbox profile, or explicitly use `--sandbox local`."
            )
        if backend_name == "microsandbox":
            return (
                "microsandbox Python SDK is not installed. Install nanocode with the "
                "sandbox extra or run `pip install microsandbox`, or explicitly use `--sandbox local`."
            )
        return f"sandbox backend is unavailable: {backend_name}"

    async def run_shell(self, command: str, timeout_ms: int, cwd: str | Path | None = None) -> str:
        try:
            backend = await self._ensure_started()
            if backend.name == "microsandbox":
                backend_cwd = self.host_path_to_guest_path(cwd)
            elif backend.name == "bwrap":
                backend_cwd = str(self._resolve_workspace_cwd(cwd))
            else:
                backend_cwd = str(Path(cwd or Path.cwd()).resolve())
            result = await backend.run_shell(command, timeout_ms, backend_cwd)
            return result.to_tool_output(timeout_ms)
        except Exception as e:
            logger.error("Sandbox run_shell failed: %s", e)
            self._startup_error = str(e)
            return f"Error: {e}"

    def describe(self) -> str:
        workspace = self.config.resolved_workspace_host_path()
        writable = self.config.workspace_mode != "read-only"
        protected = ", ".join(self.config.protected_paths) or "(none)"
        forwarded = ", ".join(self.config.forwarded_env) or "(none)"
        isolation = {
            "local": "none",
            "bwrap": "OS-level sandbox",
            "microsandbox": "microVM",
        }.get(self.config.backend, "unknown")
        home_mounted = "true" if self.config.backend == "local" else "false"
        secrets = (
            "host env available (local execution)"
            if self.config.backend == "local"
            else f"host env not forwarded; allowlist {forwarded}"
        )
        return "\n".join(
            [
                f"Sandbox profile: {self.config.profile}",
                f"Backend: {self.backend_name}",
                f"Shell isolation: {isolation}",
                f"Workspace: {workspace}",
                f"Workspace writable: {str(writable).lower()}",
                f"Network: {self.config.network_mode}",
                f"Home mounted: {home_mounted}",
                f"Protected paths: {protected}",
                f"Secrets: {secrets}",
                f"Fallback to local: {str(self.config.allow_fallback_to_local).lower()}",
            ]
        )

    async def stop(self) -> None:
        if not self._backend or not self._started:
            return
        try:
            await self._backend.stop()
        finally:
            self._started = False
