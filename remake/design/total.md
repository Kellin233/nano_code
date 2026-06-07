# Nano Code 总体重构方案

## 结论

本次重构目标不是把 `nano_code` 改成 Claude Code 的等比例复刻，而是把当前轻量 agent runtime 的核心边界理顺。

推荐路线：

```text
事件流主循环 + 强 Tool 契约 + Hooks + 更强安全
```

重构要保持简洁务实。不要大重写，不要为了抽象而抽象。先建立清晰边界，再逐步把现有逻辑迁入新边界。

## 当前问题

当前实现已经比最小 demo 更完整：`Agent` 只是状态容器，模型后端、上下文、工具运行时拆到了 mixin；memory、skill、sub-agent、MCP、sandbox 也各有模块。

但结构上仍有几个问题：

- 主循环、工具权限、UI 输出、工具执行编排仍然耦合在 backend loop 里。
- 工具系统主要是 dict schema + registry metadata + handler 分发，工具行为契约不够强。
- 权限系统偏轻量，`bypassPermissions` 当前早于 deny 规则生效，安全边界过软。
- prompt 里提到 hooks，但代码没有 hook runtime，存在文档/行为漂移。
- `docs/10-plan-mode.md` 描述全局 Plan Mode，但源码已按 `remake/design/plan_mode.md` 删除，只保留 `plan` 子 agent。
- sandbox 主要约束 `run_shell`，文件工具仍直接操作宿主文件系统，安全语义需要说清楚。

## 总体设计

目标架构：

```text
CLI / UI
  ↓ consume events
SessionEngine
  ↓ owns session, budget, save/restore
AgentLoop async generator
  ↓ model stream + tool execution + context compression
ToolRuntime
  ↓ unified Tool contract + permission + hooks + sandbox
Builtin / MCP / Skill / Agent tools
```

职责边界：

- `CLI / UI`：只消费事件并展示，不参与业务决策。
- `SessionEngine`：管理一次用户请求的生命周期，包括预算、保存、恢复、结果组装。
- `AgentLoop`：实现模型调用、工具调用、上下文压缩和循环继续/终止。
- `ModelBackend`：只负责和 Anthropic/OpenAI 协议交互，输出统一的模型事件。
- `ToolRuntime`：统一执行工具管线，负责 validation、permission、hooks、execute、result shaping。
- `Tool`：封装单个工具的 schema、权限语义、并发语义、执行逻辑。
- `Hooks`：作为工具执行和用户输入生命周期的扩展点，不进入核心业务逻辑。
- `Permissions`：统一安全策略入口，所有工具必须经过它。

设计原则：

- 兼容优先：短期保留 `Agent.chat()` 和 `Agent.run_once()`。
- 渐进迁移：先加新边界，再把旧逻辑迁进去。
- 错误是数据：工具错误返回给模型，不轻易中断 loop。
- 安全由代码强制：prompt 只做引导，不能作为唯一防线。
- 热路径简单：未配置 hooks 时，工具执行路径不能有明显额外开销。
- 不强行统一协议：Anthropic 和 OpenAI 消息格式分别维护，避免破坏 tool call 配对。

## 模块划分

建议新增或调整模块：

```text
nano_code/agent/
  engine.py        # SessionEngine: chat入口、预算、保存、恢复
  loop.py          # AgentLoop: async generator 主循环
  events.py        # 事件类型
  state.py         # LoopState / SessionState
  backends.py      # Anthropic/OpenAI backend adapter，只负责模型流

nano_code/tools/
  base.py          # Tool Protocol, ToolCall, ToolResult, ToolContext
  registry.py      # 注册、查找、deferred、MCP合并
  runtime.py       # ToolRuntime: permission -> hooks -> execute -> hooks
  builtin/         # 后续逐步拆 read/edit/shell/web

nano_code/permissions/
  policy.py        # check_permission 统一入口
  rules.py         # settings allow/deny
  shell.py         # shell safety parser/AST/正则兜底
  workspace.py     # workspace边界和保护路径

nano_code/hooks/
  config.py        # hooks配置加载和快照
  runner.py        # command hook执行
  types.py         # HookInput/HookOutput
```

