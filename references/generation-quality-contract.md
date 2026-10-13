# 生成与评审质量契约

本契约适用于 `generation_review` 和 `full` 模式的需求拆解、用例生成与评审。它解决两个常见问题：用例数量看似很多但没有需求依据，以及结构门禁通过但用例实际上重复或不可读。

## 开关声明（开工第一步）

整份契约只有**一个**版本号开关，写在 `05_requirements.yaml` 顶层：

```yaml
quality_contract_version: "1.0"
```

`06_final_test_cases.yaml` 的 `case_source.quality_contract_version` 必须同样声明 `"1.0"`。

开关**只有这两处是权威位置**（需求账本顶层 + 用例 `case_source`）。写在别处（例如 `coverage_profile`）不会被读取，门禁会告警点名 —— 历史版本里正是"声明了但没人读"导致整份契约空转。

规则如下：

- `quality_contract_version` 是**规范字段名**；`case_count_contract_version` 是历史别名。两者声明任意一个都会启用同一套契约，新项目只写规范名即可。
- 两个字段同时出现且版本不一致 → 直接失败（不允许半启用）。
- `full` / `generation_review` 交付**未声明版本号 = 门禁失败**，错误文案为 "must declare quality_contract_version"；只有显式 `l2_only` 才豁免。缺失契约不会再被静默跳过。
- 已有存量产物（契约出现之前生成的）用 `--legacy-audit` 审计：门禁只跑结构校验，同时输出"合规差距报告"（哪类字段缺多少条、影响哪些部分）。该模式会在 `quality_contract.enforced = false` 和警告里留痕，**不能当作契约合规**。补齐后必须去掉该参数重新门禁。存量迁移可用 `references/scripts/backfill_quality_fields.py` 先自动派生 `semantic_identity` / `title_contract` / `precondition_state` / `normative_statements`，再人工补差。
- 只要存在 `covered` 原子规则，门禁算出的 `N_min = 0` 直接判失败，不能当作"下限很低所以达标"。
- 错误很多时，控制台只打印按类别聚合的摘要（类别 / 命中数 / 涉及用例数 / 样例），完整明细写入 `--error-report <path>`；加 `--verbose-errors` 可强制打印全部原始错误。

## 1. 生成前先锁定口径

生成开始前必须输出一份 `generation_scope`，并写入 `05_requirements.yaml`：

```yaml
generation_scope:
  mode: generation_review       # generation_review / full / l2_only
  granularity: l2_plus_l3       # l2_plus_l3 / l2_only
  coverage_depth: standard      # standard / exhaustive
  source_policy: current_prd_only
  historical_case_reuse: forbidden
  estimated_case_range: {min: 0, max: 0}
  confirmed: true
  confirmed_by: "张三（产品负责人）"   # 谁确认的口径，必须留痕
  confirmed_at: "2026-09-13"          # 何时确认，ISO 8601 日期或时间
```

`confirmed: true` 但没有 `confirmed_by` / `confirmed_at` 视为未确认 —— 布尔值无法追溯责任人和时间点。

`full` 和 `generation_review` 必须使用 `l2_plus_l3`，并启用数量契约；只有明确的 `l2_only` 才可以不生成字段级用例。`estimated_case_range` 是预估值，不得替代门禁计算出的实际下限。门禁会把该区间与独立算出的 `N_min` 做交叉校验：区间上限低于 `N_min`、或最终用例数落在区间之外，都会告警并要求在评审中解释差异来源（这正是"预估 5758 / 实际 1465"那类偏差的前置拦截点）。实际用例数较上一次结果变化超过 20% 时，必须在评审中解释变化来源。

生成源只能是本轮用户指定的 PRD、项目背景和用户明确指令。历史用例、历史 YAML、其他项目的需求摘要只能作为用户明确授权的对照资料，不能作为需求账本、原子规则或最终用例的生成基底。

## 2. 规范性条款账本

需求账本必须保留 PRD 中每一条可验证的规范性陈述，不能只保留功能摘要：

