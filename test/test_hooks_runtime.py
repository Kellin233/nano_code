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


class FakeShell:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, Path]] = []

    async def run_shell(self, command: str, timeout_ms: int, cwd: Path) -> str:
        self.calls.append((command, timeout_ms, cwd))
        return "shell ok"


class HookRuntimeTests(unittest.TestCase):
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

    def _write_hook(self, body: str) -> str:
        path = self.project / "hook.py"
        path.write_text(body)
        return f"{sys.executable} {path}"

    def _run(self, runtime: ToolRuntime, command: str, shell: FakeShell):
        ctx = ToolContext(
            cwd=self.project,
            session_id="test",
            read_file_state={},
            sandbox_manager=shell,
        )
        return asyncio.run(runtime.execute_one(
            ToolCall(id="1", name="run_shell", input={"command": command}, provider="test"),
            ctx,
        ))

    def test_pre_tool_hook_can_deny_tool_execution(self) -> None:
        command = self._write_hook(
            "import json\n"
            "print(json.dumps({'action': 'deny', 'reason': 'blocked by test'}))\n"
        )
        shell = FakeShell()
        runtime = ToolRuntime(
            ToolRegistry.with_builtin_tools(),
            permission_mode="bypassPermissions",
            hooks=HookManager([HookCommand(event="PreToolUse", matcher="run_shell", command=command)]),
        )

        result = self._run(runtime, "echo ok", shell)

        self.assertTrue(result.is_error)
        self.assertIn("blocked by test", result.content)
        self.assertEqual(shell.calls, [])

    def test_pre_tool_hook_can_modify_tool_input(self) -> None:
        command = self._write_hook(
            "import json\n"
            "print(json.dumps({'action': 'modify', 'updated_input': {'command': 'echo modified'}}))\n"
        )
        shell = FakeShell()
        runtime = ToolRuntime(
            ToolRegistry.with_builtin_tools(),
            permission_mode="bypassPermissions",
            hooks=HookManager([HookCommand(event="PreToolUse", matcher="run_shell", command=command)]),
        )

        result = self._run(runtime, "echo original", shell)

        self.assertFalse(result.is_error)
        self.assertEqual(shell.calls[0][0], "echo modified")


if __name__ == "__main__":
    unittest.main()

