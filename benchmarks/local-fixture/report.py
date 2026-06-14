"""Markdown reports for the local fixture benchmark."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def write_reports(run_root: Path | str, artifact: dict[str, Any]) -> None:
    run_root = Path(run_root)
    (run_root / "benchmark-core-report.md").write_text(render_core_report(artifact), encoding="utf-8")
    (run_root / "DATA_PROVENANCE.md").write_text(render_data_provenance(), encoding="utf-8")


def render_core_report(artifact: dict[str, Any]) -> str:
    summary = artifact.get("summary") or {}
    scorecards = artifact.get("scorecards") or {}
    harness = scorecards.get("harness_regression") or {}
    tool = scorecards.get("tool_control") or {}
    security = scorecards.get("security") or {}
    recovery = scorecards.get("recovery") or {}
    context = scorecards.get("context_governance") or {}
    memory = scorecards.get("memory") or {}
    resume = scorecards.get("resume") or {}
    audit = scorecards.get("run_audit") or {}
    usage = scorecards.get("usage") or {}

    lines = [
        "# NanoCode Local Fixture Benchmark Report",
        "",
        "This report is derived from `benchmark.json` and per-task run artifacts. It does not include ablation experiments.",
        "",
        "## Harness Regression",
        f"- selected_tasks: {_int(summary, 'selected_tasks')}",
        f"- executed_tasks: {_int(summary, 'executed_tasks')}",
        f"- Tasks: {_int(harness, 'task_count')}",
        f"- pass_rate: {_rate(harness, 'pass_rate', 'pass_count', 'task_count')}",
        f"- within_budget_rate: {_rate(harness, 'within_budget_rate', 'within_budget_count', 'task_count')}",
        f"- verifier_pass_rate: {_rate(harness, 'verifier_pass_rate', 'verifier_pass_count', 'task_count')}",
        f"- failure_category_counts: {_dict(harness.get('failure_category_counts'))}",
        "",
        "## Tool Control",
        f"- allowed_tools_request_respected_rate: {_rate(tool, 'allowed_tools_respected_rate', 'allowed_tools_respected_count', 'allowed_tools_checked_count')}",
        f"- allowed_tools_enforced_rate: {_rate(tool, 'allowed_tools_enforced_rate', 'allowed_tools_enforced_count', 'allowed_tools_checked_count')}",
        f"- disallowed_tool_request_count: {_int(tool, 'disallowed_tool_request_count')}",
        f"- disallowed_tool_execution_count: {_int(tool, 'disallowed_tool_execution_count')}",
        f"- avg_tool_steps: {_float(tool.get('avg_tool_steps'))}",
        f"- max_tool_steps: {_int(tool, 'max_tool_steps')}",
        f"- avg_attempts: {_float(tool.get('avg_attempts'))}",
        f"- tool_error_task_rate: {_rate(tool, 'tool_error_task_rate', 'tool_error_task_count', 'task_count')}",
        f"- runtime_error_task_rate: {_rate(tool, 'runtime_error_task_rate', 'runtime_error_task_count', 'task_count')}",
        f"- tool_boundary_pass_rate: {_rate(tool, 'tool_boundary_pass_rate', 'tool_boundary_pass_count', 'tool_boundary_task_count')}",
        f"- tool_name_counts: {_dict(tool.get('tool_name_counts'))}",
        "",
        "## Security",
        f"- scenario_count: {_int(security, 'scenario_count')}",
        f"- security_task_count: {_int(security, 'task_count')}",
        f"- security_pass_rate: {_rate(security, 'pass_rate', 'pass_count', 'task_count')}",
        f"- security_event_observed_rate: {_rate(security, 'security_event_observed_rate', 'security_event_observed_count', 'task_count')}",
        f"- security_event_counts: {_dict(security.get('security_event_counts'))}",
        f"- tool_error_code_counts: {_dict(security.get('tool_error_code_counts'))}",
        "",
        "## Recovery",
        f"- recovery_primary_task_count: {_int(recovery, 'recovery_primary_task_count')}",
        f"- recovery_primary_pass_rate: {_rate(recovery, 'recovery_primary_pass_rate', 'recovery_primary_pass_count', 'recovery_primary_task_count')}",
        f"- recovery_capability_task_count: {_int(recovery, 'recovery_capability_task_count')}",
        f"- recovery_capability_pass_rate: {_rate(recovery, 'recovery_capability_pass_rate', 'recovery_capability_pass_count', 'recovery_capability_task_count')}",
        f"- recovery_within_budget_rate: {_rate(recovery, 'recovery_within_budget_rate', 'recovery_within_budget_count', 'recovery_capability_task_count')}",
        f"- recovery_avg_tool_steps: {_float(recovery.get('recovery_avg_tool_steps'))}",
        f"- recovery_after_tool_error_pass_rate: {_rate(recovery, 'recovery_after_tool_error_pass_rate', 'recovery_after_tool_error_pass_count', 'recovery_tool_error_task_count')}",
        f"- bad_stop_reason_count: {_int(recovery, 'bad_stop_reason_count')}",
        f"- failed_or_interrupted_run_count: {_int(recovery, 'failed_or_interrupted_run_count')}",
        "",
        "## Context Governance",
        f"- large_result_persist_count: {_int(context, 'large_result_persist_count')}",
        f"- large_result_persist_task_count: {_int(context, 'large_result_persist_task_count')}",
        f"- large_result_persist_coverage: {_str(context, 'large_result_persist_coverage')}",
        f"- tool_history_snip_count: {_int(context, 'tool_history_snip_count')}",
        f"- tool_history_snip_coverage: {_str(context, 'tool_history_snip_coverage')}",
        f"- context_compact_count: {_int(context, 'context_compact_count')}",
        f"- context_compact_coverage: {_str(context, 'context_compact_coverage')}",
        f"- large_file_task_pass_rate: {_rate(context, 'large_file_task_pass_rate', 'large_file_task_pass_count', 'large_file_task_count')}",
        f"- current_request_preserved_rate: {_rate(context, 'current_request_preserved_rate', 'current_request_preserved_count', 'context_stress_task_count')}",
        "",
        "## Memory",
        f"- memory_task_count: {_int(memory, 'memory_task_count')}",
        f"- memory_pass_rate: {_rate(memory, 'memory_pass_rate', 'memory_pass_count', 'memory_task_count')}",
        f"- memory_fact_hit_rate: {_rate(memory, 'memory_fact_hit_rate', 'memory_fact_hit_count', 'memory_fact_case_count')}",
        f"- memory_edit_dependency_success_rate: {_rate(memory, 'memory_edit_dependency_success_rate', 'memory_edit_dependency_success_count', 'memory_edit_dependency_case_count')}",
        f"- memory_conflict_guard_rate: {_rate(memory, 'memory_conflict_guard_rate', 'memory_conflict_guard_count', 'memory_conflict_case_count')}",
        f"- memory_fallback_read_rate: {_rate(memory, 'memory_fallback_read_rate', 'memory_fallback_read_task_count', 'memory_fallback_applicable_count')}",
        f"- memory_fallback_read_count: {_int(memory, 'memory_fallback_read_count')}",
        "",
        "## Resume",
        f"- resume_scenario_count: {_int(resume, 'resume_scenario_count')}",
        f"- resume_success_rate: {_rate(resume, 'resume_success_rate', 'resume_success_count', 'resume_scenario_count')}",
        f"- checkpoint_resume_observed_rate: {_rate(resume, 'checkpoint_resume_observed_rate', 'checkpoint_resume_observed_count', 'checkpoint_resume_case_count')}",
        f"- checkpoint_resume_success_rate: {_rate(resume, 'checkpoint_resume_success_rate', 'checkpoint_resume_success_count', 'checkpoint_resume_case_count')}",
        f"- interrupted_run_marked_rate: {_rate(resume, 'interrupted_run_marked_rate', 'interrupted_run_marked_count', 'resume_scenario_count')}",
        f"- orphaned_tool_call_repaired_rate: {_rate(resume, 'orphaned_tool_call_repaired_rate', 'orphaned_tool_call_repaired_count', 'orphaned_tool_call_case_count')}",
        "",
        "## Run Audit",
        f"- report_exists_rate: {_rate(audit, 'report_exists_rate', 'report_exists_count', 'task_count')}",
        f"- report_parse_valid_rate: {_rate(audit, 'report_parse_valid_rate', 'report_parse_valid_count', 'task_count')}",
        f"- trace_exists_rate: {_rate(audit, 'trace_exists_rate', 'trace_exists_count', 'task_count')}",
        f"- trace_parse_valid_rate: {_rate(audit, 'trace_parse_valid_rate', 'trace_parse_valid_count', 'task_count')}",
        f"- trace_contract_met_rate: {_rate(audit, 'trace_contract_met_rate', 'trace_contract_met_count', 'task_count')}",
        f"- report_schema_valid_rate: {_rate(audit, 'report_schema_valid_rate', 'report_schema_valid_count', 'task_count')}",
        f"- run_state_available_rate: {_rate(audit, 'run_state_available_rate', 'run_state_available_count', 'task_count')}",
        f"- artifact_complete_rate: {_rate(audit, 'artifact_complete_rate', 'artifact_complete_count', 'task_count')}",
        f"- trace_event_counts: {_dict(audit.get('trace_event_counts'))}",
        "",
        "## Optional Usage",
        f"- total_tokens: {_int(usage, 'total_tokens')}",
        f"- avg_input_tokens: {_float(usage.get('avg_input_tokens'))}",
        f"- avg_output_tokens: {_float(usage.get('avg_output_tokens'))}",
        f"- total_estimated_cost_usd: ${float(usage.get('total_estimated_cost_usd') or 0.0):.6f}",
        f"- avg_estimated_cost_usd: ${float(usage.get('avg_estimated_cost_usd') or 0.0):.6f}",
        "",
        "## Resume-Safe Claims",
        f"- The benchmark selected {_int(summary, 'selected_tasks')} tasks and executed {_int(harness, 'task_count')} tasks across {_category_count(harness)} categories.",
        f"- Harness pass rate was {_rate_value(harness, 'pass_rate', 'task_count')}; verifier pass rate was {_rate_value(harness, 'verifier_pass_rate', 'task_count')}.",
        f"- Tool allowlist request discipline was {_rate_value(tool, 'allowed_tools_respected_rate', 'allowed_tools_checked_count')}; runtime enforcement was {_rate_value(tool, 'allowed_tools_enforced_rate', 'allowed_tools_checked_count')}.",
        f"- Security event observation rate was {_rate_value(security, 'security_event_observed_rate', 'task_count')}.",
        f"- Memory fact lookup hit rate was {_rate_value(memory, 'memory_fact_hit_rate', 'memory_fact_case_count')}; fallback-read rate was {_rate_value(memory, 'memory_fallback_read_rate', 'memory_fallback_applicable_count')}.",
        f"- Resume success rate was {_rate_value(resume, 'resume_success_rate', 'resume_scenario_count')}.",
        f"- Run artifact completeness was {_rate_value(audit, 'artifact_complete_rate', 'task_count')}.",
        "",
        "## Not Measured",
        "- No context ablation experiment is run in this benchmark pass.",
        "- Memory metrics are single-run fixture observations, not a memory-on/off ablation matrix.",
        "- Durable memory promotion/rejection is not measured because NanoCode does not implement automatic memory promotion policy.",
        "- Workspace drift, tracked-file freshness, checkpoint schema mismatch, and partial-success replay policies are not measured because the current resume implementation does not provide those contracts.",
        "- Path/symlink/search escape and repeated-call prevention are not reported as security benchmark claims in this suite.",
        "- No provider comparison is run in this benchmark pass.",
        "",
    ]
    return "\n".join(lines)


def render_data_provenance() -> str:
    return "\n".join(
        [
            "# NanoCode Local Fixture Data Provenance",
            "",
            "Every metric in `benchmark-core-report.md` is derived from local benchmark artifacts.",
            "",
            "| Metric | Source |",
            "|---|---|",
            "| `pass_rate` | `benchmark.json` rows `passed` |",
            "| `within_budget_rate` | `benchmark.json` rows `within_budget` |",
            "| `verifier_pass_rate` | `benchmark.json` rows `verifier_passed` |",
            "| `failure_category_counts` | `benchmark.json` rows `failure_category` |",
            "| `allowed_tools_request_respected_rate` | `benchmark.json` rows `allowed_tools_respected`; false when the model requested a tool outside task allowlist |",
            "| `allowed_tools_enforced_rate` | `benchmark.json` rows `allowed_tools_enforced`; false only if a disallowed tool actually executed successfully |",
            "| `disallowed_tool_request_count` | per-row `disallowed_tool_requests` derived from `tool_started` trace events |",
            "| `disallowed_tool_execution_count` | per-row `disallowed_tool_executions` derived from successful `tool_executed` trace events |",
            "| `tool_name_counts` | per-task `report.json` field `metrics.tool_name_counts` |",
            "| `tool_error_count` | per-task `report.json` field `metrics.tool_error_count` |",
            "| `runtime_error_count` | per-task `report.json` field `metrics.runtime_error_count` |",
            "| `security_event_counts` | security rows field `security_event_type` after task `security_expectation` matching against per-task `trace.jsonl` |",
            "| `tool_error_code_counts` | security rows field `tool_error_codes` parsed from error `tool_executed` trace entries |",
            "| `security_contract_met` | task `security_expectation` matched against trace `tool_started` input and corresponding error `tool_executed` |",
            "| `recovery_primary_pass_rate` | rows with `category == \"recovery\"` and their `passed` values |",
            "| `recovery_capability_pass_rate` | rows with `category == \"recovery\"` or tag `recovery` and their `passed` values |",
            "| `recovery_after_tool_error_pass_rate` | recovery capability rows whose per-task `report.json.metrics.tool_error_count > 0` |",
            "| `large_result_persist_count` | per-task `trace.jsonl` events where `event == \"tool_executed\"` and `payload.metadata.persisted == true` |",
            "| `large_result_persist_coverage` | `covered` when `large_result_persist_count > 0`; otherwise `not_triggered` |",
            "| `tool_history_snip_count` | per-task `trace.jsonl` context governance events with `reason == \"tool_history_snip\"`, including `conversation_committed` |",
            "| `tool_history_snip_coverage` | `covered` when `tool_history_snip_count > 0`; otherwise `not_triggered` |",
            "| `context_compact_count` | per-task `trace.jsonl` context preparation events with `reason == \"context_compact\"` or `event == \"context_compacted\"` |",
            "| `context_compact_coverage` | `covered` when `context_compact_count > 0`; otherwise `not_triggered` |",
            "| `current_request_preserved_rate` | context-stress rows, currently large-file/context-stress tasks, whose final task result passed |",
            "| `memory_fact_hit_rate` | fact-lookup memory rows whose verifier passed without reading `memory_fallback_source_path` |",
            "| `memory_edit_dependency_success_rate` | edit-dependency memory rows whose verifier passed without reading `memory_fallback_source_path` |",
            "| `memory_conflict_guard_rate` | conflict-guard memory rows whose verifier passed and whose trace read `current_truth.txt` |",
            "| `memory_fallback_read_rate` | fact/edit memory rows with a fallback source path where the trace read that source path |",
            "| `resume_success_rate` | rows with `scenario == \"resume\"` or `tags` containing `resume` and their `passed` values |",
            "| `interrupted_run_marked_rate` | resume rows field `resume_interrupted_marked` |",
            "| `checkpoint_resume_observed_rate` | checkpoint resume rows field `checkpoint_resume_restore_observed`, an observation of restore/interrupted/verifier signals |",
            "| `checkpoint_resume_success_rate` | checkpoint resume rows where `passed`, `resume_contract_met`, and `resume_is_checkpoint_case` are all true |",
            "| `orphaned_tool_call_repaired_rate` | orphaned-tool-call resume rows field `resume_orphan_repaired` |",
            "| `report_exists_rate` | existence of `tasks/<task-id>/report.json` |",
            "| `report_parse_valid_rate` | rows field `report_parse_valid`, true only when selected report JSON parsed as an object |",
            "| `trace_exists_rate` | existence of `tasks/<task-id>/trace.jsonl` |",
            "| `trace_parse_valid_rate` | rows field `trace_parse_valid`, true only when selected trace JSONL parsed fully |",
            "| `trace_contract_met_rate` | rows field `trace_contract_met`; configured tool tasks require parseable trace plus basic run/tool event consistency |",
            "| `report_schema_valid_rate` | required keys in each per-task `report.json` |",
            "| `run_state_available_rate` | required run-state keys in each per-task `report.json` |",
            "| `artifact_complete_rate` | per-task existence of `report.json`, `trace.jsonl`, and `patch.diff` |",
            "| `trace_event_counts` | event counts aggregated from each per-task `trace.jsonl` |",
            "| `total_input_tokens` | per-task `report.json` field `usage.input_tokens` |",
            "| `total_output_tokens` | per-task `report.json` field `usage.output_tokens` |",
            "| `total_estimated_cost_usd` | per-task `report.json` field `usage.estimated_cost_usd` |",
            "",
            "Resume metrics are derived from fixture setup, runtime output, per-task trace, and copied session artifacts. They only cover contracts implemented by NanoCode: session restore, interrupted-run marking, checkpoint completion, and orphaned tool-call repair.",
            "",
        ]
    )


def _rate(mapping: dict[str, Any], rate_key: str, numerator_key: str, denominator_key: str) -> str:
    numerator = _int(mapping, numerator_key)
    denominator = _int(mapping, denominator_key)
    if denominator <= 0:
        return f"N/A ({numerator}/{denominator})"
    return f"{float(mapping.get(rate_key) or 0.0):.2%} ({numerator}/{denominator})"


def _rate_value(mapping: dict[str, Any], rate_key: str, denominator_key: str) -> str:
    if _int(mapping, denominator_key) <= 0:
        return "N/A"
    return f"{float(mapping.get(rate_key) or 0.0):.2%}"


def _float(value: Any) -> str:
    return f"{float(value or 0.0):.2f}"


def _int(mapping: dict[str, Any], key: str) -> int:
    return int(mapping.get(key, 0) or 0)


def _str(mapping: dict[str, Any], key: str) -> str:
    return str(mapping.get(key) or "")


def _dict(value: Any) -> str:
    if not value:
        return "{}"
    return str(dict(value))


def _category_count(harness: dict[str, Any]) -> int:
    category_counts = harness.get("category_counts") or {}
    return len(category_counts) if isinstance(category_counts, dict) else 0
