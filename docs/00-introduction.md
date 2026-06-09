# 引言

## 1. 为什么需要 NanoCode

LLM 只能生成文本。让它读文件、搜索代码、跑命令、写文件——这些动作需要有人把文本翻译成工具调用，把工具结果喂回模型，循环直到任务完成。

这就是 NanoCode 的角色：**终端里的编程 Agent**。用户输入一句话，Agent 调用 LLM，模型通过工具和项目交互，循环直到任务完成或预算耗尽。

在同类工具中（Claude Code、Codex CLI、Aider），NanoCode 的设计偏好是**轻量 + 可学习 + 可审计**。没有插件市场、没有分布式架构、没有复杂的权限系统。55 个源文件，275 个测试，一个人一个下午能读完全部核心代码。

和 Claude Code 比，Nanocode 更"透明"——每个设计决策都在本文档中有解释。和 Aider 比，Nanocode 有更完整的 Agent 能力（子 Agent、Sandbox、MCP、记忆）。和 Codex CLI 比，Nanocode 的代码量更小、学习曲线更平。

## 2. 核心概念

### 2.1 三个角色

整个系统可以从三个角色来理解：

```
用户输入
    │
    ▼
AgentLoop（导演）
    │
    ├── 读 Agent（数据仓库）的状态
    ├── 调 Backend（翻译官）与模型通信
    ├── 委托 ToolRuntime 执行工具
    ├── 委托 Compressor 压缩上下文
    └── 产出 RuntimeEvent 流 → 渲染到终端
```

**Agent**是被动的数据仓库。它持有消息历史、token 计数、工具注册表、能力模块（SandboxManager、McpManager 等）的引用。它不主动做任何事——没有循环逻辑、没有 API 调用、没有压缩策略。

**AgentLoop**是导演。它驱动"用户输入→模型调用→工具执行→再调模型"的完整循环。它读取 Agent 的状态，通过 Backend 接口调用模型（不区分 Anthropic/OpenAI），委托 ToolRuntime 执行工具，委托 Compressor 压缩上下文。它产出 `AsyncIterator[RuntimeEvent]` 流，供不同的消费端（一次性模式、TUI、Server）各自消费。

**Backend**是翻译官。Anthropic 和 OpenAI 的 API 细节完全不同——流式事件格式不同、消息格式不同、thinking 机制不同。Backend 封装了这些差异，AgentLoop 只看到统一的 `BackendResponse(text, tool_calls, usage)`。

### 2.2 一条请求贯穿全系统

```
用户输入 "修 agent.py 的 bug"
    │
    ├── cli/main.py：main()
    │     → Agent(config)                    # runtime/agent.py
    │     → create_backend(config)            # backend/__init__.py
    │     → AgentLoop(agent, backend)         # runtime/loop.py
    │
    ├── AgentLoop.run(prompt)
    │     → inject_startup_context()          # 注入 CLAUDE.md + Git + 日期
    │     → prepare_initial_attachments()     # Skill 列表 + Deferred 工具
    │     → backend.call(messages, system, tools, on_text_delta)
    │         → AnthropicBackend 流式解析 content_block_start/delta/stop
    │         → 边收文本边通过 asyncio.Queue yield RuntimeEvent
    │     → response.tool_calls: [read_file("agent.py"), grep_search("def.*bug")]
    │     → ToolRuntime.execute_many(calls, ctx)
    │         → 按并发安全性分组 batch
    │         → execute_one: 验证→PreToolUse hooks→权限→确认→执行
    │         → read_file 走 builtin.py，run_shell 走 SandboxManager
    │     → _append_tool_results() 追加结果到消息历史
    │     → Compressor.run_pipeline()         # Budget→Snip→Microcompact
    │     → backend.call() 再调模型...
    │     → 循环直到 model 不再返回 tool_calls → LoopFinished("stop")
    │
    ├── RuntimeEvent 流被消费
    │     → 一次性模式：_render_event() 直接打印
    │     → TUI 模式：TuiApp._chat() Rich 渲染
    │     → Server 模式：JSONL 转发
    │
    └── agent._auto_save() → session/__init__.py   # 保存到 ~/.nanocode/sessions/
```

### 2.3 十层架构

```
                        cli / tui / server          ← 表现层：用户入口
                               │
                        runtime/  ★内核★            ← Agent 状态 + 主循环 + 压缩
                        ╱         ╲
                  backend/     capabilities/        ← 模型调用(策略) / 能力模块(7个)
                              │
                    context/    models.py           ← 提示词构建 / 模型元数据
                    session/    protocol/           ← 持久化 / 协议层
```

