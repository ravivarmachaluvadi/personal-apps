"""Build docs/ (the hosted site) from the page sources.

  keycap-atlas.html, strongroom.html  ->  docs/keycap-atlas.html, docs/strongroom.html
      (artifact-style fragments get a real <html><head> and the Drive-backed store)
  site/dumbbell-dojo.html             ->  docs/dumbbell-dojo.html
      (the artifact's browser-only storage becomes per-key Drive sync)
  seed/export/keycap/shortcuts/*.json ->  docs/data/keycap-atlas.json (starter set)

Every replacement asserts that its anchor text exists, so a drifted source fails loudly.
"""
import json, os, glob, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, 'docs')
os.makedirs(os.path.join(DOCS, 'data'), exist_ok=True)


def rd(p):
    return open(os.path.join(ROOT, p), encoding='utf-8').read()


def wr(p, s):
    with open(os.path.join(ROOT, p), 'w', encoding='utf-8', newline='\n') as f:
        f.write(s)


def rep(s, old, new, count=1):
    assert old in s, 'anchor not found: ' + old[:80]
    return s.replace(old, new, count)


HEAD = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<style>:root{color-scheme:light}body{margin:0}[hidden]{display:none!important}</style>\n'
        '<script src="config.js"></script>\n<script src="drive-sync.js"></script>\n')


def wrap(fragment):
    head, body = fragment.split('</style>\n', 1)
    return HEAD + head + '</style>\n</head>\n<body>\n' + body + '\n</body>\n</html>\n'


# ---------------------------------------------------------------- Keycap Atlas
ka = rd('keycap-atlas.html')
ka = rep(ka, '<div class="sync" id="sync" data-state="boot"><i></i><span id="syncText">Connecting…</span></div>', '<div id="sync"></div>')
start = ka.index('  /* ---------- cloud-first store ---------- */')
end = ka.index('  /* ---------- state ---------- */')
ka = ka[:start] + r'''  /* ---------- Drive-backed store ---------- */
  var STORE=DriveStore.open({file:'keycap-atlas.json',cacheKey:'ka.doc',empty:function(){return {v:1,app:'keycap-atlas',items:{}};},onChange:function(){renderAll();},onStatus:function(st,a){maybeAutoStarter(a);}});
  DriveStore.mountStatus($('#sync'),STORE);
  function maybeAutoStarter(a){if(Object.keys(STORE.doc.items).length)return;var done=false;try{done=!!localStorage.getItem('ka.starterDone');}catch(e){}if(done)return;
    if(!a.configured||(a.signedIn&&STORE.synced)){try{localStorage.setItem('ka.starterDone','1');}catch(e){}loadStarter();}}
  function items(){var m=STORE.doc.items;return Object.keys(m).map(function(k){return m[k];}).filter(function(it){return it&&!it.deleted&&it.keys;});}
  function put(it){it.updatedAt=Date.now();if(!it.createdAt)it.createdAt=it.updatedAt;STORE.mutate(function(d){d.items[it.id]=it;});}
  function del(id){STORE.mutate(function(d){d.items[id]={id:id,deleted:true,updatedAt:Date.now()};});}
  var starterBusy=false;
  function loadStarter(){if(starterBusy)return;starterBusy=true;toast('Loading the starter set…');
    fetch('data/keycap-atlas.json').then(function(r){return r.json();}).then(function(j){var list=(j&&j.shortcuts)||[],now=Date.now(),n=0;try{localStorage.setItem('ka.starterDone','1');}catch(e){}
      STORE.mutate(function(d){list.forEach(function(s){if(!s||!s.id||!s.keys)return;if(!d.items[s.id]||d.items[s.id].deleted){s.updatedAt=now;s.createdAt=s.createdAt||now;d.items[s.id]=s;n++;}});});toast('Added '+n+' shortcuts');})
    .catch(function(){toast('Could not load the starter set');}).then(function(){starterBusy=false;});}

''' + ka[end:]
ka = rep(ka, "  function items(){return Object.keys(ITEMS).map(function(k){return ITEMS[k];});}\n", '')
ka = rep(ka, "(r.id&&!ITEMS[r.id]&&", "(r.id&&!STORE.doc.items[r.id]&&")
ka = rep(ka, "    if(!vis.length){var msg=items().length?'Nothing matches this filter.':'No shortcuts yet. Add your first one, or import a CSV.';box.appendChild(h('section',{class:'card'},[h('p',{class:'empty',text:msg})]));return;}",
         "    if(!vis.length){var any=items().length,sec=h('section',{class:'card'},[h('p',{class:'empty',text:any?'Nothing matches this filter.':'No shortcuts yet.'})]);if(!any)sec.appendChild(h('div',{class:'acts',style:'margin-top:10px'},[h('button',{class:'btn primary',type:'button',text:'Load the starter set (250 shortcuts)',onclick:loadStarter}),h('span',{class:'hint',text:'Windows, macOS, VS Code, Chrome, Excel, Gmail, Windows Terminal, Claude Code, Bash, IntelliJ, Slack, Terminal.'})]));box.appendChild(sec);return;}")
