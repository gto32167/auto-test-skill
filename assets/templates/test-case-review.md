# Test Case Review（用例评审评分）

> 评审标准：references/review-standard.md —— 11 维度逐条评分（每维度 0-2 分，满分 22）
> 阈值：18-22 通过 / 14-17 小改后通过 / 9-13 必须修订 / 0-8 驳回重写
> 本文件为固定交付物：每条用例的 11 维度评分 + 维度汇总 + 一票否决检查

## 0. Existing Case Normalization Audit（已有用例规范化专项）

- source_workbook:
- source_workbook_sha256:
- source_sheet:
- imported_row_count:
- imported_case_count:
- skipped_rows:
- requirement_source: PRD | existing_xlsx_baseline
- requirement_completeness: assessed | not_assessed
- preserved_count:
- revised_count:
- split_count:
- merged_count:
- added_count:
- rejected_count:
- source_traceability_check: pass | fail
- semantic_change_records:
  - source_row:
    source_case_id:
    normalized_case_ids: []
    change_type: revise | split | merge | add | reject
    before:
    after:
    reason:
- unresolved_import_issues: []
- normalization_conclusion: pass | revise_required | reject

## 1. Overall Summary（总览）

- review_scope:
- requirement_items_total:
- mapped_requirement_count:
- unmapped_requirement_count:
- total_cases:
- passed（≥18）:
- pass_with_minor_comments（14-17）:
- revise_required（9-13）:
- rejected（0-8）:
- average_score:
- review_conclusion: pass | pass_with_minor_comments | revise_required | reject
- priority_policy_status: pass | fail
- priority_counts: {P0: 0, P1: 0, P2: 0}
- priority_percentages: {P0: 0.00%, P1: 0.00%, P2: 0.00%}
- case_ordering_status: pass | fail
- feature_group_count:
- common_issues:

## 2. 11 维度说明（评分口径）

| # | 维度 | 0 分（明显不满足） | 1 分（基本满足有缺口） | 2 分（满足且清晰） |
|---|------|--------------------|------------------------|--------------------|
| 1 | 需求覆盖 | 无需求 ID / 明显漏测 | 覆盖主流程但缺分支 | 全部需求条目有去向 |
| 2 | 业务正确性 | 前置/步骤/预期违反业务 | 基本正确有细节偏差 | 与 PRD 完全一致 |
| 3 | 步骤清晰度 | 有模糊词/多动作混合 | 基本原子化 | 完全原子化可一致执行 |
| 4 | 断言可判定性 | 主观判断词/无结果契约 | 可观察但弱断言 | 强断言 + 可求值 observations + 副作用验证 |
| 5 | 数据可执行性 | 数据不可得 | 数据可得但可污染 | 唯一标识自造数据幂等 |
| 6 | 依赖与风险显式化 | 外部依赖未说明 | 部分标注 | 环境/账号/歧义全显式 |
| 7 | 可自动化程度 | 无法定位/能力错路由 | 部分可脚本化 | profile 能力匹配，UI 核心动作与 API 辅助分层 |
| 8 | 无重复冗余 | 一用例多验证点 | 轻度重合 | 单用例单验证点 |
| 9 | 兼容性与质量属性 | 完全未考虑 | 有兼容冒烟 | 兼容/性能/安全有最小覆盖 |
| 10 | 排序一致性 | 缺功能集合/集合被拆散/顺序不可追溯 | 基本跟随 PRD 且大体聚合 | 按 PRD→端口→页面/相似功能集合排列，同组连续且 Excel/YAML 一致 |
| 11 | 字段覆盖完整性 | 表单类未展开字段 | 部分字段 | 字段级全覆盖（有效/无效/必填/边界） |

## 3. Per Case Review（逐条评分）

- case_id:
  - title:
  - feature_group:
  - requirement_ids:
  - priority: P0 | P1 | P2
  - priority_rationale:
  - review_status: pass | pass_with_minor_comments | revise_required | reject
  - score: /22
  - dimension_scores:
      requirement_coverage: 2
      business_correctness: 2
      step_clarity: 2
      assertability: 2
      data_executability: 2
      dependency_visibility: 2
      automation_readiness: 2
      no_redundancy: 2
      quality_attribute_coverage: 2
      ordering_consistency: 2
      field_coverage: 2
  - findings:
      - ""
  - missing_coverage:
      - ""
  - revision_actions:
      - ""

