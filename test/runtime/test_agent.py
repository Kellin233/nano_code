"""测试 Agent 状态容器 — 重构后的纯数据面。

验证：
1. Agent 创建和状态初始化
2. 消息历史操作（add_user_message, add_assistant_message, add_tool_results）
3. 双后端消息历史独立存储
4. Token 统计和预算检查
5. 会话恢复和清除
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from nanocode.runtime.agent import Agent, RuntimeConfig


class TestAgentState(unittest.TestCase):
    """Agent 状态容器基础测试。"""

    def setUp(self):
        self.config = RuntimeConfig(
            model="claude-opus-4-6",
            provider="anthropic",
            api_key="test-key",
            permission_mode="default",
        )
        self.agent = Agent(self.config)

    def test_agent_initialization_sets_defaults(self):
        """Agent 创建后所有关键字段应已初始化。"""
        self.assertIsNotNone(self.agent.session_id)
        self.assertEqual(self.agent.model, "claude-opus-4-6")
        self.assertEqual(self.agent.permission_mode, "default")
        self.assertEqual(self.agent.total_input_tokens, 0)
        self.assertEqual(self.agent.total_output_tokens, 0)
        self.assertEqual(self.agent.current_turns, 0)
        self.assertFalse(self.agent._aborted)

    def test_agent_is_not_sub_agent_by_default(self):
        """默认情况下 Agent 不是子 Agent。"""
        self.assertFalse(self.agent.is_sub_agent)

    def test_messages_returns_anthropic_messages_when_not_openai(self):
        """非 OpenAI 情况下返回 Anthropic 消息历史。"""
        self.assertEqual(self.agent.messages, [])

    def test_messages_returns_openai_messages_when_openai(self):
        """OpenAI 情况下返回 OpenAI 消息历史。"""
        config = RuntimeConfig(provider="openai", api_key="k", api_base="http://x")
        agent = Agent(config)
        self.assertEqual(agent.messages, agent._openai_messages)

    def test_add_user_message_anthropic(self):
        """Anthropic 模式下添加用户消息。"""
        self.agent.add_user_message("hello")
        self.assertEqual(len(self.agent._anthropic_messages), 1)
        self.assertEqual(self.agent._anthropic_messages[0], {"role": "user", "content": "hello"})
        self.assertEqual(len(self.agent._openai_messages), 0)

    def test_add_user_message_openai(self):
        """OpenAI 模式下添加用户消息。"""
        config = RuntimeConfig(provider="openai", api_key="k", api_base="http://x")
        agent = Agent(config)
        agent.add_user_message("hello")
        self.assertEqual(len(agent._openai_messages), 1)
        self.assertEqual(agent._openai_messages[0], {"role": "user", "content": "hello"})

    def test_add_assistant_message_anthropic(self):
        """Anthropic 模式下添加 assistant 消息。"""
        content = [{"type": "text", "text": "I'll help"}]
        self.agent.add_assistant_message(content)
        self.assertEqual(len(self.agent._anthropic_messages), 1)
        self.assertEqual(self.agent._anthropic_messages[0]["role"], "assistant")

    def test_append_user_context_appends_to_last_user_message(self):
        """append_user_context 将内容追加到最新用户消息后。"""
        self.agent.add_user_message("question")
        self.agent.append_user_context("context info")
        msg = self.agent._anthropic_messages[0]
        self.assertIn("question", msg["content"])
        self.assertIn("context info", msg["content"])

    def test_append_user_context_creates_new_message_if_no_user_message(self):
        """如果没有用户消息，append_user_context 创建新消息。"""
        self.agent.append_user_context("context info")
        self.assertEqual(len(self.agent._anthropic_messages), 1)

    def test_record_usage_updates_token_counts(self):
        """record_usage 正确更新 token 计数。"""
        self.agent.record_usage(100, 200)
        self.assertEqual(self.agent.total_input_tokens, 100)
        self.assertEqual(self.agent.total_output_tokens, 200)
        self.assertEqual(self.agent.last_input_token_count, 100)

    def test_budget_exceeded_within_limits(self):
        """预算未超时返回 False。"""
        result = self.agent.budget_exceeded()
        self.assertFalse(result["exceeded"])

    def test_budget_exceeded_cost_limit(self):
        """Token 用量超过成本限制时返回 True。"""
        config = RuntimeConfig(max_cost_usd=0.01, api_key="k")
        agent = Agent(config)
        agent.total_input_tokens = 10000
        agent.total_output_tokens = 10000
        result = agent.budget_exceeded()
        self.assertTrue(result["exceeded"])

    def test_budget_exceeded_turn_limit(self):
        """轮次超过限制时返回 True。"""
        config = RuntimeConfig(max_turns=5, api_key="k")
        agent = Agent(config)
        agent.current_turns = 5
        result = agent.budget_exceeded()
        self.assertTrue(result["exceeded"])

    def test_abort_sets_flag(self):
        """abort() 设置 _aborted 标志。"""
        self.agent.abort()
        self.assertTrue(self.agent._aborted)

    def test_clear_history_resets_state(self):
        """clear_history 重置消息历史和 token 计数。"""
        self.agent.add_user_message("hello")
        self.agent.record_usage(100, 200)
        self.agent.clear_history()
        self.assertEqual(self.agent.total_input_tokens, 0)
        self.assertEqual(self.agent.total_output_tokens, 0)

    def test_tool_definitions_returns_active_tools(self):
        """tool_definitions 返回当前活动的工具列表。"""
        tools = self.agent.tool_definitions()
        self.assertIsInstance(tools, list)
        self.assertTrue(len(tools) > 0)
        self.assertIn("name", tools[0])

    def test_sub_agent_config(self):
        """子 Agent 配置正确设置 is_sub_agent 标志。"""
        config = RuntimeConfig(is_sub_agent=True, custom_system_prompt="test", api_key="k")
        agent = Agent(config)
        self.assertTrue(agent.is_sub_agent)
        self.assertEqual(agent._base_system_prompt, "test")

    def test_startup_context_empty_for_sub_agent(self):
        """子 Agent 的 startup context 应为空。"""
        config = RuntimeConfig(is_sub_agent=True, custom_system_prompt="test", api_key="k")
        agent = Agent(config)
        self.assertEqual(agent._startup_context, "")

    def test_set_confirm_fn(self):
        """set_confirm_fn 正确设置确认回调。"""
        called = False

        async def confirm(msg):
            nonlocal called
            called = True
            return True

        self.agent.set_confirm_fn(confirm)
        self.assertIsNotNone(self.agent._confirm_fn)


class TestRuntimeConfig(unittest.TestCase):
    """RuntimeConfig 配置测试。"""

    def test_default_values(self):
        config = RuntimeConfig()
        self.assertEqual(config.model, "claude-opus-4-6")
        self.assertEqual(config.provider, "anthropic")
        self.assertEqual(config.permission_mode, "default")
        self.assertFalse(config.thinking)
        self.assertFalse(config.is_sub_agent)

    def test_use_openai_property(self):
        config = RuntimeConfig(provider="openai")
        self.assertTrue(config.use_openai)

        config2 = RuntimeConfig(provider="anthropic")
        self.assertFalse(config2.use_openai)

    def test_workspace_defaults_to_cwd(self):
        config = RuntimeConfig()
        self.assertEqual(config.workspace, Path.cwd())


if __name__ == "__main__":
    unittest.main()
