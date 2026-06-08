"""Skills runtime capability."""

from __future__ import annotations

from ...runtime.capability import CapabilityContext
from ...domains.skills import discover_skills
from ...domains.tools.types import ToolDef


class SkillsCapabilityProvider:
    name = "skills"

    async def initialize(self, context: CapabilityContext) -> None:
        context.state["skills"] = {"count": len(discover_skills())}

    def contribute_tools(self) -> list[ToolDef]:
        return []

    async def turn_attachments(self, prompt: str) -> list[str]:
        _ = prompt
        return []

    async def shutdown(self) -> None:
        return None
