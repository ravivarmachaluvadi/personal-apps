/* sign.js: sign and mark up a PDF or a photo, then hand back a signed copy. Loaded only when Sign is pressed.

   HubSign.open(file, {url, existing, kind, title, driveCopy, onSave(File, note, {driveCopy})})
       file is a Hub list entry; existing are the names already in its folder, so the copy never takes one of them.
       kind 'pdf' and a title open something else as a PDF (a Word file, made into a PDF by Word on the laptop).
       driveCopy {on, set(on), label} shows the "Also copy to Google Drive › Signed" switch (or label); onSave says
       what it was set to.
   HubSign.isOpen()
   HubSign.setup({pdfjs, pdflib, hand, keys, keeper, store})   once, before open, on a page other than Home Hub's:
       pdfjs/pdflib/hand   where PDF.js (a folder), pdf-lib and the Caveat handwriting font come from
       keys                the prefix of this device's own choices in localStorage ('hub.' here)
       keeper              where the signature is kept, in words ('the laptop' here)
       store               {get() -> {signature, initials}, put(name, png, aspect)}, each {png, aspect} or null;
                           Home Hub's is /api/signature
   The same file is Personal Apps' docs/sign.js (Sign Desk): keep the two identical; its checker compares them.

   The signature and the initials are kept by the store (on the laptop here), so every device signs with the same
   ones. Each device keeps a copy too, so a tap places them at once and they still work if the store does not answer;
   one drawn while it did not answer is marked pending and goes up the next time the signer opens.

   Marks are kept in "page units", the page as it is shown: PDF points with the page's own rotation applied
   (PDF.js's viewport at scale 1), or a photo's pixels. On screen they are drawn on a canvas over each page
   (ink) or placed as movable pieces (the signature, text). Saving draws them once more, onto one transparent
   PNG per marked page that pdf-lib lays over the original page, so the page's own text stays as sharp as it was;
   a photo is redrawn with the marks in it, in its own format.

   The remembered signature is kept colourless (dark grey) and tinted to the ink when it is placed. It is drawn in
   the box, or taken from a photo of it on paper: the paper is measured block by block, so a shadow stays paper. */
