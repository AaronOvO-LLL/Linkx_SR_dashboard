# -*- coding: utf-8 -*-
"""规则计算引擎（配置驱动、确定性）

产品把「一组录入行 → 一组派生结果行」的换算规则写在
``config/products/<product_type>/rules.json``，本模块只按声明执行：

    装载与校验   load / validate_rules
    同义词归一   normalize_rows
    固定项物化   materialize        （幂等：已存在的行不重复追加）
    结果计算     compute / compute_all
    录入校验     field_issues
    界面描述     table_ui           （下拉候选、计量标签）
    文本抽取     extract_rows       （rule 通道的表格抽取）

三条硬约束（呼应 ARCHITECTURE §1.5、§4.3）：

1. 纯函数、不落库、不依赖 Flask 上下文 —— 相同输入必得相同输出，可独立单测。
2. 不出现产品名分支 —— 空间类型、物业设备、传感器、计算模式与同义词全部来自
   规则库声明；新产品只加一份 rules.json 即可复用本模块。
3. 不臆造数值 —— 规则查不到、数量说不清时返回空值并给出问题，绝不填默认值。
   唯一的例外是固定项：它本来就由规则库定义默认值（需求文档 8.1 模式 A）。
"""
import re

from .config import rules_config
# extract.py 只在函数内部导入本模块（见 _run_rule_engine 的 rules_table 分支），
# 因此这里的模块级导入不构成循环依赖。
from .extract import clean_quote, cn2num, norm_space


class RulesError(ValueError):
    pass


MODE_FIXED = 'fixed_per_room'
MODE_PER_DEVICE = 'per_device'
MODE_PER_SUB_UNIT = 'per_sub_unit'
KNOWN_MODES = (MODE_FIXED, MODE_PER_DEVICE, MODE_PER_SUB_UNIT)

# 计算结果行可供 result_columns / xlsx columns 引用的键
RESULT_KEYS = ('qty', 'qty_unit', 'qty_display', 'sensor', 'sensor_qty',
               'sensor_unit', 'sensor_qty_display')


# ------------------------------------------------------------------ 装载

def load(product_type):
    """返回产品规则库；没有 rules.json 的产品返回 None。"""
    return rules_config(product_type)


def has_rules(product_type):
    return bool(load(product_type))


def rules_version(product_type):
    cfg = load(product_type)
    return (cfg or {}).get('rules_version', '')


def outputs(product_type):
    return (load(product_type) or {}).get('outputs') or []


def get_output(product_type, key):
    for o in outputs(product_type):
        if o.get('key') == key:
            return o
    return None


def field_output(product_type, field_key):
    """找出以某个表格字段为输入源的规则输出。没有则返回 None。

    服务层与页面据此判断「这个字段是不是规则驱动的」，不需要知道产品是谁。
    """
    for o in outputs(product_type):
        if o.get('source_field') == field_key:
            return o
    return None


def _default_unit(rules):
    return ((rules or {}).get('display') or {}).get('default_unit', '')


def categories(output_def):
    return (output_def or {}).get('categories') or []


# ------------------------------------------------------------------ 规则查找

def _terms(entry):
    """一个规则条目的全部可识别写法：标准名 + 同义词。"""
    name = entry.get('item') or entry.get('name') or ''
    return [name] + list(entry.get('synonyms') or [])


def category_of(output_def, name):
    """按名称或同义词解析空间类型。解析不到返回 None（交由校验报错，不猜）。"""
    target = (name or '').strip()
    if not target:
        return None
    for cat in categories(output_def):
        if target in [t.strip() for t in _terms(cat) if t.strip()]:
            return cat
    return None


def item_of(category, name):
    """在某个空间类型内解析物业设备，返回 ('fixed'|'variable', 条目) 或 (None, None)。"""
    target = (name or '').strip()
    if not category or not target:
        return None, None
    for kind in ('fixed', 'variable'):
        for entry in category.get(kind) or []:
            if target in [t.strip() for t in _terms(entry) if t.strip()]:
                return kind, entry
    return None, None


# ------------------------------------------------------------------ 数量解析

QTY_LEAD_RE = re.compile(r'^\s*([+-]?\d+)')
QTY_PURE_RE = re.compile(r'^\s*[+]?\d+\s*$')


