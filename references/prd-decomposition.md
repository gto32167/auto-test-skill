# PRD 拆解方法

## 目标

把自然语言 PRD 转成测试可消费的结构化信息，减少 AI 在后续环节自行脑补。

## 输入源界定（严格）

- **测试范围唯一来源**：用户提供的 PRD / 需求文档 + 用户明确指令。测试点、覆盖建模、预期结果只能从这里派生。
- **辅助文档（功能白皮书 / 功能说明 / 提取版 txt / 历史 diag 记录等）仅限操作知识检索**：执行期造数、配置时查入口、路由、表单规则、控件形态用。**禁止**把辅助文档里的功能描述转化为测试点或用例——白皮书里出现而需求文档中没有的功能，默认不在测试范围内，除非用户显式追加。
- 判别口诀：辅助文档回答"**怎么操作**"，PRD 回答"**测什么、预期是什么**"。两者混用视为覆盖建模污染，评审时按用例来源违规处理。

## 拆解维度

每份 PRD 至少拆成以下九类信息：

1. 版本目标
2. 用户角色
3. 功能模块
4. 业务流程
5. 页面元素与交互入口
6. 字段规则
7. 状态流转
8. 异常与限制
9. 依赖与风险

## 输出结构

空白模板见 `assets/templates/prd-analysis-template.md`，按以下八类输出。

### 1. 版本目标

- `release_id`
- `release_goal`
- `scope_in`
- `scope_out`

### 2. 用户角色

每个角色记录：

- `role_id`
- `role_name`
- `permissions`
- `entry_points`

### 3. 功能模块

每个模块记录：

- `module_id`
- `module_name`
- `business_value`
- `related_roles`

### 4. 业务流程

每条流程记录：

- `flow_id`
- `trigger`
- `main_path`
- `alternate_paths`
- `end_state`

### 5. 字段规则

每个字段至少记录：

- `field_name`
- `required`
- `type`
- `source`
- `validation_rule`
- `default_value`
- `editable_when`

### 6. 状态流转

每个对象记录：

- `entity_name`
- `initial_state`
- `intermediate_states`
- `final_states`
- `transition_conditions`
- `forbidden_transitions`

### 7. 异常与限制

- `error_condition`
- `expected_feedback`
- `rollback_rule`
- `compatibility_constraint`

### 8. 依赖与风险

- `external_dependency`
- `mock_needed`
- `testability_risk`
- `ambiguity`

## 拆解方法

### 方法一：模块切片

按页面、菜单、功能入口切分，适合后台管理类 PRD。

### 方法二：流程切片

按用户旅程切分，适合交易、审批、签署、履约类 PRD。

### 方法三：对象切片

按单据、合同、订单、任务等业务实体切分，适合状态复杂的系统。

建议优先组合使用“流程切片 + 对象切片”，这样更利于后续状态流转覆盖。

## 测试覆盖映射规则

PRD 拆解完成后，将每个需求项映射到以下测试维度：

- `positive`
- `negative`
- `boundary`
- `permission`
- `state_transition`
- `compatibility`
- `usability`
- `data_consistency`
- `recovery`

如果某个需求项无法映射到任何维度，说明拆解粒度过粗，需要继续细化。

## 歧义处理规则

当 PRD 存在模糊描述时，不要替业务拍板，统一按以下格式记录：

- `ambiguity_id`
- `source_text`
- `possible_interpretations`
- `test_impact`
- `assumed_option`
- `need_confirmation`

## 完成标准

PRD 拆解完成需要满足：

- 每个功能点都有唯一 ID
- 每条业务流程都有起点、关键动作和终态
- 每个关键字段至少有 1 条规则描述
- 每个业务对象至少有状态定义或明确说明“无状态流转”
- 每个歧义点都已记录，不允许悄悄忽略
# 原子规则账本（强制）

不得直接把 PRD 段落概括为宽泛的功能需求后开始写用例。按文件名时间从旧到新读取全部用户指定 PRD；新版对旧版规则做新增、修改、废止标记，不得静默覆盖。每条可判定规则写入 `05_requirements.yaml.atomic_rules`，并保留 `source_document`、`source_locator` 和非空 `source_excerpt`。

最小原子规则只能包含一个独立业务判断。字段的必填性、长度下界、长度上界、格式、默认值、枚举项、联动、权限、状态迁移、计算公式、批量规则、导入文件契约和异常反馈必须分别建规则；出现两个可独立通过或失败的预期结果时必须继续拆分。需求条目仅用于分组，不能代替原子规则。`covered` 规则必须进入覆盖义务；不测或待确认规则必须逐条写明原因。

完成拆解后必须先核对原文：逐份 PRD、逐个“产品方案/产品方案细节”章节检查每个规范性句子均有规则 ID，且每条规则均能反向定位原文。无法反向定位、无摘录、把多条规则合成一句，均视为需求拆解失败。

## 最小测试点计数契约（case-count 1.0）

在 `full` 或 `generation_review` 模式下，必须启用 `quality_contract_version: "1.0"`；只有显式 `l2_only` 才允许豁免。原子规则不得只填写“已覆盖”或由作者自行估算数量。每条 `covered` 原子规则必须声明 `coverage_characteristics`，并绑定到规范性条款账本中的 PRD 事实：

```yaml
quality_contract_version: "1.0"
coverage_characteristics:
  profile: text_input
  applicability:
    input: true
    enum: false
    comparison: false
    long_flow: false
    state: false
    permission: false
    query: false
    batch: false
    file: false
    calculation: false
    time: false
    api: false
    compatibility: false
  required: true
  valid_partitions: [chinese, english, digits, mixed, special]
  invalid_partitions: [empty, whitespace, invalid_format]
  boundaries: {min: 1, max: 100}
```

最低用例数为 `N_min = Σ|U_i|`，最终数量为 `N_final = N_min + N_extra`。普通规则的 `U_i` 至少包含主正向单元；输入框增加有效等价类、空/空格/非法格式和**已声明**边界；枚举逐项覆盖**原文声明**的默认值和选项；判定表覆盖**原文声明**的结果分支、基准和阈值；长流程覆盖**原文声明**的主链路、状态迁移、备选、中断、取消、恢复/重试和终态持久化；状态机覆盖**原文声明**的允许及禁止迁移；权限按**原文声明**的角色×操作×数据范围展开；查询、批量、文件、计算、时间、API、跨端按各自**原文事实**展开。关键词不能单独触发测试维度，未声明的最大值、超限、空文件、部分失败、零值、舍入和跨端一致性不得擅自生成。额外用例必须写 `extra_case_reason`，且不能重复已有语义身份。

执行者必须为每个义务、测试点和最终用例写相同的 `coverage_unit_signature`。脚本独立重算并核对签名集合，输出规则级计算明细；`units` 只能作为完整签名账本，必须与独立计算完全相等并逐项引用 `source_fact_refs`；只增加重复用例、只修改 `calculated_minimum` 或少声明适用维度都不能通过。
