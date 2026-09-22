// NODE_PATH: bundled Playwright; PYTHONPATH: local Flask dependencies.
// Fixtures use a temporary database and output directory, never real project data.
const { chromium } = require('playwright');
const { spawnSync } = require('node:child_process');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const fixture = spawnSync('python', ['-c', `
import json
from pathlib import Path
from unittest.mock import patch
from tests.test_input_modes import InputModeTests
import app as web
from core import repo, paths
from core.config import field_map, artifact_map
from services import artifacts
t = InputModeTests()
t.setUp()
pages = {}
try:
    pid = t.project['id']
    repo.update_project(pid, {'name': '手机与电脑响应式检查：超长项目名称_' + 'LongProjectName' * 8})
    pp = repo.ensure_product(pid, 'fire_butler')
    safety = repo.ensure_product(pid, 'safety_butler')
    for name in ('review', 'preview'):
        pages['safety_' + name] = t.client.get('/p/' + safety['id'] + '/' + name).get_data(as_text=True)
    base = '/p/' + pp['id']
    pages['review'] = t.client.get(base + '/review').get_data(as_text=True)
    for key, field in field_map('fire_butler').items():
        value = 4 if field['type'] == 'number' else (field['options'][0]['value'] if field['type'] == 'select' else ([field['options'][0]['value']] if field['type'] == 'multiselect' else '现场已确认'))
        t.client.post(base + '/field', json={'key': key, 'value': value})
    run = repo.start_extraction_run(pp['id'], 'rule', pp['template_version'], 320)
    repo.finish_extraction_run(run, 'success', {}, '')
    with patch.object(paths, 'OUTPUT_DIR', str(Path(t.tmp.name) / 'outputs')), patch.object(paths, 'EXPORT_DIR', str(Path(t.tmp.name) / 'exports')):
        artifacts.generate_artifacts(pp, list(artifact_map('fire_butler')))
        artifacts.package_artifacts(pp)
        for name in ('survey', 'preview', 'result'):
            response = t.client.get(base + '/' + name)
            assert response.status_code == 200
            pages[name] = response.get_data(as_text=True)
    job = repo.create_audio_job(pid, '现场录音_' + 'VeryLongFilename' * 8 + '.mp3', '/unused/test.mp3', 123)
    repo.update_audio_job(job['id'], status='awaiting_preview', corrected_transcript='现场访谈内容。' * 80)
    for name, url in [('project', '/projects/' + pid), ('projects', '/projects'), ('password', '/password'), ('transcription', '/projects/' + pid + '/audio/' + job['id'])]:
        response = t.client.get(url)
        assert response.status_code == 200
        pages[name] = response.get_data(as_text=True)
    original_groups = web.fields_by_group
    table_field = {'key': 'qa_table', 'label': '设备明细', 'type': 'table', 'required': False, 'columns': [{'key': 'name', 'label': '名称'}, {'key': 'model', 'label': '型号'}, {'key': 'note', 'label': '备注'}]}
    with patch.object(web, 'fields_by_group', side_effect=lambda p: list(original_groups(p)) + [({'name': '设备明细', 'key': 'qa'}, [table_field])]):
        pages['table'] = t.client.get(base + '/review').get_data(as_text=True)
    pages['login'] = web.app.test_client().get('/login').get_data(as_text=True)
    print(json.dumps(pages))
finally:
    t.tearDown()
`], { cwd: root, encoding: 'utf8', maxBuffer: 8 * 1024 * 1024,
  env: { ...process.env, PYTHONUTF8: '1' } });
assert.equal(fixture.status, 0, fixture.stderr);
const pages = JSON.parse(fixture.stdout);

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const output = path.join(root, 'data', 'responsive-qa');
  fs.mkdirSync(output, { recursive: true });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('http://responsive.test/**', async route => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname.endsWith('/field')) return route.fulfill({ contentType: 'application/json', body: JSON.stringify({
        ok: true, status: 'confirmed', status_label: '已人工确认', required_done: 1,
        required_total: 26, required_missing: 25, pending_confirm: 0, validation_ok: false
      }) });
      if (pages[pathname.slice(1)]) return route.fulfill({ contentType: 'text/html', body: pages[pathname.slice(1)] });
      if (/^\/static\/(app|responsive)\.(css|js)$/.test(pathname)) return route.fulfill({
        contentType: pathname.endsWith('.js') ? 'text/javascript' : 'text/css',
        body: fs.readFileSync(path.join(root, pathname.slice(1)))
      });
      return route.fulfill({ status: 404, body: '' });
    });
    for (const width of [320, 375, 390, 430, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      for (const name of Object.keys(pages)) {
        await page.goto('http://responsive.test/' + name);
        const layout = await page.evaluate(() => ({
          viewport: innerWidth, width: document.documentElement.scrollWidth,
          overflow: [...document.querySelectorAll('body *')].filter(el => {
            const r = el.getBoundingClientRect();
            return r.width && r.right > innerWidth + 1 && !el.closest('.table-scroll');
          }).slice(0, 5).map(el => el.tagName + '.' + el.className)
        }));
        assert.ok(layout.width <= width + 1, name + ' at ' + width + ': ' + JSON.stringify(layout));
        if (name !== 'login') {
          assert.equal(await page.locator('.nav-toggle').isVisible(), width <= 860);
          assert.equal(await page.locator('#mainNav').isVisible(), width > 860);
        }
        if (name.endsWith('preview')) {
          const display = await page.locator('.summary-table th').first().evaluate(el => getComputedStyle(el).display);
          assert.equal(display, width <= 680 ? 'block' : 'table-cell');
        }
        if ([390, 1440].includes(width) && ['projects', 'review', 'preview', 'result'].includes(name)) {
          await page.screenshot({ path: path.join(output, name + '-' + width + '.png'), fullPage: name !== 'review' });
        }
      }
      console.log('PASS: ' + Object.keys(pages).length + ' pages at ' + width + 'px, no page overflow');
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('http://responsive.test/projects');
    await page.getByRole('button', { name: '菜单', exact: true }).click();
    assert.equal(await page.locator('.nav-toggle').getAttribute('aria-expanded'), 'true');
    assert.equal(await page.getByRole('link', { name: '修改密码', exact: true }).isVisible(), true);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#mainNav').isVisible(), false);
    await page.setViewportSize({ width: 1440, height: 900 });
    assert.equal(await page.locator('#mainNav').isVisible(), true);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.locator('#mainNav').isVisible(), false);
    console.log('PASS: mobile menu opens, Escape closes, desktop resize restores navigation');
    await page.goto('http://responsive.test/table');
    const wrapper = page.locator('.table-scroll');
    assert.ok(await wrapper.evaluate(el => el.scrollWidth > el.clientWidth));
    await page.getByRole('button', { name: '+ 添加一行', exact: true }).click();
    assert.equal(await page.locator('.table-scroll tbody tr').count(), 1);
    await page.locator('.table-scroll input').first().fill('摄像机');
    await page.locator('.table-scroll button').click();
    assert.equal(await page.locator('.table-scroll tbody tr').count(), 0);
    console.log('PASS: scrollable editable table still adds and deletes rows');
    await page.waitForTimeout(800);
    assert.deepEqual(errors, []);
    console.log('Screenshots: ' + output);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
