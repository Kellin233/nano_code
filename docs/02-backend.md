# Backend：模型后端

## 1. 为什么需要 Backend

Anthropic 和 OpenAI 的 API 看起来都是"发消息、收回复"，但底层细节根本不同。

流式事件格式：Anthropic 用 `content_block_start`/`content_block_delta`/`content_block_stop` 三个事件类型来构建一条消息——每个 tool_use block 的生命周期从 start 到 stop 跨越多个事件。OpenAI 用 `chunk.choices[0].delta`——tool_calls 的 `function.arguments` 增量返回，按 index 在多帧中累积。

消息格式：Anthropic 的 tool 调用和结果嵌套在 `content[]` 列表中——`tool_use` block 在 assistant.content 中，`tool_result` block 在 user.content 中。OpenAI 的 tool 调用和结果是独立的 message role——`role: assistant` + `tool_calls` 字段，`role: tool` + `tool_call_id` 字段。

thinking 机制：Anthropic 有 extended thinking——模型在回答前进行内部推理，产生 `type: thinking` block。OpenAI 没有这个机制。

token 计数：Anthropic 在 `response.usage` 中返回。OpenAI 在 `chunk.usage` 中，且需要显式开启 `stream_options={"include_usage": True}`，只有最后一个 chunk 才包含。

如果这些差异散落在 AgentLoop 主循环里，循环就会有两条几乎相同但细节不同的路径。事实上旧代码正是如此——`agent/loop.py` 中 `_run_anthropic`（~100 行）和 `_run_openai`（~100 行），80% 的代码是相同的：注入上下文、记忆召回、压缩、工具执行、结果追加。只有 API 调用方式和消息格式不同。

Backend 模块的设计目标：**把厂商差异封装在策略类里。AgentLoop 只看到统一接口。新增模型厂商只需加一个文件**。

## 2. 核心概念

### 2.1 策略模式：依赖倒置

```
AgentLoop ——依赖→ Backend（抽象接口，backend/base.py）
                         ↑
                         ├── AnthropicBackend（backend/anthropic.py）
                         │   处理 Messages API 流式事件
                         │   content_block_start → 检测 tool_use
                         │   content_block_delta  → text 回调 + partial_json 拼接
                         │   content_block_stop   → json.loads 解析工具参数
                         │   最后：过滤 thinking block
                         │
                         └── OpenAIBackend（backend/openai.py）
                             处理 Chat Completions 流式事件
                             chunk.choices[0].delta.content → 文本
                             chunk.choices[0].delta.tool_calls → 按 index 累积
                             最后：json.loads 解析 → 转 ToolCall
```

这是标准的策略模式 + 依赖倒置。AgentLoop 作为上层，只依赖 `Backend` 抽象接口，不依赖任何具体实现。`AnthropicBackend` 和 `OpenAIBackend` 作为下层，实现同一个接口。

`create_backend(provider, api_key, model, ...)` 是工厂函数——根据 `provider` 参数（"anthropic" 或 "openai"）返回对应的 Backend 实例。调用方不需要知道具体类的名字。

### 2.2 统一返回格式：BackendResponse

不管后端是 Anthropic 还是 OpenAI，AgentLoop 拿到的都是：

```python
@dataclass
class BackendResponse:
    text: str = ""                      # 模型的文本回复
    tool_calls: list[ToolCall] = []     # 工具调用列表
    usage: TokenUsage = TokenUsage()    # input_tokens + output_tokens

@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
```

`ToolCall` 是工具系统定义的统一类型——`id`、`name`、`input`(dict)、`provider`("anthropic"|"openai")。AnthropicBackend 从 `tool_use` block 中提取这些字段，OpenAIBackend 从 `function call` 中提取。AgentLoop 拿到的是统一的 `list[ToolCall]`。

### 2.3 流式输出机制

AgentLoop 不能等模型完整返回再展示——用户会盯着空白屏幕等几秒。它用两个并发任务：

