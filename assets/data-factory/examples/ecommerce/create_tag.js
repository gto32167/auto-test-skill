// 数据工厂：创建标签模板（含删除，幂等命名）
// 用法：替换 <DOMAIN>/<STORE>/<TOKEN 提取> 后运行；返回 { tagId, name }
// 统一接口：factory.createTag(name?) -> { tagId, name }
const fs = require('fs');

const STORAGE = '<项目>/<storage_state>.json';       // 替换：登录态文件
const API = '<api-domain>/<storeId>';                 // 替换为当前项目的 API 地址
const REQ_HOST = '<前端域名>';                         // 替换为当前项目的前端域名
const HEADERS = buildHeaders();

function buildHeaders() {
  const state = JSON.parse(fs.readFileSync(STORAGE, 'utf-8'));
  const cookie = state.cookies.find(c => /token/i.test(c.name));
  let token = cookie.value;
  if (!token.startsWith('Bearer ')) token = 'Bearer ' + token;
  return { 'Authorization': token, 'Req-Host': REQ_HOST, 'Content-Type': 'application/json' };
}

async function api(controller, action, method = 'POST', body = {}, qs = '') {
  const url = `${API}/${controller}/${action}${qs ? '?' + qs : ''}`;
  const res = await fetch(url, { method, headers: HEADERS, body: JSON.stringify(body) });
  return res.json().catch(() => ({}));
}

// 统一接口：创建标签（幂等：名称带时间戳）
async function createTag(name) {
  const tagName = name || 'AI标签_' + Date.now().toString().slice(-5);
  const r = await api('Product', 'CreateProductTag', 'POST', { name: tagName });
  if (r.Code === 0) {
    const tagId = (r.Data && (r.Data.Id || r.Data.id || r.Data.TagId)) || null;
    return { tagId, name: tagName, ok: true };
  }
  return { tagId: null, name: tagName, ok: false, msg: r.Msg };
}

// 统一接口：删除标签
async function deleteTag(tagId) {
  const r = await api('Product', 'DeleteTag', 'POST', { id: tagId });
  return { ok: r.Code === 0, msg: r.Msg };
}

module.exports = { createTag, deleteTag };

// 独立运行：node create_tag.js
if (require.main === module) {
  (async () => {
    const t = await createTag();
    console.log('createTag =>', JSON.stringify(t));
    if (t.ok && t.tagId) {
      const d = await deleteTag(t.tagId);
      console.log('deleteTag =>', JSON.stringify(d));
    }
  })();
}
