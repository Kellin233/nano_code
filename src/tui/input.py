"""Interactive input handling with prompt-toolkit fallback."""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _cell_width(text: str) -> int:
    """Approximate terminal display width without requiring prompt-toolkit."""
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


@dataclass(frozen=True)
class ComposerLayout:
    """Calculate the live composer height from draft text and terminal width."""

    prompt_width: int = 2
    min_text_width: int = 8
    max_height: int = 8

    def desired_height(self, text: str, width: int) -> int:
        text_width = max(self.min_text_width, width)
        lines = text.split("\n") or [""]
        height = 0
        for index, line in enumerate(lines):
            reserve = self.prompt_width if index == 0 else 0
            available = max(self.min_text_width, text_width - reserve)
            height += max(1, math.ceil(_cell_width(line) / available))
        return max(1, min(self.max_height, height))


class _SimpleCompleter:
    def __init__(self):
        self.words: list[str] = []
        self.meta: dict[str, str] = {}

    def set_words(self, words: Iterable[str], meta: dict[str, str] | None = None) -> None:
        self.words = sorted(set(words))
        self.meta = meta or {}

    def get_completions(self, document, complete_event):
        _ = complete_event
        try:
            from prompt_toolkit.completion import Completion
        except Exception:
            return
        text = document.text_before_cursor
        token = text.split()[-1] if text.split() else text
        if not token:
            return
        for word in self.words:
            if word.startswith(token):
                yield Completion(word, start_position=-len(token), display_meta=self.meta.get(word, ""))

    async def get_completions_async(self, document, complete_event):
        for completion in self.get_completions(document, complete_event):
            yield completion


