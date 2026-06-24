# 引言

## 1. 为什么需要 NanoCode

LLM 只能生成文本。让它读文件、搜索代码、跑命令、写文件，需要有人把模型输出翻译成工具调用，把工具结果喂回模型，并持续循环直到任务完成。

NanoCode 的定位是终端里的轻量编程 Agent。它支持 Anthropic 和 OpenAI-compatible 后端，提供工具调用循环、权限确认、shell sandbox、上下文压缩、TUI、headless server、MCP、skills、memory、hooks 和扩展系统。

当前实现的设计偏好是：**轻量、可学习、可审计、分层明确**。核心不是把所有能力塞进 Agent，而是让 Agent core 保持纯净，由应用层按需装配能力。

理解 NanoCode 时可以把它当成三层的组合：

1. **Agent Core**：provider-neutral 的 Agent 状态机和协议，负责“什么时候问模型、什么时候执行工具、什么时候结束”。
2. **Runtime Management**：运行过程管理，负责“让状态机安全、可恢复、可压缩、可审计地运转”。
3. **Application Layer**：应用能力和装配，负责“Agent 实际能做什么”，例如 provider、文件工具、shell sandbox、MCP、skills、memory、sub-agent、CLI/TUI/Server。

这三个层次的分离是后面所有模块设计的基础。很多看似分散的实现选择，例如 `ToolRuntime` 不放进 `AgentLoop`、`Compressor` 通过 callable 调模型、MCP 工具默认 deferred，本质都是为了保持这个边界。

## 2. 核心原则

这些原则是当前代码组织和后续改动的基准。文档描述和实现发生冲突时，以 `src/` 的实际依赖和运行链路为准。

| 原则 | 含义 |
|------|------|
| 分层单向依赖 | 下层不主动 import 上层。Agent core 不知道 CLI/TUI/工具/Provider 的存在，上层通过回调和装配把能力接进来 |
| Agent core 只管状态机和协议 | `agent/agent.py`、`agent/loop.py`、`agent/events.py`、`agent/types.py`、`agent/models.py`、`agent/budget.py` 描述 Agent 状态、对话历史、工具调用、运行事件、模型窗口和预算估算 |
| Runtime Management 管运行过程 | `agent/runtime_management/` 放上下文、压缩、权限、hooks、approvals、session/run/artifact 持久化。它可以 I/O，但不依赖 `cli/`、`tui/`、`providers/` |
| Application Layer 管能力和装配 | `cli/session.py` 是唯一总装配点；`providers/` 和 `cli/core/` 提供 provider、tools、sandbox、skills、memory、MCP、subagents、server/protocol、extensions 等能力和适配模块 |
| Provider adapter 独立 | `providers/` 把 Anthropic/OpenAI-compatible 差异统一成 `BackendResponse`，只依赖 `agent/types.py`、`agent/models.py` 的纯 helper 和自己的 SDK |
| 扩展点通过窄接口接入 | Agent 暴露 runtime callback，Loop 消费 `execute_tools`、`prepare_context_for_provider` 等注入函数；下层不 import 具体扩展系统 |
| 可恢复可审计 | session 通过 append-only `session.jsonl` 恢复；每次请求都有 `.nanocode/runs/<run_id>/trace.jsonl` 和 `report.json` |

## 3. 设计约束

