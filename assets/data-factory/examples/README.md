# 电商系统造数器示例（仅供理解，勿直接复用）

> 这是「数据工厂」在**电商 SaaS 系统（云商城）**上的一个具体实例，用来说明"如何从项目背景文档生成造数器"。
> 换测试对象时，参照本示例 + 新项目的背景文档，生成新系统专属造数器（如 OA 的 createRequest、CRM 的 createCustomer）。

## 本示例如何从背景文档生成

以云商城项目背景.md 为输入，提取信息对照：

| 背景文档内容（云商城） | 生成的造数器 |
|------------------------|--------------|
| 业务实体：商品/分类/标签/订单 | create_product.js / create_group.js / create_tag.js / place_order.js |
| 造数端点：`Product/CreateProduct`（storeapi） | create_product.js 内部调用 |
| 鉴权：Bearer + Req-Host（缺 Req-Host 返 30010） | auth_template.js 统一封装 |
| 账号：18674731640 / dev 验证码 82d1f6 | 登录态 storage_state + H5 下单 |
| 商品创建真实参数：goodsUse/picture.imageUrl/total_stock/delivery 数组 | create_product.js body 构造 |

## 关键实测坑（已沉淀到 execution-playbook.md）

- 商品创建必须 `goodsUse:[1]` + `picture:[{imageUrl}]` + `total_stock`，缺报 30902/30002
- H5 支付密码为虚拟键盘（.secret-box + .keyboard-item 逐键点击），非 input
- dev 短信验证码固定 82d1f6

## 换系统时怎么改

1. 读新项目背景文档，列出核心业务实体（不要沿用商品/订单假设）
2. **没有背景文档？先探测**：JS 反编译抓 API 端点 → 网络监听抓请求体 → UI 录制抓业务流，探测结果整理成背景文档再继续（探测产物 = 背景文档）
3. 每个实体对应一个 `create<Entity>` 造数器
4. 从背景文档取：造数端点 / 鉴权头 / 账号 / 路由（走 UI 时）
5. 统一返回 `{ id, name, ok, msg }`，接口名保持 `factory.create<Entity>()` 风格
6. 硬规则：造数必须基于实测事实（文档或探测），禁止拍脑袋假设实体/端点/参数
