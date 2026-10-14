"""Drop Tray checker: drives the page in headless Chrome as two laptops sharing one fake Google Drive.

  python tools/check-drop-tray.py            # docs/drop-tray.html (built), then site/drop-tray.html
  python tools/check-drop-tray.py --headed   # watch it

Nothing here can reach the real Google Drive. Sign-in is a stub of Google's script that hands out fake
tokens, Drive is an in-memory fake (FakeDrive below) answering every request the page makes, and any
other request to a Google host is aborted and reported. Two browser contexts on the same FakeDrive are
two laptops: what one sends, the other must show, copy and download.

Exit code 0 means every check passed; each failure is printed with the section it came from.
"""
import datetime, functools, hashlib, http.server, json, os, re, socket, struct, sys, threading, time, urllib.parse, zlib
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # the Windows console is cp1252; page text has arrows and dots
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEADED = '--headed' in sys.argv
PAGE = 'drop-tray.html'
DOC = 'drop-tray.json'
SUB = 'Drop Tray'
FOLDER = 'application/vnd.google-apps.folder'
FAILS, PASSES = [], [0]
SECTION = ['']
MB = 1024 * 1024


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
        if os.environ.get('DROPTRAY_TRACE'):
            import traceback
            traceback.print_exc()


PAGES = []  # open pages: waiting on any of them keeps Playwright answering every page's requests to the fake Drive


def nap(ms):
    """Wait without blocking Playwright. A plain time.sleep stops the route handlers, so the pages'
    requests to the fake Drive would hang until the sleep ends."""
    if PAGES:
        PAGES[-1].wait_for_timeout(ms)
    else:
        time.sleep(ms / 1000.0)


def wait_until(fn, timeout=8.0, step=0.08):
    end = time.time() + timeout
    while True:
        v = fn()
        if v or time.time() > end:
            return v
        nap(step * 1000)


# ------------------------------------------------------------------ sample content
def png(w, h, rgb):
    raw = b''.join(b'\x00' + bytes(rgb) * w for _ in range(h))

    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def pattern(size, seed=7):
    """The same bytes the page makes with makeFile() below, so a download can be compared byte for byte."""
    return bytes(((i * 31 + seed) & 255) for i in range(size))


# a File built inside the page from a size and a seed, so megabytes never cross the wire as base64
MAKE_FILE = """function makeFile(f){
  if(f.b64){var bin=atob(f.b64),u=new Uint8Array(bin.length);for(var i=0;i<bin.length;i++)u[i]=bin.charCodeAt(i);return new File([u],f.name,{type:f.type||''});}
  var a=new Uint8Array(f.size);for(var j=0;j<f.size;j++)a[j]=(j*31+(f.seed==null?7:f.seed))&255;return new File([a],f.name,{type:f.type||''});}"""


# ------------------------------------------------------------------ fake Google
GIS_STUB = r"""(function(){
  var n=0;
  window.google={accounts:{oauth2:{
    initTokenClient:function(cfg){
      window.__gis={scope:cfg.scope,client_id:cfg.client_id,requests:0};
      return {callback:cfg.callback,error_callback:cfg.error_callback,
        requestAccessToken:function(){var self=this;window.__gis.requests++;
          setTimeout(function(){
            if(window.__gisDeny){self.callback({error:'access_denied'});return;}
            self.callback({access_token:'fake-token-'+(++n)+'-'+Math.random().toString(36).slice(2),expires_in:3599,scope:cfg.scope,token_type:'Bearer'});
          },20);}};
    },
    revoke:function(t,cb){if(cb)cb({});}
  }}};
})();"""
CORS = {'Access-Control-Allow-Origin': '*', 'Access-Control-Expose-Headers': 'Location, Content-Length, Content-Type',
        'Access-Control-Allow-Headers': 'authorization, content-type, x-upload-content-type, x-upload-content-length, content-range',
        'Access-Control-Allow-Methods': 'GET, POST, PATCH, PUT, OPTIONS'}


def split_top(s, sep):
    parts, depth, quoted, cur, i = [], 0, False, '', 0
    while i < len(s):
        c = s[i]
        if c == '\\' and quoted:
            cur += s[i:i + 2]; i += 2; continue
        if c == "'":
            quoted = not quoted
        elif not quoted and c == '(':
            depth += 1
        elif not quoted and c == ')':
            depth -= 1
        if not quoted and depth == 0 and s.startswith(sep, i):
            parts.append(cur); cur = ''; i += len(sep); continue
        cur += c; i += 1
    parts.append(cur)
    return [p.strip() for p in parts]


def unq(s):
    assert s[0] == "'" and s[-1] == "'", 'unquoted value in query: ' + s
    return re.sub(r'\\(.)', r'\1', s[1:-1])


def iso(t):
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(t)) + '.%03dZ' % int((t % 1) * 1000)


