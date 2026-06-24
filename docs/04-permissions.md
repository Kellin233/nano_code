# 权限与安全

## 1. 为什么需要权限系统

模型生成的工具调用可能危险，例如写 `.env`、改 `.git`、执行破坏性 shell。权限系统在工具执行前判断：允许、拒绝，还是请求用户确认。

权限位于 `agent/runtime_management/permissions/`，因为它是 Agent 运转的横切机制。真正触发权限检查的是应用层的 `ToolRuntime`，位于 `cli/core/tools/runtime.py`。

权限和 sandbox 是两层独立防线：

- 权限管“能不能试”，发生在执行前。
- sandbox 管“试的时候边界在哪”，只约束 `run_shell` 的执行环境。

`--yolo` 跳过普通确认，不代表关闭 sandbox，也不代表 deny 规则失效。

## 2. 文件结构

```
agent/runtime_management/permissions/
├── __init__.py
├── policy.py        # check_permission() 统一入口
├── tool_policy.py   # per-run allowed_tools 白名单
├── rules.py         # settings.json allow/deny 规则
├── workspace.py     # workspace 边界和 protected paths
└── shell.py         # 危险 shell 命令检测
```

## 3. 执行位置

```
cli/core/tools/runtime.py: ToolRuntime.execute_one()
    ├── check_tool_allowlist
    ├── validate
    ├── extension before_tool_call
    ├── PreToolUse hooks
    ├── check_permission(...)
    ├── confirm callback
    ├── tool.call(...)
    └── PostToolUse hooks
```

权限模块不 import ToolRuntime。ToolRuntime import 权限模块并调用它，符合“Application Layer 使用 Runtime Management”的依赖方向。

`allowed_tools` 是第一道运行级工具白名单。它不是用户确认策略，而是 Benchmark、server 或 CLI 为本次运行声明的硬边界：

- 工具不在白名单中时直接返回 `Action denied`。
- 这一步发生在 schema 校验和权限判断之前。
- 后续 `check_permission()` 仍会继续处理路径、用户规则、shell 风险和确认。

权限系统可以看成四层：

| 层 | 入口 | 目的 | 是否可被 yolo 绕过 |
|----|------|------|--------------------|
| 工具 allowlist | `check_tool_allowlist()` | 限制本次 run 能用哪些工具 | 否 |
| 路径策略 | `check_path_policy()` | 阻止 workspace 外写入，保护敏感路径 | workspace 外写入不可绕过；protected path 仍需确认 |
| 用户规则 | `rule_decision()` | 执行 settings allow/deny | deny 不可绕过 |
| 默认策略 | `check_permission()` mode 分支 | 处理只读、编辑、shell 风险和确认 | yolo 只跳过普通确认 |

## 4. 权限模式

| 模式 | CLI 触发 | 读工具 | 已有 workspace 文件编辑/写入 | 新文件写入 | safe run_shell | risky run_shell | protected path |
|------|---------|:--:|:--:|:--:|:--:|:--:|:--:|
| `default` | 无标志 | 自动 | 确认 | 确认 | 自动 | 确认 | 确认 |
| `acceptEdits` | `--accept-edits` | 自动 | 自动 | 自动 | 自动 | 确认 | 确认 |
| `bypassPermissions` | `--yolo` | 自动 | 自动 | 自动 | 自动 | 自动 | 确认 |
| `dontAsk` | `--dont-ask` | 自动 | 拒绝 | 拒绝 | 自动 | 拒绝 | 拒绝 |

硬线：

- 用户 deny 规则不可被 `--yolo` 绕过。
- 受保护路径不会因为 `--yolo` 自动降级。
- workspace 外写入始终拒绝，即使在 `--yolo` 下也不会执行。
- 权限检查必须在工具执行前完成。
- 当前实现会对默认模式下的 `write_file` / `edit_file` 请求确认，即使目标是已有 workspace 文件。`acceptEdits` 和 `bypassPermissions` 会自动批准普通编辑；`dontAsk` 会自动拒绝需要确认的编辑。

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
    ├── 7. 新文件写入、编辑不存在文件 confirm
    ├── 8. 默认 write_file / edit_file confirm
    └── 9. 默认 allow
