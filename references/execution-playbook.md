# Playwright 执行经验库（Execution Playbook）

> 通用可复用的浏览器执行经验。每轮真实项目执行中发现的通用坑，都应回写到本文件，避免后续项目重复踩坑。
> 本文件只记录「跨项目通用」的经验；具体项目专属的选择器/端点记录在对应项目的执行结果中。

## 0. 三通道执行模型（先看这里）

执行不绑定单一工具，通道边界和正式判定以 `browser-execution.md` 为准：

- **通道 A 确定性脚本（主执行）**：`builtin_framework/` / Node+playwright API 脚本 → 正式用例批量、回归、无人值守
- **通道 B Playwright MCP（探索/排障）**：`browser_navigate` / `browser_click` / `browser_snapshot` 会话内逐步驱动 → 首次探索选择器、验证码、失败现场排障
- **通道 C API 直连（兜底）**：Bearer + Req-Host 直调接口 → UI 受阻时数据自造 + 业务断言 JSON 佐证

切换原则：脚本失败 → MCP 现场处理并回写选择器；API 只能承担前置构造或契约允许的辅助观察。`test_intent.interface: ui` 的核心 UI 动作不能被 API 成功结果替代，UI 仍无法执行时必须标记 `blocked`。只有 `interface: api` 的用例才允许 API 作为核心执行通道。

### 0.1 优先级分批与 P0 停止门禁

- 确定性脚本、MCP 和人工辅助都必须服从 `P0 -> P1 -> P2`，同一优先级内保持计划顺序。
- 先跑完全部 P0 的双跑/必要第三遍并形成最终裁决，不因单个中间 attempt 失败提前停，也不能先穿插执行 P1/P2。
- 全部 P0 完成后，只有所有 P0 的最终状态均为 `failed` 才停止启动 P1/P2；只要任意 P0 最终为 `passed` 或 `blocked`，就继续执行 P1/P2。
- 停止后 P1/P2 回填 `not_run + stability:not_run + p0_all_failed_stop_gate + attempts:[]`，随后照常生成回填 Excel、Markdown 与 Word 报告。
- 若发现 P0 失败后已有 P1/P2 attempts，视为执行顺序违规，结果门禁必须失败并按正确批次重跑，不能删除轨迹掩盖违规。

### MCP 使用要点（通道 B）

- MCP 服务器配置在 `~/.workbuddy/mcp.json`（`npx @playwright/mcp@latest`），首次使用需在连接器管理页"信任"启用
- MCP 与确定性脚本可共用同一 `storage_state`（同一登录态）
- MCP 会话内做**探索**而不是批量执行：确认稳定选择器、处理验证码/弹窗、现场排障
- 探索完成后，把确认到的稳定选择器 / 流程回写本文件对应章节，供通道 A 脚本复用
- 批量 / 回归 / 定时场景必须走通道 A 脚本（MCP 非确定性且依赖会话，不适合跑量）

## 1. 登录态生命周期

- 后台会话（storage_state）会过期，H5 会话相对持久；执行前必须探活
- 探活方法：加载 storage_state 打开受保护页面 → 检测是否跳回登录页
- 重采：执行登录采集脚本并按本文件 1.2 节分级排查，再回写当前项目 storage_state
- 后台 / H5 用独立 context 与独立 storage_state，互不污染

## 1.1 登录态重采：优先有头真实 Chrome（2026-08-24 实测，先自己判断再求助）

**核心结论：有阿里云无感验证的登录，优先用「有头 + 系统 Chrome + 反检测」采集登录态，通常全程自动通过，不需要人工。**

