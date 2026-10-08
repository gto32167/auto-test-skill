from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = SCRIPT_DIR.parent.parent / "assets" / "templates"
VALIDATE_CASES = SCRIPT_DIR / "validate_case_design.py"


def 读取模板(name: str) -> dict:
    """读取工作流 YAML 模板。

    参数：name 为模板文件名。
    返回：模板根映射。
    异常：模板不存在或不是映射时抛出对应异常。
    使用场景：复用已经通过基础门禁的标准测试数据。
    """
    value = yaml.safe_load((TEMPLATE_DIR / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"模板根节点不是映射：{name}")
    return value


def 写入_yaml(path: Path, value: dict) -> None:
    """以 UTF-8 写入 YAML。

    参数：path 为输出路径，value 为根映射。
    返回：无。
    异常：目录不可写时透传文件异常。
    使用场景：构造规范化门禁测试输入。
    """
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")


class 规范化来源门禁测试(unittest.TestCase):
    """验证已有 Excel 用例进入最终源前必须保持来源追踪。"""

    def setUp(self) -> None:
        """创建标准链和已有用例导入产物。"""
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.requirements = self.root / "05_requirements.yaml"
        self.points = self.root / "05_test_points.yaml"
        self.cases = self.root / "06_final_test_cases.yaml"
        self.imported = self.root / "03_existing_cases_import.yaml"
        self.gate = self.root / "07_case_design_gate.json"

        写入_yaml(self.requirements, 读取模板("requirements.yaml"))
        写入_yaml(self.points, 读取模板("coverage-matrix.yaml"))
        case_document = 读取模板("test-cases.yaml")
        case_document["case_source"].update(
            {
                "generation_mode": "case_normalization",
                "requirement_source": "existing_xlsx_baseline",
                "requirement_completeness": "not_assessed",
            }
        )
        imported_cases = []
        for index, case in enumerate(case_document["test_cases"], 2):
            case["normalization_trace"] = {
                "origin": "existing_xlsx",
                "source_rows": [index],
                "source_case_ids": [case["case_id"]],
                "change_type": "preserved",
                "reason": "保留原用例业务语义并补齐机器契约",
            }
            imported_cases.append(
                {
                    "source_row": index,
                    "source_case_id": case["case_id"],
                    "suggested_case_id": case["case_id"],
                }
            )
        写入_yaml(self.cases, case_document)
        写入_yaml(
            self.imported,
            {
                "import_meta": {
                    "artifact_role": "existing_case_import",
                    "mode": "case_normalization",
                    "source": {"path": "E:\\cases\\已有用例.xlsx", "sha256": "a" * 64},
                },
                "imported_cases": imported_cases,
            },
        )

    def tearDown(self) -> None:
        """清理隔离测试目录。"""
        self.temp.cleanup()

    def 运行门禁(self, include_import: bool = True) -> subprocess.CompletedProcess[str]:
        """运行用例设计门禁。

        参数：include_import 控制是否传入已有用例导入产物。
        返回：门禁子进程结果。
        异常：进程超时等异常透传给测试框架。
        使用场景：验证规范化来源约束的通过和拒绝分支。
        """
        arguments = [
            sys.executable,
            str(VALIDATE_CASES),
            "--requirements",
            str(self.requirements),
            "--test-points",
            str(self.points),
            "--final-cases",
            str(self.cases),
        ]
        if include_import:
            arguments.extend(["--existing-case-import", str(self.imported)])
        arguments.extend(["--output", str(self.gate)])
        return subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8", timeout=60)

    def test_来源完整时通过设计门禁(self) -> None:
        """验证规范化用例和真实导入源行能够形成闭合门禁。"""
        result = self.运行门禁()
        self.assertEqual(result.returncode, 0, result.stderr)
        gate = json.loads(self.gate.read_text(encoding="utf-8"))
        self.assertEqual(gate["status"], "pass")
        self.assertIn("existing_case_import", gate["inputs"])

    def test_缺少导入产物时拒绝(self) -> None:
        """验证规范化模式不能绕过已有用例导入产物。"""
        result = self.运行门禁(include_import=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires --existing-case-import", result.stderr)

    def test_引用不存在源行时拒绝(self) -> None:
        """验证最终用例不得绑定导入产物中不存在的行。"""
        document = yaml.safe_load(self.cases.read_text(encoding="utf-8"))
        document["test_cases"][0]["normalization_trace"]["source_rows"] = [999]
        写入_yaml(self.cases, document)

        result = self.运行门禁()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown source rows: 999", result.stderr)

    def test_引用不存在原用例_id_时拒绝(self) -> None:
        """验证最终用例声明的原 ID 必须属于所绑定的 Excel 行。"""
        document = yaml.safe_load(self.cases.read_text(encoding="utf-8"))
        document["test_cases"][0]["normalization_trace"]["source_case_ids"] = ["TC-NOT-FOUND"]
        写入_yaml(self.cases, document)

        result = self.运行门禁()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown source case IDs: TC-NOT-FOUND", result.stderr)


if __name__ == "__main__":
    unittest.main()
