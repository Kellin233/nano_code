# nanoCode 重构方案 v6

> 历史设计稿。当前实现以 `00-introduction.md`、`01-runtime.md`、`12-cli-tui-session.md` 和 `13-architecture.md` 为准。

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
│  │  │ 入口+会话│ │ 终端 UI  │ │ HTTP/WS  │ │ JSONL    │        │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘        │   │
│  │                                                               │   │
│  │  ┌──────────────────────────────────────────────────────┐    │   │
│  │  │  能力模块: tools/ sandbox/ skills/ memory/ mcp/      │    │   │
│  │  │             server/ protocol/ extensions/            │    │   │
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
cli/ ──→ agent/harness/ ──→ agent/
cli/ ──→ agent/              (直接使用 Agent/Types/Events/AgentConfig)
cli/ ──→ providers/ ──→ agent/types.py
cli/ ──→ cli/core/
tui/ ──→ cli/session.py
```

配置对象分层：

- `agent/agent.py` 只定义 `AgentConfig`，字段限于 Agent core 必须知道的状态机参数：`model`、`message_format`、`thinking`、`max_cost_usd`、`max_turns`。
- `cli/config.py` 定义 `RuntimeConfig`，持有 provider、API、permission、sandbox、workspace、sub-agent、自定义 system prompt 等应用层装配参数。
- `AgentSession` 负责调用 `RuntimeConfig.to_agent_config()`，并把最终 system prompt、startup context、workspace、persistence、tools、memory、hooks 等运行对象装配到对应层。

**各层 I/O 和依赖约束**：

```
agent/core (agent.py, loop.py, events.py, types.py, models.py, budget.py)
  ← 零 I/O, 零第三方依赖, 不引用 cli/、tui/
agent/harness/    ← 允许 I/O（文件读写、subprocess）, 依赖 agent/
                     不引用 cli/、tui/、providers/
                     需要 LLM 摘要时由 cli/session.py 注入 callable
providers/        ← 只依赖 agent/types.py
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

## 二、目标目录结构

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
    │       ├── compressor.py            # 多层压缩 (用 MessageView, 去 tui 依赖)
    │       ├── message_view.py          # 双后端消息统一读/写视图
    │       ├── approvals.py             # 确认管理 (yolo/ask/deny)
    │       ├── context/                 # 系统提示词 + 启动上下文
    │       │   ├── __init__.py
    │       │   ├── builder.py
    │       │   └── sources.py           # AGENTS.md / .nanocode/rules / Git / frontmatter
    │       ├── persistence/             # 会话与运行工件持久化
    │       │   ├── __init__.py
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
    │       │   ├── registry.py          # ToolRegistry
    │       │   └── runtime.py           # ToolRuntime (扩展的 before/after_tool_call 在此触发)
    │       ├── sandbox/
    │       ├── skills/
    │       ├── project/                 # ProjectScope: 项目身份和项目级数据目录
    │       │   ├── __init__.py
    │       │   └── identity.py
    │       ├── memory/                  # 本地 markdown 记忆
    │       │   ├── paths.py             # memory helper，转发到 project identity
    │       │   ├── types.py
    │       │   ├── store.py
    │       │   └── runtime.py           # ★ MemoryRuntime: system prompt + startup context + 显式写入
    │       ├── mcp/
    │       ├── subagents/
    │       ├── server/
    │       ├── protocol/
    │       └── extensions/              # 插件系统 (后续实现)
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

这取代了当前 `cli/main.py` 中的 `_TuiAgentAdapter` 胶水类和 `runtime/thread.py` 中的重复装配代码。

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

`AgentLoop` 仍保持纯状态机，不 import persistence，也不做文件 I/O。`cli/session.py` 作为装配层消费 `RuntimeEvent` 流并调用 `RunStore` 落盘；这样 one-shot、TUI、server 每次用户请求都能生成一致的 run artifacts。方案 4 的本地 fixture benchmark 以单 run 为默认评测单位，后续可聚合同一 session 下多个 run 支持 multi-turn benchmark。

Benchmark runner 不需要传 `--trace-out` 或 `--report-out`。它只需要执行一次普通 nanocode 请求，然后从 workspace 的 `.nanocode/runs/<run_id>/` 读取固定两件套。

### 3.4.1 大工具结果持久化

大工具结果持久化由 `ToolRuntime` 通过 callback 注入：

- `ToolRuntime` 不持有 `Agent`，也不读取 `_tool_results_dir`。
- `AgentSession` 创建 `ArtifactStore`，把 `ArtifactStore.write_tool_result` 作为 `persist_large_result` 传给 `ToolRuntime`。
- `ArtifactStore` 写入 `<workspace>/.nanocode/artifacts/tool-results/<call_id>.txt`，并返回 path、size、sha256 等 metadata。
- `ToolRuntime` 只负责把超大结果替换成 `<persisted-output>` 预览，并把 artifact metadata 写进 `ToolResult.metadata`。

