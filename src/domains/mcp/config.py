"""MCP configuration loading and environment expansion."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .types import McpDiagnostic, McpServerConfig

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


@dataclass
class McpConfigResult:
    configs: dict[str, McpServerConfig] = field(default_factory=dict)
    diagnostics: list[McpDiagnostic] = field(default_factory=list)


def load_mcp_configs(cwd: Path | None = None, *, home: Path | None = None) -> McpConfigResult:
    cwd = (cwd or Path.cwd()).resolve()
    home = home or Path.home()
    diagnostics: list[McpDiagnostic] = []
    merged: dict[str, McpServerConfig] = {}
    sources = [
        home / ".claude.json",
        home / ".claude" / "settings.json",
        cwd / ".claude" / "settings.json",
        cwd / ".mcp.json",
    ]

    for path in sources:
        _merge_config_file(path, merged, diagnostics)
    return McpConfigResult(configs=merged, diagnostics=diagnostics)


def build_server_env(config: McpServerConfig, project_root: Path | None = None) -> dict[str, str]:
    project_root = (project_root or Path.cwd()).resolve()
    return {
        **os.environ,
        **config.env,
        "CLAUDE_PROJECT_DIR": str(project_root),
    }


def _merge_config_file(
    path: Path,
    target: dict[str, McpServerConfig],
    diagnostics: list[McpDiagnostic],
) -> None:
    if not path.exists():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        diagnostics.append(McpDiagnostic("error", str(path), f"failed to parse config: {exc}"))
        return

    if not isinstance(raw, dict):
        diagnostics.append(McpDiagnostic("warning", str(path), "config root is not an object"))
        return

    if "projects" in raw:
        diagnostics.append(McpDiagnostic("info", str(path), "projects mapping is not expanded in this MCP loader"))

    servers = raw.get("mcpServers")
    if servers is None:
        servers = raw
    if not isinstance(servers, dict):
        diagnostics.append(McpDiagnostic("warning", str(path), "mcpServers is not an object"))
        return

    for name, value in servers.items():
        if not isinstance(value, dict):
            continue
        cfg = _parse_server_config(str(name), value, path, diagnostics)
        if cfg is not None:
            target[cfg.name] = cfg


def _parse_server_config(
    name: str,
    value: dict[str, Any],
    source: Path,
    diagnostics: list[McpDiagnostic],
) -> McpServerConfig | None:
    if "command" not in value and "url" not in value:
        return None

    transport = str(value.get("transport") or ("stdio" if value.get("command") else "http"))
    if transport not in {"stdio", "http", "sse", "ws"}:
        diagnostics.append(McpDiagnostic("warning", str(source), f"{name}: unsupported transport value {transport!r}"))
        transport = "stdio"

    timeout = _float_value(value.get("timeout"), 15.0)
    call_timeout = _float_value(value.get("callTimeout") or value.get("call_timeout"), 60.0)

    command = _expand_env(value.get("command"), source, diagnostics) if value.get("command") is not None else None
    args = [
        _expand_env(arg, source, diagnostics)
        for arg in value.get("args", [])
        if isinstance(arg, str)
    ]
    env = {
        str(k): _expand_env(v, source, diagnostics)
        for k, v in (value.get("env") or {}).items()
        if isinstance(v, str)
    }
    url = _expand_env(value.get("url"), source, diagnostics) if value.get("url") is not None else None
    always_load = bool(value.get("alwaysLoad", value.get("always_load", False)))

    return McpServerConfig(
        name=name,
        command=command,
        args=args,
        env=env,
        url=url,
        transport=transport,  # type: ignore[arg-type]
        timeout=timeout,
        call_timeout=call_timeout,
        always_load=always_load,
        source=str(source),
    )


def _expand_env(value: Any, source: Path, diagnostics: list[McpDiagnostic]) -> str:
    text = str(value)

    def replace(match: re.Match) -> str:
        key = match.group(1)
        default = match.group(2)
        if key in os.environ:
            return os.environ[key]
        if default is not None:
            return default
        diagnostics.append(McpDiagnostic("warning", str(source), f"environment variable {key} is not set"))
        return ""

    return _ENV_RE.sub(replace, text)


def _float_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
