"""Execution backend interfaces and local shell backend."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Protocol

from .types import CommandResult


class SandboxBackend(Protocol):
    name: str

    async def is_available(self) -> bool:
        ...

    async def start(self) -> None:
        ...

    async def run_shell(self, command: str, timeout_ms: int, cwd: str | None = None) -> CommandResult:
        ...

    async def stop(self) -> None:
        ...


class LocalBackend:
    name = "local"

    async def is_available(self) -> bool:
        return True

    async def start(self) -> None:
        return None

    async def run_shell(self, command: str, timeout_ms: int, cwd: str | None = None) -> CommandResult:
        timeout_s = timeout_ms / 1000

        def _run() -> CommandResult:
            try:
                result = subprocess.run(
                    command,
                    shell=True,
                    cwd=str(Path(cwd)) if cwd else None,
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                )
                return CommandResult(
                    stdout=result.stdout or "",
                    stderr=result.stderr or "",
                    exit_code=result.returncode,
                    backend_name=self.name,
                )
            except subprocess.TimeoutExpired:
                return CommandResult(timed_out=True, backend_name=self.name)
            except Exception as e:
                return CommandResult(error=str(e), backend_name=self.name)

        return await asyncio.to_thread(_run)

    async def stop(self) -> None:
        return None
