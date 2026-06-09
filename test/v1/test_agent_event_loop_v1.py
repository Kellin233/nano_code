"""Agent Event Loop V1 测试 — 适配重构后的 Backend + AgentLoop 架构。

原测试通过 monkey-patch Agent._call_anthropic_stream 来模拟模型调用。
重构后模型调用在 Backend 策略类中，因此改为通过 FakeBackend 注入。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from nanocode.runtime.agent import Agent, RuntimeConfig
from nanocode.runtime.loop import AgentLoop
from nanocode.backend.base import Backend, BackendResponse, TokenUsage
from nanocode.capabilities.tools.types import ToolCall
from nanocode.capabilities.hooks.types import HookOutput


class FakeLoopBackend(Backend):
    """模拟 Backend — 可编程控制每次调用的返回内容。"""

    def __init__(self, responses: list[BackendResponse]):
        self.responses = responses
        self.call_count = 0

    async def call(self, *, messages, system, tools, on_text_delta=None, thinking_mode="disabled"):
        if self.call_count >= len(self.responses):
            return BackendResponse(text="", usage=TokenUsage())
        resp = self.responses[self.call_count]
        self.call_count += 1
        if on_text_delta and resp.text:
            await on_text_delta(resp.text)
        return resp

    def supports_thinking(self, model: str) -> bool:
        return True

    def supports_adaptive_thinking(self, model: str) -> bool:
        return True

    def resolve_thinking_mode(self, thinking_enabled: bool) -> str:
        return "disabled"


class OneShotHookManager:
    def __init__(self, output: HookOutput):
        self.output = output
        self.calls = 0

    async def run(self, event, hook_input):
        if event != "Stop" or self.calls:
            return []
        self.calls += 1
        return [self.output]


class AgentEventLoopV1Tests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.home_patch.stop()
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def test_anthropic_loop_executes_tool_and_preserves_tool_result_pairing(self) -> None:
        """Anthropic 后端：执行工具并保持 tool_use ↔ tool_result 配对。"""
        target = self.project / "data.txt"
        target.write_text("alpha\nbeta")

        config = RuntimeConfig(
            api_key="test-key", is_sub_agent=True,
            custom_system_prompt="sub", provider="anthropic",
        )
        agent = Agent(config)

        backend = FakeLoopBackend([
            BackendResponse(
                text="checking file",
                tool_calls=[ToolCall(id="tool-1", name="read_file",
                                     input={"file_path": str(target)}, provider="anthropic")],
                usage=TokenUsage(input_tokens=7, output_tokens=3),
            ),
            BackendResponse(
                text="done",
                usage=TokenUsage(input_tokens=5, output_tokens=2),
            ),
        ])
        loop = AgentLoop(agent, backend)

        async def collect():
            result_text = []
            try:
                async for event in loop.run("read it"):
                    if event.type == "assistant.delta":
                        result_text.append(event.payload.get("text", ""))
                    if event.type == "turn.finished":
                        break
            except Exception:
                pass
            return "".join(result_text)

        text = asyncio.run(collect())
        self.assertEqual(backend.call_count, 2)
        self.assertEqual(text, "checking filedone")
        self.assertEqual(agent.total_input_tokens, 12)
        self.assertEqual(agent.total_output_tokens, 5)
        # 验证 tool_result 配对
        tool_result_msg = agent._anthropic_messages[-2]
        self.assertEqual(tool_result_msg["role"], "user")
        self.assertEqual(tool_result_msg["content"][0]["type"], "tool_result")
        self.assertIn("alpha", tool_result_msg["content"][0]["content"])

    def test_openai_loop_returns_validation_error_for_malformed_tool_arguments(self) -> None:
        """OpenAI 后端：格式错误的工具参数返回校验错误。"""
        config = RuntimeConfig(
            api_key="test-key", is_sub_agent=True,
            custom_system_prompt="sub", provider="openai",
            api_base="http://example.invalid/v1",
        )
        agent = Agent(config)

        backend = FakeLoopBackend([
            BackendResponse(
                text=None,
                tool_calls=[ToolCall(id="call-1", name="read_file",
                                     input={}, provider="openai")],
                usage=TokenUsage(input_tokens=4, output_tokens=1),
            ),
            BackendResponse(
                text="recovered",
                usage=TokenUsage(input_tokens=3, output_tokens=2),
            ),
        ])
        loop = AgentLoop(agent, backend)

        async def collect():
            result_text = []
            async for event in loop.run("bad tool args"):
                if event.type == "assistant.delta":
                    result_text.append(event.payload.get("text", ""))
            return "".join(result_text)

        text = asyncio.run(collect())
        self.assertEqual(backend.call_count, 2)
        self.assertEqual(text, "recovered")
        tool_messages = [m for m in agent._openai_messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 1)

    def test_stop_hook_can_append_context_and_force_one_more_model_turn(self) -> None:
        """Stop hook 通过追加 context 强制再执行一轮模型调用。"""
        config = RuntimeConfig(
            api_key="test-key", is_sub_agent=True,
            custom_system_prompt="sub", provider="anthropic",
        )
        agent = Agent(config)
        agent._hook_manager = OneShotHookManager(
            HookOutput(action="append_context", content="need final answer")
        )

        backend = FakeLoopBackend([
            BackendResponse(text="draft", usage=TokenUsage(input_tokens=5, output_tokens=3)),
            BackendResponse(text="final", usage=TokenUsage(input_tokens=4, output_tokens=2)),
        ])
        loop = AgentLoop(agent, backend)

        async def collect():
            result_text = []
            async for event in loop.run("answer"):
                if event.type == "assistant.delta":
                    result_text.append(event.payload.get("text", ""))
            return "".join(result_text)

        text = asyncio.run(collect())
        self.assertEqual(backend.call_count, 2)
        self.assertEqual(text, "draftfinal")
        self.assertIn("need final answer", agent._anthropic_messages[-2]["content"])


if __name__ == "__main__":
    unittest.main()
