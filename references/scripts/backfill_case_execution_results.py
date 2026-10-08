from __future__ import annotations

import argparse
import re
import shutil
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from workflow_gate_common import case_rows_from_yaml, load_yaml, text, verify_passed_gate
from execution_readiness import CASE_HEADERS
from result_explanation import explain_result
from generate_case_xlsx import add_readiness_styles, column_name, replace_zip_entry


# These fields are execution/audit plumbing rather than reviewer-facing business data.
_INTERNAL_PRODUCED_KEYS = {
    "kind",
    "outcome_observed",
    "rejection_observed",
    "mutation_committed",
    "changed",
    "comparison_verified",
    "fixture_contract",
    "fixture_validation",
}


def _strip_machine_prefix(value: str) -> str:
    value = text(value)
    value = re.sub(r"^(?:第三次裁决执行异常|前置执行异常|执行异常)\s*[：:]\s*", "", value)
    value = re.sub(r"；?未执行核心(?:业务)?断言。?$", "", value)
    return value.strip(" ；")


def _short_dom(value: str, limit: int = 260) -> str:
    """把页面全文压缩为审阅者能读懂的业务事实，绝不把 DOM/选择器日志带入回填。"""
    value = text(value)
    if not value:
        return "页面未返回可核对文本。"
    # 去除导航、账号和重复空白；保留与批量下单断言相关的锚点。
    value = re.sub(r"您好，欢迎来到.*?(?=批量下单|导入订单文件)", "", value)
    value = re.sub(r"去买家中心.*?(?=批量下单|导入订单文件)", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    anchors = [
        "导入订单文件", "下载导入模板", "选择批量订单文件", "本次导入", "外部订单号",
        "查看异常订单", "重新上传", "当前商品", "相同规格", "相同的商品", "选择收货地址",
        "请设置收货地址", "确认订单", "提交订单", "去结算", "已选订单", "商品数量",
        "规格ID", "商品名称", "文件大小", "2MB", "超过", "未找到", "异常",
    ]
    found: list[str] = []
    for anchor in anchors:
        if anchor in value and anchor not in found:
            found.append(anchor)
    # 业务数据锚点（订单号、商品名、地址提示、金额）适度保留。
    for pattern in (r"外部订单号[：:]?[^ ]{1,70}", r"测试商品[_\w-]*", r"数量[：:]?\d+", r"￥\s*[\d.]+"):
        for match in re.findall(pattern, value):
            if match not in found:
                found.append(match)
    result = "；".join(found)
    if not result:
        result = value[:limit]
    return result[:limit]


def _stage_and_resolution(reason: str) -> tuple[str, str]:
    reason = _strip_machine_prefix(reason)
    if "input[type=file]" in reason or "文件上传控件" in reason or "set_input_files" in reason:
        return "文件上传控件定位", "等待上传控件挂载或修复上传控件定位后重新执行"
    if "radio" in reason or "cannot be filled" in reason:
        return "表单控件操作", "修正控件定位和操作方式后重新执行"
    if "缺少" in reason or "前置数据" in reason:
        return "测试数据或配置准备", "按本用例准备清单补齐数据、权限或配置并验收后重新执行"
    if any(marker in reason for marker in ("超过100", "200单", "异步", "2000行", "1000行", "大数据量")):
        return "大数据量准备与加载", "准备可审计的测试数据并确认加载完成后重新执行"
    return "执行前置或页面操作", "补齐上述条件并重新执行"


def _transient_feedback_result(raw_actual: str) -> str | None:
    """Render delayed, self-clearing inline validation in reviewer language.

    The batch-import page reports malformed headers through an Element-UI
    one-line message rather than a modal.  The execution YAML deliberately
    keeps the raw observation, while the normal DOM compactor would otherwise
    reduce it to the unhelpful single word ``异常``.  Preserve the message,
    presentation type, timing and no-write outcome in the human backfill.
    """
    raw_actual = text(raw_actual)
    if "内联错误提示" not in raw_actual:
        return None
    match = re.search(
        r"内联错误提示（非弹框，约\s*([^）]+)\s*后出现，随后自动消失）：([^；]+)",
        raw_actual,
    )
    if match:
        timing = match.group(1).strip()
        message = match.group(2).strip()
    else:
        match = re.search(r"内联错误提示（非弹框，([^）]+)）：([^；]+)", raw_actual)
        timing = match.group(1).strip() if match else "延迟"
        message = match.group(2).strip() if match else "页面错误提示"
    return (
        f"上传文件后约{timing}，页面出现一行内联错误提示“{message}”（不是弹框）；"
        "提示随后自动消失。文件未进入批量下单购物车，未发生持久化写入。"
    )


def _business_feedback(raw_actual: str) -> str:
    """Extract a concrete page message instead of returning a generic DOM summary."""
    value = text(raw_actual)
    known_messages = (
        "读取导入Excel内容异常",
        "商品数量不能为空",
        "商品名称不能为空",
        "规格ID不能为空",
        "规格ID字段不存在",
    )
    messages = [message for message in known_messages if message in value]
    if messages:
        return "页面显示“" + "；".join(dict.fromkeys(messages)) + "”"
    if "实际类别=file_parse_error" in value:
        return "页面反馈为文件解析异常"
    if "实际类别=missing_header_validation" in value:
        return "页面反馈为缺少必填表头"
    return ""


def format_human_result(item: dict, row: dict[str, str]) -> tuple[str, str, str]:
    """返回（人工可读实际结果、失败原因、阻塞原因）。"""
    status = text(item.get("status")).lower()
    summary = item.get("result_summary") or {}
    if not isinstance(summary, dict):
        summary = {}
    raw_actual = text(item.get("actual_result"))
    reason = text(item.get("blocker_reason") or item.get("not_run_reason"))
    if status == "not_run":
        human_reason, next_action = explain_result(item)
        return human_reason, "", f"{human_reason} 后续处理：{next_action}"
    if status == "blocked":
        clean_reason = _strip_machine_prefix(reason or raw_actual)
        stage, _ = _stage_and_resolution(clean_reason)
        attempts = item.get("attempts") or []
        progress = "已有执行尝试记录，具体完成的动作以执行轨迹和截图为准" if attempts else "尚无核心业务操作的执行记录"
        actual = f"未执行到核心断言。执行停在“{stage}”阶段；{progress}，未完成用例要求的核心操作，因此不能据此判定产品功能失败。"
        human_reason, next_action = explain_result(item)
        blocker = f"{human_reason} 解除条件：{next_action}"
        return actual, "", blocker
    compact = _short_dom(text(summary.get("actual") or raw_actual))
    if status == "failed":
        failed_step = text(item.get("failed_step_no")) or "未记录"
        feedback = _business_feedback(text(summary.get("actual") or raw_actual))
        if feedback:
            actual = f"已执行至第{failed_step}步；{feedback}。未观察到预期结果“{text(row.get('预期结果'))}”。"
        else:
            actual = f"已执行至第{failed_step}步；页面实际观察到：{compact}。未观察到预期结果“{text(row.get('预期结果'))}”。"
        failure, _ = explain_result(item, text(row.get("预期结果")))
        return actual, failure, ""
    # passed：只写业务事实，不写“UI 断言已采集”等机器状态词。
    transient_actual = _transient_feedback_result(raw_actual)
    if transient_actual:
        return transient_actual, "", ""
    produced = item.get("produced_data") or {}
    details: list[str] = []
    if isinstance(produced, dict):
        for key, value in produced.items():
            if key in _INTERNAL_PRODUCED_KEYS:
                continue
            if text(value):
                if key in {"before_state", "after_state"}:
                    details.append(f"{'操作前' if key == 'before_state' else '操作后'}页面={_short_dom(text(value), 140)}")
                else:
                    details.append(f"{key}={text(value)}")
    selected = item.get("selected_attempt") or item.get("backfilled_from")
    attempts = item.get("attempts") or []
    if selected and isinstance(attempts, list) and 0 < int(selected) <= len(attempts):
        setup = attempts[int(selected) - 1].get("data_setup") or {}
        generated = setup.get("produced") if isinstance(setup, dict) else {}
        if isinstance(generated, dict):
            for key, value in generated.items():
                if text(value) and key not in {"kind"}:
                    details.append(f"{key}={text(value)}")
    actual = f"已完成核心操作；页面观察到：{compact}。"
    if details:
        actual += " 产生数据：" + "；".join(details) + "。"
    return actual, "", ""


NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
ET.register_namespace("", NS["x"])


def source_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if path.suffix.lower() not in {".yaml", ".yml"}:
        raise ValueError("The canonical final-case source must be YAML")
    headers = CASE_HEADERS
    rows = case_rows_from_yaml(path)
    return headers, rows


def load_execution_results(path: Path, source_rows_by_id: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    document = load_yaml(path)
    results: dict[str, dict[str, str]] = {}
    for item in document.get("execution_results", []) or []:
        case_id = text(item.get("case_id"))
        if not case_id:
            continue
        evidence = item.get("evidence") or {}
        produced_data = item.get("produced_data") or {}
        produced_parts: list[str] = []
        if isinstance(produced_data, dict):
            for key, value in produced_data.items():
                if key in _INTERNAL_PRODUCED_KEYS:
                    continue
                if text(value):
                    if key in {"before_state", "after_state"}:
                        produced_parts.append(f"{'操作前' if key == 'before_state' else '操作后'}页面:{_short_dom(text(value), 140)}")
                    else:
                        produced_parts.append(f"{key}:{text(value)}")
        selected = item.get("selected_attempt") or item.get("backfilled_from")
        attempts = item.get("attempts") or []
        if selected and isinstance(attempts, list) and 0 < int(selected) <= len(attempts):
            setup = attempts[int(selected) - 1].get("data_setup") or {}
            generated = setup.get("produced") if isinstance(setup, dict) else {}
            if isinstance(generated, dict):
                for key, value in generated.items():
                    if text(value) and key not in _INTERNAL_PRODUCED_KEYS:
                        produced_parts.append(f"{key}:{text(value)}")
        human_actual, failure_reason, blocker_reason = format_human_result(item, source_rows_by_id.get(case_id, {}))
        human_reason, next_action = explain_result(item, text(source_rows_by_id.get(case_id, {}).get("预期结果")))
        results[case_id] = {
            "执行状态": text(item.get("status")),
            "稳定性": text(item.get("stability")),
            "执行结果": human_actual,
            "失败原因": failure_reason,
            "失败步骤": text(item.get("failed_step_no")),
            "产出数据": "；".join(produced_parts),
            "证据": "；".join(
                [
                    *(f"截图:{value}" for value in evidence.get("screenshots", []) or []),
                    *(f"日志:{value}" for value in evidence.get("logs", []) or []),
                    *(f"URL:{value}" for value in evidence.get("urls", []) or []),
                ]
            ),
            "阻塞类型": text(item.get("blocker_type")),
            "阻塞/未执行原因": blocker_reason,
            "原因说明（给测试人员）": human_reason,
            "建议下一步": next_action,
            "技术原因（给Agent）": text(item.get("technical_reason") or item.get("failure_reason") or item.get("blocker_reason") or item.get("defect_summary") or item.get("not_run_reason") or item.get("actual_result")),
        }
    return results


def inline_cell(ref: str, style: str, value: str) -> ET.Element:
    cell = ET.Element(f"{{{NS['x']}}}c", r=ref, s=style, t="inlineStr")
    inline = ET.SubElement(cell, f"{{{NS['x']}}}is")
    text_node = ET.SubElement(inline, f"{{{NS['x']}}}t")
    if value.startswith(" ") or value.endswith(" ") or "\n" in value:
        text_node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text_node.text = value
    return cell


def first_sheet_path(zip_file: zipfile.ZipFile) -> str:
    workbook = ET.fromstring(zip_file.read("xl/workbook.xml"))
    sheet = workbook.find("x:sheets/x:sheet", NS)
    if sheet is None:
        raise ValueError("Workbook has no worksheet")
    relationship_id = sheet.get(f"{{{REL_NS}}}id")
    rels = ET.fromstring(zip_file.read("xl/_rels/workbook.xml.rels"))
    for relationship in rels.findall(f"{{{PKG_REL_NS}}}Relationship"):
        if relationship.get("Id") == relationship_id:
            target = str(relationship.get("Target") or "").replace("\\", "/").lstrip("/")
            return target if target.startswith("xl/") else f"xl/{target}"
    raise ValueError("Cannot resolve first worksheet")


def build_markdown(headers: list[str], rows: list[dict[str, str]], path: Path) -> None:
    lines = [
        "# 执行回填后的正式用例", "",
        "说明：本文件只接受已通过用例门禁和执行门禁的结果。每条用例按‘前置—步骤—预期—实际—结论’展开，供人工复审；机器原始日志仅保留在 execution_results YAML 和 runtime 目录。", "",
    ]
    for index, row in enumerate(rows, 1):
        status = row.get("执行状态", "")
        lines.extend([
            f"## {index}. {row.get('用例ID', '')}｜{row.get('用例名称', '')}", "",
            f"- 功能集合：{row.get('功能集合', '')}",
            f"- 优先级：{row.get('优先级', '')}",
            f"- 执行级别：{row.get('执行级别', '')}",
            f"- 人工介入说明：{row.get('人工介入说明', '')}",
            f"- 人工准备清单：{row.get('人工准备清单', '')}",
            f"- 前置条件：{row.get('前置条件', '')}",
            f"- 执行步骤：{row.get('执行步骤', '')}",
            f"- 预期结果：{row.get('预期结果', '')}",
            f"- 执行状态：{status}（稳定性：{row.get('稳定性', '')}）",
            f"- 实际执行结果：{row.get('执行结果', '')}",
        ])
        if row.get("原因说明（给测试人员）"):
            lines.append(f"- 原因说明（给测试人员）：{row['原因说明（给测试人员）']}")
        lines.append(f"- 建议下一步：{row.get('建议下一步', '')}")
        if row.get("技术原因（给Agent）"):
            lines.append(f"- 技术原因（给Agent）：{row['技术原因（给Agent）']}")
        if row.get("失败原因"):
            lines.append(f"- 失败原因：{row.get('失败原因')}")
        if row.get("失败步骤"):
            lines.append(f"- 失败步骤：第{row.get('失败步骤')}步")
        if row.get("阻塞/未执行原因"):
            lines.append(f"- 阻塞/未执行原因：{row.get('阻塞/未执行原因')}")
        if row.get("产出数据"):
            lines.append(f"- 产出数据：{row.get('产出数据')}")
        if row.get("证据"):
            lines.append(f"- 证据：{row.get('证据')}")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_xlsx(template_path: Path, headers: list[str], rows: list[dict[str, str]], output_path: Path) -> None:
    shutil.copyfile(template_path, output_path)
    readiness_styles = add_readiness_styles(output_path)
    with zipfile.ZipFile(output_path, "r") as zip_file:
        sheet_path = first_sheet_path(zip_file)
        sheet_root = ET.fromstring(zip_file.read(sheet_path))
    sheet_data = sheet_root.find("x:sheetData", NS)
    if sheet_data is None:
        raise ValueError("Template worksheet has no sheetData")
    for child in list(sheet_data):
        sheet_data.remove(child)
    columns = [column_name(index + 1) for index in range(len(headers))]
    header_row = ET.Element(f"{{{NS['x']}}}row", r="1", ht="25", customHeight="1", spans=f"1:{len(headers)}")
    for column, header in zip(columns, headers):
        header_row.append(inline_cell(f"{column}1", "3" if column == "A" else "4", header))
    sheet_data.append(header_row)
    for row_number, row in enumerate(rows, 2):
        row_element = ET.Element(f"{{{NS['x']}}}row", r=str(row_number), ht="34.5", customHeight="1", spans=f"1:{len(headers)}")
        for column, header in zip(columns, headers):
            value = row.get(header, "")
            style = readiness_styles.get(value, "6") if header == "执行级别" else ("5" if column == "A" else "6")
            row_element.append(inline_cell(f"{column}{row_number}", style, value))
        sheet_data.append(row_element)
    last_column = columns[-1]
    last_row = len(rows) + 1
    dimension = sheet_root.find("x:dimension", NS)
    if dimension is not None:
        dimension.set("ref", f"A1:{last_column}{last_row}")
    auto_filter = sheet_root.find("x:autoFilter", NS)
    if auto_filter is not None:
        auto_filter.set("ref", f"A1:{last_column}{last_row}")
    replace_zip_entry(output_path, sheet_path, ET.tostring(sheet_root, encoding="utf-8", xml_declaration=True))


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill execution results after case and execution gates pass.")
    parser.add_argument("--source", required=True, help="Gated 06_final_test_cases.yaml")
    parser.add_argument("--case-gate", required=True)
    parser.add_argument("--execution-yaml", required=True)
    parser.add_argument("--execution-gate", required=True)
    parser.add_argument("--template-xlsx", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--output-xlsx", required=True)
    args = parser.parse_args()

    source_path = Path(args.source).resolve()
    execution_path = Path(args.execution_yaml).resolve()
    verify_passed_gate(Path(args.case_gate).resolve(), source_path, "final_cases")
    verify_passed_gate(Path(args.execution_gate).resolve(), execution_path, "execution_results")
    headers, rows = source_rows(source_path)
    source_rows_by_id = {text(row.get("用例ID")): row for row in rows}
    execution_results = load_execution_results(execution_path, source_rows_by_id)
    execution_headers = ["执行状态", "稳定性", "执行结果", "原因说明（给测试人员）", "建议下一步", "失败原因", "失败步骤", "产出数据", "证据", "阻塞类型", "阻塞/未执行原因", "技术原因（给Agent）"]
    headers = headers + [header for header in execution_headers if header not in headers]
    merged_rows: list[dict[str, str]] = []
    missing: list[str] = []
    for row in rows:
        case_id = text(row.get("用例ID"))
        result = execution_results.get(case_id)
        if result is None:
            missing.append(case_id)
            continue
        merged_rows.append({**row, **result})
    if missing or len(merged_rows) != len(rows):
        raise SystemExit(f"Execution results do not cover every final case; missing={missing}")
    output_md_path = Path(args.output_md).resolve()
    output_xlsx_path = Path(args.output_xlsx).resolve()
    output_md_path.parent.mkdir(parents=True, exist_ok=True)
    output_xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    build_markdown(headers, merged_rows, output_md_path)
    build_xlsx(Path(args.template_xlsx).resolve(), headers, merged_rows, output_xlsx_path)
    print(f"Backfilled gated testcase files: {len(merged_rows)} cases")


if __name__ == "__main__":
    main()
