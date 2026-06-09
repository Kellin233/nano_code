# Runtime 内核

## 概述

`runtime/` 是 nanocode 的核心——一次对话的全部状态和行为都在这里。三个类通过构造函数注入协作：Agent 是被动的数据仓库，AgentLoop 是导演（调度一切），Compressor 是清洁工（压缩上下文）。

## 架构

```
AgentLoop(agent, backend)
     │
     ├── 读：agent.messages / agent.system_prompt / agent.tool_definitions()
     ├── 调：backend.call()
     ├── 执：ToolRuntime（委托工具执行）
     ├── 压：Compressor(agent).run_pipeline()
     └── 写：agent.add_user_message() / agent.append_user_context()

Compressor(agent)
     │
     ├── 直接读写 agent._anthropic_messages / agent._openai_messages
     └── compact_conversation() 自己调 API 生成摘要
```

Agent 是它们唯一的共享状态。AgentLoop 和 Compressor 之间不直接通信。

---

## Agent（`agent.py`，590 行）

### 定位

纯状态容器。没有循环，没有 API 调用，没有压缩逻辑。所有行为外移到了 AgentLoop、Backend、Compressor。

### 初始化持有什么

```
Agent.__init__(config)
│
├── 工具层
│   └── ToolRegistry(builtin_tool_definitions())   # 内置 12 个工具
│
├── 能力模块（实例化并持有引用）
│   ├── SandboxManager(config.sandbox_config)       # shell 沙箱
│   ├── McpManager(on_tools_changed=...)            # MCP 连接管理
│   ├── HookManager.capture()                       # Hook 配置
│   ├── SkillInvocation()                           # Skill 调用器
│   └── ActiveSkillManager()                        # Active skill 跟踪
│
├── 会话状态
│   ├── _anthropic_messages = []    # Anthropic 消息历史
│   ├── _openai_messages = []       # OpenAI 消息历史
│   ├── total_input_tokens = 0      # 累计输入 token
│   ├── total_output_tokens = 0     # 累计输出 token
│   ├── current_turns = 0           # 当前轮次
│   └── last_input_token_count = 0  # 上次 API 调用的输入量
│
├── 权限 & 文件状态
│   ├── _confirmed_paths = set()    # 本会话已确认的操作
│   ├── _read_file_state = {}       # 文件路径 → mtime（先读后改）
│   └── _confirm_fn = None          # 确认回调（TUI/CLI 注册）
│
├── 上下文状态
│   ├── _pending_context_attachments = []  # 待注入附件队列
│   ├── _startup_context_injected = False  # 启动上下文是否已发
│   └── _initial_context_attachments_prepared = False
│
└── 系统提示词
    ├── 主 Agent: build_prompt_bundle() → system_prompt + startup_context
    ├── 子 Agent: custom_system_prompt（fork 时传入）
    └── 子 Agent: build_system_prompt()（无启动上下文）
```

### 双后端消息历史

`_anthropic_messages` 和 `_openai_messages` 是两个独立的列表。**不统一**——因为结构差异太大：

```python
# Anthropic: tool_use/tool_result 嵌套在 content list 中
{"role": "assistant", "content": [
    {"type": "text", "text": "..."},
    {"type": "tool_use", "id": "t1", "name": "read_file", "input": {...}}
]}
{"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "t1", "content": "..."}
]}

# OpenAI: tool 是独立 message
{"role": "assistant", "content": "...", "tool_calls": [...]}
{"role": "tool", "tool_call_id": "t1", "content": "..."}
```

强行统一需要中间抽象层——增加复杂度但不增加价值。Agent 的 4 个公开方法（`add_user_message`、`add_assistant_message`、`add_tool_results`、`append_user_context`）在内部根据 `use_openai` 路由到正确的列表。

### 对 AgentLoop 暴露的接口

**读**：`agent.messages`（property，返回当前后端消息列表）、`agent.system_prompt`、`agent.tool_definitions()`、`agent.effective_window`（context window - 20000 margin）、`agent.budget_exceeded()` 返回 `{"exceeded": bool, "reason": str}`。

**写**：`agent.add_user_message(text)` 追加用户消息。`agent.record_usage(in, out)` 更新 token 计数。`agent.append_user_context(text)` 把系统上下文追加到最新用户消息后——保证消息角色交替合法。

**初始化**：`agent.ensure_mcp_initialized()`（仅首次，连接 MCP）、`agent.inject_startup_context()`（注入日期/CLAUDE.md/Git）、`agent.prepare_initial_attachments()`（注入 skill/deferred tool 列表）、`agent.shutdown()`（断开 MCP + 停止 sandbox）。

### 子 Agent fork

