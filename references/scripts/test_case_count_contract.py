from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from validate_case_design import (  # noqa: E402
    CASE_COUNT_DIMENSIONS,
    _coverage_characteristic_units,
    validate_case_count_contract,
)


def applicability(active: str) -> dict[str, bool]:
    return {dimension: dimension == active for dimension in CASE_COUNT_DIMENSIONS}


class CaseCountContractTests(unittest.TestCase):
    def test_text_input_expands_partitions_and_boundaries(self) -> None:
        rule = {
            "rule_id": "RULE-INPUT",
            "coverage_characteristics": {
                "profile": "text_input",
                "applicability": applicability("input"),
                "required": True,
                "valid_partitions": ["chinese", "english"],
                "boundaries": {"min": 1, "max": 100},
            },
        }
        units, errors = _coverage_characteristic_units(rule)
        self.assertFalse(errors)
        self.assertEqual(len(units), 8)
        self.assertIn("RULE-INPUT|negative|invalid|empty|rejected", units)
        self.assertIn("RULE-INPUT|boundary|max_plus_1|rejected", units)

    def test_missing_signature_and_wrong_signature_fail(self) -> None:
        rule = {
            "rule_id": "RULE-001",
            "disposition": "covered",
            "coverage_characteristics": {
                "profile": "ordinary",
                "applicability": applicability("input"),
            },
        }
        requirement_doc = {
            "case_count_contract_version": "1.0",
            "atomic_rules": [rule],
        }
        obligations = [{"obligation_id": "OB-1", "rule_id": "RULE-001", "coverage_unit_signature": "wrong"}]
        errors, report = validate_case_count_contract(requirement_doc, obligations, [], [])
        self.assertEqual(report["calculated_minimum_cases"], 1)
        self.assertTrue(any("missing required coverage signatures" in error for error in errors))
        self.assertTrue(any("undeclared coverage signatures" in error for error in errors))

    def test_applicability_is_mandatory(self) -> None:
        rule = {
            "rule_id": "RULE-001",
            "coverage_characteristics": {"profile": "ordinary", "applicability": {}},
        }
        units, errors = _coverage_characteristic_units(rule)
        self.assertTrue(units)
        self.assertTrue(any("applicability.input" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
