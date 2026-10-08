from __future__ import annotations

import argparse
from copy import deepcopy
import shutil
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from workflow_gate_common import case_rows_from_yaml, verify_passed_gate
from execution_readiness import CASE_HEADERS


NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
ET.register_namespace("", NS["x"])


def column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def source_rows(path: Path) -> list[dict[str, str]]:
    if path.suffix.lower() not in {".yaml", ".yml"}:
        raise ValueError("The canonical final-case source must be YAML")
    return case_rows_from_yaml(path)


def inline_cell(ref: str, style: str, value: str) -> ET.Element:
    cell = ET.Element(f"{{{NS['x']}}}c", r=ref, s=style, t="inlineStr")
    inline = ET.SubElement(cell, f"{{{NS['x']}}}is")
    text = ET.SubElement(inline, f"{{{NS['x']}}}t")
    if value.startswith(" ") or value.endswith(" ") or "\n" in value:
        text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text.text = value
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


def replace_zip_entry(zip_path: Path, member: str, payload: bytes) -> None:
    temporary_path = zip_path.with_suffix(".tmp")
    with zipfile.ZipFile(zip_path, "r") as source, zipfile.ZipFile(temporary_path, "w") as target:
        for item in source.infolist():
            if item.filename == member:
                continue
            target.writestr(item, source.read(item.filename))
        target.writestr(member, payload)
    # Windows can briefly keep a newly written workbook open for scanning.
    for attempt in range(4):
        try:
            temporary_path.replace(zip_path)
            break
        except PermissionError:
            if attempt == 3:
                raise
            time.sleep(0.1 * (2 ** attempt))


def create_default_workbook(path: Path) -> None:
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>'''
    root_rels = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{PKG_REL_NS}">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''
    workbook = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="{NS['x']}" xmlns:r="{REL_NS}">
  <sheets><sheet name="测试用例" sheetId="1" r:id="rId1"/></sheets>
</workbook>'''
    workbook_rels = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{PKG_REL_NS}">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''
    styles = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="{NS['x']}">
  <fonts count="2"><font><sz val="11"/><name val="DengXian"/></font><font><b/><sz val="11"/><name val="DengXian"/></font></fonts>
  <fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="7">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
    <xf numFmtId="0" fontId="1" fillId="1" borderId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="1" fillId="1" borderId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''
    worksheet = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="{NS['x']}"><dimension ref="A1:G1"/><sheetViews><sheetView workbookViewId="0"/></sheetViews><sheetFormatPr defaultRowHeight="18"/><sheetData/><autoFilter ref="A1:G1"/></worksheet>'''
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr("[Content_Types].xml", content_types.encode("utf-8"))
        zip_file.writestr("_rels/.rels", root_rels.encode("utf-8"))
        zip_file.writestr("xl/workbook.xml", workbook.encode("utf-8"))
        zip_file.writestr("xl/_rels/workbook.xml.rels", workbook_rels.encode("utf-8"))
        zip_file.writestr("xl/styles.xml", styles.encode("utf-8"))
        zip_file.writestr("xl/worksheets/sheet1.xml", worksheet.encode("utf-8"))


def add_readiness_styles(path: Path) -> dict[str, str]:
    """Append highlight styles to default or user-supplied workbook styles."""
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("xl/styles.xml"))
    fills = root.find("x:fills", NS)
    styles = root.find("x:cellXfs", NS)
    if fills is None or styles is None or not len(styles):
        raise ValueError("Workbook styles must contain fills and cellXfs")
    result = {}
    base = styles[min(6, len(styles) - 1)]
    for label, color in (("必须人工执行", "FFF8CBAD"), ("人工准备后自动执行", "FFFFF2CC")):
        fill = ET.SubElement(fills, f"{{{NS['x']}}}fill")
        pattern = ET.SubElement(fill, f"{{{NS['x']}}}patternFill", patternType="solid")
        ET.SubElement(pattern, f"{{{NS['x']}}}fgColor", rgb=color)
        ET.SubElement(pattern, f"{{{NS['x']}}}bgColor", indexed="64")
        style = deepcopy(base)
        style.set("fillId", str(len(fills) - 1))
        style.set("applyFill", "1")
        result[label] = str(len(styles))
        styles.append(style)
    fills.set("count", str(len(fills)))
    styles.set("count", str(len(styles)))
    replace_zip_entry(path, "xl/styles.xml", ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the testcase xlsx from a gated final-case source.")
    parser.add_argument("--template", help="Optional user-provided xlsx template; includes human readiness columns")
    parser.add_argument("--source", required=True, help="Gated 06_final_test_cases.yaml")
    parser.add_argument("--case-gate", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    source_path = Path(args.source).resolve()
    gate_path = Path(args.case_gate).resolve()
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    verify_passed_gate(gate_path, source_path, "final_cases")

    rows = source_rows(source_path)
    if not rows:
        raise SystemExit("No testcase rows parsed from the gated final-case source")
    if args.template:
        shutil.copyfile(Path(args.template).resolve(), output_path)
    else:
        create_default_workbook(output_path)
    readiness_styles = add_readiness_styles(output_path)

    headers = CASE_HEADERS
    values = [[row.get(header, "") for header in headers] for row in rows]

    with zipfile.ZipFile(output_path, "r") as zip_file:
        sheet_path = first_sheet_path(zip_file)
        sheet_root = ET.fromstring(zip_file.read(sheet_path))
    sheet_data = sheet_root.find("x:sheetData", NS)
    if sheet_data is None:
        raise SystemExit("Template worksheet has no sheetData")
    for child in list(sheet_data):
        sheet_data.remove(child)

    columns = [chr(ord("A") + index) for index in range(len(headers))]
    header_row = ET.Element(f"{{{NS['x']}}}row", r="1", ht="25", customHeight="1", spans=f"1:{len(headers)}")
    for column, title in zip(columns, headers):
        header_row.append(inline_cell(f"{column}1", "3" if column == "A" else "4", title))
    sheet_data.append(header_row)
    for row_number, row_values in enumerate(values, 2):
        row_element = ET.Element(f"{{{NS['x']}}}row", r=str(row_number), ht="34.5", customHeight="1", spans=f"1:{len(headers)}")
        for column, header, value in zip(columns, headers, row_values):
            style = readiness_styles.get(value, "6") if header == "执行级别" else ("5" if column == "A" else "6")
            row_element.append(inline_cell(f"{column}{row_number}", style, value))
        sheet_data.append(row_element)

    last_row = len(values) + 1
    last_column = columns[-1]
    dimension = sheet_root.find("x:dimension", NS)
    if dimension is not None:
        dimension.set("ref", f"A1:{last_column}{last_row}")
    auto_filter = sheet_root.find("x:autoFilter", NS)
    if auto_filter is not None:
        auto_filter.set("ref", f"A1:{last_column}{last_row}")
    replace_zip_entry(output_path, sheet_path, ET.tostring(sheet_root, encoding="utf-8", xml_declaration=True))
    print(f"Generated gated testcase xlsx: {output_path} ({len(values)} cases)")


if __name__ == "__main__":
    main()
