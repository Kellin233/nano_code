"""Thread-level SDK wrapper."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


class ThreadClient:
    def __init__(self, client, thread_id: str):
        self.client = client
        self.thread_id = thread_id

    async def submit(self, prompt: str) -> AsyncIterator[dict[str, Any]]:
        async for message in self.client.stream_request(
            "thread.submit",
            {"thread_id": self.thread_id, "prompt": prompt},
        ):
            if message.get("method") == "runtime.event":
                yield dict(message.get("params") or {})

    async def abort(self) -> dict[str, Any]:
        return await self.client.request("thread.abort", {"thread_id": self.thread_id})

    async def compact(self) -> dict[str, Any]:
        return await self.client.request("thread.compact", {"thread_id": self.thread_id})

    async def resolve_approval(
        self,
        request_id: str,
        *,
        approved: bool,
        remember: bool = False,
    ) -> dict[str, Any]:
        return await self.client.request(
            "approval.resolve",
            {
                "thread_id": self.thread_id,
                "request_id": request_id,
                "approved": approved,
                "remember": remember,
            },
        )
