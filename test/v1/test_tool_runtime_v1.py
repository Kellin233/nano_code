from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nanocode.capabilities.hooks import HookManager
from nanocode.capabilities.hooks.types import HookCommand
from nanocode.capabilities.tools import ToolCall, ToolContext, ToolRegistry, ToolRuntime
from nanocode.capabilities.permissions import reset_permission_cache


class FakeShell:
    def __init__(self, output: str = "ok") -> None:
        self.output = output
        self.calls: list[tuple[str, int, Path]] = []

    async def run_shell(self, command: str, timeout_ms: int, cwd: Path) -> str:
        self.calls.append((command, timeout_ms, cwd))
        return self.output


class ToolRuntimeV1Tests(unittest.TestCase):
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

    def _hook_command(self, payload: dict) -> str:
        path = self.project / f"hook_{len(list(self.project.glob('hook_*.py')))}.py"
        path.write_text(
            "import json\n"
            f"print(json.dumps({json.dumps(payload)}))\n",
            encoding="utf-8",
        )
        return f"{sys.executable} {path}"

    def _ctx(self, shell: FakeShell | None = None) -> ToolContext:
        return ToolContext(
            cwd=self.project,
            session_id="v1",
            read_file_state={},
            sandbox_manager=shell or FakeShell(),
        )

    def test_pre_hook_modified_input_still_passes_through_permission_policy(self) -> None:
        settings_dir = self.project / ".claude"
        settings_dir.mkdir()
        (settings_dir / "settings.json").write_text(json.dumps({
            "permissions": {"deny": ["run_shell(rm*)"]},
        }))
        reset_permission_cache()

        shell = FakeShell()
        runtime = ToolRuntime(
            ToolRegistry.with_builtin_tools(),
            permission_mode="bypassPermissions",
            hooks=HookManager([
                HookCommand(
                    event="PreToolUse",
                    matcher="run_shell",
                    command=self._hook_command({"action": "modify", "updated_input": {"command": "rm target"}}),
                )
            ]),
        )

        result = asyncio.run(runtime.execute_one(
            ToolCall(id="1", name="run_shell", input={"command": "echo safe"}, provider="test"),
            self._ctx(shell),
        ))

        self.assertTrue(result.is_error)
        self.assertIn("Denied by permission rule", result.content)
        self.assertEqual(shell.calls, [])

    def test_post_hook_context_survives_large_result_persistence(self) -> None:
        shell = FakeShell("x" * (35 * 1024))
        runtime = ToolRuntime(
            ToolRegistry.with_builtin_tools(),
            permission_mode="bypassPermissions",
            hooks=HookManager([
                HookCommand(
                    event="PostToolUse",
                    matcher="run_shell",
                    command=self._hook_command({"action": "append_context", "content": "post hook note"}),
                )
            ]),
        )

        result = asyncio.run(runtime.execute_one(
            ToolCall(id="1", name="run_shell", input={"command": "produce big output"}, provider="test"),
            self._ctx(shell),
        ))

        self.assertFalse(result.is_error)
        self.assertIn("Result too large", result.content)
        self.assertIn("full_result_path", result.metadata)
        self.assertTrue(Path(result.metadata["full_result_path"]).exists())
        self.assertEqual(result.extra_messages, [{"role": "user", "content": "post hook note"}])

    def test_validation_error_blocks_execution_before_shell_call(self) -> None:
        shell = FakeShell()
        runtime = ToolRuntime(ToolRegistry.with_builtin_tools(), permission_mode="bypassPermissions")

        result = asyncio.run(runtime.execute_one(
            ToolCall(id="1", name="read_file", input={}, provider="test"),
            self._ctx(shell),
        ))

        self.assertTrue(result.is_error)
        self.assertIn("missing required field: file_path", result.content)
        self.assertEqual(shell.calls, [])


if __name__ == "__main__":
    unittest.main()
