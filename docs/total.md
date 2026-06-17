# nanoCode 当前架构总览 v6

> 当前实现说明。本文保留原重构方案的结构，但所有目录、接口和落地状态均按当前 `src/`、`benchmarks/local-fixture` 和 `pyproject.toml` 修正。

> 吸取 pi 的分层思想（4 层、依赖单向、内核不感知应用），保留 nanoCode 自身设计（Hook + Extension 互补、Python 惯例、单应用尺度）。
> 2026-06-10

---

## 零、核心原则与设计约束

后续每一节都从这几条推导。每项设计决策必须对照硬性约束检查。

### 0.1 核心原则

| 原则 | 说明 |
|------|------|
| **分层单向依赖** | 4 层架构，依赖箭头只向下。上层知道下层，下层不知道上层 |
| **内核纯净** | Agent core（`agent.py`、`loop.py`、`events.py`、`types.py`、`models.py`、`budget.py`）描述 Agent 状态机和对话协议（消息、工具调用、事件）。零 I/O，零第三方 import。不描述工具/插件/TUI/文件/网络/SDK 如何接入 |
| **框架层管运转** | `agent/harness/` 提供 Agent 运转所需的横切工具（压缩、上下文构建、会话与运行工件持久化、权限、确认、Hook）。允许 I/O，但**不引用 cli/、tui/ 或 providers/** |
| **应用层管能力** | `cli/core/` 持有所有能力模块（工具、沙箱、技能、记忆存储、MCP、扩展）。能力可插拔——加/减一个能力只影响本目录 |
| **扩展点通过回调槽位解耦** | 下层暴露回调槽位（空函数指针），上层在运行时填入具体实现。下层不 import 上层，但上层可以"插线" |

### 0.2 硬性约束（每项设计决策必须逐条对照）

| # | 约束 | 违反时表现为 |
|:---:|------|------|
| C1 | `agent/agent.py`、`loop.py`、`events.py`、`types.py`、`models.py`、`budget.py` 不 import 文件系统、网络、第三方 SDK、`tui/` | `grep "from tui\|import anthropic\|import openai\|open(" agent/{agent,loop,events,types,models,budget}.py` 有结果 |
| C2 | `agent/harness/` 不 import `cli/` 或 `tui/` | `grep -r "from cli\|from tui" agent/harness/` 有结果 |
| C3 | 依赖方向：上层依赖下层。Agent core 不 import `agent/harness/`、`cli/`、`tui/`、`cli/core/`；`agent/harness/` 不 import `cli/` 或 `tui/` | Agent core 文件出现上层 import，或 harness 反向 import 表现层 |
| C4 | 核心类型（`ToolDef`、`ToolCall`、`ToolResult`、`RuntimeEvent`）定义在 `agent/types.py` | 应用层类型文件中重复定义同名字段 |
| C5 | 外部接口不变：CLI 参数名、环境变量、JSONL 协议、工具 schema | |
| C6 | 会话恢复格式不变：`~/.nanocode/sessions/<session_id>/session.jsonl` 可 resume | |
| C7 | 不新增依赖：Python >= 3.10 | |
| C8 | 不引入抽象基类：不给 capability 抽象统一接口 | |

---

## 一、架构全景

### 1.1 四层分层

```
┌──────────────────────────────────────────────────────────────────────┐
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │               cli/  应用与表现层                               │   │
│  │                                                               │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐        │   │
│  │  │ cli/     │ │ tui/     │ │ server/  │ │ protocol/│        │   │
│  │  │ 入口+会话│ │ 终端 UI  │ │ stdio    │ │ JSONL    │        │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘        │   │
│  │                                                               │   │
│  │  ┌──────────────────────────────────────────────────────┐    │   │
│  │  │  能力模块: tools/ sandbox/ skills/ memory/ mcp/      │    │   │
│  │  │             server/ protocol/ extensions/ project/    │    │   │
│  │  └──────────────────────────────────────────────────────┘    │   │
│  │                                                               │   │
│  │  pi 对应: grain-ai-agent-headless + grain-ai-agent-tui        │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                              │                                       │
│  ┌──────────────────────────┼───────────────────────────────────┐   │
│  │                          │       agent/harness/  框架层       │   │
│  │                          │                                    │   │
│  │  只管"Agent 怎么运转"，不管"Agent 能做什么"                     │   │
│  │                                                               │   │
│  │  compressor.py   message_view.py   approvals.py                │   │
│  │  context/        persistence/       permissions/                 │   │
│  │  hooks/ (外部 subprocess 扩展)                                  │   │
│  │                                                               │   │
│  │  ★ harness 允许 I/O, 不引用 cli/、tui/ 或 providers/          │   │
│  │                                                               │   │
│  │  pi 对应: grain-agent-harness                                   │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                              │                                       │
│  ┌──────────────────────────┼───────────────────────────────────┐   │
│  │                          │       agent/         Agent 内核    │   │
│  │                                                               │   │
│  │  agent.py (纯状态 + 回调槽位)  loop.py (主循环)               │   │
│  │  events.py (运行时事件)        types.py (核心类型)             │   │
│  │  models.py (模型元数据)        budget.py (Token 预算)          │   │
│  │                                                               │   │
│  │  ★ agent/core 零 I/O, 零第三方依赖, 不引用 cli/ 或 tui/      │   │
│  │                                                               │   │
│  │  pi 对应: grain-agent-core                                     │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                              │                                       │
│  ┌──────────────────────────┼───────────────────────────────────┐   │
│  │                          │     providers/    Provider 层      │   │
│  │                                                               │   │
│  │  base.py (Backend ABC)   anthropic.py   openai.py            │   │
│  │                                                               │   │
│  │  pi 对应: grain-llm-genai + grain-llm-models                   │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.2 依赖方向

```
cli/session.py ──→ agent/harness/ ──→ agent/
cli/session.py ──→ agent/              (直接使用 Agent/Types/Events/AgentConfig)
cli/session.py ──→ providers/ ──→ agent/types.py + agent/models.py
cli/session.py ──→ cli/core/
tui/ ───────────→ cli/session.py / cli/thread.py
cli/core/server ─→ cli/thread.py
```

配置对象分层：

- `agent/agent.py` 只定义 `AgentConfig`，字段限于 Agent core 必须知道的状态机参数：`model`、`message_format`、`thinking`、`max_cost_usd`、`max_turns`、`context_window`。
- `cli/config.py` 定义 `RuntimeConfig`，持有 provider、API、permission、sandbox、workspace、sub-agent、自定义 system prompt 等应用层装配参数。
- `AgentSession` 负责调用 `RuntimeConfig.to_agent_config()`，并把最终 system prompt、startup context、workspace、persistence、tools、memory、hooks 等运行对象装配到对应层。

**各层 I/O 和依赖约束**：

```
agent/core (agent.py, loop.py, events.py, types.py, models.py, budget.py)
  ← 零 I/O, 零第三方依赖, 不引用 cli/、tui/
agent/harness/    ← 允许 I/O（文件读写、subprocess）, 依赖 agent/
                     不引用 cli/、tui/、providers/
                     需要 LLM 摘要时由 cli/session.py 注入 callable
providers/        ← 只依赖 agent/types.py、agent/models.py 和 provider SDK
cli/              ← 依赖 agent/ + harness/ + providers/ + cli/core/
tui/              ← 依赖 cli/session.py
```

**核心原则**：下层不引用上层。Agent core 仅暴露回调槽位供应用层桥接扩展能力，不主动依赖任何扩展系统。

### 1.3 吸取了 Pi 的什么

| 从 Pi 吸取 | nanoCode 的落地方案 |
|------------|-------------------|
| 4 层分层、依赖单向 | 上面 4 层，依赖箭头全部向下 |
| Agent 内核不引用扩展系统 | Agent 暴露回调槽位，应用层 AgentSession 桥接 |
| Harness 作为"怎么运转"的框架层 | compressor/message_view/context/persistence/permissions/hooks/approvals 集中管理 |
| 能力模块可插拔在应用层 | tools/sandbox/skills/memory/mcp/extensions 全在 cli/core/ |
| ExtensionAPI + register() 模式 | 简化为 6 事件 3 方法，Python `register(api)` 惯例 |
| Provider 层独立 | providers/ 零依赖上层，加厂商只加一个文件 |

### 1.4 不照搬 Pi 的地方

| Pi 的做法 | nanoCode 的做法 | 原因 |
|-----------|----------------|------|
| Extension 系统 25 种事件 + UI 控制 | 6 种事件，不做 UI 自定义 | nanoCode 单用户工具，不需要多人产品特性 |
| Provider 注册、快捷键冲突检测、stale instance 保护 | 不做 | 复杂度不匹配 nanoCode 的规模 |
| 多 crate 独立发布 | 单项目内目录分层 | nanoCode 只有一个应用，拆 crate 无意义 |
| TypeScript + jiti + virtualModules | Python + importlib | 技术栈差异 |
| AgentHarness 是重型编排器 | harness/ 是薄层工具集合 | nanoCode 的 agent + loop 足够简单，不需要重型编排器 |
| export default function(pi) | register(api) | Python 社区惯例 |

---

## 二、当前目录结构

> 所有模块在 `src/` 下，当前 `pyproject.toml` 的 `package-dir = {"nanocode" = "src"}` 不变。

```
nanocode/
├── pyproject.toml
│
└── src/                                 # package-dir 指向此处
    │
    ├── providers/                       # LLM Provider 层
    │   ├── __init__.py                  # create_backend()
    │   ├── base.py                      # Backend ABC
    │   ├── anthropic.py
    │   └── openai.py
    │
    ├── agent/                           # Agent 内核 (core: 零 I/O)
    │   ├── __init__.py
    │   ├── agent.py                     # 状态容器 + 回调槽位
    │   ├── loop.py                      # 主循环 (通过注入的回调触发扩展点)
    │   ├── events.py                    # RuntimeEvent
    │   ├── types.py                     # ★ 核心类型: ToolDef, ToolCall, ToolResult, RuntimeEvent
    │   ├── models.py                    # 模型元数据
    │   ├── budget.py                    # Token 定价与预算
    │   │
    │   └── harness/                     # 框架层: "怎么运转" (允许 I/O, 不引用 cli/、tui/ 或 providers/)
    │       ├── __init__.py
    │       ├── compressor.py            # Tool History Snip / Context Compact
    │       ├── message_view.py          # canonical conversation 工具结果读/写视图
    │       ├── approvals.py             # 确认管理 (yolo/ask/deny)
    │       ├── context/                 # 系统提示词 + 启动上下文
    │       │   ├── __init__.py
    │       │   ├── builder.py
    │       │   └── sources.py           # AGENTS.md / .nanocode/rules / Git / frontmatter
    │       ├── persistence/             # 会话与运行工件持久化
    │       │   ├── __init__.py
    │       │   ├── atomic.py            # durable atomic write / JSONL append
    │       │   ├── session_log.py       # durable session.jsonl checkpoint/resume
    │       │   ├── session_store.py     # session discovery / load derived snapshot / latest
    │       │   ├── artifacts.py         # 大结果 artifact store
    │       │   ├── run_store.py         # 每次请求的 trace/report
    │       │   ├── task_state.py        # 单次请求内存状态
    │       │   └── report.py            # run report 构建与汇总指标
    │       ├── permissions/             # 权限判断
    │       │   ├── __init__.py
    │       │   ├── policy.py
    │       │   ├── rules.py
    │       │   ├── tool_policy.py
    │       │   ├── workspace.py
    │       │   └── shell.py
    │       └── hooks/                   # Hook 管理 (外部 subprocess)
    │           ├── __init__.py
    │           ├── types.py
    │           ├── config.py
    │           └── runner.py
    │
    ├── cli/                             # 应用层
    │   ├── __init__.py
    │   ├── main.py                      # 入口 + 模式派发
    │   ├── session.py                   # AgentSession: 装配一切, 桥接回调, 暴露公共接口
    │   ├── config.py                    # RuntimeConfig: 应用层运行配置 -> AgentConfig
    │   ├── args.py                      # 参数解析
    │   ├── thread.py                    # RuntimeThread
    │   ├── logging_config.py
    │   │
    │   └── core/                        # 能力模块 (可插拔)
    │       ├── __init__.py
    │       ├── tools/                   # 内置工具
    │       │   ├── types.py             # ToolContext, FunctionTool, ToolMetadata (从 agent/types.py import 核心类型)
    │       │   ├── builtin.py
    │       │   ├── recent_files.py
    │       │   ├── registry.py          # ToolRegistry
    │       │   └── runtime.py           # ToolRuntime (扩展的 before/after_tool_call 在此触发)
    │       ├── sandbox/                 # config/types/manager/bwrap/microsandbox
    │       ├── skills/                  # registry/runtime/prompt/types
    │       ├── project/                 # ProjectScope: 项目身份和项目级数据目录
    │       │   ├── __init__.py
    │       │   └── identity.py
    │       ├── memory/                  # 本地 markdown 记忆
    │       │   ├── paths.py             # memory helper，转发到 project identity
    │       │   ├── types.py
    │       │   ├── store.py
    │       │   └── runtime.py           # ★ MemoryRuntime: system prompt + startup context + 显式写入
    │       ├── mcp/                     # stdio MCP 连接、工具注册、资源读取
    │       ├── subagents/               # built-in/custom agent + orchestrator
    │       ├── server/                  # stdio JSONL server；unix/websocket 是占位
    │       ├── protocol/                # JSONL protocol dispatcher
    │       └── extensions/              # 进程内 Python 扩展系统
    │           ├── __init__.py
    │           ├── api.py
    │           ├── loader.py
    │           └── runner.py
    │
    └── tui/                             # 终端 UI
        ├── __init__.py
        ├── app.py
        ├── commands.py
        ├── input.py
        ├── renderer.py
        ├── state.py
        └── theme.py
```

---

## 三、关键设计决策

### 3.1 Agent 通过回调槽位解耦扩展

**问题**：如果 Agent 直接 import 扩展系统或能力模块，会导致内核层依赖应用层（反向依赖）。目标实现要求 Agent/Loop 不直接 import capability/tool runtime，而是通过回调和 AgentSession 解耦。

**方案**：Agent 暴露 6 个回调槽位（`_on_agent_start`、`_on_agent_end`、`_on_turn_start`、`_on_turn_end`、`_on_before_tool_call`、`_on_after_tool_call`），自己不 import 任何应用层代码。应用层 `cli/session.py` 的 `AgentSession` 负责创建 `ExtensionRunner` 并填入槽位。

**槽位触发位置与 Agent 管线对齐**：

- **生命周期事件**（4 个）→ `loop.py` 触发。Loop 只负责节奏控制。
- **工具拦截事件**（2 个）→ `ToolRuntime.execute_one()` 触发。ToolRuntime 在 `cli/core/tools/runtime.py`，由 `cli/session.py` 创建并持有。Loop **不 import ToolRuntime**，而是通过注入的 `execute_tools(calls) → results` 回调来执行工具。回调内部（ToolRuntime）在参数校验后、权限判断前触发 `before_tool_call`，在工具执行并持久化后触发 `after_tool_call`。

```
cli/session.py                        agent/loop.py
─────────────                         ─────────────
ToolRuntime(                          AgentLoop.run():
  registry,                               ...
  before_tool_call=...,                   results = execute_tools(calls)  ← 注入的回调
  after_tool_call=...,               )
)
  ↓
AgentLoop(agent, execute_tools=...)
```

Loop 只看到一个 `execute_tools` 可调用对象，不知道它内部是 ToolRuntime 还是 ExtensionRunner 还是别的实现。这样 C3（依赖单向）成立。

**对标 Pi**：Pi 的 `AgentSession._installAgentToolHooks()` 把 `ExtensionRunner` 的方法填入 `Agent.beforeToolCall` / `Agent.afterToolCall` 等属性。nanoCode 用同样的模式，区别是 tool 拦截点集中在 ToolRuntime 管线内，而不是分散在 loop 中。

### 3.2 AgentSession 是应用层的装配点

`cli/session.py` 承担以下职责：

- **配置转换**：接收 `RuntimeConfig`，统一解析 `self.workspace`，调用 `RuntimeConfig.to_agent_config()` 创建 Agent core 所需的 `AgentConfig`
- **装配**：创建 Agent、Backend、ToolRuntime（注入 `before_tool_call` / `after_tool_call` 回调与大结果持久化 callback）、MemoryRuntime、Harness 组件（compressor/context/persistence 等）、ExtensionRunner
- **桥接**：把 `execute_tools` 回调注入 AgentLoop；把 ExtensionRunner 填入 Agent 的生命周期槽位；在 `run(prompt)` 外层包装事件流，创建内存 `TaskState`、调用 `RunStore`、追加 trace、写 report，并把 stable conversation checkpoint 写入 `SessionLog`
- **持有运行状态**：workspace、permission 确认缓存、ArtifactStore、RunStore、active skills、tool registry、sandbox、MCP、hook manager 等应用对象均在 AgentSession，而不进入 Agent core
- **暴露公共接口**：`chat()`、`compact()`、`abort()`、`clear_history()`、`show_cost()` 等，CLI 和 TUI 共用

当前实现中，`cli/main.py`、TUI 和 server 都通过 `create_session()` / `RuntimeThread` 复用这一个装配点，不再保留入口侧重复装配。

### 3.2.1 Agent core 纯化边界

`Agent` 是纯状态容器和协议辅助对象，只保留：

- provider-neutral canonical conversation history
- token/cost/turn 统计和预算判断
- abort/current task 等 loop 状态
- system prompt、startup context、pending attachments
- 回调槽位和少量状态操作方法

以下内容不允许进入 `Agent`：

- workspace/path/run directory/tool-result 路径
- ToolRegistry、SandboxManager、McpManager、HookManager、SkillInvocation、ActiveSkillManager 等应用对象
- permission mode、confirm cache、sub-agent 语义、provider/API 配置
- 文件 I/O、artifact persistence、run persistence

这些状态由 `AgentSession` 或其装配的 harness/core 组件持有。`AgentLoop` 仍只读写 Agent 状态并消费注入回调。

### 3.3 harness/ 是薄层工具集合，不是重型编排器

和 Pi 的 `AgentHarness`（1200+ 行重型类）不同，nanoCode 的 harness 不做编排。编排逻辑在 `agent/loop.py`（主循环状态机）和 `cli/session.py`（应用装配）。harness 只是提供**横切关注点的工具模块**：压缩、上下文构建、会话与运行工件持久化、权限、确认管理。

这符合 nanoCode 的尺度——Agent 循环本身足够简单，不需要在它外面再包一层编排器。

### 3.4 运行工件持久化

`agent/harness/persistence/` 是框架层的持久化总包，服务 Agent 运转本身，不属于 `cli/core/` 能力模块。它分两类数据：

- `~/.nanocode/sessions/`：多轮会话 checkpoint/resume。
- `<workspace>/.nanocode/runs/`：单次用户请求的审计、benchmark 和复盘工件。

每个顶层 session 只有一个 durable conversation log：

```
~/.nanocode/sessions/<session_id>/
└── session.jsonl
```

`session.jsonl` 是 resume 的事实来源，只记录稳定边界：session header、message、replace、clear、checkpoint。Provider stream 的 `assistant.delta` 不进入 session log，只进入 run trace。恢复时由 `cli/session.py` 重新装配 backend、tools、memory、MCP、hooks 等运行时对象，再把 `session.jsonl` reduce 成 canonical `ConversationHistory` 注入 Agent core。发现 orphaned assistant tool call 时，恢复层补 synthetic error tool result，避免 provider payload 协议错误；未完成 run 只标记 interrupted，不自动重放 provider 或工具。

每次 `AgentSession.run(prompt)` 都创建一个独立 run，默认写入：

```
<workspace>/.nanocode/runs/<run_id>/
├── trace.jsonl
└── report.json
```

两类文件的职责：

- `trace.jsonl`：pico-style 过程事件流，记录 `run_started`、`assistant_delta`、`tool_started`、`tool_executed`、`budget_exceeded`、`runtime_error`、`run_finished`。
- `report.json`：最终摘要，记录运行状态、工具统计、token/cost、runtime 信息和最终答案。

`AgentLoop` 仍保持纯状态机，不 import persistence，也不做文件 I/O。`cli/session.py` 作为装配层消费 `RuntimeEvent` 流并调用 `RunStore` 落盘；这样 one-shot、TUI、server 每次用户请求都能生成一致的 run artifacts。`benchmarks/local-fixture` 以单 run 为默认评测单位，并通过 session log / run trace / report 验证 resume、context governance、permission 和 artifact 合同。

Benchmark runner 不需要传 `--trace-out` 或 `--report-out`。它只需要执行一次普通 nanocode 请求，然后从 workspace 的 `.nanocode/runs/<run_id>/` 读取固定两件套。

### 3.4.1 大工具结果持久化

大工具结果持久化由 `ToolRuntime` 通过 callback 注入：

- `ToolRuntime` 不持有 `Agent`，也不读取 `_tool_results_dir`。
- `AgentSession` 创建 `ArtifactStore`，在 `context_governance != "off"` 时把 `ArtifactStore.write_tool_result` 作为 `persist_large_result` 传给 `ToolRuntime`。
- `ArtifactStore` 写入 `<workspace>/.nanocode/artifacts/tool-results/<call_id>.txt`，并返回 path、size、sha256 等 metadata。
- `ToolRuntime` 只负责把超大结果替换成 `<persisted-output>` 预览，并把 artifact metadata 写进 `ToolResult.metadata`。

这样 artifact I/O 属于 harness persistence，tool 执行管线通过窄 callback 使用它，不把 Agent core 重新耦合进来。

### 3.5 MessageView 统一工具结果读写

当前 conversation 已经是 provider-neutral 结构。`harness/message_view.py` 提供一个小型读写视图，避免 compressor 在多个地方手写遍历 `ConversationMessage` / `ToolResultBlock`：

- `iter_tool_results()` → 遍历所有 canonical 工具结果 block
- `set_content()` → 修改指定工具结果的 content
- `iter_tool_uses()` → 建立 tool_use_id → tool_name 索引

compressor 各层保持为一个循环：Tool History Snip 通过 `MessageView.iter_tool_results()` 找到可裁剪结果，并用 `ToolResultSlot.set_content()` 原地替换内容。

LLM 摘要调用不在 harness 中 import provider 实现；`cli/session.py` 根据 Backend 构造 `summarize_messages()` callable 注入 compressor。

### 3.6 Hook 和 Extension 互补，不互相替代

| | Hook | Extension |
|---|---|---|
| 运行方式 | 外部 shell 进程，JSON stdin/stdout | 进程内 Python |
| 位置 | `agent/harness/hooks/` | `cli/core/extensions/` |
| 能做什么 | allow / deny / modify / append_context | 注册工具、订阅生命周期事件、注册命令 |
| 配置 | settings.json | 丢 .py 到 `.nanocode/extensions/` |

Hook 覆盖"执行前拦截确认"的简单场景（30%），Extension 覆盖"给 Agent 加新能力"的复杂场景（70%）。两者都由 AgentSession 装配进运行管线：Hook 走 `agent/harness/hooks/`，Extension 走 `cli/core/extensions/`，并通过 loop/ToolRuntime 回调触发，互补不冲突。

### 3.7 扩展系统定位

- **代码位置**：`cli/core/extensions/`（应用层，4 个文件 ~260 行）
- **加载时机**：`AgentSession.__init__` 中加载，早于第一次 `chat()`
- **工具注入**：扩展注册的工具通过 ToolRegistry 与内置工具走同一条路径。需在 ToolRegistry 新增单条注册方法，ToolOrigin 增加 `"extension"` 字面量
- **事件桥接**：AgentSession 把 ExtensionRunner 填入 Agent 回调槽位（生命周期事件由 loop.py 触发，tool_call/tool_result 由 ToolRuntime 触发），内核不感知扩展的存在
- **错误隔离**：单个 handler 异常不影响其他 handler 和主流程

---

## 四、落地状态（原 2 个 Phase）

原重构计划针对 4 个结构性问题：核心层反向依赖表现层、compressor 消息遍历重复、入口装配重复、能力与框架混在一起。当前实现已经完成目录迁移和扩展系统落地，本节按原 Phase 结构记录“现在代码中如何体现”，后续维护时用它检查是否回退。

### Phase 1：分层修复 + 接口建立（已落地）

| 项目 | 当前实现 | 对应约束 |
|------|----------|:---:|
| 去除反向依赖 | `agent/agent.py`、`agent/loop.py`、`agent/models.py`、`agent/harness/compressor.py` 不 import `tui`、`cli` 或 provider SDK。状态展示通过 `RuntimeEvent`、callback 和上层 renderer 完成 | C1, C2 |
| Agent 回调槽位 | `Agent.set_callbacks()` 填入 agent/turn 生命周期和 before/after tool 回调；`AgentLoop` 触发生命周期事件，工具拦截由 `ToolRuntime` 触发 | C1, C3 |
| `cli/session.py` 装配点 | `AgentSession` 创建 Agent、Backend、ToolRegistry、SandboxManager、McpManager、SkillInvocation、MemoryRuntime、HookManager、ExtensionRunner、Compressor、RunStore、SessionLog、ArtifactStore 和 AgentLoop | C3 |
| `message_view.py` | `ToolResultSlot` / `MessageView` 遍历 canonical conversation 工具结果，供 Tool History Snip 原地替换旧结果 | — |
| compressor | `Compressor.prepare_context_for_provider()` 先 Tool History Snip，再按阈值 Context Compact；摘要 callable 和 post-compact recovery callable 由 `AgentSession` 注入 | C2, C3 |
| 装配逻辑收敛 | `cli/main.py`、TUI、server 都复用 `create_session()` 或 `RuntimeThread`，不会在入口侧各自组装工具/MCP/memory | C3 |
| 轻量本地记忆 | `cli/core/project/identity.py` 计算 ProjectScope；`cli/core/memory/` 管理 `~/.nanocode/projects/<repo_key>/memory/` 下的 `MEMORY.md` 和三个 topic 文件；不做向量召回、后台抽取或文件事实缓存 | C2, C3 |
| system prompt 模板 | 当前 `STABLE_SYSTEM_PROMPT` 仍在 `agent/harness/context/builder.py`，并通过 `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` 允许 memory 规则等动态插入 | C2 |
| 运行工件持久化 | `SessionLog` 写 `~/.nanocode/sessions/<session_id>/session.jsonl`；`RunStore` 写 `<workspace>/.nanocode/runs/<run_id>/trace.jsonl` 和 `report.json`；`ArtifactStore` 写大工具结果 | C2, C3 |
| Agent core 纯化 | `AgentConfig` 只含 core 参数；workspace、permission、sandbox、MCP、skills、memory、extensions、persistence 都由 `AgentSession` 或 `cli/core` 对象持有 | C1, C3 |

**当前验证口径**：`python -m compileall -q src`、`PYTHONPATH=src python -m unittest discover -s test`、`ruff check src test`，以及 `benchmarks/local-fixture` 的本地 fixture 合同。

### Phase 2：目录迁移 + 扩展系统 + 接口补齐（已落地）

| 项目 | 当前实现 | 对应约束 |
|------|----------|:---:|
| 目录迁移 | 当前包结构已经是 `agent/`、`agent/harness/`、`providers/`、`cli/`、`cli/core/`、`tui/`；旧的 `runtime`、`backend`、`capabilities` 包不在 `src/` 中 | C3 |
| import 更新 | `pyproject.toml` 只声明当前 `nanocode.agent.*`、`nanocode.cli.core.*`、`nanocode.providers`、`nanocode.tui` 等包名 | C3 |
| ToolRegistry 接口 | `ToolRegistry.register()` 支持扩展注册单个工具；`ToolOrigin` 包含 `builtin`、`mcp`、`custom`、`extension` | — |
| Extension 系统 | `cli/core/extensions/api.py`、`loader.py`、`runner.py` 已实现；`AgentSession` 加载 `.nanocode/extensions/*.py` 并桥接生命周期、before/after tool 和扩展命令 | — |
| MCP/Server/Protocol | MCP 实现 stdio transport，HTTP/SSE/WS 只诊断跳过；server 当前 CLI 只开放 `--server stdio`，unix socket 和 websocket transport 文件是占位 | C5 |
| Benchmark 固化 | `benchmarks/local-fixture/tasks.json` 当前 41 个任务，覆盖编辑、权限、安全、resume、memory、context governance 和 run artifacts | C5, C6 |

**当前验证口径**：除了单元测试，还要看 fixture verifier 和 run artifacts。失败排查顺序通常是 `<workspace>/.nanocode/runs/<run_id>/report.json` → `trace.jsonl` → 对应 fixture 的 verifier。

---

## 五、补充约束

（架构层面的核心原则与硬性约束见[零、核心原则与设计约束](#零核心原则与设计约束)。）

以下为工程层面的外部承诺：

1. **外部接口不变**：CLI 参数名、环境变量、JSONL 协议、工具 schema
2. **会话恢复格式不变**：`~/.nanocode/sessions/<session_id>/session.jsonl` 可 resume
3. **不新增依赖**：Python >= 3.10
4. **不强求统一双后端消息格式**：只通过 MessageView 统一读写方式

---

## 附录：当前文件清单与维护映射

> 当前 `src/` 下有 97 个 Python 源文件（不含 `__pycache__` 和 `nanocode.egg-info`）。`pyproject.toml` 的 `[tool.setuptools].packages` 已按这些包名声明。

### 根级

| 当前文件 | 说明 |
|------|------|
| `src/__init__.py` | 包根 |

### providers/ (4 文件)

| 当前文件 | 说明 |
|------|------|
| `src/providers/__init__.py` | `create_backend()` 工厂 |
| `src/providers/base.py` | `Backend` / `BackendResponse` / `TokenUsage` |
| `src/providers/anthropic.py` | Anthropic Messages API 流式后端 |
| `src/providers/openai.py` | OpenAI-compatible Chat Completions 流式后端 |

### agent/ (7 文件)

| 当前文件 | 说明 |
|------|------|
| `src/agent/__init__.py` | Agent 包入口 |
| `src/agent/agent.py` | `AgentConfig`、Agent 状态容器、回调槽位 |
| `src/agent/loop.py` | provider-neutral LLM/tool 状态机 |
| `src/agent/events.py` | RuntimeEvent 工厂函数和 `TurnResult` |
| `src/agent/types.py` | `ToolDef`、`ToolCall`、`ToolResult`、Conversation、RuntimeEvent |
| `src/agent/models.py` | 模型窗口、thinking 能力、retry、schema helper |
| `src/agent/budget.py` | token 估算和费用估算 |

### agent/harness/ (25 文件)

| 当前文件或目录 | 说明 |
|------|------|
| `src/agent/harness/__init__.py` | harness public exports |
| `src/agent/harness/approvals.py` | approval request/decision 管理 |
| `src/agent/harness/compressor.py` | Tool History Snip / Context Compact |
| `src/agent/harness/message_view.py` | canonical conversation 工具结果视图 |
| `src/agent/harness/context/` | system prompt、startup context、project instructions、Git snapshot |
| `src/agent/harness/hooks/` | Hook 类型、settings loader、外部命令 runner |
| `src/agent/harness/permissions/` | policy、rules、shell、workspace、tool allowlist |
| `src/agent/harness/persistence/` | atomic、session log/store、run store、task state、report、artifact |

细分文件：

```text
agent/harness/context/{__init__.py,builder.py,sources.py}
agent/harness/hooks/{__init__.py,config.py,runner.py,types.py}
agent/harness/permissions/{__init__.py,policy.py,rules.py,shell.py,tool_policy.py,workspace.py}
agent/harness/persistence/{__init__.py,artifacts.py,atomic.py,report.py,run_store.py,session_log.py,session_store.py,task_state.py}
```

### cli/ (7 文件)

| 当前文件 | 说明 |
|------|------|
| `src/cli/__init__.py` | CLI 包入口 |
| `src/cli/main.py` | CLI 入口，一次性/TUI/server 模式选择 |
| `src/cli/args.py` | argparse、环境变量和 RuntimeConfig 构造 |
| `src/cli/config.py` | `RuntimeConfig` |
| `src/cli/session.py` | `AgentSession` 总装配点 |
| `src/cli/thread.py` | `RuntimeThread` event-stream wrapper |
| `src/cli/logging_config.py` | logging helper |

### cli/core/ (46 文件的能力层主体)

| 子包 | 文件 | 说明 |
|------|------|------|
| `extensions` | `__init__.py`、`api.py`、`loader.py`、`runner.py` | Python `.py` 扩展、工具注册、事件分发 |
| `mcp` | `__init__.py`、`config.py`、`connection.py`、`manager.py`、`output.py`、`transport.py`、`types.py` | stdio MCP、工具聚合、资源读取、输出保存 |
| `memory` | `__init__.py`、`paths.py`、`runtime.py`、`store.py`、`types.py` | Markdown topic memory |
| `project` | `__init__.py`、`identity.py` | ProjectScope、repo key、项目级数据目录 |
| `protocol` | `__init__.py`、`messages.py` | JSONL protocol request/response/dispatcher |
| `sandbox` | `__init__.py`、`bwrap_backend.py`、`config.py`、`manager.py`、`microsandbox_backend.py`、`types.py` | shell sandbox config/backend/manager |
| `server` | `__init__.py`、`app_server.py`、`transports/{__init__.py,stdio.py,unix_socket.py,websocket.py}` | headless server；当前可用 transport 是 stdio |
| `skills` | `__init__.py`、`prompt.py`、`registry.py`、`runtime.py`、`types.py` | SKILL.md discovery、invocation、active skill |
| `subagents` | `__init__.py`、`orchestrator.py` | built-in/custom 子 Agent 和 fork-return 编排 |
| `tools` | `__init__.py`、`builtin.py`、`recent_files.py`、`registry.py`、`runtime.py`、`types.py` | 内置工具、ToolRegistry、ToolRuntime、compact 恢复辅助 |

### tui/ (7 文件)

| 当前文件 | 说明 |
|------|------|
| `src/tui/__init__.py` | lazy export |
| `src/tui/app.py` | TUI REPL 主循环 |
| `src/tui/commands.py` | slash command registry |
| `src/tui/input.py` | prompt_toolkit / fallback 输入 |
| `src/tui/renderer.py` | Rich renderer 和 live footer |
| `src/tui/state.py` | TUI state 和 command context |
| `src/tui/theme.py` | Console/color helper |

### 历史口径对照

| 历史名称 | 当前实现位置 |
|------|------|
| backend | `src/providers/` |
| runtime agent/loop/events/types/models/pricing | `src/agent/` |
| runtime compressor/approvals/context/session/artifacts | `src/agent/harness/` |
| capabilities/tools | `src/cli/core/tools/` |
| capabilities/sandbox | `src/cli/core/sandbox/` |
| capabilities/skills | `src/cli/core/skills/` |
| capabilities/memory | `src/cli/core/memory/` + `src/cli/core/project/` |
| capabilities/mcp | `src/cli/core/mcp/` |
| capabilities/subagents | `src/cli/core/subagents/` |
| server/protocol | `src/cli/core/server/` + `src/cli/core/protocol/` |

### 当前统计

| 区域 | 源文件数 |
|------|:---:|
| 根级 | 1 |
| `agent/` 含 harness | 32 |
| `providers/` | 4 |
| `cli/` 含 core | 53 |
| `tui/` | 7 |
| **合计** | **97** |
