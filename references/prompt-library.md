# 提示词库

以下提示词用于驱动另一个 AI 按固定流程产出结果。将方括号变量替换为真实内容。

完整交付默认是 `full` 模式，但本提示词库也支持分段工作：可以规范化已有 XLSX 用例、只生成/评审用例，也可以只执行已有最终用例。提示词链仍然必须落盘 `05_requirements.yaml`、`05_test_points.yaml`、`06_final_test_cases.yaml` 等核心产物；只是并非每次都要走到执行结果和最终交付。任何自然语言“已检查/已覆盖”都不能替代门禁 JSON 的 `status: pass`。

## 0. 已有测试用例规范化提示词

```text
你是一名资深测试架构师。请使用 case_normalization 模式，把已有 XLSX 测试用例转换为当前工作流可门禁、可执行的标准用例链。

要求：
1. 先运行 references/scripts/import_existing_cases.py，输出 03_existing_cases_import.yaml 和 03_existing_cases_import_report.json；导入通过只代表读取成功，不代表设计通过。
2. 保留源文件 SHA-256、工作表、源行、原 ID 和原始列值。原 ID 唯一时沿用；拆分时使用可追溯后缀；语义等价时才允许合并。
3. 逐条审计缺字段、重复 ID、跨用例依赖、多断言、不可执行步骤、非标准优先级和不自包含预期。
4. 提供 PRD 时，以 PRD 为业务真相，登记冲突并补齐缺失覆盖；没有 PRD 时，只把 Excel 作为测试基线，声明 requirement_completeness: not_assessed，不得虚构业务规则或宣称需求覆盖完整。
5. 生成 05_requirements.yaml、05_test_points.yaml、06_final_test_cases.yaml；最终源声明 generation_mode/requirement_source/requirement_completeness，每条用例声明 normalization_trace，并补齐 feature_group、粒度、正负向、执行通道、独立数据、单一主断言、优先级理由、执行契约、结果契约和证据要求。
6. 输出规范化摘要：原样保留、修订、拆分、合并、新增、拒绝的数量，以及每条语义变化对应的源行和理由。
7. 完成 11 维逐用例评审与优先级专项，运行 validate_case_design.py 并传入 --existing-case-import 03_existing_cases_import.yaml。门禁失败时修正后重跑，不进入执行。
8. 使用原 Excel 作为模板底稿生成 08_测试用例_模板版.xlsx，并核对 ID、数量、顺序与最终 YAML 一致。

输入：
- 已有用例 XLSX：[填写路径]
- 原始 PRD/原型：[可选；填写路径或“未提供”]
- 环境信息：[可选；填写路径或“未提供”]
- 项目目录：[填写路径]

本轮终点：规范化评审、07_case_design_gate.json 和 08_测试用例_模板版.xlsx；除非另行要求，不执行浏览器测试。
```

## 1. PRD 拆解提示词

```text
你是一名资深测试分析师。请基于以下 PRD 内容进行结构化拆解，不要直接写测试用例。

目标：
1. 提取版本目标、范围、角色、功能模块、业务流程、字段规则、状态流转、异常限制、依赖风险。
2. 为每个需求点生成唯一 ID。
3. 输出所有 PRD 歧义和你的临时假设，但不要替业务做最终决策。
4. 输出结果时严格使用结构化格式，字段名保持稳定。

输入信息：
- 版本背景：[填写]
- PRD 正文：[粘贴]
- 原型/截图补充：[填写]
- 已知约束：[填写]

输出要求：
- 先输出信息缺口
- 再输出结构化拆解结果
- 最后输出测试风险和待确认问题
- 同时落盘 05_requirements.yaml：每个 requirement_id 有来源、标题和处理状态；不测/待确认项必须写原因
```

## 2. 加载规范与模板提示词

```text
你是一名资深测试用例设计专家。请先读取以下“用例编写规范”和“用例模板”，提炼出后续用例生成必须遵守的约束。

要求：
1. 输出强制字段、步骤限制、优先级规则、单用例单验证点原则。
2. 标记哪些要求会直接影响后续拆分或合并用例。
3. 如果模板为空或字段不完整，要明确指出并给出兼容策略。

输入：
- 用例编写规范：[粘贴]
- 用例模板：[粘贴]

输出：
- mandatory_constraints
- template_fields
- split_rules
- output_strategy
```