(function(){
  'use strict';

  /* Home Hub's settings; another page changes them with HubSign.setup before the first open */
  var CFG={pdfjs:'/static/vendor/pdfjs-6.3.289/',pdflib:'/static/vendor/pdf-lib-1.17.1/pdf-lib.min.js',
           hand:'/static/vendor/caveat-5.3.0/caveat-latin-400-normal.woff2',keys:'hub.',keeper:'the laptop',store:null};
  var SIG_KEY,INI_KEY,TEXTS_KEY,FINGER_KEY,INK_KEY,STYLE_KEY,TICK_KEY,SIZE_KEY;
  /* how thick the ink is. pen: the pen's line on a PDF, in points; sig: a drawn signature's (or initials') line once
     placed at its usual size on an A4-wide page, in points, whatever size it was written in the box. A Pencil's
     pressure still makes each line between half and one and a half times that. */
  var SIZES={fine:{pen:0.8,sig:1.0,label:'Fine'},medium:{pen:1.2,sig:1.5,label:'Medium'},bold:{pen:1.9,sig:2.2,label:'Bold'}};
  var A4_SHORT=595;   /* points: the page a placed signature's usual width is reckoned on */
  /* the two remembered pictures: where each is kept on this device, its name in the store, its pad, and how wide a
     tap places it (of the page's shorter side) */
  var PICS={
    sig:{name:'signature',title:'Your signature',width:0.36,alt:'Your signature',
         text:function(){return 'Sign in the box with the Pencil, a finger or the mouse, or use a photo of your signature on plain paper. It is kept on '+CFG.keeper+', so all your devices use it.';}},
    ini:{name:'initials',title:'Your initials',width:0.12,alt:'Your initials',
         text:function(){return 'Write your initials in the box with the Pencil, a finger or the mouse, or use a photo of them on plain paper. They are kept on '+CFG.keeper+', so all your devices use them.';}}};
  function applyKeys(){
    var p=CFG.keys;
    SIG_KEY=p+'signature';INI_KEY=p+'initials';TEXTS_KEY=p+'signTexts';FINGER_KEY=p+'fingerDraw';INK_KEY=p+'ink';STYLE_KEY=p+'textStyle';TICK_KEY=p+'tick';SIZE_KEY=p+'size';
    PICS.sig.key=SIG_KEY;PICS.ini.key=INI_KEY;
  }
  applyKeys();
  /* a tick and a cross, drawn as lines in a 1 × 1 square (no font has them in every place they are drawn) */
  var TICKS={check:[[[0.12,0.55],[0.4,0.82],[0.88,0.18]]],cross:[[[0.18,0.18],[0.82,0.82]],[[0.82,0.18],[0.18,0.82]]]};
  var TICK_W=0.14,SVGNS='http://www.w3.org/2000/svg';
  var INKS={black:'#111827',blue:'#3B2C9B'};   /* blue: a ballpoint's violet-blue, the hue of the owner's own pen */
  var FONT='Helvetica, Arial, sans-serif';
  var HAND='"Hub Hand", "Segoe Print", "Bradley Hand", cursive';   /* Caveat, from the hub; the others only if it will not load */
  var HAND_SIZE=1.4;       /* Caveat's letters are smaller than Helvetica's at the same size */
  var PHOTO_PX=2000,SIG_PX=1200;   /* a signature photo is read at most this big, and kept at most this big */
  var MAX_PX=16000000;     /* iPad Safari will not make a canvas much bigger than 16.7 million pixels */
  var PAGE_PX=6000000;     /* one page drawn on screen, at most */
  var ZOOMS=[1,1.5,2,3];
  var LOCKED='This PDF is locked against changes (or needs a password), so it cannot be signed here. On the iPad or iPhone, open it from the Files app and use Markup instead.';

  /* ---------- helpers (as in index.html) ---------- */
  function $(s){return document.querySelector(s);}
  function h(tag,attrs,kids){
    var e=document.createElement(tag);
    if(attrs)Object.keys(attrs).forEach(function(k){
      var v=attrs[k];if(v==null||v===false)return;
      if(k==='class')e.className=v;
      else if(k==='text')e.textContent=v;
      else if(k==='disabled')e.disabled=!!v;
      else if(k.slice(0,2)==='on')e.addEventListener(k.slice(2),v);
      else e.setAttribute(k,v===true?'':v);
    });
    (kids||[]).forEach(function(c){if(c==null)return;e.appendChild(typeof c==='string'?document.createTextNode(c):c);});
    return e;
  }
  function clear(el){while(el.firstChild)el.removeChild(el.firstChild);}
  function lsGet(k,d){try{var v=localStorage.getItem(k);return v==null?d:v;}catch(e){return d;}}
  function lsSet(k,v){try{if(v==null)localStorage.removeItem(k);else localStorage.setItem(k,v);return true;}catch(e){return false;}}
  function two(n){return (n<10?'0':'')+n;}
  function today(){var d=new Date();return two(d.getDate())+'/'+two(d.getMonth()+1)+'/'+d.getFullYear();}
  function todayWords(){return new Date().toLocaleDateString(undefined,{day:'numeric',month:'long',year:'numeric'});}
  function isToday(t){return t===today()||t===todayWords();}
  function recentTexts(){try{var a=JSON.parse(lsGet(TEXTS_KEY,'[]'));return Array.isArray(a)?a.filter(function(x){return typeof x==='string'&&x;}).slice(0,4):[];}catch(e){return [];}}
  function hubApi(method,path,body){
    var o={method:method,credentials:'same-origin',headers:{}};
    if(method!=='GET'){o.headers['X-Hub']='1';o.headers['Content-Type']='application/json';o.body=JSON.stringify(body||{});}
    return fetch(path,o).then(function(r){if(!r.ok)throw new Error('the hub answered '+r.status);return r.json();});
  }

  /* ---------- the libraries, loaded the first time they are needed ---------- */
  var PDFJS_P=null,PDFLIB_P=null;
  function loadPdfjs(){
    if(!PDFJS_P){PDFJS_P=import(CFG.pdfjs+'pdf.min.mjs').then(function(m){m.GlobalWorkerOptions.workerSrc=CFG.pdfjs+'pdf.worker.min.mjs';return m;});PDFJS_P.catch(function(){PDFJS_P=null;});}
    return PDFJS_P;
  }
  function loadPdfLib(){
    if(window.PDFLib)return Promise.resolve(window.PDFLib);
    if(!PDFLIB_P)PDFLIB_P=new Promise(function(res,rej){
      var s=document.createElement('script');s.src=CFG.pdflib;
      s.onload=function(){res(window.PDFLib);};s.onerror=function(){PDFLIB_P=null;rej(new Error('pdf-lib did not load'));};
      document.head.appendChild(s);
    });
    return PDFLIB_P;
  }
  /* the handwriting: resolves true once it is ready, false if it will not load (the fallbacks in HAND are used then,
     on screen and in the saved file alike); never fails */
  var HAND_P=null;
  function loadHand(){
    if(!HAND_P)HAND_P=new Promise(function(res){
      if(!window.FontFace||!document.fonts){res(false);return;}
      var f=new FontFace('Hub Hand','url('+CFG.hand+')');
      f.load().then(function(){document.fonts.add(f);res(true);},function(){HAND_P=null;res(false);});
    });
    return HAND_P;
  }

  /* ---------- the signer's own page, made once, with its own CSS (it uses the page's colours and .btn, .sm,
     .primary, .quiet, .danger, .solid and .tog) ---------- */
  var CSS=[
    "html.signing,html.signing body{overflow:hidden}",
    "#signer{position:fixed;inset:0;z-index:75;background:var(--surface-2);display:flex;flex-direction:column;color:var(--text)}",
    ".sbar{background:var(--surface);border-bottom:1px solid var(--line);padding:calc(8px + env(safe-area-inset-top,0px)) 12px 8px;display:grid;gap:6px;box-shadow:var(--shadow)}",
    ".srow{display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px;min-width:0}",
    ".sgrow{flex:1 1 auto}",
    "#sTitle{font-weight:600;overflow-wrap:anywhere;min-width:0;flex:1 1 160px;font-size:15px}",
    "#sTools [aria-pressed=\"true\"],.sinks [aria-pressed=\"true\"],.sstyles [aria-pressed=\"true\"],.sticks [aria-pressed=\"true\"],.ssizes [aria-pressed=\"true\"]{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}",
    ".sinks,.sstyles,.sticks,.ssizes{display:inline-flex;gap:4px}",
    "#sigPad,.sbar{-webkit-user-select:none;user-select:none;-webkit-touch-callout:none}",
    ".sbar input{-webkit-user-select:text;user-select:text}",
    ".smark.stick svg{width:100%;height:100%;display:block;overflow:visible;pointer-events:none}",
    "/* a tick is smaller than a fingertip: a wider area to grab it by, and its × and corner kept clear of it */",
    ".smark.stick::before{content:'';position:absolute;inset:-6px}",
    ".smark.stick .sdel{right:-30px;top:-30px}",
    ".smark.stick .shandle{right:-26px;bottom:-26px}",
    "#sText{flex:1 1 200px;width:auto;min-height:34px;font-size:15px}",
    "#sText.hand{font:21px/1.2 \"Hub Hand\",\"Segoe Print\",\"Bradley Hand\",cursive}",
    "#sMsg{margin:0;font-size:13.5px;color:var(--muted);min-height:1.4em}",
    "#sPages{flex:1;min-height:0;overflow:auto;padding:16px 12px 48px;display:flex;flex-direction:column;align-items:center;gap:16px;-webkit-overflow-scrolling:touch}",
    ".spage{position:relative;background:#FFFFFF;box-shadow:0 4px 18px rgba(0,0,0,.18);touch-action:pan-x pan-y pinch-zoom;flex:none;-webkit-user-select:none;user-select:none;-webkit-touch-callout:none}",
    ".fingerdraw .spage{touch-action:none}",
    ".spage canvas{position:absolute;inset:0;width:100%;height:100%;display:block}",
    ".smark{position:absolute;cursor:move;touch-action:none;-webkit-user-select:none;user-select:none;box-sizing:border-box}",
    ".smark img{width:100%;height:100%;display:block;pointer-events:none}",
    ".smark.stext{white-space:nowrap;line-height:1.2;font-family:Helvetica,Arial,sans-serif}",
    ".smark.sel{outline:2px dashed var(--accent);outline-offset:3px}",
    ".shandle{position:absolute;right:-11px;bottom:-11px;width:22px;height:22px;border-radius:50%;background:var(--accent);border:2px solid #FFFFFF;cursor:nwse-resize;display:none}",
    ".sdel{position:absolute;right:-13px;top:-13px;width:26px;height:26px;border-radius:50%;background:#B42318;color:#FFFFFF;border:2px solid #FFFFFF;cursor:pointer;display:none;padding:0;font:700 15px/22px var(--sans)}",
    ".sdel::before{content:'\\00D7'}",
    ".smark.sel .shandle,.smark.sel .sdel{display:block}",
    "#sAsk{position:fixed;left:50%;bottom:calc(18px + env(safe-area-inset-bottom,0px));transform:translateX(-50%);max-width:calc(100% - 24px);width:max-content;display:flex;flex-wrap:wrap;align-items:center;gap:8px 10px;background:var(--surface);color:var(--text);border:1px solid var(--line);border-radius:12px;padding:10px 12px;box-shadow:0 14px 34px rgba(0,0,0,.25);z-index:78;font-size:14.5px}",
    "#sAsk span{flex:1 1 200px}",
    "#sigPad{position:fixed;inset:0;background:rgba(8,10,14,.6);display:grid;place-items:center;z-index:80;padding:16px}",
    ".sigbox{background:var(--surface);color:var(--text);border-radius:16px;padding:16px;width:min(640px,100%);box-shadow:0 18px 40px rgba(0,0,0,.3)}",
    ".sigbox p{color:var(--muted);font-size:14px;margin:4px 0 10px}",
    ".sigbox #sigMsg{margin:0 0 8px;min-height:1.4em;font-size:13.5px}",
    "#sigCanvas{width:100%;height:200px;background:#FFFFFF;border:1.5px dashed #8A94A3;border-radius:10px;touch-action:none;display:block;margin-bottom:10px;cursor:crosshair}"];
  var UI=null,S=null;
  function build(){
    if(UI)return UI;
    if(!document.getElementById('hubsign-css')){var st=document.createElement('style');st.id='hubsign-css';st.textContent=CSS.join('\n');document.head.appendChild(st);}
    var el=h('div',{id:'signer',hidden:true,role:'dialog','aria-modal':'true','aria-labelledby':'sTitle','data-state':'closed','data-marks':'0'},[
      h('div',{class:'sbar'},[
        h('div',{class:'srow'},[
          h('b',{id:'sTitle'}),
          h('label',{class:'tog',id:'sDrive',hidden:true},[h('input',{type:'checkbox',id:'driveCopy'}),h('span',{text:'Also copy to Google Drive › Signed'})]),
          h('button',{class:'btn sm',id:'sCancel',type:'button',text:'Cancel'}),
          h('button',{class:'btn sm primary',id:'sSave',type:'button',disabled:true,text:'Save signed copy'})]),
        h('div',{class:'srow',id:'sTools',role:'toolbar','aria-label':'Tools'},[
          h('button',{class:'btn sm','data-tool':'pen',type:'button','aria-pressed':'false',text:'Pen'}),
          h('button',{class:'btn sm','data-tool':'sig',type:'button','aria-pressed':'false',text:'Signature'}),
          h('button',{class:'btn sm','data-tool':'ini',type:'button','aria-pressed':'false',text:'Initials'}),
          h('button',{class:'btn sm','data-tool':'text',type:'button','aria-pressed':'false',text:'Text'}),
          h('button',{class:'btn sm','data-tool':'tick',type:'button','aria-pressed':'false',text:'Tick ✓'}),
          h('button',{class:'btn sm',id:'sUndo',type:'button',disabled:true,text:'Undo'}),
          h('button',{class:'btn sm',id:'sEvery',type:'button',hidden:true,text:'Every page'}),
          h('span',{class:'sgrow'}),
          h('button',{class:'btn sm',id:'sZoomOut',type:'button','aria-label':'Zoom out',text:'−'}),
          h('button',{class:'btn sm',id:'sZoomIn',type:'button','aria-label':'Zoom in',text:'+'})]),
        h('div',{class:'srow',id:'sPenOpts'},[
          h('span',{class:'sinks',role:'group','aria-label':'Ink'},[
            h('button',{class:'btn sm','data-ink':'black',type:'button','aria-pressed':'false',text:'Black'}),
            h('button',{class:'btn sm','data-ink':'blue',type:'button','aria-pressed':'false',text:'Blue'})]),
          sizeButtons(),
          h('label',{class:'tog',id:'sFinger'},[h('input',{type:'checkbox',id:'fingerDraw'}),h('span',{text:'Draw with finger'})])]),
        h('div',{class:'srow',id:'sTextOpts',hidden:true},[
          h('span',{class:'sstyles',role:'group','aria-label':'Text style'},[
            h('button',{class:'btn sm','data-style':'hand',type:'button','aria-pressed':'false',text:'Handwriting'}),
            h('button',{class:'btn sm','data-style':'type',type:'button','aria-pressed':'false',text:'Typed'})]),
          h('input',{type:'text',id:'sText',maxlength:'120','aria-label':'Text to place'}),
          h('span',{id:'sChips',class:'srow'})]),
        h('div',{class:'srow',id:'sTickOpts',hidden:true},[
          h('span',{class:'sticks',role:'group','aria-label':'Tick or cross'},[
            h('button',{class:'btn sm','data-tick':'check',type:'button','aria-pressed':'false',text:'✓ Tick'}),
            h('button',{class:'btn sm','data-tick':'cross',type:'button','aria-pressed':'false',text:'✗ Cross'})])]),
        h('div',{class:'srow',id:'sSigOpts',hidden:true},[
          h('button',{class:'btn sm quiet',id:'sigRedo',type:'button',text:'Draw a new signature'})]),
        h('p',{id:'sMsg',role:'status'})]),
      h('div',{id:'sPages'}),
      h('div',{id:'sAsk',hidden:true,role:'alertdialog','aria-labelledby':'sAskText'},[
        h('span',{id:'sAskText',text:'Discard what you added? The file itself is not changed.'}),
        h('button',{class:'btn sm',id:'sAskKeep',type:'button',text:'Keep signing'}),
        h('button',{class:'btn sm danger solid',id:'sAskDiscard',type:'button',text:'Discard'})]),
      h('div',{id:'sigPad',hidden:true,role:'dialog','aria-modal':'true','aria-labelledby':'sigTitle'},[
        h('div',{class:'sigbox'},[
          h('h2',{id:'sigTitle',text:'Your signature'}),
          h('p',{id:'sigText',text:PICS.sig.text()}),
          h('div',{class:'srow',id:'sigSizes'},[sizeButtons()]),
          h('canvas',{id:'sigCanvas'}),
          h('p',{id:'sigMsg',role:'status'}),
          h('div',{class:'srow'},[
            h('button',{class:'btn sm',id:'sigClear',type:'button',text:'Clear'}),
            h('button',{class:'btn sm',id:'sigPhoto',type:'button',text:'Use a photo…'}),
            h('input',{type:'file',id:'sigFile',accept:'image/*',hidden:true}),
            h('span',{class:'sgrow'}),
            h('button',{class:'btn sm',id:'sigCancel',type:'button',text:'Cancel'}),
            h('button',{class:'btn sm primary',id:'sigUse',type:'button',disabled:true,text:'Use this signature'})])])])]);
    document.body.appendChild(el);
    UI=el;
    wire();
    return el;
  }

  function sizeButtons(){
    return h('span',{class:'ssizes',role:'group','aria-label':'Thickness'},Object.keys(SIZES).map(function(k){
      return h('button',{class:'btn sm','data-size':k,type:'button','aria-pressed':'false',text:SIZES[k].label});}));
  }
  function setState(s){UI.dataset.state=s;if(S)S.state=s;}
  function msg(t){$('#sMsg').textContent=t||'';}
  function total(){return S?S.pages.reduce(function(n,P){return n+P.marks.length;},0):0;}
  function counts(){
    var n=total();UI.dataset.marks=String(n);
    $('#sSave').disabled=!S||S.state!=='ready'||!n||S.saving;
    $('#sUndo').disabled=!S||!S.undo.length;
    paintSel();
  }
  /* Every page: offered while a piece (not ink) is selected in a PDF of more than one page */
  function paintSel(){
    $('#sEvery').hidden=!S||S.state!=='ready'||S.kind!=='pdf'||S.pages.length<2||!S.sel||S.sel.t==='ink'||!pageOf(S.sel);
  }
  function pageOf(m){return S.pages.filter(function(p){return p.marks.indexOf(m)>=0;})[0]||null;}

  /* ---------- opening ---------- */
  function open(f,o){
    build();
    if(S)close();
    o=o||{};
    var kind=o.kind==='pdf'||f.kind==='pdf'?'pdf':'image';
    S={f:f,o:o,kind:kind,bytes:null,doc:null,img:null,pages:[],tool:'pen',undo:[],penSeen:false,finger:lsGet(FINGER_KEY,'')==='1',
       zoom:0,ink:INKS[lsGet(INK_KEY,'blue')]?lsGet(INK_KEY,'blue'):'blue',style:lsGet(STYLE_KEY,'hand')==='type'?'type':'hand',
       tick:lsGet(TICK_KEY,'check')==='cross'?'cross':'check',size:SIZES[lsGet(SIZE_KEY,'medium')]?lsGet(SIZE_KEY,'medium'):'medium',
       sel:null,g:null,saving:false,state:'loading',sigs:{sig:loadSig('sig'),ini:loadSig('ini')},synced:false,gen:Math.random()};
    var me=S;
    S.syncing=syncSigs(me);
    warmSig();
    $('#sTitle').textContent='Sign '+(o.title||f.name);
    $('#sDrive').hidden=!o.driveCopy;
    $('#driveCopy').checked=!!(o.driveCopy&&o.driveCopy.on);
    $('#sDrive span').textContent=(o.driveCopy&&o.driveCopy.label)||'Also copy to Google Drive › Signed';
    clear($('#sPages'));
    $('#sAsk').hidden=true;$('#sigPad').hidden=true;
    UI.hidden=false;UI.dataset.marks='0';
    document.documentElement.classList.add('signing');
    setState('loading');msg('Opening '+f.name+'…');paintTools();counts();
    $('#sCancel').focus();
    fetch(S.o.url,{credentials:'same-origin'}).then(function(r){if(!r.ok)throw new Error('the hub answered '+r.status);return r.arrayBuffer();}).then(function(buf){
      if(S!==me)return;
      S.bytes=new Uint8Array(buf);
      return kind==='pdf'?openPdf(me):openImage(me);
    }).then(function(){
      if(S!==me)return;
      setState('ready');
      layout();
      setTool('pen');
    },function(e){
      if(S!==me)return;
      if(e&&e.locked){setState('locked');msg(LOCKED);}
      else{setState('error');msg('This file could not be opened for signing'+(e&&e.message?': '+e.message:'.'));}
      paintTools();counts();
    });
  }
  function openPdf(me){
    return Promise.all([loadPdfjs(),loadPdfLib()]).then(function(r){
      var m=r[0],lib=r[1];
      /* pdf-lib refuses an encrypted PDF (even one that opens without a password), so the check comes first */
      return lib.PDFDocument.load(me.bytes.slice(0),{updateMetadata:false}).then(null,function(e){
        if(/encrypt/i.test(String(e&&(e.name+' '+e.message))))throw {locked:true};
        throw e;
      }).then(function(){
        return m.getDocument({data:me.bytes.slice(0),isEvalSupported:false}).promise.then(null,function(e){
          if(e&&e.name==='PasswordException')throw {locked:true};
          throw e;
        });
      });
    }).then(function(doc){
      me.doc=doc;
      var chain=Promise.resolve();
      for(var i=1;i<=doc.numPages;i++)(function(i){chain=chain.then(function(){return doc.getPage(i).then(function(pg){
        var vp=pg.getViewport({scale:1});
        me.pages.push({i:i,w:vp.width,h:vp.height,pg:pg,view:pg.view.slice(),rotate:((pg.rotate%360)+360)%360,marks:[],el:null,base:null,ink:null,rendered:false,task:null});
      });});})(i);
      return chain;
    });
  }
  function openImage(me){
    return new Promise(function(res,rej){
      var url=URL.createObjectURL(new Blob([me.bytes],{type:me.f.type||'image/png'})),img=new Image();
      img.onload=function(){me.img=img;me.imgUrl=url;me.pages.push({i:1,w:img.naturalWidth,h:img.naturalHeight,marks:[],el:null,base:null,ink:null,rendered:false});res();};
      img.onerror=function(){URL.revokeObjectURL(url);rej(new Error('this browser cannot draw this picture'));};
      img.src=url;
    });
  }
  function close(){
    if(!S)return;
    S.pages.forEach(function(P){if(P.task)try{P.task.cancel();}catch(e){}});
    if(S.doc)try{S.doc.loadingTask.destroy();}catch(e){}   /* PDF.js 6: the document has no destroy() of its own */
    if(S.imgUrl)URL.revokeObjectURL(S.imgUrl);
    if(IO)IO.disconnect();
    S=null;
    UI.hidden=true;setState('closed');clear($('#sPages'));
    document.documentElement.classList.remove('signing');
  }
  function isOpen(){return !!S&&!!UI&&!UI.hidden;}

  /* ---------- laying the pages out, and drawing them ---------- */
  var IO=null;
  function cssWidth(P){
    var box=$('#sPages'),avail=Math.max(200,box.clientWidth-24);
    var fit=Math.min(avail,1000,P.w>P.h?1100:900);
    return Math.round(fit*ZOOMS[S.zoom]);
  }
  function scaleOf(P){return P.cw/P.w;}   /* CSS pixels per page unit */
  function layout(){
    var box=$('#sPages');clear(box);
    if(IO)IO.disconnect();
    IO=window.IntersectionObserver?new IntersectionObserver(seen,{root:box,rootMargin:'1200px 0px'}):null;
    S.pages.forEach(function(P,k){
      P.cw=cssWidth(P);P.ch=Math.round(P.cw*P.h/P.w);
      P.base=h('canvas',{class:'sbase'});P.ink=h('canvas',{class:'sink'});
      P.el=h('div',{class:'spage','data-i':String(P.i),'data-rendered':'0','aria-label':S.kind==='pdf'?'Page '+P.i:'The picture'},[P.base,P.ink]);
      P.el.style.width=P.cw+'px';P.el.style.height=P.ch+'px';
      P.rendered=false;
      box.appendChild(P.el);
      sizeInk(P);drawInk(P);renderMarks(P);
      if(k<3||!IO)renderPage(P);else IO.observe(P.el);
    });
    box.classList.toggle('fingerdraw',S.finger);
  }
  function seen(entries){
    entries.forEach(function(en){
      var P=S&&S.pages[+en.target.dataset.i-1];if(!P)return;
      if(en.isIntersecting){if(!P.rendered)renderPage(P);}
      else if(S.pages.length>8&&P.rendered&&P.i>3){P.base.width=P.base.height=0;P.rendered=false;P.el.dataset.rendered='0';}
    });
  }
  function dpr(){return Math.min(window.devicePixelRatio||1,3);}
  function sizeInk(P){
    var k=Math.min(dpr(),Math.sqrt(PAGE_PX/(P.cw*P.ch)));
    P.ink.width=Math.round(P.cw*k);P.ink.height=Math.round(P.ch*k);P.inkK=k;
  }
  function renderPage(P){
    var k=Math.min(dpr(),Math.sqrt(PAGE_PX/(P.cw*P.ch))),me=S;
    if(S.kind==='image'){
      P.base.width=Math.round(P.cw*k);P.base.height=Math.round(P.ch*k);
      P.base.getContext('2d').drawImage(S.img,0,0,P.base.width,P.base.height);
      P.rendered=true;P.el.dataset.rendered='1';
      return;
    }
    if(P.task)try{P.task.cancel();}catch(e){}
    var vp=P.pg.getViewport({scale:scaleOf(P)*k});
    P.base.width=Math.floor(vp.width);P.base.height=Math.floor(vp.height);
    P.task=P.pg.render({canvas:P.base,viewport:vp});
    P.task.promise.then(function(){if(S!==me)return;P.task=null;P.rendered=true;P.el.dataset.rendered='1';},function(){P.task=null;});
  }
  function inkCtx(P){
    var x=P.ink.getContext('2d'),k=scaleOf(P)*P.inkK;
    x.setTransform(k,0,0,k,0,0);
    return x;
  }
  function drawInk(P){
    var x=P.ink.getContext('2d');x.setTransform(1,0,0,1,0,0);x.clearRect(0,0,P.ink.width,P.ink.height);
    x=inkCtx(P);
    P.marks.forEach(function(m){if(m.t==='ink')strokeInk(x,m);});
  }
  function strokeInk(x,m,from){
    var p=m.pts;x.strokeStyle=m.color;x.fillStyle=m.color;x.lineCap='round';x.lineJoin='round';
    if(p.length===1){x.beginPath();x.arc(p[0][0],p[0][1],m.width*(0.5+p[0][2])/2,0,2*Math.PI);x.fill();return;}
    for(var i=Math.max(1,from||1);i<p.length;i++){
      x.lineWidth=m.width*(0.5+(p[i-1][2]+p[i][2])/2);
      x.beginPath();x.moveTo(p[i-1][0],p[i-1][1]);x.lineTo(p[i][0],p[i][1]);x.stroke();
    }
  }

  /* ---------- the signature and text pieces ---------- */
  function fontOf(m){return m.font==='hand'?HAND:FONT;}
  function textWidth(m){var c=textWidth.c||(textWidth.c=document.createElement('canvas').getContext('2d'));c.font=m.size+'px '+fontOf(m);return c.measureText(m.text).width;}
  /* a piece's size in page units: a picture its own, text its measured width and a 1.2 line, a tick its square */
  function markW(m){return m.t==='img'?m.w:m.t==='tick'?m.size:textWidth(m);}
  function markH(m){return m.t==='img'?m.h:m.t==='tick'?m.size:m.size*1.2;}
  function tickSvg(m){
    var s=document.createElementNS(SVGNS,'svg');s.setAttribute('viewBox','0 0 1 1');s.setAttribute('aria-hidden','true');
    var d=TICKS[m.kind].map(function(line){return line.map(function(p,i){return (i?'L':'M')+p[0]+' '+p[1];}).join('');}).join('');
    var p=document.createElementNS(SVGNS,'path');
    p.setAttribute('d',d);p.setAttribute('fill','none');p.setAttribute('stroke',m.color);p.setAttribute('stroke-width',String(TICK_W));
    p.setAttribute('stroke-linecap','round');p.setAttribute('stroke-linejoin','round');
    s.appendChild(p);
    return s;
  }
  function drawTick(x,m){
    x.save();x.strokeStyle=m.color;x.lineWidth=m.size*TICK_W;x.lineCap='round';x.lineJoin='round';
    TICKS[m.kind].forEach(function(line){
      x.beginPath();
      line.forEach(function(p,i){var px=m.x+p[0]*m.size,py=m.y+p[1]*m.size;if(i)x.lineTo(px,py);else x.moveTo(px,py);});
      x.stroke();
    });
    x.restore();
  }
  function renderMarks(P){
    Array.prototype.slice.call(P.el.querySelectorAll('.smark')).forEach(function(e){e.remove();});
    var s=scaleOf(P);
    P.marks.forEach(function(m,idx){
      if(m.t==='ink')return;
      var kids=[],cls=m.t==='img'?(m.who==='ini'?'ssig sini':'ssig'):m.t==='tick'?'stick':'stext';
      if(m.t==='img')kids.push(h('img',{src:m.src,alt:PICS[m.who==='ini'?'ini':'sig'].alt,draggable:'false'}));
      if(m.t==='tick')kids.push(tickSvg(m));
      var el=h('div',{class:'smark '+cls+(S.sel===m?' sel':''),'data-k':String(idx)},kids);
      if(m.t==='tick')el.dataset.kind=m.kind;
      if(m.t==='text'){el.textContent=m.text;el.style.fontSize=(m.size*s)+'px';el.style.color=m.color;el.style.fontFamily=fontOf(m);}
      el.appendChild(h('span',{class:'shandle','aria-hidden':'true'}));
      el.appendChild(h('button',{class:'sdel',type:'button','aria-label':'Remove this'}));   /* its × is drawn by CSS, so a text piece's text stays just the text */
      el.style.left=(m.x*s)+'px';el.style.top=(m.y*s)+'px';el.style.width=(markW(m)*s)+'px';el.style.height=(markH(m)*s)+'px';
      P.el.appendChild(el);
    });
  }

  /* ---------- the signature and the initials: kept by the store for every device, with a copy on this one ---------- */
  function loadSig(which){
    var k=PICS[which].key,src=lsGet(k,''),a=+lsGet(k+'.aspect','0');
    return /^data:image\/png;base64,/.test(src)&&a>0?{src:src,aspect:a}:null;
  }
  function keepSig(which,sig){var k=PICS[which].key;return lsSet(k,sig?sig.src:null)&&lsSet(k+'.aspect',sig?String(sig.aspect):null);}
  var HUB_STORE={
    get:function(){return hubApi('GET','/api/signature');},
    put:function(name,png,aspect){return hubApi('POST','/api/signature',{which:name,png:png,aspect:aspect});}};
  function store(){return CFG.store||HUB_STORE;}
  function pushSig(which,sig){
    var k=PICS[which].key+'.pending';
    lsSet(k,'1');   /* until the store has it */
    return Promise.resolve().then(function(){return store().put(PICS[which].name,sig.src,sig.aspect);})
      .then(function(){lsSet(k,null);return true;},function(){return false;});
  }
  /* What the store has wins, except over one drawn here that has not reached it yet (pending), or when the store has
     none at all: then this device's goes up, so a signature drawn before they were shared is not lost. */
  function syncSigs(me){
    return Promise.resolve().then(function(){return store().get();}).then(function(j){
      ['sig','ini'].forEach(function(which){
        var hub=j&&j[PICS[which].name],mine=loadSig(which),pending=lsGet(PICS[which].key+'.pending','')==='1';
        if(mine&&(pending||!hub||!hub.png)){pushSig(which,mine);return;}
        if(hub&&hub.png&&+hub.aspect>0&&(!mine||mine.src!==hub.png)){
          var s={src:hub.png,aspect:+hub.aspect};
          keepSig(which,s);
          if(S===me)S.sigs[which]=s;
        }
      });
    },function(){}).then(function(){
      if(S!==me)return;
      S.synced=true;warmSig();paintTools();
    });
  }
  var SIG_IMG={};
  function sigImage(src){
    if(SIG_IMG[src])return Promise.resolve(SIG_IMG[src]);
    return new Promise(function(res,rej){var i=new Image();i.onload=function(){SIG_IMG[src]=i;res(i);};i.onerror=rej;i.src=src;});
  }
  /* the signature in an ink: every pixel takes the colour and keeps its own see-through-ness, so the soft edges stay */
  var TINT={};
  function tint(src,color){
    var key=color+' '+src;
    if(TINT[key])return Promise.resolve(TINT[key]);
    return sigImage(src).then(function(i){
      var c=document.createElement('canvas');c.width=i.naturalWidth;c.height=i.naturalHeight;
      var x=c.getContext('2d');x.drawImage(i,0,0);
      x.globalCompositeOperation='source-in';x.fillStyle=color;x.fillRect(0,0,c.width,c.height);
      return (TINT[key]=c.toDataURL('image/png'));
    });
  }
  function warmSig(){   /* so a tap places it at once */
    if(!S)return;
    ['sig','ini'].forEach(function(w){if(S.sigs[w])tint(S.sigs[w].src,INKS[S.ink]).then(null,function(){});});
  }
  function recolor(m,color){
    if(m.t==='ink'||m.color===color)return;
    var me=S;
    (m.t==='img'?tint(m.base||m.src,color):Promise.resolve(null)).then(function(src){
      var P=S===me&&pageOf(m);if(!P)return;
      snap();m.color=color;if(src)m.src=src;renderMarks(P);
    },function(){});
  }

  /* ---------- tools ---------- */
  function paintTools(){
    var ready=S&&S.state==='ready';
    Array.prototype.forEach.call(UI.querySelectorAll('#sTools [data-tool]'),function(b){b.setAttribute('aria-pressed',S&&S.tool===b.dataset.tool?'true':'false');b.disabled=!ready;});
    $('#sZoomIn').disabled=!ready||S.zoom>=ZOOMS.length-1;$('#sZoomOut').disabled=!ready||S.zoom<=0;
    $('#sPenOpts').hidden=!ready;   /* the ink is for every tool; finger drawing only for the pen */
    $('#sFinger').hidden=!ready||S.tool!=='pen';
    $('#sTextOpts').hidden=!ready||S.tool!=='text';
    $('#sTickOpts').hidden=!ready||S.tool!=='tick';
    var pic=S&&PICS[S.tool]?S.tool:'';
    $('#sSigOpts').hidden=!ready||!pic||!S.sigs[pic];
    $('#sigRedo').textContent=pic==='ini'?'Draw new initials':'Draw a new signature';
    Array.prototype.forEach.call(UI.querySelectorAll('[data-ink]'),function(b){b.setAttribute('aria-pressed',S&&S.ink===b.dataset.ink?'true':'false');});
    Array.prototype.forEach.call(UI.querySelectorAll('[data-style]'),function(b){b.setAttribute('aria-pressed',S&&S.style===b.dataset.style?'true':'false');});
    Array.prototype.forEach.call(UI.querySelectorAll('[data-tick]'),function(b){b.setAttribute('aria-pressed',S&&S.tick===b.dataset.tick?'true':'false');});
    Array.prototype.forEach.call(UI.querySelectorAll('[data-size]'),function(b){b.setAttribute('aria-pressed',S&&S.size===b.dataset.size?'true':'false');});
    $('#sText').classList.toggle('hand',!!(S&&S.style==='hand'));
    $('#fingerDraw').checked=!!(S&&S.finger);
    $('#sPages').classList.toggle('fingerdraw',!!(S&&S.finger));
  }
  function setTool(t){
    S.tool=t;
    var asking=false;
    if(PICS[t]&&!S.sigs[t]){
      /* none on this device: the store may have one, so ask it first (it answers long before a page is drawn) */
      var me=S;
      if(!S.synced){
        asking=true;
        S.syncing.then(function(){if(S===me&&S.tool===t){if(!S.sigs[t])openPad(t);paintTools();msg(hint());}});
      }else openPad(t);
    }
    if(t==='text'){
      var inp=$('#sText');if(!inp.value||inp.dataset.date==='1'){inp.value=today();inp.dataset.date='1';}
      paintChips();
      if(S.style==='hand')loadHand();
    }
    paintTools();
    msg(asking?'Getting your '+PICS[t].name+' from '+CFG.keeper+'…':hint());
  }
  function hint(){
    if(S.tool==='sig')return S.sigs.sig?'Tap where your signature goes. Drag it to move it; the round corner makes it bigger or smaller.':'Draw your signature first.';
    if(S.tool==='ini')return S.sigs.ini?'Tap where your initials go. Every page then puts them in the same place on every page.':'Write your initials first.';
    if(S.tool==='text')return 'Tap where the text goes.';
    if(S.tool==='tick')return 'Tap the middle of a box to put a '+(S.tick==='cross'?'cross':'tick')+' in it. Tap as many boxes as you need.';
    if(S.penSeen)return 'Write with the Pencil. A finger scrolls the page.';
    if(window.matchMedia&&matchMedia('(pointer:coarse)').matches)return S.finger?'Draw with a finger. Turn Draw with finger off to scroll with it again.':'Write with the Pencil. To write with a finger, turn on Draw with finger.';
    return 'Draw with the mouse or trackpad.';
  }
  function paintChips(){
    var c=$('#sChips');clear(c);
    [today(),todayWords()].concat(recentTexts().filter(function(t){return !isToday(t);})).forEach(function(t){
      c.appendChild(h('button',{class:'btn sm quiet',type:'button','data-chip':t,text:t}));
    });
  }
  function setFinger(on,quiet){
    S.finger=on;lsSet(FINGER_KEY,on?'1':null);paintTools();
    if(!quiet)msg(hint());
  }

  /* ---------- undo: a copy of every page's marks before each change ---------- */
  function snap(){
    S.undo.push(S.pages.map(function(P){return P.marks.map(function(m){return Object.assign({},m);});}));
    if(S.undo.length>60)S.undo.shift();
  }
  function undo(){
    var last=S.undo.pop();if(!last)return;
    S.pages.forEach(function(P,i){P.marks=last[i];drawInk(P);});
    S.sel=null;
    S.pages.forEach(renderMarks);
    counts();
  }

  /* ---------- the pen, the pieces, and taps ---------- */
  function pagePoint(P,e){
    var r=P.el.getBoundingClientRect(),s=scaleOf(P);
    return [(e.clientX-r.left)/s,(e.clientY-r.top)/s];
  }
  function canDraw(e){
    if(e.pointerType==='pen')return true;
    if(e.pointerType==='mouse')return e.button===0;
    return S.finger;   /* a finger (or a palm) only when asked */
  }
  function down(e){
    if(!S||S.state!=='ready'||S.g||!$('#sAsk').hidden||!$('#sigPad').hidden)return;
    var pageEl=e.target.closest('.spage');if(!pageEl)return;
    var P=S.pages[+pageEl.dataset.i-1];if(!P)return;
    if(e.pointerType==='pen'&&!S.penSeen){S.penSeen=true;if(S.finger)setFinger(false,true);msg(hint());}
    var palm=e.pointerType==='touch'&&!S.finger&&S.penSeen;
    var markEl=e.target.closest('.smark');
    if(markEl){
      if(palm)return;
      var m=P.marks[+markEl.dataset.k];if(!m)return;
      e.preventDefault();
      if(e.target.closest('.sdel')){snap();P.marks.splice(P.marks.indexOf(m),1);S.sel=null;renderMarks(P);counts();return;}
      S.sel=m;
      Array.prototype.forEach.call(UI.querySelectorAll('.smark.sel'),function(x){x.classList.remove('sel');});markEl.classList.add('sel');
      paintSel();
      S.g={kind:e.target.closest('.shandle')?'resize':'move',P:P,m:m,el:markEl,id:e.pointerId,start:pagePoint(P,e),orig:Object.assign({},m),moved:false};
      capture(e);
      return;
    }
    if(S.tool==='pen'){
      if(!canDraw(e))return;   /* a finger with finger drawing off: let the page scroll */
      e.preventDefault();
      deselect();
      var p=pagePoint(P,e);
      var base=S.kind==='pdf'?SIZES[S.size].pen:Math.max(2.5,Math.min(P.w,P.h)/150)*SIZES[S.size].pen/1.6;   /* a photo: in step with its size */
      S.g={kind:'ink',P:P,id:e.pointerId,m:{t:'ink',color:INKS[S.ink],width:base,pts:[[p[0],p[1],pressure(e)]]}};
      capture(e);
      var x=inkCtx(P);strokeInk(x,S.g.m);
      return;
    }
    if(palm)return;
    /* the signature or text tool: a tap places it; a finger that moves is scrolling, not tapping */
    S.g={kind:'tap',P:P,id:e.pointerId,x:e.clientX,y:e.clientY,type:e.pointerType};
    if(e.pointerType!=='touch'){e.preventDefault();capture(e);}
  }
  function pressure(e){return e.pointerType==='pen'&&e.pressure>0?Math.min(1,e.pressure):0.5;}
  function capture(e){try{e.target.setPointerCapture(e.pointerId);}catch(err){}}
  function move(e){
    var g=S&&S.g;if(!g||e.pointerId!==g.id)return;
    if(g.kind==='ink'){
      e.preventDefault();
      var evs=e.getCoalescedEvents?e.getCoalescedEvents():[];if(!evs.length)evs=[e];
      var from=g.m.pts.length;
      evs.forEach(function(ev){var p=pagePoint(g.P,ev);g.m.pts.push([p[0],p[1],pressure(ev)]);});
      strokeInk(inkCtx(g.P),g.m,from);
    }else if(g.kind==='move'||g.kind==='resize'){
      e.preventDefault();
      var q=pagePoint(g.P,e),dx=q[0]-g.start[0],dy=q[1]-g.start[1];
      if(!g.moved&&Math.abs(dx)+Math.abs(dy)<0.5)return;
      if(!g.moved){g.moved=true;snap();}
      var m=g.m,o=g.orig;
      if(g.kind==='move'){m.x=o.x+dx;m.y=o.y+dy;}
      else if(m.t==='img'){var w=Math.max(20,o.w+dx);m.w=w;m.h=w/(o.w/o.h);}
      else if(m.t==='tick'){m.size=Math.max(4,o.size+Math.max(dx,dy));}
      else{var ow=textWidth(o);m.size=Math.max(4,o.size*Math.max(0.2,(ow+dx)/ow));}
      placeEl(g.P,m,g.el);
    }else if(g.kind==='tap'){
      if(Math.abs(e.clientX-g.x)+Math.abs(e.clientY-g.y)>12)S.g=null;
    }
  }
  function placeEl(P,m,el){
    var s=scaleOf(P);
    el.style.left=(m.x*s)+'px';el.style.top=(m.y*s)+'px';el.style.width=(markW(m)*s)+'px';el.style.height=(markH(m)*s)+'px';
    if(m.t==='text')el.style.fontSize=(m.size*s)+'px';
  }
  function up(e){
    var g=S&&S.g;if(!g||e.pointerId!==g.id)return;
    S.g=null;
    if(g.kind==='ink'){
      e.preventDefault();
      snap();g.P.marks.push(g.m);drawInk(g.P);counts();
    }else if(g.kind==='move'||g.kind==='resize'){
      e.preventDefault();counts();
    }else if(g.kind==='tap'){
      var p=pagePoint(g.P,e);
      if(PICS[S.tool]&&S.sigs[S.tool])placeSig(g.P,p,S.tool);
      else if(S.tool==='text')placeText(g.P,p);
      else if(S.tool==='tick')placeTick(g.P,p);
    }
  }
  function cancelG(e){
    var g=S&&S.g;if(!g||e.pointerId!==g.id)return;
    S.g=null;
    if(g.kind==='ink')drawInk(g.P);   /* the system took the touch (a scroll began): the stroke is dropped */
    else if((g.kind==='move'||g.kind==='resize')&&g.moved){Object.assign(g.m,g.orig);S.undo.pop();renderMarks(g.P);}
  }
  function deselect(){if(S.sel){S.sel=null;Array.prototype.forEach.call(UI.querySelectorAll('.smark.sel'),function(x){x.classList.remove('sel');});paintSel();}}
  function placeSig(P,p,which){
    var me=S,sig=S.sigs[which],color=INKS[S.ink];
    tint(sig.src,color).then(function(src){
      if(S!==me)return;
      var w=Math.min(P.w,P.h)*PICS[which].width,hh=w/sig.aspect;
      var m={t:'img',who:which,src:src,base:sig.src,color:color,x:p[0]-w/2,y:p[1]-hh/2,w:w,h:hh};
      snap();P.marks.push(m);S.sel=m;renderMarks(P);counts();
      setTool('pen');
      msg('Placed. Drag to move, the round corner resizes, × removes.'+(S.kind==='pdf'&&S.pages.length>1?' Every page puts '+(which==='ini'?'them':'it')+' on every page.':''));
    },function(){if(S===me)msg('Your '+PICS[which].name+' could not be drawn. Choose '+(which==='ini'?'Draw new initials':'Draw a new signature')+'.');});
  }
  /* a tick or cross, centred where the box was tapped and sized for a form's box (10 pt on a PDF). The tool stays in
     hand, because a form has boxes to tick in a row, and the new one is not selected: choosing Cross for the next box
     must not turn the last tick into a cross. Tapping a placed one selects it, to move, remove or change it. */
  function placeTick(P,p){
    var size=S.kind==='pdf'?10:Math.max(18,Math.round(Math.min(P.w,P.h)/40));
    var m={t:'tick',kind:S.tick,size:size,color:INKS[S.ink],x:p[0]-size/2,y:p[1]-size/2};
    snap();P.marks.push(m);deselect();renderMarks(P);counts();
    msg((S.tick==='cross'?'Crossed':'Ticked')+'. Tap the next box, or choose Pen when you are done. Tap one to move or remove it.');
  }
  /* Every page: copies of the selected piece on every other page, at the same place as a share of the page's width
     and height (so a page of another size, or turned, gets it in the matching spot). One Undo takes them all away. */
  function everyPage(){
    var m=S.sel,P=m&&pageOf(m);
    if(!P||m.t==='ink'||S.pages.length<2)return;
    var fx=m.x/P.w,fy=m.y/P.h,n=0,snapped=false;
    S.pages.forEach(function(Q){
      if(Q===P)return;
      var c=Object.assign({},m),w=markW(c),hh=markH(c);
      c.x=Math.max(0,Math.min(Q.w-w,fx*Q.w));c.y=Math.max(0,Math.min(Q.h-hh,fy*Q.h));
      var there=Q.marks.some(function(o){return o.t===c.t&&Math.abs(o.x-c.x)<1&&Math.abs(o.y-c.y)<1&&o.src===c.src&&o.text===c.text&&o.kind===c.kind;});
      if(there)return;   /* pressed twice: once is enough */
      if(!snapped){snap();snapped=true;}
      Q.marks.push(c);renderMarks(Q);n++;
    });
    counts();
    msg(n?'Added to '+n+' more page'+(n===1?'':'s')+'. Undo takes them all away again.':'It is on every page already.');
  }
  function placeText(P,p){
    var t=($('#sText').value||'').trim();
    if(!t){msg('Type the text first, then tap where it goes.');$('#sText').focus();return;}
    var me=S,style=S.style,color=INKS[S.ink];
    (style==='hand'?loadHand():Promise.resolve()).then(function(){   /* measured in the font it is drawn in */
      if(S!==me)return;
      var size=(S.kind==='pdf'?12:Math.max(16,Math.round(Math.min(P.w,P.h)/30)))*(style==='hand'?HAND_SIZE:1);
      var m={t:'text',text:t,size:size,color:color,font:style,x:p[0],y:p[1]-size*0.6};
      snap();P.marks.push(m);S.sel=m;renderMarks(P);counts();
      if(!isToday(t)){var r=recentTexts().filter(function(x){return x!==t;});r.unshift(t);lsSet(TEXTS_KEY,JSON.stringify(r.slice(0,4)));}
      setTool('pen');
      msg('Placed. Drag it to move it, or use the round corner to make it bigger.');
    });
  }

  /* ---------- the signature pad ---------- */
  var PAD={strokes:[],g:null,photo:null,job:null,which:'sig'};   /* photo: a signature taken from a photo, waiting for Use */
  function openPad(which){
    PAD.which=which==='ini'?'ini':'sig';
    $('#sigTitle').textContent=PICS[PAD.which].title;
    $('#sigText').textContent=PICS[PAD.which].text();
    $('#sigPad').hidden=false;
    var c=$('#sigCanvas'),r=c.getBoundingClientRect(),k=Math.min(window.devicePixelRatio||1,3);
    c.width=Math.round(r.width*k);c.height=Math.round(r.height*k);c.dataset.k=String(k);
    PAD.strokes=[];PAD.photo=null;PAD.job=null;padMsg('');padDraw();$('#sigUse').disabled=true;
    $('#sigCancel').focus();
  }
  function closePad(){PAD.job=null;$('#sigPad').hidden=true;if(S&&PICS[S.tool]&&!S.sigs[S.tool])setTool('pen');}
  function padMsg(t){$('#sigMsg').textContent=t||'';}
  function padInk(){return INKS[S?S.ink:'blue'];}
  function padPoint(e){var c=$('#sigCanvas'),r=c.getBoundingClientRect();return [e.clientX-r.left,e.clientY-r.top];}
  function padDraw(){
    var c=$('#sigCanvas'),k=+c.dataset.k||1,x=c.getContext('2d'),ph=PAD.photo;
    x.setTransform(1,0,0,1,0,0);x.clearRect(0,0,c.width,c.height);
    if(ph){   /* the photo's signature, fitted into the box */
      tint(ph.src,padInk()).then(sigImage).then(function(i){
        if(PAD.photo!==ph)return;
        var s=Math.min(c.width*0.92/i.naturalWidth,c.height*0.86/i.naturalHeight),w=i.naturalWidth*s,hh=i.naturalHeight*s;
        x.setTransform(1,0,0,1,0,0);x.clearRect(0,0,c.width,c.height);x.drawImage(i,(c.width-w)/2,(c.height-hh)/2,w,hh);
      },function(){});
      return;
    }
    x.setTransform(k,0,0,k,0,0);
    PAD.strokes.forEach(function(st){strokeInk(x,{color:padInk(),width:padWidth(),pts:st});});
  }
  function padWidth(){return 2*SIZES[S?S.size:'medium'].sig;}   /* in the box: 3 px for Medium, as it always was */
  function useSig(){
    var sig=PAD.photo;
    if(!sig){
      var all=[].concat.apply([],PAD.strokes);if(!all.length)return;
      var x0=Infinity,y0=Infinity,x1=-Infinity,y1=-Infinity;
      all.forEach(function(p){x0=Math.min(x0,p[0]);y0=Math.min(y0,p[1]);x1=Math.max(x1,p[0]);y1=Math.max(y1,p[1]);});
      /* the line is made as thick, for the signature's width, as the chosen thickness is for its usual placed width,
         so a signature written small in the box is not placed with a thick line (it is scaled up, line and all) */
      var lw=SIZES[S.size].sig*Math.max(40,x1-x0)/(PICS[PAD.which].width*A4_SHORT);
      var pad=Math.max(6,lw),K=3,w=Math.max(20,x1-x0+2*pad),hh=Math.max(20,y1-y0+2*pad);
      var c=document.createElement('canvas');c.width=Math.round(w*K);c.height=Math.round(hh*K);
      var x=c.getContext('2d');x.setTransform(K,0,0,K,(pad-x0)*K,(pad-y0)*K);
      PAD.strokes.forEach(function(st){strokeInk(x,{color:INKS.black,width:lw,pts:st});});   /* kept colourless; tinted when placed */
      sig={src:c.toDataURL('image/png'),aspect:w/hh};
    }
    var which=PAD.which,me=S;
    S.sigs[which]=sig;
    var kept=keepSig(which,sig);
    PAD.photo=null;
    $('#sigPad').hidden=true;
    warmSig();
    setTool(which);
    pushSig(which,sig).then(function(ok){
      if(!ok&&S===me)msg('It could not be saved to '+CFG.keeper+' just now, so only this device has the new '+PICS[which].name+' for now. Your other devices get it next time the signer opens here.');
    });
    if(!kept)msg('This device has no room to remember your '+PICS[which].name+', so '+CFG.keeper+' keeps it for every device. Tap where it goes.');
  }

  /* ---------- a signature from a photo of it on paper ----------
     The paper's brightness is measured near every point (the brightest pixel in each small block, widened to the
     blocks around it so a block that is all ink still finds paper, then blended between blocks), so a shadow across
     the page is still paper. A pixel well darker than the paper around it is ink. Specks of dust are dropped, the
     rest is cut out with a small margin and kept dark grey, like a drawn signature. */
  function photoSig(file){
    var unreadable='This browser cannot read this photo. Take it again as a JPEG (on an iPhone: Settings › Camera › Formats › Most Compatible), or use a PNG.';
    return new Promise(function(res,rej){
      var url=URL.createObjectURL(file),img=new Image();
      img.onload=function(){URL.revokeObjectURL(url);if(img.naturalWidth&&img.naturalHeight)res(img);else rej(new Error(unreadable));};
      img.onerror=function(){URL.revokeObjectURL(url);rej(new Error(unreadable));};
      img.src=url;
    }).then(function(img){
      var k=Math.min(1,PHOTO_PX/Math.max(img.naturalWidth,img.naturalHeight));
      var W=Math.max(1,Math.round(img.naturalWidth*k)),H=Math.max(1,Math.round(img.naturalHeight*k)),n=W*H,i,x,y;
      var c=document.createElement('canvas');c.width=W;c.height=H;
      var cx=c.getContext('2d',{willReadFrequently:true});
      cx.fillStyle='#FFFFFF';cx.fillRect(0,0,W,H);cx.drawImage(img,0,0,W,H);   /* a see-through picture: its holes are paper */
      var px=cx.getImageData(0,0,W,H).data,L=new Float32Array(n);
      for(i=0;i<n;i++)L[i]=0.299*px[4*i]+0.587*px[4*i+1]+0.114*px[4*i+2];
      /* the paper near each point */
      var B=Math.max(8,Math.round(Math.max(W,H)/48)),bw=Math.ceil(W/B),bh=Math.ceil(H/B),top=new Float32Array(bw*bh),wide=new Float32Array(bw*bh);
      for(y=0;y<H;y++)for(x=0;x<W;x++){var b=((y/B)|0)*bw+((x/B)|0);if(L[y*W+x]>top[b])top[b]=L[y*W+x];}
      for(y=0;y<bh;y++)for(x=0;x<bw;x++){
        var hi=0;
        for(var dy=-1;dy<=1;dy++)for(var dx=-1;dx<=1;dx++){var X=x+dx,Y=y+dy;if(X>=0&&Y>=0&&X<bw&&Y<bh&&top[Y*bw+X]>hi)hi=top[Y*bw+X];}
        wide[y*bw+x]=hi;
      }
      /* how much ink each pixel holds: none below LO of the paper's brightness darker, all of it beyond HI */
      var LO=0.14,HI=0.34,A=new Uint8Array(n);
      for(y=0;y<H;y++){
        var fy=Math.min(bh-1,Math.max(0,(y+0.5)/B-0.5)),y0=fy|0,y1=Math.min(bh-1,y0+1),ty=fy-y0;
        for(x=0;x<W;x++){
          var fx=Math.min(bw-1,Math.max(0,(x+0.5)/B-0.5)),x0=fx|0,x1=Math.min(bw-1,x0+1),tx=fx-x0;
          var paper=(wide[y0*bw+x0]*(1-tx)+wide[y0*bw+x1]*tx)*(1-ty)+(wide[y1*bw+x0]*(1-tx)+wide[y1*bw+x1]*tx)*ty;
          var d=paper>8?(paper-L[y*W+x])/paper:0,a=(d-LO)/(HI-LO);
          A[y*W+x]=a<=0?0:a>=1?255:Math.round(a*255);
        }
      }
      /* the ink's pieces (pixels at least half ink, touching on a side or a corner); small ones are dust */
      var lab=new Int32Array(n),stack=new Int32Array(n),areas=[0],total=0,SPECK=Math.max(16,Math.round(n/100000));
      for(i=0;i<n;i++){
        if(A[i]<128||lab[i])continue;
        var id=areas.length,sp=0,area=0;stack[sp++]=i;lab[i]=id;
        while(sp){
          var q=stack[--sp],qx=q%W,qy=(q-qx)/W;area++;
          for(var ny=Math.max(0,qy-1);ny<=Math.min(H-1,qy+1);ny++)for(var nx=Math.max(0,qx-1);nx<=Math.min(W-1,qx+1);nx++){
            var r=ny*W+nx;if(A[r]>=128&&!lab[r]){lab[r]=id;stack[sp++]=r;}
          }
        }
        areas.push(area);total+=area;
      }
      if(total>n*0.25)throw new Error('Most of this photo looks like ink. Photograph just the signature, on plain white paper, in good light.');
      /* what is kept: the pieces that are not dust, and the soft edge within 2 pixels of them */
      var keep=new Uint8Array(n),bx0=W,by0=H,bx1=-1,by1=-1;
      for(i=0;i<n;i++)if(lab[i]&&areas[lab[i]]>=SPECK){
        keep[i]=1;x=i%W;y=(i-x)/W;
        if(x<bx0)bx0=x;if(x>bx1)bx1=x;if(y<by0)by0=y;if(y>by1)by1=y;
      }
      if(bx1<0)throw new Error('No signature was found in this photo. Sign on plain white paper and take the photo close up, in good light.');
      var near=grow(grow(keep,W,H),W,H);
      /* cut it out with a margin, at most SIG_PX on its long side */
      var m=Math.max(4,Math.round(0.02*Math.max(bx1-bx0,by1-by0)));
      bx0=Math.max(0,bx0-m);by0=Math.max(0,by0-m);bx1=Math.min(W-1,bx1+m);by1=Math.min(H-1,by1+m);
      var cw=bx1-bx0+1,ch=by1-by0+1,cut=document.createElement('canvas');cut.width=cw;cut.height=ch;
      var cc=cut.getContext('2d'),out=cc.createImageData(cw,ch),o=out.data;
      for(y=0;y<ch;y++)for(x=0;x<cw;x++){
        var s=(by0+y)*W+bx0+x,t=4*(y*cw+x);
        o[t]=17;o[t+1]=24;o[t+2]=39;o[t+3]=near[s]?A[s]:0;
      }
      cc.putImageData(out,0,0);
      var z=Math.min(1,SIG_PX/Math.max(cw,ch)),fin=cut;
      if(z<1){fin=document.createElement('canvas');fin.width=Math.max(1,Math.round(cw*z));fin.height=Math.max(1,Math.round(ch*z));fin.getContext('2d').drawImage(cut,0,0,fin.width,fin.height);}
      return {src:fin.toDataURL('image/png'),aspect:cw/ch};
    });
  }
  function grow(a,W,H){   /* each kept pixel also keeps the 8 around it */
    var b=new Uint8Array(a.length),x,y;
    for(y=0;y<H;y++)for(x=0;x<W;x++){
      if(!a[y*W+x])continue;
      for(var ny=Math.max(0,y-1);ny<=Math.min(H-1,y+1);ny++)for(var nx=Math.max(0,x-1);nx<=Math.min(W-1,x+1);nx++)b[ny*W+nx]=1;
    }
    return b;
  }
  function usePhoto(file){
    var job=PAD.job={};
    $('#sigUse').disabled=true;padMsg('Finding the signature in the photo…');
    photoSig(file).then(function(sig){
      if(PAD.job!==job||$('#sigPad').hidden)return;
      PAD.strokes=[];PAD.photo=sig;padDraw();
      padMsg('Found it. If it looks right, choose Use this signature; if not, try another photo.');
      $('#sigUse').disabled=false;
    },function(e){
      if(PAD.job!==job)return;
      padMsg(e&&e.message?e.message:'This photo could not be used.');
      $('#sigUse').disabled=!PAD.strokes.length&&!PAD.photo;
    });
  }

  /* ---------- saving ---------- */
  function canvasBytes(c,type,q){return new Promise(function(res,rej){c.toBlob(function(b){if(!b){rej(new Error('the picture could not be made'));return;}b.arrayBuffer().then(function(a){res({bytes:new Uint8Array(a),type:b.type});},rej);},type,q);});}
  function paintMarks(x,P){
    var imgs=P.marks.filter(function(m){return m.t==='img';}).map(function(m){return sigImage(m.src);});
    var hand=P.marks.some(function(m){return m.t==='text'&&m.font==='hand';})?loadHand():null;
    return Promise.all([hand].concat(imgs)).then(function(loaded){
      var k=1;
      P.marks.forEach(function(m){
        if(m.t==='ink')strokeInk(x,m);
        else if(m.t==='img'){x.drawImage(loaded[k++],m.x,m.y,m.w,m.h);}
        else if(m.t==='tick')drawTick(x,m);
        else{
          x.fillStyle=m.color;x.font=m.size+'px '+fontOf(m);
          var mt=x.measureText(m.text),a=mt.fontBoundingBoxAscent,d=mt.fontBoundingBoxDescent;
          /* the baseline where the page put it: a line 1.2 high, the font's own height centred in it */
          if(a>0&&d>=0){x.textBaseline='alphabetic';x.fillText(m.text,m.x,m.y+(m.size*1.2-(a+d))/2+a);}
          else{x.textBaseline='top';x.fillText(m.text,m.x,m.y+m.size*0.1);}
        }
      });
    });
  }
  function signedName(name,ext){
    var stem=name.replace(/\.[^.]+$/,''),m=/^(.*) \(signed(?: (\d+))?\)$/.exec(stem),n=1;
    if(m){stem=m[1];n=(m[2]?+m[2]:1)+1;}
    var taken={};(S.o.existing||[]).forEach(function(x){taken[String(x).toLowerCase()]=1;});
    for(;;n++){var cand=stem+(n===1?' (signed)':' (signed '+n+')')+ext;if(!taken[cand.toLowerCase()])return cand;}
  }
  function buildPdf(){
    return loadPdfLib().then(function(lib){
      return lib.PDFDocument.load(S.bytes.slice(0),{updateMetadata:false}).then(function(doc){
        var pages=doc.getPages(),chain=Promise.resolve();
        S.pages.forEach(function(P,i){
          if(!P.marks.length)return;
          chain=chain.then(function(){
            /* the page as PDF.js showed it: its crop box, turned by its rotation; the overlay is drawn upright
               in the page's own (unturned) space, so the turn maps each mark back */
            var v=P.view,W=v[2]-v[0],H=v[3]-v[1],k=Math.min(4,3200/Math.max(W,H));
            var c=document.createElement('canvas');c.width=Math.ceil(W*k);c.height=Math.ceil(H*k);
            var x=c.getContext('2d'),r=P.rotate;
            if(r===90)x.setTransform(0,-k,k,0,0,k*H);
            else if(r===180)x.setTransform(-k,0,0,-k,k*W,k*H);
            else if(r===270)x.setTransform(0,k,-k,0,k*W,0);
            else x.setTransform(k,0,0,k,0,0);
            return paintMarks(x,P).then(function(){return canvasBytes(c,'image/png');}).then(function(png){
              return doc.embedPng(png.bytes);
            }).then(function(img){
              pages[i].drawImage(img,{x:v[0],y:v[1],width:W,height:H});
            });
          });
        });
        return chain.then(function(){return doc.save();}).then(function(bytes){
          return new File([bytes],signedName(S.f.name,'.pdf'),{type:'application/pdf'});
        });
      });
    });
  }
  function buildImage(){
    var P=S.pages[0],w=P.w,hh=P.h,k=1,note='';
    if(w*hh>MAX_PX){k=Math.sqrt(MAX_PX/(w*hh));note='Saved at '+Math.round(w*k)+' × '+Math.round(hh*k)+', the largest this browser can make.';}
    var c=document.createElement('canvas');c.width=Math.round(w*k);c.height=Math.round(hh*k);
    var x=c.getContext('2d');x.drawImage(S.img,0,0,c.width,c.height);
    x.setTransform(c.width/w,0,0,c.height/hh,0,0);
    var t=(S.f.type||'').toLowerCase(),want=/jpe?g/.test(t)?'image/jpeg':t==='image/webp'?'image/webp':'image/png';
    return paintMarks(x,P).then(function(){return canvasBytes(c,want,0.92);}).then(function(out){
      var ext=out.type==='image/jpeg'?(/\.jpeg$/i.test(S.f.name)?'.jpeg':'.jpg'):out.type==='image/webp'?'.webp':'.png';
      return {file:new File([out.bytes],signedName(S.f.name,ext),{type:out.type}),note:note};
    });
  }
  function save(){
    if(!S||S.saving||!total())return;
    S.saving=true;counts();msg('Saving the signed copy…');
    var me=S;
    (S.kind==='pdf'?buildPdf().then(function(f){return {file:f,note:''};}):buildImage()).then(function(r){
      if(S!==me)return;
      var cb=me.o.onSave,drive=!!me.o.driveCopy&&$('#driveCopy').checked;close();
      if(cb)cb(r.file,r.note,{driveCopy:drive});
    },function(e){
      if(S!==me)return;
      S.saving=false;counts();msg('The signed copy could not be made'+(e&&e.message?': '+e.message:'.'));
    });
  }

  /* ---------- Cancel ---------- */
  function cancel(){
    if(!S)return;
    if(!total()||S.state!=='ready'){close();return;}
    $('#sAsk').hidden=false;$('#sAskKeep').focus();
  }

  /* ---------- wiring ---------- */
  function wire(){
    var pages=$('#sPages');
    pages.addEventListener('pointerdown',down);
    window.addEventListener('pointermove',move,{passive:false});
    window.addEventListener('pointerup',up);
    window.addEventListener('pointercancel',cancelG);
    /* iOS: a Pencil touch must never scroll the page, or the stroke is cancelled half-way; a finger still scrolls */
    function stylus(e){
      if(!S||!e.target.closest('.spage'))return;
      var t=e.changedTouches||[];
      for(var i=0;i<t.length;i++)if(t[i].touchType==='stylus'){e.preventDefault();return;}
    }
    pages.addEventListener('touchstart',stylus,{passive:false});
    pages.addEventListener('touchmove',stylus,{passive:false});
    UI.querySelector('#sTools').addEventListener('click',function(e){
      var b=e.target.closest('[data-tool]');if(!b||!S||S.state!=='ready')return;
      var t=b.dataset.tool;
      if(PICS[t]&&S.tool===t&&!S.sigs[t]&&S.synced){openPad(t);return;}
      setTool(t);
    });
    UI.querySelector('.sticks').addEventListener('click',function(e){
      var b=e.target.closest('[data-tick]');if(!b||!S)return;
      S.tick=b.dataset.tick==='cross'?'cross':'check';lsSet(TICK_KEY,S.tick==='cross'?'cross':null);paintTools();msg(hint());
      var m=S.sel,P=m&&m.t==='tick'&&m.kind!==S.tick&&pageOf(m);
      if(P){snap();m.kind=S.tick;renderMarks(P);counts();}   /* a selected one turns into the other */
    });
    $('#sEvery').addEventListener('click',function(){if(S&&S.state==='ready')everyPage();});
    UI.addEventListener('click',function(e){
      var b=e.target.closest('[data-size]');if(!b||!S||!SIZES[b.dataset.size])return;
      S.size=b.dataset.size;lsSet(SIZE_KEY,S.size==='medium'?null:S.size);paintTools();
      if(!$('#sigPad').hidden)padDraw();
    });
    $('#driveCopy').addEventListener('change',function(){if(S&&S.o.driveCopy&&S.o.driveCopy.set)S.o.driveCopy.set(this.checked);});
    UI.querySelector('.sinks').addEventListener('click',function(e){
      var b=e.target.closest('[data-ink]');if(!b||!S)return;
      S.ink=b.dataset.ink;lsSet(INK_KEY,S.ink);paintTools();
      warmSig();
      if(S.sel)recolor(S.sel,INKS[S.ink]);   /* a selected signature or text takes the ink too */
    });
    UI.querySelector('.sstyles').addEventListener('click',function(e){
      var b=e.target.closest('[data-style]');if(!b||!S)return;
      S.style=b.dataset.style==='type'?'type':'hand';lsSet(STYLE_KEY,S.style==='type'?'type':null);paintTools();
      if(S.style==='hand')loadHand();
    });
    $('#fingerDraw').addEventListener('change',function(){if(S)setFinger(this.checked);});
    $('#sChips').addEventListener('click',function(e){
      var b=e.target.closest('[data-chip]');if(!b)return;
      var inp=$('#sText');inp.value=b.dataset.chip;inp.dataset.date=b.dataset.chip===today()?'1':'';
      msg('Tap where the text goes.');
    });
    $('#sText').addEventListener('input',function(){this.dataset.date='';});
    $('#sUndo').addEventListener('click',function(){if(S)undo();});
    $('#sZoomIn').addEventListener('click',function(){if(S&&S.zoom<ZOOMS.length-1){S.zoom++;layout();paintTools();}});
    $('#sZoomOut').addEventListener('click',function(){if(S&&S.zoom>0){S.zoom--;layout();paintTools();}});
    $('#sSave').addEventListener('click',save);
    $('#sCancel').addEventListener('click',cancel);
    $('#sAskKeep').addEventListener('click',function(){$('#sAsk').hidden=true;$('#sCancel').focus();});
    $('#sAskDiscard').addEventListener('click',function(){$('#sAsk').hidden=true;close();});
    $('#sigRedo').addEventListener('click',function(){if(S)openPad(S.tool==='ini'?'ini':'sig');});
    $('#sigCancel').addEventListener('click',closePad);
    $('#sigClear').addEventListener('click',function(){PAD.strokes=[];PAD.photo=null;PAD.job=null;padMsg('');padDraw();$('#sigUse').disabled=true;});
    $('#sigUse').addEventListener('click',useSig);
    $('#sigPhoto').addEventListener('click',function(){$('#sigFile').click();});
    $('#sigFile').addEventListener('change',function(){
      var f=this.files&&this.files[0];this.value='';   /* the same photo can be chosen again */
      if(f)usePhoto(f);
    });
    var pc=$('#sigCanvas');
    pc.addEventListener('pointerdown',function(e){
      if(e.pointerType==='mouse'&&e.button!==0)return;
      e.preventDefault();try{pc.setPointerCapture(e.pointerId);}catch(err){}
      if(PAD.photo||PAD.job){PAD.photo=null;PAD.job=null;padMsg('');}   /* drawing instead of the photo */
      PAD.g={id:e.pointerId,pts:[padPoint(e).concat([pressure(e)])]};PAD.strokes.push(PAD.g.pts);padDraw();
    });
    pc.addEventListener('pointermove',function(e){
      if(!PAD.g||e.pointerId!==PAD.g.id)return;e.preventDefault();
      var evs=e.getCoalescedEvents?e.getCoalescedEvents():[];if(!evs.length)evs=[e];   /* a Pencil sends far more points than frames */
      evs.forEach(function(ev){PAD.g.pts.push(padPoint(ev).concat([pressure(ev)]));});padDraw();
    });
    /* iOS: a touch in the box, Pencil or finger, is always drawing. Left to Safari, a Pencil stroke can turn into a
       scroll (the stroke is cut off), a selection of the words above the box, or Scribble */
    function padTouch(e){e.preventDefault();}
    pc.addEventListener('touchstart',padTouch,{passive:false});pc.addEventListener('touchmove',padTouch,{passive:false});
    function padUp(e){if(!PAD.g||e.pointerId!==PAD.g.id)return;PAD.g=null;$('#sigUse').disabled=!PAD.strokes.length;}
    pc.addEventListener('pointerup',padUp);pc.addEventListener('pointercancel',padUp);
    document.addEventListener('keydown',function(e){
      if(!isOpen())return;
      if(e.key==='Escape'){
        e.preventDefault();e.stopPropagation();
        if(!$('#sigPad').hidden)closePad();
        else if(!$('#sAsk').hidden){$('#sAsk').hidden=true;}
        else cancel();
      }else if((e.key==='z'||e.key==='Z')&&(e.ctrlKey||e.metaKey)&&!e.shiftKey){e.preventDefault();e.stopPropagation();undo();}
      else if((e.key==='Delete'||e.key==='Backspace')&&S.sel&&!(e.target.closest&&e.target.closest('input'))){
        e.preventDefault();e.stopPropagation();
        var P=S.pages.filter(function(p){return p.marks.indexOf(S.sel)>=0;})[0];
        if(P){snap();P.marks.splice(P.marks.indexOf(S.sel),1);S.sel=null;renderMarks(P);counts();}
      }
    },true);
    var rz=0;
    window.addEventListener('resize',function(){if(!S||S.state!=='ready')return;clearTimeout(rz);rz=setTimeout(function(){if(S&&S.state==='ready'&&!S.g)layout();},200);});
  }

  function setup(o){Object.keys(o||{}).forEach(function(k){if(k in CFG)CFG[k]=o[k];});applyKeys();}
  window.HubSign={open:open,isOpen:isOpen,setup:setup};
})();
