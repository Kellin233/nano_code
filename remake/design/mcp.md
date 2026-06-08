# MCP 重构方案

## 目标

把当前 `src/mcp_client.py` 从“能连接 stdio MCP server 的最小 JSON-RPC 客户端”，重构成稳定、可诊断、可扩展的 MCP 子系统。

本轮重构重点：

- 修复请求竞态、无限等待、stderr 死锁、粗暴关闭等稳定性问题。
- 补齐 Claude Code 常见 MCP 配置来源和环境变量展开。
- 保留 MCP 结构化输出，不只拼接 text。
- 支持 MCP resources：`resources/list` 和 `resources/read`。
- 支持 `notifications/tools/list_changed`，工具列表变化时刷新 registry。
- 支持 MCP deferred tools：默认只暴露名称和简短说明，通过 `tool_search` 激活 schema。
- 权限规则支持 `mcp__server` 级 allow/deny。

本轮不追求一次性实现 7 种 transport、OAuth、企业策略、Claude.ai proxy。第一版先把 stdio 路径做可靠，再把抽象边界留好。

## 总体设计

### 结论

保留 `mcp_client.py` 作为公共入口，但内部拆成 `mcp/` 包。`mcp_client.py` 只做兼容导出，避免调用点一次性大迁移。

建议结构：

```text
src/
├── mcp_client.py              # 兼容导出 McpManager
└── mcp/
    ├── __init__.py
    ├── types.py               # 配置、工具、资源、结果、事件类型
    ├── config.py              # 配置加载、作用域合并、env expansion
    ├── transport.py           # stdio transport 和 JSON-RPC 基础通信
    ├── connection.py          # 单 server 生命周期、初始化、请求、通知
    ├── manager.py             # 多 server 管理、工具/资源聚合、registry 回调
    ├── output.py              # tools/call 结果结构化与大结果落盘
    └── resources.py           # resources/list 和 resources/read 辅助
```

模块职责：

| 模块 | 职责 |
|------|------|
| `mcp_client.py` | 兼容旧 import：`from .mcp.manager import McpManager` |
| `mcp/types.py` | `McpServerConfig`、`McpToolDef`、`McpResult`、`McpEvent` |
| `mcp/config.py` | 读取 `~/.claude.json`、`.claude/settings.json`、`.mcp.json`，展开环境变量 |
| `mcp/transport.py` | stdio 子进程、stdin/stdout/stderr、JSON-RPC request/notification |
| `mcp/connection.py` | initialize、tools/list、tools/call、resources/list/read、通知处理 |
| `mcp/manager.py` | 多 server 连接、工具名前缀、工具刷新、资源聚合 |
| `mcp/output.py` | 结构化输出、`isError`、blob 落盘、结果摘要 |
| `mcp/resources.py` | 将 MCP resources 转成内置工具可读的结果 |

### 运行时边界

MCP 连接生命周期仍然属于 MCP 模块，不放进 `tools.runtime`。

工具系统只负责：

- 保存 MCP 工具 schema。
- 保存 origin/metadata。
- 判断 read-only / concurrency-safe。
- deferred 激活。
- 权限匹配。

Agent 层只负责：

- 首次 chat 时触发 MCP 初始化。
- 把 MCP 工具定义注册到 ToolRegistry。
- 执行 `mcp__server__tool` 时转发给 `McpManager.call_tool()`。
- 将 MCP list_changed 产生的工具变更转成动态 attachment。

不要让 `tools.runtime` 启动 MCP 子进程，也不要让 `McpConnection` 直接修改 Agent 消息历史。

## 详细设计

### 1. MCP 类型

`mcp/types.py` 保持轻量 dataclass。

```python
@dataclass
class McpServerConfig:
    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    transport: Literal["stdio", "http", "sse", "ws"] = "stdio"
    timeout: float = 15.0
    always_load: bool = False
```

第一版只实现 `stdio`。`url`、`http`、`sse`、`ws` 可以解析但标记 unsupported，输出诊断，不要假装支持。

工具定义：

```python
@dataclass
class McpToolDef:
    server_name: str
    tool_name: str
    prefixed_name: str
    description: str
    input_schema: dict
    deferred: bool = True
    always_load: bool = False
```

结果：

```python
@dataclass
class McpCallResult:
    text: str
    is_error: bool = False
    saved_files: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
```

### 2. 配置加载

`mcp/config.py` 负责从多个来源合并配置。

第一版读取：

```text
~/.claude.json
~/.claude/settings.json
<cwd>/.claude/settings.json
<cwd>/.mcp.json
```

优先级：

