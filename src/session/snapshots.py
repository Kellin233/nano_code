"""Snapshot persistence for faster resume."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..logging_config import get_logger

logger = get_logger("session.snapshots")
SESSION_ROOT = Path.home() / ".nanocode" / "sessions"


class SnapshotStore:
    def __init__(self, session_id: str, root: Path | None = None):
        self.session_id = session_id
        self.root = root or SESSION_ROOT
        self.dir = self.root / session_id
        self.path = self.dir / "snapshot.json"

    def save(self, seq: int, data: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        payload = {"seq": seq, "data": data}
        self.path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def load(self) -> tuple[int, dict[str, Any]] | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return int(payload.get("seq") or 0), dict(payload.get("data") or {})
        except Exception as exc:
            logger.debug("Failed to load snapshot for %s: %s", self.session_id, exc)
            return None
