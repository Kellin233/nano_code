# Context 重构方案

## 目标

把原来“prompt 构造”升级为完整的上下文管理设计。`prompt` 不再作为独立设计文档存在，因为 prompt 只是 context 的一个模块：稳定系统提示词、启动上下文、动态附件、记忆注入、工具结果压缩、compact 都属于“模型下一轮能看到什么”的问题。

本方案采用前面讨论的推荐路线：

```text
稳定 system prompt
+ 启动上下文
+ 动态附件
+ 记忆按需注入
+ 工具结果分层压缩
+ compact 失败熔断
+ provider 消息格式保护
```

目标不是重写模型性格，而是把上下文生命周期边界做清楚，利于后续维护、扩展、缓存和面试讲解。

## 设计定位

上下文管理回答一个问题：

```text
模型下一次 API 调用前，应该看到什么？
```

它不负责保存长期记忆，不负责执行工具，不负责权限判断，也不负责模型后端协议细节。

它负责：

- 构建稳定 system prompt。
- 构建一次性启动上下文。
- 加载项目规则和 Git 快照。
- 渲染 skills、deferred tools、MCP 变化、memory 等动态附件。
- 控制动态上下文什么时候注入。
- 管理上下文预算。
- 压缩历史消息和工具结果。
- compact 后恢复必要上下文。

## 总体设计

### 先看心智模型

上下文管理不要理解成“拼一个很长的 prompt 字符串”。更准确的理解是：每次调用模型前，系统都要临时组装一份输入包。

这份输入包由五层组成：

| 层 | 内容 | 生命周期 | 主要目的 |
|----|------|----------|----------|
| 稳定 system prompt | agent 身份、行为边界、工具原则、输出风格 | 进程内长期稳定 | 让模型保持一致行为，也方便后续 prompt caching |
| 启动上下文 | 当前日期、cwd、平台、Git 启动快照、项目规则 | 每个 Agent 会话注入一次 | 给模型会话起点信息 |
| 动态附件 | skills、deferred tools、MCP 变化、memory 召回结果、hooks 提示 | 按事件注入 | 把运行时变化告诉模型 |
| 消息历史 | 用户消息、assistant 回复、tool call、tool result | 持续增长 | 保留对话和执行轨迹 |
| 压缩摘要 | compact 后的历史摘要和恢复信息 | 触发 compact 后生成 | 控制上下文预算，避免丢失关键状态 |

context 模块的职责是管理这五层的边界和注入时机。它不应该关心“某条 memory 怎么召回”，也不应该关心“某个 shell 命令能不能执行”。这些分别属于 memory 和 tool/permission/sandbox。

### 设计主线

每一轮模型调用前，context 都按固定顺序准备输入：

```text
1. 保持 stable system prompt 不变
2. 确保 startup context 只注入一次
3. 接收当前用户消息
4. 启动或消费 memory prefetch
5. 判断是否需要 compact
6. flush pending attachments
7. 保护 tool_use / tool_result 配对
8. 生成 provider 可接受的 messages
```

这个顺序很重要：

- stable system prompt 尽量不变，避免动态内容破坏缓存。
- startup context 只能注入一次，否则会重复占预算。
- memory 召回必须发生在模型调用前，但召回逻辑不放在 context 模块里。
- compact 不能打断 tool_use 和 tool_result 的配对，否则 provider 可能直接报错。
- pending attachments 要和用户真实输入分开，避免模型把系统提示误认为用户需求。

### 模块结构

当前 `nano_code/prompt.py` 应保留为兼容门面，但不再承载主要逻辑。

推荐结构：

```text
nano_code/
├── prompt.py                  # 兼容入口：build_system_prompt / build_prompt_bundle
├── context/
│   ├── __init__.py
│   ├── types.py               # PromptBundle、ContextAttachment、PromptDiagnostic
│   ├── system_prompt.py       # 稳定 system prompt
│   ├── startup.py             # 一次性启动上下文
│   ├── claude_md.py           # CLAUDE.md、rules、include
│   ├── git_context.py         # Git 启动快照
│   └── attachments.py         # 动态附件渲染
└── agent/
    └── context.py             # 运行时上下文 mixin：注入、压缩、compact
```

