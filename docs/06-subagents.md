# SubAgent 优化方案

## 目标

把 `nanocode` 的 subagent 从"串行阻塞 + 单任务"升级为"并行编排 + 超时预算控制"，同时不引入过度设计。安全模型保持工具白名单为核心，sandbox 透明继承，不画蛇添足。

## 当前实现概述

### 一句话：Fork-and-Return

主 Agent 通过 `agent` 工具调用，fork 一个新的 Agent 实例，阻塞等待它执行完，拿回纯文本结果。

### 当前架构

```
主 Agent 调用 agent(type="explore", prompt="找到所有数据库迁移代码")
  → get_sub_agent_config("explore")
      → 内置类型匹配 explorer/plan/general，或查自定义 .md
      → 返回 { system_prompt: "...", tools: [read_file, list_files, grep_search] }
  → Agent(RuntimeConfig(is_sub_agent=True), custom_tools=..., sandbox_manager=父的)
  → agent.run_once(prompt)           # 阻塞等待
  → 返回纯文本结果
  → 合并 token 用量到父 Agent
```

### 具备的能力

- 3 种内置类型：explore（3 工具只读）、plan（3 工具只读+结构化规划提示词）、general（全工具-agent）
- 自定义 Agent：`~/.claude/agents/*.md` 和 `.claude/agents/*.md`，YAML frontmatter 声明 name/description/allowed-tools
- 子 Agent 独立消息历史，共享父 Agent 的 SandboxManager
- 递归防护：所有子 Agent 不给 `agent` 工具
- 权限继承：子 Agent 继承父 Agent 的 permission_mode

### 当前的问题

1. **串行阻塞**——`run_once` 是同步等待，父 Agent 完全空闲。模型想同时搜两个目录只能串行调两次 agent 工具
2. **无超时控制**——子 Agent 卡住（模型循环不掉、API 重试），父 Agent 永远等待
3. **无 token 预算**——子 Agent 可以无限制消耗 token
4. **Agent 工具执行路径**——`_execute_agent_tool` 通过 `SubAgentOrchestrator` 实现，委托给并行编排器

## 总体设计

### 升级后的数据流

```
主 Agent 调用 agent 工具（可传单任务或任务列表）
  → SubAgentOrchestrator.dispatch(tasks)
  → asyncio.gather(
       _run_sub_agent(task1, timeout=30, max_turns=10),
       _run_sub_agent(task2, timeout=30, max_turns=10),
       _run_sub_agent(task3, timeout=30, max_turns=10),
     )
  → 三个子 Agent 并行执行，各自独立消息历史
  → 收集结果列表返回主 Agent
```

### 新增概念：SubAgentOrchestrator

这是唯一新增的类。职责：接收任务列表，并行派发子 Agent，处理超时和预算，收集结果。

```python
class SubAgentOrchestrator:
    """并行子 Agent 编排器。

    不引入工作池、消息队列、事件总线。只做一件事：
    把多个 SubAgentTask 并行派发，asyncio 收集结果。
    """

    def __init__(self, parent_agent):
        self.parent = parent_agent
        self.max_concurrency = 4

    async def dispatch(self, tasks: list[dict]) -> list[dict]:
        """并行派发，asyncio.wait_for 控制超时，收集结果。"""
```

一个 `SubAgentTask` 就是普通 dict，不是新类：

```python
{
    "type": "explore",       # Agent 类型
    "prompt": "搜索迁移代码",  # 任务描述
    "timeout": 30.0,         # 超时秒数（可选，默认 60）
    "max_turns": 10,         # 最大对话轮次（可选，默认 20）
}
```

### 架构不变：Fork-and-Return 保留

并行编排不是替代 Fork-and-Return，而是**让 Fork-and-Return 可以并行执行**。单任务调用时行为与当前完全一致——fork 一个 Agent，run_once，返回结果。多任务时才是 `asyncio.gather`。

### 与 Sandbox 的关系

**子 Agent 复用父 Agent 的 SandboxManager，不创建独立实例。** 原因：

