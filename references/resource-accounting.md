# 资源核算与上下文效率

## 目标

本规则解决两个问题：准确回答一次工作流花了多长时间、消耗了多少模型 Token；减少重复读取和重复模型推理，但不牺牲用例覆盖、重试、证据、门禁或报告质量。

每个新运行在项目根目录维护一份 `20_workflow_run_metrics.json`。该文件是时长和 Token 的唯一事实源，计划与执行结果只保存引用和摘要，禁止把完整账本复制进大型 YAML、回填 Excel 或提示词。

## 核算边界

收到执行请求并确认项目目录后立即初始化运行；在本轮请求达到合法终点后结束运行。`generation_review`、`execution_only` 和 `full` 都必须统计。

账本同时记录：

- 总历时：开始到结束的墙钟时间。
- 暂停/外部等待：用户要求暂停、发版等待、服务器故障或等待外部恢复的时间。
- 实际活动时长：总历时减去显式暂停时间。
- 分阶段耗时：需求拆解、用例设计、评审、执行计划、浏览器执行、回填、报告。
- Token 构成：输入、输出、缓存输入、推理和总 Token。

阶段名固定使用 `requirements`、`design`、`review`、`plan`、`browser_execution`、`backfill`、`report`。确定性脚本阶段使用 `deterministic`；包含模型判断的阶段使用 `model` 或 `mixed`。

暂停时必须执行 `pause` 并写明原因，恢复后执行 `resume`。不要让一个模型阶段跨越长时间的外部暂停；先结束或暂停当前阶段，避免把等待时间误认为模型处理时间。

## Token 口径

实际 Token 只接受可核验的精确来源：

- 模型服务响应中的 `usage`。
- 平台导出的 usage 记录。
- Codex/执行平台提供的用量快照。
- 从上述来源逐字抄录的精确数值。

`total_tokens = input_tokens + output_tokens`。缓存输入已包含在输入 Token 中，推理 Token 已包含在输出 Token 中，二者只作构成披露，不能重复累加。

统计状态固定为：

| 状态 | 含义 |
|---|---|
| `exact` | 所有模型阶段、所有模型调用均有精确 usage |
| `partial` | 只取得部分调用的精确 usage；仅展示已测总数 |
| `unavailable` | 本运行存在模型阶段，但执行宿主没有暴露可核验 usage |
| `not_applicable` | 运行只有确定性脚本，没有调用模型 |

`unavailable` 不等于 0。估算值只能写入计划的 `estimated_model_tokens`，不能写成实际消耗。模型阶段标为 `unavailable` 时必须写明原因；标为 `partial` 时应说明哪些调用没有被覆盖。

## 标准命令

以下命令中的 `--run-id` 可省略，此时工具使用账本中的最新运行。建议保存 `init` 输出的运行 ID，并在门禁和报告命令中显式传入。

```powershell
$metrics = "<项目目录>\20_workflow_run_metrics.json"
$metricsTool = "<技能目录>\references\scripts\workflow_run_metrics.py"

py -3 $metricsTool init --file $metrics --project-dir "<项目目录>" --mode full
py -3 $metricsTool start-stage --file $metrics --stage requirements --executor model
```

模型响应带 usage 时逐次登记：

```powershell
py -3 $metricsTool record-usage `
  --file $metrics `
  --stage requirements `
  --source provider_response `
  --usage-json "<模型响应或usage JSON路径>" `
  --model "<模型名>" `
  --call-id "<调用ID>"

py -3 $metricsTool finish-stage `
  --file $metrics `
  --stage requirements `
  --status completed `
  --token-coverage exact
```

执行宿主未提供 usage 时：

```powershell
py -3 $metricsTool finish-stage `
  --file $metrics `
  --stage requirements `
  --status completed `
  --token-coverage unavailable `
  --unavailable-reason "当前执行宿主未暴露模型 usage 元数据"