1. 用户级低优先级。
2. 项目级覆盖用户级同名 server。
3. `.mcp.json` 覆盖 `.claude/settings.json` 中同名 server。

后续如果加 managed/enterprise，再放在更高优先级。

支持格式：

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_TOKEN": "${GITHUB_TOKEN}"
      }
    }
  }
}
```

`~/.claude.json` 可能不是只有 `mcpServers` 根字段。加载器要宽松处理：

- 如果顶层有 `mcpServers`，读它。
- 如果顶层有 `projects`，先不深入复杂项目映射，记录诊断。
- 如果整个文件就是 server map，也兼容旧实现。

环境变量展开：

```text
${VAR}           -> os.environ["VAR"]，不存在则空字符串并记录 warning
${VAR:-default} -> VAR 存在用 VAR，否则用 default
```

展开范围：

- `command`
- `args`
- `env` value
- `url`

注入环境变量：

```python
merged_env = {
    **os.environ,
    **config.env,
    "CLAUDE_PROJECT_DIR": str(project_root),
}
```

`project_root` 第一版可以使用 `Path.cwd()`。如果未来有 workspace root 检测，再替换为 git root 或显式 root。

### 3. JSON-RPC 稳定性

当前 `_send_request()` 的竞态必须修。

正确顺序：

```python
req_id = self._next_id
self._next_id += 1
future = loop.create_future()
self._pending[req_id] = future

try:
    write request
    await drain
    return await asyncio.wait_for(future, timeout)
finally:
    self._pending.pop(req_id, None)
