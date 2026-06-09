"""测试 Hooks — PreToolUse 修改输入后重新校验。"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

from nanocode.capabilities.hooks import HookManager
from nanocode.capabilities.hooks.types import HookCommand
from nanocode.capabilities.tools import (
    ToolCall,
    ToolContext,
    ToolRegistry,
    ToolRuntime,
)


class FakeShell:
    def __init__(self) -> None:
        self.calls: list = []

    async def run_shell(self, command: str, timeout_ms: int, cwd: Path) -> str:
        self.calls.append((command, timeout_ms, cwd))
        return "ok"


class TestPreToolUseRevalidation(unittest.TestCase):
    """PreToolUse hook 修改输入后重新校验。"""

    def _write_hook(self, project: Path, payload: dict) -> str:
        path = project / f"hook_{len(list(project.glob('hook_*.py')))}.py"
        path.write_text(
            f"import json\nprint(json.dumps({json.dumps(payload)}))\n",
            encoding="utf-8",
        )
        return f"{sys.executable} {path}"

    def test_hook_modified_input_missing_required_field(self):
        """hook 移除了必填字段 → 校验失败，阻断执行。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            runtime = ToolRuntime(
                ToolRegistry.with_builtin_tools(),
                permission_mode="bypassPermissions",
                hooks=HookManager([
                    HookCommand(
                        event="PreToolUse",
                        matcher="read_file",
                        command=self._write_hook(project, {
                            "action": "modify",
                            "updated_input": {"not_the_path": "x"},
                        }),
                    )
                ]),
            )
            ctx = ToolContext(
                cwd=project,
                session_id="test",
                read_file_state={},
                sandbox_manager=FakeShell(),
            )
            result = asyncio.run(runtime.execute_one(
                ToolCall(id="1", name="read_file", input={"file_path": str(project / "data.txt")}, provider="test"),
                ctx,
            ))
            self.assertTrue(result.is_error)
            self.assertIn("hook-modified input failed validation", result.content)

    def test_hook_modified_input_passes_revalidation(self):
        """hook 将输入改为合法值 → 重校验通过，正常执行。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            shell = FakeShell()
            runtime = ToolRuntime(
                ToolRegistry.with_builtin_tools(),
                permission_mode="bypassPermissions",
                hooks=HookManager([
                    HookCommand(
                        event="PreToolUse",
                        matcher="run_shell",
                        command=self._write_hook(project, {
                            "action": "modify",
                            "updated_input": {"command": "echo safe"},
                        }),
                    )
                ]),
            )
            ctx = ToolContext(
                cwd=project,
                session_id="test",
                read_file_state={},
                sandbox_manager=shell,
            )
            result = asyncio.run(runtime.execute_one(
                ToolCall(id="1", name="run_shell", input={"command": "echo original"}, provider="test"),
                ctx,
            ))
            self.assertFalse(result.is_error)
            self.assertEqual(len(shell.calls), 1)
            self.assertEqual(shell.calls[0][0], "echo safe")

    def test_multiple_hooks_each_revalidated(self):
        """多个 hook 依次 modify，每个修改都经过重校验。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            shell = FakeShell()
            runtime = ToolRuntime(
                ToolRegistry.with_builtin_tools(),
                permission_mode="bypassPermissions",
                hooks=HookManager([
                    HookCommand(
                        event="PreToolUse",
                        matcher="run_shell",
                        command=self._write_hook(project, {
                            "action": "modify",
                            "updated_input": {"command": "echo step1"},
                        }),
                    ),
                    HookCommand(
                        event="PreToolUse",
                        matcher="run_shell",
                        command=self._write_hook(project, {
                            "action": "modify",
                            "updated_input": {"command": "echo step2"},
                        }),
                    ),
                ]),
            )
            ctx = ToolContext(
                cwd=project,
                session_id="test",
                read_file_state={},
                sandbox_manager=shell,
            )
            result = asyncio.run(runtime.execute_one(
                ToolCall(id="1", name="run_shell", input={"command": "echo original"}, provider="test"),
                ctx,
            ))
            self.assertFalse(result.is_error)
            self.assertEqual(len(shell.calls), 1)
            self.assertEqual(shell.calls[0][0], "echo step2")


if __name__ == "__main__":
    unittest.main()
