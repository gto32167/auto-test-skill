from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
MODEL_EXECUTORS = {"model", "mixed"}
VALID_EXECUTORS = {"model", "deterministic", "mixed"}
VALID_RUN_STATUSES = {"running", "completed", "failed", "cancelled", "blocked"}
VALID_STAGE_STATUSES = {"running", "completed", "failed", "skipped", "blocked"}
EXACT_TOKEN_SOURCES = {
    "provider_response",
    "platform_export",
    "codex_usage_snapshot",
    "manual_exact",
}
TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "reasoning_tokens",
    "total_tokens",
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def elapsed_seconds(started_at: str, finished_at: str | None = None) -> float:
    end = _parse_datetime(finished_at) if finished_at else datetime.now().astimezone()
    start = _parse_datetime(started_at)
    return round(max(0.0, (end - start).total_seconds()), 3)


def _empty_document(project_dir: str | Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "project_dir": str(Path(project_dir).resolve()),
        "latest_run_id": "",
        "runs": [],
    }


def load_metrics(path: str | Path, project_dir: str | Path | None = None) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file():
        return _empty_document(project_dir or target.parent)
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        raise ValueError(f"Invalid workflow metrics document: {target}")
    if str(data.get("schema_version")) != SCHEMA_VERSION:
        raise ValueError(f"Unsupported workflow metrics schema: {data.get('schema_version')}")
    return data