class TuiInput:
    """Read user input with history, completions and simple fallback."""

    def __init__(
        self,
        *,
        history_file: Path | None = None,
        force_simple: bool = False,
        input_fn: Callable[[str], str] = input,
        output: Any = None,
    ):
        self.history_file = history_file or Path.home() / ".nanocode" / "input-history"
        self.input_fn = input_fn
        self.output = output
        self._toolkit: dict[str, Any] = {}
        self.multiline = False
        self._completer = _SimpleCompleter()
        self._composer_layout = ComposerLayout()
        self._prompt_toolkit_ready = False
        self._status_text = ""
        if not force_simple:
            self._init_prompt_toolkit()

    @property
    def fancy(self) -> bool:
        return self._prompt_toolkit_ready

    def set_status(self, *, model: str = "", cwd: str = "") -> None:
        parts = []
        if model:
            parts.append(model)
        if cwd:
            parts.append(cwd)
        self._status_text = "  ·  ".join(parts)

    def set_completions(
        self,
        commands: list[str],
        skills: list[str],
        descriptions: dict[str, str] | None = None,
    ) -> None:
        words = [f"/{name}" for name in commands]
        words.extend(f"/{skill}" for skill in skills)
        meta = descriptions or {}
        self._completer.set_words(words, meta)

    async def read(self, prompt: str) -> str | None:
        if self._prompt_toolkit_ready:
            return await asyncio.to_thread(self._prompt_toolkit_read, prompt)
        return await asyncio.to_thread(self._simple_read, prompt)

    async def confirm(self, message: str) -> bool:
        _ = message
        answer = await asyncio.to_thread(self.input_fn, "  Allow? (y/n): ")
        return answer.lower().startswith("y")

    def open_editor(self, initial: str = "") -> str | None:
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
        if not editor:
            return None
        with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as tmp:
            tmp.write(initial)
            tmp_path = Path(tmp.name)
        try:
            result = subprocess.run([editor, str(tmp_path)])
            if result.returncode != 0:
                return None
            return tmp_path.read_text()
        finally:
            with contextlib.suppress(OSError):
                tmp_path.unlink()

    def _init_prompt_toolkit(self) -> None:
        if os.environ.get("CI") or not sys.stdin.isatty():
            return
        try:
            from prompt_toolkit.application import Application
            from prompt_toolkit.buffer import Buffer
            from prompt_toolkit.filters import Condition
            from prompt_toolkit.history import FileHistory
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
            from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
            from prompt_toolkit.layout.dimension import Dimension
            from prompt_toolkit.layout.menus import CompletionsMenu
            from prompt_toolkit.layout.processors import BeforeInput
            from prompt_toolkit.styles import Style
        except Exception:
            return
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            self._toolkit = {
                "Application": Application,
                "BeforeInput": BeforeInput,
                "Buffer": Buffer,
                "BufferControl": BufferControl,
                "CompletionsMenu": CompletionsMenu,
                "Condition": Condition,
                "ConditionalContainer": ConditionalContainer,
                "Dimension": Dimension,
                "FileHistory": FileHistory,
                "FormattedTextControl": FormattedTextControl,
                "HSplit": HSplit,
                "KeyBindings": KeyBindings,
                "Layout": Layout,
                "Style": Style,
                "Window": Window,
            }
            self._prompt_toolkit_ready = True
        except Exception:
            self._prompt_toolkit_ready = False

    def _prompt_toolkit_read(self, prompt: str) -> str | None:
        try:
            app = self._build_application(prompt)
            result = app.run(handle_sigint=False)
            return str(result) if result is not None else None
        except EOFError:
            return None

    def _build_application(self, prompt: str) -> Any:
        tk = self._toolkit
        Buffer = tk["Buffer"]
        BufferControl = tk["BufferControl"]
        BeforeInput = tk["BeforeInput"]
        CompletionsMenu = tk["CompletionsMenu"]
        Condition = tk["Condition"]
        ConditionalContainer = tk["ConditionalContainer"]
        Dimension = tk["Dimension"]
        FileHistory = tk["FileHistory"]
        FormattedTextControl = tk["FormattedTextControl"]
        HSplit = tk["HSplit"]
        KeyBindings = tk["KeyBindings"]
        Layout = tk["Layout"]
        Style = tk["Style"]
        Window = tk["Window"]
        Application = tk["Application"]

        buffer = Buffer(
            history=FileHistory(str(self.history_file)),
            completer=self._completer,
            complete_while_typing=True,
            enable_history_search=True,
            multiline=True,
        )
        keys = KeyBindings()

        @keys.add("enter")
        def _(event):
            if self.multiline:
                event.current_buffer.insert_text("\n")
            else:
                event.app.exit(result=event.current_buffer.text)

        @keys.add("escape", "enter")
        def _(event):
            event.app.exit(result=event.current_buffer.text)

        @keys.add("c-d")
        def _(event):
            if event.current_buffer.text:
                event.current_buffer.delete()
            else:
                event.app.exit(result=None)

        @keys.add("c-c")
        def _(event):
            event.app.exit(exception=KeyboardInterrupt)

        @keys.add("tab")
        def _(event):
            event.current_buffer.complete_next()

        @keys.add("s-tab")
        def _(event):
            event.current_buffer.complete_previous()

        input_control = BufferControl(
            buffer=buffer,
            input_processors=[BeforeInput([("class:prompt", prompt)])],
            focus_on_click=True,
        )
        input_window = Window(
            content=input_control,
            height=lambda: Dimension.exact(
                self._composer_layout.desired_height(buffer.text, self._terminal_width())
            ),
            wrap_lines=True,
            style="class:input-bar",
            char=" ",
        )
        completion_menu = ConditionalContainer(
            CompletionsMenu(max_height=8),
            filter=Condition(lambda: buffer.complete_state is not None),
        )
        status_window = Window(
            content=FormattedTextControl(self._bottom_bar),
            height=1,
            style="class:status-bar",
            char=" ",
        )
        root = HSplit([input_window, completion_menu, status_window])
        return Application(
            layout=Layout(root, focused_element=input_control),
            key_bindings=keys,
            style=Style.from_dict({
                "input-bar": "bg:#303030 #f2f2f2",
                "prompt": "bg:#303030 #d0d0d0",
                "status-bar": "bg:#101010 #8a8a8a",
                "status-model": "bg:#101010 #ffd866 bold",
                "status-path": "bg:#101010 #a6e22e",
                "completion-menu.completion": "bg:#202020 #f0f0f0",
                "completion-menu.completion.current": "bg:#444444 #ffffff",
                "completion-menu.meta.completion": "bg:#202020 #8a8a8a",
                "completion-menu.meta.completion.current": "bg:#444444 #d0d0d0",
            }),
            erase_when_done=True,
            full_screen=False,
        )

    def _terminal_width(self) -> int:
        return max(20, shutil.get_terminal_size((80, 24)).columns)

    def _bottom_bar(self):
        if not self._status_text:
            return [("class:status-bar", "")]
        if "  ·  " not in self._status_text:
            return [("class:status-model", f" {self._status_text}")]
        model, cwd = self._status_text.split("  ·  ", 1)
        return [
            ("class:status-model", f" {model} "),
            ("class:status-bar", " · "),
            ("class:status-path", cwd),
        ]

    def _simple_read(self, prompt: str) -> str | None:
        try:
            first = self.input_fn(prompt)
        except EOFError:
            return None
        if self._starts_block(first):
            return self._read_block(first)
        return first

    def _starts_block(self, text: str) -> bool:
        stripped = text.strip()
        return stripped.startswith("{") and stripped not in {"{}", "{ }"}

    def _read_block(self, first: str) -> str:
        tag = first.strip()[1:].strip()
        end = f"{tag}}}" if tag else "}"
        lines: list[str] = []
        while True:
            try:
                line = self.input_fn("")
            except EOFError:
                break
            if line.strip() == end:
                break
            lines.append(line)
        return "\n".join(lines)
