"""Agent 上下文管理。

本模块负责“模型下一次能看到什么”。Agent 的真实能力不只取决于工具，
还取决于消息历史里保留了哪些上下文、哪些旧结果被裁剪、哪些记忆被注入。

主要职责：
- 启动并消费跨会话记忆预取，把相关记忆追加到当前用户消息。
- 在上下文窗口接近上限时执行自动 compact。
- 对工具结果做分层压缩：预算裁剪、snip 陈旧结果、microcompact 清理旧结果。
- compact 后重新挂载仍处于激活状态的 skill prompt。

边界：
- 不直接调用主模型生成最终回答；模型循环在 `backends.py`。
- 不真正执行工具；工具路由在 `tools_runtime.py`。
- 这里只维护 Anthropic / OpenAI 两种消息历史的合法形状。
"""

from __future__ import annotations

import time

from ..memory import MemoryPrefetch, format_memories_for_injection, start_memory_prefetch
from ..ui import print_info


# ─── 多层压缩常量 ───────────────────────────────

SNIPPABLE_TOOLS = {"read_file", "grep_search", "list_files", "run_shell"}
SNIP_PLACEHOLDER = "[Content snipped - re-read if needed]"
SNIP_THRESHOLD = 0.60
MICROCOMPACT_IDLE_S = 5 * 60  # 5 分钟
KEEP_RECENT_RESULTS = 3


