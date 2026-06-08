from __future__ import annotations

import asyncio
import types
import unittest

from nanocode.runtime.agent import Agent


class AgentLoopTests(unittest.TestCase):
    def test_run_once_consumes_event_stream_output(self) -> None:
        agent = Agent(api_key="test-key", is_sub_agent=True)

        async def fake_stream(*, on_text_delta=None, on_thinking_delta=None, on_tool_block_complete=None):
            if on_text_delta:
                on_text_delta("hello")
            return types.SimpleNamespace(
                usage=types.SimpleNamespace(input_tokens=3, output_tokens=2),
                content=[types.SimpleNamespace(type="text", text="hello")],
            )

        agent._call_anthropic_stream = fake_stream

        result = asyncio.run(agent.run_once("say hello"))

        self.assertEqual(result["text"], "hello")
        self.assertEqual(result["tokens"], {"input": 3, "output": 2})


if __name__ == "__main__":
    unittest.main()
