"""产品字段附件：文件留在私有上传目录，元数据复用既有字段存储。"""
import json
import os
import re
import secrets
import threading
from pathlib import Path

from core import paths, repo
from core.capabilities import require_capability
from core.config import field_map, field_config

_lock = threading.RLock()


class AttachmentError(ValueError):
    pass


def _field(pp, key):
    require_capability(pp['product_type'], 'field_attachments')
    field = field_map(pp['product_type']).get(key)
    if not field or field['type'] != 'attachment':
        raise AttachmentError('该字段不支持附件。')
    return field


def entries(pp, key):
    row = repo.field_values(pp['id']).get(key)
    value = json.loads(row['value_json']) if row else []
    return value if isinstance(value, list) else []


def _path(pp, key, item):
    token = item.get('id', '')
    if not re.fullmatch(r'[0-9a-f]{32}', token):
        raise AttachmentError('附件标识无效。')
    return Path(paths.UPLOAD_DIR) / 'field_attachments' / pp['id'] / key / token


def _store(pp, project, key, items):
    repo.upsert_field_value(pp['id'], key, json.dumps(items, ensure_ascii=False),
                           'confirmed' if items else 'optional_missing', None, '', 'human',
                           field_config(pp['product_type'])['template_version'], 1)
    repo.touch_project(project['id'])


def upload(pp, project, key, uploaded, host, description, kind):
    field = _field(pp, key)
    spec = field['upload']
    if not uploaded or not uploaded.filename:
        raise AttachmentError('请选择文件。')
    name = uploaded.filename.replace('\\', '/').split('/')[-1][:160]
    ext = name.rsplit('.', 1)[-1].lower()
    if ext not in spec['extensions']:
        raise AttachmentError('不支持此文件类型。')
    host, description = host.strip(), description.strip()
    if not host or len(host) > 120 or not description or len(description) > 1000:
        raise AttachmentError('请填写消控室/主机编号（最多120字）及说明（最多1000字）。')
    if kind not in spec['record_kinds']:
        raise AttachmentError('请选择记录类型。')
    content = uploaded.stream.read(spec['max_bytes'] + 1)
    if not content or len(content) > spec['max_bytes']:
        raise AttachmentError('文件为空或超过10MB。')
    signatures = {'jpg': content.startswith(b'\xff\xd8\xff'), 'jpeg': content.startswith(b'\xff\xd8\xff'),
                  'png': content.startswith(b'\x89PNG\r\n\x1a\n'), 'pdf': content.startswith(b'%PDF-'),
                  'webp': content.startswith(b'RIFF') and content[8:12] == b'WEBP'}
    if not signatures.get(ext):
        raise AttachmentError('文件内容与扩展名不符。')
    with _lock:
        items = entries(pp, key)
        if len(items) >= spec['max_files']:
            raise AttachmentError('此字段已达到50个附件上限。')
        item = dict(id=secrets.token_hex(16), name=name, host=host, description=description,
                    kind=kind, size=len(content))
        target = _path(pp, key, item)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        try:
            _store(pp, project, key, items + [item])
        except Exception:
            target.unlink(missing_ok=True)
            raise
    return item


def download(pp, key, token):
    _field(pp, key)
    item = next((i for i in entries(pp, key) if i.get('id') == token), None)
    if not item or not _path(pp, key, item).is_file():
        raise AttachmentError('附件不存在或已删除。')
    return str(_path(pp, key, item)), item['name']


def remove(pp, project, key, token):
    _field(pp, key)
    with _lock:
        download(pp, key, token)
        items = entries(pp, key)
        item = next(i for i in items if i['id'] == token)
        _store(pp, project, key, [i for i in items if i['id'] != token])
        _path(pp, key, item).unlink(missing_ok=True)