| # | 约束 | 当前代码对应边界 |
|:---:|------|------|
| C1 | Agent core 不 import 文件系统、网络、第三方 SDK、`cli/`、`tui/`、`providers/` | `agent/agent.py`、`loop.py`、`events.py`、`types.py`、`models.py`、`budget.py` 只依赖标准库和 `agent` 内部模块 |
| C2 | `agent/runtime_management/` 不 import `cli/`、`tui/`、`providers/` | Runtime Management 只依赖 `agent/types.py` 和 Runtime Management 内部包；需要 LLM 摘要时由 `cli/session.py` 注入 callable |
| C3 | 核心协议类型只有一份 | `ToolDef`、`ToolCall`、`ToolResult`、`RuntimeEvent` 定义在 `agent/types.py`；工具层从这里 import，不重复定义 |
| C4 | Provider 差异不扩散 | Anthropic/OpenAI 消息转换、tool call 解析、usage 归一都在 `providers/` 内；Loop 只看到 `Backend.call()` |
| C5 | 工具必须经过统一管线 | 工具调用统一走 `ToolRuntime`：allowlist、参数校验、PreToolUse hook、权限、确认、执行、结果落盘、PostToolUse hook |
| C6 | `run_shell` 必须走 sandbox/backend | `run_shell` 不允许从工具层裸跑宿主 shell；执行由 `SandboxManager` 分派到 local/bwrap/microsandbox |
| C7 | 会话恢复格式稳定 | `~/.nanocode/sessions/<session_id>/session.jsonl` 是 resume 事实来源，恢复时会修复 orphan tool call |
| C8 | 外部接口保持稳定 | CLI 参数、环境变量、JSONL protocol、内置工具 schema、run artifact schema 改动要按兼容性处理 |
| C9 | 依赖新增要谨慎 | 基础依赖以 `pyproject.toml` 为准；sandbox 的 `microsandbox` 是 optional extra |

## 4. 架构全景

当前实现按三层理解：

```text
Application Layer
  cli/main.py      tui/        cli/core/server/
       \            |              /
        \           |             /
         └──── cli/session.py ───┘
                  AgentSession：唯一总装配点
                    │
                    ├── providers/
                    │   base / anthropic / openai
                    │
                    ├── cli/core/
                    │   tools / sandbox / skills / memory / mcp
                    │   subagents / server / protocol / extensions
                    │
Runtime Management  │
                    ├── agent/runtime_management/
                    │   context / compressor / persistence
                    │   permissions / hooks / approvals
                    │
Agent Core          │
                    └── agent/
                        Agent / AgentLoop / RuntimeEvent / core types
```

核心运行链路：

```text
用户输入
  -> CLI/TUI/Server
  -> AgentSession
  -> AgentLoop
  -> Provider.call()
  -> assistant text 或 tool_calls
  -> ToolRuntime.execute_many()
  -> 权限/Hook/确认/sandbox/工具执行/artifact
  -> tool_result 回写 ConversationHistory
  -> 下一轮模型调用或 turn.finished
  -> session.jsonl + trace.jsonl + report.json
```

这条链路里有三个不同的“事实来源”：

- `ConversationHistory`：下一次 provider call 会看到什么，是模型上下文的事实来源。
- `session.jsonl`：resume 会恢复什么，是会话持久化的事实来源。
- `trace.jsonl` / `report.json`：评测和排障会看到什么，是运行观测的事实来源。

不要把三者混用。比如 streaming delta 会进入 trace，但不会进入 session log；大工具结果的完整内容会进入 artifact，而 conversation 只保留预览和 metadata。

## 5. 依赖方向

理想方向是上层组合下层，下层不反向感知上层：

```text
agent/runtime_management/  -> agent/
providers/      -> agent/types.py + agent/models.py
cli/session.py  -> agent/ + agent/runtime_management/ + providers/ + cli/core/
tui/            -> cli/session.py / cli/thread.py，以及少量 cli/core skills/memory 辅助
cli/core/tools/ -> agent/types.py + agent/runtime_management permissions/hooks/persistence
```

需要注意几个实际边界：

- `Agent` 暴露回调槽位，`AgentSession` 在运行时填入 extension runner、tool runtime、MCP 初始化、shutdown 和动态附件准备逻辑。
- `AgentLoop` 不 import `ToolRuntime`，只调用 `execute_tools(calls)` 回调。
- `Compressor` 不 import provider；摘要调用由 `AgentSession._summarize_messages()` 注入。
- `providers/` 是 Application Layer 使用的模型 adapter 包。它物理上独立，是为了不让 provider SDK 差异扩散到 Agent Core。
- `cli/core/server/` 虽然位于 `cli/core` 下，但它是 headless adapter，会通过 `cli/thread.py` 创建和提交 runtime thread。
- `cli/session.py` 和 `cli/thread.py` 为 CLI/TUI 渲染做了少量懒加载 `tui.renderer`，但这个依赖停留在 Application Layer，不进入 Agent Core 或 Runtime Management。

