from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import yaml

import test_workflow_gates as gates
from execution_readiness import core_actions_sha256, requires_manual, validate_case_readiness, validate_preflight
from result_explanation import explain_result
from validate_execution_delivery import xlsx_rows
from backfill_case_execution_results import format_human_result

FRAMEWORK_DIR = Path(__file__).resolve().parents[2] / "builtin_framework"
sys.path.insert(0, str(FRAMEWORK_DIR))
from common.execution_preflight import filter_targets, prepared_nodes


CONFIRMATION = {"confirmed_by": "测试负责人", "confirmed_at": "2026-10-08T14:00:00+08:00"}
PREPARATION = {"id": "activity", "description": "准备买家A的限购活动及历史购买数据", "owner": "测试负责人", "acceptance": "活动生效，历史购买数量为2，记录活动ID"}


class ExecutionReadinessTests(unittest.TestCase):
    def setUp(self):
        self.print_patch = patch("builtins.print")
        self.print_patch.start()
        self.workflow = gates.WorkflowGateTests()
        self.workflow.setUp()
        self.case = self.workflow.case_document["test_cases"][0]

    def tearDown(self):
        self.workflow.tearDown()
        self.print_patch.stop()

    def mark_manual(self):
        self.case["case_title"] = "扫码付款并完成交易"
        self.case["execution_readiness"] = {
            "level": "manual", "reason": "需要本人使用手机扫码并确认付款",
            "human_actions": ["使用手机扫码，核对金额后付款"], "preparations": [],
        }

    def mark_assisted(self):
        self.case["execution_readiness"] = {
            "level": "assisted", "reason": "活动和历史订单数据只能人工准备",
            "human_actions": [], "preparations": [deepcopy(PREPARATION)],
        }

    def plan(self, route, preparations=None):
        gates.write_json(self.workflow.cases, self.workflow.case_document)
        result = self.workflow.create_case_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.workflow.create_execution_plan()
        document = json.loads(self.workflow.execution_plan.read_text(encoding="utf-8"))
        document["execution_meta"]["preflight_acknowledgement"] = deepcopy(CONFIRMATION)
        decision = {"disposition": route, "preparations": preparations or []}
        if route != "run":
            decision.update(CONFIRMATION)
            decision["reason"] = "测试负责人安排人工付款" if route == "manual" else "测试夹具构造了已移除的旧执行路径"
            document["execution_meta"]["resource_budget"] = {"estimated_attempts": 4, "estimated_minutes": 20}
        document["browser_execution_plan"][0]["readiness"] = decision
        gates.write_json(self.workflow.execution_plan, document)
        return document

    def validate_plan(self):
        return gates.run(gates.VALIDATE_PLAN, "--case-gate", self.workflow.case_gate,
                         "--final-cases", self.workflow.cases, "--execution-plan", self.workflow.execution_plan,
                         "--output", self.workflow.plan_gate)

    def test_missing_classification_and_payment_are_rejected_during_design(self):
        self.case.pop("execution_readiness")
        gates.write_json(self.workflow.cases, self.workflow.case_document)
        result = self.workflow.create_case_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("execution_readiness.level", result.stderr)
        self.case["execution_readiness"] = {"level": "auto"}
        for title in ("交易", "完成交易", "扫码确认订单", "付款成功", "支付成功"):
            self.case["case_title"] = title
            self.assertTrue(any("manual" in error for error in validate_case_readiness(self.case)))

    def test_paid_order_prerequisite_is_distinct_from_payment_action(self):
        self.case["case_title"] = "查看已付款订单详情"
        self.case["preconditions"] = ["已有一笔付款成功的订单，自动从独立测试数据池分配"]
        self.assertEqual(validate_case_readiness(self.case), [])
        self.case["preconditions"] = ["必须人工准备历史订单"]
        self.assertTrue(validate_case_readiness(self.case))

    def test_payment_queries_and_refund_configuration_are_not_funds_actions(self):
        self.mark_assisted()
        for title, action in (
            ("查询支付金额", "查看支付金额"),
            ("查询支付流水单号", "查询交易单号"),
            ("退款设置保存", "修改并保存退款天数"),
        ):
            self.case["case_title"] = title
            self.case["formal_steps"] = [{"step_no": 1, "action": action}]
            self.assertFalse(requires_manual(self.case), (title, action))
            self.assertEqual(validate_case_readiness(self.case), [])

    def test_review_cannot_override_actual_payment_scan_or_refund(self):
        self.mark_assisted()
        self.case["case_title"] = "查看业务信息"
        for action in (
            "点击【支付】", "扫描活动码", "用户成功购买付费券包",
            "执行每日退款判定任务", "触发自动退款", "确认重新退款",
            "点击【确认重新退款】", "查看订单然后完成支付",
        ):
            self.case["formal_steps"] = [{"step_no": 1, "action": action}]
            self.case["core_operation_review"] = {
                "classification": "automation_core", "reviewed_by": "测试复核人",
                "reason": "错误的只读复核", "core_actions": [action],
                "core_actions_sha256": core_actions_sha256([action]),
                "operation_scope": "read_only_or_configuration",
            }
            self.assertTrue(requires_manual(self.case), action)
            self.assertTrue(any("manual" in error for error in validate_case_readiness(self.case)))

    def test_refund_dialog_review_is_bound_to_exact_unconfirmed_action(self):
        action = "点击【重新退款】"
        self.mark_assisted()
        self.case["case_title"] = "点击重新退款打开确认弹窗"
        self.case["formal_steps"] = [{"step_no": 1, "action": action}]
        self.assertTrue(requires_manual(self.case))
        self.case["core_operation_review"] = {
            "classification": "automation_core", "reviewed_by": "测试复核人",
            "reason": "只打开二次确认弹窗，不确认退款",
            "core_actions": [action], "core_actions_sha256": core_actions_sha256([action]),
            "operation_scope": "confirmation_dialog_only",
        }
        self.assertFalse(requires_manual(self.case))
        self.case["formal_steps"].append({"step_no": 2, "action": "点击【确认重新退款】"})
        self.assertTrue(requires_manual(self.case))
        self.case["formal_steps"] = [{"step_no": 1, "action": "查看弹窗"}]
        self.case["case_title"] = "支付成功"
        self.assertTrue(requires_manual(self.case), "A stale review must not hide changed core steps")

    def test_structured_action_target_cannot_hide_a_funds_submission(self):
        self.mark_assisted()
        self.case["case_title"] = "业务操作"
        for action, target in (("点击", "支付按钮"), ("提交", "退款申请"), ("click", "payment button")):
            self.case["formal_steps"] = [{"step_no": 1, "action": action, "target": target}]
            self.assertTrue(requires_manual(self.case), (action, target))

    def test_pending_preparation_stops_before_execution_and_produces_notice(self):
        self.mark_assisted()
        self.plan("run", [{"id": "activity", "status": "pending"}])
        result = self.validate_plan()
        self.assertNotEqual(result.returncode, 0)
        notice = (self.workflow.root / "10_执行前人工准备清单.md").read_text(encoding="utf-8")
        for phrase in (PREPARATION["description"], PREPARATION["owner"], PREPARATION["acceptance"], "待人工准备"):
            self.assertIn(phrase, notice)
        with patch.dict(os.environ, {"AI_QA_WORKFLOW_DIR": str(self.workflow.root)}):
            with self.assertRaisesRegex(ValueError, "尚未启动任何测试"):
                prepared_nodes(FRAMEWORK_DIR)

    def test_preparations_require_real_confirmations_and_exact_item_ids(self):
        self.mark_assisted()
        ready = {"id": "activity", "status": "ready", **CONFIRMATION, "evidence": "活动ID ACT-001，验收数量为2"}
        document = self.plan("run", [ready])
        self.assertEqual(self.validate_plan().returncode, 0)
        for field in ("confirmed_by", "confirmed_at", "evidence"):
            changed = deepcopy(document)
            changed["browser_execution_plan"][0]["readiness"]["preparations"][0].pop(field)
            self.assertTrue(validate_preflight(self.workflow.case_document["test_cases"], changed["browser_execution_plan"], changed["execution_meta"]))
        changed = deepcopy(document)
        changed["execution_meta"].pop("preflight_acknowledgement")
        self.assertTrue(validate_preflight(self.workflow.case_document["test_cases"], changed["browser_execution_plan"], changed["execution_meta"]))
        document["browser_execution_plan"][0]["readiness"]["preparations"].append(ready)
        self.assertTrue(validate_preflight(self.workflow.case_document["test_cases"], document["browser_execution_plan"], document["execution_meta"]))

    def test_manual_case_cannot_enter_run_queue(self):
        self.mark_manual()
        self.plan("run")
        result = self.validate_plan()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("禁止加入自动执行队列", result.stderr)

    def generate_results(self, route, reason_code, resource_accounting=False):
        metric_arguments = []
        if resource_accounting:
            self.workflow.case_document["case_source"].update({"requirement_source": "existing_xlsx_baseline", "requirement_completeness": "not_assessed"})
        document = self.plan(route, [{"id": "activity", "status": "pending"}] if route == "skip" else [])
        if resource_accounting:
            document["execution_meta"]["resource_accounting_version"] = "1.0"
            document["execution_meta"]["resource_budget"].update({
                "estimated_model_tokens": None,
                "token_budget_policy": {"quality_constraints_may_not_be_reduced": True, "checkpoint_on_overrun": True},
            })
            gates.write_json(self.workflow.execution_plan, document)
            metrics_path = self.workflow.root / "20_workflow_run_metrics.json"
            run_id = gates.initialize_run(metrics_path, project_dir=self.workflow.root, workflow_mode="full", trigger="merge_regression")
            stage = gates.start_stage(metrics_path, run_id=run_id, name="browser_execution", executor="deterministic")
            gates.finish_stage(metrics_path, run_id=run_id, stage=stage, status="completed")
            gates.finalize_run(metrics_path, run_id=run_id, status="completed")
            metric_arguments = ["--run-metrics", metrics_path, "--workflow-run-id", run_id]
        result = self.validate_plan()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.workflow.create_execution_results()
        original = json.loads(self.workflow.execution.read_text(encoding="utf-8"))
        mapping = {"case_execution_mapping": [{"case_id": case["case_id"], "test_node": f"tests/test_ready.py::test_{case['case_id']}"} for case in self.workflow.case_document["test_cases"]]}
        mapping_path = self.workflow.root / "case_execution_mapping.yaml"
        gates.write_json(mapping_path, mapping)
        raw_results = original["execution_results"][1:]
        for item in raw_results:
            item["test_node"] = f"tests/test_ready.py::test_{item['case_id']}"
        status = {"script_execution_status": raw_results, "scope_matrix": original["execution_meta"]["scope_matrix"], "resource_budget": {"actual_minutes": 1}}
        status_path = self.workflow.root / "script_status.yaml"
        gates.write_json(status_path, status)
        generated = gates.run(gates.GENERATE_EXECUTION, "--mapping", mapping_path, "--script-status", status_path,
                             "--final-cases", self.workflow.cases, "--case-gate", self.workflow.case_gate,
                             "--execution-plan", self.workflow.execution_plan, "--execution-plan-gate", self.workflow.plan_gate,
                             "--output", self.workflow.execution, *metric_arguments)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        output = yaml.safe_load(self.workflow.execution.read_text(encoding="utf-8"))
        first = output["execution_results"][0]
        self.assertEqual(first["status"], "not_run")
        self.assertEqual(first["not_run_reason"], reason_code)
        self.assertEqual(first["attempts"], [])
        self.assertEqual(first["evidence"], {})
        self.assertIn("负责人", first["human_reason"])
        if resource_accounting:
            validated = gates.run(gates.VALIDATE_EXECUTION, "--phase", "execution", "--case-gate", self.workflow.case_gate,
                                  "--final-cases", self.workflow.cases, "--execution-plan-gate", self.workflow.plan_gate,
                                  "--execution-plan", self.workflow.execution_plan, "--execution-results", self.workflow.execution,
                                  "--screenshots-dir", self.workflow.screenshots, "--require-resource-accounting",
                                  "--output", self.workflow.execution_gate, *metric_arguments)
        else:
            validated = self.workflow.validate_execution_results()
        self.assertEqual(validated.returncode, 0, validated.stderr)
        with patch.dict(os.environ, {"AI_QA_WORKFLOW_DIR": str(self.workflow.root)}):
            nodes = prepared_nodes(FRAMEWORK_DIR)
            self.assertEqual(nodes, ["tests/test_ready.py::test_TC-002", "tests/test_ready.py::test_TC-003"])
            self.assertEqual(filter_targets(["tests/test_ready.py"], nodes), nodes)
            self.assertEqual(filter_targets(["tests/test_ready.py::test_TC-001"], nodes), [])
            stale = deepcopy(document)
            stale["execution_meta"]["resource_budget"]["estimated_minutes"] = 21
            gates.write_json(self.workflow.execution_plan, stale)
            with self.assertRaisesRegex(ValueError, "Source changed after gate"):
                prepared_nodes(FRAMEWORK_DIR)
        # Restore the gated plan and validate human columns in real workbook output.
        gates.write_json(self.workflow.execution_plan, document)
        generated_xlsx = gates.run(gates.GENERATE_XLSX, "--source", self.workflow.cases, "--case-gate", self.workflow.case_gate,
                                   "--output", self.workflow.template_xlsx)
        self.assertEqual(generated_xlsx.returncode, 0, generated_xlsx.stderr)
        rows = xlsx_rows(self.workflow.template_xlsx)
        self.assertIn("执行级别", rows[0])
        self.assertIn("人工准备清单", rows[0])
        with gates.zipfile.ZipFile(self.workflow.template_xlsx) as archive:
            sheet = gates.ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            cell = sheet.find("x:sheetData/x:row[@r='2']/x:c[@r='E2']", {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"})
            self.assertGreaterEqual(int(cell.get("s")), 7)
        backfill = gates.run(gates.BACKFILL, "--source", self.workflow.cases, "--case-gate", self.workflow.case_gate,
                            "--execution-yaml", self.workflow.execution, "--execution-gate", self.workflow.execution_gate,
                            "--template-xlsx", self.workflow.template_xlsx, "--output-md", self.workflow.backfill_md,
                            "--output-xlsx", self.workflow.backfill_xlsx)
        self.assertEqual(backfill.returncode, 0, backfill.stderr)
        rows = xlsx_rows(self.workflow.backfill_xlsx)
        self.assertIn("原因说明（给测试人员）", rows[0])
        self.assertEqual(rows[1][rows[0].index("技术原因（给Agent）")], reason_code)
        delivery = gates.run(gates.VALIDATE_EXECUTION, "--phase", "delivery", "--case-gate", self.workflow.case_gate,
                             "--final-cases", self.workflow.cases, "--execution-plan-gate", self.workflow.plan_gate,
                             "--execution-plan", self.workflow.execution_plan, "--execution-results", self.workflow.execution,
                             "--screenshots-dir", self.workflow.screenshots, "--template-xlsx", self.workflow.template_xlsx,
                             "--backfill-xlsx", self.workflow.backfill_xlsx, "--viewer-check", "skip", "--output", self.workflow.delivery_gate, *metric_arguments)
        self.assertEqual(delivery.returncode, 0, delivery.stderr)
        report = gates.run(gates.GENERATE_REPORT, "--final-cases", self.workflow.cases, "--case-gate", self.workflow.case_gate,
                           "--execution-yaml", self.workflow.execution, "--execution-gate", self.workflow.execution_gate,
                           "--delivery-gate", self.workflow.delivery_gate, "--project-name", "人工准备回归",
                           "--backfill-xlsx-path", self.workflow.backfill_xlsx, "--output", self.workflow.report,
                           "--output-docx", self.workflow.report_docx, *metric_arguments)
        self.assertEqual(report.returncode, 0, report.stderr)
        report_text = self.workflow.report.read_text(encoding="utf-8")
        self.assertIn("交给人工执行：1", report_text)
        self.assertNotIn("营销路由", report_text)
        self.assertNotIn("本轮没有正式 PRD", report_text)
        if resource_accounting:
            self.assertIn("需求完整性未评估", report_text)
            self.assertIn("工作流时长与 Token 核算", report_text)
            metrics_document = json.loads(metrics_path.read_text(encoding="utf-8"))
            metrics_document["runs"][0]["trigger"] = "valid_metadata_changed_after_gate"
            gates.write_json(metrics_path, metrics_document)
            refused = gates.run(gates.GENERATE_REPORT, "--final-cases", self.workflow.cases, "--case-gate", self.workflow.case_gate,
                               "--execution-yaml", self.workflow.execution, "--execution-gate", self.workflow.execution_gate,
                               "--delivery-gate", self.workflow.delivery_gate, "--project-name", "资源账本回归",
                               "--backfill-xlsx-path", self.workflow.backfill_xlsx, "--output", self.workflow.report,
                               "--output-docx", self.workflow.report_docx, *metric_arguments)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("Source changed after gate", refused.stderr)

    def test_v3_accounting_and_manual_transfer_survive_the_delivery_chain(self):
        self.mark_manual()
        self.generate_results("manual", "manual_execution_required", resource_accounting=True)

    def test_manual_transfer_survives_results_backfill_and_p0_gate(self):
        self.mark_manual()
        self.generate_results("manual", "manual_execution_required")

    def test_removed_skip_disposition_is_rejected(self):
        self.mark_assisted()
        self.plan("skip", [{"id": "activity", "status": "pending"}])
        result = self.validate_plan()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("不允许使用 skip", result.stderr)

    def test_failures_explain_business_difference_without_raw_tool_errors(self):
        reason, action = explain_result({"status": "failed", "failed_step_no": 3, "result_summary": {"expected": "空收件人不能下单", "actual": "收件人为空仍创建了订单"}})
        for phrase in ("第3步", "空收件人不能下单", "仍创建了订单"):
            self.assertIn(phrase, reason)
        self.assertIn("提交缺陷", action)
        reason, action = explain_result({"status": "blocked", "blocker_reason": "Locator.fill: TimeoutError input[type=file]", "attempts": [{}]})
        self.assertNotIn("Locator", reason)
        self.assertIn("测试工具", reason)
        actual, _, _ = format_human_result({"status": "blocked", "blocker_reason": "缺少数据", "attempts": [{}]}, {})
        self.assertNotIn("已完成登录", actual)
        self.assertNotIn("批量下单", actual)

    def test_manual_reason_cannot_bypass_an_automatic_p0_plan(self):
        self.assertEqual(self.workflow.create_case_gate().returncode, 0)
        self.assertEqual(self.workflow.create_plan_gate().returncode, 0)
        self.workflow.create_execution_results()
        document = json.loads(self.workflow.execution.read_text(encoding="utf-8"))
        first = document["execution_results"][0]
        first.update({"status": "not_run", "stability": "not_run", "not_run_reason": "manual_execution_required", "attempts": [], "execution_trace": None, "evidence": {}, "produced_data": {}})
        self.workflow.refresh_execution_summary(document)
        gates.write_json(self.workflow.execution, document)
        result = self.workflow.validate_execution_results()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot claim manual transfer", result.stderr)


if __name__ == "__main__":
    unittest.main()
