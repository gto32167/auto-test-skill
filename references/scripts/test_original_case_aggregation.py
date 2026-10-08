from __future__ import annotations

import unittest

from aggregate_original_case_results import aggregate


class OriginalCaseAggregationTests(unittest.TestCase):
    """验证父用例聚合的优先级和失败原因追踪。"""

    def setUp(self) -> None:
        self.cases = [
            {
                "case_id": "TC-1",
                "case_title": "原始用例一-断言一",
                "normalization_trace": {"source_rows": [2]},
                "assertions": [{"expected": "列表可见"}],
            },
            {
                "case_id": "TC-2",
                "case_title": "原始用例一-断言二",
                "normalization_trace": {"source_rows": [2]},
                "assertions": [{"expected": "状态正确"}],
            },
            {
                "case_id": "TC-3",
                "case_title": "原始用例二-断言一",
                "normalization_trace": {"source_rows": [3]},
                "assertions": [{"expected": "保存成功"}],
            },
            {
                "case_id": "TC-4",
                "case_title": "原始用例三-断言一",
                "normalization_trace": {"source_rows": [4]},
                "assertions": [{"expected": "数据展示"}],
            },
        ]
        self.imported = [
            {"source_row": 2, "suggested_case_id": "LEGACY-2", "case_title": "原始用例一"},
            {"source_row": 3, "suggested_case_id": "LEGACY-3", "case_title": "原始用例二"},
            {"source_row": 4, "suggested_case_id": "LEGACY-4", "case_title": "原始用例三"},
        ]

    def test_failed_child_overrides_passed(self) -> None:
        payload = aggregate(
            self.cases,
            [
                {"case_id": "TC-1", "status": "passed"},
                {"case_id": "TC-2", "status": "failed", "failed_step_no": 3, "failure_reason": "状态未更新"},
                {"case_id": "TC-3", "status": "blocked", "blocker_reason": "缺少数据"},
            ],
            self.imported,
        )
        rows = {row["source_row"]: row for row in payload["rows"]}
        self.assertEqual(rows[2]["status"], "failed")
        self.assertIn("TC-2", rows[2]["failure_reason"])
        self.assertEqual(rows[3]["status"], "blocked")
        self.assertEqual(rows[4]["status"], "not_run")
        self.assertEqual(payload["summary"]["original_case_count"], 3)

    def test_mixed_pass_and_not_run_is_blocked(self) -> None:
        payload = aggregate(
            self.cases,
            [{"case_id": "TC-1", "status": "passed"}],
            self.imported,
        )
        rows = {row["source_row"]: row for row in payload["rows"]}
        self.assertEqual(rows[2]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
