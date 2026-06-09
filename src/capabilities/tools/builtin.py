"""内置工具：schema 定义 + 实现函数。

合并了原 definitions.py（内置工具 schema）+ builtin.py（实现函数）。
加新工具时只需修改这一个文件。

变更原因：
  - 新增/修改/删除内置工具 → 改 BUILTIN_TOOL_DEFINITIONS + 对应的实现函数
  - 修改工具分类（read_only/edit_tool/concurrency_safe） → 改常量集合
"""

from __future__ import annotations

import copy
import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

from ...logging_config import get_logger
from ..memory.store import sync_memory_file
from .types import (
    DEFAULT_FETCH_MAX_LENGTH,
    DEFAULT_SHELL_TIMEOUT_MS,
    MAX_GREP_MATCHES,
    MAX_GREP_RESULTS,
    MAX_LIST_FILES_RESULTS,
    ToolDef,
)

# ─── 工具分类常量 ───────────────────────────────

READ_TOOL_NAMES = {"read_file", "list_files", "grep_search", "web_fetch", "list_mcp_resources", "read_mcp_resource"}
EDIT_TOOL_NAMES = {"write_file", "edit_file"}
CONCURRENCY_SAFE_BUILTIN_TOOLS = {"read_file", "list_files", "grep_search", "web_fetch", "list_mcp_resources", "read_mcp_resource"}

# ─── 内置工具 Schema 定义 ────────────────────────

BUILTIN_TOOL_DEFINITIONS: list[ToolDef] = [
    {
        "name": "read_file",
        "description": "Read the contents of a file. Returns the file content with line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "The path to the file to read"},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Creates the file if it doesn't exist, overwrites if it does.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "The path to the file to write"},
                "content": {"type": "string", "description": "The content to write to the file"},
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Edit a file by replacing an exact string match with new content. The old_string must match exactly (including whitespace and indentation).",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "The path to the file to edit"},
                "old_string": {"type": "string", "description": "The exact string to find and replace"},
                "new_string": {"type": "string", "description": "The string to replace it with"},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    {
        "name": "list_files",
        "description": "List files matching a glob pattern. Returns matching file paths.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern to match files (e.g., \"**/*.py\", \"nanocode/**/*\")"},
                "path": {"type": "string", "description": "Base directory to search from. Defaults to current directory."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "grep_search",
        "description": "Search for a pattern in files. Returns matching lines with file paths and line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "The regex pattern to search for"},
                "path": {"type": "string", "description": "Directory or file to search in. Defaults to current directory."},
                "include": {"type": "string", "description": "File glob pattern to include (e.g., \"*.py\", \"*.md\")"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "run_shell",
        "description": "Execute a shell command and return its output. Use this for running tests, installing packages, git operations, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "timeout": {"type": "number", "description": "Timeout in milliseconds (default: 30000)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "skill",
        "description": "Invoke a registered skill by name. Skills are prompt templates loaded from .claude/skills/. Returns the skill's resolved prompt to follow.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "The name of the skill to invoke"},
                "args": {"type": "string", "description": "Optional arguments to pass to the skill"},
            },
            "required": ["skill_name"],
        },
    },
    {
        "name": "web_fetch",
        "description": "Fetch a URL and return its content as text. For HTML pages, tags are stripped to return readable text. For JSON/text responses, content is returned directly.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"},
                "max_length": {"type": "number", "description": "Maximum content length in characters (default 50000)"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "agent",
        "description": "Launch one or more sub-agents to handle tasks autonomously. Sub-agents have isolated context and return their result. Types: 'explore' (read-only search), 'plan' (read-only planning), 'general' (full tools except agent). Pass 'tasks' for parallel execution of multiple sub-agents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Short (3-5 word) description of the task"},
                "prompt": {"type": "string", "description": "Detailed task instructions for the sub-agent. Ignored when tasks list is provided."},
                "type": {"type": "string", "enum": ["explore", "plan", "general"], "description": "Agent type. Default: general. Ignored when tasks list is provided."},
                "tasks": {
                    "type": "array",
                    "description": "Optional list of tasks for parallel execution. When provided, each task runs as an independent sub-agent concurrently.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["explore", "plan", "general"], "description": "Agent type for this task"},
                            "prompt": {"type": "string", "description": "Task instructions"},
                        },
                        "required": ["type", "prompt"],
                    },
                },
            },
            "required": ["description", "prompt"],
        },
    },
    {
        "name": "tool_search",
        "description": "Search for available tools by name or keyword. Returns full schema definitions for matching deferred tools so you can use them.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Tool name or search keywords"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_mcp_resources",
        "description": "List MCP resources exposed by connected MCP servers. Optionally filter by server name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "Optional MCP server name to filter resources by"},
            },
        },
    },
    {
        "name": "read_mcp_resource",
        "description": "Read a resource from a connected MCP server by server name and resource URI.",
        "input_schema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "MCP server name"},
                "uri": {"type": "string", "description": "Resource URI returned by list_mcp_resources"},
            },
            "required": ["server", "uri"],
        },
    },
]


