"""Data structures and constants for long-term memory."""

from __future__ import annotations

from dataclasses import dataclass, field


VALID_TYPES = {"user", "feedback", "project", "reference"}
VALID_STATUSES = {"active", "superseded", "archived"}

MAX_INDEX_LINES = 200
MAX_INDEX_BYTES = 25000
MAX_MEMORY_FILES = 200
MAX_LOCAL_CANDIDATES = 20
MAX_SELECTED_MEMORIES = 5
MAX_FALLBACK_MEMORIES = 3
MAX_MEMORY_BYTES_PER_FILE = 4096
MAX_SESSION_MEMORY_BYTES = 60 * 1024
MAX_INJECTED_MEMORY_TOKENS = 1200


@dataclass
class MemoryEntry:
    memory_id: str
    name: str
    description: str
    type: str
    filename: str
    content: str
    status: str = "active"
    keywords: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    timestamp: str = ""
    importance: float = 0.5
    confidence: float = 0.7
    access_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    last_accessed_at: str = ""
    superseded_by: str = ""
    file_path: str = ""
    mtime_ms: float = 0.0
    extra: dict[str, str] = field(default_factory=dict)


@dataclass
class MemorySearchHit:
    entry: MemoryEntry
    score: float
    reason: str = ""


@dataclass
class RelevantMemory:
    path: str
    filename: str
    content: str
    mtime_ms: float
    type: str
    updated_at: str
    score: float = 0.0
    reason: str = ""


@dataclass
class ConsolidationAction:
    action: str
    filename: str
    reason: str
    target: str = ""
    new_importance: float | None = None


@dataclass
class ConsolidationResult:
    dry_run: bool
    actions: list[ConsolidationAction] = field(default_factory=list)

    @property
    def superseded_count(self) -> int:
        return sum(1 for action in self.actions if action.action == "supersede")

    @property
    def archived_count(self) -> int:
        return sum(1 for action in self.actions if action.action == "archive")

    @property
    def decayed_count(self) -> int:
        return sum(1 for action in self.actions if action.action == "decay")

