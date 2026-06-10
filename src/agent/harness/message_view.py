"""Provider-specific message access helpers for compression."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass
class ToolResultSlot:
    message: dict[str, Any]
    content_ref: Any
    tool_use_id: str
    content: str
    tool_name: str = ""

    def set_content(self, value: str) -> None:
        if isinstance(self.content_ref, tuple):
            container, key = self.content_ref
            container[key] = value
        else:
            self.message["content"] = value
        self.content = value


class MessageView:
    def __init__(self, messages: list[dict[str, Any]], *, use_openai: bool):
        self.messages = messages
        self.use_openai = use_openai

    def iter_tool_uses(self) -> dict[str, str]:
        if self.use_openai:
            result: dict[str, str] = {}
            for msg in self.messages:
                for call in msg.get("tool_calls") or []:
                    if call.get("id"):
                        result[str(call["id"])] = str((call.get("function") or {}).get("name") or "")
            return result

        result: dict[str, str] = {}
        for msg in self.messages:
            for block in _as_blocks(msg.get("content")):
                if block.get("type") == "tool_use" and block.get("id"):
                    result[str(block["id"])] = str(block.get("name") or "")
        return result

    def iter_tool_results(self) -> Iterable[ToolResultSlot]:
        uses = self.iter_tool_uses()
        if self.use_openai:
            for msg in self.messages:
                if msg.get("role") != "tool":
                    continue
                call_id = str(msg.get("tool_call_id") or "")
                content = msg.get("content") or ""
                if isinstance(content, str):
                    yield ToolResultSlot(
                        message=msg,
                        content_ref=None,
                        tool_use_id=call_id,
                        tool_name=uses.get(call_id, ""),
                        content=content,
                    )
            return

        for msg in self.messages:
            for block in _as_blocks(msg.get("content")):
                if block.get("type") != "tool_result":
                    continue
                call_id = str(block.get("tool_use_id") or "")
                content = block.get("content") or ""
                if isinstance(content, str):
                    yield ToolResultSlot(
                        message=msg,
                        content_ref=(block, "content"),
                        tool_use_id=call_id,
                        tool_name=uses.get(call_id, ""),
                        content=content,
                    )


def _as_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, list):
        return [block for block in content if isinstance(block, dict)]
    return []
