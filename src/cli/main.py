"""CLI 入口 — 依赖组装，启动应用。

变更原因：
  - 改启动流程（one-shot vs TUI vs server）→ 改 main()
  - 改参数解析 → 改 cli/args.py
"""

from __future__ import annotations

import asyncio
import sys

from ..backend import create_backend
from ..logging_config import get_logger, setup_logging
from ..runtime.agent import Agent
from ..runtime.loop import AgentLoop
from .args import HELP_TEXT, parse_args, resolve_runtime_config

logger = get_logger("cli")


def main() -> None:
    """NanoCode CLI 入口。

    流程：
    1. 解析 CLI 参数
    2. 组装 RuntimeConfig → Agent + Backend + AgentLoop
    3. 根据模式启动 TUI / 一次性执行 / Server
    """
    setup_logging()
    args = parse_args()

    if args.help:
        print(HELP_TEXT)
        sys.exit(0)

    if args.server == "stdio":
        from ..server.transports.stdio import run_stdio_server
        asyncio.run(run_stdio_server())
        return

    try:
        config = resolve_runtime_config(args)
    except ValueError as e:
        from ..tui.renderer import get_renderer
        get_renderer().error(str(e))
        sys.exit(1)

    if not config.api_key:
        from ..tui.renderer import get_renderer
        get_renderer().error(
            "API key is required.\n"
            "  Set ANTHROPIC_API_KEY (+ optional ANTHROPIC_BASE_URL) for Anthropic format,\n"
            "  or OPENAI_API_KEY + OPENAI_BASE_URL for OpenAI-compatible format."
        )
        sys.exit(1)

    agent = Agent(config)
    backend = create_backend(
        provider=config.provider,
        api_key=config.api_key,  # type: ignore[arg-type]
        model=config.model,
        api_base=config.api_base,
        anthropic_base_url=config.anthropic_base_url,
    )
    loop = AgentLoop(agent, backend)

    prompt = " ".join(args.prompt) if args.prompt else None

    if prompt:
        asyncio.run(_run_once(loop, prompt, args))
    else:
        asyncio.run(_run_interactive(loop, args))


async def _run_once(loop: AgentLoop, prompt: str, args) -> None:
    """一次性执行模式。"""
    from ..tui.renderer import get_renderer

    async def confirm(message: str) -> bool:
        get_renderer().confirm(message)
        try:
            answer = await asyncio.to_thread(input, "  Allow? (y/n): ")
            return answer.lower().startswith("y")
        except EOFError:
            return False

    loop.agent.set_confirm_fn(confirm)

    # 恢复会话
    if args.resume:
        from ..session import get_latest_session_id, load_session
        session_id = get_latest_session_id()
        if session_id:
            session = load_session(session_id)
            if session:
                loop.agent.restore_session({
                    "anthropicMessages": session.get("anthropicMessages"),
                    "openaiMessages": session.get("openaiMessages"),
                })
            else:
                get_renderer().info("No session found to resume.")
        else:
            get_renderer().info("No previous sessions found.")

    try:
        async for event in loop.run(prompt):
            _render_event(event)
    except Exception as e:
        logger.error("CLI fatal error: %s", e, exc_info=True)
        get_renderer().error(str(e))
        sys.exit(1)
    finally:
        await loop.agent.shutdown()


class _TuiAgentAdapter:
    """Adapter that exposes the methods TuiApp expects over AgentLoop."""

    def __init__(self, loop: AgentLoop):
        self.loop = loop
        self.agent = loop.agent
        self.model = self.agent.model

    @property
    def is_processing(self) -> bool:
        task = getattr(self.agent, "_current_task", None)
        return bool(task and not task.done())

    def set_confirm_fn(self, fn) -> None:
        self.agent.set_confirm_fn(fn)

    def abort(self) -> None:
        self.agent.abort()

    def clear_history(self) -> None:
        self.agent.clear_history()

    def show_cost(self) -> None:
        self.agent.show_cost()

    async def compact(self) -> None:
        from ..runtime.compressor import Compressor

        await Compressor(self.agent).compact_conversation()

    async def chat(self, prompt: str) -> None:
        async for event in self.loop.run(prompt):
            _render_event(event)

    async def invoke_skill(self, skill_name: str, args: str = "", invoked_by: str = "user") -> str:
        invocation = self.agent.get_skill_invocation().invoke(skill_name, args, invoked_by)
        if not invocation.ok:
            return invocation.error or f"Unknown skill: {skill_name}"
        self.agent.get_active_skills().record(invocation)
        prompt = str(invocation.rendered_prompt)
        await self.chat(prompt)
        return prompt


async def _run_interactive(loop: AgentLoop, args) -> None:
    """交互式 TUI 模式。"""
    from ..tui.app import TuiApp

    agent = loop.agent

    # 恢复会话
    if args.resume:
        from ..session import get_latest_session_id, load_session
        from ..tui.renderer import get_renderer
        session_id = get_latest_session_id()
        if session_id:
            session = load_session(session_id)
            if session:
                agent.restore_session({
                    "anthropicMessages": session.get("anthropicMessages"),
                    "openaiMessages": session.get("openaiMessages"),
                })
            else:
                get_renderer().info("No session found to resume.")
        else:
            get_renderer().info("No previous sessions found.")

    try:
        await TuiApp(_TuiAgentAdapter(loop)).run()
    finally:
        await agent.shutdown()


def _render_event(event) -> None:
    """渲染运行时事件到终端。"""
    from ..tui.renderer import get_renderer
    renderer = get_renderer()
    event_type = event.type

    if event_type == "user.input":
        return
    if event_type == "assistant.delta":
        renderer.assistant_delta(str(event.payload.get("text", "")))
    elif event_type == "tool.started":
        renderer.tool_call(str(event.payload.get("name", "")), event.payload.get("input") or {})
    elif event_type == "tool.finished":
        renderer.tool_result(str(event.payload.get("name", "")), str(event.payload.get("content", "")))
    elif event_type == "budget.exceeded":
        renderer.info(f"Budget exceeded: {event.payload.get('reason', '')}")
    elif event_type == "turn.finished":
        stop_reason = event.payload.get("stop_reason", "")
        if stop_reason == "stop":
            renderer.cost(
                event.payload.get("input_tokens", 0),
                event.payload.get("output_tokens", 0),
            )
    elif event_type == "context.compacted":
        renderer.info("Conversation compacted.")
    elif event_type == "runtime.error":
        renderer.error(str(event.payload.get("message", "")))


if __name__ == "__main__":
    main()
