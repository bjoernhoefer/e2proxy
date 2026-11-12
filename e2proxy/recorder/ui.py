"""Recorder UI fragments: Settings → Recorder tab and EPG-browser integration.

The JS is always embedded (so the WebUI self-check covers it) and stays inert
while the recorder is disabled.
"""
import json

_I18N = {
    "en": {
        "rcd.tab": "Recorder", "rcd.title": "Series recorder", "rcd.enabled": "Recorder active",
        "rcd.enabled_hint": "Records series/movies from the EPG automatically (formerly e2recorder).",
        "rcd.pre": "Start earlier", "rcd.post": "Run longer", "rcd.late": "Max. late start",
        "rcd.lookahead": "EPG lookahead", "rcd.interval": "EPG scan interval",
        "rcd.profile": "Profile", "rcd.profile_hint": "empty = recording default",
        "rcd.remote": "Further e2proxies", "rcd.remote_hint": "one URL per line — shared tuner pool",
        "rcd.save": "Save", "rcd.series": "Series", "rcd.planned": "Planned & recent",
        "rcd.scan": "Scan EPG now", "rcd.none": "Nothing here yet — use the EPG browser to add series.",
        "rcd.section": "RECORDER", "rcd.once": "⏺ Record this airing", "rcd.all": "🔁 Record all episodes",
        "rcd.skip": "⏭ Skip airing", "rcd.unskip": "↩ Record again", "rcd.disabled": "Recorder disabled",
    },
    "de": {
        "rcd.tab": "Recorder", "rcd.title": "Serien-Recorder", "rcd.enabled": "Recorder aktiv",
        "rcd.enabled_hint": "Nimmt Serien/Filme automatisch aus dem EPG auf (ehemals e2recorder).",
        "rcd.pre": "Früher starten", "rcd.post": "Länger aufnehmen", "rcd.late": "Max. verspäteter Start",
        "rcd.lookahead": "EPG-Vorschau", "rcd.interval": "EPG-Scan-Intervall",
        "rcd.profile": "Profil", "rcd.profile_hint": "leer = Aufnahme-Standard",
        "rcd.remote": "Weitere e2proxies", "rcd.remote_hint": "eine URL pro Zeile — gemeinsamer Tuner-Pool",
        "rcd.save": "Speichern", "rcd.series": "Serien", "rcd.planned": "Geplant & zuletzt",
        "rcd.scan": "EPG jetzt scannen", "rcd.none": "Noch leer — Serien im EPG-Browser anlegen.",
        "rcd.section": "RECORDER", "rcd.once": "⏺ Diese Sendung aufnehmen", "rcd.all": "🔁 Alle Folgen aufnehmen",
        "rcd.skip": "⏭ Sendung überspringen", "rcd.unskip": "↩ Doch aufnehmen", "rcd.disabled": "Recorder deaktiviert",
    },
}

I18N_PATCH = ("<script>(function(){if(typeof I18N==='undefined')return;var p="
              + json.dumps(_I18N, ensure_ascii=False)
              + ";Object.assign(I18N.en,p.en);Object.assign(I18N.de,p.de);})();</script>")

_COMMON_JS = r"""
function rcdNorm(r){return (r||'').replace(/\/+$/,'').replace(/:+$/,'').replace(/_+$/,'').replace(/:/g,'_');}
function rcdEsc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function rcdTr(k,f){return (typeof t==='function'&&t(k)!==k)?t(k):f;}
function rcdCall(method,url,data){
  return fetch(url,{method:method,headers:{'Content-Type':'application/json'},body:data?JSON.stringify(data):undefined})
    .then(function(r){return r.json();});
}
"""

# ── Settings tab ─────────────────────────────────────────────────────
SETTINGS_TAB_BUTTON = ('<button class="tab-btn" onclick="switchTab(\'recorder\', this)">⏺ '
                       '<span data-i18n="rcd.tab">Recorder</span></button>')

