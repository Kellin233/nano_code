"""Memory recall orchestration for a running AgentSession."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .retrieval import format_memories_for_injection, start_memory_prefetch
from .store import mark_accessed

SideQuery = Callable[[str, str], Awaitable[str]]


class MemoryRuntime:
    def __init__(self, agent, side_query: SideQuery | None):
        self.agent = agent
        self.side_query = side_query
        self.already_surfaced: set[str] = set()
        self.session_memory_bytes = 0

    def start_prefetch(self, user_message: str) -> Any:
        if self.agent.is_sub_agent or self.side_query is None:
            return None
        return start_memory_prefetch(
            user_message,
            self.side_query,
            self.already_surfaced,
            self.session_memory_bytes,
        )

    def consume_prefetch(self, prefetch) -> None:
        if not prefetch or not prefetch.settled or prefetch.consumed:
            return
        prefetch.consumed = True
        try:
            memories = prefetch.task.result()
            if not memories:
                return
            injection_text = format_memories_for_injection(memories)
            self.agent.append_user_context(injection_text)
            for memory in memories:
                self.already_surfaced.add(memory.path)
                self.session_memory_bytes += len(memory.content.encode())
            mark_accessed([memory.path for memory in memories])
        except Exception as exc:
            self.agent._diagnostics.append(f"memory prefetch consume failed: {exc}")
