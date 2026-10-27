// Real Edge + Flask + SQLite; all accounts, project data and media are temporary.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const root = path.resolve(__dirname, '..');
const output = path.join(root, '.verify-temp');
fs.mkdirSync(output, { recursive: true });
const server = spawn(process.env.USERS_TEST_PYTHON || path.join(root, '.venv/Scripts/python.exe'), ['-u', '-c', `
import json
from werkzeug.serving import make_server
import app as web
from tests.test_user_management import UserManagementTests
case = UserManagementTests(); case.setUp()
audio = case.audio()
file_url = case.fill_and_generate()
web.app.secret_key = 'isolated-personal-accounts-browser-test'
server = make_server('127.0.0.1', 0, web.app, threaded=True)
print(json.dumps({'port': server.server_port, 'private': case.purl, 'audio': audio,
                  'file': file_url, 'zip': case.ppurl + '/package/download',
                  'legacy': '/projects/' + case.legacy['id']}), flush=True)
try: server.serve_forever()
finally: case.tearDown()
`], { cwd: root, env: { ...process.env, PYTHONUTF8: '1', TEMP: output, TMP: output }, stdio: ['ignore', 'pipe', 'pipe'] });
let stderr = '';
server.stderr.on('data', data => { stderr += data; });

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
    const errors = [];
    async function session(username, password) {
      const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
      const page = await context.newPage();
      page.on('pageerror', e => errors.push(e.message));
      await page.goto(origin + '/login');
      const form = page.locator('#personalLoginForm');
      await form.getByLabel('手机号 / 用户名', { exact: true }).fill(username);
      await form.getByLabel('密码', { exact: true }).fill(password);
      await Promise.all([page.waitForURL('**/projects'), form.getByRole('button').click()]);
      return { context, page };
    }
    const signupContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const signupPage = await signupContext.newPage();
    signupPage.on('pageerror', e => errors.push(e.message));
    await signupPage.goto(origin + '/login');
    await signupPage.getByRole('link', { name: '注册', exact: true }).click();
    const registration = signupPage.locator('#registerForm');
    await registration.getByLabel('姓名', { exact: true }).fill('自助注册用户');
    await registration.getByLabel('电话（手机号）', { exact: true }).fill('13812345678');
    await registration.getByLabel('分部', { exact: true }).selectOption('dept1');
    assert.ok((await signupPage.locator('main').innerText()).includes('onewo123'));
    assert.equal(await registration.locator('input[type="password"]').count(), 0);
    assert.ok(await signupPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await signupPage.screenshot({ path: path.join(output, 'registration-mobile.png') });
    await Promise.all([signupPage.waitForURL('**/login'), registration.getByRole('button', { name: '完成注册' }).click()]);
    assert.ok((await signupPage.locator('main').innerText()).includes('注册成功'));
    const registered = await session('13812345678', 'onewo123');
    const sharedForm = registered.page.locator('.project-create');
    assert.equal(await sharedForm.locator('[name="visibility"]').inputValue(), 'private');
    await sharedForm.locator('[name="name"]').fill('自助注册分部共享项目');
    await sharedForm.locator('[name="product_type"]').selectOption('asset_butler');
    await sharedForm.getByLabel('项目可见范围').selectOption('department');
    await registered.page.screenshot({ path: path.join(output, 'project-visibility-desktop.png') });
    await Promise.all([registered.page.waitForNavigation(), sharedForm.getByRole('button', { name: '新建项目' }).click()]);
    const sharedUrl = registered.page.url();
    assert.ok((await registered.page.locator('main').innerText()).includes('自助注册用户（13812345678）'));
    assert.ok((await registered.page.locator('main').innerText()).includes('分部共享：维持原分部权限'));
    console.log('PASS: registration with three fields, default password login, visibility choice and mobile layout');
    const admin = await session('admin', 'admin-secret');
    await admin.page.getByRole('link', { name: '用户管理' }).click();
    assert.ok((await admin.page.locator('main').innerText()).includes('注册时间'));
    assert.ok((await admin.page.locator('main').innerText()).includes('创建的项目数量'));
    assert.equal(await admin.page.locator('#createUserForm select[name="role"] option').count(), 3);
    const create = admin.page.locator('#createUserForm');
    await create.getByLabel('用户名', { exact: true }).fill('browser.user');
    await create.getByLabel('姓名', { exact: true }).fill('浏览器用户');
    await create.getByLabel('初始密码（8–128 位）', { exact: true }).fill('browser-user-secret');
    await Promise.all([admin.page.waitForNavigation(), create.getByRole('button', { name: '创建账号' }).click()]);
    assert.equal(await admin.page.locator('[data-user="browser.user"]').count(), 1);
    await admin.page.screenshot({ path: path.join(output, 'users-admin-desktop.png') });
    console.log('PASS: administrator creates an account through real forms and CSRF');

    const user = await session('browser.user', 'browser-user-secret');
    const projectForm = user.page.locator('.project-create');
    await projectForm.locator('[name="name"]').fill('浏览器个人私密项目');
    await projectForm.locator('[name="product_type"]').selectOption('asset_butler');
    await projectForm.getByRole('button', { name: '新建项目' }).click();
    await user.page.waitForURL('**/projects/prj_*');
    const projectUrl = user.page.url();
    assert.ok((await user.page.locator('main').innerText()).includes('创建者：浏览器用户（browser.user）'));
    await user.page.getByRole('link', { name: '直接手动填写', exact: true }).click();
    const input = user.page.locator('.fi[data-key="asset_customer_city"]');
    await input.fill('浏览器自动保存城市');
    const saved = user.page.waitForResponse(r => r.url().endsWith('/field'));
    await input.blur();
    assert.equal((await saved).status(), 200);
    await user.page.reload();
    assert.equal(await input.inputValue(), '浏览器自动保存城市');
    await user.page.setViewportSize({ width: 390, height: 844 });
    await user.page.reload();
    assert.ok(await user.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await user.page.screenshot({ path: path.join(output, 'users-private-mobile.png') });
    console.log('PASS: personal login, project creator, autosave and mobile layout');

    const alice = await session('alice', 'alice-secret');
    const bob = await session('bob', 'bob-secret');
    const carol = await session('carol', 'carol-secret');
    assert.equal((await bob.context.request.get(sharedUrl)).status(), 200);
    assert.equal((await admin.context.request.get(sharedUrl)).status(), 200);
    assert.equal((await carol.context.request.get(sharedUrl)).status(), 403);
    console.log('PASS: shared project follows department access; private projects remain isolated');
    for (const resource of [metadata.private, metadata.audio + '/file', metadata.file, metadata.zip]) {
      assert.equal((await alice.context.request.get(origin + resource)).status(), 200);
      assert.equal((await admin.context.request.get(origin + resource)).status(), 200);
      assert.equal((await bob.context.request.get(origin + resource)).status(), 403);
      assert.equal((await user.context.request.get(origin + resource)).status(), 403);
    }
    assert.equal((await bob.context.request.get(projectUrl)).status(), 403);
    assert.equal((await admin.context.request.get(projectUrl)).status(), 200);
    console.log('PASS: creator/admin can read real private files; peers receive 403');

    const account = admin.page.locator('[data-user="browser.user"]');
    await account.locator('[name="active"]').selectOption('0');
    await Promise.all([admin.page.waitForNavigation(), account.getByRole('button', { name: '保存账号' }).click()]);
    await user.page.goto(projectUrl);
    assert.ok(user.page.url().endsWith('/login'));
    await account.locator('[name="active"]').selectOption('1');
    await Promise.all([admin.page.waitForNavigation(), account.getByRole('button', { name: '保存账号' }).click()]);
    admin.page.on('dialog', dialog => dialog.accept());
    await account.locator('input[name="password"]').fill('browser-reset-secret');
    await Promise.all([admin.page.waitForNavigation(), account.getByRole('button', { name: '重置密码' }).click()]);
    const reset = await session('browser.user', 'browser-reset-secret');
    assert.equal((await reset.context.request.get(projectUrl)).status(), 200);
    await reset.page.getByRole('button', { name: '退出登录' }).click();
    await reset.page.waitForURL('**/login');
    assert.ok((await reset.context.request.get(projectUrl)).url().endsWith('/login'));
    console.log('PASS: disable/re-enable/reset password and logout revoke sessions');
    await admin.page.setViewportSize({ width: 390, height: 844 });
    await admin.page.reload();
    assert.ok(await admin.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await admin.page.screenshot({ path: path.join(output, 'users-admin-mobile.png') });
    assert.deepEqual(errors, []);
    console.log('PASS: account management mobile layout, no browser runtime errors');
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
})().catch(error => { console.error(error); console.error(stderr.slice(-7000)); process.exitCode = 1; });
