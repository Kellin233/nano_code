from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from nanocode.domains.hooks import HookManager
from nanocode.domains.hooks.runner import run_command_hook
from nanocode.domains.hooks.types import HookCommand, HookInput
from nanocode.domains.permissions import check_permission
from nanocode.domains.permissions.rules import reset_permission_cache
from nanocode.domains.permissions.shell import check_shell_safety
from nanocode.domains.sandbox import SandboxConfig, SandboxManager, build_sandbox_config


class PermissionsHooksSandboxV1Tests(unittest.TestCase):
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

    def _script(self, body: str) -> str:
        path = self.project / f"hook_{len(list(self.project.glob('hook_*.py')))}.py"
        path.write_text(body, encoding="utf-8")
        return f"{sys.executable} {path}"

    def test_protected_path_and_shell_complexity_are_not_silently_allowed(self) -> None:
        (self.project / ".env").write_text("SECRET=1")

        protected = check_permission(
            "read_file",
            {"file_path": ".env"},
            mode="dontAsk",
            cwd=self.project,
        )
        command_substitution = check_shell_safety("echo $(cat token)")
        recursive_chmod = check_shell_safety("chmod -R 777 .")

        self.assertEqual(protected.action, "deny")
        self.assertIn("read protected path", protected.message)
        self.assertEqual(command_substitution.level, "confirm")
        self.assertEqual(recursive_chmod.level, "confirm")

    def test_hook_capture_requires_explicit_project_trust(self) -> None:
        home_settings = self.home / ".claude"
        project_settings = self.project / ".claude"
        home_settings.mkdir()
        project_settings.mkdir()
        (home_settings / "settings.json").write_text(json.dumps({
            "hooks": {"UserPromptSubmit": [{"command": "true"}]},
        }))
        (project_settings / "settings.json").write_text(json.dumps({
            "hooks": {"PreToolUse": [{"command": "true", "matcher": "run_shell"}]},
        }))

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NANO_CODE_TRUST_PROJECT_HOOKS", None)
            untrusted = HookManager.capture()
        with patch.dict(os.environ, {"NANO_CODE_TRUST_PROJECT_HOOKS": "1"}, clear=False):
            trusted = HookManager.capture()

        self.assertTrue(untrusted.has_hooks("UserPromptSubmit"))
        self.assertFalse(untrusted.has_hooks("PreToolUse"))
        self.assertTrue(trusted.has_hooks("PreToolUse"))

    def test_fail_closed_hook_denies_non_json_output(self) -> None:
        hook = HookCommand(
            event="PreToolUse",
            command=self._script("print('not json')\n"),
            fail_closed=True,
        )

        output = asyncio.run(run_command_hook(
            hook,
            HookInput(event="PreToolUse", session_id="s", cwd=str(self.project), tool_name="run_shell"),
        ))

        self.assertEqual(output.action, "deny")
        self.assertIn("non-JSON", output.error)

    def test_sandbox_default_profile_is_workspace_and_fallback_is_explicit(self) -> None:
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
        with patch("nanocode.domains.sandbox.config.platform.system", return_value="Linux"):
            config = build_sandbox_config(args)
        strict_manager = SandboxManager(
            SandboxConfig(profile="workspace", backend="bwrap", workspace_host_path=self.project),
            session_id="v1",
        )

        with patch("nanocode.domains.sandbox.bwrap_backend.shutil.which", return_value=None):
            result = asyncio.run(strict_manager.run_shell("printf auto", 30000, self.project))

        self.assertEqual(config.profile, "workspace")
        self.assertEqual(config.backend, "bwrap")
        self.assertEqual(config.network_mode, "none")
        self.assertIn("bubblewrap is not available", result)
        self.assertNotEqual(result, "auto")


if __name__ == "__main__":
    unittest.main()
