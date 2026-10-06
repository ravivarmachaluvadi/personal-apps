"""Spine Bell checker: drives the work / break timer in headless Chrome, signed out, on a fake clock.

  python tools/check-spine-bell.py            # docs/spine-bell.html (built), then site/spine-bell.html
  python tools/check-spine-bell.py --headed   # watch it

Nothing here can reach Google. The page is never signed in, and every request that leaves the local
server is aborted; any host other than Google Fonts is reported as a failure. Time stands still until
the checker moves it: a long wait is a jump (clock.fast_forward, like opening a laptop lid), because playing
every 250 ms tick of a 30-minute block costs about 90 s of real time.

Exit code 0 means every check passed; each failure is printed with the section it came from.
"""
import datetime, functools, http.server, os, re, socket, sys, threading, urllib.parse
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # the Windows console is cp1252; page text has middle dots
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEADED = '--headed' in sys.argv
PAGE = 'spine-bell.html'
FAILS, PASSES = [], [0]
SECTION = ['']
T0 = datetime.datetime(2026, 10, 6, 9, 0, 0)  # a morning, so nothing below crosses midnight
MIN = 60000


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


# counts the tones the page schedules and the vibrations it asks for: headless Chrome plays no sound,
# so "the bell rang" is checked as "oscillators were started"
TIMER_INIT = """(()=>{window.__tones=0;window.__buzz=0;var C=window.AudioContext||window.webkitAudioContext;
  if(C){var o=C.prototype.createOscillator;C.prototype.createOscillator=function(){window.__tones++;return o.apply(this,arguments);};}
  Object.defineProperty(navigator,'vibrate',{value:function(){window.__buzz++;return true;},configurable:true});})()"""


class Session:
    """One fresh browser context (empty localStorage) on a paused fake clock; everything off the local server is aborted."""

    def __init__(self, browser, base, viewport=(1280, 800)):
        self.blocked, self.errors = [], []
        self.ctx = browser.new_context(viewport={'width': viewport[0], 'height': viewport[1]})
        self.ctx.route('**/*', self.route)
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(6000)
        self.page.on('pageerror', lambda e: self.errors.append('pageerror: ' + str(e)))
        self.page.on('console', self.console)
        self.page.add_init_script(TIMER_INIT)
        self.page.clock.install(time=T0)
        self.page.clock.pause_at(T0 + datetime.timedelta(seconds=1))
        self.base = base

    def console(self, msg):
        if msg.type == 'error' and not re.search(r'Failed to load resource|net::ERR_|ServiceWorker|bad HTTP response code', msg.text):
            self.errors.append('console: ' + msg.text[:300])

    def route(self, route, req):
        u = urllib.parse.urlsplit(req.url)
        if (u.hostname or '') in ('127.0.0.1', 'localhost') or u.scheme in ('data', 'blob'):
            return route.continue_()
        if not re.match(r'https://fonts\.(googleapis|gstatic)\.com/', req.url):
            self.blocked.append(req.url)
        return route.abort()

    def finish(self):
        SECTION[0] += ': browser'
        for e in self.errors:
            check(False, 'browser error: ' + e)
        check(not self.blocked, 'no request to an unexpected host: %s' % self.blocked[:3])
        self.ctx.close()

    # ---- page helpers
    def open(self):
        self.page.goto(self.base + PAGE)
        self.page.locator('#primaryBtn').wait_for()

    def reload(self):
        self.page.wait_for_timeout(200)
        self.page.reload()
        self.page.locator('#resetBtn').wait_for()  # the big button is hidden while work runs; Reset never is

    def run(self, ms):
        """plays every timer in the next ms: for short spans, where a repeating bell must be heard (or not)"""
        self.page.clock.run_for(ms)

    def jump(self, ms):
        """moves the clock forward, firing each due timer at most once"""
        self.page.clock.fast_forward(ms)

    def txt(self, sel):
        loc = self.page.locator(sel)
        return loc.inner_text().strip() if loc.count() else ''

    def shown(self, sel):
        loc = self.page.locator(sel)
        return loc.count() > 0 and loc.is_visible()

    def click(self, sel):
        self.page.locator(sel).click()

    def mode(self):
        return self.page.evaluate('document.body.dataset.mode')

    def stood(self):
        return int(self.txt('#sStood') or -1)

    def tones(self):
        return self.page.evaluate('window.__tones')

    def label(self):
        # textContent, not inner_text: the small line is upper-cased by CSS
        return ' '.join(self.page.evaluate("document.querySelector('#modeLabel').textContent").split())


