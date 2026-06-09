# 引言

## NanoCode 是什么

NanoCode 是一个轻量级编程智能体 CLI 工具，受 Claude Code 启发，纯 Python 实现。支持 Anthropic 和 OpenAI 兼容接口，内置 TUI 交互模式和 headless server 模式。

**一句话定位**：把 LLM 变成能读代码、写文件、跑命令的编程助手，跑在你的终端里。

## 技术栈

- Python >= 3.10，`asyncio` + `dataclasses` + `pathlib`
- 核心依赖：`anthropic`、`openai`、`prompt_toolkit`、`rich`
- 可选依赖：`microsandbox`（容器沙箱）
- 构建：`setuptools`，入口 `nanocode.cli.main:main`

## 架构全景图

```
                         cli / tui / server        ← 表现层
                                │
                         runtime/  ★内核★
                         ╱         ╲
                   backend/     capabilities/      ← 策略类 / 可插拔能力
                                │
                     context/    models.py         ← 提示词 / 元数据
                     session/    protocol/         ← 持久化 / 协议
```

**依赖方向单向**。上层 import 下层，下层绝不 import 上层：`cli/` → `runtime/` → `backend/` / `capabilities/` / `context/` / `models.py`。`backend/` 和 `capabilities/` 之间不互相引用。

## 十个模块

### `cli/` — 入口，2 文件

| 文件 | 干什么 |
|------|--------|
| `args.py` | argparse 定义所有 CLI 参数。`resolve_runtime_config(args)` 把 CLI 参数 + 环境变量合并成一个 `RuntimeConfig` 对象 |
| `main.py` | `main()` 入口。创建三个对象——`Agent(config)`、`create_backend(config)`、`AgentLoop(agent, backend)`——然后根据有没有 prompt 选 TUI 模式还是一次性模式 |

`cli/` 没有任何对话逻辑，只负责**组装依赖 + 选择启动模式**。

### `runtime/` — 内核，6 文件

一次对话的所有状态和行为都在这里。

| 文件 | 干什么 |
|------|--------|
| `agent.py` | **Agent 纯状态容器**（~580 行）。持有消息历史（`_anthropic_messages` / `_openai_messages`）、token 计数、ToolRegistry、SandboxManager、McpManager、HookManager 等。不实现行为 |
| `loop.py` | **AgentLoop**（~300 行）。`run(prompt)` 驱动完整对话循环。通过 `Backend` 接口调用模型，不区分 Anthropic/OpenAI。产出 `AsyncIterator[RuntimeEvent]` 流 |
| `compressor.py` | **Compressor**（~260 行）。三层压缩：利用率 > 50% 裁剪超长结果 → > 60% 替换陈旧结果 → > 85% 调模型生成摘要 |
| `events.py` | **RuntimeEvent** 数据类 + 工厂函数（`ToolCallStarted(call)` → `RuntimeEvent(type="tool.started")`）。事件被 TUI/CLI/Server 消费 |
| `thread.py` | **RuntimeThread**。Server 模式的公开入口，封装 AgentLoop 的异步事件流管理 |
| `approvals.py` | **ApprovalManager**。确认/拒绝流程管理 |

**关键关系**：`Agent` 是被动的数据容器。`AgentLoop` 读取 Agent 的状态、调用 Backend、委托 ToolRuntime 执行工具、委托 Compressor 压缩上下文——但不持有这些状态。

### `backend/` — 模型调用，3 文件

| 文件 | 干什么 |
|------|--------|
| `base.py` | `Backend` 抽象类 + `BackendResponse`（text + tool_calls + usage）+ `TokenUsage` |
| `anthropic.py` | `AnthropicBackend`。处理 Messages API 流式事件：`content_block_start` 检测 tool_use→`content_block_delta` 拼接参数→`content_block_stop` 解析。thinking block 过滤掉 |
| `openai.py` | `OpenAIBackend`。处理 Chat Completions 流式：按 index 拼接增量 `function.arguments`，统一返回 `BackendResponse` |

### `capabilities/` — 7 个能力模块

每个子模块遵循**共同模板**：`types.py`（数据模型）+ 引擎文件（按独立变更原因拆）。

