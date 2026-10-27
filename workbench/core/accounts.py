"""个人账号、凭据撤销及项目权限；不信任客户端传入的角色或归属。"""
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

from . import db, departments

HASH_METHOD = 'pbkdf2:sha256:1000000'
REGISTER_DEFAULT_PASSWORD = 'onewo123'
# 未知账号也执行一次相同成本的校验，登录响应不暴露账号是否存在。
_DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(32), method=HASH_METHOD)
PUBLIC_COLUMNS = 'id, username, display_name, dept_key, role, active, auth_version, created_at, updated_at'


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def get_user(uid):
    return db.query_one('SELECT %s FROM users WHERE id=?' % PUBLIC_COLUMNS, (uid,))


def list_users():
    return db.query('SELECT %s FROM users ORDER BY created_at, username' % PUBLIC_COLUMNS)


def managed_users(actor):
    sql = '''SELECT %s, (SELECT COUNT(*) FROM projects p
        WHERE p.creator_user_id=u.id) AS project_count FROM users u''' % ', '.join(
            'u.' + column.strip() for column in PUBLIC_COLUMNS.split(','))
    args = ()
    if actor['role'] != 'admin':
        sql += ' WHERE u.dept_key=?'
        args = (actor['dept_key'],)
    return db.query(sql + ' ORDER BY u.created_at DESC, u.username', args)


def can_manage(actor, target):
    return bool(actor and actor['active'] and target and (
        actor['role'] == 'admin' or (actor['role'] == 'dept_admin' and
        actor['dept_key'] == target['dept_key'] and target['role'] == 'user')))


def _validate(display_name, dept_key, role):
    if not display_name.strip() or len(display_name) > 80:
        raise ValueError('姓名不能为空且不能超过 80 字。')
    if not departments.find(dept_key):
        raise ValueError('请选择有效分部。')
    if role not in ('user', 'dept_admin', 'admin'):
        raise ValueError('账号角色无效。')


def _password_hash(password):
    if not 8 <= len(password) <= 128 or not password.strip():
        raise ValueError('密码需为 8–128 位，且不能全为空格。')
    return generate_password_hash(password, method=HASH_METHOD)


def _event(conn, actor_id, uid, action):
    conn.execute('INSERT INTO account_events VALUES (?,?,?,?,?)',
                 ('evt_' + uuid.uuid4().hex, actor_id, uid, action, _now()))


def create_user(username, display_name, dept_key, password, role='user', actor_id='local-cli'):
    username = username.strip().lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.@-]{2,63}', username):
        raise ValueError('用户名需为 3–64 位英文字母、数字或 _.@-，首位为字母或数字。')
    _validate(display_name, dept_key, role)
    hashed = _password_hash(password)
    uid, ts = 'usr_' + uuid.uuid4().hex, _now()
    conn = db.init_db()
    try:
        with conn:
            conn.execute('''INSERT INTO users
                (id, username, display_name, dept_key, role, password_hash, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?)''',
                (uid, username, display_name.strip(), dept_key, role, hashed, ts, ts))
            _event(conn, actor_id, uid, 'create')
    except sqlite3.IntegrityError as exc:
        raise ValueError('该用户名已存在。') from exc
    return get_user(uid)


def register_user(display_name, phone, dept_key):
    """手机号作为唯一用户名，自助注册始终是普通账号。"""
    phone = phone.strip()
    if not re.fullmatch(r'1[3-9][0-9]{9}', phone):
        raise ValueError('请填写有效的 11 位手机号，注册后使用此号码登录。')
    try:
        return create_user(phone, display_name, dept_key, REGISTER_DEFAULT_PASSWORD,
                           role='user', actor_id='self-registration')
    except ValueError as exc:
        if str(exc) == '该用户名已存在。':
            raise ValueError('该手机号已注册，请直接登录。') from exc
        raise


def update_user(uid, display_name, dept_key, role, active, actor_id):
    _validate(display_name, dept_key, role)
    conn = db.init_db()
    try:
        conn.execute('BEGIN IMMEDIATE')
        current = conn.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        if not current:
            raise ValueError('账号不存在。')
        if current['active'] and current['role'] == 'admin' and (not active or role != 'admin'):
            count = conn.execute("SELECT COUNT(*) FROM users WHERE active=1 AND role='admin'").fetchone()[0]
            if count <= 1:
                raise ValueError('不能停用或降级最后一位启用的管理员。')
        conn.execute('''UPDATE users SET display_name=?, dept_key=?, role=?, active=?,
            auth_version=auth_version+1, updated_at=? WHERE id=?''',
            (display_name.strip(), dept_key, role, int(bool(active)), _now(), uid))
        _event(conn, actor_id, uid, 'update')
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_user(uid)


def reset_password(uid, password, actor_id):
    hashed = _password_hash(password)
    conn = db.init_db()
    with conn:
        cur = conn.execute('''UPDATE users SET password_hash=?, auth_version=auth_version+1,
            updated_at=? WHERE id=?''', (hashed, _now(), uid))
        if not cur.rowcount:
            raise ValueError('账号不存在。')
        _event(conn, actor_id, uid, 'password_change' if actor_id == uid else 'password_reset')
    return get_user(uid)


def verify_password(uid, password):
    row = db.query_one('SELECT password_hash FROM users WHERE id=?', (uid,))
    return check_password_hash(row['password_hash'] if row else _DUMMY_HASH, password)


def authenticate(username, password):
    """同一用户名五次失败后在五分钟窗口内拒绝登录，计数跨重启保存。"""
    username = username.strip().lower()[:64]
    now = time.time()
    conn = db.init_db()
    # 在哈希校验前占用一次尝试，并发请求也不能绕过失败次数。
    with conn:
        conn.execute('DELETE FROM login_attempts WHERE window_start <= ?', (now - 300,))
        conn.execute('''INSERT INTO login_attempts VALUES (?,1,?)
            ON CONFLICT(username) DO UPDATE SET failures=failures+1''', (username, now))
        attempts = conn.execute('SELECT failures FROM login_attempts WHERE username=?', (username,)).fetchone()[0]
    if attempts > 5:
        raise ValueError('尝试次数过多，请在 5 分钟后重试。')
    row = db.query_one('SELECT * FROM users WHERE username=?', (username,))
    valid = check_password_hash(row['password_hash'] if row else _DUMMY_HASH, password[:129])
    if not row or not row['active'] or not valid:
        raise ValueError('用户名或密码不正确，或账号已停用。')
    db.execute('DELETE FROM login_attempts WHERE username=?', (username,))
    return get_user(row['id'])


def can_access(project, user, dept_key):
    if user and user['active'] and user['role'] == 'admin':
        return True
    if (user and user['active'] and user['role'] == 'dept_admin'
            and project['dept_key'] == user['dept_key']):
        return True
    if project.get('visibility') == 'private':
        return bool(user and user['active'] and project.get('creator_user_id') == user['id'])
    return project.get('visibility') == 'department' and project['dept_key'] == dept_key


def creator_label(project):
    if not project.get('creator_user_id'):
        return '历史项目（未记录个人创建者）'
    user = get_user(project['creator_user_id'])
    return '%s（%s）' % (project['creator_name'], user['username'] if user else '账号已不可用')
