# 工具系统

## 1. 为什么需要工具系统

工具系统把模型的意图翻译成真实动作：读文件、搜索代码、编辑文件、执行 shell、抓取网页、调用 skill、派发子 Agent、访问 MCP。

当前设计中，工具是应用层能力，位于 `cli/core/tools/`。Agent core 不知道有哪些工具，也不创建 ToolRuntime。`cli/session.py` 创建 `ToolRegistry` 和 `ToolRuntime`，再把工具执行函数注入 `AgentLoop`。

## 2. 文件结构

```
cli/core/tools/
├── __init__.py
├── types.py          # ToolContext、FunctionTool、ToolMetadata、常量
├── builtin.py        # 12 个内置工具 schema + 实现
├── recent_files.py   # context compact 后恢复最近文件状态
├── registry.py       # ToolRegistry：注册、查找、deferred 激活
└── runtime.py        # ToolRuntime：验证、hooks、权限、确认、执行、持久化
```

核心协议类型在 `agent/types.py`：

- `ToolDef`
- `ToolCall`
- `ToolResult`

`cli/core/tools/types.py` 只放工具系统自己的类型，例如 `ToolContext`、`FunctionTool`、`ToolMetadata`。

核心对象职责：

| 对象 | 定义位置 | 职责 |
|------|----------|------|
| `ToolDef` | `agent/types.py` | 模型可见 schema，provider 会把它转换成各自 wire format |
| `ToolCall` | `agent/types.py` | provider 解析出的调用请求，包含 id、name、input、provider |
| `ToolResult` | `agent/types.py` | 工具执行结果，包含 content、is_error、metadata、extra_messages |
| `ToolContext` | `cli/core/tools/types.py` | 工具执行时的应用上下文：cwd、sandbox、MCP、skill/subagent 委托 |
| `ToolMetadata` | `cli/core/tools/types.py` | registry 保存的执行元信息：origin、deferred、read_only、edit_tool、concurrency_safe |
| `FunctionTool` | `cli/core/tools/types.py` | 把 Python callable 包成统一 Tool 协议 |

## 3. 三层模型

```
builtin.py
  声明工具 schema 和内置实现
        │
        ▼
registry.py
  管理 builtin / mcp / custom / extension 工具
        │
        ▼
runtime.py
  统一执行管线
```

这三层的变更原因不同：加工具主要改 `builtin.py`，改工具发现策略改 `registry.py`，改执行顺序和安全策略改 `runtime.py`。

## 4. ToolContext

工具不直接依赖 Agent core。运行上下文通过 `ToolContext` 注入：

```python
@dataclass
class ToolContext:
    cwd: Path
    session_id: str
    sandbox_manager: Any | None = None
    mcp_manager: Any | None = None
    execute_agent_tool: Callable[[dict], Awaitable[str]] | None = None
    execute_skill_tool: Callable[[dict], Awaitable[str]] | None = None
    execute_tool_search: Callable[[dict], str] | None = None
```

特殊工具通过窄 callable 回到 `AgentSession`：

- `agent` 工具委托给 `AgentSession._execute_agent_tool()`。
- `skill` 工具委托给 `AgentSession.invoke_skill()`。
- `tool_search` 激活 deferred 工具。

普通文件/搜索工具只依赖 `cwd`。`run_shell` 必须通过 `sandbox_manager`，MCP 工具通过 `mcp_manager`。工具层不保存 Agent 消息历史、token 统计或 provider 客户端。

## 5. 执行管线

`ToolRuntime.execute_one(call, ctx)`：

```
1. check_tool_allowlist(name, allowed_tools)
2. ToolRegistry.find(name)
3. tool.validate(input, ctx)
4. Extension before_tool_call
5. PreToolUse hooks
6. hook modify 后重新 validate
7. check_permission(...)
8. confirm callback
9. tool.call(input, ctx)
10. _persist_large_result()
11. RecentFileTracker.record_tool_call()
12. Extension after_tool_call
13. PostToolUse hooks append_context
```