def builtin_tool_definitions() -> list[ToolDef]:
    return copy.deepcopy(BUILTIN_TOOL_DEFINITIONS)


# ─── 内置工具实现 ─────────────────────────────────

logger = get_logger("tools.builtin")

IS_WIN = sys.platform == "win32"


def read_file(inp: dict) -> str:
    try:
        content = Path(inp["file_path"]).read_text()
        lines = content.split("\n")
        numbered = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))
        return numbered
    except Exception as e:
        return f"Error reading file: {e}"


def write_file(inp: dict) -> str:
    try:
        path = Path(inp["file_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(inp["content"])
        _auto_update_memory_index(str(path))
        lines = inp["content"].split("\n")
        line_count = len(lines)
        preview = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines[:30]))
        trunc = f"\n  ... ({line_count} lines total)" if line_count > 30 else ""
        return f"Successfully wrote to {inp['file_path']} ({line_count} lines)\n\n{preview}{trunc}"
    except Exception as e:
        return f"Error writing file: {e}"


def _auto_update_memory_index(file_path: str) -> None:
    try:
        sync_memory_file(Path(file_path))
    except Exception:
        logger.debug("Failed to sync memory index for %s", file_path, exc_info=True)


def _normalize_quotes(s: str) -> str:
    s = re.sub("[\\u2018\\u2019\\u2032]", "'", s)
    s = re.sub("[\\u201c\\u201d\\u2033]", '"', s)
    return s


def _find_actual_string(file_content: str, search_string: str) -> str | None:
    if search_string in file_content:
        return search_string
    norm_search = _normalize_quotes(search_string)
    norm_file = _normalize_quotes(file_content)
    idx = norm_file.find(norm_search)
    if idx != -1:
        return file_content[idx:idx + len(search_string)]
    return None


def _generate_diff(old_content: str, old_string: str, new_string: str) -> str:
    before_change = old_content.split(old_string)[0]
    line_num = before_change.count("\n") + 1
    old_lines = old_string.split("\n")
    new_lines = new_string.split("\n")

    parts = [f"@@ -{line_num},{len(old_lines)} +{line_num},{len(new_lines)} @@"]
    for line in old_lines:
        parts.append(f"- {line}")
    for line in new_lines:
        parts.append(f"+ {line}")
    return "\n".join(parts)


def edit_file(inp: dict) -> str:
    try:
        path = Path(inp["file_path"])
        content = path.read_text()

        actual = _find_actual_string(content, inp["old_string"])
        if not actual:
            return f"Error: old_string not found in {inp['file_path']}"

        count = content.count(actual)
        if count > 1:
            return f"Error: old_string found {count} times in {inp['file_path']}. Must be unique."

        new_content = content.replace(actual, inp["new_string"], 1)
        path.write_text(new_content)

        diff = _generate_diff(content, actual, inp["new_string"])
        quote_note = " (matched via quote normalization)" if actual != inp["old_string"] else ""
        return f"Successfully edited {inp['file_path']}{quote_note}\n\n{diff}"
    except Exception as e:
        return f"Error editing file: {e}"


def list_files(inp: dict) -> str:
    try:
        base = Path(inp.get("path") or ".")
        pattern = inp["pattern"]
        files = []
        for p in base.glob(pattern):
            if p.is_file():
                rel = str(p.relative_to(base) if base != Path(".") else p)
                if any(part in {".git", ".venv", "venv", "__pycache__"} for part in rel.split(os.sep)):
                    continue
                files.append(rel)
                if len(files) >= MAX_LIST_FILES_RESULTS:
                    break
        if not files:
            return "No files found matching the pattern."
        result = "\n".join(files[:MAX_LIST_FILES_RESULTS])
        if len(files) > MAX_LIST_FILES_RESULTS:
            result += f"\n... and {len(files) - 200} more"
        return result
    except Exception as e:
        return f"Error listing files: {e}"


