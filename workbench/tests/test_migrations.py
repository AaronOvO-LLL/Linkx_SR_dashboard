import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import db
from core.migrations import MIGRATIONS, apply_migrations


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """CREATE TABLE project_products (
                   id TEXT PRIMARY KEY, project_id TEXT NOT NULL, settings TEXT NOT NULL)""")
        self.conn.execute(
            'INSERT INTO project_products (id, project_id, settings) VALUES (?, ?, ?)',
            ('pp_1', 'prj_1', json.dumps({
                'quote_settings': {'service_years': '5'},
                'amount_overrides': {'custom': 100},
                'unrelated': {'keep': True},
            })))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def upgrade_to(self, version):
        """执行到指定版本为止，用于构造历史库形态。

        直接复用真实迁移函数而不是在测试里重抄一遍 DDL，否则历史形态一旦与
        生产迁移脱节，测试就再也发现不了升级问题。
        """
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   version     INTEGER PRIMARY KEY,
                   name        TEXT NOT NULL,
                   applied_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )""")
        for row_version, name, migrate in MIGRATIONS:
            if row_version > version:
                break
            migrate(self.conn)
            self.conn.execute(
                'INSERT OR REPLACE INTO schema_migrations (version, name) VALUES (?, ?)',
                (row_version, name))
        self.conn.commit()

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

    def test_v3_backfills_project_id_and_keeps_product_audit_column(self):
        self.upgrade_to(2)
        self.conn.execute(
            """INSERT INTO audio_transcription_jobs
               (id, project_product_id, original_name, local_path, created_at, updated_at)
               VALUES ('asr_1', 'pp_1', '踏勘.m4a', '/tmp/a.m4a',
                       '2026-09-01 10:00:00', '2026-09-01 10:05:00')""")
        self.conn.commit()

        apply_migrations(self.conn)

        row = self.conn.execute(
            'SELECT project_id, project_product_id FROM audio_transcription_jobs WHERE id=?',
            ('asr_1',)).fetchone()
        self.assertEqual(row['project_id'], 'prj_1')
        self.assertEqual(row['project_product_id'], 'pp_1')
        indexed = {r[1] for r in self.conn.execute('PRAGMA index_list(audio_transcription_jobs)')}
        self.assertIn('idx_audio_jobs_project', indexed)

    def test_v3_promotes_approved_transcript_to_project_level(self):
        """历史项目升级后不必重新转写：已确认的转写稿直接成为项目共享稿。"""
        self.upgrade_to(2)
        self.conn.execute(
            """INSERT INTO audio_transcription_jobs
               (id, project_product_id, original_name, local_path, status,
                corrected_transcript, approved_at, created_at, updated_at)
               VALUES ('asr_2', 'pp_1', '踏勘.m4a', '/tmp/a.m4a', 'approved',
                       '现场要做周界入侵。', '2026-09-01 10:05:00',
                       '2026-09-01 10:00:00', '2026-09-01 10:05:00')""")
        self.conn.execute(
            """INSERT INTO audio_transcription_jobs
               (id, project_product_id, original_name, local_path, status,
                corrected_transcript, created_at, updated_at)
               VALUES ('asr_3', 'pp_1', '未确认.m4a', '/tmp/b.m4a', 'awaiting_preview',
                       '还没有人工确认，不应提升。',
                       '2026-09-02 10:00:00', '2026-09-02 10:05:00')""")
        self.conn.commit()

        apply_migrations(self.conn)

        rows = self.conn.execute(
            'SELECT audio_job_id, content FROM project_transcripts WHERE project_id=?',
            ('prj_1',)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['audio_job_id'], 'asr_2')
        self.assertEqual(rows[0]['content'], '现场要做周界入侵。')


class LegacyUpgradeTests(unittest.TestCase):
    """历史库升级必须走 init_db 的真实顺序：先 executescript(SCHEMA) 再迁移。

    SCHEMA 里任何引用新列的语句都会在老库上直接失败——例如给还没有 project_id
    列的 audio_transcription_jobs 建索引。只测 apply_migrations 发现不了这类问题，
    而它会让用户的历史库在升级时打不开。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_file = str(Path(self.tmp.name) / 'legacy.db')
        self.original_conn = getattr(db._local, 'conn', None)
        db._local.conn = None

    def tearDown(self):
        if getattr(db._local, 'conn', None):
            db._local.conn.close()
        db._local.conn = self.original_conn
        self.tmp.cleanup()

    def build_v2_database(self):
        """按 v2 形态建一个历史库，复用真实迁移函数避免 DDL 抄错。"""
        conn = sqlite3.connect(self.db_file)
        conn.execute("""CREATE TABLE project_products (
                            id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                            settings TEXT NOT NULL DEFAULT '{}')""")
        conn.execute("""CREATE TABLE schema_migrations (
                            version INTEGER PRIMARY KEY, name TEXT NOT NULL,
                            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        for version, name, migrate in MIGRATIONS[:2]:
            migrate(conn)
            conn.execute('INSERT INTO schema_migrations (version, name) VALUES (?,?)',
                         (version, name))
        conn.execute("INSERT INTO project_products (id, project_id) VALUES ('pp_1','prj_1')")
        conn.execute(
            """INSERT INTO audio_transcription_jobs
               (id, project_product_id, original_name, local_path, status,
                corrected_transcript, approved_at, created_at, updated_at)
               VALUES ('asr_1','pp_1','踏勘.m4a','/tmp/a.m4a','approved',
                       '现场要做周界入侵。','2026-09-01 10:05:00',
                       '2026-09-01 10:00:00','2026-09-01 10:05:00')""")
        conn.commit()
        conn.close()

    def test_v2_database_upgrades_through_init_db(self):
        self.build_v2_database()
        with patch.object(db, 'db_path', return_value=self.db_file):
            db.init_db()
            job = db.query_one("SELECT project_id FROM audio_transcription_jobs WHERE id='asr_1'")
            self.assertEqual(job['project_id'], 'prj_1')
            self.assertIsNotNone(db.query_one(
                "SELECT * FROM project_transcripts WHERE audio_job_id='asr_1'"))
            indexes = {r['name'] for r in db.query(
                'PRAGMA index_list(audio_transcription_jobs)')}
            self.assertIn('idx_audio_jobs_project', indexes)
        db._local.conn.close()
        db._local.conn = None


if __name__ == '__main__':
    unittest.main()
