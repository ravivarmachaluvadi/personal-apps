"""Strip checker: the row of Home, the eleven apps and Games across the top of every page (docs/app-nav.js).

  python tools/check-nav.py            # docs/ as built
  python tools/check-nav.py --headed   # watch it
  NAV_SHOTS=<folder> python tools/check-nav.py   # also save a screenshot of every page at 390 and 1280 px

Headless Chrome on a local server. Every page is opened signed out and every request that leaves the local
server is aborted, except in "save before leaving", which signs in to the fake Google Drive that
check-drop-tray.py uses, to prove a change made a moment before switching apps reaches Drive.

Chrome cannot pretend to be the installed app (CDP does not emulate display-mode), so the installed-app checks
set window.APPNAV_TEST_INSTALLED before the page loads; app-nav.js treats that exactly like display-mode:
standalone. Nothing is stored, so it can never stick in a real browser.

Exit code 0 means every check passed; each failure is printed with the section it came from.
"""
import functools, http.server, importlib.util, json, os, re, socket, sys, threading, time, urllib.parse, urllib.request
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # the Windows console is cp1252; page text has emoji
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, 'docs')
HEADED = '--headed' in sys.argv
SHOTS = os.environ.get('NAV_SHOTS', '')
FAILS, PASSES = [], [0]
SECTION = ['']

# Home's own cards are the source of truth for which apps exist and in what order
_idx = open(os.path.join(DOCS, 'index.html'), encoding='utf-8').read()
APPS = re.findall(r'<a class="app" href="([^"]+\.html)"', _idx)
ICONS = re.findall(r'<a class="app" href="[^"]+\.html"[^>]*><div class="ic">([^<]+)</div>', _idx)
PAGES = ['index.html'] + APPS
# most used first, by when each app last saved something to Drive (checked 7 Nov 2026); Shoebox keeps no file of
# its own, so it sits after the four with changes in the last week
ORDER = ['asana-rounds.html', 'drop-tray.html', 'strongroom.html', 'hack-shelf.html', 'shoebox.html',
         'tally-board.html', 'spine-bell.html', 'dumbbell-dojo.html', 'keycap-atlas.html', 'event-log.html',
         'sign-desk.html']   # Sign Desk, new on 8 Nov 2026, last until it has been used
INSTALLED = 'window.APPNAV_TEST_INSTALLED=true;'

# the fake Google Drive (and Google's sign-in stub) from the Drop Tray checker
_spec = importlib.util.spec_from_file_location('check_drop_tray', os.path.join(ROOT, 'tools', 'check-drop-tray.py'))
_dt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dt)


def check(cond, msg):
    if cond:
        PASSES[0] += 1
    else:
        FAILS.append('[%s] %s' % (SECTION[0], msg))
        print('  FAIL', msg)
    return cond


def run_section(name, fn, *a):
    SECTION[0] = name
    print('-', name)
    try:
        fn(*a)
    except Exception as e:
        check(False, 'crashed: %s: %s' % (type(e).__name__, str(e).splitlines()[0][:300] if str(e) else ''))


def serve(directory):
    s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', port), functools.partial(Quiet, directory=directory))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, 'http://127.0.0.1:%d/' % port


class Session:
    """One browser context. With fake=None everything off the local server is aborted."""

    def __init__(self, browser, base, viewport=(1280, 800), scheme='light', installed=False, fake=None, init=None):
        self.base, self.fake, self.blocked, self.errors = base, fake, [], []
        self.ctx = browser.new_context(viewport={'width': viewport[0], 'height': viewport[1]}, color_scheme=scheme)
        if installed:
            self.ctx.add_init_script(INSTALLED)
        if init:
            self.ctx.add_init_script(init)
        self.ctx.route('**/*', self.route)
        self.page = self.ctx.new_page()
        self.watch(self.page)

    def watch(self, page):
        page.set_default_timeout(8000)
        page.on('pageerror', lambda e: self.errors.append('pageerror: ' + str(e)))
        page.on('console', self.console)

    def console(self, msg):
        if msg.type == 'error' and not re.search(r'Failed to load resource|net::ERR_|CORS policy', msg.text):
            self.errors.append('console: ' + msg.text[:300])

    def route(self, route, req):
        u = urllib.parse.urlsplit(req.url)
        if req.url.startswith(self.base) or u.scheme in ('data', 'blob'):
            return route.continue_()
        if self.fake is not None:
            if req.url.startswith('https://accounts.google.com/gsi/client'):
                return route.fulfill(status=200, body=_dt.GIS_STUB, content_type='text/javascript')
            if u.hostname == 'www.googleapis.com':
                try:
                    return self.fake.api(route, req)
                except Exception as e:
                    self.errors.append('fake drive crashed on %s %s: %r' % (req.method, req.url, e))
                    return route.abort()
        if not re.match(r'https://fonts\.(googleapis|gstatic)\.com/', req.url):
            self.blocked.append(req.url)
        return route.abort()

    def open(self, page='index.html'):
        self.page.goto(self.base + ('' if page == 'index.html' else page))
        self.page.locator('nav.appnav').wait_for(state='attached')

    def close(self):
        if self.fake:
            self.fake.release()
        self.ctx.close()


