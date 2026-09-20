import unittest

from core.extract import _normalize_llm_value


class LlmContractTests(unittest.TestCase):
    def test_number_rejects_nonnumeric_content(self):
        field = {'type': 'number'}
        self.assertEqual(_normalize_llm_value(field, '12.5'), 12.5)
        self.assertIsNone(_normalize_llm_value(field, '大约十二台'))

    def test_select_rejects_value_outside_config(self):
        field = {'type': 'select', 'options': [{'value': '有'}, {'value': '无'}]}
        self.assertEqual(_normalize_llm_value(field, '有'), '有')
        self.assertIsNone(_normalize_llm_value(field, '可能有'))


if __name__ == '__main__':
    unittest.main()
