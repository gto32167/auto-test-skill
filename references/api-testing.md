# 接口测试方法（API Testing）

> **可选执行项**。仅当输入文件提供接口文档 / OpenAPI / Swagger / 抓包记录时执行；未提供则整体跳过，不影响主流程。

## 1. 触发条件（输入即触发）

执行项由可用输入决定，以下任一存在则执行接口测试：

- `05_接口文档.md` / 接口文档字段（OpenAPI 3.0 / Swagger 2.0 / 自建接口清单）
- 接口文档链接（在线 Swagger UI / OpenAPI JSON）
- 抓包记录（HAR 文件 / 录制脚本中的接口调用）
- 输入文件中显式声明 `api_testing: enabled`
- 环境信息中提供 API 域名 + 鉴权方式（token/cookie/签名）

任一都不存在 → **跳过本执行项**，在报告中记为 `skipped(api_testing: 无接口文档输入)`，继续下一个执行项。

## 2. 输入

| 输入 | 说明 | 来源 |
|------|------|------|
| 接口文档 | OpenAPI/Swagger JSON/YAML 或自建接口清单 | 接口文档文件/链接 |
| API 域名 | 测试环境 API 基址 | 环境信息文件 |
| 鉴权信息 | token/cookie/签名方式 + 测试账号 | 环境信息文件 |
| 抓包记录 | 可选：HAR 或录制脚本，辅助补充未文档化接口 | 项目输入 |

## 3. 接口测试范围（按用例驱动）

接口测试同样**由用例反向驱动**，不测全部接口：

1. 从用例挑选 `priority: 高` 的核心链路（创建商品、下单支付、发货、退款）
2. 映射到对应接口（Controller/Action 或 OpenAPI path）
3. 对每个核心接口设计正向 + 异常用例

## 4. 测试维度

### 4.1 正向

- 正常参数调用 → 断言 Code=0 / HTTP 200 + 关键字段
- 业务链路串联（下单 → 支付 → 发货 → 确认收货的接口序列）

### 4.2 异常与边界

- 必填缺失 → 断言错误码 + 提示
- 参数类型错误、越界、非法枚举
- 未授权/无效 token → 断言 401/403
- 越权（A 账号操作 B 账号数据）
- 幂等：重复提交同一请求
- 并发/时序：重复支付、重复发货
- 金额/库存边界：0、负数、超长、超库存下单

### 4.3 契约一致性

- 接口返回字段与接口文档是否一致
- 返回结构与用例断言是否匹配

## 5. 执行方式（三通道联动）

- 接口测试优先用 **API 直连**（Bearer + Req-Host 直调），复用 `assets/data-factory/auth_template.js` 的鉴权模板
- 无独立 API 域时，可通过 **UI 操作抓取接口请求**（Playwright 监听 request）获取真实请求参数
- 接口测试产物（请求/响应 JSON）同时可作为功能执行的 **API 兜底证据**（证据三通道之一）

## 6. 输出：接口测试报告

产出 `api_testing_report.md`：

```yaml
api_testing:
  input: "<接口文档路径/链接>"
  status: passed | failed | skipped
  base_url: "<API 域名>"
  cases:
    - case_id: TC-001
      api: "POST /Product/CreateProduct"
      request:
        params: { name: "AI商品_xxx", price: 12.3 }
      expected: { code: 0, data.name: "AI商品_xxx" }
      actual: { code: 0, data: {...} }
      status: passed
    - case_id: TC-015
      api: "POST /Order/Refund"
      request:
        params: { orderId: "..." }
      expected: { code: 0 }
      actual: { code: 30002, msg: "退款金额超限" }
      status: failed
      finding:
        severity: high
        prd_ref: "PRD 4.4.9"
  summary:
    total: 12
    passed: 11
    failed: 1
    skipped: 0
```

## 7. 与用例执行的关系

- 接口测试结果回填对应用例（`api_echo` 断言通道）
- 接口失败 → 标记对应用例 `failed` 或补充说明（区分前端缺陷 / 接口缺陷）
- 接口测试发现的缺陷独立归入缺陷清单，标注 `layer: api`

## 8. 不中断原则

- 无接口文档 → 跳过（`skipped`），不阻塞功能执行与代码走查
- 单个接口失败 → 记录，不阻断其他接口
- API 域名不可达 → 标记 `blocked`，继续后续执行项