ka = rep(ka, "  renderAll();initCloud();\n  setInterval(function(){if(DB&&definitive&&Object.keys(PENDING).length)flushPending();},30000);\n", "  renderAll();\n")
ka = rep(ka, "<p>Everything you add is saved to this page's cloud database and shows up on every device you open the link from.",
         "<p>Everything you add is saved to <code>Personal Apps/keycap-atlas.json</code> in your own Google Drive and shows up on every device you sign in from. Without sign-in it still works, saved only in this browser.")
assert 'ITEMS' not in ka.split('<script>')[1] or 'ITEMS[' not in ka, 'stale ITEMS reference'
wr('docs/keycap-atlas.html', wrap(ka))

# ---------------------------------------------------------------- Strongroom
sr = rd('strongroom.html')
sr = rep(sr, '<div class="state" id="lockState" data-state="boot"><i></i><span id="lockStateText">Connecting…</span></div>', '<div id="lockState"></div>')
sr = rep(sr, '<div class="state" id="sync" data-state="boot"><i></i><span id="syncText">Connecting…</span></div>', '<div id="sync"></div>')
start = sr.index('  /* ---------- cloud-first store (ciphertext only) ---------- */')
end = sr.index('  /* ---------- session ---------- */')
sr = sr[:start] + r'''  /* ---------- Drive-backed store (ciphertext only) ---------- */
  var META=null,DOCS={},KEY=null,PLAIN={},undecryptable=0,rekeying=false;
  var STORE=DriveStore.open({file:'strongroom-vault.json',cacheKey:'sr.doc',empty:function(){return {v:1,format:'strongroom-vault',meta:null,items:{}};},
    onChange:function(doc,src){syncViews(src);},onStatus:function(){lockScreen();}});
  DriveStore.mountStatus($('#sync'),STORE);DriveStore.mountStatus($('#lockState'),STORE);
  function syncViews(src){var prevMeta=META,prevDocs=DOCS;META=(STORE.doc.meta&&STORE.doc.meta.salt&&STORE.doc.meta.check)?STORE.doc.meta:null;DOCS={};
    Object.keys(STORE.doc.items||{}).forEach(function(id){var d=STORE.doc.items[id];if(d&&!d.deleted&&d.ct&&d.iv)DOCS[id]=d;});
    if(KEY&&!rekeying){if(!META||(prevMeta&&prevMeta.vid!==META.vid)){var why=META?'The vault key changed on another device. Unlock again.':'The vault was removed on another device.';lock();toast(why);return;}
      var changed=Object.keys(DOCS).filter(function(id){return !prevDocs[id]||prevDocs[id].ct!==DOCS[id].ct;});Object.keys(PLAIN).forEach(function(id){if(!DOCS[id])delete PLAIN[id];});
      decryptMany(changed).then(renderAll);}
    lockScreen();}
  function storeMeta(meta){STORE.mutate(function(d){d.meta=meta;});}
  function storeDoc(doc){STORE.mutate(function(d){d.items[doc.id]=doc;});}
  function storeDel(id){delete PLAIN[id];STORE.mutate(function(d){d.items[id]={id:id,deleted:true,updatedAt:Date.now()};});}

''' + sr[end:]
# session block: remove the old sync-status helper usage and rewrite lockScreen
old_lock = sr[sr.index('  function lockScreen(){'):sr.index("  $('#unlockForm').addEventListener('submit'")]
sr = sr.replace(old_lock, r'''  function lockScreen(){var sub=$('#lockSub'),uf=$('#unlockForm'),cf=$('#createForm'),a=DriveStore.authState();
    if(!SUBTLE){sub.textContent='This browser cannot run the vault\'s encryption. Open the page over HTTPS in a current browser.';uf.hidden=cf.hidden=true;return;}
    if(META){sub.textContent='Enter your master password. It never leaves this device.';uf.hidden=false;cf.hidden=true;return;}
    if(a.signedIn&&!STORE.synced){sub.textContent='Looking in your Google Drive for an existing vault…';uf.hidden=cf.hidden=true;return;}
    if(!a.configured)sub.textContent='Google sign-in is not set up yet (config.js), so a vault created now would live only in this browser.';
    else if(!a.signedIn)sub.textContent='Sign in with Google first, so the vault can be found in your Drive or created there.';
    else sub.textContent='No vault in your Drive yet. Choose a master password: it encrypts everything before anything is stored.';
    uf.hidden=true;cf.hidden=!(a.signedIn||!a.configured);}
''')
sr = rep(sr, "  function lock(){KEY=null;PLAIN={};selected=null;PASTE=[];", "  function lock(){KEY=null;PLAIN={};selected=null;PASTE=[];rekeying=false;")
# change master password: one atomic write of every re-encrypted entry plus the new key record
sr = rep(sr, "openWith(cur,META).then(function(){msg.textContent='Re-encrypting…';return makeMeta(nw);}).then(function(r){var ids=Object.keys(PLAIN),i=0;\n      function next(){if(i>=ids.length)return Promise.resolve();var id=ids[i++],p=PLAIN[id],payload=clone(p);delete payload.id;return encryptJSON(r.key,payload,id).then(function(blob){var d={id:id,vid:r.meta.vid,iv:blob.iv,ct:blob.ct,updatedAt:p.updatedAt||Date.now()};DOCS[id]=d;PENDING[id]='set';cache();return DB?pushDoc(d):Promise.resolve();}).then(next);}\n      return next().then(function(){KEY=r.key;META=r.meta;PENDING.__meta=true;cache();return DB?pushMeta():Promise.resolve();});}).then(function(){msg.textContent='Done. Use the new password from now on, on every device.';$('#cpCur').value=$('#cpNew').value='';setMeter($('#cpMeter'),null,null,'');toast('Master password changed');}).catch(function(e){msg.textContent=e&&e.code?'Some entries were not saved to the cloud yet ('+e.code+'). They will retry; do not lock until the status shows saved.':'The current password is wrong.';}).then(function(){btn.disabled=false;});});",
         "openWith(cur,META).then(function(){msg.textContent='Re-encrypting…';return makeMeta(nw);}).then(function(r){var ids=Object.keys(PLAIN),i=0,out=[];\n      function next(){if(i>=ids.length)return Promise.resolve();var id=ids[i++],p=PLAIN[id],payload=clone(p);delete payload.id;return encryptJSON(r.key,payload,id).then(function(blob){out.push({id:id,vid:r.meta.vid,iv:blob.iv,ct:blob.ct,updatedAt:Date.now()});}).then(next);}\n      return next().then(function(){KEY=r.key;rekeying=true;try{STORE.mutate(function(d){out.forEach(function(x){d.items[x.id]=x;});d.meta=r.meta;});}finally{rekeying=false;}});}).then(function(){msg.textContent='Done. Use the new password from now on, on every device.';$('#cpCur').value=$('#cpNew').value='';setMeter($('#cpMeter'),null,null,'');toast('Master password changed');}).catch(function(){msg.textContent='The current password is wrong.';}).then(function(){btn.disabled=false;});});")
