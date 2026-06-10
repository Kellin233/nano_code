"""Agent-level tool registry."""

from __future__ import annotations

import contextlib
import copy
import os
from pathlib import Path

from .builtin import (
    CONCURRENCY_SAFE_BUILTIN_TOOLS,
    EDIT_TOOL_NAMES,
    READ_TOOL_NAMES,
    builtin_tool_definitions,
    edit_file,
    grep_search,
    list_files,
    read_file,
    web_fetch,
    write_file,
)
from .types import (
    DEFAULT_SHELL_TIMEOUT_MS,
    MAX_RESULT_CHARS,
    TOOL_RESULT_CHAR_LIMITS,
    FunctionTool,
    Tool,
    ToolContext,
    ToolDef,
    ToolMetadata,
    ToolOrigin,
    ToolResult,
)

INTERNAL_SCHEMA_KEYS = {
    "deferred",
    "origin",
    "concurrency_safe",
    "read_only",
    "edit_tool",
    "mcp_server",
    "mcp_tool",
    "search_hint",
    "always_load",
}


def sanitize_tool_definition(tool: ToolDef) -> ToolDef:
    return {k: copy.deepcopy(v) for k, v in tool.items() if k not in INTERNAL_SCHEMA_KEYS}


def _truncate_result(result: str) -> str:
    if len(result) <= MAX_RESULT_CHARS:
        return result
    keep_each = (MAX_RESULT_CHARS - 60) // 2
    return (
        result[:keep_each]
        + f"\n\n[... truncated {len(result) - keep_each * 2} chars ...]\n\n"
        + result[-keep_each:]
    )


def _mark_read(path: str, ctx: ToolContext) -> None:
    abs_path = str(Path(path).resolve())
    with contextlib.suppress(OSError):
        ctx.read_file_state[abs_path] = os.path.getmtime(abs_path)


def _needs_read_first(name: str, inp: dict, ctx: ToolContext) -> str | None:
    abs_path = str(Path(inp["file_path"]).resolve())
    if not os.path.exists(abs_path):
        return None
    if abs_path not in ctx.read_file_state:
        verb = "writing" if name == "write_file" else "editing"
        return f"Error: You must read this file before {verb}. Use read_file first to see its current contents."
    if os.path.getmtime(abs_path) != ctx.read_file_state[abs_path]:
        verb = "writing" if name == "write_file" else "editing"
        return f"Warning: {inp['file_path']} was modified externally since your last read. Please read_file again before {verb}."
    return None


async def _call_builtin(name: str, inp: dict, ctx: ToolContext) -> ToolResult:
    if name == "read_file":
        result = read_file(inp)
        if not result.startswith("Error"):
            _mark_read(inp["file_path"], ctx)
        return ToolResult(_truncate_result(result), is_error=result.startswith("Error"))

    if name in ("write_file", "edit_file"):
        blocked = _needs_read_first(name, inp, ctx)
        if blocked:
            return ToolResult(blocked, is_error=blocked.startswith("Error"))
        result = write_file(inp) if name == "write_file" else edit_file(inp)
        if not result.startswith("Error"):
            _mark_read(inp["file_path"], ctx)
        return ToolResult(_truncate_result(result), is_error=result.startswith("Error"))

    if name == "list_files":
        result = list_files(inp)
    elif name == "grep_search":
        result = grep_search(inp)
    elif name == "web_fetch":
        result = web_fetch(inp)
    elif name == "list_mcp_resources":
        if not ctx.mcp_manager:
            return ToolResult("Error: MCP manager unavailable", is_error=True)
        result = await ctx.mcp_manager.list_resources(inp.get("server") or None)
    elif name == "read_mcp_resource":
        if not ctx.mcp_manager:
            return ToolResult("Error: MCP manager unavailable", is_error=True)
        result = await ctx.mcp_manager.read_resource(str(inp.get("server", "")), str(inp.get("uri", "")))
    elif name == "run_shell":
        # 安全要求：run_shell 必须有 sandbox/backend，禁止裸 shell 执行。
        if ctx.sandbox_manager is None:
            return ToolResult(
                "Error: run_shell requires a sandbox manager. "
                "No sandbox backend is configured for this session.",
                is_error=True,
            )
        try:
            timeout_ms = int(inp.get("timeout", DEFAULT_SHELL_TIMEOUT_MS))
        except (TypeError, ValueError):
            return ToolResult(f"Error: invalid timeout: {inp.get('timeout')}", is_error=True)
        result = await ctx.sandbox_manager.run_shell(inp.get("command", ""), timeout_ms, ctx.cwd)
    elif name == "agent":
        if not ctx.agent:
            return ToolResult("Error: agent tool is unavailable", is_error=True)
        result = await ctx.agent._execute_agent_tool(inp)
    elif name == "skill":
        if not ctx.agent:
            return ToolResult("Error: skill tool is unavailable", is_error=True)
        result = await ctx.agent._execute_skill_tool(inp)
    elif name == "tool_search":
        if not ctx.agent:
            return ToolResult("Error: tool_search is unavailable", is_error=True)
        result = ctx.agent._execute_tool_search(inp)
    else:
        result = f"Unknown tool: {name}"
    # 对于有独立截断策略的工具（TOOL_RESULT_CHAR_LIMITS 中配置的），
    # 保留原始结果，交由 ToolRuntime._persist_large_result 统一处理。
    needs_truncation = name not in TOOL_RESULT_CHAR_LIMITS
    return ToolResult(
        _truncate_result(result) if needs_truncation else result,
        is_error=str(result).startswith("Error"),
    )


