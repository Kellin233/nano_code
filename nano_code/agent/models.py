"""Agent 使用的模型辅助函数。

本模块是纯辅助层，不依赖 `Agent` 实例，也不读写消息历史。把这些函数单独放在
这里，是为了让 Anthropic 和 OpenAI-compatible 两个后端共享同一套模型规则：

- 模型上下文窗口大小。
- thinking / adaptive thinking 支持判断。
- 每个模型允许的最大输出 token。
- API 调用的指数退避重试。
- 工具 schema 从 Anthropic 格式转换到 OpenAI function calling 格式。

这里适合放“只由输入参数决定输出”的逻辑；如果函数需要访问当前会话状态，
应放到 `core.py`、`context.py`、`tools_runtime.py` 或 `backends.py`。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..tools import ToolDef
from ..ui import print_retry


# ─── 指数退避重试 ────────────────────────────────


def _is_retryable(error: Exception) -> bool:
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    if status in (429, 503, 529):
        return True
    msg = str(error)
    if "overloaded" in msg or "ECONNRESET" in msg or "ETIMEDOUT" in msg:
        return True
    return False


async def _with_retry(fn: Callable[[], Awaitable[Any]], max_retries: int = 3) -> Any:
    """对 API 调用做轻量重试。

    只重试限流、服务过载和常见网络中断，避免把真实参数错误隐藏起来。
    """
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as error:
            if attempt >= max_retries or not _is_retryable(error):
                raise
            delay = min(1000 * (2 ** attempt), 30000) / 1000 + (hash(str(time.time())) % 1000) / 1000
            status = getattr(error, "status_code", None) or getattr(error, "status", None)
            reason = f"HTTP {status}" if status else (getattr(error, "code", None) or "network error")
            print_retry(attempt + 1, max_retries, reason)
            await asyncio.sleep(delay)


# ─── 模型上下文与输出能力 ─────────────────────────


MODEL_CONTEXT = {
    "claude-opus-4-6": 200000,
    "claude-sonnet-4-6": 200000,
    "claude-sonnet-4-20250514": 200000,
    "claude-haiku-4-5-20251001": 200000,
    "claude-opus-4-20250514": 200000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
}


def _get_context_window(model: str) -> int:
    return MODEL_CONTEXT.get(model, 200000)


def _model_supports_thinking(model: str) -> bool:
    m = model.lower()
    if "claude-3-" in m or "3-5-" in m or "3-7-" in m:
        return False
    if "claude" in m and any(x in m for x in ("opus", "sonnet", "haiku")):
        return True
    return False


def _model_supports_adaptive_thinking(model: str) -> bool:
    m = model.lower()
    return "opus-4-6" in m or "sonnet-4-6" in m


def _get_max_output_tokens(model: str) -> int:
    m = model.lower()
    if "opus-4-6" in m:
        return 64000
    if "sonnet-4-6" in m:
        return 32000
    if any(x in m for x in ("opus-4", "sonnet-4", "haiku-4")):
        return 32000
    return 16384


# ─── 工具 schema 转换 ─────────────────────────────


def _to_openai_tools(tools: list[ToolDef]) -> list[dict]:
    """把 Anthropic 风格工具定义转换成 OpenAI function calling 格式。"""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]