这样 artifact I/O 属于 harness persistence，tool 执行管线通过窄 callback 使用它，不把 Agent core 重新耦合进来。

### 3.5 MessageView 消除 compressor 双后端分支

compressor 中 Anthropic/OpenAI 消息格式差异导致 ~150 行重复逻辑。`harness/message_view.py` 提供不统一格式、只统一读写方式的视图层：

- `iter_tool_results()` → 遍历所有工具结果，屏蔽 Anthropic content block 和 OpenAI tool role 消息的差异
- `set_content()` → 修改指定工具结果的 content
- `iter_tool_uses()` → 建立 tool_use_id → tool_name 索引

compressor 各层从"两个分支"变成"一个循环"，预期从 443 行降到 ~250 行。

LLM 摘要调用（collapse/compact）不在 harness 中 import provider 实现；`cli/session.py` 根据 Backend 构造 `summarize_messages()` callable 注入 compressor。

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

## 四、重构计划（2 个 Phase）

旧代码存在 4 个结构性问题，本方案要求重构后持续避免这些问题回流：
1. 核心层反向依赖表现层（例如 `agent.py` → `tui.renderer`）
2. compressor 双后端代码重复（每个操作写两遍）
3. `_TuiAgentAdapter` 胶水类 + 装配逻辑重复（`main.py` 和 `thread.py` 各一套）
4. `src/` 目录平铺，能力和框架混在一起

两个 Phase 解决它们：

### Phase 1：修复分层 + 建立接口

**目标**：代码结构先"对"，再"搬家"。新建文件直接放在 `src/` 下目标路径，旧文件暂不移动。

| 任务 | 说明 | 对应约束 |
|------|------|:---:|
| 1a. 去除反向依赖 | `agent.py`、`models.py`、`compressor.py` 全部去掉 `tui.renderer` import。状态输出改用回调或返回结果，由 `cli/session.py` 或 TUI 决定展示方式 | C1, C2 |
| 1b. Agent 暴露回调槽位 | `agent.py` 加 6 个 `_on_*` 属性（空槽位，等待 Phase 2 填入）。`loop.py` 触发 4 个生命周期事件；`loop.py` **不 import ToolRuntime**，改为接受注入的 `execute_tools(calls) → results` 回调 | C1, C3 |
| 1c. 创建 `cli/session.py` | AgentSession 类：装配 Agent+Backend，暴露公共接口。创建 ToolRuntime 并注入 `before_tool_call` / `after_tool_call` 回调。扩展桥接暂用占位 | — |
| 1d. 创建 `agent/harness/message_view.py` | MessageView + ToolResultSlot，统一双后端消息读写 | — |
| 1e. 重写 compressor | 用 MessageView 消除 `if self.use_openai` 分支 | — |
| 1f. 收敛装配逻辑 | `main.py` 删除 `_TuiAgentAdapter`，`thread.py` 改为调用 `create_session()` | — |
| 1g. 接入轻量本地记忆 | `cli/core/project/identity.py` 统一计算 ProjectScope / repo key / 项目级数据目录；`cli/core/memory/` 只管理 `~/.nanocode/projects/<repo_key>/memory/` 下的 `MEMORY.md`、`preferences.md`、`project.md`、`debugging.md`。MemoryRuntime 负责 system prompt 规则、startup context 和显式 `/remember` 写入；不做语义召回、后台提取、文件摘要或 ReadFileTracker | C2, C3 |
| 1h. system prompt 模板化 | `context/builder.py` 中的 `STABLE_SYSTEM_PROMPT` 移到 `agent/harness/context/prompt_template.txt` | — |
| 1i. 接入运行工件持久化 | 在 `agent/harness/persistence/` 增加 `SessionLog`、`RunStore`、内存 `TaskState`、report 构建；`AgentSession.run(prompt)` 包装事件流，默认写 `~/.nanocode/sessions/<session_id>/session.jsonl` 和 `<workspace>/.nanocode/runs/<run_id>/{trace.jsonl,report.json}`，供 resume 与方案 4 本地 fixture benchmark 使用 | C2, C3 |
| 1j. Agent core 纯化 | 新建 `cli/config.py`，把 `RuntimeConfig` 放在应用层；`agent/agent.py` 改为 `AgentConfig(message_format=...)`；workspace/path/tool-result/read-file/app-object 状态全部移到 `AgentSession`；`ToolRuntime` 用 artifact callback；`ToolContext` 改为窄接口 | C1, C3 |