def _build_tool(
    tool: ToolDef,
    *,
    origin: ToolOrigin,
    default_concurrency_safe: bool,
) -> Tool:
    name = str(tool["name"])
    deferred = bool(tool.get("deferred"))
    read_only = bool(tool.get("read_only", name in READ_TOOL_NAMES if origin == "builtin" else False))
    edit_tool = bool(tool.get("edit_tool", name in EDIT_TOOL_NAMES if origin == "builtin" else False))
    builtin_safe = origin == "builtin" and name in CONCURRENCY_SAFE_BUILTIN_TOOLS
    concurrency_safe = bool(tool.get("concurrency_safe", default_concurrency_safe or builtin_safe))

    if origin == "mcp":

        async def _call_mcp(inp: dict, ctx: ToolContext) -> ToolResult:
            if not ctx.mcp_manager:
                return ToolResult(f"Error: MCP manager unavailable for {name}", is_error=True)
            if hasattr(ctx.mcp_manager, "call_tool_result"):
                result_obj = await ctx.mcp_manager.call_tool_result(name, inp)
                return ToolResult(
                    _truncate_result(result_obj.text),
                    is_error=bool(getattr(result_obj, "is_error", False)),
                    metadata={
                        "saved_files": list(getattr(result_obj, "saved_files", []) or []),
                    },
                )
            result = await ctx.mcp_manager.call_tool(name, inp)
            return ToolResult(_truncate_result(result), is_error=str(result).startswith("Error"))

        return FunctionTool(
            tool,
            _call_mcp,
            origin=origin,
            deferred=deferred,
            read_only=read_only,
            edit_tool=edit_tool,
            concurrency_safe=concurrency_safe,
        )

    async def _call(inp: dict, ctx: ToolContext) -> ToolResult:
        return await _call_builtin(name, inp, ctx)

    return FunctionTool(
        tool,
        _call,
        origin=origin,
        deferred=deferred,
        read_only=read_only,
        edit_tool=edit_tool,
        concurrency_safe=concurrency_safe,
    )


