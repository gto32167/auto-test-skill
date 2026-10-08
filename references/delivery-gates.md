# 全量交付门禁

## 目标

把用例完整性、执行语义一致性、结果证据和 XLSX 可读性从文字要求变成可重复执行的硬校验。完整测试交付默认使用 `full` 模式；同时支持分段交付：可从 PRD 生成用例、从已有 XLSX 规范化用例并在模板版 Excel 处停止，也可直接执行已有的已门禁最终用例并继续回填/报告。除非用户明确要求仅冒烟，否则不得只生成或执行场景级用例。

## 阶段级入口

- 已有用例规范化入口：`已有测试用例.xlsx` -> `03_existing_cases_import.yaml` / `03_existing_cases_import_report.json` -> `05_requirements.yaml` -> `05_test_points.yaml` -> `06_final_test_cases.yaml` -> `07_case_design_gate.json` -> 评审文档 -> `08_测试用例_模板版.xlsx`
- 生成/评审入口：`05_requirements.yaml` -> `05_test_points.yaml` -> `06_final_test_cases.yaml` -> `07_case_design_gate.json` -> `test_case_review.md` / `test_case_review_scores.md` -> `08_测试用例_模板版.xlsx`
- 执行入口：已通过门禁的 `06_final_test_cases.yaml` -> `10_browser_execution_plan.yaml` -> `10_execution_plan_gate.json` -> `11_test_execution_results.yaml` -> `12_execution_gate.json` -> `18_测试用例_执行回填版.xlsx` -> `19_delivery_gate.json` -> `23_正式测试报告.md` + `23_正式测试报告.docx`

门禁验证的是产物闭合与证据质量，不负责把失败或阻塞用例改判为通过。任一门禁返回非零退出码时，停止进入下一阶段，修正产物后重新验证。

## 唯一产物链

项目目录使用以下角色明确的文件名：

```text
03_existing_cases_import.yaml      # 仅规范化模式，已有 Excel 的可追溯导入结果
03_existing_cases_import_report.json # 仅规范化模式，机械导入状态与问题统计
04_scenario_cases.yaml          # L2 场景初稿，仅供设计，禁止执行和交付
05_requirements.yaml            # 原始需求条目及处理状态
05_test_points.yaml             # 全量测试点 + 字段覆盖清单
06_final_test_cases.yaml        # 唯一最终用例源
07_case_design_gate.json        # 用例设计门禁
08_测试用例_模板版.xlsx          # 从最终用例源生成
10_browser_execution_plan.yaml  # 浏览器执行计划
10_execution_plan_gate.json     # 执行路由与语义门禁
11_test_execution_results.yaml  # 完整 attempts/trace/observations/evidence
12_execution_gate.json          # 执行结果门禁
18_测试用例_执行回填版.xlsx      # 从已通过执行门禁的结果回填
19_delivery_gate.json           # XLSX 与最终交付门禁
23_正式测试报告.md           # 可审计源
23_正式测试报告.docx         # 正式交付版
```

`04_scenario_cases.yaml` 不能改名伪装为最终用例。`06_final_test_cases.yaml` 必须声明：

```yaml
case_source:
  artifact_role: final
  mode: full
  priority_policy: strict_p0_p1_p2
```

`03_existing_cases_import.yaml` 与其报告也不能伪装为最终用例或设计门禁。已有 Excel 的导入和规范化流程见 [existing-case-normalization.md](existing-case-normalization.md)。

## 已有 XLSX 导入门槛

先运行：

```powershell
py references/scripts/import_existing_cases.py `
  --source <项目目录>/已有测试用例.xlsx `
  --output <项目目录>/03_existing_cases_import.yaml `
  --report <项目目录>/03_existing_cases_import_report.json
