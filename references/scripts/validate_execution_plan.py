from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from semantic_contract import (
    normalized_contract_observations,
    normalized_execution_contract,
    normalized_test_intent,
    semantic_contract_hash,
)
from execution_quality import (
    VALID_EXECUTION_MODES,
    execution_minimum_attempts,
    priority_sorted_case_ids,
    validate_input_hashes,
    validate_priority_execution_policy,
)
from workflow_gate_common import as_list, list_from, load_yaml, sha256_file, source_record, text, verify_passed_gate, write_json


def plan_items(document: dict[str, Any]) -> list[dict[str, Any]]:
    return list_from(document, ("browser_execution_plan", "execution_plan"), "execution_plan")


def required_screenshot_scope(case: dict[str, Any]) -> str:
    intent = normalized_test_intent(case)
    operation_type = text(case.get("operation_type")).lower()
    if intent["kind"] == "field_validation" or operation_type == "persisted_validation":
        return "assertion_target_container"
    return "assertion_state_view"


def requires_change_evidence(case: dict[str, Any]) -> bool:
    change = case.get("change_evidence") or {}
    return isinstance(change, dict) and change.get("required") is True


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate semantic routing before any test case executes.")
    parser.add_argument("--case-gate", required=True)
    parser.add_argument("--final-cases", required=True)
    parser.add_argument("--execution-plan", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    case_gate_path = Path(args.case_gate).resolve()
    final_case_path = Path(args.final_cases).resolve()
    plan_path = Path(args.execution_plan).resolve()
    output_path = Path(args.output).resolve()
    errors: list[str] = []
    warnings: list[str] = []

    try:
        case_gate = verify_passed_gate(case_gate_path, final_case_path, "final_cases")
        case_doc = load_yaml(final_case_path)
        plan_doc = load_yaml(plan_path)
        cases = list_from(case_doc, ("test_cases",), "test_cases")
        plans = plan_items(plan_doc)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        payload = {"gate": "execution_plan", "status": "fail", "errors": [str(exc)], "warnings": []}
        write_json(output_path, payload)
        print(str(exc), file=sys.stderr)
        return 1

    if text(case_gate.get("gate")) != "case_design" or text(case_gate.get("contract_version")) != "2.0":
        errors.append("case gate must be a contract_version 2.0 case_design gate")

    expected_case_hash = sha256_file(final_case_path)
    plan_meta = plan_doc.get("execution_meta") or {}
    if text(plan_meta.get("final_cases_sha256")) != expected_case_hash:
        errors.append("execution plan final_cases_sha256 is missing or does not match the gated final cases")
    execution_mode = text(plan_meta.get("execution_mode")).lower()
    if execution_mode not in VALID_EXECUTION_MODES:
        errors.append(
            "execution plan execution_meta.execution_mode must be qualification or risk_based_regression"
        )
    errors.extend(validate_input_hashes(plan_meta.get("input_sha256"), "execution plan execution_meta.input_sha256"))
    browser_runtime = plan_meta.get("browser_runtime") or {}
    if not isinstance(browser_runtime, dict):
        errors.append("execution plan execution_meta.browser_runtime must be a mapping")
        browser_runtime = {}
    else:
        if not text(browser_runtime.get("browser")):
            errors.append("execution plan execution_meta.browser_runtime.browser must not be blank")
        if not isinstance(browser_runtime.get("headless"), bool):
            errors.append("execution plan execution_meta.browser_runtime.headless must be boolean")
        expected_mode = "headless" if browser_runtime.get("headless") is True else "headed"
        if text(browser_runtime.get("mode")).lower() != expected_mode:
            errors.append(f"execution plan execution_meta.browser_runtime.mode must be {expected_mode}")
        if not text(browser_runtime.get("config_source")):
            errors.append("execution plan execution_meta.browser_runtime.config_source must not be blank")
    errors.extend(
        validate_priority_execution_policy(
            plan_meta.get("priority_execution_policy"),
            "execution plan execution_meta.priority_execution_policy",
        )
    )
    budget = plan_meta.get("resource_budget")
    if not isinstance(budget, dict):
        errors.append("execution plan execution_meta.resource_budget must be a mapping")
    else:
        for field in ("estimated_attempts", "estimated_minutes"):
            if not isinstance(budget.get(field), (int, float)) or budget.get(field) < 0:
                errors.append(f"execution plan resource_budget.{field} must be a non-negative number")

    case_ids = [text(case.get("case_id")) for case in cases]
    plan_ids = [text(item.get("case_id")) for item in plans]
    if len(plan_ids) != len(set(plan_ids)):
        errors.append("Execution plan case IDs must not contain duplicates")
    if set(plan_ids) != set(case_ids) or len(plan_ids) != len(case_ids):
        missing = sorted(set(case_ids) - set(plan_ids))
        extra = sorted(set(plan_ids) - set(case_ids))
        errors.append(f"Execution plan case IDs must exactly match final cases; missing={missing}, extra={extra}")
    expected_plan_ids = priority_sorted_case_ids(cases)
    if plan_ids != expected_plan_ids:
        errors.append(
            "Execution plan order must be stable P0 -> P1 -> P2; "
            f"expected={expected_plan_ids}, actual={plan_ids}"
        )

    cases_by_id = {text(case.get("case_id")): case for case in cases}
    semantic_hashes: dict[str, str] = {}
    minimum_attempts_by_case: dict[str, int] = {}
    estimated_minutes_total = 0.0
    for item in plans:
        case_id = text(item.get("case_id"))
        case = cases_by_id.get(case_id)
        if case is None:
            continue
        expected_hash = semantic_contract_hash(case)
        semantic_hashes[case_id] = expected_hash
        if text(item.get("semantic_contract_sha256")) != expected_hash:
            errors.append(f"Case {case_id} execution plan has a stale or missing semantic contract hash")

        expected_execution = normalized_execution_contract(case)
        expected_intent = normalized_test_intent(case)
        minimum_attempts, budget_errors = execution_minimum_attempts(case, item, plan_meta)
        minimum_attempts_by_case[case_id] = minimum_attempts
        errors.extend(budget_errors)
        if text(item.get("execution_profile")) != text(case.get("execution_profile")):
            errors.append(f"Case {case_id} execution_profile does not match final case")
        if text(item.get("executor_capability")).lower() != expected_execution["capability"]:
            errors.append(f"Case {case_id} executor_capability does not match final case")
        if text(item.get("primary_channel")).lower() != expected_execution["primary_channel"]:
            errors.append(f"Case {case_id} primary_channel does not match final case")
        runtime_env = item.get("runtime_env") or {}
        if not isinstance(runtime_env, dict):
            errors.append(f"Case {case_id} runtime_env must be a mapping")
        else:
            if text(runtime_env.get("browser")).lower() != text(browser_runtime.get("browser")).lower():
                errors.append(f"Case {case_id} runtime_env.browser must match execution_meta.browser_runtime")
            if runtime_env.get("headless") is not browser_runtime.get("headless"):
                errors.append(f"Case {case_id} runtime_env.headless must match execution_meta.browser_runtime")
            if not text(runtime_env.get("config_source")):
                errors.append(f"Case {case_id} runtime_env.config_source must not be blank")

        intent_snapshot = item.get("intent_snapshot") or {}
        normalized_plan_intent = {
            "kind": text(intent_snapshot.get("kind")).lower(),
            "polarity": text(intent_snapshot.get("polarity")).lower(),
            "interface": text(intent_snapshot.get("interface")).lower(),
            "field_id": text(intent_snapshot.get("field_id")),
            "input_class": text(intent_snapshot.get("input_class")),
            "expected_outcome": text(intent_snapshot.get("expected_outcome")).lower(),
        }
        if normalized_plan_intent != expected_intent:
            errors.append(f"Case {case_id} intent_snapshot does not match final case")

        contract_observations = normalized_contract_observations(case)
        expected_observation_keys = {observation["key"] for observation in contract_observations}
        expected_ui_evidence_keys = {
            observation["key"]
            for observation in contract_observations
            if observation.get("source") == "ui" and observation.get("evidence_required") is True
        }
        expected_api_evidence_keys = {
            observation["key"]
            for observation in contract_observations
            if observation.get("source") == "api" and observation.get("evidence_required") is True
        }
        planned_observation_keys = {
            text(value)
            for value in as_list(item.get("planned_observation_keys"))
            if text(value)
        }
        if planned_observation_keys != expected_observation_keys:
            errors.append(f"Case {case_id} planned_observation_keys do not match result contract")

        evidence_plan = item.get("evidence_plan") or {}
        if not isinstance(evidence_plan, dict):
            errors.append(f"Case {case_id} evidence_plan must be a mapping")
        else:
            semantic_keys = {text(value) for value in as_list(evidence_plan.get("semantic_keys")) if text(value)}
            if semantic_keys != expected_ui_evidence_keys:
                errors.append(f"Case {case_id} evidence_plan.semantic_keys must match UI evidence observations")
            api_semantic_keys = {
                text(value) for value in as_list(evidence_plan.get("api_semantic_keys")) if text(value)
            }
            if api_semantic_keys != expected_api_evidence_keys:
                errors.append(f"Case {case_id} evidence_plan.api_semantic_keys must match API evidence observations")
            for field in ("assertion_moment", "target", "browser", "verification_method"):
                if not text(evidence_plan.get(field)):
                    errors.append(f"Case {case_id} evidence_plan must declare {field}")
            if expected_ui_evidence_keys and not as_list(evidence_plan.get("content_anchors")):
                errors.append(f"Case {case_id} evidence_plan.content_anchors must not be empty")
            expected_scope = required_screenshot_scope(case)
            if expected_ui_evidence_keys and text(evidence_plan.get("screenshot_scope")) != expected_scope:
                errors.append(
                    f"Case {case_id} evidence_plan.screenshot_scope must be {expected_scope}"
                )
            if expected_ui_evidence_keys and text(evidence_plan.get("attempt_binding")) != "selected_attempt_only":
                errors.append(
                    f"Case {case_id} evidence_plan.attempt_binding must be selected_attempt_only"
                )
            if requires_change_evidence(case):
                if evidence_plan.get("before_after") is not True:
                    errors.append(f"Case {case_id} change evidence requires evidence_plan.before_after: true")
                attempt_screenshots = {Path(text(value)).name for value in as_list(evidence_plan.get("attempt_screenshots")) if text(value)}
                required_names = {
                    f"{case_id}_A1_before.png", f"{case_id}_A1_after.png",
                    f"{case_id}_A2_before.png", f"{case_id}_A2_after.png",
                }
                if not required_names.issubset(attempt_screenshots):
                    errors.append(
                        f"Case {case_id} change evidence plan must include before/after screenshots for attempts 1 and 2"
                    )

        blocker_probe_plan = item.get("blocker_probe_plan") or {}
        if not isinstance(blocker_probe_plan, dict) or not as_list(blocker_probe_plan.get("channels")):
            errors.append(f"Case {case_id} blocker_probe_plan.channels must list available UI/API/setup probes")
        elif not text(blocker_probe_plan.get("probe_log_path")):
            errors.append(f"Case {case_id} blocker_probe_plan.probe_log_path must not be blank")

        state_wait = item.get("state_wait_policy") or {}
        if not isinstance(state_wait, dict):
            errors.append(f"Case {case_id} state_wait_policy must be a mapping")
        else:
            if state_wait.get("refresh_before_poll") is not True:
                errors.append(f"Case {case_id} state_wait_policy.refresh_before_poll must be true")
            for field in ("timeout_seconds", "initial_interval_seconds", "max_interval_seconds"):
                if not isinstance(state_wait.get(field), (int, float)) or state_wait.get(field) <= 0:
                    errors.append(f"Case {case_id} state_wait_policy.{field} must be a positive number")

        if "prompt_text" in expected_observation_keys and expected_intent["interface"] == "ui":
            feedback_wait = item.get("feedback_wait_policy") or {}
            if not isinstance(feedback_wait, dict):
                errors.append(f"Case {case_id} feedback_wait_policy must be a mapping")
            else:
                if feedback_wait.get("baseline_before_action") is not True:
                    errors.append(f"Case {case_id} feedback_wait_policy.baseline_before_action must be true")
                presentations = {
                    text(value).lower()
                    for value in as_list(feedback_wait.get("presentations"))
                    if text(value)
                }
                required_presentations = {"modal", "toast", "inline", "banner"}
                if not required_presentations.issubset(presentations):
                    errors.append(
                        f"Case {case_id} feedback_wait_policy.presentations must cover modal/toast/inline/banner"
                    )
                timeout_ms = feedback_wait.get("timeout_ms")
                if not isinstance(timeout_ms, (int, float)) or timeout_ms < 2000:
                    errors.append(f"Case {case_id} feedback_wait_policy.timeout_ms must be at least 2000")
                poll_interval_ms = feedback_wait.get("poll_interval_ms")
                if (
                    not isinstance(poll_interval_ms, (int, float))
                    or poll_interval_ms < 20
                    or poll_interval_ms > 250
                ):
                    errors.append(
                        f"Case {case_id} feedback_wait_policy.poll_interval_ms must be between 20 and 250"
                    )
                for field in ("screenshot_on_detection", "classify_after_capture"):
                    if feedback_wait.get(field) is not True:
                        errors.append(f"Case {case_id} feedback_wait_policy.{field} must be true")

        if expected_intent["kind"] == "field_validation" and expected_intent["interface"] == "ui":
            locator_audit = item.get("locator_audit") or {}
            required_locator_fields = ("label", "field_name", "input_type", "editable", "nearest_container")
            if not isinstance(locator_audit, dict) or any(not text(locator_audit.get(field)) for field in required_locator_fields):
                errors.append(
                    f"Case {case_id} locator_audit must declare label/field_name/input_type/editable/nearest_container"
                )
        if not isinstance(item.get("estimated_minutes"), (int, float)) or item.get("estimated_minutes") <= 0:
            errors.append(f"Case {case_id} estimated_minutes must be a positive number")
        else:
            estimated_minutes_total += float(item.get("estimated_minutes"))

        if not isinstance(item.get("setup_steps"), list):
            errors.append(f"Case {case_id} setup_steps must be a list, even when empty")
        else:
            for step in item.get("setup_steps"):
                if not isinstance(step, dict):
                    errors.append(f"Case {case_id} setup step must be a mapping")
                    continue
                if text(step.get("phase")).lower() != "setup":
                    errors.append(f"Case {case_id} setup step must declare phase: setup")
                if not text(step.get("semantic_action")):
                    errors.append(f"Case {case_id} setup step must declare semantic_action")
        raw_core_steps = as_list(item.get("core_steps"))
        if any(not isinstance(step, dict) for step in raw_core_steps):
            errors.append(f"Case {case_id} core steps must be mappings")
        core_steps = [step for step in raw_core_steps if isinstance(step, dict)]
        if not core_steps:
            errors.append(f"Case {case_id} core_steps must not be empty")
        semantic_actions: set[str] = set()
        for step in core_steps:
            if text(step.get("phase")).lower() != "core":
                errors.append(f"Case {case_id} core step must declare phase: core")
            if text(step.get("channel")).lower() != expected_execution["primary_channel"]:
                errors.append(f"Case {case_id} core step channel must match primary_channel")
            semantic_action = text(step.get("semantic_action")).lower()
            if not semantic_action:
                errors.append(f"Case {case_id} core step must declare semantic_action")
            semantic_actions.add(semantic_action)

        if expected_intent["kind"] == "field_validation":
            required_actions = {"input_test_value", "observe_outcome"}
            if expected_intent["expected_outcome"] == "rejected":
                required_actions.add("attempt_commit")
        else:
            required_actions = {"perform_operation", "observe_outcome"}
        persistence = case.get("persistence_verification") or {}
        if isinstance(persistence, dict) and persistence.get("required") is True:
            required_actions.discard("observe_outcome")
            required_actions.update({"attempt_commit", "observe_persisted_outcome"})
        missing_actions = sorted(required_actions - semantic_actions)
        if missing_actions:
            errors.append(
                f"Case {case_id} plan is missing semantic actions: {', '.join(missing_actions)}"
            )
        if expected_intent["expected_outcome"] == "rejected":
            forbidden_actions = {"create_valid_resource", "commit_valid_resource"}
            exposed_forbidden = sorted(forbidden_actions.intersection(semantic_actions))
            if exposed_forbidden:
                errors.append(
                    f"Case {case_id} rejected plan contains forbidden core actions: "
                    f"{', '.join(exposed_forbidden)}"
                )

    if isinstance(budget, dict):
        expected_attempts = sum(minimum_attempts_by_case.values())
        if budget.get("estimated_attempts") != expected_attempts:
            errors.append(
                f"execution plan resource_budget.estimated_attempts must equal gated base attempts {expected_attempts}"
            )
        declared_minutes = budget.get("estimated_minutes")
        if not isinstance(declared_minutes, (int, float)) or float(declared_minutes) != estimated_minutes_total:
            errors.append(
                f"execution plan resource_budget.estimated_minutes must equal per-case total {estimated_minutes_total:g}"
            )

    payload = {
        "gate": "execution_plan",
        "status": "fail" if errors else "pass",
        "contract_version": "2.0",
        "inputs": {
            "case_gate": source_record(case_gate_path),
            "final_cases": source_record(final_case_path),
            "execution_plan": source_record(plan_path),
        },
        "counts": {"final_cases": len(cases), "planned_cases": len(plans)},
        "semantic_contracts": semantic_hashes,
        "execution_budget": {
            "mode": execution_mode,
            "minimum_attempts_by_case": minimum_attempts_by_case,
            "single_run_exempt_cases": sum(value == 1 for value in minimum_attempts_by_case.values()),
        },
        "errors": errors,
        "warnings": warnings,
    }
    write_json(output_path, payload)
    print(f"execution-plan gate: {payload['status']} ({len(errors)} errors, {len(warnings)} warnings)")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
