from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from nanocode.capabilities.mcp.config import build_server_env, load_mcp_configs
from nanocode.capabilities.mcp.connection import McpConnection
from nanocode.capabilities.mcp.manager import McpManager
from nanocode.capabilities.mcp.output import format_call_result
from nanocode.capabilities.mcp.types import McpCallResult, McpServerConfig
from nanocode.capabilities.tools import ToolCall, ToolContext, ToolRegistry, ToolRuntime


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

    async def test_connection_uses_project_root_for_server_env(self) -> None:
        project = self.root / "project-root"
        project.mkdir()
        server = self.root / "env_mcp.py"
        server.write_text(textwrap.dedent(
            """
            import json
            import os
            import sys

            for line in sys.stdin:
                msg = json.loads(line)
                if "id" not in msg:
                    continue
                if msg["method"] == "initialize":
                    result = {"capabilities": {}}
                elif msg["method"] == "tools/call":
                    result = {"content": [{"type": "text", "text": os.environ.get("CLAUDE_PROJECT_DIR", "")}]}
                else:
                    result = {"tools": [{"name": "env"}]}
                print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
            """
        ))
        cfg = McpServerConfig(name="env", command=sys.executable, args=[str(server)])
        conn = McpConnection(cfg, project_root=project)
        await conn.connect()
        try:
            await conn.initialize()
            called = await conn.call_tool("env", {})
        finally:
            await conn.close()

        self.assertIn(str(project), called.text)

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

    async def test_timeout_late_response_is_ignored_and_next_request_succeeds(self) -> None:
        server = self.root / "late_mcp.py"
        server.write_text(textwrap.dedent(
            """
            import json
            import sys
            import time

            for line in sys.stdin:
                msg = json.loads(line)
                if "id" not in msg:
                    continue
                method = msg["method"]
                if method == "initialize":
                    time.sleep(0.1)
                    result = {"capabilities": {}}
                else:
                    result = {"tools": [{"name": "after_timeout"}]}
                print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
            """
        ))
        cfg = McpServerConfig(name="late", command=sys.executable, args=[str(server)], timeout=0.02)
        conn = McpConnection(cfg)
        await conn.connect()
        try:
            with self.assertRaises(asyncio.TimeoutError):
                await conn.initialize()
            conn.config.timeout = 1
            tools = await conn.list_tools()
            await asyncio.sleep(0.02)
        finally:
            await conn.close()

        self.assertEqual(tools[0]["name"], "after_timeout")
        self.assertIn("unknown request id 1", conn.debug_tail)

    async def test_stdout_close_fails_pending_request_without_waiting_for_timeout(self) -> None:
        server = self.root / "exit_mcp.py"
        server.write_text("import sys\nsys.stdin.readline()\n")
        cfg = McpServerConfig(name="exit", command=sys.executable, args=[str(server)], timeout=5)
        conn = McpConnection(cfg)
        await conn.connect()
        try:
            with self.assertRaisesRegex(RuntimeError, "stdout closed"):
                await asyncio.wait_for(conn.initialize(), timeout=1)
        finally:
            await conn.close()

    async def test_stderr_is_drained_and_kept_as_bounded_tail(self) -> None:
        server = self.root / "stderr_mcp.py"
        server.write_text(textwrap.dedent(
            """
            import json
            import sys

            for i in range(300):
                print(f"stderr-line-{i}-" + ("x" * 1000), file=sys.stderr, flush=True)

            for line in sys.stdin:
                msg = json.loads(line)
                if "id" not in msg:
                    continue
                result = {"capabilities": {}} if msg["method"] == "initialize" else {"tools": [{"name": "ok"}]}
                print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
            """
        ))
        cfg = McpServerConfig(name="stderr", command=sys.executable, args=[str(server)], timeout=2)
        conn = McpConnection(cfg)
        await conn.connect()
        try:
            await conn.initialize()
            tools = await conn.list_tools()
            for _ in range(20):
                if "stderr-line-299" in conn.stderr_tail:
                    break
                await asyncio.sleep(0.05)
        finally:
            await conn.close()

        self.assertEqual(tools[0]["name"], "ok")
        self.assertIn("stderr-line-299", conn.stderr_tail)
        self.assertLessEqual(len(conn.stderr_tail.splitlines()), 200)


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

    def test_structured_output_saves_large_blob_and_labels_scalar_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            payload = base64.b64encode(b"x" * (26 * 1024)).decode()
            with patch("pathlib.Path.home", return_value=home):
                blob = format_call_result(
                    {"content": [{"type": "image", "mimeType": "image/png", "data": payload}]},
                    "srv",
                    "tool",
                )
                scalar = format_call_result("plain", "srv", "tool")

                self.assertIn("[image saved to", blob.text)
                self.assertTrue(blob.saved_files)
                self.assertTrue(Path(blob.saved_files[0]).exists())
                self.assertEqual(Path(blob.saved_files[0]).read_bytes(), b"x" * (26 * 1024))
                self.assertIn("[MCP result: srv/tool]", scalar.text)

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

    def test_builtin_resources_tools_route_through_tool_runtime(self) -> None:
        class FakeManager:
            def __init__(self) -> None:
                self.calls: list[tuple[str, object]] = []

            async def list_resources(self, server: str | None = None) -> str:
                self.calls.append(("list", server))
                return "resources"

            async def read_resource(self, server: str, uri: str) -> str:
                self.calls.append(("read", (server, uri)))
                return "resource text"

        manager = FakeManager()
        registry = ToolRegistry.with_builtin_tools()
        runtime = ToolRuntime(registry, permission_mode="bypassPermissions")
        ctx = ToolContext(cwd=Path.cwd(), session_id="mcp", read_file_state={}, mcp_manager=manager)

        listed = asyncio.run(runtime.execute_one(
            ToolCall(id="1", name="list_mcp_resources", input={"server": "demo"}, provider="test"),
            ctx,
        ))
        read = asyncio.run(runtime.execute_one(
            ToolCall(id="2", name="read_mcp_resource", input={"server": "demo", "uri": "file://one"}, provider="test"),
            ctx,
        ))

        self.assertEqual(listed.content, "resources")
        self.assertEqual(read.content, "resource text")
        self.assertEqual(manager.calls, [("list", "demo"), ("read", ("demo", "file://one"))])


class McpManagerNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_changed_refresh_is_debounced_and_reports_delta(self) -> None:
        cfg = McpServerConfig(name="demo", command="python")
        manager = McpManager()
        manager._register_server_tools(cfg, [
            {"name": "old", "description": "old", "inputSchema": {"type": "object"}},
        ])
        callbacks: list[tuple[list[str], list[str], list[str]]] = []

        class FakeConnection:
            def __init__(self) -> None:
                self.config = cfg
                self.calls = 0

            async def list_tools(self) -> list[dict]:
                self.calls += 1
                return [{"name": "new", "description": "new", "inputSchema": {"type": "object"}}]

            async def close(self) -> None:
                pass

        connection = FakeConnection()
        manager._connections["demo"] = connection  # type: ignore[assignment]
        manager.set_tool_change_callback(
            lambda delta, definitions: callbacks.append((delta.added, delta.removed, delta.changed))
        )

        manager._handle_notification("demo", "notifications/tools/list_changed")
        manager._handle_notification("demo", "notifications/tools/list_changed")
        await asyncio.sleep(0.35)
        await manager.disconnect_all()

        self.assertEqual(connection.calls, 1)
        self.assertEqual(callbacks, [(["mcp__demo__new"], ["mcp__demo__old"], [])])
        self.assertEqual(manager.get_tool_definitions(), [])


if __name__ == "__main__":
    unittest.main()