```

机械导入至少要求用例名称、执行步骤、预期结果三列。导入产物必须保留原 Excel 的绝对路径、SHA-256、工作表、源行、原 ID 和原始列值。重复 ID、缺内容、非标准优先级和疑似多断言只形成审计输入，不得自动视为已修复。

规范化完成后仍执行下方同一套用例设计门禁。未提供 PRD 时，评审必须声明需求完整性未评估；关键业务预期无法从 Excel 证实时应停在 `revise_required`，不得编造规则使门禁通过。

规范化模式运行设计门禁时必须在标准命令中增加：

```text
--existing-case-import <项目目录>/03_existing_cases_import.yaml
```

最终源必须声明 `case_source.generation_mode: case_normalization`、需求来源和完整性口径；每条最终用例必须通过 `normalization_trace` 绑定真实源行或明确标记为 PRD 补充用例。导入产物也会被记录到门禁输入 SHA-256 中。

## 用例设计门禁

标准输入模板：

- `assets/templates/requirements.yaml`
- `assets/templates/coverage-matrix.yaml`（复制为项目的 `05_test_points.yaml`）
- `assets/templates/test-cases.yaml`（复制为项目的 `06_final_test_cases.yaml`）

执行：

```powershell
py references/scripts/validate_case_design.py `
  --requirements <项目目录>/05_requirements.yaml `
  --test-points <项目目录>/05_test_points.yaml `
  --final-cases <项目目录>/06_final_test_cases.yaml `
  --output <项目目录>/07_case_design_gate.json
```

通过条件：

- 所有需求都有合法处理状态；标记 `covered` 的需求至少关联一个测试点。
- 测试点 ID 唯一，每个测试点关联有效需求 ID。
- 最终用例 ID 唯一；每条最终用例只关联一个 `test_point_id`。
- 测试点与最终用例严格一对一，`final_case_count == test_point_count`。
- 每个测试点和最终用例必须声明相同的非空 `feature_group`；最终源必须声明 `case_ordering.policy: prd_port_page_group` 和非空 `group_sequence`。
- 实际功能集合顺序必须与 `group_sequence` 一致，同一集合必须连续，测试点与最终用例的行顺序及功能集合必须一致。
- `coverage_profile.priority_policy` 与 `case_source.priority_policy` 都必须为 `strict_p0_p1_p2`。
- 每个测试点和最终用例只能使用 P0/P1/P2，必须填写非空 `priority_rationale`，且一对一映射的等级和理由完全一致。
- 必须先按业务风险判级，再满足全量组合：P0 为 20%～25%，P1 为 35%～40%，P2 为 35%～40%；禁止随机改级凑比例。20 条及以上严格按比例换算整数上下限，少于 20 条只接受单条用例造成的取整误差，且三档仍必须全部存在。
- `07_case_design_gate.json.priority_policy.status` 必须为 `pass`；等级缺失、占比越界、理由缺失或测试点/最终用例不一致时，门禁失败。
- `result_contract.assertion_policy` 必须完整划分硬/软断言；精确 UI 文案硬断言必须有 `prd_exact: true`，拒绝类 `mutation_committed=false` 必须为硬断言。
- `review_audit` 必须抽样探测至少 10% 总用例和 20% P0 用例的环境可执行性，保留通道、证据和修订动作；全部 22/22 时必须提供满分依据。
- 最终用例数大于需求条目数；该条件是关系约束，不使用固定的 29、100 或 102 作为通用阈值。
- 每条用例恰好一个主断言。
- 全量模式包含场景级用例；存在核心表单时必须同时包含字段级用例。
- `field_coverage.required_classes` 中声明的每个字段覆盖类别都有对应字段测试点。
- 测试点和最终用例的正/负向、接口、字段、输入类别和预期结果完全一致。
- `execution_profile` 在 `executor_profiles` 中存在，并具备用例要求的能力和主通道。
- `result_contract.observations` 可机器求值；`required_produced_keys` 与观察键完全一致。
- 拒绝类用例同时要求主接口拒绝证据和 `mutation_committed equals false`。
- 结构化输入用例必须声明 `fixture_contract`，并在执行前校验文件格式、表头集合、行列形状及空值/非空值状态；契约缺失、互相矛盾或测试文件构造不符合契约时，用例不得进入执行，也不得把页面通用解析异常计为目标断言通过。

脚本输出包含三份输入的绝对路径、SHA-256 和每条用例的 `semantic_contract_sha256`。门禁通过后，修改任何输入或语义契约都会导致后续脚本拒绝执行。完整契约见 [semantic-contract-gates.md](semantic-contract-gates.md)。

用例评审必须单独列出 P0/P1/P2 的数量、比例、允许区间、越界项和整改后分布，并逐条检查判级理由。优先级是独立硬门禁，不计入原 11 维 22 分；即使评分达到 18 分或 22 分，优先级专项失败时，评审结论仍只能是 `revise_required` 或 `reject`，不得进入模板生成或执行阶段。

## 模板版生成

未提供用户模板时，脚本生成方案默认 7 列模板：`用例ID / 功能集合 / 用例名称 / 优先级 / 前置条件 / 执行步骤 / 预期结果`。提供模板时保留用户模板作为底稿，并把 `feature_group` 写入用户模板的等价模块/页面列：

```powershell
py references/scripts/generate_case_xlsx.py `
  --source <项目目录>/06_final_test_cases.yaml `
  --case-gate <项目目录>/07_case_design_gate.json `
  --output <项目目录>/08_测试用例_模板版.xlsx
```