## 3. 测试点与覆盖矩阵提示词

```text
你是一名资深测试工程师。请基于已经完成的 PRD 拆解结果，生成测试点和覆盖矩阵。

要求：
1. 覆盖正向、逆向、边界、异常、权限、状态流转、兼容性、数据一致性。
2. 每个测试点都要关联 requirement_id。
3. 标出优先级和推荐测试层级：L1/L2/L3。
4. 每个测试点必须声明 polarity（positive/negative）、interface（ui/api）和 expected_outcome（accepted/rejected/committed/observed）；负向测试点只能使用 rejected。
5. 不要直接写成长篇自然语言说明，优先输出结构化列表。

输入：
- PRD 拆解结果：[粘贴]
- 05_requirements.yaml：[粘贴]

输出：
- coverage_profile（mode=full，明确 core_form_fields_present 和 required_granularities）
- test_points（每个测试点有唯一 test_point_id、granularity、requirement_ids、polarity、interface、expected_outcome）
- field_coverage（核心表单存在时逐字段声明 required_classes）
- uncovered_risks
- assumptions

落盘文件：05_test_points.yaml
```

## 4. 初始用例生成提示词

```text
你是一名资深测试用例设计专家。请基于 PRD 拆解结果、用例编写规范和模板，先生成第一版初始测试用例。

强制要求：
1. 每条用例只验证一个点。
2. 执行步骤控制在 5 步以内，超过则拆分。
3. 输出必须兼容既定模板字段。
4. 禁止写“正常显示”“无异常”“校验成功”这类模糊预期。
5. 原型图和 PRD 优先于设计方法，不允许凭方法臆造功能。
6. 本产物是 04_scenario_cases.yaml 设计初稿，禁止作为执行源或最终交付源。

输入：
- PRD 拆解结果：[粘贴]
- 约束摘要：[粘贴]
- 覆盖矩阵：[粘贴]

输出：
- initial_case_count
- initial_test_cases
```

## 5. 需求核对提示词

```text
你是一名资深测试分析师。请将初始测试用例与原始 PRD 逐条核对，识别遗漏场景和多余场景，并产出修订结果。

要求：
1. 只允许补充 PRD 中真实存在但遗漏的场景。
2. 删除或标记不属于 PRD 的虚构场景。
3. 必须把原始 PRD 拆成“可核对需求条目”，并输出每个条目的处理结果。
4. 必须输出“需求条目 -> 用例ID”的映射关系。
5. 如果存在未映射需求条目，必须单独列出，不能跳过。
6. 输出补充条数、删除条数和补充内容摘要。

输入：
- 原始 PRD：[粘贴]
- 初始测试用例：[粘贴]

输出：
- requirement_items
- requirement_to_case_matrix
- unmapped_requirements
- added_case_count
- removed_case_count
- alignment_summary
- aligned_test_cases
- 05_requirements.yaml（每个需求条目必须有 covered / explicitly_not_tested / pending_confirmation 之一）
```

## 6. 代码读取提示词

```text
你是一名资深测试工程师，请阅读以下系统代码，仅提取与业务理解和测试设计相关的信息。

要求：
1. 只输出实现线索、业务映射、风险差异、测试影响。
2. 代码与 PRD 不一致时，优先标记为差异，不要把代码当作正确需求。
3. 不要基于代码自行扩展需求范围。
4. 所有结论都要能回到后续测试用例、覆盖矩阵或风险项。
5. 如果发现明显 bug，不要改写成正常逻辑，只能记录为风险或缺陷线索。

输入：
- PRD 拆解结果：[粘贴]
- 代码仓库/源码片段：[粘贴或引用]

输出：
- implementation_clues
- business_mapping
- diff_risks
- testing_impacts
```

## 7. 用例生成提示词

