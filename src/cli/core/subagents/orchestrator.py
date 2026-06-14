"""并行子 Agent 编排器。

一次性派发多个 SubAgentTask，asyncio 并行执行，控制超时和预算。
不引入工作池、消息队列、事件总线。只做并行编排这一件事。
"""

from __future__ import annotations

import asyncio
from typing import Any

from ...config import RuntimeConfig
from ...logging_config import get_logger

logger = get_logger("subagents.orchestrator")

DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_TURNS = 20
DEFAULT_MAX_CONCURRENCY = 4


def _normalise_allowed_tools(value: Any) -> set[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return {part.strip() for part in value.split(",") if part.strip()}
    if isinstance(value, (list, tuple, set)):
        return {str(part).strip() for part in value if str(part).strip()}
    return None


def _intersect_allowed_tools(left: set[str] | None, right: set[str] | None) -> set[str] | None:
    if left is None:
        return right
    if right is None:
        return left
    return left & right


class SubAgentOrchestrator:
    """并行派发多个子 Agent，收集结果。"""

    def __init__(self, parent_agent, *, max_concurrency: int = DEFAULT_MAX_CONCURRENCY):
        self.parent = parent_agent
        self.max_concurrency = max_concurrency

    async def dispatch(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """并行派发，返回结果列表（顺序与输入一致）。

        每个 task 是一个 dict：
          - type: str          # Agent 类型（必填）
          - prompt: str        # 任务描述（必填）
          - timeout: float     # 超时秒数（可选）
          - max_turns: int     # 最大对话轮次（可选）
        """
        if not tasks:
            return []

        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def _run_one(task: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                return await self._execute_task(task)

        return list(await asyncio.gather(*[_run_one(t) for t in tasks]))

    async def _execute_task(self, task: dict[str, Any]) -> dict[str, Any]:
        """执行单个子 Agent 任务，带超时保护。"""
        from . import get_sub_agent_config

        agent_type = task.get("type", "general")
        prompt = task.get("prompt", "")
        timeout = task.get("timeout", DEFAULT_TIMEOUT)
        max_turns = task.get("max_turns", DEFAULT_MAX_TURNS)

        if not prompt:
            return {"error": "empty prompt", "type": agent_type}

        # 获取子 Agent 配置（工具白名单 + system prompt）
        sub_config = get_sub_agent_config(agent_type)
        task_allowed_tools = _normalise_allowed_tools(task.get("allowed_tools"))
        runtime_allowed_tools = _intersect_allowed_tools(
            self.parent.config.allowed_tools,
            task_allowed_tools,
        )

        # 创建子 AgentSession，复用父会话的 sandbox manager。
        runtime_config = RuntimeConfig(
            model=self.parent.model,
            provider=self.parent.config.provider,
            api_key=self.parent.config.api_key,
            api_base=self.parent.config.api_base,
            anthropic_base_url=self.parent.config.anthropic_base_url,
            permission_mode=self.parent.permission_mode,
            is_sub_agent=True,
            custom_system_prompt=sub_config["system_prompt"],
            max_turns=max_turns,
            sandbox_config=self.parent.config.sandbox_config,
            allowed_tools=runtime_allowed_tools,
            workspace=self.parent.workspace,
        )
        from ...session import create_session

        sub_session = create_session(
            runtime_config,
            custom_tools=sub_config["tools"],
            sandbox_manager=self.parent.sandbox_manager,
            render_events=False,
        )

        try:
            result = await asyncio.wait_for(
                sub_session.run_once(prompt),
                timeout=timeout,
            )
            self.parent.agent.total_input_tokens += result["tokens"]["input"]
            self.parent.agent.total_output_tokens += result["tokens"]["output"]
            self.parent.agent.total_input_cache_hit_tokens += result["tokens"].get("input_cache_hit", 0)
            self.parent.agent.total_input_cache_miss_tokens += result["tokens"].get("input_cache_miss", 0)
            return {
                "type": agent_type,
                "text": result["text"],
                "tokens": result["tokens"],
            }
        except asyncio.TimeoutError:
            sub_session.abort()
            return {
                "type": agent_type,
                "error": "timeout",
                "text": f"Sub-agent '{agent_type}' timed out after {timeout}s.",
            }
        except Exception as exc:
            return {
                "type": agent_type,
                "error": str(exc),
                "text": f"Sub-agent '{agent_type}' failed: {exc}",
            }
