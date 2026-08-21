"""Self-contained HTML/JS rendering of a TimelineEntry list -- the same
sticky day/hour dividers, density-adaptive minimap, and keyword/tag find
as eco.widgets.log_timeline_qt.LogTimelineQt, but as one static HTML
document instead of a Qt widget. Used for JupyterLab/notebook display
(`show`) and for the plain-webapp fallback (`write_html_file`) when
there's no Qt event loop to attach to.

No live callback from the page back into this Python process -- it's a
static document, not a widget with a comm channel. For a source where
`entry.ref` is set (a preview, not the full content -- e.g. a scilog
snippet fetched cheaply by metadata only), clicking a row shows the
timestamp/tags/id that were already cheap to fetch and tells the reader
to open it explicitly (e.g. `logs.open_scilog(id)`) rather than
pretending to fetch it inline. See eco.logs for that half of the
contract.
"""
import json
import os
import tempfile
import webbrowser
from pathlib import Path

_TEMPLATE = r"""<!doctype html><meta charset="utf-8">
<title>__TITLE__</title>
<style>
  :root{
    --bg:#EDF1F1; --surface:#FFFFFF; --surface-2:#E2E8E9; --surface-3:#D7DEDF;
    --ink:#17232B; --ink-dim:#55666D; --ink-faint:#8A9AA0; --border:#CBD3D5;
    --accent:#B85E19; --accent2:#1E7772; --accent2-soft:#1E777222;
    --err:#AE3A2E; --err-soft:#AE3A2E1c; --match:#7A4FB0; --match-soft:#7A4FB026;
    --font-ui:system-ui,-apple-system,"Segoe UI",sans-serif;
    --font-mono:ui-monospace,"SFMono-Regular",Menlo,Consolas,monospace;
  }
  @media (prefers-color-scheme: dark){ :root{
    --bg:#0F1619; --surface:#16232A; --surface-2:#1C2A32; --surface-3:#243541;
    --ink:#E7EEEF; --ink-dim:#93A6AC; --ink-faint:#5E7379; --border:#2A3C44;
    --accent:#E39348; --accent2:#54BDB5; --accent2-soft:#54BDB526;
    --err:#E27A6C; --err-soft:#E27A6C22; --match:#B491E0; --match-soft:#B491E02c;
  }}
  *{box-sizing:border-box;}
  body{margin:0;height:92vh;background:var(--bg);color:var(--ink);font-family:var(--font-ui);}
  .app{height:100%;display:flex;flex-direction:column;border:1px solid var(--border);}
  .toolbar,.findbar{flex:0 0 auto;display:flex;align-items:center;gap:10px;padding:8px 14px;background:var(--surface);border-bottom:1px solid var(--border);}
  .toolbar h1{font-size:13.5px;margin:0;}
  .toolbar .subtitle{font-size:11px;color:var(--ink-dim);font-family:var(--font-mono);}
  .spacer{flex:1;}
  .zoom-readout{font-family:var(--font-mono);font-size:11px;color:var(--ink-dim);padding:4px 9px;background:var(--surface-2);border-radius:5px;border:1px solid var(--border);}
  button.reset,.findbar input,.nav-btn{font-family:var(--font-ui);font-size:11.5px;color:var(--ink);background:var(--surface-2);border:1px solid var(--border);border-radius:5px;padding:5px 9px;cursor:pointer;}
  .findbar input{font-family:var(--font-mono);flex:0 1 300px;cursor:text;}
  .match-count{font-family:var(--font-mono);font-size:11px;color:var(--ink-dim);min-width:50px;}
  mark.hit{background:var(--match-soft);color:var(--match);border-radius:2px;}
  .split{flex:1;display:flex;min-height:0;}
  .log-pane{flex:1;min-width:0;overflow-y:auto;position:relative;}
  .day-header{position:sticky;top:0;z-index:3;background:var(--surface-2);border-bottom:1px solid var(--border);padding:6px 14px;font-size:11.5px;font-weight:700;text-transform:uppercase;letter-spacing:.03em;}
  .hour-header{position:sticky;top:29px;z-index:2;background:var(--surface);border-bottom:1px solid var(--border);padding:3px 14px 3px 22px;font-family:var(--font-mono);font-size:10.5px;color:var(--ink-dim);}
  .row{display:flex;gap:10px;padding:2px 14px 2px 22px;font-family:var(--font-mono);font-size:11.5px;line-height:1.5;}
  .row:hover{background:var(--surface-2);cursor:pointer;}
  .row .ts{flex:0 0 auto;color:var(--ink-faint);width:48px;}
  .row .kind{flex:0 0 auto;width:60px;font-size:10px;font-weight:600;text-transform:uppercase;}
  .row .kind.input{color:var(--accent2);} .row .kind.error{color:var(--err);}
  .row .text{flex:1;min-width:0;white-space:pre;overflow:hidden;text-overflow:ellipsis;}
  .row.error{background:var(--err-soft);} .row.error .text{color:var(--err);}
  .row.match{box-shadow:inset 3px 0 0 var(--match);background:var(--match-soft);}
  .row .tags{color:var(--accent);}
  .minimap-wrap{flex:0 0 120px;position:relative;border-left:1px solid var(--border);background:var(--surface);}
  .minimap-wrap canvas{display:block;width:100%;height:100%;cursor:grab;}
  .minimap-wrap canvas:active{cursor:grabbing;}
  .detail{flex:0 0 auto;max-height:34%;overflow:auto;border-top:1px solid var(--border);padding:10px 16px;font-size:12.5px;display:none;background:var(--surface);}
  .detail.open{display:block;}
  .detail .id{font-family:var(--font-mono);color:var(--ink-dim);font-size:11px;}
</style>
<div class="app">
  <div class="toolbar">
    <div><h1>__TITLE__</h1><div class="subtitle">__SUBTITLE__</div></div>
    <div class="spacer"></div>
    <div class="zoom-readout" id="zoomReadout"></div>
    <button class="reset" id="resetZoom">Reset zoom</button>
  </div>
  <div class="findbar">
    <span style="font-size:11px;font-weight:600;color:var(--ink-faint);">FIND</span>
    <input id="findInput" type="text" placeholder="text, kind, or tag" autocomplete="off">
    <span class="match-count" id="matchCount"></span>
    <button class="nav-btn" id="prevMatch">↑</button>
    <button class="nav-btn" id="nextMatch">↓</button>
  </div>
  <div class="split">
    <div class="log-pane" id="logPane"></div>
    <div class="minimap-wrap" id="minimapWrap"><canvas id="minimap"></canvas></div>
  </div>
  <div class="detail" id="detail"></div>
</div>
<script>
(function(){
  var ENTRIES = __ENTRIES_JSON__;
  ENTRIES.sort(function(a,b){ return a.t-b.t; });
  if (!ENTRIES.length){
    document.getElementById('logPane').innerHTML = '<div style="padding:20px;color:var(--ink-dim);">no entries</div>';
    return;
  }
  var fullMin = ENTRIES[0].t, fullMax = ENTRIES[ENTRIES.length-1].t;

  var logPane = document.getElementById('logPane');
  var frag = document.createDocumentFragment();
  var curDay=null, curHour=null;
  var dayCounts={}, hourCounts={}, maxHour=1;
  ENTRIES.forEach(function(en){
    var dt=new Date(en.t*1000);
    var dk=dt.toDateString(), hk=dk+'|'+dt.getHours();
    dayCounts[dk]=(dayCounts[dk]||0)+1;
    hourCounts[hk]=(hourCounts[hk]||0)+1;
    if (hourCounts[hk]>maxHour) maxHour=hourCounts[hk];
  });
  function fmtClock(dt){ var h=dt.getHours(),m=dt.getMinutes(); return (h<10?'0':'')+h+':'+(m<10?'0':'')+m; }

  ENTRIES.forEach(function(en){
    var dt=new Date(en.t*1000);
    var dk=dt.toDateString(), hk=dk+'|'+dt.getHours();
    if (dk!==curDay){
      curDay=dk; curHour=null;
      var dh=document.createElement('div'); dh.className='day-header';
      dh.textContent = dt.toLocaleDateString(undefined,{weekday:'long',month:'long',day:'numeric'}) + '  ·  ' + dayCounts[dk] + ' entries';
      frag.appendChild(dh);
    }
    if (hk!==curHour){
      curHour=hk;
      var hh=document.createElement('div'); hh.className='hour-header';
      hh.textContent = (dt.getHours()<10?'0':'')+dt.getHours()+':00  ('+hourCounts[hk]+')';
      frag.appendChild(hh);
    }
    var row=document.createElement('div');
    row.className='row'+(en.is_error?' error':'');
    row.dataset.t=en.t;
    var tagsHtml = (en.tags&&en.tags.length) ? ' <span class="tags">['+en.tags.join(', ')+']</span>' : '';
    row.innerHTML = '<span class="ts">'+fmtClock(dt)+'</span><span class="kind '+en.kind+'">'+en.kind+'</span><span class="text"></span>';
    row.querySelector('.text').textContent = en.text;
    if (tagsHtml) row.querySelector('.text').innerHTML += tagsHtml;
    row.addEventListener('click', function(){ showDetail(en); });
    frag.appendChild(row);
  });
  logPane.appendChild(frag);
  var allRows = Array.prototype.slice.call(logPane.querySelectorAll('.row'));

  var detail = document.getElementById('detail');
  function showDetail(en){
    var dt = new Date(en.t*1000);
    var html = '<div class="id">'+dt.toLocaleString()+(en.ref?'  ·  id '+en.ref:'')+'</div>';
    html += '<div style="margin-top:4px;">'+(en.tags&&en.tags.length ? 'tags: '+en.tags.map(function(t){return '<b>'+t+'</b>';}).join(', ') : '<i>no tags</i>')+'</div>';
    if (en.ref){
      html += '<div style="margin-top:8px;color:var(--ink-dim);">this is a cheap preview only. run <code>logs.open_scilog(\''+en.ref+'\')</code> in a cell below to fetch and display the full post.</div>';
    } else {
      html += '<div style="margin-top:8px;white-space:pre-wrap;">'+ (en.text||'') +'</div>';
    }
    detail.innerHTML = html;
    detail.classList.add('open');
  }

  var wrap=document.getElementById('minimapWrap'), canvas=document.getElementById('minimap'), ctx=canvas.getContext('2d');
  var zoomReadout=document.getElementById('zoomReadout'), findInput=document.getElementById('findInput'), matchCountEl=document.getElementById('matchCount');
  var BUCKETS=140, MIN_SPAN=180;
  var win={t0:fullMin,t1:fullMax}, layout=null, isDragging=false, suppressScrollSync=false, matches=[], matchIdx=-1;

  function css(name){ return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function resizeCanvas(){
    var r=wrap.getBoundingClientRect(), dpr=window.devicePixelRatio||1;
    canvas.width=Math.round(r.width*dpr); canvas.height=Math.round(r.height*dpr);
    ctx.setTransform(dpr,0,0,dpr,0,0);
    renderMinimap();
  }
  function bucketEntries(){
    var span=win.t1-win.t0, counts=new Array(BUCKETS).fill(0), errFlags=new Array(BUCKETS).fill(false);
    for (var i=0;i<ENTRIES.length;i++){
      var t=ENTRIES[i].t; if (t<win.t0||t>win.t1) continue;
      var b=Math.min(BUCKETS-1, Math.floor((t-win.t0)/span*BUCKETS));
      counts[b]++; if (ENTRIES[i].is_error) errFlags[b]=true;
    }
    return {counts:counts, errFlags:errFlags};
  }
  function computeLayout(){
    var H=wrap.getBoundingClientRect().height, b=bucketEntries();
    var weights=b.counts.map(function(c){ return 0.14+Math.log(1+c); });
    var total=weights.reduce(function(a,v){return a+v;},0);
    var tops=new Array(BUCKETS+1), acc=0;
    for (var i=0;i<BUCKETS;i++){ tops[i]=acc; acc+=H*(weights[i]/total); }
    tops[BUCKETS]=H;
    layout={H:H,tops:tops,counts:b.counts,errFlags:b.errFlags};
    return layout;
  }
  function bucketTimeRange(i){ var span=win.t1-win.t0; return [win.t0+span*i/BUCKETS, win.t0+span*(i+1)/BUCKETS]; }
  function yToTime(y){
    if (!layout) return win.t0;
    var tops=layout.tops;
    for (var i=0;i<BUCKETS;i++){
      if (y>=tops[i]&&y<=tops[i+1]){
        var frac=(tops[i+1]>tops[i])?(y-tops[i])/(tops[i+1]-tops[i]):0;
        var r=bucketTimeRange(i); return r[0]+frac*(r[1]-r[0]);
      }
    }
    return y<tops[0]?win.t0:win.t1;
  }
  function timeToY(t){
    if (!layout) return 0;
    var span=win.t1-win.t0, bf=(t-win.t0)/span*BUCKETS, i=Math.max(0,Math.min(BUCKETS-1,Math.floor(bf))), frac=bf-i;
    var tops=layout.tops; return tops[i]+frac*(tops[i+1]-tops[i]);
  }
  function lerpColor(a,b,f){
    function h2r(h){ h=h.replace('#',''); return [parseInt(h.slice(0,2),16),parseInt(h.slice(2,4),16),parseInt(h.slice(4,6),16)]; }
    var A=h2r(a),B=h2r(b);
    return 'rgb('+Math.round(A[0]+(B[0]-A[0])*f)+','+Math.round(A[1]+(B[1]-A[1])*f)+','+Math.round(A[2]+(B[2]-A[2])*f)+')';
  }
  function findVisibleRange(){
    var pr=logPane.getBoundingClientRect(), first=null,last=null;
    for (var i=0;i<allRows.length;i++){
      var rr=allRows[i].getBoundingClientRect();
      if (rr.bottom<pr.top||rr.top>pr.bottom) continue;
      var t=+allRows[i].dataset.t;
      if (first===null) first=t; last=t;
    }
    return first===null?null:[first,last];
  }
  function renderMinimap(){
    var l=computeLayout(), r=wrap.getBoundingClientRect(), W=r.width, H=r.height;
    ctx.clearRect(0,0,W,H);
    var accent2=css('--accent2'), accent2soft=css('--accent2-soft'), errCol=css('--err'), border=css('--border'), inkDim=css('--ink-dim');
    var barX=32, barW=W-barX-8;
    for (var i=0;i<BUCKETS;i++){
      var y0=l.tops[i], y1=l.tops[i+1], h=Math.max(1,y1-y0), c=l.counts[i];
      var norm=Math.min(1, Math.log(1+c)/Math.log(1+(Math.max.apply(null,l.counts)||1)));
      var col=c===0?border:lerpColor(accent2, css('--accent'), Math.min(1,c/30));
      ctx.globalAlpha=c===0?0.35:(0.45+0.55*norm);
      ctx.fillStyle=l.errFlags[i]?errCol:col;
      var w=c===0?6:Math.max(6, barW*(0.35+0.65*norm));
      ctx.fillRect(barX,y0,w,Math.max(1,h-0.6));
    }
    ctx.globalAlpha=1;
    ctx.font='10px monospace'; ctx.fillStyle=inkDim; ctx.textBaseline='middle';
    var lastY=-999, spanMs=(win.t1-win.t0)*1000, showHours=spanMs<4*86400000, lastKey=null;
    for (var i=0;i<BUCKETS;i++){
      var tr=bucketTimeRange(i), dt=new Date(tr[0]*1000);
      var key=showHours?(dt.toDateString()+'|'+dt.getHours()):dt.toDateString();
      if (key===lastKey) continue;
      var y=l.tops[i]; if (y-lastY<13) continue;
      lastKey=key; lastY=y;
      var label=showHours?((dt.getHours()<10?'0':'')+dt.getHours()+':00'):dt.toLocaleDateString(undefined,{month:'short',day:'numeric'});
      ctx.fillText(label,2,Math.min(H-6,Math.max(8,y+4)));
      ctx.strokeStyle=border; ctx.lineWidth=1; ctx.beginPath(); ctx.moveTo(barX-4,y); ctx.lineTo(barX,y); ctx.stroke();
    }
    var vis=findVisibleRange();
    if (vis){
      var vy0=timeToY(Math.max(win.t0,vis[0])), vy1=timeToY(Math.min(win.t1,vis[1]));
      ctx.fillStyle=accent2soft; ctx.fillRect(0,vy0,W,Math.max(2,vy1-vy0));
      ctx.strokeStyle=accent2; ctx.lineWidth=1.5; ctx.strokeRect(0.75,vy0,W-1.5,Math.max(2,vy1-vy0));
    }
    if (matches.length){
      ctx.fillStyle=css('--match');
      for (var mi=0; mi<matches.length; mi++){
        var mt=+matches[mi].dataset.t; if (mt<win.t0||mt>win.t1) continue;
        var my=timeToY(mt); ctx.fillRect(16,my-1,barX-6-16,2);
      }
    }
  }
  var renderQueued=false;
  function requestRender(){ if (renderQueued) return; renderQueued=true; requestAnimationFrame(function(){ renderQueued=false; renderMinimap(); }); }
  function fmtSpan(s){ if (s<3600) return Math.round(s/60)+' min'; if (s<86400) return (s/3600).toFixed(1)+' hr'; return (s/86400).toFixed(1)+' days'; }
  function updateReadout(){ zoomReadout.textContent='window: '+fmtSpan(win.t1-win.t0); }
  function clampWindow(t0,t1){
    var span=t1-t0;
    if (span<MIN_SPAN){ var c=(t0+t1)/2; t0=c-MIN_SPAN/2; t1=c+MIN_SPAN/2; span=MIN_SPAN; }
    if (span>(fullMax-fullMin)){ return [fullMin,fullMax]; }
    if (t0<fullMin){ t1+=(fullMin-t0); t0=fullMin; }
    if (t1>fullMax){ t0-=(t1-fullMax); t1=fullMax; }
    return [t0, Math.min(t1,fullMax)];
  }
  wrap.addEventListener('wheel', function(e){
    e.preventDefault();
    var isZoom = e.ctrlKey || e.metaKey;
    if (!isZoom){ logPane.scrollTop += e.deltaY; requestRender(); return; }
    var r=wrap.getBoundingClientRect(), y=e.clientY-r.top, tAtCursor=yToTime(y);
    var factor=Math.exp(e.deltaY*0.0016), span=(win.t1-win.t0)*factor, fracFromTop=(tAtCursor-win.t0)/(win.t1-win.t0);
    var t0=tAtCursor-span*fracFromTop, t1=t0+span, c=clampWindow(t0,t1);
    win.t0=c[0]; win.t1=c[1]; updateReadout(); requestRender();
  }, {passive:false});
  function scrubTo(y){
    var t=yToTime(y), nearest=allRows[0], best=Infinity;
    for (var i=0;i<allRows.length;i++){
      var d=Math.abs(+allRows[i].dataset.t-t);
      if (d<best){ best=d; nearest=allRows[i]; }
      if (+allRows[i].dataset.t>t && d>best) break;
    }
    suppressScrollSync=true; nearest.scrollIntoView({block:'center'});
    setTimeout(function(){ suppressScrollSync=false; requestRender(); }, 60);
  }
  wrap.addEventListener('mousedown', function(e){ isDragging=true; var r=wrap.getBoundingClientRect(); scrubTo(e.clientY-r.top); });
  window.addEventListener('mousemove', function(e){ if (!isDragging) return; var r=wrap.getBoundingClientRect(); scrubTo(Math.max(0,Math.min(r.height,e.clientY-r.top))); });
  window.addEventListener('mouseup', function(){ isDragging=false; });
  logPane.addEventListener('scroll', function(){ if (!suppressScrollSync) requestRender(); }, {passive:true});
  document.getElementById('resetZoom').addEventListener('click', function(){ win.t0=fullMin; win.t1=fullMax; updateReadout(); requestRender(); });

  function rowTextEl(row){ return row.querySelector('.text'); }
  function escapeRe(s){ return s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'); }
  function clearHighlights(){
    for (var i=0;i<allRows.length;i++){
      var row=allRows[i];
      if (!row.classList.contains('match')) continue;
      row.classList.remove('match');
      var el=rowTextEl(row); if (el) el.textContent=el.textContent;
    }
  }
  function runSearch(){
    clearHighlights();
    var q=findInput.value.trim(); matches=[];
    if (q){
      var ql=q.toLowerCase(), re=new RegExp('('+escapeRe(q)+')','ig');
      for (var i=0;i<allRows.length;i++){
        var row=allRows[i];
        if ((row.textContent||'').toLowerCase().indexOf(ql)===-1) continue;
        matches.push(row); row.classList.add('match');
        var el=rowTextEl(row); if (el) el.innerHTML=el.textContent.replace(re,'<mark class="hit">$1</mark>');
      }
    }
    matchIdx=matches.length?0:-1; updateMatchCount(); requestRender();
    if (matches.length) goToMatch(0);
  }
  function updateMatchCount(){
    var q=findInput.value.trim();
    matchCountEl.textContent = !q?'':(matches.length?(matchIdx+1)+' / '+matches.length:'no matches');
  }
  function goToMatch(i){
    if (!matches.length) return;
    matchIdx=((i%matches.length)+matches.length)%matches.length;
    var row=matches[matchIdx], t=+row.dataset.t;
    if (t<win.t0||t>win.t1){ win.t0=fullMin; win.t1=fullMax; updateReadout(); }
    suppressScrollSync=true; row.scrollIntoView({block:'center'});
    setTimeout(function(){ suppressScrollSync=false; requestRender(); }, 60);
    updateMatchCount(); requestRender();
  }
  findInput.addEventListener('input', runSearch);
  findInput.addEventListener('keydown', function(e){ if (e.key!=='Enter') return; e.preventDefault(); goToMatch(matchIdx+(e.shiftKey?-1:1)); });
  document.getElementById('prevMatch').addEventListener('click', function(){ goToMatch(matchIdx-1); });
  document.getElementById('nextMatch').addEventListener('click', function(){ goToMatch(matchIdx+1); });

  window.addEventListener('resize', resizeCanvas);
  updateReadout(); resizeCanvas();
})();
</script>
"""


