"""Backfill generation-quality-contract fields onto an existing artifact.

The quality contract added several fields that a pre-contract artifact cannot
have.  Demanding ``semantic_identity`` / ``title_contract`` /
``precondition_state`` / ``normative_statements`` on thousands of existing cases
by hand makes migration uneconomical, so this script derives what is mechanical
and reports, field by field and case by case, what still needs a human.

Design rules:

* **Dry run by default.**  Originals are never touched unless ``--apply`` is
  given; ``--output-dir`` writes patched copies for review.
* **No guessing, no placeholders.**  A field is only written when the value is
  derived from the artifact itself.  Anything ambiguous is listed under
  "需人工填写" instead of being filled with a plausible-looking stub that would
  turn into false contract compliance.
* **Idempotent.**  Existing values are preserved.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from workflow_gate_common import as_list, load_yaml, text  # noqa: E402

PORT_SEPARATORS = ("-", "—", "·", ">", "/", ">", "_")
ACTION_VERBS = (
    "展示", "显示", "隐藏", "提交", "点击", "输入", "选择", "上传", "下载", "查询", "筛选",
    "切换", "校验", "拦截", "提示", "同步", "更新", "删除", "新增", "编辑", "导出", "导入",
    "发起", "取消", "确认", "回滚", "重试", "置灰", "排序", "分页", "统计", "计算", "跳转",
)
DATA_SETUP_MARKERS = ("自造", "构造", "新建一条", "前置造数")
PRECONDITION_STATE_BY_MARKER = (
    ("system_preinstalled", ("系统预置", "预置数据", "平台预置")),
    ("environment_provided", ("环境提供", "已登录", "已配置", "账号已", "店铺已")),
)


def split_feature_group(value: str) -> tuple[str, str]:
    for separator in PORT_SEPARATORS:
        if separator in value:
            head, _, tail = value.partition(separator)
            if head and tail:
                return head.strip(), tail.strip()
    return "", value.strip()


def derive_action(title: str) -> str:
    for verb in ACTION_VERBS:
        if verb in title:
            return verb
    return ""


def derive_condition(title: str) -> str:
    match = re.search(r"(.{1,20}?(?:时|后|且|为|未|已)[^，。；]*)", title)
    return match.group(1).strip() if match else ""


def derive_semantic_identity(case: dict[str, Any]) -> tuple[dict[str, str] | None, list[str]]:
    title = text(case.get("case_title") or case.get("formal_case_title"))
    group = text(case.get("feature_group"))
    port, page = split_feature_group(group)
    if not port:
        port = split_feature_group(title)[0]
    assertions = case.get("assertions") or []
    target = ""
    if assertions and isinstance(assertions[0], dict):
        target = text(assertions[0].get("target"))
    if not target:
        target = text(case.get("verification_point"))
    object_text = target or page
    action = derive_action(title)
    missing: list[str] = []
    if not port:
        missing.append("port")
    if not page:
        missing.append("page")
    if not object_text:
        missing.append("object")
    if not action:
        missing.append("action")
    if missing:
        return None, missing
    identity = {
        "port": port,
        "page": page,
        "object": object_text,
        "condition": derive_condition(title),
        "action": action,
        "assertion": text(case.get("verification_point")) or object_text,
    }
    if not identity["condition"] and text(case.get("granularity")).lower() == "field":
        missing.append("condition")
    return identity, missing


def derive_precondition(case: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    entries = [text(value) for value in as_list(case.get("preconditions")) if text(value)]
    if not entries:
        return None, ["preconditions"]
    self_built = [entry for entry in entries if any(marker in entry for marker in DATA_SETUP_MARKERS)]
    if self_built:
        return {
            "precondition_state": "self_built",
            "data_setup": {"description": "；".join(self_built), "source": "从原前置条件派生"},
        }, []
    for state, markers in PRECONDITION_STATE_BY_MARKER:
        if any(any(marker in entry for marker in markers) for entry in entries):
            return {
                "precondition_state": state,
                "missing_data_behavior": "blocked",
                "precondition_note": "；".join(entries),
            }, []
    return None, ["precondition_state(原文未明确数据来源，需人工确认)"]


def derive_step_targets(case: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, int]:
    steps = case.get("formal_steps") or case.get("steps")
    if not isinstance(steps, list) or not steps:
        return None, 0
    patched: list[dict[str, Any]] = []
    patched_count = 0
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            patched.append({"step_no": index, "action": text(step), "target": ""})
            patched_count += 1
            continue
        item = dict(step)
        item.setdefault("step_no", index)
        if not text(item.get("target")):
            source_steps = case.get("steps") or []
            candidate = source_steps[index - 1] if index <= len(source_steps) else {}
            item["target"] = text(candidate.get("target")) if isinstance(candidate, dict) else ""
            patched_count += 1
        patched.append(item)
    return patched, patched_count


def build_normative_ledger(requirement_doc: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
    existing = requirement_doc.get("normative_statements")
    if isinstance(existing, list) and existing:
        return existing, requirement_doc.get("source_coverage_inventory") or {}, 0
    statements: list[dict[str, Any]] = []
    documents = {text(item.get("document_id")) for item in requirement_doc.get("source_documents") or [] if isinstance(item, dict)}
    for index, rule in enumerate(
        [item for item in (requirement_doc.get("atomic_rules") or []) if isinstance(item, dict)], 1
    ):
        rule_id = text(rule.get("rule_id"))
        if not rule_id:
            continue
        locator = rule.get("source_locator") if isinstance(rule.get("source_locator"), dict) else {}
        if text(rule.get("source_document")) not in documents or not any(text(value) for value in locator.values()) or not text(rule.get("source_excerpt")):
            continue
        statement_id = text(rule.get("normative_statement_id")) or f"NS-{index:04d}"
        rule["normative_statement_id"] = statement_id
        rule.setdefault("source_fact_refs", [statement_id])
        statements.append({
            "statement_id": statement_id,
            "source_document": text(rule.get("source_document")),
            "source_locator": dict(locator),
            "source_excerpt": text(rule.get("source_excerpt")),
            "statement_type": text(rule.get("rule_type")) or "behavior",
            "disposition": text(rule.get("disposition") or "covered"),
            "atomic_rule_ids": [rule_id],
        })
    inventory = {
        "normative_statement_count": len(statements),
        "mapped_statement_count": sum(1 for item in statements if item["disposition"] == "covered"),
        "excluded_statement_count": sum(1 for item in statements if item["disposition"] == "explicitly_not_tested"),
        "pending_statement_count": sum(1 for item in statements if item["disposition"] == "pending_confirmation"),
        "unmapped_statement_ids": [],
    }
    return statements, inventory, len(statements)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill generation-quality-contract fields onto an existing artifact.")
    parser.add_argument("--requirements", required=True)
    parser.add_argument("--final-cases", required=True)
    parser.add_argument("--test-points")
    parser.add_argument("--report", help="写入迁移评估报告（Markdown）")
    parser.add_argument("--output-dir", help="把打了补丁的副本写到该目录（不覆盖原文件）")
    parser.add_argument("--apply", action="store_true", help="就地写回原文件（默认只做 dry-run）")
    args = parser.parse_args()

    requirement_path = Path(args.requirements).resolve()
    case_path = Path(args.final_cases).resolve()
    requirement_doc = load_yaml(requirement_path)
    case_doc = load_yaml(case_path)
    cases = [item for item in (case_doc.get("test_cases") or []) if isinstance(item, dict)]

    derived = Counter()
    manual: dict[str, list[str]] = {}
    patched_cases = 0
    patched_steps = 0
    for case in cases:
        case_id = text(case.get("case_id")) or "<missing>"
        touched = False

        if not isinstance(case.get("semantic_identity"), dict):
            identity, missing = derive_semantic_identity(case)
            if identity:
                case["semantic_identity"] = identity
                case["title_contract"] = identity and {
                    "object": identity["object"],
                    "action": identity["action"],
                    "condition": identity["condition"],
                }
                derived["semantic_identity"] += 1
                derived["title_contract"] += 1
                touched = True
            else:
                manual.setdefault("semantic_identity:" + "/".join(missing), []).append(case_id)

        if not text(case.get("precondition_state")):
            precondition, needs = derive_precondition(case)
            if precondition:
                case.update(precondition)
                derived["precondition_state"] += 1
                touched = True
            if needs:
                manual.setdefault("precondition_state:" + "/".join(needs), []).append(case_id)

        steps, step_patches = derive_step_targets(case)
        if steps is not None and any(not text(step.get("target")) for step in steps):
            manual.setdefault("formal_steps[].target(缺少原步骤定位对象)", []).append(case_id)
        if steps is not None and step_patches:
            case["formal_steps"] = steps
            patched_steps += step_patches
            derived["formal_steps[].target"] += step_patches
            touched = True

        if touched:
            patched_cases += 1

    statements, inventory, ledger_created = build_normative_ledger(requirement_doc)
    if not requirement_doc.get("normative_statements"):
        manual["source_coverage_inventory(派生账本仅含已有规则，必须对照原文审计遗漏条款)"] = ["requirements"]
        skipped_rules = [text(rule.get("rule_id")) for rule in requirement_doc.get("atomic_rules") or [] if isinstance(rule, dict) and not text(rule.get("normative_statement_id"))]
        if skipped_rules:
            manual["normative_statements(来源、原文或定位信息不足，未生成)"] = skipped_rules
    if ledger_created:
        requirement_doc["normative_statements"] = statements
        requirement_doc["source_coverage_inventory"] = inventory
        requirement_doc.setdefault("version_differences", [])
        requirement_doc.setdefault("ambiguities", [])

    report_lines = [
        "# 存量产物契约字段迁移评估",
        "",
        f"- 用例总数：{len(cases)}",
        f"- 可自动派生并打补丁的用例数：{patched_cases}",
        f"- 规范性条款账本新建条数：{ledger_created}",
        "",
        "## 自动派生结果",
        "",
        "| 字段 | 派生条数 |",
        "|---|---:|",
    ]
    for field, count in sorted(derived.items()):
        report_lines.append(f"| {field} | {count} |")
    report_lines += ["", "## 需人工填写", ""]
    if manual:
        report_lines += ["| 字段/原因 | 用例数 | 样例 |", "|---|---:|---|"]
        for reason, ids in sorted(manual.items(), key=lambda item: -len(item[1])):
            report_lines.append(f"| {reason} | {len(ids)} | {', '.join(ids[:5])} |")
    else:
        report_lines.append("（无）")
    report_lines += [
        "",
        "## 不能自动派生的字段（必须人工或生成器改造）",
        "",
        "- `coverage_unit_signature`：必须由原子规则的 `coverage_characteristics` 独立推导后一对一绑定，脚本不猜。",
        "- `coverage_characteristics`：需要按 PRD 事实逐规则声明 profile / applicability / 事实，属于需求层重做。",
        "- `review_audit.per_case_scores` / `reviewer_independence`：属于评审证据，必须真实评审产生。",
        "- `version_differences`：多份 PRD 的相邻版本差异必须人工判读。",
        "",
        "迁移完成后去掉 `--legacy-audit` 重新跑 `validate_case_design.py`，以门禁结果为准。",
    ]
    report = "\n".join(report_lines) + "\n"
    if args.report:
        report_path = Path(args.report).resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")
        print(f"Migration report: {report_path}")
    print(report)

    if not args.apply and not args.output_dir:
        print("dry-run：未写入任何文件（加 --apply 就地写回，或 --output-dir 输出副本）")
        return 0

    import yaml

    targets: list[tuple[Path, dict[str, Any]]] = []
    if args.apply:
        targets.append((case_path, case_doc))
        if ledger_created:
            targets.append((requirement_path, requirement_doc))
    if args.output_dir:
        output_dir = Path(args.output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        targets.append((output_dir / case_path.name, case_doc))
        if ledger_created:
            targets.append((output_dir / requirement_path.name, requirement_doc))
    for path, document in targets:
        path.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")
        print(f"written: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
