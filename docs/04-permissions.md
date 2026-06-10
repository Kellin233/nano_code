# 权限与安全

## 1. 为什么需要权限系统

模型生成的工具调用可能危险，例如写 `.env`、改 `.git`、执行破坏性 shell。权限系统在工具执行前判断：允许、拒绝，还是请求用户确认。

权限位于 `agent/harness/permissions/`，因为它是 Agent 运转的横切机制。真正触发权限检查的是应用层的 `ToolRuntime`，位于 `cli/core/tools/runtime.py`。

权限和 sandbox 是两层独立防线：

- 权限管“能不能试”，发生在执行前。
- sandbox 管“试的时候边界在哪”，只约束 `run_shell` 的执行环境。

`--yolo` 跳过普通确认，不代表关闭 sandbox，也不代表 deny 规则失效。

## 2. 文件结构

```
agent/harness/permissions/
├── __init__.py
├── policy.py        # check_permission() 统一入口
├── rules.py         # settings.json allow/deny 规则
├── workspace.py     # workspace 边界和 protected paths
└── shell.py         # 危险 shell 命令检测
```

## 3. 执行位置

```
cli/core/tools/runtime.py: ToolRuntime.execute_one()
    ├── validate
    ├── extension before_tool_call
    ├── PreToolUse hooks
    ├── check_permission(...)
    ├── confirm callback
    ├── tool.call(...)
    └── PostToolUse hooks
```

权限模块不 import ToolRuntime。ToolRuntime import 权限模块并调用它，符合“应用层使用 harness”的依赖方向。

## 4. 权限模式

| 模式 | CLI 触发 | 读工具 | 编辑工具 | run_shell | 新文件 |
|------|---------|:--:|:--:|:--:|:--:|
| `default` | 无标志 | 自动 | 确认 | 确认 | 确认 |
| `acceptEdits` | `--accept-edits` | 自动 | 自动 | 确认 | 确认 |
| `bypassPermissions` | `--yolo` | 自动 | 自动 | 自动 | 自动 |
| `dontAsk` | `--dont-ask` | 自动 | 拒绝 | 拒绝 | 拒绝 |

硬线：

- 用户 deny 规则不可被 `--yolo` 绕过。
- 受保护路径不会因为 `--yolo` 自动降级。
- 权限检查必须在工具执行前完成。

## 5. 检查顺序

```
check_permission(tool_name, input, mode)
    │
    ├── 1. workspace / protected path 检查
    ├── 2. settings.json deny / allow 规则
    ├── 3. bypassPermissions 特殊处理
    ├── 4. 只读工具自动 allow
    ├── 5. acceptEdits + edit 工具自动 allow
    ├── 6. run_shell 危险命令检测
    ├── 7. 新文件或编辑不存在文件 confirm
    └── 8. 默认 allow
```

路径检查返回的 `reason` 会区分：

- `protected`：`.git`、`.env`、SSH key、settings 等敏感路径。
- `workspace_boundary`：workspace 外路径。

`--yolo` 下，workspace boundary 可以放行，让 OS 报错后模型自纠错；protected path 仍要确认。

## 6. 用户规则

读取位置：

- `~/.claude/settings.json`
- `./.claude/settings.json`

格式：

```json
{
  "permissions": {
    "allow": ["run_shell(echo*)", "read_file(*.py)"],
    "deny": ["run_shell(rm*)", "write_file(.env*)"]
  }
}
```

规则格式是 `tool_name(pattern)`。`deny` 优先于 `allow`。规则有缓存，测试或热更新时可调用 `reset_permission_cache()`。

## 7. Shell 安全

`shell.py` 用正则检测常见危险命令：

- `rm`、`sudo`、`mkfs`、`dd`
- `kill`、`pkill`
- `reboot`、`shutdown`
- `curl | sh`、`wget | sh`
- `chmod -R 777`、`chown -R`
- `find ... -delete`
- 反引号、`$()`、`eval`

这不是完整攻击防护，而是防止模型无意执行高风险命令。真正的执行边界仍由 sandbox 提供。

## 8. 设计决策

### 为什么权限放在 harness

权限不是某个具体工具的能力，而是所有工具执行前的横切机制。放在 harness 后，ToolRuntime 可以调用它，Agent core 不需要知道权限系统存在。

### 为什么权限和 sandbox 不合并

权限回答“是否允许尝试”，sandbox 回答“执行时能碰哪里”。它们的触发时机、数据输入和失败模式不同，合并会让模块职责变混。

### 为什么 deny 规则不可被 yolo 绕过

`--yolo` 表示跳过临时确认，不表示忽略用户永久策略。用户写 deny 规则就是声明“这类操作永远不要做”。

## 9. 代码导读

```
agent/harness/permissions/workspace.py
agent/harness/permissions/rules.py
agent/harness/permissions/shell.py
agent/harness/permissions/policy.py
cli/core/tools/runtime.py
```
