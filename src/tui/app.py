"""Interactive TUI application."""

from __future__ import annotations

import asyncio
import signal
import sys
import time
from contextlib import suppress
from pathlib import Path

from ..cli.core.skills import get_skill_by_name
from .commands import CommandRegistry, default_commands
from .input import TuiInput
from .renderer import TuiRenderer, set_renderer
from .state import CommandContext, TuiState


class TuiApp:
    """Own the interactive REPL lifecycle."""

    def __init__(
        self,
        agent,
        input: TuiInput | None = None,
        renderer: TuiRenderer | None = None,
        commands: CommandRegistry | None = None,
    ):
        self.agent = agent
        self.input = input or TuiInput()
        self.renderer = renderer or TuiRenderer()
        if input is None:
            self.input.output = self.renderer.console.file
        self._model_label = getattr(agent, "model", "")
        self._cwd_label = self._display_path(Path.cwd())
        self.input.set_status(model=self._model_label, cwd=self._cwd_label)
        self.commands = commands or default_commands()
        self.state = TuiState(agent=agent, renderer=self.renderer, input=self.input)
        self.state.commands = self.commands
        self._sigint_count = 0

    async def run(self) -> None:
        previous_renderer = set_renderer(self.renderer)
        try:
            self._prepare()
            self.renderer.welcome(self.state)
            while not self.state.should_exit:
                line = await self._read_line()
                if line is None:
                    self.renderer.goodbye()
                    break
                await self._handle_line(line)
        finally:
            set_renderer(previous_renderer)

    def _prepare(self) -> None:
        self.agent.set_confirm_fn(lambda message: self.input.confirm(message))
        self._refresh_completions()
        with suppress(ValueError):
            signal.signal(signal.SIGINT, self._handle_sigint)

    async def _read_line(self) -> str | None:
        try:
            return await self.input.read(self.renderer.prompt_marker())
        except KeyboardInterrupt:
            return None

    async def _handle_line(self, line: str) -> None:
        text = line.strip()
        self._sigint_count = 0
        if not text:
            return
        if self.input.fancy:
            self.renderer.user_message(text)
        if text in {"exit", "quit"}:
            self.state.should_exit = True
            self.renderer.goodbye()
            return

        ctx = CommandContext(self.state)
        result = await self.commands.dispatch(text, ctx)
        if result.exit:
            self.renderer.goodbye()
            return
        if result.prompt:
            self.renderer.user_message(result.prompt)
            await self._chat(result.prompt)
            return
        if result.handled:
            self._refresh_completions()
            return

        if text.startswith("/"):
            handled = await self._try_skill(text)
            if handled:
                self._refresh_completions()
                return

        await self._chat(text)

    async def _try_skill(self, text: str) -> bool:
        name, _, args = text[1:].partition(" ")
        skill = get_skill_by_name(name)
        if not skill:
            return False
        self.renderer.info(f"Invoking skill: {skill.name}")
        try:
            result = await self.agent.invoke_skill(skill.name, args.strip(), invoked_by="user")
            if result.startswith("Unknown skill") or "not user-invocable" in result:
                self.renderer.error(result)
        except Exception as exc:
            if "abort" not in str(exc).lower():
                self.renderer.error(str(exc))
        return True

    async def _chat(self, prompt: str) -> None:
        self.state.processing = True
        started_at = time.monotonic()
        live_footer = self.input.fancy and self.renderer.begin_live_footer(
            status="Working",
            detail="0s • esc to interrupt",
            model=self._model_label,
            cwd=self._cwd_label,
        )
        ticker = None
        if live_footer:
            ticker = asyncio.create_task(self._tick_working_footer(started_at))
        else:
            self.renderer.status("working", "waiting for model and tools")
        try:
            await self.agent.chat(prompt)
        except Exception as exc:
            if "abort" not in str(exc).lower():
                self.renderer.error(str(exc))
        finally:
            if ticker:
                ticker.cancel()
                with suppress(asyncio.CancelledError):
                    await ticker
            if live_footer:
                self.renderer.end_live_footer()
            self.state.processing = False
            if not live_footer:
                self.renderer.status("ready", "awaiting input")

    async def _tick_working_footer(self, started_at: float) -> None:
        while self.state.processing:
            await asyncio.sleep(1)
            elapsed = int(time.monotonic() - started_at)
            self.renderer.update_live_footer(detail=f"{elapsed}s • esc to interrupt")

    def _refresh_completions(self) -> None:
        command_names: list[str] = []
        descriptions: dict[str, str] = {}
        for command in self.commands.visible():
            command_names.append(command.name)
            descriptions[f"/{command.name}"] = command.description
            for alias in command.aliases:
                command_names.append(alias)
                descriptions[f"/{alias}"] = f"alias for /{command.name}"
        try:
            from ..cli.core.skills import discover_skills

            discovered = [skill for skill in discover_skills() if skill.user_invocable]
            skills = [skill.name for skill in discovered]
            for skill in discovered:
                descriptions[f"/{skill.name}"] = skill.description
        except Exception:
            skills = []
        self.input.set_completions(command_names, skills, descriptions)

    def _handle_sigint(self, sig, frame) -> None:
        _ = sig, frame
        if self.state.processing or self.agent.is_processing:
            self.agent.abort()
            self.renderer.interrupted()
            self._sigint_count = 0
            return
        self._sigint_count += 1
        if self._sigint_count >= 2:
            self.renderer.goodbye()
            sys.exit(0)
        self.renderer.info("Press Ctrl+C again to exit.")

    def _display_path(self, path: Path) -> str:
        try:
            relative = path.relative_to(Path.home())
            if str(relative) == ".":
                return "~"
            return "~/" + str(relative)
        except ValueError:
            return str(path)