说明：

- `nano_code/context/` 负责构造和渲染上下文。
- `nano_code/agent/context.py` 负责在 agent 运行时把上下文放入消息历史，并维护压缩状态。
- `prompt.py` 只保留对外 API，避免旧调用点全部重写。

不要再新增 `prompt/` 包。项目已经有 `prompt.py` 文件，改成同名包会带来无意义迁移成本。

### 模块职责边界

| 模块 | 应该负责 | 不应该负责 |
|------|----------|------------|
| `prompt.py` | 兼容旧入口，转调新的 context 构造函数 | 继续堆所有 prompt 逻辑 |
| `context/system_prompt.py` | 稳定 system prompt 文本 | 当前日期、Git、memory、工具列表 |
| `context/startup.py` | 一次性启动上下文 | 每轮动态事件 |
| `context/claude_md.py` | 项目规则加载、include、截断诊断 | 解释规则是否正确 |
| `context/git_context.py` | 生成启动时 Git 快照 | 提供实时 Git 状态 |
| `context/attachments.py` | 把动态附件渲染成统一格式 | 决定动态附件是否可信 |
| `agent/context.py` | 注入、压缩、compact、消息配对保护 | 构造 memory 召回策略或执行工具 |

### 上下文生命周期

```text
Agent 初始化
  -> build_prompt_bundle()
  -> stable system prompt 保存到 provider system 字段
  -> startup_context 暂存

第一次用户回合前
  -> startup_context 作为独立 meta user message 注入一次
  -> skills/deferred tools 初始列表作为动态附件注入

每个用户回合开始
  -> 用户真实消息进入历史
  -> 启动 memory prefetch
  -> 执行上下文压缩流水线
  -> 非阻塞消费 memory prefetch
  -> flush pending attachments
  -> 调模型

工具执行后
  -> 工具结果进入历史
  -> PostToolUse attachment 进入 pending queue
  -> 下一轮模型调用前 flush

compact
  -> 保留最后用户消息
  -> 摘要旧历史
  -> 重挂 active skills 和必要上下文
  -> 不重复注入 startup_context 全量内容
```

## 详细设计

### 1. `prompt.py`

`prompt.py` 只做兼容门面。

保留：

```python
def build_system_prompt(deferred_tool_names: list[str] | None = None) -> str
def load_claude_md() -> str
def get_git_context() -> str
```

新增或继续导出：

```python
def build_prompt_bundle(...) -> PromptBundle
```

要求：

- `build_system_prompt()` 只返回稳定 system prompt。
- `deferred_tool_names` 参数保留但不再塞进 system prompt。
- 旧调用点不崩，但新代码应优先用 `build_prompt_bundle()`。

### 2. `context/types.py`

保持轻量：

```python
@dataclass(frozen=True)
class PromptDiagnostic:
    level: Literal["info", "warning", "error"]
    source: str
    message: str

@dataclass(frozen=True)
class ContextAttachment:
    title: str
    body: str
    source: str = ""
    once_key: str = ""

@dataclass
class PromptBundle:
    system_prompt: str
    startup_context: str
    diagnostics: list[PromptDiagnostic] = field(default_factory=list)
```

`ContextAttachment` 的 `once_key` 用于去重。比如 skill 列表、deferred tools 列表、MCP 工具变化不能每轮重复注入。

不要引入复杂附件类层级。附件本质上就是一段带来源的文本。

### 3. `context/system_prompt.py`

只放稳定系统提示词。

稳定内容包括：

- agent 身份。
- 安全边界。
- 工具使用原则。
- 权限和 hooks 的基本说明。
- 输出风格。
- 上下文压缩会发生的说明。

不放：

- 当前日期。
- 当前工作目录。
- Git status。
- `CLAUDE.md` 正文。
- memory index。
- skill 列表。
- MCP 工具列表。
- deferred tools。

保留动态边界常量：

```python
SYSTEM_PROMPT_DYNAMIC_BOUNDARY = "__NANO_CODE_SYSTEM_PROMPT_DYNAMIC_BOUNDARY__"
```

第一版不用真的接 provider cache control，但 stable prompt 必须为后续缓存优化留出结构。

