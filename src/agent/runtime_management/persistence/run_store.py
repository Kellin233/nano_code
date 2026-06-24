"""Run artifact persistence for trace and report."""

from __future__ import annotations

import json
from pathlib import Path

from .atomic import append_jsonl, write_json_atomic
from .report import now_iso
from .task_state import TaskState


class RunStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id: str | TaskState) -> Path:
        return self.root / self._run_id(run_id)

    def trace_path(self, run_id: str | TaskState) -> Path:
        return self.run_dir(run_id) / "trace.jsonl"

    def report_path(self, run_id: str | TaskState) -> Path:
        return self.run_dir(run_id) / "report.json"

    def start_run(self, task_state: TaskState) -> Path:
        run_dir = self.run_dir(task_state)
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def append_trace(self, task_state: TaskState, event: dict) -> Path:
        path = self.trace_path(task_state)
        path.parent.mkdir(parents=True, exist_ok=True)
        append_jsonl(path, event)
        return path

    def write_report(self, task_state: TaskState, report: dict) -> Path:
        path = self.report_path(task_state)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(path, report)
        return path

    def load_report(self, run_id: str | TaskState) -> dict:
        return json.loads(self.report_path(run_id).read_text(encoding="utf-8"))

    def mark_unfinished_interrupted(self, *, session_id: str = "") -> int:
        count = 0
        for trace_path in self.root.glob("*/trace.jsonl"):
            if self.report_path(trace_path.parent.name).exists():
                continue
            events = self._load_trace(trace_path)
            if not events or any(str(event.get("event")) in {"run_finished", "run_interrupted"} for event in events):
                continue
            first = events[0]
            if session_id and str(first.get("session_id") or "") != session_id:
                continue
            task_state = TaskState(
                run_id=str(first.get("run_id") or trace_path.parent.name),
                task_id=str(first.get("task_id") or ""),
                user_request=str(first.get("user_request") or ""),
            )
            self.append_trace(task_state, {
                "event": "run_interrupted",
                "created_at": now_iso(),
                "run_id": task_state.run_id,
                "task_id": task_state.task_id,
                "status": "stopped",
                "stop_reason": "interrupted",
            })
            count += 1
        return count

    @staticmethod
    def _load_trace(path: Path) -> list[dict]:
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except Exception:
                break
            if isinstance(event, dict):
                events.append(event)
        return events

    @staticmethod
    def _run_id(value: str | TaskState) -> str:
        if hasattr(value, "run_id"):
            return str(value.run_id)
        return str(value)