# ------------------------------------------------------------------ the checks
def idle_buttons(s):
    s.open()
    check(s.mode() == 'idle', 'a fresh page is idle (%r)' % s.mode())
    check(s.txt('#primaryBtn') == 'Start work', 'the big button says Start work (%r)' % s.txt('#primaryBtn'))
    check(s.shown('#breakBtn'), 'idle on Back care shows a break button')
    check(s.txt('#breakBtn') == 'Break · 3 min', 'it says Break · 3 min (%r)' % s.txt('#breakBtn'))
    check(s.shown('#longBreakBtn'), 'idle on Back care shows a long break button')
    check(s.txt('#longBreakBtn') == 'Long break · 10 min', 'it says Long break · 10 min (%r)' % s.txt('#longBreakBtn'))
    s.click('.pill[data-preset="timer"]')
    check(not s.shown('#breakBtn') and not s.shown('#longBreakBtn'), 'Just a timer hides both break buttons')
    s.click('.pill[data-preset="pomo"]')
    check(s.txt('#breakBtn') == 'Break · 5 min' and s.txt('#longBreakBtn') == 'Long break · 15 min',
          'Pomodoro relabels them 5 and 15 min (%r, %r)' % (s.txt('#breakBtn'), s.txt('#longBreakBtn')))
    s.page.locator('#fBreak').fill('4')
    s.page.locator('#fBreak').press('Tab')
    check(s.txt('#breakBtn') == 'Break · 4 min', 'changing Break min to 4 relabels the button (%r)' % s.txt('#breakBtn'))


def solo_break(s):
    s.open()
    s.click('#breakBtn')
    check(s.mode() == 'break', 'Break · 3 min starts a break on its own (%r)' % s.mode())
    check(s.txt('#digits') == '03:00', 'it counts down from 03:00 (%r)' % s.txt('#digits'))
    check(s.stood() == 1, 'starting a break counts one stand-up (%d)' % s.stood())
    check('after block' not in s.label() and 'Break' in s.label(), 'the label is a plain Break, not after a block (%r)' % s.label())
    check(s.shown('#pauseBtn'), 'the break can be paused')
    check(not s.shown('#breakBtn') and not s.shown('#longBreakBtn'), 'the break buttons hide while a break runs')
    s.jump(MIN)
    check(s.txt('#digits') == '02:00', 'a minute later it reads 02:00 (%r)' % s.txt('#digits'))
    t = s.tones()
    s.jump(2 * MIN + 500)
    check(s.mode() == 'break-done', 'at 0:00 the break is over (%r)' % s.mode())
    check(s.tones() > t, 'the bell rings at the end of the break')
    check(s.txt('#primaryBtn') == 'Back to work · block 1', 'a lone break leads back to block 1, not 2 (%r)' % s.txt('#primaryBtn'))
    s.click('#primaryBtn')
    check(s.mode() == 'work' and 'Block 1' in s.label(), 'back to work starts block 1 (%r, %r)' % (s.mode(), s.label()))
    s.click('#resetBtn')
    check(s.mode() == 'idle' and 'Block 1' in s.label(), 'Reset block goes back to idle on block 1 (%r)' % s.label())
    s.click('#longBreakBtn')
    check(s.mode() == 'break' and s.txt('#digits') == '10:00', 'Long break starts at 10:00 (%r)' % s.txt('#digits'))
    check('Long break' in s.label(), 'the label says Long break (%r)' % s.label())
    check(s.txt('#stretchName') == 'Long break: walk first', 'the card says walk first (%r)' % s.txt('#stretchName'))
    check(s.stood() == 2, 'a second break counts a second stand-up (%d)' % s.stood())
    s.click('#primaryBtn')
    check(s.mode() == 'work' and 'Block 1' in s.label(), 'ending a lone break early still goes to block 1 (%r)' % s.label())
    s.reload()
    check(s.mode() == 'work' and 'Block 1' in s.label(), 'and a reload keeps it on block 1 (%r)' % s.label())


