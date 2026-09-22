import json
import tempfile
import unittest
from pathlib import Path

from core import departments


class DepartmentCredentialTests(unittest.TestCase):
    """凭据从只读种子（config/）落到可写运行时文件（data/），并以哈希保存。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.seed = Path(self.tmp.name) / 'seed.json'
        self.runtime = Path(self.tmp.name) / 'departments.json'
        self.seed.write_text(json.dumps({'departments': [
            {'key': 'dept1', 'name': '一部', 'password': '000000', 'changed': False},
        ]}, ensure_ascii=False), encoding='utf-8')
        self._saved = (departments.SEED_PATH, departments.RUNTIME_PATH, departments._cache)
        departments.SEED_PATH = str(self.seed)
        departments.RUNTIME_PATH = str(self.runtime)
        departments._cache = None

    def tearDown(self):
        departments.SEED_PATH, departments.RUNTIME_PATH, departments._cache = self._saved
        self.tmp.cleanup()

    def stored(self):
        return json.loads(self.runtime.read_text(encoding='utf-8'))['departments'][0]

    def test_seed_password_is_hashed_not_copied(self):
        departments.ensure_initialized()
        dept = self.stored()
        self.assertNotIn('password', dept)
        self.assertTrue(dept['password_hash'].startswith('pbkdf2:sha256:'))
        self.assertTrue(departments.verify(dept, '000000'))
        self.assertFalse(departments.verify(dept, '000001'))

    def test_restart_keeps_changed_password(self):
        """启动时的初始化不得把改过的口令重置回种子值，否则每次重启都要重新改密。"""
        departments.ensure_initialized()
        departments.set_password('dept1', 'new-secret')
        departments.ensure_initialized()
        dept = self.stored()
        self.assertTrue(departments.verify(dept, 'new-secret'))
        self.assertFalse(departments.verify(dept, '000000'))
        self.assertTrue(dept['changed'])

    def test_missing_hash_never_authenticates(self):
        """凭据缺 password_hash 时必须拒绝，不能退化成"空口令即可登录"。"""
        for candidate in ({'key': 'dept1'}, {'key': 'dept1', 'password_hash': ''}, None):
            self.assertFalse(departments.verify(candidate, ''))
            self.assertFalse(departments.verify(candidate, '000000'))

    def test_unchanged_names_follows_changed_flag(self):
        departments.ensure_initialized()
        self.assertEqual(departments.unchanged_names(), ['一部'])
        departments.set_password('dept1', 'new-secret')
        self.assertEqual(departments.unchanged_names(), [])


if __name__ == '__main__':
    unittest.main()
