"""Chat isolation, async recovery and HTTP authorization; no real Agent calls."""
import threading
import time
import unittest
from unittest.mock import patch

from providers.gc_agent import AgentError
from services.agent_chat import ChatError, ChatStore


class FakeClient:
    def __init__(self, gate=None, failure=False):
        self.message_id = ''
        self.context_trimmed = False
        self.history = []
        self.gate = gate
        self.failure = failure

    def start_session(self):
        self.message_id = 'upstream-id-never-exposed'

    def send(self, content):
        if self.gate:
            self.gate.wait(3)
        if self.failure:
            raise AgentError('请求超时。未自动重发消息。')
        answer = '<script>alert(1)</script> 回复：' + content
        self.history.append((content, answer))
        return answer


class StoreTests(unittest.TestCase):
    def finished(self, store, sid='browser-a'):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            result = store.state(sid, 'dept-a')
            if not result['pending']:
                return result
            time.sleep(.01)
        self.fail('worker did not finish')

    def test_browser_isolation_and_followup_reuses_client(self):
        clients = []

        def factory():
            client = FakeClient()
            clients.append(client)
            return client

        store = ChatStore(factory)
        state = store.state('browser-a', 'dept-a')
        store.send('browser-a', 'dept-a', '第一条', state['generation'])
        result = self.finished(store)
        self.assertEqual(len(result['messages']), 2)
        self.assertNotIn('upstream-id', str(result))
        self.assertEqual(store.state('browser-b', 'dept-a')['messages'], [])
        store.send('browser-a', 'dept-a', '第二条', state['generation'])
        self.finished(store)
        self.assertEqual(len(clients), 1)
        self.assertEqual(len(clients[0].history), 2)
        with self.assertRaises(ChatError):
            store.state('browser-a', 'other-dept')

    def test_navigation_can_recover_pending_and_completed_reply(self):
        gate = threading.Event()
        store = ChatStore(lambda: FakeClient(gate))
        state = store.state('browser-a', 'dept-a')
        accepted = store.send('browser-a', 'dept-a', '测试', state['generation'])
        self.assertTrue(accepted['pending'])
        self.assertTrue(store.state('browser-a', 'dept-a')['pending'])
        try:
            with self.assertRaises(ChatError):
                store.send('browser-a', 'dept-a', '重复', state['generation'])
            with self.assertRaises(ChatError):
                store.new('browser-a', 'dept-a')
        finally:
            gate.set()
        result = self.finished(store)
        self.assertEqual(result['messages'][-1]['role'], 'assistant')
        self.assertFalse(result['error'])

    def test_new_conversation_drops_context_and_rejects_stale_page(self):
        store = ChatStore(FakeClient)
        old = store.state('browser-a', 'dept-a')
        store.send('browser-a', 'dept-a', '旧对话', old['generation'])
        self.finished(store)
        new = store.new('browser-a', 'dept-a')
        self.assertNotEqual(old['generation'], new['generation'])
        self.assertEqual(new['messages'], [])
        self.assertIsNone(store.sessions['browser-a'].client)
        with self.assertRaises(ChatError):
            store.send('browser-a', 'dept-a', '过期页面', old['generation'])

    def test_timeout_is_visible_without_automatic_resend(self):
        client = FakeClient(failure=True)
        store = ChatStore(lambda: client)
        state = store.state('browser-a', 'dept-a')
        store.send('browser-a', 'dept-a', '测试', state['generation'])
        result = self.finished(store)
        self.assertIn('超时', result['error'])
        self.assertEqual(len(result['messages']), 1)
        self.assertEqual(result['messages'][0]['status'], 'error')

    def test_invalid_input_and_configuration_do_not_queue_messages(self):
        store = ChatStore(lambda: (_ for _ in ()).throw(AgentError('未配置')))
        state = store.state('browser-a', 'dept-a')
        for content in (None, '', '  ', 123, '字' * 4001, '你好'):
            with self.assertRaises(ChatError):
                store.send('browser-a', 'dept-a', content, state['generation'])
        self.assertEqual(store.state('browser-a', 'dept-a')['messages'], [])


class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app
        cls.module = app

    def setUp(self):
        self.store = ChatStore(FakeClient)
        self.store_patch = patch.object(self.module.agent_chat, 'store', self.store)
        self.store_patch.start()
        self.dept_patch = patch.object(self.module.departments, 'find', side_effect=lambda key: {'key': key, 'name': key} if key else None)
        self.dept_patch.start()
        self.client = self.module.app.test_client()

    def tearDown(self):
        self.store_patch.stop()
        self.dept_patch.stop()

    def login(self):
        with self.client.session_transaction() as session:
            session['dept_key'] = 'dept-a'
        state = self.client.get('/api/agent-chat').get_json()
        with self.client.session_transaction() as session:
            self.csrf = session['chat_csrf']
        return state

    def test_anonymous_requests_are_json_401(self):
        for path in ('/api/agent-chat', '/api/agent-chat/messages', '/api/agent-chat/new'):
            response = self.client.get(path) if path == '/api/agent-chat' else self.client.post(path, json={})
            self.assertEqual(response.status_code, 401)
            self.assertTrue(response.is_json)

    def test_csrf_required_and_malformed_input_rejected(self):
        self.login()
        self.assertEqual(self.client.post('/api/agent-chat/messages', json={}).status_code, 403)
        headers = {'X-Chat-CSRF': self.csrf}
        self.assertEqual(self.client.post('/api/agent-chat/messages', json=[], headers=headers).status_code, 400)
        self.assertEqual(self.client.post('/api/agent-chat/messages', json={'content': 'x' * 4001}, headers=headers).status_code, 400)

    def test_send_and_logout_clear_session(self):
        state = self.login()
        response = self.client.post('/api/agent-chat/messages', json={'content': '你好', 'generation': state['generation']}, headers={'X-Chat-CSRF': self.csrf})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        with self.client.session_transaction() as session:
            sid = session['chat_sid']
        self.client.get('/logout')
        self.assertNotIn(sid, self.store.sessions)
        self.assertEqual(self.client.get('/api/agent-chat').status_code, 401)

    def test_global_widget_is_only_in_authenticated_templates(self):
        self.assertNotIn('id="agent-chat"', self.client.get('/login').get_data(as_text=True))
        self.login()
        # The 404 page also inherits the common template; no business data needed.
        page = self.client.get('/missing-page').get_data(as_text=True)
        self.assertIn('id="agent-chat"', page)
        self.assertIn('agent-chat.js', page)


if __name__ == '__main__':
    unittest.main()
