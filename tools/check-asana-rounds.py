"""Asana Rounds checker: drives the page in headless Chrome, signed out.

  python tools/check-asana-rounds.py            # docs/asana-rounds.html (built), then site/asana-rounds.html
  python tools/check-asana-rounds.py --headed   # watch it
  ASANA_SHOTS=<folder> python tools/check-asana-rounds.py   # also save a screenshot of each layout

Nothing here can reach Google. The page is never signed in, and every request that leaves the local
server is aborted; any host other than Google Fonts is reported as a failure. The built page runs on
its browser cache (drive-sync.js signed out), the source page on its browser-only fallback store.

Exit code 0 means every check passed; each failure is printed with the section it came from.
"""
import datetime, functools, http.server, os, re, socket, sys, threading, urllib.parse
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # the Windows console is cp1252; page text has arrows and ticks
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEADED = '--headed' in sys.argv
PAGE = 'asana-rounds.html'
POSES = ['leftfold', 'rightfold', 'leftraise', 'rightraise', 'd45', 'slpbackbend', 's2d45', 'cobra', 'sitbackbend', 'plank']
NAMES = ['Left Fold', 'Right Fold', 'Left Raise', 'Right Raise', '45D', 'SLP BackBend', '2Sd45', 'Cobra', 'Sit BackBend', 'Plank']
FAILS, PASSES = [], [0]
SECTION = ['']
# the page picks Morning or Evening by the hour, so every run happens at a fixed time on a fixed day
DAY0 = datetime.date(2026, 10, 5)


def at(day, hh, mm=0, ss=0):
    return datetime.datetime.combine(DAY0 + datetime.timedelta(days=day), datetime.time(hh, mm, ss))


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

    def __init__(self, browser, base, viewport=(1280, 800)):
        self.blocked, self.errors = [], []
        self.ctx = browser.new_context(viewport={'width': viewport[0], 'height': viewport[1]})
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
        if (u.hostname or '') in ('127.0.0.1', 'localhost') or u.scheme in ('data', 'blob'):
            return route.continue_()
        if not re.match(r'https://fonts\.(googleapis|gstatic)\.com/', req.url):
            self.blocked.append(req.url)
        return route.abort()

    def close(self):
        self.ctx.close()

    # ---- page helpers
    def open(self):
        self.page.goto(self.base + PAGE)
        self.page.locator('#doneBtn').wait_for()

    def txt(self, sel):
        return self.page.locator(sel).inner_text().strip()

    def pressed(self, r, pose):
        return self.page.locator('#grid .cell[data-round="%d"][data-pose="%s"]' % (r, pose)).get_attribute('aria-pressed') == 'true'

    def ticked(self):
        return self.page.locator('#grid .cell[aria-pressed="true"]').count()

    def done(self, n):
        for _ in range(n):
            self.page.locator('#doneBtn').click()

    def part(self):
        return 'am' if self.page.locator('#partAm').get_attribute('aria-pressed') == 'true' else (
            'pm' if self.page.locator('#partPm').get_attribute('aria-pressed') == 'true' else '?')

    def today(self, offset=0):
        return self.page.evaluate("""(o)=>{var d=new Date();d.setDate(d.getDate()+o);
            var p=n=>(n<10?'0':'')+n;return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate());}""", offset)

    def dot(self, day, part):
        return self.page.locator('#history li[data-day="%s"] .dot[data-part="%s"]' % (day, part)).get_attribute('data-state')


