"""Atomic file writes and safe JSONL appends for internal persistence."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any


def write_bytes_atomic(path: Path | str, data: bytes, *, durable: bool = True) -> None:
    """Replace a file atomically with complete bytes."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None

    try:
        fd, temp_name = tempfile.mkstemp(
            dir=str(target.parent),
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temp_path = Path(temp_name)
        try:
            _write_all(fd, data)
            if durable:
                os.fsync(fd)
        finally:
            os.close(fd)

        _preserve_existing_mode(temp_path, target)
        os.replace(temp_path, target)
        temp_path = None
        if durable:
            _fsync_directory(target.parent)
    finally:
        if temp_path is not None:
            with suppress(FileNotFoundError):
                temp_path.unlink()


def write_text_atomic(
    path: Path | str,
    content: str,
    *,
    encoding: str = "utf-8",
    durable: bool = True,
) -> None:
    """Replace a text file atomically."""
    write_bytes_atomic(path, content.encode(encoding), durable=durable)


def write_json_atomic(
    path: Path | str,
    payload: Any,
    *,
    durable: bool = True,
    indent: int = 2,
    sort_keys: bool = True,
    ensure_ascii: bool = False,
    default: Callable[[Any], Any] | None = str,
) -> None:
    """Replace a JSON file atomically using the project's stable formatting."""
    content = json.dumps(
        payload,
        indent=indent,
        sort_keys=sort_keys,
        ensure_ascii=ensure_ascii,
        default=default,
    )
    write_text_atomic(path, content + "\n", durable=durable)


def append_line(
    path: Path | str,
    line: str,
    *,
    encoding: str = "utf-8",
    durable: bool = False,
) -> None:
    """Append exactly one text line using O_APPEND."""
    text = line[:-1] if line.endswith("\n") else line
    if "\n" in text or "\r" in text:
        raise ValueError("append_line requires a single line")

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = (text + "\n").encode(encoding)
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    fd = os.open(target, flags, 0o600)
    try:
        written = os.write(fd, data)
        if written != len(data):
            raise OSError(f"short append to {target}: wrote {written} of {len(data)} bytes")
        if durable:
            os.fsync(fd)
    finally:
        os.close(fd)


def append_jsonl(
    path: Path | str,
    payload: Any,
    *,
    durable: bool = False,
    sort_keys: bool = True,
    ensure_ascii: bool = False,
    default: Callable[[Any], Any] | None = str,
) -> None:
    """Append one JSON object as a single JSONL record."""
    line = json.dumps(
        payload,
        sort_keys=sort_keys,
        ensure_ascii=ensure_ascii,
        default=default,
    )
    append_line(path, line, durable=durable)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written == 0:
            raise OSError("short write")
        view = view[written:]


def _preserve_existing_mode(temp_path: Path, target: Path) -> None:
    try:
        mode = target.stat().st_mode & 0o777
    except FileNotFoundError:
        return
    os.chmod(temp_path, mode)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
