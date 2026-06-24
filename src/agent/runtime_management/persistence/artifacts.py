"""Large session artifact storage."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from .atomic import write_text_atomic

ARTIFACT_ROOT = Path.home() / ".nanocode" / "artifacts"


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
        self.root = root or ARTIFACT_ROOT
        self.dir = self.root

    def write_text(self, name: str, content: str) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or "artifact.txt"
        artifact_id = f"{int(time.time() * 1000)}-{safe_name}"
        path = self.dir / artifact_id
        write_text_atomic(path, content)
        return ArtifactRef(
            session_id=self.session_id,
            artifact_id=artifact_id,
            path=str(path),
            size_bytes=len(content.encode()),
        ).to_dict()

    def write_tool_result(self, call_id: str, content: str) -> dict:
        tool_dir = self.root / "tool-results"
        tool_dir.mkdir(parents=True, exist_ok=True)
        safe_call_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", call_id).strip("-") or "tool-result"
        path = tool_dir / f"{safe_call_id}.txt"
        data = content.encode()
        write_text_atomic(path, content)
        payload = ArtifactRef(
            session_id=self.session_id,
            artifact_id=safe_call_id,
            path=str(path),
            size_bytes=len(data),
        ).to_dict()
        payload["sha256"] = sha256(data).hexdigest()
        return payload