class ToolRegistry:
    def __init__(self, tools: list[ToolDef] | None = None):
        self._tools: dict[str, Tool] = {}
        self._metadata: dict[str, ToolMetadata] = {}
        self._activated_deferred: set[str] = set()
        if tools:
            self.add_many(tools, origin="builtin")

    @classmethod
    def with_builtin_tools(cls) -> ToolRegistry:
        registry = cls()
        registry.add_many(builtin_tool_definitions(), origin="builtin")
        return registry

    def add_many(
        self,
        tools: list[ToolDef],
        *,
        origin: ToolOrigin = "custom",
        default_concurrency_safe: bool = False,
    ) -> None:
        for tool in tools:
            name = tool.get("name")
            if not name or name in self._tools:
                continue

            stored = _build_tool(
                copy.deepcopy(tool),
                origin=origin,
                default_concurrency_safe=default_concurrency_safe,
            )
            read_only = stored.is_read_only({})
            edit_tool = stored.is_edit_tool({})
            concurrency_safe = stored.is_concurrency_safe({})

            self._tools[name] = stored
            self._metadata[name] = ToolMetadata(
                name=name,
                origin=origin,
                deferred=stored.deferred,
                concurrency_safe=concurrency_safe,
                read_only=read_only,
                edit_tool=edit_tool,
                raw={
                    k: copy.deepcopy(tool[k])
                    for k in INTERNAL_SCHEMA_KEYS
                    if k in tool
                },
            )

    def register(
        self,
        tool: ToolDef,
        *,
        call_fn=None,
        origin: ToolOrigin = "custom",
        default_concurrency_safe: bool = False,
    ) -> None:
        """Register one tool definition, optionally backed by a Python callable."""
        name = tool.get("name")
        if not name or name in self._tools:
            return

        if call_fn is not None:
            stored = FunctionTool(
                copy.deepcopy(tool),
                call_fn,
                origin=origin,
                deferred=bool(tool.get("deferred")),
                read_only=bool(tool.get("read_only", False)),
                edit_tool=bool(tool.get("edit_tool", False)),
                concurrency_safe=bool(tool.get("concurrency_safe", default_concurrency_safe)),
            )
        else:
            stored = _build_tool(
                copy.deepcopy(tool),
                origin=origin,
                default_concurrency_safe=default_concurrency_safe,
            )

        read_only = stored.is_read_only({})
        edit_tool = stored.is_edit_tool({})
        concurrency_safe = stored.is_concurrency_safe({})
        self._tools[str(name)] = stored
        self._metadata[str(name)] = ToolMetadata(
            name=str(name),
            origin=origin,
            deferred=stored.deferred,
            concurrency_safe=concurrency_safe,
            read_only=read_only,
            edit_tool=edit_tool,
            raw={k: copy.deepcopy(tool[k]) for k in INTERNAL_SCHEMA_KEYS if k in tool},
        )

    def replace_many(
        self,
        tools: list[ToolDef],
        *,
        origin: ToolOrigin = "custom",
        default_concurrency_safe: bool = False,
    ) -> None:
        for tool in tools:
            name = tool.get("name")
            if not name:
                continue
            self.remove_many([str(name)])
            self.add_many([tool], origin=origin, default_concurrency_safe=default_concurrency_safe)

    def remove_many(self, names: list[str] | set[str]) -> None:
        for name in names:
            self._tools.pop(name, None)
            self._metadata.pop(name, None)
            self._activated_deferred.discard(name)

    def active_definitions(self, denied: set[str] | None = None) -> list[ToolDef]:
        denied = denied or set()
        result: list[ToolDef] = []
        for name, tool in self._tools.items():
            metadata = self._metadata[name]
            if name in denied:
                continue
            if metadata.deferred and name not in self._activated_deferred:
                continue
            result.append(tool.to_definition())
        return result

    def deferred_names(self, denied: set[str] | None = None) -> list[str]:
        denied = denied or set()
        return [
            name
            for name, metadata in self._metadata.items()
            if metadata.deferred and name not in self._activated_deferred and name not in denied
        ]

    def search_deferred(self, query: str) -> list[ToolDef]:
        query = (query or "").strip()
        if not query:
            return []
        if query.lower().startswith("select:"):
            selected = {
                part.strip()
                for part in query[len("select:"):].replace(",", " ").split()
                if part.strip()
            }
            selected_matches: list[ToolDef] = []
            for name, tool in self._tools.items():
                metadata = self._metadata[name]
                if metadata.deferred and name not in self._activated_deferred and name in selected:
                    self._activated_deferred.add(name)
                    selected_matches.append(tool.to_definition())
            return selected_matches

        tokens = [token for token in query.lower().split() if token]
        server_filters = [token[1:] for token in tokens if token.startswith("+") and len(token) > 1]
        keywords = [token for token in tokens if not token.startswith("+")]
        matches: list[ToolDef] = []
        for name, tool in self._tools.items():
            metadata = self._metadata[name]
            if not metadata.deferred or name in self._activated_deferred:
                continue
            description = tool.description or ""
            raw = metadata.raw
            fields = [
                name,
                description,
                str(raw.get("mcp_server", "")),
                str(raw.get("mcp_tool", "")),
                str(raw.get("search_hint", "")),
            ]
            haystack = " ".join(fields).lower()
            if server_filters and not any(server in str(raw.get("mcp_server", "")).lower() or server in name.lower() for server in server_filters):
                continue
            if keywords and not all(keyword in haystack for keyword in keywords):
                continue
            if keywords or server_filters:
                self._activated_deferred.add(name)
                matches.append(tool.to_definition())
        return matches

    def metadata_for(self, name: str) -> ToolMetadata | None:
        return self._metadata.get(name)

    def find(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def is_concurrency_safe(self, name: str, inp: dict | None = None) -> bool:
        _ = inp
        tool = self._tools.get(name)
        return bool(tool and tool.is_concurrency_safe(inp or {}))

    def names(self) -> set[str]:
        return set(self._tools)
