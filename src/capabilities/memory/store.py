"""File-backed storage for structured long-term memory."""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...context.sources import format_frontmatter, parse_frontmatter
from .types import (
    MAX_INDEX_BYTES,
    MAX_INDEX_LINES,
    MemoryEntry,
    VALID_STATUSES,
    VALID_TYPES,
)

INDEX_FILENAME = "MEMORY.md"
LIST_FIELDS = {"keywords", "entities", "topics"}
FIXED_META_ORDER = [
    "memory_id",
    "name",
    "description",
    "type",
    "status",
    "keywords",
    "entities",
    "topics",
    "timestamp",
    "importance",
    "confidence",
    "access_count",
    "created_at",
    "updated_at",
    "last_accessed_at",
    "superseded_by",
]


def _project_hash() -> str:
    return hashlib.sha256(str(Path.cwd()).encode()).hexdigest()[:16]


def get_memory_dir() -> Path:
    path = Path.home() / ".nanocode" / "projects" / _project_hash() / "memory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get_index_path() -> Path:
    return get_memory_dir() / INDEX_FILENAME


def is_memory_file(path: Path) -> bool:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    if candidate.suffix != ".md" or candidate.name == INDEX_FILENAME:
        return False
    try:
        return candidate.resolve().is_relative_to(get_memory_dir().resolve())
    except OSError:
        return False


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return (slug or "memory")[:40]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mtime_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
    except OSError:
        return _now_iso()


def _stable_memory_id(filename: str) -> str:
    digest = hashlib.sha256(filename.encode()).hexdigest()[:12]
    return f"legacy-{digest}"


