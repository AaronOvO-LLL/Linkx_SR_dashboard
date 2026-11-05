"""完整流程测试中的现场已确认数据；缺失/无效值由各用例独立构造。"""
def sample_value(field):
    key = field['key']
    if key in ('monitor_center_count', 'fire_control_count'): return 1
    if field.get('validate', {}).get('age_entries'): return 'A：4年'
    if key == 'device_room_survey':
        return [{'room_name':'1号泵房','space_type':'独立生活水泵房','device':'生活水箱','qty':2}]
    if field['type'] == 'table':
        return [{c['key']:sample_value(c) for c in field['columns']}]
    if field['type'] == 'number': return 4
    if field['type'] == 'select': return field['options'][0]['value']
    if field['type'] == 'multiselect': return [field['options'][0]['value']]
    return '现场已确认'
