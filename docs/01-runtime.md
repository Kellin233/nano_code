# Agent Core、Runtime Management 与 Application

## 1. 为什么拆成三层

旧架构把 Agent 状态、工具、MCP、skills、memory、hooks、压缩、backend 调用混在同一条运行时路径里。这样新增能力时容易污染 Agent 内核，循环逻辑也会直接知道工具系统细节。

当前架构拆成三层：

- **Agent Core**：`agent/`，只描述 Agent 状态机和对话协议。
- **Runtime Management**：`agent/runtime_management/`，处理压缩、上下文、会话、权限、hooks、approvals 这些运行过程管理能力。
- **Application Layer**：`cli/session.py`、`cli/core/`、`providers/`、`tui/`，负责 provider、tools、sandbox、MCP、memory、skills、subagents、extensions 和 CLI/TUI/Server 的接入与装配。

## 2. Agent core

Agent core 文件结构：

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

`Agent` 是纯状态容器。它保存：

- `AgentConfig`：`model`、`message_format`、`thinking`、`max_cost_usd`、`max_turns`、`context_window`。
- session id、启动时间、abort 状态和当前 task。
- provider-neutral canonical `ConversationHistory`。
- token 计数、预算状态、费用估算。
- pending context attachments、startup context 注入标记。
- 生命周期和工具调用回调槽位。

`Agent` 不保存 workspace、权限模式、确认缓存、ToolRegistry、SandboxManager、MCP、MemoryRuntime 或 artifact 路径。`Agent.bind_runtime()` 只接收窄 callable：工具定义、运行时 ready、shutdown、初始附件准备。`Agent.set_callbacks()` 接收扩展和生命周期回调，供 Session 桥接。

关键状态可以分成四类：

| 状态 | 例子 | 为什么放在 Agent |
|------|------|------------------|
| 对话协议状态 | `ConversationHistory`、startup context 注入标记、pending attachments | provider call 需要这些内容，但不需要知道内容来源 |
| 运行控制状态 | abort flag、current task、turn count、budget limit | AgentLoop 需要统一判断停止、取消和预算 |
| 计量状态 | input/output/cache token、last input token count、费用估算 | provider 返回 usage 后要累计，context 压缩也需要 token 压力 |
| 回调槽位 | lifecycle callbacks、tool definitions、ensure ready、shutdown | 上层能力通过窄接口接入，core 不反向 import |

不要把“工具注册表”“sandbox manager”“memory runtime”这类应用能力放进 Agent。Agent 只保存循环必须知道的状态，能力对象由 `AgentSession` 持有。

### AgentLoop

`AgentLoop` 只负责状态机：

```
用户消息
  → 应用 UserPromptSubmit hook
  → 注入 startup context / attachments
  → prepare_context_for_provider()
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
    prepare_context_for_provider=session._prepare_context_for_provider,
    apply_user_prompt_hooks=session._apply_user_prompt_hooks,
    run_stop_hook=session._run_stop_hook,
)
```

这保证了 loop 只知道“有一个工具执行回调”和“有一个上下文准备回调”，不知道工具系统、权限、hook、extension、memory、MCP 或 compact 恢复的实现。

状态机有几个关键出口：

- provider 抛异常：yield `runtime.error`，再 yield `turn.finished(error)`。
- Agent 被 abort：取消当前 provider task，yield `turn.finished(aborted)`。
- 模型停止且 Stop hook 不阻止：yield `turn.finished(stop)`。
- 模型停止但 Stop hook 或内置质量检查要求继续：提交追加的 user context，进入下一轮 provider call。
- 模型产生 tool calls：先检查预算，再 yield tool started，执行工具，追加 tool results，继续下一轮。
- 预算超限：yield `budget.exceeded`，再 yield `turn.finished(budget_exceeded)`。

因此 Loop 的职责不是“完成用户任务”，而是保证对话协议持续合法：assistant tool_use 后一定跟 tool_result，工具结果进入 conversation 后再继续问模型，停止前给 hook 和质量检查一次阻止机会。

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

## 3. Runtime Management

Runtime Management 文件结构：

