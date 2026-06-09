# 工具系统

## 为什么需要工具系统

LLM 只能生成文本。让它"读文件"、"搜索代码"、"跑命令"——这些不是文本生成能做的事。它需要工具。工具系统就是 Agent 的"手"——把模型的文本意图翻译成真实的文件读写、命令执行、网络请求。

设计核心理念：**普通工具无状态**。一个 `read_file` 工具不应该持有"上次读了哪个文件"的状态——那是 Agent 会话的事。工具本身是纯函数——输入参数，输出结果。运行时上下文（cwd、session_id、sandbox）通过 `ToolContext` 注入。

## 核心概念

### 三层模型

```
Schema 层（builtin.py）：声明工具叫什么、参数是什么
    ↓
Registry 层（registry.py）：注册、查找、deferred 激活
    ↓
Runtime 层（runtime.py）：执行管线（验证→权限→Hook→执行→后处理）
```

这三层有独立的变更原因。改一个工具的 schema（加参数）不改 registry。改 deferred 激活策略不改 runtime。改执行管线（并发策略）不改 schema。

### ToolContext：运行时信息的注入点

工具不应该持有 Agent 引用——那会让工具和 Agent 循环耦合。`ToolContext` 是工具执行时拿到的上下文对象：

```python
@dataclass
class ToolContext:
    cwd: Path                      # 当前工作目录
    session_id: str                # 会话 ID
    read_file_state: dict          # 文件→mtime（先读后改）
    sandbox_manager: Any | None    # run_shell 走这里
    mcp_manager: Any | None        # MCP 工具走这里
    agent: Any | None              # agent/skill 工具走这里（弱引用）
```

普通工具（read_file、grep_search）只用到 `cwd` 和 `read_file_state`。特殊工具（run_shell 走 sandbox_manager，MCP 走 mcp_manager，agent 走 agent）通过 ToolContext 拿到自己需要的能力模块。

### 执行管线

一个工具调用经过 8 步：`ToolRegistry.find`（找工具）→ `tool.validate`（参数校验）→ PreToolUse hooks（可拒绝/修改）→ `check_permission`（权限）→ confirm（用户确认）→ `tool.call`（执行）→ `_persist_large_result`（>30KB 落盘）→ PostToolUse hooks。

每一步都可能打断执行——找不到工具返回错误，校验失败返回错误，hook deny 返回错误，权限 deny 返回错误，用户拒绝返回错误。打断不抛异常——都是返回 `ToolResult(is_error=True)`。

## 设计决策

### 为什么 schema 和实现在同一个文件（builtin.py）

原来 `definitions.py`（schema）和 `builtin.py`（实现）是分开的。但加一个新工具必须同时改两个文件——它们共享同一个变更原因（"我想加一个工具"）。合并后加工具只改 `builtin.py`。

### 为什么 deferred 工具存在

MCP 工具可能有几十上百个——全部加载会撑爆 system prompt。deferred 机制让工具默认不暴露给模型，模型通过 `tool_search` 按需激活。这是一个"上下文预算管理"功能伪装成了"工具系统功能"。

### 为什么并发安全的工具只有 4 个

`read_file`、`list_files`、`grep_search`、`web_fetch` 可以并行——它们只读不写。`write_file` 和 `edit_file` 不能并行——同时写同一个文件会冲突。分类在 `CONCURRENCY_SAFE_BUILTIN_TOOLS` 常量中，`ToolRuntime.execute_many()` 据此分组 batch。

### 为什么先读后改

模型不应该在没见过文件内容的情况下修改它。`read_file_state` 字典记录每个被读取的文件的 mtime。`write_file` 和 `edit_file` 执行前检查——文件是否被读过？mtime 是否被外部修改？两者有一个不满足就返回错误。这是一个不变量，不是可选项。

## 代码走读

**`types.py`**：所有工具相关类型 + 常量。合并了原来的 types.py + base.py + constants.py。三个文件加起来才 ~120 行——分开只会增加跳转成本。

**`builtin.py`**：12 个内置工具的 JSON Schema + Python 实现。代理工具（agent、skill、tool_search）只声明 schema，执行委托给 `ToolContext.agent` 对应方法。`run_shell` 的实现函数保留但 `BUILTIN_HANDLERS` 字典不引用它——所有 shell 执行路径必须显式传入 sandbox backend。

**`registry.py`**：`ToolRegistry` 内部两套数据——`_tools`（FunctionTool 实例）和 `_metadata`（ToolMetadata）。`_build_tool` 工厂函数根据 `origin`（builtin/mcp/custom）构建不同的执行路径。deferred 工具通过 `_activated_deferred` 集合跟踪激活状态。

**`runtime.py`**：`execute_many()` 按并发安全性分组 batch——安全工具 `asyncio.gather`，不安全工具串行。`execute_one()` 是完整的 8 步管线。`execute_builtin_tool()` 是旧代码的兼容路径——不走完整管线，手动传参数。

## 面试考点

**Q: 为什么 tool_search 存在？为什么不一次性加载所有工具？**

MCP 可能有几十上百个工具。全部加载会撑爆 system prompt 和 Anthropic 的工具缓存。deferred 机制让模型按需激活——这是一个上下文预算管理功能。

**Q: 先读后改不变量怎么实现？**

`read_file_state: dict[str, float]` 记录每个被读文件的 mtime。`write_file`/`edit_file` 前检查文件是否在 dict 中且 mtime 匹配。不在 → "请先 read_file"。mtime 变了 → "文件被外部修改，请重新 read_file"。
