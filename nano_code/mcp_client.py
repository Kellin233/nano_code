"""Compatibility exports for the MCP subsystem."""

from __future__ import annotations

from .mcp import (
    McpCallResult,
    McpDiagnostic,
    McpManager,
    McpResource,
    McpServerConfig,
    McpToolDef,
    McpToolDelta,
)
from .mcp.connection import McpConnection

__all__ = [
    "McpCallResult",
    "McpConnection",
    "McpDiagnostic",
    "McpManager",
    "McpResource",
    "McpServerConfig",
    "McpToolDef",
    "McpToolDelta",
]
