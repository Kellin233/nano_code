from __future__ import annotations

import asyncio
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nanocode.runtime.agent import Agent
from nanocode.domains.memory.retrieval import (
    pack_relevant_memories,
    select_relevant_memories,
    start_memory_prefetch,
)
from nanocode.domains.memory.store import save_memory
from nanocode.domains.memory.types import MAX_SESSION_MEMORY_BYTES, RelevantMemory


class IsolatedMemoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.project = self.root / "project"
        self.home.mkdir()
        self.project.mkdir()
        self.old_cwd = os.getcwd()
        os.chdir(self.project)
        self.home_patch = patch("pathlib.Path.home", return_value=self.home)
        self.home_patch.start()

    def tearDown(self) -> None:
        self.home_patch.stop()
        os.chdir(self.old_cwd)
        self.tmp.cleanup()


class MemoryRetrievalTests(IsolatedMemoryTest):
    def test_side_query_selects_from_local_candidates(self) -> None:
        selected = save_memory(
            "Nano Memory Rules",
            "nanocode memory rules",
            "project",
            "nanocode memory should use file-backed self-contained entries.",
            keywords=["nanocode", "memory"],
            entities=["nanocode"],
            topics=["memory"],
            importance=0.9,
        )
        save_memory(
            "Shell Preference",
            "shell output preference",
            "user",
            "User prefers concise shell output summaries.",
            keywords=["shell"],
            importance=0.5,
        )

        async def side_query(system: str, user_message: str) -> str:
            self.assertIn("Available memories", user_message)
            self.assertIn(selected, user_message)
            return f'{{"selected_memories": ["{selected}"]}}'

        memories = asyncio.run(
            select_relevant_memories("How should nanocode memory work?", side_query, set())
        )

        self.assertEqual([memory.filename for memory in memories], [selected])
        self.assertIn("self-contained", memories[0].content)

    def test_side_query_empty_selection_is_respected(self) -> None:
        save_memory(
            "Nano Memory Rules",
            "nanocode memory rules",
            "project",
            "nanocode memory should use file-backed self-contained entries.",
            keywords=["nanocode", "memory"],
        )

        async def side_query(system: str, user_message: str) -> str:
            return '{"selected_memories": []}'

        memories = asyncio.run(
            select_relevant_memories("How should nanocode memory work?", side_query, set())
        )

        self.assertEqual(memories, [])

    def test_side_query_failure_uses_local_fallback(self) -> None:
        filename = save_memory(
            "Nano Memory Rules",
            "nanocode memory rules",
            "project",
            "nanocode memory should use file-backed self-contained entries.",
            keywords=["nanocode", "memory"],
            importance=0.9,
        )

        async def side_query(system: str, user_message: str) -> str:
            raise RuntimeError("model unavailable")

        with contextlib.redirect_stdout(io.StringIO()):
            memories = asyncio.run(
                select_relevant_memories("How should nanocode memory work?", side_query, set())
            )

        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0].filename, filename)

    def test_pack_relevant_memories_enforces_byte_and_token_budgets(self) -> None:
        memories = [
            RelevantMemory(
                path="a.md",
                filename="a.md",
                content="a" * 1000,
                mtime_ms=0,
                type="project",
                updated_at="2026-06-05T00:00:00+00:00",
            ),
            RelevantMemory(
                path="b.md",
                filename="b.md",
                content="b" * 1000,
                mtime_ms=0,
                type="project",
                updated_at="2026-06-05T00:00:00+00:00",
            ),
        ]

        packed = pack_relevant_memories(memories, max_bytes=120, max_estimated_tokens=60)

        self.assertEqual(len(packed), 2)
        self.assertLessEqual(len(packed[0].content.encode()), 120)
        self.assertIn("truncated", packed[0].content)

        tight = pack_relevant_memories(memories, max_bytes=120, max_estimated_tokens=20)
        self.assertEqual(tight, [])

    def test_prefetch_gates_short_queries_budget_and_subagents(self) -> None:
        save_memory(
            "Nano Memory Rules",
            "nanocode memory rules",
            "project",
            "nanocode memory should use file-backed entries.",
            keywords=["nanocode", "memory"],
        )

        async def side_query(system: str, user_message: str) -> str:
            return '{"selected_memories": []}'

        self.assertIsNone(start_memory_prefetch("singleword", side_query, set(), 0))
        self.assertIsNone(
            start_memory_prefetch("two words", side_query, set(), MAX_SESSION_MEMORY_BYTES)
        )

        agent = Agent(api_key="test-key", is_sub_agent=True)
        self.assertIsNone(agent._start_memory_prefetch("two words"))


if __name__ == "__main__":
    unittest.main()
