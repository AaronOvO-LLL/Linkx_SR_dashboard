#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分部凭据：`config/` 里是只读种子，`data/` 里是运行时哈希凭据。

为什么分两份：服务器上 `config/` 对服务进程保持只读（最小权限），而改密是运行时
行为，必须落到 `data/` 下——部署时该目录已 chown 给 www-data。种子里的明文初始
口令只在首次初始化时被读取一次，此后运行时文件里只有哈希，读到文件也拿不到可用口令。

新增分部需手工编辑 `data/departments.json`，或删除该文件按种子重建（会丢失所有已改口令）。
"""
import json
import os

from werkzeug.security import check_password_hash, generate_password_hash

from . import paths

SEED_PATH = os.path.join(paths.CONFIG_DIR, 'departments.json')
RUNTIME_PATH = os.path.join(paths.DATA_DIR, 'departments.json')

# 显式钉住算法：werkzeug 的默认值随版本变过（2.2 是 pbkdf2，2.3+ 是 scrypt），
# 而 scrypt 还依赖 OpenSSL 支持。写死后才能保证本机 Windows 与服务器 Ubuntu
# 产出同一格式，任何平台都能校验。
_HASH_METHOD = 'pbkdf2:sha256'

_cache = None


def _read(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _write(cfg):
    tmp = RUNTIME_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RUNTIME_PATH)
    os.chmod(RUNTIME_PATH, 0o600)


def ensure_initialized(force=False):
    """按种子生成运行时凭据文件。

    已存在时默认跳过——否则每次启动都会把用户改过的口令重置回初始值。
    """
    global _cache
    if force or not os.path.isfile(RUNTIME_PATH):
        seed = _read(SEED_PATH)
        _write({'departments': [
            {
                'key': d['key'],
                'name': d['name'],
                'password_hash': generate_password_hash(
                    d.get('password') or '', method=_HASH_METHOD),
                'changed': bool(d.get('changed')),
            }
            for d in seed['departments']
        ]})
    _cache = None


def config():
    global _cache
    if _cache is None:
        ensure_initialized()
        _cache = _read(RUNTIME_PATH)
    return _cache


def find(dept_key):
    return next((d for d in config()['departments'] if d['key'] == dept_key), None)


def verify(dept, password):
    stored = (dept or {}).get('password_hash') or ''
    return bool(stored) and check_password_hash(stored, password)


def set_password(dept_key, password):
    cfg = config()
    for d in cfg['departments']:
        if d['key'] == dept_key:
            d['password_hash'] = generate_password_hash(password, method=_HASH_METHOD)
            d['changed'] = True
    _write(cfg)


def unchanged_names():
    """仍在用初始口令（从未改过）的分部名，供生产模式启动告警。"""
    return [d.get('name') or d.get('key')
            for d in config()['departments'] if not d.get('changed')]