def parse_qty(value):
    """把录入的设备数量解析为整数，返回 (数量, 错误说明)。

    空值不是错误——它表示「还没确定」，由调用方决定是待确认还是允许留空。
    非纯数字但能读出前导整数时（如大模型返回「3 区」）照常取值，避免把可救的
    数据判死；真正读不出数字才报错。
    """
    if value is None or isinstance(value, bool):
        return None, ''
    if isinstance(value, int):
        number = value
    elif isinstance(value, float):
        if not value.is_integer():
            return None, '设备数量必须是整数。'
        number = int(value)
    else:
        text = str(value).strip()
        if not text:
            return None, ''
        m = QTY_LEAD_RE.match(text)
        if not m:
            return None, '设备数量「%s」无法识别为数字。' % text
        number = int(m.group(1))
    if number < 0:
        return None, '设备数量不能为负数。'
    return number, ''


def display_qty(number, unit, default_unit):
    """数量展示：单位与默认单位一致时只显示数字（如 3），否则带上单位（如 3 区、1 套）。"""
    if number is None:
        return ''
    if unit and unit != default_unit:
        return '%s %s' % (number, unit)
    return str(number)


# ------------------------------------------------------------------ 归一与物化

def _truthy(value):
    return str(value).strip() not in ('', '0', 'None', 'false', 'False')


def normalize_rows(output_def, rows):
    """把空间类型与物业设备归一到规则库的标准名。

    归一失败时保留原文：让校验能报出用户/AI 到底写了什么，比静默改成标准名
    更安全（静默改写会掩盖「这个词根本不在规则库里」的事实）。
    """
    if not isinstance(rows, list):
        return []
    cat_col = output_def.get('category_column')
    item_col = output_def.get('item_column')
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        r = dict(row)
        cat = category_of(output_def, r.get(cat_col))
        if cat:
            r[cat_col] = cat['name']
            kind, entry = item_of(cat, r.get(item_col))
            if entry:
                r[item_col] = entry['item']
        out.append(r)
    return out


def _fixed_rows(output_def, room, category):
    """构造某间设备房的全部固定项行。"""
    rows = []
    for entry in category.get('fixed') or []:
        row = {output_def['group_column']: room,
               output_def['category_column']: category['name'],
               output_def['item_column']: entry['item'],
               output_def['qty_column']: entry.get('default')}
        if output_def.get('enabled_column'):
            row[output_def['enabled_column']] = '1'
        rows.append(row)
    return rows


def materialize(output_def, rows):
    """按空间类型把缺失的固定项补全为可编辑行（需求文档 6.4 / 决策 B3）。

    幂等：已存在的固定项行原样保留（含用户改过的数量），只补该房间还缺的那些。
    固定项插在所属设备房最后一行之后，保证同一间设备房的行始终连续。

    不想要某个固定项时把数量改成 0，而不是删掉整行——删掉的行会在下一次保存
    时被重新补出来，因为「用户主动删掉」与「还没补全过」在数据上无法区分。
    数量为 0 由 compute_rows 判为不产出传感器，效果一致且可逆。
    """
    rows = normalize_rows(output_def, rows)
    if not output_def.get('materialize_fixed'):
        return rows

    group_col = output_def.get('group_column')
    cat_col = output_def.get('category_column')
    item_col = output_def.get('item_column')

    order, index_of, category_of_room, present = [], {}, {}, {}
    for i, row in enumerate(rows):
        room = str(row.get(group_col) or '').strip()
        if room not in index_of:
            index_of[room] = []
            category_of_room[room] = None
            present[room] = set()
            order.append(room)
        index_of[room].append(i)
        cat = category_of(output_def, row.get(cat_col))
        if cat:
            category_of_room[room] = category_of_room[room] or cat
            kind, entry = item_of(cat, row.get(item_col))
            if kind == 'fixed':
                present[room].add(entry['item'])

    inserts = {}
    for room in order:
        cat = category_of_room[room]
        if not cat:
            continue
        extra = [r for r in _fixed_rows(output_def, room, cat)
                 if r[item_col] not in present[room]]
        if extra:
            inserts[index_of[room][-1]] = extra

    if not inserts:
        return rows
    out = []
    for i, row in enumerate(rows):
        out.append(row)
        out.extend(inserts.get(i, []))
    return out


