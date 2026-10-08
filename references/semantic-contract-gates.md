# 测试语义契约与防假阳性门禁

## 目标

解决“用例执行成功但执行的不是目标测试语义”这一类高风险假阳性。状态不能由点击、接口调用或资源创建是否成功决定，必须由目标用例声明的结构化观察值共同决定。

典型错误链如下：字段负向用例要求验证空值拦截，但计划误用普通创建 profile；执行器创建了合法数据并返回资源名称；结果字段和截图都存在；旧门禁只检查字段非空，最终错误标记为 `passed`。

契约 2.0 将链路改为：

```text
测试点语义
  -> 最终用例语义契约 + 单用例语义哈希
  -> 执行计划路由门禁 + 计划文件哈希
  -> 每次 attempt 的真实执行轨迹 + 结构化观察值
  -> 执行结果门禁逐项求值
  -> 回填与交付
```

任一层不一致都停止，不能靠文字评审、截图存在或动作成功放行。

## 测试点必须明确语义

每个 `05_test_points.yaml` 测试点必须声明：

```yaml
polarity: negative       # positive / negative
interface: ui            # ui / api
expected_outcome: rejected  # accepted / rejected / committed / observed
```

字段测试点还必须声明 `field_id` 和 `coverage_class`。负向测试点的 `expected_outcome` 只能是 `rejected`。

## 最终用例契约

`06_final_test_cases.yaml` 顶层必须声明当前项目真实存在的执行 profile 能力目录：

```yaml
executor_profiles:
  tag:
    capabilities: [scenario]
    channels: [ui]
  tag_field:
    capabilities: [field_validation]
    channels: [ui]
```

能力目录用于机器判断 profile 是否能执行该类用例，不以 `_field` 命名后缀代替能力校验。若 `tag` 只支持场景流程，字段校验用例绑定 `tag` 时，设计门禁直接失败。

每条最终用例必须复制测试点语义，并声明执行契约：

```yaml
test_intent:
  kind: field_validation
  polarity: negative
  interface: ui
  field_id: tag.name
  input_class: whitespace
  expected_outcome: rejected
execution_profile: tag_field
execution_contract:
  capability: field_validation
  primary_channel: ui
  setup_separated: true
```

`setup_separated: true` 表示前置数据构造与核心验证动作物理分层。API 可以准备基线标签或读取列表，但不能代替“在 UI 输入空格并尝试保存”的核心动作。

## 结果契约

`required_produced_keys` 只能证明字段存在，不能证明结果正确。契约 2.0 要求每个字段同时声明来源、比较操作、期望值和证据要求：

```yaml
result_contract:
  verdict: all
  assertion_policy:
    hard_keys: [mutation_committed]
    soft_keys: [prompt_text]
  required_produced_keys: [prompt_text, mutation_committed]
  assertion_observation_keys: [prompt_text, mutation_committed]
  screenshot_required: true
  observations:
    - key: prompt_text
      assertion_class: soft
      source: ui
      operator: contains
      expected: 标签名称不能为空
      evidence_required: true
    - key: mutation_committed
      assertion_class: hard
      source: api
      operator: equals
      expected: false
      evidence_required: true
```

通过条件是所有 hard observations 同时成立。没有 PRD 逐字依据的 `prompt_text` 作为 soft 断言，文案差异会记录但不推翻业务结论；若 PRD 明确逐字文案，可声明 `prd_exact: true` 并列入 hard。`mutation_committed=true` 表示资源已创建或修改，和 `rejected` 矛盾，必须失败。

支持的比较操作：

- `equals` / `not_equals`
- `contains` / `not_contains`
- `non_empty` / `empty`
- `matches`
- `greater_than` / `greater_or_equal`
- `less_than` / `less_or_equal`

布尔值执行严格类型比较。期望 `false` 时，字符串 `"false"`、空字符串和缺失字段都不能冒充布尔值 `false`。

### 负向用例硬规则

`expected_outcome: rejected` 的用例必须同时具备：

- 与主接口一致的拒绝证据，例如 UI 提示、按钮禁用、状态保持或 API 错误码。
- `mutation_committed equals false`，证明保存、创建、修改或删除没有落地。
- `mutation_committed` 必须属于 `assertion_policy.hard_keys`；提示文案通常为 soft，只有 PRD 逐字约束时才能 hard。

仅有提示但数据已写入，或者仅声明没有写入但没有拒绝证据，都不能通过。

### 正向提交硬规则

`expected_outcome: committed` 的用例必须包含 `mutation_committed equals true`，并至少保留一个能定位业务对象或成功状态的观察值。动作执行没有抛异常不等于业务提交成功。

## 执行计划门禁

生成 `10_browser_execution_plan.yaml` 后、执行任何用例前，必须运行：

```powershell
py references/scripts/validate_execution_plan.py `
  --case-gate <项目目录>/07_case_design_gate.json `
  --final-cases <项目目录>/06_final_test_cases.yaml `
  --execution-plan <项目目录>/10_browser_execution_plan.yaml `
  --output <项目目录>/10_execution_plan_gate.json
```

