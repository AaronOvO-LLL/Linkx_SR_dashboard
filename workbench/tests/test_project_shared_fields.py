"""Project-wide answers, legacy conflict resolution and artifact freshness."""
import json
import sqlite3
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path
from unittest.mock import patch

from core import db, generate, paths, repo
from core.config import artifact_map, field_map
from core.migrations import apply_migrations
from core.validate import validate
from services import artifacts, survey
from tests import test_input_modes


class ProjectSharedFieldsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_input_modes.InputModeTests()
        self.fixture.setUp()
        self.client, self.project = self.fixture.client, self.fixture.project
        self.safety = repo.ensure_product(self.project['id'], 'safety_butler')
        self.fire = repo.ensure_product(self.project['id'], 'fire_butler')
        root = Path(self.fixture.tmp.name)
        self.patches = [patch.object(paths, 'OUTPUT_DIR', str(root / 'outputs')),
                        patch.object(paths, 'EXPORT_DIR', str(root / 'exports'))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.fixture.tearDown()

    def save(self, pp, key, value):
        response = self.client.post('/p/%s/field' % pp['id'], json={'key': key, 'value': value})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json

    def value(self, pp, key):
        return json.loads(repo.field_values(pp['id'])[key]['value_json'])

    def fill_required(self, pp, private_only=False):
        for key, field in field_map(pp['product_type']).items():
            if not field.get('required'):
                continue
            if private_only and field.get('value_scope') == 'project':
                continue
            value = (4 if field['type'] == 'number' else
                     field['options'][0]['value'] if field['type'] == 'select' else
                     [field['options'][0]['value']] if field['type'] == 'multiselect' else '现场确认')
            if field['type'] == 'table':
                value = [{c['key']: (4 if c['type'] == 'number' else c['options'][0]['value']
                                     if c['type'] == 'select' else '现场确认') for c in field['columns']}]
            self.save(pp, key, value)

    def legacy(self, pp, key, value, protected=1):
        db.execute('''INSERT INTO survey_field_values
            (id, project_product_id, field_key, value_json, status, protected, updated_by,
             template_version, updated_at, source_quote) VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (repo.new_id('old'), pp['id'], key, json.dumps(value, ensure_ascii=False),
             'confirmed' if protected else 'extracted', protected,
             'human' if protected else 'ai', 'legacy', '2026-10-01 10:00:00', '历史现场依据'))

    def migrate_legacy(self):
        db.execute('DELETE FROM schema_migrations WHERE version=4')
        db.init_db()

    def test_shared_answers_reach_existing_and_later_products_and_their_context(self):
        self.save(self.safety, 'project_name', '共享产业园')
        self.save(self.safety, 'property_company', '同一物业公司')
        self.save(self.safety, 'project_source', '渠道项目')
        self.save(self.safety, 'business_desc', ['园区'])
        for product in ('safety_butler', 'fire_butler', 'device_butler', 'energy_butler'):
            pp = repo.ensure_product(self.project['id'], product)
            self.assertEqual(self.value(pp, 'property_company'), '同一物业公司')
            self.assertEqual(self.value(pp, 'project_source'), '渠道项目')
            self.assertEqual(repo.field_stats(pp['id'], product)['filled'], 4)
            self.assertEqual(generate.build_context(pp['id'], product)['data']['project_name'], '共享产业园')
            response = self.client.get('/p/%s/review' % pp['id'])
            self.assertEqual(response.status_code, 200)
            self.assertIn('共享产业园', response.get_data(as_text=True))
            self.assertIn('项目共享', response.get_data(as_text=True))
        asset = repo.ensure_product(self.project['id'], 'asset_butler')
        self.assertEqual(self.value(asset, 'project_name'), '共享产业园')
        self.assertNotIn('property_company', repo.field_values(asset['id']))
        self.save(self.fire, 'property_company', '物业新名称')
        self.assertEqual(self.value(self.safety, 'property_company'), '物业新名称')
        self.assertEqual(len(db.query('SELECT * FROM project_field_values WHERE field_key=?', ('project_name',))), 1)
        self.assertEqual(db.query('SELECT * FROM survey_field_values WHERE field_key=?', ('project_name',)), [])

    def test_project_and_department_isolation_and_product_specific_answers(self):
        self.save(self.safety, 'project_name', '甲项目')
        self.save(self.safety, 'drawings', field_map('safety_butler')['drawings']['options'][0]['value'])
        self.assertNotIn('drawings', repo.field_values(self.fire['id']))
        for dept in ('dept1', 'dept2'):
            project = repo.create_project(dept, {'name': dept})
            other = repo.ensure_product(project['id'], 'safety_butler')
            self.assertEqual(repo.field_values(other['id']), {})
            if dept == 'dept2':
                self.assertEqual(self.client.post('/p/%s/field' % other['id'], json={
                    'key': 'project_name', 'value': '越界修改'}).status_code, 403)
                self.assertEqual(self.client.post('/p/%s/shared-field/resolve' % other['id'],
                                                 data={'key': 'project_name', 'candidate': 0}).status_code, 403)
        self.save(self.fire, 'project_name', '')
        self.assertEqual(self.value(self.safety, 'project_name'), '')

    def test_shared_value_and_product_requiredness_are_separate(self):
        self.save(self.safety, 'contract_years', '')
        safety_issues = validate(self.safety['id'], 'safety_butler')[1]
        fire_issues = validate(self.fire['id'], 'fire_butler')[1]
        self.assertFalse(any(i['field'] == 'contract_years' for i in safety_issues))
        self.assertTrue(any(i['field'] == 'contract_years' for i in fire_issues))
        self.save(self.fire, 'contract_years', '三年')
        self.assertEqual(self.value(self.safety, 'contract_years'), '三年')
        for product in ('safety_butler', 'fire_butler', 'device_butler', 'energy_butler'):
            self.assertEqual([o['value'] for o in field_map(product)['project_source']['options']],
                             ['内部项目', '外部项目', '渠道项目', '重点项目'])

    def test_ai_from_other_product_never_erases_existing_or_human_confirmed_shared_answer(self):
        self.save(self.safety, 'project_name', '人工项目名')
        repo.upsert_field_value(self.fire['id'], 'property_company', '"AI首次发现"',
                                'extracted', 'high', '', 'ai', 'test')
        for value in ('null', '"其他项目"'):
            repo.upsert_field_value(self.fire['id'], 'project_name', value,
                                    'extracted', 'high', '', 'ai', 'test')
            repo.upsert_field_value(self.safety['id'], 'property_company', value,
                                    'extracted', 'high', '', 'ai', 'test')
        self.assertEqual(self.value(self.fire, 'project_name'), '人工项目名')
        self.assertEqual(self.value(self.safety, 'property_company'), 'AI首次发现')
        text = (Path(paths.SAMPLES_DIR) / 'fire_butler/full.md').read_text(encoding='utf-8')
        survey.save_transcript(self.fire, self.project, text)
        survey.extract_latest_source(self.fire, self.project, provider='rule')
        self.assertEqual(self.value(self.fire, 'project_name'), '人工项目名')
        self.assertEqual(self.value(self.fire, 'property_company'), 'AI首次发现')

    def test_cross_product_manual_confirmation_during_extraction_is_preserved(self):
        text = (Path(paths.SAMPLES_DIR) / 'fire_butler/full.md').read_text(encoding='utf-8')
        survey.save_transcript(self.fire, self.project, text)
        original = survey.extract.run_extraction

        def during(*args, **kwargs):
            result = original(*args, **kwargs)
            self.save(self.safety, 'project_name', '分析期间人工填写')
            return result

        with patch.object(survey.extract, 'run_extraction', side_effect=during):
            survey.extract_latest_source(self.fire, self.project, provider='rule')
        self.assertEqual(self.value(self.fire, 'project_name'), '分析期间人工填写')

    def test_concurrent_shared_ai_and_manual_writes_always_preserve_manual_value(self):
        barrier = Barrier(2)

        def write(pp, actor, value):
            try:
                barrier.wait(timeout=10)
                repo.upsert_field_value(pp['id'], 'project_name', json.dumps(value),
                                        'confirmed' if actor == 'human' else 'extracted',
                                        None, '', actor, 'test', actor == 'human')
            finally:
                conn = getattr(db._local, 'conn', None)
                if conn:
                    conn.close()
                    db._local.conn = None

        with ThreadPoolExecutor(max_workers=2) as pool:
            ai = pool.submit(write, self.fire, 'ai', 'AI内容')
            human = pool.submit(write, self.safety, 'human', '人工内容')
            ai.result(timeout=35)
            human.result(timeout=35)
        self.assertEqual(self.value(self.fire, 'project_name'), '人工内容')

    def test_energy_can_generate_using_shared_answers_and_its_current_table_choices(self):
        self.fill_required(self.fire)
        energy = repo.ensure_product(self.project['id'], 'energy_butler')
        before = repo.project_fields_revision(self.project['id'])
        self.fill_required(energy, private_only=True)
        self.assertEqual(before, repo.project_fields_revision(self.project['id']))
        self.assertTrue(validate(energy['id'], 'energy_butler')[0])
        self.assertEqual(artifacts.generate_artifacts(energy, ['equipment_list'])[0]['status'], 'success')
        self.assertEqual(self.value(energy, 'project_name'), self.value(self.fire, 'project_name'))

    def test_real_v3_schema_upgrade_marks_historical_material_stale(self):
        repo.upsert_artifact_run(self.fire['id'], next(iter(artifact_map('fire_butler'))), 'success', files=[])
        self.legacy(self.fire, 'project_name', '旧版项目')
        for table in ('project_field_values', 'project_field_conflicts', 'project_field_history', 'project_field_revisions'):
            db.execute('DROP TABLE ' + table)
        db.execute('ALTER TABLE artifact_runs DROP COLUMN project_fields_revision')
        self.migrate_legacy()
        self.assertEqual(self.value(self.safety, 'project_name'), '旧版项目')
        self.assertEqual(repo.artifact_runs(self.fire['id'])[0]['status'], 'stale')
        self.assertEqual(db.query_one('PRAGMA integrity_check')['integrity_check'], 'ok')

    def test_legacy_equal_single_and_empty_answers_merge_without_losing_protection(self):
        self.legacy(self.safety, 'project_name', '一致项目', 0)
        self.legacy(self.fire, 'project_name', '一致项目', 1)
        self.legacy(self.fire, 'property_company', '唯一物业')
        self.legacy(self.safety, 'property_company', '')
        self.migrate_legacy()
        backup = Path(db.db_path() + '.pre-shared-v4.bak')
        self.assertTrue(backup.exists())
        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM survey_field_values').fetchone()[0], 4)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM schema_migrations WHERE version=4').fetchone()[0], 0)
        self.assertEqual(self.value(self.fire, 'project_name'), '一致项目')
        self.assertEqual(self.value(self.safety, 'property_company'), '唯一物业')
        self.assertEqual(repo.field_values(self.fire['id'])['project_name']['protected'], 1)
        self.assertEqual(repo.shared_field_conflicts(self.fire['id']), {})
        before = db.query('SELECT * FROM project_field_values ORDER BY field_key')
        apply_migrations(db.connect())
        self.assertEqual(before, db.query('SELECT * FROM project_field_values ORDER BY field_key'))
        self.assertEqual(len(db.query('SELECT * FROM survey_field_values')), 4)
        self.save(self.fire, 'project_name', '')
        self.assertEqual(self.value(self.safety, 'project_name'), '')  # 旧副本不能复活

    def test_conflicting_legacy_answers_are_visible_block_generation_and_resolve_with_audit(self):
        self.legacy(self.safety, 'property_company', '物业甲 <北区>')
        self.legacy(self.fire, 'property_company', '物业乙')
        self.migrate_legacy()
        self.assertIsNone(self.value(self.safety, 'property_company'))
        conflicts = repo.shared_field_conflicts(self.fire['id'])
        self.assertEqual(len(conflicts['property_company']), 2)
        self.assertTrue(any(i.get('kind') == 'shared_conflict' for i in validate(self.safety['id'], 'safety_butler')[1]))
        html = self.client.get('/p/%s/review' % self.fire['id']).get_data(as_text=True)
        self.assertIn('物业甲 &lt;北区&gt;', html)
        self.assertIn('采用这份答案并共享', html)
        self.assertIn('安全管家', html)
        self.assertIn('消防管家', html)
        blank = self.client.post('/p/%s/field' % self.fire['id'], json={'key': 'property_company', 'value': ''})
        self.assertEqual(blank.status_code, 400)
        repo.upsert_field_value(self.fire['id'], 'property_company', '"AI猜测"',
                                'extracted', 'high', '', 'ai', 'test')
        self.assertIsNone(self.value(self.fire, 'property_company'))
        chosen = conflicts['property_company'][0]['value_json']
        response = self.client.post('/p/%s/shared-field/resolve' % self.fire['id'],
                                    data={'key': 'property_company', 'candidate': 0})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(repo.field_values(self.safety['id'])['property_company']['value_json'], chosen)
        self.assertEqual(repo.shared_field_conflicts(self.fire['id']), {})
        audit = db.query_one('SELECT * FROM project_field_conflicts')
        self.assertIsNotNone(audit['resolved_at'])
        self.assertEqual(len(json.loads(audit['candidates_json'])), 2)
        self.assertEqual(len(db.query('SELECT * FROM survey_field_values')), 2)
        self.assertEqual(len(db.query('SELECT * FROM project_field_history')), 2)

    def test_manual_replacement_resolves_conflict_even_for_optional_field(self):
        self.legacy(self.safety, 'contract_years', '一年')
        self.legacy(self.fire, 'contract_years', '三年')
        self.migrate_legacy()
        self.assertTrue(any(i.get('kind') == 'shared_conflict' for i in validate(self.safety['id'], 'safety_butler')[1]))
        self.save(self.safety, 'contract_years', '重新确认五年')
        self.assertEqual(self.value(self.fire, 'contract_years'), '重新确认五年')
        self.assertEqual(repo.shared_field_conflicts(self.safety['id']), {})

    def test_shared_change_invalidates_all_validation_artifacts_exports_and_direct_downloads(self):
        self.fill_required(self.fire)
        self.fill_required(self.safety)
        key = next(iter(artifact_map('fire_butler')))
        self.assertEqual(artifacts.generate_artifacts(self.fire, [key])[0]['status'], 'success')
        archive, _, _ = artifacts.package_artifacts(self.fire)
        self.assertTrue(Path(archive).exists())
        self.assertIsNotNone(artifacts.latest_current_export(self.fire))
        repo.save_validation(self.fire['id'], True, [])
        repo.save_validation(self.safety['id'], True, [])
        run = repo.get_artifact_run(self.fire['id'], key)
        f = json.loads(run['files_json'])[0]
        self.save(self.safety, 'property_company', '更新后的物业')
        self.assertIsNone(repo.latest_validation(self.fire['id']))
        self.assertIsNone(repo.latest_validation(self.safety['id']))
        self.assertEqual(repo.get_artifact_run(self.fire['id'], key)['status'], 'stale')
        self.assertNotEqual(repo.progress_of(self.fire['id'], 'fire_butler')[0], 100)
        self.assertIsNone(artifacts.latest_current_export(self.fire))
        with self.assertRaisesRegex(ValueError, '重新生成'):
            artifacts.package_artifacts(self.fire, [key])
        self.assertEqual(self.client.get('/p/%s/file/%s/%s' % (self.fire['id'], key, f['name'])).status_code, 302)
        self.assertEqual(self.client.get('/p/%s/package/download' % self.fire['id']).status_code, 302)
        self.assertIn('需重新生成', self.client.get('/p/%s/result' % self.fire['id']).get_data(as_text=True))
        self.assertIn('需重新生成', self.client.get('/p/%s/preview' % self.fire['id']).get_data(as_text=True))
        self.assertTrue(Path(f['path']).exists())
        self.assertEqual(artifacts.regenerate_artifact(self.fire, key)['status'], 'success')
        self.assertEqual(repo.get_artifact_run(self.fire['id'], key)['status'], 'success')
        artifacts.package_artifacts(self.fire)
        self.assertIsNotNone(artifacts.latest_current_export(self.fire))

    def test_change_during_render_cannot_mark_old_output_current(self):
        self.fill_required(self.fire)
        key = next(iter(artifact_map('fire_butler')))
        render = generate.render_artifact

        def during(*args):
            files = render(*args)
            self.save(self.safety, 'project_name', '渲染时更新')
            return files

        with patch.object(generate, 'render_artifact', side_effect=during):
            artifacts.generate_artifacts(self.fire, [key])
        self.assertEqual(repo.get_artifact_run(self.fire['id'], key)['status'], 'stale')

    def test_delayed_validation_cannot_restore_old_pass_after_shared_change(self):
        revision = repo.project_fields_revision(self.project['id'])
        self.save(self.safety, 'project_name', '更改后的项目')
        repo.save_validation(self.fire['id'], True, [], shared_revision=revision)
        self.assertIsNone(repo.latest_validation(self.fire['id']))

    def test_copy_and_purge_preserve_project_boundaries_including_hidden_shared_fields(self):
        self.save(self.fire, 'project_name', '原项目')
        self.save(self.fire, 'service_type', field_map('fire_butler')['service_type']['options'][0]['value'])
        copied = repo.copy_project(self.project['id'])
        copied_fire = next(pp for pp in repo.list_products(copied['id']) if pp['product_type'] == 'fire_butler')
        self.assertEqual(self.value(copied_fire, 'project_name'), '原项目')
        self.assertEqual(self.value(copied_fire, 'service_type'), self.value(self.fire, 'service_type'))
        survey.save_field_value(copied_fire, copied, {'key': 'project_name', 'value': '副本项目'})
        self.assertEqual(self.value(self.fire, 'project_name'), '原项目')
        repo.purge_project(copied['id'])
        for table in ('project_field_values', 'project_field_conflicts', 'project_field_history', 'project_field_revisions'):
            self.assertEqual(db.query('SELECT * FROM %s WHERE project_id=?' % table, (copied['id'],)), [])
        self.assertEqual(self.value(self.fire, 'project_name'), '原项目')

    def test_copy_keeps_unresolved_conflicts_without_selecting_a_candidate(self):
        self.legacy(self.safety, 'contract_years', '一年')
        self.legacy(self.fire, 'contract_years', '三年')
        self.migrate_legacy()
        copied = repo.copy_project(self.project['id'])
        copied_safety = next(pp for pp in repo.list_products(copied['id']) if pp['product_type'] == 'safety_butler')
        self.assertEqual(len(repo.shared_field_conflicts(copied_safety['id'])['contract_years']), 2)
        self.assertIsNone(self.value(copied_safety, 'contract_years'))
        survey.save_field_value(copied_safety, copied, {'key': 'contract_years', 'value': '五年'})
        self.assertIsNone(self.value(self.safety, 'contract_years'))
        repo.purge_project(copied['id'])
        for table in ('project_field_values', 'project_field_conflicts', 'project_field_history', 'project_field_revisions'):
            self.assertEqual(db.query('SELECT * FROM %s WHERE project_id=?' % table, (copied['id'],)), [])
        self.assertIn('contract_years', repo.shared_field_conflicts(self.fire['id']))


if __name__ == '__main__':
    unittest.main()
