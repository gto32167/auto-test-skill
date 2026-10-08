// 数据工厂：鉴权探活模板（Bearer + Req-Host）
// 用法：替换 API_HOST / REQ_HOST / 探活端点后运行，验证 API 通道可用性
const fs = require('fs');
const path = require('path');

const STORAGE = process.env.STORAGE_PATH || '<项目目录>/runtime/backend_storage_state.json';
const API_HOST = process.env.API_HOST || 'https://<api-host>';
const STORE_ID = process.env.STORE_ID || '<storeId>';
const REQ_HOST = process.env.REQ_HOST || '<前端域名>';
const PROBE_ACTION = process.env.PROBE_ACTION || '<Controller>/<Action>'; // 轻量探活端点

(async () => {
  const state = JSON.parse(fs.readFileSync(STORAGE, 'utf-8'));
  const cookie = state.cookies.find(c => /token/i.test(c.name));
  if (!cookie) { console.error('storage_state 未找到 token cookie:', state.cookies.map(c => c.name)); process.exit(1); }
  let token = cookie.value;
  if (!token.startsWith('Bearer ')) token = 'Bearer ' + token;

  const url = `${API_HOST}/${STORE_ID}/${PROBE_ACTION}`;
  const res = await fetch(url, {
    headers: { 'Authorization': token, 'Req-Host': REQ_HOST, 'Content-Type': 'application/json' }
  });
  const j = await res.json().catch(() => ({}));
  console.log('探活:', res.status, 'Code:', j.Code, 'Msg:', j.Msg);
  if (j.Code === 0) { console.log('API 通道 OK'); process.exit(0); }
  else { console.error('API 通道异常'); process.exit(1); }
})().catch(e => { console.error('ERR:', e.message); process.exit(1); });
