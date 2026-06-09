"""Backend 模块 — 模型后端策略类。"""

from .anthropic import AnthropicBackend
from .base import Backend, BackendResponse, TokenUsage
from .openai import OpenAIBackend


def create_backend(
    *,
    provider: str,
    api_key: str,
    model: str,
    api_base: str | None = None,
    anthropic_base_url: str | None = None,
):
    """根据配置创建对应的 Backend 实例。"""
    if provider == "openai":
        return OpenAIBackend(
            api_key=api_key,
            base_url=api_base or "",
            model=model,
        )
    return AnthropicBackend(
        api_key=api_key,
        base_url=anthropic_base_url,
        model=model,
    )


__all__ = ["Backend", "BackendResponse", "TokenUsage", "AnthropicBackend", "OpenAIBackend", "create_backend"]
