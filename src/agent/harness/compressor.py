"""上下文压缩策略。

四层压缩流水线：
  0. Persist — ToolRuntime 中，大工具结果落盘（对标 Claude Code Level 1）
  1. Budget  — 按字符预算裁剪超长结果
  2. Snip    — 替换陈旧文件读取结果为占位符
  3. Microcompact — 空闲一段时间后清除旧结果

以及两个模型驱动的压缩操作（对标 Claude Code Level 4 & 5）：
  - collapse — 折叠早期上下文，保留近期原文（优先，90% 触发）
  - compact  — 生成对话摘要，重置消息历史（兜底，85% 触发，collapse 启用时被抑制）

变更原因：
  - 改压缩阈值 → 改 types.py 常量
  - 改 compact 摘要格式 → 改 COMPACT_SYSTEM_PROMPT 模板
  - 加新的压缩层 → 加新方法 + 在 run_pipeline 中调用
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from .message_view import MessageView

SNIPPABLE_TOOLS = {"read_file", "grep_search", "list_files", "run_shell", "web_fetch", "write_file", "edit_file"}
SNIP_PLACEHOLDER = "[Content snipped - re-read if needed]"
BUDGET_UTILIZATION_THRESHOLD = 0.5
BUDGET_MEDIUM = 30000
SNIP_THRESHOLD = 0.60
MICROCOMPACT_IDLE_S = 5 * 60
KEEP_RECENT_RESULTS = 3
COMPACT_SUMMARY_MAX_TOKENS = 2048

# ─── Collapse 常量 ───────────────────────────────

COLLAPSE_UTILIZATION_THRESHOLD = 0.90
COLLAPSE_KEEP_RATIO = 0.30
COLLAPSE_MIN_MESSAGES = 8

# ─── Compact 常量 ────────────────────────────────

MAX_CONSECUTIVE_COMPACT_FAILURES = 3
POST_COMPACT_MAX_FILES = 5
POST_COMPACT_MAX_CHARS_PER_FILE = 5000

COMPACT_SYSTEM_PROMPT = """You are a conversation summarizer. Output a structured summary with ALL of the following sections. Each section MUST be populated — never omit a section, write "None" if there is no content.

## 1. Primary Request
The user's explicit requests and intentions.

## 2. Key Technical Concepts
Technologies, frameworks, libraries, and technical concepts discussed.

## 3. Files and Code
Every file examined, modified, or created. Include:
- File path and line numbers where changes were made
- Key code snippets showing what was changed (before/after)
- Files that were read but NOT modified — note them as "examined only"

## 4. Errors and Fixes
Every error encountered and how it was resolved. Include the exact error messages and the fix applied.

## 5. Problem Solving
Problems solved, investigations completed, and investigations still in progress.

## 6. All User Messages
The original text of all user messages (excluding tool results).

## 7. Pending Tasks
Tasks the user requested that are not yet completed.

## 8. Current Work
What was actively being worked on before this compaction. Be the MOST DETAILED here — include exact file paths, line numbers, function names, and the current state of edits.

