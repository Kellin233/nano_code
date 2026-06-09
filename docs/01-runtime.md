# Runtime 内核

## 1. 为什么需要 Runtime

一个 AI Agent 最"直觉"的写法是把所有逻辑塞进一个类：消息历史、API 调用、工具执行、上下文压缩——`class Agent`，什么都能做。很多早期项目就是这样起步的。

问题是改不动。想换模型厂商？要改 Agent 类。想改压缩策略？要改 Agent 类。想加一个工具执行步骤？还是要改 Agent 类。Agent 变成 God-class——500 行、1000 行、2000 行，谁都不敢大改。

Runtime 的目标：**拆开 God-class**。把一次对话涉及的所有职责拆成独立组件，每个组件只做一件事，通过显式接口协作。拆完之后，Agent 从 600 行的 Mixin 组合变成 590 行的纯状态容器——但这次，590 行全是数据定义和简单的 getter/setter，没有隐藏的控制流。

## 2. 核心概念

### 2.1 三角关系

```
                    Agent（数据仓库）
                    ↑        ↑
                    │        │
                    │   Compressor（清洁工）
                    │        ↑
                    │        │
              AgentLoop（导演）──→ Backend（翻译官）
                    │
                    ↓
              ToolRuntime（工具执行）
```

三个角色各管一摊，通过构造函数注入协作：

**Agent**：被动数据仓库。持有消息历史、token 计数、能力模块引用。**不主动做任何事**——没有循环、没有 API 调用、没有压缩逻辑。

**AgentLoop**：导演。读取 Agent 的状态，调用 Backend，委托 ToolRuntime 执行工具，委托 Compressor 压缩上下文。产出 `AsyncIterator[RuntimeEvent]` 流。

**Compressor**：清洁工。对话太长时，直接读写 Agent 的内部消息列表。和 AgentLoop 之间不直接通信——Agent 是它们唯一的共享状态。

### 2.2 Agent 持有的能力模块

Agent 在 `__init__` 中实例化了 5 个能力模块，AgentLoop 通过 Agent 拿到这些引用：

```
Agent.__init__(config)
    │
    ├── ToolRegistry(builtin_tool_definitions())
    │       ↓ AgentLoop._execute_tools() → 打包成 ToolContext → ToolRuntime 使用
    │
    ├── SandboxManager(config.sandbox_config)
    │       ↓ ToolRuntime → run_shell 走这里
    │
    ├── McpManager(on_tools_changed=...)
    │       ↓ AgentLoop 首次调用时连接，MCP 工具走这里
    │
    ├── SkillInvocation() + ActiveSkillManager()
    │       ↓ TuiApp / skill 工具调用时使用
    │
    └── HookManager.capture()
            ↓ AgentLoop.run() 和 ToolRuntime 分别在对应事件触发
```

### 2.3 一条消息的完整旅程

```
用户输入 "修 agent.py 的 bug"
    │
    ├─[准备阶段]───────────────────────────────
    │   agent.inject_startup_context()         # 仅首次
    │   agent.prepare_initial_attachments()     # 仅首次
    │   agent.flush_pending_attachments()       # MCP 变更等
    │   agent.add_user_message(user_message)
    │   await agent.ensure_mcp_initialized()    # 仅首次
    │   await _check_and_compact()             # >85% 先压缩
    │   agent.start_memory_prefetch(msg)       # 异步，不阻塞
    │
    ├─[主循环]─────────────────────────────────
    │   while True:
    │       Compressor(agent).run_pipeline()    # 每轮开头压缩
    │       agent.consume_memory_prefetch()
    │
    │       # 调模型（并行接收 + 流式产出事件）
    │       call_task = create_task(backend.call(on_text_delta=...))
    │       while not call_task.done():
    │           yield text_events.get()          # 每 50ms，发给 TUI
    │       response = await call_task
    │
    │       agent.record_usage(in, out)
    │       _append_assistant_message(response)
    │
    │       if 无 tool_calls:
    │           _run_stop_hook() → 继续或 LoopFinished("stop")
    │
    │       agent.current_turns += 1
    │       agent.budget_exceeded() → 超了？停止
    │
    │       _execute_tools(calls)               # 打包 ToolContext
    │       _append_tool_results(results)
    │       agent.flush_pending_attachments()
    │       # 继续循环
    │
    ├─[结束时]─────────────────────────────────
    │   agent._auto_save()                      # 保存会话
    │   agent.shutdown()                        # 断开 MCP + 停止 sandbox
```