# ------------------------------------------------------------------ the checks
def flow(s):
    p = s.page
    real = {}

    def fresh():
        p.clock.set_system_time(at(0, 8))  # 8 am; the clock runs on from there
        s.open()
        names = [x.strip() for x in p.locator('#grid .gr .gname').all_inner_texts()]
        check(names == NAMES, 'the routine starts with your 10 asanas in order (%s)' % names)
        rows = p.locator('#grid .gr').evaluate_all('rs=>rs.map(r=>r.getAttribute("data-pose"))')
        check(rows == POSES, 'grid rows carry the expected asana ids (%s)' % rows)
        check(p.locator('#grid .gh[data-round]').count() == 5, 'the grid has 5 round columns')
        check(s.txt('#nowRound') == 'Round 1 of 5', 'fresh page says Round 1 of 5 (%r)' % s.txt('#nowRound'))
        check(s.txt('#nowPose') == 'Left Fold', 'fresh page is on Left Fold (%r)' % s.txt('#nowPose'))
        check(s.txt('#nowPos') == '1 of 10', 'position reads 1 of 10 (%r)' % s.txt('#nowPos'))
        check('Right Fold' in s.txt('#nowNext'), 'next is Right Fold (%r)' % s.txt('#nowNext'))
        check(s.part() == 'am', 'the page opens on Morning')
        check(s.ticked() == 0, 'nothing ticked yet')
        check(p.locator('#undoBtn').is_disabled(), 'Undo is disabled with nothing to undo')
        check(p.locator('#grid .cell.cur[data-round="1"][data-pose="leftfold"]').count() == 1, 'the current cell is highlighted')
        real['today'] = s.today()

    def ten():
        s.done(10)
        check(s.txt('#nowRound') == 'Round 2 of 5', 'after 10 Done: Round 2 of 5 (%r)' % s.txt('#nowRound'))
        check(s.txt('#nowPose') == 'Left Fold', 'after 10 Done: back to Left Fold (%r)' % s.txt('#nowPose'))
        check(all(s.pressed(1, x) for x in POSES), 'round 1 is fully ticked')
        check(not any(s.pressed(2, x) for x in POSES), 'round 2 is untouched')
        check('Round 1 done' in s.txt('#doneNote'), 'a note says Round 1 done (%r)' % s.txt('#doneNote'))
        check('1/5' in s.txt('#partAm'), 'Morning shows 1/5 (%r)' % s.txt('#partAm'))

    def undo():
        p.locator('#undoBtn').click()
        check(s.txt('#nowRound') == 'Round 1 of 5', 'Undo goes back to round 1 (%r)' % s.txt('#nowRound'))
        check(s.txt('#nowPose') == 'Plank', 'Undo goes back to Plank (%r)' % s.txt('#nowPose'))
        check(not s.pressed(1, 'plank'), 'Plank in round 1 is unticked again')
        check(s.ticked() == 9, '9 ticks remain (%d)' % s.ticked())

    def all_fifty():
        s.done(41)
        check(s.ticked() == 50, 'all 50 cells ticked (%d)' % s.ticked())
        check(p.locator('#nowCard').get_attribute('data-state') == 'complete', 'the now card shows the session as complete')
        check('Morning done' in s.txt('#nowCard'), 'it says Morning done (%r)' % s.txt('#nowCard')[:120])
        check('5/5' in s.txt('#partAm'), 'Morning shows 5/5 (%r)' % s.txt('#partAm'))
        check('evening' in s.txt('#doneBtn').lower(), 'the big button now leads to the evening (%r)' % s.txt('#doneBtn'))
        p.locator('#doneBtn').click()
        check(s.part() == 'pm', 'pressing it switches to Evening')
        check(s.txt('#nowRound') == 'Round 1 of 5' and s.txt('#nowPose') == 'Left Fold', 'evening starts at Round 1, Left Fold')
        check(s.ticked() == 0, 'the evening grid is empty')

    def reload():
        p.wait_for_timeout(300)
        p.reload()
        p.locator('#doneBtn').wait_for()
        check(s.part() == 'am', 'at 8 am, with the morning finished, a reload still opens on Morning (%r)' % s.part())
        check('Morning done' in s.txt('#nowCard') and 'evening' in s.txt('#doneBtn').lower(),
              'it shows Morning done and the way to the evening (%r)' % s.txt('#doneBtn'))
        p.clock.set_system_time(at(0, 13))
        p.reload()
        p.locator('#doneBtn').wait_for()
        check(s.part() == 'pm', 'at 1 pm a reload opens on Evening (%r)' % s.part())
        check('5/5' in s.txt('#partAm'), 'Morning still shows 5/5 after reload (%r)' % s.txt('#partAm'))
        s.done(3)
        p.wait_for_timeout(300)
        p.reload()
        p.locator('#doneBtn').wait_for()
        check(s.txt('#nowPose') == 'Right Raise', 'evening progress survives a reload: 3 done, on the 4th (%r)' % s.txt('#nowPose'))
        p.locator('#partAm').click()
        check(s.part() == 'am' and s.ticked() == 50, 'switching to Morning shows its 50 ticks (%d)' % s.ticked())
        p.locator('#partPm').click()

    def tomorrow():
        tom = s.today(1)
        p.evaluate("(d)=>localStorage.setItem('asanarounds.faketoday',d)", tom)
        p.clock.set_system_time(at(1, 8))  # the next morning, so the new day opens on Morning by the clock too
        p.reload()
        p.locator('#doneBtn').wait_for()
        check(not p.locator('#fakeBanner').is_hidden(), 'a banner says the date is pretend')
        check(s.part() == 'am', 'a new day opens on Morning')
        check(s.txt('#nowRound') == 'Round 1 of 5' and s.txt('#nowPose') == 'Left Fold', 'a new day starts at Round 1, Left Fold')
        check(s.ticked() == 0, 'a new day has nothing ticked')
        check('0/5' in s.txt('#partAm') and '0/5' in s.txt('#partPm'), 'both sessions show 0/5 on the new day')
        check(p.locator('#history li[data-day]').count() == 14, '14 days in the history strip (%d)' % p.locator('#history li[data-day]').count())
        y = real['today']
        check(s.dot(y, 'am') == 'full', 'yesterday morning shows full (%r)' % s.dot(y, 'am'))
        check(s.dot(y, 'pm') == 'part', 'yesterday evening shows partly done (%r)' % s.dot(y, 'pm'))
        check(s.dot(tom, 'am') == 'none' and s.dot(tom, 'pm') == 'none', 'today shows nothing yet')

    def out_of_order():
        p.locator('#grid .cell[data-round="1"][data-pose="rightfold"]').click()
        check(s.pressed(1, 'rightfold'), 'tapping a cell ticks it')
        check(s.txt('#nowPose') == 'Left Fold', 'current stays on the first unticked cell, Left Fold (%r)' % s.txt('#nowPose'))
        p.locator('#grid .cell[data-round="1"][data-pose="leftfold"]').click()
        check(s.txt('#nowPose') == 'Left Raise' and s.txt('#nowPos') == '3 of 10', 'after filling the gap, current is Left Raise, 3 of 10')
        p.locator('#grid .cell[data-round="1"][data-pose="rightfold"]').click()
        check(not s.pressed(1, 'rightfold'), 'tapping a ticked cell unticks it')
        check(s.txt('#nowPose') == 'Right Fold', 'current moves back to the gap, Right Fold (%r)' % s.txt('#nowPose'))
        p.locator('#grid .cell[data-round="1"][data-pose="rightfold"]').click()

    def edit():
        p.locator('#editRoutine > summary').click()
        inp = p.locator('#editRoutine .prow[data-pose="cobra"] input.pname')
        inp.fill('Cobra Hold')
        inp.press('Enter')
        p.wait_for_timeout(100)
        names = [x.strip() for x in p.locator('#grid .gr .gname').all_inner_texts()]
        check('Cobra Hold' in names, 'a renamed asana shows its new name in the grid')
        check(s.pressed(1, 'leftfold') and s.pressed(1, 'rightfold'), 'ticks survive a rename')
        p.locator('#roundsSel').select_option('3')
        p.wait_for_timeout(100)
        check(s.txt('#nowRound') == 'Round 1 of 3', 'rounds set to 3: Round 1 of 3 (%r)' % s.txt('#nowRound'))
        check('0/3' in s.txt('#partAm'), 'Morning shows 0/3 (%r)' % s.txt('#partAm'))
        check(p.locator('#grid .gh[data-round]').count() == 3, 'the grid has 3 round columns')
        # adding and moving an asana
        p.locator('#addName').fill('Child Pose')
        p.locator('#addBtn').click()
        names = [x.strip() for x in p.locator('#grid .gr .gname').all_inner_texts()]
        check(names[-1:] == ['Child Pose'], 'an added asana goes to the end (%s)' % names[-2:])
        row = p.locator('#editRoutine .prow').last
        row.locator('button.up').click()
        names = [x.strip() for x in p.locator('#grid .gr .gname').all_inner_texts()]
        check(names[-2:] == ['Child Pose', 'Plank'], 'Up moves it one place earlier (%s)' % names[-2:])
        # removing asks first
        rm = p.locator('#editRoutine .prow').nth(len(names) - 2).locator('button.rm')
        rm.click()
        check(p.locator('#grid .gr').count() == len(names), 'one tap on remove does not remove yet')
        check('?' in rm.inner_text(), 'the remove button asks to confirm (%r)' % rm.inner_text())
        rm.click()
        names2 = [x.strip() for x in p.locator('#grid .gr .gname').all_inner_texts()]
        check('Child Pose' not in names2 and len(names2) == 10, 'the second tap removes it (%s)' % names2[-2:])
        # yesterday keeps the round count it was done with
        y = real['today']
        check(s.dot(y, 'am') == 'full', 'yesterday morning still shows full after changing rounds (%r)' % s.dot(y, 'am'))

    def start_over():
        before = s.ticked()
        check(before >= 2, 'there are ticks to clear (%d)' % before)
        check(p.locator('#overAsk').is_hidden(), 'the confirm row is hidden at first')
        p.locator('#overBtn').click()
        check(p.locator('#overAsk').is_visible(), 'Start over asks first')
        check(s.ticked() == before, 'asking clears nothing')
        p.locator('#overNo').click()
        check(p.locator('#overAsk').is_hidden() and s.ticked() == before, 'Keep them keeps the ticks')
        p.locator('#overBtn').click()
        p.locator('#overYes').click()
        check(s.ticked() == 0, 'confirming clears this session (%d left)' % s.ticked())
        check(s.txt('#nowPose') == 'Left Fold', 'and the page is back at Left Fold')
        check(s.dot(real['today'], 'am') == 'full', 'other days are untouched')
        p.evaluate("()=>localStorage.removeItem('asanarounds.faketoday')")

    def theme():
        dark0 = p.evaluate("matchMedia('(prefers-color-scheme: dark)').matches")
        p.locator('#themeBtn').click()
        t = p.evaluate("document.documentElement.getAttribute('data-theme')")
        check(t == ('light' if dark0 else 'dark'), 'the sun/moon button switches the theme (%r)' % t)
        p.reload()
        p.locator('#doneBtn').wait_for()
        check(p.evaluate("document.documentElement.getAttribute('data-theme')") == t, 'the theme choice survives a reload')
        p.locator('#themeBtn').click()
        check(p.evaluate("document.documentElement.getAttribute('data-theme')") is None, 'switching back returns to Auto')

    for name, fn in [('fresh page', fresh), ('ten Done', ten), ('undo', undo), ('whole morning', all_fifty),
                     ('reload', reload), ('next day', tomorrow), ('grid taps', out_of_order),
                     ('edit routine', edit), ('start over', start_over), ('theme', theme)]:
        run_section(name, fn)


