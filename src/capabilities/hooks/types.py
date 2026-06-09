"""Data structures for command hooks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

HookEventName = Literal["UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"]
HookAction = Literal["allow", "deny", "modify", "append_context"]


@dataclass(frozen=True)
class HookCommand:
    event: HookEventName
    command: str
    matcher: str = "*"
    timeout_ms: int = 3000
    fail_closed: bool = False


@dataclass
class HookInput:
    event: HookEventName
    session_id: str
    cwd: str
    prompt: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_result: dict[str, Any] = field(default_factory=dict)
    last_assistant_text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "session_id": self.session_id,
            "cwd": self.cwd,
            "prompt": self.prompt,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "tool_result": self.tool_result,
            "last_assistant_text": self.last_assistant_text,
        }


@dataclass
class HookOutput:
    action: HookAction = "allow"
    reason: str = ""
    updated_input: dict[str, Any] | None = None
    content: str = ""
    error: str = ""

