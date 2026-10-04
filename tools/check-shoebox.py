"""Shoebox checker: drives the Shoebox page in headless Chrome against a fake Google.

  python tools/check-shoebox.py            # docs/shoebox.html (built), then site/shoebox.html
  python tools/check-shoebox.py --headed   # watch it

Nothing here can reach the real Google Drive. Sign-in is a stub of Google's script that hands
out fake tokens, Drive is an in-memory fake (FakeDrive below) answering every request the page
makes, and any other request to a Google host is aborted and reported. The only real network
use is PDF.js from cdnjs, for the phone-style PDF viewer check.

Exit code 0 means every check passed; each failure is printed with the section it came from.
"""
import functools, http.server, json, os, re, socket, struct, sys, threading, time, urllib.parse, zlib
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEADED = '--headed' in sys.argv
FOLDER = 'application/vnd.google-apps.folder'
GDOC = 'application/vnd.google-apps.document'
OOXML = 'application/vnd.openxmlformats-officedocument.'
# Papers/ holds one file of each kind the page must tell apart: name -> (mimeType, data-type, tile label, menu wording)
PAPERS = {
    'Minutes': (GDOC, 'gdoc', 'Google Doc', 'Google Doc'),
    'Budget': ('application/vnd.google-apps.spreadsheet', 'gsheet', 'Google Sheet', 'Google Sheet'),
    'Pitch': ('application/vnd.google-apps.presentation', 'gslides', 'Google Slides', 'Google Slides'),
    'Survey': ('application/vnd.google-apps.form', 'gform', 'Google Form', 'Google Form'),
    'Letter.docx': (OOXML + 'wordprocessingml.document', 'word', 'Word', 'Word document'),
    'Accounts.xlsx': (OOXML + 'spreadsheetml.sheet', 'excel', 'Excel', 'Excel spreadsheet'),
    'Old.xls': ('application/octet-stream', 'excel', 'Excel', 'Excel spreadsheet'),   # Drive said nothing: the name decides
    'Talk.pptx': (OOXML + 'presentationml.presentation', 'powerpoint', 'PowerPoint', 'PowerPoint presentation'),
    'contacts.csv': ('text/csv', 'csv', 'CSV', 'CSV table'),
    'photos.zip': ('application/zip', 'archive', 'ZIP', 'Archive'),
    'readme.txt': ('text/plain', 'text', 'TXT', 'Text file'),
    'Report.pdf': ('application/pdf', 'pdf', 'PDF', 'PDF'),
}
FAILS, PASSES = [], [0]
SECTION = ['']


def check(cond, msg):
    if cond:
        PASSES[0] += 1
    else:
        FAILS.append('[%s] %s' % (SECTION[0], msg))
        print('  FAIL', msg)
    return cond


# ------------------------------------------------------------------ sample content
def png(w, h, rgb):
    raw = b''.join(b'\x00' + bytes(rgb) * w for _ in range(h))

    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def pdf(text):
    s = b'BT /F1 18 Tf 30 100 Td (' + text.encode() + b') Tj ET'
    objs = [b'<</Type/Catalog/Pages 2 0 R>>', b'<</Type/Pages/Kids[3 0 R]/Count 1>>',
            b'<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>',
            b'<</Length %d>>stream\n' % len(s) + s + b'\nendstream',
            b'<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>']
    out, offs = b'%PDF-1.4\n', []
    for i, o in enumerate(objs):
        offs.append(len(out))
        out += b'%d 0 obj\n' % (i + 1) + o + b'\nendobj\n'
    x = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objs) + 1) + b''.join(b'%010d 00000 n \n' % o for o in offs)
    out += b'trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n' % (len(objs) + 1, x)
    return out


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
    revoke:function(t,cb){window.__gisRevoked=(window.__gisRevoked||0)+1;if(cb)cb({});}
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


def word_prefix(val, v):
    """Drive's `name contains` matches from the start of the name or of any word in it."""
    val, v = val.lower(), v.lower()
    return any(val[i:].startswith(v) for i in range(len(val)) if i == 0 or not val[i - 1].isalnum())


