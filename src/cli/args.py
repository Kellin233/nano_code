"""命令行参数定义与配置解析。

变更原因：
  - 新增/删除 CLI 参数 → 改 parse_args()
  - 改权限模式或 sandbox 配置解析 → 改对应 resolve 函数
  - 改 API key/provider 的解析策略 → 改 resolve_runtime_config()
"""

from __future__ import annotations

import argparse
import os

from .config import RuntimeConfig
from .core.sandbox.config import build_sandbox_config


def parse_args() -> argparse.Namespace:
    """定义并解析所有 CLI 参数。"""
    parser = argparse.ArgumentParser(
        prog="nanocode",
        description="Nano Code — a lightweight coding agent",
        add_help=False,
    )
    parser.add_argument("prompt", nargs="*", help="One-shot prompt")
    parser.add_argument("--server", choices=["stdio"], default=None, help="Run a protocol server transport")
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
        "--allowed-tools",
        default=None,
        help="Comma-separated tool allowlist for this run; empty value denies all tools",
    )
    parser.add_argument(
        "--sandbox",
        choices=[
            "workspace", "read-only", "local", "danger-full-access",
            "microsandbox", "microsandbox-dev", "microsandbox-safe", "microsandbox-strict",
        ],
        default=None,
        help="Shell sandbox profile",
    )
    parser.add_argument("--sandbox-network", choices=["none", "default"], default=None)
    parser.add_argument("--sandbox-image", default=None)
    parser.add_argument("--sandbox-memory", type=int, default=None)
    parser.add_argument("--sandbox-cpus", type=int, default=None)
    parser.add_argument("--sandbox-readonly-workspace", action="store_true")
    parser.add_argument("--sandbox-no-network", action="store_true")
    parser.add_argument("--sandbox-env", action="append", default=None)
    parser.add_argument("--sandbox-extra-write", action="append", default=None)
    parser.add_argument("--sandbox-allow-local-fallback", action="store_true")
    parser.add_argument("--help", "-h", action="store_true", help="Show help")
    return parser.parse_args()


def resolve_permission_mode(args: argparse.Namespace) -> str:
    """根据 CLI 参数解析权限模式。"""
    if args.yolo:
        return "bypassPermissions"
    if args.accept_edits:
        return "acceptEdits"
    if args.dont_ask:
        return "dontAsk"
    return "default"


def resolve_allowed_tools(value: str | None) -> set[str] | None:
    """Parse a comma-separated tool allowlist."""
    if value is None:
        return None
    return {part.strip() for part in value.split(",") if part.strip()}


def resolve_context_window() -> int | None:
    """Parse an optional benchmark/test context-window override."""
    raw = os.environ.get("NANO_CODE_CONTEXT_WINDOW")
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def resolve_runtime_config(args: argparse.Namespace) -> RuntimeConfig:
    """将 CLI 参数和环境变量合并为 RuntimeConfig。

    按优先级解析 API key 和 provider：
    1. OPENAI_API_KEY + OPENAI_BASE_URL → openai
    2. ANTHROPIC_API_KEY → anthropic
    3. OPENAI_API_KEY 单独 → openai
    """
    permission_mode = resolve_permission_mode(args)

    model = args.model or os.environ.get("NANO_CODE_MODEL", "claude-opus-4-6")
    api_base = args.api_base

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

    return RuntimeConfig(
        permission_mode=permission_mode,
        model=model,
        provider="openai" if resolved_use_openai else "anthropic",
        thinking=args.thinking,
        max_cost_usd=args.max_cost,
        max_turns=args.max_turns,
        context_window=resolve_context_window(),
        api_base=resolved_api_base if resolved_use_openai else None,
        anthropic_base_url=resolved_api_base if not resolved_use_openai else None,
        api_key=resolved_api_key,
        sandbox_config=build_sandbox_config(args),
        allowed_tools=resolve_allowed_tools(args.allowed_tools),
    )


HELP_TEXT = """
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
  --allowed-tools CSV Only allow this comma-separated tool list for the run
  --sandbox PROFILE   Shell sandbox profile
  --sandbox-network MODE
  --sandbox-image IMG OCI image for microsandbox mode
  --sandbox-memory MiB
  --sandbox-cpus N    Sandbox vCPU count
  --sandbox-readonly-workspace
  --sandbox-no-network
  --sandbox-env NAME  Forward an environment variable into sandbox
  --sandbox-extra-write PATH
  --sandbox-allow-local-fallback
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
"""
