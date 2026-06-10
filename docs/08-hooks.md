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

## 5. modify 后重新校验

`PreToolUse` hook 修改工具输入后，`ToolRuntime` 会重新调用 `tool.validate()`。hook 是用户脚本，可能写错，也可能被项目配置污染。重新校验保证修改后的输入仍满足工具 schema，且后续仍会进入权限检查。

## 6. 项目 hooks 默认不信任

用户级 `~/.claude/settings.json` 的 hooks 默认加载。项目级 `.claude/settings.json` 的 hooks 需要显式设置：

```bash
NANO_CODE_TRUST_PROJECT_HOOKS=1
```

clone 下来的项目可能包含恶意 hooks，默认不执行是安全边界。

## 7. Hook vs Extension

| 维度 | Hook | Extension |
|------|------|-----------|
| 位置 | `agent/harness/hooks/` | `cli/core/extensions/` |
| 形式 | 外部进程 | 进程内 Python |
| 典型用途 | 拦截、修改、拒绝、追加上下文 | 注册工具、注册命令、订阅事件 |
| 是否扩展工具 | 否 | 是 |

## 8. 代码导读

```
agent/harness/hooks/types.py
agent/harness/hooks/config.py
agent/harness/hooks/runner.py
cli/session.py::_apply_user_prompt_hooks
cli/session.py::_run_stop_hook
cli/core/tools/runtime.py
```