配置对象也按层拆分：

| 配置 | 位置 | 职责 |
|------|------|------|
| `AgentConfig` | `agent/agent.py` | core 必须知道的状态机参数：`model`、`message_format`、`thinking`、`max_cost_usd`、`max_turns`、`context_window` |
| `RuntimeConfig` | `cli/config.py` | 应用装配参数：provider/API、permission、sandbox、allowed tools、workspace、sub-agent、自定义 system prompt |
| `SandboxConfig` | `cli/core/sandbox/types.py` | shell 执行隔离参数：backend/profile、workspace mode、network、image、env、extra writable roots |

## 6. 目录结构

`pyproject.toml` 使用 `package-dir = {"nanocode" = "src"}`。下面是当前 `src/` 下和架构相关的实际目录结构，省略 `__pycache__`、egg-info 和测试缓存。

```text
src/
├── __init__.py
├── agent/
│   ├── __init__.py
│   ├── agent.py                  # AgentConfig、Agent 状态容器、回调槽位
│   ├── loop.py                   # Provider-neutral LLM/tool 状态机
│   ├── events.py                 # RuntimeEvent 工厂函数、TurnResult
│   ├── types.py                  # Conversation、ToolCall、ToolResult、RuntimeEvent
│   ├── models.py                 # 模型窗口、thinking、重试和工具 schema helper
│   ├── budget.py                 # token/cost 估算
│   └── runtime_management/       # Runtime Management：上下文、权限、hooks、持久化、恢复
│       ├── __init__.py
│       ├── approvals.py
│       ├── compressor.py
│       ├── message_view.py
│       ├── context/
│       │   ├── __init__.py
│       │   ├── builder.py        # stable system prompt、startup context、动态附件
│       │   └── sources.py        # project instructions、Git snapshot、frontmatter/include
│       ├── hooks/
│       │   ├── __init__.py
│       │   ├── config.py
│       │   ├── runner.py
│       │   └── types.py
│       ├── permissions/
│       │   ├── __init__.py
│       │   ├── policy.py
│       │   ├── rules.py
│       │   ├── shell.py
│       │   ├── tool_policy.py
│       │   └── workspace.py
│       └── persistence/
│           ├── __init__.py
│           ├── artifacts.py
│           ├── atomic.py
│           ├── report.py
│           ├── run_store.py
│           ├── session_log.py
│           ├── session_store.py
│           └── task_state.py
├── providers/
│   ├── __init__.py               # create_backend()
│   ├── base.py                   # Backend / BackendResponse / TokenUsage
│   ├── anthropic.py
│   └── openai.py
├── cli/
│   ├── __init__.py
│   ├── args.py
│   ├── config.py                 # RuntimeConfig
│   ├── logging_config.py
│   ├── main.py
│   ├── session.py                # AgentSession 总装配点
│   ├── thread.py                 # RuntimeThread event-stream wrapper
│   └── core/
│       ├── __init__.py
│       ├── extensions/
│       │   ├── __init__.py
│       │   ├── api.py
│       │   ├── loader.py
│       │   └── runner.py
│       ├── mcp/
│       │   ├── __init__.py
│       │   ├── config.py
│       │   ├── connection.py
│       │   ├── manager.py
│       │   ├── output.py
│       │   ├── transport.py
│       │   └── types.py
│       ├── memory/
│       │   ├── __init__.py
│       │   ├── paths.py
│       │   ├── runtime.py
│       │   ├── store.py
│       │   └── types.py
│       ├── project/
│       │   ├── __init__.py
│       │   └── identity.py
│       ├── protocol/
│       │   ├── __init__.py
│       │   └── messages.py
│       ├── sandbox/
│       │   ├── __init__.py
│       │   ├── bwrap_backend.py
│       │   ├── config.py
│       │   ├── manager.py
│       │   ├── microsandbox_backend.py
│       │   └── types.py
│       ├── server/
│       │   ├── __init__.py
│       │   ├── app_server.py
│       │   └── transports/
│       │       ├── __init__.py
│       │       ├── stdio.py
│       │       ├── unix_socket.py
│       │       └── websocket.py
│       ├── skills/
│       │   ├── __init__.py
│       │   ├── prompt.py
│       │   ├── registry.py
│       │   ├── runtime.py
│       │   └── types.py
│       ├── subagents/
│       │   ├── __init__.py
│       │   └── orchestrator.py
│       └── tools/
│           ├── __init__.py
│           ├── builtin.py
│           ├── recent_files.py
│           ├── registry.py
│           ├── runtime.py
│           └── types.py
└── tui/
    ├── __init__.py
    ├── app.py
    ├── commands.py
    ├── input.py
    ├── renderer.py
    ├── state.py
    └── theme.py
```