1. bwrap 的隔离是 per-command 的——每次 `run_shell()` 都是新 bwrap 进程，没有持久沙箱容器。多个 SandboxManager 实例不会带来额外隔离，只会增加不必要的配置副本
2. microsandbox 创建多个 microVM 太重——启动一个要数秒到数十秒，并行子 Agent 场景不可行
3. explore 和 plan 子 Agent 根本没有 `run_shell` 工具——sandbox 对它们完全无关
4. general 子 Agent 的 `run_shell` 调用经过同一个 `SandboxManager`，继承用户的 profile 选择，行为一致

### 安全模型：工具白名单为主，Sandbox 为辅

三内置类型的安全边界在工具注册层已经闭合：

| 子 Agent | write_file | run_shell | agent | 安全核心 |
|----------|:--:|:--:|:--:|------|
| explore | ❌ | ❌ | ❌ | 工具白名单直接闭合 |
| plan | ❌ | ❌ | ❌ | 同上 |
| general | ✅ | ✅ | ❌ | 白名单封住 agent 递归 + sandbox 约束 shell |

sandbox 只在 general 子 Agent 调用 `run_shell` 时介入——它约束命令的文件系统和网络边界，但不约束 `write_file`（文件工具走宿主机 Python 进程，由权限系统保护）。

### 为什么不做结构化输出

子 Agent 的消费者是父 Agent——一个 LLM。LLM 最擅长理解和总结自然语言。用 prompt 工程强制输出 JSON 引入两个问题：

1. 不可靠——模型不配合时格式乱了，需要兜底逻辑，增加复杂度
2. 浪费 token——同样的信息，JSON 比自然语言更啰嗦

Codex CLI 的子 Agent 也不做结构化输出。如果将来子 Agent 的结果需要驱动程序逻辑（如"critical 问题自动阻止合并"），再加。

## 详细设计

### 1. `capabilities/subagents/orchestrator.py`

新增文件，约 80 行。

```python
"""并行子 Agent 编排器。

一次性派发多个 SubAgentTask，asyncio 并行执行，控制超时和预算。
"""

from __future__ import annotations

import asyncio
from typing import Any

from ...runtime.agent import Agent, RuntimeConfig
from ...backend import create_backend
from ...logging_config import get_logger

logger = get_logger("subagents.orchestrator")

DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_TURNS = 20
DEFAULT_MAX_CONCURRENCY = 4


class SubAgentOrchestrator:
    """并行派发多个子 Agent，收集结果。

    不引入工作池或事件总线。只做并行编排这一件事。
    """

    def __init__(self, parent_agent, *, max_concurrency: int = DEFAULT_MAX_CONCURRENCY):
        self.parent = parent_agent
        self.max_concurrency = max_concurrency

    async def dispatch(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """并行派发，返回结果列表（顺序与输入一致）。

        每个 task 是一个 dict：
          - type: str          # Agent 类型（必填）
          - prompt: str        # 任务描述（必填）
          - timeout: float     # 超时秒数（可选）
          - max_turns: int     # 最大对话轮次（可选）

        对单任务也走此方法——行为与直接调用 run_once 一致。
        """
        if not tasks:
            return []

        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def _run_one(task: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                return await self._execute_task(task)

        return list(await asyncio.gather(*[_run_one(t) for t in tasks]))

    async def _execute_task(self, task: dict[str, Any]) -> dict[str, Any]:
        """执行单个子 Agent 任务，带超时保护。"""
        from . import get_sub_agent_config

        agent_type = task.get("type", "general")
        prompt = task.get("prompt", "")
        timeout = task.get("timeout", DEFAULT_TIMEOUT)
        max_turns = task.get("max_turns", DEFAULT_MAX_TURNS)

        if not prompt:
            return {"error": "empty prompt", "type": agent_type}

        # 获取子 Agent 配置
        config = get_sub_agent_config(agent_type)

        # 创建子 Agent 实例
        runtime_config = RuntimeConfig(
            model=self.parent.model,
            provider=self.parent.config.provider,
            api_key=self.parent.config.api_key,
            api_base=self.parent.config.api_base,
            anthropic_base_url=self.parent.config.anthropic_base_url,
            permission_mode=self.parent.permission_mode,
            is_sub_agent=True,
            custom_system_prompt=config["system_prompt"],
            max_turns=max_turns,
            sandbox_config=self.parent.config.sandbox_config,
            workspace=self.parent.config.workspace,
        )
        sub_agent = Agent(
            runtime_config,
            custom_tools=config["tools"],
            sandbox_manager=self.parent._sandbox_manager,  # 复用父 Agent 的
        )

        try:
            result = await asyncio.wait_for(
                sub_agent.run_once(prompt),
                timeout=timeout,
            )
            self.parent.total_input_tokens += result["tokens"]["input"]
            self.parent.total_output_tokens += result["tokens"]["output"]
            return {
                "type": agent_type,
                "text": result["text"],
                "tokens": result["tokens"],
            }
        except asyncio.TimeoutError:
            sub_agent.abort()
            return {
                "type": agent_type,
                "error": "timeout",
                "text": f"Sub-agent '{agent_type}' timed out after {timeout}s.",
            }
        except Exception as exc:
            return {
                "type": agent_type,
                "error": str(exc),
                "text": f"Sub-agent '{agent_type}' failed: {exc}",
            }
```

