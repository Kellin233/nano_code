# Hooks：生命周期钩子

## 1. 为什么需要 Hooks

Hooks 给用户一个外部进程级的拦截面：提交 prompt 前可以修改，工具执行前可以 deny 或改参数，工具执行后可以追加上下文，模型停止时可以要求继续。

Hooks 位于 `agent/harness/hooks/`，因为它们是运行框架的横切机制。它们不是 Extension，也不注册新工具。

## 2. 文件结构

```
agent/harness/hooks/
├── __init__.py
├── types.py      # HookInput、HookOutput、HookCommand、HookEventName
├── config.py     # HookManager：加载配置、事件匹配、调度
└── runner.py     # 外部命令执行、超时、JSON 解析
```

## 3. 事件

| 事件 | 触发时机 | 能做什么 | 触发者 |
|------|---------|---------|--------|
| `UserPromptSubmit` | 用户消息即将发送给模型 | deny、modify、append_context | `AgentSession` 注入给 `AgentLoop` 的回调 |
| `PreToolUse` | 工具校验后、权限检查前 | deny、modify | `ToolRuntime` |
| `PostToolUse` | 工具执行后 | append_context | `ToolRuntime` |
| `Stop` | 模型无 tool call、准备结束时 | deny、append_context | `AgentSession` 注入给 `AgentLoop` 的回调 |
| `PreCompact` | Compact 前 | append_context | `Compressor` |

`HookEventName` 类型和 `Compressor` 调用点包含 `PreCompact`。但当前 `HookManager.capture()` 的 settings loader 只加载 `UserPromptSubmit`、`PreToolUse`、`PostToolUse`、`Stop` 四类事件；配置文件里写 `PreCompact` 暂不会被加载。只有代码手动构造带 `PreCompact` 的 `HookManager` 时，它才会运行。

## 4. Hook 协议

输入通过 JSON 从 stdin 传给外部命令，包含：

- `event`
- `session_id`
- `cwd`
- `prompt`
- `tool_name`
- `tool_input`
- `tool_result`
- `last_assistant_text`

输出从 stdout JSON 解析：

- `allow`
- `deny` + `reason`
- `modify` + `updated_input`
- `append_context` + `content`

这些动作不是独立事件流，也不会直接写成 `RuntimeEvent`。它们是调用点消费的控制结果：

| action | 可用事件 | 实际效果 |
|--------|----------|----------|
| `allow` | 全部事件 | 不改变当前链路，继续执行后续 hook 或原流程 |
| `deny` | `UserPromptSubmit`、`PreToolUse`、`Stop` | prompt 被替换为 blocked 文本、工具返回拒绝错误、或停止被阻止并继续一轮 |
| `modify` | `UserPromptSubmit`、`PreToolUse` | 替换 prompt 或工具输入；工具输入修改后必须重新 validate |
| `append_context` | `UserPromptSubmit`、`PostToolUse`、`Stop`、`PreCompact` | 追加用户上下文、工具 extra message、停止续跑上下文，或 compact 前摘要上下文 |

hook 输出的 `error` 字段只描述 hook 自身执行问题。是否把执行问题转成 deny，取决于单条 hook 的 `fail_closed`。

## 5. 真实触发点

Hooks 的事件名看起来像统一 bus，但当前实现是几个明确调用点分别消费结果：

| 事件 | 真实入口 | 消费方式 |
|------|----------|----------|
| `UserPromptSubmit` | `AgentSession._apply_user_prompt_hooks()` | 用户消息进入 conversation 前运行；`deny` 返回 blocked 文本，`modify.updated_input.prompt` 替换 prompt，`append_context` 拼到 prompt 后 |
| `PreToolUse` | `ToolRuntime.execute_one()` | 工具 schema validate 后、权限检查前运行；`deny` 返回 error `ToolResult`，`modify` 更新输入后重新 validate，再进入 permission |
| `PostToolUse` | `ToolRuntime.execute_one()` | 工具执行和大结果持久化后运行；`append_context` 写入 `ToolResult.extra_messages`，进入后续模型上下文 |
| `Stop` | `AgentSession._run_stop_hook()` | 模型无 tool call、准备结束时运行；`deny` 或 `append_context` 都会阻止停止，并向 Agent 追加 user context 继续一轮 |
| `PreCompact` | `Compressor._collect_precompact_context()` | compact 构造摘要输入前收集 `append_context`，作为旧消息的一部分进入 summary |

