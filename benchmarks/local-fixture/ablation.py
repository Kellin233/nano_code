#!/usr/bin/env python3
"""Run NanoCode local-fixture ablation experiments.

The main benchmark answers "does the implemented harness contract still work?".
This runner answers narrower "what did a module contribute?" questions without
changing the main pass/fail scoring.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import tempfile
from argparse import Namespace
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BENCH_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BENCH_DIR.parents[1]
SRC_DIR = PROJECT_ROOT / "src"
DEFAULT_OUTPUT_ROOT = BENCH_DIR / "results"

if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import artifacts as benchmark_artifacts

CONTEXT_REQUEST_LEVELS = [
    ("short", "Update final_state.txt with the latest benchmark value."),
    (
        "long",
        "Update final_state.txt with the latest benchmark value, preserve the current request exactly, "
        "and do not rely on stale tool output from earlier turns.",
    ),
]
CONTEXT_SCENARIO_ORDER = (
    "no_compression_baseline",
    "tool_result_budget",
    "tool_history_snip",
    "context_compact",
)
CONTEXT_PROFILE_ORDER = ("baseline_profile", "debugging_profile", "refactor_profile", "incident_review_profile")
CONTEXT_TOOL_RESULT_BUDGET_MIX = (
    ("small_file_read", 5),
    ("medium_file_read", 3),
    ("large_search_output", 2),
    ("ci_log", 2),
)
CONTEXT_TASK_VARIANTS = ("context_on", "context_off")
CONTEXT_SENSITIVE_TAGS = {
    "context-stress",
    "large-file",
    "huge-file",
    "tool-result-budget",
    "tool-history-snip",
    "large-result",
    "controlled-context-window",
}
PRIMARY_CONTEXT_PROFILE = "context_management_four_level"
CONTEXT_BASELINE_UTILIZATION = 0.20
CONTEXT_PRESSURE_UTILIZATION = 0.75
CONTEXT_COMPACT_WINDOW = 40000

RECOVERY_TASKS = [
    ("checkpoint_resume_goal", "checkpoint_resume"),
    ("checkpoint_resume_files", "checkpoint_resume"),
    ("orphaned_tool_call", "orphaned_tool_call"),
    ("interrupted_after_user", "checkpoint_resume"),
    ("interrupted_after_assistant_final", "checkpoint_resume"),
    ("tool_result_committed_report_missing", "checkpoint_resume"),
]

MEMORY_VARIANTS = ("memory_on", "memory_off", "memory_irrelevant")
RESUME_VARIANTS = ("resume_enabled", "resume_disabled")
NOT_MEASURED = "not_measured"
MEASURED = "measured"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_run_name() -> str:
    return "ablation-" + datetime.now().strftime("%Y%m%d-%H%M%S")


def _safe_rate(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _format_rate(value: float) -> str:
    return f"{value * 100:.1f}%"


def _load_runner():
    path = BENCH_DIR / "run.py"
    spec = importlib.util.spec_from_file_location("local_fixture_runner_for_ablation", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load benchmark runner: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_harness_regression(
    *,
    run_root: Path,
    task_file: Path,
    suite: str,
    timeout: int,
    model: str | None,
    stream: bool,
    skip: bool = False,
    harness_artifact: Path | None = None,
) -> dict[str, Any]:
    """Run or import the existing local-fixture regression benchmark."""
    if skip:
        result = {
            "schema_version": 2,
            "suite": "harness_regression",
            "status": "skipped",
            "source": "skipped_by_flag",
            "pico_metrics": {
                "task_count": 0,
                "pass_rate": 0.0,
                "within_budget_rate": 0.0,
                "verifier_pass_rate": 0.0,
                "failure_category_counts": {},
            },
            "scorecards": {},
        }
        benchmark_artifacts.write_json(run_root / "harness-regression-v2.json", result)
        return result

    source = "provided_artifact"
    benchmark_path: Path | None = None
    if harness_artifact is not None:
        artifact = benchmark_artifacts.read_json(harness_artifact)
        benchmark_path = harness_artifact
    else:
        source = "executed_local_fixture"
        runner = _load_runner()
        harness_output_root = run_root / "harness-runs"
        artifact = runner.run_benchmark(
            Namespace(
                task_file=str(task_file),
                output_root=str(harness_output_root),
                run_name="harness-regression",
                task_id=None,
                suite=suite,
                limit=None,
                timeout=timeout,
                model=model,
                stream=stream,
                dry_run=False,
            )
        )
        benchmark_path = harness_output_root / "harness-regression" / "benchmark.json"

    scorecards = artifact.get("scorecards") if isinstance(artifact.get("scorecards"), dict) else {}
    harness = scorecards.get("harness_regression") if isinstance(scorecards.get("harness_regression"), dict) else {}
    result = {
        "schema_version": 2,
        "suite": "harness_regression",
        "status": "completed",
        "source": source,
        "benchmark_path": str(benchmark_path) if benchmark_path else "",
        "benchmark_summary": artifact.get("summary") or {},
        "pico_metrics": {
            "task_count": int(harness.get("task_count", 0) or 0),
            "pass_rate": float(harness.get("pass_rate", 0.0) or 0.0),
            "within_budget_rate": float(harness.get("within_budget_rate", 0.0) or 0.0),
            "verifier_pass_rate": float(harness.get("verifier_pass_rate", 0.0) or 0.0),
            "failure_category_counts": harness.get("failure_category_counts") or {},
        },
        "scorecards": {
            "harness_regression": harness,
            "tool_control": scorecards.get("tool_control") or {},
            "run_audit": scorecards.get("run_audit") or {},
            "usage": scorecards.get("usage") or {},
        },
    }
    benchmark_artifacts.write_json(run_root / "harness-regression-v2.json", result)
    return result


def run_context_ablation(*, run_root: Path) -> dict[str, Any]:
    """Exercise NanoCode's implemented context governance code paths."""
    rows: list[dict[str, Any]] = []
    specs = _context_case_specs()
    with tempfile.TemporaryDirectory(prefix="nanocode-context-ablation-") as tmp:
        artifact_root = Path(tmp) / "artifacts"
        for spec in specs:
            rows.append(_run_context_case(run_root=run_root, artifact_root=artifact_root, spec=spec))

    profiles = {
        profile: _context_profile_metrics([row for row in rows if row["profile"] == profile])
        for profile in CONTEXT_PROFILE_ORDER
    }
    scenarios = {
        scenario: _context_profile_metrics([row for row in rows if row["scenario"] == scenario])
        for scenario in CONTEXT_SCENARIO_ORDER
    }
    budget_mix = {
        name: _context_profile_metrics([
            row for row in rows if row.get("tool_result_budget_case") == name
        ])
        for name, _ in CONTEXT_TOOL_RESULT_BUDGET_MIX
    }
    primary = _context_profile_metrics(rows)
    pressure = _context_profile_metrics([
        row for row in rows if row["scenario"] != "no_compression_baseline"
    ])
    baseline = scenarios["no_compression_baseline"]
    result = {
        "schema_version": 2,
        "suite": "context_ablation",
        "method": "deterministic_four_level_context_governance_cases_using_tool_runtime_and_compressor",
        "measurement_unit": "provider_neutral_conversation_estimated_tokens",
        "primary_profile": PRIMARY_CONTEXT_PROFILE,
        "config_count": len(rows),
        "scenario_counts": {name: scenarios[name]["config_count"] for name in CONTEXT_SCENARIO_ORDER},
        "scenario_ratio": "4:3:2:1",
        "tool_result_budget_ratio": "5:3:2:2",
        "tool_result_budget_mix": budget_mix,
        "request_levels": [label for label, _ in CONTEXT_REQUEST_LEVELS],
        "profiles": profiles,
        "scenarios": scenarios,
        PRIMARY_CONTEXT_PROFILE: primary,
        "pressure_scenarios": pressure,
        "avg_raw_prompt_estimated_tokens": primary["avg_raw_prompt_estimated_tokens"],
        "avg_governed_prompt_estimated_tokens": primary["avg_governed_prompt_estimated_tokens"],
        "avg_prompt_estimated_token_compression_ratio": primary["avg_prompt_estimated_token_compression_ratio"],
        "baseline_avg_prompt_estimated_token_compression_ratio": baseline[
            "avg_prompt_estimated_token_compression_ratio"
        ],
        "pressure_avg_prompt_estimated_token_compression_ratio": pressure[
            "avg_prompt_estimated_token_compression_ratio"
        ],
        "max_prompt_estimated_token_compression_ratio": primary["max_prompt_estimated_token_compression_ratio"],
        "current_request_preserved_rate": primary["current_request_preserved_rate"],
        "large_result_persist_count": sum(int(row["large_result_persist_count"]) for row in rows),
        "snipped_tool_result_count": sum(int(row["snipped_tool_result_count"]) for row in rows),
        "pressure_context_compact_count": sum(1 for row in rows if row["pressure_context_compacted"]),
        "forced_context_compact_count": sum(1 for row in rows if row["forced_context_compacted"]),
        "post_compact_context_restored_rate": primary["post_compact_context_restored_rate"],
        "rows": rows,
    }
    result["pico_metrics"] = {
        "primary_profile": result["primary_profile"],
        "avg_raw_prompt_estimated_tokens": primary["avg_raw_prompt_estimated_tokens"],
        "avg_governed_prompt_estimated_tokens": primary["avg_governed_prompt_estimated_tokens"],
        "avg_prompt_estimated_token_compression_ratio": primary["avg_prompt_estimated_token_compression_ratio"],
        "baseline_avg_prompt_estimated_token_compression_ratio": baseline[
            "avg_prompt_estimated_token_compression_ratio"
        ],
        "pressure_avg_prompt_estimated_token_compression_ratio": pressure[
            "avg_prompt_estimated_token_compression_ratio"
        ],
        "max_prompt_estimated_token_compression_ratio": primary["max_prompt_estimated_token_compression_ratio"],
        "current_request_preserved_rate": primary["current_request_preserved_rate"],
        "scenario_counts": result["scenario_counts"],
        "scenario_avg_prompt_estimated_token_compression_ratio": {
            scenario: data["avg_prompt_estimated_token_compression_ratio"] for scenario, data in scenarios.items()
        },
        "tool_result_budget_case_avg_prompt_estimated_token_compression_ratio": {
            name: data["avg_prompt_estimated_token_compression_ratio"] for name, data in budget_mix.items()
        },
        "profile_avg_prompt_estimated_token_compression_ratio": {
            profile: data["avg_prompt_estimated_token_compression_ratio"] for profile, data in profiles.items()
        },
        "context_management_avg_prompt_estimated_token_compression_ratio": primary[
            "avg_prompt_estimated_token_compression_ratio"
        ],
    }
    benchmark_artifacts.write_json(run_root / "context-ablation-v2.json", result)
    return result


