# Providers：模型后端

## 1. 为什么需要 Providers

Anthropic 和 OpenAI-compatible API 都能“发消息、收回复”，但流式事件、tool call 格式、thinking 机制和 token usage 返回方式不同。如果这些差异进入 AgentLoop，循环就会变成多套重复分支。

`providers/` 的职责是把厂商差异封装成统一接口。AgentLoop 只看到 `BackendResponse(text, tool_calls, usage)`。

## 2. 文件结构

```
providers/
├── __init__.py       # create_backend() 工厂函数
├── base.py           # Backend 抽象类、BackendResponse、TokenUsage
├── anthropic.py      # Anthropic Messages API 流式解析
└── openai.py         # OpenAI Chat Completions 流式解析
```

依赖边界：

- `providers/` 只依赖 `agent/types.py` 和本层 helper。
- `providers/` 不 import `Agent`、`AgentLoop`、`AgentSession`、`cli/`、`tui/`。
- OpenAI/Anthropic SDK 只出现在具体 provider 文件中。

Provider 层要屏蔽的差异主要有五类：

| 差异 | Anthropic | OpenAI-compatible | 对上层暴露 |
|------|-----------|-------------------|------------|
| 消息格式 | Messages API content blocks | Chat Completions messages | `ConversationHistory` |
| 工具 schema | Anthropic tool schema | function calling schema | `ToolDef` |
| tool call streaming | `partial_json` content block delta | `tool_calls[index].function.arguments` chunk | `ToolCall` |
| thinking | request 参数和 thinking block | 当前不发送 Anthropic thinking 参数 | `thinking_mode` 字符串 |
| usage | Anthropic usage 字段 | OpenAI usage 字段或 dict | `TokenUsage` |

## 3. 统一接口

```python
class Backend(ABC):
    async def call(
        *,
        conversation: ConversationHistory,
        system: str,
        tools: list[dict],
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
        thinking_mode: str = "disabled",
    ) -> BackendResponse:
        ...
```

`BackendResponse`：

```python
@dataclass
class BackendResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
```

`ConversationHistory` 和 `ToolCall` 都来自 `agent/types.py`，不是 provider 或 tools 包私有类型。这让 provider、agent core、ToolRuntime 可以共享同一套协议类型。Provider 内部负责把 canonical conversation 转成 Anthropic Messages API 或 OpenAI Chat Completions 的 wire payload。

## 4. 调用链路与流式输出

Provider 调用链路：

```text
AgentLoop
  → backend.resolve_thinking_mode(agent.thinking)
  → backend.call(conversation, system, tools, on_text_delta, thinking_mode)
  → provider-specific wire payload
  → streaming text/tool chunks
  → BackendResponse(text, tool_calls, usage)
  → AgentLoop append assistant message or execute tools
```

Provider 收到文本 chunk 时调用 `on_text_delta(text)`。AgentLoop 把这个回调接到 `asyncio.Queue`，再 yield `AssistantTextDelta` 事件给 CLI/TUI/Server。

```
provider stream chunk
    → on_text_delta(text)
    → AgentLoop queue
    → RuntimeEvent("assistant.delta")
    → renderer/server
```

Provider 不直接产出 RuntimeEvent，因为事件是 Agent core 的协议层职责。

消息转换有一个重要约束：provider 可以改变 wire payload，但不能改变 canonical conversation。Anthropic 会把连续 user text 和 tool_result 合并成 Messages API 能接受的 user content；OpenAI 会把 tool result 转成 `role=tool` 消息。转换只发生在请求边界，session log 仍保存 provider-neutral 结构。

## 5. AnthropicBackend

`providers/anthropic.py` 负责：

- 创建 `anthropic.AsyncAnthropic` 客户端。
- 组装 Messages API 参数。
- 根据模型能力决定 thinking mode。
- 解析 `content_block_start`、`content_block_delta`、`content_block_stop`。
- 累积 `partial_json` 并转换成 `ToolCall`。
- 过滤 thinking block，避免进入后续消息历史。
- 返回统一 `BackendResponse`。

thinking block 可以在流式过程中展示，但不能进入消息历史，因为后续 API 调用不接受这类 block。