class FakeDrive:
    def __init__(self):
        self.files, self.n, self.calls, self.uploads, self.deletes, self.dead, self.seen = {}, 0, [], [], [], set(), set()
        self.sessions, self.exports, self.media = {}, [], []
        self.flags = {'thumb_img': True, 'resumable': 'ok'}
        self.bearer_thumbs = 0
        self.root = self.add('My Drive', FOLDER, None, fid='root-id')
        fam = self.add('Family', FOLDER, self.root)
        docs = self.add('Documents', FOLDER, self.root)
        bulk = self.add('Bulk', FOLDER, self.root)
        self.add('Empty', FOLDER, self.root)
        self.add('Passport scan.pdf', 'application/pdf', self.root, pdf('Passport'))
        self.add('Notes', GDOC, self.root)
        self.add('sunset.png', 'image/png', self.root, png(80, 60, (240, 120, 40)))
        old = self.add('Old photos', FOLDER, fam)
        self.add('beach day.png', 'image/png', fam, png(64, 48, (40, 140, 220)))
        self.add('birthday cake.png', 'image/png', fam, png(48, 64, (220, 60, 120)))
        self.add('grandma.png', 'image/png', fam, png(64, 64, (90, 160, 80)))
        self.add('family tree.pdf', 'application/pdf', fam, pdf('Family tree'))
        self.add('wedding 1998.png', 'image/png', old, png(60, 40, (200, 180, 60)))
        self.add('degree certificate.pdf', 'application/pdf', docs, pdf('Degree'))
        self.add('tax 2025.pdf', 'application/pdf', docs, pdf('Tax'))
        for i in range(1, 131):
            self.add('file %03d.txt' % i, 'text/plain', bulk, b'x' * i)
        papers = self.add('Papers', FOLDER, self.root)
        for name, (mime, _, _, _) in PAPERS.items():
            self.add(name, mime, papers, b'' if mime.startswith('application/vnd.google-apps') else pdf(name) if mime == 'application/pdf' else b'p' * 40)
        r = self.add('old receipt.pdf', 'application/pdf', self.root, pdf('Receipt'))
        self.files[r]['trashed'] = self.files[r]['explicitlyTrashed'] = True
        self.ids = {f['name']: f['id'] for f in self.files.values()}

    # ---- state helpers
    def stamp(self):
        # real clock times, a millisecond apart, so "newest first" and "uploaded just now" both mean something
        self.n += 1
        t = time.time() + self.n / 1000.0
        return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(t)) + '.%03dZ' % int((t % 1) * 1000)

    def add(self, name, mime, parent, content=b'', fid=None):
        fid = fid or 'id%04d' % (len(self.files) + 1)
        t = self.stamp()
        self.files[fid] = {'id': fid, 'name': name, 'mimeType': mime, 'parents': [parent] if parent else [],
                           'trashed': False, 'explicitlyTrashed': False, 'starred': False,
                           'createdTime': t, 'modifiedTime': t, 'content': content}
        return fid

    def by_name(self, name, live=None):
        out = [f for f in self.files.values() if f['name'] == name and (live is None or f['trashed'] != live)]
        return out

    def one(self, name):
        got = self.by_name(name)
        assert len(got) == 1, 'expected one file named %r, found %d' % (name, len(got))
        return got[0]

    def children(self, fid):
        return [f for f in self.files.values() if fid in f['parents']]

    def descendants(self, fid):
        out = []
        for c in self.children(fid):
            out.append(c)
            if c['mimeType'] == FOLDER:
                out += self.descendants(c['id'])
        return out

    def kill_tokens(self):
        self.dead |= self.seen

    def lists(self):
        return [c for c in self.calls if c[0] == 'GET' and c[1] == '/drive/v3/files']

    # ---- JSON shape of a file, cut down to the requested fields
    def view(self, f):
        is_folder, is_g = f['mimeType'] == FOLDER, f['mimeType'].startswith('application/vnd.google-apps')
        o = {k: f[k] for k in ('id', 'name', 'mimeType', 'parents', 'trashed', 'explicitlyTrashed', 'starred', 'createdTime', 'modifiedTime')}
        o['kind'] = 'drive#file'
        if not is_folder and not is_g:
            o['size'] = str(len(f['content']))
        o['quotaBytesUsed'] = str(len(f['content']))
        thumb = f['mimeType'].startswith('image/') or f['mimeType'] == 'application/pdf'
        o['hasThumbnail'] = thumb
        if thumb:
            o['thumbnailLink'] = 'https://lh3.googleusercontent.com/fake-thumb/%s=s220' % f['id']
        o['iconLink'] = 'https://drive-thirdparty.googleusercontent.com/16/type/' + f['mimeType']
        o['webViewLink'] = 'https://drive.google.com/file/d/%s/view' % f['id']
        o['ownedByMe'] = True
        o['capabilities'] = {k: True for k in ('canEdit', 'canRename', 'canTrash', 'canDelete', 'canMoveItemWithinDrive', 'canAddChildren', 'canDownload', 'canUntrash')}
        return o

    @staticmethod
    def mask_names(spec):
        return [p.split('(')[0].strip() for p in split_top(spec, ',') if p.strip()]

    def cut(self, o, spec):
        if not spec or spec == '*':
            return o
        return {k: o[k] for k in self.mask_names(spec) if k in o}

    def files_spec(self, fields):
        m = re.search(r'files\((.*)\)', fields or '')
        return m.group(1) if m else 'id,name,mimeType,kind'

    # ---- queries
    def match(self, q, f):
        q = q.strip()
        ands = split_top(q, ' and ')
        if len(ands) > 1:
            return all(self.match(a, f) for a in ands)
        ors = split_top(q, ' or ')
        if len(ors) > 1:
            return any(self.match(o, f) for o in ors)
        if q.startswith('(') and q.endswith(')'):
            return self.match(q[1:-1], f)
        if q.startswith('not '):
            return not self.match(q[4:], f)
        m = re.fullmatch(r"('(?:[^'\\]|\\.)*')\s+in\s+parents", q)
        if m:
            pid = unq(m.group(1))
            return (self.root if pid == 'root' else pid) in f['parents']
        m = re.fullmatch(r'(trashed|starred)\s*=\s*(true|false)', q)
        if m:
            return f[m.group(1)] == (m.group(2) == 'true')
        m = re.fullmatch(r"(name|mimeType)\s*(=|!=|contains)\s*('(?:[^'\\]|\\.)*')", q)
        if m:
            field, op, v = m.group(1), m.group(2), unq(m.group(3))
            val = f[field]
            if op == '=':
                return val == v
            if op == '!=':
                return val != v
            return word_prefix(val, v) if field == 'name' else v in val
        raise AssertionError('fake drive does not understand query clause: ' + q)

    def order(self, rows, spec):
        keys = [k.strip() for k in (spec or 'folder,name').split(',') if k.strip()]
        for k in reversed(keys):
            name, _, d = k.partition(' ')
            desc = d.strip() == 'desc'
            if name == 'folder':
                fn = lambda f: 0 if f['mimeType'] == FOLDER else 1
            elif name in ('name', 'name_natural'):
                fn = lambda f: f['name'].lower()
            elif name == 'modifiedTime':
                fn = lambda f: f['modifiedTime']
            elif name == 'quotaBytesUsed':
                fn = lambda f: len(f['content'])
            else:
                raise AssertionError('fake drive does not know orderBy key ' + k)
            rows.sort(key=fn, reverse=desc)
        return rows

    # ---- HTTP plumbing
    def reply(self, route, status, body=b'', ctype='application/json', headers=None, cors=True):
        h = dict(CORS) if cors else {}
        h.update(headers or {})
        route.fulfill(status=status, body=body, content_type=ctype, headers=h)

    def json(self, route, status, obj, cors=True):
        self.reply(route, status, json.dumps(obj).encode(), cors=cors)

    def err(self, route, status, msg):
        self.json(route, status, {'error': {'code': status, 'message': msg, 'errors': [{'reason': 'fake', 'message': msg}]}})

    def thumb(self, route, req):
        if req.method == 'OPTIONS':
            return self.reply(route, 204)
        fid = req.url.split('/fake-thumb/')[1].split('=')[0]
        f = self.files.get(fid)
        body = f['content'] if f and f['mimeType'].startswith('image/') else png(40, 52, (200, 200, 210))
        if req.headers.get('authorization'):   # the real Google refuses this (CORS), so the page must not try it
            self.bearer_thumbs += 1
            return route.abort('failed')
        if not self.flags['thumb_img']:
            return self.reply(route, 403, b'forbidden', 'text/plain', cors=False)
        return self.reply(route, 200, body, 'image/png', cors=False)

    def api(self, route, req):
        u = urllib.parse.urlsplit(req.url)
        path, qs, m = u.path, dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True)), req.method
        if m == 'OPTIONS':
            return self.reply(route, 204)
        self.calls.append((m, path, qs))
        if m == 'DELETE':
            self.deletes.append(req.url)
            return self.reply(route, 204)
        if m == 'PUT' and path == '/upload/drive/v3/files' and qs.get('upload_id'):
            return self.put_session(route, req, qs['upload_id'])
        tok = (req.headers.get('authorization') or '')[7:]
        if not tok.startswith('fake-token-') or tok in self.dead:
            return self.err(route, 401, 'Request had invalid authentication credentials.')
        self.seen.add(tok)
        body = req.post_data_buffer or b''
        if path == '/drive/v3/about':
            return self.json(route, 200, {'storageQuota': {'limit': '16106127360', 'usage': '6657199308'},
                                          'user': {'emailAddress': 'tester@example.com', 'displayName': 'Tester'}})
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
        mm = re.fullmatch(r'/drive/v3/files/([^/]+)(/export)?', path)
        if mm:
            fid = self.root if mm.group(1) == 'root' else mm.group(1)
            f = self.files.get(fid)
            if not f:
                return self.err(route, 404, 'File not found: ' + fid)
            if mm.group(2):
                if not f['mimeType'].startswith('application/vnd.google-apps') or f['mimeType'] == FOLDER:
                    return self.err(route, 403, 'Export only supports Docs Editors files.')
                self.exports.append((fid, qs.get('mimeType')))
                return self.reply(route, 200, pdf(f['name']), qs.get('mimeType') or 'application/pdf')
            if m == 'GET' and qs.get('alt') == 'media':
                if f['mimeType'].startswith('application/vnd.google-apps'):
                    return self.err(route, 403, 'Only files with binary content can be downloaded. Use Export with Docs Editors files.')
                self.media.append(fid)
                return self.reply(route, 200, f['content'], f['mimeType'])
            if m == 'GET':
                return self.json(route, 200, self.cut(self.view(f), qs.get('fields') or 'id,name,mimeType,kind'))
            if m == 'PATCH':
                meta = json.loads(body or b'{}')
                add = [p for p in (qs.get('addParents') or '').split(',') if p]
                rem = [p for p in (qs.get('removeParents') or '').split(',') if p]
                for p in add:
                    if p == fid or p in [d['id'] for d in self.descendants(fid)]:
                        return self.err(route, 400, 'cycle: a folder cannot be moved into itself')
                f['parents'] = [p for p in f['parents'] if p not in rem] + [p for p in add if p not in f['parents']]
                if 'name' in meta:
                    f['name'] = meta['name']
                if 'starred' in meta:
                    f['starred'] = bool(meta['starred'])
                if 'trashed' in meta:
                    if meta['trashed']:
                        f['trashed'] = f['explicitlyTrashed'] = True
                        for d in self.descendants(fid):
                            if not d['trashed']:
                                d['trashed'], d['explicitlyTrashed'] = True, False
                    else:
                        f['trashed'] = f['explicitlyTrashed'] = False
                        for d in self.descendants(fid):
                            if d['trashed'] and not d['explicitlyTrashed']:
                                d['trashed'] = False
                f['modifiedTime'] = self.stamp()
                return self.json(route, 200, self.cut(self.view(f), qs.get('fields') or 'id,name,mimeType,kind'))
        if path == '/upload/drive/v3/files' and m == 'POST' and qs.get('uploadType') == 'multipart':
            ct = req.headers.get('content-type') or ''
            b = re.search(r'boundary="?([^";]+)"?', ct).group(1).encode()
            parts = [p for p in body.split(b'--' + b) if p.strip(b'\r\n-')]
            hd, meta = parts[0].split(b'\r\n\r\n', 1)
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
            self.sessions[sid] = {'meta': meta, 'mime': req.headers.get('x-upload-content-type'),
                                  'fields': qs.get('fields'), 'length': req.headers.get('x-upload-content-length')}
            loc = 'https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&upload_id=' + sid
            return self.reply(route, 200, b'', headers={'Location': loc})
        return self.err(route, 400, 'fake drive has no route for %s %s' % (m, path))

    def put_session(self, route, req, sid):
        s = self.sessions.get(sid)
        if not s:
            return self.err(route, 404, 'no such upload session')
        mode = self.flags['resumable']
        if mode == 'preflight_blocked':   # the browser never gets to send the bytes
            return route.abort('failed')
        data = req.post_data_buffer or b''
        meta = s['meta']
        fid = self.add(meta['name'], meta.get('mimeType') or s['mime'] or 'application/octet-stream',
                       (meta.get('parents') or [self.root])[0], data)
        self.uploads.append(('resumable', meta['name']))
        body = json.dumps(self.cut(self.view(self.files[fid]), s['fields'] or 'id,name,mimeType,kind')).encode()
        if mode == 'response_unreadable':   # Drive stored the file, but the browser hides the answer
            return route.abort('failed')
        return self.reply(route, 200, body)


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


