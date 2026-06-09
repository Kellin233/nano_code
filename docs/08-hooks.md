# Hooks：生命周期钩子

## 概述

`capabilities/hooks/` 在工具执行的四个关键节点插入用户自定义脚本。Hook 不是权限系统——它是在权限检查之外，给用户一个额外的拦截和修改通道。

## 四个事件

| 事件 | 触发时机 | 能做什么 |
|------|---------|---------|
| `UserPromptSubmit` | 用户消息即将发送给模型 | deny（阻止）、modify（修改 prompt）、append_context（追加） |
| `PreToolUse` | 工具校验后、权限检查前 | deny（阻止）、modify（修改输入） |
| `PostToolUse` | 工具执行后 | append_context（追加系统消息） |
| `Stop` | 模型结束响应时 | deny（强制再跑一轮）、append_context（追加后继续） |

## 架构

```
HookManager.capture()
    │
    ├── 加载 ~/.claude/settings.json 中的 hooks 配置
    ├── 项目级 .claude/settings.json 默认不加载
    │   （需 NANO_CODE_TRUST_PROJECT_HOOKS=1）
    │
    └── run(event, hook_input) → [HookOutput, ...]
        对匹配 event+matcher 的 hook，调用命令
```

## 配置格式

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "run_shell",
      "command": "python .claude/hooks/check_shell.py",
      "timeout_ms": 3000
    }],
    "PostToolUse": [{
      "matcher": "*",
      "command": "python .claude/hooks/audit.py"
    }]
  }
}
```

## Hook 输入输出

**输入**（通过 JSON 传给命令）：`event`、`session_id`、`cwd`、`prompt`、`tool_name`、`tool_input`、`tool_result`、`last_assistant_text`。

**输出**（命令 stdout 的 JSON）：
- `{"action": "allow"}` — 放行
- `{"action": "deny", "reason": "..."}` — 拒绝
- `{"action": "modify", "updated_input": {...}}` — 修改工具输入
- `{"action": "append_context", "content": "..."}` — 追加上下文

## 安全约束

**PreToolUse modify 后重新校验**：hook 修改工具输入后，`ToolRuntime` 对修改后的输入重新调用 `tool.validate()`。如果校验失败（如 hook 删除了必填字段），执行被阻断。

**modify + 权限检查**：修改后的输入仍然进入权限策略——hook 不能通过 modify 绕过权限。

**项目 hooks 默认不信任**：只有 `NANO_CODE_TRUST_PROJECT_HOOKS=1` 时才加载项目级 hooks。`~/.claude/settings.json` 始终加载。

## 在 AgentLoop 中的位置

```
AgentLoop.run():
    ⑦ _apply_user_prompt_hooks(msg)     ← UserPromptSubmit
    [主循环]
        backend.call()
        if 无 tool_calls:
            ⑨ _run_stop_hook(text)        ← Stop

ToolRuntime.execute_one():
    ③ hooks.run("PreToolUse", ...)       ← PreToolUse
    ...
    ⑧ hooks.run("PostToolUse", ...)      ← PostToolUse
```

## 面试考点

**Q: hook modify 后为什么需要重新校验？**

hook 是用户自定义脚本——可能写错、可能有 bug。如果 hook 返回了不合法的工具参数（如删除了必填字段），不重新校验就会把错误输入传给工具或模型。在 PreToolUse 阶段拦截是最佳时机。
