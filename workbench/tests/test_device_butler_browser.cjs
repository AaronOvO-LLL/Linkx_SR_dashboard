// Isolated Flask page + real browser: room cards, autosave, and review navigation.
const { chromium } = require('playwright');
const { spawnSync } = require('node:child_process');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const fixture = spawnSync(path.join(root, '.venv/Scripts/python.exe'), ['-c', `
import json, tempfile
from pathlib import Path
tempfile.tempdir = str(Path('.verify-temp').resolve())
from tests.test_device_butler import DeviceButlerFlowTests, output
from core import rules
t = DeviceButlerFlowTests(); t.setUp()
try:
    t.extract(t.sample('full.md'))
    base = '/p/' + t.pp['id']
    existing = t.table()
    next(r for r in existing if r['device'] == '设备房水浸')['qty'] = 0
    existing.append({'room_name': '历史 "A" <房间>', 'space_type': '旧类型', 'device': '旧设备', 'qty': 2})
    t.save_table(existing)
    existing = t.table()
    existing_html = t.client.get(base + '/review').get_data(as_text=True)
    t.save_table([])
    rows = rules.materialize(output(), [{'room_name': '新配电房', 'space_type': '配电房（基础）', 'device': '', 'qty': ''}])
    print(json.dumps({'base': base, 'html': t.client.get(base + '/review').get_data(as_text=True), 'rows': rows,
                     'existing': existing, 'existingHtml': existing_html}))
finally:
    t.tearDown()
`], { cwd: root, encoding: 'utf8', env: { ...process.env, PYTHONUTF8: '1' } });
assert.equal(fixture.status, 0, fixture.stderr);
const { base, html, rows, existing, existingHtml } = JSON.parse(fixture.stdout);

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage();
    const errors = [], writes = [];
    let failSave = false;
    page.on('pageerror', error => errors.push(error.message));
    await page.route('http://device.test/**', async route => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === base + '/field') {
        const body = route.request().postDataJSON();
        writes.push(body);
        if (failSave) return route.fulfill({ status: 500, body: 'test failure' });
        const declaration = body.value.some(r => r.room_name === '新配电房' && r.space_type === '配电房（基础）' && !r.device);
        await new Promise(resolve => setTimeout(resolve, 100));
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify({
          ok: true, status: 'confirmed', status_label: '已人工确认',
          table_rows: declaration ? rows : body.value,
          validation_ok: true, issues: [], required_done: 1, required_total: 1,
          pending_confirm: 0, required_missing: 0
        }) });
      }
      if (pathname === base + '/review') return route.fulfill({ contentType: 'text/html', body: route.request().url().includes('?existing') ? existingHtml : html });
      if (pathname === base + '/preview') return route.fulfill({ contentType: 'text/html', body: '<h1>设备清单审阅</h1>' });
      if (/^\/static\/(app|responsive|room-editor)\.(css|js)$/.test(pathname)) return route.fulfill({
        contentType: pathname.endsWith('.js') ? 'text/javascript' : 'text/css',
        body: fs.readFileSync(path.join(root, pathname.slice(1)))
      });
      return route.fulfill({ status: 404, body: '' });
    });
    await page.goto('http://device.test' + base + '/review');
    const editor = page.locator('.room-editor');
    const cards = editor.locator('.room-card');
    const saved = () => page.waitForFunction(() => document.querySelector('.room-editor').closest('.fld-row').querySelector('[data-role=saved]').textContent === '已保存');
    assert.equal(await editor.locator('table').count(), 0);
    await editor.getByRole('button', { name: '+ 新增设备房', exact: true }).click();
    await cards.first().getByLabel('设备房名称', { exact: true }).fill('新配电房');
    await cards.first().getByLabel('设备房类型', { exact: true }).selectOption('配电房（基础）');
    await saved();
    assert.deepEqual(await cards.first().locator('[data-device]').evaluateAll(els => els.map(el => el.dataset.device)), rows.map(r => r.device));
    assert.deepEqual(await cards.first().locator('[data-device]').evaluateAll(els => els.map(el => el.value)), ['1', '1', '1', '1']);
    await cards.first().locator('summary').click();
    const water = cards.first().locator('[data-device="设备房水浸"]');
    await water.fill('0');
    await saved();
    assert.equal(writes.at(-1).value.find(r => r.device === '设备房水浸').qty, '0');
    assert.equal(await water.inputValue(), '0');

    // Each click adds a separate room; duplicate names cannot be merged by the backend.
    await editor.locator('[data-add-room]').click();
    await cards.nth(1).getByLabel('设备房名称', { exact: true }).fill('新配电房');
    await cards.nth(1).getByLabel('设备房类型', { exact: true }).selectOption('独立生活水泵房');
    await page.waitForFunction(() => document.querySelector('.room-error').textContent.includes('不能重复'));
    let validationMessage = '';
    page.once('dialog', async dialog => { validationMessage = dialog.message(); await dialog.accept(); });
    await page.locator('#nextBtn').click();
    await page.waitForTimeout(150);
    assert.match(validationMessage, /名称不能重复/);
    await cards.nth(1).getByLabel('设备房名称', { exact: true }).fill('1#生活泵房');
    const pump = cards.nth(1).locator('[data-device="给水生活水池/水箱"]');
    await pump.fill('3');
    await saved();
    assert.equal(new Set(writes.at(-1).value.map(r => r.room_name)).size, 2);
    assert.equal(writes.at(-1).value.find(r => r.device === '给水生活水池/水箱').qty, '3');

    // Switching types restores the previous type's draft when switched back.
    await cards.nth(1).getByLabel('设备房类型', { exact: true }).selectOption('空调机房');
    assert.equal(await cards.nth(1).locator('[data-device="给水生活水池/水箱"]').count(), 0);
    await cards.nth(1).getByLabel('设备房类型', { exact: true }).selectOption('独立生活水泵房');
    assert.equal(await pump.inputValue(), '3');
    await saved();
    await pump.fill('-1');
    await page.waitForFunction(() => document.querySelector('.room-error').textContent.includes('正整数'));
    await pump.fill('1.5');
    await page.waitForTimeout(800);
    assert.equal(await editor.locator('.room-error').isVisible(), true);
    await pump.fill('2');
    await saved();

    // Small screen: cards fit the viewport, and no horizontal table entry remains.
    await page.setViewportSize({ width: 390, height: 844 });
    await editor.scrollIntoViewIfNeeded();
    assert.equal(await editor.evaluate(el => el.scrollWidth <= el.clientWidth), true);
    await editor.screenshot({ path: path.join(root, '.verify-temp/device-room-cards-mobile.png') });
    await page.setViewportSize({ width: 1365, height: 1000 });
    await editor.screenshot({ path: path.join(root, '.verify-temp/device-room-cards-desktop.png') });

    page.once('dialog', dialog => dialog.accept());
    await cards.nth(1).getByRole('button', { name: '删除设备房', exact: true }).click();
    await saved();
    assert.equal(writes.at(-1).value.every(r => r.room_name === '新配电房'), true);

    // Failed saves block navigation; retry flushes the newest value before review.
    failSave = true;
    await water.fill('4');
    await page.waitForFunction(() => document.querySelector('.room-editor').closest('.fld-row').querySelector('[data-role=saved]').textContent.includes('保存失败'));
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#nextBtn').click();
    await page.waitForTimeout(250);
    assert.equal(new URL(page.url()).pathname, base + '/review');
    failSave = false;
    await water.fill('5');
    await page.locator('#nextBtn').click();
    await page.waitForURL('**/preview');
    assert.equal(writes.at(-1).value.find(r => r.device === '设备房水浸').qty, '5');

    // Existing extracted/manual rows hydrate without losing zeros, unknown items, or quoted names.
    await page.goto('http://device.test' + base + '/review?existing');
    assert.equal(await cards.count(), new Set(existing.map(r => JSON.stringify([r.room_name, r.space_type]))).size);
    const restored = await editor.evaluate(el => window.RoomEditor.read(el));
    const normalize = values => values.map(r => JSON.stringify([r.room_name, r.space_type, r.device, String(r.qty ?? '')])).sort();
    assert.deepEqual(normalize(restored), normalize(existing));
    assert.equal(await cards.last().getByLabel('设备房名称', { exact: true }).inputValue(), '历史 "A" <房间>');
    await cards.last().getByLabel('设备房类型', { exact: true }).selectOption('配电房（基础）');
    await saved();
    page.once('dialog', dialog => dialog.accept());
    await cards.last().getByRole('button', { name: '删除设备房', exact: true }).click();
    await saved();
    await cards.first().getByLabel('设备房名称', { exact: true }).fill('重命名泵房');
    await saved();
    assert.equal(writes.at(-1).value.filter(r => r.room_name === '重命名泵房').length, existing.filter(r => r.room_name === existing[0].room_name).length);
    assert.deepEqual(errors, []);
    console.log('PASS: room cards, type options, quantities, validation, deletion, mobile layout, and save-before-review');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
