"""Multi-server MCP manager."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

from .config import load_mcp_configs
from .connection import McpConnection
from .resources import render_resource_list
from .types import McpCallResult, McpDiagnostic, McpServerConfig, McpToolDef, McpToolDelta

ToolChangeCallback = Callable[[McpToolDelta, list[dict[str, Any]]], None]


class McpManager:
    """Manage configured MCP servers and expose prefixed tool definitions."""

    def __init__(
        self,
        *,
        cwd: Path | None = None,
        on_tools_changed: ToolChangeCallback | None = None,
    ):
        self.cwd = (cwd or Path.cwd()).resolve()
        self._on_tools_changed = on_tools_changed
        self._connections: dict[str, McpConnection] = {}
        self._tools: dict[str, McpToolDef] = {}
        self._tool_routes: dict[str, tuple[str, str]] = {}
        self._connected = False
        self._diagnostics: list[McpDiagnostic] = []
        self._refresh_tasks: dict[str, asyncio.Task] = {}

    @property
    def diagnostics(self) -> list[McpDiagnostic]:
        return list(self._diagnostics)

    def set_tool_change_callback(self, callback: ToolChangeCallback | None) -> None:
        self._on_tools_changed = callback

    async def load_and_connect(self) -> None:
        if self._connected:
            return
        self._connected = True
        loaded = load_mcp_configs(self.cwd)
        self._diagnostics.extend(loaded.diagnostics)

        for config in loaded.configs.values():
            if config.transport != "stdio":
                self._diagnostics.append(McpDiagnostic("warning", config.source, f"{config.name}: transport {config.transport!r} is not supported"))
                print(f"[mcp] Skipping '{config.name}': unsupported transport {config.transport}", flush=True)
                continue
            await self._connect_one(config)

    async def _connect_one(self, config: McpServerConfig) -> None:
        connection = McpConnection(
            config,
            project_root=self.cwd,
            notification_callback=self._handle_notification,
        )
        try:
            await connection.connect()
            await connection.initialize()
            raw_tools = await connection.list_tools()
            self._connections[config.name] = connection
            delta = self._register_server_tools(config, raw_tools)
            print(f"[mcp] Connected to '{config.name}' - {len(delta.added)} tools", flush=True)
        except Exception as exc:
            tail = f"\n{connection.stderr_tail}" if connection.stderr_tail else ""
            self._diagnostics.append(McpDiagnostic("error", config.source or config.name, f"{config.name}: connect failed: {exc}{tail}"))
            print(f"[mcp] Failed to connect to '{config.name}': {exc}", flush=True)
            await connection.close()

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [self._tool_to_definition(tool) for tool in self._tools.values()]

    def is_mcp_tool(self, name: str) -> bool:
        return name in self._tool_routes or name.startswith("mcp__")

    async def call_tool(self, prefixed_name: str, args: dict[str, Any]) -> str:
        result = await self.call_tool_result(prefixed_name, args)
        return result.text

    async def call_tool_result(self, prefixed_name: str, args: dict[str, Any]) -> McpCallResult:
        route = self._tool_routes.get(prefixed_name)
        if not route:
            raise RuntimeError(f"MCP tool route not found: {prefixed_name}")
        server_name, tool_name = route
        connection = self._connections.get(server_name)
        if not connection:
            raise RuntimeError(f"MCP server '{server_name}' not connected")
        return await connection.call_tool(tool_name, args)

    async def list_resources(self, server: str | None = None) -> str:
        resources = []
        connections = self._select_connections(server)
        for name, connection in connections.items():
            try:
                resources.extend(await connection.list_resources())
            except Exception as exc:
                self._diagnostics.append(McpDiagnostic("warning", name, f"resources/list failed: {exc}"))
        return render_resource_list(resources)

    async def read_resource(self, server: str, uri: str) -> str:
        connection = self._connections.get(server)
        if not connection:
            return f"Error: MCP server '{server}' is not connected"
        try:
            result = await connection.read_resource(uri)
            return result.text
        except Exception as exc:
            self._diagnostics.append(McpDiagnostic("warning", server, f"resources/read failed for {uri}: {exc}"))
            return f"Error reading MCP resource {uri!r} from {server}: {exc}"

    async def disconnect_all(self) -> None:
        tasks = [connection.close() for connection in self._connections.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for task in self._refresh_tasks.values():
            task.cancel()
        self._connections.clear()
        self._tools.clear()
        self._tool_routes.clear()
        self._refresh_tasks.clear()
        self._connected = False

    def _select_connections(self, server: str | None) -> dict[str, McpConnection]:
        if server:
            connection = self._connections.get(server)
            return {server: connection} if connection else {}
        return dict(self._connections)

    def _handle_notification(self, server_name: str, method: str) -> None:
        if method != "notifications/tools/list_changed":
            return
        existing = self._refresh_tasks.get(server_name)
        if existing and not existing.done():
            return
        try:
            self._refresh_tasks[server_name] = asyncio.create_task(self._debounced_refresh(server_name))
        except RuntimeError:
            self._diagnostics.append(McpDiagnostic("warning", server_name, "tools/list_changed received without running event loop"))

    async def _debounced_refresh(self, server_name: str) -> None:
        await asyncio.sleep(0.2)
        connection = self._connections.get(server_name)
        if not connection:
            return
        try:
            raw_tools = await connection.list_tools()
            config = connection.config
            delta = self._register_server_tools(config, raw_tools)
            if delta.has_changes and self._on_tools_changed:
                self._on_tools_changed(delta, self.get_tool_definitions())
        except Exception as exc:
            self._diagnostics.append(McpDiagnostic("warning", server_name, f"tool refresh failed: {exc}"))

    def _register_server_tools(self, config: McpServerConfig, raw_tools: list[dict[str, Any]]) -> McpToolDelta:
        old_names = {
            name
            for name, tool in self._tools.items()
            if tool.server_name == config.name
        }
        reserved = {name for name, tool in self._tools.items() if tool.server_name != config.name}
        new_tools: dict[str, McpToolDef] = {}

        for raw_tool in raw_tools:
            raw_name = str(raw_tool.get("name", ""))
            if not raw_name:
                continue
            existing = self._find_existing_prefixed(config.name, raw_name)
            prefixed = existing or self._make_prefixed_name(config.name, raw_name, reserved | set(new_tools))
            always_load = bool(config.always_load or raw_tool.get("alwaysLoad") or raw_tool.get("always_load"))
            new_tools[prefixed] = McpToolDef(
                server_name=config.name,
                tool_name=raw_name,
                prefixed_name=prefixed,
                description=str(raw_tool.get("description") or f"MCP tool {raw_name} from {config.name}"),
                input_schema=copy.deepcopy(raw_tool.get("inputSchema") or raw_tool.get("input_schema") or {"type": "object", "properties": {}}),
                deferred=not always_load,
                always_load=always_load,
                raw=copy.deepcopy(raw_tool),
            )

        new_names = set(new_tools)
        removed = sorted(old_names - new_names)
        added = sorted(new_names - old_names)
        changed = sorted(
            name for name in old_names & new_names
            if _tool_signature(self._tools[name]) != _tool_signature(new_tools[name])
        )

        for name in removed:
            self._tools.pop(name, None)
            self._tool_routes.pop(name, None)
        for name, tool in new_tools.items():
            self._tools[name] = tool
            self._tool_routes[name] = (tool.server_name, tool.tool_name)

        return McpToolDelta(added=added, removed=removed, changed=changed)

    def _tool_to_definition(self, tool: McpToolDef) -> dict[str, Any]:
        return {
            "name": tool.prefixed_name,
            "description": tool.description,
            "input_schema": tool.input_schema or {"type": "object", "properties": {}},
            "deferred": tool.deferred,
            "origin": "mcp",
            "concurrency_safe": False,
            "read_only": False,
            "mcp_server": tool.server_name,
            "mcp_tool": tool.tool_name,
            "search_hint": tool.raw.get("searchHint") or tool.raw.get("search_hint", ""),
        }

    def _find_existing_prefixed(self, server_name: str, tool_name: str) -> str | None:
        for prefixed, route in self._tool_routes.items():
            if route == (server_name, tool_name):
                return prefixed
        return None

    def _make_prefixed_name(self, server_name: str, tool_name: str, reserved: set[str]) -> str:
        base = f"mcp__{_sanitize_name(server_name)}__{_sanitize_name(tool_name)}"
        name = _shorten_name(base, server_name, tool_name)
        if name not in reserved:
            return name
        suffix = _hash_suffix(server_name, tool_name)
        candidate = _shorten_name(f"{base}_{suffix}", server_name, tool_name)
        index = 2
        while candidate in reserved:
            candidate = _shorten_name(f"{base}_{suffix}_{index}", server_name, tool_name)
            index += 1
        return candidate


def _sanitize_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "unnamed"


def _shorten_name(value: str, server_name: str, tool_name: str, limit: int = 120) -> str:
    if len(value) <= limit:
        return value
    suffix = _hash_suffix(server_name, tool_name)
    return value[: limit - len(suffix) - 1].rstrip("_") + "_" + suffix


def _hash_suffix(server_name: str, tool_name: str) -> str:
    return hashlib.sha1(f"{server_name}\0{tool_name}".encode("utf-8")).hexdigest()[:8]


def _tool_signature(tool: McpToolDef) -> str:
    return json.dumps(
        {
            "description": tool.description,
            "input_schema": tool.input_schema,
            "deferred": tool.deferred,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
