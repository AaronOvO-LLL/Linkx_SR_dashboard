"""生成物用例层。"""

import json

from core import generate, packaging, repo
from core.capabilities import require_capability
from core.config import artifact_map, artifacts_config
from core.validate import validate


class ArtifactServiceError(ValueError):
    pass


def build_preview(pp):
    """准备预览上下文并持久化本次校验结果。

    校验结果在预览时落库，是为了让项目进度和后续生成使用同一个事实，避免
    页面显示“可生成”但状态统计仍停留在上一步。
    """
    require_capability(pp['product_type'], 'artifact_generation')
    ok, issues = validate(pp['id'], pp['product_type'])
    repo.save_validation(pp['id'], ok, issues)
    ctx = generate.build_context(pp['id'], pp['product_type'])
    definitions = [dict(item) for item in artifacts_config(pp['product_type'])['artifacts']]
    runs = {row['artifact_key']: row for row in repo.artifact_runs(pp['id'])}
    for definition in definitions:
        definition['run'] = runs.get(definition['key'])
    return ok, issues, ctx, definitions


def generate_artifacts(pp, keys):
    """过滤非法 key、执行校验并生成选中的材料。"""
    require_capability(pp['product_type'], 'artifact_generation')
    valid = set(artifact_map(pp['product_type']))
    selected = [key for key in keys if key in valid]
    if not selected:
        raise ArtifactServiceError('请至少选择一项生成物。')
    ok, _ = validate(pp['id'], pp['product_type'])
    if not ok:
        raise ArtifactServiceError('存在未通过的必填校验，请先补齐后再生成。')
    return generate.generate_selected(pp['id'], pp['product_type'], selected)


def regenerate_artifact(pp, key):
    """只重试一个生成物，避免单项失败导致其它成功结果被覆盖。"""
    require_capability(pp['product_type'], 'artifact_generation')
    if key not in artifact_map(pp['product_type']):
        raise ArtifactServiceError('未知生成物。')
    repo.reset_artifact_run(pp['id'], key)
    return generate.generate_selected(pp['id'], pp['product_type'], [key])[0]


def package_artifacts(pp, keys=None):
    """打包当前产品仍声明的生成物。"""
    require_capability(pp['product_type'], 'artifact_generation')
    return packaging.package_zip(pp['id'], pp['product_type'], keys)


def latest_current_export(pp):
    """只返回与当前生成物契约兼容的最近导出包。

    已下线模块可能仍存在于历史 ZIP 中。文件继续保留用于审计，但不再通过
    当前业务页面下载，避免用户误把旧报价包当成新流程产物。
    """
    export = repo.latest_export(pp['id'])
    if not export:
        return None
    try:
        manifest = json.loads(export['manifest_json'] or '{}')
        exported_keys = {item['key'] for item in manifest.get('artifacts', [])}
    except (KeyError, TypeError, ValueError):
        return None
    current_keys = set(artifact_map(pp['product_type']))
    return export if exported_keys <= current_keys else None
