"""Prompt construction public entrypoints.

The stable system prompt is intentionally kept separate from startup and
runtime context. Dynamic content such as CLAUDE.md, git status, memory, skills,
and MCP tool changes belongs in user-context attachments.
"""

from __future__ import annotations

from pathlib import Path

from .context.claude_md import load_project_instructions
from .context.git_context import collect_git_context
from .context.startup import build_prompt_bundle
from .context.system_prompt import SYSTEM_PROMPT_DYNAMIC_BOUNDARY, build_stable_system_prompt
from .context.types import PromptBundle, PromptDiagnostic


def build_system_prompt(deferred_tool_names: list[str] | None = None) -> str:
    """Return only the stable system prompt.

    `deferred_tool_names` is accepted for compatibility with older callers.
    Deferred tool listings are now rendered as dynamic attachments, not in the
    provider system prompt.
    """
    _ = deferred_tool_names
    return build_stable_system_prompt()


def load_claude_md() -> str:
    """Compatibility wrapper returning rendered project instructions."""
    return load_project_instructions(Path.cwd()).text


def get_git_context() -> str:
    """Compatibility wrapper returning the startup git snapshot."""
    return collect_git_context(Path.cwd()).text


__all__ = [
    "PromptBundle",
    "PromptDiagnostic",
    "SYSTEM_PROMPT_DYNAMIC_BOUNDARY",
    "build_prompt_bundle",
    "build_system_prompt",
    "get_git_context",
    "load_claude_md",
]