def hrefs_expected(page):
    games = '#games' if page == 'index.html' else './#games'
    return ['./'] + APPS + [games]


def ev(p, js, arg=None):
    return p.evaluate(js, arg)


STRIP = r"""()=>{var n=document.querySelectorAll('nav.appnav');if(n.length!==1)return {count:n.length};var nav=n[0];
  var items=[...nav.querySelectorAll('a.appnav-item')];
  return {count:1, first:document.body.firstElementChild===nav, top:nav.getBoundingClientRect().top,
    hrefs:items.map(a=>a.getAttribute('href')), current:items.filter(a=>a.getAttribute('aria-current')==='page').map(a=>a.getAttribute('href')),
    labels:items.map(a=>a.getAttribute('aria-label')||''), targets:items.map(a=>[a.getAttribute('target')||'',a.getAttribute('rel')||'']),
    back:(function(b){return b?{shown:!b.hidden&&getComputedStyle(b).display!=='none'}:null})(nav.querySelector('.appnav-back'))};}"""

# contrast of every visible strip item's text against what is behind it
CONTRAST = r"""()=>{function rgb(c){var m=c.match(/[\d.]+/g).map(Number);if(/^color\(srgb/.test(c))m=m.map((v,i)=>i<3?v*255:v);return m;}
  function lum(c){var a=rgb(c).slice(0,3).map(v=>{v/=255;return v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4)});return 0.2126*a[0]+0.7152*a[1]+0.0722*a[2];}
  function bg(el){while(el){var b=getComputedStyle(el).backgroundColor,m=rgb(b);if(m.length<4||m[3]>0.5)return b;el=el.parentElement;}return 'rgb(255,255,255)';}
  var worst=99,who='';document.querySelectorAll('nav.appnav a.appnav-item, nav.appnav .appnav-back').forEach(a=>{if(a.hidden||!a.getClientRects().length)return;
    var t=a.querySelector('.appnav-t');var el=t&&getComputedStyle(t).display!=='none'?t:a;var L1=lum(getComputedStyle(el).color),L2=lum(bg(a));
    var r=(Math.max(L1,L2)+0.05)/(Math.min(L1,L2)+0.05);if(r<worst){worst=r;who=a.getAttribute('href')||'back';}});
  return [Math.round(worst*100)/100,who];}"""

# where the strip sits, and the box of each of its parts: any page CSS that leaks in shows up as a difference from Home
LOOK = r"""()=>{var nav=document.querySelector('nav.appnav'),r=nav.getBoundingClientRect(),cw=document.documentElement.clientWidth;
  function box(el,edge){if(!el)return null;var c=getComputedStyle(el);return [c.display,edge?'':c.marginTop,edge?'':c.marginRight,c.marginBottom,edge?'':c.marginLeft,c.paddingTop,c.paddingRight,c.paddingBottom,c.paddingLeft,
    c.borderTopWidth+' '+c.borderTopStyle,c.borderBottomWidth+' '+c.borderBottomStyle,c.borderLeftWidth+' '+c.borderLeftStyle,c.borderRightWidth+' '+c.borderRightStyle,
    c.fontSize,c.fontWeight,c.fontFamily,c.lineHeight,c.textDecorationLine,Math.round(el.getBoundingClientRect().height)].join('|');}
  var cur=nav.querySelector('a.appnav-item[aria-current="page"]'),other=nav.querySelector('.appnav-list a.appnav-item:not([aria-current])');
  return {edge:[Math.round(r.left),Math.round(cw-r.right)],
    parts:{nav:box(nav,true),inner:box(nav.querySelector('.appnav-in')),fixed:box(nav.querySelector('.appnav-fixed')),list:box(nav.querySelector('.appnav-list')),
      item:box(other),icon:box(other&&other.querySelector('.appnav-ic')),name:box(cur&&cur.querySelector('.appnav-t'))}};}"""