def save_metrics(path: str | Path, data: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def _find_run(data: dict[str, Any], run_id: str | None = None) -> dict[str, Any]:
    target_id = run_id or str(data.get("latest_run_id") or "")
    for run in reversed(data.get("runs") or []):
        if isinstance(run, dict) and str(run.get("run_id")) == target_id:
            return run
    raise ValueError(f"Workflow run not found: {target_id or '<latest>'}")


def latest_active_run_id(path: str | Path) -> str | None:
    data = load_metrics(path)
    for run in reversed(data.get("runs") or []):
        if isinstance(run, dict) and run.get("status") == "running":
            return str(run.get("run_id"))
    return None


def initialize_run(
    path: str | Path,
    *,
    project_dir: str | Path,
    workflow_mode: str,
    run_id: str | None = None,
    trigger: str = "user_request",
) -> str:
    data = load_metrics(path, project_dir)
    active_runs = [
        str(item.get("run_id"))
        for item in data.get("runs") or []
        if isinstance(item, dict) and item.get("status") == "running"
    ]
    if active_runs:
        raise ValueError(f"A workflow run is already active: {active_runs[-1]}")
    identifier = run_id or f"RUN-{datetime.now().strftime('%Y%m%d_%H%M%S')}-{uuid.uuid4().hex[:8]}"
    if any(str(item.get("run_id")) == identifier for item in data.get("runs") if isinstance(item, dict)):
        raise ValueError(f"Workflow run already exists: {identifier}")
    started_at = now_iso()
    data["project_dir"] = str(Path(project_dir).resolve())
    data["latest_run_id"] = identifier
    data["runs"].append(
        {
            "run_id": identifier,
            "workflow_mode": workflow_mode,
            "trigger": trigger,
            "status": "running",
            "started_at": started_at,
            "finished_at": "",
            "duration_seconds": None,
            "pauses": [],
            "stages": [],
            "token_usage": {},
            "notes": [],
        }
    )
    _refresh_run(data["runs"][-1])
    save_metrics(path, data)
    return identifier


def ensure_active_run(
    path: str | Path,
    *,
    project_dir: str | Path,
    workflow_mode: str,
    trigger: str,
) -> tuple[str, bool]:
    active = latest_active_run_id(path)
    if active:
        return active, False
    return initialize_run(
        path,
        project_dir=project_dir,
        workflow_mode=workflow_mode,
        trigger=trigger,
    ), True


def start_stage(
    path: str | Path,
    *,
    run_id: str | None,
    name: str,
    executor: str,
    stage_id: str | None = None,
) -> str:
    normalized_executor = executor.strip().lower()
    if normalized_executor not in VALID_EXECUTORS:
        raise ValueError(f"executor must be one of {sorted(VALID_EXECUTORS)}")
    data = load_metrics(path)
    run = _find_run(data, run_id)
    if run.get("status") != "running":
        raise ValueError(f"Cannot start stage on non-running workflow: {run.get('run_id')}")
    if any(stage.get("status") == "running" and stage.get("name") == name for stage in run.get("stages") or []):
        raise ValueError(f"Stage is already running: {name}")
    identifier = stage_id or f"STAGE-{len(run.get('stages') or []) + 1:02d}-{name}"
    if any(stage.get("stage_id") == identifier for stage in run.get("stages") or []):
        raise ValueError(f"Stage ID already exists: {identifier}")
    run.setdefault("stages", []).append(
        {
            "stage_id": identifier,
            "name": name,
            "executor": normalized_executor,
            "status": "running",
            "started_at": now_iso(),
            "finished_at": "",
            "duration_seconds": None,
            "token_coverage": "not_applicable" if normalized_executor == "deterministic" else "pending",
            "token_unavailable_reason": "",
            "token_events": [],
            "token_usage": {},
        }
    )
    _refresh_run(run)
    save_metrics(path, data)
    return identifier


def pause_run(path: str | Path, *, run_id: str | None, reason: str) -> None:
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise ValueError("Pause reason must not be blank")
    data = load_metrics(path)
    run = _find_run(data, run_id)
    if run.get("status") != "running":
        raise ValueError("Only a running workflow can be paused")
    if any(not pause.get("finished_at") for pause in run.get("pauses") or [] if isinstance(pause, dict)):
        raise ValueError("Workflow is already paused")
    run.setdefault("pauses", []).append(
        {
            "started_at": now_iso(),
            "finished_at": "",
            "duration_seconds": None,
            "reason": normalized_reason,
        }
    )
    _refresh_run(run)
    save_metrics(path, data)


def resume_run(path: str | Path, *, run_id: str | None) -> None:
    data = load_metrics(path)
    run = _find_run(data, run_id)
    if run.get("status") != "running":
        raise ValueError("Only a running workflow can be resumed")
    active = next(
        (
            pause
            for pause in reversed(run.get("pauses") or [])
            if isinstance(pause, dict) and not pause.get("finished_at")
        ),
        None,
    )
    if active is None:
        raise ValueError("Workflow is not paused")
    finished_at = now_iso()
    active["finished_at"] = finished_at
    active["duration_seconds"] = elapsed_seconds(str(active.get("started_at")), finished_at)
    _refresh_run(run)
    save_metrics(path, data)


def _find_stage(run: dict[str, Any], stage: str) -> dict[str, Any]:
    for item in reversed(run.get("stages") or []):
        if not isinstance(item, dict):
            continue
        if item.get("stage_id") == stage or item.get("name") == stage:
            return item
    raise ValueError(f"Workflow stage not found: {stage}")


def record_token_usage(
    path: str | Path,
    *,
    run_id: str | None,
    stage: str,
    source: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    reasoning_tokens: int = 0,
    total_tokens: int | None = None,
    model: str = "",
    call_id: str = "",
) -> None:
    normalized_source = source.strip().lower()
    if normalized_source not in EXACT_TOKEN_SOURCES:
        raise ValueError(f"Token source must be exact and one of {sorted(EXACT_TOKEN_SOURCES)}")
    values = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached_input_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": input_tokens + output_tokens if total_tokens is None else total_tokens,
    }
    if any(type(value) is not int or value < 0 for value in values.values()):
        raise ValueError("Token counts must be non-negative integers")
    if values["total_tokens"] != values["input_tokens"] + values["output_tokens"]:
        raise ValueError("total_tokens must equal input_tokens + output_tokens")
    if values["cached_input_tokens"] > values["input_tokens"]:
        raise ValueError("cached_input_tokens cannot exceed input_tokens")
    if values["reasoning_tokens"] > values["output_tokens"]:
        raise ValueError("reasoning_tokens cannot exceed output_tokens")

    data = load_metrics(path)
    run = _find_run(data, run_id)
    target = _find_stage(run, stage)
    if target.get("executor") not in MODEL_EXECUTORS:
        raise ValueError("Token usage can only be recorded for model or mixed stages")
    if target.get("status") != "running":
        raise ValueError("Token usage must be recorded before the stage is finished")
    target.setdefault("token_events", []).append(
        {
            "recorded_at": now_iso(),
            "source": normalized_source,
            "measurement": "exact",
            "model": model,
            "call_id": call_id,
            **values,
        }
    )
    _refresh_stage(target)
    _refresh_run(run)
    save_metrics(path, data)


