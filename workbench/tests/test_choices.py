import json
import unittest
from pathlib import Path
from unittest.mock import patch

from core import repo, paths, generate
from core.choices import choice_state, normalize_manual_choice
from core.config import field_map, field_config, artifact_map
from core.extract import _run_rule_engine, _normalize_llm_value, _run_llm_engine
from core.validate import validate
from services import artifacts, survey
from tests.test_input_modes import InputModeTests as Fixture


class ChoiceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.fixture.setUp()
        self.client = self.fixture.client
        self.project = self.fixture.project

    def tearDown(self):
        self.fixture.tearDown()

    def test_legacy_text_is_displayed_without_writes_and_survives_new_multiselection(self):
        pp = repo.ensure_product(self.project['id'], 'safety_butler')
        old = '仓库 A：夜间盲区\n优先处理 <北门>，不要丢失具体位置'
        repo.upsert_field_value(pp['id'], 'main_risks', json.dumps(old), 'confirmed', None, '', 'human', '0.4.0', 1)
        response = self.client.get('/p/' + pp['id'] + '/review')
        self.assertEqual(response.status_code, 200)
        self.assertIn('保留原有说明', response.get_data(as_text=True))
        self.assertIn('&lt;北门&gt;', response.get_data(as_text=True))
        self.assertEqual(json.loads(repo.field_values(pp['id'])['main_risks']['value_json']), old)
        saved = self.client.post('/p/' + pp['id'] + '/field', json={'key': 'main_risks', 'value': ['监控盲区', old]})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(generate.build_context(pp['id'], 'safety_butler')['display']['main_risks'], '监控盲区、' + old)
        self.assertEqual(repo.field_values(pp['id'])['main_risks']['template_version'], field_config('safety_butler')['template_version'])

    def test_legacy_single_remains_visible_and_new_choice_clear_and_protection_work(self):
        pp = repo.ensure_product(self.project['id'], 'safety_butler')
        old = '有机柜，深度 1 米，剩余 20U'
        survey.save_field_value(pp, self.project, {'key': 'has_cabinet', 'value': old})
        field = field_map('safety_butler')['has_cabinet']
        state = choice_state(field, json.dumps(old))
        self.assertEqual(state['custom'], [old])
        self.assertEqual(state['selected'], [])
        self.assertIn(old, self.client.get('/p/' + pp['id'] + '/review').get_data(as_text=True))
        survey.save_field_value(pp, self.project, {'key': 'has_cabinet', 'value': '无机柜'})
        self.assertEqual(repo.field_values(pp['id'])['has_cabinet']['protected'], 1)
        status, _, _ = survey.save_field_value(pp, self.project, {'key': 'has_cabinet', 'value': ''})
        self.assertEqual(status, 'optional_missing')

    def test_bad_shapes_rejected_before_saving_and_no_defaults(self):
        for product in ('safety_butler', 'fire_butler'):
            pp = repo.ensure_product(self.project['id'], product)
            self.assertEqual(repo.field_values(pp['id']), {})
            field = field_map(product)['business_desc']
            self.assertEqual(choice_state(field, None)['selected'], [])
            for value in ('文字不能作为新的多选提交', {'a': 1}, [1], [{'a': 1}]):
                result = self.client.post('/p/' + pp['id'] + '/field', json={'key': 'business_desc', 'value': value})
                self.assertEqual(result.status_code, 400)
            self.assertNotIn('business_desc', repo.field_values(pp['id']))
        fixed = field_map('safety_butler')['camera_type']
        with self.assertRaises(ValueError):
            normalize_manual_choice(fixed, '随意编写')

    def test_rule_extraction_negative_conflict_and_preserved_details(self):
        for text, expected in [('现场没有机柜。', '无机柜'), ('现场有机柜。', '有机柜'), ('目前没有预算。', '尚无预算')]:
            values, _ = _run_rule_engine(text, 'safety_butler', [])
            key = 'has_budget' if '预算' in text else 'has_cabinet'
            self.assertEqual(values[key]['value'], expected)
        for text in ('一号楼有机柜。二号楼没有机柜。', '不确定是否有机柜。'):
            values, _ = _run_rule_engine(text, 'safety_butler', [])
            self.assertEqual(values['has_cabinet']['status'], 'pending_confirm')
            self.assertIn('机柜', values['has_cabinet']['value'])
        values, _ = _run_rule_engine('业态：2-5层公寓，5-22层酒店。', 'fire_butler', [])
        self.assertIn('公寓', values['business_desc']['value'])
        self.assertIn('酒店', values['business_desc']['value'])
        self.assertTrue(any('2-5' in v for v in values['business_desc']['value']))
        values, _ = _run_rule_engine('消控室可分控且可传输信号。', 'fire_butler', [])
        self.assertEqual(values['fire_signal_transmission']['value'], '可分控且可传输信号')

    def test_manual_choices_generate_all_artifacts_for_both_products(self):
        for product in ('safety_butler', 'fire_butler'):
            pp = repo.ensure_product(self.project['id'], product)
            for key, f in field_map(product).items():
                if f['type'] == 'select': value = f['options'][0]['value']
                elif f['type'] == 'multiselect': value = [f['options'][0]['value'], '补充：北门优先']
                elif f['type'] == 'number': value = 4
                else: value = '现场已确认'
                survey.save_field_value(pp, self.project, {'key': key, 'value': value})
            self.assertTrue(validate(pp['id'], product)[0])
            with patch.object(paths, 'OUTPUT_DIR', str(Path(self.fixture.tmp.name) / 'outputs')):
                results = artifacts.generate_artifacts(pp, list(artifact_map(product)))
                self.assertTrue(all(r['status'] == 'success' for r in results), results)
                report = next(r for r in results if r['key'] == 'survey_result')
                self.assertIn('北门优先', Path(report['files'][0]['path']).read_text(encoding='utf-8'))
            self.assertGreater(len(field_config(product)['fields']), 20)

    def test_llm_shapes_and_custom_options(self):
        field = field_map('safety_butler')['main_risks']
        self.assertIsNone(_normalize_llm_value(field, '监控盲区'))
        self.assertEqual(_normalize_llm_value(field, ['监控盲区', {}, '北门死角', '监控盲区']), ['监控盲区', '北门死角'])

    def test_llm_custom_details_must_be_grounded_in_original_text(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({'choices': [{'message': {'content': json.dumps({
            'fields': {'main_risks': {'value': ['监控盲区', '北门死角', '没有提到的南门'],
                                     'quote': '北门死角', 'confidence': 'high'}}
        }, ensure_ascii=False)}}]}).encode()
        with patch('core.extract.read_env', return_value='test'), patch('core.extract.urllib.request.urlopen', return_value=response):
            # Request needs an actual URL scheme; this still never opens a network connection.
            with patch('core.extract.urllib.request.Request'):
                values, _ = _run_llm_engine('现场存在北门死角。', 'safety_butler', [])
        self.assertEqual(values['main_risks']['value'], ['监控盲区', '北门死角'])


if __name__ == '__main__':
    unittest.main()
