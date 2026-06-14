"""MCP structured output rendering."""

from __future__ import annotations

import base64
import json
import mimetypes
import re
import time
from pathlib import Path
from typing import Any

from ....agent.harness.persistence.atomic import write_bytes_atomic, write_text_atomic
from .types import McpCallResult

TEXT_BLOCK_LIMIT = 50 * 1024
BLOB_INLINE_LIMIT = 25 * 1024
FINAL_TEXT_LIMIT = 100 * 1024


def format_call_result(result: Any, server: str, tool: str) -> McpCallResult:
    raw = result if isinstance(result, dict) else {"value": result}
    if not isinstance(result, dict):
        return McpCallResult(text=f"[MCP result: {server}/{tool}]\n\n{_safe_json(result)}", raw=raw)

    saved_files: list[str] = []
    parts: list[str] = [f"[MCP result: {server}/{tool}]"]
    is_error = bool(result.get("isError"))
    if is_error:
        parts.append("[MCP tool error]")

    content = result.get("content")
    if isinstance(content, list):
        for index, block in enumerate(content):
            parts.append(_format_content_block(block, server, tool, index, saved_files))
    elif "contents" in result and isinstance(result["contents"], list):
        for index, block in enumerate(result["contents"]):
            parts.append(_format_resource_content(block, server, tool, index, saved_files))
    else:
        parts.append(_safe_json(result))

    text = "\n\n".join(part for part in parts if part)
    if len(text.encode("utf-8")) > FINAL_TEXT_LIMIT:
        path = _save_text(server, tool, "final", text)
        saved_files.append(path)
        preview = text[:FINAL_TEXT_LIMIT]
        text = (
            f"[MCP result too large. Full output saved to {path}. "
            "Use read_file to inspect it.]\n\n"
            f"{preview}"
        )
    return McpCallResult(text=text, is_error=is_error, saved_files=saved_files, raw=raw)


def _format_content_block(
    block: Any,
    server: str,
    tool: str,
    index: int,
    saved_files: list[str],
) -> str:
    if not isinstance(block, dict):
        return _safe_json(block)
    block_type = block.get("type")
    if block_type == "text":
        text = str(block.get("text", ""))
        if len(text.encode("utf-8")) > TEXT_BLOCK_LIMIT:
            path = _save_text(server, tool, str(index), text)
            saved_files.append(path)
            return f"[Large text block saved to {path}.]\n{text[:TEXT_BLOCK_LIMIT]}"
        return text
    if block_type in {"image", "blob"}:
        mime_type = str(block.get("mimeType") or block.get("mime_type") or "application/octet-stream")
        data = str(block.get("data") or block.get("blob") or "")
        size = len(data.encode("utf-8"))
        if size > BLOB_INLINE_LIMIT:
            path = _save_blob(server, tool, index, mime_type, data)
            saved_files.append(path)
            return f"[{block_type} saved to {path}; mime={mime_type}; encoded_size={size} bytes]"
        return f"[{block_type}; mime={mime_type}; encoded_size={size} bytes]\n{data}"
    if block_type == "resource":
        resource = block.get("resource", {})
        return _format_resource_content(resource, server, tool, index, saved_files)
    return _safe_json(block)


def _format_resource_content(
    resource: Any,
    server: str,
    tool: str,
    index: int,
    saved_files: list[str],
) -> str:
    if not isinstance(resource, dict):
        return _safe_json(resource)
    uri = resource.get("uri", "")
    mime_type = resource.get("mimeType") or resource.get("mime_type") or ""
    header = f"[MCP resource: {uri}; mime={mime_type}]"
    if "text" in resource:
        text = str(resource.get("text") or "")
        if len(text.encode("utf-8")) > TEXT_BLOCK_LIMIT:
            path = _save_text(server, tool, f"resource-{index}", text)
            saved_files.append(path)
            return f"{header}\n[Large resource text saved to {path}.]\n{text[:TEXT_BLOCK_LIMIT]}"
        return f"{header}\n{text}"
    if "blob" in resource:
        data = str(resource.get("blob") or "")
        path = _save_blob(server, tool, index, str(mime_type or "application/octet-stream"), data)
        saved_files.append(path)
        return f"{header}\n[Blob saved to {path}; encoded_size={len(data.encode('utf-8'))} bytes]"
    return f"{header}\n{_safe_json(resource)}"


def _save_text(server: str, tool: str, index: str, text: str) -> str:
    output_dir = _output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{int(time.time() * 1000)}-{_safe_name(server)}-{_safe_name(tool)}-{_safe_name(index)}.txt"
    write_text_atomic(path, text)
    return str(path.resolve())


def _save_blob(server: str, tool: str, index: int, mime_type: str, data: str) -> str:
    output_dir = _output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    ext = mimetypes.guess_extension(mime_type) or ".bin"
    path = output_dir / f"{int(time.time() * 1000)}-{_safe_name(server)}-{_safe_name(tool)}-{index}{ext}"
    try:
        payload = base64.b64decode(data, validate=False)
        write_bytes_atomic(path, payload)
    except Exception:
        write_text_atomic(path, data)
    return str(path.resolve())


def _safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return value[:80] or "mcp"


def _output_dir() -> Path:
    return Path.home() / ".nanocode" / "mcp-outputs"


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:
        return str(value)