class FakeDrive:
    """An in-memory Drive with the routes drive-sync.js uses: folders, the JSON file, and ordinary files."""

    def __init__(self):
        self.files, self.n, self.calls, self.uploads, self.deletes, self.dead, self.seen = {}, 0, [], [], [], set(), set()
        self.sessions, self.media, self.held = {}, [], []
        self.flags = {'resumable': 'ok', 'multipart': 'ok', 'hold_put': False}
        self.root = self.add('My Drive', FOLDER, None, fid='root-id')

    # ---- state helpers
    def stamp(self, at=None):
        self.n += 1
        return iso((at if at is not None else time.time()) + self.n / 1000.0)

    def add(self, name, mime, parent, content=b'', fid=None, at=None):
        fid = fid or 'id%04d' % (len(self.files) + 1)
        t = self.stamp(at)
        self.files[fid] = {'id': fid, 'name': name, 'mimeType': mime, 'parents': [parent] if parent else [],
                           'trashed': False, 'createdTime': t, 'modifiedTime': t, 'content': content}
        return fid

    def live(self, name=None, parent=None):
        return [f for f in self.files.values() if not f['trashed'] and (name is None or f['name'] == name)
                and (parent is None or parent in f['parents'])]

    def folder(self, name=SUB):
        got = [f for f in self.live(name) if f['mimeType'] == FOLDER]
        return got[0]['id'] if got else None

    def in_tray(self):
        fid = self.folder()
        return [f for f in self.files.values() if fid and fid in f['parents']] if fid else []

    def doc(self):
        got = self.live(DOC)
        if not got:
            return None
        try:
            return json.loads(got[0]['content'] or b'{}')
        except ValueError:
            return None

    def drops(self):
        d = self.doc() or {}
        return [it for it in (d.get('items') or {}).values() if it and it.get('kind') == 'drop' and not it.get('deleted')]

    def kill_tokens(self):
        self.dead |= self.seen

    def token_calls(self, tok, pred=lambda c: True):
        return [c for c in self.calls if c[3] == tok and pred(c)]

    # ---- JSON shape of a file, cut down to the requested fields
    def view(self, f):
        o = {k: f[k] for k in ('id', 'name', 'mimeType', 'parents', 'trashed', 'createdTime', 'modifiedTime')}
        o['kind'] = 'drive#file'
        if f['mimeType'] != FOLDER:
            o['size'] = str(len(f['content']))
        return o

    @staticmethod
    def cut(o, spec):
        if not spec or spec == '*':
            return o
        names = [p.split('(')[0].strip() for p in split_top(spec, ',') if p.strip()]
        return {k: o[k] for k in names if k in o}

    @staticmethod
    def files_spec(fields):
        m = re.search(r'files\((.*)\)', fields or '')
        return m.group(1) if m else 'id,name,mimeType,kind'

    # ---- queries
    def match(self, q, f):
        q = q.strip()
        ands = split_top(q, ' and ')
        if len(ands) > 1:
            return all(self.match(a, f) for a in ands)
        if q.startswith('(') and q.endswith(')'):
            return self.match(q[1:-1], f)
        m = re.fullmatch(r"('(?:[^'\\]|\\.)*')\s+in\s+parents", q)
        if m:
            pid = unq(m.group(1))
            return (self.root if pid == 'root' else pid) in f['parents']
        m = re.fullmatch(r'trashed\s*=\s*(true|false)', q)
        if m:
            return f['trashed'] == (m.group(1) == 'true')
        m = re.fullmatch(r"(name|mimeType)\s*(=|!=)\s*('(?:[^'\\]|\\.)*')", q)
        if m:
            val, v = f[m.group(1)], unq(m.group(3))
            return val == v if m.group(2) == '=' else val != v
        raise AssertionError('fake drive does not understand query clause: ' + q)

    @staticmethod
    def order(rows, spec):
        for k in reversed([k.strip() for k in (spec or 'name').split(',') if k.strip()]):
            name, _, d = k.partition(' ')
            if name not in ('name', 'modifiedTime', 'createdTime'):
                raise AssertionError('fake drive does not know orderBy key ' + k)
            rows.sort(key=lambda f: f[name], reverse=d.strip() == 'desc')
        return rows

    # ---- HTTP plumbing
    def reply(self, route, status, body=b'', ctype='application/json', headers=None):
        h = dict(CORS)
        h.update(headers or {})
        route.fulfill(status=status, body=body, content_type=ctype, headers=h)

    def json(self, route, status, obj):
        self.reply(route, status, json.dumps(obj).encode())

    def err(self, route, status, msg):
        self.json(route, status, {'error': {'code': status, 'message': msg, 'errors': [{'reason': 'fake', 'message': msg}]}})

    def api(self, route, req):
        u = urllib.parse.urlsplit(req.url)
        path, qs, m = u.path, dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True)), req.method
        if m == 'OPTIONS':
            return self.reply(route, 204)
        tok = (req.headers.get('authorization') or '')[7:]
        self.calls.append((m, path, qs, tok))
        if m == 'DELETE':
            self.deletes.append(req.url)
            return self.reply(route, 204)
        if m == 'PUT' and path == '/upload/drive/v3/files' and qs.get('upload_id'):
            return self.put_session(route, req, qs['upload_id'])
        if not tok.startswith('fake-token-') or tok in self.dead:
            return self.err(route, 401, 'Request had invalid authentication credentials.')
        self.seen.add(tok)
        body = req.post_data_buffer or b''
        if path == '/oauth2/v3/userinfo':
            return self.json(route, 200, {'email': 'tester@example.com'})
        if path == '/drive/v3/files' and m == 'GET':
            rows = [f for f in self.files.values() if f['id'] != self.root and self.match(qs.get('q') or 'trashed=false', f)]
            rows = self.order(rows, qs.get('orderBy'))
            size, start = int(qs.get('pageSize') or 100), int(qs.get('pageToken') or 0)
            out = {'files': [self.cut(self.view(f), self.files_spec(qs.get('fields'))) for f in rows[start:start + size]]}
            if start + size < len(rows):
                out['nextPageToken'] = str(start + size)
            return self.json(route, 200, out)
        if path == '/drive/v3/files' and m == 'POST':
            meta = json.loads(body or b'{}')
            fid = self.add(meta['name'], meta.get('mimeType') or 'application/octet-stream', (meta.get('parents') or [self.root])[0])
            return self.json(route, 200, self.cut(self.view(self.files[fid]), qs.get('fields') or 'id,name,mimeType,kind'))
        mm = re.fullmatch(r'/(upload/)?drive/v3/files/([^/]+)', path)
        if mm:
            f = self.files.get(mm.group(2))
            if not f:
                return self.err(route, 404, 'File not found: ' + mm.group(2))
            if mm.group(1):   # PATCH /upload/...?uploadType=media: the JSON file's new content
                if m != 'PATCH' or qs.get('uploadType') != 'media':
                    return self.err(route, 400, 'fake drive: unexpected upload call')
                f['content'], f['modifiedTime'] = body, self.stamp()
                return self.json(route, 200, self.cut(self.view(f), qs.get('fields') or 'id'))
            if m == 'GET' and qs.get('alt') == 'media':
                self.media.append(f['id'])
                return self.reply(route, 200, f['content'], f['mimeType'])
            if m == 'GET':
                return self.json(route, 200, self.cut(self.view(f), qs.get('fields') or 'id,name,mimeType,kind'))
            if m == 'PATCH':
                meta = json.loads(body or b'{}')
                if 'name' in meta:
                    f['name'] = meta['name']
                if 'trashed' in meta:
                    f['trashed'] = bool(meta['trashed'])
                f['modifiedTime'] = self.stamp()
                return self.json(route, 200, self.cut(self.view(f), qs.get('fields') or 'id'))
        if path == '/upload/drive/v3/files' and m == 'POST' and qs.get('uploadType') == 'multipart':
            if self.flags['multipart'] == 'fail':
                return self.err(route, 500, 'Backend Error')
            ct = req.headers.get('content-type') or ''
            b = re.search(r'boundary="?([^";]+)"?', ct).group(1).encode()
            parts = [p for p in body.split(b'--' + b) if p.strip(b'\r\n-')]
            _, meta = parts[0].split(b'\r\n\r\n', 1)
            hd2, data = parts[1].split(b'\r\n\r\n', 1)
            meta, data = json.loads(meta.strip()), data[:-2] if data.endswith(b'\r\n') else data
            mime = re.search(rb'Content-Type:\s*([^\r\n;]+)', hd2, re.I)
            fid = self.add(meta['name'], meta.get('mimeType') or (mime.group(1).decode() if mime else 'application/octet-stream'),
                           (meta.get('parents') or [self.root])[0], data)
            self.uploads.append(('multipart', meta['name']))
            return self.json(route, 200, self.cut(self.view(self.files[fid]), qs.get('fields') or 'id,name,mimeType,kind'))
        if path == '/upload/drive/v3/files' and m == 'POST' and qs.get('uploadType') == 'resumable':
            meta = json.loads(body or b'{}')
            sid = 'sess%d' % (len(self.sessions) + 1)
            self.sessions[sid] = {'meta': meta, 'mime': req.headers.get('x-upload-content-type'), 'fields': qs.get('fields')}
            loc = 'https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&upload_id=' + sid
            return self.reply(route, 200, b'', headers={'Location': loc})
        return self.err(route, 400, 'fake drive has no route for %s %s' % (m, path))

    def put_session(self, route, req, sid):
        s = self.sessions.get(sid)
        if not s:
            return self.err(route, 404, 'no such upload session')
        if self.flags['hold_put']:   # the bytes are on their way; the test decides when (or whether) they land
            self.held.append(route)
            return
        mode = self.flags['resumable']
        if mode == 'preflight_blocked':   # the browser never gets to send the bytes
            return route.abort('failed')
        meta = s['meta']
        fid = self.add(meta['name'], meta.get('mimeType') or s['mime'] or 'application/octet-stream',
                       (meta.get('parents') or [self.root])[0], req.post_data_buffer or b'')
        self.uploads.append(('resumable', meta['name']))
        if mode == 'response_unreadable':   # Drive stored the file, but the browser hides the answer
            return route.abort('failed')
        return self.reply(route, 200, json.dumps(self.cut(self.view(self.files[fid]), s['fields'] or 'id,name')).encode())

    def release(self):
        for r in self.held:
            try:
                r.abort('failed')
            except Exception:
                pass
        self.held = []


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


# Headless pages always have focus and are never hidden, so a laptop "looking elsewhere" is pretended: __blur
# makes document.hasFocus() false (another window in front), __hide also makes the page hidden (another tab, or
# minimised). Notifications are recorded in __shown instead of being shown.
AWAY_HOOKS = r"""(function(){
  var hf=Document.prototype.hasFocus;
  Document.prototype.hasFocus=function(){return window.__blur||window.__hide?false:hf.call(this);};
  Object.defineProperty(Document.prototype,'hidden',{configurable:true,get:function(){return !!window.__hide;}});
  Object.defineProperty(Document.prototype,'visibilityState',{configurable:true,get:function(){return window.__hide?'hidden':'visible';}});
  window.__shown=[];
  if(window.ServiceWorkerRegistration)ServiceWorkerRegistration.prototype.showNotification=function(t,o){window.__shown.push({title:t,body:o&&o.body,via:'sw'});return Promise.resolve();};
  if(window.Notification)window.Notification=new Proxy(window.Notification,{construct:function(T,a){window.__shown.push({title:a[0],body:a[1]&&a[1].body,via:'page'});return {close:function(){}};}});
})();"""


