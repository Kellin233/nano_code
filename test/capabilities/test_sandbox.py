"""测试 Sandbox 和 run_shell 安全执行。

验证 run_shell 在缺少 sandbox/backend 时拒绝执行并返回错误。
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

from nanocode.capabilities.tools import (
    ToolCall,
    ToolContext,
    ToolRegistry,
    ToolRuntime,
    execute_builtin_tool,
)


class FakeShell:
    """模拟 sandbox manager 的 run_shell 接口。"""

    def __init__(self, output: str = "ok") -> None:
        self.output = output
        self.calls: list[tuple[str, int, Path]] = []

    async def run_shell(self, command: str, timeout_ms: int, cwd: Path) -> str:
        self.calls.append((command, timeout_ms, cwd))
        return self.output


class FakeExecutionBackend:
    """模拟 execute_builtin_tool 路径中的 execution_backend。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, Path]] = []

    async def run_shell(self, command: str, timeout_ms: int, cwd: Path) -> str:
        self.calls.append((command, timeout_ms, cwd))
        return "from backend"


class TestRunShellNoSandbox(unittest.TestCase):
    """run_shell 在缺少 sandbox/backend 时拒绝执行。"""

    def test_run_shell_without_sandbox_manager_returns_error(self):
        runtime = ToolRuntime(
            ToolRegistry.with_builtin_tools(),
            permission_mode="bypassPermissions",
        )
        ctx = ToolContext(
            cwd=Path.cwd(),
            session_id="test",
            read_file_state={},
            sandbox_manager=None,
        )
        result = asyncio.run(runtime.execute_one(
            ToolCall(id="1", name="run_shell", input={"command": "echo dangerous"}, provider="test"),
            ctx,
        ))
        self.assertTrue(result.is_error)
        self.assertIn("requires a sandbox manager", result.content)

    def test_run_shell_with_sandbox_manager_works(self):
        shell = FakeShell("hello world")
        runtime = ToolRuntime(
            ToolRegistry.with_builtin_tools(),
            permission_mode="bypassPermissions",
        )
        ctx = ToolContext(
            cwd=Path.cwd(),
            session_id="test",
            read_file_state={},
            sandbox_manager=shell,
        )
        result = asyncio.run(runtime.execute_one(
            ToolCall(id="1", name="run_shell", input={"command": "echo hello"}, provider="test"),
            ctx,
        ))
        self.assertFalse(result.is_error)
        self.assertIn("hello world", result.content)
        self.assertEqual(len(shell.calls), 1)
        self.assertEqual(shell.calls[0][0], "echo hello")

    def test_execute_builtin_tool_run_shell_without_backend(self):
        result = asyncio.run(execute_builtin_tool(
            "run_shell",
            {"command": "echo dangerous"},
            execution_backend=None,
        ))
        self.assertIn("requires an execution backend", result)

    def test_execute_builtin_tool_run_shell_with_backend(self):
        backend = FakeExecutionBackend()
        result = asyncio.run(execute_builtin_tool(
            "run_shell",
            {"command": "echo hello", "timeout": 5000},
            execution_backend=backend,
        ))
        self.assertEqual(result, "from backend")
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(backend.calls[0][0], "echo hello")
        self.assertEqual(backend.calls[0][1], 5000)

    def test_other_tools_work_without_sandbox(self):
        """其他工具（非 shell）在无 sandbox 时仍可正常执行。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            import os
            path = Path(tmp) / "data.txt"
            path.write_text("hello world")
            old_cwd = os.getcwd()
            os.chdir(tmp)

            try:
                result = asyncio.run(execute_builtin_tool(
                    "read_file",
                    {"file_path": str(path)},
                    execution_backend=Path(tmp).home() / "nonexistent",
                ))
                # read_file 不依赖 sandbox，即使 execution_backend 为非 None 也不影响
                self.assertIn("hello world", result)
            finally:
                os.chdir(old_cwd)

    def test_invalid_timeout_returns_error(self):
        shell = FakeShell()
        runtime = ToolRuntime(
            ToolRegistry.with_builtin_tools(),
            permission_mode="bypassPermissions",
        )
        ctx = ToolContext(
            cwd=Path.cwd(),
            session_id="test",
            read_file_state={},
            sandbox_manager=shell,
        )
        result = asyncio.run(runtime.execute_one(
            ToolCall(id="1", name="run_shell", input={"command": "echo x", "timeout": "bad"}, provider="test"),
            ctx,
        ))
        self.assertTrue(result.is_error)
        self.assertIn("invalid timeout", result.content)


if __name__ == "__main__":
    unittest.main()
