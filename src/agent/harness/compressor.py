"""Context pressure management.

NanoCode uses three context layers:
  Level 1. Tool Result Budget — large tool results are persisted before entering history
  Level 2. Tool History Snip  — old rereadable tool results are replaced with placeholders
  Level 3. Context Compact    — old conversation is summarized while recent context is kept verbatim

Level 1 runs in ToolRuntime. This module implements Levels 2 and 3. It belongs
to harness: it may mutate conversation history and call injected callbacks, but
it does not import providers, cli, tui, memory, skills, or MCP modules.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import NamedTuple

from ..budget import estimate_message_tokens, estimate_messages_tokens
from ..types import ConversationHistory, ConversationMessage, TextBlock
from .message_view import MessageView

SNIPPABLE_TOOLS = {"read_file", "grep_search", "list_files", "run_shell", "web_fetch", "write_file", "edit_file"}
SNIP_PLACEHOLDER = "[Content snipped - re-read if needed]"
SNIP_THRESHOLD = 0.60
SNIP_IDLE_SECONDS = 5 * 60
KEEP_RECENT_TOOL_RESULTS = 3

CONTEXT_COMPACT_THRESHOLD = 0.80
COMPACT_KEEP_RECENT_RATIO = 0.20
COMPACT_SUMMARY_MAX_TOKENS = 2048
MAX_CONSECUTIVE_COMPACT_FAILURES = 3

COMPACT_SYSTEM_PROMPT = """You are a conversation summarizer. Output a structured summary with ALL of the following sections. Each section MUST be populated — never omit a section, write "None" if there is no content.

## 1. Primary Request
The user's explicit requests and intentions.

## 2. Key Technical Concepts
Technologies, frameworks, libraries, and technical concepts discussed.

## 3. Files and Code
Every file examined, modified, or created. Include:
- File path and line numbers where changes were made
- Key code snippets showing what was changed
- Files that were read but NOT modified — note them as "examined only"

## 4. Errors and Fixes
Every error encountered and how it was resolved. Include exact error messages and fixes.

## 5. Problem Solving
Problems solved, investigations completed, and investigations still in progress.

## 6. All User Messages
The original text of all user messages excluding tool results.

## 7. Pending Tasks
Tasks the user requested that are not yet completed.

## 8. Current Work
What was actively being worked on before compaction. Include exact file paths, function names, and current edit state.