def postprocess(product_type, field_key, rows, confidence='medium'):
    """提取之后的统一收口：同义词归一 → 补全固定项 → 有算不出的行就降为待确认。

    rule 与 llm 两个通道都走这里，避免各自实现一遍导致口径漂移。数量说不清时
    把整张表降为 low（平台据此标「待确认」），而不是替用户填一个默认值——这是
    需求文档 A2 的硬要求。
    """
    output_def = field_output(product_type, field_key)
    if not output_def or not isinstance(rows, list):
        return rows, confidence
    rows = materialize(output_def, rows)
    default_unit = _default_unit(load(product_type))
    if any(r['error'] for r in compute_rows(output_def, rows, default_unit)):
        confidence = 'low'
    return rows, confidence


# ------------------------------------------------------------------ 计算

def _sensor_qty(mode, qty, default):
    """按声明的计算模式换算传感器数量。

    本版三种模式都是 1:1（需求文档 8.6）：固定项取默认值/用户调整值，变动项取
    设备数量。未知模式直接抛错而不是回落成 1:1 —— 新增一种换算口径必须显式改
    这里，否则规则库改了、结果却静默按旧口径算，是最难发现的一类错误。
    """
    if mode == MODE_FIXED:
        return default if qty is None else qty
    if mode in (MODE_PER_DEVICE, MODE_PER_SUB_UNIT):
        return qty
    raise RulesError('规则库声明了未知的计算模式：%s' % mode)


def compute_rows(output_def, rows, default_unit=''):
    """逐行计算，返回与 rows 等长、下标对应的结果列表。

    每行结果：enabled / kind / sensor / qty / unit / qty_label / sensor_qty /
    error / warning。行级问题就地返回，不做全局聚合，方便页面把提示贴在对应行上。
    """
    cat_col = output_def.get('category_column')
    item_col = output_def.get('item_column')
    qty_col = output_def.get('qty_column')
    on_col = output_def.get('enabled_column')
    cat_count = len(categories(output_def))

    results = []
    for row in rows:
        res = {'enabled': True, 'kind': '', 'sensor': '', 'qty': None, 'unit': '',
               'qty_label': '', 'sensor_qty': None, 'sensor_unit': default_unit,
               'error': '', 'warning': '', 'remark': ''}
        if on_col and on_col in row and not _truthy(row.get(on_col)):
            res['enabled'] = False
            results.append(res)
            continue

        room = str(row.get(output_def.get('group_column') or '') or '').strip()
        cat = category_of(output_def, row.get(cat_col))
        if not cat:
            res['error'] = ('【%s】空间类型「%s」不在规则库的 %d 类取值内，请重新选择。'
                            % (room or '未命名设备房', row.get(cat_col) or '', cat_count))
            results.append(res)
            continue

        kind, entry = item_of(cat, row.get(item_col))
        if not entry:
            res['error'] = ('【%s】物业设备「%s」不在「%s」的规则内，不会产出传感器。'
                            % (room or cat['name'], row.get(item_col) or '', cat['name']))
            results.append(res)
            continue

        res['kind'] = kind
        res['sensor'] = entry.get('sensor', '')
        res['unit'] = entry.get('unit') or default_unit
        # 设备数量的计量口径（区/个/台/套）与传感器本身的计数单位是两件事：
        # 「3 区」换算出的是「3 台」压力变送器。只有网关按「套」计（需求文档 9.1）。
        res['sensor_unit'] = entry.get('sensor_unit') or default_unit
        res['qty_label'] = entry.get('qty_label') or ''
        res['remark'] = entry.get('remark') or ''

        qty, err = parse_qty(row.get(qty_col))
        if err:
            res['error'] = '【%s / %s】%s' % (room or cat['name'], entry['item'], err)
            results.append(res)
            continue
        res['qty'] = qty

        mode = MODE_FIXED if kind == 'fixed' else (entry.get('mode') or MODE_PER_DEVICE)
        try:
            res['sensor_qty'] = _sensor_qty(mode, qty, entry.get('default'))
        except RulesError as exc:
            res['error'] = '【%s / %s】%s' % (room or cat['name'], entry['item'], exc)
            results.append(res)
            continue

        if qty is None:
            if entry.get('allow_empty'):
                res['warning'] = ('【%s / %s】%s未填写，按不配置处理。'
                                  % (room or cat['name'], entry['item'], res['qty_label'] or '数量'))
            else:
                res['error'] = ('【%s / %s】%s未确定，请补齐后再计算；确实没有该项请取消勾选或填 0。'
                                % (room or cat['name'], entry['item'], res['qty_label'] or '设备数量'))
        results.append(res)
    return results


