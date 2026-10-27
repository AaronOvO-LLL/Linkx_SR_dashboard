"""个人账号、私密项目的完整 HTTP 权限边界；不调用外部云服务。"""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import app as web
from core import accounts, db, migrations, paths, repo
from services import artifacts
from tests import test_input_modes


class UserManagementTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_input_modes.InputModeTests()
        self.fixture.setUp()
        self.legacy = self.fixture.project
        self.root = Path(self.fixture.tmp.name)
        self.patches = [patch.object(paths, 'OUTPUT_DIR', str(self.root / 'outputs')),
                        patch.object(paths, 'EXPORT_DIR', str(self.root / 'exports'))]
        for p in self.patches:
            p.start()
        self.owner = accounts.create_user('alice', '张三', 'dept1', 'alice-secret')
        self.other = accounts.create_user('bob', '李四', 'dept1', 'bob-secret')
        self.cross = accounts.create_user('carol', '王五', 'dept2', 'carol-secret')
        self.admin = accounts.create_user('admin', '管理员', 'dept2', 'admin-secret', 'admin')
        self.client = self.login('alice', 'alice-secret')
        response = self.post(self.client, '/projects/create', data={
            'name': '张三私密项目', 'product_type': 'asset_butler',
            'creator_user_id': self.other['id'], 'creator_name': '伪造姓名'})
        self.assertEqual(response.status_code, 302)
        self.project = db.query_one('SELECT * FROM projects WHERE name=?', ('张三私密项目',))
        self.pid = self.project['id']
        self.pp = repo.list_products(self.pid)[0]
        self.purl, self.ppurl = '/projects/' + self.pid, '/p/' + self.pp['id']

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.fixture.tearDown()

    def login(self, username, password, client=None):
        client = client or web.app.test_client()
        client.get('/login')
        response = self.post(client, '/login', data={'username': username, 'password': password})
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        return client

    @staticmethod
    def post(client, url, **kwargs):
        with client.session_transaction() as session:
            token = session.get('csrf_token', '')
        return client.post(url, headers={'X-CSRF-Token': token}, **kwargs)

    def fill_and_generate(self):
        for key, value in {
            'submitter_contact': '张三 13800000000', 'asset_customer_name': '私密客户',
            'asset_customer_city': '杭州', 'asset_types': ['产业园区'],
            'tenant_scale': '200-500户', 'asset_pain_points': ['运营重复性劳动多'],
            'asset_deployment': 'SaaS',
        }.items():
            self.assertEqual(self.post(self.client, self.ppurl + '/field', json={
                'key': key, 'value': value}).status_code, 200)
        self.assertEqual(self.post(self.client, self.ppurl + '/generate', data={
            'artifacts': 'survey_result'}).status_code, 302)
        run = repo.get_artifact_run(self.pp['id'], 'survey_result')
        self.assertEqual(run['status'], 'success', run['error'])
        artifacts.package_artifacts(self.pp)
        file = next(f for f in json.loads(run['files_json']) if f['format'] == 'html')
        return self.ppurl + '/file/survey_result/' + Path(file['path']).name

    def audio(self):
        file = self.root / 'private.wav'
        file.write_bytes(b'RIFF-private-audio')
        job = repo.create_audio_job(self.pid, '私密录音.wav', str(file), file.stat().st_size)
        repo.update_audio_job(job['id'], status='awaiting_preview', corrected_transcript='私密转写文字')
        return self.purl + '/audio/' + job['id']

    def test_creation_cannot_forge_identity_and_edit_cannot_change_visibility(self):
        self.assertEqual(self.project['creator_user_id'], self.owner['id'])
        self.assertEqual(self.project['creator_name'], '张三')
        self.assertEqual(self.project['visibility'], 'private')
        for url in ('/projects', self.purl):
            html = self.client.get(url).get_data(as_text=True)
            self.assertIn('张三（alice）', html)
            self.assertIn('私密', html)
        self.post(self.client, self.purl + '/update', data={'name': '新名称',
                  'creator_user_id': self.other['id'], 'visibility': 'department', 'owner': '业务负责人'})
        updated = repo.get_project(self.pid)
        self.assertEqual(updated['creator_user_id'], self.owner['id'])
        self.assertEqual(updated['visibility'], 'private')
        self.assertEqual(updated['owner'], '业务负责人')

    def test_private_read_and_write_matrix_denies_peers_and_legacy_before_work(self):
        audio = self.audio()
        file_url = self.fill_and_generate()
        reads = [self.purl, audio, audio + '/status', audio + '/file', file_url,
                 file_url + '?mode=download', self.ppurl + '/package/download',
                 self.ppurl + '/survey.xlsx']
        reads += [self.ppurl + '/' + route for route in ('survey', 'review', 'preview', 'result')]
        writes = [self.purl + '/' + route for route in
                  ('update', 'archive', 'delete', 'restore', 'copy', 'products/add', 'audio')]
        writes += [audio + '/retry', audio + '/approve']
        writes += [self.ppurl + '/' + route for route in
                   ('survey', 'import-transcript', 'extract', 'field', 'shared-field/resolve',
                    'generate', 'regenerate', 'package')]
        peers = [self.login('bob', 'bob-secret'), self.login('carol', 'carol-secret'), self.fixture.client]
        with patch.object(web.audio_service, 'start_job') as start, patch.object(web, 'send_file') as send:
            for client in peers:
                for url in reads:
                    with self.subTest(url=url):
                        self.assertEqual(client.get(url).status_code, 403)
                for url in writes:
                    with self.subTest(url=url):
                        self.assertEqual(self.post(client, url, data={}).status_code, 403)
            start.assert_not_called()
            send.assert_not_called()
        self.assertEqual(repo.get_project(self.pid)['name'], '张三私密项目')

    def test_creator_and_cross_department_admin_can_read_real_audio_files_zip_and_ranges(self):
        audio, file_url = self.audio(), self.fill_and_generate()
        admin = self.login('admin', 'admin-secret')
        for client in (self.client, admin):
            for url in (self.purl, self.ppurl + '/review', audio, audio + '/status',
                        audio + '/file', file_url, self.ppurl + '/package/download',
                        self.ppurl + '/survey.xlsx'):
                with self.subTest(url=url), client.get(url) as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
            with client.get(audio + '/file', headers={'Range': 'bytes=0-3'}) as response:
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.data, b'RIFF')
        self.assertIn('张三私密项目', admin.get('/projects').get_data(as_text=True))

    def test_anonymous_and_logged_out_cannot_read_private_resources(self):
        audio, file_url = self.audio(), self.fill_and_generate()
        self.post(self.client, '/logout')
        for client in (self.client, web.app.test_client()):
            for url in (self.purl, self.ppurl + '/review', audio + '/file', file_url,
                        self.ppurl + '/package/download', self.ppurl + '/survey.xlsx'):
                response = client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.location.endswith('/login'))
            self.assertEqual(client.get(audio + '/status').status_code, 401)

    def test_lists_search_archive_and_recycle_do_not_leak_private_metadata(self):
        bob = self.login('bob', 'bob-secret')
        for archived, deleted in ((0, 0), (1, 0), (0, 1), (1, 1)):
            db.execute('UPDATE projects SET archived=?, deleted=? WHERE id=?', (archived, deleted, self.pid))
            for view in ('active', 'archived', 'deleted'):
                html = bob.get('/projects?view=%s&q=张三私密项目' % view).get_data(as_text=True)
                self.assertNotIn(self.pid, html)
                self.assertNotIn('张三（alice）', html)
            view = 'deleted' if deleted else 'archived' if archived else 'active'
            self.assertIn(self.pid, self.client.get('/projects?view=' + view).get_data(as_text=True))

    def test_copy_of_private_and_legacy_is_private_and_owned_by_actor(self):
        admin = self.login('admin', 'admin-secret')
        for source in (self.pid, self.legacy['id']):
            response = self.post(admin, '/projects/' + source + '/copy')
            new = repo.get_project(response.location.rsplit('/', 1)[-1])
            self.assertEqual(new['creator_user_id'], self.admin['id'])
            self.assertEqual(new['dept_key'], 'dept2')
            self.assertEqual(new['visibility'], 'private')
            self.assertEqual(new['copied_from'], source)
            self.assertEqual(self.client.get(response.location).status_code, 403)

    def test_legacy_shared_access_and_department_login_remain_available(self):
        url = '/projects/' + self.legacy['id']
        self.assertIn('历史项目（未记录个人创建者）', self.client.get(url).get_data(as_text=True))
        self.assertEqual(self.fixture.client.get(url).status_code, 200)
        self.assertEqual(self.login('bob', 'bob-secret').get(url).status_code, 200)
        self.assertEqual(self.login('carol', 'carol-secret').get(url).status_code, 403)
        # 真实分部入口在独立凭据文件中验证，不更改本机共享口令。
        with (patch.object(web.departments, 'RUNTIME_PATH', str(self.root / 'departments.json')),
              patch.object(web.departments, '_cache', None)):
            web.departments.ensure_initialized()
            legacy_client = web.app.test_client()
            response = legacy_client.post('/login', data={'dept_key': 'dept1', 'password': '000000'})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(legacy_client.get(url).status_code, 200)
            self.assertEqual(legacy_client.get(self.purl).status_code, 403)
            self.assertEqual(legacy_client.get('/admin/users').status_code, 403)

    def test_cross_project_audio_id_cannot_be_borrowed(self):
        audio = self.audio()
        other = repo.create_project('dept1', {'name': '另一个本人项目'}, creator=self.owner)
        borrowed = '/projects/' + other['id'] + '/audio/' + audio.rsplit('/', 1)[-1]
        for suffix in ('', '/status', '/file'):
            self.assertEqual(self.client.get(borrowed + suffix).status_code, 404)

    def test_admin_can_create_edit_disable_enable_and_reset_users(self):
        admin = self.login('admin', 'admin-secret')
        self.assertEqual(admin.get('/admin/users').status_code, 200)
        self.assertEqual(self.client.get('/admin/users').status_code, 403)
        self.assertEqual(self.post(self.client, '/admin/users', data={}).status_code, 403)
        response = self.post(admin, '/admin/users', data={'username': 'new.user', 'display_name': '新同事',
                     'dept_key': 'dept1', 'role': 'user', 'password': 'new-user-secret'})
        self.assertEqual(response.status_code, 302)
        user = db.query_one('SELECT * FROM users WHERE username=?', ('new.user',))
        self.assertNotEqual(user['password_hash'], 'new-user-secret')
        self.assertNotIn(user['password_hash'], admin.get('/admin/users').get_data(as_text=True))
        new_client = self.login('new.user', 'new-user-secret')
        update_url = '/admin/users/' + user['id'] + '/update'
        info = {'display_name': '改名同事', 'dept_key': 'dept2', 'role': 'user', 'active': '0'}
        self.post(admin, update_url, data=info)
        self.assertEqual(new_client.get('/projects').status_code, 302)
        info['active'] = '1'
        self.post(admin, update_url, data=info)
        new_client = self.login('new.user', 'new-user-secret')
        self.post(admin, '/admin/users/' + user['id'] + '/password', data={'password': 'reset-secret'})
        self.assertEqual(new_client.get('/projects').status_code, 302)
        self.login('new.user', 'reset-secret')
        events = db.query('SELECT action FROM account_events WHERE user_id=?', (user['id'],))
        self.assertEqual([e['action'] for e in events], ['create', 'update', 'update', 'password_reset'])

    def test_department_admin_scope_counts_and_role_changes(self):
        manager = accounts.create_user('manager', '分部负责人', 'dept1', 'manager-secret', 'dept_admin')
        client = self.login('manager', 'manager-secret')
        html = client.get('/admin/users').get_data(as_text=True)
        self.assertIn('已注册用户 <strong>3</strong>', html)
        self.assertIn(self.owner['created_at'], html)
        self.assertNotIn('data-user="carol"', html)
        self.assertNotIn('data-user="admin"', html)
        self.assertNotIn('href="/admin/users"', self.client.get('/projects').get_data(as_text=True))
        rows = {u['username']: u for u in accounts.managed_users(manager)}
        self.assertEqual(rows['alice']['project_count'], 1)
        self.assertEqual(rows['bob']['project_count'], 0)
        self.assertEqual(client.get(self.purl).status_code, 200)
        self.assertIn(self.pid, client.get('/projects').get_data(as_text=True))
        cross_project = repo.create_project('dept2', {'name': '跨分部私密'}, creator=self.cross)
        self.assertEqual(client.get('/projects/' + cross_project['id']).status_code, 403)
        self.assertNotIn(cross_project['id'], client.get('/projects').get_data(as_text=True))
        info = {'display_name': '张三', 'dept_key': 'dept1', 'role': 'user', 'active': '1'}
        for uid, change in ((self.owner['id'], {'role': 'admin'}),
                            (self.owner['id'], {'role': 'dept_admin'}),
                            (self.owner['id'], {'dept_key': 'dept2'}),
                            (self.cross['id'], {}), (manager['id'], {}), (self.admin['id'], {})):
            self.assertEqual(self.post(client, '/admin/users/' + uid + '/update',
                                       data={**info, **change}).status_code, 403)
        for uid in (manager['id'], self.cross['id'], self.admin['id']):
            self.assertEqual(self.post(client, '/admin/users/' + uid + '/password',
                                       data={'password': 'new-password'}).status_code, 403)
        self.assertEqual(self.post(client, '/admin/users', data={
            'username': 'evil', 'display_name': '越权', 'dept_key': 'dept1',
            'role': 'admin', 'password': 'evil-secret'}).status_code, 403)
        self.assertEqual(self.post(client, '/admin/users/' + self.other['id'] + '/update',
                                   data={**info, 'active': '0'}).status_code, 302)
        self.assertFalse(accounts.get_user(self.other['id'])['active'])
        admin = self.login('admin', 'admin-secret')
        self.assertIn('data-user="carol"', admin.get('/admin/users').get_data(as_text=True))
        self.assertEqual(self.post(admin, '/admin/users/' + self.owner['id'] + '/update',
                                   data={**info, 'role': 'dept_admin'}).status_code, 302)
        self.assertEqual(self.client.get('/projects').status_code, 302)
        promoted = self.login('alice', 'alice-secret')
        self.assertEqual(promoted.get('/admin/users').status_code, 200)

    def test_password_change_revokes_other_sessions_and_preserves_current(self):
        other_session = self.login('alice', 'alice-secret')
        self.post(self.client, '/password', data={'old_password': 'alice-secret',
                  'new_password': 'new-alice-secret', 'confirm_password': 'new-alice-secret'})
        self.assertEqual(other_session.get(self.purl).status_code, 302)
        self.assertEqual(self.client.get(self.purl).status_code, 200)
        self.login('alice', 'new-alice-secret')

    def test_csrf_is_required_for_personal_login_forms_json_and_admin(self):
        anonymous = web.app.test_client()
        self.assertEqual(anonymous.post('/login', data={'username': 'alice', 'password': 'alice-secret'}).status_code, 403)
        for url, kwargs in [('/projects/create', {'data': {'name': '伪造项目'}}),
                            (self.ppurl + '/field', {'json': {'key': 'asset_customer_name', 'value': '攻击'}})]:
            self.assertEqual(self.client.post(url, **kwargs).status_code, 403)
        admin = self.login('admin', 'admin-secret')
        self.assertEqual(admin.post('/admin/users', data={}).status_code, 403)
        self.assertEqual(admin.post('/admin/users/' + self.owner['id'] + '/password', data={'password': 'evil-password'}).status_code, 403)
        self.assertEqual(self.client.get('/logout').status_code, 405)
        self.assertEqual(self.client.post('/logout').status_code, 403)
        self.assertEqual(self.client.post('/projects/create', headers={'X-CSRF-Token': '非法令牌'},
                                         data={'name': '攻击'}).status_code, 403)

    def test_last_admin_duplicate_names_validation_and_identity_snapshots(self):
        for role, active in (('user', True), ('admin', False)):
            with self.assertRaisesRegex(ValueError, '最后一位'):
                accounts.update_user(self.admin['id'], '管理员', 'dept2', role, active, self.admin['id'])
        with self.assertRaisesRegex(ValueError, '已存在'):
            accounts.create_user('ALICE', '其他人', 'dept1', 'another-secret')
        for username, dept, password in (('bad name', 'dept1', 'long-password'),
                                         ('okay', 'missing', 'long-password'), ('okay', 'dept1', 'short')):
            with self.assertRaises(ValueError):
                accounts.create_user(username, '名字', dept, password)
        accounts.update_user(self.owner['id'], '张三新姓名', 'dept2', 'user', True, self.admin['id'])
        client = self.login('alice', 'alice-secret')
        self.assertIn('张三（alice）', client.get(self.purl).get_data(as_text=True))
        self.assertIn(self.pid, client.get('/projects').get_data(as_text=True))

    def test_disabled_or_changed_role_is_rechecked_without_trusting_session_claims(self):
        with self.client.session_transaction() as session:
            session['role'] = 'admin'
            session['dept_key'] = 'dept2'
        self.assertEqual(self.client.get('/admin/users').status_code, 403)
        self.assertEqual(self.client.get(self.purl).status_code, 200)
        accounts.update_user(self.owner['id'], '张三', 'dept1', 'user', False, self.admin['id'])
        self.assertEqual(self.client.get(self.purl).status_code, 302)
        self.assertEqual(self.client.get('/projects/' + self.legacy['id']).status_code, 302)

    def test_login_failures_are_limited_and_success_clears_counter(self):
        for _ in range(5):
            with self.assertRaisesRegex(ValueError, '不正确'):
                accounts.authenticate('alice', 'wrong-password')
        with self.assertRaisesRegex(ValueError, '次数过多'):
            accounts.authenticate('alice', 'alice-secret')
        with patch.object(accounts.time, 'time', return_value=accounts.time.time() + 301):
            self.assertEqual(accounts.authenticate('ALICE', 'alice-secret')['id'], self.owner['id'])
        self.assertIsNone(db.query_one('SELECT * FROM login_attempts WHERE username=?', ('alice',)))

    def test_department_choice_shares_pages_audio_and_files_within_department(self):
        response = self.post(self.client, '/projects/create', data={
            'name': '个人创建的分部共享项目', 'product_type': 'asset_butler', 'visibility': 'department',
            'creator_user_id': self.other['id']})
        self.project = repo.get_project(response.location.rsplit('/', 1)[-1])
        self.assertEqual(self.project['creator_user_id'], self.owner['id'])
        self.assertEqual(self.project['visibility'], 'department')
        self.pid = self.project['id']
        self.pp = repo.list_products(self.pid)[0]
        self.purl, self.ppurl = '/projects/' + self.pid, '/p/' + self.pp['id']
        audio, file_url = self.audio(), self.fill_and_generate()
        readers = [(self.client, 200), (self.login('bob', 'bob-secret'), 200),
                   (self.fixture.client, 200), (self.login('carol', 'carol-secret'), 403),
                   (self.login('admin', 'admin-secret'), 200)]
        for client, status in readers:
            for url in (self.purl, self.ppurl + '/review', audio + '/file', file_url,
                        self.ppurl + '/package/download'):
                with self.subTest(url=url, status=status), client.get(url) as result:
                    self.assertEqual(result.status_code, status)
            self.assertEqual(self.pid in client.get('/projects').get_data(as_text=True), status == 200)
        self.assertIn('张三（alice）', self.client.get(self.purl).get_data(as_text=True))

    def test_visibility_choices_validate_and_legacy_cannot_create_private(self):
        before = db.query_one('SELECT COUNT(*) AS n FROM projects')['n']
        for client, visibility in ((self.client, 'public'), (self.client, ''),
                                   (self.fixture.client, 'private')):
            self.assertEqual(self.post(client, '/projects/create', data={
                'name': '非法范围', 'visibility': visibility}).status_code, 400)
        self.assertEqual(db.query_one('SELECT COUNT(*) AS n FROM projects')['n'], before)
        with self.assertRaises(ValueError):
            repo.create_project('dept1', {'name': '无身份私密'}, visibility='private')
        html = self.client.get('/projects').get_data(as_text=True)
        self.assertIn('name="visibility"', html)
        self.assertIn('value="private"', html)
        self.assertIn('value="department"', html)


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_input_modes.InputModeTests()
        self.fixture.setUp()
        self.client = web.app.test_client()
        self.client.get('/register')

    def tearDown(self):
        self.fixture.tearDown()

    def post(self, url, **kwargs):
        return UserManagementTests.post(self.client, url, **kwargs)

    def test_three_fields_register_default_password_login_and_change_password(self):
        data = {'display_name': '自助注册同事', 'phone': '13812345678', 'dept_key': 'dept2',
                'role': 'admin', 'active': '0', 'username': 'forged', 'password': 'attacker-password'}
        response = self.post('/register', data=data, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('onewo123', html)
        self.assertIn('修改密码', html)
        user = accounts.list_users()[0]
        self.assertEqual((user['username'], user['display_name'], user['dept_key'], user['role'], user['active']),
                         ('13812345678', '自助注册同事', 'dept2', 'user', 1))
        self.assertTrue(accounts.verify_password(user['id'], 'onewo123'))
        self.assertEqual(self.post('/login', data={'username': '13812345678', 'password': 'onewo123'}).status_code, 302)
        self.assertEqual(self.client.get('/projects').status_code, 200)
        self.assertEqual(self.client.get('/admin/users').status_code, 403)
        self.assertEqual(self.post('/password', data={'old_password': 'onewo123',
            'new_password': 'personal-password', 'confirm_password': 'personal-password'}).status_code, 302)
        self.assertFalse(accounts.verify_password(user['id'], 'onewo123'))
        self.assertTrue(accounts.verify_password(user['id'], 'personal-password'))

    def test_validation_duplicate_phone_does_not_overwrite_existing_user(self):
        valid = {'display_name': '注册用户', 'phone': '13912345678', 'dept_key': 'dept1'}
        for change in ({'display_name': ''}, {'phone': ''}, {'phone': '12345'},
                       {'phone': '138１２３４５６７８'}, {'dept_key': ''}, {'dept_key': 'missing'}):
            response = self.post('/register', data={**valid, **change})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(accounts.list_users(), [])
        self.assertEqual(self.post('/register', data=valid).status_code, 302)
        original = accounts.list_users()[0]
        response = self.post('/register', data={**valid, 'display_name': '冒名', 'dept_key': 'dept2'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('该手机号已注册', response.get_data(as_text=True))
        self.assertEqual(accounts.list_users(), [original])

    def test_registration_entry_csrf_and_form_fields(self):
        self.assertIn('href="/register"', self.client.get('/login').get_data(as_text=True))
        html = self.client.get('/register').get_data(as_text=True)
        for field in ('display_name', 'phone', 'dept_key'):
            self.assertIn('name="%s"' % field, html)
        self.assertNotIn('name="password"', html)
        self.assertEqual(self.client.post('/register', data={
            'display_name': '伪造注册', 'phone': '13712345678', 'dept_key': 'dept1'}).status_code, 403)
        self.assertEqual(accounts.list_users(), [])


class AccountMigrationTests(unittest.TestCase):
    def test_v5_role_upgrade_preserves_accounts_credentials_and_project_links(self):
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.executescript(db.SCHEMA)
            with patch.object(migrations, 'MIGRATIONS', migrations.MIGRATIONS[:5]):
                migrations.apply_migrations(conn)
            conn.execute('''INSERT INTO users VALUES
                ('old-user', 'old-user', '旧用户', 'dept1', 'admin', 1, 'saved-hash', 7, 'old', 'old')''')
            conn.execute('''INSERT INTO projects
                (id, dept_key, category, name, created_at, updated_at, creator_user_id)
                VALUES ('owned', 'dept1', '内部项目', '旧项目', 'old', 'old', 'old-user')''')
            conn.commit()
            before = conn.execute('SELECT * FROM users').fetchall()
            migrations.apply_migrations(conn)
            self.assertEqual(conn.execute('SELECT * FROM users').fetchall(), before)
            self.assertEqual(conn.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            conn.execute("UPDATE users SET role='dept_admin' WHERE id='old-user'")
            conn.commit()
            migrations.apply_migrations(conn)
            self.assertEqual(conn.execute('SELECT creator_user_id FROM projects').fetchone()[0], 'old-user')
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_v4_upgrade_preserves_rows_backs_up_wal_and_is_idempotent(self):
        original = getattr(db._local, 'conn', None)
        db._local.conn = None
        with tempfile.TemporaryDirectory() as directory:
            target = str(Path(directory) / 'old.db')
            conn = sqlite3.connect(target)
            conn.row_factory = sqlite3.Row
            conn.executescript(db.SCHEMA)
            with patch.object(migrations, 'MIGRATIONS', migrations.MIGRATIONS[:4]):
                migrations.apply_migrations(conn)
            conn.execute('''INSERT INTO projects (id, dept_key, category, name, created_at, updated_at)
                VALUES ('old', 'dept1', '外部项目', '旧项目', '旧时间', '旧时间')''')
            conn.execute('''INSERT INTO project_products
                (id, project_id, product_type, product_version, template_version, created_at, updated_at)
                VALUES ('pp_old', 'old', 'asset_butler', 'v1', 'v1', '旧时间', '旧时间')''')
            conn.execute("INSERT INTO project_transcripts VALUES ('pt_old', 'old', '', '旧转写稿', 4, '旧时间')")
            conn.commit()
            before = {table: [dict(r) for r in conn.execute('SELECT * FROM ' + table)]
                      for table in ('projects', 'project_products', 'project_transcripts')}
            db._local.conn = conn
            try:
                with patch.object(db, 'db_path', return_value=target):
                    db.init_db()
                    backup = Path(target + '.pre-users-v5.bak')
                    self.assertTrue(backup.exists())
                    backup_bytes = backup.read_bytes()
                    with closing(sqlite3.connect(backup)) as saved:
                        self.assertEqual(saved.execute('SELECT name FROM projects').fetchone()[0], '旧项目')
                        self.assertNotIn('creator_user_id', [r[1] for r in saved.execute('PRAGMA table_info(projects)')])
                    for table, rows in before.items():
                        current = [dict(r) for r in conn.execute('SELECT * FROM ' + table)]
                        self.assertEqual([{k: row[k] for k in rows[0]} for row in current], rows)
                    project = repo.get_project('old')
                    self.assertEqual(project['visibility'], 'department')
                    self.assertIsNone(project['creator_user_id'])
                    db.init_db()
                    self.assertEqual(backup.read_bytes(), backup_bytes)
                    self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM schema_migrations WHERE version=5').fetchone()[0], 1)
            finally:
                conn.close()
                db._local.conn = original


if __name__ == '__main__':
    unittest.main()
