# UI 自动化测试框架

正式 PRD 工作流执行需设置 `AI_QA_WORKFLOW_DIR` 为本轮产物目录。runner 和直接 pytest 会在测试运行前校验人工准备清单与门禁，只运行计划中 `readiness.disposition: run` 的明确节点；必须人工执行或人工跳过的用例保留为本轮未执行。默认映射文件为 `case_execution_mapping.yaml`；可用 `AI_QA_CASE_MAPPING` 指定映射路径，框架复制到其他项目后用 `AI_QA_SKILL_ROOT` 指向技能目录。详见 [人工准备与结果说明](../references/human-readiness.md)。

本目录是 `ai-qa-prd-workflow` 的 pytest + Playwright 内置执行框架，负责把项目生成或录制的脚本转换为 pytest 用例，并提供共享浏览器会话、运行时配置、失败证据采集和结构化执行结果。

正式交付报告仍由工作流根目录下的 `references/scripts/generate_test_report.py` 生成。Allure HTML 只是可选辅助报告，不能代替回填版用例和 Word 正式报告。

## 核心链路

1. 项目脚本进入 `record_raw/`，或由项目执行器直接生成 `tests/test_*.py`。
2. `framework_transformer.py` 将 `record_raw/*.py` 转换为 pytest 用例和 `test_data/*.yaml`。
3. `conftest.py` 与 `common/` 提供浏览器、共享登录、运行参数、失败截图、页面 HTML 和执行进度。
4. `run_tests.py` 发现并执行 `tests/` 下的用例，保存 pytest 和 Allure 中间结果。
5. 工作流将结构化结果回填到用例，执行门禁，并生成 Markdown 与 Word 正式报告。

当前方案仓库不保存具体项目的 `record_raw/*.py`、`tests/test_*.py` 或登录状态。每个项目从零执行时必须生成或准备自己的可执行脚本；没有测试源码时，运行器会明确返回 `No test files found in tests directory.`。

## 目录结构

```text
builtin_framework/
|-- common/                  # 配置、运行时参数、脚本兼容和证据支持
|-- record_raw/              # 项目原始脚本输入目录
|-- test_data/               # 转换生成的参数化数据
|-- tests/                   # pytest 可执行用例
|-- config.yaml              # 源框架默认配置
|-- conftest.py              # pytest/Playwright 会话和失败证据
|-- framework_transformer.py # 原始脚本转换器
|-- pytest.ini               # pytest 配置
|-- requirements.txt         # Python 依赖
`-- run_tests.py             # 批量执行和结果汇总入口
```

`record_raw/`、`test_data/` 和 `tests/` 即使暂时为空也必须保留。运行副本准备脚本会复制这些目录。

## 环境要求

- Windows 10/11
- Python 3.12.x
- `requirements.txt` 中锁定的 Python 依赖
- Playwright Chromium；Firefox 和 WebKit 按项目需要安装
- 可选：系统 PATH 中的 Allure 命令，用于生成 HTML 辅助报告

安装依赖和浏览器：

```powershell
Set-Location E:\solutions\ai-qa-prd-workflow\builtin_framework
py -3.12 -m pip install -r requirements.txt
py -3.12 -m playwright install chromium
```

框架不再内置 Allure 命令行发行包。`allure-pytest` 仍负责生成 `allure-results` 和采集附件；检测到系统 Allure 时，`run_tests.py` 会继续生成 HTML 报告，未检测到时只跳过 HTML 生成，不影响测试结果和正式 Word 报告。

## 转换和执行

转换 `record_raw/` 下的项目脚本：

```powershell
py -3.12 framework_transformer.py
```

列出发现的测试：

```powershell
py -3.12 run_tests.py --list-tests
```

执行全部测试：

```powershell
py -3.12 run_tests.py --headless --no-open
```

执行指定文件或 nodeid：

```powershell
py -3.12 run_tests.py --test tests\test_example.py --headless --no-open
py -3.12 run_tests.py --nodeid "tests/test_example.py::test_example" --headless --no-open
```

## 工作流约束

内置运行器只负责按调用方提交的 nodeid 顺序执行。接入完整 PRD 测试工作流时，调用方必须遵守执行计划和门禁：

1. 先执行全部 P0，每条用例按重试策略完成最终裁决。
2. 只有全部 P0 的最终结果均为 `failed` 时才停止，不再提交 P1/P2。
3. 只要任意 P0 最终为 `passed` 或 `blocked`，继续执行 P1、P2。
4. 因 P0 全失败未执行的后续用例回填为 `not_run/p0_all_failed_stop_gate`。
5. 无论是否提前停止，都必须完成结果回填、执行门禁以及 Markdown/Word 报告生成。

## 配置与产物

`config.yaml` 保存默认浏览器、超时、环境地址和运行参数。项目正式执行时，应通过 `framework-binding.yaml` 生成项目运行副本和项目级 `config.yaml`，不要把历史登录状态或项目执行产物回写到方案目录。

以下目录均为执行时生成的临时内容，不属于方案源码，可在执行结束后清理：

```text
__pycache__/
.pytest_cache/
allure-results/
allure-report/
artifacts/
```

其中 `artifacts/auth` 可能包含认证状态，不得作为方案文件长期保存或跨项目复用。

## 关键文件

- `config.yaml`：框架默认配置
- `conftest.py`：浏览器、共享登录、运行时参数和失败证据
- `framework_transformer.py`：原始脚本转换入口
- `run_tests.py`：测试发现、执行、汇总和可选 Allure HTML 生成
- `common/`：上述入口依赖的公共实现
