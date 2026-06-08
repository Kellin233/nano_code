"""Terminal UI package for Nano Code."""

__all__ = [
    "CommandContext",
    "CommandRegistry",
    "CommandResult",
    "TuiApp",
    "TuiInput",
    "TuiRenderer",
    "TuiState",
    "default_commands",
    "get_renderer",
    "set_renderer",
]


def __getattr__(name: str):
    if name == "TuiApp":
        from .app import TuiApp

        return TuiApp
    if name in {"CommandRegistry", "default_commands"}:
        from .commands import CommandRegistry, default_commands

        return {"CommandRegistry": CommandRegistry, "default_commands": default_commands}[name]
    if name == "TuiInput":
        from .input import TuiInput

        return TuiInput
    if name in {"TuiRenderer", "get_renderer", "set_renderer"}:
        from .renderer import TuiRenderer, get_renderer, set_renderer

        return {
            "TuiRenderer": TuiRenderer,
            "get_renderer": get_renderer,
            "set_renderer": set_renderer,
        }[name]
    if name in {"CommandContext", "CommandResult", "TuiState"}:
        from .state import CommandContext, CommandResult, TuiState

        return {
            "CommandContext": CommandContext,
            "CommandResult": CommandResult,
            "TuiState": TuiState,
        }[name]
    raise AttributeError(name)
