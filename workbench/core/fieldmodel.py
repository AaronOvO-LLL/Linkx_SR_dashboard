# -*- coding: utf-8 -*-
"""字段模型装载器（field model loader）

字段模型分三层，全部是配置，代码零业务硬编码：

    config/field_model_schema.json   元定义：字段能有哪些属性、什么类型合法（契约）
    config/field_packs/*.json        可复用字段包：一组分组 + 一组字段定义
    config/products/<t>/fields.json  组装清单：引用哪些包 + 覆盖 + 增删

对外只暴露一个函数 ``load_field_model(product_type)``，返回**扁平结构**：

    {"template_version":..., "groups":[...], "fields":[...]}

与旧版（v0.2.0 单文件 fields.json）完全同构，因此 extract / generate /
validate / 模板 / 前端表单都不需要改。

向后兼容：如果 fields.json 里没有 ``packs`` 键，则视为旧的扁平格式原样返回。
"""
import os
import re

from . import paths

_MERGED_CACHE = {}


class FieldModelError(Exception):
    pass


# ------------------------------------------------------------------ 基础

def _load_json(path):
    """延迟导入 config，避免 config <- fieldmodel 循环依赖。"""
    from .config import load_json
    return load_json(path)


def reload_all():
    _MERGED_CACHE.clear()


def _issue(level, where, message):
    return {'level': level, 'where': where, 'message': message}


