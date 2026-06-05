# Plan Mode 删除方案

## 结论

只删除“用户可进入的 Plan Mode 工作流”，不动 `subagent.py` 里的 `plan` 子 agent。

`plan` 子 agent 只是一个只读分析 agent，不等同于全局 Plan Mode。保留它成本最低，也不影响后续扩展 multi-agent。

这次删除的目标不是去掉“规划能力”，而是去掉 Plan Mode 作为全局运行状态带来的特殊分支。

## 为什么删除当前 Plan Mode

当前 Plan Mode 更像 Claude Code 的交互流程复刻，而不是 agent runtime 的核心能力。它横跨 CLI、REPL、工具定义、权限系统、Agent 状态、UI 审批和文档，增加了不少特殊逻辑。

后续项目主线更适合聚焦在：

- sandbox executor：安全执行 shell 和工具调用
- structured memory：结构化长期记忆和检索
- hook system：工具调用前后、用户输入、agent 停止时的生命周期扩展点
- tool registry / provider：统一接入内置工具、skills、MCP 等能力来源

因此删除的是：

```text
Plan Mode as stateful workflow
```

保留的是：

```text
planning as capability
```

后续 planning 可以通过 `plan` 子 agent、skill、hook 或 task tracker 重新实现，而不是继续作为全局模式散落在主循环里。

## 要删除的功能边界

删除这些：

- CLI 参数：`--plan`
- REPL 命令：`/plan`
- 工具：`enter_plan_mode`、`exit_plan_mode`
- Agent 内部 Plan Mode 状态和审批流程
- UI 里的 plan approval 输出函数
- prompt 里和 Plan Mode 相关的入口提示
- README/docs 中暴露给用户的 Plan Mode 用法

保留这些：

- `subagent.py` 里的 `plan` 子 agent
- 权限系统里的只读能力思想
- `acceptEdits`、`dontAsk`、`bypassPermissions` 等其他权限模式
- context compact、memory、skills、多 agent、MCP 暂时不动

## 具体改动点

### 1. `mini_claude/__main__.py`

删除：

- `parser.add_argument("--plan", ...)`
- `_resolve_permission_mode()` 里的 `if args.plan: return "plan"`
- `agent.set_plan_approval_fn(...)`
- `plan_approval_fn()`
- REPL 里的 `/plan` 分支
- help 文案里的 `--plan`、`/plan`、示例 `mini-claude --plan ...`

这样用户就无法从 CLI 或 REPL 进入 Plan Mode。

### 2. `mini_claude/tools.py`

删除工具定义：

- `enter_plan_mode`
- `exit_plan_mode`

同时删除 `get_deferred_tool_names()` 间接暴露这两个工具的可能性。因为它们从 `tool_definitions` 中删除后，就不会再作为 deferred tools 出现。

权限部分采用最小风险处理：

- `PermissionMode` 注释中移除 `"plan"`
- `check_permission()` 中删除 `mode == "plan"` 分支
- 第一轮先保留 `plan_file_path` 参数但不使用，减少调用点改动

后续做权限系统重构时，再统一删除 `plan_file_path` 参数。

### 3. `mini_claude/agent.py`

删除 Plan Mode 状态：

- `_pre_plan_mode`
- `_plan_file_path`
- `_plan_approval_fn`
- `_context_cleared` 中只服务 Plan Mode 的逻辑

删除方法：

- `set_plan_approval_fn`
- `toggle_plan_mode`
- `_generate_plan_file_path`
- `_build_plan_mode_prompt`
- `_execute_plan_mode_tool`
- `_clear_history_keep_system`，如果它只被 Plan Mode 使用

修改工具执行：

- `_execute_tool_call()` 中删除 `enter_plan_mode` / `exit_plan_mode` 分支
- `check_permission(..., self._plan_file_path)` 改成 `check_permission(..., self.permission_mode)`，或者在第一轮保留参数时传 `None`

修改子 agent / skill fork：

当前逻辑：

```python
permission_mode="plan" if self.permission_mode == "plan" else "bypassPermissions"
```

改成：

```python
permission_mode="bypassPermissions"
```

原因：用户级 Plan Mode 已删除，子 agent 的只读约束已经由 `custom_tools` 白名单控制。

### 4. `mini_claude/ui.py`

删除：

- `print_plan_for_approval`
- `print_plan_approval_options`

同时更新欢迎语：

- 删除 `/plan`

### 5. `mini_claude/prompt.py`

修改帮助提示：

- 删除 REPL 命令列表中的 `/plan`

如果系统提示中还有 Plan Mode 相关文字，也同步删掉。但不要大改 prompt。

### 6. README 和 docs

README 中删除：

- `mini-claude --plan`
- `/plan`
- Plan Mode 核心能力描述
- docs 列表里的 `docs/10-plan-mode.md`

文档处理有两种选择：

- 最小代码改动：先只更新 README，不删 docs。
- 项目清爽：删除或标记 `docs/10-plan-mode.md` 为 deprecated，并修正 `_sidebar.md`。

第一轮建议只改 README 和 sidebar，不急着大删教程文档，避免文档引用连锁变动。

## 实现顺序

按这个顺序改，风险最低：

```text
先删入口：CLI / REPL / tool definitions
再删 Agent 状态和方法
再删 UI 辅助函数
最后清 README/help 文案
```

每一步都可以编译验证，避免一次性删除过多造成问题难定位。

## 验证方式

改完后运行：

```bash
python -m compileall mini_claude
mini-claude --help
rg -n "plan mode|Plan Mode|--plan|/plan|enter_plan_mode|exit_plan_mode|set_plan_approval|toggle_plan_mode" mini_claude README.md docs
```

预期：

- `mini_claude/` 中不再有 Plan Mode 入口和执行逻辑
- `--help` 不显示 `--plan`
- REPL 欢迎语不显示 `/plan`
- tool schema 不再暴露 `enter_plan_mode` / `exit_plan_mode`
- `subagent.py` 里的 `plan` 可以保留，因为这是计划型子 agent，不是 Plan Mode

## 后续替代方向

后续如果还需要“先规划再执行”的能力，可以用更轻量、更可组合的方式实现：

- 用 `plan` 子 agent 做只读分析
- 用 planning skill 提供用户主动触发的规划工作流
- 用 task tracker 或 `TodoWrite` 风格工具管理执行步骤
- 用 `PreToolUse` hook 在高风险操作前要求生成 action summary
- 用权限策略创建只读会话，而不是继续维护 Plan Mode 特殊分支

这样可以保留 planning 的价值，同时让 agent runtime 的主线更清晰。
