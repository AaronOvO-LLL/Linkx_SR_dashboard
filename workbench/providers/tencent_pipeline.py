"""腾讯 COS + 录音文件识别适配层。"""

import os
from pathlib import Path

from core.runtime_env import read_env
from tools import tencent_asr_smoke as smoke


class TencentPipelineError(RuntimeError):
    pass


def _environment():
    env = dict(os.environ)
    for name in (
        'TENCENTCLOUD_SECRET_ID', 'TENCENTCLOUD_SECRET_KEY',
        'TENCENT_COS_BUCKET', 'TENCENT_COS_REGION', 'TENCENT_COS_PREFIX',
    ):
        value = read_env(name)
        if value:
            env[name] = value
    return env


def is_configured():
    return all(read_env(name).strip() for name in (
        'TENCENTCLOUD_SECRET_ID', 'TENCENTCLOUD_SECRET_KEY',
        'TENCENT_COS_BUCKET', 'TENCENT_COS_REGION',
    ))


class TencentAsrCosPipeline:
    """把已跑通的联调函数封装在 provider 边界内供业务层使用。"""

    def __init__(self):
        try:
            self.settings = smoke.load_settings(_environment())
            self.sdk = smoke.import_sdks()
            self.cos_client, self.asr_client = smoke.build_clients(self.settings, self.sdk)
        except Exception as exc:
            raise TencentPipelineError(str(exc)) from exc

    def upload(self, local_path, object_key=''):
        audio = smoke.validate_audio(Path(local_path))
        key = object_key or smoke.make_object_key(self.settings, audio)
        smoke.upload_audio(self.cos_client, self.settings, audio, key)
        url = smoke.presigned_download_url(self.cos_client, self.settings, key)
        return key, url

    def submit(self, url, engine, hotword_id=None, speaker_diarization=True):
        task_id, _ = smoke.submit_task(
            self.asr_client, self.sdk['models'], url, engine,
            hotword_id, speaker_diarization)
        return str(task_id)

    def poll(self, provider_job_id):
        payload = smoke.describe_task(
            self.asr_client, self.sdk['models'], int(provider_job_id))
        data = payload.get('Data') or {}
        status = int(data.get('Status', -1))
        if status == 2:
            return 'success', payload
        if status == 3:
            raise TencentPipelineError(str(data.get('ErrorMsg') or '腾讯云转写失败'))
        return 'running', payload

    def delete(self, object_key):
        smoke.delete_cos_object(self.cos_client, self.settings, object_key)
