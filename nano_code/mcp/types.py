"""Lightweight MCP dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class McpDiagnostic:
    level: Literal["info", "warning", "error"]
    source: str
    message: str


@dataclass
class McpServerConfig:
    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    transport: Literal["stdio", "http", "sse", "ws"] = "stdio"
    timeout: float = 15.0
    call_timeout: float = 60.0
    always_load: bool = False
    source: str = ""


@dataclass
class McpToolDef:
    server_name: str
    tool_name: str
    prefixed_name: str
    description: str
    input_schema: dict[str, Any]
    deferred: bool = True
    always_load: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class McpCallResult:
    text: str
    is_error: bool = False
    saved_files: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class McpResource:
    server_name: str
    uri: str
    name: str = ""
    description: str = ""
    mime_type: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class McpToolDelta:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)
