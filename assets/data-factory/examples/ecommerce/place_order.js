// 数据工厂：下单支付模板（H5 用户端，构造"已支付订单"供售后/发货用例）
// 流程：商品详情 → 立即购买 → 规格确认 → 选配送方式 → 提交订单 → 余额支付
// 要点：H5 支付密码为虚拟键盘（.secret-box + .keyboard-item 逐键点击），非 input
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const H5_STATE = process.env.H5_STATE || '<项目目录>/runtime/h5_storage_state.json';
const PRODUCT_URL = process.env.PRODUCT_URL || '<H5 商品详情 URL>';
const PAY_PWD = process.env.PAY_PWD || '<支付密码>';

(async () => {
  const browser = await chromium.launch({ headless: false, slowMo: 30 });
  const ctx = await browser.newContext({ viewport: { width: 414, height: 896 }, storageState: H5_STATE });
  const p = await ctx.newPage();
  p.setDefaultTimeout(15000);

  await p.goto(PRODUCT_URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await p.waitForTimeout(5000);
  await p.getByText('立即购买', { exact: true }).first().click({ force: true });
  await p.waitForTimeout(2500);
  // 规格弹窗确认
  const rulePopup = p.locator('.rule-popup').last();
  if (await rulePopup.count() > 0 && (await rulePopup.innerText()).includes('数量')) {
    const ci = rulePopup.locator('text=确认').first();
    if (await ci.count() > 0) { await ci.click({ force: true }); await p.waitForTimeout(3000); }
  }
  // 确保进入订单确认页（含"提交订单"）
  for (let i = 0; i < 5; i++) {
    const body = await p.locator('body').innerText();
    if (body.includes('提交订单')) break;
    if (body.includes('数量') && (await p.getByText('确认', { exact: true }).count()) > 0) {
      await p.getByText('确认', { exact: true }).first().click({ force: true });
      await p.waitForTimeout(1000); continue;
    }
    break;
  }
  // 关闭积分抵扣（默认开启会导致金额变化，影响金额断言）
  const sw = p.locator('.van-switch, [role=switch]').first();
  if (await sw.count() > 0 && await sw.getAttribute('aria-checked') === 'true') {
    await sw.click({ force: true }).catch(() => {}); await p.waitForTimeout(500);
  }
  // 提交订单
  const submit = p.getByText('提交订单', { exact: true }).first();
  if (await submit.count() > 0) { await submit.click({ force: true }); await p.waitForTimeout(5000); }
  // 支付：余额支付 → 立即支付 → 虚拟键盘输入密码
  for (let i = 0; i < 8; i++) {
    if (await p.locator('.keyboard-item').count() > 0) break;
    if (await p.locator('.secret-box').count() > 0) { await p.locator('.secret-box').first().click({ force: true }); await p.waitForTimeout(1200); continue; }
    const bal = p.getByText('余额支付', { exact: false }).first();
    if (await bal.count() > 0) { await bal.click({ force: true }); await p.waitForTimeout(1200); continue; }
    const ip = p.getByText('立即支付', { exact: true }).first();
    if (await ip.count() > 0) { await ip.click({ force: true }); await p.waitForTimeout(1000); continue; }
    const ip2 = p.getByText('立即付款', { exact: true }).first();
    if (await ip2.count() > 0) { await ip2.click({ force: true }); await p.waitForTimeout(1500); continue; }
    break;
  }
  const kb = p.locator('.keyboard-item');
  if (await kb.count() > 0) {
    for (const d of PAY_PWD) {
      const key = p.locator('.keyboard-item', { hasText: d }).first();
      if (await key.count() > 0) { await key.click({ force: true }); await p.waitForTimeout(400); }
    }
  }
  await p.waitForTimeout(4000);
  const payText = await p.locator('body').innerText();
  const payOk = payText.includes('支付成功');
  console.log('支付成功:', payOk);
  // 提取订单号（支付页无则从待发货列表取）
  let orderNo = (payText.match(/订单(?:号|编号)[:：]?\s*([0-9]{8,})/) || [])[1] || null;
  if (!orderNo) {
    await p.goto('<H5 订单列表 URL>', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await p.waitForTimeout(4000);
    const lt = await p.locator('body').innerText();
    const mm = lt.match(/订单号[：:]\s*([0-9]{8,})/);
    orderNo = mm ? mm[1] : null;
  }
  fs.writeFileSync('runtime/placed_order.json', JSON.stringify({ orderNo, payOk, at: new Date().toISOString() }));
  console.log('订单号:', orderNo);
  await browser.close();
  process.exit(payOk && orderNo ? 0 : 1);
})().catch(e => { console.error('ERR:', e.message); process.exit(1); });
