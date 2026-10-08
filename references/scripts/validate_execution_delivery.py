from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

from semantic_contract import (
    evaluate_observation_results,
    has_observation_value,
    normalized_contract_observations,
    normalized_test_intent,
    observation_values,
    semantic_contract_hash,
    values_equal,
)
from execution_quality import (
    P0_STOP_ACTUAL,
    P0_STOP_REASON,
    evaluate_p0_stop_gate,
    execution_metrics,
    execution_minimum_attempts,
    retry_decision,
    validate_input_hashes,
    validate_priority_execution_policy,
)
from workflow_gate_common import (
    as_list,
    list_from,
    load_yaml,
    normalize_execution_status,
    sha256_file,
    source_record,
    text,
    verify_passed_gate,
    write_json,
)


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
BARE_RESULTS = {"pass", "passed", "fail", "failed", "blocked", "not_run", "通过", "失败", "阻塞", "未执行"}
DOM_MARKERS = ("<html", "<body", "__next_data__", "document.queryselector", "webpack")
MAX_ACTUAL_RESULT_LENGTH = 500


def duplicate_values(values: list[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if value and count > 1)


def attempt_statuses(item: dict[str, Any]) -> list[str]:
    attempts = [attempt for attempt in as_list(item.get("attempts")) if isinstance(attempt, dict)]
    return [normalize_execution_status(attempt.get("status")) for attempt in attempts]


def required_produced_keys(case: dict[str, Any]) -> list[str]:
    contract = case.get("result_contract") or {}
    return [text(value) for value in as_list(contract.get("required_produced_keys")) if text(value)]


def validate_result_summary(case_id: str, item: dict[str, Any], status: str) -> list[str]:
    errors: list[str] = []
    summary = item.get("result_summary")
    if not isinstance(summary, dict):
        return [f"Case {case_id} result_summary must be a mapping"]
    for field in ("expected", "actual", "key_message"):
        if not text(summary.get(field)):
            errors.append(f"Case {case_id} result_summary.{field} must not be blank")
    if status in {"passed", "failed"} and "mutation" not in summary:
        errors.append(f"Case {case_id} result_summary must include mutation")
    if status == "failed" and not text(item.get("defect_summary")):
        errors.append(f"Failed case {case_id} must include defect_summary")
    if status == "failed":
        defect = item.get("defect")
        if not isinstance(defect, dict):
            errors.append(f"Failed case {case_id} defect must be a mapping")
        else:
            for field in ("severity_suggestion", "confirmation_status", "owner", "defect_status", "regression_status"):
                if not text(defect.get(field)):
                    errors.append(f"Failed case {case_id} defect.{field} must not be blank")
    actual = text(item.get("actual_result"))
    if len(actual) > MAX_ACTUAL_RESULT_LENGTH:
        errors.append(f"Case {case_id} actual_result exceeds {MAX_ACTUAL_RESULT_LENGTH} characters")
    lowered = actual.lower()
    if any(marker in lowered for marker in DOM_MARKERS):
        errors.append(f"Case {case_id} actual_result contains raw DOM; keep it in logs instead")
    return errors


def backfill_actual_result(item: dict[str, Any]) -> str:
    summary = item.get("result_summary") or {}
    if not isinstance(summary, dict):
        return text(item.get("actual_result"))
    labels = (
        ("预期", "expected"),
        ("实际", "actual"),
        ("关键消息", "key_message"),
        ("写入结果", "mutation"),
        ("业务对象", "object_id"),
    )
    value = "；".join(
        f"{label}:{text(summary.get(key))}"
        for label, key in labels
        if text(summary.get(key))
    )
    return value or text(item.get("actual_result"))


def normalized_intent_snapshot(raw: Any) -> dict[str, Any]:
    value = raw if isinstance(raw, dict) else {}
    return {
        "kind": text(value.get("kind")).lower(),
        "polarity": text(value.get("polarity")).lower(),
        "interface": text(value.get("interface")).lower(),
        "field_id": text(value.get("field_id")),
        "input_class": text(value.get("input_class")),
        "expected_outcome": text(value.get("expected_outcome")).lower(),
    }


def validate_execution_trace(
    case_id: str,
    trace: Any,
    plan: dict[str, Any],
    label: str,
) -> list[str]:
    if not isinstance(trace, dict):
        return [f"Case {case_id} {label} must include execution_trace"]
    errors: list[str] = []
    expected = {
        "execution_profile": text(plan.get("execution_profile")),
        "executor_capability": text(plan.get("executor_capability")).lower(),
        "primary_channel": text(plan.get("primary_channel")).lower(),
    }
    actual = {
        "execution_profile": text(trace.get("execution_profile")),
        "executor_capability": text(trace.get("executor_capability")).lower(),
        "primary_channel": text(trace.get("primary_channel")).lower(),
    }
    if actual != expected:
        errors.append(f"Case {case_id} {label} execution_trace does not match the gated plan")
    if normalized_intent_snapshot(trace.get("intent_snapshot")) != normalized_intent_snapshot(plan.get("intent_snapshot")):
        errors.append(f"Case {case_id} {label} intent trace does not match the gated plan")
    return errors


def validate_executed_payload(
    case: dict[str, Any],
    payload: dict[str, Any],
    screenshot_names: set[str],
    label: str,
    status: str,
) -> tuple[list[str], list[str]]:
    case_id = text(case.get("case_id"))
    errors, raw_mismatches = evaluate_observation_results(
        case, payload.get("observations"), screenshot_names
    )
    errors.extend(validate_feedback_observation(case, payload, screenshot_names, label))
    soft_keys = {
        item["key"]
        for item in normalized_contract_observations(case)
        if item.get("assertion_class") == "soft"
    }
    mismatches = []
    soft_mismatches = []
    for mismatch in raw_mismatches:
        if any(f" observation {key} " in mismatch for key in soft_keys):
            soft_mismatches.append(mismatch)
        else:
            mismatches.append(mismatch)
    if soft_mismatches:
        payload.setdefault("soft_mismatches", []).extend(soft_mismatches)
    if label:
        errors = [error.replace(f"Case {case_id}", f"Case {case_id} {label}", 1) for error in errors]
        mismatches = [
            mismatch.replace(f"Case {case_id}", f"Case {case_id} {label}", 1)
            for mismatch in mismatches
        ]
    produced_data = payload.get("produced_data") or payload.get("produced") or {}
    if requires_change_evidence(case):
        comparison = payload.get("change_comparison")
        if not isinstance(comparison, dict):
            errors.append(f"Case {case_id} {label} change evidence requires change_comparison mapping")
        else:
            has_before = has_observation_value(comparison.get("before_value")) or has_observation_value(comparison.get("before_state"))
            has_after = has_observation_value(comparison.get("after_value")) or has_observation_value(comparison.get("after_state"))
            if not has_before or not has_after:
                errors.append(f"Case {case_id} {label} change_comparison must include before/after value or state")
            if not isinstance(comparison.get("changed"), bool):
                errors.append(f"Case {case_id} {label} change_comparison.changed must be boolean")
            if comparison.get("comparison_verified") is not True:
                errors.append(f"Case {case_id} {label} change_comparison.comparison_verified must be true")
    if not isinstance(produced_data, dict):
        return [*errors, f"Case {case_id} {label} produced_data must be a mapping"], mismatches
    observed_values = observation_values(payload.get("observations"))
    if set(produced_data) != set(observed_values):
        errors.append(
            f"Case {case_id} {label} produced_data keys must exactly match structured observations"
        )
    for key, observed_value in observed_values.items():
        if key not in produced_data or not values_equal(produced_data.get(key), observed_value):
            errors.append(
                f"Case {case_id} {label} produced_data.{key} must equal its structured observation"
            )
    if status == "passed":
        errors.extend(mismatches)
    elif status == "failed" and not mismatches:
        errors.append(
            f"Case {case_id} {label} is failed but its observations satisfy the result contract"
        )
    return errors, mismatches


def requires_ui_screenshot(case: dict[str, Any], status: str, attempts: list[Any]) -> bool:
    interface = text((case.get("test_intent") or {}).get("interface")).lower()
    has_executed_attempt = any(isinstance(attempt, dict) for attempt in attempts)
    return interface == "ui" and (status in {"passed", "failed"} or (status == "blocked" and has_executed_attempt))


def required_evidence_scope(case: dict[str, Any]) -> str:
    intent = normalized_test_intent(case)
    operation_type = text(case.get("operation_type")).lower()
    if intent["kind"] == "field_validation" or operation_type == "persisted_validation":
        return "assertion_target_container"
    return "assertion_state_view"


def requires_change_evidence(case: dict[str, Any]) -> bool:
    change = case.get("change_evidence") or {}
    return isinstance(change, dict) and change.get("required") is True


def requires_feedback_observation(case: dict[str, Any]) -> bool:
    return any(
        item.get("key") == "prompt_text" and item.get("source") == "ui"
        for item in normalized_contract_observations(case)
    )


def validate_feedback_observation(
    case: dict[str, Any],
    payload: dict[str, Any],
    screenshot_names: set[str],
    label: str,
) -> list[str]:
    if not requires_feedback_observation(case):
        return []
    case_id = text(case.get("case_id"))
    feedback = payload.get("feedback_observation")
    if not isinstance(feedback, dict):
        return [f"Case {case_id} {label} must include feedback_observation for UI prompt_text"]
    errors: list[str] = []
    presentation = text(feedback.get("presentation")).lower()
    if presentation not in {"modal", "toast", "inline", "banner"}:
        errors.append(
            f"Case {case_id} {label} feedback_observation.presentation must be modal/toast/inline/banner"
        )
    feedback_text = text(feedback.get("text"))
    observations = payload.get("observations") or {}
    prompt = observations.get("prompt_text") if isinstance(observations, dict) else None
    prompt_value = text(prompt.get("value")) if isinstance(prompt, dict) else ""
    if not feedback_text:
        errors.append(f"Case {case_id} {label} feedback_observation.text must not be blank")
    elif feedback_text != prompt_value:
        errors.append(
            f"Case {case_id} {label} feedback_observation.text must equal observations.prompt_text.value"
        )
    appeared_after_ms = feedback.get("appeared_after_ms")
    if not isinstance(appeared_after_ms, (int, float)) or appeared_after_ms < 0:
        errors.append(
            f"Case {case_id} {label} feedback_observation.appeared_after_ms must be non-negative"
        )
    if not isinstance(feedback.get("auto_dismissed"), bool):
        errors.append(f"Case {case_id} {label} feedback_observation.auto_dismissed must be boolean")
    screenshot_path = text(feedback.get("screenshot_path"))
    if not screenshot_path:
        errors.append(f"Case {case_id} {label} feedback_observation.screenshot_path must not be blank")
    elif Path(screenshot_path).name not in screenshot_names:
        errors.append(
            f"Case {case_id} {label} feedback_observation screenshot is not declared execution evidence"
        )
    return errors


def validate_evidence_manifest(
    case: dict[str, Any],
    evidence: Any,
    screenshot_files: dict[str, Path],
    label: str,
    *,
    strict_v2: bool = False,
    expected_attempt: int | None = None,
    selected_attempt: int | None = None,
    expected_headless: bool | None = None,
    source_attempt_files: dict[str, Path] | None = None,
) -> list[str]:
    """Validate that evidence describes what the screenshot proves.

    File existence is checked separately. This manifest binds the file to the
    case, assertion moment, browser, target and semantic observation keys, and
    requires a content text/anchor that can be reviewed without opening the
    whole DOM log.
    """
    case_id = text(case.get("case_id"))
    if not isinstance(evidence, dict):
        return [f"Case {case_id} {label} evidence must be a mapping"]
    manifest = evidence.get("manifest") or evidence.get("evidence_manifest")
    if not isinstance(manifest, list) or not manifest:
        return [f"Case {case_id} {label} evidence.manifest must contain at least one semantic entry"]
    errors: list[str] = []
    observed = normalized_contract_observations(case)
    change_required = requires_change_evidence(case)
    all_keys = {item["key"] for item in observed}
    required_keys = {
        item["key"]
        for item in observed
        if item.get("source") == "ui" and item.get("evidence_required") is True
    }
    covered_keys: set[str] = set()
    declared_files = {Path(text(value)).name for value in as_list(evidence.get("screenshots")) if text(value)}
    manifested_files: set[str] = set()
    browser_evidence: dict[str, set[tuple[str, str]]] = {}
    change_phases: set[str] = set()
    for index, entry in enumerate(manifest, 1):
        if not isinstance(entry, dict):
            errors.append(f"Case {case_id} {label} evidence.manifest[{index}] must be a mapping")
            continue
        file_name = Path(text(entry.get("file") or entry.get("path"))).name
        if not file_name:
            errors.append(f"Case {case_id} {label} evidence.manifest[{index}] needs file")
        elif file_name not in declared_files:
            errors.append(f"Case {case_id} {label} evidence manifest file {file_name} is not declared in screenshots")
        else:
            manifested_files.add(file_name)
            actual_file = screenshot_files.get(file_name)
            if actual_file is None or not actual_file.is_file() or actual_file.stat().st_size == 0:
                errors.append(f"Case {case_id} {label} evidence manifest file does not exist or is empty: {file_name}")
            elif text(entry.get("sha256")).lower() != sha256_file(actual_file).lower():
                errors.append(f"Case {case_id} {label} evidence manifest SHA-256 mismatch: {file_name}")
        if text(entry.get("case_id")) != case_id:
            errors.append(f"Case {case_id} {label} evidence manifest case_id must be {case_id}")
        for field in ("assertion_moment", "target", "browser"):
            if not text(entry.get(field)):
                errors.append(f"Case {case_id} {label} evidence manifest {file_name or index} needs {field}")
        anchors = [text(value) for value in as_list(entry.get("content_anchors")) if text(value)]
        content_text = text(entry.get("content_text"))
        if not content_text:
            errors.append(f"Case {case_id} {label} evidence manifest {file_name or index} needs content_text")
        missing_anchors = [anchor for anchor in anchors if anchor.lower() not in content_text.lower()]
        if missing_anchors:
            errors.append(
                f"Case {case_id} {label} evidence manifest {file_name or index} content_text misses anchors: "
                f"{', '.join(missing_anchors)}"
            )
        semantic_keys = {text(value) for value in as_list(entry.get("semantic_keys")) if text(value)}
        unknown_keys = sorted(semantic_keys - all_keys)
        if unknown_keys:
            errors.append(
                f"Case {case_id} {label} evidence manifest {file_name or index} has unknown semantic keys: "
                f"{', '.join(unknown_keys)}"
            )
        covered_keys.update(semantic_keys)
        if entry.get("content_verified") is not True:
            errors.append(f"Case {case_id} {label} evidence manifest {file_name or index} must set content_verified: true")
        if text(entry.get("verification_method")).lower() not in {"ocr", "text_anchor", "manual", "video_frame"}:
            errors.append(
                f"Case {case_id} {label} evidence manifest {file_name or index} verification_method must be ocr/text_anchor/manual/video_frame"
            )
        phase = text(entry.get("phase")).lower()
        if phase in {"before", "after"}:
            change_phases.add(phase)
        browser = text(entry.get("browser")).lower()
        if strict_v2:
            if not isinstance(entry.get("headless"), bool):
                errors.append(f"Case {case_id} {label} evidence manifest {file_name or index} headless must be boolean")
            elif expected_headless is not None and entry.get("headless") is not expected_headless:
                errors.append(f"Case {case_id} {label} evidence manifest {file_name or index} headless does not match runtime")
            expected_scope = required_evidence_scope(case)
            if text(entry.get("evidence_scope")) != expected_scope:
                errors.append(
                    f"Case {case_id} {label} evidence manifest {file_name or index} "
                    f"evidence_scope must be {expected_scope}"
                )
            observed_target_text = text(entry.get("observed_target_text"))
            if not observed_target_text:
                errors.append(
                    f"Case {case_id} {label} evidence manifest {file_name or index} needs observed_target_text"
                )
            else:
                missing_observed_anchors = [
                    anchor for anchor in anchors if anchor.lower() not in observed_target_text.lower()
                ]
                if missing_observed_anchors:
                    errors.append(
                        f"Case {case_id} {label} evidence manifest {file_name or index} "
                        "observed_target_text misses anchors: "
                        f"{', '.join(missing_observed_anchors)}"
                    )
            generic_anchors = {"店铺", "首页", "商品", "管理", "店铺管理"}
            exposed_generic = sorted(anchor for anchor in anchors if anchor.strip() in generic_anchors)
            if exposed_generic:
                errors.append(
                    f"Case {case_id} {label} evidence manifest {file_name or index} uses generic anchors: "
                    f"{', '.join(exposed_generic)}"
                )
            if expected_attempt is not None and entry.get("attempt") != expected_attempt:
                errors.append(
                    f"Case {case_id} {label} evidence manifest {file_name or index} attempt must be {expected_attempt}"
                )
            if selected_attempt is not None:
                if entry.get("selected_attempt") != selected_attempt:
                    errors.append(
                        f"Case {case_id} {label} evidence manifest {file_name or index} selected_attempt must be {selected_attempt}"
                    )
                source_attempt_file = Path(text(entry.get("source_attempt_file"))).name
                expected_source = (
                    f"{case_id}_A{selected_attempt}_{phase}.png"
                    if change_required and phase in {"before", "after"}
                    else f"{case_id}_A{selected_attempt}_assertion.png"
                )
                if source_attempt_file != expected_source:
                    errors.append(
                        f"Case {case_id} {label} evidence manifest source_attempt_file must be {expected_source}"
                    )
                elif source_attempt_files is not None:
                    source_file = source_attempt_files.get(source_attempt_file)
                    final_file = screenshot_files.get(file_name)
                    if source_file is None or not source_file.is_file():
                        errors.append(
                            f"Case {case_id} {label} selected-attempt source screenshot is missing: {expected_source}"
                        )
                    elif final_file is not None and final_file.is_file() and sha256_file(final_file) != sha256_file(source_file):
                        errors.append(
                            f"Case {case_id} {label} final screenshot must be copied from selected attempt {selected_attempt}"
                        )
        if browser:
            frame_id = text(entry.get("frame_id"))
            if text(entry.get("verification_method")).lower() == "video_frame" and not frame_id:
                errors.append(f"Case {case_id} {label} video-frame evidence {file_name or index} needs frame_id")
            browser_evidence.setdefault(browser, set()).add((file_name, frame_id))
    missing_keys = sorted(required_keys - covered_keys)
    if missing_keys:
        errors.append(f"Case {case_id} {label} evidence manifest misses semantic keys: {', '.join(missing_keys)}")
    missing_manifest_files = sorted(declared_files - manifested_files)
    if missing_manifest_files:
        errors.append(
            f"Case {case_id} {label} screenshots missing manifest entries: {', '.join(missing_manifest_files)}"
        )
    if change_required:
        if not {"before", "after"}.issubset(change_phases):
            errors.append(
                f"Case {case_id} {label} change evidence manifest must contain phase before and phase after entries"
            )
        if not any(text(entry.get("phase")).lower() == "before" for entry in manifest if isinstance(entry, dict)):
            errors.append(f"Case {case_id} {label} is missing before screenshot evidence")
        if not any(text(entry.get("phase")).lower() == "after" for entry in manifest if isinstance(entry, dict)):
            errors.append(f"Case {case_id} {label} is missing after screenshot evidence")
    required_browsers = {
        text(value).lower()
        for value in as_list((case.get("result_contract") or {}).get("required_browsers") or case.get("compatibility_browsers"))
        if text(value)
    }
    if required_browsers:
        covered_browsers = {
            text(entry.get("browser")).lower()
            for entry in manifest
            if isinstance(entry, dict) and text(entry.get("browser"))
        }
        missing_browsers = sorted(required_browsers - covered_browsers)
        if missing_browsers:
            errors.append(f"Case {case_id} {label} compatibility evidence misses browsers: {', '.join(missing_browsers)}")
        evidence_identities = [
            next(iter(browser_evidence.get(browser, set())), ("", "")) for browser in sorted(required_browsers)
        ]
        if not missing_browsers and len(set(evidence_identities)) != len(evidence_identities):
            errors.append(
                f"Case {case_id} {label} compatibility browsers must use separate screenshots or video frames"
            )
    return errors


def cell_column(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref.upper())
    if not letters:
        return 0
    value = 0
    for char in letters.group(0):
        value = value * 26 + ord(char) - 64
    return value - 1


def first_sheet_path(zf: zipfile.ZipFile) -> str:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    sheet = workbook.find(f"{{{MAIN_NS}}}sheets/{{{MAIN_NS}}}sheet")
    if sheet is None:
        raise ValueError("Workbook has no sheets")
    relationship_id = sheet.get(f"{{{REL_NS}}}id")
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    for relationship in rels.findall(f"{{{PKG_REL_NS}}}Relationship"):
        if relationship.get("Id") == relationship_id:
            target = text(relationship.get("Target")).replace("\\", "/").lstrip("/")
            return target if target.startswith("xl/") else f"xl/{target}"
    raise ValueError("Cannot resolve first worksheet relationship")


def xlsx_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as zf:
        broken = zf.testzip()
        if broken:
            raise ValueError(f"Corrupt xlsx member: {broken}")
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            shared_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for item in shared_root.findall(f"{{{MAIN_NS}}}si"):
                shared.append("".join(node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t")))
        sheet_root = ET.fromstring(zf.read(first_sheet_path(zf)))
    rows: list[list[str]] = []
    for row in sheet_root.findall(f"{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row"):
        values: dict[int, str] = {}
        for cell in row.findall(f"{{{MAIN_NS}}}c"):
            index = cell_column(text(cell.get("r")))
            cell_type = text(cell.get("t"))
            if cell_type == "inlineStr":
                value = "".join(node.text or "" for node in cell.iter(f"{{{MAIN_NS}}}t"))
            else:
                value_node = cell.find(f"{{{MAIN_NS}}}v")
                raw = value_node.text if value_node is not None and value_node.text is not None else ""
                if cell_type == "s" and raw:
                    value = shared[int(raw)]
                else:
                    value = raw
            values[index] = value
        width = max(values, default=-1) + 1
        rows.append([values.get(index, "") for index in range(width)])
    return rows


def find_header(headers: list[str], aliases: set[str]) -> int | None:
    for index, header in enumerate(headers):
        if text(header).lower() in aliases:
            return index
    return None


def validate_xlsx(
    path: Path,
    case_ids: list[str],
    backfill: bool,
    errors: list[str],
    expected_statuses: dict[str, str] | None = None,
    expected_actual_results: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        rows = xlsx_rows(path)
    except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError, IndexError) as exc:
        errors.append(f"Cannot read xlsx {path}: {exc}")
        return {"path": str(path), "rows": 0, "columns": 0}
    if not rows:
        errors.append(f"Xlsx is empty: {path}")
        return {"path": str(path), "rows": 0, "columns": 0}
    headers = rows[0]
    id_column = find_header(headers, {"功能路径", "用例id", "用例编号", "case_id"})
    if id_column is None:
        errors.append(f"Xlsx has no case ID column: {path}")
        id_column = 0
    data_rows = rows[1:]
    actual_ids = [text(row[id_column]) if id_column < len(row) else "" for row in data_rows]
    if actual_ids != case_ids:
        errors.append(f"Xlsx case IDs/order do not match final cases: {path}")
    if len(data_rows) != len(case_ids):
        errors.append(f"Xlsx data rows ({len(data_rows)}) must equal case count ({len(case_ids)}): {path}")
    if backfill:
        status_column = find_header(headers, {"执行状态", "状态", "status"})
        actual_column = find_header(headers, {"执行结果", "实际结果", "actual_result"})
        if status_column is None or actual_column is None:
            errors.append(f"Backfill xlsx must contain status and actual-result columns: {path}")
        else:
            for row_number, row in enumerate(data_rows, 2):
                row_case_id = text(row[id_column]) if id_column < len(row) else ""
                status = text(row[status_column]) if status_column < len(row) else ""
                actual = text(row[actual_column]) if actual_column < len(row) else ""
                normalized_status = normalize_execution_status(status)
                if normalized_status not in {"passed", "failed", "blocked", "not_run"}:
                    errors.append(f"Backfill xlsx row {row_number} has invalid status: {status or '<blank>'}")
                elif expected_statuses is not None and normalized_status != expected_statuses.get(row_case_id):
                    errors.append(
                        f"Backfill xlsx row {row_number} status does not match execution result "
                        f"for {row_case_id}: {normalized_status} != {expected_statuses.get(row_case_id)}"
                    )
                if not actual or actual.lower() in BARE_RESULTS:
                    errors.append(f"Backfill xlsx row {row_number} actual result is blank or status-only")
                elif expected_actual_results is not None and row_case_id in expected_actual_results and actual != expected_actual_results.get(row_case_id):
                    errors.append(f"Backfill xlsx row {row_number} actual result does not match {row_case_id} execution result")
    return {"path": str(path), "rows": len(rows), "columns": max((len(row) for row in rows), default=0)}


def powershell_executable() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


def viewer_check(viewer: str, path: Path) -> tuple[str, str]:
    executable = powershell_executable()
    if not executable:
        return "unavailable", "PowerShell is unavailable"
    escaped = str(path.resolve()).replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
try {{ $app = New-Object -ComObject '{viewer}' }} catch {{ Write-Output 'UNAVAILABLE'; exit 3 }}
$app.Visible = $false
try {{
  $book = $app.Workbooks.Open('{escaped}')
  $sheet = $book.Worksheets.Item(1)
  Write-Output ('OK|' + $sheet.UsedRange.Rows.Count + '|' + $sheet.UsedRange.Columns.Count + '|' + $sheet.Cells.Item(2,1).Text)
  $book.Close($false)
}} catch {{
  Write-Output ('ERROR|' + $_.Exception.Message)
  exit 2
}} finally {{
  $app.Quit()
  [Runtime.InteropServices.Marshal]::FinalReleaseComObject($app) | Out-Null
}}
"""
    completed = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
    )
    output = (completed.stdout or completed.stderr).strip()
    if completed.returncode == 3 or "UNAVAILABLE" in output:
        return "unavailable", output
    if completed.returncode != 0 or not output.startswith("OK|"):
        return "failed", output or f"viewer exited {completed.returncode}"
    return "passed", output


def validate_viewers(paths: list[Path], mode: str, errors: list[str], warnings: list[str]) -> list[dict[str, str]]:
    if mode == "skip":
        warnings.append("Viewer validation was explicitly skipped")
        return []
    if os.name != "nt":
        message = "Excel/WPS COM viewer validation is only available on Windows"
        (errors if mode == "required" else warnings).append(message)
        return []
    checks: list[dict[str, str]] = []
    available_count = 0
    for viewer in ("Excel.Application", "KET.Application"):
        for path in paths:
            status, detail = viewer_check(viewer, path)
            checks.append({"viewer": viewer, "path": str(path), "status": status, "detail": detail})
            if status == "passed":
                available_count += 1
            elif status == "failed":
                errors.append(f"{viewer} could not open {path}: {detail}")
    if available_count == 0:
        message = "Neither Excel nor WPS viewer validation was available"
        (errors if mode == "required" else warnings).append(message)
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate execution completeness and final delivery artifacts.")
    parser.add_argument("--case-gate", required=True)
    parser.add_argument("--final-cases", required=True)
    parser.add_argument("--execution-plan-gate", required=True)
    parser.add_argument("--execution-plan", required=True)
    parser.add_argument("--execution-results", required=True)
    parser.add_argument("--screenshots-dir", required=True)
    parser.add_argument("--phase", choices=("execution", "delivery"), default="delivery")
    parser.add_argument("--template-xlsx")
    parser.add_argument("--backfill-xlsx")
    parser.add_argument("--viewer-check", choices=("auto", "required", "skip"), default="auto")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    case_gate_path = Path(args.case_gate).resolve()
    final_case_path = Path(args.final_cases).resolve()
    plan_gate_path = Path(args.execution_plan_gate).resolve()
    execution_plan_path = Path(args.execution_plan).resolve()
    execution_path = Path(args.execution_results).resolve()
    screenshots_dir = Path(args.screenshots_dir).resolve()
    output_path = Path(args.output).resolve()
    errors: list[str] = []
    warnings: list[str] = []

    try:
        case_gate = verify_passed_gate(case_gate_path, final_case_path, "final_cases")
        plan_gate = verify_passed_gate(plan_gate_path, final_case_path, "final_cases")
        verify_passed_gate(plan_gate_path, execution_plan_path, "execution_plan")
        case_doc = load_yaml(final_case_path)
        plan_doc = load_yaml(execution_plan_path)
        execution_doc = load_yaml(execution_path)
        cases = list_from(case_doc, ("test_cases",), "test_cases")
        plans = list_from(plan_doc, ("browser_execution_plan", "execution_plan"), "execution_plan")
        results = list_from(execution_doc, ("execution_results",), "execution_results")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        payload = {"gate": "execution_delivery", "status": "fail", "phase": args.phase, "errors": [str(exc)], "warnings": []}
        write_json(output_path, payload)
        print(str(exc), file=sys.stderr)
        return 1

    if text(case_gate.get("gate")) != "case_design" or text(case_gate.get("contract_version")) != "2.0":
        errors.append("case gate must be a contract_version 2.0 case_design gate")
    if text(plan_gate.get("gate")) != "execution_plan" or text(plan_gate.get("contract_version")) != "2.0":
        errors.append("execution plan gate must be a contract_version 2.0 execution_plan gate")

    expected_case_hash = sha256_file(final_case_path)
    execution_meta = execution_doc.get("execution_meta") or {}
    evidence_manifest_v2 = text(execution_meta.get("evidence_manifest_version")) == "2.0"
    browser_runtime = execution_meta.get("browser_runtime") or {}
    runtime_headless = browser_runtime.get("headless") if isinstance(browser_runtime, dict) else None
    if evidence_manifest_v2:
        if not isinstance(browser_runtime, dict):
            errors.append("execution_meta.browser_runtime must be a mapping for evidence manifest 2.0")
            browser_runtime = {}
        if not text(browser_runtime.get("browser")):
            errors.append("execution_meta.browser_runtime.browser must not be blank")
        if not isinstance(browser_runtime.get("headless"), bool):
            errors.append("execution_meta.browser_runtime.headless must be boolean")
        expected_mode = "headless" if browser_runtime.get("headless") is True else "headed"
        if text(browser_runtime.get("mode")).lower() != expected_mode:
            errors.append(f"execution_meta.browser_runtime.mode must be {expected_mode}")
        if not text(browser_runtime.get("config_source")):
            errors.append("execution_meta.browser_runtime.config_source must not be blank")
    declared_hash = text(execution_meta.get("final_cases_sha256"))
    if declared_hash != expected_case_hash:
        errors.append("execution_meta.final_cases_sha256 is missing or does not match the gated final cases")
    expected_plan_hash = sha256_file(execution_plan_path)
    if text(execution_meta.get("execution_plan_sha256")) != expected_plan_hash:
        errors.append("execution_meta.execution_plan_sha256 is missing or does not match the gated execution plan")
    if text(execution_meta.get("retry_policy_version")) != "3.0":
        errors.append("execution_meta.retry_policy_version must be 3.0")
    plan_meta = plan_doc.get("execution_meta") or {}
    execution_mode = text(execution_meta.get("execution_mode")).lower()
    if execution_mode != text(plan_meta.get("execution_mode")).lower():
        errors.append("execution_meta.execution_mode must match the gated execution plan")
    errors.extend(validate_input_hashes(execution_meta.get("input_sha256"), "execution_meta.input_sha256"))
    if execution_meta.get("input_sha256") != plan_meta.get("input_sha256"):
        errors.append("execution_meta.input_sha256 must exactly match the gated execution plan")
    errors.extend(
        validate_priority_execution_policy(
            execution_meta.get("priority_execution_policy"),
            "execution_meta.priority_execution_policy",
        )
    )
    if execution_meta.get("priority_execution_policy") != plan_meta.get("priority_execution_policy"):
        errors.append("execution_meta.priority_execution_policy must exactly match the gated execution plan")
    scope_matrix = execution_meta.get("scope_matrix")
    if not isinstance(scope_matrix, list) or not scope_matrix:
        errors.append("execution_meta.scope_matrix must disclose every test track")
    else:
        required_scope_fields = {"track", "status", "reason"}
        for index, scope in enumerate(scope_matrix, 1):
            if not isinstance(scope, dict):
                errors.append(f"execution_meta.scope_matrix[{index}] must be a mapping")
                continue
            missing_fields = sorted(field for field in required_scope_fields if not text(scope.get(field)))
            if missing_fields:
                errors.append(f"execution_meta.scope_matrix[{index}] is missing: {', '.join(missing_fields)}")
            if text(scope.get("status")).lower() not in {"executed", "skipped", "blocked"}:
                errors.append(f"execution_meta.scope_matrix[{index}].status must be executed/skipped/blocked")
    resource_budget = execution_meta.get("resource_budget")
    if not isinstance(resource_budget, dict):
        errors.append("execution_meta.resource_budget must disclose estimated and actual execution resources")
    else:
        for field in ("estimated_attempts", "estimated_minutes", "actual_attempts", "actual_minutes"):
            if not isinstance(resource_budget.get(field), (int, float)) or resource_budget.get(field) < 0:
                errors.append(f"execution_meta.resource_budget.{field} must be a non-negative number")

    case_ids = [text(case.get("case_id")) for case in cases]
    result_ids = [text(item.get("case_id")) for item in results]
    duplicates = duplicate_values(result_ids)
    if duplicates:
        errors.append(f"Duplicate execution result IDs: {', '.join(duplicates)}")
    if set(result_ids) != set(case_ids) or len(result_ids) != len(case_ids):
        missing = sorted(set(case_ids) - set(result_ids))
        extra = sorted(set(result_ids) - set(case_ids))
        errors.append(f"Execution result IDs must exactly match final cases; missing={missing}, extra={extra}")
    elif result_ids != case_ids:
        errors.append("Execution result IDs must preserve final-case order")

    cases_by_id = {text(case.get("case_id")): case for case in cases}
    plans_by_id = {text(plan.get("case_id")): plan for plan in plans}
    expected_statuses: dict[str, str] = {}
    expected_actual_results: dict[str, str] = {}
    semantic_evaluations: dict[str, dict[str, Any]] = {}
    normalized_counts = Counter()
    for item in results:
        case_id = text(item.get("case_id"))
        status = normalize_execution_status(item.get("status"))
        expected_statuses[case_id] = status
        normalized_counts[status] += 1
        if status not in {"passed", "failed", "blocked", "not_run"}:
            errors.append(f"Case {case_id} has invalid status: {item.get('status')}")
            continue
        actual_result = text(item.get("actual_result"))
        expected_actual_results[case_id] = backfill_actual_result(item)
        if not actual_result or actual_result.lower() in BARE_RESULTS:
            errors.append(f"Case {case_id} actual_result is blank or status-only")
        errors.extend(validate_result_summary(case_id, item, status))
        case = cases_by_id.get(case_id) or {}
        plan = plans_by_id.get(case_id) or {}
        case_evaluation: dict[str, Any] = {"attempts": []}
        semantic_evaluations[case_id] = case_evaluation
        expected_semantic_hash = semantic_contract_hash(case) if case else ""
        if text(item.get("semantic_contract_sha256")) != expected_semantic_hash:
            errors.append(f"Case {case_id} result has a stale or missing semantic contract hash")
        if not plan:
            errors.append(f"Case {case_id} has no gated execution plan item")
        raw_attempts = as_list(item.get("attempts"))
        if any(not isinstance(attempt, dict) for attempt in raw_attempts):
            errors.append(f"Case {case_id} attempts must contain only mappings")
        trace_required = status in {"passed", "failed"} or any(
            isinstance(attempt, dict) for attempt in raw_attempts
        )
        if plan and (trace_required or isinstance(item.get("execution_trace"), dict)):
            errors.extend(validate_execution_trace(case_id, item.get("execution_trace"), plan, "result"))
        evidence = item.get("evidence") or {}
        if not isinstance(evidence, dict):
            errors.append(f"Case {case_id} evidence must be a mapping")
            evidence = {}
        declared_screenshot_paths: dict[str, Path] = {}
        for value in as_list(evidence.get("screenshots")):
            raw_value = text(value)
            if not raw_value:
                continue
            raw_path = Path(raw_value)
            candidates = (
                [raw_path]
                if raw_path.is_absolute()
                else [execution_path.parent / raw_path, screenshots_dir / raw_path.name]
            )
            resolved = next((candidate.resolve() for candidate in candidates if candidate.is_file()), candidates[-1].resolve())
            file_name = raw_path.name
            if file_name in declared_screenshot_paths and declared_screenshot_paths[file_name] != resolved:
                errors.append(f"Case {case_id} declares duplicate screenshot name with different paths: {file_name}")
            declared_screenshot_paths[file_name] = resolved
        declared_screenshots = set(declared_screenshot_paths)
        attempts = [attempt for attempt in raw_attempts if isinstance(attempt, dict)]
        all_screenshot_names = set(declared_screenshots)
        attempt_evidence_paths: dict[int, dict[str, Path]] = {}
        for attempt_index, attempt in enumerate(attempts, 1):
            attempt_evidence = attempt.get("evidence") or {}
            paths: dict[str, Path] = {}
            if isinstance(attempt_evidence, dict):
                for value in as_list(attempt_evidence.get("screenshots")):
                    raw_value = text(value)
                    if not raw_value:
                        continue
                    raw_path = Path(raw_value)
                    candidates = (
                        [raw_path]
                        if raw_path.is_absolute()
                        else [execution_path.parent / raw_path, screenshots_dir / raw_path.name]
                    )
                    resolved = next(
                        (candidate.resolve() for candidate in candidates if candidate.is_file()),
                        candidates[-1].resolve(),
                    )
                    paths[raw_path.name] = resolved
                    all_screenshot_names.add(raw_path.name)
            attempt_evidence_paths[attempt_index] = paths
            if evidence_manifest_v2 and normalize_execution_status(attempt.get("status")) in {"passed", "failed", "blocked"}:
                errors.extend(
                    validate_evidence_manifest(
                        case,
                        attempt_evidence,
                        paths,
                        f"attempt {attempt_index}",
                        strict_v2=True,
                        expected_attempt=attempt_index,
                        expected_headless=runtime_headless,
                    )
                )
        statuses = attempt_statuses(item)
        attempt_numbers = [attempt.get("attempt") for attempt in attempts]
        if attempts and attempt_numbers != list(range(1, len(attempts) + 1)):
            errors.append(f"Case {case_id} attempt numbers must be consecutive integers starting at 1")
        selected_attempt = item.get("selected_attempt") or item.get("backfilled_from")
        if status in {"passed", "failed", "blocked"} and attempts:
            minimum_attempts, budget_errors = execution_minimum_attempts(case, plan, plan_meta)
            errors.extend(budget_errors)
            decision = retry_decision(attempts, text(case.get("priority")), minimum_attempts)
            if len(attempts) != decision.required_attempts:
                errors.append(
                    f"Case {case_id} requires {decision.required_attempts} attempts under retry policy 3.0; got {len(attempts)}"
                )
            if status != decision.final_status:
                errors.append(
                    f"Case {case_id} final status must be {decision.final_status} under retry policy 3.0; got {status}"
                )
            if selected_attempt not in {decision.selected_attempt, text(decision.selected_attempt)}:
                errors.append(
                    f"Case {case_id} selected_attempt must be {decision.selected_attempt} under retry policy 3.0"
                )
            expected_stability = decision.stability
            if text(item.get("stability")).lower() != expected_stability:
                errors.append(f"Case {case_id} stability must be {expected_stability}")
            retry_policy = item.get("retry_policy") or {}
            if not isinstance(retry_policy, dict):
                errors.append(f"Case {case_id} retry_policy must be a mapping")
            else:
                expected_retry_fields = {
                    "minimum_attempts": minimum_attempts,
                    "required_attempts": decision.required_attempts,
                    "third_attempt_required": decision.required_attempts == 3,
                    "single_run_exempt": minimum_attempts == 1,
                    "full_retry_upgraded": minimum_attempts == 1 and decision.required_attempts == 3,
                    "priority": text(case.get("priority")).upper(),
                }
                for field, expected_value in expected_retry_fields.items():
                    if retry_policy.get(field) != expected_value:
                        errors.append(
                            f"Case {case_id} retry_policy.{field} must be {expected_value!r}"
                        )
        elif status in {"passed", "failed"}:
            errors.append(f"Case {case_id} must keep retry attempts")
        if status == "blocked":
            blocker_reason = text(item.get("blocker_reason"))
            if not blocker_reason:
                errors.append(f"Blocked case {case_id} must include blocker_reason")
            probes = item.get("capability_probe") or {}
            if not isinstance(probes, dict) or not probes.get("attempted_channels") or not text(probes.get("probe_log")):
                errors.append(f"Blocked case {case_id} must include capability_probe.attempted_channels and probe_log")
        if status == "not_run" and not text(item.get("blocker_reason") or item.get("not_run_reason")):
            errors.append(f"Not-run case {case_id} must include a reason")
        if status == "not_run" and attempts:
            errors.append(f"Not-run case {case_id} must not contain attempts")
        if status == "not_run" and text(item.get("stability")).lower() != "not_run":
            errors.append(f"Not-run case {case_id} stability must be not_run")

        produced_data = item.get("produced_data") or item.get("produced") or {}
        for attempt_index, attempt in enumerate(attempts, 1):
            if text(attempt.get("semantic_contract_sha256")) != expected_semantic_hash:
                errors.append(
                    f"Case {case_id} attempt {attempt_index} has a stale or missing semantic contract hash"
                )
            if plan:
                errors.extend(
                    validate_execution_trace(
                        case_id,
                        attempt.get("execution_trace"),
                        plan,
                        f"attempt {attempt_index}",
                    )
                )
            attempt_status = normalize_execution_status(attempt.get("status"))
            if attempt_status in {"passed", "failed"}:
                payload_errors, mismatches = validate_executed_payload(
                    case,
                    attempt,
                    all_screenshot_names,
                    f"attempt {attempt_index}",
                    attempt_status,
                )
                errors.extend(payload_errors)
                case_evaluation["attempts"].append(
                    {
                        "attempt": attempt_index,
                        "status": attempt_status,
                        "contract_satisfied": not mismatches,
                        "mismatches": mismatches,
                    }
                )
        if status in {"passed", "failed"}:
            if not isinstance(produced_data, dict) or not produced_data:
                errors.append(
                    f"{status.title()} case {case_id} must include non-empty produced_data"
                )
            else:
                missing_keys = [
                    key
                    for key in required_produced_keys(case)
                    if key not in produced_data or not has_observation_value(produced_data.get(key))
                ]
                if missing_keys:
                    errors.append(f"Case {case_id} is missing required produced_data keys: {', '.join(missing_keys)}")
            payload_errors, result_mismatches = validate_executed_payload(
                case, item, all_screenshot_names, "result", status
            )
            errors.extend(payload_errors)
            case_evaluation["result"] = {
                "status": status,
                "contract_satisfied": not result_mismatches,
                "mismatches": result_mismatches,
            }
            if selected_attempt in {1, 2, 3, "1", "2", "3"} and len(attempts) >= int(selected_attempt):
                selected = attempts[int(selected_attempt) - 1]
                selected_produced = selected.get("produced_data") or selected.get("produced") or {}
                if isinstance(selected_produced, dict) and isinstance(produced_data, dict):
                    mismatched_keys = [
                        key
                        for key in required_produced_keys(case)
                        if not values_equal(selected_produced.get(key), produced_data.get(key))
                    ]
                    if mismatched_keys:
                        errors.append(
                            f"Case {case_id} produced_data does not match selected_attempt for keys: "
                            f"{', '.join(mismatched_keys)}"
                        )
        if status == "failed" and not text(item.get("failed_step_no")):
            errors.append(f"Failed case {case_id} must include failed_step_no")

        screenshot_required = requires_ui_screenshot(case, status, attempts)
        if screenshot_required:
            expected_screenshot = screenshots_dir / f"{case_id}.png"
            if not expected_screenshot.is_file() or expected_screenshot.stat().st_size == 0:
                errors.append(f"Case {case_id} is missing screenshot {expected_screenshot}")
            if f"{case_id}.png" not in declared_screenshots:
                errors.append(f"Case {case_id} evidence must reference {case_id}.png")
            errors.extend(
                validate_evidence_manifest(
                    case,
                    evidence,
                    declared_screenshot_paths,
                    "result",
                    strict_v2=evidence_manifest_v2,
                    selected_attempt=int(selected_attempt) if evidence_manifest_v2 and str(selected_attempt).isdigit() else None,
                    expected_headless=runtime_headless,
                    source_attempt_files=(
                        attempt_evidence_paths.get(int(selected_attempt), {})
                        if evidence_manifest_v2 and str(selected_attempt).isdigit()
                        else None
                    ),
                )
            )

    calculated_priority_gate = evaluate_p0_stop_gate(cases, results)
    declared_priority_gate = execution_meta.get("priority_gate")
    if not isinstance(declared_priority_gate, dict):
        errors.append("execution_meta.priority_gate must be a mapping")
        declared_priority_gate = {}
    for field, expected_value in calculated_priority_gate.items():
        if declared_priority_gate.get(field) != expected_value:
            errors.append(
                f"execution_meta.priority_gate.{field} must be {expected_value!r}, "
                f"got {declared_priority_gate.get(field)!r}"
            )

    results_by_id = {text(item.get("case_id")): item for item in results}
    for case in cases:
        case_id = text(case.get("case_id"))
        priority = text(case.get("priority")).upper()
        result = results_by_id.get(case_id) or {}
        status = normalize_execution_status(result.get("status"))
        attempts = [attempt for attempt in as_list(result.get("attempts")) if isinstance(attempt, dict)]
        if priority == "P0" and status not in {"passed", "failed", "blocked"}:
            errors.append(f"P0 case {case_id} must complete final adjudication before lower priorities")
        if priority not in {"P1", "P2"}:
            continue
        if calculated_priority_gate["triggered"]:
            if status != "not_run":
                errors.append(f"Case {case_id} must be not_run after the all-P0-failed stop gate")
                continue
            if text(result.get("not_run_reason")) != P0_STOP_REASON:
                errors.append(f"Case {case_id} not_run_reason must be {P0_STOP_REASON}")
            if text(result.get("actual_result")) != P0_STOP_ACTUAL:
                errors.append(f"Case {case_id} actual_result must disclose the all-P0-failed stop gate")
            summary = result.get("result_summary") or {}
            if not isinstance(summary, dict) or text(summary.get("key_message")) != P0_STOP_REASON:
                errors.append(f"Case {case_id} result_summary.key_message must be {P0_STOP_REASON}")
            if attempts:
                errors.append(f"Case {case_id} must not retain attempts after the all-P0-failed stop gate")
        elif text(result.get("not_run_reason")) == P0_STOP_REASON:
            errors.append(f"Case {case_id} cannot claim the all-P0-failed stop gate when it was not triggered")

    if calculated_priority_gate["p0_completed"] < calculated_priority_gate["p0_total"]:
        for case in cases:
            if text(case.get("priority")).upper() not in {"P1", "P2"}:
                continue
            result = results_by_id.get(text(case.get("case_id"))) or {}
            if normalize_execution_status(result.get("status")) in {"passed", "failed", "blocked"} or as_list(result.get("attempts")):
                errors.append(
                    f"Case {text(case.get('case_id'))} cannot execute before every P0 case completes final adjudication"
                )

    if isinstance(resource_budget, dict):
        actual_attempts = sum(
            len([attempt for attempt in as_list(item.get("attempts")) if isinstance(attempt, dict)])
            for item in results
        )
        if resource_budget.get("actual_attempts") != actual_attempts:
            errors.append(
                f"execution_meta.resource_budget.actual_attempts must equal recorded attempts {actual_attempts}"
            )

    summary = execution_doc.get("summary") or {}
    expected_summary = {
        "total": len(results),
        "passed": normalized_counts["passed"],
        "failed": normalized_counts["failed"],
        "blocked": normalized_counts["blocked"],
        "not_run": normalized_counts["not_run"],
    }
    for key, expected in expected_summary.items():
        try:
            actual = int(summary.get(key))
        except (TypeError, ValueError):
            actual = None
        if actual != expected:
            errors.append(f"summary.{key} must be {expected}, got {summary.get(key)!r}")
    calculated_metrics = execution_metrics(results)
    for key in (
        "total_attempts",
        "third_attempt_cases",
        "unstable_cases",
        "single_run_exempt_cases",
        "full_retry_upgrades",
        "first_passed",
        "first_pass_rate",
        "final_pass_rate_including_blocked",
        "final_pass_rate_judged_only",
    ):
        if summary.get(key) != calculated_metrics[key]:
            errors.append(f"summary.{key} must be {calculated_metrics[key]}, got {summary.get(key)!r}")

    artifacts: dict[str, Any] = {}
    viewer_results: list[dict[str, str]] = []
    if args.phase == "delivery":
        if not args.template_xlsx or not args.backfill_xlsx:
            errors.append("Delivery phase requires --template-xlsx and --backfill-xlsx")
        else:
            template_path = Path(args.template_xlsx).resolve()
            backfill_path = Path(args.backfill_xlsx).resolve()
            artifacts["template_xlsx"] = validate_xlsx(template_path, case_ids, False, errors)
            artifacts["backfill_xlsx"] = validate_xlsx(
                backfill_path,
                case_ids,
                True,
                errors,
                expected_statuses,
                # 人工可读回填允许对原始 DOM 做业务摘要转换，不能再用
                # 旧版“预期:…；实际:…”机器拼接文本做逐字比较；这里仍
                # 校验状态、行数、ID、非空和非 DOM，具体业务事实由回填
                # 生成器从同一 YAML 生成。
                {},
            )
            viewer_results = validate_viewers([template_path, backfill_path], args.viewer_check, errors, warnings)

    payload = {
        "gate": "execution_delivery",
        "status": "fail" if errors else "pass",
        "phase": args.phase,
        "inputs": {
            "case_gate": source_record(case_gate_path),
            "final_cases": source_record(final_case_path),
            "execution_plan_gate": source_record(plan_gate_path),
            "execution_plan": source_record(execution_plan_path),
            "execution_results": source_record(execution_path),
        },
        "counts": expected_summary,
        "priority_gate": calculated_priority_gate,
        "semantic_evaluations": semantic_evaluations,
        "artifacts": artifacts,
        "viewer_checks": viewer_results,
        "errors": errors,
        "warnings": warnings,
    }
    write_json(output_path, payload)
    print(f"execution-delivery gate: {payload['status']} ({len(errors)} errors, {len(warnings)} warnings)")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    for warning in warnings:
        print(f"WARN: {warning}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