def finish_stage(
    path: str | Path,
    *,
    run_id: str | None,
    stage: str,
    status: str,
    token_coverage: str | None = None,
    unavailable_reason: str = "",
) -> None:
    normalized_status = status.strip().lower()
    if normalized_status not in VALID_STAGE_STATUSES - {"running"}:
        raise ValueError(f"Invalid completed stage status: {status}")
    data = load_metrics(path)
    run = _find_run(data, run_id)
    target = _find_stage(run, stage)
    if target.get("status") != "running":
        raise ValueError(f"Stage is not running: {stage}")
    executor = str(target.get("executor"))
    events = target.get("token_events") or []
    if executor == "deterministic":
        coverage = "not_applicable"
    else:
        coverage = (token_coverage or ("partial" if events else "unavailable")).strip().lower()
        if coverage not in {"exact", "partial", "unavailable"}:
            raise ValueError("Model/mixed stage token_coverage must be exact/partial/unavailable")
        if coverage == "exact" and not events:
            raise ValueError("Exact token coverage requires at least one exact usage event")
        if coverage == "unavailable" and not unavailable_reason:
            raise ValueError("Unavailable token coverage requires an explicit reason")
    finished_at = now_iso()
    target.update(
        {
            "status": normalized_status,
            "finished_at": finished_at,
            "duration_seconds": elapsed_seconds(str(target.get("started_at")), finished_at),
            "token_coverage": coverage,
            "token_unavailable_reason": unavailable_reason if coverage != "not_applicable" else "",
        }
    )
    _refresh_stage(target)
    _refresh_run(run)
    save_metrics(path, data)


def finalize_run(
    path: str | Path,
    *,
    run_id: str | None,
    status: str,
    note: str = "",
) -> None:
    normalized_status = status.strip().lower()
    if normalized_status not in VALID_RUN_STATUSES - {"running"}:
        raise ValueError(f"Invalid final workflow status: {status}")
    data = load_metrics(path)
    run = _find_run(data, run_id)
    if any(not pause.get("finished_at") for pause in run.get("pauses") or [] if isinstance(pause, dict)):
        raise ValueError("Cannot finalize while the workflow is paused; resume it first")
    running = [stage.get("stage_id") for stage in run.get("stages") or [] if stage.get("status") == "running"]
    if running:
        raise ValueError(f"Cannot finalize while stages are running: {running}")
    finished_at = now_iso()
    run.update(
        {
            "status": normalized_status,
            "finished_at": finished_at,
            "duration_seconds": elapsed_seconds(str(run.get("started_at")), finished_at),
        }
    )
    if note:
        run.setdefault("notes", []).append(note)
    _refresh_run(run)
    save_metrics(path, data)


def _sum_events(events: list[dict[str, Any]]) -> dict[str, int]:
    return {
        field: sum(
            value
            for event in events
            if isinstance(event, dict)
            for value in [event.get(field)]
            if type(value) is int and value >= 0
        )
        for field in TOKEN_FIELDS
    }


def _refresh_stage(stage: dict[str, Any]) -> None:
    events = [item for item in stage.get("token_events") or [] if isinstance(item, dict)]
    usage = _sum_events(events)
    usage.update(
        {
            "measurement_status": stage.get("token_coverage"),
            "exact_call_count": len(events),
        }
    )
    stage["token_usage"] = usage


