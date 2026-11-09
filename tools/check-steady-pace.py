"""Steady Pace checker: drives docs/steady-pace.html in headless Chrome.

  python tools/check-steady-pace.py            # docs/steady-pace.html
  python tools/check-steady-pace.py --headed   # watch it
  PACE_SHOTS=<folder> python tools/check-steady-pace.py   # also save a screenshot of each layout

The page signs in to nothing and stores nothing except its light/dark choice. Every request that leaves the
local server is aborted, and any host other than Google Fonts is reported as a failure.

The repo is public and the address carries a real name, so the page must not name a person or a company:
the source, the rendered text and the Home row are all searched for the words in BANNED.

Exit code 0 means every check passed; each failure is printed with the section it came from.
"""
import functools, hashlib, http.server, os, re, socket, sys, threading, urllib.parse
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # the Windows console is cp1252; page text has dashes
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, 'docs')
PAGE = 'steady-pace.html'
HEADED = '--headed' in sys.argv
SHOTS = os.environ.get('PACE_SHOTS', '')
FAILS, PASSES = [], [0]
SECTION = ['']
AUDIENCES = ('manager', 'lead', 'peer', 'client')
# Kept as hashes (the first 16 hex digits of the SHA-256 of each lower-case word) so that this public file does not
# itself list the names it is keeping off the page. To add a word, hash it in a throwaway shell.
BANNED = {'372fb41298889bc4', '3bbfaa9a8daf4faa', '43f8e3d0b6692a99', '67d8f50a8ee77c60', 'a1ce7a1b010b368f',
          'ace3e5f024e34055', 'b9711c8944c04de2', 'd67200d6eadbdbeb', 'e82161d1551b9aa2', 'ec22019b737fbad7'}
SECTIONS = ('truth', 'day', 'evidence', 'targets', 'guilt', 'say', 'protect', 'toomuch', 'sources')
# each researched figure must survive edits to the page
FACTS = ('52 min', '14.25', '19.29', '19.6', '10.9', '51.7%', '55', '1,450', '250,000')


def named(text):
    """How many different banned words the text contains."""
    return len({hashlib.sha256(w.encode()).hexdigest()[:16] for w in re.findall(r'[a-z0-9]+', text.lower())} & BANNED)


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
    handler = functools.partial(Quiet, directory=directory)
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, 'http://127.0.0.1:%d/' % port


class Session:
    """One browser context; everything off the local server is aborted."""

    def __init__(self, browser, base, viewport=(1280, 800), scheme='light', init=None):
        self.blocked, self.errors = [], []
        self.ctx = browser.new_context(viewport={'width': viewport[0], 'height': viewport[1]}, color_scheme=scheme)
        self.ctx.grant_permissions(['clipboard-read', 'clipboard-write'], origin=base.rstrip('/'))
        if init:
            self.ctx.add_init_script(init)
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

    def open(self):
        self.page.goto(self.base + PAGE)
        self.page.locator('#phraseList .phrase').first.wait_for()

    def clean(self):
        check(self.blocked == [], 'no request left the site except Google Fonts (%s)' % self.blocked[:3])
        check(self.errors == [], 'no page errors (%s)' % self.errors[:3])

    def close(self):
        self.ctx.close()


def shown(p):
    return p.evaluate("""()=>[...document.querySelectorAll('.phrase')].filter(e=>e.offsetParent!==null)
        .map(e=>({aud:e.dataset.aud.split(' '),text:e.querySelector('.phrase-text').textContent}))""")


def counted(p):
    m = re.search(r'\d+', p.locator('#phraseCount').inner_text())
    return int(m.group(0)) if m else -1


# ------------------------------------------------------------------ the file itself
def source():
    path = os.path.join(DOCS, PAGE)
    if not check(os.path.isfile(path), 'docs/%s exists' % PAGE):
        return
    src = open(path, encoding='utf-8').read()
    check('<meta name="robots" content="noindex">' in src, 'the page asks search engines not to list it')
    check(src.count('<script src="app-nav.js"></script>') == 1, 'the strip is loaded exactly once')
    check(re.search(r'<body[^>]*>\n<script src="app-nav\.js"></script>', src), 'the strip loads right after <body>')
    check('drive-sync.js' not in src, 'the page has nothing to sync, so it loads no Drive code')
    check(named(src) == 0, 'the source names no person or company (%d found)' % named(src))


