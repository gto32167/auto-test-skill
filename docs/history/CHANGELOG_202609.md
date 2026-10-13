> 此文为 V3 来源方案的历史记录，保留演进背景；其中旧路径、项目结论和验证数字不代表本次升级的验证结果。当前结果见 `docs/maintenance/V3升级分析与验证.md`。

# 2026 年 9 月质量机制变更记录

> 本文件只用于追溯升级背景、实现范围和验证结果。当前执行规则以 `SKILL.md`、`references/` 和门禁脚本为准。

## 2026-09-04：上下文节流与运行资源核算 1.0

### 背景

历史完整交付存在模型上下文反复加载、确定性产物重复交给模型处理、无法区分外部等待和实际执行、Token 只能事后估算等问题。直接减少用例、重试或证据虽然能降低消耗，但会破坏测试质量，因此未采用。

### 变更

- 新增 `references/resource-accounting.md`，把按阶段读取、确定性脚本分工、目标用例切片、阶段 handoff、缓存边界和超预算 checkpoint 作为统一规则。
- 新增 `20_workflow_run_metrics.json` 账本及 `workflow_run_metrics.py` CLI，记录多次运行、阶段、总历时、暂停/外部等待、实际活动时长和 Token usage 事件。
- Token 只接受模型服务、平台导出或用量快照中的精确值；缓存输入和推理 Token 不重复计入总数。缺失时使用 `partial` 或 `unavailable`，禁止估算冒充实际消耗。
- 内置 `run_tests.py` 自动登记 `browser_execution/deterministic` 阶段；项目运行副本自动绑定项目根目录账本并包含运行入口。
- 执行计划模板新增资源核算版本、可空的预计模型 Token 和“质量约束不得降低/超预算先 checkpoint”策略。
- 执行结果只保存账本路径、运行 ID 和摘要，不复制完整事件，避免继续放大 2 MB 级结果文件。
- 计划门禁校验预算策略；执行/交付门禁校验账本、运行 ID、浏览器阶段和 Token 状态；正式 Word/Markdown 报告展示总时长、暂停、活动时长、阶段耗时和 Token 状态/数量。
- 修正 `not_run` 回填未展示“阻塞/未执行原因”的旧问题，使 P0 全失败停止后的结果可由人工直接理解。

### 为什么这样改

- 节流集中在重复读取、重复推理和重复格式化，不触碰全量覆盖、优先级比例、双跑/第三遍、截图、三态判定和四道门禁。
- 独立 JSON 是单一事实源，避免同一统计在计划、执行结果、Excel 和报告中多份维护并发生漂移。
- 精确、部分、不可用和不适用四态可防止“拿不到 usage 就填 0”的错误结论。
- 总历时与暂停时间分离后，可以区分真实执行成本和发版、服务器故障、等待通知等外部耗时。

### 兼容性

历史计划/执行结果没有 `resource_accounting_version` 时继续按旧格式通过门禁，并输出兼容警告；不会要求修改历史项目或伪造历史 Token。新模板生成的项目启用 1.0 后，资源账本和运行 ID 属于硬门禁。**2026-09-13 补充**：该兼容通道已显式化——新项目向两个门禁传 `--require-resource-accounting` 后，缺失该字段即失败；不带开关时行为不变，并以 `resource_accounting.enforced=false` 留痕，兼容通过不冒充契约合规。

### 验证

- `test_run_metrics.py`：10 项通过，覆盖精确/部分/不可用/不适用、暂停、篡改检测、自动执行器接入、报告展示和运行副本绑定。
- `test_workflow_gates.py`：58 项通过，覆盖原有设计、计划、执行、回填和报告链及新版资源门禁。
- Skill `quick_validate.py`：通过。
- 历史项目 `Hishop_Himall_S2B2B-批量下单` 只读兼容检查：104 条用例的计划门禁和执行结果门禁均为 `pass`，各有一条未启用资源核算的兼容警告。

## 2026-09-13：资源核算契约显式强硬化

### 背景

`validate_execution_plan.py` 与 `validate_execution_delivery.py` 在缺少 `execution_meta.resource_accounting_version` 时只输出一条兼容警告就放行（`validate_execution_plan.py:131-134`、`validate_execution_delivery.py:772-775`）。2026-09-04 把这一点写成了有意兼容——历史项目不要求改造——但它同时意味着**新项目漏写该字段也会静默通过**，而且 `pass` 的载荷里看不出本轮究竟有没有启用资源核算。同类问题的另一半（`case_count_contract` 照文档声明却空转）已于同日修复，本次把执行/交付两处按同一标准补齐。直接改成“缺失即失败”不可取：会打断历史项目的只读复跑，违背 2026-09-04 的兼容承诺。

### 变更

- 两个门禁新增 `--require-resource-accounting` 开关：携带时缺少 `resource_accounting_version` 直接失败，错误文案同时点名该开关和需要补的字段。
- 默认行为保持不变：不带开关时仍是 `pass` + 兼容警告，历史计划/执行结果无需改造即可继续通过。
- 载荷新增 `resource_accounting` 状态对象：`version` / `enforced` / `legacy_mode` / `required_by_cli`（交付门禁另含 `run_id` / `run_status` / `duration_seconds` / `token_usage`）。`enforced=false` 明确表示本轮走兼容路径，**兼容通过不能冒充契约合规**。
- 兼容警告补充引导语，直接给出开关用法。
- 文档同步：`SKILL.md`、`delivery-gates.md`（3 处命令 + 2 段说明）、`semantic-contract-gates.md`（2 处命令）、`resource-accounting.md`、`browser-execution.md`、`prompt-library.md`、`new-project-kickoff-prompt.md` 的标准命令统一追加该开关，并写明新项目强制。

