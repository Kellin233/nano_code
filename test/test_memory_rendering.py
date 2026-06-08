from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from nanocode.domains.memory.rendering import build_memory_prompt_section, format_memories_for_injection
from nanocode.domains.memory.store import save_memory
from nanocode.domains.memory.types import RelevantMemory


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


class MemoryRenderingTests(IsolatedMemoryTest):
    def test_format_memories_for_injection_includes_freshness_warning(self) -> None:
        old_mtime = (time.time() - 3 * 86400) * 1000
        memory = RelevantMemory(
            path="/tmp/user_preference.md",
            filename="user_preference.md",
            content="User prefers simple memory refactors.",
            mtime_ms=old_mtime,
            type="user",
            updated_at="2026-06-02T00:00:00+00:00",
            score=4.2,
            reason="matched terms: memory",
        )

        text = format_memories_for_injection([memory])

        self.assertIn("<system-reminder>", text)
        self.assertIn("Relevant long-term memory", text)
        self.assertIn("3 days old", text)
        self.assertIn("Score reason: matched terms: memory", text)
        self.assertIn("User prefers simple memory refactors.", text)

    def test_prompt_section_describes_write_side_compression_and_index(self) -> None:
        save_memory(
            "Memory Refactor Preference",
            "User wants low-intrusion memory refactor",
            "feedback",
            "User wants nanocode memory refactor to avoid heavy dependencies.",
            keywords=["nanocode", "memory"],
        )

        prompt = build_memory_prompt_section()

        self.assertIn("# Memory System", prompt)
        self.assertIn("keywords: comma, separated, terms", prompt)
        self.assertIn("Write a self-contained memory", prompt)
        self.assertIn("Convert relative dates to absolute dates", prompt)
        self.assertIn("Memory Refactor Preference", prompt)


if __name__ == "__main__":
    unittest.main()
