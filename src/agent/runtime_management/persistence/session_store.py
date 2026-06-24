"""Session log discovery helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .session_log import SessionLog

SESSION_DIR = Path.home() / ".nanocode" / "sessions"


def session_dir(session_id: str) -> Path:
    return SESSION_DIR / str(session_id)


def session_path(session_id: str) -> Path:
    return session_dir(session_id) / "session.jsonl"


def load_session(session_id: str) -> dict[str, Any] | None:
    log = SessionLog(session_id, root=SESSION_DIR)
    if not log.path.exists():
        return None
    metadata = log.metadata()
    if not metadata:
        return None
    return {
        "metadata": metadata,
        "conversation": log.load().snapshot(),
    }


def list_sessions() -> list[dict[str, Any]]:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    sessions: list[dict[str, Any]] = []
    for path in SESSION_DIR.glob("*/session.jsonl"):
        metadata = SessionLog(path.parent.name, root=SESSION_DIR).metadata()
        if metadata:
            sessions.append(metadata)
    sessions.sort(key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
    return sessions


def get_latest_session_id() -> str | None:
    sessions = list_sessions()
    if not sessions:
        return None
    value = sessions[0].get("id")
    return str(value) if value else None
