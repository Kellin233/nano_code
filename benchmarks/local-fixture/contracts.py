"""Specialty capability contracts for local fixture benchmark rows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import artifacts


def resume_session_id(task: dict[str, Any]) -> str:
    return str(task.get("resume_session_id") or f"bench_{safe_name(str(task['id']))}")


def resume_interrupted_run_id(task: dict[str, Any]) -> str:
    return str(task.get("resume_interrupted_run_id") or f"run_seed_{safe_name(str(task['id']))}")


def resume_case(task: dict[str, Any]) -> str:
    if task.get("recovery_case"):
        return str(task["recovery_case"])
    if task.get("resume_orphaned_tool_call"):
        return "orphaned_tool_call"
    return ""


def memory_case(task: dict[str, Any]) -> str:
    case = str(task.get("memory_case") or "").strip()
    if case:
        return case
    tags = set(task.get("tags") or [])
    if "memory-fact_lookup" in tags:
        return "fact_lookup"
    if "memory-edit_dependency" in tags:
        return "edit_dependency"
    if "memory-irrelevant" in tags:
        return "conflict_guard"
    return ""


def resume_case_category(case: str) -> str:
    if case.startswith("checkpoint_resume"):
        return "checkpoint_resume"
    if case == "orphaned_tool_call":
        return "orphaned_tool_call"
    return "resume"


def resume_expected_status(case: str) -> str:
    category = resume_case_category(case)
    if category == "checkpoint_resume":
        return "resume_checkpoint"
    if category == "orphaned_tool_call":
        return "repair_orphaned_tool_call"
    return "resume"


def resume_observed_status(
    *,
    output: str,
    session_exists: bool,
    orphan_repaired: bool,
    returncode: int,
) -> str:
    if orphan_repaired:
        return "orphaned_tool_call_repaired"
    if resume_output_restored(output):
        return "session_restored"
    if "no previous sessions found" in output.lower() or not session_exists:
        return "no_checkpoint"
    if returncode != 0:
        return "runtime_error"
    return "not_observed"


def resume_output_restored(output: str) -> bool:
    return "session restored" in output.lower()


def context_expectations(task: dict[str, Any]) -> dict[str, bool]:
    tags = set(task.get("tags") or [])
    return {
        "large_result_persist": "tool-result-budget" in tags or "large-result" in tags,
        "tool_history_snip": "tool-history-snip" in tags,
        "context_compact": "context-compact" in tags,
    }


def context_contract_expected(task: dict[str, Any]) -> bool:
    return any(context_expectations(task).values())


def evaluate_contracts(
    *,
    task: dict[str, Any],
    trace_path: Path | None,
    verifier_returncode: int,
    session_exists: bool,
    resume_interrupted_marked: bool,
    resume_orphan_repaired: bool,
    resume_output: str,
    nanocode_returncode: int,
) -> dict[str, Any]:
    recovery_case = resume_case(task)
    recovery_case_category = resume_case_category(recovery_case)
    output_restored = resume_output_restored(resume_output)
    observed_status = resume_observed_status(
        output=resume_output,
        session_exists=session_exists,
        orphan_repaired=resume_orphan_repaired,
        returncode=nanocode_returncode,
    )

    tool_error_codes = artifacts.trace_tool_error_codes(trace_path)
    security_match = security_expectation_match(task, trace_path, tool_error_codes)
    security_observed_event = str(security_match["security_matched_event_type"] or "")
    if str(task.get("security_case") or "") and not security_observed_event:
        security_observed_event = "not_observed"
    security_ok = _security_contract_met(
        security_case=str(task.get("security_case") or ""),
        expectation_configured=bool(security_match["security_expectation_configured"]),
        matched_tool_call=bool(security_match["security_matched_tool_call"]),
    )

    memory_fields = _memory_fields(
        task=task,
        trace_path=trace_path,
        verifier_returncode=verifier_returncode,
    )
    memory_ok = _memory_contract_met(**memory_fields)

    resume_ok = _resume_contract_met(
        scenario=str(task.get("scenario") or "default"),
        case_category=recovery_case_category,
        session_exists=session_exists,
        interrupted_marked=resume_interrupted_marked,
        orphan_repaired=resume_orphan_repaired,
        output_restored=output_restored,
    )

    context_counts = artifacts.trace_context_governance_counts(trace_path)
    large_result_persist_count = context_counts["large_result_persist"]
    tool_history_snip_count = context_counts["tool_history_snip"]
    context_compact_count = context_counts["context_compact"]
    context_expectation = context_expectations(task)
    context_expected = any(context_expectation.values())
    context_ok = _context_contract_met(
        expected_large_result_persist=context_expectation["large_result_persist"],
        large_result_persist_observed=large_result_persist_count > 0,
        expected_tool_history_snip=context_expectation["tool_history_snip"],
        tool_history_snip_observed=tool_history_snip_count > 0,
        expected_context_compact=context_expectation["context_compact"],
        context_compact_observed=context_compact_count > 0,
    )
    tool_path_limit_fields = _tool_path_limit_fields(task=task, trace_path=trace_path)
    tool_path_limit_ok = bool(tool_path_limit_fields["tool_path_limit_contract_met"])

    specialty_checks = {
        "security": security_ok,
        "memory": memory_ok,
        "resume": resume_ok,
        "context": context_ok,
        "tool_path_limit": tool_path_limit_ok,
    }
    specialty_failure_category = ""
    for name, ok in specialty_checks.items():
        if not ok:
            specialty_failure_category = f"{name}_contract_failed"
            break

    checkpoint_resume_restore_observed = bool(
        recovery_case_category == "checkpoint_resume"
        and verifier_returncode == 0
        and resume_interrupted_marked
        and output_restored
    )

    return {
        "resume_interrupted_marked": resume_interrupted_marked,
        "resume_orphan_repaired": resume_orphan_repaired,
        "recovery_case": recovery_case,
        "recovery_case_category": recovery_case_category,
        "resume_is_orphan_case": recovery_case_category == "orphaned_tool_call",
        "resume_is_checkpoint_case": recovery_case_category == "checkpoint_resume",
        "resume_expected_status": resume_expected_status(recovery_case),
        "resume_observed_status": observed_status,
        "resume_output_restored": output_restored,
        "resume_session_exists": session_exists,
        "checkpoint_resume_restore_observed": checkpoint_resume_restore_observed,
        "resume_contract_met": resume_ok,
        "security_case": str(task.get("security_case") or ""),
        "security_event_type": security_observed_event,
        "security_contract_met": security_ok,
        **security_match,
        "tool_error_codes": tool_error_codes,
        **memory_fields,
        "memory_contract_met": memory_ok,
        "context_contract_expected": context_expected,
        "context_expected_large_result_persist": context_expectation["large_result_persist"],
        "context_expected_tool_history_snip": context_expectation["tool_history_snip"],
        "context_expected_context_compact": context_expectation["context_compact"],
        "large_result_persist_count": large_result_persist_count,
        "large_result_persist_observed": large_result_persist_count > 0,
        "tool_history_snip_count": tool_history_snip_count,
        "tool_history_snip_observed": tool_history_snip_count > 0,
        "context_compact_count": context_compact_count,
        "context_compact_observed": context_compact_count > 0,
        "context_contract_met": context_ok,
        **tool_path_limit_fields,
        "specialty_contract_met": all(specialty_checks.values()),
        "specialty_failure_category": specialty_failure_category,
    }


def safe_name(value: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "run"


def security_expectation_match(
    task: dict[str, Any],
    trace_path: Path | None,
    tool_error_codes: list[str],
) -> dict[str, Any]:
    expectation = task.get("security_expectation")
    if not isinstance(expectation, dict):
        return {
            "security_expectation_configured": False,
            "security_expected_tool": "",
            "security_expected_input": {},
            "security_expected_error_code": "",
            "security_matched_tool_call": False,
            "security_matched_error_code": "",
            "security_matched_event_type": "",
        }

    expected_event = str(expectation.get("event") or "")
    expected_tool = str(expectation.get("tool") or "")
    expected_input = expectation.get("input") if isinstance(expectation.get("input"), dict) else {}
    expected_error_code = str(expectation.get("error_code") or _default_security_error_code(expected_event))
    events = artifacts.read_jsonl_optional(trace_path) if trace_path is not None else []
    started = []
    for index, event in enumerate(events):
        if str(event.get("event") or "") != "tool_started":
            continue
        if expected_tool and artifacts.event_tool_name(event) != expected_tool:
            continue
        if expected_input and not _object_matches_expected_input(event, expected_input):
            continue
        started.append((index, artifacts.event_call_id(event)))

    started_ids = {call_id for _, call_id in started if call_id}
    matched_error_code = ""
    matched_event_type = ""
    matched = False
    for index, event in enumerate(events):
        if str(event.get("event") or "") != "tool_executed":
            continue
        if expected_tool and artifacts.event_tool_name(event) != expected_tool:
            continue
        if not artifacts.event_is_error(event):
            continue
        error_code = _event_error_code(event)
        if expected_error_code and error_code != expected_error_code:
            continue
        if _security_error_matches_start(
            error_event=event,
            error_index=index,
            expected_input=expected_input,
            started=started,
            started_ids=started_ids,
        ):
            matched = True
            matched_error_code = error_code
            matched_event_type = expected_event
            break

    if not matched and not expected_tool and not expected_input and expected_error_code in set(tool_error_codes):
        matched = True
        matched_error_code = expected_error_code
        matched_event_type = expected_event

    return {
        "security_expectation_configured": True,
        "security_expected_tool": expected_tool,
        "security_expected_input": expected_input,
        "security_expected_error_code": expected_error_code,
        "security_matched_tool_call": matched,
        "security_matched_error_code": matched_error_code,
        "security_matched_event_type": matched_event_type,
    }


def _memory_fields(
    *,
    task: dict[str, Any],
    trace_path: Path | None,
    verifier_returncode: int,
) -> dict[str, Any]:
    source_path = str(task.get("memory_source_path") or "")
    source_read_count = artifacts.trace_tool_path_count(trace_path, "read_file", source_path) if source_path else 0
    is_memory_task = bool(task.get("memory_setup") or "memory" in set(task.get("tags") or []))
    case = memory_case(task)
    fallback_applicable = case in {"fact_lookup", "edit_dependency"}
    fallback_source_path = str(task.get("memory_fallback_source_path") or "")
    if not fallback_source_path and fallback_applicable:
        fallback_source_path = source_path
    fallback_read_count = (
        artifacts.trace_tool_path_count(trace_path, "read_file", fallback_source_path)
        if fallback_source_path
        else 0
    )
    fallback_read = fallback_read_count > 0
    current_truth_path = source_path if case == "conflict_guard" else ""
    current_truth_read_count = (
        artifacts.trace_tool_path_count(trace_path, "read_file", current_truth_path)
        if current_truth_path
        else 0
    )
    current_truth_read = current_truth_read_count > 0
    fact_hit = bool(case == "fact_lookup" and verifier_returncode == 0 and not fallback_read)
    edit_dependency_success = bool(case == "edit_dependency" and verifier_returncode == 0 and not fallback_read)
    conflict_guard_passed = bool(case == "conflict_guard" and verifier_returncode == 0 and current_truth_read)
    return {
        "memory_task": is_memory_task,
        "memory_case": case,
        "memory_source_path": source_path,
        "memory_source_read_count": source_read_count,
        "memory_current_truth_path": current_truth_path,
        "memory_current_truth_read_count": current_truth_read_count,
        "memory_current_truth_read": current_truth_read,
        "memory_fallback_source_path": fallback_source_path,
        "memory_fallback_read_count": fallback_read_count,
        "memory_fallback_read": fallback_read,
        "memory_fact_hit": fact_hit,
        "memory_edit_dependency_success": edit_dependency_success,
        "memory_conflict_guard_passed": conflict_guard_passed,
    }


def _security_contract_met(
    *,
    security_case: str,
    expectation_configured: bool,
    matched_tool_call: bool,
) -> bool:
    if not security_case:
        return True
    return expectation_configured and matched_tool_call


def _default_security_error_code(expected_event: str) -> str:
    if expected_event == "action_denied":
        return "action_denied"
    return ""


def _security_error_matches_start(
    *,
    error_event: dict[str, Any],
    error_index: int,
    expected_input: dict[str, Any],
    started: list[tuple[int, str]],
    started_ids: set[str],
) -> bool:
    error_call_id = artifacts.event_call_id(error_event)
    if expected_input and _object_matches_expected_input(error_event, expected_input):
        return True
    if error_call_id and error_call_id in started_ids:
        return True
    return any(start_index <= error_index and (not start_call_id or not error_call_id or start_call_id == error_call_id) for start_index, start_call_id in started)


def _event_error_code(event: dict[str, Any]) -> str:
    return artifacts.event_tool_error_code(event)


def _object_matches_expected_input(value: Any, expected: dict[str, Any]) -> bool:
    return all(_object_has_key_value(value, str(key), str(expected_value)) for key, expected_value in expected.items())


def _object_has_key_value(value: Any, wanted_key: str, wanted_value: str) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) == wanted_key and _input_value_matches(str(key), item, wanted_value):
                return True
            if _object_has_key_value(item, wanted_key, wanted_value):
                return True
    if isinstance(value, list):
        return any(_object_has_key_value(item, wanted_key, wanted_value) for item in value)
    return False


def _input_value_matches(key: str, actual: Any, expected: str) -> bool:
    if str(actual) == expected:
        return True
    if not isinstance(actual, str):
        return False
    key_text = key.lower()
    if "path" in key_text or "file" in key_text:
        return artifacts.path_text_matches(actual, {expected})
    return False


def _memory_contract_met(
    *,
    memory_task: bool,
    memory_case: str,
    memory_fact_hit: bool,
    memory_edit_dependency_success: bool,
    memory_conflict_guard_passed: bool,
    **_: Any,
) -> bool:
    if not memory_task:
        return True
    if memory_case == "fact_lookup":
        return memory_fact_hit
    if memory_case == "edit_dependency":
        return memory_edit_dependency_success
    if memory_case == "conflict_guard":
        return memory_conflict_guard_passed
    return False


def _resume_contract_met(
    *,
    scenario: str,
    case_category: str,
    session_exists: bool,
    interrupted_marked: bool,
    orphan_repaired: bool,
    output_restored: bool,
) -> bool:
    if scenario != "resume":
        return True
    if not session_exists or not interrupted_marked or not output_restored:
        return False
    if case_category == "orphaned_tool_call":
        return orphan_repaired
    if case_category == "checkpoint_resume":
        return output_restored
    return True


def _context_contract_met(
    *,
    expected_large_result_persist: bool,
    large_result_persist_observed: bool,
    expected_tool_history_snip: bool,
    tool_history_snip_observed: bool,
    expected_context_compact: bool,
    context_compact_observed: bool,
) -> bool:
    return (
        (not expected_large_result_persist or large_result_persist_observed)
        and (not expected_tool_history_snip or tool_history_snip_observed)
        and (not expected_context_compact or context_compact_observed)
    )


def _tool_path_limit_fields(*, task: dict[str, Any], trace_path: Path | None) -> dict[str, Any]:
    limits = task.get("tool_path_limits")
    if not isinstance(limits, list) or not limits:
        return {
            "tool_path_limit_contract_expected": False,
            "tool_path_limit_contract_met": True,
            "tool_path_limit_counts": [],
            "tool_path_limit_violations": [],
        }

    counts = []
    violations = []
    for raw_limit in limits:
        if not isinstance(raw_limit, dict):
            continue
        tool_name = str(raw_limit.get("tool") or "")
        relpath = str(raw_limit.get("path") or "")
        max_count = _optional_int(raw_limit, "max_count")
        max_pre_edit_count = _optional_int(raw_limit, "max_pre_edit_count")
        max_post_edit_count = _optional_int(raw_limit, "max_post_edit_count")
        phase_counts = artifacts.trace_tool_path_phase_counts(trace_path, tool_name, relpath)
        row = {
            "tool": tool_name,
            "path": relpath,
            "max_count": max_count,
            "max_pre_edit_count": max_pre_edit_count,
            "max_post_edit_count": max_post_edit_count,
            **phase_counts,
        }
        counts.append(row)
        for field, observed_key in [
            ("max_count", "observed_count"),
            ("max_pre_edit_count", "pre_edit_count"),
            ("max_post_edit_count", "post_edit_count"),
        ]:
            allowed = row[field]
            if allowed is not None and row[observed_key] > allowed:
                violations.append({**row, "violation": field})

    return {
        "tool_path_limit_contract_expected": True,
        "tool_path_limit_contract_met": not violations,
        "tool_path_limit_counts": counts,
        "tool_path_limit_violations": violations,
    }


def _optional_int(value: dict[str, Any], key: str) -> int | None:
    if key not in value or value[key] is None:
        return None
    return int(value[key])
