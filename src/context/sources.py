"""上下文数据源：CLAUDE.md 解析、Git 状态、frontmatter + 共享数据类型。

合并了原来的：
  - claude_md.py（CLAUDE.md 发现、include 解析、指令加载）
  - git_context.py（Git 状态快照收集）
  - frontmatter.py（YAML frontmatter 解析/格式化）

共享数据类型（PromptDiagnostic, PromptBundle 等）放在本文件
以避免 builder.py 和 sources.py 之间的循环导入。

变更原因：
  - 改 CLAUDE.md 发现规则 → 改 _discover_instruction_files
  - 改 Git 上下文收集策略 → 改 collect_git_context
  - 改 frontmatter 格式 → 改 parse_frontmatter / format_frontmatter
"""

from __future__ import annotations

import concurrent.futures
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# ─── 共享数据类型 ───────────────────────────────


@dataclass(frozen=True)
class PromptDiagnostic:
    level: Literal["info", "warning", "error"]
    source: str
    message: str


@dataclass(frozen=True)
class ContextAttachment:
    title: str
    body: str


@dataclass
class PromptBundle:
    system_prompt: str
    startup_context: str
    diagnostics: list[PromptDiagnostic] = field(default_factory=list)

# ─── Frontmatter 解析 ──────────────────────────


@dataclass
class FrontmatterResult:
    meta: dict[str, str] = field(default_factory=dict)
    body: str = ""


def parse_frontmatter(content: str) -> FrontmatterResult:
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return FrontmatterResult(body=content)

    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx == -1:
        return FrontmatterResult(body=content)

    meta: dict[str, str] = {}
    for i in range(1, end_idx):
        colon_idx = lines[i].find(":")
        if colon_idx == -1:
            continue
        key = lines[i][:colon_idx].strip()
        value = lines[i][colon_idx + 1:].strip()
        if key:
            meta[key] = value

    body = "\n".join(lines[end_idx + 1:]).strip()
    return FrontmatterResult(meta=meta, body=body)


def format_frontmatter(meta: dict[str, str], body: str) -> str:
    lines = ["---"]
    for key, value in meta.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    return "\n".join(lines)


# ─── Git 上下文 ──────────────────────────────────

DISCLAIMER = "This git context is a snapshot from the start of the conversation and will not update automatically."
STATUS_LIMIT = 2000


@dataclass
class GitContextResult:
    text: str = ""
    diagnostics: list[PromptDiagnostic] = field(default_factory=list)


def collect_git_context(cwd: Path | None = None, timeout: float = 3.0) -> GitContextResult:
    cwd = (cwd or Path.cwd()).resolve()
    diagnostics: list[PromptDiagnostic] = []
    inside = _run_git(["rev-parse", "--is-inside-work-tree"], cwd, timeout)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return GitContextResult()

    commands = {
        "branch": ["rev-parse", "--abbrev-ref", "HEAD"],
        "remote_head": ["symbolic-ref", "refs/remotes/origin/HEAD"],
        "status": ["status", "--short"],
        "log": ["log", "--oneline", "-5"],
        "user": ["config", "user.name"],
    }
    results: dict[str, _GitRun] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(commands)) as pool:
        futures = {
            pool.submit(_run_git, command, cwd, timeout): name
            for name, command in commands.items()
        }
        for future in concurrent.futures.as_completed(futures, timeout=timeout + 1):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                diagnostics.append(PromptDiagnostic("warning", "git", f"{name} failed: {exc}"))

    lines = [DISCLAIMER]
    branch = _stdout(results.get("branch"))
    if branch:
        lines.append(f"Branch: {branch}")
    remote_head = _stdout(results.get("remote_head"))
    if remote_head:
        lines.append(f"Origin HEAD: {remote_head}")
    user = _stdout(results.get("user"))
    if user:
        lines.append(f"Git user: {user}")
    log = _stdout(results.get("log"))
    if log:
        lines.append("Recent commits:\n" + log)
    status = _stdout(results.get("status"))
    if status:
        if len(status) > STATUS_LIMIT:
            status = status[:STATUS_LIMIT] + "\n[Truncated: git status exceeded prompt budget.]"
            diagnostics.append(PromptDiagnostic("warning", "git", "status truncated by prompt budget"))
        lines.append("Status:\n" + status)

    return GitContextResult(text="\n".join(lines), diagnostics=diagnostics)


@dataclass
class _GitRun:
    returncode: int
    stdout: str
    stderr: str


def _run_git(command: list[str], cwd: Path, timeout: float) -> _GitRun:
    def timeout_output(value: bytes | str | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace").strip()
        return value.strip()

    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", *command],
            cwd=str(cwd),
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
        return _GitRun(proc.returncode, proc.stdout.strip(), proc.stderr.strip())
    except subprocess.TimeoutExpired as exc:
        return _GitRun(124, timeout_output(exc.stdout), "timeout")
    except Exception as exc:
        return _GitRun(1, "", str(exc))


def _stdout(result: _GitRun | None) -> str:
    if not result or result.returncode != 0:
        return ""
    return result.stdout.strip()


# ─── CLAUDE.md 加载 ──────────────────────────────

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
        return _render_instructions(self.instructions)


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


def _render_instructions(instructions: list[LoadedInstruction]) -> str:
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
    content: str, base_dir: Path, diagnostics: list[PromptDiagnostic],
    stack: list[Path], depth: int,
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
    match: re.Match, base_dir: Path, diagnostics: list[PromptDiagnostic],
    stack: list[Path], depth: int,
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
