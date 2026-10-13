"""Self-tests for the read-only PRD source-text scanner.

The scanner is a review aid, not a gate, so these tests pin the behavior that
keeps it trustworthy as an aid: normative-candidate detection must not fire on
background sentences, ledger matching must accept verbatim quotes and clause
chunks while rejecting unrelated sentences, and the boundary heuristic must
only fire for input/comparison rules with numbers and no declared boundaries.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import scan_prd_source_statements as scanner  # noqa: E402

SCANNER = SCRIPT_DIR / "scan_prd_source_statements.py"


class ScannerUnitTests(unittest.TestCase):
    def test_normative_markers_and_quantities_trigger_candidates(self) -> None:
        self.assertTrue(scanner.is_normative_candidate("商品名称必须填写"))
        self.assertTrue(scanner.is_normative_candidate("单次最多只能购买999件商品"))
        self.assertTrue(scanner.is_normative_candidate("订单超时未支付将自动取消"))
        self.assertTrue(scanner.is_normative_candidate("下单后30分钟内未支付则关闭订单"))

    def test_background_sentences_are_not_candidates(self) -> None:
        self.assertFalse(scanner.is_normative_candidate("本版本整体提升了用户的购物体验"))
        self.assertFalse(scanner.is_normative_candidate("商城面向广大消费者群体"))

    def test_verbatim_excerpt_covers_sentence_either_direction(self) -> None:
        excerpts = [("statement:NS-001", "商品名称必须填写且长度不超过100个字符")]
        sentence = "用户提交时，商品名称必须填写且长度不超过100个字符，否则提示错误。"
        covered, best_ref, score = scanner.match_excerpts(sentence, excerpts, 0.55)
        self.assertTrue(covered)
        self.assertEqual(best_ref, "statement:NS-001")
        self.assertGreaterEqual(score, 0.99)

    def test_clause_chunk_match_covers_partial_quote(self) -> None:
        excerpts = [("atomic_rule:RULE-001", "下单前库存充足，支付成功后订单状态变更为待发货")]
        sentence = "订单支付成功后订单状态变更为待发货并通知买家。"
        covered, _, score = scanner.match_excerpts(sentence, excerpts, 0.55)
        self.assertTrue(covered)
        self.assertGreaterEqual(score, 0.6)

    def test_unrelated_sentence_stays_uncovered(self) -> None:
        excerpts = [("statement:NS-001", "商品名称必须填写且长度不超过100个字符")]
        sentence = "订单超时未支付自动取消，等待时间不超过30分钟。"
        covered, _, score = scanner.match_excerpts(sentence, excerpts, 0.55)
        self.assertFalse(covered)
        self.assertLess(score, 0.55)

    def test_boundary_warning_requires_input_and_numbers_and_missing_boundaries(self) -> None:
        base = {
            "rule_id": "RULE-001",
            "coverage_characteristics": {
                "profile": "text_input",
                "applicability": {"input": True},
            },
        }
        with_numbers = {**base, "source_excerpt": "商品名称长度不能超过100个字符"}
        self.assertEqual(len(scanner.boundary_warnings([with_numbers])), 1)

        declared = {
            **with_numbers,
            "coverage_characteristics": {
                "profile": "text_input",
                "applicability": {"input": True},
                "boundaries": {"min": 1, "max": 100},
            },
        }
        self.assertEqual(scanner.boundary_warnings([declared]), [])

        without_numbers = {**base, "source_excerpt": "商品名称必须填写"}
        self.assertEqual(scanner.boundary_warnings([without_numbers]), [])

        other_dimension = {
            **with_numbers,
            "coverage_characteristics": {
                "profile": "query",
                "applicability": {"input": False, "comparison": False, "query": True},
            },
        }
        self.assertEqual(scanner.boundary_warnings([other_dimension]), [])


class ScannerEndToEndTests(unittest.TestCase):
    def test_report_lists_uncovered_candidates_and_boundary_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prd = root / "01_需求原文.md"
            prd.write_text(
                "# 商城需求\n"
                "商品名称必须填写且长度不超过100个字符。\n"
                "订单超时未支付自动取消，等待时间不超过30分钟。\n"
                "本版本整体提升了用户的购物体验。\n",
                encoding="utf-8",
            )
            requirements = root / "05_requirements.yaml"
            requirements.write_text(
                "quality_contract_version: \"1.0\"\n"
                "normative_statements:\n"
                "  - statement_id: NS-001\n"
                "    source_document: PRD-001\n"
                "    source_excerpt: \"商品名称必须填写且长度不超过100个字符\"\n"
                "    disposition: covered\n"
                "atomic_rules:\n"
                "  - rule_id: RULE-001\n"
                "    source_excerpt: \"商品名称必填，长度上限100字符\"\n"
                "    coverage_characteristics:\n"
                "      profile: text_input\n"
                "      applicability: {input: true}\n"
                "      boundaries: {min: 1, max: 100}\n"
                "  - rule_id: RULE-002\n"
                "    source_excerpt: \"单次库存最多999件\"\n"
                "    coverage_characteristics:\n"
                "      profile: number_input\n"
                "      applicability: {input: true}\n",
                encoding="utf-8",
            )
            report = root / "review" / "source_scan.md"
            payload = root / "review" / "source_scan.yaml"

            result = subprocess.run(
                [
                    sys.executable, str(SCANNER),
                    "--prd", str(prd),
                    "--requirements", str(requirements),
                    "--output", str(report),
                    "--output-yaml", str(payload),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            report_text = report.read_text(encoding="utf-8")
            self.assertIn("未命中候选清单", report_text)
            self.assertIn("订单超时未支付自动取消", report_text)
            self.assertIn("边界启发式警告", report_text)
            self.assertIn("RULE-002", report_text)
            self.assertNotIn("整体提升了用户的购物体验", report_text)
            self.assertIn("处置 disposition", report_text)

            machine = json.loads(json.dumps(_load_yaml(payload)))
            self.assertEqual(machine["summary"]["uncovered_candidates"], 1)
            self.assertEqual(machine["summary"]["total_sentences"], 3)
            self.assertEqual(len(machine["boundary_warnings"]), 1)

    def test_missing_inputs_fail_with_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = subprocess.run(
                [
                    sys.executable, str(SCANNER),
                    "--prd", str(root / "missing.md"),
                    "--requirements", str(root / "missing.yaml"),
                    "--output", str(root / "out.md"),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ERROR", result.stderr)


def _load_yaml(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
