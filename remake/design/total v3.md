# NanoCode src/ 重构设计方案 v3

> **设计目标**：以 Agent Runtime 为内核，围绕内核组织能力模块、模型后端和表现层。消除 God-file、God-class、双后端重复代码和薄适配层。方案利于维护和扩展，不过度划分。

---

## 目录

1. [总体设计](#1-总体设计)
2. [详细设计](#2-详细设计)
   - [2.1 cli/ — CLI 入口层](#21-cli--cli-入口层)
   - [2.2 runtime/ — Agent Runtime 内核](#22-runtime--agent-runtime-内核)
   - [2.3 backend/ — 模型后端](#23-backend--模型后端)
   - [2.4 capabilities/ — 能力模块](#24-capabilities--能力模块)
   - [2.5 context/ — 上下文构建](#25-context--上下文构建)
   - [2.6 models.py — 模型元数据](#26-modelspy--模型元数据)
   - [2.7 tui/ server/ protocol/ session/ — 表现层与基础设施](#27-tui-server-protocol-session--表现层与基础设施)
3. [硬性约束与隐含要求](#3-硬性约束与隐含要求)
4. [不能做什么](#4-不能做什么)
5. [可能踩坑的地方](#5-可能踩坑的地方)
6. [代码风格约定](#6-代码风格约定)
7. [附录：文件清单](#7-附录文件清单)

---

## 1. 总体设计

### 1.1 架构全景图

```
                         ┌──────────────────┐
                         │   用户 / 客户端    │
                         └────────┬─────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │                   │                   │
        ┌─────▼─────┐      ┌─────▼─────┐      ┌──────▼──────┐
        │   cli/     │      │   tui/    │      │   server/   │
        │  main.py   │      │  app.py   │      │ app_server  │
        │  args.py   │      │ input.py  │      │ transports/ │
        └─────┬──────┘      │ renderer  │      └──────┬──────┘
              │             │ state.py  │             │
              │             │ commands  │             │
              │             │ theme.py  │             │
              │             └─────┬──────┘             │
              │                   │                   │
              │          使用 / 创建                   │
              │                   │                   │
              └───────────────────┼───────────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │      runtime/  ★内核★      │
                    │  ┌─────────────────────┐  │
                    │  │ agent.py            │  │  Agent 状态容器
                    │  │ loop.py             │  │  主对话循环
                    │  │ compressor.py       │  │  上下文压缩
                    │  │ events.py           │  │  运行时事件
                    │  └─────────────────────┘  │
                    └──────┬───────────┬────────┘
                           │           │
              ┌────────────▼──┐   ┌────▼────────────────┐
              │   backend/    │   │   capabilities/     │
              │  ┌──────────┐ │   │  ┌────────────────┐ │
              │  │ base.py  │ │   │  │ tools/          │ │
              │  │anthropic │ │   │  │ mcp/            │ │
              │  │ openai   │ │   │  │ skills/         │ │
              │  └──────────┘ │   │  │ hooks/          │ │
              └───────────────┘   │  │ memory/         │ │
                                  │  │ sandbox/        │ │
              ┌───────────────┐   │  │ permissions/    │ │
              │   context/    │   │  └────────────────┘ │
              │  builder.py   │   └─────────────────────┘
              │  sources.py   │
              └───────────────┘
              ┌───────────────┐
              │  models.py    │  模型元数据（被各方引用）
              └───────────────┘
              ┌───────────────┐
              │  session/     │  会话持久化
              │  protocol/    │  协议层
              │  logging      │  日志配置
              └───────────────┘
```

### 1.2 模块间依赖关系

依赖方向严格单向，上层可依赖下层，下层不反向引用上层：

```
clients (cli / tui / server)
      │
      ▼
runtime (agent / loop / compressor / events)
      │
      ├────► backend (base / anthropic / openai)
      │
      ├────► capabilities (tools / mcp / skills / hooks / memory / sandbox / permissions)
      │
      ├────► context (builder / sources)
      │
      └────► models.py
```

**红线**：
- `backend/` 不引用 `runtime/`、`capabilities/`、`tui/`
- `capabilities/` 各子模块之间不互相引用
- `runtime/` 不引用 `cli/`、`tui/`、`server/`
- `context/` 不引用 `runtime/` 和 `capabilities/`

### 1.3 关键设计决策

| 决策 | 说明 |
|------|------|
| **Agent 从 Mixin 改为纯状态容器** | Agent 类只持有状态字段，行为通过 `runtime/loop.py`（循环）、`runtime/compressor.py`（压缩）、`backend/`（API 调用）实现。消除 Mixin 的隐式耦合，改为显式组合。 |
| **Backend 从 Mixin 改为策略类** | `backend/base.py` 定义 Backend 接口，`anthropic.py` 和 `openai.py` 各自实现。loop.py 通过接口调用，不再区分 `_run_anthropic` / `_run_openai`。新增模型厂商只需加一个文件。 |
| **capabilities 替代 domains + capabilities** | 当前 `domains/` 和 `capabilities/` 是两层一一对应的目录结构。合并为单层 `capabilities/`，每个子模块自包含：既有数据模型，也有运行时实现。消除薄适配层。 |
| **能力模块保持共同模板** | 每个 `capabilities/<name>/` 都遵循 `types.py`（数据模型）+ 引擎文件（按变更原因拆分）的结构约定。一致性降低学习成本。 |
| **双后端代码通过抽象消除** | loop.py 的单一循环通过 Backend 接口驱动；compressor.py 通过 `MessageStore` 抽象统一操作 Anthropic/OpenAI 消息历史，消除所有 1:1 重复。 |

---

## 2. 详细设计

### 2.1 cli/ — CLI 入口层

**职责**：解析命令行参数，组装依赖，启动应用。

**当前问题**：`__main__.py` 253 行混杂了 argparse 定义、API key 检测、配置解析、一次性模式、交互模式。加参数、改配置、改启动流程全在一个文件。

**重构后**：

```
cli/
├── __init__.py
├── main.py       # 入口 + 依赖组装
└── args.py       # argparse 定义 + 配置解析
```

#### `cli/args.py`

```python
"""命令行参数定义与配置解析。"""

from __future__ import annotations

import argparse
import os

from ..runtime.agent import RuntimeConfig
from ..capabilities.sandbox.config import build_sandbox_config


def parse_args() -> argparse.Namespace:
    """定义并解析所有 CLI 参数。"""
    parser = argparse.ArgumentParser(
        prog="nanocode",
        description="Nano Code — a lightweight coding agent",
        add_help=False,
    )
    parser.add_argument("prompt", nargs="*", help="One-shot prompt")
    parser.add_argument("--server", choices=["stdio"], default=None)
    parser.add_argument("--yolo", "-y", action="store_true")
    parser.add_argument("--accept-edits", action="store_true")
    parser.add_argument("--dont-ask", action="store_true")
    parser.add_argument("--thinking", action="store_true")
    parser.add_argument("--model", "-m", default=None)
    parser.add_argument("--api-base", default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-cost", type=float, default=None)
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument("--sandbox", choices=[...], default=None)
    parser.add_argument("--sandbox-network", choices=["none", "default"], default=None)
    parser.add_argument("--sandbox-image", default=None)
    parser.add_argument("--sandbox-memory", type=int, default=None)
    parser.add_argument("--sandbox-cpus", type=int, default=None)
    parser.add_argument("--sandbox-readonly-workspace", action="store_true")
    parser.add_argument("--sandbox-no-network", action="store_true")
    parser.add_argument("--sandbox-env", action="append", default=None)
    parser.add_argument("--sandbox-extra-write", action="append", default=None)
    parser.add_argument("--sandbox-allow-local-fallback", action="store_true")
    parser.add_argument("--help", "-h", action="store_true")
    return parser.parse_args()


def resolve_permission_mode(args: argparse.Namespace) -> str:
    """根据 CLI 参数解析权限模式。"""
    if args.yolo:
        return "bypassPermissions"
    if args.accept_edits:
        return "acceptEdits"
    if args.dont_ask:
        return "dontAsk"
    return "default"


def resolve_runtime_config(args: argparse.Namespace) -> RuntimeConfig:
    """将 CLI 参数和环境变量合并为 RuntimeConfig。

    变更原因：加新的 CLI 参数时，只需要在此函数中增加映射逻辑。
    """
    permission_mode = resolve_permission_mode(args)
    try:
        sandbox_config = build_sandbox_config(args)
    except ValueError:
        raise

    model = args.model or os.environ.get("NANO_CODE_MODEL", "claude-opus-4-6")
    api_base = args.api_base

    resolved_api_base = api_base
    resolved_api_key: str | None = None
    resolved_use_openai = bool(api_base)

    # 按优先级解析 API key 和 provider
    if os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_BASE_URL"):
        resolved_api_key = os.environ["OPENAI_API_KEY"]
        resolved_api_base = resolved_api_base or os.environ.get("OPENAI_BASE_URL")
        resolved_use_openai = True
    elif os.environ.get("ANTHROPIC_API_KEY"):
        resolved_api_key = os.environ["ANTHROPIC_API_KEY"]
        resolved_api_base = resolved_api_base or os.environ.get("ANTHROPIC_BASE_URL")
        resolved_use_openai = False
    elif os.environ.get("OPENAI_API_KEY"):
        resolved_api_key = os.environ["OPENAI_API_KEY"]
        resolved_api_base = resolved_api_base or os.environ.get("OPENAI_BASE_URL")
        resolved_use_openai = True

    if not resolved_api_key and api_base:
        resolved_api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        resolved_use_openai = True

    return RuntimeConfig(
        permission_mode=permission_mode,
        model=model,
        provider="openai" if resolved_use_openai else "anthropic",
        thinking=args.thinking,
        max_cost_usd=args.max_cost,
        max_turns=args.max_turns,
        api_base=resolved_api_base if resolved_use_openai else None,
        anthropic_base_url=resolved_api_base if not resolved_use_openai else None,
        api_key=resolved_api_key,
        sandbox_config=sandbox_config,
    )
```

#### `cli/main.py`

```python
"""CLI 入口 — 依赖组装，启动应用。"""

from __future__ import annotations

import asyncio
import sys

from .args import parse_args, resolve_runtime_config
from ..runtime.agent import Agent
from ..runtime.loop import AgentLoop
from ..backend import create_backend
from ..logging_config import setup_logging, get_logger
from ..session import load_session, get_latest_session_id

logger = get_logger("cli")


def main() -> None:
    """NanoCode CLI 入口。

    流程：
    1. 解析 CLI 参数
    2. 组装 RuntimeConfig
    3. 创建 Agent（内核） + Backend（模型后端）
    4. 根据模式启动 TUI / 一次性执行 / Server
    """
    setup_logging()
    args = parse_args()

    if args.help:
        _print_help()
        sys.exit(0)

    if args.server == "stdio":
        from ..server.transports.stdio import run_stdio_server
        asyncio.run(run_stdio_server())
        return

    try:
        config = resolve_runtime_config(args)
    except ValueError as e:
        from ..tui.renderer import get_renderer
        get_renderer().error(str(e))
        sys.exit(1)

    if not config.api_key:
        from ..tui.renderer import get_renderer
        get_renderer().error(
            "API key is required.\n"
            "  Set ANTHROPIC_API_KEY or OPENAI_API_KEY + OPENAI_BASE_URL."
        )
        sys.exit(1)

    agent = Agent(config)
    backend = create_backend(config)
    loop = AgentLoop(agent, backend)

    prompt = " ".join(args.prompt) if args.prompt else None

    if prompt:
        asyncio.run(_run_once(loop, prompt, config))
    else:
        asyncio.run(_run_interactive(agent, loop, config))


async def _run_once(loop, prompt: str, config) -> None:
    """一次性执行模式。"""
    from ..tui.renderer import get_renderer

    async def confirm(message: str) -> bool:
        get_renderer().confirm(message)
        try:
            answer = await asyncio.to_thread(input, "  Allow? (y/n): ")
            return answer.lower().startswith("y")
        except EOFError:
            return False

    loop.agent.set_confirm_fn(confirm)
    try:
        await loop.run(prompt)
    except Exception as e:
        logger.error("CLI fatal error: %s", e, exc_info=True)
        get_renderer().error(str(e))
        sys.exit(1)
    finally:
        await loop.agent.shutdown()


async def _run_interactive(agent, loop, config) -> None:
    """交互式 TUI 模式。"""
    from ..tui.app import TuiApp

    try:
        await TuiApp(agent, loop).run()
    finally:
        await agent.shutdown()


def _print_help() -> None:
    """打印帮助信息。保持与原版一致的输出。"""
    print("""
Usage: nanocode [options] [prompt]
...（帮助文本与原版保持一致）...
""")


if __name__ == "__main__":
    main()
```

**与 runtime/ 的关系**：`cli/main.py` 创建 Agent（内核）、Backend（后端）、AgentLoop（循环），交给 TUI 或一次性模式驱动。自己不包含任何对话逻辑。

---

### 2.2 runtime/ — Agent Runtime 内核

**职责**：管理 Agent 的一次对话生命周期——状态、循环、压缩、事件。

**当前问题**：Agent 类通过 3 个 Mixin（ContextMixin、ToolRuntimeMixin、BackendMixin）拼装，Mixin 通过 `self._anthropic_messages` 等方式隐式访问状态。阅读时需要在 4 个文件中来回跳转才能拼出完整行为。

**重构后**：

```
runtime/
├── __init__.py
├── agent.py       # Agent 状态容器
├── loop.py        # 主对话循环（后端无关）
├── compressor.py  # 上下文压缩策略
└── events.py      # 运行时事件定义
```

#### 2.2.1 `runtime/agent.py` — Agent 状态容器

```python
"""Agent 状态容器。

本模块是 Agent 的数据面。它持有一次对话的所有状态字段，
但不实现对话循环、API 调用、压缩策略等行为。这些行为由
runtime/loop.py、backend/、runtime/compressor.py 分别实现。

这样设计的理由：
- 状态和行为的变更原因不同（加字段 vs 改循环策略 vs 改压缩策略）
- 子 Agent fork 可以复用同一个状态容器
- 单元测试可以独立测试状态变更，不依赖后端
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

from .events import RuntimeEvent
from ..capabilities.sandbox.config import SandboxConfig
from ..capabilities.sandbox.manager import SandboxManager
from ..capabilities.mcp.manager import McpManager
from ..capabilities.hooks.runner import HookManager
from ..capabilities.skills.runtime import SkillRuntime
from ..capabilities.memory.retrieval import MemoryPrefetch
from ..capabilities.tools.registry import ToolRegistry
from ..capabilities.tools.definitions import builtin_tool_definitions


@dataclass
class RuntimeConfig:
    """Agent 运行时配置。"""
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

    子 Agent fork 复用此类，通过 custom_system_prompt 和
    custom_tools 定制行为边界。
    """

    def __init__(
        self,
        config: RuntimeConfig,
        *,
        custom_tools: list[dict] | None = None,
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
        self._tool_registry = ToolRegistry(
            custom_tools if custom_tools is not None else builtin_tool_definitions()
        )

        # ── 沙箱 ──
        self._sandbox_manager = sandbox_manager or SandboxManager(
            config.sandbox_config, session_id=self.session_id
        )

        # ── 能力模块 ──
        self._skill_runtime = SkillRuntime()
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

        # ── 权限 ──
        self._confirmed_paths: set[str] = set()
        self._confirm_fn: Callable[[str], Awaitable[bool]] | None = None

        # ── 文件状态 ──
        self._read_file_state: dict[str, float] = {}

        # ── 输出缓冲 ──
        self._output_buffer: list[str] | None = None

        # ── 上下文 ──
        self._anthropic_messages: list[dict] = []
        self._openai_messages: list[dict] = []
        self._pending_context_attachments: list[str] = []
        self._sent_skill_names: set[str] = set()
        self._sent_deferred_tool_names: set[str] = set()
        self._initial_context_attachments_prepared = False
        self._startup_context_injected = False

        # ── 记忆 ──
        self._already_surfaced_memories: set[str] = set()
        self._session_memory_bytes = 0
        self._memory_prefetch: MemoryPrefetch | None = None

        # ── 诊断 ──
        self._diagnostics: list[str] = []

        # ── 系统提示词 ──
        self._base_system_prompt = config.custom_system_prompt or self._build_system_prompt()
        self._system_prompt = self._base_system_prompt
        self._startup_context = self._build_startup_context()

    # ─── 公开方法 ────────────────────────────────

    def set_confirm_fn(self, fn: Callable[[str], Awaitable[bool]]) -> None:
        self._confirm_fn = fn

    @property
    def aborted(self) -> bool:
        return self._aborted

    def abort(self) -> None:
        self._aborted = True

    @property
    def messages(self) -> list[dict]:
        """返回当前后端对应的消息历史。"""
        return self._openai_messages if self.config.use_openai else self._anthropic_messages

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def effective_window(self) -> int:
        from ..models import get_context_window
        from ..capabilities.tools.constants import CONTEXT_WINDOW_MARGIN
        return get_context_window(self.model) - CONTEXT_WINDOW_MARGIN

    def tool_definitions(self) -> list[dict]:
        return self._tool_registry.active_definitions(
            denied=self._skill_runtime.disallowed_tools()
        )

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
        """追加用户消息。"""
        if self.config.use_openai:
            self._openai_messages.append({"role": "user", "content": content})
        else:
            self._anthropic_messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: list[dict]) -> None:
        """追加 assistant 消息（包含 text 和 tool_use block）。"""
        if self.config.use_openai:
            self._openai_messages.append(content)
        else:
            self._anthropic_messages.append({"role": "assistant", "content": content})

    def add_tool_results(self, results: list[dict]) -> None:
        """追加工具结果。"""
        if self.config.use_openai:
            for r in results:
                self._openai_messages.append(r)
        else:
            self._anthropic_messages.append({"role": "user", "content": results})

    def append_user_context(self, text: str) -> None:
        """把补充上下文追加到最新用户消息后面。"""
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
                    content.append({"type": "text", "content": text})
            else:
                self._anthropic_messages.append({"role": "user", "content": text})

    def restore_session(self, data: dict) -> None:
        if data.get("anthropicMessages"):
            self._anthropic_messages = data["anthropicMessages"]
        if data.get("openaiMessages"):
            self._openai_messages = data["openaiMessages"]
        if self._anthropic_messages or len(self._openai_messages) > 1:
            self._startup_context_injected = True

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

    # ─── 内部 ────────────────────────────────────

    def _build_system_prompt(self) -> str:
        from ..context.builder import build_system_prompt
        return build_system_prompt()

    def _build_startup_context(self) -> str:
        if self.is_sub_agent or self.config.custom_system_prompt:
            return ""
        from ..context.builder import build_startup_context
        return build_startup_context()

    def _on_mcp_tool_delta(self, delta, definitions) -> None:
        # 处理 MCP 工具变更通知
        removed = set(getattr(delta, "removed", []) or [])
        if removed:
            self._tool_registry.remove_many(removed)
        added = set(getattr(delta, "added", []) or [])
        changed = set(getattr(delta, "changed", []) or [])
        wanted = added | changed
        if wanted:
            self._tool_registry.replace_many(
                [d for d in definitions if d.get("name") in wanted],
                origin="mcp",
                default_concurrency_safe=False,
            )
```

**设计决策**：

- `RuntimeConfig` 与 `Agent` 放在同一个文件——它们共享同一份变更原因（"Agent 的配置字段 + 状态字段"），加一个配置项时往往也要知道对应的状态字段。
- Agent **不** import backend/——它不知道谁会调用模型，只提供 `messages`、`system_prompt`、`tool_definitions()` 的访问接口。
- Agent **不** import loop.py——循环依赖状态，状态不依赖循环。

#### 2.2.2 `runtime/loop.py` — 主对话循环

```python
"""主对话循环 — 后端无关的事件驱动循环。

当前 agent/loop.py 中有 `_run_anthropic` 和 `_run_openai` 两个方法，
它们 80% 的代码相同。本文件通过 Backend 接口统一循环逻辑，
消除重复。

流程：
  用户输入 → 注入上下文 → 记忆召回 → [模型调用 → 解析响应 → 执行工具] × N
"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

from ..backend.base import Backend
from ..capabilities.hooks.types import HookInput
from ..capabilities.tools.base import ToolCall, ToolContext, ToolResult
from ..capabilities.tools.runtime import ToolRuntime
from .events import (
    RuntimeEvent,
    AssistantTextDelta,
    BudgetExceeded,
    LoopFinished,
    PermissionRequested,
    ToolCallFinished,
    ToolCallStarted,
)


class AgentLoop:
    """后端无关的主对话循环。

    通过 Backend 接口调用模型，不区分 Anthropic / OpenAI 差异。
    """

    def __init__(self, agent, backend: Backend):
        self.agent = agent
        self.backend = backend

    async def run(self, user_message: str) -> AsyncIterator[RuntimeEvent]:
        """执行一次完整的对话轮次。

        产出一系列 RuntimeEvent，供 TUI / CLI / Server 消费。
        """
        agent = self.agent

        # 1. 注入启动上下文（仅首次）
        self._inject_startup_context()

        # 2. 准备初始上下文附件（仅首次）
        self._prepare_initial_context_attachments()

        # 3. 刷新挂起的附件
        self._flush_pending_attachments()

        # 4. 添加用户消息
        agent.add_user_message(user_message)

        # 5. 检查是否需要 compact
        await self._check_and_compact()

        # 6. 启动记忆预取
        memory_prefetch = agent.start_memory_prefetch(user_message)

        # 7. 主循环
        while True:
            if agent.aborted:
                yield LoopFinished("aborted")
                return

            # 压缩流水线
            agent.run_compression_pipeline()

            # 消费记忆预取结果
            agent.consume_memory_prefetch(memory_prefetch)

            # 调用模型
            try:
                response = await self._call_model()
            except Exception as exc:
                yield RuntimeEvent(type="runtime.error", payload={"message": str(exc)})
                yield LoopFinished("error")
                return

            agent.last_api_call_time = time.time()

            # 没有工具调用 → 对话结束（检查 Stop hook）
            if not response.tool_calls:
                if await self._run_stop_hook(response.text):
                    continue  # Hook 要求继续
                yield LoopFinished("stop")
                return

            # 有工具调用 → 执行工具
            agent.current_turns += 1
            budget = agent.budget_exceeded()
            if budget["exceeded"]:
                yield BudgetExceeded(budget["reason"])
                yield LoopFinished("budget_exceeded")
                return

            # 执行工具
            for call in response.tool_calls:
                yield ToolCallStarted(call)

            events, results = await self._execute_tools(response.tool_calls)

            for event in events:
                yield event

            agent.add_tool_results(self._format_tool_results(results))

            for result in results:
                yield ToolCallFinished(result.call, result)

            # 追加额外上下文
            self._append_extra_context(results)

            # 刷新挂起的附件
            self._flush_pending_attachments()

    async def _call_model(self):
        """委托给 Backend 调用模型，返回统一的 BackendResponse。"""
        return await self.backend.call(
            messages=self.agent.messages,
            system=self.agent.system_prompt,
            tools=self.agent.tool_definitions(),
            on_text_delta=lambda text: self.agent.emit_text(text),
            thinking_mode=self.agent.thinking_mode,
        )

    async def _execute_tools(
        self, calls: list[ToolCall]
    ) -> tuple[list[RuntimeEvent], list[ToolResult]]:
        """执行工具管线：验证 → 权限 → 执行 → 后处理。"""
        agent = self.agent
        events: list[RuntimeEvent] = []

        async def capture(event) -> None:
            events.append(event)

        runtime = ToolRuntime(
            agent._tool_registry,
            permission_mode=agent.permission_mode,
            confirm_fn=agent._confirm_dangerous,
            confirmed=agent._confirmed_paths,
            hooks=agent._hook_manager,
            event_callback=capture,
        )

        ctx = ToolContext(
            cwd=agent.config.workspace,
            session_id=agent.session_id,
            read_file_state=agent._read_file_state,
            sandbox_manager=agent._sandbox_manager,
            mcp_manager=agent._mcp_manager,
            agent=agent,
        )

        return events, await runtime.execute_many(calls, ctx)

    # ─── 上下文管理辅助 ────────────────────────────

    def _inject_startup_context(self) -> None:
        agent = self.agent
        if agent._startup_context_injected:
            return
        context = agent._startup_context
        if context:
            agent.append_user_context(context)
        agent._startup_context_injected = True

    def _prepare_initial_context_attachments(self) -> None:
        # 原 AgentContextMixin._prepare_initial_context_attachments 的逻辑
        ...

    def _flush_pending_attachments(self) -> None:
        ...

    def _append_extra_context(self, results: list[ToolResult]) -> None:
        ...

    async def _check_and_compact(self) -> None:
        ...

    async def _run_stop_hook(self, last_text: str) -> bool:
        ...
```

**关键设计决策**：

- loop.py import `backend.base.Backend`（接口），不 import `backend.anthropic`（实现）——依赖倒置。
- `_call_model()` 方法 ~10 行，不再有 Anthropic 和 OpenAI 两套 100+ 行的分支。
- 工具执行委托给 `capabilities/tools/runtime.py` 的 `ToolRuntime`，loop 只管编排不管细节。

#### 2.2.3 `runtime/compressor.py` — 上下文压缩

```python
"""上下文压缩策略。

三层压缩流水线：
  1. Budget — 按字符预算裁剪超长工具结果
  2. Snip   — 替换陈旧文件读取结果为占位符
  3. Microcompact — 空闲一段时间后清除旧结果

以及 compact 操作——通过调用模型生成对话摘要，压缩消息历史。

与当前实现的区别：
- 当前 agent/context.py 中 Anthropic 和 OpenAI 的压缩方法各写一遍
- 本文件通过 agent.messages 属性统一操作，消除重复
"""

class Compressor:
    """Agent 的上下文压缩策略实现。

    从 Agent 的状态容器读取消息历史，执行压缩后写回。
    不依赖 Backend——compact 时自行调用模型客户端。
    """

    def __init__(self, agent):
        self.agent = agent

    def run_compression_pipeline(self) -> None:
        """按顺序执行三层压缩。"""
        self._budget_results()
        self._snip_stale_results()
        self._microcompact()

    async def compact_conversation(self) -> None:
        """生成对话摘要，重置消息历史。"""
        ...

    def _budget_results(self) -> None:
        """第 1 层：裁剪超长工具结果。"""
        utilization = self.agent.last_input_token_count / self.agent.effective_window
        if utilization < BUDGET_UTILIZATION_THRESHOLD:
            return
        budget = BUDGET_HIGH if utilization > BUDGET_HIGH_UTILIZATION else BUDGET_MEDIUM
        for msg in self.agent.messages:
            self._budget_message(msg, budget)

    def _snip_stale_results(self) -> None:
        """第 2 层：替换陈旧结果。"""
        ...

    def _microcompact(self) -> None:
        """第 3 层：空闲后清除旧结果。"""
        ...

    # 通过 agent.messages 统一操作，不再区分 Anthropic/OpenAI
    def _budget_message(self, msg: dict, budget: int) -> None:
        ...
```

#### 2.2.4 `runtime/events.py` — 运行时事件

```python
"""运行时事件定义。

合并了现存的 runtime/events.py（RuntimeEvent + TurnResult）
和 agent/events.py（AgentEvent 的各种子类）。统一为一套事件模型。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..capabilities.tools.base import ToolCall, ToolResult


@dataclass(frozen=True)
class RuntimeEvent:
    """统一运行时事件。"""
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=lambda: time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }


# ─── 常用事件的工厂函数 ────────────────────────────


def AssistantTextDelta(text: str) -> RuntimeEvent:
    return RuntimeEvent(type="assistant.delta", payload={"text": text})


def ToolCallStarted(call: ToolCall) -> RuntimeEvent:
    return RuntimeEvent(type="tool.started", payload={
        "id": call.id, "name": call.name, "input": call.input, "provider": call.provider,
    })


def ToolCallFinished(call: ToolCall, result: ToolResult) -> RuntimeEvent:
    return RuntimeEvent(type="tool.finished", payload={
        "id": call.id, "name": call.name, "content": result.content,
        "is_error": result.is_error, "metadata": result.metadata,
    })


def PermissionRequested(call: ToolCall, message: str) -> RuntimeEvent:
    return RuntimeEvent(type="approval.requested", payload={
        "call_id": call.id, "tool_name": call.name, "message": message,
    })


def BudgetExceeded(reason: str) -> RuntimeEvent:
    return RuntimeEvent(type="budget.exceeded", payload={"reason": reason})


def LoopFinished(stop_reason: str) -> RuntimeEvent:
    return RuntimeEvent(type="turn.finished", payload={"stop_reason": stop_reason})
```

**设计决策**：用工厂函数替代子类。Python 的 dataclass 继承在大型项目中容易产生复杂的 isinstance 判断链。工厂函数更简洁，类型检查直接用 `event.type == "tool.started"`，不需要 `isinstance(event, ToolCallStarted)`。

---

### 2.3 backend/ — 模型后端

**职责**：封装模型 API 的调用细节（流式/非流式、消息格式、tool schema 转换）。上层只看到统一的 `Backend` 接口。

**当前问题**：`AgentBackendMixin` 通过 `self._anthropic_client` 等方式直接访问 Agent 实例的字段，与 Agent 状态强耦合。新增模型厂商需要修改 Mixin 并重新接入 Agent。

**重构后**：

```
backend/
├── __init__.py
├── base.py         # Backend 接口 + 统一返回类型
├── anthropic.py    # Anthropic 流式后端
└── openai.py       # OpenAI 兼容流式后端
```

#### `backend/base.py`

```python
"""Backend 接口与统一返回类型。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Awaitable

from ..capabilities.tools.base import ToolCall


@dataclass
class BackendResponse:
    """后端返回的统一结构。

    不管是 Anthropic 还是 OpenAI，对外暴露相同的字段。
    """
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=lambda: TokenUsage())


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0


class Backend(ABC):
    """模型后端抽象接口。

    每种模型厂商（Anthropic、OpenAI 等）提供各自的实现。
    """

    @abstractmethod
    async def call(
        self,
        *,
        messages: list[dict],
        system: str,
        tools: list[dict],
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
        thinking_mode: str = "disabled",
    ) -> BackendResponse:
        """调用模型，返回统一的 BackendResponse。"""
        ...

    @abstractmethod
    def supports_thinking(self, model: str) -> bool:
        """检查模型是否支持 extended thinking。"""
        ...
```

#### `backend/anthropic.py`

```python
"""Anthropic Messages API 流式后端。"""

class AnthropicBackend(Backend):
    def __init__(self, api_key: str, base_url: str | None = None, model: str = "claude-opus-4-6"):
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = anthropic.AsyncAnthropic(**kwargs)
        self.model = model

    async def call(self, *, messages, system, tools, on_text_delta=None, thinking_mode="disabled") -> BackendResponse:
        """流式调用 Anthropic API，返回 BackendResponse。"""
        # 原 AgentBackendMixin._call_anthropic_stream 的逻辑
        # 但不依赖 self.agent，所有数据通过参数传入
        ...
```

#### `backend/openai.py`

```python
"""OpenAI Chat Completions 兼容流式后端。"""

class OpenAIBackend(Backend):
    def __init__(self, api_key: str, base_url: str, model: str):
        self.client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    async def call(self, *, messages, system, tools, on_text_delta=None, thinking_mode="disabled") -> BackendResponse:
        """流式调用 OpenAI API，返回 BackendResponse。"""
        ...
```

#### `backend/__init__.py`

```python
"""Backend 工厂。"""

from .base import Backend, BackendResponse, TokenUsage
from .anthropic import AnthropicBackend
from .openai import OpenAIBackend


def create_backend(config) -> Backend:
    """根据 RuntimeConfig 创建对应的 Backend 实例。"""
    if config.use_openai:
        return OpenAIBackend(
            api_key=config.api_key,
            base_url=config.api_base,
            model=config.model,
        )
    return AnthropicBackend(
        api_key=config.api_key,
        base_url=config.anthropic_base_url,
        model=config.model,
    )


__all__ = ["Backend", "BackendResponse", "TokenUsage", "AnthropicBackend", "OpenAIBackend", "create_backend"]
```

---

### 2.4 capabilities/ — 能力模块

**职责**：Agent 的所有可插拔能力——工具使用、MCP 集成、Hook 响应、技能执行、记忆、沙箱、权限。

**当前问题**：`domains/` 和 `capabilities/` 两层一一对应的目录结构。`domains/tools/` 和 `capabilities/tools/` 之间存在薄适配层，只是把方法调用从 domains 转发到 runtime。每次需要同时理解和修改两个目录。

**重构后**：合并为单层 `capabilities/`。每个子模块自包含——既有类型定义也有运行时实现。

**共同模板**：

```
capabilities/<name>/
├── __init__.py       # 公开导出
├── types.py          # ★ 共同模板 — 数据模型/类型定义
├── config.py         # 共同模板 — 配置（如有）
└── <engine>.py       # 运行时引擎 — 按独立变更原因拆 1~N 个
```

#### 2.4.1 `capabilities/tools/` — 工具系统（5 个文件）

```
tools/
├── __init__.py
├── types.py       # 数据模型：ToolDef, ToolMetadata, ToolOrigin,
│                  #            ToolCall, ToolContext, ToolResult
│                  #            FunctionTool, PermissionMode
│                  #            + 全部工具相关常量
├── builtin.py     # 内置工具：schema 定义 + 实现函数
├── registry.py    # 注册中心：ToolRegistry（增删查、deferred 激活）
└── runtime.py     # 执行管线：ToolRuntime（验证→权限→执行→后处理）
```

**变更原因分析**：

| 文件 | 独立变更原因 | 与谁可能一起改 |
|------|-------------|---------------|
| `types.py` | 改工具的数据结构约定 | — |
| `builtin.py` | 新增/修改/删除内置工具 | types.py（改 schema 时） |
| `registry.py` | 改注册机制（如 deferred 激活策略） | — |
| `runtime.py` | 改执行管线（如并发调度、Hook 集成） | — |

`types.py` 和 `builtin.py` 分开的原因是：改一个 ToolDef 的字段（如新增 `ephemeral` 标记）不需要改任何工具的 schema 定义；反之改某个工具的 description 也不涉及数据模型层面。

`builtin.py` 包含原先的 `definitions.py`（schema）+ `builtin.py`（实现），因为加一个新工具时两者**必须同时修改**。

`types.py` 合并了原先的 `types.py` + `base.py` + `constants.py`，因为 ToolDef（schema 面）和 ToolCall（运行时面）共享同一个变更域——改工具的输入输出约定。

#### 2.4.2 `capabilities/mcp/` — MCP 集成（6 个文件）

```
mcp/
├── __init__.py
├── types.py       # 共同模板 — MCP 数据结构
├── config.py      # 共同模板 — MCP 配置
├── manager.py     # 生命周期管理：加载配置、连接/断开、工具变更通知
├── connection.py  # 协议连接：initialize/initialized 握手、消息收发
├── transport.py   # 底层传输：stdio pipe / SSE HTTP
└── resources.py   # 资源操作：list / read
```

**变更原因分析**：

| 文件 | 独立变更原因 |
|------|-------------|
| `types.py` | 改 MCP 协议的数据类型 |
| `config.py` | 改 MCP 配置格式（如新增 server 配置字段） |
| `manager.py` | 改管理策略（如延迟连接、故障恢复） |
| `connection.py` | 改连接协议（如更新 MCP 协议版本适配） |
| `transport.py` | 改底层传输（如新增 WebSocket transport） |
| `resources.py` | 改资源操作（如新增资源类型支持） |

manager、connection、transport 三者分开是关键决策。加一个 WebSocket transport 只改 `transport.py`，改 MCP 握手逻辑只改 `connection.py`，两者变更原因独立。

#### 2.4.3 `capabilities/skills/` — 技能系统（4 个文件）

```
skills/
├── __init__.py
├── types.py       # 共同模板 — Skill 数据结构
├── registry.py    # Skill 发现：扫描路径、解析 frontmatter、查找
├── runtime.py     # Skill 运行时：调用 + 激活状态管理
└── prompt.py      # 提示词渲染：Skill 的 prompt 模板渲染
```

**变更原因分析**：

| 文件 | 独立变更原因 |
|------|-------------|
| `types.py` | 改 Skill 的数据结构 |
| `registry.py` | 改 Skill 发现机制（如扫描新路径） |
| `runtime.py` | 改运行时行为（如 skill fork 策略、工具过滤） |
| `prompt.py` | 改提示词格式（如新增 prompt 变量） |

原先的 `invocation.py` + `active.py` 合并为 `runtime.py`——调用一个 skill 和跟踪其激活状态是同一活动的两面。invoke 之后必然 record，compact 之后必然 reattach。不存在独立变更的需求。

#### 2.4.4 `capabilities/hooks/` — Hook 系统（3 个文件）

```
hooks/
├── __init__.py
├── types.py       # 共同模板 — HookInput, HookOutput, HookEvent
├── config.py      # 共同模板 — Hook 配置（加载、信任验证）
└── runner.py      # 执行引擎：HookManager（触发 Hook、收集结果）
```

#### 2.4.5 `capabilities/memory/` — 记忆系统（4 个文件）

```
memory/
├── __init__.py
├── types.py       # 共同模板 — Memory, MemoryPrefetch
├── store.py       # 持久化：文件读写、mark_accessed
├── retrieval.py   # 召回引擎：语义预取 + 结果格式化注入
└── consolidation.py # 整理引擎：合并、总结历史记忆
```

原先的 `rendering.py` 合并进 `retrieval.py`——召回记忆和格式化注入文本是同一个流程的头尾，改召回策略通常也要调整呈现格式。

#### 2.4.6 `capabilities/sandbox/` — 沙箱执行（6 个文件）

```
sandbox/
├── __init__.py
├── types.py              # 共同模板 — Sandbox 数据结构
├── config.py             # 共同模板 — SandboxConfig + build_sandbox_config()
├── manager.py            # 生命周期：选择后端、管理实例
├── backend.py            # 后端接口：run_shell 抽象方法
├── bwrap_backend.py      # Bubblewrap 实现
└── microsandbox_backend.py # Microsandbox 实现
```

#### 2.4.7 `capabilities/permissions/` — 权限检查（4 个文件）

```
permissions/
├── __init__.py
├── policy.py      # 权限策略：check_permission() 主入口
├── rules.py       # 权限规则：文件/命令的白名单黑名单
├── workspace.py   # 工作区权限：文件读写路径检查
└── shell.py       # Shell 权限：命令安全检查
```

---

### 2.5 context/ — 上下文构建

**职责**：构建 Agent 发送给模型前的 system prompt 和 startup context。

**当前问题**：`domains/context/` 8 个文件，其中 `types.py` 仅 1 个 dataclass、`startup.py` 仅 ~50 行、`attachments.py` ~80 行且与 builder 紧密耦合。阅读时需要跨 8 个文件拼出完整上下文。

**重构后**：

```
context/
├── __init__.py
├── builder.py     # 组装：system prompt + startup context
└── sources.py     # 数据源：CLAUDE.md 解析、Git 状态、frontmatter
```

**变更原因分析**：

| 文件 | 职责 | 独立变更原因 |
|------|------|-------------|
| `builder.py` | 将各种数据源组装成完整 system prompt | 改 prompt 的结构/顺序/注入方式 |
| `sources.py` | 从文件系统中提取上下文数据 | 改数据源（如新增 README 上下文、自定义 CLAUDE.md 路径） |

---

### 2.6 models.py — 模型元数据

**职责**：模型相关的静态元数据——上下文窗口大小、thinking 能力、最大输出 token、API 重试策略。被 `runtime/`、`backend/`、`cli/` 共用。

**提升到顶层的原因**：当前埋在 `agent/models.py`，但 `backend/anthropic.py` 和 `cli/args.py` 也需要上下文窗口大小来决定 default model。放在 `agent/` 下会导致 `backend/` 反向引用 `runtime/agent/`。

```python
"""模型元数据。

被 runtime/、backend/、cli/ 共同引用。
不依赖任何项目中的其他模块。
"""

# 上下文窗口（必须是字面量映射，用于快速查找）
MODEL_CONTEXT_WINDOW: dict[str, int] = {
    "claude-opus-4-6": 200000,
    "claude-sonnet-4-6": 200000,
    "claude-haiku-4-5-20251001": 200000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
}

# Thinking 能力检查
THINKING_MODELS = {"claude-opus-4-6", "claude-sonnet-4-6", "claude-sonnet-4-20250514"}
ADAPTIVE_THINKING_MODELS = {"claude-opus-4-6", "claude-sonnet-4-6"}

# 最大输出 token
MODEL_MAX_OUTPUT: dict[str, int] = {
    "claude-opus-4-6": 64000,
    "claude-sonnet-4-6": 32000,
}


def get_context_window(model: str) -> int:
    """返回模型的上下文窗口大小。"""
    for prefix, window in MODEL_CONTEXT_WINDOW.items():
        if model.startswith(prefix):
            return window
    return 200000


def model_supports_thinking(model: str) -> bool:
    return model.lower() in THINKING_MODELS


def model_supports_adaptive_thinking(model: str) -> bool:
    return model.lower() in ADAPTIVE_THINKING_MODELS


def get_max_output_tokens(model: str) -> int:
    return MODEL_MAX_OUTPUT.get(model.lower(), 8192)
```

---

### 2.7 tui/ server/ protocol/ session/ — 表现层与基础设施

这四部分**保持现有结构不变**。原因是它们当前结构已经合理，不存在过度划分或 God-class 问题。

**tui/**（5 文件）：app / input / renderer / state / commands / theme —— 每个文件职责清晰，变更原因独立。

**server/**（4 文件）：app_server + 3 种 transport —— transport 之间变更原因独立。

**protocol/**（2 文件）：messages + dispatcher —— 改消息格式不改分发逻辑，反之亦然。

**session/**（3 文件）：event_store / artifacts / snapshots —— 各管一种持久化内容。

---

## 3. 硬性约束与隐含要求

### 3.1 硬性约束

| 约束 | 说明 |
|------|------|
| **不改变外部接口** | `RuntimeEvent` 的 JSON 格式、CLI 参数名、Server 的 JSONL 协议保持不变。TUI 用户不应感知到重构。 |
| **不改变工具 schema** | 内置工具的 input_schema 定义（给模型的 tool definition）保持不变。重构内部代码，不改变模型看到的东西。 |
| **不改变会话格式** | `session/*.json` 的存储格式保持不变，以确保已有会话可被 resume。 |
| **Python 3.10+** | 保持与当前 `pyproject.toml` 一致的 Python 版本要求。 |
| **子 Agent fork 机制不变** | Agent(..., is_sub_agent=True) + run_once() 的调用方式保持不变，内部代码仍使用。 |

### 3.2 隐含要求

| 要求 | 说明 |
|------|------|
| **可测试性** | 每个模块应可独立单元测试。Backend 可以通过 mock 测试 loop。Agent 状态可以通过纯数据测试 compressor。 |
| **可调试性** | 重构后不应增加调试难度。事件类型的字符串值应与原版一致，方便按事件名搜索日志。 |
| **渐进迁移** | 不要求一次性重构全部文件。可以按模块顺序渐进完成：先 backend/，再 runtime/，再 cli/，再 capabilities/。每步完成后测试应通过。 |
| **命名一致** | 模块名、文件名、类名、函数名在全项目中应有一致的风格。不出现同一个概念在不同地方叫不同名字。 |

---

## 4. 不能做什么

### 4.1 不要引入新的依赖或框架

- 不要引入 DI 容器（如 `dependency-injector`）
- 不要引入事件总线框架（如 `pyee`、`blinker`）
- 不要引入 ORM 或新的持久化方案
- 不要引入新的 CLI 框架（如 `click`、`typer`）——保持 `argparse`

### 4.2 不要改变核心流程

- 不要改 Agent 的对话循环策略（工具执行顺序、并发策略、压缩时机）
- 不要改权限检查的规则（`--yolo` / `--accept-edits` / `--dont-ask` 的行为）
- 不要改 MCP 的连接和重连机制
- 不要改 Skill 的 fork 和 inline 两种模式

### 4.3 不要过度抽象

- 不要为"将来可能的扩展"创建抽象——只为"当前已存在的变更原因"拆分模块
- `Backend` 接口只需要 `call()` 和 `supports_thinking()` 两个方法——不需要 `stream()`、`count_tokens()`、`validate()` 等"将来可能有用"的方法
- `capabilities/` 各子模块是**按功能的垂直切分**，不要抽象出统一的 `Capability` 基类——每个 capability 的接口天然不同
- 不要在 `runtime/` 和 `capabilities/` 之间再加一层抽象——它们已经是两层，再加就是三层

### 4.4 不要为了"干净"牺牲功能

- 工具结果过大时落盘并返回预览（`_persist_large_result`）——这个逻辑不优雅但必要，保留
- 双后端消息历史分开存储（`_anthropic_messages` / `_openai_messages`）——虽然想统一，但 Anthropic 和 OpenAI 的消息格式差异太大，强行抽象反而增加复杂度
- `RuntimeThread` 中的 `_render_event` 和 Agent 中的渲染逻辑——当前两处有重复，但 `RuntimeThread` 面向 JSON 事件流客户端（Server 模式），Agent 面向直接渲染（TUI 模式）。统一成本高收益小，保留现状

---

## 5. 可能踩坑的地方

### 5.1 Backend 接口的返回格式

**风险**：Anthropic 的 `tool_use` content block 和 OpenAI 的 `tool_calls` 格式差异很大。如果 `BackendResponse` 抽象不当，会在 loop.py 中再次出现后端判断。

**对策**：`BackendResponse.tool_calls` 使用统一的 `ToolCall` 数据类，由各 Backend 实现负责转换。loop.py 只处理 `ToolCall`。

```python
# AnthropicBackend 内部：
# Anthropic tool_use block → ToolCall(id=..., name=..., input=..., provider="anthropic")

# OpenAIBackend 内部：
# OpenAI function call → ToolCall(id=..., name=..., input=..., provider="openai")
```

### 5.2 消息历史的双后端差异

**风险**：`agent.messages` 返回的是其中一个后端的消息列表。但 `compressor.py` 需要操作消息历史，直接操作原生 dict 意味着需要知道 Anthropic 和 OpenAI 的消息格式差异。

**对策**：不在 compressor 层面做格式抽象。compressor 通过 `Agent` 的方法（`agent.messages`、`agent.append_user_context` 等）间接操作消息，Agent 内部根据 `config.use_openai` 决定操作哪个列表。这样 compressor 的代码是后端无关的。

### 5.3 循环导入

**风险**：`agent.py` 引入 `capabilities/tools/registry.py` 和 `capabilities/sandbox/manager.py`；`loop.py` 引入 `capabilities/tools/runtime.py`；如果 `runtime.py` 引用 `agent.py`，就形成循环。

**对策**：
- `capabilities/` 下的模块**不引用** `runtime/` 下的任何模块
- `ToolContext.agent` 字段使用 `Any` 类型标注，避免在 capabilities 中显式 import Agent
- 需要用 Agent 的方法时，通过 `ToolContext.agent` 动态调用，不静态 import

### 5.4 TuiApp 和 AgentLoop 的对接

**风险**：当前 `TuiApp._chat()` 直接调用 `agent.chat(prompt)`。重构后 `Agent` 不再有 `chat()` 方法——对话循环在 `AgentLoop` 中。

**对策**：`TuiApp` 构造函数增加 `loop: AgentLoop` 参数。`_chat()` 方法改为 `loop.run(prompt)` 并消费产生的事件流来渲染 UI：

```python
class TuiApp:
    def __init__(self, agent: Agent, loop: AgentLoop):
        self.agent = agent
        self.loop = loop

    async def _chat(self, prompt: str) -> None:
        """驱动 AgentLoop 产生事件，渲染到 TUI。"""
        async for event in self.loop.run(prompt):
            self._render_event(event)
```

### 5.5 现有测试

**风险**：重构会改变 import 路径和部分类名，现有测试可能大量失败。

**对策**：
1. 先确保所有现有测试在重构前通过
2. 重构每个模块后立即运行该模块相关的测试
3. 如果需要，在旧路径保留 `from old_path import NewClass` 的兼容性导入，标记 `# deprecated`
4. 全部重构完成后，清理兼容性导入

### 5.6 capabilities 子模块的内部引用

**风险**：`capabilities/tools/runtime.py` 需要引用 `capabilities/hooks/runner.py`。当前 domains/tools 有跨子模块引用。

**对策**：允许 capabilities 子模块之间通过 `from ..hooks import HookManager` 引用。因为 tools 的运行时执行确实需要 hooks 的参与。这不是循环依赖——hooks 不引用 tools。但需要显式列出允许的跨子模块引用，防止不知不觉形成依赖网。

---

## 6. 代码风格约定

### 6.1 命名

- **文件名**：小写 + 下划线（`agent.py`, `tool_registry.py`, `bwrap_backend.py`）。不使用 `bwrapBackend.py` 或 `ToolRegistry.py`
- **类名**：大驼峰（`Agent`, `ToolRegistry`, `AnthropicBackend`）
- **函数名**：小写 + 下划线（`build_system_prompt`, `resolve_runtime_config`）
- **私有方法**：单下划线前缀（`_call_model`, `_inject_startup_context`）
- **"内部"属性**：单下划线前缀（`_aborted`, `_tool_registry`）。仅在子类或紧密耦合的模块中访问

### 6.2 文件组织

每个 `.py` 文件的前 20 行应该是模块文档字符串，说明：
1. 这个文件负责什么
2. 依赖什么（关键 import）
3. 变更原因（什么情况下需要修改这个文件）

示例：
```python
"""Agent 状态容器。

本模块是 Agent 的数据面。它持有一次对话的所有状态字段，
但不实现对话循环、API 调用、压缩策略等行为。

变更原因：
  - 加新的 Agent 状态字段 → 改 __init__
  - 改消息历史的内部表示 → 改 add_* / append_* 方法
  - 加新的能力模块 → 改 __init__ 中的能力模块实例化
"""
```

### 6.3 docstring 语言

- 模块文档字符串、类文档字符串：中文
- 公开方法的文档字符串：中文（如果面向中文开发者）或英文（如果准备开源）
- 私有方法的注释：中文

### 6.4 导入顺序

1. `from __future__ import annotations`（每个文件）
2. 标准库
3. 第三方库
4. 项目内部模块（用相对导入）

### 6.5 类型标注

- 所有公开方法必须标注参数类型和返回类型
- `dict` 和 `list` 尽量标注具体类型（`list[ToolDef]`、`dict[str, Any]`）
- 不使用 `Optional[X]`，使用 `X | None`

---

## 7. 附录：文件清单

### 重构后完整目录树（排除 `__pycache__` 和测试文件）

```
src/
├── __init__.py
├── logging_config.py
├── models.py
│
├── cli/
│   ├── __init__.py
│   ├── main.py
│   └── args.py
│
├── runtime/
│   ├── __init__.py
│   ├── agent.py
│   ├── loop.py
│   ├── compressor.py
│   └── events.py
│
├── backend/
│   ├── __init__.py
│   ├── base.py
│   ├── anthropic.py
│   └── openai.py
│
├── capabilities/
│   ├── __init__.py
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── types.py
│   │   ├── builtin.py
│   │   ├── registry.py
│   │   └── runtime.py
│   ├── mcp/
│   │   ├── __init__.py
│   │   ├── types.py
│   │   ├── config.py
│   │   ├── manager.py
│   │   ├── connection.py
│   │   ├── transport.py
│   │   └── resources.py
│   ├── skills/
│   │   ├── __init__.py
│   │   ├── types.py
│   │   ├── registry.py
│   │   ├── runtime.py
│   │   └── prompt.py
│   ├── hooks/
│   │   ├── __init__.py
│   │   ├── types.py
│   │   ├── config.py
│   │   └── runner.py
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── types.py
│   │   ├── store.py
│   │   ├── retrieval.py
│   │   └── consolidation.py
│   ├── sandbox/
│   │   ├── __init__.py
│   │   ├── types.py
│   │   ├── config.py
│   │   ├── manager.py
│   │   ├── backend.py
│   │   ├── bwrap_backend.py
│   │   └── microsandbox_backend.py
│   └── permissions/
│       ├── __init__.py
│       ├── policy.py
│       ├── rules.py
│       ├── workspace.py
│       └── shell.py
│
├── context/
│   ├── __init__.py
│   ├── builder.py
│   └── sources.py
│
├── tui/
│   ├── __init__.py
│   ├── app.py
│   ├── input.py
│   ├── renderer.py
│   ├── state.py
│   ├── commands.py
│   └── theme.py
│
├── server/
│   ├── __init__.py
│   ├── app_server.py
│   └── transports/
│       ├── __init__.py
│       ├── stdio.py
│       ├── websocket.py
│       └── unix_socket.py
│
├── protocol/
│   ├── __init__.py
│   ├── messages.py
│   └── dispatcher.py
│
└── session/
    ├── __init__.py
    ├── event_store.py
    ├── artifacts.py
    └── snapshots.py
```

**总计 58 个 `.py` 文件**（排除 `__init__.py` 中的纯导出文件按实际内容算）。

### 对比原版的变化

| 新增 | 删除（合并） | 修改 |
|------|-------------|------|
| `cli/main.py` | `__main__.py` (拆为 cli/) | `tui/app.py` (构造函数增加 loop 参数) |
| `cli/args.py` | `runtime/agent/models.py` (提升为顶层 models.py) | `server/app_server.py` (适配新 Agent) |
| `backend/base.py` | `runtime/agent/backends.py` (独立为 backend/) | 各 capabilities 子模块的 import 路径 |
| `backend/anthropic.py` | `runtime/agent/context.py` (拆入 compressor.py + capabilities/) | |
| `backend/openai.py` | `runtime/agent/tools_runtime.py` (合并进 capabilities/tools/) | |
| `runtime/compressor.py` | `runtime/events.py` + `runtime/agent/events.py` (合并) | |
| `context/builder.py` (合并 4 个文件) | `domains/` 整个目录 (合并进 capabilities/ + context/) | |
| `context/sources.py` (合并 3 个文件) | `capabilities/` 旧子目录 (合并进新的 capabilities/) | |
| `capabilities/skills/runtime.py` (合并 2 个文件) | `runtime/agent/engine.py` (合并进 loop.py) | |
| `capabilities/memory/retrieval.py` (合并 2 个文件) | | |