# delete vault
old_del = sr[sr.index("  $('#delVault').addEventListener('click'"):sr.index("  /* ---------- search & keys ---------- */")]
sr = sr.replace(old_del, "  $('#delVault').addEventListener('click',function(){if($('#delConfirm').value!=='DELETE')return;KEY=null;PLAIN={};var ids=Object.keys(DOCS),now=Date.now();STORE.mutate(function(d){ids.forEach(function(id){d.items[id]={id:id,deleted:true,updatedAt:now};});d.meta=null;});$('#settings').close();lock();toast('Vault deleted');});\n\n")
sr = rep(sr, "  lockScreen();initCloud();\n  setInterval(function(){if(DB&&definitive&&Object.keys(PENDING).length)flushPending();},30000);\n", "  syncViews('cache');\n")
sr = rep(sr, "The cloud copy that syncs between your devices is ciphertext only. Keep this artifact private and keep an encrypted backup from Settings somewhere safe.",
         "The file that syncs between your devices, <code>Personal Apps/strongroom-vault.json</code> in your own Google Drive, holds ciphertext only. Keep an encrypted backup from Settings somewhere safe as well.")
for stale in ['PENDING', 'initCloud', 'setSync(', 'metaDefinitive', 'DB.doc', 'pushMeta', 'pushDoc']:
    assert stale not in sr, 'stale reference: ' + stale
