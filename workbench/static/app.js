/* 灵石解决方案工作台 · 原生 JS 交互增强
   只做三件事：字段自动保存、核对页筛选、表格行增删。
   页面结构与状态一律由服务端 Jinja2 渲染，前端不持有业务规则。 */

(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    initAutosave();
    initFilters();
    initGroups();
    initTranscriptDraft();
  });

  /* ---------------- 字段自动保存 ---------------- */

  var dirtyFields = new Set();
  var saveTimers = new Map();
  var saveQueue = Promise.resolve();

  function scheduleSave(el, delay) {
    dirtyFields.add(el);
    clearTimeout(saveTimers.get(el));
    var saved = el.closest('.fld-row').querySelector('[data-role=saved]');
    if (saved) { saved.textContent = '待保存'; saved.classList.add('on'); }
    saveTimers.set(el, setTimeout(function () {
      saveTimers.delete(el);
      saveField(el);
    }, delay));
  }

  function initAutosave() {
    if (typeof SAVE_URL === 'undefined') return;

    document.querySelectorAll('.fi').forEach(function (el) {
      var ev = el.tagName === 'SELECT' ? 'change' : 'input';
      el.addEventListener(ev, function () { scheduleSave(el, 700); });
    });

    document.querySelectorAll('.chk-group').forEach(function (group) {
      group.addEventListener('change', function (e) {
        if (e.target.type !== 'checkbox') return;
        e.target.closest('.chk').classList.toggle('on', e.target.checked);
        scheduleSave(group, 0);
      });
    });

    document.querySelectorAll('.choice-field').forEach(function (group) {
      function refresh() {
        group.querySelectorAll('.chk').forEach(function (label) {
          label.classList.toggle('on', label.querySelector('input').checked);
        });
        var custom = group.querySelector('.choice-custom');
        if (custom) {
          var selected = group.querySelector('input[type=radio]:checked, select option:checked');
          custom.hidden = !(selected && selected.hasAttribute('data-custom-toggle'));
        }
        var other = group.querySelector('.choice-other');
        if (other) {
          var checked = group.querySelector('input[value="其他"]').checked;
          var note = other.querySelector('textarea');
          other.hidden = !checked && !note.value.trim();
          note.required = checked;
        }
      }
      group.addEventListener('change', function (e) {
        if (group.hasAttribute('data-require-other-detail') && e.target.value === '其他' && !e.target.checked) {
          group.querySelector('.choice-note').value = '';
        }
        if (e.target.matches('input,select')) { refresh(); scheduleSave(group, 0); }
      });
      group.addEventListener('input', function (e) {
        if (e.target.matches('textarea')) scheduleSave(group, 700);
      });
      group.querySelector('.choice-clear').addEventListener('click', function () {
        group.querySelectorAll('input').forEach(function (input) { input.checked = false; });
        group.querySelectorAll('textarea,select').forEach(function (el) { el.value = ''; });
        refresh();
        scheduleSave(group, 0);
      });
      refresh();
    });

    document.querySelectorAll('.tbl[data-type=table]').forEach(function (tbl) {
      tbl.addEventListener('input', function () { scheduleSave(tbl, 700); });
    });

    // 切换方式前立即保存，包括尚未到达自动保存延时的最后一次输入。
    document.addEventListener('click', async function (e) {
      var link = e.target.closest('a[href]');
      if (!link || e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey ||
          e.shiftKey || e.altKey || link.target === '_blank' || link.hasAttribute('download') ||
          link.getAttribute('href').charAt(0) === '#' || link.protocol === 'javascript:' ||
          !dirtyFields.size) return;
      e.preventDefault();
      saveTimers.forEach(function (timer) { clearTimeout(timer); });
      saveTimers.clear();
      Array.from(dirtyFields).forEach(function (el) { saveField(el); });
      await saveQueue;
      if (dirtyFields.size) {
        alert('仍有内容未保存，请检查网络后再次点击切换。当前填写内容已保留在页面中。');
        return;
      }
      window.location.assign(link.href);
    });
    window.addEventListener('beforeunload', function (e) {
      if (dirtyFields.size) { e.preventDefault(); e.returnValue = ''; }
    });
  }

  function readValue(el) {
    var type = el.getAttribute('data-type');
    if (type === 'choice-single') {
      var selected = el.querySelector('input[type=radio]:checked, select option:checked');
      if (!selected) return '';
      return selected.hasAttribute('data-custom-toggle') ? el.querySelector('.choice-note').value.trim() : selected.value;
    }
    if (type === 'choice-list') {
      var choices = Array.from(el.querySelectorAll('input[type=checkbox]:checked')).map(function (input) { return input.value; });
      var note = el.querySelector('.choice-note');
      if (note && note.value.trim()) choices.push(note.value.trim());
      return Array.from(new Set(choices));
    }
    if (type === 'list') {
      return Array.prototype.slice
        .call(el.querySelectorAll('input[type=checkbox]:checked'))
        .map(function (c) { return c.value; });
    }
    if (type === 'table') {
      return Array.prototype.slice.call(el.querySelectorAll('tbody tr')).map(function (tr) {
        var obj = {};
        tr.querySelectorAll('input[data-col]').forEach(function (inp) {
          obj[inp.getAttribute('data-col')] = (inp.value || '').trim();
        });
        return obj;
      }).filter(function (r) {
        return Object.keys(r).some(function (k) { return r[k]; });
      });
    }
    if (type === 'number') {
      var v = (el.value || '').trim();
      if (v === '') return null;
      var n = parseFloat(v);
      return isNaN(n) ? v : n;
    }
    return (el.value || '').trim();
  }

  function saveField(el) {
    // 按提交顺序写入，避免慢请求覆盖同一字段的较新输入。
    var value = readValue(el);
    saveQueue = saveQueue.then(function () { return persistField(el, value); });
    return saveQueue;
  }

  function persistField(el, value) {
    var key = el.getAttribute('data-key');
    var row = el.closest('.fld-row');
    if (el.hasAttribute('data-require-other-detail') && value.includes('其他')) {
      var allowed = Array.from(el.querySelectorAll('input[type=checkbox]')).map(function (input) { return input.value; });
      if (!value.some(function (v) { return v.trim() && !allowed.includes(v.trim()); })) {
        dirtyFields.add(el);
        var hint = row && row.querySelector('[data-role=saved]');
        if (hint) { hint.textContent = '请填写具体品牌名称后保存'; hint.classList.add('on'); }
        return Promise.resolve();
      }
    }

    return fetch(SAVE_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: key, value: value })
    })
      .then(function (r) { if (!r.ok) throw new Error('保存失败'); return r.json(); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.error || '保存失败');
        if (JSON.stringify(readValue(el)) === JSON.stringify(value)) dirtyFields.delete(el);
        if (row) {
          var tag = row.querySelector('[data-role=status]');
          if (tag) {
            tag.textContent = res.status_label;
            tag.className = 'tag ' + tagClass(res.status);
          }
          row.setAttribute('data-status', res.status);
          row.setAttribute('data-filled', value === null || value === '' ||
            (Array.isArray(value) && !value.length) ? '0' : '1');
          row.classList.toggle('miss', res.status === 'required_missing');
          row.classList.toggle('confirm', res.status === 'pending_confirm');
          var saved = row.querySelector('[data-role=saved]');
          if (saved) {
            saved.textContent = dirtyFields.has(el) ? '待保存' : '已保存';
            saved.classList.add('on');
            setTimeout(function () { if (!dirtyFields.has(el)) saved.classList.remove('on'); }, 1600);
          }
        }
        updateStats(res);
        applyFilter();
      })
      .catch(function () {
        dirtyFields.add(el);
        var saved = row && row.querySelector('[data-role=saved]');
        if (saved) { saved.textContent = '保存失败，请重试'; saved.classList.add('on'); }
      });
  }

  function tagClass(st) {
    return { extracted: 'blue', confirmed: 'green', pending_confirm: 'orange',
             required_missing: 'red', optional_missing: '' }[st] || '';
  }

  function updateStats(res) {
    set('sDone', res.required_done + '/' + res.required_total);
    set('sConfirm', res.pending_confirm);
    set('sMissing', res.required_missing);
    set('warnMissing', res.required_missing);

    var btn = document.getElementById('nextBtn');
    var hint = document.getElementById('nextHint');
    if (btn) {
      if (!btn.getAttribute('data-href')) btn.setAttribute('data-href', btn.href);
      var blocked = !res.validation_ok;
      btn.classList.toggle('gray', blocked);
      btn.setAttribute('aria-disabled', blocked ? 'true' : 'false');
      if (blocked) {
        btn.setAttribute('href', 'javascript:void(0)');
        btn.onclick = function () {
          alert('还有 ' + res.required_missing + ' 项必填内容未补齐，补齐后才能进入生成步骤。');
        };
      } else {
        btn.setAttribute('href', btn.getAttribute('data-href') || btn.href);
        btn.onclick = null;
      }
    }
    if (hint) {
      hint.textContent = res.validation_ok
        ? '校验通过，可以进入下一步。'
        : '还需补齐 ' + res.required_missing + ' 项必填内容。';
    }

    var missRow = document.querySelector('.fld-row.miss');
    if (missRow) missRow.id = 'firstMissing';
  }

  function set(id, v) {
    var el = document.getElementById(id);
    if (el) el.textContent = v;
  }

  /* ---------------- 筛选 ---------------- */

  var currentFilter = 'all';

  function initFilters() {
    document.querySelectorAll('.filt').forEach(function (b) {
      b.addEventListener('click', function () {
        document.querySelectorAll('.filt').forEach(function (x) { x.classList.remove('on'); });
        b.classList.add('on');
        currentFilter = b.getAttribute('data-f');
        applyFilter();
      });
    });
  }

  function applyFilter() {
    var f = currentFilter;
    document.querySelectorAll('.fld-row').forEach(function (row) {
      var st = row.getAttribute('data-status');
      var filled = row.getAttribute('data-filled') === '1';
      var show = true;
      if (f === 'pending_confirm') show = (st === 'pending_confirm');
      else if (f === 'required_missing') show = (st === 'required_missing');
      else if (f === 'filled') show = filled;
      row.style.display = show ? '' : 'none';
    });
    document.querySelectorAll('.grp').forEach(function (g) {
      var any = Array.prototype.slice.call(g.querySelectorAll('.fld-row'))
        .some(function (r) { return r.style.display !== 'none'; });
      g.style.display = any ? '' : 'none';
    });
  }

  /* ---------------- 分组折叠 ---------------- */

  function initGroups() {
    var firstMissing = document.querySelector('.fld-row.miss');
    if (firstMissing) firstMissing.id = 'firstMissing';
    document.querySelectorAll('.grp-head').forEach(function (h) {
      h.addEventListener('click', function () { toggleGroup(h); });
    });
  }

  window.toggleGroup = function (head) {
    var body = head.nextElementSibling;
    if (!body) return;
    var hidden = body.style.display === 'none';
    body.style.display = hidden ? '' : 'none';
    var cnt = head.querySelector('.cnt');
    if (cnt) cnt.innerHTML = cnt.innerHTML.replace(hidden ? '▸' : '▾', hidden ? '▾' : '▸');
  };

  /* ---------------- 表格行 ---------------- */

  window.addRow = function (btn, cols) {
    var tbl = btn.previousElementSibling;
    if (tbl && tbl.classList.contains('table-scroll')) tbl = tbl.querySelector('table');
    while (tbl && tbl.tagName !== 'TABLE') tbl = tbl.previousElementSibling;
    if (!tbl) return;
    var tr = document.createElement('tr');
    cols.split(',').forEach(function (c) {
      var td = document.createElement('td');
      var inp = document.createElement('input');
      inp.setAttribute('data-col', c);
      td.appendChild(inp);
      tr.appendChild(td);
    });
    var td = document.createElement('td');
    var b = document.createElement('button');
    b.className = 'btn danger sm';
    b.type = 'button';
    b.textContent = '删除';
    b.onclick = function () { delRow(b); };
    td.appendChild(b);
    tr.appendChild(td);
    tbl.querySelector('tbody').appendChild(tr);
    tr.querySelector('input').focus();
  };

  window.delRow = function (btn) {
    var tr = btn.closest('tr');
    if (tr) {
      var tbl = tr.closest('table');
      tr.remove();
      scheduleSave(tbl, 0);
    }
  };

  function initTranscriptDraft() {
    var form = document.getElementById('transcriptForm');
    if (!form) return;
    var ta = document.getElementById('transcript');
    var hint = document.getElementById('draftHint');
    var key = form.getAttribute('data-draft-key');
    var sourceId = form.getAttribute('data-source-id');
    var original = ta.value;
    var draftAvailable = true;
    try {
      var draft = JSON.parse(sessionStorage.getItem(key) || 'null');
      if (draft && draft.sourceId === sourceId) {
        ta.value = draft.text;
        hint.textContent = '已恢复尚未提交的文字草稿；点击「保存文字稿」后可用于梳理。';
        ta.dispatchEvent(new Event('input'));
      } else { sessionStorage.removeItem(key); }
    } catch (e) { draftAvailable = false; }
    function storeDraft() {
      try {
        sessionStorage.setItem(key, JSON.stringify({sourceId: sourceId, text: ta.value}));
        draftAvailable = true;
        hint.textContent = '草稿已暂存，切换到手动填写后再回来可继续编辑。用于梳理前请保存文字稿。';
      } catch (e) {
        draftAvailable = false;
        hint.textContent = '浏览器无法暂存草稿，请先保存文字稿再切换。';
      }
    }
    ta.addEventListener('input', storeDraft);
    window.addEventListener('beforeunload', function (e) {
      if (!draftAvailable && ta.value !== original) { e.preventDefault(); e.returnValue = ''; }
    });
    document.getElementById('transcriptFile').addEventListener('change', async function (e) {
      var file = e.target.files[0];
      if (!file) return;
      try {
        var text = await file.text();
        if (ta.value.trim() && !confirm('用所选文件替换当前文字稿？已人工确认的字段不受影响。')) return;
        ta.value = text;
        ta.dispatchEvent(new Event('input'));
      } catch (error) { hint.textContent = '文件读取失败，请重试或直接粘贴文字稿。'; }
      finally { e.target.value = ''; }
    });
  }
})();
