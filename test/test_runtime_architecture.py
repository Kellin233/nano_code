from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nanocode.core import (
    AgentTurn,
    AssistantMessage,
    CoreToolCall,
    CoreToolResult,
    Message,
    ModelTextDelta,
    ModelTurnComplete,
    ModelUsage,
    TurnFinished,
    TurnToolCallFinished,
    TurnToolCallStarted,
)
from nanocode.protocol import ProtocolRequest
from nanocode.server import NanoCodeServer
from nanocode.session import ArtifactStore, SessionEventStore
from nanocode.runtime import RuntimeEvent


class FakeProvider:
    def __init__(self, turns):
        self.turns = list(turns)

    async def stream_turn(self, messages):
        _ = messages
        for event in self.turns.pop(0):
            yield event


class FakeTools:
    async def execute(self, calls):
        return [
            CoreToolResult(call_id=call.id, name=call.name, content=f"result:{call.name}")
            for call in calls
        ]


class CoreTurnArchitectureTests(unittest.IsolatedAsyncioTestCase):
    async def test_core_turn_stops_without_tool_calls(self):
        provider = FakeProvider([[
            ModelTextDelta("hello"),
            ModelTurnComplete(
                AssistantMessage(content="hello"),
                usage=ModelUsage(input_tokens=3, output_tokens=4),
            ),
        ]])
        events = [event async for event in AgentTurn(provider, FakeTools()).run([
            Message(role="user", content="hi")
        ])]

        self.assertEqual(events[0], ModelTextDelta("hello"))
        self.assertIsInstance(events[-1], TurnFinished)
        self.assertEqual(events[-1].reason, "stop")
        self.assertEqual(events[-1].input_tokens, 3)
        self.assertEqual(events[-1].output_tokens, 4)

    async def test_core_turn_executes_tool_and_continues(self):
        call = CoreToolCall(id="tool-1", name="read_file", input={"file_path": "x"})
        provider = FakeProvider([
            [
                ModelTurnComplete(
                    AssistantMessage(content=[{"type": "tool_use"}], tool_calls=[call]),
                    usage=ModelUsage(input_tokens=1, output_tokens=2),
                    stop_reason="tool_calls",
                ),
            ],
            [
                ModelTextDelta("done"),
                ModelTurnComplete(
                    AssistantMessage(content="done"),
                    usage=ModelUsage(input_tokens=3, output_tokens=4),
                ),
            ],
        ])
        messages = [Message(role="user", content="read")]
        events = [event async for event in AgentTurn(provider, FakeTools()).run(messages)]

        self.assertIsInstance(events[0], TurnToolCallStarted)
        self.assertIsInstance(events[1], TurnToolCallFinished)
        self.assertEqual(events[1].result.content, "result:read_file")
        self.assertEqual(events[-1], TurnFinished("stop", 4, 6))
        self.assertEqual(messages[-2].role, "tool")


class SessionArchitectureTests(unittest.TestCase):
    def test_event_store_replays_events_and_artifacts_are_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SessionEventStore("abc", root=root)
            event = RuntimeEvent(type="assistant.delta", thread_id="abc", seq=1, payload={"text": "hi"})
            store.append(event)

            replayed = store.replay()
            self.assertEqual(len(replayed), 1)
            self.assertEqual(replayed[0].payload["text"], "hi")
            self.assertEqual(store.next_seq(), 2)

            ref = ArtifactStore("abc", root=root).write_text("stdout.txt", "large output")
            self.assertTrue(Path(ref["path"]).exists())
            self.assertEqual(ref["size_bytes"], len("large output".encode()))


class ProtocolArchitectureTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_create_resume_abort_and_session_list(self):
        server = NanoCodeServer()
        create = [
            message
            async for message in server.handle(ProtocolRequest(
                id=1,
                method="thread.create",
                params={"config": {"api_key": "test", "permission_mode": "dontAsk"}},
            ))
        ]
        thread_id = create[0]["result"]["thread_id"]
        self.assertIn(thread_id, server.threads)

        abort = [
            message
            async for message in server.handle(ProtocolRequest(
                id=2,
                method="thread.abort",
                params={"thread_id": thread_id},
            ))
        ]
        self.assertTrue(abort[0]["result"]["aborted"])

        sessions = [
            message
            async for message in server.handle(ProtocolRequest(
                id=3,
                method="session.list",
                params={},
            ))
        ]
        self.assertIsInstance(sessions[0]["result"]["sessions"], list)


if __name__ == "__main__":
    unittest.main()
