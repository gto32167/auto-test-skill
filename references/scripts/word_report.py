from __future__ import annotations

"""Minimal, dependency-free OOXML writer for formal workflow reports."""

from datetime import datetime, timezone
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS = "http://purl.org/dc/elements/1.1/"
DCTERMS_NS = "http://purl.org/dc/terms/"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
ET.register_namespace("w", W_NS)
ET.register_namespace("cp", CP_NS)
ET.register_namespace("dc", DC_NS)
ET.register_namespace("dcterms", DCTERMS_NS)
ET.register_namespace("xsi", XSI_NS)


def qn(name: str) -> str:
    return f"{{{W_NS}}}{name}"


def paragraph(text: str, style: str = "Normal", *, bold: bool = False) -> ET.Element:
    node = ET.Element(qn("p"))
    props = ET.SubElement(node, qn("pPr"))
    ET.SubElement(props, qn("pStyle"), {qn("val"): style})
    run = ET.SubElement(node, qn("r"))
    run_props = ET.SubElement(run, qn("rPr"))
    ET.SubElement(run_props, qn("rFonts"), {qn("ascii"): "Microsoft YaHei", qn("eastAsia"): "Microsoft YaHei"})
    if bold:
        ET.SubElement(run_props, qn("b"))
    text_node = ET.SubElement(run, qn("t"))
    text_node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text_node.text = text
    return node


def table(headers: list[str], rows: list[list[str]]) -> ET.Element:
    table_node = ET.Element(qn("tbl"))
    props = ET.SubElement(table_node, qn("tblPr"))
    ET.SubElement(props, qn("tblStyle"), {qn("val"): "TableGrid"})
    ET.SubElement(props, qn("tblW"), {qn("w"): "0", qn("type"): "auto"})
    for row_index, values in enumerate([headers, *rows]):
        row = ET.SubElement(table_node, qn("tr"))
        for value in values:
            cell = ET.SubElement(row, qn("tc"))
            cell_props = ET.SubElement(cell, qn("tcPr"))
            ET.SubElement(cell_props, qn("tcW"), {qn("w"): "0", qn("type"): "auto"})
            cell.append(paragraph(str(value), bold=row_index == 0))
    return table_node


def markdown_body(lines: list[str]) -> list[ET.Element]:
    blocks: list[ET.Element] = []
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        if not line:
            index += 1
            continue
        if line.startswith("|") and index + 1 < len(lines) and lines[index + 1].lstrip().startswith("|"):
            raw_rows: list[list[str]] = []
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                raw_rows.append([cell.strip() for cell in lines[index].strip().strip("|").split("|")])
                index += 1
            if len(raw_rows) >= 2 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in raw_rows[1]):
                blocks.append(table(raw_rows[0], raw_rows[2:]))
            else:
                for raw_row in raw_rows:
                    blocks.append(paragraph(" | ".join(raw_row)))
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            level = len(heading.group(1))
            blocks.append(paragraph(heading.group(2), "Title" if level == 1 else f"Heading{level - 1}"))
        elif line.startswith("- "):
            blocks.append(paragraph(f"• {line[2:]}", "ListParagraph"))
        elif line.startswith("> "):
            blocks.append(paragraph(line[2:], "Quote"))
        else:
            blocks.append(paragraph(line))
        index += 1
    return blocks


def styles_xml() -> bytes:
    styles = ET.Element(qn("styles"))
    definitions = [
        ("Normal", "Normal", 21, False),
        ("Title", "Title", 36, True),
        ("Heading1", "Heading 1", 28, True),
        ("Heading2", "Heading 2", 24, True),
        ("ListParagraph", "List Paragraph", 21, False),
        ("Quote", "Quote", 20, False),
        ("TableGrid", "Table Grid", 20, False),
    ]
    for style_id, name, size, bold in definitions:
        style_type = "table" if style_id == "TableGrid" else "paragraph"
        style = ET.SubElement(styles, qn("style"), {qn("type"): style_type, qn("styleId"): style_id})
        ET.SubElement(style, qn("name"), {qn("val"): name})
        if style_type == "paragraph":
            run_props = ET.SubElement(style, qn("rPr"))
            ET.SubElement(run_props, qn("rFonts"), {qn("ascii"): "Microsoft YaHei", qn("eastAsia"): "Microsoft YaHei"})
            ET.SubElement(run_props, qn("sz"), {qn("val"): str(size)})
            if bold:
                ET.SubElement(run_props, qn("b"))
        else:
            table_props = ET.SubElement(style, qn("tblPr"))
            borders = ET.SubElement(table_props, qn("tblBorders"))
            for border in ("top", "left", "bottom", "right", "insideH", "insideV"):
                ET.SubElement(borders, qn(border), {qn("val"): "single", qn("sz"): "4", qn("color"): "B7B7B7"})
    return ET.tostring(styles, encoding="utf-8", xml_declaration=True)


def write_docx_from_markdown(lines: list[str], output_path: Path, title: str) -> None:
    document = ET.Element(qn("document"))
    body = ET.SubElement(document, qn("body"))
    for block in markdown_body(lines):
        body.append(block)
    section = ET.SubElement(body, qn("sectPr"))
    ET.SubElement(section, qn("pgSz"), {qn("w"): "11906", qn("h"): "16838"})
    ET.SubElement(section, qn("pgMar"), {qn("top"): "1134", qn("right"): "1134", qn("bottom"): "1134", qn("left"): "1134"})

    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    core = ET.Element(f"{{{CP_NS}}}coreProperties")
    ET.SubElement(core, f"{{{DC_NS}}}title").text = title
    ET.SubElement(core, f"{{{DC_NS}}}creator").text = "AI QA PRD Workflow"
    ET.SubElement(core, f"{{{DCTERMS_NS}}}created", {f"{{{XSI_NS}}}type": "dcterms:W3CDTF"}).text = timestamp
    ET.SubElement(core, f"{{{DCTERMS_NS}}}modified", {f"{{{XSI_NS}}}type": "dcterms:W3CDTF"}).text = timestamp

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""
    root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""
    document_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""
    app = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>AI QA PRD Workflow</Application></Properties>"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("word/document.xml", ET.tostring(document, encoding="utf-8", xml_declaration=True))
        archive.writestr("word/styles.xml", styles_xml())
        archive.writestr("word/_rels/document.xml.rels", document_rels)
        archive.writestr("docProps/core.xml", ET.tostring(core, encoding="utf-8", xml_declaration=True))
        archive.writestr("docProps/app.xml", app)
