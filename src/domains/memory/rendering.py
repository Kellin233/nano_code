"""Prompt and injection rendering for long-term memory."""

from __future__ import annotations

import time

from .store import get_memory_dir, load_memory_index
from .types import RelevantMemory


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
Use the write_file tool to create a memory file with simple frontmatter:

```markdown
---
name: memory name
description: one-line description
type: user|feedback|project|reference
keywords: comma, separated, terms
entities: nanocode, service name
topics: coding style, memory
timestamp: absolute ISO timestamp when useful
importance: 0.5
confidence: 0.7
---
Memory content here.
```

Save to: `{memory_dir}/`
Filename format: `{{type}}_{{slugified_name}}.md`

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

