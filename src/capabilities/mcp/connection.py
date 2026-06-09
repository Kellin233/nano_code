"""Single MCP server lifecycle and JSON-RPC communication."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import deque
from pathlib import Path
from typing import Any, Callable

from .config import build_server_env
from .output import format_call_result
from .transport import StdioTransport
from .types import McpCallResult, McpResource, McpServerConfig

NotificationCallback = Callable[[str, str], None]


class McpConnection:
    def __init__(
        self,
        config: McpServerConfig,
        *,
        project_root: Path | None = None,
        notification_callback: NotificationCallback | None = None,
    ):
        self.config = config
        self.server_name = config.name
        self.project_root = (project_root or Path.cwd()).resolve()
        self._notification_callback = notification_callback
        self._transport: StdioTransport | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._stderr: deque[str] = deque(maxlen=200)
        self._debug: deque[str] = deque(maxlen=200)
        self._closed = False

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr)

    @property
    def debug_tail(self) -> str:
        return "\n".join(self._debug)

    async def connect(self) -> None:
        if self.config.transport != "stdio":
            raise RuntimeError(f"MCP transport {self.config.transport!r} is not supported")
        if not self.config.command:
            raise RuntimeError("stdio MCP server requires command")
        self._transport = StdioTransport(
            self.config.command,
            self.config.args,
            build_server_env(self.config, self.project_root),
        )
        await self._transport.start()
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._read_stderr_loop())

    async def initialize(self) -> None:
        await self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "nanocode", "version": "1.0.0"},
            },
            timeout=self.config.timeout,
        )
        await self._send_notification("notifications/initialized")

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._send_request("tools/list", timeout=self.config.timeout)
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            return []
        return [tool for tool in result["tools"] if isinstance(tool, dict) and "name" in tool]

    async def call_tool(self, name: str, args: dict[str, Any]) -> McpCallResult:
        result = await self._send_request(
            "tools/call",
            {"name": name, "arguments": args},
            timeout=self.config.call_timeout,
        )
        return format_call_result(result, self.server_name, name)

    async def list_resources(self) -> list[McpResource]:
        result = await self._send_request("resources/list", timeout=self.config.timeout)
        if not isinstance(result, dict) or not isinstance(result.get("resources"), list):
            return []
        resources: list[McpResource] = []
        for resource in result["resources"]:
            if not isinstance(resource, dict) or not resource.get("uri"):
                continue
            resources.append(McpResource(
                server_name=self.server_name,
                uri=str(resource.get("uri", "")),
                name=str(resource.get("name", "")),
                description=str(resource.get("description", "")),
                mime_type=str(resource.get("mimeType") or resource.get("mime_type") or ""),
                raw=resource,
            ))
        return resources

    async def read_resource(self, uri: str) -> McpCallResult:
        result = await self._send_request(
            "resources/read",
            {"uri": uri},
            timeout=self.config.call_timeout,
        )
        return format_call_result(result, self.server_name, f"resource:{uri}")

    async def _send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        if not self._transport:
            raise RuntimeError("MCP connection is not connected")
        loop = asyncio.get_running_loop()
        req_id = self._next_id
        self._next_id += 1
        future = loop.create_future()
        self._pending[req_id] = future
        message = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        try:
            await self._transport.write_json(message)
            try:
                return await asyncio.wait_for(future, timeout=timeout or self.config.timeout)
            except asyncio.TimeoutError as exc:
                raise asyncio.TimeoutError(
                    f"MCP request {self.server_name}/{method} timed out after {timeout or self.config.timeout:g}s"
                ) from exc
        finally:
            self._pending.pop(req_id, None)

    async def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        if not self._transport:
            return
        await self._transport.write_json({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def _read_loop(self) -> None:
        assert self._transport is not None
        try:
            while not self._closed:
                line = await self._transport.read_stdout_line()
                if not line:
                    break
                try:
                    message = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    self._debug.append("ignored invalid JSON-RPC stdout line")
                    continue

                msg_id = message.get("id")
                if msg_id is not None:
                    future = self._pending.get(msg_id)
                    if not future or future.done():
                        self._debug.append(f"ignored response for unknown request id {msg_id}")
                        continue
                    if "error" in message:
                        err = message["error"] if isinstance(message["error"], dict) else {}
                        future.set_exception(RuntimeError(f"MCP error {err.get('code')}: {err.get('message')}"))
                    else:
                        future.set_result(message.get("result"))
                    continue

                method = message.get("method")
                if isinstance(method, str) and self._notification_callback:
                    self._notification_callback(self.server_name, method)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._closed:
                self._debug.append(f"stdout reader failed: {exc}")
                self._fail_pending(RuntimeError(f"MCP server '{self.server_name}' stdout reader failed: {exc}"))
        finally:
            if not self._closed:
                self._fail_pending(RuntimeError(f"MCP server '{self.server_name}' stdout closed"))

    async def _read_stderr_loop(self) -> None:
        assert self._transport is not None
        while not self._closed:
            line = await self._transport.read_stderr_line()
            if not line:
                break
            self._stderr.append(line.decode("utf-8", errors="replace").rstrip())

    async def close(self) -> None:
        self._closed = True
        for task in (self._reader_task, self._stderr_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._reader_task = None
        self._stderr_task = None

        if self._transport:
            await self._transport.close()
            self._transport = None

        self._fail_pending(RuntimeError(f"MCP server '{self.server_name}' closed"))

    def _fail_pending(self, exc: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()
