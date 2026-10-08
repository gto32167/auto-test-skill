// 数据工厂：创建分类模板（一/二级）+ 创建/删除标签模板
// 注意：真实端点字段名以 GET 返回为准，示例为常见形态
const fs = require('fs');
const path = require('path');

const STORAGE = process.env.STORAGE_PATH || '<项目目录>/runtime/backend_storage_state.json';
const API_HOST = process.env.API_HOST || 'https://<api-host>';
const STORE_ID = process.env.STORE_ID || '<storeId>';
const REQ_HOST = process.env.REQ_HOST || '<前端域名>';

const state = JSON.parse(fs.readFileSync(STORAGE, 'utf-8'));
const cookie = state.cookies.find(c => /token/i.test(c.name));
let token = cookie.value;
if (!token.startsWith('Bearer ')) token = 'Bearer ' + token;
const headers = { 'Authorization': token, 'Req-Host': REQ_HOST, 'Content-Type': 'application/json' };

async function post(action, body) {
  const res = await fetch(`${API_HOST}/${STORE_ID}/${action}`, { method: 'POST', headers, body: JSON.stringify(body) });
  return res.json();
}
async function get(action, params) {
  const res = await fetch(`${API_HOST}/${STORE_ID}/${action}${params ? '?' + params : ''}`, { headers });
  return res.json();
}

(async () => {
  const suffix = Date.now().toString().slice(-5);

  // 1. 一级分类（先 GET 拿已有分类确认字段结构）
  const groups = await get('Product/GetFirstAndSecondList', '');
  console.log('现有分类字段示例:', Array.isArray(groups.Data) && groups.Data[0] ? Object.keys(groups.Data[0]) : '空');

  // 2. 二级分类（常见字段：FirstId + Name + ImgPath）
  const cat2 = '二级分类_' + suffix;
  const firstGroupId = process.env.FIRST_GROUP_ID || '<first-group-id>';
  const imagePath = process.env.IMAGE_PATH || '<image-path>';
  const r2 = await post('Product/CreateSecondGroup', { FirstId: firstGroupId, Name: cat2, ImgPath: imagePath });
  console.log('CreateSecondGroup:', r2.Code, r2.Msg, cat2);

  // 3. 创建标签（注意名称字节长度限制，一般 ≤12 字节，用短名）
  const tag = 'TAG' + suffix;
  const r3 = await post('Product/CreateProductTag', { Name: tag });
  console.log('CreateProductTag:', r3.Code, r3.Msg, tag);

  // 4. 删除标签
  const tags = await get('Product/GetTagList', '');
  const obj = (Array.isArray(tags.Data) ? tags.Data : []).find(t => (t.name || t.Name || '') === tag);
  if (obj) {
    const tid = obj.id || obj.Id;
    const r4 = await post('Product/DeleteTag', { Id: tid });
    console.log('DeleteTag:', r4.Code, r4.Msg, 'id=', tid);
  }

  fs.writeFileSync('runtime/created_group_tag.json', JSON.stringify({ cat2, tag, at: new Date().toISOString() }));
  process.exit(0);
})().catch(e => { console.error('ERR:', e.message); process.exit(1); });
