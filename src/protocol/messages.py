"""JSONL protocol messages, methods, and errors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


THREAD_CREATE = "thread.create"
THREAD_RESUME = "thread.resume"
THREAD_SUBMIT = "thread.submit"
THREAD_ABORT = "thread.abort"
THREAD_COMPACT = "thread.compact"
APPROVAL_RESOLVE = "approval.resolve"
SESSION_LIST = "session.list"

SUPPORTED_METHODS = {
    THREAD_CREATE,
    THREAD_RESUME,
    THREAD_SUBMIT,
    THREAD_ABORT,
    THREAD_COMPACT,
    APPROVAL_RESOLVE,
    SESSION_LIST,
}


ProtocolMessage = dict[str, Any]


class ProtocolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class ProtocolRequest:
    id: str | int | None
    method: str
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_message(cls, data: ProtocolMessage) -> "ProtocolRequest":
        return cls(
            id=data.get("id"),
            method=str(data.get("method", "")),
            params=dict(data.get("params") or {}),
        )


@dataclass(frozen=True)
class ProtocolResponse:
    id: str | int | None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    def to_message(self) -> ProtocolMessage:
        data: ProtocolMessage = {"id": self.id}
        if self.error is not None:
            data["error"] = self.error
        else:
            data["result"] = self.result or {}
        return data
