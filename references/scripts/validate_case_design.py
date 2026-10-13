from __future__ import annotations

import argparse
from execution_readiness import validate_case_readiness
import re
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime
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
VALID_PRIORITY_POLICIES = {"strict_p0_p1_p2", "risk_first_configurable"}
NORMALIZATION_MODE = "case_normalization"
NORMALIZATION_CHANGE_TYPES = {"preserved", "revised", "split", "merged", "added"}
CASE_ORDER_POLICY = "prd_port_page_group"
VALID_PRIORITIES = ("P0", "P1", "P2")
PRIORITY_PERCENT_RANGES = {
    "P0": (20.0, 25.0),
    "P1": (35.0, 40.0),
    "P2": (35.0, 40.0),
}
ATOMIC_CONTRACT_VERSION = "1.0"
VALID_RULE_TYPES = {
    "field", "interaction", "display", "query", "permission",
    "state_transition", "calculation", "batch_rule", "file_contract",
    "api_contract", "time_rule", "compatibility", "quality_attribute",
}
VALID_DESIGN_METHODS = {
    "equivalence_partitioning", "boundary_value", "decision_table",
    "scenario", "state_transition", "cause_effect", "error_guessing",
    "pairwise", "permission_matrix", "contract_validation",
}
VALID_COVERAGE_DIMENSIONS = {
    "positive", "negative", "boundary", "permission",
    "state_transition", "compatibility", "usability",
    "data_consistency", "recovery", "security", "performance",
}
CASE_COUNT_CONTRACT_VERSION = "1.0"
# The whole generation/review quality contract (count contract + normative
# ledgers + semantic identity + title/precondition contracts) shares a single
# version switch.  ``quality_contract_version`` is the canonical field name;
# ``case_count_contract_version`` is kept as an accepted alias so artifacts
# produced against the older documentation keep working.  Declaring either one
# enables the same contract - a mismatch between the two is reported instead of
# silently disabling half of the checks.
QUALITY_CONTRACT_VERSION = "1.0"
CANONICAL_CONTRACT_FIELD = "quality_contract_version"
LEGACY_CONTRACT_FIELD = "case_count_contract_version"
CONTRACT_VERSION_FIELDS = (CANONICAL_CONTRACT_FIELD, LEGACY_CONTRACT_FIELD)
CASE_COUNT_DIMENSIONS = (
    "input", "enum", "comparison", "long_flow", "state", "permission",
    "query", "batch", "file", "calculation", "time", "api", "compatibility",
)
# Delivery modes that must declare the contract; only an explicit l2_only run
# may waive it.
CONTRACT_REQUIRED_MODES = {"full", "generation_review"}
CONTRACT_WAIVER_MODES = {"l2_only"}
# Above this many raw errors the console switches to the aggregated view; the
# payload always keeps the complete list.
CONSOLE_ERROR_DETAIL_LIMIT = 300
ERROR_SAMPLE_LIMIT = 3



def resolve_contract_version(
    document: dict[str, Any], prefix: str = "requirements"
) -> tuple[str, str, list[str], list[str]]:
    """Resolve the single quality-contract version switch.

    Returns ``(field_name, version, errors, warnings)``.  Both the canonical
    field and the legacy alias enable the same contract; declaring conflicting
    versions is an error rather than a silent half-activation.
    """
    canonical = text(document.get(CANONICAL_CONTRACT_FIELD))
    legacy = text(document.get(LEGACY_CONTRACT_FIELD))
    errors: list[str] = []
    warnings: list[str] = []
    if canonical and legacy and canonical != legacy:
        errors.append(
            f"{prefix}.{CANONICAL_CONTRACT_FIELD} ({canonical}) and {prefix}.{LEGACY_CONTRACT_FIELD} "
            f"({legacy}) declare conflicting contract versions"
        )
        return CANONICAL_CONTRACT_FIELD, canonical, errors, warnings
    if canonical:
        field, version = CANONICAL_CONTRACT_FIELD, canonical
        if legacy:
            warnings.append(
                f"{prefix}.{LEGACY_CONTRACT_FIELD} is a legacy alias of "
                f"{CANONICAL_CONTRACT_FIELD}; declaring both is redundant and the old field may be removed"
            )
    elif legacy:
        field, version = LEGACY_CONTRACT_FIELD, legacy
        warnings.append(
            f"{prefix}.{LEGACY_CONTRACT_FIELD} is a legacy alias of {CANONICAL_CONTRACT_FIELD}; "
            f"it is enforced as the same contract, but rename it to {CANONICAL_CONTRACT_FIELD} to stay current"
        )
    else:
        field, version = "", ""
    return field, version, errors, warnings


