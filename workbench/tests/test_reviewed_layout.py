"""人工审核后的分类、表单、分室存储、历史升级及完整导出。"""
import json
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook
from core import db, generate, paths, repo
from core.config import field_config, field_map, artifact_map
from core.extract import run_extraction, _normalize_llm_value
from core.validate import validate
from services import artifacts, survey_export
from tests import test_input_modes
from tests.survey_helpers import sample_value


PRODUCTS = ('safety_butler', 'fire_butler', 'energy_butler', 'device_butler', 'asset_butler')


class ReviewedLayoutTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_input_modes.InputModeTests()
        self.fixture.setUp()
        self.client, self.project = self.fixture.client, self.fixture.project
        root = Path(self.fixture.tmp.name)
        self.patches = [patch.object(paths, 'OUTPUT_DIR', str(root/'outputs')),
                        patch.object(paths, 'EXPORT_DIR', str(root/'exports'))]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.fixture.tearDown()

    def pp(self, product):
        return repo.ensure_product(self.project['id'], product)

    def save(self, pp, key, value, code=200):
        r = self.client.post('/p/%s/field' % pp['id'], json={'key':key,'value':value})
        self.assertEqual(r.status_code, code, r.get_data(as_text=True))
        return r.json

    def room(self, name='东区监控室'):
        return dict(room_name=name, camera_brand=['海康'], camera_type='数字摄像头', camera_count=20,
                    storage_brand=['大华'], storage_type='NVR', storage_model='NVR-100', storage_count=2,
                    storage_protocol='是', sdk_device_compatible='')

    def fill_required(self, pp):
        for key, f in field_map(pp['product_type']).items():
            if not f.get('required'): continue
            self.save(pp, key, sample_value(f))

    def test_each_model_review_preview_and_export_follow_three_sections(self):
        for product in PRODUCTS:
            with self.subTest(product=product):
                model = field_config(product)
                self.assertEqual([s['name'] for s in model['sections']],
                                 ['项目基础信息','产品交付条件踏勘','相关业务访谈'])
                assigned = [k for s in model['sections'] for g in s['groups'] for k in g['fields']]
                self.assertEqual(assigned, [f['key'] for f in model['fields']])
                self.assertEqual(len(assigned), len(set(assigned)))
                pp = self.pp(product)
                for page in ('review', 'preview'):
                    r = self.client.get('/p/%s/%s' % (pp['id'], page))
                    self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
                    html = r.get_data(as_text=True)
                    self.assertEqual(html.count('data-section='), 3)
                    for section in model['sections']:
                        self.assertIn(section['name'], html)
                stream, _ = survey_export.export_survey(pp)
                wb = load_workbook(stream)
                rows = list(wb['调研结果'].values)
                self.assertEqual(rows[0], ('部分','分类','调研项','结果','单位','核对状态'))
                self.assertEqual(len(rows)-1, len(assigned))
                self.assertEqual({r[0] for r in rows[1:]}, {s['name'] for s in model['sections']})
                wb.close()

    def test_room_values_survive_refresh_are_independent_and_export_as_rows(self):
        pp = self.pp('safety_butler')
        rooms = [self.room(), dict(self.room('西区监控室'), storage_model='WEST-NVR', camera_count=30)]
        self.save(pp, 'monitor_center_count', 2)
        self.save(pp, 'monitor_room_survey', rooms)
        stored = json.loads(repo.field_values(pp['id'])['monitor_room_survey']['value_json'])
        self.assertEqual(stored, rooms)
        html = self.client.get('/p/%s/review' % pp['id']).get_data(as_text=True)
        self.assertIn('card-editor.js', html)
        self.assertIn('WEST-NVR', html)
        self.assertIn('东区监控室', html)
        stream, _ = survey_export.export_survey(pp)
        wb = load_workbook(stream)
        self.assertEqual(wb['监控室设备清单'].max_row, 3)
        self.assertEqual(wb['监控室设备清单']['A3'].value, 2)
        self.assertEqual(wb['监控室设备清单']['B3'].value, '西区监控室')
        wb.close()
        self.save(pp, 'monitor_room_survey', rooms[:1])
        self.assertTrue(any('须与' in i['message'] for i in validate(pp['id'],'safety_butler')[1]))
        self.assertNotIn('monitor_room_survey', repo.field_values(self.pp('fire_butler')['id']))

    def test_room_validation_rejects_duplicate_names_analog_negative_and_empty_required(self):
        pp = self.pp('safety_butler')
        self.save(pp, 'monitor_center_count', 2)
        self.save(pp, 'monitor_room_survey', [self.room(), self.room()])
        self.assertTrue(any('名称重复' in i['message'] for i in validate(pp['id'],'safety_butler')[1]))
        self.save(pp, 'monitor_room_survey', [dict(self.room(), camera_type='模拟摄像头', camera_count=-1)])
        errors = validate(pp['id'],'safety_butler')[1]
        self.assertTrue(any('纯模拟' in i['message'] for i in errors))
        self.assertTrue(any('有效数字' in i['message'] for i in errors))
        self.save(pp, 'monitor_room_survey', [dict(self.room(), room_name='', storage_type='')])
        self.assertTrue(any('监控室名称' in i['message'] for i in validate(pp['id'],'safety_butler')[1]))
        self.save(pp, 'monitor_room_survey', [dict(self.room(), camera_brand=['其他'])], 400)

    def test_fire_reviewed_fields_and_age_condition(self):
        pp = self.pp('fire_butler')
        f = field_map('fire_butler')
        self.assertNotIn('fire_survey_photos', f)
        self.assertNotIn('fire_host_maintenance_records', f)
        field = f['fire_control_room_survey']
        columns = {col['key']: col for col in field['columns']}
        self.assertFalse(columns['fire_renovation_cost']['required'])
        self.assertTrue(f['fire_duty_shifts']['required'])
        self.assertNotIn('fire_package_type', f)
        self.save(pp, 'fire_control_count', 2)
        one = dict(sample_value(field)[0], room_name='主楼消控室', fire_host_age='A：12年', fire_records_available='')
        two = dict(sample_value(field)[0], room_name='辅楼消控室', fire_host_age='B：5年', fire_records_available='')
        self.save(pp, field['key'], [one, two])
        errors = [i['message'] for i in validate(pp['id'], 'fire_butler')[1] if i['field']==field['key']]
        self.assertEqual(len(errors), 1)
        self.assertIn('第1行', errors[0])
        one['fire_records_available'] = '否'
        self.save(pp, field['key'], [one, two])
        self.assertFalse(any(i['field']==field['key'] for i in validate(pp['id'], 'fire_butler')[1]))
        self.save(pp, field['key'], [dict(one,fire_point_code_table='有编码表')], 400)

    def test_guide_download_and_project_metadata(self):
        pp = self.pp('safety_butler')
        repo.update_project(self.project['id'], {'name':'旧项目', 'location':'历史地点'})
        url = '/projects/' + self.project['id']
        self.client.post(url + '/update', data={'name':'旧项目','owner':'王AR','contact':'李FR'})
        self.assertEqual(repo.get_project(self.project['id'])['location'], '历史地点')
        page = self.client.get(url).get_data(as_text=True)
        self.assertIn('安全管家/云眸', page)
        self.assertIn('项目AR', page)
        self.assertIn('项目FR', page)
        self.assertNotIn('name="location"', page)
        self.assertIn('踏勘条件及问题指引（供踏勘时备查）', page)
        guide = '/p/%s/survey-guide' % pp['id']
        response = self.client.get(guide)
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        for term in ('灵石八大赛道','安全管家/云眸','保洁类','其他算法需求','type="checkbox"','下载 HTML'):
            self.assertIn(term, body)
        downloaded = self.client.get(guide + '?download=1')
        self.assertIn('attachment;', downloaded.headers['Content-Disposition'])
        self.assertIn('localStorage', downloaded.get_data(as_text=True))

    def test_new_interviews_and_removed_asset_fields(self):
        pp = self.pp('device_butler')
        self.save(pp, 'automation_systems', ['BA','IBMS'])
        self.save(pp, 'inspection_frequency', '每两小时一次')
        self.save(pp, 'engineering_staff_count', 0)
        self.assertIn('每两小时一次', generate.build_context(pp['id'],'device_butler')['display']['inspection_frequency'])
        asset = self.pp('asset_butler')
        self.assertNotIn('submitted_at', field_map('asset_butler'))
        self.assertNotIn('submitter_contact', field_map('asset_butler'))
        self.assertNotIn('信息提交时间', self.client.get('/p/%s/review'%asset['id']).get_data(as_text=True))

    def test_all_artifacts_generate_with_current_required_fields(self):
        for product in PRODUCTS:
            with self.subTest(product=product):
                pp = self.pp(product)
                self.fill_required(pp)
                self.assertTrue(validate(pp['id'],product)[0], validate(pp['id'],product)[1])
                for result in artifacts.generate_artifacts(pp, list(artifact_map(product))):
                    self.assertEqual(result['status'],'success',result.get('error'))

    def test_legacy_migration_keeps_old_answers_and_marks_room_assignment_pending(self):
        pp = self.pp('safety_butler')
        for key, value in [('camera_count',100), ('storage_model','OLD-NVR'), ('current_staffing','保安8人，两班轮值')]:
            repo.upsert_field_value(pp['id'],key,json.dumps(value,ensure_ascii=False),'confirmed',None,'原始现场','human','0.10.0',1)
        db.execute('DELETE FROM schema_migrations WHERE version=8')
        db.init_db()
        values = repo.field_values(pp['id'])
        rows = json.loads(values['monitor_room_survey']['value_json'])
        self.assertEqual(rows[0]['room_name'],'')
        self.assertEqual(rows[0]['camera_count'],100)
        self.assertEqual(values['monitor_room_survey']['status'],'pending_confirm')
        self.assertEqual(values['monitor_room_survey']['protected'],1)
        self.assertNotIn('security_staff_total',values)
        self.assertTrue(Path(db.db_path()+'.pre-review-v8.bak').exists())
        self.assertIn('保安8人', self.client.get('/p/%s/review'%pp['id']).get_data(as_text=True))
        before = db.query('SELECT * FROM survey_field_values ORDER BY id')
        db.init_db()
        self.assertEqual(before, db.query('SELECT * FROM survey_field_values ORDER BY id'))
        self.assertTrue(any(r['field_key']=='current_staffing' for r in before))

    def test_rule_and_llm_card_contract_and_single_station_guard(self):
        field = field_map('safety_butler')['monitor_room_survey']
        self.assertEqual(_normalize_llm_value(field,[self.room()])[0]['camera_brand'],['海康'])
        self.assertIsNone(_normalize_llm_value(field,[dict(self.room(),camera_brand='海康')]))
        result, _ = run_extraction('监控室名称：东区监控室，摄像头品牌：海康，摄像头类型：数字摄像头，摄像头数量：20，存储设备类型：NVR', 'safety_butler',provider='rule')
        self.assertEqual(result['monitor_room_survey']['value'][0]['room_name'],'东区监控室')
        self.assertEqual(result['monitor_room_survey']['value'][0]['camera_brand'],['海康'])
        result, _ = run_extraction('项目采用水冷机组中央空调，没有冰蓄冷也没有水蓄冷，3个冷站合计电费超过100万。', 'energy_butler',provider='rule')
        self.assertIsNone(result['energy_station_survey']['value'])