def home_row():
    idx = open(os.path.join(DOCS, 'index.html'), encoding='utf-8').read()
    rows = [r for r in re.findall(r'<li>.*?</li>', idx) if PAGE in r]
    if not check(len(rows) == 1, 'Home links the page exactly once (%d)' % len(rows)):
        return
    check('<a href="%s" target="_blank" rel="noopener">Steady Pace</a>' % PAGE in rows[0], 'the Home link opens a new tab')
    check(named(rows[0]) == 0, 'the Home row names no person or company')
    check(re.search(r'<section id="career".*?%s.*?</section>' % re.escape(PAGE), idx, re.S), 'the link sits in Career')


# ------------------------------------------------------------------ what the page says
def content(s):
    p = s.page
    s.open()
    for sid in SECTIONS:
        check(p.locator('#' + sid).count() == 1, 'section #%s exists' % sid)
    text = p.locator('main').inner_text()
    check(len(text) > 5000, 'the page has real text to search (%d characters)' % len(text))
    check(named(text) == 0, 'the rendered text names no person or company (%d found)' % named(text))
    for f in FACTS:
        check(f in text, 'the page still states %s' % f)
    for sid, words in (('targets', ('judgment', 'timesheet')), ('toomuch', ('14416', 'occupational phenomenon')),
                       ('day', ("what's left",)), ('protect', ('blam',))):
        t = p.locator('#' + sid).inner_text()
        for w in words:
            check(w in t, '#%s says "%s"' % (sid, w))
    check(p.locator('#guilt .pair').count() >= 10, 'at least 10 guilt pairs (%d)' % p.locator('#guilt .pair').count())

    sits = p.locator('.sit').count()
    phrases = p.locator('.phrase').count()
    check(sits >= 25, 'at least 25 situations (%d)' % sits)
    check(60 <= phrases <= 80, '60 to 80 phrases (%d)' % phrases)
    check(p.locator('.group').count() == 6, 'six groups of situations (%d)' % p.locator('.group').count())
    data = shown(p)
    texts = [d['text'] for d in data]
    check(len(texts) == len(set(texts)), 'no phrase appears twice')
    check(all(d['aud'] and set(d['aud']) <= set(AUDIENCES) for d in data), 'every phrase names a known audience')
    for a in AUDIENCES:
        n = sum(a in d['aud'] for d in data)
        check(n >= 5, 'at least 5 phrases for %s (%d)' % (a, n))
    bare = p.evaluate("""()=>[...document.querySelectorAll('.sit')].filter(e=>!e.querySelector('.sit-why')||
        !e.querySelector('.phrase')).map(e=>e.querySelector('.sit-title').textContent)""")
    check(bare == [], 'every situation has a reason and at least one phrase (%s)' % bare)
    bad = p.evaluate("""()=>[...document.querySelectorAll('.jump a')].filter(a=>!document.querySelector(a.getAttribute('href')))
        .map(a=>a.getAttribute('href'))""")
    check(p.locator('.jump a').count() >= 6 and bad == [], 'the jump links all land on a section (%s)' % bad)
    s.clean()


def sources(s):
    p = s.page
    s.open()
    links = p.evaluate("""()=>[...document.querySelectorAll('#sources a, #evidence a')].map(a=>({h:a.getAttribute('href'),
        t:a.target,r:a.rel}))""")
    check(len(links) >= 10, 'at least 10 source links (%d)' % len(links))
    check(all(l['h'].startswith('https://') for l in links), 'every source link is https')
    check(all(l['t'] == '_blank' and 'noopener' in l['r'] for l in links), 'every source link opens a new tab safely')
    items = p.locator('#sources li').all_inner_texts()
    undated = [i for i in items if not re.search(r'\b(19|20)\d\d\b', i)]
    check(items and undated == [], 'every source shows its year (%s)' % undated)
    s.clean()


