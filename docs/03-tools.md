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
├── registry.py       # ToolRegistry：注册、查找、deferred 激活
└── runtime.py        # ToolRuntime：验证、hooks、权限、确认、执行、持久化
```

核心协议类型在 `agent/types.py`：

- `ToolDef`
- `ToolCall`
- `ToolResult`

`cli/core/tools/types.py` 只放工具系统自己的类型，例如 `ToolContext`、`FunctionTool`、`ToolMetadata`。

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
    read_file_state: dict[str, float]
    sandbox_manager: Any | None = None
    mcp_manager: Any | None = None
    agent: Any | None = None
```

特殊工具才使用 `agent` 弱引用：

- `agent` 工具委托给 `AgentSession._execute_agent_tool()`。
- `skill` 工具委托给 `AgentSession.invoke_skill()`。
- `tool_search` 激活 deferred 工具。

普通文件/搜索工具只依赖 `cwd` 和 `read_file_state`。

## 5. 执行管线

`ToolRuntime.execute_one(call, ctx)`：

```
1. ToolRegistry.find(name)
2. tool.validate(input, ctx)
3. Extension before_tool_call
4. PreToolUse hooks
5. hook modify 后重新 validate
6. check_permission(...)
7. confirm callback
8. tool.call(input, ctx)
9. _persist_large_result()
10. Extension after_tool_call
11. PostToolUse hooks append_context
```

工具错误以 `ToolResult(is_error=True)` 返回给模型，而不是让整个 AgentLoop 崩掉。系统级异常才会进入 `runtime.error`。

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
| `read_file` | 只读 | 支持 offset/limit，记录 mtime |
| `write_file` | 编辑 | 写入前检查先读后改 |
| `edit_file` | 编辑 | old_string 唯一匹配，写入前检查先读后改 |
| `list_files` | 只读 | glob 文件列表 |
| `grep_search` | 只读 | 系统 grep 优先，Python fallback |
| `run_shell` | shell | 必须通过 SandboxManager |
| `skill` | 编排 | 调用 SkillInvocation |
| `web_fetch` | 只读 | urllib 抓取并清理 HTML |
| `agent` | 编排 | 派发子 Agent |
| `tool_search` | 工具发现 | 搜索并激活 deferred 工具 |
| `list_mcp_resources` | MCP | 列出资源 |
| `read_mcp_resource` | MCP | 读取资源 |

## 8. ToolRegistry

工具来源包括：

- `builtin`
- `mcp`
- `custom`
- `extension`

MCP 工具默认 deferred，避免一次性把大量 schema 塞进上下文。模型通过 `tool_search` 激活需要的工具。Extension 注册的工具通过 `ExtensionAPI.register_tool()` 进入同一个 registry，后续执行路径和内置工具一致。

## 9. 大结果持久化

`ToolRuntime._persist_large_result()` 对超大结果做本地落盘：

```
{workspace}/.nanocode/sessions/{session_id}/tool-results/{call_id}.txt
```

消息历史中只保留 `<persisted-output>` 和约 2KB 预览。这样既不丢完整结果，也避免单个工具输出撑爆上下文。

## 10. 代码导读

阅读顺序：

```
cli/core/tools/types.py
cli/core/tools/registry.py
cli/core/tools/builtin.py
cli/core/tools/runtime.py
cli/session.py::_execute_tools
```
