"""Runtime assembly for lightweight local memory."""

from __future__ import annotations

import time
from pathlib import Path

from ....agent.runtime_management.context.builder import SYSTEM_PROMPT_DYNAMIC_BOUNDARY, render_system_reminder
from .store import (
    append_memory,
    get_memory_dir,
    list_memory_topics,
    load_memory_index,
    read_memory_topic,
    truncate_topic_content,
    update_memory_index,
)
from .types import MAX_TOTAL_MEMORY_BYTES, TOPIC_ORDER, MemoryTopic

MEMORY_SYSTEM_SECTION = """\
# Local Memory

Local memory is project-specific, user-editable markdown context. It is point-in-time context, not live project state.

Rules:
 - Do not store code structure, file paths, architecture facts, git history, recent edits, ordinary debugging steps, or temporary task state as memory.
 - Prefer current files, git, and project instructions over memory when they conflict.
 - If memory mentions code behavior, verify it against current files before relying on it.
 - If the user says to ignore memory, treat memory as unavailable for that request.
 - Convert relative dates to absolute dates before saving memory.
 - Save memory only when the information is useful across conversations and cannot be derived from the current project state.
"""


class MemoryRuntime:
    def __init__(self, workspace: Path | str, *, enabled: bool = True):
        self.workspace = Path(workspace).resolve()
        self.enabled = enabled

    @property
    def memory_dir(self) -> Path:
        return get_memory_dir(self.workspace)

    def apply_to_system_prompt(self, system_prompt: str) -> str:
        if not self.enabled:
            return system_prompt
        section = MEMORY_SYSTEM_SECTION.strip()
        if SYSTEM_PROMPT_DYNAMIC_BOUNDARY in system_prompt:
            return system_prompt.replace(
                SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
                section + "\n\n" + SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
            )
        return system_prompt.rstrip() + "\n\n" + section

    def build_startup_context(self) -> str:
        if not self.enabled:
            return ""
        memory_dir = self.memory_dir
        topics = list_memory_topics(self.workspace)
        if topics:
            update_memory_index(self.workspace)
        index = load_memory_index(self.workspace)

        lines = [
            f"Memory directory: {memory_dir}",
            "MEMORY.md is an index. Topic files contain the actual local memory.",
        ]
        if index:
            lines.extend(["", "Memory index:", index])
        else:
            lines.extend(["", "No local memories saved yet."])

        remaining = MAX_TOTAL_MEMORY_BYTES
        for topic in topics:
            content, truncated = truncate_topic_content(topic)
            cost = len(content.encode("utf-8"))
            if cost > remaining:
                lines.extend(["", f"Memory file {topic.filename} was not loaded because the memory context budget is full."])
                continue
            remaining -= cost
            freshness = _freshness_text(topic)
            warning = (
                " Verify code-related claims against current files before relying on them."
                if _age_days(topic.mtime_ms) > 1
                else ""
            )
            truncation = "\n[Only part of this memory file was loaded.]" if truncated else ""
            lines.extend([
                "",
                f"Memory file {topic.filename} ({freshness}).{warning}",
                content.strip() + truncation,
            ])

        return render_system_reminder("Local memory for this project.", "\n".join(lines))

    def build_compact_context(self) -> str:
        if not self.enabled:
            return ""
        context = self.build_startup_context()
        if not context:
            return ""
        return context.replace(
            "Local memory for this project.",
            "Local memory refreshed after context compaction.",
            1,
        )

    def remember(self, topic: str, text: str) -> MemoryTopic:
        return append_memory(topic, text, self.workspace)

    def list_topics(self) -> list[MemoryTopic]:
        return list_memory_topics(self.workspace)

    def read_topic(self, topic: str) -> MemoryTopic | None:
        return read_memory_topic(topic, self.workspace)


def _age_days(mtime_ms: float) -> int:
    return max(0, int((time.time() * 1000 - mtime_ms) / 86_400_000))


def _freshness_text(topic: MemoryTopic) -> str:
    days = _age_days(topic.mtime_ms)
    if days == 0:
        return "saved today"
    if days == 1:
        return "saved yesterday"
    return f"saved {days} days ago"


def valid_topics_text() -> str:
    return ", ".join(TOPIC_ORDER)
