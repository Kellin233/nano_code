# 工具系统设计

## 目标

工具系统是 Agent 与外部世界交互的唯一通道——读文件、写文件、搜索、执行 shell、调用子 Agent。设计核心理念：**普通工具无状态**，运行时信息和 Agent 会话状态通过 `ToolContext` 注入，工具本身不持有 Agent 引用。

## 代码流程

```
模型生成 tool_use → loop.py 收到 ToolCall
    │
    ▼
ToolRuntime.execute_many(calls, ctx)
    │
    ├── 按并发安全性分组 batch
    │     read_file/list_files/grep_search → 并发执行
    │     write_file/edit_file/run_shell → 串行执行
    │
    └── 对每个 call：
         │
         ▼
    ToolRuntime.execute_one(call, ctx)
         │
         ├── 1. ToolRegistry.find(name) → 找工具
         ├── 2. tool.validate(inp, ctx) → 参数校验
         ├── 3. PreToolUse hooks → 可拒绝/修改输入
         ├── 4. check_permission → 权限检查
         ├── 5. confirm callback → 用户确认
         ├── 6. tool.call(inp, ctx) → 执行
         ├── 7. _persist_large_result → 大结果落盘
         └── 8. PostToolUse hooks → 可追加上下文
```

## 总体设计

### 文件结构

```
capabilities/tools/
├── __init__.py       # 公共导出
├── types.py          # 数据模型 + 常量（合并 types/base/constants）
├── builtin.py        # 内置工具 schema + 实现（合并 definitions/builtin）
├── registry.py       # ToolRegistry：注册/查找/激活
└── runtime.py        # ToolRuntime：执行管线
```

### 三层模型

| 层 | 文件 | 职责 | 变更原因 |
|---|------|------|---------|
| Schema | `builtin.py` 前半 | 声明每个工具叫什么、参数是什么 | 新增工具时改 |
| Registry | `registry.py` | 注册、查找、deferred 激活、MCP 工具合并 | 改注册机制时改 |
| Runtime | `runtime.py` | 执行管线：验证→权限→确认→执行→后处理 | 改执行策略时改 |

### 工具分类常量

```python
READ_TOOL_NAMES   = {"read_file", "list_files", "grep_search", "web_fetch", ...}
EDIT_TOOL_NAMES   = {"write_file", "edit_file"}
CONCURRENCY_SAFE  = {"read_file", "list_files", "grep_search", "web_fetch", ...}
```

只读工具在 `default` 模式下自动允许。并发安全工具可以在同一个 ToolRuntime 批次中并行执行。

### MCP 工具接入

MCP 工具通过 `ToolRegistry.add_many(defs, origin="mcp")` 注册。命名规则 `mcp__server__tool`。注册表中 `origin` 区分 builtin/mcp/custom，影响：
- builtin 工具：`registry.py` 的 `_call_builtin` 直接执行
- MCP 工具：`registry.py` 的 `_build_tool` 构建 `FunctionTool`，执行时委托 `ctx.mcp_manager.call_tool`

deferred 机制适用于所有非 builtin 工具——默认不暴露给模型，模型需通过 `tool_search` 主动激活。

## 详细设计

### `types.py`——数据模型

合并了三个旧文件的内容：
- `types.py`：`ToolDef`、`ToolMetadata`、`ToolOrigin`、`PermissionMode`
- `base.py`：`ToolCall`、`ToolContext`、`ToolResult`、`FunctionTool`、`ValidationResult`
- `constants.py`：所有工具相关常量（超时、压缩阈值、结果截断等）

合并原因：改一个类型的字段时，schema 面和运行时面往往要一起改（如改 `ToolCall` 增加 `provider` 字段，`ToolResult` 的调用方也需要知道）。放在一个文件中变更窗口一致。

**核心类型**：

- `ToolCall`：一次工具调用的参数化——id、name、input、provider
- `ToolContext`：工具执行时的上下文——cwd、session_id、read_file_state、sandbox_manager、mcp_manager、agent
- `ToolResult`：工具执行结果——content、is_error、metadata、extra_messages（供 PostToolUse hook 追加上下文）
- `FunctionTool`：基于函数的工具适配器——包裹一个 `call_fn`，提供 validate/call 接口

### `builtin.py`——内置工具

合并了旧 `definitions.py`（Schema 定义）和旧 `builtin.py`（实现函数）。加新工具时只改这一个文件。

**当前 12 个内置工具**：read_file、write_file、edit_file、list_files、grep_search、run_shell、skill、web_fetch、agent、tool_search、list_mcp_resources、read_mcp_resource

**Schema 定义**：每个工具的 `input_schema` 使用 JSON Schema 格式，包含 type、properties、required 字段。

**实现函数**：
- `read_file`：读取文件，返回带行号的内容
- `write_file`：写入文件，自动创建父目录，触发 memory index 同步
- `edit_file`：精确替换——old_string 必须在文件中唯一匹配。支持 Unicode 引号归一化
- `list_files`：glob 匹配，跳过 `.git`/`__pycache__`/`venv`
- `grep_search`：优先系统 grep（Linux），fallback Python 实现
- `web_fetch`：urllib 获取 URL，HTML 标签剥离，长度截断
- `run_shell`：**内部实现参考，不被直接调用**——所有执行路径要求显式传入 sandbox backend