def _context_case_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []

    # 16 cases: normal work should not trigger any context compression.
    for history_turns in (1, 2, 3, 4):
        for tool_result_count in (0, 1):
            for request_label, request in CONTEXT_REQUEST_LEVELS:
                specs.append({
                    "scenario": "no_compression_baseline",
                    "profile": "baseline_profile",
                    "history_label": f"{history_turns}_turns",
                    "history_turns": history_turns,
                    "result_label": f"{tool_result_count}_small_results",
                    "tool_result_count": tool_result_count,
                    "request_label": request_label,
                    "current_request": request,
                    "pressure_utilization": CONTEXT_BASELINE_UTILIZATION,
                    "context_window": None,
                    "expected": "none",
                })

    # 12 cases: realistic admission-budget mix, 5:3:2:2.
    # Every case crosses its tool-specific persistence threshold; baseline owns
    # the "no compression should happen" negative-control cases.
    request_by_label = dict(CONTEXT_REQUEST_LEVELS)
    budget_layout = [
        ("small_file_read", 1, "short", "large_result_persist"),
        ("small_file_read", 1, "long", "large_result_persist"),
        ("small_file_read", 2, "short", "large_result_persist"),
        ("small_file_read", 2, "long", "large_result_persist"),
        ("small_file_read", 3, "short", "large_result_persist"),
        ("medium_file_read", 1, "long", "large_result_persist"),
        ("medium_file_read", 2, "short", "large_result_persist"),
        ("medium_file_read", 3, "long", "large_result_persist"),
        ("large_search_output", 1, "short", "large_result_persist"),
        ("large_search_output", 2, "long", "large_result_persist"),
        ("ci_log", 1, "short", "large_result_persist"),
        ("ci_log", 2, "long", "large_result_persist"),
    ]
    for budget_case, history_turns, request_label, expected in budget_layout:
        specs.append({
            "scenario": "tool_result_budget",
            "profile": "debugging_profile",
            "history_label": f"{history_turns}_turns",
            "history_turns": history_turns,
            "result_label": budget_case,
            "tool_result_count": 1,
            "tool_result_budget_case": budget_case,
            "tool_name": _budget_tool_name(budget_case),
            "request_label": request_label,
            "current_request": request_by_label[request_label],
            "pressure_utilization": CONTEXT_BASELINE_UTILIZATION,
            "context_window": None,
            "expected": expected,
        })

    # 8 cases: accumulated medium-sized tool history is snipped, not compacted.
    for profile in ("refactor_profile", "incident_review_profile"):
        for history_turns in (8, 12):
            for request_label, request in CONTEXT_REQUEST_LEVELS:
                specs.append({
                    "scenario": "tool_history_snip",
                    "profile": profile,
                    "history_label": f"{history_turns}_turns",
                    "history_turns": history_turns,
                    "result_label": "medium_history",
                    "tool_result_count": history_turns,
                    "request_label": request_label,
                    "current_request": request,
                    "pressure_utilization": CONTEXT_PRESSURE_UTILIZATION,
                    "context_window": None,
                    "expected": "tool_history_snip",
                })

    # 4 cases: long-chain pressure should compact after cheaper governance.
    for profile in ("refactor_profile", "incident_review_profile"):
        for request_label, request in CONTEXT_REQUEST_LEVELS:
            specs.append({
                "scenario": "context_compact",
                "profile": profile,
                "history_label": "24_turns",
                "history_turns": 24,
                "result_label": "long_chain",
                "tool_result_count": 24,
                "request_label": request_label,
                "current_request": request,
                "pressure_utilization": CONTEXT_PRESSURE_UTILIZATION,
                "context_window": CONTEXT_COMPACT_WINDOW,
                "expected": "context_compact",
            })

    expected_counts = {
        "no_compression_baseline": 16,
        "tool_result_budget": 12,
        "tool_history_snip": 8,
        "context_compact": 4,
    }
    actual_counts = Counter(spec["scenario"] for spec in specs)
    actual_budget_mix = Counter(
        spec.get("tool_result_budget_case", "")
        for spec in specs
        if spec["scenario"] == "tool_result_budget"
    )
    if dict(actual_counts) != expected_counts or len(specs) != 40:
        raise AssertionError(f"invalid context ablation scenario matrix: {dict(actual_counts)}")
    expected_budget_mix = dict(CONTEXT_TOOL_RESULT_BUDGET_MIX)
    if dict(actual_budget_mix) != expected_budget_mix:
        raise AssertionError(f"invalid tool result budget mix: {dict(actual_budget_mix)}")
    return specs


def _context_profile_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    raw_values = [int(row["raw_prompt_estimated_tokens"]) for row in rows]
    governed_values = [int(row["governed_prompt_estimated_tokens"]) for row in rows]
    ratios = [float(row["prompt_estimated_token_compression_ratio"]) for row in rows]
    preserved = sum(1 for row in rows if row["current_request_preserved"])
    compact_restore_rows = [
        row for row in rows if row["pressure_context_compacted"] or row["forced_context_compacted"]
    ]
    compact_restored = sum(1 for row in compact_restore_rows if row["post_compact_context_restored"])
    compression_triggered = sum(1 for row in rows if row["compression_triggered"])
    persist_triggered = sum(1 for row in rows if row["large_result_persist_count"] > 0)
    snip_triggered = sum(1 for row in rows if row["tool_history_snip_triggered"])
    compact_triggered = sum(1 for row in rows if row["pressure_context_compacted"])
    return {
        "config_count": len(rows),
        "avg_raw_prompt_estimated_tokens": _mean(raw_values),
        "avg_governed_prompt_estimated_tokens": _mean(governed_values),
        "avg_prompt_estimated_token_compression_ratio": _mean(ratios),
        "max_prompt_estimated_token_compression_ratio": max(ratios) if ratios else 0.0,
        "current_request_preserved_rate": _safe_rate(preserved, len(rows)),
        "compression_triggered_count": compression_triggered,
        "compression_triggered_rate": _safe_rate(compression_triggered, len(rows)),
        "large_result_persist_count": sum(int(row["large_result_persist_count"]) for row in rows),
        "large_result_persist_trigger_rate": _safe_rate(persist_triggered, len(rows)),
        "snipped_tool_result_count": sum(int(row["snipped_tool_result_count"]) for row in rows),
        "tool_history_snip_trigger_rate": _safe_rate(snip_triggered, len(rows)),
        "pressure_context_compact_count": sum(1 for row in rows if row["pressure_context_compacted"]),
        "context_compact_trigger_rate": _safe_rate(compact_triggered, len(rows)),
        "forced_context_compact_count": sum(1 for row in rows if row["forced_context_compacted"]),
        "post_compact_context_restored_rate": _safe_rate(compact_restored, len(compact_restore_rows)),
    }


def _run_context_case(
    *,
    run_root: Path,
    artifact_root: Path,
    spec: dict[str, Any],
) -> dict[str, Any]:
    from nanocode.agent.agent import Agent, AgentConfig
    from nanocode.agent.budget import estimate_conversation_tokens
    from nanocode.agent.runtime_management.compressor import Compressor, SNIP_PLACEHOLDER
    from nanocode.agent.types import ConversationHistory

    raw_history = _build_context_history(
        artifact_root=artifact_root,
        session_id=f"raw-{spec['scenario']}-{spec['profile']}",
        profile=str(spec["profile"]),
        history_turns=int(spec["history_turns"]),
        tool_result_count=int(spec["tool_result_count"]),
        current_request=str(spec["current_request"]),
        budget_large_results=False,
        tool_result_budget_case=str(spec.get("tool_result_budget_case") or ""),
    )
    governed_history = _build_context_history(
        artifact_root=artifact_root,
        session_id=(
            f"{spec['scenario']}-{spec['profile']}-{spec['history_label']}-"
            f"{spec['result_label']}-{spec['request_label']}"
        ),
        profile=str(spec["profile"]),
        history_turns=int(spec["history_turns"]),
        tool_result_count=int(spec["tool_result_count"]),
        current_request=str(spec["current_request"]),
        budget_large_results=True,
        tool_result_budget_case=str(spec.get("tool_result_budget_case") or ""),
    )
    raw_tokens = estimate_conversation_tokens(raw_history["history"])

    async def summarize_messages(history, _system_prompt, _user_prompt, _max_tokens):
        return (
            "Ablation summary of earlier context.\n"
            f"Messages summarized: {history.count()}.\n"
            "Important files: checkout logs, source files, release notes, service config, and final_state.txt."
        )

    recovery_context = (
        "[PostCompact context]\n"
        "Project instructions restored.\n"
        "Local memory reminder restored.\n"
        "Recent file context refreshed."
    )

    pressure_agent = Agent(AgentConfig(
        model="claude-opus-4-6",
        context_window=spec.get("context_window"),
    ))
    pressure_agent.conversation = governed_history["history"]
    pressure_agent.last_input_token_count = int(
        pressure_agent.effective_window * float(spec["pressure_utilization"])
    )
    pressure_compressor = Compressor(
        pressure_agent,
        workspace=run_root,
        summarize_messages=summarize_messages,
        build_post_compact_context=lambda: recovery_context,
    )
    pressure_preparation = asyncio.run(pressure_compressor.prepare_context_for_provider())
    snipped_count = _count_text(pressure_agent.conversation, SNIP_PLACEHOLDER)
    governed_tokens = estimate_conversation_tokens(pressure_agent.conversation)
    current_preserved = _history_contains_text(pressure_agent.conversation, str(spec["current_request"]))

    forced_compacted = False
    forced_restored = False
    forced_compacted_tokens = 0
    if spec["scenario"] == "context_compact":
        forced_agent = Agent(AgentConfig(
            model="claude-opus-4-6",
            context_window=spec.get("context_window"),
        ))
        forced_agent.conversation = ConversationHistory.restore(governed_history["history"].snapshot())
        forced_compressor = Compressor(
            forced_agent,
            workspace=run_root,
            summarize_messages=summarize_messages,
            build_post_compact_context=lambda: recovery_context,
        )
        forced_compacted = asyncio.run(forced_compressor.compact_context(reason="ablation_forced_compact", force=True))
        forced_restored = _history_contains_text(forced_agent.conversation, "Recent file context refreshed.")
        forced_compacted_tokens = estimate_conversation_tokens(forced_agent.conversation)
    ratio = max(0.0, 1.0 - _safe_rate(governed_tokens, raw_tokens))
    compression_triggered = bool(
        governed_history["persisted_count"] > 0
        or snipped_count > 0
        or pressure_preparation.reason == "context_compact"
    )
    return {
        "config_id": (
            f"{spec['scenario']}-{spec['profile']}-{spec['history_label']}-"
            f"{spec['result_label']}-{spec['request_label']}"
        ),
        "scenario": spec["scenario"],
        "expected": spec["expected"],
        "profile": spec["profile"],
        "tool_result_budget_case": spec.get("tool_result_budget_case", ""),
        "tool_name": spec.get("tool_name", ""),
        "history_level": spec["history_label"],
        "history_turns": spec["history_turns"],
        "tool_result_level": spec["result_label"],
        "tool_result_count": spec["tool_result_count"],
        "request_level": spec["request_label"],
        "pressure_utilization": spec["pressure_utilization"],
        "context_window": spec.get("context_window"),
        "raw_prompt_estimated_tokens": raw_tokens,
        "governed_prompt_estimated_tokens": governed_tokens,
        "raw_conversation_estimated_tokens": raw_tokens,
        "governed_conversation_estimated_tokens": governed_tokens,
        "prompt_estimated_token_compression_ratio": ratio,
        "large_result_persist_count": governed_history["persisted_count"],
        "context_preparation_reason": pressure_preparation.reason,
        "compression_triggered": compression_triggered,
        "tool_history_snip_triggered": snipped_count > 0,
        "snipped_tool_result_count": snipped_count,
        "pressure_context_compacted": pressure_preparation.reason == "context_compact",
        "forced_context_compacted": forced_compacted,
        "forced_compacted_estimated_tokens": forced_compacted_tokens,
        "current_request_preserved": current_preserved,
        "post_compact_context_restored": forced_restored,
    }


