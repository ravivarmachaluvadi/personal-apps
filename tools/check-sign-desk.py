"""Sign Desk checker: drives the page in headless Chrome as a laptop and a phone sharing one fake Google Drive.

  python tools/check-sign-desk.py            # docs/sign-desk.html (built), then site/sign-desk.html
  python tools/check-sign-desk.py --headed   # watch it

Nothing here can reach the real Google Drive: sign-in and Drive are the fakes from check-drop-tray.py (imported,
not copied). PDF.js, pdf-lib and the Caveat font, which the page loads from cdnjs and jsDelivr, are answered from
Home Hub's copies (../home-hub/web/vendor, the same versions, checked by SHA-256 there), so no internet is needed
when Home Hub sits beside this repo; without it they are fetched from the CDNs.

docs/sign.js is Home Hub's signer (web/sign.js), copied unchanged: the first section compares the two and says
SKIPPED, never passes, when Home Hub is not beside this repo.

Exit code 0 means every check passed; each failure is printed with the section it came from.
"""
import base64, hashlib, importlib.util, io, json, os, re, sys, urllib.parse, zipfile
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location('check_drop_tray', os.path.join(ROOT, 'tools', 'check-drop-tray.py'))
dt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dt)
dt.PAGE, dt.DOC = 'sign-desk.html', 'sign-desk.json'
check, run_section, wait_until, nap, ratio = dt.check, dt.run_section, dt.wait_until, dt.nap, dt.ratio

HEADED = '--headed' in sys.argv
HOME_HUB = os.path.join(os.path.dirname(ROOT), 'home-hub')
SUB = 'Sign Desk'
SKIPS = []
PDFJS = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/6.3.289/'
CDN_FILES = {   # what the page asks the CDNs for -> Home Hub's copy of the same file
    PDFJS + 'pdf.min.mjs': 'pdfjs-6.3.289/pdf.min.mjs',
    PDFJS + 'pdf.worker.min.mjs': 'pdfjs-6.3.289/pdf.worker.min.mjs',
    'https://cdnjs.cloudflare.com/ajax/libs/pdf-lib/1.17.1/pdf-lib.min.js': 'pdf-lib-1.17.1/pdf-lib.min.js',
    'https://cdn.jsdelivr.net/npm/@fontsource/caveat@5.3.0/files/caveat-latin-400-normal.woff2': 'caveat-5.3.0/caveat-latin-400-normal.woff2',
}
IPHONE_UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) '
             'Version/18.0 Mobile/15E148 Safari/604.1')


# ------------------------------------------------------------------ PDFs made by hand, so every box is known
def make_pdf(pages):
    """pages: [{'media': box, 'crop': box or None, 'rotate': 0|90, 'content': bytes}] -> a PDF."""
    ids = [3 + 2 * i for i in range(len(pages))]
    objs = [b'<< /Type /Catalog /Pages 2 0 R >>',
            ('<< /Type /Pages /Kids [%s] /Count %d >>' % (' '.join('%d 0 R' % i for i in ids), len(pages))).encode()]
    for i, pg in enumerate(pages):
        d = '<< /Type /Page /Parent 2 0 R /MediaBox [%s] ' % ' '.join(str(v) for v in pg['media'])
        if pg.get('crop'):
            d += '/CropBox [%s] ' % ' '.join(str(v) for v in pg['crop'])
        if pg.get('rotate'):
            d += '/Rotate %d ' % pg['rotate']
        d += '/Resources << >> /Contents %d 0 R >>' % (ids[i] + 1)
        objs.append(d.encode())
        c = pg['content']
        objs.append(b'<< /Length %d >>\nstream\n' % len(c) + c + b'\nendstream')
    out, offs = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n', []
    for n, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b'%d 0 obj\n' % n + o + b'\nendobj\n'
    xref = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objs) + 1) + b''.join(b'%010d 00000 n \n' % o for o in offs)
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objs) + 1, xref)
    return out


# a form: a plain page with a 14 pt box, a page turned 90 degrees with one, a page with a crop box with one
FORM_PAGES = [
    {'media': (0, 0, 612, 792), 'box': (100, 600, 114, 614)},
    {'media': (0, 0, 595, 842), 'rotate': 90, 'box': (200, 300, 214, 314)},
    {'media': (0, 0, 612, 792), 'crop': (36, 36, 576, 756), 'box': (200, 400, 214, 414)},
]
FORM_PDF = make_pdf([dict(p, content=b'0.85 g %d %d 14 14 re f' % p['box'][:2]) for p in FORM_PAGES])


def shown(page, box, pad=0.0):
    """A box in PDF points, grown by pad, as fractions of the page as shown (crop box, turned clockwise)."""
    cx0, cy0, cx1, cy1 = page.get('crop') or page['media']
    W, H = cx1 - cx0, cy1 - cy0
    x0, y0, x1, y1 = box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad
    if page.get('rotate') == 90:
        return ((y0 - cy0) / H, (x0 - cx0) / W, (y1 - cy0) / H, (x1 - cx0) / W)
    return ((x0 - cx0) / W, (cy1 - y1) / H, (x1 - cx0) / W, (cy1 - y0) / H)


def centre(b):
    return [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]


def ring(b):
    x0, y0, x1, y1 = b
    xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
    return [[x0, y0], [xm, y0], [x1, y0], [x0, ym], [x1, ym], [x0, y1], [xm, y1], [x1, y1]]