```

暂停、恢复和结束：

```powershell
py -3 $metricsTool pause --file $metrics --reason "服务器故障，等待恢复通知"
py -3 $metricsTool resume --file $metrics
py -3 $metricsTool finalize --file $metrics --status completed
py -3 $metricsTool validate --file $metrics
```

内置浏览器框架会自动登记 `browser_execution` 确定性阶段。如果它独立运行，会自动创建并结束 `execution_only` 运行；如果外层已有活跃工作流，则只追加浏览器阶段。运行副本的 `config.yaml` 会指向项目根目录的账本。

## 上下文读取规则

1. 启动时只读取 `SKILL.md`、`references/overview.md` 和当前阶段路由到的参考文档，不加载全部 `references/`、全部模板或历史项目。
2. PRD 原文只解析一次并落成 `05_requirements.yaml`；后续优先读取结构化条目和当前待处理切片，需要核对原文时再按页或章节回读。
3. 用例设计按 `feature_group` 分块处理，但最终用例、映射和优先级分布必须由确定性门禁做全局复算。分块不是缩小覆盖。
4. 执行时只加载目标用例、对应计划项、契约哈希和当前 attempt。目标重跑不重读全部历史结果，只读取该用例的已有 attempts 和选中证据。
5. 原始 DOM、网络包、截图、日志和大 YAML 保存在文件中。模型只读取当前断言区域、失败差异、必要日志片段或短摘要，不把完整 DOM/二进制/全量日志反复放入上下文。
6. 同一轮浏览器用例使用稳定运行副本按优先级批量驱动。不得为了每条用例分别重开一个模型会话或重复加载整套方案。
7. 只有歧义判断、业务推理、用例设计、失败归因和结论编制交给模型。哈希、计数、排序、Excel 生成/回填、账本聚合、门禁复算和 Word 渲染使用现有确定性脚本。
8. 阶段结束时形成小型 handoff，只保留运行 ID、输入哈希、产物路径、门禁状态、变更 ID、未决问题和下一阶段需读文件。不要复制上一阶段全文。

## 复用与缓存边界

允许按“输入 SHA-256 + 脚本版本 + 参数”复用确定性产物，例如解析索引、文件哈希、Excel 渲染和门禁复算结果。输入或脚本版本变化后必须失效。

新项目不得复用历史项目的业务结论、账号假设、登录态、订单、商品、截图、阻塞原因或通过结果。风险回归中的历史基线仍必须满足既有基线资格门禁；资源优化不能绕过它。

## 超预算处理

计划可填写 `estimated_model_tokens`；没有可靠历史基线时填 `null`。接近或超过预算时：

1. 先完成当前原子阶段并写入产物、账本和 handoff。
2. 结束冗长上下文，在新上下文中只读取 handoff 与下一阶段必需文件。
3. 停止重复解释、重复格式化和全量重读，继续剩余质量流程。
4. 在报告中披露预计/实际偏差和未完整采集的 Token 阶段。

不得通过减少测试点、降低 P0/P1/P2 约束、跳过双跑/第三次裁决、减少截图、弱化断言、跳过复核或绕过门禁来节省 Token。Token 预算是资源观测和上下文切换信号，不是质量停止条件。

## 门禁与报告

新版计划声明 `resource_accounting_version: "1.0"` 后，必须同时声明 Token 预算策略。新版执行结果必须引用账本路径和运行 ID。执行门禁要求浏览器阶段已结束；交付门禁和正式报告要求工作流已结束。精确 Token 不可取得不会让业务测试失败，但缺少统计状态、伪造 0、账本无效或运行 ID 不一致会导致门禁失败。

新项目必须给 `validate_execution_plan.py` 和 `validate_execution_delivery.py` 加 `--require-resource-accounting`，此时缺少 `execution_meta.resource_accounting_version` 直接失败。资源核算契约之前产出的历史计划/执行结果不带该开关仍按兼容方式通过，载荷里以 `resource_accounting.enforced=false` 与 `legacy_mode=true` 留痕并在 warnings 提示；兼容通过不等于资源核算合规。

正式报告展示总历时、暂停时间、活动时长、分阶段耗时、Token 状态、已测总数、精确总数和未覆盖原因。完整事件明细只留在 `20_workflow_run_metrics.json`。

交付前先结束需计入账本的阶段并冻结账本。如果最终核算状态或结果元数据变化，更新执行结果的账本引用并重新执行执行门禁及回填，然后跑交付门禁。正式报告按最终交付门禁绑定的账本 SHA-256 复验，门禁通过后修改账本需要重新校验。最终报告渲染后不再向已冻结账本追加事件。
