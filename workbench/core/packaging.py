"""ZIP 打包模块

目录结构与命名规则全部来自 config/app.json 的 zip 配置，代码不含业务命名。
"""
import json
import os
import re
import zipfile
from datetime import datetime

from . import paths, repo
from .config import app_config, artifact_map, artifacts_config, product_config
from .repo import artifact_runs, get_pp, latest_extraction_run, latest_source

INVALID = re.compile(r'[\\/:*?"<>|\r\n\t]')


def safe_name(s, fallback='未命名'):
    s = INVALID.sub('_', (s or '').strip())
    return s[:60] or fallback


def package_zip(pp_id, product_type, keys=None):
    """把已成功生成的产物打包为 ZIP，附带 manifest.json 与 README.txt。"""
    cfg = app_config()['zip']
    pp = get_pp(pp_id)
    project = repo.get_project(pp['project_id'])
    prod = product_config(product_type)
    adefs = artifact_map(product_type)
    runs = {r['artifact_key']: r for r in artifact_runs(pp_id)}
    if keys is None:
        keys = [k for k in adefs if runs.get(k) and runs[k]['status'] == 'success']
    else:
        # 请求参数可能来自旧页面或历史表单；只接受当前产品仍声明的生成物，
        # 防止已下线模块的历史 key 重新进入新导出包。
        keys = [key for key in keys if key in adefs]

    folders = cfg['folders']
    ts = datetime.now()
    file_name = '%s_%s_%s.zip' % (safe_name(project['name'], '项目'),
                                  safe_name(prod['name'], '产品'),
                                  ts.strftime('%Y%m%d_%H%M'))

    out_dir = os.path.join(paths.EXPORT_DIR, pp_id)
    os.makedirs(out_dir, exist_ok=True)
    zip_path = os.path.join(out_dir, file_name)

    manifest = {
        'schema': 'lingshi-export-manifest/1.0',
        'generated_at': ts.strftime('%Y-%m-%d %H:%M:%S'),
        'project': {
            'id': project['id'],
            'name': project['name'],
            'category': project['category'],
            'customer_name': project['customer_name'],
            'dept_key': project['dept_key'],
        },
        'product': {
            'product_type': product_type,
            'name': prod['name'],
            'product_version': prod['version'],
            'template_version': artifacts_config(product_type)['template_version'],
        },
        'input': {},
        'artifacts': [],
        'files': [],
    }

    src = latest_source(pp_id)
    run = latest_extraction_run(pp_id)
    if src:
        manifest['input'] = {
            'kind': src['kind'],
            'char_count': src['char_count'],
            'created_at': src['created_at'],
        }
    if run:
        manifest['input']['extraction'] = {
            'provider': run['provider'],
            'template_version': run['template_version'],
            'stats': json.loads(run['stats_json'] or '{}'),
            'finished_at': run['finished_at'],
        }

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for key in keys:
            r = runs.get(key)
            if not r or r['status'] != 'success':
                continue
            adef = adefs[key]
            folder = folders.get(adef['folder_key'], adef['name'])
            files = json.loads(r['files_json'] or '[]')
            manifest['artifacts'].append({
                'key': key,
                'name': adef['name'],
                'folder': folder,
                'template_version': r['template_version'],
                'generated_at': r['finished_at'],
                'files': [f['name'] for f in files],
            })
            for f in files:
                if not os.path.exists(f['path']):
                    continue
                arc = '%s/%s' % (folder, f['name'])
                z.write(f['path'], arc)
                manifest['files'].append({'path': arc, 'size': f.get('size', 0)})

        z.writestr(cfg.get('manifest_file', 'manifest.json'),
                   json.dumps(manifest, ensure_ascii=False, indent=2))
        z.writestr(cfg.get('readme_file', 'README.txt'), _readme(manifest))

    repo.save_export_package(pp_id, file_name, zip_path, manifest)
    return zip_path, file_name, manifest


def _readme(m):
    lines = [
        '%s · %s 生成物包' % (m['project']['name'], m['product']['name']),
        '=' * 40,
        '',
        '生成时间：%s' % m['generated_at'],
        '客户：%s' % (m['project'].get('customer_name') or '-'),
        '产品版本：%s    字段模板版本：%s' % (m['product']['product_version'],
                                           m['product']['template_version']),
        '',
        '本包包含以下生成物：',
    ]
    for a in m['artifacts']:
        lines.append('  [%s] %s —— %s' % (a['folder'], a['name'], '、'.join(a['files'])))
    if m.get('input', {}).get('extraction'):
        e = m['input']['extraction']
        lines += ['', '本次内容由「%s」提取引擎基于 %s 字文字稿生成（模板版本 %s）。' % (
            e['provider'], m['input'].get('char_count', 0), e['template_version'])]
    lines += ['', '所有内容均经人工确认后生成，仅供方案沟通使用。', '']
    return '\n'.join(lines)
