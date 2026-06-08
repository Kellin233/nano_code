"""Git startup snapshot collection."""

from __future__ import annotations

import concurrent.futures
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .types import PromptDiagnostic

DISCLAIMER = "This git context is a snapshot from the start of the conversation and will not update automatically."
STATUS_LIMIT = 2000


@dataclass
class GitContextResult:
    text: str = ""
    diagnostics: list[PromptDiagnostic] = field(default_factory=list)


def collect_git_context(cwd: Path | None = None, timeout: float = 3.0) -> GitContextResult:
    cwd = (cwd or Path.cwd()).resolve()
    diagnostics: list[PromptDiagnostic] = []
    inside = _run_git(["rev-parse", "--is-inside-work-tree"], cwd, timeout)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return GitContextResult()

    commands = {
        "branch": ["rev-parse", "--abbrev-ref", "HEAD"],
        "remote_head": ["symbolic-ref", "refs/remotes/origin/HEAD"],
        "status": ["status", "--short"],
        "log": ["log", "--oneline", "-5"],
        "user": ["config", "user.name"],
    }
    results: dict[str, _GitRun] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(commands)) as pool:
        futures = {
            pool.submit(_run_git, command, cwd, timeout): name
            for name, command in commands.items()
        }
        for future in concurrent.futures.as_completed(futures, timeout=timeout + 1):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                diagnostics.append(PromptDiagnostic("warning", "git", f"{name} failed: {exc}"))

    lines = [DISCLAIMER]
    branch = _stdout(results.get("branch"))
    if branch:
        lines.append(f"Branch: {branch}")
    remote_head = _stdout(results.get("remote_head"))
    if remote_head:
        lines.append(f"Origin HEAD: {remote_head}")
    user = _stdout(results.get("user"))
    if user:
        lines.append(f"Git user: {user}")
    log = _stdout(results.get("log"))
    if log:
        lines.append("Recent commits:\n" + log)
    status = _stdout(results.get("status"))
    if status:
        if len(status) > STATUS_LIMIT:
            status = status[:STATUS_LIMIT] + "\n[Truncated: git status exceeded prompt budget.]"
            diagnostics.append(PromptDiagnostic("warning", "git", "status truncated by prompt budget"))
        lines.append("Status:\n" + status)

    return GitContextResult(text="\n".join(lines), diagnostics=diagnostics)


@dataclass
class _GitRun:
    returncode: int
    stdout: str
    stderr: str


def _run_git(command: list[str], cwd: Path, timeout: float) -> _GitRun:
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", *command],
            cwd=str(cwd),
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
        return _GitRun(proc.returncode, proc.stdout.strip(), proc.stderr.strip())
    except subprocess.TimeoutExpired as exc:
        return _GitRun(124, exc.stdout or "", "timeout")
    except Exception as exc:
        return _GitRun(1, "", str(exc))


def _stdout(result: _GitRun | None) -> str:
    if not result or result.returncode != 0:
        return ""
    return result.stdout.strip()