### 为什么这样改

- 历史兼容是 2026-09-04 的明确承诺，保留。
- 但“兼容”与“静默”是两件事：兼容可以留，静默不可以。状态对象让 `pass` 自证是否真的启用了契约。
- 新项目通过文档标准命令默认带上开关，等价于新项目强制启用；存量项目零影响。

### 验证

- `test_workflow_gates.py` 新增 `test_require_resource_accounting_flag_hardens_legacy_artifacts`：同一份契约前 fixture，不带开关 `pass` 且 `enforced=false`，带开关 `fail` 且错误指向缺失字段；计划门禁与交付门禁各验一遍。
- 历史项目 `Hishop_Himall_S2B2B-批量下单` 实测：不带开关计划门禁与执行门禁均为 `pass (0 errors, 1 warnings)`，与本文档 2026-09-04 条目的验证记录一致；带开关两处均 `fail (1 errors)` 并精确给出补齐路径。
- 出厂模板 `assets/templates/browser-execution-plan.yaml` 与 `test-execution-results.yaml` 已声明 `resource_accounting_version: "1.0"` 与 `token_budget_policy`，带开关后仍通过，新项目起手即合规。

## 2026-09-17：变化类证据出厂同步 + 维护纪律与覆盖盲区强化

### 背景

`change_evidence` 强硬化于 2026-09-13 落地 `semantic_contract.py`（当日 17:01 修改），但出厂模板 `test-cases.yaml` 停留在当日 10:09，TC-002（`persisted_validation`）缺少 `change_evidence`，导致 6 项自测失败，且新项目复制出厂模板后会在用例设计门禁即被 `state-changing intent requires change_evidence.required: true` 卡住。同时该强硬化未记录进变更日志，暴露出门禁-模板-日志三者缺乏同步纪律。此外，原文覆盖专项长期只能靠人工回比发现"漏拆"（账本对账只对账本内部自洽）。

### 变更

- **出厂同步（修复漂移）**：`test-cases.yaml` TC-002 补 `change_evidence`（state_transition）与 `before_state/after_state/changed/comparison_verified` 4 个硬观察键；`browser-execution-plan.yaml` TC-002 同步 `planned_observation_keys`/`semantic_keys` 并新增 `before_after` 与 4 个 attempt 前后截图名；`test-execution-results.yaml` TC-002 建模 `change_comparison` 与 phase before/after 证据形状。`test_workflow_gates.py` 的 scenario-edit 用例与三个 fixture 辅助方法改为变化类感知。修复后自测 97/97 通过，出厂三件套直接过设计门禁。
- **维护纪律**：新增 `docs/maintenance/发布检查单.md`——门禁/模板/规则变更必须走"同步出厂模板 → 全量自测 → 出厂模板冒烟门禁 → 补变更日志"四步。
- **原文扫描器**：新增只读辅助脚本 `references/scripts/scan_prd_source_statements.py`（含 8 项自测），把 PRD 原文疑似规范性句子与条款账本摘录比对，输出未命中候选（评审必须逐条 disposition）与"摘录含数值但未声明边界"启发式警告；`review-standard.md` 原文覆盖专项接入该命令。
- **评审会话隔离**：`review-standard.md` 新增要求——评审在新会话中执行，只携带 PRD 原文与三份结构化产物；无法隔离时声明 `same_session_fallback` 降级并留痕。
- **试点用例检查点**：`delivery-gates.md` 新增——P0 批次以成本最低的一条为试点先形成最终裁决，试点 `blocked` 先修环境再重跑，防止系统性故障烧掉整批 attempt 预算；SKILL.md 与使用说明同步。
- **经验回灌**：`--error-report` 的错误类别汇总留存项目目录，同一类别跨项目反复出现时回写生成前自检提示（SKILL.md harvest 原则扩展 + 使用说明 §9）。
- **结果模板锚点拆分**：TC-002/TC-003 每个 attempt 的观察值 `evidence_refs` 拆分为独立锚点，指向该 attempt 自身声明的截图，消除共享锚点导致的跨 attempt 引用错位。

### 为什么这样改

- 出厂模板是新项目的起点，模板与门禁不一致等于第一道门禁必然返工；同步义务必须制度化而不是靠记忆。
- "漏拆"是覆盖面保障的最后盲区：账本自洽校验无法发现从未进入账本的原文条款，启发式扫描 + 强制 disposition 把它变成有清单可查、必须回应的评审输入，同时不做硬门禁以避免误报死锁。
- 试点用例与评审会话隔离都是把既有原则（环境门禁、评审独立性）从"声明"推进到"机制"，成本极低。

### 验证

- 全量自测套件 97 + 8 = 105 项全部通过（含扫描器 8 项）。
- 出厂三件套直接运行 `validate_case_design.py`：`pass (0 errors, 0 warnings)`，`quality_contract.enforced = true`，`N_min = 3 = actual_cases`。
- 结果模板 YAML 解析通过，无悬空锚点，各 attempt 观察值引用指向自身声明的截图。
