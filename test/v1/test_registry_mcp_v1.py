from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from nano_code.tools import ToolCall, ToolContext, ToolRegistry, ToolRuntime


class FakeMcpManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, args: dict) -> str:
        self.calls.append((name, args))
        return f"mcp result: {name}={args.get('value')}"


class RegistryMcpV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        self.old_cwd = os.getcwd()
        os.chdir(self.project)

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def _ctx(self, mcp=None) -> ToolContext:
        return ToolContext(
            cwd=self.project,
            session_id="v1",
            read_file_state={},
            mcp_manager=mcp,
        )

    def test_mcp_tool_routes_through_registry_contract(self) -> None:
        registry = ToolRegistry()
        registry.add_many([
            {
                "name": "mcp__demo__tool__with__separator",
                "description": "MCP demo",
                "input_schema": {"type": "object", "properties": {"value": {"type": "string"}}},
                "origin": "mcp",
                "concurrency_safe": True,
                "read_only": True,
            }
        ], origin="mcp")
        mcp = FakeMcpManager()
        runtime = ToolRuntime(registry, permission_mode="bypassPermissions")

        result = asyncio.run(runtime.execute_one(
            ToolCall(
                id="1",
                name="mcp__demo__tool__with__separator",
                input={"value": "ok"},
                provider="test",
            ),
            self._ctx(mcp),
        ))

        self.assertFalse(result.is_error)
        self.assertEqual(result.content, "mcp result: mcp__demo__tool__with__separator=ok")
        self.assertEqual(mcp.calls, [("mcp__demo__tool__with__separator", {"value": "ok"})])
        self.assertTrue(registry.is_concurrency_safe("mcp__demo__tool__with__separator"))
        active = registry.active_definitions()[0]
        self.assertNotIn("origin", active)
        self.assertNotIn("concurrency_safe", active)
        self.assertNotIn("read_only", active)

    def test_mcp_tool_without_manager_returns_tool_error(self) -> None:
        registry = ToolRegistry()
        registry.add_many([
            {"name": "mcp__demo__missing", "description": "MCP", "input_schema": {"type": "object"}}
        ], origin="mcp")
        runtime = ToolRuntime(registry, permission_mode="bypassPermissions")

        result = asyncio.run(runtime.execute_one(
            ToolCall(id="1", name="mcp__demo__missing", input={}, provider="test"),
            self._ctx(None),
        ))

        self.assertTrue(result.is_error)
        self.assertIn("MCP manager unavailable", result.content)


if __name__ == "__main__":
    unittest.main()
