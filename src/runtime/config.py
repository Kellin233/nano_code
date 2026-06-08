"""Runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..domains.sandbox import SandboxConfig

ProviderKind = Literal["anthropic", "openai"]


@dataclass
class RuntimeConfig:
    model: str = "claude-opus-4-6"
    provider: ProviderKind = "anthropic"
    api_base: str | None = None
    anthropic_base_url: str | None = None
    api_key: str | None = None
    thinking: bool = False
    permission_mode: str = "default"
    max_cost_usd: float | None = None
    max_turns: int | None = None
    custom_system_prompt: str | None = None
    is_sub_agent: bool = False
    sandbox_config: SandboxConfig | None = None
    workspace: Path = field(default_factory=Path.cwd)

    @property
    def use_openai(self) -> bool:
        return self.provider == "openai"
