"""Validate a complete Excel replacement before updating the survey table."""
from io import BytesIO
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from core import repo, rules
from core.config import field_config, field_map
from core.paths import CONFIG_DIR
from core.table_values import normalize_rows
from core.validate import table_issues


MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 2000
TEMPLATES = {
    'device_butler': ('device_room_survey', '设备管家_设备房导入模板.xlsx'),
    'energy_butler': ('energy_station_survey', '能源管家_冷站导入模板.xlsx'),
}


class SurveyImportError(ValueError):
    pass


def template_path(product_type):
    return Path(CONFIG_DIR, 'import_templates', TEMPLATES[product_type][1])


def sheet_columns(product_type):
    columns = field_map(product_type)[TEMPLATES[product_type][0]]['columns']
    if product_type == 'device_butler':
        return {'设备房设备': columns}
    station, fee, equipment = columns
    return {
        '冷站': [station, {**fee, 'label': '单站可改造空调电费是否大于100万元'}],
        '冷站设备': [station, *equipment['columns']],
    }


def _read_sheet(book, name, columns):
    if name not in {sheet.title for sheet in book.worksheets}:
        raise SurveyImportError(f'缺少「{name}」工作表，请使用对应产品的模板。')
    sheet = book[name]
    sheet.reset_dimensions()
    width = len(columns)
    cells = sheet.iter_rows(max_col=width + 1)
    header = next(cells, ())
    if [cell.value for cell in header[:width]] != [col['label'] for col in columns]:
        raise SurveyImportError(f'「{name}」的表头与模板不符，请保留原列名和顺序。')
    result = []
    for index, row in enumerate(cells, 2):
        if index > MAX_ROWS + 1:
            raise SurveyImportError(f'「{name}」超过 {MAX_ROWS} 行，请缩小清单。')
        if not any(cell.value is not None for cell in row):
            continue
        if row[width].value is not None:
            raise SurveyImportError(f'「{name}」第 {index} 行超出模板列。')
        values = {}
        for column, cell in zip(columns, row):
            value = cell.value
            if cell.data_type in ('f', 'e') or (value is not None and type(value) not in (str, int, float)):
                raise SurveyImportError(f'「{name}」第 {index} 行「{column["label"]}」请填写文本或数字。')
            if isinstance(value, str):
                value = value.strip()
            if column['type'] in ('text', 'select') and value is not None:
                value = str(value)
            if column.get('required') and value in (None, ''):
                raise SurveyImportError(f'「{name}」第 {index} 行缺少「{column["label"]}」。')
            values[column['key']] = value if value is not None else ''
        if any(value != '' for value in values.values()):
            result.append((index, values))
    if not result:
        raise SurveyImportError(f'「{name}」没有可导入的记录。空表不会清空现有清单。')
    return result


def _device_rows(field, rows):
    rule = rules.field_output('device_butler', field['key'])
    normalized = rules.normalize_rows(rule, [row for _, row in rows])
    categories = {}
    seen = set()
    for (index, _), row in zip(rows, normalized):
        room, category, device = (row[key] for key in ('room_name', 'space_type', 'device'))
        if categories.setdefault(room, category) != category:
            raise SurveyImportError(f'「设备房设备」第 {index} 行同名设备房使用了不同空间类型。')
        if (room, device) in seen:
            raise SurveyImportError(f'「设备房设备」第 {index} 行同一设备房的物业设备重复，请合并数量。')
        seen.add((room, device))
        _, error = rules.parse_qty(row['qty'])
        cat = rules.category_of(rule, category)
        if not cat or not rules.item_of(cat, device)[1] or error:
            raise SurveyImportError(f'「设备房设备」第 {index} 行：{error or "空间类型与物业设备不匹配，请查阅模板内的对照表。"}')
    return rules.materialize(rule, normalized)


def _station_rows(field, stations, equipment):
    by_name = {}
    for index, row in stations:
        name = row['room_name']
        if name in by_name:
            raise SurveyImportError(f'「冷站」第 {index} 行冷站名称重复。')
        if row['energy_fee_over_1m'] not in ('是', '否'):
            raise SurveyImportError(f'「冷站」第 {index} 行电费判断请填写「是」或「否」。')
        by_name[name] = {**row, 'energy_equipment_list': []}
    columns = field['columns'][-1]
    for index, row in equipment:
        name = row.pop('room_name')
        if name not in by_name:
            raise SurveyImportError(f'「冷站设备」第 {index} 行的冷站名称在「冷站」表中不存在。')
        issues = table_issues(columns, [row])
        if issues:
            raise SurveyImportError(f'「冷站设备」第 {index} 行：{issues[0]["message"].removeprefix("第1行")}')
        by_name[name]['energy_equipment_list'].append(row)
    for name, row in by_name.items():
        if not row['energy_equipment_list']:
            raise SurveyImportError(f'冷站「{name}」至少需要一条设备记录。')
    return normalize_rows(field, list(by_name.values()))


def parse_workbook(product_type, upload):
    if not upload or not upload.filename.lower().endswith('.xlsx'):
        raise SurveyImportError('请选择对应产品的 .xlsx 模板文件。')
    raw = upload.stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise SurveyImportError('Excel 文件不能超过 2 MB。')
    try:
        with ZipFile(BytesIO(raw)) as archive:
            members = archive.infolist()
            if len(members) > 200 or sum(item.file_size for item in members) > 20 * 1024 * 1024:
                raise SurveyImportError('工作簿内容过大，请使用空白模板重新整理。')
        book = load_workbook(BytesIO(raw), read_only=True, data_only=False, keep_links=False)
        try:
            data = {name: _read_sheet(book, name, cols) for name, cols in sheet_columns(product_type).items()}
        finally:
            book.close()
    except (BadZipFile, InvalidFileException, ParseError, KeyError, OSError, IndexError, TypeError, ValueError) as exc:
        if isinstance(exc, SurveyImportError):
            raise
        raise SurveyImportError('无法读取工作簿，请使用模板另存为有效的 .xlsx 文件。') from exc
    field = field_map(product_type)[TEMPLATES[product_type][0]]
    if product_type == 'device_butler':
        return _device_rows(field, data['设备房设备'])
    return _station_rows(field, data['冷站'], data['冷站设备'])


def replace(pp, upload):
    product_type = pp['product_type']
    rows = parse_workbook(product_type, upload)
    repo.replace_imported_table(pp, TEMPLATES[product_type][0], rows, field_config(product_type)['template_version'])
    return len(rows)