def _coverage_characteristic_units(rule: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Calculate minimum coverage units from an explicit rule feature profile."""
    rid = text(rule.get("rule_id"))
    raw = rule.get("coverage_characteristics")
    if not isinstance(raw, dict):
        return [], ["coverage_characteristics must be a mapping"]
    profile = text(raw.get("profile")).lower()
    errors: list[str] = []
    if not profile:
        errors.append("coverage_characteristics.profile is required")
    applicability = raw.get("applicability")
    if not isinstance(applicability, dict):
        errors.append("coverage_characteristics.applicability must explicitly mark every dimension")
        applicability = {}
    for dimension in CASE_COUNT_DIMENSIONS:
        if not isinstance(applicability.get(dimension), bool):
            errors.append(f"coverage_characteristics.applicability.{dimension} must be true or false")
    reasons = raw.get("applicability_reason")
    if isinstance(reasons, dict):
        for dimension, enabled in applicability.items():
            if enabled and not text(reasons.get(dimension)):
                errors.append(f"coverage_characteristics.applicability_reason.{dimension} is required when dimension is true")
    unsupported = {text(value) for value in as_list(raw.get("unsupported_dimensions")) if text(value)}
    enabled_dimensions = {dimension for dimension, enabled in applicability.items() if enabled is True}
    overlap = sorted(enabled_dimensions & unsupported)
    if overlap:
        errors.append(f"coverage_characteristics.unsupported_dimensions cannot include enabled dimensions: {', '.join(overlap)}")
    profile_dimension = {
        "text_input": "input", "input": "input", "field": "input",
        "enum": "enum", "dropdown": "enum", "select": "enum",
        "comparison": "comparison", "decision_table": "comparison", "decision": "comparison",
        "long_flow": "long_flow", "workflow": "long_flow", "flow": "long_flow",
        "state_machine": "state", "state": "state", "permission": "permission", "authorization": "permission",
        "query": "query", "filter": "query", "search": "query", "batch": "batch", "bulk": "batch",
        "file": "file", "file_contract": "file", "upload": "file", "calculation": "calculation", "formula": "calculation",
        "time": "time", "temporal": "time", "api": "api", "api_contract": "api",
        "compatibility": "compatibility", "cross_port": "compatibility",
    }.get(profile)
    if profile_dimension and applicability.get(profile_dimension) is not True:
        errors.append(f"coverage_characteristics.profile={profile} requires applicability.{profile_dimension}: true")

    def vals(key: str) -> list[str]:
        value = raw.get(key)
        if isinstance(value, list):
            return [text(v) for v in value if text(v)]
        return [text(value)] if text(value) else []

    base: list[str] = ["positive|main_flow|accepted"]
    if profile in {"text_input", "input", "field"}:
        base = [f"positive|valid|{v}|accepted" for v in (vals("valid_partitions") or ["valid"])]
        invalid = vals("invalid_partitions")
        if raw.get("required") is True and not invalid:
            invalid = ["empty", "whitespace"]
        base += [f"negative|invalid|{v}|rejected" for v in invalid]
        boundary = raw.get("boundaries")
        if isinstance(boundary, dict):
            if text(boundary.get("min")):
                base += ["boundary|min_minus_1|rejected", "boundary|min|accepted"]
            if text(boundary.get("max")):
                base += ["boundary|max|accepted", "boundary|max_plus_1|rejected"]
        base += [f"positive|character_type|{v}|accepted" for v in vals("character_types")]
    elif profile in {"enum", "dropdown", "select"}:
        if not vals("options"):
            errors.append("enum profile requires non-empty options")
        if not vals("default_values") and raw.get("has_default") is not False:
            errors.append("enum profile requires default_values or has_default: false")
        base = [f"positive|default|{v}|accepted" for v in vals("default_values")]
        if raw.get("has_default") is False:
            base.append("positive|default|none|not_selected")
        base += [f"positive|option|{v}|accepted" for v in vals("options")]
        base += [f"negative|invalid_option|{v}|rejected" for v in vals("invalid_values")]
    elif profile in {"comparison", "decision_table", "decision"}:
        if not vals("result_branches") and not vals("branches"):
            errors.append("comparison profile requires declared result_branches or branches")
        base = [f"positive|branch|{v}|{text(raw.get('expected_outcome')) or 'accepted'}" for v in (vals("result_branches") or vals("branches") or ["baseline"])]
        if vals("baselines"):
            base += [f"comparison|baseline|{v}|observed" for v in vals("baselines")]
        if vals("thresholds"):
            base += [f"boundary|threshold_{v}|threshold|observed" for v in ("below", "equal", "above")]
    elif profile in {"long_flow", "workflow", "flow"}:
        base = []
        if not any(vals(key) for key in ("states", "alternate_paths", "interruptions", "cancellations", "recovery_paths")):
            errors.append("long_flow profile requires at least one declared flow fact")
        if raw.get("main_flow") is True:
            base.append("positive|main_flow|committed")
        base += [f"state_transition|state|{v}|reached" for v in vals("states")]
        base += [f"negative|alternate|{v}|alternate" for v in vals("alternate_paths")]
        base += [f"recovery|interrupt|{v}|recoverable" for v in vals("interruptions")]
        base += [f"recovery|cancel|{v}|cancelled" for v in vals("cancellations")]
        base += [f"recovery|retry|{v}|resumed" for v in vals("recovery_paths")]
        if raw.get("terminal_persistence") is True:
            base.append("data_consistency|terminal_persistence|terminal|persisted")
    elif profile in {"state_machine", "state"}:
        if not vals("allowed_transitions") and not vals("forbidden_transitions"):
            errors.append("state profile requires allowed_transitions or forbidden_transitions")
        base = [f"state_transition|allowed|{v}|reached" for v in vals("allowed_transitions")]
        base += [f"negative|forbidden|{v}|rejected" for v in vals("forbidden_transitions")]
    elif profile in {"permission", "authorization"}:
        for key in ("roles", "operations", "data_scopes", "outcomes"):
            if not vals(key):
                errors.append(f"permission profile requires non-empty {key}")
            elif key != "roles" and len(vals(key)) != 1:
                errors.append(f"permission profile requires exactly one {key} per atomic rule; split distinct permission facts")
        roles, ops, scopes = vals("roles") or ["role"], vals("operations") or ["operation"], vals("data_scopes") or ["scope"]
        outcomes = vals("outcomes") or ["allowed"]
        # Keep role coverage while avoiding role×operation×scope×outcome
        # Cartesian inflation; each rule's declared operation/scope/outcome
        # remains in one independently judgeable obligation per role.
        base = [f"permission|{ops[0]}|{scopes[0]}|{outcomes[0]}|{r}" for r in roles]
    elif profile in {"query", "filter", "search"}:
        if not vals("hit_conditions") and not vals("miss_conditions") and not vals("required_combinations"):
            errors.append("query profile requires declared hit/miss conditions or combinations")
        base = [f"positive|query|{v}|matched" for v in vals("hit_conditions")]
        base += [f"negative|query|{v}|not_matched" for v in vals("miss_conditions")]
        if raw.get("empty_result") is True:
            base.append("positive|empty_result|none|empty")
        if raw.get("reset_supported") is True:
            base.append("recovery|reset|clear|restored")
        base += [f"positive|combination|{v}|matched" for v in vals("required_combinations")]
    elif profile in {"batch", "bulk"}:
        if not vals("sizes") and not vals("outcomes"):
            errors.append("batch profile requires declared sizes or outcomes")
        base = [f"boundary|batch_size|{v}|observed" for v in vals("sizes")]
        if not base:
            base = ["positive|batch_operation|provided|observed"]
        base += [f"recovery|batch_result|{v}|observed" for v in vals("outcomes")]
    elif profile in {"file", "file_contract", "upload"}:
        if not any(vals(key) for key in ("formats", "invalid_formats", "required_headers_missing", "required_fields_empty", "empty_files", "corrupt_files", "size_boundaries", "duplicate_rows", "partial_failure_modes")):
            errors.append("file profile requires at least one declared format, header, field, size or failure fact")
        base = [f"positive|format|{v}|accepted" for v in vals("formats")]
        if not base and any(vals(key) for key in ("required_headers_missing", "required_fields_empty", "empty_files", "corrupt_files", "size_boundaries", "duplicate_rows", "partial_failure_modes")):
            base = []
        base += [f"negative|format|{v}|rejected" for v in vals("invalid_formats")]
        base += [f"negative|missing_header|{v}|rejected" for v in vals("required_headers_missing")]
        base += [f"negative|empty_field|{v}|rejected" for v in vals("required_fields_empty")]
        base += [f"negative|empty_file|{v}|rejected" for v in vals("empty_files")]
        base += [f"negative|corrupt_file|{v}|rejected" for v in vals("corrupt_files")]
        base += [f"boundary|file_size|{v}|observed" for v in vals("size_boundaries")]
        base += [f"data_consistency|duplicate|{v}|rejected" for v in vals("duplicate_rows")]
        base += [f"recovery|partial_failure|{v}|reported" for v in vals("partial_failure_modes")]
    elif profile in {"calculation", "formula"}:
        if not any(vals(key) for key in ("formula", "included_items", "excluded_items", "zero_values", "rounding_cases", "recalculation_cases")):
            errors.append("calculation profile requires a declared formula or calculation fact")
        base = [f"positive|formula|{v}|calculated" for v in vals("formula")]
        if raw.get("baseline_case") is True:
            base.append("positive|formula|baseline|calculated")
        base += [f"positive|included_item|{v}|included" for v in vals("included_items")]
        base += [f"negative|excluded_item|{v}|excluded" for v in vals("excluded_items")]
        base += [f"boundary|zero|{v}|calculated" for v in vals("zero_values")]
        base += [f"boundary|rounding|{v}|correct" for v in vals("rounding_cases")]
        base += [f"data_consistency|recalculation|{v}|updated" for v in vals("recalculation_cases")]
    elif profile in {"time", "temporal"}:
        if not vals("boundaries") and not vals("rollovers"):
            errors.append("time profile requires declared boundaries or rollovers")
        base = [f"boundary|time|{v}|observed" for v in vals("boundaries")]
        base += [f"boundary|rollover|{v}|consistent" for v in vals("rollovers")]
    elif profile in {"api", "api_contract"}:
        if raw.get("normal_request") is True:
            base = ["positive|request|normal|success"]
        else:
            base = []
        if not any(vals(key) for key in ("required_params", "invalid_params", "auth_cases", "idempotency_cases")) and not base:
            errors.append("api profile requires a declared normal_request or contract case")
        base += [f"negative|required_param|{v}|rejected" for v in vals("required_params")]
        base += [f"negative|invalid_param|{v}|rejected" for v in vals("invalid_params")]
        base += [f"security|auth|{v}|rejected" for v in vals("auth_cases")]
        base += [f"positive|idempotency|{v}|same_result" for v in vals("idempotency_cases")]
    elif profile in {"compatibility", "cross_port"}:
        ports = vals("ports")
        if not ports:
            errors.append("compatibility profile requires non-empty ports")
        base = [f"compatibility|cross_port|{'、'.join(ports) or 'declared_ports'}|consistent"]
    elif profile not in {"ordinary", "general", "interaction", "display"}:
        errors.append(f"unsupported coverage_characteristics.profile: {profile}")

    roles, ports = vals("roles"), vals("ports")
    units = []
    for item in base:
        # Permission rules already encode one role in each base item. Other
        # profiles are intentionally not multiplied by role/port metadata.
        units.append(f"{rid}|{item}")
    units = list(dict.fromkeys(units))
    if not units:
        errors.append("coverage_characteristics calculates zero required units")

    # An explicit unit ledger is evidence and documentation, never an escape
    # hatch from the profile calculation.  The declared signatures must equal
    # the independently derived set and every entry must point back to a PRD
    # fact so a producer cannot report an arbitrary count of one per rule.
    explicit_units = raw.get("units") or raw.get("required_units")
    if explicit_units is not None:
        if not isinstance(explicit_units, list) or not explicit_units:
            errors.append("coverage_characteristics.units must be a non-empty list when provided")
        else:
            declared: list[str] = []
            for index, value in enumerate(explicit_units, 1):
                if not isinstance(value, dict):
                    errors.append(f"coverage_characteristics.units[{index}] must be a mapping with signature and source_fact_refs")
                    continue
                signature = text(value.get("coverage_unit_signature") or value.get("signature") or value.get("id"))
                if signature and not signature.startswith(f"{rid}|"):
                    signature = f"{rid}|{signature}"
                if not signature:
                    errors.append(f"coverage_characteristics.units[{index}] must declare coverage_unit_signature")
                else:
                    declared.append(signature)
                if not as_list(value.get("source_fact_refs") or value.get("source_facts")):
                    errors.append(f"coverage_characteristics.units[{index}] must declare source_fact_refs")
                if not text(value.get("derived_from") or value.get("derivation")):
                    errors.append(f"coverage_characteristics.units[{index}] must declare derived_from")
            declared = list(dict.fromkeys(declared))
            if set(declared) != set(units):
                missing = sorted(set(units) - set(declared))
                extra = sorted(set(declared) - set(units))
                if missing:
                    errors.append(f"coverage_characteristics.units is missing independently calculated signatures: {', '.join(missing)}")
                if extra:
                    errors.append(f"coverage_characteristics.units contains undeclared signatures: {', '.join(extra)}")
    return units, errors


def validate_case_count_contract(
    requirement_doc: dict[str, Any],
    obligations: list[dict[str, Any]],
    test_points: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    delivery_mode: str = "full",
    enforce_missing_contract: bool = True,
) -> tuple[list[str], dict[str, Any]]:
    """Validate the mandatory case-count contract for full generation.

    The only intentional exception is an explicitly declared ``l2_only``
    delivery.  A missing version in a full/generation-review artifact is a
    design error, not an optional feature.
    """
    version_raw = text(requirement_doc.get(CANONICAL_CONTRACT_FIELD)) or text(
        requirement_doc.get(LEGACY_CONTRACT_FIELD)
    )
    if not version_raw:
        if not enforce_missing_contract:
            return [], {"active": False, "legacy": True, "contract_version": None,
                        "calculated_minimum_cases": None, "actual_cases": len(cases),
                        "coverage_surplus": None, "rules_below_minimum": [],
                        "calculation_breakdown": []}
        if text(delivery_mode).lower() in CONTRACT_WAIVER_MODES:
            return [], {"active": False, "waived": True, "waiver_reason": "explicit_l2_only",
                        "contract_version": None, "calculated_minimum_cases": None,
                        "actual_cases": len(cases), "coverage_surplus": None,
                        "rules_below_minimum": [], "calculation_breakdown": []}
        return [
            f"Full/generation-review delivery must declare {CANONICAL_CONTRACT_FIELD}: "
            f"\"{QUALITY_CONTRACT_VERSION}\" ({LEGACY_CONTRACT_FIELD} is accepted as a legacy alias); "
            "only explicit l2_only delivery may waive the contract"
        ], {"active": False, "waived": False, "contract_version": None,
            "calculated_minimum_cases": None, "actual_cases": len(cases),
            "coverage_surplus": None, "rules_below_minimum": [], "calculation_breakdown": []}
    errors: list[str] = []
    version = version_raw
    if version != CASE_COUNT_CONTRACT_VERSION:
        return [f"requirements must declare {CANONICAL_CONTRACT_FIELD}: {CASE_COUNT_CONTRACT_VERSION}"], {
            "active": False, "contract_version": version, "calculated_minimum_cases": 0,
            "actual_cases": len(cases), "coverage_surplus": 0, "rules_below_minimum": [],
            "calculation_breakdown": []}

    rules = [r for r in (requirement_doc.get("atomic_rules") or []) if isinstance(r, dict)]
    expected_by_rule: dict[str, list[str]] = {}
    for rule in rules:
        rid = text(rule.get("rule_id"))
        if not rid or text(rule.get("disposition", "covered")).lower() not in COVERED:
            continue
        units, unit_errors = _coverage_characteristic_units(rule)
        expected_by_rule[rid] = units
        errors.extend(f"Atomic rule {rid} {message}" for message in unit_errors)
    minimum = sum(len(units) for units in expected_by_rule.values())
    errors.extend(
        f"Atomic rule {rid} must declare non-empty coverage_characteristics"
        for rid, units in expected_by_rule.items() if not units
    )
    covered_rule_ids = [text(r.get("rule_id")) for r in rules if text(r.get("rule_id")) and text(r.get("disposition", "covered")).lower() in COVERED]
    if covered_rule_ids and not expected_by_rule:
        errors.append(
            f"{CANONICAL_CONTRACT_FIELD} {QUALITY_CONTRACT_VERSION} requires atomic_rules.coverage_characteristics"
        )
    # A zero lower bound is not a pass: it means the contract could not derive
    # any unit from the declared PRD facts, which is exactly the situation the
    # contract exists to prevent (a 600-case "exhaustive" delivery can otherwise
    # satisfy "N >= 0").
    minimum_derivation = "ok"
    if minimum == 0 and covered_rule_ids:
        minimum_derivation = "zero"
        errors.append(
            "calculated_minimum_cases is 0 while covered atomic rules exist: the contract cannot derive a "
            "lower bound. Declare PRD-backed coverage_characteristics (profile + applicability + profile facts) "
            "for every covered rule instead of relying on an empty minimum"
        )

    def signatures(items: list[dict[str, Any]], label: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
        result: dict[str, dict[str, Any]] = {}
        local_errors: list[str] = []
        for item in items:
            sig = text(item.get("coverage_unit_signature"))
            ident = text(item.get("obligation_id") or item.get("test_point_id") or item.get("case_id")) or "<missing>"
            if not sig:
                local_errors.append(f"{label} {ident} must declare coverage_unit_signature")
            elif sig in result:
                local_errors.append(f"Duplicate coverage_unit_signature across {label}: {sig}")
            else:
                result[sig] = item
        return result, local_errors

    ob_sigs, ob_errors = signatures(obligations, "Coverage obligation")
    tp_sigs, tp_errors = signatures(test_points, "Test point")
    case_sigs, case_errors = signatures(cases, "Case")
    errors.extend(ob_errors + tp_errors + case_errors)
    if version == QUALITY_CONTRACT_VERSION:
        for obligation in obligations:
            oid = text(obligation.get("obligation_id")) if isinstance(obligation, dict) else "<missing>"
            if not as_list(obligation.get("source_fact_refs") or obligation.get("source_facts")):
                errors.append(f"Coverage obligation {oid} must declare source_fact_refs")

    ob_by_id = {text(o.get("obligation_id")): o for o in obligations if isinstance(o, dict)}
    tp_by_id = {text(p.get("test_point_id") or p.get("coverage_id") or p.get("id")): p for p in test_points if isinstance(p, dict)}
    for tp in test_points:
        oid = text(tp.get("obligation_id")); ob = ob_by_id.get(oid)
        if ob and text(ob.get("coverage_unit_signature")) != text(tp.get("coverage_unit_signature")):
            errors.append(f"Test point {text(tp.get('test_point_id'))} coverage_unit_signature must match obligation {oid}")
    for case in cases:
        cid = text(case.get("case_id")); pids = [text(v) for v in as_list(case.get("test_point_id") or case.get("test_point_ids")) if text(v)]
        pid = pids[0] if len(pids) == 1 else ""; tp = tp_by_id.get(pid)
        if tp and text(tp.get("coverage_unit_signature")) != text(case.get("coverage_unit_signature")):
            errors.append(f"Case {cid} coverage_unit_signature must match test point {pid}")

    # Attribute actual units to rules through the existing obligation chain.
    actual_by_rule: dict[str, set[str]] = defaultdict(set)
    for ob in obligations:
        rid = text(ob.get("rule_id")); sig = text(ob.get("coverage_unit_signature"))
        if rid and sig:
            actual_by_rule[rid].add(sig)
    below: list[str] = []
    breakdown: list[dict[str, Any]] = []
    for rid, units in expected_by_rule.items():
        actual_set = actual_by_rule.get(rid, set())
        actual = len(actual_set)
        missing_units = sorted(set(units) - actual_set)
        unexpected_units = sorted(actual_set - set(units))
        if missing_units:
            errors.append(f"Atomic rule {rid} is missing required coverage signatures: {', '.join(missing_units)}")
        if unexpected_units:
            # Extra cases are allowed only as explicitly justified risk
            # coverage.  They must still be one-to-one mapped and auditable;
            # an unexplained extra signature is indistinguishable from
            # keyword/template padding.
            ob_by_sig = {
                text(ob.get("coverage_unit_signature")): ob
                for ob in obligations
                if isinstance(ob, dict) and text(ob.get("coverage_unit_signature")) in unexpected_units
            }
            for signature in unexpected_units:
                obligation = ob_by_sig.get(signature) or {}
                if not text(obligation.get("extra_case_reason")):
                    errors.append(
                        f"Atomic rule {rid} has undeclared coverage signatures: {signature}; "
                        "extra coverage requires extra_case_reason"
                    )
        if actual < len(units):
            below.append(rid)
        breakdown.append({"rule_id": rid, "required_units": len(units), "actual_units": actual,
                          "missing_units": max(0, len(units) - actual), "declared_units": units,
                          "missing_signatures": missing_units, "unexpected_signatures": unexpected_units,
                          "extra_units": len(unexpected_units)})
    for rid in below:
        required = next(item["required_units"] for item in breakdown if item["rule_id"] == rid)
        actual = next(item["actual_units"] for item in breakdown if item["rule_id"] == rid)
        errors.append(f"Atomic rule {rid} coverage units {actual} below minimum {required}")
    actual_cases = len(cases)
    if actual_cases < minimum:
        errors.append(f"actual_cases ({actual_cases}) is below calculated_minimum_cases ({minimum})")
    return errors, {"active": True, "contract_version": version,
                    "calculated_minimum_cases": minimum, "actual_cases": actual_cases,
                    "coverage_surplus": actual_cases - minimum,
                    "minimum_derivation": minimum_derivation,
                    "covered_rule_count": len(covered_rule_ids),
                    "rules_below_minimum": below, "calculation_breakdown": breakdown}


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
    intent = case.get("test_intent") or {}
    profile_name = text(case.get("execution_profile")).lower()
    # File-contract cases must declare their fixture shape; silently accepting
    # a missing contract was allowing upload cases to pass without executable
    # data definitions.
    if raw is None:
        if profile_name == "invoice_scenario" and text(intent.get("expected_outcome")).lower() in {"accepted", "rejected", "observed"} and text(case.get("operation_type")).lower() == "file_upload":
            return [f"Case {text(case.get('case_id')) or '<missing>'} file_upload cases must declare fixture_contract"]
        return []
    case_id = text(case.get("case_id")) or "<missing>"
    if not isinstance(raw, dict):
        return [f"Case {case_id} fixture_contract must be a mapping"]
    errors: list[str] = []
    # Invoice PRDs explicitly require PDF attachments; the previous gate
    # rejected a valid PRD-backed fixture simply because PDF was omitted.
    allowed_formats = {"csv", "xls", "xlsx", "json", "pdf"}
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
    header_not_applicable = raw.get("header_not_applicable") is True
    if not required and not missing and not header_not_applicable:
        errors.append(f"Case {case_id} fixture_contract must declare required_headers or missing_headers")
    if header_not_applicable and (required or missing):
        errors.append(f"Case {case_id} fixture_contract.header_not_applicable cannot be combined with headers")
    valid_categories = {"missing_header_validation", "required_value_validation", "file_parse_error", "file_size_validation", "business_validation"}
    category = text(raw.get("expected_feedback_category"))
    if category and category not in valid_categories:
        errors.append(f"Case {case_id} fixture_contract.expected_feedback_category is unsupported: {category}")
    if category == "required_value_validation" and not empty:
        errors.append(f"Case {case_id} required_value_validation fixture must declare empty_fields")
    if category == "missing_header_validation" and not missing:
        errors.append(f"Case {case_id} missing_header_validation fixture must declare missing_headers")
    return errors


VAGUE_TITLE_PHRASES = (
    "符合预期", "与需求一致", "按规则处理", "按需求处理", "同样处理",
    "功能正确", "正常验证", "业务规则验证", "正常处理",
)


def normalize_semantic_text(value: Any) -> str:
    """Normalize only presentation noise; do not erase business qualifiers."""
    normalized = text(value).lower()
    normalized = re.sub(r"[\s\u3000，。；：、（）()【】\[\]“”‘’'\"<>/\\\-_:,.;!?！？]+", "", normalized)
    return normalized


def semantic_identity_key(case: dict[str, Any]) -> str:
    """Return the normalized identity key, or "" when there is nothing to compare.

    An identity whose every component is blank must not collapse into a single
    shared key: doing so would group every incomplete case into one bogus
    duplicate report and drown the real duplicates.  Missing components are
    reported separately as "must not be blank" errors.
    """
    identity = case.get("semantic_identity") or {}
    if not isinstance(identity, dict):
        return ""
    values = (
        identity.get("port"), identity.get("page"), identity.get("object"),
        identity.get("condition"), identity.get("action"), identity.get("assertion"),
    )
    parts = [normalize_semantic_text(value) for value in values]
    if not any(parts):
        return ""
    return "|".join(parts)


def validate_semantic_quality(
    requirement_doc: dict[str, Any],
    test_points: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    enforce: bool = True,
) -> tuple[list[str], list[str], dict[str, Any]]:
    """Reject unsupported dimensions, vague titles and semantic duplicates."""
    if not enforce:
        return [], [], {
            "enforced": False,
            "semantic_identity_count": 0,
            "identity_missing_count": 0,
            "semantic_duplicate_groups": [],
        }
    errors: list[str] = []
    warnings: list[str] = []
    rules_by_id = {
        text(rule.get("rule_id")): rule
        for rule in requirement_doc.get("atomic_rules", [])
        if isinstance(rule, dict) and text(rule.get("rule_id"))
    }
    obligations_by_id = {
        text(ob.get("obligation_id")): ob
        for ob in requirement_doc.get("coverage_obligations", [])
        if isinstance(ob, dict) and text(ob.get("obligation_id"))
    }
    points_by_id = {
        text(point.get("test_point_id") or point.get("coverage_id")): point
        for point in test_points
        if isinstance(point, dict) and text(point.get("test_point_id") or point.get("coverage_id"))
    }
    point_keys: dict[str, str] = {}
    duplicate_keys: dict[str, list[str]] = defaultdict(list)
    identity_missing: list[str] = []
    identity_incomplete: list[str] = []
    applicability_map = {
        "permission": "permission", "state_transition": "state",
        "compatibility": "compatibility", "security": "permission",
    }
    profile_map = {
        "permission": "permission", "authorization": "permission",
        "state": "state", "state_machine": "state", "long_flow": "long_flow",
        "workflow": "long_flow", "flow": "long_flow", "query": "query",
        "filter": "query", "search": "query", "batch": "batch", "bulk": "batch",
        "file": "file", "file_contract": "file", "upload": "file",
        "calculation": "calculation", "formula": "calculation", "time": "time",
        "temporal": "time", "api": "api", "api_contract": "api",
        "compatibility": "compatibility", "cross_port": "compatibility",
        "comparison": "comparison",
    }
    for case in cases:
        case_id = text(case.get("case_id")) or "<missing>"
        title = text(case.get("formal_case_title") or case.get("case_title") or case.get("title"))
        identity = case.get("semantic_identity")
        if not isinstance(identity, dict):
            identity_missing.append(case_id)
            errors.append(f"Case {case_id} must declare semantic_identity")
        else:
            for key in ("port", "page", "object", "action", "assertion"):
                if not text(identity.get(key)):
                    errors.append(f"Case {case_id} semantic_identity.{key} must not be blank")
            if text(identity.get("condition")) == "" and text(case.get("granularity")).lower() != "scenario":
                errors.append(f"Case {case_id} field/boundary identity must declare semantic_identity.condition")
        key = semantic_identity_key(case)
        if key:
            duplicate_keys[key].append(case_id)
        elif isinstance(identity, dict):
            # Declared but unusable for comparison: report once, keep it out of
            # the duplicate buckets so real duplicates stay visible.
            identity_incomplete.append(case_id)
        if any(phrase in title for phrase in VAGUE_TITLE_PHRASES):
            errors.append(f"Case {case_id} title contains vague phrase; name the object, condition and action explicitly")
        formal_steps = case.get("formal_steps") or case.get("steps")
        if not isinstance(formal_steps, list) or not formal_steps:
            errors.append(f"Case {case_id} must declare non-empty formal_steps")
        elif len(formal_steps) > 5:
            errors.append(f"Case {case_id} formal_steps has {len(formal_steps)} steps; split long flows before review")
        else:
            step_numbers: list[int] = []
            for index, step in enumerate(formal_steps, 1):
                if not isinstance(step, dict):
                    errors.append(f"Case {case_id} formal_steps[{index}] must be a mapping with step_no/action/target")
                    continue
                step_no = step.get("step_no")
                if not isinstance(step_no, int):
                    errors.append(f"Case {case_id} formal_steps[{index}].step_no must be an integer")
                else:
                    step_numbers.append(step_no)
                if not text(step.get("formal_text") or step.get("display_text") or step.get("action")):
                    errors.append(f"Case {case_id} formal_steps[{index}] action text must not be blank")
                if not text(step.get("target")):
                    errors.append(f"Case {case_id} formal_steps[{index}].target must not be blank")
            if step_numbers != list(range(1, len(formal_steps) + 1)):
                errors.append(f"Case {case_id} formal_steps.step_no must be sequential from 1")
        precondition_state = text(case.get("precondition_state")).lower()
        if precondition_state not in {"self_built", "system_preinstalled", "environment_provided"}:
            errors.append(f"Case {case_id} must declare precondition_state: self_built/system_preinstalled/environment_provided")
        elif precondition_state == "self_built" and not isinstance(case.get("data_setup"), dict):
            errors.append(f"Case {case_id} self_built precondition requires a separate data_setup mapping")
        elif precondition_state in {"system_preinstalled", "environment_provided"} and text(case.get("missing_data_behavior")) != "blocked":
            errors.append(f"Case {case_id} precondition_state {precondition_state} requires missing_data_behavior: blocked")
        title_contract = case.get("title_contract")
        if not isinstance(title_contract, dict):
            errors.append(f"Case {case_id} must declare title_contract with object/action/condition")
        else:
            for key_name in ("object", "action"):
                if not text(title_contract.get(key_name)):
                    errors.append(f"Case {case_id} title_contract.{key_name} must not be blank")
            if text(case.get("granularity")).lower() != "scenario" and not text(title_contract.get("condition")):
                errors.append(f"Case {case_id} field/boundary title_contract.condition must not be blank")
        pid = text(case.get("test_point_id"))
        point = points_by_id.get(pid, {})
        oid = text(point.get("obligation_id"))
        obligation = obligations_by_id.get(oid, {})
        rid = text(obligation.get("rule_id"))
        rule = rules_by_id.get(rid, {})
        characteristics = rule.get("coverage_characteristics") or {}
        profile = text(characteristics.get("profile")).lower()
        applicability = characteristics.get("applicability") or {}
        coverage_dimension = text(obligation.get("coverage_dimension"))
        required_dimension = applicability_map.get(coverage_dimension)
        if required_dimension and applicability.get(required_dimension) is not True:
            errors.append(
                f"Case {case_id} uses coverage dimension {coverage_dimension} but rule {rid} "
                f"does not declare applicability.{required_dimension}: true"
            )
        profile_dimension = profile_map.get(profile)
        if profile_dimension and applicability.get(profile_dimension) is not True:
            errors.append(f"Case {case_id} profile {profile} is not applicable to declared rule dimensions")
        # Profile-specific facts are mandatory whenever the corresponding
        # profile is used.  This catches keyword-generated dimensions that have
        # no PRD evidence before they reach the count contract.
        fact_requirements = {
            "permission": ("roles", "operations", "data_scopes"),
            "authorization": ("roles", "operations", "data_scopes"),
            "comparison": ("result_branches",),
            "decision": ("result_branches",),
            "batch": ("sizes", "outcomes"), "bulk": ("sizes", "outcomes"),
            "time": ("boundaries", "rollovers"), "temporal": ("boundaries", "rollovers"),
            "state": ("allowed_transitions", "forbidden_transitions"),
            "state_machine": ("allowed_transitions", "forbidden_transitions"),
            "compatibility": ("ports",), "cross_port": ("ports",),
        }
        required_facts = fact_requirements.get(profile, ())
        if required_facts and not any(as_list(characteristics.get(name)) for name in required_facts):
            errors.append(f"Case {case_id} profile {profile} has no declared PRD fact for its coverage dimension")
    duplicate_groups: list[list[str]] = []
    for key, ids in duplicate_keys.items():
        if len(ids) > 1:
            duplicate_groups.append(ids)
            if len(ids) > 10:
                errors.append(
                    "Semantic duplicate cases share the same port/page/object/condition/action/assertion "
                    f"({len(ids)} cases): " + ", ".join(ids[:10]) + ", ..."
                )
            else:
                errors.append(
                    "Semantic duplicate cases share the same port/page/object/condition/action/assertion: "
                    + ", ".join(ids)
                )
    return errors, warnings, {
        "enforced": True,
        "semantic_identity_count": len(duplicate_keys),
        "identity_missing_count": len(identity_missing),
        "identity_incomplete_ids": identity_incomplete,
        "identity_missing_ids": identity_missing,
        "semantic_duplicate_groups": duplicate_groups,
    }


def validate_source_ledgers(requirement_doc: dict[str, Any], enforce: bool = True) -> tuple[list[str], dict[str, Any]]:
    """Validate the normative statement and version-difference ledgers."""
    if not enforce:
        statements = requirement_doc.get("normative_statements")
        differences = requirement_doc.get("version_differences")
        return [], {
            "enforced": False,
            "normative_statement_count": len(statements) if isinstance(statements, list) else 0,
            "version_difference_count": len(differences) if isinstance(differences, list) else 0,
            "source_coverage_inventory_declared": isinstance(requirement_doc.get("source_coverage_inventory"), dict),
            "ambiguities_declared": isinstance(requirement_doc.get("ambiguities"), list),
        }
    errors: list[str] = []
    inventory = requirement_doc.get("source_coverage_inventory")
    statements = requirement_doc.get("normative_statements")
    if not isinstance(inventory, dict):
        errors.append("requirements.source_coverage_inventory must be a mapping")
        inventory = {}
    if not isinstance(statements, list) or not statements:
        errors.append("requirements.normative_statements must be a non-empty list")
        statements = []
    statement_ids: list[str] = []
    covered_ids: set[str] = set()
    excluded_ids: set[str] = set()
    pending_ids: set[str] = set()
    for item in statements:
        if not isinstance(item, dict):
            errors.append("normative_statements entries must be mappings")
            continue
        sid = text(item.get("statement_id")); statement_ids.append(sid)
        if not sid or not text(item.get("source_document")) or not text(item.get("source_excerpt")):
            errors.append(f"Normative statement {sid or '<missing>'} must include ID, source_document and source_excerpt")
        if not isinstance(item.get("source_locator"), dict) or not any(text(item["source_locator"].get(k)) for k in ("line", "section", "page", "anchor", "sheet", "row")):
            errors.append(f"Normative statement {sid or '<missing>'} must include source_locator")
        elif not text(item["source_locator"].get("line")) and not (text(item["source_locator"].get("section")) and text(item["source_locator"].get("anchor"))) and not (text(item["source_locator"].get("sheet")) and isinstance(item["source_locator"].get("row"), int)):
            errors.append(f"Normative statement {sid or '<missing>'} source_locator must use line, section+anchor or sheet+row")
        disposition = text(item.get("disposition") or "covered").lower()
        if disposition == "covered": covered_ids.add(sid)
        elif disposition in EXPLICITLY_EXCLUDED: excluded_ids.add(sid)
        else: pending_ids.add(sid)
        if disposition in EXPLICITLY_EXCLUDED and not text(item.get("reason") or item.get("notes")):
            errors.append(f"Normative statement {sid or '<missing>'} excluded/pending requires reason")
        if not isinstance(item.get("atomic_rule_ids"), list):
            errors.append(f"Normative statement {sid or '<missing>'} must declare atomic_rule_ids")
    for sid in duplicate_values(statement_ids):
        errors.append(f"Duplicate normative statement IDs: {sid}")
    mapped_ids = {
        text(value)
        for rule in (requirement_doc.get("atomic_rules") or [])
        if isinstance(rule, dict)
        for value in as_list(rule.get("normative_statement_id") or rule.get("normative_statement_ids"))
        if text(value)
    }
    for sid in sorted(covered_ids - mapped_ids):
        errors.append(f"Covered normative statement without atomic rule: {sid}")
    for sid in sorted(mapped_ids - set(statement_ids)):
        errors.append(f"Atomic rules reference unknown normative statement: {sid}")
    actual_rule_ids = {
        text(rule.get("rule_id"))
        for rule in (requirement_doc.get("atomic_rules") or [])
        if isinstance(rule, dict) and text(rule.get("rule_id"))
    }
    for item in statements:
        if not isinstance(item, dict):
            continue
        sid = text(item.get("statement_id"))
        declared_rules = {text(value) for value in as_list(item.get("atomic_rule_ids")) if text(value)}
        unknown_rules = sorted(declared_rules - actual_rule_ids)
        if unknown_rules:
            errors.append(f"Normative statement {sid or '<missing>'} references unknown atomic rules: {', '.join(unknown_rules)}")
        disposition = text(item.get("disposition") or "covered").lower()
        if disposition in EXPLICITLY_EXCLUDED and declared_rules:
            errors.append(f"Excluded/pending normative statement {sid or '<missing>'} must not map covered atomic rules")
    for item in statements:
        if not isinstance(item, dict):
            continue
        sid = text(item.get("statement_id"))
        declared_rules = {text(value) for value in as_list(item.get("atomic_rule_ids")) if text(value)}
        if text(item.get("disposition") or "covered").lower() == "covered" and not declared_rules:
            errors.append(f"Covered normative statement {sid or '<missing>'} must map to atomic_rule_ids")
    for rule in (requirement_doc.get("atomic_rules") or []):
        if not isinstance(rule, dict):
            continue
        rid = text(rule.get("rule_id"))
        for sid in as_list(rule.get("normative_statement_id") or rule.get("normative_statement_ids")):
            sid_text = text(sid)
            statement = next((item for item in statements if isinstance(item, dict) and text(item.get("statement_id")) == sid_text), None)
            if statement is not None and rid not in {text(value) for value in as_list(statement.get("atomic_rule_ids")) if text(value)}:
                errors.append(f"Normative statement {sid_text} atomic_rule_ids does not include {rid}")
    def integer(name: str, expected: int) -> None:
        value = inventory.get(name)
        if value != expected:
            errors.append(f"source_coverage_inventory.{name} must equal {expected}, got {value!r}")
    integer("normative_statement_count", len(statement_ids))
    integer("mapped_statement_count", len(mapped_ids & set(statement_ids)))
    integer("excluded_statement_count", len(excluded_ids))
    integer("pending_statement_count", len(pending_ids))
    if "unmapped_statement_ids" not in inventory:
        errors.append("source_coverage_inventory.unmapped_statement_ids must be declared")
    else:
        declared_unmapped = {text(v) for v in as_list(inventory.get("unmapped_statement_ids")) if text(v)}
        expected_unmapped = set(statement_ids) - mapped_ids - excluded_ids - pending_ids
        if declared_unmapped != expected_unmapped:
            errors.append("source_coverage_inventory.unmapped_statement_ids does not match the normative ledger")
    documents = requirement_doc.get("source_documents") or []
    if len(documents) > 1:
        effective_dates = [text(doc.get("effective_date")) for doc in documents if isinstance(doc, dict)]
        if len(effective_dates) == len(documents) and any(effective_dates[index] > effective_dates[index + 1] for index in range(len(effective_dates) - 1)):
            errors.append("source_documents must be ordered from oldest to newest by effective_date")
        differences = requirement_doc.get("version_differences")
        if not isinstance(differences, list):
            errors.append("requirements.version_differences must be a list when multiple PRD documents are supplied")
            differences = []
        expected_pairs = {
            (text(documents[index].get("document_id")), text(documents[index + 1].get("document_id")))
            for index in range(len(documents) - 1)
            if isinstance(documents[index], dict) and isinstance(documents[index + 1], dict)
        }
        actual_pairs = {(text(item.get("from_document")), text(item.get("to_document"))) for item in differences if isinstance(item, dict)}
        missing_pairs = expected_pairs - actual_pairs
        if missing_pairs:
            errors.append("version_differences is missing adjacent PRD comparisons: " + ", ".join(f"{left}->{right}" for left, right in sorted(missing_pairs)))
        for item in differences:
            if not isinstance(item, dict):
                errors.append("version_differences entries must be mappings")
                continue
            if text(item.get("change_type")) not in {"added", "modified", "superseded", "deprecated", "unchanged", "ambiguous"}:
                errors.append("version_differences.change_type must be added/modified/superseded/deprecated/unchanged/ambiguous")
            if text(item.get("resolution_status")) not in {"resolved", "pending_confirmation", "not_applicable"}:
                errors.append("version_differences.resolution_status must be resolved/pending_confirmation/not_applicable")
            if text(item.get("change_type")) == "ambiguous" and text(item.get("resolution_status")) != "pending_confirmation":
                errors.append("ambiguous version differences must remain pending_confirmation")
            if text(item.get("resolution_status")) == "pending_confirmation" and not text(item.get("resolution")):
                errors.append("pending version differences must declare resolution/confirmation question")
            if text(item.get("from_document")) not in {text(doc.get("document_id")) for doc in documents if isinstance(doc, dict)} or text(item.get("to_document")) not in {text(doc.get("document_id")) for doc in documents if isinstance(doc, dict)}:
                errors.append("version_differences must reference declared source documents")
    if not isinstance(requirement_doc.get("ambiguities"), list):
        errors.append("requirements.ambiguities must be an explicit list, even when empty")
    return errors, {
        "enforced": True,
        "normative_statement_count": len(statement_ids),
        "mapped_statement_count": len(mapped_ids & set(statement_ids)),
        "excluded_statement_count": len(excluded_ids),
        "pending_statement_count": len(pending_ids),
        "version_difference_count": len(requirement_doc.get("version_differences") or []) if isinstance(requirement_doc.get("version_differences"), list) else 0,
    }


def validate_atomic_contract(
    requirement_doc: dict[str, Any],
    requirements: list[dict[str, Any]],
    test_points: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    enforce_quality_fields: bool = True,
) -> tuple[list[str], dict[str, Any]]:
    """Validate the lossless PRD -> atomic rule -> obligation chain.

    This is deliberately structural: summaries cannot satisfy source traceability,
    and a point/case cannot silently cover more than one independently judgeable rule.
    """
    errors: list[str] = []
    contract_version = text(requirement_doc.get("atomicity_contract_version"))
    if contract_version != ATOMIC_CONTRACT_VERSION:
        errors.append(f"requirements must declare atomicity_contract_version: {ATOMIC_CONTRACT_VERSION}")
    sources = requirement_doc.get("source_documents")
    if not isinstance(sources, list) or not sources:
        errors.append("requirements.source_documents must be a non-empty list")
    source_ids = {text(s.get("document_id") or s.get("path") or s.get("name")) for s in (sources or []) if isinstance(s, dict)}
    rules = requirement_doc.get("atomic_rules")
    obligations = requirement_doc.get("coverage_obligations")
    if not isinstance(rules, list) or not rules:
        errors.append("requirements.atomic_rules must be a non-empty list")
        rules = []
    if not isinstance(obligations, list) or not obligations:
        errors.append("requirements.coverage_obligations must be a non-empty list")
        obligations = []
    req_ids = {item_id(r, ("requirement_id", "req_id", "id")) for r in requirements}
    rule_ids: list[str] = []
    rule_req: dict[str, str] = {}
    rules_by_id: dict[str, dict[str, Any]] = {}
    for rule in rules:
        if not isinstance(rule, dict):
            errors.append("atomic_rules entries must be mappings"); continue
        rid = text(rule.get("rule_id")); rule_ids.append(rid)
        rules_by_id[rid] = rule
        if not rid: errors.append("Every atomic rule must declare rule_id")
        req = text(rule.get("requirement_id")); rule_req[rid] = req
        if req not in req_ids: errors.append(f"Atomic rule {rid or '<missing>'} references unknown requirement: {req}")
        src = text(rule.get("source_document") or rule.get("document_id"))
        if src not in source_ids: errors.append(f"Atomic rule {rid or '<missing>'} must reference a declared source document")
        locator = rule.get("source_locator")
        if not isinstance(locator, dict) or not any(text(locator.get(k)) for k in ("section", "page", "line", "anchor", "sheet", "row")):
            errors.append(f"Atomic rule {rid or '<missing>'} must include source_locator (section/page/line/anchor)")
        elif enforce_quality_fields and not text(locator.get("line")) and not (text(locator.get("section")) and text(locator.get("anchor"))) and not (text(locator.get("sheet")) and isinstance(locator.get("row"), int)):
            errors.append(f"Atomic rule {rid or '<missing>'} source_locator must use line, section+anchor or sheet+row")
        if not text(rule.get("source_excerpt")): errors.append(f"Atomic rule {rid or '<missing>'} must include source_excerpt")
        if enforce_quality_fields:
            if not as_list(rule.get("normative_statement_id") or rule.get("normative_statement_ids")):
                errors.append(f"Atomic rule {rid or '<missing>'} must reference normative_statement_id")
            if not as_list(rule.get("source_fact_refs") or rule.get("source_facts")):
                errors.append(f"Atomic rule {rid or '<missing>'} must declare source_fact_refs")
            if not isinstance(rule.get("applicability_reason"), (dict, list)):
                errors.append(f"Atomic rule {rid or '<missing>'} must declare applicability_reason for each dimension")
            if not isinstance(rule.get("unsupported_dimensions"), list):
                errors.append(f"Atomic rule {rid or '<missing>'} must declare unsupported_dimensions")
        if text(rule.get("rule_type")) not in VALID_RULE_TYPES: errors.append(f"Atomic rule {rid or '<missing>'} has invalid rule_type")
        if not text(rule.get("statement")): errors.append(f"Atomic rule {rid or '<missing>'} must include atomic statement")
        coverage_plan = rule.get("coverage_plan")
        if not isinstance(coverage_plan, dict):
            errors.append(f"Atomic rule {rid or '<missing>'} must declare coverage_plan")
        else:
            required_methods = {text(v) for v in as_list(coverage_plan.get("required_design_methods")) if text(v)}
            required_dimensions = {text(v) for v in as_list(coverage_plan.get("required_dimensions")) if text(v)}
            required_classes = {text(v) for v in as_list(coverage_plan.get("required_classes")) if text(v)}
            if not required_methods or not required_dimensions or not required_classes:
                errors.append(f"Atomic rule {rid or '<missing>'} coverage_plan must declare required methods, dimensions, and classes")
            if required_methods - VALID_DESIGN_METHODS:
                errors.append(f"Atomic rule {rid or '<missing>'} coverage_plan has invalid design methods")
            if required_dimensions - VALID_COVERAGE_DIMENSIONS:
                errors.append(f"Atomic rule {rid or '<missing>'} coverage_plan has invalid dimensions")
        if rule.get("disposition", "covered") in EXPLICITLY_EXCLUDED and not text(rule.get("reason")): errors.append(f"Atomic rule {rid or '<missing>'} excluded/pending requires reason")
    for rid in duplicate_values(rule_ids): errors.append(f"Duplicate atomic rule IDs: {rid}")
    obligation_ids: list[str] = []; obligation_rule: dict[str, str] = {}; obligation_point: dict[str, str] = {}; obligations_by_id: dict[str, dict[str, Any]] = {}
    for ob in obligations:
        if not isinstance(ob, dict): errors.append("coverage_obligations entries must be mappings"); continue
        oid = text(ob.get("obligation_id")); obligation_ids.append(oid)
        obligations_by_id[oid] = ob
        if not oid: errors.append("Every coverage obligation must declare obligation_id")
        rid_values = [text(v) for v in as_list(ob.get("rule_id") or ob.get("rule_ids")) if text(v)]
        if len(rid_values) != 1: errors.append(f"Obligation {oid or '<missing>'} must reference exactly one rule_id")
        rid = rid_values[0] if rid_values else ""; obligation_rule[oid] = rid
        if rid not in set(rule_ids): errors.append(f"Obligation {oid or '<missing>'} references unknown atomic rule: {rid}")
        if text(ob.get("design_method")) not in VALID_DESIGN_METHODS: errors.append(f"Obligation {oid or '<missing>'} has invalid design_method")
        if text(ob.get("coverage_dimension")) not in VALID_COVERAGE_DIMENSIONS: errors.append(f"Obligation {oid or '<missing>'} has invalid coverage_dimension")
        for key in ("coverage_class", "verification_target", "polarity", "interface", "expected_outcome"):
            if not text(ob.get(key)): errors.append(f"Obligation {oid or '<missing>'} must declare {key}")
        tp_values = [text(v) for v in as_list(ob.get("test_point_id") or ob.get("test_point_ids")) if text(v)]
        if len(tp_values) > 1: errors.append(f"Obligation {oid or '<missing>'} must map to exactly one test point")
        if tp_values: obligation_point[oid] = tp_values[0]
    for oid in duplicate_values(obligation_ids): errors.append(f"Duplicate obligation IDs: {oid}")
    covered_rules = {text(r.get("rule_id")) for r in rules if isinstance(r, dict) and text(r.get("disposition", "covered")).lower() in COVERED}
    mapped_rules = {obligation_rule.get(oid) for oid in obligation_ids}
    for rid in sorted(covered_rules - mapped_rules): errors.append(f"Covered atomic rule without coverage obligation: {rid}")
    for rid in sorted(covered_rules):
        rule = rules_by_id.get(rid, {}); plan = rule.get("coverage_plan") or {}
        rule_obligations = [o for o in obligations if isinstance(o, dict) and text(o.get("rule_id")) == rid]
        actual_methods = {text(o.get("design_method")) for o in rule_obligations}
        actual_dimensions = {text(o.get("coverage_dimension")) for o in rule_obligations}
        actual_classes = {text(o.get("coverage_class")) for o in rule_obligations}
        for label, required, actual in (
            ("design methods", {text(v) for v in as_list(plan.get("required_design_methods")) if text(v)}, actual_methods),
            ("coverage dimensions", {text(v) for v in as_list(plan.get("required_dimensions")) if text(v)}, actual_dimensions),
            ("coverage classes", {text(v) for v in as_list(plan.get("required_classes")) if text(v)}, actual_classes),
        ):
            missing = sorted(required - actual)
            if missing: errors.append(f"Atomic rule {rid} is missing required {label}: {', '.join(missing)}")
    point_ids = {item_id(p, ("test_point_id", "coverage_id", "id")) for p in test_points}
    point_obligations: dict[str, list[str]] = defaultdict(list)
    for ob in obligations:
        oid = text(ob.get("obligation_id")) if isinstance(ob, dict) else ""
        tp = obligation_point.get(oid)
        if tp: point_obligations[tp].append(oid)
    for p in test_points:
        pid = item_id(p, ("test_point_id", "coverage_id", "id")); vals = [text(v) for v in as_list(p.get("obligation_id") or p.get("obligation_ids")) if text(v)]
        if len(vals) != 1: errors.append(f"Test point {pid or '<missing>'} must map to exactly one obligation_id")
        elif vals[0] not in set(obligation_ids): errors.append(f"Test point {pid} references unknown obligation: {vals[0]}")
        elif obligation_point.get(vals[0]) != pid: errors.append(f"Test point {pid} and obligation {vals[0]} must reference each other")
        if vals and vals[0] in obligations_by_id:
            ob = obligations_by_id[vals[0]]
            for point_key, obligation_key in (("polarity", "polarity"), ("interface", "interface"), ("expected_outcome", "expected_outcome"), ("coverage_class", "coverage_class"), ("field_id", "field_id")):
                if text(p.get(point_key)) != text(ob.get(obligation_key)):
                    errors.append(f"Test point {pid} {point_key} does not match obligation {vals[0]}")
            if text(p.get("verification_target")) != text(ob.get("verification_target")):
                errors.append(f"Test point {pid} verification_target must equal obligation {vals[0]} verification_target")
        if len(point_obligations.get(pid, [])) != 1: errors.append(f"Obligation-to-test-point mapping for {pid} must be one-to-one")
    for case in cases:
        cid = text(case.get("case_id")); pid = text(case.get("test_point_id")); vals = [text(v) for v in as_list(case.get("obligation_id")) if text(v)]
        if len(vals) != 1: errors.append(f"Case {cid or '<missing>'} must declare exactly one obligation_id")
        point = next((p for p in test_points if item_id(p, ("test_point_id", "coverage_id", "id")) == pid), None)
        if vals and point is not None and vals[0] != text(point.get("obligation_id")):
            errors.append(f"Case {cid} obligation_id does not match test point {pid}")
        if pid and vals and vals[0] in obligation_rule and text(case.get("verification_point")) != text(next((o.get("verification_target") for o in obligations if isinstance(o, dict) and text(o.get("obligation_id")) == vals[0]), "")):
            errors.append(f"Case {cid} verification_point must equal its obligation verification_target")
    return errors, {"atomic_rules": len(rules), "coverage_obligations": len(obligations), "source_documents": len(source_ids), "covered_rules_without_obligations": len(covered_rules - mapped_rules)}


_CASE_ID_PATTERN = re.compile(r"\bTC-[A-Za-z0-9_.\-]+\b")
_ID_NORMALIZERS = (
    (re.compile(r"TC-[A-Za-z0-9_.\-]+"), "<case>"),
    (re.compile(r"RULE-[A-Za-z0-9_.\-]+"), "<rule>"),
    (re.compile(r"NS-[A-Za-z0-9_.\-]+"), "<statement>"),
    (re.compile(r"OB-[A-Za-z0-9_.\-]+"), "<obligation>"),
    (re.compile(r"TP-[A-Za-z0-9_.\-]+"), "<testpoint>"),
    (re.compile(r"VD-[A-Za-z0-9_.\-]+"), "<difference>"),
    (re.compile(r'"\s*"'), '""'),
    (re.compile(r"\b\d+\b"), "<n>"),
)


def error_category(message: str) -> str:
    """Collapse an error message into a stable, ID-free category label."""
    label = message
    for pattern, replacement in _ID_NORMALIZERS:
        label = pattern.sub(replacement, label)
    return label.strip()


def summarize_errors(errors: list[str]) -> list[dict[str, Any]]:
    """Group raw errors by category so a 10k-error run stays readable."""
    buckets: dict[str, dict[str, Any]] = {}
    for message in errors:
        category = error_category(message)
        bucket = buckets.setdefault(
            category,
            {"category": category, "count": 0, "case_ids": [], "samples": []},
        )
        bucket["count"] += 1
        for case_id in _CASE_ID_PATTERN.findall(message):
            if case_id not in bucket["case_ids"]:
                bucket["case_ids"].append(case_id)
        if len(bucket["samples"]) < ERROR_SAMPLE_LIMIT:
            bucket["samples"].append(message)
    ordered = sorted(buckets.values(), key=lambda item: (-item["count"], item["category"]))
    for bucket in ordered:
        bucket["affected_case_count"] = len(bucket["case_ids"])
        bucket["case_ids"] = bucket["case_ids"][:20]
    return ordered


def render_error_report(summary: list[dict[str, Any]], errors: list[str], warnings: list[str]) -> str:
    lines = [
        "# 用例设计门禁错误报告（聚合视图）",
        "",
        f"- 原始错误总数：{len(errors)}",
        f"- 错误类别数：{len(summary)}",
        f"- 警告数：{len(warnings)}",
        "",
        "| # | 错误类别 | 命中数 | 涉及用例数 | 样例 |",
        "|---|---|---:|---:|---|",
    ]
    for index, bucket in enumerate(summary, 1):
        sample = "<br>".join(part.replace("|", "\\|") for part in bucket["samples"])
        lines.append(
            f"| {index} | {bucket['category'].replace('|', chr(92) + '|')} | {bucket['count']} | "
            f"{bucket['affected_case_count']} | {sample} |"
        )
    if warnings:
        lines += ["", "## 警告", ""]
        lines += [f"- {warning}" for warning in warnings]
    return "\n".join(lines) + "\n"


def build_legacy_gap_report(
    requirement_doc: dict[str, Any],
    test_point_doc: dict[str, Any],
    case_doc: dict[str, Any],
) -> dict[str, Any]:
    """Describe how far a pre-contract artifact is from the quality contract.

    Used by ``--legacy-audit`` so an existing project gets a prioritised gap
    report instead of ~10k raw errors it cannot action.
    """
    cases = [item for item in (case_doc.get("test_cases") or []) if isinstance(item, dict)]
    points = [item for item in (test_point_doc.get("test_points") or []) if isinstance(item, dict)]
    obligations = [item for item in (requirement_doc.get("coverage_obligations") or []) if isinstance(item, dict)]
    rules = [item for item in (requirement_doc.get("atomic_rules") or []) if isinstance(item, dict)]
    source_meta = case_doc.get("case_source") or {}
    review_audit = case_doc.get("review_audit") or {}
    generation_scope = requirement_doc.get("generation_scope")
    gaps: list[dict[str, Any]] = []

    def record(scope: str, field: str, missing: int, total: int, hint: str) -> None:
        if missing:
            gaps.append({
                "scope": scope, "field": field, "missing": missing, "total": total,
                "ratio": round(missing / total, 4) if total else 1.0, "hint": hint,
            })

    def missing_ids(items: list[dict[str, Any]], predicate) -> list[str]:
        return [
            text(item.get("case_id") or item.get("test_point_id") or item.get("obligation_id") or item.get("rule_id")) or "<missing>"
            for item in items
            if predicate(item)
        ]

    def blank_list(item: dict[str, Any], keys: tuple[str, ...]) -> bool:
        for key in keys:
            if isinstance(item.get(key), list) and item.get(key):
                return False
        return True

    contract_field, contract_version, _, _ = resolve_contract_version(requirement_doc)
    if not contract_version:
        gaps.append({
            "scope": "requirements", "field": CANONICAL_CONTRACT_FIELD, "missing": 1, "total": 1,
            "ratio": 1.0,
            "hint": f'声明 {CANONICAL_CONTRACT_FIELD}: "{QUALITY_CONTRACT_VERSION}" 才能启用质量契约',
        })
    if not isinstance(generation_scope, dict):
        gaps.append({"scope": "requirements", "field": "generation_scope", "missing": 1, "total": 1,
                     "ratio": 1.0, "hint": "生成前必须锁定口径（mode/granularity/source_policy/confirmed）"})
    else:
        for field in ("mode", "granularity", "source_policy", "historical_case_reuse"):
            if not text(generation_scope.get(field)):
                gaps.append({"scope": "requirements", "field": f"generation_scope.{field}", "missing": 1,
                             "total": 1, "ratio": 1.0, "hint": "口径字段缺失"})
        if generation_scope.get("confirmed") is not True:
            gaps.append({"scope": "requirements", "field": "generation_scope.confirmed", "missing": 1,
                         "total": 1, "ratio": 1.0, "hint": "口径必须由人确认后才能开工"})
        if not text(generation_scope.get("confirmed_by")) or not text(generation_scope.get("confirmed_at")):
            gaps.append({"scope": "requirements", "field": "generation_scope.confirmed_by/confirmed_at",
                         "missing": 1, "total": 1, "ratio": 1.0, "hint": "确认留痕缺失（谁确认、何时确认）"})
    statements = requirement_doc.get("normative_statements")
    if not isinstance(statements, list) or not statements:
        gaps.append({"scope": "requirements", "field": "normative_statements", "missing": 1, "total": 1,
                     "ratio": 1.0, "hint": "缺少规范性条款账本，PRD 原文无法逐条对账"})
    if not isinstance(requirement_doc.get("source_coverage_inventory"), dict):
        gaps.append({"scope": "requirements", "field": "source_coverage_inventory", "missing": 1, "total": 1,
                     "ratio": 1.0, "hint": "账本对账清单缺失"})
    if not isinstance(requirement_doc.get("version_differences"), list):
        gaps.append({"scope": "requirements", "field": "version_differences", "missing": 1, "total": 1,
                     "ratio": 1.0, "hint": "多份 PRD 缺相邻版本差异记录"})
    if not isinstance(requirement_doc.get("ambiguities"), list):
        gaps.append({"scope": "requirements", "field": "ambiguities", "missing": 1, "total": 1,
                     "ratio": 1.0, "hint": "歧义清单必须显式声明（可为空）"})
    if text(source_meta.get("quality_contract_version") or source_meta.get("case_count_contract_version")) != QUALITY_CONTRACT_VERSION:
        gaps.append({"scope": "final_cases", "field": "case_source.quality_contract_version",
                     "missing": 1, "total": 1, "ratio": 1.0, "hint": "最终用例必须声明契约版本"})

    record(
        "atomic_rules", "coverage_characteristics",
        len(missing_ids(rules, lambda rule: not isinstance(rule.get("coverage_characteristics"), dict)
                         or not text((rule.get("coverage_characteristics") or {}).get("profile")))),
        len(rules),
        "没有覆盖特征，门禁无法推导 N_min（这是数量震荡的根因）",
    )
    record(
        "coverage_obligations", "coverage_unit_signature",
        len(missing_ids(obligations, lambda item: not text(item.get("coverage_unit_signature")))),
        len(obligations), "覆盖单元签名缺失，义务/测试点/用例无法一对一闭合",
    )
    record(
        "test_points", "coverage_unit_signature",
        len(missing_ids(points, lambda item: not text(item.get("coverage_unit_signature")))),
        len(points), "覆盖单元签名缺失",
    )
    record("test_cases", "coverage_unit_signature",
           len(missing_ids(cases, lambda item: not text(item.get("coverage_unit_signature")))), len(cases),
           "用例未绑定覆盖单元")
    record("test_cases", "semantic_identity",
           len(missing_ids(cases, lambda item: not isinstance(item.get("semantic_identity"), dict))), len(cases),
           "缺语义身份，无法机器判定改写型重复")
    record("test_cases", "title_contract",
           len(missing_ids(cases, lambda item: not isinstance(item.get("title_contract"), dict))), len(cases),
           "标题未结构化（object/action/condition）")
    record("test_cases", "precondition_state",
           len(missing_ids(cases, lambda item: text(item.get("precondition_state")).lower()
                           not in {"self_built", "system_preinstalled", "environment_provided"})), len(cases),
           "前置状态三态未声明")
    record(
        "test_cases", "formal_steps[].target",
        len(missing_ids(cases, lambda item: any(
            isinstance(step, dict) and not text(step.get("target"))
            for step in (item.get("formal_steps") or [])
        ))),
        len(cases), "步骤缺少操作目标，执行器无法定位元素",
    )
    if not isinstance(review_audit.get("per_case_scores"), list) or len(review_audit.get("per_case_scores") or []) != len(cases):
        gaps.append({"scope": "review_audit", "field": "per_case_scores", "missing": 1, "total": 1,
                     "ratio": 1.0, "hint": "逐条 11 维度评分证据缺失"})
    if not isinstance((review_audit.get("reviewer_independence")), dict):
        gaps.append({"scope": "review_audit", "field": "reviewer_independence", "missing": 1, "total": 1,
                     "ratio": 1.0, "hint": "评审独立性未声明"})
    gaps.sort(key=lambda item: (-item["missing"], item["scope"], item["field"]))
    return {
        "enforced": False,
        "compliance": "legacy",
        "declared_contract_field": contract_field or None,
        "declared_contract_version": contract_version or None,
        "gap_count": len(gaps),
        "total_gap_items": sum(item["missing"] for item in gaps),
        "gaps": gaps,
    }


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

def validate_xlsx_source_ledger(
    requirement_doc: dict[str, Any], import_doc: dict[str, Any] | None,
) -> list[str]:
    """Bind baseline facts to the supplied workbook without claiming PRD coverage."""
    if not isinstance(import_doc, dict):
        return ["current_xlsx_baseline requires --existing-case-import"]
    meta = import_doc.get("import_meta") or {}
    source = meta.get("source") or {}
    sheet = text(meta.get("sheet"))
    rows = {item.get("source_row") for item in import_doc.get("imported_cases") or [] if isinstance(item, dict)}
    documents = {
        text(item.get("document_id")): item
        for item in requirement_doc.get("source_documents") or [] if isinstance(item, dict)
    }
    errors: list[str] = []
    if not sheet:
        errors.append("current_xlsx_baseline import_meta.sheet is required")
    for item in [*(requirement_doc.get("normative_statements") or []), *(requirement_doc.get("atomic_rules") or [])]:
        if not isinstance(item, dict):
            continue
        label = text(item.get("statement_id") or item.get("rule_id"))
        document = documents.get(text(item.get("source_document"))) or {}
        if document.get("source_type") != "existing_xlsx_baseline" or text(document.get("sha256")).lower() != text(source.get("sha256")).lower():
            errors.append(f"Baseline fact {label} must bind source_type existing_xlsx_baseline and the imported workbook sha256")
        locator = item.get("source_locator") or {}
        if text(locator.get("sheet")) != sheet or locator.get("row") not in rows:
            errors.append(f"Baseline fact {label} must locate a real imported sheet and row")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate requirements -> test points -> final cases before execution.")
    parser.add_argument("--requirements", required=True)
    parser.add_argument("--test-points", required=True)
    parser.add_argument("--final-cases", required=True)
    parser.add_argument("--existing-case-import")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--legacy-audit",
        action="store_true",
        help="存量产物迁移审计：缺少质量契约字段时不阻断，改为输出合规差距报告",
    )
    parser.add_argument("--error-report", help="把聚合后的错误报告写入该路径（Markdown）")
    parser.add_argument("--verbose-errors", action="store_true", help="无论错误多少都打印全部原始错误")
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

    source_meta = case_doc.get("case_source") or {}
    errors.extend(validate_normalization_trace(cases, source_meta, import_doc))
    profile = test_point_doc.get("coverage_profile") or {}
    generation_scope = requirement_doc.get("generation_scope")
    if not isinstance(generation_scope, dict):
        generation_scope = {}

    # ------------------------------------------------------------------
    # 契约开关解析
    #   P0-1 单一版本号：quality_contract_version 为规范名，case_count_contract_version
    #        作为 legacy 别名同样生效，避免"照文档声明却整份契约空转"。
    #   P0-2 缺失即失败：full / generation_review 未声明契约版本直接 fail，而不是静默 pass。
    # ------------------------------------------------------------------
    contract_field, contract_version, contract_errors, contract_warnings = resolve_contract_version(
        requirement_doc
    )
    errors.extend(contract_errors)
    warnings.extend(contract_warnings)
    case_contract_field, case_contract_version, case_contract_errors, case_contract_warnings = resolve_contract_version(
        source_meta, prefix="case_source"
    )
    errors.extend(case_contract_errors)
    warnings.extend(case_contract_warnings)
    # The switch is authoritative in exactly two places. A third copy somewhere
    # else is how "declared but never read" used to happen, so it is surfaced.
    misplaced_contract_declarations = [
        label
        for label, holder in (
            ("test_points.coverage_profile", profile),
            ("final_cases.case_ordering", case_doc.get("case_ordering")),
            ("final_cases.review_audit", case_doc.get("review_audit")),
            ("final_cases.executor_profiles", case_doc.get("executor_profiles")),
        )
        if isinstance(holder, dict)
        and any(text(holder.get(field)) for field in CONTRACT_VERSION_FIELDS)
    ]
    if misplaced_contract_declarations:
        warnings.append(
            "contract version is declared where the gate does not read it: "
            + ", ".join(misplaced_contract_declarations)
            + f"; declare it at requirements top level and in case_source.{CANONICAL_CONTRACT_FIELD} only"
        )

    delivery_mode = text(
        generation_scope.get("mode") or profile.get("mode") or source_meta.get("mode") or "full"
    ).lower()
    if delivery_mode not in CONTRACT_REQUIRED_MODES | CONTRACT_WAIVER_MODES:
        errors.append("generation/review delivery mode must be full, generation_review or explicit l2_only")
    contract_required = delivery_mode in CONTRACT_REQUIRED_MODES
    l2_scope = delivery_mode in CONTRACT_WAIVER_MODES
    contract_declared = bool(contract_version)
    contract_waived = l2_scope and not contract_declared
    contract_valid = contract_version == QUALITY_CONTRACT_VERSION
    if contract_declared and not contract_valid:
        errors.append(
            f'{contract_field} must be "{QUALITY_CONTRACT_VERSION}", got {contract_version!r}'
        )
    quality_enabled = contract_valid
    missing_contract_message = (
        f"full/generation_review delivery must declare {CANONICAL_CONTRACT_FIELD}: "
        f"\"{QUALITY_CONTRACT_VERSION}\" ({LEGACY_CONTRACT_FIELD} is accepted as a legacy alias); "
        "only an explicit l2_only scope may waive the quality contract"
    )
    if contract_required and not contract_declared:
        if args.legacy_audit:
            warnings.append("legacy-audit did not block: " + missing_contract_message)
        else:
            errors.append(missing_contract_message)
    if args.legacy_audit:
        # 审计模式只跑结构校验，质量契约缺口交由差距报告呈现
        quality_enabled = False

    executor_profiles, profile_errors = validate_executor_profiles(case_doc.get("executor_profiles"))
    errors.extend(profile_errors)
    atomic_errors, atomic_counts = validate_atomic_contract(
        requirement_doc, requirements, test_points, cases,
        enforce_quality_fields=quality_enabled,
    )
    errors.extend(atomic_errors)
    baseline_only = (
        text(source_meta.get("generation_mode")) == NORMALIZATION_MODE
        and text(source_meta.get("requirement_source")) == "existing_xlsx_baseline"
    )
    if not isinstance(requirement_doc.get("generation_scope"), dict):
        if quality_enabled:
            errors.append("requirements.generation_scope must declare the current generation/review scope")
    elif quality_enabled:
        if text(generation_scope.get("mode")) not in {"generation_review", "full", "l2_only"}:
            errors.append("generation_scope.mode must be generation_review/full/l2_only")
        if text(generation_scope.get("mode")) in CONTRACT_REQUIRED_MODES and text(generation_scope.get("granularity")) != "l2_plus_l3":
            errors.append("generation_scope.granularity must be l2_plus_l3 for full/generation_review")
        expected_policy = "current_xlsx_baseline" if baseline_only else "current_prd_only"
        if text(generation_scope.get("source_policy")) != expected_policy:
            errors.append(f"generation_scope.source_policy must be {expected_policy}")
        allowed_reuse = {"forbidden"}
        if text(source_meta.get("generation_mode")) == NORMALIZATION_MODE:
            allowed_reuse.add("supplied_baseline_only")
        if generation_scope.get("historical_case_reuse") not in allowed_reuse:
            errors.append("generation_scope.historical_case_reuse must be forbidden or explicitly supplied_baseline_only in normalization mode")
        if baseline_only:
            errors.extend(validate_xlsx_source_ledger(requirement_doc, import_doc))
        if generation_scope.get("confirmed") is not True:
            errors.append("generation_scope.confirmed must be true before generation")
        else:
            # P2-9 留痕：布尔值无法追溯"谁在何时确认了口径"
            if not text(generation_scope.get("confirmed_by")):
                errors.append("generation_scope.confirmed_by must record who confirmed the generation scope")
            confirmed_at = text(generation_scope.get("confirmed_at"))
            if not confirmed_at:
                errors.append("generation_scope.confirmed_at must record when the generation scope was confirmed")
            else:
                try:
                    datetime.fromisoformat(confirmed_at)
                except ValueError:
                    errors.append("generation_scope.confirmed_at must be a valid ISO 8601 date or datetime")
        estimated_range = generation_scope.get("estimated_case_range")
        if not isinstance(estimated_range, dict) or not isinstance(estimated_range.get("min"), int) or not isinstance(estimated_range.get("max"), int) or estimated_range.get("min") < 0 or estimated_range.get("max") < estimated_range.get("min"):
            errors.append("generation_scope.estimated_case_range must declare non-negative min/max")
    count_requirement_doc = requirement_doc
    if args.legacy_audit:
        count_requirement_doc = {key: value for key, value in requirement_doc.items() if key not in CONTRACT_VERSION_FIELDS}
    case_count_errors, case_count = validate_case_count_contract(
        count_requirement_doc,
        [item for item in (requirement_doc.get("coverage_obligations") or []) if isinstance(item, dict)],
        test_points,
        cases,
        delivery_mode=delivery_mode,
        enforce_missing_contract=quality_enabled or (contract_waived and not args.legacy_audit),
    )
    errors.extend(case_count_errors)
    # P2-10 预估区间与门禁下限交叉校验：提前拦截"预估 5758 / 实际 1465"这类背离
    estimate_note = ""
    estimated_range = generation_scope.get("estimated_case_range")
    calculated_minimum = case_count.get("calculated_minimum_cases")
    if isinstance(estimated_range, dict) and isinstance(calculated_minimum, int) and calculated_minimum > 0:
        declared_max = estimated_range.get("max")
        if isinstance(declared_max, int) and 0 < declared_max < calculated_minimum:
            estimate_note = "below_minimum"
            warnings.append(
                f"generation_scope.estimated_case_range.max ({declared_max}) is below the independently "
                f"calculated minimum ({calculated_minimum}); an estimate can never replace the contract lower bound"
            )
        elif isinstance(estimated_range.get("min"), int) and not (
            estimated_range["min"] <= len(cases) <= declared_max
        ):
            estimate_note = "outside_declared_range"
            warnings.append(
                f"actual_cases ({len(cases)}) is outside generation_scope.estimated_case_range "
                f"[{estimated_range.get('min')}, {declared_max}]; explain the deviation in the review"
            )
        else:
            estimate_note = "consistent"

    ledger_errors, ledger_counts = validate_source_ledgers(requirement_doc, enforce=quality_enabled)
    errors.extend(ledger_errors)
    semantic_errors, semantic_warnings, semantic_counts = validate_semantic_quality(
        requirement_doc, test_points, cases, enforce=quality_enabled
    )
    errors.extend(semantic_errors)
    warnings.extend(semantic_warnings)

    if text(source_meta.get("artifact_role")).lower() != "final":
        errors.append("final-cases must declare case_source.artifact_role: final")
    expected_artifact_mode = "l2_only" if l2_scope else "full"
    if text(source_meta.get("mode")).lower() != expected_artifact_mode:
        errors.append(f"final-cases must declare case_source.mode: {expected_artifact_mode}")
    if quality_enabled and case_contract_version != QUALITY_CONTRACT_VERSION:
        errors.append(
            f"final-cases must declare case_source.{CANONICAL_CONTRACT_FIELD}: {QUALITY_CONTRACT_VERSION}"
            f" ({case_contract_field or 'no contract version declared'})"
        )
    if quality_enabled and text(source_meta.get("generated_from")) != "05_test_points.yaml":
        errors.append("final-cases must declare case_source.generated_from: 05_test_points.yaml")

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

    if text(profile.get("mode")).lower() != expected_artifact_mode:
        errors.append(f"test-points must declare coverage_profile.mode: {expected_artifact_mode}")
    core_form_fields_present = profile.get("core_form_fields_present")
    if not isinstance(core_form_fields_present, bool):
        errors.append("coverage_profile.core_form_fields_present must be true or false")
    required_granularities = {granularity({"granularity": value}) for value in as_list(profile.get("required_granularities"))}
    invalid_required_granularities = sorted(required_granularities - {"scenario", "field"})
    if invalid_required_granularities:
        errors.append(f"coverage_profile.required_granularities contains invalid values: {', '.join(invalid_required_granularities)}")
    declared_priority_policy = text(profile.get("priority_policy")).lower()
    source_priority_policy = text(source_meta.get("priority_policy")).lower()
    if declared_priority_policy not in VALID_PRIORITY_POLICIES:
        add_priority_error(
            "test-points must declare coverage_profile.priority_policy: "
            + " or ".join(sorted(VALID_PRIORITY_POLICIES))
        )
    if source_priority_policy not in VALID_PRIORITY_POLICIES:
        add_priority_error(
            "final-cases must declare case_source.priority_policy: "
            + " or ".join(sorted(VALID_PRIORITY_POLICIES))
        )
    if declared_priority_policy and source_priority_policy and declared_priority_policy != source_priority_policy:
        add_priority_error("coverage_profile.priority_policy and case_source.priority_policy must match")

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
    if not quality_enabled and not l2_scope and len(cases) <= len(requirements):
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
    if declared_priority_policy == "strict_p0_p1_p2" and 0 < len(cases) < len(VALID_PRIORITIES):
        add_priority_error(
            f"Full delivery must contain at least {len(VALID_PRIORITIES)} final cases "
            "to represent P0, P1, and P2"
        )
    if len(cases) >= len(VALID_PRIORITIES) and declared_priority_policy == "strict_p0_p1_p2":
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
    elif len(cases) >= len(VALID_PRIORITIES):
        for priority in VALID_PRIORITIES:
            count = priority_counts.get(priority, 0)
            lower, upper = priority_bounds[priority]
            if count < lower or count > upper:
                warnings.append(
                    f"Risk-first priority advisory: {priority} count {count}/{len(cases)} "
                    f"({priority_percentages[priority]:.2f}%) is outside indicative {lower}-{upper}; "
                    "business-risk rationale remains authoritative"
                )
    review_audit = case_doc.get("review_audit") or {}
    if not isinstance(review_audit, dict):
        errors.append("final-cases review_audit must be a mapping")
        review_audit = {}
    if declared_priority_policy == "risk_first_configurable":
        missing_priorities = [priority for priority in VALID_PRIORITIES if not priority_counts.get(priority)]
        rationale = review_audit.get("priority_coverage_rationale") or {}
        for priority in missing_priorities:
            if not isinstance(rationale, dict) or not text(rationale.get(priority)):
                message = f"review_audit.priority_coverage_rationale.{priority} must explain why this risk level has no cases"
                if quality_enabled:
                    add_priority_error(message)
                else:
                    warnings.append(message)
    reviewer_independence = review_audit.get("reviewer_independence")
    if quality_enabled and not isinstance(reviewer_independence, dict):
        errors.append("review_audit.reviewer_independence must declare an independent review")
        reviewer_independence = {}
    elif quality_enabled:
        if text(reviewer_independence.get("mode")) != "independent":
            errors.append("review_audit.reviewer_independence.mode must be independent")
        source_artifacts = {text(value) for value in as_list(reviewer_independence.get("source_artifacts")) if text(value)}
        review_source = "current_xlsx_baseline" if baseline_only else "current_prd"
        if not {review_source, "normative_statements"}.issubset(source_artifacts):
            errors.append(f"independent review must read {review_source} and normative_statements")
        if reviewer_independence.get("generated_cases_used_as_scope_source") is not False:
            errors.append("review must not use generated cases as the review scope source")
        if text(reviewer_independence.get("semantic_duplicate_review")) != "completed":
            errors.append("review_audit must record semantic_duplicate_review: completed")
    sampled_case_ids = [text(value) for value in as_list(review_audit.get("environment_sample_case_ids")) if text(value)]
    probes = [value for value in as_list(review_audit.get("environment_probes")) if isinstance(value, dict)]
    minimum_sample = max(1, math.ceil(len(cases) * 0.10)) if cases else 0
    p0_ids = [case_id for case, case_id in zip(cases, case_ids) if normalized_priority(case) == "P0"]
    minimum_p0_sample = max(1, math.ceil(len(p0_ids) * 0.20)) if p0_ids else 0
    sampled_p0 = set(sampled_case_ids).intersection(p0_ids)
    review_execution_scope = text(review_audit.get("execution_scope") or delivery_mode)
    # The declared review scope drives this branch, not the contract switch:
    # whether the quality contract is enforced must never change which review
    # rules apply (that coupling is what hid the contract in the first place).
    if review_execution_scope in {"generation_review", "generation_only"}:
        if text(review_audit.get("environment_sampling_status")).lower() != "not_applicable":
            errors.append("generation_review review_audit.environment_sampling_status must be not_applicable")
        if sampled_case_ids or probes:
            errors.append("generation_review must not claim environment probes; defer them to execution")
    else:
        if len(set(sampled_case_ids)) < minimum_sample:
            errors.append(f"review_audit must sample at least {minimum_sample} cases for environment executability")
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
    per_case_scores = review_audit.get("per_case_scores")
    if quality_enabled and (not isinstance(per_case_scores, list) or len(per_case_scores) != len(cases)):
        errors.append("review_audit.per_case_scores must contain one evidence-backed score for every case")
    elif quality_enabled:
        score_case_ids = set()
        derived_scores: dict[str, float] = {}
        required_dimensions = {
            "requirement_coverage", "business_correctness", "step_clarity", "assertability",
            "data_executability", "dependency_visibility", "automation_readiness", "no_redundancy",
            "quality_attribute_coverage", "ordering_consistency", "field_coverage",
        }
        for score_item in per_case_scores:
            if not isinstance(score_item, dict):
                errors.append("review_audit.per_case_scores entries must be mappings")
                continue
            score_case_id = text(score_item.get("case_id")); score_case_ids.add(score_case_id)
            if score_case_id not in set(case_ids):
                errors.append(f"review_audit.per_case_scores references unknown case: {score_case_id}")
            dimension_scores = score_item.get("dimension_scores")
            if not isinstance(dimension_scores, dict) or set(dimension_scores) != required_dimensions:
                errors.append(f"review_audit.per_case_scores {score_case_id} must include all 11 dimension scores")
            elif any(not isinstance(value, int) or value < 0 or value > 2 for value in dimension_scores.values()):
                errors.append(f"review_audit.per_case_scores {score_case_id} dimension scores must be integers from 0 to 2")
            elif isinstance(score_item.get("score"), (int, float)) and score_item.get("score") != sum(dimension_scores.values()):
                errors.append(f"review_audit.per_case_scores {score_case_id} score must equal the sum of its 11 dimension scores")
            if not text(score_item.get("evidence")):
                errors.append(f"review_audit.per_case_scores {score_case_id} must include evidence")
            score = score_item.get("score")
            if not isinstance(score, (int, float)) or score < 0 or score > 22:
                errors.append(f"review_audit.per_case_scores {score_case_id} score must be between 0 and 22")
            else:
                derived_scores[score_case_id] = float(score)
        if len(score_case_ids) != len(cases):
            errors.append("review_audit.per_case_scores must not omit or duplicate case IDs")
        if isinstance(score_distribution, dict):
            reported_perfect = score_distribution.get("perfect_scores")
            actual_perfect = sum(score == 22 for score in derived_scores.values())
            if isinstance(reported_perfect, int) and reported_perfect != actual_perfect:
                errors.append("review_audit.score_distribution.perfect_scores must equal per_case_scores")
    if scenario_count == 0:
        errors.append("Full delivery must include scenario-level cases")
    if not l2_scope and core_form_fields_present and field_count == 0:
        errors.append("Core form fields are present, but no field-level cases exist")
    actual_granularities = {value for value, count in (("scenario", scenario_count), ("field", field_count)) if count > 0}
    expected_granularities = {"scenario"} if l2_scope else required_granularities
    missing_granularities = sorted(expected_granularities - actual_granularities)
    if missing_granularities:
        errors.append(f"Full delivery is missing required granularities: {', '.join(missing_granularities)}")

    field_specs = test_point_doc.get("field_coverage") or []
    if not l2_scope and core_form_fields_present and not field_specs:
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

    legacy_report: dict[str, Any] = {}
    if args.legacy_audit:
        legacy_report = build_legacy_gap_report(requirement_doc, test_point_doc, case_doc)
    error_summary = summarize_errors(errors)
    quality_contract_payload = {
        "canonical_field": CANONICAL_CONTRACT_FIELD,
        "declared_field": contract_field or None,
        "declared_version": contract_version or None,
        "case_source_declared_field": case_contract_field or None,
        "case_source_declared_version": case_contract_version or None,
        "delivery_mode": delivery_mode,
        "required": contract_required,
        "waived": contract_waived,
        "enforced": quality_enabled,
        "status": (
            "enforced" if quality_enabled
            else "waived_l2_only" if contract_waived and not contract_declared
            else "declared_but_unenforced" if args.legacy_audit and contract_declared
            else "missing" if not contract_declared
            else "version_mismatch"
        ),
        "estimate_vs_minimum": estimate_note,
        "misplaced_declarations": misplaced_contract_declarations,
    }

    payload = {
        "gate": "case_design",
        "status": "fail" if errors else "pass",
        "mode": "full",
        "contract_version": "2.0",
        "inputs": {
            "requirements": source_record(requirement_path),
            "test_points": source_record(test_point_path),
            "final_cases": source_record(final_case_path),
            **({"existing_case_import": source_record(existing_case_import_path)} if existing_case_import_path else {}),
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
            **atomic_counts,
        },
        "atomic_coverage": {
            "contract_version": text(requirement_doc.get("atomicity_contract_version")),
            "status": "fail" if atomic_errors else "pass",
            "errors": atomic_errors,
        },
        "case_count_contract_version": case_count.get("contract_version"),
        "calculated_minimum_cases": case_count.get("calculated_minimum_cases"),
        "actual_cases": case_count.get("actual_cases"),
        "coverage_surplus": case_count.get("coverage_surplus"),
        "rules_below_minimum": case_count.get("rules_below_minimum", []),
        "calculation_breakdown": case_count.get("calculation_breakdown", []),
        "case_count_contract": case_count,
        "source_ledgers": ledger_counts,
        "semantic_quality": semantic_counts,
        "generation_scope": {
            "mode": delivery_mode,
            "granularity": text(generation_scope.get("granularity")),
        },
        "priority_policy": {
            "name": declared_priority_policy,
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
            "execution_scope": review_execution_scope,
        },
        "semantic_contracts": {
            case_id: semantic_contract_hash(case)
            for case, case_id in zip(cases, case_ids)
            if case_id
        },
        "quality_contract": quality_contract_payload,
        "error_summary": error_summary,
        "legacy_audit": legacy_report,
        "errors": errors,
        "warnings": warnings,
    }
    write_json(output_path, payload)
    print(f"case-design gate: {payload['status']} ({len(errors)} errors, {len(warnings)} warnings)")
    if args.error_report:
        report_path = Path(args.error_report).resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_error_report(error_summary, errors, warnings), encoding="utf-8")
        print(f"Grouped error report: {report_path}")
    print_errors_in_full = args.verbose_errors or len(errors) <= CONSOLE_ERROR_DETAIL_LIMIT
    if print_errors_in_full:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
    else:
        print(
            f"ERROR: {len(errors)} raw errors suppressed; showing {len(error_summary)} aggregated categories "
            f"(pass --verbose-errors or --error-report <path> for the full detail)",
            file=sys.stderr,
        )
        for bucket in error_summary[:20]:
            print(
                f"ERROR[{bucket['count']:>6}] {bucket['category']} | sample: {bucket['samples'][0]}",
                file=sys.stderr,
            )
        if len(error_summary) > 20:
            print(f"ERROR: ... {len(error_summary) - 20} more categories", file=sys.stderr)
    for warning in warnings:
        print(f"WARN: {warning}")
    if legacy_report:
        gaps = legacy_report.get("gaps", [])
        print(
            f"LEGACY-AUDIT: {len(gaps)} gap categories, "
            f"{legacy_report.get('total_gap_items', 0)} missing field instances"
        )
        for gap in gaps[:15]:
            print(
                f"GAP: {gap['scope']}.{gap['field']} missing {gap['missing']}/{gap['total']} - {gap['hint']}"
            )
        if len(gaps) > 15:
            print(f"GAP: ... {len(gaps) - 15} more gap categories")
        print(
            "LEGACY-AUDIT: 本次未按质量契约强制，产物不能视为契约合规；"
            "补齐后去掉 --legacy-audit 重新门禁"
        )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
