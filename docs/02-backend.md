# Backend：模型后端

## 1. 为什么需要 Backend

Anthropic 和 OpenAI 的 API 看起来都是"发消息收回复"，但细节根本不同。流式事件：Anthropic 是 `content_block_start/delta/stop`，OpenAI 是 `chunk.choices[0].delta`。消息格式：Anthropic 的 tool 嵌套在 `content[]` 列表中，OpenAI 的是独立 `role: tool` message。token 计数：Anthropic 在 `response.usage` 里，OpenAI 在 `chunk.usage` 里（还要开 `stream_options: {include_usage: true}`）。

如果这些差异散落在 AgentLoop 里，循环就会有两条路径。旧代码正是如此——`_run_anthropic` 和 `_run_openai` 各 100 行，80% 相同。

Backend 模块的设计目标：**把厂商差异封装在策略类里。AgentLoop 只看到统一接口。加新厂商只加一个文件**。

## 2. 核心概念

### 2.1 策略模式

```
Backend (抽象类, backend/base.py)
    │
    ├── AnthropicBackend →  Messages API 流式调用
    │     content_block_start → 检测 tool_use
    │     content_block_delta  → text 回调 + partial_json 拼接
    │     content_block_stop   → json.loads 解析工具参数
    │     最后：过滤 thinking block
    │
    └── OpenAIBackend →  Chat Completions 流式调用
          chunk.choices[0].delta.content → 文本增量
          chunk.choices[0].delta.tool_calls → 按 index 累积 function.arguments
          最后：json.loads 解析工具参数
```

### 2.2 统一返回格式

不管哪个后端，AgentLoop 拿到的都是：

```python
@dataclass
class BackendResponse:
    text: str                      # 模型文本回复
    tool_calls: list[ToolCall]     # 工具调用列表
    usage: TokenUsage              # input_tokens + output_tokens
```

`ToolCall` 统一了 Anthropic 的 `tool_use` block 和 OpenAI 的 `function call`。AgentLoop 不需要知道区别。

### 2.3 流式输出机制

两个并发任务并行：`create_task(backend.call(...))` 异步执行，`asyncio.Queue` 接收 `on_text_delta` 回调放入的 text chunk。主循环每 50ms 从队列取一次 yield 给消费端。

`on_text_delta` 是一个 `async def(text)` 回调——Backend 在收到每个 text chunk 时调用它。Anthropic 路径在 `content_block_delta` 事件中触发，OpenAI 路径在 `chunk.choices[0].delta.content` 非空时触发。

## 3. 总体设计

### 3.1 文件结构

```
backend/
├── __init__.py       # create_backend() 工厂函数
├── base.py           # Backend 抽象类 + BackendResponse + TokenUsage（60 行）
├── anthropic.py      # AnthropicBackend（160 行）
└── openai.py         # OpenAIBackend（110 行）
```

### 3.2 与 runtime 的关系

```
AgentLoop(agent, backend: Backend)
    │
    ├── backend.call(messages=agent.messages, system=agent.system_prompt,
    │                tools=agent.tool_definitions(), on_text_delta=..., thinking_mode=...)
    │
    └── 拿到 BackendResponse → agent.record_usage() → _append_assistant_message()
```

Backend 不 import Agent 或 AgentLoop。它是纯策略类——接收参数，返回结果。

## 4. 详细设计

### 4.1 Backend 接口（base.py，60 行）

`Backend` 抽象类只有三个方法：`call()`（核心——调模型，返回统一响应）、`supports_thinking()`（检查模型是否支持 extended thinking）、`supports_adaptive_thinking()`（检查是否支持 adaptive thinking）。`resolve_thinking_mode(thinking_enabled)` 不是抽象方法——每个后端自己实现。

`BackendResponse` 是 dataclass：`text`（模型文本）、`tool_calls`（ToolCall 列表）、`usage`（TokenUsage）。

### 4.2 AnthropicBackend（160 行）

**流式解析的核心**：`tool_blocks_by_index: dict[int, dict]` 按 stream event 的 index 跟踪 tool_use block 的创建过程。`content_block_start` 时记录 `{index: {id, name, input_json: ""}}`，`content_block_delta` 时累积 `partial_json`，`content_block_stop` 时 `json.loads` 解析完成。

