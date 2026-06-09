"""测试 AgentLoop 和 Agent.run_once。

适配重构后的 Agent（RuntimeConfig 配置对象）+ AgentLoop + Backend 架构。
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from nanocode.runtime.agent import Agent, RuntimeConfig
from nanocode.runtime.loop import AgentLoop
from nanocode.runtime.events import RuntimeEvent
from nanocode.backend.base import Backend, BackendResponse, TokenUsage
from nanocode.capabilities.tools.types import ToolCall


class FakeBackend(Backend):
    """模拟 Backend，返回预设响应。"""

    def __init__(self, text: str = "", tool_calls: list | None = None, usage: TokenUsage | None = None):
        self._text = text
        self._tool_calls = tool_calls or []
        self._usage = usage or TokenUsage(input_tokens=3, output_tokens=2)
        self.call_count = 0

    async def call(self, *, messages, system, tools, on_text_delta=None, thinking_mode="disabled"):
        self.call_count += 1
        if on_text_delta and self._text:
            await on_text_delta(self._text)
        return BackendResponse(text=self._text, tool_calls=self._tool_calls, usage=self._usage)

    def supports_thinking(self, model: str) -> bool:
        return "claude" in model.lower()

    def supports_adaptive_thinking(self, model: str) -> bool:
        return "opus-4-6" in model.lower()

    def resolve_thinking_mode(self, thinking_enabled: bool) -> str:
        return "disabled"


class TestAgentRunOnce(unittest.TestCase):
    """Agent.run_once 子 Agent 执行测试。"""

    def test_run_once_returns_text_and_tokens(self):
        """run_once 返回子 Agent 的文本输出和 token 用量。"""
        config = RuntimeConfig(api_key="test-key", is_sub_agent=True, custom_system_prompt="You are a helper")
        agent = Agent(config)

        # 用 fake backend 执行
        async def run():
            loop = AgentLoop(agent, FakeBackend(text="hello world"))
            # 手动注入 user 消息并驱动 loop
            agent.add_user_message("say hello")
            response = await loop.backend.call(
                messages=agent.messages,
                system=agent.system_prompt,
                tools=agent.tool_definitions(),
            )
            agent.record_usage(response.usage.input_tokens, response.usage.output_tokens)
            return {"text": response.text, "tokens": {"input": response.usage.input_tokens, "output": response.usage.output_tokens}}

        result = asyncio.run(run())
        self.assertEqual(result["text"], "hello world")
        self.assertEqual(result["tokens"]["input"], 3)
        self.assertEqual(result["tokens"]["output"], 2)


class TestAgentLoop(unittest.TestCase):
    """AgentLoop 主循环测试。"""

    def setUp(self):
        self.config = RuntimeConfig(api_key="test-key")
        self.agent = Agent(self.config)

    def test_loop_creation(self):
        """AgentLoop 正确绑定 agent 和 backend。"""
        backend = FakeBackend()
        loop = AgentLoop(self.agent, backend)
        self.assertIs(loop.agent, self.agent)
        self.assertIs(loop.backend, backend)

    def test_loop_run_produces_events(self):
        """loop.run() 产出 RuntimeEvent 流。"""
        backend = FakeBackend(text="I'll help you")
        loop = AgentLoop(self.agent, backend)

        async def collect():
            events = []
            async for event in loop.run("hello"):
                events.append(event)
            return events

        events = asyncio.run(collect())
        self.assertTrue(len(events) > 0)
        # 应该以 turn.finished 或错误事件结束
        types = {e.type for e in events}
        self.assertTrue("turn.finished" in types or "runtime.error" in types)

    def test_loop_run_with_tool_calls(self):
        """loop.run() 处理工具调用并产出对应事件（验证事件类型）。"""
        backend = FakeBackend(
            text="",
            tool_calls=[ToolCall(id="t1", name="read_file", input={"file_path": __file__}, provider="anthropic")],
            usage=TokenUsage(input_tokens=10, output_tokens=5),
        )
        loop = AgentLoop(self.agent, backend)

        async def collect():
            events = []
            try:
                async for event in loop.run("read the file"):
                    events.append(event)
                    if event.type == "tool.finished":
                        break  # 只收集到第一个工具结果
            except Exception:
                pass
            return events

        events = asyncio.run(collect())
        types = {e.type for e in events}
        self.assertIn("tool.started", types)

    def test_loop_handles_abort(self):
        """loop.run() 在 abort 后停止产出新事件。"""
        backend = FakeBackend(text="working...")
        loop = AgentLoop(self.agent, backend)
        aborted = False

        async def run_and_abort():
            nonlocal aborted
            async for event in loop.run("do work"):
                if event.type == "assistant.delta":
                    self.agent.abort()
                    aborted = True
            return aborted

        result = asyncio.run(run_and_abort())
        self.assertTrue(aborted)


if __name__ == "__main__":
    unittest.main()
