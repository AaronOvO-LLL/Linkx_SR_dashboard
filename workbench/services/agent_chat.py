"""Browser-scoped, in-memory chat sessions for the single-process workbench.

Workers finish independently of page navigation. Credentials and upstream IDs
never go into cookies or browser storage. Restarting the server clears chats.
"""
from dataclasses import dataclass, field
from pathlib import Path
import secrets
import threading
import time

from core import runtime, runtime_env
from providers.gc_agent import AgentClient, AgentError

ROOT = Path(__file__).resolve().parents[1]
MAX_CONTENT = 4000
MAX_MESSAGES = 100
SESSION_TTL = 8 * 3600
MAX_SESSIONS = 200


def _env_file(path):
    if not path.is_file():
        return {}
    values = {}
    for raw in path.read_text(encoding='utf-8-sig').splitlines():
        key, sep, value = raw.strip().partition('=')
        if sep and key.startswith('GC_'):
            values[key.strip()] = value.strip().strip('\"\'')
    return values


def create_client():
    local = _env_file(ROOT / '.env')
    # Reuse the existing local test credentials; deployed workbench is standalone.
    fallback = {} if runtime.is_production() else _env_file(ROOT.parents[1] / 'agent_cli_mvp' / '.env')

    def value(key, default=''):
        return runtime_env.read_env(key).strip() or local.get(key) or fallback.get(key) or default

    required = ['GC_APP_ID', 'GC_SECRET_KEY', 'GC_BOT_ID']
    if any(not value(key) for key in required):
        raise AgentError('姜子牙尚未配置，请在服务器配置 GC_APP_ID、GC_SECRET_KEY 和 GC_BOT_ID。')
    try:
        return AgentClient(
            value('GC_BASE_URL', 'https://gc.4009515151.com'),
            value('GC_APP_ID'), value('GC_SECRET_KEY'), value('GC_BOT_ID'),
            user_id=value('GC_USER_ID', 'lingshi_cli_mvp'),
            user_name=value('GC_USER_NAME', '命令行测试用户'),
            timeout=float(value('GC_TIMEOUT', '120')),
            context_mode=value('GC_CONTEXT_MODE', 'client'))
    except ValueError:
        raise AgentError('GC_TIMEOUT 配置必须为有效秒数。') from None


class ChatError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@dataclass
class Conversation:
    owner: str
    generation: str = field(default_factory=lambda: secrets.token_urlsafe(16))
    messages: list = field(default_factory=list)
    client: object = None
    pending: bool = False
    error: str = ''
    touched: float = field(default_factory=time.monotonic)
    lock: object = field(default_factory=threading.Lock)


class ChatStore:
    def __init__(self, client_factory=create_client):
        self.sessions = {}
        self.lock = threading.Lock()
        self.client_factory = client_factory

    def _get(self, sid, owner):
        with self.lock:
            now = time.monotonic()
            for key, old in list(self.sessions.items()):
                if not old.pending and now - old.touched > SESSION_TTL:
                    del self.sessions[key]
            chat = self.sessions.get(sid)
            if chat is None:
                if len(self.sessions) >= MAX_SESSIONS:
                    raise ChatError('当前对话人数较多，请稍后再试。', 503)
                chat = self.sessions[sid] = Conversation(owner)
            if chat.owner != owner:
                raise ChatError('当前对话不属于此分部，请重新登录。', 403)
            chat.touched = now
            return chat

    @staticmethod
    def _snapshot(chat):
        return {'generation': chat.generation, 'messages': list(chat.messages),
                'pending': chat.pending, 'error': chat.error,
                'context_trimmed': bool(chat.client and chat.client.context_trimmed)}

    def state(self, sid, owner):
        chat = self._get(sid, owner)
        with chat.lock:
            return self._snapshot(chat)

    def new(self, sid, owner):
        chat = self._get(sid, owner)
        with chat.lock:
            if chat.pending:
                raise ChatError('请等待当前回复完成后再开始新对话。', 409)
            chat.client = None
            chat.messages.clear()
            chat.error = ''
            chat.generation = secrets.token_urlsafe(16)
            return self._snapshot(chat)

    def send(self, sid, owner, content, generation):
        if not isinstance(content, str) or not content.strip():
            raise ChatError('请输入消息。')
        content = content.strip()
        if len(content) > MAX_CONTENT:
            raise ChatError('每条消息最多 4000 字，请分段发送。')
        chat = self._get(sid, owner)
        with chat.lock:
            if generation != chat.generation:
                raise ChatError('对话已更新，请重新发送。', 409)
            if chat.pending:
                raise ChatError('姜子牙正在回复，请稍等。', 409)
            if len(chat.messages) >= MAX_MESSAGES:
                raise ChatError('本次对话已达 50 轮，请开始新对话。', 409)
            if chat.client is None:
                try:
                    chat.client = self.client_factory()
                except AgentError as exc:
                    raise ChatError(str(exc), 503) from None
            message = {'role': 'user', 'content': content, 'status': 'pending'}
            chat.messages.append(message)
            chat.pending = True
            chat.error = ''
            try:
                threading.Thread(target=self._reply, args=(chat, message), daemon=True).start()
            except RuntimeError:
                chat.messages.pop()
                chat.pending = False
                raise ChatError('暂时无法发送，请稍后重试。', 503) from None
            return self._snapshot(chat)

    def _reply(self, chat, message):
        try:
            if not chat.client.message_id:
                chat.client.start_session()
            answer = chat.client.send(message['content'])
            with chat.lock:
                message['status'] = 'sent'
                chat.messages.append({'role': 'assistant', 'content': answer, 'status': 'sent'})
        except Exception as exc:
            # Never retry automatically: a timed-out upstream message may have arrived.
            with chat.lock:
                message['status'] = 'error'
                chat.error = (str(exc) if isinstance(exc, AgentError)
                              else '暂时无法取得回复，请稍后再试。')
        finally:
            with chat.lock:
                chat.pending = False
                chat.touched = time.monotonic()

    def discard(self, sid):
        with self.lock:
            self.sessions.pop(sid, None)


store = ChatStore()
