"""CLAUDE.md, .claude/rules, and include loading."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .frontmatter import parse_frontmatter
from .types import PromptDiagnostic

TEXT_EXTENSIONS = {".md", ".txt", ".rst", ".adoc", ".yaml", ".yml", ".json"}
MAX_INCLUDE_DEPTH = 5
MAX_FILE_CHARS = 20_000
MAX_TOTAL_CHARS = 60_000

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_INCLUDE_RE = re.compile(
    r"(?<![\w:/.-])@(?P<path>(?:~/|\.?/|[A-Za-z0-9_.-]+/|[A-Za-z0-9_.-]+)"
    r"[A-Za-z0-9_./~+-]*\.(?:md|txt|rst|adoc|yaml|yml|json))(?![\w@-])"
)


@dataclass
class LoadedInstruction:
    path: Path
    content: str
    kind: str = "claude"
    paths: str | None = None


@dataclass
class InstructionLoadResult:
    instructions: list[LoadedInstruction] = field(default_factory=list)
    diagnostics: list[PromptDiagnostic] = field(default_factory=list)

    @property
    def text(self) -> str:
        return render_instructions(self.instructions)


def load_project_instructions(
    cwd: Path | None = None,
    *,
    home: Path | None = None,
) -> InstructionLoadResult:
    cwd = (cwd or Path.cwd()).resolve()
    home = home or Path.home()
    diagnostics: list[PromptDiagnostic] = []
    files = _discover_instruction_files(cwd, home)
    instructions: list[LoadedInstruction] = []
    total_chars = 0

    for path, kind in files:
        loaded = _load_instruction_file(path, kind, diagnostics)
        if loaded is None:
            continue
        if not loaded.content.strip():
            diagnostics.append(PromptDiagnostic("info", str(path), "file is empty after comment stripping"))
            continue
        remaining = MAX_TOTAL_CHARS - total_chars
        if remaining <= 0:
            diagnostics.append(PromptDiagnostic("warning", str(path), "project instructions budget exhausted"))
            break
        if len(loaded.content) > remaining:
            loaded.content = loaded.content[:remaining] + "\n\n[Truncated: project instructions budget exhausted.]"
            diagnostics.append(PromptDiagnostic("warning", str(path), "project instructions truncated by total budget"))
        total_chars += len(loaded.content)
        instructions.append(loaded)

    return InstructionLoadResult(instructions=instructions, diagnostics=diagnostics)


def render_instructions(instructions: list[LoadedInstruction]) -> str:
    if not instructions:
        return ""
    parts = [
        "Project instructions loaded in increasing priority order. Later sections are closer to the working directory and take precedence."
    ]
    for item in instructions:
        scope = f" (path-scoped: {item.paths})" if item.paths else ""
        parts.append(f"## {item.path}{scope}\n{item.content.strip()}")
    return "\n\n".join(parts)


def _discover_instruction_files(cwd: Path, home: Path) -> list[tuple[Path, str]]:
    discovered: list[tuple[Path, str]] = []
    seen: set[Path] = set()

    def add(path: Path, kind: str) -> None:
        resolved = path.expanduser()
        try:
            resolved = resolved.resolve()
        except OSError:
            resolved = resolved.absolute()
        if resolved in seen or not resolved.is_file():
            return
        seen.add(resolved)
        discovered.append((resolved, kind))

    add(home / ".claude" / "CLAUDE.md", "user")

    dirs = list(reversed([cwd, *cwd.parents]))
    for directory in dirs:
        add(directory / "CLAUDE.md", "claude")
        add(directory / ".claude" / "CLAUDE.md", "claude")
        rules_dir = directory / ".claude" / "rules"
        if rules_dir.is_dir():
            for rule in sorted(rules_dir.rglob("*.md"), key=lambda p: str(p)):
                add(rule, "rule")
        add(directory / "CLAUDE.local.md", "local")

    return discovered


def _load_instruction_file(
    path: Path,
    kind: str,
    diagnostics: list[PromptDiagnostic],
) -> LoadedInstruction | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        diagnostics.append(PromptDiagnostic("error", str(path), f"read failed: {exc}"))
        return None

    if len(raw) > MAX_FILE_CHARS:
        raw = raw[:MAX_FILE_CHARS] + "\n\n[Truncated: file exceeded prompt budget.]"
        diagnostics.append(PromptDiagnostic("warning", str(path), "file truncated by per-file budget"))

    paths: str | None = None
    body = raw
    if kind == "rule":
        if raw.startswith("---") and raw.count("---") < 2:
            diagnostics.append(PromptDiagnostic("warning", str(path), "frontmatter parse failed"))
        parsed = parse_frontmatter(raw)
        body = parsed.body
        paths = parsed.meta.get("paths") or parsed.meta.get("path")

    content = _strip_html_comments_outside_code(body)
    content = _resolve_includes(content, path.parent, diagnostics, [path.resolve()], 0)
    content = _strip_html_comments_outside_code(content).strip()
    return LoadedInstruction(path=path, kind=kind, paths=paths, content=content)


def _split_fenced_segments(content: str) -> list[tuple[bool, str]]:
    segments: list[tuple[bool, str]] = []
    current: list[str] = []
    in_code = False
    fence = ""

    for line in content.splitlines(keepends=True):
        stripped = line.lstrip()
        marker = ""
        if stripped.startswith("```"):
            marker = "```"
        elif stripped.startswith("~~~"):
            marker = "~~~"

        if marker and not in_code:
            if current:
                segments.append((False, "".join(current)))
                current = []
            in_code = True
            fence = marker
            current.append(line)
            continue

        if marker and in_code and marker == fence:
            current.append(line)
            segments.append((True, "".join(current)))
            current = []
            in_code = False
            fence = ""
            continue

        current.append(line)

    if current:
        segments.append((in_code, "".join(current)))
    return segments


def _strip_html_comments_outside_code(content: str) -> str:
    parts: list[str] = []
    for in_code, segment in _split_fenced_segments(content):
        parts.append(segment if in_code else _HTML_COMMENT_RE.sub("", segment))
    return "".join(parts)


def _resolve_includes(
    content: str,
    base_dir: Path,
    diagnostics: list[PromptDiagnostic],
    stack: list[Path],
    depth: int,
) -> str:
    if depth >= MAX_INCLUDE_DEPTH:
        diagnostics.append(PromptDiagnostic("warning", str(base_dir), "maximum include depth reached"))
        return content

    rendered: list[str] = []
    for in_code, segment in _split_fenced_segments(content):
        if in_code:
            rendered.append(segment)
            continue
        rendered.append(_INCLUDE_RE.sub(lambda m: _include_replacement(m, base_dir, diagnostics, stack, depth), segment))
    return "".join(rendered)


def _include_replacement(
    match: re.Match,
    base_dir: Path,
    diagnostics: list[PromptDiagnostic],
    stack: list[Path],
    depth: int,
) -> str:
    raw_path = match.group("path")
    path = _resolve_include_path(raw_path, base_dir)
    source = str(stack[-1]) if stack else str(base_dir)

    if path.suffix.lower() not in TEXT_EXTENSIONS:
        diagnostics.append(PromptDiagnostic("warning", source, f"include skipped for non-text file: {raw_path}"))
        return ""
    if path in stack:
        diagnostics.append(PromptDiagnostic("warning", source, f"include cycle detected: {raw_path}"))
        return ""
    if not path.is_file():
        diagnostics.append(PromptDiagnostic("warning", source, f"include not found: {raw_path}"))
        return ""

    try:
        included = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        diagnostics.append(PromptDiagnostic("error", source, f"include read failed for {raw_path}: {exc}"))
        return ""

    if len(included) > MAX_FILE_CHARS:
        included = included[:MAX_FILE_CHARS] + "\n\n[Truncated: included file exceeded prompt budget.]"
        diagnostics.append(PromptDiagnostic("warning", str(path), "included file truncated by per-file budget"))

    included = _strip_html_comments_outside_code(included)
    return _resolve_includes(included, path.parent, diagnostics, [*stack, path], depth + 1)


def _resolve_include_path(raw_path: str, base_dir: Path) -> Path:
    if raw_path.startswith("~/"):
        return (Path.home() / raw_path[2:]).resolve()
    if raw_path.startswith("/"):
        return Path(raw_path).resolve()
    if raw_path.startswith("./"):
        return (base_dir / raw_path[2:]).resolve()
    return (base_dir / raw_path).resolve()