```python
# 任务 1：调模型（异步执行）
text_events: asyncio.Queue[RuntimeEvent] = asyncio.Queue()

async def on_text_delta(text):
    await text_events.put(AssistantTextDelta(text))

call_task = asyncio.create_task(
    backend.call(on_text_delta=on_text_delta, ...)
)

# 任务 2：主循环取文本（每 50ms 检查一次）
while not call_task.done():
    try:
        event = await asyncio.wait_for(text_events.get(), timeout=0.05)
        yield event   # 发给 TUI/CLI 渲染
    except asyncio.TimeoutError:
        if agent.aborted:  # Ctrl+C？
            call_task.cancel()
```

`backend.call()` 在收到每个 text chunk 时调用 `on_text_delta(text)`——这是一个 async 回调，把 chunk 包装成 `RuntimeEvent` 放入队列。主循环每 50ms 取一次，取到就 yield 给消费端。50ms 对用户感知是瞬时的（相当于 20fps），同时给 abort 检查留有窗口。

### 2.4 thinking block 的完整生命周期

Anthropic 的 extended thinking 只在流式输出时展示给用户看——产生一种"模型在思考"的视觉反馈。但 thinking block **不能进入消息历史**——后续 API 调用不接受 `type: thinking` 的 content block。

`AnthropicBackend.call()` 的最后一件事是过滤：

```python
final_message.content = [
    b for b in final_message.content
    if b.type != "thinking"
]
```

这是静默过滤。用户看到了 thinking 内容（在流式输出时），但消息历史中没有。

## 3. 总体设计

### 3.1 文件结构

```
backend/
├── __init__.py       # create_backend() 工厂函数（30 行）
├── base.py           # Backend 抽象类 + BackendResponse + TokenUsage（60 行）
├── anthropic.py      # AnthropicBackend（160 行）
└── openai.py         # OpenAIBackend（110 行）
```

### 3.2 模块职责

| 文件 | 职责 | 变更原因 |
|------|------|---------|
| `base.py` | 定义 Backend 接口 + 统一返回类型 | 改接口时改（罕见，当前接口已稳定） |
| `anthropic.py` | Anthropic Messages API 流式调用 | Anthropic API 变更时改 |
| `openai.py` | OpenAI Chat Completions 流式调用 | OpenAI API 变更时改 |
| `__init__.py` | 工厂函数，按 provider 选实现 | 新增模型厂商时增加分支 |

### 3.3 与上下文的交互

Backend 不 import `Agent`、`AgentLoop`、`runtime/` 的任何文件。它只接收参数、返回结果。完全无状态——每次 `call()` 是独立调用。

But `models.py` 和 `backend/` 之间是双向关系：Backend 调 `models.py` 的 `get_max_output_tokens()`、`model_supports_thinking()`、`with_retry()`；`models.py` 不 import Backend。

`AnthropicBackend` 需要 `anthropic` 包（Anthropic 官方 Python SDK），`OpenAIBackend` 需要 `openai` 包。两者互不依赖。

## 4. 详细设计

### 4.1 Backend 接口（base.py，60 行）

`Backend` 抽象类定义三个方法：

**`call()`**——核心。接收消息历史、system prompt、工具定义、文本回调、thinking 模式，返回 `BackendResponse`。签名设计成 keyword-only arguments（`*, messages, system, tools, ...`），调用时参数名必须显式写出，减少参数顺序错误。

**`supports_thinking(model)`**——检查模型是否支持 extended thinking。`AnthropicBackend` 通过 `models.py` 的 `model_supports_thinking()` 判断。`OpenAIBackend` 始终返回 False。

**`supports_adaptive_thinking(model)`**——检查模型是否支持 adaptive thinking。目前只有 `opus-4-6` 和 `sonnet-4-6` 返回 True。

**`resolve_thinking_mode(thinking_enabled)`**——不是抽象方法但每个后端需要实现。根据 `thinking_enabled` 和模型能力决定最终的 thinking 模式：`"disabled"`（不启用）、`"enabled"`（启用但固定 budget）、`"adaptive"`（启用并让模型自适应）。