def _parse_list(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        text = str(value or "")
        raw_items = text.split(",")
    return [str(item).strip() for item in raw_items if str(item).strip()]


def _format_list(values: list[str]) -> str:
    return ", ".join(value.strip() for value in values if value.strip())


def _parse_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def _parse_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def _parse_dt_sort(value: str) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _memory_from_file(path: Path) -> MemoryEntry | None:
    try:
        stat = path.stat()
        result = parse_frontmatter(path.read_text())
    except Exception:
        return None

    meta = result.meta
    name = meta.get("name", "").strip()
    if not name:
        return None

    memory_type = meta.get("type", "project").strip()
    if memory_type not in VALID_TYPES:
        memory_type = "project"

    status = meta.get("status", "active").strip()
    if status not in VALID_STATUSES:
        status = "active"

    fallback_time = _mtime_iso(path)
    fixed_keys = set(FIXED_META_ORDER)
    extra = {key: value for key, value in meta.items() if key not in fixed_keys}

    return MemoryEntry(
        memory_id=meta.get("memory_id", "").strip() or _stable_memory_id(path.name),
        name=name,
        description=meta.get("description", "").strip(),
        type=memory_type,
        filename=path.name,
        content=result.body,
        status=status,
        keywords=_parse_list(meta.get("keywords", "")),
        entities=_parse_list(meta.get("entities", "")),
        topics=_parse_list(meta.get("topics", "")),
        timestamp=meta.get("timestamp", "").strip(),
        importance=_parse_float(meta.get("importance"), 0.5),
        confidence=_parse_float(meta.get("confidence"), 0.7),
        access_count=_parse_int(meta.get("access_count"), 0),
        created_at=meta.get("created_at", "").strip() or fallback_time,
        updated_at=meta.get("updated_at", "").strip() or fallback_time,
        last_accessed_at=meta.get("last_accessed_at", "").strip(),
        superseded_by=meta.get("superseded_by", "").strip(),
        file_path=str(path),
        mtime_ms=stat.st_mtime * 1000,
        extra=extra,
    )


def list_memories(include_inactive: bool = False) -> list[MemoryEntry]:
    entries: list[MemoryEntry] = []
    for path in sorted(get_memory_dir().glob("*.md")):
        if path.name == INDEX_FILENAME:
            continue
        entry = _memory_from_file(path)
        if entry is None:
            continue
        if not include_inactive and entry.status != "active":
            continue
        entries.append(entry)
    entries.sort(key=lambda entry: (_parse_dt_sort(entry.updated_at), entry.filename), reverse=True)
    return entries


def get_memory(filename: str) -> MemoryEntry | None:
    path = get_memory_dir() / Path(filename).name
    if path.name == INDEX_FILENAME or not path.exists():
        return None
    return _memory_from_file(path)


def _entry_meta(entry: MemoryEntry) -> dict[str, str]:
    meta: dict[str, str] = {
        "memory_id": entry.memory_id,
        "name": entry.name,
        "description": entry.description,
        "type": entry.type,
        "status": entry.status,
        "keywords": _format_list(entry.keywords),
        "entities": _format_list(entry.entities),
        "topics": _format_list(entry.topics),
        "timestamp": entry.timestamp,
        "importance": f"{entry.importance:.2f}",
        "confidence": f"{entry.confidence:.2f}",
        "access_count": str(entry.access_count),
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
        "last_accessed_at": entry.last_accessed_at,
        "superseded_by": entry.superseded_by,
    }
    for key, value in entry.extra.items():
        if key not in meta:
            meta[key] = str(value)
    return meta


def _write_entry(path: Path, entry: MemoryEntry) -> None:
    path.write_text(format_frontmatter(_entry_meta(entry), entry.content))


def save_memory(name: str, description: str, type: str, content: str, **meta: Any) -> str:
    memory_type = type if type in VALID_TYPES else "project"
    now = _now_iso()
    filename = f"{memory_type}_{_slugify(name)}.md"
    entry = MemoryEntry(
        memory_id=str(meta.get("memory_id") or f"{int(time.time())}-{hashlib.sha256(filename.encode()).hexdigest()[:8]}"),
        name=name,
        description=description,
        type=memory_type,
        filename=filename,
        content=content,
        status=str(meta.get("status") or "active"),
        keywords=_parse_list(meta.get("keywords", "")),
        entities=_parse_list(meta.get("entities", "")),
        topics=_parse_list(meta.get("topics", "")),
        timestamp=str(meta.get("timestamp") or ""),
        importance=_parse_float(meta.get("importance"), 0.5),
        confidence=_parse_float(meta.get("confidence"), 0.7),
        access_count=_parse_int(meta.get("access_count"), 0),
        created_at=str(meta.get("created_at") or now),
        updated_at=str(meta.get("updated_at") or now),
        last_accessed_at=str(meta.get("last_accessed_at") or ""),
        superseded_by=str(meta.get("superseded_by") or ""),
        extra={key: str(value) for key, value in meta.items() if key not in set(FIXED_META_ORDER) | LIST_FIELDS},
    )
    if entry.status not in VALID_STATUSES:
        entry.status = "active"
    path = get_memory_dir() / filename
    entry.file_path = str(path)
    _write_entry(path, entry)
    update_memory_index()
    return filename


def delete_memory(filename: str) -> bool:
    path = get_memory_dir() / Path(filename).name
    if path.name == INDEX_FILENAME or not path.exists():
        return False
    path.unlink()
    update_memory_index()
    return True


def mark_accessed(filenames: list[str]) -> None:
    now = _now_iso()
    for filename in filenames:
        entry = get_memory(Path(filename).name)
        if entry is None:
            continue
        entry.access_count += 1
        entry.last_accessed_at = now
        try:
            _write_entry(Path(entry.file_path), entry)
        except Exception:
            continue


def update_status(filename: str, status: str, superseded_by: str = "") -> bool:
    if status not in VALID_STATUSES:
        return False
    entry = get_memory(Path(filename).name)
    if entry is None:
        return False
    entry.status = status
    entry.superseded_by = superseded_by
    entry.updated_at = _now_iso()
    _write_entry(Path(entry.file_path), entry)
    update_memory_index()
    return True


def update_importance(filename: str, importance: float) -> bool:
    entry = get_memory(Path(filename).name)
    if entry is None:
        return False
    entry.importance = max(0.0, min(1.0, importance))
    entry.updated_at = _now_iso()
    _write_entry(Path(entry.file_path), entry)
    update_memory_index()
    return True


def sync_memory_file(path: Path) -> None:
    if is_memory_file(path):
        update_memory_index()


def _format_index_line(entry: MemoryEntry) -> str:
    description = entry.description.strip()
    suffix = f" - {description}" if description else ""
    return f"- **[{entry.name}]({entry.filename})** ({entry.type}) [importance={entry.importance:.2f}]{suffix}"


def update_memory_index() -> None:
    lines = ["# Memory Index", ""]
    for entry in list_memories():
        lines.append(_format_index_line(entry))
    _get_index_path().write_text("\n".join(lines))


def load_memory_index() -> str:
    path = _get_index_path()
    if not path.exists():
        return ""
    content = path.read_text()
    lines = content.split("\n")
    if len(lines) > MAX_INDEX_LINES:
        content = "\n".join(lines[:MAX_INDEX_LINES])
        content += "\n\n[... truncated, too many memory entries. Keep each memory index entry to one short line and archive stale memories ...]"
    if len(content.encode()) > MAX_INDEX_BYTES:
        content = content[:MAX_INDEX_BYTES]
        content += "\n\n[... truncated, index too large. Keep memory descriptions short and archive stale memories ...]"
    return content
