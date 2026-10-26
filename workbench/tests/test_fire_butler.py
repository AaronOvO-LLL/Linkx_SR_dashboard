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
        values = repo.field_values(pp['id'])
        self.assertEqual(json.loads(values['fire_renovation_cost']['value_json']), 50000)
        self.assertIn('4号', json.loads(values['fire_host_relationships']['value_json']))
        self.assertIn('4号', json.loads(values['fire_host_brands']['value_json']))
        for key, f in field_map('fire_butler').items():
            if key == 'fire_renovation_cost' or f['type'] == 'attachment':
                continue
            value = 4 if f['type'] == 'number' else (
                f['options'][0]['value'] if f['type'] == 'select' else (
                [f['options'][0]['value']] if f['type'] == 'multiselect' else '现场已确认'))
            if key == 'fire_host_age':
                value = '1号主机：5年'
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
        shared = repo.field_values(safety['id'])
        self.assertEqual(shared['project_name']['value_json'], repo.field_values(pp['id'])['project_name']['value_json'])
        self.assertTrue(all(field_map('safety_butler')[key].get('value_scope') == 'project' for key in shared))
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
        self.assertIn('field_attachments', product_config('fire_butler')['capabilities'])
        self.assertNotIn('field_attachments', product_config('safety_butler')['capabilities'])
        repo.save_project_transcript(project['id'], '', text)
        survey.import_project_transcript(pp, project)
        self.assertEqual(repo.latest_source(pp['id'])['kind'], 'audio_transcript')

    def test_fire_extraction_context_and_money(self):
        for amount, expected in [('5万元', 50000), ('五万元', 50000), ('50000', 50000), ('0元', 0)]:
            text = '消防主机功能正常。图纸有。消防主机点位编码表无。消控中心数量4个。恒益报价一次性改造费用为%s。' % amount
            result, _ = run_extraction(text, 'fire_butler', provider='rule')
            self.assertEqual(result['fire_renovation_cost']['value'], expected)
            self.assertEqual(result['fire_point_code_table']['value'], '无')
            self.assertEqual(result['fire_host_buttons']['value'], '正常')
        result, _ = run_extraction('消控中心数量4个。消防主机按键有损坏。图纸有，项目设备台账也有，需要安排现场进一步核查。', 'fire_butler', provider='rule')
        self.assertIsNone(result['fire_point_code_table']['value'])
        self.assertIsNone(result['fire_host_buttons']['value'])
        with self.assertRaisesRegex(ExtractionError, '消防管家'):
            run_extraction('今天天气晴朗，非常适合外出散步，我们准备去河边走走再回家吃晚饭。', 'fire_butler', provider='rule')

    def make_product(self):
        self.client.post('/projects/create', data={'name':'消防修订测试', 'product_type':'fire_butler'})
        project = repo.list_projects('dept1')[0]
        return repo.list_products(project['id'])[0], project

    def test_supplier_isolation_and_function_negative(self):
        text = '恒益报价5万元，卫士报价3万元。消防主机功能不正常。套餐选择智控，本项目不具备实施条件。'
        result, _ = run_extraction(text, 'fire_butler', provider='rule')
        self.assertEqual(result['fire_renovation_cost']['value'], 50000)
        self.assertEqual(result['fire_weishi_renovation_cost']['value'], 30000)
        self.assertEqual(result['fire_host_buttons']['value'], '不正常')
        self.assertEqual(result['fire_package_type']['value'], '智控')
        for key in ['fire_survey_photos', 'fire_host_maintenance_records']:
            self.assertIsNone(result[key]['value'])
        result, _ = run_extraction('消防主机功能正常，卫士报价3万元。消控室有一间，维保单位资料尚未提供。', 'fire_butler', provider='rule')
        self.assertIsNone(result['fire_renovation_cost']['value'])

    def test_conditional_reason_and_historical_reconfirmation(self):
        pp, project = self.make_product()
        def save(key,value):survey.save_field_value(pp,project,{'key':key,'value':value})
        save('fire_implementation_ready','否')
        self.assertTrue(any(i['field']=='fire_implementation_reason' for i in validate(pp['id'],'fire_butler')[1]))
        save('fire_implementation_reason','现场网络尚未接通')
        self.assertFalse(any(i['field']=='fire_implementation_reason' for i in validate(pp['id'],'fire_butler')[1]))
        repo.upsert_field_value(pp['id'],'fire_host_buttons',json.dumps('按键无损坏',ensure_ascii=False),'confirmed',None,'','human','0.3.0',1)
        self.assertTrue(any('历史答案' in i['message'] for i in validate(pp['id'],'fire_butler')[1]))
        page=self.client.get(f"/p/{pp['id']}/review")
        self.assertIn('按键无损坏',page.get_data(as_text=True))
        save('fire_host_buttons','正常')
        self.assertFalse(any('历史答案' in i['message'] for i in validate(pp['id'],'fire_butler')[1]))
        self.assertFalse(field_map('fire_butler')['land_area']['required'])
        self.assertTrue(field_map('device_butler')['land_area']['required'])

    def test_upload_age_boundary_and_access(self):
        from services import field_attachments
        from core.field_conditions import age_entries
        from datetime import date
        self.assertEqual(age_entries('A：2014-10-28',date(2026,10,27)), {'A':11})
        self.assertEqual(age_entries('A：2014-10-27',date(2026,10,27)), {'A':12})
        self.assertIsNone(age_entries('主机编号12，出厂时间待确认'))
        pp, project = self.make_product()
        survey.save_field_value(pp,project,{'key':'fire_host_age','value':'A：12年；B：11年'})
        key='fire_host_maintenance_records'
        def problems():return [i for i in validate(pp['id'],'fire_butler')[1] if i['field']==key]
        self.assertTrue(problems())
        url=f"/p/{pp['id']}/attachments/{key}"
        for kind in ['维保记录','维修记录']:
            response=self.client.post(url,data={'file':(io.BytesIO(b'%PDF-1.4\nrecord'),'记录.pdf'),'host':'A','description':'主机记录','kind':kind})
            self.assertEqual(response.status_code,302)
        self.assertFalse(problems())
        items=field_attachments.entries(pp,key)
        self.assertEqual(len(items),2)
        with self.client.get(url+'/'+items[0]['id']) as response:
            self.assertEqual(response.status_code,200)
            self.assertIn('attachment',response.headers['Content-Disposition'])
        rejected=self.client.post(f"/p/{pp['id']}/field",json={'key':key,'value':items})
        self.assertEqual(rejected.status_code,400)
        self.client.post(url,data={'file':(io.BytesIO(b'fake'),'fake.pdf'),'host':'A','description':'test','kind':'维修记录'})
        self.assertEqual(len(field_attachments.entries(pp,key)),2)
        other=repo.create_project('dept2',{'name':'其他分部'})
        other_pp=repo.add_product(other['id'],'fire_butler')
        self.assertEqual(self.client.get(f"/p/{other_pp['id']}/attachments/{key}/"+items[0]['id']).status_code,403)
        survey.save_transcript(pp,project,'消防主机有两台，A出厂12年，B出厂11年。项目已提供维保维修资料，恒益报价5万元。')
        survey.extract_latest_source(pp,project,provider='rule')
        self.assertEqual(field_attachments.entries(pp,key),items)
        copy = repo.copy_project(project['id'])
        copy_pp = repo.list_products(copy['id'])[0]
        self.assertTrue(Path(field_attachments.download(copy_pp,key,items[0]['id'])[0]).is_file())
        self.client.post(url+'/'+items[0]['id']+'/remove')
        self.assertTrue(problems())
        self.assertTrue(Path(field_attachments.download(copy_pp,key,items[0]['id'])[0]).is_file())


if __name__ == '__main__':
    unittest.main()
