"""Core protocol types shared by agent, providers, and application code."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

ToolDef = dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]
    provider: str = "model"


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    extra_messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TextBlock:
    text: str


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    tool_name: str = ""
    is_error: bool = False


ConversationBlock = TextBlock | ToolUseBlock | ToolResultBlock
ConversationRole = Literal["user", "assistant", "tool_result"]


@dataclass
class ConversationMessage:
    role: ConversationRole
    content: list[ConversationBlock] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class ConversationHistory:
    """A single canonical transcript independent of provider wire formats."""

    def __init__(self, messages: list[ConversationMessage] | None = None):
        self.messages: list[ConversationMessage] = list(messages or [])

    def __len__(self) -> int:
        return len(self.messages)

    def __iter__(self):
        return iter(self.messages)

    def add_user(self, text: str) -> None:
        self.messages.append(ConversationMessage(role="user", content=[TextBlock(str(text))]))

    def add_assistant(self, text: str = "", tool_calls: list[ToolCall] | None = None) -> None:
        content: list[ConversationBlock] = []
        if text:
            content.append(TextBlock(str(text)))
        for call in tool_calls or []:
            content.append(ToolUseBlock(id=call.id, name=call.name, input=dict(call.input or {})))
        if content:
            self.messages.append(ConversationMessage(role="assistant", content=content))

    def add_tool_results(self, results: list[tuple[ToolCall, ToolResult]]) -> None:
        blocks = [
            ToolResultBlock(
                tool_use_id=call.id,
                tool_name=call.name,
                content=result.content,
                is_error=result.is_error,
            )
            for call, result in results
        ]
        if blocks:
            self.messages.append(ConversationMessage(role="tool_result", content=blocks))

    def append_user_context(self, text: str) -> None:
        if not text:
            return
        last = self.messages[-1] if self.messages else None
        if last and last.role == "user":
            last.content.append(TextBlock(str(text)))
            return
        self.add_user(str(text))

    def clear(self) -> None:
        self.messages.clear()

    def replace(self, messages: list[ConversationMessage]) -> None:
        self.messages = list(messages)

    def count(self) -> int:
        return len(self.messages)

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": 1,
            "messages": [message_to_dict(message) for message in self.messages],
        }

    @classmethod
    def restore(cls, payload: dict[str, Any] | None) -> ConversationHistory:
        if not isinstance(payload, dict):
            return cls()
        raw_messages = payload.get("messages")
        if not isinstance(raw_messages, list):
            return cls()
        return cls([message_from_dict(item) for item in raw_messages if isinstance(item, dict)])


def message_text(message: ConversationMessage) -> str:
    return "\n\n".join(block.text for block in message.content if isinstance(block, TextBlock))


def message_to_dict(message: ConversationMessage) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": [block_to_dict(block) for block in message.content],
        "metadata": dict(message.metadata),
    }


def message_from_dict(data: dict[str, Any]) -> ConversationMessage:
    role = str(data.get("role") or "user")
    if role not in {"user", "assistant", "tool_result"}:
        role = "user"
    content = data.get("content")
    blocks = [block_from_dict(block) for block in content] if isinstance(content, list) else []
    return ConversationMessage(
        role=role,  # type: ignore[arg-type]
        content=[block for block in blocks if block is not None],
        metadata=dict(data.get("metadata") or {}),
    )


def block_to_dict(block: ConversationBlock) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input)}
    return {
        "type": "tool_result",
        "tool_use_id": block.tool_use_id,
        "tool_name": block.tool_name,
        "content": block.content,
        "is_error": block.is_error,
    }


def block_from_dict(data: dict[str, Any]) -> ConversationBlock | None:
    block_type = data.get("type")
    if block_type == "text":
        return TextBlock(str(data.get("text") or ""))
    if block_type == "tool_use":
        raw_input = data.get("input")
        return ToolUseBlock(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            input=dict(raw_input) if isinstance(raw_input, dict) else {},
        )
    if block_type == "tool_result":
        return ToolResultBlock(
            tool_use_id=str(data.get("tool_use_id") or ""),
            tool_name=str(data.get("tool_name") or ""),
            content=str(data.get("content") or ""),
            is_error=bool(data.get("is_error")),
        )
    return None


@dataclass(frozen=True)
class RuntimeEvent:
    """Unified runtime event emitted by the agent loop."""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    thread_id: str = ""
    seq: int = 0
    timestamp: float = field(default_factory=lambda: time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "thread_id": self.thread_id,
            "seq": self.seq,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeEvent:
        return cls(
            type=str(data.get("type", "")),
            thread_id=str(data.get("thread_id", "")),
            seq=int(data.get("seq", 0)),
            payload=dict(data.get("payload") or {}),
            timestamp=float(data.get("timestamp", time.time())),
        )


CONTEXT_WINDOW_MARGIN = 20000
DEFAULT_MAX_TOKENS = 16384
MAX_RETRIES = 3
MAX_RETRY_DELAY_MS = 30000
