"""Agent state container.

This module is the pure core data plane. It stores conversation state,
token accounting, core callbacks, and provider-neutral message history.
Application capabilities are injected by cli/session.py.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from .budget import estimate_model_cost_usd, pricing_for_model
from .models import get_context_window
from .types import (
    CONTEXT_WINDOW_MARGIN,
    ConversationHistory,
    ConversationMessage,
    RuntimeEvent,
    TextBlock,
    ToolCall,
    ToolDef,
    ToolResult,
    ToolUseBlock,
)


@dataclass
class AgentConfig:
    model: str = "claude-opus-4-6"
    message_format: Literal["anthropic", "openai"] = "anthropic"
    thinking: bool = False
    max_cost_usd: float | None = None
    max_turns: int | None = None
    context_window: int | None = None

    @property
    def use_openai(self) -> bool:
        return self.message_format == "openai"


RuntimeCallback = Callable[[RuntimeEvent], Awaitable[None] | None]


class Agent:
    """Pure Agent state and protocol helpers."""

    def __init__(
        self,
        config: AgentConfig,
        *,
        system_prompt: str | None = None,
        startup_context: str = "",
        session_id: str | None = None,
    ):
        self.config = config
        self.model = config.model
        self.message_format = config.message_format
        self.thinking = config.thinking
        self.max_cost_usd = config.max_cost_usd
        self.max_turns = config.max_turns

        self.session_id = session_id or uuid.uuid4().hex[:8]
        self.session_start_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_input_cache_hit_tokens = 0
        self.total_input_cache_miss_tokens = 0
        self.last_input_token_count = 0
        self.current_turns = 0
        self.last_api_call_time = 0.0

        self._consecutive_compact_failures = 0
        self._aborted = False
        self._current_task = None

        self.conversation = ConversationHistory()

        self._pending_context_attachments: list[str] = []
        self._startup_context = startup_context
        self._startup_context_injected = False
        self._initial_context_attachments_prepared = False

        self._system_prompt = system_prompt or ""

        self._diagnostics: list[str] = []

        # Callback slots filled by AgentSession.
        self._on_agent_start: RuntimeCallback | None = None
        self._on_agent_end: RuntimeCallback | None = None
        self._on_turn_start: RuntimeCallback | None = None
        self._on_turn_end: RuntimeCallback | None = None
        self._on_before_tool_call: Callable[[ToolCall], Awaitable[None] | None] | None = None
        self._on_after_tool_call: Callable[[ToolCall, ToolResult], Awaitable[None] | None] | None = None

        self._tool_definitions_fn: Callable[[], list[ToolDef]] | None = None
        self._ensure_ready_fn: Callable[[], Awaitable[None]] | None = None
        self._shutdown_fn: Callable[[], Awaitable[None]] | None = None
        self._prepare_initial_attachments_fn: Callable[[], None] | None = None

    @property
    def use_openai(self) -> bool:
        return self.message_format == "openai"

    @property
    def effective_window(self) -> int:
        window = self.config.context_window or get_context_window(self.model)
        return max(1, window - CONTEXT_WINDOW_MARGIN)

    @property
    def aborted(self) -> bool:
        return self._aborted

    @property
    def messages(self) -> ConversationHistory:
        return self.conversation

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def bind_runtime(
        self,
        *,
        tool_definitions: Callable[[], list[ToolDef]] | None = None,
        ensure_ready: Callable[[], Awaitable[None]] | None = None,
        shutdown: Callable[[], Awaitable[None]] | None = None,
        prepare_initial_attachments: Callable[[], None] | None = None,
    ) -> None:
        self._tool_definitions_fn = tool_definitions
        self._ensure_ready_fn = ensure_ready
        self._shutdown_fn = shutdown
        self._prepare_initial_attachments_fn = prepare_initial_attachments

    def set_callbacks(
        self,
        *,
        on_agent_start: RuntimeCallback | None = None,
        on_agent_end: RuntimeCallback | None = None,
        on_turn_start: RuntimeCallback | None = None,
        on_turn_end: RuntimeCallback | None = None,
        on_before_tool_call: Callable[[ToolCall], Awaitable[None] | None] | None = None,
        on_after_tool_call: Callable[[ToolCall, ToolResult], Awaitable[None] | None] | None = None,
    ) -> None:
        self._on_agent_start = on_agent_start
        self._on_agent_end = on_agent_end
        self._on_turn_start = on_turn_start
        self._on_turn_end = on_turn_end
        self._on_before_tool_call = on_before_tool_call
        self._on_after_tool_call = on_after_tool_call

    async def emit(self, callback: RuntimeCallback | None, event: RuntimeEvent) -> None:
        if callback is None:
            return
        result = callback(event)
        if hasattr(result, "__await__"):
            await result  # type: ignore[misc]

    def abort(self) -> None:
        self._aborted = True
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()

    def reset_abort(self) -> None:
        self._aborted = False

    def tool_definitions(self) -> list[ToolDef]:
        if self._tool_definitions_fn is None:
            return []
        return self._tool_definitions_fn()

    def get_token_usage(self) -> dict:
        return {"input": self.total_input_tokens, "output": self.total_output_tokens}

    def record_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        *,
        input_cache_hit_tokens: int = 0,
        input_cache_miss_tokens: int = 0,
    ) -> None:
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_input_cache_hit_tokens += input_cache_hit_tokens
        self.total_input_cache_miss_tokens += input_cache_miss_tokens
        self.last_input_token_count = input_tokens

    def estimated_cost_usd(self) -> float:
        return estimate_model_cost_usd(
            self.model,
            input_tokens=self.total_input_tokens,
            output_tokens=self.total_output_tokens,
            input_cache_hit_tokens=self.total_input_cache_hit_tokens,
            input_cache_miss_tokens=self.total_input_cache_miss_tokens,
        )

    def cost_summary(self) -> str:
        cost = self.estimated_cost_usd()
        pricing = pricing_for_model(self.model)
        budget_info = f" / ${self.max_cost_usd} budget" if self.max_cost_usd else ""
        turn_info = f" | Turns: {self.current_turns}/{self.max_turns}" if self.max_turns else ""
        cache_info = ""
        if self.total_input_cache_hit_tokens or self.total_input_cache_miss_tokens:
            cache_info = (
                f"\n  Cache: {self.total_input_cache_hit_tokens} hit / "
                f"{self.total_input_cache_miss_tokens} miss input tokens"
            )
        return (
            f"Tokens: {self.total_input_tokens} in / {self.total_output_tokens} out\n"
            f"  Estimated cost: ${cost:.4f}{budget_info} ({pricing.label}){turn_info}"
            f"{cache_info}"
        )

    def show_cost(self) -> str:
        return self.cost_summary()

    def budget_exceeded(self) -> dict:
        cost = self.estimated_cost_usd()
        if self.max_cost_usd is not None and cost >= self.max_cost_usd:
            pricing = pricing_for_model(self.model)
            return {"exceeded": True, "reason": f"Cost limit reached (${cost:.4f}, {pricing.label})"}
        if self.max_turns is not None and self.current_turns >= self.max_turns:
            return {"exceeded": True, "reason": f"Turn limit reached ({self.current_turns})"}
        return {"exceeded": False}

    def add_user_message(self, content: str) -> None:
        self.conversation.add_user(content)

    def add_assistant_message(self, content: str | list[dict], tool_calls: list[ToolCall] | None = None) -> None:
        if isinstance(content, str):
            self.conversation.add_assistant(content, tool_calls)
            return

        blocks = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = str(block.get("text") or "")
                if text:
                    blocks.append(TextBlock(text))
            elif block.get("type") == "tool_use":
                raw_input = block.get("input")
                blocks.append(ToolUseBlock(
                    id=str(block.get("id") or ""),
                    name=str(block.get("name") or ""),
                    input=dict(raw_input) if isinstance(raw_input, dict) else {},
                ))
        if blocks:
            self.conversation.messages.append(ConversationMessage(role="assistant", content=blocks))

    def add_tool_results(self, results: list[tuple[ToolCall, ToolResult]]) -> None:
        self.conversation.add_tool_results(results)

    def append_user_context(self, text: str) -> None:
        self.conversation.append_user_context(text)

    def append_meta_user_message(self, text: str) -> None:
        self.conversation.append_user_context(text)

    def restore_conversation(self, conversation: ConversationHistory) -> int:
        self.conversation = conversation
        if self.conversation.count():
            self._startup_context_injected = True
        return self.conversation.count()

    def clear_history(self) -> None:
        self.conversation.clear()
        self._startup_context_injected = not bool(self._startup_context)
        self._pending_context_attachments.clear()
        self._initial_context_attachments_prepared = False
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_input_cache_hit_tokens = 0
        self.total_input_cache_miss_tokens = 0
        self.last_input_token_count = 0

    async def ensure_mcp_initialized(self) -> None:
        if self._ensure_ready_fn is not None:
            await self._ensure_ready_fn()

    async def shutdown(self) -> None:
        if self._shutdown_fn is not None:
            await self._shutdown_fn()

    def queue_context_attachment(self, text: str) -> None:
        if text and text.strip():
            self._pending_context_attachments.append(text)

    def flush_pending_attachments(self) -> None:
        if not self._pending_context_attachments:
            return
        combined = "\n\n".join(self._pending_context_attachments)
        self._pending_context_attachments.clear()
        self.append_meta_user_message(combined)

    def inject_startup_context(self) -> None:
        if self._startup_context_injected:
            return
        if self._startup_context:
            self.append_meta_user_message(self._startup_context)
        self._startup_context_injected = True

    def prepare_initial_attachments(self) -> None:
        if self._initial_context_attachments_prepared:
            return
        self._initial_context_attachments_prepared = True
        if self._prepare_initial_attachments_fn is not None:
            self._prepare_initial_attachments_fn()


def format_agent_results(results: list[dict]) -> str:
    if not results:
        return "(Sub-agent produced no output)"

    parts = []
    for i, result in enumerate(results):
        if len(results) > 1:
            parts.append(f"--- Sub-agent {i + 1} ({result.get('type', 'general')}) ---")
        if "error" in result:
            parts.append(f"Error: {result['error']}")
        parts.append(result.get("text", "(no output)"))
    return "\n\n".join(parts)