短期可以保留旧模块路径，并通过 adapter 兼容：

- `nano_code.agent.Agent` 继续作为外部入口。
- `tools.definitions` 继续提供旧 schema，但逐步迁移到 `Tool` 对象。
- `tools.permissions` 可以先 re-export 新 `permissions.policy.check_permission`。
- `agent/backends.py` 先保留 API stream 逻辑，但去掉工具执行和 UI 打印。

## 事件流主循环详细设计

### 事件类型

先定义少量稳定事件，不复刻完整 Claude Code 事件集。

```python
@dataclass
class AssistantTextDelta:
    text: str

@dataclass
class ToolCallStarted:
    call: ToolCall

@dataclass
class ToolCallFinished:
    call: ToolCall
    result: ToolResult

@dataclass
class PermissionRequested:
    call: ToolCall
    message: str

@dataclass
class ContextCompacted:
    reason: str

@dataclass
class ApiRetry:
    attempt: int
    reason: str

@dataclass
class LoopFinished:
    stop_reason: str
```

后续按需增加：

- `ToolCallDenied`
- `HookStarted`
- `HookFinished`
- `MemoryInjected`
- `BudgetExceeded`
- `ModelThinkingDelta`

### AgentLoop

`AgentLoop.run()` 是 `async generator`：

```python
async def run(self, user_message: str) -> AsyncIterator[AgentEvent]:
    ...
```

执行流程：

1. 把用户消息加入当前后端消息历史。
2. 启动 memory prefetch，但不阻塞首 token。
3. 在回合边界检查是否需要 compact。
4. 每轮开始执行轻量压缩流水线。
5. 调用 model backend，边收文本边 yield `AssistantTextDelta`。
6. 收集完整 tool calls。
7. 没有 tool calls 时 yield `LoopFinished` 并结束。
8. 有 tool calls 时交给 `ToolRuntime.execute_many()`。
9. 把 tool results 按后端协议回灌消息历史。
10. 继续下一轮。

`AgentLoop` 不直接 `print_*`，不直接读 stdin，不直接写 session 文件。

### SessionEngine

`SessionEngine` 负责外层生命周期：

- MCP 懒连接。
- 调用 `AgentLoop.run()`。
- 消费事件并交给 UI sink。
- 统计 token/cost/turn。
- 检查 `max_cost_usd` 和 `max_turns`。
- 保存和恢复 session。
- 保持 `Agent.chat()` 兼容。

短期 `Agent.chat()` 可以这样实现：

```python
async def chat(self, user_message: str) -> None:
    async for event in self._engine.submit(user_message):
        self._ui.render(event)
```

### ModelBackend

后端只负责模型协议：

- Anthropic：stream text/thinking/tool_use blocks。
- OpenAI-compatible：stream content/tool_calls。
- 统一产出 model-level events 或返回 `ModelTurn`。

它不能：

- 执行工具。
- 做权限确认。
- 打印 UI。
- 保存会话。

当前 `agent/backends.py` 需要拆掉工具执行部分。Anthropic 的 early execution 逻辑可以保留思路，但执行入口应交给 `ToolRuntime`。

## 强 Tool 契约详细设计

### Tool Protocol

目标接口：

```python
class Tool(Protocol):
    name: str
    description: str
    input_schema: dict
    origin: ToolOrigin
    deferred: bool

    def is_read_only(self, inp: dict) -> bool:
        ...

    def is_concurrency_safe(self, inp: dict) -> bool:
        ...

    async def validate(self, inp: dict, ctx: ToolContext) -> ValidationResult:
        ...

    async def call(self, inp: dict, ctx: ToolContext) -> ToolResult:
        ...
```

`ToolContext`：

```python
@dataclass
class ToolContext:
    cwd: Path
    session_id: str
    read_file_state: dict[str, float]
    sandbox_manager: SandboxManager
    mcp_manager: McpManager | None = None
    emit: Callable[[AgentEvent], Awaitable[None]] | None = None
```

`ToolCall`：

```python
@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict
    provider: str
```

`ToolResult`：

