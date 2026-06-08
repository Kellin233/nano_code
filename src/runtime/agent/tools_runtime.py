"""Agent 工具运行时。

本模块保留需要访问 Agent 状态的工具辅助能力，例如 skill、sub-agent、
MCP 和确认提示。正常工具执行管线已经迁到 `tools.runtime.ToolRuntime`；
这里的 `_execute_tool_call()` 作为兼容入口保留，避免旧测试或内部调用直接失效。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ...domains.skills import SkillInvocationResult
from ...domains.subagents import get_available_agent_types, get_sub_agent_config
from ...domains.tools import ToolDef, execute_builtin_tool
from ...tui.renderer import get_renderer


class AgentToolRuntimeMixin:
    """给 `Agent` 增加工具执行和派生任务能力。

    依赖 `Agent` 上的状态：
    `_tool_registry`、`permission_mode`、`_read_file_state`、`_mcp_manager`、
    `_skill_invocation`、`_active_skills`、token 计数和输出 buffer。

    提供给事件流和兼容路径使用的方法：
    `_current_tool_definitions()`、`_execute_tool_call()`、
    `_persist_large_result()`、`_confirm_dangerous()`。
    """

    def _current_tool_definitions(self) -> list[ToolDef]:
        denied = self._active_skills.disallowed_tools()
        return self._tool_registry.active_definitions(denied=denied)

    # ─── 大结果持久化 ─────────────────────────────────

    def _persist_large_result(self, tool_name: str, result: str) -> str:
        """超大工具结果落盘，仅把预览放回上下文，防止消息历史膨胀。"""
        threshold = 30 * 1024  # 30 KB
        if len(result.encode()) <= threshold:
            return result
        output_dir = Path.home() / ".nanocode" / "tool-results"
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{int(time.time() * 1000)}-{tool_name}.txt"
        filepath = output_dir / filename
        filepath.write_text(result, encoding="utf-8")

        lines = result.split("\n")
        preview = "\n".join(lines[:200])
        size_kb = len(result.encode()) / 1024

        return (
            f"[Result too large ({size_kb:.1f} KB, {len(lines)} lines). "
            f"Full output saved to {filepath}. "
            f"You can use read_file to see the full result.]\n\n"
            f"Preview (first 200 lines):\n{preview}"
        )

    # ─── 执行工具（内部处理智能体和技能）────────

    async def _execute_tool_call(self, name: str, inp: dict) -> str:
        if name == "agent":
            # agent 工具需要访问当前实例的模型、权限和 token 计数。
            return await self._execute_agent_tool(inp)
        if name == "skill":
            return await self._execute_skill_tool(inp)
        if name == "tool_search":
            return self._execute_tool_search(inp)
        if self._mcp_manager.is_mcp_tool(name):
            return await self._mcp_manager.call_tool(name, inp)
        if name == "list_mcp_resources":
            return await self._mcp_manager.list_resources(inp.get("server") or None)
        if name == "read_mcp_resource":
            return await self._mcp_manager.read_resource(str(inp.get("server", "")), str(inp.get("uri", "")))
        return await execute_builtin_tool(
            name,
            inp,
            self._read_file_state,
            self._sandbox_manager,
        )

    def _execute_tool_search(self, inp: dict) -> str:
        matches = self._tool_registry.search_deferred(inp.get("query", ""))
        if not matches:
            return "No matching deferred tools found."
        return json.dumps(matches, indent=2)

    # ─── 技能派生模式 ───────────────────────────────

    async def _execute_skill_tool(self, inp: dict) -> str:
        invocation = self._skill_invocation.invoke(
            inp.get("skill_name", ""), inp.get("args", ""), invoked_by="model"
        )
        if not invocation.ok:
            return invocation.error or f"Unknown skill: {inp.get('skill_name', '')}"

        self._active_skills.record(invocation)
        if invocation.context == "fork":
            return await self._run_fork_skill(invocation)

        return f'[Skill "{invocation.skill.name}" activated]\n\n{invocation.rendered_prompt}'

    async def invoke_skill(self, skill_name: str, args: str = "", invoked_by: str = "user") -> str:
        invocation = self._skill_invocation.invoke(skill_name, args, invoked_by=invoked_by)
        if not invocation.ok:
            return invocation.error or f"Unknown skill: {skill_name}"

        self._active_skills.record(invocation)
        if invocation.context == "fork":
            result = await self._run_fork_skill(invocation)
            self._emit_text("\n" + result + "\n")
            return result

        await self.chat(invocation.rendered_prompt)
        return invocation.rendered_prompt

    async def _run_fork_skill(self, invocation: SkillInvocationResult) -> str:
        assert invocation.skill is not None
        agent_type = invocation.agent or "general"
        valid_types = {t["name"] for t in get_available_agent_types()}
        fallback_note = ""
        if agent_type not in valid_types:
            fallback_note = f'\n[Skill agent "{agent_type}" not found; using general.]\n'
            agent_type = "general"

        config = get_sub_agent_config(agent_type)
        tools = self._filter_skill_tools(config["tools"], invocation)
        system_prompt = (
            config["system_prompt"]
            + "\n\n# Skill Instructions\n"
            + invocation.rendered_prompt
        )

        get_renderer().sub_agent_start(f"skill:{agent_type}", invocation.skill.name)
        # 用 type(self) 创建子 Agent，避免本模块反向 import agent 包形成循环依赖。
        sub_agent = type(self)(
            model=self.model,
            api_base=str(self._openai_client.base_url) if self.use_openai and self._openai_client else None,
            custom_system_prompt=system_prompt,
            custom_tools=tools,
            is_sub_agent=True,
            permission_mode="bypassPermissions",
            sandbox_manager=self._sandbox_manager,
        )
        try:
            sub_result = await sub_agent.run_once(invocation.args or "Execute this skill task.")
            self.total_input_tokens += sub_result["tokens"]["input"]
            self.total_output_tokens += sub_result["tokens"]["output"]
            get_renderer().sub_agent_end(f"skill:{agent_type}")
            return fallback_note + (sub_result["text"] or "(Skill produced no output)")
        except Exception as e:
            get_renderer().sub_agent_end(f"skill:{agent_type}")
            return fallback_note + f"Skill fork error: {e}"

    def _filter_skill_tools(self, tools: list[ToolDef], invocation: SkillInvocationResult) -> list[ToolDef]:
        filtered = [t for t in tools if t["name"] != "agent"]
        if invocation.allowed_tools:
            allowed = set(invocation.allowed_tools)
            filtered = [t for t in filtered if t["name"] in allowed]
        if invocation.disallowed_tools:
            denied = set(invocation.disallowed_tools)
            filtered = [t for t in filtered if t["name"] not in denied]
        return filtered

    async def _execute_agent_tool(self, inp: dict) -> str:
        agent_type = inp.get("type", "general")
        description = inp.get("description", "sub-agent task")
        prompt = inp.get("prompt", "")

        get_renderer().sub_agent_start(agent_type, description)

        config = get_sub_agent_config(agent_type)
        # fork-return：子 Agent 拥有独立消息历史，只继承工具、模型和权限边界。
        sub_agent = type(self)(
            model=self.model,
            api_base=str(self._openai_client.base_url) if self.use_openai and self._openai_client else None,
            custom_system_prompt=config["system_prompt"],
            custom_tools=config["tools"],
            is_sub_agent=True,
            permission_mode="bypassPermissions",
            sandbox_manager=self._sandbox_manager,
        )

        try:
            result = await sub_agent.run_once(prompt)
            self.total_input_tokens += result["tokens"]["input"]
            self.total_output_tokens += result["tokens"]["output"]
            get_renderer().sub_agent_end(agent_type)
            return result["text"] or "(Sub-agent produced no output)"
        except Exception as e:
            get_renderer().sub_agent_end(agent_type)
            return f"Sub-agent error: {e}"

    # ─── 共享确认逻辑 ─────────────────────────────────

    async def _confirm_dangerous(self, command: str) -> bool:
        get_renderer().confirm(command)
        if self.confirm_fn:
            return await self.confirm_fn(command)
        # 兜底：阻塞式输入，主要用于非 REPL 或测试注入缺失的场景。
        try:
            answer = input("  Allow? (y/n): ")
            return answer.lower().startswith("y")
        except EOFError:
            return False
