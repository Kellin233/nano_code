# Nano Code 完整重构设计 v1

## 结论

本次重构的目标是一次性把 Nano Code 从围绕 `Agent` 大对象运行的 CLI 程序，整理成以 `RuntimeThread` 为公开执行入口、以 runtime event 为统一输出、以 append-only session event store 为事实来源的 code agent runtime。

这不是长期分阶段并存方案。内部施工可以按依赖顺序推进和验收，但最终交付形态必须是同一套目标架构：

- 仓库目录改为 `nanocode`，源码直接位于 `src/`，并通过 `package-dir` 映射为 Python 包 `nanocode`。
- CLI 主命令是 `nanocode`，`nano-code` 可以作为短期 alias。
- CLI/TUI/server 都通过 `RuntimeThread` 提交 turn，不直接访问旧 `agent._xxx` 状态总线。
- `core/runtime/providers/capabilities/session/protocol/server/sdk` 都在本次重构范围内。
- `unix_socket` 和 `websocket` 不实现真实传输，只保留明确的 `NotImplementedError` 占位。
- sandbox 是一等安全子系统，不能被 runtime 重构弱化。

`nanocode.runtime.agent.Agent` 是内部有状态执行模块，不能作为应用边界或推荐 API。外部集成应使用 `nanocode.runtime.RuntimeThread`、stdio protocol 或 SDK。

## 总体设计

### 分层关系

```text
CLI / TUI / SDK client / future IDE
  ↓
RuntimeThread / protocol server
  ↓
core AgentTurn / provider-neutral events
  ↓
providers / ToolRuntime / approvals / capabilities
  ↓
domain packages: tools, memory, skill, mcp, hooks, permissions, sandbox
  ↓
filesystem / provider SDK / sandbox backend / MCP server
```

核心边界：

- `core/` 只表达 agent turn 状态机和 provider-neutral 消息模型。
- `runtime/` 是组合根，持有 thread、config、events、approval、capability manager 和 turn result。
- `providers/` 负责 Anthropic/OpenAI-compatible 的 stream 解析、tool call 聚合和 usage 归一化。
- `capabilities/` 只做薄生命周期接入，不承载领域实现。
- `session/` 负责 event store、snapshot、artifact。
- `protocol/` 定义 JSONL 方法、请求响应、错误。
- `server/transports/stdio.py` 实现 JSONL over stdin/stdout。
- `sdk/` 提供 Python client 和 thread client。

### 目标目录

```text
src/
  core/
    messages.py
    ports.py
    turn.py

  runtime/
    config.py
    events.py
    approvals.py
    capability.py
    thread.py
    agent/

  providers/
    base.py
    anthropic.py
    openai_chat.py

  capabilities/
    tools/
      provider.py
    skills/
      provider.py
    memory/
      provider.py
    mcp/
      provider.py
    subagents/
      provider.py
    hooks/
      provider.py

  domains/
    tools/
    skills/
    memory/
    mcp/
    subagents/
    hooks/
    permissions/
    sandbox/
    context/

  session/
    event_store.py
    snapshots.py
    artifacts.py

  protocol/
    types.py
    methods.py
    errors.py
    dispatcher.py

  server/
    app_server.py
    transports/
      stdio.py
      unix_socket.py
      websocket.py

  sdk/
    client.py
    thread.py

  tui/
```

领域实现统一放入 `domains/`：`domains/tools/`、`domains/memory/`、`domains/skills/`、`domains/mcp/`、`domains/hooks/`、`domains/permissions/`、`domains/sandbox/`、`domains/context/`。这些包是业务能力本体，不应该被搬进 `capabilities/`。`capabilities/` 只负责把这些能力挂到 runtime 生命周期上。

内部 Agent 执行模块位于 `runtime/agent/`，用于承载当前成熟的模型循环、上下文压缩、工具回灌和 token/cost 统计；它不占据顶层目录，也不作为公开主入口。

## 详细设计

### 1. core

`core/messages.py` 定义 provider-neutral 的 `Message`、`CoreToolCall`、`CoreToolResult`、`ModelTextDelta`、`ModelTurnComplete`、`ModelUsage`。

`core/ports.py` 只定义 `ModelProvider` 和 `ToolExecutor` Protocol。core 不 import Anthropic/OpenAI SDK，不 import TUI，不读写 session 文件。

`core/turn.py` 实现 `AgentTurn`：

```text
messages -> ModelProvider.stream_turn()
  -> ModelTextDelta 直接向上游透传
  -> ModelTurnComplete
      -> 无 tool call: TurnFinished(stop)
      -> 有 tool call: ToolExecutor.execute()
      -> tool result 追加为 tool message
      -> 继续下一轮 model turn
```

