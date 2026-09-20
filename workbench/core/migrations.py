"""轻量数据库迁移器。

项目仍使用 SQLite，不需要为当前规模引入大型迁移框架；但每个已发布的数据
变更必须有不可变版本记录，才能安全升级用户已经存在的数据库。
"""

import json


def _remove_pricing_settings(conn):
    """移除活动项目设置中的历史报价参数。

    旧生成记录和文件故意保留，便于审计与备份；运行时已经不再展示或读取
    它们。将历史文件物理删除应当是独立、可确认的数据清理动作。
    """
    rows = conn.execute('SELECT id, settings FROM project_products').fetchall()
    for row in rows:
        try:
            settings = json.loads(row['settings'] or '{}')
        except (TypeError, ValueError):
            continue
        changed = False
        for key in ('quote_settings', 'amount_overrides'):
            if key in settings:
                settings.pop(key)
                changed = True
        if changed:
            conn.execute(
                'UPDATE project_products SET settings=? WHERE id=?',
                (json.dumps(settings, ensure_ascii=False), row['id']))


def _add_audio_transcription_jobs(conn):
    """增加录音转写任务表，让长任务可跨页面刷新和应用重启恢复。"""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS audio_transcription_jobs (
            id                  TEXT PRIMARY KEY,
            project_product_id  TEXT NOT NULL,
            original_name       TEXT NOT NULL,
            local_path          TEXT NOT NULL,
            file_size           INTEGER NOT NULL DEFAULT 0,
            provider            TEXT NOT NULL DEFAULT 'tencent',
            status              TEXT NOT NULL DEFAULT 'pending',
            status_message      TEXT NOT NULL DEFAULT '',
            provider_job_id     TEXT,
            object_key          TEXT,
            raw_transcript      TEXT NOT NULL DEFAULT '',
            corrected_transcript TEXT NOT NULL DEFAULT '',
            segments_json       TEXT NOT NULL DEFAULT '[]',
            corrections_json    TEXT NOT NULL DEFAULT '[]',
            audio_duration      REAL NOT NULL DEFAULT 0,
            error               TEXT NOT NULL DEFAULT '',
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL,
            completed_at        TEXT,
            approved_at         TEXT
        )""")
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_audio_jobs_pp
           ON audio_transcription_jobs(project_product_id, created_at)""")


MIGRATIONS = [
    (1, 'remove_pricing_settings', _remove_pricing_settings),
    (2, 'add_audio_transcription_jobs', _add_audio_transcription_jobs),
]


def apply_migrations(conn):
    """按版本顺序执行尚未应用的迁移，每个版本独立提交。

    单个版本使用事务，是为了避免应用启动中断时留下“数据改了一半、版本却
    未登记”的状态。迁移函数抛错时会回滚并阻止应用带病启动。
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               version     INTEGER PRIMARY KEY,
               name        TEXT NOT NULL,
               applied_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )""")
    conn.commit()
    applied = {
        row[0] for row in conn.execute('SELECT version FROM schema_migrations').fetchall()
    }
    for version, name, migrate in MIGRATIONS:
        if version in applied:
            continue
        try:
            conn.execute('BEGIN')
            migrate(conn)
            conn.execute(
                'INSERT INTO schema_migrations (version, name) VALUES (?, ?)',
                (version, name))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
