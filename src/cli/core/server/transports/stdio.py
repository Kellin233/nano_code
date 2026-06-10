"""JSONL over stdin/stdout transport."""

from __future__ import annotations

import asyncio
import json
import sys

from ...protocol import ProtocolDispatcher
from ..app_server import NanoCodeServer


class StdioTransport:
    def __init__(self, server: NanoCodeServer | None = None):
        self.server = server or NanoCodeServer()
        self.dispatcher = ProtocolDispatcher(self.server)
        self._write_lock = asyncio.Lock()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        tasks: set[asyncio.Task] = set()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                return
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                await self._write({"id": None, "error": {"code": "parse_error", "message": str(exc)}})
                continue
            task = asyncio.create_task(self._handle(data))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    async def _handle(self, data: dict) -> None:
        async for message in self.dispatcher.dispatch(data):
            await self._write(message)

    async def _write(self, message: dict) -> None:
        async with self._write_lock:
            sys.stdout.write(json.dumps(message, ensure_ascii=False, default=str) + "\n")
            sys.stdout.flush()


async def run_stdio_server() -> None:
    await StdioTransport().run()
