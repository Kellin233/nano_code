"""Agent state container.

This module is the pure core data plane. It stores conversation state,
token accounting, core callbacks, and provider-neutral message history.
Application capabilities are injected by cli/session.py.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .budget import estimate_model_cost_usd, pricing_for_model
from .models import get_context_window
from .types import CONTEXT_WINDOW_MARGIN, RuntimeEvent, ToolCall, ToolDef, ToolResult


@dataclass
class RuntimeConfig:
    model: str = "claude-opus-4-6"
    provider: str = "anthropic"
    api_base: str | None = None
    anthropic_base_url: str | None = None
    api_key: str | None = None
    thinking: bool = False
    permission_mode: str = "default"
    max_cost_usd: float | None = None
    max_turns: int | None = None
    custom_system_prompt: str | None = None
    is_sub_agent: bool = False
    sandbox_config: Any | None = None
    workspace: Path = field(default_factory=Path.cwd)

    @property
    def use_openai(self) -> bool:
        return self.provider == "openai"


ConfirmFn = Callable[[str], Awaitable[bool]]
RuntimeCallback = Callable[[RuntimeEvent], Awaitable[None] | None]


class Agent:
    """Pure Agent state and protocol helpers."""

    def __init__(
        self,
        config: RuntimeConfig,
        *,
        system_prompt: str | None = None,
        startup_context: str = "",
        session_id: str | None = None,
    ):
        self.config = config
        self.model = config.model
        self.permission_mode = config.permission_mode
        self.thinking = config.thinking
        self.max_cost_usd = config.max_cost_usd
        self.max_turns = config.max_turns
        self.is_sub_agent = config.is_sub_agent

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
        self._confirmed_paths: set[str] = set()
        self._confirm_fn: ConfirmFn | None = None

        self._read_file_state: dict[str, float] = {}
        self._tool_results_dir: Path = (
            Path(self.config.workspace) / ".nanocode" / "sessions" / self.session_id / "tool-results"
        )
        self._result_replacements: dict[str, str] = {}

        self._anthropic_messages: list[dict] = []
        self._openai_messages: list[dict] = []

        self._pending_context_attachments: list[str] = []
        self._startup_context = startup_context
        self._startup_context_injected = False
        self._initial_context_attachments_prepared = False

        self._system_prompt = system_prompt or config.custom_system_prompt or ""

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
        self._start_memory_prefetch_fn: Callable[[str], Any] | None = None
        self._consume_memory_prefetch_fn: Callable[[Any], None] | None = None

        # Application-owned objects. Core stores them opaquely for session/tool
        # callbacks that share the same Agent instance.
        self._tool_registry: Any | None = None
        self._sandbox_manager: Any | None = None
        self._mcp_manager: Any | None = None
        self._hook_manager: Any | None = None
        self._skill_invocation: Any | None = None
        self._active_skills: Any | None = None

    @property
    def effective_window(self) -> int:
        return get_context_window(self.model) - CONTEXT_WINDOW_MARGIN

    @property
    def aborted(self) -> bool:
        return self._aborted

    @property
    def messages(self) -> list[dict]:
        return self._openai_messages if self.config.use_openai else self._anthropic_messages

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def bind_runtime(
        self,
        *,
        tool_registry: Any | None = None,
        sandbox_manager: Any | None = None,
        mcp_manager: Any | None = None,
        hook_manager: Any | None = None,
        skill_invocation: Any | None = None,
        active_skills: Any | None = None,
        tool_definitions: Callable[[], list[ToolDef]] | None = None,
        ensure_ready: Callable[[], Awaitable[None]] | None = None,
        shutdown: Callable[[], Awaitable[None]] | None = None,
        prepare_initial_attachments: Callable[[], None] | None = None,
        start_memory_prefetch: Callable[[str], Any] | None = None,
        consume_memory_prefetch: Callable[[Any], None] | None = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._sandbox_manager = sandbox_manager
        self._mcp_manager = mcp_manager
        self._hook_manager = hook_manager
        self._skill_invocation = skill_invocation
        self._active_skills = active_skills
        self._tool_definitions_fn = tool_definitions
        self._ensure_ready_fn = ensure_ready
        self._shutdown_fn = shutdown
        self._prepare_initial_attachments_fn = prepare_initial_attachments
        self._start_memory_prefetch_fn = start_memory_prefetch
        self._consume_memory_prefetch_fn = consume_memory_prefetch

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

    def set_confirm_fn(self, fn: ConfirmFn) -> None:
        self._confirm_fn = fn

    async def _confirm_dangerous(self, command: str) -> bool:
        if not self._confirm_fn:
            return False
        return await self._confirm_fn(command)

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
        if self.config.use_openai:
            self._openai_messages.append({"role": "user", "content": content})
        else:
            self._anthropic_messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: list[dict]) -> None:
        if self.config.use_openai:
            self._openai_messages.append({"role": "assistant", "content": content})
        else:
            self._anthropic_messages.append({"role": "assistant", "content": content})

    def add_tool_results(self, results: list[dict]) -> None:
        if self.config.use_openai:
            self._openai_messages.extend(results)
        else:
            self._anthropic_messages.append({"role": "user", "content": results})

    def append_user_context(self, text: str) -> None:
        if self.config.use_openai:
            last = self._openai_messages[-1] if self._openai_messages else None
            if last and last.get("role") == "user":
                last["content"] = (last.get("content") or "") + "\n\n" + text
            else:
                self._openai_messages.append({"role": "user", "content": text})
        else:
            last = self._anthropic_messages[-1] if self._anthropic_messages else None
            if last and last.get("role") == "user":
                content = last.get("content", "")
                if isinstance(content, str):
                    last["content"] = content + "\n\n" + text
                elif isinstance(content, list):
                    content.append({"type": "text", "text": text})
            else:
                self._anthropic_messages.append({"role": "user", "content": text})

    def append_meta_user_message(self, text: str) -> None:
        if not text:
            return
        if self.config.use_openai:
            self._openai_messages.append({"role": "user", "content": text})
        else:
            self._anthropic_messages.append({"role": "user", "content": text})

    def restore_session(self, data: dict) -> int:
        if data.get("anthropicMessages"):
            self._anthropic_messages = data["anthropicMessages"]
        if data.get("openaiMessages"):
            self._openai_messages = data["openaiMessages"]
        if self._anthropic_messages or len(self._openai_messages) > (1 if self.config.use_openai else 0):
            self._startup_context_injected = True
        return len(self._openai_messages) if self.config.use_openai else len(self._anthropic_messages)

    def clear_history(self) -> None:
        self._anthropic_messages = []
        self._openai_messages = []
        if self.config.use_openai and self._system_prompt:
            self._openai_messages.append({"role": "system", "content": self._system_prompt})
        if self._active_skills is not None:
            self._active_skills.clear()
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

    def get_read_file_state(self) -> dict[str, float]:
        return self._read_file_state

    def get_sandbox_manager(self):
        return self._sandbox_manager

    def get_mcp_manager(self):
        return self._mcp_manager

    def get_hook_manager(self):
        return self._hook_manager

    def get_skill_invocation(self):
        return self._skill_invocation

    def get_active_skills(self):
        return self._active_skills

    def get_tool_registry(self):
        return self._tool_registry

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

    def start_memory_prefetch(self, user_message: str):
        if self._start_memory_prefetch_fn is None:
            return None
        return self._start_memory_prefetch_fn(user_message)

    def consume_memory_prefetch(self, prefetch) -> None:
        if self._consume_memory_prefetch_fn is not None:
            self._consume_memory_prefetch_fn(prefetch)


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
