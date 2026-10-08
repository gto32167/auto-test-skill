from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from semantic_contract import normalized_test_intent, semantic_contract_hash
from execution_quality import (
    P0_STOP_ACTUAL,
    P0_STOP_REASON,
    PRIORITY_EXECUTION_POLICY,
    evaluate_p0_stop_gate,
    execution_metrics,
    priority_sorted_cases,
    retry_decision,
)
from validate_execution_delivery import requires_ui_screenshot
from workflow_gate_common import text as gate_text


VALIDATE_CASES = SCRIPT_DIR / "validate_case_design.py"
VALIDATE_PLAN = SCRIPT_DIR / "validate_execution_plan.py"
GENERATE_XLSX = SCRIPT_DIR / "generate_case_xlsx.py"
GENERATE_EXECUTION = SCRIPT_DIR / "generate_case_execution_results.py"
VALIDATE_EXECUTION = SCRIPT_DIR / "validate_execution_delivery.py"
BACKFILL = SCRIPT_DIR / "backfill_case_execution_results.py"
GENERATE_REPORT = SCRIPT_DIR / "generate_test_report.py"
ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
TEMPLATE_DIR = SCRIPT_DIR.parent.parent / "assets" / "templates"
COMPAT_BACKFILL = TEMPLATE_DIR / "gen_xlsx_backfill.py"


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def run(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *(str(arg) for arg in args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


class WorkflowGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.requirements = self.root / "05_requirements.yaml"
        self.points = self.root / "05_test_points.yaml"
        self.cases = self.root / "06_final_test_cases.yaml"
        self.case_gate = self.root / "07_case_design_gate.json"
        self.template_xlsx = self.root / "08_测试用例_模板版.xlsx"
        self.execution_plan = self.root / "10_browser_execution_plan.yaml"
        self.plan_gate = self.root / "10_execution_plan_gate.json"
        self.execution = self.root / "11_test_execution_results.yaml"
        self.execution_gate = self.root / "12_execution_gate.json"
        self.backfill_md = self.root / "18_测试用例_执行回填版.md"
        self.backfill_xlsx = self.root / "18_测试用例_执行回填版.xlsx"
        self.delivery_gate = self.root / "19_delivery_gate.json"
        self.report = self.root / "23_正式测试报告.md"
        self.report_docx = self.root / "23_正式测试报告.docx"
        self.screenshots = self.root / "artifacts" / "screenshots"
        self.screenshots.mkdir(parents=True)

        write_json(
            self.requirements,
            {"requirements": [{"requirement_id": "REQ-001", "title": "创建订单", "disposition": "covered"}]},
        )
        write_json(
            self.points,
            {
                "coverage_profile": {
                    "mode": "full",
                    "core_form_fields_present": True,
                    "required_granularities": ["scenario", "field"],
                    "priority_policy": "strict_p0_p1_p2",
                },
                "test_points": [
                    {
                        "test_point_id": "TP-001",
                        "requirement_ids": ["REQ-001"],
                        "feature_group": "买家端-订单创建页",
                        "granularity": "scenario",
                        "polarity": "positive",
                        "interface": "ui",
                        "expected_outcome": "committed",
                        "title": "有效信息可创建订单",
                        "priority": "P0",
                        "priority_rationale": "创建订单是核心交易主流程，失败会阻断成交。",
                    },
                    {
                        "test_point_id": "TP-002",
                        "requirement_ids": ["REQ-001"],
                        "feature_group": "买家端-订单创建页",
                        "granularity": "field",
                        "field_id": "receiver_name",
                        "coverage_class": "required_empty",
                        "polarity": "negative",
                        "interface": "ui",
                        "expected_outcome": "rejected",
                        "title": "收件人为空被拦截",
                        "priority": "P1",
                        "priority_rationale": "必填拦截是主要数据校验，失效会产生不完整订单。",
                    },
                    {
                        "test_point_id": "TP-003",
                        "requirement_ids": ["REQ-001"],
                        "feature_group": "买家端-订单创建页",
                        "granularity": "field",
                        "field_id": "receiver_name",
                        "coverage_class": "valid",
                        "polarity": "positive",
                        "interface": "ui",
                        "expected_outcome": "accepted",
                        "title": "收件人合法值可输入",
                        "priority": "P2",
                        "priority_rationale": "合法字段输入是低风险基础校验，失败影响局部录入且可恢复。",
                    },
                ],
                "field_coverage": [
                    {"field_id": "receiver_name", "required_classes": ["required_empty", "valid"]}
                ],
            },
        )
        self.case_document = {
            "case_source": {
                "artifact_role": "final",
                "mode": "full",
                "priority_policy": "strict_p0_p1_p2",
            },
            "case_ordering": {
                "policy": "prd_port_page_group",
                "source": "01_需求原文.docx",
                "group_sequence": ["买家端-订单创建页"],
            },
            "executor_profiles": {
                "order_flow": {"capabilities": ["scenario"], "channels": ["ui"]},
                "order_field": {"capabilities": ["field_validation"], "channels": ["ui"]},
            },
            "review_audit": {
                "environment_sample_case_ids": ["TC-001"],
                "environment_probes": [
                    {
                        "case_id": "TC-001",
                        "status": "executable",
                        "channels_tried": ["ui", "api_setup"],
                        "evidence": "runtime/TC-001_review_probe.json",
                        "revision_action": "",
                    }
                ],
                "score_distribution": {
                    "scored_cases": 3,
                    "perfect_scores": 0,
                    "perfect_score_justification": "",
                },
            },
            "test_cases": [
                {
                    "case_id": "TC-001",
                    "test_point_id": "TP-001",
                    "case_title": "有效信息创建订单",
                    "requirement_ids": ["REQ-001"],
                    "feature_group": "买家端-订单创建页",
                    "granularity": "scenario",
                    "test_intent": {
                        "kind": "scenario",
                        "polarity": "positive",
                        "interface": "ui",
                        "field_id": "",
                        "input_class": "",
                        "expected_outcome": "committed",
                    },
                    "execution_profile": "order_flow",
                    "execution_contract": {
                        "capability": "scenario",
                        "primary_channel": "ui",
                        "setup_separated": True,
                    },
                    "priority": "P0",
                    "priority_rationale": "创建订单是核心交易主流程，失败会阻断成交。",
                    "preconditions": ["已登录"],
                    "steps": [{"step_no": 1, "action": "click", "target": "提交"}],
                    "formal_steps": [{"step_no": 1, "action": "提交订单", "target": "订单表单"}],
                    "assertions": [{"assert_id": "A-001", "type": "text", "expected": "创建成功"}],
                    "result_contract": {
                        "verdict": "all",
                        "assertion_policy": {
                            "hard_keys": ["success_text", "order_no", "mutation_committed"],
                            "soft_keys": [],
                        },
                        "required_produced_keys": ["success_text", "order_no", "mutation_committed"],
                        "assertion_observation_keys": ["success_text", "order_no", "mutation_committed"],
                        "screenshot_required": True,
                        "observations": [
                            {
                                "key": "success_text",
                                "assertion_class": "hard",
                                "prd_exact": True,
                                "source": "ui",
                                "operator": "contains",
                                "expected": "创建成功",
                                "evidence_required": True,
                            },
                            {
                                "key": "order_no",
                                "assertion_class": "hard",
                                "source": "ui",
                                "operator": "non_empty",
                                "evidence_required": True,
                            },
                            {
                                "key": "mutation_committed",
                                "assertion_class": "hard",
                                "source": "api",
                                "operator": "equals",
                                "expected": True,
                                "evidence_required": True,
                            },
                        ],
                    },
                },
                {
                    "case_id": "TC-002",
                    "test_point_id": "TP-002",
                    "case_title": "收件人为空时禁止提交",
                    "requirement_ids": ["REQ-001"],
                    "feature_group": "买家端-订单创建页",
                    "granularity": "field",
                    "test_intent": {
                        "kind": "field_validation",
                        "polarity": "negative",
                        "interface": "ui",
                        "field_id": "receiver_name",
                        "input_class": "required_empty",
                        "expected_outcome": "rejected",
                    },
                    "execution_profile": "order_field",
                    "execution_contract": {
                        "capability": "field_validation",
                        "primary_channel": "ui",
                        "setup_separated": True,
                    },
                    "priority": "P1",
                    "priority_rationale": "必填拦截是主要数据校验，失效会产生不完整订单。",
                    "preconditions": ["已登录"],
                    "steps": [{"step_no": 1, "action": "click", "target": "提交"}],
                    "formal_steps": [{"step_no": 1, "action": "保持收件人为空并提交", "target": "订单表单"}],
                    "assertions": [{"assert_id": "A-002", "type": "text", "expected": "请输入收件人"}],
                    "result_contract": {
                        "verdict": "all",
                        "assertion_policy": {
                            "hard_keys": ["prompt_text", "mutation_committed"],
                            "soft_keys": [],
                        },
                        "required_produced_keys": ["prompt_text", "mutation_committed"],
                        "assertion_observation_keys": ["prompt_text", "mutation_committed"],
                        "screenshot_required": True,
                        "observations": [
                            {
                                "key": "prompt_text",
                                "assertion_class": "hard",
                                "prd_exact": True,
                                "source": "ui",
                                "operator": "contains",
                                "expected": "请输入收件人",
                                "evidence_required": True,
                            },
                            {
                                "key": "mutation_committed",
                                "assertion_class": "hard",
                                "source": "api",
                                "operator": "equals",
                                "expected": False,
                                "evidence_required": True,
                            },
                        ],
                    },
                },
                {
                    "case_id": "TC-003",
                    "test_point_id": "TP-003",
                    "case_title": "收件人合法值可输入",
                    "requirement_ids": ["REQ-001"],
                    "feature_group": "买家端-订单创建页",
                    "granularity": "field",
                    "test_intent": {
                        "kind": "field_validation",
                        "polarity": "positive",
                        "interface": "ui",
                        "field_id": "receiver_name",
                        "input_class": "valid",
                        "expected_outcome": "accepted",
                    },
                    "execution_profile": "order_field",
                    "execution_contract": {
                        "capability": "field_validation",
                        "primary_channel": "ui",
                        "setup_separated": True,
                    },
                    "priority": "P2",
                    "priority_rationale": "合法字段输入是低风险基础校验，失败影响局部录入且可恢复。",
                    "preconditions": ["已登录"],
                    "steps": [{"step_no": 1, "action": "input", "target": "收件人", "input": "张三"}],
                    "formal_steps": [{"step_no": 1, "action": "在收件人字段输入张三", "target": "订单表单"}],
                    "assertions": [{"assert_id": "A-003", "type": "value", "expected": "收件人字段保留张三"}],
                    "result_contract": {
                        "verdict": "all",
                        "assertion_policy": {
                            "hard_keys": ["receiver_value"],
                            "soft_keys": [],
                        },
                        "required_produced_keys": ["receiver_value"],
                        "assertion_observation_keys": ["receiver_value"],
                        "screenshot_required": True,
                        "observations": [
                            {
                                "key": "receiver_value",
                                "assertion_class": "hard",
                                "source": "ui",
                                "operator": "equals",
                                "expected": "张三",
                                "evidence_required": True,
                            },
                        ],
                    },
                },
            ],
        }
        write_json(self.cases, self.case_document)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_case_gate(self) -> subprocess.CompletedProcess[str]:
        return run(
            VALIDATE_CASES,
            "--requirements",
            self.requirements,
            "--test-points",
            self.points,
            "--final-cases",
            self.cases,
            "--output",
            self.case_gate,
        )

    def expand_case_set_with_priorities(self, priorities: list[str]) -> None:
        point_document = json.loads(self.points.read_text(encoding="utf-8"))
        point_templates = point_document["test_points"]
        case_templates = self.case_document["test_cases"]
        rationale_by_priority = {
            "P0": "核心交易流程或高影响数据风险，失败会阻断成交。",
            "P1": "主要业务校验或常见异常，失败影响主要功能但可恢复。",
            "P2": "低风险字段或次要边界验证，失败影响局部且可恢复。",
        }
        expanded_points = []
        expanded_cases = []
        for index, priority in enumerate(priorities, 1):
            point = deepcopy(point_templates[(index - 1) % len(point_templates)])
            case = deepcopy(case_templates[(index - 1) % len(case_templates)])
            point_id = f"TP-{index:03d}"
            case_id = f"TC-{index:03d}"
            rationale = rationale_by_priority[priority]
            point["test_point_id"] = point_id
            point["priority"] = priority
            point["priority_rationale"] = rationale
            case["case_id"] = case_id
            case["test_point_id"] = point_id
            case["priority"] = priority
            case["priority_rationale"] = rationale
            case["assertions"][0]["assert_id"] = f"A-{index:03d}"
            expanded_points.append(point)
            expanded_cases.append(case)
        point_document["test_points"] = expanded_points
        self.case_document["test_cases"] = expanded_cases
        sample_count = max(1, (len(expanded_cases) + 9) // 10)
        p0_case_ids = [case["case_id"] for case in expanded_cases if case["priority"] == "P0"]
        sample_ids = [p0_case_ids[0]] if p0_case_ids else []
        sample_ids.extend(
            case["case_id"]
            for case in expanded_cases
            if case["case_id"] not in sample_ids
        )
        sample_ids = sample_ids[:sample_count]
        self.case_document["review_audit"] = {
            "environment_sample_case_ids": sample_ids,
            "environment_probes": [
                {
                    "case_id": case_id,
                    "status": "executable",
                    "channels_tried": ["ui", "api_setup"],
                    "evidence": f"runtime/{case_id}_review_probe.json",
                    "revision_action": "",
                }
                for case_id in sample_ids
            ],
            "score_distribution": {
                "scored_cases": len(expanded_cases),
                "perfect_scores": 0,
                "perfect_score_justification": "",
            },
        }
        write_json(self.points, point_document)
        write_json(self.cases, self.case_document)

    def create_execution_plan(self) -> None:
        final_hash = hashlib.sha256(self.cases.read_bytes()).hexdigest()
        plans = []
        for case in priority_sorted_cases(self.case_document["test_cases"]):
            intent = normalized_test_intent(case)
            observations = case["result_contract"]["observations"]
            if intent["kind"] == "field_validation" and intent["expected_outcome"] == "rejected":
                semantic_actions = ["input_test_value", "attempt_commit", "observe_outcome"]
            elif intent["kind"] == "field_validation":
                semantic_actions = ["input_test_value", "observe_outcome"]
            else:
                semantic_actions = ["perform_operation", "observe_outcome"]
            plan_item = {
                    "case_id": case["case_id"],
                    "semantic_contract_sha256": semantic_contract_hash(case),
                    "execution_profile": case["execution_profile"],
                    "executor_capability": case["execution_contract"]["capability"],
                    "primary_channel": case["execution_contract"]["primary_channel"],
                    "intent_snapshot": intent,
                    "runtime_env": {
                        "base_url": "https://example.test",
                        "browser": "chromium",
                        "headless": False,
                        "config_source": "fixture/framework_binding.yaml",
                    },
                    "setup_steps": [],
                    "core_steps": [
                        {
                            "step_no": index,
                            "phase": "core",
                            "channel": case["execution_contract"]["primary_channel"],
                            "semantic_action": action,
                        }
                        for index, action in enumerate(semantic_actions, 1)
                    ],
                    "planned_observation_keys": [
                        observation["key"]
                        for observation in observations
                    ],
                    "execution_budget": {
                        "minimum_attempts": 2,
                        "single_run_exemption": {"applied": False},
                    },
                    "estimated_minutes": 10,
                    "blocker_probe_plan": {
                        "channels": ["ui", "api_setup"],
                        "probe_log_path": f"runtime/{case['case_id']}_capability_probe.json",
                    },
                    "state_wait_policy": {
                        "refresh_before_poll": True,
                        "timeout_seconds": 30,
                        "initial_interval_seconds": 0.5,
                        "max_interval_seconds": 4,
                    },
                    "evidence_plan": {
                        "semantic_keys": [
                            observation["key"]
                            for observation in observations
                            if observation["source"] == "ui" and observation["evidence_required"] is True
                        ],
                        "api_semantic_keys": [
                            observation["key"]
                            for observation in observations
                            if observation["source"] == "api" and observation["evidence_required"] is True
                        ],
                        "assertion_moment": "核心操作完成后的断言时刻",
                        "target": "业务结果区域",
                        "browser": "chromium",
                        "verification_method": "text_anchor",
                        "content_anchors": ["关键结果"],
                        "screenshot_scope": (
                            "assertion_target_container"
                            if intent["kind"] == "field_validation"
                            else "assertion_state_view"
                        ),
                        "attempt_binding": "selected_attempt_only",
                    },
                }
            if intent["kind"] == "field_validation" and intent["interface"] == "ui":
                plan_item["locator_audit"] = {
                    "label": "收件人",
                    "field_name": "receiver_name",
                    "input_type": "text",
                    "editable": "true",
                    "nearest_container": "收件人表单项",
                }
            plans.append(plan_item)
        write_json(
            self.execution_plan,
            {
                "execution_meta": {
                    "final_cases_path": self.cases.name,
                    "final_cases_sha256": final_hash,
                    "execution_mode": "qualification",
                    "browser_runtime": {
                        "browser": "chromium",
                        "headless": False,
                        "mode": "headed",
                        "config_source": "fixture/framework_binding.yaml",
                    },
                    "priority_execution_policy": deepcopy(PRIORITY_EXECUTION_POLICY),
                    "input_sha256": {
                        "requirements": "a" * 64,
                        "environment": "b" * 64,
                    },
                    "resource_budget": {
                        "estimated_attempts": len(plans) * 2,
                        "estimated_minutes": len(plans) * 10,
                    },
                },
                "browser_execution_plan": plans,
            },
        )

    def create_plan_gate(self) -> subprocess.CompletedProcess[str]:
        self.create_execution_plan()
        return run(
            VALIDATE_PLAN,
            "--case-gate",
            self.case_gate,
            "--final-cases",
            self.cases,
            "--execution-plan",
            self.execution_plan,
            "--output",
            self.plan_gate,
        )

    def execution_trace(self, case_id: str) -> dict[str, object]:
        plan_document = json.loads(self.execution_plan.read_text(encoding="utf-8"))
        plan = next(item for item in plan_document["browser_execution_plan"] if item["case_id"] == case_id)
        return {
            "execution_profile": plan["execution_profile"],
            "executor_capability": plan["executor_capability"],
            "primary_channel": plan["primary_channel"],
            "intent_snapshot": plan["intent_snapshot"],
        }

    def create_execution_results(self) -> None:
        final_hash = hashlib.sha256(self.cases.read_bytes()).hexdigest()
        plan_hash = hashlib.sha256(self.execution_plan.read_bytes()).hexdigest()
        first_case, second_case, third_case = self.case_document["test_cases"]
        first_semantic_hash = semantic_contract_hash(first_case)
        second_semantic_hash = semantic_contract_hash(second_case)
        third_semantic_hash = semantic_contract_hash(third_case)
        first_trace = self.execution_trace("TC-001")
        second_trace = self.execution_trace("TC-002")
        third_trace = self.execution_trace("TC-003")
        screenshot_sha256 = hashlib.sha256(ONE_PIXEL_PNG).hexdigest()

        def evidence_manifest(case_id: str, semantic_keys: list[str], content_text: str) -> dict[str, object]:
            return {
                "screenshots": [f"{case_id}.png"],
                "manifest": [
                    {
                        "file": f"{case_id}.png",
                        "sha256": screenshot_sha256,
                        "case_id": case_id,
                        "assertion_moment": "核心操作完成后的断言时刻",
                        "target": "业务结果区域",
                        "browser": "chromium",
                        "semantic_keys": semantic_keys,
                        "content_text": content_text,
                        "content_anchors": ["关键结果"],
                        "content_verified": True,
                        "verification_method": "text_anchor",
                    }
                ],
            }

        def passed_order_attempt(attempt: int, order_no: str) -> dict[str, object]:
            observations = {
                "success_text": {
                    "value": f"订单 {order_no} 创建成功",
                    "source": "ui",
                    "evidence_refs": ["TC-001.png"],
                },
                "order_no": {
                    "value": order_no,
                    "source": "ui",
                    "evidence_refs": ["TC-001.png"],
                },
                "mutation_committed": {
                    "value": True,
                    "source": "api",
                    "evidence_refs": [f"api:orders/{order_no}"],
                },
            }
            return {
                "attempt": attempt,
                "status": "passed",
                "detail": "订单创建成功",
                "semantic_contract_sha256": first_semantic_hash,
                "execution_trace": first_trace,
                "observations": observations,
                "produced_data": {
                    "success_text": f"订单 {order_no} 创建成功",
                    "order_no": order_no,
                    "mutation_committed": True,
                },
            }

        def failed_rejection_attempt(attempt: int) -> dict[str, object]:
            observations = {
                "prompt_text": {
                    "value": "订单创建成功",
                    "source": "ui",
                    "evidence_refs": ["TC-002.png"],
                },
                "mutation_committed": {
                    "value": True,
                    "source": "api",
                    "evidence_refs": [f"api:orders/unexpected-{attempt}"],
                },
            }
            return {
                "attempt": attempt,
                "status": "failed",
                "detail": "收件人为空时订单仍创建成功",
                "semantic_contract_sha256": second_semantic_hash,
                "execution_trace": second_trace,
                "observations": observations,
                "produced_data": {
                    "prompt_text": "订单创建成功",
                    "mutation_committed": True,
                },
            }

        def passed_field_attempt(attempt: int) -> dict[str, object]:
            observations = {
                "receiver_value": {
                    "value": "张三",
                    "source": "ui",
                    "evidence_refs": ["TC-003.png"],
                },
            }
            return {
                "attempt": attempt,
                "status": "passed",
                "detail": "收件人字段保留合法值张三",
                "semantic_contract_sha256": third_semantic_hash,
                "execution_trace": third_trace,
                "observations": observations,
                "produced_data": {"receiver_value": "张三"},
            }

        selected_attempt = passed_order_attempt(1, "O-001")
        selected_failure = failed_rejection_attempt(3)
        write_json(
            self.execution,
            {
                "execution_meta": {
                    "final_cases_path": self.cases.name,
                    "final_cases_sha256": final_hash,
                    "execution_plan_path": self.execution_plan.name,
                    "execution_plan_sha256": plan_hash,
                    "execution_mode": "qualification",
                    "priority_execution_policy": deepcopy(PRIORITY_EXECUTION_POLICY),
                    "priority_gate": {
                        "evaluated_after_priority": "P0",
                        "p0_total": 1,
                        "p0_completed": 1,
                        "p0_failed": 0,
                        "p0_blocked": 0,
                        "triggered": False,
                        "reason_code": "",
                        "skipped_priorities": [],
                        "not_run_case_ids": [],
                    },
                    "retry_policy_version": "3.0",
                    "input_sha256": {
                        "requirements": "a" * 64,
                        "environment": "b" * 64,
                    },
                    "scope_matrix": [
                        {
                            "track": "功能UI",
                            "status": "executed",
                            "reason": "本轮核心范围",
                            "coverage": "全部最终用例",
                            "artifact": "18_测试用例_执行回填版.xlsx",
                        }
                    ],
                    "resource_budget": {
                        "estimated_attempts": 6,
                        "estimated_minutes": 30,
                        "actual_attempts": 7,
                        "actual_minutes": 30,
                    },
                },
                "execution_results": [
                    {
                        "case_id": "TC-001",
                        "status": "passed",
                        "attempts": [
                            selected_attempt,
                            passed_order_attempt(2, "O-002"),
                        ],
                        "selected_attempt": 1,
                        "semantic_contract_sha256": first_semantic_hash,
                        "execution_trace": first_trace,
                        "actual_result": "创建订单 O-001，页面显示创建成功",
                        "result_summary": {
                            "expected": "订单创建成功",
                            "actual": "订单 O-001 创建成功",
                            "key_message": "创建成功",
                            "mutation": True,
                            "object_id": "O-001",
                        },
                        "observations": selected_attempt["observations"],
                        "produced_data": selected_attempt["produced_data"],
                        "stability": "stable",
                        "retry_policy": {
                            "minimum_attempts": 2,
                            "required_attempts": 2,
                            "third_attempt_required": False,
                            "single_run_exempt": False,
                            "full_retry_upgraded": False,
                            "priority": "P0",
                            "reason": "首两遍均通过",
                        },
                        "evidence": evidence_manifest("TC-001", ["success_text", "order_no"], "关键结果 订单 O-001 创建成功"),
                    },
                    {
                        "case_id": "TC-002",
                        "status": "failed",
                        "attempts": [
                            failed_rejection_attempt(1),
                            failed_rejection_attempt(2),
                            selected_failure,
                        ],
                        "selected_attempt": 3,
                        "semantic_contract_sha256": second_semantic_hash,
                        "execution_trace": second_trace,
                        "failed_step_no": 1,
                        "actual_result": "收件人为空时提交成功，未显示请输入收件人",
                        "result_summary": {
                            "expected": "空收件人被拦截且不写入",
                            "actual": "订单仍创建成功",
                            "key_message": "必填拦截失效",
                            "mutation": True,
                            "object_id": "unexpected-order",
                        },
                        "observations": selected_failure["observations"],
                        "produced_data": selected_failure["produced_data"],
                        "stability": "unstable",
                        "retry_policy": {
                            "minimum_attempts": 2,
                            "required_attempts": 3,
                            "third_attempt_required": True,
                            "single_run_exempt": False,
                            "full_retry_upgraded": False,
                            "priority": "P1",
                            "reason": "三遍后失败",
                        },
                        "defect_summary": "收件人为空时仍创建订单",
                        "defect": {
                            "severity_suggestion": "S1",
                            "confirmation_status": "待人工确认",
                            "owner": "待分配",
                            "defect_status": "待确认",
                            "regression_status": "待回归",
                        },
                        "evidence": evidence_manifest("TC-002", ["prompt_text"], "关键结果 订单创建成功"),
                    },
                    {
                        "case_id": "TC-003",
                        "status": "passed",
                        "attempts": [passed_field_attempt(1), passed_field_attempt(2)],
                        "selected_attempt": 1,
                        "semantic_contract_sha256": third_semantic_hash,
                        "execution_trace": third_trace,
                        "actual_result": "收件人字段输入张三后保留原值",
                        "result_summary": {
                            "expected": "字段保留合法值",
                            "actual": "字段值为张三",
                            "key_message": "字段值一致",
                            "mutation": False,
                            "object_id": "receiver_name",
                        },
                        "observations": passed_field_attempt(1)["observations"],
                        "produced_data": passed_field_attempt(1)["produced_data"],
                        "stability": "stable",
                        "retry_policy": {
                            "minimum_attempts": 2,
                            "required_attempts": 2,
                            "third_attempt_required": False,
                            "single_run_exempt": False,
                            "full_retry_upgraded": False,
                            "priority": "P2",
                            "reason": "首两遍均通过",
                        },
                        "evidence": evidence_manifest("TC-003", ["receiver_value"], "关键结果 收件人字段为张三"),
                    },
                ],
                "summary": {
                    "total": 3,
                    "passed": 2,
                    "failed": 1,
                    "blocked": 0,
                    "not_run": 0,
                    "total_attempts": 7,
                    "third_attempt_cases": 1,
                    "unstable_cases": 1,
                    "single_run_exempt_cases": 0,
                    "full_retry_upgrades": 0,
                    "first_passed": 2,
                    "first_pass_rate": 66.67,
                    "final_pass_rate_including_blocked": 66.67,
                    "final_pass_rate_judged_only": 66.67,
                },
            },
        )
        for case_id in ("TC-001", "TC-002", "TC-003"):
            (self.screenshots / f"{case_id}.png").write_bytes(ONE_PIXEL_PNG)

    def upgrade_execution_evidence_to_v2(self, document: dict[str, object]) -> None:
        document["execution_meta"]["evidence_manifest_version"] = "2.0"
        document["execution_meta"]["browser_runtime"] = {
            "browser": "chromium",
            "headless": False,
            "mode": "headed",
            "config_source": "fixture/framework_binding.yaml",
        }
        cases_by_id = {case["case_id"]: case for case in self.case_document["test_cases"]}
        screenshot_sha256 = hashlib.sha256(ONE_PIXEL_PNG).hexdigest()
        for result in document["execution_results"]:
            case_id = result["case_id"]
            case = cases_by_id[case_id]
            scope = (
                "assertion_target_container"
                if case["test_intent"]["kind"] == "field_validation"
                else "assertion_state_view"
            )
            canonical_manifest = result["evidence"]["manifest"][0]
            selected_attempt = int(result["selected_attempt"])
            source_name = f"{case_id}_A{selected_attempt}_assertion.png"
            canonical_manifest.update(
                {
                    "headless": False,
                    "evidence_scope": scope,
                    "selected_attempt": selected_attempt,
                    "source_attempt_file": f"artifacts/screenshots/{source_name}",
                    "observed_target_text": canonical_manifest["content_text"],
                }
            )
            for attempt in result["attempts"]:
                attempt_no = int(attempt["attempt"])
                attempt_name = f"{case_id}_A{attempt_no}_assertion.png"
                (self.screenshots / attempt_name).write_bytes(ONE_PIXEL_PNG)
                attempt["evidence"] = {
                    "screenshots": [f"artifacts/screenshots/{attempt_name}"],
                    "manifest": [
                        {
                            "file": f"artifacts/screenshots/{attempt_name}",
                            "sha256": screenshot_sha256,
                            "case_id": case_id,
                            "attempt": attempt_no,
                            "assertion_moment": "本次尝试完成最终断言后",
                            "target": canonical_manifest["target"],
                            "browser": "chromium",
                            "headless": False,
                            "evidence_scope": scope,
                            "semantic_keys": deepcopy(canonical_manifest["semantic_keys"]),
                            "content_text": canonical_manifest["content_text"],
                            "observed_target_text": canonical_manifest["content_text"],
                            "content_anchors": deepcopy(canonical_manifest["content_anchors"]),
                            "content_verified": True,
                            "verification_method": "text_anchor",
                        }
                    ],
                }

    def configure_persistence_case(self, operation_type: str) -> dict[str, object]:
        case = self.case_document["test_cases"][2]
        point_document = json.loads(self.points.read_text(encoding="utf-8"))
        point_document["test_points"][2]["persistence_verification_required"] = True
        write_json(self.points, point_document)
        case["operation_type"] = operation_type
        case["persistence_verification"] = {
            "required": True,
            "commit_required": True,
            "reopen_or_query_required": True,
            "compare_input_persisted": True,
            "baseline_required": operation_type == "edit",
        }
        case["formal_steps"] = [
            {"step_no": 1, "action": "记录修改前值并在收件人字段输入张三", "target": "订单表单"},
            {"step_no": 2, "action": "保存订单", "target": "订单表单"},
            {"step_no": 3, "action": "重新打开同一订单并读取回显值", "target": "订单详情"},
            {"step_no": 4, "action": "比较输入值与保存后回显值", "target": "收件人字段"},
        ]
        keys = ["object_id", "input_value", "persisted_value", "persistence_verified", "mutation_committed"]
        if operation_type == "edit":
            keys.insert(1, "before_value")
        observations = []
        for key in keys:
            source = "api" if key in {"object_id", "mutation_committed"} else "ui"
            observation = {
                "key": key,
                "assertion_class": "hard",
                "source": source,
                "operator": "non_empty",
                "evidence_required": True,
            }
            if key in {"persistence_verified", "mutation_committed"}:
                observation["operator"] = "equals"
                observation["expected"] = True
            observations.append(observation)
        case["result_contract"] = {
            "verdict": "all",
            "assertion_policy": {"hard_keys": keys, "soft_keys": []},
            "required_produced_keys": keys,
            "assertion_observation_keys": keys,
            "screenshot_required": True,
            "observations": observations,
        }
        write_json(self.cases, self.case_document)
        return case

    def validate_execution_results(self) -> subprocess.CompletedProcess[str]:
        return run(
            VALIDATE_EXECUTION,
            "--phase",
            "execution",
            "--case-gate",
            self.case_gate,
            "--final-cases",
            self.cases,
            "--execution-plan-gate",
            self.plan_gate,
            "--execution-plan",
            self.execution_plan,
            "--execution-results",
            self.execution,
            "--screenshots-dir",
            self.screenshots,
            "--output",
            self.execution_gate,
        )

    def refresh_execution_summary(self, document: dict[str, object]) -> None:
        results = document["execution_results"]
        counts = {status: 0 for status in ("passed", "failed", "blocked", "not_run")}
        for result in results:
            counts[str(result["status"])] += 1
        metrics = execution_metrics(results)
        document["summary"] = {"total": len(results), **counts, **metrics}
        document["execution_meta"]["resource_budget"]["actual_attempts"] = metrics["total_attempts"]
        document["execution_meta"]["priority_gate"] = evaluate_p0_stop_gate(
            self.case_document["test_cases"], results
        )

    def apply_p0_all_failed_stop(self, document: dict[str, object]) -> None:
        p0_result = document["execution_results"][0]
        failed_attempts = []
        for attempt_number in (1, 2, 3):
            attempt = deepcopy(p0_result["attempts"][0])
            attempt["attempt"] = attempt_number
            attempt["status"] = "failed"
            attempt["observations"]["success_text"]["value"] = "订单创建失败"
            attempt["observations"]["mutation_committed"]["value"] = False
            attempt["produced_data"]["success_text"] = "订单创建失败"
            attempt["produced_data"]["mutation_committed"] = False
            failed_attempts.append(attempt)
        selected = failed_attempts[2]
        p0_result.update(
            {
                "status": "failed",
                "attempts": failed_attempts,
                "selected_attempt": 3,
                "failed_step_no": 1,
                "actual_result": "订单提交后三遍均未创建成功，核心交易主流程失败",
                "result_summary": {
                    "expected": "订单创建成功",
                    "actual": "订单三遍均创建失败",
                    "key_message": "核心交易主流程失败",
                    "mutation": False,
                    "object_id": "O-001",
                },
                "observations": selected["observations"],
                "produced_data": selected["produced_data"],
                "stability": "unstable",
                "retry_policy": {
                    "minimum_attempts": 2,
                    "required_attempts": 3,
                    "third_attempt_required": True,
                    "single_run_exempt": False,
                    "full_retry_upgraded": False,
                    "priority": "P0",
                    "reason": "P0 三遍中至少两遍失败",
                },
                "defect_summary": "核心订单创建流程连续失败",
                "defect": {
                    "severity_suggestion": "S1",
                    "confirmation_status": "待人工确认",
                    "owner": "待分配",
                    "defect_status": "待确认",
                    "regression_status": "待回归",
                },
            }
        )
        for result in document["execution_results"][1:]:
            case = next(case for case in self.case_document["test_cases"] if case["case_id"] == result["case_id"])
            result.clear()
            result.update(
                {
                    "case_id": case["case_id"],
                    "status": "not_run",
                    "semantic_contract_sha256": semantic_contract_hash(case),
                    "actual_result": P0_STOP_ACTUAL,
                    "result_summary": {
                        "expected": "按 P0 -> P1 -> P2 顺序执行",
                        "actual": P0_STOP_ACTUAL,
                        "key_message": P0_STOP_REASON,
                    },
                    "not_run_reason": P0_STOP_REASON,
                    "attempts": [],
                    "evidence": {},
                    "stability": "not_run",
                    "retry_policy": {
                        "minimum_attempts": 2,
                        "required_attempts": 0,
                        "third_attempt_required": False,
                        "single_run_exempt": False,
                        "full_retry_upgraded": False,
                        "priority": case["priority"],
                        "reason": P0_STOP_ACTUAL,
                    },
                }
            )
        self.refresh_execution_summary(document)

    def test_execution_plan_enforces_stable_priority_order(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.create_execution_plan()
        document = json.loads(self.execution_plan.read_text(encoding="utf-8"))
        self.assertEqual(
            [item["case_id"] for item in document["browser_execution_plan"]],
            ["TC-001", "TC-002", "TC-003"],
        )
        valid = run(
            VALIDATE_PLAN,
            "--case-gate", self.case_gate,
            "--final-cases", self.cases,
            "--execution-plan", self.execution_plan,
            "--output", self.plan_gate,
        )
        self.assertEqual(valid.returncode, 0, valid.stderr)
        document["browser_execution_plan"][0], document["browser_execution_plan"][1] = (
            document["browser_execution_plan"][1],
            document["browser_execution_plan"][0],
        )
        write_json(self.execution_plan, document)
        invalid = run(
            VALIDATE_PLAN,
            "--case-gate", self.case_gate,
            "--final-cases", self.cases,
            "--execution-plan", self.execution_plan,
            "--output", self.plan_gate,
        )
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("stable P0 -> P1 -> P2", invalid.stderr)

    def test_execution_gate_accepts_p0_all_failed_stop_with_lower_not_run(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        self.apply_p0_all_failed_stop(document)
        write_json(self.execution, document)
        validation = self.validate_execution_results()
        self.assertEqual(validation.returncode, 0, validation.stderr)
        self.assertTrue(document["execution_meta"]["priority_gate"]["triggered"])
        self.assertEqual(
            [item["status"] for item in document["execution_results"]],
            ["failed", "not_run", "not_run"],
        )

    def test_p0_stop_gate_requires_the_complete_batch_to_all_fail(self) -> None:
        cases = [
            {"case_id": "TC-P0-A", "priority": "P0"},
            {"case_id": "TC-P0-B", "priority": "P0"},
            {"case_id": "TC-P1-A", "priority": "P1"},
        ]
        incomplete = evaluate_p0_stop_gate(
            cases,
            [{"case_id": "TC-P0-A", "status": "failed"}],
        )
        self.assertFalse(incomplete["triggered"])
        self.assertEqual(incomplete["p0_completed"], 1)
        failed_and_blocked = evaluate_p0_stop_gate(
            cases,
            [
                {"case_id": "TC-P0-A", "status": "failed"},
                {"case_id": "TC-P0-B", "status": "blocked"},
            ],
        )
        self.assertFalse(failed_and_blocked["triggered"])
        self.assertEqual(failed_and_blocked["p0_completed"], 2)
        self.assertEqual(failed_and_blocked["not_run_case_ids"], [])

        failed_and_passed = evaluate_p0_stop_gate(
            cases,
            [
                {"case_id": "TC-P0-A", "status": "failed"},
                {"case_id": "TC-P0-B", "status": "passed"},
            ],
        )
        self.assertFalse(failed_and_passed["triggered"])
        self.assertEqual(failed_and_passed["not_run_case_ids"], [])

        all_blocked = evaluate_p0_stop_gate(
            cases,
            [
                {"case_id": "TC-P0-A", "status": "blocked"},
                {"case_id": "TC-P0-B", "status": "blocked"},
            ],
        )
        self.assertFalse(all_blocked["triggered"])
        self.assertEqual(all_blocked["not_run_case_ids"], [])

        all_failed = evaluate_p0_stop_gate(
            cases,
            [
                {"case_id": "TC-P0-A", "status": "failed"},
                {"case_id": "TC-P0-B", "status": "failed"},
            ],
        )
        self.assertTrue(all_failed["triggered"])
        self.assertEqual(all_failed["p0_completed"], 2)
        self.assertEqual(all_failed["p0_failed"], 2)
        self.assertEqual(all_failed["reason_code"], P0_STOP_REASON)
        self.assertEqual(all_failed["not_run_case_ids"], ["TC-P1-A"])

    def test_priority_sort_preserves_canonical_order_within_each_priority(self) -> None:
        cases = [
            {"case_id": "TC-P1-A", "priority": "P1"},
            {"case_id": "TC-P0-A", "priority": "P0"},
            {"case_id": "TC-P2-A", "priority": "P2"},
            {"case_id": "TC-P0-B", "priority": "P0"},
            {"case_id": "TC-P1-B", "priority": "P1"},
        ]
        self.assertEqual(
            [case["case_id"] for case in priority_sorted_cases(cases)],
            ["TC-P0-A", "TC-P0-B", "TC-P1-A", "TC-P1-B", "TC-P2-A"],
        )

    def test_execution_result_generator_applies_p0_all_failed_stop(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        source_document = json.loads(self.execution.read_text(encoding="utf-8"))
        self.apply_p0_all_failed_stop(source_document)
        mapping_path = self.root / "case_mapping.yaml"
        script_status_path = self.root / "script_status.yaml"
        write_json(
            mapping_path,
            {
                "case_execution_mapping": [
                    {"case_id": "TC-001", "test_node": "node-1", "script_id": "S-001"},
                    {"case_id": "TC-002", "test_node": "node-2", "script_id": "S-002"},
                    {"case_id": "TC-003", "test_node": "node-3", "script_id": "S-003"},
                ]
            },
        )
        write_json(
            script_status_path,
            {
                "script_execution_status": [
                    {"test_node": "node-1", **source_document["execution_results"][0]},
                ],
                "scope_matrix": source_document["execution_meta"]["scope_matrix"],
                "resource_budget": {"actual_minutes": 20},
            },
        )
        generated = run(
            GENERATE_EXECUTION,
            "--mapping", mapping_path,
            "--script-status", script_status_path,
            "--final-cases", self.cases,
            "--case-gate", self.case_gate,
            "--execution-plan", self.execution_plan,
            "--execution-plan-gate", self.plan_gate,
            "--output", self.execution,
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)
        document = yaml.safe_load(self.execution.read_text(encoding="utf-8"))
        self.assertEqual(
            [item["status"] for item in document["execution_results"]],
            ["failed", "not_run", "not_run"],
        )
        for result in document["execution_results"][1:]:
            self.assertEqual(result["not_run_reason"], P0_STOP_REASON)
            self.assertEqual(result["stability"], "not_run")
            self.assertEqual(result["attempts"], [])
        self.assertTrue(document["execution_meta"]["priority_gate"]["triggered"])
        validation = self.validate_execution_results()
        self.assertEqual(validation.returncode, 0, validation.stderr)

    def test_execution_result_generator_rejects_lower_execution_after_all_p0_failed(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        source_document = json.loads(self.execution.read_text(encoding="utf-8"))
        executed_p1 = deepcopy(source_document["execution_results"][1])
        self.apply_p0_all_failed_stop(source_document)
        mapping_path = self.root / "case_mapping.yaml"
        script_status_path = self.root / "script_status.yaml"
        write_json(
            mapping_path,
            {
                "case_execution_mapping": [
                    {"case_id": "TC-001", "test_node": "node-1", "script_id": "S-001"},
                    {"case_id": "TC-002", "test_node": "node-2", "script_id": "S-002"},
                    {"case_id": "TC-003", "test_node": "node-3", "script_id": "S-003"},
                ]
            },
        )
        write_json(
            script_status_path,
            {
                "script_execution_status": [
                    {"test_node": "node-1", **source_document["execution_results"][0]},
                    {"test_node": "node-2", **executed_p1},
                ]
            },
        )
        generated = run(
            GENERATE_EXECUTION,
            "--mapping", mapping_path,
            "--script-status", script_status_path,
            "--final-cases", self.cases,
            "--case-gate", self.case_gate,
            "--execution-plan", self.execution_plan,
            "--execution-plan-gate", self.plan_gate,
            "--output", self.execution,
        )
        self.assertNotEqual(generated.returncode, 0)
        self.assertIn("execution data after all P0 cases finally failed", generated.stderr)

    def test_p0_all_failed_stop_generates_backfill_and_formal_reports(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        generated_template = run(
            GENERATE_XLSX,
            "--source", self.cases,
            "--case-gate", self.case_gate,
            "--output", self.template_xlsx,
        )
        self.assertEqual(generated_template.returncode, 0, generated_template.stderr)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        self.apply_p0_all_failed_stop(document)
        write_json(self.execution, document)
        self.assertEqual(self.validate_execution_results().returncode, 0)
        backfill = run(
            BACKFILL,
            "--source", self.cases,
            "--case-gate", self.case_gate,
            "--execution-yaml", self.execution,
            "--execution-gate", self.execution_gate,
            "--template-xlsx", self.template_xlsx,
            "--output-md", self.backfill_md,
            "--output-xlsx", self.backfill_xlsx,
        )
        self.assertEqual(backfill.returncode, 0, backfill.stderr)
        backfill_text = self.backfill_md.read_text(encoding="utf-8")
        self.assertIn("阻塞/未执行原因", backfill_text)
        self.assertIn(P0_STOP_REASON, backfill_text)
        delivery = run(
            VALIDATE_EXECUTION,
            "--phase", "delivery",
            "--case-gate", self.case_gate,
            "--final-cases", self.cases,
            "--execution-plan-gate", self.plan_gate,
            "--execution-plan", self.execution_plan,
            "--execution-results", self.execution,
            "--screenshots-dir", self.screenshots,
            "--template-xlsx", self.template_xlsx,
            "--backfill-xlsx", self.backfill_xlsx,
            "--viewer-check", "skip",
            "--output", self.delivery_gate,
        )
        self.assertEqual(delivery.returncode, 0, delivery.stderr)
        report = run(
            GENERATE_REPORT,
            "--final-cases", self.cases,
            "--case-gate", self.case_gate,
            "--execution-yaml", self.execution,
            "--execution-gate", self.execution_gate,
            "--delivery-gate", self.delivery_gate,
            "--project-name", "P0停止门禁回归",
            "--backfill-xlsx-path", self.backfill_xlsx,
            "--output", self.report,
            "--output-docx", self.report_docx,
        )
        self.assertEqual(report.returncode, 0, report.stderr)
        report_text = self.report.read_text(encoding="utf-8")
        self.assertIn("P0 优先级停止门禁", report_text)
        self.assertIn("门禁状态：已触发", report_text)
        self.assertIn("全部 P0 最终均失败", report_text)
        self.assertIn("只有全部 P0 的最终状态均为 `failed`", report_text)
        self.assertIn("不建议验收或发布", report_text)
        self.assertIn("不代表产品通过，也不属于环境阻塞", report_text)
        self.assertTrue(self.report_docx.is_file())
        self.assertGreater(self.report_docx.stat().st_size, 0)

    def test_execution_gate_rejects_lower_attempts_after_all_p0_failed(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        executed_p1 = deepcopy(document["execution_results"][1])
        self.apply_p0_all_failed_stop(document)
        document["execution_results"][1] = executed_p1
        self.refresh_execution_summary(document)
        write_json(self.execution, document)
        validation = self.validate_execution_results()
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("must be not_run after the all-P0-failed stop gate", validation.stderr)

    def test_p0_blocked_does_not_trigger_failure_stop(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        p0_result = document["execution_results"][0]
        attempts = []
        for attempt_number in (1, 2, 3):
            attempt = deepcopy(p0_result["attempts"][0])
            attempt["attempt"] = attempt_number
            attempt["status"] = "blocked"
            attempts.append(attempt)
        p0_result.update(
            {
                "status": "blocked",
                "attempts": attempts,
                "selected_attempt": 3,
                "stability": "blocked",
                "blocker_type": "environment",
                "blocker_reason": "测试环境订单入口暂不可达",
                "capability_probe": {
                    "attempted_channels": ["ui", "api_setup", "mcp"],
                    "probe_log": "runtime/TC-001_capability_probe.json",
                },
                "retry_policy": {
                    "minimum_attempts": 2,
                    "required_attempts": 3,
                    "third_attempt_required": True,
                    "single_run_exempt": False,
                    "full_retry_upgraded": False,
                    "priority": "P0",
                    "reason": "P0 未形成两遍一致的可靠结论",
                },
            }
        )
        self.refresh_execution_summary(document)
        write_json(self.execution, document)
        validation = self.validate_execution_results()
        self.assertEqual(validation.returncode, 0, validation.stderr)
        self.assertFalse(document["execution_meta"]["priority_gate"]["triggered"])

    def test_p0_not_run_blocks_lower_priority_execution(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        p0_result = document["execution_results"][0]
        p0_result.update(
            {
                "status": "not_run",
                "attempts": [],
                "selected_attempt": None,
                "actual_result": "P0 执行记录缺失",
                "not_run_reason": "script_status_missing",
                "stability": "not_run",
            }
        )
        p0_result.pop("execution_trace", None)
        self.refresh_execution_summary(document)
        write_json(self.execution, document)
        validation = self.validate_execution_results()
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("must complete final adjudication", validation.stderr)
        self.assertIn("cannot execute before every P0 case completes", validation.stderr)

    def test_p0_mixed_attempts_final_pass_does_not_trigger_stop(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        p0_result = document["execution_results"][0]
        failed_first = deepcopy(p0_result["attempts"][0])
        failed_first["status"] = "failed"
        failed_first["observations"]["success_text"]["value"] = "订单创建失败"
        failed_first["produced_data"]["success_text"] = "订单创建失败"
        second = deepcopy(p0_result["attempts"][1])
        third = deepcopy(p0_result["attempts"][1])
        third["attempt"] = 3
        p0_result.update(
            {
                "attempts": [failed_first, second, third],
                "selected_attempt": 3,
                "observations": third["observations"],
                "produced_data": third["produced_data"],
                "stability": "unstable",
                "retry_policy": {
                    "minimum_attempts": 2,
                    "required_attempts": 3,
                    "third_attempt_required": True,
                    "single_run_exempt": False,
                    "full_retry_upgraded": False,
                    "priority": "P0",
                    "reason": "P0 已达到三遍中至少两遍通过",
                },
            }
        )
        self.refresh_execution_summary(document)
        write_json(self.execution, document)
        validation = self.validate_execution_results()
        self.assertEqual(validation.returncode, 0, validation.stderr)
        self.assertFalse(document["execution_meta"]["priority_gate"]["triggered"])

    def test_full_delivery_chain_generates_non_empty_workbooks(self) -> None:
        result = self.create_case_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.create_plan_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run(
            GENERATE_XLSX,
            "--source",
            self.cases,
            "--case-gate",
            self.case_gate,
            "--output",
            self.template_xlsx,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(self.template_xlsx.stat().st_size, 0)

        self.create_execution_results()
        result = run(
            VALIDATE_EXECUTION,
            "--phase",
            "execution",
            "--case-gate",
            self.case_gate,
            "--final-cases",
            self.cases,
            "--execution-plan-gate",
            self.plan_gate,
            "--execution-plan",
            self.execution_plan,
            "--execution-results",
            self.execution,
            "--screenshots-dir",
            self.screenshots,
            "--output",
            self.execution_gate,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run(
            COMPAT_BACKFILL,
            "--workflow-root",
            SCRIPT_DIR.parent.parent,
            "--source",
            self.cases,
            "--case-gate",
            self.case_gate,
            "--execution-yaml",
            self.execution,
            "--execution-gate",
            self.execution_gate,
            "--template-xlsx",
            self.template_xlsx,
            "--output-md",
            self.backfill_md,
            "--output-xlsx",
            self.backfill_xlsx,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run(
            VALIDATE_EXECUTION,
            "--phase",
            "delivery",
            "--case-gate",
            self.case_gate,
            "--final-cases",
            self.cases,
            "--execution-plan-gate",
            self.plan_gate,
            "--execution-plan",
            self.execution_plan,
            "--execution-results",
            self.execution,
            "--screenshots-dir",
            self.screenshots,
            "--template-xlsx",
            self.template_xlsx,
            "--backfill-xlsx",
            self.backfill_xlsx,
            "--viewer-check",
            os.environ.get("WORKFLOW_VIEWER_CHECK", "skip"),
            "--output",
            self.delivery_gate,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        delivery = json.loads(self.delivery_gate.read_text(encoding="utf-8"))
        self.assertEqual(delivery["status"], "pass")
        if os.environ.get("WORKFLOW_VIEWER_CHECK") == "required":
            self.assertTrue(any(item["status"] == "passed" for item in delivery["viewer_checks"]))
        result = run(
            GENERATE_REPORT,
            "--final-cases",
            self.cases,
            "--case-gate",
            self.case_gate,
            "--execution-yaml",
            self.execution,
            "--execution-gate",
            self.execution_gate,
            "--delivery-gate",
            self.delivery_gate,
            "--project-name",
            "门禁回归测试",
            "--backfill-xlsx-path",
            self.backfill_xlsx,
            "--output",
            self.report,
            "--output-docx",
            self.report_docx,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report_text = self.report.read_text(encoding="utf-8")
        self.assertIn("执行计划门禁", report_text)
        self.assertIn("最终交付门禁", report_text)
        self.assertIn("测试范围矩阵", report_text)
        self.assertIn("P2 单跑豁免用例", report_text)
        self.assertNotIn("通过项摘要", report_text)
        self.assertNotIn("失败与阻塞用例摘要", report_text)
        with zipfile.ZipFile(self.report_docx) as archive:
            self.assertIn("word/document.xml", archive.namelist())
            self.assertIn("测试范围矩阵", archive.read("word/document.xml").decode("utf-8"))

        markdown_only = run(
            GENERATE_REPORT,
            "--final-cases", self.cases,
            "--case-gate", self.case_gate,
            "--execution-yaml", self.execution,
            "--execution-gate", self.execution_gate,
            "--delivery-gate", self.delivery_gate,
            "--project-name", "门禁回归测试",
            "--backfill-xlsx-path", self.backfill_xlsx,
            "--output", self.root / "markdown_only.md",
        )
        self.assertNotEqual(markdown_only.returncode, 0)
        self.assertIn("--output-docx", markdown_only.stderr)

    def test_legacy_backfill_positional_call_cannot_bypass_gates(self) -> None:
        output = self.root / "legacy_backfill.xlsx"
        result = run(COMPAT_BACKFILL, self.root, self.cases.name, output.name)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--case-gate", result.stderr)
        self.assertFalse(output.exists())

    def test_case_gate_rejects_multiple_assertions(self) -> None:
        self.case_document["test_cases"][0]["assertions"].append(
            {"assert_id": "A-EXTRA", "type": "url", "expected": "/orders/1"}
        )
        write_json(self.cases, self.case_document)
        result = self.create_case_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exactly one assertion", result.stderr)

    def test_case_gate_rejects_missing_feature_group(self) -> None:
        self.case_document["test_cases"][0].pop("feature_group")
        write_json(self.cases, self.case_document)
        result = self.create_case_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must declare feature_group", result.stderr)

    def test_case_gate_rejects_split_feature_group(self) -> None:
        point_document = json.loads(self.points.read_text(encoding="utf-8"))
        point_document["test_points"][1]["feature_group"] = "买家端-订单字段弹窗"
        self.case_document["test_cases"][1]["feature_group"] = "买家端-订单字段弹窗"
        self.case_document["case_ordering"]["group_sequence"] = [
            "买家端-订单创建页",
            "买家端-订单字段弹窗",
        ]
        write_json(self.points, point_document)
        write_json(self.cases, self.case_document)
        result = self.create_case_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Feature groups must be contiguous", result.stderr)

    def test_case_gate_rejects_missing_review_samples_and_unsupported_perfect_scores(self) -> None:
        self.case_document["review_audit"]["environment_sample_case_ids"] = []
        self.case_document["review_audit"]["environment_probes"] = []
        self.case_document["review_audit"]["score_distribution"] = {
            "scored_cases": 3,
            "perfect_scores": 3,
            "perfect_score_justification": "",
        }
        write_json(self.cases, self.case_document)
        result = self.create_case_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sample at least 1 cases for environment executability", result.stderr)
        self.assertIn("sample at least 1 P0 cases", result.stderr)
        self.assertIn("must provide perfect_score_justification", result.stderr)

    def test_case_gate_rejects_hard_exact_ui_text_without_prd_basis(self) -> None:
        observation = self.case_document["test_cases"][0]["result_contract"]["observations"][0]
        observation.pop("prd_exact")
        write_json(self.cases, self.case_document)
        result = self.create_case_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be soft or declare prd_exact: true", result.stderr)

    def test_shipped_case_templates_pass_design_gate(self) -> None:
        result = run(
            VALIDATE_CASES,
            "--requirements",
            TEMPLATE_DIR / "requirements.yaml",
            "--test-points",
            TEMPLATE_DIR / "coverage-matrix.yaml",
            "--final-cases",
            TEMPLATE_DIR / "test-cases.yaml",
            "--output",
            self.case_gate,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_default_case_workbook_has_feature_group_column(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        generated = run(
            GENERATE_XLSX,
            "--source", self.cases,
            "--case-gate", self.case_gate,
            "--output", self.template_xlsx,
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)
        with zipfile.ZipFile(self.template_xlsx) as archive:
            root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        header_row = root.find("x:sheetData/x:row", namespace)
        self.assertIsNotNone(header_row)
        headers = [
            gate_text(cell.findtext("x:is/x:t", default="", namespaces=namespace))
            for cell in header_row.findall("x:c", namespace)
        ]
        self.assertEqual(
            headers,
            ["用例ID", "功能集合", "用例名称", "优先级", "前置条件", "执行步骤", "预期结果"],
        )
        self.assertEqual(root.find("x:dimension", namespace).get("ref"), "A1:G4")

    def test_priority_policy_gate_reports_valid_distribution(self) -> None:
        self.expand_case_set_with_priorities(["P0"] * 5 + ["P1"] * 7 + ["P2"] * 8)
        result = self.create_case_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        gate = json.loads(self.case_gate.read_text(encoding="utf-8"))
        self.assertEqual(gate["priority_policy"]["status"], "pass")
        self.assertEqual(gate["priority_policy"]["counts"], {"P0": 5, "P1": 7, "P2": 8})
        self.assertEqual(gate["priority_policy"]["percentages"], {"P0": 25.0, "P1": 35.0, "P2": 40.0})

    def test_case_gate_rejects_invalid_priority(self) -> None:
        self.case_document["test_cases"][2]["priority"] = "P3"
        write_json(self.cases, self.case_document)
        result = self.create_case_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("priority must be one of: P0, P1, P2", result.stderr)

    def test_case_gate_rejects_missing_priority_rationale(self) -> None:
        self.case_document["test_cases"][0].pop("priority_rationale")
        write_json(self.cases, self.case_document)
        result = self.create_case_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must declare priority_rationale", result.stderr)

    def test_case_gate_rejects_test_point_case_priority_mismatch(self) -> None:
        self.case_document["test_cases"][2]["priority"] = "P1"
        write_json(self.cases, self.case_document)
        result = self.create_case_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match test point TP-003", result.stderr)

    def test_case_gate_rejects_missing_p2(self) -> None:
        point_document = json.loads(self.points.read_text(encoding="utf-8"))
        point_document["test_points"][2]["priority"] = "P1"
        point_document["test_points"][2]["priority_rationale"] = "主要字段校验，失败影响录入。"
        self.case_document["test_cases"][2]["priority"] = "P1"
        self.case_document["test_cases"][2]["priority_rationale"] = "主要字段校验，失败影响录入。"
        write_json(self.points, point_document)
        write_json(self.cases, self.case_document)
        result = self.create_case_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing required priority levels: P2", result.stderr)

    def test_case_gate_rejects_p0_above_required_ratio(self) -> None:
        self.expand_case_set_with_priorities(["P0"] * 6 + ["P1"] * 6 + ["P2"] * 8)
        result = self.create_case_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Priority P0 count 6/20 (30.00%)", result.stderr)
        gate = json.loads(self.case_gate.read_text(encoding="utf-8"))
        self.assertEqual(gate["priority_policy"]["status"], "fail")

    def test_scalar_text_preserves_false_and_zero_evidence(self) -> None:
        self.assertEqual(gate_text(False), "False")
        self.assertEqual(gate_text(0), "0")
        self.assertEqual(gate_text(None), "")

    def test_case_gate_rejects_field_case_routed_to_scenario_executor(self) -> None:
        self.case_document["test_cases"][1]["execution_profile"] = "order_flow"
        write_json(self.cases, self.case_document)
        result = self.create_case_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not support capability field_validation", result.stderr)

    def test_case_gate_rejects_negative_case_without_no_mutation_oracle(self) -> None:
        contract = self.case_document["test_cases"][1]["result_contract"]
        contract["observations"] = [
            item for item in contract["observations"] if item["key"] != "mutation_committed"
        ]
        contract["required_produced_keys"] = ["prompt_text"]
        contract["assertion_observation_keys"] = ["prompt_text"]
        write_json(self.cases, self.case_document)
        result = self.create_case_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rejected outcome must require mutation_committed equals false", result.stderr)

    def test_execution_plan_gate_rejects_field_intent_mismatch(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.create_execution_plan()
        document = json.loads(self.execution_plan.read_text(encoding="utf-8"))
        document["browser_execution_plan"][1]["intent_snapshot"]["expected_outcome"] = "committed"
        write_json(self.execution_plan, document)
        result = run(
            VALIDATE_PLAN,
            "--case-gate",
            self.case_gate,
            "--final-cases",
            self.cases,
            "--execution-plan",
            self.execution_plan,
            "--output",
            self.plan_gate,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("intent_snapshot does not match final case", result.stderr)

    def test_execution_plan_gate_rejects_positive_create_in_negative_core_steps(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.create_execution_plan()
        document = json.loads(self.execution_plan.read_text(encoding="utf-8"))
        document["browser_execution_plan"][1]["core_steps"].append(
            {
                "step_no": 4,
                "phase": "core",
                "channel": "ui",
                "semantic_action": "create_valid_resource",
            }
        )
        write_json(self.execution_plan, document)
        result = run(
            VALIDATE_PLAN,
            "--case-gate",
            self.case_gate,
            "--final-cases",
            self.cases,
            "--execution-plan",
            self.execution_plan,
            "--output",
            self.plan_gate,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("contains forbidden core actions", result.stderr)

    def test_execution_gate_rejects_tc_zero_050_style_creation_false_positive(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        result = document["execution_results"][1]
        semantic_hash = result["semantic_contract_sha256"]
        trace = result["execution_trace"]

        def false_positive_attempt(attempt: int) -> dict[str, object]:
            observations = {
                "prompt_text": {
                    "value": "tagName=U27558; uiVisible=True",
                    "source": "ui",
                    "evidence_refs": ["TC-002.png"],
                },
                "mutation_committed": {
                    "value": True,
                    "source": "api",
                    "evidence_refs": ["api:tags/U27558"],
                },
                "tag_name": {
                    "value": "U27558",
                    "source": "api",
                    "evidence_refs": ["api:tags/U27558"],
                },
            }
            return {
                "attempt": attempt,
                "status": "passed",
                "semantic_contract_sha256": semantic_hash,
                "execution_trace": trace,
                "observations": observations,
                "produced_data": {
                    "prompt_text": "tagName=U27558; uiVisible=True",
                    "mutation_committed": True,
                    "tag_name": "U27558",
                },
            }

        selected = false_positive_attempt(1)
        result.update(
            {
                "status": "passed",
                "attempts": [selected, false_positive_attempt(2)],
                "selected_attempt": 1,
                "failed_step_no": None,
                "actual_result": "标签创建成功，tagName=U27558，页面可见",
                "observations": selected["observations"],
                "produced_data": selected["produced_data"],
            }
        )
        document["summary"] = {"total": 3, "passed": 3, "failed": 0, "blocked": 0, "not_run": 0}
        write_json(self.execution, document)
        validation = self.validate_execution_results()
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("observation prompt_text does not satisfy contains", validation.stderr)
        self.assertIn("observation mutation_committed does not satisfy equals False", validation.stderr)
        self.assertIn("observation keys must exactly match result contract", validation.stderr)

    def test_execution_gate_rejects_route_trace_mismatch(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        result = document["execution_results"][0]
        result["execution_trace"]["execution_profile"] = "order_field"
        for attempt in result["attempts"]:
            attempt["execution_trace"]["execution_profile"] = "order_field"
        write_json(self.execution, document)
        validation = self.validate_execution_results()
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("execution_trace does not match the gated plan", validation.stderr)

    def test_execution_gate_accepts_negative_case_with_rejection_evidence(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        result = document["execution_results"][1]
        semantic_hash = result["semantic_contract_sha256"]
        trace = result["execution_trace"]

        def rejected_attempt(attempt: int) -> dict[str, object]:
            observations = {
                "prompt_text": {
                    "value": "请输入收件人",
                    "source": "ui",
                    "evidence_refs": ["TC-002.png"],
                },
                "mutation_committed": {
                    "value": False,
                    "source": "api",
                    "evidence_refs": ["api:orders/no-new-record"],
                },
            }
            return {
                "attempt": attempt,
                "status": "passed",
                "semantic_contract_sha256": semantic_hash,
                "execution_trace": trace,
                "observations": observations,
                "produced_data": {"prompt_text": "请输入收件人", "mutation_committed": False},
            }

        selected = rejected_attempt(1)
        result.update(
            {
                "status": "passed",
                "attempts": [selected, rejected_attempt(2)],
                "selected_attempt": 1,
                "failed_step_no": None,
                "actual_result": "收件人为空时显示请输入收件人，提交被阻止，未新增订单",
                "result_summary": {
                    "expected": "空收件人被拦截且不写入",
                    "actual": "显示请输入收件人且未新增订单",
                    "key_message": "请输入收件人",
                    "mutation": False,
                    "object_id": "no-new-order",
                },
                "observations": selected["observations"],
                "produced_data": selected["produced_data"],
                "stability": "stable",
                "retry_policy": {
                    "minimum_attempts": 2,
                    "required_attempts": 2,
                    "third_attempt_required": False,
                    "single_run_exempt": False,
                    "full_retry_upgraded": False,
                    "priority": "P1",
                    "reason": "首两遍均通过",
                },
            }
        )
        result["evidence"]["manifest"][0]["content_text"] = "关键结果 请输入收件人"
        self.refresh_execution_summary(document)
        write_json(self.execution, document)
        validation = self.validate_execution_results()
        self.assertEqual(validation.returncode, 0, validation.stderr)

    def test_execution_gate_rejects_failed_attempt_without_structured_observations(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        attempt = document["execution_results"][1]["attempts"][0]
        attempt.pop("observations")
        attempt.pop("produced_data")
        write_json(self.execution, document)
        validation = self.validate_execution_results()
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("attempt 1 observations must be a mapping", validation.stderr)

    def test_execution_gate_rejects_failed_status_when_contract_is_satisfied(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        result = document["execution_results"][1]
        observations = {
            "prompt_text": {
                "value": "请输入收件人",
                "source": "ui",
                "evidence_refs": ["TC-002.png"],
            },
            "mutation_committed": {
                "value": False,
                "source": "api",
                "evidence_refs": ["api:orders/no-new-record"],
            },
        }
        produced_data = {"prompt_text": "请输入收件人", "mutation_committed": False}
        result["attempts"][2]["observations"] = observations
        result["attempts"][2]["produced_data"] = produced_data
        result["observations"] = observations
        result["produced_data"] = produced_data
        write_json(self.execution, document)
        validation = self.validate_execution_results()
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("is failed but its observations satisfy the result contract", validation.stderr)

    def test_execution_gate_accepts_not_run_without_fake_trace(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        result = document["execution_results"][1]
        result.update(
            {
                "status": "not_run",
                "attempts": [],
                "selected_attempt": None,
                "actual_result": "测试环境未提供可使用的订单提交入口，本轮未执行",
                "not_run_reason": "environment_unavailable",
                "stability": "not_run",
            }
        )
        result.pop("execution_trace", None)
        result.pop("failed_step_no", None)
        result.pop("observations", None)
        result.pop("produced_data", None)
        self.refresh_execution_summary(document)
        write_json(self.execution, document)
        validation = self.validate_execution_results()
        self.assertEqual(validation.returncode, 0, validation.stderr)

    def test_execution_gate_rejects_incomplete_result_set(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        document["execution_results"] = document["execution_results"][:1]
        document["summary"] = {"total": 1, "passed": 1, "failed": 0, "blocked": 0, "not_run": 0}
        write_json(self.execution, document)
        result = run(
            VALIDATE_EXECUTION,
            "--phase",
            "execution",
            "--case-gate",
            self.case_gate,
            "--final-cases",
            self.cases,
            "--execution-plan-gate",
            self.plan_gate,
            "--execution-plan",
            self.execution_plan,
            "--execution-results",
            self.execution,
            "--screenshots-dir",
            self.screenshots,
            "--output",
            self.execution_gate,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must exactly match final cases", result.stderr)

    def test_execution_result_generator_preserves_complete_gated_mapping(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        source_document = json.loads(self.execution.read_text(encoding="utf-8"))
        mapping_path = self.root / "case_mapping.yaml"
        script_status_path = self.root / "script_status.yaml"
        write_json(
            mapping_path,
            {
                "case_execution_mapping": [
                    {"case_id": "TC-001", "test_node": "node-1", "script_id": "S-001"},
                    {"case_id": "TC-002", "test_node": "node-2", "script_id": "S-002"},
                    {"case_id": "TC-003", "test_node": "node-3", "script_id": "S-003"},
                ]
            },
        )
        raw_results = []
        for node, result in zip(("node-1", "node-2", "node-3"), source_document["execution_results"]):
            raw_results.append({"test_node": node, **result})
        write_json(script_status_path, {"script_execution_status": raw_results})
        result = run(
            GENERATE_EXECUTION,
            "--mapping",
            mapping_path,
            "--script-status",
            script_status_path,
            "--final-cases",
            self.cases,
            "--case-gate",
            self.case_gate,
            "--execution-plan",
            self.execution_plan,
            "--execution-plan-gate",
            self.plan_gate,
            "--output",
            self.execution,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        generated = yaml.safe_load(self.execution.read_text(encoding="utf-8"))
        self.assertEqual(
            [item["case_id"] for item in generated["execution_results"]],
            ["TC-001", "TC-002", "TC-003"],
        )
        self.assertEqual(len(generated["execution_results"][1]["attempts"]), 3)

    def test_execution_gate_rejects_mixed_pair_without_third_attempt(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        result = document["execution_results"][2]
        failed_attempt = deepcopy(result["attempts"][1])
        failed_attempt["status"] = "failed"
        failed_attempt["observations"]["receiver_value"]["value"] = "李四"
        failed_attempt["produced_data"]["receiver_value"] = "李四"
        result["attempts"] = [result["attempts"][0], failed_attempt]
        self.refresh_execution_summary(document)
        write_json(self.execution, document)
        validation = self.validate_execution_results()
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("requires 3 attempts under retry policy 3.0", validation.stderr)

    def test_execution_gate_accepts_mixed_pair_after_unstable_third_pass(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        result = document["execution_results"][2]
        failed_attempt = deepcopy(result["attempts"][1])
        failed_attempt["status"] = "failed"
        failed_attempt["observations"]["receiver_value"]["value"] = "李四"
        failed_attempt["produced_data"]["receiver_value"] = "李四"
        third_attempt = deepcopy(result["attempts"][0])
        third_attempt["attempt"] = 3
        result.update(
            {
                "attempts": [result["attempts"][0], failed_attempt, third_attempt],
                "selected_attempt": 3,
                "stability": "unstable",
                "retry_policy": {
                    "minimum_attempts": 2,
                    "required_attempts": 3,
                    "third_attempt_required": True,
                    "single_run_exempt": False,
                    "full_retry_upgraded": False,
                    "priority": "P2",
                    "reason": "第三遍通过",
                },
            }
        )
        self.refresh_execution_summary(document)
        write_json(self.execution, document)
        validation = self.validate_execution_results()
        self.assertEqual(validation.returncode, 0, validation.stderr)

    def test_retry_decision_does_not_ignore_extra_third_attempt_evidence(self) -> None:
        p1_decision = retry_decision(
            [{"status": "passed"}, {"status": "passed"}, {"status": "failed"}],
            priority="P1",
        )
        self.assertEqual(p1_decision.final_status, "failed")
        self.assertEqual(p1_decision.selected_attempt, 3)
        self.assertEqual(p1_decision.stability, "unstable")

        p0_decision = retry_decision(
            [{"status": "passed"}, {"status": "passed"}, {"status": "failed"}],
            priority="P0",
        )
        self.assertEqual(p0_decision.final_status, "passed")
        self.assertEqual(p0_decision.required_attempts, 3)
        self.assertEqual(p0_decision.stability, "unstable")

    def test_screenshot_requirement_follows_case_interface(self) -> None:
        ui_case = {"test_intent": {"interface": "ui"}}
        api_case = {"test_intent": {"interface": "api"}}
        self.assertTrue(requires_ui_screenshot(ui_case, "passed", [{"status": "passed"}]))
        self.assertTrue(requires_ui_screenshot(ui_case, "blocked", [{"status": "blocked"}]))
        self.assertFalse(requires_ui_screenshot(api_case, "passed", [{"status": "passed"}]))
        self.assertFalse(requires_ui_screenshot(api_case, "failed", [{"status": "failed"}]))

    @unittest.skipUnless(shutil.which("node"), "Node.js is required to validate the shared browser retry template")
    def test_browser_retry_template_enforces_p0_majority_and_p2_upgrade(self) -> None:
        module_path = (TEMPLATE_DIR / "retry-framework.js").as_posix()
        script = f"""
const {{ runWithQualityRetry }} = require({json.dumps(module_path)});
(async () => {{
  const p0Statuses = ['passed', 'failed', 'passed'];
  const p0 = await runWithQualityRetry('TC-P0', async i => ({{status: p0Statuses[i - 1]}}), {{priority: 'P0'}});
  const p2Pass = await runWithQualityRetry('TC-P2-PASS', async () => ({{status: 'passed'}}), {{priority: 'P2', executionMode: 'risk_based_regression', minimumAttempts: 1, singleRunQualified: true}});
  const p2Statuses = ['failed', 'passed', 'passed'];
  const p2Upgrade = await runWithQualityRetry('TC-P2-UPGRADE', async i => ({{status: p2Statuses[i - 1]}}), {{priority: 'P2', executionMode: 'risk_based_regression', minimumAttempts: 1, singleRunQualified: true}});
  process.stdout.write(JSON.stringify({{p0, p2Pass, p2Upgrade}}));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["p0"]["status"], "passed")
        self.assertEqual(payload["p0"]["selected_attempt"], 3)
        self.assertEqual(payload["p2Pass"]["stability"], "qualified_single_run")
        self.assertEqual(len(payload["p2Pass"]["attempts"]), 1)
        self.assertTrue(payload["p2Upgrade"]["retry_policy"]["full_retry_upgraded"])
        self.assertEqual(len(payload["p2Upgrade"]["attempts"]), 3)

    def test_execution_gate_rejects_p0_with_only_one_pass_in_three(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        result = document["execution_results"][0]
        failed_attempts = []
        for attempt_number in (2, 3):
            attempt = deepcopy(result["attempts"][0])
            attempt["attempt"] = attempt_number
            attempt["status"] = "failed"
            attempt["observations"]["success_text"]["value"] = "创建失败"
            attempt["produced_data"]["success_text"] = "创建失败"
            failed_attempts.append(attempt)
        result["attempts"] = [result["attempts"][0], *failed_attempts]
        result["stability"] = "unstable"
        result["retry_policy"]["required_attempts"] = 3
        result["retry_policy"]["third_attempt_required"] = True
        self.refresh_execution_summary(document)
        write_json(self.execution, document)
        validation = self.validate_execution_results()
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("final status must be failed", validation.stderr)

    def test_execution_gate_rejects_blocked_case_without_probe_even_with_attempts(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        result = document["execution_results"][2]
        attempts = []
        for attempt_number in (1, 2, 3):
            attempt = deepcopy(result["attempts"][0])
            attempt["attempt"] = attempt_number
            attempt["status"] = "blocked"
            attempts.append(attempt)
        result.update(
            {
                "status": "blocked",
                "attempts": attempts,
                "selected_attempt": 3,
                "stability": "blocked",
                "blocker_reason": "",
                "capability_probe": {},
                "retry_policy": {
                    "minimum_attempts": 2,
                    "required_attempts": 3,
                    "third_attempt_required": True,
                    "single_run_exempt": False,
                    "full_retry_upgraded": False,
                    "priority": "P2",
                    "reason": "第三遍仍阻塞",
                },
            }
        )
        self.refresh_execution_summary(document)
        write_json(self.execution, document)
        validation = self.validate_execution_results()
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("must include blocker_reason", validation.stderr)
        self.assertIn("must include capability_probe.attempted_channels and probe_log", validation.stderr)

    def test_execution_gate_rejects_evidence_hash_or_anchor_mismatch(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        manifest = document["execution_results"][0]["evidence"]["manifest"][0]
        manifest["sha256"] = "0" * 64
        manifest["content_anchors"] = ["页面中不存在的锚点"]
        write_json(self.execution, document)
        validation = self.validate_execution_results()
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("evidence manifest SHA-256 mismatch", validation.stderr)
        self.assertIn("content_text misses anchors", validation.stderr)

    def test_execution_plan_and_gate_accept_qualified_p2_single_run(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.create_execution_plan()
        plan_document = json.loads(self.execution_plan.read_text(encoding="utf-8"))
        plan_document["execution_meta"]["execution_mode"] = "risk_based_regression"
        plan_document["execution_meta"]["resource_budget"]["estimated_attempts"] = 5
        p2_plan = plan_document["browser_execution_plan"][2]
        p2_plan["execution_budget"] = {
            "minimum_attempts": 1,
            "single_run_exemption": {
                "applied": True,
                "baseline_input_sha256": deepcopy(plan_document["execution_meta"]["input_sha256"]),
                "historical_attempts": 2,
                "historical_passed": 2,
                "historical_stability": "stable",
                "requirement_affected": False,
                "baseline_execution_results_sha256": "c" * 64,
                "rationale": "输入一致、需求未受影响且历史两遍稳定通过",
            },
        }
        write_json(self.execution_plan, plan_document)
        result = run(
            VALIDATE_PLAN,
            "--case-gate", self.case_gate,
            "--final-cases", self.cases,
            "--execution-plan", self.execution_plan,
            "--output", self.plan_gate,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.create_execution_results()
        execution_document = json.loads(self.execution.read_text(encoding="utf-8"))
        execution_document["execution_meta"]["execution_mode"] = "risk_based_regression"
        p2_result = execution_document["execution_results"][2]
        p2_result["attempts"] = [p2_result["attempts"][0]]
        p2_result["stability"] = "qualified_single_run"
        p2_result["retry_policy"] = {
            "minimum_attempts": 1,
            "required_attempts": 1,
            "third_attempt_required": False,
            "single_run_exempt": True,
            "full_retry_upgraded": False,
            "priority": "P2",
            "reason": "P2 风险回归单跑资格生效",
        }
        self.refresh_execution_summary(execution_document)
        write_json(self.execution, execution_document)
        validation = self.validate_execution_results()
        self.assertEqual(validation.returncode, 0, validation.stderr)

    def test_execution_plan_rejects_unqualified_p2_single_run(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.create_execution_plan()
        plan_document = json.loads(self.execution_plan.read_text(encoding="utf-8"))
        plan_document["execution_meta"]["execution_mode"] = "risk_based_regression"
        plan_document["execution_meta"]["resource_budget"]["estimated_attempts"] = 5
        plan_document["browser_execution_plan"][2]["execution_budget"] = {
            "minimum_attempts": 1,
            "single_run_exemption": {
                "applied": True,
                "baseline_input_sha256": {"requirements": "d" * 64},
                "historical_attempts": 1,
                "historical_passed": 1,
                "historical_stability": "unstable",
                "requirement_affected": True,
                "baseline_execution_results_sha256": "bad",
                "rationale": "",
            },
        }
        write_json(self.execution_plan, plan_document)
        result = run(
            VALIDATE_PLAN,
            "--case-gate", self.case_gate,
            "--final-cases", self.cases,
            "--execution-plan", self.execution_plan,
            "--output", self.plan_gate,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("baseline_input_sha256 must equal current input_sha256", result.stderr)
        self.assertIn("requires at least two historical attempts", result.stderr)

    def test_execution_gate_rejects_raw_dom_and_incomplete_defect_lifecycle(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        failed_result = document["execution_results"][1]
        failed_result["actual_result"] = "<html><body>raw failure page</body></html>"
        failed_result["defect"].pop("owner")
        write_json(self.execution, document)
        validation = self.validate_execution_results()
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("actual_result contains raw DOM", validation.stderr)
        self.assertIn("defect.owner must not be blank", validation.stderr)

    def test_case_gate_rejects_positive_persistence_case_without_save(self) -> None:
        case = self.configure_persistence_case("persisted_validation")
        case["formal_steps"] = [
            {"step_no": 1, "action": "在收件人字段输入张三", "target": "订单表单"},
            {"step_no": 2, "action": "重新打开同一订单并读取回显值", "target": "订单详情"},
            {"step_no": 3, "action": "比较输入值与保存后回显值", "target": "收件人字段"},
        ]
        write_json(self.cases, self.case_document)
        validation = self.create_case_gate()
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("persistence steps must explicitly save or submit", validation.stderr)

    def test_case_gate_rejects_edit_without_before_input_and_persisted_values(self) -> None:
        case = self.configure_persistence_case("edit")
        missing = {"before_value", "input_value", "persisted_value"}
        contract = case["result_contract"]
        for field in ("required_produced_keys", "assertion_observation_keys"):
            contract[field] = [key for key in contract[field] if key not in missing]
        contract["assertion_policy"]["hard_keys"] = [
            key for key in contract["assertion_policy"]["hard_keys"] if key not in missing
        ]
        contract["observations"] = [
            item for item in contract["observations"] if item["key"] not in missing
        ]
        write_json(self.cases, self.case_document)
        validation = self.create_case_gate()
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("persistence result contract is missing keys", validation.stderr)
        for key in sorted(missing):
            self.assertIn(key, validation.stderr)

    def test_scenario_edit_uses_state_contract_without_field_persistence_requirements(self) -> None:
        scenario = self.case_document["test_cases"][0]
        scenario["operation_type"] = "edit"
        scenario.pop("persistence_verification", None)
        write_json(self.cases, self.case_document)

        self.assertEqual(self.create_case_gate().returncode, 0)
        self.create_execution_plan()
        plan_document = json.loads(self.execution_plan.read_text(encoding="utf-8"))
        scenario_plan = next(
            item for item in plan_document["browser_execution_plan"] if item["case_id"] == "TC-001"
        )
        self.assertEqual(
            scenario_plan["evidence_plan"]["screenshot_scope"],
            "assertion_state_view",
        )
        self.assertEqual(self.create_plan_gate().returncode, 0)

        self.create_execution_results()
        execution_document = json.loads(self.execution.read_text(encoding="utf-8"))
        self.upgrade_execution_evidence_to_v2(execution_document)
        write_json(self.execution, execution_document)
        self.assertEqual(self.validate_execution_results().returncode, 0)

    def test_execution_plan_rejects_runtime_headless_mismatch(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.create_execution_plan()
        document = json.loads(self.execution_plan.read_text(encoding="utf-8"))
        document["browser_execution_plan"][0]["runtime_env"]["headless"] = True
        write_json(self.execution_plan, document)
        validation = run(
            VALIDATE_PLAN,
            "--case-gate", self.case_gate,
            "--final-cases", self.cases,
            "--execution-plan", self.execution_plan,
            "--output", self.plan_gate,
        )
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("runtime_env.headless must match execution_meta.browser_runtime", validation.stderr)

    def test_execution_gate_rejects_final_screenshot_not_copied_from_selected_attempt(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        self.upgrade_execution_evidence_to_v2(document)
        canonical = self.screenshots / "TC-001.png"
        canonical.write_bytes(ONE_PIXEL_PNG + b"different-final-image")
        document["execution_results"][0]["evidence"]["manifest"][0]["sha256"] = hashlib.sha256(
            canonical.read_bytes()
        ).hexdigest()
        write_json(self.execution, document)
        validation = self.validate_execution_results()
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("final screenshot must be copied from selected attempt", validation.stderr)

    def test_execution_gate_rejects_runtime_headless_evidence_mismatch(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        self.upgrade_execution_evidence_to_v2(document)
        document["execution_meta"]["browser_runtime"].update({"headless": True, "mode": "headless"})
        write_json(self.execution, document)
        validation = self.validate_execution_results()
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("headless does not match runtime", validation.stderr)

    def test_execution_gate_rejects_generic_navigation_anchor(self) -> None:
        self.assertEqual(self.create_case_gate().returncode, 0)
        self.assertEqual(self.create_plan_gate().returncode, 0)
        self.create_execution_results()
        document = json.loads(self.execution.read_text(encoding="utf-8"))
        self.upgrade_execution_evidence_to_v2(document)
        manifest = document["execution_results"][0]["evidence"]["manifest"][0]
        manifest["content_text"] = "店铺"
        manifest["content_anchors"] = ["店铺"]
        write_json(self.execution, document)
        validation = self.validate_execution_results()
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("uses generic anchors", validation.stderr)


if __name__ == "__main__":
    unittest.main()