# counts the tones the page schedules and the vibrations it asks for: headless Chrome plays no sound,
# so "the sound happened" is checked as "oscillators were started"
TIMER_INIT = """(()=>{window.__tones=0;window.__buzz=0;var C=window.AudioContext||window.webkitAudioContext;
  if(C){var o=C.prototype.createOscillator;C.prototype.createOscillator=function(){window.__tones++;return o.apply(this,arguments);};}
  Object.defineProperty(navigator,'vibrate',{value:function(){window.__buzz++;return true;},configurable:true});})()"""
CHIPS = ['0:30', '0:45', '1:00', '1:04', 'Custom']


def timer_flow(browser, base, label):
    """The hold timer, on a fake clock: time stands still until clock.run_for moves it."""
    s = Session(browser, base)
    p = s.page
    p.add_init_script(TIMER_INIT)
    p.clock.install(time=at(0, 9))  # a morning: everything below happens well before the 12:00 switch
    p.clock.pause_at(at(0, 9, 0, 1))
    real = {}

    def run(ms):
        p.clock.run_for(ms)

    def left():
        return s.txt('#tLeft')

    def state():
        return p.locator('#timer').get_attribute('data-state')

    def tones():
        return p.evaluate('window.__tones')

    def chip(sec):
        return p.locator('#tPresets .chip[data-sec="%s"]' % sec)

    def custom(m, sec, enter=False):
        p.locator('#tCustomBtn').click()
        check(p.locator('#tCustom').is_visible(), 'Custom opens a minutes and seconds row')
        p.locator('#tMin').fill(m)
        p.locator('#tSec').fill(sec)
        if enter:
            p.locator('#tSec').press('Enter')
        else:
            p.locator('#tSet').click()

    def reopen():
        p.reload()
        p.locator('#doneBtn').wait_for()

    def fresh():
        s.open()
        check(state() == 'idle', 'the timer starts stopped (%r)' % state())
        check(left() == '0:30', 'an asana with no length set gets 0:30 (%r)' % left())
        labels = [x.strip() for x in p.locator('#tPresets .chip').all_inner_texts()]
        check(labels == CHIPS, 'the length chips are %s (%s)' % (CHIPS, labels))
        check(chip(30).get_attribute('aria-pressed') == 'true', '0:30 is the highlighted chip')
        check(chip(64).get_attribute('aria-pressed') == 'false', '1:04 is not highlighted')
        check(s.txt('#tGo') == 'Start', 'the timer button says Start (%r)' % s.txt('#tGo'))
        check(p.locator('#tCustom').is_hidden(), 'the custom row is closed at first')

    def per_asana():
        chip(64).click()
        check(left() == '1:04', 'tapping 1:04 sets the timer to 1:04 (%r)' % left())
        check(chip(64).get_attribute('aria-pressed') == 'true' and chip(30).get_attribute('aria-pressed') == 'false',
              'the 1:04 chip is now the highlighted one')
        reopen()
        check(left() == '1:04', 'Left Fold keeps 1:04 after a reload (%r)' % left())
        p.locator('#doneBtn').click()
        check(s.txt('#nowPose') == 'Right Fold' and left() == '0:30', 'Right Fold has its own length, 0:30 (%r)' % left())
        check(state() == 'idle', 'Done without using the timer does not start one (%r)' % state())
        p.locator('#undoBtn').click()
        check(s.txt('#nowPose') == 'Left Fold' and left() == '1:04', 'back on Left Fold it reads 1:04 again (%r)' % left())

    def countdown():
        p.locator('#tGo').click()
        check(state() == 'run' and s.txt('#tGo') == 'Pause', 'Start runs the timer and becomes Pause')
        run(10000)
        check(left() == '0:54', '10 s later it reads 0:54 (%r)' % left())
        check('0:54' in p.title() and 'Left Fold' in p.title(), 'the tab title shows the countdown (%r)' % p.title())
        p.locator('#tGo').click()
        check(state() == 'paused' and s.txt('#tGo') == 'Resume', 'Pause stops it and becomes Resume')
        run(5000)
        check(left() == '0:54', 'paused for 5 s it still reads 0:54 (%r)' % left())
        p.locator('#tGo').click()
        check(state() == 'run', 'Resume runs it again')
        run(50000)
        check(left() == '0:04', '50 s more and it reads 0:04 (%r)' % left())
        t = tones()
        run(3500)
        check(left() == '0:01' and state() == 'run', 'still running at 0:01 (%r)' % left())
        check(tones() >= t + 3, 'the last 3 seconds beep (%d tones)' % (tones() - t))
        t, b = tones(), p.evaluate('window.__buzz')
        run(1000)
        check(state() == 'up', 'at zero the timer is up (%r)' % state())
        check(left() == 'Time up', 'it reads Time up (%r)' % left())
        check(tones() >= t + 8, 'time up plays the chime (%d tones)' % (tones() - t))
        check(p.evaluate('window.__buzz') > b, 'time up vibrates the phone')
        check(s.ticked() == 0, 'time up ticks nothing by itself (%d ticked)' % s.ticked())
        check('Time up' in s.txt('#doneNote'), 'the note says Time up (%r)' % s.txt('#doneNote'))
        real['up'] = tones()

    def ring_stops():
        run(20000)
        a = tones()
        check(a >= real['up'] + 8, 'the chime repeats while nobody taps (%d more tones)' % (a - real['up']))
        run(10000)
        check(tones() == a, 'and stops by itself (%d more after 20 s)' % (tones() - a))

    def done_waits():
        p.locator('#doneBtn').click()
        check(s.pressed(1, 'leftfold'), 'Done after time up ticks Left Fold')
        check(s.txt('#nowPose') == 'Right Fold', 'and moves on to Right Fold')
        check(state() == 'idle' and left() == '0:30', 'Right Fold\'s 0:30 timer waits, stopped (%r, %r)' % (state(), left()))
        check(s.txt('#tGo') == 'Start', 'its button says Start (%r)' % s.txt('#tGo'))
        check(p.title() == 'Asana Rounds', 'the tab title is plain again (%r)' % p.title())
        t = tones()
        run(10000)
        check(state() == 'idle' and left() == '0:30', '10 s later it is still waiting at 0:30 (%r, %r)' % (state(), left()))
        check(tones() == t, 'and nothing has played (%d tones)' % (tones() - t))
        p.locator('#tGo').click()
        run(5000)
        check(state() == 'run' and left() == '0:25', 'Start runs Right Fold\'s hold (%r, %r)' % (state(), left()))

    def undo_stops():
        p.locator('#undoBtn').click()
        check(s.txt('#nowPose') == 'Left Fold', 'Undo goes back to Left Fold')
        check(state() == 'idle' and left() == '1:04', 'Undo stops the timer and shows Left Fold\'s 1:04 (%r, %r)' % (state(), left()))
        check(p.title() == 'Asana Rounds', 'the tab title is plain again (%r)' % p.title())

    def reset_and_done():
        p.locator('#tGo').click()
        run(2000)
        p.locator('#tReset').click()
        check(state() == 'idle' and left() == '1:04', 'Reset stops it at the full length (%r, %r)' % (state(), left()))
        p.locator('#doneBtn').click()
        check(s.txt('#nowPose') == 'Right Fold' and state() == 'idle', 'after Reset, Done leaves the next timer stopped (%r)' % state())
        p.locator('#undoBtn').click()
        p.locator('#tGo').click()
        run(3000)
        p.locator('#tGo').click()
        p.locator('#doneBtn').click()
        check(state() == 'idle' and left() == '0:30', 'Done on a paused hold leaves the next timer stopped (%r, %r)' % (state(), left()))
        p.locator('#undoBtn').click()
        p.locator('#tGo').click()
        run(3000)
        p.locator('#doneBtn').click()
        check(state() == 'idle' and left() == '0:30', 'Done mid-hold stops the timer and leaves the next one stopped (%r, %r)' % (state(), left()))
        t = tones()
        run(70000)
        check(tones() == t, 'and nothing rings later (%d tones)' % (tones() - t))
        p.locator('#undoBtn').click()

    def change_while_running():
        p.locator('#tGo').click()
        run(3000)
        chip(45).click()
        check(state() == 'run' and left() == '0:45', 'picking 0:45 while running restarts at 0:45 (%r, %r)' % (state(), left()))
        p.locator('#tReset').click()

    def customs():
        custom('1', '20')
        check(left() == '1:20', 'Custom 1 min 20 s gives 1:20 (%r)' % left())
        check(p.locator('#tCustom').is_hidden(), 'Set closes the custom row')
        c = p.locator('#tCustomBtn')
        check(c.inner_text().strip() == '1:20' and c.get_attribute('aria-pressed') == 'true',
              'the Custom chip shows 1:20 and is highlighted (%r)' % c.inner_text())
        check(chip(45).get_attribute('aria-pressed') == 'false', 'no preset chip is highlighted')
        custom('99', '0', enter=True)
        check(left() == '10:00', 'Enter sets it too, and 99 min is brought down to 10:00 (%r)' % left())
        custom('0', '2')
        check(left() == '0:05', '0 min 2 s is brought up to 0:05 (%r)' % left())
        p.locator('#tGo').click()
        run(5000)
        check(state() == 'up', 'a 0:05 hold is up after 5 s')
        t = tones()
        p.locator('#tLeft').click()
        run(15000)
        check(tones() == t, 'tapping the timer silences the chime (%d more tones)' % (tones() - t))
        reopen()
        check(left() == '0:05', 'the custom length is kept after a reload (%r)' % left())
        custom('1', '20')

    def editor():
        p.locator('#editRoutine > summary').click()
        lf = p.locator('#editRoutine .prow[data-pose="leftfold"] select.psec')
        rf = p.locator('#editRoutine .prow[data-pose="rightfold"] select.psec')
        check(p.locator('#editRoutine select.psec').count() == 10, 'every asana row has a length dropdown')
        vals = rf.evaluate('s=>[...s.options].map(o=>o.value)')
        check(vals == ['30', '45', '60', '64'], 'the dropdown offers 0:30, 0:45, 1:00, 1:04 (%s)' % vals)
        check(rf.input_value() == '30', 'Right Fold\'s dropdown reads 0:30')
        check(lf.input_value() == '80', 'Left Fold\'s dropdown shows its custom 1:20 (%r)' % lf.input_value())
        check('1:20' in lf.evaluate('s=>s.options[s.selectedIndex].text'), 'labelled 1:20')
        rf.select_option('45')
        lf.select_option('64')
        check(left() == '1:04', 'setting Left Fold to 1:04 in the editor updates the timer (%r)' % left())
        p.locator('#doneBtn').click()
        check(s.txt('#nowPose') == 'Right Fold' and left() == '0:45', 'Right Fold now reads 0:45 (%r)' % left())
        p.locator('#undoBtn').click()
        reopen()
        check(left() == '1:04', 'the editor\'s lengths survive a reload (%r)' % left())

    def sound():
        snd = p.locator('#tSound')
        check(snd.get_attribute('aria-pressed') == 'true', 'sound is on at first')
        snd.click()
        check(snd.get_attribute('aria-pressed') == 'false', 'the speaker button turns sound off')
        reopen()
        check(snd.get_attribute('aria-pressed') == 'false', 'sound stays off after a reload')
        t = tones()
        p.locator('#tGo').click()
        run(70000)
        check(state() == 'up', 'the hold still ends with sound off')
        check(tones() == t, 'with sound off nothing plays (%d tones)' % (tones() - t))
        p.locator('#tReset').click()
        snd.click()
        check(snd.get_attribute('aria-pressed') == 'true', 'and back on')
        check(tones() > t, 'turning sound on plays a short beep to show it works')

    def session_end():
        if not p.locator('#roundsSel').is_visible():
            p.locator('#editRoutine > summary').click()
        p.locator('#roundsSel').select_option('1')
        s.done(9)
        check(s.txt('#nowPose') == 'Plank', 'one round: on Plank after 9 Done (%r)' % s.txt('#nowPose'))
        p.locator('#tGo').click()
        p.locator('#doneBtn').click()
        check(p.locator('#nowCard').get_attribute('data-state') == 'complete', 'the morning is complete')
        check(p.locator('#timer').is_hidden(), 'the timer hides when the session is finished')
        t = tones()
        run(70000)
        check(tones() == t and state() == 'idle', 'and nothing starts or rings (%r, %d tones)' % (state(), tones() - t))

    try:
        for name, fn in [('timer: fresh', fresh), ('timer: each asana its own length', per_asana),
                         ('timer: countdown and time up', countdown), ('timer: the ring stops', ring_stops),
                         ('timer: Done waits for Start', done_waits), ('timer: Undo stops it', undo_stops),
                         ('timer: Reset and Done', reset_and_done), ('timer: change while running', change_while_running),
                         ('timer: custom', customs), ('timer: editor', editor), ('timer: sound off', sound),
                         ('timer: session end', session_end)]:
            run_section(name, fn)
        SECTION[0] = label + ': timer browser'
        for e in s.errors:
            check(False, 'browser error: ' + e)
        check(not s.blocked, 'no request to an unexpected host: %s' % s.blocked[:3])
    finally:
        s.close()