```python
@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    metadata: dict = field(default_factory=dict)
    extra_messages: list[dict] = field(default_factory=list)
```

`ValidationResult`：

```python
@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    message: str = ""
    updated_input: dict | None = None
```

### ToolRuntime

统一工具执行管线：

```text
ToolRuntime.execute_one(call)
  -> registry.find(call.name)
  -> validate schema/basic input
  -> tool.validate()
  -> permission precheck
  -> PreToolUse hooks
  -> final permission / user confirm
  -> tool.call()
  -> persist large result
  -> PostToolUse hooks
  -> ToolResult
```

任何工具都不能绕过 `ToolRuntime`。

`execute_many()` 负责并发：

- 连续的 concurrency-safe 工具可以 batch 并行。
- 非 concurrency-safe 工具独占执行。
- 结果顺序必须按原 tool call 顺序回灌。
- 失败结果也必须占位，不能丢失对应 tool_use id。

### 迁移策略

第一步：写 `FunctionTool` adapter 包住现有 handler。

```python
class FunctionTool:
    def __init__(self, defn: dict, handler: Callable[..., Awaitable[str] | str]):
        ...
```

第二步：优先拆复杂工具：

1. `run_shell`
2. `edit_file`
3. `write_file`
4. `agent`
5. `skill`
6. `mcp`

第三步：`ToolRegistry` 管理 `Tool` 对象，而不是 dict。

registry 职责：

- `find(name)`
- `active_definitions(denied)`
- `deferred_names()`
- `search_deferred(query)`
- origin 分区排序
- name 去重，内置工具优先

## Hooks 详细设计

### MVP 事件

先实现 4 个事件：

```text
UserPromptSubmit
PreToolUse
PostToolUse
Stop
```

暂不实现：

- HTTP Hook
- Prompt Hook
- Agent Hook
- asyncRewake
- session-scoped function hook
- skill-level hook

### 配置格式

从 `~/.claude/settings.json`、`.claude/settings.json` 读取：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "run_shell",
        "command": "./hooks/check-shell.py",
        "timeout_ms": 3000
      }
    ]
  }
}
```

`matcher` 先只支持：

- 空：匹配全部。
- 工具名精确匹配。
- `*`：匹配全部。

后续再扩展 `if` 条件和权限规则语法。

### Hook 输入

通过 stdin JSON 传给子进程：

```json
{
  "event": "PreToolUse",
  "session_id": "abc123",
  "cwd": "/repo",
  "tool_name": "run_shell",
  "tool_input": {
    "command": "pytest"
  }
}
```

`UserPromptSubmit`：

```json
{
  "event": "UserPromptSubmit",
  "session_id": "abc123",
  "cwd": "/repo",
  "prompt": "fix tests"
}
```

`PostToolUse`：

```json
{
  "event": "PostToolUse",
  "session_id": "abc123",
  "cwd": "/repo",
  "tool_name": "edit_file",
  "tool_input": {},
  "tool_result": {
    "is_error": false,
    "content": "Successfully edited ..."
  }
}
```

`Stop`：

```json
{
  "event": "Stop",
  "session_id": "abc123",
  "cwd": "/repo",
  "last_assistant_text": "Done"
}
```

### Hook 输出

MVP 支持：

```json
{
  "action": "allow"
}
```

```json
{
  "action": "deny",
  "reason": "git push is blocked"
}
```

```json
{
  "action": "modify",
  "updated_input": {
    "command": "git push --dry-run"
  }
}
```

```json
{
  "action": "append_context",
  "content": "Current lint warnings: ..."
}
```

处理规则：

- `PreToolUse allow`：继续执行。
- `PreToolUse deny`：返回 denied tool result 给模型。
- `PreToolUse modify`：使用 `updated_input` 替换工具输入。
- `PostToolUse append_context`：追加上下文给下一轮模型。
- `Stop deny`：阻止终止，向模型追加 hook feedback 后继续 loop。

非 JSON 输出：

- MVP 中按 hook 失败处理，不作为上下文注入。
- hook 失败不应默认阻塞工具，除非配置 `fail_closed: true`。

### Hook 安全

Hook 本身是 RCE 风险，必须加边界：

- 配置启动时快照，不在工具调用热路径反复读 settings。
- 默认只有 trusted workspace 才执行项目级 hooks。
- 没有 trust 机制前，项目级 hooks 默认禁用，只允许用户级 hooks，或者首次提示用户确认。
- hook command 超时后杀进程。
- hook 子进程只接收 JSON，不暴露 Python 对象。
- 不允许 hook 动态修改当前 hooks 配置快照。

## 更强安全详细设计

### 权限优先级

当前 `bypassPermissions` 直接放行，风险过高。

目标顺序：

```text
protected path / workspace boundary
  ↓
