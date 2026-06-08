"""Dispatch JSONL protocol requests to the server application."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from .messages import ProtocolError, ProtocolRequest, ProtocolResponse


class ProtocolDispatcher:
    def __init__(self, app):
        self.app = app

    async def dispatch(self, data: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        request = ProtocolRequest.from_message(data)
        try:
            async for message in self.app.handle(request):
                yield message
        except ProtocolError as exc:
            yield ProtocolResponse(id=request.id, error=exc.to_dict()).to_message()
        except Exception as exc:
            yield ProtocolResponse(
                id=request.id,
                error={"code": "internal_error", "message": str(exc)},
            ).to_message()
