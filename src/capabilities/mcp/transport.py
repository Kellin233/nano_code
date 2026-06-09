"""stdio transport for MCP JSON-RPC."""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any


class StdioTransport:
    def __init__(self, command: str, args: list[str], env: dict[str, str]):
        self.command = command
        self.args = args
        self.env = env
        self.process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        self.process = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.env,
        )

    async def write_json(self, message: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise RuntimeError("MCP transport is not connected")
        self.process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        await self.process.stdin.drain()

    async def read_stdout_line(self) -> bytes:
        if not self.process or not self.process.stdout:
            return b""
        return await self.process.stdout.readline()

    async def read_stderr_line(self) -> bytes:
        if not self.process or not self.process.stderr:
            return b""
        return await self.process.stderr.readline()

    async def close(self, timeout: float = 2.0) -> None:
        if not self.process:
            return
        process = self.process
        self.process = None
        if process.stdin:
            try:
                process.stdin.close()
            except Exception:
                pass
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
