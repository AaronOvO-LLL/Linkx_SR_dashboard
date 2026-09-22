import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import app as web
from core import db, paths, repo
from core.config import artifact_map, field_map, product_config
from core.extract import ExtractionError, run_extraction
from core.validate import validate
from services import artifacts, survey


class FireButlerTests(unittest.TestCase):
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

    def tearDown(self):
        if getattr(db._local, 'conn', None):
            db._local.conn.close()
        db._local.conn = self.original_conn
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_complete_workflow_and_product_isolation(self):
        page = self.client.get('/projects').get_data(as_text=True)
        self.assertIn('value="fire_butler"', page)
        response = self.client.post('/projects/create', data={
            'name': '消防测试园区', 'product_type': 'fire_butler'}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('消防管家', response.get_data(as_text=True))
        project = repo.list_projects('dept1')[0]
        pp = repo.list_products(project['id'])[0]
        text = (Path(paths.SAMPLES_DIR) / 'fire_butler/full.md').read_text(encoding='utf-8')
        survey.save_transcript(pp, project, text)
        survey.extract_latest_source(pp, project, provider='rule')
        values = repo.field_values(pp['id'])
        self.assertEqual(json.loads(values['fire_renovation_cost']['value_json']), 50000)
        self.assertIn('4号', json.loads(values['fire_host_relationships']['value_json']))
        self.assertIn('4号', json.loads(values['fire_host_brands']['value_json']))
        for key, f in field_map('fire_butler').items():
            if key == 'fire_renovation_cost':
                continue
            value = 4 if f['type'] == 'number' else (
                f['options'][0]['value'] if f['type'] == 'select' else (
                [f['options'][0]['value']] if f['type'] == 'multiselect' else '现场已确认'))
            r = self.client.post(f"/p/{pp['id']}/field", json={'key': key, 'value': value})
            self.assertEqual(r.status_code, 200)
        self.assertTrue(validate(pp['id'], 'fire_butler')[0])
        for page in ['review', 'preview']:
            r = self.client.get(f"/p/{pp['id']}/{page}")
            self.assertEqual(r.status_code, 200)
            self.assertNotIn('摄像头品牌', r.get_data(as_text=True))
        results = artifacts.generate_artifacts(pp, list(artifact_map('fire_butler')))
        self.assertTrue(all(r['status'] == 'success' for r in results), results)
        delivery = next(r for r in results if r['key'] == 'delivery')
        content = Path(delivery['files'][0]['path']).read_text(encoding='utf-8')
        self.assertIn('50000', content)
        self.assertNotIn('安全管家', content)
        archive, name, manifest = artifacts.package_artifacts(pp)
        self.assertIn('消防管家', name)
        with zipfile.ZipFile(archive) as z:
            self.assertEqual(len(manifest['artifacts']), 4)
            self.assertTrue(z.testzip() is None)
        with self.client.get(f"/p/{pp['id']}/package/download") as response:
            self.assertEqual(response.status_code, 200)
        self.client.post(f"/projects/{project['id']}/products/add", data={'product_type': 'safety_butler'})
        safety = next(p for p in repo.list_products(project['id']) if p['product_type'] == 'safety_butler')
        self.assertEqual(repo.field_values(safety['id']), {})
        survey.save_field_value(pp, project, {'key': 'fire_renovation_cost', 'value': 12345})
        survey.extract_latest_source(pp, project, provider='rule')
        self.assertEqual(json.loads(repo.field_values(pp['id'])['fire_renovation_cost']['value_json']), 12345)
        survey.save_field_value(pp, project, {'key': 'fire_renovation_cost', 'value': ''})
        self.assertFalse(validate(pp['id'], 'fire_butler')[0])
        with self.assertRaises(artifacts.ArtifactServiceError):
            artifacts.generate_artifacts(pp, ['delivery'])
        survey.save_field_value(pp, project, {'key': 'fire_renovation_cost', 'value': -1})
        self.assertFalse(validate(pp['id'], 'fire_butler')[0])
        survey.save_field_value(pp, project, {'key': 'fire_renovation_cost', 'value': 0})
        self.assertTrue(validate(pp['id'], 'fire_butler')[0])
        self.assertEqual(product_config('fire_butler')['capabilities'], product_config('safety_butler')['capabilities'])
        repo.save_project_transcript(project['id'], '', text)
        survey.import_project_transcript(pp, project)
        self.assertEqual(repo.latest_source(pp['id'])['kind'], 'audio_transcript')

    def test_fire_extraction_context_and_money(self):
        for amount, expected in [('5万元', 50000), ('五万元', 50000), ('50000', 50000), ('0元', 0)]:
            text = '消防主机按键无损坏。图纸有。消防主机点位编码表无。消控中心数量4个。恒益报价一次性改造费用为%s。' % amount
            result, _ = run_extraction(text, 'fire_butler', provider='rule')
            self.assertEqual(result['fire_renovation_cost']['value'], expected)
            self.assertEqual(result['fire_point_code_table']['value'], '无')
            self.assertEqual(result['fire_host_buttons']['value'], '按键无损坏')
        result, _ = run_extraction('消控中心数量4个。消防主机按键有损坏。图纸有，项目设备台账也有，需要安排现场进一步核查。', 'fire_butler', provider='rule')
        self.assertIsNone(result['fire_point_code_table']['value'])
        self.assertEqual(result['fire_host_buttons']['value'], '按键有损坏')
        with self.assertRaisesRegex(ExtractionError, '消防管家'):
            run_extraction('今天天气晴朗，非常适合外出散步，我们准备去河边走走再回家吃晚饭。', 'fire_butler', provider='rule')


if __name__ == '__main__':
    unittest.main()
