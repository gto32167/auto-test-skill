# AI QA PRD Workflow

`ai-qa-prd-workflow` 是一套面向 Codex/Agent 的 AI 测试工作流，把 PRD、测试点、最终用例、浏览器执行、证据、结果回填和正式报告串成一条可验证的交付链。

它既支持从 PRD 生成测试用例，也支持导入已有 XLSX 用例并规范化；工作流通过语义契约、版本哈希、优先级批次和机器门禁减少“动作成功但业务断言失败”以及执行结果不可追溯的问题。

仓库只维护跨项目复用的规则、模板、脚本和内置执行框架，不应提交业务项目凭据、登录态、截图、运行产物或未经脱敏的环境配置。

## 当前版本

本仓库当前为持续演进的工作区快照，尚未发布独立的语义化版本。规则和行为以根目录 `SKILL.md`、`references/` 及当次门禁结果为准；历史变更见 [CHANGELOG_202608.md](docs/history/CHANGELOG_202608.md)。

## 核心能力

| 模式 | 必要输入 | 主要输出 |
|---|---|---|
| `case_normalization` | 已有测试用例 XLSX；PRD 可选 | 导入审计、规范化用例、设计门禁、模板版 Excel |
| `generation_review` | PRD | 需求拆解、测试点、最终用例、评审文档、设计门禁 |
| `execution_only` | 已通过门禁的最终用例、执行计划和环境信息 | 执行结果、证据、回填版 Excel、正式报告 |
| `full` | PRD、环境信息 | 从需求拆解到执行交付的完整产物链 |

工作流始终区分 `passed`、`failed`、`blocked` 三态；P0 停止门禁产生的未启动项另记为 `not_run`。环境、权限、数据或工具不足时应记录阻塞原因，不把不可判定的情况伪装成产品失败。

## 文档

| 文档 | 说明 |
|---|---|
| [SKILL.md](SKILL.md) | Agent 入口、路由规则、质量边界和交付要求 |
| [工作流总览](references/overview.md) | 四种模式、阶段路由和标准产物 |
| [用例设计](references/case-design.md) | 测试点、字段覆盖、优先级和语义约束 |
| [浏览器执行](references/browser-execution.md) | 执行通道、重试、三态判定和证据要求 |
| [交付门禁](references/delivery-gates.md) | 设计、计划、执行和交付四道门禁 |
| [框架集成](references/framework-integration.md) | 内置 pytest + Playwright 框架和外部框架绑定 |
| [数据工厂](assets/data-factory/README.md) | 项目级前置数据构造机制和统一接口 |
| [试点成果归档](docs/presentation/AI测试工作流试点成果报告.md) | 历史试点定位、能力与边界 |

## 目录

```text
ai-qa-prd-workflow/
├── SKILL.md                         # Agent 入口和核心规则
├── agents/openai.yaml               # Codex 展示信息和默认调用提示
├── references/                      # 工作流规范、门禁说明和执行经验
│   └── scripts/                     # 导入、校验、回填、报告脚本
├── assets/templates/                # PRD、用例、执行计划和报告模板
├── assets/data-factory/             # 通用数据工厂与示例
├── builtin_framework/               # pytest + Playwright 内置执行框架
└── docs/                            # 试点报告和维护记录
```

## 安装

### 安装为个人 Skill

```powershell
git clone https://github.com/gto32167/auto-test-skill.git `
  "$env:USERPROFILE\.codex\skills\ai-qa-prd-workflow"
```

### 安装到项目工作区

在业务项目根目录执行：

```powershell
git clone https://github.com/gto32167/auto-test-skill.git `
  ".codex\skills\ai-qa-prd-workflow"
```

Codex 重新加载 Skill 列表后，可使用 `$ai-qa-prd-workflow` 作为入口。

### 更新

```powershell
Set-Location "$env:USERPROFILE\.codex\skills\ai-qa-prd-workflow"
git pull
```

## 使用

### 从 PRD 生成并评审用例

```text
使用 $ai-qa-prd-workflow，根据这份 PRD 拆解需求、设计测试点并生成通过门禁的最终测试用例。
```

### 规范化已有 XLSX

```text
使用 $ai-qa-prd-workflow，导入并审计这份已有测试用例 XLSX，保留源行追溯关系，生成规范化用例和评审产物。
```

### 执行已通过门禁的用例

```text
使用 $ai-qa-prd-workflow，读取已通过设计和执行计划门禁的用例，按 P0 -> P1 -> P2 执行，保留证据并生成回填版和正式报告。
```

## 标准产物链

完整交付的唯一最终用例源是 `06_final_test_cases.yaml`，典型链路如下：

```text
03_existing_cases_import.yaml   # 可选：已有 XLSX 导入审计
        ↓
