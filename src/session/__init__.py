"""Session persistence entrypoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .artifacts import ArtifactRef, ArtifactStore
from .event_store import SessionEventStore
from .snapshots import SnapshotStore

SESSION_DIR = Path.home() / ".nanocode" / "sessions"


def _ensure_dir() -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)


def save_session(session_id: str, data: dict[str, Any]) -> None:
    """Save a session snapshot used by the internal agent adapter."""
    _ensure_dir()
    (SESSION_DIR / f"{session_id}.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def load_session(session_id: str) -> dict[str, Any] | None:
    path = SESSION_DIR / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_sessions() -> list[dict[str, Any]]:
    _ensure_dir()
    results = []
    for path in SESSION_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if "metadata" in data:
                results.append(data["metadata"])
        except Exception:
            pass
    for path in SESSION_DIR.glob("*/events.jsonl"):
        try:
            store = SessionEventStore(path.parent.name)
            metadata = store.metadata()
            if metadata:
                results.append(metadata)
        except Exception:
            pass
    return results


def get_latest_session_id() -> str | None:
    sessions = list_sessions()
    if not sessions:
        return None
    sessions.sort(key=lambda item: str(item.get("startTime", "") or item.get("updatedAt", "")), reverse=True)
    return sessions[0].get("id")


__all__ = [
    "ArtifactRef",
    "ArtifactStore",
    "SessionEventStore",
    "SnapshotStore",
    "get_latest_session_id",
    "list_sessions",
    "load_session",
    "save_session",
]
