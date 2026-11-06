"""Launcher (Home) checker: drives docs/index.html in headless Chrome, signed out.

  python tools/check-launcher.py            # docs/index.html
  python tools/check-launcher.py --headed   # watch it
  LAUNCHER_SHOTS=<folder> python tools/check-launcher.py   # also save a screenshot of each layout

Nothing here can reach Google. The page is never signed in, and every request that leaves the local
server is aborted; any host other than Google Fonts is reported as a failure. The to-do list runs on its
browser cache (drive-sync.js signed out), which is exactly what a device sees before its first sign-in.

Exit code 0 means every check passed; each failure is printed with the section it came from.
"""
import datetime, functools, http.server, json, os, re, socket, struct, sys, threading, urllib.parse, urllib.request
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # the Windows console is cp1252; page text has arrows and ticks
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, 'docs')
HEADED = '--headed' in sys.argv
SHOTS = os.environ.get('LAUNCHER_SHOTS', '')
FAILS, PASSES = [], [0]
SECTION = ['']
# every run happens on a fixed day, so "today", "overdue" and "the next day" never depend on when it runs
DAY0 = datetime.date(2026, 11, 6)

LH = 'https://ravivarmachaluvadi.github.io/learning-hub/'
EXTERNAL = {
    LH,
    *(LH + 'tools/%s/' % t for t in ('claude', 'lead-engineer', 'ui', 'react', 'python', 'kotlin', 'java-concurrency',
                                          'java-practice')),
    'https://ravivarmachaluvadi.github.io/',
    'https://github.com/ravivarmachaluvadi/java-interview-practice',
    'http://127.0.0.1:8025/',
    'http://127.0.0.1:8028/',
    'http://localhost:8765/',
}
APPS = ['keycap-atlas.html', 'strongroom.html', 'dumbbell-dojo.html', 'tally-board.html', 'spine-bell.html',
        'event-log.html', 'hack-shelf.html', 'asana-rounds.html', 'shoebox.html', 'drop-tray.html']
GAMES = ['river-rapids/river-rapids.html', 'sky-rings/sky-rings.html', 'sound-safari/sound-safari.html',
         'vroom-valley/vroom-valley.html', 'word-racer/word-racer.html']


def at(day, hh=9, mm=0):
    return datetime.datetime.combine(DAY0 + datetime.timedelta(days=day), datetime.time(hh, mm))


def iso(day):
    return (DAY0 + datetime.timedelta(days=day)).isoformat()


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


# ------------------------------------------------------------------ local web server
def serve(directory):
    s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = functools.partial(Quiet, directory=directory)
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, 'http://127.0.0.1:%d/' % port


class Session:
    """One browser context; everything off the local server is aborted."""

    def __init__(self, browser, base, viewport=(1280, 800), scheme='light'):
        self.blocked, self.errors = [], []
        self.ctx = browser.new_context(viewport={'width': viewport[0], 'height': viewport[1]}, color_scheme=scheme)
        self.ctx.route('**/*', self.route)
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(6000)
        self.page.on('pageerror', lambda e: self.errors.append('pageerror: ' + str(e)))
        self.page.on('console', self.console)
        self.base = base

    def console(self, msg):
        if msg.type == 'error' and not re.search(r'Failed to load resource|net::ERR_', msg.text):
            self.errors.append('console: ' + msg.text[:300])

    def route(self, route, req):
        u = urllib.parse.urlsplit(req.url)
        if req.url.startswith(self.base) or u.scheme in ('data', 'blob'):
            return route.continue_()
        if not re.match(r'https://fonts\.(googleapis|gstatic)\.com/', req.url):
            self.blocked.append(req.url)
        return route.abort()

    def close(self):
        self.ctx.close()

    # ---- page helpers
    def open(self, day=0, hh=9):
        self.page.clock.set_system_time(at(day, hh))
        self.page.goto(self.base)
        self.page.locator('#todoForm').wait_for()

    def txt(self, sel):
        return self.page.locator(sel).inner_text().strip()

    def add(self, text, due=''):
        p = self.page
        p.locator('#todoText').fill(text)
        p.locator('#todoDue').fill(due)
        p.locator('#todoAddBtn').click()

    def open_texts(self):
        return [x.strip() for x in self.page.locator('#todoOpen li.todo .t').all_inner_texts()]

    def done_texts(self):
        return [x.strip() for x in self.page.locator('#todoDone li.todo .t').all_inner_texts()]

    def item(self, text, where='#todoOpen'):
        return self.page.locator('%s li.todo' % where).filter(has=self.page.locator('.t', has_text=re.compile('^%s$' % re.escape(text))))


