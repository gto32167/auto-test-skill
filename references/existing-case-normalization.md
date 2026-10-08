# 已有测试用例规范化

## 目标

`case_normalization` 用于把用户已经维护的 `.xlsx` 测试用例转换成当前工作流可评审、可门禁、可执行的标准用例链。它是生成阶段的一种输入模式，不是绕过设计门禁的执行捷径。

规范化完成后仍以 `06_final_test_cases.yaml` 为唯一执行源。原 Excel 是来源证据和交付模板，不是执行器输入。

## 输入

必选：

- 一份 `.xlsx` 测试用例文件。
- 至少能映射出用例名称、执行步骤、预期结果三列。

推荐：

- 原始 PRD、原型或需求说明。提供后可以审计需求覆盖并补充缺失场景。
- 环境信息。提供后可以完成评审阶段的可执行性抽样。
- 用户希望保留的 Excel 模板列和工作表名称。

当前确定性导入器不支持旧 `.xls`。先使用 Excel/WPS 另存为 `.xlsx`，不得通过修改扩展名伪装格式。

## 阶段产物

```text
已有测试用例.xlsx
  -> 03_existing_cases_import.yaml
  -> 03_existing_cases_import_report.json
  -> 05_requirements.yaml
  -> 05_test_points.yaml
  -> 06_final_test_cases.yaml
  -> 07_case_design_gate.json
  -> test_case_review.md
  -> test_case_review_scores.md
  -> 08_测试用例_模板版.xlsx
```

`03_existing_cases_import_report.json` 的 `status: pass` 只表示 Excel 被完整读取；它不是用例设计门禁。即使导入报告通过，也必须完成规范化评审并让 `07_case_design_gate.json` 通过后才能执行。

## 导入命令

```powershell
py references/scripts/import_existing_cases.py `
  --source <项目目录>/已有测试用例.xlsx `
  --output <项目目录>/03_existing_cases_import.yaml `
  --report <项目目录>/03_existing_cases_import_report.json
```

自动识别失败或工作簿存在多个相似工作表时，可增加：

```text
--sheet 测试用例 --header-row 2
```

导入器会：

- 在前 20 行自动识别表头；
- 识别常见中文和英文别名；
- 展开合并单元格中的功能模块值；
- 保留源工作表、源行号、原用例 ID、原始列值和文件 SHA-256；
- 为缺失或重复 ID 生成稳定建议 ID；
- 把常见高/中/低优先级映射为 P0/P1/P2 建议值；
- 标记缺列、缺内容、非标准优先级和疑似多断言用例。

导入器不会根据词语猜测业务规则，不会自动宣称需求已覆盖，也不会直接生成可执行脚本。

## 表头映射

| 标准字段 | 常见可识别列名 |
|---|---|
| `case_id` | 用例ID、用例编号、编号、ID |
| `feature_group` | 功能集合、所属模块、功能模块、模块、页面、功能点 |
| `case_title` | 用例名称、测试用例、用例标题、标题、测试点 |
| `priority` | 优先级、级别、Priority |
| `preconditions` | 前置条件、前提条件、前置 |
| `test_data` | 测试数据、用例数据、输入数据 |
| `steps` | 执行步骤、测试步骤、操作步骤、步骤 |
| `expected_result` | 预期结果、期望结果、预期 |

除必要三列外，缺失列不会阻止机械导入，但会在规范化评审中形成待补项。无法可靠映射时，不得悄悄丢列；使用 `source_values` 保留原内容并在报告中暴露问题。

## 规范化规则

### 保留原则

- 保留原始业务意图、步骤顺序、预期口径和来源行。
- 原 ID 唯一且非空时优先沿用；拆分用例使用可追溯后缀，例如 `TC-001-A`、`TC-001-B`。
- 只有语义完全等价的重复用例才允许合并，并在评审文档记录来源行与合并理由。
- 不得为了满足优先级占比而随意改变业务风险等级。

### 必须补齐

每条规范化后的测试点和最终用例必须补齐现有设计门禁要求，包括：

- `requirement_ids`、`test_point_id` 和非空 `feature_group`；
- 场景/字段粒度、正负向、接口、字段、输入类别和预期结果；
- P0/P1/P2 及非空 `priority_rationale`；
- 独立前置状态、测试数据、数据构造策略和清理策略；
- 一个主断言；必要时把原行拆成多条用例；
- `executor_profiles`、`test_intent`、`execution_contract`；
- 可机器求值的 `result_contract`、硬/软断言和证据要求；
- 变化类、持久化类、拒绝类和结构化输入类用例的专项契约。

最终源同时声明规范化来源：

```yaml
case_source:
  artifact_role: final
  mode: full
  generation_mode: case_normalization
  requirement_source: prd              # 或 existing_xlsx_baseline
  requirement_completeness: assessed   # 无 PRD 时必须为 not_assessed
  priority_policy: strict_p0_p1_p2
