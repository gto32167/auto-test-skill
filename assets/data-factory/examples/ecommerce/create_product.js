// 数据工厂：创建商品模板（UI 受阻时的 API 兜底通道）
// 用法：替换 API_HOST/REQ_HOST/表单字段后调用
const fs = require('fs');
const path = require('path');

const STORAGE = process.env.STORAGE_PATH || '<项目目录>/runtime/backend_storage_state.json';
const API_HOST = process.env.API_HOST || 'https://<api-host>';
const STORE_ID = process.env.STORE_ID || '<storeId>';
const REQ_HOST = process.env.REQ_HOST || '<前端域名>';

function tokenFrom(state) {
  const cookie = state.cookies.find(c => /token/i.test(c.name));
  let t = cookie.value;
  if (!t.startsWith('Bearer ')) t = 'Bearer ' + t;
  return t;
}

async function call(action, body) {
  const res = await fetch(`${API_HOST}/${STORE_ID}/${action}`, {
    method: 'POST',
    headers: { 'Authorization': tokenFrom(JSON.parse(fs.readFileSync(STORAGE, 'utf-8'))), 'Req-Host': REQ_HOST, 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  return res.json();
}

(async () => {
  const name = '商品_' + Date.now().toString().slice(-8);
  // 字段名以真实环境 GET 返回为准；此处为示例骨架
  const body = {
    name,
    goods_no: 'TS' + Date.now().toString().slice(-8),
    price: '12.30',
    total_stock: 100,
    // ... 按真实环境补齐
  };
  const j = await call('Product/CreateProduct', body);
  console.log('CreateProduct:', j.Code, j.Msg, '| 名称:', name);
  // 回写供用例断言
  fs.writeFileSync('runtime/created_product.json', JSON.stringify({ goodsName: name, code: j.Code, msg: j.Msg, at: new Date().toISOString() }));
  process.exit(j.Code === 0 ? 0 : 1);
})().catch(e => { console.error('ERR:', e.message); process.exit(1); });
