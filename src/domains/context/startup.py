"""Startup context rendering."""

from __future__ import annotations

import os
import platform
import sys
from datetime import date
from pathlib import Path

from .claude_md import load_project_instructions
from .git_context import collect_git_context
from .system_prompt import build_stable_system_prompt
from .types import PromptBundle, PromptDiagnostic


def build_prompt_bundle(cwd: Path | None = None, *, today: date | None = None) -> PromptBundle:
    cwd = (cwd or Path.cwd()).resolve()
    instructions = load_project_instructions(cwd)
    git = collect_git_context(cwd)
    startup_context = build_startup_context(
        cwd=cwd,
        today=today,
        git_context=git.text,
        project_instructions=instructions.text,
    )
    diagnostics: list[PromptDiagnostic] = []
    diagnostics.extend(instructions.diagnostics)
    diagnostics.extend(git.diagnostics)
    return PromptBundle(
        system_prompt=build_stable_system_prompt(),
        startup_context=startup_context,
        diagnostics=diagnostics,
    )


def build_startup_context(
    *,
    cwd: Path,
    today: date | None = None,
    git_context: str = "",
    project_instructions: str = "",
) -> str:
    current_date = (today or date.today()).isoformat()
    shell = (os.environ.get("ComSpec") or "cmd.exe") if sys.platform == "win32" else os.environ.get("SHELL", "/bin/sh")
    lines = [
        "<system-reminder>",
        "Startup context for this Nano Code session.",
        "",
        f"Current date: {current_date}.",
        f"Working directory: {cwd}.",
        f"Platform: {platform.system()} {platform.machine()}.",
        f"Shell: {shell}.",
    ]
    if git_context:
        lines.extend(["", "Git context:", git_context])
    if project_instructions:
        lines.extend(["", "Project instructions:", project_instructions])
    lines.append("</system-reminder>")
    return "\n".join(lines)