- **错误做法（踩过的坑）**：默认 `headless: true` 点登录 → 阿里无感验证识别无头环境为机器、不自动通过 → 误判"必须人工点验证码" → 把登录推给用户
- **正确做法**：`chromium.launchPersistentContext(userDataDir, { headless: false, channel: 'chrome', args: ['--disable-blink-features=AutomationControlled'] })` + `addInitScript` 隐藏 `navigator.webdriver` → 填账号密码点登录 → 轮询 token/URL 检测登录成功 → `context.storageState()` 保存
- **判断登录成功的标准（脚本自动轮询，不要靠人）**：`context.cookies()` 出现 `/token|dev_token|fat_token|store_token/i` 匹配的 cookie，或 URL 进入当前项目配置的业务域。
- **登录 API（项目相关示例）**：
  - API 域、登录路径和前端 `Req-Host` 必须从当前项目背景或探测结果读取，不能复制其他项目的地址。
  - 如果服务要求 `Req-Host`，请求必须使用当前项目的业务域；缺少或写错时，应记录真实响应并标记环境/鉴权问题。
  - `AliCheckLoginNew(TelPhone, Password, AliCode, ReturnUrl, ...)`：密码+阿里验证码，AliCode 非法返回 30002
  - `NewLogin(TelPhone, Password, Session, Sig, Token, Scene, ReturnUrl)`：密码+阿里无感参数
  - `VerificationCode(TelPhone, SmsCode, ReturnUrl)`：短信验证码登录的参数和有效期以当前项目为准；验证码必须运行时注入，不能写死在模板或仓库中。
  - `SendSmsCode(TelPhone, Session, Sig, Token, Scene)`：发短信，阿里参数为空时 30003 阿里滑块校验不通过
  - 后端异常会通过 `Code:500 + Msg: NullReferenceException` 暴露方法签名 → 从堆栈直接拿参数名（这是盲试参数的捷径）
- **登录流程判断原则**：遇到登录/风控问题，先自己按上面步骤尝试（有头真实 Chrome → 轮询检测），**不要第一时间假设必须人工介入**；确认真实 chrome 也无法自动过时，再提示用户手动配合一次并自动接管后续

## 1.2 登录失败分级排查

登录问题按以下顺序处理，前一级失败并留下探测结果后再进入下一级：

1. **复用并探活当前项目登录态**：加载 `storage_state` 打开受保护页面；有效时直接执行。
2. **自动重新采集**：优先有头系统 Chrome、持久化 profile 和反自动化参数，自动轮询业务域 URL 或 token/cookie。
3. **人工完成一次挑战**：只有确认真实 Chrome 仍无法通过验证码或风控时，才请求用户在可视浏览器完成一次，并立即保存当前项目状态。
4. **登录探针**：确认验证码对象、登录 action、跨域回跳、所需 header、token/cookie/localStorage 和阻塞位置。

登录阻塞必须记录尝试层级、页面或接口证据、最终 URL 和具体原因。不能把登录工具问题写成产品用例失败，也不能把一个项目的登录状态复制给另一个项目。

标准采集脚本为 `references/scripts/capture_backend_login_state.py`。采集后的状态只保存在当前项目目录，后台和 H5 分开管理。

## 2. 中文 antd / Vue 弹窗按钮（带空格）

- 常见按钮文本：`确 定`、`确 认`、`发 货`、`取 消` —— 中间是空格
- 精确文本匹配 `getByText('确定')` 会失败
- 正确姿势（正则容忍空白）：
  ```js
  // 弹窗确认 helper
  const confirm = page.locator('.ant-modal').last().getByRole('button')
    .filter({ hasText: /确\s*认|确\s*定|同\s*意/ }).first();
  // 或
  const shipBtn = page.getByText(/发\s*货/).last();
  ```
- 注意：宽松正则可能命中左侧菜单（如"快递发货"），需限定范围（行内 / 弹窗内）

## 3. H5 数字键盘支付（非 input）

- 支付密码往往不是 `<input>`，而是组件：
  - `.secret-box`（6 个 `.secret-item` 圆点）+ `.keyboard-item`（数字键）
  - 或 `.set-pay-secret-box` + `.keyboard-box`
