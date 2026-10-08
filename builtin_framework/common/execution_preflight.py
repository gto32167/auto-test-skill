"""Filter prepared workflow nodes before pytest runs any fixture."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def normalize_node(node: str) -> str:
    return str(node).replace("\\", "/").removeprefix("./")


def prepared_nodes(project_root: Path) -> list[str] | None:
    bundle = os.environ.get("AI_QA_WORKFLOW_DIR")
    if not bundle and not (project_root / "06_final_test_cases.yaml").is_file():
        return None  # Legacy standalone scripts have no workflow case source.
    root = Path(bundle).resolve() if bundle else project_root
    script_root = Path(__file__).resolve().parents[2] / "references" / "scripts"
    skill_root = os.environ.get("AI_QA_SKILL_ROOT")
    if not script_root.is_dir() and skill_root:
        script_root = Path(skill_root).resolve() / "references" / "scripts"
    if not script_root.is_dir():
        raise ValueError("无法找到执行前检查模块，请设置 AI_QA_SKILL_ROOT 指向框架技能目录")
    sys.path.insert(0, str(script_root))
    from execution_readiness import disposition, validate_preflight, write_preflight_report
    from workflow_gate_common import list_from, load_yaml, verify_passed_gate

    case_path = root / "06_final_test_cases.yaml"
    plan_path = root / "10_browser_execution_plan.yaml"
    cases = list_from(load_yaml(case_path), ("test_cases",), "test_cases")
    plan_document = load_yaml(plan_path)
    plans = list_from(plan_document, ("browser_execution_plan", "execution_plan"), "execution_plan")
    errors = validate_preflight(cases, plans, plan_document.get("execution_meta") or {})
    notice = root / "10_执行前人工准备清单.md"
    write_preflight_report(notice, cases, plans, errors)
    print(notice.read_text(encoding="utf-8"))
    if errors:
        raise ValueError("执行前准备未完成，尚未启动任何测试；请查看：" + str(notice))
    gate = verify_passed_gate(root / "10_execution_plan_gate.json", case_path, "final_cases")
    verify_passed_gate(root / "10_execution_plan_gate.json", plan_path, "execution_plan")
    verify_passed_gate(root / "07_case_design_gate.json", case_path, "final_cases")
    if gate.get("readiness_version") != "1.0":
        raise ValueError("执行计划尚未通过人工准备门禁，请重新运行 validate_execution_plan.py")
    mapping_path = Path(os.environ.get("AI_QA_CASE_MAPPING", str(root / "case_execution_mapping.yaml"))).resolve()
    mappings = list_from(load_yaml(mapping_path), ("case_execution_mapping",), "case_execution_mapping")
    ids = [str(item.get("case_id", "")) for item in mappings]
    expected = {str(case.get("case_id", "")) for case in cases}
    if set(ids) != expected or len(ids) != len(expected):
        raise ValueError("执行映射必须逐条覆盖最终用例，不能遗漏或重复")
    plan_ids = [str(plan.get("case_id", "")) for plan in plans]
    if set(plan_ids) != expected or len(plan_ids) != len(expected):
        raise ValueError("执行计划必须逐条覆盖最终用例，不能遗漏或重复")
    node_by_id = {str(item["case_id"]): normalize_node(item.get("test_node", "")) for item in mappings}
    nodes = [node_by_id[str(plan["case_id"])] for plan in plans if disposition(plan) == "run"]
    if any(not node or "::" not in node for node in nodes) or len(nodes) != len(set(nodes)):
        raise ValueError("每条用例必须映射到独立、明确的 pytest 节点")
    excluded_nodes = {node_by_id[str(plan["case_id"])] for plan in plans if disposition(plan) != "run"} - {""}
    if excluded_nodes.intersection(nodes):
        raise ValueError("人工/跳过用例不能与自动用例共享测试节点")
    return nodes


def filter_targets(targets: list[str], nodes: list[str]) -> list[str]:
    def matches(node: str, target: str) -> bool:
        target = normalize_node(target).rstrip("/")
        return node == target or node.startswith(target + "::") or node.startswith(target + "/")
    return [node for node in nodes if any(matches(node, target) for target in targets)]