```yaml
normative_statements:
  - statement_id: NS-001
    source_document: PRD-001
    source_locator: {section: "产品方案细节/3.1", line: "L00123"}
    source_excerpt: "原文中的完整规范性句子"
    statement_type: behavior       # behavior / field / state / data / message / nonfunctional
    disposition: covered           # covered / explicitly_not_tested / pending_confirmation
    atomic_rule_ids: [RULE-0001]
```

`source_coverage_inventory` 必须与账本自动对账：规范性条款总数、已映射数、排除数、待确认数和未映射 ID 均由门禁重算。`covered` 条款必须至少关联一个原子规则；排除或待确认条款必须写明原因，不能通过删除条款逃避覆盖。

8 份或更多按时间递进的 PRD 还必须记录版本差异：

```yaml
version_differences:
  - comparison_id: VD-001
    from_document: PRD-001
    to_document: PRD-002
    change_type: modified       # added / modified / superseded / deprecated / unchanged / ambiguous
    subject: "受影响的需求主题"
    affected_requirement_ids: [REQ-001]
    resolution_status: resolved # resolved / pending_confirmation / not_applicable
    resolution: "如何处理旧版本与新版本关系"
```

每一对相邻版本都必须有比较记录。`ambiguous` 或 `pending_confirmation` 不得静默变成 `covered`，必须进入待确认清单并影响评审结论。

## 3. 用例数计算契约

对每条 `covered` 原子规则 `R_i`，门禁独立依据 PRD 事实计算覆盖单元 `U_i`：

```text
N_min = Σ |U_i|
N_final = N_min + N_extra
```

`N_extra` 只能用于额外风险验证，每条必须声明 `extra_case_reason` 和对应风险，不得用于重复同一验证点。每个 `U_i` 必须一对一落地为 coverage obligation、test point 和 final case，并共享稳定的 `coverage_unit_signature`。

`N_min` 由门禁独立重算，不接受产物自报。**只要存在 `covered` 原子规则，`N_min = 0` 就是契约失败**（"calculated_minimum_cases is 0 while covered atomic rules exist"）：它意味着门禁无法从声明的 PRD 事实推导出任何覆盖单元，而不是"下限很低所以轻松达标"。这种情况必须补齐 `coverage_characteristics`（profile + applicability + 该 profile 的 PRD 事实），不能靠调低预估区间绕过。

`coverage_characteristics.units` 不能覆盖自动计算。若填写，必须与门禁根据 profile 和 PRD 特征计算出的完整签名集合逐项相等；多写、少写或只有自定义签名均失败。每条规则还必须提供：

- `source_fact_refs`：指向规范性条款或原文定位；
- `applicability_reason`：说明每个启用维度为何适用；
- `unsupported_dimensions`：明确说明没有生成的维度及原因。

覆盖维度只由 PRD 明确事实启用，不得因关键词套模板。未声明长度、范围、选项、角色、端口、文件格式、数量阈值、时间阈值或公式时，禁止擅自生成最大值、超限、权限矩阵、跨端、空文件、部分失败、零值、舍入等测试。

## 4. 语义适用性与重复

每个最终用例必须声明 `semantic_identity`：

```yaml
semantic_identity:
  port: "PC商城"
  page: "订单列表"
  object: "订单开票入口"
  condition: "订单状态为待发货"
  action: "查看入口"
  assertion: "入口可见"
```

门禁按 `port + page + object + condition + action + assertion` 归一化后检查重复。标题、步骤或来源不同不能掩盖相同测试目标；若确实是不同端、不同角色或不同数据分支，必须体现在身份字段中。覆盖单元不同但语义身份相同仍需合并或写明保留理由。

`profile`、`rule_type`、`coverage_dimension` 必须互相适用：导出不能自动产生上传校验，时间不能自动产生金额边界，展示不能自动产生状态流转，没有角色声明不能产生权限矩阵。

## 5. 标题和步骤可读性

标题必须让执行人员不看步骤也能知道测试对象和条件，至少具备 `object`、`action`，有分支时还要具备 `condition`。禁止只写“正常验证”“按规则处理”“同样处理”“符合预期”“功能正确”等弱标题。