class AgentContextMixin:
    """给 `Agent` 增加上下文管理能力。

    依赖 `Agent` 上的状态：
    `_anthropic_messages`、`_openai_messages`、`_active_skills`、
    `_already_surfaced_memories`、`_session_memory_bytes`、`last_input_token_count`。

    提供给后端循环使用的方法：
    `_start_memory_prefetch()`、`_consume_memory_prefetch()`、
    `_check_and_compact()`、`_run_compression_pipeline()`。
    """

    # ─── 记忆召回 ────────────────────────────────────

    def _build_side_query(self):
        """构建用于记忆召回的旁路查询可调用对象，兼容两种后端。"""
        if self._anthropic_client:
            client = self._anthropic_client
            model = self.model

            async def _sq(system: str, user_message: str) -> str:
                resp = await client.messages.create(
                    model=model, max_tokens=256, system=system,
                    messages=[{"role": "user", "content": user_message}],
                )
                return "".join(b.text for b in resp.content if b.type == "text")

            return _sq
        if self._openai_client:
            client = self._openai_client
            model = self.model

            async def _sq_oai(system: str, user_message: str) -> str:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_message},
                    ],
                )
                if not resp.choices:
                    return ""
                return resp.choices[0].message.content or ""

            return _sq_oai
        return None

    def _start_memory_prefetch(self, user_message: str) -> MemoryPrefetch | None:
        """在用户回合开始时启动异步记忆召回。

        子智能体不触发记忆系统，避免 fork 任务污染主会话的记忆预算。
        """
        if self.is_sub_agent:
            return None
        side_query = self._build_side_query()
        if not side_query:
            return None
        return start_memory_prefetch(
            user_message,
            side_query,
            self._already_surfaced_memories,
            self._session_memory_bytes,
        )

    def _consume_memory_prefetch(self, memory_prefetch: MemoryPrefetch | None) -> None:
        """非阻塞消费记忆预取结果，并注入到当前最后一条用户消息。"""
        if not memory_prefetch or not memory_prefetch.settled or memory_prefetch.consumed:
            return

        memory_prefetch.consumed = True
        try:
            memories = memory_prefetch.task.result()
            if not memories:
                return

            injection_text = format_memories_for_injection(memories)
            self._append_user_context(injection_text)
            for memory in memories:
                self._already_surfaced_memories.add(memory.path)
                self._session_memory_bytes += len(memory.content.encode())
        except Exception:
            pass  # 预取错误已在 memory.py 中记录；主循环不应因此中断。

    def _append_user_context(self, text: str) -> None:
        """把系统补充上下文追加到最新用户消息，保持消息角色交替合法。"""
        if self.use_openai:
            last = self._openai_messages[-1] if self._openai_messages else None
            if last and last.get("role") == "user":
                last["content"] = (last.get("content") or "") + "\n\n" + text
            else:
                self._openai_messages.append({"role": "user", "content": text})
            return

        last = self._anthropic_messages[-1] if self._anthropic_messages else None
        if last and last.get("role") == "user":
            content = last.get("content", "")
            if isinstance(content, str):
                last["content"] = content + "\n\n" + text
            elif isinstance(content, list):
                content.append({"type": "text", "text": text})
            else:
                last["content"] = text
        else:
            self._anthropic_messages.append({"role": "user", "content": text})

    # ─── 自动压缩 ─────────────────────────────────────

    async def compact(self) -> None:
        await self._compact_conversation()

    async def _check_and_compact(self) -> None:
        if self.last_input_token_count > self.effective_window * 0.85:
            print_info("Context window filling up, compacting conversation...")
            await self._compact_conversation()

    async def _compact_conversation(self) -> None:
        if self.use_openai:
            compacted = await self._compact_openai()
        else:
            compacted = await self._compact_anthropic()
        if compacted:
            self._reattach_active_skills()
        print_info("Conversation compacted.")

    async def _compact_anthropic(self) -> bool:
        # 不变量：调用方必须保证最后一条消息是普通用户文本消息
        # （不是工具结果）。否则切片会打断 tool_use ↔ tool_result 配对。
        if len(self._anthropic_messages) < 4:
            return False
        last_user_msg = self._anthropic_messages[-1]
        summary_resp = await self._anthropic_client.messages.create(
            model=self.model,
            max_tokens=2048,
            system="You are a conversation summarizer. Be concise but preserve important details.",
            messages=[
                *self._anthropic_messages[:-1],
                {"role": "user", "content": "Summarize the conversation so far in a concise paragraph, preserving key decisions, file paths, and context needed to continue the work."},
            ],
        )
        summary_text = summary_resp.content[0].text if summary_resp.content and summary_resp.content[0].type == "text" else "No summary available."
        self._anthropic_messages = [
            {"role": "user", "content": f"[Previous conversation summary]\n{summary_text}"},
            {"role": "assistant", "content": "Understood. I have the context from our previous conversation. How can I continue helping?"},
        ]
        if last_user_msg.get("role") == "user":
            self._anthropic_messages.append(last_user_msg)
        self.last_input_token_count = 0
        return True

    async def _compact_openai(self) -> bool:
        # OpenAI 工具消息同样要求 assistant.tool_calls 与 role=tool 成对出现。
        if len(self._openai_messages) < 5:
            return False
        system_msg = self._openai_messages[0]
        last_user_msg = self._openai_messages[-1]
        summary_resp = await self._openai_client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "You are a conversation summarizer. Be concise but preserve important details."},
                *self._openai_messages[1:-1],
                {"role": "user", "content": "Summarize the conversation so far in a concise paragraph, preserving key decisions, file paths, and context needed to continue the work."},
            ],
        )
        summary_text = summary_resp.choices[0].message.content or "No summary available."
        self._openai_messages = [
            system_msg,
            {"role": "user", "content": f"[Previous conversation summary]\n{summary_text}"},
            {"role": "assistant", "content": "Understood. I have the context from our previous conversation. How can I continue helping?"},
        ]
        if last_user_msg.get("role") == "user":
            self._openai_messages.append(last_user_msg)
        self.last_input_token_count = 0
        return True

    def _reattach_active_skills(self) -> None:
        context = self._active_skills.build_context()
        if not context:
            return
        self._append_user_context(context)

    # ─── 多层压缩流水线 ───────────────────────────────

    def _run_compression_pipeline(self) -> None:
        if self.use_openai:
            self._budget_tool_results_openai()
            self._snip_stale_results_openai()
            self._microcompact_openai()
        else:
            self._budget_tool_results_anthropic()
            self._snip_stale_results_anthropic()
            self._microcompact_anthropic()

    # 第 1 层：上下文压力升高时，先按字符预算裁剪超长工具结果。
    def _budget_tool_results_anthropic(self) -> None:
        utilization = self.last_input_token_count / self.effective_window if self.effective_window else 0
        if utilization < 0.5:
            return
        budget = 15000 if utilization > 0.7 else 30000
        for msg in self._anthropic_messages:
            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("content"), str) and len(block["content"]) > budget:
                    keep = (budget - 80) // 2
                    block["content"] = block["content"][:keep] + f"\n\n[... budgeted: {len(block['content']) - keep * 2} chars truncated ...]\n\n" + block["content"][-keep:]

    def _budget_tool_results_openai(self) -> None:
        utilization = self.last_input_token_count / self.effective_window if self.effective_window else 0
        if utilization < 0.5:
            return
        budget = 15000 if utilization > 0.7 else 30000
        for msg in self._openai_messages:
            if msg.get("role") == "tool" and isinstance(msg.get("content"), str) and len(msg["content"]) > budget:
                keep = (budget - 80) // 2
                msg["content"] = msg["content"][:keep] + f"\n\n[... budgeted: {len(msg['content']) - keep * 2} chars truncated ...]\n\n" + msg["content"][-keep:]

    # 第 2 层：保留工具调用元数据，裁掉陈旧结果正文。
    def _snip_stale_results_anthropic(self) -> None:
        utilization = self.last_input_token_count / self.effective_window if self.effective_window else 0
        if utilization < SNIP_THRESHOLD:
            return

        results = []
        for mi, msg in enumerate(self._anthropic_messages):
            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue
            for bi, block in enumerate(msg["content"]):
                if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("content"), str) and block["content"] != SNIP_PLACEHOLDER:
                    tool_use_id = block.get("tool_use_id")
                    tool_info = self._find_tool_use_by_id(tool_use_id)
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
            self._anthropic_messages[result["mi"]]["content"][result["bi"]]["content"] = SNIP_PLACEHOLDER

    def _snip_stale_results_openai(self) -> None:
        utilization = self.last_input_token_count / self.effective_window if self.effective_window else 0
        if utilization < SNIP_THRESHOLD:
            return
        tool_msgs = []
        for i, msg in enumerate(self._openai_messages):
            if msg.get("role") == "tool" and isinstance(msg.get("content"), str) and msg["content"] != SNIP_PLACEHOLDER:
                tool_msgs.append(i)
        if len(tool_msgs) <= KEEP_RECENT_RESULTS:
            return
        snip_count = len(tool_msgs) - KEEP_RECENT_RESULTS
        for i in range(snip_count):
            self._openai_messages[tool_msgs[i]]["content"] = SNIP_PLACEHOLDER

    # 第 3 层：空闲一段时间后进一步清理旧结果，给后续回合留余量。
    def _microcompact_anthropic(self) -> None:
        if not self.last_api_call_time or (time.time() - self.last_api_call_time) < MICROCOMPACT_IDLE_S:
            return
        all_results = []
        for mi, msg in enumerate(self._anthropic_messages):
            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue
            for bi, block in enumerate(msg["content"]):
                if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("content"), str) and block["content"] not in (SNIP_PLACEHOLDER, "[Old result cleared]"):
                    all_results.append((mi, bi))
        clear_count = len(all_results) - KEEP_RECENT_RESULTS
        for i in range(max(0, clear_count)):
            mi, bi = all_results[i]
            self._anthropic_messages[mi]["content"][bi]["content"] = "[Old result cleared]"

    def _microcompact_openai(self) -> None:
        if not self.last_api_call_time or (time.time() - self.last_api_call_time) < MICROCOMPACT_IDLE_S:
            return
        tool_msgs = []
        for i, msg in enumerate(self._openai_messages):
            if msg.get("role") == "tool" and isinstance(msg.get("content"), str) and msg["content"] not in (SNIP_PLACEHOLDER, "[Old result cleared]"):
                tool_msgs.append(i)
        clear_count = len(tool_msgs) - KEEP_RECENT_RESULTS
        for i in range(max(0, clear_count)):
            self._openai_messages[tool_msgs[i]]["content"] = "[Old result cleared]"

    def _find_tool_use_by_id(self, tool_use_id: str) -> dict | None:
        for msg in self._anthropic_messages:
            if msg.get("role") != "assistant" or not isinstance(msg.get("content"), list):
                continue
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id") == tool_use_id:
                    return {"name": block["name"], "input": block.get("input", {})}
        return None
