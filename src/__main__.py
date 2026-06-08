"""命令行入口与交互式循环。"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from .runtime import RuntimeConfig, RuntimeThread
from .domains.sandbox import build_sandbox_config
from .server.transports.stdio import run_stdio_server
from .tui import TuiApp
from .tui.renderer import get_renderer
from .session import load_session, get_latest_session_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="nanocode",
        description="Nano Code — a lightweight coding agent",
        add_help=False,
    )
    parser.add_argument("--server", choices=["stdio"], default=None, help="Run a protocol server transport")
    parser.add_argument("prompt", nargs="*", help="One-shot prompt")
    parser.add_argument("--yolo", "-y", action="store_true", help="Skip all confirmation prompts")
    parser.add_argument("--accept-edits", action="store_true", help="Auto-approve file edits")
    parser.add_argument("--dont-ask", action="store_true", help="Auto-deny confirmations (for CI)")
    parser.add_argument("--thinking", action="store_true", help="Enable extended thinking")
    parser.add_argument("--model", "-m", default=None, help="Model to use")
    parser.add_argument("--api-base", default=None, help="OpenAI-compatible API base URL")
    parser.add_argument("--resume", action="store_true", help="Resume last session")
    parser.add_argument("--max-cost", type=float, default=None, help="Max USD spend")
    parser.add_argument("--max-turns", type=int, default=None, help="Max agentic turns")
    parser.add_argument(
        "--sandbox",
        choices=[
            "workspace",
            "read-only",
            "local",
            "danger-full-access",
            "microsandbox",
            "microsandbox-dev",
            "microsandbox-safe",
            "microsandbox-strict",
        ],
        default=None,
        help="Shell sandbox profile",
    )
    parser.add_argument("--sandbox-network", choices=["none", "default"], default=None, help="Sandbox network mode")
    parser.add_argument("--sandbox-image", default=None, help="OCI image for microsandbox mode")
    parser.add_argument("--sandbox-memory", type=int, default=None, help="Sandbox memory in MiB")
    parser.add_argument("--sandbox-cpus", type=int, default=None, help="Sandbox vCPU count")
    parser.add_argument("--sandbox-readonly-workspace", action="store_true", help="Mount the workspace read-only in sandbox")
    parser.add_argument("--sandbox-no-network", action="store_true", help="Disable networking in sandbox")
    parser.add_argument("--sandbox-env", action="append", default=None, help="Forward an environment variable into sandbox")
    parser.add_argument("--sandbox-extra-write", action="append", default=None, help="Allow sandbox writes to an extra host path")
    parser.add_argument("--sandbox-allow-local-fallback", action="store_true", help="Allow explicit fallback to local when sandbox backend is unavailable")
    parser.add_argument("--help", "-h", action="store_true", help="Show help")
    return parser.parse_args()


def _resolve_permission_mode(args: argparse.Namespace) -> str:
    if args.yolo:
        return "bypassPermissions"
    if args.accept_edits:
        return "acceptEdits"
    if args.dont_ask:
        return "dontAsk"
    return "default"


def main() -> None:
    args = parse_args()

    if args.server == "stdio":
        asyncio.run(run_stdio_server())
        return

    if args.help:
        print("""
Usage: nanocode [options] [prompt]

Options:
  --yolo, -y          Skip all confirmation prompts (bypassPermissions mode)
  --accept-edits      Auto-approve file edits, still confirm dangerous shell
  --dont-ask          Auto-deny anything needing confirmation (for CI)
  --thinking          Enable extended thinking (Anthropic only)
  --model, -m         Model to use (default: claude-opus-4-6, or NANO_CODE_MODEL env)
  --api-base URL      Use OpenAI-compatible API endpoint (key via env var)
  --server stdio      Run JSONL protocol server over stdin/stdout
  --resume            Resume the last session
  --max-cost USD      Stop when estimated cost exceeds this amount
  --max-turns N       Stop after N agentic turns
  --sandbox PROFILE   Shell sandbox profile:
                      workspace, read-only, local, danger-full-access,
                      microsandbox-dev, microsandbox-safe, microsandbox-strict
                      (default: workspace on Linux, local elsewhere)
  --sandbox-network MODE
                      Sandbox network mode: none or default (default: none)
  --sandbox-image IMG OCI image for microsandbox mode (default: python:3.12)
  --sandbox-memory MiB
                      Sandbox memory in MiB (default: 2048)
  --sandbox-cpus N    Sandbox vCPU count (default: 2)
  --sandbox-readonly-workspace
                      Mount workspace read-only inside sandbox
  --sandbox-no-network
                      Disable networking inside sandbox
  --sandbox-env NAME  Forward an environment variable into sandbox
  --sandbox-extra-write PATH
                      Allow sandbox writes to an extra host path
  --sandbox-allow-local-fallback
                      Explicitly fall back to local if sandbox backend is unavailable
  --help, -h          Show this help