def day_bar(s):
    p = s.page
    s.open()
    segs = p.evaluate("""()=>[...document.querySelectorAll('#dayBar .seg')].map(e=>({h:parseFloat(e.dataset.hours),
        w:e.getBoundingClientRect().width,n:e.dataset.name}))""")
    if not check(len(segs) == 3, 'three segments in the day bar (%d)' % len(segs)):
        return
    check(abs(sum(x['h'] for x in segs) - 8) < 0.01, 'the bar adds up to 8 hours')
    total = sum(x['w'] for x in segs)
    for x in segs:
        check(abs(x['w'] / total - x['h'] / 8) < 0.02, '%s is drawn to scale' % x['n'])
    p.locator('#dayBar .seg').first.hover()
    tip = p.locator('#vizTip')
    check(tip.is_visible() and segs[0]['n'] in tip.inner_text(), 'hovering a segment names it')
    p.mouse.move(0, 0)
    check(not tip.is_visible(), 'the tooltip leaves with the pointer')
    p.locator('#dayBar .seg').nth(1).focus()
    check(tip.is_visible() and segs[1]['n'] in tip.inner_text(), 'keyboard focus shows it as well')
    check(p.locator('#dayLegend li').count() == 3, 'the legend lists all three')
    s.clean()


# ------------------------------------------------------------------ what the page does
def filters(s):
    p = s.page
    s.open()
    total = len(shown(p))
    check(counted(p) == total, 'the count shows every phrase at first (%d vs %d)' % (counted(p), total))
    for a in AUDIENCES:
        p.locator('.audbtn[data-aud="%s"]' % a).click()
        now = shown(p)
        check(0 < len(now) < total, '%s narrows the list (%d of %d)' % (a, len(now), total))
        check(all(a in d['aud'] for d in now), 'only %s phrases are left' % a)
        check(counted(p) == len(now), 'the count follows the %s filter' % a)
        check(p.locator('.audbtn[data-aud="%s"]' % a).get_attribute('aria-pressed') == 'true', '%s is marked as chosen' % a)
        check(p.evaluate("()=>[...document.querySelectorAll('.sit')].filter(e=>e.offsetParent!==null&&!e.querySelector('.phrase:not([hidden])')).length") == 0,
              'a situation with nothing left for %s is hidden' % a)
    p.locator('.audbtn[data-aud="all"]').click()
    check(len(shown(p)) == total, 'All brings every phrase back')
    s.clean()


def search(s):
    p = s.page
    s.open()
    total = len(shown(p))
    box, empty = p.locator('#phraseSearch'), p.locator('#phraseEmpty')
    check(not empty.is_visible(), 'the empty message starts hidden')

    box.fill('stand behind')
    now = shown(p)
    check(len(now) == 1 and 'stand behind' in now[0]['text'], 'a phrase is found by its words (%d)' % len(now))
    check(counted(p) == len(now), 'the count follows the search')

    box.fill('LATE-NIGHT')
    now = shown(p)
    check(len(now) >= 2, 'a situation title finds all its phrases, in any case (%d)' % len(now))

    p.locator('.audbtn[data-aud="peer"]').click()
    box.fill('stand behind')
    check(shown(p) == [] and empty.is_visible(), 'search and audience combine, and an empty result says so')
    p.locator('.audbtn[data-aud="all"]').click()

    box.fill('zzqqxx')
    check(shown(p) == [] and empty.is_visible(), 'no match shows the empty message')
    check(p.evaluate("()=>[...document.querySelectorAll('.group')].filter(e=>e.offsetParent!==null).length") == 0,
          'groups with nothing to show are hidden')
    box.fill('')
    check(len(shown(p)) == total and not empty.is_visible(), 'clearing the search brings everything back')
    s.clean()


