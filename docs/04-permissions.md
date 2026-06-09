# 权限与安全

## 为什么需要权限系统

模型生成 `run_shell("rm -rf /")` 怎么办？权限系统是工具执行前的安全门——判断能不能做、要不要确认、还是直接拒绝。和 sandbox 是两层独立防线：权限管"能不能试"，sandbox 管"试的时候边界在哪"。

## 核心概念

### 四层检查，有不可绕过的

```
check_permission(tool_name, inp, mode)
    ├── 1. 路径边界（workspace.py）     ← 不可绕过
    │      protected paths / workspace 外 → confirm/deny
    ├── 2. 用户规则（rules.py）         ← deny 不可绕过
    │      settings.json 的 allow/deny
    ├── 3. bypassPermissions？ → allow  ← 跳过后续
    └── 4. 模式策略（policy.py）
           read_only → allow / acceptEdits+edit → allow
           run_shell → 危险检测 / 新文件 → confirm
```

`--yolo` 只跳过 confirm，不跳过 deny。路径 deny 和用户 deny 在任何模式下都生效。

### 四种权限模式

| 模式 | CLI | 读文件 | 编辑 | 危险 shell |
|------|-----|:--:|:--:|:--:|
| default | 无 | 自动 | 确认 | 确认 |
| acceptEdits | --accept-edits | 自动 | 自动 | 确认 |
| bypassPermissions | --yolo | 自动 | 自动 | 自动 |
| dontAsk | --dont-ask | 自动 | 拒绝 | 拒绝 |

## 设计决策

### 为什么 deny 规则不可被 bypassPermissions 绕过

用户写 `deny: ["run_shell(rm*)"]` 是声明"我永远不想让模型执行这类命令"。`--yolo` 的意思是"我相信模型"，但信任不覆盖永久的"不信任"。两者是独立维度——确认是"信任模型"的开关，deny 是"不信任操作"的声明。

### 为什么 Shell 检查用正则而非 AST

20 个正则匹配危险模式。浅层检测，可解释，成本低。当前目标不是防御有意攻击，而是防止无意危险操作。用 AST 解析 shell 命令不仅实现复杂，而且对不同 shell（bash/zsh/fish/sh）的语法差异会误报。

### 为什么路径检查在最前面

路径是"物理"限制——读 workspace 外的文件、写 `.git`。这些不应依赖用户配置。放在规则之前是因为：纯字符串比较，不需要读文件；deny 不需要用户在 settings.json 里声明。

## 代码走读

**`workspace.py`**：`check_path_policy()` 检查 protected 路径（`.git`、`.env`、SSH key）和 workspace 边界。写入/编辑 protected 路径 → confirm。

**`rules.py`**：从 `~/.claude/settings.json` 加载 allow/deny 规则。格式 `tool_name(pattern)`。全局缓存，`reset_permission_cache()` 清除。

**`shell.py`**：20 个 `DANGEROUS_PATTERNS` 正则 + 3 个 `COMPLEX_SHELL_PATTERNS`。返回 safe/confirm/deny 三级。

**`policy.py`**：`check_permission()` 统一入口，按顺序调用上述三模块。

## 面试考点

**Q: `--yolo` 真的跳过所有检查吗？**

不。路径边界 deny 和用户 deny 规则在任何模式下都生效。`--yolo` 只跳过 confirm。settings.json 里写了 `deny: ["run_shell(rm*)"]` 的话，yolo 模式下 `rm -rf /` 仍被拒。

**Q: 权限和 sandbox 是什么关系？**

独立维度。权限管执行前能不能试，sandbox 管执行中边界在哪。`--yolo` 关权限确认不影响 sandbox。
