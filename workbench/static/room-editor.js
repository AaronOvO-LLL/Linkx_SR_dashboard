/* 按房间填写，按既有行结构保存；候选项与默认数量均来自服务端规则。 */
(function () {
  'use strict';

  function node(tag, className, text) {
    var el = document.createElement(tag);
    if (className) el.className = className;
    if (text) el.textContent = text;
    return el;
  }

  function field(label, input) {
    var el = node('label', 'room-field');
    input.setAttribute('aria-label', label);
    el.append(node('span', '', label), input);
    return el;
  }

  function categoryOf(editor, name) {
    return editor.roomRules.categories.find(function (c) { return c.name === name; });
  }

  function updateCount(editor) {
    var cards = editor.querySelectorAll('.room-card');
    editor.querySelector('[data-room-count]').textContent = '已添加 ' + cards.length + ' 间设备房';
    editor.querySelector('.room-empty').hidden = cards.length > 0;
    cards.forEach(function (card, i) {
      card.querySelector('.room-number').textContent = '设备房 ' + (i + 1);
    });
  }

  function renderItems(editor, card, rows, scheduleSave) {
    var rules = editor.roomRules;
    var area = card.querySelector('.room-items');
    var category = categoryOf(editor, card.querySelector('[data-room-type]').value);
    area.replaceChildren();
    if (!category && !rows.length) {
      area.append(node('p', 'hint', '选择设备房类型后，将显示对应的子设备数量选项。'));
      return;
    }
    var items = category ? category.items : [];
    var variable = node('div', 'room-quantity-grid');
    var fixed = node('div', 'room-quantity-grid');
    function addQuantity(item, row, target) {
      var input = node('input');
      input.type = 'number'; input.min = '0'; input.step = '1';
      input.dataset.device = item.name;
      // 保存原行存在性：旧数据中未录入的可选设备不会因修改房名而被补成待确认。
      input.dataset.included = row ? '1' : '0';
      input.value = row ? (row[rules.qty_column] == null ? '' : row[rules.qty_column]) :
        (item.kind === 'fixed' && item.default != null ? item.default : '');
      if (item.kind === 'fixed') input.dataset.included = '1';
      input.placeholder = '待核实';
      var label = field(item.name, input);
      var detail = [item.qty_label, item.unit ? '单位：' + item.unit : ''].filter(Boolean).join(' · ');
      if (detail) label.append(node('span', 'hint', detail));
      if (item.kind === 'legacy') {
        var legacy = node('div', 'room-legacy');
        var remove = node('button', 'btn danger sm', '移除历史设备');
        remove.type = 'button';
        remove.addEventListener('click', function () {
          if (!confirm('移除「' + item.name + '」？请在对应的子设备选项中重新填写数量。')) return;
          legacy.remove(); scheduleSave(editor, 0);
        });
        legacy.append(label, node('p', 'hint', '此设备不在当前类型的选项中，请核实后移除或重新填写。'), remove);
        target.append(legacy);
      } else target.append(label);
    }
    items.forEach(function (item) {
      var existing = rows.filter(function (r) { return r[rules.item_column] === item.name; });
      if (!existing.length) existing = [null];
      existing.forEach(function (row) { addQuantity(item, row, item.kind === 'fixed' ? fixed : variable); });
    });
    // 历史未知设备保留为可编辑数量，供核对修正，不能静默丢失。
    rows.filter(function (r) {
      return r[rules.item_column] && !items.some(function (it) { return it.name === r[rules.item_column]; });
    }).forEach(function (row) {
      addQuantity({name: row[rules.item_column], kind: 'legacy'}, row, variable);
    });
    if (variable.children.length) {
      area.append(node('h4', '', '子设备数量'));
      area.append(node('p', 'hint', '按现场情况填写，没有的设备填 0；数量待核实可留空。'));
      area.append(variable);
    } else {
      area.append(node('p', 'hint', '该类型无需填写额外子设备，确认下方固定配置即可。'));
    }
    if (fixed.children.length) {
      var details = node('details', 'room-fixed');
      details.append(node('summary', '', '固定配置 · 已按类型带出，可展开调整'));
      details.append(fixed);
      area.append(details);
    }
  }

  function appendRoom(editor, name, type, rows, scheduleSave) {
    var card = node('section', 'room-card');
    var header = node('div', 'room-card-head');
    var remove = node('button', 'btn danger sm', '删除设备房');
    remove.type = 'button';
    header.append(node('strong', 'room-number'), remove);
    var nameInput = node('input');
    nameInput.type = 'text'; nameInput.dataset.roomName = '';
    nameInput.placeholder = '例如：1#生活水泵房'; nameInput.value = name;
    var select = node('select'); select.dataset.roomType = '';
    var placeholder = node('option', '', '请选择设备房类型'); placeholder.value = ''; select.append(placeholder);
    editor.roomRules.categories.forEach(function (category) {
      var option = node('option', '', category.name); option.value = category.name; select.append(option);
    });
    if (type && !categoryOf(editor, type)) {
      var legacy = node('option', '', type + '（历史类型，请核实）'); legacy.value = type; select.append(legacy);
    }
    select.value = type;
    var identity = node('div', 'room-identity');
    identity.append(field('设备房名称', nameInput), field('设备房类型', select));
    card.append(header, identity, node('div', 'room-items'));
    editor.querySelector('.room-list').append(card);
    renderItems(editor, card, rows, scheduleSave);
    // 类型切换保留本页草稿，切回时恢复原数量，避免误操作丢失已填信息。
    var cache = new Map();
    var currentType = type;
    select.addEventListener('change', function () {
      cache.set(currentType, readCard(editor, card, currentType));
      currentType = select.value;
      renderItems(editor, card, cache.get(currentType) || [], scheduleSave);
      card.dataset.newRoom = cache.has(currentType) ? '' : '1';
      scheduleSave(editor, 0);
    });
    remove.addEventListener('click', function () {
      if ((nameInput.value.trim() || select.value) && !confirm('删除这间设备房及其全部子设备？')) return;
      card.remove(); updateCount(editor); scheduleSave(editor, 0);
    });
    updateCount(editor);
    return card;
  }

  function readCard(editor, card, type) {
    var rules = editor.roomRules;
    var name = card.querySelector('[data-room-name]').value.trim();
    if (type === undefined) type = card.querySelector('[data-room-type]').value;
    var inputs = Array.from(card.querySelectorAll('[data-device]'));
    var rows = inputs.filter(function (input) {
      return card.dataset.newRoom === '1' || input.dataset.included === '1' || input.value !== '';
    }).map(function (input) {
      return {[rules.group_column]: name, [rules.category_column]: type,
        [rules.item_column]: input.dataset.device, [rules.qty_column]: input.value};
    });
    if (!rows.length) rows.push({[rules.group_column]: name, [rules.category_column]: type,
      [rules.item_column]: '', [rules.qty_column]: ''});
    return rows;
  }

  function validate(editor) {
    var names = new Set();
    var error = '';
    editor.querySelectorAll('.room-card').forEach(function (card) {
      var name = card.querySelector('[data-room-name]');
      var type = card.querySelector('[data-room-type]');
      var value = name.value.trim();
      var message = !value ? '请填写设备房名称。' : names.has(value) ? '设备房名称不能重复，请分别命名。' : '';
      name.setCustomValidity(message); names.add(value);
      type.setCustomValidity(!type.value ? '请选择设备房类型。' : '');
      var invalid = Array.from(card.querySelectorAll('input,select')).find(function (input) { return !input.validity.valid; });
      card.classList.toggle('room-invalid', !!invalid);
      if (invalid && invalid.closest('details')) invalid.closest('details').open = true;
      if (invalid && !error) error = invalid === name ? message : invalid === type ? '请选择设备房类型。' : '子设备数量请填写 0 或正整数。';
    });
    var hint = editor.querySelector('.room-error');
    hint.textContent = error; hint.hidden = !error;
    return !error;
  }

  window.RoomEditor = {
    init: function (scheduleSave) {
      document.querySelectorAll('.room-editor').forEach(function (editor) {
        editor.roomRules = JSON.parse(editor.dataset.rules);
        var rules = editor.roomRules;
        var rooms = new Map();
        (JSON.parse(editor.dataset.rows) || []).forEach(function (row) {
          var name = row[rules.group_column] || '', type = row[rules.category_column] || '';
          var key = JSON.stringify([name, type]);
          if (!rooms.has(key)) rooms.set(key, {name: name, type: type, rows: []});
          rooms.get(key).rows.push(row);
        });
        rooms.forEach(function (room) { appendRoom(editor, room.name, room.type, room.rows, scheduleSave); });
        updateCount(editor);
        editor.querySelector('[data-add-room]').addEventListener('click', function () {
          var card = appendRoom(editor, '', '', [], scheduleSave);
          card.dataset.newRoom = '1';
          card.querySelector('[data-room-name]').focus();
          scheduleSave(editor, 700);
        });
        editor.addEventListener('input', function (event) {
          if (event.target.matches('[data-device]')) event.target.dataset.included = '1';
          scheduleSave(editor, 700);
        });
      });
    },
    read: function (editor) {
      return Array.from(editor.querySelectorAll('.room-card')).flatMap(function (card) { return readCard(editor, card); });
    },
    validate: validate
  };
})();
