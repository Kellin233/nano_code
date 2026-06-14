"""Project identity and project-scoped data paths."""

from .identity import (
    ProjectScope,
    get_project_identity,
    get_project_key,
    get_project_memory_dir,
    resolve_project_scope,
)

__all__ = [
    "ProjectScope",
    "get_project_identity",
    "get_project_key",
    "get_project_memory_dir",
    "resolve_project_scope",
]