Anthropic tool call 的难点在于参数 JSON 是流式拼接出来的。实现会按 content block index 累积 `partial_json`，block stop 后解析成 dict，再生成 canonical `ToolCall(id, name, input, provider="anthropic")`。解析失败时输入会降级为空 dict，后续工具 schema 校验会返回工具错误，而不是让 provider 层崩掉整个循环。

## 6. OpenAIBackend

`providers/openai.py` 负责：

- 创建 `openai.AsyncOpenAI` 客户端。
- 把工具 schema 转为 OpenAI function calling 格式。
- 解析 streaming chunks。
- 按 tool call index 累积 `function.arguments`。
- 从最后的 usage chunk 获取 token 用量。
- 返回统一 `BackendResponse`。

OpenAI-compatible 后端当前不会向请求发送 Anthropic extended thinking 参数：它继承 `Backend.resolve_thinking_mode()`，请求级 `thinking_mode` 始终是 `disabled`。`supports_thinking()` 方法仍复用模型元数据 helper，主要保持 provider 接口形状一致，不代表 OpenAI Chat Completions 请求会启用 Anthropic thinking。

OpenAI-compatible tool call 的关键是按 `tool_call.index` 聚合碎片。一个 response 可以同时流式生成多个 function call，因此实现不能只维护一个 arguments buffer。最终按 index 排序生成 canonical tool calls，保证工具执行顺序稳定。

## 7. 新增模型厂商

新增 provider 的边界应该很小：

1. 在 `agent/models.py` 增加模型窗口、输出 token 上限、thinking 能力等元数据。
2. 新建 `providers/gemini.py` 或其他 provider 文件，实现 `Backend.call()`。
3. 在 `providers/__init__.py` 的 `create_backend()` 加分支。

不应修改 `agent/loop.py` 或 `agent/agent.py`。如果必须修改 loop，说明 provider 抽象漏了公共能力。

新增 provider 的检查清单：

- 能从 canonical `ConversationHistory` 转成目标 API payload。
- 能把文本流通过 `on_text_delta` 逐块回调。
- 能把工具调用稳定组装成 `ToolCall`，包括参数 JSON 解析失败的降级策略。
- 能把 usage 归一成 input/output/cache token。
- 能实现 `supports_thinking()`、`supports_adaptive_thinking()`、`resolve_thinking_mode()`，即使当前后端永远 disabled。
- 能复用 `with_retry()` 或提供等价的可重试错误边界，不重试 model_not_found 这类确定错误。

`agent/models.py` 不是 provider 私有文件。它保存模型窗口、max output tokens、thinking 能力和 schema 转换 helper，这些信息会同时影响 Provider、Agent effective window、Context Compact 和 Benchmark 中的 `context_window` override。

## 8. 设计决策

### 为什么 provider 返回 canonical 类型

`AgentLoop` 只处理 `BackendResponse` 和 canonical `ToolCall`，不处理 Anthropic content block 或 OpenAI function call chunk。这样模型厂商差异集中在 provider 文件里，工具执行、session log、context compact 和 run trace 都能共享同一套消息结构。

### 为什么 usage 在 provider 内归一

不同 API 返回 token usage 字段名不同。把 input/output/cache token 在 provider 内归一后，`Agent` 和 `RunStore` 可以统一累计费用和生成 report，不需要知道后端细节。

### 为什么 thinking mode 由 provider resolve

是否支持 extended/adaptive thinking 是模型和 provider 的共同约束。Loop 只传入用户是否启用 thinking；具体降级为 `disabled`、`enabled` 或 `adaptive` 由 provider 决定。

## 9. Benchmark 覆盖

`benchmarks/local-fixture` 不绑定具体厂商，但约束 provider 抽象：

- 所有任务只依赖 `BackendResponse(text, tool_calls, usage)`，不能让 Anthropic/OpenAI wire 格式漏到 AgentLoop。
- run artifacts 要记录 usage/cost 相关字段，因此 provider 必须归一化 input/output/cache token。
- context-governance 任务通过 `context_window` 压缩压力验证 `AgentConfig` 与模型窗口元数据共同生效。
- tool-call 任务要求 provider 正确把流式 tool call 组装成 canonical `ToolCall`。

## 10. 代码导读

阅读顺序：

```
providers/base.py
providers/anthropic.py
providers/openai.py
providers/__init__.py
agent/models.py
```
