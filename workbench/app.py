#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""灵石解决方案工作台 · Demo 入口

启动： python app.py
依赖： pip install flask
"""
import os
import sys

from flask import (Flask, abort, flash, jsonify, make_response, redirect,
                   render_template, request, send_file, session, url_for)
from werkzeug.utils import safe_join

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import db, departments, extract, generate, repo, runtime  # noqa: E402
from core.config import (app_config, artifact_map, fields_by_group, field_config,  # noqa: E402
                         list_products, product_config)
from core.capabilities import has_capability  # noqa: E402
from core.paths import ROOT, STATIC_DIR  # noqa: E402
from core.validate import validate  # noqa: E402
from core.choices import choice_state  # noqa: E402
from services import artifacts as artifact_service  # noqa: E402
from services import survey as survey_service  # noqa: E402
from services import audio as audio_service  # noqa: E402

CFG = app_config()

app = Flask(__name__, template_folder='templates', static_folder='static')
app.jinja_env.globals['choice_state'] = choice_state
app.secret_key = runtime.resolve_secret_key(CFG)
app.config['JSON_AS_ASCII'] = False
# 会话 Cookie 加固：HttpOnly 防脚本窃取，SameSite 防 CSRF。
# Secure 由 LS_COOKIE_SECURE 开关控制（默认关）——只有 HTTPS 真正上线后才开，
# 否则在明文 HTTP（本地或备案前的 IP 访问）下会导致登录后立即掉线。
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = runtime.cookie_secure()

# 放在密钥校验之后：生产环境缺密钥时应直接拒绝启动，不留任何落盘副作用。
departments.ensure_initialized()
# 在导入期而非 main() 里告警：生产走 wsgi.py + waitress，main() 不会被执行。
runtime.warn_insecure_departments(departments.unchanged_names())


# ---------------------------------------------------------------- 基础工具

def current_dept():
    key = session.get('dept_key')
    if not key:
        return None
    return departments.find(key)


def require_dept():
    if not current_dept():
        return redirect(url_for('login'))
    return None


def flash_msg(msg, kind='ok'):
    session['_flash'] = {'msg': msg, 'kind': kind}


def pop_flash():
    return session.pop('_flash', None)


def load_pp(pp_id):
    """加载产品实例，并校验其所属分部与当前会话一致。"""
    pp = repo.get_pp(pp_id)
    if not pp:
        abort(404)
    project = repo.get_project(pp['project_id'])
    if not project or project['dept_key'] != session.get('dept_key'):
        abort(403)
    return pp, project


def load_project(pid):
    """加载当前分部可访问的项目。

    所有项目写操作都必须经过同一个租户边界检查；只在列表页过滤分部并不能
    防止用户手工构造其它分部项目的 URL。
    """
    project = repo.get_project(pid)
    if not project:
        abort(404)
    if project['dept_key'] != session.get('dept_key'):
        abort(403)
    return project


def load_audio_job(pid, job_id):
    """转写任务访问受项目与分部边界约束。

    任务归属项目而不是产品，因此这里不再经过 load_pp；分部检查由 load_project
    完成，足以阻止跨分部读取他人项目的录音与转写结果。
    """
    project = load_project(pid)
    job = repo.get_audio_job(job_id)
    if not job or job['project_id'] != pid:
        abort(404)
    return project, job


app.jinja_env.globals['product_name'] = lambda t: product_config(t)['name']
app.jinja_env.globals['has_capability'] = has_capability
app.jinja_env.globals['audio_status_label'] = lambda s: repo.AUDIO_JOB_STATUS_LABELS.get(s, s)
app.jinja_env.globals['audio_status_tag'] = repo.audio_status_tag


@app.template_filter('hit_count')
def hit_count(stats_json):
    try:
        return repo.json.loads(stats_json or '{}').get('hit_fields', 0)
    except Exception:
        return 0


@app.template_filter('fromjson')
def fromjson_filter(s):
    try:
        return repo.json.loads(s or 'null')
    except Exception:
        return None


@app.template_filter('jval')
def jval_filter(s):
    """把字段的 JSON 存储值渲染为标量文本。"""
    try:
        v = repo.json.loads(s or 'null')
    except Exception:
        return ''
    return '' if (v is None or isinstance(v, (list, dict))) else v


@app.template_filter('tag_class')
def tag_class(status):
    return {'extracted': 'blue', 'confirmed': 'green', 'pending_confirm': 'orange',
            'required_missing': 'red', 'optional_missing': ''}.get(status, '')


@app.template_filter('ms_time')
def ms_time(value):
    total = max(0, int(value or 0))
    seconds, millis = divmod(total, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return '%02d:%02d:%02d.%03d' % (hours, minutes, seconds, millis)


# 同一个函数在页面里既当过滤器也当全局函数用，两种注册都补上
app.jinja_env.globals['tag_class'] = tag_class


@app.context_processor
def inject_globals():
    return {
        'app_cfg': CFG,
        'dept': current_dept(),
        'flash_data': pop_flash(),
        'step_of': step_of,
    }


STEP_NAMES = ['分部入口', '项目中心', '项目与转写', '调研输入', '信息核对', '预览与生成', '结果中心']


def step_of(n):
    return {'names': STEP_NAMES, 'current': n}


# ---------------------------------------------------------------- 1. 分部入口

@app.route('/login', methods=['GET', 'POST'])
def login():
    depts = departments.config()['departments']
    if request.method == 'POST':
        dept = departments.find(request.form.get('dept_key', ''))
        if dept and departments.verify(dept, request.form.get('password', '')):
            session['dept_key'] = dept['key']
            if not dept.get('changed'):
                flash_msg('当前仍在使用初始口令，建议尽快在「修改密码」中更换。', 'warn')
            return redirect(url_for('projects'))
        return render_template('login.html', depts=depts,
                               error='分部密码不正确，请重试。')
    if current_dept():
        return redirect(url_for('projects'))
    return render_template('login.html', depts=depts, error=None)


@app.route('/logout')
def logout():
    session.pop('dept_key', None)
    return redirect(url_for('login'))


@app.route('/password', methods=['GET', 'POST'])
def change_password():
    r = require_dept()
    if r:
        return r
    dept = current_dept()
    if request.method == 'POST':
        old = request.form.get('old_password', '')
        new = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')
        if not departments.verify(dept, old):
            return render_template('password.html', error='原密码不正确。')
        if len(new) < 6:
            return render_template('password.html', error='新密码至少 6 位。')
        if new != confirm:
            return render_template('password.html', error='两次输入的新密码不一致。')
        departments.set_password(dept['key'], new)
        flash_msg('分部密码已更新，下次进入需使用新密码。')
        return redirect(url_for('projects'))
    return render_template('password.html', error=None)


# ---------------------------------------------------------------- 2. 项目中心

@app.route('/')
def index():
    return redirect(url_for('projects') if current_dept() else url_for('login'))


@app.route('/projects')
def projects():
    r = require_dept()
    if r:
        return r
    dept = current_dept()
    kw = request.args.get('q', '').strip()
    view = request.args.get('view', 'active')
    if view == 'archived':
        rows = repo.list_projects(dept['key'], kw, archived=True)
    elif view == 'deleted':
        rows = repo.list_deleted(dept['key'])
    else:
        rows = repo.list_projects(dept['key'], kw, archived=False)
    rows = [repo.decorate_project(x) for x in rows]
    catalog = [p for p in list_products() if p.get('status') == 'active']
    return render_template('projects.html', rows=rows, kw=kw, view=view,
                           categories=repo.PROJECT_CATEGORIES, catalog=catalog)


@app.route('/projects/create', methods=['POST'])
def project_create():
    r = require_dept()
    if r:
        return r
    dept = current_dept()
    name = request.form.get('name', '').strip()
    if not name:
        flash_msg('请填写项目名称。', 'warn')
        return redirect(url_for('projects'))
    p = repo.create_project(dept['key'], {
        'category': request.form.get('category', '外部项目'),
        'name': name,
        'customer_name': request.form.get('customer_name', '').strip(),
        'location': request.form.get('location', '').strip(),
        'owner': request.form.get('owner', '').strip(),
        'contact': request.form.get('contact', '').strip(),
    })
    ptype = request.form.get('product_type', '').strip()
    if ptype:
        try:
            repo.ensure_product(p['id'], ptype)
        except Exception:
            flash_msg('项目已创建，但产品选择无效，请在项目概览中重新添加。', 'warn')
    # 一律回到项目概览：录音转写已是项目级动作，直接跳进产品调研页会让用户
    # 错过转写入口。
    return redirect(url_for('project_detail', pid=p['id']))


@app.route('/projects/<pid>/update', methods=['POST'])
def project_update(pid):
    r = require_dept()
    if r:
        return r
    load_project(pid)
    repo.update_project(pid, {
        'category': request.form.get('category', '外部项目'),
        'name': request.form.get('name', '').strip(),
        'customer_name': request.form.get('customer_name', '').strip(),
        'location': request.form.get('location', '').strip(),
        'owner': request.form.get('owner', '').strip(),
        'contact': request.form.get('contact', '').strip(),
    })
    flash_msg('项目信息已保存。')
    return redirect(url_for('project_detail', pid=pid))


@app.route('/projects/<pid>/archive', methods=['POST'])
def project_archive(pid):
    r = require_dept()
    if r:
        return r
    load_project(pid)
    repo.archive_project(pid, request.form.get('undo') != '1')
    flash_msg('项目已归档。' if request.form.get('undo') != '1' else '项目已恢复。')
    return redirect(request.referrer or url_for('projects'))


@app.route('/projects/<pid>/delete', methods=['POST'])
def project_delete(pid):
    r = require_dept()
    if r:
        return r
    load_project(pid)
    mode = request.form.get('mode', 'soft')
    if mode == 'purge':
        repo.purge_project(pid)
        flash_msg('项目及其全部数据已彻底删除，不可恢复。', 'warn')
    else:
        repo.soft_delete_project(pid)
        flash_msg('项目已删除，可在「已删除」中恢复。')
    return redirect(request.referrer or url_for('projects'))


@app.route('/projects/<pid>/restore', methods=['POST'])
def project_restore(pid):
    r = require_dept()
    if r:
        return r
    load_project(pid)
    repo.restore_project(pid)
    flash_msg('项目已恢复。')
    return redirect(url_for('projects'))


@app.route('/projects/<pid>/copy', methods=['POST'])
def project_copy(pid):
    r = require_dept()
    if r:
        return r
    load_project(pid)
    new = repo.copy_project(pid)
    flash_msg('已复制为「%s」，请修改名称。' % new['name'] if new else '复制失败。')
    return redirect(url_for('project_detail', pid=new['id']) if new else url_for('projects'))


# ---------------------------------------------------------------- 3. 项目概览与录音转写

@app.route('/projects/<pid>')
def project_detail(pid):
    r = require_dept()
    if r:
        return r
    project = load_project(pid)
    pps = []
    for pp in repo.list_products(pid):
        item = dict(pp)
        item['cfg'] = product_config(pp['product_type'])
        pct, st = repo.progress_of(pp['id'], pp['product_type'])
        item['progress'] = pct
        item['status_label'] = repo.PROJECT_STATUS_LABELS[st]
        item['stats'] = repo.field_stats(pp['id'], pp['product_type'])
        src = repo.latest_source(pp['id'])
        item['source_kind'] = src['kind'] if src else ''
        pps.append(item)
    # 进入项目概览即恢复未完成的转写任务：长任务可能跨越页面刷新和应用重启，
    # 只靠转写预览页恢复会让停在中间状态的任务无人认领。
    audio_jobs = repo.list_audio_jobs(pid)
    for job in audio_jobs:
        if job['status'] in repo.AUDIO_JOB_ACTIVE_STATUSES:
            audio_service.start_job(job['id'])
    selected = {p['product_type'] for p in pps}
    catalog = list_products()
    planned = []
    for c in catalog:
        if c['product_type'] not in selected:
            c = dict(c)
            c['is_planned'] = c.get('status') != 'active'
            planned.append(c)
    return render_template('project.html', project=repo.decorate_project(project),
                           pps=pps, planned=planned,
                           categories=repo.PROJECT_CATEGORIES,
                           audio_jobs=audio_jobs,
                           transcript=repo.latest_project_transcript(pid),
                           asr_status=audio_service.configuration_status())


@app.route('/projects/<pid>/products/add', methods=['POST'])
def product_add(pid):
    r = require_dept()
    if r:
        return r
    load_project(pid)
    ptype = request.form.get('product_type', '')
    try:
        product_config(ptype)
    except Exception:
        flash_msg('未知的产品类型。', 'warn')
        return redirect(url_for('project_detail', pid=pid))
    repo.ensure_product(pid, ptype)
    flash_msg('已添加产品。')
    return redirect(url_for('project_detail', pid=pid))


@app.route('/projects/<pid>/audio', methods=['POST'])
def audio_upload(pid):
    r = require_dept()
    if r:
        return r
    project = load_project(pid)
    uploaded = request.files.get('audio')
    if not uploaded:
        flash_msg('请选择录音文件。', 'warn')
        return redirect(url_for('project_detail', pid=pid))
    try:
        job = audio_service.create_job(project, uploaded)
    except Exception as exc:
        flash_msg(str(exc), 'warn')
        return redirect(url_for('project_detail', pid=pid))
    flash_msg('录音已接收，正在后台上传并转写。')
    return redirect(url_for('transcription_preview', pid=pid, job_id=job['id']))


@app.route('/projects/<pid>/audio/<job_id>/status')
def audio_job_status(pid, job_id):
    r = require_dept()
    if r:
        return jsonify({'ok': False, 'error': '未登录'}), 401
    _, job = load_audio_job(pid, job_id)
    audio_service.start_job(job_id)
    job = repo.get_audio_job(job_id)
    return jsonify({
        'ok': True, 'status': job['status'], 'message': job['status_message'],
        'error': job['error'],
        'preview_url': (url_for('transcription_preview', pid=pid, job_id=job_id)
                        if job['status'] in ('awaiting_preview', 'approved') else ''),
    })


@app.route('/projects/<pid>/audio/<job_id>')
def transcription_preview(pid, job_id):
    r = require_dept()
    if r:
        return r
    project, job = load_audio_job(pid, job_id)
    audio_service.start_job(job_id)
    job = repo.get_audio_job(job_id)
    try:
        segments = repo.json.loads(job['segments_json'] or '[]')
        corrections = repo.json.loads(job['corrections_json'] or '[]')
    except Exception:
        segments, corrections = [], []
    return render_template('transcription.html', project=project, job=job,
                           segments=segments, corrections=corrections,
                           pps=repo.list_products(pid))


@app.route('/projects/<pid>/audio/<job_id>/file')
def audio_file(pid, job_id):
    r = require_dept()
    if r:
        return r
    _, job = load_audio_job(pid, job_id)
    if not os.path.isfile(job['local_path']):
        abort(404)
    return send_file(job['local_path'], as_attachment=False,
                     download_name=job['original_name'], conditional=True)


@app.route('/projects/<pid>/audio/<job_id>/retry', methods=['POST'])
def audio_retry(pid, job_id):
    r = require_dept()
    if r:
        return r
    _, job = load_audio_job(pid, job_id)
    audio_service.retry_job(job)
    flash_msg('已重新提交转写任务。')
    return redirect(url_for('transcription_preview', pid=pid, job_id=job_id))


@app.route('/projects/<pid>/audio/<job_id>/approve', methods=['POST'])
def audio_approve(pid, job_id):
    r = require_dept()
    if r:
        return r
    _, job = load_audio_job(pid, job_id)
    if job['status'] not in ('awaiting_preview', 'approved'):
        flash_msg('请等待转写完成后再确认。', 'warn')
        return redirect(url_for('transcription_preview', pid=pid, job_id=job_id))
    try:
        text = audio_service.approve_transcript(job, request.form.get('transcript', ''))
    except audio_service.AudioServiceError as exc:
        flash_msg(str(exc), 'warn')
        return redirect(url_for('transcription_preview', pid=pid, job_id=job_id))
    flash_msg('转写稿已确认（%d 字），可在各产品调研页导入使用。' % len(text))
    return redirect(url_for('project_detail', pid=pid))


# ---------------------------------------------------------------- 4. 调研输入

@app.route('/p/<pp_id>/survey', methods=['GET', 'POST'])
def survey(pp_id):
    r = require_dept()
    if r:
        return r
    pp, project = load_pp(pp_id)
    if request.method == 'POST':
        text = request.form.get('transcript', '')
        try:
            survey_service.save_transcript(pp, project, text)
        except survey_service.SurveyServiceError as exc:
            flash_msg(str(exc), 'warn')
            return redirect(url_for('survey', pp_id=pp_id))
        flash_msg('文字稿已保存，共 %d 字。' % len(text))
        return redirect(url_for('survey', pp_id=pp_id))
    runs = db.query('SELECT * FROM extraction_runs WHERE project_product_id=? '
                      'ORDER BY started_at DESC LIMIT 5', (pp_id,))
    return render_template('survey.html', pp=pp, project=project,
                           source=repo.latest_source(pp_id),
                           runs=runs, samples=_samples(pp['product_type']),
                           limits=CFG['extraction'],
                           transcript=repo.latest_project_transcript(project['id']),
                           llm_status=extract.llm_status(),
                           product=product_config(pp['product_type']))


@app.route('/p/<pp_id>/import-transcript', methods=['POST'])
def import_transcript(pp_id):
    """把项目级共享转写稿导入当前产品。

    导入与提取刻意分成两步：导入后用户仍有机会先修正明显的错字，再决定是否
    消耗大模型额度。
    """
    r = require_dept()
    if r:
        return r
    pp, project = load_pp(pp_id)
    try:
        _, row = survey_service.import_project_transcript(pp, project)
    except survey_service.SurveyServiceError as exc:
        flash_msg(str(exc), 'warn')
        return redirect(url_for('survey', pp_id=pp_id))
    flash_msg('已导入项目转写稿（%d 字），确认无误后即可开始结构化梳理。' % row['char_count'])
    return redirect(url_for('survey', pp_id=pp_id))


def _samples(product_type='safety_butler'):
    out = []
    from core.paths import SAMPLES_DIR
    sample_dir = os.path.join(SAMPLES_DIR, product_type)
    if not os.path.isdir(sample_dir):
        if product_type != 'safety_butler':
            return out
        sample_dir = SAMPLES_DIR
    for fn in sorted(os.listdir(sample_dir)):
        if fn.endswith('.md'):
            with open(os.path.join(sample_dir, fn), encoding='utf-8') as f:
                out.append({'name': fn, 'content': f.read(),
                            'label': '完整样例（信息充分）' if 'full' in fn else '残缺样例（缺必填项）'})
    return out


@app.route('/p/<pp_id>/extract', methods=['POST'])
def run_extract(pp_id):
    r = require_dept()
    if r:
        return r
    pp, project = load_pp(pp_id)
    try:
        stats, protected_count = survey_service.extract_latest_source(pp, project)
    except survey_service.SurveyServiceError as exc:
        flash_msg(str(exc), 'warn')
        return redirect(url_for('survey', pp_id=pp_id))
    msg = ('提取完成：命中 %d/%d 个字段' % (stats['hit_fields'], stats['total_fields']))
    if protected_count:
        msg += '，已保护 %d 个人工确认字段' % protected_count
    flash_msg(msg + '。')
    return redirect(url_for('review', pp_id=pp_id))


# ---------------------------------------------------------------- 5. 信息核对

@app.route('/p/<pp_id>/review')
def review(pp_id):
    r = require_dept()
    if r:
        return r
    pp, project = load_pp(pp_id)
    ptype = pp['product_type']
    values = repo.field_values(pp_id)
    stats = repo.field_stats(pp_id, ptype)
    ok, issues = validate(pp_id, ptype)
    issue_map = {}
    for i in issues:
        issue_map.setdefault(i['field'], []).append(i)

    groups = []
    for g, fs in fields_by_group(ptype):
        items = []
        for f in fs:
            row = values.get(f['key'])
            if row:
                row = dict(row, status=repo.field_status(row, f))
            items.append({'def': f, 'row': row, 'issues': issue_map.get(f['key'], [])})
        groups.append({
            'def': g,
            'items': items,
            'miss': sum(1 for i in items if
                        (i['row'] and i['row']['status'] == 'required_missing') or
                        (not i['row'] and i['def'].get('required'))),
            'conf': sum(1 for i in items if i['row'] and i['row']['status'] == 'pending_confirm'),
        })

    return render_template('review.html', pp=pp, project=project, groups=groups,
                           stats=stats, ok=ok, issues=issues,
                           status_labels=repo.FIELD_STATUS_LABELS,
                           product=product_config(ptype),
                           run=repo.latest_extraction_run(pp_id),
                           field_template_version=field_config(ptype)['template_version'])


@app.route('/p/<pp_id>/field', methods=['POST'])
def save_field(pp_id):
    r = require_dept()
    if r:
        return r
    pp, project = load_pp(pp_id)
    data = request.get_json(force=True) or {}
    try:
        status, ok, stats = survey_service.save_field_value(pp, project, data)
    except survey_service.SurveyServiceError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    return jsonify({'ok': True, 'status': status,
                    'status_label': repo.FIELD_STATUS_LABELS[status],
                    'validation_ok': ok,
                    'required_done': stats['required_done'],
                    'required_total': stats['required_total'],
                    'pending_confirm': stats['pending_confirm'],
                    'required_missing': stats['required_missing']})


# ---------------------------------------------------------------- 6. 预览与生成

@app.route('/p/<pp_id>/preview')
def preview(pp_id):
    r = require_dept()
    if r:
        return r
    pp, project = load_pp(pp_id)
    ptype = pp['product_type']
    ok, issues, ctx, adefs = artifact_service.build_preview(pp)
    return render_template('preview.html', pp=pp, project=project, groups=ctx['groups'],
                           ok=ok, issues=issues, artifacts=adefs,
                           product=product_config(ptype))


@app.route('/p/<pp_id>/generate', methods=['POST'])
def do_generate(pp_id):
    r = require_dept()
    if r:
        return r
    pp, project = load_pp(pp_id)
    keys = request.form.getlist('artifacts')
    try:
        results = artifact_service.generate_artifacts(pp, keys)
    except artifact_service.ArtifactServiceError as exc:
        flash_msg(str(exc), 'warn')
        if '必填校验' in str(exc):
            return redirect(url_for('review', pp_id=pp_id))
        return redirect(url_for('preview', pp_id=pp_id))
    failed = [x for x in results if x['status'] == 'failed']
    if failed:
        flash_msg('生成完成，但有 %d 项失败，可在结果中心单独重试。' % len(failed), 'warn')
    else:
        flash_msg('已生成 %d 项内容。' % len(results))
    return redirect(url_for('result', pp_id=pp_id))


@app.route('/p/<pp_id>/regenerate', methods=['POST'])
def regenerate(pp_id):
    r = require_dept()
    if r:
        return r
    pp, project = load_pp(pp_id)
    key = request.form.get('artifact', '')
    try:
        res = artifact_service.regenerate_artifact(pp, key)
        flash_msg('重新生成成功。' if res['status'] == 'success' else '重新生成失败：%s' % res['error'],
                  'ok' if res['status'] == 'success' else 'warn')
    except artifact_service.ArtifactServiceError as exc:
        flash_msg(str(exc), 'warn')
    return redirect(url_for('result', pp_id=pp_id))


# ---------------------------------------------------------------- 7. 结果中心

@app.route('/p/<pp_id>/result')
def result(pp_id):
    r = require_dept()
    if r:
        return r
    pp, project = load_pp(pp_id)
    adefs = {k: v for k, v in artifact_map(pp['product_type']).items()}
    runs = []
    for x in repo.artifact_runs(pp_id):
        # 历史数据库可能保留已下线模块的生成记录。只展示当前产品契约中仍然
        # 存在的 artifact，既保留审计数据，也避免已删除模块重新出现在页面。
        if x['artifact_key'] not in adefs:
            continue
        item = dict(x)
        item['def'] = adefs.get(x['artifact_key'])
        item['files'] = repo.json.loads(x['files_json'] or '[]')
        runs.append(item)
    runs.sort(key=lambda x: (x['def'] or {}).get('order', 99))
    export = artifact_service.latest_current_export(pp)
    return render_template('result.html', pp=pp, project=project, runs=runs,
                           export=export, product=product_config(pp['product_type']))


@app.route('/p/<pp_id>/file/<artifact_key>/<path:filename>')
def artifact_file(pp_id, artifact_key, filename):
    r = require_dept()
    if r:
        return r
    pp, project = load_pp(pp_id)
    if artifact_key not in artifact_map(pp['product_type']):
        abort(404)
    d = generate.artifact_output_dir(pp_id, artifact_key)
    # safe_join 阻止通过 ../ 越过当前生成物目录读取服务器上的其它文件。
    path = safe_join(d, filename)
    if not path or not os.path.isfile(path):
        abort(404)
    inline = request.args.get('mode', 'view') == 'view' and filename.endswith('.html')
    return send_file(path, as_attachment=not inline, download_name=filename)


@app.route('/p/<pp_id>/package', methods=['POST'])
def package(pp_id):
    r = require_dept()
    if r:
        return r
    pp, project = load_pp(pp_id)
    keys = request.form.getlist('artifacts') or None
    try:
        zip_path, file_name, manifest = artifact_service.package_artifacts(pp, keys)
    except Exception as e:
        flash_msg('打包失败：%s' % e, 'warn')
        return redirect(url_for('result', pp_id=pp_id))
    return send_file(zip_path, as_attachment=True, download_name=file_name)


@app.route('/p/<pp_id>/package/download')
def package_download(pp_id):
    r = require_dept()
    if r:
        return r
    pp, project = load_pp(pp_id)
    export = artifact_service.latest_current_export(pp)
    if not export or not os.path.isfile(export['file_path']):
        flash_msg('尚未生成打包文件。', 'warn')
        return redirect(url_for('result', pp_id=pp_id))
    return send_file(export['file_path'], as_attachment=True,
                     download_name=export['file_name'])


# ---------------------------------------------------------------- 启动

@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', code=404, msg='页面或资源不存在。'), 404


@app.errorhandler(403)
def forbidden(e):
    return render_template('error.html', code=403, msg='无权访问该资源。'), 403


def main():
    db.init_db()
    host = runtime.resolve_host(CFG)
    port = runtime.resolve_port(CFG)
    url = 'http://%s:%d' % (host, port)
    print('=' * 52)
    print('  灵石解决方案工作台 Demo')
    print('  运行模式：%s' % runtime.mode())
    print('  访问地址：%s' % url)
    print('  数据目录：%s' % os.path.join(ROOT, 'data'))
    print('  停止服务：Ctrl + C')
    print('=' * 52)
    if runtime.is_production():
        print('  注意：当前为 Flask 开发服务器，生产环境请改用 waitress 承载（见 wsgi.py / deploy）。')
    if runtime.should_open_browser(CFG):
        import threading
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == '__main__':
    main()
