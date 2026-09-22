import json
import os
from . import paths

_CACHE = {}


def load_json(path, use_cache=True):
    if use_cache and path in _CACHE:
        return _CACHE[path]
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if use_cache:
        _CACHE[path] = data
    return data


def reload_all():
    _CACHE.clear()
    from . import fieldmodel
    fieldmodel.reload_all()


def app_config():
    return load_json(os.path.join(paths.CONFIG_DIR, 'app.json'))


def asr_config():
    return load_json(os.path.join(paths.CONFIG_DIR, 'asr.json'))


def asr_terms_config():
    """项目级 ASR 热词与术语纠错词表。

    转写发生在选择产品之前，此时无法确定产品类型，所以词表必须是项目级的
    一份合并结果，而不是各产品目录下的 asr_terms.json。文件缺失时返回空配置，
    让转写退化为"不带热词、不做纠错"，而不是直接失败。
    """
    path = os.path.join(paths.CONFIG_DIR, 'asr_terms.json')
    return load_json(path) if os.path.isfile(path) else {}


def list_products():
    """扫描 config/products/ 下所有产品定义。

    以 ``_`` 开头的目录（如 _template 脚手架）不视为产品。
    """
    out = []
    if not os.path.isdir(paths.PRODUCTS_DIR):
        return out
    for name in sorted(os.listdir(paths.PRODUCTS_DIR)):
        if name.startswith('_'):
            continue
        p = os.path.join(paths.PRODUCTS_DIR, name, 'product.json')
        if os.path.isfile(p):
            out.append(load_json(p))
    return out


def product_config(product_type):
    return load_json(os.path.join(paths.product_dir(product_type), 'product.json'))


def field_config(product_type):
    """字段模型：由 field_packs/*.json + 本产品组装清单合并而成。

    合并逻辑见 core/fieldmodel.py。返回值与旧版单文件 fields.json 同构，
    消费方（extract / generate / validate / 模板 / 前端）无需感知。
    """
    from . import fieldmodel
    return fieldmodel.load_field_model(product_type)


def artifacts_config(product_type):
    return load_json(os.path.join(paths.product_dir(product_type), 'artifacts.json'))


def fields_by_group(product_type):
    """返回 [(group, [field,...]), ...]，按分组顺序与字段顺序排列。"""
    cfg = field_config(product_type)
    groups = sorted(cfg['groups'], key=lambda g: g['order'])
    bucket = {g['key']: [] for g in groups}
    for f in cfg['fields']:
        bucket.setdefault(f['group'], []).append(f)
    return [(g, bucket.get(g['key'], [])) for g in groups]


def field_map(product_type):
    return {f['key']: f for f in field_config(product_type)['fields']}


def required_fields(product_type):
    return [f for f in field_config(product_type)['fields'] if f.get('required')]


def artifact_map(product_type):
    return {a['key']: a for a in artifacts_config(product_type)['artifacts']}