使用用户模板时增加：

```text
--template <用户模板.xlsx>
```

生成器使用 inline string 写入单元格，避免仅修改 `sharedStrings` 导致部分 Excel/WPS 查看器显示空白。

说明：`08_测试用例_模板版.xlsx` 是评审/生成阶段的固定输出，不必等执行完成后才生成；如果本轮只要求生成用例或复核用例，到这里即可停止。

正式模板版必须使用中文业务文本：不得把 `execution_profile`、英文动作名、字段 ID、覆盖类别、脚本变量或“符合 PRD 约束”这类非自包含预期写入单元格。自动化元数据只允许保留在 YAML 内部或执行计划中。

## 执行计划门禁

执行计划必须逐条复制设计门禁中的语义哈希，并明确 profile、执行器能力、主通道、意图快照、前置步骤、核心步骤和计划观察键。计划还必须固定 `qualification` 或 `risk_based_regression` 模式、输入 SHA-256、attempt/分钟预算、每条用例执行预算、UI/API 分离证据计划、blocked 前能力探测、状态等待策略和 UI 字段控件审计。计划条目必须对最终用例做稳定优先级排序：全部 P0、全部 P1、全部 P2，同一优先级内保持最终用例原顺序，并在 `execution_meta.priority_execution_policy` 声明版本、顺序、P0 完成后评估、全部 P0 最终均为 failed 才触发、任一 P0 为 passed 或 blocked 则继续，以及 `not_run` 原因码。生成计划后、执行任何用例前运行：

```powershell
py references/scripts/validate_execution_plan.py `
  --case-gate <项目目录>/07_case_design_gate.json `
  --final-cases <项目目录>/06_final_test_cases.yaml `
  --execution-plan <项目目录>/10_browser_execution_plan.yaml `
  --output <项目目录>/10_execution_plan_gate.json
```

计划门禁失败时不得执行。字段负向用例必须计划 `input_test_value`、`attempt_commit` 和 `observe_outcome` 核心动作，禁止在核心步骤中走合法资源创建路径。风险回归 P2 单跑必须由门禁复算输入哈希一致、历史至少 2/2 稳定通过、需求未受影响和基线结果 SHA-256；不满足时自动回到双跑预算。计划 ID 集合必须完整，但计划顺序不再照抄最终用例文件，而是由门禁强制为稳定的 `P0 -> P1 -> P2`。

### P0 产品质量停止门禁

该门禁与四道产物门禁不同：它根据产品执行结果决定是否继续启动低优先级用例。固定流程如下：

