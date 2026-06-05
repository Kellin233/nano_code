"""Agent 软件包公开入口。

这个文件只负责“对外暴露什么”，不承载运行时逻辑。外部代码仍然使用
`from nano_code.agent import Agent`，因此把单文件 `agent.py` 拆成包以后，
调用方不需要改 import。

包内模块职责：
- `core.py`：Agent 主类、状态初始化、chat/run_once、会话和预算。
- `context.py`：模型上下文管理，包括记忆注入、compact、工具结果裁剪。
- `tools_runtime.py`：需要 Agent 状态的工具路由，包括 skill、sub-agent、MCP。
- `backends.py`：Anthropic / OpenAI 两套模型循环和流式协议处理。
- `models.py`：无状态模型辅助函数，如 context window、thinking、retry、schema 转换。

本文件也 re-export 了旧版 `agent.py` 中可直接访问的一些 helper/常量，
这是兼容层；真正实现仍然在对应子模块里。
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
