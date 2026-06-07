"""Compatibility wrapper for permission policy."""

from __future__ import annotations

from ..permissions.policy import check_permission, reset_permission_cache
from ..permissions.rules import load_permission_rules, matches_rule as _matches_rule, rule_decision as _check_permission_rules
from ..permissions.shell import DANGEROUS_PATTERNS, is_dangerous

__all__ = [
    "DANGEROUS_PATTERNS",
    "check_permission",
    "is_dangerous",
    "load_permission_rules",
    "reset_permission_cache",
    "_check_permission_rules",
    "_matches_rule",
]
