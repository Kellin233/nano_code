from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from nanocode.capabilities.sandbox import (
    BwrapBackend,
    CommandResult,
    SandboxConfig,
    SandboxManager,
    build_sandbox_config,
)
from nanocode.capabilities.tools.runtime import execute_builtin_tool


class FakeBackend:
    name = "microsandbox"

    def __init__(self, result: CommandResult | None = None):
        self.result = result or CommandResult(stdout="ok\n", backend_name=self.name)
        self.started = False
        self.stopped = False
        self.calls: list[tuple[str, int, str | None]] = []

    async def is_available(self) -> bool:
        return True

    async def start(self) -> None:
        self.started = True

    async def run_shell(self, command: str, timeout_ms: int, cwd: str | None = None) -> CommandResult:
        self.calls.append((command, timeout_ms, cwd))
        return self.result

    async def stop(self) -> None:
        self.stopped = True


class FakeToolBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, Path]] = []

    async def run_shell(self, command: str, timeout_ms: int, cwd: Path) -> str:
        self.calls.append((command, timeout_ms, cwd))
        return "from fake backend"


class SandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"
        self.subdir = self.workspace / "pkg"
        self.outside = self.root / "outside"
        self.subdir.mkdir(parents=True)
        self.outside.mkdir()
        self.old_cwd = os.getcwd()
        os.chdir(self.subdir)

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_execute_builtin_tool_uses_injected_backend_for_shell(self) -> None:
        backend = FakeToolBackend()

        result = self.run_async(
            execute_builtin_tool(
                "run_shell",
                {"command": "echo should-not-run", "timeout": 1234},
                execution_backend=backend,
            )
        )

        self.assertEqual(result, "from fake backend")
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(backend.calls[0][0], "echo should-not-run")
        self.assertEqual(backend.calls[0][1], 1234)
        self.assertEqual(backend.calls[0][2], self.subdir)

    def test_invalid_shell_timeout_returns_tool_error(self) -> None:
        backend = FakeToolBackend()

        result = self.run_async(
            execute_builtin_tool(
                "run_shell",
                {"command": "echo nope", "timeout": "bad"},
                execution_backend=backend,
            )
        )

        self.assertEqual(result, "Error: invalid timeout: bad")
        self.assertEqual(backend.calls, [])

    def test_local_manager_preserves_shell_behavior(self) -> None:
        manager = SandboxManager(
            SandboxConfig(profile="local", backend="local", workspace_host_path=self.workspace)
        )

        result = self.run_async(manager.run_shell("printf local", 30000, self.subdir))

        self.assertEqual(result, "local")

    def test_guest_cwd_mapping_for_sandbox_backend(self) -> None:
        fake = FakeBackend(CommandResult(stdout="mapped\n", backend_name="microsandbox"))
        manager = SandboxManager(
            SandboxConfig(
                profile="microsandbox-dev",
                backend="microsandbox",
                workspace_host_path=self.workspace,
            ),
            backend=fake,
        )

        result = self.run_async(manager.run_shell("pwd", 30000, self.subdir))

        self.assertEqual(result, "mapped\n")
        self.assertTrue(fake.started)
        self.assertEqual(fake.calls[0], ("pwd", 30000, "/workspace/pkg"))

    def test_guest_cwd_outside_workspace_fails_closed(self) -> None:
        fake = FakeBackend()
        manager = SandboxManager(
            SandboxConfig(
                profile="microsandbox-dev",
                backend="microsandbox",
                workspace_host_path=self.workspace,
            ),
            backend=fake,
        )

        result = self.run_async(manager.run_shell("pwd", 30000, self.outside))

        self.assertIn("cwd is outside sandbox workspace", result)
        self.assertEqual(fake.calls, [])

    def test_command_result_failure_and_timeout_format_match_existing_shell(self) -> None:
        failed = SandboxManager(
            SandboxConfig(
                profile="microsandbox-dev",
                backend="microsandbox",
                workspace_host_path=self.workspace,
            ),
            backend=FakeBackend(CommandResult(stdout="out", stderr="err", exit_code=7)),
        )
        timed_out = SandboxManager(
            SandboxConfig(
                profile="microsandbox-dev",
                backend="microsandbox",
                workspace_host_path=self.workspace,
            ),
            backend=FakeBackend(CommandResult(timed_out=True)),
        )

        failed_result = self.run_async(failed.run_shell("bad", 1234, self.subdir))
        timeout_result = self.run_async(timed_out.run_shell("sleep 10", 4321, self.subdir))

        self.assertEqual(failed_result, "Command failed (exit code 7)\nStdout: out\nStderr: err")
        self.assertEqual(timeout_result, "Command timed out after 4321ms")

    def test_explicit_microsandbox_unavailable_does_not_fallback_to_local(self) -> None:
        manager = SandboxManager(
            SandboxConfig(
                profile="microsandbox-dev",
                backend="microsandbox",
                workspace_host_path=self.workspace,
            ),
            session_id="test",
        )

        with patch("importlib.util.find_spec", return_value=None):
            result = self.run_async(manager.run_shell("printf local", 30000, self.subdir))

        self.assertIn("microsandbox Python SDK is not installed", result)
        self.assertNotEqual(result, "local")

    def test_stop_delegates_to_backend(self) -> None:
        fake = FakeBackend()
        manager = SandboxManager(
            SandboxConfig(
                profile="microsandbox-dev",
                backend="microsandbox",
                workspace_host_path=self.workspace,
            ),
            backend=fake,
        )

        self.run_async(manager.run_shell("echo ok", 30000, self.subdir))
        self.run_async(manager.stop())

        self.assertTrue(fake.stopped)

    def test_linux_default_config_uses_workspace_bwrap_profile(self) -> None:
        args = Namespace(
            sandbox=None,
            sandbox_network=None,
            sandbox_image=None,
            sandbox_memory=512,
            sandbox_cpus=1,
            sandbox_readonly_workspace=False,
            sandbox_no_network=False,
            sandbox_env=None,
            sandbox_extra_write=None,
            sandbox_allow_local_fallback=False,
        )

        with patch("nanocode.capabilities.sandbox.config.platform.system", return_value="Linux"):
            config = build_sandbox_config(args)

        self.assertEqual(config.profile, "workspace")
        self.assertEqual(config.backend, "bwrap")
        self.assertEqual(config.workspace_mode, "workspace-write")
        self.assertEqual(config.network_mode, "none")

    def test_microsandbox_alias_maps_to_safe_when_readonly(self) -> None:
        args = Namespace(
            sandbox="microsandbox",
            sandbox_network=None,
            sandbox_image=None,
            sandbox_memory=512,
            sandbox_cpus=1,
            sandbox_readonly_workspace=True,
            sandbox_no_network=False,
            sandbox_env=None,
            sandbox_extra_write=None,
            sandbox_allow_local_fallback=False,
        )

        config = build_sandbox_config(args)

        self.assertEqual(config.profile, "microsandbox-safe")
        self.assertEqual(config.backend, "microsandbox")
        self.assertEqual(config.workspace_mode, "read-only")
        self.assertEqual(config.network_mode, "none")

    def test_bwrap_command_uses_minimal_env_network_none_and_protected_paths(self) -> None:
        (self.workspace / ".env").write_text("TOKEN=secret")
        (self.workspace / ".git").mkdir()
        config = SandboxConfig(
            profile="workspace",
            backend="bwrap",
            workspace_host_path=self.workspace,
            forwarded_env=("CUSTOM_TOKEN",),
        )
        backend = BwrapBackend(config)

        with patch.dict(os.environ, {"PATH": "/usr/bin", "OPENAI_API_KEY": "secret", "CUSTOM_TOKEN": "ok"}, clear=True):
            argv = backend.build_argv("echo ok", str(self.subdir))

        joined = "\n".join(argv)
        self.assertIn("--unshare-net", argv)
        self.assertIn("--bind", argv)
        self.assertIn(str(self.workspace), argv)
        self.assertIn(str(self.workspace / ".env"), argv)
        self.assertIn(str(self.workspace / ".git"), argv)
        self.assertIn("CUSTOM_TOKEN", argv)
        self.assertIn("ok", argv)
        self.assertNotIn("OPENAI_API_KEY", joined)
        self.assertNotIn("secret", joined)

    def test_bwrap_unavailable_fails_closed_without_local_fallback(self) -> None:
        manager = SandboxManager(
            SandboxConfig(profile="workspace", backend="bwrap", workspace_host_path=self.workspace)
        )

        with patch("nanocode.capabilities.sandbox.bwrap_backend.shutil.which", return_value=None):
            result = self.run_async(manager.run_shell("printf unsafe", 30000, self.subdir))

        self.assertIn("bubblewrap is not available", result)
        self.assertNotEqual(result, "unsafe")

    def test_bwrap_unavailable_can_fallback_only_when_explicit(self) -> None:
        manager = SandboxManager(
            SandboxConfig(
                profile="workspace",
                backend="bwrap",
                workspace_host_path=self.workspace,
                allow_fallback_to_local=True,
            )
        )

        with patch("nanocode.capabilities.sandbox.bwrap_backend.shutil.which", return_value=None):
            result = self.run_async(manager.run_shell("printf fallback", 30000, self.subdir))

        self.assertEqual(result, "fallback")

    def test_describe_exposes_boundary(self) -> None:
        manager = SandboxManager(
            SandboxConfig(profile="workspace", backend="bwrap", workspace_host_path=self.workspace)
        )

        description = manager.describe()

        self.assertIn("Sandbox profile: workspace", description)
        self.assertIn("Backend: bwrap", description)
        self.assertIn("Network: none", description)
        self.assertIn("Secrets: host env not forwarded", description)


if __name__ == "__main__":
    unittest.main()