## 3. 总体设计

### 3.1 文件结构

```
runtime/
├── __init__.py         # 公共导出
├── agent.py            # Agent 状态容器（590 行）
├── loop.py             # AgentLoop 主循环（326 行）
├── compressor.py       # Compressor 压缩策略（264 行）
├── events.py           # RuntimeEvent + 工厂函数（88 行）
├── thread.py           # RuntimeThread 公开入口（218 行）
└── approvals.py        # ApprovalManager 确认管理（83 行）
```

### 3.2 模块关系

| 文件 | 依赖谁 | 被谁依赖 |
|------|--------|---------|
| `agent.py` | capabilities/tools/, capabilities/sandbox/, capabilities/mcp/, capabilities/hooks/, capabilities/skills/, models.py, context/ | loop.py, compressor.py, thread.py, cli/main.py |
| `loop.py` | agent.py, backend/base.py, capabilities/tools/runtime.py, compressor.py | thread.py, cli/main.py, agent.py（子 Agent fork） |
| `compressor.py` | agent.py, capabilities/tools/types.py（常量） | loop.py |
| `events.py` | capabilities/tools/types.py | loop.py, thread.py, tui/, cli/ |
| `thread.py` | agent.py, loop.py, backend/, session/, approvals.py | server/ |
| `approvals.py` | 无外部依赖 | thread.py |

### 3.3 与其他模块的关系

Runtime 是架构的中心。上层（cli/tui/server）创建 Runtime 的组件并启动。下层（backend/capabilities/context）被 Runtime 调用。

与 `backend/` 的关系：AgentLoop 通过 `Backend` 接口调用模型。AgentLoop 不 import `AnthropicBackend` 或 `OpenAIBackend`——依赖倒置。

与 `capabilities/` 的关系：Agent 持有能力模块的实例，AgentLoop 把它们打包成 `ToolContext` 传给 `ToolRuntime`。capabilities 模块不 import runtime 的任何文件（除了 `ToolCall` 类型定义）。

与 `context/` 的关系：Agent 在 `__init__` 中调用 `context/builder.py` 构建 system prompt 和 startup context。context 模块不 import runtime。

## 4. 详细设计

### 4.1 Agent（590 行）

**构造过程**：

`__init__` 做了三件事。**第一**，实例化 5 个能力模块——每个都是独立的对象，Agent 持有引用但不控制它们的生命周期。**第二**，初始化约 30 个状态字段——分为会话状态（session_id、消息历史、token 计数）、权限状态（_confirmed_paths、_read_file_state、_confirm_fn）、上下文状态（_pending_context_attachments、各种 injected/prepared 标志位）、诊断状态（_diagnostics）。**第三**，根据 Agent 类型构建系统提示词——主 Agent 走 `_build_prompt_bundle()`（同时获得 system_prompt 和 startup_context），子 Agent 走 `custom_system_prompt` 或 `_build_system_prompt()`（子 Agent 无启动上下文）。

**消息操作**：

`add_user_message(content)`：追加用户消息。在内部根据 `config.use_openai` 路由到 `_anthropic_messages` 或 `_openai_messages`。

`append_user_context(text)`：把系统上下文追加到**最新用户消息后面**，而不是创建新消息。这保证了消息角色交替合法——不会出现"连续两条 user 消息"的情况。Anthropic 的 API 对此有严格校验。如果最新的 user 消息的 content 是 list（包含 tool_result block），则将 text 作为新的 text block append 到 list 中。

`append_meta_user_message(text)`：与 `append_user_context` 不同——这是追加**独立的**系统上下文 user 消息，不混入真实用户输入。用于启动上下文注入和附件刷新。

**子 Agent fork**：

`run_once(prompt)` 是子 Agent 的入口。它创建自己的 AgentLoop（复用同一个 Backend 类型），通过 `_output_buffer` 收集输出而非直接渲染，最后返回 `{"text": str, "tokens": {"input": int, "output": int}}`。子 Agent 的特殊行为：不注入启动上下文（startup_context 为空字符串）、不初始化 MCP（`_mcp_initialized` 不生效）、不触发记忆系统（`is_sub_agent` 分支跳过）、文本进 buffer 而非渲染。

### 4.2 AgentLoop（326 行）

