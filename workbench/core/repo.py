import json
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone

from . import db, paths
from .config import app_config, artifact_map, field_map, product_config, field_config

FIELD_STATUS_LABELS = {
    'extracted': '已提取',
    'pending_confirm': '待确认',
    'required_missing': '必须补填',
    'optional_missing': '可选缺失',
    'confirmed': '已人工确认',
}

PROJECT_STATUS_LABELS = {
    'draft': '草稿',
    'researching': '调研处理中',
    'pending_fill': '待补充',
    'pending_generate': '待生成',
    'completed': '已完成',
}

PROJECT_CATEGORIES = ['内部项目', '外部项目', '重点项目']

AUDIO_JOB_STATUS_LABELS = {
    'pending': '等待中',
    'uploading': '上传中',
    'submitted': '已提交',
    'transcribing': '转写中',
    'awaiting_preview': '待预览',
    'approved': '已确认',
    'failed': '失败',
}

# 未终结的转写任务状态。页面加载时据此尝试恢复后台线程，避免长任务因用户
# 离开页面或应用重启而永久停在中间状态。
AUDIO_JOB_ACTIVE_STATUSES = ('pending', 'uploading', 'submitted', 'transcribing')


def audio_status_tag(status):
    """转写任务状态对应的标签配色。"""
    if status in ('awaiting_preview', 'approved'):
        return 'green'
    return 'red' if status == 'failed' else 'blue'


def new_id(prefix):
    return '%s_%s' % (prefix, uuid.uuid4().hex[:12])


def now_iso():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def stamp_compact():
    return datetime.now().strftime('%Y%m%d_%H%M')


# ---------------------------------------------------------------- 项目