wr('docs/strongroom.html', wrap(sr))

# ---------------------------------------------------------------- Dumbbell Dojo
dj = rd('site/dumbbell-dojo.html')
dj = rep(dj, '<title>Dumbbell Dojo</title>', '<title>Dumbbell Dojo</title>\n<script src="config.js"></script>\n<script src="drive-sync.js"></script>')
dj = rep(dj, '<div class="tagline">home strength · a pair of dumbbells · every muscle, every variety</div>',
         '<div class="tagline">home strength · a pair of dumbbells · every muscle, every variety</div>\n    <div id="sync"></div>')
old_store = dj[dj.index('/* ---------- storage (safe) ---------- */'):dj.index('function todayKey(d){')]
dj = dj.replace(old_store, r'''/* ---------- storage: one JSON file in Google Drive, cached in this browser ---------- */
function sGet(k, fb){ try{ const v = localStorage.getItem(k); return v===null? fb : JSON.parse(v); }catch(e){ return fb; } }
function sSet(k, v){ try{ localStorage.setItem(k, JSON.stringify(v)); }catch(e){} }
let mode='ppl6', progress={}, complete={}, customEx={}, logs={}, standEvery=0;
const STORE = DriveStore.open({file:'dumbbell-dojo.json', cacheKey:'dd.doc', empty:()=>({v:1, app:'dumbbell-dojo', items:{}}),
  onChange:(doc, src)=>{ hydrate(); if(src==='remote' && BOOTED){ renderStrip(); renderToday(); renderWeek(); renderCare(); } }});
let BOOTED=false;
function itemVal(k, fb){ const it=STORE.doc.items[k]; return (it && !it.deleted && it.v!==undefined) ? it.v : fb; }
function collect(prefix){ const out={}; Object.keys(STORE.doc.items).forEach(k=>{ if(k.startsWith(prefix)){ const it=STORE.doc.items[k]; if(it && !it.deleted && it.v!==undefined) out[k.slice(prefix.length)]=it.v; } }); return out; }
function hydrate(){ mode=itemVal('mode','ppl6'); if(!SCHEDULES[mode]) mode='ppl6'; progress=collect('progress:'); complete=collect('complete:'); customEx=collect('customex:'); logs=collect('log:'); standEvery=itemVal('stand',0); }
function saveKey(k, v){ STORE.mutate(d=>{ d.items[k] = (v===undefined||v===null) ? {id:k, deleted:true, updatedAt:Date.now()} : {id:k, v:v, updatedAt:Date.now()}; }); }
function importDojo(o){ const now=Date.now(); let n=0; STORE.mutate(d=>{ const put=(k,v)=>{ d.items[k]={id:k, v:v, updatedAt:now}; n++; };
  if(o.mode) put('mode', o.mode); if(o.stand!=null) put('stand', o.stand);
  Object.entries(o.progress||{}).forEach(([k,v])=>put('progress:'+k, v)); Object.entries(o.complete||{}).forEach(([k,v])=>put('complete:'+k, v));
  Object.entries(o.customex||o.customEx||{}).forEach(([k,v])=>put('customex:'+k, v)); Object.entries(o.log||o.logs||{}).forEach(([k,v])=>put('log:'+k, v)); }); return n; }
function exportDojo(){ return {format:'dumbbell-dojo-export', v:1, exportedAt:new Date().toISOString(), mode, stand:standEvery, progress, complete, customex:customEx, log:logs}; }
(function migrateOld(){ if(Object.keys(STORE.doc.items).length) return;
  const o={mode:sGet('dd-mode',null), progress:sGet('dd-progress',null), complete:sGet('dd-complete',null), customex:sGet('dd-customex',null), log:sGet('dd-log',null), stand:sGet('dd-stand',null)};
  if(o.mode || o.progress || o.log || o.complete || o.customex) importDojo(o); })();
hydrate();
''')
dj = rep(dj, "function saveCustom(){ sSet('dd-customex', customEx); }", "function saveCustom(idx){ saveKey('customex:'+idx, customEx[idx]||null); }")
dj = rep(dj, "  if(cu.add.length===0 && cu.remove.length===0) delete customEx[idx];\n  else customEx[idx]=cu;\n  saveCustom();", "  if(cu.add.length===0 && cu.remove.length===0) delete customEx[idx];\n  else customEx[idx]=cu;\n  saveCustom(idx);")
dj = rep(dj, "if(rb) rb.addEventListener('click', ()=>{ delete customEx[dayIdx]; saveCustom(); renderToday(); renderStrip(); });", "if(rb) rb.addEventListener('click', ()=>{ delete customEx[dayIdx]; saveCustom(dayIdx); renderToday(); renderStrip(); });")
dj = rep(dj, "  if(arr.length) logs[exId]=arr; else delete logs[exId];\n  sSet('dd-log',logs);", "  if(arr.length) logs[exId]=arr; else delete logs[exId];\n  saveKey('log:'+exId, logs[exId]||null);")
dj = rep(dj, "      if(Object.keys(progress[dk]).length===0) delete progress[dk];\n      sSet('dd-progress',progress);", "      if(Object.keys(progress[dk]).length===0) delete progress[dk];\n      saveKey('progress:'+dk, progress[dk]||null);")
dj = rep(dj, "    if(fin) complete[dk]=true; else delete complete[dk];\n    sSet('dd-complete',complete);", "    if(fin) complete[dk]=true; else delete complete[dk];\n    saveKey('complete:'+dk, complete[dk]||null);")
dj = rep(dj, "      mode=btn.getAttribute('data-mode');\n      sSet('dd-mode',mode);", "      mode=btn.getAttribute('data-mode');\n      saveKey('mode',mode);")
dj = rep(dj, "  standEvery=min; sSet('dd-stand',min);", "  standEvery=min; saveKey('stand',min);")
dj = rep(dj, "heart condition, or any medical concern, check with a doctor before starting. Progress data lives only in this browser\n      on this device.</p>",
         "heart condition, or any medical concern, check with a doctor before starting.</p>\n      <h2>Your data</h2>\n      <p>Ticks, logs, plan choice and custom days are saved to <b>Personal Apps/dumbbell-dojo.json</b> in your Google Drive when you are signed in (top of the page), and cached in this browser so the page works offline. Every device you sign in from sees the same log.</p>\n      <p><button class=\"tbtn\" id=\"ddExport\" type=\"button\">Download my data (JSON)</button> <label class=\"tbtn\" style=\"cursor:pointer\">Import a backup <input type=\"file\" id=\"ddImport\" accept=\".json,application/json\" hidden></label> <span class=\"loglast\" id=\"ddImportMsg\"></span></p>")
