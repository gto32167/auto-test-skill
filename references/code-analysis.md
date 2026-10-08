# 代码分析方法

代码分析是可选能力，分为设计阶段的“实现检查”和执行阶段的“正式走查”。两种模式都以 PRD 为需求来源，不能用当前实现覆盖需求。

## 1. 模式选择

| 模式 | 使用时机 | 输出 | 是否影响核心流程 |
|---|---|---|---|
| `inspection` | PRD 拆解后、最终用例生成前 | 实现线索、业务映射、差异和测试影响 | 可选增强 |
| `review` | 已有代码输入且进入执行阶段 | `code_review_report.md` | 可选执行项 |

没有代码路径、仓库地址或项目源码时，记录 `skipped(code_analysis: 无代码输入)`，继续功能测试。

## 2. 共同原则

- PRD 定义“应该怎样”，代码只说明“当前怎样实现”。
- 发现冲突时登记实现与 PRD 差异，不把代码行为改写成需求。
- 代码中的旧逻辑、疑似缺陷或未描述能力只能作为风险和验证线索。
- 按当前需求和用例定位代码，不做无目标的全仓扫描。
- 所有发现必须能回到需求、测试点、用例、风险或差异记录。

## 3. 设计阶段实现检查

适用于 PRD 对页面入口、字段、接口或状态细节描述不足，但已有源码可供确认的情况。

优先读取：

- 页面路由和菜单入口
- 表单字段名、控件类型和校验规则
- 接口路径、请求和返回字段
- 状态枚举与流转条件
- 权限检查和隐藏依赖

输出四类信息：

```yaml
implementation_inspection:
  clues: []              # 页面、组件、接口和字段位置
  business_mapping: []   # 实现字段与 PRD 的对应关系
  differences: []        # PRD 与代码不一致或待确认项
  test_impact: []        # 需要新增、强化或调整的测试点
```

实现检查不能直接产生用例通过或失败结论，也不能自动扩大 PRD 范围。

## 4. 执行阶段代码走查

以下任一条件成立时可以执行：

- 提供本地代码路径或 git 仓库地址与分支。
- 当前项目目录包含明确的源码目录。
- 输入显式声明 `code_review: enabled`。

按高风险需求或用例反向定位：路由、控制器、服务、数据访问和相关前端逻辑。重点检查：

- 功能、字段和状态流转是否与 PRD 一致
- 前后端数据校验是否能够被绕过
- 权限和越权控制
- 非法状态迁移
- 并发、幂等和重复提交
- 错误处理、日志和敏感信息

代码质量问题可以记录为观察项，但不应把一般可读性问题误报为产品功能失败。

## 5. 走查报告

正式走查输出 `code_review_report.md`：

```yaml
code_review:
  input: "<代码路径或仓库>"
  status: passed | failed | blocked | skipped
  reviewed_modules:
    - module: "商品创建"
      requirement_refs: [REQ-001]
      case_refs: [TC-001]
      files: ["app/services/ProductService.py"]
      findings:
        - severity: high | medium | low
          title: "后端缺少字段长度校验"
          location: "app/services/ProductService.py:88"
          detail: "..."
          prd_ref: "PRD 4.2"
  summary:
    total_findings: 1
    high: 1
    medium: 0
    low: 0
```

高风险发现应写入对应用例的风险或复核重点。代码走查与功能执行可以互相印证，但代码发现本身不能直接修改 UI/API 用例的最终状态。

代码不可读、权限不足或仓库获取失败时，记录 `blocked` 或 `skipped` 和原因；它不绕过功能测试的核心门禁，也不阻断其他可用执行项。
