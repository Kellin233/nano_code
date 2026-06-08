"""Small terminal theme helpers."""

from __future__ import annotations

import os
import sys

from rich.console import Console


def use_color() -> bool:
    """Return whether styled terminal output should be enabled."""
    return not bool(os.environ.get("NO_COLOR"))


def make_console() -> Console:
    """Create the default console used by the TUI."""
    return Console(
        highlight=False,
        no_color=not use_color(),
        force_terminal=sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else None,
    )
