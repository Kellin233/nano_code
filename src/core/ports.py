"""Core-facing ports.

The core loop depends on these protocols only. Provider SDKs, local tool
implementations, session files, TUI, and server transports live outside core.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from .messages import CoreToolCall, CoreToolResult, Message, ModelEvent


class ModelProvider(Protocol):
    async def stream_turn(self, messages: list[Message]) -> AsyncIterator[ModelEvent]:
        ...


class ToolExecutor(Protocol):
    async def execute(self, calls: list[CoreToolCall]) -> list[CoreToolResult]:
        ...
