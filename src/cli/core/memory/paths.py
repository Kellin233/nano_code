"""Project-local memory paths.

Project identity is resolved by ``cli.core.project.identity``. This module
keeps the memory-specific public helpers as thin wrappers.
"""

from __future__ import annotations

from pathlib import Path

from ..project import identity as project_identity


def get_project_identity(workspace: Path | str | None = None) -> Path:
    return project_identity.get_project_identity(workspace)


def get_project_key(workspace: Path | str | None = None) -> str:
    return project_identity.get_project_key(workspace)


def get_memory_dir(workspace: Path | str | None = None, *, create: bool = True) -> Path:
    return project_identity.get_project_memory_dir(workspace, create=create)
