"""Conversation access helpers for compression."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..types import ConversationHistory, ConversationMessage, ToolResultBlock, ToolUseBlock


@dataclass
class ToolResultSlot:
    message: ConversationMessage
    block: ToolResultBlock
    tool_use_id: str
    content: str
    tool_name: str = ""

    def set_content(self, value: str) -> None:
        self.block.content = value
        self.content = value


class MessageView:
    def __init__(self, conversation: ConversationHistory):
        self.conversation = conversation

    def iter_tool_uses(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for message in self.conversation:
            if message.role != "assistant":
                continue
            for block in message.content:
                if isinstance(block, ToolUseBlock) and block.id:
                    result[block.id] = block.name
        return result

    def iter_tool_results(self) -> Iterable[ToolResultSlot]:
        uses = self.iter_tool_uses()
        for message in self.conversation:
            if message.role != "tool_result":
                continue
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    yield ToolResultSlot(
                        message=message,
                        block=block,
                        tool_use_id=block.tool_use_id,
                        tool_name=block.tool_name or uses.get(block.tool_use_id, ""),
                        content=block.content,
                    )
