import os
import sqlite3
import threading
from . import paths
from .config import app_config
from .migrations import apply_migrations

_local = threading.local()

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS projects (
    id              TEXT PRIMARY KEY,
    dept_key        TEXT NOT NULL,
    category        TEXT NOT NULL,
    name            TEXT NOT NULL,
    customer_name   TEXT NOT NULL DEFAULT '',
    location        TEXT NOT NULL DEFAULT '',
    owner           TEXT NOT NULL DEFAULT '',
    contact         TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'draft',
    archived        INTEGER NOT NULL DEFAULT 0,
    deleted         INTEGER NOT NULL DEFAULT 0,
    copied_from     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projects_dept ON projects(dept_key, deleted, archived);

CREATE TABLE IF NOT EXISTS project_products (
    id               TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL,
    product_type     TEXT NOT NULL,
    product_version  TEXT NOT NULL,
    template_version TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'draft',
    settings         TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pp_project ON project_products(project_id);

CREATE TABLE IF NOT EXISTS survey_sources (
    id                  TEXT PRIMARY KEY,
    project_product_id  TEXT NOT NULL,
    kind                TEXT NOT NULL DEFAULT 'transcript',
    content             TEXT NOT NULL DEFAULT '',
    char_count          INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_src_pp ON survey_sources(project_product_id);

-- project_product_id 是 v3 之前的归属列，现仅用于历史审计。保留而不删除，
-- 是因为老任务需要能回答"当时是在哪个产品下转写的"，且 SQLite 删列会连带
-- 丢失该审计信息。新任务一律写空串，归属只看 project_id。
CREATE TABLE IF NOT EXISTS audio_transcription_jobs (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL DEFAULT '',
    project_product_id  TEXT NOT NULL DEFAULT '',
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
);
-- idx_audio_jobs_project 刻意不在这里声明：init_db 先跑 SCHEMA 再跑迁移，历史库
-- 中 audio_transcription_jobs 还没有 project_id 列，在此建索引会让升级直接失败。
-- 该索引由迁移 v3 在补列之后创建，新库走 init_db 同样能拿到。
CREATE INDEX IF NOT EXISTS idx_audio_jobs_pp
    ON audio_transcription_jobs(project_product_id, created_at);

-- 项目级共享转写稿：录音确认后的正式文本，供项目下所有产品导入使用。
CREATE TABLE IF NOT EXISTS project_transcripts (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL,
    audio_job_id        TEXT NOT NULL DEFAULT '',
    content             TEXT NOT NULL DEFAULT '',
    char_count          INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_transcripts
    ON project_transcripts(project_id, created_at);

CREATE TABLE IF NOT EXISTS survey_field_values (
    id                  TEXT PRIMARY KEY,
    project_product_id  TEXT NOT NULL,
    field_key           TEXT NOT NULL,
    value_json          TEXT NOT NULL DEFAULT '""',
    status              TEXT NOT NULL DEFAULT 'optional_missing',
    confidence          TEXT,
    source_quote        TEXT NOT NULL DEFAULT '',
    updated_by          TEXT NOT NULL DEFAULT 'ai',
    template_version    TEXT NOT NULL DEFAULT '',
    protected           INTEGER NOT NULL DEFAULT 0,
    note                TEXT NOT NULL DEFAULT '',
    updated_at          TEXT NOT NULL,
    UNIQUE(project_product_id, field_key)
);
CREATE INDEX IF NOT EXISTS idx_fv_pp ON survey_field_values(project_product_id);

CREATE TABLE IF NOT EXISTS extraction_runs (
    id                  TEXT PRIMARY KEY,
    project_product_id  TEXT NOT NULL,
    provider            TEXT NOT NULL,
    template_version    TEXT NOT NULL,
    input_chars         INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'running',
    stats_json          TEXT NOT NULL DEFAULT '{}',
    message             TEXT NOT NULL DEFAULT '',
    started_at          TEXT NOT NULL,
    finished_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_er_pp ON extraction_runs(project_product_id);

CREATE TABLE IF NOT EXISTS validation_results (
    id                  TEXT PRIMARY KEY,
    project_product_id  TEXT NOT NULL,
    ok                  INTEGER NOT NULL DEFAULT 0,
    issues_json         TEXT NOT NULL DEFAULT '[]',
    ran_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vr_pp ON validation_results(project_product_id);

CREATE TABLE IF NOT EXISTS artifact_runs (
    id                  TEXT PRIMARY KEY,
    project_product_id  TEXT NOT NULL,
    artifact_key        TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    files_json          TEXT NOT NULL DEFAULT '[]',
    template_version    TEXT NOT NULL DEFAULT '',
    error               TEXT NOT NULL DEFAULT '',
    started_at          TEXT,
    finished_at         TEXT,
    UNIQUE(project_product_id, artifact_key)
);
CREATE INDEX IF NOT EXISTS idx_ar_pp ON artifact_runs(project_product_id);

CREATE TABLE IF NOT EXISTS export_packages (
    id                  TEXT PRIMARY KEY,
    project_product_id  TEXT NOT NULL,
    file_name           TEXT NOT NULL,
    file_path           TEXT NOT NULL,
    size                INTEGER NOT NULL DEFAULT 0,
    manifest_json       TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ep_pp ON export_packages(project_product_id);
"""


def db_path():
    rel = app_config()['storage']['db_file']
    return os.path.join(paths.ROOT, rel)


def connect():
    conn = getattr(_local, 'conn', None)
    if conn is None:
        conn = sqlite3.connect(db_path(), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        _local.conn = conn
    return conn


def init_db():
    """创建新库并升级历史库。

    基础 schema 负责“今天新建数据库”的形态，迁移器负责已有数据库。两者
    必须同时执行，否则开发环境正常而用户历史库会在升级时失败。
    """
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()
    apply_migrations(conn)
    return conn


def query(sql, args=()):
    conn = init_db()
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def query_one(sql, args=()):
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql, args=()):
    conn = init_db()
    cur = conn.execute(sql, args)
    conn.commit()
    return cur


def executemany(sql, seq):
    conn = init_db()
    cur = conn.executemany(sql, seq)
    conn.commit()
    return cur