def _deep_merge(base, patch):
    """dict 递归合并，其余类型整体覆盖。用于 overrides 的字段级改写。"""
    out = dict(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def list_packs():
    out = []
    if not os.path.isdir(paths.FIELD_PACKS_DIR):
        return out
    for n in sorted(os.listdir(paths.FIELD_PACKS_DIR)):
        if n.endswith('.json'):
            out.append(n[:-5])
    return out


def load_pack(pack_key):
    p = os.path.join(paths.FIELD_PACKS_DIR, pack_key + '.json')
    if not os.path.isfile(p):
        raise FieldModelError('字段包不存在：%s（应位于 config/field_packs/%s.json）'
                              % (pack_key, pack_key))
    return _load_json(p)


# ------------------------------------------------------------------ 装载

def _merge(manifest, product_type, problems=None):
    """组装清单 + 字段包 → 扁平模型。

    problems 为 None  → 遇到致命问题立刻抛 FieldModelError（运行时路径，快失败）
    problems 为 list  → 记录问题并尽力合并（校验路径，一次暴露全部问题）
    """
    def fail(msg):
        if problems is None:
            raise FieldModelError(msg)
        problems.append(_issue('error', product_type, msg))

    # 1) 读包。缺包不中断：校验时先把其余问题一次报全
    packs = []
    for k in (manifest.get('packs') or []):
        p = os.path.join(paths.FIELD_PACKS_DIR, k + '.json')
        if os.path.isfile(p):
            packs.append(_load_json(p))
        else:
            packs.append({'pack': k, 'groups': [], 'fields': []})
            fail('字段包不存在：%s（应位于 config/field_packs/%s.json）' % (k, k))

    # 2) 分组：包提供定义，清单的 group_order 决定顺序（缺省回落 group.order）
    groups, seen_group = [], set()
    for pk in packs:
        for g in pk.get('groups', []):
            if g.get('key') in seen_group:
                continue
            seen_group.add(g.get('key'))
            groups.append(dict(g))
    gorder = manifest.get('group_order') or []
    gidx = {k: i for i, k in enumerate(gorder)}
    groups.sort(key=lambda g: (gidx.get(g['key'], len(gidx) + g.get('order', 99)),
                               g.get('order', 99)))

    # 3) 字段：按包顺序收集
    raw_fields, seen_field = [], set()
    for pk in packs:
        for f in pk.get('fields', []):
            if f.get('key') in seen_field:
                fail('字段 key 重复（跨包冲突）：%s' % f.get('key'))
                continue
            seen_field.add(f.get('key'))
            raw_fields.append(f)

    removed = set(manifest.get('remove_fields') or [])
    overrides = manifest.get('overrides') or {}
    for k in overrides:
        if k not in seen_field:
            fail('overrides 引用了不存在的字段：%s' % k)
    for k in removed:
        if k not in seen_field:
            fail('remove_fields 引用了不存在的字段：%s' % k)

    fields, seq = [], {}
    for i, f in enumerate(raw_fields):
        if f['key'] in removed:
            continue
        seq[f['key']] = i
        fields.append(_deep_merge(f, overrides[f['key']]) if f['key'] in overrides else dict(f))

    # 4) 本产品独有字段
    for j, f in enumerate(manifest.get('extra_fields') or []):
        key = f.get('key')
        if key in seen_field:
            fail('extra_fields 与已有字段 key 冲突：%s' % key)
        seen_field.add(key)
        seq[key] = len(raw_fields) + j
        fields.append(dict(f))

    # 5) 排序：分组顺序优先，组内保持声明顺序
    fields.sort(key=lambda f: (gidx.get(f.get('group'), len(gidx) + 99),
                               seq.get(f.get('key'), 0)))

    return {
        'template_version': manifest.get('template_version', '0.0.0'),
        'product_type': product_type,
        'note': manifest.get('note', ''),
        'source': 'manifest',
        'packs': manifest.get('packs') or [],
        'groups': groups,
        'fields': fields,
    }


def load_field_model(product_type):
    if product_type in _MERGED_CACHE:
        return _MERGED_CACHE[product_type]

    manifest_path = os.path.join(paths.product_dir(product_type), 'fields.json')
    if not os.path.isfile(manifest_path):
        raise FieldModelError('字段模型不存在：%s' % manifest_path)
    manifest = _load_json(manifest_path)

    if 'packs' not in manifest:      # 旧扁平格式：原样返回
        _MERGED_CACHE[product_type] = manifest
        return manifest

    merged = _merge(manifest, product_type)
    _MERGED_CACHE[product_type] = merged
    return merged


# ------------------------------------------------------------------ 校验

def load_schema():
    return _load_json(os.path.join(paths.CONFIG_DIR, 'field_model_schema.json'))


KEY_RE = re.compile(r'^[a-z][a-z0-9_]*$')


def validate_field_model(product_type):
    """按 field_model_schema.json 校验一个产品的字段模型。返回问题列表（一次报全）。"""
    issues = []
    try:
        schema = load_schema()
    except Exception as e:
        return [_issue('error', product_type, '读取 field_model_schema.json 失败：%s' % e)]

    manifest_path = os.path.join(paths.product_dir(product_type), 'fields.json')
    if not os.path.isfile(manifest_path):
        return [_issue('error', product_type, '字段模型不存在：%s' % manifest_path)]
    raw = _load_json(manifest_path)

    if 'packs' in raw:
        model = _merge(raw, product_type, problems=issues)
        if not raw.get('group_order'):
            issues.append(_issue('warn', product_type,
                                 '未声明 group_order，分组顺序将回落到字段包内的 order'))
    else:
        model = raw
        issues.append(_issue('info', product_type,
                             '仍在使用旧的单文件扁平格式，建议迁移到字段包'))

    types = schema.get('field_types', {})
    modes = schema.get('extract_modes', {})

    group_keys = [g.get('key') for g in model.get('groups', [])]
    if len(group_keys) != len(set(group_keys)):
        issues.append(_issue('error', product_type, '分组 key 重复'))

    field_keys = [f.get('key') for f in model.get('fields', [])]
    dup = sorted({k for k in field_keys if field_keys.count(k) > 1 and k})
    if dup:
        issues.append(_issue('error', product_type, '字段 key 重复：%s' % '、'.join(dup)))

    for f in model.get('fields', []):
        key = f.get('key') or '?'
        w = '%s/%s' % (product_type, key)
        if not KEY_RE.match(str(key)):
            issues.append(_issue('error', w, 'key 必须是 snake_case（小写字母开头，仅含小写字母/数字/下划线）'))
        for must in ('label', 'group', 'type'):
            if not f.get(must):
                issues.append(_issue('error', w, '缺少必填属性 %s' % must))
        if f.get('group') and f.get('group') not in group_keys:
            issues.append(_issue('error', w, "group '%s' 未在任何字段包中定义" % f.get('group')))
        t = f.get('type')
        if t not in types:
            issues.append(_issue('error', w, "type '%s' 不在 schema 允许的取值内：%s"
                                 % (t, '、'.join(types.keys()))))
        elif not types[t].get('enabled', True):
            issues.append(_issue('warn', w, "type '%s' 前端表单尚未实现，字段不会渲染" % t))
        if t in ('select', 'multiselect'):
            opts = f.get('options') or []
            if not opts:
                issues.append(_issue('error', w, '%s 必须提供 options' % t))
            vals = [o.get('value') for o in opts]
            if len(vals) != len(set(vals)):
                issues.append(_issue('error', w, 'options 的 value 有重复'))
            if any(not o.get('value') for o in opts):
                issues.append(_issue('error', w, 'options 存在空 value'))
        if t == 'table':
            cols = f.get('columns') or []
            if not cols:
                issues.append(_issue('error', w, 'table 必须提供 columns'))
            for c in cols:
                if not (c.get('key') and c.get('label')):
                    issues.append(_issue('error', w, 'columns 每项必须含 key 与 label'))
        if t == 'number' and not f.get('unit'):
            issues.append(_issue('info', w, 'number 类型建议提供 unit（如 m² / 路 / 台）'))
        for b in ('required', 'allow_custom', 'require_other_detail'):
            if f.get(b) is not None and not isinstance(f[b], bool):
                issues.append(_issue('error', w, '%s 必须是布尔值' % b))
        if f.get('allow_custom') and t not in ('select', 'multiselect'):
            issues.append(_issue('error', w, 'allow_custom 仅用于选择题'))
        if f.get('require_other_detail') and (t != 'multiselect' or not f.get('allow_custom')
                or '其他' not in [o['value'] for o in f.get('options', [])]):
            issues.append(_issue('error', w, 'require_other_detail 需要允许补充文字且含其他选项的多选题'))

        spec = f.get('extract') or {}
        if spec:
            mode = spec.get('mode', 'sentence')
            if mode not in modes:
                issues.append(_issue('error', w, "extract.mode '%s' 不在允许取值内：%s"
                                     % (mode, '、'.join(modes.keys()))))
            elif not modes[mode].get('enabled', True):
                issues.append(_issue('warn', w, "extract.mode '%s' 规则引擎尚未实现" % mode))
            if mode in ('sentence', 'entity', 'number', 'money') and not spec.get('keywords'):
                issues.append(_issue('warn', w,
                                     "extract.mode '%s' 未配置 keywords，规则引擎将命中不到" % mode))
        elif f.get('required'):
            issues.append(_issue('warn', w, '必填字段但没有 extract 配置，只能靠人工填写'))

        v = f.get('validate') or {}
        if v.get('pattern'):
            try:
                re.compile(v['pattern'])
            except re.error as e:
                issues.append(_issue('error', w, 'validate.pattern 不是合法正则：%s' % e))
        for k in ('min', 'max'):
            if k in v and not isinstance(v[k], (int, float)):
                issues.append(_issue('error', w, 'validate.%s 必须是数字' % k))

    return issues


def render_report(product_type):
    """返回 (可读报告, error 条数)。"""
    issues = validate_field_model(product_type)
    errs = sum(1 for i in issues if i['level'] == 'error')
    warns = sum(1 for i in issues if i['level'] == 'warn')
    try:
        # 报告要能展示「有问题时模型长什么样」，所以走宽容合并而不是严格装载
        raw = _load_json(os.path.join(paths.product_dir(product_type), 'fields.json'))
        m = _merge(raw, product_type, problems=[]) if 'packs' in raw else raw
    except Exception as e:
        return '【%s】装载失败：%s' % (product_type, e), max(errs, 1)

    lines = ['【%s】版本 %s ｜ 字段包 %s\n'
             '        分组 %d ｜ 字段 %d ｜ 必填 %d ｜ 错误 %d ｜ 警告 %d'
             % (product_type, m.get('template_version', '-'),
                '、'.join(m.get('packs') or ['(内置扁平格式)']),
                len(m.get('groups', [])), len(m.get('fields', [])),
                sum(1 for f in m.get('fields', []) if f.get('required')),
                errs, warns)]
    if not issues:
        lines.append('  ✅ 校验通过')
    for i in issues:
        mark = {'error': '❌', 'warn': '⚠️ ', 'info': 'ℹ️ '}.get(i['level'], '·')
        if i['level'] == 'error':
            lines.append('  %s %s' % (mark, i['message']))
        else:
            lines.append('  %s [%s] %s' % (mark, i['where'], i['message']))
    return '\n'.join(lines), errs
