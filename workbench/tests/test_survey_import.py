"""Exercise the shipped templates and the replacement boundary through Flask."""
import json
import unittest
from io import BytesIO

from openpyxl import load_workbook

from core import repo
from tests import test_input_modes


class SurveyImportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_input_modes.InputModeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.client, self.project = self.fixture.client, self.fixture.project

    def open_product(self, product):
        pp = repo.ensure_product(self.project['id'], product)
        url = '/p/' + pp['id']
        page = self.client.get(url + '/review').get_data(as_text=True)
        self.assertIn('下载 Excel 模板', page)
        with self.client.session_transaction() as session:
            token = session['csrf_token']
        with self.client.get(url + '/survey-table-template') as response:
            self.assertEqual(response.status_code, 200)
            workbook = load_workbook(BytesIO(response.data))
        return pp, url, token, workbook

    def upload(self, url, token, workbook, confirm='yes'):
        stream = BytesIO()
        workbook.save(stream)
        stream.seek(0)
        return self.client.post(url + '/survey-table-import', data={
            'csrf_token': token, 'replace': confirm, 'workbook': (stream, '踏勘.xlsx'),
        }, follow_redirects=True)

    def fill_row(self, sheet, row, values):
        for col, value in enumerate(values, 1):
            sheet.cell(row, col).value = value

    def test_device_replaces_only_the_equipment_table_and_materializes_fixed_items(self):
        pp, url, token, book = self.open_product('device_butler')
        key = 'device_room_survey'
        self.client.post(url + '/field', json={'key': 'inspection_frequency', 'value': '每天一次'})
        self.client.post(url + '/field', json={'key': key, 'value': [{
            'room_name': '旧泵房', 'space_type': '独立生活水泵房', 'device': '生活水箱', 'qty': 4,
        }]})
        revision = repo.project_fields_revision(self.project['id'])
        self.fill_row(book['设备房设备'], 2, ['新泵房', '独立生活水泵房', '生活水箱', 2])
        response = self.upload(url, token, book)
        self.assertIn('设备清单已替换', response.get_data(as_text=True))
        values = repo.field_values(pp['id'])
        rows = json.loads(values[key]['value_json'])
        self.assertEqual({row['room_name'] for row in rows}, {'新泵房'})
        self.assertTrue(any(row['device'] == '设备房温湿度' for row in rows))
        self.assertTrue(values[key]['protected'])
        self.assertEqual(json.loads(values['inspection_frequency']['value_json']), '每天一次')
        self.assertEqual(repo.project_fields_revision(self.project['id']), revision + 1)
        book.close()

    def test_energy_invalid_upload_keeps_original_then_valid_upload_replaces_by_station(self):
        pp, url, token, book = self.open_product('energy_butler')
        key = 'energy_station_survey'
        old = [{'room_name': '旧冷站', 'energy_fee_over_1m': '否', 'energy_equipment_list': []}]
        self.client.post(url + '/field', json={'key': key, 'value': old})
        original = repo.field_values(pp['id'])[key]
        revision = repo.project_fields_revision(self.project['id'])
        self.fill_row(book['冷站'], 2, ['东区', '是'])
        self.fill_row(book['冷站'], 3, ['西区', '否'])
        self.fill_row(book['冷站设备'], 2, ['东区', '办公', '一号楼', '制冷主机', None, None, None, 2])
        self.fill_row(book['冷站设备'], 3, ['未知冷站', '商业', '二号楼', '冷冻泵', None, None, None, 3])
        response = self.upload(url, token, book)
        self.assertIn('第 3 行的冷站名称', response.get_data(as_text=True))
        self.assertEqual(repo.field_values(pp['id'])[key], original)
        self.assertEqual(repo.project_fields_revision(self.project['id']), revision)
        book['冷站设备']['A3'] = '西区'
        self.assertIn('确认替换', self.upload(url, token, book, confirm='no').get_data(as_text=True))
        self.assertEqual(repo.field_values(pp['id'])[key], original)
        self.assertIn('设备清单已替换', self.upload(url, token, book).get_data(as_text=True))
        stations = json.loads(repo.field_values(pp['id'])[key]['value_json'])
        self.assertEqual([row['room_name'] for row in stations], ['东区', '西区'])
        self.assertEqual([row['energy_equipment_list'][0]['quantity'] for row in stations], [2, 3])
        book.close()