REPL commands:
  /help               Show available commands
  /clear              Clear conversation history
  /cost, /tokens      Show token usage and cost
  /compact            Manually compact conversation
  /memory             List saved memories
  /skills             List available skills
  /model              Show the current model
  /editor             Open $EDITOR to write a prompt
  /multiline          Toggle multiline input mode
  /exit, /quit        Exit Nano Code
  /<skill-name>       Invoke a skill (e.g. /commit "fix types")

Examples:
  nanocode "fix the bug in nanocode/agent/core.py"
  nanocode --yolo "run all tests and fix failures"
  nanocode --sandbox workspace "run tests"
  nanocode --sandbox microsandbox-safe "inspect untrusted project"
  nanocode --max-cost 0.50 --max-turns 20 "implement feature X"
  OPENAI_API_KEY=sk-xxx nanocode --api-base https://aihubmix.com/v1 --model gpt-4o "hello"
  nanocode --resume
  nanocode  # starts interactive REPL
""")
        sys.exit(0)

    permission_mode = _resolve_permission_mode(args)
    try:
        sandbox_config = build_sandbox_config(args)
    except ValueError as e:
        get_renderer().error(str(e))
        sys.exit(1)

    model = args.model or os.environ.get("NANO_CODE_MODEL", "claude-opus-4-6")
    api_base = args.api_base

    # 解析接口配置
    resolved_api_base = api_base
    resolved_api_key: str | None = None
    resolved_use_openai = bool(api_base)

    if os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_BASE_URL"):
        resolved_api_key = os.environ["OPENAI_API_KEY"]
        resolved_api_base = resolved_api_base or os.environ.get("OPENAI_BASE_URL")
        resolved_use_openai = True
    elif os.environ.get("ANTHROPIC_API_KEY"):
        resolved_api_key = os.environ["ANTHROPIC_API_KEY"]
        resolved_api_base = resolved_api_base or os.environ.get("ANTHROPIC_BASE_URL")
        resolved_use_openai = False
    elif os.environ.get("OPENAI_API_KEY"):
        resolved_api_key = os.environ["OPENAI_API_KEY"]
        resolved_api_base = resolved_api_base or os.environ.get("OPENAI_BASE_URL")
        resolved_use_openai = True

    if not resolved_api_key and api_base:
        resolved_api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        resolved_use_openai = True

    if not resolved_api_key:
        get_renderer().error(
            "API key is required.\n"
            "  Set ANTHROPIC_API_KEY (+ optional ANTHROPIC_BASE_URL) for Anthropic format,\n"
            "  or OPENAI_API_KEY + OPENAI_BASE_URL for OpenAI-compatible format."
        )
        sys.exit(1)

    runtime = RuntimeThread(RuntimeConfig(
        permission_mode=permission_mode,
        model=model,
        provider="openai" if resolved_use_openai else "anthropic",
        thinking=args.thinking,
        max_cost_usd=args.max_cost,
        max_turns=args.max_turns,
        api_base=resolved_api_base if resolved_use_openai else None,
        anthropic_base_url=resolved_api_base if not resolved_use_openai else None,
        api_key=resolved_api_key,
        sandbox_config=sandbox_config,
    ))

    # 恢复会话
    if args.resume:
        session_id = get_latest_session_id()
        if session_id:
            session = load_session(session_id)
            if session:
                runtime.restore_session({
                    "anthropicMessages": session.get("anthropicMessages"),
                    "openaiMessages": session.get("openaiMessages"),
                })
            else:
                get_renderer().info("No session found to resume.")
        else:
            get_renderer().info("No previous sessions found.")

    prompt = " ".join(args.prompt) if args.prompt else None

    if prompt:
        # 单次执行模式
        async def confirm(message: str) -> bool:
            get_renderer().confirm(message)
            try:
                answer = await asyncio.to_thread(input, "  Allow? (y/n): ")
                return answer.lower().startswith("y")
            except EOFError:
                return False

        async def run_once() -> None:
            runtime.set_confirm_fn(confirm)
            try:
                await runtime.chat(prompt)
            finally:
                await runtime.shutdown()

        try:
            asyncio.run(run_once())
        except Exception as e:
            get_renderer().error(str(e))
            sys.exit(1)
    else:
        # 交互式命令行
        async def run_interactive() -> None:
            try:
                await TuiApp(runtime).run()
            finally:
                await runtime.shutdown()

        asyncio.run(run_interactive())


if __name__ == "__main__":
    main()