1. 执行全部 P0；每条都按重试策略 3.0 完成双跑、必要第三遍和最终裁决。
2. 不因第一条 P0 失败或某个中间 attempt 失败提前停止，必须完成整个 P0 批次。
3. P0 批次结束后，只有全部 P0 的最终状态均为 `failed` 才触发 `p0_all_failed_stop_gate`，禁止启动 P1/P2；只要任意 P0 最终为 `passed` 或 `blocked`，就继续执行 P1/P2。
4. 全部 P0 最终均失败时，未启动的 P1/P2 全部回填 `status: not_run`、`stability: not_run`、`not_run_reason: p0_all_failed_stop_gate`、`attempts: []`，不生成伪证据或业务结果。
5. 停止后仍运行执行结果门禁、回填、交付门禁并生成 Markdown/Word 正式报告。规则执行正确时，执行结果门禁可以为 `pass`；这只表示停止轨迹和交付物可信，不代表产品通过。
6. P0 失败后若 P1/P2 已有 attempts，属于顺序违规，执行门禁失败，必须按正确批次重跑；禁止删除真实轨迹掩盖违规。

## 执行结果门禁

执行计划和执行结果必须记录 `06_final_test_cases.yaml` 的 SHA-256：

```yaml
execution_meta:
  final_cases_path: "06_final_test_cases.yaml"
  final_cases_sha256: "..."
  execution_plan_path: "10_browser_execution_plan.yaml"
  execution_plan_sha256: "..."
```

若原始执行器先输出脚本节点状态，可用 `generate_case_execution_results.py` 合并，但必须同时提供最终用例、用例门禁、执行计划和计划门禁。该脚本要求映射 ID 集合与最终用例完全一致，映射顺序按稳定的 P0/P1/P2 排列，正式结果输出再恢复最终用例原顺序以便回填；它保留原始 `attempts`、`execution_trace`、`observations`、`produced_data` 和 `evidence`，不会把缺失记录伪装为通过，也不会把普通脚本记录缺失误写为 P0 停止。

```powershell
py references/scripts/generate_case_execution_results.py `
  --mapping <项目目录>/case_execution_mapping.yaml `
  --script-status <项目目录>/script_execution_status.yaml `
  --final-cases <项目目录>/06_final_test_cases.yaml `
  --case-gate <项目目录>/07_case_design_gate.json `
  --execution-plan <项目目录>/10_browser_execution_plan.yaml `
  --execution-plan-gate <项目目录>/10_execution_plan_gate.json `
  --output <项目目录>/11_test_execution_results.yaml
```

先验证执行结果，再允许回填：

```powershell
py references/scripts/validate_execution_delivery.py `
  --phase execution `
  --case-gate <项目目录>/07_case_design_gate.json `
  --final-cases <项目目录>/06_final_test_cases.yaml `
  --execution-plan-gate <项目目录>/10_execution_plan_gate.json `
  --execution-plan <项目目录>/10_browser_execution_plan.yaml `
  --execution-results <项目目录>/11_test_execution_results.yaml `
  --screenshots-dir <项目目录>/artifacts/screenshots `
  --output <项目目录>/12_execution_gate.json
