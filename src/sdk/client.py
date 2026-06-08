"""Async stdio SDK client."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from typing import Any

from .thread import ThreadClient


class NanoCodeClient:
    def __init__(self, command: list[str] | None = None):
        self.command = command or [sys.executable, "-m", "nanocode", "--server", "stdio"]
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 1

    async def start(self) -> None:
        if self._proc is not None:
            return
        self._proc = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def close(self) -> None:
        if self._proc is None:
            return
        self._proc.terminate()
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=2)
        except asyncio.TimeoutError:
            self._proc.kill()
            await self._proc.wait()
        self._proc = None

    async def create_thread(self, **config: Any) -> ThreadClient:
        response = await self.request("thread.create", {"config": config})
        return ThreadClient(self, str(response["thread_id"]))

    async def resume_thread(self, thread_id: str, **config: Any) -> ThreadClient:
        await self.request("thread.resume", {"thread_id": thread_id, "config": config})
        return ThreadClient(self, thread_id)

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async for message in self.stream_request(method, params):
            if "result" in message:
                return dict(message["result"])
            if "error" in message:
                raise RuntimeError(message["error"])
        raise RuntimeError("server closed without response")

    async def stream_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        await self.start()
        assert self._proc and self._proc.stdin and self._proc.stdout
        request_id = self._next_id
        self._next_id += 1
        self._proc.stdin.write(json.dumps({
            "id": request_id,
            "method": method,
            "params": params or {},
        }).encode() + b"\n")
        await self._proc.stdin.drain()

        while True:
            raw = await self._proc.stdout.readline()
            if not raw:
                return
            message = json.loads(raw.decode())
            yield message
            if message.get("id") == request_id:
                return