# ------------------------------------------------------------------ the checks
def todo_flow(s):
    p = s.page

    def empty():
        s.open()
        check(s.open_texts() == [], 'a fresh device has no to-dos (%s)' % s.open_texts())
        check(p.locator('#todoEmpty').is_visible(), 'the empty list says so')
        check(not p.locator('#todoDoneWrap').is_visible(), '"Done today" is hidden while nothing is done')
        check(p.locator('#auth .ds-google').count() == 1, 'signed out, the header offers Continue with Google')
        s.add('   ')
        check(s.open_texts() == [], 'a blank to-do is not added')

    def add_and_order():
        s.add('Pay electricity bill', iso(2))
        s.add('Call bank')
        s.add('Renew passport photo', iso(-1))
        s.add('Book cab', iso(0))
        check(s.open_texts() == ['Renew passport photo', 'Book cab', 'Pay electricity bill', 'Call bank'],
              'order: overdue, then by due date, then undated (%s)' % s.open_texts())
        check(p.locator('#todoText').input_value() == '', 'the text box clears after adding')
        check(p.locator('#todoDue').input_value() == '', 'the date box clears after adding')
        check(not p.locator('#todoEmpty').is_visible(), 'the empty note goes away once there is a to-do')
        check(s.item('Renew passport photo').get_attribute('data-state') == 'overdue', 'a past due date is overdue')
        check(s.item('Book cab').get_attribute('data-state') == 'open', 'due today is not overdue')
        check('today' in s.item('Book cab').locator('.due').inner_text().lower(), 'due today says "today" (%r)' % s.item('Book cab').locator('.due').inner_text())
        check('overdue' in s.item('Renew passport photo').locator('.due').inner_text().lower(), 'the overdue item says overdue')
        check(s.item('Call bank').locator('.due').count() == 0 or s.item('Call bank').locator('.due').inner_text().strip() == '', 'an undated to-do shows no date')
        summ = s.txt('#todoSummary')
        check('4 to do' in summ and '1 overdue' in summ, 'the summary counts 4 to do, 1 overdue (%r)' % summ)
        red = p.evaluate("""()=>{var o=document.querySelector('#todoOpen li.todo[data-state="overdue"] .due');
                              var n=document.querySelector('#todoOpen li.todo[data-state="open"] .due');
                              return o&&n?[getComputedStyle(o).color,getComputedStyle(n).color]:null}""")
        check(red and red[0] != red[1], 'the overdue date is coloured differently from a normal one (%s)' % red)

    def markup_is_text():
        s.add('<b>x</b> & <img src=y onerror=alert(1)>')
        it = s.item('<b>x</b> & <img src=y onerror=alert(1)>')
        check(it.count() == 1, 'typed markup is stored and shown as the exact text')
        check(p.locator('#todoOpen b, #todoOpen img').count() == 0, 'typed markup never becomes elements')
        it.locator('button.del').click()

    def tick_untick():
        # click, not check(): ticking moves the row to the other list, so the box Playwright would re-read is gone
        s.item('Call bank').locator('input.tick').click()
        check('Call bank' not in s.open_texts(), 'a ticked to-do leaves the open list')
        check(s.done_texts() == ['Call bank'], '... and shows under Done today (%s)' % s.done_texts())
        check(p.locator('#todoDoneWrap').is_visible(), '"Done today" appears once something is done')
        check('3 to do' in s.txt('#todoSummary'), 'the summary drops to 3 to do (%r)' % s.txt('#todoSummary'))
        s.item('Call bank', '#todoDone').locator('input.tick').click()
        check('Call bank' in s.open_texts() and s.done_texts() == [], 'unticking puts it back in the open list')
        check(not s.item('Call bank').locator('input.tick').is_checked(), '... with its box unticked')
        s.item('Call bank').locator('input.tick').click()
        check(s.item('Call bank', '#todoDone').locator('input.tick').is_checked(), 'ticked again, its box shows ticked under Done today')

    def delete_undo():
        s.item('Pay electricity bill').locator('button.del').click()
        check('Pay electricity bill' not in s.open_texts(), 'delete removes the to-do')
        check(p.locator('#undoBtn').is_visible(), 'an Undo button appears after a delete')
        p.locator('#undoBtn').click()
        check('Pay electricity bill' in s.open_texts(), 'Undo brings it back')
        check(not p.locator('#undoBtn').is_visible(), 'the Undo button goes away after use')

    def paste():
        check(not p.locator('#pasteBox').is_visible(), 'the paste box starts hidden')
        p.locator('#pasteToggle').click()
        p.locator('#pasteBox').fill('Send IR 71 form\n\n   Submit iEngage timesheet  \n- Check Percipio\n')
        p.locator('#pasteAddBtn').click()
        got = s.open_texts()
        for t in ('Send IR 71 form', 'Submit iEngage timesheet', 'Check Percipio'):
            check(t in got, 'pasted line added, trimmed and without its bullet: %r (%s)' % (t, got))
        check(len(got) == 6, 'blank lines add nothing: 3 + 3 = 6 open to-dos (%d)' % len(got))
        check(not p.locator('#pasteBox').is_visible(), 'the paste box closes after adding')

    def survives_reload():
        before = (s.open_texts(), s.done_texts())
        s.open()
        check((s.open_texts(), s.done_texts()) == before, 'everything is still there after a reload')
        check(p.locator('#auth').inner_text().find('changes waiting') >= 0, 'signed out, the header says changes are waiting')

    def next_day():
        s.open(day=1)
        check(s.done_texts() == [], 'the next day, yesterday\'s done items have dropped off (%s)' % s.done_texts())
        check('Call bank' not in s.open_texts(), '... and do not come back as open')
        check(s.item('Book cab').get_attribute('data-state') == 'overdue', 'yesterday\'s "due today" is now overdue')
        check('2 overdue' in s.txt('#todoSummary'), 'the summary counts 2 overdue (%r)' % s.txt('#todoSummary'))
        doc = p.evaluate("()=>JSON.parse(localStorage.getItem('drivesync.home.json'))")
        items = (doc or {}).get('doc', {}).get('items', {})
        check(any(v.get('text') == 'Call bank' and v.get('done') for v in items.values()), 'the done item is kept in the data, just not shown')
        check(all(v.get('updatedAt') for v in items.values()), 'every stored item carries updatedAt, so Drive merge can order it')

    for name, fn in (('empty', empty), ('add and order', add_and_order), ('markup is text', markup_is_text),
                     ('tick and untick', tick_untick), ('delete and undo', delete_undo), ('paste a list', paste),
                     ('survives reload', survives_reload), ('the next day', next_day)):
        run_section('to-do: ' + name, fn)


