# CLI / TUI / Server / 会话

## 1. 为什么需要这几层

用户入口、交互 UI、headless server 和会话持久化都在 Agent core 之外。它们只负责把用户输入变成 Session 调用，再消费 RuntimeEvent 流。

当前装配边界是：

- `cli/main.py` 负责入口和模式选择。
- `cli/session.py` 负责创建和连接所有运行对象。
- `cli/thread.py` 负责把 `AgentSession` 包成 server/TUI 可消费的事件流，并处理 approvals。
- `tui/` 负责交互式 REPL 和渲染。
- `cli/core/server/` 和 `cli/core/protocol/` 负责 JSONL 协议 server。
- `agent/harness/session/` 负责 session 和 artifact 持久化。

## 2. 文件结构

```
cli/
├── args.py             # argparse + RuntimeConfig 构造
├── main.py             # CLI 入口，一次性/TUI/server 模式选择
├── session.py          # AgentSession，唯一装配点
├── thread.py           # RuntimeThread，事件流包装和 approvals
├── logging_config.py
└── core/
    ├── protocol/       # JSONL protocol messages
    └── server/         # NanoCodeServer + transports

tui/
├── app.py
├── commands.py
├── input.py
├── renderer.py
├── state.py
└── theme.py

agent/harness/session/
├── __init__.py         # save/load/list session
├── event_store.py      # events.jsonl
└── artifacts.py        # large artifacts
```

## 3. 三种运行模式

### 一次性模式

```
nanocode "fix bug"
  → cli/main.py
  → create_session(...)
  → session.chat(prompt)
  → 直接渲染 RuntimeEvent
```

### TUI 模式

```
nanocode
  → TuiApp.run()
  → 用户输入 / 命令分发
  → session.run(prompt)
  → renderer 渲染 RuntimeEvent
```

### Server 模式

```
nanocode --server stdio
  → NanoCodeServer
  → RuntimeThread
  → AgentSession
  → RuntimeEvent.to_dict()
  → JSONL protocol
```

## 4. AgentSession

`AgentSession` 是运行时装配边界。它创建：

- `Agent`
- provider backend
- `ToolRegistry` / `ToolRuntime`
- `SandboxManager`
- `McpManager`
- `SkillInvocation` / `ActiveSkillManager`
- `MemoryRuntime`
- `HookManager`
- `ExtensionRunner`
- `Compressor`
- `AgentLoop`

它也负责桥接：

- ToolRuntime 的 before/after tool extension hook。
- Agent 生命周期事件到 ExtensionRunner。
- Loop 的 `execute_tools` 回调。
- Compressor 的 summary callable。
- MemoryRuntime 的 side-query callable。

## 5. RuntimeThread

`RuntimeThread` 是 server/TUI 友好的事件流包装：

- 持有 `AgentSession`。
- 维护 `SessionEventStore`。
- 管理 `ApprovalManager`。
- 给 server 暴露 `submit()`、`abort()`、`compact()`、`restore_session()`。

`AgentSession.approvals` 不是权限状态的唯一来源；协议层 approvals 由 `RuntimeThread` 管理。

## 6. 会话持久化

```
~/.nanocode/sessions/
├── <session-id>.json              # snapshot 兼容路径
└── <session-id>/
    ├── events.jsonl               # RuntimeEvent append-only log
    ├── artifacts/
    └── tool-results/
```

`SessionEventStore` 保存事件流，`ArtifactStore` 保存大 artifact，`save_session/load_session` 继续支持 snapshot resume。

## 7. 设计决策

### 为什么 CLI 不直接组装所有能力

如果 `main.py` 直接创建工具、MCP、memory、extensions，入口会变成第二个 runtime。把装配集中到 `AgentSession` 后，CLI、TUI、Server 都复用同一条路径。

### 为什么 Server 放到 cli/core

Server/protocol 是应用层能力，不属于 Agent core。它消费 RuntimeEvent，但不改变 Agent 状态机。

### 为什么会话在 harness

会话持久化是运行框架机制，需要文件 I/O，但不应依赖 CLI 或 TUI。放在 harness 符合“怎么运转”的边界。

## 8. 代码导读

```
cli/args.py
cli/main.py
cli/session.py
cli/thread.py
cli/core/server/app_server.py
cli/core/protocol/messages.py
tui/app.py
agent/harness/session/event_store.py
```