```

路径检查返回的 `reason` 会区分：

- `protected`：`.git`、`.env`、SSH key、settings 等敏感路径。
- `workspace_boundary`：workspace 外路径。

workspace 外写入在 path policy 中直接拒绝。workspace 外读取会确认；`--yolo` 下可自动允许这类读取，让 OS 报错后模型自纠错。protected path 在 `--yolo` 下仍返回确认，不会被普通自动批准绕过。

当前代码中的 protected path 判断覆盖：

- `.git`
- `.mcp.json`
- `.env`、`.env.*`
- `id_rsa`、`id_ed25519`、`known_hosts`、`authorized_keys`
- workspace 下 `.claude/settings.json`

`protected` 和 `workspace_boundary` 的语义不同：

- `workspace_boundary` 主要防止误写/越界。写入类工具在这里硬拒绝；读取类工具可以确认，因为用户可能确实想让模型看外部文件。
- `protected` 主要防止泄密或破坏配置。`.env`、SSH key、`.git`、settings 即使在 workspace 内也更敏感；`bypassPermissions` 不会自动批准 protected path。

这也是为什么 Benchmark 里 `path_escape_denied_recovery` 和 `permission_yolo_protected_path_blocked` 是两类不同测试。

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

匹配细节：

- `run_shell(pattern)` 匹配命令字符串；pattern 以 `*` 结尾时做前缀匹配。
- 文件工具匹配 `file_path`，相对路径会按 cwd 解析，支持末尾 `*` 前缀匹配。
- MCP 工具规则支持 `mcp__server` 前缀匹配一组工具，也支持完整 `mcp__server__tool`。
- deny 先于 allow，表示用户的永久禁止规则优先级最高。

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

### 为什么权限放在 Runtime Management

权限不是某个具体工具的能力，而是所有工具执行前的横切机制。放在 Runtime Management 后，ToolRuntime 可以调用它，Agent Core 不需要知道权限系统存在。

### 为什么权限和 sandbox 不合并

权限回答“是否允许尝试”，sandbox 回答“执行时能碰哪里”。它们的触发时机、数据输入和失败模式不同，合并会让模块职责变混。

### 为什么 deny 规则不可被 yolo 绕过

`--yolo` 表示跳过临时确认，不表示忽略用户永久策略。用户写 deny 规则就是声明“这类操作永远不要做”。

## 9. 边界与失败恢复

权限拒绝不是 runtime fatal error。ToolRuntime 会把拒绝包装成 `ToolResult(is_error=True)`，模型可以继续：

- 外部路径写入被拒后，改写 workspace 内目标。
- 非唯一 patch 被 edit_file 拒绝后，先 read_file 定位更精确 old_string。
- shell deny rule 命中后，跳过命令并继续做文件修改。
- `dontAsk` 自动拒绝确认后，CI/benchmark 可以验证不会发生交互式阻塞。

这也是权限系统放在工具执行前的原因：危险动作未执行，但错误仍进入对话，模型有机会恢复。

## 10. Benchmark 覆盖

`benchmarks/local-fixture` 的 security、permissions 和 tool-boundary case 共同覆盖权限合同：

- `security_approval_denied_shell`：`.claude/settings.json` deny rule 阻断 shell。
- `security_read_only_write`：deny rule 阻断指定文件写入。
- `path_escape_denied_recovery`：workspace 外写入被拒绝后恢复到 workspace 内目标。
- `security_patch_nonunique` / `security_patch_missing_new_text`：工具参数和编辑约束错误被记录为工具错误。

Benchmark 同时区分 `allowed_tools_respected` 和 `allowed_tools_enforced`：模型请求了不允许的工具会降低工具选择纪律指标，但只要 runtime 拦截成功，最终任务仍可通过。

## 11. 代码导读

```
agent/runtime_management/permissions/tool_policy.py
agent/runtime_management/permissions/workspace.py
agent/runtime_management/permissions/rules.py
agent/runtime_management/permissions/shell.py
agent/runtime_management/permissions/policy.py
cli/core/tools/runtime.py
```
