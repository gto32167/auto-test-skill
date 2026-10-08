# 新项目启动提示词（可直接发给 AI）

> 用法：把下面代码块里的 `[ ]` 占位符替换成你的真实信息，然后把 `{这段提示词}` 发给 AI 即可。
> 可选输入（06/07）有就给、没有就不给，AI 会自动跳过对应执行项，不会中断。

```text
请按"ai-qa-prd-workflow"方案，为我执行一个完整的测试交付流程。

【项目信息】
- 项目名称：{项目名，如 云商城后台管理系统}
- 需求版本：{PRD 版本号}
- 项目目录：{建议目录，如 E:\solutions\ai-qa-prd-workflow\20260823_项目名}

【输入文件（放在项目目录里）】
必选：
- 01_需求原文.docx / .md   ← 完整 PRD 原文
- 04_环境信息.md           ← 测试环境 URL、后台账号/密码、用户账号、验证码、支付密码、浏览器要求
已有用例规范化模式：
- 已有测试用例.xlsx        ← 使用 case_normalization 时必选；PRD 可选但强烈建议提供
- 该模式先生成 03_existing_cases_import.yaml 和导入报告，再进入 05/06/07/08 标准链，原 Excel 不能直接作为执行源
可选（提供什么就执行什么，缺的跳过）：
- 02_代码库信息.md         ← 代码本地路径 或 git 仓库地址 + 分支
- 03_数据库信息.md         ← 数据库类型、连接方式、只读账号、重点表
- 05_待确认问题.md         ← 已知风险点
- 06_代码库信息（若单独有） ← 触发【代码走查】执行项
- 07_接口文档.md           ← OpenAPI/Swagger 文档或接口清单 + API 域名 + 鉴权方式，触发【接口测试】执行项
- 如果不想拆文件，也可以只给一个 00_项目输入总表.md/docx（AI 会先帮你拆分）

【执行要求】
1. 先建项目目录，把输入文件放进去；如果输入是总文件，先拆成结构化输入
2. 按流程依次执行：
   a. PRD 结构化拆解（角色/模块/流程/字段/状态/风险）→ prd_analysis.md
   b. 读取规范和模板 → 04_scenario_cases.yaml（仅设计初稿，禁止执行/交付）
   c. 需求对齐核对 → 05_requirements.yaml，所有需求逐条有处理状态
   d. 用例评审（11 维度 + 优先级专项）→ 评审评分文档；逐条检查判级理由并列出三档数量、比例、允许区间和越界项；环境可执行性抽样不少于总用例 10%，P0 抽样不少于 P0 总数 20%，满分项给出可复核依据，不通过的打回补全
   e. 生成 05_test_points.yaml：场景级 L2 + 字段级 L3；每点声明 feature_group/polarity/interface/expected_outcome/priority/priority_rationale；按“PRD 首次出现顺序→端口→页面/相似功能集合→集合内场景顺序”编排，同一 feature_group 必须连续；1 测试点=1 最终用例、单用例恰好一个主断言
   f. 可选：有代码库则读取代码补充实现线索（不能覆盖 PRD）
   g. 可选：有数据库信息则探测基础数据（账号/商品/配送员/订单基线）
   h. 生成唯一最终源 06_final_test_cases.yaml，声明 case_source.priority_policy=strict_p0_p1_p2 和 case_ordering.policy=prd_port_page_group/group_sequence，包含 executor_profiles、test_intent、execution_contract 和可求值的 observations；每个 observation 声明 hard/soft，assertion_policy 完整分组；拒绝类 mutation_committed=false 永远是 hard；运行用例设计门禁生成 07_case_design_gate.json，priority_policy.status 必须为 pass
   i. 门禁通过后生成 08_测试用例_模板版.xlsx；有用户模板就沿用，未提供也必须按默认 7 列模板生成：用例ID / 功能集合 / 用例名称 / 优先级 / 前置条件 / 执行步骤 / 预期结果
3. 执行阶段（执行项编排）：
   - 环境门禁：登录态探活、权限核对、API 可达、前置构造通道就绪；不通过则不执行
   - 【功能执行】必做：只读取已通过门禁的 06_final_test_cases.yaml；执行计划先通过 10_execution_plan_gate.json，profile/能力/意图/核心步骤/观察键不一致时禁止执行
   - UI 核心动作不得用 API 成功结果替代；计划先固定 execution_mode：qualification 全量至少双跑，risk_based_regression 仅门禁证明合格的 P2 可单跑。首两遍只有双通过稳定收口，其他组合强制第 3 遍；非 P0 由第 3 遍裁决，P0 按 2/3 严格多数，P2 单跑失败/阻塞立即升级完整三遍。保留 attempts/semantic hash/execution_trace/observations/produced_data/evidence
   - 浏览器有头/无头按项目 framework binding 和 builtin config 解析，禁止执行器写死；计划、结果和证据记录实际模式及配置来源
   - 合法字段值要证明已保存时，必须保存、重新查询/打开同一对象并比较输入值和回显值；编辑类同时记录前值、输入值、回显值和对象 ID
   - 每个 attempt 独立截图：字段类对准断言字段容器，场景类对准断言状态视图；最终截图必须来自 selected attempt，manifest 保存实际目标文本和真实 SHA-256；API observation 使用自己的 evidence_refs
   - 【代码走查】如果提供了 06_代码库信息：按用例反向走查核心功能代码，产出 code_review_report.md
   - 【接口测试】如果提供了 07_接口文档：按用例映射核心接口，正向+异常测试，产出 api_testing_report.md
   - 可选执行项缺输入时记录 skipped 并继续；核心门禁失败则停止进入下一阶段并修正
   - 执行后生成 12_execution_gate.json；回填后生成 19_delivery_gate.json，全部 pass 才能交付
4. 交付物：
   - 08_测试用例_模板版.xlsx（未给模板也必须生成，禁止空表）
   - 执行结果回填版（实际结果为业务数据，禁止只填状态）
   - 23_正式测试报告.md（机器可审计源）+ 23_正式测试报告.docx（正式交付版）；包含总体结论、范围、资源、稳定性、缺陷生命周期和实现与 PRD 差异，不重复逐用例摘要
   - 07_case_design_gate.json、10_execution_plan_gate.json、12_execution_gate.json、19_delivery_gate.json（全部 pass）
   - 代码走查报告、接口测试报告（如果执行了）
5. 发现"实现与 PRD 口径差异"（如运费/退款金额规则）时，单独记入差异登记表，不作为失败，但必须暴露给产品确认。

【质量底线】
- 每条用例独立自造数据，禁止跨用例引用执行结果
- 完整交付默认 full；测试点与最终用例一对一，用例数大于需求数，每条用例恰好一个主断言
- 用例顺序必须跟随当前 PRD，并按端口和页面/相似功能集合聚合；同一功能集合不得被其他集合穿插，Excel 顺序必须与最终 YAML 一致
- 优先级只允许 P0/P1/P2；测试点与最终用例的等级和非空判级理由必须一致；按业务风险判级后，全量占比必须满足 P0 20%～25%、P1 35%～40%、P2 35%～40%，禁止随机凑数
- 优先级专项是独立硬门禁；缺等级、缺理由、占比越界或映射不一致时，即使 11 维评分通过也必须打回
- 正式执行计划和实际启动顺序固定为 P0→P1→P2，同级保持最终用例原顺序；先完成全部 P0 的重试 3.0 最终裁决；只有全部 P0 最终均为 failed 时才停止 P1/P2，只要任意 P0 最终为 passed 或 blocked 就继续执行 P1/P2
- 全部 P0 最终均失败而停止后，仍须覆盖全部结果 ID：未启动 P1/P2 写 not_run/stability:not_run/p0_all_failed_stop_gate/attempts:[]，继续执行门禁、回填 Excel、交付门禁和 Markdown/Word 正式报告
- 断言用强断言（状态机+时间戳+数据对比），避免"页面出现关键词"误判
- 失败必须留证据（截图/日志/API 返回）
- UI 截图必须对准实际断言目标并有真实文件 SHA-256、attempt 绑定和可复核语义 manifest；动作成功、偶然一遍通过或原始状态字段都不能直接决定用例通过
- 有验证码/风控的环境：先探活当前项目登录态；失效时优先用有头真实 Chrome 自动重采，确认仍无法通过后再由人工完成一次挑战并保存状态
```

