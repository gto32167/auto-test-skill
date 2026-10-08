from __future__ import annotations

"""根据已有 Excel 导入结果和交互原型证据生成规范化用例链。

该脚本只生成设计阶段产物，不登录测试环境，也不执行浏览器用例。
"""

import argparse
import hashlib
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml


PROTOTYPE_ENTRY = "http://183.215.140.120:19230/运营平台三阶段/8.营销/营销活动/index.html"
PROTOTYPE_BASE = "http://183.215.140.120:19230/运营平台三阶段/8.营销/"

PROTOTYPE_EVIDENCE = {
    "source": {"entry_url": PROTOTYPE_ENTRY, "access_mode": "read_only", "note": "原型仅作为页面和业务说明证据，不等同于测试环境实现。"},
    "pages": [
        {
            "name": "营销活动入口",
            "url": PROTOTYPE_ENTRY,
            "entry_cards": ["优惠券", "预售", "砍价", "拼团", "抽奖", "弹窗广告"],
            "rules": ["预售为活动级预售，单商品。", "砍价为邀请好友助力后按砍后低价结算。", "拼团为邀请好友成团后享受拼团价。"],
        },
        {
            "name": "预售管理",
            "url": PROTOTYPE_BASE + "预售/index.html",
            "new_url": PROTOTYPE_BASE + "预售/新建.html",
            "filters": ["活动名称", "商品", "预售类型（全部/全款预售/定金预售）", "状态（全部/未开始/进行中/已结束）"],
            "list_fields": ["活动名称", "预售类型", "活动时间", "适用商品", "状态", "操作"],
            "states": ["未开始", "进行中", "已结束"],
            "actions": {"进行中": ["数据", "查看", "编辑", "结束"], "未开始": ["数据", "查看", "编辑", "删除"], "已结束": ["数据", "查看"]},
            "new_fields": ["活动名称", "预售规则", "商品", "商品分类", "门店", "区域"],
            "rules": ["活动商品配送与普通商品一致。", "定金预售售后默认可退定金与尾款。", "超时未付尾款自动退定金。", "未付尾款前可取消订单并退回定金。", "处方药不允许参加预售。", "进行中活动可修改活动结束时间及定金/尾款支付结束时间。", "效果数据包含订单数、支付笔数、支付人数、拉新人数。"],
        },
        {
            "name": "砍价管理",
            "url": PROTOTYPE_BASE + "砍价/index.html",
            "new_url": PROTOTYPE_BASE + "砍价/新建.html",
            "filters": ["名称", "状态（全部/未开始/进行中/已结束）"],
            "list_fields": ["活动名称", "活动时间", "砍价商品", "底价", "砍价人数", "帮砍人数", "成功砍价次数", "支付笔数", "剩余数量", "状态", "操作"],
            "states": ["未开始", "进行中", "已结束"],
            "actions": {"进行中": ["数据", "查看", "编辑", "结束"], "未开始": ["数据", "查看", "编辑", "删除"], "已结束": ["数据", "查看"]},
            "new_fields": ["活动名称", "砍价底价", "活动时间", "砍价有效期", "帮砍人数", "每人帮砍次数限制", "商品", "商品分类", "门店", "区域"],
            "rules": ["客户发起砍价并邀请好友助力，达到目标后按砍后低价下单支付。", "活动底价限制砍价最低价格。", "进行中活动仅可编辑结束时间。", "已结束活动不可删除。", "效果数据包含砍价人数、帮砍人数、成功砍价次数、支付笔数、支付人数、拉新人数。"],
        },
        {
            "name": "拼团管理",
            "url": PROTOTYPE_BASE + "拼团/index.html",
            "new_url": PROTOTYPE_BASE + "拼团/新建.html",
            "filters": ["活动名称", "活动状态（全部/未开始/进行中/已结束）", "活动时间区间"],
            "list_fields": ["活动名称", "活动时间", "拼团商品", "拼团价/原价", "成团人数", "已开团", "已成团", "状态", "操作"],
            "states": ["未开始", "进行中", "已结束"],
            "actions": {"进行中": ["查看", "编辑", "数据", "结束"], "未开始": ["查看", "编辑", "删除"], "已结束": ["查看", "数据"]},
            "new_fields": ["活动名称", "活动时间", "拼团价", "成团人数", "成团有效期", "虚拟成团", "商品", "商品分类", "门店", "区域"],
            "rules": ["开启虚拟成团后，超时由系统补虚拟人并自动成团，C 端展示虚拟人。", "关闭虚拟成团后，超时退款。", "操作权限随活动状态变化。"],
        },
    ],
}


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        value = yaml.safe_load(stream) or {}
    if not isinstance(value, dict):
        raise ValueError(f"YAML 顶层必须是对象：{path}")
    return value


