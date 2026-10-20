// Real Flask + SQLite integration; all state and generated screenshots are isolated.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const root = path.resolve(__dirname, '..');
const output = path.join(root, '.verify-temp');
fs.mkdirSync(output, { recursive: true });
const server = spawn(process.env.SHARED_TEST_PYTHON || path.join(root, '.venv/Scripts/python.exe'), ['-u', '-c', `
import json
from flask import session
from werkzeug.serving import make_server
import app as web
from core import db, repo
from tests.test_project_shared_fields import ProjectSharedFieldsTests
case = ProjectSharedFieldsTests(); case.setUp()
case.legacy(case.safety, 'contract_years', '一年')
case.legacy(case.fire, 'contract_years', '三年')
case.legacy(case.safety, 'property_company', '物业甲')
case.legacy(case.fire, 'property_company', '物业乙')
case.migrate_legacy()
web.app.secret_key = 'isolated-shared-fields-browser-test'
cookie = web.app.session_interface.get_signing_serializer(web.app).dumps({'dept_key': 'dept1'})
server = make_server('127.0.0.1', 0, web.app, threaded=True)
print(json.dumps({'port': server.server_port, 'cookie': cookie, 'safety': case.safety['id'], 'fire': case.fire['id']}), flush=True)
try: server.serve_forever()
finally: case.tearDown()
`], { cwd: root, env: { ...process.env, PYTHONUTF8: '1', TEMP: output, TMP: output }, stdio: ['ignore', 'pipe', 'pipe'] });
let stderr = '';
server.stderr.on('data', chunk => { stderr += chunk; });

(async () => {
  let browser;
  try {
    const metadata = await new Promise((resolve, reject) => {
      const lines = readline.createInterface({ input: server.stdout });
      lines.once('line', line => { resolve(JSON.parse(line)); lines.close(); });
      server.once('exit', code => reject(new Error('Fixture exited ' + code + ': ' + stderr)));
    });
    const origin = 'http://127.0.0.1:' + metadata.port;
    browser = await chromium.launch({ channel: 'msedge', headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.addCookies([{ name: 'session', value: metadata.cookie, url: origin }]);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const review = pp => origin + '/p/' + pp + '/review';
    const input = key => page.locator('.fi[data-key="' + key + '"]');
    async function save(key, value) {
      const response = page.waitForResponse(r => r.url().endsWith('/field') && r.request().postDataJSON().key === key);
      await input(key).fill(value);
      await input(key).blur();
      const result = await response;
      assert.equal(result.status(), 200, await result.text());
    }
    await page.goto(review(metadata.safety));
    assert.equal(await page.locator('[data-shared-conflict]').count(), 2);
    await page.locator('[data-shared-conflict="contract_years"]').screenshot({ path: path.join(output, 'shared-conflict-desktop.png') });
    await save('project_name', '浏览器共享项目');
    await save('property_company', '浏览器确认的新物业');
    assert.equal(await page.locator('[data-shared-conflict="property_company"]').count(), 0);
    await page.goto(review(metadata.fire));
    assert.equal(await input('project_name').inputValue(), '浏览器共享项目');
    assert.equal(await input('property_company').inputValue(), '浏览器确认的新物业');
    const source = page.locator('.choice-field[data-key="project_source"]');
    assert.equal(await source.locator('input[type="radio"]').count(), 4);
    const saved = page.waitForResponse(r => r.url().endsWith('/field') && r.request().postDataJSON().key === 'project_source');
    await source.getByLabel('渠道项目', { exact: true }).check();
    assert.equal((await saved).status(), 200);
    console.log('PASS: real autosave, refresh and source choices share across butlers');
    const conflict = page.locator('[data-shared-conflict="contract_years"]');
    await Promise.all([
      page.waitForNavigation(),
      conflict.getByRole('button', { name: '采用这份答案并共享' }).first().click()
    ]);
    assert.equal(await page.locator('[data-shared-conflict]').count(), 0);
    const contract = await input('contract_years').inputValue();
    assert.ok(['一年', '三年'].includes(contract));
    await page.goto(review(metadata.safety));
    assert.equal(await input('contract_years').inputValue(), contract);
    assert.equal(await page.locator('[data-shared-conflict]').count(), 0);
    assert.equal(await page.locator('.choice-field[data-key="project_source"] input:checked').inputValue(), '渠道项目');
    await save('project_name', '');
    await page.goto(review(metadata.fire));
    assert.equal(await input('project_name').inputValue(), '');
    console.log('PASS: conflict choice and clearing are shared through the real database');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(review(metadata.fire));
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(output, 'shared-form-mobile.png') });
    assert.deepEqual(errors, []);
    console.log('PASS: mobile layout fits, no browser runtime errors');
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