# make every sticky thing's container tall, scroll, and report where the strip and the sticky things are
STICKY = r"""(y)=>{var nav=document.querySelector('nav.appnav');
  document.querySelectorAll('#app[hidden]').forEach(e=>{e.hidden=false;});   /* Strongroom's layout sits behind its lock screen */
  var st=[...document.querySelectorAll('body *')].filter(e=>!nav.contains(e)&&!e.closest('dialog,#tray')&&getComputedStyle(e).position==='sticky'&&getComputedStyle(e).top!=='auto'&&e.getClientRects().length);
  st.forEach(e=>{if(e.parentElement)e.parentElement.style.minHeight='4000px';});
  var pad=document.createElement('div');pad.style.height='4000px';document.body.appendChild(pad);
  window.scrollTo(0,y);
  return {y:scrollY, nav:nav.getBoundingClientRect().top, bottom:nav.getBoundingClientRect().bottom,
    sticky:st.map(e=>({name:e.tagName.toLowerCase()+(e.className&&typeof e.className==='string'?'.'+e.className.split(' ')[0]:''),top:e.getBoundingClientRect().top}))};}"""


# ------------------------------------------------------------------ the strip on every page, every size
def layout_flow(browser, base):
    for scheme in ('light', 'dark'):
        for w, h in ((390, 844), (1280, 800)):
            s = Session(browser, base, viewport=(w, h), scheme=scheme)
            home_look = {}
            try:
                for page in PAGES:
                    def one():
                        p = s.page
                        s.open(page)
                        st = ev(p, STRIP)
                        if not check(st.get('count') == 1, 'exactly one strip (%s)' % st.get('count')):
                            return
                        check(st['first'], 'the strip is the first thing in <body>')
                        check(abs(st['top']) < 0.5, 'the strip starts at the very top (%s)' % st['top'])
                        look = ev(p, LOOK)
                        check(look['edge'] == [0, 0], 'the strip runs edge to edge whatever the page\'s own padding (%s)' % look['edge'])
                        if page == 'index.html':
                            home_look.update(look['parts'])
                        else:
                            diff = {k: (v, home_look.get(k)) for k, v in look['parts'].items() if v != home_look.get(k)}
                            check(not diff, 'the strip looks exactly as on Home (the page\'s own CSS leaks in: %s)' % diff)
                        check(st['hrefs'] == hrefs_expected(page), 'Home, the eleven apps in Home\'s order, then Games\n      got: %s' % st['hrefs'])
                        want = './' if page == 'index.html' else page
                        check(st['current'] == [want], 'only this page is marked current (%s)' % st['current'])
                        check(all(st['labels']), 'every item has a name for screen readers and the icon-only view')
                        icons = ev(p, "()=>[...document.querySelectorAll('nav.appnav .appnav-list a.appnav-item .appnav-ic')].map(i=>i.textContent)")
                        check(icons == ICONS + ['🎮'], 'the strip shows the same icons as Home cards, decoded right on this page (%s)' % icons)
                        check(st['back'] is not None and not st['back']['shown'], 'no Back arrow in a browser tab')
                        sw = ev(p, '()=>document.documentElement.scrollWidth')
                        check(sw <= w, 'no sideways scrolling of the page at %d px (%d)' % (w, sw))
                        tap = ev(p, "()=>Math.min(...[...document.querySelectorAll('nav.appnav a.appnav-item')].map(a=>{var r=a.getBoundingClientRect();return Math.min(r.width,r.height)}))")
                        check(tap >= 36, 'every item is at least 36 px to tap (%s)' % tap)
                        con = ev(p, CONTRAST)
                        check(con[0] >= 4.5, 'strip text contrast at least 4.5:1 in %s (%s on %s)' % (scheme, con[0], con[1]))
                        if w == 1280:
                            fit = ev(p, """()=>{var l=document.querySelector('nav.appnav .appnav-list');
                                return {over:l.scrollWidth-l.clientWidth, names:[...document.querySelectorAll('nav.appnav a.appnav-item .appnav-t')].filter(t=>getComputedStyle(t).display==='none').length}}""")
                            check(fit['names'] == 0, 'at 1280 px every item shows its name (%d hidden)' % fit['names'])
                            check(fit['over'] <= 1, 'at 1280 px the strip does not scroll (%d px over)' % fit['over'])
                        else:
                            cur = ev(p, """()=>{var a=document.querySelector('nav.appnav a.appnav-item[aria-current="page"]'),r=a.getBoundingClientRect();
                                var t=a.querySelector('.appnav-t');return {l:r.left,r:r.right,name:!!t&&getComputedStyle(t).display!=='none'}}""")
                            check(cur['l'] >= 0 and cur['r'] <= w + 0.5, 'on a phone the current app is scrolled into view (%s..%s)' % (round(cur['l']), round(cur['r'])))
                            check(cur['name'], 'on a phone the current app still shows its name')
                            if page == 'index.html':
                                seen = ev(p, """()=>[...document.querySelectorAll('nav.appnav .appnav-list a.appnav-item')].slice(0,3).map(a=>{var r=a.getBoundingClientRect();return [a.getAttribute('href'),r.left>=0&&r.right<=innerWidth]})""")
                                check(all(v for _, v in seen), 'on a phone, Home shows the three most used apps without scrolling the strip (%s)' % seen)
                        if SHOTS:
                            p.screenshot(path=os.path.join(SHOTS, 'nav-%s-%s-%d.png' % (page.replace('.html', ''), scheme, w)))
                        sk = ev(p, STICKY, 600)
                        if page == 'strongroom.html' and w == 1280:
                            check(any(x['name'] == 'aside.rail' for x in sk['sticky']), 'the Strongroom side panel was measured (%s)' % sk['sticky'])
                        if sk['y'] > 100:
                            check(abs(sk['nav']) < 0.5, 'after scrolling down the strip is still at the top (%s)' % sk['nav'])
                            under = [x for x in sk['sticky'] if x['top'] < sk['bottom'] - 1]
                            check(not under, 'nothing of the page sticks underneath the strip: %s' % under)
                    run_section('%s %s %dpx' % (page, scheme, w), one)
                for e in s.errors:
                    check(False, 'browser error: ' + e)
                check(not s.blocked, 'no request to an unexpected host: %s' % s.blocked[:3])
            finally:
                s.close()