```

原因：

- response 可能在 `drain()` 返回前已经到 stdout。
- reader loop 查 `_pending` 时必须已经能找到 future。

timeout：

- initialize/tools/list 默认 15 秒。
- tools/call 默认 60 秒，可通过 server config 或环境变量覆盖。
- resources/read 默认 60 秒。

超时后：

- 从 `_pending` 删除 future。
- 返回明确错误。
- 不立刻 kill server，除非连续超时或进程已退出。

stderr drain：

```python
self._stderr_task = asyncio.create_task(self._read_stderr_loop())
```

stderr 行可以保存在 ring buffer 中，例如最多 200 行。连接失败或调用失败时把最近 stderr 摘要放进诊断。

关闭顺序：

1. 取消 reader/stderr task。
2. 关闭 stdin 或发送 EOF。
3. `terminate()`。
4. wait 2 秒。
5. 仍未退出再 `kill()`。
6. 拒绝所有 pending future。

不要默认直接 `kill()`。

### 4. 通知处理

reader loop 不能只处理带 `id` 的响应，也要处理 notification。

第一版处理：

```text
notifications/tools/list_changed
```

收到后：

- 标记当前 server 工具列表 dirty。
- 由 manager 安排异步 refresh。
- refresh 时调用 `tools/list`。
- 计算 added/removed/changed。
- 更新 manager 内部工具表。
- 通过 callback 通知 Agent/ToolRegistry。

不要在 reader loop 里直接改 registry。reader loop 只负责通信事件，registry 更新由 manager 或 Agent 明确执行。

### 5. 工具命名和映射

继续使用：

```text
mcp__server__tool
```

但不能只靠 `split("__")` 路由。

问题：

- server 名可能含 `__`。
- tool 名可能含非法字符。
- provider 对工具名长度和字符可能有限制。

建议：

```python
self._tool_routes: dict[str, tuple[str, str]] = {}
```

生成 prefixed name 时：

- server name 和 tool name 做 sanitize。
- 非 `[A-Za-z0-9_-]` 替换为 `_`。
- 连续 `_` 合并。
- 太长时截断并加短 hash。
- 如果冲突，加 `_<hash>`。

调用时通过 `_tool_routes[prefixed_name]` 找原始 server/tool 名，不再反向 split。

### 6. MCP 工具定义和 ToolRegistry

`McpManager.get_tool_definitions()` 返回符合 ToolRegistry 的 dict：

```python
{
    "name": "mcp__github__list_issues",
    "description": "...",
    "input_schema": {...},
    "deferred": True,
    "origin": "mcp",
    "concurrency_safe": False,
    "read_only": False,
    "mcp_server": "github",
    "mcp_tool": "list_issues",
}
```

第一版策略：

- MCP 工具默认 `deferred=True`。
- `alwaysLoad` 的 server 或 tool 设置 `deferred=False`。
- MCP 工具默认不是 read-only。
- MCP 工具默认不是 concurrency-safe。

`tool_search` 激活 deferred 工具后，下一轮模型请求才包含完整 schema。

注意：Agent 初始化时 system prompt 已经构建完成，MCP 首次连接发生在首次 chat。MCP deferred 工具名称不能依赖 system prompt 中的 `{{deferred_tools}}`，必须作为动态 attachment 注入。

### 7. ToolSearch 查询

现有 ToolRegistry 已有简单 `search_deferred()`。MCP 重构可以增强但不要过度实现 BM25。

第一版支持：

```text
select:tool1,tool2
keyword search
+server keyword
```

匹配字段：

- prefixed tool name
- raw tool name
- server name
- description
- optional `searchHint`

返回格式仍然是 JSON schema 列表，保持当前后端可用。

后续如果需要更接近 Claude Code 的 `<functions>` 格式，再单独改，不要和 MCP 稳定性混在一起。

### 8. MCP 输出结构化

`mcp/output.py` 负责 `tools/call` result 转成模型可读文本。

MCP result 常见结构：

```json
{
  "content": [
    {"type": "text", "text": "..."},
    {"type": "image", "data": "...", "mimeType": "image/png"},
    {"type": "resource", "resource": {...}}
  ],
  "isError": false
}
```

处理规则：

- text：原样拼接。
- image/blob：超过阈值落盘，只返回路径、mime type、大小。
- resource：保留 uri、mimeType、text 或 blob 摘要。
- unknown type：JSON 序列化摘要，不丢弃。
- `isError=true`：结果开头加 `[MCP tool error]`，并在 `McpCallResult.is_error` 保留。

落盘目录：

```text
~/.nanocode/mcp-outputs/
```

文件名：

```text
{timestamp}-{server}-{tool}-{index}.{ext}
```

阈值建议：

| 类型 | 阈值 |
|------|------|
| text 单块 | 50KB |
| image/blob inline | 25KB |
| 最终文本 | 100KB |

最终文本超过阈值时，和普通工具一样走大结果持久化，避免上下文膨胀。

### 9. MCP Resources

新增两个内置工具：

```text
list_mcp_resources
read_mcp_resource
```

工具定义放在 `tools/definitions.py`。

执行路由放在 `agent/tools_runtime.py`，因为它需要访问 `_mcp_manager`。

`list_mcp_resources` 输入：

```json
{
  "server": "optional server name"
}
```

行为：

- 不传 server：聚合所有 connected server 的资源。
- 传 server：只列该 server。
- 每个资源返回 `server` 字段，便于后续读取。

`read_mcp_resource` 输入：

```json
{
  "server": "server name",
  "uri": "resource uri"
}
```

行为：

- 调用 MCP `resources/read`。
- 结果走 `mcp/output.py`，支持 text/blob 落盘。

权限：

- `list_mcp_resources` 可视为只读、并发安全。
- `read_mcp_resource` 可视为只读、并发安全。
- 但它们读取外部服务，仍应允许 deny 规则禁用。

### 10. 权限增强

当前权限规则只精确匹配工具名，不支持 `mcp__server`。

增强 `_matches_rule()`：

```python
def _matches_tool(rule_tool: str, tool_name: str) -> bool:
    if rule_tool == tool_name:
        return True
    if tool_name.startswith(rule_tool + "__") and rule_tool.startswith("mcp__"):
        return True
    return False
