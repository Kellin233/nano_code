"""命令行入口与交互式循环。"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys

from .agent import Agent
from .sandbox import build_sandbox_config
from .ui import print_welcome, print_user_prompt, print_error, print_info
from .session import load_session, get_latest_session_id
from .memory.store import list_memories
from .skill import discover_skills, get_skill_by_name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="nano-code",
        description="Nano Code — a lightweight coding agent",
        add_help=False,
    )
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


async def run_repl(agent: Agent) -> None:
    """交互式命令行循环。"""

    async def confirm_fn(message: str) -> bool:
        try:
            answer = input("  Allow? (y/n): ")
            return answer.lower().startswith("y")
        except EOFError:
            return False

    agent.set_confirm_fn(confirm_fn)

    sigint_count = 0

    def handle_sigint(sig, frame):
        nonlocal sigint_count
        if agent._aborted is False and agent._output_buffer is not None:
            # 智能体正在处理请求。
            agent.abort()
            print("\n  (interrupted)")
            sigint_count = 0
            print_user_prompt()
        else:
            sigint_count += 1
            if sigint_count >= 2:
                print("\nBye!\n")
                sys.exit(0)
            print("\n  Press Ctrl+C again to exit.")
            print_user_prompt()

    signal.signal(signal.SIGINT, handle_sigint)
    print_welcome()

    while True:
        print_user_prompt()
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!\n")
            break

        inp = line.strip()
        sigint_count = 0

        if not inp:
            continue
        if inp in ("exit", "quit"):
            print("\nBye!\n")
            break

        # 交互命令
        if inp == "/clear":
            agent.clear_history()
            continue
        if inp == "/cost":
            agent.show_cost()
            continue
        if inp == "/compact":
            try:
                await agent.compact()
            except Exception as e:
                print_error(str(e))
            continue
        if inp == "/memory":
            memories = list_memories()
            if not memories:
                print_info("No memories saved yet.")
            else:
                print_info(f"{len(memories)} memories:")
                for m in memories:
                    print(f"    [{m.type}] {m.name} — {m.description}")
            continue
        if inp == "/skills":
            skills = [s for s in discover_skills() if s.user_invocable]
            if not skills:
                print_info("No skills found. Add skills to .claude/skills/<name>/SKILL.md")
            else:
                print_info(f"{len(skills)} skills:")
                for s in skills:
                    print(f"    /{s.name} ({s.source}, {s.context}) — {s.description}")
            continue

        # 技能调用：/<技能名> [参数]
        if inp.startswith("/"):
            space_idx = inp.find(" ")
            cmd_name = inp[1:space_idx] if space_idx > 0 else inp[1:]
            cmd_args = inp[space_idx + 1:] if space_idx > 0 else ""
            skill = get_skill_by_name(cmd_name)
            if skill:
                print_info(f"Invoking skill: {skill.name}")
                try:
                    result = await agent.invoke_skill(skill.name, cmd_args, invoked_by="user")
                    if result.startswith("Unknown skill") or "not user-invocable" in result:
                        print_error(result)
                except Exception as e:
                    if "abort" not in str(e).lower():
                        print_error(str(e))
                continue

        # 普通对话
        try:
            await agent.chat(inp)
        except Exception as e:
            if "abort" not in str(e).lower():
                print_error(str(e))


def main() -> None:
    args = parse_args()

    if args.help:
        print("""
Usage: nano-code [options] [prompt]

Options:
  --yolo, -y          Skip all confirmation prompts (bypassPermissions mode)
  --accept-edits      Auto-approve file edits, still confirm dangerous shell
  --dont-ask          Auto-deny anything needing confirmation (for CI)
  --thinking          Enable extended thinking (Anthropic only)
  --model, -m         Model to use (default: claude-opus-4-6, or NANO_CODE_MODEL env)
  --api-base URL      Use OpenAI-compatible API endpoint (key via env var)
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
  /clear              Clear conversation history
  /cost               Show token usage and cost
  /compact            Manually compact conversation
  /memory             List saved memories
  /skills             List available skills
  /<skill-name>       Invoke a skill (e.g. /commit "fix types")

Examples:
  nano-code "fix the bug in nano_code/agent/core.py"
  nano-code --yolo "run all tests and fix failures"
  nano-code --sandbox workspace "run tests"
  nano-code --sandbox microsandbox-safe "inspect untrusted project"
  nano-code --max-cost 0.50 --max-turns 20 "implement feature X"
  OPENAI_API_KEY=sk-xxx nano-code --api-base https://aihubmix.com/v1 --model gpt-4o "hello"
  nano-code --resume
  nano-code  # starts interactive REPL
""")
        sys.exit(0)

    permission_mode = _resolve_permission_mode(args)
    try:
        sandbox_config = build_sandbox_config(args)
    except ValueError as e:
        print_error(str(e))
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
        print_error(
            "API key is required.\n"
            "  Set ANTHROPIC_API_KEY (+ optional ANTHROPIC_BASE_URL) for Anthropic format,\n"
            "  or OPENAI_API_KEY + OPENAI_BASE_URL for OpenAI-compatible format."
        )
        sys.exit(1)

    agent = Agent(
        permission_mode=permission_mode,
        model=model,
        thinking=args.thinking,
        max_cost_usd=args.max_cost,
        max_turns=args.max_turns,
        api_base=resolved_api_base if resolved_use_openai else None,
        anthropic_base_url=resolved_api_base if not resolved_use_openai else None,
        api_key=resolved_api_key,
        sandbox_config=sandbox_config,
    )

    # 恢复会话
    if args.resume:
        session_id = get_latest_session_id()
        if session_id:
            session = load_session(session_id)
            if session:
                agent.restore_session({
                    "anthropicMessages": session.get("anthropicMessages"),
                    "openaiMessages": session.get("openaiMessages"),
                })
            else:
                print_info("No session found to resume.")
        else:
            print_info("No previous sessions found.")

    prompt = " ".join(args.prompt) if args.prompt else None

    if prompt:
        # 单次执行模式
        async def run_once() -> None:
            try:
                await agent.chat(prompt)
            finally:
                await agent.shutdown()

        try:
            asyncio.run(run_once())
        except Exception as e:
            print_error(str(e))
            sys.exit(1)
    else:
        # 交互式命令行
        async def run_interactive() -> None:
            try:
                await run_repl(agent)
            finally:
                await agent.shutdown()

        asyncio.run(run_interactive())


if __name__ == "__main__":
    main()