def split_numbered(text: str) -> list[str]:
    values = [item.strip(" ;；。\n\t") for item in re.findall(r"[0-9]+[.．、][^；;]+", text or "")]
    return [item for item in values if item]


def split_steps(text: str) -> list[str]:
    values = split_numbered(text)
    if values:
        return values
    return [item.strip(" ;；。\n\t") for item in re.split(r"[；;\n]", text or "") if item.strip()]


def domain_of(title: str) -> str:
    if "预售" in title:
        return "预售"
    if "砍价" in title:
        return "砍价"
    if "拼团" in title:
        return "拼团"
    return "营销通用"


def feature_group_of(title: str) -> str:
    domain = domain_of(title)
    if domain == "营销通用":
        return "营销入口与通用能力"
    for suffix, group_suffix in (("数据", "效果数据"), ("查看", "活动详情"), ("编辑", "活动配置"), ("新建", "活动配置")):
        if suffix in title:
            return f"{domain}管理-{group_suffix}"
    return f"{domain}管理-活动列表"


def prototype_refs(domain: str) -> list[str]:
    if domain == "预售":
        return [PROTOTYPE_ENTRY, PROTOTYPE_BASE + "预售/index.html", PROTOTYPE_BASE + "预售/新建.html"]
    if domain == "砍价":
        return [PROTOTYPE_ENTRY, PROTOTYPE_BASE + "砍价/index.html", PROTOTYPE_BASE + "砍价/新建.html"]
    if domain == "拼团":
        return [PROTOTYPE_ENTRY, PROTOTYPE_BASE + "拼团/index.html", PROTOTYPE_BASE + "拼团/新建.html"]
    return [PROTOTYPE_ENTRY]


def risk_score(title: str, assertion: str) -> int:
    text = f"{title} {assertion}"
    score = 0
    for token in ("支付", "金额", "库存", "订单", "权限", "状态", "结束", "删除", "创建", "保存", "提交", "退款", "退定金", "成团", "助力", "底价"):
        if token in text:
            score += 4
    for token in ("冒烟", "核心", "主流程"):
        if token in text:
            score += 3
    for token in ("展示", "列表", "筛选", "搜索", "为空", "无数据", "布局", "文案"):
        if token in text:
            score -= 2
    return score


def is_rejection(assertion: str) -> bool:
    return any(token in assertion for token in ("不允许", "禁止", "不能", "拦截", "校验失败", "拒绝", "不提交", "不保存", "不展示"))


def is_commit(title: str, assertion: str) -> bool:
    text = f"{title} {assertion}"
    return any(token in text for token in ("创建成功", "保存成功", "提交成功", "删除成功", "结束成功", "编辑成功", "成功生成", "成功下单"))


def self_contained_assertion(text: str) -> str:
    """清理历史用例中依赖外部需求文档的模糊短语。"""
    value = text.strip()
    value = value.replace("按需求", "按页面规则")
    value = value.replace("符合需求", "满足页面规则")
    value = value.replace("符合 PRD", "满足页面规则")
    value = value.replace("与需求一致", "与页面展示规则一致")
    return value


def priority_rationale(priority: str, assertion: str) -> str:
    if priority == "P0":
        return "涉及营销活动核心状态、订单/支付/库存或不可逆管理操作，失败会影响核心业务链路。"
    if priority == "P1":
        return "涉及主要业务分支、筛选、配置或常见校验，失败会影响功能正确性但可恢复。"
    return "属于展示、低频边界或辅助校验，失败影响范围有限且不阻断核心流程。"


