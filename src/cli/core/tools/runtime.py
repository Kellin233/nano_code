"""Runtime entrypoint for built-in tool execution."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Collection
from pathlib import Path
from typing import Any

from ....agent.harness.hooks import HookInput, HookManager
from ....agent.harness.permissions import check_permission, check_tool_allowlist
from .builtin import edit_file, grep_search, list_files, read_file, web_fetch, write_file
from .registry import ToolRegistry
from .types import (
    DEFAULT_MAX_RESULT_CHARS,
    DEFAULT_SHELL_TIMEOUT_MS,
    TOOL_RESULT_CHAR_LIMITS,
    TOOL_RESULT_PREVIEW_CHARS,
    PermissionMode,
    ToolCall,
    ToolContext,
    ToolResult,
)

ConfirmFn = Callable[[str], Awaitable[bool]]
EventCallback = Callable[[Any], Awaitable[None]]
BeforeToolCall = Callable[[ToolCall], Awaitable[None] | None]
AfterToolCall = Callable[[ToolCall, ToolResult], Awaitable[None] | None]
PersistLargeResult = Callable[[str, str], dict[str, Any]]
RecordToolCall = Callable[[str, dict[str, Any]], None]


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
        persist_large_result: PersistLargeResult | None = None,
        record_tool_call: RecordToolCall | None = None,
        before_tool_call: BeforeToolCall | None = None,
        after_tool_call: AfterToolCall | None = None,
        allowed_tools: Collection[str] | None = None,
    ):
        self.registry = registry
        self.permission_mode = permission_mode
        self.confirm_fn = confirm_fn
        self.confirmed = confirmed if confirmed is not None else set()
        self.hooks = hooks or HookManager()
        self.event_callback = event_callback
        self.persist_large_result = persist_large_result
        self.record_tool_call = record_tool_call
        self.before_tool_call = before_tool_call
        self.after_tool_call = after_tool_call
        self.allowed_tools = allowed_tools

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
        allowlist_decision = check_tool_allowlist(call.name, self.allowed_tools)
        if allowlist_decision.action == "deny":
            return ToolResult(
                f"Action denied: {allowlist_decision.message}",
                is_error=True,
                metadata={"error_code": allowlist_decision.code or "action_denied"},
            )

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
                return ToolResult(
                    f"Action denied by hook: {reason}",
                    is_error=True,
                    metadata={"error_code": "action_denied"},
                )
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
            return ToolResult(
                f"Action denied: {decision.message}",
                is_error=True,
                metadata={"error_code": decision.code or "action_denied"},
            )
        if decision.action == "confirm" and decision.message and decision.message not in self.confirmed:
            if self.event_callback:
                from ....agent.events import PermissionRequested

                await self.event_callback(
                    PermissionRequested(
                        call,
                        decision.message,
                        requires_explicit_confirmation=decision.requires_explicit_confirmation,
                    )
                )
            confirmed = await self._confirm(
                decision.message,
                call_id=call.id,
                tool_name=call.name,
                requires_explicit_confirmation=decision.requires_explicit_confirmation,
            )
            if not confirmed:
                return ToolResult(
                    "User denied this action.",
                    is_error=True,
                    metadata={"error_code": decision.code or "action_denied"},
                )
            self.confirmed.add(decision.message)

        result = await tool.call(inp, ctx)
        result = self._persist_large_result(call.name, call.id, result)
        if self.record_tool_call and not result.is_error:
            self.record_tool_call(call.name, inp)

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

    async def _confirm(
        self,
        message: str,
        *,
        call_id: str | None = None,
        tool_name: str | None = None,
        requires_explicit_confirmation: bool = False,
    ) -> bool:
        if not self.confirm_fn:
            return False
        try:
            signature = inspect.signature(self.confirm_fn)
        except (TypeError, ValueError):
            return await self.confirm_fn(message)

        params = signature.parameters
        accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values())
        kwargs: dict[str, object] = {}
        for name, value in (
            ("call_id", call_id),
            ("tool_name", tool_name),
            ("requires_explicit_confirmation", requires_explicit_confirmation),
        ):
            if accepts_kwargs or name in params:
                kwargs[name] = value
        if "requires_explicit" in params:
            kwargs["requires_explicit"] = requires_explicit_confirmation
        return await self.confirm_fn(message, **kwargs)

    def _persist_large_result(self, tool_name: str, call_id: str, result: ToolResult) -> ToolResult:
        """对标 Claude Code：超大工具结果落盘 + <persisted-output> 预览。"""
        limit = TOOL_RESULT_CHAR_LIMITS.get(tool_name, DEFAULT_MAX_RESULT_CHARS)
        text = result.content
        if len(text) <= limit:
            return result
        if self.persist_large_result is None:
            return result

        artifact = self.persist_large_result(call_id, text)
        filepath = str(artifact.get("path", ""))

        size_kb = len(text.encode()) / 1024
        preview_chars = min(TOOL_RESULT_PREVIEW_CHARS, len(text))
        preview = text[:preview_chars]
        trunc_note = "\n... [truncated]" if len(text) > preview_chars else ""

        result.content = (
            "<persisted-output>\n"
            f"Output too large ({size_kb:.1f} KB). "
            f"Full output saved to: {filepath}\n\n"
            f"Preview (first {preview_chars} chars):\n"
            f"{preview}{trunc_note}\n"
            "</persisted-output>"
        )
        result.metadata["persisted"] = True
        result.metadata["artifact_path"] = str(filepath)
        result.metadata["full_result_path"] = str(filepath)
        result.metadata["original_size"] = len(text)
        result.metadata["preview_chars"] = preview_chars
        result.metadata["threshold_chars"] = limit
        result.metadata["tool_name"] = tool_name
        if artifact.get("sha256"):
            result.metadata["sha256"] = artifact["sha256"]

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
    execution_backend: Any | None = None,
) -> str:
    if name == "read_file":
        return read_file(inp)

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
        return result

    handler = BUILTIN_HANDLERS.get(name)
    if not handler:
        return f"Unknown tool: {name}"
    return handler(inp)
