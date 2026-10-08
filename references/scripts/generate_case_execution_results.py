from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from semantic_contract import semantic_contract_hash
from workflow_gate_common import (
    as_list,
    list_from,
    load_yaml,
    normalize_execution_status,
    sha256_file,
    text,
    verify_passed_gate,
)
from execution_quality import (
    P0_STOP_ACTUAL,
    P0_STOP_REASON,
    evaluate_p0_stop_gate,
    execution_metrics,
    execution_minimum_attempts,
    priority_sorted_case_ids,
    retry_decision,
)


def dump_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def duplicate_values(values: list[str]) -> list[str]:
    return sorted({value for value in values if value and values.count(value) > 1})


def not_run_result(
    *,
    index: int,
    case: dict[str, Any],
    mapping: dict[str, Any],
    semantic_hash: str,
    minimum_attempts: int,
    reason_code: str,
    actual: str,
    key_message: str,
) -> dict[str, Any]:
    return {
        "execution_id": f"CASE-{index:03d}",
        "case_id": text(case.get("case_id")),
        "script_id": text(mapping.get("script_id")),
        "test_node": text(mapping.get("test_node")),
        "status": "not_run",
        "semantic_contract_sha256": semantic_hash,
        "actual_result": actual,
        "result_summary": {
            "expected": "按 P0 -> P1 -> P2 顺序执行",
            "actual": actual,
            "key_message": key_message,
            "object_id": "",
        },
        "not_run_reason": reason_code,
        "attempts": [],
        "evidence": {},
        "stability": "not_run",
        "retry_policy": {
            "minimum_attempts": minimum_attempts,
            "required_attempts": 0,
            "third_attempt_required": False,
            "single_run_exempt": minimum_attempts == 1,
            "full_retry_upgraded": False,
            "priority": text(case.get("priority")).upper(),
            "reason": actual,
        },
        "telemetry": {},
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge raw script statuses into the gated, complete execution-result schema."
    )
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--script-status", required=True)
    parser.add_argument("--final-cases", required=True)
    parser.add_argument("--case-gate", required=True)
    parser.add_argument("--execution-plan", required=True)
    parser.add_argument("--execution-plan-gate", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    final_case_path = Path(args.final_cases).resolve()
    case_gate = verify_passed_gate(Path(args.case_gate).resolve(), final_case_path, "final_cases")
    if text(case_gate.get("gate")) != "case_design" or text(case_gate.get("contract_version")) != "2.0":
        raise SystemExit("case gate must be a contract_version 2.0 case_design gate")
    execution_plan_path = Path(args.execution_plan).resolve()
    plan_gate_path = Path(args.execution_plan_gate).resolve()
    plan_gate = verify_passed_gate(plan_gate_path, final_case_path, "final_cases")
    verify_passed_gate(plan_gate_path, execution_plan_path, "execution_plan")
    if text(plan_gate.get("gate")) != "execution_plan" or text(plan_gate.get("contract_version")) != "2.0":
        raise SystemExit("execution plan gate must be a contract_version 2.0 execution_plan gate")
    final_cases = list_from(load_yaml(final_case_path), ("test_cases",), "test_cases")
    plan_document = load_yaml(execution_plan_path)
    plan_meta = plan_document.get("execution_meta") or {}
    plan_items = list_from(plan_document, ("browser_execution_plan", "execution_plan"), "execution_plan")
    plans_by_id = {text(item.get("case_id")): item for item in plan_items}
    final_ids = [text(item.get("case_id")) for item in final_cases]
    cases_by_id = {text(item.get("case_id")): item for item in final_cases}
    final_position = {case_id: index for index, case_id in enumerate(final_ids, start=1)}

    mapping_data = load_yaml(Path(args.mapping).resolve())
    status_data = load_yaml(Path(args.script_status).resolve())
    mappings = list_from(mapping_data, ("case_execution_mapping",), "case_execution_mapping")
    mapping_ids = [text(item.get("case_id")) for item in mappings]
    duplicates = duplicate_values(mapping_ids)
    if duplicates:
        raise SystemExit(f"Duplicate mapping case IDs: {duplicates}")
    if set(mapping_ids) != set(final_ids) or len(mapping_ids) != len(final_ids):
        raise SystemExit("Mapping case IDs must exactly match the gated final cases")
    expected_mapping_ids = priority_sorted_case_ids(final_cases)
    if mapping_ids != expected_mapping_ids:
        raise SystemExit(
            "Mapping order must be stable P0 -> P1 -> P2; "
            f"expected={expected_mapping_ids}, actual={mapping_ids}"
        )

    status_by_node: dict[str, dict[str, Any]] = {}
    for item in as_list(status_data.get("script_execution_status")):
        if not isinstance(item, dict):
            continue
        node = text(item.get("test_node"))
        if not node:
            continue
        if node in status_by_node:
            raise SystemExit(f"Duplicate script status node: {node}")
        status_by_node[node] = item

    generated: list[dict[str, Any]] = []
    for mapping in mappings:
        case_id = text(mapping.get("case_id"))
        case = cases_by_id[case_id]
        index = final_position[case_id]
        plan = plans_by_id.get(case_id) or {}
        minimum_attempts, budget_errors = execution_minimum_attempts(case, plan, plan_meta)
        if budget_errors:
            raise SystemExit("; ".join(budget_errors))
        semantic_hash = semantic_contract_hash(case)
        test_node = text(mapping.get("test_node"))
        raw = status_by_node.get(test_node)
        p0_gate = evaluate_p0_stop_gate(final_cases, generated)
        stopped_before_case = text(case.get("priority")).upper() in {"P1", "P2"} and p0_gate["triggered"]
        if stopped_before_case:
            raw_attempts = [attempt for attempt in as_list((raw or {}).get("attempts")) if isinstance(attempt, dict)]
            raw_status = normalize_execution_status((raw or {}).get("status"))
            if raw_attempts or raw_status in {"passed", "failed", "blocked"}:
                raise SystemExit(
                    f"Case {case_id} has execution data after all P0 cases finally failed; rerun with priority batching"
                )
            result = not_run_result(
                index=index,
                case=case,
                mapping=mapping,
                semantic_hash=semantic_hash,
                minimum_attempts=minimum_attempts,
                reason_code=P0_STOP_REASON,
                actual=P0_STOP_ACTUAL,
                key_message=P0_STOP_REASON,
            )
        elif raw is None:
            actual = f"未找到测试节点 {test_node} 的执行记录"
            result = not_run_result(
                index=index,
                case=case,
                mapping=mapping,
                semantic_hash=semantic_hash,
                minimum_attempts=minimum_attempts,
                reason_code="script_status_missing",
                actual=actual,
                key_message="执行记录缺失",
            )
        else:
            status = normalize_execution_status(raw.get("status"))
            attempts = [attempt for attempt in as_list(raw.get("attempts")) if isinstance(attempt, dict)]
            priority = text(case.get("priority")).upper()
            decision = retry_decision(attempts, priority, minimum_attempts) if attempts else None
            if decision is not None:
                status = decision.final_status
            selected_attempt = decision.selected_attempt if decision is not None else raw.get("selected_attempt")
            selected = attempts[selected_attempt - 1] if selected_attempt and 0 < selected_attempt <= len(attempts) else None
            result = {
                "execution_id": text(raw.get("execution_id")) or f"CASE-{index:03d}",
                "case_id": case_id,
                "script_id": text(mapping.get("script_id")),
                "test_node": test_node,
                "status": status,
                "semantic_contract_sha256": text(raw.get("semantic_contract_sha256")) or semantic_hash,
                "execution_trace": raw.get("execution_trace") or {},
                "start_time": text(raw.get("start_time")),
                "end_time": text(raw.get("end_time") or raw.get("executed_at")),
                "actual_result": text(raw.get("actual_result")),
                "result_summary": raw.get("result_summary") or {},
                "selected_attempt": selected_attempt,
                "attempts": attempts,
                "produced_data": (selected or {}).get("produced_data") or (selected or {}).get("produced") or raw.get("produced_data") or raw.get("produced") or {},
                "observations": (selected or {}).get("observations") or raw.get("observations") or {},
                "evidence": raw.get("evidence") or {},
                "failed_step_no": raw.get("failed_step_no"),
                "blocker_type": text(raw.get("blocker_type")),
                "blocker_reason": text(raw.get("blocker_reason")),
                "not_run_reason": text(raw.get("not_run_reason")),
                "defect_suspected": bool(raw.get("defect_suspected")),
                "defect_summary": text(raw.get("defect_summary")),
                "defect": raw.get("defect") or {},
                "capability_probe": raw.get("capability_probe") or {},
                "stability": decision.stability if decision is not None else text(raw.get("stability")) or ("blocked" if status == "blocked" else "stable"),
                "retry_policy": {
                    "minimum_attempts": minimum_attempts,
                    "required_attempts": decision.required_attempts if decision is not None else len(attempts),
                    "third_attempt_required": decision.required_attempts == 3 if decision is not None else len(attempts) >= 3,
                    "single_run_exempt": minimum_attempts == 1,
                    "full_retry_upgraded": minimum_attempts == 1 and bool(decision and decision.required_attempts == 3),
                    "priority": priority,
                    "reason": decision.reason if decision is not None else "",
                },
                "telemetry": raw.get("telemetry") or {},
            }
        generated.append(result)

    generated_by_id = {text(item.get("case_id")): item for item in generated}
    generated = [generated_by_id[case_id] for case_id in final_ids]
    counts: Counter[str] = Counter(normalize_execution_status(item.get("status")) for item in generated)
    priority_gate = evaluate_p0_stop_gate(final_cases, generated)
    metrics = execution_metrics(generated)
    plan_resource_budget = plan_meta.get("resource_budget") or {}
    raw_resource_budget = status_data.get("resource_budget") or {}
    resource_budget = {
        "estimated_attempts": plan_resource_budget.get("estimated_attempts"),
        "estimated_minutes": plan_resource_budget.get("estimated_minutes"),
        "actual_attempts": metrics["total_attempts"],
        "actual_minutes": raw_resource_budget.get("actual_minutes"),
    }
    dump_yaml(
        Path(args.output).resolve(),
        {
            "execution_meta": {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "final_cases_path": final_case_path.name,
                "final_cases_sha256": sha256_file(final_case_path),
                "execution_plan_path": execution_plan_path.name,
                "execution_plan_sha256": sha256_file(execution_plan_path),
                "execution_mode": text(plan_meta.get("execution_mode")).lower(),
                "priority_execution_policy": plan_meta.get("priority_execution_policy") or {},
                "priority_gate": priority_gate,
                "retry_policy_version": "3.0",
                "retry_rule": "按P0/P1/P2分批；先完成全部P0最终裁决；仅当全部P0最终均为failed时停止P1/P2；任一P0为passed或blocked则继续；资格执行全量双跑；首轮未稳定通过则三遍裁决",
                "evidence_manifest_version": "1.0",
                "scope_matrix": status_data.get("scope_matrix") or [],
                "resource_budget": resource_budget,
                "input_sha256": plan_meta.get("input_sha256") or {},
            },
            "execution_results": generated,
            "summary": {
                "total": len(generated),
                "passed": counts["passed"],
                "failed": counts["failed"],
                "blocked": counts["blocked"],
                "not_run": counts["not_run"],
                **metrics,
            },
        },
    )
    print(f"Generated gated execution-result candidate: {args.output} ({len(generated)} cases)")
    print("Run validate_execution_delivery.py --phase execution before backfill.")


if __name__ == "__main__":
    main()
