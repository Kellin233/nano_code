# 工具系统

## 1. 为什么需要工具系统

LLM 只能生成文本。让它读文件、搜索代码、跑命令——这些是真实的 I/O 操作。工具系统是 Agent 的"手"——把模型的文本意图翻译成文件读写、命令执行、网络请求。

设计核心理念：**普通工具无状态**。`read_file` 不应该记住"上次读了哪个文件"——那是 Agent 会话的事。工具是纯函数——输入参数，输出结果。运行时上下文（cwd、session_id、sandbox）通过 `ToolContext` 注入。

## 2. 核心概念

### 2.1 三层模型

```
Schema 层 → 声明工具叫什么、参数是什么（builtin.py）
    ↓
Registry 层 → 注册、查找、deferred 激活、MCP 合并（registry.py）
    ↓
Runtime 层 → 执行管线：验证→权限→Hook→执行→后处理（runtime.py）
```

每层独立变更。加工具只改 Schema 层。改 deferred 策略只改 Registry 层。改并发调度只改 Runtime 层。

### 2.2 ToolContext：运行时信息注入点

工具不应持有 Agent 引用——那会让工具和 Agent 耦合。`ToolContext` 是工具执行时拿到的上下文：

```python
@dataclass
class ToolContext:
    cwd: Path                      # 当前目录
    session_id: str
    read_file_state: dict          # 文件→mtime（先读后改）
    sandbox_manager: Any | None    # run_shell 走这里
    mcp_manager: Any | None        # MCP 工具走这里
    agent: Any | None              # agent/skill 工具走这里
```

普通工具只用 `cwd` 和 `read_file_state`。特殊工具通过 ToolContext 拿到自己的依赖。

### 2.3 执行管线

```
ToolRuntime.execute_one(call, ctx)
    ├── 1. ToolRegistry.find(name) → 找工具
    ├── 2. tool.validate(inp, ctx) → 参数校验（required 字段检查）
    ├── 3. PreToolUse hooks → 可返回 deny/modify
    ├── 4. check_permission() → 权限策略
    ├── 5. confirm callback → 用户确认（default 模式）
    ├── 6. tool.call(inp, ctx) → 执行
    ├── 7. _persist_large_result() → >30KB 落盘
    └── 8. PostToolUse hooks → 可返回 append_context
```

每步都可能打断——返回 `ToolResult(is_error=True)` 而非抛异常。打断不中断循环。

## 3. 总体设计

### 3.1 文件结构

```
capabilities/tools/
├── __init__.py       # 公共导出
├── types.py          # 数据模型 + 常量（types+base+constants 合并）
├── builtin.py        # 12 个内置工具 schema + 实现（definitions+builtin 合并）
├── registry.py       # ToolRegistry：注册/查找/激活
└── runtime.py        # ToolRuntime：执行管线
```

### 3.2 工具分类

| 常量 | 包含 | 用途 |
|------|------|------|
| `READ_TOOL_NAMES` | read_file, list_files, grep_search, web_fetch, MCP resources | default 模式自动 allow |
| `EDIT_TOOL_NAMES` | write_file, edit_file | acceptEdits 模式自动 allow |
| `CONCURRENCY_SAFE_BUILTIN_TOOLS` | read_file, list_files, grep_search, web_fetch, MCP resources | 可并行执行 |

### 3.3 12 个内置工具

| 工具 | 类型 | 执行路径 |
|------|------|---------|
| read_file | 只读/并发 | builtin.read_file() |
| write_file | 编辑 | builtin.write_file() → 触发 memory index 同步 |
| edit_file | 编辑 | builtin.edit_file() → 唯一匹配 + 智能引号归一化 |
| list_files | 只读/并发 | builtin.list_files() → glob 匹配 |
| grep_search | 只读/并发 | 优先系统 grep，fallback Python |
| run_shell | - | **必须通过 SandboxManager** |
| skill | - | → ctx.agent SkillInvocation |
| web_fetch | 只读/并发 | builtin.web_fetch() → urllib + HTML 剥离 |
| agent | - | → ctx.agent._execute_agent_tool() → SubAgentOrchestrator |
| tool_search | - | → ctx.agent ToolRegistry.search_deferred() |
| list_mcp_resources | 只读/并发 | → ctx.mcp_manager |
| read_mcp_resource | 只读/并发 | → ctx.mcp_manager |

## 4. 详细设计

### 4.1 types.py——所有数据结构

`ToolCall`：一次工具调用的参数化——id、name、input(dict)、provider("anthropic"|"openai")。

`ToolResult`：工具执行结果——content(字符串)、is_error(bool)、metadata(dict)、extra_messages(list)。`extra_messages` 是 PostToolUse hook 追加的系统上下文。

`FunctionTool`：基于函数的工具适配器。构造函数接收 `definition`（ToolDef dict）+ `call_fn`（可调用对象）+ 可选的 `read_only`/`edit_tool`/`concurrency_safe` 标记。`validate()` 检查 required 字段，`call()` 执行 call_fn 并转换为 ToolResult。

