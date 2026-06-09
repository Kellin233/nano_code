"""并行子 Agent 编排器。

一次性派发多个 SubAgentTask，asyncio 并行执行，控制超时和预算。
不引入工作池、消息队列、事件总线。只做并行编排这一件事。
"""

from __future__ import annotations

import asyncio
from typing import Any

from ...logging_config import get_logger
from ...runtime.agent import Agent, RuntimeConfig

logger = get_logger("subagents.orchestrator")

DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_TURNS = 20
DEFAULT_MAX_CONCURRENCY = 4


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

        # 创建子 Agent 实例——复用父 Agent 的 sandbox_manager
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
            workspace=self.parent.config.workspace,
        )
        sub_agent = Agent(
            runtime_config,
            custom_tools=sub_config["tools"],
            sandbox_manager=self.parent._sandbox_manager,
        )

        try:
            result = await asyncio.wait_for(
                sub_agent.run_once(prompt),
                timeout=timeout,
            )
            self.parent.total_input_tokens += result["tokens"]["input"]
            self.parent.total_output_tokens += result["tokens"]["output"]
            return {
                "type": agent_type,
                "text": result["text"],
                "tokens": result["tokens"],
            }
        except asyncio.TimeoutError:
            sub_agent.abort()
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