PDF_VIEWER_INIT = """Object.defineProperty(Navigator.prototype,'pdfViewerEnabled',{configurable:true,get:function(){return %s;}});"""


class Session:
    """One browser context wired to one fake Drive."""

    def __init__(self, browser, base, fake, viewport=(1280, 900), pdf_viewer=True, allow_cdn=False):
        self.fake, self.blocked, self.errors = fake, [], []
        self.ctx = browser.new_context(viewport={'width': viewport[0], 'height': viewport[1]}, accept_downloads=True)
        self.ctx.add_init_script(PDF_VIEWER_INIT % ('true' if pdf_viewer else 'false'))
        self.allow_cdn = allow_cdn
        self.ctx.route('**/*', self.route)
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(8000)
        self.page.on('pageerror', lambda e: self.errors.append('pageerror: ' + str(e)))
        self.page.on('console', self.console)
        self.base = base

    def console(self, msg):
        t = msg.text
        if msg.type == 'error' and not re.search(r'Failed to load resource|net::ERR_|CORS policy|blocked by CORS', t):
            self.errors.append('console: ' + t[:300])

    def route(self, route, req):
        u = urllib.parse.urlsplit(req.url)
        host = u.hostname or ''
        try:
            if host in ('127.0.0.1', 'localhost') or u.scheme in ('chrome', 'chrome-extension', 'blob', 'data'):
                return route.continue_()
            if req.url.startswith('https://accounts.google.com/gsi/client'):
                return route.fulfill(status=200, body=GIS_STUB, content_type='text/javascript')
            if host == 'www.googleapis.com':
                return self.fake.api(route, req)
            if host == 'lh3.googleusercontent.com':
                return self.fake.thumb(route, req)
            if host == 'cdnjs.cloudflare.com' and self.allow_cdn:
                return route.continue_()
            self.blocked.append(req.url)
            return route.abort()
        except Exception as e:  # a crash in the fake must show up as a failure, not a hang
            self.errors.append('fake drive crashed on %s %s: %r' % (req.method, req.url, e))
            try:
                route.fulfill(status=500, body=b'{"error":{"code":500,"message":"fake crashed"}}', content_type='application/json', headers=CORS)
            except Exception:
                pass

    def close(self):
        self.ctx.close()

    # ---- page helpers
    def names(self):
        return self.page.eval_on_selector_all('#list .it', 'els=>els.map(e=>e.dataset.name)')

    def wait_names(self, present=(), absent=(), timeout=8000):
        end = time.time() + timeout / 1000.0
        while time.time() < end:
            n = self.names()
            if all(p in n for p in present) and not any(a in n for a in absent):
                return n
            self.page.wait_for_timeout(80)
        n = self.names()
        check(False, 'list never showed %s / hid %s; it shows %s' % (list(present), list(absent), n[:12]))
        return n

    def item(self, name):
        return self.page.locator('#list .it[data-name="%s"]' % name)

    def open(self, name):
        self.item(name).locator('.open').click()

    def menu(self, name, act):
        self.item(name).locator('.more').click()
        self.page.locator('#menuDlg [data-act="%s"]' % act).click()

    def crumbs(self):
        return self.page.eval_on_selector_all('#crumbs [data-id]', 'els=>els.map(e=>e.textContent.trim())')

    def nav(self, view):
        self.page.locator('#nav [data-view="%s"]' % view).first.click()
        self.page.wait_for_function("(v)=>document.querySelector('#nav [data-view=\"'+v+'\"]').getAttribute('aria-current')==='page' && !document.querySelector('#main .loading')", arg=view)

    def go_root(self):
        self.nav('drive')
        self.page.wait_for_function("document.querySelector('#crumbs') && document.querySelector('#crumbs').textContent.trim()==='My Drive'")

    def wait_done(self, name, timeout=15000):
        self.page.locator('#tray .up[data-name="%s"][data-state="done"]' % name).wait_for(timeout=timeout)

    def move_to(self, path):
        dlg = self.page.locator('#moveDlg')
        dlg.wait_for(state='visible')
        self.page.locator('#moveCrumbs [data-id]').first.click()
        for p in path:
            self.page.locator('#moveList .mrow[data-name="%s"]' % p).click()
            self.page.wait_for_function("(n)=>[...document.querySelectorAll('#moveCrumbs [data-id]')].pop().textContent.trim()===n", arg=p)
        self.page.locator('#moveHere').click()
        dlg.wait_for(state='hidden')

    def ls(self, key):
        return self.page.evaluate('(k)=>localStorage.getItem(k)', key)