class Session:
    """One browser context (one laptop) wired to a fake Drive."""

    def __init__(self, browser, base, fake, device=None, viewport=(1280, 900), clock=None, prefs=None):
        self.fake, self.blocked, self.errors, self.base = fake, [], [], base
        self.ctx = browser.new_context(viewport={'width': viewport[0], 'height': viewport[1]}, accept_downloads=True)
        self.ctx.grant_permissions(['clipboard-read', 'clipboard-write', 'notifications'], origin=base.rstrip('/'))
        self.ctx.add_init_script(AWAY_HOOKS)
        if device:
            self.ctx.add_init_script("try{if(!localStorage.getItem('droptray.device'))localStorage.setItem('droptray.device',%s);}catch(e){}" % json.dumps(device))
        for k, v in (prefs or {}).items():
            self.ctx.add_init_script("try{if(localStorage.getItem(%s)==null)localStorage.setItem(%s,%s);}catch(e){}" % (json.dumps(k), json.dumps(k), json.dumps(v)))
        self.ctx.route('**/*', self.route)
        self.page = self.ctx.new_page()
        PAGES.append(self.page)
        self.page.set_default_timeout(8000)
        if clock is not None:
            self.page.clock.install(time=clock)
        self.page.on('pageerror', lambda e: self.errors.append('pageerror: ' + str(e)))
        self.page.on('console', self.console)

    def console(self, msg):
        if msg.type == 'error' and not re.search(r'Failed to load resource|net::ERR_|CORS policy', msg.text):
            self.errors.append('console: ' + msg.text[:300])

    def route(self, route, req):
        u = urllib.parse.urlsplit(req.url)
        host = u.hostname or ''
        try:
            if host in ('127.0.0.1', 'localhost') or u.scheme in ('blob', 'data'):
                return route.continue_()
            if req.url.startswith('https://accounts.google.com/gsi/client'):
                return route.fulfill(status=200, body=GIS_STUB, content_type='text/javascript')
            if host == 'www.googleapis.com' and self.fake is not None:
                return self.fake.api(route, req)
            self.blocked.append(req.url)
            return route.abort()
        except Exception as e:  # a crash in the fake must show up as a failure, not a hang
            self.errors.append('fake drive crashed on %s %s: %r' % (req.method, req.url, e))
            try:
                route.fulfill(status=500, body=b'{"error":{"code":500,"message":"fake crashed"}}', content_type='application/json', headers=CORS)
            except Exception:
                pass

    def close(self):
        if self.fake:
            self.fake.release()
        if self.page in PAGES:
            PAGES.remove(self.page)
        self.ctx.close()

    # ---- page helpers
    def open(self):
        self.page.goto(self.base + PAGE)
        self.page.locator('#list').wait_for(state='attached')

    def signed(self):
        return self.page.evaluate("document.querySelector('#sync').dataset.state==='saved'")

    def sign_in(self):
        """Press Continue with Google, unless a sign-in started some other way gets there first."""
        p = self.page
        if not wait_until(self.signed, 0.3):
            b = p.locator('#sync .ds-google')
            try:
                b.click(timeout=3000)
            except Exception:
                pass   # it went away: the sign-in finished on its own
        p.wait_for_function("document.querySelector('#sync').dataset.state==='saved'", timeout=10000)

    def gis_requests(self):
        return self.page.evaluate('window.__gis&&window.__gis.requests||0')

    def away(self, hide=False):
        """Look elsewhere: another window in front (hide=False), or another tab or minimised (hide=True)."""
        self.page.evaluate("(h)=>{window.__blur=true;window.__hide=h;window.dispatchEvent(new Event('blur'));if(h)document.dispatchEvent(new Event('visibilitychange'));}", hide)

    def back(self):
        """Come back to the page: it is shown, then gets the focus, as when you click into it."""
        self.page.evaluate("()=>{var h=window.__hide;window.__blur=false;window.__hide=false;if(h)document.dispatchEvent(new Event('visibilitychange'));window.dispatchEvent(new Event('focus'));}")

    def shown(self):
        return self.page.evaluate('window.__shown')

    def set_clip(self, text):
        self.page.evaluate('(t)=>navigator.clipboard.writeText(t)', text)

    def nap_until_refused(self):
        """The stub answers a sign-in 20 ms after it is asked; wait for that answer before changing what it says."""
        nap(250)

    def token(self):
        t = self.page.evaluate("localStorage.getItem('drivesync.token')")
        return (json.loads(t) or {}).get('token') if t else None

    def drops(self):
        return self.page.eval_on_selector_all('.drop', """els=>els.map(e=>({id:e.dataset.id,kind:e.dataset.kind,from:e.dataset.from,
            pinned:!!e.closest('#pinned'),text:(e.querySelector('pre.txt')||{}).textContent,name:(e.querySelector('.fname')||{}).textContent,
            fromText:(e.querySelector('.from')||{}).textContent}))""")

    def wait_drop(self, pred, timeout=8.0, what='a drop'):
        got = wait_until(lambda: next((d for d in self.drops() if pred(d)), None), timeout)
        check(got is not None, 'never showed %s; the list has %s' % (what, [(d['kind'], (d['text'] or d['name'] or '')[:30]) for d in self.drops()][:8]))
        return got

    def wait_gone(self, pred, timeout=8.0, what='a drop'):
        ok = wait_until(lambda: not any(pred(d) for d in self.drops()), timeout)
        check(ok, 'still shows %s' % what)
        return ok

    def card(self, did):
        return self.page.locator('.drop[data-id="%s"]' % did)

    def act(self, did, act):
        self.card(did).locator('[data-act="%s"]' % act).click()

    def paste(self, text=None, files=None, target='body'):
        self.page.evaluate("""(a)=>{%s
            var dt=new DataTransfer();if(a.text!=null)dt.setData('text/plain',a.text);(a.files||[]).forEach(function(f){dt.items.add(makeFile(f));});
            var t=document.querySelector(a.target)||document.body;
            t.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));}""" % MAKE_FILE,
                           {'text': text, 'files': files or [], 'target': target})

    def drag_drop(self, files):
        return self.page.evaluate("""(a)=>{%s
            var dt=new DataTransfer();a.files.forEach(function(f){dt.items.add(makeFile(f));});
            var t=document.querySelector('#list');
            t.dispatchEvent(new DragEvent('dragenter',{dataTransfer:dt,bubbles:true,cancelable:true}));
            t.dispatchEvent(new DragEvent('dragover',{dataTransfer:dt,bubbles:true,cancelable:true}));
            var z=document.querySelector('#dropzone'),shown=!!z&&!z.hidden&&getComputedStyle(z).display!=='none';
            t.dispatchEvent(new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true}));
            return {shown:shown,after:!!z&&!z.hidden};}""" % MAKE_FILE, {'files': files})

    def pick(self, name, data, mime='application/octet-stream'):
        self.page.set_input_files('#fileInput', files=[{'name': name, 'mimeType': mime, 'buffer': data}])

    def wait_up(self, name, state='done', timeout=20000):
        self.page.locator('#tray .up[data-name="%s"][data-state="%s"]' % (name, state)).wait_for(state='attached', timeout=timeout)

    def set_box(self, text):
        self.page.evaluate("(v)=>{var t=document.querySelector('#text');t.focus();t.value=v;t.dispatchEvent(new Event('input',{bubbles:true}));}", text)

    def keep(self, value):
        """Choose a clear-after setting; it sits in the Settings fold, which starts closed."""
        if not self.page.evaluate("document.querySelector('#settings').open"):
            self.page.locator('#settings summary').click()
        self.page.select_option('#keepSel', value)

    def clip_text(self):
        """What the clipboard holds. The Windows clipboard keeps line ends as CRLF (Chrome converts on the
        way in), so they are read back as LF; everything else must match byte for byte."""
        return self.page.evaluate('navigator.clipboard.readText()').replace('\r\n', '\n')

    def clip_types(self):
        return self.page.evaluate("navigator.clipboard.read().then(function(items){var t=[];items.forEach(function(i){t=t.concat(i.types);});return t;})")

    def focus(self):
        self.page.evaluate("window.dispatchEvent(new Event('focus'))")

    def toast(self):
        return self.page.evaluate("(function(){var t=document.querySelector('#toast');return t&&!t.hidden?t.textContent:'';})()")

    def download(self, did):
        with self.page.expect_download() as dl:
            self.act(did, 'download')
        p = dl.value.path()
        return dl.value.suggested_filename, open(p, 'rb').read()

    def ls(self, key):
        return self.page.evaluate('(k)=>localStorage.getItem(k)', key)

    def report(self):
        for e in self.errors:
            check(False, 'browser error: ' + e)
        odd = [u for u in self.blocked if not re.match(r'https://fonts\.(googleapis|gstatic)\.com/', u)]
        check(not odd, 'no request left for an unexpected host: %s' % odd[:3])


def doc_has(fake, pred, timeout=6.0):
    return wait_until(lambda: next((d for d in fake.drops() if pred(d)), None), timeout)