dj = rep(dj, "      <p class=\"fine\">This plan is general fitness guidance", "      <p class=\"fine\">This plan is general fitness guidance")
dj = rep(dj, "    </div>`;\n}\n\nfunction renderStrip(){", r'''    </div>`;
  const ex=document.getElementById('ddExport'); if(ex) ex.addEventListener('click', ()=>{
    const blob=new Blob([JSON.stringify(exportDojo(),null,2)],{type:'application/json'}); const a=document.createElement('a');
    a.href=URL.createObjectURL(blob); a.download='dumbbell-dojo-'+todayKey()+'.json'; document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(a.href),2000); });
  const im=document.getElementById('ddImport'); if(im) im.addEventListener('change', ()=>{ const f=im.files&&im.files[0]; if(!f) return; const rd=new FileReader();
    rd.onload=()=>{ try{ const o=JSON.parse(rd.result); const n=importDojo(o); hydrate(); renderStrip(); renderToday(); renderWeek(); renderCare(); document.getElementById('ddImportMsg').textContent='Imported '+n+' records.'; }catch(e){ document.getElementById('ddImportMsg').textContent='That file is not a Dumbbell Dojo export.'; } im.value=''; };
    rd.readAsText(f); });
}

function renderStrip(){''')
dj = rep(dj, "/* ---------- boot ---------- */\nrenderStrip(); renderToday(); renderWeek(); renderLibrary(); renderGuide(); renderCare(); showView('today');",
         "/* ---------- boot ---------- */\nDriveStore.mountStatus(document.getElementById('sync'), STORE);\nrenderStrip(); renderToday(); renderWeek(); renderLibrary(); renderGuide(); renderCare(); showView('today'); BOOTED=true;")