### 2. `runtime/agent.py`——补回 Agent 工具入口

在 `Agent` 类中补回 `_execute_agent_tool` 方法。这是 `ToolRegistry._call_builtin` 对 `agent` 工具的调用入口。

```python
async def _execute_agent_tool(self, inp: dict) -> str:
    """agent 工具入口。被 ToolRegistry._call_builtin 调用。

    支持单任务和多任务两种模式：
      - 单任务：type + prompt → 派发一个子 Agent
      - 多任务：tasks 列表 → 并行派发多个子 Agent
    """
    from ..capabilities.subagents.orchestrator import SubAgentOrchestrator
    from ..tui.renderer import get_renderer

    orchestrator = SubAgentOrchestrator(self)

    # 多任务模式
    if "tasks" in inp and isinstance(inp["tasks"], list):
        tasks = inp["tasks"]
    else:
        # 单任务模式（向后兼容）
        tasks = [{
            "type": inp.get("type", "general"),
            "prompt": inp.get("prompt", ""),
        }]

    get_renderer().sub_agent_start(
        inp.get("type", "general"),
        inp.get("description", "sub-agent task"),
    )

    results = await orchestrator.dispatch(tasks)
    return self._format_agent_results(results)

def _format_agent_results(self, results: list[dict]) -> str:
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
```

### 3. `capabilities/tools/builtin.py`——agent 工具的 input_schema

`agent` 工具的 schema 增加 `tasks` 字段支持多任务并行：

```python
{
    "name": "agent",
    "description": (
        "Launch one or more sub-agents to handle tasks autonomously. "
        "Sub-agents have isolated context and return their result. "
        "Types: 'explore' (read-only search), 'plan' (read-only planning), "
        "'general' (full tools except agent). "
        "Pass 'tasks' list for parallel execution."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Short (3-5 word) description of the task",
            },
            "prompt": {
                "type": "string",
                "description": "Detailed task instructions for the sub-agent",
            },
            "type": {
                "type": "string",
                "enum": ["explore", "plan", "general"],
                "description": "Agent type. Default: general",
            },
            "tasks": {
                "type": "array",
                "description": (
                    "Optional list of tasks for parallel execution. "
                    "Each item has {type, prompt}. "
                    "When provided, 'type' and 'prompt' at top level are ignored."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "prompt": {"type": "string"},
                    },
                    "required": ["type", "prompt"],
                },
            },
        },
        "required": ["description", "prompt"],
    },
}
```

单任务调用保持现有接口不变：`{type, description, prompt}`。

多任务新增：`{description, tasks: [{type, prompt}, ...]}`。

### 4. 文件变更清单

| 文件 | 动作 | 说明 |
|------|:--:|------|
| `capabilities/subagents/orchestrator.py` | **新增** | SubAgentOrchestrator，~80 行 |
| `capabilities/subagents/__init__.py` | 修改 | 导出 Orchestrator |
| `runtime/agent.py` | 修改 | 补回 `_execute_agent_tool` + `_format_agent_results`，~50 行 |
| `capabilities/tools/builtin.py` | 修改 | agent 工具 schema 增加 `tasks` 字段 |
| `test/runtime/test_subagent.py` | **新增** | 并行编排 + 超时测试 |

### 5. 模块关系图

