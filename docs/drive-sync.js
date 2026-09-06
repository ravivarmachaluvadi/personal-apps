/* drive-sync.js — the record for each app is one JSON file in the user's own Google Drive.
 *
 *   DriveStore.open({file, cacheKey, empty, onChange, onStatus, merge?, debounceMs?, pollMs?})
 *     .doc                 the current document (plain object; {v, items:{id:{...updatedAt, deleted?}}})
 *     .mutate(fn)          fn(doc) edits in place; the change is cached at once and saved shortly after
 *     .sync()              pull from Drive (merge) and push if anything is waiting
 *     .signIn() / .signOut()
 *   DriveStore.mountStatus(element, store)   renders the sign-in button / sync state into an element
 *
 * Design: the browser keeps a cache of the document plus a "dirty" flag, so the page renders instantly
 * and works offline. Google Drive (scope drive.file, via a "Continue with Google" token) holds the file
 * that every device reads and writes. Merge is per item by updatedAt; deletions are tombstones so a
 * stale device cannot resurrect them. A device that finds its own items missing from Drive re-uploads
 * them, which heals the rare clobber when two devices save in the same second.
 */
(function (global) {
  'use strict';
  var CFG = global.APP_CONFIG || {};
  var SCOPE = 'https://www.googleapis.com/auth/drive.file openid email';
  var DRIVE = 'https://www.googleapis.com/drive/v3';
  var UPLOAD = 'https://www.googleapis.com/upload/drive/v3';
  var TOKEN_KEY = 'drivesync.token';
  var TOMBSTONE_DAYS = 45;

  function loadJSON(k, d) { try { var v = JSON.parse(localStorage.getItem(k)); return v == null ? d : v; } catch (e) { return d; } }
  function saveJSON(k, v) { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }

  /* ---------------- auth (shared by every store on the page) ---------------- */
  var auth = { token: '', exp: 0, email: '', client: null, gis: null, listeners: [], reason: '' };
  (function restore() { var t = loadJSON(TOKEN_KEY, null); if (t && t.token && t.exp > Date.now() + 30000) { auth.token = t.token; auth.exp = t.exp; auth.email = t.email || ''; } })();
  function hasToken() { return !!auth.token && auth.exp > Date.now(); }
  function authState() { return { signedIn: hasToken(), email: auth.email, configured: !!CFG.googleClientId, reason: auth.reason }; }
  function emit() { auth.listeners.forEach(function (fn) { try { fn(authState()); } catch (e) {} }); }
  function loadGis() {
    if (auth.gis) return auth.gis;
    auth.gis = new Promise(function (res, rej) {
      if (global.google && global.google.accounts && global.google.accounts.oauth2) return res();
      var s = document.createElement('script'); s.src = 'https://accounts.google.com/gsi/client'; s.async = true; s.defer = true;
      s.onload = function () { res(); }; s.onerror = function () { auth.gis = null; rej(new Error('gis')); };
      document.head.appendChild(s);
    });
    return auth.gis;
  }
  function tokenClient() {
    return loadGis().then(function () {
      if (!auth.client) auth.client = global.google.accounts.oauth2.initTokenClient({ client_id: CFG.googleClientId, scope: SCOPE, callback: function () {}, error_callback: function () {} });
      return auth.client;
    });
  }
  /* Call from a user gesture (a click); browsers block the Google popup otherwise. */
  function signIn(opts) {
    opts = opts || {};
    return new Promise(function (resolve, reject) {
      if (!CFG.googleClientId) { auth.reason = 'not_configured'; emit(); return reject({ code: 'not_configured' }); }
      tokenClient().then(function (c) {
        c.callback = function (resp) {
          if (!resp || resp.error) { auth.reason = (resp && resp.error) || 'error'; emit(); return reject({ code: auth.reason }); }
          auth.token = resp.access_token; auth.exp = Date.now() + ((+resp.expires_in || 3600) * 1000) - 60000; auth.reason = '';
          fetch('https://www.googleapis.com/oauth2/v3/userinfo', { headers: { Authorization: 'Bearer ' + auth.token } })
            .then(function (r) { return r.ok ? r.json() : null; }).then(function (u) { if (u && u.email) auth.email = u.email; })
            .catch(function () {})
            .then(function () { saveJSON(TOKEN_KEY, { token: auth.token, exp: auth.exp, email: auth.email }); emit(); resolve(authState()); });
        };
        c.error_callback = function (err) { auth.reason = (err && err.type) || 'error'; emit(); reject({ code: auth.reason }); };
        try { c.requestAccessToken({ prompt: opts.consent ? 'consent' : '' }); } catch (e) { reject({ code: 'request_failed' }); }
      }, function () { auth.reason = 'gis_load'; emit(); reject({ code: 'gis_load' }); });
    });
  }
  function signOut() {
    var t = auth.token; auth.token = ''; auth.exp = 0; auth.email = ''; auth.reason = ''; saveJSON(TOKEN_KEY, null);
    if (t && global.google && global.google.accounts) { try { global.google.accounts.oauth2.revoke(t, function () {}); } catch (e) {} }
    emit();
  }
  function expire() { auth.token = ''; auth.exp = 0; saveJSON(TOKEN_KEY, null); auth.reason = 'expired'; emit(); }
  setInterval(function () { if (auth.token && !hasToken()) expire(); }, 30000);

  /* ---------------- Drive REST ---------------- */
  function api(url, opts) {
    opts = opts || {};
    if (!hasToken()) return Promise.reject({ code: 'signed_out' });
    var headers = Object.assign({ Authorization: 'Bearer ' + auth.token }, opts.headers || {});
    return fetch(url, Object.assign({}, opts, { headers: headers })).then(function (r) {
      if (r.status === 401) { expire(); throw { code: 'signed_out' }; }
      if (r.status === 404) throw { code: 'not_found' };
      if (r.status === 403) return r.text().then(function (t) { throw { code: 'forbidden', message: t.slice(0, 300) }; });
      if (!r.ok) return r.text().then(function (t) { throw { code: 'http', status: r.status, message: t.slice(0, 300) }; });
      return r;
    }, function () { throw { code: 'network' }; });
  }
  function q(s) { return String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'"); }
  function findFolder(name) {
    return api(DRIVE + '/files?q=' + encodeURIComponent("name='" + q(name) + "' and mimeType='application/vnd.google-apps.folder' and trashed=false") + '&fields=files(id,name)&pageSize=5')
      .then(function (r) { return r.json(); }).then(function (j) { return j.files && j.files[0] ? j.files[0].id : null; });
  }
  function createFolder(name) {
    return api(DRIVE + '/files?fields=id', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name, mimeType: 'application/vnd.google-apps.folder' }) })
      .then(function (r) { return r.json(); }).then(function (j) { return j.id; });
  }
  function findFiles(folderId, name) {
    return api(DRIVE + '/files?q=' + encodeURIComponent("name='" + q(name) + "' and '" + folderId + "' in parents and trashed=false") + '&fields=files(id,name,modifiedTime)&orderBy=modifiedTime%20desc&pageSize=10')
      .then(function (r) { return r.json(); }).then(function (j) { return j.files || []; });
  }
  function readFile(id) { return api(DRIVE + '/files/' + id + '?alt=media').then(function (r) { return r.json(); }); }
  function fileMeta(id) { return api(DRIVE + '/files/' + id + '?fields=id,modifiedTime,trashed').then(function (r) { return r.json(); }); }
  function trashFile(id) { return api(DRIVE + '/files/' + id + '?fields=id', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ trashed: true }) }); }
  function createFile(folderId, name, obj) {
    var boundary = '----drivesync' + Math.random().toString(36).slice(2);
    var body = '--' + boundary + '\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n' + JSON.stringify({ name: name, parents: [folderId], mimeType: 'application/json' }) +
      '\r\n--' + boundary + '\r\nContent-Type: application/json\r\n\r\n' + JSON.stringify(obj) + '\r\n--' + boundary + '--';
    return api(UPLOAD + '/files?uploadType=multipart&fields=id,modifiedTime', { method: 'POST', headers: { 'Content-Type': 'multipart/related; boundary=' + boundary }, body: body }).then(function (r) { return r.json(); });
  }
  function updateFile(id, obj) {
    return api(UPLOAD + '/files/' + id + '?uploadType=media&fields=id,modifiedTime', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(obj) }).then(function (r) { return r.json(); });
  }

  /* ---------------- merge ---------------- */
  function isObj(x) { return x && typeof x === 'object' && !Array.isArray(x); }
  function defaultMerge(local, remote) {
    local = isObj(local) ? local : {}; remote = isObj(remote) ? remote : {};
    var out = {}, localNewer = false, keys = {};
    Object.keys(local).concat(Object.keys(remote)).forEach(function (k) { keys[k] = 1; });
    Object.keys(keys).forEach(function (k) {
      var a = local[k], b = remote[k];
      if (k === 'items') {
        var items = {}, ids = {};
        Object.keys(isObj(a) ? a : {}).concat(Object.keys(isObj(b) ? b : {})).forEach(function (id) { ids[id] = 1; });
        Object.keys(ids).forEach(function (id) {
          var x = a && a[id], y = b && b[id];
          if (!x) items[id] = y;
          else if (!y) { items[id] = x; localNewer = true; }
          else if ((+x.updatedAt || 0) > (+y.updatedAt || 0)) { items[id] = x; localNewer = true; }
          else items[id] = y;
        });
        out.items = items;
      } else if (isObj(a) && isObj(b) && ('updatedAt' in a || 'updatedAt' in b)) {
        if ((+a.updatedAt || 0) > (+b.updatedAt || 0)) { out[k] = a; localNewer = true; } else out[k] = b;
      } else if (a === undefined) out[k] = b;
      else if (b === undefined) { out[k] = a; localNewer = true; }
      else out[k] = b;
    });
    return { doc: out, localNewer: localNewer };
  }
  function purgeTombstones(doc) {
    if (!doc || !isObj(doc.items)) return;
    var cut = Date.now() - TOMBSTONE_DAYS * 86400000;
    Object.keys(doc.items).forEach(function (id) { var it = doc.items[id]; if (it && it.deleted && (+it.updatedAt || 0) < cut) delete doc.items[id]; });
  }

  /* ---------------- store ---------------- */
  function Store(o) {
    var self = this;
    this.o = o; this.file = o.file; this.folderName = CFG.driveFolderName || 'Personal Apps'; this.cacheKey = o.cacheKey || ('drivesync.' + o.file);
    var c = loadJSON(this.cacheKey, null);
    this.doc = (c && isObj(c.doc)) ? c.doc : o.empty(); if (!isObj(this.doc.items)) this.doc.items = {};
    this.dirty = !!(c && c.dirty); this.fileId = (c && c.fileId) || ''; this.folderId = (c && c.folderId) || ''; this.remoteModified = (c && c.remoteModified) || '';
    this.synced = false; this.busy = false; this.timer = null; this.status = { state: 'idle', text: '' }; this.lastError = '';
    auth.listeners.push(function () { self.setStatus(); if (hasToken()) self.sync(); });
    document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'visible') self.sync(); });
    global.addEventListener('online', function () { self.sync(); });
    global.addEventListener('pagehide', function () { if (self.dirty) self.persist(); });
    setInterval(function () { if (document.visibilityState === 'visible' && hasToken() && !self.busy && !self.dirty) self.pull(); }, o.pollMs || 90000);
    setTimeout(function () { self.setStatus(); if (hasToken()) self.sync(); }, 0);
  }
  Store.prototype.persist = function () { saveJSON(this.cacheKey, { doc: this.doc, dirty: this.dirty, fileId: this.fileId, folderId: this.folderId, remoteModified: this.remoteModified }); };
  Store.prototype.setStatus = function (s) {
    if (s) this.status = s;
    var st = this.status, a = authState(), text;
    if (!a.configured) text = 'Drive sign-in not set up yet';
    else if (!a.signedIn) text = (a.reason === 'expired' ? 'Session expired' : 'Not signed in') + (this.dirty ? ' · changes waiting' : '');
    else if (st.state === 'saving') text = 'Saving to Drive…';
    else if (st.state === 'pulling') text = 'Checking Drive…';
    else if (st.state === 'pending') text = 'Change waiting…';
    else if (st.state === 'error') text = 'Drive error' + (this.lastError ? ': ' + this.lastError : '');
    else if (st.state === 'offline') text = 'Offline · will save when back';
    else if (st.state === 'saved') text = 'Saved in Drive' + (a.email ? ' · ' + a.email : '');
    else text = 'Connecting…';
    this.status.text = text;
    if (this.o.onStatus) { try { this.o.onStatus(this.status, a); } catch (e) {} }
    this.renderMounts();
  };
  Store.prototype.mutate = function (fn) {
    var self = this; fn(this.doc); this.dirty = true; this.persist();
    if (this.o.onChange) this.o.onChange(this.doc, 'local');
    this.setStatus({ state: hasToken() ? 'pending' : (navigator.onLine === false ? 'offline' : 'pending') });
    clearTimeout(this.timer); this.timer = setTimeout(function () { self.push(); }, this.o.debounceMs || 1500);
  };
  Store.prototype.merge = function (remote) {
    var res = this.o.merge ? this.o.merge(this.doc, remote) : defaultMerge(this.doc, remote);
    this.doc = res.doc; if (!isObj(this.doc.items)) this.doc.items = {};
    if (res.localNewer) this.dirty = true;
    this.persist();
    if (this.o.onChange) this.o.onChange(this.doc, 'remote');
  };
  Store.prototype.ensureFile = function () {
    var self = this;
    var p = this.folderId ? Promise.resolve(this.folderId) : findFolder(this.folderName).then(function (id) { return id || createFolder(self.folderName); });
    return p.then(function (fid) { self.folderId = fid; if (self.fileId) return true;
      return findFiles(fid, self.file).then(function (files) {
        if (!files.length) return false;
        self.fileId = files[0].id;
        // more than one copy (two devices created it at once): fold the others in, then bin them
        var extras = files.slice(1);
        return extras.reduce(function (chain, f) { return chain.then(function () { return readFile(f.id).then(function (d) { self.merge(d); return trashFile(f.id); }).catch(function () {}); }); }, Promise.resolve()).then(function () { return true; });
      });
    }).then(function (ok) { self.persist(); return ok; });
  };
  Store.prototype.pull = function (force) {
    var self = this;
    if (!hasToken() || this.busy) return Promise.resolve();
    this.busy = true; this.setStatus({ state: 'pulling' });
    return this.ensureFile().then(function (exists) {
      if (!exists) { self.synced = true; return Object.keys(self.doc.items).length || self.dirty ? self._push() : self.setStatus({ state: 'saved' }); }
      return fileMeta(self.fileId).then(function (m) {
        if (m.trashed) { self.fileId = ''; self.remoteModified = ''; return self._push(); }
        if (force || m.modifiedTime !== self.remoteModified) {
          return readFile(self.fileId).then(function (remote) { self.merge(remote); self.remoteModified = m.modifiedTime; self.persist(); self.synced = true; return self.dirty ? self._push() : self.setStatus({ state: 'saved' }); });
        }
        self.synced = true;
        return self.dirty ? self._push() : self.setStatus({ state: 'saved' });
      });
    }).catch(function (e) { self.fail(e); }).then(function () { self.busy = false; });
  };
  Store.prototype.push = function () {
    var self = this;
    if (!this.dirty) return Promise.resolve();
    if (!hasToken()) { this.setStatus({ state: 'pending' }); return Promise.resolve(); }
    if (this.busy) { clearTimeout(this.timer); this.timer = setTimeout(function () { self.push(); }, 800); return Promise.resolve(); }
    this.busy = true;
    return this.ensureFile().then(function (exists) {
      if (!exists) return self._push();
      return fileMeta(self.fileId).then(function (m) {
        if (m.trashed) { self.fileId = ''; self.remoteModified = ''; return self._push(); }
        if (m.modifiedTime !== self.remoteModified) return readFile(self.fileId).then(function (remote) { self.merge(remote); self.remoteModified = m.modifiedTime; return self._push(); });
        return self._push();
      });
    }).catch(function (e) { self.fail(e); }).then(function () { self.busy = false; if (self.dirty && hasToken() && !self.timer) { self.timer = setTimeout(function () { self.timer = null; self.push(); }, 2500); } });
  };
  Store.prototype._push = function () {
    var self = this;
    this.setStatus({ state: 'saving' }); clearTimeout(this.timer); this.timer = null;
    purgeTombstones(this.doc);
    this.dirty = false; var snapshot = JSON.parse(JSON.stringify(this.doc));
    var p = this.fileId ? updateFile(this.fileId, snapshot) : createFile(this.folderId, this.file, snapshot);
    return p.then(function (r) { if (r && r.id) self.fileId = r.id; if (r && r.modifiedTime) self.remoteModified = r.modifiedTime; self.synced = true; self.persist(); self.setStatus({ state: self.dirty ? 'pending' : 'saved' }); },
      function (e) { self.dirty = true; self.persist(); throw e; });
  };
  Store.prototype.fail = function (e) {
    var code = (e && e.code) || 'error';
    if (code === 'signed_out') { this.setStatus({ state: 'pending' }); return; }
    if (code === 'network') { this.setStatus({ state: 'offline' }); return; }
    if (code === 'not_found') { this.fileId = ''; this.remoteModified = ''; this.persist(); this.setStatus({ state: 'pending' }); return; }
    this.lastError = (e && (e.message || e.status || code)) ? String(e.message || e.status || code).slice(0, 120) : code;
    this.setStatus({ state: 'error' });
  };
  Store.prototype.sync = function () { var self = this; return this.pull().then(function () { if (self.dirty) return self.push(); }); };
  Store.prototype.signIn = function () { return signIn(); };
  Store.prototype.signOut = function () { signOut(); };
  Store.prototype.replaceDoc = function (doc) { this.doc = doc; if (!isObj(this.doc.items)) this.doc.items = {}; this.dirty = true; this.persist(); if (this.o.onChange) this.o.onChange(this.doc, 'local'); this.push(); };

  /* ---------------- status control ---------------- */
  var CSS = '.ds{display:inline-flex;align-items:center;gap:8px;font:12.5px ui-monospace,Menlo,Consolas,monospace;color:var(--muted,#666);position:relative}' +
    '.ds i{width:8px;height:8px;border-radius:50%;background:var(--faint,#aaa);display:inline-block;flex:none}' +
    '.ds[data-state="saved"] i{background:var(--good,#2e7d4f)}.ds[data-state="saving"] i,.ds[data-state="pending"] i,.ds[data-state="pulling"] i{background:var(--warn,#9a6212)}.ds[data-state="error"] i,.ds[data-state="offline"] i{background:var(--danger,#b42318)}' +
    '.ds button{font:inherit;cursor:pointer;border:0;border-radius:9px;color:inherit;background:var(--surface-2,#eee);padding:6px 10px;font-weight:600}' +
    '.ds .ds-google{display:inline-flex;align-items:center;gap:8px;background:var(--surface,#fff);border:1.5px solid var(--line,#ccc);color:var(--text,#111);font:600 13px system-ui,sans-serif;padding:7px 12px;border-radius:10px}' +
    '.ds .ds-google svg{width:16px;height:16px}' +
    '.ds .ds-text{cursor:pointer;text-decoration:underline dotted;text-underline-offset:3px}' +
    '.ds-menu{position:absolute;top:calc(100% + 6px);right:0;background:var(--surface,#fff);border:1px solid var(--line,#ccc);border-radius:10px;padding:8px;display:grid;gap:6px;min-width:200px;box-shadow:0 10px 30px rgba(0,0,0,.18);z-index:30;font:13px system-ui,sans-serif;color:var(--text,#111)}' +
    '.ds-menu span{color:var(--muted,#666);font-size:12px;overflow-wrap:anywhere}' +
    '.ds-menu button{text-align:left}';
  var cssDone = false;
  function ensureCss() { if (cssDone) return; cssDone = true; var s = document.createElement('style'); s.textContent = CSS; document.head.appendChild(s); }
  var mounts = [];
  Store.prototype.renderMounts = function () { var self = this; mounts.forEach(function (m) { if (m.store === self) renderMount(m); }); };
  function googleIcon() {
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', '0 0 48 48');
    [['#EA4335', 'M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.5 5.4 2.6 13.3l7.9 6.1C12.4 13.6 17.7 9.5 24 9.5z'], ['#4285F4', 'M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.6 3-2.3 5.5-4.8 7.2l7.5 5.8c4.4-4.1 7.1-10.1 7.1-17.5z'], ['#FBBC05', 'M10.5 28.6A14.5 14.5 0 0 1 9.5 24c0-1.6.3-3.2.8-4.6l-7.9-6.1A24 24 0 0 0 0 24c0 3.9.9 7.5 2.6 10.7l7.9-6.1z'], ['#34A853', 'M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.5-5.8c-2.1 1.4-4.9 2.3-8.4 2.3-6.3 0-11.6-4.1-13.5-9.9l-7.9 6.1C6.5 42.6 14.6 48 24 48z']].forEach(function (p) { var e = document.createElementNS('http://www.w3.org/2000/svg', 'path'); e.setAttribute('fill', p[0]); e.setAttribute('d', p[1]); svg.appendChild(e); });
    return svg;
  }
  function renderMount(m) {
    var el = m.el, store = m.store, a = authState();
    while (el.firstChild) el.removeChild(el.firstChild);
    el.className = 'ds'; el.dataset.state = a.signedIn ? store.status.state : (store.dirty ? 'pending' : 'idle');
    if (!a.configured) { var dot0 = document.createElement('i'); el.appendChild(dot0); el.appendChild(document.createTextNode('Drive sign-in not set up (see README)')); return; }
    if (!a.signedIn) {
      var b = document.createElement('button'); b.type = 'button'; b.className = 'ds-google'; b.appendChild(googleIcon()); b.appendChild(document.createTextNode(a.reason === 'expired' ? 'Continue with Google' : 'Continue with Google'));
      b.addEventListener('click', function () { b.disabled = true; store.signIn().catch(function () {}).then(function () { b.disabled = false; }); });
      el.appendChild(b);
      if (store.dirty) { var w = document.createElement('span'); w.textContent = 'changes waiting'; el.appendChild(w); }
      if (a.reason && a.reason !== 'expired') { var r = document.createElement('span'); r.textContent = a.reason === 'popup_failed_to_open' ? 'Allow pop-ups for this site, then try again' : a.reason === 'access_denied' ? 'Sign-in was cancelled' : a.reason === 'gis_load' ? 'Could not load Google sign-in (offline?)' : ''; if (r.textContent) el.appendChild(r); }
      return;
    }
    var dot = document.createElement('i'); el.appendChild(dot);
    var t = document.createElement('span'); t.className = 'ds-text'; t.textContent = store.status.text || 'Connecting…'; t.title = 'Sync options'; el.appendChild(t);
    t.addEventListener('click', function () {
      var old = el.querySelector('.ds-menu'); if (old) { old.remove(); return; }
      var menu = document.createElement('div'); menu.className = 'ds-menu';
      var who = document.createElement('span'); who.textContent = (a.email ? a.email + ' · ' : '') + 'file: ' + store.folderName + '/' + store.file; menu.appendChild(who);
      var s = document.createElement('button'); s.type = 'button'; s.textContent = 'Sync now'; s.addEventListener('click', function () { menu.remove(); store.pull(true); }); menu.appendChild(s);
      var o = document.createElement('button'); o.type = 'button'; o.textContent = 'Sign out of Google'; o.addEventListener('click', function () { menu.remove(); store.signOut(); }); menu.appendChild(o);
      el.appendChild(menu);
      setTimeout(function () { document.addEventListener('click', function close(ev) { if (!menu.contains(ev.target) && ev.target !== t) { menu.remove(); document.removeEventListener('click', close); } }); }, 0);
    });
  }
  function mountStatus(el, store) { ensureCss(); var m = { el: el, store: store }; mounts.push(m); renderMount(m); return m; }

  global.DriveStore = {
    open: function (o) { return new Store(o); },
    mountStatus: mountStatus, ensureCss: ensureCss,
    signIn: signIn, signOut: signOut, authState: authState, onAuth: function (fn) { auth.listeners.push(fn); }
  };
})(window);
