"""Extension event dispatcher."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from ....agent.types import RuntimeEvent, ToolCall, ToolResult

Handler = Callable[[dict[str, Any]], Awaitable[None] | None]


class ExtensionRunner:
    def __init__(self):
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._commands: dict[str, Handler] = {}
        self.errors: list[str] = []

    def on(self, event: str, handler: Handler) -> None:
        self._handlers[event].append(handler)

    def register_command(self, name: str, handler: Handler) -> None:
        self._commands[name] = handler

    async def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        for handler in list(self._handlers.get(event, [])):
            try:
                result = handler(payload or {})
                if hasattr(result, "__await__"):
                    await result  # type: ignore[misc]
            except Exception as exc:
                self.errors.append(f"{event}: {exc}")

    async def on_runtime_event(self, event: RuntimeEvent) -> None:
        await self.emit(event.type.replace(".", "_"), {"event": event})

    async def before_tool_call(self, call: ToolCall) -> None:
        await self.emit("before_tool_call", {"call": call})

    async def after_tool_call(self, call: ToolCall, result: ToolResult) -> None:
        await self.emit("after_tool_call", {"call": call, "result": result})

    async def run_command(self, name: str, payload: dict[str, Any] | None = None) -> bool:
        handler = self._commands.get(name)
        if handler is None:
            return False
        try:
            result = handler(payload or {})
            if hasattr(result, "__await__"):
                await result  # type: ignore[misc]
        except Exception as exc:
            self.errors.append(f"command:{name}: {exc}")
        return True