## 7. 一条请求如何运行

```text
用户输入 "修这个 bug"
    │
    ├── cli/main.py
    │     └── create_session(config)
    │
    ├── cli/session.py: AgentSession
    │     ├── Agent(config.to_agent_config())
    │     ├── create_backend(config)
    │     ├── ToolRegistry + ToolRuntime
    │     ├── SandboxManager / McpManager / SkillInvocation / MemoryRuntime
    │     ├── HookManager.capture()
    │     ├── ExtensionRunner + load_extensions()
    │     ├── Compressor / RunStore / SessionLog / ArtifactStore
    │     └── AgentLoop(agent, backend, execute_tools=...)
    │
    ├── agent/loop.py: AgentLoop.run()
    │     ├── 应用 UserPromptSubmit hook
    │     ├── 注入 startup context 和动态附件
    │     ├── 调 provider backend
    │     ├── 收到 tool_calls
    │     ├── 调注入的 execute_tools 回调
    │     ├── 追加 tool results
    │     └── 继续循环，直到模型停止、Stop hook 要求继续、出错、abort 或预算耗尽
    │
    ├── cli/core/tools/runtime.py
    │     └── allowlist → 参数校验 → extension before → PreToolUse hook
    │         → 权限 → 确认 → 执行 → 大结果落盘 → extension after → PostToolUse hook
    │
    └── RuntimeEvent 流
          ├── CLI/TUI 渲染
          ├── Server JSONL 转发
          └── RunStore 写 trace/report
```

## 8. 主要模块

| 模块 | 职责 | 关键边界 |
|------|------|---------|
| `agent/` | Agent 状态、LLM/tool 循环、事件、核心类型、模型元数据和费用估算 | 不持有具体能力，不 import 应用层 |
| `agent/runtime_management/` | 压缩、上下文构建、会话持久化、权限、approvals、hooks | 可以 I/O，不依赖表现层和 provider |
| `providers/` | Anthropic/OpenAI-compatible 调用、流式解析、统一返回 `BackendResponse` | 只依赖 core types 和 provider-neutral model helper |
| `cli/session.py` | 创建并连接所有运行对象 | 唯一总装配点 |
| `cli/core/tools/` | 工具 schema、注册、执行管线、deferred 激活、recent files | ToolRuntime 由 Session 创建 |
| `cli/core/sandbox/` | `run_shell` 的执行隔离 | 只管执行边界，不替代权限 |
| `cli/core/skills/` | Skill 发现、参数渲染、active skill 管理 | Skill 是提示词模板，不是代码插件 |
| `cli/core/project/` | ProjectScope、项目身份、项目级数据目录 | memory 复用项目身份，不重复计算 repo key |
| `cli/core/memory/` | 轻量本地 Markdown 记忆：启动注入、显式 `/remember`、索引同步 | MemoryRuntime 由 Session 调用 |
| `cli/core/mcp/` | MCP server 连接、工具聚合、资源读取 | MCP 工具进入 ToolRegistry，默认 deferred |
| `cli/core/subagents/` | fork-return 子 Agent 并发编排 | 子 Agent 独立上下文，复用父会话 sandbox |
| `cli/core/server/` / `protocol/` | headless JSONL server 和协议消息 | 复用 RuntimeThread/AgentSession |
| `cli/core/extensions/` | 进程内 Python 扩展，注册工具、命令、事件订阅 | Agent core 不感知扩展存在 |
| `tui/` | 交互式 REPL、输入补全、slash command、Rich 渲染 | 只在表现层消费 Session/Thread 和少量 metadata helper |

