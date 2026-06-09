"""测试 CLI 参数解析和配置解析。

验证：
1. argparse 参数解析
2. 权限模式解析
3. RuntimeConfig 组装
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

from nanocode.cli.args import parse_args, resolve_permission_mode, resolve_runtime_config


class TestCliArgs(unittest.TestCase):
    """CLI 参数解析测试。"""

    def test_parse_args_yolo_flag(self):
        """--yolo 标志被正确解析。"""
        with patch.object(sys, "argv", ["nanocode", "--yolo", "hello"]):
            args = parse_args()
        self.assertTrue(args.yolo)
        self.assertEqual(args.prompt, ["hello"])

    def test_parse_args_accept_edits_flag(self):
        """--accept-edits 标志被正确解析。"""
        with patch.object(sys, "argv", ["nanocode", "--accept-edits"]):
            args = parse_args()
        self.assertTrue(args.accept_edits)

    def test_parse_args_dont_ask_flag(self):
        """--dont-ask 标志被正确解析。"""
        with patch.object(sys, "argv", ["nanocode", "--dont-ask"]):
            args = parse_args()
        self.assertTrue(args.dont_ask)

    def test_parse_args_model_flag(self):
        """--model 参数被正确解析。"""
        with patch.object(sys, "argv", ["nanocode", "--model", "gpt-4o"]):
            args = parse_args()
        self.assertEqual(args.model, "gpt-4o")

    def test_parse_args_max_cost_and_turns(self):
        """--max-cost 和 --max-turns 被正确解析。"""
        with patch.object(sys, "argv", ["nanocode", "--max-cost", "0.50", "--max-turns", "10"]):
            args = parse_args()
        self.assertEqual(args.max_cost, 0.50)
        self.assertEqual(args.max_turns, 10)

    def test_parse_args_thinking_flag(self):
        """--thinking 标志被正确解析。"""
        with patch.object(sys, "argv", ["nanocode", "--thinking"]):
            args = parse_args()
        self.assertTrue(args.thinking)

    def test_parse_args_server_stdio(self):
        """--server stdio 被正确解析。"""
        with patch.object(sys, "argv", ["nanocode", "--server", "stdio"]):
            args = parse_args()
        self.assertEqual(args.server, "stdio")

    def test_parse_args_prompt_multiple_words(self):
        """多个 prompt 参数被正确收集。"""
        with patch.object(sys, "argv", ["nanocode", "fix", "the", "bug"]):
            args = parse_args()
        self.assertEqual(args.prompt, ["fix", "the", "bug"])

    def test_parse_args_sandbox_profile(self):
        """--sandbox 参数被正确解析。"""
        with patch.object(sys, "argv", ["nanocode", "--sandbox", "workspace"]):
            args = parse_args()
        self.assertEqual(args.sandbox, "workspace")


class TestPermissionMode(unittest.TestCase):
    """权限模式解析测试。"""

    def test_yolo_gives_bypass_permissions(self):
        """--yolo 返回 bypassPermissions 模式。"""
        with patch.object(sys, "argv", ["nanocode", "--yolo"]):
            args = parse_args()
        self.assertEqual(resolve_permission_mode(args), "bypassPermissions")

    def test_accept_edits_gives_accept_edits(self):
        """--accept-edits 返回 acceptEdits 模式。"""
        with patch.object(sys, "argv", ["nanocode", "--accept-edits"]):
            args = parse_args()
        self.assertEqual(resolve_permission_mode(args), "acceptEdits")

    def test_dont_ask_gives_dont_ask(self):
        """--dont-ask 返回 dontAsk 模式。"""
        with patch.object(sys, "argv", ["nanocode", "--dont-ask"]):
            args = parse_args()
        self.assertEqual(resolve_permission_mode(args), "dontAsk")

    def test_default_gives_default(self):
        """无权限标志返回 default 模式。"""
        with patch.object(sys, "argv", ["nanocode"]):
            args = parse_args()
        self.assertEqual(resolve_permission_mode(args), "default")

    def test_yolo_overrides_accept_edits(self):
        """--yolo 覆盖 --accept-edits。"""
        with patch.object(sys, "argv", ["nanocode", "--yolo", "--accept-edits"]):
            args = parse_args()
        self.assertEqual(resolve_permission_mode(args), "bypassPermissions")


class TestRuntimeConfigResolution(unittest.TestCase):
    """RuntimeConfig 组装测试。"""

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=True)
    def test_resolve_with_anthropic_key(self):
        """ANTHROPIC_API_KEY 被检测为 anthropic provider。"""
        with patch.object(sys, "argv", ["nanocode"]):
            args = parse_args()
        config = resolve_runtime_config(args)
        self.assertEqual(config.provider, "anthropic")
        self.assertEqual(config.api_key, "sk-ant-test")

    @patch.dict(os.environ, {
        "OPENAI_API_KEY": "sk-test",
        "OPENAI_BASE_URL": "http://localhost/v1",
    }, clear=True)
    def test_resolve_with_openai_key_and_base_url(self):
        """OPENAI_API_KEY + OPENAI_BASE_URL 被检测为 openai provider。"""
        with patch.object(sys, "argv", ["nanocode"]):
            args = parse_args()
        config = resolve_runtime_config(args)
        self.assertEqual(config.provider, "openai")
        self.assertEqual(config.api_key, "sk-test")
        self.assertEqual(config.api_base, "http://localhost/v1")

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=True)
    def test_resolve_with_anthropic_base_url(self):
        """ANTHROPIC_BASE_URL 被正确传递。"""
        with patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "http://proxy/v1"}):
            with patch.object(sys, "argv", ["nanocode"]):
                args = parse_args()
            config = resolve_runtime_config(args)
            self.assertEqual(config.anthropic_base_url, "http://proxy/v1")

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=True)
    def test_resolve_honors_model_from_args(self):
        """--model 参数覆盖环境变量。"""
        with patch.object(sys, "argv", ["nanocode", "--model", "gpt-4o"]):
            args = parse_args()
        config = resolve_runtime_config(args)
        self.assertEqual(config.model, "gpt-4o")


if __name__ == "__main__":
    unittest.main()
