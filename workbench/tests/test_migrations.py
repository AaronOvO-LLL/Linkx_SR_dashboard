import json
import sqlite3
import unittest

from core.migrations import apply_migrations


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            'CREATE TABLE project_products (id TEXT PRIMARY KEY, settings TEXT NOT NULL)')
        self.conn.execute(
            'INSERT INTO project_products (id, settings) VALUES (?, ?)',
            ('pp_1', json.dumps({
                'quote_settings': {'service_years': '5'},
                'amount_overrides': {'custom': 100},
                'unrelated': {'keep': True},
            })))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_migration_removes_only_pricing_settings(self):
        apply_migrations(self.conn)
        settings = json.loads(
            self.conn.execute(
                'SELECT settings FROM project_products WHERE id=?', ('pp_1',)
            ).fetchone()['settings'])
        self.assertEqual(settings, {'unrelated': {'keep': True}})

    def test_migration_is_idempotent(self):
        apply_migrations(self.conn)
        apply_migrations(self.conn)
        count = self.conn.execute(
            'SELECT COUNT(*) FROM schema_migrations WHERE version=1').fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == '__main__':
    unittest.main()