## 9. Optional Next Step
The most logical next action to continue the work."""

COMPACT_USER_PROMPT = (
    "Summarize the conversation above using the 9-section structured format. "
    "Be precise: include exact file paths, function names, and error messages. "
    "The summary must be self-contained, but do not invent details that are not present."
)


class ContextPreparation(NamedTuple):
    conversation: ConversationHistory
    changed: bool
    reason: str


class Compressor:
    """Tool history snipping and context compacting for one Agent instance."""

    def __init__(
        self,
        agent,
        *,
        workspace: Path,
        hooks=None,
        summarize_messages: Callable[[ConversationHistory, str, str, int], Awaitable[str | None]] | None = None,
        build_post_compact_context: Callable[[], str] | None = None,
        notify: Callable[[str], None] | None = None,
        enable_tool_history_snip: bool = True,
        enable_context_compact: bool = True,
    ):
        self.agent = agent
        self.workspace = workspace
        self.hooks = hooks
        self.summarize_messages = summarize_messages
        self.build_post_compact_context = build_post_compact_context
        self.notify = notify
        self.enable_tool_history_snip = enable_tool_history_snip
        self.enable_context_compact = enable_context_compact

    async def prepare_context_for_provider(self) -> ContextPreparation:
        """Run cheap history cleanup first, then compact only if pressure remains high."""
        snipped = self.snip_tool_history() if self.enable_tool_history_snip else False
        if self.enable_context_compact and await self.should_compact():
            compacted = await self.compact_context(reason="context_pressure")
            if compacted:
                return ContextPreparation(self.agent.conversation, True, "context_compact")
        if snipped:
            return ContextPreparation(self.agent.conversation, True, "tool_history_snip")
        return ContextPreparation(self.agent.conversation, False, "")

    def snip_tool_history(self) -> bool:
        """Replace old rereadable tool results when pressure is high or the session was idle."""
        reason = self._snip_reason()
        if not reason:
            return False

        slots = [
            slot
            for slot in self._message_view().iter_tool_results()
            if slot.content != SNIP_PLACEHOLDER and (not slot.tool_name or slot.tool_name in SNIPPABLE_TOOLS)
        ]
        if len(slots) <= KEEP_RECENT_TOOL_RESULTS:
            return False

        for slot in slots[: len(slots) - KEEP_RECENT_TOOL_RESULTS]:
            slot.set_content(SNIP_PLACEHOLDER)
        return True

    async def should_compact(self) -> bool:
        if self.agent.effective_window <= 1:
            return False
        estimated = estimate_messages_tokens(self.agent.conversation.messages)
        return estimated >= int(self.agent.effective_window * CONTEXT_COMPACT_THRESHOLD)

    async def compact_context(self, *, reason: str = "manual_compact", force: bool = False) -> bool:
        """Summarize old context and keep recent messages verbatim."""
        if not self.enable_context_compact:
            return False
        if not force and not await self.should_compact():
            return False

        try:
            compacted = await self._build_compacted_history()
            if compacted is None:
                return False
            self.agent.conversation.replace(compacted.messages)
            self.agent.last_input_token_count = 0
            self.agent._consecutive_compact_failures = 0
            if self.notify:
                self.notify("Conversation compacted.")
            return True
        except Exception as exc:
            self.agent._consecutive_compact_failures += 1
            if self.agent._consecutive_compact_failures >= MAX_CONSECUTIVE_COMPACT_FAILURES:
                if self.notify:
                    self.notify(
                        f"Compaction failed {MAX_CONSECUTIVE_COMPACT_FAILURES} consecutive times. "
                        "Context may be unrecoverable. Consider using /clear to start fresh."
                    )
                raise
            if self.notify:
                self.notify(f"Compaction skipped (API error: {exc}). Continuing with current context.")
            return False

    async def _build_compacted_history(self) -> ConversationHistory | None:
        messages = self.agent.conversation.messages
        keep_recent_tokens = compact_keep_recent_tokens(self.agent.effective_window)
        if keep_recent_tokens <= 0:
            return None
        cut_index = find_compact_cut_index(messages, keep_recent_tokens)
        if cut_index <= 0:
            return None

        old_messages = list(messages[:cut_index])
        recent_messages = list(messages[cut_index:])
        precompact_context = await self._collect_precompact_context()
        if precompact_context:
            old_messages.append(ConversationMessage(
                role="user",
                content=[TextBlock(f"[PreCompact hook context]\n{precompact_context}")],
            ))

        summary_text = await self._summarize_messages(old_messages)
        if not summary_text:
            return None

        compacted = ConversationHistory([
            ConversationMessage(role="user", content=[TextBlock(f"[Previous context summary]\n{summary_text}")]),
            ConversationMessage(
                role="assistant",
                content=[TextBlock("Understood. I will continue from the compacted context.")],
            ),
            *recent_messages,
        ])
        recovery_context = self.build_post_compact_context() if self.build_post_compact_context else ""
        compacted.append_user_context(recovery_context)
        return compacted

    async def _summarize_messages(self, messages: list[ConversationMessage]) -> str | None:
        history = ConversationHistory(messages)
        if history.count() < 4 or self.summarize_messages is None:
            return None
        return await self.summarize_messages(
            history,
            COMPACT_SYSTEM_PROMPT,
            COMPACT_USER_PROMPT,
            COMPACT_SUMMARY_MAX_TOKENS,
        )

    async def _collect_precompact_context(self) -> str:
        if self.hooks is None:
            return ""
        from .hooks import HookInput

        hook_input = HookInput(
            event="PreCompact",
            session_id=self.agent.session_id,
            cwd=str(self.workspace),
        )
        parts: list[str] = []
        for hook_result in await self.hooks.run("PreCompact", hook_input):
            if hook_result.action == "append_context" and hook_result.content:
                parts.append(str(hook_result.content))
        return "\n\n".join(parts)

    def _snip_reason(self) -> str:
        if self._last_input_utilization() >= SNIP_THRESHOLD:
            return "context_pressure"
        if self.agent.last_api_call_time and (time.time() - self.agent.last_api_call_time) >= SNIP_IDLE_SECONDS:
            return "idle_cleanup"
        return ""

    def _last_input_utilization(self) -> float:
        if not self.agent.effective_window:
            return 0.0
        return float(self.agent.last_input_token_count) / float(self.agent.effective_window)

    def _message_view(self) -> MessageView:
        return MessageView(self.agent.conversation)


def find_compact_cut_index(messages: list[ConversationMessage], keep_recent_tokens: int) -> int:
    """Return an index where recent context starts at a user message."""
    if keep_recent_tokens <= 0:
        return -1

    accumulated = 0
    boundary = max(1, len(messages) - 1)
    for index in range(len(messages) - 1, 0, -1):
        accumulated += estimate_message_tokens(messages[index])
        if accumulated >= keep_recent_tokens:
            boundary = index
            break

    for index in range(boundary, len(messages)):
        if messages[index].role == "user":
            return index
    for index in range(boundary - 1, 0, -1):
        if messages[index].role == "user":
            return index
    return -1


def compact_keep_recent_tokens(effective_window: int) -> int:
    """Return the recent-context budget for compacting.

    The caller owns the model/window configuration. Compressor only applies the
    configured policy: keep the most recent 20% of the effective context window.
    """
    if effective_window <= 1:
        return 0
    return int(effective_window * COMPACT_KEEP_RECENT_RATIO)
