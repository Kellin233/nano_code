"""Session and per-run artifact persistence."""

from pathlib import Path
from typing import Any

from . import session_store as _session_store
from .artifacts import ArtifactRef, ArtifactStore
from .atomic import (
    append_jsonl,
    append_line,
    write_bytes_atomic,
    write_json_atomic,
    write_text_atomic,
)
from .report import RunMetrics, build_report, now_iso, runtime_event_to_trace, trace_event
from .run_store import RunStore
from .session_log import INTERRUPTED_TOOL_RESULT, SessionLog, repair_orphaned_tool_calls
from .task_state import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_STOPPED,
    STOP_REASON_ABORTED,
    STOP_REASON_BUDGET_EXCEEDED,
    STOP_REASON_ERROR,
    STOP_REASON_STOP,
    TaskState,
)

SESSION_DIR = _session_store.SESSION_DIR


def _sync_session_dir() -> None:
    _session_store.SESSION_DIR = Path(SESSION_DIR)


def load_session(session_id: str) -> dict[str, Any] | None:
    _sync_session_dir()
    return _session_store.load_session(session_id)


def list_sessions() -> list[dict[str, Any]]:
    _sync_session_dir()
    return _session_store.list_sessions()


def get_latest_session_id() -> str | None:
    _sync_session_dir()
    return _session_store.get_latest_session_id()


__all__ = [
    "ArtifactRef",
    "ArtifactStore",
    "RunMetrics",
    "RunStore",
    "SESSION_DIR",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_RUNNING",
    "STATUS_STOPPED",
    "STOP_REASON_ABORTED",
    "STOP_REASON_BUDGET_EXCEEDED",
    "STOP_REASON_ERROR",
    "STOP_REASON_STOP",
    "INTERRUPTED_TOOL_RESULT",
    "SessionLog",
    "TaskState",
    "append_jsonl",
    "append_line",
    "build_report",
    "get_latest_session_id",
    "list_sessions",
    "load_session",
    "now_iso",
    "repair_orphaned_tool_calls",
    "runtime_event_to_trace",
    "trace_event",
    "write_bytes_atomic",
    "write_json_atomic",
    "write_text_atomic",
]