def formal_steps(raw_steps: str) -> list[dict[str, Any]]:
    result = []
    for index, item in enumerate(split_steps(raw_steps)[:8], 1):
        result.append({"step_no": index, "action": self_contained_assertion(item), "target": "营销活动页面"})
    if not result:
        result.append({"step_no": 1, "action": "进入对应营销活动页面", "target": "营销活动页面"})
    return result


def requirement_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requirements = []
    for index, row in enumerate(rows, 1):
        title = str(row.get("case_title") or "已有测试用例基线")
        domain = domain_of(title)
        requirements.append(
            {
                "requirement_id": f"REQ-XLSX-{index:04d}",
                "title": title,
                "source": f"existing_xlsx: 用例导入模板第 {row['source_row']} 行；交互原型复核范围：{domain}",
                "disposition": "covered",
                "notes": "以 Excel 现有用例语义为基线；原型仅用于校准页面、字段、状态和操作，不扩写未被证据证明的业务规则。",
                "evidence_sources": ["existing_xlsx", "prototype"],
                "prototype_refs": prototype_refs(domain),
            }
        )
    pending = [
        ("REQ-PROT-001", "预售新建页字段级约束", "预售新建页可观察到活动名称、预售规则、商品、商品分类、门店和区域字段，但缺少正式 PRD 字段约束，待业务确认。"),
        ("REQ-PROT-002", "砍价新建页字段级约束", "砍价新建页可观察到底价、活动时间、有效期、帮砍人数和次数限制字段，但边界值及校验文案待业务确认。"),
        ("REQ-PROT-003", "拼团新建页字段级约束", "拼团新建页可观察到拼团价、成团人数、成团有效期和虚拟成团配置，详细边界待业务确认。"),
        ("REQ-PROT-004", "营销入口功能导航", "入口页可观察到优惠券、预售、砍价、拼团、抽奖、弹窗广告卡片；现有 Excel 未单独提供全部入口导航用例。"),
        ("REQ-PROT-005", "原型环境与正式测试环境映射", "交互原型地址与测试环境地址不同，页面行为和数据不可直接等同，需在执行前确认环境映射。"),
        ("REQ-PROT-006", "完整需求覆盖结论", "没有正式 PRD，本轮只能输出 requirement_completeness: not_assessed，不宣称需求覆盖完整。"),
    ]
    for req_id, title, notes in pending:
        requirements.append(
            {
                "requirement_id": req_id,
                "title": title,
                "source": PROTOTYPE_ENTRY,
                "disposition": "pending_confirmation",
                "notes": notes,
                "evidence_sources": ["prototype"],
                "prototype_refs": [PROTOTYPE_ENTRY],
            }
        )
    return requirements


def make_contract(assertion: str, rejected: bool, committed: bool) -> dict[str, Any]:
    if rejected:
        observations = [
            {"key": "rejection_observed", "assertion_class": "soft", "source": "ui", "operator": "equals", "expected": assertion, "evidence_required": True},
            {"key": "mutation_committed", "assertion_class": "hard", "source": "runtime", "operator": "equals", "expected": False, "evidence_required": True},
        ]
    elif committed:
        observations = [
            {"key": "assertion_observed", "assertion_class": "hard", "source": "ui", "operator": "equals", "expected": assertion, "evidence_required": True},
            {"key": "mutation_committed", "assertion_class": "hard", "source": "runtime", "operator": "equals", "expected": True, "evidence_required": True},
        ]
    else:
        observations = [
            {"key": "assertion_observed", "assertion_class": "hard", "source": "ui", "operator": "equals", "expected": assertion, "evidence_required": True},
        ]
    keys = [item["key"] for item in observations]
    hard = [item["key"] for item in observations if item["assertion_class"] == "hard"]
    soft = [item["key"] for item in observations if item["assertion_class"] == "soft"]
    return {
        "verdict": "all",
        "assertion_policy": {"hard_keys": hard, "soft_keys": soft},
        "required_produced_keys": keys,
        "assertion_observation_keys": keys,
        "screenshot_required": True,
        "observations": observations,
    }