def grep_search(inp: dict) -> str:
    pattern = inp["pattern"]
    path = inp.get("path") or "."
    include = inp.get("include")

    if not IS_WIN:
        try:
            args = ["grep", "--line-number", "--color=never", "-r", "-E"]
            if include:
                args.append(f"--include={include}")
            args.extend(["--", pattern, path])
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                lines = [line for line in result.stdout.split("\n") if line]
                output = "\n".join(lines[:MAX_GREP_RESULTS])
                if len(lines) > MAX_GREP_RESULTS:
                    output += f"\n... and {len(lines) - 100} more matches"
                return output
        except Exception:
            logger.debug("System grep failed; falling back to Python grep", exc_info=True)

    return _grep_python(pattern, path, include)


def _grep_python(pattern: str, directory: str, include: str | None) -> str:
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"Error: invalid regex: {exc}"
    include_pattern = include
    matches: list[str] = []

    def search_file(file_path: str) -> None:
        if include_pattern and not fnmatch.fnmatch(os.path.basename(file_path), include_pattern):
            return
        try:
            text = Path(file_path).read_text(errors="replace")
            for i, line in enumerate(text.split("\n")):
                if regex.search(line):
                    matches.append(f"{file_path}:{i+1}:{line}")
                    if len(matches) >= MAX_GREP_MATCHES:
                        return
        except (OSError, UnicodeDecodeError):
            pass

    def walk(d: str) -> None:
        if len(matches) >= MAX_GREP_MATCHES:
            return
        try:
            entries = os.listdir(d)
        except OSError:
            return
        for name in entries:
            if name.startswith(".") or name in {"venv", "__pycache__"}:
                continue
            full = os.path.join(d, name)
            if os.path.isdir(full):
                walk(full)
                continue
            search_file(full)

    if os.path.isfile(directory):
        search_file(directory)
    else:
        walk(directory)
    if not matches:
        return "No matches found."
    output = "\n".join(matches[:MAX_GREP_RESULTS])
    if len(matches) > MAX_GREP_RESULTS:
        output += f"\n... and {len(matches) - 100} more matches"
    return output


def run_shell(inp: dict) -> str:
    """（内部实现参考，不再被 BUILTIN_HANDLERS 引用）

    所有 run_shell 执行路径都已要求显式传入 sandbox/backend。
    直接调用此函数会在宿主机上以 shell=True 裸执行命令，禁止使用。
    """
    try:
        timeout_ms = inp.get("timeout", DEFAULT_SHELL_TIMEOUT_MS)
        timeout_s = timeout_ms / 1000
        result = subprocess.run(
            inp["command"],
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        output = result.stdout or ""
        if result.returncode != 0:
            stderr = f"\nStderr: {result.stderr}" if result.stderr else ""
            stdout = f"\nStdout: {result.stdout}" if result.stdout else ""
            return f"Command failed (exit code {result.returncode}){stdout}{stderr}"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Command timed out after {inp.get('timeout', 30000)}ms"
    except Exception as e:
        return f"Error: {e}"


def web_fetch(inp: dict) -> str:
    import urllib.error
    import urllib.request

    url = inp.get("url", "")
    max_length = inp.get("max_length", DEFAULT_FETCH_MAX_LENGTH)
    req = urllib.request.Request(url, headers={"User-Agent": "nanocode/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "")
            text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return f"HTTP error: {e.code} {e.reason}"
    except urllib.error.URLError as e:
        return f"Error fetching {url}: {e.reason}"
    except Exception as e:
        return f"Error fetching {url}: {e}"

    if "html" in content_type:
        text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]*>", " ", text)
        text = text.replace("&nbsp;", " ").replace("&amp;", "&")
        text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
        text = re.sub(r"\s{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()

    if len(text) > max_length:
        text = text[:max_length] + f"\n\n[... truncated at {max_length} characters]"

    return text or "(empty response)"
