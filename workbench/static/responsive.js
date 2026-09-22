/* Navigation is expanded without JavaScript; enhance it into a mobile disclosure. */
(function () {
  'use strict';
  var toggle = document.querySelector('.nav-toggle');
  if (!toggle) return;
  var header = toggle.closest('.topbar');
  var narrow = window.matchMedia('(max-width: 860px)');

  function setOpen(open) {
    header.classList.toggle('nav-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    toggle.textContent = open ? '收起菜单' : '菜单';
  }

  header.classList.add('nav-ready');
  toggle.hidden = false;
  toggle.addEventListener('click', function () {
    setOpen(toggle.getAttribute('aria-expanded') !== 'true');
  });
  header.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && narrow.matches && header.classList.contains('nav-open')) {
      setOpen(false);
      toggle.focus();
    }
  });
  narrow.addEventListener('change', function () { setOpen(false); });
})();
