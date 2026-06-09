from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from nanocode.capabilities.memory.store import get_memory_dir, load_memory_index
from nanocode.capabilities.tools.builtin import edit_file, write_file
from nanocode.capabilities.tools.builtin import builtin_tool_definitions
from nanocode.capabilities.permissions import check_permission, reset_permission_cache
from nanocode.capabilities.tools.registry import ToolRegistry
from nanocode.capabilities.tools.runtime import execute_builtin_tool
from nanocode.capabilities.tools.types import ToolMetadata


class IsolatedToolTest(unittest.TestCase):
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
        reset_permission_cache()

    def tearDown(self) -> None:
        reset_permission_cache()
        self.home_patch.stop()
        os.chdir(self.old_cwd)
        self.tmp.cleanup()


class PermissionTests(IsolatedToolTest):
    def test_read_only_tools_are_allowed_by_default(self) -> None:
        registry = ToolRegistry.with_builtin_tools()

        decision = check_permission(
            "read_file",
            {"file_path": "a.py"},
            metadata=registry.metadata_for("read_file"),
        )

        self.assertEqual(decision.action, "allow")

    def test_dangerous_shell_confirms_and_dontask_denies(self) -> None:
        default = check_permission("run_shell", {"command": "rm file.txt"})
        dont_ask = check_permission("run_shell", {"command": "rm file.txt"}, mode="dontAsk")

        self.assertEqual(default.action, "confirm")
        self.assertEqual(default.message, "rm file.txt")
        self.assertEqual(dont_ask.action, "deny")
        self.assertIn("Auto-denied (dontAsk mode): rm file.txt", dont_ask.message)

    def test_deny_rules_take_priority_over_allow_rules(self) -> None:
        settings_dir = self.project / ".claude"
        settings_dir.mkdir()
        (settings_dir / "settings.json").write_text(json.dumps({
            "permissions": {
                "allow": ["run_shell(rm*)"],
                "deny": ["run_shell(rm*)"],
            }
        }))
        reset_permission_cache()

        decision = check_permission("run_shell", {"command": "rm target"})

        self.assertEqual(decision.action, "deny")
        self.assertEqual(decision.message, "Denied by permission rule for run_shell")

    def test_deny_rules_take_priority_over_bypass_permissions(self) -> None:
        settings_dir = self.project / ".claude"
        settings_dir.mkdir()
        (settings_dir / "settings.json").write_text(json.dumps({
            "permissions": {
                "deny": ["run_shell(git push*)"],
            }
        }))
        reset_permission_cache()

        decision = check_permission(
            "run_shell",
            {"command": "git push origin main"},
            mode="bypassPermissions",
        )

        self.assertEqual(decision.action, "deny")

    def test_mcp_server_level_permission_rule_matches_prefixed_tools(self) -> None:
        settings_dir = self.project / ".claude"
        settings_dir.mkdir()
        (settings_dir / "settings.json").write_text(json.dumps({
            "permissions": {
                "deny": ["mcp__github"],
            }
        }))
        reset_permission_cache()

        decision = check_permission("mcp__github__list_issues", {"owner": "acme"})

        self.assertEqual(decision.action, "deny")
        self.assertEqual(decision.message, "Denied by permission rule for mcp__github__list_issues")

    def test_protected_paths_are_not_bypassed_by_yolo(self) -> None:
        git_dir = self.project / ".git"
        git_dir.mkdir()
        target = git_dir / "config"

        decision = check_permission(
            "write_file",
            {"file_path": str(target)},
            mode="bypassPermissions",
        )

        self.assertEqual(decision.action, "confirm")
        self.assertIn("protected path", decision.message)

    def test_accept_edits_allows_builtin_edit_tools(self) -> None:
        path = self.project / "existing.txt"
        path.write_text("old")
        registry = ToolRegistry.with_builtin_tools()

        decision = check_permission(
            "edit_file",
            {"file_path": str(path)},
            mode="acceptEdits",
            metadata=registry.metadata_for("edit_file"),
        )

        self.assertEqual(decision.action, "allow")

    def test_metadata_missing_edit_flag_does_not_get_accept_edits_allow(self) -> None:
        decision = check_permission(
            "write_file",
            {"file_path": str(self.project / "new.txt")},
            mode="acceptEdits",
            metadata=ToolMetadata(name="write_file", origin="custom"),
        )

        self.assertEqual(decision.action, "confirm")
        self.assertEqual(decision.message, f"write new file: {self.project / 'new.txt'}")

    def test_file_existence_checks_use_passed_cwd(self) -> None:
        subdir = self.project / "pkg"
        subdir.mkdir()
        (subdir / "existing.txt").write_text("old")

        decision = check_permission(
            "write_file",
            {"file_path": "existing.txt"},
            cwd=subdir,
        )

        self.assertEqual(decision.action, "allow")


