"""测试 Runtime 架构 — Session, Events, Protocol。

适配重构后的 RuntimeEvent（无 thread_id/seq 字段）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nanocode.protocol import ProtocolRequest, ProtocolResponse
from nanocode.protocol.messages import ProtocolError
from nanocode.server import NanoCodeServer
from nanocode.session import ArtifactStore, SessionEventStore
from nanocode.runtime import RuntimeEvent


class TestSessionEventStore(unittest.TestCase):
    """SessionEventStore 测试。"""

    def test_append_and_replay_events(self):
        """EventStore 正确存储和回放事件。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SessionEventStore("abc", root=root)
            event = RuntimeEvent(type="assistant.delta", thread_id="abc", seq=1, payload={"text": "hi"})
            store.append(event)

            replayed = store.replay()
            self.assertEqual(len(replayed), 1)
            self.assertEqual(replayed[0].type, "assistant.delta")
            self.assertEqual(replayed[0].payload["text"], "hi")
            self.assertEqual(replayed[0].thread_id, "abc")
            self.assertEqual(store.next_seq(), 2)

    def test_artifact_store_separate(self):
        """ArtifactStore 独立于 EventStore 存储大文件。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ArtifactStore("abc", root=root)
            ref = store.write_text("stdout.txt", "large output")
            self.assertTrue(Path(ref["path"]).exists())
            self.assertEqual(ref["size_bytes"], len("large output".encode()))


class TestProtocolMessages(unittest.TestCase):
    """协议消息测试。"""

    def test_protocol_request_from_message(self):
        """ProtocolRequest.from_message 正确解析。"""
        data = {"id": "1", "method": "thread.create", "params": {"model": "claude"}}
        req = ProtocolRequest.from_message(data)
        self.assertEqual(req.id, "1")
        self.assertEqual(req.method, "thread.create")

    def test_protocol_response_to_message(self):
        """ProtocolResponse.to_message 正确序列化。"""
        resp = ProtocolResponse(id="1", result={"thread_id": "abc"})
        msg = resp.to_message()
        self.assertEqual(msg["id"], "1")
        self.assertEqual(msg["result"]["thread_id"], "abc")

    def test_protocol_response_with_error(self):
        """ProtocolResponse 包含 error 时正确序列化。"""
        resp = ProtocolResponse(id="1", error={"code": "internal_error", "message": "boom"})
        msg = resp.to_message()
        self.assertEqual(msg["error"]["code"], "internal_error")

    def test_protocol_error_to_dict(self):
        """ProtocolError.to_dict 正确格式化。"""
        err = ProtocolError("invalid_params", "missing field")
        d = err.to_dict()
        self.assertEqual(d["code"], "invalid_params")
        self.assertEqual(d["message"], "missing field")


class TestNanoCodeServer(unittest.TestCase):
    """NanoCodeServer 测试。"""

    def test_thread_create_returns_thread_id(self):
        """thread.create 返回 thread_id。"""
        server = NanoCodeServer()
        req = ProtocolRequest(id="1", method="thread.create", params={})
        # handle 是 async generator
        import asyncio

        async def collect():
            results = []
            async for msg in server.handle(req):
                results.append(msg)
            return results

        results = asyncio.run(collect())
        self.assertTrue(len(results) > 0)
        self.assertIn("thread_id", results[0].get("result", {}))


if __name__ == "__main__":
    unittest.main()