```
agent/runtime_management/
├── __init__.py
├── approvals.py              # ApprovalManager
├── compressor.py             # Tool History Snip / Context Compact
├── message_view.py           # canonical conversation 的工具结果读写视图
├── context/
│   ├── builder.py            # system prompt、startup context、动态附件
│   └── sources.py            # AGENTS.md、.nanocode/rules、Git 快照、frontmatter
├── hooks/
│   ├── config.py             # HookManager 配置和调度
│   ├── runner.py             # 外部进程 hook 执行
│   └── types.py
├── permissions/
│   ├── policy.py
│   ├── rules.py
│   ├── shell.py
│   └── workspace.py
└── persistence/
    ├── __init__.py
    ├── atomic.py             # 原子替换与 JSONL append helper
    ├── session_log.py        # durable session.jsonl checkpoint/resume
    ├── session_store.py      # session discovery/load/latest
    ├── run_store.py          # 每次请求 trace/report
    ├── task_state.py         # 单次请求内存状态
    ├── report.py             # trace 归一化和 report 构建
    └── artifacts.py          # 大结果 artifact
```

Runtime Management 可以做 I/O，因为它负责“怎么运转”。但它不能依赖 `cli/`、`tui/`、`providers/`。需要模型摘要时，`Compressor` 接收 `summarize_messages` callable；需要 compact 后恢复上下文时，接收 `build_post_compact_context` callable。两个 callable 都由 `AgentSession` 注入。

Runtime Management 模块之间的协作关系：

| 能力 | 入口 | 依赖输入 | 输出 |
|------|------|----------|------|
| context | `build_prompt_bundle()` | workspace、project instructions、Git 状态 | stable system prompt、startup context |
| compressor | `prepare_context_for_provider()` | Agent conversation、token 压力、summary callable | snipped 或 compacted conversation |
| permissions | `check_permission()` | tool name/input、mode、metadata、cwd | allow/deny/confirm |
| hooks | `HookManager.run()` | event name、`HookInput` | allow/deny/modify/append_context |
| approvals | `ApprovalManager.request()` | permission message、confirm fn 或 protocol resolve | approved/denied |
| persistence | `SessionLog`、`RunStore`、`ArtifactStore` | conversation、runtime events、tool output | session log、trace/report、artifact |

这些能力属于“运行框架”，不是“应用能力”。比如 permissions 不知道 `write_file` 如何写文件，只知道给定 tool metadata 和输入时是否允许尝试。

## 4. Application Layer 与 AgentSession

Application Layer 负责把 Agent Core、Runtime Management 和具体应用能力组装成可运行的本地 Code Agent。`cli/session.py` 是唯一装配点，负责：

- 创建 `Agent`。
- 创建 `Backend`。
- 创建 `ToolRegistry`、`SandboxManager`、`McpManager`、`SkillInvocation`、`ActiveSkillManager`。
- 创建 `MemoryRuntime` 和 `Compressor`。
- 捕获 hooks，加载 extensions。
- 把 `ExtensionRunner` 填入 Agent 回调槽位。
- 把 `_execute_tools()` 注入 `AgentLoop`。
- 把 `_prepare_context_for_provider()` 注入 `AgentLoop`，固定执行 Tool History Snip 后再 Context Compact。
- 给 CLI/TUI/Server 暴露统一的 `run()`、`chat()`、`run_once()`、`compact()`、`shutdown()`。

这对标 Pi 的 Session 桥接思路：内核不认识插件，Session 负责把插件 runner 接到回调槽位。

Session 桥接主要靠两类接口：

| 接口类型 | 例子 | 作用 |
|----------|------|------|
| core callback slot | `on_before_tool_call`、`on_after_tool_call`、`on_turn_start` | 把 extension runner 接到 Agent 生命周期和工具生命周期 |
| injected callable | `_execute_tools`、`_prepare_context_for_provider`、`_summarize_messages`、`_build_post_compact_context` | 让 Loop/Compressor 调用应用能力，但不让下层 import 应用层 |

这也是为什么 `cli/session.py` 看起来比其他文件“杂”：它是唯一允许同时认识 Agent Core、Runtime Management、provider、tools、memory、MCP、skills、sandbox、extensions 的总装配点。

## 5. 单次请求链路

