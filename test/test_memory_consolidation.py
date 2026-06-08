from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nanocode.domains.memory.consolidation import consolidate_memories
from nanocode.domains.memory.store import get_memory, get_memory_dir, list_memories, save_memory


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


class MemoryConsolidationTests(IsolatedMemoryTest):
    def test_exact_duplicate_supersedes_lower_importance_without_deleting(self) -> None:
        low = save_memory(
            "Duplicate Low",
            "duplicate memory",
            "project",
            "nanocode memory should stay file-backed and self-contained.",
            importance=0.4,
        )
        high = save_memory(
            "Duplicate High",
            "duplicate memory",
            "project",
            "nanocode memory should stay file-backed and self-contained.",
            importance=0.8,
        )

        dry = consolidate_memories(dry_run=True)
        self.assertEqual(dry.superseded_count, 1)
        self.assertEqual(get_memory(low).status, "active")

        applied = consolidate_memories(dry_run=False)

        self.assertEqual(applied.superseded_count, 1)
        low_entry = get_memory(low)
        high_entry = get_memory(high)
        self.assertEqual(low_entry.status, "superseded")
        self.assertEqual(low_entry.superseded_by, high_entry.memory_id)
        self.assertTrue((get_memory_dir() / low).exists())
        self.assertEqual([memory.filename for memory in list_memories()], [high])

    def test_decay_and_archive_are_soft_maintenance_actions(self) -> None:
        decayed = save_memory(
            "Old Medium",
            "old medium importance memory",
            "project",
            "Old medium-importance memory that has not been accessed recently.",
            importance=0.6,
            updated_at="2025-01-01T00:00:00+00:00",
        )
        archived = save_memory(
            "Old Low",
            "old low importance memory",
            "project",
            "Old low-importance memory that should be archived softly.",
            importance=0.15,
            updated_at="2025-01-01T00:00:00+00:00",
        )

        dry = consolidate_memories(dry_run=True)
        self.assertEqual(dry.decayed_count, 1)
        self.assertEqual(dry.archived_count, 1)

        consolidate_memories(dry_run=False)

        decayed_entry = get_memory(decayed)
        archived_entry = get_memory(archived)
        self.assertLess(decayed_entry.importance, 0.6)
        self.assertEqual(archived_entry.status, "archived")
        self.assertTrue((get_memory_dir() / archived).exists())


if __name__ == "__main__":
    unittest.main()