05_requirements.yaml → 05_test_points.yaml → 06_final_test_cases.yaml
        ↓
07_case_design_gate.json → 08_测试用例_模板版.xlsx
        ↓
10_browser_execution_plan.yaml → 10_execution_plan_gate.json
        ↓
11_test_execution_results.yaml → 12_execution_gate.json
        ↓
18_测试用例_执行回填版.xlsx → 19_delivery_gate.json
        ↓
23_正式测试报告.md / 23_正式测试报告.docx
```

四道机器门禁必须按顺序通过：用例设计、执行计划、执行结果、最终交付。业务用例失败可以进入报告，但结构、版本、证据和语义门禁失败时不得宣称交付完成。

## 内置执行框架

`builtin_framework/` 提供 pytest + Playwright 执行器，负责：

- 将 `record_raw/` 原始脚本转换为 pytest 用例；
- 复用浏览器上下文和登录态，采集失败截图、页面 HTML 和执行进度；
- 保存结构化执行结果和可选的 Allure 中间产物；
- 遵循调用方传入的 P0/P1/P2 执行顺序，不自行改变门禁裁决。

环境要求：Python 3.12.x、`builtin_framework/requirements.txt` 中的依赖，以及按需安装的 Playwright 浏览器。

```powershell
Set-Location .\builtin_framework
py -3.12 -m pip install -r requirements.txt
py -3.12 -m playwright install chromium

# 转换原始脚本、列出用例、执行用例
py -3.12 framework_transformer.py
py -3.12 run_tests.py --list-tests
py -3.12 run_tests.py --headless --no-open
```

没有项目级 `record_raw/`、`tests/` 和环境配置时，运行器不会伪造测试结果；应明确记录为未执行或阻塞。正式执行前必须在项目运行副本中提供目标 URL、登录方式和必要凭据。

## 数据工厂

数据工厂是执行期的前置构造层，不是正式业务用例步骤。项目接入时，根据当前项目背景文档或只读探测结果生成实体造数器，并统一返回 `ok`、`msg`、业务标识等字段。

示例位于 [assets/data-factory/examples](assets/data-factory/examples)。其中的域名、账号、令牌、验证码、登录态和字段必须替换为当前项目值，示例不能直接用于生产或真实测试环境。

## 配置与安全

- `builtin_framework/config.yaml` 只保留通用默认结构；正式执行请在项目运行副本中填写环境地址和账号，或使用框架支持的 `TEST_LOGIN_URL`、`TEST_USERNAME`、`TEST_PASSWORD`、`TEST_AUTHORIZATION_BEARER` 环境变量。
- 登录态文件、Bearer token、密码、验证码、支付密码、截图和报告属于运行时数据，禁止提交到仓库。
- `assets/templates/framework-binding.yaml` 是结构模板，必须替换其中的占位符；不要把绝对路径或其他项目的凭据写回通用仓库。
- 如果凭据曾经进入公开 Git 历史，应立即轮换凭据；仅删除当前文件不能消除历史提交中的暴露。

## 质量边界

- AI 生成的用例和执行观察需要人工复审；自动化结果不等于已确认缺陷。
- UI、验证码、外部依赖、权限或前置数据不可用时，应使用 `blocked` 并保留解除条件。
- API 可用于探活、造数和辅助观察，但不能替代声明为 UI 主通道的核心业务动作。
- 历史基线只有在输入哈希、版本链和独立复核均满足要求时才能复用。

## 开发与校验

修改规则、脚本或模板后，至少运行针对性脚本；完整工作流门禁回归可使用：

```powershell
py -3.12 references/scripts/test_workflow_gates.py
```

该回归依赖测试夹具与当前门禁规则保持同步；发布说明应以本次实际运行结果为准，不要直接引用历史回归数字。

本仓库当前未附带许可证文件。使用、二次分发或纳入商业项目之前，请先确认仓库维护者的授权范围。
