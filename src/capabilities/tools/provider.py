"""Builtin tool runtime capability."""

from __future__ import annotations

from ...runtime.capability import CapabilityContext
from ...domains.tools import ToolDef, builtin_tool_definitions


class ToolsCapabilityProvider:
    name = "tools"

    async def initialize(self, context: CapabilityContext) -> None:
        context.state.setdefault("tools", {})

    def contribute_tools(self) -> list[ToolDef]:
        return builtin_tool_definitions()

    async def turn_attachments(self, prompt: str) -> list[str]:
        _ = prompt
        return []

    async def shutdown(self) -> None:
        return None