def links_flow(browser, base):
    def links():
        for h in sorted(set(hrefs_expected('x') + APPS)):
            try:
                code = urllib.request.urlopen(base + h.split('#')[0], timeout=5).status
            except Exception as e:
                code = getattr(e, 'code', str(e))
            check(code == 200, 'strip link %s answers 200 (%s)' % (h, code))
        src = open(os.path.join(DOCS, 'app-nav.js'), encoding='utf-8').read()
        for api in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write'):
            check(api not in src, 'app-nav.js builds the strip without ' + api)
        games = [g for g in os.listdir(DOCS) if os.path.isdir(os.path.join(DOCS, g)) and os.path.isfile(os.path.join(DOCS, g, g + '.html'))]
        for g in games:
            check('app-nav.js' not in open(os.path.join(DOCS, g, g + '.html'), encoding='utf-8').read(), 'the game %s keeps its full screen (no strip)' % g)
    run_section('links', links)


# ------------------------------------------------------------------ moving between pages
def browser_tab_flow(browser, base):
    s = Session(browser, base)
    p = s.page
    try:
        def app_to_app():
            s.open('asana-rounds.html')
            p.locator('nav.appnav a.appnav-item[href="drop-tray.html"]').click()
            p.wait_for_url('**/drop-tray.html')
            check(len(s.ctx.pages) == 1, 'in an app, a strip link opens in the same tab (%d tabs)' % len(s.ctx.pages))
            st = ev(p, STRIP)
            check(st['current'] == ['drop-tray.html'], 'Drop Tray is now the current item (%s)' % st['current'])
            check(not st['back']['shown'], 'still no Back arrow in a browser tab, though there is history now')
            check(all(t == ['', ''] for t in st['targets']), 'in an app no strip link has a target (%s)' % st['targets'])

        def ctrl_click():
            s.open('hack-shelf.html')
            with s.ctx.expect_page() as newp:
                p.locator('nav.appnav a.appnav-item[href="shoebox.html"]').click(modifiers=['Control'])
            np_ = newp.value
            s.watch(np_)
            np_.wait_for_load_state()
            check(np_.url.endswith('/shoebox.html'), 'Ctrl+click opens the app in a new tab (%s)' % np_.url)
            check(p.url.endswith('/hack-shelf.html'), '... and leaves this tab where it was (%s)' % p.url)
            np_.close()

        def current_item():
            s.open('event-log.html')
            ev(p, "()=>{var d=document.createElement('div');d.style.height='3000px';document.body.appendChild(d);scrollTo(0,800);}")
            p.locator('nav.appnav a.appnav-item[aria-current="page"]').click()
            p.wait_for_function('scrollY<5')
            check(p.url.endswith('/event-log.html'), 'tapping the current app stays on it (%s)' % p.url)

        def home_new_tab():
            s.open('index.html')
            st = ev(p, STRIP)
            others = [t for h, t in zip(st['hrefs'], st['targets']) if h not in ('./', '#games')]
            check(all(t[0] == '_blank' and 'noopener' in t[1] for t in others), 'on Home in a browser tab, strip links open new tabs like the cards (%s)' % others)
            with s.ctx.expect_page() as newp:
                p.locator('nav.appnav a.appnav-item[href="keycap-atlas.html"]').click()
            np_ = newp.value
            s.watch(np_)
            np_.wait_for_load_state()
            check(np_.url.endswith('/keycap-atlas.html') and p.url.rstrip('/') == s.base.rstrip('/'), 'Home stays open; the app opens beside it (%s)' % np_.url)
            np_.close()
            cards = ev(p, "()=>[...document.querySelectorAll('#apps a.app')].map(a=>a.target)")
            check(cards and all(t == '_blank' for t in cards), 'Home\'s app cards still open new tabs in a browser tab')

        def home_from_app():
            s.open('spine-bell.html')
            p.locator('nav.appnav a.appnav-item[href="./"]').click()
            p.wait_for_url(s.base)
            check(len(s.ctx.pages) == 1, 'in an app, Home opens in the same tab')

        def shoebox_home():
            s.open('shoebox.html')
            t0 = time.time()
            p.locator('nav.appnav a.appnav-item[href="./"]').click()
            p.wait_for_url(s.base)
            check(time.time() - t0 < 2, 'Shoebox (no Drive store of its own) goes Home at once (%.1f s)' % (time.time() - t0))

        for name, fn in (('app to app', app_to_app), ('ctrl+click', ctrl_click), ('current item', current_item),
                         ('Home keeps new tabs', home_new_tab), ('Home from an app', home_from_app), ('Shoebox to Home', shoebox_home)):
            run_section('browser tab: ' + name, fn)
        SECTION[0] = 'browser tab'
        for e in s.errors:
            check(False, 'browser error: ' + e)
    finally:
        s.close()


