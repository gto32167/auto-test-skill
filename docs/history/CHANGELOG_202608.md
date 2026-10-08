# 2026 年 8 月质量机制变更记录

> 本文件只用于追溯升级背景、历史验证和旧项目迁移。当前执行规则以 `SKILL.md` 和 `references/` 下的正式规范为准。

## 2026-08-26：语义一致性门禁 2.0

### 问题

旧执行器可能把“动作成功”误当成“用例通过”，特别是字段负向用例走到了合法创建路径，或只看到提示但没有验证是否发生写入。

### 改动

- 测试点和最终用例增加 polarity、interface、expected outcome 和执行 profile。
- 最终用例增加可求值 observations、hard/soft 分类和语义哈希。
- 新增执行计划门禁，校验 profile、核心步骤、观察键和最终用例版本。
- 每个 attempt 保留语义哈希、执行轨迹、observations、produced_data 和证据。
- 拒绝类用例强制验证 `mutation_committed: false`。
- 修复布尔 `false` 和数字 `0` 被当成空值的问题。

### 真实回归

对 `TC-ZERO-038` 至 `TC-ZERO-041`、`TC-ZERO-048` 至 `TC-ZERO-051` 共 8 条分类/标签字段用例执行 17 个 attempt：

| 判定方式 | 结果 |
|---|---|
| 新语义门禁 | 7 passed / 1 failed |
| 旧动作成功逻辑 | 8 passed / 0 failed |

`TC-ZERO-041` 三次均出现输入 25 字、控件保留 24 字、没有长度提示并保存成功。新门禁根据拒绝证据缺失和 `mutation_committed=true` 稳定判为失败，避免了假阳性。

正式契约见 `references/semantic-contract-gates.md`。

## 2026-08-29：执行质量 3.0

### 问题

历史执行存在重试口径不统一、阻塞与失败混淆、回填只有状态、证据路径不对应真实文件以及评审满分无依据等问题。

### 改动

- 统一 qualification 和 risk-based regression 两种执行模式。
- 首两遍只有双通过稳定收口，其他组合进入第三遍裁决。
- P0 使用 2/3 严格多数；合格 P2 单跑失败或阻塞时升级完整重试。
- 结果统一为 passed、failed、blocked，并输出双口径通过率。
- UI 截图绑定文件 SHA-256、断言时刻、语义键、验证文本和锚点。
- 回填“实际结果”保存业务数据和可定位对象。
- 评审增加环境可执行性抽样、满分依据、资源预算和缺陷生命周期。
- Word 报告成为强制正式交付物，Markdown 保留审计源。

正式执行和证据规则见 `references/browser-execution.md`，命令和门禁见 `references/delivery-gates.md`。

## 2026-08-31：优先级分批与 P0 停止门禁

### 规则纠正

停止条件不是“出现一个 P0 失败”，也不是“某次 P0 attempt 失败”。固定流程是：

1. 按 P0、P1、P2 稳定排序。
2. 完成全部 P0 的重试和最终裁决。
3. 只有所有 P0 最终均为 `failed` 时停止 P1/P2。
4. 任意 P0 为 `passed` 或 `blocked` 时继续。
5. 未启动项写入 `not_run/p0_all_failed_stop_gate`，仍完成回填、门禁和正式报告。

正式规则见 `references/delivery-gates.md` 的 P0 产品质量停止门禁。

## 旧项目迁移

旧项目缺少以下任一内容时，不能只补一个新门禁状态后继续使用旧结果：

- 测试点语义字段和 P0/P1/P2 理由
- `executor_profiles`、`test_intent`、`execution_contract`
- 结构化 `result_contract.observations`
- `10_execution_plan_gate.json`
- attempt 级语义哈希、轨迹、observations、produced_data 和证据
- hard/soft policy、执行模式、资源预算和优先级停止元数据
- UI 真实文件哈希与语义 manifest，或 API 独立 evidence refs

这类项目应从测试点和最终用例重新生成计划与结果。业务用例失败可以进入报告，版本、结构或证据门禁失败不能进入回填和正式报告。

## 自动化验证记录

- 2026-08-29：工作流门禁回归 `36/36` 通过。
- 2026-09-01：精简内置框架和文档后，工作流门禁回归 `47/47` 通过；历史项目报告可在无 Allure HTML 的情况下重新生成 Word。

数字只记录当时验证状态，不作为当前发布标准。每次变更仍应重新运行 `references/scripts/test_workflow_gates.py`。
