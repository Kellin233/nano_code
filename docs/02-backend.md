# Backend：模型后端

## 为什么需要 Backend

Anthropic 和 OpenAI 的 API 看起来都是"发消息、收回复"，但细节完全不同。Anthropic 的 tool 参数在 `content[]` 列表中，OpenAI 的 tool 是独立 `role: tool` message。Anthropic 有 extended thinking，OpenAI 没有。Anthropic 流式事件是 `content_block_start/delta/stop`，OpenAI 是 `chunk.choices[0].delta`。

如果把这些差异散落在 AgentLoop 的主循环里，循环就会变成两套——事实上旧代码正是如此（`_run_anthropic` 和 `_run_openai` 各 100 行，80% 相同）。

Backend 模块的设计目标：**把厂商差异封装在策略类里，AgentLoop 只看到统一接口**。

## 核心概念

### 策略模式

```
Backend（抽象接口）
    ├── AnthropicBackend：处理 Messages API 流式事件
    └── OpenAIBackend：处理 Chat Completions 流式事件

create_backend(provider, ...) → 工厂函数选实现
```

`AgentLoop` 只依赖 `Backend`，不依赖具体实现。这是依赖倒置。

### 统一返回格式

不管后面是 Anthropic 还是 OpenAI，`AgentLoop` 拿到的都是：

```python
BackendResponse(
    text="模型的文本回复",
    tool_calls=[ToolCall(id="t1", name="read_file", input={...})],
    usage=TokenUsage(input_tokens=100, output_tokens=200),
)
```

工具调用被统一为 `ToolCall` 对象——AgentLoop 不需要知道 Anthropic 的 `tool_use` block 和 OpenAI 的 `function call` 有什么区别。

### 流式输出的机制

AgentLoop 不等待模型完整返回。它用两个并发任务并行：一个调 Backend，一个从 `asyncio.Queue` 取文本 yield。`on_text_delta` 回调把每个 text chunk 放入队列，主循环每 50ms 取一次。用户看到逐字输出。

## 设计决策

### 为什么 Backend 不放在 Agent 里

旧代码 `AgentBackendMixin` 是所有后端逻辑的载体。它是 Agent 的 Mixin——通过 `self._anthropic_client` 访问 Agent 状态。问题是：换模型厂商要改 Agent 核心，新增厂商要改 Mixin。现在 Backend 是独立策略类——加 Google Gemini 只需要新建 `backend/gemini.py`。

### 为什么 thinking block 被静默过滤

Anthropic 的 extended thinking 产生 `type: thinking` block。它们只在流式输出时展示给用户看，不能放进消息历史——后续 API 调用不接受 thinking block。过滤是 `AnthropicBackend.call()` 内部的最后一步：`final_message.content = [b for b in content if b.type != "thinking"]`。

### 为什么双后端消息历史不统一

Backend 只负责"调模型"，不负责"存消息"。消息格式的差异由 Agent 处理——`_anthropic_messages` 和 `_openai_messages` 分开存储。AgentLoop 的 `_append_assistant_message()` 和 `_append_tool_results()` 在内部根据 `use_openai` 路由到正确的格式。

## 代码走读

**`base.py`（~60 行）**：`Backend` 抽象类定义 `call()` 接口 + `BackendResponse` + `TokenUsage`。

**`anthropic.py`（~160 行）**：处理 `content_block_start`（检测 tool_use）→ `content_block_delta`（text 回调 + partial_json 拼接）→ `content_block_stop`（json.loads 解析）的流式事件链。`block_to_dict()` 把 Anthropic 内容块转成可序列化的 dict。

**`openai.py`（~110 行）**：按 `index` 累积增量 `function.arguments`，帧结束时 `json.loads` 解析为 `ToolCall`。`to_openai_tools()` 转换 schema 格式。

## 面试考点

**Q: 新增模型厂商改哪些文件？**

两个：`models.py` 加模型元数据，新建 `backend/gemini.py` 实现 `Backend` 接口。AgentLoop 完全不用动——这证明了策略模式的价值。

**Q: Anthropic thinking block 为什么必须过滤？**

后续 API 调用不接受 `type: thinking` content block——它是模型内部推理，不是对话内容。过滤是必须的清理步骤，不是可选优化。
