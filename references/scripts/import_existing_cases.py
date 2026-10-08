from __future__ import annotations

import argparse
import hashlib
import json
import logging
import posixpath
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import yaml


WORKSHEET_NAMESPACE = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOCUMENT_RELATIONSHIP_NAMESPACE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_RELATIONSHIP_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"
MAX_HEADER_SCAN_ROWS = 20
GENERATED_CASE_ID_PREFIX = "LEGACY"

FIELD_LABELS = {
    "case_id": "用例ID",
    "feature_group": "功能集合",
    "case_title": "用例名称",
    "priority": "优先级",
    "preconditions": "前置条件",
    "test_data": "测试数据",
    "steps": "执行步骤",
    "expected_result": "预期结果",
}

HEADER_ALIASES = {
    "case_id": ["用例ID", "用例编号", "编号", "ID", "Case ID", "Test Case ID"],
    "feature_group": ["功能集合", "所属模块", "功能模块", "模块", "页面", "功能点"],
    "case_title": ["用例名称", "测试用例", "用例标题", "标题", "测试点"],
    "priority": ["优先级", "级别", "Priority"],
    "preconditions": ["前置条件", "前提条件", "前置", "Precondition", "Preconditions"],
    "test_data": ["测试数据", "用例数据", "输入数据", "Data", "Test Data"],
    "steps": ["执行步骤", "测试步骤", "操作步骤", "步骤", "Steps", "Test Steps"],
    "expected_result": ["预期结果", "期望结果", "预期", "Expected Result", "Expected"],
}

REQUIRED_FIELDS = {"case_title", "steps", "expected_result"}

PRIORITY_ALIASES = {
    "P0": "P0",
    "0": "P0",
    "高": "P0",
    "高优先级": "P0",
    "HIGH": "P0",
    "P1": "P1",
    "1": "P1",
    "中": "P1",
    "中优先级": "P1",
    "MEDIUM": "P1",
    "P2": "P2",
    "2": "P2",
    "低": "P2",
    "低优先级": "P2",
    "LOW": "P2",
}


