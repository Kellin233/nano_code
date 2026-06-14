"""Types and constants for lightweight local memory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

INDEX_FILENAME = "MEMORY.md"

TOPIC_DESCRIPTIONS: dict[str, str] = {
    "preferences": "User preferences and behavior feedback.",
    "project": "Project decisions, goals, external constraints, and references not derivable from code.",
    "debugging": "Stable environment, tool, and test gotchas.",
}

TOPIC_ALIASES: dict[str, str] = {
    "preference": "preferences",
    "prefs": "preferences",
    "pref": "preferences",
    "feedback": "preferences",
    "user": "preferences",
    "proj": "project",
    "reference": "project",
    "references": "project",
    "debug": "debugging",
    "gotcha": "debugging",
    "gotchas": "debugging",
}

TOPIC_ORDER = ("preferences", "project", "debugging")

MAX_INDEX_LINES = 200
MAX_INDEX_BYTES = 25_000
MAX_TOPIC_BYTES = 16 * 1024
MAX_TOTAL_MEMORY_BYTES = 40 * 1024


@dataclass(frozen=True)
class MemoryTopic:
    topic: str
    path: Path
    description: str
    content: str
    mtime_ms: float

    @property
    def filename(self) -> str:
        return self.path.name