def clock_flow(browser, base, label):
    """Morning or Evening by the clock (Evening from 12:00 unless Edit routine says otherwise), on a fake clock."""
    def paused(start):
        s = Session(browser, base)
        s.page.clock.install(time=start)
        s.page.clock.pause_at(start + datetime.timedelta(seconds=1))
        return s

    s = paused(at(0, 11, 59))
    p = s.page
    try:
        def reopen():
            p.reload()
            p.locator('#doneBtn').wait_for()

        def by_the_hour():
            s.open()
            check(s.part() == 'am', 'at 11:59 the page opens on Morning (%r)' % s.part())
            sel = p.locator('#pmSel')
            check(sel.input_value() == '12', 'Edit routine says Evening starts at 12 (%r)' % sel.input_value())
            check(sel.evaluate('s=>s.options[s.selectedIndex].text') == '12 noon', 'labelled 12 noon')
            labels = sel.evaluate('s=>[...s.options].map(o=>o.text)')
            check(labels[0] == '9 am' and '2 pm' in labels and labels[-1] == '8 pm', 'the choices run from 9 am to 8 pm (%s)' % labels)
            p.clock.run_for(90000)
            check(s.part() == 'pm', 'left open past 12:00, the page moves to Evening by itself (%r)' % s.part())
            check(s.txt('#nowRound') == 'Round 1 of 5', 'on an empty evening, Round 1 of 5 (%r)' % s.txt('#nowRound'))

        def in_progress():
            p.locator('#partAm').click()
            s.done(3)
            p.clock.run_for(120000)
            check(s.part() == 'am', 'a session you are ticking is never switched by the clock (%r)' % s.part())
            reopen()
            check(s.part() == 'am', 'after a reload, a morning ticked minutes ago still opens on Morning (%r)' % s.part())
            check(s.txt('#nowPose') == 'Right Raise', 'right where it was, on Right Raise (%r)' % s.txt('#nowPose'))
            p.clock.run_for(31 * 60000)
            reopen()
            check(s.part() == 'pm', 'a morning left more than 30 minutes ago no longer holds the page: Evening (%r)' % s.part())

        def setting():
            p.locator('#editRoutine > summary').click()
            p.locator('#pmSel').select_option('14')
            check(s.part() == 'am', 'with Evening from 2 pm, 12:35 is Morning again (%r)' % s.part())
            reopen()
            check(p.locator('#pmSel').input_value() == '14' and s.part() == 'am', 'the 2 pm switch is kept after a reload')
            p.locator('#editRoutine > summary').click()
            p.locator('#pmSel').select_option('12')
            reopen()
            check(s.part() == 'pm', 'back to 12 noon: Evening (%r)' % s.part())

        for name, fn in [('clock: by the hour', by_the_hour), ('clock: a session in progress', in_progress),
                         ('clock: the switch time', setting)]:
            run_section(name, fn)
        SECTION[0] = label + ': clock browser'
        for e in s.errors:
            check(False, 'browser error: ' + e)
    finally:
        s.close()

    s = paused(at(0, 11, 59))
    p = s.page
    try:
        def hold_over_noon():
            s.open()
            p.locator('#tPresets .chip[data-sec="64"]').click()
            p.locator('#tGo').click()
            p.clock.run_for(62000)
            check(s.part() == 'am', 'a hold started at 11:59 keeps the Morning past 12:00 (%r)' % s.part())
            check(p.locator('#timer').get_attribute('data-state') == 'run' and s.txt('#tLeft') == '0:02',
                  'and keeps counting (%r)' % s.txt('#tLeft'))
        run_section('clock: a hold across 12:00', hold_over_noon)
        SECTION[0] = label + ': clock browser'
        for e in s.errors:
            check(False, 'browser error: ' + e)
    finally:
        s.close()