**依赖方向严格单向**：`cli/tui/server` → `runtime` → `backend` / `capabilities` / `context` / `models`。下层绝不 import 上层。`backend` 和 `capabilities` 之间不互相引用。`capabilities` 子模块之间允许有限交叉引用（如 `tools.runtime` → `hooks`）。

## 3. 总体设计

### 3.1 模块全景

| 模块 | 文件数 | 核心职责 | 为什么独立 |
|------|:--:|------|------|
| `cli/` | 2 | 参数解析 + 依赖组装 | 启动逻辑独立于对话逻辑 |
| `tui/` | 5 | 交互式 REPL、Rich 渲染 | 渲染策略独立于 Agent 逻辑 |
| `server/` | 4 | JSONL 协议 + 多 transport | headless 模式独立于 TUI |
| `runtime/` | 6 | Agent 状态 + 主循环 + 压缩 + 事件 | 内核，变更原因集中的地方 |
| `backend/` | 3 | 模型后端策略类 | 模型差异封装，不与 Agent 耦合 |
| `capabilities/tools/` | 4 | 12 个内置工具 + 注册 + 执行 | 工具系统是最大能力模块 |
| `capabilities/permissions/` | 4 | 四种模式 + 四层检查 | 安全策略独立于工具实现 |
| `capabilities/sandbox/` | 6 | Shell 沙箱（bwrap/microsandbox/local） | 执行隔离独立于权限 |
| `capabilities/subagents/` | 2 | 子 Agent 编排 | 并行编排逻辑独立于工具执行 |
| `capabilities/skills/` | 4 | Skill 发现 + 调用 + 激活管理 | 模板系统独立于工具系统 |
| `capabilities/hooks/` | 3 | 生命周期钩子 | Hook 逻辑独立于工具执行 |
| `capabilities/memory/` | 4 | 文件式长期记忆 | 记忆逻辑独立于上下文管理 |
| `capabilities/mcp/` | 8 | MCP 协议集成 | 外部协议独立于内置工具 |
| `context/` | 2 | System prompt + CLAUDE.md + 附件 | 提示词构建独立于 Agent |
| `models.py` | 1 | 模型元数据 + 重试策略 | 被多方引用，不适合放在任何子包 |
| `session/` | 3 | 会话持久化 + 大结果落盘 | 持久化独立于对话逻辑 |
| `protocol/` | 2 | JSONL 消息协议 | 协议独立于服务端实现 |

### 3.2 能力模块的共同模板

每个 `capabilities/<name>/` 遵循相同的结构约定：

```
capabilities/<name>/
├── __init__.py       # 公共导出
├── types.py          # 本模块的数据模型（共同模板）
└── <engine>.py       # 运行时引擎（按变更原因拆 1~N 个）
```

这是刻意设计的一致性——看完 `tools/` 的结构，不用查文档就能推断 `skills/` 的入口在哪。`types.py` 短不是问题——一致性本身就是价值。

### 3.3 文件合并记录

重构过程中做了若干文件合并，每次合并的理由都是"同一变更原因"：

| 合并 | 合并前 | 合并后 | 理由 |
|------|--------|--------|------|
| tools 数据模型 | types.py + base.py + constants.py | types.py | 改 ToolCall 结构时三者总是一起改 |
| tools 内置工具 | definitions.py + builtin.py | builtin.py | 加新工具时 schema 和实现必须同时改 |
| skills 运行时 | invocation.py + active.py | runtime.py | 调用 skill 后必然记录激活状态 |
| memory 召回 | retrieval.py + rendering.py | retrieval.py | 召回和格式化总是同一流程 |
| context 构建 | system_prompt + startup + prompt + attachments + types | builder.py | 全部是"组装提示词"的不同步骤 |
| context 数据源 | claude_md + git_context + frontmatter | sources.py | 全部是"从文件系统提取上下文" |

## 4. 详细设计

### 4.1 Agent（`runtime/agent.py`，590 行）

Agent 是项目最大的文件。它的 `__init__` 做了三件事：

**实例化能力模块**：`ToolRegistry(builtin_tool_definitions())` 加载所有内置工具，`SandboxManager(config.sandbox_config)` 创建沙箱管理器，`McpManager(on_tools_changed=...)` 创建 MCP 连接管理器，`SkillInvocation()` 和 `ActiveSkillManager()` 创建 Skill 运行时。

**初始化状态字段**：消息历史（`_anthropic_messages` 和 `_openai_messages` 两个独立列表）、token 计数（`total_input_tokens`、`total_output_tokens`、`current_turns`）、权限状态（`_confirmed_paths`、`_read_file_state`）、上下文状态（`_pending_context_attachments`、`_startup_context_injected` 等标志位）。

