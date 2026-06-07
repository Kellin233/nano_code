"""Session-level engine that drives the event loop."""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

from ..hooks import HookInput
from .events import AgentEvent, LoopFinished
from .loop import AgentLoop


class SessionEngine:
    def __init__(self, agent):
        self.agent = agent

    async def submit(self, user_message: str) -> AsyncIterator[AgentEvent]:
        await self._ensure_mcp()
        prompt = await self._apply_user_prompt_hooks(user_message)
        loop = AgentLoop(self.agent)

        async for event in loop.run(prompt):
            yield event
        if not self.agent.is_sub_agent:
            self.agent._auto_save()

    async def _ensure_mcp(self) -> None:
        agent = self.agent
        if agent._mcp_initialized or agent.is_sub_agent:
            return
        agent._mcp_initialized = True
        try:
            await agent._mcp_manager.load_and_connect()
            mcp_defs = agent._mcp_manager.get_tool_definitions()
            if mcp_defs:
                agent._tool_registry.add_many(
                    mcp_defs,
                    origin="mcp",
                    default_concurrency_safe=False,
                )
        except Exception as exc:
            print(f"[mcp] Init failed: {exc}", flush=True)

    async def _apply_user_prompt_hooks(self, user_message: str) -> str:
        agent = self.agent
        hook_input = HookInput(
            event="UserPromptSubmit",
            session_id=agent.session_id,
            cwd=str(Path.cwd()),
            prompt=user_message,
        )
        prompt = user_message
        for output in await agent._hook_manager.run("UserPromptSubmit", hook_input):
            if output.action == "deny":
                reason = output.reason or output.error or "User prompt denied by hook."
                return f"[UserPromptSubmit hook blocked the original prompt]\n{reason}"
            if output.action == "append_context" and output.content:
                prompt += "\n\n" + output.content
            if output.action == "modify" and output.updated_input and "prompt" in output.updated_input:
                prompt = str(output.updated_input["prompt"])
        return prompt