维护时更有用的是看“输入/输出”和“常见修改原因”：

| 模块 | 关键对象 | 主要输入 | 主要输出 | 常见修改原因 |
|------|----------|----------|----------|--------------|
| `agent/` | `Agent`、`AgentLoop`、`RuntimeEvent` | 用户消息、provider response、tool results | canonical conversation、runtime events | 改主循环、预算、事件协议、核心状态 |
| `agent/runtime_management/context` | prompt bundle、startup context、attachments | workspace、project instructions、Git 状态 | system prompt 和 user context | 改系统提示词、AGENTS/rules 读取、动态附件 |
| `agent/runtime_management/compressor.py` | `Compressor`、`MessageView` | conversation、token 压力、summary callable | snipped/compacted conversation | 改上下文治理、compact 策略、post-compact 恢复 |
| `agent/runtime_management/permissions` | `PermissionDecision`、path/rule/shell policy | tool name、tool input、mode、metadata | allow/deny/confirm | 改安全策略、权限模式、settings rule |
| `agent/runtime_management/persistence` | `SessionLog`、`RunStore`、`ArtifactStore` | RuntimeEvent、ConversationHistory、ToolResult | session log、trace、report、artifact | 改 resume、评测观测、artifact schema |
| `providers/` | `Backend`、`BackendResponse`、`TokenUsage` | canonical conversation、tools、system prompt | text、tool calls、usage | 接新模型厂商、修 streaming/tool call 解析 |
| `cli/session.py` | `AgentSession` | `RuntimeConfig`、prompt、callbacks | event stream、run artifacts、conversation commits | 改装配、跨模块桥接、生命周期 |
| `cli/core/tools` | `ToolRegistry`、`ToolRuntime`、`ToolContext` | model tool calls、tool definitions | `ToolResult`、tool events | 新增工具、改执行管线、deferred/allowlist |
| `cli/core/sandbox` | `SandboxConfig`、`SandboxManager`、backend | shell command、cwd、profile | command output | 改 shell 隔离、profile、fallback、env/network |
| `cli/core/memory` | `MemoryRuntime`、topic store | project scope、topic markdown、`/remember` | memory context、topic files | 改长期记忆主题、注入规则、索引同步 |
| `cli/core/mcp` | `McpManager`、`McpConnection` | MCP config、stdio JSON-RPC | MCP tool defs、resources、diagnostics | 改 MCP transport、工具刷新、输出保存 |
| `cli/core/skills` | `SkillRegistry`、`SkillInvocation`、`ActiveSkillManager` | `SKILL.md` metadata/body、args | rendered prompt、active skill context | 改 skill 发现、参数渲染、工具限制 |
| `cli/core/subagents` | `SubAgentOrchestrator` | agent tool task、custom agent md | 子 Agent 摘要结果 | 改 fork-return、并发、agent 类型 |
| `cli/core/server` / `protocol` | `NanoCodeServer`、`RuntimeThread` | JSONL request | runtime.event、response | 改 headless protocol、approval、resume/abort |

## 9. Benchmark 约束面

`benchmarks/local-fixture/tasks.json` 是当前架构的本地行为约束。它不是单独的业务模块，而是用 fixture workspace 验证 Agent/runtime 的关键合同。

当前任务结构：