```
主 Agent (runtime/agent.py)
  │
  │ agent 工具调用
  ▼
SubAgentOrchestrator (capabilities/subagents/orchestrator.py)
  │
  │ asyncio.gather ─┬─ _run_sub_agent ─► Agent (fork) ─► run_once()
  │                 ├─ _run_sub_agent ─► Agent (fork) ─► run_once()
  │                 └─ _run_sub_agent ─► Agent (fork) ─► run_once()
  │
  │ 每个子 Agent:
  │   - 独立消息历史（Agent 自身维护）
  │   - 工具白名单（ToolRegistry，由 get_sub_agent_config 决定）
  │   - 共享 SandboxManager（run_shell 时走父 Agent 的 sandbox）
  │   - 继承 permission_mode
  │   - asyncio.wait_for 控制超时
  │   - RuntimeConfig.max_turns 控制轮次
  ▼
收集结果 → 格式化文本 → 返回主 Agent
```

## 硬性约束

- 单任务调用接口不变：`{type, description, prompt}` 与当前完全一致
- 子 Agent 安全模型不变：工具白名单是核心防护，sandbox 透明继承
- 递归防护不变：所有子 Agent 不给 `agent` 工具
- Fork-and-Return 模式不变：不引入持久 worker 池或事件总线
- Python >= 3.10，不新增依赖
- 三类内置类型的工具限制不变：explore/plan 只读，general 全工具-agent

## 隐含要求

- 并行子 Agent 的失败不互相影响——一个超时，其他正常完成
- 子 Agent 的 token 用量回合并入父 Agent 的统计
- 超时后的子 Agent 实例应被 abort，释放资源
- Orchestrator 对单任务的调用路径应退化为直接 `run_once`，以便测试和调试
- 自定义 Agent 的 `.md` 发现和缓存机制不变

## 不能做什么

- 不引入 AgentBus、EventBus、消息队列、WorkerPool
- 不引入持久 Sandbox 容器或独立 SandboxManager
- 不做结构化输出（JSON schema 校验）
- 不做子 Agent 优先级调度——asyncio.gather 足够
- 不做嵌套子 Agent（子 Agent 已禁止拿 agent 工具，天然限制为 1 层）
- 不改变自定义 Agent 的 .md 文件格式（本次优化不涉及）
- 不在 orchestrator 中引入"任务依赖图"或"工作流 DAG"

## 可能踩坑的地方

### Agent 工具执行路径断裂

`ToolRegistry._call_builtin` 通过 `ctx.agent._execute_agent_tool(inp)` 调用 agent 工具，该方法委托给 `SubAgentOrchestrator` 执行并行编排。

### 并行子 Agent 的 token 用量合并

`asyncio.gather` 并行执行时，多个子 Agent 同时写入 `self.parent.total_input_tokens`。Python 的 `+=` 对 int 是原子的（GIL 保护），但为了明确语义，建议在 `_execute_task` 中用局部变量累加，gather 返回后再批量合并。

### TuiApp 的子 Agent 渲染

当前 `TuiApp` 对子 Agent 的渲染通过 `get_renderer().sub_agent_start/end` 标记。并行执行时，多个子 Agent 的输出会交织。Orchestrator 应在派发前调用 `sub_agent_start`，全部完成后调用 `sub_agent_end`，不暴露内部并行细节给 TUI。

### 超时后的资源清理

`asyncio.wait_for` 抛出 `TimeoutError` 后，子 Agent 的 `AgentLoop` 可能仍在运行。必须在 catch 块中调用 `sub_agent.abort()` 确保循环终止。

### asyncio.gather 的异常传播

`asyncio.gather` 默认会抛出第一个异常。必须用 `return_exceptions=True` 或者每个任务内部 try/except 包裹，确保一个子 Agent 失败不影响其他。

### 自定义 Agent 缓存

`_discover_custom_agents()` 使用全局缓存，改了 `.md` 文件不重启不生效。**这不是本次优化要解决的问题**，但 Orchestrator 在获取配置时如果缓存未命中，应能正常回退到 general 类型。

## 验收标准

- 单任务 agent 工具调用：行为与当前一致
- 多任务并行调用：模型传 `{tasks: [{type, prompt}, ...]}`，异步并行执行
- 超时：子 Agent 超过 timeout 秒后被 abort，返回超时错误信息
- 预算：子 Agent 达到 max_turns 后正常退出，不阻塞父 Agent
- 一个子 Agent 失败不影响其他子 Agent
- token 用量正确合并到父 Agent
- 子 Agent 不给 agent 工具（递归防护不破）
- explore/plan 只有 read_file、list_files、grep_search（工具白名单不破）
- 编译通过 + 全部现有测试通过 + 新增测试通过

