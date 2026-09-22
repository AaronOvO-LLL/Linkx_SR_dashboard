"""Choice controls keep their string/list contract and display historical text losslessly."""
import json


def other_detail_error(field, value):
    if not field.get('require_other_detail'):
        return ''
    values = value if isinstance(value, list) else [value]
    allowed = {o['value'] for o in field.get('options', [])}
    if '其他' in values and not any(isinstance(v, str) and v.strip()
                                    and v.strip() not in allowed for v in values):
        return '选择“其他”时，请填写具体品牌名称。'
    return ''


def choice_state(field, value_json):
    value = json.loads(value_json) if value_json else None
    allowed = [o['value'] for o in field.get('options', [])]
    values = value if isinstance(value, list) else ([value] if isinstance(value, str) and value else [])
    return {
        'selected': [v for v in values if v in allowed],
        'custom': [v for v in values if isinstance(v, str) and v not in allowed],
        'inline': len(allowed) <= 5,
    }


def normalize_manual_choice(field, value):
    """Accept only valid shapes; custom text is explicit per field, never guessed."""
    if value in (None, '', []):
        return [] if field['type'] == 'multiselect' else ''
    if field['type'] == 'select':
        if not isinstance(value, str):
            raise ValueError('请选择一项或填写详细说明。')
        values = [value.strip()]
    else:
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            raise ValueError('多选答案必须为文字选项列表。')
        values = list(dict.fromkeys(v.strip() for v in value if v.strip()))
    allowed = {o['value'] for o in field.get('options', [])}
    if not field.get('allow_custom') and any(v and v not in allowed for v in values):
        raise ValueError('请选择提供的候选项。')
    error = other_detail_error(field, values)
    if error:
        raise ValueError(error)
    return values if field['type'] == 'multiselect' else values[0]
