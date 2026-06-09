# 权限与安全

## 1. 为什么需要权限系统

模型生成的工具调用可能危险——`run_shell("rm -rf /")`、`write_file("~/.ssh/id_rsa", malicious)`。权限系统在工具执行前判断：能不能做、要不要让用户确认、还是直接拒绝。

**权限和 Sandbox 是两层独立防线**。权限管"能不能试"（执行前），sandbox 管"试的时候边界在哪"（执行中）。`--yolo` 跳过权限确认不代表关闭 sandbox。`--sandbox microsandbox-safe` 不影响权限确认行为。两个维度独立组合。

设计上有两条硬线。**第一条硬线**：路径边界 deny 和用户规则 deny 在任何权限模式下都生效——包括 `--yolo`。**第二条硬线**：权限检查必须在工具执行之前完成——不能"先执行再补确认"。

## 2. 核心概念

### 2.1 四层检查，两条硬线

```
check_permission(tool_name, inp, mode)
    │
    ├── 1. check_path_policy(tool_name, inp, cwd)
    │      protected paths（.git/.env/id_rsa）→ confirm
    │      workspace 外路径 → confirm
    │      ├── 如果是 deny → 返回 deny（不可绕过）
    │
    ├── 2. rule_decision(tool_name, inp)
    │      从 settings.json 加载 allow/deny 规则
    │      先查 deny → 匹配就拒绝
    │      再查 allow → 匹配就放行
    │      ├── deny 规则不可绕过
    │
    ├── 3. bypassPermissions？
    │      是 → 返回 allow（跳过后续）
    │
    └── 4. 模式策略
           read_only 工具 → allow
           acceptEdits + edit 工具 → allow
           run_shell → check_shell_safety() → deny/confirm
           新文件/编辑不存在文件 → confirm
           默认 → allow
```

第 1、2 层是硬线——返回 deny 则整个检查结束。`bypassPermissions` 在第 3 层才检查——它在前两层之后。这意味着路径保护（`.git` 不可写）和用户 deny 规则（`rm*` 不可执行）在任何模式下都生效。

### 2.2 四种权限模式

| 模式 | CLI 触发 | 读工具 | 编辑工具 | run_shell | 新文件 |
|------|---------|:--:|:--:|:--:|:--:|
| default | 无标志 | 自动 | 确认 | 确认（危险检测） | 确认 |
| acceptEdits | --accept-edits | 自动 | 自动 | 确认 | 确认 |
| bypassPermissions | --yolo | 自动 | 自动 | 自动 | 自动 |
| dontAsk | --dont-ask | 自动 | 拒绝 | 拒绝 | 拒绝 |

注意：`--yolo` 不跳过路径边界和 deny 规则。即使 yolo 模式下，写 `.git` 目录也会被 confirm（路径边界），执行 `rm -rf /` 也会被 deny（用户规则匹配 `rm*`）。

### 2.3 用户规则格式

`~/.claude/settings.json` 和 `./.claude/settings.json` 中的 `permissions` 对象：

```json
{"permissions": {"allow": ["run_shell(echo*)", "read_file(*.py)"],
                  "deny":  ["run_shell(rm*)", "write_file(.env*)"]}}
```

规则格式 `tool_name(pattern)`。`run_shell` 的 pattern 匹配命令，文件工具的 pattern 匹配 `file_path`。MCP 工具用 `mcp__server` 前缀匹配整个 server。`*` 后缀做前缀匹配。

规则有全局缓存——`load_permission_rules()` 只在首次调用时读文件。`reset_permission_cache()` 清除缓存（测试中用于隔离）。

## 3. 总体设计

```
capabilities/permissions/
├── __init__.py       # 导出 check_permission + reset_permission_cache
├── policy.py         # check_permission() 统一入口（~80 行）
├── rules.py          # settings.json 加载 + 规则匹配（~85 行）
├── workspace.py      # protected paths + workspace 边界（~75 行）
└── shell.py          # 危险命令 + 复杂 shell 检测（~65 行）
```