```

执行门禁检查：

- 执行结果 ID 及顺序与最终用例完全一致。
- 所有 P0 均已形成 passed/failed/blocked 最终裁决；P0 未完成时禁止出现已执行的 P1/P2。
- `priority_gate` 与逐条结果复算一致；全部 P0 最终均为 failed 时，P1/P2 全部为标准 `not_run/p0_all_failed_stop_gate` 且无 attempts；只要任意 P0 最终为 passed 或 blocked 就不得伪造停止。
- 计划门禁、最终用例、执行计划和执行结果哈希形成同一版本链。
- 每条结果和每个 attempt 的语义哈希、profile、能力、主通道和意图轨迹与计划一致。
- attempt 编号从 1 连续；资格执行全部双跑，首两遍只有双通过可以收口，其他组合强制第三遍。P0 三遍至少 2/3 通过才能放行；合格 P2 单跑失败/阻塞强制升级完整三遍。
- `selected_attempt` 指向门禁裁决遍，最终 observations/`produced_data` 与该遍一致，并准确标记 stable/unstable/qualified_single_run/blocked。
- `actual_result` 非空、最长 500 字且不能只是状态词或原始 DOM；正式回填由结构化 `result_summary` 生成，并转换为人工可读业务事实。
- 结构化输入用例的执行结果必须保留数据契约校验结论；只有在契约自检通过后，页面反馈类别和字段锚点均与预期一致，才能判定字段校验类断言通过。页面显示通用解析异常时，应按实际观察判定失败或阻塞，不能用“上传被拒绝”替代字段级结论。
- 结果契约含 UI `prompt_text` 时，执行计划必须声明瞬态提示等待策略；每个实际 attempt 与最终选中结果必须记录 `feedback_observation` 的提示形式、原文、出现耗时、自动消失状态和截图引用。提示原文必须与结构化 observation 同值，截图必须在提示可见时采集；固定等待后只读一次页面的结果不通过执行门禁。
- 回填交付必须包含“前置条件、执行步骤、预期结果、实际执行结果、失败原因、失败步骤、阻塞/未执行原因、产出数据、证据”字段。`blocked` 需说明停留阶段、已/未完成动作、不可判定产品缺陷的理由和解除条件；`failed` 需说明实际观察事实和未满足的步骤。纯 `Locator` 错误、`UI 断言未满足` 或 `未执行核心业务动作` 不得作为唯一人工说明。
- 每个 `passed` / `failed` attempt 以及最终选中结果都具有完整 observations；来源、键、类型和证据引用必须有效。
- `passed` 的硬断言不匹配项必须为 0；`failed` 必须至少有一项硬断言不匹配，否则状态与证据矛盾，门禁失败；软断言差异只记录表现偏差。
- `produced_data` 与结构化 observations 逐项同值；布尔 `false` 按真实值保留，不能用空值或字符串代替。
- 失败用例包含 `failed_step_no`、缺陷摘要、严重级别建议、确认状态、责任人、缺陷状态和回归状态。
- 阻塞/未执行包含原因；最终 blocked 即使有 attempts 也必须保留能力探测通道和探测日志；`not_run` 的稳定性必须为 `not_run`，不能混写成 blocked。
- 每条实际执行的 UI 用例存在 `<case_id>.png`；manifest 中每个文件真实存在且 SHA-256 一致，锚点位于验证文本，UI 语义键覆盖完整，兼容浏览器证据彼此独立。API 用例和 API 来源 observation 使用独立 `evidence_refs`，不得伪造截图引用。
- 变化类用例必须在每个实际 attempt 同时存在 `<case_id>_A<n>_before.png` 与 `<case_id>_A<n>_after.png`，manifest 分别声明 `phase: before/after`、同一对象标识和 SHA-256；before manifest 的 `content_text`/`observed_target_text` 必须来自 before_state，after manifest 必须来自 after_state，并用实际断言值/字段标签填充 `content_anchors`；结果必须包含 `change_comparison`，前后证据缺失或无法证明差异时不得判定 `passed`。
- `scope_matrix`、输入哈希、预计/实际资源和 summary 稳定性指标与逐条结果复算一致。

## 回填与最终交付门禁

执行门禁通过后回填：

```powershell
py references/scripts/backfill_case_execution_results.py `
  --source <项目目录>/06_final_test_cases.yaml `
  --case-gate <项目目录>/07_case_design_gate.json `
  --execution-yaml <项目目录>/11_test_execution_results.yaml `
  --execution-gate <项目目录>/12_execution_gate.json `
  --template-xlsx <项目目录>/08_测试用例_模板版.xlsx `
  --output-md <项目目录>/18_测试用例_执行回填版.md `
  --output-xlsx <项目目录>/18_测试用例_执行回填版.xlsx
```

