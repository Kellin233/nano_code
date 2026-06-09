"""上下文压缩策略。

三层压缩流水线：
  1. Budget — 按字符预算裁剪超长工具结果
  2. Snip   — 替换陈旧文件读取结果为占位符
  3. Microcompact — 空闲一段时间后清除旧结果

以及 compact 操作——通过调用模型生成对话摘要，压缩消息历史。

与原 agent/context.py 的区别：
  原版对 Anthropic 和 OpenAI 分别写了 6 对方法（budget×2 + snip×2 + microcompact×2）。
  本文件通过 agent.messages 统一操作消息列表，snip 阶段通过策略差异分别处理。
"""

from __future__ import annotations

import time

from ..capabilities.tools.types import (
    BUDGET_HIGH,
    BUDGET_HIGH_UTILIZATION,
    BUDGET_MEDIUM,
    BUDGET_UTILIZATION_THRESHOLD,
    COMPACT_SUMMARY_MAX_TOKENS,
    COMPACT_UTILIZATION_THRESHOLD,
    KEEP_RECENT_RESULTS,
    MICROCOMPACT_IDLE_S,
    SNIP_THRESHOLD,
)

SNIPPABLE_TOOLS = {"read_file", "grep_search", "list_files", "run_shell"}
SNIP_PLACEHOLDER = "[Content snipped - re-read if needed]"


