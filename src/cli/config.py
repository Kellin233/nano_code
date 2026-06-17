"""Application-layer runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..agent.agent import AgentConfig


@dataclass
class RuntimeConfig:
    model: str = "claude-opus-4-6"
    provider: str = "anthropic"
    api_base: str | None = None
    anthropic_base_url: str | None = None
    api_key: str | None = None
    thinking: bool = False
    permission_mode: str = "default"
    max_cost_usd: float | None = None
    max_turns: int | None = None
    context_window: int | None = None
    context_governance: Literal["full", "off"] = "full"
    custom_system_prompt: str | None = None
    is_sub_agent: bool = False
    sandbox_config: Any | None = None
    allowed_tools: set[str] | None = None
    workspace: Path = field(default_factory=Path.cwd)

    @property
    def use_openai(self) -> bool:
        return self.provider == "openai"

    @property
    def message_format(self) -> str:
        return "openai" if self.use_openai else "anthropic"

    def to_agent_config(self) -> AgentConfig:
        return AgentConfig(
            model=self.model,
            message_format="openai" if self.use_openai else "anthropic",
            thinking=self.thinking,
            max_cost_usd=self.max_cost_usd,
            max_turns=self.max_turns,
            context_window=self.context_window,
        )