### 4. `context/startup.py`

负责生成一次性启动上下文。

示例：

```text
<system-reminder>
Startup context for this Nano Code session.

Current date: 2026-06-07.
Working directory: /root/EvoCode/nano_code.
Platform: Linux x86_64.
Shell: /bin/bash.

Git context:
This git context is a snapshot from the start of the conversation and will not update automatically.
...

Project instructions:
...
</system-reminder>
```

要求：

- 每个 Agent 会话只注入一次。
- 作为独立 meta user message 注入，不追加到真实用户输入后面。
- 明确 Git context 是启动快照，不是实时状态。
- 超长内容必须截断并记录 diagnostic。
- diagnostics 不默认注入模型，避免污染上下文。

`build_prompt_bundle()` 应返回：

```python
PromptBundle(
    system_prompt=build_stable_system_prompt(),
    startup_context=build_startup_context(...),
    diagnostics=[...],
)
```

### 5. `context/claude_md.py`

负责加载项目指令。

加载来源从低到高：

```text
~/.claude/CLAUDE.md
项目路径从远到近的 CLAUDE.md
项目路径从远到近的 .claude/CLAUDE.md
项目路径从远到近的 .claude/rules/**/*.md
项目路径从远到近的 CLAUDE.local.md
```

越靠近当前目录的指令越晚注入，优先级越高。

include 规则：

- 支持 `@path`、`@./path`、`@~/path`、`@/path`。
- 相对路径按当前文件目录解析。
- 代码块内不解析 include。
- URL、email、Python decorator 不应被误判。
- 只允许文本扩展名：`.md`、`.txt`、`.rst`、`.adoc`、`.yaml`、`.yml`、`.json`。
- 最大递归深度 5。
- 循环引用生成 warning，不中断启动。

rules：

- `.claude/rules/**/*.md` 递归加载。
- 支持 frontmatter 的 `paths` 字段。
- 第一版可先把 path-scoped rule 标注在文本中，不必立刻做按文件动态注入。

注释：

- 剥离 HTML 注释。
- 不剥离代码块内内容。

预算：

```text
MAX_FILE_CHARS = 20000
MAX_TOTAL_CHARS = 60000
```

超限要截断并记录 diagnostic。

### 6. `context/git_context.py`

负责启动时 Git 快照。

建议收集：

```text
git rev-parse --is-inside-work-tree
git rev-parse --abbrev-ref HEAD
git symbolic-ref refs/remotes/origin/HEAD
git --no-optional-locks status --short
git --no-optional-locks log --oneline -5
git config user.name
```

要求：

- 不在 Git repo 中时返回空。
- 每个命令有 timeout，默认 3 秒。
- 可以用线程池并行执行。
- `status` 截断到 2000 字符。
- 开头必须有快照声明：

```text
This git context is a snapshot from the start of the conversation and will not update automatically.
```

不要每轮刷新 Git context。实时状态应通过工具命令读取。

### 7. `context/attachments.py`

负责把动态信息渲染为 `<system-reminder>`。

建议接口：

```python
def render_system_reminder(title: str, body: str) -> str
def render_memory_attachment(memories: list[RelevantMemory]) -> str
def render_skill_listing_attachment(skills, sent: set[str]) -> tuple[str, set[str]]
def render_deferred_tools_attachment(names: list[str]) -> str
def render_mcp_delta_attachment(delta: object) -> str
```

附件渲染原则：

- 只渲染，不决定何时注入。
- 内容必须短。
- 必须说明来源。
- 不重复发送同一批内容。
- 对外部来源和工具结果要提醒 prompt injection 风险。

memory attachment 应调用 `memory.rendering.format_memories_for_injection()`，不要在 `context/attachments.py` 重复实现 freshness/evidence 逻辑。

### 8. `agent/context.py`

这是运行时上下文管理。它不应该膨胀成“所有 context 逻辑大杂烩”，但第一版保留 mixin 是务实选择。

主要职责：

- 注入 startup context。
- 管理 pending attachments。
- 启动和消费 memory prefetch。
- 保持 Anthropic/OpenAI 消息历史格式合法。
- 执行工具结果压缩流水线。
- 触发 compact。
- compact 后重挂必要上下文。

