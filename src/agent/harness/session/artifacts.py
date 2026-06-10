"""Large session artifact storage."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

SESSION_ROOT = Path.home() / ".nanocode" / "sessions"


@dataclass(frozen=True)
class ArtifactRef:
    session_id: str
    artifact_id: str
    path: str
    size_bytes: int

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "artifact_id": self.artifact_id,
            "path": self.path,
            "size_bytes": self.size_bytes,
        }


class ArtifactStore:
    def __init__(self, session_id: str, root: Path | None = None):
        self.session_id = session_id
        self.root = root or SESSION_ROOT
        self.dir = self.root / session_id / "artifacts"

    def write_text(self, name: str, content: str) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or "artifact.txt"
        artifact_id = f"{int(time.time() * 1000)}-{safe_name}"
        path = self.dir / artifact_id
        path.write_text(content, encoding="utf-8")
        return ArtifactRef(
            session_id=self.session_id,
            artifact_id=artifact_id,
            path=str(path),
            size_bytes=len(content.encode()),
        ).to_dict()