```

每条最终用例声明：

```yaml
normalization_trace:
  origin: existing_xlsx                # PRD 补充用例使用 prd_supplement
  source_rows: [12]
  source_case_ids: ["TC-001"]
  change_type: revised                  # preserved/revised/split/merged/added
  reason: "把原预期中的两个独立断言拆分，并补齐可判定结果"
```

`prd_supplement` 只能在提供 PRD 时使用，必须设置 `change_type: added` 且不填写 Excel 源行。来自 Excel 的用例必须引用导入产物中真实存在的正整数源行。

### 有 PRD 时

- PRD 是业务预期和需求覆盖的真相来源。
- 现有 Excel 是待复用资产；与 PRD 冲突时保留原文证据并列入差异，不得静默覆盖。
- 除规范化原有用例外，还要补齐 PRD 中缺失的 L2 场景和必要 L3 字段、边界、负向覆盖。
- `case_ordering.source` 使用 PRD，功能集合顺序按 PRD 首次出现顺序组织。

### 没有 PRD 时

- Excel 是本轮测试基线，不等同于完整产品需求。
- `05_requirements.yaml` 只创建从现有用例可证实的基线需求容器，`source` 必须定位到工作表和源行，禁止补写无法从用例推导的业务规则。
- `case_ordering.source` 使用原 Excel 文件和工作表。
- 可以审计内部一致性、独立性和可执行性，也可以补齐实现执行所需的结构字段。
- 评审和报告必须写明 `requirement_completeness: not_assessed`，不得宣称需求覆盖完整，也不得仅为了让用例数大于需求数而虚构需求。
- 如果现有用例无法形成“用例数大于需求数”的合法需求聚合，停止在 `revise_required`，要求补充 PRD 或人工确认需求分组。

## 审计与变更记录

评审文档必须新增规范化摘要：

- 输入行数、成功导入数、跳过行及原因；
- 原样保留、修订、拆分、合并、新增、拒绝六类数量；
- 每条发生语义变化的用例对应源行、变更前后内容和理由；
- 重复 ID、缺字段、跨用例依赖、多断言、非标准优先级清单；
- 有 PRD 时的新增覆盖；无 PRD 时的覆盖完整性限制。

禁止把自动生成建议当成用户已确认的产品规则。存在无法从 PRD、Excel 或环境证据确定的业务预期时，把它记录为待确认问题；在关键断言不明确时不得通过设计门禁。

## 进入执行的条件

只有同时满足以下条件，才能切换到 `execution_only`：

1. `03_existing_cases_import.yaml` 能追溯到原 Excel 的 SHA-256 和源行；
2. `05_requirements.yaml`、`05_test_points.yaml`、`06_final_test_cases.yaml` 已生成；
3. 规范化评审和 11 维逐用例评分已完成；
4. 使用 `--existing-case-import <项目目录>/03_existing_cases_import.yaml` 运行设计门禁，且 `07_case_design_gate.json.status` 为 `pass`；
5. `08_测试用例_模板版.xlsx` 与最终 YAML 的 ID、数量和顺序一致；
6. 环境信息足以生成执行计划和项目脚本。

原 Excel 可以作为 `generate_case_xlsx.py --template` 的模板底稿。无法映射到正式七列的附加列应保留，不得破坏用户原有审阅信息。

## 子用例结果反向聚合到原始用例

规范化执行后，不能只看拆分后的最终用例结果。使用 `normalization_trace.source_rows` 将每条子用例绑定回 Excel 原始行，再按以下规则聚合：

- 任一子用例 `failed`，原始用例为 `failed`；
- 没有失败但存在 `blocked`，原始用例为 `blocked`；
- 没有失败或阻塞但存在 `not_run`，全部未执行时为 `not_run`，部分未执行时为 `blocked`；
- 只有全部子用例 `passed`，原始用例才为 `passed`。

聚合结果必须同时保留子用例数量、各状态数量、失败子用例 ID、失败步骤、失败原因、阻塞原因和证据索引。原始 Excel 不直接覆盖，先生成独立的聚合 YAML/JSON 和结果回填副本：

```powershell
py references/scripts/aggregate_original_case_results.py `
  --final-cases <项目目录>/06_final_test_cases.yaml `
  --execution-results <项目目录>/11_test_execution_results.yaml `
  --existing-case-import <项目目录>/03_existing_cases_import.yaml `
  --output-yaml <项目目录>/20_original_case_result_aggregation.yaml `
  --output-json <项目目录>/20_original_case_result_aggregation.json
```

然后使用 `generate_original_case_result_xlsx.mjs` 将聚合列追加到原 Excel 的副本，生成 `21_原始用例_执行结果回填.xlsx`。这份父用例结果与 `18_测试用例_执行回填版.xlsx` 的子用例结果互相引用，不能用父用例汇总替代子用例证据。

规范化模式的门禁命令：

```powershell
py references/scripts/validate_case_design.py `
  --requirements <项目目录>/05_requirements.yaml `
  --test-points <项目目录>/05_test_points.yaml `
  --final-cases <项目目录>/06_final_test_cases.yaml `
  --existing-case-import <项目目录>/03_existing_cases_import.yaml `
  --output <项目目录>/07_case_design_gate.json
```