# ------------------------------------------------------------------ two laptops
def two_laptops(browser, base):
    fake = FakeDrive()
    A = Session(browser, base, fake, device='Home laptop')
    B = Session(browser, base, fake, device='Work laptop')
    st = {}
    try:
        def boot():
            for s in (A, B):
                s.open()
                s.sign_in()
            check(A.page.locator('#deviceName').text_content().strip() == 'Home laptop', 'A knows it is the Home laptop')
            check(B.page.locator('#deviceName').text_content().strip() == 'Work laptop', 'B knows it is the Work laptop')
            check(not A.drops() and A.page.locator('#empty').is_visible(), 'an empty tray says so')
        run_section('two laptops: sign in', boot)

        def text_across():
            code = 'def hello():\n\tprint("hi")   \n    return 42\n'
            A.paste(text=code)
            a = A.wait_drop(lambda d: d['text'] == code, what='the pasted code on A')
            check(a and a['kind'] == 'code', 'pasted code is labelled as code (%s)' % (a and a['kind']))
            rec = doc_has(fake, lambda d: d.get('text') == code)
            check(rec is not None, 'the code reached drop-tray.json in Drive')
            if rec:
                check(rec.get('from') == 'Home laptop' and rec.get('type') == 'text', 'the record says text, from Home laptop (%s)' % {k: rec.get(k) for k in ('type', 'from')})
            t0 = time.time()
            b = B.wait_drop(lambda d: d['text'] == code, timeout=12.0, what='the code on B (by the 8 s poll)')
            dt = time.time() - t0
            check(dt <= 10.0, 'B picked it up within 10 s without being touched (%.1f s)' % dt)
            if b:
                check(b['from'] == 'Home laptop' and 'Home laptop' in (b['fromText'] or ''), 'B says it came from Home laptop (%s)' % b['fromText'])
                check('this device' in (a['fromText'] or ''), 'A says it came from this device (%s)' % a['fromText'])
                B.act(b['id'], 'copy')
                got = wait_until(lambda: B.clip_text() == code, 3)
                check(got, 'B copies exactly the bytes A pasted, tab and trailing spaces included (%r)' % B.clip_text())
                st['code'] = b['id']
        run_section('text: paste on A, copy on B', text_across)

        def focus_pickup():
            tok = B.token()
            polls = lambda: len(fake.token_calls(tok, lambda c: c[0] == 'GET' and c[1].startswith('/drive/v3/files/')))
            n0 = polls()
            check(wait_until(lambda: polls() > n0, 10), 'B checks Drive on its own every few seconds')
            A.paste(text='picked up on focus')
            check(doc_has(fake, lambda d: d.get('text') == 'picked up on focus') is not None, 'A saved it')
            B.focus()
            t0 = time.time()
            got = B.wait_drop(lambda d: d['text'] == 'picked up on focus', timeout=2.5, what='the drop right after focus')
            check(got and time.time() - t0 < 2.5, 'clicking into B picks it up at once, not at the next poll')
        run_section('text: clicking into the other laptop picks it up', focus_pickup)

        def compose():
            A.page.fill('#text', 'typed in the box')
            A.page.press('#text', 'Control+Enter')
            A.wait_drop(lambda d: d['text'] == 'typed in the box', what='the Ctrl+Enter drop')
            check(A.page.input_value('#text') == '', 'the box empties after sending')
            A.page.fill('#text', 'sent with the button')
            A.page.click('#sendBtn')
            A.wait_drop(lambda d: d['text'] == 'sent with the button', what='the Send button drop')
            A.page.fill('#text', '   \n  ')
            n = len(A.drops())
            A.page.click('#sendBtn')
            A.page.wait_for_timeout(300)
            check(len(A.drops()) == n, 'blank text is not sent')
            # pasting into the box only fills the box; it sends nothing until you press Send
            A.page.fill('#text', '')
            A.page.focus('#text')
            n = len(A.drops())
            A.paste(text='edit me first', target='#text')
            A.page.wait_for_timeout(300)
            check(len(A.drops()) == n, 'a paste into the box waits for Send')
            url = 'https://example.com/a/b?q=1#x'
            A.paste(text=url)
            d = A.wait_drop(lambda d: d['text'] == url, what='the link')
            if d:
                check(d['kind'] == 'link', 'a single web address is labelled as a link (%s)' % d['kind'])
                a = A.card(d['id']).locator('a[data-act="open"]')
                check(a.count() == 1 and a.get_attribute('href') == url and a.get_attribute('target') == '_blank'
                      and 'noopener' in (a.get_attribute('rel') or ''), 'a link drop has Open, in a new tab, with noopener')
            A.paste(text='javascript:alert(1)')
            d = A.wait_drop(lambda d: d['text'] == 'javascript:alert(1)', what='a javascript: text')
            if d:
                check(A.card(d['id']).locator('a[data-act="open"]').count() == 0, 'a javascript: text never becomes a link')
            long = '\n'.join('line %d of thirty' % i for i in range(1, 31))
            A.paste(text=long)
            d = A.wait_drop(lambda d: (d['text'] or '').startswith('line 1 of thirty'), what='the 30-line drop')
            if d:
                shown = (d['text'] or '').rstrip('\n').split('\n')
                check(len(shown) == 12, 'a long text shows its first 12 lines (%d)' % len(shown))
                more = A.card(d['id']).locator('[data-act="more"]')
                check(more.count() == 1 and 'Show all 30 lines' in more.text_content(), 'and offers Show all 30 lines')
                more.click()
                d2 = next(x for x in A.drops() if x['id'] == d['id'])
                check(d2['text'] == long, 'Show all shows every line')
                A.act(d['id'], 'copy')
                check(wait_until(lambda: A.clip_text() == long, 3), 'Copy copies all 30 lines, folded or not')
            wide = 'x' * 900
            A.paste(text=wide)
            d = A.wait_drop(lambda d: (d['text'] or '').startswith('xxxx'), what='the 900-character line')
            check(A.page.evaluate('document.documentElement.scrollWidth') <= 1280, 'a 900-character line wraps instead of widening the page')
        run_section('text: the box, Ctrl+Enter, links and long text', compose)

        def image_across():
            pic = png(40, 30, (30, 120, 220))
            A.paste(files=[{'name': 'image.png', 'type': 'image/png', 'b64': __import__('base64').b64encode(pic).decode()}])
            a = A.wait_drop(lambda d: d['kind'] == 'image', timeout=10, what='the pasted screenshot on A')
            got = [f for f in fake.in_tray() if f['mimeType'] == 'image/png']
            check(len(got) == 1 and got[0]['content'] == pic, 'the screenshot is a file in Personal Apps/Drop Tray')
            if got:
                check(re.fullmatch(r'pasted image \d{4}-\d\d-\d\d \d\d\.\d\d\.\d\d\.png', got[0]['name']) is not None, 'a pasted screenshot gets a dated name (%s)' % got[0]['name'])
                parent = [f for f in fake.files.values() if f['id'] == got[0]['parents'][0]][0]
                app = [f for f in fake.files.values() if f['id'] == parent['parents'][0]]
                check(parent['name'] == SUB and app and app[0]['name'] == 'Personal Apps', 'Drop Tray sits inside Personal Apps')
            B.focus()
            b = B.wait_drop(lambda d: d['kind'] == 'image', what='the screenshot on B')
            if b:
                ok = B.page.wait_for_function("(id)=>{var i=document.querySelector('.drop[data-id=\"'+id+'\"] img.preview');return i&&i.complete&&i.naturalWidth===40;}", arg=b['id'], timeout=8000)
                check(bool(ok), 'B shows the screenshot itself')
                B.act(b['id'], 'copyimg')
                check(wait_until(lambda: 'image/png' in B.clip_types(), 4), 'B copies it to the clipboard as an image (%s)' % B.clip_types())
                st['png'] = b['id']
            # a JPEG made in A's own canvas: Chrome can only put PNG on the clipboard, so B converts it
            A.page.evaluate("""()=>new Promise(function(res){var c=document.createElement('canvas');c.width=50;c.height=20;var x=c.getContext('2d');x.fillStyle='#c33';x.fillRect(0,0,50,20);
                c.toBlob(function(b){var dt=new DataTransfer();dt.items.add(new File([b],'photo.jpg',{type:'image/jpeg'}));
                document.body.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));res();},'image/jpeg',0.9);})""")
            j = A.wait_drop(lambda d: d['kind'] == 'image' and d['name'] == 'photo.jpg', timeout=10, what='the JPEG on A')
            B.focus()
            jb = B.wait_drop(lambda d: d['kind'] == 'image' and d['name'] == 'photo.jpg', what='the JPEG on B')
            if jb:
                B.page.evaluate('navigator.clipboard.writeText("")')
                B.act(jb['id'], 'copyimg')
                check(wait_until(lambda: 'image/png' in B.clip_types(), 4), 'a JPEG is copied as PNG (%s)' % B.clip_types())
                name, data = B.download(jb['id'])
                check(name == 'photo.jpg' and data[:2] == b'\xff\xd8', 'Download gives the JPEG itself (%s)' % name)
        run_section('images: paste a screenshot on A, see and copy it on B', image_across)

        def files_across():
            one = pattern(1 * MB, seed=3)
            A.pick('notes 2026.zip', one, 'application/zip')
            A.wait_up('notes 2026.zip')
            check(('multipart', 'notes 2026.zip') in fake.uploads, 'a 1 MB file goes up in one request')
            r = A.drag_drop([{'name': 'big video.mp4', 'type': 'video/mp4', 'size': 6 * MB, 'seed': 9}])
            check(r['shown'], 'dragging files over the page shows where to drop them')
            check(not r['after'], 'and the hint goes away after the drop')
            A.wait_up('big video.mp4', timeout=30000)
            check(('resumable', 'big video.mp4') in fake.uploads, 'a 6 MB file uses the resumable upload')
            for name in ('notes 2026.zip', 'big video.mp4'):
                check(len([f for f in fake.in_tray() if f['name'] == name]) == 1, 'exactly one copy of %s in Drive' % name)
                rec = doc_has(fake, lambda d, n=name: d.get('name') == n)
                f = [x for x in fake.in_tray() if x['name'] == name]
                check(rec is not None and f and rec.get('fileId') == f[0]['id'], 'the %s record points at its Drive file' % name)
            B.focus()
            for name, data in (('notes 2026.zip', one), ('big video.mp4', pattern(6 * MB, seed=9))):
                b = B.wait_drop(lambda d, n=name: d['name'] == n, what=name + ' on B')
                if b:
                    check(b['kind'] == 'file', '%s is shown as a file (%s)' % (name, b['kind']))
                    got_name, got = B.download(b['id'])
                    check(got_name == name and hashlib.sha256(got).hexdigest() == hashlib.sha256(data).hexdigest(),
                          'B downloads %s byte for byte (%s, %d bytes)' % (name, got_name, len(got)))
                    st[name] = b['id']
            label = B.card(st.get('notes 2026.zip', 'none')).locator('.badge').text_content() if st.get('notes 2026.zip') else ''
            check(label.strip() == 'ZIP', 'a zip says ZIP (%r)' % label)
            check('1 MB' in (B.card(st.get('notes 2026.zip', 'none')).text_content() or ''), 'and shows its size')
        run_section('files: choose, drag and drop, download on B', files_across)

        def big_text():
            big = ''.join('%06d the quick brown fox jumps over the lazy dog\n' % i for i in range(5000))  # ~250 KB
            A.set_box(big)   # Playwright's fill types 5000 lines in ~25 s even on an empty page; a paste sets it at once
            A.page.click('#sendBtn')
            a = A.wait_drop(lambda d: d['kind'] == 'bigtext', timeout=15, what='the 250 KB text on A')
            txt = [f for f in fake.in_tray() if f['mimeType'].startswith('text/plain')]
            check(len(txt) == 1 and txt[0]['content'] == big.encode(), 'text over 200 KB goes to Drive as a .txt file')
            check(not any(len(d.get('text') or '') > 200000 for d in fake.drops()), 'and stays out of drop-tray.json')
            B.focus()
            b = B.wait_drop(lambda d: d['kind'] == 'bigtext', what='the big text on B')
            if b:
                check((b['text'] or '').startswith('000000 the quick brown fox'), 'B previews its first lines without downloading it')
                B.act(b['id'], 'copy')
                check(wait_until(lambda: B.clip_text() == big, 6), 'B copies the whole 250 KB text')
        run_section('text over 200 KB: sent as a file, copied whole', big_text)

        def pin_and_delete():
            a = next((d for d in A.drops() if d['text'] == 'typed in the box'), None)
            check(a is not None, 'the typed drop is there to pin')
            if not a:
                return
            A.act(a['id'], 'pin')
            check(wait_until(lambda: any(d['id'] == a['id'] and d['pinned'] for d in A.drops()), 3), 'a pinned drop moves to Pinned')
            check(doc_has(fake, lambda d: d['id'] == a['id'] and d.get('pinned')) is not None, 'the pin is saved to Drive')
            B.focus()
            check(wait_until(lambda: any(d['id'] == a['id'] and d['pinned'] for d in B.drops()), 4), 'and B sees it pinned')
            d = next((x for x in B.drops() if x['text'] == 'sent with the button'), None)
            B.act(d['id'], 'del')
            ask = B.card(d['id']).locator('.ask')
            check(ask.is_visible(), 'Delete asks first')
            check(B.page.evaluate("document.activeElement&&document.activeElement.dataset.act") == 'no', 'with Cancel focused')
            B.page.keyboard.press('Enter')
            check(not B.card(d['id']).locator('.ask').is_visible() and any(x['id'] == d['id'] for x in B.drops()), 'Enter on the question cancels')
            B.act(d['id'], 'del')
            B.card(d['id']).locator('[data-act="yes"]').click()
            B.wait_gone(lambda x: x['id'] == d['id'], what='the deleted text')
            check('Undo' in B.toast(), 'a delete offers Undo (%r)' % B.toast())
            # a file, deleted for good: its Drive file goes to Trash once the Undo has passed
            zid = st.get('notes 2026.zip')
            B.act(zid, 'del')
            B.card(zid).locator('[data-act="yes"]').click()
            B.wait_gone(lambda x: x['id'] == zid, what='the deleted zip')
            # another file, deleted and undone: it comes back and its file stays
            vid = st.get('big video.mp4')
            B.act(vid, 'del')
            B.card(vid).locator('[data-act="yes"]').click()
            B.wait_gone(lambda x: x['id'] == vid, what='the deleted video')
            B.page.locator('#toast [data-act="undo"]').click()
            check(B.wait_drop(lambda x: x['id'] == vid, what='the undone video') is not None, 'Undo brings the drop back')
            zip_file = [f for f in fake.files.values() if f['name'] == 'notes 2026.zip'][0]
            vid_file = [f for f in fake.files.values() if f['name'] == 'big video.mp4'][0]
            check(not zip_file['trashed'], 'the file is not trashed while Undo is still offered')
            check(wait_until(lambda: zip_file['trashed'], 12), 'the deleted zip goes to Drive\'s Trash')
            check(not vid_file['trashed'], 'the undone video stays in Drive')
            check(not fake.deletes, 'nothing was ever deleted for good (%s)' % fake.deletes[:2])
            A.focus()
            A.wait_gone(lambda x: x['id'] == d['id'] or x['id'] == zid, what='drops deleted on B')
            check(any(x['id'] == vid for x in A.drops()), 'A still has the undone video')
            doc = fake.doc() or {}
            tomb = (doc.get('items') or {}).get(zid) or {}
            check(tomb.get('deleted') and 'name' not in tomb and 'fileId' not in tomb, 'a deleted drop leaves only a small tombstone')
        run_section('pin and delete', pin_and_delete)
        for s in (A, B):
            SECTION[0] = 'two laptops: browser'
            s.report()
    finally:
        A.close(); B.close()


