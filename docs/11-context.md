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
└── sources.py    # CLAUDE.md、Git 快照、frontmatter

agent/harness/
├── compressor.py
└── message_view.py
```

`message_view.py` 提供双消息格式的读写视图，避免 compressor 到处手写 Anthropic/OpenAI 分支。

## 3. 稳定 vs 动态

```
stable system prompt
  角色、行为规范、工具使用原则、输出要求

startup context
  日期、平台、shell、CLAUDE.md、Git 快照

dynamic attachments
  skill 列表、deferred tools、MCP delta、memory
```

动态内容通过 user context 注入，不频繁修改 stable system prompt。这样更利于 prompt cache 命中，也让“长期规则”和“本轮状态”边界清楚。

## 4. CLAUDE.md 和 Git 快照

`sources.py` 负责：

- 加载用户级和项目级 `CLAUDE.md`。
- 解析 `.claude/rules/*.md`、`CLAUDE.local.md`。
- 处理 `@path/to/file.md` include，限制递归深度。
- 剥离 HTML 注释。
- 收集 Git branch、status、log、user 等启动快照。

Git 快照是会话启动时的一次性信息。对话中代码会变化，实时刷新会让历史里出现多个互相矛盾的状态。

## 5. 五层压缩

```
Layer 0  Persist        单个工具结果过大时落盘
Layer 1  Snip           利用率较高时去掉旧的可重读工具结果
Layer 2  Microcompact   空闲一段时间后清理旧工具结果
Layer 3  Collapse       摘要早期 70% 消息，保留最近 30% 原文
Layer 4  Compact        全量摘要，最后兜底
```

### Layer 0: Persist

位置：`cli/core/tools/runtime.py`

触发：工具返回结果超过阈值。

行为：完整结果写入：

```
{workspace}/.nanocode/sessions/{session_id}/tool-results/{call_id}.txt
```

消息历史只保留 `<persisted-output>` 和约 2KB 预览。

### Layer 1: Snip

位置：`agent/harness/compressor.py`

触发：上下文利用率超过阈值。

行为：对可重读的工具结果做去重和保留最近项，旧结果替换为提示文本。可重读工具包括 read、grep、list、shell、web_fetch、write、edit 等。

### Layer 2: Microcompact

位置：`agent/harness/compressor.py`

触发：距离上次 API 调用超过空闲阈值。

行为：保留最近少量工具结果，旧结果替换为 `[Old result cleared]`。

### Layer 3: Collapse

位置：`agent/harness/compressor.py`

触发：上下文利用率很高且消息数量足够。

行为：调用模型总结前 70% 消息，保留后 30% 原文。它比 Compact 破坏性更低，成功后通常不需要 Compact。

### Layer 4: Compact

位置：`agent/harness/compressor.py`

触发：对话开始时检查到利用率超过阈值，或用户手动 `/compact`。

行为：全量摘要消息历史，随后恢复最近文件上下文和 active skills。连续失败会熔断，避免无限重试。

## 6. Compressor 如何避免依赖 provider

Compressor 需要调用模型做摘要，但它不创建 backend，不 import `providers/`。`AgentSession` 注入：

```python
Compressor(
    agent,
    summarize_messages=self._summarize_messages,
    notify=self._notify,
)
```

这样 harness 仍然只依赖 agent core。

## 7. 设计决策

### 为什么 Collapse 和 Compact 共用摘要引擎

两者都是“把消息列表变成结构化摘要”。区别只是输入范围：Collapse 输入早期消息，Compact 输入全部消息。共用引擎减少 prompt 和调用逻辑重复。

### 为什么 Persist 在 ToolRuntime

Persist 是工具结果返回后的即时处理，必须发生在结果进入消息历史之前。ToolRuntime 是唯一能统一拦截所有工具结果的地方。

### 为什么动态附件不用 system prompt

system prompt 变化会影响缓存，也会混淆稳定规则和当前状态。动态信息作为 user context 注入，更清晰也更可控。

## 8. 代码导读

```
agent/harness/context/builder.py
agent/harness/context/sources.py
agent/harness/message_view.py
agent/harness/compressor.py
cli/core/tools/runtime.py::_persist_large_result
cli/session.py::_summarize_messages
```
