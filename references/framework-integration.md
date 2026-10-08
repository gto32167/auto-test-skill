# 执行框架接入

## 1. 定位

本文只说明如何把内置框架或已有自动化资产接入项目运行目录。执行顺序、重试、三态判定和证据规则由 `browser-execution.md` 维护；选择器、登录和弹窗排障由 `execution-playbook.md` 维护。

默认执行器是方案内的 `builtin_framework/`。只有当前项目已经有成熟自动化资产且不适合迁移时，才把外部框架作为兼容来源。

## 2. 接入时机

框架接在用例设计门禁通过之后、正式执行之前：

```text
06_final_test_cases.yaml + 07_case_design_gate.json
  -> 10_browser_execution_plan.yaml + 10_execution_plan_gate.json
  -> 环境门禁
  -> 项目运行副本执行
  -> 11_test_execution_results.yaml
```

框架只负责实际执行能力，不能替代 PRD 拆解、用例设计、评审或四道门禁。

## 3. 隔离原则

- 不在方案源目录或外部框架原目录中保存项目结果。
- 每个项目创建独立运行副本。
- 当前项目的配置、登录态、日志、截图和结果只写入当前项目目录。
- 不复用其他项目的测试数据、账号假设或历史结果。
- 源框架目录缺少可执行脚本时，必须由当前项目生成脚本或明确使用项目级执行器，不能依赖 `.pyc` 或历史产物。

## 4. 标准绑定

复制 `assets/templates/framework-binding.yaml` 到项目目录并填写：

```yaml
framework_binding:
  framework_root: "E:\\solutions\\ai-qa-prd-workflow\\builtin_framework"
  runtime_workspace: "<项目目录>\\runtime\\external_framework_runtime"
  runtime_config:
    browser: chromium
    # 正式资格执行默认有头；只有风险回归且已验证稳定时才显式改为 true。
    headless: false
    runtime:
      test_url_input: "<当前项目 URL>"
      auth_storage_state_path: "<当前项目登录态路径>"
```

绑定文件中不得保存无关项目的账号、token 或绝对产物路径。

## 5. 准备运行副本

执行：

```powershell
py references/scripts/prepare_external_framework_runtime.py `
  --binding <项目目录>/11_framework_binding.yaml
```

准备脚本复制：

```text
common/
record_raw/
test_data/
tests/
conftest.py
pytest.ini
requirements.txt
```

并在项目运行副本生成 `config.yaml`、`prepare_report.yaml`、`artifacts/` 和 `allure-results/`。

`record_raw/`、`test_data/` 和 `tests/` 即使为空也必须存在。运行副本没有 `tests/*.py` 时不具备正式执行能力，应先生成当前项目测试脚本。

## 6. 登录态

运行副本只使用当前项目登录态：

1. 加载 `storage_state` 并访问受保护页面探活。
2. 失效时按 `execution-playbook.md` 的登录章节重新采集。
3. 后台和 H5 使用独立 context 与独立状态文件。
4. 新状态只回写当前项目目录。
5. 登录态重采后，API 与数据工厂必须从新状态重建 Authorization；请求模板不得继续携带旧抓包 token。

登录态探活应紧邻正式执行，不能用数小时前或前一轮的环境门禁结果代替。如果 UI 已登录而数据工厂集中返回 401/403，应先审计浏览器 context 与 API header 是否来自同一份当前状态，再决定是否属于环境阻塞。

不得把历史项目的登录状态作为新项目默认前置。

## 7. 执行与输出

执行器接收已通过计划门禁的 nodeid 或项目级执行任务，并按调用方给出的优先级批次运行。浏览器配置优先级固定为：本次运行显式环境覆盖（如 `TEST_HEADLESS`）-> 项目 `11_framework_binding.yaml` -> `builtin_framework/config.yaml` -> 默认 `headless: false`。执行器禁止写死另一种模式。解析后的 `browser/headless/mode/config_source` 必须进入计划元数据、每条 `runtime_env`、结果元数据和每份证据 manifest；任一处不一致即门禁失败。框架结果必须转换为完整 attempt 结构，不能只返回 pytest 通过/失败状态。

资格执行、首次执行、复杂弹窗、登录风控、支付、发货、收货和退款默认使用有头浏览器；无头只允许在风险回归中由调用方显式开启，并在环境门禁中记录理由。无头或有头都不能降低截图证据要求。

预期出现短暂 UI 提示的用例使用运行副本中的 `common.ui_feedback.capture_transient_feedback`：动作前采集可见提示基线，动作后轮询 modal/toast/inline/banner，并在提示可见时立即截图。具体参数和三态判定见 `execution-playbook.md`“瞬态提示捕获”；项目执行器不得重新退化为固定 sleep 后单次读取。

项目运行目录应至少保留：

```text
runtime/external_framework_runtime/prepare_report.yaml
runtime/external_framework_runtime/config.yaml
artifacts/
11_test_execution_results.yaml
```

检测到系统 Allure 命令时可以额外生成 HTML 报告；未检测到时只跳过该辅助报告，不影响执行、回填、门禁和 Word 正式报告。

## 8. 接入验证

正式批量执行前至少验证一条当前项目测试：

- 运行副本准备成功。
- 核心模块能够导入。
- Playwright 浏览器能够启动。
- 当前项目登录态或登录流程可用。
- 一条测试能够产生 pytest 结果和证据中间文件。
- 结果能够映射回当前 `case_id` 和语义契约。

上述验证失败时先修复框架接入或标记环境阻塞，不能用历史执行结果代替。
