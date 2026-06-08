"""microsandbox-backed command execution."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import uuid

from .types import CommandResult, SandboxConfig, text_or_empty


class MicrosandboxBackend:
    name = "microsandbox"

    def __init__(self, config: SandboxConfig, *, session_id: str = ""):
        self.config = config
        suffix = session_id or uuid.uuid4().hex[:8]
        self.sandbox_name = f"nanocode-{suffix}-{uuid.uuid4().hex[:6]}"
        self._sandbox = None
        self._run_lock = asyncio.Lock()

    def sdk_importable(self) -> bool:
        return importlib.util.find_spec("microsandbox") is not None

    async def is_available(self) -> bool:
        return self.sdk_importable()

    async def start(self) -> None:
        if self._sandbox is not None:
            return
        if not self.sdk_importable():
            raise RuntimeError(
                "microsandbox Python SDK is not installed. Install nanocode with the "
                "sandbox extra or run `pip install microsandbox`, or use --sandbox local."
            )

        module = importlib.import_module("microsandbox")
        Sandbox = module.Sandbox
        Volume = module.Volume
        Network = getattr(module, "Network", None)

        mount = Volume.bind(
            str(self.config.resolved_workspace_host_path()),
            readonly=self.config.workspace_mode == "read-only",
        )
        kwargs = {
            "image": self.config.image,
            "cpus": self.config.cpus,
            "memory": self.config.memory_mib,
            "workdir": self.config.workspace_guest_path,
            "volumes": {self.config.workspace_guest_path: mount},
            "replace": True,
        }
        if self.config.network_mode == "none":
            if Network is None:
                raise RuntimeError("microsandbox Network.none() is unavailable in this SDK version")
            kwargs["network"] = Network.none()

        try:
            self._sandbox = await asyncio.wait_for(
                Sandbox.create(self.sandbox_name, **kwargs),
                timeout=self.config.startup_timeout_s,
            )
        except Exception as e:
            raise RuntimeError(
                f"failed to start microsandbox {self.sandbox_name!r} with image "
                f"{self.config.image!r}: {e}"
            ) from e

    async def run_shell(self, command: str, timeout_ms: int, cwd: str | None = None) -> CommandResult:
        if self._sandbox is None:
            await self.start()
        assert self._sandbox is not None

        async with self._run_lock:
            try:
                output = await self._sandbox.shell(
                    command,
                    cwd=cwd or self.config.workspace_guest_path,
                    timeout=timeout_ms / 1000,
                )
                return CommandResult(
                    stdout=text_or_empty(getattr(output, "stdout_text", "")),
                    stderr=text_or_empty(getattr(output, "stderr_text", "")),
                    exit_code=int(getattr(output, "exit_code", 0)),
                    backend_name=self.name,
                )
            except Exception as e:
                name = e.__class__.__name__.lower()
                if "timeout" in name:
                    return CommandResult(timed_out=True, backend_name=self.name)
                return CommandResult(error=str(e), backend_name=self.name)

    async def stop(self) -> None:
        if self._sandbox is None:
            return
        sandbox = self._sandbox
        self._sandbox = None
        try:
            stop_and_wait = getattr(sandbox, "stop_and_wait", None)
            if stop_and_wait is not None:
                await stop_and_wait()
            else:
                await sandbox.stop()
        except Exception as e:
            print(f"[sandbox] Stop failed: {e}", flush=True)


__all__ = ["MicrosandboxBackend"]
