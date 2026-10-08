# PRD 拆解方法

## 目标

把自然语言 PRD 转成测试可消费的结构化信息，减少 AI 在后续环节自行脑补。

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
