"""Runtime event model and JSONL serialization helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RuntimeEvent:
    type: str
    thread_id: str
    seq: int
    payload: dict[str, Any] = field(default_factory=dict)
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
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeEvent":
        return cls(
            type=str(data["type"]),
            thread_id=str(data["thread_id"]),
            seq=int(data["seq"]),
            payload=dict(data.get("payload") or {}),
            timestamp=float(data.get("timestamp") or time.time()),
        )


@dataclass(frozen=True)
class TurnResult:
    thread_id: str
    stop_reason: str
    input_tokens: int
    output_tokens: int
    events: int
    metadata: dict[str, Any] = field(default_factory=dict)