class Compressor:
    """Agent 的上下文压缩策略实现。

    从 Agent 状态读取消息历史，执行压缩后写回。
    """

    def __init__(self, agent):
        self.agent = agent

    @property
    def use_openai(self) -> bool:
        return self.agent.config.use_openai

    # ─── 压缩流水线 ────────────────────────────────

    def run_pipeline(self) -> None:
        """按顺序执行三层压缩。"""
        self._budget_results()
        self._snip_stale_results()
        self._microcompact()

    async def compact_conversation(self) -> None:
        """生成对话摘要，重置消息历史。"""
        from ..tui.renderer import get_renderer
        try:
            if self.use_openai:
                compacted = await self._compact_openai()
            else:
                compacted = await self._compact_anthropic()
            if compacted:
                self._reattach_active_skills()
            get_renderer().info("Conversation compacted.")
        except Exception as exc:
            get_renderer().info(f"Compaction skipped (API error: {exc}). Continuing with current context.")

    # ─── 第 1 层：Budget — 裁剪超长结果 ─────────────

    def _budget_results(self) -> None:
        utilization = self.agent.last_input_token_count / self.agent.effective_window if self.agent.effective_window else 0
        if utilization < BUDGET_UTILIZATION_THRESHOLD:
            return
        budget = BUDGET_HIGH if utilization > BUDGET_HIGH_UTILIZATION else BUDGET_MEDIUM

        if self.use_openai:
            for msg in self.agent._openai_messages:
                if msg.get("role") == "tool" and isinstance(msg.get("content"), str) and len(msg["content"]) > budget:
                    keep = (budget - 80) // 2
                    msg["content"] = (
                        msg["content"][:keep]
                        + f"\n\n[... budgeted: {len(msg['content']) - keep * 2} chars truncated ...]\n\n"
                        + msg["content"][-keep:]
                    )
        else:
            for msg in self.agent._anthropic_messages:
                if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                    continue
                for block in msg["content"]:
                    if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("content"), str) and len(block["content"]) > budget:
                        keep = (budget - 80) // 2
                        block["content"] = (
                            block["content"][:keep]
                            + f"\n\n[... budgeted: {len(block['content']) - keep * 2} chars truncated ...]\n\n"
                            + block["content"][-keep:]
                        )

    # ─── 第 2 层：Snip — 替换陈旧结果 ───────────────

    def _snip_stale_results(self) -> None:
        utilization = self.agent.last_input_token_count / self.agent.effective_window if self.agent.effective_window else 0
        if utilization < SNIP_THRESHOLD:
            return

        if self.use_openai:
            self._snip_openai()
        else:
            self._snip_anthropic()

    def _snip_openai(self) -> None:
        tool_msgs = []
        for i, msg in enumerate(self.agent._openai_messages):
            if msg.get("role") == "tool" and isinstance(msg.get("content"), str) and msg["content"] != SNIP_PLACEHOLDER:
                tool_msgs.append(i)
        if len(tool_msgs) <= KEEP_RECENT_RESULTS:
            return
        snip_count = len(tool_msgs) - KEEP_RECENT_RESULTS
        for i in range(snip_count):
            self.agent._openai_messages[tool_msgs[i]]["content"] = SNIP_PLACEHOLDER

    def _snip_anthropic(self) -> None:
        # 构建 tool_use_id → {name, input} 索引
        tool_use_index: dict[str, dict] = {}
        for msg in self.agent._anthropic_messages:
            if msg.get("role") != "assistant" or not isinstance(msg.get("content"), list):
                continue
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_use_index[block["id"]] = {
                        "name": block["name"],
                        "input": block.get("input", {}),
                    }

        results = []
        for mi, msg in enumerate(self.agent._anthropic_messages):
            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue
            for bi, block in enumerate(msg["content"]):
                if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("content"), str) and block["content"] != SNIP_PLACEHOLDER:
                    tool_use_id = block.get("tool_use_id")
                    tool_info = tool_use_index.get(tool_use_id or "")
                    if tool_info and tool_info["name"] in SNIPPABLE_TOOLS:
                        results.append({"mi": mi, "bi": bi, "name": tool_info["name"], "file_path": tool_info.get("input", {}).get("file_path")})

        if len(results) <= KEEP_RECENT_RESULTS:
            return

        to_snip = set()
        seen_files: dict[str, list[int]] = {}
        for i, result in enumerate(results):
            if result["name"] == "read_file" and result.get("file_path"):
                seen_files.setdefault(result["file_path"], []).append(i)

        for indices in seen_files.values():
            if len(indices) > 1:
                for j in indices[:-1]:
                    to_snip.add(j)

        snip_before = len(results) - KEEP_RECENT_RESULTS
        for i in range(snip_before):
            to_snip.add(i)

        for idx in to_snip:
            result = results[idx]
            self.agent._anthropic_messages[result["mi"]]["content"][result["bi"]]["content"] = SNIP_PLACEHOLDER

    # ─── 第 3 层：Microcompact — 空闲后清除 ─────────

    def _microcompact(self) -> None:
        if not self.agent.last_api_call_time or (time.time() - self.agent.last_api_call_time) < MICROCOMPACT_IDLE_S:
            return

        if self.use_openai:
            tool_msgs = []
            for i, msg in enumerate(self.agent._openai_messages):
                if msg.get("role") == "tool" and isinstance(msg.get("content"), str) and msg["content"] not in (SNIP_PLACEHOLDER, "[Old result cleared]"):
                    tool_msgs.append(i)
            clear_count = len(tool_msgs) - KEEP_RECENT_RESULTS
            for i in range(max(0, clear_count)):
                self.agent._openai_messages[tool_msgs[i]]["content"] = "[Old result cleared]"
        else:
            all_results = []
            for mi, msg in enumerate(self.agent._anthropic_messages):
                if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                    continue
                for bi, block in enumerate(msg["content"]):
                    if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("content"), str) and block["content"] not in (SNIP_PLACEHOLDER, "[Old result cleared]"):
                        all_results.append((mi, bi))
            clear_count = len(all_results) - KEEP_RECENT_RESULTS
            for i in range(max(0, clear_count)):
                mi, bi = all_results[i]
                self.agent._anthropic_messages[mi]["content"][bi]["content"] = "[Old result cleared]"

    # ─── Compact — 对话摘要 ────────────────────────

    async def _compact_anthropic(self) -> bool:
        if len(self.agent._anthropic_messages) < 4:
            return False
        last_user_msg = self.agent._anthropic_messages[-1]

        import anthropic
        kwargs: dict = {"api_key": self.agent.config.api_key}
        if self.agent.config.anthropic_base_url:
            kwargs["base_url"] = self.agent.config.anthropic_base_url
        client = anthropic.AsyncAnthropic(**kwargs)

        summary_resp = await client.messages.create(
            model=self.agent.model,
            max_tokens=COMPACT_SUMMARY_MAX_TOKENS,
            system="You are a conversation summarizer. Be concise but preserve important details.",
            messages=[
                *self.agent._anthropic_messages[:-1],
                {"role": "user", "content": "Summarize the conversation so far in a concise paragraph, preserving key decisions, file paths, and context needed to continue the work."},
            ],
        )
        summary_text = summary_resp.content[0].text if summary_resp.content and summary_resp.content[0].type == "text" else "No summary available."
        self.agent._anthropic_messages = [
            {"role": "user", "content": f"[Previous conversation summary]\n{summary_text}"},
            {"role": "assistant", "content": "Understood. I have the context from our previous conversation. How can I continue helping?"},
        ]
        if last_user_msg.get("role") == "user":
            self.agent._anthropic_messages.append(last_user_msg)
        self.agent.last_input_token_count = 0
        return True

    async def _compact_openai(self) -> bool:
        if len(self.agent._openai_messages) < 5:
            return False
        system_msg = self.agent._openai_messages[0]
        last_user_msg = self.agent._openai_messages[-1]

        from ..backend.openai import OpenAIBackend
        client = OpenAIBackend(
            api_key=self.agent.config.api_key or "",
            base_url=self.agent.config.api_base or "",
            model=self.agent.model,
        ).client

        summary_resp = await client.chat.completions.create(
            model=self.agent.model,
            messages=[
                {"role": "system", "content": "You are a conversation summarizer. Be concise but preserve important details."},
                *self.agent._openai_messages[1:-1],
                {"role": "user", "content": "Summarize the conversation so far in a concise paragraph, preserving key decisions, file paths, and context needed to continue the work."},
            ],
        )
        summary_text = summary_resp.choices[0].message.content or "No summary available."
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
        context = self.agent._active_skills.build_context()
        if not context:
            return
        self.agent.append_user_context(context)