`run_once(prompt)` 创建自己的 AgentLoop，跑完返回 `{"text": str, "tokens": {...}}`。子 Agent 不注入启动上下文、不初始化 MCP、不触发记忆系统——`is_sub_agent` 标志控制所有这些差异。

---

## AgentLoop（`loop.py`，326 行）

### 定位

后端无关的主对话循环。通过 `Backend` 接口调用模型，不区分 Anthropic/OpenAI。产出 `AsyncIterator[RuntimeEvent]` 流。

### `run(user_message)` 完整流程

```
1. agent.inject_startup_context()         # 仅首次：日期/CLAUDE.md/Git
2. agent.prepare_initial_attachments()    # 仅首次：skill/deferred tool 列表
3. agent.flush_pending_attachments()      # 刷新 MCP 变更等挂起附件
4. agent.add_user_message(user_message)   # 追加用户消息
5. await agent.ensure_mcp_initialized()   # 仅首次：连接 MCP
6. await _check_and_compact()             # 利用率 > 85% 预先压缩
7. await _apply_user_prompt_hooks(msg)    # UserPromptSubmit hook
8. memory_prefetch = agent.start_memory_prefetch(msg)

9. while True:                            # 主循环
     if agent.aborted: yield LoopFinished("aborted"); return
     _run_compression_pipeline()          # Budget → Snip → Microcompact
     agent.consume_memory_prefetch(...)    # 消费记忆预取
     response = await backend.call(...)    # 调模型
     _append_assistant_message(response)
     if 无 tool_calls:
         检查 Stop hook → 结束或继续
     if budget_exceeded: yield BudgetExceeded(...); return
     执行工具 → 追加结果 → 继续循环
```

### 文本流实时输出

两个并发任务实现边收边渲染：

```python
call_task = create_task(backend.call(on_text_delta=...))  # 调模型
while not call_task.done():
    event = await text_events.get()   # 每 50ms 取一次
    yield event                        # 发给 TUI/CLI
```

`on_text_delta` 把每个 text chunk 放入 `asyncio.Queue`，主循环每 50ms 取一次。用户看到逐字流式输出。

### 工具执行：打包 ToolContext

AgentLoop 不直接执行工具——把 Agent 的能力模块打包成 ToolContext：

```python
ctx = ToolContext(
    read_file_state=agent._read_file_state,   # 先读后改
    sandbox_manager=agent._sandbox_manager,    # run_shell
    mcp_manager=agent._mcp_manager,            # MCP 工具
    agent=agent,                                # agent/skill 工具
)
runtime = ToolRuntime(agent._tool_registry, permission_mode=..., confirm_fn=...)
await runtime.execute_many(calls, ctx)
```

ToolRuntime 通过 ToolContext 拿到需要的一切，不直接引用 Agent——工具执行层和 Agent 状态层解耦。

---

## Compressor（`compressor.py`，264 行）

### 定位

对话太长时压缩消息历史。不压缩语义，只压缩大小。

### 三层 + Compact

```
利用率 < 50%：什么都不做
利用率 > 50%：Budget    — 裁剪超长工具结果到 15K-30K 字符（头尾保留、中间截断）
利用率 > 60%：Snip      — 旧 read_file 结果替换为 [Content snipped]
利用率 > 85%：Compact   — 调模型生成摘要，重置消息历史
空闲 > 5分钟：Microcompact — 清除旧结果为 [Old result cleared]
```

**Snip 的 Anthropic 优化**：先扫 assistant 消息建 `tool_use_id → block` 索引，再扫 user 消息中的 tool_result。同一文件的多次 `read_file` 只保留最后一次——前面的替换为占位符。

**Compact**：调用模型生成摘要（`max_tokens=2048`），重置历史为 `摘要 + 最后一条用户消息 + "Understood..."`，然后重挂 active skill 上下文。compact 失败时降级——保留未压缩的历史继续对话。

---

## events.py（88 行）

统一事件模型。工厂函数替代子类——类型判断用 `event.type` 字符串：

```python
ToolCallStarted(call)     # → RuntimeEvent(type="tool.started")
LoopFinished("stop")      # → RuntimeEvent(type="turn.finished")
BudgetExceeded(reason)    # → RuntimeEvent(type="budget.exceeded")
```

三种消费端各自消费事件流：一次性模式（`_render_event`）、TUI（`TuiApp._chat`）、Server（JSONL 转发）。

## 面试考点

**Q: 为什么 Agent 从 Mixin 改为纯状态容器？**

原 Agent 通过 3 个 Mixin 拼装行为。Mixin 通过 `self._anthropic_messages` 隐式访问状态——改了字段名 Mixin 就崩了，跨 4 个文件阅读才能理解完整行为。现在每个组件职责单一、依赖显式。
