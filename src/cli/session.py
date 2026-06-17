"""Application assembly point for Agent, backend, tools, memory, and extensions."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from ..agent.agent import Agent, format_agent_results
from ..agent.budget import estimate_model_cost_usd
from ..agent.events import RuntimeEvent
from ..agent.harness.compressor import COMPACT_SUMMARY_MAX_TOKENS, Compressor
from ..agent.harness.context.builder import (
    build_prompt_bundle,
    build_stable_system_prompt,
    render_deferred_tools_attachment,
    render_mcp_delta_attachment,
    render_skill_listing_attachment,
)
from ..agent.harness.hooks import HookInput, HookManager
from ..agent.harness.persistence import (
    STATUS_FAILED,
    STATUS_STOPPED,
    STOP_REASON_ABORTED,
    STOP_REASON_BUDGET_EXCEEDED,
    STOP_REASON_ERROR,
    ArtifactStore,
    RunMetrics,
    RunStore,
    SessionLog,
    TaskState,
    build_report,
    now_iso,
    runtime_event_to_trace,
    trace_event,
)
from ..agent.types import ConversationHistory, ToolCall, ToolDef, ToolResult
from ..providers import create_backend
from ..providers.anthropic import to_anthropic_messages
from ..providers.openai import to_openai_messages
from .config import RuntimeConfig
from .core.extensions import ExtensionAPI, ExtensionRunner, load_extensions
from .core.mcp.manager import McpManager
from .core.memory.runtime import MemoryRuntime
from .core.memory.types import TOPIC_ORDER
from .core.sandbox import SandboxManager
from .core.skills.registry import discover_skills
from .core.skills.runtime import ActiveSkillManager, SkillInvocation
from .core.subagents import build_agent_tool_definition
from .core.subagents.orchestrator import SubAgentOrchestrator
from .core.tools.builtin import builtin_tool_definitions
from .core.tools.recent_files import RecentFileTracker
from .core.tools.registry import ToolRegistry
from .core.tools.runtime import ToolRuntime
from .core.tools.types import ToolContext


_CHANGE_ACTION_RE = re.compile(
    r"\b("
    r"updat(?:e|es|ed|ing)|fix(?:es|ed|ing)?|modif(?:y|ies|ied|ying)|edit(?:s|ed|ing)?|"
    r"chang(?:e|es|ed|ing)|add(?:s|ed|ing)?|remov(?:e|es|ed|ing)|delet(?:e|es|ed|ing)|"
    r"creat(?:e|es|ed|ing)|writ(?:e|es|ing|ten)|refactor(?:s|ed|ing)?|implement(?:s|ed|ing)?|"
    r"replac(?:e|es|ed|ing)|patch(?:es|ed|ing)?|overwrite|overwrit(?:e|es|ing|ten)"
    r")\b|"
    r"(修改|更新|修复|实现|添加|删除|创建|写入|替换|重构)",
    re.IGNORECASE,
)
_WORKSPACE_CONTEXT_RE = re.compile(
    r"\b(file|files|code|repo|repository|workspace|function|class|module|config|readme)\b|"
    r"\.[A-Za-z0-9]{1,8}\b|"
    r"(文件|代码|项目|仓库|函数|类|配置)",
    re.IGNORECASE,
)
_NO_WRITE_REQUEST_RE = re.compile(
    r"do not (edit|modify|change|write) (anything|files|code|the workspace|the repo|this)|"
    r"don't (edit|modify|change|write) (anything|files|code|the workspace|the repo|this)|"
    r"\b(only explain|no code changes|do not make changes|don't make changes)\b|"
    r"(不要修改|不要改|先别改|只分析)",
    re.IGNORECASE,
)


@dataclass
class _QualityState:
    prompt: str = ""
    requires_workspace_change: bool = False
    tool_sequence: int = 0
    mutation_count: int = 0
    modified_paths: set[str] = field(default_factory=set)
    last_mutation_sequence: int = 0
    verified_after_mutation: bool = False
    last_nudged_mutation_sequence: int = 0
    blocked_reasons: set[str] = field(default_factory=set)


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
        self.workspace = Path(config.workspace).resolve()
        self.render_events = render_events
        self._confirm_fn = None
        self._confirm_auto = False
        self._confirm_emits_approval_events = False
        self._confirmed_paths: set[str] = set()
        self._sent_skill_names: set[str] = set()
        self._sent_deferred_tool_names: set[str] = set()
        self._mcp_initialized = False
        self._output_buffer: list[str] | None = None
        self.run_store = RunStore(self.workspace / ".nanocode" / "runs")
        self.current_task_state: TaskState | None = None
        self.current_run_dir: Path | None = None
        self.session_log: SessionLog | None = None
        self._quality_state = _QualityState()

        self.memory_runtime = MemoryRuntime(self.workspace, enabled=not config.is_sub_agent)

        if config.custom_system_prompt:
            system_prompt = config.custom_system_prompt
            startup_context = ""
        elif config.is_sub_agent:
            system_prompt = build_stable_system_prompt()
            startup_context = ""
        else:
            bundle = build_prompt_bundle(self.workspace)
            system_prompt = self.memory_runtime.apply_to_system_prompt(bundle.system_prompt)
            startup_context = _join_context(bundle.startup_context, self.memory_runtime.build_startup_context())

        self.agent = Agent(
            config.to_agent_config(),
            system_prompt=system_prompt,
            startup_context=startup_context,
            session_id=thread_id,
        )
        self.model = self.agent.model
        if not config.is_sub_agent:
            self.session_log = SessionLog(self.agent.session_id)
            self.session_log.ensure_session({
                "workspace": str(self.workspace),
                "provider": config.provider,
                "model": self.agent.model,
            })
        self.artifact_store = ArtifactStore(
            self.agent.session_id,
            root=self.workspace / ".nanocode" / "artifacts",
        )
        self.recent_files = RecentFileTracker(self.workspace)

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
        self.mcp_manager = McpManager(cwd=self.workspace, on_tools_changed=self._on_mcp_tool_delta)
        self.extension_runner = ExtensionRunner()
        self.extension_api = ExtensionAPI(self.extension_runner, self.tool_registry)
        self.loaded_extensions = load_extensions(self.extension_api)
        self.compressor = Compressor(
            self.agent,
            workspace=self.workspace,
            hooks=self.hook_manager,
            summarize_messages=self._summarize_messages,
            build_post_compact_context=self._build_post_compact_context,
            notify=self._notify,
            enable_tool_history_snip=self.config.context_governance != "off",
            enable_context_compact=self.config.context_governance != "off",
        )

        self.agent.bind_runtime(
            tool_definitions=self._tool_definitions,
            ensure_ready=self._ensure_mcp_initialized,
            shutdown=self._shutdown_runtime,
            prepare_initial_attachments=self._prepare_initial_attachments,
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
            prepare_context_for_provider=self._prepare_context_for_provider,
            apply_user_prompt_hooks=self._apply_user_prompt_hooks,
            run_stop_hook=self._run_stop_hook,
            commit_conversation=self._commit_conversation,
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
        return self.config.permission_mode

    @property
    def approvals(self):
        return None

    def set_confirm_fn(
        self,
        fn,
        *,
        auto_confirm: bool = False,
        emits_approval_events: bool = False,
    ) -> None:
        self._confirm_fn = fn
        self._confirm_auto = auto_confirm
        self._confirm_emits_approval_events = emits_approval_events

    async def _confirm_dangerous(
        self,
        message: str,
        *,
        call_id: str | None = None,
        tool_name: str | None = None,
        requires_explicit_confirmation: bool = False,
    ) -> bool:
        if requires_explicit_confirmation and self._confirm_auto:
            return False
        if self._confirm_fn is None:
            return False
        try:
            signature = inspect.signature(self._confirm_fn)
        except (TypeError, ValueError):
            return await self._confirm_fn(message)

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
        return await self._confirm_fn(message, **kwargs)

    def abort(self) -> None:
        self.agent.abort()

    async def run(self, prompt: str) -> AsyncIterator[RuntimeEvent]:
        task_state = TaskState.create(prompt)
        metrics = RunMetrics()
        final_chunks: list[str] = []
        started_at = now_iso()
        started_monotonic = time.monotonic()
        prev_in = self.agent.total_input_tokens
        prev_out = self.agent.total_output_tokens
        prev_hit = self.agent.total_input_cache_hit_tokens
        prev_miss = self.agent.total_input_cache_miss_tokens
        attempt_open = False
        finished = False

        self.current_task_state = task_state
        self.current_run_dir = self.run_store.start_run(task_state)
        self._quality_state = _QualityState(
            prompt=prompt,
            requires_workspace_change=_requires_workspace_change(prompt),
        )
        self.run_store.append_trace(
            task_state,
            trace_event(
                task_state,
                "run_started",
                {
                    "session_id": self.agent.session_id,
                    "user_request": prompt,
                    "workspace": str(self.workspace),
                },
            ),
        )

        try:
            async for event in self.loop.run(prompt):
                if (
                    event.type in {"assistant.delta", "tool.started", "turn.finished", "budget.exceeded", "runtime.error"}
                    and not attempt_open
                ):
                    task_state.record_attempt()
                    attempt_open = True

                if event.type == "assistant.delta":
                    final_chunks.append(str(event.payload.get("text", "")))

                if event.type == "tool.finished":
                    task_state.record_tool(str(event.payload.get("name", "")))
                    attempt_open = False

                metrics.observe(event)

                if event.type == "budget.exceeded":
                    task_state.stop(
                        STOP_REASON_BUDGET_EXCEEDED,
                        status=STATUS_STOPPED,
                        final_answer="".join(final_chunks),
                    )
                elif event.type == "runtime.error":
                    task_state.stop(STOP_REASON_ERROR, status=STATUS_FAILED, final_answer="".join(final_chunks))
                elif event.type == "turn.finished":
                    stop_reason = str(event.payload.get("stop_reason") or "")
                    self._apply_finished_stop_reason(task_state, stop_reason, "".join(final_chunks))
                    finished = True
                    self._append_session_checkpoint("turn_finished")

                self.run_store.append_trace(task_state, runtime_event_to_trace(task_state, event))
                yield event
        except asyncio.CancelledError:
            task_state.stop(STOP_REASON_ABORTED, status=STATUS_STOPPED, final_answer="".join(final_chunks))
            finished = True
            self.run_store.append_trace(
                task_state,
                trace_event(
                    task_state,
                    "run_finished",
                    {
                        "type": "turn.finished",
                        "payload": {"stop_reason": STOP_REASON_ABORTED},
                        "status": task_state.status,
                        "stop_reason": STOP_REASON_ABORTED,
                    },
                ),
            )
            raise
        except Exception as exc:
            task_state.stop(STOP_REASON_ERROR, status=STATUS_FAILED, final_answer="".join(final_chunks))
            metrics.runtime_error_count += 1
            metrics.runtime_error = str(exc)
            self.run_store.append_trace(
                task_state,
                trace_event(task_state, "runtime_error", {"message": str(exc), "type": "runtime.error"}),
            )
            raise
        finally:
            if not finished and task_state.status == "running":
                task_state.stop(STOP_REASON_ERROR, status=STATUS_FAILED, final_answer="".join(final_chunks))
            finished_at = now_iso()
            duration_ms = int((time.monotonic() - started_monotonic) * 1000)
            try:
                self.run_store.write_report(
                    task_state,
                    build_report(
                        task_state,
                        runtime=self._run_runtime_metadata(),
                        usage=self._run_usage_delta(prev_in, prev_out, prev_hit, prev_miss),
                        metrics=metrics,
                        started_at=started_at,
                        finished_at=finished_at,
                        duration_ms=duration_ms,
                    ),
                )
            finally:
                self.current_task_state = None
                self._quality_state = _QualityState()

    @staticmethod
    def _apply_finished_stop_reason(task_state: TaskState, stop_reason: str, final_answer: str) -> None:
        if stop_reason == "stop":
            task_state.finish_success(final_answer)
        elif stop_reason == "budget_exceeded":
            task_state.stop(STOP_REASON_BUDGET_EXCEEDED, status=STATUS_STOPPED, final_answer=final_answer)
        elif stop_reason == "aborted":
            task_state.stop(STOP_REASON_ABORTED, status=STATUS_STOPPED, final_answer=final_answer)
        elif stop_reason == "error":
            task_state.stop(STOP_REASON_ERROR, status=STATUS_FAILED, final_answer=final_answer)
        else:
            task_state.stop(stop_reason or STOP_REASON_ERROR, status=STATUS_STOPPED, final_answer=final_answer)

    def _run_runtime_metadata(self) -> dict:
        return {
            "model": self.agent.model,
            "provider": self.config.provider,
            "permission_mode": self.permission_mode,
            "allowed_tools": sorted(self.config.allowed_tools) if self.config.allowed_tools is not None else None,
            "workspace": str(self.workspace),
            "session_id": self.agent.session_id,
            "is_sub_agent": bool(self.config.is_sub_agent),
            "context_governance": self.config.context_governance,
        }

    def _run_usage_delta(self, prev_in: int, prev_out: int, prev_hit: int, prev_miss: int) -> dict:
        input_tokens = self.agent.total_input_tokens - prev_in
        output_tokens = self.agent.total_output_tokens - prev_out
        input_cache_hit_tokens = self.agent.total_input_cache_hit_tokens - prev_hit
        input_cache_miss_tokens = self.agent.total_input_cache_miss_tokens - prev_miss
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_cache_hit_tokens": input_cache_hit_tokens,
            "input_cache_miss_tokens": input_cache_miss_tokens,
            "estimated_cost_usd": estimate_model_cost_usd(
                self.agent.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                input_cache_hit_tokens=input_cache_hit_tokens,
                input_cache_miss_tokens=input_cache_miss_tokens,
            ),
        }

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
        if await self.compressor.compact_context(reason="manual_compact", force=True):
            self._commit_conversation("manual_compact")

    def clear_history(self) -> None:
        self.agent.clear_history()
        self.active_skills.clear()
        self._commit_conversation("manual_clear")
        self._notify("Conversation cleared.")

    def show_cost(self) -> None:
        self._notify(self.agent.cost_summary())

    def remember_memory(self, topic: str, text: str) -> str:
        try:
            record = self.memory_runtime.remember(topic, text)
        except ValueError as exc:
            return str(exc)
        return f"Saved memory to {record.path}"

    def memory_path(self) -> str:
        return str(self.memory_runtime.memory_dir)

    def memory_summary(self) -> str:
        topics = self.memory_runtime.list_topics()
        if not topics:
            return f"No local memories saved yet.\nMemory directory: {self.memory_runtime.memory_dir}"
        lines = [f"Memory directory: {self.memory_runtime.memory_dir}", ""]
        for topic in topics:
            lines.append(f"- {topic.filename}: {topic.description}")
        return "\n".join(lines)

    def show_memory_topic(self, topic: str) -> str:
        try:
            record = self.memory_runtime.read_topic(topic)
        except ValueError as exc:
            return str(exc)
        if record is None:
            valid = ", ".join(TOPIC_ORDER)
            return f"No memory saved for '{topic}'. Valid topics: {valid}"
        return f"{record.path}\n\n{record.content.rstrip()}"

    def restore_from_persistence(self) -> None:
        if self.session_log is None:
            return
        original = self.session_log.load(repair=False)
        repaired = self.session_log.load(repair=True)
        count = self.agent.restore_conversation(repaired)
        if repaired.snapshot() != original.snapshot():
            self.session_log.commit(repaired, reason="resume_repair")
        interrupted = self.run_store.mark_unfinished_interrupted(session_id=self.agent.session_id)
        suffix = f"; marked {interrupted} interrupted run(s)" if interrupted else ""
        self._notify(f"Session restored ({count} messages{suffix}).")

    def _commit_conversation(self, reason: str) -> None:
        if self.session_log is None:
            return
        run_id = self.current_task_state.run_id if self.current_task_state else ""
        if not self.session_log.commit(self.agent.conversation, reason=reason, run_id=run_id):
            return
        if self.current_task_state is not None:
            self.run_store.append_trace(
                self.current_task_state,
                trace_event(
                    self.current_task_state,
                    "conversation_committed",
                    {
                        "session_id": self.agent.session_id,
                        "conversation_seq": self.session_log.metadata().get("lastSeq", 0),
                        "reason": reason,
                    },
                ),
            )

    def _append_session_checkpoint(self, reason: str) -> None:
        if self.session_log is None:
            return
        run_id = self.current_task_state.run_id if self.current_task_state else ""
        self.session_log.append_checkpoint(reason=reason, run_id=run_id)

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
        if invocation.context == "fork":
            return await self._execute_agent_tool({
                "type": invocation.agent or "general",
                "prompt": invocation.rendered_prompt,
                "description": f"skill {invocation.skill.name if invocation.skill else skill_name}",
                "allowed_tools": invocation.allowed_tools,
            })
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
            permission_mode=self.permission_mode,
            confirm_fn=self._confirm_dangerous,
            confirmed=self._confirmed_paths,
            hooks=self.hook_manager,
            event_callback=None if self._confirm_emits_approval_events else capture,
            persist_large_result=(
                None if self.config.context_governance == "off" else self.artifact_store.write_tool_result
            ),
            record_tool_call=self.recent_files.record_tool_call,
            before_tool_call=self.agent._on_before_tool_call,
            after_tool_call=self.agent._on_after_tool_call,
            allowed_tools=self._effective_allowed_tools(),
        )
        ctx = ToolContext(
            cwd=self.workspace,
            session_id=self.agent.session_id,
            sandbox_manager=self.sandbox_manager,
            mcp_manager=self.mcp_manager,
            execute_agent_tool=self._execute_agent_tool,
            execute_skill_tool=self._execute_skill_tool,
            execute_tool_search=self._execute_tool_search,
        )
        results = await runtime.execute_many(calls, ctx)
        self._observe_quality_tool_results(results)
        self._append_post_tool_quality_reminder(results)
        return events, results

    def _observe_quality_tool_results(self, results: list[tuple[ToolCall, ToolResult]]) -> None:
        state = self._quality_state
        if not state.requires_workspace_change:
            return

        for call, result in results:
            state.tool_sequence += 1
            if result.is_error:
                continue

            path = _tool_path(call.input)
            if call.name in {"edit_file", "write_file"}:
                if path and not self._is_workspace_path(path):
                    continue
                state.mutation_count += 1
                state.last_mutation_sequence = state.tool_sequence
                state.verified_after_mutation = False
                if path:
                    state.modified_paths.add(_normalize_tool_path(path))
                continue

            if state.last_mutation_sequence and self._is_quality_verification(call.name, path):
                state.verified_after_mutation = True

    def _append_post_tool_quality_reminder(self, results: list[tuple[ToolCall, ToolResult]]) -> None:
        state = self._quality_state
        if (
            not state.requires_workspace_change
            or not state.last_mutation_sequence
            or state.verified_after_mutation
            or state.last_nudged_mutation_sequence == state.last_mutation_sequence
            or not results
        ):
            return

        state.last_nudged_mutation_sequence = state.last_mutation_sequence
        paths = _format_paths(state.modified_paths)
        message = (
            "<system-reminder>\n"
            f"You changed workspace files in this turn{paths}.\n\n"
            "Before reporting completion, verify the final workspace state. Keep the check proportional: use an "
            "existing relevant test, a focused command, or read back the changed file and check the relevant lines. "
            "Do not create or leave extra files solely for verification unless the user requested tests or the "
            "project already expects that file.\n"
            "</system-reminder>"
        )
        results[-1][1].extra_messages.append({"role": "user", "content": message})

    def _is_quality_verification(self, tool_name: str, path: str) -> bool:
        if tool_name == "run_shell":
            return True
        if tool_name == "read_file":
            return bool(path and _matches_any_path(path, self._quality_state.modified_paths))
        if tool_name == "grep_search":
            if not self._quality_state.modified_paths:
                return False
            return not path or _covers_any_modified_path(path, self._quality_state.modified_paths)
        return False

    def _is_workspace_path(self, path: str) -> bool:
        raw = str(path or "").strip()
        if not raw:
            return False
        candidate = Path(raw)
        if not candidate.is_absolute():
            return True
        try:
            candidate.resolve().relative_to(self.workspace)
            return True
        except ValueError:
            return False

    def _run_builtin_stop_quality_check(self, last_text: str) -> bool:
        _ = last_text
        state = self._quality_state
        if not state.requires_workspace_change:
            return False

        if state.mutation_count == 0:
            return self._block_stop_once(
                "missing_mutation",
                "<system-reminder>\n"
                "Completion check failed.\n\n"
                "The user requested a workspace file change, but this turn has no successful edit_file or write_file "
                "result. Do not finish by only describing the intended change. Apply the requested change with a file "
                "editing tool, then verify the result before your final response.\n"
                "</system-reminder>",
            )

        if not state.verified_after_mutation:
            return self._block_stop_once(
                "missing_verification",
                "<system-reminder>\n"
                "Completion check failed.\n\n"
                "You modified workspace files but have not verified the final state after the last edit. Before "
                "finishing, run a proportional check or read back the changed file. Do not create or leave extra "
                "files solely for verification unless the user requested tests or the project already expects them.\n"
                "</system-reminder>",
            )

        return False

    def _block_stop_once(self, reason: str, message: str) -> bool:
        state = self._quality_state
        if reason in state.blocked_reasons:
            return False
        state.blocked_reasons.add(reason)
        self.agent.append_user_context(message)
        self._append_quality_trace("completion_quality_blocked", {"reason": reason})
        return True

    def _append_quality_trace(self, event: str, payload: dict) -> None:
        task_state = self.current_task_state
        if task_state is None:
            return
        self.run_store.append_trace(task_state, trace_event(task_state, event, payload))

    def _effective_allowed_tools(self) -> set[str] | None:
        configured = set(self.config.allowed_tools) if self.config.allowed_tools is not None else None
        skill_allowed = self.active_skills.allowed_tools()
        if configured is None:
            return skill_allowed
        if skill_allowed is None:
            return configured
        return configured & skill_allowed

    def _tool_definitions(self) -> list[ToolDef]:
        denied = self.active_skills.disallowed_tools()
        definitions = self.tool_registry.active_definitions(
            denied=denied,
            allowed=self._effective_allowed_tools(),
        )
        return [
            build_agent_tool_definition(tool) if tool.get("name") == "agent" else tool
            for tool in definitions
        ]

    async def _ensure_mcp_initialized(self) -> None:
        if self._mcp_initialized or self.config.is_sub_agent:
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
        if not self.config.is_sub_agent:
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
            names = self.tool_registry.deferred_names(denied=denied, allowed=self._effective_allowed_tools())
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

    async def _prepare_context_for_provider(self):
        from ..agent.loop import PreparedContext

        prepared = await self.compressor.prepare_context_for_provider()
        return PreparedContext(
            conversation=prepared.conversation,
            changed=prepared.changed,
            reason=prepared.reason,
        )

    def _build_post_compact_context(self) -> str:
        if self.config.is_sub_agent:
            return ""
        denied = self.active_skills.disallowed_tools()
        deferred = self.tool_registry.deferred_names(denied=denied, allowed=self._effective_allowed_tools())
        return _join_context(
            build_prompt_bundle(self.workspace).startup_context,
            self.memory_runtime.build_compact_context(),
            self.active_skills.build_context(),
            render_deferred_tools_attachment(deferred),
            self.recent_files.build_context(),
        )

    async def _apply_user_prompt_hooks(self, user_message: str) -> str:
        hook_input = HookInput(
            event="UserPromptSubmit",
            session_id=self.agent.session_id,
            cwd=str(self.workspace),
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
        blocked = self._run_builtin_stop_quality_check(last_text)
        outputs = await self.hook_manager.run(
            "Stop",
            HookInput(
                event="Stop",
                session_id=self.agent.session_id,
                cwd=str(self.workspace),
                last_assistant_text=last_text,
            ),
        )
        for output in outputs:
            if output.action == "deny":
                self.agent.append_user_context(output.reason or output.error or "Stop hook requested continuation.")
                blocked = True
            elif output.action == "append_context" and output.content:
                self.agent.append_user_context(output.content)
                blocked = True
        return blocked

    async def _summarize_messages(
        self,
        messages: ConversationHistory,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = COMPACT_SUMMARY_MAX_TOKENS,
    ) -> str | None:
        if self.config.use_openai:
            resp = await self.backend.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    *to_openai_messages(messages),
                    {"role": "user", "content": user_prompt},
                ],
            )
            return resp.choices[0].message.content or None

        resp = await self.backend.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[*to_anthropic_messages(messages), {"role": "user", "content": user_prompt}],
        )
        if resp.content and resp.content[0].type == "text":
            return resp.content[0].text
        return None

    async def _execute_agent_tool(self, inp: dict) -> str:
        orchestrator = SubAgentOrchestrator(self)
        if "tasks" in inp and isinstance(inp["tasks"], list):
            tasks = inp["tasks"]
        else:
            tasks = [{
                "type": inp.get("type", "general"),
                "prompt": inp.get("prompt", ""),
                "allowed_tools": inp.get("allowed_tools"),
            }]
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
        if invocation.context == "fork":
            return await self._execute_agent_tool({
                "type": invocation.agent or "general",
                "prompt": invocation.rendered_prompt,
                "description": f"skill {invocation.skill.name if invocation.skill else inp.get('skill_name', '')}",
                "allowed_tools": invocation.allowed_tools,
            })
        self.active_skills.record(invocation)
        return str(invocation.rendered_prompt)

    def _execute_tool_search(self, inp: dict) -> str:
        denied = self.active_skills.disallowed_tools()
        definitions = self.tool_registry.search_deferred(
            str(inp.get("query") or ""),
            allowed=self._effective_allowed_tools(),
            denied=denied,
        )
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


def _join_context(*parts: str) -> str:
    return "\n\n".join(part.strip() for part in parts if part and part.strip())


def _requires_workspace_change(prompt: str) -> bool:
    text = str(prompt or "")
    if _NO_WRITE_REQUEST_RE.search(text):
        return False
    return bool(_CHANGE_ACTION_RE.search(text) and _WORKSPACE_CONTEXT_RE.search(text))


def _tool_path(inp: dict) -> str:
    for key in ("file_path", "path"):
        value = inp.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _normalize_tool_path(path: str) -> str:
    text = str(path or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip("/")


def _format_paths(paths: set[str]) -> str:
    if not paths:
        return ""
    preview = ", ".join(sorted(paths)[:5])
    if len(paths) > 5:
        preview += ", ..."
    return f": {preview}"


def _matches_any_path(path: str, wanted_paths: set[str]) -> bool:
    actual = _normalize_tool_path(path)
    if not actual:
        return False
    for wanted in wanted_paths:
        wanted_norm = _normalize_tool_path(wanted)
        if actual == wanted_norm or actual.endswith(f"/{wanted_norm}") or wanted_norm.endswith(f"/{actual}"):
            return True
    return False


def _covers_any_modified_path(path: str, modified_paths: set[str]) -> bool:
    actual = _normalize_tool_path(path)
    if not actual:
        return True
    for modified in modified_paths:
        modified_norm = _normalize_tool_path(modified)
        if modified_norm == actual or modified_norm.startswith(f"{actual}/") or actual.endswith(f"/{modified_norm}"):
            return True
    return False
