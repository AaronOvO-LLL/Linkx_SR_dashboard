"""生成物渲染

模板位置：config/products/<product_type>/templates/*.md.j2
输出位置：data/outputs/<pp_id>/<artifact_key>/
新增生成物 = 新增一份 artifacts.json 条目 + 一个 .j2 模板，无需改动本文件。
"""
import json
import os
import re
import traceback
from datetime import datetime

from jinja2 import Environment, FileSystemLoader, select_autoescape, StrictUndefined

from . import paths, repo
from .config import (artifact_map, artifacts_config, app_config, field_map,
                     fields_by_group, product_config)
from .repo import (FIELD_STATUS_LABELS, field_values, get_pp, latest_extraction_run,
                   latest_source)
from .validate import parse_value

_ENV_CACHE = {}


def _env_for(product_type):
    if product_type in _ENV_CACHE:
        return _ENV_CACHE[product_type]
    d = paths.artifact_template_dir(product_type)
    env = Environment(
        loader=FileSystemLoader(d, encoding='utf-8'),
        autoescape=False,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters['joinlist'] = _joinlist
    _ENV_CACHE[product_type] = env
    return env


def _joinlist(v, sep='、'):
    if v is None:
        return ''
    if isinstance(v, list):
        return sep.join(str(x) for x in v)
    return str(v)


def _value_to_text(v):
    if v is None:
        return ''
    if isinstance(v, list):
        if v and isinstance(v[0], dict):
            return '；'.join(
                ' '.join(str(x.get(k, '')) for x in [r] for k in r if str(x.get(k, '')).strip())
                for r in v)
        return '、'.join(str(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def build_context(pp_id, product_type):
    """构造模板渲染上下文：所有业务数据都在这里解析好，模板只负责表达。"""
    pp = get_pp(pp_id)
    project = repo.get_project(pp['project_id'])
    fmap = field_map(product_type)
    values = field_values(pp_id)
    src = latest_source(pp_id)
    run = latest_extraction_run(pp_id)

    data, display, detail = {}, {}, {}
    for key, f in fmap.items():
        row = values.get(key)
        v = parse_value(row['value_json']) if row else None
        data[key] = v
        display[key] = _value_to_text(v)
        detail[key] = {
            'label': f['label'],
            'value': v,
            'text': _value_to_text(v),
            'status': row['status'] if row else 'optional_missing',
            'status_label': FIELD_STATUS_LABELS.get(row['status'], '') if row else '可选缺失',
            'confidence': row['confidence'] if row else None,
            'quote': row['source_quote'] if row else '',
            'updated_by': row['updated_by'] if row else '',
            'unit': f.get('unit', ''),
            'type': f['type'],
        }

    groups = []
    for g, fs in fields_by_group(product_type):
        groups.append({
            'key': g['key'], 'name': g['name'], 'desc': g.get('desc', ''),
            'fields': [detail[f['key']] for f in fs],
        })

    cfg_ver = product_config(product_type)['version']
    return {
        'project': dict(project) if project else {},
        'product': product_config(product_type),
        'pp': dict(pp),
        'groups': groups,
        'data': data,
        'display': display,
        'detail': detail,
        'meta': {
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'product_version': cfg_ver,
            'template_version': artifacts_config(product_type)['template_version'],
            'extraction_provider': (run['provider'] if run else '-'),
            'source_chars': (src['char_count'] if src else 0),
        },
    }

# ------------------------------------------------------------------ 极简 Markdown → HTML

def md_to_html(md, title=''):
    lines = md.splitlines()
    out = []
    in_list = False
    in_table = False
    para = []

    def flush_para():
        nonlocal para
        if para:
            out.append('<p>' + _inline(' '.join(para)) + '</p>')
            para = []

    def close_list():
        nonlocal in_list
        if in_list:
            out.append('</ul>')
            in_list = False

    def close_table():
        nonlocal in_table
        if in_table:
            out.append('</tbody></table>')
            in_table = False

    for ln in lines:
        s = ln.rstrip()
        if not s.strip():
            flush_para()
            close_list()
            close_table()
            continue
        if s.startswith('|---') or re.match(r'^\|[\s:|-]+\|$', s):
            continue
        if s.startswith('|'):
            body = [c.strip() for c in s.strip('|').split('|')]
            if not in_table:
                close_list()
                flush_para()
                out.append('<table><thead><tr>' +
                           ''.join('<th>%s</th>' % _inline(c) for c in body) +
                           '</tr></thead><tbody>')
                in_table = True
            else:
                out.append('<tr>' + ''.join('<td>%s</td>' % _inline(c) for c in body) + '</tr>')
            continue
        close_table()
        m = re.match(r'^(#{1,6})\s+(.*)$', s)
        if m:
            flush_para()
            close_list()
            lv = len(m.group(1))
            out.append('<h%d>%s</h%d>' % (lv, _inline(m.group(2)), lv))
            continue
        if re.match(r'^\s*[-*+]\s+', s):
            flush_para()
            if not in_list:
                out.append('<ul>')
                in_list = True
            out.append('<li>%s</li>' % _inline(re.sub(r'^\s*[-*+]\s+', '', s)))
            continue
        if re.match(r'^\s*\d+[.、]\s+', s):
            flush_para()
            close_list()
            out.append('<li>%s</li>' % _inline(re.sub(r'^\s*\d+[.、]\s+', '', s)))
            continue
        if s.startswith('>'):
            flush_para()
            out.append('<blockquote>%s</blockquote>' % _inline(s.lstrip('> ')))
            continue
        if re.match(r'^-{3,}$', s.strip()):
            flush_para()
            out.append('<hr>')
            continue
        close_list()
        para.append(s.strip())
    flush_para()
    close_list()
    close_table()

    body = '\n'.join(out)
    esc_title = (title or '生成物').replace('<', '&lt;')
    return HTML_WRAPPER.replace('{{title}}', esc_title).replace('{{body}}', body)


def _inline(s):
    s = (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
    s = re.sub(r'`(.+?)`', r'<code>\1</code>', s)
    return s


HTML_WRAPPER = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{title}}</title>
<style>
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;max-width:900px;margin:0 auto;padding:28px 22px;color:#1f2937;line-height:1.75;font-size:15px}
h1{font-size:24px;color:#2F5597;border-bottom:2px solid #2F5597;padding-bottom:10px;margin:0 0 18px}
h2{font-size:19px;color:#2F5597;margin:28px 0 12px;padding-left:10px;border-left:4px solid #2F5597}
h3{font-size:16px;margin:20px 0 8px;color:#374151}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px}
th,td{border:1px solid #d6dae2;padding:8px 10px;text-align:left}
th{background:#eef2fa;color:#2F5597;font-weight:600}
tr:nth-child(even) td{background:#fafbfd}
ul{padding-left:22px}li{margin:4px 0}
blockquote{margin:12px 0;padding:10px 14px;background:#f5f7fb;border-left:4px solid #2F5597;color:#4b5563}
hr{border:none;border-top:1px solid #e5e7eb;margin:22px 0}
code{background:#f1f3f7;padding:2px 6px;border-radius:4px;font-size:13px}
.meta{color:#6b7280;font-size:12px;margin-top:30px;border-top:1px solid #e5e7eb;padding-top:12px}
</style></head><body>{{body}}</body></html>"""


# ------------------------------------------------------------------ 生成调度

def artifact_output_dir(pp_id, artifact_key):
    d = os.path.join(paths.OUTPUT_DIR, pp_id, artifact_key)
    os.makedirs(d, exist_ok=True)
    return d


def render_artifact(pp_id, product_type, artifact_key):
    """渲染单个生成物。成功返回 files 列表；失败抛出异常由调用方记录。"""
    adef = artifact_map(product_type)[artifact_key]
    ctx = build_context(pp_id, product_type)
    env = _env_for(product_type)
    tpl = env.get_template(adef['template'])
    md = tpl.render(**ctx)
    md = re.sub(r'\n{3,}', '\n\n', md).strip() + '\n'

    out_dir = artifact_output_dir(pp_id, artifact_key)
    base = adef['name']
    files = []

    for fmt in adef.get('formats', ['md']):
        if fmt == 'md':
            p = os.path.join(out_dir, base + '.md')
            _write_text(p, md)
        elif fmt == 'html':
            p = os.path.join(out_dir, base + '.html')
            _write_text(p, md_to_html(md, '%s · %s' % (adef['name'], ctx['project'].get('name', ''))))
        else:
            continue
        files.append({'format': fmt, 'name': os.path.basename(p), 'path': p,
                      'size': os.path.getsize(p)})
    return files


def _write_text(path, content):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


def generate_selected(pp_id, product_type, keys):
    """逐项生成，单项失败不影响其他项。返回 [{key,status,files,error}]。"""
    results = []
    for key in keys:
        try:
            files = render_artifact(pp_id, product_type, key)
            repo.upsert_artifact_run(pp_id, key, 'success', files=files,
                                     template_version=artifacts_config(product_type)['template_version'])
            results.append({'key': key, 'status': 'success', 'files': files, 'error': ''})
        except Exception as e:
            msg = '%s: %s' % (type(e).__name__, e)
            repo.upsert_artifact_run(pp_id, key, 'failed', files=[], error=msg)
            results.append({'key': key, 'status': 'failed', 'files': [], 'error': msg})
            traceback.print_exc()
    return results
