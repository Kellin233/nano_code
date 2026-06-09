"""Append-only runtime event store."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..runtime.events import RuntimeEvent

SESSION_ROOT = Path.home() / ".nanocode" / "sessions"


class SessionEventStore:
    def __init__(self, session_id: str, root: Path | None = None):
        self.session_id = session_id
        self.root = root or SESSION_ROOT
        self.dir = self.root / session_id
        self.path = self.dir / "events.jsonl"

    def append(self, event: RuntimeEvent) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), ensure_ascii=False, default=str) + "\n")

    def replay(self) -> list[RuntimeEvent]:
        from ..runtime.events import RuntimeEvent

        if not self.path.exists():
            return []
        events: list[RuntimeEvent] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                events.append(RuntimeEvent.from_dict(json.loads(line)))
            except Exception:
                continue
        return events

    def extend(self, events: Iterable[RuntimeEvent]) -> None:
        for event in events:
            self.append(event)

    def next_seq(self) -> int:
        events = self.replay()
        if not events:
            return 1
        return max(event.seq for event in events) + 1

    def metadata(self) -> dict:
        events = self.replay()
        if not events:
            return {}
        first = events[0]
        last = events[-1]
        return {
            "id": self.session_id,
            "startTime": first.timestamp,
            "updatedAt": last.timestamp,
            "messageCount": sum(1 for event in events if event.type in {"user.input", "assistant.delta"}),
        }