def _build_context_history(
    *,
    artifact_root: Path,
    session_id: str,
    profile: str,
    history_turns: int,
    tool_result_count: int,
    current_request: str,
    budget_large_results: bool,
    tool_result_budget_case: str = "",
) -> dict[str, Any]:
    from nanocode.agent.types import ConversationHistory, ConversationMessage, TextBlock, ToolResultBlock, ToolUseBlock

    history = ConversationHistory()
    persisted_count = 0

    for index in range(history_turns):
        path = _profile_path(profile, index)
        task = _profile_task(profile)
        budget_case = tool_result_budget_case if index < tool_result_count else ""
        tool_name = _budget_tool_name(budget_case) if budget_case else "read_file"
        tool_input = _context_tool_input(tool_name, path)
        history.messages.append(ConversationMessage(
            role="user",
            content=[TextBlock(f"Previous request {index}: inspect {path} for {task}.")],
        ))
        call_id = f"call_{index}"
        history.messages.append(ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id=call_id, name=tool_name, input=tool_input)],
        ))
        if budget_case:
            result = _run_budget_tool_result(
                artifact_root=artifact_root,
                session_id=session_id,
                call_id=call_id,
                budget_case=budget_case,
                persist=budget_large_results,
            )
            if result.metadata.get("persisted"):
                persisted_count += 1
            content = result.content
        elif index < tool_result_count:
            content = _tool_result_content(profile, index)
            if budget_large_results:
                result = _budget_large_result_via_tool_runtime(
                    artifact_root=artifact_root,
                    session_id=session_id,
                    call_id=call_id,
                    content=content,
                )
                if result.metadata.get("persisted"):
                    persisted_count += 1
                content = result.content
        else:
            content = f"{path}: small context result {index}"
            if budget_large_results:
                result = _budget_large_result_via_tool_runtime(
                    artifact_root=artifact_root,
                    session_id=session_id,
                    call_id=call_id,
                    content=content,
                )
                if result.metadata.get("persisted"):
                    persisted_count += 1
                content = result.content
        history.messages.append(ConversationMessage(
            role="tool_result",
            content=[ToolResultBlock(tool_use_id=call_id, tool_name=tool_name, content=content)],
        ))
        history.messages.append(ConversationMessage(
            role="assistant",
            content=[TextBlock(f"Noted {path}.")],
        ))

    history.messages.append(ConversationMessage(role="user", content=[TextBlock(current_request)]))
    return {"history": history, "persisted_count": persisted_count}


def _run_budget_tool_result(
    *,
    artifact_root: Path,
    session_id: str,
    call_id: str,
    budget_case: str,
    persist: bool,
):
    from nanocode.agent.types import ToolCall
    from nanocode.agent.runtime_management.persistence.artifacts import ArtifactStore
    from nanocode.cli.core.sandbox.manager import SandboxManager
    from nanocode.cli.core.sandbox.types import SandboxConfig
    from nanocode.cli.core.tools.registry import ToolRegistry
    from nanocode.cli.core.tools.runtime import ToolRuntime
    from nanocode.cli.core.tools.types import ToolContext

    workspace = artifact_root / session_id / "workspace" / call_id
    workspace.mkdir(parents=True, exist_ok=True)
    content = _budget_tool_content(budget_case)
    tool_name = _budget_tool_name(budget_case)
    if tool_name == "grep_search":
        search_dir = workspace / "search"
        search_dir.mkdir(parents=True, exist_ok=True)
        (search_dir / "checkout-index.log").write_text(content, encoding="utf-8")
        tool_input = {"pattern": "CHECKOUT_MATCH", "path": str(search_dir), "include": "*.log"}
        sandbox_manager = None
    elif tool_name == "run_shell":
        (workspace / "ci_log.txt").write_text(content, encoding="utf-8")
        (workspace / "emit_ci_log.py").write_text(
            "from pathlib import Path\n"
            "print(Path('ci_log.txt').read_text(encoding='utf-8'), end='')\n",
            encoding="utf-8",
        )
        tool_input = {"command": f"{sys.executable} emit_ci_log.py", "timeout": 30_000}
        sandbox_manager = SandboxManager(
            SandboxConfig(
                profile="local",
                backend="local",
                workspace_host_path=workspace,
            ),
            session_id=session_id,
        )
    else:
        source_path = workspace / f"{call_id}.txt"
        source_path.write_text(content, encoding="utf-8")
        tool_input = {"file_path": str(source_path)}
        sandbox_manager = None

    store = ArtifactStore(session_id=session_id, root=artifact_root / session_id / "persisted")
    runtime = ToolRuntime(
        ToolRegistry.with_builtin_tools(),
        permission_mode="bypassPermissions",
        persist_large_result=store.write_tool_result if persist else None,
    )
    ctx = ToolContext(cwd=workspace, session_id=session_id, sandbox_manager=sandbox_manager)
    call = ToolCall(id=call_id, name=tool_name, input=tool_input)

    async def _execute():
        return await runtime.execute_one(call, ctx)

    return asyncio.run(_execute())


def _budget_large_result_via_tool_runtime(*, artifact_root: Path, session_id: str, call_id: str, content: str):
    from nanocode.agent.types import ToolCall
    from nanocode.agent.runtime_management.persistence.artifacts import ArtifactStore
    from nanocode.cli.core.tools.registry import ToolRegistry
    from nanocode.cli.core.tools.runtime import ToolRuntime
    from nanocode.cli.core.tools.types import ToolContext

    workspace = artifact_root / session_id / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    source_path = workspace / f"{call_id}.txt"
    source_path.write_text(content, encoding="utf-8")
    store = ArtifactStore(session_id=session_id, root=artifact_root / session_id / "persisted")
    runtime = ToolRuntime(
        ToolRegistry.with_builtin_tools(),
        permission_mode="bypassPermissions",
        persist_large_result=store.write_tool_result,
    )
    ctx = ToolContext(cwd=workspace, session_id=session_id)
    call = ToolCall(id=call_id, name="read_file", input={"file_path": str(source_path)})

    async def _execute():
        return await runtime.execute_one(call, ctx)

    return asyncio.run(_execute())


def _budget_tool_name(budget_case: str) -> str:
    if budget_case == "large_search_output":
        return "grep_search"
    if budget_case == "ci_log":
        return "run_shell"
    return "read_file"


def _context_tool_input(tool_name: str, path: str) -> dict[str, Any]:
    if tool_name == "grep_search":
        return {"pattern": "CHECKOUT_MATCH", "path": str(Path(path).parent or "."), "include": "*.log"}
    if tool_name == "run_shell":
        return {"command": "python emit_ci_log.py", "timeout": 30_000}
    return {"file_path": path}


def _budget_tool_content(budget_case: str) -> str:
    if budget_case == "small_file_read":
        return _small_file_read_result()
    if budget_case == "medium_file_read":
        return _medium_file_read_result()
    if budget_case == "large_search_output":
        return _large_search_fixture()
    if budget_case == "ci_log":
        return _ci_log_result()
    raise ValueError(f"unknown tool result budget case: {budget_case}")


def _tool_result_limit(tool_name: str) -> int:
    from nanocode.cli.core.tools.types import DEFAULT_MAX_RESULT_CHARS, TOOL_RESULT_CHAR_LIMITS

    return TOOL_RESULT_CHAR_LIMITS.get(tool_name, DEFAULT_MAX_RESULT_CHARS)


def _tool_result_content(profile: str, index: int) -> str:
    if profile == "baseline_profile":
        return _baseline_tool_result(index)
    if profile == "debugging_profile":
        return _debugging_tool_result(index)
    if profile == "refactor_profile":
        return _refactor_tool_result(index)
    if profile == "incident_review_profile":
        return _incident_review_tool_result(index)
    raise ValueError(f"unknown context profile: {profile}")


def _profile_task(profile: str) -> str:
    tasks = {
        "baseline_profile": "a small configuration edit",
        "debugging_profile": "a checkout retry failure",
        "refactor_profile": "a cross-file checkout refactor",
        "incident_review_profile": "release readiness evidence",
    }
    return tasks.get(profile, "the current task")


def _profile_path(profile: str, index: int) -> str:
    paths = {
        "baseline_profile": [
            "config/service.json",
            "README.md",
            "src/settings.py",
            "tests/test_settings.py",
        ],
        "debugging_profile": [
            "logs/pytest-full.log",
            "tests/checkout/test_retry_guard.py",
            "src/checkout/retry_guard.py",
            "src/payment/commit_adapter.py",
            "logs/checkout-rerun.log",
        ],
        "refactor_profile": [
            "src/checkout/retry_policy.py",
            "src/checkout/retry_guard.py",
            "src/payment/commit_adapter.py",
            "tests/test_retry_policy.py",
            "docs/retry-behavior.md",
        ],
        "incident_review_profile": [
            "audit/incident_log.md",
            "audit/service_notes.md",
            "audit/retry_matrix.md",
            "audit/deploy_review.md",
            "audit/customer_impact.md",
            "audit/decision_register.md",
        ],
    }
    choices = paths.get(profile)
    if not choices:
        raise ValueError(f"unknown context profile: {profile}")
    return choices[index % len(choices)]


def _baseline_tool_result(index: int) -> str:
    sections = [
        (
            "config/service.json",
            [
                '{',
                '  "service_name": "checkout",',
                '  "retry_enabled": false,',
                '  "max_attempts": 2',
                '}',
            ],
        ),
        (
            "README.md",
            [
                "# Checkout Service",
                "This fixture describes a small service configuration task.",
                "Keep service_name unchanged while enabling retry.",
            ],
        ),
        (
            "src/settings.py",
            [
                "SERVICE_NAME = 'checkout'",
                "RETRY_ENABLED = False",
                "MAX_ATTEMPTS = 2",
            ],
        ),
        (
            "tests/test_settings.py",
            [
                "def test_service_name_is_checkout():",
                "    assert SERVICE_NAME == 'checkout'",
                "def test_retry_flag_defaults_false():",
                "    assert RETRY_ENABLED is False",
            ],
        ),
    ]
    path, lines = sections[index % len(sections)]
    return "\n".join([f"## {path}", *lines])


