"""Permission policy package."""

from __future__ import annotations

from .policy import check_permission, reset_permission_cache

__all__ = ["check_permission", "reset_permission_cache"]