# ------------------------------------------------------------------ the checks
def run_section(name, fn, *a):
    SECTION[0] = name
    print('-', name)
    try:
        fn(*a)
    except Exception as e:
        check(False, 'crashed: %s: %s' % (type(e).__name__, str(e).splitlines()[0][:300] if str(e) else ''))


def main_flow(s):
    f, p = s.fake, s.page

    def signed_out():
        p.goto(s.base + 'shoebox.html')
        p.locator('#signin').wait_for()
        check(not f.calls, 'no Drive request before sign-in (got %d)' % len(f.calls))
        check(s.ls('drivesync.token') is None, 'Shoebox never writes the other pages\' drivesync.token')

    def sign_in():
        p.locator('#signin').click()
        s.wait_names(['Family', 'Documents', 'Passport scan.pdf', 'sunset.png'])
        n = s.names()
        check(n[:4] == ['Bulk', 'Documents', 'Empty', 'Family'], 'folders come first, by name: %s' % n[:5])
        check('old receipt.pdf' not in n, 'trashed files stay out of My Drive')
        scope = p.evaluate('window.__gis && window.__gis.scope') or ''
        check('https://www.googleapis.com/auth/drive' in scope.split(), 'asks Google for full-Drive access (scope was %r)' % scope)
        check('drive.file' not in scope, 'does not ask for drive.file')
        check(s.ls('shoebox.token') is not None, 'token kept under shoebox.token')
        check(s.ls('drivesync.token') is None, 'drivesync.token untouched after sign-in')
        p.wait_for_function("document.querySelector('#storage') && /GB/.test(document.querySelector('#storage').textContent)")
        check('6.2' in p.text_content('#storage') and '15' in p.text_content('#storage'), 'storage bar reads 6.2 of 15 GB: %r' % p.text_content('#storage'))
        p.wait_for_function("document.querySelector('#acct') && document.querySelector('#acct').textContent.indexOf('tester@example.com')>=0")
        check(s.crumbs() == ['My Drive'], 'path shows My Drive: %s' % s.crumbs())

    def browse():
        s.open('Family')
        s.wait_names(['beach day.png', 'Old photos'], ['sunset.png'])
        check(s.crumbs() == ['My Drive', 'Family'], 'path is My Drive / Family: %s' % s.crumbs())
        check(s.names()[0] == 'Old photos', 'sub-folder first in Family')
        p.wait_for_function("""[...document.querySelectorAll('#list .it[data-kind="image"] img')].length>=3 &&
            [...document.querySelectorAll('#list .it[data-kind="image"] img')].every(i=>i.complete&&i.naturalWidth>0)""", timeout=8000)
        check(f.bearer_thumbs == 0, 'thumbnails load as plain images, never fetched with the token (%d tries)' % f.bearer_thumbs)
        p.locator('#crumbs [data-id]').first.click()
        s.wait_names(['sunset.png'], ['beach day.png'])
        check(s.crumbs() == ['My Drive'], 'clicking My Drive in the path goes back up')
        p.go_back()
        s.wait_names(['beach day.png'])
        check(s.crumbs() == ['My Drive', 'Family'], 'browser Back returns to Family')

    def new_folder():
        p.locator('#newFolderBtn').click()
        p.locator('#nameInp').fill('Trips')
        p.locator('#nameOk').click()
        s.wait_names(['Trips'])
        t = f.one('Trips')
        check(t['mimeType'] == FOLDER and t['parents'] == [f.ids['Family']], 'Trips is a folder inside Family')

    def upload():
        small, big = png(20, 20, (10, 200, 10)), bytes(range(256)) * (6 * 1024 * 4 + 7)
        p.set_input_files('#upInput', files=[{'name': 'small.png', 'mimeType': 'image/png', 'buffer': small},
                                             {'name': 'big.bin', 'mimeType': 'application/octet-stream', 'buffer': big}])
        s.wait_done('small.png'); s.wait_done('big.bin', 30000)
        s.wait_names(['small.png', 'big.bin'])
        check(('multipart', 'small.png') in f.uploads, 'a small file uses one multipart request')
        check(('resumable', 'big.bin') in f.uploads, 'a file over 5 MB uses a resumable upload')
        sp, bp = f.one('small.png'), f.one('big.bin')
        check(sp['content'] == small and sp['parents'] == [f.ids['Family']], 'small.png arrives intact in Family')
        check(bp['content'] == big and bp['mimeType'] == 'application/octet-stream', 'big.bin arrives intact (%d of %d bytes)' % (len(bp['content']), len(big)))

    def rename():
        s.menu('small.png', 'rename')
        check(p.input_value('#nameInp') == 'small.png', 'rename starts from the current name')
        p.locator('#nameInp').fill('tiny.png')
        p.locator('#nameOk').click()
        s.wait_names(['tiny.png'], ['small.png'])
        check(f.files[f.one('tiny.png')['id']]['name'] == 'tiny.png', 'Drive has the new name')

    def move():
        s.menu('tiny.png', 'move')
        s.move_to(['Family', 'Trips'])
        s.wait_names([], ['tiny.png'])
        check(f.one('tiny.png')['parents'] == [f.one('Trips')['id']], 'tiny.png moved into Trips')
        s.menu('Trips', 'move')
        p.locator('#moveDlg').wait_for(state='visible')
        p.locator('#moveCrumbs [data-id]').first.click()
        p.locator('#moveList .mrow[data-name="Family"]').click()
        row = p.locator('#moveList .mrow[data-name="Trips"]')
        row.wait_for()
        check(row.is_disabled(), 'a folder cannot be moved into itself (row is disabled)')
        p.keyboard.press('Escape')
        p.locator('#moveDlg').wait_for(state='hidden')

    def ask(name_bits):
        p.locator('#confirmDlg').wait_for(state='visible')
        txt = p.text_content('#confirmText')
        check(all(b in txt for b in name_bits), 'the question says %s: %r' % (' and '.join(name_bits), txt))
        focused = p.evaluate("document.activeElement && document.activeElement.id")
        check(focused == 'confirmNo', 'Cancel has the focus, so Enter does not delete by accident (focus on %r)' % focused)

    def trash_undo():
        s.menu('grandma.png', 'trash')
        ask(['grandma.png'])
        p.locator('#confirmNo').click()
        p.locator('#confirmDlg').wait_for(state='hidden')
        check(not f.one('grandma.png')['trashed'] and 'grandma.png' in s.names(), 'Cancel leaves the file where it was')
        s.menu('grandma.png', 'trash')
        ask(['grandma.png'])
        p.keyboard.press('Escape')
        p.locator('#confirmDlg').wait_for(state='hidden')
        check(not f.one('grandma.png')['trashed'], 'Esc cancels too')
        s.menu('grandma.png', 'trash')
        ask(['grandma.png'])
        p.locator('#confirmYes').click()
        s.wait_names([], ['grandma.png'])
        check(f.one('grandma.png')['trashed'], 'grandma.png is in Drive\'s Trash once you say yes')
        p.locator('#toast .undo').click()
        s.wait_names(['grandma.png'])
        check(not f.one('grandma.png')['trashed'], 'Undo brings grandma.png back')

    def folder_trash():
        s.menu('Trips', 'trash')
        p.locator('#confirmDlg').wait_for(state='visible')
        txt = p.text_content('#confirmText')
        check('Trips' in txt and '1 item' in txt, 'the question names the folder and its 1 item: %r' % txt)
        p.locator('#confirmYes').click()
        s.wait_names([], ['Trips'])
        check(f.one('Trips')['trashed'] and f.one('tiny.png')['trashed'], 'Trips and what is in it are in Trash')
        s.nav('trash')
        s.wait_names(['Trips', 'old receipt.pdf'], ['tiny.png'])
        check(True, 'Trash lists what you deleted, not every file inside a deleted folder')
        s.menu('Trips', 'restore')
        s.wait_names([], ['Trips'])
        check(not f.one('Trips')['trashed'] and not f.one('tiny.png')['trashed'], 'Restore brings back Trips and its contents')
        check(not any(c[0] == 'DELETE' for c in f.calls), 'nothing was ever permanently deleted')

    def star():
        s.go_root()
        s.menu('Documents', 'star')
        p.wait_for_function("document.querySelector('#list .it[data-name=\"Documents\"]').dataset.starred==='1'")
        check(f.one('Documents')['starred'], 'Documents is starred in Drive')
        s.nav('starred')
        s.wait_names(['Documents'], ['Family'])
        s.open('Documents')
        s.wait_names(['tax 2025.pdf'])
        check(s.crumbs() == ['My Drive', 'Documents'], 'a folder opened from Starred shows its real path: %s' % s.crumbs())

    def search():
        p.locator('#q').fill('beach')
        p.locator('#q').press('Enter')
        s.wait_names(['beach day.png'], ['sunset.png'])
        p.locator('#q').fill('day')
        p.locator('#q').press('Enter')
        s.wait_names(['beach day.png'], ['birthday cake.png', 'sunset.png'])
        check(True, 'search matches the start of words, as Drive does ("day" finds "beach day", not "birthday")')
        p.locator('#q').fill('ach')
        p.locator('#q').press('Enter')
        p.wait_for_function("document.querySelectorAll('#list .it').length===0 && /No/.test(document.querySelector('#main').textContent)")
        check(True, 'a search with no match says so')
        check("name contains 'ach'" in (f.lists()[-1][2].get('q') or ''), 'search asks Drive by name')

    def filters():
        s.go_root(); s.open('Family')
        s.wait_names(['family tree.pdf'])
        p.locator('#filter [data-f="photos"]').click()
        s.wait_names(['beach day.png', 'Old photos'], ['family tree.pdf'])
        check("mimeType contains 'image/'" in (f.lists()[-1][2].get('q') or ''), 'Photos asks Drive for images only')
        p.locator('#filter [data-f="pdfs"]').click()
        s.wait_names(['family tree.pdf', 'Old photos'], ['beach day.png'])
        p.locator('#filter [data-f="all"]').click()
        s.wait_names(['family tree.pdf', 'beach day.png'])
        check(s.ls('shoebox.filter') in ('"all"', 'all'), 'filter choice saved in this browser')

    def multiselect():
        p.locator('#selBtn').click()
        s.item('beach day.png').locator('.ck').check()
        s.item('birthday cake.png').locator('.ck').check()
        check('2' in p.text_content('#selCount'), 'selection count says 2: %r' % p.text_content('#selCount'))
        p.locator('#selbar [data-sel="move"]').click()
        s.move_to(['Family', 'Old photos'])
        s.wait_names([], ['beach day.png', 'birthday cake.png'])
        old = f.ids['Old photos']
        check(f.one('beach day.png')['parents'] == [old] and f.one('birthday cake.png')['parents'] == [old], 'both photos moved to Old photos')
        s.open('Old photos')
        s.wait_names(['beach day.png', 'birthday cake.png', 'wedding 1998.png'])
        if not p.locator('#selbar').is_visible():
            p.locator('#selBtn').click()
        s.item('beach day.png').locator('.ck').check()
        s.item('birthday cake.png').locator('.ck').check()
        p.locator('#selbar [data-sel="trash"]').click()
        ask(['2 items'])
        p.locator('#confirmYes').click()
        s.wait_names(['wedding 1998.png'], ['beach day.png', 'birthday cake.png'])
        check(f.one('beach day.png')['trashed'] and f.one('birthday cake.png')['trashed'], 'both photos in Trash')
        p.locator('#toast .undo').click()
        s.wait_names(['beach day.png', 'birthday cake.png'])
        check(not f.one('beach day.png')['trashed'] and not f.one('birthday cake.png')['trashed'], 'Undo restores both')
        p.locator('#selBtn').click()
        s.item('wedding 1998.png').locator('.ck').check()
        p.keyboard.press('Delete')
        ask(['wedding 1998.png'])
        p.keyboard.press('Escape')
        p.locator('#confirmDlg').wait_for(state='hidden')
        check(not f.one('wedding 1998.png')['trashed'], 'the Delete key asks first too, and Esc keeps the file')
        p.keyboard.press('Escape')
        p.wait_for_function("!document.querySelector('#selbar') || document.querySelector('#selbar').hidden")

    def download():
        s.go_root()
        with p.expect_download() as d:
            s.menu('sunset.png', 'download')
        dl = d.value
        check(dl.suggested_filename == 'sunset.png', 'download is named sunset.png: %r' % dl.suggested_filename)
        check(open(dl.path(), 'rb').read() == f.one('sunset.png')['content'], 'downloaded bytes match Drive')
        with p.expect_download() as d2:
            s.menu('Notes', 'download')
        dl2 = d2.value
        check(dl2.suggested_filename == 'Notes.pdf', 'a Google Doc downloads as Notes.pdf: %r' % dl2.suggested_filename)
        check(open(dl2.path(), 'rb').read()[:5] == b'%PDF-', 'the Google Doc download is a PDF')
        check((f.ids['Notes'], 'application/pdf') in f.exports, 'Drive was asked to export the Doc as PDF')

    def viewer():
        s.open('Family')
        s.wait_names(['grandma.png'])
        s.open('grandma.png')
        p.locator('#viewer').wait_for(state='visible')
        check(p.text_content('#vName').strip() == 'grandma.png', 'viewer shows grandma.png')
        p.wait_for_function("(()=>{var i=document.querySelector('#vBody img');return i&&i.complete&&i.naturalWidth===64;})()")
        check(True, 'full-size photo loads from Drive')
        first = p.text_content('#vName').strip()
        p.keyboard.press('ArrowRight')
        p.wait_for_function("(n)=>document.querySelector('#vName').textContent.trim()!==n", arg=first)
        check(True, 'Right arrow goes to the next item (%s)' % p.text_content('#vName').strip())
        p.keyboard.press('ArrowLeft')
        p.wait_for_function("(n)=>document.querySelector('#vName').textContent.trim()===n", arg=first)
        p.keyboard.press('Escape')
        p.locator('#viewer').wait_for(state='hidden')
        check(s.crumbs() == ['My Drive', 'Family'], 'closing the viewer stays in Family')
        s.open('grandma.png')
        p.locator('#viewer').wait_for(state='visible')
        p.go_back()
        p.locator('#viewer').wait_for(state='hidden')
        check(s.crumbs() == ['My Drive', 'Family'], 'phone Back closes the viewer and stays in Family: %s' % s.crumbs())
        s.open('family tree.pdf')
        p.locator('#viewer').wait_for(state='visible')
        p.wait_for_function("(()=>{var f=document.querySelector('#vBody iframe');return f&&/^blob:/.test(f.src);})()")
        check(True, 'PDF opens in the browser\'s own viewer')
        p.locator('#vClose').click()
        p.locator('#viewer').wait_for(state='hidden')

    def paging():
        s.go_root(); s.open('Bulk')
        s.wait_names(['file 001.txt'])
        check(len(s.names()) == 100, 'first page shows 100 of 130 (%d)' % len(s.names()))
        p.locator('#moreBtn').click()
        p.wait_for_function("document.querySelectorAll('#list .it').length===130")
        check(True, 'Load more brings all 130')

    def sorting():
        s.go_root()
        p.select_option('#sortSel', 'size')
        p.wait_for_timeout(300)
        check('quotaBytesUsed desc' in (f.lists()[-1][2].get('orderBy') or ''), 'size sort asks Drive for biggest first')
        p.select_option('#sortSel', 'name')
        s.wait_names(['Family'])
        p.locator('#viewBtn').click()
        check('rows' in (p.get_attribute('#list', 'class') or ''), 'view button switches to the list view')
        p.locator('#viewBtn').click()
        check('grid' in (p.get_attribute('#list', 'class') or ''), 'and back to the grid')

    def recent():
        s.nav('recent')
        p.wait_for_function("document.querySelectorAll('#list .it').length>0")
        last = f.lists()[-1][2]
        check('modifiedTime desc' in (last.get('orderBy') or ''), 'Recent is newest first')
        check("mimeType!='%s'" % FOLDER in (last.get('q') or '').replace(' ', ''), 'Recent leaves folders out')
        check(not p.locator('#upBtn').is_enabled(), 'Upload is off outside a folder')

    def expiry():
        s.go_root()
        f.kill_tokens()
        s.open('Family')
        p.locator('#signin').wait_for()
        check(s.ls('shoebox.token') is None, 'an expired token is forgotten')
        p.locator('#signin').click()
        s.wait_names(['grandma.png'])
        check(s.crumbs() == ['My Drive', 'Family'], 'signing back in returns to where you were')

    def theme():
        before = p.evaluate("getComputedStyle(document.body).backgroundColor")
        p.locator('#themeBtn').click()
        after = p.evaluate("getComputedStyle(document.body).backgroundColor")
        check(before != after, 'theme button switches light/dark')
        check(s.ls('shoebox.theme') is not None, 'theme saved as shoebox.theme')
        p.reload()
        s.wait_names(['grandma.png'])
        check(p.evaluate("getComputedStyle(document.body).backgroundColor") == after, 'theme survives a reload (and so does the sign-in and folder)')
        p.locator('#themeBtn').click()

    def file_types():
        s.go_root()
        s.wait_names(['Family', 'sunset.png'])
        check(not s.item('Family').locator('.ty').count(), 'a folder carries no type label')
        check(not s.item('sunset.png').locator('.ty').count(), 'a photo\'s thumbnail is not covered by a type label')
        s.open('Papers')
        s.wait_names(list(PAPERS))
        got = p.evaluate("""()=>[...document.querySelectorAll('#list .it')].map(e=>{var c=e.querySelector('.th .ty');
            return [e.dataset.name,e.dataset.type||'',c?c.textContent.trim():'',!!c&&c.getBoundingClientRect().width>0];})""")
        rows = {g[0]: g[1:] for g in got}
        for name, (_, key, label, _) in PAPERS.items():
            r = rows.get(name, ['', '', False])
            check(r[0] == key, '%s is typed %r (got %r)' % (name, key, r[0]))
            check(r[1] == label and r[2], '%s shows a visible %r label on its tile (got %r, visible=%s)' % (name, label, r[1], r[2]))
        look = p.evaluate("""(ns)=>ns.map(n=>{var t=document.querySelector('#list .it[data-name="'+n+'"] .th');
            return [getComputedStyle(t).color,t.querySelector('svg path').getAttribute('d')];})""", ['Budget', 'Minutes', 'Letter.docx', 'Accounts.xlsx'])
        check(look[0][0] != look[1][0], 'a Google Sheet and a Google Doc have different colours (%s vs %s)' % (look[0][0], look[1][0]))
        check(look[0][1] != look[1][1], 'a Google Sheet and a Google Doc have different icons')
        check(look[2][1] != look[1][1], 'a Word file and a Google Doc have different icons')
        check(look[3][1] != look[0][1], 'an Excel file and a Google Sheet have different icons')
        for name in ('Accounts.xlsx', 'Budget'):
            s.item(name).locator('.more').click()
            facts = p.text_content('#menuFacts') or ''
            check(PAPERS[name][3] in facts, 'the menu calls %s a %s: %r' % (name, PAPERS[name][3], facts))
            p.keyboard.press('Escape')
            p.locator('#menuDlg').wait_for(state='hidden')
        p.locator('#viewBtn').click()
        lab = p.evaluate("""()=>{var e=document.querySelector('#list .it[data-name="Accounts.xlsx"] .tyl');
            return e&&e.getBoundingClientRect().width>0?e.textContent.trim():'';}""")
        check(lab == 'Excel', 'the list view names the type too (%r)' % lab)
        p.locator('#viewBtn').click()

    def drag_drop():
        s.go_root()
        s.wait_names(['Empty', 'sunset.png'])
        hint = p.locator('#dropHint')
        check(hint.is_visible() and 'My Drive' in (hint.text_content() or ''), 'a visible hint says files can be dragged in, to My Drive: %r' % (hint.text_content() if hint.count() else None))
        if hint.count():
            with p.expect_file_chooser() as fc:
                hint.click()
            fc.value.set_files(files=[{'name': 'picked.txt', 'mimeType': 'text/plain', 'buffer': b'picked'}])
            s.wait_done('picked.txt')
            s.wait_names(['picked.txt'])
            check(f.one('picked.txt')['parents'] == [f.root], 'clicking the hint opens the file picker and uploads into My Drive')

        def drag(target, name):
            dt = p.evaluate_handle("(n)=>{var d=new DataTransfer();d.items.add(new File(['dropped '+n],n,{type:'text/plain'}));return d;}", name)
            target.dispatch_event('dragenter', {'dataTransfer': dt})
            target.dispatch_event('dragover', {'dataTransfer': dt})
            return dt

        def gone():
            return not p.locator('#drop').is_visible() and not p.locator('#list .droptarget').count()

        tile = s.item('Empty')
        dt = drag(tile.locator('.open'), 'into-empty.txt')
        check(p.locator('#drop').is_visible(), 'dragging files in shows the drop overlay')
        check('Empty' in (p.text_content('#drop') or ''), 'over a folder, the overlay names that folder: %r' % p.text_content('#drop'))
        check('droptarget' in (tile.get_attribute('class') or ''), 'the folder under the pointer is highlighted')
        tile.locator('.open').dispatch_event('drop', {'dataTransfer': dt})
        s.wait_done('into-empty.txt')
        check(f.one('into-empty.txt')['parents'] == [f.ids['Empty']], 'dropping on a folder uploads into that folder')
        check('into-empty.txt' not in s.names(), 'and not into the folder on screen')
        check(gone(), 'the overlay and the highlight go away after the drop')

        other = s.item('sunset.png').locator('.open')
        dt = drag(other, 'here.txt')
        check('My Drive' in (p.text_content('#drop') or ''), 'over a file, the overlay names the folder you are in: %r' % p.text_content('#drop'))
        check(not p.locator('#list .droptarget').count(), 'no folder is highlighted over a file')
        other.dispatch_event('drop', {'dataTransfer': dt})
        s.wait_done('here.txt')
        s.wait_names(['here.txt'])
        check(f.one('here.txt')['parents'] == [f.root], 'dropping anywhere else uploads into the folder you are in')

        docs = s.item('Documents').locator('.open')
        dt = drag(docs, 'never.txt')
        docs.dispatch_event('dragleave', {'dataTransfer': dt})
        check(gone(), 'dragging away clears the overlay and the highlight')
        check(not f.by_name('never.txt'), 'and uploads nothing')

        s.nav('recent')
        p.wait_for_function("document.querySelectorAll('#list .it').length>0")
        check(not p.locator('#dropHint').is_visible(), 'no drop hint where nothing can be uploaded (Recent)')

    def sign_out():
        p.locator('#signOut').click()
        p.locator('#signin').wait_for()
        check(s.ls('shoebox.token') is None, 'sign out forgets the token')
        check(not p.evaluate('window.__gisRevoked'), 'sign out does not revoke (that would sign the other pages out too)')

    for name, fn in [('signed out', signed_out), ('sign in', sign_in), ('browse', browse), ('new folder', new_folder),
                     ('upload', upload), ('rename', rename), ('move', move), ('trash + undo', trash_undo),
                     ('folder trash + restore', folder_trash), ('star', star), ('search', search), ('filters', filters),
                     ('select several', multiselect), ('download', download), ('viewer', viewer), ('load more', paging),
                     ('sort + view', sorting), ('recent', recent), ('expired sign-in', expiry), ('theme', theme),
                     ('file types', file_types), ('drag and drop', drag_drop), ('sign out', sign_out)]:
        run_section(name, fn)
    SECTION[0] = 'whole run'
    check(not f.deletes, 'no DELETE request ever reached Drive: %s' % f.deletes[:3])