def grid(box, n=6):
    x0, y0, x1, y1 = box
    return [[x0 + (x1 - x0) * (i + .5) / n, y0 + (y1 - y0) * (j + .5) / n] for i in range(n) for j in range(n)]


def jpeg(w, h, rgb):
    from PIL import Image
    b = io.BytesIO()
    Image.new('RGB', (w, h), rgb).save(b, 'JPEG', quality=90)
    return b.getvalue()


def docx():
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        z.writestr('word/document.xml', '<w:document/>')
    return b.getvalue()


def is_blue(rgb):
    return rgb is not None and rgb[2] - rgb[0] > 60 and rgb[2] - rgb[1] > 60


STROKE = """(a)=>{var el=document.querySelector(a.sel);if(!el)return null;el.scrollIntoView({block:'center',inline:'nearest'});var r=el.getBoundingClientRect();
  var id=a.type==='pen'?11:a.type==='touch'?12:1;
  function at(p){return [r.left+p[0]*r.width,r.top+p[1]*r.height];}
  function fire(name,p,target){var xy=at(p),t=target||document.elementFromPoint(xy[0],xy[1])||el;
    t.dispatchEvent(new PointerEvent(name,{pointerId:id,pointerType:a.type,isPrimary:true,clientX:xy[0],clientY:xy[1],pressure:name==='pointerup'?0:0.6,
      buttons:name==='pointerup'?0:1,button:0,bubbles:true,cancelable:true,composed:true}));return t;}
  var pts=a.pts,first=fire('pointerdown',pts[0]);
  for(var i=1;i<pts.length;i++){var p0=pts[i-1],p1=pts[i];for(var k=1;k<=12;k++)fire('pointermove',[p0[0]+(p1[0]-p0[0])*k/12,p0[1]+(p1[1]-p0[1])*k/12],first);}
  fire('pointerup',pts[pts.length-1],first);return {w:r.width,h:r.height};}"""

# render page n of a PDF with PDF.js (the page's own copy, from the CDN) and return the darkest value near each point
SAMPLE_PDF = """async (a)=>{const m=await import(a.pdfjs+'pdf.min.mjs');m.GlobalWorkerOptions.workerSrc=a.pdfjs+'pdf.worker.min.mjs';
  const bin=atob(a.b64),u=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)u[i]=bin.charCodeAt(i);
  const doc=await m.getDocument({data:u,isEvalSupported:false}).promise;const pg=await doc.getPage(a.page);const vp=pg.getViewport({scale:2});
  const c=document.createElement('canvas');c.width=Math.floor(vp.width);c.height=Math.floor(vp.height);await pg.render({canvas:c,viewport:vp}).promise;
  const x=c.getContext('2d',{willReadFrequently:true});
  const out=a.pts.map(p=>{const cx=Math.round(p[0]*c.width),cy=Math.round(p[1]*c.height),R=a.r||4;
    const d=x.getImageData(Math.max(0,cx-R),Math.max(0,cy-R),2*R+1,2*R+1).data;let lo=255,rgb=[255,255,255];for(let i=0;i<d.length;i+=4){const l=(d[i]+d[i+1]+d[i+2])/3;if(l<lo){lo=l;rgb=[d[i],d[i+1],d[i+2]];}}return [Math.round(lo),rgb];});
  return {count:doc.numPages,v:out.map(o=>o[0]),c:out.map(o=>o[1])};}"""


def mark_box(s, page, cls):
    return s.page.evaluate("""(a)=>{var pg=document.querySelector('#signer .spage[data-i="'+a.page+'"]'),m=pg&&pg.querySelector('.smark.'+a.cls);if(!m)return null;
        var r=pg.getBoundingClientRect(),q=m.getBoundingClientRect();return [(q.left-r.left)/r.width,(q.top-r.top)/r.height,(q.right-r.left)/r.width,(q.bottom-r.top)/r.height];}""",
                           {'page': page, 'cls': cls})


