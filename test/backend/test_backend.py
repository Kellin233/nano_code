"""测试 Backend 模块 — 模型后端策略类。

验证：
1. Backend 接口定义
2. AnthropicBackend 创建和基本属性
3. OpenAIBackend 创建和基本属性
4. create_backend 工厂函数
"""

from __future__ import annotations

import unittest

from nanocode.backend.base import Backend, BackendResponse, TokenUsage
from nanocode.backend.anthropic import AnthropicBackend
from nanocode.backend.openai import OpenAIBackend
from nanocode.backend import create_backend


class TestBackendInterface(unittest.TestCase):
    """Backend 接口测试。"""

    def test_backend_is_abstract(self):
        """Backend 是抽象类，不能直接实例化。"""
        with self.assertRaises(TypeError):
            Backend()  # type: ignore

    def test_backend_response_defaults(self):
        """BackendResponse 默认值测试。"""
        resp = BackendResponse()
        self.assertEqual(resp.text, "")
        self.assertEqual(resp.tool_calls, [])
        self.assertEqual(resp.usage.input_tokens, 0)
        self.assertEqual(resp.usage.output_tokens, 0)

    def test_backend_response_with_data(self):
        """BackendResponse 包含数据时正确存储。"""
        usage = TokenUsage(input_tokens=100, output_tokens=200)
        resp = BackendResponse(text="hello", usage=usage)
        self.assertEqual(resp.text, "hello")
        self.assertEqual(resp.usage.input_tokens, 100)

    def test_token_usage_defaults(self):
        """TokenUsage 默认值测试。"""
        usage = TokenUsage()
        self.assertEqual(usage.input_tokens, 0)
        self.assertEqual(usage.output_tokens, 0)


class TestAnthropicBackend(unittest.TestCase):
    """AnthropicBackend 测试。"""

    def test_create_anthropic_backend(self):
        """创建 AnthropicBackend 实例。"""
        backend = AnthropicBackend(api_key="sk-ant-test", model="claude-opus-4-6")
        self.assertIsInstance(backend, Backend)
        self.assertEqual(backend.model, "claude-opus-4-6")

    def test_anthropic_supports_thinking(self):
        """AnthropicBackend 支持 thinking 检测。"""
        backend = AnthropicBackend(api_key="sk-test")
        self.assertTrue(backend.supports_thinking("claude-opus-4-6"))
        self.assertTrue(backend.supports_thinking("claude-sonnet-4-6"))
        self.assertFalse(backend.supports_thinking("gpt-4o"))

    def test_anthropic_supports_adaptive_thinking(self):
        """AnthropicBackend 支持 adaptive thinking 检测。"""
        backend = AnthropicBackend(api_key="sk-test")
        self.assertTrue(backend.supports_adaptive_thinking("claude-opus-4-6"))
        self.assertFalse(backend.supports_adaptive_thinking("claude-haiku-4-5-20251001"))

    def test_resolve_thinking_mode_disabled(self):
        """thinking_enabled=False 时返回 disabled。"""
        backend = AnthropicBackend(api_key="sk-test", model="claude-opus-4-6")
        self.assertEqual(backend.resolve_thinking_mode(False), "disabled")

    def test_resolve_thinking_mode_adaptive(self):
        """支持 adaptive thinking 的模型开启时返回 adaptive。"""
        backend = AnthropicBackend(api_key="sk-test", model="claude-opus-4-6")
        self.assertEqual(backend.resolve_thinking_mode(True), "adaptive")

    def test_resolve_thinking_mode_unsupported_model(self):
        """不支持的模型即使开启 thinking 也返回 disabled。"""
        backend = AnthropicBackend(api_key="sk-test", model="gpt-4o")
        self.assertEqual(backend.resolve_thinking_mode(True), "disabled")

    def test_block_to_dict_text(self):
        """block_to_dict 正确转换 text 块。"""
        class TextBlock:
            type = "text"
            text = "hello"

        result = AnthropicBackend.block_to_dict(TextBlock())
        self.assertEqual(result, {"type": "text", "text": "hello"})

    def test_block_to_dict_tool_use(self):
        """block_to_dict 正确转换 tool_use 块。"""
        class ToolUseBlock:
            type = "tool_use"
            id = "tool_1"
            name = "read_file"
            input = {"file_path": "f.py"}

        result = AnthropicBackend.block_to_dict(ToolUseBlock())
        self.assertEqual(result["type"], "tool_use")
        self.assertEqual(result["id"], "tool_1")
        self.assertEqual(result["name"], "read_file")


class TestOpenAIBackend(unittest.TestCase):
    """OpenAIBackend 测试。"""

    def test_create_openai_backend(self):
        """创建 OpenAIBackend 实例。"""
        backend = OpenAIBackend(
            api_key="sk-test",
            base_url="http://localhost/v1",
            model="gpt-4o",
        )
        self.assertIsInstance(backend, Backend)
        self.assertEqual(backend.model, "gpt-4o")


class TestCreateBackendFactory(unittest.TestCase):
    """create_backend 工厂函数测试。"""

    def test_create_anthropic_backend(self):
        """provider=anthropic 时创建 AnthropicBackend。"""
        backend = create_backend(
            provider="anthropic",
            api_key="sk-ant-test",
            model="claude-opus-4-6",
        )
        self.assertIsInstance(backend, AnthropicBackend)

    def test_create_openai_backend(self):
        """provider=openai 时创建 OpenAIBackend。"""
        backend = create_backend(
            provider="openai",
            api_key="sk-test",
            model="gpt-4o",
            api_base="http://localhost/v1",
        )
        self.assertIsInstance(backend, OpenAIBackend)


if __name__ == "__main__":
    unittest.main()