def links_flow(s):
    p = s.page

    def links():
        s.open()
        hrefs = p.evaluate("()=>[...document.querySelectorAll('a[href]')].map(a=>a.getAttribute('href'))")
        ext = {h for h in hrefs if re.match(r'https?://', h)}
        check(ext == EXTERNAL, 'external links are exactly the planned ones\n      missing: %s\n      extra: %s' % (sorted(EXTERNAL - ext), sorted(ext - EXTERNAL)))
        local = sorted({h for h in hrefs if not re.match(r'https?://|#|mailto:', h)})
        for a in APPS:
            check(a in local, 'the launcher links the app %s' % a)
        for g in GAMES:
            check(g in local, 'the launcher links the game %s' % g)
        for h in local:
            try:
                code = urllib.request.urlopen(s.base + h, timeout=5).status
            except Exception as e:
                code = getattr(e, 'code', str(e))
            check(code == 200, 'same-site link %s answers 200 (%s)' % (h, code))
        bad = p.evaluate("""()=>[...document.querySelectorAll('a[href]')].filter(a=>!/^#/.test(a.getAttribute('href'))&&
                              (a.target!=='_blank'||!/noopener/.test(a.rel))).map(a=>a.getAttribute('href'))""")
        check(bad == [], 'every link opens in a new tab with rel=noopener (%s)' % bad)
        for sec in ('#today', '#apps', '#learning', '#career', '#laptop', '#games'):
            check(p.locator(sec).count() == 1, 'the page has a %s section' % sec)
        order = p.evaluate("()=>['#today','#apps','#learning','#career','#laptop','#games'].map(s=>document.querySelector(s)?document.querySelector(s).getBoundingClientRect().top+scrollY:-1)")
        check(order == sorted(order) and -1 not in order, 'sections run Today, Apps, Learning, Career, Laptop, Games top to bottom (%s)' % order)
        how = p.locator('#laptop').inner_text()
        for t in ('Code Viewer', 'Java Concurrency', 'Home Hub'):
            check(t in how, 'the laptop section names %s' % t)
        check('Desktop' in how, 'the laptop section says how to start each tool (the Desktop icons)')

    def manifest():
        s.open()
        href = p.evaluate("()=>{var l=document.querySelector('link[rel=manifest]');return l&&l.getAttribute('href')}")
        check(bool(href), 'the page links a web manifest')
        if not href:
            return
        m = json.loads(urllib.request.urlopen(s.base + href, timeout=5).read().decode('utf-8'))
        check(m.get('name') == 'Personal Apps' and m.get('short_name') == 'Home', 'manifest names: Personal Apps / Home (%r, %r)' % (m.get('name'), m.get('short_name')))
        check(m.get('display') == 'standalone', 'manifest display is standalone')
        check(m.get('start_url') in ('./', './index.html', '.'), 'manifest start_url is the launcher (%r)' % m.get('start_url'))
        check(bool(m.get('theme_color')) and bool(m.get('background_color')), 'manifest sets theme and background colours')
        sizes = {}
        for ic in m.get('icons', []):
            path = os.path.join(DOCS, *urllib.parse.urljoin(href, ic['src']).split('/'))
            ok = os.path.isfile(path)
            check(ok and ic.get('type') == 'image/png', 'icon %s exists and is declared image/png' % ic.get('src'))
            if not ok:
                continue
            raw = open(path, 'rb').read(32)
            w, h = struct.unpack('>II', raw[16:24]) if raw[:8] == b'\x89PNG\r\n\x1a\n' else (0, 0)
            check('%dx%d' % (w, h) == ic.get('sizes'), 'icon %s is really %s (%dx%d)' % (ic['src'], ic.get('sizes'), w, h))
            for purpose in (ic.get('purpose') or 'any').split():
                sizes.setdefault(purpose, set()).add(ic.get('sizes'))
        check({'192x192', '512x512'} <= sizes.get('any', set()), 'icons include 192 and 512 for "any" (%s)' % sizes)
        check('512x512' in sizes.get('maskable', set()), 'a 512 maskable icon is included (%s)' % sizes)
        reg = p.evaluate("()=>navigator.serviceWorker.ready.then(r=>r.active&&r.active.scriptURL)")
        check(bool(reg) and reg.endswith('/sw.js'), 'the launcher registers the existing sw.js (%r)' % reg)
        check(bool(p.evaluate("()=>{var m=document.querySelector('meta[name=theme-color]');return m&&m.content}")), 'a theme-color meta tag is set')

    run_section('links', links)
    run_section('manifest', manifest)