### 3.1 与沙箱的关系

权限管"能不能试"（执行前），sandbox 管"试的时候边界在哪"（执行中）。两者互不替代：

```python
# 在 ToolRuntime.execute_one 中的位置
check_permission(...)       # ← 权限：这一步判断能不能执行
    ↓
confirm callback(...)       # ← 确认：用户点 yes/no
    ↓
tool.call(inp, ctx)         # ← 执行：run_shell 走 SandboxManager
    │                           #   sandbox 限制实际能碰的文件/网络
    └── SandboxManager.run_shell(...)
```

## 4. 详细设计

### 4.1 workspace.py——路径边界

`check_path_policy(tool_name, inp, cwd)` 检查两条规则：

**Protected 路径**：`.git`（整个目录）、`.env`、`.env.*`、`id_rsa`、`id_ed25519`、`known_hosts`、`authorized_keys`、`.claude/settings.json`。写入/编辑 protected 路径 → confirm。读取 protected 路径 → confirm。

**Workspace 外路径**：不在当前工作区内的任何路径 → confirm。路径解析通过 `Path.resolve()` 处理符号链接（不会通过 `..` 绕过）。

### 4.2 rules.py——用户规则

`load_permission_rules()` 合并 `~/.claude/settings.json` 和 `./.claude/settings.json` 的 permissions。`_parse_rule(rule_str)` 用正则 `tool_name(pattern)` 提取。

`matches_rule(rule, tool_name, inp)` 检查 tool_name 是否匹配（完整匹配或 mcp__server 前缀匹配），如果 rule 有 pattern，再检查 command/file_path 是否匹配（后缀 `*` 做前缀匹配，否则精确匹配）。

**缓存策略**：全局 `_cached_rules`。修改 settings.json 后需重启或调用 `reset_permission_cache()` 才生效。

### 4.3 shell.py——Shell 安全

**DANGEROUS_PATTERNS**（20 个正则）：`rm`、`sudo`、`mkfs`、`dd`、`kill`/`pkill`、`reboot`/`shutdown`、`curl|sh`/`wget|sh`（下载并执行）、`chmod -R 777`、`chown -R`、`find ... -delete`。每个正则匹配 → `confirm`。

**COMPLEX_SHELL_PATTERNS**（3 个正则）：反引号命令替换、`$()` 命令替换、`eval`。匹配 → `confirm`。

`check_shell_safety(command)` 返回 `ShellSafetyResult(level="safe"|"confirm"|"deny")`。当前所有危险命令都是 confirm 级别——没有 deny 级别的 shell 检测（deny 留给用户规则）。

### 4.4 policy.py——统一入口

`check_permission(tool_name, inp, mode, metadata=None, cwd=None)` 是权限检查的总入口。按顺序：路径边界 → 用户规则 → bypassPermissions？→ 只读工具自动 allow → acceptEdits+编辑工具自动 allow → run_shell 危险检测 → 新文件/编辑不存在文件 confirm → 默认 allow。

`mode` 参数控制四种权限模式。`metadata` 参数（`ToolMetadata`）提供工具分类信息（read_only、edit_tool）——如果提供了则使用 metadata，否则 fallback 到 `READ_TOOL_NAMES`/`EDIT_TOOL_NAMES` 常量。

## 5. 设计决策

### 决策 1：为什么 deny 规则不可被 bypassPermissions 绕过

**问题**：用户写了 `deny: ["run_shell(rm*)"]` 来声明"永远不想执行这类命令"。"yolo" 的意思是"我相信模型这次没问题"——但信任不应覆盖永久的"不信任"。确认模式和 deny 规则是独立维度。

**选择**：deny 规则在 bypassPermissions 之前检查。路径边界 deny 也在之前。

**为什么**：如果 yolo 能跳过 deny，用户就需要在每次 yolo 时担心"这次的模型会不会刚好调用那个被我 deny 的命令"——这就失去了 deny 规则的意义。