RESTORED = "window.addEventListener('pageshow',function(e){if(e.persisted)window.__restored=true;});"


def back_to(p, path):
    """Wait for Back to land on path. A page restored from the back/forward cache fires no load event,
    so this polls the address instead of using wait_for_url."""
    p.wait_for_function('(p)=>location.pathname.endsWith(p)', arg=path)


def installed_flow(browser, base):
    # browser has Chrome's back/forward cache switched on (Playwright turns it off by default), because phones and
    # the laptop restore a page from it on Back, exactly as it was when it was left
    for w, h in ((390, 844), (1280, 800)):
        s = Session(browser, base, viewport=(w, h), installed=True, init=RESTORED)
        p = s.page
        try:
            def same_window():
                s.open('index.html')
                st = ev(p, STRIP)
                check(not st['back']['shown'], 'the first page has no Back arrow (nowhere to go back to)')
                check(all(t == ['', ''] for t in st['targets']), 'installed, strip links on Home have no target (%s)' % st['targets'])
                local = ev(p, """()=>[...document.querySelectorAll('a[href]')].filter(a=>a.href.startsWith(location.origin)&&!a.closest('nav.appnav')).map(a=>[a.getAttribute('href'),a.target])""")
                check(local and all(t == '' for _, t in local), 'installed, Home\'s own app and game links open in the same window (%s)' % [x for x in local if x[1]])
                ext = ev(p, """()=>[...document.querySelectorAll('a[href]')].filter(a=>!a.href.startsWith(location.origin)).map(a=>a.target)""")
                check(ext and all(t == '_blank' for t in ext), 'outside links (Learning Hub, laptop tools) still open outside the app')
                p.locator('#apps a.app[href="asana-rounds.html"]').click()
                p.wait_for_url('**/asana-rounds.html')
                check(len(s.ctx.pages) == 1, 'a Home card opens the app in the same window (%d windows)' % len(s.ctx.pages))
                p.locator('nav.appnav').wait_for(state='attached')
                st = ev(p, STRIP)
                check(st['back']['shown'], 'in the app there is now a Back arrow')
                b = ev(p, "()=>{var r=document.querySelector('nav.appnav .appnav-back').getBoundingClientRect();return Math.min(r.width,r.height)}")
                check(b >= 36, 'the Back arrow is at least 36 px to tap (%s)' % b)
                p.locator('nav.appnav .appnav-back').click()
                back_to(p, '/')
                check(p.url.rstrip('/') == s.base.rstrip('/'), 'Back returns to Home (%s)' % p.url)
                check(ev(p, '()=>!!window.__restored'), 'Home came back from the back/forward cache (so the next check means something)')
                p.locator('#apps a.app[href="hack-shelf.html"]').click()
                try:
                    p.wait_for_url('**/hack-shelf.html', timeout=5000)
                    ok = True
                except Exception:
                    ok = False
                check(ok, 'after coming Back, Home\'s cards still open apps (%s)' % p.url)

            def app_to_app():
                s.open('drop-tray.html')
                p.locator('nav.appnav a.appnav-item[href="hack-shelf.html"]').click()
                p.wait_for_url('**/hack-shelf.html')
                p.locator('nav.appnav').wait_for(state='attached')
                check(ev(p, STRIP)['back']['shown'], 'after switching apps, Back is offered')
                p.locator('nav.appnav .appnav-back').click()
                back_to(p, '/drop-tray.html')
                check(True, 'Back returns to the previous app')
                check(ev(p, '()=>!!window.__restored'), 'Drop Tray came back from the back/forward cache (so the next check means something)')
                # the page may come back from the back/forward cache, as it was when it was left: the strip must still work
                p.locator('nav.appnav a.appnav-item[href="event-log.html"]').click()
                try:
                    p.wait_for_url('**/event-log.html', timeout=5000)
                    ok = True
                except Exception:
                    ok = False
                check(ok, 'after coming Back, the strip still switches apps (%s)' % p.url)
                sw = ev(p, '()=>document.documentElement.scrollWidth')
                check(sw <= w, 'with the Back arrow the page still fits %d px (%d)' % (w, sw))

            def back_asks():
                s.open('index.html')
                p.locator('#apps a.app[href="asana-rounds.html"]').click()
                p.wait_for_url('**/asana-rounds.html')
                p.locator('#tGo').click()
                p.wait_for_function("document.querySelector('#timer').dataset.state==='run'")
                p.locator('nav.appnav .appnav-back').click()
                p.locator('dialog.appnav-dlg').wait_for(state='visible', timeout=3000)
                d = ev(p, DIALOG)
                check(d['buttons'] == ['Stay', 'Leave anyway'], 'installed, Back asks too, with no new tab (that would leave the app) (%s)' % d['buttons'])
                p.locator('dialog.appnav-dlg button', has_text='Leave anyway').click()
                back_to(p, '/')
                check(True, 'Leave anyway goes back')

            for name, fn in (('Home, an app, Back', same_window), ('app to app and Back', app_to_app), ('Back with a timer running', back_asks)):
                run_section('installed %dpx: %s' % (w, name), fn)
            SECTION[0] = 'installed %dpx' % w
            for e in s.errors:
                check(False, 'browser error: ' + e)
            check(not s.blocked, 'no request to an unexpected host: %s' % s.blocked[:3])
        finally:
            s.close()


