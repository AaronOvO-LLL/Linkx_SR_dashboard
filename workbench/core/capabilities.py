"""产品能力声明。

能力 key 是页面、服务和产品配置之间的稳定契约。集中维护可以避免某个
模块被删除后，页面仍通过“配置文件是否碰巧存在”来猜测它是否可用。
"""

from .config import product_config


KNOWN_CAPABILITIES = {
    'text_input': '粘贴或编辑调研文字稿',
    'field_extraction': '从文字稿提取结构化字段',
    'artifact_generation': '预览、生成和打包材料',
    'audio_asr': '上传录音并异步转写',
    'image_evidence': '上传并查看现场图片',
}


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
    return ['未知 capability：%s' % key for key in unknown]


def require_capability(product_type, capability):
    """在服务边界再次校验能力，防止绕过页面直接调用未启用功能。"""
    if capability not in KNOWN_CAPABILITIES:
        raise CapabilityError('系统未注册能力：%s' % capability)
    if not has_capability(product_type, capability):
        raise CapabilityError('产品 %s 未启用能力：%s' % (product_type, capability))

