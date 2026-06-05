from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nano_code.memory.store import (
    get_memory,
    get_memory_dir,
    list_memories,
    load_memory_index,
    mark_accessed,
    save_memory,
)
from nano_code.tools.builtin import write_file


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


class MemoryStoreTests(IsolatedMemoryTest):
    def test_reads_legacy_memory_with_defaults(self) -> None:
        mem_dir = get_memory_dir()
        (mem_dir / "user_legacy.md").write_text(
            """---
name: Legacy Preference
description: User prefers practical code
type: user
---
User prefers simple, maintainable implementations.
"""
        )

        memories = list_memories()

        self.assertEqual(len(memories), 1)
        memory = memories[0]
        self.assertEqual(memory.name, "Legacy Preference")
        self.assertEqual(memory.type, "user")
        self.assertEqual(memory.status, "active")
        self.assertEqual(memory.importance, 0.5)
        self.assertEqual(memory.confidence, 0.7)
        self.assertEqual(memory.keywords, [])
        self.assertTrue(memory.memory_id.startswith("legacy-"))
        self.assertTrue(memory.created_at)
        self.assertTrue(memory.updated_at)

    def test_save_memory_writes_simple_metadata_and_marks_access(self) -> None:
        filename = save_memory(
            "Nano Code Memory",
            "Memory design preference",
            "feedback",
            "User wants nano_code memory to stay simple and file-backed.",
            keywords=["nano_code", "memory"],
            entities=["nano_code"],
            topics=["memory management"],
            timestamp="2026-06-05T00:00:00+00:00",
            importance=0.8,
        )

        mem_dir = get_memory_dir()
        raw = (mem_dir / filename).read_text()
        self.assertIn("keywords: nano_code, memory", raw)
        self.assertNotIn("keywords: [", raw)

        mark_accessed([str(mem_dir / filename)])
        memory = get_memory(filename)
        self.assertIsNotNone(memory)
        self.assertEqual(memory.access_count, 1)
        self.assertTrue(memory.last_accessed_at)

    def test_tool_write_updates_active_only_memory_index(self) -> None:
        mem_dir = get_memory_dir()
        ordinary = self.project / "note.md"
        write_file({"file_path": str(ordinary), "content": "not a memory"})
        self.assertFalse((mem_dir / "MEMORY.md").exists())

        write_file(
            {
                "file_path": str(mem_dir / "user_active.md"),
                "content": """---
name: Active Memory
description: should be indexed
type: user
status: active
importance: 0.7
---
Active memory body.
""",
            }
        )
        write_file(
            {
                "file_path": str(mem_dir / "project_archived.md"),
                "content": """---
name: Archived Memory
description: should not be indexed
type: project
status: archived
importance: 0.9
---
Archived memory body.
""",
            }
        )

        index = load_memory_index()
        self.assertIn("Active Memory", index)
        self.assertIn("[importance=0.70]", index)
        self.assertNotIn("Archived Memory", index)


if __name__ == "__main__":
    unittest.main()
