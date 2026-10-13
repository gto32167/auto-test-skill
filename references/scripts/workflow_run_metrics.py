from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


FRAMEWORK_ROOT = Path(__file__).resolve().parents[2] / "builtin_framework"
if str(FRAMEWORK_ROOT) not in sys.path:
    sys.path.insert(0, str(FRAMEWORK_ROOT))

from common.run_metrics import (  # noqa: E402
    current_run,
    finalize_run,
    finish_stage,
    initialize_run,
    record_token_usage,
    pause_run,
    resume_run,
    start_stage,
    validate_metrics_document,
)


def _usage_payload(path: str) -> dict[str, Any]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    value = json.loads(raw)
    if isinstance(value, dict) and isinstance(value.get("usage"), dict):
        value = value["usage"]
    if not isinstance(value, dict):
        raise ValueError("Usage JSON must be an object or contain an object at usage")
    input_details = value.get("input_tokens_details") or value.get("prompt_tokens_details") or {}
    output_details = value.get("output_tokens_details") or value.get("completion_tokens_details") or {}
    return {
        "input_tokens": value.get("input_tokens", value.get("prompt_tokens", 0)),
        "output_tokens": value.get("output_tokens", value.get("completion_tokens", 0)),
        "cached_input_tokens": input_details.get("cached_tokens", 0) if isinstance(input_details, dict) else 0,
        "reasoning_tokens": output_details.get("reasoning_tokens", 0) if isinstance(output_details, dict) else 0,
        "total_tokens": value.get("total_tokens"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record workflow duration and exact model token usage.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--file", required=True)
    init_parser.add_argument("--project-dir", required=True)
    init_parser.add_argument("--mode", required=True)
    init_parser.add_argument("--run-id", default="")
    init_parser.add_argument("--trigger", default="user_request")

    start_parser = subparsers.add_parser("start-stage")
    start_parser.add_argument("--file", required=True)
    start_parser.add_argument("--run-id", default="")
    start_parser.add_argument("--stage", required=True)
    start_parser.add_argument("--executor", choices=["model", "deterministic", "mixed"], required=True)

    usage_parser = subparsers.add_parser("record-usage")
    usage_parser.add_argument("--file", required=True)
    usage_parser.add_argument("--run-id", default="")
    usage_parser.add_argument("--stage", required=True)
    usage_parser.add_argument(
        "--source",
        choices=["provider_response", "platform_export", "codex_usage_snapshot", "manual_exact"],
        required=True,
    )
    usage_parser.add_argument("--usage-json", default="")
    usage_parser.add_argument("--input-tokens", type=int, default=0)
    usage_parser.add_argument("--output-tokens", type=int, default=0)
    usage_parser.add_argument("--cached-input-tokens", type=int, default=0)
    usage_parser.add_argument("--reasoning-tokens", type=int, default=0)
    usage_parser.add_argument("--total-tokens", type=int)
    usage_parser.add_argument("--model", default="")
    usage_parser.add_argument("--call-id", default="")

    pause_parser = subparsers.add_parser("pause")
    pause_parser.add_argument("--file", required=True)
    pause_parser.add_argument("--run-id", default="")
    pause_parser.add_argument("--reason", required=True)

    resume_parser = subparsers.add_parser("resume")
    resume_parser.add_argument("--file", required=True)
    resume_parser.add_argument("--run-id", default="")

    finish_parser = subparsers.add_parser("finish-stage")
    finish_parser.add_argument("--file", required=True)
    finish_parser.add_argument("--run-id", default="")
    finish_parser.add_argument("--stage", required=True)
    finish_parser.add_argument("--status", choices=["completed", "failed", "skipped", "blocked"], required=True)
    finish_parser.add_argument("--token-coverage", choices=["exact", "partial", "unavailable"])
    finish_parser.add_argument("--unavailable-reason", default="")

    final_parser = subparsers.add_parser("finalize")
    final_parser.add_argument("--file", required=True)
    final_parser.add_argument("--run-id", default="")
    final_parser.add_argument("--status", choices=["completed", "failed", "cancelled", "blocked"], required=True)
    final_parser.add_argument("--note", default="")

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("--file", required=True)
    show_parser.add_argument("--run-id", default="")

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--file", required=True)
    validate_parser.add_argument("--run-id", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    run_id = args.run_id or None
    try:
        if args.command == "init":
            identifier = initialize_run(
                args.file,
                project_dir=args.project_dir,
                workflow_mode=args.mode,
                run_id=run_id,
                trigger=args.trigger,
            )
            print(identifier)
        elif args.command == "start-stage":
            print(start_stage(args.file, run_id=run_id, name=args.stage, executor=args.executor))
        elif args.command == "record-usage":
            usage = _usage_payload(args.usage_json) if args.usage_json else {
                "input_tokens": args.input_tokens,
                "output_tokens": args.output_tokens,
                "cached_input_tokens": args.cached_input_tokens,
                "reasoning_tokens": args.reasoning_tokens,
                "total_tokens": args.total_tokens,
            }
            record_token_usage(
                args.file,
                run_id=run_id,
                stage=args.stage,
                source=args.source,
                model=args.model,
                call_id=args.call_id,
                **usage,
            )
        elif args.command == "finish-stage":
            finish_stage(
                args.file,
                run_id=run_id,
                stage=args.stage,
                status=args.status,
                token_coverage=args.token_coverage,
                unavailable_reason=args.unavailable_reason,
            )
        elif args.command == "pause":
            pause_run(args.file, run_id=run_id, reason=args.reason)
        elif args.command == "resume":
            resume_run(args.file, run_id=run_id)
        elif args.command == "finalize":
            finalize_run(args.file, run_id=run_id, status=args.status, note=args.note)
        elif args.command == "show":
            print(json.dumps(current_run(args.file, run_id), ensure_ascii=False, indent=2))
        elif args.command == "validate":
            errors = validate_metrics_document(args.file, run_id)
            if errors:
                for error in errors:
                    print(f"ERROR: {error}", file=sys.stderr)
                return 1
            print("workflow metrics: pass")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