**文本流的实时输出**：

AgentLoop 不等待模型完整返回再展示。它用两个并发任务并行：

```python
text_events: asyncio.Queue[RuntimeEvent] = asyncio.Queue()

async def on_text_delta(text):
    await text_events.put(AssistantTextDelta(text))

call_task = asyncio.create_task(
    backend.call(on_text_delta=on_text_delta, ...)
)
agent._current_task = call_task  # 用于 Ctrl+C 中断

while not call_task.done():
    try:
        event = await asyncio.wait_for(text_events.get(), timeout=0.05)
        yield event
    except asyncio.TimeoutError:
        if agent.aborted:  # 用户按了 Ctrl+C？
            call_task.cancel()
            break
```

这个设计的关键点：`agent._current_task = call_task` 用于 Ctrl+C 中断。用户按 Ctrl+C 时，`agent.abort()` 设置 `_aborted = True`，主循环检测到 abort 后 cancel 模型调用 task。每 50ms 的 timeout 保证了 abort 检测的响应速度。

**`_execute_tools()`**：

这是 AgentLoop 和工具系统的桥接点。它把 Agent 身上的状态打包成 `ToolContext`：

```python
ctx = ToolContext(
    cwd=agent.config.workspace,
    session_id=agent.session_id,
    read_file_state=agent._read_file_state,    # 先读后改
    sandbox_manager=agent._sandbox_manager,     # run_shell
    mcp_manager=agent._mcp_manager,             # MCP 工具
    agent=agent,                                 # agent/skill 工具
)
runtime = ToolRuntime(
    agent._tool_registry,
    permission_mode=agent.permission_mode,
    confirm_fn=agent._confirm_dangerous,
    confirmed=agent._confirmed_paths,
    hooks=agent._hook_manager,
    event_callback=capture,  # 捕获 PermissionRequested 事件
)
return await runtime.execute_many(calls, ctx)
```

ToolRuntime 通过 ToolContext 拿到一切——不直接引用 Agent。这个间接层让工具执行可以独立测试（只需提供 ToolContext 而不需要完整 Agent 实例）。

**消息格式的追加**：

`_append_assistant_message()` 和 `_append_tool_results()` 都在内部根据 `use_openai` 路由。Anthropic 的 assistant 消息是 `{"role": "assistant", "content": [{"type": "text", ...}, {"type": "tool_use", ...}]}`，OpenAI 的是 `{"role": "assistant", "content": "...", "tool_calls": [...]}`。Anthropic 的 tool_result 嵌套在 user content list 中，OpenAI 的是独立 `role: tool` message。

### 4.3 Compressor（264 行）

**三层递进的设计哲学**：压缩的成本递增——Budget 是 O(n) 字符串操作（零 API 成本），Snip 需要先建索引（O(n) 内存），Compact 是一次模型调用（消耗 token）。“只在必要时付出更高成本"。

**Budget 的两种预算级别**：高利用率（> 70%）用 15000 字符预算，中等利用率用 30000 字符。保留头尾各一半，中间替换为 `[... budgeted: N chars truncated ...]`。

**Snip 的 Anthropic 优化**：先扫所有 assistant 消息建 `tool_use_id → {name, input}` 索引，再扫 user 消息中的 tool_result。对于 `read_file` 类型的 tool_result，同一 `file_path` 的多次读取只保留最后一次——前面的替换为 `[Content snipped - re-read if needed]`。更通用的规则：所有 snipable 工具类型的结果中，保留最近的 `KEEP_RECENT_RESULTS = 3` 个。

**Compact 的格式**：压缩后 Anthropic 的历史 = `[摘要 user msg, "Understood..." assistant msg, 最后一条真实 user msg]`。OpenAI 的历史 = `[system msg, 摘要 user msg, "Understood..." assistant msg, 最后一条真实 user msg]`（OpenAI 的 system message 需要保留在第一位）。压缩成功后重挂 active skill 上下文。

**Compact 失败降级**：compact 本身是一次模型调用——可能因 API 限流或网络问题失败。catch 异常后打印 info 消息，继续用当前未压缩的历史对话。不抛异常、不中断。

### 4.4 RuntimeEvent（88 行）

`RuntimeEvent` 是一个 frozen dataclass，包含 `type`（字符串事件类型）、`payload`（dict）、`thread_id`（会话标识）、`seq`（序列号）、`timestamp`。`to_dict()` 和 `from_dict()` 用于 JSONL 序列化。