def work_then_wait(s):
    s.open()
    s.click('#primaryBtn')
    check(s.mode() == 'work', 'Start work starts block 1 (%r)' % s.mode())
    s.jump(30 * MIN + 500)
    check(s.mode() == 'work-done', 'after 30 min the work bell rings (%r)' % s.mode())
    check(s.txt('#primaryBtn') == 'Stop the bell', 'the big button says Stop the bell (%r)' % s.txt('#primaryBtn'))
    check(s.txt('#secondaryBtn') == 'Snooze 2 min', 'snooze is still offered (%r)' % s.txt('#secondaryBtn'))
    check(s.stood() == 0, 'the bell alone counts no stand-up (%d)' % s.stood())
    s.click('#primaryBtn')
    check(s.mode() == 'break-ready', 'stopping the bell does not start the break (%r)' % s.mode())
    t = s.tones()
    s.run(20000)
    check(s.tones() == t, 'the bell stays quiet once stopped (%d more tones)' % (s.tones() - t))
    check(s.txt('#digits') == '03:00', 'the waiting screen shows the 03:00 break (%r)' % s.txt('#digits'))
    s.jump(MIN)
    check(s.txt('#digits') == '03:00', 'a minute later it still shows 03:00: nothing counts down (%r)' % s.txt('#digits'))
    check(s.stood() == 0, 'waiting counts no stand-up (%d)' % s.stood())
    check(s.txt('#primaryBtn') == 'Start break · 3 min', 'the big button says Start break · 3 min (%r)' % s.txt('#primaryBtn'))
    check(s.txt('#secondaryBtn') == 'Skip break · block 2', 'a second button skips to block 2 (%r)' % s.txt('#secondaryBtn'))
    check(s.shown('#stretchCard'), 'the stretch card shows while waiting')
    check(not s.shown('#pauseBtn'), 'there is nothing to pause while waiting')
    check(not s.shown('#breakBtn') and not s.shown('#longBreakBtn'), 'the idle break buttons stay hidden while waiting')

    s.reload()
    check(s.mode() == 'break-ready', 'a reload keeps the break waiting (%r)' % s.mode())
    check('ended while this page was closed' not in s.txt('#status'), 'and does not claim a timer ended (%r)' % s.txt('#status'))
    check(s.txt('#digits') == '03:00', 'still 03:00 after the reload (%r)' % s.txt('#digits'))

    s.click('#primaryBtn')
    check(s.mode() == 'break', 'Start break starts the countdown (%r)' % s.mode())
    check(s.stood() == 1, 'starting the break counts the stand-up (%d)' % s.stood())
    check('after block 1' in s.label(), 'the label says after block 1 (%r)' % s.label())
    s.jump(MIN)
    check(s.txt('#digits') == '02:00', 'a minute into the break it reads 02:00 (%r)' % s.txt('#digits'))
    s.jump(2 * MIN + 500)
    check(s.txt('#primaryBtn') == 'Back to work · block 2', 'the break leads to block 2 (%r)' % s.txt('#primaryBtn'))
    s.click('#primaryBtn')
    check(s.mode() == 'work' and 'Block 2' in s.label(), 'block 2 runs (%r)' % s.label())

    s.jump(30 * MIN + 500)
    s.click('#primaryBtn')
    check(s.mode() == 'break-ready', 'block 2 also ends waiting (%r)' % s.mode())
    s.click('#secondaryBtn')
    check(s.mode() == 'work' and 'Block 3' in s.label(), 'Skip break starts block 3 at once (%r, %r)' % (s.mode(), s.label()))
    check(s.stood() == 1, 'a skipped break counts no stand-up (%d)' % s.stood())

    s.jump(30 * MIN + 500)
    s.click('#primaryBtn')
    check(s.mode() == 'break-ready' and s.txt('#digits') == '03:00', 'block 3 waits on a 03:00 break (%r)' % s.txt('#digits'))
    s.click('#resetBtn')
    check(s.mode() == 'idle' and 'Block 4' in s.label(), 'Reset while waiting goes to idle on block 4 (%r)' % s.label())

    s.click('#primaryBtn')
    s.jump(30 * MIN + 500)
    s.click('#primaryBtn')
    check(s.mode() == 'break-ready', 'block 4 ends waiting (%r)' % s.mode())
    check(s.txt('#digits') == '10:00', 'block 4 waits on the 10:00 long break (%r)' % s.txt('#digits'))
    check(s.txt('#primaryBtn') == 'Start break · 10 min', 'the big button says Start break · 10 min (%r)' % s.txt('#primaryBtn'))
    check(s.txt('#stretchName') == 'Long break: walk first', 'the card says walk first (%r)' % s.txt('#stretchName'))
    s.click('#primaryBtn')
    check(s.txt('#digits') == '10:00' and 'Long break after block 4' in s.label(), 'the long break runs (%r)' % s.label())
    check(s.stood() == 2, 'two breaks taken, two stand-ups (%d)' % s.stood())


