"""完整性校验与业务规则校验

输出统一结构：
    [{'level':'error'|'warn', 'field':'key', 'message':'...'}]
只有 error 才会阻止进入生成步骤。
"""
import json
import math
import re
from datetime import datetime

from .config import field_map, product_config
from .repo import field_values, shared_field_conflicts
from .choices import other_detail_error
from .field_conditions import age_entries, required_now, needs_reconfirmation


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
    conflicts = shared_field_conflicts(pp_id)
    issues = []
    get = lambda k: parse_value(values[k]['value_json']) if k in values else None

    for key, f in fmap.items():
        if needs_reconfirmation(values.get(key), f):
            issues.append({'level': 'error', 'field': key,
                           'message': '【%s】要求已更新，历史答案已保留，请按新要求重新保存确认。' % f['label']})
        if key in conflicts:
            issues.append({'level': 'error', 'field': key, 'kind': 'shared_conflict',
                           'message': '【%s】在不同管家中有不同的历史答案，请选择或重新填写后共享。' % f['label']})
            continue
        v = get(key)
        empty = is_empty(v)
        label = f['label']

        if required_now(f, values) and empty:
            issues.append({'level': 'error', 'field': key,
                           'message': '【%s】为必填项，文字稿中未找到依据，请人工补填。' % label})
            continue
        if empty:
            continue

        if f.get('validate', {}).get('age_entries') and age_entries(v) is None:
            issues.append({'level': 'error', 'field': key,
                           'message': '【%s】请每行填写“主机编号：13年”或“主机编号：2013-10-27”，用分号或换行分隔；未知年限须核实后填写。' % label})

        if f['type'] == 'attachment':
            from . import paths
            from pathlib import Path
            valid = isinstance(v, list) and all(isinstance(x, dict) and
                re.fullmatch(r'[0-9a-f]{32}', str(x.get('id', ''))) and
                (Path(paths.UPLOAD_DIR) / 'field_attachments' / pp_id / key / x['id']).is_file()
                for x in v)
            if not valid:
                issues.append({'level': 'error', 'field': key, 'message': '【%s】附件缺失，请重新上传。' % label})
            condition = f.get('required_if', {})
            if valid and condition.get('operator') == 'age_at_least':
                for host, age in (age_entries(get(condition['field'])) or {}).items():
                    if age < condition['value']:
                        continue
                    for kind in f['upload']['record_kinds']:
                        if not any(x.get('host') == host and x.get('kind') == kind for x in v):
                            issues.append({'level': 'error', 'field': key,
                                           'message': '【%s】请上传%s的%s，主机编号须与出厂年限中一致。' % (label, host, kind)})

        error = other_detail_error(f, v)
        if error:
            issues.append({'level': 'error', 'field': key,
                           'message': '【%s】%s' % (label, error)})

        if f['type'] == 'table':
            from . import rules as rule_engine
            output_def = rule_engine.field_output(product_type, key)
            if output_def and isinstance(v, list) and all(isinstance(r, dict) for r in v):
                v = rule_engine.materialize(output_def, v)
            issues.extend(table_issues(f, v))

        # 类型与格式
        if f['type'] == 'number':
            n = as_number(v)
            if n is None:
                issues.append({'level': 'error', 'field': key,
                               'message': '【%s】需要填写数字，当前值无法识别。' % label})
            else:
                rules = f.get('validate') or {}
                if rules.get('integer') and (isinstance(v, bool) or not math.isfinite(n)
                        or not n.is_integer() or (isinstance(v, str) and
                        not re.fullmatch(r'-?\d+(?:\.0+)?', v.strip()))):
                    issues.append({'level': 'error', 'field': key,
                                   'message': rules.get('message') or '【%s】需要填写整数。' % label})
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
    issues.extend(eligibility_issues(product_type, get))
    issues.extend(rules_issues(product_type, values))
    return issues


def rules_issues(product_type, values):
    """规则库驱动的录入校验：换算不出结果的行就地报错，不等到生成 Excel 才暴露。

    没有 rules.json 的产品返回空列表，因此公共校验流程不需要知道产品是谁
    （ARCHITECTURE §1.5：公共代码不用产品名写分支）。
    """
    from . import rules
    if not rules.has_rules(product_type):
        return []
    parsed = {key: parse_value(row['value_json']) for key, row in values.items()}
    return rules.field_issues(product_type, parsed)


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


def eligibility_issues(product_type, get):
    """只读取当前产品的准入配置，缺失值交给必填校验。"""
    out = []
    for rule in product_config(product_type).get('eligibility_rules', []):
        value = get(rule['field'])
        if is_empty(value):
            continue
        failed = (rule['operator'] == 'contains_any' and
                  (not isinstance(value, list) or not any(x in value for x in rule['values'])))
        failed |= rule['operator'] == 'not_equals' and value == rule['value']
        if failed:
            out.append(dict(level='error', field=rule['field'], message=rule['message'], kind='eligibility'))
    return out


def table_issues(field, value):
    """允许不完整行保存，但生成前逐列验证，防止空行绕过必填。"""
    out = []
    def error(message):
        out.append(dict(level='error', field=field['key'], message=message))
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        error('【%s】需要设备行列表。' % field['label'])
        return out
    for index, row in enumerate(value, 1):
        for col in field['columns']:
            v = row.get(col['key'])
            prefix = '第%d行【%s】' % (index, col['label'])
            if is_empty(v):
                if col.get('required'): error(prefix + '为必填项。')
                continue
            if col.get('type') == 'number':
                try:
                    n = float(v)
                    valid = not isinstance(v, bool) and math.isfinite(n)
                    valid &= n >= col.get('min', float('-inf'))
                    valid &= not col.get('exclusive_min') or n > col.get('min', 0)
                    valid &= not col.get('integer') or n.is_integer()
                except (ValueError, TypeError): valid = False
                if not valid: error(prefix + col.get('message', '需要符合范围与整数约束的有效数字。'))
            elif col.get('type') == 'select':
                if v not in [o['value'] for o in col['options']]: error(prefix + '不在候选范围内。')
            elif not isinstance(v, str): error(prefix + '需要文字。')
        if any(x in str(row.get('equipment', '')) for x in field.get('validate', {}).get('excluded_equipment', [])):
            error('第%d行：%s' % (index, field['validate'].get('excluded_message', '包含不应登记的设备，请删除该行。')))
    return out
