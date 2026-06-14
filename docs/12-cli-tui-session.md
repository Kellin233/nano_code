# CLI / TUI / Server / 会话

## 1. 为什么需要这几层

用户入口、交互 UI、headless server、会话恢复和运行工件都在 Agent core 之外。它们只负责把用户输入变成 Session 调用，再消费 `RuntimeEvent` 流。

当前装配边界是：

- `cli/main.py` 负责入口和模式选择。
- `cli/args.py` 负责 CLI 参数和环境变量解析。
- `cli/config.py` 保存应用层 `RuntimeConfig`。
- `cli/session.py` 负责创建和连接所有运行对象。
- `cli/thread.py` 负责把 `AgentSession` 包成 server/TUI 可消费的事件流，并处理 approvals。
- `tui/` 负责交互式 REPL 和渲染。
- `cli/core/server/` 和 `cli/core/protocol/` 负责 JSONL 协议 server。
- `agent/harness/persistence/` 负责 session log、run trace/report 和 artifact 持久化。

## 2. 文件结构

```
cli/
├── args.py             # argparse + RuntimeConfig 构造
├── config.py           # RuntimeConfig，应用层配置
├── main.py             # CLI 入口，一次性/TUI/server 模式选择
├── session.py          # AgentSession，唯一装配点
├── thread.py           # RuntimeThread，事件流包装和 approvals
├── logging_config.py
└── core/
    ├── protocol/       # JSONL protocol messages
    └── server/         # NanoCodeServer + transports
        └── transports/
            ├── stdio.py        # 已实现，CLI 当前只开放 --server stdio
            ├── unix_socket.py  # placeholder，初始化即 NotImplementedError
            └── websocket.py    # placeholder，初始化即 NotImplementedError

tui/
├── app.py
├── commands.py
├── input.py
├── renderer.py
├── state.py
└── theme.py

agent/harness/persistence/
├── atomic.py           # 原子写入和 JSONL append
├── session_log.py      # session.jsonl checkpoint/resume
├── session_store.py    # list/load/latest session
├── run_store.py        # trace.jsonl + report.json
├── task_state.py       # 单次 run 的内存状态
├── report.py           # trace event 与 report 构建
└── artifacts.py        # 大工具结果 artifact
```

## 3. RuntimeConfig 解析链路

`RuntimeConfig` 是应用层配置，不等同于 `AgentConfig`。它保存入口层需要的全部信息，然后只把 Agent core 需要的字段投影给 `Agent`：

```text
CLI args + env
  → resolve_runtime_config(args)
  → RuntimeConfig
  → RuntimeConfig.to_agent_config()
  → AgentConfig
```

主要字段传递如下：

| 输入 | RuntimeConfig 字段 | 下游消费者 |
|------|--------------------|------------|
| `--model` / `NANO_CODE_MODEL` | `model` | provider backend、`AgentConfig` |
| `--api-base` / API key env | `provider`、`api_base`、`anthropic_base_url`、`api_key` | backend 创建 |
| `--thinking` | `thinking` | `AgentConfig` 和 provider thinking mode |
| `--max-cost`、`--max-turns` | `max_cost_usd`、`max_turns` | `AgentLoop` budget/stop |
| `NANO_CODE_CONTEXT_WINDOW` | `context_window` | `AgentConfig.effective_window`，影响 snip/compact 阈值 |
| `--yolo`、`--accept-edits`、`--dont-ask` | `permission_mode` | `ToolRuntime` permission policy |
| `--sandbox*` / `NANO_CODE_SANDBOX` | `sandbox_config` | `SandboxManager` 和 `run_shell` |
| `--allowed-tools` | `allowed_tools` | schema 可见性、ToolRuntime allowlist、subagent/skill 交集 |

Provider 推断当前是保守的：有 `OPENAI_API_KEY + OPENAI_BASE_URL` 或显式 `--api-base` 时走 OpenAI-compatible；有 `ANTHROPIC_API_KEY` 时走 Anthropic；只有 `OPENAI_API_KEY` 时也走 OpenAI-compatible。`RuntimeConfig.to_agent_config()` 不携带 API key、permission、sandbox 或 allowed tools，因为这些属于应用装配层，不属于 Agent core 状态。

Server 入口也会构造 `RuntimeConfig`，但参数来自 JSONL request 而不是 argparse。无论来源是 CLI 参数、TUI 命令还是 server request，最终都必须经过 `create_session(config)`，避免出现多套 runtime 装配逻辑。

