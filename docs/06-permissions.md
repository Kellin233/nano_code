# 权限与安全设计

## 目标

权限系统是工具执行前的安全门——判断能不能做、要不要用户确认、还是直接拒绝。它与 sandbox 是两层独立防线：权限管"能不能试"，sandbox 管"执行时的边界"。

## 代码流程

```
模型生成工具调用
    │
    ▼
ToolRuntime.execute_one()
    │
    ├── 1. 工具校验（Tool.validate）
    ├── 2. PreToolUse hooks
    ├── 3. check_permission(tool_name, inp, mode, metadata, cwd)  ← 权限入口
    │       │
    │       ├── check_path_policy(tool_name, inp, cwd)     # 路径边界
    │       │     → deny / confirm / allow
    │       │
    │       ├── rule_decision(tool_name, inp)               # 用户规则
    │       │     → deny / allow / None
    │       │
    │       ├── bypassPermissions？ → allow（跳过后续）
    │       ├── acceptEdits + edit_tool？ → allow
    │       ├── read_only tool？ → allow
    │       ├── run_shell？ → check_shell_safety(command)
    │       ├── write_file 新文件？ → confirm
    │       └── 默认 → allow
    │
    ├── 4. 如需确认 → 回调用户
    ├── 5. 工具执行
    └── 6. PostToolUse hooks
```

## 总体设计

### 四层检查

| 检查层 | 文件 | 能否被 bypassPermissions 绕过 |
|--------|------|:--:|
| 路径边界 | `workspace.py` | ❌ |
| 用户规则 | `rules.py` | ❌ deny 不可绕过 |
| 命令安全 | `shell.py` | ❌ deny 不可绕过 |
| 确认策略 | `policy.py` | ✅ |

关键设计：`--yolo` 跳过的是"确认"，不是"拒绝"。路径 deny 和用户 deny 规则在任何模式下都生效。

### 四种权限模式

| 模式 | CLI 触发 | 行为 |
|------|---------|------|
| `default` | 无标志 | 写文件/跑 shell 需确认，读文件自动允许 |
| `acceptEdits` | `--accept-edits` | 编辑文件自动允许，危险 shell 仍需确认 |
| `bypassPermissions` | `--yolo` | 跳过 confirm，不绕过 deny |
| `dontAsk` | `--dont-ask` | 所有 confirm 自动拒绝 |

### 与 Sandbox 的关系

权限和 sandbox 是独立维度。`--yolo` 关闭权限确认，不代表关闭 sandbox。`--sandbox microsandbox-safe` 也不会影响权限确认行为。

## 详细设计

### `workspace.py`——路径边界

`check_path_policy(tool_name, inp, cwd)` 检查文件路径：

- **protected 路径**：`.git`、`.env`、`id_rsa`、`.claude/settings.json`——写入/编辑 confirm，读取 confirm
- **workspace 外路径**：confirm
- 其他：allow

### `rules.py`——用户规则

从 `~/.claude/settings.json` 和 `./.claude/settings.json` 加载 allow/deny 列表。规则格式 `tool_name(pattern)`，支持通配符。先查 deny 后查 allow。全局缓存，`reset_permission_cache()` 清除。

### `shell.py`——Shell 命令安全

用正则匹配检测危险命令：`rm`、`sudo`、`mkfs`、`curl | sh`、`chmod -R 777` 等。返回 `safe`/`confirm`/`deny` 三级。复杂 shell 构造（反引号、`$()`、`eval`）标记为 confirm。

局限：正则匹配是浅层检测，不是 AST 解析。目标是防止无意危险操作，不是防御有意攻击。

### `policy.py`——统一策略入口

`check_permission(tool_name, inp, mode, metadata, cwd)` 按顺序执行：

1. 路径边界 → deny/confirm
2. 用户规则 deny → deny
3. bypassPermissions？ → allow
4. 用户规则 allow → allow
5. 只读工具 → allow
6. acceptEdits + 编辑工具 → allow
7. run_shell → 命令安全检查
8. 新建/编辑不存在的文件 → confirm
9. 默认 → allow

## 硬性约束

- 路径边界 deny 和用户规则 deny 不能被 bypassPermissions 绕过
- protected paths 包括 `.git`、`.env`、SSH key、`.claude/settings.json`
- Shell 安全检测是正则匹配，不要声称"完整命令注入防护"

## 隐含要求

- ToolMetadata 的 read_only/edit_tool 属性必须准确——标注错误导致权限策略失效
- workspace 边界以 cwd 为基准
- `--dont-ask` 模式下用户看不到拒绝详情——CLI 应给简短提示

## 不能做什么

- 不能把权限确认和 sandbox 混为一谈
- 不能宣传 bypassPermissions 为"安全关闭"
- 不能让 `--yolo` 绕过 deny 规则

## 可能踩坑的地方

### bypassPermissions 的语义

`--yolo` 只跳过确认环节。deny 规则和路径保护仍然生效。文档必须强调。

### 规则缓存的测试互扰

`load_permission_rules()` 使用全局缓存。测试中用不同规则时必须在 tearDown 中调用 `reset_permission_cache()`。

### Shell 检测的误报

正则可能误报——如命令中包含字符串 "rm" 但不真是删除操作。当前偏安全策略（宁可多确认），后续可能需要白名单机制。

### 路径解析的跨平台

`Path.expanduser()` 处理 `~`，`Path.resolve()` 处理符号链接。Windows 上 `~` 行为不同，指向 `C:\Users\用户名`。
