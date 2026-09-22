// Exercise the rendered controls using isolated Flask fixtures and local route stubs.
const { chromium } = require('playwright');
const { spawnSync } = require('node:child_process');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const fixture = spawnSync('python', ['-c', `
import json
from tests.test_input_modes import InputModeTests
from core import repo
t = InputModeTests(); t.setUp()
try:
    pp = repo.ensure_product(t.project['id'], 'safety_butler')
    old = '北门有监控死角，优先整改'
    repo.upsert_field_value(pp['id'], 'main_risks', json.dumps(old), 'confirmed', None, '', 'human', '0.4.0', 1)
    repo.upsert_field_value(pp['id'], 'has_cabinet', json.dumps('有机柜，深度1米'), 'confirmed', None, '', 'human', '0.4.0', 1)
    base = '/p/' + pp['id']
    print(json.dumps({'base': base, 'old': old, 'html': t.client.get(base + '/review').get_data(as_text=True)}))
finally: t.tearDown()
`], { cwd: root, encoding: 'utf8', env: { ...process.env, PYTHONUTF8: '1' } });
assert.equal(fixture.status, 0, fixture.stderr);
const { base, old, html } = JSON.parse(fixture.stdout);

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, hasTouch: true });
    const writes = [], errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('http://choices.test/**', async route => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === base + '/field') {
        const body = route.request().postDataJSON();
        writes.push(body);
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify({
          ok: true, status: 'confirmed', status_label: '已人工确认', required_done: 1,
          required_total: 34, required_missing: 33, pending_confirm: 0, validation_ok: false
        }) });
      }
      if (pathname === base + '/review') return route.fulfill({ contentType: 'text/html', body: html });
      if (pathname === base + '/survey') return route.fulfill({ contentType: 'text/html', body: '<p>文字稿</p>' });
      if (/^\/static\/(app|responsive)\.(css|js)$/.test(pathname)) return route.fulfill({
        contentType: pathname.endsWith('.js') ? 'text/javascript' : 'text/css', body: fs.readFileSync(path.join(root, pathname.slice(1)))
      });
      return route.fulfill({ status: 404, body: '' });
    });
    async function saved(key) {
      await page.waitForResponse(r => r.url().endsWith('/field') && r.request().postDataJSON().key === key);
      return writes.filter(w => w.key === key).at(-1).value;
    }
    await page.goto('http://choices.test' + base + '/review');
    const field = key => page.locator('.choice-field[data-key="' + key + '"]');
    const source = field('project_source');
    assert.equal(await source.locator('input[type=radio]').count(), 3);
    assert.equal(await source.locator('input:checked').count(), 0);
    assert.equal(await field('business_type').locator('select').count(), 1);
    let response = saved('project_source');
    await source.getByLabel('外部项目', { exact: true }).check();
    assert.equal(await response, '外部项目');
    response = saved('project_source');
    await source.getByRole('button', { name: '清空此题' }).click();
    assert.equal(await response, '');
    console.log('PASS: short choices are radios, long choices are dropdowns, no defaults, clear saves');

    const risks = field('main_risks');
    response = saved('main_risks');
    await risks.getByLabel('监控盲区', { exact: true }).check();
    assert.deepEqual(await response, ['监控盲区', old]);
    await risks.locator('summary').click();
    response = saved('main_risks');
    await risks.locator('textarea').fill('南门次优先');
    assert.deepEqual(await response, ['监控盲区', old, '南门次优先']);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    const output = path.join(root, 'data', 'responsive-qa');
    fs.mkdirSync(output, { recursive: true });
    await risks.screenshot({ path: path.join(output, 'choices-mobile.png') });
    console.log('PASS: legacy free text survives additional choices and notes');

    for (const key of ['camera_brand', 'storage_brand', 'switch_brand']) {
      const brand = field(key);
      const other = brand.getByLabel('其他', { exact: true });
      const note = brand.locator('textarea');
      const first = brand.locator('input[type=checkbox]').first();
      assert.equal(await note.isVisible(), false);
      response = saved(key);
      await first.check();
      assert.deepEqual(await response, [await first.inputValue()]);
      const count = writes.filter(w => w.key === key).length;
      await other.check();
      await brand.locator('..').getByText('请填写具体品牌名称后保存', { exact: true }).waitFor();
      assert.equal(await note.isVisible(), true);
      assert.equal(await note.getAttribute('required'), '');
      assert.equal(writes.filter(w => w.key === key).length, count);
      response = saved(key);
      await note.fill('天地伟业、TP-LINK');
      assert.deepEqual(await response, [await first.inputValue(), '其他', '天地伟业、TP-LINK']);
      response = saved(key);
      await other.uncheck();
      assert.deepEqual(await response, [await first.inputValue()]);
      assert.equal(await note.isVisible(), false);
      response = saved(key);
      await brand.getByRole('button', { name: '清空此题' }).click();
      assert.deepEqual(await response, []);
    }
    assert.equal(await page.locator('[data-key="service_type"]').count(), 0);
    assert.equal(await field('storage_protocol').getByLabel('是', { exact: true }).count(), 1);
    await source.getByLabel('渠道项目', { exact: true }).waitFor();
    console.log('PASS: optional brand multiselects require other names, save details and clear');

    const cabinet = field('has_cabinet');
    assert.equal(await cabinet.locator('textarea').inputValue(), '有机柜，深度1米');
    response = saved('has_cabinet');
    await cabinet.getByLabel('无机柜', { exact: true }).check();
    assert.equal(await response, '无机柜');
    assert.equal(await cabinet.locator('textarea').isVisible(), false);
    response = saved('has_cabinet');
    await cabinet.getByLabel('其他 / 详细说明', { exact: true }).check();
    assert.equal(await response, '有机柜，深度1米');
    response = saved('has_cabinet');
    await cabinet.locator('textarea').fill('有机柜，余量12U');
    await page.getByRole('link', { name: '导入 / 编辑文字稿', exact: true }).click();
    await page.waitForURL('**/survey');
    assert.equal(await response, '有机柜，余量12U');
    console.log('PASS: custom single answer is editable and saved before navigation');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
