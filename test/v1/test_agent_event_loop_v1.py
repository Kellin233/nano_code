from __future__ import annotations

import asyncio
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from nanocode.runtime.agent import Agent
from nanocode.domains.hooks.types import HookOutput


def _message(content, input_tokens: int = 1, output_tokens: int = 1):
    return types.SimpleNamespace(
        usage=types.SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        content=content,
    )


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
        target = self.project / "data.txt"
        target.write_text("alpha\nbeta")
        agent = Agent(api_key="test-key", is_sub_agent=True)
        calls = {"count": 0}

        async def fake_stream(**kwargs):
            calls["count"] += 1
            on_text_delta = kwargs.get("on_text_delta")
            if calls["count"] == 1:
                if on_text_delta:
                    on_text_delta("checking file")
                return _message([
                    types.SimpleNamespace(type="text", text="checking file"),
                    types.SimpleNamespace(
                        type="tool_use",
                        id="tool-1",
                        name="read_file",
                        input={"file_path": str(target)},
                    ),
                ], input_tokens=7, output_tokens=3)
            if on_text_delta:
                on_text_delta("done")
            return _message([types.SimpleNamespace(type="text", text="done")], input_tokens=5, output_tokens=2)

        agent._call_anthropic_stream = fake_stream

        result = asyncio.run(agent.run_once("read it"))

        self.assertEqual(calls["count"], 2)
        self.assertEqual(result["text"], "checking filedone")
        self.assertEqual(result["tokens"], {"input": 12, "output": 5})
        tool_result_msg = agent._anthropic_messages[-2]
        self.assertEqual(tool_result_msg["role"], "user")
        self.assertEqual(tool_result_msg["content"][0]["type"], "tool_result")
        self.assertIn("1 | alpha", tool_result_msg["content"][0]["content"])

    def test_openai_loop_returns_validation_error_for_malformed_tool_arguments(self) -> None:
        agent = Agent(api_base="http://example.invalid/v1", api_key="test-key", is_sub_agent=True)
        calls = {"count": 0}

        async def fake_stream(**kwargs):
            calls["count"] += 1
            on_text_delta = kwargs.get("on_text_delta")
            if calls["count"] == 1:
                return {
                    "choices": [{
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{
                                "id": "call-1",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": "{"},
                            }],
                        },
                    }],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 1},
                }
            if on_text_delta:
                on_text_delta("recovered")
            return {
                "choices": [{"message": {"role": "assistant", "content": "recovered", "tool_calls": None}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            }

        agent._call_openai_stream = fake_stream

        result = asyncio.run(agent.run_once("bad tool args"))

        self.assertEqual(calls["count"], 2)
        self.assertEqual(result["text"], "recovered")
        tool_messages = [m for m in agent._openai_messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 1)
        self.assertIn("missing required field: file_path", tool_messages[0]["content"])

    def test_stop_hook_can_append_context_and_force_one_more_model_turn(self) -> None:
        agent = Agent(api_key="test-key", is_sub_agent=True)
        agent._hook_manager = OneShotHookManager(HookOutput(action="append_context", content="need final answer"))
        calls = {"count": 0}

        async def fake_stream(**kwargs):
            calls["count"] += 1
            on_text_delta = kwargs.get("on_text_delta")
            text = "draft" if calls["count"] == 1 else "final"
            if on_text_delta:
                on_text_delta(text)
            return _message([types.SimpleNamespace(type="text", text=text)])

        agent._call_anthropic_stream = fake_stream

        result = asyncio.run(agent.run_once("answer"))

        self.assertEqual(calls["count"], 2)
        self.assertEqual(result["text"], "draftfinal")
        self.assertIn("need final answer", agent._anthropic_messages[-2]["content"])


if __name__ == "__main__":
    unittest.main()
