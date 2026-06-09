"""Slash command registry for the interactive TUI."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..capabilities.memory.store import list_memories
from ..capabilities.skills import discover_skills
from .state import CommandContext, CommandResult

CommandHandler = Callable[[CommandContext, str], Awaitable[CommandResult]]


@dataclass
class TuiCommand:
    name: str
    description: str
    handler: CommandHandler
    usage: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def display_usage(self) -> str:
        return self.usage or f"/{self.name}"


class CommandRegistry:
    def __init__(self):
        self._commands: dict[str, TuiCommand] = {}

    def register(self, command: TuiCommand) -> None:
        self._commands[command.name] = command
        for alias in command.aliases:
            self._commands[alias] = command

    def names(self) -> list[str]:
        return sorted({command.name for command in self._commands.values()})

    def visible(self) -> list[TuiCommand]:
        seen: set[str] = set()
        result: list[TuiCommand] = []
        for command in self._commands.values():
            if command.name in seen:
                continue
            seen.add(command.name)
            result.append(command)
        return sorted(result, key=lambda c: c.name)

    def find(self, name: str) -> TuiCommand | None:
        return self._commands.get(name)

    async def dispatch(self, text: str, ctx: CommandContext) -> CommandResult:
        if not text.startswith("/"):
            return CommandResult(handled=False)
        name, args = self._split(text[1:])
        command = self.find(name)
        if command is None:
            return CommandResult(handled=False)
        return await command.handler(ctx, args)

    def _split(self, text: str) -> tuple[str, str]:
        if not text.strip():
            return "", ""
        name, _, args = text.strip().partition(" ")
        return name, args.strip()


def default_commands() -> CommandRegistry:
    registry = CommandRegistry()
    registry.register(TuiCommand("clear", "Clear conversation history", _clear))
    registry.register(TuiCommand("cost", "Show token usage and estimated cost", _cost, aliases=("tokens",)))
    registry.register(TuiCommand("compact", "Compact the conversation", _compact))
    registry.register(TuiCommand("memory", "List saved memories", _memory))
    registry.register(TuiCommand("skills", "List user-invocable skills", _skills))
    registry.register(TuiCommand("help", "Show commands", _help))
    registry.register(TuiCommand("model", "Show the current model", _model))
    registry.register(TuiCommand("editor", "Open $EDITOR to write a prompt", _editor))
    registry.register(TuiCommand("multiline", "Toggle multiline input mode", _multiline))
    registry.register(TuiCommand("exit", "Exit Nano Code", _exit, aliases=("quit",)))
    return registry


async def _clear(ctx: CommandContext, args: str) -> CommandResult:
    _ = args
    ctx.agent.clear_history()
    return CommandResult()


async def _cost(ctx: CommandContext, args: str) -> CommandResult:
    _ = args
    ctx.agent.show_cost()
    return CommandResult()


async def _compact(ctx: CommandContext, args: str) -> CommandResult:
    _ = args
    await ctx.agent.compact()
    return CommandResult()


async def _memory(ctx: CommandContext, args: str) -> CommandResult:
    _ = args
    memories = list_memories()
    if not memories:
        ctx.renderer.info("No memories saved yet.")
        return CommandResult()
    lines = [f"[{m.type}] {m.name} - {m.description}" for m in memories]
    ctx.renderer.list_items(f"{len(memories)} memories:", lines)
    return CommandResult()


async def _skills(ctx: CommandContext, args: str) -> CommandResult:
    _ = args
    skills = [skill for skill in discover_skills() if skill.user_invocable]
    if not skills:
        ctx.renderer.info("No skills found. Add skills to .claude/skills/<name>/SKILL.md")
        return CommandResult()
    lines = [f"/{s.name} ({s.source}, {s.context}) - {s.description}" for s in skills]
    ctx.renderer.list_items(f"{len(skills)} skills:", lines)
    return CommandResult()


async def _help(ctx: CommandContext, args: str) -> CommandResult:
    _ = args
    commands = ctx.state.commands.visible() if ctx.state.commands else []
    lines = [f"/{c.name:<10} {c.description}" for c in commands]
    ctx.renderer.list_items("Commands:", lines)
    return CommandResult()


async def _model(ctx: CommandContext, args: str) -> CommandResult:
    _ = args
    ctx.renderer.info(f"Model: {ctx.agent.model}")
    return CommandResult()


async def _editor(ctx: CommandContext, args: str) -> CommandResult:
    prompt = ctx.input.open_editor(args)
    if prompt is None or not prompt.strip():
        ctx.renderer.warning("$EDITOR is not configured or no prompt was entered.")
        return CommandResult()
    return CommandResult(prompt=prompt.strip())


async def _multiline(ctx: CommandContext, args: str) -> CommandResult:
    _ = args
    ctx.input.multiline = not ctx.input.multiline
    ctx.state.multiline = ctx.input.multiline
    mode = "on" if ctx.input.multiline else "off"
    ctx.renderer.info(f"Multiline mode: {mode}")
    return CommandResult()


async def _exit(ctx: CommandContext, args: str) -> CommandResult:
    _ = args
    ctx.state.should_exit = True
    return CommandResult(exit=True)
