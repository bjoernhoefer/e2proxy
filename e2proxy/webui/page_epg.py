"""WebUI: EPG browser (timeline grid)."""
from e2proxy.config import get_proxy_host, get_proxy_port
from e2proxy.webui.i18n import I18N_JS
from e2proxy.webui.base import CSS_BASE
from e2proxy.recorder import is_enabled as recorder_enabled
from e2proxy.recorder import ui as recorder_ui

def build_epg_browser():
    proxy_host = get_proxy_host()
    proxy_port = get_proxy_port()
    rcd_on = recorder_enabled()
    rcd_detail = recorder_ui.epg_detail_html(rcd_on)
    rcd_js = recorder_ui.epg_js(rcd_on)

    css = """
/* EPG Browser */
.epg-wrap{display:flex;flex-direction:column;height:100vh;overflow:hidden;background:var(--bg);}
.epg-toolbar{background:var(--surface);border-bottom:1px solid var(--border);padding:10px 16px;display:flex;align-items:center;gap:12px;flex-shrink:0;z-index:10;}
.epg-title{font-family:'JetBrains Mono',monospace;font-weight:700;font-size:13px;color:var(--accent2);margin-right:8px;}
.epg-day-btn{font-family:'JetBrains Mono',monospace;font-size:10px;padding:4px 10px;border-radius:4px;border:1px solid var(--border);background:none;color:var(--muted);cursor:pointer;transition:all 0.15s;}
.epg-day-btn.active{background:var(--accent);border-color:var(--accent);color:white;}
.epg-search{background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:5px 10px;border-radius:5px;font-size:11px;font-family:'JetBrains Mono',monospace;outline:none;width:200px;}
.epg-search:focus{border-color:var(--accent);}
.epg-now-btn{font-family:'JetBrains Mono',monospace;font-size:10px;padding:4px 10px;border-radius:4px;border:1px solid var(--accent);background:none;color:var(--accent2);cursor:pointer;}
.epg-main{flex:1;overflow:hidden;display:flex;flex-direction:column;}
/* Einziger Scroll-Container */
.epg-scroll-wrap{flex:1;overflow:auto;position:relative;}
.epg-scroll-wrap::-webkit-scrollbar{height:5px;width:5px;}
.epg-scroll-wrap::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px;}
/* Zeit-Header Zeile (sticky top) */
.epg-time-header-row{display:flex;position:sticky;top:0;z-index:10;background:var(--surface);border-bottom:1px solid var(--border);}
.epg-ch-header-cell{width:110px;flex-shrink:0;height:36px;border-right:1px solid var(--border);background:var(--surface);}
.epg-time-header{height:36px;flex:1;position:relative;display:flex;align-items:center;}
.epg-time-label{position:absolute;font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--muted);transform:translateX(-50%);white-space:nowrap;}
/* Jede Zeile: sticky Channel-Label + EPG-Events */
.epg-row{display:flex;height:52px;border-bottom:1px solid var(--border);min-width:max-content;}
.epg-ch-cell{width:110px;flex-shrink:0;position:sticky;left:0;z-index:5;background:var(--surface);border-right:1px solid var(--border);display:flex;align-items:center;justify-content:center;padding:3px;cursor:pointer;transition:background 0.1s;}
.epg-ch-cell:hover{background:var(--surface2);}
.epg-ch-logo{max-width:76px;max-height:24px;object-fit:contain;filter:brightness(0.9);display:block;margin:0 auto 2px;}
.epg-ch-name{font-family:'JetBrains Mono',monospace;font-size:8px;color:var(--muted);text-align:center;padding:0 2px;line-height:1.3;word-break:break-word;}
/* EPG Events Bereich */
.epg-events{flex-shrink:0;position:relative;}
.epg-prog{position:absolute;top:4px;bottom:4px;border-radius:4px;padding:0 6px;overflow:hidden;cursor:pointer;transition:all 0.1s;display:flex;flex-direction:column;justify-content:center;border:1px solid transparent;}
.epg-prog.has-data{background:var(--surface2);border-color:var(--border);}
.epg-prog.has-data:hover{background:var(--surface);border-color:var(--accent);z-index:2;}
.epg-prog.no-data{background:rgba(255,255,255,0.02);border-color:rgba(255,255,255,0.04);}
.epg-prog.now{border-left:2px solid var(--accent);}
.epg-prog-title{font-size:10px;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:500;}
.epg-prog-time{font-family:'JetBrains Mono',monospace;font-size:8px;color:var(--muted);}
/* Now line */
.epg-now-line{position:absolute;top:0;bottom:0;width:2px;background:var(--accent);opacity:0.8;z-index:4;pointer-events:none;}
/* Detail popup */
.epg-detail{position:fixed;bottom:20px;right:20px;width:320px;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px;z-index:100;box-shadow:0 8px 32px rgba(0,0,0,0.5);display:none;}
.epg-detail.show{display:block;}
.epg-detail-title{font-size:13px;font-weight:600;color:var(--text);margin-bottom:4px;}
.epg-detail-time{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--accent2);margin-bottom:8px;}
.epg-detail-ch{font-size:10px;color:var(--muted);margin-bottom:8px;}
.epg-detail-desc{font-size:11px;color:var(--muted);line-height:1.6;max-height:120px;overflow-y:auto;}
.epg-detail-close{position:absolute;top:10px;right:12px;background:none;border:none;color:var(--muted);cursor:pointer;font-size:14px;}
/* Search highlight */
.epg-prog.match{border-color:var(--amber);background:rgba(245,158,11,0.1);}
.epg-loading{display:flex;align-items:center;justify-content:center;height:200px;color:var(--muted);font-family:'JetBrains Mono',monospace;font-size:12px;}
"""

    body = f"""
<div class="header">
  <a class="logo" href="/" style="text-decoration:none">e2<span>proxy</span></a>
  <div class="header-right">
    <a class="btn" href="/settings">⚙ <span data-i18n="nav.settings">Settings</span></a>
    <a class="btn" href="/" data-i18n="nav.mainpage">← Main page</a>
  </div>
</div>

<div class="epg-wrap">
  <div class="epg-toolbar">
    <span class="epg-title">📺 <span data-i18n="epg.title">EPG Browser</span></span>
    <button class="epg-day-btn active" id="btn-today" onclick="switchDay(0)" data-i18n="epg.today">Today</button>
    <button class="epg-day-btn" id="btn-tomorrow" onclick="switchDay(1)" data-i18n="epg.tomorrow">Tomorrow</button>
    <input class="epg-search" type="text" data-i18n-ph="epg.search" placeholder="Search…" oninput="searchEpg(this.value)">
    <button class="epg-now-btn" onclick="scrollToNow()" data-i18n="epg.now">▶ Now</button>
    <span id="epg-status" style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--muted);margin-left:auto;"></span>
  </div>

  <!-- Einziger Scroll-Container — Channel-Label sticky left, EPG rechts daneben -->
  <div class="epg-main">
    <div class="epg-scroll-wrap" id="epg-scroll">
      <!-- Zeit-Header (sticky top) -->
      <div class="epg-time-header-row">
        <div class="epg-ch-header-cell"></div><!-- Platzhalter für Channel-Spalte -->
        <div class="epg-time-header" id="epg-time-header"></div>
      </div>
      <!-- Grid: Zeilen mit sticky Channel-Label -->
      <div id="epg-grid"></div>
      <div class="epg-now-line" id="now-line" style="display:none"></div>
    </div>
  </div>
</div>

<!-- Detail popup -->
<div class="epg-detail" id="epg-detail">
  <button class="epg-detail-close" onclick="closeDetail()">✕</button>
  <div class="epg-detail-title" id="d-title"></div>
  <div class="epg-detail-time" id="d-time"></div>
  <div class="epg-detail-ch" id="d-ch"></div>
  <div class="epg-detail-desc" id="d-desc"></div>
  <div style="margin-top:14px;padding-top:10px;border-top:1px solid var(--border)">
    <div style="font-size:10px;color:var(--muted);margin-bottom:6px;font-family:'JetBrains Mono',monospace" data-i18n="epg.recording">RECORDING</div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
      <button class="btn btn-primary" onclick="recordEpgEvent('series')" style="font-size:11px" data-i18n="epg.record_series">📺 Record series</button>
      <button class="btn" onclick="recordEpgEvent('movie')" style="font-size:11px" data-i18n="epg.record_movie">🎬 Record as movie</button>
      <span id="d-rec-fb" style="font-size:10px;font-family:monospace;margin-left:4px"></span>
    </div>
    <div style="margin-top:6px;font-size:9px;color:var(--muted)">
      <b>Serie:</b> TVDB-Lookup für echte S/E, Daily-Show-Fallback (S2026E<i>tag</i>) ·
      <b>Film:</b> Movies/&lt;Titel&gt; (&lt;Jahr&gt;)/
    </div>
  </div>
{rcd_detail}
  <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
    <a id="d-tmdb-link" href="#" target="_blank" rel="noopener" data-i18n="epg.search_tmdb"
       style="font-family:monospace;font-size:10px;color:var(--accent2);text-decoration:none;border:1px solid var(--border);padding:3px 8px;border-radius:4px;display:none">
      🎬 Search on TMDB →
    </a>
    <a id="d-tvdb-link" href="#" target="_blank" rel="noopener" data-i18n="epg.search_tvdb"
       style="font-family:monospace;font-size:10px;color:var(--accent2);text-decoration:none;border:1px solid var(--border);padding:3px 8px;border-radius:4px;display:none">
      📺 Search on TVDB →
    </a>
  </div>
</div>

<script>
const PX_PER_MIN = 4;  // 4px pro Minute = 240px/h
const ROW_H = 52;
const HDR_H = 36;
let epgData = null;
let currentDay = 0;
let dayStart = 0;
let dayEnd = 0;

function ts2x(ts) {{ return ((ts - dayStart) / 60) * PX_PER_MIN; }}
function x2ts(x) {{ return dayStart + (x / PX_PER_MIN) * 60; }}
function fmtTime(ts) {{
  const d = new Date(ts * 1000);
  return d.getHours().toString().padStart(2,'0') + ':' + d.getMinutes().toString().padStart(2,'0');
}}

function switchDay(d) {{
  currentDay = d;
  document.getElementById('btn-today').classList.toggle('active', d===0);
  document.getElementById('btn-tomorrow').classList.toggle('active', d===1);
  if (epgData) renderGrid();
}}

function setDayBounds() {{
  const now = epgData.now;
  const d = new Date(now * 1000);
  d.setHours(0,0,0,0);
  const base = d.getTime()/1000 + currentDay * 86400;
  dayStart = base;
  dayEnd = base + 86400;
}}

async function loadEpg() {{
  document.getElementById('epg-status').textContent = 'Lade EPG…';
  try {{
    const r = await fetch('/api/epg/data');
    epgData = await r.json();
    if (!epgData.ok) {{ document.getElementById('epg-status').textContent = 'Fehler: ' + (epgData.error||'?'); return; }}
    document.getElementById('epg-status').textContent = epgData.channels.length + ' Sender';
    renderGrid();
    setTimeout(scrollToNowOffset, 100);
  }} catch(e) {{
    document.getElementById('epg-status').textContent = 'Fehler beim Laden';
  }}
}}

function renderGrid() {{
  if (!epgData || !epgData.channels) {{
    console.warn('renderGrid: keine EPG Daten');
    return;
  }}
  setDayBounds();
  const totalMins = 24 * 60;
  const totalW = totalMins * PX_PER_MIN;
  const CH_W = 110;  // Breite der Channel-Spalte
  const channels = epgData.channels;
  const now = epgData.now;

  // Zeit-Header (sticky top)
  const hdr = document.getElementById('epg-time-header');
  hdr.style.width = totalW + 'px';
  let hdrHtml = '';
  for (let h = 0; h < 24; h++) {{
    const x = h * 60 * PX_PER_MIN;
    const ts = dayStart + h * 3600;
    hdrHtml += `<span class="epg-time-label" style="left:${{x}}px">${{fmtTime(ts)}}</span>`;
  }}
  hdr.innerHTML = hdrHtml;

  // Grid: jede Zeile = sticky Channel-Label + EPG Events nebeneinander
  const grid = document.getElementById('epg-grid');
  let rowsHtml = '';

  channels.forEach((ch, ci) => {{
    // Channel Label (sticky left)
    const logo = ch.logo
      ? `<img class="epg-ch-logo" src="${{ch.logo}}" alt="" onerror="this.style.display='none'">`
      : '';
    const chLabel = `<div class="epg-ch-cell">
      <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;width:100%">
        ${{logo}}<span class="epg-ch-name">${{ch.name}}</span>
      </div>
    </div>`;

    // EPG Events
    const events = (ch.events || []).filter(e => e.stop > dayStart && e.start < dayEnd);
    let progsHtml = '';
    let covered = dayStart;
    const sorted = events.sort((a,b) => a.start-b.start);
    for (const ev of sorted) {{
      const s = Math.max(ev.start, dayStart);
      const e = Math.min(ev.stop, dayEnd);
      if (s > covered) {{
        const x = ts2x(covered), w = ts2x(s) - x;
        if (w > 1) progsHtml += `<div class="epg-prog no-data" style="left:${{x}}px;width:${{w}}px"></div>`;
      }}
      const x = ts2x(s), w = Math.max(ts2x(e) - x, 2);
      const isNow = now >= s && now < e ? ' now' : '';
      progsHtml += `<div class="epg-prog has-data${{isNow}}" style="left:${{x}}px;width:${{w}}px"
        data-title="${{escHtml(ev.title)}}" data-desc="${{escHtml(ev.desc||'')}}"
        data-sub="${{escHtml(ev.sub||'')}}" data-chid="${{ch.id}}"
        data-start="${{ev.start}}" data-stop="${{ev.stop}}" data-ch="${{escHtml(ch.name)}}"
        onclick="showDetail(this)">
        <div class="epg-prog-title">${{ev.title||''}}</div>
        <div class="epg-prog-time">${{fmtTime(s)}} – ${{fmtTime(e)}}</div>
      </div>`;
      covered = e;
    }}
    if (covered < dayEnd) {{
      const x = ts2x(covered), w = ts2x(dayEnd) - x;
      if (w > 1) progsHtml += `<div class="epg-prog no-data" style="left:${{x}}px;width:${{w}}px"></div>`;
    }}

    rowsHtml += `<div class="epg-row" style="height:${{ROW_H}}px" data-ch="${{ci}}">
      ${{chLabel}}
      <div class="epg-events" style="width:${{totalW}}px;position:relative">${{progsHtml}}</div>
    </div>`;
  }});

  // Now line über alle Zeilen
  const nowX = ts2x(now);
  const nowLine = (currentDay === 0 && nowX >= 0 && nowX <= totalW)
    ? `<div class="epg-now-line" style="left:${{CH_W + nowX}}px;top:0;bottom:0"></div>`
    : '';

  grid.innerHTML = `<div style="position:relative">${{nowLine}}${{rowsHtml}}</div>`;
}}

function syncScroll() {{
  // Nicht mehr nötig — ein Scroll-Container
}}

function scrollToNow() {{
  if (!epgData || currentDay !== 0) return;
  setDayBounds();
  const nowX = ts2x(epgData.now);
  const wrap = document.getElementById('epg-scroll');
  wrap.scrollLeft = Math.max(0, nowX - wrap.clientWidth / 3);
}}

function scrollToNowOffset() {{
  if (!epgData || currentDay !== 0) return;
  setDayBounds();
  const nowX = ts2x(epgData.now);
  const wrap = document.getElementById('epg-scroll');
  // Jetzt-Linie ca. 30 Minuten (= 120px bei 4px/min) vom linken Rand
  wrap.scrollLeft = Math.max(0, nowX - 120);
}}

function showDetail(el) {{
  const title = el.dataset.title;
  document.getElementById('d-title').textContent = title;
  document.getElementById('d-ch').textContent = el.dataset.ch;
  const s = parseInt(el.dataset.start), e = parseInt(el.dataset.stop);
  document.getElementById('d-time').textContent = fmtTime(s) + ' – ' + fmtTime(e) + ' (' + Math.round((e-s)/60) + ' Min)';
  document.getElementById('d-desc').textContent = el.dataset.desc || '(Keine Beschreibung)';
  // Daten für Aufnahme merken
  const detail = document.getElementById('epg-detail');
  detail.dataset.title = title;
  detail.dataset.chId = el.dataset.chid || '';
  detail.dataset.desc = el.dataset.desc || '';
  detail.dataset.start = el.dataset.start;
  detail.dataset.stop = el.dataset.stop;
  detail.dataset.epTitle = el.dataset.sub || '';
  document.getElementById('d-rec-fb').textContent = '';
  // TMDB / TVDB Links
  const tmdbLink = document.getElementById('d-tmdb-link');
  const tvdbLink = document.getElementById('d-tvdb-link');
  if (tmdbLink && title) {{
    const query = encodeURIComponent(title);
    tmdbLink.href = `https://www.themoviedb.org/search?query=${{query}}`;
    tmdbLink.style.display = 'inline-block';
    tvdbLink.href = `https://www.thetvdb.com/search?query=${{query}}`;
    tvdbLink.style.display = 'inline-block';
  }}
  detail.classList.add('show');
}}

function recordEpgEvent(kind) {{
  const detail = document.getElementById('epg-detail');
  const fb = document.getElementById('d-rec-fb');
  const cid = detail.dataset.chId;
  if (!cid) {{ fb.textContent = 'Fehler: Sender unbekannt'; fb.style.color = 'var(--red)'; return; }}
  const start = parseInt(detail.dataset.start);
  const stop  = parseInt(detail.dataset.stop);
  const dur   = Math.max(60, stop - start + 60);  // +60s Puffer
  fb.textContent = (typeof t==='function'?t('epg.starting'):'Starting recording…');
  fb.style.color = 'var(--muted)';
  apiPost('/api/record/start', {{
    ref: cid.replace(/_/g, ':'),
    title: detail.dataset.title,
    duration: dur,
    description: detail.dataset.desc || '',
    kind: kind,
    episode_title: detail.dataset.epTitle || '',
  }}).then(d => {{
    if (d.ok) {{
      fb.textContent = '✓ ' + (kind === 'movie' ? 'Film' : 'Serie') + ' aufgenommen: ' + (d.file || '').split('/').slice(-2).join('/');
      fb.style.color = 'var(--green)';
    }} else {{
      fb.textContent = 'Fehler: ' + (d.message || 'unbekannt');
      fb.style.color = 'var(--red)';
    }}
  }}).catch(err => {{
    fb.textContent = 'Fehler: ' + err;
    fb.style.color = 'var(--red)';
  }});
}}

function closeDetail() {{
  document.getElementById('epg-detail').classList.remove('show');
}}

function searchEpg(q) {{
  if (!epgData) return;
  const lower = q.toLowerCase().trim();
  document.querySelectorAll('.epg-prog.has-data').forEach(el => {{
    const match = lower && el.dataset.title.toLowerCase().includes(lower);
    el.classList.toggle('match', match);
  }});
}}

function escHtml(s) {{
  return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

// Schließe Detail bei Klick außerhalb
document.addEventListener('click', e => {{
  const d = document.getElementById('epg-detail');
  if (d.classList.contains('show') && !d.contains(e.target) && !e.target.closest('.epg-prog')) {{
    closeDetail();
  }}
}});

loadEpg();
</script>
"""
    # EPG Browser als eigenständige Seite — nicht durch html_page um
    # den EPG-Status-Banner und andere Layout-Eingriffe zu vermeiden
    theme_init = "(function(){var t=localStorage.getItem('e2proxy-theme');if(t==='light')document.documentElement.setAttribute('data-theme','light');})()"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>e2proxy · EPG Browser</title>
<script>{theme_init}</script>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
{CSS_BASE}
html,body{{height:100%;overflow:hidden;}}
{css}
{recorder_ui.EPG_CSS}
</style>
{I18N_JS}
</head>
<body>
{body}
{rcd_js}
<div class="toast" id="toast"></div>
<script>
function showToast(msg,type){{const t=document.getElementById('toast');t.textContent=msg;t.className='toast show'+(type?' '+type:'');setTimeout(()=>t.className='toast',3000);}}
function apiPost(url,data){{return fetch(url,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(data)}}).then(r=>r.json());}}
function toggleTheme(){{const isLight=document.documentElement.getAttribute('data-theme')==='light';if(isLight){{document.documentElement.removeAttribute('data-theme');localStorage.setItem('e2proxy-theme','dark');}}else{{document.documentElement.setAttribute('data-theme','light');localStorage.setItem('e2proxy-theme','light');}}}}
</script>
</body>
</html>"""


