#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""端到端自测：覆盖需求文档第 13 节的验收场景 A / B / C / E / F。

运行： python selftest.py
"""
import io
import json
import os
import shutil
import sys
import zipfile

# 端到端回归不应消耗真实大模型额度；生产页面仍按 app.json 使用 LLM。
os.environ['LS_FORCE_EXTRACTION_PROVIDER'] = 'rule'

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import app as web
from core import db, repo

PASS, FAIL = '✅', '❌'
results = []


def check(name, cond, detail=''):
    results.append((cond, name, detail))
    print('%s %s%s' % (PASS if cond else FAIL, name, (' —— ' + detail) if detail else ''))
    return cond


def section(t):
    print('\n' + '─' * 60)
    print('  ' + t)
    print('─' * 60)


def main():
    # 用临时数据目录，避免污染真实演示数据
    tmp_db = os.path.join(web.ROOT, 'data', 'selftest.db')
    for ext in ('', '-wal', '-shm'):
        p = tmp_db + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:          # 文件被占用时忽略，下次运行会重建
                open(p, 'w').close()

    from core import paths, db
    db._local.conn = None
    orig_db = db.db_path
    db.db_path = lambda: tmp_db
    paths.OUTPUT_DIR = os.path.join(paths.DATA_DIR, 'outputs_selftest')
    paths.EXPORT_DIR = os.path.join(paths.DATA_DIR, 'exports_selftest')
    os.makedirs(paths.OUTPUT_DIR, exist_ok=True)
    os.makedirs(paths.EXPORT_DIR, exist_ok=True)

    client = web.app.test_client()

    # ---------------------------------------------------------------- 登录
    section('步骤 1 · 分部入口')
    r = client.post('/login', data={'dept_key': 'dept1', 'password': 'wrong'})
    check('错误密码被拒绝', b'\xe5\xaf\x86\xe7\xa0\x81\xe4\xb8\x8d\xe6\xad\xa3\xe7\xa1\xae' in r.data
          or '不正确' in r.data.decode('utf-8'))
    r = client.post('/login', data={'dept_key': 'dept1', 'password': '000000'},
                    follow_redirects=True)
    check('正确密码进入项目库', r.status_code == 200 and '项目库' in r.data.decode('utf-8'))

    # ---------------------------------------------------------------- 建项目
    section('步骤 2-3 · 项目中心与项目概览')
    r = client.post('/projects/create', data={
        'name': '自测-新宇精密安全管家', 'customer_name': '苏州新宇精密制造有限公司',
        'category': '外部项目', 'location': '苏州市吴中区', 'owner': '张伟',
        'contact': '陈主任 13812345678', 'product_type': 'safety_butler'}, follow_redirects=True)
    html = r.data.decode('utf-8')
    check('项目创建成功', '自测-新宇精密安全管家' in html)

    pid = db.query("SELECT id FROM projects WHERE name LIKE '自测-%'")[0]['id']
    pps = repo.list_products(pid)
    check('新建项目时已选择产品并进入录入', len(pps) == 1 and
          pps[0]['product_type'] == 'safety_butler' and '选择调研资料' in html)
    pp_id = pps[0]['id']

    # 不调用云端，用一条已完成的模拟任务验证“转写结果必须先预览”的页面契约。
    audio_job = repo.create_audio_job(pp_id, '虚构测试录音.m4a', 'Z:\\not-real\\audio.m4a', 1024)
    repo.update_audio_job(
        audio_job['id'], status='awaiting_preview', status_message='转写完成，请预览并确认',
        raw_transcript='现场要做洲际入侵。', corrected_transcript='现场要做周界入侵。',
        segments_json=json.dumps([{'index': 1, 'start_ms': 0, 'end_ms': 3000,
                                  'speaker_id': 0, 'raw_text': '现场要做洲际入侵。',
                                  'text': '现场要做周界入侵。'}], ensure_ascii=False),
        corrections_json=json.dumps([{'from': '洲际入侵', 'to': '周界入侵',
                                      'count': 1, 'segment': 1}], ensure_ascii=False))
    r = client.get('/p/%s/audio/%s' % (pp_id, audio_job['id']))
    preview = r.data.decode('utf-8')
    check('转写结果先进入预览页', r.status_code == 200 and
          '确认并开始大模型结构化梳理' in preview and '现场要做周界入侵' in preview)

    # ---------------------------------------------------------------- 场景 A：完整主流程
    section('场景 A · 完整主流程')
    text = open(os.path.join(paths.SAMPLES_DIR, 'transcript_full.md'), encoding='utf-8').read()
    client.post('/p/%s/survey' % pp_id, data={'transcript': text}, follow_redirects=True)
    src = repo.latest_source(pp_id)
    check('文字稿已保存', src is not None and src['char_count'] == len(text),
          '%d 字' % (src['char_count'] if src else 0))

    r = client.post('/p/%s/extract' % pp_id, follow_redirects=True)
    check('解析执行成功', r.status_code == 200)
    run = repo.latest_extraction_run(pp_id)
    stats = json.loads(run['stats_json'])
    check('提取命中率高', stats['hit_fields'] >= 10, '命中 %d/%d' % (stats['hit_fields'], stats['total_fields']))

    r = client.get('/p/%s/review' % pp_id)
    check('核对页可访问', r.status_code == 200)
    fs = repo.field_stats(pp_id, 'safety_butler')
    check('必填字段共 34 项', fs['required_total'] == 34, '必填 %d 项' % fs['required_total'])

    # ---------------------------------------------------------------- 场景 C：低置信度
    section('场景 C · 低置信度内容')
    fv = repo.field_values(pp_id)
    check('存在待确认字段', fs['pending_confirm'] >= 1, '%d 项' % fs['pending_confirm'])
    cc = fv['camera_count']
    check('摄像头数量标记为待确认', cc['status'] == 'pending_confirm', '值=%s' % cc['value_json'])
    check('待确认字段展示原文依据', len(cc['source_quote']) > 10,
          cc['source_quote'][:40])

    r = client.post('/p/%s/field' % pp_id, json={'key': 'camera_count', 'value': 68})
    res = r.get_json()
    check('人工确认后状态更新', res['ok'] and res['status'] == 'confirmed')
    check('人工确认后计入受保护', repo.field_values(pp_id)['camera_count']['protected'] == 1)

    # ---------------------------------------------------------------- 场景 B：必填缺失
    section('场景 B · 必填项缺失')
    r = client.post('/projects/create', data={'name': '自测-残缺样例', 'category': '内部项目'},
                    follow_redirects=True)
    pid2 = db.query("SELECT id FROM projects WHERE name='自测-残缺样例'")[0]['id']
    client.post('/projects/%s/products/add' % pid2, data={'product_type': 'safety_butler'})
    pp2 = repo.list_products(pid2)[0]['id']
    text2 = open(os.path.join(paths.SAMPLES_DIR, 'transcript_missing.md'), encoding='utf-8').read()
    client.post('/p/%s/survey' % pp2, data={'transcript': text2}, follow_redirects=True)
    client.post('/p/%s/extract' % pp2, follow_redirects=True)

    from core.validate import validate
    ok2, issues2 = validate(pp2, 'safety_butler')
    check('残缺样例校验不通过', not ok2)
    errs = [i for i in issues2 if i['level'] == 'error']
    check('明确指出必须补填项', len(errs) >= 20, '%d 项 error' % len(errs))

    r = client.post('/p/%s/generate' % pp2, data={'artifacts': ['value_card']},
                    follow_redirects=True)
    check('补齐前阻止生成', '存在未通过的必填校验' in r.data.decode('utf-8')
          or repo.artifact_runs(pp2) == [])

    fill = {
        'project_name': '测试产业园', 'project_source': '外部项目',
        'business_type': '园区', 'build_type': '旧改',
        'land_area': 80000, 'building_area': 50000,
        'site_address': '苏州市相城区测试路 1 号',
        'contract_years': '新签合同，服务期3年', 'property_company': '测试物业服务有限公司',
        'project_owner': '测试业主有限公司', 'service_type': 'FM',
        'business_desc': '1-3层厂房，4层办公', 'fee_mode': '包干制',
        'mgmt_scope': '全委',
        'monitor_center_count': 1,
        'camera_brand': '海康', 'camera_type': '数字摄像头', 'camera_count': 68,
        'storage_brand': '海康', 'storage_type': 'NVR', 'storage_model': 'DS-8664N',
        'storage_count': 2, 'storage_protocol': 'GB/T28181-2022',
        'has_internet': '有', 'switch_brand': '华为', 'switch_port_type': '千兆',
        'switch_ports_free': 8, 'network_topo': '有', 'network_admin': '王工 13900000000',
        'has_cabinet': '有（机柜深度1米，剩余20U，供电插口8个）',
        'expected_launch': '下个月底', 'decision_maker': '李总', 'customer_contact': '陈主任',
        'has_budget': '有预算，约二十万一年',
    }
    for k, v in fill.items():
        client.post('/p/%s/field' % pp2, json={'key': k, 'value': v})
    ok2b, issues2b = validate(pp2, 'safety_butler')
    check('补齐后校验通过', ok2b,
          '剩余 error：%s' % [i['field'] for i in issues2b if i['level'] == 'error'])

    # 补齐主项目剩余必填字段（文字稿未覆盖的部分），确保后续生成步骤校验通过
    for k, v in fill.items():
        client.post('/p/%s/field' % pp_id, json={'key': k, 'value': v})
    ok_a, issues_a = validate(pp_id, 'safety_butler')
    check('主项目补齐必填后校验通过', ok_a,
          '剩余 error：%s' % [i['field'] for i in issues_a if i['level'] == 'error'])

    # ---------------------------------------------------------------- 生成与打包
    section('步骤 6-7 · 生成物与 ZIP 打包')
    keys = ['survey_result', 'value_card', 'delivery', 'data_request']
    r = client.post('/p/%s/generate' % pp_id, data={'artifacts': keys}, follow_redirects=True)
    check('生成任务触发', r.status_code == 200)
    runs = {x['artifact_key']: x for x in repo.artifact_runs(pp_id)}
    ok_runs = [k for k in keys if runs.get(k) and runs[k]['status'] == 'success']
    check('四类生成物全部成功', len(ok_runs) == 4, '成功 %d/4：%s' % (len(ok_runs), ok_runs))

    for k in keys:
        files = json.loads(runs[k]['files_json'])
        names = [f['name'] for f in files]
        check('「%s」产出文件' % k, len(files) >= 2, '、'.join(names))
        for f in files:
            check('  文件非空：%s' % f['name'], os.path.getsize(f['path']) > 200,
                  '%d 字节' % os.path.getsize(f['path']))

    # ---------------------------------------------------------------- 场景 E：部分生成失败
    section('场景 E · 部分生成失败')
    tpl = os.path.join(paths.artifact_template_dir('safety_butler'), 'value_card.md.j2')
    bak = tpl + '.bak'
    shutil.move(tpl, bak)
    client.post('/p/%s/regenerate' % pp_id, data={'artifact': 'value_card'},
                follow_redirects=True)
    runs2 = {x['artifact_key']: x for x in repo.artifact_runs(pp_id)}
    check('单项失败被记录', runs2['value_card']['status'] == 'failed',
          runs2['value_card']['error'][:60])
    others = [k for k in keys if k != 'value_card' and runs2[k]['status'] == 'success']
    check('其他成功结果不受影响', len(others) == 3, '仍成功 %d 项' % len(others))
    shutil.move(bak, tpl)
    client.post('/p/%s/regenerate' % pp_id, data={'artifact': 'value_card'},
                follow_redirects=True)
    runs3 = {x['artifact_key']: x for x in repo.artifact_runs(pp_id)}
    check('失败项可单独重试成功', runs3['value_card']['status'] == 'success')

    # ---------------------------------------------------------------- 打包
    section('打包下载')
    r = client.post('/p/%s/package' % pp_id,
                    data={'artifacts': [k for k in keys]}, follow_redirects=False)
    check('ZIP 接口返回成功', r.status_code == 200)
    exp = repo.latest_export(pp_id)
    check('导出记录已保存', exp is not None and os.path.isfile(exp['file_path']),
          '%s (%.1f KB)' % (exp['file_name'], exp['size'] / 1024) if exp else '')

    with zipfile.ZipFile(exp['file_path']) as z:
        names = z.namelist()
        mf = json.loads(z.read('manifest.json').decode('utf-8'))
    check('ZIP 含 manifest.json', 'manifest.json' in names)
    check('ZIP 含 README.txt', 'README.txt' in names)
    check('ZIP 目录按配置命名',
          any(n.startswith('01_踏勘结果/') for n in names) and
          any(n.startswith('03_交付内容/') for n in names),
          '共 %d 个文件' % len(names))
    check('manifest 记录模板版本', mf['product']['template_version'] != '')
    check('manifest 记录提取信息', mf['input'].get('extraction', {}).get('provider') == 'rule')
    check('文件名包含项目与产品',
          '自测-新宇精密安全管家' in exp['file_name'] and '安全管家' in exp['file_name'],
          exp['file_name'])

    # ---------------------------------------------------------------- 场景 F：重新进入
    section('场景 F · 退出后重新进入')
    r = client.get('/logout', follow_redirects=True)
    check('退出后回到分部入口', '选择分部' in r.data.decode('utf-8'))
    r = client.get('/p/%s/review' % pp_id, follow_redirects=True)
    check('未登录无法访问项目', '选择分部' in r.data.decode('utf-8'))
    client.post('/login', data={'dept_key': 'dept1', 'password': '000000'})
    r = client.get('/projects/%s' % pid)
    html = r.data.decode('utf-8')
    check('重新登录后项目进度恢复', '100%' in html or '已完成' in html, '进度已恢复')
    r = client.get('/p/%s/result' % pp_id)
    check('已生成结果可重新查看', r.status_code == 200 and '结果中心' in r.data.decode('utf-8'))
    src2 = repo.latest_source(pp_id)
    check('原始文字稿完整保留', src2['content'] == text)

    # ---------------------------------------------------------------- 项目复制/归档/删除
    section('项目复制 / 归档 / 删除')
    client.post('/projects/%s/copy' % pid, follow_redirects=True)
    copies = db.query("SELECT * FROM projects WHERE name LIKE '自测-新宇精密安全管家（副本）'")
    check('复制生成副本', len(copies) == 1)
    if copies:
        cid = copies[0]['id']
        cpp = repo.list_products(cid)[0]
        check('副本复制了结构化内容', len(repo.field_values(cpp['id'])) >= 40,
              '%d 个字段' % len(repo.field_values(cpp['id'])))
        check('副本复制了原始文字稿', repo.latest_source(cpp['id']) is not None)
        repo.purge_project(cid)
    client.post('/projects/%s/archive' % pid2, follow_redirects=True)
    check('归档后不在默认列表',
          all(p['id'] != pid2 for p in repo.list_projects('dept1')))
    check('归档数据完整保留', repo.get_project(pid2) is not None)
    client.post('/projects/%s/delete' % pid2, follow_redirects=True)
    check('删除后可在已删除列表找回', any(p['id'] == pid2 for p in repo.list_deleted('dept1')))
    client.post('/projects/%s/restore' % pid2, follow_redirects=True)
    repo.purge_project(pid2)
    check('彻底删除清理干净', repo.get_project(pid2) is None)

    # ---------------------------------------------------------------- 全路由冒烟
    section('页面冒烟（每个 GET 路由至少 200 一次）')
    pages = [
        ('分部入口', '/'),
        ('修改分部密码页', '/password'),
        ('项目中心', '/projects'),
        ('项目概览', '/projects/%s' % pid),
        ('调研输入', '/p/%s/survey' % pp_id),
        ('信息核对', '/p/%s/review' % pp_id),
        ('预览与生成', '/p/%s/preview' % pp_id),
        ('结果中心', '/p/%s/result' % pp_id),
        ('ZIP 下载', '/p/%s/package/download' % pp_id),
    ]
    for name, url in pages:
        r = client.get(url, follow_redirects=True)
        check('页面可访问：%s' % name, r.status_code == 200, 'HTTP %d' % r.status_code)

    preview_html = client.get('/p/%s/preview' % pp_id).data.decode('utf-8')
    check('预览页不再包含费用或报价入口',
          '费用估算' not in preview_html and '报价设置' not in preview_html)
    check('历史报价设置接口已下线',
          client.post('/p/%s/amount' % pp_id).status_code == 404)
    check('已下线生成物不能通过旧链接访问',
          client.get('/p/%s/file/cost_estimate/费用估算卡片.html' % pp_id).status_code == 404)

    mf_files = json.loads(runs['survey_result']['files_json'])
    fn = mf_files[0]['name']
    r = client.get('/p/%s/file/survey_result/%s' % (pp_id, fn), follow_redirects=True)
    check('单文件预览/下载可访问', r.status_code == 200, fn)

    # 改密会真实写入 config/departments.json，先备份、测完还原
    from core.paths import CONFIG_DIR as _CD
    dept_file = os.path.join(_CD, 'departments.json')
    dept_bak = open(dept_file, encoding='utf-8').read()
    try:
        r = client.post('/password', data={'old_password': '000000',
                                           'new_password': '123456',
                                           'confirm_password': '654321'},
                        follow_redirects=True)
        check('两次新密码不一致被拒绝', '不一致' in r.data.decode('utf-8'))
        client.post('/password', data={'old_password': '000000',
                                       'new_password': '123456',
                                       'confirm_password': '123456'},
                    follow_redirects=True)
        client.get('/logout', follow_redirects=True)
        r = client.post('/login', data={'dept_key': 'dept1', 'password': '000000'},
                        follow_redirects=True)
        check('旧密码失效', '不正确' in r.data.decode('utf-8'))
        r = client.post('/login', data={'dept_key': 'dept1', 'password': '123456'},
                        follow_redirects=True)
        check('新密码可登录', '项目' in r.data.decode('utf-8') or r.status_code == 200)
    finally:
        with open(dept_file, 'w', encoding='utf-8') as f:
            f.write(dept_bak)

    # ---------------------------------------------------------------- 汇总
    section('自测汇总')
    bad = [r for r in results if not r[0]]
    print('总检查项：%d    通过：%d    失败：%d' % (len(results), len(results) - len(bad), len(bad)))
    if bad:
        print('\n未通过项：')
        for _, n, d in bad:
            print('  %s %s %s' % (FAIL, n, d))

    # 清理
    db._local.conn = None
    db.db_path = orig_db
    shutil.rmtree(paths.OUTPUT_DIR, ignore_errors=True)
    shutil.rmtree(paths.EXPORT_DIR, ignore_errors=True)
    for ext in ('', '-wal', '-shm'):
        if os.path.exists(tmp_db + ext):
            try:
                os.remove(tmp_db + ext)
            except OSError:
                pass

    return 0 if not bad else 1


if __name__ == '__main__':
    sys.exit(main())