# ------------------------------------------------------------------ picking up fast: auto-copy, C, a click to renew, notifications
def quick_pickup(browser, base):
    fake = FakeDrive()
    A = Session(browser, base, fake, device='Home laptop')
    B = Session(browser, base, fake, device='Work laptop')
    st = {}
    try:
        def boot():
            for s in (A, B):
                s.open(); s.sign_in()
            for s in (A, B):
                check(not s.page.is_checked('#autoCopy') and not s.page.is_checked('#notifyChk'), 'auto-copy and notifications start off')
            check(A.page.locator('#autoCopy').is_visible(), 'the auto-copy switch is on the page, not hidden in Settings')
        run_section('quick pickup: sign in', boot)

        def auto_copy():
            B.page.check('#autoCopy')
            check(B.ls('droptray.autocopy') == '1', 'auto-copy is remembered on this device')
            check(A.ls('droptray.autocopy') is None, 'and only on this device')
            B.set_clip('sentinel')
            B.away()
            A.paste(text='auto one')
            check(doc_has(fake, lambda d: d.get('text') == 'auto one') is not None, 'A sent it')
            B.wait_drop(lambda d: d['text'] == 'auto one', timeout=12, what='the drop on B while it looks elsewhere')
            nap(400)
            check(B.clip_text() == 'sentinel', 'nothing is copied while you are in another window (%r)' % B.clip_text())
            check(B.page.title().startswith('(1)'), 'the tab title counts the drop that came in (%r)' % B.page.title())
            B.back()
            check(wait_until(lambda: B.clip_text() == 'auto one', 4), 'clicking back into the page puts it on the clipboard (%r)' % B.clip_text())
            check('Home laptop' in B.toast(), 'and says where it came from (%r)' % B.toast())
            check(B.page.title() == 'Drop Tray', 'the count clears when you come back (%r)' % B.page.title())
            A.paste(text='auto two')
            check(doc_has(fake, lambda d: d.get('text') == 'auto two') is not None, 'A sent another')
            B.focus()
            check(wait_until(lambda: B.clip_text() == 'auto two', 5), 'a drop arriving while you are on the page is copied at once (%r)' % B.clip_text())
            # a picture goes on the clipboard as a picture
            B.set_clip('sentinel')
            A.paste(files=[{'name': 'image.png', 'type': 'image/png', 'b64': __import__('base64').b64encode(png(30, 20, (200, 40, 90))).decode()}])
            A.wait_drop(lambda d: d['kind'] == 'image', timeout=10, what='the picture on A')
            check(doc_has(fake, lambda d: (d.get('mime') or '').startswith('image/')) is not None, 'A sent the picture')
            B.focus()
            check(wait_until(lambda: 'image/png' in B.clip_types(), 6), 'a picture is copied as a picture (%s)' % B.clip_types())
            # a file cannot go on a clipboard, so it is left alone
            B.set_clip('sentinel 2')
            A.pick('report.pdf', b'%PDF-1.4 tiny', 'application/pdf')
            A.wait_up('report.pdf')
            check(doc_has(fake, lambda d: d.get('name') == 'report.pdf') is not None, 'A sent the PDF')
            B.focus()
            B.wait_drop(lambda d: d['name'] == 'report.pdf', what='the PDF on B')
            nap(600)
            check(B.clip_text() == 'sentinel 2', 'a file is not put on the clipboard (%r)' % B.clip_text())
            # A never turned it on: B's drops leave A's clipboard alone
            A.set_clip('a sentinel')
            B.paste(text='from B')
            check(doc_has(fake, lambda d: d.get('text') == 'from B') is not None, 'B sent one')
            A.focus()
            A.wait_drop(lambda d: d['text'] == 'from B', what='B\'s drop on A')
            nap(500)
            check(A.clip_text() == 'a sentinel', 'a device with auto-copy off keeps its clipboard (%r)' % A.clip_text())
            # switched off again
            B.page.uncheck('#autoCopy')
            check(B.ls('droptray.autocopy') is None, 'switching it off is remembered')
            B.set_clip('kept')
            A.paste(text='after off')
            check(doc_has(fake, lambda d: d.get('text') == 'after off') is not None, 'A sent it')
            B.focus()
            B.wait_drop(lambda d: d['text'] == 'after off', what='after off on B')
            nap(500)
            check(B.clip_text() == 'kept', 'with auto-copy off nothing is copied (%r)' % B.clip_text())
        run_section('quick pickup: copy new drops to the clipboard', auto_copy)

        def stale_on_a_new_device():
            # a device opened for the first time copies only what was sent in the last hour, not yesterday's drop
            d = next(x for x in fake.drops() if x.get('text') == 'after off')
            f = fake.live(DOC)[0]
            doc = json.loads(f['content'])
            doc['items'][d['id']]['createdAt'] = d['createdAt'] - 2 * 3600 * 1000
            for it in doc['items'].values():   # everything else older still, so 'after off' is the newest
                if it.get('kind') == 'drop' and it['id'] != d['id'] and not it.get('deleted'):
                    it['createdAt'] = d['createdAt'] - 3 * 3600 * 1000
            f['content'], f['modifiedTime'] = json.dumps(doc).encode(), fake.stamp()
            C = Session(browser, base, fake, device='Old laptop', prefs={'droptray.autocopy': '1'})
            try:
                C.open()
                C.set_clip('c sentinel')
                C.sign_in()
                C.wait_drop(lambda x: x['text'] == 'after off', what='the old drop on a new device')
                nap(600)
                check(C.clip_text() == 'c sentinel', 'a drop from two hours ago is not copied on a newly opened device (%r)' % C.clip_text())
                A.paste(text='fresh one')
                check(doc_has(fake, lambda x: x.get('text') == 'fresh one') is not None, 'A sent a fresh one')
                C.focus()
                check(wait_until(lambda: C.clip_text() == 'fresh one', 5), 'but a new one is (%r)' % C.clip_text())
                C.report()
            finally:
                C.close()
        run_section('quick pickup: a newly opened device', stale_on_a_new_device)

        def key_c():
            check('C copies' in A.page.locator('#composeHint').text_content(), 'the hint mentions C (%r)' % A.page.locator('#composeHint').text_content())
            A.page.locator('h1').click()
            A.set_clip('nothing yet')
            A.page.keyboard.press('c')
            newest = next(d for d in A.drops() if d['kind'] in ('text', 'code', 'link'))
            check(wait_until(lambda: A.clip_text() == newest['text'], 3), 'C copies the newest drop (%r, wanted %r)' % (A.clip_text(), newest['text']))
            check('newest' in A.toast().lower(), 'and says so (%r)' % A.toast())
            A.pick('newer.zip', b'PK\x03\x04', 'application/zip')
            A.wait_up('newer.zip')
            A.page.locator('h1').click()
            A.set_clip('nothing yet')
            A.page.keyboard.press('c')
            check(wait_until(lambda: A.clip_text() == newest['text'], 3), 'C skips a file and copies the newest thing that fits on a clipboard (%r)' % A.clip_text())
            A.page.fill('#text', '')
            A.page.focus('#text')
            A.set_clip('untouched')
            A.page.keyboard.press('c')
            nap(300)
            check(A.page.input_value('#text') == 'c' and A.clip_text() == 'untouched', 'C typed in the box is just a letter')
            A.page.fill('#text', '')
        run_section('quick pickup: C copies the newest drop', key_c)

        def click_to_renew():
            p = B.page
            expire_b = lambda: fake.dead.add(B.token())   # only B's hour runs out; A keeps sending
            expire_b()
            B.focus()
            check(wait_until(lambda: p.locator('#sync .ds-google').count() == 1, 6), 'the hour ran out')
            check('click anywhere' in p.locator('#authNote').text_content().lower(), 'the note says a click renews it (%r)' % p.locator('#authNote').text_content())
            before = B.gis_requests()
            p.locator('.lead').click()
            check(wait_until(lambda: B.gis_requests() == before + 1, 3), 'a click anywhere on the page starts the sign-in, once (%d -> %d)' % (before, B.gis_requests()))
            check(wait_until(B.signed, 6), 'and signs back in')
            # after a reload the token is simply gone; the page still knows this device was signed in
            p.evaluate("()=>{var t=JSON.parse(localStorage.getItem('drivesync.token'));t.exp=Date.now()-1000;localStorage.setItem('drivesync.token',JSON.stringify(t));}")
            p.reload(); p.locator('#list').wait_for(state='attached')
            check(p.locator('#sync .ds-google').count() == 1, 'reloaded after the hour: signed out')
            p.locator('h1').click()
            check(wait_until(B.signed, 6), 'one click on the page signs back in after a reload too')
            # Send in a run-out sign-in asks once, not twice
            expire_b()
            B.focus()
            wait_until(lambda: p.locator('#sync .ds-google').count() == 1, 6)
            before = B.gis_requests()
            p.fill('#text', 'one sign-in')
            p.click('#sendBtn')
            check(wait_until(B.signed, 6), 'Send signs back in')
            check(B.gis_requests() == before + 1, 'with one sign-in, not one for the click and one for Send (%d)' % (B.gis_requests() - before))
            check(doc_has(fake, lambda d: d.get('text') == 'one sign-in') is not None, 'and the text arrives')
            # a refused sign-in is not asked again on every click
            expire_b()
            B.focus()
            wait_until(lambda: p.locator('#sync .ds-google').count() == 1, 6)
            p.evaluate('window.__gisDeny=true')
            before = B.gis_requests()
            p.locator('.lead').click()
            B.nap_until_refused()
            p.locator('.lead').click()
            p.locator('h1').click()
            nap(300)
            check(B.gis_requests() == before + 1, 'after you cancel Google\'s window, clicking around does not reopen it (%d)' % (B.gis_requests() - before))
            p.evaluate('window.__gisDeny=false')
            before = B.gis_requests()
            B.sign_in()
            check(B.gis_requests() == before + 1, 'Continue with Google asks once (%d)' % (B.gis_requests() - before))
        run_section('quick pickup: a click anywhere renews the hour-long sign-in', click_to_renew)

        def notifications():
            p = B.page
            p.check('#notifyChk')
            check(wait_until(lambda: B.ls('droptray.notify') == '1', 3), 'notifications are remembered on this device')
            check(not A.page.is_checked('#notifyChk'), 'and only on this device')
            p.evaluate('window.__shown=[]')
            B.away()
            A.paste(text='ping from home\nsecond line')
            check(doc_has(fake, lambda d: (d.get('text') or '').startswith('ping from home')) is not None, 'A sent it')
            got = wait_until(lambda: B.shown(), 12)
            check(got and got[0]['title'] == 'New drop from Home laptop' and got[0]['body'] == 'ping from home',
                  'B shows "New drop from Home laptop" with the first line (%s)' % got)
            B.back()
            p.evaluate('window.__shown=[]')
            A.paste(text='no ping')
            check(doc_has(fake, lambda d: d.get('text') == 'no ping') is not None, 'A sent another')
            B.focus()
            B.wait_drop(lambda d: d['text'] == 'no ping', what='no ping on B')
            nap(400)
            check(not B.shown(), 'no notification while you are looking at the page (%s)' % B.shown())
            # in another tab the page does not check Drive by itself; with notifications on it looks every 20 s
            B.away(hide=True)
            p.evaluate('window.__shown=[]')
            A.pick('hidden tab.pdf', b'%PDF-1.4 x', 'application/pdf')
            A.wait_up('hidden tab.pdf')
            check(doc_has(fake, lambda d: d.get('name') == 'hidden tab.pdf') is not None, 'A sent a file')
            got = wait_until(lambda: B.shown(), 26)
            check(got and got[0]['body'] == 'hidden tab.pdf', 'a page in a background tab still notifies, within about 20 s (%s)' % got)
            B.back()
            p.uncheck('#notifyChk')
            check(B.ls('droptray.notify') is None, 'switching notifications off is remembered')
            p.evaluate('window.__shown=[]')
            B.away()
            A.paste(text='quiet')
            check(doc_has(fake, lambda d: d.get('text') == 'quiet') is not None, 'A sent one more')
            B.wait_drop(lambda d: d['text'] == 'quiet', timeout=12, what='quiet on B')
            nap(400)
            check(not B.shown(), 'with notifications off nothing pops up (%s)' % B.shown())
            B.back()
        run_section('quick pickup: a notification when a drop arrives', notifications)
        for s in (A, B):
            SECTION[0] = 'quick pickup: browser'
            s.report()
    finally:
        A.close(); B.close()


