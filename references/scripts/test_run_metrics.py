from __future__ import annotations

import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


FRAMEWORK_ROOT = Path(__file__).resolve().parents[2] / "builtin_framework"
if str(FRAMEWORK_ROOT) not in sys.path:
    sys.path.insert(0, str(FRAMEWORK_ROOT))

from common.run_metrics import (  # noqa: E402
    current_run,
    finalize_run,
    finish_stage,
    initialize_run,
    pause_run,
    record_token_usage,
    resume_run,
    start_stage,
    validate_metrics_document,
)
from run_tests import TestRunner as FrameworkTestRunner  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from generate_test_report import resource_accounting_rows  # noqa: E402
from prepare_external_framework_runtime import ESSENTIAL_DIRS, ESSENTIAL_FILES, main as prepare_runtime  # noqa: E402


class WorkflowRunMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.metrics = self.root / "20_workflow_run_metrics.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_exact_usage_does_not_double_count_cache_or_reasoning(self) -> None:
        run_id = initialize_run(
            self.metrics,
            project_dir=self.root,
            workflow_mode="full",
            trigger="unit_test",
        )
        model_stage = start_stage(
            self.metrics,
            run_id=run_id,
            name="design",
            executor="model",
        )
        record_token_usage(
            self.metrics,
            run_id=run_id,
            stage=model_stage,
            source="provider_response",
            input_tokens=100,
            output_tokens=25,
            cached_input_tokens=40,
            reasoning_tokens=5,
            total_tokens=125,
        )
        finish_stage(
            self.metrics,
            run_id=run_id,
            stage=model_stage,
            status="completed",
            token_coverage="exact",
        )
        browser_stage = start_stage(
            self.metrics,
            run_id=run_id,
            name="browser_execution",
            executor="deterministic",
        )
        finish_stage(
            self.metrics,
            run_id=run_id,
            stage=browser_stage,
            status="completed",
        )
        finalize_run(self.metrics, run_id=run_id, status="completed")

        run = current_run(self.metrics, run_id)
        self.assertEqual(run["token_usage"]["input_tokens"], 100)
        self.assertEqual(run["token_usage"]["output_tokens"], 25)
        self.assertEqual(run["token_usage"]["cached_input_tokens"], 40)
        self.assertEqual(run["token_usage"]["reasoning_tokens"], 5)
        self.assertEqual(run["token_usage"]["measured_total_tokens"], 125)
        self.assertEqual(run["token_usage"]["exact_total_tokens"], 125)
        self.assertEqual(run["token_usage"]["measurement_status"], "exact")
        self.assertEqual(run["stages"][1]["token_coverage"], "not_applicable")
        self.assertEqual(validate_metrics_document(self.metrics, run_id), [])

    def test_unavailable_model_usage_is_not_reported_as_zero(self) -> None:
        run_id = initialize_run(
            self.metrics,
            project_dir=self.root,
            workflow_mode="full",
            trigger="unit_test",
        )
        stage = start_stage(self.metrics, run_id=run_id, name="requirements", executor="model")
        finish_stage(
            self.metrics,
            run_id=run_id,
            stage=stage,
            status="completed",
            token_coverage="unavailable",
            unavailable_reason="Host did not expose model usage metadata.",
        )
        finalize_run(self.metrics, run_id=run_id, status="completed")

        usage = current_run(self.metrics, run_id)["token_usage"]
        self.assertEqual(usage["measurement_status"], "unavailable")
        self.assertEqual(usage["measured_total_tokens"], 0)
        self.assertIsNone(usage["exact_total_tokens"])
        self.assertEqual(validate_metrics_document(self.metrics, run_id), [])

    def test_partial_usage_has_measured_but_no_exact_total(self) -> None:
        run_id = initialize_run(
            self.metrics,
            project_dir=self.root,
            workflow_mode="full",
            trigger="unit_test",
        )
        stage = start_stage(self.metrics, run_id=run_id, name="review", executor="mixed")
        record_token_usage(
            self.metrics,
            run_id=run_id,
            stage=stage,
            source="platform_export",
            input_tokens=20,
            output_tokens=10,
        )
        finish_stage(
            self.metrics,
            run_id=run_id,
            stage=stage,
            status="completed",
            token_coverage="partial",
            unavailable_reason="One model call did not return usage metadata.",
        )
        finalize_run(self.metrics, run_id=run_id, status="completed")

        usage = current_run(self.metrics, run_id)["token_usage"]
        self.assertEqual(usage["measurement_status"], "partial")
        self.assertEqual(usage["measured_total_tokens"], 30)
        self.assertIsNone(usage["exact_total_tokens"])
        self.assertEqual(validate_metrics_document(self.metrics, run_id), [])

    def test_multiple_completed_runs_are_preserved(self) -> None:
        first = initialize_run(
            self.metrics,
            project_dir=self.root,
            workflow_mode="execution_only",
            trigger="unit_test",
        )
        finalize_run(self.metrics, run_id=first, status="completed")
        second = initialize_run(
            self.metrics,
            project_dir=self.root,
            workflow_mode="execution_only",
            trigger="unit_test",
        )
        finalize_run(self.metrics, run_id=second, status="completed")

        document = json.loads(self.metrics.read_text(encoding="utf-8"))
        self.assertEqual(len(document["runs"]), 2)
        self.assertEqual(document["latest_run_id"], second)
        self.assertEqual(validate_metrics_document(self.metrics, first), [])
        self.assertEqual(validate_metrics_document(self.metrics, second), [])

    def test_new_run_is_rejected_while_another_run_is_active(self) -> None:
        initialize_run(
            self.metrics,
            project_dir=self.root,
            workflow_mode="full",
            trigger="unit_test",
        )
        with self.assertRaisesRegex(ValueError, "already active"):
            initialize_run(
                self.metrics,
                project_dir=self.root,
                workflow_mode="full",
                trigger="unit_test",
            )

    def test_pause_is_separated_from_active_duration(self) -> None:
        run_id = initialize_run(
            self.metrics,
            project_dir=self.root,
            workflow_mode="full",
            trigger="unit_test",
        )
        pause_run(self.metrics, run_id=run_id, reason="Waiting for deployment")
        with self.assertRaisesRegex(ValueError, "resume it first"):
            finalize_run(self.metrics, run_id=run_id, status="cancelled")
        resume_run(self.metrics, run_id=run_id)
        finalize_run(self.metrics, run_id=run_id, status="completed")

        run = current_run(self.metrics, run_id)
        self.assertEqual(len(run["pauses"]), 1)
        self.assertEqual(run["pauses"][0]["reason"], "Waiting for deployment")
        self.assertGreaterEqual(run["paused_seconds"], 0)
        self.assertGreaterEqual(run["active_duration_seconds"], 0)
        self.assertEqual(validate_metrics_document(self.metrics, run_id), [])

    def test_invalid_token_counts_are_rejected_and_tampering_is_detected(self) -> None:
        run_id = initialize_run(
            self.metrics,
            project_dir=self.root,
            workflow_mode="full",
            trigger="unit_test",
        )
        stage = start_stage(self.metrics, run_id=run_id, name="design", executor="model")
        with self.assertRaisesRegex(ValueError, "total_tokens"):
            record_token_usage(
                self.metrics,
                run_id=run_id,
                stage=stage,
                source="provider_response",
                input_tokens=10,
                output_tokens=5,
                total_tokens=99,
            )
        with self.assertRaisesRegex(ValueError, "cached_input_tokens"):
            record_token_usage(
                self.metrics,
                run_id=run_id,
                stage=stage,
                source="provider_response",
                input_tokens=10,
                output_tokens=5,
                cached_input_tokens=11,
            )
        record_token_usage(
            self.metrics,
            run_id=run_id,
            stage=stage,
            source="provider_response",
            input_tokens=10,
            output_tokens=5,
        )
        finish_stage(
            self.metrics,
            run_id=run_id,
            stage=stage,
            status="completed",
            token_coverage="exact",
        )
        finalize_run(self.metrics, run_id=run_id, status="completed")

        document = json.loads(self.metrics.read_text(encoding="utf-8"))
        document["runs"][0]["stages"][0]["token_events"][0]["total_tokens"] = 999
        self.metrics.write_text(json.dumps(document), encoding="utf-8")
        errors = validate_metrics_document(self.metrics, run_id)
        self.assertTrue(any("total_tokens must equal" in error for error in errors), errors)

    def test_builtin_runner_automatically_records_deterministic_execution(self) -> None:
        runner = object.__new__(FrameworkTestRunner)
        runner.workflow_metrics_path = self.metrics
        runner.workflow_project_dir = self.root
        runner._run_all_impl = lambda **kwargs: {"success": True}

        result = FrameworkTestRunner.run_all(runner, return_details=True)

        run = current_run(self.metrics, result["workflow_run_id"])
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["token_usage"]["measurement_status"], "not_applicable")
        self.assertEqual(run["token_usage"]["exact_total_tokens"], 0)
        self.assertEqual(run["stages"][0]["name"], "browser_execution")
        self.assertEqual(run["stages"][0]["executor"], "deterministic")
        self.assertEqual(run["stages"][0]["status"], "completed")

    def test_report_rows_disclose_exact_tokens_and_duration(self) -> None:
        run_id = initialize_run(
            self.metrics,
            project_dir=self.root,
            workflow_mode="full",
            trigger="unit_test",
        )
        stage = start_stage(self.metrics, run_id=run_id, name="report", executor="model")
        record_token_usage(
            self.metrics,
            run_id=run_id,
            stage=stage,
            source="codex_usage_snapshot",
            input_tokens=80,
            output_tokens=20,
            cached_input_tokens=25,
            reasoning_tokens=4,
        )
        finish_stage(
            self.metrics,
            run_id=run_id,
            stage=stage,
            status="completed",
            token_coverage="exact",
        )
        finalize_run(self.metrics, run_id=run_id, status="completed")

        rows, stage_rows, note = resource_accounting_rows(current_run(self.metrics, run_id))
        rendered = dict(rows)
        self.assertEqual(rendered["Token 统计状态"], "精确")
        self.assertEqual(rendered["输入 Token"], "80")
        self.assertEqual(rendered["输出 Token"], "20")
        self.assertEqual(rendered["精确总 Token"], "100")
        self.assertIn("实际活动时长", rendered)
        self.assertEqual(stage_rows[0][1], "报告编制")
        self.assertIn("不重复加入总数", note)

    def test_runtime_copy_binds_metrics_to_current_project(self) -> None:
        framework = self.root / "framework"
        runtime = self.root / "runtime" / "external_framework_runtime"
        for name in ESSENTIAL_DIRS:
            (framework / name).mkdir(parents=True, exist_ok=True)
        for name in ESSENTIAL_FILES:
            (framework / name).write_text(f"fixture for {name}\n", encoding="utf-8")
        binding = self.root / "11_framework_binding.yaml"
        binding.write_text(
            json.dumps(
                {
                    "framework_binding": {
                        "framework_root": str(framework),
                        "runtime_workspace": str(runtime),
                        "runtime_config": {"paths": {"tests": "tests"}},
                    }
                }
            ),
            encoding="utf-8",
        )

        with patch.object(sys, "argv", ["prepare_external_framework_runtime.py", "--binding", str(binding)]):
            prepare_runtime()

        import yaml

        config = yaml.safe_load((runtime / "config.yaml").read_text(encoding="utf-8"))
        self.assertTrue((runtime / "run_tests.py").is_file())
        self.assertEqual(
            Path(config["paths"]["workflow_metrics"]),
            self.root / "20_workflow_run_metrics.json",
        )
        self.assertEqual(Path(config["paths"]["workflow_project_dir"]), self.root)


if __name__ == "__main__":
    unittest.main()
