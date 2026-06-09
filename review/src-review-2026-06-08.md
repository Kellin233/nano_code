# src 审查与优化报告 —— 2026-06-08

## 一、代码结构摘要

- **范围**：`src/` 下 117 个 Python 文件，覆盖 agent 运行时、工具系统、权限、hooks、sandbox、MCP、skills、memory、TUI、session 等模块。
- **测试基线**（修复前）：161 个测试全部通过（139 个主测试 + 22 个 v1 测试）。
- **现有 review 材料**：`review/hook.md`（未跟踪旧文件），本次报告不覆盖。
- **主要发现**：3 个安全/正确性问题，均已修复并验证。

---

## 二、审查发现与已实施修复

### 2.1 [严重] `run_shell` 无 sandbox/backend 时回退到裸 `subprocess.run(shell=True)`

**文件**：[src/domains/tools/builtin.py:197-217](src/domains/tools/builtin.py#L197-L217)、[src/domains/tools/registry.py:101-109](src/domains/tools/registry.py#L101-L109)、[src/domains/tools/runtime.py:217-228](src/domains/tools/runtime.py#L217-L228)

**问题**：`run_shell` 函数在 `builtin.py` 中使用 `subprocess.run(inp["command"], shell=True)` 执行任意命令。该函数在两个地方可能被直接调用：

1. **registry 路径** (`_call_builtin`)：如果 `ctx.sandbox_manager is None`，直接调用裸 `run_shell(inp)`。
2. **runtime 路径** (`execute_builtin_tool`)：如果 `execution_backend is None`，落到 `BUILTIN_HANDLERS["run_shell"]`，即裸 `run_shell`。

这意味着如果调用方没有正确传入 sandbox/backend，shell 命令将以 `shell=True` 在宿主机上执行，绕过了所有沙箱隔离。

**修复**：

1. `registry.py`：`ctx.sandbox_manager is None` 时不再调用裸 `run_shell`，返回明确错误：
   ```
   Error: run_shell requires a sandbox manager. No sandbox backend is configured for this session.
   ```
2. `runtime.py`：`execution_backend is None` 时返回明确错误：
   ```
   Error: run_shell requires an execution backend. No sandbox is configured.
   ```
3. 主路径继续走 `SandboxManager.run_shell()` 注入 backend，行为不变。

**保留的非 shell 执行路径**（不是 `run_shell` 工具路径，不在此次修复范围）：
- `grep_search` → `subprocess.run(["grep", ...])` — 非 `shell=True`，参数固定。
- git context → `subprocess.run(["git", ...])` — 只读操作，非用户可控。
- TUI editor → `subprocess.run([editor, tmp_path])` — 固定参数。

### 2.2 [高] PreToolUse hook 修改输入后不重新校验

**文件**：[src/domains/tools/runtime.py:107-112](src/domains/tools/runtime.py#L107-L112)

**问题**：`execute_one()` 在 hook 运行前对原始输入做了 `tool.validate()`，但当 hook 返回 `action: "modify"` 并替换 `inp` 后，修改后的输入**没有重新校验**。恶意或配置错误的 hook 可以：

- 移除必填字段（如 `file_path`），导致工具以不完整参数执行。
- 修改参数类型，导致下游 `builtin` 函数异常。
- 注入不符合 schema 的参数值。

**修复**：每次 `modify` 后立即对修改后的 `inp` 执行 `tool.validate(inp, ctx)`。校验失败则返回错误并阻断执行：
```
Error: hook-modified input failed validation: missing required field: {field}
```

### 2.3 [中] Sub-agent/skill fork 强制 `bypassPermissions`

**文件**：[src/runtime/agent/tools_runtime.py:143](src/runtime/agent/tools_runtime.py#L143)、[src/runtime/agent/tools_runtime.py:181](src/runtime/agent/tools_runtime.py#L181)

**问题**：`_run_fork_skill()` 和 `_execute_agent_tool()` 创建子 Agent 时硬编码 `permission_mode="bypassPermissions"`。这意味着：

- 父 Agent 配置为 `default` 模式（需要确认）时，子 Agent 静默跳过所有确认。
- 父 Agent 配置为 `dontAsk` 模式时，子 Agent 反而升级权限（从 "拒绝危险操作" 变成 "全部放行"）。
- 违反最小权限原则：子任务不应该拥有比父任务更高的权限。

**修复**：将 `permission_mode="bypassPermissions"` 改为 `permission_mode=self.permission_mode`，让子 Agent 继承父 Agent 的权限模式。sandbox manager 继续复用父级 `self._sandbox_manager`，保持隔离边界一致。

**兼容性影响**：
- 之前依赖子 Agent 自动跳过权限确认的用户，需要显式设置 `--yolo`（即父 Agent 使用 `bypassPermissions`）。
- 这是**正确的**行为变更：权限继承比静默提升权限更安全。

---

## 三、测试清单与结果

### 3.1 基线测试

| 测试类别 | 命令 | 结果 |
|---------|------|------|
| 编译检查 | `python -m compileall src test` | ✅ 全部通过 |
| 主测试套件 | `python -m unittest discover -s test -v` | ✅ 139/139 通过 |
| V1 测试套件 | `python -m unittest discover -s test/v1 -v` | ✅ 22/22 通过 |

### 3.2 新增测试（`test/test_src_review_2026_06_08.py`）

#### 修复 1：run_shell 无 sandbox 拒绝执行（6 个测试）

| 测试名 | 场景 | 结果 |
|-------|------|------|
| `test_run_shell_without_sandbox_manager_returns_error` | ToolRuntime 路径：无 sandbox → 错误 | ✅ |
| `test_run_shell_with_sandbox_manager_works_normally` | 正常场景：有 sandbox → 正常执行 | ✅ |
| `test_execute_builtin_tool_run_shell_without_backend_returns_error` | runtime 路径：无 backend → 错误 | ✅ |
| `test_execute_builtin_tool_run_shell_with_backend_works_normally` | 正常场景：有 backend → 正常执行 | ✅ |
| `test_other_tools_still_work_without_sandbox` | 非 shell 工具：无 sandbox 仍可用 | ✅ |
| `test_invalid_timeout_with_backend_still_returns_error` | 边界：invalid timeout → 正确报错 | ✅ |

#### 修复 2：PreToolUse hook 重校验（4 个测试）

| 测试名 | 场景 | 结果 |
|-------|------|------|
| `test_hook_modified_input_is_revalidated_missing_required_field` | hook 移除必填字段 → 校验失败 | ✅ |
| `test_hook_modified_input_passes_revalidation_and_executes` | hook 合法修改 → 通过并执行 | ✅ |
| `test_hook_modified_input_still_goes_through_permission_check` | hook 修改后仍进入权限策略 | ✅ |
| `test_multiple_hooks_each_modified_input_is_revalidated` | 多个 hook 依次修改 → 各自校验 | ✅ |

#### 修复 3：Sub-agent 权限继承（6 个测试）

| 测试名 | 场景 | 结果 |
|-------|------|------|
| `test_sub_agent_inherits_parent_default_mode` | 子 Agent 继承 `default` | ✅ |
| `test_sub_agent_inherits_parent_accept_edits_mode` | 子 Agent 继承 `acceptEdits` | ✅ |
| `test_sub_agent_inherits_parent_bypass_mode` | 子 Agent 继承 `bypassPermissions` | ✅ |
| `test_sub_agent_does_not_force_bypass` | 子 Agent 不提升为 bypass | ✅ |
| `test_sub_agent_sandbox_manager_shared_with_parent` | sandbox manager 复用父级 | ✅ |
| `test_tool_runtime_uses_inherited_permission_for_sub_agent_context` | ToolRuntime 使用继承权限 | ✅ |

### 3.3 回归测试

| 测试类别 | 结果 |
|---------|------|
| 主测试套件（含新增） | ✅ 155/155 通过 |
| V1 测试套件 | ✅ 22/22 通过 |
| 编译检查 | ✅ clean |
| **总计** | ✅ **177/177 通过** |

所有原有测试（MCP、memory、skills、session、protocol、TUI）保持通过。

---

## 四、修改文件清单

| 文件 | 修改内容 |
|------|---------|
| [src/domains/tools/registry.py](src/domains/tools/registry.py#L101-L109) | `_call_builtin` 中 `run_shell` 无 sandbox 时返回错误 |
| [src/domains/tools/runtime.py](src/domains/tools/runtime.py#L217-L228) | `execute_builtin_tool` 中 `run_shell` 无 backend 时返回错误 |
| [src/domains/tools/runtime.py](src/domains/tools/runtime.py#L107-L112) | PreToolUse hook `modify` 后重新执行 `tool.validate()` |
| [src/runtime/agent/tools_runtime.py](src/runtime/agent/tools_runtime.py#L143) | `_run_fork_skill` 的 `permission_mode` 改为 `self.permission_mode` |
| [src/runtime/agent/tools_runtime.py](src/runtime/agent/tools_runtime.py#L181) | `_execute_agent_tool` 的 `permission_mode` 改为 `self.permission_mode` |
| [test/test_src_review_2026_06_08.py](test/test_src_review_2026_06_08.py) | 新增 16 个测试，覆盖三个修复点 |

---

## 五、剩余风险

1. **`grep_search` 使用 `subprocess.run` 但非 `shell=True`**：`grep_search` 通过参数列表调用 `grep` 命令，不使用 `shell=True`，不会被 shell 注入攻击。但该路径在无 sandbox 环境下仍会调用宿主机命令，如果外部代码直接使用 `grep_search` 并通过 `include` 参数传入用户可控值，可能引发参数注入。当前 `grep_search` 只在工具注册表中被内置工具路径调用，且参数由模型生成（非外部用户直接输入），风险较低。

2. **直接调用 `builtin.run_shell` 的外部代码**：`builtin.py` 中的 `run_shell` 函数仍是公开的。如果外部代码绕过 `ToolRuntime` 或 `_call_builtin` 直接调用它，命令会裸执行。建议所有 shell 执行路径都通过 `SandboxManager` 或 `execution_backend`。

3. **Sub-agent 权限继承的兼容性**：之前依赖子 Agent 自动跳过权限检查的用户，需要升级后显式使用 `--yolo` 模式。这是故意为之的安全加固。

4. **沙箱配置缺失场景**：尽管我们修复了 fallback 路径，但如果 sandbox manager 被错误配置（例如 backend 为空字符串或未知值），`_build_backend` 会抛出 `ValueError`，这个异常在 `_ensure_started` 中会被转换为 `RuntimeError`，在 `run_shell` 中被捕获并返回 "Error: ..." 字符串。这个行为是正确的 fail-closed 策略。

---

## 六、结论

本次审查在 `src` 中发现了 3 个问题，按严重程度分别为：1 个严重（裸 shell fallback）、1 个高（hook 输入未重校验）、1 个中（子 Agent 权限提升）。所有问题均已修复，并编写了 16 个新增测试覆盖三个修复点。全量测试（177 个，含原有 161 + 新增 16 = 177）全部通过，无回归。

报告文件不覆盖现有 `review/hook.md`，也无意删除它。
