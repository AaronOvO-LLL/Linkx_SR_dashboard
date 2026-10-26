"""配置驱动的条件必填和主机出厂年限解析，不依赖 Web 或产品名称。"""
import json
import re
from datetime import date, datetime, timedelta, timezone


def age_entries(value, today=None):
    """每行/分号一台主机：编号：N年 或 编号：YYYY-MM-DD。无法判定时拒绝猜测。"""
    today = today or datetime.now(timezone(timedelta(hours=8))).date()
    if not isinstance(value, str) or not value.strip():
        return None
    result = {}
    for line in re.split(r'[；;\n]+', value):
        if not line.strip():
            continue
        pair = re.fullmatch(r'\s*([^:：]+)[:：]\s*(.*?)\s*', line)
        if not pair:
            return None
        host, age = pair[1].strip(), pair[2].strip()
        if host in result:
            return None
        number = re.fullmatch(r'(?:出厂)?\s*(\d+(?:\.\d+)?)\s*年', age)
        if number:
            years = float(number[1])
        else:
            try:
                born = date.fromisoformat(age)
                if born > today:
                    return None
                years = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
            except ValueError:
                return None
        result[host] = years
    return result or None


def required_now(field, values):
    if field.get('required'):
        return True
    rule = field.get('required_if')
    if not rule:
        return False
    row = values.get(rule['field'])
    value = json.loads(row['value_json']) if row else None
    if rule['operator'] == 'equals':
        return value == rule['value']
    entries = age_entries(value)
    return bool(entries and any(v >= rule['value'] for v in entries.values()))


def needs_reconfirmation(row, field):
    threshold = field.get('reconfirm_before_template')
    if not threshold or not row or row['value_json'] in ('null', '""', '[]'):
        return False
    def version(v):
        return tuple(int(n) for n in v.split('.'))
    try:
        return version(row.get('template_version') or '0.0.0') < version(threshold)
    except (ValueError, TypeError):
        return True