`Tool` Protocol：类型协议——`name`、`description`、`input_schema`、`origin`、`deferred`。`to_definition()` 返回传给模型的 ToolDef。

常量：`MAX_RESULT_CHARS=50000`（工具结果截断）、`LARGE_RESULT_BYTES=30KB`（落盘阈值）、所有压缩阈值、`DEFAULT_SHELL_TIMEOUT_MS=30000`、`MAX_RETRIES=3`。

### 4.2 builtin.py——12 个内置工具

`read_file`：读文件，返回带行号内容。成功读取后更新 `read_file_state[abs_path] = mtime`。

`write_file`/`edit_file`：先检查"先读后改"——文件是否在 `read_file_state` 中？mtime 是否匹配？两者有任一不满足则返回错误。`edit_file` 使用精确字符串匹配——`old_string` 必须唯一匹配（支持 Unicode 引号归一化）。

`grep_search`：非 Windows 优先使用系统 `grep`（参数列表，非 shell=True），fallback 到 Python 实现。系统 grep 更快，Python 实现跨平台。

`run_shell`：**不直接被 BUILTIN_HANDLERS 引用**。所有执行路径要求显式传入 sandbox backend/manager。直接调用会返回错误。

### 4.3 registry.py——ToolRegistry

两套内部数据：`_tools: dict[name, FunctionTool]`（工具实例）和 `_metadata: dict[name, ToolMetadata]`（元数据）。

`_build_tool()` 工厂函数：根据 `origin` 构建不同 FunctionTool。builtin/origin → `_call_builtin`（本地执行）。mcp → `_call_mcp`（委托 ctx.mcp_manager）。custom → 与 builtin 相同路径。

`active_definitions(denied)`：返回当前激活的工具列表。排除 deferred 未激活的、排除 skill 禁用的。

`search_deferred(query)`：按关键词搜索 deferred 工具。匹配后自动激活。支持 `select:tool1,tool2` 精确选择和 `+server keyword` 按 MCP server 过滤。

### 4.4 runtime.py——ToolRuntime

`execute_many(calls, ctx)`：按并发安全性分组 batch。`CONCURRENCY_SAFE_BUILTIN_TOOLS` 中的工具 `asyncio.gather` 并行，其余串行。

`execute_one(call, ctx)`：完整的 8 步管线。第 3 步 PreToolUse hook modify 后重新校验——防止 hook 修改输入导致不合法参数。

`_persist_large_result()`：结果超过 30KB 时写入 `~/.nanocode/tool-results/`，返回只保留前 200 行预览 + artifact 路径引用。

`execute_builtin_tool()`：旧代码兼容路径——不走完整管线，手动传参数。用于 `registry.py` 的 `_call_builtin` 和 Agent 旧路径的双重入口。

## 5. 设计决策

### 决策 1：为什么 schema 和实现在同一文件

原来 `definitions.py`（schema）和 `builtin.py`（实现）分开。加新工具必须同时改两个文件——它们共享同一个变更原因。合并后加工具只改 `builtin.py`。

### 决策 2：为什么 deferred 工具存在

MCP 可能有几十上百个工具。全部加载撑爆 system prompt + prompt cache。deferred 机制让工具默认不可见，模型通过 `tool_search` 按需激活。这是"上下文预算管理"伪装成了"工具系统功能"。

### 决策 3：为什么先读后改是不变量

模型不应在没见过文件内容时修改它。`read_file_state` 字典记录每个被读文件的 mtime。`write_file`/`edit_file` 执行前检查——文件是否被读过？mtime 是否被外部修改？任一不满足 → 返回错误，要求重新 `read_file`。

## 6. 面试考点

### Q1: 怎么加一个新工具？

`builtin.py`：在 `BUILTIN_TOOL_DEFINITIONS` 加 schema，下方加实现函数。如果是并发的加到 `CONCURRENCY_SAFE_BUILTIN_TOOLS`，如果会编辑文件加到 `EDIT_TOOL_NAMES`。

### Q2: tool_search 为什么存在？为什么不一次性加载所有工具？

MCP 可能有几十上百个工具。deferred 是上下文预算管理——让模型按需激活。

**追问"内置工具为什么不是 deferred？"**：内置工具只有 12 个——token 成本可忽略。deferred 的价值在于大规模工具（MCP）。

### Q3: run_shell 为什么必须通过 sandbox？

防止裸 `subprocess.run(shell=True)`。所有执行路径都要求显式传入 sandbox backend/manager——不传就返回错误。`builtin.py` 中的 `run_shell` 函数保留但不被 `BUILTIN_HANDLERS` 引用。

## 7. 代码导读

**阅读顺序**：`types.py`（理解数据结构）→ `registry.py`（理解注册机制）→ `builtin.py`（看具体工具）→ `runtime.py`（理解执行管线）。

**关键代码**：`types.py` ToolCall/ToolResult/ToolContext/FunctionTool 定义、`builtin.py` BUILTIN_TOOL_DEFINITIONS + write_file/edit_file 的先读后改检查、`registry.py` ToolRegistry.add_many() + _build_tool() 工厂、`runtime.py` ToolRuntime.execute_many() 并发分组 + execute_one() 8 步管线。