deny rules
  ↓
bypassPermissions
  ↓
allow rules
  ↓
read-only auto allow
  ↓
mode policy
  ↓
danger classifier / shell safety
  ↓
user confirm
```

要点：

- deny 规则必须优先于 `bypassPermissions`。
- protected path 不应被 yolo 绕过。
- read-only 是工具级和输入级判断，不是工具名硬编码。

### Protected Paths

默认保护：

```text
.git/
.claude/settings.json
.mcp.json
.env
.env.*
id_rsa
id_ed25519
known_hosts
authorized_keys
```

策略：

- 写入 protected path：默认 confirm，关键路径可 deny。
- 删除 protected path：默认 deny。
- 读取 secret-like 文件：默认 confirm 或 deny，避免模型泄露凭据。

### Workspace Boundary

默认文件工具只能访问 workspace 内路径。

策略：

- workspace 内普通文件：按现有规则。
- workspace 外路径：confirm。
- home 下敏感目录：deny 或 confirm。
- 临时工具结果目录要纳入可读策略，否则模型无法读取大结果落盘文件。

### Shell Safety

分阶段增强：

第一阶段：

- 保留现有正则危险检测。
- 增加常见缺口：`find ... -delete`、`curl|sh`、`wget|sh`、重定向覆盖系统路径、`chmod -R 777`、`chown -R`。
- 解析失败或命令过复杂时 confirm。

第二阶段：

- 引入 `bashlex` 或 tree-sitter bash。
- 识别 pipeline、command substitution、subshell、redirect。
- 解析失败 fail-closed 到 confirm。

第三阶段：

- 将 shell safety 结果结构化：

```python
@dataclass
class ShellSafetyResult:
    level: Literal["safe", "confirm", "deny"]
    reason: str
    commands: list[str]
