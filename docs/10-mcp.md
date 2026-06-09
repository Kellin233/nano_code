# MCP 集成

## 1. 为什么需要 MCP

内置工具只有 12 个——不可能内建"查 GitHub Issues"、"读数据库"、"调 API"。MCP（Model Context Protocol）是 Anthropic 提出的标准协议——外部服务以标准化 JSON-RPC 接口暴露工具给 Agent。server 是独立进程，通过 stdio 通信。

## 2. 核心概念

### 2.1 协议栈

`McpManager`（多 server 管理+工具聚合）→`McpConnection`（单 server 生命周期，initialize/tools/list/call）→`StdioTransport`（子进程 stdin/stdout JSON-RPC）。

### 2.2 工具注册

MCP 工具用 `mcp__server__tool` 命名注册到 ToolRegistry。默认 deferred——模型通过 `tool_search` 按需激活。`alwaysLoad` 的 server 除外。`notifications/tools/list_changed`→debounced refresh（0.2s 延迟）→计算 added/removed/changed→通过回调通知 Agent。

### 2.3 配置加载

从 `~/.claude.json`、`~/.claude/settings.json`、`./.claude/settings.json`、`./.mcp.json` 合并。`${VAR}` 和 `${VAR:-default}` 环境变量展开。优先级：项目覆盖用户。

## 3. 总体设计

```
capabilities/mcp/
├── config.py       # 多源配置加载+合并+环境变量展开
├── transport.py    # stdio 子进程生命周期+JSON-RPC 通信
├── connection.py   # initialize/tools/list/call+通知处理
├── manager.py      # 多 server 管理+工具命名+回调
├── output.py       # 结构化输出（文本/图片/blob/大结果落盘）
└── resources.py    # resources/list+resources/read
```

## 4. 详细设计

**`config.py`**：`load_mcp_configs()` 合并四个来源，同名 server 项目覆盖用户。展开 `${VAR:-default}`。

**`transport.py`**：`StdioTransport` 管理子进程的生命周期。stderr ring buffer（200 行）用于诊断。

**`connection.py`**：initialize 握手（协议版本+能力交换）。`tools/list` 获取工具列表。`tools/call` 执行。请求 id 竞态修复——先注册 future 再发请求。关闭顺序：cancel reader→close stdin→terminate→wait 2s→kill。

**`manager.py`**：`_make_prefixed_name()` sanitize 命名（非字母数字替换为 _，连续合并，太长截断加 hash）。`load_and_connect()` 首次连接所有 server。`on_tools_changed` 回调通知 Agent 更新 ToolRegistry。

## 5. 设计决策

### 为什么只支持 stdio transport

Stdio 最可靠——启动进程，读写 stdin/stdout。HTTP/SSE/WS 需要额外依赖和重连逻辑。先做好一个。

### 为什么 MCP 工具默认 deferred

MCP server 可能暴露几十上百工具。全部加载浪费 token。deferred + tool_search 按需激活。

### 为什么 MCP 连接不放在 tools.runtime

连接生命周期和工具执行管线是不同关注点。tools.runtime 负责通用执行流程，MCP 工具执行时由 ToolRegistry 路由到 `ctx.mcp_manager`。

## 6. 面试考点

**Q: MCP server 崩溃了怎么办？** 当前无自动重连。连接断开后工具调用返回错误。重连在 roadmap。

**Q: 为什么工具命名用 mcp__server__tool？** 避免和内置工具冲突。sanitize 处理非标准字符。`_tool_routes` 字典做反向映射——不需要反向 split。

## 7. 代码导读

**关键行号**：`config.py` load_mcp_configs()、`connection.py` _send_request() 竞态修复、`manager.py` load_and_connect() + _make_prefixed_name()。