## 当前 SubAgent 类型的关系

explore、plan、general 都是在 `capabilities/subagents/__init__.py` 中定义的，属于 subagent 系统的子类型——不是独立模块。三者共用同一套 fork-and-return 基础设施，区别仅在于 system prompt 和工具白名单：

| 类型 | 工具 | system prompt 定位 |
|------|------|-----|
| explore | read_file, list_files, grep_search | 快速搜索，并行工具调用，返回搜索结 果 |
| plan | read_file, list_files, grep_search | 架构分析，列出步骤，识别风险 |
| general | 全工具-agent | 独立执行任务，完成并报告 |

explore 和 plan 的工具白名单完全相同——它们的差异纯粹靠 prompt 工程驱动。plan subagent 与 Claude Code 的"系统级 Plan Mode"是两回事：当前的 plan 只是一个带特殊提示词的只读 Agent，无法控制主 Agent 的执行流程。

## 下一阶段优化方向

以下方向不作为本次优化的一部分，但在设计 `SubAgentOrchestrator` 时应预留扩展空间。按优先级从高到低排列。

### Stage 2：子 Agent 失败自动重试

**动机**：当前超时或 API 限流后直接返回错误。如果失败原因是可重试的（429、503、网络抖动），重试一次可能是合理的。

**设计方向**：Orchestrator 在 `_execute_task` 中增加重试判定——检查子 Agent 异常类型，对可重试错误自动重试一次，对真正的超时不重试。

**改动范围**：`orchestrator.py` 约 20 行。

### Stage 3：自定义 Agent 热加载

**动机**：当前 `_discover_custom_agents()` 使用全局缓存，修改 `.md` 文件后必须重启 nanocode 才生效。

**设计方向**：在缓存中记录每个 `.md` 文件的 mtime，读取前检查是否过期。过期则重新加载该文件。同时提供 `/agents reload` REPL 命令手动刷新。

**改动范围**：`subagents/__init__.py` 约 30 行，`tui/commands.py` 约 10 行。

### Stage 4：系统级 Plan Mode

**动机**：当前 `plan` 子 Agent 可以输出计划，但无法控制主 Agent 的执行流程。主 Agent 是否按计划执行完全取决于模型自觉。真正的 Plan Mode 应该是系统级功能——Agent 进入"规划阶段"只允许读文件和搜索，用户确认计划后再切回执行模式。

**设计方向**：这不是 subagent 的功能，而是 AgentLoop 层面的模式切换。需要增加 `AgentMode` 枚举（`execute` / `plan`），在 plan 模式下工具注册表只暴露只读工具，用户通过 `/plan` 或模型自主选择进入。

**改动范围**：`runtime/agent.py`、`runtime/loop.py`、`tui/commands.py`。不与 subagent 模块交叉。

### Stage 5：子 Agent 结果的引用机制

**动机**：当前子 Agent 返回的文本被直接拼接到主 Agent 消息历史中。模型想引用"上一个子 Agent 找到的那个文件"只能靠记文本。如果对话很长或经过 compact，模型可能丢失上下文。

**设计方向**：子 Agent 的输出前后加上引用标记，方便模型精确引用：

```
[sub-agent-result id="sub-1" type="explore"]
发现 migrations/001.py 有 SQL 注入风险
发现 api/routes.py 缺少权限检查
[/sub-agent-result]
```

主 Agent 可以引用 `[ref: sub-1]` 来指代结果。如果结果太大，将完整输出写入 `ArtifactStore`，标记中只放摘要 + artifact 引用。

**改动范围**：`orchestrator.py` 的结果格式化部分约 30 行。

### Stage 6：子 Agent 结果持久化

**动机**：当前子 Agent 的结果只在当前消息历史中可用。`/clear` 或 compact 后，子 Agent 的执行结果丢失，模型无法回溯之前的子 Agent 做了什 么。

**设计方向**：子 Agent 的关键输出（发现的文件清单、识别的问题、token 用量）写入会话的 `ArtifactStore`，与消息历史解耦。compact 后可通过 artifact 引用恢复上下文。

**改动范围**：`orchestrator.py` 约 30 行，`session/artifacts.py` 无需修改。