- 流程：
  1. 选支付方式（余额支付）→ 点"立即支付 / 立即付款"（注意文案差异）
  2. 等待 `.secret-box` 出现并点击（触发键盘渲染）
  3. 逐键点击 `.keyboard-item`（`hasText` 匹配数字）
- 失败特征：直接 `fill()` 密码报 "Element is not an input"；`page.keyboard.type()` 无效（虚拟键盘不监听物理键盘）

## 4. 新页面 / 新窗口流转

- 后台"处理退款"、部分"查看详情"会开新 tab
- 处理模式：
  ```js
  const before = page.url();
  await btn.click();
  const pages = context.pages();
  let target = page;
  if (pages.length > 1) target = pages[pages.length - 1];
  await target.waitForTimeout(2000);
  ```
- 完成后关闭新页面，防止页面堆积

## 5. 选择器稳定性

- 优先 `getByRole` / `placeholder` / 可见文本 / form label
- 禁止依赖 antd/umi 编译类名（可能含 `\` 反斜杠或哈希，如 `antd-pro\pages\...`）
- 显式等待优先：`waitFor({state:'visible'})` / `waitForURL`，避免固定 sleep
- 页面加载：`waitUntil: 'domcontentloaded'` + 显式等待关键元素
- UI 字段用例执行前必须记录控件语义审计：label、字段名、input type、是否可编辑、最近表单容器；审计不一致先修计划，不得继续操作相邻或只读控件
- 状态流转断言必须刷新后以指数退避轮询到明确上限，并落盘状态时间线；固定等待后单次读取不能作为最终状态证据

## 6. API 直连兜底通道（UI 受阻时）

- 适用场景：UI 弹窗阻塞、验证码拦截、时序不稳定
- 通用鉴权模式：
  ```
  URL:    https://<api-host>/<storeId>/<Controller>/<Action>?<query>
  Headers:
    Authorization: Bearer <JWT>     ← 必须
    Req-Host: <前端域名>             ← 必须（缺省会 30010）
    Content-Type: application/json
  响应:   {"Code":0,"Msg":"获取成功","Data":...}   Code=0 成功
  ```
- 常见数据构造端点（按控制器归类）：
  - Product：CreateProduct / CreateFirstGroup / CreateSecondGroup / CreateProductTag / DeleteTag
  - Order：GetOrderList / Ship / Cancel / ConfirmReceive
  - Member：GetMemberCenterInfo / AddTag
- 响应字段命名不稳定时（如 `Name` vs `name`、`Id` vs `id`），先 GET 探活拿真实字段再构造请求
- **商品创建真实参数（storeapi Product/CreateProduct，已实测 2026-08-23）**：字段必须用 `goodsUse:[1]`（商品用途，上架售卖=1）、`picture:[{imageUrl:"相对路径",width:1,height:1}]`（不是 images/Picture-url）、`total_stock`（不是 first_stock）、`delivery:[1]`（发货方式数组）、`freight`、`freightWay:1`、`status:1`。缺 `goodsUse` 报 30902「请选择商品用途」；缺图片报 30002「至少添加一张商品图」；`delivery` 传 int 报 400「错误参数delivery」（必须数组）。参数可通过编辑页保存时抓 `UpdateProduct` 请求体反推
- 配送员列表端点：`AccountManagement/GetAccountListByPage`（返回 25 个员工，含 realName/phone），同城发货弹窗的配送员 select 数据源

## 7. 强断言模式（避免误判）

- 弱断言示例（会误判）：页面出现"退款"二字就判通过（可能来自菜单/Tab 文案）
- 强断言示例：
  - 时间戳型：`商家处理退款申请(14:38:35) + 退款完成(14:38:35)` 同时出现
  - 数据对比型：库存三段式 `下单前 82 → 下单后 81 → 退款后 82`
  - 金额显式：期望值带公式 `12.31 = 12.30 + 0.01`，而非纯数字
- 金额/库存等高风险硬断言必须辅以 API 返回 JSON、数据库回写或其他独立业务证据佐证
- 每个 observation 必须在 `assertion_policy` 中分类为 hard 或 soft；硬断言决定结论，软断言只记录差异
- 精确提示文案没有 PRD 逐字依据时必须是 soft；拒绝类 `mutation_committed=false` 必须是 hard

## 8. 数据自造与幂等

- 测试数据命名带唯一标识：`商品名_时间戳`、`分类_时间戳`、`订单号`（系统生成）
- 每条用例独立构造前置数据（下单→支付→…），不依赖其他用例中间状态
- 重复执行不冲突：数据带唯一标识即天然幂等

## 9. 回填数据沉淀（2026-08-24 实测教训）

- **坑**：执行脚本落盘时用 `map(r => ({case_id, final_status, backfilled_from, detail}))` 把 `attempts`、语义轨迹和 observations 丢了 → 回填版只能填「第1遍:passed」状态标记，既无法复审，也无法证明跑的是目标用例
- **正解**：
  - 每个 `runOnce` 必须回传 `semantic_contract_sha256`、`execution_trace`、结构化 `observations` 和同值的 `produced_data`
  - `status: passed` 只能由契约求值器在全部 observations 满足后产生，不能由动作函数返回成功、HTTP 200 或资源创建成功直接决定
  - `status: failed` 同样必须经过契约求值：观察值结构和证据完整，且至少一项明确不满足契约；缺观察值或证据不足应标记 `blocked`
  - **修改/编辑类用例的 produced 必须带操作对象标识**：`{goodsId, goodsName, field}`（如 `{goodsId:27323, goodsName:'AI商品_78264729', field:'价格'}`）——只回填「可编辑: true」但不说改了哪个商品，复审者无法定位，属回填不达标（2026-08-24 用户反馈）；对象标识取执行脚本操作的目标常量（如 `PRODUCT_ID`/`PRODUCT_NAME`）
  - 落盘/合并时**必须保留完整 attempts**（attempt/status/semantic_contract_sha256/execution_trace/observations/produced_data/detail）；`retry_results.jsonl` 是逐条追加的完整记录源，回填生成器优先从 jsonl 读取
  - 回填生成器把 produced 格式化为可读中文写入「实际结果」列（如「订单号=260823214947787916，状态=待发货，金额=16.61」），状态列单独保留 passed/failed
  - 校验/拦截类用例必须同时回填拒绝证据和 `mutation_committed: false`；出现新资源 ID、保存成功或 mutation 为 true 时禁止通过
- 最终结果必须提供结构化 `result_summary`；`actual_result` 仅保留不超过 500 字的可读事实，原始 DOM/长响应进日志

### 回填可读性与阻塞解释（2026-09-03）

为避免“机器能读、人员看不懂”，回填生成阶段将执行器原始页面文本转换为业务事实摘要。每条用例必须能独立阅读：前置条件说明准备了什么，步骤说明实际走到哪里，预期结果保持需求口径，实际结果列列出页面观察到的对象/数值/提示，失败原因列指出未满足的步骤，阻塞原因列给出阶段、已完成/未完成动作、不可判定产品缺陷的理由和解除条件。阻塞原因按“工具定位/时序”“业务前置数据”“账号权限/环境”分类，不得将一条 `Locator` 异常或“未执行核心业务动作”直接当作结论。Markdown 采用一条用例一个审阅卡片，Excel 保留筛选列；两者均不得粘贴完整 DOM。
  - failed 必须写缺陷严重级别建议、确认状态、责任人、缺陷状态和回归状态；blocked 必须先完成能力探测并写入尝试通道与日志
- 反查兜底：执行时未沉淀的订单号/金额，可用 `Order/GetOrderList?orderNo=` 反查补录（需登录态有效）

## 9.1 交付规范化教训（2026-08-24 Codex 对比复盘）

- **三态判定优于两态**：UI 自动化"点击后状态未变"这类结果，如果只有动作发生截图但无法确证产品缺陷，标 `failed` 会冤枉产品（可能是选择器没真正触发），标 `blocked` + blocker_type（环境/工具/证据不足）更严谨——Codex 将 9 条同类问题标为"阻塞"而非"失败"，并给出"已判定项通过率"双口径
- **复用基线必须披露**：Codex 92 条"通过"全部复用我方基线（actual_result 逐字一致、截图 md5 相同）但报告未醒目披露，读者易误以为全部实跑——复用必须标注 `backfilled_round` + 复用占比 + 关键用例独立复核
- **用例集指纹防错位**：Codex 报告引用 TC-SP-099 定义与源表不符（编号口径漂移）——对用例表算 `case_set_fingerprint`，回填/报告引用时校验可拦截
- **按用户模板出回填版**：用户给了模板（00_default_template_base.xlsx）时，Codex 保留了模板全部列（失败步骤/阻塞类型/阻塞原因/证据）再追加状态列——比自定义列更贴交付预期
- **输入 SHA-256 校验**：Codex 在 meta 记录输入文件哈希（input_identity_verified: true），基线复用/对比的前提是输入一致——防"换了 PRD 还拿旧结果"

## 9.2 数据自造闭环（2026-08-24 用户反馈落地）

- **坑**：用例前置写"存在商品/存在待发包裹订单"（声明式），执行脚本直接查已有数据，查不到就报"无数据阻塞"——方案有数据工厂模板但没闭环
- **正解（三层）**：
  1. 用例设计：前置必须三态（自造/系统预置/环境提供）；自造态正式前置只写业务状态，如"需要一条已支付未发货订单"，不得把 `factory.placeOrder()`、`prepare_data` 等构造命令写进正式步骤
  2. 执行框架：`data_setup` / setup strategy 在用例第 1 步前调度构造层——前置自造→探测当前项目可用资源/通道→构造→注入→执行核心 UI→回填 produced；构造路径实际失败才标记 blocked
  3. 工厂接入：统一接口 createProduct/placeOrder/createTag/applyRefund 返回 {id,name,orderNo,ok}，幂等命名，结果写 runtime/<case_id>_data.json；门禁=确认当前项目至少一种构造路径可用，不是把所有前置提前造完
- 参考：case-design.md「前置条件三态」/ browser-execution.md「数据自造生命周期」/ assets/data-factory/README.md

## 10. 截图与证据

- 关键步骤截图 + 失败现场截图（截图超时可用 `timeout: 8000` 包裹 try/catch，避免字体加载阻塞）
- **UI 截图以用例编号命名**：`TC-SP-002.png`，存放 `artifacts/screenshots/`（UI 用例硬性要求，见 browser-execution.md「UI 截图留证」）；纯 API 用例使用请求/响应或日志 `evidence_refs`，不伪造截图
- **截图必须截出关键证据时刻**：校验类截实际提示文案；创建类截保存后的列表行；订单类截订单状态页。不能先固定等待再只读一次页面，也不能在提示已消失后补截空白页面。
- 框架在用例结束时检查截图文件是否存在，缺失记录 `evidence_missing` 并纳入交付自检
- 框架同时计算截图 SHA-256 并写 `evidence.manifest`，绑定 case_id、浏览器、断言时刻、目标、UI 语义键、验证文本和内容锚点；门禁会从磁盘复算哈希并确认锚点实际出现在验证文本
- API observation 使用接口记录 `evidence_refs`，不把 API 键强塞进截图 manifest；兼容性测试每个浏览器必须有独立截图或视频帧

### 瞬态提示捕获（modal / toast / inline / banner）

页面反馈不能默认等同于弹框。每个预期出现 UI 提示的核心动作，都按以下顺序执行：

1. 动作前记录当前可见提示快照，避免把历史弹框或原有表单错误当成本次结果。
2. 执行动作后每 `50～200ms` 轮询一次可见的 modal、toast/message、行内校验文字和页面 banner，默认观察至少 `3000ms`；项目已知更慢时按当前环境上调上限。
3. 捕获第一条与本次业务相关的新提示时，立即读取原文并截图，不等待它自动消失。禁止用固定 `sleep 800ms` 后读取一次页面作为最终判断。
4. 记录 `presentation`、`text`、`appeared_after_ms`、`auto_dismissed` 和截图引用。需要确认自动消失时，在完成截图后继续观察到约定上限。
5. 捕获条件只能用于识别“相关反馈家族”，不能只接受预期原文；否则产品返回通用解析异常时会被错误丢弃。捕获后再按反馈类别、字段锚点和硬/软断言分类。
6. 监控窗口内未捕获预期提示时，只有核心动作已可靠触发、监控范围完整且页面状态可证明时才能判 `failed`；动作或监听可靠性不足时判 `blocked`，不得猜测产品结果。

内置框架提供 `common.ui_feedback.capture_transient_feedback`。推荐用法：

```python
from common.ui_feedback import capture_transient_feedback

