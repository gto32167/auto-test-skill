from __future__ import annotations

"""契约 2.0 回填兼容入口。

旧版脚本会直接读取 JSONL/汇总文件并生成 XLSX，能够绕过用例设计门禁和
执行结果门禁。该入口现在只负责把完整参数转交给正式门禁回填器；缺少任一
门禁或权威 YAML 输入时不会生成交付物。
"""

import argparse
import subprocess
import sys
from pathlib import Path


def default_workflow_root() -> Path:
    bundled_root = Path(__file__).resolve().parents[2]
    if (bundled_root / "references" / "scripts" / "backfill_case_execution_results.py").is_file():
        return bundled_root
    return Path.cwd()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate backfill files only through the semantic-contract gates."
    )
    parser.add_argument(
        "--workflow-root",
        default=str(default_workflow_root()),
        help="ai-qa-prd-workflow 根目录；模板被复制到项目后必须显式提供",
    )
    parser.add_argument("--source", required=True, help="已通过门禁的 06_final_test_cases.yaml")
    parser.add_argument("--case-gate", required=True, help="状态为 pass 的 07_case_design_gate.json")
    parser.add_argument("--execution-yaml", required=True, help="完整的 11_test_execution_results.yaml")
    parser.add_argument("--execution-gate", required=True, help="状态为 pass 的 12_execution_gate.json")
    parser.add_argument("--template-xlsx", required=True, help="08_测试用例_模板版.xlsx")
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--output-xlsx", required=True)
    args = parser.parse_args()

    workflow_root = Path(args.workflow_root).resolve()
    canonical_script = workflow_root / "references" / "scripts" / "backfill_case_execution_results.py"
    if not canonical_script.is_file():
        parser.error(
            "找不到门禁回填器，请通过 --workflow-root 指向 ai-qa-prd-workflow 正式目录"
        )

    completed = subprocess.run(
        [
            sys.executable,
            str(canonical_script),
            "--source",
            args.source,
            "--case-gate",
            args.case_gate,
            "--execution-yaml",
            args.execution_yaml,
            "--execution-gate",
            args.execution_gate,
            "--template-xlsx",
            args.template_xlsx,
            "--output-md",
            args.output_md,
            "--output-xlsx",
            args.output_xlsx,
        ],
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
