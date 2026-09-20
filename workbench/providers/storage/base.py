"""对象存储的厂商无关接口。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class StoredObject:
    """只保存稳定对象标识，不保存会过期的签名 URL。"""

    key: str
    size_bytes: int
    content_type: str = ''
    etag: str = ''


class ObjectStorage(ABC):
    """COS、OSS 或本地测试存储必须实现的最小接口。

    签名 URL 与 object key 分开，是因为 URL 会过期且可能泄露访问权限，不能
    被当作资产的永久身份写入数据库。
    """

    @abstractmethod
    def put_file(self, local_path, object_key, content_type=''):
        """上传本地文件并返回稳定元数据。"""

    @abstractmethod
    def create_download_url(self, object_key, expires_seconds=600):
        """按需生成短时下载地址。"""

    @abstractmethod
    def delete_object(self, object_key):
        """删除对象；实现必须把“对象不存在”视为幂等成功。"""