工厂函数替代子类继承：

```python
def ToolCallStarted(call: ToolCall) -> RuntimeEvent:
    return RuntimeEvent(type="tool.started", payload={
        "id": call.id, "name": call.name, "input": call.input, "provider": call.provider
    })
```

这个选择的好处：类型判断用 `event.type == "tool.started"` 字符串比较——不需要 `isinstance(event, ToolCallStarted)` 的判断链。新增事件类型只需加一个工厂函数，不需要新增子类。

## 5. 设计决策

### 决策 1：Agent 从 Mixin 改为纯状态容器

**问题**：原 Agent 通过 `AgentContextMixin`、`AgentToolRuntimeMixin`、`AgentBackendMixin` 三个 Mixin 拼装行为。Mixin 通过 `self._anthropic_messages` 隐式访问状态——改了字段名 Mixin 就崩溃。阅读完整行为需要跨 4 个文件，每个 Mixin 都可能修改同一个字段。

**可选方案**：(a) 保持 Mixin 但加类型检查；(b) 改为纯组合（每个行为独立类，注入 Agent）；(c) 改为纯状态容器 + 外部行为。

**选择**：(c)。Agent 只持有数据，AgentLoop 负责调度，Compressor 负责压缩，Backend 负责 API 调用。

**代价**：AgentLoop 构造函数显式传 Agent + Backend，Compressor 每次 `Compressor(agent)` 创建。但每个文件的变更原因独立——改压缩策略只改 compressor.py，改循环只改 loop.py。

### 决策 2：AgentLoop 后端无关

**问题**：原代码 `agent/loop.py` 有 `_run_anthropic`（~100 行）和 `_run_openai`（~100 行），80% 相同——注入上下文、记忆召回、工具执行、压缩完全一样。只有 API 调用和消息格式不同。

**可选方案**：(a) 保持两套循环；(b) 抽象 Template Method 模式；(c) 策略模式——将 API 调用差异封装到 Backend 策略类中。

**选择**：(c)。循环只有一套，通过 `Backend` 接口调用。差异封装在 `AnthropicBackend` 和 `OpenAIBackend` 中。

**代价**：`_append_assistant_message` 和 `_append_tool_results` 仍需根据 `use_openai` 分支——因为消息格式差异无法完全消除。但这比两套完整循环好得多。

### 决策 3：Compressor 直接访问 Agent 私有字段

**问题**：Compressor 读写 `agent._anthropic_messages`、`agent._openai_messages`——打破封装。要不要给 Agent 加压缩接口？

**可选方案**：(a) Agent 暴露 `_budget_message()` 等压缩方法；(b) Compressor 通过公开接口操作（`agent.messages` property + `agent.xxx_message()` 方法）；(c) Compressor 直接访问私有字段。

**选择**：(c)。Compressor 的唯一职责就是修改消息历史——它是 Agent 的"外科医生"。给 Agent 加压缩接口会让 API 膨胀（这些接口只有 Compressor 会用），而 (b) 的性能更差（property 每次返回新 list 的副本）。

**代价**：Compressor 和 Agent 紧密耦合——改 Agent 的内部数据结构时，Compressor 也要改。但放在同一个包（`runtime/`）下明确了这种亲密关系——封装边界在包级别。

### 决策 4：RuntimeEvent 用工厂函数而非子类

**问题**：原来有 `AgentEvent` 基类和 7 个子类（`AssistantTextDelta`、`ToolCallStarted` 等）。新增事件类型要加子类，消费端需要 `isinstance` 判断链。

**可选方案**：(a) 保持子类继承；(b) 单一 `RuntimeEvent` 类 + 工厂函数。

**选择**：(b)。`RuntimeEvent` 只有一个——`type` 字符串区分事件种类。工厂函数创建事件，消费端用 `event.type == "tool.started"` 判断。

**代价**：失去类型安全——`event.payload` 是 dict，IDE 无法推断 payload 的结构。但换来了简单性——新增事件只需加一个工厂函数，不需要改消费端的类型判断逻辑。

## 6. 面试考点

### Q1: Agent 为什么不继承任何东西？

原有 3 个 Mixin 拼装行为，Mixin 通过 `self._xxx` 隐式访问状态。改字段名 Mixin 就崩，跨 4 文件阅读。改为纯状态容器后，依赖关系从隐式变成显式——`AgentLoop(agent, backend)` 的构造函数就是依赖声明。

