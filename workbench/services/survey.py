"""调研输入与字段提取用例。"""

from core import extract, repo
from core.capabilities import require_capability
from core.config import app_config, field_map, product_config
from core.runtime_env import read_env
from core.validate import validate


class SurveyServiceError(ValueError):
    pass


def save_transcript(pp, project, text):
    """校验并保存文字稿。

    长度规则放在服务层而不是路由中，确保未来 API、批量导入和页面入口使用
    同一条业务约束，不会出现不同入口保存出不同质量的数据。
    """
    require_capability(pp['product_type'], 'text_input')
    limits = app_config()['extraction']
    if len((text or '').strip()) < limits['min_transcript_chars']:
        raise SurveyServiceError('文字稿过短，请粘贴完整的调研内容。')
    return repo.save_source(pp['id'], text, kind='transcript')


def save_audio_transcript(pp, project, text):
    """把人工预览确认后的转写稿提升为正式提取来源。"""
    require_capability(pp['product_type'], 'audio_asr')
    limits = app_config()['extraction']
    if len((text or '').strip()) < limits['min_transcript_chars']:
        raise SurveyServiceError('转写稿过短，暂时无法进行结构化梳理。')
    source_id = repo.save_source(pp['id'], text, kind='audio_transcript')
    repo.touch_project(project['id'])
    return source_id


def extract_latest_source(pp, project, provider=None):
    """执行一次可追溯的提取，并保护已经人工确认的字段。

    先建立运行记录再调用 provider，是为了让失败也能留下审计信息；人工确认
    字段不覆盖则是重新转写、重新提取时必须守住的数据边界。
    """
    require_capability(pp['product_type'], 'field_extraction')
    src = repo.latest_source(pp['id'])
    if not src:
        raise SurveyServiceError('请先保存文字稿。')

    protected = [k for k, v in repo.field_values(pp['id']).items() if v['protected']]
    template_version = product_config(pp['product_type'])['version']
    provider = provider or read_env('LS_FORCE_EXTRACTION_PROVIDER',
                                    app_config()['extraction']['provider'])
    run_id = repo.start_extraction_run(
        pp['id'], provider, template_version, src['char_count'])
    try:
        result, stats = extract.run_extraction(
            src['content'], pp['product_type'], protected, provider=provider)
    except extract.ExtractionError as exc:
        repo.finish_extraction_run(run_id, 'failed', {}, str(exc))
        raise SurveyServiceError(str(exc)) from exc

    for key, item in result.items():
        if item['protected']:
            continue
        repo.upsert_field_value(
            pp['id'], key, repo.json.dumps(item['value'], ensure_ascii=False),
            item['status'], item['confidence'], item['quote'], 'ai',
            template_version, 0)
    repo.finish_extraction_run(run_id, 'success', stats)
    repo.set_pp_status(pp['id'], 'pending_fill')
    repo.touch_project(project['id'])
    return stats, len(protected)


def save_field_value(pp, project, data):
    """保存人工字段值，并返回页面刷新状态所需的最小结果。"""
    key = data.get('key', '')
    fmap = field_map(pp['product_type'])
    if key not in fmap:
        raise SurveyServiceError('未知字段')

    value = data.get('value')
    is_empty = value in (None, '', [], {})
    field = fmap[key]
    if is_empty:
        status = 'required_missing' if field.get('required') else 'optional_missing'
        protected, updated_by = 0, 'ai'
    else:
        status, protected, updated_by = 'confirmed', 1, 'human'

    repo.upsert_field_value(
        pp['id'], key, repo.json.dumps(value, ensure_ascii=False), status,
        None, data.get('quote', ''), updated_by, pp['template_version'], protected)
    repo.touch_project(project['id'])
    ok, _ = validate(pp['id'], pp['product_type'])
    stats = repo.field_stats(pp['id'], pp['product_type'])
    return status, ok, stats
