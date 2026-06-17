from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

RUNNER_PATH = Path(__file__).resolve().parents[2] / "benchmarks" / "local-fixture" / "run.py"
ARTIFACTS_PATH = Path(__file__).resolve().parents[2] / "benchmarks" / "local-fixture" / "artifacts.py"
METRICS_PATH = Path(__file__).resolve().parents[2] / "benchmarks" / "local-fixture" / "metrics.py"
REPORT_PATH = Path(__file__).resolve().parents[2] / "benchmarks" / "local-fixture" / "report.py"
ABLATION_PATH = Path(__file__).resolve().parents[2] / "benchmarks" / "local-fixture" / "ablation.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_runner():
    return _load_module(RUNNER_PATH, "local_fixture_runner")


def _load_ablation():
    return _load_module(ABLATION_PATH, "local_fixture_ablation")


class LocalFixtureBenchmarkTests(unittest.TestCase):
    def test_validates_implemented_task_set_and_writes_dry_run_artifact(self) -> None:
        runner = _load_runner()
        benchmark = runner.load_benchmark()
        tasks = benchmark["tasks"]

        self.assertEqual(benchmark["schema_version"], 1)
        self.assertEqual(len(tasks), 41)
        self.assertTrue(all(task.get("allowed_tools") for task in tasks))
        self.assertTrue(all(task.get("tags") for task in tasks))
        self.assertEqual(
            {task["category"] for task in tasks},
            {
                "documentation",
                "text-edit",
                "python-bugfix",
                "tool-boundary",
                "recovery",
                "structured-edit",
                "run-artifacts",
                "resume",
                "security",
                "memory",
                "context-governance",
                "permissions",
            },
        )

        task_ids = {task["id"] for task in tasks}
        self.assertIn("python_slugify_boundaries", task_ids)
        self.assertIn("context_tool_history_snip_realistic", task_ids)
        self.assertFalse(
            {
                "resume_partial_stale_single",
                "resume_workspace_mismatch_fingerprint",
                "resume_schema_mismatch_version",
                "resume_partial_success_tool",
                "security_path_escape_read",
                "security_symlink_escape",
                "security_search_escape",
                "security_repeated_identical_call",
                "security_timeout_out_of_range",
                "security_empty_delegate_task",
                "durable_memory_accept",
                "durable_memory_reject",
            }
            & task_ids
        )

        security_cases = [task for task in tasks if task.get("security_case")]
        self.assertEqual(len(security_cases), 7)
        self.assertTrue(all(task.get("security_expectation") for task in security_cases))
        self.assertEqual(
            {task["security_case"] for task in security_cases},
            {
                "approval_denied_shell",
                "read_only_write",
                "patch_nonunique",
                "patch_missing_new_text",
                "path_escape_write",
                "permission_dontask_edit_denied",
                "protected_path_write",
            },
        )
        self.assertEqual(len([task for task in tasks if task.get("memory_setup")]), 3)
        self.assertEqual(
            {task["memory_case"] for task in tasks if task.get("memory_setup")},
            {"fact_lookup", "edit_dependency", "conflict_guard"},
        )
        self.assertEqual(len([task for task in tasks if task.get("scenario") == "resume"]), 4)
        self.assertEqual(len([task for task in tasks if runner._task_matches_suite(task, "core")]), 34)
        self.assertEqual(len([task for task in tasks if runner._task_matches_suite(task, "security")]), 5)
        self.assertEqual(len([task for task in tasks if runner._task_matches_suite(task, "permissions")]), 2)
        self.assertEqual(len([task for task in tasks if runner._task_matches_suite(task, "all")]), 41)
        self.assertEqual(len([task for task in tasks if task["category"] == "python-bugfix"]), 6)
        self.assertEqual(len([task for task in tasks if task["category"] == "context-governance"]), 2)
        snip_task = next(task for task in tasks if task["id"] == "context_tool_history_snip_realistic")
        self.assertEqual(snip_task["context_window"], 70000)
        self.assertIn("tool-history-snip", snip_task["tags"])
        repeated = next(task for task in tasks if task["id"] == "repeated_read_budget_guard")
        self.assertEqual(
            repeated["tool_path_limits"],
            [{"tool": "read_file", "path": "repeat.txt", "max_count": 2, "max_pre_edit_count": 1, "max_post_edit_count": 1}],
        )

        with tempfile.TemporaryDirectory() as tmp:
            artifact = runner.run_benchmark(
                Namespace(
                    task_file=str(runner.DEFAULT_TASK_FILE),
                    output_root=tmp,
                    run_name="dry-run",
                    task_id=None,
                    suite="core",
                    limit=2,
                    timeout=5,
                    model=None,
                    stream=False,
                    dry_run=True,
                    context_governance="off",
                )
            )

            output = Path(tmp) / "dry-run" / "benchmark.json"
            self.assertTrue(output.exists())
            persisted = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(persisted, artifact)
            self.assertEqual(artifact["benchmark"]["task_count"], 2)
            self.assertEqual(artifact["benchmark"]["suite"], "core")
            self.assertEqual(artifact["runtime"]["context_governance"], "off")
            self.assertEqual(artifact["summary"]["selected_tasks"], 2)
            self.assertEqual(artifact["summary"]["executed_tasks"], 0)
            self.assertIn("benchmark_definition_id", artifact["reproducibility"])
            self.assertEqual(artifact["scorecards"]["harness_regression"]["task_count"], 0)
            self.assertNotIn("recovery_ablation", artifact["scorecards"])
            self.assertTrue((Path(tmp) / "dry-run" / "benchmark-core-report.md").exists())
            self.assertTrue((Path(tmp) / "dry-run" / "DATA_PROVENANCE.md").exists())

    def test_run_nanocode_passes_task_allowed_tools(self) -> None:
        runner = _load_runner()
        task = {
            "prompt": "hello",
            "step_budget": 3,
            "allowed_tools": ["read_file", "edit_file"],
        }

        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, "_run_subprocess") as run_subprocess:
            runner._run_nanocode(task, Path(tmp), timeout=5, model=None, stream=False)

        command = run_subprocess.call_args.args[0]
        self.assertIn("--yolo", command)
        self.assertIn("--allowed-tools", command)
        self.assertEqual(command[command.index("--allowed-tools") + 1], "read_file,edit_file")
        self.assertEqual(command[command.index("--max-turns") + 1], "3")

        task["max_turns"] = 2
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, "_run_subprocess") as run_subprocess:
            runner._run_nanocode(task, Path(tmp), timeout=5, model=None, stream=False)

        command = run_subprocess.call_args.args[0]
        self.assertEqual(command[command.index("--max-turns") + 1], "2")

    def test_run_nanocode_maps_task_permission_mode_to_cli_flags(self) -> None:
        runner = _load_runner()
        base_task = {
            "prompt": "hello",
            "step_budget": 3,
        }

        cases = [
            ("yolo", "--yolo", ["--dont-ask", "--accept-edits"]),
            ("dontAsk", "--dont-ask", ["--yolo", "--accept-edits"]),
            ("acceptEdits", "--accept-edits", ["--yolo", "--dont-ask"]),
            ("default", None, ["--yolo", "--dont-ask", "--accept-edits"]),
        ]

        for mode, expected, forbidden in cases:
            task = {**base_task, "permission_mode": mode}
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory() as tmp, patch.object(runner, "_run_subprocess") as run_subprocess:
                    runner._run_nanocode(task, Path(tmp), timeout=5, model=None, stream=False)

                command = run_subprocess.call_args.args[0]
                if expected is not None:
                    self.assertIn(expected, command)
                for flag in forbidden:
                    self.assertNotIn(flag, command)

    def test_run_nanocode_passes_context_window_override_in_env(self) -> None:
        runner = _load_runner()
        task = {
            "prompt": "hello",
            "step_budget": 3,
            "context_window": 70000,
        }

        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, "_run_subprocess") as run_subprocess:
            runner._run_nanocode(task, Path(tmp), timeout=5, model=None, stream=False)

        env = run_subprocess.call_args.kwargs["env"]
        self.assertEqual(env["NANO_CODE_CONTEXT_WINDOW"], "70000")

    def test_run_nanocode_passes_context_governance_override_in_env(self) -> None:
        runner = _load_runner()
        task = {
            "prompt": "hello",
            "step_budget": 3,
        }

        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, "_run_subprocess") as run_subprocess:
            runner._run_nanocode(
                task,
                Path(tmp),
                timeout=5,
                model=None,
                stream=False,
                context_governance="off",
            )

        env = run_subprocess.call_args.kwargs["env"]
        self.assertEqual(env["NANO_CODE_CONTEXT_GOVERNANCE"], "off")

    def test_run_benchmark_records_harness_error_and_continues(self) -> None:
        runner = _load_runner()

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runner, "run_task", side_effect=RuntimeError("synthetic harness failure")),
        ):
            artifact = runner.run_benchmark(
                Namespace(
                    task_file=str(runner.DEFAULT_TASK_FILE),
                    output_root=tmp,
                    run_name="harness-error",
                    task_id=["readme_intro_locked"],
                    suite="core",
                    limit=None,
                    timeout=5,
                    model=None,
                    stream=False,
                    dry_run=False,
                )
            )
            row = artifact["rows"][0]
            task_dir = Path(tmp) / "harness-error" / row["artifact_dir_relpath"]
            self.assertTrue((task_dir / "task_result.json").exists())
            self.assertTrue((task_dir / "harness_error.txt").exists())

        self.assertEqual(row["failure_category"], "harness_error")
        self.assertEqual(row["harness_error_type"], "RuntimeError")
        self.assertEqual(artifact["summary"]["failed"], 1)

    def test_budget_token_estimator_is_block_aware(self) -> None:
        from nanocode.agent.budget import (
            estimate_block_tokens,
            estimate_conversation_tokens,
            estimate_message_tokens,
        )
        from nanocode.agent.types import (
            ConversationHistory,
            ConversationMessage,
            TextBlock,
            ToolResultBlock,
            ToolUseBlock,
        )

        text = TextBlock("abcd " * 20)
        tool_use = ToolUseBlock(id="call_1", name="read_file", input={"file_path": "src/example.py"})
        short_result = ToolResultBlock(tool_use_id="call_1", tool_name="read_file", content="short")
        long_result = ToolResultBlock(tool_use_id="call_1", tool_name="read_file", content="x" * 1000)

        self.assertGreater(estimate_block_tokens(tool_use), estimate_block_tokens(TextBlock("read_file")))
        self.assertGreater(estimate_block_tokens(long_result), estimate_block_tokens(short_result))

        messages = [
            ConversationMessage(role="user", content=[text]),
            ConversationMessage(role="assistant", content=[tool_use]),
            ConversationMessage(role="tool_result", content=[long_result]),
        ]
        history = ConversationHistory(messages)

        self.assertEqual(
            estimate_conversation_tokens(history),
            sum(estimate_message_tokens(message) for message in messages),
        )

    def test_security_expectation_rejects_unrelated_error(self) -> None:
        runner = _load_runner()
        tasks = {task["id"]: task for task in runner.load_benchmark()["tasks"]}
        task = tasks["security_approval_denied_shell"]

        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.jsonl"
            trace_path.write_text(
                json.dumps({"event": "run_started", "user_request": task["prompt"]}) + "\n"
                + json.dumps(
                    {
                        "event": "tool_started",
                        "name": "write_file",
                        "payload": {"tool_call_id": "wrong", "input": {"file_path": "unrelated.txt"}},
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "event": "tool_executed",
                        "name": "write_file",
                        "payload": {"tool_call_id": "wrong", "is_error": True, "content": "Action denied"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            fields = runner.benchmark_contracts.evaluate_contracts(
                task=task,
                trace_path=trace_path,
                verifier_returncode=0,
                session_exists=False,
                resume_interrupted_marked=False,
                resume_orphan_repaired=False,
                resume_output="",
                nanocode_returncode=0,
            )

        self.assertEqual(fields["tool_error_codes"], ["action_denied"])
        self.assertEqual(fields["security_event_type"], "not_observed")
        self.assertFalse(fields["security_matched_tool_call"])
        self.assertFalse(fields["security_contract_met"])

    def test_security_expectation_matches_absolute_file_path(self) -> None:
        runner = _load_runner()
        tasks = {task["id"]: task for task in runner.load_benchmark()["tasks"]}
        task = tasks["security_patch_missing_new_text"]

        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.jsonl"
            trace_path.write_text(
                json.dumps({"event": "run_started", "user_request": task["prompt"]}) + "\n"
                + json.dumps(
                    {
                        "event": "tool_started",
                        "name": "edit_file",
                        "payload": {
                            "id": "call_missing_new_text",
                            "input": {"file_path": f"{tmp}/workspace/missing_new_text.txt", "old_string": "missing_new_text=todo"},
                        },
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "event": "tool_executed",
                        "name": "edit_file",
                        "payload": {
                            "id": "call_missing_new_text",
                            "is_error": True,
                            "content": "Error: missing required field: new_string",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            fields = runner.benchmark_contracts.evaluate_contracts(
                task=task,
                trace_path=trace_path,
                verifier_returncode=0,
                session_exists=False,
                resume_interrupted_marked=False,
                resume_orphan_repaired=False,
                resume_output="",
                nanocode_returncode=0,
            )

        self.assertEqual(fields["security_event_type"], "invalid_patch_blocked")
        self.assertEqual(fields["security_matched_error_code"], "patch_missing_new_text")
        self.assertTrue(fields["security_matched_tool_call"])
        self.assertTrue(fields["security_contract_met"])

    def test_security_expectation_prefers_metadata_error_code(self) -> None:
        runner = _load_runner()
        tasks = {task["id"]: task for task in runner.load_benchmark()["tasks"]}
        task = tasks["path_escape_denied_recovery"]

        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.jsonl"
            trace_path.write_text(
                json.dumps({"event": "run_started", "user_request": task["prompt"]}) + "\n"
                + json.dumps(
                    {
                        "event": "tool_started",
                        "name": "write_file",
                        "payload": {
                            "id": "call_escape",
                            "input": {"file_path": f"{tmp}/workspace/../outside_escape.txt"},
                        },
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "event": "tool_executed",
                        "name": "write_file",
                        "payload": {
                            "id": "call_escape",
                            "is_error": True,
                            "content": "Action denied: generic denial text",
                            "metadata": {"error_code": "outside_workspace"},
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            fields = runner.benchmark_contracts.evaluate_contracts(
                task=task,
                trace_path=trace_path,
                verifier_returncode=0,
                session_exists=False,
                resume_interrupted_marked=False,
                resume_orphan_repaired=False,
                resume_output="",
                nanocode_returncode=0,
            )

        self.assertEqual(fields["tool_error_codes"], ["outside_workspace"])
        self.assertEqual(fields["security_event_type"], "outside_workspace_blocked")
        self.assertEqual(fields["security_matched_error_code"], "outside_workspace")
        self.assertTrue(fields["security_contract_met"])

    def test_permissions_suite_matches_protected_path_error_code(self) -> None:
        runner = _load_runner()
        tasks = {task["id"]: task for task in runner.load_benchmark()["tasks"]}
        task = tasks["permission_yolo_protected_path_blocked"]

        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.jsonl"
            trace_path.write_text(
                json.dumps({"event": "run_started", "user_request": task["prompt"]}) + "\n"
                + json.dumps(
                    {
                        "event": "tool_started",
                        "name": "write_file",
                        "payload": {
                            "id": "call_env",
                            "input": {"file_path": ".env", "content": "SECRET=blocked"},
                        },
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "event": "tool_executed",
                        "name": "write_file",
                        "payload": {
                            "id": "call_env",
                            "is_error": True,
                            "content": "User denied this action.",
                            "metadata": {"error_code": "protected_path"},
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            fields = runner.benchmark_contracts.evaluate_contracts(
                task=task,
                trace_path=trace_path,
                verifier_returncode=0,
                session_exists=False,
                resume_interrupted_marked=False,
                resume_orphan_repaired=False,
                resume_output="",
                nanocode_returncode=0,
            )

        self.assertEqual(fields["security_event_type"], "protected_path")
        self.assertEqual(fields["security_matched_error_code"], "protected_path")
        self.assertTrue(fields["security_matched_tool_call"])
        self.assertTrue(fields["security_contract_met"])

    def test_security_error_classifier_handles_legacy_outside_workspace_text(self) -> None:
        artifacts = _load_module(ARTIFACTS_PATH, "local_fixture_artifacts_error_code")

        self.assertEqual(
            artifacts.classify_tool_error("Action denied: path outside workspace: /tmp/outside.txt"),
            "outside_workspace",
        )

    def test_tool_path_limit_contract_rejects_repeated_reads(self) -> None:
        runner = _load_runner()
        task = {
            "id": "repeat_guard",
            "tool_path_limits": [{"tool": "read_file", "path": "repeat.txt", "max_count": 2, "max_pre_edit_count": 1}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.jsonl"
            trace_path.write_text(
                json.dumps({"event": "run_started", "user_request": "repeat"}) + "\n"
                + json.dumps({"event": "tool_started", "name": "read_file", "payload": {"input": {"file_path": "repeat.txt"}}}) + "\n"
                + json.dumps({"event": "tool_started", "name": "read_file", "payload": {"input": {"file_path": "./repeat.txt"}}}) + "\n"
                + json.dumps({"event": "run_finished"}) + "\n",
                encoding="utf-8",
            )

            fields = runner.benchmark_contracts.evaluate_contracts(
                task=task,
                trace_path=trace_path,
                verifier_returncode=0,
                session_exists=False,
                resume_interrupted_marked=False,
                resume_orphan_repaired=False,
                resume_output="",
                nanocode_returncode=0,
            )

        self.assertFalse(fields["tool_path_limit_contract_met"])
        self.assertEqual(fields["tool_path_limit_violations"][0]["observed_count"], 2)
        self.assertEqual(fields["tool_path_limit_violations"][0]["pre_edit_count"], 2)
        self.assertEqual(fields["tool_path_limit_violations"][0]["violation"], "max_pre_edit_count")
        self.assertFalse(fields["specialty_contract_met"])
        self.assertEqual(fields["specialty_failure_category"], "tool_path_limit_contract_failed")

    def test_tool_path_limit_allows_post_edit_verification_read(self) -> None:
        runner = _load_runner()
        task = {
            "id": "repeat_guard",
            "tool_path_limits": [
                {"tool": "read_file", "path": "repeat.txt", "max_count": 2, "max_pre_edit_count": 1, "max_post_edit_count": 1}
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.jsonl"
            trace_path.write_text(
                json.dumps({"event": "run_started", "user_request": "repeat"}) + "\n"
                + json.dumps({"event": "tool_started", "name": "read_file", "payload": {"id": "read_1", "input": {"file_path": "repeat.txt"}}}) + "\n"
                + json.dumps({"event": "tool_executed", "name": "read_file", "payload": {"id": "read_1", "is_error": False}}) + "\n"
                + json.dumps({"event": "tool_started", "name": "edit_file", "payload": {"id": "edit_1", "input": {"file_path": "repeat.txt"}}}) + "\n"
                + json.dumps({"event": "tool_executed", "name": "edit_file", "payload": {"id": "edit_1", "is_error": False}}) + "\n"
                + json.dumps({"event": "tool_started", "name": "read_file", "payload": {"id": "read_2", "input": {"file_path": "./repeat.txt"}}}) + "\n"
                + json.dumps({"event": "tool_executed", "name": "read_file", "payload": {"id": "read_2", "is_error": False}}) + "\n"
                + json.dumps({"event": "run_finished"}) + "\n",
                encoding="utf-8",
            )

            fields = runner.benchmark_contracts.evaluate_contracts(
                task=task,
                trace_path=trace_path,
                verifier_returncode=0,
                session_exists=False,
                resume_interrupted_marked=False,
                resume_orphan_repaired=False,
                resume_output="",
                nanocode_returncode=0,
            )

        self.assertTrue(fields["tool_path_limit_contract_met"])
        self.assertEqual(fields["tool_path_limit_counts"][0]["observed_count"], 2)
        self.assertEqual(fields["tool_path_limit_counts"][0]["pre_edit_count"], 1)
        self.assertEqual(fields["tool_path_limit_counts"][0]["post_edit_count"], 1)
        self.assertTrue(fields["tool_path_limit_counts"][0]["mutation_observed"])

    def test_select_run_dir_prefers_matching_main_run(self) -> None:
        runner = _load_runner()
        prompt = "Update README.md."

        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / ".nanocode" / "runs"
            main_run = runs_root / "run_main"
            sub_run = runs_root / "run_sub"
            main_run.mkdir(parents=True)
            sub_run.mkdir(parents=True)
            (main_run / "report.json").write_text(
                json.dumps({"runtime": {"is_sub_agent": False}}),
                encoding="utf-8",
            )
            (main_run / "trace.jsonl").write_text(
                json.dumps({"event": "run_started", "user_request": prompt}) + "\n",
                encoding="utf-8",
            )
            (sub_run / "report.json").write_text(
                json.dumps({"runtime": {"is_sub_agent": True}}),
                encoding="utf-8",
            )
            (sub_run / "trace.jsonl").write_text(
                json.dumps({"event": "run_started", "user_request": prompt}) + "\n",
                encoding="utf-8",
            )

            artifacts = _load_module(ARTIFACTS_PATH, "local_fixture_artifacts")
            selected = artifacts.select_run_dir(Path(tmp), prompt)

        self.assertEqual(selected.name, "run_main")

    def test_path_matching_respects_path_boundaries(self) -> None:
        artifacts = _load_module(ARTIFACTS_PATH, "local_fixture_artifacts_path")

        self.assertTrue(artifacts.path_text_matches("/tmp/work/current_truth.txt", {"current_truth.txt"}))
        self.assertTrue(artifacts.path_text_matches("./nested/current_truth.txt", {"nested/current_truth.txt"}))
        self.assertFalse(artifacts.path_text_matches("not_current_truth.txt", {"current_truth.txt"}))
        self.assertFalse(artifacts.path_text_matches("readonly.txt.bak", {"readonly.txt"}))

    def test_run_task_keeps_full_report_as_per_task_artifact(self) -> None:
        runner = _load_runner()
        task = runner.load_benchmark()["tasks"][0]

        def fake_nanocode(task, workspace, *, timeout, model, stream, context_governance="full"):
            run_dir = workspace / ".nanocode" / "runs" / "run_1"
            run_dir.mkdir(parents=True)
            report = {
                "schema_version": 1,
                "run_id": "run_1",
                "task_id": "task_1",
                "status": "completed",
                "stop_reason": "stop",
                "tool_steps": 1,
                "attempts": 1,
                "runtime": {"is_sub_agent": False, "allowed_tools": task["allowed_tools"]},
                "usage": {"input_tokens": 7, "output_tokens": 3},
                "metrics": {"tool_name_counts": {"read_file": 1}},
            }
            (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
            (run_dir / "task_state.json").write_text(
                json.dumps({"user_request": task["prompt"]}),
                encoding="utf-8",
            )
            (run_dir / "trace.jsonl").write_text(
                json.dumps({"event": "run_started", "user_request": task["prompt"]}) + "\n"
                + json.dumps({"event": "tool_started", "name": "read_file"}) + "\n"
                + json.dumps({"event": "tool_executed", "name": "read_file", "payload": {"is_error": False}})
                + "\n"
                + json.dumps({"event": "run_finished"}) + "\n",
                encoding="utf-8",
            )
            Path(workspace / task["artifact_path"]).write_text("This fixture is a locked benchmark workspace.\n", encoding="utf-8")
            return runner.subprocess.CompletedProcess(["nanocode"], 0, "", "")

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runner, "_run_nanocode", side_effect=fake_nanocode),
            patch.object(runner, "_run_verifier", return_value=runner.subprocess.CompletedProcess(["verify"], 0, "", "")),
        ):
            row = runner.run_task(task, run_root=Path(tmp), timeout=5, model=None, stream=False)
            task_dir = Path(tmp) / row["artifact_dir_relpath"]

            self.assertNotIn("report", row)
            self.assertIn("report_summary", row)
            self.assertTrue((task_dir / "report.json").exists())
            self.assertTrue((task_dir / "trace.jsonl").exists())
            self.assertFalse((task_dir / "task_state.json").exists())

    def test_prepare_resume_scenario_writes_repairable_state(self) -> None:
        runner = _load_runner()
        tasks = {task["id"]: task for task in runner.load_benchmark()["tasks"]}

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "bench_repo_resume"
            workspace.mkdir()
            (workspace / "resume_marker.txt").write_text("resume_marker=todo\n", encoding="utf-8")
            runner._prepare_resume_scenario(tasks["resume_orphaned_tool_call"], workspace)

            home = runner._task_home(workspace)
            session_path = home / ".nanocode" / "sessions" / "bench_resume_orphaned_tool_call" / "session.jsonl"
            trace_path = workspace / ".nanocode" / "runs" / "run_seed_resume_orphaned_tool_call" / "trace.jsonl"

            self.assertTrue(session_path.exists())
            self.assertTrue(trace_path.exists())
            text = session_path.read_text(encoding="utf-8")
            self.assertIn('"version": 2', text)
            self.assertIn('"type": "tool_use"', text)
            self.assertIn("Update resume_marker.txt", trace_path.read_text(encoding="utf-8"))

            runner._prepare_resume_scenario(tasks["resume_checkpoint_goal"], workspace)
            checkpoint_path = home / ".nanocode" / "sessions" / "bench_resume_checkpoint_goal" / "session.jsonl"
            checkpoint_text = checkpoint_path.read_text(encoding="utf-8")
            self.assertIn("assistant_checkpoint", checkpoint_text)
            self.assertNotIn("tracked_files", checkpoint_text)

    def test_prepare_memory_and_security_fixtures(self) -> None:
        runner = _load_runner()
        tasks = {task["id"]: task for task in runner.load_benchmark()["tasks"]}

        with tempfile.TemporaryDirectory() as tmp:
            memory_workspace = Path(tmp) / "bench_repo_memory"
            memory_workspace.mkdir()
            runner._write_memory_fixture(tasks["memory_fact_lookup"], memory_workspace)
            memory_dir = runner._memory_dir_for_workspace(memory_workspace)
            self.assertTrue((memory_dir / "project.md").exists())
            self.assertIn("canary-blue", (memory_dir / "project.md").read_text(encoding="utf-8"))
            self.assertIn("project.md", (memory_dir / "MEMORY.md").read_text(encoding="utf-8"))

            security_workspace = Path(tmp) / "bench_repo_security"
            security_workspace.mkdir()
            runner._prepare_security_scenario(tasks["security_approval_denied_shell"], security_workspace)
            settings = json.loads((security_workspace / ".claude" / "settings.json").read_text(encoding="utf-8"))
            self.assertIn("run_shell(printf denied-shell)", settings["permissions"]["deny"])

    def test_run_task_records_resume_observations(self) -> None:
        runner = _load_runner()
        task = next(task for task in runner.load_benchmark()["tasks"] if task["id"] == "resume_checkpoint_goal")

        def fake_nanocode(task, workspace, *, timeout, model, stream, context_governance="full"):
            interrupted_trace = workspace / ".nanocode" / "runs" / task["resume_interrupted_run_id"] / "trace.jsonl"
            with interrupted_trace.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"event": "run_interrupted"}) + "\n")

            run_dir = workspace / ".nanocode" / "runs" / "run_resume"
            run_dir.mkdir(parents=True)
            report = {
                "schema_version": 1,
                "run_id": "run_resume",
                "task_id": "task_resume",
                "status": "completed",
                "stop_reason": "stop",
                "tool_steps": 1,
                "attempts": 1,
                "runtime": {"is_sub_agent": False, "allowed_tools": task["allowed_tools"]},
                "usage": {},
                "metrics": {"tool_name_counts": {"edit_file": 1}},
            }
            (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
            (run_dir / "trace.jsonl").write_text(
                json.dumps({"event": "run_started", "user_request": task["prompt"]}) + "\n"
                + json.dumps({"event": "tool_started", "name": "edit_file"}) + "\n"
                + json.dumps({"event": "tool_executed", "name": "edit_file", "payload": {"is_error": False}})
                + "\n"
                + json.dumps({"event": "run_finished"}) + "\n",
                encoding="utf-8",
            )
            (workspace / "checkpoint_goal.txt").write_text("checkpoint_goal=done\n", encoding="utf-8")
            return runner.subprocess.CompletedProcess(["nanocode"], 0, "Session restored (2 messages; marked 1 interrupted run(s)).", "")

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runner, "_run_nanocode", side_effect=fake_nanocode),
            patch.object(runner, "_run_verifier", return_value=runner.subprocess.CompletedProcess(["verify"], 0, "", "")),
        ):
            row = runner.run_task(task, run_root=Path(tmp), timeout=5, model=None, stream=False)

        self.assertTrue(row["passed"])
        self.assertEqual(row["recovery_case"], "checkpoint_resume_goal")
        self.assertEqual(row["recovery_case_category"], "checkpoint_resume")
        self.assertFalse(row["resume_is_orphan_case"])
        self.assertTrue(row["resume_is_checkpoint_case"])
        self.assertTrue(row["resume_interrupted_marked"])
        self.assertTrue(row["checkpoint_resume_restore_observed"])
        self.assertEqual(row["resume_observed_status"], "session_restored")
        self.assertNotIn("workspace_drift_expected", row)
        self.assertNotIn("schema_mismatch_expected", row)

    def test_run_task_records_security_and_memory_observations(self) -> None:
        runner = _load_runner()
        security_task = next(task for task in runner.load_benchmark()["tasks"] if task["id"] == "security_approval_denied_shell")
        memory_task = next(task for task in runner.load_benchmark()["tasks"] if task["id"] == "memory_fact_lookup")

        def fake_security_nanocode(task, workspace, *, timeout, model, stream, context_governance="full"):
            run_dir = workspace / ".nanocode" / "runs" / "run_security"
            run_dir.mkdir(parents=True)
            (run_dir / "report.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": "run_security",
                        "task_id": "task_security",
                        "status": "completed",
                        "stop_reason": "stop",
                        "tool_steps": 2,
                        "attempts": 1,
                        "runtime": {"is_sub_agent": False, "allowed_tools": task["allowed_tools"]},
                        "usage": {},
                        "metrics": {"tool_name_counts": {"run_shell": 1, "edit_file": 1}},
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "trace.jsonl").write_text(
                json.dumps({"event": "run_started", "user_request": task["prompt"]}) + "\n"
                + json.dumps(
                    {
                        "event": "tool_started",
                        "name": "run_shell",
                        "payload": {"input": {"command": "printf denied-shell"}},
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "event": "tool_executed",
                        "name": "run_shell",
                        "is_error": True,
                        "payload": {"is_error": True, "content": "Action denied: matched benchmark deny rule"},
                    }
                )
                + "\n"
                + json.dumps({"event": "tool_started", "name": "edit_file"}) + "\n"
                + json.dumps({"event": "tool_executed", "name": "edit_file", "payload": {"is_error": False}})
                + "\n"
                + json.dumps({"event": "run_finished"}) + "\n",
                encoding="utf-8",
            )
            (workspace / "safe.txt").write_text("safe=shell-denied\n", encoding="utf-8")
            return runner.subprocess.CompletedProcess(["nanocode"], 0, "", "")

        def fake_memory_nanocode(task, workspace, *, timeout, model, stream, context_governance="full"):
            run_dir = workspace / ".nanocode" / "runs" / "run_memory"
            run_dir.mkdir(parents=True)
            (run_dir / "report.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": "run_memory",
                        "task_id": "task_memory",
                        "status": "completed",
                        "stop_reason": "stop",
                        "tool_steps": 1,
                        "attempts": 1,
                        "runtime": {"is_sub_agent": False, "allowed_tools": task["allowed_tools"]},
                        "usage": {},
                        "metrics": {"tool_name_counts": {"edit_file": 1}},
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "trace.jsonl").write_text(
                json.dumps({"event": "run_started", "user_request": task["prompt"]}) + "\n"
                + json.dumps({"event": "tool_started", "name": "edit_file", "payload": {"input": {"file_path": "memory_answer.txt"}}})
                + "\n"
                + json.dumps({"event": "tool_executed", "name": "edit_file", "payload": {"is_error": False}})
                + "\n"
                + json.dumps({"event": "run_finished"}) + "\n",
                encoding="utf-8",
            )
            (workspace / "memory_answer.txt").write_text("memory_answer=canary-blue\n", encoding="utf-8")
            return runner.subprocess.CompletedProcess(["nanocode"], 0, "", "")

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runner, "_run_nanocode", side_effect=fake_security_nanocode),
            patch.object(runner, "_run_verifier", return_value=runner.subprocess.CompletedProcess(["verify"], 0, "", "")),
        ):
            security_row = runner.run_task(security_task, run_root=Path(tmp), timeout=5, model=None, stream=False)

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runner, "_run_nanocode", side_effect=fake_memory_nanocode),
            patch.object(runner, "_run_verifier", return_value=runner.subprocess.CompletedProcess(["verify"], 0, "", "")),
        ):
            memory_row = runner.run_task(memory_task, run_root=Path(tmp), timeout=5, model=None, stream=False)

        self.assertEqual(security_row["security_event_type"], "action_denied")
        self.assertEqual(security_row["tool_error_codes"], ["action_denied"])
        self.assertTrue(security_row["security_matched_tool_call"])
        self.assertEqual(security_row["security_expected_tool"], "run_shell")
        self.assertNotIn("security_secret_leaked", security_row)
        self.assertTrue(memory_row["memory_task"])
        self.assertEqual(memory_row["memory_case"], "fact_lookup")
        self.assertEqual(memory_row["memory_source_read_count"], 0)
        self.assertEqual(memory_row["memory_fallback_source_path"], "memory_source.txt")
        self.assertEqual(memory_row["memory_fallback_read_count"], 0)
        self.assertFalse(memory_row["memory_fallback_read"])
        self.assertTrue(memory_row["memory_fact_hit"])
        self.assertNotIn("memory_hit", memory_row)

    def test_run_task_fails_when_specialty_contract_is_not_met(self) -> None:
        runner = _load_runner()
        tasks = {task["id"]: task for task in runner.load_benchmark()["tasks"]}

        def fake_security_without_event(task, workspace, *, timeout, model, stream, context_governance="full"):
            run_dir = workspace / ".nanocode" / "runs" / "run_security"
            run_dir.mkdir(parents=True)
            (run_dir / "report.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": "run_security",
                        "task_id": "task_security",
                        "status": "completed",
                        "stop_reason": "stop",
                        "tool_steps": 1,
                        "attempts": 1,
                        "runtime": {"is_sub_agent": False, "allowed_tools": task["allowed_tools"]},
                        "usage": {},
                        "metrics": {"tool_name_counts": {"edit_file": 1}},
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "trace.jsonl").write_text(
                json.dumps({"event": "run_started", "user_request": task["prompt"]}) + "\n"
                + json.dumps({"event": "tool_started", "name": "edit_file"}) + "\n"
                + json.dumps({"event": "tool_executed", "name": "edit_file", "payload": {"is_error": False}})
                + "\n"
                + json.dumps({"event": "run_finished"}) + "\n",
                encoding="utf-8",
            )
            (workspace / "safe.txt").write_text("safe=shell-denied\n", encoding="utf-8")
            return runner.subprocess.CompletedProcess(["nanocode"], 0, "", "")

        def fake_memory_with_fallback(task, workspace, *, timeout, model, stream, context_governance="full"):
            run_dir = workspace / ".nanocode" / "runs" / "run_memory"
            run_dir.mkdir(parents=True)
            (run_dir / "report.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": "run_memory",
                        "task_id": "task_memory",
                        "status": "completed",
                        "stop_reason": "stop",
                        "tool_steps": 2,
                        "attempts": 1,
                        "runtime": {"is_sub_agent": False, "allowed_tools": task["allowed_tools"]},
                        "usage": {},
                        "metrics": {"tool_name_counts": {"read_file": 1, "edit_file": 1}},
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "trace.jsonl").write_text(
                json.dumps({"event": "run_started", "user_request": task["prompt"]}) + "\n"
                + json.dumps({"event": "tool_started", "name": "read_file", "payload": {"input": {"file_path": "memory_source.txt"}}})
                + "\n"
                + json.dumps({"event": "tool_executed", "name": "read_file", "payload": {"is_error": False}})
                + "\n"
                + json.dumps({"event": "tool_started", "name": "edit_file", "payload": {"input": {"file_path": "memory_answer.txt"}}})
                + "\n"
                + json.dumps({"event": "tool_executed", "name": "edit_file", "payload": {"is_error": False}})
                + "\n"
                + json.dumps({"event": "run_finished"}) + "\n",
                encoding="utf-8",
            )
            (workspace / "memory_answer.txt").write_text("memory_answer=canary-blue\n", encoding="utf-8")
            return runner.subprocess.CompletedProcess(["nanocode"], 0, "", "")

        def fake_memory_conflict_without_current_truth_read(task, workspace, *, timeout, model, stream, context_governance="full"):
            run_dir = workspace / ".nanocode" / "runs" / "run_memory_conflict"
            run_dir.mkdir(parents=True)
            (run_dir / "report.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": "run_memory_conflict",
                        "task_id": "task_memory_conflict",
                        "status": "completed",
                        "stop_reason": "stop",
                        "tool_steps": 1,
                        "attempts": 1,
                        "runtime": {"is_sub_agent": False, "allowed_tools": task["allowed_tools"]},
                        "usage": {},
                        "metrics": {"tool_name_counts": {"edit_file": 1}},
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "trace.jsonl").write_text(
                json.dumps({"event": "run_started", "user_request": task["prompt"]}) + "\n"
                + json.dumps({"event": "tool_started", "name": "edit_file", "payload": {"input": {"file_path": "memory_guard.txt"}}})
                + "\n"
                + json.dumps({"event": "tool_executed", "name": "edit_file", "payload": {"is_error": False}})
                + "\n"
                + json.dumps({"event": "run_finished"}) + "\n",
                encoding="utf-8",
            )
            (workspace / "memory_guard.txt").write_text("memory_guard=use-current-file\n", encoding="utf-8")
            return runner.subprocess.CompletedProcess(["nanocode"], 0, "", "")

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runner, "_run_nanocode", side_effect=fake_security_without_event),
            patch.object(runner, "_run_verifier", return_value=runner.subprocess.CompletedProcess(["verify"], 0, "", "")),
        ):
            row = runner.run_task(tasks["security_approval_denied_shell"], run_root=Path(tmp), timeout=5, model=None, stream=False)

        self.assertFalse(row["passed"])
        self.assertFalse(row["security_contract_met"])
        self.assertEqual(row["failure_category"], "security_contract_failed")

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runner, "_run_nanocode", side_effect=fake_memory_with_fallback),
            patch.object(runner, "_run_verifier", return_value=runner.subprocess.CompletedProcess(["verify"], 0, "", "")),
        ):
            row = runner.run_task(tasks["memory_fact_lookup"], run_root=Path(tmp), timeout=5, model=None, stream=False)

        self.assertFalse(row["passed"])
        self.assertFalse(row["memory_contract_met"])
        self.assertEqual(row["memory_fallback_read_count"], 1)
        self.assertEqual(row["failure_category"], "memory_contract_failed")

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runner, "_run_nanocode", side_effect=fake_memory_conflict_without_current_truth_read),
            patch.object(runner, "_run_verifier", return_value=runner.subprocess.CompletedProcess(["verify"], 0, "", "")),
        ):
            row = runner.run_task(tasks["memory_irrelevant_guard"], run_root=Path(tmp), timeout=5, model=None, stream=False)

        self.assertFalse(row["passed"])
        self.assertFalse(row["memory_current_truth_read"])
        self.assertFalse(row["memory_conflict_guard_passed"])
        self.assertEqual(row["failure_category"], "memory_contract_failed")

    def test_context_contract_requires_declared_snip_event(self) -> None:
        runner = _load_runner()
        task = next(task for task in runner.load_benchmark()["tasks"] if task["id"] == "context_tool_history_snip_realistic")

        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.jsonl"
            trace_path.write_text(
                json.dumps({"event": "run_started", "user_request": task["prompt"]}) + "\n"
                + json.dumps({"event": "conversation_committed", "reason": "assistant_final"}) + "\n"
                + json.dumps({"event": "run_finished"}) + "\n",
                encoding="utf-8",
            )
            fields = runner.benchmark_contracts.evaluate_contracts(
                task=task,
                trace_path=trace_path,
                verifier_returncode=0,
                session_exists=False,
                resume_interrupted_marked=False,
                resume_orphan_repaired=False,
                resume_output="",
                nanocode_returncode=0,
            )

            trace_path.write_text(
                json.dumps({"event": "run_started", "user_request": task["prompt"]}) + "\n"
                + json.dumps({"event": "conversation_committed", "reason": "tool_history_snip"}) + "\n"
                + json.dumps({"event": "run_finished"}) + "\n",
                encoding="utf-8",
            )
            repaired = runner.benchmark_contracts.evaluate_contracts(
                task=task,
                trace_path=trace_path,
                verifier_returncode=0,
                session_exists=False,
                resume_interrupted_marked=False,
                resume_orphan_repaired=False,
                resume_output="",
                nanocode_returncode=0,
            )

        self.assertTrue(fields["context_contract_expected"])
        self.assertTrue(fields["context_expected_tool_history_snip"])
        self.assertFalse(fields["tool_history_snip_observed"])
        self.assertFalse(fields["context_contract_met"])
        self.assertTrue(repaired["tool_history_snip_observed"])
        self.assertTrue(repaired["context_contract_met"])

    def test_run_task_fails_without_parseable_trace(self) -> None:
        runner = _load_runner()
        task = runner.load_benchmark()["tasks"][0]

        def fake_without_trace(task, workspace, *, timeout, model, stream, context_governance="full"):
            run_dir = workspace / ".nanocode" / "runs" / "run_missing_trace"
            run_dir.mkdir(parents=True)
            (run_dir / "report.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": "run_missing_trace",
                        "task_id": "task_missing_trace",
                        "status": "completed",
                        "stop_reason": "stop",
                        "tool_steps": 1,
                        "attempts": 1,
                        "runtime": {"is_sub_agent": False, "allowed_tools": task["allowed_tools"]},
                        "usage": {},
                        "metrics": {"tool_name_counts": {"edit_file": 1}},
                    }
                ),
                encoding="utf-8",
            )
            Path(workspace / task["artifact_path"]).write_text("This fixture is a locked benchmark workspace.\n", encoding="utf-8")
            return runner.subprocess.CompletedProcess(["nanocode"], 0, "", "")

        def fake_invalid_trace(task, workspace, *, timeout, model, stream, context_governance="full"):
            run_dir = workspace / ".nanocode" / "runs" / "run_invalid_trace"
            run_dir.mkdir(parents=True)
            (run_dir / "report.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": "run_invalid_trace",
                        "task_id": "task_invalid_trace",
                        "status": "completed",
                        "stop_reason": "stop",
                        "tool_steps": 1,
                        "attempts": 1,
                        "runtime": {"is_sub_agent": False, "allowed_tools": task["allowed_tools"]},
                        "usage": {},
                        "metrics": {"tool_name_counts": {"edit_file": 1}},
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "trace.jsonl").write_text("{not json}\n", encoding="utf-8")
            Path(workspace / task["artifact_path"]).write_text("This fixture is a locked benchmark workspace.\n", encoding="utf-8")
            return runner.subprocess.CompletedProcess(["nanocode"], 0, "", "")

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runner, "_run_nanocode", side_effect=fake_without_trace),
            patch.object(runner, "_run_verifier", return_value=runner.subprocess.CompletedProcess(["verify"], 0, "", "")),
        ):
            row = runner.run_task(task, run_root=Path(tmp), timeout=5, model=None, stream=False)

        self.assertFalse(row["passed"])
        self.assertFalse(row["trace_contract_met"])
        self.assertEqual(row["failure_category"], "missing_trace")

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runner, "_run_nanocode", side_effect=fake_invalid_trace),
            patch.object(runner, "_run_verifier", return_value=runner.subprocess.CompletedProcess(["verify"], 0, "", "")),
        ):
            row = runner.run_task(task, run_root=Path(tmp), timeout=5, model=None, stream=False)

        self.assertFalse(row["passed"])
        self.assertTrue(row["trace_exists"])
        self.assertFalse(row["trace_parse_valid"])
        self.assertEqual(row["failure_category"], "invalid_trace")

    def test_run_task_records_invalid_report_without_crashing(self) -> None:
        runner = _load_runner()
        task = runner.load_benchmark()["tasks"][0]

        def fake_invalid_report(task, workspace, *, timeout, model, stream, context_governance="full"):
            run_dir = workspace / ".nanocode" / "runs" / "run_invalid_report"
            run_dir.mkdir(parents=True)
            (run_dir / "report.json").write_text("{not json}\n", encoding="utf-8")
            (run_dir / "trace.jsonl").write_text(
                json.dumps({"event": "run_started", "user_request": task["prompt"]}) + "\n"
                + json.dumps({"event": "run_finished"}) + "\n",
                encoding="utf-8",
            )
            Path(workspace / task["artifact_path"]).write_text("This fixture is a locked benchmark workspace.\n", encoding="utf-8")
            return runner.subprocess.CompletedProcess(["nanocode"], 0, "", "")

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runner, "_run_nanocode", side_effect=fake_invalid_report),
            patch.object(runner, "_run_verifier", return_value=runner.subprocess.CompletedProcess(["verify"], 0, "", "")),
        ):
            row = runner.run_task(task, run_root=Path(tmp), timeout=5, model=None, stream=False)
            self.assertTrue((Path(tmp) / row["artifact_dir_relpath"] / "report.json").exists())

        self.assertFalse(row["passed"])
        self.assertTrue(row["report_exists"])
        self.assertFalse(row["report_parse_valid"])
        self.assertEqual(row["failure_category"], "invalid_report")

    def test_run_task_fails_when_trace_contract_is_incomplete(self) -> None:
        runner = _load_runner()
        task = runner.load_benchmark()["tasks"][0]

        def fake_incomplete_trace(task, workspace, *, timeout, model, stream, context_governance="full"):
            run_dir = workspace / ".nanocode" / "runs" / "run_incomplete_trace"
            run_dir.mkdir(parents=True)
            (run_dir / "report.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": "run_incomplete_trace",
                        "task_id": "task_incomplete_trace",
                        "status": "completed",
                        "stop_reason": "stop",
                        "tool_steps": 1,
                        "attempts": 1,
                        "runtime": {"is_sub_agent": False, "allowed_tools": task["allowed_tools"]},
                        "usage": {},
                        "metrics": {"tool_name_counts": {"edit_file": 1}},
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "trace.jsonl").write_text(
                json.dumps({"event": "run_started", "user_request": task["prompt"]}) + "\n"
                + json.dumps({"event": "tool_started", "name": "edit_file"}) + "\n"
                + json.dumps({"event": "tool_executed", "name": "edit_file", "payload": {"is_error": False}})
                + "\n",
                encoding="utf-8",
            )
            Path(workspace / task["artifact_path"]).write_text("This fixture is a locked benchmark workspace.\n", encoding="utf-8")
            return runner.subprocess.CompletedProcess(["nanocode"], 0, "", "")

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runner, "_run_nanocode", side_effect=fake_incomplete_trace),
            patch.object(runner, "_run_verifier", return_value=runner.subprocess.CompletedProcess(["verify"], 0, "", "")),
        ):
            row = runner.run_task(task, run_root=Path(tmp), timeout=5, model=None, stream=False)

        self.assertFalse(row["passed"])
        self.assertFalse(row["trace_contract_met"])
        self.assertIn("missing_run_finished", row["trace_contract_errors"])
        self.assertEqual(row["failure_category"], "trace_contract_failed")

    def test_trace_contract_rejects_report_trace_tool_count_mismatch(self) -> None:
        runner = _load_runner()

        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.jsonl"
            trace_path.write_text(
                json.dumps({"event": "run_started", "user_request": "count mismatch"}) + "\n"
                + json.dumps({"event": "tool_started", "name": "read_file"}) + "\n"
                + json.dumps({"event": "tool_executed", "name": "read_file", "payload": {"is_error": False}})
                + "\n"
                + json.dumps({"event": "tool_started", "name": "edit_file"}) + "\n"
                + json.dumps({"event": "tool_executed", "name": "edit_file", "payload": {"is_error": False}})
                + "\n"
                + json.dumps({"event": "run_finished"}) + "\n",
                encoding="utf-8",
            )
            report = {
                "tool_steps": 2,
                "metrics": {"tool_name_counts": {"read_file": 2}},
            }

            met, errors = runner._trace_contract(
                trace_path=trace_path,
                trace_exists=True,
                trace_parse_valid=True,
                report=report,
                report_parse_valid=True,
                allowed_tools=["read_file", "edit_file"],
                nanocode_returncode=0,
            )

        self.assertFalse(met)
        self.assertTrue(any(error.startswith("tool_name_counts_mismatch:") for error in errors))

    def test_run_task_passes_selected_run_to_verifier(self) -> None:
        runner = _load_runner()
        task = next(task for task in runner.load_benchmark()["tasks"] if task["id"] == "run_artifacts_present")

        def fake_nanocode(task, workspace, *, timeout, model, stream, context_governance="full"):
            runs_root = workspace / ".nanocode" / "runs"
            main_run = runs_root / "run_main"
            sub_run = runs_root / "run_sub"
            main_run.mkdir(parents=True)
            sub_run.mkdir(parents=True)
            report = {
                "schema_version": 1,
                "run_id": "run_main",
                "task_id": "task_main",
                "status": "completed",
                "stop_reason": "stop",
                "tool_steps": 1,
                "attempts": 1,
                "runtime": {"is_sub_agent": False, "allowed_tools": task["allowed_tools"]},
                "usage": {},
                "metrics": {"tool_name_counts": {"edit_file": 1}},
            }
            (main_run / "report.json").write_text(json.dumps(report), encoding="utf-8")
            (main_run / "trace.jsonl").write_text(
                json.dumps({"event": "run_started", "user_request": task["prompt"]}) + "\n"
                + json.dumps({"event": "tool_started", "name": "edit_file"}) + "\n"
                + json.dumps({"event": "tool_executed", "name": "edit_file", "payload": {"is_error": False}})
                + "\n"
                + json.dumps({"event": "run_finished"}) + "\n",
                encoding="utf-8",
            )
            (sub_run / "report.json").write_text(json.dumps({"runtime": {"is_sub_agent": True}}), encoding="utf-8")
            (sub_run / "trace.jsonl").write_text(json.dumps({"event": "run_started", "user_request": "nested"}) + "\n", encoding="utf-8")
            (workspace / "marker.txt").write_text("artifact_marker=done\n", encoding="utf-8")
            return runner.subprocess.CompletedProcess(["nanocode"], 0, "", "")

        def verifier(task, workspace, timeout, *, stream=False, env_extra=None):
            assert env_extra is not None
            assert Path(env_extra["NANOCODE_BENCH_RUN_DIR"]).name == "run_main"
            return runner.subprocess.CompletedProcess(["verify"], 0, "", "")

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runner, "_run_nanocode", side_effect=fake_nanocode),
            patch.object(runner, "_run_verifier", side_effect=verifier),
        ):
            row = runner.run_task(task, run_root=Path(tmp), timeout=5, model=None, stream=False)

        self.assertTrue(row["passed"])

    def test_run_task_does_not_fail_for_blocked_disallowed_tool_request(self) -> None:
        runner = _load_runner()
        task = dict(runner.load_benchmark()["tasks"][0])
        task["allowed_tools"] = ["edit_file"]

        def fake_nanocode(task, workspace, *, timeout, model, stream, context_governance="full"):
            run_dir = workspace / ".nanocode" / "runs" / "run_blocked"
            run_dir.mkdir(parents=True)
            report = {
                "schema_version": 1,
                "run_id": "run_blocked",
                "task_id": "task_blocked",
                "status": "completed",
                "stop_reason": "stop",
                "tool_steps": 2,
                "attempts": 2,
                "runtime": {"is_sub_agent": False, "allowed_tools": task["allowed_tools"]},
                "usage": {},
                "metrics": {"tool_name_counts": {"list_files": 1, "edit_file": 1}},
            }
            (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
            (run_dir / "trace.jsonl").write_text(
                json.dumps({"event": "run_started", "user_request": task["prompt"]}) + "\n"
                + json.dumps({"event": "tool_started", "name": "list_files"}) + "\n"
                + json.dumps(
                    {
                        "event": "tool_executed",
                        "name": "list_files",
                        "is_error": True,
                        "payload": {"is_error": True, "content": "Action denied: Tool is not allowed in this run"},
                    }
                )
                + "\n"
                + json.dumps({"event": "tool_started", "name": "edit_file"}) + "\n"
                + json.dumps(
                    {
                        "event": "tool_executed",
                        "name": "edit_file",
                        "is_error": False,
                        "payload": {"is_error": False, "content": "ok"},
                    }
                )
                + "\n"
                + json.dumps({"event": "run_finished"}) + "\n",
                encoding="utf-8",
            )
            Path(workspace / task["artifact_path"]).write_text("This fixture is a locked benchmark workspace.\n", encoding="utf-8")
            return runner.subprocess.CompletedProcess(["nanocode"], 0, "", "")

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runner, "_run_nanocode", side_effect=fake_nanocode),
            patch.object(runner, "_run_verifier", return_value=runner.subprocess.CompletedProcess(["verify"], 0, "", "")),
        ):
            row = runner.run_task(task, run_root=Path(tmp), timeout=5, model=None, stream=False)

        self.assertTrue(row["passed"])
        self.assertFalse(row["allowed_tools_respected"])
        self.assertTrue(row["allowed_tools_enforced"])
        self.assertEqual(row["disallowed_tool_requests"], ["list_files"])
        self.assertEqual(row["disallowed_tool_executions"], [])

    def test_scorecards_aggregate_task_artifacts(self) -> None:
        metrics = _load_module(METRICS_PATH, "local_fixture_metrics")

        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp)
            task_dir = run_root / "tasks" / "large_file"
            task_dir.mkdir(parents=True)
            (task_dir / "trace.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"event": "run_started"}),
                        json.dumps(
                            {
                                "event": "tool_executed",
                                "payload": {"metadata": {"persisted": True}},
                            }
                        ),
                        json.dumps({"event": "conversation_committed", "reason": "tool_history_snip"}),
                        json.dumps({"event": "conversation_committed", "reason": "context_compact"}),
                        json.dumps({"event": "run_finished"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (task_dir / "patch.diff").write_text("diff", encoding="utf-8")
            (task_dir / "report.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": "run_1",
                        "task_id": "task_1",
                        "status": "completed",
                        "stop_reason": "stop",
                        "tool_steps": 1,
                        "attempts": 2,
                        "runtime": {},
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 20,
                            "estimated_cost_usd": 0.01,
                        },
                        "metrics": {
                            "tool_name_counts": {"read_file": 1},
                            "tool_error_count": 0,
                            "runtime_error_count": 0,
                            "approval_request_count": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )

            rows = [
                {
                    "id": "large_file",
                    "category": "tool-boundary",
                    "tags": ["large-file"],
                    "artifact_dir_relpath": "tasks/large_file",
                    "passed": True,
                    "within_budget": True,
                    "verifier_passed": True,
                    "allowed_tools": ["read_file"],
                    "allowed_tools_respected": True,
                    "tool_steps": 1,
                    "attempts": 2,
                    "non_failure_stop_reason": True,
                    "report_exists": True,
                    "trace_exists": True,
                }
            ]

            scorecards = metrics.build_scorecards(rows, run_root)

        self.assertEqual(scorecards["harness_regression"]["pass_rate"], 1.0)
        self.assertEqual(scorecards["tool_control"]["tool_name_counts"], {"read_file": 1})
        self.assertEqual(scorecards["tool_control"]["allowed_tools_enforced_rate"], 1.0)
        self.assertEqual(scorecards["context_governance"]["large_result_persist_count"], 1)
        self.assertEqual(scorecards["context_governance"]["large_result_persist_coverage"], "covered")
        self.assertEqual(scorecards["context_governance"]["tool_history_snip_count"], 1)
        self.assertEqual(scorecards["context_governance"]["tool_history_snip_task_count"], 1)
        self.assertEqual(scorecards["context_governance"]["tool_history_snip_coverage"], "covered")
        self.assertEqual(scorecards["context_governance"]["context_compact_count"], 1)
        self.assertEqual(scorecards["context_governance"]["context_compact_task_count"], 1)
        self.assertEqual(scorecards["context_governance"]["context_compact_coverage"], "covered")
        self.assertEqual(scorecards["context_governance"]["tool_history_snip_expected_task_count"], 0)
        self.assertEqual(scorecards["context_governance"]["current_request_preserved_rate"], 1.0)
        self.assertEqual(scorecards["run_audit"]["artifact_complete_rate"], 1.0)
        self.assertEqual(scorecards["run_audit"]["run_state_available_rate"], 1.0)
        self.assertEqual(scorecards["usage"]["total_tokens"], 120)
        self.assertNotIn("recovery_ablation", scorecards)

    def test_security_memory_and_resume_scorecards(self) -> None:
        metrics = _load_module(METRICS_PATH, "local_fixture_metrics")
        rows = [
            {
                "id": "security",
                "tags": ["security"],
                "security_case": "approval_denied_shell",
                "security_event_type": "action_denied",
                "tool_error_codes": ["action_denied"],
                "passed": True,
            },
            {
                "id": "security_unobserved",
                "tags": ["security"],
                "security_case": "patch_nonunique",
                "security_event_type": "not_observed",
                "tool_error_codes": [],
                "passed": False,
            },
            {
                "id": "memory",
                "tags": ["memory", "memory-fact_lookup"],
                "memory_task": True,
                "memory_case": "fact_lookup",
                "memory_fact_hit": True,
                "memory_fallback_source_path": "memory_source.txt",
                "memory_fallback_read_count": 0,
                "memory_fallback_read": False,
                "passed": True,
            },
            {
                "id": "memory_read",
                "tags": ["memory", "memory-edit_dependency"],
                "memory_task": True,
                "memory_case": "edit_dependency",
                "memory_edit_dependency_success": False,
                "memory_fallback_source_path": "memory_source.txt",
                "memory_fallback_read_count": 2,
                "memory_fallback_read": True,
                "passed": True,
            },
            {
                "id": "memory_conflict",
                "tags": ["memory", "memory-irrelevant"],
                "memory_task": True,
                "memory_case": "conflict_guard",
                "memory_conflict_guard_passed": True,
                "passed": True,
            },
            {
                "id": "resume_orphan",
                "tags": ["resume"],
                "scenario": "resume",
                "resume_is_orphan_case": True,
                "resume_interrupted_marked": True,
                "resume_orphan_repaired": True,
                "passed": True,
            },
            {
                "id": "resume_checkpoint",
                "tags": ["resume"],
                "scenario": "resume",
                "resume_is_checkpoint_case": True,
                "resume_interrupted_marked": True,
                "checkpoint_resume_restore_observed": True,
                "resume_contract_met": True,
                "passed": True,
            },
            {
                "id": "recovery_primary",
                "category": "recovery",
                "tags": ["recovery"],
                "within_budget": True,
                "passed": True,
                "report_summary": {"metrics": {"tool_error_count": 0}},
            },
            {
                "id": "trace_error_recovery",
                "category": "run-artifacts",
                "tags": ["artifact-contract", "recovery"],
                "within_budget": True,
                "passed": True,
                "report_summary": {"metrics": {"tool_error_count": 1}},
            },
        ]

        scorecards = metrics.build_scorecards(rows, Path("/tmp/does-not-matter"))

        self.assertEqual(scorecards["security"]["scenario_count"], 2)
        self.assertEqual(scorecards["security"]["security_event_counts"]["action_denied"], 1)
        self.assertEqual(scorecards["security"]["tool_error_code_counts"]["action_denied"], 1)
        self.assertNotIn("secret_leak_rate", scorecards["security"])
        self.assertNotIn("memory_hit_rate", scorecards["memory"])
        self.assertEqual(scorecards["memory"]["memory_fact_hit_rate"], 1.0)
        self.assertEqual(scorecards["memory"]["memory_edit_dependency_success_rate"], 0.0)
        self.assertEqual(scorecards["memory"]["memory_conflict_guard_rate"], 1.0)
        self.assertEqual(scorecards["memory"]["memory_fallback_read_rate"], 0.5)
        self.assertEqual(scorecards["memory"]["memory_fallback_read_count"], 2)
        self.assertNotIn("repeated_reads", scorecards["memory"])
        self.assertEqual(scorecards["resume"]["resume_success_rate"], 1.0)
        self.assertEqual(scorecards["resume"]["interrupted_run_marked_rate"], 1.0)
        self.assertEqual(scorecards["resume"]["checkpoint_resume_success_rate"], 1.0)
        self.assertEqual(scorecards["resume"]["orphaned_tool_call_case_count"], 1)
        self.assertEqual(scorecards["resume"]["orphaned_tool_call_repaired_rate"], 1.0)
        self.assertEqual(scorecards["recovery"]["recovery_primary_task_count"], 1)
        self.assertEqual(scorecards["recovery"]["recovery_capability_task_count"], 2)
        self.assertEqual(scorecards["recovery"]["recovery_after_tool_error_pass_rate"], 1.0)

        strict_resume = metrics.build_scorecards(
            [
                {
                    "id": "checkpoint_observed_but_failed",
                    "tags": ["resume"],
                    "scenario": "resume",
                    "resume_is_checkpoint_case": True,
                    "resume_contract_met": True,
                    "checkpoint_resume_restore_observed": True,
                    "passed": False,
                }
            ],
            Path("/tmp/does-not-matter"),
        )
        self.assertEqual(strict_resume["resume"]["checkpoint_resume_observed_rate"], 1.0)
        self.assertEqual(strict_resume["resume"]["checkpoint_resume_success_rate"], 0.0)

    def test_report_mentions_not_measured_boundaries_and_na_rates(self) -> None:
        report = _load_module(REPORT_PATH, "local_fixture_report")
        artifact = {
            "summary": {"selected_tasks": 1, "executed_tasks": 0},
            "scorecards": {
                "harness_regression": {
                    "task_count": 1,
                    "pass_count": 1,
                    "verifier_pass_count": 1,
                    "pass_rate": 1.0,
                    "verifier_pass_rate": 1.0,
                },
                "tool_control": {"allowed_tools_respected_rate": 1.0},
                "security": {"task_count": 0},
                "memory": {"memory_task_count": 0},
                "resume": {"resume_scenario_count": 0},
                "run_audit": {"artifact_complete_rate": 1.0},
            },
        }

        text = report.render_core_report(artifact)

        self.assertIn("Not Measured", text)
        self.assertIn("No context ablation experiment", text)
        self.assertIn("Durable memory promotion/rejection is not measured", text)
        self.assertIn("Workspace drift, tracked-file freshness", text)
        self.assertIn("allowed_tools_request_respected_rate: N/A (0/0)", text)
        self.assertIn("allowed_tools_enforced_rate: N/A (0/0)", text)
        self.assertIn("recovery_capability_pass_rate", text)
        self.assertIn("memory_fact_hit_rate", text)
        self.assertIn("checkpoint_resume_success_rate", text)
        self.assertIn("orphaned_tool_call_repaired_rate: N/A (0/0)", text)
        self.assertNotIn("Recovery Ablation", text)
        self.assertNotIn("secret_leak_rate", text)

    def test_context_ablation_outputs_pico_metrics_and_preserves_current_request(self) -> None:
        ablation = _load_ablation()

        with tempfile.TemporaryDirectory() as tmp:
            result = ablation.run_context_ablation(run_root=Path(tmp))

        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["config_count"], 40)
        self.assertEqual(result["primary_profile"], "context_management_four_level")
        self.assertEqual(result["context_management_four_level"]["config_count"], 40)
        self.assertEqual(
            result["scenario_counts"],
            {
                "no_compression_baseline": 16,
                "tool_result_budget": 12,
                "tool_history_snip": 8,
                "context_compact": 4,
            },
        )
        self.assertEqual(result["scenario_ratio"], "4:3:2:1")
        self.assertEqual(result["tool_result_budget_ratio"], "5:3:2:2")
        self.assertEqual(
            {name: data["config_count"] for name, data in result["tool_result_budget_mix"].items()},
            {
                "small_file_read": 5,
                "medium_file_read": 3,
                "large_search_output": 2,
                "ci_log": 2,
            },
        )
        self.assertEqual(
            set(result["profiles"]),
            {"baseline_profile", "debugging_profile", "refactor_profile", "incident_review_profile"},
        )
        self.assertEqual(result["profiles"]["baseline_profile"]["config_count"], 16)
        self.assertNotIn("synthetic_stress", result)
        self.assertEqual(result["measurement_unit"], "provider_neutral_conversation_estimated_tokens")
        self.assertIn("avg_raw_prompt_estimated_tokens", result["pico_metrics"])
        self.assertIn("avg_governed_prompt_estimated_tokens", result["pico_metrics"])
        self.assertIn("avg_prompt_estimated_token_compression_ratio", result["pico_metrics"])
        self.assertIn("baseline_avg_prompt_estimated_token_compression_ratio", result["pico_metrics"])
        self.assertIn("pressure_avg_prompt_estimated_token_compression_ratio", result["pico_metrics"])
        self.assertIn("scenario_avg_prompt_estimated_token_compression_ratio", result["pico_metrics"])
        self.assertIn("tool_result_budget_case_avg_prompt_estimated_token_compression_ratio", result["pico_metrics"])
        self.assertIn("profile_avg_prompt_estimated_token_compression_ratio", result["pico_metrics"])
        self.assertIn("context_management_avg_prompt_estimated_token_compression_ratio", result["pico_metrics"])
        self.assertIn("max_prompt_estimated_token_compression_ratio", result["pico_metrics"])
        self.assertIn("current_request_preserved_rate", result["pico_metrics"])
        self.assertEqual(result["current_request_preserved_rate"], 1.0)
        self.assertGreater(result["avg_raw_prompt_estimated_tokens"], result["avg_governed_prompt_estimated_tokens"])
        self.assertEqual(
            result["avg_prompt_estimated_token_compression_ratio"],
            result["context_management_four_level"]["avg_prompt_estimated_token_compression_ratio"],
        )
        self.assertEqual(result["scenarios"]["no_compression_baseline"]["compression_triggered_rate"], 0.0)
        self.assertEqual(result["scenarios"]["tool_result_budget"]["large_result_persist_trigger_rate"], 1.0)
        self.assertEqual(result["scenarios"]["tool_result_budget"]["tool_history_snip_trigger_rate"], 0.0)
        self.assertEqual(result["tool_result_budget_mix"]["small_file_read"]["large_result_persist_trigger_rate"], 1.0)
        self.assertEqual(result["tool_result_budget_mix"]["medium_file_read"]["large_result_persist_trigger_rate"], 1.0)
        self.assertEqual(result["tool_result_budget_mix"]["large_search_output"]["large_result_persist_trigger_rate"], 1.0)
        self.assertEqual(result["tool_result_budget_mix"]["ci_log"]["large_result_persist_trigger_rate"], 1.0)
        for data in result["tool_result_budget_mix"].values():
            self.assertGreater(data["avg_prompt_estimated_token_compression_ratio"], 0.0)
        ci_rows = [row for row in result["rows"] if row.get("tool_result_budget_case") == "ci_log"]
        self.assertEqual({row["tool_name"] for row in ci_rows}, {"run_shell"})
        self.assertEqual({row["tool_name"] for row in result["rows"] if row.get("tool_result_budget_case") == "large_search_output"}, {"grep_search"})
        self.assertEqual(result["scenarios"]["tool_history_snip"]["tool_history_snip_trigger_rate"], 1.0)
        self.assertEqual(result["scenarios"]["tool_history_snip"]["context_compact_trigger_rate"], 0.0)
        self.assertEqual(result["scenarios"]["context_compact"]["context_compact_trigger_rate"], 1.0)
        self.assertEqual(result["scenarios"]["context_compact"]["post_compact_context_restored_rate"], 1.0)
        self.assertEqual(result["baseline_avg_prompt_estimated_token_compression_ratio"], 0.0)
        self.assertGreater(
            result["pressure_avg_prompt_estimated_token_compression_ratio"],
            result["baseline_avg_prompt_estimated_token_compression_ratio"],
        )
        self.assertGreater(result["large_result_persist_count"], 0)
        self.assertGreater(result["profiles"]["debugging_profile"]["large_result_persist_count"], 0)
        self.assertGreater(result["snipped_tool_result_count"], 0)
        self.assertIn("pressure_context_compact_count", result)
        self.assertEqual(result["pressure_context_compact_count"], 4)

    def test_context_task_ablation_uses_task_completion_not_context_contract(self) -> None:
        ablation = _load_ablation()

        def row(*, task_id: str, passed: bool, context_contract_met: bool, persist_count: int, input_tokens: int):
            return {
                "id": task_id,
                "category": "context-governance",
                "tags": ["context-stress", "tool-result-budget"],
                "nanocode_returncode": 0,
                "verifier_passed": True,
                "report_exists": True,
                "report_parse_valid": True,
                "expected_artifact_exists": True,
                "trace_exists": True,
                "trace_parse_valid": True,
                "trace_contract_met": True,
                "within_budget": True,
                "non_failure_stop_reason": True,
                "allowed_tools_enforced": True,
                "security_contract_met": True,
                "memory_contract_met": True,
                "resume_contract_met": True,
                "tool_path_limit_contract_met": True,
                "context_contract_met": context_contract_met,
                "passed": passed,
                "stop_reason": "stop",
                "tool_steps": 3,
                "attempts": 3,
                "large_result_persist_count": persist_count,
                "tool_history_snip_count": 0,
                "context_compact_count": 0,
                "report_summary": {
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": 20,
                        "estimated_cost_usd": 0.01,
                    }
                },
            }

        context_on = {"rows": [row(
            task_id="context_large_result_persist",
            passed=True,
            context_contract_met=True,
            persist_count=1,
            input_tokens=100,
        )]}
        context_off = {"rows": [row(
            task_id="context_large_result_persist",
            passed=False,
            context_contract_met=False,
            persist_count=0,
            input_tokens=400,
        )]}

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(ablation, "_run_context_task_variant", return_value=context_off),
        ):
            result = ablation.run_context_task_completion_ablation(
                run_root=Path(tmp),
                task_file=RUNNER_PATH.parent / "tasks.json",
                suite="all",
                timeout=5,
                model=None,
                stream=False,
                execute=True,
                context_on_artifact=context_on,
            )

        self.assertEqual(result["status"], "measured")
        self.assertEqual(result["variants"]["context_on"]["task_completion_pass_rate"], 1.0)
        self.assertEqual(result["variants"]["context_off"]["task_completion_pass_rate"], 1.0)
        self.assertEqual(result["variants"]["context_off"]["original_pass_rate"], 0.0)
        self.assertEqual(result["context_sensitive_variants"]["context_off"]["run_count"], 1)
        self.assertEqual(result["variants"]["context_on"]["large_result_persist_count"], 1)
        self.assertEqual(result["variants"]["context_off"]["large_result_persist_count"], 0)
        self.assertEqual(result["deltas"]["avg_input_tokens_delta_off_minus_on"], 300.0)

    def test_memory_ablation_uses_real_rows_and_strict_hit_definition(self) -> None:
        ablation = _load_ablation()
        benchmark_artifact = {
            "rows": [
                {
                    "id": "memory_fact_lookup",
                    "category": "memory",
                    "memory_task": True,
                    "memory_case": "fact_lookup",
                    "verifier_passed": True,
                    "memory_fallback_read_count": 0,
                    "memory_fact_hit": True,
                    "tool_steps": 1,
                    "attempts": 1,
                    "passed": True,
                },
                {
                    "id": "memory_edit_dependency",
                    "category": "memory",
                    "memory_task": True,
                    "memory_case": "edit_dependency",
                    "verifier_passed": True,
                    "memory_fallback_read_count": 2,
                    "memory_edit_dependency_success": False,
                    "tool_steps": 3,
                    "attempts": 1,
                    "passed": False,
                },
                {
                    "id": "memory_irrelevant_guard",
                    "category": "memory",
                    "memory_task": True,
                    "memory_case": "conflict_guard",
                    "verifier_passed": True,
                    "memory_current_truth_read_count": 1,
                    "tool_steps": 2,
                    "attempts": 1,
                    "passed": True,
                },
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            result = ablation.run_memory_ablation(
                run_root=Path(tmp),
                repetitions=1,
                benchmark_artifact=benchmark_artifact,
            )

        self.assertEqual(result["status"], "measured")
        self.assertEqual(result["task_count"], 3)
        self.assertEqual(result["runs_per_variant"]["memory_on"], 2)
        self.assertEqual(result["runs_per_variant"]["memory_off"], 0)
        self.assertEqual(result["runs_per_variant"]["memory_irrelevant"], 1)
        self.assertEqual(result["variants"]["memory_on"]["repeated_reads"], 2)
        self.assertEqual(result["variants"]["memory_on"]["memory_hit_rate"], 0.5)
        self.assertEqual(result["variants"]["memory_off"]["memory_hit_rate"], 0.0)
        self.assertEqual(result["variants"]["memory_off"]["status"], "not_measured")
        self.assertEqual(result["variants"]["memory_irrelevant"]["repeated_reads"], 1)
        self.assertEqual(result["variants"]["memory_irrelevant"]["memory_hit_rate"], 0.0)
        self.assertEqual(result["variants"]["memory_irrelevant"]["correct_rate"], 1.0)

    def test_memory_ablation_does_not_fabricate_metrics_without_rows(self) -> None:
        ablation = _load_ablation()

        with tempfile.TemporaryDirectory() as tmp:
            result = ablation.run_memory_ablation(run_root=Path(tmp), repetitions=1)

        self.assertEqual(result["status"], "not_measured")
        self.assertEqual(result["runs_per_variant"]["memory_on"], 0)
        self.assertEqual(result["variants"]["memory_on"]["memory_hit_rate"], 0.0)

    def test_recovery_ablation_separates_primitives_from_e2e_rows(self) -> None:
        ablation = _load_ablation()
        benchmark_artifact = {
            "rows": [
                {
                    "id": "resume_checkpoint_goal",
                    "scenario": "resume",
                    "tags": ["resume"],
                    "recovery_case_category": "checkpoint_resume",
                    "resume_session_exists": True,
                    "resume_output_restored": True,
                    "resume_contract_met": True,
                    "passed": True,
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            result = ablation.run_recovery_ablation(
                run_root=Path(tmp),
                repetitions=1,
                benchmark_artifact=benchmark_artifact,
            )

        primitive_enabled = result["primitive_variants"]["resume_enabled"]
        primitive_disabled = result["primitive_variants"]["resume_disabled"]
        e2e_enabled = result["e2e_variants"]["resume_enabled"]
        self.assertEqual(result["primitive_task_count"], 6)
        self.assertEqual(result["primitive_runs_per_variant"], 6)
        self.assertEqual(result["e2e_status"], "measured")
        self.assertEqual(e2e_enabled["resume_success_rate"], 1.0)
        self.assertGreater(primitive_enabled["resume_success_rate"], primitive_disabled["resume_success_rate"])
        self.assertEqual(primitive_enabled["orphan_repair_count"], 1)
        self.assertNotIn("stale_reanchor_rate", primitive_enabled)
        self.assertNotIn("workspace_drift_detection_rate", primitive_enabled)
        self.assertNotIn("unsupported_capability_counts", result)

    def test_ablation_runner_writes_artifacts_without_running_harness(self) -> None:
        ablation = _load_ablation()

        with tempfile.TemporaryDirectory() as tmp:
            artifact = ablation.run_ablation(
                Namespace(
                    task_file=str(RUNNER_PATH.parent / "tasks.json"),
                    output_root=tmp,
                    run_name="ablation-test",
                    suite="all",
                    timeout=5,
                    model=None,
                    stream=False,
                    repetitions=1,
                    recovery_repetitions=1,
                    skip_harness=True,
                    harness_artifact=None,
                    run_memory_ablation=False,
                    run_resume_ablation=False,
                    dry_run=False,
                )
            )
            root = Path(tmp) / "ablation-test"

            self.assertTrue((root / "ablation.json").exists())
            self.assertTrue((root / "harness-regression-v2.json").exists())
            self.assertTrue((root / "context-ablation-v2.json").exists())
            self.assertTrue((root / "context-task-ablation-v2.json").exists())
            self.assertTrue((root / "memory-ablation-v2.json").exists())
            self.assertTrue((root / "recovery-ablation-v2.json").exists())
            self.assertTrue((root / "ablation-report.md").exists())
            self.assertTrue((root / "DATA_PROVENANCE.md").exists())

        self.assertEqual(artifact["schema_version"], 2)
        self.assertEqual(artifact["suites"]["harness_regression"]["status"], "skipped")
        self.assertEqual(artifact["suites"]["context_task_completion_ablation"]["status"], "not_measured")
        self.assertEqual(artifact["suites"]["working_memory_ablation"]["status"], "not_measured")
        self.assertEqual(artifact["suites"]["recovery_resume_ablation"]["e2e_status"], "not_measured")
        self.assertEqual(artifact["summary"]["context_current_request_preserved_rate"], 1.0)
        self.assertEqual(artifact["summary"]["context_task_status"], "not_measured")


if __name__ == "__main__":
    unittest.main()
