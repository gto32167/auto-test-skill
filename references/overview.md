# 方案总览

## 目标与模式

本工作流把当前项目的需求、最终用例、执行计划、逐次结果、证据和交付物绑定为同一条可验证版本链。

支持三种模式：

| 模式 | 必要输入 | 本轮终点 |
|---|---|---|
| `case_normalization` | 已有测试用例 XLSX；PRD 可选 | 导入审计、规范化最终用例、用例设计门禁、评审文档、模板版 Excel |
| `generation_review` | PRD | 用例设计门禁、评审文档、模板版 Excel |
| `execution_only` | 已通过门禁的最终用例、环境信息 | 执行结果、回填版、正式报告 |
| `full` | PRD、环境信息 | 全部标准产物 |

代码、数据库和接口文档是可选输入。缺少代码或接口文档只跳过相应可选轨道；缺少可运行环境会阻塞功能执行，但不能伪造执行结果。

## 阶段路由

只读取当前阶段需要的参考文档，不要在开始时加载全部资料。

| 阶段 | 主要产物 | 需要读取 |
|---|---|---|
| 已有用例导入与规范化 | `03_existing_cases_import.yaml`、导入报告、规范化审计 | `existing-case-normalization.md`、`case-design.md` |
| 输入与 PRD 拆解 | `prd_analysis.md`、需求缺口 | `prd-decomposition.md` |
| 测试点和最终用例 | `05_requirements.yaml`、`05_test_points.yaml`、`06_final_test_cases.yaml` | `case-design.md`、`semantic-contract-gates.md` |
| 用例评审 | 评审结论、11 维评分 | `review-standard.md` |
| 设计门禁与模板版 | `07_case_design_gate.json`、`08_测试用例_模板版.xlsx` | `delivery-gates.md` |
| 可选代码分析 | 实现线索或 `code_review_report.md` | `code-analysis.md` |
| 可选接口测试 | `api_testing_report.md` | `api-testing.md` |
| 框架运行副本 | 绑定文件、运行目录 | `framework-integration.md` |
| 执行计划与执行 | 计划、逐 attempt 结果和证据 | `browser-execution.md`、`semantic-contract-gates.md` |
| 排障 | 稳定选择器、登录、弹窗、H5 键盘 | `execution-playbook.md` |
| 回填和报告 | 回填版 Excel、Markdown、Word | `delivery-gates.md` |

## 标准流程

1. 读取当前项目输入，记录输入清单和缺口；输入是已有 XLSX 时先运行确定性导入并保留来源追踪。
2. PRD 模式拆解角色、模块、流程、字段、状态、异常和风险；已有用例模式审计原用例并按是否提供 PRD 决定覆盖口径。
3. 生成需求条目、L2 场景测试点和 L3 字段/边界测试点。
4. 生成唯一最终用例源并完成 11 维评审与优先级专项评审。
5. 运行用例设计门禁；通过后生成模板版 Excel。生成/评审模式可在此停止。
6. 需要执行时，生成执行计划并运行计划门禁。
7. 完成环境就绪检查，按 P0、P1、P2 稳定顺序执行。
8. 每个 attempt 保存语义哈希、执行轨迹、结构化观察值、业务数据和证据。
9. 运行执行结果门禁；通过后生成回填版 Excel。
10. 运行最终交付门禁；通过后生成 Markdown 审计源和 Word 正式报告。

## 唯一产物链

```text
03_existing_cases_import.yaml      # 仅 case_normalization 模式，分析输入
03_existing_cases_import_report.json
  ->
05_requirements.yaml
  -> 05_test_points.yaml
  -> 06_final_test_cases.yaml
  -> 07_case_design_gate.json
  -> 08_测试用例_模板版.xlsx
  -> 10_browser_execution_plan.yaml
  -> 10_execution_plan_gate.json
  -> 11_test_execution_results.yaml
  -> 12_execution_gate.json
  -> 18_测试用例_执行回填版.xlsx
  -> 19_delivery_gate.json
  -> 23_正式测试报告.md
  -> 23_正式测试报告.docx
```

`04_scenario_cases.yaml` 只是 L2 初稿。只有声明 `case_source.artifact_role: final`、`case_source.mode: full` 且通过设计门禁的 `06_final_test_cases.yaml` 可以进入模板生成和执行。

`03_existing_cases_import.yaml` 也只是规范化输入。导入报告通过不等于用例设计通过，不能直接进入执行。

## 阶段停止规则

- 用户仅要求生成或评审时，交付模板版 Excel 和评审产物后停止。
- 用户仅要求规范化已有用例时，在规范化评审、设计门禁和模板版 Excel 完成后停止。
- 任一核心门禁失败时，修正对应输入后重跑，不进入下一阶段。
- 环境不可执行时保留计划和阻塞依据，不伪造正式执行结果。
- 先完成全部 P0 的最终裁决；仅当全部 P0 最终均为 `failed` 时停止 P1/P2。
- P0 停止门禁触发后，低优先级用例仍以 `not_run/p0_all_failed_stop_gate` 进入结果、回填和正式报告。
- 业务用例失败不等于产物门禁失败。真实失败可以进入可信报告。

## 标准交付角色

| 产物 | 角色 |
|---|---|
| `test_case_review.md` | 评审结论 |
| `test_case_review_scores.md` | 11 维逐用例评分和优先级专项 |
| `08_测试用例_模板版.xlsx` | 生成/评审阶段正式用例交付 |
| `11_test_execution_results.yaml` | 完整执行事实源 |
| `18_测试用例_执行回填版.xlsx` | 人工复审的逐用例事实来源 |
| `23_正式测试报告.md` | 机器可审计报告源 |
| `23_正式测试报告.docx` | 正式报告 |

模板、字段和命令行参数以 `assets/templates/` 与 `delivery-gates.md` 为准。本文件只负责阶段路由，不复制各专项规则正文。
