"""模型元数据与 API 辅助函数。

本模块是纯辅助层，不依赖 Agent 实例，也不读写消息历史。
被 runtime/、backend/、cli/ 共同引用。

提供：
- 模型上下文窗口大小
- thinking / adaptive thinking 支持判断
- 每个模型允许的最大输出 token
- API 调用的指数退避重试
- 工具 schema 从 Anthropic 格式转换到 OpenAI function calling 格式
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .types import DEFAULT_MAX_TOKENS, MAX_RETRIES, MAX_RETRY_DELAY_MS, ToolDef

# ─── 默认模型 ────────────────────────────────────

DEFAULT_MODEL = "claude-opus-4-6"

# ─── 模型上下文窗口 ───────────────────────────────

MODEL_CONTEXT: dict[str, int] = {
    "claude-opus-4-6": 200000,
    "claude-sonnet-4-6": 200000,
    "claude-sonnet-4-20250514": 200000,
    "claude-haiku-4-5-20251001": 200000,
    "claude-opus-4-20250514": 200000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
}


def get_context_window(model: str) -> int:
    """返回模型的上下文窗口大小。"""
    return MODEL_CONTEXT.get(model, 200000)


def model_supports_thinking(model: str) -> bool:
    """检查模型是否支持 extended thinking。"""
    m = model.lower()
    if "claude-3-" in m or "3-5-" in m or "3-7-" in m:
        return False
    return "claude" in m and any(x in m for x in ("opus", "sonnet", "haiku"))


def model_supports_adaptive_thinking(model: str) -> bool:
    """检查模型是否支持 adaptive thinking。"""
    m = model.lower()
    return "opus-4-6" in m or "sonnet-4-6" in m


def get_max_output_tokens(model: str) -> int:
    """返回模型的最大输出 token 数。"""
    m = model.lower()
    if "opus-4-6" in m:
        return 64000
    if "sonnet-4-6" in m:
        return 32000
    if any(x in m for x in ("opus-4", "sonnet-4", "haiku-4")):
        return 32000
    return DEFAULT_MAX_TOKENS


# ─── 指数退避重试 ────────────────────────────────


def is_retryable(error: Exception) -> bool:
    """判断 API 错误是否可重试。"""
    msg = str(error)
    if "model_not_found" in msg or "No available channel" in msg:
        return False
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    if status in (429, 503, 529):
        return True
    return "overloaded" in msg or "ECONNRESET" in msg or "ETIMEDOUT" in msg


async def with_retry(fn: Callable[[], Awaitable[Any]], max_retries: int = MAX_RETRIES) -> Any:
    """对 API 调用做轻量重试。只重试限流、服务过载和常见网络中断。"""
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as error:
            if attempt >= max_retries or not is_retryable(error):
                raise
            delay = min(1000 * (2 ** attempt), MAX_RETRY_DELAY_MS) / 1000 + (hash(str(time.time())) % 1000) / 1000
            await asyncio.sleep(delay)


# ─── 工具 schema 转换 ─────────────────────────────


def to_openai_tools(tools: list[ToolDef]) -> list[dict]:
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