工具错误以 `ToolResult(is_error=True)` 返回给模型，而不是让整个 AgentLoop 崩掉。系统级异常才会进入 `runtime.error`。

每一步的失败边界：

| 阶段 | 失败时行为 |
|------|------------|
| allowlist | 返回 `Action denied`，metadata error_code 为 `action_denied` |
| registry find | 返回 `Unknown tool` |
| validate | 返回 `Error: <schema/required field message>` |
| PreToolUse deny | 返回 `Action denied by hook` |
| PreToolUse modify 后校验失败 | 返回 hook-modified validation error |
| permission deny | 返回 `Action denied`，保留 policy error_code |
| confirm denied | 返回 `User denied this action.` |
| tool.call 异常 | `FunctionTool.call()` 包成 `Error executing tool ...` |
| PostToolUse append_context | 不改工具 content，只把额外 user context 附到下一轮 |

这个设计让模型可以从工具错误里恢复，例如重新读取文件、改用唯一 patch、或在权限被拒后选择 workspace 内目标。

## 6. 并发安全

`ToolRuntime.execute_many()` 按工具的 `concurrency_safe` 元数据分 batch。连续的只读工具可以并行，编辑和 shell 类工具串行执行。

默认并发安全的内置工具包括：

- `read_file`
- `list_files`
- `grep_search`
- `web_fetch`
- MCP resource 读取类工具

`write_file`、`edit_file`、`run_shell` 不并发。

## 7. 内置工具

| 工具 | 分类 | 说明 |
|------|------|------|
| `read_file` | 只读 | 支持 offset/limit，返回带行号内容 |
| `write_file` | 编辑 | 原子文本替换，自动创建父目录，写 memory topic 时同步索引 |
| `edit_file` | 编辑 | old_string 唯一匹配，支持 Unicode 引号归一化，原子写入 |
| `list_files` | 只读 | glob 文件列表 |
| `grep_search` | 只读 | 系统 grep 优先，Python fallback |
| `run_shell` | shell | 必须通过 SandboxManager |
| `skill` | 编排 | 调用 SkillInvocation |
| `web_fetch` | 只读 | urllib 抓取并清理 HTML |
| `agent` | 编排 | 派发子 Agent |
| `tool_search` | 工具发现 | 搜索并激活 deferred 工具 |
| `list_mcp_resources` | MCP | 列出资源 |
| `read_mcp_resource` | MCP | 读取资源 |

从安全和执行角度也可以这样分类：

| 分类 | 工具 | 特点 |
|------|------|------|
| 可并发只读 | `read_file`、`list_files`、`grep_search`、`web_fetch`、MCP resource 工具 | 不修改 workspace，ToolRuntime 可放入同一并发 batch |
| 编辑 | `write_file`、`edit_file` | 需要路径策略、权限确认、原子写入，执行后可进入 RecentFileTracker |
| shell | `run_shell` | 必须经过权限和 SandboxManager，不允许裸 subprocess fallback |
| 编排 | `agent`、`skill`、`tool_search` | 通过 `ToolContext` 委托回 AgentSession，不直接操作 Agent core |
| 外部服务 | `mcp__<server>__<tool>` | 由 MCP manager 路由，默认 deferred，默认不并发 |

## 8. ToolRegistry

工具来源包括：

- `builtin`
- `mcp`
- `custom`
- `extension`

MCP 工具默认 deferred，避免一次性把大量 schema 塞进上下文。模型通过 `tool_search` 激活需要的工具。Extension 注册的工具通过 `ExtensionAPI.register_tool()` 进入同一个 registry，后续执行路径和内置工具一致。

`ToolRegistry.active_definitions()` 同时接收两类限制：

- active skill 的 `disallowed_tools()`：从模型可见 schema 中移除被禁工具。
- runtime/task 的 `allowed_tools`：只暴露本次运行允许的工具。

