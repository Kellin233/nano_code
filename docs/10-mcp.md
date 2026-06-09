# MCP 集成

## 为什么需要 MCP

内置工具有限——read_file、write_file、run_shell——但用户可能需要"查 GitHub Issues"、"读数据库"、"调 API"。不可能在 Agent 里内建所有工具。MCP（Model Context Protocol）让外部服务以标准协议暴露工具给 Agent。

## 核心概念

### 协议栈

```
McpManager（多 server 管理、工具聚合）
    ↓
McpConnection（单 server 生命周期、initialize、tools/call）
    ↓
StdioTransport（子进程 stdin/stdout JSON-RPC）
```

MCP server 是独立进程——通过 stdio 的 JSON-RPC 通信。当前只支持 stdio transport。http/sse/ws 配置可解析但不连接。

### 工具命名

MCP 工具注册到 ToolRegistry 时用 `mcp__server__tool` 命名——避免和内置工具冲突。sanitize 处理：非 `[A-Za-z0-9_-]` 字符替换为 `_`，连续 `_` 合并，太长截断加 hash。

### 工具变更通知

MCP server 可以通过 `notifications/tools/list_changed` 通知工具列表变化。`McpManager` 收到后标记 server 工具 dirty，debounced refresh（延迟 0.2s），计算 added/removed/changed，通过 callback 通知 Agent 更新 ToolRegistry。

## 设计决策

### 为什么 MCP 工具默认 deferred

MCP server 可能暴露几十上百个工具。全部加载到 system prompt 浪费 token。deferred 让工具默认不可见，模型通过 `tool_search` 按需激活。alwaysLoad 的 server 除外。

### 为什么只支持 stdio transport

Stdio 最可靠——启动子进程，读 stdin/stdout。HTTP/SSE/WebSocket 需要额外依赖（httpx、websockets）和处理重连、认证等复杂度。当前先做好一个 transport，以后按需扩展。

### 为什么 MCP 不放进 tools.runtime

MCP 连接生命周期（启动进程、握手、心跳）和工具执行管线是不同的关注点。tools.runtime 负责"执行工具"的通用流程（验证→权限→执行），MCP 工具执行时由 ToolRegistry 路由到 `ctx.mcp_manager.call_tool()`——MCP 模块只提供"怎么调 MCP 工具"，不参与执行管线。

## 代码走读

**`config.py`**：从 `~/.claude.json`、`settings.json`、`.mcp.json` 加载 server 配置，`${VAR}` 环境变量展开。

**`transport.py`**：stdio 子进程 + JSON-RPC request/response + stderr ring buffer。

**`connection.py`**：initialize 握手、tools/list、tools/call、通知处理。request id 竞态修复。

**`manager.py`**：多 server 管理、前缀命名、工具刷新回调、资源聚合。

**`output.py`**：结构化输出处理——文本/图片/blob/大结果落盘到 `~/.nanocode/mcp-outputs/`。

## 面试考点

**Q: MCP server 进程崩溃了怎么办？**

当前没有自动重连。`McpManager` 会标记连接断开，工具调用返回错误。未来可以加重连机制（roadmap 中）。