计划中每条用例必须包含：

- `semantic_contract_sha256`
- 与最终用例完全一致的 `execution_profile`、`executor_capability`、`primary_channel`
- 完全一致的 `intent_snapshot`
- 独立的 `setup_steps` 和非空 `core_steps`
- 与结果契约完全一致的 `planned_observation_keys`
- `execution_mode`、输入 SHA-256、资源和逐用例 attempt 预算
- UI/API 语义键分离的证据计划、blocked 能力探测计划、状态等待策略；UI 字段用例还必须有控件语义审计

字段负向计划的核心步骤必须包含 `input_test_value`、`attempt_commit`、`observe_outcome`，且不得出现 `create_valid_resource` 或 `commit_valid_resource`。

计划门禁未通过时不得开始执行。环境门禁不能替代语义计划门禁，两者都必须通过。

## 执行结果结构

每条结果和每个 attempt 必须回传同一语义哈希和执行轨迹：

```yaml
semantic_contract_sha256: "..."
execution_trace:
  execution_profile: tag_field
  executor_capability: field_validation
  primary_channel: ui
  intent_snapshot:
    kind: field_validation
    polarity: negative
    interface: ui
    field_id: tag.name
    input_class: whitespace
    expected_outcome: rejected
```

每个已经实际判定为 `passed` 或 `failed` 的 attempt，以及最终选中结果，都必须回传结构化 observations：

```yaml
observations:
  prompt_text:
    value: 标签名称不能为空
    source: ui
    evidence_refs: [TC-ZERO-050.png]
  mutation_committed:
    value: false
    source: api
    evidence_refs: [api:tags/no-new-record]
produced_data:
  prompt_text: 标签名称不能为空
  mutation_committed: false
feedback_observation:
  presentation: inline
  text: 标签名称不能为空
  appeared_after_ms: 1046
  auto_dismissed: true
  screenshot_path: TC-ZERO-050.png
```

`produced_data` 必须与 observations 的值逐项一致。UI observation 的证据引用必须指向该用例在顶层 evidence 中声明的截图。

当结果契约包含 UI 来源的 `prompt_text` 时，执行计划必须声明 `feedback_wait_policy`，每个实际执行的 attempt 和最终选中结果必须提供 `feedback_observation`。提示可能是 modal、toast、inline 或 banner；捕获器必须先记录动作前基线，再以短间隔有界轮询，并在提示仍可见时取证。`feedback_observation.text` 必须与 `observations.prompt_text.value` 一致，截图引用必须属于本 attempt 或最终结果的证据。具体捕获方法见 `execution-playbook.md`“瞬态提示捕获”。

状态也必须由契约求值结果反推，不能先写状态再拼证据：

- `passed`：全部 hard observations 满足结果契约；soft 差异单独记录，不伪装为业务失败。
- `failed`：observations 的结构、来源和证据完整，但至少一项 hard observation 不满足结果契约；不匹配项会写入 `12_execution_gate.json.semantic_evaluations` 供复审。
- `blocked` / `not_run`：环境、工具或前置条件使业务结果无法可靠求值，必须写明原因，禁止伪造 observations 冒充已执行结果。

因此，失败 attempt 不能只写“失败”或一段总结文本；如果没有可证明预期未满足的结构化观察值，应归为 `blocked`，而不是产品 `failed`。

## 执行门禁判定

`validate_execution_delivery.py` 必须同时接收已经通过的计划门禁和计划文件：

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

门禁会对每个标记为 `passed` / `failed` 的 attempt 和最终选中结果重新计算硬/软契约，并复算重试 3.0。`passed` 必须全部硬断言满足，`failed` 必须至少有一项真实硬断言不满足；动作成功、返回资源 ID、截图存在或回填字段非空均不能单独触发通过或失败。截图还必须有与真实文件一致的 SHA-256、验证文本和语义锚点 manifest。

## 版本与迁移

契约 2.0 是严格升级，不对旧结果静默降级。缺少 `test_intent`、`executor_profiles`、结构化 observations、计划门禁或语义哈希的旧项目，必须从测试点/最终用例重新生成计划和结果，不能补一个 `status: pass` 继续交付。

用例失败可以进入报告，门禁失败不能进入回填和报告。门禁失败说明测试过程本身不可证明，而不是产品测试失败。

## 能保证与不能保证的边界

门禁可以稳定发现错 profile、错能力、旧版本计划、缺证据、截图文件/哈希/锚点不一致、结果值不满足预期、负向用例产生写入等结构化矛盾。OCR/文本锚点与文件哈希显著提高可复核性，但仍不能阻止执行器恶意伪造全部结构化数据。因此仍需：

- 执行器从真实浏览器/API事件生成 trace 和 observations，不允许由总结模型事后臆造。
- 截图对准关键证据时刻，并保留运行日志/API响应索引。
- 对高风险用例抽样人工复核或增加可验证的原始事件日志。
