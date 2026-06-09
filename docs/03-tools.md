# 工具系统

## 1. 为什么需要工具系统

LLM 只能生成文本。让它读文件、搜索代码、编辑文件、跑命令——这些是真实的 I/O 操作，不是文本生成能完成的。工具系统是 Agent 的"手"——把模型的文本意图翻译成文件读写、命令执行、网络请求。

设计核心理念有两条。**第一，普通工具无状态**。`read_file` 不应该记住"上次读了哪个文件"——那是 Agent 会话的事。工具本身是纯函数——输入参数，输出结果。**第二，运行时上下文通过注入而非全局变量传递**。`ToolContext` 携带 cwd、session_id、sandbox 等上下文，工具只通过 ToolContext 访问这些信息，不持有 Agent 引用。

工具系统的"客户"是模型——它通过 `input_schema`（JSON Schema）告诉模型"怎么调用我"。12 个内置工具覆盖了编程 Agent 的核心场景：文件操作（读、写、编辑）、搜索（glob、grep）、shell 执行、web 抓取、子 Agent 派发、skill 调用、MCP 资源访问、工具发现。

## 2. 核心概念

### 2.1 三层模型

```
┌─────────────────────────────────────────────┐
│ Schema 层（builtin.py）                      │
│   声明工具叫什么、参数是什么、参数怎么校验      │
│   12 个工具的 JSON Schema + Python 实现        │
├─────────────────────────────────────────────┤
│ Registry 层（registry.py）                   │
│   注册、查找、deferred 激活、MCP 合并          │
│   两套内部数据：_tools + _metadata            │
├─────────────────────────────────────────────┤
│ Runtime 层（runtime.py）                     │
│   执行管线：验证→权限→Hook→执行→后处理         │
│   并发调度：按 batch 分组，安全工具并行         │
└─────────────────────────────────────────────┘
```

这三层有独立的变更原因。改一个工具的 schema（增减参数）不改 registry。改 deferred 激活策略不改 runtime。改并发调度策略不改 schema。

### 2.2 ToolContext：依赖注入点

工具不应该持有 Agent 引用——那会让工具和 Agent 循环耦合，而且测试工具时需要构造完整 Agent 实例（重）。`ToolContext` 是依赖注入点：

```python
@dataclass
class ToolContext:
    cwd: Path                        # 当前目录
    session_id: str                  # 会话 ID
    read_file_state: dict[str, float]  # 文件→mtime（先读后改）
    sandbox_manager: Any | None      # → run_shell
    mcp_manager: Any | None          # → MCP 工具
    agent: Any | None                # → agent/skill 工具（弱引用）
```

普通工具（read_file、grep_search、list_files、web_fetch）只用到 `cwd` 和 `read_file_state`。特殊工具各取所需：run_shell 通过 `sandbox_manager` 执行，MCP 工具通过 `mcp_manager` 调用，agent 和 skill 工具通过 `agent` 引用（`Any` 类型，避免循环导入）委托给 Agent 的方法。

### 2.3 执行管线

一个工具调用经过 8 步，每步都可能打断（返回 `ToolResult(is_error=True)` 而非抛异常）：

```
ToolRuntime.execute_one(call, ctx)
    ├── 1. ToolRegistry.find(name)
    │      找不到 → 返回 "Unknown tool: {name}"
    ├── 2. tool.validate(inp, ctx)
    │      检查 required 字段 → 缺失 → 返回 "missing required field: {key}"
    ├── 3. PreToolUse hooks
    │      hook.output.action == "deny" → 返回 "Action denied by hook"
    │      hook.output.action == "modify" → inp = updated_input
    │      → 重新 validate（防止恶意修改）
    ├── 4. check_permission(tool_name, inp, mode)
    │      deny → 返回 "Action denied: {reason}"
    │      confirm → 进入步骤 5
    ├── 5. confirm callback（用户确认）
    │      拒绝 → 返回 "User denied this action"
    ├── 6. tool.call(inp, ctx)
    │      异常 → 返回 ToolResult("Error: {e}", is_error=True)
    ├── 7. _persist_large_result()
    │      >30KB → 落盘 + 只返回预览
    └── 8. PostToolUse hooks
           append_context → result.extra_messages 追加
```