SETTINGS_PANEL = I18N_PATCH + r"""
  <div class="tab-panel" id="tab-recorder">
    <div class="card">
      <div class="card-title">⏺ <span data-i18n="rcd.title">Series recorder</span>
        <span id="rcd-state" style="margin-left:auto;font-family:monospace;font-size:10px;color:var(--muted)"></span></div>
      <table class="table"><tbody>
        <tr><td style="color:var(--muted);width:170px;font-size:11px" data-i18n="rcd.enabled">Recorder active</td>
          <td><label style="font-size:11px"><input type="checkbox" id="rcd-enabled">
            <span data-i18n="rcd.enabled_hint">Records series/movies from the EPG automatically.</span></label></td></tr>
        <tr><td style="color:var(--muted);font-size:11px" data-i18n="rcd.pre">Start earlier</td>
          <td><input class="input" id="rcd-pre" type="number" min="0" style="width:90px"> s
            &nbsp; <span style="color:var(--muted);font-size:11px" data-i18n="rcd.post">Run longer</span>
            <input class="input" id="rcd-post" type="number" min="0" style="width:90px"> s
            &nbsp; <span style="color:var(--muted);font-size:11px" data-i18n="rcd.late">Max. late start</span>
            <input class="input" id="rcd-late" type="number" min="0" style="width:90px"> s</td></tr>
        <tr><td style="color:var(--muted);font-size:11px" data-i18n="rcd.lookahead">EPG lookahead</td>
          <td><input class="input" id="rcd-lookahead" type="number" min="1" style="width:90px"> h
            &nbsp; <span style="color:var(--muted);font-size:11px" data-i18n="rcd.interval">EPG scan interval</span>
            <input class="input" id="rcd-interval" type="number" min="300" style="width:90px"> s</td></tr>
        <tr><td style="color:var(--muted);font-size:11px" data-i18n="rcd.profile">Profile</td>
          <td><input class="input" id="rcd-profile" style="width:160px;font-family:monospace;font-size:11px">
            <span style="color:var(--muted);font-size:10px" data-i18n="rcd.profile_hint">empty = recording default</span></td></tr>
        <tr><td style="color:var(--muted);font-size:11px" data-i18n="rcd.remote">Further e2proxies</td>
          <td><textarea class="input" id="rcd-remote" rows="2" style="width:100%;max-width:400px;min-height:0;height:56px;font-family:monospace;font-size:11px" placeholder="http://192.168.1.20:8888"></textarea>
            <div style="color:var(--muted);font-size:10px" data-i18n="rcd.remote_hint">one URL per line</div></td></tr>
      </tbody></table>
      <div style="display:flex;gap:8px;align-items:center;margin-top:10px">
        <button class="btn btn-primary" onclick="rcdSave()" data-i18n="rcd.save">Save</button>
        <button class="btn" onclick="rcdScan()" data-i18n="rcd.scan">Scan EPG now</button>
        <span id="rcd-backends" style="font-family:monospace;font-size:10px;color:var(--muted)"></span>
      </div>
    </div>
    <div class="card" id="rcd-lists">
      <div class="card-title">🔁 <span data-i18n="rcd.series">Series</span></div>
      <div id="rcd-series"></div>
      <div class="card-title" style="margin-top:16px">📋 <span data-i18n="rcd.planned">Planned &amp; recent</span></div>
      <div id="rcd-recs"></div>
    </div>
  </div>
"""

