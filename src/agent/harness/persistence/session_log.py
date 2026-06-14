"""Durable session log for checkpoint/resume."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...types import (
    ConversationHistory,
    ConversationMessage,
    ToolResultBlock,
    ToolUseBlock,
    message_from_dict,
    message_to_dict,
)
from .atomic import append_jsonl
from .report import now_iso

SESSION_LOG_VERSION = 2
INTERRUPTED_TOOL_RESULT = "Interrupted before tool result"


class SessionLog:
    """Append-only durable conversation state for one session."""

    def __init__(self, session_id: str, root: Path | str | None = None):
        self.session_id = str(session_id)
        if root is None:
            root = Path.home() / ".nanocode" / "sessions"
        self.root = Path(root)
        self.dir = self.root / self.session_id
        self.path = self.dir / "session.jsonl"

    def ensure_session(self, metadata: dict[str, Any]) -> None:
        if self.path.exists():
            return
        payload = {
            "type": "session",
            "version": SESSION_LOG_VERSION,
            "id": self.session_id,
            "created_at": now_iso(),
            **metadata,
        }
        self._append(payload)

    def commit(self, conversation: ConversationHistory, *, reason: str, run_id: str = "") -> bool:
        current = [message_to_dict(message) for message in conversation.messages]
        persisted = [message_to_dict(message) for message in self.load(repair=False).messages]

        if current == persisted:
            return False

        if not current:
            self._append_entry({"type": "clear", "reason": reason, "run_id": run_id})
            return True

        if len(current) > len(persisted) and current[: len(persisted)] == persisted:
            for message in conversation.messages[len(persisted):]:
                self._append_entry({
                    "type": "message",
                    "reason": reason,
                    "run_id": run_id,
                    "message": message_to_dict(message),
                })
            return True

        entry_type = "compact" if reason in {"context_compact", "manual_compact"} else "replace"
        self._append_entry({
            "type": entry_type,
            "reason": reason,
            "run_id": run_id,
            "conversation": conversation.snapshot(),
        })
        return True

    def append_checkpoint(self, *, reason: str, run_id: str = "") -> int:
        return self._append_entry({"type": "checkpoint", "reason": reason, "run_id": run_id})

    def load(self, *, repair: bool = True) -> ConversationHistory:
        history = ConversationHistory()
        for entry in self._entries():
            entry_type = entry.get("type")
            if entry_type == "message":
                message = entry.get("message")
                if isinstance(message, dict):
                    parsed = message_from_dict(message)
                    if parsed.content:
                        history.messages.append(parsed)
            elif entry_type in {"replace", "compact"}:
                history = ConversationHistory.restore(entry.get("conversation"))
            elif entry_type == "clear":
                history.clear()
        return repair_orphaned_tool_calls(history) if repair else history

    def metadata(self) -> dict[str, Any]:
        header: dict[str, Any] = {}
        last_at = ""
        last_seq = 0

        for entry in self._entries():
            if entry.get("type") == "session":
                header = entry
            if "created_at" in entry:
                last_at = str(entry.get("created_at") or "")
            if isinstance(entry.get("seq"), int):
                last_seq = max(last_seq, int(entry["seq"]))

        if not header:
            return {}
        created_at = str(header.get("created_at") or "")
        return {
            "id": str(header.get("id") or self.session_id),
            "createdAt": created_at,
            "updatedAt": last_at or created_at,
            "startTime": created_at,
            "workspace": str(header.get("workspace") or ""),
            "provider": str(header.get("provider") or ""),
            "model": str(header.get("model") or ""),
            "messageCount": self.load(repair=False).count(),
            "lastSeq": last_seq,
        }

    def _append_entry(self, payload: dict[str, Any]) -> int:
        seq = self._next_seq()
        self._append({"seq": seq, "created_at": now_iso(), **payload})
        return seq

    def _append(self, payload: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        append_jsonl(self.path, payload, durable=True, sort_keys=False)

    def _next_seq(self) -> int:
        seq = 0
        for entry in self._entries():
            if isinstance(entry.get("seq"), int):
                seq = max(seq, int(entry["seq"]))
        return seq + 1

    def _entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except Exception:
                break
            if isinstance(entry, dict):
                entries.append(entry)
        return entries


def repair_orphaned_tool_calls(history: ConversationHistory) -> ConversationHistory:
    repaired: list[ConversationMessage] = []
    pending: dict[str, ToolUseBlock] = {}

    def flush_pending() -> None:
        nonlocal pending
        if not pending:
            return
        repaired.append(ConversationMessage(
            role="tool_result",
            content=[
                ToolResultBlock(
                    tool_use_id=call.id,
                    tool_name=call.name,
                    content=INTERRUPTED_TOOL_RESULT,
                    is_error=True,
                )
                for call in pending.values()
            ],
        ))
        pending = {}

    for message in history.messages:
        if pending and message.role != "tool_result":
            flush_pending()

        repaired.append(message)
        if message.role == "assistant":
            pending.update({
                block.id: block
                for block in message.content
                if isinstance(block, ToolUseBlock) and block.id
            })
        elif message.role == "tool_result":
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    pending.pop(block.tool_use_id, None)

    flush_pending()
    return ConversationHistory(repaired)