```

### Sandbox 语义

当前 sandbox 主要约束 `run_shell`。文档和 prompt 不能暗示文件编辑也在 sandbox 内，除非后续真的实现。

后续可选方向：

- 文件工具仍直接写宿主，但受 workspace/protected path policy 限制。
- shell 工具通过 microsandbox 运行。
- 若开启 readonly workspace sandbox，shell 内不能写 workspace；但 `edit_file` 仍可写宿主，需要权限层明确处理。

## Context 和 Memory 调整

当前上下文压缩可保留，但要纳入事件流。

要求：

- compact 事件 yield `ContextCompacted`。
- 压缩不破坏 tool_use/tool_result 配对。
- Anthropic 和 OpenAI 消息合法性分别维护。
- active skills compact 后继续重挂。
- memory prefetch 不阻塞首 token。

改进方向：

- 将 `_run_compression_pipeline()` 变成可测试的 `ContextManager.apply(messages, pressure)`。
- 大结果落盘统一由 `ToolRuntime` 处理。
- 大结果路径要能被 `read_file` 读取，或提供专门 `read_tool_result` 工具。

## Prompt 和文档调整

必须先修正漂移：

- 如果 hooks 没实现，从 system prompt 删除 hooks 相关说明。
- `docs/10-plan-mode.md` 标记为 deprecated，或从 sidebar 移除。
- 明确当前只有 `plan` 子 agent，不存在全局 Plan Mode。
- sandbox 文档明确只约束 shell。

Prompt caching 后续再做：

- 将 system prompt 拆成 static/dynamic section。
- static：身份、行为规则、工具使用原则。
- dynamic：cwd/date/git/CLAUDE.md/memory/skills/agents/deferred tools。
- Anthropic 下对稳定 section 加 cache_control。
- 工具排序保持内置工具前缀稳定，MCP 工具后缀追加。

## 硬性约束

- 不能破坏现有 CLI：`nano-code "prompt"`、REPL、`--resume`、`--api-base` 必须继续可用。
- `Agent.chat()` 和 `Agent.run_once()` 短期保持兼容。
- 子 agent 和 fork skill 继续可用。
- Anthropic 和 OpenAI 消息格式分别维护，不强行统一成一个可逆中间格式。
- 压缩不能破坏 tool_use/tool_result 配对。
- MCP 工具名继续使用 `mcp__server__tool`。
- 工具错误必须回灌给模型，不能随意抛出导致 loop 终止。
- hooks 默认关闭，未配置时热路径开销接近零。
- 所有工具调用必须经过 `ToolRuntime`。
- 安全策略必须在代码层强制，不能只靠 prompt。

## 隐含要求

- 文档、prompt、代码行为必须一致。
- 用户拒绝工具后，模型不能反复尝试同一调用。
- 大结果不能无限进入上下文。
- 子 agent 不应污染主 agent 的 memory budget。
- 自定义 agent 不能递归无限创建子 agent。
- deferred tools 激活后 schema 变化要可控，避免破坏 prompt/cache 稳定性。
- hook 配置要快照化，避免当前会话被工具调用动态改变。
- permission result 要结构化，便于 UI、hooks、测试复用。

## 不能做什么

- 不要一次性重写所有文件。
- 不要把事件系统做成复杂 event bus，`async generator` 足够。
- 不要引入重型 DI/container/plugin 框架。
- 不要让 hook 直接拿 Python 对象或内部状态。
- 不要让 Tool 自己做 UI 输入输出。
- 不要让 Tool 绕过统一 permission/runtime 入口。
- 不要为了“统一协议”破坏 Anthropic/OpenAI 原生消息合法性。
- 不要把安全策略只写进 prompt。
- 不要让 `bypassPermissions` 绕过 deny/protected path。
- 不要把 Plan Mode 作为全局状态加回来；需要规划能力时用 `plan` 子 agent、skill 或 task tracker。
- 不要在实现 hooks 前继续让 prompt 声称支持 hooks。

## 可能踩坑

- 早期执行只读工具时，tool call id 和提前任务结果必须严格对应。
- 并发工具执行不能改变 tool result 回灌顺序。
- Anthropic 的 `tool_use` 和 `tool_result` 配对一旦被 compact 切断，后续 API 会失败。
- OpenAI 的 `assistant.tool_calls` 和 `role=tool` 同样必须成对。
- Hook stdout 可能不是 JSON，需要明确失败策略。
- Command hook 是 RCE 风险，没有 trust 机制时不要默认执行项目级 hooks。
- `bypassPermissions` 命名会诱导全跳过，但产品安全上必须保留硬边界。
- 大结果落盘到 workspace 外后，模型可能无法用 `read_file` 读取。
- memory prefetch 是旁路模型调用，事件化后不要阻塞主模型首 token。
- sandbox readonly workspace 只限制 shell，不限制宿主文件工具。
- MCP server stderr 如果不消费，长时间运行可能阻塞；后续需要处理 stderr drain。
- Tool schema 从 dict 迁移到对象时，MCP/OpenAI schema 转换容易漏字段。
- skill allowed/disallowed tools 与 active denied tools 合并时，要避免把必要工具误删。

## 实施顺序

### 阶段 0：对齐文档和测试

目标：先建立安全网，避免重构时行为漂移。

任务：

- 标记或修正 Plan Mode 文档。
- 从 prompt 删除未实现 hooks 的描述，或明确“hooks 尚未实现”。
- 补测试：
  - permission deny 优先级
  - `bypassPermissions` 不绕过 protected path
  - Anthropic tool_use/tool_result compact 配对
  - OpenAI tool_calls/tool messages 配对
  - skill fork 仍可用
  - MCP tool name routing
  - large result persist

### 阶段 1：引入事件类型和 SessionEngine

目标：不改变外部行为，只把输出改为事件驱动。

任务：

- 新增 `agent/events.py`。
- 新增 `agent/engine.py`。
- `Agent.chat()` 消费 engine events 并调用现有 UI print。
- 后端暂时仍可复用旧逻辑，但逐步停止直接打印。

验收：

- CLI 行为基本不变。
- REPL 可用。
- 子 agent `run_once()` 可用。

### 阶段 2：抽 AgentLoop

目标：把主循环从 backend mixin 中移出。

任务：

- 新增 `agent/loop.py`。
- `backends.py` 只负责模型 stream。
- `loop.py` 负责 tool call 收集、ToolRuntime 调用、消息回灌。

验收：

- Anthropic 和 OpenAI 后端都能跑工具循环。
- 文本 streaming 仍可显示。
- 工具结果顺序正确。

### 阶段 3：引入 ToolRuntime 和 Tool adapter

目标：统一工具执行入口。

任务：

- 新增 `tools/base.py`。
- 用 `FunctionTool` 包装现有内置工具。
- `ToolRuntime.execute_one/execute_many` 接管权限、执行、大结果落盘。
- `agent/tools_runtime.py` 中的特殊分支逐步移入 Tool 实现或 Tool adapter。

验收：

- 所有工具调用都经过 `ToolRuntime`。
- 旧工具功能不变。
- 工具错误以 `ToolResult(is_error=True)` 表示。

### 阶段 4：权限系统重构

目标：修复安全优先级，增加 protected path 和 workspace boundary。

任务：

- 新建 `permissions/` 包。
- deny 规则优先于 `bypassPermissions`。
- 增加 protected path policy。
- 增加 workspace boundary。
- shell safety 先增强正则，保留后续 AST 扩展点。

验收：

- yolo 不绕过 deny/protected path。
- workspace 外写文件会被确认或拒绝。
- 常见危险 shell 命令会 confirm/deny。

### 阶段 5：最小 Hooks

目标：提供平台扩展点，但不引入过度复杂性。

任务：

- 新增 `hooks/config.py`、`hooks/runner.py`、`hooks/types.py`。
- 支持 `UserPromptSubmit`、`PreToolUse`、`PostToolUse`、`Stop`。
- 支持 command hook + stdin/stdout JSON。
- 启动时捕获配置快照。
- 项目级 hooks 默认需要 trust 或显式确认。

验收：

- 未配置 hooks 时无明显开销。
- PreToolUse hook 可 deny 或 modify 工具输入。
- PostToolUse hook 可 append context。
- Hook timeout 不挂死主循环。

### 阶段 6：逐个拆强 Tool

优先顺序：

1. `run_shell`
2. `edit_file`
3. `write_file`
4. `agent`
5. `skill`
6. `mcp`

目标：

- 每个复杂工具自带 validation、permission metadata、concurrency 判断。
- registry 不再依赖工具名硬编码判断 read-only/edit。

### 阶段 7：成本和可靠性增强

任务：

- prompt static/dynamic 分区。
- Anthropic prompt caching。
- prompt-too-long compact retry。
- max-output continuation。
- API retry 事件化。
- stop hook blocking continue reason。

这些是后续增强，不阻塞前六阶段。

## 验收标准

代码层：

- 主循环是 `async generator`，UI 通过事件消费输出。
- 工具执行只有一个入口：`ToolRuntime`。
- Tool 契约能表达 read-only、concurrency、validation、call。
- Permission 结果结构化。
- Hooks 未配置时默认不影响工具执行。
- 文档和 prompt 不描述不存在的功能。

行为层：

- 现有 CLI/REPL/session/sub-agent/skill/MCP 基本行为不回退。
- 拒绝和错误能回灌给模型。
- 大结果不会撑爆上下文。
- yolo 模式仍不能绕过硬安全边界。

维护性：

- 新增工具不需要改主循环。
- 新增 hook event 不需要改工具实现。
- 新增权限规则不需要改 UI。
- Anthropic/OpenAI 后端差异被限制在 backend adapter 内。
