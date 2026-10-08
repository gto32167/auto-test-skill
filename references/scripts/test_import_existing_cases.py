from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from import_existing_cases import 导入用例, 写入结果


WORKBOOK_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="测试用例" sheetId="1" r:id="rId1"/></sheets>
</workbook>
"""

RELATIONSHIPS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>
"""


def 单元格(reference: str, value: str) -> str:
    """生成测试用 inline string 单元格。

    参数：reference 为单元格引用，value 为单元格文本。
    返回：可写入工作表的 XML 片段。
    异常：无。
    使用场景：构造不依赖第三方库的最小 XLSX 测试输入。
    """
    escaped = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f'<c r="{reference}" t="inlineStr"><is><t>{escaped}</t></is></c>'


def 创建测试工作簿(path: Path, include_expected_header: bool = True) -> None:
    """创建包含既有用例的最小 XLSX。

    参数：path 为输出路径；include_expected_header 控制是否包含预期结果列。
    返回：无。
    异常：文件不可写时透传文件异常。
    使用场景：验证表头映射、合并单元格和审计问题输出。
    """
    headers = ["编号", "模块", "用例标题", "级别", "前提条件", "操作步骤"]
    if include_expected_header:
        headers.append("期望结果")
    header_cells = "".join(单元格(f"{chr(65 + index)}1", value) for index, value in enumerate(headers))
    first_values = ["TC-001", "商品管理", "创建商品", "高", "已登录", "填写后保存"]
    second_values = ["TC-001", "", "创建商品并校验列表", "中", "已登录", "填写、保存并返回列表"]
    if include_expected_header:
        first_values.append("商品创建成功")
        second_values.append("商品创建成功且列表显示该商品")
    first_cells = "".join(单元格(f"{chr(65 + index)}2", value) for index, value in enumerate(first_values) if value)
    second_cells = "".join(单元格(f"{chr(65 + index)}3", value) for index, value in enumerate(second_values) if value)
    sheet_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1">{header_cells}</row>
    <row r="2">{first_cells}</row>
    <row r="3">{second_cells}</row>
  </sheetData>
  <mergeCells count="1"><mergeCell ref="B2:B3"/></mergeCells>
</worksheet>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", WORKBOOK_XML)
        archive.writestr("xl/_rels/workbook.xml.rels", RELATIONSHIPS_XML)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)


class 已有用例导入测试(unittest.TestCase):
    """验证 case_normalization 模式的 XLSX 导入边界。"""

    def test_导入别名表头并保留来源(self) -> None:
        """验证别名、合并模块、优先级和重复 ID 审计。"""
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            source = root / "已有用例.xlsx"
            创建测试工作簿(source)

            document, report = 导入用例(source)

            self.assertEqual(report["status"], "pass")
            self.assertEqual(document["summary"]["imported_cases"], 2)
            self.assertEqual(document["imported_cases"][0]["priority_suggestion"], "P0")
            self.assertEqual(document["imported_cases"][1]["priority_suggestion"], "P1")
            self.assertEqual(document["imported_cases"][1]["feature_group"], "商品管理")
            issue_codes = {item["code"] for item in document["imported_cases"][1]["issues"]}
            self.assertIn("duplicate_case_id", issue_codes)
            self.assertIn("possible_multiple_assertions", issue_codes)
            self.assertEqual(document["imported_cases"][0]["source_row"], 2)
            self.assertIn("G:期望结果", document["imported_cases"][0]["source_values"])

    def test_必要表头缺失时拒绝导入(self) -> None:
        """验证缺少预期结果列时不会产生伪规范化结果。"""
        with tempfile.TemporaryDirectory() as temp_directory:
            source = Path(temp_directory) / "缺列用例.xlsx"
            创建测试工作簿(source, include_expected_header=False)

            with self.assertRaisesRegex(ValueError, "预期结果"):
                导入用例(source)

    def test_写入结果使用_utf8_中文(self) -> None:
        """验证 YAML 和 JSON 交付物保留可读中文。"""
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            source = root / "已有用例.xlsx"
            output = root / "03_existing_cases_import.yaml"
            report_path = root / "03_existing_cases_import_report.json"
            创建测试工作簿(source)
            document, report = 导入用例(source)

            写入结果(output, report_path, document, report)

            self.assertIn("商品管理", output.read_text(encoding="utf-8"))
            self.assertIn("导入", report_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
