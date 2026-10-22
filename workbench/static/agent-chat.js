(function () {
  'use strict';
  var root = document.getElementById('agent-chat');
  if (!root) return;
  var launcher = document.getElementById('chat-launcher');
  var panel = document.getElementById('chat-panel');
  var log = document.getElementById('chat-messages');
  var input = document.getElementById('chat-input');
  var send = document.getElementById('chat-send');
  var fresh = document.getElementById('chat-new');
  var error = document.getElementById('chat-error');
  var notice = document.getElementById('chat-notice');
  var reconnect = document.getElementById('chat-reload');
  var unread = root.querySelector('.chat-unread');
  var storageKey = 'lingshi-chat-' + root.dataset.scope;
  var state = null, busy = false, connected = false, timer, syncing = false;
  var signature = '', initial = true, revision = 0;

  function saved(key, value) {
    try {
      if (arguments.length === 2) sessionStorage.setItem(storageKey + key, value);
      else return sessionStorage.getItem(storageKey + key) || '';
    } catch (_) { return ''; }
  }
  input.value = saved('-draft');

  function showError(message) {
    error.textContent = message || '';
    error.hidden = !message;
  }
  function controls() {
    send.disabled = busy || !connected || !state || state.pending || !input.value.trim();
    fresh.disabled = busy || !connected || !state || state.pending;
    reconnect.hidden = connected;
  }
  async function api(url, body) {
    var controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, 15000);
    try {
      var options = {credentials: 'same-origin', cache: 'no-store', signal: controller.signal};
      if (body !== undefined) {
        options.method = 'POST';
        options.headers = {'Content-Type': 'application/json', 'X-Chat-CSRF': root.dataset.csrf,
          'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]').content};
        options.body = JSON.stringify(body);
      }
      var response = await fetch(url, options);
      var data;
      try { data = await response.json(); } catch (_) { throw new Error('服务连接异常，请重新连接。'); }
      if (!response.ok) throw new Error(data.error || '暂时无法连接姜子牙。');
      return data;
    } finally { clearTimeout(timeout); }
  }
  function element(tag, className, text) {
    var node = document.createElement(tag);
    node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function render(data) {
    var changed = JSON.stringify([data.generation, data.messages, data.pending]);
    var old = state;
    state = data;
    connected = true;
    if (changed !== signature) {
      var bottom = log.scrollHeight - log.scrollTop - log.clientHeight < 70;
      var scroll = log.scrollTop;
      var fragment = document.createDocumentFragment();
      if (!data.messages.length) {
        var welcome = element('div', 'chat-welcome');
        welcome.appendChild(element('strong', '', '你好，我是姜子牙'));
        welcome.appendChild(element('p', '', '可以和我聊聊灵石产品、业务场景和解决方案。'));
        var suggestions = element('div', 'chat-suggestions');
        ['灵石有哪些产品？', '设备管家适合什么场景？'].forEach(function (question) {
          var button = element('button', '', question);
          button.type = 'button';
          button.addEventListener('click', function () {
            input.value = question;
            saved('-draft', question);
            controls();
            input.focus();
          });
          suggestions.appendChild(button);
        });
        welcome.appendChild(suggestions);
        fragment.appendChild(welcome);
      }
      data.messages.forEach(function (message) {
        var row = element('div', 'chat-message ' + (message.role === 'user' ? 'user' : 'assistant') + (message.status === 'error' ? ' failed' : ''));
        row.appendChild(element('span', 'chat-message-label', message.role === 'user' ? '你' : '姜子牙'));
        // Agent text is untrusted. Do not inject returned HTML into the page.
        row.appendChild(element('div', 'chat-bubble', message.content));
        if (message.status === 'error') row.appendChild(element('span', 'chat-message-status', '未取得回复 · 未自动重发'));
        fragment.appendChild(row);
      });
      if (data.pending) fragment.appendChild(element('div', 'chat-thinking', '姜子牙正在思考，请稍候…'));
      log.replaceChildren(fragment);
      signature = changed;
      if (bottom || initial || !old || old.generation !== data.generation) log.scrollTop = log.scrollHeight;
      else log.scrollTop = scroll;
      if (panel.hidden && old && old.pending && !data.pending) unread.hidden = false;
    }
    notice.textContent = data.context_trimmed ? '对话较长，后续回复将参考最近的部分对话。' : '';
    notice.hidden = !data.context_trimmed;
    showError(data.error);
    initial = false;
    controls();
  }
  function schedule() {
    clearTimeout(timer);
    if (!panel.hidden || (state && state.pending)) {
      timer = setTimeout(sync, !connected ? 5000 : (state && state.pending ? 1200 : 8000));
    }
  }
  async function sync() {
    if (syncing || busy) { schedule(); return; }
    syncing = true;
    var requestedRevision = revision;
    try {
      var data = await api(root.dataset.stateUrl);
      if (requestedRevision === revision) render(data);
    }
    catch (exc) {
      if (requestedRevision !== revision) return;
      connected = false;
      showError(exc.message === 'Failed to fetch' || exc.name === 'AbortError' ? '连接中断，请检查网络后重新连接。' : exc.message);
      controls();
    } finally { syncing = false; schedule(); }
  }
  function openPanel(open) {
    panel.hidden = !open;
    launcher.setAttribute('aria-expanded', String(open));
    saved('-open', open ? '1' : '');
    if (open) {
      unread.hidden = true;
      log.scrollTop = log.scrollHeight;
      input.focus();
      sync();
    } else { launcher.focus(); schedule(); }
  }
  launcher.addEventListener('click', function () { openPanel(panel.hidden); });
  document.getElementById('chat-close').addEventListener('click', function () { openPanel(false); });
  panel.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') { event.preventDefault(); openPanel(false); }
  });
  input.addEventListener('input', function () { saved('-draft', input.value); controls(); });
  input.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      if (!send.disabled) document.getElementById('chat-form').requestSubmit();
    }
  });
  document.getElementById('chat-form').addEventListener('submit', async function (event) {
    event.preventDefault();
    if (send.disabled) return;
    var content = input.value.trim();
    revision += 1;
    busy = true;
    clearTimeout(timer);
    controls();
    try {
      var data = await api(root.dataset.sendUrl, {content: content, generation: state.generation});
      input.value = '';
      saved('-draft', '');
      render(data);
      log.scrollTop = log.scrollHeight;
    } catch (exc) {
      connected = false;
      showError(exc.name === 'AbortError' || exc.message === 'Failed to fetch'
        ? '发送状态尚未确认。请重新连接，核对聊天记录后再发送，避免重复提交。' : exc.message);
    } finally { busy = false; controls(); schedule(); }
  });
  fresh.addEventListener('click', async function () {
    if (fresh.disabled) return;
    revision += 1;
    busy = true;
    clearTimeout(timer);
    controls();
    try { render(await api(root.dataset.newUrl, {})); input.focus(); }
    catch (exc) { connected = false; showError(exc.name === 'AbortError' ? '连接超时，请重新连接后查看对话状态。' : exc.message); }
    finally { busy = false; controls(); schedule(); }
  });
  reconnect.addEventListener('click', sync);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) sync(); });
  if (saved('-open') === '1') openPanel(true);
  else sync();
  controls();
})();