def build_dataset(product_type, output_def, rows):
    """计算一个规则输出，返回可直接交给页面与 xlsx renderer 的数据集。"""
    default_unit = _default_unit(load(product_type))
    rows = normalize_rows(output_def, rows)
    results = compute_rows(output_def, rows, default_unit)

    group_col = output_def.get('group_column')
    item_col = output_def.get('item_column')
    out_rows, issues = [], []
    for i, (row, res) in enumerate(zip(rows, results)):
        if res['error']:
            issues.append({'level': 'error', 'field': output_def.get('source_field'),
                           'row': i, 'message': res['error']})
        elif res['warning']:
            issues.append({'level': 'warn', 'field': output_def.get('source_field'),
                           'row': i, 'message': res['warning']})
        if not res['enabled'] or not res['sensor'] or not res['sensor_qty']:
            continue
        out_rows.append({
            group_col: str(row.get(group_col) or '').strip(),
            item_col: str(row.get(item_col) or '').strip(),
            'qty': res['qty'],
            'qty_unit': res['unit'],
            'qty_display': display_qty(res['qty'], res['unit'], default_unit),
            'sensor': res['sensor'],
            'sensor_qty': res['sensor_qty'],
            'sensor_unit': res['sensor_unit'],
            'sensor_qty_display': display_qty(res['sensor_qty'], res['sensor_unit'], default_unit),
        })

    return {
        'key': output_def.get('key'),
        'label': output_def.get('label', ''),
        'source_field': output_def.get('source_field'),
        'columns': [dict(c) for c in output_def.get('result_columns') or []],
        'rows': out_rows,
        'row_results': results,
        'input_rows': rows,
        'issues': issues,
        'rules_version': rules_version(product_type),
    }


def compute_all(product_type, values):
    """按当前录入重算全部规则输出。values 为 {字段 key: 已解析的值}。

    刻意不落库：预览与生成各自按当下录入重算，避免「改了数量但 BOM 还是旧的」
    这类陈旧派生数据（需求文档 16-P0-3 已确认此时机）。
    """
    datasets = {}
    for output_def in outputs(product_type):
        rows = (values or {}).get(output_def.get('source_field'))
        datasets[output_def['key']] = build_dataset(
            product_type, output_def, rows if isinstance(rows, list) else [])
    return datasets


def field_issues(product_type, values):
    """规则库视角的录入问题，格式与 core/validate.py 一致。"""
    issues = []
    for dataset in compute_all(product_type, values).values():
        issues.extend({'level': i['level'], 'field': i['field'], 'message': i['message']}
                      for i in dataset['issues'])
    return issues


# ------------------------------------------------------------------ 界面描述

def table_ui(product_type, output_def):
    """给核对页的表格列描述：候选项、计量标签、固定项默认值。

    页面据此渲染下拉与提示，前端不持有规则——规则库改了，候选项自动跟着变。
    """
    cats = []
    for cat in categories(output_def):
        items = []
        for kind in ('variable', 'fixed'):
            for entry in cat.get(kind) or []:
                items.append({
                    'name': entry['item'],
                    'kind': kind,
                    'sensor': entry.get('sensor', ''),
                    'unit': entry.get('unit') or _default_unit(load(product_type)),
                    'qty_label': entry.get('qty_label') or '',
                    'default': entry.get('default'),
                    'allow_empty': bool(entry.get('allow_empty')),
                    'remark': entry.get('remark') or '',
                })
        cats.append({'name': cat['name'], 'remark': cat.get('remark') or '', 'items': items})
    return {
        'source_field': output_def.get('source_field'),
        'group_column': output_def.get('group_column'),
        'category_column': output_def.get('category_column'),
        'item_column': output_def.get('item_column'),
        'qty_column': output_def.get('qty_column'),
        'enabled_column': output_def.get('enabled_column'),
        'default_unit': _default_unit(load(product_type)),
        'categories': cats,
    }


# ------------------------------------------------------------------ 规则库自检