MERGE_CASES = r"""(function(){
  var on=function(s,k){return (+((s.marks||{})[k])||0)>(+((s.off||{})[k])||0);};
  var ses=function(marks,off,at,extra){var s={id:'s-2026-10-04-am',kind:'session',day:'2026-10-04',part:'am',poseIds:['a','b'],rounds:2,marks:marks,off:off||{},updatedAt:at};
    Object.keys(extra||{}).forEach(function(k){s[k]=extra[k];});return {v:1,app:'asana-rounds',items:{'s-2026-10-04-am':s}};};
  var get=function(r){return r.doc.items['s-2026-10-04-am'];};
  var out={};
  // the phone ticked 3 cells and saved; the laptop had not caught up and ticked one more, later
  var r=mergeDocs(ses({'1:b':500},{},500), ses({'1:a':100,'1:b':200,'2:a':300},{},300));
  out.union=['1:a','1:b','2:a'].every(function(k){return on(get(r),k);});
  out.unionLocalNewer=r.localNewer;
  // an untick on one device beats an older tick of the same cell on the other
  r=mergeDocs(ses({'1:a':100},{'1:a':400},400), ses({'1:a':100,'1:b':200},{},200));
  out.untickWins=!on(get(r),'1:a')&&on(get(r),'1:b');
  // ...and a re-tick after that untick wins again
  r=mergeDocs(ses({'1:a':100},{'1:a':400},400), ses({'1:a':500},{'1:a':400},500));
  out.retickWins=on(get(r),'1:a');
  out.retickRemoteOnly=!r.localNewer;
  // the same copy on both sides changes nothing and asks for no upload
  r=mergeDocs(ses({'1:a':100},{},100), ses({'1:a':100},{},100));
  out.sameQuiet=!r.localNewer;
  // a month-old session folded to its totals stays folded
  r=mergeDocs(ses({'1:a':100},{},100), ses({},{},900,{compact:true,count:4,total:4,full:2}));
  out.compactKept=get(r).compact===true&&get(r).count===4;
  // other items: newest wins, and a local-only item is kept and uploaded
  r=mergeDocs({items:{routine:{id:'routine',kind:'routine',rounds:3,updatedAt:900},x:{id:'x',kind:'session',day:'2026-10-03',part:'pm',marks:{},off:{},updatedAt:5}}},
              {items:{routine:{id:'routine',kind:'routine',rounds:5,updatedAt:100}}});
  out.routineNewer=r.doc.items.routine.rounds===3;
  out.localOnlyKept=!!r.doc.items.x&&r.localNewer;
  // a broken Drive file does not throw
  try{r=mergeDocs({items:{}},null);out.nullRemote=!!r.doc&&!!r.doc.items;}catch(e){out.nullRemote=false;}
  return out;
})()"""


