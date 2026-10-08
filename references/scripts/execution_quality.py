from __future__ import annotations

"""Reusable execution-quality policies shared by runners and delivery gates."""

import time
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Iterable


VALID_ATTEMPT_STATUSES = {"passed", "failed", "blocked"}
MAX_ATTEMPTS = 3
VALID_EXECUTION_MODES = {"qualification", "risk_based_regression"}
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
PRIORITY_ORDER = ("P0", "P1", "P2")
P0_STOP_REASON = "p0_all_failed_stop_gate"
P0_STOP_ACTUAL = "全部 P0 用例最终均失败，按优先级停止门禁未启动本用例"
PRIORITY_EXECUTION_POLICY = {
    "version": "1.1",
    "order": list(PRIORITY_ORDER),
    "complete_p0_before_lower_priorities": True,
    "stop_when_all_p0_failed": True,
    "blocked_triggers_stop": False,
    "unstarted_status": "not_run",
    "not_run_reason_code": P0_STOP_REASON,
}


@dataclass(frozen=True)
class RetryDecision:
    final_status: str
    selected_attempt: int | None
    required_attempts: int
    stability: str
    reason: str


def priority_sorted_cases(cases: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a stable P0 -> P1 -> P2 ordering for the canonical cases."""
    order = {priority: index for index, priority in enumerate(PRIORITY_ORDER)}
    rows = list(cases)
    return sorted(
        rows,
        key=lambda item: order.get(str(item.get("priority") or "").strip().upper(), len(order)),
    )


def priority_sorted_case_ids(cases: Iterable[dict[str, Any]]) -> list[str]:
    return [str(case.get("case_id") or "").strip() for case in priority_sorted_cases(cases)]


def validate_priority_execution_policy(value: Any, label: str = "priority_execution_policy") -> list[str]:
    if not isinstance(value, dict):
        return [f"{label} must be a mapping"]
    errors: list[str] = []
    for field, expected in PRIORITY_EXECUTION_POLICY.items():
        if value.get(field) != expected:
            errors.append(f"{label}.{field} must be {expected!r}")
    return errors


def evaluate_p0_stop_gate(
    cases: Iterable[dict[str, Any]],
    results: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate the product-result stop gate only after the full P0 batch."""
    case_rows = list(cases)
    result_by_id = {
        str(item.get("case_id") or "").strip(): item
        for item in results
        if isinstance(item, dict)
    }
    p0_ids = [
        str(case.get("case_id") or "").strip()
        for case in case_rows
        if str(case.get("priority") or "").strip().upper() == "P0"
    ]
    p0_statuses = [_status((result_by_id.get(case_id) or {}).get("status")) for case_id in p0_ids]
    completed_statuses = {"passed", "failed", "blocked"}
    p0_completed = sum(status in completed_statuses for status in p0_statuses)
    p0_failed = sum(status == "failed" for status in p0_statuses)
    p0_blocked = sum(status == "blocked" for status in p0_statuses)
    triggered = (
        bool(p0_ids)
        and p0_completed == len(p0_ids)
        and p0_failed == len(p0_ids)
    )
    lower_cases = [
        case
        for case in case_rows
        if str(case.get("priority") or "").strip().upper() in {"P1", "P2"}
    ]
    skipped_priorities = [
        priority
        for priority in PRIORITY_ORDER[1:]
        if any(str(case.get("priority") or "").strip().upper() == priority for case in lower_cases)
    ]
    return {
        "evaluated_after_priority": "P0",
        "p0_total": len(p0_ids),
        "p0_completed": p0_completed,
        "p0_failed": p0_failed,
        "p0_blocked": p0_blocked,
        "triggered": triggered,
        "reason_code": P0_STOP_REASON if triggered else "",
        "skipped_priorities": skipped_priorities if triggered else [],
        "not_run_case_ids": [
            str(case.get("case_id") or "").strip() for case in lower_cases
        ] if triggered else [],
    }


def valid_sha256(value: Any) -> bool:
    return bool(SHA256_PATTERN.fullmatch(str(value or "").strip()))


def validate_input_hashes(value: Any, label: str = "input_sha256") -> list[str]:
    if not isinstance(value, dict) or not value:
        return [f"{label} must be a non-empty mapping"]
    errors: list[str] = []
    for key, digest in value.items():
        if not str(key or "").strip() or not valid_sha256(digest):
            errors.append(f"{label}.{key or '<blank>'} must be a SHA-256 digest")
    return errors


def execution_minimum_attempts(
    case: dict[str, Any],
    plan: dict[str, Any],
    plan_meta: dict[str, Any],
) -> tuple[int, list[str]]:
    """Validate and return the gated minimum attempt budget for one case."""
    case_id = str(case.get("case_id") or "").strip()
    priority = str(case.get("priority") or "").strip().upper()
    mode = str(plan_meta.get("execution_mode") or "").strip().lower()
    budget = plan.get("execution_budget")
    if not isinstance(budget, dict):
        return 2, [f"Case {case_id} execution_budget must be a mapping"]
    minimum = budget.get("minimum_attempts")
    errors: list[str] = []
    if minimum not in {1, 2}:
        errors.append(f"Case {case_id} execution_budget.minimum_attempts must be 1 or 2")
        minimum = 2
    exemption = budget.get("single_run_exemption")
    if not isinstance(exemption, dict):
        errors.append(f"Case {case_id} execution_budget.single_run_exemption must be a mapping")
        exemption = {}
    applied = exemption.get("applied") is True

    if mode == "qualification":
        if minimum != 2 or applied:
            errors.append(f"Case {case_id} qualification mode requires two attempts and forbids single-run exemption")
        return 2, errors
    if mode != "risk_based_regression":
        errors.append(f"execution_meta.execution_mode must be one of: {', '.join(sorted(VALID_EXECUTION_MODES))}")
        return 2, errors
    if priority in {"P0", "P1"}:
        if minimum != 2 or applied:
            errors.append(f"Case {case_id} {priority} must run at least twice in risk-based regression")
        return 2, errors
    if priority != "P2":
        errors.append(f"Case {case_id} has unsupported priority for execution budgeting: {priority}")
        return 2, errors
    if not applied:
        if minimum != 2:
            errors.append(f"Case {case_id} P2 without an applied exemption must run at least twice")
        return 2, errors
    if minimum != 1:
        errors.append(f"Case {case_id} applied P2 exemption must set minimum_attempts to 1")
    current_hashes = plan_meta.get("input_sha256")
    baseline_hashes = exemption.get("baseline_input_sha256")
    if baseline_hashes != current_hashes:
        errors.append(f"Case {case_id} P2 exemption baseline_input_sha256 must equal current input_sha256")
    errors.extend(validate_input_hashes(baseline_hashes, f"Case {case_id} baseline_input_sha256"))
    historical_attempts = exemption.get("historical_attempts")
    historical_passed = exemption.get("historical_passed")
    if not isinstance(historical_attempts, int) or isinstance(historical_attempts, bool) or historical_attempts < 2:
        errors.append(f"Case {case_id} P2 exemption requires at least two historical attempts")
    if historical_passed != historical_attempts:
        errors.append(f"Case {case_id} P2 exemption requires every historical attempt to pass")
    if str(exemption.get("historical_stability") or "").strip().lower() != "stable":
        errors.append(f"Case {case_id} P2 exemption requires historical_stability: stable")
    if exemption.get("requirement_affected") is not False:
        errors.append(f"Case {case_id} P2 exemption requires requirement_affected: false")
    if not valid_sha256(exemption.get("baseline_execution_results_sha256")):
        errors.append(f"Case {case_id} P2 exemption requires baseline_execution_results_sha256")
    if not str(exemption.get("rationale") or "").strip():
        errors.append(f"Case {case_id} P2 exemption requires a rationale")
    return 1, errors


def _status(value: Any) -> str:
    raw = str(value or "").strip().lower()
    aliases = {"pass": "passed", "fail": "failed", "阻塞": "blocked", "通过": "passed", "失败": "failed"}
    return aliases.get(raw, raw)


def retry_decision(
    attempts: Iterable[dict[str, Any]],
    priority: str = "P1",
    minimum_attempts: int = 2,
) -> RetryDecision:
    """Return the only allowed final decision for a sequence of attempts.

    The first two attempts are mandatory unless a gated P2 risk-regression
    exemption sets ``minimum_attempts`` to 1. A mixed pair is deliberately not
    accepted as a final pass: it gets a third attempt and is marked unstable.
    For P0, a pass requires two passing attempts out of the three when a third
    attempt was needed. If that cannot be demonstrated, the result is blocked
    rather than silently promoted to passed.
    """
    if minimum_attempts not in {1, 2}:
        return RetryDecision("blocked", None, 2, "blocked", "minimum_attempts 只能为 1 或 2")

    statuses = [_status(item.get("status")) for item in attempts]
    if len(statuses) > MAX_ATTEMPTS:
        return RetryDecision("blocked", None, MAX_ATTEMPTS, "blocked", "执行遍数超过上限")
    if any(value not in VALID_ATTEMPT_STATUSES for value in statuses):
        return RetryDecision("blocked", None, minimum_attempts, "blocked", "执行记录包含无效状态")

    if minimum_attempts == 1 and statuses[:1] == ["passed"]:
        if len(statuses) == 1:
            return RetryDecision("passed", 1, 1, "qualified_single_run", "P2 风险回归单跑资格生效")
        # An executor that continued after the exempt pass is adjudicated by
        # the full policy, so extra contradictory evidence cannot be ignored.
        minimum_attempts = 2

    if minimum_attempts == 1 and len(statuses) < 3:
        return RetryDecision("blocked", None, 3, "blocked", "P2 单跑失败或阻塞，必须升级完整三遍重试")

    if len(statuses) < 2:
        return RetryDecision("blocked", None, 2, "blocked", "首两遍执行记录不完整")

    first_two = statuses[:2]
    needs_third = first_two != ["passed", "passed"]
    required = 3 if needs_third else 2
    if len(statuses) < required:
        return RetryDecision("blocked", None, required, "blocked", "未达到本用例要求的执行遍数")
    used = statuses[:required]
    if not needs_third and len(statuses) == 2:
        return RetryDecision("passed", 1, 2, "stable", "首两遍均通过")

    # A correctly behaving runner stops after the stable pair. If a third
    # attempt nevertheless exists, its evidence is part of the audit record
    # and must not be ignored. P0 keeps the strict-majority rule; for other
    # priorities the recorded third attempt is the adjudication attempt.
    if not needs_third:
        third = statuses[2]
        priority_value = str(priority or "").strip().upper()
        if third == "passed":
            return RetryDecision("passed", 3, 3, "stable", "三遍均通过")
        if priority_value == "P0":
            return RetryDecision("passed", 2, 3, "unstable", "P0 三遍中已有两遍通过，但额外第三遍出现反证")
        return RetryDecision(
            third,
            3,
            3,
            "blocked" if third == "blocked" else "unstable",
            "首两遍通过后仍记录第三遍，已按第三遍反证裁决",
        )

    third = used[2]
    passed_count = used.count("passed")
    failed_count = used.count("failed")
    priority_value = str(priority or "").strip().upper()
    if priority_value == "P0":
        if passed_count >= 2:
            selected = next(index + 1 for index in range(2, -1, -1) if used[index] == "passed")
            return RetryDecision("passed", selected, 3, "unstable", "P0 已达到三遍中至少两遍通过")
        if failed_count >= 2:
            selected = next(index + 1 for index in range(2, -1, -1) if used[index] == "failed")
            return RetryDecision("failed", selected, 3, "unstable", "P0 三遍中至少两遍失败")
        return RetryDecision("blocked", 3, 3, "blocked", "P0 未形成两遍一致的可靠结论")

    # The third run is the adjudication run for non-P0 cases. Its contract
    # evidence remains the source of the final result. An early blocked run can
    # therefore recover, but a blocked third run remains blocked.
    return RetryDecision(
        third,
        3,
        3,
        "blocked" if third == "blocked" else "unstable",
        "首两遍未形成稳定通过，已按第三遍证据裁决",
    )


def execution_metrics(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(results)
    statuses = Counter(_status(item.get("status")) for item in rows)
    attempts = [attempt for item in rows for attempt in (item.get("attempts") or []) if isinstance(attempt, dict)]
    first_statuses = [
        _status((item.get("attempts") or [])[0].get("status"))
        for item in rows
        if isinstance(item.get("attempts"), list) and item.get("attempts") and isinstance(item["attempts"][0], dict)
    ]
    first_passed = sum(value == "passed" for value in first_statuses)
    executed = statuses["passed"] + statuses["failed"] + statuses["blocked"]
    unstable = sum(str(item.get("stability") or "").lower() == "unstable" for item in rows)
    single_run_exempt = sum(
        str(item.get("stability") or "").lower() == "qualified_single_run" for item in rows
    )
    third_runs = sum(len(item.get("attempts") or []) >= 3 for item in rows)
    full_retry_upgrades = sum(
        bool((item.get("retry_policy") or {}).get("full_retry_upgraded"))
        for item in rows
        if isinstance(item.get("retry_policy") or {}, dict)
    )
    meta = {}
    for item in rows:
        item_meta = item.get("telemetry") or {}
        if isinstance(item_meta, dict):
            for key, value in item_meta.items():
                if isinstance(value, (int, float)):
                    meta[key] = meta.get(key, 0) + value
    return {
        "total_cases": len(rows),
        "executed_cases": executed,
        "passed": statuses["passed"],
        "failed": statuses["failed"],
        "blocked": statuses["blocked"],
        "not_run": statuses["not_run"],
        "total_attempts": len(attempts),
        "third_attempt_cases": third_runs,
        "unstable_cases": unstable,
        "single_run_exempt_cases": single_run_exempt,
        "full_retry_upgrades": full_retry_upgrades,
        "first_passed": first_passed,
        "first_pass_rate": round(first_passed * 100.0 / len(first_statuses), 2) if first_statuses else 0.0,
        "final_pass_rate_including_blocked": round(statuses["passed"] * 100.0 / len(rows), 2) if rows else 0.0,
        "final_pass_rate_judged_only": round(statuses["passed"] * 100.0 / (statuses["passed"] + statuses["failed"]), 2)
        if statuses["passed"] + statuses["failed"] else 0.0,
        "telemetry": meta,
    }


def wait_for_business_state(
    read_state: Callable[[], Any],
    predicate: Callable[[Any], bool],
    *,
    timeout_seconds: float = 30.0,
    initial_interval_seconds: float = 0.5,
    max_interval_seconds: float = 4.0,
    refresh: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Poll a business state with refresh and exponential backoff.

    The returned timeline is persisted with the attempt so a stale read can
    be distinguished from a product state-transition failure.
    """
    started = time.monotonic()
    interval = max(0.05, initial_interval_seconds)
    timeline: list[dict[str, Any]] = []
    while True:
        state = read_state()
        elapsed = round(time.monotonic() - started, 3)
        timeline.append({"elapsed_seconds": elapsed, "state": state})
        if predicate(state):
            return {"matched": True, "elapsed_seconds": elapsed, "timeline": timeline, "state": state}
        if elapsed >= timeout_seconds:
            return {"matched": False, "elapsed_seconds": elapsed, "timeline": timeline, "state": state}
        if refresh is not None:
            refresh()
        time.sleep(min(interval, max(0.0, timeout_seconds - elapsed)))
        interval = min(max_interval_seconds, interval * 2)
