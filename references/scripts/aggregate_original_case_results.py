from __future__ import annotations

"""将规范化子用例的执行结果聚合回原始 Excel 用例。

父用例与子用例通过 normalization_trace.source_rows/source_case_ids 绑定。
脚本只负责结果聚合和审计数据生成，不执行浏览器、不修改原始 Excel。
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml
from result_explanation import explain_result


VALID_STATUSES = {"passed", "failed", "blocked", "not_run"}


def load_yaml(path: Path) -> dict[str, Any]:
    """读取 YAML 对象。"""
    with path.open("r", encoding="utf-8") as stream:
        value = yaml.safe_load(stream) or {}
    if not isinstance(value, dict):
        raise ValueError(f"YAML 顶层必须是对象：{path}")
    return value


def text(value: Any) -> str:
    """把任意字段转成稳定文本。"""
    return "" if value is None else str(value).strip()


def normalize_status(value: Any) -> str:
    """统一执行状态。"""
    status = text(value).lower()
    aliases = {"pass": "passed", "fail": "failed", "通过": "passed", "失败": "failed", "阻塞": "blocked", "未执行": "not_run"}
    return aliases.get(status, status)


def child_summary(case: dict[str, Any], result: dict[str, Any] | None) -> dict[str, Any]:
    """提取子用例结果中可供原始用例复核的事实。"""
    case_id = text(case.get("case_id"))
    if result is None:
        return {
            "case_id": case_id,
            "status": "not_run",
            "title": text(case.get("formal_case_title") or case.get("case_title")),
            "assertion": text((case.get("assertions") or [{}])[0].get("expected")),
            "failed_step_no": "",
            "failure_reason": "",
            "blocker_type": "",
            "blocker_reason": "缺少该规范化子用例的执行结果。",
            "actual_result": "",
            "evidence": {},
            "produced_data": {},
        }
    status = normalize_status(result.get("status"))
    if status not in VALID_STATUSES:
        status = "blocked"
    summary = result.get("result_summary") or {}
    human_reason, next_action = explain_result(result, text((case.get("assertions") or [{}])[0].get("expected")))
    return {
        "case_id": case_id,
        "status": status,
        "title": text(case.get("formal_case_title") or case.get("case_title")),
        "assertion": text((case.get("assertions") or [{}])[0].get("expected")),
        "failed_step_no": text(result.get("failed_step_no")),
        "failure_reason": text(result.get("failure_reason") or result.get("defect_summary") or result.get("actual_result")),
        "blocker_type": text(result.get("blocker_type")),
        "blocker_reason": text(result.get("blocker_reason") or result.get("not_run_reason")),
        "actual_result": text(result.get("actual_result") or summary.get("actual")),
        "evidence": result.get("evidence") or {},
        "produced_data": result.get("produced_data") or {},
        "human_reason": human_reason,
        "next_action": next_action,
    }


def aggregate_status(children: list[dict[str, Any]]) -> tuple[str, str]:
    """按父用例门禁规则计算聚合状态和规则说明。"""
    statuses = [item["status"] for item in children]
    if any(status == "failed" for status in statuses):
        return "failed", "存在至少一个拆分子用例失败，原始用例不得判定为通过。"
    if any(status == "blocked" for status in statuses):
        return "blocked", "没有子用例失败，但存在阻塞结果，原始用例无法完成业务判定。"
    if any(status == "not_run" for status in statuses):
        if all(status == "not_run" for status in statuses):
            return "not_run", "该原始用例的拆分子用例均未执行。"
        return "blocked", "部分拆分子用例未执行，原始用例证据不完整。"
    if children and all(status == "passed" for status in statuses):
        return "passed", "该原始用例的全部拆分子用例均通过。"
    return "blocked", "没有形成完整的拆分子用例结果集合。"


def aggregate(final_cases: list[dict[str, Any]], execution_results: list[dict[str, Any]], imported_cases: list[dict[str, Any]]) -> dict[str, Any]:
    """按原始 Excel 行聚合所有规范化执行结果。"""
    results_by_id = {text(item.get("case_id")): item for item in execution_results if text(item.get("case_id"))}
    cases_by_row: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for case in final_cases:
        trace = case.get("normalization_trace") or {}
        rows = trace.get("source_rows") or []
        for row in rows:
            try:
                cases_by_row[int(row)].append(case)
            except (TypeError, ValueError):
                continue

    output_rows: list[dict[str, Any]] = []
    for source in imported_cases:
        source_row = int(source["source_row"])
        children = [child_summary(case, results_by_id.get(text(case.get("case_id")))) for case in cases_by_row.get(source_row, [])]
        status, rule = aggregate_status(children)
        counts = {name: sum(item["status"] == name for item in children) for name in ("passed", "failed", "blocked", "not_run")}
        failed_children = [item for item in children if item["status"] == "failed"]
        blocked_children = [item for item in children if item["status"] in {"blocked", "not_run"}]
        output_rows.append(
            {
                "source_row": source_row,
                "source_case_id": text(source.get("source_case_id") or source.get("suggested_case_id")),
                "source_title": text(source.get("case_title")),
                "status": status,
                "status_rule": rule,
                "child_count": len(children),
                "passed_count": counts["passed"],
                "failed_count": counts["failed"],
                "blocked_count": counts["blocked"],
                "not_run_count": counts["not_run"],
                "child_case_ids": [item["case_id"] for item in children],
                "failed_child_cases": failed_children,
                "blocked_child_cases": blocked_children,
                "result_summary": "；".join(
                    [
                        f"通过 {counts['passed']} 条",
                        f"失败 {counts['failed']} 条",
                        f"阻塞 {counts['blocked']} 条",
                        f"未执行 {counts['not_run']} 条",
                    ]
                ),
                "failure_reason": "；".join(
                    f"{item['case_id']}：{item['failure_reason'] or '未满足子用例预期'}" for item in failed_children
                ),
                "blocker_reason": "；".join(
                    f"{item['case_id']}：{item['blocker_reason'] or '子用例未形成可判定结果'}" for item in blocked_children
                ),
                "human_reason": "；".join(f"{item['case_id']}：{item.get('human_reason') or item.get('blocker_reason')}" for item in failed_children + blocked_children),
                "next_action": "；".join(dict.fromkeys(item.get("next_action", "") for item in failed_children + blocked_children if item.get("next_action"))),
            }
        )
    return {
        "artifact_role": "original_case_result_aggregation",
        "aggregation_policy": {"failed_overrides_blocked": True, "blocked_overrides_not_run": True, "all_children_must_pass_for_parent_pass": True},
        "summary": {
            "original_case_count": len(output_rows),
            "passed": sum(row["status"] == "passed" for row in output_rows),
            "failed": sum(row["status"] == "failed" for row in output_rows),
            "blocked": sum(row["status"] == "blocked" for row in output_rows),
            "not_run": sum(row["status"] == "not_run" for row in output_rows),
        },
        "rows": output_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate normalized execution results back to original cases.")
    parser.add_argument("--final-cases", required=True)
    parser.add_argument("--execution-results", required=True)
    parser.add_argument("--existing-case-import", required=True)
    parser.add_argument("--output-yaml", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args()
    final_doc = load_yaml(Path(args.final_cases).resolve())
    execution_doc = load_yaml(Path(args.execution_results).resolve())
    import_doc = load_yaml(Path(args.existing_case_import).resolve())
    payload = aggregate(final_doc.get("test_cases") or [], execution_doc.get("execution_results") or [], import_doc.get("imported_cases") or [])
    yaml_path = Path(args.output_yaml).resolve()
    json_path = Path(args.output_json).resolve()
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