def fallback_flow(s):
    f, p = s.fake, s.page
    f.flags.update(thumb_img=False)

    def thumbs():
        p.goto(s.base + 'shoebox.html')
        p.locator('#signin').click()
        s.wait_names(['Family'])
        s.open('Family')
        s.wait_names(['beach day.png'])
        p.wait_for_function("""[...document.querySelectorAll('#list .it[data-kind="image"] img')].length>=3 &&
            [...document.querySelectorAll('#list .it[data-kind="image"] img')].every(i=>i.complete&&i.naturalWidth>0&&/^blob:/.test(i.src))""", timeout=10000)
        check(any(m == f.ids['beach day.png'] for m in f.media), 'when Google\'s thumbnail links are refused, thumbnails are made from the photo itself')

    def unreadable():
        f.flags['resumable'] = 'response_unreadable'
        p.set_input_files('#upInput', files=[{'name': 'big2.bin', 'mimeType': 'application/octet-stream', 'buffer': b'a' * (5 * 1024 * 1024 + 10)}])
        s.wait_done('big2.bin', 30000)
        s.wait_names(['big2.bin'])
        check(len(f.by_name('big2.bin')) == 1, 'an upload whose answer was blocked is not uploaded twice (%d copies)' % len(f.by_name('big2.bin')))

    def blocked():
        f.flags['resumable'] = 'preflight_blocked'
        p.set_input_files('#upInput', files=[{'name': 'big3.bin', 'mimeType': 'application/octet-stream', 'buffer': b'b' * (5 * 1024 * 1024 + 10)}])
        s.wait_done('big3.bin', 30000)
        s.wait_names(['big3.bin'])
        check(len(f.by_name('big3.bin')) == 1 and ('multipart', 'big3.bin') in f.uploads, 'when resumable upload is blocked, it falls back to one multipart upload')

    def reload():
        p.reload()
        s.wait_names(['big3.bin'])

    for name, fn in [('thumbnail fallback', thumbs), ('resumable blocked', blocked), ('reload', reload), ('upload answer blocked', unreadable)]:
        run_section(name, fn)


