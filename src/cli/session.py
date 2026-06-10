"""Application assembly point for Agent, backend, tools, memory, and extensions."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from ..agent.agent import Agent, RuntimeConfig, format_agent_results
from ..agent.events import PermissionRequested, RuntimeEvent
from ..agent.harness.compressor import COMPACT_SUMMARY_MAX_TOKENS, Compressor
from ..agent.harness.context.builder import (
    build_prompt_bundle,
    build_stable_system_prompt,
    render_deferred_tools_attachment,
    render_mcp_delta_attachment,
    render_skill_listing_attachment,
)
from ..agent.harness.hooks import HookInput, HookManager
from ..agent.types import ToolCall, ToolDef, ToolResult
from ..providers import create_backend
from .core.extensions import ExtensionAPI, ExtensionRunner, load_extensions
from .core.mcp.manager import McpManager
from .core.memory.runtime import MemoryRuntime
from .core.sandbox import SandboxManager
from .core.skills.registry import discover_skills
from .core.skills.runtime import ActiveSkillManager, SkillInvocation
from .core.subagents.orchestrator import SubAgentOrchestrator
from .core.tools.builtin import builtin_tool_definitions
from .core.tools.registry import ToolRegistry
from .core.tools.runtime import ToolRuntime
from .core.tools.types import COMPACT_UTILIZATION_THRESHOLD, ToolContext


class AgentSession:
    def __init__(
        self,
        config: RuntimeConfig,
        *,
        thread_id: str | None = None,
        custom_tools: list[ToolDef] | None = None,
        sandbox_manager: SandboxManager | None = None,
        render_events: bool = True,
    ):
        self.config = config
        self.render_events = render_events
        self._confirm_fn = None
        self._sent_skill_names: set[str] = set()
        self._sent_deferred_tool_names: set[str] = set()
        self._mcp_initialized = False
        self._output_buffer: list[str] | None = None

        if config.custom_system_prompt:
            system_prompt = config.custom_system_prompt
            startup_context = ""
        elif config.is_sub_agent:
            system_prompt = build_stable_system_prompt()
            startup_context = ""
        else:
            bundle = build_prompt_bundle(Path(config.workspace))
            system_prompt = bundle.system_prompt
            startup_context = bundle.startup_context

        self.agent = Agent(
            config,
            system_prompt=system_prompt,
            startup_context=startup_context,
            session_id=thread_id,
        )
        self.model = self.agent.model

        self.backend = create_backend(
            provider=config.provider,
            api_key=config.api_key or "",
            model=config.model,
            api_base=config.api_base,
            anthropic_base_url=config.anthropic_base_url,
        )

        self.tool_registry = ToolRegistry(custom_tools if custom_tools is not None else builtin_tool_definitions())
        self.sandbox_manager = sandbox_manager or SandboxManager(config.sandbox_config, session_id=self.agent.session_id)
        self.skill_invocation = SkillInvocation()
        self.active_skills = ActiveSkillManager()
        self.hook_manager = HookManager.capture()
        self.mcp_manager = McpManager(cwd=Path(config.workspace), on_tools_changed=self._on_mcp_tool_delta)
        self.extension_runner = ExtensionRunner()
        self.extension_api = ExtensionAPI(self.extension_runner, self.tool_registry)
        self.loaded_extensions = load_extensions(self.extension_api)
        self.memory_runtime = MemoryRuntime(self.agent, self._build_side_query())
        self.compressor = Compressor(
            self.agent,
            summarize_messages=self._summarize_messages,
            notify=self._notify,
        )

        self.agent.bind_runtime(
            tool_registry=self.tool_registry,
            sandbox_manager=self.sandbox_manager,
            mcp_manager=self.mcp_manager,
            hook_manager=self.hook_manager,
            skill_invocation=self.skill_invocation,
            active_skills=self.active_skills,
            tool_definitions=self._tool_definitions,
            ensure_ready=self._ensure_mcp_initialized,
            shutdown=self._shutdown_runtime,
            prepare_initial_attachments=self._prepare_initial_attachments,
            start_memory_prefetch=self.memory_runtime.start_prefetch,
            consume_memory_prefetch=self.memory_runtime.consume_prefetch,
        )
        self.agent.set_callbacks(
            on_agent_start=self.extension_runner.on_runtime_event,
            on_agent_end=self.extension_runner.on_runtime_event,
            on_turn_start=self.extension_runner.on_runtime_event,
            on_turn_end=self.extension_runner.on_runtime_event,
            on_before_tool_call=self.extension_runner.before_tool_call,
            on_after_tool_call=self.extension_runner.after_tool_call,
        )

        from ..agent.loop import AgentLoop

        self.loop = AgentLoop(
            self.agent,
            self.backend,
            execute_tools=self._execute_tools,
            run_compression_pipeline=self.compressor.run_pipeline,
            check_and_compact=self._check_and_compact,
            apply_user_prompt_hooks=self._apply_user_prompt_hooks,
            run_stop_hook=self._run_stop_hook,
        )

    @property
    def is_processing(self) -> bool:
        task = getattr(self.agent, "_current_task", None)
        return bool(task and not task.done())

    @property
    def thread_id(self) -> str:
        return self.agent.session_id

    @property
    def permission_mode(self) -> str:
        return self.agent.permission_mode

    @property
    def approvals(self):
        return None

    def set_confirm_fn(self, fn) -> None:
        self._confirm_fn = fn
        self.agent.set_confirm_fn(fn)

    def abort(self) -> None:
        self.agent.abort()

    async def run(self, prompt: str) -> AsyncIterator[RuntimeEvent]:
        async for event in self.loop.run(prompt):
            yield event

    async def chat(self, prompt: str) -> None:
        async for event in self.run(prompt):
            self._render_event(event)

    async def run_once(self, prompt: str) -> dict:
        self._output_buffer = []
        prev_in = self.agent.total_input_tokens
        prev_out = self.agent.total_output_tokens
        prev_hit = self.agent.total_input_cache_hit_tokens
        prev_miss = self.agent.total_input_cache_miss_tokens
        async for event in self.run(prompt):
            if event.type == "assistant.delta":
                self._output_buffer.append(str(event.payload.get("text", "")))
        text = "".join(self._output_buffer)
        self._output_buffer = None
        return {
            "text": text,
            "tokens": {
                "input": self.agent.total_input_tokens - prev_in,
                "output": self.agent.total_output_tokens - prev_out,
                "input_cache_hit": self.agent.total_input_cache_hit_tokens - prev_hit,
                "input_cache_miss": self.agent.total_input_cache_miss_tokens - prev_miss,
            },
        }

    async def compact(self) -> None:
        await self.compressor.compact_conversation()

    def clear_history(self) -> None:
        self.agent.clear_history()
        self._notify("Conversation cleared.")

    def show_cost(self) -> None:
        self._notify(self.agent.cost_summary())

    def restore_session(self, data: dict) -> None:
        count = self.agent.restore_session(data)
        self._notify(f"Session restored ({count} messages).")

    async def shutdown(self) -> None:
        await self.agent.emit(
            self.agent._on_agent_end,
            RuntimeEvent("agent.end", {"session_id": self.agent.session_id}),
        )
        await self.agent.shutdown()

    async def invoke_skill(self, skill_name: str, args: str = "", invoked_by: str = "user") -> str:
        invocation = self.skill_invocation.invoke(skill_name, args, invoked_by)
        if not invocation.ok:
            return invocation.error or f"Unknown skill: {skill_name}"
        self.active_skills.record(invocation)
        prompt = str(invocation.rendered_prompt)
        await self.chat(prompt)
        return prompt

    async def _execute_tools(
        self,
        calls: list[ToolCall],
    ) -> tuple[list[RuntimeEvent], list[tuple[ToolCall, ToolResult]]]:
        events: list[RuntimeEvent] = []

        async def capture(event_obj) -> None:
            events.append(event_obj)

        runtime = ToolRuntime(
            self.tool_registry,
            permission_mode=self.agent.permission_mode,
            confirm_fn=self.agent._confirm_dangerous,
            confirmed=self.agent._confirmed_paths,
            hooks=self.hook_manager,
            event_callback=capture,
            agent=self.agent,
            before_tool_call=self.agent._on_before_tool_call,
            after_tool_call=self.agent._on_after_tool_call,
        )
        ctx = ToolContext(
            cwd=Path(self.config.workspace),
            session_id=self.agent.session_id,
            read_file_state=self.agent._read_file_state,
            sandbox_manager=self.sandbox_manager,
            mcp_manager=self.mcp_manager,
            agent=self,
        )
        return events, await runtime.execute_many(calls, ctx)

    def _tool_definitions(self) -> list[ToolDef]:
        denied = self.active_skills.disallowed_tools()
        return self.tool_registry.active_definitions(denied=denied)

    async def _ensure_mcp_initialized(self) -> None:
        if self._mcp_initialized or self.agent.is_sub_agent:
            return
        self._mcp_initialized = True
        try:
            await self.mcp_manager.load_and_connect()
            mcp_defs = self.mcp_manager.get_tool_definitions()
            if mcp_defs:
                self.tool_registry.add_many(mcp_defs, origin="mcp", default_concurrency_safe=False)
        except Exception as exc:
            self.agent._diagnostics.append(f"MCP init failed: {exc}")

    async def _shutdown_runtime(self) -> None:
        await self.mcp_manager.disconnect_all()
        await self.sandbox_manager.stop()

    def _prepare_initial_attachments(self) -> None:
        if not self.agent.is_sub_agent:
            try:
                attachment, sent = render_skill_listing_attachment(
                    discover_skills(),
                    self._sent_skill_names,
                )
                self._sent_skill_names = sent
                self.agent.queue_context_attachment(attachment)
            except Exception as exc:
                self.agent._diagnostics.append(f"skill listing attachment failed: {exc}")

        try:
            denied = self.active_skills.disallowed_tools()
            names = self.tool_registry.deferred_names(denied=denied)
            unseen = [name for name in names if name not in self._sent_deferred_tool_names]
            self._sent_deferred_tool_names.update(unseen)
            self.agent.queue_context_attachment(render_deferred_tools_attachment(unseen))
        except Exception as exc:
            self.agent._diagnostics.append(f"deferred tools attachment failed: {exc}")

    def _on_mcp_tool_delta(self, delta, definitions: list[dict]) -> None:
        removed = set(getattr(delta, "removed", []) or [])
        changed = set(getattr(delta, "changed", []) or [])
        added = set(getattr(delta, "added", []) or [])
        if removed:
            self.tool_registry.remove_many(removed)
            self._sent_deferred_tool_names.difference_update(removed)
        wanted = added | changed
        if wanted:
            self.tool_registry.replace_many(
                [d for d in definitions if d.get("name") in wanted],
                origin="mcp",
                default_concurrency_safe=False,
            )
            self._sent_deferred_tool_names.difference_update(wanted)
        try:
            self.agent.queue_context_attachment(render_mcp_delta_attachment(delta))
        except Exception as exc:
            self.agent._diagnostics.append(f"MCP delta attachment failed: {exc}")

    async def _check_and_compact(self) -> None:
        if self.agent.last_input_token_count > self.agent.effective_window * COMPACT_UTILIZATION_THRESHOLD:
            self._notify("Context window filling up, compacting conversation...")
            await self.compressor.compact_conversation()

    async def _apply_user_prompt_hooks(self, user_message: str) -> str:
        hook_input = HookInput(
            event="UserPromptSubmit",
            session_id=self.agent.session_id,
            cwd=str(self.config.workspace),
            prompt=user_message,
        )
        prompt = user_message
        for output in await self.hook_manager.run("UserPromptSubmit", hook_input):
            if output.action == "deny":
                reason = output.reason or output.error or "User prompt denied by hook."
                return f"[UserPromptSubmit hook blocked the original prompt]\n{reason}"
            if output.action == "append_context" and output.content:
                prompt += "\n\n" + output.content
            if output.action == "modify" and output.updated_input and "prompt" in output.updated_input:
                prompt = str(output.updated_input["prompt"])
        return prompt

    async def _run_stop_hook(self, last_text: str) -> bool:
        outputs = await self.hook_manager.run(
            "Stop",
            HookInput(
                event="Stop",
                session_id=self.agent.session_id,
                cwd=str(self.config.workspace),
                last_assistant_text=last_text,
            ),
        )
        blocked = False
        for output in outputs:
            if output.action == "deny":
                self.agent.append_user_context(output.reason or output.error or "Stop hook requested continuation.")
                blocked = True
            elif output.action == "append_context" and output.content:
                self.agent.append_user_context(output.content)
                blocked = True
        return blocked

    def _build_side_query(self):
        async def _side_query(system: str, user_message: str) -> str:
            if self.config.use_openai:
                resp = await self.backend.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_message},
                    ],
                )
                if not resp.choices:
                    return ""
                return resp.choices[0].message.content or ""

            resp = await self.backend.client.messages.create(
                model=self.model,
                max_tokens=256,
                system=system,
                messages=[{"role": "user", "content": user_message}],
            )
            return "".join(b.text for b in resp.content if b.type == "text")

        if self.agent.is_sub_agent:
            return None
        return _side_query

    async def _summarize_messages(
        self,
        messages: list[dict],
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = COMPACT_SUMMARY_MAX_TOKENS,
    ) -> str | None:
        if self.config.use_openai:
            resp = await self.backend.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    *messages,
                    {"role": "user", "content": user_prompt},
                ],
            )
            return resp.choices[0].message.content or None

        resp = await self.backend.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[*messages, {"role": "user", "content": user_prompt}],
        )
        if resp.content and resp.content[0].type == "text":
            return resp.content[0].text
        return None

    async def _execute_agent_tool(self, inp: dict) -> str:
        orchestrator = SubAgentOrchestrator(self)
        if "tasks" in inp and isinstance(inp["tasks"], list):
            tasks = inp["tasks"]
        else:
            tasks = [{"type": inp.get("type", "general"), "prompt": inp.get("prompt", "")}]
        self._render_sub_agent_start(inp.get("type", "general"), inp.get("description", "sub-agent task"))
        results = await orchestrator.dispatch(tasks)
        return format_agent_results(results)

    async def _execute_skill_tool(self, inp: dict) -> str:
        invocation = self.skill_invocation.invoke(
            str(inp.get("skill_name") or ""),
            str(inp.get("args") or ""),
            invoked_by="model",
        )
        if not invocation.ok:
            return invocation.error or "Skill invocation failed."
        self.active_skills.record(invocation)
        return str(invocation.rendered_prompt)

    def _execute_tool_search(self, inp: dict) -> str:
        definitions = self.tool_registry.search_deferred(str(inp.get("query") or ""))
        return json.dumps(definitions, ensure_ascii=False, indent=2)

    def _render_event(self, event: RuntimeEvent) -> None:
        if not self.render_events:
            return
        from ..tui.renderer import get_renderer

        renderer = get_renderer()
        event_type = event.type
        if event_type == "user.input":
            return
        if event_type == "assistant.delta":
            renderer.assistant_delta(str(event.payload.get("text", "")))
        elif event_type == "tool.started":
            renderer.tool_call(str(event.payload.get("name", "")), event.payload.get("input") or {})
        elif event_type == "tool.finished":
            renderer.tool_result(str(event.payload.get("name", "")), str(event.payload.get("content", "")))
        elif event_type == "approval.requested":
            renderer.confirm(str(event.payload.get("message", "")))
        elif event_type == "budget.exceeded":
            renderer.info(f"Budget exceeded: {event.payload.get('reason', '')}")
        elif event_type == "turn.finished":
            if event.payload.get("stop_reason") == "stop":
                renderer.cost(
                    self.agent.total_input_tokens,
                    self.agent.total_output_tokens,
                    model=self.agent.model,
                    input_cache_hit_tokens=self.agent.total_input_cache_hit_tokens,
                    input_cache_miss_tokens=self.agent.total_input_cache_miss_tokens,
                )
        elif event_type == "context.compacted":
            renderer.info("Conversation compacted.")
        elif event_type == "runtime.error":
            renderer.error(str(event.payload.get("message", "")))

    def _notify(self, message: str) -> None:
        if not self.render_events:
            return
        from ..tui.renderer import get_renderer

        get_renderer().info(message)

    def _render_sub_agent_start(self, agent_type: str, description: str) -> None:
        if not self.render_events:
            return
        from ..tui.renderer import get_renderer

        get_renderer().sub_agent_start(agent_type, description)


def create_session(
    config: RuntimeConfig,
    *,
    thread_id: str | None = None,
    custom_tools: list[ToolDef] | None = None,
    sandbox_manager: SandboxManager | None = None,
    render_events: bool = True,
) -> AgentSession:
    return AgentSession(
        config,
        thread_id=thread_id,
        custom_tools=custom_tools,
        sandbox_manager=sandbox_manager,
        render_events=render_events,
    )
