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

当前只连接 stdio transport。配置里可以声明 `http`、`sse`、`ws`，但 `McpManager.load_and_connect()` 会记录 warning diagnostic 并跳过；`McpConnection.connect()` 对非 stdio transport 直接报错。

stdio server 的生命周期是：

```text
AgentSession._ensure_mcp_initialized()
  → McpManager.load_and_connect()
  → load_mcp_configs(cwd)
  → 过滤 transport == "stdio"
  → McpConnection.connect()
  → StdioTransport.start()
  → initialize
  → notifications/initialized
  → tools/list
  → McpManager 注册 ToolDef
```

这条链路发生在首次需要工具 schema 前，而不是 Agent core 初始化时。失败 server 不会阻断整个 session；错误进入 diagnostics，其他 server 仍可继续连接。

## 4. 工具注册

MCP 工具命名为：

```
mcp__<server>__<tool>
```

server 名和 tool 名会做 ASCII 安全化，过长名称会加 hash 后缀缩短到工具名长度上限。这个前缀是权限、deferred search、trace/report 和 ToolRegistry 路由的共同边界：模型看到的是 prefixed name，真正发给 MCP server 的仍是原始 tool name。

注册流程：

```
McpManager.load_and_connect()
  → tools/list
  → 转成 ToolDef
  → ToolRegistry.add_many(origin="mcp", default_concurrency_safe=False)
```

MCP 工具默认 deferred，避免大量 schema 进入上下文。模型通过 `tool_search` 激活需要的 MCP 工具。

如果 server 配置或单个工具声明 `alwaysLoad` / `always_load`，该工具会非 deferred 注册，启动后直接进入可见 schema。MCP 工具默认 `concurrency_safe=False`，避免外部服务状态不明时并发调用。

`alwaysLoad` 本质上是对 deferred 的反向配置：server 级设置会影响该 server 全部工具，单工具设置只影响自身。它适合极少数“总是需要、schema 很小、风险明确”的工具；大量外部工具仍应保持 deferred，由 `tool_search` 按需激活。

## 5. 工具变更

MCP server 发送 `notifications/tools/list_changed` 后，manager 做 debounce refresh，计算 added/removed/changed，然后通过 `on_tools_changed` 回调通知 `AgentSession`。

`AgentSession` 再更新 `ToolRegistry`，并通过 `render_mcp_delta_attachment()` 把变化作为动态附件注入对话。

refresh 有两个重要细节：

- debounce 当前为 0.2 秒，避免 server 连续发送变更通知时反复 `tools/list`。
- delta 只描述同一 server 下的 added/removed/changed。`ToolRegistry` 更新后，已经发给模型的 deferred tool 列表也会按 removed/changed 做状态修正，避免模型继续调用已删除或 schema 已变的工具。

## 6. 配置

配置来源：

- `~/.claude.json`
- `~/.claude/settings.json`
- `./.claude/settings.json`
- `./.mcp.json`

支持 `${VAR}` 和 `${VAR:-default}` 展开。项目级配置覆盖用户级配置。

加载规则按上面的顺序合并，后出现的同名 server 覆盖先出现的配置。配置既可以写在 `mcpServers` 字段下，也可以直接把根对象当 server mapping。当前 loader 对 Claude 的 `projects` mapping 只记录 info diagnostic，不展开项目级子配置。

server 启动环境由当前进程环境、server 配置里的 `env` 和 `CLAUDE_PROJECT_DIR` 组成。`command`、`args`、`env`、`url` 都会做环境变量展开；缺失且没有默认值的变量会展开为空字符串，并产生 warning diagnostic。

连接和配置问题不会直接让 Agent core 崩溃。配置解析、环境变量缺失、unsupported transport、连接失败、resource 读取失败都会进入 `McpManager.diagnostics`，`AgentSession` 初始化 MCP 失败时也会把错误写入 agent diagnostics。

需要区分三类“unsupported”：

| 场景 | 行为 |
|------|------|
| `transport` 是 `http` / `sse` / `ws` | 配置可解析，但 `load_and_connect()` 跳过并记录 warning |
| `transport` 是未知字符串 | loader 记录 warning，并按 stdio 形态继续解析 |
| `McpConnection.connect()` 收到非 stdio | 直接抛错，调用方记录 connect failed diagnostic |

