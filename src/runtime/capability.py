"""Runtime capability lifecycle hooks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..domains.tools.types import ToolDef


@dataclass
class CapabilityContext:
    thread_id: str
    config: Any
    state: dict[str, Any]


class CapabilityProvider(Protocol):
    name: str

    async def initialize(self, context: CapabilityContext) -> None:
        ...

    def contribute_tools(self) -> list[ToolDef]:
        ...

    async def turn_attachments(self, prompt: str) -> list[str]:
        ...

    async def shutdown(self) -> None:
        ...


class CapabilityManager:
    def __init__(self, providers: list[CapabilityProvider] | None = None):
        self.providers = providers or []
        self.context: CapabilityContext | None = None

    async def initialize(self, context: CapabilityContext) -> None:
        self.context = context
        for provider in self.providers:
            await provider.initialize(context)

    def contribute_tools(self) -> list[ToolDef]:
        tools: list[ToolDef] = []
        for provider in self.providers:
            tools.extend(provider.contribute_tools())
        return tools

    async def turn_attachments(self, prompt: str) -> list[str]:
        attachments: list[str] = []
        for provider in self.providers:
            attachments.extend(await provider.turn_attachments(prompt))
        return attachments

    async def shutdown(self) -> None:
        for provider in reversed(self.providers):
            await provider.shutdown()
