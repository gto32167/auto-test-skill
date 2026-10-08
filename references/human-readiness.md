# 人工执行分级与执行前准备

业务优先级 `P0/P1/P2`、覆盖粒度 `L2/L3` 与执行级别是三个独立维度。不能把必须人工操作的用例降为 P2 来掩盖它，也不能用 `automation_candidate: low` 代替正式分级。

## 用例设计阶段就要标记

每条最终用例必须有 `execution_readiness`：

| level | 用例表展示 | 自动执行规则 |
| --- | --- | --- |
| auto | 可自动执行 | 当前环境和执行器能完成核心动作、独立准备数据 |
| assisted | 人工准备后自动执行 | 人工先准备数据、权限或配置；确认全部准备事项后可自动执行 |
| manual | 必须人工执行 | 核心操作需要人完成，移交人工；本轮自动化不执行 |

交易、付款、支付、扫码操作必须标记为 `manual`，列出具体人工操作，不能自动重试资金操作，也不能用接口模拟结果冒充人工完成。检查已付款订单的查询等用例，前置状态本身不代表核心操作需要人工；如果已付款数据只能人工产生，则标记 `assisted`。设计门禁检查核心步骤、名称、操作类型中的支付/扫码/交易信号及前置里的显式人工依赖，语义审核还必须核对当前业务实际需要谁完成，不能只依赖关键词。

复杂用例在评审时逐项拆解前置依赖，确认自动造数渠道确实可用。凡必须人工配置活动、生成特殊状态订单、准备多角色账号、完成线下审批、绑定设备或等待真实时间的，都应提前列入 `preparations`，写出负责人和可验收的具体业务状态。不能一边写“人工准备”，一边标记 `auto`，也不能等核心测试开始后才探测已知人工依赖。

```yaml
execution_readiness:
  level: assisted
  reason: 特殊活动和指定状态的订单需要测试人员先在后台准备
  human_actions: [] # 核心步骤全部可自动执行；有人工核心动作则改为 manual
  preparations:
    - id: activity_order
      description: 为买家A准备生效中的限购活动和一笔已完成订单
      owner: 测试负责人
      acceptance: 活动未过期，买家A历史购买数量为2，提供活动ID和订单号
```

```yaml
execution_readiness:
  level: manual
  reason: 需要使用手机扫码并亲自确认付款
  human_actions:
    - 使用测试手机扫描订单付款二维码
    - 核对金额后完成付款并检查订单状态
  preparations:
    - id: payment_account
      description: 准备付款测试账号和可核对的订单
      owner: 测试负责人
      acceptance: 测试账号可登录，记录订单号、应付金额和测试支付环境
```

以上是字段示例，业务规则必须来自当前项目。不得把示例里的活动或金额当作所有项目的前置要求。人工准备的各条数据必须独立分配，不能依赖另一条用例先执行成功。

默认用例 Excel 新增 `执行级别 / 人工介入说明 / 人工准备清单`，位于优先级之后，测试人员在执行前就能看到。旧用例需补充分级和准备事项，再重新通过设计门禁；缺少分级不会默认为自动执行。已有 Excel 的规范化流程也要执行相同审计。

## 自动化开始前的安排

生成执行计划后运行 `validate_execution_plan.py`。无论准备校验通过与否，脚本都会生成同目录的 `10_执行前人工准备清单.md`（也可用 `--preflight-report` 指定路径），逐条列出人工用例、准备内容、负责人、验收条件和缺少的确认。Agent 必须先向测试人员展示这份清单和待办内容，不能只说“有阻塞”或只输出文件路径。

人确认清单后，写入计划的 `execution_meta.preflight_acknowledgement`，以及逐条安排：

```yaml
execution_meta:
  preflight_acknowledgement:
    confirmed_by: 实际确认的测试人员姓名
    confirmed_at: "2026-10-08T14:00:00+08:00"

browser_execution_plan:
  - case_id: TC-ACTIVITY-001
    # 其余自动执行步骤、语义哈希和证据计划仍须完整声明
    readiness:
      disposition: run
      preparations:
        - id: activity_order
          status: ready
          confirmed_by: 实际准备人姓名
          confirmed_at: "2026-10-08T14:00:00+08:00"
          evidence: 活动ID ACT-001，订单号 ORD-001，验收记录 runtime/preparation.md
```

确认人、时间和数据编号必须来自真实人工回复或验收记录，Agent 不得为了过门禁代填确认。待准备的项写 `pending`。人工未准备好时，由人明确决定跳过：

```yaml
readiness:
  disposition: skip
  reason: 本轮活动尚未配置，测试负责人决定跳过，配置完成后补测
  confirmed_by: 实际作出决定的人
  confirmed_at: "2026-10-08T14:00:00+08:00"
  preparations:
    - id: activity_order
      status: pending
```

必须人工执行的用例安排为 `disposition: manual`，记录移交原因、确认人、时间和全部准备事项状态；也允许人决定 `skip`，禁止 `run`。人工分流/跳过仍保留 case_id 和语义哈希，资源预算只计算 `run` 的 attempts 和分钟。未完成准备且尚未决定跳过时，整个自动化批次停在准备阶段；处理完再重新生成/校验计划。

正式工作流执行设置 `AI_QA_WORKFLOW_DIR=<本轮产物目录>`；builtin runner 和直接 pytest 都会在测试运行前校验确认、设计门禁、计划门禁及输入哈希，只选择 `run` 节点。目录需包含 `06_final_test_cases.yaml`、`07_case_design_gate.json`、`10_browser_execution_plan.yaml`、`10_execution_plan_gate.json` 和 `case_execution_mapping.yaml`。映射文件可用 `AI_QA_CASE_MAPPING` 指定；框架复制到其他项目后，用 `AI_QA_SKILL_ROOT` 指向技能根目录。独立旧脚本没有这些产物时仍可使用原模式，PRD 工作流不得借旧模式绕开检查。

人工用例及人工跳过用例本轮自动化结果统一为 `not_run`，分别使用 `manual_execution_required`、`human_preflight_skip` 原因码，`attempts: []`，无自动执行证据和产出数据。不得写成 `blocked` 或 `passed`。人工结果另行记录，不能拿移交决定当测试通过。P0 的人工分流/跳过属于已完成执行安排，允许继续其他准备好的用例；存在人工未测试 P0 时，不能声称“全部 P0 测试通过”或“全部 P0 失败”。

运行中真正新发生的环境变化、工具故障或证据缺失仍可判定 `blocked`；报告需区分它们与执行前已安排的人工用例。

## 测试人员看得懂的结果

回填 Excel 和 Markdown 新增 `原因说明（给测试人员） / 建议下一步 / 技术原因（给Agent）`。执行 YAML 可以提供 `human_reason`、`next_action`，生成器也会依据状态和业务摘要补充说明。技术错误、选择器、原始日志和原因码留在技术字段。

失败说明写“哪一步、原本应该发生什么、实际发生什么、接下来怎么处理”，例如：“第3步没有达到预期。预期：收件人为空时禁止提交。实际：系统仍创建了订单。核对截图，确认后提交缺陷，修复后回归。”准备问题写“缺什么，谁准备，准备到什么状态”，人工跳过写“本轮未执行及人工决定的原因”。不能只写“断言失败”“UI assertion not met”或“前置缺失”，也不能凭一次失败推断已完成登录、已下单或资金已到账。
