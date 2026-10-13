"""End-to-end tests for the generation/review quality-contract switch.

These tests exist because the contract used to be opt-in under a field name the
documentation never mentioned: an artifact that declared the documented field
silently skipped the whole contract and the gate still reported ``pass``.  They
pin the four rules that fix it:

1. one canonical version field, with the legacy name accepted as an alias;
2. a missing contract fails ``full`` / ``generation_review`` delivery;
3. ``N_min == 0`` is a contract failure, not a satisfied lower bound;
4. ``--legacy-audit`` reports gaps instead of blocking, and is visible.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from validate_case_design import semantic_identity_key  # noqa: E402

TEMPLATE_DIR = SCRIPT_DIR.parent.parent / "assets" / "templates"
GATE = SCRIPT_DIR / "validate_case_design.py"
CANONICAL = "quality_contract_version"
LEGACY = "case_count_contract_version"


class QualityContractSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        # The shipped templates are the reference for a contract-compliant
        # artifact, so every scenario is a controlled mutation of them.
        for name in ("requirements.yaml", "coverage-matrix.yaml", "test-cases.yaml"):
            shutil.copyfile(TEMPLATE_DIR / name, self.root / name)
        self.requirements = self.load("requirements.yaml")
        self.cases = self.load("test-cases.yaml")
        self.points = self.load("coverage-matrix.yaml")
        self.gate = self.root / "gate.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_scope_confirmation_accepts_timezone_and_rejects_invalid_calendar_date(self) -> None:
        self.requirements["generation_scope"]["confirmed_at"] = "2026-10-13T14:00:00+08:00"
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.requirements["generation_scope"]["confirmed_at"] = "2026-02-30"
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("valid ISO 8601", result.stderr)

    def test_smoke_label_cannot_waive_the_full_generation_contract(self) -> None:
        self.requirements.pop(CANONICAL)
        self.cases["case_source"].pop(CANONICAL)
        self.requirements["generation_scope"]["mode"] = "smoke"
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("explicit l2_only", result.stderr)

    def test_missing_scope_reports_gate_errors_without_crashing(self) -> None:
        self.requirements.pop("generation_scope")
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requirements.generation_scope must declare", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(self.payload()["status"], "fail")

    def load(self, name: str) -> dict:
        return yaml.safe_load((self.root / name).read_text(encoding="utf-8"))

    def save(self) -> None:
        for name, document in (
            ("requirements.yaml", self.requirements),
            ("coverage-matrix.yaml", self.points),
            ("test-cases.yaml", self.cases),
        ):
            (self.root / name).write_text(
                yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )

    def run_gate(self, *extra: str) -> subprocess.CompletedProcess[str]:
        self.save()
        return subprocess.run(
            [
                sys.executable, str(GATE),
                "--requirements", str(self.root / "requirements.yaml"),
                "--test-points", str(self.root / "coverage-matrix.yaml"),
                "--final-cases", str(self.root / "test-cases.yaml"),
                "--output", str(self.gate),
                *extra,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )

    def payload(self) -> dict:
        return json.loads(self.gate.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # 1. baseline: the shipped templates are contract-compliant
    # ------------------------------------------------------------------
    def test_contract_compliant_templates_pass_with_enforcement_on(self) -> None:
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        contract = self.payload()["quality_contract"]
        self.assertTrue(contract["enforced"])
        self.assertEqual(contract["status"], "enforced")
        self.assertEqual(contract["declared_field"], CANONICAL)

    # ------------------------------------------------------------------
    # 2. P0-2: missing contract must fail in full / generation_review
    # ------------------------------------------------------------------
    def test_missing_contract_fails_strict_delivery(self) -> None:
        self.requirements.pop(CANONICAL, None)
        self.requirements.pop(LEGACY, None)
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must declare quality_contract_version", result.stderr)
        contract = self.payload()["quality_contract"]
        self.assertFalse(contract["enforced"])
        self.assertEqual(contract["status"], "missing")
        self.assertTrue(contract["required"])

    def test_l2_only_delivery_still_waives_the_contract(self) -> None:
        self.configure_simple_rule()
        self.requirements.pop(CANONICAL, None)
        self.requirements.pop(LEGACY, None)
        self.requirements["generation_scope"]["mode"] = "l2_only"
        self.cases["case_source"]["mode"] = "l2_only"
        self.points["coverage_profile"]["mode"] = "l2_only"
        self.points["coverage_profile"]["core_form_fields_present"] = True
        self.points["coverage_profile"]["required_granularities"] = ["scenario", "field"]
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("must declare quality_contract_version", result.stderr)
        self.assertEqual(self.payload()["quality_contract"]["status"], "waived_l2_only")

    def test_declared_contract_in_l2_scope_is_enforced_and_not_marked_waived(self) -> None:
        self.requirements["generation_scope"]["mode"] = "l2_only"
        self.cases["case_source"]["mode"] = "l2_only"
        self.points["coverage_profile"]["mode"] = "l2_only"
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.payload()["quality_contract"]["enforced"])
        self.assertFalse(self.payload()["quality_contract"]["waived"])

    def configure_simple_rule(self) -> None:
        statement = self.requirements["normative_statements"][0]
        statement["source_excerpt"] = "保存商品后列表显示该商品。"
        rule = self.requirements["atomic_rules"][0]
        rule.update({"rule_type": "display", "statement": statement["source_excerpt"], "source_excerpt": statement["source_excerpt"],
                     "coverage_plan": {"required_design_methods": ["scenario"], "required_dimensions": ["positive"], "required_classes": ["main_flow"]},
                     "coverage_characteristics": {"profile": "display", "applicability": {key: False for key in rule["coverage_characteristics"]["applicability"]}}})
        signature = "RULE-001|positive|main_flow|accepted"
        self.requirements["coverage_obligations"] = self.requirements["coverage_obligations"][:1]
        self.requirements["coverage_obligations"][0]["coverage_unit_signature"] = signature
        self.points["test_points"] = self.points["test_points"][:1]
        self.points["test_points"][0]["coverage_unit_signature"] = signature
        self.points["coverage_profile"].update({"core_form_fields_present": False, "required_granularities": ["scenario"]})
        self.points["field_coverage"] = []
        self.cases["test_cases"] = self.cases["test_cases"][:1]
        self.cases["test_cases"][0]["coverage_unit_signature"] = signature
        review = self.cases["review_audit"]
        review["per_case_scores"] = review["per_case_scores"][:1]
        review["score_distribution"].update({"scored_cases": 1, "perfect_scores": int(review["per_case_scores"][0]["score"] == 22)})

    def test_a_simple_rule_uses_the_computed_minimum_without_padding_cases(self) -> None:
        self.configure_simple_rule()
        review = self.cases["review_audit"]
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("priority_coverage_rationale", result.stderr)
        review["priority_coverage_rationale"] = {"P1": "夹具只有一个核心场景，无其他主要分支", "P2": "来源未声明其他低风险验证事实"}
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.payload()["calculated_minimum_cases"], 1)

    def test_legacy_audit_discloses_declared_but_incomplete_count_contract(self) -> None:
        self.requirements["atomic_rules"][0].pop("coverage_characteristics")
        result = self.run_gate("--legacy-audit")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.payload()["quality_contract"]["enforced"])
        self.assertFalse(self.payload()["case_count_contract"]["active"])

    # ------------------------------------------------------------------
    # 3. P0-1: the legacy field name must enforce the same contract
    # ------------------------------------------------------------------
    def test_legacy_alias_declaration_enforces_the_full_contract(self) -> None:
        self.requirements[LEGACY] = "1.0"
        self.requirements.pop(CANONICAL, None)
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        contract = self.payload()["quality_contract"]
        self.assertEqual(contract["declared_field"], LEGACY)
        self.assertTrue(contract["enforced"], "legacy alias must enable the same contract")
        self.assertIn("legacy alias", result.stdout + result.stderr)

    def test_legacy_alias_run_is_actually_enforced(self) -> None:
        """The decisive check: a broken artifact must fail under the alias too."""
        self.requirements[LEGACY] = "1.0"
        self.requirements.pop(CANONICAL, None)
        self.cases["test_cases"][0].pop("semantic_identity", None)
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must declare semantic_identity", result.stderr)

    def test_conflicting_versions_are_rejected(self) -> None:
        self.requirements[CANONICAL] = "1.0"
        self.requirements[LEGACY] = "2.0"
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicting contract versions", result.stderr)

    def test_redundant_double_declaration_only_warns(self) -> None:
        self.requirements[CANONICAL] = "1.0"
        self.requirements[LEGACY] = "1.0"
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("legacy alias", result.stdout + result.stderr)
        self.assertTrue(self.payload()["quality_contract"]["enforced"])

    # ------------------------------------------------------------------
    # 4. P0-3: N_min == 0 is a failure
    # ------------------------------------------------------------------
    def test_zero_calculated_minimum_is_a_failure(self) -> None:
        for rule in self.requirements["atomic_rules"]:
            rule.pop("coverage_characteristics", None)
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("calculated_minimum_cases is 0", result.stderr)
        self.assertEqual(self.payload()["case_count_contract"]["minimum_derivation"], "zero")

    # ------------------------------------------------------------------
    # 5. P1-6: --legacy-audit reports gaps without blocking
    # ------------------------------------------------------------------
    def test_legacy_audit_reports_gaps_without_blocking(self) -> None:
        self.requirements.pop(CANONICAL, None)
        self.requirements.pop(LEGACY, None)
        self.requirements.pop("normative_statements", None)
        self.requirements.pop("source_coverage_inventory", None)
        for case in self.cases["test_cases"]:
            case.pop("semantic_identity", None)
            case.pop("title_contract", None)
            case.pop("precondition_state", None)
        result = self.run_gate("--legacy-audit")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.payload()
        self.assertEqual(payload["status"], "pass")
        self.assertFalse(payload["quality_contract"]["enforced"])
        report = payload["legacy_audit"]
        self.assertEqual(report["compliance"], "legacy")
        self.assertGreater(report["gap_count"], 0)
        fields = {gap["field"] for gap in report["gaps"]}
        self.assertIn(CANONICAL, fields)
        self.assertIn("semantic_identity", fields)
        self.assertIn("normative_statements", fields)
        # The missing contract is downgraded to a warning, not dropped.
        self.assertTrue(any("legacy-audit did not block" in warning for warning in payload["warnings"]))
        self.assertNotIn("ERROR: full/generation_review delivery must declare", result.stderr)

    # ------------------------------------------------------------------
    # 6. P2-9 / P2-10: traceability and estimate cross-check
    # ------------------------------------------------------------------
    def test_generation_scope_requires_confirmation_traceability(self) -> None:
        scope = self.requirements["generation_scope"]
        scope.pop("confirmed_by", None)
        scope.pop("confirmed_at", None)
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("generation_scope.confirmed_by", result.stderr)
        self.assertIn("generation_scope.confirmed_at", result.stderr)

    def test_invalid_confirmation_timestamp_is_rejected(self) -> None:
        self.requirements["generation_scope"]["confirmed_at"] = "昨天"
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ISO 8601", result.stderr)

    def test_estimate_below_calculated_minimum_warns(self) -> None:
        self.requirements["generation_scope"]["estimated_case_range"] = {"min": 1, "max": 1}
        result = self.run_gate()
        payload = self.payload()
        self.assertEqual(payload["quality_contract"]["estimate_vs_minimum"], "below_minimum")
        self.assertTrue(any("is below the independently" in warning for warning in payload["warnings"]))

    # ------------------------------------------------------------------
    # 7. P1-5: aggregated error reporting
    # ------------------------------------------------------------------
    def test_error_report_is_aggregated_and_written(self) -> None:
        for rule in self.requirements["atomic_rules"]:
            rule.pop("coverage_characteristics", None)
        target = self.root / "errors.md"
        result = self.run_gate("--error-report", str(target))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(target.exists())
        summary = self.payload()["error_summary"]
        self.assertTrue(summary)
        self.assertLessEqual(len(summary), len(self.payload()["errors"]))
        self.assertTrue(all("<rule>" in bucket["category"] or bucket["count"] for bucket in summary))
        self.assertIn("命中数", target.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # 8. P1-4: blank identities must not collapse into one duplicate group
    # ------------------------------------------------------------------
    def test_all_blank_identity_is_not_treated_as_a_duplicate(self) -> None:
        blank = {key: "" for key in ("port", "page", "object", "condition", "action", "assertion")}
        self.assertFalse(semantic_identity_key({"semantic_identity": blank}))
        self.assertFalse(semantic_identity_key({}))
        self.assertTrue(
            semantic_identity_key({
                "semantic_identity": {**blank, "port": "PC商城"},
            })
        )

    def test_blank_identities_produce_no_semantic_duplicate_error(self) -> None:
        blank = {key: "" for key in ("port", "page", "object", "condition", "action", "assertion")}
        for case in self.cases["test_cases"]:
            case["semantic_identity"] = dict(blank)
        result = self.run_gate()
        self.assertNotIn("Semantic duplicate", result.stderr)
        report = self.payload()["semantic_quality"]
        self.assertEqual(report["semantic_duplicate_groups"], [])
        self.assertEqual(report["identity_missing_count"], 0)
        self.assertEqual(len(report["identity_incomplete_ids"]), 3)

    def test_real_semantic_duplicate_is_still_detected(self) -> None:
        first, second = self.cases["test_cases"][0], self.cases["test_cases"][1]
        second["semantic_identity"] = copy.deepcopy(first["semantic_identity"])
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Semantic duplicate", result.stderr)


if __name__ == "__main__":
    unittest.main()