def 计算文件哈希(path: Path) -> str:
    """计算文件 SHA-256。

    参数：path 为待计算的文件路径。
    返回：文件内容对应的十六进制 SHA-256。
    异常：文件不存在或不可读时透传底层文件异常。
    使用场景：绑定导入结果与原始 Excel，防止后续替换输入。
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def 规范文本(value: Any) -> str:
    """把单元格值转换为稳定文本。

    参数：value 为任意单元格值。
    返回：去除首尾空白并统一换行后的文本。
    异常：不主动抛出异常。
    使用场景：表头识别和用例字段导入。
    """
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def 规范表头(value: Any) -> str:
    """生成用于别名匹配的表头键。

    参数：value 为原始表头值。
    返回：移除空白和常见分隔符后的大写文本。
    异常：不主动抛出异常。
    使用场景：兼容不同团队的 Excel 列名称。
    """
    text = 规范文本(value).upper()
    return re.sub(r"[\s_\-:：/\\（）()]+", "", text)


def 列索引(cell_reference: str) -> int:
    """把 Excel 单元格引用转换为从零开始的列索引。

    参数：cell_reference 为 A1、BC12 等引用。
    返回：从零开始的列索引。
    异常：引用不含列名时抛出 ValueError。
    使用场景：解析稀疏存储的工作表单元格。
    """
    match = re.match(r"([A-Za-z]+)", cell_reference)
    if not match:
        raise ValueError(f"无法识别单元格引用：{cell_reference}")
    result = 0
    for character in match.group(1).upper():
        result = result * 26 + ord(character) - ord("A") + 1
    return result - 1


def 列名称(index: int) -> str:
    """把从零开始的列索引转换为 Excel 列名。

    参数：index 为从零开始的非负列索引。
    返回：A、B、AA 等列名。
    异常：负数索引抛出 ValueError。
    使用场景：在导入报告中记录原始列位置。
    """
    if index < 0:
        raise ValueError("列索引不能为负数")
    result = ""
    current = index + 1
    while current:
        current, remainder = divmod(current - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def 读取共享字符串(archive: zipfile.ZipFile) -> list[str]:
    """读取 XLSX 共享字符串表。

    参数：archive 为已打开的 XLSX 压缩包。
    返回：按索引排列的共享字符串列表。
    异常：XML 损坏时透传解析异常；文件缺失时返回空列表。
    使用场景：解析使用 sharedStrings 保存的 Excel 文本。
    """
    path = "xl/sharedStrings.xml"
    if path not in archive.namelist():
        return []
    root = ET.fromstring(archive.read(path))
    return ["".join(node.text or "" for node in item.findall(f".//{{{WORKSHEET_NAMESPACE}}}t")) for item in root]


def 读取单元格(cell: ET.Element, shared_strings: list[str]) -> str:
    """读取单个 XLSX 单元格的显示值。

    参数：cell 为单元格 XML，shared_strings 为共享字符串列表。
    返回：单元格文本；公式单元格使用工作簿缓存值。
    异常：共享字符串索引非法时返回空文本，不中断整表导入。
    使用场景：导入人工维护的现有用例表。
    """
    cell_type = cell.get("t", "")
    if cell_type == "inlineStr":
        return 规范文本("".join(node.text or "" for node in cell.findall(f".//{{{WORKSHEET_NAMESPACE}}}t")))
    value_node = cell.find(f"{{{WORKSHEET_NAMESPACE}}}v")
    value = value_node.text if value_node is not None else ""
    if cell_type == "s":
        try:
            return 规范文本(shared_strings[int(value)])
        except (ValueError, IndexError):
            return ""
    if cell_type == "b":
        return "是" if value == "1" else "否"
    return 规范文本(value)


def 解析区域(reference: str) -> tuple[int, int, int, int]:
    """解析 Excel 区域引用。

    参数：reference 为 A2:B5 或单一单元格引用。
    返回：起始行、结束行、起始列、结束列，行号从一开始。
    异常：引用格式非法时抛出 ValueError。
    使用场景：把合并单元格的顶端值传播到其覆盖范围。
    """
    start, _, end = reference.partition(":")
    end = end or start
    start_row_match = re.search(r"(\d+)$", start)
    end_row_match = re.search(r"(\d+)$", end)
    if not start_row_match or not end_row_match:
        raise ValueError(f"无法识别单元格区域：{reference}")
    return (
        int(start_row_match.group(1)),
        int(end_row_match.group(1)),
        列索引(start),
        列索引(end),
    )


def 读取工作簿(path: Path) -> list[dict[str, Any]]:
    """读取 XLSX 中全部工作表及稀疏行数据。

    参数：path 为 .xlsx 文件路径。
    返回：工作表名称、内部路径和行数据列表。
    异常：非 XLSX、缺少核心 XML 或 XML 损坏时抛出可读异常。
    使用场景：为规范化模式提供不依赖 Excel/WPS 的确定性导入。
    """
    if path.suffix.lower() != ".xlsx":
        raise ValueError("现有用例导入目前只支持 .xlsx；请先把 .xls 另存为 .xlsx")
    if not path.is_file():
        raise FileNotFoundError(f"Excel 文件不存在：{path}")

    with zipfile.ZipFile(path) as archive:
        required = {"xl/workbook.xml", "xl/_rels/workbook.xml.rels"}
        missing = required.difference(archive.namelist())
        if missing:
            raise ValueError(f"不是有效的 XLSX，缺少：{', '.join(sorted(missing))}")

        shared_strings = 读取共享字符串(archive)
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target_by_id = {
            item.get("Id", ""): item.get("Target", "")
            for item in relationships.findall(f"{{{PACKAGE_RELATIONSHIP_NAMESPACE}}}Relationship")
        }
        sheets: list[dict[str, Any]] = []
        for sheet in workbook.findall(f".//{{{WORKSHEET_NAMESPACE}}}sheet"):
            relationship_id = sheet.get(f"{{{DOCUMENT_RELATIONSHIP_NAMESPACE}}}id", "")
            target = target_by_id.get(relationship_id, "")
            if not target:
                continue
            sheet_path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
            if sheet_path not in archive.namelist():
                raise ValueError(f"工作表文件缺失：{sheet_path}")
            root = ET.fromstring(archive.read(sheet_path))
            rows: dict[int, dict[int, str]] = {}
            for row_node in root.findall(f".//{{{WORKSHEET_NAMESPACE}}}sheetData/{{{WORKSHEET_NAMESPACE}}}row"):
                row_number = int(row_node.get("r", "0") or 0)
                values: dict[int, str] = {}
                for cell in row_node.findall(f"{{{WORKSHEET_NAMESPACE}}}c"):
                    reference = cell.get("r", "")
                    values[列索引(reference)] = 读取单元格(cell, shared_strings)
                rows[row_number] = values

            # Excel 常用合并单元格表达模块分组，导入时传播顶端值并保留来源关系。
            for merged in root.findall(f".//{{{WORKSHEET_NAMESPACE}}}mergeCell"):
                start_row, end_row, start_column, end_column = 解析区域(merged.get("ref", ""))
                merged_value = rows.get(start_row, {}).get(start_column, "")
                for row_number in range(start_row, end_row + 1):
                    row = rows.setdefault(row_number, {})
                    for column in range(start_column, end_column + 1):
                        if not row.get(column):
                            row[column] = merged_value

            sheets.append({"name": sheet.get("name", ""), "path": sheet_path, "rows": rows})
        return sheets


def 构建别名索引() -> dict[str, str]:
    """构建规范表头到内部字段的索引。

    参数：无。
    返回：表头规范值到内部字段名的字典。
    异常：同一别名映射多个字段时抛出 ValueError。
    使用场景：自动识别用户已有 Excel 的列结构。
    """
    result: dict[str, str] = {}
    for field, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            key = 规范表头(alias)
            if key in result and result[key] != field:
                raise ValueError(f"表头别名冲突：{alias}")
            result[key] = field
    return result


HEADER_INDEX = 构建别名索引()


def 识别表头(rows: dict[int, dict[int, str]], specified_row: int | None = None) -> tuple[int, dict[str, int]]:
    """识别最可能的表头行和字段列。

    参数：rows 为工作表稀疏行；specified_row 可强制指定表头行。
    返回：表头行号和内部字段到列索引的映射。
    异常：必要字段无法全部识别时抛出 ValueError。
    使用场景：兼容首行标题、第二行才是表头等常见模板。
    """
    candidate_rows = [specified_row] if specified_row else sorted(row for row in rows if row <= MAX_HEADER_SCAN_ROWS)
    best_row = 0
    best_mapping: dict[str, int] = {}
    for row_number in candidate_rows:
        mapping: dict[str, int] = {}
        for column, value in rows.get(row_number, {}).items():
            field = HEADER_INDEX.get(规范表头(value))
            if field and field not in mapping:
                mapping[field] = column
        if len(mapping) > len(best_mapping):
            best_row = row_number
            best_mapping = mapping
    missing = sorted(REQUIRED_FIELDS.difference(best_mapping))
    if missing:
        labels = "、".join(FIELD_LABELS[item] for item in missing)
        raise ValueError(f"无法识别必要列：{labels}；可调整表头或使用 --header-row 指定表头行")
    return best_row, best_mapping


def 选择工作表(
    sheets: list[dict[str, Any]],
    specified_sheet: str | None,
    specified_header_row: int | None,
) -> tuple[dict[str, Any], int, dict[str, int]]:
    """选择包含测试用例的工作表。

    参数：sheets 为工作簿内容；specified_sheet 和 specified_header_row 为可选覆盖。
    返回：选中的工作表、表头行和列映射。
    异常：指定工作表不存在或所有工作表都无法识别时抛出 ValueError。
    使用场景：多工作表 Excel 的自动或显式路由。
    """
    candidates = sheets
    if specified_sheet:
        candidates = [item for item in sheets if item["name"] == specified_sheet]
        if not candidates:
            names = "、".join(item["name"] for item in sheets)
            raise ValueError(f"找不到工作表“{specified_sheet}”；可选工作表：{names}")
    results: list[tuple[int, dict[str, Any], int, dict[str, int]]] = []
    errors: list[str] = []
    for sheet in candidates:
        try:
            header_row, mapping = 识别表头(sheet["rows"], specified_header_row)
            results.append((len(mapping), sheet, header_row, mapping))
        except ValueError as error:
            errors.append(f"{sheet['name']}：{error}")
    if not results:
        raise ValueError("没有找到可导入的用例工作表；" + "；".join(errors))
    _, sheet, header_row, mapping = max(results, key=lambda item: item[0])
    return sheet, header_row, mapping


def 规范优先级(value: str) -> tuple[str, str | None]:
    """把常见人工优先级映射为 P0/P1/P2。

    参数：value 为原始优先级文本。
    返回：规范优先级和可选问题代码。
    异常：不主动抛出异常。
    使用场景：减少不同团队高/中/低写法造成的机械修订。
    """
    original = 规范文本(value)
    if not original:
        return "", "missing_priority"
    normalized = PRIORITY_ALIASES.get(original.upper())
    if normalized:
        return normalized, None if normalized == original.upper() else "priority_mapped"
    return original, "unsupported_priority"


def 导入用例(
    source: Path,
    sheet_name: str | None = None,
    header_row: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """把已有 Excel 用例导入为规范化中间产物。

    参数：source 为 Excel；sheet_name 和 header_row 用于覆盖自动识别。
    返回：YAML 中间产物和 JSON 导入报告。
    异常：输入无效、表头不完整或没有有效用例时抛出 ValueError。
    使用场景：case_normalization 模式的确定性第一阶段。
    """
    logging.info("开始读取已有测试用例：%s", source)
    sheets = 读取工作簿(source)
    sheet, detected_header_row, mapping = 选择工作表(sheets, sheet_name, header_row)
    rows: dict[int, dict[int, str]] = sheet["rows"]
    header_values = rows.get(detected_header_row, {})
    seen_case_ids: set[str] = set()
    imported_cases: list[dict[str, Any]] = []
    issue_counts: dict[str, int] = {}

    for row_number in sorted(row for row in rows if row > detected_header_row):
        row = rows[row_number]
        source_values = {
            f"{列名称(column)}:{规范文本(header_values.get(column)) or '未命名列'}": 规范文本(value)
            for column, value in sorted(row.items())
            if 规范文本(value)
        }
        title = 规范文本(row.get(mapping["case_title"]))
        steps = 规范文本(row.get(mapping["steps"]))
        expected = 规范文本(row.get(mapping["expected_result"]))
        if not title and not steps and not expected:
            continue

        issues: list[dict[str, str]] = []
        source_case_id = 规范文本(row.get(mapping.get("case_id", -1)))
        suggested_case_id = source_case_id or f"{GENERATED_CASE_ID_PREFIX}-{row_number:04d}"
        if not source_case_id:
            issues.append({"code": "missing_case_id", "severity": "warning", "message": "原用例缺少 ID，已生成稳定建议 ID"})
        if suggested_case_id in seen_case_ids:
            suggested_case_id = f"{suggested_case_id}-R{row_number}"
            issues.append({"code": "duplicate_case_id", "severity": "warning", "message": "原用例 ID 重复，已生成不重复建议 ID"})
        seen_case_ids.add(suggested_case_id)

        priority_source = 规范文本(row.get(mapping.get("priority", -1)))
        priority, priority_issue = 规范优先级(priority_source)
        if priority_issue:
            messages = {
                "missing_priority": "原用例缺少优先级，需要按业务风险补充",
                "priority_mapped": f"优先级已从“{priority_source}”映射为“{priority}”，仍需补充判级理由",
                "unsupported_priority": f"优先级“{priority_source}”不符合 P0/P1/P2，需要人工判级",
            }
            issues.append({"code": priority_issue, "severity": "warning", "message": messages[priority_issue]})
        if not title:
            issues.append({"code": "missing_case_title", "severity": "error", "message": "缺少用例名称"})
        if not steps:
            issues.append({"code": "missing_steps", "severity": "error", "message": "缺少执行步骤"})
        if not expected:
            issues.append({"code": "missing_expected_result", "severity": "error", "message": "缺少预期结果"})
        if expected and re.search(r"(?:同时|并且|以及|且|；|;)", expected):
            issues.append({"code": "possible_multiple_assertions", "severity": "warning", "message": "预期结果可能包含多个独立断言，规范化时需要判断是否拆分"})

        for issue in issues:
            issue_counts[issue["code"]] = issue_counts.get(issue["code"], 0) + 1

        imported_cases.append(
            {
                "source_row": row_number,
                "source_case_id": source_case_id,
                "suggested_case_id": suggested_case_id,
                "feature_group": 规范文本(row.get(mapping.get("feature_group", -1))),
                "case_title": title,
                "priority_source": priority_source,
                "priority_suggestion": priority,
                "preconditions_raw": 规范文本(row.get(mapping.get("preconditions", -1))),
                "test_data_raw": 规范文本(row.get(mapping.get("test_data", -1))),
                "steps_raw": steps,
                "expected_result_raw": expected,
                "source_values": source_values,
                "issues": issues,
            }
        )

    if not imported_cases:
        raise ValueError("工作表中没有识别到有效测试用例")

    source_record = {"path": str(source.resolve()), "sha256": 计算文件哈希(source)}
    mapping_document = {
        field: {
            "header": 规范文本(header_values.get(column)),
            "column": 列名称(column),
        }
        for field, column in mapping.items()
    }
    error_count = sum(1 for case in imported_cases for issue in case["issues"] if issue["severity"] == "error")
    warning_count = sum(1 for case in imported_cases for issue in case["issues"] if issue["severity"] == "warning")
    document = {
        "import_meta": {
            "artifact_role": "existing_case_import",
            "mode": "case_normalization",
            "source": source_record,
            "sheet": sheet["name"],
            "header_row": detected_header_row,
            "column_mapping": mapping_document,
            "downstream_role": "normalization_input_only",
        },
        "normalization_policy": {
            "preserve_business_meaning": True,
            "preserve_source_traceability": True,
            "allow_split_for_single_primary_assertion": True,
            "allow_merge_only_when_semantically_equivalent": True,
            "prd_required_for_requirement_completeness_claim": True,
            "execution_requires_case_design_gate": True,
        },
        "summary": {
            "imported_cases": len(imported_cases),
            "errors": error_count,
            "warnings": warning_count,
            "issue_counts": issue_counts,
        },
        "imported_cases": imported_cases,
    }
    report = {
        "status": "pass" if error_count == 0 else "review_required",
        "gate_role": "import_only",
        "source": source_record,
        "sheet": sheet["name"],
        "header_row": detected_header_row,
        "column_mapping": mapping_document,
        "summary": document["summary"],
        "next_step": "根据 PRD（如有）和导入问题清单生成 05_requirements.yaml、05_test_points.yaml、06_final_test_cases.yaml，并运行用例设计门禁",
    }
    logging.info("已有用例导入完成：%s 条，%s 个错误，%s 个警告", len(imported_cases), error_count, warning_count)
    return document, report


def 写入结果(output: Path, report_path: Path, document: dict[str, Any], report: dict[str, Any]) -> None:
    """写入导入中间产物和报告。

    参数：output、report_path 为输出路径，document、report 为结构化结果。
    返回：无。
    异常：目录不可创建或文件不可写时透传文件异常。
    使用场景：命令行导入成功后的原子交付步骤。
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def 构建参数解析器() -> argparse.ArgumentParser:
    """构建命令行参数解析器。

    参数：无。
    返回：配置完成的 ArgumentParser。
    异常：无。
    使用场景：统一脚本命令行接口和帮助文本。
    """
    parser = argparse.ArgumentParser(description="把已有 XLSX 测试用例导入为规范化流程中间产物")
    parser.add_argument("--source", required=True, type=Path, help="已有测试用例 .xlsx")
    parser.add_argument("--output", required=True, type=Path, help="输出 03_existing_cases_import.yaml")
    parser.add_argument("--report", required=True, type=Path, help="输出 03_existing_cases_import_report.json")
    parser.add_argument("--sheet", help="用例工作表名称；未填写时自动识别")
    parser.add_argument("--header-row", type=int, help="表头行号；未填写时扫描前 20 行")
    return parser


def main() -> int:
    """执行命令行导入流程。

    参数：从命令行读取。
    返回：成功为 0，输入或导入失败为 1。
    异常：已转换为中文错误日志，不向调用方泄漏无上下文堆栈。
    使用场景：由 case_normalization 模式或人工命令调用。
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s：%(message)s")
    args = 构建参数解析器().parse_args()
    try:
        document, report = 导入用例(args.source, args.sheet, args.header_row)
        写入结果(args.output, args.report, document, report)
    except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError) as error:
        logging.error("已有用例导入失败：%s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
