"""Memory retrieval, side-query selection, budget packing, prefetch, and rendering.

合并了原 retrieval.py（召回引擎）+ rendering.py（格式化注入）。
召回记忆和格式化注入文本是同一个流程的头尾。
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

from .store import get_memory_dir, list_memories, load_memory_index
from .types import (
    MAX_FALLBACK_MEMORIES,
    MAX_INJECTED_MEMORY_TOKENS,
    MAX_LOCAL_CANDIDATES,
    MAX_MEMORY_BYTES_PER_FILE,
    MAX_SELECTED_MEMORIES,
    MAX_SESSION_MEMORY_BYTES,
    MemoryEntry,
    MemorySearchHit,
    RelevantMemory,
)

SideQueryFn = Callable[[str, str], Any]

SELECT_MEMORIES_PROMPT = """You are selecting long-term memories for a coding agent.
Only select memories that are clearly useful for the current user request.
Prefer self-contained memories with matching entities/topics.
Do not select stale project facts unless they are directly relevant.
Return JSON: {"selected_memories": ["filename.md"]}.
- Select at most 5 memories.
- If no memories are clearly useful, return {"selected_memories": []}."""


class MemoryPrefetch:
    def __init__(self, task: asyncio.Task):
        self.task = task
        self.consumed = False

    @property
    def settled(self) -> bool:
        return self.task.done()


def _tokenize(text: str) -> list[str]:
    token: list[str] = []
    out: list[str] = []
    for ch in text.lower():
        if ch.isalnum() or ch in {"_", "-"}:
            token.append(ch)
            continue
        if token:
            item = "".join(token)
            if len(item) >= 2:
                out.append(item)
            token = []
    if token:
        item = "".join(token)
        if len(item) >= 2:
            out.append(item)
    return out


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _recency_bonus(updated_at: str) -> float:
    parsed = _parse_time(updated_at)
    if parsed is None:
        return 0.0
    age_hours = max((datetime.now(timezone.utc) - parsed).total_seconds() / 3600.0, 0.0)
    return max(0.0, 1.0 - age_hours / 72.0)


def _field_hits(terms: list[str], text: str, weight: float) -> tuple[float, list[str]]:
    lowered = text.lower()
    matched = [term for term in terms if term in lowered]
    return len(matched) * weight, matched


def _score_memory(query: str, entry: MemoryEntry) -> MemorySearchHit | None:
    terms = _tokenize(query)
    query_lower = query.strip().lower()

    keyword_text = " ".join([entry.name, entry.description, entry.content, " ".join(entry.keywords)])
    metadata_text = " ".join([entry.type, entry.timestamp, " ".join(entry.entities), " ".join(entry.topics)])

    score = 0.0
    reasons: list[str] = []
    has_relevance = False

    if query_lower and len(query_lower) >= 4 and query_lower in keyword_text.lower():
        score += 3.0
        has_relevance = True
        reasons.append("query substring")

    keyword_score, keyword_matches = _field_hits(terms, keyword_text, 1.0)
    metadata_score, metadata_matches = _field_hits(terms, metadata_text, 0.6)
    if keyword_matches:
        has_relevance = True
        score += keyword_score
        reasons.append(f"matched terms: {', '.join(keyword_matches[:5])}")
    if metadata_matches:
        has_relevance = True
        score += metadata_score
        reasons.append(f"matched metadata: {', '.join(metadata_matches[:5])}")

    if not has_relevance:
        return None

    if entry.type in {"user", "feedback"}:
        score += 0.2
    elif entry.type == "project":
        score += 0.1

    score += entry.importance * 0.5
    recent = _recency_bonus(entry.updated_at)
    if recent:
        score += recent * 0.3
    if entry.access_count:
        score += min(0.2, math.log1p(entry.access_count) * 0.05)
    if entry.confidence:
        score *= 0.8 + 0.2 * entry.confidence

    if entry.importance >= 0.8:
        reasons.append("high importance")
    if recent > 0.3:
        reasons.append("recent")

    return MemorySearchHit(entry=entry, score=score, reason="; ".join(reasons))


def _local_candidates(query: str, already_surfaced: set[str]) -> list[MemorySearchHit]:
    hits: list[MemorySearchHit] = []
    for entry in list_memories():
        if entry.file_path in already_surfaced or entry.filename in already_surfaced:
            continue
        hit = _score_memory(query, entry)
        if hit is not None:
            hits.append(hit)
    hits.sort(key=lambda hit: (hit.score, hit.entry.updated_at), reverse=True)
    return hits[:MAX_LOCAL_CANDIDATES]


def _format_manifest(hits: list[MemorySearchHit]) -> str:
    lines: list[str] = []
    for hit in hits:
        entry = hit.entry
        meta_bits = []
        if entry.keywords:
            meta_bits.append(f"keywords={', '.join(entry.keywords[:6])}")
        if entry.entities:
            meta_bits.append(f"entities={', '.join(entry.entities[:6])}")
        if entry.topics:
            meta_bits.append(f"topics={', '.join(entry.topics[:6])}")
        meta = f" [{'; '.join(meta_bits)}]" if meta_bits else ""
        lines.append(
            f"- [{entry.type}] {entry.filename} score={hit.score:.2f}: "
            f"{entry.description}{meta}"
        )
    return "\n".join(lines)


def _extract_json_object(text: str) -> dict | None:
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _truncate_bytes(text: str, max_bytes: int) -> str:
    if len(text.encode()) <= max_bytes:
        return text
    suffix = "\n\n[... truncated, memory file too large ...]"
    suffix_bytes = suffix.encode()
    if max_bytes <= len(suffix_bytes):
        return text.encode()[:max_bytes].decode(errors="ignore")
    encoded = text.encode()[:max_bytes - len(suffix_bytes)]
    return encoded.decode(errors="ignore") + suffix


def pack_relevant_memories(
    memories: list[RelevantMemory],
    max_bytes: int = MAX_MEMORY_BYTES_PER_FILE,
    max_estimated_tokens: int = MAX_INJECTED_MEMORY_TOKENS,
) -> list[RelevantMemory]:
    packed: list[RelevantMemory] = []
    used_tokens = 0
    for memory in memories:
        content = _truncate_bytes(memory.content, max_bytes)
        token_cost = estimate_tokens(content)
        if used_tokens + token_cost > max_estimated_tokens:
            break
        memory.content = content
        packed.append(memory)
        used_tokens += token_cost
    return packed


def _to_relevant(hit: MemorySearchHit) -> RelevantMemory:
    entry = hit.entry
    return RelevantMemory(
        path=entry.file_path,
        filename=entry.filename,
        content=entry.content,
        mtime_ms=entry.mtime_ms,
        type=entry.type,
        updated_at=entry.updated_at,
        score=hit.score,
        reason=hit.reason,
    )


def _fallback_hits(hits: list[MemorySearchHit]) -> list[MemorySearchHit]:
    return hits[:MAX_FALLBACK_MEMORIES]


async def select_relevant_memories(
    query: str,
    side_query: SideQueryFn,
    already_surfaced: set[str],
) -> list[RelevantMemory]:
    hits = _local_candidates(query, already_surfaced)
    if not hits:
        return []

    selected_hits: list[MemorySearchHit] | None = None
    try:
        text = await side_query(
            SELECT_MEMORIES_PROMPT,
            f"Query: {query}\n\nAvailable memories:\n{_format_manifest(hits)}",
        )
        parsed = _extract_json_object(text)
        if parsed is None:
            selected_hits = _fallback_hits(hits)
        else:
            selected_filenames = [
                str(item)
                for item in parsed.get("selected_memories", [])
                if isinstance(item, str)
            ]
            selected = set(selected_filenames[:MAX_SELECTED_MEMORIES])
            selected_hits = [hit for hit in hits if hit.entry.filename in selected]
    except Exception as exc:
        if "cancel" in str(exc).lower():
            return []
        print(f"[memory] semantic recall failed: {exc}")
        selected_hits = _fallback_hits(hits)

    if not selected_hits:
        return []
    return pack_relevant_memories([_to_relevant(hit) for hit in selected_hits[:MAX_SELECTED_MEMORIES]])


def start_memory_prefetch(
    query: str,
    side_query: SideQueryFn,
    already_surfaced: set[str],
    session_memory_bytes: int,
) -> MemoryPrefetch | None:
    if not re.search(r"\s", query.strip()):
        return None
    if session_memory_bytes >= MAX_SESSION_MEMORY_BYTES:
        return None
    if not list_memories():
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(select_relevant_memories(query, side_query, already_surfaced))
    return MemoryPrefetch(task)


# ─── 记忆格式化（原 rendering.py） ────────────────


def memory_age(mtime_ms: float) -> str:
    days = max(0, int((time.time() * 1000 - mtime_ms) / 86_400_000))
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def memory_freshness_warning(mtime_ms: float) -> str:
    days = max(0, int((time.time() * 1000 - mtime_ms) / 86_400_000))
    if days <= 1:
        return ""
    return (
        f"This memory is {days} days old. Memories are point-in-time observations, "
        "not live state. Verify code-related claims against current files before "
        "asserting them as fact."
    )


def format_memories_for_injection(memories: list[RelevantMemory]) -> str:
    parts: list[str] = []
    for memory in memories:
        warning = memory_freshness_warning(memory.mtime_ms)
        freshness = warning or f"Memory saved {memory_age(memory.mtime_ms)}."
        reason = f"\nScore reason: {memory.reason}" if memory.reason else ""
        parts.append(
            "<system-reminder>\n"
            "Relevant long-term memory. Use it as prior context, but verify "
            "code-related claims against current files.\n\n"
            f"Memory: {memory.path}\n"
            f"Type: {memory.type}\n"
            f"Updated: {memory.updated_at}\n"
            f"Freshness: {freshness}{reason}\n\n"
            f"{memory.content}\n"
            "</system-reminder>"
        )
    return "\n\n".join(parts)


def build_memory_prompt_section() -> str:
    index = load_memory_index()
    memory_dir = str(get_memory_dir())
    index_section = f"\n## Current Memory Index\n{index}" if index else "\n(No memories saved yet.)"

    return f"""# Memory System

You have a persistent, file-based memory system at `{memory_dir}`.

## Memory Types
- **user**: User's role, preferences, knowledge level
- **feedback**: Corrections and guidance from the user (include Why + How to apply)
- **project**: Ongoing work, goals, deadlines, decisions
- **reference**: Pointers to external resources (URLs, tools, dashboards)

## How to Save Memories
Use the write_file tool to create a memory file with simple frontmatter.

When saving a memory:
- Write a self-contained memory. Avoid pronouns that require prior context.
- Convert relative dates to absolute dates using the current date.
- Add keywords, entities, topics, and timestamp when useful.
- Do not save code facts that can be recovered by reading files or git history.
- For feedback memories, include Why and How to apply.

The MEMORY.md index is auto-updated when you write to the memory directory. Do not update it manually.

## What NOT to Save
- Code patterns or architecture that can be read from the code
- Git history
- Anything already in CLAUDE.md or project rules
- Ephemeral task details

## When to Recall
When the user asks you to remember or recall, or when prior context seems relevant.
{index_section}"""