def layout_flow(browser, base):
    for scheme, bg in (('light', 'rgb(233, 236, 241)'), ('dark', 'rgb(20, 23, 28)')):
        for w in (390, 1280):
            s = Session(browser, base, viewport=(w, 844), scheme=scheme)
            try:
                def one():
                    s.open()
                    s.add('A fairly long to-do that should wrap on a phone rather than push the page sideways at all', iso(1))
                    sw = p_eval(s, 'document.documentElement.scrollWidth')
                    check(sw <= w, 'no sideways scrolling at %d px (%d)' % (w, sw))
                    got = p_eval(s, 'getComputedStyle(document.body).backgroundColor')
                    check(got == bg, '%s mode background is %s (%s)' % (scheme, bg, got))
                    cols = p_eval(s, "getComputedStyle(document.querySelector('#apps .apps')).gridTemplateColumns.split(' ').length")
                    if w == 390:
                        check(cols == 2, 'on a phone the app grid is 2 columns (%d)' % cols)
                        tap = p_eval(s, "Math.min(...[...document.querySelectorAll('#todoOpen input.tick, #todoOpen button.del, #todoAddBtn')].map(e=>Math.min(e.getBoundingClientRect().width,e.getBoundingClientRect().height)))")
                        check(tap >= 32, 'tick, delete and add are at least 32 px to tap (%s)' % tap)
                    else:
                        check(cols >= 3, 'on a laptop the app grid has 3 or more columns (%d)' % cols)
                    if SHOTS:
                        s.page.screenshot(path=os.path.join(SHOTS, 'launcher-%s-%d.png' % (scheme, w)), full_page=True)
                run_section('layout %s %dpx' % (scheme, w), one)
                for e in s.errors:
                    check(False, 'browser error: ' + e)
            finally:
                s.close()


def p_eval(s, expr):
    return s.page.evaluate('()=>' + expr)


def main():
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
    httpd, base = serve(DOCS)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='chrome', headless=not HEADED)
            s = Session(browser, base)
            try:
                todo_flow(s)
                links_flow(s)
                SECTION[0] = 'browser'
                for e in s.errors:
                    check(False, 'browser error: ' + e)
                check(not s.blocked, 'no request to an unexpected host: %s' % s.blocked[:3])
            finally:
                s.close()
            print('== layout')
            layout_flow(browser, base)
            browser.close()
    finally:
        httpd.shutdown()
    print('\n%d checks passed, %d failed' % (PASSES[0], len(FAILS)))
    for x in FAILS:
        print('  ' + x)
    sys.exit(1 if FAILS else 0)


if __name__ == '__main__':
    main()