# ------------------------------------------------------------------ uploads that go wrong
def upload_failures(browser, base):
    fake = FakeDrive()
    s = Session(browser, base, fake, device='Laptop')
    try:
        def boot():
            s.open(); s.sign_in()
        run_section('uploads: sign in', boot)

        def preflight_blocked():
            fake.flags['resumable'] = 'preflight_blocked'
            s.pick('blocked.bin', pattern(6 * MB, 1))
            s.wait_up('blocked.bin', timeout=30000)
            ups = [u for u in fake.uploads if u[1] == 'blocked.bin']
            check(ups == [('multipart', 'blocked.bin')], 'a blocked resumable upload goes again in one request (%s)' % ups)
            check(len(fake.live('blocked.bin')) == 1, 'and leaves one copy')
            check(doc_has(fake, lambda d: d.get('name') == 'blocked.bin') is not None, 'and its record is written')
        run_section('uploads: resumable blocked before the bytes', preflight_blocked)

        def unreadable():
            fake.flags['resumable'] = 'response_unreadable'
            s.page.reload(); s.page.locator('#list').wait_for(state='attached')
            s.page.wait_for_function("document.querySelector('#sync').dataset.state==='saved'", timeout=10000)
            s.pick('hidden answer.bin', pattern(6 * MB, 2))
            s.wait_up('hidden answer.bin', timeout=30000)
            ups = [u for u in fake.uploads if u[1] == 'hidden answer.bin']
            check(ups == [('resumable', 'hidden answer.bin')], 'a file that arrived is not sent twice (%s)' % ups)
            rec = doc_has(fake, lambda d: d.get('name') == 'hidden answer.bin')
            f = fake.live('hidden answer.bin')
            check(rec is not None and f and rec.get('fileId') == f[0]['id'], 'the record points at the file that arrived')
            fake.flags['resumable'] = 'ok'
        run_section('uploads: Drive stored it but the answer was hidden', unreadable)

        def failed_then_retry():
            fake.flags['multipart'] = 'fail'
            s.pick('flaky.txt', b'flaky content', 'text/plain')
            s.wait_up('flaky.txt', 'failed')
            row = s.page.locator('#tray .up[data-name="flaky.txt"]')
            check(row.locator('[data-act="retry"]').count() == 1, 'a failed upload offers Retry')
            s.page.wait_for_timeout(600)
            check(doc_has(fake, lambda d: d.get('name') == 'flaky.txt', 0.2) is None, 'a failed upload writes no record')
            check(not any(d['name'] == 'flaky.txt' for d in s.drops()), 'and shows no drop')
            fake.flags['multipart'] = 'ok'
            row.locator('[data-act="retry"]').click()
            s.wait_up('flaky.txt')
            check(doc_has(fake, lambda d: d.get('name') == 'flaky.txt') is not None, 'Retry sends it and writes the record')
            check(len(fake.live('flaky.txt')) == 1, 'one copy after Retry')
        run_section('uploads: a failure, then Retry', failed_then_retry)

        def cancel():
            # the sections above end with this visit sending everything in one request; a fresh visit tries resumable again
            s.page.reload(); s.page.locator('#list').wait_for(state='attached')
            s.page.wait_for_function("document.querySelector('#sync').dataset.state==='saved'", timeout=10000)
            fake.flags['hold_put'] = True
            s.pick('slow.bin', pattern(6 * MB, 4))
            s.wait_up('slow.bin', 'uploading')
            check(wait_until(lambda: len(fake.held) == 1, 10), 'the bytes are on their way')
            s.page.locator('#tray .up[data-name="slow.bin"] [data-act="cancel"]').click()
            s.wait_up('slow.bin', 'cancelled')
            fake.flags['hold_put'] = False
            fake.release()
            s.page.wait_for_timeout(500)
            check(doc_has(fake, lambda d: d.get('name') == 'slow.bin', 0.2) is None, 'a cancelled upload writes no record')
            check(not fake.live('slow.bin'), 'and leaves no file')
        run_section('uploads: Cancel', cancel)

        def too_big():
            n = len(fake.uploads)
            s.page.evaluate("""()=>{var dt=new DataTransfer();dt.items.add(new File([new Uint8Array(101*1024*1024)],'huge.iso'));
                document.body.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));}""")
            check(wait_until(lambda: '100 MB' in s.toast(), 3), 'a file over 100 MB is refused, and says why (%r)' % s.toast())
            s.page.wait_for_timeout(300)
            check(len(fake.uploads) == n and not s.page.locator('#tray .up[data-name="huge.iso"]').count(), 'and is never sent')
        run_section('uploads: over 100 MB', too_big)
        SECTION[0] = 'uploads: browser'
        s.report()
    finally:
        s.close()