建议保留这些方法：

```python
def _inject_startup_context_once(self) -> None
def _queue_context_attachment(self, text: str) -> None
def _flush_pending_context_attachments(self) -> None
def _prepare_initial_context_attachments(self) -> None
def _append_meta_user_message(self, text: str) -> None
def _append_user_context(self, text: str) -> None
def _start_memory_prefetch(self, user_message: str) -> MemoryPrefetch | None
def _consume_memory_prefetch(self, memory_prefetch: MemoryPrefetch | None) -> None
def _run_compression_pipeline(self) -> None
async def _check_and_compact(self) -> None
async def _compact_conversation(self) -> None
```

如果后续 `agent/context.py` 继续变大，再拆：

```text
agent/context_memory.py
agent/context_compaction.py
agent/context_attachments.py
```

但现在不要为了好看提前拆。

### 9. 消息注入规则

真实用户输入和系统补充上下文要区分。

推荐：

```text
startup_context         -> 独立 meta user message
skill/deferred/MCP      -> 独立 meta user message
memory recall           -> 追加到当前用户消息或独立 meta user message均可，第一版保持现状追加当前用户消息
PostToolUse attachment  -> pending queue，下轮前独立 meta user message
Stop hook continuation  -> 追加 user context
```

关键要求：

- 不要把 startup context 拼到用户真实请求后面。
- 不要重复注入同一 skill 列表。
- 不要在工具结果还未配对完成时插入会破坏 provider 协议的消息。
- Anthropic 的 `tool_use` 和 `tool_result` 必须成对。
- OpenAI 的 `assistant.tool_calls` 和 `role=tool` 必须成对。

### 10. 压缩流水线

保持分层，不要只靠 `/compact`。

推荐顺序：

```text
第 0 层：大工具结果落盘，只把预览放入上下文
第 1 层：上下文压力升高时裁剪超长工具结果
第 2 层：snip 陈旧 read/search/shell 结果正文
第 3 层：microcompact 清理冷缓存下的旧工具结果
第 4 层：模型摘要 compact
```

原则：

- 工具结果正文可以裁剪，但工具调用元数据尽量保留。
- 保留最近几个工具结果，避免模型刚读完就忘。
- `read_file`、`grep_search`、`list_files`、`run_shell` 是可重新获取的，适合 snip。
- `edit_file`、`write_file` 结果要谨慎裁剪，因为它们描述了实际变更。

compact 前必须确认最后一条消息不是未配对工具结果。

### 11. compact 失败熔断

当前 compact 如果 API 调用失败，容易在后续回合重复失败。

新增状态：

```python
self._compact_failure_count = 0
self._auto_compact_disabled = False
```

策略：

- 自动 compact 连续失败 3 次后禁用自动 compact。
- 手动 `/compact` 仍可执行。
- 手动 compact 成功后清零。
- 失败时提示用户可以 `/clear`、手动 `/compact` 或开启新会话。
- 不要无限重试 compact summary。

模型 API retry 可以复用 `agent.models._with_retry()`，但只重试限流、过载、网络 timeout，不能吞掉参数错误。

### 12. 和记忆系统的关系

记忆系统负责召回：

```text
query -> relevant memories
```

上下文管理负责注入：

```text
relevant memories -> provider message history
```

边界：

- `memory.retrieval` 不应该直接改 `_anthropic_messages` 或 `_openai_messages`。
- `agent/context.py` 不应该重新实现记忆打分。
- `context/attachments.py` 不应该重复实现 memory freshness/evidence 格式。

### 13. 和工具、hooks、MCP 的关系

工具执行结束后，`PostToolUse` hook 可能返回追加上下文。它应进入 `pending_context_attachments`，由 context 在下一轮模型调用前统一 flush。

MCP 工具列表变化也应作为动态附件，而不是重建 stable system prompt。

deferred tools 只在需要时通过 `tool_search` 展开。初始上下文只告诉模型有哪些 deferred tool 名称，不塞完整 schema。

## 硬性约束

