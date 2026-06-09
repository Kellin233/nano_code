# Hooks：生命周期钩子

## 1. 为什么需要 Hooks

权限系统说 yes/no——但用户可能想说"yes，但要改参数"。或者"跑完了记个日志"。Hooks 在工具执行的四个关键节点插入用户自定义脚本——不是替代权限，是额外的拦截和修改通道。

## 2. 核心概念

### 2.1 四个事件

| 事件 | 时机 | 能做什么 |
|------|------|---------|
| UserPromptSubmit | 用户消息发出前 | deny/modify/append_context |
| PreToolUse | 工具校验后、权限前 | deny/modify |
| PostToolUse | 工具执行后 | append_context |
| Stop | 模型结束响应时 | deny（强制再跑一轮）/append_context |

### 2.2 modify 后的重新校验

PreToolUse hook 修改输入后，`ToolRuntime` 重新调用 `tool.validate()`。hook 可能写错、可能恶意——修改后的输入校验失败则阻断。修改后仍进入权限策略——hook 不能通过 modify 绕过 deny。

### 2.3 项目 hooks 默认不信任

`~/.claude/settings.json` 始终加载。项目级需要 `NANO_CODE_TRUST_PROJECT_HOOKS=1`——clone 的项目你未必信任。

## 3. 总体设计

```
capabilities/hooks/
├── types.py      # HookInput、HookOutput、HookCommand
├── config.py     # HookManager：配置加载 + 事件匹配 + 运行
└── runner.py     # run_command_hook()：进程执行
```

## 4. 详细设计

**`config.py`**：`HookManager.capture()` 加载配置。`run(event, hook_input)` 匹配 matcher，执行匹配的命令。

**`runner.py`**：`run_command_hook()` 用 `subprocess.run` 执行命令，超时 3000ms。命令 stdout 的 JSON 解析为 HookOutput。

## 5. 设计决策

### 为什么 modify 后重新校验

hook 是用户脚本——可能写错。返回不合法参数不重新校验就传给工具或模型。PreToolUse 阶段拦截成本最低。

### 为什么 Stop hook 能强制再跑一轮

模型结束响应但"方向对了但不够"——Stop hook 通过 deny+append_context 追加系统消息后，主循环继续。让用户在模型停止后"推一把"。

## 6. 面试考点

**Q: hook modify 后为什么不跳过权限？** 修改后的输入仍然进入权限策略——hook 不能绕过 deny 规则。hook 是用户自定义，权限是系统安全策略。两层独立。

## 7. 代码导读

**关键代码**：`config.py` HookManager.run()、`runner.py` run_command_hook() 超时处理。