```

示例：

```text
mcp__github
mcp__github__list_issues
```

注意：

- server 级规则只对 `mcp__server__tool` 生效。
- 不要让普通工具名用前缀误匹配。
- MCP 工具仍默认非 read-only、非 concurrency-safe。

## 硬性约束

### 稳定性优先

MCP 是增强能力，不是启动前提。

- 某个 MCP server 失败，不影响内置工具。
- 某个工具调用失败，不断开其他 server。
- tools/list_changed 刷新失败，不删除旧工具，除非明确知道 server 已断开。

### 第一版只实现 stdio

可以解析 http/sse/ws 配置，但不要假装支持。

遇到非 stdio：

- 记录诊断。
- 打印简短 warning。
- 跳过该 server。

后续实现远程 transport 时再加 httpx/websockets/OAuth，不要在本轮把范围放大。

### 不引入 MCP SDK

第一版继续使用标准库和裸 JSON-RPC。

原因：

- 当前实现已经是教学式轻量客户端。
- MCP SDK 会带来依赖和抽象迁移成本。
- stdio JSON-RPC 本身不复杂，先把正确性补齐即可。

如果后续要支持 HTTP/OAuth，再评估是否引入 SDK。

### 不让 MCP 连接生命周期进入工具系统

`tools/registry.py` 可以保存 MCP schema 和 metadata，但不能启动进程、重连 server、读 stdout。

这样做的原因：

- 工具 registry 是目录，不是连接管理器。
- MCP 连接有异步任务、进程、pending request、stderr、通知，这些属于 MCP 子系统。

### 外部输出必须有大小控制

MCP server 不可信，可能返回巨大文本或 base64。

必须限制：

- 单块大小。
- 最终结果大小。
- 落盘文件大小或至少落盘路径。

不能把大 blob 直接塞进模型上下文。

### 失败要可诊断

不能像当前 `_merge_config_file()` 那样所有异常都 `pass`。

配置解析、连接失败、initialize 失败、tools/list 失败，都应保留诊断。

诊断不一定都展示给模型，但应能通过日志或 debug 命令查看。

## 隐含要求

### MCP 工具不是内置工具

即使 schema 进入 ToolRegistry，也不能把 MCP 当成内置工具处理。

差异：

- 内置工具实现可控。
- MCP 工具来自外部服务。
- MCP 工具读写语义不可可靠推断。
- MCP 工具调用可能慢、失败、断线。

因此默认保守：

- 非 read-only。
- 非 concurrency-safe。
- 不自动允许并发。

### 工具列表变化会影响模型可见能力

ToolRegistry 更新后，不代表模型马上知道变化。

要么：

- 下一轮 API 请求工具 schema 变化。
- 要么通过 attachment 告诉模型 deferred 工具名称变化。

因此 MCP manager 需要向 Agent 返回“工具变更事件”，不能只内部刷新。

### 配置是安全边界

`.mcp.json` 能声明任意 command。项目仓库里的配置不应被完全无提示信任。

第一版如果不实现项目级审批，至少要：

- 明确在文档和日志里标记来源。
- 未来预留 `approved` / `scope` 字段。
- 不把项目配置里的路径用于写用户敏感目录。

### 输出格式要服务模型恢复

结构化输出转文本时，必须让模型知道：

- 这是哪个 server/tool 的结果。
- 是否 error。
- 哪些内容被落盘。
- 如何读取完整内容。

不要只返回“saved to file”而没有足够上下文。

## 不能做什么

- 不要一次性实现 HTTP/SSE/WebSocket/OAuth/企业策略。
- 不要为了追求完整 MCP spec 引入大量依赖。
- 不要把 MCP 进程管理放进 `tools.runtime`。
- 不要把 reader loop 和 ToolRegistry 直接耦合。
- 不要继续只 join text，丢弃 image/resource/blob/isError。
- 不要让 `_send_request()` 没有 timeout。
- 不要继续直接 `kill()` server 作为唯一关闭方式。
- 不要默认认为 MCP 工具只读或并发安全。
- 不要把所有 MCP 工具 schema 一次性塞给模型，除非配置为 `alwaysLoad`。
- 不要吞掉配置错误和连接错误。
- 不要把 diagnostics 全部注入模型上下文。
- 不要为了支持 server 名里的特殊字符继续用 `split("__")` 反向解析路由。

## 可能踩坑的地方

### 1. pending future 竞态

如果 future 注册晚于写 stdin，快速响应会丢失。

解决：

- future 先入 `_pending`。
- 写入失败时再从 `_pending` 删除。

### 2. timeout 后旧响应回来

请求 timeout 后，server 可能稍后返回旧 id。

解决：

- timeout 时删除 `_pending`。
- reader 收到未知 id 时忽略并记录 debug，不要报错。

### 3. stderr 死锁

server 大量写 stderr，无人读取会阻塞整个进程。

解决：

- 启动 stderr drain task。
- 保留最近 N 行用于诊断。

### 4. close 和 reader task 竞态

关闭时 reader 可能正在 set_result。

解决：

- close 设置 `_closed=True`。
- pending future set_exception 前检查 `done()`。
- cancel task 后吞掉 `CancelledError`。

### 5. tools/list_changed 刷新风暴

server 可能连续发送多次 list_changed。

解决：

- debounce，例如 200ms 内合并一次刷新。
- 同一 server 同时只允许一个 refresh task。

### 6. ToolRegistry 和模型请求不同步

工具刚刷新，当前 API 请求已经开始，模型本轮看不到新工具。

解决：

- 接受“一轮延迟”。
- 刷新后通过 attachment 告诉模型下一轮可搜索/使用。

### 7. MCP 工具名冲突

sanitize 后两个工具可能变成同名。

解决：

- prefixed name 冲突时加短 hash。
- `_tool_routes` 保存 prefixed -> raw mapping。

### 8. 输出落盘路径泄露或不可读

如果文件保存路径太长、权限不对、目录不存在，模型拿到路径也读不了。

解决：

- 启动时确保 `~/.nanocode/mcp-outputs` 存在。
- 写文件失败时回退为文本摘要。
- 返回绝对路径。

### 9. 配置 env expansion 误处理 JSON

有些 env value 本身包含 `${...}` 字符串，不一定是变量。

解决：

- 只处理字符串字段。
- 不递归解析展开后的结果。
- 未定义变量记录 warning。

### 10. 项目配置安全

`.mcp.json` 来自仓库，可能启动恶意命令。

解决：

- 本轮至少保留来源信息。
- 后续加入 approval cache。
- 不把 project-scope MCP 默默视为用户完全信任。

## 实施顺序

### Phase 1：稳定性修复

1. 拆出 `mcp/transport.py` 和 `mcp/connection.py`。
2. 修复 `_send_request()` pending race。
3. 加 request timeout。
4. 加 stderr drain。
5. 改 close 顺序。
6. 用 fake MCP server 写回归测试。

### Phase 2：配置解析

1. 新增 `mcp/config.py`。
2. 支持 `~/.claude.json`、`~/.claude/settings.json`、项目 `.claude/settings.json`、`.mcp.json`。
3. 支持 `${VAR}` / `${VAR:-default}`。
4. 注入 `CLAUDE_PROJECT_DIR`。
5. 保留 diagnostics。

### Phase 3：结构化输出

1. 新增 `mcp/output.py`。
2. 支持 text/image/resource/blob/isError。
3. 大结果落盘。
4. `McpManager.call_tool()` 返回文本摘要，但内部保留 raw result。

### Phase 4：resources 工具

1. 在 `tools/definitions.py` 加 `list_mcp_resources`、`read_mcp_resource`。
2. 在 `agent/tools_runtime.py` 路由到 `_mcp_manager`。
3. 在 `McpConnection` 实现 `list_resources()`、`read_resource()`。
4. 测试无 resources capability 时返回明确错误。

### Phase 5：list_changed

1. reader loop 识别 notification。
2. connection 发出事件。
3. manager debounce refresh。
4. Agent/registry 接收工具 delta。
5. 动态 attachment 告诉模型工具变化。

### Phase 6：MCP ToolSearch

1. MCP 工具默认 `deferred=True`。
2. `alwaysLoad` 支持 server 级和 tool 级。
3. ToolRegistry 搜索支持 `select:`、keyword、`+server`。
4. prompt 动态附件列出 deferred MCP tool names。

### Phase 7：权限增强

1. 支持 `mcp__server` server 级匹配。
2. 确保 MCP/custom 默认非并发安全。
3. 补权限规则测试。

## 测试建议

### JSON-RPC

- server 立即响应，验证 pending race 不再挂住。
- server 不响应，验证 timeout。
- server 写大量 stderr，验证不死锁。
- close 时 pending 请求收到异常。
- timeout 后旧响应回来不会影响后续请求。

### 配置

- `~/.claude.json` 有 `mcpServers`。
- `.mcp.json` 覆盖同名 server。
- `${VAR}` 展开成功。
- `${VAR:-default}` 使用 default。
- 未定义 `${VAR}` 记录 warning。
- `CLAUDE_PROJECT_DIR` 注入 env。

### 输出

- text content 正常返回。
- `isError=true` 被保留。
- image/blob 超阈值落盘。
- unknown content type 不丢弃。
- 超大最终结果被截断或落盘。

### resources

- `list_mcp_resources` 聚合多 server。
- `list_mcp_resources(server=...)` 只列一个 server。
- `read_mcp_resource` 读取 text resource。
- server 不支持 resources 时返回可理解错误。

### list_changed

- fake server 发送 `notifications/tools/list_changed`。
- manager 刷新工具列表。
- added/removed tools 正确更新 registry。
- 多次 notification 被 debounce。

### ToolSearch

- MCP deferred 工具初始不在 active definitions。
- `tool_search("select:mcp__x__y")` 后激活。
- `alwaysLoad` 工具启动即 active。
- MCP 工具默认非 concurrency-safe。

## 本章小结

MCP 重构的核心不是“把协议支持堆满”，而是先把边界做稳。

连接层要可靠，配置层要兼容，输出层要保留结构，工具层只保存 schema 和 metadata，Agent 层负责把工具变化注入模型上下文。这样实现后，Nano Code 可以继续保持轻量，但不会被一个外部 MCP server 卡死，也能逐步扩展 resources、ToolSearch、远程 transport 和权限审批。
