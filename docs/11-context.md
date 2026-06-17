# 上下文管理与压缩

## 1. 为什么需要上下文管理

每次模型调用都需要组装 system prompt、启动上下文、动态附件、记忆和消息历史。与此同时，对话会不断增长，工具输出可能很大，必须在超过上下文窗口前压缩。

当前架构中：

- 上下文构建位于 `agent/harness/context/`。
- 消息压缩位于 `agent/harness/compressor.py`。
- 单个工具大结果持久化位于 `cli/core/tools/runtime.py`。
- 模型摘要 callable 由 `cli/session.py` 注入 Compressor，harness 不 import provider。

## 2. 文件结构

```
agent/harness/context/
├── __init__.py
├── builder.py    # stable system prompt、startup context、动态附件 render
└── sources.py    # AGENTS.md、.nanocode/rules、Git 快照、frontmatter

agent/harness/
├── compressor.py
└── message_view.py
```

`message_view.py` 提供 canonical conversation 的工具结果读写视图，避免 compressor 手写遍历和修改 block 的重复逻辑。

## 3. 稳定 vs 动态

```
stable system prompt
  角色、行为规范、工具使用原则、输出要求

startup context
  日期、平台、shell、project instructions、Git 快照

dynamic attachments
  skill 列表、deferred tools、MCP delta
```

动态内容通过 user context 注入，不频繁修改 stable system prompt。这样更利于 prompt cache 命中，也让“长期规则”和“本轮状态”边界清楚。

三类上下文的维护边界不同：

| 层次 | 生命周期 | 典型内容 | 维护原则 |
|------|----------|----------|----------|
| stable system prompt | 会话内尽量稳定 | Agent 行为、工具使用原则、权限/输出规则、memory 使用规则 | 少变、通用、不要放项目实时状态 |
| startup context | session 启动时构建，compact 后可刷新 | 日期、平台、shell、project instructions、Git 启动快照、local memory | 作为 point-in-time context，冲突时以当前文件为准 |
| dynamic attachments | 对话中按需追加 | skill listing、deferred tools、MCP tool delta、post-compact recovery | 只描述当前运行状态，不改写长期规则 |

这个分层避免了两个常见问题：把项目状态塞进 system prompt 导致缓存不稳定；或者把长期行为规则当作普通 user message，后续 compact 时被摘要弱化。

## 4. Project instructions 和 Git 快照

`sources.py` 负责：

- 从当前目录向上加载 `AGENTS.md`。
- 解析各级 `.nanocode/rules/*.md`，并读取 frontmatter 中的 `paths`/`path` 作为 path-scoped 提示。
- 处理 `@path/to/file.md` include，限制递归深度。
- 剥离 HTML 注释。
- 收集 Git branch、status、log、user 等启动快照。

Git 快照是会话启动时的一次性信息。对话中代码会变化，实时刷新会让历史里出现多个互相矛盾的状态。

project instructions 的细节：

- discovery 会从祖先目录到当前目录收集 `AGENTS.md`，并收集每级 `.nanocode/rules/**/*.md`。
- `.nanocode/rules` 支持简单 frontmatter；当前读取 `paths` 或 `path` 字段并在渲染时保留路径作用域提示。
- `@path/to/file.md` include 只在非代码块文本中解析，限制 include 深度，跳过非文本扩展名，并检测循环。
- HTML 注释会在代码块外剥离，避免把维护备注注入给模型。
- 单文件和总 project instructions 都有字符预算，超预算时写 diagnostic，而不是让 prompt 无限制膨胀。

这些规则让项目指令成为“可审计的启动输入”。它们不是 live watcher；如果运行中修改了 `AGENTS.md` 或 rules，通常需要新 session 或 compact 后恢复链路重新读取相关上下文。

## 5. 三层上下文治理

```
Level 1  Tool Result Budget  单个工具结果过大时落盘，只保留预览和 artifact 引用
Level 2  Tool History Snip   provider call 前裁剪旧的可重读工具结果
Level 3  Context Compact     摘要旧上下文，保留最近原文，并恢复关键上下文
```

三层职责不重叠：

- Level 1 是 ingress control：工具结果进入 conversation 前先控制体积。
- Level 2 是 history cleanup：已经进入历史的旧工具结果，在下一次 provider call 前被局部裁剪。
- Level 3 是 conversation compression：局部裁剪不够时，才调用模型做 LLM 级摘要。

顺序很重要：先做便宜、确定性的治理，再做昂贵、不完全确定的摘要。Tool Result Budget 能保留完整 artifact；Tool History Snip 只替换可重读的旧工具结果；Context Compact 才会让模型重写历史。如果直接 compact，既浪费 token，也更容易丢失刚刚仍可通过本地文件重读的细节。