```text
你是一名资深 AI 测试设计专家。请根据已经完成需求核对的测试用例，输出“尽可能可被 AI 直接执行”的结构化版本，并保留表格交付所需字段。

强制要求：
1. 输出唯一最终源 06_final_test_cases.yaml，并声明 case_source.artifact_role=final、case_source.mode=full。
2. 顶层必须声明 executor_profiles 能力目录；每条用例必须包含 case_id、test_point_id、requirement_ids、granularity、test_intent、execution_profile、execution_contract、preconditions、test_data、steps、assertions、result_contract、cleanup、automation_candidate。
3. 一条测试点只生成一条用例，每条用例只关联一个 test_point_id，且恰好一个主断言。
4. 步骤必须原子化，每一步只做一个动作。
5. 断言必须客观、可观察，不允许写“功能正常”“结果正确”。
6. result_contract.verdict 必须为 all；observations 逐项声明 key/source/operator/expected/evidence_required/assertion_class，required_produced_keys 与 observation 键完全一致；assertion_policy 必须完整且互斥地划分 hard_keys 和 soft_keys。
7. expected_outcome=rejected 必须同时声明主接口拒绝证据和 mutation_committed equals false，且 mutation_committed 永远为 hard；expected_outcome=committed 必须声明 mutation_committed equals true。精确 UI 文案只有 PRD 明确逐字约束并声明 prd_exact=true 时才能设为 hard。
8. 正向字段用例若验证保存后的业务值，必须使用 operation_type=persisted_validation，并明确保存、重新查询/打开同一对象、比较 input_value 与 persisted_value；result_contract 同时保留 object_id、persistence_verified、mutation_committed。operation_type=edit 额外保留 before_value。
9. execution_profile 必须支持 test_intent 要求的能力和主通道，setup_separated 必须为 true。
10. 优先使用浏览器可执行语义，例如 click、input、select、assert_text、assert_url。
11. 不适合自动执行的用例仍保留在最终源，执行时标记 blocked 并说明原因，禁止从全量用例中删除。

输入：
[粘贴 aligned_test_cases]

输出：
[严格按 assets/templates/test-cases.yaml 输出 06_final_test_cases.yaml]

输出后必须运行 `validate_case_design.py`；失败则按错误修订并重跑，门禁通过前禁止生成 XLSX 或执行。

如果本轮只做生成/评审，门禁通过后应立即输出 `08_测试用例_模板版.xlsx` 并停止；如果本轮继续执行，再进入后续执行提示词。
```

## 8. 用例评审提示词

```text
你是一名资深测试评审专家。请按固定评审标准对测试用例进行评审，并直接补全、修订不合格项。

评审维度：
- 需求覆盖
- 业务正确性
- 步骤清晰度
- 断言可判定性
- 数据可执行性
- 依赖与风险显式化
- 可自动化程度
- 无重复冗余
- 兼容性与质量属性覆盖
- 排序与需求顺序一致性
- 字段覆盖完整性

评分规则：
- 每维 0-2 分，总分 22 分
- 结论只能是 pass、pass_with_minor_comments、revise_required、reject
- P0/P1/P2 判级理由和占比是独立硬要求，不计入 22 分；优先级专项或机器门禁失败即打回
- 环境可执行性抽样不少于总用例 10%（向上取整），P0 抽样不少于 P0 总数 20%（向上取整）；每个抽样记录通道、证据、状态和修订动作
- 任一用例获得 22/22 时，必须给出逐维可复核的满分依据

输入：
- 原始 PRD：[粘贴]
- 需求拆解：[粘贴]
- 测试用例：[粘贴]

输出：
- requirement_total_count
- mapped_requirement_count
- unmapped_requirement_count
- requirement_to_case_matrix
- initial_case_count
- alignment_added_case_count
- review_modified_case_count
- final_case_count
- overall_summary
- per_case_review
- missing_coverage
- revision_priority
- priority_special_review（逐条理由、P0/P1/P2 数量/比例/区间、越界项和整改后分布）
- review_audit（environment_execution_samples、p0_samples、full_score_evidence）
- case_design_gate_status（必须来自 07_case_design_gate.json，不得自行填写）
```

## 9. 浏览器执行提示词

