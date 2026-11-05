/* 可重复录入的独立空间：数据契约仍为 table 对象数组。 */
(function () {
  'use strict';
  function node(tag, text, cls) {
    var el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  }
  function readCell(el) {
    if (el.dataset.cellType === 'multiselect') {
      var values = Array.from(el.querySelectorAll('input:checked')).map(function (input) { return input.value; });
      var detail = el.querySelector('textarea');
      if (detail && detail.value.trim()) values.push(detail.value.trim());
      return Array.from(new Set(values));
    }
    if (el.dataset.cellType === 'table') return readList(el.querySelector('.card-list'));
    var input = el.querySelector('input, select, textarea');
    return input ? input.value.trim() : '';
  }
  function readList(list) {
    return Array.from(list.children).map(function (card) {
      var row = {};
      card.querySelectorAll(':scope > .survey-room-grid > [data-cell-key]').forEach(function (cell) { row[cell.dataset.cellKey] = readCell(cell); });
      return row;
    });
  }
  function read(editor) { return readList(editor.querySelector('.card-list')); }
  function init(save) {
    document.querySelectorAll('.card-editor').forEach(function (editor) {
      var cols = JSON.parse(editor.dataset.columns);
      var list = editor.querySelector('.card-list');
      var item = editor.dataset.itemName;
      function refresh() {
        var cards = Array.from(list.children);
        editor.querySelector('[data-card-count]').textContent = '已填写 ' + cards.length + ' 个' + item;
        cards.forEach(function (card, i) {
          var data = {};
          card.querySelectorAll(':scope > .survey-room-grid > [data-cell-key]').forEach(function (cell) { data[cell.dataset.cellKey] = readCell(cell); });
          card.querySelector('.survey-room-title').textContent = item + ' ' + (i + 1) + (data.room_name ? ' · ' + data.room_name : '（请填写名称）');
          var sdk = card.querySelector('[data-cell-key="sdk_device_compatible"]');
          if (sdk) sdk.hidden = data.storage_protocol !== '否';
        });
      }
      function add(row, target = list, columns = cols, label = item) {
        var card = node('article', undefined, 'survey-room');
        var head = node('div', undefined, 'room-toolbar');
        head.appendChild(node('h3', label, 'survey-room-title'));
        var remove = node('button', '删除' + label, 'btn danger sm'); remove.type = 'button';
        remove.addEventListener('click', function () { card.remove(); refresh(); save(editor, 0); });
        head.appendChild(remove); card.appendChild(head);
        var grid = node('div', undefined, 'survey-room-grid');
        columns.forEach(function (col) {
          var cell = node('div', undefined, 'survey-room-cell');
          cell.dataset.cellKey = col.key; cell.dataset.cellType = col.type;
          cell.appendChild(node('label', col.label + (col.required ? ' *' : '')));
          var value = row[col.key];
          if (col.type === 'multiselect') {
            var values = Array.isArray(value) ? value : (value ? [value] : []);
            var options = (col.options || []).map(function (o) { return o.value; });
            var categories = new Map();
            (col.options || []).forEach(function (option) {
              var category = option.category || '';
              if (!categories.has(category)) {
                if (category) cell.appendChild(node('strong', category));
                var choices = node('div', undefined, 'survey-room-choices');
                categories.set(category, choices);
                cell.appendChild(choices);
              }
              var optionLabel = node('label');
              var input = node('input');
              input.type = 'checkbox'; input.value = option.value; input.checked = values.includes(option.value);
              optionLabel.appendChild(input);
              optionLabel.appendChild(document.createTextNode(option.value));
              categories.get(category).appendChild(optionLabel);
            });
            if (col.allow_custom) {
              var note = node('textarea'); note.rows = 2; note.placeholder = '其他具体品牌 / 补充说明';
              note.setAttribute('aria-label', col.label + '补充说明');
              note.value = values.filter(function (v) { return !options.includes(v); }).join('；'); cell.appendChild(note);
            }
          } else if (col.type === 'select') {
            var select = node('select'); var blank = node('option', '请选择'); blank.value = ''; select.appendChild(blank);
            var options = (col.options || []).map(function (o) { return o.value; });
            if (value && !options.includes(value)) options.push(value);
            options.forEach(function (v) { var op = node('option', v); op.value = v; select.appendChild(op); });
            select.value = value || ''; select.setAttribute('aria-label', col.label); cell.appendChild(select);
          } else if (col.type === 'table') {
            cell.classList.add('survey-room-nested');
            var nested = node('div', undefined, 'card-list');
            var addRow = node('button', '+ 新增设备', 'btn ghost sm');
            addRow.type = 'button';
            addRow.addEventListener('click', function () { add({}, nested, col.columns, '设备'); save(editor, 0); });
            cell.append(nested, addRow);
            (Array.isArray(value) ? value : []).forEach(function (entry) { add(entry, nested, col.columns, '设备'); });
          } else {
            var input = node(col.type === 'textarea' ? 'textarea' : 'input');
            if (col.type === 'number') {
              input.type = 'number';
              input.min = col.min === undefined ? '0' : col.min;
              input.step = col.integer ? '1' : 'any';
            }
            input.value = value === undefined || value === null ? '' : value;
            input.setAttribute('aria-label', col.label); cell.appendChild(input);
          }
          if (col.help) cell.appendChild(node('small', col.help, 'hint'));
          if (col.unit) cell.appendChild(node('small', '单位：' + col.unit, 'hint'));
          grid.appendChild(cell);
        });
        card.appendChild(grid);
        if (columns.some(function (column) { return column.key === 'sdk_device_compatible'; })) {
          var sdkLink = node('a', '查看 SDK 可对接设备清单 ↗', 'btn ghost sm');
          sdkLink.href = '/static/sdk-device-list.html'; sdkLink.target = '_blank'; sdkLink.rel = 'noopener';
          card.appendChild(sdkLink);
        }
        target.appendChild(card);
        refresh();
      }
      (JSON.parse(editor.dataset.rows) || []).forEach(function (row) { add(row); });
      editor.querySelector('[data-add-card]').addEventListener('click', function () { add({}); save(editor, 0); });
      editor.addEventListener('input', function () { refresh(); save(editor, 700); });
      editor.addEventListener('change', function () { refresh(); save(editor, 0); });
      refresh();
    });
  }
  window.CardEditor = { init: init, read: read };
})();
