from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from workflow_gate_common import as_list, text


CONTRACT_VERSION = "2.0"
VALID_POLARITIES = {"positive", "negative"}
VALID_INTERFACES = {"ui", "api"}
VALID_INTENT_KINDS = {"scenario", "field_validation"}
VALID_OUTCOMES = {"accepted", "rejected", "committed", "observed"}
VALID_CHANNELS = {"ui", "api", "database", "file", "runtime"}
VALID_OPERATORS = {
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "non_empty",
    "empty",
    "matches",
    "greater_than",
    "greater_or_equal",
    "less_than",
    "less_or_equal",
}
VALID_ASSERTION_CLASSES = {"hard", "soft"}


def lower_text(value: Any) -> str:
    return text(value).lower()


def normalized_test_intent(case: dict[str, Any]) -> dict[str, Any]:
    intent = case.get("test_intent") or {}
    return {
        "kind": lower_text(intent.get("kind")),
        "polarity": lower_text(intent.get("polarity")),
        "interface": lower_text(intent.get("interface")),
        "field_id": text(intent.get("field_id")),
        "input_class": text(intent.get("input_class")),
        "expected_outcome": lower_text(intent.get("expected_outcome")),
    }


def requires_persistence_verification(
    case: dict[str, Any], *, point_requires: bool = False
) -> bool:
    """Return whether the case explicitly carries a field-persistence contract."""
    intent = normalized_test_intent(case)
    operation_type = lower_text(case.get("operation_type"))
    policy = case.get("persistence_verification") or {}
    policy_requires = isinstance(policy, dict) and policy.get("required") is True
    return (
        operation_type == "persisted_validation"
        or (intent["kind"] == "field_validation" and operation_type == "edit")
        or point_requires
        or policy_requires
    )


def is_field_edit_persistence(case: dict[str, Any]) -> bool:
    intent = normalized_test_intent(case)
    return intent["kind"] == "field_validation" and lower_text(case.get("operation_type")) == "edit"


def normalized_execution_contract(case: dict[str, Any]) -> dict[str, Any]:
    contract = case.get("execution_contract") or {}
    return {
        "capability": lower_text(contract.get("capability")),
        "primary_channel": lower_text(contract.get("primary_channel")),
        "setup_separated": contract.get("setup_separated"),
    }


def normalized_contract_observations(case: dict[str, Any]) -> list[dict[str, Any]]:
    contract = case.get("result_contract") or {}
    observations: list[dict[str, Any]] = []
    for item in as_list(contract.get("observations")):
        if not isinstance(item, dict):
            continue
        normalized = {
            "key": text(item.get("key")),
            "source": lower_text(item.get("source")),
            "operator": lower_text(item.get("operator")),
            "evidence_required": item.get("evidence_required"),
            "assertion_class": lower_text(item.get("assertion_class") or "hard"),
            "prd_exact": item.get("prd_exact") is True,
        }
        if "expected" in item:
            normalized["expected"] = item.get("expected")
        observations.append(normalized)
    return sorted(observations, key=lambda item: item["key"])


def semantic_contract_snapshot(case: dict[str, Any]) -> dict[str, Any]:
    result_contract = case.get("result_contract") or {}
    assertions = [item for item in as_list(case.get("assertions")) if isinstance(item, dict)]
    assertion = assertions[0] if len(assertions) == 1 else {}
    return {
        "contract_version": CONTRACT_VERSION,
        "test_intent": normalized_test_intent(case),
        "execution_profile": text(case.get("execution_profile")),
        "execution_contract": normalized_execution_contract(case),
        "main_assertion": {
            "type": lower_text(assertion.get("type")),
            "target": text(assertion.get("target")),
            "expected": assertion.get("expected"),
        },
        "result_contract": {
            "verdict": lower_text(result_contract.get("verdict")),
            "required_produced_keys": sorted(
                text(value)
                for value in as_list(result_contract.get("required_produced_keys"))
                if text(value)
            ),
            "assertion_observation_keys": sorted(
                text(value)
                for value in as_list(result_contract.get("assertion_observation_keys"))
                if text(value)
            ),
            "screenshot_required": result_contract.get("screenshot_required"),
            "observations": normalized_contract_observations(case),
        },
    }


