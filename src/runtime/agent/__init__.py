"""Internal agent execution package.

The public runtime entrypoint is now `nanocode.runtime.RuntimeThread`. The
`Agent` class is the runtime's internal stateful execution adapter while
provider/tool behavior continues to move behind runtime ports.

包内模块职责：
- `core.py`：Agent 主类、状态初始化、chat/run_once、会话和预算。
- `context.py`：模型上下文管理，包括记忆注入、compact、工具结果裁剪。
- `tools_runtime.py`：需要 Agent 状态的工具路由，包括 skill、sub-agent、MCP。
- `backends.py`：Anthropic / OpenAI 两套模型循环和流式协议处理。
- `models.py`：无状态模型辅助函数，如 context window、thinking、retry、schema 转换。

This module intentionally does not own the application boundary.
"""

from __future__ import annotations

from .context import (
    KEEP_RECENT_RESULTS,
    MICROCOMPACT_IDLE_S,
    SNIP_PLACEHOLDER,
    SNIP_THRESHOLD,
    SNIPPABLE_TOOLS,
)
from .core import Agent
from .models import (
    MODEL_CONTEXT,
    _get_context_window,
    _get_max_output_tokens,
    _is_retryable,
    _model_supports_adaptive_thinking,
    _model_supports_thinking,
    _to_openai_tools,
    _with_retry,
)


__all__ = [
    "Agent",
    "KEEP_RECENT_RESULTS",
    "MICROCOMPACT_IDLE_S",
    "MODEL_CONTEXT",
    "SNIP_PLACEHOLDER",
    "SNIP_THRESHOLD",
    "SNIPPABLE_TOOLS",
    "_get_context_window",
    "_get_max_output_tokens",
    "_is_retryable",
    "_model_supports_adaptive_thinking",
    "_model_supports_thinking",
    "_to_openai_tools",
    "_with_retry",
]