打断不抛异常。工具错误以 `ToolResult(is_error=True)` 的形式返回给模型——模型看到错误描述后可以调整策略。抛异常会跳出循环——那是系统级错误（如 API 调用本身失败）。

### 2.4 并发安全与 batch 分组

不是所有工具都能并行。`read_file`、`list_files`、`grep_search`、`web_fetch` 是只读的——它们只消费数据，不修改状态——可以安全并行。`write_file`、`edit_file`、`run_shell` 会修改文件或系统状态——必须串行。

`ToolRuntime.execute_many(calls, ctx)` 按并发安全性分组：

```python
batches = []
for call in calls:
    safe = registry.is_concurrency_safe(call.name, call.input)
    if safe and batches and batches[-1]["concurrent"]:
        batches[-1]["items"].append(call)   # 追加到当前并发 batch
    else:
        batches.append({"concurrent": safe, "items": [call]})  # 新 batch
```

并发安全的工具放入同一个 batch，用 `asyncio.gather(*[_run(c) for c in batch])` 并行执行。不安全的串行执行。这个分组策略是贪婪的——连续的并发工具合并，遇到非并发工具就切分。

## 3. 总体设计

### 3.1 文件结构

```
capabilities/tools/
├── __init__.py       # 公共导出
├── types.py          # 数据模型 + 全部常量（types+base+constants 合并，~170 行）
├── builtin.py        # 12 个内置工具的 schema + 实现（definitions+builtin 合并，~440 行）
├── registry.py       # ToolRegistry：注册/查找/激活（~330 行）
└── runtime.py        # ToolRuntime：执行管线 + 并发调度（~250 行）
```

### 3.2 合并记录

| 合并前 | 合并后 | 理由 |
|--------|--------|------|
| types.py + base.py + constants.py | types.py | 改 ToolCall 结构时三者总是一起改 |
| definitions.py + builtin.py | builtin.py | 加工具时 schema 和实现必须同时改 |
| permissions.py | 移到 capabilities/permissions/ | 权限系统独立出来 |

### 3.3 工具分类

| 常量 | 包含 | 作用 |
|------|------|------|
| `READ_TOOL_NAMES` | read_file, list_files, grep_search, web_fetch, MCP resources | default 模式自动 allow |
| `EDIT_TOOL_NAMES` | write_file, edit_file | acceptEdits 模式自动 allow |
| `CONCURRENCY_SAFE_BUILTIN_TOOLS` | read_file, list_files, grep_search, web_fetch, MCP resources | 可并行 |

### 3.4 12 个内置工具一览

| # | 工具 | 分类 | 执行路径 |
|:--:|------|------|---------|
| 1 | read_file | 只读/并发 | builtin.read_file() → 返回带行号内容 |
| 2 | write_file | 编辑 | builtin.write_file() → 先读后改检查 → 写文件 + memory sync |
| 3 | edit_file | 编辑 | builtin.edit_file() → old_string 唯一匹配 + 智能引号归一化 |
| 4 | list_files | 只读/并发 | builtin.list_files() → glob 匹配 + 过滤 |
| 5 | grep_search | 只读/并发 | 优先系统 grep(参数列表非 shell)，fallback Python |
| 6 | run_shell | - | **必须通过 SandboxManager**，不传则返回错误 |
| 7 | skill | - | → ctx.agent SkillInvocation.invoke() |
| 8 | web_fetch | 只读/并发 | builtin.web_fetch() → urllib + HTML 标签剥离 |
| 9 | agent | - | → ctx.agent._execute_agent_tool() → SubAgentOrchestrator |
| 10 | tool_search | - | → ctx.agent ToolRegistry.search_deferred() |
| 11 | list_mcp_resources | 只读/并发 | → ctx.mcp_manager.list_resources() |
| 12 | read_mcp_resource | 只读/并发 | → ctx.mcp_manager.read_resource() |