def _small_file_read_result() -> str:
    lines = [
        "# checkout retry config",
        "retry_enabled=false",
        "max_attempts=2",
        "owner=checkout-platform",
        "note=small over-threshold read should persist with a preview",
    ]
    return _render_context_packet_at_least(
        title="config/checkout-retry.env",
        intro="Small over-threshold config read during a normal edit.",
        lines=lines,
        min_chars=_tool_result_limit("read_file") + 5_000,
        extra="Conclusion: update retry_enabled only after checking tests.",
    )


def _medium_file_read_result() -> str:
    lines = [
        "def classify_retry(error_code: str) -> str:",
        "    if error_code in {'timeout', 'transport-reset'}:",
        "        return 'guarded-retry'",
        "    if error_code == 'validation':",
        "        return 'no-retry'",
        "    return 'manual-review'",
        "class RetryGuard:",
        "    def has_attempt(self, order_id: str, token: str) -> bool:",
        "        return (order_id, token) in self._attempts",
    ]
    return _render_context_packet_at_least(
        title="src/checkout/retry_policy.py",
        intro="Medium over-threshold source file read with nearby code and comments.",
        lines=lines,
        min_chars=_tool_result_limit("read_file") + 90_000,
        extra="Conclusion: preserve retry guard state across timeout retries.",
    )


def _large_search_fixture() -> str:
    chunks: list[str] = []
    payload = (
        "CHECKOUT_MATCH retry_token=tok-duplicate-commit "
        "root_cause=retry_guard_state_reset_between_attempts "
        "expected_state=blocked-duplicate actual_state=accepted-duplicate "
        "owner=checkout-platform remediation=preserve_guard_state "
    )
    for index in range(1, 181):
        chunks.append(
            f"{payload}case={index:03d} shard={index % 11} "
            f"trace={'x' * 1_200}"
        )
    chunks.append("summary=large grep/search output should be persisted after runtime admission budget")
    return "\n".join(chunks)


def _ci_log_result() -> str:
    lines = [
        "PASSED tests/cart/test_cart_flow.py::test_cart_case",
        "PASSED tests/checkout/test_checkout_flow.py::test_checkout_case",
        "INFO checkout.retry_guard retry_token loaded for order=ord-107",
        "WARNING payment.commit duplicate commit candidate observed",
        "ERROR checkout.retry_guard prior attempt state missing after timeout retry",
    ]
    return _render_context_packet_at_least(
        title="logs/pytest-full.log",
        intro="Large CI log from a checkout retry investigation.",
        lines=lines,
        min_chars=_tool_result_limit("run_shell") + 250_000,
        extra=(
            "Root cause summary: retry guard state was reset between attempts.\n"
            "Required diagnosis line: failing_test=tests/checkout/test_retry_guard.py::"
            "test_retry_guard_blocks_duplicate_commit\n"
            "Required diagnosis line: root_cause=retry guard state reset between attempts"
        ),
    )


def _debugging_tool_result(index: int) -> str:
    if index == 0:
        return _debugging_pytest_log()
    sections = [
        (
            "tests/checkout/test_retry_guard.py",
            [
                "def test_retry_guard_blocks_duplicate_commit():",
                "    first = service.commit(order_id='ord-107', retry_token='retry-a')",
                "    second = service.commit(order_id='ord-107', retry_token='retry-a')",
                "    assert second.state == 'blocked-duplicate'",
            ],
        ),
        (
            "src/checkout/retry_guard.py",
            [
                "class RetryGuard:",
                "    def record_attempt(self, order_id: str, token: str) -> None:",
                "        self._attempts[(order_id, token)] = datetime.utcnow()",
                "    def has_attempt(self, order_id: str, token: str) -> bool:",
                "        return (order_id, token) in self._attempts",
            ],
        ),
        (
            "logs/checkout-rerun.log",
            [
                "checkout.retry_guard INFO retry token loaded for order=ord-107",
                "checkout.retry_guard ERROR retry token missing after timeout boundary",
                "payment.commit WARNING duplicate commit accepted by adapter",
            ],
        ),
    ]
    return _render_context_packet(
        title=sections[index % len(sections)][0],
        intro=f"Debugging packet {index}: checkout retry investigation.",
        lines=sections[index % len(sections)][1],
        repeat=180,
        extra=f"Conclusion: preserve retry guard state across timeout retries. packet={index}",
    )


def _debugging_pytest_log() -> str:
    services = ["cart", "checkout", "payment", "inventory", "notifications", "analytics", "auth"]
    chunks = [
        "============================= test session starts ==============================",
        "platform linux -- Python 3.12.4, pytest-8.2.2",
        "rootdir: /workspace/checkout-service",
        "collected 184 items",
        "CI failure index:",
        "  Required diagnosis line: failing_test=tests/checkout/test_retry_guard.py::test_retry_guard_blocks_duplicate_commit",
        "  Required diagnosis line: root_cause=retry guard state reset between attempts",
        "  Detail: full traceback is repeated near the end of this log for local debugging.",
    ]
    for index in range(1, 2850):
        service = services[index % len(services)]
        test_name = f"tests/{service}/test_{service}_flow.py::test_{service}_case_{index % 37:02d}"
        duration_ms = 12 + (index * 7) % 240
        if service == "checkout" and index % 41 == 0:
            chunks.append(
                f"INFO checkout.retry_guard retry_token=tok-{index:04d} "
                f"idempotency_key=stable-{index % 13} state=loaded duration_ms={duration_ms}"
            )
        elif index % 53 == 0:
            chunks.append(
                f"WARNING {service}: slow fixture setup took {duration_ms + 220}ms "
                "but stayed below alert threshold"
            )
        chunks.append(f"PASSED {test_name} [{duration_ms}ms]")
    chunks.extend([
        "================================== FAILURES ===================================",
        "________ test_retry_guard_blocks_duplicate_commit[transport-reset] ________",
        "tests/checkout/test_retry_guard.py:17: in test_retry_guard_blocks_duplicate_commit",
        "    assert second_commit.state == 'blocked-duplicate'",
        "E   AssertionError: duplicate checkout commit was accepted after retry timeout",
        "E   assert 'accepted-duplicate' == 'blocked-duplicate'",
        "Captured log call for tests/checkout/test_retry_guard.py::test_retry_guard_blocks_duplicate_commit",
        "  checkout.retry_guard DEBUG loading prior attempt state for retry_token=tok-duplicate-commit",
        "  checkout.retry_guard ERROR prior attempt state missing after timeout retry",
        "  payment.commit WARNING duplicate commit request accepted by downstream adapter",
        "Root cause summary: retry guard state was reset between attempts.",
        "Required diagnosis line: failing_test=tests/checkout/test_retry_guard.py::test_retry_guard_blocks_duplicate_commit",
        "Required diagnosis line: root_cause=retry guard state reset between attempts",
        "==================== 183 passed, 1 failed, 14 warnings in 19.84s ====================",
    ])
    return "\n".join(chunks)


def _refactor_tool_result(index: int) -> str:
    sections = [
        (
            "src/checkout/retry_policy.py",
            [
                "def classify_retry(error_code: str) -> str:",
                "    if error_code in {'timeout', 'transport-reset'}:",
                "        return 'guarded-retry'",
                "    if error_code == 'validation':",
                "        return 'no-retry'",
                "    return 'manual-review'",
            ],
        ),
        (
            "tests/test_retry_policy.py",
            [
                "def test_timeout_retries_once_with_guard():",
                "    assert classify_retry('timeout') == 'guarded-retry'",
                "def test_validation_does_not_retry():",
                "    assert classify_retry('validation') == 'no-retry'",
            ],
        ),
        (
            "logs/release-smoke.log",
            [
                "2026-06-13T10:12:01Z checkout smoke PASS route=/confirm latency_ms=184",
                "2026-06-13T10:12:03Z retry guard active duplicate_commit=false",
                "2026-06-13T10:12:08Z downstream 503 recovered retry_count=1",
            ],
        ),
        (
            "docs/release-notes.md",
            [
                "Release decision depends on checkout retry guard remaining enabled.",
                "Rollback if duplicate payment confirmation count becomes non-zero.",
                "Support runbook should mention delayed confirmation warnings.",
            ],
        ),
    ]
    path, lines = sections[index % len(sections)]
    return _render_context_packet(
        title=path,
        intro=f"Refactor packet {index}: mixed code, tests, logs, and release notes.",
        lines=lines,
        repeat=190,
        extra=f"Refactor note: keep retry policy behavior stable across modules. packet={index}",
    )


def _incident_review_tool_result(index: int) -> str:
    sections = [
        (
            "audit/incident_log.md",
            [
                "Timeline: checkout retry spikes were limited to the canary ring.",
                "Operator note: no data loss observed; duplicate warnings increased.",
                "Mitigation: keep retry guard enabled during rollout.",
            ],
        ),
        (
            "audit/service_notes.md",
            [
                "Checkout owns idempotency checks before payment confirmation.",
                "Payment adapter must not receive duplicate commit requests.",
                "Retry guard state must survive transport reset handling.",
            ],
        ),
        (
            "audit/retry_matrix.md",
            [
                "Validation failure -> do not retry.",
                "Network timeout -> retry once with guard token.",
                "Downstream 503 -> retry twice with exponential backoff.",
            ],
        ),
        (
            "audit/deploy_review.md",
            [
                "Rollback signal: duplicate payment confirmation count greater than zero.",
                "Smoke tests must pass before widening traffic.",
                "Staged rollout approved only if retry guard remains active.",
            ],
        ),
        (
            "audit/customer_impact.md",
            [
                "No confirmed duplicate charges in the sampled window.",
                "Watch checkout_confirmation_latency_p95 during the first hour.",
                "Support runbook should mention delayed confirmation warnings.",
            ],
        ),
        (
            "audit/decision_register.md",
            [
                "Do not ship without the retry guard.",
                "Write context_decision=ship-with-retry-guard to decision.txt.",
                "Final release decision is ship-with-retry-guard.",
            ],
        ),
    ]
    path, lines = sections[index % len(sections)]
    return _render_context_packet(
        title=path,
        intro=f"Incident review packet {index}: release evidence and operational notes.",
        lines=lines,
        repeat=210,
        extra=f"Decision note: final release decision depends on retry guard evidence. packet={index}",
    )


def _render_context_packet(*, title: str, intro: str, lines: list[str], repeat: int, extra: str) -> str:
    chunks = [f"## {title}\n", f"{intro}\n"]
    for line_number in range(1, repeat + 1):
        source = lines[(line_number - 1) % len(lines)]
        chunks.append(
            f"{line_number:04d} | {source} | reviewer=release-eng "
            f"status=reviewed shard={line_number % 17}\n"
        )
    chunks.append(extra + "\n")
    return "".join(chunks)