**thinking block 过滤**：`final_message.content = [b for b in content if b.type != "thinking"]`。thinking 是模型内部推理——在流式输出时展示给用户，但不能进入消息历史（后续 API 调用不接受）。

**retry**：所有 API 调用通过 `models.py` 的 `with_retry()` 做指数退避。`backoff = min(1000 * 2^attempt, 30000)/1000 + jitter`。只重试 429/503/529 和网络中断——不重试 model_not_found 等永久错误。

**block_to_dict**：静态方法。把 Anthropic 的 content block 对象转为可序列化 dict——text block→`{"type": "text", "text": "..."}`，tool_use block→`{"type": "tool_use", "id": "..", "name": "..", "input": {...}}`。

### 4.3 OpenAIBackend（110 行）

**增量 tool_calls 拼接**：OpenAI 的 tool_calls 是增量返回的——同一个 index 的 `function.arguments` 分散在多帧。`tool_calls_map: dict[int, dict]` 按 index 累积，帧结束时 `json.loads` 解析。

**工具 schema 转换**：`models.py` 的 `to_openai_tools()` 把 Anthropic 风格的 `input_schema` 转为 OpenAI function calling 格式：`{"type": "function", "function": {"name": ..., "description": ..., "parameters": input_schema}}`。

**usage**：OpenAI 的 token 用量在 `chunk.usage` 中，且需要 `stream_options={"include_usage": True}` 才返回。不是每个 chunk 都有 usage——只有最后一个 chunk 有。

## 5. 设计决策

### 决策 1：为什么 Backend 是独立策略类

**问题**：原代码 `AgentBackendMixin` 是 Agent 的 Mixin——通过 `self._anthropic_client` 访问 Agent 状态。换模型厂商要改 Agent 核心。

**选择**：独立策略类。`AnthropicBackend` 和 `OpenAIBackend` 是独立的类，通过 `call()` 参数接收所有数据。

**代价**：Agent 和 Backend 不再共享 Client 实例。但 Client 的创建成本极低（单行 `AsyncAnthropic(api_key=...)`），不算实际代价。

### 决策 2：为什么 thinking block 被静默过滤

**问题**：Anthropic 的 extended thinking 产生 `type: thinking` block。要不要保留在消息历史中？

**选择**：静默过滤。后续 API 调用**不接受** thinking block——如果保留会直接报错。在流式输出时展示给用户，但追加到历史前移除。

**代价**：用户看不到历史中的 thinking 内容。但这是 Anthropic 的 API 限制——不是设计选择。

### 决策 3：为什么 OpenAI 的 token 用量在 usage 而非 content 中

OpenAI 的 streaming 模式下，token 用量只在最后一个 chunk 返回（需要 `stream_options={"include_usage": True}`）。Anthropic 在 `final_message.usage` 中。Backend 封装了这个差异——AgentLoop 只拿到统一的 `TokenUsage`。

## 6. 面试考点

### Q1: 加第三个模型厂商改哪两个文件？

`models.py`：加模型的上下文窗口、thinking 支持、输出 token 上限。新建 `backend/gemini.py`：实现 `Backend` 接口的 `call()` 方法。AgentLoop 零改动——策略模式的价值在这里体现。

**追问"Backend 接口够用吗"**：`call()` 的参数（messages, system, tools, on_text_delta, thinking_mode）覆盖了所有模型 API 的公共部分。如果某个厂商需要特殊参数，可以在子类的 `__init__` 中接收，在 `call()` 内部使用。

### Q2: 为什么不用 httpx 统一 HTTP 调用？

两个厂商都用了各自的官方 SDK（`anthropic` 和 `openai` 包），而不是自己拼 HTTP 请求。原因：SDK 处理了流式解析的边界情况（重连、chunk 分片、错误响应解析）。自己拼不比 SDK 更可靠。

### Q3: thinking block 如果保留会怎样？

后续 API 调用报错——`type: thinking` 是 Anthropic 不接受的 content block 类型。静默过滤是唯一的正确做法。

## 7. 代码导读

**阅读顺序**：`base.py`（接口）→ `anthropic.py`（主要后端）→ `openai.py`（对比理解差异）。

**关键代码**：`anthropic.py:48-139`（`call()` 完整流程）、`anthropic.py:69-71`（thinking mode 参数组装）、`anthropic.py:116`（thinking block 过滤）、`openai.py:48-113`（OpenAI 流式解析 + tool_calls 增量拼接）。