### 决策 2：为什么 Shell 检查用正则而非 AST 解析

**问题**：20 个正则只能做浅层检测。聪明的模型可以用 base64 编码或间接方式绕过。

**选择**：正则检测。当前目标不是防御有意的攻击，而是防止无意的危险操作。正则成本低、可解释、用户能看懂哪条命令触发了确认。

**代价**：一个真正恶意的模型（或 prompt injection 攻击）可以绕过。但对于日常使用场景——用户和模型协作写代码——正则足够。

### 决策 3：为什么路径检查在规则检查之前

**问题**：规则匹配可能依赖文件内容（如文件是否在 workspace 内），要不要在规则之后做路径检查？

**选择**：路径检查在最前面。路径是"物理"限制——读 workspace 外的文件、写 `.git` 目录。这些不依赖用户配置——是内置的安全边界。放在前面是因为路径检查更快（纯字符串比较，不需要读文件），而且路径 deny 不需要用户在 settings.json 里声明。

### 决策 4：为什么权限确认和 sandbox 不合并

**问题**：两者都是"安全相关"，要不要合并成一个模块？

**选择**：不合并。权限在 `ToolRuntime` 中，sandbox 在 `SandboxManager` 中，各自独立。权限在工具执行前判断，sandbox 在工具执行中限制。合并会让一个模块承担两种职责——"判断能不能做"和"限制怎么做"逻辑不同、变更原因不同。

## 6. 面试考点

### Q1: --yolo 真的跳过所有检查吗？

不。路径边界 deny 和用户规则 deny 在任何模式下都生效。yolo 只跳过 confirm——"需要用户点 yes 的环节"。写 `.git` 目录即使 yolo 也会被 confirm。settings.json 的 `deny: ["run_shell(rm*)"]` 即使 yolo 也会拒绝 `rm -rf /`。

### Q2: 权限和 sandbox 什么关系？

两层独立防线。权限管执行前能不能试，sandbox 管执行中边界在哪。互不替代。--yolo 不影响 sandbox，--sandbox microsandbox-safe 不影响权限确认。

### Q3: 为什么 shell 检查用正则而不是 AST？

20 个正则覆盖常见危险模式。AST 解析 shell 命令在不同 shell (bash/zsh/fish/sh) 间有语法差异——误报率高。当前目标是防止无意危险操作而非防御有意攻击。正则成本低、可解释、足够。

### Q4: 怎么加一个新的 deny 规则？

编辑 `~/.claude/settings.json` 的 `permissions.deny` 数组，格式 `tool_name(pattern)`。`pattern` 可选——不加 pattern 匹配该工具的所有调用。`*` 后缀做前缀通配符。规则全局缓存——修改后需重启或调用 `reset_permission_cache()` 生效。

### Q5: protected paths 为什么包含 .git？

`.git` 包含仓库的完整历史。通过 shell 命令直接修改 `.git` 可以绕过所有版本控制——git 历史被污染后无法恢复。这是比"不小心删了个文件"更严重的风险。读 `.git` 也要 confirm——虽然只读，但 `.git` 中的信息（如 `.git/config` 中的 token）可能是敏感的。

## 7. 代码导读

**阅读顺序**：`workspace.py`（最简单的检查）→ `rules.py`（用户规则）→ `shell.py`（危险检测）→ `policy.py`（统一入口，最后看）。

**关键行号**：
- `workspace.py:19-26`——PROTECTED_NAMES 集合
- `workspace.py:59-77`——check_path_policy() 完整逻辑
- `rules.py:13-17`——_parse_rule() 正则解析
- `rules.py:79-87`——rule_decision() 先查 deny 再 allow
- `shell.py:20-41`——DANGEROUS_PATTERNS 20 个正则
- `shell.py:51-58`——check_shell_safety() 检测逻辑
- `policy.py:21-82`——check_permission() 完整 9 步流程
