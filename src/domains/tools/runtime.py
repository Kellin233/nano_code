"""Runtime entrypoint for built-in tool execution."""

from __future__ import annotations

import os
import asyncio
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..hooks import HookInput, HookManager
from ..permissions import check_permission
from .base import ToolCall, ToolContext, ToolResult
from .builtin import edit_file, grep_search, list_files, read_file, run_shell, web_fetch, write_file
from .registry import ToolRegistry
from .types import PermissionMode

MAX_RESULT_CHARS = 50000
LARGE_RESULT_BYTES = 30 * 1024


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
    ):
        self.registry = registry
        self.permission_mode = permission_mode
        self.confirm_fn = confirm_fn
        self.confirmed = confirmed if confirmed is not None else set()
        self.hooks = hooks or HookManager()
        self.event_callback = event_callback

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
        if decision.action == "confirm" and decision.message:
            if decision.message not in self.confirmed:
                if self.event_callback:
                    from ...runtime.agent.events import PermissionRequested

                    await self.event_callback(PermissionRequested(call, decision.message))
                confirmed = await self._confirm(decision.message)
                if not confirmed:
                    return ToolResult("User denied this action.", is_error=True)
                self.confirmed.add(decision.message)

        result = await tool.call(inp, ctx)
        result = self._persist_large_result(call.name, result)

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

    def _persist_large_result(self, tool_name: str, result: ToolResult) -> ToolResult:
        if len(result.content.encode()) <= LARGE_RESULT_BYTES:
            return result
        output_dir = Path.home() / ".nanocode" / "tool-results"
        output_dir.mkdir(parents=True, exist_ok=True)
        filepath = output_dir / f"{int(time.time() * 1000)}-{tool_name}.txt"
        filepath.write_text(result.content, encoding="utf-8")

        lines = result.content.split("\n")
        preview = "\n".join(lines[:200])
        size_kb = len(result.content.encode()) / 1024
        result.content = (
            f"[Result too large ({size_kb:.1f} KB, {len(lines)} lines). "
            f"Full output saved to {filepath}. "
            f"You can use read_file to see the full result.]\n\n"
            f"Preview (first 200 lines):\n{preview}"
        )
        result.metadata["full_result_path"] = str(filepath)
        return result


BUILTIN_HANDLERS = {
    "write_file": write_file,
    "edit_file": edit_file,
    "list_files": list_files,
    "grep_search": grep_search,
    "run_shell": run_shell,
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
            try:
                read_file_state[abs_path] = os.path.getmtime(abs_path)
            except OSError:
                pass
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

    if name == "run_shell" and execution_backend is not None:
        try:
            timeout_ms = int(inp.get("timeout", 30000))
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
        try:
            read_file_state[abs_path] = os.path.getmtime(abs_path)
        except OSError:
            pass

    return result