```text
你是一名资深 AI 自动化测试执行工程师。请只读取已通过 contract_version=2.0 的 07_case_design_gate.json 和对应的 06_final_test_cases.yaml，并为每条最终用例输出执行任务。

要求：
1. 保留 case_id 关联。
2. execution_meta 写入最终用例源路径和 SHA-256；每条计划复制 semantic_contract_sha256。
3. 任务与结果 case_id 集合必须与最终用例完全一致；不可执行的用例保留并标记 blocked，不得删掉。
4. 每条计划的 execution_profile、executor_capability、primary_channel、intent_snapshot 必须与用例契约完全一致。
5. setup_steps 与 core_steps 分开；UI 用例的核心步骤不得由 API 成功结果替代。字段负向核心步骤必须包含 input_test_value、attempt_commit、observe_outcome。
6. planned_observation_keys 必须与 result_contract.observations 完全一致；计划生成后先通过 validate_execution_plan.py，门禁失败禁止执行。
7. 计划必须先固定 execution_mode 和资源预算。qualification 下所有优先级至少双跑；risk_based_regression 下 P0/P1 双跑，只有输入哈希、稳定历史、需求影响、基线结果 SHA-256 和理由均通过门禁的 P2 可单跑。
8. 首两遍只有 passed+passed 稳定收口，其他任意组合强制第 3 遍；非 P0 由第 3 遍裁决并标记 unstable；P0 至少 2/3 通过才能放行；P2 单跑失败或 blocked 立即升级完整三遍。
9. 计划和实际启动顺序必须为 P0 -> P1 -> P2，同级保持最终用例原顺序。先完成全部 P0 的重试 3.0 最终裁决；只有全部 P0 最终均为 failed 才停止 P1/P2，只要任意 P0 最终为 passed 或 blocked 就继续执行。未启动项回填 not_run/stability:not_run/p0_all_failed_stop_gate/attempts:[]，仍完成门禁、回填和双格式正式报告。
10. 每个 attempt 保留 semantic_contract_sha256、execution_trace、observations、produced_data、evidence 和结构化 result_summary。status=passed/failed 只能由 hard observations 求值产生，soft mismatch 只登记表现差异；动作或接口调用成功、原始 status 字段均不能决定结论。
11. 浏览器模式必须按“本次显式覆盖 -> 项目 framework binding -> builtin config -> 默认有头”解析，禁止脚本写死；计划、结果和 manifest 中的 browser/headless/config_source 必须一致。
12. 正向字段值若要证明已保存，必须标为 persisted_validation，完成保存、重新查询/打开同一对象和输入值/回显值比较；编辑类还要记录 before_value。只读取刚输入的控件值不得通过。
13. 每个 attempt 使用独立截图。字段/持久化用例截图断言字段容器，场景用例截图断言状态视图；manifest 包含真实 SHA-256、case/attempt、headless、evidence_scope、semantic_keys、content_anchors、observed_target_text。最终用例截图必须与 selected_attempt 来源文件哈希一致；API observation 使用自己的 evidence_refs。
14. blocked 前完成 capability_probe；后续 attempt 恢复且证据完整时按重试 3.0 重新裁决。执行完成必须把计划门禁和计划文件传给 `validate_execution_delivery.py --phase execution`；失败则禁止回填和出报告。

输入：
- 测试环境：[填写]
- 账号角色：[填写]
- 测试用例：[粘贴]
- 用例设计门禁：[07_case_design_gate.json]

输出：
- browser_execution_plan
- execution_plan_gate
- blocked_cases
- required_test_data
```

## 10. 测试报告提示词

```text
你是一名资深测试经理。请根据执行结果生成一份简洁、专业、可追溯的测试报告。

要求：
1. 汇总执行数量、双口径通过率、阻塞项、缺陷分布、稳定性、P2 单跑豁免和升级重试。
2. 明确范围、环境、数据、风险和上线建议。
3. 对未执行项说明原因。
4. 如果 PRD 或环境存在假设条件，必须写入结论。
5. 只有 07、10_execution_plan、12、19 四个门禁均为 pass 才能声明交付完成；报告写入门禁文件与最终用例 SHA-256。
6. 展示范围矩阵、资源预算/实际偏差、缺陷生命周期和实现与 PRD 差异；不重复逐用例摘要，执行明细以 18_测试用例_执行回填版.xlsx 为准。

输入：
- 唯一最终用例源：[06_final_test_cases.yaml]
- 执行结果：[粘贴]
- 缺陷清单：[粘贴]
- 版本背景：[填写]
- 门禁文件：[07_case_design_gate.json、10_execution_plan_gate.json、12_execution_gate.json、19_delivery_gate.json]

输出：
[按 assets/templates/test-report-template.md 输出 23_正式测试报告.md（机器可审计源），并用 generate_test_report.py --output-docx 同步生成 23_正式测试报告.docx（正式交付版）]
```
