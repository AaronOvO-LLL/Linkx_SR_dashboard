"""资产调研从手填/提取到事实型结果下载的隔离回归。"""
import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import app as web
from core import db, paths, repo
from core.config import artifact_map, field_map
from core.extract import run_extraction
from core.validate import validate
from services import artifacts, survey


class AssetButlerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_conn = getattr(db._local, 'conn', None)
        db._local.conn = None
        self.patches = [
            patch.object(db, 'db_path', return_value=str(Path(self.tmp.name) / 'test.db')),
            patch.object(paths, 'OUTPUT_DIR', str(Path(self.tmp.name) / 'outputs')),
            patch.object(paths, 'EXPORT_DIR', str(Path(self.tmp.name) / 'exports')),
        ]
        for p in self.patches:
            p.start()
        self.client = web.app.test_client()
        with self.client.session_transaction() as session:
            session['dept_key'] = 'dept1'
        self.project = repo.create_project('dept1', {'name': '资产测试记录'})
        self.pp = repo.ensure_product(self.project['id'], 'asset_butler')
        self.fmap = field_map('asset_butler')

    def tearDown(self):
        if getattr(db._local, 'conn', None):
            db._local.conn.close()
        db._local.conn = self.original_conn
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def save(self, key, value):
        return self.client.post(f"/p/{self.pp['id']}/field", json={'key': key, 'value': value})

    def fill_required(self):
        values = {
            'submitter_contact': '陈岚 13800000000',
            'asset_customer_name': '云澜资产运营有限公司',
            'asset_customer_city': '杭州市',
            'asset_types': ['产业园区', '写字楼'],
            'tenant_scale': '200-500户',
            'asset_pain_points': ['运营重复性劳动多'],
            'asset_deployment': 'SaaS',
        }
        for key, value in values.items():
            self.assertEqual(self.save(key, value).status_code, 200)

    def test_source_contract_and_initial_time(self):
        self.assertEqual(len(self.fmap), 14)
        required = {k for k, f in self.fmap.items() if f.get('required')}
        self.assertEqual(required, {'submitter_contact', 'asset_customer_name',
            'asset_customer_city', 'asset_types', 'tenant_scale', 'asset_pain_points',
            'asset_deployment', 'submitted_at'})
        self.assertNotIn('camera_type', self.fmap)
        self.assertNotIn('land_area', self.fmap)
        self.assertEqual([len(self.fmap[k]['options']) for k in
            ('asset_types', 'asset_pain_points', 'asset_value_services')], [7, 9, 6])
        initial = repo.field_values(self.pp['id'])['submitted_at']
        stamp = datetime.strptime(json.loads(initial['value_json']), '%Y-%m-%d %H:%M:%S')
        expected = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        self.assertLess(abs((expected - stamp).total_seconds()), 5)
        self.assertEqual((initial['updated_by'], initial['protected']), ('system', 1))
        self.assertEqual(repo.ensure_product(self.project['id'], 'asset_butler')['id'], self.pp['id'])
        self.assertEqual(repo.field_values(self.pp['id'])['submitted_at']['value_json'], initial['value_json'])

    def test_manual_workflow_preview_generation_zip_and_isolation(self):
        self.assertIn('value="asset_butler"', self.client.get('/projects').get_data(as_text=True))
        for route in ('survey', 'review', 'preview'):
            self.assertEqual(self.client.get(f"/p/{self.pp['id']}/{route}").status_code, 200)
        self.fill_required()
        self.assertTrue(validate(self.pp['id'], 'asset_butler')[0])
        self.assertIsNone(repo.latest_source(self.pp['id']))
        preview = self.client.get(f"/p/{self.pp['id']}/preview").get_data(as_text=True)
        self.assertIn('产业园区、写字楼', preview)
        self.assertIn('客户调研结果', preview)
        self.assertIn('客户项目', preview)
        self.assertNotIn('项目名称、来源、业态、建设类型与规模', preview)
        self.assertNotIn('摄像头品牌', preview)
        self.assertEqual(set(artifact_map('asset_butler')), {'survey_result'})
        response = self.client.post(f"/p/{self.pp['id']}/generate", data={'artifacts': ['survey_result']})
        self.assertEqual(response.status_code, 302)
        run = repo.get_artifact_run(self.pp['id'], 'survey_result')
        self.assertEqual(run['status'], 'success', run['error'])
        files = json.loads(run['files_json'])
        self.assertEqual({f['format'] for f in files}, {'md', 'html'})
        content = Path(files[0]['path']).read_text(encoding='utf-8')
        self.assertIn('云澜资产运营有限公司', content)
        self.assertIn('（未填写）', content)
        self.assertNotIn('安全管家', content)
        self.assertEqual(self.client.get(f"/p/{self.pp['id']}/result").status_code, 200)
        archive, name, manifest = artifacts.package_artifacts(self.pp)
        self.assertIn('资产管家', name)
        self.assertEqual(manifest['product']['product_type'], 'asset_butler')
        with zipfile.ZipFile(archive) as z:
            self.assertIsNone(z.testzip())
            self.assertTrue(any(p.endswith('.html') for p in z.namelist()))
        with self.client.get(f"/p/{self.pp['id']}/package/download") as response:
            self.assertEqual(response.status_code, 200)
        safety = repo.ensure_product(self.project['id'], 'safety_butler')
        self.assertEqual(repo.field_values(safety['id']), {})
        self.save('asset_customer_city', '')
        self.assertFalse(validate(self.pp['id'], 'asset_butler')[0])
        with self.assertRaises(artifacts.ArtifactServiceError):
            artifacts.generate_artifacts(self.pp, ['survey_result'])

    def test_rule_extraction_and_protection(self):
        text = (Path(paths.SAMPLES_DIR) / 'asset_butler/full.md').read_text(encoding='utf-8')
        stamp = repo.field_values(self.pp['id'])['submitted_at']['value_json']
        self.save('asset_customer_city', '苏州市')
        repo.save_project_transcript(self.project['id'], '', text)
        survey.import_project_transcript(self.pp, self.project)
        survey.extract_latest_source(self.pp, self.project, provider='rule')
        values = repo.field_values(self.pp['id'])
        self.assertEqual(json.loads(values['asset_customer_name']['value_json']), '云澜资产运营有限公司')
        self.assertEqual(json.loads(values['operating_project_count']['value_json']), 3)
        self.assertEqual(json.loads(values['asset_deployment']['value_json']), 'SaaS')
        self.assertIn('产业园区', json.loads(values['asset_types']['value_json']))
        self.assertIn('软件对接', json.loads(values['asset_value_services']['value_json']))
        self.assertIn('2023年上线', json.loads(values['digitalization_status']['value_json']))
        self.assertIn('30万元', json.loads(values['digitalization_status']['value_json']))
        self.assertIn('运营重复性劳动多', json.loads(values['asset_pain_points']['value_json']))
        self.assertEqual(json.loads(values['tenant_scale']['value_json']), '200-500户')
        self.assertEqual(json.loads(values['asset_customer_city']['value_json']), '苏州市')
        self.assertEqual(values['submitted_at']['value_json'], stamp)
        self.assertTrue(validate(self.pp['id'], 'asset_butler')[0])

    def test_unknown_zero_and_boundary_are_preserved(self):
        for amount, expected in [('0', 0), ('3', 3), ('未知', None)]:
            text = '客户名称：云澜公司。运营项目数：%s。经营性资产业态：产业园区，用于长期对外租赁。' % amount
            result, _ = run_extraction(text, 'asset_butler', provider='rule')
            self.assertEqual(result['operating_project_count']['value'], expected)
        for number in (200, 500, 1000):
            text = '客户名称：云澜公司。在租客户数量：恰好%d户。经营性资产业态：产业园区。' % number
            result, _ = run_extraction(text, 'asset_butler', provider='rule')
            self.assertIn(str(number), result['tenant_scale']['value'])
            self.assertEqual(result['tenant_scale']['status'], 'pending_confirm')
        text = '客户名称：云澜公司。数字化现状：目前使用SaaS租赁系统，但新的部署方式尚未确定。经营性资产业态：产业园区。'
        result, _ = run_extraction(text, 'asset_butler', provider='rule')
        self.assertNotEqual(result['asset_deployment']['value'], 'SaaS')
        self.assertIsNone(result['submitted_at']['value'])
        result, _ = run_extraction('客户名称：云澜公司。客户痛点：经营数据不准、不及时，运营重复性劳动多；没有多系统数据孤岛。', 'asset_butler', provider='rule')
        self.assertIn('运营重复性劳动多', result['asset_pain_points']['value'])
        self.assertNotIn('多系统数据孤岛', result['asset_pain_points']['value'])

    def test_count_constraints_and_choice_shape(self):
        self.fill_required()
        for amount in (0, 3, '3'):
            self.save('operating_project_count', amount)
            self.assertTrue(validate(self.pp['id'], 'asset_butler')[0])
        for amount in (-1, 1.5, True, '三', 'abc3', float('inf')):
            self.save('operating_project_count', amount)
            self.assertFalse(validate(self.pp['id'], 'asset_butler')[0], amount)
        self.assertEqual(self.save('asset_types', '产业园区').status_code, 400)
        self.assertEqual(self.save('asset_deployment', ['SaaS']).status_code, 400)
        self.save('operating_project_count', '')
        self.save('asset_types', ['其他', '仓储物流资产'])
        self.save('tenant_scale', '项目尚未开业，满租预估200户')
        self.assertTrue(validate(self.pp['id'], 'asset_butler')[0])


if __name__ == '__main__':
    unittest.main()
