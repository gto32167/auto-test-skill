from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping at the root")
    return data


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object at the root")
    return data


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_from(document: dict[str, Any], keys: tuple[str, ...], label: str) -> list[dict[str, Any]]:
    for key in keys:
        value = document.get(key)
        if isinstance(value, list):
            if not all(isinstance(item, dict) for item in value):
                raise ValueError(f"{label} must contain mapping items")
            return value
    raise ValueError(f"Missing {label}; expected one of: {', '.join(keys)}")


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_record(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": sha256_file(path)}


def verify_passed_gate(gate_path: Path, source_path: Path, source_key: str) -> dict[str, Any]:
    gate = load_json(gate_path)
    if gate.get("status") != "pass":
        raise ValueError(f"Gate did not pass: {gate_path}")
    inputs = gate.get("inputs") or {}
    expected = inputs.get(source_key) or {}
    expected_hash = text(expected.get("sha256"))
    if not expected_hash:
        raise ValueError(f"Gate is missing inputs.{source_key}.sha256")
    actual_hash = sha256_file(source_path)
    if actual_hash != expected_hash:
        raise ValueError(
            f"Source changed after gate: {source_path} (expected {expected_hash}, got {actual_hash})"
        )
    return gate


def normalize_execution_status(value: Any) -> str:
    raw = text(value).lower()
    aliases = {
        "passed": "passed",
        "pass": "passed",
        "通过": "passed",
        "failed": "failed",
        "fail": "failed",
        "失败": "failed",
        "blocked": "blocked",
        "阻塞": "blocked",
        "not_run": "not_run",
        "not run": "not_run",
        "未执行": "not_run",
    }
    return aliases.get(raw, raw)


def case_rows_from_yaml(source_path: Path) -> list[dict[str, str]]:
    document = load_yaml(source_path)
    cases = list_from(document, ("test_cases",), "test_cases")
    rows: list[dict[str, str]] = []
    for case in cases:
        formal_preconditions = case.get("formal_preconditions")
        if formal_preconditions is None:
            formal_preconditions = case.get("preconditions")
        preconditions = "；".join(text(item) for item in as_list(formal_preconditions) if text(item))
        step_parts: list[str] = []
        formal_steps = case.get("formal_steps")
        if formal_steps is None:
            formal_steps = case.get("steps")
        for index, step in enumerate(as_list(formal_steps), 1):
            if isinstance(step, str):
                step_text = text(step)
                if step_text:
                    step_parts.append(f"{index}. {step_text}")
                continue
            if not isinstance(step, dict):
                continue
            step_no = step.get("step_no") or index
            display_text = text(step.get("formal_text") or step.get("display_text"))
            if display_text:
                step_parts.append(f"{step_no}. {display_text}")
                continue
            detail = " ".join(
                part
                for part in [text(step.get("action")), text(step.get("target")), text(step.get("input"))]
                if part
            )
            step_parts.append(f"{step_no}. {detail}".strip())
        formal_expected = text(case.get("formal_expected_result"))
        if not formal_expected:
            assertions = [item for item in as_list(case.get("assertions")) if isinstance(item, dict)]
            formal_expected = text(assertions[0].get("expected")) if assertions else ""
        rows.append(
            {
                "用例ID": text(case.get("case_id")),
                "功能集合": text(
                    case.get("formal_feature_group")
                    or case.get("feature_group")
                    or case.get("module")
                ),
                "用例名称": text(case.get("formal_case_title") or case.get("case_title") or case.get("title")),
                "优先级": text(case.get("priority")),
                "前置条件": preconditions,
                "执行步骤": "；".join(step_parts),
                "预期结果": formal_expected,
            }
        )
    return rows