class RegistryTests(IsolatedToolTest):
    def _deferred_tool(self, name: str = "deferred_read") -> dict:
        return {
            "name": name,
            "description": "Deferred file reader",
            "input_schema": {"type": "object", "properties": {}},
            "deferred": True,
            "origin": "custom",
            "concurrency_safe": True,
            "read_only": True,
        }

    def test_builtin_tools_keep_original_order(self) -> None:
        registry = ToolRegistry.with_builtin_tools()

        self.assertEqual(
            [tool["name"] for tool in registry.active_definitions()],
            [tool["name"] for tool in builtin_tool_definitions()],
        )

    def test_deferred_tools_activate_through_search_and_are_sanitized(self) -> None:
        registry = ToolRegistry.with_builtin_tools()
        registry.add_many([self._deferred_tool()], origin="custom")

        self.assertNotIn("deferred_read", [tool["name"] for tool in registry.active_definitions()])
        matches = registry.search_deferred("reader")

        self.assertEqual([tool["name"] for tool in matches], ["deferred_read"])
        self.assertIn("deferred_read", [tool["name"] for tool in registry.active_definitions()])
        for key in {"deferred", "origin", "concurrency_safe", "read_only", "edit_tool"}:
            self.assertNotIn(key, matches[0])
            activated = next(t for t in registry.active_definitions() if t["name"] == "deferred_read")
            self.assertNotIn(key, activated)

    def test_deferred_search_supports_select_and_server_filter(self) -> None:
        registry = ToolRegistry()
        registry.add_many([
            {
                "name": "mcp__github__list_issues",
                "description": "List issues",
                "input_schema": {"type": "object"},
                "deferred": True,
                "origin": "mcp",
                "mcp_server": "github",
                "mcp_tool": "list_issues",
            },
            {
                "name": "mcp__linear__list_issues",
                "description": "List issues",
                "input_schema": {"type": "object"},
                "deferred": True,
                "origin": "mcp",
                "mcp_server": "linear",
                "mcp_tool": "list_issues",
            },
        ], origin="mcp")

        selected = registry.search_deferred("select:mcp__github__list_issues")
        filtered = registry.search_deferred("+linear issues")

        self.assertEqual([tool["name"] for tool in selected], ["mcp__github__list_issues"])
        self.assertEqual([tool["name"] for tool in filtered], ["mcp__linear__list_issues"])

    def test_registry_instances_do_not_share_deferred_state(self) -> None:
        first = ToolRegistry([self._deferred_tool()])
        second = ToolRegistry([self._deferred_tool()])

        first.search_deferred("reader")

        self.assertIn("deferred_read", [tool["name"] for tool in first.active_definitions()])
        self.assertNotIn("deferred_read", [tool["name"] for tool in second.active_definitions()])

    def test_mcp_and_custom_tools_are_not_concurrency_safe_by_default(self) -> None:
        tool = {"name": "custom_tool", "description": "Custom", "input_schema": {"type": "object"}}
        registry = ToolRegistry()
        registry.add_many([tool], origin="custom")
        registry.add_many([{**tool, "name": "mcp__srv__tool"}], origin="mcp")

        self.assertFalse(registry.is_concurrency_safe("custom_tool"))
        self.assertFalse(registry.is_concurrency_safe("mcp__srv__tool"))


