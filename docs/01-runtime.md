# Agent Core 与 Harness

## 1. 为什么拆成 core 和 harness

旧架构把 Agent 状态、工具、MCP、skills、memory、hooks、压缩、backend 调用混在同一条运行时路径里。这样新增能力时容易污染 Agent 内核，循环逻辑也会直接知道工具系统细节。

当前架构把运行时拆成两层：

- `agent/` 是 core，只描述 Agent 状态机和对话协议。
- `agent/harness/` 是运行框架，处理压缩、上下文、会话、权限、hooks、approvals 这些横切机制。

具体能力不放在这两层，而是由 `cli/session.py` 在应用层装配。

## 2. Agent core

```
agent/
├── __init__.py
├── agent.py       # Agent 状态容器、消息操作、预算、回调槽位
├── loop.py        # AgentLoop，LLM/tool 状态机
├── events.py      # RuntimeEvent 工厂函数
├── types.py       # ToolDef / ToolCall / ToolResult / RuntimeEvent
├── models.py      # 模型窗口、schema 转换、retry helper
└── budget.py      # 费用估算
```

core 的硬性约束：

- 不 import `cli/`、`tui/`、`providers/`、`cli/core/`。
- 不 import OpenAI/Anthropic SDK。
- 不加载工具、不扫描插件、不读配置、不启动 sandbox。
- 只暴露回调槽位和 provider-neutral 类型。

### Agent

`Agent` 是状态容器。它保存：

- session id、启动时间、abort 状态和当前 task。
- Anthropic/OpenAI 两套原生消息历史。
- token 计数、预算状态、费用估算。
- pending context attachments、startup context 注入标记。
- 先读后改状态、确认缓存、大工具结果落盘路径。
- 生命周期和工具调用回调槽位。

`Agent.bind_runtime()` 接收应用层对象，但只把它们当 opaque object 保存。Agent 不 import 这些对象的具体类型。`Agent.set_callbacks()` 接收扩展和生命周期回调，供 Session 桥接。

### AgentLoop

`AgentLoop` 只负责状态机：

```
用户消息
  → 应用 UserPromptSubmit hook
  → 注入 startup context / attachments
  → backend.call(...)
  → append assistant message
  → 如果有 tool_calls，调用注入的 execute_tools(calls)
  → append tool results
  → 继续循环或结束
```

`AgentLoop` 不 import `ToolRuntime`。工具执行通过构造函数注入：

```python
AgentLoop(
    agent,
    backend,
    execute_tools=session._execute_tools,
    run_compression_pipeline=compressor.run_pipeline,
    check_and_compact=session._check_and_compact,
    apply_user_prompt_hooks=session._apply_user_prompt_hooks,
    run_stop_hook=session._run_stop_hook,
)
```

这保证了 loop 只知道“有一个工具执行回调”，不知道工具系统、权限、hook、extension 的实现。

### RuntimeEvent

`RuntimeEvent` 定义在 `agent/types.py`，`agent/events.py` 提供工厂函数：

```python
AssistantTextDelta(text)
ToolCallStarted(call)
ToolCallFinished(call, result)
PermissionRequested(call, message)
BudgetExceeded(reason)
LoopFinished(stop_reason)
```

事件由 CLI/TUI/Server 消费。core 不知道这些消费端如何渲染。

## 3. Harness

```
agent/harness/
├── __init__.py
├── approvals.py              # ApprovalManager
├── compressor.py             # Collapse / Snip / Microcompact / Compact
├── message_view.py           # 双消息格式的读写视图
├── context/
│   ├── builder.py            # system prompt、startup context、动态附件
│   └── sources.py            # CLAUDE.md、Git 快照、frontmatter
├── hooks/
│   ├── config.py             # HookManager 配置和调度
│   ├── runner.py             # 外部进程 hook 执行
│   └── types.py
├── permissions/
│   ├── policy.py
│   ├── rules.py
│   ├── shell.py
│   └── workspace.py
└── session/
    ├── __init__.py           # save/load/list session
    ├── event_store.py        # append-only RuntimeEvent JSONL
    └── artifacts.py          # 大结果 artifact
```

Harness 可以做 I/O，因为它负责“怎么运转”。但它不能依赖 `cli/`、`tui/`、`providers/`。需要模型摘要时，`Compressor` 接收 `summarize_messages` callable，由 `AgentSession` 从 backend 注入。

## 4. AgentSession 的职责

`cli/session.py` 是唯一装配点。它负责：

- 创建 `Agent`。
- 创建 `Backend`。
- 创建 `ToolRegistry`、`SandboxManager`、`McpManager`、`SkillInvocation`、`ActiveSkillManager`。
- 创建 `MemoryRuntime` 和 `Compressor`。
- 捕获 hooks，加载 extensions。
- 把 `ExtensionRunner` 填入 Agent 回调槽位。
- 把 `_execute_tools()` 注入 `AgentLoop`。
- 给 CLI/TUI/Server 暴露统一的 `run()`、`chat()`、`run_once()`、`compact()`、`shutdown()`。

这对标 Pi 的 Session 桥接思路：内核不认识插件，Session 负责把插件 runner 接到回调槽位。

## 5. 设计决策

### 为什么 Agent 不再持有具体能力

工具、MCP、skills、memory、extensions 都是“Agent 能做什么”。这些属于应用层能力，不属于状态机协议。放进 Agent 会让 core 随能力增长而膨胀，也会破坏单向依赖。

### 为什么 ToolRuntime 不在 AgentLoop 里创建

ToolRuntime 需要权限、hooks、sandbox、MCP、extension before/after hook、event callback。这些都是应用层装配细节。Loop 创建 ToolRuntime 会直接依赖 `cli/core/tools`，违反 core 纯净原则。

### 为什么 Compressor 在 harness 而不是 core

压缩需要读写消息历史、读文件恢复最近文件、运行 PreCompact hook、调用模型摘要。它是运行框架机制，不是状态机本身。放在 harness 后，core 仍然只描述对话协议。

## 6. 代码导读

阅读顺序：

```
agent/agent.py
agent/loop.py
agent/events.py
agent/types.py
cli/session.py
agent/harness/compressor.py
agent/harness/context/builder.py
```

架构检查：

```bash
rg -n "from .*cli|from .*tui|from .*providers|import anthropic|import openai|open\(" \
  src/agent/agent.py src/agent/loop.py src/agent/events.py src/agent/types.py src/agent/models.py src/agent/budget.py

rg -n "from .*cli|from .*tui|from .*providers" src/agent/harness -g '*.py'
```
