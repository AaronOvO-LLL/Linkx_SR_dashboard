"""Safety survey requirements, conditional brand details and historical records."""
import json
import unittest

from core import generate, repo
from core.config import field_map
from core.extract import _run_rule_engine
from core.validate import validate
from tests import test_input_modes


class SafetyFieldRulesTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_input_modes.InputModeTests()
        self.fixture.setUp()
        self.client = self.fixture.client
        self.pp = repo.ensure_product(self.fixture.project['id'], 'safety_butler')
        self.base = '/p/' + self.pp['id']

    def tearDown(self):
        self.fixture.tearDown()

    def save(self, key, value):
        return self.client.post(self.base + '/field', json={'key': key, 'value': value})

    def test_required_only_form_can_generate_and_shared_product_is_unchanged(self):
        fields = field_map('safety_butler')
        self.assertNotIn('service_type', fields)
        expected = {'property': {'property_company', 'project_owner'},
                    'network': {'has_internet'}, 'cabinet': set()}
        for group, keys in expected.items():
            self.assertEqual({k for k, f in fields.items() if f['group'] == group and f['required']}, keys)
        self.assertEqual(sum(f['group'] == 'property' for f in fields.values()), 6)
        self.assertEqual([o['value'] for o in fields['project_source']['options']],
                         ['内部项目', '外部项目', '渠道项目'])
        self.assertEqual([o['value'] for o in fields['storage_protocol']['options']], ['是', '否', '其他'])
        self.assertFalse(fields['storage_model']['required'])
        for key, f in fields.items():
            if not f['required']:
                continue
            value = (f['options'][0]['value'] if f['type'] == 'select' else
                     [f['options'][0]['value']] if f['type'] == 'multiselect' else
                     4 if f['type'] == 'number' else '现场确认')
            self.assertEqual(self.save(key, value).status_code, 200)
        self.assertTrue(validate(self.pp['id'], 'safety_butler')[0])
        self.assertEqual(repo.field_stats(self.pp['id'], 'safety_butler')['required_missing'], 0)
        context = generate.build_context(self.pp['id'], 'safety_butler')
        self.assertNotIn('service_type', context['data'])
        self.assertEqual(context['detail']['storage_protocol']['label'], '是否支持GB28181')
        fire = field_map('fire_butler')
        self.assertIn('service_type', fire)
        self.assertTrue(fire['contract_years']['required'])
        self.assertIn('重点项目', [o['value'] for o in fire['project_source']['options']])

    def test_other_brand_requires_detail_in_api_and_generation_validation(self):
        for key in ('camera_brand', 'storage_brand', 'switch_brand'):
            with self.subTest(key=key):
                options = field_map('safety_butler')[key]['options']
                standard = [options[0]['value'], options[1]['value']]
                self.assertEqual(self.save(key, standard).status_code, 200)
                for value in (['其他'], standard + ['其他', '  ']):
                    response = self.save(key, value)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn('具体品牌', response.json['error'])
                    self.assertEqual(json.loads(repo.field_values(self.pp['id'])[key]['value_json']), standard)
                value = standard + ['其他', '天地伟业']
                self.assertEqual(self.save(key, value).status_code, 200)
                self.assertEqual(generate.build_context(self.pp['id'], 'safety_butler')['display'][key], '、'.join(value))
                html = self.client.get(self.base + '/review').get_data(as_text=True)
                self.assertIn('data-require-other-detail="true"', html)
                self.assertIn('天地伟业</textarea>', html)
                repo.upsert_field_value(self.pp['id'], key, '["其他"]', 'extracted', 'high', '', 'ai', '0.6.0')
                self.assertTrue(any(i['field'] == key and i['level'] == 'error'
                                    for i in validate(self.pp['id'], 'safety_butler')[1]))
                self.assertEqual(self.save(key, []).json['status'], 'optional_missing')

    def test_historical_values_and_missing_status_follow_current_rules(self):
        for key in ('contract_years', 'network_admin', 'has_cabinet'):
            repo.upsert_field_value(self.pp['id'], key, 'null', 'required_missing', None, '', 'ai', '0.5.0')
        repo.upsert_field_value(self.pp['id'], 'camera_brand', '"海康"', 'confirmed', None, '', 'human', '0.5.0', 1)
        before = repo.field_values(self.pp['id'])
        stats = repo.field_stats(self.pp['id'], 'safety_butler')
        self.assertEqual(stats['required_missing'], stats['required_total'])
        html = self.client.get(self.base + '/review').get_data(as_text=True)
        self.assertEqual(html.count('data-status="required_missing"'), stats['required_total'])
        self.assertFalse(any(i['field'] == 'camera_brand' for i in validate(self.pp['id'], 'safety_butler')[1]))
        context = generate.build_context(self.pp['id'], 'safety_butler')
        self.assertEqual(context['display']['camera_brand'], '海康')
        self.assertEqual(context['detail']['has_cabinet']['status'], 'optional_missing')
        self.assertEqual(repo.field_values(self.pp['id']), before)

    def test_extraction_uses_new_options_and_multiple_brands(self):
        values, _ = _run_rule_engine('摄像头品牌为海康和大华。存储设备品牌为宇视。交换机品牌为华为和锐捷。项目来源为渠道项目。', 'safety_butler', [])
        self.assertEqual(set(values['camera_brand']['value']), {'海康', '大华'})
        self.assertEqual(values['storage_brand']['value'], ['宇视'])
        self.assertEqual(set(values['switch_brand']['value']), {'华为', '锐捷'})
        self.assertEqual(values['project_source']['value'], '渠道项目')
        for text, expected in [('存储设备支持GB28181。', '是'), ('存储设备不支持GB28181。', '否'),
                               ('存储设备支持GB/T28181-2022。', '是'), ('存储设备采用其他协议ONVIF。', '其他'),
                               ('存储设备是否支持GB28181尚不确定。', None),
                               ('存储设备是海康。', None)]:
            values, _ = _run_rule_engine(text, 'safety_butler', [])
            self.assertEqual(values['storage_protocol']['value'], expected, text)


if __name__ == '__main__':
    unittest.main()
