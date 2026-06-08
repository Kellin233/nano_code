"""Runtime capability adapters.

Domain logic stays in `nanocode.domains`. The modules under
`capabilities/` only connect those domains to the runtime lifecycle.
"""

from .hooks.provider import HooksCapabilityProvider
from .mcp.provider import McpCapabilityProvider
from .memory.provider import MemoryCapabilityProvider
from .skills.provider import SkillsCapabilityProvider
from .subagents.provider import SubagentsCapabilityProvider
from .tools.provider import ToolsCapabilityProvider
from ..runtime.capability import CapabilityManager


def default_capability_manager() -> CapabilityManager:
    return CapabilityManager([
        ToolsCapabilityProvider(),
        MemoryCapabilityProvider(),
        SkillsCapabilityProvider(),
        McpCapabilityProvider(),
        HooksCapabilityProvider(),
        SubagentsCapabilityProvider(),
    ])


__all__ = [
    "HooksCapabilityProvider",
    "McpCapabilityProvider",
    "MemoryCapabilityProvider",
    "SkillsCapabilityProvider",
    "SubagentsCapabilityProvider",
    "ToolsCapabilityProvider",
    "default_capability_manager",
]