正式步骤按“进入对象 → 构造条件/输入 → 执行动作 → 检查唯一主断言”组织。每步只包含一个动作，目标和输入明确；超过 5 步应优先拆分。预期结果必须是可直接观察和判定的结果，不能要求读者另查 PRD 才知道通过条件。

每条用例还必须声明 `precondition_state`：`self_built`、`system_preinstalled` 或 `environment_provided`。自造前置必须有独立的 `data_setup`，系统预置/环境提供必须写来源，并声明数据缺失时 `blocked`。正式步骤只执行被测业务动作，不把造数命令混入步骤。

## 6. 独立评审

生成器不能同时作为唯一评审器。评审必须声明：

```yaml
review_audit:
  reviewer_independence:
    mode: independent
    source_artifacts: [current_prd, normative_statements]
    generated_cases_used_as_scope_source: false
    semantic_duplicate_review: completed
  execution_scope: generation_review
```

`generation_review` 模式下，环境可执行性抽样为 `not_applicable`，必须写明尚未进入执行阶段；不得伪造 UI/API 探测证据。`full` 模式仍要求总用例 10% 和 P0 20% 的真实环境抽样。

逐条评分必须依据 11 个维度填写证据。自动化候选等级、标题唯一或门禁通过都不能直接换算为 20/22 或 22/22。全部满分时必须提供逐项证据索引。

## 7. 风险优先级

优先级先按业务影响、发生可能性、数据/资金风险、流程阻断范围判定，再进行组合统计。默认策略为 `risk_first_configurable`；P0/P1/P2 比例是项目配置或告警，不得通过关键词批量降级凑比例。若项目明确要求严格比例，必须在 `priority_policy` 中声明并记录每次调整理由。

## 8. 交付前验收

至少满足以下条件才允许生成模板版 Excel：

- 规范性条款账本与原文逐条对账通过；
- 版本差异记录完整，歧义项已显式列出；
- `N_min` 由门禁计算，所有覆盖单元一对一闭合；
- 语义适用性和语义重复审计通过；
- 标题包含对象/动作/条件，步骤和预期可直接执行；
- 评审独立性和评分证据完整；
- 生成/评审模式的环境探测明确为 `N/A`，没有伪造执行证据。


## Excel 基线与 V3 质量契约

无 PRD 的规范化仍启用质量契约，但 `generation_scope.source_policy: current_xlsx_baseline`、`historical_case_reuse: supplied_baseline_only`；最终源保留 `requirement_source: existing_xlsx_baseline` 和 `requirement_completeness: not_assessed`。`source_documents` 使用 `source_type: existing_xlsx_baseline`，其 `sha256` 必须匹配导入账本；每条条款和原子规则的 `source_locator` 使用真实 `sheet`、整数 `row`，原文摘录来自原工作簿。覆盖下限仅代表基线事实的最低设计规模，不代表完整 PRD 覆盖。提供 PRD 时用 `current_prd_only`，原 Excel 仅作为用户提供的可复用资产。

存量链先用 `--legacy-audit` 排查迁移缺口；该模式仍校验原子账本、执行语义和来源等基础结构，不代表 V3 质量契约合规。`backfill_quality_fields.py` 只按已有明确事实派生字段；未明确的前置数据来源、步骤目标、评审结论和原文覆盖完整性必须补充后重新运行严格门禁。

XLSX-only 独立评审的 source_artifacts 使用 current_xlsx_baseline 与 normative_statements，不要求填写不存在的 current_prd。risk-first 缺少某个优先级时，review_audit.priority_coverage_rationale 必须逐档说明依据；无需为三档配额伪造用例。显式 l2_only 必须在 generation_scope、coverage_profile 和 case_source 三处一致声明；它是限定范围，不能作为 full 交付。

显式 l2_only 只有在未声明质量契约版本时才标记 waived；若仍声明版本，则继续校验已声明的事实、数量和质量，不同时声称 enforced 与 waived。两种情况都属于限定范围，不能冒充 full 覆盖。
