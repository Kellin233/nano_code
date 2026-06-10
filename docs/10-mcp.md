# MCP 集成

## 1. 为什么需要 MCP

内置工具不可能覆盖所有外部系统。MCP 让外部服务通过标准 JSON-RPC 协议暴露工具和资源给 Agent。

MCP 是应用层能力，位于 `cli/core/mcp/`。Agent core 不连接 MCP server；`AgentSession` 创建 `McpManager`，首次对话前加载并把 MCP 工具注册进 `ToolRegistry`。

## 2. 文件结构

```
cli/core/mcp/
├── __init__.py
├── types.py        # 配置、工具、资源等共享类型
├── config.py       # 多源配置加载、合并、环境变量展开
├── transport.py    # stdio 子进程 transport
├── connection.py   # 单 server initialize/list/call
├── manager.py      # 多 server 管理、工具命名、变更回调
└── output.py       # MCP 输出格式化和大结果处理
```

`resources.py` 已合并进 `types.py`/manager 相关逻辑，避免为少量类型和转发函数保留独立模块。

## 3. 协议栈

```
McpManager
  → McpConnection
  → StdioTransport
  → MCP server process
```

当前只支持 stdio transport。HTTP/SSE/WS 属于后续扩展。

## 4. 工具注册

MCP 工具命名为：

```
mcp__<server>__<tool>
```

注册流程：

```
McpManager.load_and_connect()
  → tools/list
  → 转成 ToolDef
  → ToolRegistry.add_many(origin="mcp", default_concurrency_safe=False)
```

MCP 工具默认 deferred，避免大量 schema 进入上下文。模型通过 `tool_search` 激活需要的 MCP 工具。

## 5. 工具变更

MCP server 发送 `notifications/tools/list_changed` 后，manager 做 debounce refresh，计算 added/removed/changed，然后通过 `on_tools_changed` 回调通知 `AgentSession`。

`AgentSession` 再更新 `ToolRegistry`，并通过 `render_mcp_delta_attachment()` 把变化作为动态附件注入对话。

## 6. 配置

配置来源：

- `~/.claude.json`
- `~/.claude/settings.json`
- `./.claude/settings.json`
- `./.mcp.json`

支持 `${VAR}` 和 `${VAR:-default}` 展开。项目级配置覆盖用户级配置。

## 7. 设计决策

### 为什么 MCP 不放在 tools.runtime

MCP 连接生命周期和工具执行管线是两个关注点。ToolRuntime 只负责通用执行流程；真正调用 MCP 工具时，ToolRegistry 通过 `ctx.mcp_manager` 路由。

### 为什么 MCP 工具默认 deferred

MCP server 可能提供几十上百个工具。一次性暴露全部 schema 会浪费 token，也会破坏工具缓存稳定性。deferred 让模型按需激活。

## 8. 代码导读

```
cli/core/mcp/config.py
cli/core/mcp/transport.py
cli/core/mcp/connection.py
cli/core/mcp/manager.py
cli/core/tools/registry.py
cli/session.py::_ensure_mcp_initialized
```