### Level 1: Tool Result Budget

位置：`cli/core/tools/runtime.py`

触发：工具返回结果超过阈值。

行为：

- 完整工具结果写入 artifact：

```
{workspace}/.nanocode/artifacts/tool-results/{call_id}.txt
```

- `ToolResult.content` 只保留 `<persisted-output>`、预览和本地文件路径。
- metadata 记录 `persisted`、`artifact_path`、`original_size`、`preview_chars`、`sha256`。

这一层不是旧版 head/tail 截断。主工具执行路径会先返回原始结果，再由 ToolRuntime 统一判断是否落盘。这样 artifact 保存的是完整输出，conversation 只接收预算内预览。

### Level 2: Tool History Snip

位置：`agent/harness/compressor.py`

触发点：每次 provider call 前。

触发条件：

```
last_input_token_count / effective_window >= SNIP_THRESHOLD
or now - last_api_call_time >= SNIP_IDLE_SECONDS
```

行为：

- 遍历 canonical conversation 中的 `tool_result`。
- 只处理可重读、可重跑或可重新确认状态的工具结果：`read_file`、`grep_search`、`list_files`、`run_shell`、`web_fetch`、`write_file`、`edit_file`。
- 保留最近 `KEEP_RECENT_TOOL_RESULTS` 个结果。
- 旧结果替换成 `[Content snipped - re-read if needed]`。
- 保持 `tool_use` / `tool_result` 配对，不删除消息和 block。

原来的 `Microcompact` 不再是独立层。空闲整理只是 Tool History Snip 的一种触发条件，避免为同一类“旧工具结果太多”问题维护两套概念。

### Level 3: Context Compact

位置：`agent/harness/compressor.py`

触发：Tool History Snip 之后，估算 conversation 仍超过 compact 阈值，或用户手动 `/compact`。

行为：

- 根据 token 预算选择 cut point。
- 摘要 cut point 之前的旧消息。
- 保留 cut point 之后的最近原文。
- cut point 优先选择 user message，避免切坏完整 turn。
- compact 成功后恢复关键运行上下文：project instructions、local memory、active skills、deferred tools、最近文件内容。
- 连续失败会熔断，避免无限重试。

Compact 不再默认“全量摘要后只保留最后 user message”。对 code agent 来说，最近原文通常比全局摘要更重要，因此当前实现采用 rolling compact：旧上下文摘要化，最近上下文原文保留。

cut point 由 `find_compact_cut_index()` 选择：`compact_keep_recent_tokens(effective_window)` 会按 `COMPACT_KEEP_RECENT_RATIO = 0.20` 计算最近原文预算，然后从尾部估算并优先落在 user message 边界，避免把一次 tool-use turn 从中间切开。旧消息少于 4 条时 `_summarize_messages()` 不会调用摘要模型，本次 compact 会跳过。

摘要 prompt 要求输出 9 个固定章节：用户请求、技术概念、文件和代码、错误和修复、问题解决、所有用户消息、待办、当前工作、下一步。这不是为了生成漂亮文档，而是为了降低恢复时漏掉“已修改文件、当前错误、用户未完成要求”的概率。

## 6. Provider Call 前的顺序

`AgentLoop` 只有一个 provider-call 前准备入口：

```python
prepare_context_for_provider()
  -> Tool History Snip
  -> Context Compact if pressure remains high
  -> provider.call(conversation=prepared.conversation)
```

这样不会出现“还没做便宜 Snip，就直接进入昂贵 Compact”的问题。

## 7. Compact 后恢复

`Compressor` 不直接 import memory、skills、MCP 或工具模块。它只接收：

```python
build_post_compact_context: Callable[[], str]
```

这个 callable 由 `AgentSession` 实现，负责拼接：

- `build_prompt_bundle(workspace).startup_context`：当前 project instructions 和 Git 启动快照。
- `MemoryRuntime.build_compact_context()`：compact 后重新注入 local memory。
- `ActiveSkillManager.build_context()`：恢复 active skills。
- `render_deferred_tools_attachment(...)`：重新提示仍可按需加载的 deferred tools。
- `RecentFileTracker.build_context()`：重新从磁盘读取最近 read/edit/write 的当前文件内容。

最近文件恢复只记录路径，不缓存文件正文。compact 后重新读取当前磁盘内容，最多恢复少量 workspace 内文本文件；文件过大、缺失、二进制或在 workspace 外时只注入状态说明。

