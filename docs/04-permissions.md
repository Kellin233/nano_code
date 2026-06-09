# 权限与安全

## 1. 为什么需要权限系统

模型生成 `run_shell("rm -rf /")` 怎么办？权限系统是工具执行前的安全门——判断能不能做、要不要确认、还是直接拒绝。

**和 Sandbox 是两层独立防线**：权限管"能不能试"（执行前），sandbox 管"试的时候边界在哪"（执行中）。`--yolo` 关闭权限确认不代表关闭 sandbox。`--sandbox microsandbox-safe` 不影响权限确认。

## 2. 核心概念

### 2.1 四层检查，两层不可绕过

```
check_permission(tool_name, inp, mode)
    ├── 1. 路径边界（workspace.py）     ← 不可绕过
    │      protected paths + workspace 外
    ├── 2. 用户规则（rules.py）         ← deny 不可绕过
    │      settings.json allow/deny
    ├── 3. bypassPermissions？→ allow  ← 跳过后续
    └── 4. 模式策略（policy.py）
           read_only→allow / acceptEdits+edit→allow
           run_shell→危险检测 / 新文件→confirm
```

### 2.2 四种权限模式

| 模式 | CLI | 读 | 编辑 | 危险 shell |
|------|-----|:--:|:--:|:--:|
| default | 无 | 自动 | 确认 | 确认 |
| acceptEdits | --accept-edits | 自动 | 自动 | 确认 |
| bypassPermissions | --yolo | 自动 | 自动 | 自动 |
| dontAsk | --dont-ask | 自动 | 拒绝 | 拒绝 |

## 3. 总体设计

```
capabilities/permissions/
├── policy.py       # check_permission() 统一入口
├── rules.py        # settings.json allow/deny 规则加载
├── workspace.py    # protected paths + workspace 边界
└── shell.py        # 20 个危险命令正则 + 3 个复杂 shell 正则
```

## 4. 详细设计

**`workspace.py`**：`check_path_policy()` 检查两条规则。Protected 路径（`.git`、`.env`、`id_rsa`、`.claude/settings.json`）：写入/编辑 → confirm，读取 → confirm。Workspace 外路径：confirm。

**`rules.py`**：从 `~/.claude/settings.json` 和 `./.claude/settings.json` 加载。规则格式 `tool_name(pattern)`——`run_shell(rm*)` 匹配以 rm 开头的 shell 命令。全局缓存，`reset_permission_cache()` 清除。

**`shell.py`**：`DANGEROUS_PATTERNS` 20 个正则（rm、sudo、mkfs、dd、kill、reboot、curl|sh、chmod -R 777 等）。`COMPLEX_SHELL_PATTERNS` 3 个正则（反引号、`$()`、eval）。`check_shell_safety()` 返回 safe/confirm/deny。

**`policy.py`**：`check_permission()` 按顺序调用上述模块，然后根据 permission_mode 判断。路径 deny 和规则 deny 在任何模式下都返回。

## 5. 设计决策

### 为什么 deny 不可绕过

用户写 `deny: ["run_shell(rm*)"]` 是声明"永远不想执行这类命令"。`--yolo` 的意思是"我相信模型"——信任不应覆盖永久的"不信任"。确认模式和 deny 规则是独立维度。

### 为什么 Shell 检查用正则

20 个正则覆盖常见危险模式。不是 AST 解析——目标是防止无意危险操作而非防御有意攻击。正则成本低、可解释。

## 6. 面试考点

**Q: `--yolo` 跳过所有检查吗？** 不。路径边界 deny 和用户 deny 规则在任何模式生效。yolo 只跳过 confirm。

**Q: 权限和 sandbox 什么关系？** 独立维度。权限管执行前，sandbox 管执行中。互不替代。

## 7. 代码导读

**阅读顺序**：`workspace.py`→`rules.py`→`shell.py`→`policy.py`（最后看统一入口）。关键代码：`policy.py:21-82` check_permission 完整流程。
