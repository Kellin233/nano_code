#!/usr/bin/env python3
"""Run NanoCode against local implementation-based fixture tasks."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BENCH_DIR.parents[1]
DEFAULT_TASK_FILE = BENCH_DIR / "tasks.json"
DEFAULT_RESULTS_DIR = BENCH_DIR / "results"
DEFAULT_SUITE = "core"
SUITE_CHOICES = {"core", "all", "security", "memory", "resume", "permissions"}
PERMISSION_MODE_CHOICES = {"yolo", "bypassPermissions", "default", "acceptEdits", "dontAsk"}
CONTEXT_GOVERNANCE_CHOICES = {"full", "off"}
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

import artifacts as benchmark_artifacts
import contracts as benchmark_contracts

REQUIRED_TASK_KEYS = {
    "id",
    "prompt",
    "fixture_repo",
    "artifact_path",
    "step_budget",
    "expected_artifact",
    "verifier",
    "category",
}


def _redact(text: str) -> str:
    if not text:
        return text
    redacted = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-[redacted]", text)
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        secret = os.environ.get(key)
        if secret and len(secret) >= 8:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "run"


def _write_memory_fixture(task: dict, workspace: Path) -> None:
    setup = task.get("memory_setup")
    if not isinstance(setup, dict):
        return
    topic = str(setup.get("topic") or "project")
    filename = {
        "preferences": "preferences.md",
        "project": "project.md",
        "debugging": "debugging.md",
    }.get(topic, "project.md")
    body = str(setup.get("content") or "").strip()
    if not body:
        return
    memory_dir = _memory_dir_for_workspace(workspace)
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / filename).write_text(f"# {topic.capitalize()}\n\n- {body}\n", encoding="utf-8")
    lines = [
        "# Memory Index",
        "",
        f"- [{filename}]({filename}): benchmark fixture memory",
        "",
    ]
    (memory_dir / "MEMORY.md").write_text("\n".join(lines), encoding="utf-8")


def _memory_dir_for_workspace(workspace: Path) -> Path:
    src_path = str(PROJECT_ROOT / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    from nanocode.cli.core.project.identity import get_project_memory_dir

    return get_project_memory_dir(
        workspace,
        home=_task_home(workspace),
        git_ceiling_directories=[_git_ceiling_for_workspace(workspace)],
    )


def _git_ceiling_for_workspace(workspace: Path) -> Path:
    return workspace.parent.resolve()


def _apply_git_ceiling(env: dict[str, str], workspace: Path) -> None:
    ceiling = str(_git_ceiling_for_workspace(workspace))
    existing = env.get("GIT_CEILING_DIRECTORIES")
    env["GIT_CEILING_DIRECTORIES"] = ceiling if not existing else os.pathsep.join([ceiling, existing])


def _prepare_security_scenario(task: dict, workspace: Path) -> None:
    setup = task.get("security_setup")
    if not isinstance(setup, dict):
        return

    deny_rules = setup.get("deny_rules")
    if isinstance(deny_rules, list) and deny_rules:
        settings = {"permissions": {"deny": [str(rule) for rule in deny_rules]}}
        path = workspace / ".claude" / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_local_module(name: str):
    path = BENCH_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"local_fixture_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load benchmark module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git_value(args: list[str], fallback: str = "") -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return result.stdout.strip() or fallback
    except Exception:
        return fallback


def _run_subprocess(
    command,
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
    shell: bool = False,
    stream: bool = False,
) -> subprocess.CompletedProcess:
    if not stream:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            shell=shell,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        shell=shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    def _forward(pipe, sink, chunks: list[str]) -> None:
        if pipe is None:
            return
        try:
            for line in iter(pipe.readline, ""):
                chunks.append(line)
                sink.write(_redact(line))
                sink.flush()
        finally:
            pipe.close()

    threads = [
        threading.Thread(target=_forward, args=(process.stdout, sys.stdout, stdout_chunks), daemon=True),
        threading.Thread(target=_forward, args=(process.stderr, sys.stderr, stderr_chunks), daemon=True),
    ]
    for thread in threads:
        thread.start()

    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        stderr_chunks.append(f"Command timed out after {timeout} seconds.\n")
        for thread in threads:
            thread.join(timeout=1)
        raise subprocess.TimeoutExpired(
            exc.cmd,
            exc.timeout,
            output="".join(stdout_chunks),
            stderr="".join(stderr_chunks),
        ) from exc

    for thread in threads:
        thread.join(timeout=1)
    return subprocess.CompletedProcess(
        command,
        returncode,
        "".join(stdout_chunks),
        "".join(stderr_chunks),
    )


def _fixture_snapshot_id(fixture_paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for fixture in sorted({path.resolve() for path in fixture_paths}, key=str):
        for path in sorted((item for item in fixture.rglob("*") if item.is_file()), key=lambda item: str(item.relative_to(fixture))):
            digest.update(fixture.name.encode())
            digest.update(b"\0")
            digest.update(str(path.relative_to(fixture)).encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _benchmark_definition_id(task_file: Path = DEFAULT_TASK_FILE) -> str:
    digest = hashlib.sha256()
    for path in [
        task_file,
        BENCH_DIR / "run.py",
        BENCH_DIR / "artifacts.py",
        BENCH_DIR / "contracts.py",
        BENCH_DIR / "metrics.py",
        BENCH_DIR / "report.py",
    ]:
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def load_benchmark(path: Path = DEFAULT_TASK_FILE) -> dict:
    payload = benchmark_artifacts.read_json(path)
    if int(payload.get("schema_version", 0)) != 1:
        raise ValueError("unsupported schema_version")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("tasks must be a non-empty list")

    seen: set[str] = set()
    normalized = []
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise ValueError(f"task at index {index} must be an object")
        missing = sorted(REQUIRED_TASK_KEYS - set(task))
        if missing:
            raise ValueError(f"task {task.get('id', index)!r} missing keys: {', '.join(missing)}")
        task_id = str(task["id"]).strip()
        if not task_id:
            raise ValueError(f"task at index {index} has empty id")
        if task_id in seen:
            raise ValueError(f"duplicate task id: {task_id}")
        seen.add(task_id)
        fixture = BENCH_DIR / str(task["fixture_repo"])
        if not fixture.is_dir():
            raise ValueError(f"task {task_id} fixture_repo not found: {task['fixture_repo']}")
        step_budget = int(task["step_budget"])
        max_turns = int(task.get("max_turns", step_budget))
        tool_step_budget = int(task.get("tool_step_budget", step_budget))
        if step_budget < 1:
            raise ValueError(f"task {task_id} step_budget must be positive")
        if max_turns < 1:
            raise ValueError(f"task {task_id} max_turns must be positive")
        if tool_step_budget < 1:
            raise ValueError(f"task {task_id} tool_step_budget must be positive")
        row = dict(task)
        if "context_window" in row:
            context_window = int(row["context_window"])
            if context_window <= 20000:
                raise ValueError(f"task {task_id} context_window must be greater than 20000")
            row["context_window"] = context_window
        if "allowed_tools" in row:
            allowed_tools = row["allowed_tools"]
            if not isinstance(allowed_tools, list) or not all(isinstance(name, str) for name in allowed_tools):
                raise ValueError(f"task {task_id} allowed_tools must be a list of strings")
            row["allowed_tools"] = [name.strip() for name in allowed_tools if name.strip()]
        permission_mode = str(row.get("permission_mode") or "yolo")
        if permission_mode not in PERMISSION_MODE_CHOICES:
            raise ValueError(
                f"task {task_id} permission_mode must be one of: "
                f"{', '.join(sorted(PERMISSION_MODE_CHOICES))}"
            )
        row["permission_mode"] = permission_mode
        if "tags" in row:
            tags = row["tags"]
            if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
                raise ValueError(f"task {task_id} tags must be a list of strings")
            row["tags"] = [tag.strip() for tag in tags if tag.strip()]
        if row.get("security_case") and not isinstance(row.get("security_expectation"), dict):
            raise ValueError(f"task {task_id} security tasks must define security_expectation")
        if "tool_path_limits" in row:
            limits = row["tool_path_limits"]
            if not isinstance(limits, list):
                raise ValueError(f"task {task_id} tool_path_limits must be a list")
            for limit in limits:
                if not isinstance(limit, dict):
                    raise ValueError(f"task {task_id} tool_path_limits entries must be objects")
                if not str(limit.get("tool") or "").strip():
                    raise ValueError(f"task {task_id} tool_path_limits entries require tool")
                if not str(limit.get("path") or "").strip():
                    raise ValueError(f"task {task_id} tool_path_limits entries require path")
                if int(limit.get("max_count", -1)) < 0:
                    raise ValueError(f"task {task_id} tool_path_limits entries require non-negative max_count")
                for key in ("max_pre_edit_count", "max_post_edit_count"):
                    if key in limit and limit[key] is not None and int(limit[key]) < 0:
                        raise ValueError(f"task {task_id} tool_path_limits entries require non-negative {key}")
        scenario = str(row.get("scenario") or "default")
        if scenario not in {"default", "resume"}:
            raise ValueError(f"task {task_id} scenario must be 'default' or 'resume'")
        if scenario == "resume":
            required_resume = {"resume_session_id", "resume_interrupted_run_id", "resume_seed_prompt"}
            missing_resume = sorted(required_resume - set(row))
            if missing_resume:
                raise ValueError(f"task {task_id} missing resume keys: {', '.join(missing_resume)}")
        if "suite" in row:
            suite = str(row["suite"] or "").strip()
            if suite not in SUITE_CHOICES:
                raise ValueError(f"task {task_id} suite must be one of: {', '.join(sorted(SUITE_CHOICES))}")
            row["suite"] = suite
        else:
            row["suite"] = _task_suite(row)
        row["scenario"] = scenario
        row["id"] = task_id
        row["step_budget"] = step_budget
        row["max_turns"] = max_turns
        row["tool_step_budget"] = tool_step_budget
        normalized.append(row)

    return {**payload, "tasks": normalized}


def _task_suite(task: dict) -> str:
    if task.get("security_case"):
        return "security"
    return "core"


def _task_matches_suite(task: dict, suite: str) -> bool:
    if suite == "all":
        return True
    if suite == "resume":
        return task.get("scenario") == "resume"
    if suite == "memory":
        return task.get("category") == "memory" or "memory" in set(task.get("tags") or [])
    return str(task.get("suite") or _task_suite(task)) == suite


def _normalize_context_governance(value: str | None) -> str:
    normalized = (value or "full").strip().lower()
    if normalized not in CONTEXT_GOVERNANCE_CHOICES:
        raise ValueError(f"context_governance must be one of: {', '.join(sorted(CONTEXT_GOVERNANCE_CHOICES))}")
    return normalized


def _run_nanocode(
    task: dict,
    workspace: Path,
    *,
    timeout: int,
    model: str | None = None,
    stream: bool = False,
    context_governance: str = "full",
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HOME"] = str(_task_home(workspace))
    env["NANO_CODE_CONTEXT_GOVERNANCE"] = _normalize_context_governance(context_governance)
    if task.get("context_window"):
        env["NANO_CODE_CONTEXT_WINDOW"] = str(int(task["context_window"]))
    _apply_git_ceiling(env, workspace)
    src_path = str(PROJECT_ROOT / "src")
    env["PYTHONPATH"] = src_path + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    command = [
        sys.executable,
        "-m",
        "nanocode.cli.main",
        "--max-turns",
        str(task.get("max_turns", task["step_budget"])),
    ]
    permission_mode = str(task.get("permission_mode") or "yolo")
    if permission_mode in {"yolo", "bypassPermissions"}:
        command.append("--yolo")
    elif permission_mode == "dontAsk":
        command.append("--dont-ask")
    elif permission_mode == "acceptEdits":
        command.append("--accept-edits")
    elif permission_mode != "default":
        raise ValueError(f"unsupported permission_mode: {permission_mode}")
    if model:
        command.extend(["--model", model])
    if "allowed_tools" in task:
        command.extend(["--allowed-tools", ",".join(task["allowed_tools"])])
    if task.get("scenario") == "resume":
        command.append("--resume")
    command.append(str(task["prompt"]))
    return _run_subprocess(command, cwd=workspace, env=env, timeout=timeout, stream=stream)


def _task_home(workspace: Path) -> Path:
    return (workspace.parent / ".home").resolve()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prepare_resume_scenario(task: dict, workspace: Path) -> None:
    case = benchmark_contracts.resume_case(task)
    session_id = benchmark_contracts.resume_session_id(task)
    interrupted_run_id = benchmark_contracts.resume_interrupted_run_id(task)
    seed_prompt = str(task.get("resume_seed_prompt") or task["prompt"])
    tool_call_id = str(task.get("resume_tool_call_id") or "call_resume_seed")
    created_at = _now_iso()
    target_path = str(task["artifact_path"])
    old_string = str(task.get("resume_old_string") or "resume_marker=todo")
    new_string = str(task.get("resume_new_string") or "resume_marker=done")

    session_path = _task_home(workspace) / ".nanocode" / "sessions" / session_id / "session.jsonl"
    rows = [
        {
            "type": "session",
            "version": 2,
            "id": session_id,
            "created_at": created_at,
            "workspace": str(workspace),
            "provider": "",
            "model": "",
        },
        {
            "seq": 1,
            "created_at": created_at,
            "type": "message",
            "reason": "user_message_accepted",
            "run_id": interrupted_run_id,
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": seed_prompt}],
                "metadata": {},
            },
        },
    ]
    if task.get("resume_orphaned_tool_call"):
        rows.append(
            {
                "seq": 2,
                "created_at": created_at,
                "type": "message",
                "reason": "assistant_tool_call",
                "run_id": interrupted_run_id,
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": tool_call_id,
                            "name": "edit_file",
                            "input": {
                                "file_path": target_path,
                                "old_string": old_string,
                                "new_string": new_string,
                            },
                        }
                    ],
                    "metadata": {},
                },
            }
        )
    else:
        rows.append(
            {
                "seq": 2,
                "created_at": created_at,
                "type": "message",
                "reason": "assistant_checkpoint",
                "run_id": interrupted_run_id,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": f"Task checkpoint: {seed_prompt}"}],
                    "metadata": {},
                },
            }
        )
    rows.append(
        {
            "seq": 3,
            "created_at": created_at,
            "type": "checkpoint",
            "reason": case,
            "run_id": interrupted_run_id,
        }
    )
    benchmark_artifacts.write_jsonl(session_path, rows)

    trace_path = workspace / ".nanocode" / "runs" / interrupted_run_id / "trace.jsonl"
    benchmark_artifacts.write_jsonl(trace_path, [
        {
            "event": "run_started",
            "created_at": created_at,
            "run_id": interrupted_run_id,
            "session_id": session_id,
            "task_id": f"task_{_safe_name(str(task['id']))}_seed",
            "user_request": seed_prompt,
            "workspace": str(workspace),
        }
    ])


def _run_verifier(
    task: dict,
    workspace: Path,
    timeout: int,
    *,
    stream: bool = False,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return _run_subprocess(
        str(task["verifier"]),
        cwd=workspace,
        env=env,
        shell=True,
        timeout=timeout,
        stream=stream,
    )


def _failure_category(row: dict) -> str:
    if row.get("passed"):
        return ""
    if row.get("nanocode_returncode") not in (0, None):
        return "nanocode_failed"
    if not row.get("report_exists"):
        return "missing_report"
    if not row.get("report_parse_valid"):
        return "invalid_report"
    if not row.get("trace_exists"):
        return "missing_trace"
    if not row.get("trace_parse_valid"):
        return "invalid_trace"
    if not row.get("trace_contract_met", True):
        return "trace_contract_failed"
    if not row.get("non_failure_stop_reason"):
        return "bad_stop_reason"
    if not row.get("within_budget"):
        return "budget_exceeded"
    if not row.get("allowed_tools_enforced", True):
        return "disallowed_tool_executed"
    if not row.get("expected_artifact_exists"):
        return "missing_artifact"
    if not row.get("verifier_passed"):
        return "verifier_failed"
    if not row.get("specialty_contract_met", True):
        return str(row.get("specialty_failure_category") or "specialty_contract_failed")
    return "unknown"


def _trace_contract(
    *,
    trace_path: Path | None,
    trace_exists: bool,
    trace_parse_valid: bool,
    report: dict,
    report_parse_valid: bool,
    allowed_tools: list[str] | None,
    nanocode_returncode: int,
) -> tuple[bool, list[str]]:
    if allowed_tools is None:
        return True, []
    if not trace_exists:
        return False, ["missing_trace"]
    if not trace_parse_valid:
        return False, ["invalid_trace"]

    events = benchmark_artifacts.read_jsonl_optional(trace_path) if trace_path is not None else []
    event_names = [str(event.get("event") or "") for event in events]
    errors: list[str] = []
    if "run_started" not in event_names:
        errors.append("missing_run_started")
    if nanocode_returncode == 0 and "run_finished" not in event_names:
        errors.append("missing_run_finished")

    tool_started = [event for event in events if str(event.get("event") or "") == "tool_started"]
    tool_executed = [event for event in events if str(event.get("event") or "") == "tool_executed"]
    tool_steps = _int_report_value(report, "tool_steps") if report_parse_valid else 0
    if tool_steps > 0 and not tool_executed:
        errors.append("missing_tool_executed")
    if report_parse_valid and len(tool_executed) != tool_steps:
        errors.append(f"tool_executed_count_mismatch:trace={len(tool_executed)} report={tool_steps}")

    started_names = {_event_tool_name(event) for event in tool_started}
    executed_names = {_event_tool_name(event) for event in tool_executed}
    trace_tool_counts = _trace_tool_counts(tool_executed)
    reported_tool_counts = _reported_tool_counts(report) if report_parse_valid else {}
    reported_names = set(reported_tool_counts)
    if len(tool_executed) != sum(trace_tool_counts.values()):
        errors.append("unnamed_tool_executed")
    if report_parse_valid and sum(reported_tool_counts.values()) != tool_steps:
        errors.append(
            "report_tool_name_count_sum_mismatch:"
            f"counts={sum(reported_tool_counts.values())} tool_steps={tool_steps}"
        )
    if missing_reported := sorted(reported_names - (started_names | executed_names)):
        errors.append("report_tools_missing_from_trace:" + ",".join(missing_reported))
    if missing_starts := sorted(name for name in executed_names if name and name not in started_names):
        errors.append("tool_executed_without_started:" + ",".join(missing_starts))
    if report_parse_valid and trace_tool_counts != reported_tool_counts:
        errors.append(
            "tool_name_counts_mismatch:"
            f"trace={dict(sorted(trace_tool_counts.items()))} "
            f"report={dict(sorted(reported_tool_counts.items()))}"
        )

    return not errors, errors


def _event_tool_name(event: dict) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return str(event.get("name") or payload.get("name") or "")


def _trace_tool_counts(events: list[dict]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for event in events:
        name = _event_tool_name(event)
        if name:
            counts[name] += 1
    return dict(sorted(counts.items()))


def _reported_tool_counts(report: dict) -> dict[str, int]:
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    counts = metrics.get("tool_name_counts") if isinstance(metrics.get("tool_name_counts"), dict) else {}
    return dict(sorted((str(name), _safe_int(count)) for name, count in counts.items() if _safe_int(count) > 0))


def _int_report_value(report: dict, key: str) -> int:
    try:
        return int(report.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _safe_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _progress_bar(done: int, total: int, *, width: int = 24) -> str:
    if total <= 0:
        return "[" + "-" * width + "]"
    filled = min(width, max(0, round(width * done / total)))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def _print_progress(done: int, total: int, *, passed: int = 0, failed: int = 0, label: str = "") -> None:
    message = f"{_progress_bar(done, total)} {done}/{total}"
    if done:
        message += f" passed={passed} failed={failed}"
    if label:
        message += f" {label}"
    print(message, flush=True)


def run_task(
    task: dict,
    *,
    run_root: Path,
    timeout: int,
    model: str | None,
    stream: bool = False,
    context_governance: str = "full",
) -> dict:
    context_governance = _normalize_context_governance(context_governance)
    fixture_source = BENCH_DIR / str(task["fixture_repo"])
    workspace = run_root / "workspaces" / task["id"] / fixture_source.name
    task_artifact_dir = run_root / "tasks" / task["id"]
    if workspace.exists():
        shutil.rmtree(workspace)
    if task_artifact_dir.exists():
        shutil.rmtree(task_artifact_dir)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    task_artifact_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(fixture_source, workspace)
    _write_memory_fixture(task, workspace)
    _prepare_security_scenario(task, workspace)
    if task.get("scenario") == "resume" and not task.get("skip_resume_seed"):
        _prepare_resume_scenario(task, workspace)

    started = time.monotonic()
    if stream:
        print(f"\n=== {task['id']} :: nanocode ===", flush=True)
    try:
        nanocode = _run_nanocode(
            task,
            workspace,
            timeout=timeout,
            model=model,
            stream=stream,
            context_governance=context_governance,
        )
    except subprocess.TimeoutExpired as exc:
        nanocode = subprocess.CompletedProcess(exc.cmd, 124, exc.stdout or "", exc.stderr or "NanoCode timed out.")
    duration_ms = int((time.monotonic() - started) * 1000)

    (task_artifact_dir / "nanocode_stdout.txt").write_text(_redact(nanocode.stdout or ""), encoding="utf-8")
    (task_artifact_dir / "nanocode_stderr.txt").write_text(_redact(nanocode.stderr or ""), encoding="utf-8")

    run_artifacts = benchmark_artifacts.collect_run_artifacts(workspace, task_artifact_dir, str(task["prompt"]))
    run_dir = run_artifacts.run_dir
    report = run_artifacts.report
    report_path = run_artifacts.report_path
    report_exists = run_artifacts.report_exists
    report_parse_valid = run_artifacts.report_parse_valid
    trace_path = run_artifacts.trace_path
    trace_exists = run_artifacts.trace_exists

    resume_interrupted_marked = False
    resume_orphan_repaired = False
    resume_interrupted_trace_relpath = ""
    session_path = None
    session_exists = False
    if task.get("scenario") == "resume":
        interrupted_trace = workspace / ".nanocode" / "runs" / benchmark_contracts.resume_interrupted_run_id(task) / "trace.jsonl"
        if interrupted_trace.exists():
            target = task_artifact_dir / "resume_interrupted_trace.jsonl"
            shutil.copy2(interrupted_trace, target)
            resume_interrupted_trace_relpath = str(target.relative_to(run_root))
            trace_text = interrupted_trace.read_text(encoding="utf-8", errors="replace")
            resume_interrupted_marked = '"event": "run_interrupted"' in trace_text
        session_path = _task_home(workspace) / ".nanocode" / "sessions" / benchmark_contracts.resume_session_id(task) / "session.jsonl"
        session_exists = session_path.exists()
        if session_path.exists():
            session_copy = task_artifact_dir / "resume_session.jsonl"
            shutil.copy2(session_path, session_copy)
            resume_orphan_repaired = "Interrupted before tool result" in session_path.read_text(
                encoding="utf-8",
                errors="replace",
            )

    if stream:
        print(f"\n=== {task['id']} :: verifier ===", flush=True)
    verifier_env = benchmark_artifacts.verifier_env(run_artifacts)
    try:
        verifier = _run_verifier(task, workspace, timeout=timeout, stream=stream, env_extra=verifier_env)
    except subprocess.TimeoutExpired as exc:
        verifier = subprocess.CompletedProcess(exc.cmd, 124, exc.stdout or "", exc.stderr or "Verifier timed out.")

    verifier_output = (verifier.stdout or "") + ("\n" if verifier.stdout and verifier.stderr else "") + (verifier.stderr or "")
    (task_artifact_dir / "verifier_output.txt").write_text(_redact(verifier_output), encoding="utf-8")
    (task_artifact_dir / "patch.diff").write_text(
        benchmark_artifacts.directory_diff(fixture_source, workspace),
        encoding="utf-8",
    )

    expected_artifact = workspace / str(task["artifact_path"])
    resume_output = "\n".join([nanocode.stdout or "", nanocode.stderr or ""])
    contract_fields = benchmark_contracts.evaluate_contracts(
        task=task,
        trace_path=trace_path,
        verifier_returncode=verifier.returncode,
        session_exists=session_exists,
        resume_interrupted_marked=resume_interrupted_marked,
        resume_orphan_repaired=resume_orphan_repaired,
        resume_output=resume_output,
        nanocode_returncode=nanocode.returncode,
    )
    tool_steps = int(report.get("tool_steps", 0) or 0)
    stop_reason = str(report.get("stop_reason", "") or "")
    allowed_tools = task.get("allowed_tools")
    trace_contract_required = allowed_tools is not None
    trace_contract_met, trace_contract_errors = _trace_contract(
        trace_path=trace_path,
        trace_exists=trace_exists,
        trace_parse_valid=run_artifacts.trace_parse_valid,
        report=report,
        report_parse_valid=report_parse_valid,
        allowed_tools=allowed_tools,
        nanocode_returncode=nanocode.returncode,
    )
    tool_counts = report.get("metrics", {}).get("tool_name_counts", {}) if report else {}
    reported_tools = sorted(str(name) for name, count in tool_counts.items() if _safe_int(count) > 0)
    requested_tools = sorted(set(benchmark_artifacts.trace_tool_names(trace_path, "tool_started")))
    successful_tools = sorted(set(benchmark_artifacts.trace_tool_names(trace_path, "tool_executed", successful_only=True)))
    used_tools = reported_tools or requested_tools
    disallowed_tool_requests = benchmark_artifacts.disallowed_tool_names(requested_tools or used_tools, allowed_tools)
    disallowed_tool_executions = benchmark_artifacts.disallowed_tool_names(successful_tools, allowed_tools)
    allowed_tools_respected = not disallowed_tool_requests
    allowed_tools_enforced = not disallowed_tool_executions
    tool_step_budget = int(task["tool_step_budget"])
    row = {
        "id": task["id"],
        "suite": str(task.get("suite") or _task_suite(task)),
        "category": task["category"],
        "tags": list(task.get("tags", [])),
        "ablation_variant": str(task.get("ablation_variant") or ""),
        "ablation_repetition": int(task.get("ablation_repetition", 0) or 0),
        "prompt": task["prompt"],
        "fixture_repo": task["fixture_repo"],
        "workspace_relpath": str(workspace.relative_to(run_root)),
        "artifact_dir_relpath": str(task_artifact_dir.relative_to(run_root)),
        "scenario": str(task.get("scenario") or "default"),
        "permission_mode": str(task.get("permission_mode") or "yolo"),
        "context_governance": context_governance,
        "context_window": int(task["context_window"]) if task.get("context_window") else None,
        "run_dir_relpath": str(run_dir.relative_to(run_root)) if run_dir else "",
        "report_relpath": str(report_path.relative_to(run_root)) if report_path and report_exists else "",
        "trace_relpath": str(trace_path.relative_to(run_root)) if trace_path and trace_exists else "",
        "resume_interrupted_trace_relpath": resume_interrupted_trace_relpath,
        "duration_ms": duration_ms,
        "nanocode_returncode": nanocode.returncode,
        "verifier_returncode": verifier.returncode,
        "verifier_passed": verifier.returncode == 0,
        "report_exists": report_exists,
        "report_parse_valid": report_parse_valid,
        "report_parse_error": run_artifacts.report_parse_error,
        "trace_exists": trace_exists,
        "trace_parse_valid": run_artifacts.trace_parse_valid,
        "trace_parse_error": run_artifacts.trace_parse_error,
        "trace_event_count": run_artifacts.trace_event_count,
        "trace_contract_required": trace_contract_required,
        "trace_contract_met": trace_contract_met,
        "trace_contract_errors": trace_contract_errors,
        "expected_artifact_exists": expected_artifact.exists(),
        **contract_fields,
        "tool_steps": tool_steps,
        "allowed_tools": list(allowed_tools) if allowed_tools is not None else None,
        "runtime_allowed_tools": report.get("runtime", {}).get("allowed_tools") if report else None,
        "requested_tools": requested_tools,
        "successful_tools": successful_tools,
        "used_tools": used_tools,
        "disallowed_tool_requests": disallowed_tool_requests,
        "disallowed_tool_executions": disallowed_tool_executions,
        "allowed_tools_respected": allowed_tools_respected,
        "allowed_tools_enforced": allowed_tools_enforced,
        "attempts": int(report.get("attempts", 0) or 0),
        "step_budget": int(task["step_budget"]),
        "max_turns": int(task["max_turns"]),
        "tool_step_budget": tool_step_budget,
        "within_budget": tool_steps <= tool_step_budget,
        "stop_reason": stop_reason,
        "non_failure_stop_reason": stop_reason == "stop",
        "report_summary": benchmark_artifacts.report_summary(report),
    }
    row["non_context_specialty_contract_met"] = bool(
        row["security_contract_met"]
        and row["memory_contract_met"]
        and row["resume_contract_met"]
        and row["tool_path_limit_contract_met"]
    )
    row["task_completion_pass"] = bool(
        row["nanocode_returncode"] == 0
        and row["verifier_passed"]
        and row["report_exists"]
        and row["report_parse_valid"]
        and row["expected_artifact_exists"]
        and row["trace_contract_met"]
        and row["within_budget"]
        and row["non_failure_stop_reason"]
        and row["allowed_tools_enforced"]
        and row["non_context_specialty_contract_met"]
    )
    row["passed"] = (
        row["nanocode_returncode"] == 0
        and row["verifier_passed"]
        and row["report_exists"]
        and row["report_parse_valid"]
        and row["expected_artifact_exists"]
        and row["trace_contract_met"]
        and row["within_budget"]
        and row["non_failure_stop_reason"]
        and row["allowed_tools_enforced"]
        and row["specialty_contract_met"]
    )
    row["status"] = "pass" if row["passed"] else "fail"
    row["failure_category"] = _failure_category(row)
    benchmark_artifacts.write_json(task_artifact_dir / "task_result.json", row)
    if stream:
        suffix = "" if row["passed"] else f" ({row['failure_category']})"
        print(f"=== {task['id']} :: {row['status']}{suffix} ===", flush=True)
    return row


def _harness_error_row(task: dict, *, run_root: Path, exc: Exception, context_governance: str = "full") -> dict:
    task_id = str(task.get("id") or "unknown_task")
    context_governance = _normalize_context_governance(context_governance)
    task_artifact_dir = run_root / "tasks" / task_id
    task_artifact_dir.mkdir(parents=True, exist_ok=True)
    error_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    (task_artifact_dir / "harness_error.txt").write_text(_redact(error_text), encoding="utf-8")
    row = {
        "id": task_id,
        "suite": str(task.get("suite") or _task_suite(task)),
        "category": str(task.get("category") or "unknown"),
        "tags": list(task.get("tags", [])),
        "ablation_variant": str(task.get("ablation_variant") or ""),
        "ablation_repetition": int(task.get("ablation_repetition", 0) or 0),
        "prompt": str(task.get("prompt") or ""),
        "fixture_repo": str(task.get("fixture_repo") or ""),
        "workspace_relpath": "",
        "artifact_dir_relpath": str(task_artifact_dir.relative_to(run_root)),
        "scenario": str(task.get("scenario") or "default"),
        "permission_mode": str(task.get("permission_mode") or "yolo"),
        "context_governance": context_governance,
        "run_dir_relpath": "",
        "report_relpath": "",
        "trace_relpath": "",
        "resume_interrupted_trace_relpath": "",
        "duration_ms": 0,
        "nanocode_returncode": None,
        "verifier_returncode": None,
        "verifier_passed": False,
        "report_exists": False,
        "report_parse_valid": False,
        "report_parse_error": "",
        "trace_exists": False,
        "trace_parse_valid": False,
        "trace_parse_error": "",
        "trace_event_count": 0,
        "trace_contract_required": task.get("allowed_tools") is not None,
        "trace_contract_met": False,
        "trace_contract_errors": ["harness_error"],
        "expected_artifact_exists": False,
        "tool_steps": 0,
        "allowed_tools": list(task.get("allowed_tools") or []) if task.get("allowed_tools") is not None else None,
        "runtime_allowed_tools": None,
        "requested_tools": [],
        "successful_tools": [],
        "used_tools": [],
        "disallowed_tool_requests": [],
        "disallowed_tool_executions": [],
        "allowed_tools_respected": True,
        "allowed_tools_enforced": True,
        "attempts": 0,
        "step_budget": int(task.get("step_budget", 0) or 0),
        "max_turns": int(task.get("max_turns", task.get("step_budget", 0)) or 0),
        "tool_step_budget": int(task.get("tool_step_budget", task.get("step_budget", 0)) or 0),
        "within_budget": False,
        "stop_reason": "",
        "non_failure_stop_reason": False,
        "report_summary": {},
        "non_context_specialty_contract_met": False,
        "task_completion_pass": False,
        "harness_error_type": type(exc).__name__,
        "harness_error": str(exc),
        "passed": False,
        "status": "fail",
        "failure_category": "harness_error",
    }
    benchmark_artifacts.write_json(task_artifact_dir / "task_result.json", row)
    return row


def summarize_rows(rows: list[dict], *, selected_task_count: int | None = None) -> dict:
    total = len(rows)
    passed = sum(1 for row in rows if row.get("passed"))
    failure_counts: dict[str, int] = {}
    category_counts: dict[str, dict] = {}
    for row in rows:
        category = str(row.get("category") or "uncategorized")
        category_summary = category_counts.setdefault(
            category,
            {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "pass_rate": 0.0,
                "avg_tool_steps": 0.0,
            },
        )
        category_summary["total"] += 1
        category_summary["passed"] += 1 if row.get("passed") else 0
        category_summary["failed"] += 0 if row.get("passed") else 1
        category_summary["avg_tool_steps"] += int(row.get("tool_steps", 0))

        if row.get("passed"):
            continue
        failure_category = str(row.get("failure_category") or "unknown")
        failure_counts[failure_category] = failure_counts.get(failure_category, 0) + 1

    for summary in category_counts.values():
        category_total = int(summary["total"])
        summary["pass_rate"] = (int(summary["passed"]) / category_total) if category_total else 0.0
        summary["avg_tool_steps"] = (float(summary["avg_tool_steps"]) / category_total) if category_total else 0.0

    durations = [int(row.get("duration_ms", 0)) for row in rows]
    selected_tasks = selected_task_count if selected_task_count is not None else total
    return {
        "selected_tasks": selected_tasks,
        "executed_tasks": total,
        "total_tasks": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": (passed / total) if total else 0.0,
        "within_budget": sum(1 for row in rows if row.get("within_budget")),
        "within_budget_rate": (sum(1 for row in rows if row.get("within_budget")) / total) if total else 0.0,
        "verifier_passes": sum(1 for row in rows if row.get("verifier_passed")),
        "verifier_pass_rate": (sum(1 for row in rows if row.get("verifier_passed")) / total) if total else 0.0,
        "avg_tool_steps": (sum(int(row.get("tool_steps", 0)) for row in rows) / total) if total else 0.0,
        "avg_attempts": (sum(int(row.get("attempts", 0)) for row in rows) / total) if total else 0.0,
        "avg_duration_ms": (sum(durations) / total) if total else 0.0,
        "max_duration_ms": max(durations) if durations else 0,
        "category_counts": dict(sorted(category_counts.items())),
        "category_avg_tool_steps": {
            category: summary["avg_tool_steps"]
            for category, summary in sorted(category_counts.items())
        },
        "failure_category_counts": failure_counts,
    }


def run_benchmark(args: argparse.Namespace) -> dict:
    benchmark = load_benchmark(Path(args.task_file))
    context_governance = _normalize_context_governance(getattr(args, "context_governance", "full"))
    tasks = list(benchmark["tasks"])
    if args.task_id:
        wanted = set(args.task_id)
        tasks = [task for task in tasks if task["id"] in wanted]
    else:
        suite = str(getattr(args, "suite", DEFAULT_SUITE) or DEFAULT_SUITE)
        if suite not in SUITE_CHOICES:
            raise ValueError(f"unsupported suite: {suite}")
        tasks = [task for task in tasks if _task_matches_suite(task, suite)]
    if args.limit is not None:
        tasks = tasks[: int(args.limit)]
    if not tasks:
        raise ValueError("no tasks selected")

    run_name = _safe_name(args.run_name or time.strftime("local-fixture-%Y%m%d-%H%M%S"))
    run_root = Path(args.output_root) / run_name
    run_root.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        rows = []
        print(f"dry-run selected {len(tasks)} tasks; no tasks executed.", flush=True)
    else:
        rows = []
        total = len(tasks)
        passed = 0
        failed = 0
        for index, task in enumerate(tasks, start=1):
            _print_progress(index - 1, total, passed=passed, failed=failed, label=f"starting {task['id']}")
            try:
                row = run_task(
                    task,
                    run_root=run_root,
                    timeout=int(args.timeout),
                    model=args.model,
                    stream=bool(args.stream),
                    context_governance=context_governance,
                )
            except Exception as exc:
                row = _harness_error_row(
                    task,
                    run_root=run_root,
                    exc=exc,
                    context_governance=context_governance,
                )
            rows.append(row)
            if row.get("passed"):
                passed += 1
            else:
                failed += 1
            suffix = "" if row.get("passed") else f" ({row.get('failure_category') or 'unknown'})"
            _print_progress(index, total, passed=passed, failed=failed, label=f"finished {task['id']}={row['status']}{suffix}")

    metrics_module = _load_local_module("metrics")
    report_module = _load_local_module("report")
    scorecards = metrics_module.build_scorecards(rows, run_root)

    artifact = {
        "schema_version": 2,
        "run_name": run_name,
        "benchmark": {
            "source": str(Path(args.task_file).resolve()),
            "task_count": len(tasks),
            "suite": "task-id" if args.task_id else str(getattr(args, "suite", DEFAULT_SUITE) or DEFAULT_SUITE),
            "selected_task_ids": [task["id"] for task in tasks],
        },
        "runtime": {
            "commit_sha": _git_value(["rev-parse", "HEAD"]),
            "branch": _git_value(["branch", "--show-current"]),
            "model": args.model or os.environ.get("NANO_CODE_MODEL", ""),
            "context_governance": context_governance,
        },
        "reproducibility": {
            "fixture_snapshot_id": _fixture_snapshot_id([BENCH_DIR / str(task["fixture_repo"]) for task in tasks]),
            "benchmark_definition_id": _benchmark_definition_id(Path(args.task_file)),
        },
        "summary": summarize_rows(rows, selected_task_count=len(tasks)),
        "scorecards": scorecards,
        "rows": rows,
    }
    benchmark_artifacts.write_json(run_root / "benchmark.json", artifact)
    report_module.write_reports(run_root, artifact)
    return artifact


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the NanoCode local fixture benchmark.")
    parser.add_argument("--task-file", default=str(DEFAULT_TASK_FILE))
    parser.add_argument("--output-root", default=str(DEFAULT_RESULTS_DIR))
    parser.add_argument("--run-name", default="")
    parser.add_argument("--task-id", action="append", default=None, help="Run only a specific task id; may be repeated.")
    parser.add_argument("--suite", choices=sorted(SUITE_CHOICES), default=DEFAULT_SUITE, help="Task suite to run when --task-id is not set.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=180, help="Per-task NanoCode/verifier timeout in seconds.")
    parser.add_argument("--model", default=None, help="Optional model override passed to nanocode --model.")
    parser.add_argument(
        "--context-governance",
        choices=sorted(CONTEXT_GOVERNANCE_CHOICES),
        default="full",
        help="Context governance variant for ablation runs.",
    )
    parser.add_argument("--stream", action="store_true", help="Print NanoCode and verifier output while each task runs.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and write an empty benchmark artifact without invoking NanoCode.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    artifact = run_benchmark(args)
    summary = artifact["summary"]
    if args.dry_run:
        print(
            f"{artifact['run_name']}: dry-run selected={summary['selected_tasks']} "
            f"executed={summary['executed_tasks']}"
        )
    else:
        print(
            f"{artifact['run_name']}: {summary['passed']}/{summary['total_tasks']} passed "
            f"({summary['pass_rate']:.1%})"
        )
    print(Path(args.output_root) / artifact["run_name"] / "benchmark.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
