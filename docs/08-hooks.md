# Hooks：生命周期钩子

## 1. 为什么需要 Hooks

权限系统说 yes/no——但用户可能想说"yes，但要改参数"。或者"跑完了把结果记日志"。Hooks 在工具执行的四个关键节点插入用户自定义脚本——不是替代权限系统，而是给用户一个额外的拦截和修改通道。

和权限系统的本质区别：权限是"系统级安全策略"（不可绕过的 deny 规则），Hooks 是"用户自定义的额外逻辑"（用户选择配置、用户选择信任）。

## 2. 核心概念

### 2.1 四个事件

| 事件 | 触发时机 | 能做什么 | 谁触发 |
|------|---------|---------|--------|
| UserPromptSubmit | 用户消息即将发送给模型 | deny（阻止）、modify（改 prompt）、append_context（追加指令） | AgentLoop |
| PreToolUse | 工具校验后、权限检查前 | deny（阻止）、modify（改工具输入） | ToolRuntime |
| PostToolUse | 工具执行后 | append_context（追加系统消息） | ToolRuntime |
| Stop | 模型结束响应时 | deny（强制再跑一轮）、append_context（追加后续指令） | AgentLoop |

### 2.2 modify 后的重新校验（关键安全约束）

PreToolUse hook 修改工具输入后，`ToolRuntime` 对修改后的输入重新调用 `tool.validate()`。hook 是用户脚本——可能写错。如果返回了不合法的工具参数（如删除必填字段 file_path），不重新校验就把错误输入传给模型或工具。PreToolUse 阶段拦截成本最低。

修改后的输入仍然进入权限策略检查——hook 不能通过 modify 绕过 deny 规则。permission 是系统安全策略，hook 是用户自定义逻辑。两层独立。

### 2.3 项目 hooks 默认不信任

`~/.claude/settings.json` 的 hooks 始终加载（用户自己的配置）。项目级 `.claude/settings.json` 的 hooks 需要 `NANO_CODE_TRUST_PROJECT_HOOKS=1`。clone 的项目不一定信任——这是安全设计。

### 2.4 Hook 输入输出协议

输入通过 JSON 从 stdin 传给命令，包含 `event`、`session_id`、`cwd`、`prompt`、`tool_name`、`tool_input`、`tool_result`、`last_assistant_text`。输出从 stdout JSON 解析：`allow`（放行）、`deny` + `reason`（拒绝）、`modify` + `updated_input`（修改）、`append_context` + `content`（追加上下文）。

## 3. 总体设计

```
capabilities/hooks/
├── types.py      # HookInput、HookOutput、HookCommand、HookEventName
├── config.py     # HookManager：加载配置、事件匹配、调度执行
└── runner.py     # run_command_hook()：子进程执行 + 超时控制
```

## 4. 详细设计

**`config.py`**：`HookManager.capture()` 加载 `~/.claude/settings.json`。`HookManager.run(event, hook_input)` 遍历匹配的 hook→`run_command_hook()`。matcher 支持具体工具名和 `*` 通配。

**`runner.py`**：`run_command_hook()` 用 `subprocess.run` 执行命令。默认 3000ms 超时。命令 stdout JSON→解析为 `HookOutput`。超时或非零退出→根据 `fail_closed` 返回 deny 或 allow。

## 5. 设计决策

### 为什么 modify 后重新校验

hook 是用户脚本——可能写错。返回不合法参数不重校验就传给工具→执行时报错。PreToolUse 阶段拦截成本最低、信息量最大。

### 为什么项目 hooks 默认不信任

clone 的项目可能包含恶意 hooks 配置→窃取 API key 或修改命令。`NANO_CODE_TRUST_PROJECT_HOOKS=1` 是用户明确表示"我审查过这个项目的 hooks 配置"。

### 为什么 Stop hook 能强制再跑一轮

模型说"完成了"但用户觉得不够。Stop hook deny+append_context→追加提示后主循环继续。用户可以在模型停止后"推一把"。

## 6. 面试考点

**Q: hook modify 后为什么重新校验？** 用户脚本可能写错。不重校验→错误输入传给工具。PreToolUse 拦截成本最低。

**Q: hook 和权限什么关系？** 两层独立。权限是系统安全策略（deny 不可绕过），hook 是用户自定义逻辑。modify 后的输入仍进权限检查。

**Q: 为什么不信任项目 hooks？** 安全考量——clone 的项目可能包含恶意配置。显式信任机制。

## 7. 代码导读

**关键行号**：`config.py` HookManager.__init__() + run()、`runner.py` run_command_hook() 超时处理。