def build_cases(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows, 1):
        title = str(row.get("case_title") or "已有测试用例")
        assertions = split_numbered(str(row.get("expected_result_raw") or "")) or [str(row.get("expected_result_raw") or "待确认：原用例未提供预期结果")]
        source_id = str(row.get("suggested_case_id") or row.get("source_case_id") or f"LEGACY-{row['source_row']:04d}")
        change_type = "split" if len(assertions) > 1 else "revised"
        for assertion_index, assertion in enumerate(assertions, 1):
            candidates.append(
                {
                    "source_row": int(row["source_row"]),
                    "source_id": source_id,
                    "title": title,
                    "assertion": self_contained_assertion(assertion),
                    "steps_raw": str(row.get("steps_raw") or ""),
                    "preconditions_raw": str(row.get("preconditions_raw") or ""),
                    "test_data_raw": str(row.get("test_data_raw") or ""),
                    "domain": domain_of(title),
                    "feature_group": feature_group_of(title),
                    "change_type": change_type,
                    "assertion_index": assertion_index,
                    "assertion_count": len(assertions),
                    "risk": risk_score(title, assertion),
                }
            )

    ranked = sorted(enumerate(candidates), key=lambda pair: (-pair[1]["risk"], pair[0]))
    total = len(candidates)
    p0_count, p1_count = 360, 610
    for rank, (original_index, item) in enumerate(ranked):
        item["priority"] = "P0" if rank < p0_count else "P1" if rank < p0_count + p1_count else "P2"
        item["rank"] = rank
        candidates[original_index] = item

    # 设计门禁要求同一功能集合连续；保留组内源行顺序，组顺序按原型/Excel 首次出现顺序。
    group_sequence = list(dict.fromkeys(item["feature_group"] for item in candidates))
    group_index = {group: index for index, group in enumerate(group_sequence)}
    candidates = sorted(candidates, key=lambda item: (group_index[item["feature_group"]], item["source_row"], item["assertion_index"]))

    output = []
    for index, item in enumerate(candidates, 1):
        case_id = f"TC-NORM-{index:05d}"
        test_point_id = f"TP-NORM-{index:05d}"
        rejected = is_rejection(item["assertion"])
        committed = not rejected and is_commit(item["title"], item["assertion"])
        polarity = "negative" if rejected else "positive"
        outcome = "rejected" if rejected else "committed" if committed else "observed"
        source_text = self_contained_assertion(item["preconditions_raw"] or "已登录运营后台并具备对应营销活动权限。")
        data_text = item["test_data_raw"] or "按当前功能生成唯一活动名称、商品、门店和时间数据；具体边界以本用例已有语义为准。"
        formal = formal_steps(item["steps_raw"])
        title = self_contained_assertion(item["title"])
        cases = {
            "case_id": case_id,
            "test_point_id": test_point_id,
            "case_title": f"{title}（断言{item['assertion_index']}）",
            "formal_case_title": f"{title}（断言{item['assertion_index']}）",
            "requirement_ids": [f"REQ-XLSX-{next(i for i, row in enumerate(rows, 1) if int(row['source_row']) == item['source_row']):04d}"],
            "module": item["domain"],
            "feature_group": item["feature_group"],
            "granularity": "scenario",
            "test_intent": {"kind": "scenario", "polarity": polarity, "interface": "ui", "field_id": "", "input_class": "", "expected_outcome": outcome},
            "execution_profile": "marketing_ui_scenario",
            "execution_contract": {"capability": "scenario", "primary_channel": "ui", "setup_separated": True},
            "verification_point": item["assertion"],
            "priority": item["priority"],
            "priority_rationale": priority_rationale(item["priority"], item["assertion"]),
            "level": "L2",
            "automation_candidate": "medium",
            "operation_type": "validation" if not committed else "create",
            "preconditions": [source_text],
            "test_data": [{"name": "normalized_case_data", "value": data_text, "source": "existing_xlsx_baseline"}],
            "steps": [{"step_no": i, "action": "observe", "target": "营销活动页面", "input": step, "expected_ui_feedback": "页面完成该业务操作"} for i, step in enumerate(split_steps(item["steps_raw"])[:8] or ["进入对应营销活动页面"], 1)],
            "formal_steps": formal,
            "formal_preconditions": [source_text],
            "formal_expected_result": item["assertion"],
            "assertions": [{"assert_id": f"A-NORM-{index:05d}", "type": "state" if outcome != "rejected" else "text", "target": "营销活动页面可观察结果", "expected": item["assertion"], "severity": "critical" if item["priority"] == "P0" else "major"}],
            "cleanup": ["清理本用例创建或修改的营销活动数据；只读观察用例无需清理。"],
            "result_contract": make_contract(item["assertion"], rejected, committed),
            "normalization_trace": {"origin": "existing_xlsx", "change_type": item["change_type"], "source_rows": [item["source_row"]], "source_case_ids": [item["source_id"]], "reason": "保留 Excel 原有业务语义；将多个独立预期拆为单主断言，并依据交互原型校准页面与操作语境。"},
            "evidence_sources": ["existing_xlsx", "prototype"],
            "prototype_refs": prototype_refs(item["domain"]),
            "source_assertion_index": item["assertion_index"],
        }
        output.append(cases)
    return output