SETTINGS_JS = "<script>" + _COMMON_JS + r"""
var RCD_STATUS_COLOR={scheduled:'var(--accent2)',starting:'var(--yellow)',recording:'var(--red)',done:'var(--green)',failed:'var(--red)',skipped:'var(--muted)',missed:'var(--muted)'};
function rcdLoad(){
  rcdCall('GET','/api/recorder/settings').then(function(s){
    document.getElementById('rcd-enabled').checked=!!s.enabled;
    document.getElementById('rcd-pre').value=s.pre_buffer_sec;
    document.getElementById('rcd-post').value=s.post_buffer_sec;
    document.getElementById('rcd-late').value=s.max_late_sec;
    document.getElementById('rcd-lookahead').value=s.epg_lookahead_hours;
    document.getElementById('rcd-interval').value=s.epg_scan_interval;
    document.getElementById('rcd-profile').value=s.stream_profile||'';
    document.getElementById('rcd-remote').value=(s.remote_proxies||[]).join('\n');
    document.getElementById('rcd-lists').style.display=s.enabled?'':'none';
    if(s.enabled){rcdLoadStatus();rcdLoadLists();}
    else document.getElementById('rcd-state').textContent=rcdTr('rcd.disabled','Recorder disabled');
  });
}
function rcdLoadStatus(){
  rcdCall('GET','/api/recorder/status').then(function(d){
    if(!d.enabled)return;
    var c=d.recordings||{};
    document.getElementById('rcd-state').textContent=(d.series||0)+' series · '+(c.scheduled||0)+' planned · '+(c.recording||0)+' recording'
      +(d.scan&&d.scan.last_run?' · scan '+d.scan.last_run.slice(11,16):'');
    document.getElementById('rcd-backends').textContent=(d.backends||[]).map(function(b){
      return (b.online?'● ':'○ ')+b.url.replace(/^https?:\/\//,'')+' '+b.free+'/'+b.total;}).join('  ');
  });
}
function rcdSave(){
  var num=function(id){return parseInt(document.getElementById(id).value||'0',10);};
  rcdCall('POST','/api/recorder/settings',{
    enabled:document.getElementById('rcd-enabled').checked,
    pre_buffer_sec:num('rcd-pre'),post_buffer_sec:num('rcd-post'),max_late_sec:num('rcd-late'),
    epg_lookahead_hours:num('rcd-lookahead'),epg_scan_interval:num('rcd-interval'),
    stream_profile:document.getElementById('rcd-profile').value,
    remote_proxies:document.getElementById('rcd-remote').value.split('\n')
  }).then(function(d){showToast(d.ok?'✓ Recorder':'Fehler: '+(d.error||'?'),d.ok?'success':'error');rcdLoad();});
}
function rcdScan(){rcdCall('POST','/api/recorder/scan').then(function(){showToast('✓ Scan','success');setTimeout(rcdLoad,3000);});}
function rcdFmt(ts){var d=new Date(ts*1000);return d.toLocaleDateString(undefined,{weekday:'short',day:'2-digit',month:'2-digit'})+' '+d.toTimeString().slice(0,5);}
function rcdLoadLists(){
  rcdCall('GET','/api/recorder/series').then(function(list){
    var el=document.getElementById('rcd-series');
    if(!list.length){el.innerHTML='<div style="color:var(--muted);font-size:11px">'+rcdTr('rcd.none','Nothing here yet.')+'</div>';return;}
    el.innerHTML='<table class="table"><thead><tr><th></th><th>Name</th><th>Sender</th><th>Regex</th><th>Keep</th><th></th></tr></thead><tbody>'
      +list.map(function(s){return '<tr><td><input type="checkbox" '+(s.enabled?'checked':'')+' onchange="rcdSeries(\''+s.id+'\',{enabled:this.checked})"></td>'
        +'<td>'+rcdEsc(s.name)+(s.once?' <span style="color:var(--muted);font-size:10px">(1×)</span>':'')+(s.kind==='movie'?' 🎬':'')+'</td>'
        +'<td style="font-size:11px">'+rcdEsc(s.channel_name)+'</td>'
        +'<td><input class="input" style="width:180px;font-family:monospace;font-size:10px" value="'+rcdEsc(s.regex_pattern)+'" placeholder="'+rcdEsc(s.name)+'" onchange="rcdSeries(\''+s.id+'\',{regex_pattern:this.value})"></td>'
        +'<td><input class="input" type="number" min="0" style="width:56px" value="'+(s.keep_last||0)+'" onchange="rcdSeries(\''+s.id+'\',{keep_last:parseInt(this.value||\'0\',10)})"></td>'
        +'<td><button class="btn" style="font-size:10px" onclick="rcdDelSeries(\''+s.id+'\')">✕</button></td></tr>';}).join('')+'</tbody></table>';
  });
  rcdCall('GET','/api/recorder/recordings').then(function(list){
    var now=Date.now()/1000, el=document.getElementById('rcd-recs');
    var up=list.filter(function(r){return r.stop_ts>now-7*86400;}).slice(-40);
    if(!up.length){el.innerHTML='<div style="color:var(--muted);font-size:11px">—</div>';return;}
    el.innerHTML='<table class="table"><tbody>'+up.map(function(r){
      var act=r.status==='scheduled'?'<button class="btn" style="font-size:10px" onclick="rcdAct(\'skip\',\''+r.id+'\')">⏭</button>'
        :(r.status==='skipped'&&r.stop_ts>now?'<button class="btn" style="font-size:10px" onclick="rcdAct(\'unskip\',\''+r.id+'\')">↩</button>':'');
      return '<tr><td style="font-family:monospace;font-size:10px;white-space:nowrap">'+rcdFmt(r.epg_start_ts||r.start_ts)+'</td>'
        +'<td style="font-size:11px">'+rcdEsc(r.channel_name)+'</td><td>'+rcdEsc(r.title)+(r.subtitle?' <span style="color:var(--muted);font-size:10px">'+rcdEsc(r.subtitle)+'</span>':'')+'</td>'
        +'<td style="font-family:monospace;font-size:10px;color:'+(RCD_STATUS_COLOR[r.status]||'var(--muted)')+'" title="'+rcdEsc(r.error||'')+'">'+r.status+'</td>'
        +'<td style="white-space:nowrap">'+act+' <button class="btn" style="font-size:10px" onclick="rcdDelRec(\''+r.id+'\')">✕</button></td></tr>';}).join('')+'</tbody></table>';
  });
}
function rcdSeries(id,ch){rcdCall('PUT','/api/recorder/series/'+id,ch).then(function(d){showToast(d.ok?'✓':'Fehler',d.ok?'success':'error');});}
function rcdDelSeries(id){if(confirm('Serie löschen? / Delete series?'))rcdCall('DELETE','/api/recorder/series/'+id).then(rcdLoadLists);}
function rcdDelRec(id){if(confirm('Eintrag löschen? / Delete entry?'))rcdCall('DELETE','/api/recorder/recordings/'+id).then(rcdLoadLists);}
function rcdAct(a,id){rcdCall('POST','/api/recorder/'+a,{id:id}).then(rcdLoadLists);}
</script>"""