def _entry_to_dict(e):
    return {
        "t": e.t,
        "kind": e.kind,
        "text": e.text,
        "tags": list(e.tags or ()),
        "ref": e.ref,
        "is_error": bool(e.is_error),
    }


def render_html(entries, title="Log timeline", subtitle=""):
    """A self-contained HTML document (no external requests, no build
    step) with the sticky-header list + density-adaptive minimap + find
    bar. `entries` is any iterable of TimelineEntry."""
    entries_json = json.dumps([_entry_to_dict(e) for e in entries])
    html = _TEMPLATE.replace("__TITLE__", title).replace("__SUBTITLE__", subtitle)
    html = html.replace("__ENTRIES_JSON__", entries_json)
    return html


def show(entries, title="Log timeline", subtitle=""):
    """Display inline in a Jupyter/JupyterLab cell."""
    from IPython.display import HTML, display

    display(HTML(render_html(entries, title=title, subtitle=subtitle)))


def _has_display():
    """False on a headless SSH session with no X forwarding / no Wayland
    -- webbrowser.open() doesn't know that and instead burns several
    seconds trying (and failing) each browser-launcher candidate in turn
    before giving up. Measured live: ~6.3s wasted in exactly that
    situation. Cheap to check, not foolproof (a real desktop session
    should always have one of these set), but avoids the common case."""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def write_html_file(entries, path=None, title="Log timeline", subtitle="", open_browser=True):
    """Write the viewer to a standalone .html file (the plain-webapp
    fallback, for when there's neither a Qt event loop nor a Jupyter
    kernel) and optionally open it in the default browser -- skipped
    automatically (regardless of `open_browser`) when there's no
    DISPLAY/WAYLAND_DISPLAY to open it on. Returns the Path written to."""
    if path is None:
        fd, path = tempfile.mkstemp(prefix="eco_log_timeline_", suffix=".html")
        os.close(fd)
    path = Path(path)
    path.write_text(render_html(entries, title=title, subtitle=subtitle))
    if open_browser and _has_display():
        webbrowser.open(path.as_uri())
    return path