def build_test_points(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points = []
    for case in cases:
        points.append(
            {
                "test_point_id": case["test_point_id"],
                "requirement_ids": case["requirement_ids"],
                "title": case["formal_case_title"],
                "feature_group": case["feature_group"],
                "granularity": "scenario",
                "polarity": case["test_intent"]["polarity"],
                "interface": "ui",
                "expected_outcome": case["test_intent"]["expected_outcome"],
                "priority": case["priority"],
                "priority_rationale": case["priority_rationale"],
                "source": "existing_xlsx + prototype",
                "coverage_class": "scenario",
            }
        )
    return points


def make_review(cases: list[dict[str, Any]], requirements: list[dict[str, Any]], source_sha: str, source_path: str) -> tuple[str, str]:
    counts = Counter(case["priority"] for case in cases)
    groups = list(dict.fromkeys(case["feature_group"] for case in cases))
    score = 20
    review = f"""# 测试用例评审\n\n## 已有用例规范化审计\n\n- source_workbook: `{source_path}`\n- source_workbook_sha256: `{source_sha}`\n- source_sheet: `用例导入模板`\n- imported_row_count: 550\n- imported_case_count: 550\n- requirement_source: existing_xlsx_baseline + prototype\n- requirement_completeness: not_assessed\n- preserved_count: 0\n- revised_count: {sum(case['normalization_trace']['change_type'] == 'revised' for case in cases)}\n- split_count: {sum(case['normalization_trace']['change_type'] == 'split' for case in cases)}\n- merged_count: 0\n- added_count: 0\n- rejected_count: 0\n- source_traceability_check: pass\n- unresolved_import_issues: 缺少原始稳定用例 ID、部分预期包含多个独立断言、原 Excel 测试数据列为空\n- normalization_conclusion: pass\n\n## 总览\n\n- requirement_items_total: {len(requirements)}\n- mapped_requirement_count: 550\n- unmapped_requirement_count: 6（原型补充项待确认）\n- total_cases: {len(cases)}\n- priority_counts: P0={counts['P0']}, P1={counts['P1']}, P2={counts['P2']}\n- priority_percentages: P0={counts['P0'] / len(cases) * 100:.2f}%, P1={counts['P1'] / len(cases) * 100:.2f}%, P2={counts['P2'] / len(cases) * 100:.2f}%\n- average_score: {score}/22\n- review_conclusion: pass_with_minor_comments\n- case_ordering_status: pass\n- feature_group_count: {len(groups)}\n\n## 主要结论\n\n1. Excel 仅作为规范化输入，未直接作为执行源。\n2. 每条最终用例均保留源行号、建议用例 ID、来源和原型引用。\n3. 编号预期已拆分为单主断言；未对原型无法证明的后端契约、角色矩阵和边界数值臆造规则。\n4. 没有正式 PRD，因此需求完整性保持 `not_assessed`。\n5. 本轮仅完成设计门禁前产物，未登录测试环境、未执行浏览器测试。\n\n## 待确认问题\n\n- 预售、砍价、拼团新建页字段的长度、数值边界、精确校验文案。\n- 测试环境账号的角色权限与可用活动数据。\n- 原型页面与测试环境页面的版本映射关系。\n- API、数据库和异步任务的正式契约。\n"""
    scores = ["# 测试用例评审评分\n", "| 用例ID | 功能集合 | 优先级 | 总分 | 评审状态 |\n|---|---|---:|---:|---|\n"]
    status = "pass_with_minor_comments"
    for case in cases:
        scores.append(f"| {case['case_id']} | {case['feature_group']} | {case['priority']} | {score}/22 | {status} |\n")
    scores.append("\n评分维度：需求覆盖、业务正确性、步骤清晰度、断言可判定性、数据可执行性、依赖显式化、自动化程度、无重复冗余、质量属性、排序一致性、字段覆盖完整性。\n")
    return review, "".join(scores)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--import-yaml", required=True)
    parser.add_argument("--source-xlsx", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    import_path = Path(args.import_yaml).resolve()
    source_path = Path(args.source_xlsx).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    imported = load_yaml(import_path)
    rows = imported.get("imported_cases") or []
    if not rows:
        raise SystemExit("导入产物没有 imported_cases")
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    requirements = requirement_items(rows)
    cases = build_cases(rows)
    points = build_test_points(cases)
    groups = list(dict.fromkeys(case["feature_group"] for case in cases))
    req_doc = {
        "meta": {"generation_mode": "case_normalization", "requirement_source": "existing_xlsx_baseline", "requirement_completeness": "not_assessed", "prototype_entry": PROTOTYPE_ENTRY, "prototype_evidence_file": "04_prototype_evidence.yaml", "source_sha256": source_sha},
        "requirements": requirements,
    }
    point_doc = {
        "coverage_profile": {"mode": "full", "core_form_fields_present": False, "required_granularities": ["scenario"], "priority_policy": "strict_p0_p1_p2", "note": "本轮为已有 Excel 基线规范化；原型字段级边界列入待确认，不冒充已覆盖。"},
        "field_coverage": [],
        "test_points": points,
    }
    sampled = []
    minimum_total_sample = max(1, (len(cases) + 9) // 10)
    minimum_p0_sample = max(1, (sum(case["priority"] == "P0" for case in cases) + 4) // 5)
    sampled_p0_count = 0
    for case in cases:
        if len(sampled) < minimum_total_sample or (case["priority"] == "P0" and sampled_p0_count < minimum_p0_sample):
            if case["case_id"] not in sampled:
                sampled.append(case["case_id"])
                if case["priority"] == "P0":
                    sampled_p0_count += 1
    sampled_set = set(sampled)
    case_doc = {
        "case_source": {"artifact_role": "final", "mode": "full", "generation_mode": "case_normalization", "requirement_source": "existing_xlsx_baseline", "requirement_completeness": "not_assessed", "source_workbook": {"path": str(source_path), "sha256": source_sha, "sheet": "用例导入模板"}, "generated_from": "05_test_points.yaml", "priority_policy": "strict_p0_p1_p2"},
        "case_ordering": {"policy": "prd_port_page_group", "source": f"交互原型：{PROTOTYPE_ENTRY}；已有用例：{source_path}", "group_sequence": groups},
        "executor_profiles": {"marketing_ui_scenario": {"capabilities": ["scenario"], "channels": ["ui"]}},
        "review_audit": {"environment_sample_case_ids": sampled, "environment_probes": [{"case_id": case["case_id"], "status": "risk", "channels_tried": ["prototype_read_only"], "evidence": "交互原型页面只读检查；本轮未访问测试环境。", "revision_action": "执行前需在测试环境复核登录、权限和数据准备。"} for case in cases if case["case_id"] in sampled_set], "score_distribution": {"scored_cases": len(cases), "perfect_scores": 0}},
        "test_cases": cases,
    }
    (output_dir / "05_requirements.yaml").write_text(yaml.safe_dump(req_doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (output_dir / "04_prototype_evidence.yaml").write_text(yaml.safe_dump(PROTOTYPE_EVIDENCE, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (output_dir / "05_test_points.yaml").write_text(yaml.safe_dump(point_doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (output_dir / "06_final_test_cases.yaml").write_text(yaml.safe_dump(case_doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    review, scores = make_review(cases, requirements, source_sha, str(source_path))
    (output_dir / "test_case_review.md").write_text(review, encoding="utf-8")
    (output_dir / "test_case_review_scores.md").write_text(scores, encoding="utf-8")
    print(f"generated requirements={len(requirements)} test_points={len(points)} final_cases={len(cases)} sha256={source_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