# ------------------------------------------------------------------ a running timer: ask before leaving
DIALOG = r"""()=>{var d=document.querySelector('dialog.appnav-dlg');if(!d||!d.open)return {open:false};
  function rgb(c){var m=c.match(/[\d.]+/g).map(Number);if(/^color\(srgb/.test(c))m=m.map((v,i)=>i<3?v*255:v);return m;}
  function lum(c){var a=rgb(c).slice(0,3).map(v=>{v/=255;return v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4)});return 0.2126*a[0]+0.7152*a[1]+0.0722*a[2];}
  function bg(el){while(el){var b=getComputedStyle(el).backgroundColor,m=rgb(b);if(m.length<4||m[3]>0.5)return b;el=el.parentElement;}return 'rgb(255,255,255)';}
  var worst=99;d.querySelectorAll('h2,p,button').forEach(e=>{if(!e.getClientRects().length)return;var L1=lum(getComputedStyle(e).color),L2=lum(bg(e));worst=Math.min(worst,(Math.max(L1,L2)+0.05)/(Math.min(L1,L2)+0.05));});
  var r=d.getBoundingClientRect(),btns=[...d.querySelectorAll('button')].filter(b=>b.getClientRects().length);
  return {open:true,text:d.innerText,focus:document.activeElement&&document.activeElement.textContent,
    buttons:btns.map(b=>b.textContent),tap:Math.min(...btns.map(b=>{var q=b.getBoundingClientRect();return Math.min(q.width,q.height)})),
    inside:r.left>=0&&r.top>=0&&r.right<=innerWidth&&r.bottom<=innerHeight,contrast:Math.round(worst*100)/100};}"""