def _render_context_packet_at_least(*, title: str, intro: str, lines: list[str], min_chars: int, extra: str) -> str:
    chunks = [f"## {title}\n", f"{intro}\n"]
    total_chars = sum(len(chunk) for chunk in chunks)
    line_number = 1
    while total_chars < min_chars:
        source = lines[(line_number - 1) % len(lines)]
        chunk = (
            f"{line_number:04d} | {source} | reviewer=release-eng "
            f"status=reviewed shard={line_number % 17}\n"
        )
        chunks.append(chunk)
        total_chars += len(chunk)
        line_number += 1
    chunks.append(extra + "\n")
    return "".join(chunks)


def _history_json(history) -> str:
    return json.dumps(history.snapshot(), ensure_ascii=False, sort_keys=True)


def _history_contains_text(history, needle: str) -> bool:
    return needle in _history_json(history)


def _count_text(history, needle: str) -> int:
    return _history_json(history).count(needle)


def run_context_task_completion_ablation(
    *,
    run_root: Path,
    task_file: Path,
    suite: str,
    timeout: int,
    model: str | None,
    stream: bool,
    execute: bool = False,
    context_on_artifact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare task completion with context governance enabled and disabled."""
    if not execute:
        result = {
            "schema_version": 2,
            "suite": "context_task_completion_ablation",
            "status": NOT_MEASURED,
            "method": "full_task_completion_context_on_off",
            "task_count": 0,
            "context_sensitive_task_count": 0,
            "variants": {
                variant: _context_task_variant_metrics([])
                for variant in CONTEXT_TASK_VARIANTS
            },
            "context_sensitive_variants": {
                variant: _context_task_variant_metrics([])
                for variant in CONTEXT_TASK_VARIANTS
            },
            "deltas": {},
            "context_sensitive_deltas": {},
            "rows": [],
        }
        result["pico_metrics"] = _context_task_pico_metrics(result)
        benchmark_artifacts.write_json(run_root / "context-task-ablation-v2.json", result)
        return result

    if context_on_artifact is None:
        context_on_artifact = _run_context_task_variant(
            run_root=run_root,
            task_file=task_file,
            suite=suite,
            timeout=timeout,
            model=model,
            stream=stream,
            variant="context_on",
            context_governance="full",
        )

    context_off_artifact = _run_context_task_variant(
        run_root=run_root,
        task_file=task_file,
        suite=suite,
        timeout=timeout,
        model=model,
        stream=stream,
        variant="context_off",
        context_governance="off",
    )

    rows = [
        *_context_task_rows_from_benchmark(context_on_artifact, "context_on"),
        *_context_task_rows_from_benchmark(context_off_artifact, "context_off"),
    ]
    variants = {
        variant: _context_task_variant_metrics([row for row in rows if row["variant"] == variant])
        for variant in CONTEXT_TASK_VARIANTS
    }
    context_rows = [row for row in rows if row["context_sensitive"]]
    context_variants = {
        variant: _context_task_variant_metrics([row for row in context_rows if row["variant"] == variant])
        for variant in CONTEXT_TASK_VARIANTS
    }
    result = {
        "schema_version": 2,
        "suite": "context_task_completion_ablation",
        "status": MEASURED if rows else NOT_MEASURED,
        "method": "full_task_completion_context_on_off",
        "task_count": len({row["task_id"] for row in rows}),
        "context_sensitive_task_count": len({row["task_id"] for row in context_rows}),
        "variants": variants,
        "context_sensitive_variants": context_variants,
        "deltas": _context_task_deltas(variants),
        "context_sensitive_deltas": _context_task_deltas(context_variants),
        "rows": rows,
    }
    result["pico_metrics"] = _context_task_pico_metrics(result)
    benchmark_artifacts.write_json(run_root / "context-task-ablation-v2.json", result)
    return result


def _run_context_task_variant(
    *,
    run_root: Path,
    task_file: Path,
    suite: str,
    timeout: int,
    model: str | None,
    stream: bool,
    variant: str,
    context_governance: str,
) -> dict[str, Any]:
    runner = _load_runner()
    output_root = run_root / "context-task-ablation-runs"
    return runner.run_benchmark(
        Namespace(
            task_file=str(task_file),
            output_root=str(output_root),
            run_name=variant,
            task_id=None,
            suite=suite,
            limit=None,
            timeout=timeout,
            model=model,
            stream=stream,
            dry_run=False,
            context_governance=context_governance,
        )
    )


def _context_task_rows_from_benchmark(
    benchmark_artifact: dict[str, Any] | None,
    variant: str,
) -> list[dict[str, Any]]:
    if not isinstance(benchmark_artifact, dict):
        return []
    rows = benchmark_artifact.get("rows")
    if not isinstance(rows, list):
        return []

    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        report_summary = row.get("report_summary") if isinstance(row.get("report_summary"), dict) else {}
        usage = report_summary.get("usage") if isinstance(report_summary.get("usage"), dict) else {}
        result.append({
            "task_id": str(row.get("id") or ""),
            "category": str(row.get("category") or ""),
            "tags": list(row.get("tags") or []),
            "variant": variant,
            "context_governance": "off" if variant == "context_off" else "full",
            "context_sensitive": _is_context_sensitive_row(row),
            "task_completion_pass": _task_completion_pass(row),
            "original_passed": bool(row.get("passed")),
            "verifier_passed": bool(row.get("verifier_passed")),
            "within_budget": bool(row.get("within_budget")),
            "non_failure_stop_reason": bool(row.get("non_failure_stop_reason")),
            "stop_reason": str(row.get("stop_reason") or ""),
            "failure_category": _task_completion_failure_category(row),
            "tool_steps": int(row.get("tool_steps", 0) or 0),
            "attempts": int(row.get("attempts", 0) or 0),
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "estimated_cost_usd": float(usage.get("estimated_cost_usd", 0.0) or 0.0),
            "large_result_persist_count": int(row.get("large_result_persist_count", 0) or 0),
            "tool_history_snip_count": int(row.get("tool_history_snip_count", 0) or 0),
            "context_compact_count": int(row.get("context_compact_count", 0) or 0),
            "context_contract_met": bool(row.get("context_contract_met", True)),
        })
    return result


def _task_completion_pass(row: dict[str, Any]) -> bool:
    if "task_completion_pass" in row:
        return bool(row["task_completion_pass"])
    non_context_specialty = bool(
        row.get("security_contract_met", True)
        and row.get("memory_contract_met", True)
        and row.get("resume_contract_met", True)
        and row.get("tool_path_limit_contract_met", True)
    )
    return bool(
        row.get("nanocode_returncode") == 0
        and row.get("verifier_passed")
        and row.get("report_exists")
        and row.get("report_parse_valid")
        and row.get("expected_artifact_exists")
        and row.get("trace_contract_met")
        and row.get("within_budget")
        and row.get("non_failure_stop_reason")
        and row.get("allowed_tools_enforced")
        and non_context_specialty
    )


def _task_completion_failure_category(row: dict[str, Any]) -> str:
    if _task_completion_pass(row):
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
    if not row.get("security_contract_met", True):
        return "security_contract_failed"
    if not row.get("memory_contract_met", True):
        return "memory_contract_failed"
    if not row.get("resume_contract_met", True):
        return "resume_contract_failed"
    if not row.get("tool_path_limit_contract_met", True):
        return "tool_path_limit_contract_failed"
    return "unknown"


def _is_context_sensitive_row(row: dict[str, Any]) -> bool:
    tags = set(row.get("tags") or [])
    return str(row.get("category") or "") == "context-governance" or bool(tags & CONTEXT_SENSITIVE_TAGS)


def _context_task_variant_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    task_passes = sum(1 for row in rows if row["task_completion_pass"])
    original_passes = sum(1 for row in rows if row["original_passed"])
    verifier_passes = sum(1 for row in rows if row["verifier_passed"])
    within_budget = sum(1 for row in rows if row["within_budget"])
    return {
        "run_count": total,
        "task_completion_pass_count": task_passes,
        "task_completion_pass_rate": _safe_rate(task_passes, total),
        "original_pass_count": original_passes,
        "original_pass_rate": _safe_rate(original_passes, total),
        "verifier_pass_count": verifier_passes,
        "verifier_pass_rate": _safe_rate(verifier_passes, total),
        "within_budget_count": within_budget,
        "within_budget_rate": _safe_rate(within_budget, total),
        "avg_tool_steps": _mean(int(row["tool_steps"]) for row in rows),
        "avg_attempts": _mean(int(row["attempts"]) for row in rows),
        "avg_input_tokens": _mean(int(row["input_tokens"]) for row in rows),
        "avg_output_tokens": _mean(int(row["output_tokens"]) for row in rows),
        "total_input_tokens": sum(int(row["input_tokens"]) for row in rows),
        "total_output_tokens": sum(int(row["output_tokens"]) for row in rows),
        "total_estimated_cost_usd": sum(float(row["estimated_cost_usd"]) for row in rows),
        "large_result_persist_count": sum(int(row["large_result_persist_count"]) for row in rows),
        "tool_history_snip_count": sum(int(row["tool_history_snip_count"]) for row in rows),
        "context_compact_count": sum(int(row["context_compact_count"]) for row in rows),
        "stop_reason_counts": dict(sorted(Counter(row["stop_reason"] for row in rows).items())),
        "failure_category_counts": dict(sorted(Counter(
            row["failure_category"] for row in rows if row["failure_category"]
        ).items())),
    }


def _context_task_deltas(variants: dict[str, dict[str, Any]]) -> dict[str, Any]:
    on = variants.get("context_on") or {}
    off = variants.get("context_off") or {}
    return {
        "task_completion_pass_rate_delta_on_minus_off": float(on.get("task_completion_pass_rate", 0.0) or 0.0)
        - float(off.get("task_completion_pass_rate", 0.0) or 0.0),
        "verifier_pass_rate_delta_on_minus_off": float(on.get("verifier_pass_rate", 0.0) or 0.0)
        - float(off.get("verifier_pass_rate", 0.0) or 0.0),
        "avg_input_tokens_delta_off_minus_on": float(off.get("avg_input_tokens", 0.0) or 0.0)
        - float(on.get("avg_input_tokens", 0.0) or 0.0),
        "avg_tool_steps_delta_off_minus_on": float(off.get("avg_tool_steps", 0.0) or 0.0)
        - float(on.get("avg_tool_steps", 0.0) or 0.0),
    }


def _context_task_pico_metrics(result: dict[str, Any]) -> dict[str, Any]:
    variants = result.get("variants") if isinstance(result.get("variants"), dict) else {}
    context_variants = (
        result.get("context_sensitive_variants")
        if isinstance(result.get("context_sensitive_variants"), dict)
        else {}
    )
    return {
        "all_tasks": {
            variant: {
                "task_completion_pass_rate": data.get("task_completion_pass_rate", 0.0),
                "verifier_pass_rate": data.get("verifier_pass_rate", 0.0),
                "within_budget_rate": data.get("within_budget_rate", 0.0),
                "avg_input_tokens": data.get("avg_input_tokens", 0.0),
                "avg_tool_steps": data.get("avg_tool_steps", 0.0),
            }
            for variant, data in variants.items()
        },
        "context_sensitive_tasks": {
            variant: {
                "task_completion_pass_rate": data.get("task_completion_pass_rate", 0.0),
                "verifier_pass_rate": data.get("verifier_pass_rate", 0.0),
                "within_budget_rate": data.get("within_budget_rate", 0.0),
                "avg_input_tokens": data.get("avg_input_tokens", 0.0),
                "avg_tool_steps": data.get("avg_tool_steps", 0.0),
            }
            for variant, data in context_variants.items()
        },
        "deltas": result.get("deltas") or {},
        "context_sensitive_deltas": result.get("context_sensitive_deltas") or {},
    }


def run_memory_ablation(
    *,
    run_root: Path,
    repetitions: int = 5,
    benchmark_artifact: dict[str, Any] | None = None,
    task_file: Path | None = None,
    timeout: int = 180,
    model: str | None = None,
    stream: bool = False,
    execute: bool = False,
) -> dict[str, Any]:
    """Measure memory behavior from real benchmark rows when available."""
    if execute and task_file is not None:
        benchmark_artifact = _run_memory_ablation_benchmark(
            run_root=run_root,
            task_file=task_file,
            repetitions=repetitions,
            timeout=timeout,
            model=model,
            stream=stream,
        )
    rows = _memory_rows_from_benchmark(benchmark_artifact)
    variants = {
        variant: _memory_variant_metrics([row for row in rows if row["variant"] == variant])
        for variant in MEMORY_VARIANTS
    }
    category_counts = Counter(row["category"] for row in rows)
    status = MEASURED if rows else NOT_MEASURED
    result = {
        "schema_version": 2,
        "suite": "working_memory_ablation",
        "status": status,
        "method": "trace_based_memory_rows_from_local_fixture_benchmark",
        "task_count": len({row["task_id"] for row in rows}),
        "repetitions": 0,
        "runs_per_variant": {variant: variants[variant]["run_count"] for variant in MEMORY_VARIANTS},
        "category_counts": dict(sorted(category_counts.items())),
        "variants": variants,
        "rows": rows,
    }
    result["pico_metrics"] = {
        variant: {
            "repeated_reads": data["repeated_reads"],
            "avg_tool_steps": data["avg_tool_steps"],
            "avg_attempts": data["avg_attempts"],
            "correct_rate": data["correct_rate"],
            "memory_hit_rate": data["memory_hit_rate"],
        }
        for variant, data in variants.items()
    }
    benchmark_artifacts.write_json(run_root / "memory-ablation-v2.json", result)
    return result


def _run_memory_ablation_benchmark(
    *,
    run_root: Path,
    task_file: Path,
    repetitions: int,
    timeout: int,
    model: str | None,
    stream: bool,
) -> dict[str, Any]:
    runner = _load_runner()
    benchmark = runner.load_benchmark(task_file)
    base_tasks = [task for task in benchmark["tasks"] if task.get("category") == "memory"]
    generated_tasks: list[dict[str, Any]] = []
    for repetition in range(1, repetitions + 1):
        for task in base_tasks:
            case = str(task.get("memory_case") or "")
            if case in {"fact_lookup", "edit_dependency"}:
                generated_tasks.append(_memory_variant_task(task, variant="memory_on", repetition=repetition))
                generated_tasks.append(_memory_variant_task(task, variant="memory_off", repetition=repetition))
            elif case == "conflict_guard":
                generated_tasks.append(_memory_variant_task(task, variant="memory_irrelevant", repetition=repetition))

    generated_path = run_root / "memory-ablation-tasks.json"
    benchmark_artifacts.write_json(generated_path, {
        "schema_version": 1,
        "description": "Generated memory on/off/irrelevant ablation tasks.",
        "tasks": generated_tasks,
    })
    return runner.run_benchmark(
        Namespace(
            task_file=str(generated_path),
            output_root=str(run_root / "memory-ablation-runs"),
            run_name="memory-variants",
            task_id=None,
            suite="all",
            limit=None,
            timeout=timeout,
            model=model,
            stream=stream,
            dry_run=False,
        )
    )


def _memory_variant_task(task: dict[str, Any], *, variant: str, repetition: int) -> dict[str, Any]:
    row = dict(task)
    row["id"] = f"{task['id']}__{variant}__r{repetition}"
    row["ablation_variant"] = variant
    row["ablation_repetition"] = repetition
    row["suite"] = "all"
    if variant == "memory_off":
        row.pop("memory_setup", None)
        row["tags"] = [tag for tag in row.get("tags", []) if tag != "memory"]
        row["expected_artifact"] = str(row.get("expected_artifact") or "") + " with memory disabled"
    return row


def _memory_rows_from_benchmark(benchmark_artifact: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(benchmark_artifact, dict):
        return []
    rows = benchmark_artifact.get("rows")
    if not isinstance(rows, list):
        return []

    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not (row.get("memory_task") or row.get("category") == "memory" or "memory" in set(row.get("tags") or [])):
            continue
        case = str(row.get("memory_case") or "")
        variant = str(row.get("ablation_variant") or "")
        if not variant:
            variant = "memory_irrelevant" if case == "conflict_guard" else "memory_on"
        repeated_reads = _memory_repeated_reads(row, case)
        correct = bool(row.get("verifier_passed"))
        memory_hit = bool(
            correct
            and variant == "memory_on"
            and case in {"fact_lookup", "edit_dependency"}
            and repeated_reads == 0
            and (row.get("memory_fact_hit") or row.get("memory_edit_dependency_success"))
        )
        result.append({
            "task_id": str(row.get("id") or ""),
            "category": case or "memory",
            "variant": variant,
            "repetition": int(row.get("ablation_repetition", 1) or 1),
            "relevant_memory_loaded": variant == "memory_on",
            "irrelevant_memory_loaded": variant == "memory_irrelevant",
            "fallback_read_count": int(row.get("memory_fallback_read_count", 0) or 0),
            "current_truth_read_count": int(row.get("memory_current_truth_read_count", 0) or 0),
            "repeated_reads": repeated_reads,
            "tool_steps": int(row.get("tool_steps", 0) or 0),
            "attempts": int(row.get("attempts", 0) or 0),
            "correct": correct,
            "memory_hit": memory_hit,
            "passed": bool(row.get("passed")),
        })
    return result


def _memory_repeated_reads(row: dict[str, Any], case: str) -> int:
    if case == "conflict_guard":
        return int(row.get("memory_current_truth_read_count", 0) or 0)
    return int(row.get("memory_fallback_read_count", 0) or 0)


def _memory_variant_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    correct = sum(1 for row in rows if row["correct"])
    hits = sum(1 for row in rows if row["memory_hit"])
    repeated_reads = sum(int(row["repeated_reads"]) for row in rows)
    return {
        "run_count": total,
        "status": MEASURED if rows else NOT_MEASURED,
        "repeated_reads": repeated_reads,
        "avg_repeated_reads": _mean(int(row["repeated_reads"]) for row in rows),
        "avg_tool_steps": _mean(int(row["tool_steps"]) for row in rows),
        "avg_attempts": _mean(int(row["attempts"]) for row in rows),
        "correct_count": correct,
        "correct_rate": _safe_rate(correct, total),
        "memory_hit_count": hits,
        "memory_hit_rate": _safe_rate(hits, total),
    }


def run_recovery_ablation(
    *,
    run_root: Path,
    repetitions: int = 3,
    benchmark_artifact: dict[str, Any] | None = None,
    task_file: Path | None = None,
    timeout: int = 180,
    model: str | None = None,
    stream: bool = False,
    execute: bool = False,
) -> dict[str, Any]:
    """Evaluate resume primitives separately from end-to-end benchmark rows."""
    primitive_rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="nanocode-recovery-ablation-") as tmp:
        root = Path(tmp)
        for task_id, category in RECOVERY_TASKS:
            for variant in RESUME_VARIANTS:
                for repetition in range(repetitions):
                    primitive_rows.append(_recovery_row(root, task_id, category, variant, repetition))

    primitive_variants = {
        variant: _recovery_variant_metrics([row for row in primitive_rows if row["variant"] == variant])
        for variant in RESUME_VARIANTS
    }
    e2e_artifact = benchmark_artifact
    if execute and task_file is not None:
        e2e_artifact = _run_resume_ablation_benchmark(
            run_root=run_root,
            task_file=task_file,
            repetitions=repetitions,
            timeout=timeout,
            model=model,
            stream=stream,
        )
    e2e_rows = _recovery_rows_from_benchmark(e2e_artifact)
    e2e_variants = {
        variant: _recovery_variant_metrics([row for row in e2e_rows if row["variant"] == variant])
        for variant in RESUME_VARIANTS
    }
    result = {
        "schema_version": 2,
        "suite": "recovery_resume_ablation",
        "method": "session_log_primitives_plus_optional_e2e_local_fixture_rows",
        "primitive_task_count": len(RECOVERY_TASKS),
        "e2e_task_count": len({row["task_id"] for row in e2e_rows}),
        "repetitions": repetitions,
        "primitive_runs_per_variant": len(RECOVERY_TASKS) * repetitions,
        "e2e_status": MEASURED if e2e_rows else NOT_MEASURED,
        "primitive_variants": primitive_variants,
        "e2e_variants": e2e_variants,
        "variants": e2e_variants if e2e_rows else primitive_variants,
        "primitive_rows": primitive_rows,
        "e2e_rows": e2e_rows,
    }
    result["pico_metrics"] = {
        "primitive": {
            variant: {
                "resume_success_rate": data["resume_success_rate"],
                "orphan_repair_count": data["orphan_repair_count"],
            }
            for variant, data in primitive_variants.items()
        },
        "e2e": {
            variant: {
                "resume_success_rate": data["resume_success_rate"],
                "orphan_repair_count": data["orphan_repair_count"],
            }
            for variant, data in e2e_variants.items()
        }
    }
    benchmark_artifacts.write_json(run_root / "recovery-ablation-v2.json", result)
    return result


def _run_resume_ablation_benchmark(
    *,
    run_root: Path,
    task_file: Path,
    repetitions: int,
    timeout: int,
    model: str | None,
    stream: bool,
) -> dict[str, Any]:
    runner = _load_runner()
    benchmark = runner.load_benchmark(task_file)
    hidden = next((task for task in benchmark["tasks"] if task.get("id") == "resume_hidden_goal"), None)
    if hidden is None:
        return {"rows": []}

    generated_tasks: list[dict[str, Any]] = []
    for repetition in range(1, repetitions + 1):
        generated_tasks.append(_resume_variant_task(hidden, variant="resume_enabled", repetition=repetition))
        generated_tasks.append(_resume_variant_task(hidden, variant="resume_disabled", repetition=repetition))

    generated_path = run_root / "resume-ablation-tasks.json"
    benchmark_artifacts.write_json(generated_path, {
        "schema_version": 1,
        "description": "Generated checkpoint resume enabled/disabled ablation tasks.",
        "tasks": generated_tasks,
    })
    return runner.run_benchmark(
        Namespace(
            task_file=str(generated_path),
            output_root=str(run_root / "resume-ablation-runs"),
            run_name="resume-variants",
            task_id=None,
            suite="all",
            limit=None,
            timeout=timeout,
            model=model,
            stream=stream,
            dry_run=False,
        )
    )


def _resume_variant_task(task: dict[str, Any], *, variant: str, repetition: int) -> dict[str, Any]:
    row = dict(task)
    row["id"] = f"{task['id']}__{variant}__r{repetition}"
    row["ablation_variant"] = variant
    row["ablation_repetition"] = repetition
    row["suite"] = "all"
    if variant == "resume_disabled":
        row["skip_resume_seed"] = True
        row["expected_artifact"] = "resume disabled observes no checkpoint and leaves the hidden target unchanged"
        row["verifier"] = (
            "python3 -c \"from pathlib import Path; "
            "assert Path('hidden_resume_target.txt').read_text().strip() == 'hidden_resume_target=todo'\""
        )
    return row


def _recovery_rows_from_benchmark(benchmark_artifact: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(benchmark_artifact, dict):
        return []
    rows = benchmark_artifact.get("rows")
    if not isinstance(rows, list):
        return []

    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not (row.get("scenario") == "resume" or "resume" in set(row.get("tags") or [])):
            continue
        category = str(row.get("recovery_case_category") or "checkpoint_resume")
        result.append({
            "task_id": str(row.get("id") or ""),
            "category": category,
            "variant": str(row.get("ablation_variant") or "resume_enabled"),
            "repetition": int(row.get("ablation_repetition", 1) or 1),
            "checkpoint_available": bool(row.get("resume_session_exists")),
            "session_restored": bool(row.get("resume_output_restored")),
            "orphan_repaired": bool(row.get("resume_orphan_repaired")),
            "resume_success": bool(row.get("passed") and row.get("resume_contract_met")),
        })
    return result


def _recovery_row(root: Path, task_id: str, category: str, variant: str, repetition: int) -> dict[str, Any]:
    if variant == "resume_disabled":
        return {
            "task_id": task_id,
            "category": category,
            "variant": variant,
            "repetition": repetition + 1,
            "checkpoint_available": False,
            "session_restored": False,
            "orphan_repaired": False,
            "resume_success": False,
        }

    restored, orphan_repaired = _exercise_session_resume_primitive(root, task_id, category, repetition)
    success = restored

    if category == "orphaned_tool_call":
        success = restored and orphan_repaired

    return {
        "task_id": task_id,
        "category": category,
        "variant": variant,
        "repetition": repetition + 1,
        "checkpoint_available": True,
        "session_restored": restored,
        "orphan_repaired": orphan_repaired,
        "resume_success": success,
    }


def _exercise_session_resume_primitive(root: Path, task_id: str, category: str, repetition: int) -> tuple[bool, bool]:
    from nanocode.agent.runtime_management.persistence.session_log import INTERRUPTED_TOOL_RESULT, SessionLog
    from nanocode.agent.types import ConversationHistory, ToolCall, ToolResult

    session_id = f"ablation_{task_id}_{repetition}"
    log = SessionLog(session_id, root=root / "sessions")
    log.ensure_session({
        "workspace": str(root / "workspace"),
        "provider": "ablation",
        "model": "deterministic",
    })
    history = ConversationHistory()
    history.add_user(f"Resume task {task_id}.")
    if category == "orphaned_tool_call":
        history.add_assistant("", [ToolCall(id="call_orphan", name="edit_file", input={"file_path": "target.txt"})])
    else:
        history.add_assistant(f"Checkpoint for {task_id}.")
        if category == "checkpoint_resume":
            call = ToolCall(id="call_done", name="edit_file", input={"file_path": "target.txt"})
            history.add_tool_results([(call, ToolResult("target=done"))])
    log.commit(history, reason="ablation_seed", run_id=f"run_{task_id}")
    log.append_checkpoint(reason=category, run_id=f"run_{task_id}")
    loaded = log.load(repair=True)
    encoded = json.dumps(loaded.snapshot(), ensure_ascii=False)
    return loaded.count() > 0, INTERRUPTED_TOOL_RESULT in encoded


def _recovery_variant_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    successes = sum(1 for row in rows if row["resume_success"])
    return {
        "run_count": total,
        "resume_success_count": successes,
        "resume_success_rate": _safe_rate(successes, total),
        "orphan_repair_count": sum(1 for row in rows if row["orphan_repaired"]),
    }


def run_ablation(args: Namespace) -> dict[str, Any]:
    run_root = Path(args.output_root).resolve() / str(args.run_name)
    run_root.mkdir(parents=True, exist_ok=True)

    harness = run_harness_regression(
        run_root=run_root,
        task_file=Path(args.task_file),
        suite=args.suite,
        timeout=int(args.timeout),
        model=args.model,
        stream=bool(args.stream),
        skip=bool(args.skip_harness or args.dry_run),
        harness_artifact=Path(args.harness_artifact) if args.harness_artifact else None,
    )
    benchmark_artifact = _benchmark_artifact_for_ablation(harness)
    context = run_context_ablation(run_root=run_root)
    context_on_artifact = benchmark_artifact
    context_on_arg = getattr(args, "context_on_artifact", None)
    if context_on_artifact is None and context_on_arg:
        context_on_artifact = benchmark_artifacts.read_json_optional(Path(context_on_arg))
    context_task = run_context_task_completion_ablation(
        run_root=run_root,
        task_file=Path(args.task_file),
        suite=args.suite,
        timeout=int(args.timeout),
        model=args.model,
        stream=bool(args.stream),
        execute=bool(getattr(args, "run_context_task_ablation", False) and not args.dry_run),
        context_on_artifact=context_on_artifact,
    )
    memory = run_memory_ablation(
        run_root=run_root,
        repetitions=int(args.repetitions),
        benchmark_artifact=benchmark_artifact,
        task_file=Path(args.task_file),
        timeout=int(args.timeout),
        model=args.model,
        stream=bool(args.stream),
        execute=bool(args.run_memory_ablation and not args.dry_run),
    )
    recovery = run_recovery_ablation(
        run_root=run_root,
        repetitions=int(args.recovery_repetitions),
        benchmark_artifact=benchmark_artifact,
        task_file=Path(args.task_file),
        timeout=int(args.timeout),
        model=args.model,
        stream=bool(args.stream),
        execute=bool(args.run_resume_ablation and not args.dry_run),
    )

    artifact = {
        "schema_version": 2,
        "created_at": _now_iso(),
        "benchmark": {
            "name": "nanocode-local-fixture-ablation",
            "run_name": str(args.run_name),
            "run_root": str(run_root),
        },
        "summary": _ablation_summary(harness, context, context_task, memory, recovery),
        "suites": {
            "harness_regression": harness,
            "context_ablation": context,
            "context_task_completion_ablation": context_task,
            "working_memory_ablation": memory,
            "recovery_resume_ablation": recovery,
        },
    }
    benchmark_artifacts.write_json(run_root / "ablation.json", artifact)
    (run_root / "ablation-report.md").write_text(_render_report(artifact), encoding="utf-8")
    (run_root / "DATA_PROVENANCE.md").write_text(_render_provenance(artifact), encoding="utf-8")
    return artifact


def _benchmark_artifact_for_ablation(harness: dict[str, Any]) -> dict[str, Any] | None:
    path = str(harness.get("benchmark_path") or "")
    if not path:
        return None
    return benchmark_artifacts.read_json_optional(Path(path))


def _ablation_summary(
    harness: dict[str, Any],
    context: dict[str, Any],
    context_task: dict[str, Any],
    memory: dict[str, Any],
    recovery: dict[str, Any],
) -> dict[str, Any]:
    memory_variants = memory.get("variants") if isinstance(memory.get("variants"), dict) else {}
    context_task_variants = (
        context_task.get("variants")
        if isinstance(context_task.get("variants"), dict)
        else {}
    )
    context_on = context_task_variants.get("context_on") or {}
    context_off = context_task_variants.get("context_off") or {}
    recovery_e2e = recovery.get("e2e_variants") if isinstance(recovery.get("e2e_variants"), dict) else {}
    recovery_primitive = recovery.get("primitive_variants") if isinstance(recovery.get("primitive_variants"), dict) else {}
    recovery_enabled = recovery_e2e.get("resume_enabled") or recovery_primitive.get("resume_enabled") or {}
    return {
        "harness_pass_rate": harness.get("pico_metrics", {}).get("pass_rate", 0.0),
        "harness_task_count": harness.get("pico_metrics", {}).get("task_count", 0),
        "context_avg_prompt_estimated_token_compression_ratio": context.get(
            "avg_prompt_estimated_token_compression_ratio",
            0.0,
        ),
        "context_baseline_avg_prompt_estimated_token_compression_ratio": context.get(
            "baseline_avg_prompt_estimated_token_compression_ratio",
            0.0,
        ),
        "context_pressure_avg_prompt_estimated_token_compression_ratio": context.get(
            "pressure_avg_prompt_estimated_token_compression_ratio",
            0.0,
        ),
        "context_current_request_preserved_rate": context.get("current_request_preserved_rate", 0.0),
        "context_pressure_compact_count": context.get("pressure_context_compact_count", 0),
        "context_forced_compact_count": context.get("forced_context_compact_count", 0),
        "context_task_status": context_task.get("status", NOT_MEASURED),
        "context_on_task_completion_pass_rate": context_on.get("task_completion_pass_rate", 0.0),
        "context_off_task_completion_pass_rate": context_off.get("task_completion_pass_rate", 0.0),
        "context_task_avg_input_tokens_delta_off_minus_on": (context_task.get("deltas") or {}).get(
            "avg_input_tokens_delta_off_minus_on",
            0.0,
        ),
        "memory_status": memory.get("status", NOT_MEASURED),
        "memory_on_repeated_reads": (memory_variants.get("memory_on") or {}).get("repeated_reads", 0),
        "memory_off_repeated_reads": (memory_variants.get("memory_off") or {}).get("repeated_reads", 0),
        "memory_irrelevant_repeated_reads": (memory_variants.get("memory_irrelevant") or {}).get("repeated_reads", 0),
        "resume_e2e_status": recovery.get("e2e_status", NOT_MEASURED),
        "resume_enabled_success_rate": recovery_enabled.get("resume_success_rate", 0.0),
    }


def _render_report(artifact: dict[str, Any]) -> str:
    suites = artifact["suites"]
    harness = suites["harness_regression"]
    context = suites["context_ablation"]
    context_task = suites["context_task_completion_ablation"]
    memory = suites["working_memory_ablation"]
    recovery = suites["recovery_resume_ablation"]
    lines = [
        "# NanoCode Local Fixture Ablation Report",
        "",
        f"- Created at: {artifact['created_at']}",
        f"- Run root: `{artifact['benchmark']['run_root']}`",
        "",
        "## Harness Regression",
        "",
        f"- Status: `{harness['status']}`",
        f"- Task count: {harness['pico_metrics']['task_count']}",
        f"- Pass rate: {_format_rate(harness['pico_metrics']['pass_rate'])}",
        f"- Within budget rate: {_format_rate(harness['pico_metrics']['within_budget_rate'])}",
        f"- Verifier pass rate: {_format_rate(harness['pico_metrics']['verifier_pass_rate'])}",
        "",
        "## Context Ablation",
        "",
        f"- Config count: {context['config_count']}",
        f"- Primary profile: `{context['primary_profile']}`",
        f"- Scenario ratio: `{context['scenario_ratio']}`",
        f"- Tool Result Budget mix: `{context['tool_result_budget_ratio']}` "
        "(small read / medium read / large search / CI log)",
        f"- Measurement unit: `{context['measurement_unit']}`",
        "- Token counts use NanoCode's provider-neutral local estimator, not provider-exact billing tokens.",
        "- Overall average raw estimated tokens: "
        f"{context['context_management_four_level']['avg_raw_prompt_estimated_tokens']:.1f}",
        "- Overall average governed estimated tokens: "
        f"{context['context_management_four_level']['avg_governed_prompt_estimated_tokens']:.1f}",
        "- Overall average estimated-token compression ratio: "
        f"{_format_rate(context['context_management_four_level']['avg_prompt_estimated_token_compression_ratio'])}",
        "- Baseline average estimated-token compression ratio: "
        f"{_format_rate(context['baseline_avg_prompt_estimated_token_compression_ratio'])}",
        "- Pressure scenarios average estimated-token compression ratio: "
        f"{_format_rate(context['pressure_avg_prompt_estimated_token_compression_ratio'])}",
        f"- Current request preserved rate: {_format_rate(context['current_request_preserved_rate'])}",
        f"- Large result persist count: {context['large_result_persist_count']}",
        f"- Tool result snip count: {context['snipped_tool_result_count']}",
        f"- Pressure-triggered compact count: {context['pressure_context_compact_count']}",
        f"- Post-compact context restored rate: {_format_rate(context['post_compact_context_restored_rate'])}",
        "",
        "### Context Scenarios",
        "",
        "| Scenario | Configs | Avg Estimated Token Compression | Trigger Rate | Persist Rate | Snip Rate | Compact Rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for scenario, data in context["scenarios"].items():
        lines.append(
            f"| {scenario} | {data['config_count']} | "
            f"{_format_rate(data['avg_prompt_estimated_token_compression_ratio'])} | "
            f"{_format_rate(data['compression_triggered_rate'])} | "
            f"{_format_rate(data['large_result_persist_trigger_rate'])} | "
            f"{_format_rate(data['tool_history_snip_trigger_rate'])} | "
            f"{_format_rate(data['context_compact_trigger_rate'])} |"
        )
    lines.extend([
        "",
        "### Tool Result Budget Mix",
        "",
        "| Case | Configs | Avg Estimated Token Compression | Persist Rate |",
        "| --- | ---: | ---: | ---: |",
    ])
    for name, data in context["tool_result_budget_mix"].items():
        lines.append(
            f"| {name} | {data['config_count']} | "
            f"{_format_rate(data['avg_prompt_estimated_token_compression_ratio'])} | "
            f"{_format_rate(data['large_result_persist_trigger_rate'])} |"
        )
    lines.extend([
        "",
        "## Context Task Completion Ablation",
        "",
        f"- Status: `{context_task['status']}`",
        "- `task_completion_pass` excludes context-governance event contracts so context_off is not penalized for disabling those events.",
        "",
        "### All Tasks",
        "",
        "| Variant | Runs | Task Completion | Verifier Pass | Within Budget | Avg Input Tokens | Avg Tool Steps | Persist | Snip | Compact |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for variant, data in context_task["variants"].items():
        lines.append(
            f"| {variant} | {data['run_count']} | {_format_rate(data['task_completion_pass_rate'])} | "
            f"{_format_rate(data['verifier_pass_rate'])} | {_format_rate(data['within_budget_rate'])} | "
            f"{data['avg_input_tokens']:.1f} | {data['avg_tool_steps']:.2f} | "
            f"{data['large_result_persist_count']} | {data['tool_history_snip_count']} | "
            f"{data['context_compact_count']} |"
        )
    lines.extend([
        "",
        "### Context-Sensitive Tasks",
        "",
        "| Variant | Runs | Task Completion | Verifier Pass | Within Budget | Avg Input Tokens | Avg Tool Steps | Persist | Snip | Compact |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for variant, data in context_task["context_sensitive_variants"].items():
        lines.append(
            f"| {variant} | {data['run_count']} | {_format_rate(data['task_completion_pass_rate'])} | "
            f"{_format_rate(data['verifier_pass_rate'])} | {_format_rate(data['within_budget_rate'])} | "
            f"{data['avg_input_tokens']:.1f} | {data['avg_tool_steps']:.2f} | "
            f"{data['large_result_persist_count']} | {data['tool_history_snip_count']} | "
            f"{data['context_compact_count']} |"
        )
    lines.extend([
        "",
        "## Working Memory Ablation",
        "",
        f"- Status: `{memory['status']}`",
        "- Metrics are populated from real local-fixture benchmark rows when available.",
        "",
        "| Variant | Repeated Reads | Avg Tool Steps | Avg Attempts | Correct Rate | Memory Hit Rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ])
    for variant, data in memory["variants"].items():
        lines.append(
            f"| {variant} | {data['repeated_reads']} | {data['avg_tool_steps']:.2f} | "
            f"{data['avg_attempts']:.2f} | {_format_rate(data['correct_rate'])} | "
            f"{_format_rate(data['memory_hit_rate'])} |"
        )
    lines.extend([
        "",
        "## Recovery / Resume Ablation",
        "",
        f"- End-to-end status: `{recovery['e2e_status']}`",
        "",
        "### End-to-End Rows",
        "",
        "| Variant | Runs | Resume Success | Orphan Repairs |",
        "| --- | ---: | ---: | ---: |",
    ])
    for variant, data in recovery["e2e_variants"].items():
        lines.append(
            f"| {variant} | {data['run_count']} | {_format_rate(data['resume_success_rate'])} | "
            f"{data['orphan_repair_count']} |"
        )
    lines.extend([
        "",
        "### Session Log Primitive",
        "",
        "| Variant | Runs | Resume Success | Orphan Repairs |",
        "| --- | ---: | ---: | ---: |",
    ])
    for variant, data in recovery["primitive_variants"].items():
        lines.append(
            f"| {variant} | {data['run_count']} | {_format_rate(data['resume_success_rate'])} | "
            f"{data['orphan_repair_count']} |"
        )
    lines.extend([
        "",
        "Recovery ablation only includes NanoCode's implemented resume contract; unsupported drift/stale cases are not measured.",
        "",
    ])
    return "\n".join(lines)


def _render_provenance(artifact: dict[str, Any]) -> str:
    suites = artifact["suites"]
    return "\n".join([
        "# Data Provenance",
        "",
        "This ablation run is scoped to NanoCode local-fixture implementation behavior.",
        "",
        "## Harness Regression",
        "",
        f"- Source: `{suites['harness_regression']['source']}`",
        f"- Benchmark artifact: `{suites['harness_regression'].get('benchmark_path', '')}`",
        "- Metrics reuse the existing local-fixture scorecards.",
        "",
        "## Context Ablation",
        "",
        "- Uses 40 deterministic cases split 4:3:2:1 across no-compression baseline, Tool Result Budget, Tool History Snip, and Context Compact.",
        "- Tool Result Budget uses a 5:3:2:2 mix of small-over-threshold file reads, medium-over-threshold file reads, large grep/search output, and CI logs.",
        "- Baseline cases verify that small normal tasks do not trigger persistence, snip, or compact.",
        "- Level 1 cases measure ToolRuntime.execute_one(read_file/grep_search/run_shell), including validation, permissions, sandboxed shell execution, and large-result persistence.",
        "- Level 2 cases use Compressor.prepare_context_for_provider() under pressure to trigger Tool History Snip without compact.",
        "- Level 3 cases use long-chain realistic workflows under a constrained context window to trigger Context Compact.",
        "- Overall, baseline, and pressure-scenario compression ratios are reported separately to avoid treating stress compression as daily average behavior.",
        "- Token counts use NanoCode's provider-neutral local estimator from `agent.budget`; they are not provider-exact billing tokens.",
        "- The current request is counted as preserved only when the exact request text remains after natural governance.",
        "",
        "## Context Task Completion Ablation",
        "",
        "- Runs the same local-fixture task suite with context governance on/off when explicitly enabled.",
        "- `context_on` uses normal NanoCode behavior; `context_off` sets `NANO_CODE_CONTEXT_GOVERNANCE=off`.",
        "- Task completion pass keeps verifier, budget, stop reason, trace/report, allowlist, security, memory, resume, and tool-path contracts, but excludes context-governance event contracts.",
        "- Reports all tasks and context-sensitive tasks separately so non-context tasks do not dilute context behavior.",
        "",
        "## Working Memory Ablation",
        "",
        "- Uses real local-fixture benchmark rows when a harness benchmark artifact is available.",
        "- If no benchmark rows are available, the suite is marked not_measured instead of fabricating memory benefit.",
        "- `memory_hit` means: final result is correct, relevant memory was loaded, and the follow-up did not reread fallback files.",
        "- `memory_irrelevant` intentionally does not get credit for memory hits; correct behavior must fall back to the current source of truth.",
        "",
        "## Recovery / Resume Ablation",
        "",
        "- Reports end-to-end local-fixture resume rows separately from SessionLog primitive checks.",
        "- Measures checkpoint resume and orphaned tool-call repair only; workspace drift and stale reanchor are out of scope for this implementation contract.",
        "",
    ])


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run NanoCode local-fixture ablation experiments.")
    parser.add_argument("--task-file", default=str(BENCH_DIR / "tasks.json"))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-name", default=_default_run_name())
    parser.add_argument("--suite", default="all", choices=["core", "all", "security", "memory", "resume"])
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--model", default=None)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--recovery-repetitions", type=int, default=3)
    parser.add_argument("--skip-harness", action="store_true")
    parser.add_argument("--harness-artifact", default=None)
    parser.add_argument(
        "--run-context-task-ablation",
        action="store_true",
        help="Run full task completion context_on/context_off ablation.",
    )
    parser.add_argument(
        "--context-on-artifact",
        default=None,
        help="Optional existing benchmark.json for the context_on side of context task completion ablation.",
    )
    parser.add_argument("--run-memory-ablation", action="store_true", help="Run generated memory on/off/irrelevant tasks.")
    parser.add_argument("--run-resume-ablation", action="store_true", help="Run generated resume enabled/disabled tasks.")
    parser.add_argument("--dry-run", action="store_true", help="Write deterministic ablations without running NanoCode.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    artifact = run_ablation(args)
    print(f"Wrote ablation results to {artifact['benchmark']['run_root']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
