"""Tester-facing explanations; technical details stay in their own fields."""
from __future__ import annotations

import re
from typing import Any

from workflow_gate_common import text


def readable_fact(value: Any) -> str:
    value = text(value)
    if not value or re.search(r"<[^>]+>|Locator|Traceback|stack trace|UI\s*断言|^断言失败|\b(?:selector|TimeoutError|Error:|UI assertion|execution_trace|semantic_contract)\b", value, re.I):
        return ""
    if value.startswith(("{", "[")) or not re.search(r"[\u4e00-\u9fff]", value):
        return ""
    return re.sub(r"\s+", " ", value)[:300]


def explain_result(item: dict[str, Any], expected: str = "") -> tuple[str, str]:
    """Return (plain-language reason, next action), without inventing progress."""
    status = text(item.get("status"))
    summary = item.get("result_summary") or {}
    summary = summary if isinstance(summary, dict) else {}
    explicit = readable_fact(item.get("human_reason"))
    action = readable_fact(item.get("next_action"))
    if explicit and action:
        return explicit, action
    reason_code = text(item.get("not_run_reason"))
    if status == "not_run":
        reasons = {
            "manual_execution_required": ("这条用例需要人亲自操作，本轮自动化没有执行。", "按人工操作清单执行，并单独记录人工测试结果。"),
            "human_preflight_skip": ("测试人员已在执行前标记跳过，本轮没有执行这条用例。", "按准备清单补齐数据或条件，确认后再安排测试。"),
            "p0_all_failed_stop_gate": ("核心用例全部失败，本轮后续用例已停止，尚未执行。", "先修复核心问题，再重新执行后续用例。"),
            "script_status_missing": ("没有找到这条用例的执行记录，暂时无法判断测试结果。", "检查脚本映射和执行日志，补跑这条用例。"),
        }
        reason, next_step = reasons.get(reason_code, ("这条用例尚未执行，当前没有测试结论。", "确认执行安排后补跑这条用例。"))
        decision_reason = readable_fact(item.get("preflight_reason"))
        if decision_reason:
            reason += f"原因：{decision_reason}"
        return explicit or reason, action or next_step
    if status == "blocked":
        raw = text(item.get("blocker_reason"))
        blocker_type = text(item.get("blocker_type"))
        rules = [
            (("401", "403", "登录", "权限", "authentication"), "登录状态或账号权限不满足要求，暂时无法判断功能是否正常。", "重新登录并确认测试账号权限，再重跑这条用例。"),
            (("前置", "数据工厂", "缺少", "数据缺失"), "测试所需的数据或业务状态没有准备好，暂时无法判断功能是否正常。", "按人工准备清单补齐数据，并核对验收条件后重跑。"),
            (("Locator", "selector", "Timeout", "控件", "定位"), "测试工具没有完成页面操作，暂时无法判断功能是否正常。", "查看截图和技术原因，修复页面定位或等待方式后重跑。"),
            (("证据", "观察", "截图"), "已记录的测试证据不足，暂时无法确认功能是否达到预期。", "补充关键步骤的截图和实际业务结果，再复核。"),
        ]
        for words, reason, next_step in rules:
            if any(word.lower() in (raw + blocker_type).lower() for word in words):
                return explicit or reason, action or next_step
        detail = readable_fact(raw)
        return explicit or (f"测试暂时无法继续：{detail} 当前不能判断功能是否正常。" if detail else "测试条件或工具尚未就绪，当前不能判断功能是否正常。"), action or "查看技术原因，确认缺少的条件并补齐后重跑。"
    if status == "failed":
        step = text(item.get("failed_step_no"))
        where = f"第{step}步" if step else "本次检查"
        wanted = readable_fact(summary.get("expected") or expected)
        actual = readable_fact(summary.get("actual"))
        reason = f"{where}没有达到预期。"
        if wanted:
            reason += f"预期：{wanted}。"
        if actual:
            reason += f"实际：{actual}。"
        if not actual:
            reason += "具体差异请结合截图和技术原因复核。"
        return explicit or reason, action or "核对实际结果和截图，确认问题后提交缺陷，修复后回归。"
    return "", "无需处理。"