def create_project(dept_key, data, creator=None, visibility=None):
    visibility = visibility if visibility is not None else ('private' if creator else 'department')
    if visibility not in ('private', 'department'):
        raise ValueError('请选择有效的项目可见范围。')
    if visibility == 'private' and not creator:
        raise ValueError('请使用个人账号创建私密项目。')
    pid = new_id('prj')
    ts = now_iso()
    db.execute(
        """INSERT INTO projects (id, dept_key, category, name, customer_name, location,
           owner, contact, status, created_at, updated_at, creator_user_id, creator_name, visibility)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (pid, dept_key, data.get('category', '外部项目'), data.get('name', '未命名项目'),
         data.get('customer_name', ''), data.get('location', ''), data.get('owner', ''),
         data.get('contact', ''), 'draft', ts, ts,
         creator['id'] if creator else None, creator['display_name'] if creator else '',
         visibility))
    return get_project(pid)


def update_project(pid, data):
    ts = now_iso()
    db.execute(
        """UPDATE projects SET category=?, name=?, customer_name=?, location=?, owner=?,
           contact=?, updated_at=? WHERE id=?""",
        (data.get('category', '外部项目'), data.get('name', '未命名项目'),
         data.get('customer_name', ''), data.get('location', ''), data.get('owner', ''),
         data.get('contact', ''), ts, pid))
    return get_project(pid)


def set_project_status(pid, status):
    db.execute('UPDATE projects SET status=?, updated_at=? WHERE id=?', (status, now_iso(), pid))


def touch_project(pid):
    db.execute('UPDATE projects SET updated_at=? WHERE id=?', (now_iso(), pid))


def get_project(pid):
    return db.query_one('SELECT * FROM projects WHERE id=?', (pid,))


def list_projects(dept_key, keyword='', archived=None, include_deleted=False, user=None):
    sql = 'SELECT * FROM projects WHERE 1=1'
    args = []
    if not (user and user['active'] and user['role'] == 'admin'):
        sql += " AND ((visibility='department' AND dept_key=?)"
        args.append(dept_key)
        if user and user['active']:
            sql += " OR (visibility='private' AND creator_user_id=?)"
            args.append(user['id'])
        sql += ')'
    if not include_deleted:
        sql += ' AND deleted=0'
    if archived is None:
        sql += ' AND archived=0'
    else:
        sql += ' AND archived=?'
        args.append(1 if archived else 0)
    if keyword:
        sql += ' AND (name LIKE ? OR customer_name LIKE ?)'
        like = '%%%s%%' % keyword
        args.extend([like, like])
    sql += ' ORDER BY updated_at DESC'
    return db.query(sql, args)


def archive_project(pid, archived=True):
    db.execute('UPDATE projects SET archived=?, updated_at=? WHERE id=?',
               (1 if archived else 0, now_iso(), pid))


def soft_delete_project(pid):
    db.execute('UPDATE projects SET deleted=1, updated_at=? WHERE id=?', (now_iso(), pid))


def restore_project(pid):
    db.execute('UPDATE projects SET deleted=0, updated_at=? WHERE id=?', (now_iso(), pid))


def list_deleted(dept_key, user=None):
    # 同样复用列表权限；回收站不能暴露他人私密项目。
    rows = list_projects(dept_key, include_deleted=True, user=user)
    rows += list_projects(dept_key, archived=True, include_deleted=True, user=user)
    return sorted((p for p in rows if p['deleted']), key=lambda p: p['updated_at'], reverse=True)


def purge_project(pid):
    """彻底删除项目及其全部关联数据与产出文件。"""
    for pp in list_products(pid):
        pp_dir = os.path.join(paths.OUTPUT_DIR, pp['id'])
        if os.path.isdir(pp_dir):
            shutil.rmtree(pp_dir, ignore_errors=True)
        # 项目级转写上线前，录音按产品目录存放。两条路径都要清理，否则用户
        # 执行"彻底删除"后客户原始录音仍留在磁盘上。
        legacy_upload_dir = os.path.join(paths.UPLOAD_DIR, pp['id'])
        if os.path.isdir(legacy_upload_dir):
            shutil.rmtree(legacy_upload_dir, ignore_errors=True)
        db.execute('DELETE FROM survey_field_values WHERE project_product_id=?', (pp['id'],))
        db.execute('DELETE FROM survey_sources WHERE project_product_id=?', (pp['id'],))
        db.execute('DELETE FROM extraction_runs WHERE project_product_id=?', (pp['id'],))
        db.execute('DELETE FROM validation_results WHERE project_product_id=?', (pp['id'],))
        db.execute('DELETE FROM artifact_runs WHERE project_product_id=?', (pp['id'],))
        db.execute('DELETE FROM export_packages WHERE project_product_id=?', (pp['id'],))
        db.execute('DELETE FROM project_products WHERE id=?', (pp['id'],))
    upload_dir = os.path.join(paths.UPLOAD_DIR, pid)
    if os.path.isdir(upload_dir):
        shutil.rmtree(upload_dir, ignore_errors=True)
    db.execute('DELETE FROM audio_transcription_jobs WHERE project_id=?', (pid,))
    db.execute('DELETE FROM project_transcripts WHERE project_id=?', (pid,))
    for table in ('project_field_values', 'project_field_conflicts', 'project_field_history', 'project_field_revisions'):
        db.execute('DELETE FROM %s WHERE project_id=?' % table, (pid,))
    db.execute('DELETE FROM projects WHERE id=?', (pid,))


def copy_project(pid, creator=None):
    """复制项目：基础信息 + 已选产品 + 结构化内容。原文字稿与生成结果按待确认策略默认复制原文、不复制产物。"""
    src = get_project(pid)
    if not src:
        return None
    if src['visibility'] == 'private' and creator is None:
        raise ValueError('复制私密项目必须提供当前创建者。')
    new = create_project(creator['dept_key'] if creator else src['dept_key'], {
        'category': src['category'],
        'name': src['name'] + '（副本）',
        'customer_name': src['customer_name'],
        'location': src['location'],
        'owner': src['owner'],
        'contact': src['contact'],
    }, creator=creator)
    db.execute('UPDATE projects SET copied_from=? WHERE id=?', (pid, new['id']))
    for pp in list_products(pid):
        npp = add_product(new['id'], pp['product_type'])
        src_row = latest_source(pp['id'])
        if src_row:
            save_source(npp['id'], src_row['content'])
        for fv in field_values(pp['id']).values():
            if 'project_id' in fv:
                continue
            if field_map(pp['product_type'])[fv['field_key']]['type'] == 'attachment':
                # 副本使用独立私有目录，删除原项目附件不影响副本。
                from pathlib import Path
                import shutil
                import re
                from . import paths
                copied = []
                for item in json.loads(fv['value_json']) or []:
                    if not re.fullmatch(r'[0-9a-f]{32}', str(item.get('id', ''))):
                        continue
                    base = Path(paths.UPLOAD_DIR) / 'field_attachments'
                    source = base / pp['id'] / fv['field_key'] / item['id']
                    if source.is_file():
                        target = base / npp['id'] / fv['field_key'] / item['id']
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, target)
                        copied.append(item)
                fv = dict(fv, value_json=json.dumps(copied, ensure_ascii=False))
            upsert_field_value(npp['id'], fv['field_key'], fv['value_json'], fv['status'],
                               fv['confidence'], fv['source_quote'], fv['updated_by'],
                               fv['template_version'], fv['protected'], fv['note'])
    # 完整复制公共资料，包括当前没有产品显示的字段与待确认冲突。副本之后独立修改。
    for fv in db.query('SELECT * FROM project_field_values WHERE project_id=?', (pid,)):
        columns = list(fv)
        fv.update(id=new_id('pf'), project_id=new['id'], source_product_id='')
        db.execute('INSERT INTO project_field_values (%s) VALUES (%s)' %
                   (','.join(columns), ','.join('?' for _ in columns)), tuple(fv[k] for k in columns))
    for conflict in db.query('SELECT * FROM project_field_conflicts WHERE project_id=?', (pid,)):
        db.execute('''INSERT INTO project_field_conflicts
            (project_id, field_key, candidates_json, resolved_at, resolved_value_json) VALUES (?,?,?,?,?)''',
            (new['id'], conflict['field_key'], conflict['candidates_json'],
             conflict['resolved_at'], conflict['resolved_value_json']))
    revision = project_fields_revision(pid)
    if revision:
        db.execute('INSERT INTO project_field_revisions (project_id, revision) VALUES (?,?)', (new['id'], revision))
    return new


# ---------------------------------------------------------------- 产品实例

def add_product(project_id, product_type):
    cfg = product_config(product_type)
    pid = new_id('pp')
    ts = now_iso()
    db.execute(
        """INSERT INTO project_products (id, project_id, product_type, product_version,
           template_version, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)""",
        (pid, project_id, product_type, cfg['version'],
         field_config(product_type)['template_version'], 'draft', ts, ts))
    touch_project(project_id)
    # Only explicitly configured system values are initialized. Protect the first
    # entry time from transcript parsing so a later deadline cannot replace it.
    for field in field_config(product_type)['fields']:
        initial = field.get('initial_value') or {}
        if initial.get('source') == 'current_datetime':
            zone = timezone(timedelta(minutes=initial.get('utc_offset_minutes', 0)))
            value = datetime.now(zone).strftime('%Y-%m-%d %H:%M:%S')
            upsert_field_value(pid, field['key'], json.dumps(value, ensure_ascii=False),
                               'extracted', 'high', '新增产品记录时由系统自动填写',
                               'system', field_config(product_type)['template_version'], 1)
    return db.query_one('SELECT * FROM project_products WHERE id=?', (pid,))


def list_products(project_id):
    return db.query('SELECT * FROM project_products WHERE project_id=? ORDER BY created_at',
                    (project_id,))


def get_pp(pp_id):
    return db.query_one('SELECT * FROM project_products WHERE id=?', (pp_id,))


def set_pp_status(pp_id, status):
    db.execute('UPDATE project_products SET status=?, updated_at=? WHERE id=?',
               (status, now_iso(), pp_id))


def get_pp_settings(pp_id):
    row = get_pp(pp_id)
    try:
        return json.loads(row['settings'] or '{}') if row else {}
    except Exception:
        return {}


def set_pp_settings(pp_id, settings):
    db.execute('UPDATE project_products SET settings=?, updated_at=? WHERE id=?',
               (json.dumps(settings, ensure_ascii=False), now_iso(), pp_id))


def ensure_product(project_id, product_type):
    rows = db.query('SELECT * FROM project_products WHERE project_id=? AND product_type=?',
                    (project_id, product_type))
    return rows[0] if rows else add_product(project_id, product_type)


# ---------------------------------------------------------------- 调研原文

def save_source(pp_id, content, kind='transcript'):
    sid = new_id('src')
    db.execute(
        """INSERT INTO survey_sources (id, project_product_id, kind, content, char_count, created_at)
           VALUES (?,?,?,?,?,?)""",
        (sid, pp_id, kind, content, len(content or ''), now_iso()))
    return sid


def latest_source(pp_id):
    return db.query_one(
        'SELECT * FROM survey_sources WHERE project_product_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1',
        (pp_id,))


def all_sources(pp_id):
    return db.query('SELECT * FROM survey_sources WHERE project_product_id=? ORDER BY created_at',
                    (pp_id,))


# ---------------------------------------------------------------- 录音转写任务

def create_audio_job(project_id, original_name, local_path, file_size, provider='tencent'):
    """登记一个项目级转写任务。

    任务归属项目而非产品：同一段踏勘录音常被多个产品共用，按产品建任务会导致
    重复上传和重复计费。project_product_id 仍写入空串而不是省略，因为历史库中
    该列为 NOT NULL 且无默认值。
    """
    jid = new_id('asr')
    ts = now_iso()
    db.execute(
        """INSERT INTO audio_transcription_jobs
           (id, project_id, project_product_id, original_name, local_path, file_size, provider,
            status, status_message, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (jid, project_id, '', original_name, local_path, file_size, provider,
         'pending', '等待开始转写', ts, ts))
    return get_audio_job(jid)


def get_audio_job(job_id):
    return db.query_one('SELECT * FROM audio_transcription_jobs WHERE id=?', (job_id,))


def list_audio_jobs(project_id):
    return db.query(
        'SELECT * FROM audio_transcription_jobs WHERE project_id=? '
        'ORDER BY created_at DESC', (project_id,))


def update_audio_job(job_id, **values):
    """仅允许更新任务白名单字段，避免动态 SQL 接受外部列名。"""
    allowed = {
        'status', 'status_message', 'provider_job_id', 'object_key',
        'raw_transcript', 'corrected_transcript', 'segments_json',
        'corrections_json', 'audio_duration', 'error', 'completed_at',
        'approved_at',
    }
    clean = {k: v for k, v in values.items() if k in allowed}
    if not clean:
        return get_audio_job(job_id)
    clean['updated_at'] = now_iso()
    cols = ', '.join('%s=?' % key for key in clean)
    db.execute('UPDATE audio_transcription_jobs SET %s WHERE id=?' % cols,
               tuple(clean.values()) + (job_id,))
    return get_audio_job(job_id)


# ---------------------------------------------------------------- 项目级共享转写稿

def save_project_transcript(project_id, audio_job_id, content):
    """保存一份确认后的项目级转写稿。

    与 survey_sources 分开存放：这里是项目下所有产品的共同输入源，产品导入时
    才复制进各自的 survey_sources，之后产品内的修改不影响本项目稿。
    """
    tid = new_id('pt')
    db.execute(
        """INSERT INTO project_transcripts (id, project_id, audio_job_id, content,
           char_count, created_at) VALUES (?,?,?,?,?,?)""",
        (tid, project_id, audio_job_id or '', content, len(content or ''), now_iso()))
    touch_project(project_id)
    return tid


def latest_project_transcript(project_id):
    return db.query_one(
        'SELECT * FROM project_transcripts WHERE project_id=? '
        'ORDER BY created_at DESC, rowid DESC LIMIT 1', (project_id,))


def all_project_transcripts(project_id):
    return db.query('SELECT * FROM project_transcripts WHERE project_id=? ORDER BY created_at',
                    (project_id,))


# ---------------------------------------------------------------- 字段值

def field_values(pp_id):
    """产品字段与项目公共答案合并；公共字段不回退到迁移前的旧副本。"""
    pp = get_pp(pp_id)
    if not pp:
        return {}
    fmap = field_map(pp['product_type'])
    shared = {k for k, f in fmap.items() if f.get('value_scope') == 'project'}
    rows = db.query('SELECT * FROM survey_field_values WHERE project_product_id=?', (pp_id,))
    values = {r['field_key']: r for r in rows if r['field_key'] in fmap and r['field_key'] not in shared}
    rows = db.query('SELECT * FROM project_field_values WHERE project_id=?', (pp['project_id'],))
    values.update({r['field_key']: r for r in rows if r['field_key'] in shared})
    return values


def project_fields_revision(project_id):
    row = db.query_one('SELECT revision FROM project_field_revisions WHERE project_id=?', (project_id,))
    return row['revision'] if row else 0


def shared_field_conflicts(pp_id):
    pp = get_pp(pp_id)
    fmap = field_map(pp['product_type'])
    rows = db.query('''SELECT * FROM project_field_conflicts
        WHERE project_id=? AND resolved_at IS NULL''', (pp['project_id'],))
    return {r['field_key']: json.loads(r['candidates_json']) for r in rows
            if r['field_key'] in fmap and fmap[r['field_key']].get('value_scope') == 'project'}


def _upsert_project_field(pp, field_key, value_json, status, confidence, source_quote,
                          updated_by, template_version, protected, note):
    # BEGIN IMMEDIATE 把人工确认保护检查与 AI 写回放在同一事务，避免跨管家竞态。
    conn = db.init_db()
    try:
        conn.execute('BEGIN IMMEDIATE')
        pid, ts = pp['project_id'], now_iso()
        row = conn.execute('SELECT * FROM project_field_values WHERE project_id=? AND field_key=?',
                           (pid, field_key)).fetchone()
        conflict = conn.execute('''SELECT 1 FROM project_field_conflicts
            WHERE project_id=? AND field_key=? AND resolved_at IS NULL''', (pid, field_key)).fetchone()
        if updated_by == 'ai' and ((row and (row['protected'] or not _value_is_empty(row['value_json']))) or conflict):
            conn.rollback()
            return False
        # 冲突必须明确选择一个非空答案；清空输入不能让 AI 接管未解决的冲突。
        if conflict and _value_is_empty(value_json):
            conn.rollback()
            return False
        attrs = ('value_json', 'status', 'confidence', 'source_quote', 'updated_by',
                 'template_version', 'protected', 'note')
        args = (value_json, status, confidence, source_quote, updated_by, template_version, protected, note)
        if row and not conflict and all(row[k] == v for k, v in zip(attrs, args)):
            conn.rollback()
            return False
        conn.execute('''INSERT INTO project_field_values
            (id, project_id, field_key, value_json, status, confidence, source_quote,
             updated_by, template_version, protected, note, updated_at, source_product_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(project_id, field_key) DO UPDATE SET
            value_json=excluded.value_json, status=excluded.status, confidence=excluded.confidence,
            source_quote=excluded.source_quote, updated_by=excluded.updated_by,
            template_version=excluded.template_version, protected=excluded.protected,
            note=excluded.note, updated_at=excluded.updated_at, source_product_id=excluded.source_product_id''',
            (new_id('pf'), pid, field_key, *args, ts, pp['id']))
        record = dict(zip(attrs, args), source_product_id=pp['id'])
        conn.execute('''INSERT INTO project_field_history
            (id, project_id, field_key, record_json, created_at) VALUES (?,?,?,?,?)''',
            (new_id('ph'), pid, field_key, json.dumps(record, ensure_ascii=False), ts))
        if conflict and updated_by == 'human':
            conn.execute('''UPDATE project_field_conflicts SET resolved_at=?, resolved_value_json=?
                WHERE project_id=? AND field_key=?''', (ts, value_json, pid, field_key))
        conn.execute('''INSERT INTO project_field_revisions (project_id, revision) VALUES (?,1)
            ON CONFLICT(project_id) DO UPDATE SET revision=revision+1''', (pid,))
        conn.execute('''DELETE FROM validation_results WHERE project_product_id IN
            (SELECT id FROM project_products WHERE project_id=?)''', (pid,))
        conn.execute('UPDATE project_products SET updated_at=? WHERE project_id=?', (ts, pid))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def upsert_field_value(pp_id, field_key, value_json, status, confidence, source_quote,
                       updated_by, template_version, protected=0, note=''):
    pp = get_pp(pp_id)
    field = field_map(pp['product_type']).get(field_key) if pp else None
    if field and field.get('value_scope') == 'project':
        return _upsert_project_field(pp, field_key, value_json, status, confidence,
                                     source_quote, updated_by, template_version, protected, note)
    exist = db.query_one(
        'SELECT id FROM survey_field_values WHERE project_product_id=? AND field_key=?',
        (pp_id, field_key))
    ts = now_iso()
    if exist:
        db.execute(
            """UPDATE survey_field_values SET value_json=?, status=?, confidence=?, source_quote=?,
               updated_by=?, template_version=?, protected=?, note=?, updated_at=? WHERE id=?""",
            (value_json, status, confidence, source_quote, updated_by, template_version,
             protected, note, ts, exist['id']))
    else:
        db.execute(
            """INSERT INTO survey_field_values (id, project_product_id, field_key, value_json, status,
               confidence, source_quote, updated_by, template_version, protected, note, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (new_id('fv'), pp_id, field_key, value_json, status, confidence, source_quote,
             updated_by, template_version, protected, note, ts))


def bulk_upsert_field_values(pp_id, items):
    for it in items:
        upsert_field_value(pp_id, **it)


def delete_field_values(pp_id):
    """清理产品专属答案；项目公共资料的生命周期属于项目。"""
    db.execute('DELETE FROM survey_field_values WHERE project_product_id=?', (pp_id,))


# ---------------------------------------------------------------- 提取运行

def start_extraction_run(pp_id, provider, template_version, input_chars):
    rid = new_id('er')
    db.execute(
        """INSERT INTO extraction_runs (id, project_product_id, provider, template_version,
           input_chars, status, started_at) VALUES (?,?,?,?,?,?,?)""",
        (rid, pp_id, provider, template_version, input_chars, 'running', now_iso()))
    return rid


def finish_extraction_run(rid, status, stats, message=''):
    db.execute(
        'UPDATE extraction_runs SET status=?, stats_json=?, message=?, finished_at=? WHERE id=?',
        (status, json.dumps(stats, ensure_ascii=False), message, now_iso(), rid))


def latest_extraction_run(pp_id):
    return db.query_one(
        'SELECT * FROM extraction_runs WHERE project_product_id=? ORDER BY started_at DESC LIMIT 1',
        (pp_id,))


# ---------------------------------------------------------------- 校验

def save_validation(pp_id, ok, issues, shared_revision=None):
    revision = (project_fields_revision(get_pp(pp_id)['project_id'])
                if shared_revision is None else shared_revision)
    db.execute('DELETE FROM validation_results WHERE project_product_id=?', (pp_id,))
    vid = new_id('vr')
    db.execute(
        '''INSERT INTO validation_results (id, project_product_id, ok, issues_json, ran_at,
           project_fields_revision) VALUES (?,?,?,?,?,?)''',
        (vid, pp_id, 1 if ok else 0, json.dumps(issues, ensure_ascii=False), now_iso(), revision))
    return vid


def latest_validation(pp_id):
    row = db.query_one(
        'SELECT * FROM validation_results WHERE project_product_id=? ORDER BY ran_at DESC, rowid DESC LIMIT 1',
        (pp_id,))
    revision = project_fields_revision(get_pp(pp_id)['project_id'])
    return row if row and row['project_fields_revision'] == revision else None


# ---------------------------------------------------------------- 生成物

def upsert_artifact_run(pp_id, artifact_key, status, files=None, template_version='',
                        error='', started=True, shared_revision=None):
    """新增或更新一次生成结果。

    记录只保存通用模板版本，不再携带具体业务模块的版本字段；这样删除某项
    能力时，生成物仓储接口不需要跟着变化。
    """
    exist = db.query_one(
        'SELECT id FROM artifact_runs WHERE project_product_id=? AND artifact_key=?',
        (pp_id, artifact_key))
    ts = now_iso()
    revision = (project_fields_revision(get_pp(pp_id)['project_id'])
                if shared_revision is None else shared_revision)
    if exist:
        sql = 'UPDATE artifact_runs SET status=?, error=?, finished_at=?, project_fields_revision=?'
        args = [status, error, ts, revision]
        if files is not None:
            sql += ', files_json=?'
            args.append(json.dumps(files, ensure_ascii=False))
        if template_version:
            sql += ', template_version=?'
            args.append(template_version)
        sql += ' WHERE id=?'
        args.append(exist['id'])
        db.execute(sql, args)
    else:
        db.execute(
            """INSERT INTO artifact_runs (id, project_product_id, artifact_key, status, files_json,
               template_version, error, started_at, finished_at, project_fields_revision)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (new_id('ar'), pp_id, artifact_key, status,
             json.dumps(files or [], ensure_ascii=False), template_version, error, ts, ts, revision))


def artifact_runs(pp_id):
    rows = db.query('SELECT * FROM artifact_runs WHERE project_product_id=?', (pp_id,))
    revision = project_fields_revision(get_pp(pp_id)['project_id'])
    for row in rows:
        if row['status'] == 'success' and row['project_fields_revision'] != revision:
            row['status'] = 'stale'
    return rows


def get_artifact_run(pp_id, key):
    return next((r for r in artifact_runs(pp_id) if r['artifact_key'] == key), None)


def reset_artifact_run(pp_id, key):
    db.execute('DELETE FROM artifact_runs WHERE project_product_id=? AND artifact_key=?',
               (pp_id, key))


# ---------------------------------------------------------------- 导出包

def save_export_package(pp_id, file_name, file_path, manifest):
    size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
    eid = new_id('ep')
    db.execute(
        """INSERT INTO export_packages (id, project_product_id, file_name, file_path, size,
           manifest_json, created_at) VALUES (?,?,?,?,?,?,?)""",
        (eid, pp_id, file_name, file_path, size,
         json.dumps(manifest, ensure_ascii=False), now_iso()))
    return eid


def latest_export(pp_id):
    return db.query_one(
        'SELECT * FROM export_packages WHERE project_product_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1',
        (pp_id,))


# ---------------------------------------------------------------- 派生状态

def field_status(row, field):
    """Use current requiredness for empty historical answers without rewriting them."""
    from .field_conditions import needs_reconfirmation
    if needs_reconfirmation(row, field):
        return 'pending_confirm'
    if row and row['status'] == 'pending_confirm' and row['protected']:
        return 'pending_confirm'
    if not row or _value_is_empty(row['value_json']):
        return 'required_missing' if field.get('required') else 'optional_missing'
    return row['status']


def field_stats(pp_id, product_type):
    """统计字段状态，用于进度与提示。"""
    fmap = field_map(product_type)
    values = field_values(pp_id)
    stats = {'total': len(fmap), 'required_total': 0, 'required_done': 0,
             'pending_confirm': 0, 'required_missing': 0, 'filled': 0}
    for key, f in fmap.items():
        from .field_conditions import required_now, needs_reconfirmation
        f = dict(f, required=required_now(f, values))
        row = values.get(key)
        filled = bool(row and _value_is_empty(row['value_json']) is False)
        if filled:
            stats['filled'] += 1
        if f.get('required'):
            stats['required_total'] += 1
            if filled and not needs_reconfirmation(row, f):
                stats['required_done'] += 1
        st = field_status(row, f)
        if st == 'pending_confirm':
            stats['pending_confirm'] += 1
        elif st == 'required_missing':
            stats['required_missing'] += 1
    return stats


def _value_is_empty(value_json):
    try:
        v = json.loads(value_json)
    except Exception:
        return True
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, list):
        return len(v) == 0
    if isinstance(v, dict):
        return len(v) == 0
    return False