这样做的原因：

- model stream 解析不再散落在主循环里。
- tool 执行管线可以用 fake provider/fake tools 独立测试。
- core 的复杂度只来自 agent loop 本身，不来自 IO 和 UI。

### 2. runtime

`RuntimeThread` 是唯一公开 turn 执行入口：

```text
RuntimeThread.submit(prompt) -> AsyncIterator[RuntimeEvent]
RuntimeThread.chat(prompt) -> TurnResult
RuntimeThread.abort()
RuntimeThread.compact()
RuntimeThread.clear_history()
RuntimeThread.invoke_skill()
RuntimeThread.shutdown()
```

`RuntimeThread` 负责：

- 初始化 capability manager。
- 创建并维护 thread id。
- 把 user input、assistant delta、tool start、tool finish、approval、compact、abort、error、turn finished 写入 `SessionEventStore`。
- 把内部执行事件转换成稳定的 `RuntimeEvent`。
- 管理 approval pending request，支持 TUI confirm 和 protocol `approval.resolve`。
- 向 TUI/CLI 暴露兼容命令方法，但不让 TUI 直接操作 core/private state。

`RuntimeEvent` 是统一事件模型：

```text
{
  "type": "assistant.delta",
  "thread_id": "...",
  "seq": 12,
  "timestamp": 1234567890.0,
  "payload": {...}
}
```

事件类型包括：

- `user.input`
- `assistant.delta`
- `tool.started`
- `tool.finished`
- `approval.requested`
- `approval.resolved`
- `context.compacted`
- `budget.exceeded`
- `api.retry`
- `runtime.error`
- `turn.finished`

### 3. providers

`providers/anthropic.py` 和 `providers/openai_chat.py` 负责 provider 原生 stream 到 core model event 的转换：

- 文本 delta 归一化为 `ModelTextDelta`。
- tool call 聚合为 `CoreToolCall`。
- usage 归一化为 `ModelUsage`。
- provider 原生 final message 放在 `AssistantMessage.provider_message`，仅 provider adapter 知道其结构。

core 不直接读取 Anthropic event chunk，也不解析 OpenAI delta。

### 4. ToolRuntime / permissions / sandbox

工具执行仍然走现有领域实现：

```text
validation
  -> PreToolUse hooks
  -> permission policy
  -> approval request
  -> execute
  -> large result persistence
  -> PostToolUse hooks
  -> result shaping
```

`run_shell` 必须继续通过 `SandboxManager.run_shell()`。禁止在新 runtime 中绕过 `SandboxManager` 直接调用 `subprocess.run()`。

文件工具当前仍在宿主进程执行，通过 permission policy、read-before-write 和 protected path 约束降低风险。shell sandbox 化不能被误认为已经覆盖文件工具。

### 5. capabilities

`runtime/capability.py` 定义：

- `CapabilityContext`
- `CapabilityProvider`
- `CapabilityManager`

各 capability provider 只做 runtime 生命周期接入：

- `capabilities/tools/provider.py` 贡献 builtin tool definitions。
- `capabilities/memory/provider.py` 记录 memory runtime 状态。
- `capabilities/skills/provider.py` 连接 skill discovery 状态。
- `capabilities/mcp/provider.py` 连接 MCP 生命周期。
- `capabilities/hooks/provider.py` 连接 hook 生命周期。
- `capabilities/subagents/provider.py` 连接 sub-agent 生命周期。

具体能力仍在领域包中：

- memory 检索、渲染、持久化仍在 `domains/memory/`。
- skill 发现和 prompt 渲染仍在 `domains/skills/`，共享 frontmatter/prompt 入口在 `domains/context/`。
- MCP 连接、资源、工具输出仍在 `domains/mcp/`。
- hooks 配置和执行仍在 `domains/hooks/`。
- tool registry/runtime/builtin tools 仍在 `domains/tools/`。
- permissions 和 sandbox 安全边界仍在 `domains/permissions/`、`domains/sandbox/`。

这样拆分的原因是避免 `capabilities/tools.py`、`capabilities/skills.py`、`capabilities/memory.py` 变成新的大杂烩，同时也避免把领域实现搬到 runtime 层造成反向依赖。

### 6. session

`session/event_store.py` 是事实来源：

```text
~/.nanocode/sessions/<thread_id>/events.jsonl
```

特点：

- append-only。
- 每行一个 `RuntimeEvent`。
- 支持 replay。
- `next_seq()` 从已有事件恢复序号。

`session/snapshots.py` 用于恢复加速，不是事实来源。

`session/artifacts.py` 保存大输出：

```text
~/.nanocode/sessions/<thread_id>/artifacts/<artifact_id>
```

