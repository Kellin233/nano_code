from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from nanocode.domains.context.claude_md import load_project_instructions
from nanocode.domains.context.git_context import DISCLAIMER, collect_git_context
from nanocode.domains.context.startup import build_prompt_bundle
from nanocode.domains.context.prompt import build_system_prompt


class PromptContextRefactorTests(unittest.TestCase):
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

    def test_system_prompt_is_stable_and_startup_context_gets_project_rules(self) -> None:
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "CLAUDE.md").write_text("user rule")
        (self.project / "extra.md").write_text("included rule")
        (self.project / "CLAUDE.md").write_text("project rule\n@./extra.md")

        system_prompt = build_system_prompt(deferred_tool_names=["rare_tool"])
        bundle = build_prompt_bundle(today=date(2026, 6, 7))

        self.assertNotIn("project rule", system_prompt)
        self.assertNotIn("rare_tool", system_prompt)
        self.assertIn("Current date: 2026-06-07.", bundle.startup_context)
        self.assertIn("Working directory:", bundle.startup_context)
        self.assertIn("user rule", bundle.startup_context)
        self.assertIn("project rule", bundle.startup_context)
        self.assertIn("included rule", bundle.startup_context)

    def test_claude_loader_expands_includes_outside_code_and_records_diagnostics(self) -> None:
        rules = self.project / ".claude" / "rules"
        rules.mkdir(parents=True)
        (self.project / "ok.md").write_text("ok include")
        (self.project / "loop.md").write_text("@./loop.md")
        (self.project / "CLAUDE.md").write_text(
            "keep\n<!-- hidden -->\n@./ok.md\n```\n@./missing.md\n```\n@./loop.md"
        )
        (rules / "scoped.md").write_text("---\npaths: src/**/*.py\n---\nscoped rule")

        result = load_project_instructions(self.project)
        text = result.text

        self.assertIn("keep", text)
        self.assertIn("ok include", text)
        self.assertNotIn("hidden", text)
        self.assertIn("@./missing.md", text)
        self.assertIn("path-scoped: src/**/*.py", text)
        self.assertTrue(any("include cycle" in diagnostic.message for diagnostic in result.diagnostics))

    def test_git_context_empty_outside_repo(self) -> None:
        result = collect_git_context(self.project)

        self.assertEqual(result.text, "")
        self.assertFalse(result.diagnostics)

    def test_git_context_has_snapshot_disclaimer_inside_repo(self) -> None:
        import subprocess

        subprocess.run(["git", "init"], cwd=self.project, check=True, capture_output=True)
        (self.project / "file.txt").write_text("x")

        result = collect_git_context(self.project)

        self.assertIn(DISCLAIMER, result.text)
        self.assertIn("Status:", result.text)


if __name__ == "__main__":
    unittest.main()
