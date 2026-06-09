# Runtime 内核

## 为什么需要 Runtime

一个 Agent 的代码可以写成一个大类——消息历史、API 调用、工具执行、上下文压缩全塞在一起。很多早期 AI Agent 项目就是这样做的。

问题是改不动。想换模型厂商？要改 Agent 类。想改压缩策略？要改 Agent 类。想加一种新工具？还是要改 Agent 类。Agent 变成了 God-class——什么都能做，但改什么都危险。

Runtime 的设计目标就是**拆开 God-class**。把一次对话涉及的所有职责拆成三个独立组件：Agent（状态容器）、AgentLoop（调度器）、Compressor（压缩器）。每个组件只做一件事，通过显式接口协作。

## 核心概念

### 三角关系

```
Agent（数据仓库）←── AgentLoop（导演）──→ Backend（翻译官）
    ↑                    │
    └── Compressor（清洁工）←┘
```

**Agent**：被动数据仓库。存消息历史、token 计数、能力模块引用。不主动做任何事。

**AgentLoop**：导演。读取 Agent 的状态，调用 Backend，委托 ToolRuntime 执行工具，委托 Compressor 压缩上下文。产出 RuntimeEvent 流。

**Compressor**：清洁工。对话太长时，压缩消息历史。直接读写 Agent 的内部消息列表。

Agent 是它们唯一的共享状态——AgentLoop 和 Compressor 之间不直接通信。

### Agent 持有的能力模块

Agent 在 `__init__` 中实例化了 5 个能力模块：`ToolRegistry`（工具注册表）、`SandboxManager`（沙箱）、`McpManager`（MCP 连接）、`SkillInvocation`（Skill 调用）、`HookManager`（Hook 配置）。AgentLoop 通过 Agent 拿到这些引用，打包成 `ToolContext` 传给 `ToolRuntime`。

这体现了**组合优于继承**：Agent 不是"继承所有能力"，而是"持有能力模块的引用"。换一个 sandbox 实现？换 `SandboxManager` 的构造函数参数就行，Agent 类不用动。

### 一条消息的旅程

用户输入 "修 bug" → AgentLoop.run() 注入上下文 → Backend.call() 调模型 → 模型返回 tool_calls → ToolRuntime 执行（验证→权限→确认→执行）→ 追加结果到 Agent 的消息历史 → Compressor 压缩检查 → 再调模型 → 直到模型不再调用工具 → LoopFinished("stop")。

## 设计决策

### 决策 1：Agent 是纯状态容器，行为全部外移

**为什么**：原来的 Agent 通过 3 个 Mixin 拼装行为。Mixin 通过 `self._anthropic_messages` 隐式访问状态——改了字段名 Mixin 就崩了。阅读完整行为需要跨 core.py、context.py、tools_runtime.py、backends.py 四个文件。

**代价**：AgentLoop 需要显式传入 agent 和 backend。但换来的好处是每个文件的变更原因独立——改压缩策略只改 compressor.py，改循环逻辑只改 loop.py。

### 决策 2：AgentLoop 后端无关

AgentLoop 只依赖 `Backend` 抽象接口（`backend/base.py`），不依赖具体的 `AnthropicBackend` 或 `OpenAIBackend`。这是依赖倒置——上层依赖抽象，下层实现抽象。

**为什么之前有两套循环**：旧的 `agent/loop.py` 有 `_run_anthropic`（~100 行）和 `_run_openai`（~100 行），80% 代码相同。现在后端差异封装在 Backend 策略类中，循环只有一套。

### 决策 3：Compressor 直接访问 Agent 的私有字段

Compressor 不通过公开接口操作消息历史——它直接读写 `agent._anthropic_messages` 和 `agent._openai_messages`。这打破了封装。

**为什么可以接受**：Compressor 的职责就是修改消息历史——它是 Agent 的"外科医生"。给它公开接口反而会让 Agent 的 API 面膨胀（需要暴露 `_budget_message()`、`_snip_message()`、`_compact_message()` 等方法）。Compressor 和 Agent 放在同一个包（`runtime/`）下，明确了它们的亲密关系。

## 代码走读

**`agent.py`（~580 行）**：项目最大的文件。`__init__` 实例化所有能力模块并初始化状态字段。消息操作方法（`add_user_message`、`add_assistant_message`、`add_tool_results`、`append_user_context`）在内部根据 `use_openai` 路由到 Anthropic 或 OpenAI 的消息列表。`run_once()` 是子 Agent 的入口——创建自己的 AgentLoop 跑完返回结果。

**`loop.py`（~326 行）**：`run(user_message)` 方法的 9 个步骤注释清晰。文本流实时输出用 `asyncio.Queue` + `create_task` 实现——边收文本边 yield 事件，每 50ms 检查一次。

**`compressor.py`（~264 行）**：`run_pipeline()` 按顺序执行三层压缩。`compact_conversation()` 调模型生成摘要——compact 失败时降级而不中断会话。

**`events.py`（~88 行）**：`RuntimeEvent` 数据类 + 工厂函数。事件流被 TUI/CLI/Server 三种消费端各自消费。

## 面试考点

**Q: 为什么有 `_anthropic_messages` 和 `_openai_messages` 两套？不统一吗？**

两种格式差异太大——Anthropic 的 tool_use/tool_result 嵌套在 content list 中，OpenAI 的是独立 message。强行统一需要中间抽象层——增加复杂度不减少代码。这是刻意的"不抽象"。

**Q: Compressor 为什么直接访问 Agent 的私有字段？**

Compressor 是 Agent 的"外科医生"——它的职责就是修改消息历史。通过公开接口反而会让 Agent 的 API 膨胀——需要暴露各种裁剪方法。放在同一个包（`runtime/`）下明确了这个亲密关系。