**验证**：编译检查 + 全部单元测试。Phase 1 后所有文件仍在 `src/` 下，`pip install -e .` 正常。

### Phase 2：目录迁移 + 扩展系统 + 接口补齐

**目标**：迁移旧文件到目标结构，实现扩展系统，补齐接口缺口。所有文件仍在 `src/` 下。

| 任务 | 说明 | 对应约束 |
|------|------|:---:|
| 2a. 创建目标目录 | 按 [二、目标目录结构](#二目标目录结构) 完善目录。Phase 1 已建的文件就地保留，补建其余目录 | — |
| 2b. 迁移旧文件 | 将 `src/` 下剩余文件按映射表搬移到新目录（见 [附录](#附录文件迁移映射)） | — |
| 2c. 更新全部 import | 全局替换 import 路径。一次性完成，避免中间状态 | C3 |
| 2d. 更新 pyproject.toml | 更新 `[tool.setuptools].packages`：删除旧包名（`nanocode.backend`、`nanocode.runtime`、`nanocode.capabilities.*`），加入新包名（`nanocode.agent`、`nanocode.agent.harness`、`nanocode.agent.harness.context`、`nanocode.agent.harness.persistence`、`nanocode.agent.harness.permissions`、`nanocode.agent.harness.hooks`、`nanocode.providers`、`nanocode.cli`、`nanocode.cli.core`、`nanocode.cli.core.tools`、`nanocode.cli.core.sandbox`、`nanocode.cli.core.skills`、`nanocode.cli.core.memory`、`nanocode.cli.core.mcp`、`nanocode.cli.core.subagents`、`nanocode.cli.core.server`、`nanocode.cli.core.server.transports`、`nanocode.cli.core.protocol`、`nanocode.cli.core.extensions`、`nanocode.tui`）。入口 `nanocode.cli.main:main` 不变 | — |
| 2e. 实现扩展系统 | 在 `cli/core/extensions/` 实现 api.py + loader.py + runner.py（~260 行）。AgentSession 的桥接代码（Phase 1 占位）填实 | — |
| 2f. 补齐 ToolRegistry 接口 | ToolRegistry 新增单条注册方法；`ToolOrigin` 增加 `"extension"` 字面量 | — |
| 2g. 附带示例插件 | danger_guard.py、todo_writer.py，各 ≤40 行 | — |

**验证**：编译检查 + 全部测试 + `pip install -e .`。

---

## 五、补充约束

（架构层面的核心原则与硬性约束见[零、核心原则与设计约束](#零核心原则与设计约束)。）

以下为工程层面的外部承诺：

1. **外部接口不变**：CLI 参数名、环境变量、JSONL 协议、工具 schema
2. **会话恢复格式不变**：`~/.nanocode/sessions/<session_id>/session.jsonl` 可 resume
3. **不新增依赖**：Python >= 3.10
4. **不强求统一双后端消息格式**：只通过 MessageView 统一读写方式

---

## 附录：文件迁移映射

> 当前 `src/` 下 82 个 .py 文件 → 目标结构。

### 根级

| 当前 | 目标 | 说明 |
|------|------|------|
| `src/__init__.py` | `src/__init__.py` | 包根保留 |

### providers/ (4 文件)

| 当前 | 目标 |
|------|------|
| `src/backend/__init__.py` | `providers/__init__.py` |
| `src/backend/base.py` | `providers/base.py` |
| `src/backend/anthropic.py` | `providers/anthropic.py` |
| `src/backend/openai.py` | `providers/openai.py` |

### agent/ (7 文件, 含 1 新建)

| 当前 | 目标 | 说明 |
|------|------|------|
| `src/runtime/__init__.py` | `agent/__init__.py` | |
| `src/runtime/agent.py` | `agent/agent.py` | 暴露回调槽位 |
| `src/runtime/loop.py` | `agent/loop.py` | 生命周期事件 + 注入 `execute_tools` 回调 |
| `src/runtime/events.py` | `agent/events.py` | |
| — | `agent/types.py` | 新建：核心类型 ToolDef / ToolCall / ToolResult / RuntimeEvent |
| `src/models.py` | `agent/models.py` | 去 tui 依赖 |
| `src/pricing.py` | `agent/budget.py` | |

### agent/harness/ (23 文件, 含 6 新建)

| 当前 | 目标 | 说明 |
|------|------|------|
| `src/runtime/compressor.py` | `agent/harness/compressor.py` | 用 MessageView, 去 tui 依赖 |
| `src/runtime/approvals.py` | `agent/harness/approvals.py` | |
| `src/context/__init__.py` | `agent/harness/context/__init__.py` | |
| `src/context/builder.py` | `agent/harness/context/builder.py` | |
| `src/context/sources.py` | `agent/harness/context/sources.py` | |
| `src/session/__init__.py` | `agent/harness/persistence/session_store.py` | session discovery / load_session / list_sessions |
| `src/session/artifacts.py` | `agent/harness/persistence/artifacts.py` | |
| — | `agent/harness/persistence/__init__.py` | 新建 |
| — | `agent/harness/persistence/session_log.py` | 新建：durable session checkpoint/resume |
| — | `agent/harness/persistence/run_store.py` | 新建：单次请求 trace/report 落盘 |
| — | `agent/harness/persistence/task_state.py` | 新建：单次请求内存状态 |
| — | `agent/harness/persistence/report.py` | 新建：run report 构建与汇总指标 |
| `src/capabilities/permissions/__init__.py` | `agent/harness/permissions/__init__.py` | |
| `src/capabilities/permissions/policy.py` | `agent/harness/permissions/policy.py` | |
| `src/capabilities/permissions/rules.py` | `agent/harness/permissions/rules.py` | |
| `src/capabilities/permissions/workspace.py` | `agent/harness/permissions/workspace.py` | |
| `src/capabilities/permissions/shell.py` | `agent/harness/permissions/shell.py` | |
| `src/capabilities/hooks/` (4 文件) | `agent/harness/hooks/` (4 文件) | |
| — | `agent/harness/__init__.py` | 新建 |
| — | `agent/harness/message_view.py` | 新建 |

### cli/ (6 文件)

| 当前 | 目标 | 说明 |
|------|------|------|
| `src/cli/__init__.py` | `cli/__init__.py` | |
| `src/cli/main.py` | `cli/main.py` | 无 Adapter |
| `src/cli/args.py` | `cli/args.py` | |
| `src/runtime/thread.py` | `cli/thread.py` | |
| `src/logging_config.py` | `cli/logging_config.py` | |
| — | `cli/session.py` | AgentSession 新建 |

### cli/core/ (44 文件, 含 6 新建)

| 当前 | 目标 | 说明 |
|------|------|------|
| `src/capabilities/tools/` (5 文件) | `cli/core/tools/` (5 文件) | types.py 从 agent/types.py import 核心类型（ToolDef 等），本文件只放 ToolContext / FunctionTool / ToolMetadata |
| `src/capabilities/sandbox/` (7 文件) | `cli/core/sandbox/` (6 文件) | backend.py 合并到 types.py |
| `src/capabilities/skills/` (5 文件) | `cli/core/skills/` (5 文件) | |
| — | `cli/core/project/` (identity) | ProjectScope、项目身份和项目级数据目录 |
| `src/capabilities/memory/` | `cli/core/memory/` (paths/types/store/runtime) | 轻量本地 markdown 记忆；paths.py 不重复计算 repo key |
| — | `cli/core/memory/runtime.py` | **新建**: MemoryRuntime — system prompt + startup context + 显式写入 |
| `src/capabilities/mcp/` (8 文件) | `cli/core/mcp/` (7 文件) | resources.py 合并到 types.py |
| `src/capabilities/subagents/` (2 文件) | `cli/core/subagents/` (2 文件) | |
| `src/server/` (6 文件) | `cli/core/server/` (6 文件) | 含 transports/ 子包 |
| `src/protocol/` (3 文件) | `cli/core/protocol/` (2 文件) | dispatcher.py 合并到 messages.py |
| — | `cli/core/__init__.py` | 新建 |
| — | `cli/core/extensions/` (4 文件) | Phase 2e 新建 |

### tui/ (7 文件)

| 当前 | 目标 |
|------|------|
| `src/tui/` (7 文件) | `tui/` (7 文件) |

### 统计

> 当前 `src/` 下 82 个 .py 文件。计算：82 - 1(删除) - 3(合并删除) = 78(迁移) + 14(新建) = 92(目标合计)。

| 类别 | 文件数 |
|------|:---:|
| 迁移 | 78 |
| 合并删除（旧文件内容迁走后移除） | 3 (sandbox/backend.py, mcp/resources.py, protocol/dispatcher.py) |
| 删除（不再需要） | 1 (capabilities/__init__.py) |
| 新建 (Phase 1) | 8 (agent/types.py, memory/runtime.py, message_view.py, session.py, persistence/__init__.py, persistence/run_store.py, persistence/task_state.py, persistence/report.py) |
| 新建 (Phase 2) | 6 (extensions/*.py ×4, harness/__init__.py, cli/core/__init__.py) |
| **目标合计** | **92** |
