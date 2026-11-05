"""表格列共享人工保存与模型输出的值契约。"""
from .choices import normalize_manual_choice


def normalize_rows(field, value):
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError('表格值必须为对象数组')
    result = []
    for row in value:
        normalized = {}
        for col in field['columns']:
            cell = row.get(col['key'])
            if col.get('type') == 'table':
                cell = normalize_rows({**col, 'ui': field.get('ui', {})}, [] if cell in (None, '', []) else cell)
            elif cell in (None, '', []):
                cell = col.get('default', [] if col.get('type') == 'multiselect' else '')
            elif field.get('ui', {}).get('mode') == 'cards' and col.get('type') in ('select', 'multiselect'):
                cell = normalize_manual_choice(col, cell)
            normalized[col['key']] = cell
        result.append(normalized)
    return result


def flatten_nested_rows(columns, rows, nested_key):
    """Export a child table with its parent identity and other scalar columns."""
    nested = next(col for col in columns if col['key'] == nested_key)
    parent_columns = [col for col in columns if col['key'] != nested_key]
    result = []
    for row in rows:
        parent = {col['key']: row.get(col['key']) for col in parent_columns}
        for child in row.get(nested_key) or [{}]:
            result.append({**parent, **child})
    return parent_columns + nested['columns'], result
