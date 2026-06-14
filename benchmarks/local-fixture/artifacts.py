"""Run artifact and trace helpers for the local fixture benchmark."""

from __future__ import annotations

import difflib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunArtifacts:
    run_dir: Path | None
    report: dict[str, Any]
    report_path: Path | None
    report_exists: bool
    report_parse_valid: bool
    report_parse_error: str
    trace_path: Path | None
    trace_exists: bool
    trace_parse_valid: bool
    trace_parse_error: str
    trace_event_count: int


@dataclass(frozen=True)
class TraceRead:
    exists: bool
    parse_valid: bool
    events: list[dict[str, Any]]
    error: str = ""


@dataclass(frozen=True)
class ReportRead:
    exists: bool
    parse_valid: bool
    data: dict[str, Any]
    error: str = ""


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_json_optional(path: Path) -> dict[str, Any]:
    try:
        if path.exists():
            data = read_json(path)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    return {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def read_report(path: Path | None) -> ReportRead:
    if path is None or not path.exists():
        return ReportRead(exists=False, parse_valid=False, data={}, error="missing_report")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ReportRead(
            exists=True,
            parse_valid=False,
            data={},
            error=f"invalid_json: {exc.msg}",
        )
    except OSError as exc:
        return ReportRead(exists=path.exists(), parse_valid=False, data={}, error=str(exc))
    if not isinstance(data, dict):
        return ReportRead(exists=True, parse_valid=False, data={}, error="non_object_report")
    return ReportRead(exists=True, parse_valid=True, data=data)


def read_trace(path: Path | None) -> TraceRead:
    if path is None or not path.exists():
        return TraceRead(exists=False, parse_valid=False, events=[], error="missing_trace")
    try:
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                return TraceRead(
                    exists=True,
                    parse_valid=False,
                    events=[],
                    error=f"invalid_json_line_{line_number}: {exc.msg}",
                )
            if not isinstance(event, dict):
                return TraceRead(
                    exists=True,
                    parse_valid=False,
                    events=[],
                    error=f"non_object_line_{line_number}",
                )
            rows.append(event)
        return TraceRead(exists=True, parse_valid=True, events=rows)
    except OSError as exc:
        return TraceRead(exists=path.exists(), parse_valid=False, events=[], error=str(exc))


def read_jsonl_optional(path: Path) -> list[dict[str, Any]]:
    result = read_trace(path)
    return result.events if result.parse_valid else []


def collect_run_artifacts(workspace: Path, task_artifact_dir: Path, prompt: str) -> RunArtifacts:
    run_dir = select_run_dir(workspace, prompt)
    report: dict[str, Any] = {}
    report_path = None
    report_exists = False
    report_parse_valid = False
    report_parse_error = ""
    trace_path = None
    trace_exists = False
    trace_parse_valid = False
    trace_parse_error = ""
    trace_event_count = 0

    if run_dir is not None:
        report_path = run_dir / "report.json"
        trace_path = run_dir / "trace.jsonl"
        if report_path.exists():
            shutil.copy2(report_path, task_artifact_dir / "report.json")
            report_read = read_report(report_path)
            report_exists = report_read.exists
            report_parse_valid = report_read.parse_valid
            report_parse_error = report_read.error
            report = report_read.data
        if trace_path.exists():
            trace_exists = True
            shutil.copy2(trace_path, task_artifact_dir / "trace.jsonl")
            trace_read = read_trace(trace_path)
            trace_parse_valid = trace_read.parse_valid
            trace_parse_error = trace_read.error
            trace_event_count = len(trace_read.events)

    return RunArtifacts(
        run_dir=run_dir,
        report=report,
        report_path=report_path,
        report_exists=report_exists,
        report_parse_valid=report_parse_valid,
        report_parse_error=report_parse_error,
        trace_path=trace_path,
        trace_exists=trace_exists,
        trace_parse_valid=trace_parse_valid,
        trace_parse_error=trace_parse_error,
        trace_event_count=trace_event_count,
    )


def verifier_env(run_artifacts: RunArtifacts) -> dict[str, str]:
    env: dict[str, str] = {}
    if run_artifacts.run_dir is not None:
        env["NANOCODE_BENCH_RUN_DIR"] = str(run_artifacts.run_dir.resolve())
    if run_artifacts.trace_path is not None and run_artifacts.trace_path.exists():
        env["NANOCODE_BENCH_TRACE"] = str(run_artifacts.trace_path.resolve())
    if run_artifacts.report_path is not None and run_artifacts.report_path.exists():
        env["NANOCODE_BENCH_REPORT"] = str(run_artifacts.report_path.resolve())
    return env


def directory_diff(original: Path, modified: Path) -> str:
    relpaths = sorted({
        *(path.relative_to(original) for path in _iter_files(original)),
        *(path.relative_to(modified) for path in _iter_files(modified)),
    }, key=str)
    chunks: list[str] = []
    for relpath in relpaths:
        before_path = original / relpath
        after_path = modified / relpath
        before = before_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if before_path.exists() else []
        after = after_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if after_path.exists() else []
        if before == after:
            continue
        chunks.extend(
            difflib.unified_diff(
                before,
                after,
                fromfile=f"a/{relpath}",
                tofile=f"b/{relpath}",
            )
        )
    return "".join(chunks)


def latest_run_dir(workspace: Path) -> Path | None:
    runs_root = workspace / ".nanocode" / "runs"
    if not runs_root.exists():
        return None
    candidates = [path for path in runs_root.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def select_run_dir(workspace: Path, prompt: str) -> Path | None:
    """Pick the main NanoCode run for this task, not a nested/sub-agent run."""
    runs_root = workspace / ".nanocode" / "runs"
    if not runs_root.exists():
        return None
    candidates = [path for path in runs_root.iterdir() if path.is_dir()]
    if not candidates:
        return None

    scored = [(run_dir_score(path, prompt), path) for path in candidates]
    scored.sort(key=lambda item: (item[0], item[1].stat().st_mtime), reverse=True)
    best_score, best_path = scored[0]
    if best_score <= 0:
        return latest_run_dir(workspace)
    return best_path


def run_dir_score(run_dir: Path, prompt: str) -> int:
    report = read_json_optional(run_dir / "report.json")
    trace_request = trace_user_request(run_dir / "trace.jsonl")
    score = 0

    runtime = report.get("runtime") if isinstance(report.get("runtime"), dict) else {}
    if runtime.get("is_sub_agent") is True:
        score -= 100
    else:
        score += 2

    if report:
        score += 1
    if (run_dir / "trace.jsonl").exists():
        score += 1

    if trace_request == prompt:
        score += 10
    return score


def trace_user_request(trace_path: Path) -> str:
    try:
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if isinstance(event, dict) and event.get("event") == "run_started":
                return str(event.get("user_request") or "")
    except Exception:
        return ""
    return ""


def report_summary(report: dict[str, Any]) -> dict[str, Any]:
    if not report:
        return {}
    return {
        "schema_version": report.get("schema_version"),
        "run_id": report.get("run_id"),
        "task_id": report.get("task_id"),
        "status": report.get("status"),
        "stop_reason": report.get("stop_reason"),
        "tool_steps": report.get("tool_steps", 0),
        "attempts": report.get("attempts", 0),
        "duration_ms": report.get("duration_ms", 0),
        "runtime": report.get("runtime") or {},
        "usage": report.get("usage") or {},
        "metrics": report.get("metrics") or {},
    }


def trace_tool_error_codes(trace_path: Path | None) -> list[str]:
    if trace_path is None:
        return []
    codes: list[str] = []
    for event in read_jsonl_optional(trace_path):
        if str(event.get("event") or "") != "tool_executed":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        is_error = bool(event.get("is_error") or payload.get("is_error"))
        if not is_error:
            continue
        codes.append(event_tool_error_code(event))
    return [code for code in codes if code]


def event_tool_error_code(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    code = str(metadata.get("error_code") or event.get("error_code") or "").strip()
    if code:
        return code
    content = str(payload.get("content") or event.get("content") or "")
    return classify_tool_error(content)


def classify_tool_error(content: str) -> str:
    text = content.lower()
    if "outside workspace" in text:
        return "outside_workspace"
    if "action denied" in text or "user denied" in text or "auto-denied" in text:
        return "action_denied"
    if "old_string found" in text and "must be unique" in text:
        return "patch_nonunique"
    if "old_string not found" in text:
        return "patch_old_string_missing"
    if "missing required" in text or "required" in text and "new_string" in text:
        return "patch_missing_new_text"
    if "invalid timeout" in text or "timeout" in text and "invalid" in text:
        return "timeout_out_of_range"
    if "timed out" in text:
        return "tool_timeout"
    if "requires a sandbox" in text:
        return "sandbox_required"
    return "tool_error"


def trace_tool_path_count(trace_path: Path | None, tool_name: str, relpath: str) -> int:
    if trace_path is None:
        return 0
    count = 0
    wanted = {relpath}
    for event in read_jsonl_optional(trace_path):
        if str(event.get("event") or "") != "tool_started":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        name = str(event.get("name") or payload.get("name") or "")
        if name != tool_name:
            continue
        if object_mentions_any_path(event, wanted):
            count += 1
    return count


def trace_tool_path_phase_counts(trace_path: Path | None, tool_name: str, relpath: str) -> dict[str, Any]:
    if trace_path is None:
        return {
            "observed_count": 0,
            "pre_edit_count": 0,
            "post_edit_count": 0,
            "mutation_observed": False,
        }

    events = read_jsonl_optional(trace_path)
    wanted = {relpath}
    mutation_index = _first_successful_path_mutation_index(events, wanted)
    pre_edit_count = 0
    post_edit_count = 0
    for index, event in enumerate(events):
        if str(event.get("event") or "") != "tool_started":
            continue
        if event_tool_name(event) != tool_name:
            continue
        if not object_mentions_any_path(event, wanted):
            continue
        if mutation_index is not None and index > mutation_index:
            post_edit_count += 1
        else:
            pre_edit_count += 1

    return {
        "observed_count": pre_edit_count + post_edit_count,
        "pre_edit_count": pre_edit_count,
        "post_edit_count": post_edit_count,
        "mutation_observed": mutation_index is not None,
    }


def _first_successful_path_mutation_index(events: list[dict[str, Any]], wanted: set[str]) -> int | None:
    mutation_call_ids: set[str] = set()
    mutation_start_seen = False
    for event in events:
        if str(event.get("event") or "") != "tool_started":
            continue
        if event_tool_name(event) not in {"edit_file", "write_file"}:
            continue
        if not object_mentions_any_path(event, wanted):
            continue
        mutation_start_seen = True
        call_id = event_call_id(event)
        if call_id:
            mutation_call_ids.add(call_id)

    for index, event in enumerate(events):
        if str(event.get("event") or "") != "tool_executed":
            continue
        if event_tool_name(event) not in {"edit_file", "write_file"}:
            continue
        if event_is_error(event):
            continue
        call_id = event_call_id(event)
        if call_id and call_id in mutation_call_ids:
            return index
        if object_mentions_any_path(event, wanted):
            return index
        if mutation_start_seen and not call_id and not mutation_call_ids:
            return index
    return None


def event_tool_name(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return str(event.get("name") or payload.get("name") or "")


def event_call_id(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    for source in (event, payload):
        for key in ("tool_call_id", "call_id", "id"):
            value = source.get(key)
            if value:
                return str(value)
    return ""


def event_is_error(event: dict[str, Any]) -> bool:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return bool(event.get("is_error") or payload.get("is_error"))


def trace_tool_names(trace_path: Path | None, event_name: str, *, successful_only: bool = False) -> list[str]:
    if trace_path is None:
        return []
    names: list[str] = []
    for event in read_jsonl_optional(trace_path):
        if str(event.get("event") or "") != event_name:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if successful_only:
            is_error = bool(event.get("is_error") or payload.get("is_error"))
            if is_error:
                continue
        name = str(event.get("name") or payload.get("name") or "")
        if name:
            names.append(name)
    return names


def trace_large_result_persist_count(trace_path: Path | None) -> int:
    if trace_path is None:
        return 0
    count = 0
    for event in read_jsonl_optional(trace_path):
        if str(event.get("event") or "") != "tool_executed":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        if metadata.get("persisted") is True:
            count += 1
    return count


def trace_context_governance_counts(trace_path: Path | None) -> dict[str, int]:
    counts = {
        "large_result_persist": trace_large_result_persist_count(trace_path),
        "tool_history_snip": 0,
        "context_compact": 0,
    }
    if trace_path is None:
        return counts
    for event in read_jsonl_optional(trace_path):
        name = str(event.get("event") or "")
        reason = event_reason(event)
        if name in {"context_prepared", "context_preparation", "conversation_committed"}:
            if reason == "tool_history_snip":
                counts["tool_history_snip"] += 1
            elif reason == "context_compact":
                counts["context_compact"] += 1
        elif name == "context_compacted":
            counts["context_compact"] += 1
    return counts


def event_reason(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return str(event.get("reason") or payload.get("reason") or "")


def disallowed_tool_names(names: list[str], allowed_tools: list[str] | None) -> list[str]:
    if allowed_tools is None:
        return []
    allowed = set(allowed_tools)
    return sorted({name for name in names if name not in allowed})


def object_mentions_any_path(value: Any, wanted: set[str]) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).lower()
            if isinstance(item, str) and path_text_matches(item, wanted) and ("path" in key_text or "file" in key_text):
                return True
            if object_mentions_any_path(item, wanted):
                return True
        return False
    if isinstance(value, list):
        return any(object_mentions_any_path(item, wanted) for item in value)
    return False


def path_text_matches(value: str, wanted: set[str]) -> bool:
    normalized = _normalize_path_text(value)
    return any(_path_matches_wanted(normalized, _normalize_path_text(path)) for path in wanted)


def _path_matches_wanted(value: str, wanted: str) -> bool:
    if not value or not wanted:
        return False
    return value == wanted or value.endswith("/" + wanted)


def _normalize_path_text(value: str) -> str:
    normalized = str(value).strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.rstrip("/")


def _iter_files(root: Path) -> list[Path]:
    skipped = {".nanocode", ".git", "__pycache__", ".pytest_cache"}
    files = []
    for path in root.rglob("*"):
        if any(part in skipped for part in path.relative_to(root).parts):
            continue
        if path.is_file():
            files.append(path)
    return sorted(files, key=lambda item: str(item.relative_to(root)))