### `registry.py`——ToolRegistry

**初始化**：`ToolRegistry(builtin_tool_definitions())` 加载所有内置工具。MCP 工具通过 `add_many()` 后续追加。

**核心方法**：
- `find(name)`：按名查找
- `active_definitions(denied)`：返回当前激活的工具列表（排除 deferred 未激活和被 skill 禁用的）
- `deferred_names(denied)`：返回可以被 `tool_search` 激活的 deferred 工具名
- `search_deferred(query)`：按关键词搜索 deferred 工具，匹配后自动激活
- `add_many(tools, origin)` / `remove_many(names)` / `replace_many(tools, origin)`：批量操作

**内部实现**：工具分为两套数据：
- `_tools`：`dict[name, FunctionTool]`——实际可调用的工具实例
- `_metadata`：`dict[name, ToolMetadata]`——工具元数据（origin、deferred、read_only 等）

`_build_tool` 工厂函数根据 `origin` 构建不同的 `FunctionTool`：
- `builtin` → `_call_builtin`（本地执行）
- `mcp` → `_call_mcp`（委托 ctx.mcp_manager）
- `custom` → 与 builtin 相同路径

### `runtime.py`——ToolRuntime

**执行管线**：

`execute_many(calls, ctx)` 按并发安全性分组。并发安全的工具放入同一个 batch 用 `asyncio.gather` 并行执行，不安全的串行执行。

`execute_one(call, ctx)` 是单个工具的执行流程：

1. `ToolRegistry.find(name)`——找不到返回错误
2. `tool.validate(inp, ctx)`——参数校验，required 字段检查
3. PreToolUse hooks——可返回 deny（拒绝）或 modify（修改输入）。每次 modify 后重新校验
4. `check_permission()`——权限策略判断
5. 如需确认 → 通过 `event_callback` 发出 PermissionRequested 事件，等待用户确认
6. `tool.call(inp, ctx)`——实际执行
7. `_persist_large_result()`——结果超过 30KB 时落盘，消息历史中只存预览
8. PostToolUse hooks——可返回 `append_context`（追加到 user context）

**大结果处理**：阈值 `LARGE_RESULT_BYTES = 30KB`。超限时写入 `~/.nanocode/tool-results/`，返回结果只保留前 200 行预览 + artifact 引用。

**先读后改**：`write_file` 和 `edit_file` 执行前检查 `read_file_state`——如果文件未在本次会话中读取过，或读取后 mtime 被外部修改，返回错误要求重新 `read_file`。这个不变量保证模型不能"盲写"文件。

## 硬性约束

- 工具错误必须返回 `ToolResult(..., is_error=True)` 或 `"Error: ..."` 字符串，不能打断主循环
- `run_shell` 的所有执行路径必须显式传入 sandbox manager 或 execution_backend
- 先读后改不变量——`write_file`/`edit_file` 前必须先 `read_file`
- 工具 schema 定义（`input_schema`）不能变更——模型依赖这些 schema

## 隐含要求

- 新增内置工具的变更窗口应该是 `builtin.py` 一个文件
- `ToolRegistry` 的 deferred 机制必须与 MCP 工具加载异步兼容
- 工具结果是模型的消息上下文——截断/落盘策略直接影响模型决策质量

## 不能做什么

- 不能让普通工具执行异常打断主循环
- 不能让 MCP 工具的特殊路由渗透到主模型循环中
- 不能把 Agent 会话状态（消息历史、token 统计等）写进工具执行逻辑
- 不能直接 `subprocess.run(shell=True)`——必须通过 sandbox

## 可能踩坑的地方

### `builtin.py` 的 `run_shell` 函数

`builtin.py` 中仍有 `run_shell(inp)` 函数，但它**不被 BUILTIN_HANDLERS 引用**。所有 `run_shell` 执行路径现在要求在 ToolRegistry 或 execute_builtin_tool 中显式传入 sandbox backend。直接调用 `builtin.run_shell()` 会导致裸 shell 执行——如果未来有人不小心恢复了这个引用，会绕过所有安全防护。

### ToolRegistry 的 `_call_builtin` 与 `runtime.execute_builtin_tool`

两处都有工具执行逻辑：
- `registry.py` 的 `_call_builtin`：用于 `ToolRuntime.execute_one()` 路径（有 ToolContext，走完整管线）
- `runtime.py` 的 `execute_builtin_tool`：用于 Agent 旧代码的兼容路径（无 ToolContext，手动传参数）

两处需要保持行为一致。如果改了其中一处忘记改另一处，会导致不同调用路径对同一工具返回不同结果。

### concurrent 分组

`execute_many` 按 `registry.is_concurrency_safe()` 分组。如果一个标记为安全的工具实际不安全（如未来的某个新工具），会导致数据竞争。`CONCURRENCY_SAFE_BUILTIN_TOOLS` 集合必须精确。

### mtime 检查在并发场景

两个并发子 Agent 如果同时读同一文件再写，先读后改的 mtime 检查在 Agent 各自的 `read_file_state` 中是隔离的——因为它们有不同的 Agent 实例。这不是 bug（各自独立），但如果将来共享 `read_file_state`，需要加锁。
