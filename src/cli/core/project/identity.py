"""Project identity and project-scoped storage paths.

This module is the single source of truth for deciding which project a
workspace belongs to and where user-local project data is stored.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectScope:
    workspace: Path
    home: Path
    identity_path: Path
    project_key: str
    project_dir: Path

    @property
    def memory_dir(self) -> Path:
        return self.project_dir / "memory"


def resolve_project_scope(
    workspace: Path | str | None = None,
    *,
    home: Path | str | None = None,
    create: bool = False,
    git_ceiling_directories: list[Path | str] | None = None,
) -> ProjectScope:
    workspace_path = _resolve_path(Path(workspace) if workspace is not None else Path.cwd())
    home_path = _resolve_path(Path(home) if home is not None else Path.home())
    identity = get_project_identity(workspace_path, git_ceiling_directories=git_ceiling_directories)
    project_key = _project_key_from_identity(identity)
    project_dir = home_path / ".nanocode" / "projects" / project_key
    if create:
        project_dir.mkdir(parents=True, exist_ok=True)
    return ProjectScope(
        workspace=workspace_path,
        home=home_path,
        identity_path=identity,
        project_key=project_key,
        project_dir=project_dir,
    )


def get_project_identity(
    workspace: Path | str | None = None,
    *,
    git_ceiling_directories: list[Path | str] | None = None,
) -> Path:
    workspace_path = _resolve_path(Path(workspace) if workspace is not None else Path.cwd())
    git_common = _git_common_dir(workspace_path, git_ceiling_directories=git_ceiling_directories)
    return git_common if git_common is not None else workspace_path


def get_project_key(
    workspace: Path | str | None = None,
    *,
    git_ceiling_directories: list[Path | str] | None = None,
) -> str:
    return _project_key_from_identity(
        get_project_identity(workspace, git_ceiling_directories=git_ceiling_directories)
    )


def get_project_memory_dir(
    workspace: Path | str | None = None,
    *,
    home: Path | str | None = None,
    create: bool = True,
    git_ceiling_directories: list[Path | str] | None = None,
) -> Path:
    scope = resolve_project_scope(
        workspace,
        home=home,
        create=create,
        git_ceiling_directories=git_ceiling_directories,
    )
    path = scope.memory_dir
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _project_key_from_identity(identity: Path) -> str:
    name_source = identity.parent.name if identity.name == ".git" else identity.name
    name = _safe_name(name_source or "project")
    digest = hashlib.sha256(str(identity).encode("utf-8")).hexdigest()[:16]
    return f"{name}-{digest}"


def _git_common_dir(
    cwd: Path,
    *,
    git_ceiling_directories: list[Path | str] | None = None,
) -> Path | None:
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", "rev-parse", "--git-common-dir"],
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            env=_git_env(git_ceiling_directories),
            timeout=2,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute():
        path = cwd / path
    return _resolve_path(path)


def _git_env(git_ceiling_directories: list[Path | str] | None) -> dict[str, str] | None:
    if not git_ceiling_directories:
        return None
    env = os.environ.copy()
    ceiling = os.pathsep.join(str(_resolve_path(Path(path))) for path in git_ceiling_directories)
    existing = env.get("GIT_CEILING_DIRECTORIES")
    env["GIT_CEILING_DIRECTORIES"] = ceiling if not existing else os.pathsep.join([ceiling, existing])
    return env


def _resolve_path(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser().absolute()


def _safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return name or "project"