### 4.2 AnthropicBackend（160 行）

**构造函数**：接收 `api_key`、可选的 `base_url`、`model`。创建 `anthropic.AsyncAnthropic` 客户端。`api_key` 通过 kwargs 传入，`base_url` 仅在非空时传入（允许用户指向 Anthropic-compatible 代理）。

**`call()` 方法**的核心是 `_do()` 内部函数——包装在 `with_retry()` 中：

1. 组装 `create_params`——model、max_tokens、system、tools、messages。如果 `thinking_mode` 不是 disabled，添加 `thinking: {type: "enabled", budget_tokens: max_output - 1}`。
2. 调用 `client.messages.stream(**create_params)`，返回 async context manager。
3. 在 `async for event in stream:` 循环中处理三种事件类型：
   - `content_block_start`：检测 tool_use block，记录 `{index: {id, name, input_json: ""}}`
   - `content_block_delta`：text delta 通过 `on_text_delta` 回调；partial_json delta 累积到对应 index 的 `input_json` 中
   - `content_block_stop`：从 `tool_blocks_by_index` 中取出对应的 block，`json.loads(input_json)` 解析为 dict，加入 `completed_tool_blocks` 列表
4. `stream.get_final_message()` 获取完整消息。
5. 过滤 thinking block。
6. 遍历 `final_message.content`——text block 累积到 `text` 字符串，tool_use block 转为 `ToolCall` 对象。
7. 返回 `BackendResponse(text, tool_calls, usage)`。

**`block_to_dict()`**：静态方法。把 Anthropic content block 对象转为可序列化 dict。text block → `{"type": "text", "text": "..."}`。tool_use block → `{"type": "tool_use", "id": "...", "name": "...", "input": {...}}`。用于消息历史存储（`session/` JSON 序列化）。

### 4.3 OpenAIBackend（110 行）

**构造函数**：接收 `api_key`、`base_url`、`model`。创建 `openai.AsyncOpenAI` 客户端。`base_url` 是必填的——OpenAI 的 SDK 不像 Anthropic 那样有默认端点。

**`call()` 方法**的核心同样包装在 `with_retry()` 中：

1. 调用 `client.chat.completions.create(model, tools=to_openai_tools(tools), messages=messages, stream=True, stream_options={"include_usage": True})`。
2. `async for chunk in stream:` 循环：
   - 检查 `chunk.usage`——OpenAI streaming 的 token 用量只在最后一个 chunk 返回。
   - `chunk.choices[0].delta.content`——文本增量，通过 `on_text_delta` 回调。
   - `chunk.choices[0].delta.tool_calls`——增量 tool_calls，按 index 累积 `function.arguments`。
3. 遍历累积的 `tool_calls_map`，对每个 index 调用 `json.loads(arguments)` 解析为 dict，构建 `ToolCall` 列表。
4. 返回 `BackendResponse(text, tool_calls, usage)`。

**工具 schema 转换**：`models.py` 的 `to_openai_tools()` 把 Anthropic 风格的 `{name, description, input_schema}` 转为 OpenAI function calling 格式 `{"type": "function", "function": {"name": ..., "description": ..., "parameters": input_schema}}`。OpenAI 不使用 Anthropic 的顶层 `tools` 数组格式。

### 4.4 with_retry()——指数退避重试

`models.py` 中，被两个 Backend 共用：

```python
async def with_retry(fn, max_retries=3):
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as e:
            if not is_retryable(e) or attempt >= max_retries:
                raise
            delay = min(1000 * 2**attempt, 30000) / 1000 + random_jitter
            await asyncio.sleep(delay)
```

**`is_retryable()`** 判断可重试的错误：HTTP 429（限流）、503（服务不可用）、529（过载）、网络错误（ECONNRESET、ETIMEDOUT）、overloaded 消息。不重试 model_not_found（配置错误，重试没用）、No available channel（模型不存在）。

