# 引言

## 为什么需要 NanoCode

LLM 能写代码，但它不能读你的项目、不能跑命令、不能改文件。你需要一个"中介"——把 LLM 的文本输出翻译成工具调用，把工具结果喂回给 LLM，循环直到任务完成。

这就是 NanoCode 做的事。它是一个跑在终端里的 Agent：你输入一句话，它调用 LLM，模型可以读文件、搜索代码、编辑文件、跑 shell 命令，循环直到任务完成。

和同类工具（Claude Code、Codex CLI、Aider）相比，NanoCode 的设计偏好是：**轻量、可审计、可学习**。没有庞大的插件体系，没有复杂的分布式架构。一个 Python 项目，55 个源文件，275 个测试，从头读到尾只需要一个下午。

## 核心概念

### 三个角色

整个系统可以理解成三个角色在协作：

**Agent（状态仓库）**。一次对话的完整"数据库"——消息历史、token 计数、工具注册表、沙箱管理器。Agent 是被动的：它不主动做任何事，只提供读写的接口。

**AgentLoop（导演）**。驱动"用户输入 → 模型调用 → 工具执行"的循环。它读取 Agent 的状态、调用 Backend、委托 ToolRuntime 执行工具、委托 Compressor 压缩上下文。它产出 RuntimeEvent 流，供 TUI/CLI/Server 消费。

**Backend（翻译官）**。封装模型 API 的细节。Anthropic 和 OpenAI 的流式格式不同、消息格式不同、thinking 机制不同——Backend 把这些差异封装在策略类里，AgentLoop 只看到统一接口。

### 一条请求贯穿全系统

```
用户: "修 agent.py 的 bug"

cli/main.py: Agent(config) + create_backend(config) + AgentLoop(agent, backend)

AgentLoop.run(prompt):
  → 注入上下文（CLAUDE.md、Git 状态、日期/平台）
  → Backend.call() 调用模型
  → 模型返回 tool_calls: [read_file("agent.py"), grep_search("def.*bug")]
  → ToolRuntime 执行工具（验证→权限→确认→执行）
  → 结果追加到消息历史 → 压缩检查 → 再调模型
  → 循环直到 LoopFinished("stop")

RuntimeEvent 流 → TUI 渲染 / 一次性输出
```

### 十个模块

```
cli / tui / server          ← 表现层
        │
   runtime/  ★内核★         ← Agent 状态 + 主循环 + 压缩
   ╱         ╲
backend/   capabilities/    ← 模型调用 / 7 个可插拔能力
               │
  context/  models.py       ← 提示词 / 元数据
  session/  protocol/       ← 持久化 / 协议
```

依赖方向单向：表现层 → runtime → backend/capabilities/context。下层绝不 import 上层。

| 模块 | 一句话 |
|------|--------|
| `runtime/` | 内核：Agent 状态容器 + AgentLoop 主循环 + Compressor 压缩 |
| `backend/` | 模型后端策略类：AnthropicBackend / OpenAIBackend |
| `capabilities/tools/` | 工具系统：12 个内置工具 + ToolRegistry + ToolRuntime |
| `capabilities/permissions/` | 权限检查：四种模式 + 四层检查 |
| `capabilities/sandbox/` | Shell 沙箱：bwrap / microsandbox / local |
| `capabilities/subagents/` | 子 Agent：Fork-and-Return + 并行编排 |
| `capabilities/skills/` | 技能系统：可复用提示词模板 |
| `capabilities/hooks/` | Hook 生命周期：PreToolUse/PostToolUse/Stop |
| `capabilities/memory/` | 长期记忆：文件式存储 + LLM 精选 |
| `capabilities/mcp/` | MCP 协议：外部工具接入 |
| `context/` | 系统提示词 + CLAUDE.md 加载 + 动态附件 |
| `cli/` `tui/` `server/` | 三种入口：一次性/TUI 交互/JSONL Server |

## 设计决策

### 决策 1：Agent 不继承任何 Mixin

**问题**：最初的 Agent 通过 3 个 Mixin 拼装行为——ContextMixin、ToolRuntimeMixin、BackendMixin。Mixin 通过 `self._anthropic_messages` 隐式访问状态，改了字段名 Mixin 就崩了。阅读完整行为需要跨 4 个文件。

**方案**：Agent 改为纯状态容器。所有行为外移到 AgentLoop、Backend、Compressor。Agent 只暴露 getter/setter。

**代价**：AgentLoop 需要显式传入所有依赖（agent、backend），构造函数参数变多。但这个代价是一次性的——之后加新行为不需要改 Agent。

### 决策 2：双后端消息历史不统一

**问题**：Anthropic 的 `tool_use/tool_result` 嵌套在 `content[]` 列表中，OpenAI 的 `function call` 和 `role: tool` 是独立 message。统一抽象怎么做？

**方案**：不统一。`_anthropic_messages` 和 `_openai_messages` 分开存储。Agent 的 4 个公开方法在内部根据 `use_openai` 路由。

**为什么不做中间层**：两种格式的语义差异太大。做中间层需要引入"通用消息模型"→"厂商格式"的双向转换——增加一层抽象但不减少任何代码量。

### 决策 3：capabilities 不抽象统一基类

每个 capability 的接口天然不同——tools 需要 registry+runtime，hooks 只需要 runner+config。抽象出统一的 `Capability` 基类只会增加约束而不增加价值。

### 决策 4：Backend 是策略类而非 Mixin

Anthropic 和 OpenAI 的流式解析差异被封装在两个独立类中。AgentLoop 只依赖 `Backend` 接口。新增模型厂商只需加一个文件——不改 AgentLoop，不改 Agent。

## 代码走读

**入口**：`cli/main.py` → `main()` 创建 Agent、Backend、AgentLoop，选 TUI/一次性/Server 启动。

**内核**：`runtime/agent.py`（~580 行）是项目最大的文件——Agent 的全部状态 + 消息操作 + 子 Agent fork。`runtime/loop.py`（~326 行）是主循环——不区分后端，通过 Backend 接口驱动。`runtime/compressor.py`（~264 行）是三层压缩。

**能力模块**：`capabilities/tools/` 是最大最复杂的能力模块——4 文件，12 个内置工具。其他 capability 结构类似：types.py（数据模型）+ 引擎文件（按变更原因拆分）。

**上下文**：`context/builder.py` 构建 system prompt，`context/sources.py` 加载 CLAUDE.md 和 Git 快照。两者之间通过共享数据类型（PromptDiagnostic、PromptBundle）协作——类型定义在 sources.py 以避免循环导入。

## 面试考点

**Q: 这个项目最值得讲的设计决策是什么？**

Agent 从 Mixin 到纯状态容器的重构。这展示了识别隐式耦合、用显式组合替代继承、按变更原因拆分的工程能力。面试官如果追问"你怎么知道该拆不该拆"，可以用"独立变更原因"原则回答。

**Q: 如果要加第三个模型厂商，改哪里？**

两个文件：`models.py` 加模型元数据，新建 `backend/gemini.py` 实现 `Backend` 接口。AgentLoop、Agent、ToolRuntime 完全不用改。这证明了 Backend 策略模式的设计价值。

**Q: 项目最大的 trade-off 是什么？**

双消息历史不统一。好处是代码简单、没有中间抽象层、每种格式的操作都是原生的。代价是 Compressor 里每种压缩操作都要写两份（Anthropic 版本和 OpenAI 版本）。这是刻意选择——我们认为两份简单代码比一份复杂的抽象层更好维护。