## 4. 详细设计

### 4.1 types.py——数据模型 + 全部常量

核心类型：

**`ToolCall`**（frozen dataclass）：`id`（模型生成的 tool_use id）、`name`（工具名）、`input`（dict，工具参数）、`provider`（"anthropic"|"openai"|"model"）。

**`ToolResult`**：`content`（字符串输出）、`is_error`（是否错误）、`metadata`（dict，额外信息如 saved_files）、`extra_messages`（list，PostToolUse hook 追加的上下文）。

**`ToolContext`**：依赖注入点（见 2.2）。`agent: Any` 是弱引用——不 import Agent 类，避免循环依赖。

**`FunctionTool`**：基于函数的工具适配器。包装一个 `call_fn(inp, ctx) → ToolResult | str`。`validate()` 检查 required 字段。`is_read_only()`/`is_edit_tool()`/`is_concurrency_safe()` 通过构造函数参数或可调用对象判断。

常量集合：`MAX_RESULT_CHARS=50000`（结果截断）、`LARGE_RESULT_BYTES=30KB`（落盘阈值）、压缩阈值（`BUDGET_UTILIZATION_THRESHOLD=0.5` 等）、`DEFAULT_SHELL_TIMEOUT_MS=30000`、`MAX_RETRIES=3`、`CONTEXT_WINDOW_MARGIN=20000` 等。

### 4.2 builtin.py——12 个内置工具

**read_file**：读取文件，每行加 `{i:4d} | ` 前缀（行号格式）。成功读取后更新 `read_file_state[abs_path] = mtime`——这是先读后改不变量的一部分。

**write_file/edit_file**：执行前先检查"这个文件在我上次读它之后有没有被外部修改？"。`read_file_state` 中没有这个文件的记录 → "请先 read_file"。有记录但 mtime 不匹配 → "文件被外部修改，请重新 read_file"。edit_file 额外要求 `old_string` 在文件中唯一匹配——出现 0 次返回 not found，出现 2 次返回 not unique。支持 Unicode 引号归一化（`'` ↔ `'`、`"` ↔ `"`）。

**grep_search**：非 Windows 优先用系统 `grep --line-number --color=never -r`（参数列表形式，**不是 shell=True**），fallback 到 Python 实现的 `_grep_python()`。系统 grep 更快，Python 实现保证跨平台行为一致且可用。

**run_shell**：函数体仍在 `builtin.py` 中，但 `BUILTIN_HANDLERS` 字典不引用它。所有执行路径都要求显式传入 sandbox backend——`ToolRegistry._call_builtin` 通过 `ctx.sandbox_manager` 执行，`execute_builtin_tool` 通过 `execution_backend` 参数执行。直接调用 `builtin.run_shell(inp)` 会裸执行命令——这是禁止的。

**web_fetch**：用标准库 `urllib.request`（不引入 `requests`/`httpx` 依赖）。HTML 响应自动剥离标签（`<script>`、`<style>`、所有 HTML 标签），替换 HTML 实体，合并空白。默认最大 50K 字符。

### 4.3 registry.py——ToolRegistry

**两套内部数据**：

- `_tools: dict[name, FunctionTool]`——工具实例。`find(name)` 从这里查。
- `_metadata: dict[name, ToolMetadata]`——元数据。包含 `origin`（builtin/mcp/custom）、`deferred`（是否延迟加载）、`concurrency_safe`、`read_only`、`edit_tool`、`raw`（原始 ToolDef dict 中 `INTERNAL_SCHEMA_KEYS` 的字段）。

