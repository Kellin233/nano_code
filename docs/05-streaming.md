# 流式输出与双后端

## 概述

nanocode 支持 Anthropic Messages API 和 OpenAI Chat Completions API 两种后端。流式响应的解析差异被封装在 `AnthropicBackend` 和 `OpenAIBackend` 两个策略类中，`AgentLoop` 只看到统一的 `Backend` 接口。

## Backend 接口

```python
class Backend(ABC):
    async def call(self, *, messages, system, tools,
                   on_text_delta, thinking_mode) -> BackendResponse: ...
    def supports_thinking(self, model) -> bool: ...

@dataclass
class BackendResponse:
    text: str                      # 文本内容
    tool_calls: list[ToolCall]     # 工具调用
    usage: TokenUsage              # token 用量
```

不管后端是什么，上层只拿到 `BackendResponse`——text + tool_calls + usage。

## Anthropic 流式解析

Anthropic Messages API 的流式事件有三种类型：

```
content_block_start  → 检测 tool_use block，记录 id/name
content_block_delta  → text delta：通过 on_text_delta 回调发出
                     → partial_json delta：拼接工具调用参数 JSON
content_block_stop   → 工具参数完成，json.loads 解析为 dict
```

**thinking block 过滤**：Anthropic 的 extended thinking 会产生 `type: thinking` 的 content block。这些 block 只用于展示，会在追加到消息历史前被过滤掉——因为后续 API 调用不接受 thinking block。

## OpenAI 流式解析

OpenAI 的流式响应结构不同：

```
chunk.choices[0].delta.content     → 文本增量
chunk.choices[0].delta.tool_calls  → 函数调用增量（按 index 拼接 arguments）
chunk.usage                        → token 用量（仅在 stream_options: {include_usage: true} 时出现）
```

OpenAI 的 tool_calls 是增量返回的——同一个 index 的 `function.arguments` 在多帧中拼接。`OpenAIBackend` 内部用 `tool_calls_map: dict[int, dict]` 按 index 累积。

## Token 统计与预算

每次 `Backend.call()` 返回后，`Agent.record_usage(input_tokens, output_tokens)` 更新累计用量。成本估算公式：

```python
cost = (input_tokens / 1_000_000) * 3 + (output_tokens / 1_000_000) * 15
```

这是 Anthropic 的 Opus 定价（$3/$15 per MTok）。预算在每轮工具调用前检查——超限则产出 `BudgetExceeded` 事件并终止循环。

## 指数退避重试

`models.py` 的 `with_retry()` 对 API 调用加指数退避。只重试限流（429）、服务过载（503/529）和网络中断。不重试模型不存在（model_not_found）等错误：

```python
async def with_retry(fn, max_retries=3):
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as e:
            if not is_retryable(e) or attempt >= max_retries:
                raise
            delay = min(1000 * 2^attempt, 30000) / 1000 + jitter
            await asyncio.sleep(delay)
```

## 双后端消息历史

`_anthropic_messages` 和 `_openai_messages` 分开存储。**为什么不统一**：

- Anthropic：`tool_use` block 嵌套在 `assistant.content` 中，`tool_result` block 嵌套在 `user.content` 中
- OpenAI：`tool_calls` 嵌套在 `assistant` message 中，`role: tool` 是独立 message

两者的语义结构差异太大。统一抽象需要引入中间层来映射——增加复杂度但不增加价值。`Agent` 的公开方法（`add_user_message`、`add_assistant_message`、`add_tool_results`、`append_user_context`）在内部根据 `use_openai` 路由到正确的列表。

## 面试考点

**Q: Anthropic 的 thinking block 为什么需要从消息历史中过滤掉？**

因为后续 API 调用不接受包含 `type: thinking` content block 的消息。thinking 是模型内部的推理过程，不是对话内容。过滤是必须的清理步骤，不是可选优化。