**tools/** — 工具系统（4 文件）

| 文件 | 干什么 |
|------|--------|
| `types.py` | `ToolCall`、`ToolResult`、`ToolContext`、`FunctionTool`、`ToolMetadata` + 所有常量 |
| `builtin.py` | 12 个内置工具的 JSON Schema + 实现函数 |
| `registry.py` | `ToolRegistry`。注册/查找/deferred 激活/MCP 工具合并 |
| `runtime.py` | `ToolRuntime`。执行管线：验证→权限→Hook→执行→后处理 |

**permissions/** — 权限检查（4 文件）

`policy.py` 是入口——`check_permission()` 按顺序检查：
1. `workspace.py`：路径边界（protected paths、workspace 外），不可被 bypassPermissions 绕过
2. `rules.py`：用户 allow/deny 规则（`settings.json`），deny 不可绕过
3. `shell.py`：正则匹配危险命令（rm、sudo、curl\|sh）
4. 权限模式判断——bypassPermissions 跳过 confirm 但不跳过 deny

**sandbox/** — Shell 沙箱（6 文件）

Profile/Backend/Policy 三层分离。`SandboxManager` 按 config 选 backend（bwrap/local/microsandbox）。**只管 `run_shell`**，文件工具在宿主机执行靠权限保护。

**subagents/** — 子 Agent（2 文件）

3 种内置类型（explore/plan/general）+ 自定义发现 + `SubAgentOrchestrator` 并行编排器。

**mcp/** — MCP 协议（8 文件）

Stdio transport + JSON-RPC 通信 + 工具注册到 ToolRegistry（`mcp__server__tool`）。

**skills/** — 技能系统（4 文件）

三层阶段式披露。只在调用时才加载完整 SKILL.md 正文。

**hooks/** — Hook 生命周期（3 文件）

PreToolUse/PostToolUse/Stop 事件的配置加载和进程执行。

**memory/** — 长期记忆（4 文件）

文件式存储，本地多视角匹配 + LLM 侧查询精选。

### `context/` — 提示词，2 文件

| 文件 | 干什么 |
|------|--------|
| `builder.py` | 稳定 system prompt 模板（利于 prompt caching）+ 启动上下文 + 动态附件渲染 |
| `sources.py` | CLAUDE.md 加载（优先级链 + include 解析）、Git 快照、frontmatter |

### 其余

| 模块 | 干什么 |
|------|--------|
| `models.py` | 模型元数据：上下文窗口、thinking 支持、输出 token、重试策略、工具 schema 转换 |
| `tui/` (5 文件) | TuiApp 驱动 REPL，prompt_toolkit 输入，Rich 渲染 |
| `server/` (4 文件) | NanoCodeServer + stdio/websocket/unix transport |
| `protocol/` (2 文件) | JSONL 消息定义 + dispatcher |
| `session/` (3 文件) | SessionEventStore（JSONL 追加）+ ArtifactStore（大结果落盘） |
| `logging_config.py` | 日志配置 |

## 一条请求贯穿全部模块

```
用户: "修 agent.py 的 bug"

1. cli/main.py: main()
   → Agent(config)                    # runtime/agent.py
   → create_backend(config)           # backend/__init__.py
   → AgentLoop(agent, backend)        # runtime/loop.py

2. AgentLoop.run(prompt)
   → agent.inject_startup_context()    # 注入 CLAUDE.md + Git + 日期
   → backend.call()                    # backend/anthropic.py
   → 模型返回 tool_calls
   → ToolRuntime.execute_one()         # capabilities/tools/runtime.py
       → check_permission()            # capabilities/permissions/policy.py
       → _confirm_dangerous()          # 用户确认
       → tool.call()                   # 执行工具
       → 大结果落盘                    # session/artifacts.py
   → 结果追加到消息历史
   → Compressor.run_pipeline()         # runtime/compressor.py
   → 循环直到 LoopFinished("stop")

3. RuntimeEvent 流 → TUI 渲染 / 一次性模式输出
4. agent._auto_save() → session/__init__.py
```

## 核心设计决策

1. **Agent 是纯状态容器**：不实现行为。循环、API 调用、压缩分别由 AgentLoop、Backend、Compressor 实现。从 Mixin 模式改过来，消除隐式耦合

2. **Backend 是策略类**：Anthropic/OpenAI 封装在独立类中，AgentLoop 只依赖 Backend 接口。新增模型厂商只需加一个文件

3. **双消息历史不统一**：`_anthropic_messages` 和 `_openai_messages` 分开存储——两者的 tool_use/tool_result 格式差异太大，强行抽象增加复杂度

4. **事件用工厂函数**：`ToolCallStarted(call)` → `RuntimeEvent(type="tool.started")`——不用 isinstance 判断链

5. **capabilities 不抽象统一基类**：每个 capability 接口天然不同——tools 需要 registry+runtime，hooks 只需要 runner+config