# ------------------------------------------------------------------ one device
class Desk(dt.Session):
    """A browser on the fake Drive, whose CDN requests are answered from Home Hub's copies when it is here."""

    def __init__(self, browser, base, fake, viewport=(1280, 900), ua=None, touch=False):
        super().__init__(browser, base, fake, viewport=viewport)
        if ua or touch:   # a phone: its own context, the same fakes
            self.ctx.close()
            dt.PAGES.remove(self.page)
            self.ctx = browser.new_context(viewport={'width': viewport[0], 'height': viewport[1]}, accept_downloads=True,
                                           user_agent=ua, has_touch=touch, is_mobile=touch)
            self.ctx.add_init_script(dt.AWAY_HOOKS)
            self.ctx.route('**/*', self.route)
            self.page = self.ctx.new_page()
            dt.PAGES.append(self.page)
            self.page.set_default_timeout(8000)
            self.page.on('pageerror', lambda e: self.errors.append('pageerror: ' + str(e)))
            self.page.on('console', self.console)
        self.page.set_default_timeout(10000)

    def route(self, route, req):
        local = CDN_FILES.get(req.url)
        if local is None and urllib.parse.urlsplit(req.url).hostname in ('cdnjs.cloudflare.com', 'cdn.jsdelivr.net'):
            self.blocked.append(req.url)
            return route.abort()
        if local is not None:
            p = os.path.join(HOME_HUB, 'web', 'vendor', *local.split('/'))
            if not os.path.isfile(p):
                return route.continue_()
            ctype = 'font/woff2' if p.endswith('.woff2') else 'text/javascript'
            return route.fulfill(status=200, body=open(p, 'rb').read(), content_type=ctype,
                                 headers={'Access-Control-Allow-Origin': '*', 'Cache-Control': 'max-age=3600'})
        return super().route(route, req)

    def open(self, path=None):
        self.page.goto(self.base + (path or dt.PAGE))
        self.page.locator('#pickBtn').wait_for(state='visible')

    def pick(self, name, data, mime='application/octet-stream'):
        self.page.set_input_files('#fileInput', files=[{'name': name, 'mimeType': mime, 'buffer': data}])

    def stroke(self, sel, pts, kind='pen'):
        return self.page.evaluate(STROKE, {'sel': sel, 'pts': pts, 'type': kind})

    def marks(self):
        return int(self.page.evaluate("+(document.querySelector('#signer')||{dataset:{}}).dataset.marks||0"))

    def state(self):
        return self.page.evaluate("(document.querySelector('#signer')||{dataset:{}}).dataset.state")

    def signer_ready(self, pages=1):
        ok = wait_until(lambda: self.state() in ('ready', 'locked', 'error'), 20)
        check(ok and self.state() == 'ready', 'the signer opened the document (%s, %r)' % (self.state(), self.page.evaluate("(document.querySelector('#sMsg')||{}).textContent")))
        return wait_until(lambda: self.page.locator('#signer .spage[data-rendered="1"]').count() >= pages, 15)

    def tool(self, name):
        self.page.click('#sTools [data-tool="%s"]' % name)

    def sample(self, data, page, pts, r=4):
        return self.page.evaluate(SAMPLE_PDF, {'b64': base64.b64encode(data).decode(), 'page': page, 'pts': pts, 'r': r, 'pdfjs': PDFJS})

    def visible(self, sel):
        return self.page.locator(sel).first.is_visible()


def pics(fake, kind):
    """The live signatures ('sig') or initials ('ini') in sign-desk.json, oldest first."""
    d = (fake.doc() or {}).get('items') or {}
    live = [it for it in d.values() if it and it.get('kind') == kind and not it.get('deleted') and it.get('png')]
    return sorted(live, key=lambda it: it.get('made') or 0)


def pic(fake, kind):
    got = pics(fake, kind)
    return got[-1] if got else None


def drive_copies(fake):
    fid = fake.folder(SUB)
    return [f for f in fake.files.values() if fid and fid in f['parents'] and not f['trashed']] if fid else []


# ------------------------------------------------------------------ the signer is Home Hub's
def same_signer():
    def one():
        ours = os.path.join(ROOT, 'docs', 'sign.js')
        check(os.path.isfile(ours), 'docs/sign.js is there')
        theirs = os.path.join(HOME_HUB, 'web', 'sign.js')
        if not os.path.isfile(theirs):
            SKIPS.append('docs/sign.js was not compared with Home Hub\'s: ../home-hub is not beside this repo')
            return
        a, b = open(ours, 'rb').read(), open(theirs, 'rb').read()
        check(a == b, 'docs/sign.js is Home Hub\'s web/sign.js, unchanged (sha256 %s vs %s); copy it across again' %
              (hashlib.sha256(a).hexdigest()[:12], hashlib.sha256(b).hexdigest()[:12]))
        for api in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write'):
            check(api.encode() not in a, 'sign.js builds with createElement, never %s' % api)
    run_section('the signer is Home Hub\'s', one)


