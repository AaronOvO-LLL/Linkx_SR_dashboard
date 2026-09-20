#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""预置演示数据：向真实演示库灌入两个示例项目。

通过真实 HTTP 接口（Flask test_client）执行，与页面操作完全同路径：

  1. 示例·新宇精密产业园 —— 完整走通 7 步：完整样例文字稿 → AI 提取
     → 人工确认低置信字段 → 补充巡检数量 → 生成五类材料 → 打包 ZIP。
  2. 示例·东坊仓储园（必填待补齐） —— 只录入残缺样例并解析，
     保留红色"必须补填"状态，供体验必填拦截与补填流程。

可重复执行：脚本会先清掉旧的「示例·」项目再重建。

运行： python seed_demo.py
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import app as web  # noqa: E402
from core import db, repo  # noqa: E402
from core.paths import SAMPLES_DIR  # noqa: E402

PREFIX = '示例·'
NAME_FULL = PREFIX + '新宇精密产业园'
NAME_MISSING = PREFIX + '东坊仓储园（必填待补齐）'


def main():
    db.init_db()

    # 幂等：清掉旧的示例项目
    for row in db.query("SELECT id FROM projects WHERE name LIKE ?", (PREFIX + '%',)):
        repo.purge_project(row['id'])
        print('· 已清理旧示例：%s' % row['id'])

    client = web.app.test_client()
    client.post('/login', data={'dept_key': 'dept1', 'password': '000000'})

    # ------------------------------------------------ 示例 1：完整流程
    client.post('/projects/create', data={
        'name': NAME_FULL, 'category': '外部项目',
        'customer_name': '苏州新宇精密制造有限公司',
        'location': '苏州市吴中区临湖镇兴业路 88 号', 'owner': '张伟',
    })
    pid = db.query('SELECT id FROM projects WHERE name=?', (NAME_FULL,))[0]['id']
    client.post('/projects/%s/products/add' % pid, data={'product_type': 'safety_butler'})
    pp = repo.list_products(pid)[0]['id']

    text = open(os.path.join(SAMPLES_DIR, 'transcript_full.md'), encoding='utf-8').read()
    client.post('/p/%s/survey' % pp, data={'transcript': text}, follow_redirects=True)
    client.post('/p/%s/extract' % pp, follow_redirects=True)

    # 人工确认自我修正过的低置信字段（楼栋数量：四栋→五栋）
    client.post('/p/%s/field' % pp, json={'key': 'camera_count', 'value': 68})
    # 踏勘补录存储设备事实（协议国标 + 品牌海康），NVR 档位由字段自动判定为国标 0.25 万
    client.post('/p/%s/field' % pp, json={'key': 'storage_protocol', 'value': 'GB/T28181-2022'})
    client.post('/p/%s/field' % pp, json={'key': 'storage_brand', 'value': '海康'})
    keys = ['survey_result', 'value_card', 'delivery', 'data_request']
    client.post('/p/%s/generate' % pp, data={'artifacts': keys}, follow_redirects=True)
    client.post('/p/%s/package' % pp, data={'artifacts': keys}, follow_redirects=True)

    stats = repo.field_stats(pp, 'safety_butler')
    runs = repo.artifact_runs(pp)
    exp = repo.latest_export(pp)
    print('✅ %s' % NAME_FULL)
    print('   字段 %d/%d 已填写，必填完成 %d/%d，待确认 %d' % (
        stats['filled'], stats['total'], stats['required_done'],
        stats['required_total'], stats['pending_confirm']))
    print('   生成材料 %d 类：%s' % (len(runs), '、'.join(sorted(r['artifact_key'] for r in runs))))
    print('   打包：%s（%.1f KB）' % (exp['file_name'], exp['size'] / 1024))

    # ------------------------------------------------ 示例 2：必填待补齐
    client.post('/projects/create', data={
        'name': NAME_MISSING, 'category': '内部项目',
        'customer_name': '东坊仓储（苏州）有限公司', 'owner': '王磊',
    })
    pid2 = db.query('SELECT id FROM projects WHERE name=?', (NAME_MISSING,))[0]['id']
    client.post('/projects/%s/products/add' % pid2, data={'product_type': 'safety_butler'})
    pp2 = repo.list_products(pid2)[0]['id']

    text2 = open(os.path.join(SAMPLES_DIR, 'transcript_missing.md'), encoding='utf-8').read()
    client.post('/p/%s/survey' % pp2, data={'transcript': text2}, follow_redirects=True)
    client.post('/p/%s/extract' % pp2, follow_redirects=True)

    stats2 = repo.field_stats(pp2, 'safety_butler')
    print('✅ %s' % NAME_MISSING)
    print('   字段 %d/%d 已填写，必须补填 %d 项（体验必填拦截流程）' % (
        stats2['filled'], stats2['total'], stats2['required_missing']))

    print('\n完成。打开 http://127.0.0.1:8770 即可查看（一分部，密码 000000）。')


if __name__ == '__main__':
    main()