`PreCompact` 的边界需要特别注意：`types.py` 和 `Compressor` 支持它，但 `HookManager.capture()` 从 settings 加载时只接受 `UserPromptSubmit`、`PreToolUse`、`PostToolUse`、`Stop`。因此“代码调用点存在”不等于“用户配置已经能启用”。

## 6. modify 后重新校验

`PreToolUse` hook 修改工具输入后，`ToolRuntime` 会重新调用 `tool.validate()`。hook 是用户脚本，可能写错，也可能被项目配置污染。重新校验保证修改后的输入仍满足工具 schema，且后续仍会进入权限检查。

实际管线：

```text
tool.validate(original)
  → extension before_tool_call
  → PreToolUse hook
  → validate(modified input)
  → permission + confirmation
  → tool.call
  → extension after_tool_call
  → PostToolUse hook append_context
```

这条链路也说明 hook 不能绕过权限和 sandbox。`PreToolUse` 发生在 permission 前，可以把参数改成更安全或更受限的形式，但修改后的输入仍要接受同一套 path/rule/mode 检查；`run_shell` 仍只能通过 configured sandbox/backend 执行。

## 7. 项目 hooks 默认不信任

用户级 `~/.claude/settings.json` 的 hooks 默认加载。项目级 `.claude/settings.json` 的 hooks 需要显式设置：

```bash
NANO_CODE_TRUST_PROJECT_HOOKS=1
```

clone 下来的项目可能包含恶意 hooks，默认不执行是安全边界。

hook 失败策略由单条 hook 的 `fail_closed` 控制：

- 超时、非 JSON 输出、命令错误默认返回 allow 并记录 error。
- `fail_closed=true` 时，上述失败会返回 deny。
- `timeout_ms` 默认 3000。

## 8. Hook vs Extension

| 维度 | Hook | Extension |
|------|------|-----------|
| 位置 | `agent/harness/hooks/` | `cli/core/extensions/` |
| 形式 | 外部进程 | 进程内 Python |
| 典型用途 | 拦截、修改、拒绝、追加上下文 | 注册工具、注册命令、订阅事件 |
| 是否扩展工具 | 否 | 是 |

Hook 的安全优势是隔离：外部命令只能通过 JSON 输入输出影响指定生命周期点，不能直接拿到 Python 对象、ToolRegistry 或 Agent 内存状态。Extension 的能力更强，也意味着更适合受信任的本地扩展，而不是仓库随附的策略脚本。

## 9. 设计决策

### 为什么 hooks 放在 harness

Hooks 拦截用户 prompt、工具调用、停止和 compact，是运行框架的横切机制。它们不属于某个具体工具，也不应该让 Agent core 感知外部脚本。

### 为什么项目 hooks 默认不信任

项目目录可能来自不可信仓库。默认只加载用户级 hooks，项目 hooks 需要显式环境变量开启，避免 clone 后自动执行仓库内命令。

### 为什么 PreToolUse modify 后重验

Hook 可以改工具输入；改完不重新校验会绕过 schema 必填字段和参数约束。ToolRuntime 在每次 modify 后重新 `validate()`，再进入权限检查。

### 为什么 HookOutput 不建成 RuntimeEvent

RuntimeEvent 是给 UI、server 和 trace/report 消费的观测流；HookOutput 是调用点内部的控制协议。把两者混在一起会让外部 UI 可以误以为自己能“执行”hook 决策，也会让 hook 结果在持久化和恢复中承担不该承担的状态职责。

## 10. Benchmark 覆盖

当前 `benchmarks/local-fixture` 没有专门 hook case。hooks 仍影响工具、权限和 context compact 的公共链路；新增 hook benchmark 时应覆盖：

- `PreToolUse` deny 与 modify 后重新校验。
- `PostToolUse` append_context 是否进入下一轮消息。
- 项目 hooks 默认不加载，设置 `NANO_CODE_TRUST_PROJECT_HOOKS=1` 后才加载。

维护者可以用这些问题检查自己是否理解 hook 合同：

- hook 是外部进程拦截面，不是 plugin/extension；它不能注册工具，也不能直接改 Agent 状态。
- `PreToolUse` 可以改变参数，但不能跳过 schema validate、permission policy 或 sandbox。
- 项目 hooks 默认不可信，必须显式开启；这和 protected path、allowed tools 属于不同安全层。
- `PreCompact` 类型支持和 settings loader 支持不是同一件事；当前配置加载不会启用它。

## 11. 代码导读

```
agent/harness/hooks/types.py
agent/harness/hooks/config.py
agent/harness/hooks/runner.py
cli/session.py::_apply_user_prompt_hooks
cli/session.py::_run_stop_hook
cli/core/tools/runtime.py
```