大 stdout/stderr、MCP blob、tool large result 不应该完整塞入 event store。event store 只保存 preview 和 artifact ref。

### 7. protocol / server / sdk

协议是 JSONL over stdin/stdout：

```json
{"id":1,"method":"thread.create","params":{"config":{"model":"..."}}}
{"id":2,"method":"thread.submit","params":{"thread_id":"...","prompt":"..."}}
```

server 可以输出两类消息：

```json
{"method":"runtime.event","params":{...}}
{"id":2,"result":{"thread_id":"...","events":12,"stop_reason":"stop"}}
```

支持方法：

- `thread.create`
- `thread.resume`
- `thread.submit`
- `thread.abort`
- `thread.compact`
- `approval.resolve`
- `session.list`

stdio transport 必须并发处理请求。原因是 `thread.submit` 可能等待 approval，如果 transport 在该请求完成前不继续读取 stdin，客户端就无法发送 `approval.resolve`，会产生死锁。

`sdk/NanoCodeClient` 负责启动 stdio server、发送请求、读取响应。

`sdk/ThreadClient` 负责 thread 级别操作：

- `submit(prompt)` 流式产出 runtime events。
- `abort()`
- `compact()`
- `resolve_approval()`

### 8. CLI / TUI

CLI 创建 `RuntimeConfig` 和 `RuntimeThread`。

one-shot：

```text
nanocode "prompt"
  -> RuntimeThread.chat(prompt)
```

interactive：

```text
nanocode
  -> TuiApp(RuntimeThread).run()
```

server：

```text
nanocode --server stdio
  -> StdioTransport.run()
```

TUI 只依赖 runtime 暴露的方法：`chat`、`abort`、`is_processing`、`set_confirm_fn`、`clear_history`、`compact`、`show_cost`、`invoke_skill`、`shutdown`。TUI 不应该读取 `_anthropic_messages`、`_tool_registry`、`_mcp_manager` 等内部状态。

## sandbox 设计

sandbox 是安全边界，不是普通工具实现细节。

必须保留的 profile：

- `workspace`
- `read-only`
- `local`
- `danger-full-access`
- `microsandbox-dev`
- `microsandbox-safe`
- `microsandbox-strict`
- `microsandbox` alias

必须保留的 backend：

- `local`
- `bwrap`
- `microsandbox`

必须保留的语义：

- Linux 默认 `workspace`，非 Linux 默认 `local`。
- network 默认 `none`，只有 local/danger full access 默认 `default`。
- bwrap/microsandbox 不默认转发 host env。
- env 只通过 allowlist 转发。
- protected paths 默认包含 `.git`、`.env`、`.env.*`、`.codex`、`.claude`。
- workspace/read-only 不允许 shell 写出工作区边界。
- backend 不可用时默认 fail closed。
- 只有显式 `allow_fallback_to_local` 才能 fallback。
- `microsandbox-strict` 永远不能 fallback 到 local。
- `SandboxManager.host_path_to_guest_path()` 必须拒绝 workspace 外 cwd。
- `SandboxManager.describe()` 必须能解释 profile、backend、network、home/env/protected path/fallback 边界。

不能做的事：

- 不能因为 runtime 重构把 `run_shell` 改回裸 `subprocess.run()`。
- 不能让 `--yolo` 绕过 protected paths。
- 不能把 file tools 说成已经被 sandbox 隔离。
- 不能在 bwrap/microsandbox 中默认暴露 host secrets。

## 硬性约束

1. 新包名为 `nanocode`，源码直接在 `src/`，由 `pyproject.toml` 的 `package-dir = {"nanocode" = "src"}` 映射为 Python 包。
2. `pyproject.toml` entry point 为 `nanocode`，可保留 `nano-code` alias。
3. `RuntimeThread.submit()` 是公开 turn 执行入口。
4. CLI/TUI/server 不直接调用内部 `Agent.chat()` 作为主路径。
5. provider stream 解析只放在 provider adapter 或 runtime agent adapter 内，不扩散到 TUI/server。
6. tool 调用必须经过 validation、hooks、permission、approval、execute、result shaping。
7. session event store 是 append-only。
8. snapshot 不能成为事实来源。
9. 大输出进入 artifact。
10. `unix_socket.py`、`websocket.py` 必须明确未实现，不能假装可用。

## 隐含要求

- 保留现有用户可见能力：tools、MCP、memory、skills、hooks、TUI、session resume、sub-agent、sandbox。
- 保留现有测试契约，尤其是 sandbox、permissions、hooks、MCP、tool runtime。
- 不引入重量级框架或 DI container。
- 代码风格保持 dataclass、Protocol、小模块、小函数，务实可读。
- 抽象必须服务于边界和测试，不为了目录漂亮而拆。
- 新协议和 SDK 必须能在没有长期 daemon 的情况下工作。