def asana_state(p):
    return ev(p, "()=>document.querySelector('#timer').dataset.state")


def guard_flow(browser, base):
    for scheme in ('light', 'dark'):
        for w, h in ((390, 844), (1280, 800)):
            s = Session(browser, base, viewport=(w, h), scheme=scheme)
            p = s.page
            try:
                def asana():
                    s.open('asana-rounds.html')
                    p.locator('#tGo').click()
                    p.wait_for_function("document.querySelector('#timer').dataset.state==='run'")
                    p.locator('nav.appnav a.appnav-item[href="drop-tray.html"]').click()
                    p.locator('dialog.appnav-dlg').wait_for(state='visible', timeout=3000)
                    d = ev(p, DIALOG)
                    check(p.url.endswith('/asana-rounds.html'), 'with the hold timer running, the strip asks instead of leaving (%s)' % p.url)
                    check('lost' in d['text'] and 'ticks are saved' in d['text'], 'it says the countdown is lost and the ticks are not (%r)' % d['text'])
                    check(d['buttons'] == ['Stay', 'Open in a new tab', 'Leave anyway'], 'in a browser tab: Stay, Open in a new tab, Leave anyway (%s)' % d['buttons'])
                    check(d['focus'] == 'Stay', 'Stay is the button already chosen, so Enter keeps the timer (%r)' % d['focus'])
                    check(d['inside'], 'the question fits on screen at %d px' % w)
                    check(d['tap'] >= 40, 'its buttons are at least 40 px to tap (%s)' % d['tap'])
                    check(d['contrast'] >= 4.5, 'its text contrast is at least 4.5:1 in %s (%s)' % (scheme, d['contrast']))
                    p.keyboard.press('Escape')
                    check(not ev(p, DIALOG)['open'] and asana_state(p) == 'run', 'Esc closes it and the timer keeps running')
                    p.locator('nav.appnav a.appnav-item[href="drop-tray.html"]').click()
                    p.locator('dialog.appnav-dlg button', has_text='Stay').click()
                    check(not ev(p, DIALOG)['open'] and asana_state(p) == 'run' and p.url.endswith('/asana-rounds.html'), 'Stay closes it, stays here, and the timer keeps running')
                    p.locator('nav.appnav a.appnav-item[href="drop-tray.html"]').click()
                    with s.ctx.expect_page() as newp:
                        p.locator('dialog.appnav-dlg button', has_text='Open in a new tab').click()
                    np_ = newp.value
                    s.watch(np_)
                    np_.wait_for_load_state()
                    check(np_.url.endswith('/drop-tray.html'), 'Open in a new tab opens the app beside this one (%s)' % np_.url)
                    check(not ev(p, DIALOG)['open'] and asana_state(p) == 'run' and p.url.endswith('/asana-rounds.html'), '... and this tab keeps its timer running')
                    np_.close()
                    with s.ctx.expect_page() as newp:
                        p.locator('nav.appnav a.appnav-item[href="hack-shelf.html"]').click(modifiers=['Control'])
                    newp.value.close()
                    check(not ev(p, DIALOG)['open'], 'Ctrl+click opens a new tab without asking (this tab stays)')
                    p.locator('nav.appnav a.appnav-item[href="drop-tray.html"]').click()
                    p.locator('dialog.appnav-dlg button', has_text='Leave anyway').click()
                    p.wait_for_url('**/drop-tray.html')
                    check(True, 'Leave anyway goes')
                    s.open('asana-rounds.html')
                    check(asana_state(p) == 'idle', 'back in Asana Rounds the countdown is gone, as the question said (%s)' % asana_state(p))
                    p.locator('nav.appnav a.appnav-item[href="drop-tray.html"]').click()
                    p.wait_for_url('**/drop-tray.html')
                    check(True, 'with no timer running, the strip goes without asking')

                def spine():
                    s.open('spine-bell.html')
                    p.locator('#primaryBtn').click()
                    p.wait_for_function("/^\\d+:\\d\\d · /.test(document.title)")
                    p.locator('nav.appnav a.appnav-item[href="./"]').click()
                    p.locator('dialog.appnav-dlg').wait_for(state='visible', timeout=3000)
                    d = ev(p, DIALOG)
                    check('cannot ring' in d['text'] and 'keeps counting' in d['text'], 'Spine Bell says the bell cannot ring away from it but the time keeps counting (%r)' % d['text'])
                    check(d['inside'] and d['contrast'] >= 4.5, 'it fits and reads at %d px in %s (%s)' % (w, scheme, d['contrast']))
                    p.locator('dialog.appnav-dlg button', has_text='Leave anyway').click()
                    p.wait_for_url(s.base)
                    s.open('spine-bell.html')
                    t = ev(p, '()=>document.title')
                    check(re.match(r'^\d+:\d\d · ', t) is not None, 'back in Spine Bell the block is still counting down, as the question said (%r)' % t)
                    p.locator('#resetBtn').click()

                for name, fn in (('Asana Rounds hold timer', asana), ('Spine Bell work block', spine)):
                    run_section('ask before leaving %s %dpx: %s' % (scheme, w, name), fn)
                SECTION[0] = 'ask before leaving %s %dpx' % (scheme, w)
                for e in s.errors:
                    check(False, 'browser error: ' + e)
                check(not s.blocked, 'no request to an unexpected host: %s' % s.blocked[:3])
            finally:
                s.close()


