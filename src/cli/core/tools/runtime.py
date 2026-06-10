"""Runtime entrypoint for built-in tool execution."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ....agent.harness.hooks import HookInput, HookManager
from ....agent.harness.permissions import check_permission
from .builtin import edit_file, grep_search, list_files, read_file, web_fetch, write_file
from .registry import ToolRegistry
from .types import (
    DEFAULT_MAX_RESULT_CHARS,
    DEFAULT_SHELL_TIMEOUT_MS,
    MAX_RESULT_CHARS,
    TOOL_RESULT_CHAR_LIMITS,
    PermissionMode,
    ToolCall,
    ToolContext,
    ToolResult,
)


def _truncate_result(result: str) -> str:
    if len(result) <= MAX_RESULT_CHARS:
        return result
    keep_each = (MAX_RESULT_CHARS - 60) // 2
    return (
        result[:keep_each]
        + f"\n\n[... truncated {len(result) - keep_each * 2} chars ...]\n\n"
        + result[-keep_each:]
    )


ConfirmFn = Callable[[str], Awaitable[bool]]
EventCallback = Callable[[Any], Awaitable[None]]
BeforeToolCall = Callable[[ToolCall], Awaitable[None] | None]
AfterToolCall = Callable[[ToolCall, ToolResult], Awaitable[None] | None]


class ToolRuntime:
    """Unified tool execution pipeline."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        permission_mode: PermissionMode = "default",
        confirm_fn: ConfirmFn | None = None,
        confirmed: set[str] | None = None,
        hooks: HookManager | None = None,
        event_callback: EventCallback | None = None,
        agent: Any = None,
        before_tool_call: BeforeToolCall | None = None,
        after_tool_call: AfterToolCall | None = None,
    ):
        self.registry = registry
        self.permission_mode = permission_mode
        self.confirm_fn = confirm_fn
        self.confirmed = confirmed if confirmed is not None else set()
        self.hooks = hooks or HookManager()
        self.event_callback = event_callback
        self._agent = agent  # 用于 _persist_large_result 访问 _tool_results_dir
        self.before_tool_call = before_tool_call
        self.after_tool_call = after_tool_call

    async def execute_many(
        self,
        calls: list[ToolCall],
        ctx: ToolContext,
    ) -> list[tuple[ToolCall, ToolResult]]:
        batches: list[dict] = []
        for call in calls:
            safe = self.registry.is_concurrency_safe(call.name, call.input)
            if safe and batches and batches[-1]["concurrent"]:
                batches[-1]["items"].append(call)
            else:
                batches.append({"concurrent": safe, "items": [call]})

        results: list[tuple[ToolCall, ToolResult]] = []
        for batch in batches:
            if batch["concurrent"]:
                batch_results = await self._execute_concurrent(batch["items"], ctx)
                results.extend(batch_results)
                continue
            for call in batch["items"]:
                results.append((call, await self.execute_one(call, ctx)))
        return results

    async def _execute_concurrent(
        self,
        calls: list[ToolCall],
        ctx: ToolContext,
    ) -> list[tuple[ToolCall, ToolResult]]:
        async def _run(call: ToolCall) -> tuple[ToolCall, ToolResult]:
            return call, await self.execute_one(call, ctx)

        return await asyncio.gather(*[_run(call) for call in calls])

    async def execute_one(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        tool = self.registry.find(call.name)
        if tool is None:
            return ToolResult(f"Unknown tool: {call.name}", is_error=True)

        validation = await tool.validate(call.input, ctx)
        if not validation.ok:
            return ToolResult(f"Error: {validation.message}", is_error=True)
        inp = validation.updated_input or call.input
        call = ToolCall(id=call.id, name=call.name, input=inp, provider=call.provider)

        if self.before_tool_call:
            hook_result = self.before_tool_call(call)
            if hasattr(hook_result, "__await__"):
                await hook_result  # type: ignore[misc]

        hook_input = HookInput(
            event="PreToolUse",
            session_id=ctx.session_id,
            cwd=str(ctx.cwd),
            tool_name=call.name,
            tool_input=inp,
        )
        for hook_result in await self.hooks.run("PreToolUse", hook_input):
            if hook_result.action == "deny":
                reason = hook_result.reason or hook_result.error or "denied by hook"
                return ToolResult(f"Action denied by hook: {reason}", is_error=True)
            if hook_result.action == "modify" and hook_result.updated_input is not None:
                inp = hook_result.updated_input
                # 每次 hook 修改输入后重新校验，防止恶意/错误 hook 绕过参数约束。
                revalidation = await tool.validate(inp, ctx)
                if not revalidation.ok:
                    return ToolResult(
                        f"Error: hook-modified input failed validation: {revalidation.message}",
                        is_error=True,
                    )

        metadata = self.registry.metadata_for(call.name)
        decision = check_permission(
            call.name,
            inp,
            mode=self.permission_mode,
            metadata=metadata,
            cwd=ctx.cwd,
        )
        if decision.action == "deny":
            return ToolResult(f"Action denied: {decision.message}", is_error=True)
        if decision.action == "confirm" and decision.message and decision.message not in self.confirmed:
            if self.event_callback:
                from ....agent.events import PermissionRequested

                await self.event_callback(PermissionRequested(call, decision.message))
            confirmed = await self._confirm(decision.message)
            if not confirmed:
                return ToolResult("User denied this action.", is_error=True)
            self.confirmed.add(decision.message)

        result = await tool.call(inp, ctx)
        result = self._persist_large_result(call.name, call.id, result)

        if self.after_tool_call:
            hook_result = self.after_tool_call(call, result)
            if hasattr(hook_result, "__await__"):
                await hook_result  # type: ignore[misc]

        post_input = HookInput(
            event="PostToolUse",
            session_id=ctx.session_id,
            cwd=str(ctx.cwd),
            tool_name=call.name,
            tool_input=inp,
            tool_result={
                "content": result.content,
                "is_error": result.is_error,
                "metadata": result.metadata,
            },
        )
        for hook_result in await self.hooks.run("PostToolUse", post_input):
            if hook_result.action == "append_context" and hook_result.content:
                result.extra_messages.append({"role": "user", "content": hook_result.content})
        return result

    async def _confirm(self, message: str) -> bool:
        if not self.confirm_fn:
            return False
        return await self.confirm_fn(message)

    def _persist_large_result(self, tool_name: str, call_id: str, result: ToolResult) -> ToolResult:
        """对标 Claude Code：超大工具结果落盘 + <persisted-output> 预览。"""
        limit = TOOL_RESULT_CHAR_LIMITS.get(tool_name, DEFAULT_MAX_RESULT_CHARS)
        text = result.content
        if len(text) <= limit:
            return result

        # 落盘路径对标 Claude Code: {workspace}/.nanocode/sessions/{id}/tool-results/{call_id}.txt
        output_dir = self._agent._tool_results_dir if self._agent else Path.home() / ".nanocode" / "tool-results"
        output_dir.mkdir(parents=True, exist_ok=True)
        filepath = output_dir / f"{call_id}.txt"
        filepath.write_text(text, encoding="utf-8")

        size_kb = len(text.encode()) / 1024
        preview_chars = min(2000, len(text))
        preview = text[:preview_chars]
        trunc_note = "\n... [truncated]" if len(text) > preview_chars else ""

        result.content = (
            "<persisted-output>\n"
            f"Output too large ({size_kb:.1f} KB). "
            f"Full output saved to: {filepath}\n\n"
            f"Preview (first {preview_chars // 1000}.0 KB):\n"
            f"{preview}{trunc_note}\n"
            "</persisted-output>"
        )
        result.metadata["full_result_path"] = str(filepath)
        result.metadata["original_size"] = len(text)

        # 记录替换哈希，用于会话恢复时重放替换决策
        if self._agent:
            self._agent._result_replacements[call_id] = hashlib.sha256(text.encode()).hexdigest()

        return result


# 内置工具 handler 映射。注意 run_shell 已从此字典移除：
# 所有 run_shell 执行路径（execute_builtin_tool / ToolRegistry._call_builtin）
# 都要求显式传入 sandbox manager 或 execution_backend，禁止回退到裸 shell。
BUILTIN_HANDLERS = {
    "write_file": write_file,
    "edit_file": edit_file,
    "list_files": list_files,
    "grep_search": grep_search,
    "web_fetch": web_fetch,
}


async def execute_builtin_tool(
    name: str,
    inp: dict,
    read_file_state: dict[str, float] | None = None,
    execution_backend: Any | None = None,
) -> str:
    if name == "read_file":
        result = read_file(inp)
        if read_file_state is not None and not result.startswith("Error"):
            abs_path = str(Path(inp["file_path"]).resolve())
            with contextlib.suppress(OSError):
                read_file_state[abs_path] = os.path.getmtime(abs_path)
        return _truncate_result(result)

    if name in ("write_file", "edit_file") and read_file_state is not None:
        abs_path = str(Path(inp["file_path"]).resolve())
        if os.path.exists(abs_path):
            if abs_path not in read_file_state:
                verb = "writing" if name == "write_file" else "editing"
                return f"Error: You must read this file before {verb}. Use read_file first to see its current contents."
            if os.path.getmtime(abs_path) != read_file_state[abs_path]:
                verb = "writing" if name == "write_file" else "editing"
                return f"Warning: {inp['file_path']} was modified externally since your last read. Please read_file again before {verb}."

    if name == "run_shell":
        # 安全要求：必须有 execution_backend，禁止回退到裸 subprocess.run(shell=True)。
        if execution_backend is None:
            return "Error: run_shell requires an execution backend. No sandbox is configured."
        try:
            timeout_ms = int(inp.get("timeout", DEFAULT_SHELL_TIMEOUT_MS))
        except (TypeError, ValueError):
            return f"Error: invalid timeout: {inp.get('timeout')}"
        result = await execution_backend.run_shell(
            inp.get("command", ""),
            timeout_ms,
            Path.cwd(),
        )
        return _truncate_result(result)

    handler = BUILTIN_HANDLERS.get(name)
    if not handler:
        return f"Unknown tool: {name}"
    result = _truncate_result(handler(inp))

    if name in ("write_file", "edit_file") and read_file_state is not None and not result.startswith("Error"):
        abs_path = str(Path(inp["file_path"]).resolve())
        with contextlib.suppress(OSError):
            read_file_state[abs_path] = os.path.getmtime(abs_path)

    return result