## 4. Dimension Summary（维度汇总）

| 维度 | 平均分 | 得分 <2 的用例数 | 主要缺口 |
|------|--------|------------------|----------|
| 1. 需求覆盖 |  |  |  |
| 2. 业务正确性 |  |  |  |
| 3. 步骤清晰度 |  |  |  |
| 4. 断言可判定性 |  |  |  |
| 5. 数据可执行性 |  |  |  |
| 6. 依赖与风险显式化 |  |  |  |
| 7. 可自动化程度 |  |  |  |
| 8. 无重复冗余 |  |  |  |
| 9. 兼容性与质量属性 |  |  |  |
| 10. 排序一致性 |  |  |  |
| 11. 字段覆盖完整性 |  |  |  |

## 5. 一票否决检查（否决项 → 直接 reject / revise_required）

| 否决项 | 是否触发 | 涉及用例 |
|--------|----------|----------|
| 无需求来源 | 否 | — |
| 未完成需求文档逐条回比 | 否 | — |
| 存在未映射需求条目且未说明 | 否 | — |
| 无明确预期结果 | 否 | — |
| 步骤缺少关键动作 | 否 | — |
| 使用大量主观判断词 | 否 | — |
| 测试数据不可得 | 否 | — |
| 前置条件无法验证 | 否 | — |
| 依赖外部条件但未说明 | 否 | — |
| 测试意图与 execution profile 能力不匹配 | 否 | — |
| 拒绝类用例缺 mutation_committed=false 契约 | 否 | — |
| 优先级不是 P0/P1/P2 或缺少判级理由 | 否 | — |
| 测试点与最终用例优先级/理由不一致 | 否 | — |
| P0/P1/P2 任一缺失或占比越界 | 否 | — |
| priority_policy.status 不是 pass | 否 | — |
| 缺功能集合、同组不连续、声明顺序与实际顺序不一致 | 否 | — |
| 缺少硬/软断言分类，或拒绝类 mutation_committed 不是硬断言 | 否 | — |
| 环境可执行性抽样不足（总用例 <10% 或 P0 <20%） | 否 | — |
| 全部用例均为 22/22 但未提供逐项证据和满分依据 | 否 | — |

## 6. Priority Portfolio Review（优先级组合专项评审）

| 等级 | 定义 | 要求占比 | 允许数量 | 实际数量 | 实际占比 | 状态 |
|---|---|---:|---:|---:|---:|---|
| P0 | 核心链路、资金/库存/权限安全、关键状态 | 20%~25% |  |  |  |  |
| P1 | 主要分支、重要校验、常见异常 | 35%~40% |  |  |  |  |
| P2 | 低频异常、次要边界、展示/兼容/低影响验证 | 35%~40% |  |  |  |  |

- invalid_priority_case_ids:
- missing_priority_rationale_case_ids:
- test_point_case_priority_mismatch:
- distribution_revision_actions:

## 7. Feature Group Ordering Audit（功能集合与排序专项）

- ordering_policy: prd_port_page_group
- ordering_source:
- declared_group_sequence:
- actual_group_sequence:
- test_point_case_order_match: pass | fail
- xlsx_yaml_order_match: pass | fail

| 顺序 | 功能集合 | 端口/页面依据 | 用例ID范围 | 是否连续 | 状态 |
|---:|---|---|---|---|---|
|  |  |  |  |  |  |

## 8. Revision Priority（修订优先级）

- priority: "high"
  - case_ids:
  - action: ""
- priority: "medium"
  - case_ids:
  - action: ""
- priority: "low"
  - case_ids:
  - action: ""

## 9. Environment Executability Audit（环境可执行性抽样）

- minimum_total_sample: 总用例数向上取整 10%
- minimum_p0_sample: P0 用例数向上取整 20%
- sampled_case_ids:

| 用例ID | 优先级 | 探测通道 | 状态（executable/risk/blocked） | 证据 | 修订动作 |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

## 10. Assertion Policy Audit（断言策略专项）

| 用例ID | 硬断言键 | 软断言键 | 精确提示文案是否有 PRD 原文依据 | 拒绝类无写入硬断言 | 状态 |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

- scored_cases:
- perfect_scores:
- perfect_score_justification: 仅当全部用例 22/22 时必填，必须引用逐项评审证据，不能只写“已检查”。