## 快捷用法示例（最简版）

如果你只有 PRD + 环境信息，直接这样发也行：

```text
请按 ai-qa-prd-workflow 方案执行测试交付。项目目录：{路径}
输入文件已放好：01_需求原文.docx、04_环境信息.md（含账号/验证码/支付密码）。
没有代码库和接口文档，跳过代码走查和接口测试即可，功能执行照常。
执行完给我：模板版用例（如果我没给模板就按方案默认模板）、执行回填版、正式 Word 测试报告（同时保留 Markdown 审计源）。
```

如果你已经有 Excel 测试用例，先规范化再执行：

```text
请按 ai-qa-prd-workflow 的 case_normalization 模式处理已有用例。
项目目录：{路径}
已有用例：{路径}\已有测试用例.xlsx
PRD：{路径或“未提供”}
请保留源行和原 ID，完成导入审计、规范化、11 维评审、设计门禁和模板版 Excel；本轮先不执行浏览器测试。
```

## 说明

- **full 模式必选输入只有 2 个**：PRD 原文 + 环境信息（账号/URL）。`case_normalization` 模式必选已有用例 XLSX，PRD 和环境信息按目标阶段提供
- **提供了什么就执行什么**：代码 → 走查；接口文档 → 接口测试；都没有 → 只跑功能执行
- **可选通道缺输入不中断**：缺代码/接口只跳过对应项；核心门禁失败必须停止进入下一阶段
- **数据自造是硬规则**：每条用例独立构造前置数据，这也是用例能被可靠执行的前提
- **唯一来源与非空交付是硬规则**：只执行 06_final_test_cases.yaml；两份 XLSX 行数/ID 必须与最终用例一致并通过可读性检查
