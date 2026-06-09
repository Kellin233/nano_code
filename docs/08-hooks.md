# Hooks：生命周期钩子

## 为什么需要 Hooks

权限系统说"yes or no"，但用户可能想说"yes，但要改一下参数"。或者"这个工具跑完之后，把结果记个日志"。Hooks 在工具执行的四个关键节点插入用户自定义脚本——不是替代权限系统，而是给用户一个额外的拦截和修改通道。

## 核心概念

### 四个事件

| 事件 | 时机 | 能做什么 |
|------|------|---------|
| UserPromptSubmit | 用户消息发出前 | deny（阻止）、modify（改 prompt）、append_context（追加） |
| PreToolUse | 工具校验后、权限前 | deny（阻止）、modify（改参数） |
| PostToolUse | 工具执行后 | append_context（追加系统消息） |
| Stop | 模型结束响应时 | deny（强制再跑一轮） |

### modify 后的重新校验

PreToolUse hook 修改工具输入后，`ToolRuntime` 对修改后的输入重新调用 `tool.validate()`。这是关键安全约束——hook 可能写错、可能有 bug、可能恶意的。如果修改后的输入校验失败（如删除了必填字段），执行被阻断。

修改后的输入仍然进入权限策略——hook 不能通过 modify 绕过 deny 规则。

### 项目 hooks 默认不信任

`~/.claude/settings.json` 的 hooks 始终加载。项目级 `.claude/settings.json` 的 hooks 需要 `NANO_CODE_TRUST_PROJECT_HOOKS=1` 才加载——因为 clone 一个项目时，它的 hooks 配置你不一定信任。

## 设计决策

### 为什么 hooks 不替代权限系统

权限系统有不可绕过的 deny 层（路径边界 + 用户规则），hooks 的输出是可绕过的（用户可以选择不配 hook）。hooks 是"用户自定义的额外逻辑"，权限是"系统级的安全策略"。两者定位不同。

### 为什么 modify 后重新校验

hook 是用户脚本——可能写错。如果返回了不合法的工具参数，不重新校验就把错误输入传给工具或模型。在 PreToolUse 阶段拦截是最佳时机——比工具执行时报错更有信息量。

### 为什么 Stop hook 能强制再跑一轮

有时模型的回复"方向对了但不够"——`Stop` hook 通过 `deny` + `append_context` 追加一条系统消息后，主循环继续再跑一轮。这让用户可以在模型停止后"推一把"。

## 代码走读

**`types.py`**：`HookInput`（传给命令的 JSON）、`HookOutput`（命令 stdout 解析）、`HookCommand`（配置项）。

**`config.py`**：`HookManager.capture()` 加载 hooks 配置。`run(event, hook_input)` 匹配 matcher 并执行匹配的命令。

**`runner.py`**：`run_command_hook()` 用 `subprocess.run` 执行 hook 命令，超时控制（默认 3000ms）。

## 面试考点

**Q: hook modify 后为什么需要重新校验？**

hook 是用户脚本——可能写错。如果返回了不合法参数（删除必填字段），不重新校验就传给工具会报错或行为异常。PreToolUse 阶段拦截成本最低。
