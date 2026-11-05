import json
import io
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
from tests.survey_helpers import sample_value


class FireButlerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_conn = getattr(db._local, 'conn', None)
        db._local.conn = None
        self.patches = [
            patch.object(db, 'db_path', return_value=str(Path(self.tmp.name) / 'test.db')),
            patch.object(paths, 'OUTPUT_DIR', str(Path(self.tmp.name) / 'outputs')),
            patch.object(paths, 'EXPORT_DIR', str(Path(self.tmp.name) / 'exports')),
            patch.object(paths, 'UPLOAD_DIR', str(Path(self.tmp.name) / 'uploads')),
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
        self.assertIsNone(json.loads(repo.field_values(pp['id'])['fire_control_room_survey']['value_json']))
        field = field_map('fire_butler')['fire_control_room_survey']
        room = dict(sample_value(field)[0], room_name='主楼', fire_renovation_cost=50000)
        for key, f in field_map('fire_butler').items():
            if f.get('required'):
                value = [room] if key == field['key'] else sample_value(f)
                r = self.client.post(f"/p/{pp['id']}/field", json={'key':key,'value':value})
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
        shared = repo.field_values(safety['id'])
        self.assertEqual(shared['project_name']['value_json'], repo.field_values(pp['id'])['project_name']['value_json'])
        self.assertTrue(all(field_map('safety_butler')[key].get('value_scope') == 'project' for key in shared))
        def save_cost(value):
            survey.save_field_value(pp, project, {'key':field['key'],'value':[dict(room,fire_renovation_cost=value)]})
        save_cost(12345)
        survey.extract_latest_source(pp, project, provider='rule')
        stored = json.loads(repo.field_values(pp['id'])[field['key']]['value_json'])
        self.assertEqual(stored[0]['fire_renovation_cost'],12345)
        save_cost('')
        self.assertTrue(validate(pp['id'], 'fire_butler')[0])
        self.assertEqual(artifacts.generate_artifacts(pp, ['delivery'])[0]['status'], 'success')
        save_cost(-1)
        self.assertFalse(validate(pp['id'], 'fire_butler')[0])
        save_cost(0)
        self.assertTrue(validate(pp['id'], 'fire_butler')[0])
        self.assertIn('field_attachments', product_config('fire_butler')['capabilities'])
        self.assertNotIn('field_attachments', product_config('safety_butler')['capabilities'])
        repo.save_project_transcript(project['id'], '', text)
        survey.import_project_transcript(pp, project)
        self.assertEqual(repo.latest_source(pp['id'])['kind'], 'audio_transcript')

    def test_fire_extraction_context_and_money(self):
        text = '消控中心数量1个。\n消控室名称：一号，是否可提供消防主机点位编码表：否，消防主机功能判断是否正常：正常，恒益报价一次性改造费用：50000'
        result, _ = run_extraction(text, 'fire_butler', provider='rule')
        room = result['fire_control_room_survey']['value'][0]
        self.assertEqual(room['fire_renovation_cost'],'50000')
        self.assertEqual(room['fire_point_code_table'],'否')
        self.assertEqual(room['fire_host_buttons'],'正常')
        with self.assertRaisesRegex(ExtractionError, '消防管家'):
            run_extraction('今天天气晴朗，非常适合外出散步，我们准备去河边走走再回家吃晚饭。', 'fire_butler', provider='rule')

    def make_product(self):
        self.client.post('/projects/create', data={'name':'消防修订测试', 'product_type':'fire_butler'})
        project = repo.list_projects('dept1')[0]
        return repo.list_products(project['id'])[0], project

    def test_supplier_isolation_and_function_negative(self):
        text = '消控中心数量2个。\n消控室名称：一号，恒益报价一次性改造费用：50000，消防主机功能判断是否正常：不正常\n消控室名称：二号，卫士报价一次性改造费用：30000'
        result, _ = run_extraction(text, 'fire_butler', provider='rule')
        first, second = result['fire_control_room_survey']['value']
        self.assertEqual(first['fire_renovation_cost'],'50000')
        self.assertEqual(first['fire_weishi_renovation_cost'],'')
        self.assertEqual(first['fire_host_buttons'],'不正常')
        self.assertEqual(second['fire_weishi_renovation_cost'],'30000')
        self.assertEqual(second['fire_renovation_cost'],'')
        self.assertNotIn('fire_package_type',result)

    def test_conditional_reason_and_historical_reconfirmation(self):
        pp, project = self.make_product()
        def save(key,value):survey.save_field_value(pp,project,{'key':key,'value':value})
        save('fire_implementation_ready','否')
        self.assertTrue(any(i['field']=='fire_implementation_reason' for i in validate(pp['id'],'fire_butler')[1]))
        save('fire_implementation_reason','现场网络尚未接通')
        self.assertFalse(any(i['field']=='fire_implementation_reason' for i in validate(pp['id'],'fire_butler')[1]))
        repo.upsert_field_value(pp['id'],'fire_host_buttons',json.dumps('按键无损坏',ensure_ascii=False),'confirmed',None,'','human','0.3.0',1)
        db.execute('DELETE FROM schema_migrations WHERE version=9')
        values = repo.field_values(pp['id'])
        self.assertEqual(values['fire_control_room_survey']['status'],'pending_confirm')
        self.assertEqual(json.loads(values['fire_control_room_survey']['value_json'])[0]['room_name'],'')
        self.assertIn('按键无损坏',self.client.get(f"/p/{pp['id']}/review").get_data(as_text=True))
        self.assertFalse(field_map('fire_butler')['land_area']['required'])
        self.assertTrue(field_map('device_butler')['land_area']['required'])

    def test_availability_age_boundary_and_removed_attachment_contract(self):
        from core.field_conditions import age_entries
        from datetime import date
        self.assertEqual(age_entries('A：2014-10-28',date(2026,10,27)), {'A':11})
        self.assertEqual(age_entries('A：2014-10-27',date(2026,10,27)), {'A':12})
        self.assertIsNone(age_entries('主机编号12，出厂时间待确认'))
        pp, project = self.make_product()
        field = field_map('fire_butler')['fire_control_room_survey']
        room = dict(sample_value(field)[0],fire_host_age='A：12年；B：11年',fire_records_available='')
        survey.save_field_value(pp,project,{'key':field['key'],'value':[room]})
        self.assertTrue(any('维保和维修记录' in i['message'] for i in validate(pp['id'],'fire_butler')[1]))
        for old in ('fire_survey_photos','fire_host_maintenance_records'):
            self.assertNotIn(old,field_map('fire_butler'))
            self.assertEqual(self.client.post(f"/p/{pp['id']}/field",json={'key':old,'value':[]}).status_code,400)
        other=repo.create_project('dept2',{'name':'其他分部'})
        other_pp=repo.add_product(other['id'],'fire_butler')
        self.assertEqual(self.client.get(f"/p/{other_pp['id']}/review").status_code,403)


if __name__ == '__main__':
    unittest.main()