def _refresh_run(run: dict[str, Any]) -> None:
    stages = [item for item in run.get("stages") or [] if isinstance(item, dict)]
    for stage in stages:
        _refresh_stage(stage)
    model_stages = [stage for stage in stages if stage.get("executor") in MODEL_EXECUTORS]
    events = [
        event
        for stage in model_stages
        for event in stage.get("token_events") or []
        if isinstance(event, dict)
    ]
    totals = _sum_events(events)
    incomplete = [
        str(stage.get("name"))
        for stage in model_stages
        if stage.get("token_coverage") != "exact"
    ]
    if not model_stages:
        measurement_status = "not_applicable"
        exact_total: int | None = 0
    elif not incomplete and run.get("status") != "running":
        measurement_status = "exact"
        exact_total = totals["total_tokens"]
    elif events:
        measurement_status = "partial"
        exact_total = None
    else:
        measurement_status = "unavailable"
        exact_total = None
    run["token_usage"] = {
        **totals,
        "measured_total_tokens": totals["total_tokens"],
        "exact_total_tokens": exact_total,
        "measurement_status": measurement_status,
        "model_stage_count": len(model_stages),
        "exact_model_stage_count": sum(stage.get("token_coverage") == "exact" for stage in model_stages),
        "incomplete_model_stages": incomplete,
        "exact_call_count": len(events),
        "rule": "Cached input is included in input tokens; reasoning tokens are included in output tokens and are not added again.",
    }
    run["stage_duration_seconds"] = round(
        sum(float(stage.get("duration_seconds") or 0) for stage in stages), 3
    )
    pause_seconds = 0.0
    for pause in run.get("pauses") or []:
        if not isinstance(pause, dict) or not pause.get("started_at"):
            continue
        duration = pause.get("duration_seconds")
        if isinstance(duration, (int, float)) and duration >= 0:
            pause_seconds += float(duration)
        elif not pause.get("finished_at"):
            pause_seconds += elapsed_seconds(str(pause.get("started_at")))
    run["paused_seconds"] = round(pause_seconds, 3)
    if run.get("status") == "running":
        run["elapsed_seconds"] = elapsed_seconds(str(run.get("started_at")))
        wall_seconds = run["elapsed_seconds"]
    else:
        run.pop("elapsed_seconds", None)
        wall_seconds = float(run.get("duration_seconds") or 0)
    run["active_duration_seconds"] = round(max(0.0, wall_seconds - pause_seconds), 3)


def current_run(path: str | Path, run_id: str | None = None) -> dict[str, Any]:
    data = load_metrics(path)
    run = _find_run(data, run_id)
    _refresh_run(run)
    return run


