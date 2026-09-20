# -*- coding: utf-8 -*-
"""生成 6 个差异化演示项目。

差异维度：
  1. 流程完整度：completed / pending_generate / pending_fill
  2. 现场条件：国标协议、设备品牌、网络和机柜条件
  3. 特殊提示：纯模拟硬门槛、超 300 路、无外网、交换机端口不足

用法：python seed_projects.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as web  # noqa: E402
from core import db, repo  # noqa: E402
from core.paths import SAMPLES_DIR  # noqa: E402

PREFIX = '示例·'
ALL_ARTIFACTS = ['survey_result', 'value_card', 'delivery', 'data_request']


def read_sample(name):
    with open(os.path.join(SAMPLES_DIR, name), encoding='utf-8') as f:
        return f.read()


def build(client, meta, transcript, fields=None, artifacts=None, package=False):
    """建项目 → 加产品 → 录原文 → 提取 → 补字段 → 生成/打包。"""
    full_name = PREFIX + meta['name']
    client.post('/projects/create', data={
        'name': full_name,
        'category': meta.get('category', '外部项目'),
        'customer_name': meta['customer_name'],
        'location': meta.get('location', ''),
        'owner': meta.get('owner', ''),
    })
    pid = db.query('SELECT id FROM projects WHERE name=?', (full_name,))[0]['id']
    client.post('/projects/%s/products/add' % pid, data={'product_type': 'safety_butler'})
    pp = repo.list_products(pid)[0]['id']

    client.post('/p/%s/survey' % pp, data={'transcript': read_sample(transcript)},
                follow_redirects=True)
    client.post('/p/%s/extract' % pp, follow_redirects=True)

    for k, v in (fields or {}).items():
        client.post('/p/%s/field' % pp, json={'key': k, 'value': v})
    if artifacts:
        client.post('/p/%s/generate' % pp, data={'artifacts': artifacts},
                    follow_redirects=True)
        if package:
            client.post('/p/%s/package' % pp, data={'artifacts': artifacts},
                        follow_redirects=True)
    return pid, pp


def report(title, pp, note=''):
    st = repo.field_stats(pp, 'safety_butler')
    runs = repo.artifact_runs(pp)
    ok_runs = [r for r in runs if r['status'] == 'success']
    pct, status = repo.progress_of(pp, 'safety_butler')

    print('\n✅ %s' % title)
    if note:
        print('   场景：%s' % note)
    print('   进度 %d%% · 状态 %s' % (pct, status))
    print('   字段 %d/%d 已填，必填 %d/%d，待确认 %d，必须补填 %d'
          % (st['filled'], st['total'], st['required_done'],
             st['required_total'], st['pending_confirm'], st['required_missing']))
    print('   生成材料 %d/%d 类' % (len(ok_runs), len(runs)))


# ============================================================ 案例定义

# 1) 完整标杆：园区 · 国标协议 · 全流程完成
CASE1 = dict(
    meta=dict(name='新宇精密产业园（完整标杆）', category='外部项目',
              customer_name='苏州新宇精密制造有限公司',
              location='苏州市吴中区临湖镇兴业路 88 号', owner='张伟'),
    transcript='transcript_full.md',
    fields={
        'project_name': '新宇精密产业园', 'project_source': '外部项目',
        'business_type': '园区', 'build_type': '旧改',
        'land_area': 40000, 'building_area': 30000,
        'site_address': '苏州市吴中区临湖镇兴业路 88 号',
        'contract_years': '新签合同，服务期 5 年',
        'property_company': '苏州新宇物业管理有限公司',
        'project_owner': '苏州新宇精密制造有限公司',
        'service_type': 'FM',
        'business_desc': '两栋生产车间、一栋办公楼加宿舍、东侧新建车间',
        'fee_mode': '包干制', 'mgmt_scope': '大物业',
        'monitor_center_count': 1,
        'camera_brand': '海康', 'camera_type': '数字摄像头', 'camera_count': 68,
        'storage_brand': '海康', 'storage_type': 'NVR',
        'storage_model': 'DS-8664NXI-I8', 'storage_count': 2,
        'storage_protocol': 'GB/T28181-2022',
        'has_internet': '有', 'switch_brand': 'H3C',
        'switch_port_type': '千兆', 'switch_ports_free': 8,
        'network_topo': '无', 'network_admin': '陈主任 13812345678',
        'has_cabinet': '有，弱电间标准机柜，深度 600，剩余 12U，供电正常',
        'expected_launch': '下个月底前（年底验厂）',
        'decision_maker': '陈主任', 'customer_contact': '陈主任 13812345678',
        'has_budget': '有，约二十万一年',
        'drawings': '有', 'equipment_ledger': '无', 'staffing_table': '有',
        'cabinet_condition': '具备', 'server_noise_ok': '能',
    },
    artifacts=ALL_ARTIFACTS, package=True,
)

# 2) 必填待补齐：只调研提取，不补填，演示必填拦截
CASE2 = dict(
    meta=dict(name='东坊仓储园（必填待补齐）', category='内部项目',
              customer_name='东坊仓储（苏州）有限公司', owner='王磊'),
    transcript='transcript_missing.md',
)

# 3) 纯模拟硬门槛：住区 · 模拟摄像头 → 无法配置安全管家；端口 2 个不足
CASE3 = dict(
    meta=dict(name='锦绣家园小区（模拟摄像头·硬门槛）', category='外部项目',
              customer_name='苏州锦绣家园物业管理有限公司',
              location='苏州市姑苏区锦绣路 45 号', owner='李强'),
    transcript='transcript_analog.md',
    fields={
        'project_name': '锦绣家园小区', 'project_source': '外部项目',
        'business_type': '住区', 'build_type': '旧改',
        'land_area': 20000, 'building_area': 50000,
        'site_address': '苏州市姑苏区锦绣路 45 号',
        'contract_years': '存量合同，服务期 3 年',
        'property_company': '苏州锦绣家园物业管理有限公司',
        'project_owner': '锦绣家园业主委员会',
        'service_type': 'FM',
        'business_desc': '纯住宅小区，12 栋住宅楼，无商业配套',
        'fee_mode': '酬金制', 'mgmt_scope': '小物业',
        'monitor_center_count': 1,
        'camera_brand': '大华', 'camera_type': '模拟摄像头', 'camera_count': 32,
        'storage_brand': '大华', 'storage_type': 'DVR',
        'storage_model': 'DH-DVR1604', 'storage_count': 1,
        'storage_protocol': '其他',
        'has_internet': '有', 'switch_brand': 'H3C',
        'switch_port_type': '百兆', 'switch_ports_free': 2,
        'network_topo': '无', 'network_admin': '赵工 13900002222',
        'has_cabinet': '有，门卫室机柜，深度 600，剩余 8U',
        'expected_launch': '年底前（业委会换届前）',
        'decision_maker': '周主任', 'customer_contact': '刘经理 13900001111',
        'has_budget': '暂未定，需业主大会表决',
        'drawings': '有', 'equipment_ledger': '无', 'staffing_table': '有',
        'cabinet_condition': '具备', 'server_noise_ok': '不能',
    },
    artifacts=ALL_ARTIFACTS, package=False,
)

# 4) 超 300 路大项目：420 路 · 重点项目
CASE4 = dict(
    meta=dict(name='临港智造基地（420路·需算力卡）', category='重点项目',
              customer_name='临港智造（上海）实业有限公司',
              location='上海市浦东新区临港新片区腾飞路 1 号', owner='王磊'),
    transcript='transcript_large.md',
    fields={
        'project_name': '临港智造基地', 'project_source': '重点项目',
        'business_type': '园区', 'build_type': '新建',
        'land_area': 150000, 'building_area': 90000,
        'site_address': '上海市浦东新区临港新片区腾飞路 1 号',
        'contract_years': '新签合同，服务期 10 年',
        'property_company': '临港智造物业分公司',
        'project_owner': '临港智造（上海）实业有限公司',
        'service_type': 'PF',
        'business_desc': '8 栋生产厂房加 1 栋五层研发楼',
        'fee_mode': '包干制', 'mgmt_scope': '大物业',
        'monitor_center_count': 2,
        'camera_brand': '海康', 'camera_type': '数字摄像头', 'camera_count': 420,
        'storage_brand': '海康', 'storage_type': 'CVR',
        'storage_model': 'DS-A71048R', 'storage_count': 6,
        'storage_protocol': 'GB/T28181-2022',
        'has_internet': '有', 'switch_brand': '华为',
        'switch_port_type': '万兆', 'switch_ports_free': 24,
        'network_topo': '有', 'network_admin': '老周 13700004444',
        'has_cabinet': '有，数据中心机房，深度 1200，剩余 20U，双路 UPS',
        'expected_launch': '明年一季度（二期年底封顶）',
        'decision_maker': '陈总', 'customer_contact': '孙主管 13700003333',
        'has_budget': '有，约一年一百多万',
        'drawings': '有', 'equipment_ledger': '有', 'staffing_table': '有',
        'cabinet_condition': '具备', 'server_noise_ok': '能',
    },
    artifacts=ALL_ARTIFACTS, package=True,
)

# 5) 无外网 + 非主流设备 · 端口仅 1 个 · 医疗之外的极端场景
CASE5 = dict(
    meta=dict(name='青山矿区（无外网·非主流设备）', category='外部项目',
              customer_name='青山矿业（安徽）有限公司',
              location='安徽省池州市青山县矿区 3 号井', owner='张伟'),
    transcript='transcript_nointernet.md',
    fields={
        'project_name': '青山矿区', 'project_source': '外部项目',
        'business_type': '其他', 'build_type': '旧改',
        'land_area': 500000, 'building_area': 20000,
        'site_address': '安徽省池州市青山县矿区 3 号井',
        'contract_years': '内部协议，服务期 5 年',
        'property_company': '青山矿业后勤部（自管）',
        'project_owner': '青山矿业（安徽）有限公司',
        'service_type': 'PF',
        'business_desc': '矿井作业区、物料堆场、办公生活区',
        'fee_mode': '其他', 'mgmt_scope': '小物业',
        'monitor_center_count': 1,
        'camera_brand': '其他', 'camera_type': '混合', 'camera_count': 86,
        'storage_brand': '其他', 'storage_type': 'NVR',
        'storage_model': 'XVR-8800', 'storage_count': 2,
        'storage_protocol': '其他',
        'has_internet': '无', 'switch_brand': '其他',
        'switch_port_type': '百兆', 'switch_ports_free': 1,
        'network_topo': '无', 'network_admin': '老李 13600006666',
        'has_cabinet': '有，调度室旧机柜，深度 600，剩余 4U，供电仅 2 个插口',
        'expected_launch': '尽快，最好下个月（年底安监检查）',
        'decision_maker': '马矿长', 'customer_contact': '马矿长 13600005555',
        'has_budget': '有，约一年十几万',
        'drawings': '无', 'equipment_ledger': '无', 'staffing_table': '无',
        'cabinet_condition': '不具备', 'server_noise_ok': '不能',
    },
    artifacts=ALL_ARTIFACTS, package=False,
)

# 6) 待生成：字段已齐、校验通过，但停在生成前
CASE6 = dict(
    meta=dict(name='仁和医院新院区（待生成）', category='重点项目',
              customer_name='苏州市仁和医院',
              location='苏州市工业园区星湖街 328 号', owner='王磊'),
    transcript='transcript_medical.md',
    fields={
        'project_name': '仁和医院新院区', 'project_source': '重点项目',
        'business_type': '医疗', 'build_type': '新建',
        'land_area': 80000, 'building_area': 120000,
        'site_address': '苏州市工业园区星湖街 328 号',
        'contract_years': '新签合同，服务期 3 年',
        'property_company': '仁和后勤服务公司',
        'project_owner': '苏州市仁和医院',
        'service_type': 'FM',
        'business_desc': '门诊楼五层、住院楼二十二层、医技楼四层、地下两层停车场',
        'fee_mode': '包干制', 'mgmt_scope': '大物业',
        'monitor_center_count': 2,
        'camera_brand': '海康', 'camera_type': '数字摄像头', 'camera_count': 156,
        'storage_brand': '海康', 'storage_type': 'CVR',
        'storage_model': 'DS-A72048R', 'storage_count': 4,
        'storage_protocol': 'GB28181-2016',
        'has_internet': '有', 'switch_brand': '锐捷',
        'switch_port_type': '千兆', 'switch_ports_free': 16,
        'network_topo': '有', 'network_admin': '郑工 13500008888',
        'has_cabinet': '有，信息中心机房，深度 1000，剩余 15U，UPS 供电',
        'expected_launch': '年底前（明年一月开诊）',
        'decision_maker': '赵副院长', 'customer_contact': '吴科长 13500007777',
        'has_budget': '有，财政拨款约六十万一年',
        'drawings': '有', 'equipment_ledger': '有', 'staffing_table': '有',
        'cabinet_condition': '具备', 'server_noise_ok': '能',
    },
    artifacts=None, package=False,
)

CASES = [
    (CASE1, '园区 · 国标协议 · 全流程完成'),
    (CASE2, '信息不足 · 必填拦截 · 停在待补充'),
    (CASE3, '住区 · 纯模拟摄像头 · 硬门槛 + 端口不足'),
    (CASE4, '园区 · 420 路 · 大规模设备接入'),
    (CASE5, '矿区 · 无外网 · 非主流设备'),
    (CASE6, '医疗 · 字段齐备 · 停在待生成'),
]


def main():
    db.init_db()

    print('=' * 68)
    print('清空现有项目')
    print('=' * 68)
    for row in db.query('SELECT id, name FROM projects'):
        repo.purge_project(row['id'])
        print('· 已删除：%s' % row['name'])

    client = web.app.test_client()
    client.post('/login', data={'dept_key': 'dept1', 'password': '000000'})

    print('\n' + '=' * 68)
    print('生成 %d 个差异化项目' % len(CASES))
    print('=' * 68)

    for case, note in CASES:
        pid, pp = build(
            client, case['meta'], case['transcript'],
            fields=case.get('fields'),
            artifacts=case.get('artifacts'), package=case.get('package', False),
        )
        report(case['meta']['name'], pp, note)

    total = len(db.query('SELECT id FROM projects WHERE deleted=0'))
    print('\n' + '=' * 68)
    print('完成：当前共 %d 个项目' % total)
    print('=' * 68)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