## 4. 三种运行模式

### 一次性模式

```
nanocode "fix bug"
  → cli/main.py
  → create_session(...)
  → session.chat(prompt)
  → 直接渲染 RuntimeEvent
  → session.shutdown()
```

一次性模式也会生成 session log 和 run artifacts。`--resume` 会先恢复最近会话，再执行本次 prompt。

### TUI 模式

```
nanocode
  → TuiApp.run()
  → 用户输入 / 命令分发
  → session.run(prompt)
  → renderer 渲染 RuntimeEvent
```

TUI 命令通过 `AgentSession` 或 `RuntimeThread` 暴露的窄方法访问能力，例如 `/compact`、`/clear`、`/memory`、`/remember`、`/skills`。

### Server 模式

```
nanocode --server stdio
  → run_stdio_server()
  → NanoCodeServer
  → RuntimeThread
  → AgentSession
  → RuntimeEvent.to_dict()
  → JSONL protocol
```

Server 不创建第二套 runtime。它通过 `RuntimeThread.submit()` 消费同一条 `AgentSession.run(prompt)` 事件流。

当前 CLI 只支持 `--server stdio`。`unix_socket.py` 和 `websocket.py` 文件存在，但都是占位实现，不能作为可用 transport 宣称。

三种入口的区别只在“输入从哪里来、事件渲染到哪里去”：

| 入口 | 输入 | 事件消费 | 是否复用 AgentSession |
|------|------|----------|------------------------|
| 一次性 CLI | 命令行 prompt | 直接渲染 `RuntimeEvent` | 是 |
| TUI | REPL 输入和命令 | `renderer` 增量渲染 | 是 |
| Server stdio | JSONL protocol | `RuntimeThread` 转成 protocol response/event | 是 |

## 5. AgentSession

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
- `RecentFileTracker`
- `SessionLog`
- `RunStore`
- `ArtifactStore`
- `AgentLoop`

它也负责桥接：

- ToolRuntime 的 before/after tool extension hook。
- Agent 生命周期事件到 ExtensionRunner。
- Loop 的 `execute_tools` 回调。
- Loop 的 `prepare_context_for_provider` 回调。
- Loop 的 conversation commit 回调。
- Compressor 的 summary callable。
- Compressor 的 post-compact recovery callable。
- MCP 工具变更到 ToolRegistry 和动态附件。
- memory startup context 与 `/remember` 写入。

`AgentSession` 文件会比普通模块更“胶水化”，这是设计选择：装配复杂度集中在一个地方，Agent core、provider、harness 和能力模块都保持边界清晰。

装配顺序也体现依赖方向：先构造 `Agent` 和 provider，再构造工具、sandbox、MCP、skills、memory、hooks、compressor，最后把 callable 注入 `AgentLoop`。`AgentLoop` 只知道“如何调用 provider、如何执行工具、如何在 provider call 前准备 context”，不知道这些 callable 背后是 TUI、server、memory 还是 MCP。

## 6. RuntimeThread

`RuntimeThread` 是 server/TUI 友好的事件流包装：

- 持有一个 `AgentSession`。
- 管理 `ApprovalManager`。
- 给 server/TUI 暴露 `submit()`、`abort()`、`compact()`、`clear_history()`、`restore_from_persistence()`。
- 把内部异常转换成 `runtime.error` 和 `turn.finished(error)` 事件。

协议层 approvals 由 `RuntimeThread` 管理；工具权限判断仍在 `ToolRuntime` + `agent/harness/permissions/`。

普通 CLI 一次性模式不走 `RuntimeThread`，而是在 `cli/main.py` 里给 `AgentSession` 设置同步 confirm 函数；TUI 直接持有 `AgentSession`。Server/headless 通道才使用 `RuntimeThread` 的 event queue 和 `ApprovalManager`。

Server approval 时序：

```text
client -> thread.submit
server -> runtime.event: approval.requested
client -> approval.resolve
server -> 继续 ToolRuntime
server -> runtime.event: tool.finished / turn.finished
server -> thread.submit response
```

`approval.requested` payload：

```json
{
  "thread_id": "<thread>",
  "request_id": "<approval-request>",
  "call_id": "<tool-call>",
  "tool_name": "write_file",
  "message": "write new file: example.txt",
  "requires_explicit_confirmation": false
}
```

