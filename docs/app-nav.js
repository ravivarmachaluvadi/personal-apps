/* app-nav.js — the strip across the top of every Personal Apps page: Home, the ten apps, then Games.
 *
 * tools/build-site.py puts <script src="app-nav.js"></script> right after each app page's <body>, and Home has
 * it by hand, so the strip is in place before the page's own content is parsed and nothing jumps on load.
 *
 * Where a link opens:
 *   - in an app, every strip link opens in the same tab (Ctrl+click or middle-click still gives a new one);
 *   - on Home in a browser tab, strip links open new tabs, like Home's cards, so Home stays open;
 *   - in the installed app (display-mode standalone), every same-site link on Home opens in the same window
 *     as well, so the phone's Back works and you never drop out into Chrome. A Back arrow is shown, only in the
 *     installed app (a browser tab has its own) and only when there is a page to go back to.
 * Before leaving through the strip, or through a Home link in the installed app, any change still waiting is
 * saved to Drive first (DriveStore.flushAll, at most 2.5 s), instead of being left behind on this device.
 *
 * The page list below must match Home's app cards (file, order, icon); the build refuses to ship them apart.
 * window.APPNAV_TEST_INSTALLED === true (set by tools/check-nav.py) counts as installed: Chrome cannot fake
 * display-mode for a test. Everything is built with createElement/textContent, and every class name starts with
 * appnav- so a page's own rules (Dumbbell Dojo's .more, for one) never reach the strip.
 */
