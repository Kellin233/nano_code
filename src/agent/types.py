"""Core protocol types shared by agent, providers, and application code."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

ToolDef = dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]
    provider: str = "model"


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    extra_messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class RuntimeEvent:
    """Unified runtime event emitted by the agent loop."""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    thread_id: str = ""
    seq: int = 0
    timestamp: float = field(default_factory=lambda: time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "thread_id": self.thread_id,
            "seq": self.seq,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeEvent:
        return cls(
            type=str(data.get("type", "")),
            thread_id=str(data.get("thread_id", "")),
            seq=int(data.get("seq", 0)),
            payload=dict(data.get("payload") or {}),
            timestamp=float(data.get("timestamp", time.time())),
        )


CONTEXT_WINDOW_MARGIN = 20000
DEFAULT_MAX_TOKENS = 16384
MAX_RETRIES = 3
MAX_RETRY_DELAY_MS = 30000