def snooze_still_works(s):
    s.open()
    s.click('#primaryBtn')
    s.jump(30 * MIN + 500)
    s.click('#secondaryBtn')
    check(s.mode() == 'work' and 'snoozed' in s.label(), 'Snooze 2 min gives two more minutes of work (%r)' % s.label())
    s.jump(2 * MIN + 500)
    check(s.mode() == 'work-done', 'the bell rings again after the snooze (%r)' % s.mode())
    s.click('#primaryBtn')
    check(s.mode() == 'break-ready', 'stopping it waits for the break (%r)' % s.mode())
    check(s.stood() == 0, 'no stand-up yet (%d)' % s.stood())


def layout(s):
    s.open()
    w = s.page.evaluate('innerWidth')

    def fits(what):
        sw = s.page.evaluate('document.documentElement.scrollWidth')
        check(sw <= w, '%s fits %d px without sideways scroll (%d)' % (what, w, sw))
        out = s.page.evaluate("""(w)=>[...document.querySelectorAll('.actions button')].filter(b=>b.offsetParent)
            .filter(b=>{var r=b.getBoundingClientRect();return r.left<0||r.right>w;}).map(b=>b.id)""", w)
        check(not out, '%s: every button is on screen (%s)' % (what, out))

    fits('idle, four buttons')
    s.click('#primaryBtn')
    s.jump(30 * MIN + 500)
    s.click('#primaryBtn')
    fits('the waiting screen')


def run_target(browser, label, directory):
    if not os.path.exists(os.path.join(directory, PAGE)):
        SECTION[0] = label
        check(False, '%s does not exist' % os.path.join(os.path.basename(directory), PAGE))
        return
    httpd, base = serve(directory)
    try:
        print('==', label)
        for name, fn, vp in (('idle buttons', idle_buttons, None), ('a break on its own', solo_break, None),
                             ('work, then a break you start', work_then_wait, None), ('snooze', snooze_still_works, None),
                             ('layout 390px', layout, (390, 844))):
            s = Session(browser, base, viewport=vp or (1280, 800))
            try:
                run_section('%s %s' % (label, name), fn, s)
            finally:
                s.finish()
    finally:
        httpd.shutdown()


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=not HEADED)
        run_target(browser, 'docs/', os.path.join(ROOT, 'docs'))
        run_target(browser, 'site/', os.path.join(ROOT, 'site'))
        browser.close()
    print('\n%d checks passed, %d failed' % (PASSES[0], len(FAILS)))
    for x in FAILS:
        print('  ' + x)
    sys.exit(1 if FAILS else 0)


if __name__ == '__main__':
    main()