# ------------------------------------------------------------------ signed out, and an expired sign-in
def signed_out(browser, base):
    fake = FakeDrive()
    s = Session(browser, base, fake, device='Laptop')
    try:
        def waiting():
            s.open()
            p = s.page
            check(p.locator('#authNote').is_visible(), 'signed out, the page says what sign-in is for')
            check(p.locator('#pickBtn').is_disabled(), 'Choose files is off until you sign in')
            p.evaluate('window.__gisDeny=true')
            p.fill('#text', 'waiting for sign-in')
            p.click('#sendBtn')
            s.wait_drop(lambda d: d['text'] == 'waiting for sign-in', what='the text sent while signed out')
            check(wait_until(lambda: p.evaluate('window.__gis&&window.__gis.requests||0') >= 1, 3), 'Send while signed out asks Google to sign in')
            check(wait_until(lambda: 'changes waiting' in p.locator('#sync').text_content(), 3), 'the text waits on this device (%r)' % p.locator('#sync').text_content())
            check(fake.doc() is None, 'nothing reached Drive')
            s.nap_until_refused()   # while Send's sign-in is still open, a paste shares it rather than opening a second window
            n, asked = len(fake.uploads), p.evaluate('window.__gis.requests')
            s.paste(files=[{'name': 'image.png', 'type': 'image/png', 'b64': __import__('base64').b64encode(png(4, 4, (1, 2, 3))).decode()}])
            check(wait_until(lambda: 'sign in' in s.toast().lower(), 3), 'an image pasted while signed out says to sign in (%r)' % s.toast())
            check(wait_until(lambda: p.evaluate('window.__gis.requests') > asked, 3), 'and offers the sign-in')
            s.nap_until_refused()
            check(len(fake.uploads) == n, 'and is not sent')
            p.evaluate('window.__gisDeny=false')
            s.sign_in()
            check(doc_has(fake, lambda d: d.get('text') == 'waiting for sign-in') is not None, 'after sign-in the waiting text reaches Drive')
            check(not p.locator('#authNote').is_visible() and p.locator('#pickBtn').is_enabled(), 'signed in, files can be chosen')
        run_section('signed out: text waits, files wait for sign-in', waiting)

        def expired():
            p = s.page
            fake.kill_tokens()
            s.focus()
            check(wait_until(lambda: p.locator('#sync .ds-google').count() == 1, 6), 'an expired sign-in shows Continue with Google again')
            before = p.evaluate('window.__gis.requests')
            p.fill('#text', 'after the hour')
            p.click('#sendBtn')
            check(wait_until(lambda: p.evaluate('window.__gis.requests') > before, 3), 'Send after the hour starts the sign-in (one tap)')
            check(doc_has(fake, lambda d: d.get('text') == 'after the hour') is not None, 'and the text arrives in Drive')
        run_section('signed out: the hour-long sign-in ran out', expired)
        SECTION[0] = 'signed out: browser'
        s.report()
    finally:
        s.close()


