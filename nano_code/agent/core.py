"""Agent 主类实现。

本模块是 `nano_code.agent` 包的运行时核心，但它刻意不实现所有细节。
它负责保存 Agent 的共享状态，并把不同职责组合到同一个 `Agent` 对象上：

- 初始化配置：模型、权限模式、API client、系统提示词、工具列表。
- 保存运行状态：消息历史、token 统计、MCP 连接状态、记忆预算、skill 状态。
- 提供公开入口：`chat()`、`run_once()`、`abort()`、`clear_history()`、`show_cost()`。
- 调度后端循环：根据 `use_openai` 选择 Anthropic 或 OpenAI-compatible 后端。

具体行为拆到 mixin 中：
- `AgentContextMixin`：消息上下文、记忆、压缩。
- `AgentToolRuntimeMixin`：工具路由、skill、sub-agent、MCP。
- `AgentBackendMixin`：模型 API 调用、streaming、工具调用回灌。

判断代码该放哪里：
- 需要新增 Agent 持有的状态，通常放在本文件的 `__init__`。
- 需要改模型消息历史或压缩策略，放到 `context.py`。
- 需要改工具执行流程，放到 `tools_runtime.py`。
- 需要改 API 请求或流式解析，放到 `backends.py`。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

import anthropic
import openai

from .backends import AgentBackendMixin
from .context import (
    KEEP_RECENT_RESULTS,
    MICROCOMPACT_IDLE_S,
    SNIP_PLACEHOLDER,
    SNIP_THRESHOLD,
    SNIPPABLE_TOOLS,
    AgentContextMixin,
)
from .models import (
    MODEL_CONTEXT,
    _get_context_window,
    _get_max_output_tokens,
    _is_retryable,
    _model_supports_adaptive_thinking,
    _model_supports_thinking,
    _to_openai_tools,
    _with_retry,
)
from .tools_runtime import AgentToolRuntimeMixin
from ..mcp_client import McpManager
from ..prompt import build_system_prompt
from ..session import save_session
from ..skill import ActiveSkillManager, SkillInvocation
from ..tools import (
    CONCURRENCY_SAFE_TOOLS,
    ToolDef,
    check_permission,
    execute_tool,
    get_active_tool_definitions,
    tool_definitions,
)
from ..ui import print_assistant_text, print_divider, print_info


# 兼容旧代码中直接从 nano_code.agent 访问这些私有 helper/常量的用法。
# 运行时实现已经拆到对应子模块；这里保留名字，避免无意义的导入破坏。


class Agent(AgentContextMixin, AgentToolRuntimeMixin, AgentBackendMixin):
    """Coding agent 的状态容器和公开入口。

    这个类不是传统意义上的“全部逻辑都在一个类里”。它更像运行时状态容器：
    本文件定义状态和少量公开方法，mixin 读取/修改这些状态来完成上下文管理、
    工具执行和模型循环。

    保持 `Agent` 入口稳定很重要：CLI、测试、子 Agent fork 都依赖
    `from nano_code.agent import Agent`。
    """

    def __init__(
        self,
        *,
        permission_mode: str = "default",
        model: str = "claude-opus-4-6",
        api_base: str | None = None,
        anthropic_base_url: str | None = None,
        api_key: str | None = None,
        thinking: bool = False,
        max_cost_usd: float | None = None,
        max_turns: int | None = None,
        confirm_fn: Callable[[str], Awaitable[bool]] | None = None,
        custom_system_prompt: str | None = None,
        custom_tools: list[ToolDef] | None = None,
        is_sub_agent: bool = False,
    ):
        self.permission_mode = permission_mode
        self.thinking = thinking
        self.model = model
        self.use_openai = bool(api_base)

        # 子智能体复用同一个 Agent 类，只是换系统提示词、工具集和 UI 行为。
        self.is_sub_agent = is_sub_agent
        self.tools = custom_tools or tool_definitions
        self.max_cost_usd = max_cost_usd
        self.max_turns = max_turns
        self.confirm_fn = confirm_fn
        self.effective_window = _get_context_window(model) - 20000
        self.session_id = uuid.uuid4().hex[:8]
        self.session_start_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.last_input_token_count = 0
        self.current_turns = 0
        self.last_api_call_time = 0.0

        # 中断支持：REPL 调 abort() 后，当前 chat 循环会尽快停下。
        self._aborted = False
        self._current_task: asyncio.Task | None = None

        # 权限白名单：同一个确认目标本会话内不重复问。
        self._confirmed_paths: set[str] = set()

        # Anthropic thinking 配置在初始化时固定，避免每次请求重复判断。
        self._thinking_mode = self._resolve_thinking_mode()

        # None 表示主 Agent 直接打印；list 表示子 Agent 收集输出作为工具结果。
        self._output_buffer: list[str] | None = None

        # 先读后改：工具层用它检测文件读取后是否被外部修改。
        self._read_file_state: dict[str, float] = {}

        # Skill runtime：调用器负责发现和渲染，active manager 用于 compact 后重挂。
        self._skill_invocation = SkillInvocation()
        self._active_skills = ActiveSkillManager()

        # MCP 延迟连接：只在主 Agent 第一次 chat 时加载外部工具。
        self._mcp_manager = McpManager()
        self._mcp_initialized = False

        # 记忆召回状态：每个用户回合最多进行一次语义预取。
        self._already_surfaced_memories: set[str] = set()
        self._session_memory_bytes = 0

        # 分离两种后端的消息历史，避免格式转换带来额外复杂度。
        self._anthropic_messages: list[dict] = []
        self._openai_messages: list[dict] = []

        # custom_system_prompt 是多 Agent 的关键入口。
        self._base_system_prompt = custom_system_prompt or build_system_prompt()
        self._system_prompt = self._base_system_prompt

        # 初始化客户端。OpenAI-compatible 后端需要 system message 进入消息历史。
        if self.use_openai:
            self._openai_client = openai.AsyncOpenAI(base_url=api_base, api_key=api_key)
            self._anthropic_client = None
            self._openai_messages.append({"role": "system", "content": self._system_prompt})
        else:
            kwargs: dict[str, Any] = {}
            if api_key:
                kwargs["api_key"] = api_key
            if anthropic_base_url:
                kwargs["base_url"] = anthropic_base_url
            self._anthropic_client = anthropic.AsyncAnthropic(**kwargs)
            self._openai_client = None

    def _resolve_thinking_mode(self) -> str:
        if not self.thinking:
            return "disabled"
        if not _model_supports_thinking(self.model):
            return "disabled"
        if _model_supports_adaptive_thinking(self.model):
            return "adaptive"
        return "enabled"

    @property
    def is_processing(self) -> bool:
        return self._current_task is not None and not self._current_task.done()

    def abort(self) -> None:
        self._aborted = True
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()

    def set_confirm_fn(self, fn: Callable[[str], Awaitable[bool]]) -> None:
        self.confirm_fn = fn

    def get_token_usage(self) -> dict:
        return {"input": self.total_input_tokens, "output": self.total_output_tokens}

    # ─── 主入口 ────────────────────────────────────────

    async def chat(self, user_message: str) -> None:
        # 首次对话时懒连接 MCP 服务器（仅主智能体）。
        if not self._mcp_initialized and not self.is_sub_agent:
            self._mcp_initialized = True
            try:
                await self._mcp_manager.load_and_connect()
                mcp_defs = self._mcp_manager.get_tool_definitions()
                if mcp_defs:
                    self.tools = self.tools + mcp_defs
            except Exception as e:
                print(f"[mcp] Init failed: {e}", flush=True)

        self._aborted = False
        coro = self._chat_openai(user_message) if self.use_openai else self._chat_anthropic(user_message)
        self._current_task = asyncio.current_task()
        try:
            await coro
        except asyncio.CancelledError:
            self._aborted = True
        finally:
            self._current_task = None
        if not self.is_sub_agent:
            # 子智能体结果会回到父 Agent，不单独保存会话或打印分隔线。
            print_divider()
            self._auto_save()

    # ─── 子智能体入口 ─────────────────────────────────

    async def run_once(self, prompt: str) -> dict:
        """作为子智能体执行一次任务，返回最终文本和本次增量 token 用量。"""
        self._output_buffer = []
        prev_in = self.total_input_tokens
        prev_out = self.total_output_tokens
        await self.chat(prompt)
        text = "".join(self._output_buffer)
        self._output_buffer = None
        return {
            "text": text,
            "tokens": {
                "input": self.total_input_tokens - prev_in,
                "output": self.total_output_tokens - prev_out,
            },
        }

    # ─── 输出辅助函数 ─────────────────────────────────

    def _emit_text(self, text: str) -> None:
        if self._output_buffer is not None:
            self._output_buffer.append(text)
        else:
            print_assistant_text(text)

    # ─── 交互命令和预算 ───────────────────────────────

    def clear_history(self) -> None:
        self._anthropic_messages = []
        self._openai_messages = []
        if self.use_openai:
            self._openai_messages.append({"role": "system", "content": self._system_prompt})
        self._active_skills.clear()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.last_input_token_count = 0
        print_info("Conversation cleared.")

    def show_cost(self) -> None:
        total = self._get_current_cost_usd()
        budget_info = f" / ${self.max_cost_usd} budget" if self.max_cost_usd else ""
        turn_info = f" | Turns: {self.current_turns}/{self.max_turns}" if self.max_turns else ""
        print_info(f"Tokens: {self.total_input_tokens} in / {self.total_output_tokens} out\n  Estimated cost: ${total:.4f}{budget_info}{turn_info}")

    def _get_current_cost_usd(self) -> float:
        return (self.total_input_tokens / 1_000_000) * 3 + (self.total_output_tokens / 1_000_000) * 15

    def _check_budget(self) -> dict:
        if self.max_cost_usd is not None and self._get_current_cost_usd() >= self.max_cost_usd:
            return {"exceeded": True, "reason": f"Cost limit reached (${self._get_current_cost_usd():.4f} >= ${self.max_cost_usd})"}
        if self.max_turns is not None and self.current_turns >= self.max_turns:
            return {"exceeded": True, "reason": f"Turn limit reached ({self.current_turns} >= {self.max_turns})"}
        return {"exceeded": False}

    # ─── 会话 ─────────────────────────────────────────

    def restore_session(self, data: dict) -> None:
        if data.get("anthropicMessages"):
            self._anthropic_messages = data["anthropicMessages"]
        if data.get("openaiMessages"):
            self._openai_messages = data["openaiMessages"]
        print_info(f"Session restored ({self._get_message_count()} messages).")

    def _get_message_count(self) -> int:
        return len(self._openai_messages) if self.use_openai else len(self._anthropic_messages)

    def _auto_save(self) -> None:
        try:
            save_session(self.session_id, {
                "metadata": {
                    "id": self.session_id,
                    "model": self.model,
                    "cwd": str(Path.cwd()),
                    "startTime": self.session_start_time,
                    "messageCount": self._get_message_count(),
                },
                "anthropicMessages": self._anthropic_messages if not self.use_openai else None,
                "openaiMessages": self._openai_messages if self.use_openai else None,
            })
        except Exception:
            pass
