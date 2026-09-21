"""Drive the already-running Chrome (started with --remote-debugging-port=9222) with Playwright.

Usage:  python tools/cdp.py  < snippet.py
The snippet runs with `page` (the active tab), `ctx`, `shot(name)`, `wait(ms)` available.
Screenshots go to the SHOT_DIR folder as <name>.png.
"""
import os, sys, time
from playwright.sync_api import sync_playwright

SHOT_DIR = os.environ.get('SHOT_DIR') or os.getcwd()
code = sys.stdin.read()

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp('http://localhost:9222')
    ctx = browser.contexts[0]
    pages = ctx.pages
    page = None
    for pg in pages:
        if 'console.cloud.google.com' in pg.url or 'accounts.google.com' in pg.url:
            page = pg
    if page is None:
        page = pages[0] if pages else ctx.new_page()
    page.bring_to_front()

    def shot(name='shot'):
        path = os.path.join(SHOT_DIR, name + '.png')
        page.screenshot(path=path, full_page=False)
        print('shot:', path)

    def wait(ms):
        time.sleep(ms / 1000.0)

    try:
        exec(code, {'page': page, 'ctx': ctx, 'shot': shot, 'wait': wait, 'print': print, 'time': time})
    except Exception as e:
        print('ERROR:', type(e).__name__, str(e)[:500])
    print('url:', page.url)
