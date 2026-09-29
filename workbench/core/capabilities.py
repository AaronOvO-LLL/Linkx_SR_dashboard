"""产品能力声明。

能力 key 是页面、服务和产品配置之间的稳定契约。集中维护可以避免某个
模块被删除后，页面仍通过“配置文件是否碰巧存在”来猜测它是否可用。
"""

from .config import product_config


KNOWN_CAPABILITIES = {
    'text_input': '粘贴或编辑调研文字稿',
    'field_extraction': '从文字稿提取结构化字段',
    'artifact_generation': '预览、生成和打包材料',
    'audio_asr': '导入项目级录音转写稿',
    'image_evidence': '上传并查看现场图片',
}

# audio_asr 的语义是"该产品可以使用项目共享转写稿"，不是"该产品自己上传录音"。
# 上传与转写发生在项目级、选择产品之前，由 config/asr.json 的 enabled 开关控制，
# 不受产品 capability 约束；capability 只决定产品调研页是否显示导入入口。


class CapabilityError(ValueError):
    pass


def product_capabilities(product_type):
    """返回产品显式声明的能力集合。

    不提供隐式默认值，是为了让新模块上线和下线都经过产品配置确认；否则
    新增公共代码可能会意外地给所有历史产品打开入口。
    """
    raw = product_config(product_type).get('capabilities') or []
    return set(raw)


def has_capability(product_type, capability):
    return capability in product_capabilities(product_type)


def validate_capabilities(product_type):
    """验证能力声明，返回可被配置检查器直接展示的问题列表。"""
    declared = product_capabilities(product_type)
    unknown = sorted(declared - set(KNOWN_CAPABILITIES))
    issues = ['未知 capability：%s' % key for key in unknown]
    from .config import field_map, artifacts_config
    from .renderers import RENDERERS
    fields = field_map(product_type)
    for rule in product_config(product_type).get('eligibility_rules', []):
        if rule.get('field') not in fields or rule.get('operator') not in ('contains_any', 'not_equals'):
            issues.append('准入规则引用未知字段或操作符')
        elif rule['operator'] == 'contains_any' and not rule.get('values'):
            issues.append('contains_any 准入规则需提供 values')
        elif rule['operator'] == 'not_equals' and 'value' not in rule:
            issues.append('not_equals 准入规则需提供 value')
        if not rule.get('message'): issues.append('准入规则需提供 message')
    if 'artifact_generation' in declared:
        for artifact in artifacts_config(product_type)['artifacts']:
            renderer = artifact.get('renderer')
            if renderer and renderer not in RENDERERS:
                issues.append('未知 renderer：%s' % renderer)
            if renderer == 'xlsx_table':
                field = fields.get(artifact.get('table_field'), {})
                if field.get('type') != 'table': issues.append('xlsx_table 必须引用 table 字段')
                if not artifact.get('filename'): issues.append('xlsx_table 必须声明 filename')
                if artifact.get('formats') != ['xlsx']: issues.append('xlsx_table 仅支持 xlsx 格式')
                if any(k not in [c['key'] for c in field.get('columns', [])] for k in artifact.get('merge_columns', [])):
                    issues.append('merge_columns 引用了不存在的列')
    return issues


def require_capability(product_type, capability):
    """在服务边界再次校验能力，防止绕过页面直接调用未启用功能。"""
    if capability not in KNOWN_CAPABILITIES:
        raise CapabilityError('系统未注册能力：%s' % capability)
    if not has_capability(product_type, capability):
        raise CapabilityError('产品 %s 未启用能力：%s' % (product_type, capability))