- 不再把动态内容塞进 stable system prompt。
- 不改变模型主循环语义。
- 不破坏 Anthropic/OpenAI 工具消息配对。
- 不让 startup context 每轮重复注入。
- 不把所有 memory 正文放入上下文。
- 不在 context 模块执行工具。
- 不在 context 模块做权限判断。
- 不引入模板引擎。
- 不引入复杂依赖。
- 不为了拆分而拆分。

## 隐含要求

- 上下文来源必须可解释。
- Git status 必须标注为启动快照。
- 文件和工具结果可能带 prompt injection，模型要被提醒。
- 诊断信息默认不要塞进 prompt。
- compact 后 active skills 和必要 context 不能丢。
- memory 是旧信息，不能覆盖当前文件事实。
- meta context 和用户真实意图要分开。
- provider message history 要始终合法。
- 未来要能接 prompt caching，所以 stable prompt 必须稳定。

## 不能做什么

- 不能继续把 `prompt.py` 当成大杂烩。
- 不能把 `CLAUDE.md`、Git、memory、skills、tools 全部拼进 system prompt。
- 不能把 prompt 重构变成重写模型性格。
- 不能每轮重新读取并注入所有项目规则。
- 不能为了“上下文完整”把大文件全文塞进去。
- 不能 compact 时随意删除 tool_use / tool_result 中的一边。
- 不能让 hooks 或 MCP 附件直接插入到不合法的位置。
- 不能把上下文压缩失败变成无限 API 消耗。

## 可能踩坑

### 动态内容破坏 prompt caching

如果日期、Git、memory 都放在 system prompt，每轮缓存命中都会变差。稳定 system prompt 必须干净。

### 用户输入和 meta context 混在一起

把 startup context 拼到用户 prompt 后面，会让模型误把项目规则当成本轮用户请求的一部分。启动上下文应独立注入。

### 工具消息配对被打断

Anthropic 和 OpenAI 都对工具调用消息结构敏感。compact 和附件注入必须避开未配对状态。

### Git 快照过期

启动时 Git status 不是实时状态。文档和注入文本必须明确说明，否则模型会错误引用旧状态。

### `CLAUDE.md` include 循环

include 必须有深度限制和循环检测。失败生成 diagnostic，不中断启动。

### path-scoped rules 过早复杂化

第一版可以加载并标注 `paths`，不要急着做按文件动态注入。否则会牵连 read/edit/search 工具路径跟踪。

### 诊断污染上下文

诊断是给调试和 UI 的，不是给模型的。只有必要警告才可短文本输出。

### compact 丢失技能状态

compact 后如果 active skills 不重挂，模型会忘记当前技能约束。必须保留 `_active_skills.build_context()` 的重挂逻辑。

### 记忆注入太晚

异步 memory prefetch 可能第一轮来不及注入。如果用户问题不触发工具，模型可能直接回答。这个行为可以接受，但文档中要说明；后续可在明确“回忆/记得”类请求中同步等待短时间。

## 实施步骤

1. 删除 `remake/design/prompt.md`，以后只维护 `context.md`。
2. 保留 `nano_code/prompt.py` 作为兼容门面。
3. 确认 `context/system_prompt.py` 不包含动态内容。
4. 确认 `context/startup.py` 生成一次性 startup context。
5. 完善 `context/claude_md.py` 的 include、rules、预算、diagnostics。
6. 完善 `context/attachments.py`，让 memory 附件复用 `memory.rendering`。
7. 在 `Agent` 初始化中保存 `PromptBundle` 的 startup context 和 diagnostics。
8. 在首轮用户请求前注入 startup context。
9. 在每轮模型调用前 flush pending attachments。
10. 给 compact 加失败计数和自动熔断。
11. 后续再补端到端测试和 prompt cache 支持。

## 验收标准

- `build_system_prompt()` 输出不包含 cwd、date、git、memory index、skill 列表。
- `build_prompt_bundle()` 返回 stable system prompt 和 startup context。
- startup context 每会话只注入一次。
- dynamic attachments 不重复注入。
- memory 召回由 memory 模块完成，context 只负责注入。
- compact 不破坏工具消息配对。
- compact 失败连续 3 次后自动熔断。
- prompt.md 设计文档不再存在，context.md 成为 prompt/context 的唯一设计文档。
