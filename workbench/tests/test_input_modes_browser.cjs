// Run with NODE_PATH pointing to Playwright and PYTHONPATH containing Flask.
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
t = InputModeTests()
t.setUp()
try:
    pp = repo.ensure_product(t.project['id'], 'fire_butler')
    base = '/p/' + pp['id']
    pages = {base + '/' + page: t.client.get(base + '/' + page).get_data(as_text=True)
             for page in ('survey', 'review')}
    print(json.dumps({'base': base, 'pages': pages}))
finally:
    t.tearDown()
`], { cwd: root, encoding: 'utf8', env: { ...process.env, PYTHONUTF8: '1' } });
assert.equal(fixture.status, 0, fixture.stderr);
const { base, pages } = JSON.parse(fixture.stdout);

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let failSaves = false;
    const writes = [];
    await page.route('http://input-modes.test/**', async route => {
      const request = route.request();
      const pathname = new URL(request.url()).pathname;
      if (pathname === base + '/field') {
        const body = request.postDataJSON();
        if (failSaves) return route.fulfill({ status: 500, body: 'Save failed' });
        // A slow response exposes navigation-before-save races.
        await new Promise(resolve => setTimeout(resolve, 120));
        writes.push(body);
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify({
          ok: true, status: 'confirmed', status_label: '已人工确认',
          required_done: 1, required_total: 15, required_missing: 14,
          pending_confirm: 0, validation_ok: false
        }) });
      }
      if (pages[pathname]) return route.fulfill({ contentType: 'text/html', body: pages[pathname] });
      if (['/static/app.js', '/static/app.css', '/static/responsive.js', '/static/responsive.css'].includes(pathname)) {
        return route.fulfill({ contentType: pathname.endsWith('.js') ? 'text/javascript' : 'text/css',
          body: fs.readFileSync(path.join(root, pathname.slice(1)), 'utf8') });
      }
      return route.fulfill({ status: 404, body: '' });
    });

    await page.goto('http://input-modes.test' + base + '/review');
    const field = page.locator('.fi[data-type="text"]').first();
    await field.fill('切换前刚输入的人工内容');
    await page.getByRole('link', { name: '导入 / 编辑文字稿', exact: true }).click();
    await page.waitForURL('**/survey');
    assert.equal(writes.at(-1).value, '切换前刚输入的人工内容');
    console.log('PASS: immediate mode switch waits for manual field save');

    await page.locator('#transcript').fill('尚未提交的逐字稿草稿');
    await page.getByRole('link', { name: '直接手动填写 →', exact: true }).click();
    await page.waitForURL('**/review');
    await page.getByRole('link', { name: '导入 / 编辑文字稿', exact: true }).click();
    await page.waitForURL('**/survey');
    assert.equal(await page.locator('#transcript').inputValue(), '尚未提交的逐字稿草稿');
    console.log('PASS: transcript draft survives round-trip switching');

    page.once('dialog', dialog => dialog.accept());
    await page.locator('#transcriptFile').setInputFiles({ name: 'transcript.txt', mimeType: 'text/plain',
      buffer: Buffer.from('已完成转写的文字文件内容', 'utf8') });
    await page.waitForFunction(() => document.getElementById('transcript').value === '已完成转写的文字文件内容');
    console.log('PASS: existing transcript file imports into the editable draft');

    await page.getByRole('link', { name: '直接手动填写 →', exact: true }).click();
    await page.waitForURL('**/review');
    failSaves = true;
    await page.locator('.fi[data-type="text"]').first().fill('网络失败时不能丢失的内容');
    const dialogPromise = page.waitForEvent('dialog');
    await page.getByRole('link', { name: '导入 / 编辑文字稿', exact: true }).click();
    const dialog = await dialogPromise;
    assert.match(dialog.message(), /未保存/);
    await dialog.accept();
    assert.match(page.url(), /\/review$/);
    assert.equal(await page.locator('.fi[data-type="text"]').first().inputValue(), '网络失败时不能丢失的内容');
    failSaves = false;
    await page.getByRole('link', { name: '导入 / 编辑文字稿', exact: true }).click();
    await page.waitForURL('**/survey');
    assert.equal(writes.at(-1).value, '网络失败时不能丢失的内容');
    console.log('PASS: failed save retains the form and retry succeeds');
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