**追问"你怎么判断该拆"**：用"独立变更原因"。改压缩策略时如果总得同时改循环逻辑，说明该拆。改完压缩不改循环——说明拆对了。

### Q2: 为什么 Compressor 和 Agent 放在同一个包？

Compressor 直接读写 Agent 的私有字段——如果放在不同包，这个访问就是"跨包破坏封装"。放在 `runtime/` 包内是刻意设计——封装边界在包级别，不在文件级别。Compressor 是 Agent 的外科医生——它们之间需要紧密的内部访问。

**追问"为什么不合并到 Agent？"**：压缩和状态管理的变更原因不同。改压缩阈值不涉及 Agent 的状态字段。分开放让每次改动的 diff 更小、更聚焦。

### Q3: 双消息历史为什么不统一？

Anthropic 的 tool_use/tool_result 嵌套在 content list 中，OpenAI 的是独立 message。强行统一需要双向转换——增加抽象层不减少代码。两份简单的原生操作比一层复杂抽象好维护。

**追问"加 Google Gemini 怎么办？"**：加 `_gemini_messages` 列表，Agent 方法里加路由。不尝试统一——因为 Gemini 格式和两者又不同。预测未来的统一抽象是过度设计。

### Q4: AgentLoop 的文本流为什么用 asyncio.Queue + 50ms timeout？

AgentLoop 需要在调模型的同时检测 abort（Ctrl+C）。如果 `await backend.call()` 阻塞等完整返回，abort 检测不到。`asyncio.Queue` + `wait_for(0.05)` 提供了一个"每 50ms 检查一次"的轮询窗口——text delta 到达时立即 yield，没有 delta 时给 abort 检查机会。

**追问"为什么是 50ms 不是更低？"**：50ms 对用户感知是瞬时的（20fps），同时不会让 CPU 空转。如果用 1ms，几乎等于忙等；如果用 1s，用户感知到延迟。

### Q5: Compact 失败怎么办？

Compact 是一次模型调用——可能因 API 限流或网络问题失败。catch 异常后打印 info 消息，继续用未压缩历史对话。降级是明确设计——不能让辅助功能（压缩）中断核心功能（对话）。

**追问"如果对话超过上下文窗口但不触发 compact？"**：这是需要关注的边界情况。当前 COMPACT_UTILIZATION_THRESHOLD = 0.85，在 200K 窗口下大约 170K token。如果模型突然在一个请求内返回海量内容，可能在 compact 触发前就超过窗口。后续可以加"最后一刻紧急压缩"机制。

### Q6: 子 Agent 和主 Agent 的差异是怎么控制的？

所有差异都通过 `is_sub_agent` 标志控制——不注入启动上下文、不初始化 MCP、不触发记忆系统、文本输出进 buffer。这是在"共享同样的 Agent 类"和"行为差异"之间的务实选择——不需要 SubAgent 子类，只需要一个 bool 检查。

**追问"为什么不新建 SubAgent 类？"**：代码复用——子 Agent 和主 Agent 共享同样的消息操作、工具注册、token 统计逻辑。`is_sub_agent` 比 `isinstance` 更简单——避免引入继承层次。

## 7. 代码导读

**推荐阅读顺序**：

```
1. agent.py → Agent.__init__()，理解有哪些状态
2. loop.py → AgentLoop.run()，理解主循环流程
3. loop.py → _execute_tools()，理解工具执行桥接
4. loop.py → _append_* 方法，理解消息格式差异
5. compressor.py → run_pipeline() + 各层实现
6. events.py → 事件工厂函数
```

**关键代码片段**：

- `agent.py:67-153` —— Agent 初始化的完整状态树
- `agent.py:235-252` —— `append_user_context` 的双格式路由
- `agent.py:396-427` —— `run_once` 子 Agent fork 实现
- `loop.py:46-182` —— `run()` 完整流程
- `loop.py:88-133` —— 文本流实时输出 + abort 检测
- `loop.py:237-265` —— `_execute_tools` 打包 ToolContext
- `compressor.py:50-53` —— `run_pipeline` 入口
- `compressor.py:71-98` —— Budget 层 Anthropic 实现
- `compressor.py:123-167` —— Snip 层 Anthropic 实现（含文件去重）