def progress_of(pp_id, product_type):
    """返回 (百分比, 状态key)。与需求 6.1.3 状态定义对应。"""
    src = latest_source(pp_id)
    run = latest_extraction_run(pp_id)
    val = latest_validation(pp_id)
    current_artifacts = set(artifact_map(product_type))
    # 已下线模块的历史记录继续保留用于审计，但不能让当前项目状态误判为完成。
    arts = [a for a in artifact_runs(pp_id) if a['artifact_key'] in current_artifacts]
    done = [a for a in arts if a['status'] == 'success']

    if done:
        return 100, 'completed'
    if val and val['ok']:
        return 80, 'pending_generate'
    st = field_stats(pp_id, product_type)
    if (run and run['status'] == 'success') or st['filled']:
        if st['required_done'] >= st['required_total']:
            return 65, 'pending_generate'
        return 50, 'pending_fill'
    if src:
        return 30, 'researching'
    return 10, 'draft'


def project_progress(project_id):
    """项目级进度取所有产品的平均。"""
    pps = list_products(project_id)
    if not pps:
        return 0, 'draft'
    total = 0
    statuses = []
    for pp in pps:
        pct, st = progress_of(pp['id'], pp['product_type'])
        total += pct
        statuses.append(st)
    avg = int(total / len(pps))
    # 项目状态取"最靠后完成"的那个，避免已完成被草稿拉低
    order = ['draft', 'researching', 'pending_fill', 'pending_generate', 'completed']
    worst = min(statuses, key=lambda s: order.index(s))
    return avg, worst


def decorate_project(row):
    row = dict(row)
    pct, st = project_progress(row['id'])
    row['progress'] = pct
    row['derived_status'] = st
    row['status_label'] = PROJECT_STATUS_LABELS.get(st, '草稿')
    row['products'] = list_products(row['id'])
    return row
