"""Shared state objects for the terminal UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..runtime import RuntimeThread
    from .commands import CommandRegistry
    from .input import TuiInput
    from .renderer import TuiRenderer


@dataclass
class TuiState:
    """Mutable session state owned by the interactive TUI."""

    agent: "RuntimeThread"
    renderer: "TuiRenderer"
    input: "TuiInput"
    commands: "CommandRegistry | None" = None
    should_exit: bool = False
    multiline: bool = False
    processing: bool = False


@dataclass
class CommandContext:
    """Context passed to local slash commands."""

    state: TuiState

    @property
    def agent(self) -> "RuntimeThread":
        return self.state.agent

    @property
    def renderer(self) -> "TuiRenderer":
        return self.state.renderer

    @property
    def input(self) -> "TuiInput":
        return self.state.input


@dataclass
class CommandResult:
    """Result from a local command."""

    handled: bool = True
    exit: bool = False
    prompt: str | None = None