退避策略：第一次重试等 1 秒 + jitter，第二次等 2 秒 + jitter，以此类推，最大 30 秒。jitter 是 `hash(time.time()) % 1000 / 1000`——加 0-1 秒的随机抖动防止惊群效应（多个请求同时被限流后同时重试）。

## 5. 设计决策

### 决策 1：为什么 Backend 是独立策略类而非 Agent 的 Mixin

**问题**：旧代码 `AgentBackendMixin` 是 Agent 的 Mixin——通过 `self._anthropic_client` 访问 Agent 状态。换模型厂商需要改 Agent 核心代码。新增厂商需要改 Mixin 自身。

**可选方案**：(a) 保持 Mixin，加更多条件分支；(b) 把 API 调用逻辑移到独立策略类；(c) 用事件总线抽象模型调用。

**选择**：(b)。`AnthropicBackend` 和 `OpenAIBackend` 是独立类，通过 `call()` 的参数接收所有需要的数据（messages、system、tools）。AgentLoop 通过 `create_backend()` 工厂拿到实例。

**代价**：Agent 和 Backend 不再共享 Client 实例。但 Client 创建成本极低——单行 `AsyncAnthropic(api_key=...)`，不算实际代价。Backend 实例在会话期间复用。

### 决策 2：为什么 thinking block 被静默过滤

**问题**：Anthropic extended thinking 产生 `type: thinking` block。要不要保留在消息历史中？

**选择**：静默过滤。在流式输出时展示（给用户"模型思考中"的反馈），但在追加到消息历史前移除。

**为什么**：后续 API 调用**不接受**包含 `type: thinking` 的消息。如果保留，下一次调用会直接报错。这不是可选的优化——是 API 的硬性约束。流式输出期间用户看到 thinking 内容，但消息历史中没有。

**代价**：用户查看对话历史时看不到 thinking 内容。但这是 Anthropic 的设计意图——thinking 是内部推理过程，不应该作为对话的持久部分。

### 决策 3：为什么 AgentLoop 的 `_append_assistant_message` 和 `_append_tool_results` 不放在 Backend 中

**问题**：消息格式的差异由谁负责？Backend 负责调模型，消息格式的差异在追加到 Agent 的消息历史时也需要处理。

**选择**：Backend 只负责"调模型、返回统一格式"。消息追加的格式差异由 AgentLoop 的辅助方法处理（`_append_assistant_message`、`_append_tool_results`）。

**为什么**：Backend 的职责是"把厂商响应转换为 BackendResponse"——输入是 API response，输出是统一格式。消息追加是"把 BackendResponse 转换为 Agent 的消息格式"——属于 Agent 消息管理，不属于 API 调用。分离后，Backend 变得更纯粹——不接触 Agent 的内部状态。

**代价**：AgentLoop 中有 `if self.use_openai` 的分支。但这个分支比完整的"两套循环"简单得多——每个分支只做消息格式组装。

### 决策 4：为什么用 SDK 而非直接 HTTP

**问题**：要不要自己拼 HTTP 请求来调 Anthropic 和 OpenAI 的 API？

**选择**：使用官方 SDK（`anthropic` 和 `openai` 包）。

**为什么**：SDK 处理了流式解析的边界情况——重连、chunk 分片、错误响应解析、stream 中断恢复。自己拼 HTTP 请求需要自己处理 SSE（Server-Sent Events）协议和流式 chunk 的解析，不比 SDK 更可靠，而且 Anthropic 的 stream 格式和 OpenAI 的完全不同。

**代价**：依赖两个第三方包。但这两个包是 AI Agent 项目几乎必然的依赖——不存在"为了一个功能引入一个重依赖"的问题。

## 6. 面试考点

### Q1: 加第三个模型厂商（如 Google Gemini）需要改哪些文件？

两个文件就够了。`models.py`：加 Gemini 模型的上下文窗口、thinking 支持、输出 token 上限。新建 `backend/gemini.py`：实现 `Backend` 接口的 `call()` 方法——处理 Gemini 的流式响应格式，组装成 `BackendResponse`。`AgentLoop` 零改动——策略模式的价值在这里体现。

