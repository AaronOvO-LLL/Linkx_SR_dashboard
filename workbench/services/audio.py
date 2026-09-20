"""录音上传、异步转写、结果预览与确认。"""

import json
import os
import threading
import time
import uuid
from pathlib import Path

from core import paths, repo
from core.capabilities import require_capability
from core.config import asr_config, load_json
from providers.tencent_pipeline import TencentAsrCosPipeline, is_configured


class AudioServiceError(ValueError):
    pass


_running = set()
_running_lock = threading.Lock()


def configuration_status():
    return {
        'enabled': bool(asr_config().get('enabled')),
        'provider': asr_config().get('provider', 'tencent'),
        'configured': is_configured(),
    }


def create_job(pp, project, uploaded):
    require_capability(pp['product_type'], 'audio_asr')
    cfg = asr_config()
    original_name = os.path.basename((uploaded.filename or '').strip())
    suffix = Path(original_name).suffix.lower().lstrip('.')
    if not original_name or suffix not in set(cfg.get('allowed_extensions') or []):
        raise AudioServiceError('不支持该录音格式，请上传：%s。' %
                                ' / '.join(cfg.get('allowed_extensions') or []))

    token = uuid.uuid4().hex
    folder = os.path.join(paths.UPLOAD_DIR, pp['id'], token)
    os.makedirs(folder, exist_ok=True)
    local_path = os.path.join(folder, 'original.' + suffix)
    uploaded.save(local_path)
    size = os.path.getsize(local_path)
    limit = int(cfg.get('max_file_mb', 500)) * 1024 * 1024
    if size <= 0 or size > limit:
        try:
            os.remove(local_path)
        except OSError:
            pass
        raise AudioServiceError('录音文件为空或超过 %dMB 上限。' % cfg.get('max_file_mb', 500))

    job = repo.create_audio_job(pp['id'], original_name, local_path, size)
    repo.touch_project(project['id'])
    start_job(job['id'])
    return job


def _terms_config(product_type):
    path = os.path.join(paths.product_dir(product_type), 'asr_terms.json')
    return load_json(path) if os.path.isfile(path) else {}


def _correct_text(text, rules):
    corrected = text or ''
    hits = []
    for rule in rules:
        if not rule.get('enabled', True) or rule.get('mode', 'literal') != 'literal':
            continue
        old, new = str(rule.get('from') or ''), str(rule.get('to') or '')
        if not old or not new:
            continue
        count = corrected.count(old)
        if count:
            corrected = corrected.replace(old, new)
            hits.append({'from': old, 'to': new, 'count': count})
    return corrected, hits


def _normalize_result(payload, rules):
    data = payload.get('Data') or {}
    raw_segments = data.get('ResultDetail') or []
    segments, all_hits = [], []
    raw_lines, corrected_lines = [], []
    for index, item in enumerate(raw_segments, 1):
        raw = str(item.get('FinalSentence') or '').strip()
        if not raw:
            continue
        corrected, hits = _correct_text(raw, rules)
        for hit in hits:
            hit['segment'] = index
            all_hits.append(hit)
        segments.append({
            'index': index,
            'start_ms': int(item.get('StartMs') or 0),
            'end_ms': int(item.get('EndMs') or 0),
            'speaker_id': item.get('SpeakerId'),
            'raw_text': raw,
            'text': corrected,
        })
        raw_lines.append(raw)
        corrected_lines.append(corrected)
    raw_text = '\n'.join(raw_lines) or str(data.get('Result') or '').strip()
    corrected_text = '\n'.join(corrected_lines)
    if not corrected_text:
        corrected_text, all_hits = _correct_text(raw_text, rules)
    return raw_text, corrected_text, segments, all_hits, float(data.get('AudioDuration') or 0)


def _worker(job_id):
    job = repo.get_audio_job(job_id)
    if not job:
        return
    pp = repo.get_pp(job['project_product_id'])
    cfg = asr_config()
    pipeline = None
    object_key = job.get('object_key') or ''
    try:
        pipeline = TencentAsrCosPipeline()
        provider_job_id = job.get('provider_job_id')
        if not provider_job_id:
            repo.update_audio_job(job_id, status='uploading', status_message='正在上传到私有 COS', error='')
            object_key, url = pipeline.upload(job['local_path'], object_key)
            repo.update_audio_job(job_id, status='submitted', status_message='录音已上传，正在创建转写任务',
                                  object_key=object_key)
            terms = _terms_config(pp['product_type'])
            hotword_id = (terms.get('provider') or {}).get('vocab_id')
            provider_job_id = pipeline.submit(
                url, cfg.get('engine_model_type', '16k_zh_en_2.0'), hotword_id,
                bool(cfg.get('speaker_diarization', True)))
            repo.update_audio_job(job_id, provider_job_id=provider_job_id,
                                  status='transcribing', status_message='腾讯云正在转写，可离开本页')

        deadline = time.monotonic() + int(cfg.get('timeout_minutes', 180)) * 60
        while time.monotonic() < deadline:
            status, payload = pipeline.poll(provider_job_id)
            if status == 'success':
                terms = _terms_config(pp['product_type'])
                raw, corrected, segments, hits, duration = _normalize_result(
                    payload, terms.get('corrections') or [])
                if not corrected.strip():
                    raise AudioServiceError('腾讯云返回了空转写结果，请检查录音内容后重试。')
                repo.update_audio_job(
                    job_id, status='awaiting_preview', status_message='转写完成，请预览并确认',
                    raw_transcript=raw, corrected_transcript=corrected,
                    segments_json=json.dumps(segments, ensure_ascii=False),
                    corrections_json=json.dumps(hits, ensure_ascii=False),
                    audio_duration=duration, completed_at=repo.now_iso(), error='')
                if cfg.get('delete_cos_after_completion') and object_key:
                    try:
                        pipeline.delete(object_key)
                    except Exception:
                        # COS 清理失败不能覆盖已经取得的转写结果。
                        pass
                return
            repo.update_audio_job(job_id, status='transcribing', status_message='腾讯云正在转写，可离开本页')
            time.sleep(max(2, int(cfg.get('poll_seconds', 8))))
        repo.update_audio_job(job_id, status='submitted',
                              status_message='云端任务仍在处理，刷新页面后会继续查询')
    except Exception as exc:
        repo.update_audio_job(job_id, status='failed', status_message='转写失败',
                              error='%s：%s' % (type(exc).__name__, exc))
        if pipeline and object_key and cfg.get('delete_cos_after_completion'):
            try:
                pipeline.delete(object_key)
            except Exception:
                pass
    finally:
        with _running_lock:
            _running.discard(job_id)


def start_job(job_id):
    job = repo.get_audio_job(job_id)
    if not job or job['status'] not in ('pending', 'uploading', 'submitted', 'transcribing'):
        return False
    with _running_lock:
        if job_id in _running:
            return False
        _running.add(job_id)
    thread = threading.Thread(target=_worker, args=(job_id,), daemon=True,
                              name='asr-' + job_id[-6:])
    thread.start()
    return True


def retry_job(job):
    repo.update_audio_job(
        job['id'], status='pending', status_message='等待重新转写', error='',
        provider_job_id=None, object_key=None)
    start_job(job['id'])


def approve_transcript(job, transcript):
    text = (transcript or '').strip()
    if not text:
        raise AudioServiceError('转写结果不能为空。')
    repo.update_audio_job(job['id'], corrected_transcript=text,
                          status='approved', status_message='已确认并进入结构化梳理',
                          approved_at=repo.now_iso())
    return text
