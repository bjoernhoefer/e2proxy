"""WebUI: main page (channel list, player, tuner status)."""
import urllib.parse
import urllib.request
from datetime import datetime

from e2proxy.config import get_config, get_receivers, is_receiver_locked
from e2proxy.favorites import get_favorites
from e2proxy.receivers import is_receiver_online, receiver_health_snapshot
from e2proxy.recordings import _active_recordings, _active_recordings_lock
from e2proxy.webui.base import html_page
from e2proxy import receivers

def build_web_ui(channels):
    cfg = get_config()
    proxy_host = cfg.get("proxy_host", "127.0.0.1")
    proxy_port = int(cfg.get("proxy_port", 8888))
    device_profiles = cfg.get("device_profiles", {})
    default_profile = cfg.get("default_device_profile", "Web-SD")

    # Receiver Status Bar
    rx_items = []
    for r in get_receivers():
        if not r.get("enabled", True):
            continue
        state = receivers._receiver_state.get(r["id"])
        online = is_receiver_online(r["id"])
        if is_receiver_locked(r):
            color = "#64748b"
            status = "🔒 gesperrt"
            rx_items.append(
                f'<div class="rx-item">'
                f'<span class="rx-dot" id="rx-dot-{r["id"]}" style="background:{color}"></span>'
                f'<span>{r["name"]}</span>'
                f'<span class="rx-status" id="rx-status-{r["id"]}" '
                f'onclick="openStreamModal(\'{r["id"]}\')" title="Details">{status}</span>'
                f'</div>'
            )
            continue
        if state is None:
            color = "#22c55e" if online else "#ef4444"
            status = "free"
            _hs = receiver_health_snapshot().get(r["id"], {})
            if _hs.get("unhealthy"):
                color = "#ef4444"
                status = f"⚠ nicht erreichbar ({int(_hs.get('unreachable_sec', 0)) // 60} min)"
        else:
            # Check if this receiver has an active recording
            is_rec = False
            with _active_recordings_lock:
                for rec in _active_recordings.values():
                    if rec.get("receiver") == r["id"]:
                        is_rec = True
                        rec_title = rec.get("title", "")
                        break
            if is_rec:
                color = "#ef4444"
                dot_class = "rx-dot recording"
                ch = state.get("channel_name", "?")
                client = state.get("client_ip", "?")
                since = state.get("started", "?")
                status = f'🔴 REC {rec_title} · {ch} · {client} · {since}'
            else:
                color = "#f59e0b"
                dot_class = "rx-dot"
                ch = state.get("channel_name", "?")
                client = state.get("client_ip", "?")
                since = state.get("started", "?")
                status = f"{ch} · {client} · {since}"
        rx_items.append(
            f'<div class="rx-item">'
            f'<span class="{dot_class if state else "rx-dot"}" id="rx-dot-{r["id"]}" style="background:{color}"></span>'
            f'<span>{r["name"]}</span>'
            f'<span class="rx-status" id="rx-status-{r["id"]}" '
            f'onclick="openStreamModal(\'{r["id"]}\')" title="Details">{status}</span>'
            f'</div>'
        )
    rx_html = "\n".join(rx_items)

    # Profile options
    profile_options = "\n".join(
        f'<option value="{pid}"{" selected" if pid == default_profile else ""}>{dp.get("label", pid)}</option>'
        for pid, dp in device_profiles.items()
    )

    # Channel list
    favs = get_favorites()
    fav_set = {f["ref"] for f in favs}
    ch_map = {ch["ref"]: ch for ch in channels}
    bouquet_groups = {}
    for ch in channels:
        b = ch["bouquet"]
        bouquet_groups.setdefault(b, []).append(ch)

    def ch_row(ch):
        ref_enc = urllib.parse.quote(ch["ref"])
        name = ch["name"].replace("'", "\\'").replace("<", "&lt;").replace('"', '&quot;')
        name_d = ch["name"].replace("<", "&lt;")
        return (f'<div class="channel-row" data-name="{name.lower()}" '
                f'data-ref="{ref_enc}" data-chname="{name_d}">'
                f'<span class="ch-name">{name_d}</span>'
                f'<div class="ch-actions">'
                f'<button class="btn-play" onclick="playChannel(this)" title="Abspielen">▶</button>'
                f'<button class="btn-copy" onclick="copyStream(\'{ref_enc}\')" title="URL kopieren">⎘</button>'
                f'</div></div>')

    channels_html = ""
    fav_channels = [ch_map[f["ref"]] for f in favs if f["ref"] in ch_map]
    if fav_channels:
        rows = "\n".join(ch_row(ch) for ch in fav_channels)
        channels_html += f'<div class="group-section open" id="group-favorites"><div class="group-label" onclick="toggleGroup(\'group-favorites\')"><span>⭐ Favoriten</span><span class="group-arrow">▾</span><span class="group-count">{len(fav_channels)}</span></div><div class="group-rows">{rows}</div></div>\n'

    for bouquet, chs in bouquet_groups.items():
        label = bouquet.replace("_", " ").title()
        rows = "\n".join(ch_row(ch) for ch in chs)
        channels_html += f'<div class="group-section" id="group-{bouquet}"><div class="group-label" onclick="toggleGroup(\'group-{bouquet}\')"><span>{label}</span><span class="group-arrow">▸</span><span class="group-count">{len(chs)}</span></div><div class="group-rows" style="display:none">{rows}</div></div>\n'

    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    total = len(channels)

    css = """
.rx-bar{background:var(--surface);border-bottom:1px solid var(--border);padding:7px 24px;display:flex;gap:16px;font-size:11px;font-family:'JetBrains Mono',monospace;flex-wrap:wrap;align-items:center;}
.rx-item{display:flex;align-items:center;gap:5px;}
.rx-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0;transition:background 0.3s;}
.rx-dot.recording{background:#ef4444 !important;animation:rec-blink 1s infinite;}
@keyframes rec-blink{0%,100%{opacity:1;box-shadow:0 0 4px #ef4444}50%{opacity:0.3;box-shadow:none}}
.rx-status{color:var(--muted);cursor:pointer;border-bottom:1px dotted transparent;}
.rx-status:hover{color:var(--text);border-bottom-color:var(--muted);}
.rx-kill{background:none;border:1px solid var(--red);color:var(--red);width:16px;height:16px;border-radius:3px;cursor:pointer;font-size:9px;display:inline-flex;align-items:center;justify-content:center;margin-left:2px;}
.rx-kill:hover{background:var(--red);color:white;}
.sm-overlay{position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.7);display:none;align-items:center;justify-content:center;padding:20px;}
.sm-overlay.show{display:flex;}
.sm-card{background:var(--surface);border:1px solid var(--border);border-radius:10px;max-width:440px;width:100%;box-shadow:0 12px 48px rgba(0,0,0,0.5);overflow:hidden;}
.sm-head{display:flex;align-items:center;gap:10px;padding:14px 18px;border-bottom:1px solid var(--border);}
.sm-title{font-family:'JetBrains Mono',monospace;font-size:13px;color:var(--accent2);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.sm-close{background:none;border:1px solid var(--border);color:var(--text);width:26px;height:26px;border-radius:5px;cursor:pointer;font-size:13px;flex-shrink:0;}
.sm-close:hover{border-color:var(--accent);}
.sm-body{padding:14px 18px;display:flex;flex-direction:column;gap:9px;}
.sm-row{display:flex;gap:10px;font-size:12px;align-items:flex-start;}
.sm-key{font-family:'JetBrains Mono',monospace;font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);width:120px;flex-shrink:0;padding-top:1px;}
.sm-val{color:var(--text);flex:1;word-break:break-word;}
.sm-foot{padding:12px 18px;border-top:1px solid var(--border);display:flex;justify-content:flex-end;}
.main{display:flex;flex:1;overflow:hidden;height:calc(100vh - 80px);}
.sidebar{width:320px;flex-shrink:0;display:flex;flex-direction:column;border-right:1px solid var(--border);overflow:hidden;}
.search-bar{padding:10px 12px;border-bottom:1px solid var(--border);background:var(--surface);}
.search-input{width:100%;background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:7px 10px;border-radius:5px;font-size:12px;outline:none;}
.search-input:focus{border-color:var(--accent);}
.search-input::placeholder{color:var(--muted);}
.stats{font-size:10px;color:var(--muted);font-family:'JetBrains Mono',monospace;margin-top:5px;}
.channel-list{flex:1;overflow-y:auto;padding:2px 6px 12px;}
.channel-list::-webkit-scrollbar{width:3px;}
.channel-list::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px;}
.group-section{margin-bottom:1px;}
.group-label{font-family:'JetBrains Mono',monospace;font-size:9px;font-weight:700;color:var(--accent);letter-spacing:2px;text-transform:uppercase;padding:10px 6px 6px;border-bottom:1px solid var(--border);cursor:pointer;display:flex;align-items:center;gap:5px;user-select:none;transition:background 0.1s;}
.group-label:hover{background:var(--surface2);border-radius:3px;}
.group-label span:first-child{flex:1;}
.group-arrow{font-size:9px;color:var(--muted);}
.group-count{font-size:9px;color:var(--muted);background:var(--surface2);padding:1px 4px;border-radius:6px;}
.group-rows{overflow:hidden;}
.channel-row{display:flex;align-items:center;justify-content:space-between;padding:7px 6px;border-radius:4px;transition:background 0.1s;cursor:pointer;}
.channel-row:hover{background:var(--surface2);}
.channel-row.active{background:var(--surface2);border-left:2px solid var(--accent);padding-left:4px;}
.channel-row.hidden{display:none;}
.ch-name{font-size:13px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.ch-actions{display:flex;gap:3px;opacity:0;transition:opacity 0.1s;flex-shrink:0;}
.channel-row:hover .ch-actions{opacity:1;}
.btn-play,.btn-copy{background:none;border:1px solid var(--border);color:var(--text);width:24px;height:24px;border-radius:4px;cursor:pointer;font-size:11px;display:flex;align-items:center;justify-content:center;transition:all 0.1s;}
.btn-play:hover{background:var(--accent);border-color:var(--accent);}
.btn-copy:hover{background:var(--surface2);border-color:var(--accent);}
.player-area{flex:1;display:flex;flex-direction:column;background:var(--bg);overflow:hidden;}
.player-header{background:var(--surface);border-bottom:1px solid var(--border);padding:9px 16px;display:flex;align-items:center;gap:10px;min-height:44px;}
.now-playing{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--accent2);}
.now-playing.idle{color:var(--muted);}
.player-controls{margin-left:auto;display:flex;gap:6px;}
.video-container{flex:1;display:flex;align-items:center;justify-content:center;background:var(--bg);position:relative;}
video{width:100%;height:100%;object-fit:contain;}
.placeholder{display:flex;flex-direction:column;align-items:center;gap:10px;color:var(--muted);}
.placeholder-icon{font-size:42px;opacity:0.3;}
.loading-overlay{position:absolute;inset:0;background:rgba(0,0,0,0.85);display:none;flex-direction:column;align-items:center;justify-content:center;gap:10px;}
.loading-overlay.show{display:flex;}
.spinner{width:32px;height:32px;border:3px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin 0.8s linear infinite;}
@keyframes spin{to{transform:rotate(360deg);}}
.loading-text{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--muted);}
@media(max-width:700px){.main{flex-direction:column;height:auto;}.sidebar{width:100%;height:50vh;border-right:none;border-bottom:1px solid var(--border);}.player-area{height:50vh;}.ch-actions{opacity:1;}}
"""

    body = f"""
<div class="header">
  <div class="logo">e2<span>proxy</span></div>
  <div class="header-right">
    <select class="select" id="profile-select" onchange="onProfileChange()" data-i18n-title="main.device_profile" title="Device profile">
      {profile_options}
    </select>
    <a class="btn" href="/epg-browser">📺 EPG</a>
    <a class="btn" href="/favorites">⭐ <span data-i18n="nav.favorites">Favorites</span></a>
    <a class="btn" href="/settings">⚙ <span data-i18n="nav.settings">Settings</span></a>
    <a class="btn btn-primary" href="/playlist.m3u?profile={default_profile}&list=favorites" id="m3u-link">↓ M3U</a>
    <a class="btn" href="/help" data-i18n-title="nav.help" title="Help" style="font-size:13px;padding:6px 10px">❓</a>
  </div>
</div>

<div class="rx-bar">
  {rx_html}
  <span style="margin-left:auto;color:var(--muted);font-size:10px"><span data-i18n="main.status">Status:</span> {now}</span>
</div>

<div class="sm-overlay" id="stream-modal" onclick="if(event.target===this)closeStreamModal()">
  <div class="sm-card">
    <div class="sm-head">
      <span class="sm-title" id="sm-title">Stream</span>
      <button class="sm-close" onclick="closeStreamModal()" title="Close">✕</button>
    </div>
    <div class="sm-body" id="sm-body"></div>
    <div class="sm-foot" id="sm-foot"></div>
  </div>
</div>

<div class="main">
  <div class="sidebar">
    <div class="search-bar">
      <input class="search-input" id="search" type="text" data-i18n-ph="common.search" placeholder="Search channels…" oninput="filterChannels(this.value)" autocomplete="off">
      <div class="stats" id="stats">{total} <span data-i18n="common.channels">channels</span></div>
    </div>
    <div class="channel-list" id="channel-list">
      {channels_html}
    </div>
  </div>

  <div class="player-area">
    <div class="player-header">
      <span class="now-playing idle" id="now-playing" data-i18n="main.select_channel">Select a channel…</span>
      <div class="player-controls">
        <button class="btn btn-danger" id="btn-stop" onclick="stopStream()" style="display:none">■ Stop</button>
        <button class="btn" onclick="toggleFullscreen()" data-i18n="main.fullscreen">⤢ Fullscreen</button>
      </div>
    </div>
    <div class="video-container" id="video-container">
      <div class="placeholder" id="placeholder">
        <div class="placeholder-icon">📺</div>
        <div style="font-family:'JetBrains Mono',monospace;font-size:12px;" data-i18n="main.pick_from_list">Pick a channel from the list</div>
      </div>
      <video id="player" controls style="display:none" playsinline></video>
      <div class="loading-overlay" id="loading">
        <div class="spinner"></div>
        <div class="loading-text" id="loading-text" data-i18n="main.connecting">Connecting…</div>
      </div>
    </div>
  </div>
</div>

<script>
const PROXY = 'http://{proxy_host}:{proxy_port}';
let activeRow = null;

function getProfile() {{ return document.getElementById('profile-select').value; }}

function getStreamUrl(ref, name) {{
  const profile = getProfile();
  return PROXY + '/stream?ref=' + ref + '&profile=' + encodeURIComponent(profile) + (name ? '&name=' + encodeURIComponent(name) : '');
}}

function onProfileChange() {{
  const profile = getProfile();
  document.getElementById('m3u-link').href = PROXY + '/playlist.m3u?profile=' + encodeURIComponent(profile) + '&list=favorites';
  apiPost('/api/config-update', {{default_device_profile: profile}}).catch(()=>{{}});
  if (activeRow) startPlay(activeRow);
}}

function playChannel(btn) {{ startPlay(btn.closest('.channel-row')); }}

function startPlay(row) {{
  const ref = row.dataset.ref;
  const name = row.dataset.chname;
  const url = getStreamUrl(ref, name);
  if (activeRow) activeRow.classList.remove('active');
  row.classList.add('active');
  activeRow = row;
  document.getElementById('now-playing').textContent = name;
  document.getElementById('now-playing').classList.remove('idle');
  document.getElementById('placeholder').style.display = 'none';
  document.getElementById('btn-stop').style.display = 'inline-block';
  document.getElementById('loading').classList.add('show');
  document.getElementById('loading-text').textContent = 'Zappt & verbindet…';
  const video = document.getElementById('player');
  video.style.display = 'block';
  video.src = url;
  video.oncanplay = () => {{ document.getElementById('loading').classList.remove('show'); video.play().catch(()=>{{}}); }};
  video.onerror = () => {{
    if (!video.src || video.src === window.location.href) return; // Stop wurde gedrückt
    document.getElementById('loading').classList.remove('show');
    showToast('Stream konnte nicht started werden', 'error');
  }};
}}

document.getElementById('channel-list').addEventListener('click', e => {{
  const row = e.target.closest('.channel-row');
  if (row && !e.target.closest('.btn-copy')) startPlay(row);
}});

function stopStream() {{
  const v = document.getElementById('player');
  v.pause(); v.src = '';
  v.style.display = 'none';
  document.getElementById('placeholder').style.display = 'flex';
  document.getElementById('loading').classList.remove('show');
  document.getElementById('btn-stop').style.display = 'none';
  document.getElementById('now-playing').textContent = 'Sender auswählen…';
  document.getElementById('now-playing').classList.add('idle');
  if (activeRow) activeRow.classList.remove('active');
  activeRow = null;
}}

function toggleFullscreen() {{
  const c = document.getElementById('video-container');
  if (!document.fullscreenElement) c.requestFullscreen().catch(()=>{{}});
  else document.exitFullscreen();
}}

function copyStream(ref) {{
  const url = getStreamUrl(ref, '');
  navigator.clipboard.writeText(url).then(() => showToast('URL kopiert!', 'success')).catch(() => {{ showToast('Kopieren fehlgeschlagen', 'error'); }});
}}

function toggleGroup(id) {{
  const s = document.getElementById(id);
  if (!s) return;
  const rows = s.querySelector('.group-rows');
  const arrow = s.querySelector('.group-arrow');
  const open = s.classList.contains('open');
  rows.style.display = open ? 'none' : 'block';
  arrow.textContent = open ? '▸' : '▾';
  open ? s.classList.remove('open') : s.classList.add('open');
}}

function filterChannels(q) {{
  const rows = document.querySelectorAll('.channel-row');
  const ql = q.toLowerCase().trim();
  let visible = 0;
  rows.forEach(r => {{
    const m = !ql || (r.dataset.name||'').includes(ql);
    r.classList.toggle('hidden', !m);
    if (m) visible++;
  }});
  document.querySelectorAll('.group-section').forEach(s => {{
    const rows2 = s.querySelector('.group-rows');
    const arrow = s.querySelector('.group-arrow');
    const hasVisible = s.querySelectorAll('.channel-row:not(.hidden)').length > 0;
    if (ql) {{
      rows2.style.display = hasVisible ? 'block' : 'none';
      arrow.textContent = hasVisible ? '▾' : '▸';
      hasVisible ? s.classList.add('open') : s.classList.remove('open');
    }} else {{
      const isFav = s.id === 'group-favorites';
      rows2.style.display = isFav ? 'block' : 'none';
      arrow.textContent = isFav ? '▾' : '▸';
      isFav ? s.classList.add('open') : s.classList.remove('open');
    }}
  }});
  document.getElementById('stats').textContent = ql ? `${{visible}} von {total}` : `{total} Sender`;
}}

let _lastStatus = [];
let _modalRxId = null;

function killReceiver(id) {{
  if (!confirm(t('sm.confirm_stop'))) return;
  fetch('/kill?receiver=' + id).then(r=>r.json()).then(d => {{
    showToast(d.message || 'Stream stopped', d.ok ? 'success' : 'error');
    closeStreamModal();
    refreshStatus();
  }});
}}

function _fmtDur(sec) {{
  sec = Math.max(0, parseInt(sec||0, 10));
  const h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60), s = sec%60;
  const p = n => String(n).padStart(2,'0');
  return h > 0 ? `${{h}}:${{p(m)}}:${{p(s)}}` : `${{m}}:${{p(s)}}`;
}}

function _smRow(key, val) {{
  return `<div class="sm-row"><span class="sm-key">${{key}}</span><span class="sm-val">${{val}}</span></div>`;
}}

function _esc(s) {{
  return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}

function openStreamModal(id) {{
  _modalRxId = id;
  document.getElementById('stream-modal').classList.add('show');
  renderStreamModal();
}}

function closeStreamModal() {{
  _modalRxId = null;
  document.getElementById('stream-modal').classList.remove('show');
}}

function renderStreamModal() {{
  if (_modalRxId == null) return;
  const rx = _lastStatus.find(r => r.id === _modalRxId);
  const titleEl = document.getElementById('sm-title');
  const bodyEl = document.getElementById('sm-body');
  const footEl = document.getElementById('sm-foot');
  if (!rx) {{ closeStreamModal(); return; }}
  const s = rx.stream;
  if (!s) {{
    titleEl.textContent = rx.name;
    const msg = rx.locked ? t('sm.locked') : t('sm.no_stream');
    bodyEl.innerHTML = `<div class="sm-row"><span class="sm-val" style="color:var(--muted)">${{_esc(msg)}}</span></div>`;
    footEl.innerHTML = '';
    return;
  }}
  const isRec = !!rx.recording;
  titleEl.textContent = (isRec ? '🔴 ' : '') + (s.channel_name || rx.name);
  let tc;
  if (s.transcode && s.transcode.active) {{
    const parts = [];
    if (s.transcode.label) parts.push(_esc(s.transcode.label));
    if (s.transcode.codec) parts.push('<code>'+_esc(s.transcode.codec)+'</code>');
    tc = '✅ ' + t('sm.yes') + (parts.length ? ' · ' + parts.join(' · ') : '');
  }} else {{
    tc = '➖ ' + t('sm.no_passthrough');
  }}
  let rows = '';
  rows += _smRow(t('sm.receiver'), _esc(rx.name));
  rows += _smRow(t('sm.channel'), _esc(s.channel_name || '?'));
  if (isRec) rows += _smRow(t('sm.recording'), '🔴 ' + _esc(rx.recording.title||''));
  rows += _smRow(t('sm.device'), _esc(s.client_ip || '?'));
  rows += _smRow(t('sm.software'), _esc(s.software || '?') + (s.user_agent ? ` <span style="color:var(--muted);font-size:10px">(${{_esc(s.user_agent)}})</span>` : ''));
  if (s.profile) rows += _smRow(t('sm.profile'), _esc(s.profile));
  rows += _smRow(t('sm.data'), _esc(s.bytes_human || '0 B'));
  rows += _smRow(t('sm.transcode'), tc);
  rows += _smRow(t('sm.runtime'), _fmtDur(s.duration_sec) + (s.started ? ` <span style="color:var(--muted);font-size:10px">(${{_esc(s.started)}})</span>` : ''));
  bodyEl.innerHTML = rows;
  footEl.innerHTML = `<button class="btn btn-danger" onclick="killReceiver('${{rx.id}}')">⏹ ${{t('sm.stop')}}</button>`;
}}

function refreshStatus() {{
  fetch('/api/status').then(r=>r.json()).then(data => {{
    _lastStatus = data.receivers || [];
    _lastStatus.forEach(rx => {{
      const dot = document.getElementById('rx-dot-' + rx.id);
      const st  = document.getElementById('rx-status-' + rx.id);
      if (!dot || !st) return;
      if (rx.locked) {{
        dot.style.background = '#64748b';
        dot.className = 'rx-dot';
        st.textContent = '🔒 gesperrt';
      }} else if (rx.busy && rx.stream) {{
        const isRec = !!rx.recording;
        dot.style.background = isRec ? '#ef4444' : '#f59e0b';
        dot.className = isRec ? 'rx-dot recording' : 'rx-dot';
        const ch = rx.stream.channel_name || '?';
        const cl = rx.stream.client_ip || '?';
        const since = rx.stream.started || '?';
        const recLabel = isRec ? '🔴 REC ' + (rx.recording.title||'') + ' · ' : '';
        st.textContent = recLabel + ch + ' · ' + cl + ' · ' + since;
      }} else if (rx.unhealthy) {{
        dot.style.background = '#ef4444';
        dot.className = 'rx-dot';
        st.textContent = '⚠ ' + t('rx.unreachable') + ' ' + (rx.unreachable_sec ? new Date(Date.now() - rx.unreachable_sec*1000).toLocaleString([], {{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}}) : '?');
        st.title = t('rx.unreachable_hint');
      }} else {{
        dot.style.background = rx.online ? '#22c55e' : '#ef4444';
        dot.className = 'rx-dot';
        st.textContent = t('rec.tuner_free');
        st.title = 'Details';
      }}
    }});
    if (_modalRxId != null) renderStreamModal();
  }}).catch(()=>{{}});
}}

setInterval(refreshStatus, 5000);
</script>

</script>
"""
    return html_page("Live TV", body, css)


