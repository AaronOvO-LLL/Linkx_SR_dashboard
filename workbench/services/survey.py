"""调研输入与字段提取用例。"""

from core import extract, repo
from core.capabilities import require_capability
from core.config import app_config, field_map, field_config
from core.choices import normalize_manual_choice
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


def import_project_transcript(pp, project):
    """把项目级共享转写稿导入为当前产品的提取来源。

    采用复制而非引用：产品导入后可以独立微调文字、独立保留自己的提取历史，
    多个产品之间不会互相覆盖。重复导入会以最新一份项目稿重新覆盖当前产品的
    来源，已人工确认的字段仍由 extract_latest_source 保护。
    """
    require_capability(pp['product_type'], 'audio_asr')
    row = repo.latest_project_transcript(project['id'])
    text = (row['content'] if row else '') or ''
    if not text.strip():
        raise SurveyServiceError('项目还没有已确认的录音转写稿，请先在项目概览中上传并确认。')
    limits = app_config()['extraction']
    if len(text.strip()) < limits['min_transcript_chars']:
        raise SurveyServiceError('转写稿过短，暂时无法进行结构化梳理。')
    source_id = repo.save_source(pp['id'], text, kind='audio_transcript')
    repo.touch_project(project['id'])
    return source_id, row


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
    template_version = field_config(pp['product_type'])['template_version']
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

    # 模型运行期间用户也可能在填表，写回前再次检查人工确认状态。
    current_values = repo.field_values(pp['id'])
    protected = [k for k, v in current_values.items() if v['protected']]
    for key, item in result.items():
        if item['protected'] or key in protected:
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
    field = fmap[key]
    if field['type'] in ('select', 'multiselect'):
        try:
            value = normalize_manual_choice(field, value)
        except ValueError as exc:
            raise SurveyServiceError(str(exc)) from exc
    is_empty = value in (None, '', [], {})
    if is_empty:
        status = 'required_missing' if field.get('required') else 'optional_missing'
        protected, updated_by = 0, 'ai'
    else:
        status, protected, updated_by = 'confirmed', 1, 'human'

    repo.upsert_field_value(
        pp['id'], key, repo.json.dumps(value, ensure_ascii=False), status,
        None, data.get('quote', ''), updated_by,
        field_config(pp['product_type'])['template_version'], protected)
    repo.touch_project(project['id'])
    ok, _ = validate(pp['id'], pp['product_type'])
    stats = repo.field_stats(pp['id'], pp['product_type'])
    return status, ok, stats