`AgentSession.run(prompt)` 在 `AgentLoop` 外层包一层可审计运行状态：

```text
AgentSession.run(prompt)
  → TaskState.create(prompt)
  → RunStore.start_run()
  → trace: run_started
  → AgentLoop.run(prompt)
      → UserPromptSubmit hook
      → startup context / initial attachments
      → provider call
      → tool loop
      → Stop hook / built-in completion quality check
  → trace RuntimeEvent
  → session checkpoint: turn_finished
  → report.json
```

`SessionLog` 是 resume 的事实来源；`RunStore` 是单次请求的观测面。两者都由 `AgentSession` 更新，`Agent` 只持有 canonical conversation。

## 6. 内置完成质量检查

`cli/session.py` 有一个轻量 `_QualityState`。它只在用户请求明显要求修改 workspace 文件时启用：

- 如果整轮没有成功的 `edit_file` / `write_file`，Stop 前追加系统提醒并阻止结束一次。
- 如果最后一次修改后没有验证，Stop 前要求继续。验证可以是 `run_shell`、读取已修改文件，或 `grep_search` 覆盖修改路径。
- 工具执行后也会追加一次提醒，提示在最终回答前验证最终 workspace 状态。

这不是权限系统，也不是测试框架；它是 runtime 层的完成质量护栏，防止模型在代码修改任务里只描述计划或改完不看结果就结束。

质量检查的边界：

- 只基于用户 prompt 的关键词启发式判断是否需要 workspace 修改。
- 只把成功的 `edit_file` / `write_file` 视为 mutation。
- `run_shell` 总是算修改后的验证；`read_file` 需要读回修改路径；`grep_search` 需要覆盖修改路径。
- 每类失败只阻止停止一次，避免模型陷入无限自我纠正。
- 它不会替代 Benchmark verifier；最终正确性仍由测试、用户检查或 fixture verifier 证明。

## 7. Benchmark 覆盖

`benchmarks/local-fixture/tasks.json` 当前包含 41 个任务，直接约束 runtime 设计：

- `resume_*`：验证 session log 恢复、orphan tool call 修复和 interrupted run 标记。
- `run_artifacts_present`、`trace_contains_tool_events`、`report_tool_metrics`、`trace_error_recovery`：验证 `RunStore`、`TaskState`、trace/report schema。
- `context_large_result_persist`、`context_tool_history_snip_realistic`：验证 provider call 前 context 准备顺序和工具结果治理。
- 多数编辑类任务结合 allowed tools 与最终 verifier，间接验证 AgentLoop 工具循环和完成质量检查。

## 8. 设计决策

### 为什么 Agent 不再持有具体能力

工具、MCP、skills、memory、extensions 都是“Agent 能做什么”。这些属于应用层能力，不属于状态机协议。放进 Agent 会让 core 随能力增长而膨胀，也会破坏单向依赖。

### 为什么 ToolRuntime 不在 AgentLoop 里创建

ToolRuntime 需要权限、hooks、sandbox、MCP、extension before/after hook、event callback。这些都是应用层装配细节。Loop 创建 ToolRuntime 会直接依赖 `cli/core/tools`，违反 core 纯净原则。

### 为什么 Compressor 在 Runtime Management 而不是 core

压缩需要读写消息历史、运行 PreCompact hook、调用模型摘要，并维护工具结果裁剪策略。它是运行框架机制，不是状态机本身。放在 Runtime Management 后，core 仍然只描述对话协议；需要模型摘要和 compact 后恢复上下文时由 `AgentSession` 注入 callable。

## 9. 代码导读

阅读顺序：

```
agent/agent.py
agent/loop.py
agent/events.py
agent/types.py
cli/session.py
agent/runtime_management/compressor.py
agent/runtime_management/context/builder.py
agent/runtime_management/persistence/session_log.py
```

架构检查：

```bash
rg -n "from .*cli|from .*tui|from .*providers|import anthropic|import openai|open\(" \
  src/agent/agent.py src/agent/loop.py src/agent/events.py src/agent/types.py src/agent/models.py src/agent/budget.py

rg -n "from .*cli|from .*tui|from .*providers" src/agent/runtime_management -g '*.py'
```