**`_build_tool()` 工厂**：根据 `origin` 构建不同的 FunctionTool：
- `builtin` → `_call_fn` 是 `_call_builtin(name, inp, ctx)`
- `mcp` → `_call_fn` 是 `_call_mcp(name, inp, ctx)`——委托 `ctx.mcp_manager.call_tool()`
- `custom` → 同 builtin 路径

**deferred 机制**：`active_definitions(denied)` 返回的列表中，deferred 工具只有在 `_activated_deferred` 集合中才会出现。默认内置工具都不是 deferred。MCP 工具默认 deferred=True——模型看不到它们的完整 schema，直到通过 `tool_search` 激活。`search_deferred(query)` 支持三种搜索方式：`select:tool1,tool2`（精确选择）、`+server keyword`（按 MCP server 过滤）、裸关键词搜索。

**sanitize**：`sanitize_tool_definition(tool)` 在传给模型前剥离 `INTERNAL_SCHEMA_KEYS`（deferred、origin、concurrency_safe 等内部字段）——模型只需要 name、description、input_schema。

### 4.4 runtime.py——ToolRuntime

**`execute_many(calls, ctx)`**：按并发安全性分组（见 2.4）。并发工具只能和并发工具在一个 batch，非并发工具必须单独执行。分组策略是贪婪的——连续并发工具合并为 batch。

**`execute_one(call, ctx)`**：完整的 8 步管线（见 2.3）。Steps 3 的 hook modify 后重新校验是关键安全约束——hook 是用户脚本，可能写错。修改后的输入如果校验失败，在 PreToolUse 阶段就阻断，而不是等到工具执行时报错。

**`_persist_large_result()`**：结果 >30KB 时写入 `~/.nanocode/tool-results/{timestamp}-{tool_name}.txt`，消息历史中只保留前 200 行预览 + artifact 路径引用。`result.metadata["full_result_path"]` 记录完整路径。

**`execute_builtin_tool()`（module-level 函数）**：旧代码兼容路径。不走 ToolRuntime 的完整管线（没有权限检查、没有 hooks），直接执行工具。`registry.py` 的 `_call_builtin` 在内部使用这个函数。`run_shell` 通过 `execution_backend` 参数获取 sandbox 引用——为 None 则返回错误。

## 5. 设计决策

### 决策 1：为什么 schema 和实现在同一文件

**问题**：原来 `definitions.py`（schema）和 `builtin.py`（实现）分开。

**选择**：合并到 `builtin.py`。加新工具时 schema 和实现必须同时改——它们共享同一个变更原因（"我想加一个工具"）。

**代价**：`builtin.py` 440 行，相对较大。但这 440 行是高度重复的结构——每个工具的 schema 格式相同、实现模式相同。阅读时不是线性读完，而是跳到关心的工具。

### 决策 2：为什么 deferred 工具存在

**问题**：MCP 可能有几十上百个工具。全部加载会撑爆 system prompt（token 成本）和 Anthropic 的工具缓存（tool schema 变化导致 cache miss）。

**选择**：deferred 默认不可见，模型通过 `tool_search` 按需激活。`_activated_deferred` 集合在会话期间跟踪激活状态。

**为什么这是"上下文预算管理"伪装成了"工具系统功能"**：deferred 的真正价值不是"按需发现"（模型本来就能看到所有工具），而是"避免一次性加载几百个工具 schema 浪费 token"。

### 决策 3：为什么先读后改是不变量

**问题**：模型能否直接写一个它从没读过的文件？

**选择**：不能。`write_file`/`edit_file` 执行前检查 `read_file_state` 字典——文件必须被读过，且读取后的 mtime 没有被外部修改。

**为什么是不可协商的**：不基于当前文件内容做修改是编程 Agent 最基本的正确性保证。模型可能基于过时的记忆或训练数据中的模式来"猜测"文件内容——但这不可靠。强制先读后改也防止模型创建不存在的文件路径（`edit_file("不存在.py", ...)` → 确认提示）。

### 决策 4：为什么并发安全的工具列表是硬编码的

**问题**：能不能让工具自己声明"我是并发安全的"？

