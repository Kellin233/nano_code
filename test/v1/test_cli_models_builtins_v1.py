from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nanocode.cli.args import resolve_permission_mode, parse_args
from nanocode.models import to_openai_tools, with_retry
from nanocode.capabilities.tools.builtin import grep_search, list_files


class RetryableError(Exception):
    status_code = 429


class MissingModelError(Exception):
    status_code = 503


class CliModelsBuiltinsV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        self.old_cwd = os.getcwd()
        os.chdir(self.project)

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def test_cli_permission_priority_and_prompt_joining(self) -> None:
        with patch.object(sys, "argv", ["nanocode", "--yolo", "--dont-ask", "hello", "world"]):
            args = parse_args()

        self.assertEqual(resolve_permission_mode(args), "bypassPermissions")
        self.assertEqual(args.prompt, ["hello", "world"])

    def test_model_retry_retries_only_retryable_errors(self) -> None:
        calls = {"count": 0}

        async def flaky():
            calls["count"] += 1
            if calls["count"] == 1:
                raise RetryableError("rate limited")
            return "ok"

        async def no_sleep(_delay):
            return None

        with patch("nanocode.models.asyncio.sleep", new=no_sleep):
            result = asyncio.run(with_retry(flaky, max_retries=2))

        self.assertEqual(result, "ok")
        self.assertEqual(calls["count"], 2)

    def test_model_retry_does_not_retry_missing_model_errors(self) -> None:
        calls = {"count": 0}

        async def missing_model():
            calls["count"] += 1
            raise MissingModelError("model_not_found: No available channel for model")

        with self.assertRaises(MissingModelError):
            asyncio.run(with_retry(missing_model, max_retries=2))

        self.assertEqual(calls["count"], 1)

    def test_openai_tool_conversion_preserves_schema(self) -> None:
        converted = to_openai_tools([
            {
                "name": "read_file",
                "description": "Read",
                "input_schema": {"type": "object", "required": ["file_path"]},
            }
        ])

        self.assertEqual(converted[0]["type"], "function")
        self.assertEqual(converted[0]["function"]["name"], "read_file")
        self.assertEqual(converted[0]["function"]["parameters"]["required"], ["file_path"])

    def test_builtin_list_files_skips_hidden_runtime_dirs_and_grep_reports_bad_regex(self) -> None:
        (self.project / "src").mkdir()
        (self.project / "src" / "app.py").write_text("print('hello')\n")
        (self.project / ".git").mkdir()
        (self.project / ".git" / "config").write_text("secret")
        (self.project / "__pycache__").mkdir()
        (self.project / "__pycache__" / "x.pyc").write_text("cache")

        listed = list_files({"path": str(self.project), "pattern": "**/*"})
        bad_regex = grep_search({"path": str(self.project), "pattern": "["})

        self.assertIn("src/app.py", listed)
        self.assertNotIn(".git", listed)
        self.assertNotIn("__pycache__", listed)
        self.assertIn("invalid regex", bad_regex)


if __name__ == "__main__":
    unittest.main()
