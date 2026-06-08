"""Transcript-style terminal renderer."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import re
from pathlib import Path
from typing import Iterable

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from .theme import make_console


@dataclass
class _LiveFooter:
    status: str
    detail: str
    prompt: str
    model: str
    cwd: str


class TuiRenderer:
    """Render Nano Code output as a terminal-native chat transcript."""

    def __init__(self, console: Console | None = None, *, max_result_chars: int = 900):
        self.console = console or make_console()
        self.max_result_chars = max_result_chars
        self._assistant_active = False
        self._live_footer: _LiveFooter | None = None
        self._live_footer_rendered = False
        self._output_depth = 0

    def welcome(self, state) -> None:
        with self._transcript_output():
            self._welcome(state)

    def _welcome(self, state) -> None:
        model = getattr(state.agent, "model", "")
        cwd = str(Path.cwd())
        body = Text()
        body.append("  Nano Code", style="bold white")
        body.append("\n\n")
        body.append("model:     ", style="dim")
        body.append(model or "unknown", style="bold cyan")
        body.append("   ")
        body.append("/model", style="cyan")
        body.append(" to inspect")
        body.append("\n")
        body.append("directory: ", style="dim")
        body.append(cwd, style="bold")
        body.append("\n")
        body.append("status:    ", style="dim")
        body.append("ready", style="green")
        self.console.print()
        self.console.print(Panel(body, border_style="bright_black", width=min(self.console.width, 72)))
        self.console.print("[dim]Type a request, /help for commands, or /exit to quit.[/dim]\n")

    def prompt_marker(self) -> str:
        return "› "

    def user_message(self, text: str) -> None:
        with self._transcript_output():
            self._assistant_active = False
            width = max(20, self.console.width)
            lines = text.splitlines() or [""]
            self.console.print()
            for index, line in enumerate(lines):
                prefix = "› " if index == 0 else "  "
                rendered = (prefix + line)[:width]
                self.console.print(Text(rendered.ljust(width), style="white on #303030"))

    def status(self, label: str, detail: str = "") -> None:
        with self._transcript_output():
            self._assistant_active = False
            suffix = f" [dim]{escape(detail)}[/dim]" if detail else ""
            self.console.print(f"\n[cyan]• {escape(label)}[/cyan]{suffix}")

    def assistant_delta(self, text: str) -> None:
        if not text:
            return
        with self._transcript_output():
            if not self._assistant_active:
                stripped = text.lstrip("\n")
                if not stripped:
                    return
                self.console.file.write("\n• ")
                text = stripped
                self._assistant_active = True
            self.console.file.write(text)
            self.console.file.flush()

    def tool_call(self, name: str, inp: dict) -> None:
        with self._transcript_output():
            self._assistant_active = False
            summary = self._tool_summary(name, inp)
            suffix = f" [dim]{escape(summary)}[/dim]" if summary else ""
            self.console.print(f"\n[yellow]• running[/yellow] [bold]{escape(name)}[/bold]{suffix}")

    def tool_result(self, name: str, result: str) -> None:
        with self._transcript_output():
            self._assistant_active = False
            if name in {"edit_file", "write_file"} and not result.startswith("Error"):
                self._file_change(result)
                return
            if name == "run_shell":
                self._shell_result(result)
                return
            self._result_block(result)

    def info(self, message: str) -> None:
        with self._transcript_output():
            self._assistant_active = False
            self.console.print(f"\n[cyan]• info[/cyan] {escape(message)}")

    def warning(self, message: str) -> None:
        with self._transcript_output():
            self._assistant_active = False
            self.console.print(f"\n[yellow]• warning[/yellow] {escape(message)}")

    def error(self, message: str) -> None:
        with self._transcript_output():
            self._assistant_active = False
            self.console.print(f"\n[red]• error[/red] {escape(message)}")

    def confirm(self, message: str) -> None:
        self._clear_live_footer()
        self._assistant_active = False
        self.console.print(f"\n[yellow]• confirm[/yellow] {escape(message)}")

    def divider(self) -> None:
        with self._transcript_output():
            self._assistant_active = False
            return None

    def cost(self, input_tokens: int, output_tokens: int) -> None:
        with self._transcript_output():
            if self._assistant_active:
                self.console.print()
            self._assistant_active = False
            cost_in = (input_tokens / 1_000_000) * 3
            cost_out = (output_tokens / 1_000_000) * 15
            self.console.print()
            self.console.print(
                f"[dim]Tokens: {input_tokens} in / {output_tokens} out "
                f"(~${cost_in + cost_out:.4f})[/dim]"
            )

    def retry(self, attempt: int, max_retries: int, reason: str) -> None:
        with self._transcript_output():
            self._assistant_active = False
            self.console.print(f"\n[yellow]• retry[/yellow] {attempt}/{max_retries}: {escape(reason)}")

    def interrupted(self) -> None:
        with self._transcript_output(redraw_footer=False):
            self._assistant_active = False
            self.console.print("\n[yellow]• interrupted[/yellow]")

    def goodbye(self) -> None:
        self.end_live_footer()
        self._assistant_active = False
        self.console.print("\nBye!\n")

    def list_items(self, header: str, items: Iterable[str]) -> None:
        self.info(header)
        for item in items:
            self.console.print(f"  {escape(item)}")

    def sub_agent_start(self, agent_type: str, description: str) -> None:
        with self._transcript_output():
            self._assistant_active = False
            self.console.print(f"\n[magenta]• sub-agent[/magenta] {escape(agent_type)}: {escape(description)}")

    def sub_agent_end(self, agent_type: str) -> None:
        with self._transcript_output():
            self._assistant_active = False
            self.console.print(f"[magenta]• sub-agent[/magenta] {escape(agent_type)}: completed")

    def stop_spinner(self) -> None:
        """Kept as a renderer concern; currently no threaded spinner is used."""
        return None

    def begin_live_footer(
        self,
        *,
        status: str,
        detail: str,
        prompt: str = "",
        model: str = "",
        cwd: str = "",
    ) -> bool:
        """Show a Codex-style sticky footer while transcript output continues."""
        if not self._supports_live_footer():
            return False
        self._live_footer = _LiveFooter(status=status, detail=detail, prompt=prompt, model=model, cwd=cwd)
        self._draw_live_footer()
        return True

    def update_live_footer(self, *, status: str | None = None, detail: str | None = None) -> None:
        if not self._live_footer:
            return
        if status is not None:
            self._live_footer.status = status
        if detail is not None:
            self._live_footer.detail = detail
        if self._supports_live_footer():
            self._clear_live_footer()
            self._draw_live_footer()

    def end_live_footer(self) -> None:
        self._clear_live_footer()
        self._live_footer = None

    def _result_block(self, result: str) -> None:
        text = self._truncate(result)
        for line in text.splitlines() or [""]:
            self.console.print(f"[dim]  {escape(line)}[/dim]")

    def _file_change(self, result: str) -> None:
        lines = result.splitlines()
        if not lines:
            return
        self.console.print(f"[dim]  {escape(lines[0])}[/dim]")
        for line in lines[1:50]:
            if not line.strip():
                continue
            if line.startswith("@@"):
                self.console.print(f"[cyan]  {escape(line)}[/cyan]")
            elif line.startswith("- "):
                self.console.print(f"[red]  {escape(line)}[/red]")
            elif line.startswith("+ "):
                self.console.print(f"[green]  {escape(line)}[/green]")
            else:
                self.console.print(f"[dim]  {escape(line)}[/dim]")
        if len(lines) > 50:
            self.console.print(f"[dim]  ... ({len(lines) - 50} more lines)[/dim]")

    def _shell_result(self, result: str) -> None:
        if result.startswith("Command failed") or result.startswith("Command timed out") or result.startswith("Error"):
            self.error(self._truncate(result, limit=1400))
            return
        self._result_block(result)

    def _truncate(self, text: str, *, limit: int | None = None) -> str:
        limit = limit or self.max_result_chars
        if len(text) <= limit:
            return text
        keep = max(0, limit - 80)
        return text[:keep] + f"\n  ... ({len(text)} chars total)"

    def _tool_summary(self, name: str, inp: dict) -> str:
        if name in {"read_file", "write_file", "edit_file"}:
            return str(inp.get("file_path", ""))
        if name == "list_files":
            base = inp.get("path") or "."
            return f"{base}/{inp.get('pattern', '')}"
        if name == "grep_search":
            return f"{inp.get('pattern', '')!r} in {inp.get('path', '.')}"
        if name == "run_shell":
            command = str(inp.get("command", ""))
            return re.sub(r"\s+", " ", command)[:90]
        if name == "skill":
            return str(inp.get("skill_name", ""))
        if name == "agent":
            return f"{inp.get('type', 'general')}: {inp.get('description', '')}"
        return ""

    def _single_line(self, text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return ""
        value = " / ".join(lines)
        if len(value) <= 140:
            return value
        return value[:137] + "..."

    @contextmanager
    def _transcript_output(self, *, redraw_footer: bool = True):
        outermost = self._output_depth == 0
        if outermost:
            self._clear_live_footer()
        self._output_depth += 1
        try:
            yield
        finally:
            self._output_depth -= 1
            if outermost and redraw_footer:
                self._draw_live_footer()

    def _supports_live_footer(self) -> bool:
        return bool(getattr(self.console, "is_terminal", False))

    def _clear_live_footer(self) -> None:
        if not self._live_footer_rendered:
            return
        self.console.file.write("\x1b[J")
        self.console.file.flush()
        self._live_footer_rendered = False

    def _draw_live_footer(self) -> None:
        if not self._live_footer or not self._supports_live_footer():
            return
        footer = self._live_footer
        width = max(20, self.console.width - 1)
        self.console.file.write("\x1b[s")
        self.console.print()
        status_line = f"• {footer.status}"
        if footer.detail:
            status_line += f" ({footer.detail})"
        self.console.print(self._fit_line(status_line, width), style="bold white")
        prompt_text = f"› {footer.prompt}".rstrip()
        self.console.print(Text(self._fit_line(prompt_text, width), style="white on #303030"))
        bottom = "  ".join(part for part in [footer.model, footer.cwd] if part)
        self.console.print(self._fit_line(bottom, width), style="bold yellow")
        self.console.file.write("\x1b[u")
        self.console.file.flush()
        self._live_footer_rendered = True

    def _fit_line(self, text: str, width: int) -> str:
        if len(text) > width:
            text = text[: max(0, width - 1)]
        return text.ljust(width)


_renderer = TuiRenderer()


def get_renderer() -> TuiRenderer:
    return _renderer


def set_renderer(renderer: TuiRenderer) -> TuiRenderer:
    global _renderer
    previous = _renderer
    _renderer = renderer
    return previous
