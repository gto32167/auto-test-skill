from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
import sys

FRAMEWORK_ROOT = Path(__file__).resolve().parents[2] / "builtin_framework"
if str(FRAMEWORK_ROOT) not in sys.path:
    sys.path.insert(0, str(FRAMEWORK_ROOT))
from common.run_metrics import current_run, validate_metrics_document

from workflow_gate_common import (
    list_from,
    load_json,
    load_yaml,
    normalize_execution_status,
    sha256_file,
    text,
    verify_passed_gate,
)
from execution_quality import execution_metrics
from result_explanation import explain_result
from word_report import write_docx_from_markdown


def fmt_produced_data(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    return "；".join(f"{text(key)}:{text(value)}" for key, value in data.items() if text(key) and text(value))


def md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join([":---"] * len(headers)) + " |",
    ]
    for row in rows or [[""] * len(headers)]:
        lines.append("| " + " | ".join(text(value).replace("|", "/") for value in row) + " |")
    return lines


def require_gate_role(path: Path, gate_name: str, phase: str | None = None) -> dict[str, Any]:
    gate = load_json(path)
    if gate.get("status") != "pass" or gate.get("gate") != gate_name:
        raise SystemExit(f"Required gate did not pass or has wrong role: {path}")
    if phase is not None and gate.get("phase") != phase:
        raise SystemExit(f"Gate {path} must have phase={phase}")
    return gate


def severity_suggestion(case: dict[str, Any], result: dict[str, Any]) -> str:
    defect = result.get("defect") or {}
    if text(defect.get("severity_suggestion")):
        return text(defect.get("severity_suggestion"))
    priority = text(case.get("priority")).upper()
    summary = result.get("result_summary") or {}
    combined = " ".join(
        [text(case.get("case_title") or case.get("title")), text(result.get("defect_summary")), text(summary.get("actual"))]
    )
    mutation = summary.get("mutation")
    if priority == "P0" or mutation is True or any(word in combined for word in ("资金", "金额", "库存", "权限", "负数", "越权")):
        return "严重"
    return "主要" if priority == "P1" else "次要"


def scope_rows(meta: dict[str, Any]) -> list[list[str]]:
    rows = []
    for item in meta.get("scope_matrix") or []:
        if not isinstance(item, dict):
            continue
        rows.append([
            text(item.get("track")),
            text(item.get("status")),
            text(item.get("coverage")),
            text(item.get("reason")),
            text(item.get("artifact")) or "未产出",
        ])
    return rows