feedback = capture_transient_feedback(
    page,
    action=lambda: page.get_by_role("button", name="提交").click(),
    timeout_ms=3000,
    poll_interval_ms=100,
    dismissal_timeout_ms=3500,
    screenshot_path=shots / f"{case_id}_A{attempt}_assertion.png",
)
if feedback is None:
    # Combine this with proof that the core action actually fired before
    # deciding failed versus blocked.
    raise RuntimeError("No relevant UI feedback was captured in the bounded window")
feedback_observation = feedback.as_dict()
```

该助手先做动作前快照，在提示可见时立即截图，并返回结构化观察。调用方仍负责把提示归类为预期业务分支；“看见任意异常文字”不能直接设置 `passed`。

### 变化类操作截图模板（2026-09-02）

对编辑、删除以及任何会改变列表/字段/状态的用例，脚本必须显式调用前后截图，不得以一张“操作完成”截图代替：

```js
await shot(page, caseId, `A${attempt}_before`); // 前置数据已加载，记录对象/字段/状态
await performChange(page);
await waitForStableState(page);
await shot(page, caseId, `A${attempt}_after`);  // 重新查询/打开后，记录变化后的对象/字段/状态
```

`evidence.manifest` 为两张图分别记录 `phase: before|after`、`attempt`、`source_attempt_file`、SHA-256、目标和锚点；`change_comparison` 记录前值/后值（或前后状态）、是否发生变化及对比是否完成。编辑必须能看到同一对象的前后字段值，删除必须能看到删除前对象和删除后不可见/数量变化。任一阶段无法留证时，结果按证据不足阻塞处理。
manifest 的 `content_text`/`observed_target_text` 必须按 phase 取对应的 before_state 或 after_state，不能用最终 after 摘要覆盖前图；`content_anchors` 从该 phase 的实测字段值、对象标识或状态文案提取，并逐一命中 observed_target_text。
- API 返回 JSON 作为业务断言佐证
- 日志记录选择器日志 / 页面文本摘要，便于事后定位

## 10. 常见失败模式速查

| 现象 | 原因 | 处理 |
|------|------|------|
| `locator.click` 超时但元素 resolved | 元素不可见 / 被弹窗遮挡 | `force: true` 或先关弹窗 |
| `fill` 报 not an input | 定位到 div（H5 密码框等） | 改键盘逐键点击 |
| 点击后 URL 不变 | 动作未触发 / 新页面已打开 | 检查 `pages.length`，切新页面 |
| `getByText('确定')` 找不到 | 按钮带空格 `确 定` | 正则 `/确\s*定/` |
| 截图超时 30s | 字体加载阻塞 | `timeout: 8000` + try/catch |
| 页面跳回登录页 | storage_state 过期 | 执行登录态重采 |
| API 返回 30010 | 缺 `Req-Host` 头 | 补 Req-Host |
| 商品创建 UI 卡在弹窗 | antd Modal 上传后不关闭 | 切 API 直连创建（记录 UI 缺陷） |