# ── EPG browser ──────────────────────────────────────────────────────
EPG_CSS = """
.epg-prog.rcd-scheduled,.epg-prog.rcd-starting{box-shadow:inset 3px 0 0 var(--red);}
.epg-prog.rcd-recording{box-shadow:inset 3px 0 0 var(--red);background:rgba(239,68,68,0.18);}
.epg-prog.rcd-match{box-shadow:inset 3px 0 0 var(--accent2);}
.epg-prog.rcd-done{box-shadow:inset 3px 0 0 var(--green);}
.epg-prog.rcd-skipped .epg-prog-title{text-decoration:line-through;opacity:.6;}
.epg-prog.rcd-scheduled .epg-prog-title::before,.epg-prog.rcd-recording .epg-prog-title::before{content:'● ';color:var(--red);}
"""


def epg_detail_html(enabled):
    hide = "" if enabled else "display:none"
    return f"""
  <div id="rcd-detail" style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border);{hide}">
    <div style="font-size:10px;color:var(--muted);margin-bottom:6px;font-family:'JetBrains Mono',monospace" data-i18n="rcd.section">RECORDER</div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
      <button class="btn btn-primary" id="rcd-btn-once" onclick="rcdEpg('once')" style="font-size:11px" data-i18n="rcd.once">⏺ Record this airing</button>
      <button class="btn" id="rcd-btn-all" onclick="rcdEpg('all')" style="font-size:11px" data-i18n="rcd.all">🔁 Record all episodes</button>
      <button class="btn" id="rcd-btn-skip" onclick="rcdEpg('skip')" style="font-size:11px;display:none" data-i18n="rcd.skip">⏭ Skip airing</button>
      <button class="btn" id="rcd-btn-unskip" onclick="rcdEpg('unskip')" style="font-size:11px;display:none" data-i18n="rcd.unskip">↩ Record again</button>
      <span id="rcd-fb" style="font-size:10px;font-family:monospace;margin-left:4px"></span>
    </div>
  </div>"""