**追问"Backend 接口够用吗"**：`call()` 的参数（messages, system, tools, on_text_delta, thinking_mode）覆盖了当前所有主流模型 API 的公共部分。如果某个厂商需要特殊参数，可以在子类的 `__init__` 中接收，在 `call()` 内部使用——不影响接口。

### Q2: Anthropic 的 thinking block 为什么要过滤？

后续 API 调用不接受包含 `type: thinking` 的消息。如果保留会直接报错。过滤是 Anthropic API 的硬性约束。thinking 是模型内部推理过程——流式输出时展示给用户（"模型在思考"），但不应作为对话的持久内容。

**追问"用户看不到 thinking 历史会不会有问题"**：不会。thinking 是模型的中间推理，不是给用户消费的内容。用户关心的是最终答案——thinking 的作用是提升答案质量，不是给用户看推理过程。

### Q3: 双后端消息历史为什么不统一？

Anthropic 的 `tool_use`/`tool_result` 嵌套在 content list 中——一条 user message 可能包含多个 `tool_result` block，每个 block 引用之前 assistant 消息中的 tool_use id。OpenAI 的 tool 是独立 `role: tool` message——一条 tool message 对应一个 tool_call_id。

强行统一需要中间抽象层做双向映射——"通用消息模型" ↔ "厂商格式"。增加一层抽象但不减少任何代码量。两份简单的原生操作比一层复杂的抽象好维护。

**追问"加 Google Gemini 怎么办"**：加 `_gemini_messages` 列表，Agent 方法里加一个路由分支。不尝试统一三种格式——预测未来的统一抽象是过度设计。等有 5 个以上厂商时再考虑统一——此刻不是瓶颈。

### Q4: `with_retry()` 的 jitter 为什么要用 `hash(time.time())` 而非 `random.random()`？

`Date.now()` 和 `Math.random()` 在 workflow 脚本中不可用（会破坏幂等性）。虽然 Python 的 `with_retry` 没有这个限制，但保持一致的实现风格。功能上 `hash(time.time())` 提供了类似的随机性——足够分散同时被限流的请求，且不需要 import random。

### Q5: AgentLoop 的文本流为什么不用 WebSocket 或 SSE 而是 asyncio.Queue？

AgentLoop 和消费端（TUI/CLI/Server）在同一个 Python 进程中。asyncio.Queue 是进程内最快的异步通信方式——零网络开销、零序列化开销。WebSocket/SSE 用于跨进程或跨网络通信——当前不需要。

**追问"Server 模式怎么转发给远程客户端"**：Server 模式（`NanoCodeServer`）把 RuntimeEvent 序列化为 JSONL，通过 stdio/websocket/unix socket 转发。AgentLoop 不关心消费端是本地的还是远程的——它只产出 RuntimeEvent 流。

## 7. 代码导读

**推荐阅读顺序**：`base.py`（理解接口 + 返回类型）→ `anthropic.py`（主要后端，理解完整调用流程）→ `openai.py`（对比理解差异）→ `models.py`（with_retry 和 to_openai_tools）。

**关键代码行号**：
- `base.py:13-16`——BackendResponse 数据类定义
- `base.py:19-37`——Backend 抽象类定义
- `anthropic.py:48-139`——call() 完整流程（含 _do 内部函数）
- `anthropic.py:59-67`——thinking mode 参数组装
- `anthropic.py:75-111`——流式事件循环（content_block_start/delta/stop）
- `anthropic.py:116`——thinking block 过滤
- `anthropic.py:118-138`——tool_use/tool_call 转换
- `openai.py:48-113`——OpenAI call() 完整流程
- `openai.py:73-95`——增量 tool_calls 拼接（按 index 累积）
- `models.py:46-62`——with_retry 指数退避实现
- `models.py:110-122`——to_openai_tools schema 转换