def validate_rules(product_type):
    """校验规则库自身，返回与 fieldmodel.validate_field_model 同构的问题列表。"""
    from . import fieldmodel

    def issue(level, where, message):
        return {'level': level, 'where': where, 'message': message}

    cfg = load(product_type)
    if cfg is None:
        return []
    out = []
    if not cfg.get('rules_version'):
        out.append(issue('error', product_type, 'rules.json 缺少 rules_version'))

    fields = {f['key']: f for f in fieldmodel.load_field_model(product_type).get('fields', [])}
    seen_outputs = set()
    for o in cfg.get('outputs') or []:
        key = o.get('key') or '?'
        w = '%s/rules:%s' % (product_type, key)
        if key in seen_outputs:
            out.append(issue('error', w, 'output key 重复'))
        seen_outputs.add(key)
        for must in ('label', 'source_field', 'group_column', 'category_column',
                     'item_column', 'qty_column'):
            if not o.get(must):
                out.append(issue('error', w, '缺少必填声明 %s' % must))

        src = fields.get(o.get('source_field'))
        if not src:
            out.append(issue('error', w, "source_field '%s' 不在字段模型中" % o.get('source_field')))
        elif src.get('type') != 'table':
            out.append(issue('error', w, "source_field '%s' 必须是 table 类型字段" % o.get('source_field')))
        else:
            cols = {c.get('key') for c in src.get('columns') or []}
            for decl in ('group_column', 'category_column', 'item_column', 'qty_column'):
                if o.get(decl) and o[decl] not in cols:
                    out.append(issue('error', w, "%s '%s' 不是该表格字段的列" % (decl, o[decl])))
            on_col = o.get('enabled_column')
            if on_col and on_col in cols:
                out.append(issue('warn', w, "enabled_column '%s' 不应声明在 columns 中（它是界面勾选态，不进预览表）" % on_col))

        emitted = set(RESULT_KEYS) | {o.get('group_column'), o.get('item_column')}
        for c in o.get('result_columns') or []:
            if not c.get('key') or not c.get('label'):
                out.append(issue('error', w, 'result_columns 每项必须含 key 与 label'))
            elif c['key'] not in emitted:
                out.append(issue('error', w, "result_columns 的 '%s' 不是引擎会输出的键（可用：%s）"
                                 % (c['key'], '、'.join(sorted(k for k in emitted if k)))))

        cat_keys, seen_names = set(), set()
        for cat in o.get('categories') or []:
            cw = '%s/%s' % (w, cat.get('key') or '?')
            if not cat.get('key') or not cat.get('name'):
                out.append(issue('error', cw, '空间类型缺少 key 或 name'))
            if cat.get('key') in cat_keys:
                out.append(issue('error', cw, '空间类型 key 重复'))
            cat_keys.add(cat.get('key'))
            if cat.get('name') in seen_names:
                out.append(issue('error', cw, '空间类型名称重复'))
            seen_names.add(cat.get('name'))
            if not cat.get('synonyms'):
                out.append(issue('warn', cw, '未配置 synonyms，口语提法将无法归一'))

            names = set()
            for kind in ('fixed', 'variable'):
                for entry in cat.get(kind) or []:
                    ew = '%s/%s' % (cw, entry.get('item') or '?')
                    if not entry.get('item') or not entry.get('sensor'):
                        out.append(issue('error', ew, '缺少 item 或 sensor'))
                    if entry['item'] in names:
                        out.append(issue('error', ew, '同一空间类型下物业设备重复'))
                    names.add(entry.get('item'))
                    if kind == 'fixed':
                        if not isinstance(entry.get('default'), int) or entry['default'] < 0:
                            out.append(issue('error', ew, '固定项 default 必须是非负整数'))
                        if not entry.get('unit'):
                            out.append(issue('warn', ew, '固定项建议声明 unit'))
                    else:
                        mode = entry.get('mode')
                        if mode not in (MODE_PER_DEVICE, MODE_PER_SUB_UNIT):
                            out.append(issue('error', ew, "变动项 mode '%s' 不合法（可用：%s）"
                                             % (mode, '、'.join((MODE_PER_DEVICE, MODE_PER_SUB_UNIT)))))
                        if not entry.get('qty_label'):
                            out.append(issue('warn', ew, '变动项建议声明 qty_label，否则界面给不出计量口径提示'))
    return out


# ------------------------------------------------------------------ 文本抽取（rule 通道）

_CLAUSE_RE = re.compile(r'[，,。；;！!？?\n\r]+')
# 设备房提法：可选的显式编号（1#）、可选的数量（2 个）、空间类型同义词
_ROOM_PREFIX = r'(?:(\d{1,3})\s*[#＃])?\s*(?:([0-9]+|[一二三四五六七八九十两]{1,4})\s*(?:个|间|座|处)?)?\s*'
_NUM_IN_CLAUSE = re.compile(r'([0-9]+|[一二三四五六七八九十两]{1,4})\s*(?:个|台|只|套|间|座|条|根|组)?')
# 「2 个连通的生活水箱」里的 2 是物理台数，不是独立数——不能当成答案（决策 B2）
_QTY_SKIP = ('共', '一共', '总共', '合计')
# 「现场有几个变配电房」——间数说不清时只落一间并把整张表降为待确认，不猜数量（A2）
_VAGUE_COUNT = ('几', '若干', '好几', '数个')


