"""独立文件格式渲染器注册；业务列与分组策略由配置提供。"""
import os
import re
from datetime import datetime


def render_xlsx_table(definition, context, fields, output_dir):
    """按表字段导出十列清单，层级合并不会跨越上级分组。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    field = fields[definition['table_field']]
    rows = context['data'][field['key']]
    columns = field['columns']
    book = Workbook()
    sheet = book.active
    sheet.title = definition.get('sheet_name', '清单')
    sheet.append(['序号'] + [c['label'] for c in columns])
    for index, row in enumerate(rows, 1):
        sheet.append([index] + [row.get(c['key'], '') for c in columns])
        for j, col in enumerate(columns, 2):
            cell = sheet.cell(index + 1, j)
            if col.get('type') == 'number' and cell.value not in (None, ''):
                cell.value = float(cell.value)
            elif isinstance(cell.value, str):
                # 用户文本按文本单元格保存，防止以等号开头的备注变成公式。
                cell.data_type = 's'
    edge = Side(style='thin', color='CBD5E1')
    for row in sheet:
        for cell in row:
            cell.border = Border(left=edge, right=edge, top=edge, bottom=edge)
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            cell.font = Font(name='微软雅黑', size=11)
    for cell in sheet[1]:
        cell.fill = PatternFill('solid', fgColor='264B65')
        cell.font = Font(name='微软雅黑', bold=True, color='FFFFFF', size=11)
    merge_keys = definition.get('merge_columns', [])
    for level, key in enumerate(merge_keys):
        column = next(i + 2 for i, c in enumerate(columns) if c['key'] == key)
        start = 0
        for end in range(1, len(rows) + 1):
            same = end < len(rows) and all(rows[end].get(k) == rows[start].get(k) for k in merge_keys[:level + 1])
            if same: continue
            if end - start > 1:
                sheet.merge_cells(start_row=start + 2, end_row=end + 1, start_column=column, end_column=column)
            start = end
    for i, col in enumerate([{}] + columns, 1):
        sheet.column_dimensions[get_column_letter(i)].width = 8 if i == 1 else (36 if col.get('key') == 'remark' else 20)
    sheet.row_dimensions[1].height = 30
    for i in range(2, len(rows) + 2): sheet.row_dimensions[i].height = 42
    sheet.freeze_panes = 'D2'
    sheet.auto_filter.ref = sheet.dimensions
    sheet.print_title_rows = '1:1'
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    name = definition['filename'].format(project=context['project']['name'], date=datetime.now().strftime('%Y%m%d'))
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip('. ')[:180] + '.xlsx'
    path = os.path.join(output_dir, name)
    book.save(path)
    return [dict(format='xlsx', name=name, path=path, size=os.path.getsize(path))]


def _xlsx_file_name(context, definition):
    """文件名由配置给出模板，这里只做占位替换与非法字符清理。"""
    name = definition['filename'].format(project=context['project']['name'],
                                         date=datetime.now().strftime('%Y%m%d'))
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip('. ')[:180] + '.xlsx'


def _resolve_path(context, path):
    """按 ``a.b.c`` 从渲染上下文取值，取不到返回空串。

    页眉信息全部走声明式路径，渲染器不写死任何业务字段名，因此换一个产品出
    Excel 只需要改 artifacts.json。
    """
    node = context
    for part in str(path or '').split('.'):
        if not part:
            continue
        node = node.get(part) if isinstance(node, dict) else getattr(node, part, None)
        if node is None:
            return ''
    return node


def render_xlsx_dataset(definition, context, fields, output_dir):
    """把规则引擎算出的数据集导出为 Excel。

    与 render_xlsx_table 的分工：那个直接导表格字段的原始录入行，这个导
    ``context['datasets']`` 里按规则库换算后的派生结果（如传感器配置清单）。
    列标签只在规则库的 result_columns 里定义一次，artifacts.json 仅补充宽度与
    对齐，避免同一份列名两处各写一遍后各自漂移。
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    dataset = (context.get('datasets') or {}).get(definition.get('dataset'))
    if dataset is None:
        raise ValueError('生成物「%s」声明的数据集「%s」不存在，请检查产品规则库配置。'
                         % (definition.get('name'), definition.get('dataset')))
    labels = {c.get('key'): c.get('label', '') for c in dataset.get('columns') or []}
    columns = definition.get('columns') or [{'key': c.get('key')} for c in dataset.get('columns') or []]
    columns = [dict(c, label=c.get('label') or labels.get(c.get('key')) or c.get('key')) for c in columns]
    rows = dataset.get('rows') or []

    book = Workbook()
    sheet = book.active
    sheet.title = (definition.get('sheet_name') or definition.get('name') or '清单')[:31]
    width = len(columns)
    edge = Side(style='thin', color='CBD5E1')
    border = Border(left=edge, right=edge, top=edge, bottom=edge)
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    left = Alignment(horizontal='left', vertical='center', wrap_text=True)

    index = 1
    if definition.get('title'):
        sheet.cell(index, 1, definition['title']).font = Font(name='微软雅黑', bold=True, size=15, color='264B65')
        sheet.merge_cells(start_row=index, start_column=1, end_row=index, end_column=width)
        sheet.row_dimensions[index].height = 28
        index += 1
    for meta in definition.get('meta') or []:
        value = _resolve_path(context, meta.get('path'))
        if value == '':
            continue
        sheet.cell(index, 1, '%s：%s' % (meta.get('label', ''), value)).font = Font(name='微软雅黑', size=10, color='5A6472')
        sheet.merge_cells(start_row=index, start_column=1, end_row=index, end_column=width)
        index += 1
    index += 1

    head = index
    for i, col in enumerate(columns, 1):
        cell = sheet.cell(head, i, col['label'])
        cell.fill = PatternFill('solid', fgColor='264B65')
        cell.font = Font(name='微软雅黑', bold=True, size=11, color='FFFFFF')
        cell.alignment = center
        cell.border = border
        sheet.column_dimensions[get_column_letter(i)].width = col.get('width') or 18
    sheet.row_dimensions[head].height = 26

    body = head
    for record in rows:
        body += 1
        for i, col in enumerate(columns, 1):
            value = record.get(col['key'])
            cell = sheet.cell(body, i, '' if value is None else value)
            cell.font = Font(name='微软雅黑', size=11)
            cell.border = border
            cell.alignment = center if col.get('align') == 'center' else left
            if isinstance(cell.value, str):
                # 用户文本按文本单元格保存，防止以等号开头的内容变成公式。
                cell.data_type = 's'
    if not rows:
        body += 1
        sheet.cell(body, 1, '（没有可输出的配置行）').font = Font(name='微软雅黑', color='9AA2AE')
        sheet.merge_cells(start_row=body, start_column=1, end_row=body, end_column=width)
    if definition.get('footer'):
        body += 2
        sheet.cell(body, 1, definition['footer']).font = Font(name='微软雅黑', size=9, color='8A93A0')
        sheet.merge_cells(start_row=body, start_column=1, end_row=body, end_column=width)

    sheet.freeze_panes = sheet.cell(head + 1, 1)
    if rows:
        sheet.auto_filter.ref = 'A%d:%s%d' % (head, get_column_letter(width), head + len(rows))
    sheet.print_title_rows = '%d:%d' % (head, head)
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0

    name = _xlsx_file_name(context, definition)
    path = os.path.join(output_dir, name)
    book.save(path)
    return [dict(format='xlsx', name=name, path=path, size=os.path.getsize(path))]


RENDERERS = {'xlsx_table': render_xlsx_table, 'xlsx_dataset': render_xlsx_dataset}