assert "sSet('dd-" not in dj.replace("sSet('dd-vol'", ''), 'stale sSet'
wr('docs/dumbbell-dojo.html', dj)

# ---------------------------------------------------------------- Tally Board
tb = rd('site/tally-board.html')
tb = rep(tb, '<title>Tally Board</title>', '<title>Tally Board</title>\n<script src="config.js"></script>\n<script src="drive-sync.js"></script>')
tb = rep(tb, '<div class="sync" id="sync" data-state="local"><i></i><span id="syncText">Local only on this device</span></div>', '<div id="sync"></div>')
start = tb.index("  var ITEMS=load(KEY.items,{});")
end = tb.index("  /* ---------- activities ---------- */")
tb = tb[:start] + r'''  var STORE=DriveStore.open({file:'tally-board.json',cacheKey:'tally.doc',empty:function(){return {v:1,app:'tally-board',items:{}};},
    onChange:function(doc,src){ITEMS=doc.items;if(src==='remote'&&BOOTED)renderAll();}});
  var ITEMS=STORE.doc.items,BOOTED=false;
  (function migrateOld(){if(Object.keys(ITEMS).length)return;var old=load(KEY.items,null);if(old&&Object.keys(old).length){STORE.mutate(function(d){Object.keys(old).forEach(function(id){if(old[id]&&old[id].id&&old[id].kind)d.items[id]=old[id];});});}})();
  function live(kind){return Object.keys(ITEMS).map(function(k){return ITEMS[k];}).filter(function(i){return i&&i.kind===kind&&!i.deleted;});}
  function put(it){it.updatedAt=Date.now();STORE.mutate(function(d){d.items[it.id]=it;});renderAll();}
  function remove(id){var it=ITEMS[id];if(!it)return;it.deleted=true;put(it);}
  if(!Object.keys(ITEMS).length){(function seed(){
    STORE.mutate(function(d){
      [['seed-walk','Walk',0,{type:'daily',n:1}],['seed-dumbbell','Dumbbells',1,{type:'every',n:2}],['seed-badminton','Badminton',2,{type:'weekly',n:2}],['seed-cricket','Cricket',3,{type:'none',n:1}],['seed-rest','Rest day',4,{type:'weekly',n:1}]].forEach(function(s){d.items[s[0]]={id:s[0],kind:'activity',name:s[1],color:s[2],cad:s[3],dates:[],updatedAt:1};});
      d.items['seed-note']={id:'seed-note',kind:'note',text:'Things to remember go here.\nPin the important ones. Put a date on the ones that must not slip.',color:0,pinned:true,remind:'',done:false,updatedAt:1};});})();}

  /* ---------- sync status ---------- */
  DriveStore.mountStatus($('#sync'),STORE);

''' + tb[end:]
tb = rep(tb, "  initSync();\n", "  BOOTED=true;\n")
tb = rep(tb, "Everything you add syncs between your phone and laptop when opened through the artifact link.",
         "Everything you add is saved to <code>Personal Apps/tally-board.json</code> in your Google Drive and follows you to every device you sign in on.")
for stale in ['initSync', 'setSync(', 'queueSync', 'DB.doc']:
    assert stale not in tb, 'stale reference in tally: ' + stale
wr('docs/tally-board.html', tb)