因此文档和 UI 只能说 HTTP/SSE/WS 配置会被识别并跳过，不能宣称这些 transport 已实现。

## 7. 资源工具

MCP resource 通过两个内置工具暴露：

- `list_mcp_resources`：列出所有 connected server 的资源，可按 server 过滤。
- `read_mcp_resource`：按 server + uri 读取资源。

这两个工具是内置只读工具，和普通 read 工具一样并发安全；它们不是 deferred MCP server 工具。

## 8. 输出处理

`cli/core/mcp/output.py` 会把 MCP 返回结果统一格式化：

- `content[].text` 过大时保存到 `~/.nanocode/mcp-outputs/*.txt`，返回预览和路径。
- `image` / `blob` 超过内联阈值时保存为文件，返回 mime、大小和路径。
- `resources/read` 的大文本或 blob 也会保存到同一目录。
- 最终格式化文本超过总限制时，完整结果保存到文件，工具结果只返回预览。

`ToolRegistry` 调用 MCP 工具时会把 `saved_files` 放入 `ToolResult.metadata`，方便 trace/report 或后续调试定位完整输出。

## 9. 边界与失败模式

MCP 集成的失败模式被限制在应用层：

| 场景 | 行为 | 影响 |
|------|------|------|
| 配置 JSON 解析失败 | diagnostic error | 该文件配置不生效，session 继续 |
| server 启动失败或 initialize 超时 | diagnostic error，关闭连接 | 该 server 工具不可用 |
| `tools/list` 返回异常形态 | 视为空工具列表或 connect failed | 不注册无 schema 工具 |
| MCP tool 路由不存在 | 工具调用返回错误 | 不会落到任意 server |
| `resources/list/read` 失败 | warning diagnostic，工具返回错误文本 | 只影响资源工具结果 |
| server 下线后发送变更失败 | refresh warning | 保留已有连接状态或等待后续恢复 |

这些错误不进入 Agent core 的状态机，也不改变权限策略。MCP 工具注册进 `ToolRegistry` 后，执行仍走同一条 `ToolRuntime`：allowed tools、active skill deny list、permission policy、大结果持久化和 trace/report 都继续生效。

## 10. 设计决策

### 为什么 MCP 不放在 tools.runtime

MCP 连接生命周期和工具执行管线是两个关注点。ToolRuntime 只负责通用执行流程；真正调用 MCP 工具时，ToolRegistry 通过 `ctx.mcp_manager` 路由。

### 为什么 MCP 工具默认 deferred

MCP server 可能提供几十上百个工具。一次性暴露全部 schema 会浪费 token，也会破坏工具缓存稳定性。deferred 让模型按需激活。

### 为什么只实现 stdio

stdio server 的生命周期最容易和本地 CLI/session 对齐：进程由 NanoCode 启动，环境可控，关闭 session 时可直接清理。HTTP/SSE/WS 需要额外处理长连接鉴权、重连、跨 session 复用和网络安全策略；当前代码只保留配置解析和 diagnostic，不把未完成能力包装成已实现功能。

## 11. Benchmark 覆盖

当前 `benchmarks/local-fixture` 没有专门 MCP server case。MCP 仍受工具系统公共约束覆盖：deferred 工具需要通过 `tool_search` 激活，allowed tools 要同时限制 schema 和执行，MCP 输出进入同一套 ToolRuntime 大结果治理。

新增 MCP benchmark 时应覆盖 stdio server 连接、tool list changed 刷新、resource 读取、大 blob 保存和 unsupported transport diagnostic。

维护者自查重点：

- MCP connection lifecycle 和 ToolRuntime execution 是两条链路，只有注册后的工具调用才进入 ToolRuntime。
- unsupported HTTP/SSE/WS 是“跳过并诊断”，不是 fatal，也不是已实现。
- `mcp__server__tool` 前缀是权限和路由边界，不能在调用时随意截断或重新推断。
- `alwaysLoad` 会增加 prompt schema 压力，应作为例外而不是默认。

## 12. 代码导读

```
cli/core/mcp/config.py
cli/core/mcp/transport.py
cli/core/mcp/connection.py
cli/core/mcp/manager.py
cli/core/mcp/output.py
cli/core/tools/registry.py
cli/session.py::_ensure_mcp_initialized
```