def pdfjs_flow(s):
    p = s.page

    def pdfjs():
        p.goto(s.base + 'shoebox.html')
        p.locator('#signin').click()
        s.wait_names(['Passport scan.pdf'])
        s.open('Passport scan.pdf')
        p.locator('#viewer').wait_for(state='visible')
        p.wait_for_function("(()=>{var c=document.querySelector('#vBody canvas');return c&&c.width>0;})()", timeout=20000)
        check(True, 'without a built-in PDF viewer (Android), PDF.js draws the pages')
    run_section('PDF.js viewer (needs internet for cdnjs)', pdfjs)


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
        for w, h in ((390, 844), (1280, 900)):
            s = Session(browser, base, FakeDrive(), viewport=(w, h))
            p = s.page
            try:
                def one():
                    p.emulate_media(color_scheme=scheme)
                    p.goto(base + 'shoebox.html')
                    p.locator('#signin').wait_for()
                    check(p.evaluate('document.documentElement.scrollWidth') <= w, 'signed-out page fits %d px' % w)
                    p.locator('#signin').click()
                    s.wait_names(['Family'])
                    s.open('Family')
                    s.wait_names(['beach day.png'])
                    p.wait_for_timeout(300)
                    check(p.evaluate('document.documentElement.scrollWidth') <= w, 'folder view fits %d px without sideways scroll (%d)' % (w, p.evaluate('document.documentElement.scrollWidth')))
                    p.locator('#selBtn').click()
                    s.item('beach day.png').locator('.ck').check()
                    check(p.evaluate('document.documentElement.scrollWidth') <= w, 'selection bar fits %d px' % w)
                    p.keyboard.press('Escape')
                    s.open('grandma.png')
                    p.locator('#viewer').wait_for(state='visible')
                    vb = p.evaluate("(()=>{var r=document.querySelector('#viewer').getBoundingClientRect();return [r.width,r.height];})()")
                    check(vb[0] <= w + 1, 'viewer fits %d px' % w)
                    p.keyboard.press('Escape')
                    if w == 390:
                        cols = p.evaluate("getComputedStyle(document.querySelector('#list')).gridTemplateColumns.split(' ').length")
                        check(cols == 3, 'phone grid has 3 columns (%d)' % cols)
                    v = p.evaluate("""(()=>{var cs=getComputedStyle(document.documentElement);var o={};
                        ['--bg','--surface','--surface-2','--text','--muted','--faint','--accent','--accent-ink','--danger'].forEach(k=>o[k]=cs.getPropertyValue(k).trim());return o;})()""")
                    for fg in ('--text', '--muted', '--faint', '--accent', '--danger'):
                        for bg in ('--bg', '--surface', '--surface-2'):
                            r = ratio(v[fg], v[bg])
                            check(r >= 4.5, '%s on %s is %.2f:1 in %s (needs 4.5)' % (fg, bg, r, scheme))
                    r = ratio(v['--accent-ink'], v['--accent'])
                    check(r >= 4.5, 'button text on accent is %.2f:1 in %s' % (r, scheme))
                    s.go_root()
                    s.open('Papers')
                    s.wait_names(list(PAPERS))
                    p.wait_for_timeout(200)
                    check(p.evaluate('document.documentElement.scrollWidth') <= w, 'typed files fit %d px' % w)
                    # every colour resolved through a canvas, so color-mix() and rgb() come back as plain hex
                    chips = p.evaluate("""()=>{var cv=document.createElement('canvas');cv.width=cv.height=1;var x=cv.getContext('2d',{willReadFrequently:true});
                        function hex(c){x.clearRect(0,0,1,1);x.fillStyle='#000';x.fillStyle=c;x.fillRect(0,0,1,1);var d=x.getImageData(0,0,1,1).data;
                          return '#'+[d[0],d[1],d[2]].map(v=>v.toString(16).padStart(2,'0')).join('');}
                        return [...document.querySelectorAll('#list .th .ty')].map(e=>{var t=e.closest('.th').getBoundingClientRect(),r=e.getBoundingClientRect(),cs=getComputedStyle(e);
                          return [e.closest('.it').dataset.name,hex(cs.color),hex(cs.backgroundColor),r.left>=t.left-0.5&&r.right<=t.right+0.5&&r.width>0];});}""")
                    check(len(chips) == len(PAPERS), 'every file in Papers has a type label (%d of %d)' % (len(chips), len(PAPERS)))
                    for name, fg, bg, inside in chips:
                        r = ratio(fg, bg)
                        check(r >= 4.5, 'the %s label is %.2f:1 in %s (needs 4.5)' % (name, r, scheme))
                        check(inside, 'the %s label stays inside its tile at %d px' % (name, w))
                run_section('layout %s %dpx' % (scheme, w), one)
                for e in s.errors:
                    check(False, 'browser error: ' + e)
            finally:
                s.close()