# ------------------------------------------------------------------ clearing old drops
def clean_up(browser, base):
    fake = FakeDrive()
    now = time.time()
    t0 = datetime.datetime.fromtimestamp(now)
    s = Session(browser, base, fake, device='Laptop', clock=t0)
    p = s.page
    ids = {}
    try:
        def seed():
            s.open(); s.sign_in()
            check(p.locator('#keepSel').input_value() == '7', 'drops are kept 7 days unless you choose otherwise (%s)' % p.locator('#keepSel').input_value())
            p.fill('#text', 'old text'); p.click('#sendBtn')
            s.pick('old.bin', b'old bytes')
            s.wait_up('old.bin')
            p.fill('#text', 'keep me'); p.click('#sendBtn')
            k = s.wait_drop(lambda d: d['text'] == 'keep me', what='keep me')
            s.act(k['id'], 'pin')
            for d in s.drops():
                ids[d['text'] or d['name']] = d['id']
            check(wait_until(lambda: len(fake.drops()) == 3 and any(d.get('pinned') for d in fake.drops()), 6), 'three drops in Drive, one pinned')
            s.keep('30')
            check(wait_until(lambda: ((fake.doc() or {}).get('items') or {}).get('settings', {}).get('keepDays') == 30, 6), 'the setting is saved with the drops, so every device follows it')
        run_section('clean-up: three drops, one pinned', seed)

        def day8():
            later = now + 8 * 86400
            p.clock.set_system_time(datetime.datetime.fromtimestamp(later))
            # two files nobody points at: one left by an upload that never finished two days ago, one from an hour ago
            fid = fake.folder()
            fake.add('orphan old.bin', 'application/octet-stream', fid, b'o', at=later - 2 * 86400)
            fake.add('orphan new.bin', 'application/octet-stream', fid, b'n', at=later - 3600)
            p.reload(); p.locator('#list').wait_for(state='attached')
            check(abs(p.evaluate('Date.now()') / 1000.0 - later) < 120, 'the page now thinks it is eight days later')
            check(len(s.drops()) == 3, 'kept for 30 days, nothing is cleared at 8 days (%d)' % len(s.drops()))
            s.keep('7')
            s.wait_gone(lambda d: d['text'] == 'old text' or d['name'] == 'old.bin', what='the week-old drops')
            check(any(d['text'] == 'keep me' for d in s.drops()), 'the pinned drop stays')
            # the sign-in from 8 days ago has long run out: the files are trashed once it is back
            if p.locator('#sync .ds-google').count():
                s.sign_in()
            old = fake.live('old.bin')
            check(wait_until(lambda: not fake.live('old.bin'), 8), 'the week-old file goes to Drive\'s Trash')
            check(wait_until(lambda: not fake.live('orphan old.bin'), 8), 'a two-day-old file no drop points at is swept too')
            check(fake.live('orphan new.bin'), 'a file from an hour ago is left alone (its upload may still be finishing)')
            check(not fake.deletes, 'nothing deleted for good')
            check(wait_until(lambda: [d.get('text') for d in fake.drops()] == ['keep me'], 6), 'Drive keeps only the pinned drop (%s)' % [d.get('text') or d.get('name') for d in fake.drops()])
        run_section('clean-up: eight days later', day8)

        def never():
            s.keep('0')
            p.fill('#text', 'forever'); p.click('#sendBtn')
            s.wait_drop(lambda d: d['text'] == 'forever', what='forever')
            check(doc_has(fake, lambda d: d.get('text') == 'forever') is not None, 'saved')
            p.clock.set_system_time(datetime.datetime.fromtimestamp(now + 400 * 86400))
            p.reload(); p.locator('#list').wait_for(state='attached')
            check(any(d['text'] == 'forever' for d in s.drops()), '"Until I delete them" keeps a drop for over a year')
            # a deletion is remembered for 60 days, not ten years, so the file stays small
            if p.locator('#sync .ds-google').count():
                s.sign_in()
            p.fill('#text', 'nudge'); p.click('#sendBtn')
            check(doc_has(fake, lambda d: d.get('text') == 'nudge') is not None, 'a new drop saves')
            items = (fake.doc() or {}).get('items') or {}
            check(ids.get('old text') and ids['old text'] not in items, 'a deletion older than 60 days is forgotten (the file stays small)')
        run_section('clean-up: until I delete them, and old tombstones', never)
        SECTION[0] = 'clean-up: browser'
        s.report()
    finally:
        s.close()


# ------------------------------------------------------------------ layout and contrast
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


HEX_OF = """()=>{var cv=document.createElement('canvas');cv.width=cv.height=1;var x=cv.getContext('2d',{willReadFrequently:true});
  function hex(c){x.clearRect(0,0,1,1);x.fillStyle='#000';x.fillStyle=c;x.fillRect(0,0,1,1);var d=x.getImageData(0,0,1,1).data;return '#'+[d[0],d[1],d[2]].map(v=>v.toString(16).padStart(2,'0')).join('');}
  function bg(e){while(e){var c=getComputedStyle(e).backgroundColor;if(c&&c!=='rgba(0, 0, 0, 0)'&&c!=='transparent')return c;e=e.parentElement;}return getComputedStyle(document.body).backgroundColor;}
  var out=[];document.querySelectorAll('.drop .badge,.drop .from,.drop .when,.drop pre.txt,.drop .fname,.drop [data-act],#deviceLine,.hint,.foot').forEach(function(e){
    if(!e.offsetParent)return;out.push([e.className||e.tagName,hex(getComputedStyle(e).color),hex(bg(e))]);});return out;}"""


def layout(browser, base):
    for scheme in ('light', 'dark'):
        for w, h in ((390, 844), (1280, 900)):
            fake = FakeDrive()
            s = Session(browser, base, fake, device='A rather long laptop name', viewport=(w, h))
            p = s.page
            try:
                def one():
                    p.emulate_media(color_scheme=scheme)
                    s.open()
                    check(p.evaluate('document.documentElement.scrollWidth') <= w, 'signed-out page fits %d px' % w)
                    s.sign_in()
                    s.paste(text='const x = await fetch("/api/v1/items?filter=" + encodeURIComponent(query) + "&limit=100&offset=" + offset);\n' * 20)
                    s.paste(text='https://example.com/' + 'very-long-path-segment/' * 8)
                    s.paste(text='A plain note to self, a line of text.')
                    s.pick('quarterly-report-final-version-with-a-very-long-name-2026.xlsx', b'x' * 2048, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                    s.wait_up('quarterly-report-final-version-with-a-very-long-name-2026.xlsx')
                    s.paste(files=[{'name': 'image.png', 'type': 'image/png', 'b64': __import__('base64').b64encode(png(900, 300, (200, 80, 40))).decode()}])
                    s.wait_drop(lambda d: d['kind'] == 'image', timeout=10, what='the image')
                    s.wait_drop(lambda d: d['kind'] == 'file', what='the spreadsheet')
                    p.wait_for_timeout(400)
                    sw = p.evaluate('document.documentElement.scrollWidth')
                    check(sw <= w, 'a full tray fits %d px without sideways scroll (%d)' % (w, sw))
                    d = s.drops()[0]
                    s.act(d['id'], 'del')
                    check(p.evaluate('document.documentElement.scrollWidth') <= w, 'the delete question fits %d px' % w)
                    s.card(d['id']).locator('[data-act="no"]').click()
                    xl = next(x for x in s.drops() if x['kind'] == 'file')
                    check(s.card(xl['id']).locator('.badge').text_content().strip() == 'Excel', 'a spreadsheet says Excel')
                    v = p.evaluate("""(()=>{var cs=getComputedStyle(document.documentElement);var o={};
                        ['--bg','--surface','--surface-2','--text','--muted','--faint','--accent','--accent-ink','--danger','--code-bg'].forEach(k=>o[k]=cs.getPropertyValue(k).trim());return o;})()""")
                    for fg in ('--text', '--muted', '--faint', '--accent', '--danger'):
                        for bg in ('--bg', '--surface', '--surface-2', '--code-bg'):
                            r = ratio(v[fg], v[bg])
                            check(r >= 4.5, '%s on %s is %.2f:1 in %s (needs 4.5)' % (fg, bg, r, scheme))
                    r = ratio(v['--accent-ink'], v['--accent'])
                    check(r >= 4.5, 'button text on accent is %.2f:1 in %s' % (r, scheme))
                    for cls, fg, bg in p.evaluate(HEX_OF):
                        r = ratio(fg, bg)
                        check(r >= 4.5, '%s text is %.2f:1 in %s (needs 4.5)' % (cls, r, scheme))
                run_section('layout %s %dpx' % (scheme, w), one)
                s.report()
            finally:
                s.close()


# ------------------------------------------------------------------ the source page, no Drive at all
def source_page(browser):
    httpd, base = serve(os.path.join(ROOT, 'site'))
    s = Session(browser, base, None, device='Laptop')
    try:
        def local():
            s.open()
            p = s.page
            check('only in this browser' in p.locator('#foot').text_content(), 'the source page says it saves only in this browser')
            check(p.locator('#pickBtn').is_disabled(), 'files need the hosted page')
            s.paste(text='local note')
            s.wait_drop(lambda d: d['text'] == 'local note', what='the local note')
            p.reload(); p.locator('#list').wait_for(state='attached')
            check(any(d['text'] == 'local note' for d in s.drops()), 'it is still there after a reload')
            d = next(x for x in s.drops() if x['text'] == 'local note')
            s.act(d['id'], 'copy')
            check(wait_until(lambda: s.clip_text() == 'local note', 3), 'and copies')
        run_section('site/: browser-only copy', local)
        SECTION[0] = 'site/: browser'
        s.report()
    finally:
        s.close(); httpd.shutdown()


def main():
    docs = os.path.join(ROOT, 'docs')
    if not os.path.exists(os.path.join(docs, PAGE)):
        print('docs/%s does not exist yet; run tools/build-site.py' % PAGE)
        sys.exit(1)
    httpd, base = serve(docs)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=not HEADED)
        for label, flow in (('two laptops', two_laptops), ('quick pickup', quick_pickup), ('uploads', upload_failures), ('signed out', signed_out),
                            ('clean-up', clean_up), ('layout', layout)):
            print('==', label)
            flow(browser, base)
        print('== site/')
        source_page(browser)
        browser.close()
    httpd.shutdown()
    print('\n%d checks passed, %d failed' % (PASSES[0], len(FAILS)))
    for x in FAILS:
        print('  ' + x)
    sys.exit(1 if FAILS else 0)


if __name__ == '__main__':
    main()