# ------------------------------------------------------------------ a laptop and a phone
def laptop_and_phone(browser, base):
    fake = dt.FakeDrive()
    A = Desk(browser, base, fake)
    B = None
    st = {}
    try:
        def signed_out():
            A.open()
            check(A.visible('#authNote') and 'Continue with Google' in A.page.locator('#authNote').text_content(),
                  'signed out, the page says to continue with Google so the signature comes from Drive (%r)' % A.page.locator('#authNote').text_content())
            check('None yet' in A.page.locator('#mine').text_content(), 'no signatures yet (%r)' % A.page.locator('#mine').text_content())
            A.sign_in()
            check(wait_until(lambda: not A.visible('#authNote'), 3), 'signed in, the note goes')
        run_section('signed out, then in', signed_out)

        def word_and_others():
            A.pick('letter.docx', docx(), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
            check(wait_until(lambda: 'Save as PDF' in A.toast() and 'Home Hub' in A.toast(), 3),
                  'a Word file: save it as PDF in Word, or sign it with Home Hub at home (%r)' % A.toast())
            check(not A.visible('#signer'), 'and the signer stays shut')
            A.pick('notes.txt', b'hello', 'text/plain')
            check(wait_until(lambda: 'PDF' in A.toast() and 'photo' in A.toast(), 3), 'anything else: only PDFs and photos (%r)' % A.toast())
        run_section('Word files and others are explained, not opened', word_and_others)

        def sign_on_laptop():
            A.pick('form.pdf', FORM_PDF, 'application/pdf')
            A.page.locator('#signer').wait_for(state='visible')
            check(A.signer_ready(3), 'all three pages are drawn')
            check('form.pdf' in A.page.locator('#sTitle').text_content(), 'the signer names the file')
            check(A.visible('#sDrive') and 'Sign Desk' in A.page.locator('#sDrive').text_content(),
                  'it offers a copy in Google Drive › Personal Apps › Sign Desk (%r)' % A.page.locator('#sDrive').text_content())
            A.tool('sig')
            check(wait_until(lambda: A.visible('#sigPad'), 3), 'with no signature yet, the pad opens')
            check('Google Drive' in A.page.locator('#sigText').text_content(), 'and says it is kept in Google Drive (%r)' % A.page.locator('#sigText').text_content())
            A.stroke('#sigCanvas', [[0.1, 0.55], [0.5, 0.4], [0.9, 0.55]], 'mouse')
            A.page.click('#sigUse')
            check(wait_until(lambda: pic(fake, 'sig') is not None, 8), 'the signature is saved to sign-desk.json in Drive')
            check((pic(fake, 'sig') or {}).get('name') == 'My signature', 'as "My signature", the name the first one gets')
            check(wait_until(lambda: A.page.locator('#mine .pic[data-kind="sig"] img').count() == 1, 3), 'and shown under Your signatures')
            A.stroke('#signer .spage[data-i="1"]', [[0.6, 0.45]], 'mouse')
            check(wait_until(lambda: A.marks() == 1, 3), 'one click places it')
            st['sig'] = mark_box(A, 1, 'ssig')
            A.tool('ini')
            check(wait_until(lambda: A.visible('#sigPad'), 3), 'Initials: the pad opens for them')
            A.stroke('#sigCanvas', [[0.3, 0.3], [0.4, 0.7], [0.5, 0.3]], 'mouse')
            A.page.click('#sigUse')
            check(wait_until(lambda: pic(fake, 'ini') is not None, 8), 'the initials are saved to Drive too')
            A.stroke('#signer .spage[data-i="1"]', [[0.85, 0.92]], 'mouse')
            check(wait_until(lambda: A.marks() == 2, 3), 'placed')
            A.page.click('#sEvery')
            check(wait_until(lambda: A.marks() == 4, 3), 'Every page puts them on the other two (%d)' % A.marks())
            st['ini'] = {n: mark_box(A, n, 'sini') for n in (1, 2, 3)}
            A.tool('tick')
            for n, page in enumerate(FORM_PAGES, 1):
                A.stroke('#signer .spage[data-i="%d"]' % n, [centre(shown(page, page['box']))], 'mouse')
            check(wait_until(lambda: A.marks() == 7, 3), 'a tick in each box (%d)' % A.marks())
            with A.page.expect_download(timeout=20000) as dl:
                A.page.click('#sSave')
            st['name'] = dl.value.suggested_filename
            st['bytes'] = open(dl.value.path(), 'rb').read()
            check(st['name'] == 'form (signed).pdf', 'on a laptop, Save downloads the signed copy (%s)' % st['name'])
            check(wait_until(lambda: A.visible('#result'), 3) and A.page.locator('#resName').text_content() == st['name'],
                  'and the page shows it, with Download again')
        run_section('signing on the laptop: signature, initials on every page, ticks', sign_on_laptop)

        def the_copy():
            data = st.get('bytes') or b''
            check(data[:5] == b'%PDF-', 'the download is a PDF')
            if data[:5] != b'%PDF-':
                return
            for n, page in enumerate(FORM_PAGES, 1):
                g = A.sample(FORM_PDF, n, [centre(shown(page, page['box']))], r=1)['v'][0]
                check(190 < g < 235, 'page %d: the grey box is where the checker expects it (%d)' % (n, g))
                inside = A.sample(data, n, grid(shown(page, page['box']), 5), r=2)
                check(min(inside['v']) < 120 and is_blue(inside['c'][inside['v'].index(min(inside['v']))]), 'page %d: the tick is in its box, blue (%d)' % (n, min(inside['v'])))
                around = A.sample(data, n, ring(shown(page, page['box'], pad=2.5)), r=1)['v']
                check(min(around) > 245, 'page %d: and nothing outside the box (%s)' % (n, around))
                b = st['ini'].get(n)
                if b:
                    s = A.sample(data, n, grid(b, 8), r=1)
                    check(min(s['v']) < 120, 'page %d: the initials are where they were placed (%d)' % (n, min(s['v'])))
            if st.get('sig'):
                s = A.sample(data, 1, grid(st['sig'], 8), r=1)
                check(min(s['v']) < 120, 'page 1: the signature is where it was placed (%d)' % min(s['v']))
                x0, y0, x1, y1 = st['sig']
                out = A.sample(data, 1, [[x0 - 0.03, (y0 + y1) / 2], [x1 + 0.03, (y0 + y1) / 2]], r=1)['v']
                check(min(out) > 245, 'page 1: and only there (%s)' % out)
            got = wait_until(lambda: drive_copies(fake), 10)
            check(got and len(got) == 1 and got[0]['name'] == st['name'], 'a copy is in Google Drive › Personal Apps › Sign Desk (%s)' % [f['name'] for f in got or []])
            if got:
                check(got[0]['content'] == data, 'the very same bytes as the download')
            check(wait_until(lambda: 'Google Drive' in A.page.locator('#driveState').text_content(), 5),
                  'the page says the copy is in Google Drive (%r)' % A.page.locator('#driveState').text_content())
        run_section('the signed copy: every mark in place, and the copy in Drive', the_copy)

        def on_the_phone():
            nonlocal B
            B = Desk(browser, base, fake, viewport=(390, 844), ua=IPHONE_UA, touch=True)
            B.open()
            B.sign_in()
            check(wait_until(lambda: B.page.locator('#mine .pic[data-kind="sig"] img').count() == 1 and B.page.locator('#mine .pic[data-kind="ini"] img').count() == 1, 8),
                  'the phone shows the signature and initials drawn on the laptop')
            B.pick('photo.jpg', jpeg(640, 480, (255, 255, 255)), 'image/jpeg')
            B.page.locator('#signer').wait_for(state='visible')
            B.signer_ready(1)
            B.tool('sig')
            B.page.wait_for_timeout(400)
            check(not B.visible('#sigPad'), "the laptop's signature is ready on the phone: no pad")
            B.stroke('#signer .spage[data-i="1"]', [[0.5, 0.5]], 'touch')
            check(wait_until(lambda: B.marks() == 1, 3), 'one tap places it (%d)' % B.marks())
            want = (pic(fake, 'sig') or {}).get('png')
            check(want and B.page.evaluate("(w)=>Object.values(JSON.parse(localStorage.getItem('signdesk.pics')||'{}')).some(x=>x.png===w)", want),
                  'and the phone keeps a copy of the same one')
            B.page.click('#driveCopy')   # off on this phone: no copy in Drive
            B.page.click('#sSave')
            check(wait_until(lambda: B.visible('#result'), 10), 'on a phone, Save shows the signed copy to share or download')
            check(B.page.locator('#resName').text_content() == 'photo (signed).jpg', 'named photo (signed).jpg (%r)' % B.page.locator('#resName').text_content())
            can = B.page.evaluate("(()=>{try{return !!(navigator.canShare&&navigator.share&&navigator.canShare({files:[new File(['x'],'a.jpg',{type:'image/jpeg'})]}));}catch(e){return false;}})()")
            check(B.visible('#shareBtn') == can, 'Share is offered exactly when this browser can share files (%s)' % can)
            with B.page.expect_download() as dl:
                B.page.click('#dlBtn')
            from PIL import Image
            im = Image.open(io.BytesIO(open(dl.value.path(), 'rb').read()))
            check(im.size == (640, 480) and im.format == 'JPEG', 'Download gives the photo, same size and kind (%s %s)' % (im.size, im.format))
            dark = min(im.convert('L').getpixel((320 + dx, 240 + dy)) for dx in range(-30, 31, 3) for dy in range(-10, 11, 2))
            check(dark < 150, 'with the signature on it (%d)' % dark)
            nap(1500)
            check(len(drive_copies(fake)) == 1, 'switched off, nothing more went to Drive (%d)' % len(drive_copies(fake)))
            check(B.ls('signdesk.copyDrive') == '0', 'and off is remembered on the phone')
        run_section('on the phone: the same signature, and Share or Download', on_the_phone)

        def other_ways_in():
            A.page.click('#againBtn')
            check(not A.visible('#result'), 'Sign another clears the last result')
            A.page.evaluate("""(a)=>{%s
                var dt=new DataTransfer();dt.items.add(makeFile(a));
                document.body.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));}""" % dt.MAKE_FILE,
                            {'name': 'pasted.pdf', 'type': 'application/pdf', 'b64': base64.b64encode(FORM_PDF).decode()})
            check(wait_until(lambda: A.state() == 'ready', 15), 'Ctrl+V with a PDF on the clipboard opens it for signing')
            A.page.click('#sCancel')
            shown_zone = A.page.evaluate("""(a)=>{%s
                var dt=new DataTransfer();dt.items.add(makeFile(a));var t=document.body;
                t.dispatchEvent(new DragEvent('dragenter',{dataTransfer:dt,bubbles:true,cancelable:true}));
                t.dispatchEvent(new DragEvent('dragover',{dataTransfer:dt,bubbles:true,cancelable:true}));
                var z=document.querySelector('#dropzone'),on=!!z&&!z.hidden;
                t.dispatchEvent(new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true}));return on;}""" % dt.MAKE_FILE,
                                        {'name': 'dragged.pdf', 'type': 'application/pdf', 'b64': base64.b64encode(FORM_PDF).decode()})
            check(shown_zone, 'dragging a file over the page shows where to drop it')
            check(wait_until(lambda: A.state() == 'ready' and 'dragged.pdf' in A.page.locator('#sTitle').text_content(), 15), 'and dropping it opens it')
            A.page.click('#sCancel')
        run_section('paste and drag', other_ways_in)

        def names_on_the_page():
            A.pick('form.pdf', FORM_PDF, 'application/pdf')
            A.signer_ready(1)
            A.tool('sig')
            for name in ('HDFC', 'SBI'):
                A.page.click('#sigNew')
                A.stroke('#sigCanvas', [[0.1, 0.5], [0.9, 0.45]], 'mouse')
                A.page.fill('#sigName', name)
                A.page.click('#sigUse')
                check(wait_until(lambda: any(x.get('name') == name for x in pics(fake, 'sig')), 8), '"%s" is saved to Drive' % name)
            A.page.click('#sCancel')
            rows = lambda s: s.page.eval_on_selector_all('#mine .pic[data-kind="sig"] .pname', 'els=>els.map(e=>e.textContent)')
            check(wait_until(lambda: rows(A) == ['My signature', 'HDFC', 'SBI'], 3), 'the page lists the signatures by name (%s)' % rows(A))
            sbi = '#mine .pic[data-kind="sig"]:has(.pname:text-is("SBI"))'
            A.page.click(sbi + ' [data-act="rename"]')
            check(A.page.input_value(sbi + ' [data-role="name"]') == 'SBI', 'Rename starts from the name it has')
            A.page.fill(sbi + ' [data-role="name"]', 'SBI Bank')
            A.page.keyboard.press('Enter')
            check(wait_until(lambda: any(x.get('name') == 'SBI Bank' for x in pics(fake, 'sig')), 8), 'Enter renames it in Drive')
            check(wait_until(lambda: 'SBI Bank' in rows(A), 3), 'and on the page')
            hdfc = '#mine .pic[data-kind="sig"]:has(.pname:text-is("HDFC"))'
            A.page.click(hdfc + ' [data-act="del"]')
            check(A.page.evaluate("document.activeElement&&document.activeElement.dataset.act") == 'cancel', 'Delete asks first, with Cancel chosen')
            A.page.keyboard.press('Escape')
            check('HDFC' in rows(A) and not A.visible(hdfc + ' .pask'), 'Esc keeps it')
            A.page.click(hdfc + ' [data-act="del"]')
            A.page.click(hdfc + ' [data-act="yes"]')
            check(wait_until(lambda: not any(x.get('name') == 'HDFC' for x in pics(fake, 'sig')), 8), 'Delete removes it from Drive, for every device')
            check('HDFC' not in rows(A), 'and from the page')
            B.page.reload()
            B.page.locator('#pickBtn').wait_for(state='visible')
            check(wait_until(lambda: rows(B) == ['My signature', 'SBI Bank'], 10), 'the phone lists the same, renamed and without the deleted one (%s)' % rows(B))
            B.pick('photo.jpg', jpeg(640, 480, (255, 255, 255)), 'image/jpeg')
            B.signer_ready(1)
            B.tool('sig')
            chips = lambda: sorted(B.page.eval_on_selector_all('#sigChoices [data-pic]', 'els=>els.map(e=>e.textContent)'))
            check(wait_until(lambda: chips() == ['My signature', 'SBI Bank'], 5), 'and its signer offers just those (%s)' % chips())
            check(not B.page.evaluate("Object.values(JSON.parse(localStorage.getItem('signdesk.pics')||'{}')).some(x=>x.name==='HDFC')"),
                  'the phone kept no copy of the deleted one')
            B.page.click('#sCancel')
        run_section('several signatures by name: rename and delete on the page', names_on_the_page)

        def copies_list():
            A.page.reload()
            A.page.locator('#pickBtn').wait_for(state='visible')
            names = lambda: A.page.eval_on_selector_all('#copies .copy .cname', 'els=>els.map(e=>e.textContent)')
            check(wait_until(lambda: names() == ['form (signed).pdf'], 8), 'the signed copy kept in Drive is listed (%s)' % names())
            f = drive_copies(fake)[0] if drive_copies(fake) else None
            row = '#copies .copy:has(.cname:text-is("form (signed).pdf"))'
            with A.page.expect_download() as dl:
                A.page.click(row + ' [data-act="dl"]')
            check(f and open(dl.value.path(), 'rb').read() == f['content'], 'Download from the list gives the same file')
            A.page.click(row + ' [data-act="del"]')
            check(A.page.evaluate("document.activeElement&&document.activeElement.dataset.act") == 'keep', 'Delete asks first, with Cancel chosen')
            A.page.click(row + ' [data-act="keep"]')
            check(names() == ['form (signed).pdf'], 'Cancel keeps it')
            A.page.click(row + ' [data-act="del"]')
            A.page.click(row + ' [data-act="yes"]')
            check(names() == [] and 'Undo' in A.toast(), 'Delete takes it off the list, with Undo (%r)' % A.toast())
            A.page.click('#toastAct')
            check(names() == ['form (signed).pdf'], 'Undo puts it back')
            nap(7000)
            check(f and not fake.files[f['id']]['trashed'], 'and it never went to the Trash')
            A.page.click(row + ' [data-act="del"]')
            A.page.click(row + ' [data-act="yes"]')
            check(f and wait_until(lambda: fake.files[f['id']]['trashed'], 12), 'once Undo has passed, it goes to Google Drive’s Trash')
            check(names() == [], 'and stays off the list')
            folder = fake.folder(SUB)
            now = __import__('time').time()
            old = fake.add('old (signed).pdf', 'application/pdf', folder, b'%PDF-1.4 old', at=now - 10 * 86400)
            recent = fake.add('recent (signed).pdf', 'application/pdf', folder, b'%PDF-1.4 recent', at=now - 2 * 86400)
            A.page.reload()
            A.page.locator('#pickBtn').wait_for(state='visible')
            check(wait_until(lambda: sorted(names()) == ['old (signed).pdf', 'recent (signed).pdf'], 8), 'copies saved elsewhere show up (%s)' % names())
            check(A.page.input_value('#keepSel') == '0', 'nothing is cleared until you choose to')
            A.page.select_option('#keepSel', '7')
            check(wait_until(lambda: fake.files[old]['trashed'], 8), 'Clear after 7 days sends the 10-day-old copy to the Trash')
            check(not fake.files[recent]['trashed'], 'and keeps the 2-day-old one')
            check(wait_until(lambda: names() == ['recent (signed).pdf'], 3) and 'older than 7 days' in A.toast(), 'the page says so (%r)' % A.toast())
            st_ = ((fake.doc() or {}).get('items') or {}).get('settings') or {}
            check(wait_until(lambda: (((fake.doc() or {}).get('items') or {}).get('settings') or {}).get('keepDays') == 7, 8),
                  'the setting is kept in Drive, for every device (%s)' % st_)
            A.page.select_option('#keepSel', '0')
            older = fake.add('older (signed).pdf', 'application/pdf', folder, b'%PDF-1.4 older', at=now - 40 * 86400)
            A.page.reload()
            A.page.locator('#pickBtn').wait_for(state='visible')
            check(wait_until(lambda: 'older (signed).pdf' in names(), 8), 'set back to Never, an old copy is listed')
            nap(1500)
            check(not fake.files[older]['trashed'], 'and left alone')
        run_section('signed copies in Drive: list, download, delete with Undo, clear old ones', copies_list)

        def templates_and_protected():
            A.pick('form.pdf', FORM_PDF, 'application/pdf')
            A.signer_ready(3)
            A.tool('sig')
            A.stroke('#signer .spage[data-i="1"]', [[0.5, 0.5]], 'mouse')
            check(wait_until(lambda: A.marks() == 1, 5), 'a signature placed')
            A.tool('tick')
            A.stroke('#signer .spage[data-i="3"]', [[0.3, 0.3]], 'mouse')
            check(wait_until(lambda: A.marks() == 2, 5), 'and a tick')
            A.page.click('#sTplPick')
            A.page.click('#sTplSave')
            A.page.fill('#sTplName', 'Bank form')
            A.page.keyboard.press('Enter')
            tpls = lambda: [t for t in ((fake.doc() or {}).get('items') or {}).values() if t and t.get('kind') == 'tpl' and not t.get('deleted')]
            check(wait_until(lambda: [(t['name'], len(t['marks'])) for t in tpls()] == [('Bank form', 2)], 8),
                  'Save as template keeps "Bank form" in sign-desk.json (%s)' % [(t.get('name'), len(t.get('marks', []))) for t in tpls()])
            A.page.click('#sCancel')
            A.page.click('#sAskDiscard')
            B.page.reload()
            B.page.locator('#pickBtn').wait_for(state='visible')
            B.pick('again.pdf', FORM_PDF, 'application/pdf')
            B.signer_ready(3)
            check(wait_until(lambda: B.visible('#sTplOffer') and 'Bank form' in B.page.locator('#sTplOffer').text_content(), 8),
                  'the phone, opening the same form, is offered "Bank form"')
            B.page.click('#sTplOffer [data-tpl]')
            check(wait_until(lambda: B.marks() == 2, 5), 'and one tap places its signature and tick (%d)' % B.marks())
            B.page.click('#sCancel')
            B.page.click('#sAskDiscard')
            from pypdf import PdfReader, PdfWriter
            w = PdfWriter()
            for pg in PdfReader(io.BytesIO(make_pdf([{'media': (0, 0, 300, 300), 'content': b'0.5 g 10 10 50 50 re f'}]))).pages:
                w.add_page(pg)
            w.encrypt(user_password='pw123', owner_password='owner-only', algorithm='RC4-128')   # RC4 needs no extra package; PDF.js opens AES too
            b = io.BytesIO()
            w.write(b)
            A.pick('statement.pdf', b.getvalue(), 'application/pdf')
            check(wait_until(lambda: A.state() == 'password', 15), 'a PDF with a password asks for it (%s)' % A.state())
            A.page.fill('#sPw', 'pw123')
            A.page.keyboard.press('Enter')
            check(wait_until(lambda: A.state() == 'ready', 15) and A.visible('#sNote'), 'the right password opens it, with the note about its signed copy')
            A.tool('sig')
            A.stroke('#signer .spage[data-i="1"]', [[0.6, 0.5]], 'mouse')
            check(wait_until(lambda: A.marks() == 1, 5), 'the signature goes on it')
            with A.page.expect_download(timeout=30000) as dl:
                A.page.click('#sSave')
            data = open(dl.value.path(), 'rb').read()
            check(data[:5] == b'%PDF-' and not PdfReader(io.BytesIO(data)).is_encrypted, 'its signed copy downloads, a PDF with no password (%s)' % dl.value.suggested_filename)
        run_section('form templates across devices, and a PDF with a password', templates_and_protected)
        A.report()
        if B:
            B.report()
    finally:
        A.close()
        if B:
            B.close()


# ------------------------------------------------------------------ layout and contrast
HEX_OF = """(sel)=>{var cv=document.createElement('canvas');cv.width=cv.height=1;var x=cv.getContext('2d',{willReadFrequently:true});
  function hex(c){x.clearRect(0,0,1,1);x.fillStyle='#000';x.fillStyle=c;x.fillRect(0,0,1,1);var d=x.getImageData(0,0,1,1).data;return '#'+[d[0],d[1],d[2]].map(v=>v.toString(16).padStart(2,'0')).join('');}
  function bg(e){while(e){var c=getComputedStyle(e).backgroundColor;if(c&&c!=='rgba(0, 0, 0, 0)'&&c!=='transparent')return c;e=e.parentElement;}return getComputedStyle(document.body).backgroundColor;}
  var out=[];document.querySelectorAll(sel).forEach(function(e){if(!e.offsetParent||!e.textContent.trim())return;out.push([e.id||e.className||e.tagName,hex(getComputedStyle(e).color),hex(bg(e))]);});return out;}"""


def layout(browser, base):
    for scheme in ('light', 'dark'):
        for w, h in ((390, 844), (1280, 900)):
            fake = dt.FakeDrive()
            s = Desk(browser, base, fake, viewport=(w, h), touch=w < 500, ua=IPHONE_UA if w < 500 else None)
            p = s.page
            try:
                def one():
                    p.emulate_media(color_scheme=scheme)
                    s.open()
                    check(p.evaluate('document.documentElement.scrollWidth') <= w, 'signed out, the page fits %d px' % w)
                    s.sign_in()
                    page_text = p.evaluate(HEX_OF, '.lead,.hint,.sect,.sect2,#foot,#mine .pname,#mine .none,#copiesNote,#keepRow label,.btn,h1')
                    check(len(page_text) >= 6, 'the page\'s text was measured (%d)' % len(page_text))
                    for cls, fg, bg in page_text:
                        check(ratio(fg, bg) >= 4.5, '%s is %.2f:1 in %s at %d px' % (cls, ratio(fg, bg), scheme, w))
                    s.pick('quarterly-statement-for-the-tax-year-2026-final-version.pdf', FORM_PDF, 'application/pdf')
                    s.signer_ready(1)
                    sw = p.evaluate('Math.max(document.documentElement.scrollWidth,document.querySelector("#signer").scrollWidth)')
                    check(sw <= w, 'the signer fits %d px (%d)' % (w, sw))
                    for cls, fg, bg in p.evaluate(HEX_OF, '#signer .sbar button,#signer .sbar label,#signer #sMsg,#signer #sTitle'):
                        check(ratio(fg, bg) >= 4.5, 'signer: %s is %.2f:1 in %s at %d px' % (cls, ratio(fg, bg), scheme, w))
                    for cls, fg, bg in p.evaluate(HEX_OF, '.btn.primary'):
                        check(ratio(fg, bg) >= 4.5, 'a main button is %.2f:1 in %s' % (ratio(fg, bg), scheme))
                    s.tool('sig')
                    check(wait_until(lambda: s.visible('#sigPad'), 3), 'the signature pad opens')
                    check(p.evaluate('document.documentElement.scrollWidth') <= w, 'and fits %d px' % w)
                    for cls, fg, bg in p.evaluate(HEX_OF, '#sigPad button,#sigPad p,#sigPad h2'):
                        check(ratio(fg, bg) >= 4.5, 'signature pad: %s is %.2f:1 in %s at %d px' % (cls, ratio(fg, bg), scheme, w))
                    p.click('#sigCancel')
                    p.click('#sCancel')
                    check(wait_until(lambda: not s.visible('#signer'), 3), 'Cancel closes the signer')
                run_section('layout %s %dpx' % (scheme, w), one)
                s.report()
            finally:
                s.close()


# ------------------------------------------------------------------ the source page, no Drive at all
def source_page(browser):
    httpd, base = dt.serve(ROOT)   # the source loads ../docs/sign.js, so serve the whole repo
    s = Desk(browser, base, None)
    try:
        def one():
            s.open('site/sign-desk.html')
            check('this browser' in s.page.locator('#foot').text_content(), 'it says it saves only in this browser (%r)' % s.page.locator('#foot').text_content())
            s.pick('form.pdf', FORM_PDF, 'application/pdf')
            s.signer_ready(1)
            check(not s.visible('#sDrive'), 'no Google Drive copy to offer')
            s.tool('sig')
            check(wait_until(lambda: s.visible('#sigPad'), 3), 'the pad opens')
            s.stroke('#sigCanvas', [[0.1, 0.5], [0.9, 0.5]], 'mouse')
            s.page.click('#sigUse')
            s.stroke('#signer .spage[data-i="1"]', [[0.5, 0.5]], 'mouse')
            check(wait_until(lambda: s.marks() == 1, 3), 'the signature is placed')
            with s.page.expect_download(timeout=20000) as dl:
                s.page.click('#sSave')
            check(open(dl.value.path(), 'rb').read()[:5] == b'%PDF-', 'and the signed PDF downloads')
            s.page.reload()
            s.page.locator('#pickBtn').wait_for(state='visible')
            check(wait_until(lambda: s.page.locator('#mine .pic img').count() == 1, 3), 'the signature is still there after a reload, in this browser')
        run_section('site/: the source page, no Drive', one)
        s.report()
    finally:
        s.close()
        httpd.shutdown()


def main():
    docs = os.path.join(ROOT, 'docs')
    if not os.path.exists(os.path.join(docs, dt.PAGE)):
        print('docs/%s does not exist yet; run tools/build-site.py' % dt.PAGE)
        sys.exit(1)
    if not os.path.isdir(os.path.join(HOME_HUB, 'web', 'vendor')):
        SKIPS.append('Home Hub is not beside this repo, so PDF.js, pdf-lib and the font came from the internet')
    print('== the signer')
    same_signer()
    httpd, base = dt.serve(docs)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=not HEADED)
        for label, flow in (('a laptop and a phone', laptop_and_phone), ('layout', layout)):
            print('==', label)
            flow(browser, base)
        print('== site/')
        source_page(browser)
        browser.close()
    httpd.shutdown()
    for s in SKIPS:
        print('  SKIPPED  ' + s)
    print('\n%d checks passed, %d failed' % (dt.PASSES[0], len(dt.FAILS)))
    for x in dt.FAILS:
        print('  ' + x)
    sys.exit(1 if dt.FAILS else 0)


if __name__ == '__main__':
    main()
