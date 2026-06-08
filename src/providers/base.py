"""Shared provider adapter contracts."""

from __future__ import annotations

from dataclasses import dataclass

from ..core import AssistantMessage, CoreToolCall, Message, ModelEvent, ModelTextDelta, ModelTurnComplete, ModelUsage
from ..domains.tools.types import ToolDef


@dataclass(frozen=True)
class ProviderConfig:
    model: str
    api_key: str | None = None
    base_url: str | None = None
    thinking: bool = False
    system_prompt: str = ""
    tools: tuple[ToolDef, ...] = ()


__all__ = [
    "AssistantMessage",
    "CoreToolCall",
    "Message",
    "ModelEvent",
    "ModelTextDelta",
    "ModelTurnComplete",
    "ModelUsage",
    "ProviderConfig",
]
