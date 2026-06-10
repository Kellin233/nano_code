"""API object passed to Python extensions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from ....agent.types import ToolCall, ToolDef, ToolResult

ExtensionHandler = Callable[[dict[str, Any]], Awaitable[None] | None]
ExtensionTool = Callable[[dict[str, Any], Any], Awaitable[ToolResult | str] | ToolResult | str]


class ExtensionAPI:
    def __init__(self, runner, tool_registry):
        self.runner = runner
        self.tool_registry = tool_registry

    def register_tool(
        self,
        definition: ToolDef,
        handler: ExtensionTool,
        *,
        deferred: bool = False,
        read_only: bool = False,
        edit_tool: bool = False,
        concurrency_safe: bool = False,
    ) -> None:
        tool = dict(definition)
        tool["deferred"] = deferred
        tool["read_only"] = read_only
        tool["edit_tool"] = edit_tool
        tool["concurrency_safe"] = concurrency_safe
        self.tool_registry.register(tool, call_fn=handler, origin="extension")

    def on(self, event: str, handler: ExtensionHandler) -> None:
        self.runner.on(event, handler)

    def register_command(self, name: str, handler: ExtensionHandler) -> None:
        self.runner.register_command(name, handler)

    async def emit_tool_before(self, call: ToolCall) -> None:
        await self.runner.emit("before_tool_call", {"call": call})

    async def emit_tool_after(self, call: ToolCall, result: ToolResult) -> None:
        await self.runner.emit("after_tool_call", {"call": call, "result": result})
