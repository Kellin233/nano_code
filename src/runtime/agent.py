"""Agent 状态容器。

本模块是 Agent 的数据面，持有一次对话的所有状态字段。
不实现对话循环（见 loop.py）、API 调用（见 backend/）、
压缩策略（见 compressor.py）。

子 Agent fork 复用此类，通过 custom_system_prompt 和
custom_tools 定制行为边界。

变更原因：
  - 加新状态字段 → 改 __init__
  - 改消息历史操作 → 改 add_*/append_* 方法
  - 加新能力模块 → 改 __init__ 的能力模块实例化
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..capabilities.hooks import HookManager
from ..capabilities.mcp.manager import McpManager
from ..capabilities.sandbox import SandboxConfig, SandboxManager
from ..capabilities.skills.runtime import ActiveSkillManager, SkillInvocation
from ..capabilities.tools.builtin import builtin_tool_definitions
from ..capabilities.tools.registry import ToolRegistry
from ..capabilities.tools.types import CONTEXT_WINDOW_MARGIN, ToolDef
from ..models import get_context_window


@dataclass
class RuntimeConfig:
    """Agent 运行时配置。

    放在 agent.py 而非独立文件，因为配置字段和 Agent 状态字段
    共享变更原因——加一个配置项往往需要同时知道对应的状态字段。
    """
    model: str = "claude-opus-4-6"
    provider: str = "anthropic"  # "anthropic" | "openai"
    api_base: str | None = None
    anthropic_base_url: str | None = None
    api_key: str | None = None
    thinking: bool = False
    permission_mode: str = "default"
    max_cost_usd: float | None = None
    max_turns: int | None = None
    custom_system_prompt: str | None = None
    is_sub_agent: bool = False
    sandbox_config: SandboxConfig | None = None
    workspace: Path = field(default_factory=Path.cwd)

    @property
    def use_openai(self) -> bool:
        return self.provider == "openai"


class Agent:
    """Coding agent 的状态容器。

    不实现具体行为——循环、API 调用、压缩、上下文管理
    分别由 AgentLoop、Backend、Compressor、ContextBuilder 实现。
    """

    def __init__(
        self,
        config: RuntimeConfig,
        *,
        custom_tools: list[ToolDef] | None = None,
        sandbox_manager: SandboxManager | None = None,
    ):
        self.config = config
        self.model = config.model
        self.permission_mode = config.permission_mode
        self.thinking = config.thinking
        self.max_cost_usd = config.max_cost_usd
        self.max_turns = config.max_turns
        self.is_sub_agent = config.is_sub_agent

        # ── 工具 ──
        base_tools = custom_tools if custom_tools is not None else builtin_tool_definitions()
        self._tool_registry = ToolRegistry(base_tools)

        # ── 沙箱 ──
        self._sandbox_manager = sandbox_manager or SandboxManager(
            config.sandbox_config, session_id=""
        )

        # ── 能力模块 ──
        self._skill_invocation = SkillInvocation()
        self._active_skills = ActiveSkillManager()
        self._mcp_manager = McpManager(on_tools_changed=self._on_mcp_tool_delta)
        self._mcp_initialized = False
        self._hook_manager = HookManager.capture()

        # ── 会话标识 ──
        self.session_id = uuid.uuid4().hex[:8]
        self.session_start_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # ── Token 与预算 ──
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.last_input_token_count = 0
        self.current_turns = 0
        self.last_api_call_time = 0.0

        # ── 中断 ──
        self._aborted = False
        self._current_task = None

        # ── 权限 ──
        self._confirmed_paths: set[str] = set()
        self._confirm_fn: Callable[[str], Awaitable[bool]] | None = None

        # ── 文件状态（先读后改） ──
        self._read_file_state: dict[str, float] = {}

        # ── 输出缓冲（子 Agent fork 收集输出用） ──
        self._output_buffer: list[str] | None = None

        # ── 消息历史（双后端分开存储） ──
        self._anthropic_messages: list[dict] = []
        self._openai_messages: list[dict] = []

        # ── 上下文附件 ──
        self._pending_context_attachments: list[str] = []
        self._sent_skill_names: set[str] = set()
        self._sent_deferred_tool_names: set[str] = set()
        self._initial_context_attachments_prepared = False
        self._startup_context_injected = False

        # ── 记忆 ──
        self._already_surfaced_memories: set[str] = set()
        self._session_memory_bytes = 0
        self._memory_prefetch = None

        # ── 诊断 ──
        self._diagnostics: list[str] = []

        # ── 系统提示词 ──
        if config.custom_system_prompt:
            self._base_system_prompt = config.custom_system_prompt
            self._startup_context = ""
        elif config.is_sub_agent:
            self._base_system_prompt = self._build_system_prompt()
            self._startup_context = ""
        else:
            bundle = self._build_prompt_bundle()
            self._base_system_prompt = bundle["system_prompt"]
            self._startup_context = bundle["startup_context"]
        self._system_prompt = self._base_system_prompt

    @property
    def effective_window(self) -> int:
        return get_context_window(self.model) - CONTEXT_WINDOW_MARGIN

    # ─── 公开方法 ────────────────────────────────

    def set_confirm_fn(self, fn: Callable[[str], Awaitable[bool]]) -> None:
        self._confirm_fn = fn

    async def _confirm_dangerous(self, command: str) -> bool:
        """共享确认逻辑（供 ToolRuntime 和 loop 使用）。"""
        from ..tui.renderer import get_renderer
        get_renderer().confirm(command)
        if self._confirm_fn:
            return await self._confirm_fn(command)
        try:
            answer = input("  Allow? (y/n): ")
            return answer.lower().startswith("y")
        except EOFError:
            return False

    @property
    def aborted(self) -> bool:
        return self._aborted

    def abort(self) -> None:
        self._aborted = True
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()

    @property
    def messages(self) -> list[dict]:
        """返回当前后端对应的消息历史。"""
        return self._openai_messages if self.config.use_openai else self._anthropic_messages

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def tool_definitions(self) -> list[ToolDef]:
        denied = self._active_skills.disallowed_tools()
        return self._tool_registry.active_definitions(denied=denied)

    def get_token_usage(self) -> dict:
        return {"input": self.total_input_tokens, "output": self.total_output_tokens}

    def record_usage(self, input_tokens: int, output_tokens: int) -> None:
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.last_input_token_count = input_tokens

    def budget_exceeded(self) -> dict:
        cost = (self.total_input_tokens / 1_000_000) * 3 + (self.total_output_tokens / 1_000_000) * 15
        if self.max_cost_usd is not None and cost >= self.max_cost_usd:
            return {"exceeded": True, "reason": f"Cost limit reached (${cost:.4f})"}
        if self.max_turns is not None and self.current_turns >= self.max_turns:
            return {"exceeded": True, "reason": f"Turn limit reached ({self.current_turns})"}
        return {"exceeded": False}

    # ─── 消息历史操作 ────────────────────────────

    def add_user_message(self, content: str) -> None:
        if self.config.use_openai:
            self._openai_messages.append({"role": "user", "content": content})
        else:
            self._anthropic_messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: list[dict]) -> None:
        if self.config.use_openai:
            self._openai_messages.append({"role": "assistant", "content": content})
        else:
            self._anthropic_messages.append({"role": "assistant", "content": content})

    def add_tool_results(self, results: list[dict]) -> None:
        if self.config.use_openai:
            for r in results:
                self._openai_messages.append(r)
        else:
            self._anthropic_messages.append({"role": "user", "content": results})

    def append_user_context(self, text: str) -> None:
        """把补充上下文追加到最新用户消息后面，保持消息角色交替合法。"""
        if self.config.use_openai:
            last = self._openai_messages[-1] if self._openai_messages else None
            if last and last.get("role") == "user":
                last["content"] = (last.get("content") or "") + "\n\n" + text
            else:
                self._openai_messages.append({"role": "user", "content": text})
        else:
            last = self._anthropic_messages[-1] if self._anthropic_messages else None
            if last and last.get("role") == "user":
                content = last.get("content", "")
                if isinstance(content, str):
                    last["content"] = content + "\n\n" + text
                elif isinstance(content, list):
                    content.append({"type": "text", "text": text})
            else:
                self._anthropic_messages.append({"role": "user", "content": text})

    def append_meta_user_message(self, text: str) -> None:
        """追加独立的系统上下文 user 消息，不混入真实用户输入。"""
        if not text:
            return
        if self.config.use_openai:
            self._openai_messages.append({"role": "user", "content": text})
        else:
            self._anthropic_messages.append({"role": "user", "content": text})

    def restore_session(self, data: dict) -> None:
        from ..tui.renderer import get_renderer
        if data.get("anthropicMessages"):
            self._anthropic_messages = data["anthropicMessages"]
        if data.get("openaiMessages"):
            self._openai_messages = data["openaiMessages"]
        if self._anthropic_messages or len(self._openai_messages) > (1 if self.config.use_openai else 0):
            self._startup_context_injected = True
        msg_count = len(self._openai_messages) if self.config.use_openai else len(self._anthropic_messages)
        get_renderer().info(f"Session restored ({msg_count} messages).")

    def clear_history(self) -> None:
        from ..tui.renderer import get_renderer
        self._anthropic_messages = []
        self._openai_messages = []
        if self.config.use_openai:
            self._openai_messages.append({"role": "system", "content": self._system_prompt})
        self._active_skills.clear()
        self._startup_context_injected = not bool(self._startup_context)
        self._pending_context_attachments.clear()
        self._sent_skill_names.clear()
        self._sent_deferred_tool_names.clear()
        self._initial_context_attachments_prepared = False
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.last_input_token_count = 0
        get_renderer().info("Conversation cleared.")

    def show_cost(self) -> None:
        from ..tui.renderer import get_renderer
        cost = (self.total_input_tokens / 1_000_000) * 3 + (self.total_output_tokens / 1_000_000) * 15
        budget_info = f" / ${self.max_cost_usd} budget" if self.max_cost_usd else ""
        turn_info = f" | Turns: {self.current_turns}/{self.max_turns}" if self.max_turns else ""
        get_renderer().info(
            f"Tokens: {self.total_input_tokens} in / {self.total_output_tokens} out\n"
            f"  Estimated cost: ${cost:.4f}{budget_info}{turn_info}"
        )

    # ─── 生命周期 ────────────────────────────────

    async def ensure_mcp_initialized(self) -> None:
        if self._mcp_initialized or self.is_sub_agent:
            return
        self._mcp_initialized = True
        try:
            await self._mcp_manager.load_and_connect()
            mcp_defs = self._mcp_manager.get_tool_definitions()
            if mcp_defs:
                self._tool_registry.add_many(mcp_defs, origin="mcp", default_concurrency_safe=False)
        except Exception as exc:
            self._diagnostics.append(f"MCP init failed: {exc}")

    async def shutdown(self) -> None:
        await self._mcp_manager.disconnect_all()
        await self._sandbox_manager.stop()

    # ─── 工具辅助（供 ToolRuntime 和 loop 使用）───

    def get_read_file_state(self) -> dict[str, float]:
        return self._read_file_state

    def get_sandbox_manager(self):
        return self._sandbox_manager

    def get_mcp_manager(self):
        return self._mcp_manager

    def get_hook_manager(self):
        return self._hook_manager

    def get_skill_invocation(self):
        return self._skill_invocation

    def get_active_skills(self):
        return self._active_skills

    def get_tool_registry(self):
        return self._tool_registry

    # ─── 上下文附件 ──────────────────────────────

    def queue_context_attachment(self, text: str) -> None:
        if not text or not text.strip():
            return
        self._pending_context_attachments.append(text)

    def flush_pending_attachments(self) -> None:
        if not self._pending_context_attachments:
            return
        combined = "\n\n".join(self._pending_context_attachments)
        self._pending_context_attachments.clear()
        self.append_meta_user_message(combined)

    def inject_startup_context(self) -> None:
        if self._startup_context_injected:
            return
        context = self._startup_context
        if context:
            self.append_meta_user_message(context)
        self._startup_context_injected = True

    def prepare_initial_attachments(self) -> None:
        if self._initial_context_attachments_prepared:
            return
        self._initial_context_attachments_prepared = True

        if not self.is_sub_agent:
            try:
                from ..capabilities.skills.registry import discover_skills
                from ..context.builder import render_skill_listing_attachment

                attachment, sent = render_skill_listing_attachment(
                    discover_skills(),
                    self._sent_skill_names,
                )
                self._sent_skill_names = sent
                self.queue_context_attachment(attachment)
            except Exception as exc:
                self._diagnostics.append(f"skill listing attachment failed: {exc}")

        try:
            from ..context.builder import render_deferred_tools_attachment

            denied = self._active_skills.disallowed_tools()
            names = self._tool_registry.deferred_names(denied=denied)
            unseen = [name for name in names if name not in self._sent_deferred_tool_names]
            self._sent_deferred_tool_names.update(unseen)
            self.queue_context_attachment(render_deferred_tools_attachment(unseen))
        except Exception as exc:
            self._diagnostics.append(f"deferred tools attachment failed: {exc}")

    # ─── 子 Agent fork ───────────────────────────

    async def run_once(self, prompt: str) -> dict:
        """作为子 Agent 执行一次任务，返回最终文本和增量 token 用量。"""
        self._output_buffer = []
        prev_in = self.total_input_tokens
        prev_out = self.total_output_tokens

        # 延迟导入避免循环
        from ..backend import create_backend

        backend = create_backend(
            provider=self.config.provider,
            api_key=self.config.api_key or "",  # type: ignore[arg-type]
            model=self.model,
            api_base=self.config.api_base,
            anthropic_base_url=self.config.anthropic_base_url,
        )
        from .loop import AgentLoop
        loop = AgentLoop(self, backend)

        async for event in loop.run(prompt):
            if event.type == "assistant.delta":
                self._emit_text(event.payload.get("text", ""))

        text = "".join(self._output_buffer)
        self._output_buffer = None
        return {
            "text": text,
            "tokens": {
                "input": self.total_input_tokens - prev_in,
                "output": self.total_output_tokens - prev_out,
            },
        }

    def _emit_text(self, text: str) -> None:
        if self._output_buffer is not None:
            self._output_buffer.append(text)
        else:
            from ..tui.renderer import get_renderer
            get_renderer().assistant_delta(text)

    # ─── 记忆召回 ─────────────────────────────────

    def start_memory_prefetch(self, user_message: str):
        """启动异步记忆召回。子 Agent 不触发记忆系统。"""
        if self.is_sub_agent:
            return None
        from ..capabilities.memory.retrieval import start_memory_prefetch

        side_query = self._build_side_query()
        if not side_query:
            return None
        return start_memory_prefetch(
            user_message,
            side_query,
            self._already_surfaced_memories,
            self._session_memory_bytes,
        )

    def consume_memory_prefetch(self, prefetch) -> None:
        """非阻塞消费记忆预取结果。"""
        if not prefetch or not prefetch.settled or prefetch.consumed:
            return
        prefetch.consumed = True
        try:
            from ..capabilities.memory.retrieval import format_memories_for_injection
            from ..capabilities.memory.store import mark_accessed

            memories = prefetch.task.result()
            if not memories:
                return
            injection_text = format_memories_for_injection(memories)
            self.append_user_context(injection_text)
            for memory in memories:
                self._already_surfaced_memories.add(memory.path)
                self._session_memory_bytes += len(memory.content.encode())
            mark_accessed([memory.path for memory in memories])
        except Exception as exc:
            self._diagnostics.append(f"memory prefetch consume failed: {exc}")

    def _build_side_query(self):
        """构建用于记忆召回的旁路查询可调用对象。"""
        import anthropic
        import openai

        if self.config.use_openai:
            client = openai.AsyncOpenAI(
                base_url=self.config.api_base,
                api_key=self.config.api_key,
            )
            model = self.model

            async def _sq(system: str, user_message: str) -> str:
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

            return _sq

        kwargs: dict = {"api_key": self.config.api_key}
        if self.config.anthropic_base_url:
            kwargs["base_url"] = self.config.anthropic_base_url
        client = anthropic.AsyncAnthropic(**kwargs)
        model = self.model

        async def _sq_a(system: str, user_message: str) -> str:
            resp = await client.messages.create(
                model=model, max_tokens=256, system=system,
                messages=[{"role": "user", "content": user_message}],
            )
            return "".join(b.text for b in resp.content if b.type == "text")

        return _sq_a

    # ─── MCP 回调 ─────────────────────────────────

    def _on_mcp_tool_delta(self, delta, definitions: list[dict]) -> None:
        removed = set(getattr(delta, "removed", []) or [])
        changed = set(getattr(delta, "changed", []) or [])
        added = set(getattr(delta, "added", []) or [])
        if removed:
            self._tool_registry.remove_many(removed)
            self._sent_deferred_tool_names.difference_update(removed)
        wanted = added | changed
        if wanted:
            self._tool_registry.replace_many(
                [d for d in definitions if d.get("name") in wanted],
                origin="mcp",
                default_concurrency_safe=False,
            )
            self._sent_deferred_tool_names.difference_update(wanted)
        try:
            from ..context.builder import render_mcp_delta_attachment
            self.queue_context_attachment(render_mcp_delta_attachment(delta))
        except Exception as exc:
            self._diagnostics.append(f"MCP delta attachment failed: {exc}")

    # ─── 内部 ────────────────────────────────────

    async def _execute_agent_tool(self, inp: dict) -> str:
        """agent 工具入口——被 ToolRegistry._call_builtin 调用。

        支持单任务和多任务：
          - 单任务：type + prompt → 派发一个子 Agent
          - 多任务：tasks 列表 → 并行派发多个子 Agent
        """
        from ..capabilities.subagents.orchestrator import SubAgentOrchestrator
        from ..tui.renderer import get_renderer

        orchestrator = SubAgentOrchestrator(self)

        if "tasks" in inp and isinstance(inp["tasks"], list):
            tasks = inp["tasks"]
        else:
            tasks = [{"type": inp.get("type", "general"), "prompt": inp.get("prompt", "")}]

        agent_type = inp.get("type", "general")
        get_renderer().sub_agent_start(agent_type, inp.get("description", "sub-agent task"))

        results = await orchestrator.dispatch(tasks)
        return _format_agent_results(results)

    def _build_system_prompt(self) -> str:
        from ..context.builder import build_system_prompt
        return build_system_prompt()

    def _build_prompt_bundle(self) -> dict:
        from ..context.builder import build_prompt_bundle
        bundle = build_prompt_bundle()
        return {
            "system_prompt": bundle.system_prompt,
            "startup_context": bundle.startup_context,
        }


def _format_agent_results(results: list[dict]) -> str:
    """格式化子 Agent 结果为可读文本。"""
    if not results:
        return "(Sub-agent produced no output)"

    parts = []
    for i, r in enumerate(results):
        if len(results) > 1:
            parts.append(f"--- Sub-agent {i + 1} ({r.get('type', 'general')}) ---")
        if "error" in r:
            parts.append(f"Error: {r['error']}")
        parts.append(r.get("text", "(no output)"))
    return "\n\n".join(parts)