## 9. Optional Next Step
The most logical next action to continue the work. Include direct quotes or file references from the conversation."""

COMPACT_USER_PROMPT = (
    "Summarize the conversation above using the 9-section structured format. "
    "Be precise: include exact file paths, line numbers, function names, and error messages. "
    "The summary must be self-contained — a new agent reading it should be able to "
    "continue the work without re-reading any files."
)


class Compressor:
    """Agent 的上下文压缩策略实现。

    从 Agent 状态读取消息历史，执行压缩后写回。
    """

    def __init__(
        self,
        agent,
        *,
        summarize_messages: Callable[[list[dict], str, str, int], Awaitable[str | None]] | None = None,
        notify: Callable[[str], None] | None = None,
    ):
        self.agent = agent
        self.summarize_messages = summarize_messages
        self.notify = notify

    @property
    def use_openai(self) -> bool:
        return bool(self.agent.config.use_openai)

    # ─── 压缩流水线 ────────────────────────────────

    def _calc_utilization(self) -> float:
        return self.agent.last_input_token_count / self.agent.effective_window if self.agent.effective_window else 0

    async def run_pipeline(self) -> bool:
        """执行压缩流水线。Collapse 优先，成功则跳过后续层。"""
        util = self._calc_utilization()

        # Level 4: Context Collapse（在 Budget 之前）
        if util >= COLLAPSE_UTILIZATION_THRESHOLD:
            collapsed = await self.collapse_early_context()
            if collapsed:
                return True

        # Level 1-3: 原有逻辑
        self._budget_results()
        self._snip_stale_results()
        self._microcompact()
        return False

    # ─── 共享摘要引擎 ──────────────────────────────

    async def _summarize_messages(self, messages: list[dict]) -> str | None:
        """调模型生成摘要。Compact 和 Collapse 共用此方法。"""
        if len(messages) < 4 or self.summarize_messages is None:
            return None
        return await self.summarize_messages(
            messages,
            COMPACT_SYSTEM_PROMPT,
            COMPACT_USER_PROMPT,
            COMPACT_SUMMARY_MAX_TOKENS,
        )

    # ─── Level 4: Context Collapse ──────────────────

    async def collapse_early_context(self) -> bool:
        """折叠早期消息，保留近期原文。与 Autocompact 共用 _summarize_messages。"""
        if self.use_openai:
            msgs = self.agent._openai_messages
            if msgs and msgs[0].get("role") == "system":
                system, rest = msgs[0], msgs[1:]
            else:
                system, rest = None, msgs
        else:
            rest = self.agent._anthropic_messages
            system = None

        if len(rest) < COLLAPSE_MIN_MESSAGES:
            return False

        split = max(len(rest) - int(len(rest) * COLLAPSE_KEEP_RATIO), COLLAPSE_MIN_MESSAGES // 2)
        early = rest[:split]
        recent = rest[split:]

        summary = await self._summarize_messages(early)
        if not summary:
            return False

        collapsed_msg = {"role": "user", "content": f"[Collapsed early context]\n{summary}"}

        if self.use_openai:
            rebuilt: list[dict] = [system] if system else []
            rebuilt.append(collapsed_msg)
            rebuilt.extend(recent)
            self.agent._openai_messages = rebuilt
        else:
            self.agent._anthropic_messages = [collapsed_msg, *recent]

        self.agent.last_input_token_count = 0
        return True

    # ─── Level 5: Compact（兜底） ───────────────────

    async def compact_conversation(self) -> None:
        """生成对话摘要，重置消息历史。"""
        await self._run_precompact_hooks()

        try:
            compacted = await (self._compact_openai() if self.use_openai else self._compact_anthropic())
            if compacted:
                self.agent._consecutive_compact_failures = 0
                self._restore_recent_files()
                self._reattach_active_skills()
            if self.notify:
                self.notify("Conversation compacted.")
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

    # ─── 第 1 层：Budget — 裁剪超长结果 ─────────────

    def _budget_results(self) -> None:
        utilization = self.agent.last_input_token_count / self.agent.effective_window if self.agent.effective_window else 0
        if utilization < BUDGET_UTILIZATION_THRESHOLD:
            return
        budget = BUDGET_MEDIUM

        for slot in self._message_view().iter_tool_results():
            if len(slot.content) <= budget or "<persisted-output>" in slot.content:
                continue
            keep = (budget - 80) // 2
            slot.set_content(
                slot.content[:keep]
                + f"\n\n[... budgeted: {len(slot.content) - keep * 2} chars truncated ...]\n\n"
                + slot.content[-keep:]
            )

    # ─── 第 2 层：Snip — 替换陈旧结果 ───────────────

    def _snip_stale_results(self) -> None:
        utilization = self.agent.last_input_token_count / self.agent.effective_window if self.agent.effective_window else 0
        if utilization < SNIP_THRESHOLD:
            return

        results = [
            slot for slot in self._message_view().iter_tool_results()
            if slot.content != SNIP_PLACEHOLDER and (not slot.tool_name or slot.tool_name in SNIPPABLE_TOOLS)
        ]
        if len(results) <= KEEP_RECENT_RESULTS:
            return
        for slot in results[: len(results) - KEEP_RECENT_RESULTS]:
            slot.set_content(SNIP_PLACEHOLDER)

    # ─── 第 3 层：Microcompact — 空闲后清除 ─────────

    def _microcompact(self) -> None:
        if not self.agent.last_api_call_time or (time.time() - self.agent.last_api_call_time) < MICROCOMPACT_IDLE_S:
            return

        results = [
            slot for slot in self._message_view().iter_tool_results()
            if slot.content not in (SNIP_PLACEHOLDER, "[Old result cleared]")
        ]
        for slot in results[: max(0, len(results) - KEEP_RECENT_RESULTS)]:
            slot.set_content("[Old result cleared]")

    # ─── Compact helpers ────────────────────────────

    async def _run_precompact_hooks(self) -> None:
        """运行 PreCompact hooks，将输出注入摘要上下文。"""
        from .hooks import HookInput
        if self.agent._hook_manager is None:
            return
        hook_input = HookInput(
            event="PreCompact",
            session_id=self.agent.session_id,
            cwd=str(self.agent.config.workspace),
        )
        for hook_result in await self.agent._hook_manager.run("PreCompact", hook_input):
            if hook_result.action == "append_context" and hook_result.content:
                self.agent.append_user_context(hook_result.content)

    def _restore_recent_files(self) -> None:
        """压缩后恢复最近读取的文件，防止模型丢失关键上下文。对标 Claude Code Level 5。"""
        if not self.agent._read_file_state:
            return
        recent = sorted(
            self.agent._read_file_state.items(),
            key=lambda x: x[1], reverse=True,
        )[:POST_COMPACT_MAX_FILES]
        for path, _ in recent:
            try:
                content = Path(path).read_text()
                if len(content) > POST_COMPACT_MAX_CHARS_PER_FILE:
                    content = content[:POST_COMPACT_MAX_CHARS_PER_FILE] + "\n... [truncated]"
                self.agent.append_user_context(
                    f"[Restored after compaction]\nFile: {path}\n```\n{content}\n```"
                )
            except Exception:
                pass

    async def _compact_anthropic(self) -> bool:
        """Level 5: Anthropic 后端 Compact。"""
        if len(self.agent._anthropic_messages) < 4:
            return False
        last_user_msg = self.agent._anthropic_messages[-1]

        summary_text = await self._summarize_messages(self.agent._anthropic_messages[:-1])
        if not summary_text:
            return False

        self.agent._anthropic_messages = [
            {"role": "user", "content": f"[Previous conversation summary]\n{summary_text}"},
            {"role": "assistant", "content": "Understood. I have the context from our previous conversation. How can I continue helping?"},
        ]
        if last_user_msg.get("role") == "user":
            self.agent._anthropic_messages.append(last_user_msg)
        self.agent.last_input_token_count = 0
        return True

    async def _compact_openai(self) -> bool:
        """Level 5: OpenAI 后端 Compact。"""
        if len(self.agent._openai_messages) < 5:
            return False
        system_msg = self.agent._openai_messages[0]
        last_user_msg = self.agent._openai_messages[-1]

        summary_text = await self._summarize_messages(self.agent._openai_messages[1:-1])
        if not summary_text:
            return False

        self.agent._openai_messages = [
            system_msg,
            {"role": "user", "content": f"[Previous conversation summary]\n{summary_text}"},
            {"role": "assistant", "content": "Understood. I have the context from our previous conversation. How can I continue helping?"},
        ]
        if last_user_msg.get("role") == "user":
            self.agent._openai_messages.append(last_user_msg)
        self.agent.last_input_token_count = 0
        return True

    def _reattach_active_skills(self) -> None:
        if self.agent._active_skills is None:
            return
        context = self.agent._active_skills.build_context()
        if not context:
            return
        self.agent.append_user_context(context)

    def _message_view(self) -> MessageView:
        return MessageView(
            self.agent._openai_messages if self.use_openai else self.agent._anthropic_messages,
            use_openai=self.use_openai,
        )