**构建系统提示词**：根据 Agent 类型（主 Agent / 子 Agent / 自定义 system prompt）走不同路径。主 Agent 调用 `_build_prompt_bundle()` 同时获得 system_prompt 和 startup_context。

Agent 的公开方法分为三类。**状态读写**：`messages`（property，根据 `use_openai` 返回正确列表）、`tool_definitions()`（排除 skill 禁用的）、`budget_exceeded()`。**消息操作**：`add_user_message()`、`add_assistant_message()`、`add_tool_results()`、`append_user_context()` 都在内部根据 `use_openai` 路由到 Anthropic 或 OpenAI 格式。**生命周期**：`ensure_mcp_initialized()`（首次调用时连接 MCP）、`shutdown()`（断开 MCP + 停止 sandbox）、`run_once()`（子 Agent 入口）。

### 4.2 AgentLoop（`runtime/loop.py`，326 行）

`run(user_message)` 方法是整个系统最核心的函数。它分为准备阶段和主循环阶段：

**准备阶段（步骤 1-8）**：注入启动上下文（仅首次）、准备动态附件（仅首次）、刷新挂起附件、添加用户消息、初始化 MCP（仅首次）、检查是否需要 compact、运行 UserPromptSubmit hooks、启动异步记忆预取。

**主循环（步骤 9）**：`while True` 循环中，每轮先跑三层压缩，再消费记忆预取结果，然后调模型。模型调用使用两个并发任务——`create_task(backend.call(...))` 异步执行，`text_events: asyncio.Queue` 每 50ms 取一次文本增量 yield 给消费端。模型返回后，追加 assistant 消息。如果没有 tool_calls，检查 Stop hook 决定继续还是结束。如果有 tool_calls，预算检查 → `_execute_tools()` → 追加结果 → 刷新附件 → 继续循环。

**`_execute_tools()` 的关键设计**：它把 Agent 身上的能力模块打包成 `ToolContext`——`read_file_state`（先读后改）、`sandbox_manager`（run_shell 走）、`mcp_manager`（MCP 工具走）、`agent` 自身（agent/skill 工具走）。ToolRuntime 通过 ToolContext 拿到一切，不直接引用 Agent。

### 4.3 Compressor（`runtime/compressor.py`，264 行）

压缩不是"生成摘要"——那是最后手段。常态是三层递进：

**Budget（利用率 > 50%）**：纯字符串操作，零 API 成本。遍历消息列表，对超长工具结果做头尾保留、中间截断。为什么先做 Budget？超长工具结果（如 grep_search 返回 10 万行）是上下文膨胀的首要原因。

**Snip（利用率 > 60%）**：Anthropic 特定优化。先扫所有 assistant 消息建 `tool_use_id → {name, input}` 索引，再扫 user 消息中的 tool_result。对于 `read_file` 类型的结果，同一文件的多次读取只保留最后一次——前面的全替换为 `[Content snipped - re-read if needed]`。OpenAI 路径是简化版——按位置保留最近 N 个 tool 消息。

**Compact（利用率 > 85%）**：调用模型生成摘要（`max_tokens=2048`），重置消息历史为 `摘要 + 最后一条用户消息 + "Understood..."`，然后通过 `agent.append_user_context()` 重挂 active skill 上下文。compact 失败时降级——不中断会话。

**Microcompact（空闲 > 5 分钟）**：不依赖利用率。基于判断——用户离开了一段时间，旧工具结果可能已经过时。清除操作标记为 `[Old result cleared]`。

### 4.4 RuntimeEvent + 事件流（`runtime/events.py`，88 行）

`RuntimeEvent` 是一个 frozen dataclass——`type`（字符串）、`payload`（dict）、`thread_id`、`seq`、`timestamp`。用工厂函数替代子类：

```python
ToolCallStarted(call)     # → RuntimeEvent(type="tool.started")
ToolCallFinished(c, r)    # → RuntimeEvent(type="tool.finished")
LoopFinished("stop")      # → RuntimeEvent(type="turn.finished")
BudgetExceeded(reason)    # → RuntimeEvent(type="budget.exceeded")
```

类型判断用 `event.type == "tool.started"` 而不是 `isinstance(event, ToolCallStarted)`——工厂函数模式比子类继承链更轻量。

三种消费端各自消费事件流：一次性模式用 `_render_event()` 根据 `event.type` 调用不同的 `get_renderer()` 方法；TUI 模式通过 `TuiApp._chat()` 驱动 Rich 渲染；Server 模式用 `event.to_dict()` 转 JSONL。