# ------------------------------------------------------------------ a change made just before switching reaches Drive
def save_flow(browser, base):
    fake = _dt.FakeDrive()
    token = json.dumps({'token': 'fake-token-nav', 'exp': int(time.time() * 1000) + 3600 * 1000, 'email': 'tester@example.com'})
    s = Session(browser, base, fake=fake, init="try{if(!sessionStorage.getItem('navcheck.t')){localStorage.setItem('drivesync.token',%s);sessionStorage.setItem('navcheck.t','1');}}catch(e){}" % json.dumps(token))
    p = s.page
    try:
        def saved_before_leaving():
            s.open('event-log.html')
            p.wait_for_function("document.querySelector('.ds')&&document.querySelector('.ds').dataset.state==='saved'", timeout=10000)
            ev(p, "()=>{window.__nav=DriveStore.open({file:'nav-check.json',empty:function(){return {v:1,items:{}};}});}")
            p.wait_for_function('window.__nav.synced', timeout=10000)
            ev(p, "()=>window.__nav.mutate(function(d){d.items.a={text:'ticked a moment before leaving',updatedAt:Date.now()};})")
            t0 = time.time()
            p.locator('nav.appnav a.appnav-item[href="./"]').click()   # well inside the 1.5 s the page would wait before saving
            p.wait_for_url(s.base, timeout=8000)
            took = time.time() - t0
            got = [f for f in fake.live('nav-check.json')]
            doc = json.loads(got[0]['content'] or b'{}') if got else {}
            check(((doc.get('items') or {}).get('a') or {}).get('text') == 'ticked a moment before leaving',
                  'the change reached Drive before Home opened (%s)' % (doc or 'no file in Drive'))
            check(took < 4, 'saving first does not hold you up long (%.1f s)' % took)

        def signed_out_quick():
            ev(p, "()=>localStorage.removeItem('drivesync.token')")
            s.open('tally-board.html')
            t0 = time.time()
            p.locator('nav.appnav a.appnav-item[href="./"]').click()
            p.wait_for_url(s.base)
            check(time.time() - t0 < 2, 'signed out there is nothing to save, so Home opens at once (%.1f s)' % (time.time() - t0))

        run_section('save before leaving', saved_before_leaving)
        run_section('signed out', signed_out_quick)
        SECTION[0] = 'save before leaving'
        for e in s.errors:
            check(False, 'browser error: ' + e)
    finally:
        s.close()


def main():
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
    check(len(APPS) == 11, 'Home lists eleven app cards (%d)' % len(APPS))
    SECTION[0] = 'order'
    check(APPS == ORDER, 'Home and the strip list the apps most used first\n      want: %s\n      got:  %s' % (ORDER, APPS))
    httpd, base = serve(DOCS)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='chrome', headless=not HEADED)
            links_flow(browser, base)
            print('== every page')
            layout_flow(browser, base)
            print('== moving between pages')
            browser_tab_flow(browser, base)
            bf = pw.chromium.launch(channel='chrome', headless=not HEADED, ignore_default_args=['--disable-back-forward-cache'])
            installed_flow(bf, base)
            bf.close()
            print('== ask before leaving a running timer')
            guard_flow(browser, base)
            save_flow(browser, base)
            browser.close()
    finally:
        httpd.shutdown()
    print('\n%d checks passed, %d failed' % (PASSES[0], len(FAILS)))
    for x in FAILS:
        print('  ' + x)
    sys.exit(1 if FAILS else 0)


if __name__ == '__main__':
    main()
