from __future__ import annotations

from execution_readiness import validate_case_readiness

import argparse
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from semantic_contract import (
    is_field_edit_persistence,
    requires_persistence_verification,
    semantic_contract_hash,
    validate_case_semantics,
    validate_executor_profiles,
)
from workflow_gate_common import as_list, list_from, load_yaml, source_record, text, write_json


COVERED = {"covered", "included", "test", "已覆盖", "已生成用例"}
EXPLICITLY_EXCLUDED = {
    "explicitly_not_tested",
    "not_tested",
    "skipped",
    "pending_confirmation",
    "明确不测",
    "待确认",
}
SCENARIO = {"scenario", "场景", "场景级"}
FIELD = {"field", "字段", "字段级"}
PRIORITY_POLICY = "strict_p0_p1_p2"
CASE_ORDER_POLICY = "prd_port_page_group"
VALID_PRIORITIES = ("P0", "P1", "P2")
NORMALIZATION_MODE = "case_normalization"
NORMALIZATION_CHANGE_TYPES = {"preserved", "revised", "split", "merged", "added"}
PRIORITY_PERCENT_RANGES = {
    "P0": (20.0, 25.0),
    "P1": (35.0, 40.0),
    "P2": (35.0, 40.0),
}


def item_id(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = text(item.get(key))
        if value:
            return value
    return ""


def req_ids(item: dict[str, Any]) -> list[str]:
    values = item.get("requirement_ids")
    if values is None:
        values = item.get("requirement_id")
    return [text(value) for value in as_list(values) if text(value)]


def granularity(item: dict[str, Any]) -> str:
    value = text(item.get("granularity")).lower()
    if value in SCENARIO:
        return "scenario"
    if value in FIELD:
        return "field"
    return value


def duplicate_values(values: list[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if value and count > 1)


def normalized_priority(item: dict[str, Any]) -> str:
    return text(item.get("priority")).upper()


def feature_group(item: dict[str, Any]) -> str:
    return text(item.get("feature_group"))


def ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def priority_count_bounds(total: int) -> dict[str, tuple[int, int]]:
    """Use one-case rounding tolerance only when strict percentages are not integral."""
    bounds: dict[str, tuple[int, int]] = {}
    for priority, (minimum, maximum) in PRIORITY_PERCENT_RANGES.items():
        if total < 20:
            lower = max(1, math.floor(total * minimum / 100.0))
            upper = max(1, math.ceil(total * maximum / 100.0))
        else:
            lower = math.ceil(total * minimum / 100.0)
            upper = math.floor(total * maximum / 100.0)
        bounds[priority] = (lower, upper)
    return bounds


def flatten_case_text(case: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("formal_case_title", "case_title", "title", "formal_expected_result"):
        value = text(case.get(key))
        if value:
            parts.append(value)
    precondition_source = case.get("formal_preconditions")
    if precondition_source is None:
        precondition_source = case.get("preconditions")
    for item in as_list(precondition_source):
        value = text(item)
        if value:
            parts.append(value)
    step_source = case.get("formal_steps")
    if step_source is None:
        step_source = case.get("steps")
    for item in as_list(step_source):
        if isinstance(item, str):
            value = text(item)
            if value:
                parts.append(value)
            continue
        if isinstance(item, dict):
            for subkey in ("formal_text", "display_text", "action", "target", "input"):
                value = text(item.get(subkey))
                if value:
                    parts.append(value)
    assertions = [item for item in as_list(case.get("assertions")) if isinstance(item, dict)]
    if assertions:
        parts.append(text(assertions[0].get("expected")))
    return " ".join(part for part in parts if part)


def formal_step_actions(case: dict[str, Any]) -> list[str]:
    step_source = case.get("formal_steps")
    if step_source is None:
        step_source = case.get("steps")
    actions: list[str] = []
    for item in as_list(step_source):
        if isinstance(item, str):
            if text(item):
                actions.append(text(item))
            continue
        if isinstance(item, dict):
            action = text(item.get("formal_text") or item.get("display_text") or item.get("action"))
            if action:
                actions.append(action)
    return actions


def validate_persistence_verification(
    case: dict[str, Any], point: dict[str, Any] | None
) -> list[str]:
    case_id = text(case.get("case_id")) or "<missing>"
    point_requires = bool(point and point.get("persistence_verification_required") is True)
    required = requires_persistence_verification(case, point_requires=point_requires)
    field_edit = is_field_edit_persistence(case)
    policy = case.get("persistence_verification")
    if not required and policy is None:
        return []
    if not isinstance(policy, dict):
        return [f"Case {case_id} must declare persistence_verification as a mapping"]

    errors: list[str] = []
    if policy.get("required") is not required:
        errors.append(f"Case {case_id} persistence_verification.required must be {required}")
    for field in (
        "commit_required",
        "reopen_or_query_required",
        "compare_input_persisted",
    ):
        if required and policy.get(field) is not True:
            errors.append(f"Case {case_id} persistence_verification.{field} must be true")
    if field_edit and policy.get("baseline_required") is not True:
        errors.append(f"Case {case_id} edit persistence_verification.baseline_required must be true")

    formal_text = flatten_case_text(case)
    step_actions = formal_step_actions(case)
    explicit_commit = any(
        any(token in action.replace("保存后", "").replace("提交后", "") for token in ("保存", "提交"))
        for action in step_actions
    )
    if required and not explicit_commit:
        errors.append(f"Case {case_id} persistence steps must explicitly save or submit")
    if required and not any(
        token in action
        for action in step_actions
        for token in ("重新打开", "重新查询", "再次打开")
    ):
        errors.append(f"Case {case_id} persistence steps must explicitly reopen or re-query the same object")
    if required and not all(token in formal_text for token in ("输入", "回显")):
        errors.append(f"Case {case_id} persistence steps must compare input and persisted echo values")

    required_keys = {
        text(value)
        for value in as_list((case.get("result_contract") or {}).get("required_produced_keys"))
        if text(value)
    }
    expected_keys = {
        "object_id",
        "input_value",
        "persisted_value",
        "persistence_verified",
        "mutation_committed",
    }
    if field_edit:
        expected_keys.add("before_value")
    missing_keys = sorted(expected_keys - required_keys) if required else []
    if missing_keys:
        errors.append(
            f"Case {case_id} persistence result contract is missing keys: {', '.join(missing_keys)}"
        )
    return errors


def validate_fixture_contract(case: dict[str, Any]) -> list[str]:
    """Validate the declarative shape of structured test-data fixtures.

    Runtime validation checks the generated file itself.  This design-time
    check catches contradictory contracts before a case can be scheduled.
    """
    raw = case.get("fixture_contract")
    if raw is None:
        return []
    case_id = text(case.get("case_id")) or "<missing>"
    if not isinstance(raw, dict):
        return [f"Case {case_id} fixture_contract must be a mapping"]
    errors: list[str] = []
    allowed_formats = {"csv", "xls", "xlsx", "json"}
    file_format = text(raw.get("format")).lower().lstrip(".")
    if file_format and file_format not in allowed_formats:
        errors.append(f"Case {case_id} fixture_contract.format is unsupported: {file_format}")
    required = {text(v) for v in as_list(raw.get("required_headers")) if text(v)}
    missing = {text(v) for v in as_list(raw.get("missing_headers")) if text(v)}
    empty = {text(v) for v in as_list(raw.get("empty_fields")) if text(v)}
    non_empty = {text(v) for v in as_list(raw.get("non_empty_fields")) if text(v)}
    if required & missing:
        errors.append(f"Case {case_id} fixture_contract required_headers and missing_headers overlap: {sorted(required & missing)}")
    if empty & non_empty:
        errors.append(f"Case {case_id} fixture_contract empty_fields and non_empty_fields overlap: {sorted(empty & non_empty)}")
    if not required and not missing:
        errors.append(f"Case {case_id} fixture_contract must declare required_headers or missing_headers")
    valid_categories = {"missing_header_validation", "required_value_validation", "file_parse_error", "file_size_validation", "business_validation"}
    category = text(raw.get("expected_feedback_category"))
    if category and category not in valid_categories:
        errors.append(f"Case {case_id} fixture_contract.expected_feedback_category is unsupported: {category}")
    if category == "required_value_validation" and not empty:
        errors.append(f"Case {case_id} required_value_validation fixture must declare empty_fields")
    if category == "missing_header_validation" and not missing:
        errors.append(f"Case {case_id} missing_header_validation fixture must declare missing_headers")
    return errors


def validate_normalization_trace(
    cases: list[dict[str, Any]],
    source_meta: dict[str, Any],
    import_doc: dict[str, Any] | None,
) -> list[str]:
    """校验已有用例规范化模式的源行追踪。

    参数：cases 为最终用例，source_meta 为最终源元数据，import_doc 为已有用例导入产物。
    返回：所有不可追溯或来源声明不一致的错误。
    异常：不主动抛出异常，结构问题统一转换为门禁错误。
    使用场景：阻止已有 Excel 在缺少审计链时直接进入执行。
    """
    if text(source_meta.get("generation_mode")).lower() != NORMALIZATION_MODE:
        return []

    errors: list[str] = []
    if import_doc is None:
        return ["case_normalization requires --existing-case-import"]
    import_meta = import_doc.get("import_meta") or {}
    if not isinstance(import_meta, dict):
        return ["existing-case import must declare import_meta"]
    if text(import_meta.get("artifact_role")).lower() != "existing_case_import":
        errors.append("existing-case import must declare import_meta.artifact_role: existing_case_import")
    if text(import_meta.get("mode")).lower() != NORMALIZATION_MODE:
        errors.append("existing-case import must declare import_meta.mode: case_normalization")
    imported_source = import_meta.get("source") or {}
    imported_sha256 = text(imported_source.get("sha256"))
    if not text(imported_source.get("path")) or not re.fullmatch(r"[0-9a-fA-F]{64}", imported_sha256):
        errors.append("existing-case import must bind source.path and source.sha256")

    imported_cases = import_doc.get("imported_cases") or []
    if not isinstance(imported_cases, list) or not all(isinstance(item, dict) for item in imported_cases):
        return [*errors, "existing-case import must contain imported_cases mappings"]
    imported_rows = {
        item.get("source_row")
        for item in imported_cases
        if isinstance(item.get("source_row"), int) and item.get("source_row") > 0
    }
    if not imported_rows:
        errors.append("existing-case import must contain positive source_row values")
    imported_ids_by_row = {
        item.get("source_row"): {
            value
            for value in (
                text(item.get("source_case_id")),
                text(item.get("suggested_case_id")),
            )
            if value
        }
        for item in imported_cases
        if isinstance(item.get("source_row"), int) and item.get("source_row") > 0
    }

    requirement_source = text(source_meta.get("requirement_source")).lower()
    completeness = text(source_meta.get("requirement_completeness")).lower()
    if requirement_source not in {"prd", "existing_xlsx_baseline"}:
        errors.append("case_normalization case_source.requirement_source must be prd or existing_xlsx_baseline")
    if completeness not in {"assessed", "not_assessed"}:
        errors.append("case_normalization case_source.requirement_completeness must be assessed or not_assessed")
    if requirement_source == "existing_xlsx_baseline" and completeness != "not_assessed":
        errors.append("existing_xlsx_baseline must declare requirement_completeness: not_assessed")
    if requirement_source == "prd" and completeness != "assessed":
        errors.append("PRD-backed normalization must declare requirement_completeness: assessed")

    split_rows: dict[int, int] = defaultdict(int)
    for case in cases:
        case_id = text(case.get("case_id")) or "<missing>"
        trace = case.get("normalization_trace")
        if not isinstance(trace, dict):
            errors.append(f"Case {case_id} must declare normalization_trace in case_normalization mode")
            continue
        origin = text(trace.get("origin")).lower()
        change_type = text(trace.get("change_type")).lower()
        source_rows = as_list(trace.get("source_rows"))
        source_case_ids = [text(value) for value in as_list(trace.get("source_case_ids")) if text(value)]
        if change_type not in NORMALIZATION_CHANGE_TYPES:
            errors.append(
                f"Case {case_id} normalization_trace.change_type must be one of: "
                f"{', '.join(sorted(NORMALIZATION_CHANGE_TYPES))}"
            )
        if not text(trace.get("reason")):
            errors.append(f"Case {case_id} normalization_trace.reason must not be blank")
        if origin == "existing_xlsx":
            if change_type == "added":
                errors.append(f"Case {case_id} existing_xlsx origin cannot use change_type added")
            if not source_rows or not all(isinstance(value, int) and value > 0 for value in source_rows):
                errors.append(f"Case {case_id} existing_xlsx origin must declare positive source_rows")
            unknown_rows = sorted({value for value in source_rows if value not in imported_rows})
            if unknown_rows:
                errors.append(
                    f"Case {case_id} normalization_trace references unknown source rows: "
                    f"{', '.join(str(value) for value in unknown_rows)}"
                )
            if not source_case_ids:
                errors.append(f"Case {case_id} existing_xlsx origin must declare source_case_ids")
            known_ids = {
                value
                for row in source_rows
                for value in imported_ids_by_row.get(row, set())
            }
            unknown_ids = sorted(set(source_case_ids) - known_ids)
            if unknown_ids:
                errors.append(
                    f"Case {case_id} normalization_trace references unknown source case IDs: "
                    f"{', '.join(unknown_ids)}"
                )
            if change_type == "merged" and len(set(source_rows)) < 2:
                errors.append(f"Case {case_id} merged normalization must reference at least two source rows")
            if change_type == "split":
                for row in set(source_rows):
                    split_rows[row] += 1
        elif origin == "prd_supplement":
            if requirement_source != "prd":
                errors.append(f"Case {case_id} cannot use prd_supplement without PRD-backed normalization")
            if change_type != "added":
                errors.append(f"Case {case_id} prd_supplement origin must use change_type added")
            if source_rows:
                errors.append(f"Case {case_id} prd_supplement origin must not declare source_rows")
            if source_case_ids:
                errors.append(f"Case {case_id} prd_supplement origin must not declare source_case_ids")
        else:
            errors.append(f"Case {case_id} normalization_trace.origin must be existing_xlsx or prd_supplement")
    for row, count in sorted(split_rows.items()):
        if count < 2:
            errors.append(
                f"Source row {row} uses change_type split but maps to only {count} final case"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate requirements -> test points -> final cases before execution.")
    parser.add_argument("--requirements", required=True)
    parser.add_argument("--test-points", required=True)
    parser.add_argument("--final-cases", required=True)
    parser.add_argument("--existing-case-import")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    requirement_path = Path(args.requirements).resolve()
    test_point_path = Path(args.test_points).resolve()
    final_case_path = Path(args.final_cases).resolve()
    existing_case_import_path = Path(args.existing_case_import).resolve() if args.existing_case_import else None
    output_path = Path(args.output).resolve()
    errors: list[str] = []
    warnings: list[str] = []
    priority_errors: list[str] = []
    ordering_errors: list[str] = []

    def add_priority_error(message: str) -> None:
        priority_errors.append(message)
        errors.append(message)

    def add_ordering_error(message: str) -> None:
        ordering_errors.append(message)
        errors.append(message)

    try:
        requirement_doc = load_yaml(requirement_path)
        test_point_doc = load_yaml(test_point_path)
        case_doc = load_yaml(final_case_path)
        requirements = list_from(requirement_doc, ("requirements", "requirement_items"), "requirements")
        test_points = list_from(test_point_doc, ("test_points", "coverage_matrix"), "test_points")
        cases = list_from(case_doc, ("test_cases",), "test_cases")
        import_doc = load_yaml(existing_case_import_path) if existing_case_import_path else None
    except (OSError, ValueError) as exc:
        payload = {"gate": "case_design", "status": "fail", "errors": [str(exc)], "warnings": []}
        write_json(output_path, payload)
        print(str(exc), file=sys.stderr)
        return 1

    executor_profiles, profile_errors = validate_executor_profiles(case_doc.get("executor_profiles"))
    errors.extend(profile_errors)

    source_meta = case_doc.get("case_source") or {}
    if text(source_meta.get("artifact_role")).lower() != "final":
        errors.append("final-cases must declare case_source.artifact_role: final")
    if text(source_meta.get("mode")).lower() != "full":
        errors.append("final-cases must declare case_source.mode: full")
    errors.extend(validate_normalization_trace(cases, source_meta, import_doc))

    case_ordering = case_doc.get("case_ordering") or {}
    if not isinstance(case_ordering, dict):
        add_ordering_error("final-cases case_ordering must be a mapping")
        case_ordering = {}
    if text(case_ordering.get("policy")).lower() != CASE_ORDER_POLICY:
        add_ordering_error(
            f"final-cases must declare case_ordering.policy: {CASE_ORDER_POLICY}"
        )
    if not text(case_ordering.get("source")):
        add_ordering_error("final-cases case_ordering.source must identify the current PRD")
    declared_group_sequence = [
        text(value) for value in as_list(case_ordering.get("group_sequence")) if text(value)
    ]
    if not declared_group_sequence:
        add_ordering_error("final-cases case_ordering.group_sequence must not be empty")
    duplicate_groups = duplicate_values(declared_group_sequence)
    if duplicate_groups:
        add_ordering_error(
            f"case_ordering.group_sequence contains duplicate groups: {', '.join(duplicate_groups)}"
        )

    profile = test_point_doc.get("coverage_profile") or {}
    if text(profile.get("mode")).lower() != "full":
        errors.append("test-points must declare coverage_profile.mode: full")
    core_form_fields_present = profile.get("core_form_fields_present")
    if not isinstance(core_form_fields_present, bool):
        errors.append("coverage_profile.core_form_fields_present must be true or false")
    required_granularities = {granularity({"granularity": value}) for value in as_list(profile.get("required_granularities"))}
    invalid_required_granularities = sorted(required_granularities - {"scenario", "field"})
    if invalid_required_granularities:
        errors.append(f"coverage_profile.required_granularities contains invalid values: {', '.join(invalid_required_granularities)}")
    if text(profile.get("priority_policy")).lower() != PRIORITY_POLICY:
        add_priority_error(
            f"test-points must declare coverage_profile.priority_policy: {PRIORITY_POLICY}"
        )
    if text(source_meta.get("priority_policy")).lower() != PRIORITY_POLICY:
        add_priority_error(
            f"final-cases must declare case_source.priority_policy: {PRIORITY_POLICY}"
        )

    requirement_ids = [item_id(item, ("requirement_id", "req_id", "id")) for item in requirements]
    test_point_ids = [item_id(item, ("test_point_id", "coverage_id", "id")) for item in test_points]
    case_ids = [item_id(item, ("case_id", "id")) for item in cases]
    if not requirements:
        errors.append("requirements must not be empty")
    if not test_points:
        errors.append("test_points must not be empty")
    if not cases:
        errors.append("final test_cases must not be empty")
    for label, values in (("requirement", requirement_ids), ("test point", test_point_ids), ("case", case_ids)):
        if any(not value for value in values):
            errors.append(f"One or more {label} items have no ID")
        duplicates = duplicate_values(values)
        if duplicates:
            errors.append(f"Duplicate {label} IDs: {', '.join(duplicates)}")

    requirement_set = set(requirement_ids)
    covered_requirements: set[str] = set()
    excluded_requirements: set[str] = set()
    for item, requirement_id in zip(requirements, requirement_ids):
        disposition = text(item.get("disposition") or item.get("status") or "covered").lower()
        if disposition in COVERED:
            covered_requirements.add(requirement_id)
        elif disposition in EXPLICITLY_EXCLUDED:
            excluded_requirements.add(requirement_id)
            if not text(item.get("notes") or item.get("reason")):
                errors.append(f"Requirement {requirement_id or '<missing>'} is excluded/pending but has no notes or reason")
        else:
            errors.append(f"Requirement {requirement_id or '<missing>'} has unhandled disposition: {disposition or '<blank>'}")

    points_by_requirement: dict[str, list[str]] = defaultdict(list)
    test_point_requirement_map: dict[str, set[str]] = {}
    test_point_granularity_map: dict[str, str] = {}
    test_point_priority_map: dict[str, str] = {}
    test_point_priority_rationale_map: dict[str, str] = {}
    test_point_feature_group_map: dict[str, str] = {}
    test_points_by_id: dict[str, dict[str, Any]] = {}
    for item, test_point_id in zip(test_points, test_point_ids):
        test_points_by_id[test_point_id] = item
        related = req_ids(item)
        test_point_requirement_map[test_point_id] = set(related)
        point_granularity = granularity(item)
        test_point_granularity_map[test_point_id] = point_granularity
        point_priority = normalized_priority(item)
        point_priority_rationale = text(item.get("priority_rationale"))
        point_feature_group = feature_group(item)
        test_point_priority_map[test_point_id] = point_priority
        test_point_priority_rationale_map[test_point_id] = point_priority_rationale
        test_point_feature_group_map[test_point_id] = point_feature_group
        if point_granularity not in {"scenario", "field"}:
            errors.append(f"Test point {test_point_id or '<missing>'} has invalid granularity: {point_granularity or '<blank>'}")
        if not related:
            errors.append(f"Test point {test_point_id or '<missing>'} has no requirement_ids")
        if point_priority not in VALID_PRIORITIES:
            add_priority_error(
                f"Test point {test_point_id or '<missing>'} priority must be one of: "
                f"{', '.join(VALID_PRIORITIES)}"
            )
        if not point_priority_rationale:
            add_priority_error(
                f"Test point {test_point_id or '<missing>'} must declare priority_rationale"
            )
        if not point_feature_group:
            add_ordering_error(
                f"Test point {test_point_id or '<missing>'} must declare feature_group"
            )
        unknown = sorted(set(related) - requirement_set)
        if unknown:
            errors.append(f"Test point {test_point_id} references unknown requirements: {', '.join(unknown)}")
        for requirement_id in related:
            points_by_requirement[requirement_id].append(test_point_id)

    unmapped = sorted(requirement_id for requirement_id in covered_requirements if not points_by_requirement[requirement_id])
    if unmapped:
        errors.append(f"Covered requirements without test points: {', '.join(unmapped)}")
    excluded_with_points = sorted(requirement_id for requirement_id in excluded_requirements if points_by_requirement[requirement_id])
    if excluded_with_points:
        warnings.append(f"Explicitly excluded requirements still have test points: {', '.join(excluded_with_points)}")

    point_to_cases: dict[str, list[str]] = defaultdict(list)
    scenario_count = 0
    field_count = 0
    final_case_point_ids: list[str] = []
    case_feature_groups: list[str] = []
    for case, case_id in zip(cases, case_ids):
        errors.extend(validate_case_readiness(case))
        point_values = [text(value) for value in as_list(case.get("test_point_id") or case.get("test_point_ids")) if text(value)]
        if len(point_values) != 1:
            errors.append(f"Case {case_id or '<missing>'} must reference exactly one test_point_id")
            continue
        point_id = point_values[0]
        final_case_point_ids.append(point_id)
        point_to_cases[point_id].append(case_id)
        case_feature_group = feature_group(case)
        case_feature_groups.append(case_feature_group)
        if not case_feature_group:
            add_ordering_error(f"Case {case_id or '<missing>'} must declare feature_group")
        point_feature_group = test_point_feature_group_map.get(point_id, "")
        if point_feature_group and case_feature_group != point_feature_group:
            add_ordering_error(
                f"Case {case_id} feature_group ({case_feature_group or '<blank>'}) does not match "
                f"test point {point_id} ({point_feature_group})"
            )
        if point_id not in set(test_point_ids):
            errors.append(f"Case {case_id} references unknown test point: {point_id}")
        case_priority = normalized_priority(case)
        case_priority_rationale = text(case.get("priority_rationale"))
        if case_priority not in VALID_PRIORITIES:
            add_priority_error(
                f"Case {case_id or '<missing>'} priority must be one of: "
                f"{', '.join(VALID_PRIORITIES)}"
            )
        if not case_priority_rationale:
            add_priority_error(
                f"Case {case_id or '<missing>'} must declare priority_rationale"
            )
        point_priority = test_point_priority_map.get(point_id)
        if point_priority in VALID_PRIORITIES and case_priority != point_priority:
            add_priority_error(
                f"Case {case_id} priority ({case_priority or '<blank>'}) does not match "
                f"test point {point_id} ({point_priority})"
            )
        point_priority_rationale = test_point_priority_rationale_map.get(point_id, "")
        if point_priority_rationale and case_priority_rationale != point_priority_rationale:
            add_priority_error(
                f"Case {case_id} priority_rationale does not match test point {point_id}"
            )
        related_requirements = set(req_ids(case))
        if not related_requirements:
            errors.append(f"Case {case_id} has no requirement_ids")
        unknown_case_requirements = sorted(related_requirements - requirement_set)
        if unknown_case_requirements:
            errors.append(f"Case {case_id} references unknown requirements: {', '.join(unknown_case_requirements)}")
        missing_requirements = sorted(test_point_requirement_map.get(point_id, set()) - related_requirements)
        if missing_requirements:
            errors.append(f"Case {case_id} is missing test-point requirements: {', '.join(missing_requirements)}")
        unexpected_requirements = sorted(related_requirements - test_point_requirement_map.get(point_id, set()))
        if unexpected_requirements:
            errors.append(f"Case {case_id} has requirements outside its test point: {', '.join(unexpected_requirements)}")
        assertions = [item for item in as_list(case.get("assertions")) if isinstance(item, dict)]
        if len(assertions) != 1:
            errors.append(f"Case {case_id} must contain exactly one assertion; got {len(assertions)}")
        elif not text(assertions[0].get("expected")):
            errors.append(f"Case {case_id} assertion expected value is blank")
        case_title = text(case.get("formal_case_title") or case.get("case_title") or case.get("title"))
        case_expected = text(case.get("formal_expected_result") or (assertions[0].get("expected") if assertions else ""))
        if case_title and case_expected:
            normalized_title = "".join(ch for ch in case_title if not ch.isspace() and ch not in "，。,.！!？?；;：:")
            normalized_expected = "".join(ch for ch in case_expected if not ch.isspace() and ch not in "，。,.！!？?；;：:")
            if normalized_title == normalized_expected:
                errors.append(f"Case {case_id} case title and expected result are identical; split name and outcome")
        formal_text = flatten_case_text(case)
        forbidden_tokens = ["open_url", "click", "input", "select", "hover", "upload", "wait_for", "assert_text", "assert_element", "execution_profile", "field_id", "coverage_class", "runtime_value"]
        exposed_tokens = sorted(token for token in forbidden_tokens if token in formal_text)
        if exposed_tokens:
            errors.append(f"Case {case_id} formal text exposes execution tokens: {', '.join(exposed_tokens)}")
        bad_phrases = ["符合 PRD", "与需求一致", "按 PRD", "按需求", "符合需求"]
        exposed_phrases = sorted(phrase for phrase in bad_phrases if phrase in formal_text)
        if exposed_phrases:
            errors.append(f"Case {case_id} formal text uses non-self-contained expected phrasing: {', '.join(exposed_phrases)}")
        case_granularity = granularity(case)
        if case_granularity == "scenario":
            scenario_count += 1
        elif case_granularity == "field":
            field_count += 1
        else:
            errors.append(f"Case {case_id} has invalid granularity: {case_granularity or '<blank>'}")
        point_granularity = test_point_granularity_map.get(point_id)
        if point_granularity in {"scenario", "field"} and case_granularity != point_granularity:
            errors.append(
                f"Case {case_id} granularity ({case_granularity or '<blank>'}) does not match "
                f"test point {point_id} ({point_granularity})"
            )
        point = test_points_by_id.get(point_id)
        if point is not None:
            errors.extend(validate_case_semantics(case, point, executor_profiles))
        errors.extend(validate_persistence_verification(case, point))
        errors.extend(validate_fixture_contract(case))

    missing_case_points = sorted(set(test_point_ids) - set(final_case_point_ids))
    extra_case_points = sorted(set(final_case_point_ids) - set(test_point_ids))
    multiple_case_points = sorted(point_id for point_id, mapped_cases in point_to_cases.items() if len(mapped_cases) > 1)
    if missing_case_points:
        errors.append(f"Test points without final cases: {', '.join(missing_case_points)}")
    if extra_case_points:
        errors.append(f"Final cases reference unknown test points: {', '.join(extra_case_points)}")
    if multiple_case_points:
        errors.append(f"Test points mapped to multiple final cases: {', '.join(multiple_case_points)}")
    if len(cases) != len(test_points):
        errors.append(f"final_case_count ({len(cases)}) must equal test_point_count ({len(test_points)})")
    if final_case_point_ids != test_point_ids:
        add_ordering_error(
            "Test point order must exactly match final-case test_point_id order"
        )
    test_point_feature_groups = [feature_group(item) for item in test_points]
    if test_point_feature_groups != case_feature_groups:
        add_ordering_error(
            "Test point feature_group order must exactly match final-case feature_group order"
        )
    actual_group_sequence = ordered_unique(case_feature_groups)
    if actual_group_sequence != declared_group_sequence:
        add_ordering_error(
            "Actual final-case feature-group sequence does not match "
            "case_ordering.group_sequence: "
            f"actual={actual_group_sequence}, declared={declared_group_sequence}"
        )
    positions_by_group: dict[str, list[int]] = defaultdict(list)
    for index, group in enumerate(case_feature_groups):
        if group:
            positions_by_group[group].append(index)
    split_groups = sorted(
        group
        for group, positions in positions_by_group.items()
        if positions != list(range(positions[0], positions[-1] + 1))
    )
    if split_groups:
        add_ordering_error(
            f"Feature groups must be contiguous; split groups: {', '.join(split_groups)}"
        )
    if len(cases) <= len(requirements):
        errors.append(f"final_case_count ({len(cases)}) must be greater than requirement_count ({len(requirements)})")
    priority_counts = Counter(
        normalized_priority(case)
        for case in cases
        if normalized_priority(case) in VALID_PRIORITIES
    )
    priority_percentages = {
        priority: round(priority_counts.get(priority, 0) * 100.0 / len(cases), 2)
        if cases
        else 0.0
        for priority in VALID_PRIORITIES
    }
    priority_bounds = priority_count_bounds(len(cases)) if cases else {
        priority: (0, 0) for priority in VALID_PRIORITIES
    }
    if 0 < len(cases) < len(VALID_PRIORITIES):
        add_priority_error(
            f"Full delivery must contain at least {len(VALID_PRIORITIES)} final cases "
            "to represent P0, P1, and P2"
        )
    if len(cases) >= len(VALID_PRIORITIES):
        missing_priorities = [
            priority for priority in VALID_PRIORITIES if priority_counts.get(priority, 0) == 0
        ]
        if missing_priorities:
            add_priority_error(
                f"Full delivery is missing required priority levels: {', '.join(missing_priorities)}"
            )
        for priority in VALID_PRIORITIES:
            count = priority_counts.get(priority, 0)
            lower, upper = priority_bounds[priority]
            if count < lower or count > upper:
                minimum, maximum = PRIORITY_PERCENT_RANGES[priority]
                add_priority_error(
                    f"Priority {priority} count {count}/{len(cases)} "
                    f"({priority_percentages[priority]:.2f}%) is outside required "
                    f"{minimum:.0f}%-{maximum:.0f}% range; allowed count is {lower}-{upper}"
                )
    review_audit = case_doc.get("review_audit") or {}
    if not isinstance(review_audit, dict):
        errors.append("final-cases review_audit must be a mapping")
        review_audit = {}
    sampled_case_ids = [text(value) for value in as_list(review_audit.get("environment_sample_case_ids")) if text(value)]
    probes = [value for value in as_list(review_audit.get("environment_probes")) if isinstance(value, dict)]
    minimum_sample = max(1, math.ceil(len(cases) * 0.10)) if cases else 0
    p0_ids = [case_id for case, case_id in zip(cases, case_ids) if normalized_priority(case) == "P0"]
    minimum_p0_sample = max(1, math.ceil(len(p0_ids) * 0.20)) if p0_ids else 0
    if len(set(sampled_case_ids)) < minimum_sample:
        errors.append(f"review_audit must sample at least {minimum_sample} cases for environment executability")
    sampled_p0 = set(sampled_case_ids).intersection(p0_ids)
    if len(sampled_p0) < minimum_p0_sample:
        errors.append(f"review_audit must sample at least {minimum_p0_sample} P0 cases for environment executability")
    unknown_samples = sorted(set(sampled_case_ids) - set(case_ids))
    if unknown_samples:
        errors.append(f"review_audit references unknown sampled cases: {', '.join(unknown_samples)}")
    probes_by_case = {text(probe.get("case_id")): probe for probe in probes if text(probe.get("case_id"))}
    for sampled_case_id in sampled_case_ids:
        probe = probes_by_case.get(sampled_case_id)
        if probe is None:
            errors.append(f"review_audit has no environment probe for {sampled_case_id}")
            continue
        if text(probe.get("status")).lower() not in {"executable", "risk", "blocked"}:
            errors.append(f"review_audit probe {sampled_case_id} status must be executable/risk/blocked")
        if not as_list(probe.get("channels_tried")) or not text(probe.get("evidence")):
            errors.append(f"review_audit probe {sampled_case_id} must include channels_tried and evidence")
        if text(probe.get("status")).lower() != "executable" and not text(probe.get("revision_action")):
            errors.append(f"review_audit probe {sampled_case_id} needs revision_action when status is not executable")
    score_distribution = review_audit.get("score_distribution") or {}
    if not isinstance(score_distribution, dict):
        errors.append("review_audit.score_distribution must be a mapping")
    else:
        scored_cases = score_distribution.get("scored_cases")
        perfect_scores = score_distribution.get("perfect_scores")
        if scored_cases != len(cases):
            errors.append(f"review_audit.score_distribution.scored_cases must be {len(cases)}")
        if not isinstance(perfect_scores, int) or perfect_scores < 0 or perfect_scores > len(cases):
            errors.append("review_audit.score_distribution.perfect_scores is invalid")
        if perfect_scores == len(cases) and not text(score_distribution.get("perfect_score_justification")):
            errors.append("All cases scored 22/22; review_audit must provide perfect_score_justification")
    if scenario_count == 0:
        errors.append("Full delivery must include scenario-level cases")
    if core_form_fields_present and field_count == 0:
        errors.append("Core form fields are present, but no field-level cases exist")
    actual_granularities = {value for value, count in (("scenario", scenario_count), ("field", field_count)) if count > 0}
    missing_granularities = sorted(required_granularities - actual_granularities)
    if missing_granularities:
        errors.append(f"Full delivery is missing required granularities: {', '.join(missing_granularities)}")

    field_specs = test_point_doc.get("field_coverage") or []
    if core_form_fields_present and not field_specs:
        errors.append("Core form fields are present, but field_coverage is empty")
    observed_field_classes: dict[str, set[str]] = defaultdict(set)
    for item in test_points:
        if granularity(item) != "field":
            continue
        field_id = text(item.get("field_id"))
        coverage_class = text(item.get("coverage_class"))
        if not field_id or not coverage_class:
            errors.append(f"Field test point {item_id(item, ('test_point_id', 'coverage_id', 'id'))} must declare field_id and coverage_class")
        else:
            observed_field_classes[field_id].add(coverage_class)
    for spec in field_specs:
        if not isinstance(spec, dict):
            errors.append("field_coverage entries must be mappings")
            continue
        field_id = text(spec.get("field_id"))
        required_classes = {text(value) for value in as_list(spec.get("required_classes")) if text(value)}
        if not field_id or not required_classes:
            errors.append("Each field_coverage entry must declare field_id and required_classes")
            continue
        missing_classes = sorted(required_classes - observed_field_classes.get(field_id, set()))
        if missing_classes:
            errors.append(f"Field {field_id} is missing coverage classes: {', '.join(missing_classes)}")

    payload = {
        "gate": "case_design",
        "status": "fail" if errors else "pass",
        "mode": "full",
        "contract_version": "2.0",
        "inputs": {
            "requirements": source_record(requirement_path),
            "test_points": source_record(test_point_path),
            "final_cases": source_record(final_case_path),
            **(
                {"existing_case_import": source_record(existing_case_import_path)}
                if existing_case_import_path
                else {}
            ),
        },
        "counts": {
            "requirements": len(requirements),
            "covered_requirements": len(covered_requirements),
            "explicitly_excluded_requirements": len(excluded_requirements),
            "test_points": len(test_points),
            "final_cases": len(cases),
            "scenario_cases": scenario_count,
            "field_cases": field_count,
            "unmapped_covered_requirements": len(unmapped),
        },
        "priority_policy": {
            "name": PRIORITY_POLICY,
            "status": "fail" if priority_errors else "pass",
            "ranges": {
                priority: {
                    "min_percent": PRIORITY_PERCENT_RANGES[priority][0],
                    "max_percent": PRIORITY_PERCENT_RANGES[priority][1],
                    "min_count": priority_bounds[priority][0],
                    "max_count": priority_bounds[priority][1],
                }
                for priority in VALID_PRIORITIES
            },
            "counts": {priority: priority_counts.get(priority, 0) for priority in VALID_PRIORITIES},
            "percentages": priority_percentages,
            "errors": priority_errors,
        },
        "case_ordering": {
            "policy": CASE_ORDER_POLICY,
            "status": "fail" if ordering_errors else "pass",
            "source": text(case_ordering.get("source")),
            "declared_group_sequence": declared_group_sequence,
            "actual_group_sequence": actual_group_sequence,
            "errors": ordering_errors,
        },
        "review_audit": {
            "status": "fail" if any("review_audit" in error for error in errors) else "pass",
            "sampled_cases": len(set(sampled_case_ids)),
            "minimum_sample": minimum_sample,
            "sampled_p0_cases": len(sampled_p0),
            "minimum_p0_sample": minimum_p0_sample,
        },
        "semantic_contracts": {
            case_id: semantic_contract_hash(case)
            for case, case_id in zip(cases, case_ids)
            if case_id
        },
        "errors": errors,
        "warnings": warnings,
    }
    write_json(output_path, payload)
    print(f"case-design gate: {payload['status']} ({len(errors)} errors, {len(warnings)} warnings)")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    for warning in warnings:
        print(f"WARN: {warning}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