def validate_metrics_document(path: str | Path, run_id: str | None = None) -> list[str]:
    try:
        data = load_metrics(path)
        run = _find_run(data, run_id)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [str(exc)]
    errors: list[str] = []
    run_ids = [str(item.get("run_id") or "") for item in data.get("runs") or [] if isinstance(item, dict)]
    if not text_values_unique(run_ids):
        errors.append("runs must have non-blank unique run_id values")
    if run.get("status") not in VALID_RUN_STATUSES:
        errors.append("run.status is invalid")
    if not str(run.get("started_at") or ""):
        errors.append("run.started_at is required")
    if run.get("status") != "running":
        if not str(run.get("finished_at") or ""):
            errors.append("finished run needs finished_at")
        if not isinstance(run.get("duration_seconds"), (int, float)) or run.get("duration_seconds") < 0:
            errors.append("finished run needs non-negative duration_seconds")
        else:
            try:
                expected_duration = elapsed_seconds(str(run.get("started_at")), str(run.get("finished_at")))
                if abs(float(run.get("duration_seconds")) - expected_duration) > 0.001:
                    errors.append(f"run.duration_seconds must equal {expected_duration}")
            except (TypeError, ValueError):
                errors.append("run start/finish timestamps must be valid ISO datetimes")
    if run.get("status") != "running" and any(
        isinstance(stage, dict) and stage.get("status") == "running"
        for stage in run.get("stages") or []
    ):
        errors.append("finished run cannot contain a running stage")
    active_pause_count = 0
    for index, pause in enumerate(run.get("pauses") or [], 1):
        if not isinstance(pause, dict):
            errors.append(f"pauses[{index}] must be a mapping")
            continue
        if not str(pause.get("reason") or "").strip():
            errors.append(f"pauses[{index}].reason is required")
        if not str(pause.get("started_at") or "").strip():
            errors.append(f"pauses[{index}].started_at is required")
        if not pause.get("finished_at"):
            active_pause_count += 1
        elif not isinstance(pause.get("duration_seconds"), (int, float)) or pause.get("duration_seconds") < 0:
            errors.append(f"pauses[{index}] needs non-negative duration_seconds")
        else:
            try:
                expected_pause = elapsed_seconds(str(pause.get("started_at")), str(pause.get("finished_at")))
                if abs(float(pause.get("duration_seconds")) - expected_pause) > 0.001:
                    errors.append(f"pauses[{index}].duration_seconds must equal {expected_pause}")
            except (TypeError, ValueError):
                errors.append(f"pauses[{index}] timestamps must be valid ISO datetimes")
    if active_pause_count > 1:
        errors.append("workflow can have at most one active pause")
    if run.get("status") != "running" and active_pause_count:
        errors.append("finished run cannot contain an active pause")
    stage_ids = [
        str(stage.get("stage_id") or "")
        for stage in run.get("stages") or []
        if isinstance(stage, dict)
    ]
    if not text_values_unique(stage_ids):
        errors.append("stages must have non-blank unique stage_id values")
    all_events: list[dict[str, Any]] = []
    incomplete_model_stages: list[str] = []
    for index, stage in enumerate(run.get("stages") or [], 1):
        if not isinstance(stage, dict):
            errors.append(f"stages[{index}] must be a mapping")
            continue
        if stage.get("executor") not in VALID_EXECUTORS:
            errors.append(f"stages[{index}].executor is invalid")
        if stage.get("status") not in VALID_STAGE_STATUSES:
            errors.append(f"stages[{index}].status is invalid")
        if stage.get("status") != "running":
            if not isinstance(stage.get("duration_seconds"), (int, float)) or stage.get("duration_seconds") < 0:
                errors.append(f"stages[{index}] needs non-negative duration_seconds")
            else:
                try:
                    expected_stage_duration = elapsed_seconds(
                        str(stage.get("started_at")), str(stage.get("finished_at"))
                    )
                    if abs(float(stage.get("duration_seconds")) - expected_stage_duration) > 0.001:
                        errors.append(
                            f"stages[{index}].duration_seconds must equal {expected_stage_duration}"
                        )
                except (TypeError, ValueError):
                    errors.append(f"stages[{index}] timestamps must be valid ISO datetimes")
            if stage.get("executor") == "deterministic" and stage.get("token_coverage") != "not_applicable":
                errors.append(f"stages[{index}] deterministic token_coverage must be not_applicable")
            if stage.get("executor") in MODEL_EXECUTORS and stage.get("token_coverage") not in {
                "exact",
                "partial",
                "unavailable",
            }:
                errors.append(f"stages[{index}] model token_coverage must be exact/partial/unavailable")
        events = [event for event in stage.get("token_events") or [] if isinstance(event, dict)]
        if len(events) != len(stage.get("token_events") or []):
            errors.append(f"stages[{index}].token_events must contain only mappings")
        if stage.get("executor") == "deterministic" and events:
            errors.append(f"stages[{index}] deterministic stage cannot contain token events")
        for event_index, event in enumerate(events, 1):
            prefix = f"stages[{index}].token_events[{event_index}]"
            if event.get("source") not in EXACT_TOKEN_SOURCES:
                errors.append(f"{prefix}.source is not an exact supported source")
            if event.get("measurement") != "exact":
                errors.append(f"{prefix}.measurement must be exact")
            values = {field: event.get(field) for field in TOKEN_FIELDS}
            if any(type(value) is not int or value < 0 for value in values.values()):
                errors.append(f"{prefix} token counts must be non-negative integers")
                continue
            if values["total_tokens"] != values["input_tokens"] + values["output_tokens"]:
                errors.append(f"{prefix}.total_tokens must equal input_tokens + output_tokens")
            if values["cached_input_tokens"] > values["input_tokens"]:
                errors.append(f"{prefix}.cached_input_tokens cannot exceed input_tokens")
            if values["reasoning_tokens"] > values["output_tokens"]:
                errors.append(f"{prefix}.reasoning_tokens cannot exceed output_tokens")
        expected_stage_usage = _sum_events(events)
        stored_stage_usage = stage.get("token_usage") or {}
        for field, expected in expected_stage_usage.items():
            if stored_stage_usage.get(field) != expected:
                errors.append(f"stages[{index}].token_usage.{field} must equal {expected}")
        if stored_stage_usage.get("measurement_status") != stage.get("token_coverage"):
            errors.append(
                f"stages[{index}].token_usage.measurement_status must match token_coverage"
            )
        if stored_stage_usage.get("exact_call_count") != len(events):
            errors.append(f"stages[{index}].token_usage.exact_call_count must equal {len(events)}")
        coverage = stage.get("token_coverage")
        if stage.get("executor") in MODEL_EXECUTORS:
            if stage.get("status") != "running" and coverage == "exact" and not events:
                errors.append(f"stages[{index}] exact token coverage requires token events")
            if stage.get("status") != "running" and coverage == "unavailable" and not str(
                stage.get("token_unavailable_reason") or ""
            ).strip():
                errors.append(f"stages[{index}] unavailable token coverage requires a reason")
            if coverage != "exact":
                incomplete_model_stages.append(str(stage.get("name") or ""))
            all_events.extend(events)

    expected_totals = _sum_events(all_events)
    stored_run_usage = run.get("token_usage") or {}
    for field, expected in expected_totals.items():
        if stored_run_usage.get(field) != expected:
            errors.append(f"run.token_usage.{field} must equal {expected}")
    model_stage_count = sum(
        isinstance(stage, dict) and stage.get("executor") in MODEL_EXECUTORS
        for stage in run.get("stages") or []
    )
    if not model_stage_count:
        expected_measurement = "not_applicable"
        expected_exact_total: int | None = 0
    elif not incomplete_model_stages and run.get("status") != "running":
        expected_measurement = "exact"
        expected_exact_total = expected_totals["total_tokens"]
    elif all_events:
        expected_measurement = "partial"
        expected_exact_total = None
    else:
        expected_measurement = "unavailable"
        expected_exact_total = None
    if stored_run_usage.get("measurement_status") != expected_measurement:
        errors.append(f"run.token_usage.measurement_status must be {expected_measurement}")
    if stored_run_usage.get("exact_total_tokens") != expected_exact_total:
        errors.append(f"run.token_usage.exact_total_tokens must be {expected_exact_total!r}")
    if stored_run_usage.get("measured_total_tokens") != expected_totals["total_tokens"]:
        errors.append(
            f"run.token_usage.measured_total_tokens must equal {expected_totals['total_tokens']}"
        )
    if stored_run_usage.get("model_stage_count") != model_stage_count:
        errors.append(f"run.token_usage.model_stage_count must equal {model_stage_count}")
    exact_model_stage_count = sum(
        isinstance(stage, dict)
        and stage.get("executor") in MODEL_EXECUTORS
        and stage.get("token_coverage") == "exact"
        for stage in run.get("stages") or []
    )
    if stored_run_usage.get("exact_model_stage_count") != exact_model_stage_count:
        errors.append(
            f"run.token_usage.exact_model_stage_count must equal {exact_model_stage_count}"
        )
    if stored_run_usage.get("incomplete_model_stages") != incomplete_model_stages:
        errors.append("run.token_usage.incomplete_model_stages does not match stage coverage")
    if stored_run_usage.get("exact_call_count") != len(all_events):
        errors.append(f"run.token_usage.exact_call_count must equal {len(all_events)}")

    expected_pause_total = round(
        sum(
            float(pause.get("duration_seconds") or 0)
            for pause in run.get("pauses") or []
            if isinstance(pause, dict) and pause.get("finished_at")
        ),
        3,
    )
    if run.get("status") != "running" and run.get("paused_seconds") != expected_pause_total:
        errors.append(f"run.paused_seconds must equal {expected_pause_total}")
    if run.get("status") != "running" and isinstance(run.get("duration_seconds"), (int, float)):
        expected_active = round(max(0.0, float(run.get("duration_seconds")) - expected_pause_total), 3)
        if run.get("active_duration_seconds") != expected_active:
            errors.append(f"run.active_duration_seconds must equal {expected_active}")
    return errors


def text_values_unique(values: list[str]) -> bool:
    return all(value.strip() for value in values) and len(values) == len(set(values))