## 不能做什么

- 不做长期两套主架构并行。
- 不把所有工具、memory、skill、MCP 实现搬进 `capabilities/`。
- 不让 `capabilities/` 变成新的大文件集合。
- 不让 TUI 直接访问 runtime/private state。
- 不把 approval 绑死到 blocking input。
- 不在 stdio server 中串行阻塞所有请求。
- 不在 event store 中保存不可控的大 blob。
- 不隐藏 sandbox fallback。
- 不用复杂框架、反射注册、元类或过度泛型炫技。
- 不把内部 `Agent` 包装成公开主 API。

## 可能踩坑

1. `session.py` 与 `session/` 包不能同时作为真实 import 入口存在，否则导入行为不稳定。
2. `session.event_store` 顶层导入 `runtime` 容易造成循环依赖，应使用局部导入或弱类型边界。
3. approval 如果先阻塞 engine 再发事件，会导致 protocol 客户端永远拿不到 `request_id`。
4. stdio transport 如果串行处理 `thread.submit`，客户端无法发送 `approval.resolve`，会死锁。
5. capability provider 贡献 builtin tools 时要避免重复注册；ToolRegistry 需要按名称去重。
6. provider adapter 不能把 provider 原生 chunk 泄漏到 core。
7. OpenAI tool arguments 可能是分片 JSON，必须聚合后再解析。
8. Anthropic thinking block 不能直接进入后续消息历史。
9. 大 tool result 既可能在 ToolRuntime 层持久化，也可能在 session artifact 层引用，不能无限复制。
10. 删除旧源码目录前要确认新 `src/` 已包含用户未提交改动。

## 完整重构执行序列

这些步骤是一次性完成过程中的施工顺序，不是长期阶段。

1. 迁移包布局到 `src/`，更新 `pyproject.toml`、entry points、测试 import、文档命令。
2. 建立 `core/` 和 `runtime/` 基础模型。
3. 建立 provider adapters。
4. 保留并接入 `domains/tools`、`domains/permissions`、`domains/sandbox` 领域实现。
5. 建立 `capabilities/<ability>/provider.py` 薄接入层。
6. 让 CLI/TUI 使用 `RuntimeThread`。
7. 建立 session event store、snapshot、artifact。
8. 建立 protocol、stdio server、SDK。
9. 收拢内部 Agent 执行模块，清理旧包主路径。
10. 更新设计文档和测试。

## 验收条件

必须通过：

```bash
python -m compileall src test
python -m unittest discover -s test -v
python -m unittest discover -s test/v1 -v
```

关键检查点：

- `nanocode --help` 使用新命令名。
- `nanocode --server stdio` 可启动 JSONL server。
- `RuntimeThread` 可 import，CLI/TUI 主路径使用 runtime。
- `SessionEventStore` 可 append/replay。
- `ArtifactStore` 能保存大输出引用。
- protocol server 支持 `thread.create`、`thread.submit`、`thread.abort`、`thread.compact`、`approval.resolve`、`session.list`。
- sandbox 测试继续覆盖 profile、bwrap argv、microsandbox、fallback、network none、env allowlist、protected paths。

## 当前落地状态

已落地：

- `src/` 直接承载 `nanocode` 包内容。
- `nanocode` 主命令和 `nano-code` alias。
- `core/messages.py`、`core/ports.py`、`core/turn.py`。
- `runtime/config.py`、`runtime/events.py`、`runtime/approvals.py`、`runtime/capability.py`、`runtime/thread.py`。
- Anthropic/OpenAI-compatible provider adapters。
- `domains/` 领域实现聚合目录。
- `capabilities/tools|memory|skills|mcp|hooks|subagents/provider.py`。
- `runtime/agent/` 内部执行模块。
- `session/event_store.py`、`session/snapshots.py`、`session/artifacts.py`。
- JSONL protocol、stdio server、Python SDK。
- `unix_socket.py`、`websocket.py` 明确占位。
- CLI/TUI 主路径切到 `RuntimeThread`。
- 新增架构测试覆盖 core turn、session event/artifact、protocol server 基本方法。

内部执行模块：

- `nanocode.runtime.agent.Agent` 仍存在，用于承载已成熟的模型循环、上下文压缩、工具回灌和 token/cost 统计。
- 它不再是 CLI/TUI/server 的公开主入口。
- 后续如果继续收窄，应逐步把内部执行也迁到 `core.AgentTurn + providers + ToolRuntime`，但不再改变外部 runtime/protocol 边界。