(function () {
  'use strict';
  if (window.__appnav) return;
  window.__appnav = true;

  var PAGES = [
    { file: 'keycap-atlas.html', name: 'Keycap Atlas', short: 'Keycap', icon: '⌨️' },
    { file: 'strongroom.html', name: 'Strongroom', short: 'Strongroom', icon: '🔐' },
    { file: 'dumbbell-dojo.html', name: 'Dumbbell Dojo', short: 'Dojo', icon: '🏋️' },
    { file: 'tally-board.html', name: 'Tally Board', short: 'Tally', icon: '📌' },
    { file: 'spine-bell.html', name: 'Spine Bell', short: 'Spine Bell', icon: '🔔' },
    { file: 'event-log.html', name: 'Event Log', short: 'Event Log', icon: '🗓️' },
    { file: 'hack-shelf.html', name: 'Hack Shelf', short: 'Hack Shelf', icon: '🧰' },
    { file: 'asana-rounds.html', name: 'Asana Rounds', short: 'Asana', icon: '🧘' },
    { file: 'shoebox.html', name: 'Shoebox', short: 'Shoebox', icon: '📦' },
    { file: 'drop-tray.html', name: 'Drop Tray', short: 'Drop Tray', icon: '📋' }
  ];
  var NS = 'http://www.w3.org/2000/svg';
  var ICON = { home: 'M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z', back: 'M15 5l-7 7 7 7' };

  var path = location.pathname, FILE = path.slice(path.lastIndexOf('/') + 1) || 'index.html';
  var ON_HOME = FILE === 'index.html';
  var INSTALLED = (function () {
    if (window.APPNAV_TEST_INSTALLED === true || navigator.standalone === true) return true;
    try { return ['standalone', 'window-controls-overlay', 'fullscreen'].some(function (m) { return window.matchMedia('(display-mode: ' + m + ')').matches; }); }
    catch (e) { return false; }
  })();

  var CSS = '.appnav{display:block;position:sticky;top:0;z-index:15;margin:0;padding:0;background:var(--surface,#fff);color:var(--text,#1c2129);' +
      'border-bottom:1px solid var(--line,#d4d9e1);font:600 13.5px/1.2 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;letter-spacing:0;' +
      '-webkit-font-smoothing:antialiased;-webkit-tap-highlight-color:transparent;text-align:left}' +
    '.appnav-in{display:flex;align-items:center;gap:6px;max-width:1280px;margin:0 auto;padding:4px 8px;box-sizing:border-box}' +
    '.appnav-fixed{display:flex;align-items:center;gap:2px;flex:none;padding-right:6px;border-right:1px solid var(--line,#d4d9e1)}' +
    '.appnav-list{display:flex;align-items:center;gap:2px;flex:1 1 auto;min-width:0;overflow-x:auto;overflow-y:hidden;overscroll-behavior-x:contain;scrollbar-width:none;position:relative}' +
    '.appnav-list::-webkit-scrollbar{display:none}' +
    '.appnav-list.appnav-more{-webkit-mask-image:linear-gradient(to right,#000 calc(100% - 28px),transparent);mask-image:linear-gradient(to right,#000 calc(100% - 28px),transparent)}' +
    '.appnav a.appnav-item,.appnav button.appnav-back{display:inline-flex;align-items:center;justify-content:center;gap:6px;flex:none;box-sizing:border-box;' +
      'min-width:38px;height:38px;margin:0;padding:0 9px;border:0;border-radius:9px;background:transparent;color:inherit;font:inherit;' +
      'text-decoration:none;white-space:nowrap;cursor:pointer;box-shadow:none}' +
    '.appnav a.appnav-item:hover,.appnav button.appnav-back:hover{background:var(--surface-2,#f2f4f7);text-decoration:none}' +
    '.appnav a.appnav-item[aria-current="page"]{background:var(--surface-2,#f2f4f7);box-shadow:inset 0 -3px 0 var(--accent,#e0562b)}' +
    '.appnav .appnav-ic{font-size:18px;line-height:1;font-weight:400}' +
    '.appnav svg{width:19px;height:19px;flex:none;display:block}' +
    '.appnav .appnav-t{display:inline}' +
    '.appnav.appnav-compact .appnav-t{display:none}' +
    '.appnav.appnav-compact a.appnav-item[aria-current="page"] .appnav-t{display:inline}' +
    '.appnav a.appnav-item:focus-visible,.appnav button.appnav-back:focus-visible{outline:3px solid var(--accent,#e0562b);outline-offset:-3px}' +
    '.appnav [hidden]{display:none!important}' +
    '@media print{.appnav{display:none!important}}';

  function svg(d) {
    var s = document.createElementNS(NS, 'svg'), p = document.createElementNS(NS, 'path');
    [['viewBox', '0 0 24 24'], ['fill', 'none'], ['stroke', 'currentColor'], ['stroke-width', '2'], ['stroke-linecap', 'round'], ['stroke-linejoin', 'round'], ['aria-hidden', 'true']]
      .forEach(function (a) { s.setAttribute(a[0], a[1]); });
    p.setAttribute('d', d); s.appendChild(p); return s;
  }
  function text(cls, t) { var s = document.createElement('span'); s.className = cls; s.textContent = t; return s; }
  function item(href, name, short, icon, current) {
    var a = document.createElement('a');
    a.className = 'appnav-item'; a.href = href; a.setAttribute('aria-label', name); a.title = name;
    if (current) a.setAttribute('aria-current', 'page');
    if (typeof icon === 'string') { var i = text('appnav-ic', icon); i.setAttribute('aria-hidden', 'true'); a.appendChild(i); } else a.appendChild(icon);
    a.appendChild(text('appnav-t', short));
    return a;
  }

  /* ---- leaving: save what is waiting, then go */
  var leaving = false;   // only while the save is under way: a second tap then would start a second save
  function leave(go) {
    if (leaving) return;
    leaving = true;
    /* cleared before going, not after: Back can restore this page from the back/forward cache exactly as it was
       left, and a flag still set then would make every tap on the strip do nothing */
    function now() { leaving = false; go(); }
    var p = null;
    try { p = window.DriveStore && window.DriveStore.flushAll ? window.DriveStore.flushAll(2500) : null; } catch (e) { p = null; }
    if (!p || typeof p.then !== 'function') return now();
    p.then(now, now);
  }
  function plain(ev) { return !(ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey); }
  function follow(a, ev) {
    if (!plain(ev)) return;                                    // a new tab or window: this page stays open
    if (a.getAttribute('aria-current') === 'page') {           // already here: back to the top instead
      ev.preventDefault();
      var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      window.scrollTo({ top: 0, behavior: still ? 'auto' : 'smooth' });
      return;
    }
    if (a.target === '_blank' || (a.getAttribute('href') || '').charAt(0) === '#') return;
    ev.preventDefault();
    var href = a.href;
    leave(function () { location.assign(href); });
  }

  function build() {
    var css = document.createElement('style');
    css.textContent = CSS;
    (document.head || document.documentElement).appendChild(css);

    var nav = document.createElement('nav'), inner = document.createElement('div'), fixed = document.createElement('div'), list = document.createElement('div');
    nav.className = 'appnav'; nav.setAttribute('aria-label', 'Personal Apps');
    inner.className = 'appnav-in'; fixed.className = 'appnav-fixed'; list.className = 'appnav-list';

    var back = document.createElement('button');
    back.type = 'button'; back.className = 'appnav-back'; back.title = 'Back'; back.setAttribute('aria-label', 'Back'); back.hidden = true;
    back.appendChild(svg(ICON.back));
    back.addEventListener('click', function () { leave(function () { history.back(); }); });
    fixed.appendChild(back);
    fixed.appendChild(item('./', 'Home', 'Home', svg(ICON.home), ON_HOME));
    PAGES.forEach(function (pg) { list.appendChild(item(pg.file, pg.name, pg.short, pg.icon, pg.file === FILE)); });
    list.appendChild(item(ON_HOME ? '#games' : './#games', 'Games', 'Games', '🎮', false));

    inner.appendChild(fixed); inner.appendChild(list); nav.appendChild(inner);
    [].slice.call(nav.getElementsByTagName('a')).forEach(function (a) {
      if (ON_HOME && !INSTALLED && a.getAttribute('href').charAt(0) !== '#') { a.target = '_blank'; a.rel = 'noopener'; }
      a.addEventListener('click', function (ev) { follow(a, ev); });
    });
    document.body.insertBefore(nav, document.body.firstChild);

    /* names when they all fit, icons otherwise (the current page keeps its name); the strip scrolls, the page never */
    function more() { list.classList.toggle('appnav-more', list.scrollLeft + list.clientWidth < list.scrollWidth - 2); }
    function fit() {
      /* edge to edge and flush with the top, whatever padding the page gives its <body> */
      var bs = window.getComputedStyle(document.body);
      nav.style.marginLeft = '-' + bs.paddingLeft; nav.style.marginRight = '-' + bs.paddingRight; nav.style.marginTop = '-' + bs.paddingTop;
      nav.classList.remove('appnav-compact');
      if (list.scrollWidth > list.clientWidth + 1) nav.classList.add('appnav-compact');
      var cur = list.querySelector('[aria-current="page"]');
      if (cur && list.scrollWidth > list.clientWidth + 1) {
        var x = cur.getBoundingClientRect().left - list.getBoundingClientRect().left + list.scrollLeft;
        list.scrollLeft = Math.max(0, x - (list.clientWidth - cur.offsetWidth) / 2);
      }
      more();
      document.documentElement.style.setProperty('--appnav-h', nav.offsetHeight + 'px');
    }
    function showBack() {
      var can = false;
      try { can = !!(window.navigation && window.navigation.canGoBack); } catch (e) { can = false; }
      var hide = !(INSTALLED && can);
      if (back.hidden !== hide) { back.hidden = hide; fit(); }
    }
    list.addEventListener('scroll', more, { passive: true });
    window.addEventListener('resize', fit);
    window.addEventListener('load', fit);
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fit);   /* older pages set their body padding further down */
    if (window.navigation && window.navigation.addEventListener) window.navigation.addEventListener('currententrychange', showBack);
    window.addEventListener('pageshow', showBack);
    fit();
    showBack();

    /* installed, Home's own same-site links stay in the app window too, and save first like the strip */
    if (ON_HOME && INSTALLED) {
      var keep = function () {
        [].slice.call(document.querySelectorAll('a[href]')).forEach(function (a) {
          if (nav.contains(a) || a.origin !== location.origin) return;
          a.removeAttribute('target');
          a.addEventListener('click', function (ev) { follow(a, ev); });
        });
      };
      if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', keep); else keep();
    }
  }

  if (document.body) build();
  else document.addEventListener('DOMContentLoaded', build);
})();