# ---------------------------------------------------------------- Spine Bell
sb = rd('site/spine-bell.html')
sb = rep(sb, '<title>Spine Bell</title>', '<title>Spine Bell</title>\n<script src="config.js"></script>\n<script src="drive-sync.js"></script>')
sb = rep(sb, '<div class="sync" id="sync" data-state="local"><i></i><span id="syncText">Local only on this device</span></div>', '<div id="sync"></div>')
sb = rep(sb, "  var DAYS=load(KEY.days,{});", r'''  var STORE=DriveStore.open({file:'spine-bell.json',cacheKey:'spinebell.doc',empty:function(){return {v:1,app:'spine-bell',items:{}};},merge:mergeDays,
    onChange:function(doc,src){DAYS=doc.items;if(src==='remote'&&BOOTED){renderToday();renderDiary();renderCharts();}}});
  var DAYS=STORE.doc.items,BOOTED=false;
  (function migrateOld(){if(Object.keys(DAYS).length)return;var old=load(KEY.days,null);if(old&&Object.keys(old).length){STORE.mutate(function(d){Object.keys(old).forEach(function(k){if(old[k]&&old[k].day)d.items[k]=old[k];});});}})();''')
sb = rep(sb, "  function persistDays(){var cut=dayOffset(-90);Object.keys(DAYS).forEach(function(k){if(k<cut)delete DAYS[k];});save(KEY.days,DAYS);}",
         "  function persistDays(){var cut=dayOffset(-90);Object.keys(DAYS).forEach(function(k){if(k<cut)delete DAYS[k];});}")
start = sb.index("  /* ---------- cross-device sync (Artifact db, when available) ---------- */")
end = sb.index("  var el={")
sb = sb[:start] + r'''  /* ---------- cross-device sync: one JSON file in Google Drive; counters merge per device, diary by time ---------- */
  function mergeDays(local,remote){var out={},newer=false,keys={};
    Object.keys((local&&local.items)||{}).concat(Object.keys((remote&&remote.items)||{})).forEach(function(k){keys[k]=1;});
    Object.keys(keys).forEach(function(k){var a=local.items&&local.items[k],b=remote.items&&remote.items[k];
      if(!a){out[k]=b;return;}if(!b){out[k]=a;newer=true;return;}
      var rec={day:k,devices:{},diary:{}},ids={};
      Object.keys(a.devices||{}).concat(Object.keys(b.devices||{})).forEach(function(id){ids[id]=1;});
      Object.keys(ids).forEach(function(id){var x=(a.devices||{})[id],y=(b.devices||{})[id];
        if(!x)rec.devices[id]=y;else if(!y){rec.devices[id]=x;newer=true;}else if((+x.updatedAt||0)>(+y.updatedAt||0)){rec.devices[id]=x;newer=true;}else rec.devices[id]=y;});
      var da=a.diary||{},db=b.diary||{};if((+da.updatedAt||0)>(+db.updatedAt||0)){rec.diary=da;newer=true;}else rec.diary=Object.keys(db).length?db:da;
      out[k]=rec;});
    return {doc:{v:1,app:'spine-bell',items:out},localNewer:newer};}
  function queueSync(){STORE.mutate(function(){});}
  DriveStore.mountStatus($('#sync'),STORE);

''' + sb[end:]
sb = rep(sb, "  initSync();\n", "  BOOTED=true;\n")
sb = rep(sb, "Add this page to your home screen for one-tap access.</p>",
         "Add this page to your home screen for one-tap access. Counters and the diary are saved to <code>Personal Apps/spine-bell.json</code> in your Google Drive and follow you to every device you sign in on.</p>")
for stale in ['initSync', 'setSync(', 'pushDay', 'DB.doc', 'firstSnap']:
    assert stale not in sb, 'stale reference in spine bell: ' + stale
wr('docs/spine-bell.html', sb)

# ---------------------------------------------------------------- starter data
docs = []
for f in sorted(glob.glob(os.path.join(ROOT, 'seed', 'export', 'keycap', 'shortcuts', '*.json'))):
    j = json.load(open(f, encoding='utf-8'))
    d = j.get('data') if isinstance(j, dict) and isinstance(j.get('data'), dict) else j
    if d and d.get('id') and d.get('keys'):
        docs.append(d)
docs.sort(key=lambda d: d.get('order', 0))
with open(os.path.join(DOCS, 'data', 'keycap-atlas.json'), 'w', encoding='utf-8') as f:
    json.dump({'format': 'keycap-atlas', 'v': 1, 'shortcuts': docs}, f, ensure_ascii=False)
print('built docs/: keycap-atlas.html, strongroom.html, dumbbell-dojo.html, data/keycap-atlas.json (%d shortcuts)' % len(docs))