def copying(s):
    p = s.page
    s.open()
    first = p.locator('.phrase').first
    want = first.locator('.phrase-text').text_content()
    first.locator('button.copy').click()
    first.locator('button.copy', has_text='Copied').wait_for()  # the write is asynchronous: only read once it reports done
    check(p.evaluate('()=>navigator.clipboard.readText()') == want, 'Copy puts the exact phrase on the clipboard')

    slot = p.locator('.phrase', has_text='[name]').first
    want = slot.locator('.phrase-text').text_content()
    check('[name]' in want and slot.locator('.ph').count() >= 1, 'a blank to fill in is highlighted')
    slot.locator('button.copy').click()
    slot.locator('button.copy', has_text='Copied').wait_for()
    check(p.evaluate('()=>navigator.clipboard.readText()') == want, 'the blank is copied as plain text')
    s.clean()


def theme(s):
    p = s.page
    s.open()
    check(p.evaluate("()=>getComputedStyle(document.body).backgroundColor") == 'rgb(233, 236, 241)', 'light follows the device')
    p.locator('#themeBtn').click()
    check(p.evaluate("()=>document.documentElement.getAttribute('data-theme')") == 'dark', 'the button switches to dark')
    check(p.evaluate("()=>getComputedStyle(document.body).backgroundColor") == 'rgb(20, 23, 28)', 'the colours follow')
    p.reload()
    p.locator('#phraseList .phrase').first.wait_for()
    check(p.evaluate("()=>document.documentElement.getAttribute('data-theme')") == 'dark', 'the choice survives a reload')
    s.clean()


def no_storage(s):
    p = s.page
    s.open()
    check(p.locator('.phrase').count() >= 60, 'the page renders with browser storage blocked')
    p.locator('#themeBtn').click()
    s.clean()


def strip(s):
    p = s.page
    s.open()
    check(p.locator('.appnav').count() == 1, 'the strip is on the page')
    check(p.locator('.appnav a[href="./"]').count() == 1, 'the strip leads back to Home')
    s.clean()


def layouts(browser, base):
    for scheme in ('light', 'dark'):
        for w in (320, 360, 768, 1280):
            s = Session(browser, base, viewport=(w, 800), scheme=scheme)
            try:
                s.open()
                p = s.page
                over = p.evaluate('()=>document.documentElement.scrollWidth-window.innerWidth')
                check(over <= 0, 'no sideways scroll at %d px, %s (%d px over)' % (w, scheme, over))
                wide = p.evaluate("""()=>[...document.querySelectorAll('main *')].filter(e=>{const r=e.getBoundingClientRect();
                    return r.width>0&&r.right>window.innerWidth+1;}).slice(0,5).map(e=>e.tagName+'.'+e.className)""")
                check(wide == [], 'nothing sticks out at %d px, %s (%s)' % (w, scheme, wide))
                if SHOTS:
                    os.makedirs(SHOTS, exist_ok=True)
                    p.screenshot(path=os.path.join(SHOTS, 'pace-%s-%d.png' % (scheme, w)), full_page=True)
                s.clean()
            finally:
                s.close()


def main():
    run_section('source', source)
    run_section('home row', home_row)
    BLOCK = "Storage.prototype.getItem=function(){throw new Error('blocked')};Storage.prototype.setItem=function(){throw new Error('blocked')};"
    httpd, base = serve(DOCS)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='chrome', headless=not HEADED)
            for name, fn, kw in (('content', content, {}), ('sources', sources, {}), ('day bar', day_bar, {}),
                                 ('filters', filters, {}), ('search', search, {}), ('copy', copying, {}),
                                 ('theme', theme, {}), ('storage blocked', no_storage, {'init': BLOCK}), ('strip', strip, {})):
                s = Session(browser, base, **kw)
                try:
                    run_section(name, fn, s)
                finally:
                    s.close()
            run_section('layouts', layouts, browser, base)
            browser.close()
    finally:
        httpd.shutdown()
    print('\n%d passed, %d failed' % (PASSES[0], len(FAILS)))
    for f in FAILS:
        print('  ', f)
    sys.exit(1 if FAILS else 0)


if __name__ == '__main__':
    main()