def semantic_contract_hash(case: dict[str, Any]) -> str:
    payload = json.dumps(
        semantic_contract_snapshot(case),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_executor_profiles(raw_profiles: Any) -> tuple[dict[str, dict[str, set[str]]], list[str]]:
    errors: list[str] = []
    profiles: dict[str, dict[str, set[str]]] = {}
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        return {}, ["final-cases must declare a non-empty executor_profiles mapping"]
    for raw_name, raw_config in raw_profiles.items():
        name = text(raw_name)
        if not name or not isinstance(raw_config, dict):
            errors.append("Each executor_profiles entry must have a name and mapping value")
            continue
        capabilities = {lower_text(value) for value in as_list(raw_config.get("capabilities")) if text(value)}
        channels = {lower_text(value) for value in as_list(raw_config.get("channels")) if text(value)}
        invalid_capabilities = sorted(capabilities - VALID_INTENT_KINDS)
        invalid_channels = sorted(channels - VALID_INTERFACES)
        if not capabilities:
            errors.append(f"Executor profile {name} must declare capabilities")
        if invalid_capabilities:
            errors.append(f"Executor profile {name} has invalid capabilities: {', '.join(invalid_capabilities)}")
        if not channels:
            errors.append(f"Executor profile {name} must declare channels")
        if invalid_channels:
            errors.append(f"Executor profile {name} has invalid channels: {', '.join(invalid_channels)}")
        profiles[name] = {"capabilities": capabilities, "channels": channels}
    return profiles, errors


def validate_case_semantics(
    case: dict[str, Any],
    test_point: dict[str, Any],
    profiles: dict[str, dict[str, set[str]]],
) -> list[str]:
    case_id = text(case.get("case_id")) or "<missing>"
    errors: list[str] = []
    intent = normalized_test_intent(case)
    case_granularity = lower_text(case.get("granularity"))
    expected_kind = "field_validation" if case_granularity in {"field", "字段", "字段级"} else "scenario"
    point_polarity = lower_text(test_point.get("polarity"))
    point_interface = lower_text(test_point.get("interface"))
    point_outcome = lower_text(test_point.get("expected_outcome"))

    if point_polarity not in VALID_POLARITIES:
        errors.append(f"Test point for case {case_id} must declare polarity: positive/negative")
    if point_interface not in VALID_INTERFACES:
        errors.append(f"Test point for case {case_id} must declare interface: ui/api")
    if point_outcome not in VALID_OUTCOMES:
        errors.append(
            f"Test point for case {case_id} must declare expected_outcome: "
            "accepted/rejected/committed/observed"
        )

    if intent["kind"] != expected_kind:
        errors.append(f"Case {case_id} test_intent.kind must be {expected_kind}")
    if intent["polarity"] not in VALID_POLARITIES or intent["polarity"] != point_polarity:
        errors.append(f"Case {case_id} test_intent.polarity must match its test point")
    if intent["interface"] not in VALID_INTERFACES or intent["interface"] != point_interface:
        errors.append(f"Case {case_id} test_intent.interface must match its test point")
    if intent["expected_outcome"] not in VALID_OUTCOMES or intent["expected_outcome"] != point_outcome:
        errors.append(f"Case {case_id} test_intent.expected_outcome must match its test point")
    if intent["polarity"] == "negative" and intent["expected_outcome"] != "rejected":
        errors.append(f"Case {case_id} negative intent must use expected_outcome: rejected")

    if expected_kind == "field_validation":
        point_field = text(test_point.get("field_id"))
        point_input_class = text(test_point.get("coverage_class"))
        if not intent["field_id"] or intent["field_id"] != point_field:
            errors.append(f"Case {case_id} test_intent.field_id must match its field test point")
        if not intent["input_class"] or intent["input_class"] != point_input_class:
            errors.append(f"Case {case_id} test_intent.input_class must match coverage_class")

    profile_name = text(case.get("execution_profile"))
    execution = normalized_execution_contract(case)
    if not profile_name:
        errors.append(f"Case {case_id} must declare execution_profile")
    if execution["capability"] != intent["kind"]:
        errors.append(f"Case {case_id} execution capability must match test intent kind")
    if execution["primary_channel"] != intent["interface"]:
        errors.append(f"Case {case_id} execution primary_channel must match test intent interface")
    if execution["setup_separated"] is not True:
        errors.append(f"Case {case_id} execution_contract.setup_separated must be true")
    profile = profiles.get(profile_name)
    if profile is None:
        errors.append(f"Case {case_id} references unknown executor profile: {profile_name or '<blank>'}")
    else:
        if execution["capability"] not in profile["capabilities"]:
            errors.append(
                f"Case {case_id} executor profile {profile_name} does not support "
                f"capability {execution['capability'] or '<blank>'}"
            )
        if execution["primary_channel"] not in profile["channels"]:
            errors.append(
                f"Case {case_id} executor profile {profile_name} does not support "
                f"channel {execution['primary_channel'] or '<blank>'}"
            )

    result_contract = case.get("result_contract") or {}
    observations = normalized_contract_observations(case)
    if lower_text(result_contract.get("verdict")) != "all":
        errors.append(f"Case {case_id} result_contract.verdict must be all")
    if not observations:
        errors.append(f"Case {case_id} result_contract.observations must not be empty")
    observation_keys = [item["key"] for item in observations]
    if any(not key for key in observation_keys):
        errors.append(f"Case {case_id} result observations must declare non-empty keys")
    if len(set(observation_keys)) != len(observation_keys):
        errors.append(f"Case {case_id} result observation keys must be unique")
    for observation in observations:
        key = observation["key"] or "<missing>"
        if observation["source"] not in VALID_CHANNELS:
            errors.append(f"Case {case_id} observation {key} has invalid source")
        operator = observation["operator"]
        if operator not in VALID_OPERATORS:
            errors.append(f"Case {case_id} observation {key} has invalid operator")
        if operator not in {"non_empty", "empty"} and "expected" not in observation:
            errors.append(f"Case {case_id} observation {key} must declare expected")
        if observation["evidence_required"] is not True:
            errors.append(f"Case {case_id} observation {key} must set evidence_required: true")
        if observation["assertion_class"] not in VALID_ASSERTION_CLASSES:
            errors.append(
                f"Case {case_id} observation {key} assertion_class must be hard or soft"
            )

    required_keys = {
        text(value)
        for value in as_list(result_contract.get("required_produced_keys"))
        if text(value)
    }
    if required_keys != set(observation_keys):
        errors.append(
            f"Case {case_id} required_produced_keys must exactly match result observation keys"
        )
    required_key_items = [
        text(value)
        for value in as_list(result_contract.get("required_produced_keys"))
        if text(value)
    ]
    if len(required_key_items) != len(set(required_key_items)):
        errors.append(f"Case {case_id} required_produced_keys must not contain duplicates")
    assertion_keys = {
        text(value)
        for value in as_list(result_contract.get("assertion_observation_keys"))
        if text(value)
    }
    if not assertion_keys or not assertion_keys.issubset(set(observation_keys)):
        errors.append(
            f"Case {case_id} assertion_observation_keys must reference result observations"
        )
    if intent["interface"] == "ui" and result_contract.get("screenshot_required") is not True:
        errors.append(f"Case {case_id} UI result contract must require a screenshot")

    change = case.get("change_evidence") or {}
    if isinstance(change, dict) and change.get("required") is True:
        if text(change.get("change_type")) not in {"edit", "delete", "change", "state_transition"}:
            errors.append(f"Case {case_id} change_evidence.change_type must be edit/delete/change/state_transition")
        if change.get("comparison_required") is not True:
            errors.append(f"Case {case_id} change_evidence.comparison_required must be true")
        if not text(change.get("before_assertion")) or not text(change.get("after_assertion")):
            errors.append(f"Case {case_id} change_evidence must declare before_assertion and after_assertion")
        observation_names = {item["key"] for item in observations}
        missing_change_keys = sorted({"before_state", "after_state", "changed", "comparison_verified"} - observation_names)
        if missing_change_keys:
            errors.append(f"Case {case_id} change evidence observations missing: {', '.join(missing_change_keys)}")

    assertion_policy = result_contract.get("assertion_policy")
    if not isinstance(assertion_policy, dict):
        errors.append(f"Case {case_id} result_contract.assertion_policy must be a mapping")
    else:
        for key in ("hard_keys", "soft_keys"):
            values = [text(value) for value in as_list(assertion_policy.get(key)) if text(value)]
            if len(values) != len(set(values)):
                errors.append(f"Case {case_id} assertion_policy.{key} must not contain duplicates")
        hard_keys = set(text(value) for value in as_list(assertion_policy.get("hard_keys")) if text(value))
        soft_keys = set(text(value) for value in as_list(assertion_policy.get("soft_keys")) if text(value))
        if hard_keys & soft_keys:
            errors.append(f"Case {case_id} assertion_policy hard_keys and soft_keys overlap")
        if hard_keys | soft_keys:
            missing_policy_keys = set(observation_keys) - (hard_keys | soft_keys)
            if missing_policy_keys:
                errors.append(
                    f"Case {case_id} assertion_policy is missing observation keys: {', '.join(sorted(missing_policy_keys))}"
                )
        if not hard_keys:
            errors.append(f"Case {case_id} assertion_policy.hard_keys must not be empty")
        declared_class_by_key = {item["key"]: item["assertion_class"] for item in observations}
        for key in hard_keys:
            if declared_class_by_key.get(key) != "hard":
                errors.append(f"Case {case_id} hard assertion {key} must declare assertion_class: hard")
        for key in soft_keys:
            if declared_class_by_key.get(key) != "soft":
                errors.append(f"Case {case_id} soft assertion {key} must declare assertion_class: soft")
        for observation in observations:
            if (
                observation["assertion_class"] == "hard"
                and observation["operator"] in {"contains", "matches", "equals"}
                and observation["source"] == "ui"
                and isinstance(observation.get("expected"), str)
                and observation["key"] in {"prompt_text", "success_text", "message_text"}
                and observation.get("prd_exact") is not True
            ):
                errors.append(
                    f"Case {case_id} exact UI message assertion {observation['key']} must be soft or declare prd_exact: true"
                )

    by_key = {item["key"]: item for item in observations}
    if intent["expected_outcome"] == "rejected":
        mutation = by_key.get("mutation_committed")
        if not mutation or mutation.get("operator") != "equals" or mutation.get("expected") is not False:
            errors.append(
                f"Case {case_id} rejected outcome must require mutation_committed equals false"
            )
        if isinstance(assertion_policy, dict) and "mutation_committed" not in {
            text(value) for value in as_list(assertion_policy.get("hard_keys")) if text(value)
        }:
            errors.append(f"Case {case_id} rejected outcome must keep mutation_committed as a hard assertion")
        primary_evidence = [
            item
            for item in observations
            if item["key"] != "mutation_committed" and item["source"] == intent["interface"]
        ]
        if not primary_evidence:
            errors.append(
                f"Case {case_id} rejected outcome needs primary {intent['interface']} rejection evidence"
            )
    if intent["expected_outcome"] == "committed":
        mutation = by_key.get("mutation_committed")
        if not mutation or mutation.get("operator") != "equals" or mutation.get("expected") is not True:
            errors.append(
                f"Case {case_id} committed outcome must require mutation_committed equals true"
            )
    return errors


def has_observation_value(value: Any) -> bool:
    if value is None:
        return False
    return not isinstance(value, str) or bool(value.strip())


def values_equal(actual: Any, expected: Any) -> bool:
    if isinstance(expected, bool):
        return isinstance(actual, bool) and actual is expected
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) and actual == expected
    return str(actual) == str(expected)


def numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def observation_matches(actual: Any, operator: str, expected: Any = None) -> bool:
    if operator == "equals":
        return values_equal(actual, expected)
    if operator == "not_equals":
        return not values_equal(actual, expected)
    if operator == "contains":
        return str(expected) in str(actual)
    if operator == "not_contains":
        return str(expected) not in str(actual)
    if operator == "non_empty":
        return has_observation_value(actual)
    if operator == "empty":
        return not has_observation_value(actual)
    if operator == "matches":
        try:
            return re.search(str(expected), str(actual)) is not None
        except re.error:
            return False
    actual_number = numeric(actual)
    expected_number = numeric(expected)
    if actual_number is None or expected_number is None:
        return False
    if operator == "greater_than":
        return actual_number > expected_number
    if operator == "greater_or_equal":
        return actual_number >= expected_number
    if operator == "less_than":
        return actual_number < expected_number
    if operator == "less_or_equal":
        return actual_number <= expected_number
    return False


def evaluate_observation_results(
    case: dict[str, Any],
    raw_observations: Any,
    evidence_screenshots: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    case_id = text(case.get("case_id")) or "<missing>"
    errors: list[str] = []
    mismatches: list[str] = []
    if not isinstance(raw_observations, dict):
        return [f"Case {case_id} observations must be a mapping"], []
    contract_observations = normalized_contract_observations(case)
    expected_keys = {item["key"] for item in contract_observations}
    actual_keys = {text(key) for key in raw_observations if text(key)}
    if actual_keys != expected_keys:
        errors.append(
            f"Case {case_id} observation keys must exactly match result contract; "
            f"missing={sorted(expected_keys - actual_keys)}, extra={sorted(actual_keys - expected_keys)}"
        )
    for contract_observation in contract_observations:
        key = contract_observation["key"]
        actual_observation = raw_observations.get(key)
        if not isinstance(actual_observation, dict):
            errors.append(f"Case {case_id} is missing structured observation: {key}")
            continue
        source = lower_text(actual_observation.get("source"))
        if source != contract_observation["source"]:
            errors.append(
                f"Case {case_id} observation {key} source mismatch: "
                f"{source or '<blank>'} != {contract_observation['source']}"
            )
        if "value" not in actual_observation:
            errors.append(f"Case {case_id} observation {key} has no value")
            continue
        expected = contract_observation.get("expected")
        if not observation_matches(
            actual_observation.get("value"),
            contract_observation["operator"],
            expected,
        ):
            mismatches.append(
                f"Case {case_id} observation {key} does not satisfy "
                f"{contract_observation['operator']} {expected!r}"
            )
        evidence_refs = [
            text(value)
            for value in as_list(actual_observation.get("evidence_refs"))
            if text(value)
        ]
        if contract_observation["evidence_required"] is True and not evidence_refs:
            errors.append(f"Case {case_id} observation {key} has no evidence_refs")
        if source == "ui" and evidence_screenshots is not None:
            referenced_names = {
                re.split(r"[:\\/]", reference)[-1]
                for reference in evidence_refs
                if reference
            }
            if not referenced_names.intersection(evidence_screenshots):
                errors.append(
                    f"Case {case_id} UI observation {key} does not reference a declared screenshot"
                )
    return errors, mismatches


def validate_observation_results(
    case: dict[str, Any],
    raw_observations: Any,
    evidence_screenshots: set[str] | None = None,
) -> list[str]:
    errors, mismatches = evaluate_observation_results(
        case, raw_observations, evidence_screenshots
    )
    return [*errors, *mismatches]


def observation_values(raw_observations: Any) -> dict[str, Any]:
    if not isinstance(raw_observations, dict):
        return {}
    return {
        text(key): value.get("value")
        for key, value in raw_observations.items()
        if text(key) and isinstance(value, dict) and "value" in value
    }
