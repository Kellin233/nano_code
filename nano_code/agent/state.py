"""Small state containers for the event-driven agent runtime."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LoopState:
    turn_count: int = 0
    stop_reason: str = ""
    pending_context: list[str] = field(default_factory=list)


@dataclass
class SessionState:
    started: bool = False
    saved: bool = False

