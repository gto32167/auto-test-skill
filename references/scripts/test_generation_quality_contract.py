from __future__ import annotations

import sys
import unittest
from pathlib import Path
from copy import deepcopy
import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from validate_case_design import (  # noqa: E402
    CASE_COUNT_DIMENSIONS,
    _coverage_characteristic_units,
    validate_semantic_quality,
    validate_source_ledgers,
)
from backfill_quality_fields import derive_precondition, derive_step_targets, build_normative_ledger
from semantic_contract import validate_case_semantics


def applicability(active: str) -> dict[str, bool]:
    return {dimension: dimension == active for dimension in CASE_COUNT_DIMENSIONS}


class GenerationQualityContractTests(unittest.TestCase):
    def test_permission_facts_cannot_silently_drop_secondary_operations(self) -> None:
        rule = {"rule_id": "RULE-P", "coverage_characteristics": {
            "profile": "permission", "applicability": applicability("permission"),
            "roles": ["viewer"], "operations": ["read", "delete"],
            "data_scopes": ["own"], "outcomes": ["allowed"],
        }}
        _, errors = _coverage_characteristic_units(rule)
        self.assertTrue(any("exactly one operations" in error for error in errors))

    def test_environment_data_may_not_be_marked_passed_when_missing(self) -> None:
        root = SCRIPT_DIR.parents[1] / "assets" / "templates"
        requirements = yaml.safe_load((root / "requirements.yaml").read_text(encoding="utf-8"))
        points = yaml.safe_load((root / "coverage-matrix.yaml").read_text(encoding="utf-8"))["test_points"]
        cases = yaml.safe_load((root / "test-cases.yaml").read_text(encoding="utf-8"))["test_cases"]
        cases[0].update({"precondition_state": "environment_provided", "missing_data_behavior": "passed"})
        errors, _, _ = validate_semantic_quality(requirements, points, cases)
        self.assertTrue(any("missing_data_behavior: blocked" in error for error in errors))

    def test_reading_status_does_not_require_mutation_evidence_but_editing_does(self) -> None:
        root = SCRIPT_DIR.parents[1] / "assets" / "templates"
        case = yaml.safe_load((root / "test-cases.yaml").read_text(encoding="utf-8"))["test_cases"][0]
        case = deepcopy(case)
        case.update({"operation_type": "read", "case_title": "查看商品状态流转说明"})
        case.pop("change_evidence", None)
        self.assertFalse(any("state-changing intent" in error for error in validate_case_semantics(case, {}, {})))
        case["operation_type"] = "edit"
        self.assertTrue(any("state-changing intent" in error for error in validate_case_semantics(case, {}, {})))

    def test_legacy_migration_leaves_unsupported_facts_for_review(self) -> None:
        value, missing = derive_precondition({"preconditions": ["页面中有订单"]})
        self.assertIsNone(value)
        self.assertTrue(missing)
        steps, _ = derive_step_targets({"formal_steps": ["点击按钮"], "feature_group": "历史模块"})
        self.assertEqual(steps[0]["target"], "")
        statements, _, created = build_normative_ledger({"atomic_rules": [{"rule_id": "R1", "statement": "旧摘要"}]})
        self.assertEqual((statements, created), ([], 0))

    def test_file_profile_does_not_invent_empty_or_corrupt_cases(self) -> None:
        rule = {
            "rule_id": "RULE-FILE",
            "coverage_characteristics": {
                "profile": "file",
                "applicability": applicability("file"),
                "formats": ["xlsx"],
                "invalid_formats": ["txt"],
            },
        }
        units, errors = _coverage_characteristic_units(rule)
        self.assertFalse(errors)
        self.assertEqual(
            units,
            [
                "RULE-FILE|positive|format|xlsx|accepted",
                "RULE-FILE|negative|format|txt|rejected",
            ],
        )

    def test_explicit_units_cannot_override_profile_calculation(self) -> None:
        rule = {
            "rule_id": "RULE-INPUT",
            "coverage_characteristics": {
                "profile": "text_input",
                "applicability": applicability("input"),
                "valid_partitions": ["valid"],
                "units": [{
                    "coverage_unit_signature": "RULE-INPUT|positive|main_flow|accepted",
                    "source_fact_refs": ["NS-1"],
                    "derived_from": "manual count",
                }],
            },
        }
        units, errors = _coverage_characteristic_units(rule)
        self.assertEqual(len(units), 1)
        self.assertTrue(any("undeclared signatures" in error for error in errors))

    def test_semantic_identity_catches_reworded_duplicate_cases(self) -> None:
        requirement_doc = {"atomic_rules": [], "coverage_obligations": []}
        points = [{"test_point_id": "TP-1"}, {"test_point_id": "TP-2"}]
        identity = {
            "port": "PC",
            "page": "订单列表",
            "object": "开票入口",
            "condition": "待发货订单",
            "action": "查看入口",
            "assertion": "入口可见",
        }
        cases = [
            {"case_id": "TC-1", "test_point_id": "TP-1", "case_title": "待发货订单显示开票入口", "granularity": "scenario", "semantic_identity": identity, "title_contract": {"object": "开票入口", "action": "查看"}},
            {"case_id": "TC-2", "test_point_id": "TP-2", "case_title": "开票按钮在待发货状态可见", "granularity": "scenario", "semantic_identity": identity, "title_contract": {"object": "开票入口", "action": "查看"}},
        ]
        errors, _, report = validate_semantic_quality(requirement_doc, points, cases)
        self.assertTrue(any("Semantic duplicate" in error for error in errors))
        self.assertEqual(len(report["semantic_duplicate_groups"]), 1)

    def test_normative_ledger_requires_bidirectional_mapping(self) -> None:
        requirement_doc = {
            "source_documents": [{"document_id": "PRD-1"}, {"document_id": "PRD-2"}],
            "source_coverage_inventory": {
                "normative_statement_count": 1,
                "mapped_statement_count": 0,
                "excluded_statement_count": 0,
                "pending_statement_count": 0,
                "unmapped_statement_ids": ["NS-1"],
            },
            "normative_statements": [{
                "statement_id": "NS-1",
                "source_document": "PRD-1",
                "source_locator": {"section": "1"},
                "source_excerpt": "必须展示发票入口",
                "disposition": "covered",
                "atomic_rule_ids": [],
            }],
            "atomic_rules": [],
            "version_differences": [],
            "ambiguities": [],
        }
        errors, _ = validate_source_ledgers(requirement_doc)
        self.assertTrue(any("Covered normative statement without atomic rule" in error for error in errors))
        self.assertTrue(any("version_differences is missing adjacent" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