**选择**：`CONCURRENCY_SAFE_BUILTIN_TOOLS` 是显式的集合常量。`FunctionTool` 的 `concurrency_safe` 属性从 `ToolMetadata` 中读取——而 metadata 的 `concurrency_safe` 在注册时通过 `_build_tool()` 的常量匹配判断。

**为什么不是运行时判断**：一个工具是否并发安全取决于它的语义（是否修改状态），不是运行时条件。`read_file` 在并发时总是安全的，`write_file` 在并发时总是不安全的——不需要"运行时检查参数来决定"的灵活性。硬编码更简单、更快、更不容易出错。

## 6. 面试考点

### Q1: 12 个内置工具怎么分类？

按只读/编辑/并发三个维度交叉分类。只读+并发的（read_file、list_files、grep_search、web_fetch）→ default 模式自动 allow + 可并行执行。编辑的（write_file、edit_file）→ 需要确认 + 必须串行 + 先读后改。特殊的（run_shell、agent、skill）→ 各自走不同的执行路径（sandbox、orchestrator、skill invocation）。

### Q2: tool_search 为什么存在？

MCP 可能暴露几十上百工具——全部加载浪费 token。deferred 机制让工具默认不可见，`tool_search` 是激活入口。这是上下文预算管理伪装成了工具系统功能。内置工具不需要 deferred——它们只有 12 个。

### Q3: 先读后改怎么实现？

`read_file_state: dict[str, float]` 记录每个被读文件的绝对路径→mtime。`write_file`/`edit_file` 执行前查这个 dict——文件在不在？mtime 匹不匹配？不在 → "请先 read_file"。mtime 变了 → "被外部修改，请重新读取"。

**追问"两个子 Agent 同时读同一文件怎么办"**：各自有独立的 `read_file_state`（独立的 Agent 实例）。读了之后外部修改在各自状态下都能检测到。不存在冲突——它们是独立的消息历史。

### Q4: run_shell 为什么不在 BUILTIN_HANDLERS 里？

所有 run_shell 执行路径都要求显式传入 sandbox backend。`BUILTIN_HANDLERS` 是直接映射到函数的——没有机会传入 sandbox。不引用它是设计选择，不是遗漏。如果有人不小心加了引用，裸 shell 执行会绕过所有安全防护——所以要显式排除。

### Q5: execute_builtin_tool 和 ToolRuntime.execute_one 是什么关系？

两套路径。`execute_builtin_tool` 是旧代码的兼容路径——不走完整管线（没有 hooks、不经过 ToolRuntime 的权限和确认逻辑），直接执行工具。`ToolRuntime.execute_one` 是新的完整路径——8 步管线全部经过。`registry.py` 的 `_call_builtin` 内部调用 `execute_builtin_tool`（兼容），外部通过 `ToolRuntime` 调用走完整路径。两条路径的行为需要保持一致——改一个要改另一个。

## 7. 代码导读

**阅读顺序**：`types.py`（数据模型 + 常量）→ `registry.py`（注册机制 + deferred）→ `builtin.py`（看具体工具实现）→ `runtime.py`（执行管线）。

**关键行号**：
- `types.py:13-38`——ToolCall/ToolResult/ToolContext 定义
- `types.py:70-137`——FunctionTool 完整实现
- `types.py:140-190`——全部常量定义
- `builtin.py:40-175`——BUILTIN_TOOL_DEFINITIONS（12 个 schema）
- `builtin.py:267-302`——read_file 实现（含先读后改标记）
- `builtin.py:317-348`——edit_file 实现（old_string 唯一匹配 + 引号归一化）
- `registry.py:185-233`——ToolRegistry.add_many() 工厂方法
- `registry.py:131-168`——_build_tool() 按 origin 分发
- `registry.py:275-318`——search_deferred() 三种搜索模式
- `runtime.py:61-82`——execute_many() 并发分组
- `runtime.py:94-158`——execute_one() 完整 8 步管线