`approval.resolve` 必须带 `thread_id` 和 `request_id`。未知 `request_id` 返回 `resolved=false`；缺少 `request_id` 返回 `invalid_params`。

批准后的权限消息还会进入 `ToolRuntime.confirmed` 集合，同一会话内相同确认消息不会重复询问。`remember` 字段被协议接收，但当前没有持久化到 settings。

更完整的 approval 内部时序是：

```text
ToolRuntime.check_permission()
  → confirm_fn(...)
  → RuntimeThread._confirm()
  → ApprovalManager.request()
  → event queue: approval.requested
  → client approval.resolve
  → ApprovalManager.resolve()
  → pending future completes
  → ToolRuntime continues or returns denied ToolResult
```

`RuntimeThread.abort()` 会同时 abort pending approvals、标记 session abort，并取消当前 producer task。这样 server 客户端中断时不会留下永远等待的 permission future。

## 7. AgentSession 完成质量检查

`AgentSession` 除了装配运行对象，还维护 `_QualityState`：

```text
用户请求疑似需要修改 workspace
  → 观察 edit_file / write_file 成功结果
  → 记录修改路径
  → 观察后续 read_file / grep_search / run_shell 是否验证
  → Stop 前如果缺少修改或验证，则追加 system-reminder 并继续一轮
```

这条链路只影响当前 turn，不改权限策略，也不替代 benchmark verifier。它的目标是让修改类任务在最终回答前先确认磁盘最终状态。

## 8. 会话持久化

会话恢复使用 durable session log：

```
~/.nanocode/sessions/<session-id>/
└── session.jsonl
```

`session.jsonl` 是 resume 的事实来源。它记录稳定边界：

- `session`：会话元数据，包含 workspace、provider、model。
- `message`：canonical `ConversationMessage`。
- `compact`：Context Compact 后的 working conversation snapshot。
- `replace`：修复或其他非 append 场景下替换整个 conversation snapshot。
- `clear`：清空历史。
- `checkpoint`：稳定边界标记，例如 `turn_finished`。

它不记录 provider streaming 中间 token。`assistant.delta` 只进入 run trace，不进入 conversation。

恢复流程：

```
--resume
  → get_latest_session_id()
  → create_session(config, thread_id=session_id)
  → session.restore_from_persistence()
  → SessionLog.load(repair=False)
  → SessionLog.load(repair=True)
  → Agent.restore_conversation(history)
  → RunStore.mark_unfinished_interrupted(session_id=...)
```

如果发现 assistant tool call 后没有对应 tool result，恢复层会补 synthetic error tool result，内容为 `Interrupted before tool result`，避免下一次 provider call 因协议不完整失败。

session log 的事件粒度刻意小于 trace：它只记录能恢复 canonical conversation 的稳定边界。`compact` 和 `replace` 记录的是替换后的工作 conversation snapshot；`checkpoint` 表示 turn 已达到稳定点；`clear` 是用户主动清空历史。恢复时 replay 这些事件，而不是读取上一次 run 的 report。

## 9. Run artifacts

每次 `AgentSession.run(prompt)` 都会创建一个 run：

```
<workspace>/.nanocode/runs/<run-id>/
├── trace.jsonl
└── report.json
```

`TaskState` 是单次 run 的内存状态，不单独写 `task_state.json`。它在运行中记录：

- `run_id`
- `task_id`
- `user_request`
- `status`
- `tool_steps`
- `attempts`
- `last_tool`
- `stop_reason`
- `final_answer`

`trace.jsonl` 是过程事件流，面向调试和 benchmark：

- `run_started`
- `assistant_delta`
- `tool_started`
- `tool_executed`
- `approval_requested`
- `budget_exceeded`
- `runtime_error`
- `conversation_committed`
- `run_finished`
- `run_interrupted`

`report.json` 是最终摘要，面向评测和快速审计：

- run/task/status/stop reason
- final answer
- tool steps / attempts
- started/finished/duration
- runtime metadata：model、provider、permission mode、allowed tools、workspace、session id
- usage：input/output/cache/cost
- metrics：tool counts、error counts、approval counts

`trace.jsonl` 可以比 session log 更细；它不是 conversation 的 source of truth。恢复永远以 `session.jsonl` 为准。

## 10. ArtifactStore

超大工具结果写到 workspace：

```
<workspace>/.nanocode/artifacts/tool-results/<call-id>.txt
```

