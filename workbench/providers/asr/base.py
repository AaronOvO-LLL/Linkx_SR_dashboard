"""异步语音识别的厂商无关接口。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class AsrStatus(str, Enum):
    PENDING = 'pending'
    RUNNING = 'running'
    SUCCESS = 'success'
    FAILED = 'failed'
    CANCELLED = 'cancelled'


@dataclass(frozen=True)
class AsrResult:
    transcript: str
    segments: list = field(default_factory=list)
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class AsrJob:
    provider_job_id: str
    status: AsrStatus
    result: AsrResult | None = None
    error: str = ''


class AsrProvider(ABC):
    """所有 ASR 厂商实现都必须提供提交和查询两个动作。

    接口刻意不提供“同步识别”，因为长录音必须跨请求持久化任务 ID；否则
    页面刷新或服务重启会导致重复提交、重复计费和无法恢复。
    """

    @abstractmethod
    def submit(self, audio_url, options=None):
        """提交一次异步识别，返回包含厂商任务 ID 的 AsrJob。"""

    @abstractmethod
    def poll(self, provider_job_id):
        """查询任务状态；成功时同时返回标准化 AsrResult。"""

