"""Scorecard aggregation for the local fixture benchmark."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any

BENCH_DIR = Path(__file__).resolve().parent
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

import artifacts as benchmark_artifacts


def build_scorecards(rows: list[dict[str, Any]], run_root: Path | str) -> dict[str, dict[str, Any]]:
    """Build Pico-style scorecards from NanoCode benchmark rows and run artifacts."""
    run_root = Path(run_root)
    rows = list(rows)
    trace_events_by_task = {
        str(row.get("id") or ""): parse_trace_events(_task_artifact_path(run_root, row, "trace.jsonl"))
        for row in rows
    }
    reports_by_task = {
        str(row.get("id") or ""): _report_for_row(run_root, row)
        for row in rows
    }
    return {
        "harness_regression": _harness_regression(rows),
        "tool_control": _tool_control(rows, reports_by_task),
        "security": _security(rows),
        "recovery": _recovery(rows, reports_by_task),
        "context_governance": _context_governance(rows, trace_events_by_task),
        "memory": _memory(rows),
        "resume": _resume(rows),
        "run_audit": _run_audit(rows, run_root, trace_events_by_task, reports_by_task),
        "usage": _usage(rows, reports_by_task),
    }


def parse_trace_events(path: Path | str) -> list[dict[str, Any]]:
    result = benchmark_artifacts.read_trace(Path(path))
    return result.events if result.parse_valid else []


def validate_report_schema(report: dict[str, Any] | None) -> bool:
    if not isinstance(report, dict) or not report:
        return False
    required = {
        "schema_version",
        "run_id",
        "status",
        "stop_reason",
        "tool_steps",
        "attempts",
        "runtime",
        "usage",
        "metrics",
    }
    return required.issubset(report)


def _harness_regression(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    passed = sum(1 for row in rows if row.get("passed"))
    within_budget = sum(1 for row in rows if row.get("within_budget"))
    verifier_passes = sum(1 for row in rows if row.get("verifier_passed"))
    category_counts: dict[str, dict[str, Any]] = {}
    failure_counts: Counter[str] = Counter()
    for row in rows:
        category = str(row.get("category") or "uncategorized")
        bucket = category_counts.setdefault(category, {"total": 0, "passed": 0, "failed": 0, "pass_rate": 0.0})
        bucket["total"] += 1
        if row.get("passed"):
            bucket["passed"] += 1
        else:
            bucket["failed"] += 1
            failure_counts[str(row.get("failure_category") or "unknown")] += 1
    for bucket in category_counts.values():
        bucket["pass_rate"] = _safe_rate(int(bucket["passed"]), int(bucket["total"]))

    return {
        "task_count": total,
        "pass_count": passed,
        "fail_count": total - passed,
        "pass_rate": _safe_rate(passed, total),
        "within_budget_count": within_budget,
        "within_budget_rate": _safe_rate(within_budget, total),
        "verifier_pass_count": verifier_passes,
        "verifier_pass_rate": _safe_rate(verifier_passes, total),
        "category_counts": dict(sorted(category_counts.items())),
        "failure_category_counts": dict(sorted(failure_counts.items())),
    }


def _tool_control(rows: list[dict[str, Any]], reports_by_task: dict[str, dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    tool_counts: Counter[str] = Counter()
    tool_error_count = 0
    tool_error_task_count = 0
    runtime_error_count = 0
    runtime_error_task_count = 0
    approval_request_count = 0
    tool_steps = []
    attempts = []

    for row in rows:
        report_metrics = _report_metrics(reports_by_task.get(str(row.get("id") or ""), {}))
        tool_counts.update({str(name): int(count or 0) for name, count in report_metrics.get("tool_name_counts", {}).items()})
        row_tool_errors = int(report_metrics.get("tool_error_count", 0) or 0)
        row_runtime_errors = int(report_metrics.get("runtime_error_count", 0) or 0)
        tool_error_count += row_tool_errors
        runtime_error_count += row_runtime_errors
        if row_tool_errors:
            tool_error_task_count += 1
        if row_runtime_errors:
            runtime_error_task_count += 1
        approval_request_count += int(report_metrics.get("approval_request_count", 0) or 0)
        tool_steps.append(int(row.get("tool_steps", 0) or 0))
        attempts.append(int(row.get("attempts", 0) or 0))

    allowed_checked = sum(1 for row in rows if row.get("allowed_tools") is not None)
    allowed_respected = sum(1 for row in rows if row.get("allowed_tools") is not None and row.get("allowed_tools_respected"))
    allowed_enforced = sum(1 for row in rows if row.get("allowed_tools") is not None and row.get("allowed_tools_enforced", True))
    disallowed_request_count = sum(len(row.get("disallowed_tool_requests") or []) for row in rows)
    disallowed_execution_count = sum(len(row.get("disallowed_tool_executions") or []) for row in rows)
    boundary_rows = [row for row in rows if row.get("category") == "tool-boundary"]
    boundary_passes = sum(1 for row in boundary_rows if row.get("passed"))
    return {
        "task_count": total,
        "allowed_tools_checked_count": allowed_checked,
        "allowed_tools_respected_count": allowed_respected,
        "allowed_tools_respected_rate": _safe_rate(allowed_respected, allowed_checked),
        "allowed_tools_enforced_count": allowed_enforced,
        "allowed_tools_enforced_rate": _safe_rate(allowed_enforced, allowed_checked),
        "disallowed_tool_request_task_count": allowed_checked - allowed_respected,
        "disallowed_tool_request_count": disallowed_request_count,
        "disallowed_tool_execution_task_count": allowed_checked - allowed_enforced,
        "disallowed_tool_execution_count": disallowed_execution_count,
        "avg_tool_steps": _mean(tool_steps),
        "max_tool_steps": max(tool_steps) if tool_steps else 0,
        "avg_attempts": _mean(attempts),
        "tool_name_counts": dict(sorted(tool_counts.items())),
        "tool_error_count": tool_error_count,
        "tool_error_task_count": tool_error_task_count,
        "tool_error_task_rate": _safe_rate(tool_error_task_count, total),
        "runtime_error_count": runtime_error_count,
        "runtime_error_task_count": runtime_error_task_count,
        "runtime_error_task_rate": _safe_rate(runtime_error_task_count, total),
        "approval_request_count": approval_request_count,
        "tool_boundary_task_count": len(boundary_rows),
        "tool_boundary_pass_count": boundary_passes,
        "tool_boundary_pass_rate": _safe_rate(boundary_passes, len(boundary_rows)),
    }


def _recovery(rows: list[dict[str, Any]], reports_by_task: dict[str, dict[str, Any]]) -> dict[str, Any]:
    primary_rows = [row for row in rows if row.get("category") == "recovery"]
    capability_rows = [
        row for row in rows
        if row.get("category") == "recovery" or "recovery" in _row_tags(row)
    ]
    primary_total = len(primary_rows)
    capability_total = len(capability_rows)
    primary_passes = sum(1 for row in primary_rows if row.get("passed"))
    capability_passes = sum(1 for row in capability_rows if row.get("passed"))
    recovery_within_budget = sum(1 for row in capability_rows if row.get("within_budget"))
    recovery_tool_error_rows = [
        row
        for row in capability_rows
        if int(_report_metrics(reports_by_task.get(str(row.get("id") or ""), {})).get("tool_error_count", 0) or 0) > 0
    ]
    recovery_tool_error_passes = sum(1 for row in recovery_tool_error_rows if row.get("passed"))
    failed_or_interrupted = sum(
        1
        for row in rows
        if str(reports_by_task.get(str(row.get("id") or ""), {}).get("status") or row.get("status") or "") in {"failed", "stopped", "fail"}
        or str(row.get("stop_reason") or "") in {"error", "aborted", "interrupted"}
    )
    return {
        "recovery_primary_task_count": primary_total,
        "recovery_primary_pass_count": primary_passes,
        "recovery_primary_pass_rate": _safe_rate(primary_passes, primary_total),
        "recovery_capability_task_count": capability_total,
        "recovery_capability_pass_count": capability_passes,
        "recovery_capability_pass_rate": _safe_rate(capability_passes, capability_total),
        "recovery_within_budget_count": recovery_within_budget,
        "recovery_within_budget_rate": _safe_rate(recovery_within_budget, capability_total),
        "recovery_avg_tool_steps": _mean(int(row.get("tool_steps", 0) or 0) for row in capability_rows),
        "recovery_tool_error_task_count": len(recovery_tool_error_rows),
        "recovery_after_tool_error_pass_count": recovery_tool_error_passes,
        "recovery_after_tool_error_pass_rate": _safe_rate(recovery_tool_error_passes, len(recovery_tool_error_rows)),
        "bad_stop_reason_count": sum(1 for row in rows if not row.get("non_failure_stop_reason")),
        "failed_or_interrupted_run_count": failed_or_interrupted,
    }


def _context_governance(
    rows: list[dict[str, Any]],
    trace_events_by_task: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    large_result_persist_count = 0
    large_result_tasks: set[str] = set()
    tool_history_snip_count = 0
    tool_history_snip_tasks: set[str] = set()
    context_compact_count = 0
    context_compact_tasks: set[str] = set()
    context_prepared_tasks: set[str] = set()

    for task_id, events in trace_events_by_task.items():
        for event in events:
            if _is_large_result_persist_event(event):
                large_result_persist_count += 1
                large_result_tasks.add(task_id)
            if _is_context_governance_event(event):
                context_prepared_tasks.add(task_id)
                reason = _event_reason(event)
                if reason == "tool_history_snip":
                    tool_history_snip_count += 1
                    tool_history_snip_tasks.add(task_id)
                elif reason == "context_compact":
                    context_compact_count += 1
                    context_compact_tasks.add(task_id)
            elif str(event.get("event") or "") == "context_compacted":
                context_compact_count += 1
                context_compact_tasks.add(task_id)
                context_prepared_tasks.add(task_id)

    large_file_rows = [row for row in rows if "large-file" in set(row.get("tags") or [])]
    large_file_passes = sum(1 for row in large_file_rows if row.get("passed"))
    context_stress_rows = [
        row for row in rows
        if "context-stress" in _row_tags(row) or "large-file" in _row_tags(row)
    ]
    current_request_preserved = sum(1 for row in context_stress_rows if row.get("passed"))
    large_result_persist_observed = large_result_persist_count > 0
    tool_history_snip_observed = tool_history_snip_count > 0
    context_compact_observed = context_compact_count > 0
    snip_expected_rows = [row for row in rows if "tool-history-snip" in _row_tags(row)]
    snip_passes = sum(1 for row in snip_expected_rows if row.get("passed"))
    compact_expected_rows = [row for row in rows if "context-compact" in _row_tags(row)]
    compact_passes = sum(1 for row in compact_expected_rows if row.get("passed"))
    return {
        "large_result_persist_count": large_result_persist_count,
        "large_result_persist_task_count": len(large_result_tasks),
        "large_result_persist_observed": large_result_persist_observed,
        "large_result_persist_coverage": "covered" if large_result_persist_observed else "not_triggered",
        "tool_history_snip_count": tool_history_snip_count,
        "tool_history_snip_task_count": len(tool_history_snip_tasks),
        "tool_history_snip_observed": tool_history_snip_observed,
        "tool_history_snip_coverage": "covered" if tool_history_snip_observed else "not_triggered",
        "tool_history_snip_expected_task_count": len(snip_expected_rows),
        "tool_history_snip_expected_pass_count": snip_passes,
        "tool_history_snip_expected_pass_rate": _safe_rate(snip_passes, len(snip_expected_rows)),
        "context_compact_count": context_compact_count,
        "context_compact_task_count": len(context_compact_tasks),
        "context_compact_observed": context_compact_observed,
        "context_compact_coverage": "covered" if context_compact_observed else "not_triggered",
        "context_compact_expected_task_count": len(compact_expected_rows),
        "context_compact_expected_pass_count": compact_passes,
        "context_compact_expected_pass_rate": _safe_rate(compact_passes, len(compact_expected_rows)),
        "context_prepared_task_count": len(context_prepared_tasks),
        "large_file_task_count": len(large_file_rows),
        "large_file_task_pass_count": large_file_passes,
        "large_file_task_pass_rate": _safe_rate(large_file_passes, len(large_file_rows)),
        "context_stress_task_count": len(context_stress_rows),
        "current_request_preserved_count": current_request_preserved,
        "current_request_preserved_rate": _safe_rate(current_request_preserved, len(context_stress_rows)),
    }


def _security(rows: list[dict[str, Any]]) -> dict[str, Any]:
    security_rows = [row for row in rows if row.get("security_case") or "security" in set(row.get("tags") or [])]
    event_counts: Counter[str] = Counter()
    error_code_counts: Counter[str] = Counter()
    scenario_counts: Counter[str] = Counter()

    for row in security_rows:
        scenario = str(row.get("security_case") or "unclassified")
        scenario_counts[scenario] += 1
        event = str(row.get("security_event_type") or "")
        if event:
            event_counts[event] += 1
        for code in row.get("tool_error_codes") or []:
            error_code_counts[str(code)] += 1

    passed = sum(1 for row in security_rows if row.get("passed"))
    observed = sum(1 for row in security_rows if str(row.get("security_event_type") or "") not in {"", "not_observed"})
    return {
        "scenario_count": len(scenario_counts),
        "task_count": len(security_rows),
        "pass_count": passed,
        "pass_rate": _safe_rate(passed, len(security_rows)),
        "security_event_observed_count": observed,
        "security_event_observed_rate": _safe_rate(observed, len(security_rows)),
        "security_event_counts": dict(sorted(event_counts.items())),
        "tool_error_code_counts": dict(sorted(error_code_counts.items())),
        "security_scenario_counts": dict(sorted(scenario_counts.items())),
    }


def _memory(rows: list[dict[str, Any]]) -> dict[str, Any]:
    memory_rows = [row for row in rows if row.get("memory_task") or "memory" in _row_tags(row)]
    fact_rows = [row for row in memory_rows if _memory_case(row) == "fact_lookup"]
    edit_rows = [row for row in memory_rows if _memory_case(row) == "edit_dependency"]
    conflict_rows = [row for row in memory_rows if _memory_case(row) == "conflict_guard"]
    fallback_rows = [
        row for row in fact_rows + edit_rows
        if str(row.get("memory_fallback_source_path") or row.get("memory_source_path") or "")
    ]
    passed = sum(1 for row in memory_rows if row.get("passed"))
    fact_hits = sum(1 for row in fact_rows if _memory_fact_hit(row))
    edit_successes = sum(1 for row in edit_rows if _memory_edit_dependency_success(row))
    conflict_guards = sum(1 for row in conflict_rows if _memory_conflict_guard_passed(row))
    fallback_read_tasks = sum(1 for row in fallback_rows if _memory_fallback_read_count(row) > 0)
    fallback_read_count = sum(_memory_fallback_read_count(row) for row in fallback_rows)
    categories: Counter[str] = Counter()
    for row in memory_rows:
        for tag in row.get("tags") or []:
            if str(tag).startswith("memory-"):
                categories[str(tag).removeprefix("memory-")] += 1
    return {
        "memory_task_count": len(memory_rows),
        "memory_pass_count": passed,
        "memory_pass_rate": _safe_rate(passed, len(memory_rows)),
        "memory_fact_case_count": len(fact_rows),
        "memory_fact_hit_count": fact_hits,
        "memory_fact_hit_rate": _safe_rate(fact_hits, len(fact_rows)),
        "memory_edit_dependency_case_count": len(edit_rows),
        "memory_edit_dependency_success_count": edit_successes,
        "memory_edit_dependency_success_rate": _safe_rate(edit_successes, len(edit_rows)),
        "memory_conflict_case_count": len(conflict_rows),
        "memory_conflict_guard_count": conflict_guards,
        "memory_conflict_guard_rate": _safe_rate(conflict_guards, len(conflict_rows)),
        "memory_fallback_applicable_count": len(fallback_rows),
        "memory_fallback_read_task_count": fallback_read_tasks,
        "memory_fallback_read_rate": _safe_rate(fallback_read_tasks, len(fallback_rows)),
        "memory_fallback_read_count": fallback_read_count,
        "memory_category_counts": dict(sorted(categories.items())),
    }


def _run_audit(
    rows: list[dict[str, Any]],
    run_root: Path,
    trace_events_by_task: dict[str, list[dict[str, Any]]],
    reports_by_task: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    total = len(rows)
    report_exists = sum(1 for row in rows if row.get("report_exists") or _task_artifact_path(run_root, row, "report.json").exists())
    report_parse_valid = sum(1 for row in rows if row.get("report_parse_valid"))
    trace_exists = sum(1 for row in rows if row.get("trace_exists") or _task_artifact_path(run_root, row, "trace.jsonl").exists())
    trace_parse_valid = sum(1 for row in rows if row.get("trace_parse_valid"))
    trace_contract_met = sum(1 for row in rows if row.get("trace_contract_met"))
    patch_diff_exists = sum(1 for row in rows if _task_artifact_path(run_root, row, "patch.diff").exists())
    report_schema_valid = sum(1 for row in rows if validate_report_schema(reports_by_task.get(str(row.get("id") or ""), {})))
    run_state_available = sum(1 for row in rows if _report_has_run_state(reports_by_task.get(str(row.get("id") or ""), {})))
    trace_event_counts: Counter[str] = Counter()
    has_started = 0
    has_finished = 0
    has_tool_events = 0

    for events in trace_events_by_task.values():
        event_names = {str(event.get("event") or "") for event in events}
        trace_event_counts.update(str(event.get("event") or "") for event in events if event.get("event"))
        if "run_started" in event_names:
            has_started += 1
        if "run_finished" in event_names:
            has_finished += 1
        if {"tool_started", "tool_executed"} & event_names:
            has_tool_events += 1

    artifact_complete = sum(
        1
        for row in rows
        if (row.get("report_exists") or _task_artifact_path(run_root, row, "report.json").exists())
        and (row.get("trace_exists") or _task_artifact_path(run_root, row, "trace.jsonl").exists())
        and _task_artifact_path(run_root, row, "patch.diff").exists()
    )
    return {
        "task_count": total,
        "report_exists_count": report_exists,
        "report_exists_rate": _safe_rate(report_exists, total),
        "report_parse_valid_count": report_parse_valid,
        "report_parse_valid_rate": _safe_rate(report_parse_valid, total),
        "trace_exists_count": trace_exists,
        "trace_exists_rate": _safe_rate(trace_exists, total),
        "trace_parse_valid_count": trace_parse_valid,
        "trace_parse_valid_rate": _safe_rate(trace_parse_valid, total),
        "trace_contract_met_count": trace_contract_met,
        "trace_contract_met_rate": _safe_rate(trace_contract_met, total),
        "patch_diff_exists_count": patch_diff_exists,
        "patch_diff_exists_rate": _safe_rate(patch_diff_exists, total),
        "trace_has_run_started_count": has_started,
        "trace_has_run_started_rate": _safe_rate(has_started, total),
        "trace_has_run_finished_count": has_finished,
        "trace_has_run_finished_rate": _safe_rate(has_finished, total),
        "trace_has_tool_events_count": has_tool_events,
        "trace_has_tool_events_rate": _safe_rate(has_tool_events, total),
        "report_schema_valid_count": report_schema_valid,
        "report_schema_valid_rate": _safe_rate(report_schema_valid, total),
        "run_state_available_count": run_state_available,
        "run_state_available_rate": _safe_rate(run_state_available, total),
        "artifact_complete_count": artifact_complete,
        "artifact_complete_rate": _safe_rate(artifact_complete, total),
        "trace_event_counts": dict(sorted(trace_event_counts.items())),
    }


def _memory_case(row: dict[str, Any]) -> str:
    case = str(row.get("memory_case") or "").strip()
    if case:
        return case
    tags = _row_tags(row)
    if "memory-fact_lookup" in tags:
        return "fact_lookup"
    if "memory-edit_dependency" in tags:
        return "edit_dependency"
    if "memory-irrelevant" in tags or "memory-history_reference" in tags:
        return "conflict_guard"
    return ""


def _memory_fallback_read_count(row: dict[str, Any]) -> int:
    if "memory_fallback_read_count" in row:
        return int(row.get("memory_fallback_read_count", 0) or 0)
    return int(row.get("memory_source_read_count", 0) or 0)


def _memory_fact_hit(row: dict[str, Any]) -> bool:
    if "memory_fact_hit" in row:
        return bool(row.get("memory_fact_hit"))
    return _memory_case(row) == "fact_lookup" and bool(row.get("passed")) and _memory_fallback_read_count(row) == 0


def _memory_edit_dependency_success(row: dict[str, Any]) -> bool:
    if "memory_edit_dependency_success" in row:
        return bool(row.get("memory_edit_dependency_success"))
    return _memory_case(row) == "edit_dependency" and bool(row.get("passed")) and _memory_fallback_read_count(row) == 0


def _memory_conflict_guard_passed(row: dict[str, Any]) -> bool:
    if "memory_conflict_guard_passed" in row:
        return bool(row.get("memory_conflict_guard_passed"))
    return _memory_case(row) == "conflict_guard" and bool(row.get("passed"))


def _usage(rows: list[dict[str, Any]], reports_by_task: dict[str, dict[str, Any]]) -> dict[str, Any]:
    input_tokens = []
    output_tokens = []
    cache_hit_tokens = []
    cache_miss_tokens = []
    costs = []
    for row in rows:
        report = reports_by_task.get(str(row.get("id") or ""), {})
        usage = report.get("usage") or {}
        input_tokens.append(int(usage.get("input_tokens", 0) or 0))
        output_tokens.append(int(usage.get("output_tokens", 0) or 0))
        cache_hit_tokens.append(int(usage.get("input_cache_hit_tokens", 0) or 0))
        cache_miss_tokens.append(int(usage.get("input_cache_miss_tokens", 0) or 0))
        costs.append(float(usage.get("estimated_cost_usd", 0.0) or 0.0))
    total_input = sum(input_tokens)
    total_output = sum(output_tokens)
    return {
        "task_count": len(rows),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "avg_input_tokens": _mean(input_tokens),
        "avg_output_tokens": _mean(output_tokens),
        "input_cache_hit_tokens": sum(cache_hit_tokens),
        "input_cache_miss_tokens": sum(cache_miss_tokens),
        "total_estimated_cost_usd": sum(costs),
        "avg_estimated_cost_usd": _mean(costs),
        "max_estimated_cost_usd": max(costs) if costs else 0.0,
    }


def _task_artifact_path(run_root: Path, row: dict[str, Any], filename: str) -> Path:
    artifact_dir = str(row.get("artifact_dir_relpath") or "")
    if artifact_dir:
        return run_root / artifact_dir / filename
    task_id = str(row.get("id") or "")
    return run_root / "tasks" / task_id / filename


def _row_tags(row: dict[str, Any]) -> set[str]:
    tags = row.get("tags") or []
    return {str(tag) for tag in tags}


def _report_for_row(run_root: Path, row: dict[str, Any]) -> dict[str, Any]:
    report = benchmark_artifacts.read_json_optional(_task_artifact_path(run_root, row, "report.json"))
    if report:
        return report
    embedded = row.get("report_summary") or row.get("report") or {}
    return embedded if isinstance(embedded, dict) else {}


def _report_has_run_state(report: dict[str, Any]) -> bool:
    required = {"run_id", "task_id", "status", "stop_reason", "tool_steps", "attempts"}
    return bool(required.issubset(report))


def _resume(rows: list[dict[str, Any]]) -> dict[str, Any]:
    resume_rows = [
        row for row in rows
        if row.get("scenario") == "resume" or "resume" in _row_tags(row)
    ]
    checkpoint_rows = [
        row for row in resume_rows
        if row.get("resume_is_checkpoint_case") or row.get("recovery_case_category") == "checkpoint_resume"
    ]
    orphan_rows = [
        row for row in resume_rows
        if row.get("resume_is_orphan_case") or row.get("recovery_case_category") == "orphaned_tool_call"
    ]
    total = len(resume_rows)
    passed = sum(1 for row in resume_rows if row.get("passed"))
    interrupted_marked = sum(1 for row in resume_rows if row.get("resume_interrupted_marked"))
    checkpoint_observed = sum(1 for row in checkpoint_rows if row.get("checkpoint_resume_restore_observed"))
    checkpoint_successes = sum(
        1
        for row in checkpoint_rows
        if row.get("passed") and row.get("resume_contract_met") and (row.get("resume_is_checkpoint_case") or row.get("recovery_case_category") == "checkpoint_resume")
    )
    repaired = sum(1 for row in orphan_rows if row.get("resume_orphan_repaired"))
    return {
        "resume_scenario_count": total,
        "resume_success_count": passed,
        "resume_success_rate": _safe_rate(passed, total),
        "checkpoint_resume_case_count": len(checkpoint_rows),
        "checkpoint_resume_observed_count": checkpoint_observed,
        "checkpoint_resume_observed_rate": _safe_rate(checkpoint_observed, len(checkpoint_rows)),
        "checkpoint_resume_success_count": checkpoint_successes,
        "checkpoint_resume_success_rate": _safe_rate(checkpoint_successes, len(checkpoint_rows)),
        "interrupted_run_marked_count": interrupted_marked,
        "interrupted_run_marked_rate": _safe_rate(interrupted_marked, total),
        "orphaned_tool_call_case_count": len(orphan_rows),
        "orphaned_tool_call_repaired_count": repaired,
        "orphaned_tool_call_repaired_rate": _safe_rate(repaired, len(orphan_rows)),
    }


def _report_metrics(report: dict[str, Any]) -> dict[str, Any]:
    metrics = report.get("metrics") or {}
    return metrics if isinstance(metrics, dict) else {}


def _is_large_result_persist_event(event: dict[str, Any]) -> bool:
    if str(event.get("event") or "") != "tool_executed":
        return False
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return bool(metadata.get("persisted"))


def _is_context_prepared_event(event: dict[str, Any]) -> bool:
    name = str(event.get("event") or "")
    return name in {"context_prepared", "context_preparation"}


def _is_context_governance_event(event: dict[str, Any]) -> bool:
    name = str(event.get("event") or "")
    if name == "conversation_committed":
        return _event_reason(event) in {"tool_history_snip", "context_compact"}
    return _is_context_prepared_event(event)


def _event_reason(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return str(event.get("reason") or payload.get("reason") or "")


def _safe_rate(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _mean(values) -> float:
    values = list(values)
    return (sum(values) / len(values)) if values else 0.0