def format_duration(seconds: Any) -> str:
    if not isinstance(seconds, (int, float)) or seconds < 0:
        return "不可用"
    total = int(round(float(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes}分{secs}秒"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"

def resource_accounting_rows(workflow_run: dict[str, Any] | None) -> tuple[list[list[str]], list[list[str]], str]:
    if workflow_run is None:
        return [["工作流运行统计", "历史产物未启用"]], [], "历史产物未启用运行账本。"
    usage = workflow_run.get("token_usage") or {}
    measurement = text(usage.get("measurement_status"))
    measurement_labels = {
        "exact": "精确",
        "partial": "部分采集",
        "unavailable": "不可用",
        "not_applicable": "不适用",
    }
    if measurement in {"exact", "partial"}:
        measured = lambda field: str(usage.get(field, 0))
    elif measurement == "not_applicable":
        measured = lambda field: "0（没有模型阶段）"
    else:
        measured = lambda field: "不可用"
    exact_total = usage.get("exact_total_tokens")
    incomplete = []
    for stage in workflow_run.get("stages") or []:
        if not isinstance(stage, dict) or stage.get("executor") not in {"model", "mixed"}:
            continue
        if stage.get("token_coverage") == "exact":
            continue
        reason = text(stage.get("token_unavailable_reason")) or "未取得精确 usage 元数据"
        incomplete.append(f"{text(stage.get('name'))}: {reason}")
    rows = [
        ["工作流运行 ID", text(workflow_run.get("run_id"))],
        ["工作流状态", text(workflow_run.get("status"))],
        ["开始时间", text(workflow_run.get("started_at"))],
        ["结束时间", text(workflow_run.get("finished_at")) or "尚未结束"],
        ["工作流总时长", format_duration(workflow_run.get("duration_seconds"))],
        ["暂停/外部等待时长", format_duration(workflow_run.get("paused_seconds"))],
        ["实际活动时长", format_duration(workflow_run.get("active_duration_seconds"))],
        ["Token 统计状态", measurement_labels.get(measurement, measurement or "不可用")],
        ["输入 Token", measured("input_tokens")],
        ["输出 Token", measured("output_tokens")],
        ["缓存输入 Token（已包含在输入中）", measured("cached_input_tokens")],
        ["推理 Token（已包含在输出中）", measured("reasoning_tokens")],
        ["已测总 Token", measured("measured_total_tokens")],
        ["精确总 Token", str(exact_total) if exact_total is not None else "不可用"],
        ["未完整采集阶段", "；".join(incomplete) or "无"],
    ]
    stage_labels = {
        "requirements": "需求拆解",
        "design": "用例设计",
        "review": "用例评审",
        "plan": "执行计划",
        "browser_execution": "浏览器执行",
        "backfill": "结果回填",
        "report": "报告编制",
    }
    stage_rows = [
        [
            text(stage.get("stage_id")),
            stage_labels.get(text(stage.get("name")), text(stage.get("name"))),
            text(stage.get("executor")),
            text(stage.get("status")),
            format_duration(stage.get("duration_seconds")),
            text(stage.get("token_coverage")),
        ]
        for stage in workflow_run.get("stages") or []
        if isinstance(stage, dict)
    ]
    note = (
        "Token 来自模型服务或平台返回的精确 usage；缓存输入和推理 Token 只作构成披露，不重复加入总数。"
        if measurement in {"exact", "partial"}
        else "当前运行未取得可核验的模型 usage；“不可用”不等于 0，也不使用估算值冒充实际消耗。"
    )
    return rows, stage_rows, note

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a formal report only after all delivery gates pass.")
    parser.add_argument("--final-cases", required=True)
    parser.add_argument("--case-gate", required=True)
    parser.add_argument("--execution-yaml", required=True)
    parser.add_argument("--execution-gate", required=True)
    parser.add_argument("--delivery-gate", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--requirement-version", default="")
    parser.add_argument("--environment", default="")
    parser.add_argument("--batch-id", default="")
    parser.add_argument("--allure-report-path", default="")
    parser.add_argument("--run-metrics", default="")
    parser.add_argument("--workflow-run-id", default="")
    parser.add_argument("--backfill-xlsx-path", required=True)
    parser.add_argument("--original-backfill-xlsx-path", default="")
    parser.add_argument("--output", required=True, help="Machine-auditable Markdown report path (.md)")
    parser.add_argument("--output-docx", required=True, help="Formal Word report path (.docx)")
    args = parser.parse_args()

    final_case_path = Path(args.final_cases).resolve()
    execution_path = Path(args.execution_yaml).resolve()
    case_gate_path = Path(args.case_gate).resolve()
    execution_gate_path = Path(args.execution_gate).resolve()
    delivery_gate_path = Path(args.delivery_gate).resolve()

    require_gate_role(case_gate_path, "case_design")
    require_gate_role(execution_gate_path, "execution_delivery", "execution")
    require_gate_role(delivery_gate_path, "execution_delivery", "delivery")
    case_gate = verify_passed_gate(case_gate_path, final_case_path, "final_cases")
    verify_passed_gate(execution_gate_path, execution_path, "execution_results")
    verify_passed_gate(delivery_gate_path, execution_path, "execution_results")
    execution_gate = load_json(execution_gate_path)
    plan_gate_record = (execution_gate.get("inputs") or {}).get("execution_plan_gate") or {}
    plan_gate_name = Path(text(plan_gate_record.get("path"))).name or "<missing>"

    case_document = load_yaml(final_case_path)
    cases = list_from(case_document, ("test_cases",), "test_cases")
    results = list_from(load_yaml(execution_path), ("execution_results",), "execution_results")
    results_by_id = {text(item.get("case_id")): item for item in results}
    case_ids = [text(item.get("case_id")) for item in cases]
    if set(results_by_id) != set(case_ids) or len(results) != len(cases):
        raise SystemExit("Execution result IDs must exactly match final cases")

    counts: Counter[str] = Counter()
    defect_rows: list[list[str]] = []
    unstable_rows: list[list[str]] = []
    for case in cases:
        case_id = text(case.get("case_id"))
        result = results_by_id[case_id]
        status = normalize_execution_status(result.get("status"))
        counts[status] += 1
        title = text(case.get("case_title") or case.get("title"))
        actual = text(result.get("actual_result"))
        if text(result.get("stability")).lower() == "unstable":
            # 不稳定性表只给出可复核索引，不把整段 DOM/页面全文复制进
            # 报告；逐用例的人类可读实际结果、失败/阻塞原因统一以回填
            # 版为准。
            unstable_rows.append([case_id, text(case.get("priority")), " / ".join(text(item.get("status")) for item in result.get("attempts") or []), "详见执行回填版的实际执行结果、失败原因和阻塞说明"])
        if status == "failed":
            conclusion = explain_result(result, text(case.get("formal_expected_result")))[0]
            defect = result.get("defect") or {}
            defect_rows.append(
                [
                    f"BUG-{len(defect_rows) + 1:03d}",
                    case_id,
                    f"{case_id} {title}",
                    severity_suggestion(case, result),
                    text(defect.get("confirmation_status")) or "待确认",
                    text(defect.get("owner")) or "待分配",
                    text(defect.get("defect_status")) or "待确认",
                    text(defect.get("regression_status")) or "待回归",
                    conclusion,
                ]
            )

    total = len(cases)
    executed = counts["passed"] + counts["failed"] + counts["blocked"]
    execution_doc = load_yaml(execution_path)
    execution_meta = execution_doc.get("execution_meta") or {}
    resource_accounting_version = text(execution_meta.get("resource_accounting_version"))
    workflow_run = None
    metrics_path = Path(args.run_metrics).resolve() if args.run_metrics else None
    if resource_accounting_version:
        if resource_accounting_version != "1.0":
            raise SystemExit("Unsupported execution resource_accounting_version")
        if metrics_path is None:
            raise SystemExit("Resource accounting 1.0 requires --run-metrics")
    if metrics_path is not None:
        verify_passed_gate(delivery_gate_path, metrics_path, "workflow_run_metrics")
        reference = execution_meta.get("workflow_run_metrics") or {}
        run_id = args.workflow_run_id or text(reference.get("run_id")) or None
        metric_errors = validate_metrics_document(metrics_path, run_id)
        if metric_errors:
            raise SystemExit("Invalid workflow run metrics: " + "; ".join(metric_errors))
        workflow_run = current_run(metrics_path, run_id)
        if workflow_run.get("status") == "running":
            raise SystemExit("Formal report requires a finalized workflow run")
        if resource_accounting_version and text(reference.get("run_id")) != text(workflow_run.get("run_id")):
            raise SystemExit("Execution results and workflow metrics use different run IDs")
    priority_gate = execution_meta.get("priority_gate") or {}
    special_disposition_rows: list[str] = []
    for case in cases:
        case_id = text(case.get("case_id"))
        case_title = text(case.get("formal_case_title") or case.get("case_title"))
        if not any(keyword in case_title for keyword in ("大数据", "批量", "千条", "万条")):
            continue
        result = results_by_id.get(case_id) or {}
        status = normalize_execution_status(result.get("status"))
        reason = text(result.get("blocker_reason") or result.get("actual_result"))
        special_disposition_rows.append(f"- {case_id}（{status}）：{reason or '详见执行回填版。'}")
    priority_gate_triggered = priority_gate.get("triggered") is True
    metrics = execution_metrics(results)
    telemetry = metrics.get("telemetry") or {}
    budget = execution_meta.get("resource_budget") or {}
    accounting_rows, stage_rows, accounting_note = resource_accounting_rows(workflow_run)
    source_meta = case_document.get("case_source") or {}
    requirement_source = text(source_meta.get("requirement_source"))
    if requirement_source == "existing_xlsx_baseline":
        source_note = "测试依据为用户提供的 Excel 用例基线；需求完整性未评估，结果不代表完整 PRD 覆盖。"
    elif requirement_source == "prd":
        source_note = "测试依据为本次提供的 PRD；需求处理范围以设计门禁绑定的需求和测试点为准。"
    else:
        source_note = "最终用例未显式声明需求来源；请结合设计门禁绑定的输入核对测试依据和覆盖范围。"
    execution_mode = text(execution_meta.get("execution_mode")) or "未声明"
    overall = "全部 P0 最终均失败，已停止 P1/P2，不建议验收或发布" if priority_gate_triggered else (
        "存在失败项，需修复后回归" if counts["failed"] else (
        "存在阻塞或未执行项，结论受限" if counts["blocked"] or counts["not_run"] else "本轮执行通过"
        )
    )
    priority_gate_status = "已触发" if priority_gate_triggered else "未触发"
    judged = counts["passed"] + counts["failed"]
    completed_priorities = ["P0"] if priority_gate_triggered else [
        priority
        for priority in ("P0", "P1", "P2")
        if any(text(case.get("priority")).upper() == priority for case in cases)
        and all(
            normalize_execution_status(results_by_id[text(case.get("case_id"))].get("status")) != "not_run"
            for case in cases
            if text(case.get("priority")).upper() == priority
        )
    ]
    skipped_priorities = [text(value) for value in priority_gate.get("skipped_priorities") or []]
    not_run_reasons = Counter(
        text(result.get("not_run_reason") or result.get("blocker_reason"))
        for result in results
        if normalize_execution_status(result.get("status")) == "not_run"
    )
    not_run_reason_text = "；".join(
        f"{ {'manual_execution_required': '交给人工执行', 'p0_all_failed_stop_gate': '核心用例全部失败后停止', 'script_status_missing': '缺少执行记录'}.get(reason, reason or '未说明')}:{count}条" for reason, count in sorted(not_run_reasons.items())
    ) or "无"
    lines = [
        "# 正式测试报告",
        "",
        "## 1. 基本信息",
        "",
        f"- 项目名称：{args.project_name}",
        f"- 测试批次：{args.batch_id or datetime.now().strftime('%Y%m%d_%H%M%S')}",
        f"- 测试日期：{datetime.now().strftime('%Y-%m-%d')}",
        f"- 需求版本：{args.requirement_version}",
        f"- 执行环境：{args.environment}",
        f"- 执行模式：{execution_meta.get('execution_mode', '')}",
        f"- 用例范围：{text(source_meta.get('mode')) or '未声明'}",
        f"- 最终用例源：{final_case_path.name}",
        f"- 最终用例 SHA-256：{sha256_file(final_case_path)}",
        "",
        "## 2. 测试结论",
        "",
        f"- 用例总数：{total}",
        f"- 已实际执行：{executed}",
        f"- 通过：{counts['passed']}",
        f"- 失败：{counts['failed']}",
        f"- 阻塞：{counts['blocked']}",
        f"- 未执行：{counts['not_run']}",
        f"- 其中交给人工执行：{not_run_reasons['manual_execution_required']}",
        f"- 含阻塞通过率：{metrics['final_pass_rate_including_blocked']:.2f}%",
        f"- 已判定项通过率：{metrics['final_pass_rate_judged_only']:.2f}%",
        f"- 总体结论：{overall}",
        "",
        "### 2.1 质量门禁说明与状态",
        "",
        "> 门禁是测试流程阶段之间的自动质量校验点。`pass` 表示测试产物完整、版本一致、结果与证据符合规则，并允许进入下一阶段；不表示产品功能全部通过。产品质量结论必须以本报告的通过、失败、阻塞和未执行统计为准。",
        "",
        *md_table(
            ["门禁", "检查重点", "pass 的含义", "状态"],
            [
                ["用例设计门禁", "需求覆盖、测试点与用例映射、优先级、单断言和语义契约", "最终用例源可用于模板和计划", f"pass（{case_gate_path.name}）"],
                ["执行计划门禁", "计划与最终用例的版本、通道、核心步骤和观察项一致", "可以启动正式执行", f"pass（{plan_gate_name}，由执行结果门禁绑定）"],
                ["执行结果门禁", "重试、轨迹、观察值、证据、判定和汇总一致", "可以生成执行回填版", f"pass（{execution_gate_path.name}）"],
                ["最终交付门禁", "回填行数、ID、状态、实际结果、文件结构和查看器可读性", "交付物完整，可以生成正式报告", f"pass（{delivery_gate_path.name}）"],
            ],
        ),
        "",
        "### 2.2 P0 优先级停止门禁",
        "",
        f"- 门禁状态：{priority_gate_status}",
        f"- 评估时点：全部 P0 完成双跑/必要第三次重试及最终裁决后",
        f"- P0 总数/已完成/最终失败/最终阻塞：{priority_gate.get('p0_total', 0)} / {priority_gate.get('p0_completed', 0)} / {priority_gate.get('p0_failed', 0)} / {priority_gate.get('p0_blocked', 0)}",
        f"- 已完成优先级：{', '.join(completed_priorities) or '无'}",
        f"- 门禁停止而未启动的优先级：{', '.join(skipped_priorities) or '无'}",
        f"- 未执行数量及原因：{counts['not_run']}；{not_run_reason_text}",
        f"- 发布建议：{'不建议验收或发布，先修复全部 P0 失败并重新从 P0 开始执行' if priority_gate_triggered else '按总体结论、缺陷和阻塞情况决定'}",
        "",
        "> 只有全部 P0 的最终状态均为 `failed` 才触发停止门禁。只要任意 P0 最终为 `passed` 或 `blocked`，就继续执行 P1/P2。门禁触发后，P1/P2 的 `not_run` 表示尚未启动，不代表产品通过，也不属于环境阻塞。",
        "",
        "## 3. 测试范围矩阵",
        "",
        *md_table(["测试轨道", "状态", "覆盖说明", "原因", "产物"], scope_rows(execution_meta)),
        "",
        "> `executed` 表示该轨道已实际执行；`blocked` 表示计划执行但环境或能力受阻；人工用例会明确移交并保留为 not_run。API 仅用于造数或辅助观察时，不计为独立 API 测试。",
        "",
        "## 4. 执行稳定性与资源",
        "",
        *md_table(
            ["指标", "结果"],
            [
                ["首遍通过率", f"{metrics['first_passed']}/{metrics['executed_cases']} = {metrics['first_pass_rate']:.2f}%"],
                ["最终通过率（含阻塞）", f"{metrics['final_pass_rate_including_blocked']:.2f}%"],
                ["最终通过率（已判定项）", f"{metrics['final_pass_rate_judged_only']:.2f}%"],
                ["总 attempts", metrics["total_attempts"]],
                ["执行第三遍用例", metrics["third_attempt_cases"]],
                ["不稳定用例", metrics["unstable_cases"]],
                ["P2 单跑豁免用例", metrics["single_run_exempt_cases"]],
                ["单跑异常升级完整重试", metrics["full_retry_upgrades"]],
                ["环境中断次数", telemetry.get("environment_interruptions", 0)],
                ["登录态重采次数", telemetry.get("login_recaptures", 0)],
                ["前置构造失败次数", telemetry.get("precondition_failures", 0)],
                ["工具定位失败次数", telemetry.get("locator_failures", 0)],
                ["平均每条执行时间", f"{round(float(budget.get('actual_minutes', 0)) / executed, 2) if executed else 0} 分钟"],
                ["预计/实际 attempts", f"{budget.get('estimated_attempts', 0)} / {budget.get('actual_attempts', 0)}"],
                ["预计/实际耗时", f"{budget.get('estimated_minutes', 0)} / {budget.get('actual_minutes', 0)} 分钟"],
            ],
        ),
        "",
        "### 工作流时长与 Token 核算",
        "",
        *md_table(["指标", "结果"], accounting_rows),
        "",
        accounting_note,
        "",
        *md_table(["阶段 ID", "阶段", "执行类型", "状态", "耗时", "Token 覆盖"], stage_rows),
        "",
        "### 4.1 不稳定性风险",
        "",
        *md_table(["用例ID", "优先级", "尝试序列", "最终观察"], unstable_rows),
        "",
        "### 4.2 本轮耗时与执行策略说明",
        "",
        f"- 执行模式：{execution_mode}。执行路由、优先级顺序和重试资格以已通过门禁的计划与结果为准。",
        f"- 实际记录 {metrics['total_attempts']} 次 attempt，其中 {metrics['third_attempt_cases']} 条用例进入第三次裁决；人工执行的用例不计入自动 attempts。",
        "- 已执行的动作、前置数据和断言通道以逐用例 execution_trace、observations 和 evidence 为准。",
        f"- attempt 记录时长合计：{budget.get('actual_minutes', '未记录')} 分钟；工作流总时长、暂停和阶段耗时见资源账本。",
        "",
        "### 4.3 大数据量用例处置",
        "",
        *(special_disposition_rows or ["- 本轮没有识别到需要单独处置的大数据量用例。"]),
        "",
        f"逐用例的执行状态、实际结果、产出数据和证据索引以 `{Path(args.backfill_xlsx_path).name}` 为准。本报告不重复列出通过、失败或阻塞用例摘要，只保留总体结论、缺陷和风险。",
        "",
        "## 5. 缺陷清单",
        "",
        *md_table(["缺陷ID", "来源用例", "缺陷标题", "严重级别建议", "确认状态", "责任人", "缺陷状态", "回归状态", "结论"], defect_rows),
        "",
        "## 6. 风险与差异说明",
        "",
        f"- {source_note}",
        *(["- 本次明确限定 L2 场景范围，未启用完整生成质量契约，不代表 L2 + L3 全量覆盖。"] if (case_gate.get("quality_contract") or {}).get("waived") else
          ["- 用例设计使用存量兼容审计；设计门禁通过不代表 V3 生成质量契约合规。"] if not (case_gate.get("quality_contract") or {}).get("enforced") else []),
        f"- 不稳定用例：{metrics['unstable_cases']} 条，必须进入专项回归队列，不得仅凭最终 passed 隐藏。",
        f"- P2 单跑豁免：{metrics['single_run_exempt_cases']} 条；仅在风险回归且基线资格通过门禁时有效。",
        "- blocked 前必须完成能力探测；阻塞原因和已尝试通道以回填用例及执行结果为准。",
        f"- P0 优先级停止门禁：{priority_gate_status}；{'未执行项及其原因见范围统计和回填文件。' if not priority_gate_triggered else 'P1/P2 未启动只表示质量停止，不得解释为通过或环境阻塞。'}",
        "",
        "## 7. 关键产出文件",
        "",
        f"- 用例回填文件：{args.backfill_xlsx_path}",
        *(
            [f"- 原始用例聚合回填文件：{args.original_backfill_xlsx_path}"]
            if args.original_backfill_xlsx_path
            else []
        ),
        f"- 执行结果：{execution_path}",
        f"- Allure 报告：{args.allure_report_path or '未产出'}",
        f"- 工作流运行账本：{str(metrics_path) if metrics_path is not None else '历史产物未启用'}",
    ]
    output_path = Path(args.output).resolve()
    docx_path = Path(args.output_docx).resolve()
    if output_path.suffix.lower() != ".md":
        raise SystemExit("--output must be the machine-auditable Markdown report (.md)")
    if docx_path.suffix.lower() != ".docx":
        raise SystemExit("--output-docx must be the formal Word report (.docx)")
    if docx_path == output_path:
        raise SystemExit("--output-docx must differ from the Markdown --output path")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_docx_from_markdown(lines, docx_path, f"{args.project_name} 正式测试报告")
    generated = [str(output_path), str(docx_path)]
    print(f"Generated gated formal test report: {', '.join(generated)}")


if __name__ == "__main__":
    main()