`assets/templates/gen_xlsx_backfill.py` 仅保留为兼容入口，内部强制委托给上述门禁回填器，并要求相同的最终用例、用例门禁、完整执行结果和执行门禁参数。2026-08-24 及更早版本的“项目目录 + Markdown 文件名 + 输出文件名”位置参数调用已禁用，不能再从裸 JSONL/汇总文件旁路生成正式回填版。

然后执行交付门禁：

```powershell
py references/scripts/validate_execution_delivery.py `
  --phase delivery `
  --case-gate <项目目录>/07_case_design_gate.json `
  --final-cases <项目目录>/06_final_test_cases.yaml `
  --execution-plan-gate <项目目录>/10_execution_plan_gate.json `
  --execution-plan <项目目录>/10_browser_execution_plan.yaml `
  --execution-results <项目目录>/11_test_execution_results.yaml `
  --screenshots-dir <项目目录>/artifacts/screenshots `
  --template-xlsx <项目目录>/08_测试用例_模板版.xlsx `
  --backfill-xlsx <项目目录>/18_测试用例_执行回填版.xlsx `
  --viewer-check auto `
  --output <项目目录>/19_delivery_gate.json
```

交付门禁额外检查：

- 两份 XLSX 数据行数均等于最终用例数，ID 及顺序一致。
- 回填版状态与实际结果列无空值、无纯状态回填。
- 回填版实际结果和原因字段面向人工复审，禁止直接展示完整页面 DOM；Markdown 审计源按用例卡片展示，Excel 与 Markdown 的业务结论必须一致。报告中的不稳定性列表只引用用例 ID/尝试序列，不复制原始页面全文，详细事实以回填版为准。
- XLSX ZIP/OpenXML 可解析。
- Windows 上安装 Excel/WPS 时，自动使用实际应用打开并读取首张工作表；已安装的查看器打不开即失败。没有可用查看器时保留 warning，CI 可用 `--viewer-check required` 强制要求。

交付门禁通过后才允许生成正式报告：

```powershell
py references/scripts/generate_test_report.py `
  --final-cases <项目目录>/06_final_test_cases.yaml `
  --case-gate <项目目录>/07_case_design_gate.json `
  --execution-yaml <项目目录>/11_test_execution_results.yaml `
  --execution-gate <项目目录>/12_execution_gate.json `
  --delivery-gate <项目目录>/19_delivery_gate.json `
  --project-name <项目名称> `
  --backfill-xlsx-path <项目目录>/18_测试用例_执行回填版.xlsx `
  --output <项目目录>/23_正式测试报告.md `
  --output-docx <项目目录>/23_正式测试报告.docx
```

报告生成器会重新验证用例、执行和交付门禁的角色、阶段和输入 SHA-256；执行门禁已经绑定计划门禁及执行计划，从而形成四段门禁链。报告从唯一最终源计算总数，不能再从旧初稿或手工表格生成。正式交付使用 Word 版；Markdown 保留为可审计源。逐用例详情不在报告重复展开，以回填 Excel 为准。

## 停止条件

- 用例设计门禁失败：不得生成模板版、执行计划或开始执行。
- 优先级专项评审或 `priority_policy` 机器门禁失败：不得以 11 维评分、人工说明或进度要求豁免。
- 用例设计门禁通过后，如果本轮只要求生成/评审，模板版 Excel 和评审文档就是合法终点，不必强制进入执行阶段。
- 执行计划门禁失败：不得启动任何用例；不能用环境门禁或人工说明替代。
- 执行门禁失败：不得生成回填版或正式报告。
- 交付门禁失败：不得声明交付完成。
- 缺代码库或接口文档只跳过代码走查/接口契约测试，不影响功能用例设计门禁和功能执行。
- P1/P2 的单条业务失败不停止后续用例；P0 也不在单条或中间 attempt 失败时立即停止，而是在全部 P0 完成最终裁决后统一评估。只有 P0 批次全部最终为 failed，才按产品质量停止门禁停止 P1/P2；任一 P0 最终为 passed 或 blocked 时继续。门禁失败与业务用例失败仍是两个不同概念。