def _room_mentions(output_def, text):
    """扫描全部设备房提法，返回按出现位置排列、互不重叠的命中。"""
    hits = []
    for cat in categories(output_def):
        for term in [t for t in _terms(cat) if t]:
            rx = re.compile(_ROOM_PREFIX + re.escape(term))
            for m in rx.finditer(text):
                explicit, count = m.group(1), m.group(2)
                number = 1
                if explicit:
                    number = 1
                elif count:
                    parsed = cn2num(count)
                    number = int(parsed) if parsed and parsed.is_integer() and 0 < parsed < 100 else 1
                hits.append({'start': m.start(), 'end': m.end(), 'len': len(term),
                             'surface': term, 'category': cat, 'count': number,
                             'index': int(explicit) if explicit else None,
                             'vague': any(c in text[max(0, m.start() - 4):m.start()]
                                          for c in _VAGUE_COUNT)})
    hits.sort(key=lambda h: (h['start'], -h['len']))
    taken, out = [], []
    for h in hits:
        if any(not (h['end'] <= s or h['start'] >= e) for s, e in taken):
            continue
        taken.append((h['start'], h['end']))
        out.append(h)
    out.sort(key=lambda h: h['start'])
    return out


def _clause_at(text, start, end):
    """取出包含 [start,end) 的那一小句，用于把数量判定限制在局部语境里。"""
    left = max([m.end() for m in _CLAUSE_RE.finditer(text, 0, start)] or [0])
    right = min([m.start() for m in _CLAUSE_RE.finditer(text, end)] or [len(text)])
    return text[left:right], start - left


def _cue_hit(clause, cue):
    """线索词在句中是否处于肯定语境。

    「消防水池独立设置、不连通」里的「不连通」恰恰说明是 1 个独立水体，不能
    当成需要人工确认的模糊表述，否则会逼用户去确认一个原文已经说清的事实。
    """
    start = 0
    while True:
        i = clause.find(cue, start)
        if i < 0:
            return False
        if not any(n in clause[max(0, i - 2):i] for n in ('不', '非', '未', '没')):
            return True
        start = i + len(cue)


def _qty_in_clause(clause, rel_pos, entry):
    """在一小句里找设备数量，返回 (数量|None, 是否模糊)。

    优先取设备名之前最近的数字（「3 个生活水泵控制柜」），其次取其后最近的
    数字（「生活给水分高、中、低 3 个区」）。句中出现总量线索时不取值——那种
    数字说的是总数而不是这一项，宁可留空让人补。
    """
    if any(_cue_hit(clause, cue) for cue in entry.get('ambiguous_cues') or []):
        return None, True
    if any(cue in clause for cue in _QTY_SKIP):
        return None, True
    spans = []
    for m in _NUM_IN_CLAUSE.finditer(clause):
        parsed = cn2num(m.group(1))
        if parsed is None or not parsed.is_integer() or not 0 < parsed < 10000:
            continue
        spans.append((m.start(), m.end(), int(parsed)))
    if not spans:
        return None, False
    before = [s for s in spans if s[1] <= rel_pos]
    if before:
        return before[-1][2], False
    after = [s for s in spans if s[0] >= rel_pos]
    if after:
        return after[0][2], False
    return None, False


def _scan_variable_items(scope, category):
    """在一段文本范围内找出变动项及其数量，返回 {规则库声明序号: 命中信息}。

    同义词按长度倒序认领，避免「集水井泵控制柜」被更短的「集水井」抢先命中。
    每个物业设备只取第一次命中的数量。以声明序号为键，是为了让同一间设备房被
    提到多次时能按设备合并，并让 BOM 行序始终与规则库一致、不随叙述顺序漂移。
    """
    entries = []
    for position, entry in enumerate(category.get('variable') or []):
        for term in [t for t in _terms(entry) if t]:
            entries.append((term, entry, position))
    entries.sort(key=lambda triple: -len(triple[0]))

    claimed, found = [], {}
    for term, entry, position in entries:
        for m in re.finditer(re.escape(term), scope):
            if any(not (m.end() <= s or m.start() >= e) for s, e in claimed):
                continue
            claimed.append(m.span())
            if position in found:
                continue
            clause, rel = _clause_at(scope, m.start(), m.end())
            qty, ambiguous = _qty_in_clause(clause, rel, entry)
            found[position] = {'entry': entry, 'qty': qty, 'ambiguous': ambiguous}
    return found


