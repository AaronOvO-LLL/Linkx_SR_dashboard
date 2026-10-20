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


def _column_names(conn, table):
    return {row[1] for row in conn.execute('PRAGMA table_info(%s)' % table).fetchall()}


def _add_project_level_audio(conn):
    """把录音转写从产品级提升为项目级。

    一次踏勘通常只录一份音，但可能同时评估多个产品；挂在产品下会导致同一段
    录音被重复上传、重复转写、重复计费。改为项目级后，转写稿由项目下所有产品
    共享导入。

    历史任务通过 project_products 反查回填 project_id，保证老记录仍可查、可回听；
    project_product_id 列保留不删，作为"当时在哪个产品下转写"的审计信息。
    """
    if 'project_id' not in _column_names(conn, 'audio_transcription_jobs'):
        conn.execute(
            "ALTER TABLE audio_transcription_jobs ADD COLUMN project_id TEXT NOT NULL DEFAULT ''")
    conn.execute(
        """UPDATE audio_transcription_jobs
           SET project_id = (SELECT project_id FROM project_products
                             WHERE project_products.id = audio_transcription_jobs.project_product_id)
           WHERE project_id = '' AND project_product_id <> ''
             AND EXISTS (SELECT 1 FROM project_products
                         WHERE project_products.id = audio_transcription_jobs.project_product_id)""")
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_audio_jobs_project
           ON audio_transcription_jobs(project_id, created_at)""")

    conn.execute(
        """CREATE TABLE IF NOT EXISTS project_transcripts (
            id                  TEXT PRIMARY KEY,
            project_id          TEXT NOT NULL,
            audio_job_id        TEXT NOT NULL DEFAULT '',
            content             TEXT NOT NULL DEFAULT '',
            char_count          INTEGER NOT NULL DEFAULT 0,
            created_at          TEXT NOT NULL
        )""")
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_project_transcripts
           ON project_transcripts(project_id, created_at)""")

    # 已经人工确认过的历史转写稿提升为项目级共享稿，老项目升级后不必重新转写。
    # 未确认的任务不提升：只有人工预览确认过的文本才有资格成为产品的提取来源。
    rows = conn.execute(
        """SELECT id, project_id, corrected_transcript, approved_at
           FROM audio_transcription_jobs
           WHERE status = 'approved' AND project_id <> '' AND corrected_transcript <> ''""",
    ).fetchall()
    for row in rows:
        conn.execute(
            """INSERT INTO project_transcripts
               (id, project_id, audio_job_id, content, char_count, created_at)
               VALUES (?,?,?,?,?,?)""",
            ('pt_%s' % row['id'][-12:], row['project_id'], row['id'],
             row['corrected_transcript'], len(row['corrected_transcript']),
             row['approved_at']))


