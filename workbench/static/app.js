/* 灵石解决方案工作台 · 原生 JS 交互增强
   只做三件事：字段自动保存、核对页筛选、表格行增删。
   页面结构与状态一律由服务端 Jinja2 渲染，前端不持有业务规则。 */

(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    initAutosave();
    initFilters();
    initGroups();
  });

  /* ---------------- 字段自动保存 ---------------- */

  function initAutosave() {
    if (typeof SAVE_URL === 'undefined') return;

    document.querySelectorAll('.fi').forEach(function (el) {
      var ev = (el.tagName === 'SELECT' || el.type === 'date' || el.type === 'number') ? 'change' : 'input';
      el.addEventListener(ev, debounce(function () { saveField(el); }, 700));
    });

    document.querySelectorAll('.chk-group').forEach(function (group) {
      group.addEventListener('change', function (e) {
        if (e.target.type !== 'checkbox') return;
        e.target.closest('.chk').classList.toggle('on', e.target.checked);
        saveField(group);
      });
    });

    document.querySelectorAll('.tbl[data-type=table]').forEach(function (tbl) {
      tbl.addEventListener('input', debounce(function () { saveField(tbl); }, 700));
      tbl.addEventListener('click', function (e) {
        if (e.target.classList.contains('btn') && e.target.textContent.indexOf('删除') >= 0) {
          setTimeout(function () { saveField(tbl); }, 30);
        }
      });
    });
  }

  function readValue(el) {
    var type = el.getAttribute('data-type');
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
    var key = el.getAttribute('data-key');
    var value = readValue(el);
    var row = el.closest('.fld-row');

    fetch(SAVE_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: key, value: value })
    })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (!res.ok) return;
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
            saved.classList.add('on');
            setTimeout(function () { saved.classList.remove('on'); }, 1600);
          }
        }
        updateStats(res);
        applyFilter();
      })
      .catch(function () { /* 保存失败静默重试由用户再次触发 */ });
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
    if (tr) tr.remove();
  };

  function debounce(fn, ms) {
    var t;
    return function () {
      clearTimeout(t);
      var args = arguments, self = this;
      t = setTimeout(function () { fn.apply(self, args); }, ms);
    };
  }
})();