## 5. 设计决策

### 决策 1：Agent 从 Mixin 改为纯状态容器

**问题**：最初的 Agent 通过 3 个 Mixin 拼装行为——`AgentContextMixin`（上下文管理）、`AgentToolRuntimeMixin`（工具执行）、`AgentBackendMixin`（API 调用）。Mixin 通过 `self._anthropic_messages` 隐式访问 Agent 状态——改了字段名，Mixin 就崩溃。阅读完整行为需要跨 `core.py`、`context.py`、`tools_runtime.py`、`backends.py` 四个文件，每个 Mixin 都可能修改同一个字段。

**可选方案**：保持 Mixin 但加类型检查；改为纯组合（每个行为独立类）；改为纯状态容器 + 外部行为。

**选择**：改为纯状态容器 + 外部行为。Agent 只持有数据，AgentLoop 负责调度，Compressor 负责压缩，Backend 负责 API 调用。

**代价**：AgentLoop 的构造函数需要显式传入 Agent 和 Backend 两个参数。Compressor 每次使用时需要 `Compressor(agent)` 创建实例。但换来的好处是每个文件的变更原因独立——改压缩策略只改 `compressor.py`，改循环逻辑只改 `loop.py`。加新能力（如加一个新模型厂商）不需要动 Agent 核心。

### 决策 2：双后端消息历史不统一

**问题**：Anthropic 的 `tool_use`/`tool_result` 嵌套在 `content[]` 列表中，OpenAI 的 `function call` 和 `role: tool` 是独立 message。要不要抽象一个"通用消息模型"？

**可选方案**：创建 `MessageStore` 抽象层，内部做格式转换；或分开存储，在 API 边界处路由。

**选择**：分开存储，不统一。`_anthropic_messages` 和 `_openai_messages` 是两个独立列表，Agent 的公开方法（`add_user_message`、`add_assistant_message`、`add_tool_results`、`append_user_context`）在内部根据 `config.use_openai` 路由。

**为什么不做中间层**：两种格式的语义结构差异太大——Anthropic 的 tool_result 是 user message content 的一部分，OpenAI 的 tool 是独立 role。做中间层需要引入双向转换——增加一层抽象但不减少任何代码量。两份简单的原生操作比一层复杂的抽象好维护。

**代价**：Compressor 里每种压缩操作都要写两份（Anthropic 版本和 OpenAI 版本）。但这个代价是可接受的——压缩逻辑本身是简单的字符串处理，分两份不会引入新的复杂度。

### 决策 3：capabilities 不抽象统一基类

**问题**：7 个能力模块（tools/mcp/skills/hooks/memory/sandbox/permissions）要不要抽象出一个统一的 `Capability` 基类？

**可选方案**：定义 `Capability` 抽象类，要求所有模块实现 `initialize()/contribute_tools()/shutdown()`；或者保持各自独立的接口。

**选择**：不抽象。每个 capability 的接口天然不同——`tools` 需要 `registry + runtime`，`skills` 需要 `registry + runtime + prompt`，`hooks` 只需要 `runner + config`。抽象出统一基类只会增加约束——所有模块被迫实现不需要的方法（hooks 不需要 `contribute_tools()`），或者基类方法太多导致"胖接口"。

**设计原则**：不为"看起来整齐"抽象。只为"消除真正重复"抽象。

### 决策 4：Compressor 直接访问 Agent 的私有字段

**问题**：Compressor 读写 `agent._anthropic_messages`、`agent._openai_messages`——这打破了 Agent 的封装。要不要让 Agent 暴露压缩相关的公开接口？

**可选方案**：在 Agent 上添加 `_budget_message()`、`_snip_message()` 等压缩方法；或者让 Compressor 直接访问。

**选择**：Compressor 直接访问。Compressor 是 Agent 的"外科医生"——它的唯一职责就是修改消息历史。给 Agent 加压缩接口会让 Agent 的 API 膨胀，而且这些接口除了 Compressor 没人会用。Compressor 和 Agent 放在同一个包（`runtime/`）下，明确了它们的亲密关系——封装边界不在文件级别，在包级别。

## 6. 面试考点

### Q1: Agent 为什么不继承任何东西？

原架构中 Agent 通过 3 个 Mixin 拼装行为。Mixin 的隐式耦合导致：改了 Agent 字段名，Mixin 就崩；跨 4 个文件阅读才能理解完整行为。

