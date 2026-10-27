/* 本页支持 Markdown 标题、段落、无序列表、引用、粗体和 HTTP(S) 链接。
   文本使用 DOM 节点渲染，不执行 Markdown 内的 HTML。 */
(function () {
  'use strict';
  function inline(parent, text) {
    var pattern = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|\*\*([^*]+)\*\*/g;
    var end = 0, match;
    while ((match = pattern.exec(text))) {
      parent.appendChild(document.createTextNode(text.slice(end, match.index)));
      var el = document.createElement(match[3] ? 'strong' : 'a');
      el.textContent = match[3] || match[1];
      if (!match[3]) { el.href = match[2]; el.target = '_blank'; el.rel = 'noopener noreferrer'; }
      parent.appendChild(el);
      end = pattern.lastIndex;
    }
    parent.appendChild(document.createTextNode(text.slice(end)));
  }
  function render(text, root) {
    root.replaceChildren();
    var parent = root, list = null, paragraph = null;
    text.replace(/^\uFEFF/, '').split(/\r?\n/).forEach(function (line) {
      line = line.trim();
      if (!line) { list = null; paragraph = null; return; }
      var heading = /^(#{1,6})\s+(.+)$/.exec(line);
      var item = /^[-*+]\s+(.+)$/.exec(line);
      var quote = /^>\s?(.*)$/.exec(line);
      if (heading) {
        list = null; paragraph = null;
        if (heading[1].length === 1) parent = root;
        if (heading[1].length === 2) { parent = document.createElement('section'); root.appendChild(parent); }
        var h = document.createElement('h' + heading[1].length);
        inline(h, heading[2]); parent.appendChild(h);
      } else if (item) {
        paragraph = null;
        if (!list) { list = document.createElement('ul'); parent.appendChild(list); }
        var li = document.createElement('li'); inline(li, item[1]); list.appendChild(li);
      } else if (quote) {
        list = null; paragraph = null;
        var block = document.createElement('blockquote'); inline(block, quote[1]); parent.appendChild(block);
      } else {
        list = null;
        if (!paragraph) { paragraph = document.createElement('p'); parent.appendChild(paragraph); }
        else paragraph.appendChild(document.createTextNode(' '));
        inline(paragraph, line);
      }
    });
  }
  var root = document.getElementById('content');
  fetch('sdk-device-list.md', {cache: 'no-store'})
    .then(function (response) {
      if (!response.ok) throw new Error('HTTP ' + response.status);
      return response.text();
    })
    .then(function (text) { render(text, root); })
    .catch(function () {
      root.className = 'error';
      root.textContent = location.protocol === 'file:'
        ? '请通过工作台的 HTTP 地址打开此页，以便读取 Markdown 清单。'
        : '设备清单加载失败，请刷新重试，或点击上方“查看 Markdown 原文”。';
    });
}());