def _add_project_fields(conn):
    """v4：提升公共答案；有歧义时留空待确认，原答案及候选快照永久保留。"""
    conn.execute('''CREATE TABLE IF NOT EXISTS project_field_values (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, field_key TEXT NOT NULL,
        value_json TEXT NOT NULL DEFAULT 'null', status TEXT NOT NULL,
        confidence TEXT, source_quote TEXT NOT NULL DEFAULT '',
        updated_by TEXT NOT NULL, template_version TEXT NOT NULL DEFAULT '',
        protected INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL, source_product_id TEXT NOT NULL DEFAULT '',
        UNIQUE(project_id, field_key))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS project_field_conflicts (
        project_id TEXT NOT NULL, field_key TEXT NOT NULL, candidates_json TEXT NOT NULL,
        resolved_at TEXT, resolved_value_json TEXT,
        PRIMARY KEY(project_id, field_key))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS project_field_history (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, field_key TEXT NOT NULL,
        record_json TEXT NOT NULL, created_at TEXT NOT NULL)''')
    conn.execute('''CREATE INDEX IF NOT EXISTS idx_pfh_project
        ON project_field_history(project_id, field_key)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS project_field_revisions (
        project_id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0)''')
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ('artifact_runs', 'validation_results'):
        if table in tables and 'project_fields_revision' not in _column_names(conn, table):
            conn.execute('ALTER TABLE %s ADD COLUMN project_fields_revision INTEGER NOT NULL DEFAULT 0' % table)
    if 'survey_field_values' not in tables:
        return
    # 固定迁移当时的字段集合，未来修改配置不能改变历史迁移的含义。
    keys = ('project_name', 'project_source', 'business_type', 'build_type', 'land_area',
            'building_area', 'site_address', 'contract_years', 'property_company',
            'project_owner', 'service_type', 'business_desc', 'fee_mode', 'mgmt_scope')
    rows = conn.execute('''SELECT fv.*, pp.project_id FROM survey_field_values fv
        JOIN project_products pp ON pp.id=fv.project_product_id
        WHERE fv.field_key IN (%s) ORDER BY fv.updated_at DESC, fv.id''' %
        ','.join('?' for _ in keys), keys).fetchall()
    grouped = {}
    for row in rows:
        item = dict(row)
        try:
            value = json.loads(item['value_json'])
        except (TypeError, ValueError):
            value = item['value_json']  # 损坏的历史记录也不能静默丢弃
        if value is None or value == [] or value == {} or (isinstance(value, str) and not value.strip()):
            continue
        grouped.setdefault((item['project_id'], item['field_key']), []).append(item)
    for (pid, key), candidates in grouped.items():
        # 相同值优先保留人工确认的保护状态；不同值一律交由用户选择。
        candidates.sort(key=lambda r: bool(r['protected']), reverse=True)
        values = {json.dumps(json.loads(r['value_json']), ensure_ascii=False, sort_keys=True)
                  if _valid_json(r['value_json']) else r['value_json'] for r in candidates}
        chosen = dict(candidates[0])
        if len(values) > 1 or any(not _valid_json(r['value_json']) for r in candidates):
            conn.execute('''INSERT INTO project_field_conflicts
                (project_id, field_key, candidates_json) VALUES (?,?,?)''',
                (pid, key, json.dumps(candidates, ensure_ascii=False)))
            chosen.update(value_json='null', status='pending_confirm', protected=1,
                          updated_by='migration', note='历史公共资料存在不同答案，请确认后共享。')
        conn.execute('''INSERT INTO project_field_values
            (id, project_id, field_key, value_json, status, confidence, source_quote,
             updated_by, template_version, protected, note, updated_at, source_product_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            ('pf_' + chosen['id'], pid, key, chosen['value_json'], chosen['status'],
             chosen['confidence'], chosen['source_quote'], chosen['updated_by'],
             chosen['template_version'], chosen['protected'], chosen['note'],
             chosen['updated_at'], chosen['project_product_id']))
        conn.execute('''INSERT INTO project_field_history
            (id, project_id, field_key, record_json, created_at) VALUES (?,?,?,?,?)''',
            ('ph_' + chosen['id'], pid, key, json.dumps(candidates, ensure_ascii=False), chosen['updated_at']))
        conn.execute('INSERT OR IGNORE INTO project_field_revisions (project_id, revision) VALUES (?,1)', (pid,))
    if 'validation_results' in tables:
        conn.execute('''DELETE FROM validation_results WHERE project_product_id IN
            (SELECT id FROM project_products WHERE project_id IN (SELECT project_id FROM project_field_revisions))''')


def _valid_json(value):
    try:
        json.loads(value)
        return True
    except (TypeError, ValueError):
        return False


MIGRATIONS = [
    (1, 'remove_pricing_settings', _remove_pricing_settings),
    (2, 'add_audio_transcription_jobs', _add_audio_transcription_jobs),
    (3, 'add_project_level_audio', _add_project_level_audio),
    (4, 'add_project_fields', _add_project_fields),
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
