"""Backend 接口与统一返回类型。

每种模型厂商（Anthropic、OpenAI 等）提供各自的实现。
上层只看到 Backend 接口，不关心具体厂商差异。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..capabilities.tools.types import ToolCall


@dataclass
class TokenUsage:
    """一次 API 调用的 token 用量。"""
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class BackendResponse:
    """后端返回的统一结构。

    不管 Anthropic 还是 OpenAI，对外暴露相同的字段。
    """
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)


class Backend(ABC):
    """模型后端抽象接口。"""

    @abstractmethod
    async def call(
        self,
        *,
        messages: list[dict],
        system: str,
        tools: list[dict],
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
        thinking_mode: str = "disabled",
    ) -> BackendResponse:
        """调用模型，返回统一的 BackendResponse。"""
        ...

    @abstractmethod
    def supports_thinking(self, model: str) -> bool:
        """检查模型是否支持 extended thinking。"""
        ...

    @abstractmethod
    def supports_adaptive_thinking(self, model: str) -> bool:
        """检查模型是否支持 adaptive thinking。"""
        ...

    def resolve_thinking_mode(self, thinking_enabled: bool) -> str:
        """Return provider-specific thinking mode for a request."""
        _ = thinking_enabled
        return "disabled"


__all__ = ["Backend", "BackendResponse", "TokenUsage"]
