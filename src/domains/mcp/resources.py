"""MCP resource result helpers."""

from __future__ import annotations

import json

from .types import McpResource


def render_resource_list(resources: list[McpResource]) -> str:
    return json.dumps(
        [
            {
                "server": resource.server_name,
                "uri": resource.uri,
                "name": resource.name,
                "description": resource.description,
                "mimeType": resource.mime_type,
            }
            for resource in resources
        ],
        ensure_ascii=False,
        indent=2,
    )
