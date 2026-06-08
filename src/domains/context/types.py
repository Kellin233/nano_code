"""Lightweight prompt context types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class PromptDiagnostic:
    level: Literal["info", "warning", "error"]
    source: str
    message: str


@dataclass(frozen=True)
class ContextAttachment:
    title: str
    body: str


@dataclass
class PromptBundle:
    system_prompt: str
    startup_context: str
    diagnostics: list[PromptDiagnostic] = field(default_factory=list)
