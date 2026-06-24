"""CLI entrypoint."""

from __future__ import annotations

import asyncio
import sys

from .args import HELP_TEXT, parse_args, resolve_runtime_config
from .logging_config import get_logger, setup_logging
from .session import create_session

logger = get_logger("cli")


def main() -> None:
    setup_logging()
    args = parse_args()

    if args.help:
        print(HELP_TEXT)
        sys.exit(0)

    if args.server == "stdio":
        from .core.server.transports.stdio import run_stdio_server

        asyncio.run(run_stdio_server())
        return

    try:
        config = resolve_runtime_config(args)
    except ValueError as exc:
        from ..tui.renderer import get_renderer

        get_renderer().error(str(exc))
        sys.exit(1)

    if not config.api_key:
        from ..tui.renderer import get_renderer

        get_renderer().error(
            "API key is required.\n"
            "  Set ANTHROPIC_API_KEY (+ optional ANTHROPIC_BASE_URL) for Anthropic format,\n"
            "  or OPENAI_API_KEY + OPENAI_BASE_URL for OpenAI-compatible format."
        )
        sys.exit(1)

    thread_id = _resume_thread_id(args)
    session = create_session(config, thread_id=thread_id)
    prompt = " ".join(args.prompt) if args.prompt else None

    if prompt:
        asyncio.run(_run_once(session, prompt, args))
    else:
        asyncio.run(_run_interactive(session, args))


async def _run_once(session, prompt: str, args) -> None:
    from ..tui.renderer import get_renderer

    if args.yolo:

        async def _confirm_auto(message: str) -> bool:
            _ = message
            return True

        session.set_confirm_fn(_confirm_auto, auto_confirm=True)
    else:

        async def confirm(message: str) -> bool:
            get_renderer().confirm(message)
            try:
                answer = await asyncio.to_thread(input, "  Allow? (y/n): ")
                return answer.lower().startswith("y")
            except EOFError:
                return False

        session.set_confirm_fn(confirm)

    _restore_if_requested(session, args)

    try:
        await session.chat(prompt)
    except Exception as exc:
        logger.error("CLI fatal error: %s", exc, exc_info=True)
        get_renderer().error(str(exc))
        sys.exit(1)
    finally:
        await session.shutdown()


async def _run_interactive(session, args) -> None:
    from ..tui.app import TuiApp

    _restore_if_requested(session, args)
    try:
        await TuiApp(session).run()
    finally:
        await session.shutdown()


def _restore_if_requested(session, args) -> None:
    if not args.resume:
        return

    if not getattr(args, "_resume_session_id", None):
        return
    session.restore_from_persistence()


def _resume_thread_id(args) -> str | None:
    if not args.resume:
        return None
    from ..agent.runtime_management.persistence import get_latest_session_id
    from ..tui.renderer import get_renderer

    session_id = get_latest_session_id()
    if not session_id:
        get_renderer().info("No previous sessions found.")
        return None
    args._resume_session_id = session_id
    return session_id


if __name__ == "__main__":
    main()
