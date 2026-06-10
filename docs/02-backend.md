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

## 3. 统一接口

```python
class Backend(ABC):
    async def call(
        *,
        messages: list[dict],
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

`ToolCall` 来自 `agent/types.py`，不是 tools 包私有类型。这让 provider、agent core、ToolRuntime 可以共享同一套协议类型。

## 4. 流式输出

Provider 收到文本 chunk 时调用 `on_text_delta(text)`。AgentLoop 把这个回调接到 `asyncio.Queue`，再 yield `AssistantTextDelta` 事件给 CLI/TUI/Server。

```
provider stream chunk
    → on_text_delta(text)
    → AgentLoop queue
    → RuntimeEvent("assistant.delta")
    → renderer/server
```

Provider 不直接产出 RuntimeEvent，因为事件是 Agent core 的协议层职责。

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

## 6. OpenAIBackend

`providers/openai.py` 负责：

- 创建 `openai.AsyncOpenAI` 客户端。
- 把工具 schema 转为 OpenAI function calling 格式。
- 解析 streaming chunks。
- 按 tool call index 累积 `function.arguments`。
- 从最后的 usage chunk 获取 token 用量。
- 返回统一 `BackendResponse`。

OpenAI 不支持 Anthropic 的 extended thinking，因此 `supports_thinking()` 返回 false。

## 7. 新增模型厂商

新增 provider 的边界应该很小：

1. 在 `agent/models.py` 增加模型窗口、输出 token 上限、thinking 能力等元数据。
2. 新建 `providers/gemini.py` 或其他 provider 文件，实现 `Backend.call()`。
3. 在 `providers/__init__.py` 的 `create_backend()` 加分支。

不应修改 `agent/loop.py` 或 `agent/agent.py`。如果必须修改 loop，说明 provider 抽象漏了公共能力。

## 8. 代码导读

阅读顺序：

```
providers/base.py
providers/anthropic.py
providers/openai.py
providers/__init__.py
agent/models.py
```