def _group_rooms(output_def, text, mentions):
    """把设备房提法归并为房间，返回 (房间名顺序, {房间名: 房间信息})。

    同一间房在文字稿里被提到两次（「另有 1 个空调机房……空调机房没说管网数量」）
    必须并成一间，否则会重复补出两套固定项；而「2 个变配电房」必须拆成两间
    （决策 A1）。区分依据是提法里有没有给出数量或显式编号。
    """
    order, rooms, created, reuse = [], {}, {}, {}
    for i, hit in enumerate(mentions):
        end = mentions[i + 1]['start'] if i + 1 < len(mentions) else len(text)
        scope = text[hit['start']:end]
        cat = hit['category']
        ckey = cat.get('key') or cat['name']
        if hit['index']:
            names = ['%d#%s' % (hit['index'], hit['surface'])]
        elif hit['count'] > 1:
            base = created.get(ckey, 0)
            names = ['%d#%s' % (base + n + 1, hit['surface']) for n in range(hit['count'])]
            created[ckey] = base + hit['count']
        else:
            key = (ckey, hit['surface'])
            if key in reuse:
                names = [reuse[key]]
            else:
                names = ['%d#%s' % (created.get(ckey, 0) + 1, hit['surface'])]
                reuse[key] = names[0]
                created[ckey] = created.get(ckey, 0) + 1
        for name in names:
            room = rooms.get(name)
            if room is None:
                room = rooms[name] = {'category': cat, 'scopes': [], 'multi': False, 'vague': False}
                order.append(name)
            room['scopes'].append(scope)
            room['multi'] = room['multi'] or (hit['count'] > 1 and not hit['index'])
            room['vague'] = room['vague'] or hit['vague']
    return order, rooms


def extract_rows(output_def, text):
    """从文字稿抽取设备房踏勘行。返回 (rows, quote, confidence)。

    遵循需求文档 6.3：同类多间分别成条（A1）、模糊不臆造（A2）、只抽变动项
    （固定项由本函数按空间类型补齐）。抽不到设备房时返回空值，交由字段状态机
    判为「必须补填」。
    """
    text = norm_space(text or '')
    mentions = _room_mentions(output_def, text)
    if not mentions:
        return None, '', 'low'

    group_col = output_def['group_column']
    cat_col = output_def['category_column']
    item_col = output_def['item_column']
    qty_col = output_def['qty_column']
    on_col = output_def.get('enabled_column')

    order, rooms = _group_rooms(output_def, text, mentions)
    quote = clean_quote(_clause_at(text, mentions[0]['start'], mentions[0]['end'])[0])

    rows, uncertain = [], False
    for name in order:
        room = rooms[name]
        merged = {}
        for scope in room['scopes']:
            for position, hit in _scan_variable_items(scope, room['category']).items():
                merged.setdefault(position, hit)
        if room['vague']:
            uncertain = True
        if room['multi'] and merged:
            # 一次提法覆盖多间、设备只描述了一遍：按「每间相同」落行，但整体降为
            # 待确认，让人核对是否真的每间都一样（A2 不猜）。
            uncertain = True
        for position in sorted(merged):
            f = merged[position]
            # 非必填项（如空调机房的管网数）留空是允许的正常状态，不该把整张表拉成
            # 待确认；只有「该有数量却没读到」的行才需要人看一眼。
            uncertain = uncertain or f['ambiguous'] or (
                f['qty'] is None and not f['entry'].get('allow_empty'))
            row = {group_col: name, cat_col: room['category']['name'],
                   item_col: f['entry']['item'], qty_col: f['qty']}
            if on_col:
                row[on_col] = '1'
            rows.append(row)
        # 固定项不要求文字稿提及（需求文档 6.3），在这里直接按空间类型补齐。
        # 电梯机房、配电房这类没有变动项的设备房也因此才能进入清单。
        rows.extend(_fixed_rows(output_def, name, room['category']))
    return rows, quote, ('low' if uncertain else 'high')
