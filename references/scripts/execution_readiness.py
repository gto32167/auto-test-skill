"""Design-time human classification and pre-execution preparation gate."""
from __future__ import annotations

import re
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from workflow_gate_common import as_list, text

LEVEL_LABELS = {"auto": "可自动执行", "assisted": "人工准备后自动执行", "manual": "必须人工执行"}
CASE_HEADERS = ["用例ID", "功能集合", "用例名称", "优先级", "执行级别", "人工介入说明", "人工准备清单", "前置条件", "执行步骤", "预期结果"]
NON_RUN_REASONS = {"manual": "manual_execution_required"}


def readiness(case: dict[str, Any]) -> dict[str, Any]:
    value = case.get("execution_readiness")
    return value if isinstance(value, dict) else {}


def core_action_texts(case: dict[str, Any]) -> list[str]:
    """Use formal actions when present; normalized 'execute' steps keep input."""
    source = case.get("formal_steps") or case.get("steps")
    return [
        " ".join(text(step.get(field)) for field in ("action", "formal_text", "input") if text(step.get(field)))
        if isinstance(step, dict) else text(step)
        for step in as_list(source)
    ]


def core_actions_sha256(actions: list[str]) -> str:
    return hashlib.sha256(json.dumps(actions, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _actual_transaction_action(action: str) -> bool:
    # Reading payment fields and changing refund configuration are business UI
    # actions; submitting money movement or running a refund task is different.
    value = re.sub(r"[【】\[\]（）()\s]", "", action)
    readonly = bool(re.match(r"^(查看|查询|核对|与.+核对|展示|检查|展开|选择|输入|清空|修改|将|进入|打开|刷新|取消|关闭)", value))
    if not readonly and re.search(r"扫码|扫描.*(?:码|二维码)|\b(?:qr_scan|scan_qr)\b", value, re.I):
        return True
    if re.search(r"(?:并|然后|后)(?:使用.*)?(?:扫码|扫描.*码)", value):
        return True
    if re.search(r"(?:发起|触发|执行|完成|提交|尝试|确认|成功)(?:自动|每日|重新|进行|微信|渠道|实际|真实|一笔|.*?任务)?(?:支付|付款|退款|交易|转账|扣款)", value):
        return True
    if re.search(r"点击(?:确认)?(?:立即)?(?:支付|付款|确认重新退款|确认退款|提交退款|发起退款)", value):
        return True
    if re.search(r"(?:用户)?成功购买|^(?:购买|下单并支付)(?:付费|商品|券包|订单)", value):
        return True
    if re.search(r"\b(?:click|press|submit|confirm|execute|trigger|perform)\b.*\b(?:payment|pay|refund|transaction)\b", action, re.I):
        return True
    return bool(re.search(r"\b(?:pay|submit_payment|confirm_payment|execute_refund|submit_refund|confirm_refund|transaction|qr_scan|scan_qr)\b", action, re.I))


def _valid_non_transaction_review(case: dict[str, Any], actions: list[str]) -> bool:
    review = case.get("core_operation_review")
    if not isinstance(review, dict) or review.get("classification") != "automation_core":
        return False
    if not text(review.get("reviewed_by")) or not text(review.get("reason")):
        return False
    if review.get("core_actions") != actions or review.get("core_actions_sha256") != core_actions_sha256(actions):
        return False
    allowed = r"^(查看|查询|核对|与.+核对|使用.+登录|展示|检查|展开|选择|输入|清空|修改|将|进入|打开|刷新|取消|关闭|点击|分别|重新进入|等待|按|尝试查找|直接访问|创建活动)"
    if not actions or any(not re.match(allowed, action) for action in actions):
        return False
    # Opening a retry-refund dialog is safe only when that exact operation was
    # reviewed as preview-only. A later confirmation always wins above.
    if any(re.search(r"点击.*重新退款", action) for action in actions):
        if review.get("operation_scope") != "confirmation_dialog_only":
            return False
        title = text(case.get("formal_case_title") or case.get("case_title"))
        if "打开确认弹窗" not in title or len(actions) != 1:
            return False
    return True


def requires_manual(case: dict[str, Any]) -> bool:
    actions = core_action_texts(case)
    operation = text(case.get("operation_type"))
    if re.fullmatch(r"payment|transaction|qr_scan|scan_qr|refund|execute_refund", operation, re.I):
        return True
    if any(_actual_transaction_action(action) for action in actions):
        return True
    # Structured cases may split 'click' and 'payment button' into action and
    # target; the target must not disappear from the submission guard.
    for step in as_list(case.get("formal_steps") or case.get("steps")):
        if isinstance(step, dict) and _actual_transaction_action(
            " ".join(text(step.get(field)) for field in ("action", "formal_text", "input", "target"))
        ):
            return True
    if _valid_non_transaction_review(case, actions):
        return False
    if not case.get("core_operation_review") and actions and all(
        re.match(r"^(查看|查询|核对|与.+核对|展示|检查)", action) for action in actions
    ):
        return False
    parts = [operation, text(case.get("case_title")), text(case.get("formal_case_title")), *actions]
    value = re.sub(r"已(?:付款|支付)(?:成功)?", "", " ".join(parts))
    # A field name alone is not evidence of money movement. Without reviewed
    # actions, retain conservative treatment of actual payment/scan titles.
    value = re.sub(r"(?:微信)?(?:支付|付款|交易)(?:流水单号|交易单号|单号|金额|时间|状态|字段|结果|信息)", "", value)
    value = re.sub(r"查看(?:三种状态的)?[【】]*重新退款[【】]*按钮", "", value)
    if any(re.search(r"点击.*重新退款", action) for action in actions):
        return True
    return bool(re.search(r"扫码|扫描.*码|交易|付款|支付|\b(payment|transaction|qr_scan|scan_qr)\b", value, re.I))


def validate_case_readiness(case: dict[str, Any]) -> list[str]:
    label = f"用例 {text(case.get('case_id'))}"
    data = readiness(case)
    errors = []
    level = text(data.get("level"))
    if level not in LEVEL_LABELS:
        errors.append(f"{label} 必须标记执行级别 execution_readiness.level（auto/assisted/manual），不能默认自动执行")
    if requires_manual(case) and level != "manual":
        errors.append(f"{label} 涉及交易、付款或扫码，必须标记 manual，执行前交给人工")
    actions = data.get("human_actions", [])
    preparations = data.get("preparations", [])
    if not isinstance(actions, list) or any(not isinstance(action, str) or not text(action) for action in actions):
        errors.append(f"{label} human_actions 必须是人工操作说明列表")
    if not isinstance(preparations, list) or any(not isinstance(item, dict) for item in preparations):
        errors.append(f"{label} preparations 必须是人工准备事项列表")
        preparations = []
    if level in {"assisted", "manual"} and not text(data.get("reason")):
        errors.append(f"{label} 需要说明为什么必须人工介入")
    if level == "manual" and not actions:
        errors.append(f"{label} 必须列出需要人完成的操作 human_actions")
    if level == "assisted" and actions:
        errors.append(f"{label} 核心操作需要人工完成，应标记 manual；assisted 只允许人工准备")
    if level == "assisted" and not preparations:
        errors.append(f"{label} 必须列出人工准备的数据、权限或配置")
    if level == "auto" and (actions or preparations):
        errors.append(f"{label} 有人工依赖，不能标记为 auto")
    ids = []
    for item in preparations:
        ids.append(text(item.get("id")))
        for field in ("id", "description", "owner", "acceptance"):
            if not text(item.get(field)):
                errors.append(f"{label} 人工准备事项必须填写 {field}（编号、准备内容、负责人、验收条件）")
    if len(ids) != len(set(ids)):
        errors.append(f"{label} 人工准备事项编号重复")
    # Free-text dependencies must also be structured before execution.
    setup_text = " ".join(text(case.get(key)) for key in ("preconditions", "formal_preconditions", "data_setup", "setup_strategy"))
    if re.search(r"人工|手工|人为|manual|human", setup_text, re.I) and level == "auto":
        errors.append(f"{label} 前置条件含人工依赖，请改为 assisted/manual 并列出准备事项")
    return errors


def disposition(plan: dict[str, Any]) -> str:
    value = plan.get("readiness") or {}
    return text(value.get("disposition")) if isinstance(value, dict) else ""


def _confirmed(value: dict[str, Any], label: str) -> list[str]:
    errors = []
    if not text(value.get("confirmed_by")):
        errors.append(f"{label} 未记录人工确认人 confirmed_by")
    try:
        datetime.fromisoformat(text(value.get("confirmed_at")).replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{label} 未记录有效的确认时间 confirmed_at（ISO 8601）")
    return errors


def validate_preflight(cases: list[dict[str, Any]], plans: list[dict[str, Any]], meta: dict[str, Any]) -> list[str]:
    errors = []
    by_id = {text(plan.get("case_id")): plan for plan in plans}
    human_cases = [case for case in cases if text(readiness(case).get("level")) in {"assisted", "manual"}]
    if human_cases:
        ack = meta.get("preflight_acknowledgement")
        if not isinstance(ack, dict):
            errors.append("开始自动化前必须把人工执行/准备清单提示给测试人员，并记录 preflight_acknowledgement")
        else:
            errors.extend(_confirmed(ack, "人工准备清单"))
    for case in cases:
        errors.extend(validate_case_readiness(case))
        case_id = text(case.get("case_id"))
        label = f"用例 {case_id}"
        case_ready = readiness(case)
        plan = by_id.get(case_id, {})
        decision = plan.get("readiness") or {}
        if not isinstance(decision, dict):
            decision = {}
        route = disposition(plan)
        if route == "skip":
            errors.append(f"{label} 不允许使用 skip；请完成准备后 run，或明确移交 manual")
            continue
        if route not in {"run", "manual"}:
            errors.append(f"{label} 执行前必须决定 run/manual；未完成准备不能跳过，必须先补齐准备事项")
        if case_ready.get("level") == "manual" and route != "manual":
            errors.append(f"{label} 必须人工执行，禁止加入自动执行队列")
        if case_ready.get("level") != "manual" and route == "manual":
            errors.append(f"{label} 人工执行分级与执行计划不一致")
        if route == "manual":
            errors.extend(_confirmed(decision, f"{label} 人工移交决定"))
            if not text(decision.get("reason")):
                errors.append(f"{label} 人工移交必须说明原因")
        confirmations = decision.get("preparations", [])
        if not isinstance(confirmations, list) or any(not isinstance(item, dict) for item in confirmations):
            errors.append(f"{label} 准备确认必须是列表")
            confirmations = []
        required = {text(item.get("id")) for item in as_list(case_ready.get("preparations")) if isinstance(item, dict)}
        actual = [text(item.get("id")) for item in confirmations]
        if required != set(actual) or len(actual) != len(set(actual)):
            errors.append(f"{label} 必须逐项列出全部准备确认，不能遗漏或重复")
        for item in confirmations:
            state = text(item.get("status"))
            if state not in {"pending", "ready"}:
                errors.append(f"{label} 准备事项 {item.get('id')} 状态必须是 pending/ready")
            if route == "run" and state != "ready":
                errors.append(f"{label} 尚未准备好 {item.get('id')}；必须先准备数据，不能跳过")
            if state == "ready":
                errors.extend(_confirmed(item, f"{label} 准备事项 {item.get('id')}"))
                if not text(item.get("evidence")):
                    errors.append(f"{label} 准备事项 {item.get('id')} 需填写可核对的数据编号或验收记录 evidence")
    return errors


def readiness_columns(case: dict[str, Any]) -> dict[str, str]:
    data = readiness(case)
    actions = "；".join(text(action) for action in as_list(data.get("human_actions")))
    reason = "；".join(value for value in (text(data.get("reason")), actions) if value)
    preparations = []
    for item in as_list(data.get("preparations")):
        if isinstance(item, dict):
            preparations.append(f"{text(item.get('id'))}：{text(item.get('description'))}；负责人：{text(item.get('owner'))}；验收：{text(item.get('acceptance'))}")
    return {"执行级别": LEVEL_LABELS.get(text(data.get("level")), "未分级（不可执行）"), "人工介入说明": reason or "无需人工介入", "人工准备清单": "\n".join(preparations) or "无"}


def write_preflight_report(path: Path, cases: list[dict[str, Any]], plans: list[dict[str, Any]], errors: list[str]) -> None:
    by_id = {text(plan.get("case_id")): plan for plan in plans}
    lines = ["# 自动化开始前的人工准备清单", "", "请先处理下面的人工事项：准备完成并留下确认记录。准备未完成时批次停止，不能跳过；必须人工执行的用例交给测试人员执行。", ""]
    for case in cases:
        plan = by_id.get(text(case.get("case_id")), {})
        if readiness(case).get("level") == "auto" and disposition(plan) == "run":
            continue
        columns = readiness_columns(case)
        decision = plan.get("readiness") or {}
        decision = decision if isinstance(decision, dict) else {}
        states = {text(item.get("id")): item for item in as_list(decision.get("preparations")) if isinstance(item, dict)}
        title = text(case.get("formal_case_title") or case.get("case_title") or case.get("title"))
        lines += [f"## {text(case.get('case_id'))} · {title}", "", f"- 执行级别：**{columns['执行级别']}**", f"- 原因和人工操作：{columns['人工介入说明']}", f"- 本轮安排：{ {'run': '准备确认后自动执行', 'manual': '交给人工执行'}.get(disposition(plan), '尚未决定，请先处理')}"]
        if text(decision.get("reason")):
            lines.append(f"- 安排原因：{text(decision.get('reason'))}")
        for item in as_list(readiness(case).get("preparations")):
            if isinstance(item, dict):
                confirmation = states.get(text(item.get("id")), {})
                state = "已确认准备好" if confirmation.get("status") == "ready" else "待人工准备"
                lines.append(f"- **{state}**：{text(item.get('description'))}；负责人：{text(item.get('owner'))}；验收条件：{text(item.get('acceptance'))}；验收记录：{text(confirmation.get('evidence')) or '未填写'}")
        lines.append("")
    lines += ["## 开始执行前还需处理", "", *([f"- {error}" for error in errors] or ["准备检查通过，可执行计划中的自动用例。"]), ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
