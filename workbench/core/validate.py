"""完整性校验与业务规则校验

输出统一结构：
    [{'level':'error'|'warn', 'field':'key', 'message':'...'}]
只有 error 才会阻止进入生成步骤。
"""
import json
import re
from datetime import datetime

from .config import field_map
from .repo import field_values
from .choices import other_detail_error


def parse_value(value_json):
    try:
        return json.loads(value_json)
    except Exception:
        return None


def is_empty(v):
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list, dict)):
        return len(v) == 0
    return False


def as_number(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r'-?\d+(?:\.\d+)?', v.replace(',', ''))
        if m:
            return float(m.group(0))
    return None


def validate_fields(pp_id, product_type):
    """对已保存的字段值做必填、格式与业务规则校验。"""
    fmap = field_map(product_type)
    values = field_values(pp_id)
    issues = []
    get = lambda k: parse_value(values[k]['value_json']) if k in values else None

    for key, f in fmap.items():
        v = get(key)
        empty = is_empty(v)
        label = f['label']

        if f.get('required') and empty:
            issues.append({'level': 'error', 'field': key,
                           'message': '【%s】为必填项，文字稿中未找到依据，请人工补填。' % label})
            continue
        if empty:
            continue

        error = other_detail_error(f, v)
        if error:
            issues.append({'level': 'error', 'field': key,
                           'message': '【%s】%s' % (label, error)})

        # 类型与格式
        if f['type'] == 'number':
            n = as_number(v)
            if n is None:
                issues.append({'level': 'error', 'field': key,
                               'message': '【%s】需要填写数字，当前值无法识别。' % label})
            else:
                rules = f.get('validate') or {}
                if rules.get('min') is not None and n < rules['min']:
                    issues.append({'level': 'error', 'field': key, 'message': rules.get('message')})
                if rules.get('max') is not None and n > rules['max']:
                    issues.append({'level': 'error', 'field': key, 'message': rules.get('message')})

        if f['type'] == 'date' and isinstance(v, str):
            try:
                datetime.strptime(v.strip(), '%Y-%m-%d')
            except ValueError:
                issues.append({'level': 'warn', 'field': key,
                               'message': '【%s】建议格式为 YYYY-MM-DD。' % label})

        pat = (f.get('validate') or {}).get('pattern')
        if pat and isinstance(v, str) and not re.match(pat, v.strip()):
            issues.append({'level': 'warn', 'field': key,
                           'message': (f.get('validate') or {}).get('message')
                                      or '【%s】格式看起来不合法。' % label})

        if f['type'] == 'select' and f.get('options'):
            allowed = [o['value'] for o in f['options']]
            if not isinstance(v, str):
                issues.append({'level': 'error', 'field': key, 'message': '【%s】需要单选文字值。' % label})
            elif v not in allowed and not f.get('allow_custom'):
                issues.append({'level': 'warn', 'field': key,
                               'message': '【%s】取值不在候选范围内：%s。' % (label, '、'.join(allowed))})

        if f['type'] == 'multiselect' and f.get('options'):
            # Previously these fields held free text. Read without rewriting old projects.
            if isinstance(v, str) and f.get('allow_custom'):
                continue
            if not isinstance(v, list) or any(not isinstance(x, str) for x in v):
                issues.append({'level': 'error', 'field': key, 'message': '【%s】需要多选文字列表。' % label})
                continue
            allowed = set(o['value'] for o in f['options'])
            bad = [x for x in (v or []) if x not in allowed]
            if bad and not f.get('allow_custom'):
                issues.append({'level': 'warn', 'field': key,
                               'message': '【%s】包含无效取值：%s。' % (label, '、'.join(bad))})

    issues.extend(_business_rules(get, fmap))
    return issues


def _business_rules(get, fmap):
    """跨字段业务规则。规则依据：收资清单『备注』列。"""
    out = []

    # 摄像头类型：纯模拟 → 无法配置安全管家（硬门槛）
    cam_type = get('camera_type')
    if cam_type and '模拟' in str(cam_type) and '混合' not in str(cam_type):
        out.append({'level': 'error', 'field': 'camera_type',
                    'message': '摄像头类型为纯模拟，无法配置安全管家（需先改造为数字或混合摄像头）。'})

    return out


def validate(pp_id, product_type):
    issues = validate_fields(pp_id, product_type)
    ok = not any(i['level'] == 'error' for i in issues)
    return ok, issues