`ToolRuntime` 会再次执行 `allowed_tools` 检查，因此即使模型请求了不可见或不允许的工具，执行层也会拒绝。

Deferred 工具的生命周期：

```text
MCP 或 extension 注册 tool(deferred=true)
  → ToolRegistry 保存，但 active_definitions 不暴露
  → render_deferred_tools_attachment 提示可搜索名称
  → 模型调用 tool_search(query)
  → search_deferred 匹配并激活
  → 下一次 provider call schema 中出现该工具
  → ToolRuntime 执行时仍检查 allowed_tools
```

active skill 的 `disallowed_tools()` 会让工具从 schema 中消失；`allowed_tools()` 会和 runtime/task allowlist 取交集。这种双层限制保证“模型看不到”和“执行层拒绝”同时成立。

## 9. 设计决策

### 为什么 registry 和 runtime 分开

`ToolRegistry` 只回答“有哪些工具、哪些可见、如何查找/激活 deferred 工具”。`ToolRuntime` 只回答“一个工具调用如何安全执行”。这样新增工具来源不会改执行管线，调整权限或 hook 顺序也不会改工具发现逻辑。

### 为什么工具通过 ToolContext 取能力

工具实现不持有 `AgentSession`，只从 `ToolContext` 取得 cwd、sandbox、MCP manager 和少量委托 callable。这样文件工具、MCP 工具、子 Agent、skill 都能复用同一执行管线，又不会让工具层反向依赖 session。

### 为什么大结果在 ToolRuntime 统一处理

所有工具结果最终都会经过 `ToolRuntime`，在这里做落盘可以保证内置工具、MCP 工具和 extension 工具遵守同一上下文预算合同。

## 10. 大结果持久化

`ToolRuntime._persist_large_result()` 对超大结果做本地落盘：

```
{workspace}/.nanocode/artifacts/tool-results/{call_id}.txt
```

消息历史中只保留 `<persisted-output>` 和约 2KB 预览，并在 `ToolResult.metadata` 中保留 `persisted`、`artifact_path`、`full_result_path`、`original_size`、`preview_chars`、`threshold_chars`、`tool_name` 和 `sha256`。这样既不丢完整结果，也避免单个工具输出撑爆上下文。

## 11. RecentFileTracker

`recent_files.py` 不是工具本身，而是 compact 恢复辅助模块。ToolRuntime 在成功执行文件相关工具后记录路径：

- `read_file`：记录读取路径。
- `write_file` / `edit_file`：记录修改路径。

它当前只跟踪 `read_file`、`write_file`、`edit_file`。`grep_search` 和 `list_files` 的搜索范围不会进入 RecentFileTracker，因为 compact 后恢复需要的是“当前文件内容”，不是历史搜索范围。

Context Compact 成功后，`AgentSession._build_post_compact_context()` 会调用 `RecentFileTracker.build_context()`。它重新从当前磁盘读取少量最近相关文件，注入“当前文件状态”，避免 compact 摘要只保留过期片段。

## 12. Benchmark 覆盖

`benchmarks/local-fixture` 重点验证工具系统这些合同：

- 精确编辑：`sample_beta_locked`、`tool_duplicate_second_beta`、`large_file_targeted_edit`。
- Python 修复和本地检查：`python_slugify`、`test_driven_fix`、`recovery_config_check`。
- 工具错误恢复：`invalid_edit_recovery`、`trace_error_recovery`。
- allowlist 执行边界：所有配置了 `allowed_tools` 的任务都会检查 disallowed tool 是否成功执行。
- 大结果落盘：`context_large_result_persist` 检查 `payload.metadata.persisted == true`。

## 13. 代码导读

阅读顺序：

```
cli/core/tools/types.py
cli/core/tools/registry.py
cli/core/tools/builtin.py
cli/core/tools/runtime.py
cli/core/tools/recent_files.py
cli/session.py::_execute_tools
```
