from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from nano_code.mcp.config import build_server_env, load_mcp_configs
from nano_code.mcp.connection import McpConnection
from nano_code.mcp.manager import McpManager
from nano_code.mcp.output import format_call_result
from nano_code.mcp.types import McpCallResult, McpServerConfig
from nano_code.tools import ToolCall, ToolContext, ToolRegistry, ToolRuntime


class McpConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.project = self.root / "project"
        self.home.mkdir()
        self.project.mkdir()
        self.home_patch = patch("pathlib.Path.home", return_value=self.home)
        self.home_patch.start()

    def tearDown(self) -> None:
        self.home_patch.stop()
        self.tmp.cleanup()

    def test_config_precedence_env_expansion_and_project_dir_env(self) -> None:
        os.environ["MCP_TOKEN"] = "secret"
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "settings.json").write_text(json.dumps({
            "mcpServers": {
                "demo": {"command": "old", "args": ["${MISSING:-fallback}"]},
            }
        }))
        (self.project / ".mcp.json").write_text(json.dumps({
            "mcpServers": {
                "demo": {"command": "${MCP_TOKEN}", "args": ["ok"], "env": {"A": "${UNKNOWN}"}},
            }
        }))

        loaded = load_mcp_configs(self.project, home=self.home)
        cfg = loaded.configs["demo"]
        env = build_server_env(cfg, self.project)

        self.assertEqual(cfg.command, "secret")
        self.assertEqual(cfg.args, ["ok"])
        self.assertEqual(env["CLAUDE_PROJECT_DIR"], str(self.project))
        self.assertTrue(any("UNKNOWN" in diagnostic.message for diagnostic in loaded.diagnostics))


class McpConnectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.server = self.root / "fake_mcp.py"
        self.server.write_text(textwrap.dedent(
            """
            import json
            import sys

            tools = [{"name": "echo", "description": "Echo text", "inputSchema": {"type": "object"}}]

            for line in sys.stdin:
                msg = json.loads(line)
                if "id" not in msg:
                    continue
                method = msg["method"]
                if method == "initialize":
                    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"capabilities": {}}}), flush=True)
                elif method == "tools/list":
                    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": tools}}), flush=True)
                elif method == "tools/call":
                    text = msg["params"]["arguments"].get("text", "")
                    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"content": [{"type": "text", "text": text}], "isError": False}}), flush=True)
                elif method == "resources/list":
                    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"resources": [{"uri": "file://one", "name": "one", "mimeType": "text/plain"}]}}), flush=True)
                elif method == "resources/read":
                    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"contents": [{"uri": msg["params"]["uri"], "mimeType": "text/plain", "text": "resource text"}]}}), flush=True)
            """
        ))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    async def test_stdio_connection_tools_calls_and_resources(self) -> None:
        cfg = McpServerConfig(name="demo", command=sys.executable, args=[str(self.server)])
        conn = McpConnection(cfg)
        await conn.connect()
        try:
            await conn.initialize()
            tools = await conn.list_tools()
            called = await conn.call_tool("echo", {"text": "hello"})
            resources = await conn.list_resources()
            resource = await conn.read_resource("file://one")
        finally:
            await conn.close()

        self.assertEqual(tools[0]["name"], "echo")
        self.assertIn("hello", called.text)
        self.assertEqual(resources[0].uri, "file://one")
        self.assertIn("resource text", resource.text)

    async def test_request_timeout_cleans_pending(self) -> None:
        server = self.root / "silent_mcp.py"
        server.write_text("import time; time.sleep(10)\n")
        cfg = McpServerConfig(name="silent", command=sys.executable, args=[str(server)], timeout=0.05)
        conn = McpConnection(cfg)
        await conn.connect()
        try:
            with self.assertRaises(asyncio.TimeoutError):
                await conn.initialize()
            self.assertEqual(conn._pending, {})
        finally:
            await conn.close()


class McpManagerOutputTests(unittest.TestCase):
    def test_manager_sanitizes_names_and_uses_route_map(self) -> None:
        manager = McpManager()
        cfg = McpServerConfig(name="demo server", command="python")

        delta = manager._register_server_tools(cfg, [
            {"name": "tool/with space", "description": "demo", "inputSchema": {"type": "object"}},
        ])
        defs = manager.get_tool_definitions()

        self.assertEqual(delta.added, ["mcp__demo_server__tool_with_space"])
        self.assertEqual(defs[0]["mcp_server"], "demo server")
        self.assertEqual(manager._tool_routes["mcp__demo_server__tool_with_space"], ("demo server", "tool/with space"))
        self.assertTrue(defs[0]["deferred"])

    def test_structured_output_preserves_error_and_unknown_blocks(self) -> None:
        result = format_call_result(
            {
                "isError": True,
                "content": [
                    {"type": "text", "text": "bad"},
                    {"type": "custom", "value": 1},
                ],
            },
            "srv",
            "tool",
        )

        self.assertTrue(result.is_error)
        self.assertIn("[MCP tool error]", result.text)
        self.assertIn('"value": 1', result.text)

    def test_registry_preserves_mcp_is_error_when_manager_returns_structured_result(self) -> None:
        class FakeManager:
            async def call_tool_result(self, name: str, args: dict) -> McpCallResult:
                return McpCallResult(text="[MCP tool error]\nbad", is_error=True, saved_files=["/tmp/out"])

        registry = ToolRegistry()
        registry.add_many([
            {"name": "mcp__srv__bad", "description": "bad", "input_schema": {"type": "object"}}
        ], origin="mcp")
        runtime = ToolRuntime(registry, permission_mode="bypassPermissions")
        ctx = ToolContext(cwd=Path.cwd(), session_id="mcp", read_file_state={}, mcp_manager=FakeManager())

        result = asyncio.run(runtime.execute_one(
            ToolCall(id="1", name="mcp__srv__bad", input={}, provider="test"),
            ctx,
        ))

        self.assertTrue(result.is_error)
        self.assertEqual(result.metadata["saved_files"], ["/tmp/out"])


if __name__ == "__main__":
    unittest.main()