def merge_flow(browser):
    """The two-device merge, run on the page's own merge code lifted out of the source."""
    src = open(os.path.join(ROOT, 'site', PAGE), encoding='utf-8').read()
    a, b = src.index('  function mergeMaps('), src.index('  function localFallback(')
    helper = re.search(r'  function isObj\(x\)\{.*?\}\n', src).group(0)
    s = Session(browser, '')
    try:
        def cases():
            res = s.page.evaluate('(function(){' + helper + src[a:b] + 'return ' + MERGE_CASES + ';})()')
            say = {'union': 'ticks from two devices on one session are combined, not replaced',
                   'unionLocalNewer': 'a device holding a tick Drive lacks uploads after merging',
                   'untickWins': 'an untick beats an older tick of the same cell, other cells kept',
                   'retickWins': 'a tick after an untick wins again',
                   'retickRemoteOnly': 'when Drive already has everything, no upload is asked for',
                   'sameQuiet': 'identical copies merge quietly',
                   'compactKept': 'a session folded to its totals stays folded',
                   'routineNewer': 'the newer routine wins',
                   'localOnlyKept': 'an item only this device has is kept and uploaded',
                   'nullRemote': 'an empty or broken Drive file does not break the merge'}
            for k, msg in say.items():
                check(res.get(k) is True, msg + ' (%r)' % res.get(k))
        run_section('two-device merge', cases)
    finally:
        s.close()