def site_flow(browser):
    httpd, base = serve(os.path.join(ROOT, 'site'))
    s = Session(browser, base, FakeDrive())
    try:
        def unconfigured():
            s.page.goto(base + 'shoebox.html')
            s.page.wait_for_function("/not set up/i.test(document.querySelector('#main').textContent)")
            check(not s.page.locator('#signin').count() or not s.page.locator('#signin').is_enabled(), 'no sign-in button without a client ID')
            check(not s.fake.calls, 'no Drive calls without a client ID')
        run_section('site/ without config.js', unconfigured)
        for e in s.errors:
            check(False, 'browser error: ' + e)
    finally:
        s.close(); httpd.shutdown()


def main():
    docs = os.path.join(ROOT, 'docs')
    if not os.path.exists(os.path.join(docs, 'shoebox.html')):
        print('docs/shoebox.html does not exist yet; run tools/build-site.py')
        sys.exit(1)
    httpd, base = serve(docs)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=not HEADED)
        for label, flow, kw in [('main', main_flow, {}), ('fallbacks', fallback_flow, {}), ('pdf.js', pdfjs_flow, {'pdf_viewer': False, 'allow_cdn': True})]:
            print('==', label)
            s = Session(browser, base, FakeDrive(), **kw)
            try:
                flow(s)
                SECTION[0] = label + ': browser'
                for e in s.errors:
                    check(False, 'browser error: ' + e)
                odd = [u for u in s.blocked if not re.match(r'https://fonts\.(googleapis|gstatic)\.com/', u)]
                check(not odd, 'no request left for an unexpected host: %s' % odd[:3])
            finally:
                s.close()
        print('== layout')
        layout_flow(browser, base)
        print('== site/')
        site_flow(browser)
        browser.close()
    httpd.shutdown()
    print('\n%d checks passed, %d failed' % (PASSES[0], len(FAILS)))
    for x in FAILS:
        print('  ' + x)
    sys.exit(1 if FAILS else 0)


if __name__ == '__main__':
    main()