post-compact recovery 是对摘要的补充，不是替代。摘要负责保存“对话历史里发生过什么”，恢复上下文负责重新给模型当前仍然有效的运行状态：项目指令、memory、active skills、deferred tools 和最近文件当前内容。两者都进入 compact 后的新 conversation，但来源和可信度不同。

## 8. Compressor 如何避免依赖 provider

Compressor 需要调用模型做摘要，但它不创建 backend，不 import `providers/`。`AgentSession` 注入：

```python
Compressor(
    agent,
    summarize_messages=self._summarize_messages,
    build_post_compact_context=self._build_post_compact_context,
    notify=self._notify,
)
```

这样 harness 仍然只依赖 agent core。

## 9. 设计决策

### 为什么删除 Collapse 和 Microcompact

Collapse 和 Compact 都是“摘要消息历史”，职责重叠且容易触发顺序冲突。现在统一成 Context Compact：摘要旧上下文，保留最近原文。

Microcompact 和 Snip 都是“裁剪旧工具结果”，区别只是触发条件。现在统一成 Tool History Snip：上下文压力升高或空闲后，使用同一个占位符清理旧结果。

### 为什么 Tool Result Budget 在 ToolRuntime

Budget 是工具结果返回后的准入控制，必须发生在结果进入消息历史之前。ToolRuntime 是唯一能统一拦截所有工具结果的地方。

### 为什么动态附件不用 system prompt

system prompt 变化会影响缓存，也会混淆稳定规则和当前状态。动态信息作为 user context 注入，更清晰也更可控。

## 10. 边界与失败模式

上下文治理的失败策略同样保持分层：

| 场景 | 行为 | 维护含义 |
|------|------|----------|
| 单个工具结果过大 | 落盘到 artifact，conversation 只保留预览 | 完整信息仍可本地审计，不挤占 provider context |
| 旧工具结果过多 | 只 snip 可重读/可重跑工具的旧结果，保留最近结果 | 不删除 tool_use/tool_result 配对，provider 协议仍完整 |
| compact summarizer 返回空 | 本次 compact 跳过 | 不用空摘要破坏历史 |
| compact provider 调用失败 | 记录通知并继续当前 conversation | 短期失败不直接中断任务 |
| compact 连续失败 3 次 | 熔断并抛出 | 避免每轮 provider call 都重复失败 |
| recent file 缺失/过大/二进制/workspace 外 | post-compact context 注入状态说明 | 不伪造文件内容，不越界读取 |
| project instruction include 失败 | 写 diagnostic，跳过 include | 启动上下文可用性高于强行失败 |

这些行为说明 context 管理不是“尽量把所有东西塞给模型”，而是保持 provider payload、session log 和本地 artifact 三者各司其职。

## 11. Benchmark 覆盖

`benchmarks/local-fixture` 的 context-governance 任务直接约束三层上下文治理：

- `context_large_result_persist`：强制完整读取大日志，要求 ToolRuntime 把完整输出保存为 artifact，trace 中 `metadata.persisted == true`，同时 Agent 仍能提取诊断值。
- `context_tool_history_snip_realistic`：用受控 `context_window` 和多份 audit 文件触发 Tool History Snip，要求出现 `tool_history_snip`，且不触发 `context_compact`。

相关 run-artifacts 任务还要求 trace/report 记录 context 准备、工具执行和最终状态。Benchmark 可通过任务级 `context_window` 或环境变量 `NANO_CODE_CONTEXT_WINDOW` 收窄窗口，验证压缩策略不会依赖真实大模型窗口。

`NANO_CODE_CONTEXT_WINDOW` 是测试和 benchmark 的重要入口：它最终进入 `RuntimeConfig.context_window` 和 `AgentConfig.context_window`，从而改变 `effective_window`。这样 fixture 可以在小输入下触发 snip/compact，而不需要构造接近真实模型窗口的大型对话。

维护者自查问题：

- 为什么 Level 1 必须在 ToolRuntime，而不是等 provider call 前再处理？
- 为什么 Tool History Snip 不能删除旧消息，只能替换 tool result 内容？
- compact 为什么优先保留最近原文，而不是只保留一份全量摘要？
- post-compact recovery 为什么重新读取最近文件，而不是缓存读文件结果？

## 12. 代码导读

```
agent/harness/context/builder.py
agent/harness/context/sources.py
agent/harness/message_view.py
agent/harness/compressor.py
cli/core/tools/recent_files.py
cli/core/tools/runtime.py::_persist_large_result
cli/session.py::_summarize_messages
cli/session.py::_build_post_compact_context
```