class RuntimeTests(IsolatedToolTest):
    def run_tool(self, name: str, inp: dict, state: dict[str, float] | None = None) -> str:
        return asyncio.run(execute_builtin_tool(name, inp, state))

    def test_existing_file_must_be_read_before_write_or_edit(self) -> None:
        path = self.project / "target.txt"
        path.write_text("old")
        state: dict[str, float] = {}

        write_result = self.run_tool("write_file", {"file_path": str(path), "content": "new"}, state)
        edit_result = self.run_tool(
            "edit_file",
            {"file_path": str(path), "old_string": "old", "new_string": "new"},
            state,
        )

        self.assertIn("You must read this file before writing", write_result)
        self.assertIn("You must read this file before editing", edit_result)

    def test_read_then_write_and_edit_are_allowed(self) -> None:
        path = self.project / "target.txt"
        path.write_text("old")
        state: dict[str, float] = {}

        self.run_tool("read_file", {"file_path": str(path)}, state)
        write_result = self.run_tool("write_file", {"file_path": str(path), "content": "middle"}, state)
        edit_result = self.run_tool(
            "edit_file",
            {"file_path": str(path), "old_string": "middle", "new_string": "new"},
            state,
        )

        self.assertIn("Successfully wrote", write_result)
        self.assertIn("Successfully edited", edit_result)
        self.assertEqual(path.read_text(), "new")

    def test_external_modification_after_read_returns_warning(self) -> None:
        path = self.project / "target.txt"
        path.write_text("old")
        state: dict[str, float] = {}

        self.run_tool("read_file", {"file_path": str(path)}, state)
        time.sleep(0.01)
        path.write_text("changed")
        os.utime(path, None)
        result = self.run_tool(
            "edit_file",
            {"file_path": str(path), "old_string": "changed", "new_string": "new"},
            state,
        )

        self.assertIn("was modified externally since your last read", result)

    def test_unknown_builtin_tool_and_truncation(self) -> None:
        unknown = self.run_tool("missing_tool", {})
        huge = self.project / "huge.txt"
        huge.write_text("a" * 60000)
        result = self.run_tool("read_file", {"file_path": str(huge)}, {})

        self.assertEqual(unknown, "Unknown tool: missing_tool")
        self.assertIn("[... truncated", result)


class EditToolTests(IsolatedToolTest):
    def test_missing_and_duplicate_old_string_errors(self) -> None:
        path = self.project / "target.txt"
        path.write_text("one two two")

        missing = edit_file({"file_path": str(path), "old_string": "three", "new_string": "x"})
        duplicate = edit_file({"file_path": str(path), "old_string": "two", "new_string": "x"})

        self.assertEqual(missing, f"Error: old_string not found in {path}")
        self.assertEqual(duplicate, f"Error: old_string found 2 times in {path}. Must be unique.")

    def test_quote_normalization_can_match(self) -> None:
        path = self.project / "quotes.py"
        left = chr(0x201C)
        right = chr(0x201D)
        path.write_text(f"print({left}hello{right})")

        result = edit_file({
            "file_path": str(path),
            "old_string": 'print("hello")',
            "new_string": 'print("hi")',
        })

        self.assertIn("matched via quote normalization", result)
        self.assertEqual(path.read_text(), 'print("hi")')


class MemoryIndexTests(IsolatedToolTest):
    def test_write_file_updates_active_memory_index(self) -> None:
        mem_dir = get_memory_dir()

        write_file({
            "file_path": str(mem_dir / "user_active.md"),
            "content": """---
name: Active Tool Memory
description: indexed from tool write
type: user
status: active
importance: 0.6
---
Remember tool writes.
""",
        })

        index = load_memory_index()
        self.assertIn("Active Tool Memory", index)
        self.assertIn("[importance=0.60]", index)


if __name__ == "__main__":
    unittest.main()