ToolRuntime 只把 `<persisted-output>` 预览和 artifact metadata 放回 `ToolResult`。这样 session log 和 provider payload 不会被巨大输出撑爆，同时完整结果仍可从本地文件检查。

## 11. Compact 后恢复

`Context Compact` 成功后，`AgentSession` 会通过 `build_post_compact_context` 重新装配运行时上下文：

- project instructions / Git startup snapshot
- local memory
- active skills
- deferred tools listing
- 最近 read/edit/write 文件的当前内容

最近文件恢复只记录路径。恢复时重新读磁盘当前内容，不缓存旧内容；workspace 外、缺失、二进制或超预算文件只写状态说明。

## 12. 边界与失败模式

入口层的失败处理目标是“不破坏 session source of truth”：

| 场景 | 行为 |
|------|------|
| provider/runtime 异常 | `RuntimeThread` 转成 `runtime.error` 和 `turn.finished(error)` |
| 用户 abort | pending approvals 被拒绝，当前 task 取消，turn 以 aborted 结束 |
| approval 缺少 request id | server 返回 `invalid_params` |
| approval request id 不存在 | 返回 `resolved=false`，不影响其它 pending approval |
| resume 发现孤儿 tool call | 补 synthetic error tool result，并写回 repaired session log |
| 上次 run 未完成 | `RunStore.mark_unfinished_interrupted()` 标记 interrupted |
| trace/report 写入失败 | 不应替代 session log；恢复仍以 `session.jsonl` 为准 |
| `unix_socket` / `websocket` transport | 初始化即 `NotImplementedError`，不能作为可用 server transport |

这些边界说明 CLI/TUI/server 的职责是“接入和观测”，不是改变 AgentLoop 的语义。权限、sandbox、context、MCP、memory 都经由 `AgentSession` 装配后进入同一条 core/harness 链路。

## 13. Benchmark 覆盖

`benchmarks/local-fixture` 对 CLI/session/persistence 的约束最直接：

- `resume_orphaned_tool_call`、`resume_checkpoint_*`、`resume_hidden_goal`：验证 `--resume`、session log repair、旧 run interrupted 标记。
- `run_artifacts_present`、`trace_contains_tool_events`、`report_tool_metrics`、`trace_error_recovery`：验证每次 `AgentSession.run()` 都产出 trace/report，并记录工具错误和恢复。
- permission suite 通过 `permission_mode` 验证 CLI/RuntimeConfig 到 ToolRuntime 的模式传递。
- context-governance 任务通过 task-level `context_window` 验证 `RuntimeConfig.context_window` 传到 `AgentConfig` 后能影响压缩阈值。

维护者自查重点：

- 为什么 `RuntimeConfig.to_agent_config()` 不传 permission/sandbox/API key 之外的应用层对象？
- 为什么 server approval 由 `RuntimeThread` 管，而不是让 protocol 层直接调用 ToolRuntime？
- 为什么 `trace.jsonl` 不能作为 resume 的事实来源？
- 为什么 placeholder transport 文件存在，也不能把 unix/websocket 当作可用 server transport？

## 14. 设计决策

### 为什么 CLI 不直接组装所有能力

如果 `main.py` 直接创建工具、MCP、memory、extensions，入口会变成第二个 runtime。把装配集中到 `AgentSession` 后，CLI、TUI、Server 都复用同一条路径。

### 为什么 Server 放到 cli/core

Server/protocol 是应用层能力，不属于 Agent core。它消费 `RuntimeEvent`，但不改变 Agent 状态机。

### 为什么 session log 和 run trace 分开

session log 负责恢复对话上下文，必须小而稳定。run trace 负责观测、排障和 benchmark，可以包含流式 delta、工具事件和错误细节。两者职责不同，不能互相替代。

### 为什么不保留 snapshot JSON

当前实现以 `session.jsonl` 为唯一 resume 格式。列表和加载通过 replay session log 得到 metadata 和 conversation snapshot，不再维护一份独立 snapshot 文件，避免 source of truth 重复。

## 15. 代码导读

```
cli/args.py
cli/config.py
cli/main.py
cli/session.py
cli/core/tools/recent_files.py
cli/thread.py
cli/core/server/app_server.py
cli/core/protocol/messages.py
cli/core/server/transports/stdio.py
tui/app.py
agent/harness/persistence/session_log.py
agent/harness/persistence/run_store.py
agent/harness/persistence/report.py
```
