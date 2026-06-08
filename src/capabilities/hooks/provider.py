"""Hooks runtime capability."""

from __future__ import annotations

from ...runtime.capability import CapabilityContext
from ...domains.tools.types import ToolDef


class HooksCapabilityProvider:
    name = "hooks"

    async def initialize(self, context: CapabilityContext) -> None:
        context.state.setdefault("hooks", {})

    def contribute_tools(self) -> list[ToolDef]:
        return []

    async def turn_attachments(self, prompt: str) -> list[str]:
        _ = prompt
        return []

    async def shutdown(self) -> None:
        return None