- 总数：41 个任务。
- suite：`core` 39 个、`permissions` 2 个。
- category：documentation、text-edit、python-bugfix、tool-boundary、recovery、structured-edit、resume、security、permissions、memory、context-governance、run-artifacts。
- scenario：普通任务 37 个、resume 任务 4 个。

这些任务对应的设计压力点：

| Benchmark 面向 | 约束的实现 |
|----------------|------------|
| 工具边界 | 精确编辑、重复文本拒绝、allowed tools、workspace 外写入拒绝、大文件 targeted edit |
| 权限与安全 | deny rule、`dontAsk`、`yolo` 下 protected path 仍需显式确认 |
| Context 治理 | 大工具结果落盘、Tool History Snip、受控 context window |
| Resume | session.jsonl 恢复、orphan tool call 补 synthetic error、旧 run 标记 interrupted |
| Memory | startup memory 注入、memory 与当前文件冲突时以当前文件为准 |
| Run artifacts | `.nanocode/runs/<run_id>/trace.jsonl`、`report.json`、tool/error/usage 指标 |

因此文档描述架构时不能只看模块命名，还要看这些 fixture 是否仍然能解释当前行为。

## 10. Hook 和 Extension 的关系

Hook 和 Extension 不是替代关系。

| 维度 | Hook | Extension |
|------|------|-----------|
| 位置 | `agent/runtime_management/hooks/` | `cli/core/extensions/` |
| 形式 | 外部进程，JSON stdin/stdout | 进程内 Python `.py` |
| 适合 | deny/allow/modify/append_context 这类简单拦截 | 注册工具、注册命令、订阅事件 |
| 装配 | AgentSession 创建 HookManager | AgentSession 加载 ExtensionRunner |
| 触发 | UserPromptSubmit、PreToolUse、PostToolUse、Stop；`PreCompact` 类型和调用点存在，但当前 settings loader 不加载该事件 | Agent runtime event、before/after tool call |

## 11. 推荐阅读顺序

```text
1. cli/session.py                 # 看唯一总装配点
2. agent/agent.py                 # 看 Agent 保存哪些状态和回调槽位
3. agent/loop.py                  # 看主循环如何只依赖注入回调
4. providers/anthropic.py         # 看模型响应如何统一成 BackendResponse
5. cli/core/tools/runtime.py      # 看工具执行管线
6. agent/runtime_management/permissions/     # 看权限如何分层判断
7. agent/runtime_management/compressor.py    # 看上下文治理
8. agent/runtime_management/context/builder.py
9. agent/runtime_management/persistence/     # 看 session/run/artifact 如何审计和恢复
```

按问题类型阅读会更快：

| 任务 | 建议入口 |
|------|----------|
| 新增一个内置工具 | `cli/core/tools/builtin.py` → `registry.py` → `runtime.py` → `04-permissions.md` |
| 修复工具被错误允许/拒绝 | `cli/core/tools/runtime.py` → `agent/runtime_management/permissions/` → `benchmarks/local-fixture` security/permissions case |
| 修复上下文爆炸或 compact 后丢信息 | `agent/runtime_management/compressor.py` → `cli/session.py::_build_post_compact_context` → `11-context.md` |
| 修复 resume 问题 | `agent/runtime_management/persistence/session_log.py` → `session_store.py` → `cli/session.py::restore_from_persistence` |
| 接入新 provider | `providers/base.py` → 现有 provider → `agent/models.py` → `02-backend.md` |
| 排查 Benchmark 失败 | 先看 `.nanocode/runs/<run-id>/report.json`，再看 `trace.jsonl`，最后对照对应 fixture verifier |
| 审查安全边界 | `04-permissions.md` 和 `05-sandbox.md` 一起看；权限决定能否尝试，sandbox 决定 shell 尝试时碰哪里 |

面试式理解可以围绕三个问题自测：

- 如果把某能力塞进 `AgentLoop`，会破坏哪个依赖边界？
- 如果一次 run 成功但 resume 后失败，应该看 conversation、session log 还是 trace？
- 如果模型请求了不可见工具，schema 层、allowlist 层、权限层分别会发生什么？
