"""Shell command safety checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

SafetyLevel = Literal["safe", "confirm", "deny"]


@dataclass(frozen=True)
class ShellSafetyResult:
    level: SafetyLevel
    reason: str = ""
    commands: list[str] | None = None


DANGEROUS_PATTERNS = [
    (re.compile(r"\brm\s"), "rm command"),
    (re.compile(r"\bgit\s+(push|reset|clean|checkout\s+\.)"), "dangerous git command"),
    (re.compile(r"\bsudo\b"), "sudo command"),
    (re.compile(r"\bmkfs\b"), "filesystem formatting"),
    (re.compile(r"\bdd\s"), "raw disk write command"),
    (re.compile(r">\s*/dev/"), "redirect to device"),
    (re.compile(r"\bkill\b"), "kill process"),
    (re.compile(r"\bpkill\b"), "kill process"),
    (re.compile(r"\breboot\b"), "reboot command"),
    (re.compile(r"\bshutdown\b"), "shutdown command"),
    (re.compile(r"\bfind\b[\s\S]*\s-delete\b"), "find delete"),
    (re.compile(r"\b(curl|wget)\b[\s\S]*(\||;)\s*(sh|bash)\b"), "download and execute"),
    (re.compile(r"\bchmod\s+-R\s+777\b"), "recursive world-writable chmod"),
    (re.compile(r"\bchown\s+-R\b"), "recursive ownership change"),
    (re.compile(r"\bdel\s", re.IGNORECASE), "delete command"),
    (re.compile(r"\brmdir\s", re.IGNORECASE), "remove directory"),
    (re.compile(r"\bformat\s", re.IGNORECASE), "format command"),
    (re.compile(r"\btaskkill\s", re.IGNORECASE), "kill process"),
    (re.compile(r"\bRemove-Item\s", re.IGNORECASE), "remove item"),
    (re.compile(r"\bStop-Process\s", re.IGNORECASE), "stop process"),
]


COMPLEX_SHELL_PATTERNS = [
    re.compile(r"`[^`]+`"),
    re.compile(r"\$\("),
    re.compile(r"\beval\b"),
]


def check_shell_safety(command: str) -> ShellSafetyResult:
    for pattern, reason in DANGEROUS_PATTERNS:
        if pattern.search(command):
            return ShellSafetyResult("confirm", reason)
    for pattern in COMPLEX_SHELL_PATTERNS:
        if pattern.search(command):
            return ShellSafetyResult("confirm", "complex shell expansion")
    return ShellSafetyResult("safe")


def is_dangerous(command: str) -> bool:
    return check_shell_safety(command).level != "safe"