改为纯状态容器后，每个组件职责单一。Agent 只持有数据，AgentLoop 只调度，Compressor 只压缩。依赖关系从隐式变成显式——`AgentLoop(agent, backend)` 的构造函数就是依赖声明。

**如果面试官追问"你怎么知道该拆不该拆"**：用"独立变更原因"原则回答。改压缩策略时，如果不拆就要同时理解循环逻辑——说明它们应该拆开。改工具执行时不需要动压缩——说明拆对了。

### Q2: 双消息历史为什么不统一？

Anthropic 的 `tool_use/tool_result` 嵌套在 content list 中，OpenAI 的是独立 message。强行统一需要中间抽象层来映射——增加复杂度不减少代码。

两份简单的原生操作比一层复杂的抽象好维护。Compressor 里的双份代码是刻意的代价——每份都是简单的字符串处理，不会引入新的复杂度。

**如果面试官追问"以后加 Google Gemini 怎么办"**：加第三个列表 `_gemini_messages`，在 Agent 的方法里加一个路由分支。不会尝试统一——因为 Gemini 的格式和 Anthropic、OpenAI 又不一样。统一抽象需要预测未来的所有格式差异——这是过度设计的典型陷阱。

### Q3: AgentLoop 为什么后端无关？

AgentLoop 只依赖 `Backend` 抽象接口（`backend/base.py`），不依赖 `AnthropicBackend` 或 `OpenAIBackend`。这是依赖倒置——上层依赖抽象，下层实现抽象。

旧代码有 `_run_anthropic` 和 `_run_openai` 两个方法，80% 相同。封装差异到策略类后，循环只有一套——新增厂商只需新建 `backend/gemini.py`，AgentLoop 零改动。

### Q4: Compressor 为什么直接访问 Agent 的私有字段？

Compressor 的唯一职责是修改消息历史——它是 Agent 的"外科医生"。给它公开接口会让 Agent 的 API 膨胀（需要暴露 `_budget_message()`、`_snip_message()` 等方法），而且只有 Compressor 会用这些接口。

封装边界不在文件级别——在包级别。`runtime/` 包内的组件互相知道内部细节是合理的设计。

**如果面试官追问"那为什么不把 Compressor 合并到 Agent？"**：因为压缩和状态管理的变更原因不同。改压缩策略（调整阈值、增加压缩层）是一个独立的需求——改了压缩不改 Agent 状态字段。如果合并，每次改压缩策略都要触碰 590 行的 Agent 文件。

### Q5: 子 Agent fork 和主 Agent 共享什么？

子 Agent 和主 Agent 是**同一个 Agent 类**的不同实例——共享代码但不共享状态。消息历史独立、token 计数独立、output_buffer 独立。共享的是：`sandbox_manager`（同一个 SandboxManager 实例）、`permission_mode`（权限模式继承）、Backend 类型（都是 Anthropic 或都是 OpenAI）。

不共享的是：消息历史（独立上下文窗口）、MCP 连接（子 Agent 不初始化 MCP）、记忆系统（子 Agent 不触发记忆召回）、启动上下文（子 Agent 不注入 CLAUDE.md/Git）。

### Q6: 如果对话超过 200K token 怎么办？

三层压缩会依次触发。Budget 先裁剪超长结果（利用率 > 50%），Snip 替换旧结果为占位符（> 60%），Compact 调模型生成摘要重置历史（> 85%）。

Compact 是一次模型调用（`max_tokens=2048`），不是本地操作——所以要等到 85% 利用率才触发。compact 失败时降级：保留未压缩历史继续对话，不中断会话。

## 7. 代码导读

**推荐阅读顺序**：

```
1. cli/main.py          → main() 入口，理解组装逻辑
2. runtime/agent.py      → Agent 状态容器，理解有哪些状态
3. runtime/loop.py       → AgentLoop.run()，理解主循环
4. backend/anthropic.py  → 模型怎么被调用的
5. capabilities/tools/   → 工具怎么被注册和执行
6. runtime/compressor.py → 上下文怎么压缩
7. context/builder.py    → System prompt + 附件怎么构建
```

**关键文件**：

| 文件 | 行数 | 为什么重要 |
|------|:--:|------|
| `runtime/agent.py` | 590 | 项目最大文件，Agent 全部状态 |
| `runtime/loop.py` | 326 | 主循环，后端无关的核心逻辑 |
| `runtime/compressor.py` | 264 | 三层压缩 + compact |
| `backend/anthropic.py` | 160 | Anthropic 流式解析 |
| `capabilities/tools/runtime.py` | 250 | 工具执行管线 |
| `context/sources.py` | 320 | CLAUDE.md 加载链 |
