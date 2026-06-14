"""Recent file context refreshed after compaction."""

from __future__ import annotations

from pathlib import Path

from ....agent.harness.context.builder import render_system_reminder

TRACKED_FILE_TOOLS = {"read_file", "write_file", "edit_file"}


class RecentFileTracker:
    def __init__(self, workspace: Path | str, *, max_files: int = 5):
        self.workspace = Path(workspace).resolve()
        self.max_files = max_files
        self._paths: list[Path] = []

    def record_tool_call(self, tool_name: str, tool_input: dict) -> None:
        if tool_name not in TRACKED_FILE_TOOLS:
            return
        raw_path = tool_input.get("file_path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = self.workspace / path
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path.absolute()
        self._paths = [item for item in self._paths if item != resolved]
        self._paths.append(resolved)
        if len(self._paths) > self.max_files:
            self._paths = self._paths[-self.max_files:]

    def build_context(self, *, per_file_bytes: int = 8192, total_bytes: int = 25_000) -> str:
        if not self._paths:
            return ""

        remaining = total_bytes
        sections: list[str] = []
        for path in reversed(self._paths[-self.max_files:]):
            label = _display_path(path, self.workspace)
            if not _is_relative_to(path, self.workspace):
                sections.append(f"## {label}\nSkipped: outside workspace; re-read explicitly if needed.")
                continue
            if not path.exists():
                sections.append(f"## {label}\nSkipped: file no longer exists.")
                continue
            if not path.is_file():
                sections.append(f"## {label}\nSkipped: not a regular file.")
                continue
            if remaining <= 0:
                sections.append(f"## {label}\nSkipped: recent file context budget exhausted.")
                continue
            content, note = _read_preview(path, min(per_file_bytes, remaining))
            if content is None:
                sections.append(f"## {label}\nSkipped: {note}")
                continue
            remaining -= len(content.encode("utf-8"))
            suffix = f"\n[{note}]" if note else ""
            sections.append(f"## {label}\n```text\n{content.rstrip()}\n```{suffix}")

        if not sections:
            return ""
        return render_system_reminder(
            "Recent file context refreshed after compaction.",
            "\n\n".join(sections),
        )


def _read_preview(path: Path, max_bytes: int) -> tuple[str | None, str]:
    try:
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except OSError as exc:
        return None, str(exc)
    if b"\0" in data:
        return None, "binary file"
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]
    text = data.decode("utf-8", errors="replace")
    note = f"truncated to {max_bytes} bytes" if truncated else ""
    return text, note


def _display_path(path: Path, workspace: Path) -> str:
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return str(path)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
