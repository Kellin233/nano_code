# Backend：模型后端

## 概述

`backend/` 封装了模型 API 调用的全部细节。上层（AgentLoop）只依赖 `Backend` 接口，不关心具体厂商。新增模型厂商只需加一个文件。

## 架构

```
Backend (抽象类, backend/base.py)
    │
    ├── AnthropicBackend (backend/anthropic.py)
    │   Messages API 流式调用
    │   content_block_start/delta/stop 事件解析
    │
    ├── OpenAIBackend (backend/openai.py)
    │   Chat Completions 流式调用
    │   增量 tool_calls 拼接
    │
    └── create_backend() (backend/__init__.py)
        工厂函数：按 provider 选择实现
```

## Backend 接口

```python
class Backend(ABC):
    async def call(self, *, messages, system, tools,
                   on_text_delta, thinking_mode) -> BackendResponse: ...
    def supports_thinking(self, model) -> bool: ...
    def supports_adaptive_thinking(self, model) -> bool: ...

@dataclass
class BackendResponse:
    text: str                      # 文本内容
    tool_calls: list[ToolCall]     # 工具调用
    usage: TokenUsage              # input_tokens + output_tokens
```

不管后端是 Anthropic 还是 OpenAI，`AgentLoop` 只拿到 `BackendResponse`。

## AnthropicBackend

**流式解析**：Anthropic Messages API 有三种流式事件：
- `content_block_start`：检测 tool_use block，记录 id 和 name
- `content_block_delta`：text delta 通过 `on_text_delta` 回调发出；partial_json delta 累积拼接工具参数
- `content_block_stop`：工具参数完整，`json.loads` 解析为 dict

**thinking block 过滤**：Anthropic extended thinking 产生 `type: thinking` block。这些只在流式输出时展示，追加到消息历史前被过滤掉——后续 API 调用不接受 thinking block。

**retry**：所有 API 调用通过 `models.py` 的 `with_retry()` 做指数退避。只重试 429/503/529 和网络中断，不重试 model_not_found 等永久错误。

## OpenAIBackend

**流式解析**：OpenAI Chat Completions 的 tool_calls 是增量返回的——同一 index 的 `function.arguments` 分散在多帧中。OpenAIBackend 内部用 `tool_calls_map: dict[int, dict]` 按 index 累积，帧结束时统一 `json.loads`。

**工具 schema 转换**：`models.py` 的 `to_openai_tools()` 把 Anthropic 风格的 `input_schema` 转为 OpenAI function calling 格式。

## Token 统计与成本

每次 `Backend.call()` 返回后，`Agent.record_usage()` 更新累计用量。成本估算：

```python
cost = (input / 1_000_000) * 3 + (output / 1_000_000) * 15  # Opus 定价
```

## 指数退避重试

`models.py` 的 `with_retry()`：

```python
async def with_retry(fn, max_retries=3):
    for attempt in range(max_retries + 1):
        try: return await fn()
        except Exception as e:
            if not is_retryable(e) or attempt >= max_retries: raise
            delay = min(1000 * 2^attempt, 30000)/1000 + jitter
            await asyncio.sleep(delay)
```

## 面试考点

**Q: Anthropic thinking block 为什么必须从消息历史中过滤掉？**

后续 API 调用不接受 `type: thinking` content block——它是模型内部推理过程，不是对话内容。过滤是必须的清理步骤。

**Q: 加第三个模型厂商改哪些文件？**

两个：`models.py` 加模型元数据，新建 `backend/gemini.py` 实现 `Backend` 接口。AgentLoop 完全不用动。
