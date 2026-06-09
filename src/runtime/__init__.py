"""Runtime 内核模块 — Agent 状态 + 主循环 + 压缩 + 事件。"""

from .agent import Agent, RuntimeConfig
from .loop import AgentLoop
from .compressor import Compressor
from .events import RuntimeEvent, TurnResult, AssistantTextDelta, ToolCallStarted, ToolCallFinished, PermissionRequested, BudgetExceeded, LoopFinished
from .approvals import ApprovalDecision, ApprovalManager, ApprovalRequest
from .thread import RuntimeThread

__all__ = [
    "Agent",
    "AgentLoop",
    "ApprovalDecision",
    "ApprovalManager",
    "ApprovalRequest",
    "AssistantTextDelta",
    "BudgetExceeded",
    "Compressor",
    "LoopFinished",
    "PermissionRequested",
    "RuntimeConfig",
    "RuntimeEvent",
    "RuntimeThread",
    "ToolCallFinished",
    "ToolCallStarted",
    "TurnResult",
]
