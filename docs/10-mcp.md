# MCP 集成

## 1. 为什么需要 MCP

内置工具有限——不能"查 GitHub Issues"、"读数据库"。MCP（Model Context Protocol）让外部服务以标准协议暴露工具给 Agent。server 是独立进程，通过 stdio JSON-RPC 通信。

## 2. 核心概念

### 2.1 协议栈

`McpManager`（多 server 管理）→ `McpConnection`（单 server 生命周期）→ `StdioTransport`（子进程 stdin/stdout）。

### 2.2 工具注册

MCP 工具用 `mcp__server__tool` 命名注册到 ToolRegistry。默认 deferred——模型通过 `tool_search` 按需激活。`notifications/tools/list_changed` 触发 debounced refresh。

## 3. 总体设计

```
capabilities/mcp/
├── config.py       # 配置加载（.claude.json/settings.json/.mcp.json）
├── transport.py    # stdio 子进程 + JSON-RPC
├── connection.py   # 单 server 生命周期（initialize/tools/call）
├── manager.py      # 多 server 管理 + 工具聚合 + 回调
├── output.py       # 结果结构化处理（文本/图片/blob）
└── resources.py    # resources/list + resources/read
```

## 4. 详细设计

**`config.py`**：从 `~/.claude.json`、`settings.json`、`.mcp.json` 加载。`${VAR}` 和 `${VAR:-default}` 展开。优先级：项目覆盖用户。

**`transport.py`**：`StdioTransport` 管理子进程生命周期。stderr ring buffer（最多 200 行）用于诊断。

**`connection.py`**：initialize 握手、tools/list、tools/call。request id 竞态修复——先注册 future 再发送请求。关闭顺序：cancel reader→close stdin→terminate→wait→kill。

**`manager.py`**：`McpManager` 管理多 server。`_make_prefixed_name()` sanitize 命名。`load_and_connect()` 首次连接。`on_tools_changed` 回调通知 Agent。

## 5. 设计决策

### 为什么 MCP 工具默认 deferred

MCP server 可能暴露几十上百工具——全部加载浪费 token。deferred + tool_search 按需激活。

### 为什么只支持 stdio

Stdio 最可靠——子进程，stdin/stdout。HTTP/SSE 需要额外依赖和重连逻辑。先做好一个，以后扩展。

## 6. 面试考点

**Q: MCP server 崩溃了怎么办？** 当前无自动重连。连接断开后工具调用返回错误。加重连在 roadmap。

## 7. 代码导读

**关键代码**：`config.py` load_mcp_configs()、`connection.py` _send_request() 竞态修复、`manager.py` load_and_connect()。