def lum(hexv):
    hexv = hexv.strip().lstrip('#')
    if len(hexv) == 3:
        hexv = ''.join(c * 2 for c in hexv)
    rgb = [int(hexv[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(a, b):
    la, lb = sorted([lum(a), lum(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)


def layout_flow(browser, base):
    for scheme in ('light', 'dark'):
        for w, h in ((390, 844), (1280, 800)):
            s = Session(browser, base, viewport=(w, h))
            p = s.page
            try:
                def one():
                    p.emulate_media(color_scheme=scheme)
                    p.clock.set_system_time(at(0, 8))
                    s.open()
                    check(p.evaluate('document.documentElement.scrollWidth') <= w, 'page fits %d px without sideways scroll (%d)' % (w, p.evaluate('document.documentElement.scrollWidth')))
                    b = p.locator('#doneBtn').bounding_box()
                    check(b and b['y'] + b['height'] <= h, 'the Done button is on screen without scrolling at %dx%d (bottom %s)' % (w, h, b and round(b['y'] + b['height'])))
                    # the digits, Start, Reset and the speaker share one line, also with the longest texts in them
                    for digits, go, st in (('0:30', 'Start', 'idle'), ('10:00', 'Resume', 'paused'), ('Time up', 'Again', 'up')):
                        tops = p.evaluate("""([d,g,st])=>{var t=document.querySelector('#timer');t.setAttribute('data-state',st);
                            document.querySelector('#tLeft').textContent=d;document.querySelector('#tGo').textContent=g;
                            return ['#tLeft','#tGo','#tReset','#tSound'].map(s=>{var r=document.querySelector(s).getBoundingClientRect();return Math.round(r.top+r.height/2);});}""",
                                          [digits, go, st])
                        check(max(tops) - min(tops) <= 4, 'the timer row stays on one line at %d px with %r and %r (%s)' % (w, digits, go, tops))
                    p.reload()
                    p.locator('#doneBtn').wait_for()
                    p.locator('#editRoutine > summary').click()
                    p.locator('#roundsSel').select_option('10')
                    p.locator('#doneBtn').click()
                    check(p.evaluate('document.documentElement.scrollWidth') <= w, 'ten rounds and the open editor still fit %d px (%d)' % (w, p.evaluate('document.documentElement.scrollWidth')))
                    v = p.evaluate("""(()=>{var cs=getComputedStyle(document.documentElement);var o={};
                        ['--bg','--surface','--surface-2','--text','--muted','--faint','--accent','--accent-ink','--good','--danger'].forEach(k=>o[k]=cs.getPropertyValue(k).trim());return o;})()""")
                    for fg in ('--text', '--muted', '--faint', '--accent', '--good', '--danger'):
                        for bg in ('--bg', '--surface', '--surface-2'):
                            r = ratio(v[fg], v[bg])
                            check(r >= 4.5, '%s on %s is %.2f:1 in %s (needs 4.5)' % (fg, bg, r, scheme))
                    r = ratio(v['--accent-ink'], v['--accent'])
                    check(r >= 4.5, 'button text on accent is %.2f:1 in %s' % (r, scheme))
                    if SHOTS:
                        p.screenshot(path=os.path.join(SHOTS, '%s-%d.png' % (scheme, w)), full_page=True)
                run_section('layout %s %dpx' % (scheme, w), one)
                for e in s.errors:
                    check(False, 'browser error: ' + e)
            finally:
                s.close()


SHOTS = os.environ.get('ASANA_SHOTS', '')  # a folder to save full-page screenshots of each layout into


def run_target(browser, label, directory, with_layout):
    if not os.path.exists(os.path.join(directory, PAGE)):
        SECTION[0] = label
        check(False, '%s does not exist yet' % os.path.join(os.path.basename(directory), PAGE))
        return
    httpd, base = serve(directory)
    try:
        print('==', label)
        s = Session(browser, base)
        try:
            flow(s)
            SECTION[0] = label + ': browser'
            for e in s.errors:
                check(False, 'browser error: ' + e)
            check(not s.blocked, 'no request to an unexpected host: %s' % s.blocked[:3])
        finally:
            s.close()
        print('==', label, 'timer')
        timer_flow(browser, base, label)
        print('==', label, 'clock')
        clock_flow(browser, base, label)
        if with_layout:
            print('==', label, 'layout')
            layout_flow(browser, base)
    finally:
        httpd.shutdown()


def main():
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=not HEADED)
        run_target(browser, 'docs/', os.path.join(ROOT, 'docs'), True)
        run_target(browser, 'site/', os.path.join(ROOT, 'site'), False)
        print('== merge')
        merge_flow(browser)
        browser.close()
    print('\n%d checks passed, %d failed' % (PASSES[0], len(FAILS)))
    for x in FAILS:
        print('  ' + x)
    sys.exit(1 if FAILS else 0)


if __name__ == '__main__':
    main()
