"""测试 Compressor 上下文压缩策略。

验证：
1. Budget 裁剪超长工具结果
2. Snip 替换陈旧结果
3. Microcompact 清除旧结果
4. 双后端消息格式正确处理
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from nanocode.runtime.agent import Agent, RuntimeConfig
from nanocode.runtime.compressor import Compressor, SNIP_PLACEHOLDER, SNIPPABLE_TOOLS


class TestCompressor(unittest.TestCase):
    """Compressor 压缩策略测试。"""

    def setUp(self):
        config = RuntimeConfig(model="claude-opus-4-6", api_key="test-key")
        self.agent = Agent(config)
        self.agent.last_input_token_count = 180000  # 高利用率触发压缩
        self.compressor = Compressor(self.agent)

    def test_snippable_tools_contains_expected(self):
        """SNIPPABLE_TOOLS 包含预期的可裁剪工具。"""
        self.assertIn("read_file", SNIPPABLE_TOOLS)
        self.assertIn("grep_search", SNIPPABLE_TOOLS)
        self.assertIn("list_files", SNIPPABLE_TOOLS)
        self.assertIn("run_shell", SNIPPABLE_TOOLS)

    def test_snip_placeholder_is_string(self):
        """SNIP_PLACEHOLDER 是非空字符串。"""
        self.assertIsInstance(SNIP_PLACEHOLDER, str)
        self.assertTrue(len(SNIP_PLACEHOLDER) > 0)

    def test_budget_results_truncates_long_anthropic_tool_results(self):
        """Budget 阶段裁剪 Anthropic 超长工具结果。"""
        long_content = "x" * 40000
        self.agent._anthropic_messages = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "1", "name": "read_file", "input": {"file_path": "f.py"}}
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "1", "content": long_content}
            ]},
        ]
        self.agent.last_input_token_count = int(self.agent.effective_window * 0.6)
        self.compressor._budget_results()
        content = self.agent._anthropic_messages[1]["content"][0]["content"]
        self.assertLess(len(content), len(long_content))
        self.assertIn("budgeted", content)

    def test_budget_results_truncates_long_openai_tool_results(self):
        """Budget 阶段裁剪 OpenAI 超长工具结果。"""
        config = RuntimeConfig(provider="openai", api_key="k", api_base="http://x")
        agent = Agent(config)
        agent.last_input_token_count = int(agent.effective_window * 0.6)
        compressor = Compressor(agent)

        long_content = "x" * 40000
        agent._openai_messages = [
            {"role": "tool", "tool_call_id": "1", "content": long_content},
        ]
        compressor._budget_results()
        content = agent._openai_messages[0]["content"]
        self.assertLess(len(content), len(long_content))

    def test_budget_skips_when_utilization_low(self):
        """利用率低时跳过 budget 裁剪。"""
        self.agent.last_input_token_count = 100
        self.agent._anthropic_messages = [
            {"role": "user", "content": [{"type": "tool_result", "content": "x" * 40000}]}
        ]
        self.compressor._budget_results()
        # 不应该裁剪
        content = self.agent._anthropic_messages[0]["content"][0]["content"]
        self.assertEqual(len(content), 40000)

    def test_snip_anthropic_replaces_old_read_file_results(self):
        """Snip 阶段替换 Anthropic 旧的 read_file 结果。"""
        self.agent._anthropic_messages = []
        # 构建多条 read_file 工具调用和结果
        for i in range(10):
            uid = f"tool_{i}"
            self.agent._anthropic_messages.append({
                "role": "assistant", "content": [
                    {"type": "tool_use", "id": uid, "name": "read_file", "input": {"file_path": f"f{i}.py"}}
                ]
            })
            self.agent._anthropic_messages.append({
                "role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": uid, "content": f"content {i}"}
                ]
            })
        self.agent.last_input_token_count = int(self.agent.effective_window * 0.7)
        self.compressor._snip_stale_results()
        # 至少有一些早期结果被替换为 SNIP_PLACEHOLDER
        snipped = any(
            block.get("content") == SNIP_PLACEHOLDER
            for msg in self.agent._anthropic_messages
            if msg.get("role") == "user" and isinstance(msg.get("content"), list)
            for block in msg["content"]
        )
        self.assertTrue(snipped)

    def test_snip_skips_when_utilization_low(self):
        """利用率低时跳过 snip。"""
        self.agent.last_input_token_count = 100
        self.agent._anthropic_messages = [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "read_file", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "hello"}]},
        ]
        self.compressor._snip_stale_results()
        # 不应该替换
        content = self.agent._anthropic_messages[1]["content"][0]["content"]
        self.assertEqual(content, "hello")

    def test_microcompact_clears_old_results_after_idle(self):
        """Microcompact 在空闲后清除旧结果。"""
        import time
        self.agent.last_api_call_time = time.time() - 600  # 10 分钟前
        self.agent._anthropic_messages = [
            {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "read_file", "input": {}}]}
            for i in range(10)
        ]
        for i in range(10):
            self.agent._anthropic_messages.append({
                "role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": f"result {i}"}]
            })
        self.compressor._microcompact()
        cleared = any(
            block.get("content") == "[Old result cleared]"
            for msg in self.agent._anthropic_messages
            if msg.get("role") == "user" and isinstance(msg.get("content"), list)
            for block in msg["content"]
        )
        self.assertTrue(cleared)

    def test_run_pipeline_executes_all_three_layers(self):
        """run_pipeline 按顺序执行三层压缩。"""
        self.agent.last_api_call_time = 0  # 确保 microcompact 也会运行
        self.agent.last_input_token_count = int(self.agent.effective_window * 0.8)
        # 构建消息
        for i in range(8):
            self.agent._anthropic_messages.append({
                "role": "assistant", "content": [
                    {"type": "tool_use", "id": f"t{i}", "name": "read_file", "input": {"file_path": f"f{i}.py"}}
                ]
            })
            self.agent._anthropic_messages.append({
                "role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": f"t{i}", "content": "x" * 40000}
                ]
            })
        # 不应该抛出异常
        self.compressor.run_pipeline()


if __name__ == "__main__":
    unittest.main()
