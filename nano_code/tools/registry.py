"""Agent-level tool registry."""

from __future__ import annotations

import copy

from .definitions import (
    CONCURRENCY_SAFE_BUILTIN_TOOLS,
    EDIT_TOOL_NAMES,
    READ_TOOL_NAMES,
    builtin_tool_definitions,
)
from .types import ToolDef, ToolMetadata, ToolOrigin

INTERNAL_SCHEMA_KEYS = {"deferred", "origin", "concurrency_safe", "read_only", "edit_tool"}


def sanitize_tool_definition(tool: ToolDef) -> ToolDef:
    return {k: copy.deepcopy(v) for k, v in tool.items() if k not in INTERNAL_SCHEMA_KEYS}


class ToolRegistry:
    def __init__(self, tools: list[ToolDef] | None = None):
        self._tools: dict[str, ToolDef] = {}
        self._metadata: dict[str, ToolMetadata] = {}
        self._activated_deferred: set[str] = set()
        if tools:
            self.add_many(tools, origin="builtin")

    @classmethod
    def with_builtin_tools(cls) -> "ToolRegistry":
        registry = cls()
        registry.add_many(builtin_tool_definitions(), origin="builtin")
        return registry

    def add_many(
        self,
        tools: list[ToolDef],
        *,
        origin: ToolOrigin = "custom",
        default_concurrency_safe: bool = False,
    ) -> None:
        for tool in tools:
            name = tool.get("name")
            if not name or name in self._tools:
                continue

            stored = copy.deepcopy(tool)
            deferred = bool(stored.get("deferred"))
            read_only = bool(stored.get("read_only", name in READ_TOOL_NAMES if origin == "builtin" else False))
            edit_tool = bool(stored.get("edit_tool", name in EDIT_TOOL_NAMES if origin == "builtin" else False))
            builtin_safe = origin == "builtin" and name in CONCURRENCY_SAFE_BUILTIN_TOOLS
            concurrency_safe = bool(stored.get("concurrency_safe", default_concurrency_safe or builtin_safe))

            self._tools[name] = stored
            self._metadata[name] = ToolMetadata(
                name=name,
                origin=origin,
                deferred=deferred,
                concurrency_safe=concurrency_safe,
                read_only=read_only,
                edit_tool=edit_tool,
                raw={
                    k: copy.deepcopy(stored[k])
                    for k in INTERNAL_SCHEMA_KEYS
                    if k in stored
                },
            )

    def active_definitions(self, denied: set[str] | None = None) -> list[ToolDef]:
        denied = denied or set()
        result: list[ToolDef] = []
        for name, tool in self._tools.items():
            metadata = self._metadata[name]
            if name in denied:
                continue
            if metadata.deferred and name not in self._activated_deferred:
                continue
            result.append(sanitize_tool_definition(tool))
        return result

    def deferred_names(self, denied: set[str] | None = None) -> list[str]:
        denied = denied or set()
        return [
            name
            for name, metadata in self._metadata.items()
            if metadata.deferred and name not in self._activated_deferred and name not in denied
        ]

    def search_deferred(self, query: str) -> list[ToolDef]:
        q = (query or "").lower()
        matches: list[ToolDef] = []
        for name, tool in self._tools.items():
            metadata = self._metadata[name]
            if not metadata.deferred or name in self._activated_deferred:
                continue
            description = tool.get("description") or ""
            if q in name.lower() or q in description.lower():
                self._activated_deferred.add(name)
                matches.append(sanitize_tool_definition(tool))
        return matches

    def metadata_for(self, name: str) -> ToolMetadata | None:
        return self._metadata.get(name)

    def is_concurrency_safe(self, name: str, inp: dict | None = None) -> bool:
        _ = inp
        metadata = self._metadata.get(name)
        return bool(metadata and metadata.concurrency_safe)

    def names(self) -> set[str]:
        return set(self._tools)
