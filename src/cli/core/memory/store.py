"""Plain markdown storage for project-local memory."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ....agent.harness.persistence.atomic import write_text_atomic
from .paths import get_memory_dir as _get_memory_dir
from .types import (
    INDEX_FILENAME,
    MAX_INDEX_BYTES,
    MAX_INDEX_LINES,
    MAX_TOPIC_BYTES,
    TOPIC_ALIASES,
    TOPIC_DESCRIPTIONS,
    TOPIC_ORDER,
    MemoryTopic,
)


def get_memory_dir(workspace: Path | str | None = None, *, create: bool = True) -> Path:
    return _get_memory_dir(workspace, create=create)


def normalize_topic(topic: str) -> str:
    key = topic.strip().lower().replace("_", "-")
    key = key.replace("-", "")
    resolved = TOPIC_ALIASES.get(key, key)
    if resolved not in TOPIC_DESCRIPTIONS:
        valid = ", ".join(TOPIC_ORDER)
        raise ValueError(f"unknown memory topic '{topic}'. Expected one of: {valid}")
    return resolved


def topic_path(topic: str, workspace: Path | str | None = None) -> Path:
    resolved = normalize_topic(topic)
    return get_memory_dir(workspace) / f"{resolved}.md"


def index_path(workspace: Path | str | None = None) -> Path:
    return get_memory_dir(workspace) / INDEX_FILENAME


def is_memory_file(path: Path, workspace: Path | str | None = None) -> bool:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        resolved = candidate.resolve()
        memory_root = get_memory_dir(workspace, create=False).resolve()
    except OSError:
        return False
    if resolved.parent != memory_root:
        return False
    return resolved.name in {INDEX_FILENAME, *(f"{topic}.md" for topic in TOPIC_ORDER)}


def read_memory_topic(topic: str, workspace: Path | str | None = None) -> MemoryTopic | None:
    resolved = normalize_topic(topic)
    path = topic_path(resolved, workspace)
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        stat = path.stat()
    except OSError:
        return None
    return MemoryTopic(
        topic=resolved,
        path=path,
        description=TOPIC_DESCRIPTIONS[resolved],
        content=content,
        mtime_ms=stat.st_mtime * 1000,
    )


def list_memory_topics(workspace: Path | str | None = None) -> list[MemoryTopic]:
    topics: list[MemoryTopic] = []
    for topic in TOPIC_ORDER:
        item = read_memory_topic(topic, workspace)
        if item is not None and item.content.strip():
            topics.append(item)
    return topics


def append_memory(topic: str, text: str, workspace: Path | str | None = None, *, today: date | None = None) -> MemoryTopic:
    body = text.strip()
    if not body:
        raise ValueError("memory text cannot be empty")

    resolved = normalize_topic(topic)
    path = topic_path(resolved, workspace)
    existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else _topic_header(resolved)
    entry = _format_entry(body, today or date.today())
    content = existing.rstrip() + "\n\n" + entry + "\n"
    write_text_atomic(path, content)
    update_memory_index(workspace)
    topic_record = read_memory_topic(resolved, workspace)
    assert topic_record is not None
    return topic_record


def update_memory_index(workspace: Path | str | None = None) -> None:
    lines = ["# Memory Index", ""]
    for topic in list_memory_topics(workspace):
        lines.append(f"- [{topic.filename}]({topic.filename}): {topic.description}")
    if len(lines) == 2:
        lines.append("No local memories saved yet.")
    write_text_atomic(index_path(workspace), "\n".join(lines) + "\n")


def load_memory_index(workspace: Path | str | None = None) -> str:
    path = index_path(workspace)
    if not path.exists():
        return ""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return _truncate_index(content)


def sync_memory_file(path: Path, workspace: Path | str | None = None) -> None:
    if is_memory_file(path, workspace):
        update_memory_index(workspace)


def truncate_topic_content(topic: MemoryTopic, max_bytes: int = MAX_TOPIC_BYTES) -> tuple[str, bool]:
    return _truncate_bytes(topic.content, max_bytes)


def _topic_header(topic: str) -> str:
    title = topic.capitalize()
    return f"# {title}\n\n{TOPIC_DESCRIPTIONS[topic]}"


def _format_entry(text: str, today: date) -> str:
    lines = text.splitlines()
    if not lines:
        return f"## {today.isoformat()}\n\n-"
    rendered = [f"## {today.isoformat()}", "", f"- {lines[0].strip()}"]
    for line in lines[1:]:
        rendered.append(f"  {line.rstrip()}")
    return "\n".join(rendered)


def _truncate_index(content: str) -> str:
    lines = content.splitlines()
    truncated = False
    if len(lines) > MAX_INDEX_LINES:
        lines = lines[:MAX_INDEX_LINES]
        truncated = True
    content = "\n".join(lines)
    content, byte_truncated = _truncate_bytes(content, MAX_INDEX_BYTES)
    truncated = truncated or byte_truncated
    if truncated:
        content = content.rstrip() + (
            "\n\n[Truncated: keep MEMORY.md entries to one short line and move details into topic files.]"
        )
    return content


def _truncate_bytes(content: str, max_bytes: int) -> tuple[str, bool]:
    encoded = content.encode("utf-8")
    if len(encoded) <= max_bytes:
        return content, False
    suffix = "\n\n[Truncated: memory file exceeded context budget.]"
    suffix_bytes = suffix.encode("utf-8")
    if max_bytes <= len(suffix_bytes):
        return encoded[:max_bytes].decode("utf-8", errors="ignore"), True
    body = encoded[: max_bytes - len(suffix_bytes)].decode("utf-8", errors="ignore")
    if "\n" in body:
        body = body[: body.rfind("\n")]
    return body.rstrip() + suffix, True