def epg_js(enabled):
    return I18N_PATCH + "<script>" + _COMMON_JS + f"const RCD_ENABLED = {'true' if enabled else 'false'};" + r"""
var rcdMarks={}, rcdCur=null;
function rcdKeyOf(el){return rcdNorm(el.dataset.chid)+'@'+el.dataset.start;}
function rcdApply(){
  document.querySelectorAll('.epg-prog.has-data').forEach(function(el){
    el.className=el.className.replace(/\brcd-\w+/g,'').trim();
    var m=rcdMarks[rcdKeyOf(el)]; if(m)el.classList.add('rcd-'+m.status);
  });
}
function rcdLoadMarks(){
  if(!RCD_ENABLED)return;
  fetch('/api/recorder/epg-marks').then(function(r){return r.json();}).then(function(d){
    if(d&&d.ok){rcdMarks=d.marks||{};rcdApply();}
  }).catch(function(){});
}
function rcdDetail(el){
  if(!RCD_ENABLED)return;
  rcdCur=el; var m=rcdMarks[rcdKeyOf(el)]||null;
  var st=m?m.status:'';
  document.getElementById('rcd-btn-once').style.display=(st===''||st==='match')?'':'none';
  document.getElementById('rcd-btn-all').style.display=(st==='match'||st==='recording')?'none':'';
  document.getElementById('rcd-btn-skip').style.display=st==='scheduled'?'':'none';
  document.getElementById('rcd-btn-unskip').style.display=st==='skipped'?'':'none';
  var fb=document.getElementById('rcd-fb'); fb.style.color='var(--muted)';
  fb.textContent=m?(st+(m.serie?' · '+m.serie:'')):'';
}
function rcdEpg(action){
  if(!rcdCur)return;
  var el=rcdCur, m=rcdMarks[rcdKeyOf(el)]||{}, fb=document.getElementById('rcd-fb'), p;
  if(action==='skip'||action==='unskip'){p=rcdCall('POST','/api/recorder/'+action,{id:m.rec_id});}
  else{p=rcdCall('POST','/api/recorder/event',{channel_ref:el.dataset.chid.replace(/_/g,':'),channel_name:el.dataset.ch,
    start:parseInt(el.dataset.start,10),stop:parseInt(el.dataset.stop,10),title:el.dataset.title,
    sub:el.dataset.sub||'',desc:el.dataset.desc||'',kind:'series',recurring:action==='all'});}
  p.then(function(d){
    fb.style.color=d.ok?'var(--green)':'var(--red)';
    fb.textContent=d.ok?'✓':'Fehler: '+(d.error||'?');
    setTimeout(function(){rcdLoadMarks();setTimeout(function(){rcdDetail(el);},400);},action==='all'?1500:200);
  }).catch(function(e){fb.style.color='var(--red)';fb.textContent=String(e);});
}
if(RCD_ENABLED){
  var _rcdRender=renderGrid; renderGrid=function(){_rcdRender();rcdApply();};
  var _rcdShow=showDetail; showDetail=function(el){_rcdShow(el);rcdDetail(el);};
  rcdLoadMarks(); setInterval(rcdLoadMarks,60000);
}
</script>"""
