"""WebUI: settings page."""
import json
import urllib.parse
import urllib.request

from e2proxy.version import INTERNAL_VERSION, VERSION
from e2proxy.config import get_config, get_owif_config, is_receiver_locked
from e2proxy.recordings import get_recordings_config
from e2proxy.compression import get_compression_config
from e2proxy.webui.base import html_page

def build_settings_ui():
    cfg = get_config()
    receivers = cfg.get("receivers", [])
    transcode_profiles = cfg.get("transcode_profiles", {})
    device_profiles = cfg.get("device_profiles", {})
    proxy_host = cfg.get("proxy_host", "?")
    proxy_port = cfg.get("proxy_port", 8888)
    tmdb_api_key = cfg.get("tmdb_api_key", "")
    tvdb_api_key = cfg.get("tvdb_api_key", "")
    recorder_url = cfg.get("recorder_url", "")
    log_retention_days = cfg.get("log_retention_days", 5)
    diag_enabled_attr = "checked" if cfg.get("diag_logging", False) else ""
    # OpenWebif-Emulation ("virtuelle Enigma2-Box")
    owif = get_owif_config()
    owif_enabled_attr = "checked" if owif["enabled"] else ""
    owif_webif_port = owif["webif_port"]
    owif_stream_port = owif["stream_port"]
    owif_recordings_port = owif["recordings_port"]
    owif_recordings_attr = "checked" if owif["recordings_enabled"] else ""
    owif_bind = owif["bind"]
    owif_default_profile = owif["default_profile"]
    owif_profile_options = "".join(
        f'<option value="{pid}"{" selected" if pid == owif_default_profile else ""}>{pid}</option>'
        for pid in transcode_profiles.keys()
    )
    owif_ua_text = "\n".join(f'{o["match"]} => {o["profile"]}' for o in owif["ua_overrides"])
    version = VERSION
    api_logging_enabled = cfg.get("api_logging", False)
    cfg_json = json.dumps(cfg, indent=2, ensure_ascii=False)
    # Recording config
    rcfg = get_recordings_config()
    rec_path = rcfg["path"]
    rec_max_dur = rcfg["max_duration"]
    rec_plex_url = rcfg["plex_url"]
    rec_plex_token = rcfg["plex_token"]
    rec_plex_section = rcfg["plex_section"]
    rec_plex_verify_attr = "checked" if rcfg.get("plex_verify") else ""
    rec_plex_dvr_refresh_attr = "checked" if rcfg.get("plex_dvr_refresh") else ""
    # Compression config
    ccfg = get_compression_config()
    comp_enabled_attr = "checked" if ccfg["enabled"] else ""
    comp_delete_attr = "checked" if ccfg["delete_original"] else ""
    comp_window_start = ccfg["window_start"]
    comp_window_end = ccfg["window_end"]
    comp_profile_current = ccfg["profile"]
    comp_cpu_limit = ccfg["cpu_limit"]
    comp_ignore_window_attr = "checked" if ccfg["ignore_window"] else ""
    # Section HTML — kommagetrennte IDs → Anzeige
    if rec_plex_section:
        _ids = [s.strip() for s in rec_plex_section.split(",") if s.strip()]
        rec_plex_section_html = " ".join(
            f'<span style="background:var(--accent);color:white;border-radius:4px;padding:1px 6px;margin:1px">{i}</span>'
            for i in _ids
        )
    else:
        rec_plex_section_html = '<span style="color:var(--muted)">Keine — alle Libraries</span>'
    rec_profile_opts = "".join(
        f'<option value="{pid}" {"selected" if pid == rcfg["profile"] else ""}>{tp.get("label", pid)}</option>'
        for pid, tp in transcode_profiles.items()
    )

    # ── Receiver rows ──────────────────────────────────────
    rx_rows = ""
    for r in receivers:
        enabled = '<span class="tag green">aktiv</span>' if r.get("enabled", True) else '<span class="tag muted">inaktiv</span>'
        if is_receiver_locked(r):
            enabled += ' <span class="tag red">🔒 gesperrt</span>'
        default = '<span class="tag amber">default</span>' if r.get("default") else ""
        r_json = json.dumps(r).replace('"', '&quot;')
        rx_rows += f'''<tr>
          <td><span class="tag">{r['id']}</span></td>
          <td>{r['name']}</td>
          <td>{r['ip']}</td>
          <td>{r.get('port', 80)}</td>
          <td>{r.get('stream_port', 8001)}</td>
          <td>{enabled}</td>
          <td>{default}</td>
          <td style="white-space:nowrap">
            <button class="btn" onclick='editReceiver({r_json})' style="font-size:10px;padding:3px 7px">✎</button>
            <button class="btn btn-danger" onclick='deleteReceiver("{r['id']}")' style="font-size:10px;padding:3px 7px">✕</button>
          </td>
        </tr>'''


    # ── Transcode profile rows ─────────────────────────────
    tp_rows = ""
    for pid, tp in transcode_profiles.items():
        codec = tp.get("codec", "?")
        container = tp.get("container", "?")
        vb = tp.get("vbitrate", "-")
        abitrate = tp.get("abitrate", "-")
        height = tp.get("height", "-")
        label = tp.get("label", pid)
        tp_json = json.dumps({**{"id": pid}, **tp}).replace('"', '&quot;')
        tp_rows += f'''<tr>
          <td><span class="tag">{pid}</span></td>
          <td>{label}</td>
          <td>{codec}</td>
          <td>{container}</td>
          <td>{vb if vb != "-" else abitrate}</td>
          <td>{"" if height == "-" else str(height)+"p"}</td>
          <td style="white-space:nowrap">
            <button class="btn" onclick='editTranscodeProfile({tp_json})' style="font-size:10px;padding:3px 7px">✎</button>
            <button class="btn btn-danger" onclick='deleteTranscodeProfile("{pid}")' style="font-size:10px;padding:3px 7px">✕</button>
          </td>
        </tr>'''


    # ── Device profile rows ────────────────────────────────
    dp_rows = ""
    for pid, dp in device_profiles.items():
        tp_name = dp.get("transcode_profile", "?")
        receiver = dp.get("receiver", "auto")
        label = dp.get("label", pid)
        short = dp.get("short_url", "")
        m3u_url = f"http://{proxy_host}:{proxy_port}/playlist.m3u?profile={urllib.parse.quote(pid)}&list=favorites"
        short_cell = f'<a href="http://{proxy_host}:{proxy_port}/{short}" class="tag amber" style="text-decoration:none">/{short}</a>' if short else '<span class="tag muted">-</span>'
        dp_json = json.dumps({**{"id": pid}, **dp}).replace('"', '&quot;')
        dp_rows += f'''<tr>
          <td><span class="tag">{pid}</span></td>
          <td>{label}</td>
          <td>{tp_name}</td>
          <td>{receiver}</td>
          <td>{short_cell}</td>
          <td><a href="{m3u_url}" class="tag green" style="text-decoration:none">↓ M3U</a></td>
          <td style="white-space:nowrap">
            <button class="btn" onclick='editDeviceProfile({dp_json})' style="font-size:10px;padding:3px 7px">✎</button>
            <button class="btn btn-danger" onclick='deleteDeviceProfile("{pid}")' style="font-size:10px;padding:3px 7px">✕</button>
          </td>
        </tr>'''


    # ── API Reference ──────────────────────────────────────
    # API Referenz als strukturierte Liste für Copy-Buttons
    base = f"http://{proxy_host}:{proxy_port}"
    api_entries = [
        ("GET", f"{base}/api/status", "Receiver Status"),
        ("GET", f"{base}/api/config", "Aktuelle Config"),
        ("POST", f"{base}/api/config", "Config speichern"),
        ("GET", f"{base}/api/favorites", "Favoriten"),
        ("POST", f"{base}/api/favorites", "Favoriten speichern"),
        ("GET", f"{base}/kill?receiver=ID", "Stream abbrechen"),
        None,
        ("GET", f"{base}/playlist.m3u?profile=NAME", "M3U Alle Sender"),
        ("GET", f"{base}/playlist.m3u?profile=NAME&list=favorites", "M3U Nur Favoriten"),
        ("GET", f"{base}/stream?ref=REF&profile=NAME", "Stream"),
        ("GET", f"{base}/epg.xml", "XMLTV EPG"),
        ("GET", f"{base}/epg/refresh", "EPG neu laden"),
        None,
        ("GET", f"{base}/plex/discover.json", "Plex Gerätebeschreibung"),
        ("GET", f"{base}/plex/lineup.json", "Kanalliste (Favoriten)"),
        ("GET", f"{base}/plex/lineup_status.json", "Tuner-Status"),
        ("GET", f"{base}/plex/device.xml", "UPnP Beschreibung"),
        ("→",  f"{base}/plex", "In Plex eingeben"),
    ]
    api_rows_html = ""
    for entry in api_entries:
        if entry is None:
            api_rows_html += '<tr><td colspan="4" style="padding:4px 0;border:none;"></td></tr>'
            continue
        method, url, desc = entry
        method_color = "var(--accent2)" if method == "GET" else "var(--amber)" if method == "POST" else "var(--green)"
        api_rows_html += f'''<tr>
          <td style="width:44px;padding:6px 8px;font-family:'JetBrains Mono',monospace;font-size:10px;color:{method_color};font-weight:600">{method}</td>
          <td style="padding:6px 8px;font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--text)">{url}</td>
          <td style="padding:6px 8px;font-size:11px;color:var(--muted)">{desc}</td>
          <td style="padding:6px 8px;white-space:nowrap">
            <button class="copy-btn" onclick="copyUrl(this, '{url}')" title="Kopieren">⎘</button>
          </td>
        </tr>'''

    css = """
/* ── Tab Navigation ─────────────────────────────────── */
.settings-wrap{max-width:960px;margin:0 auto;padding:24px;}
.tabs{display:flex;gap:2px;border-bottom:1px solid var(--border);margin-bottom:24px;}
.tab-btn{font-family:'JetBrains Mono',monospace;font-size:11px;padding:8px 16px;background:none;border:none;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px;transition:all 0.15s;}
.tab-btn:hover{color:var(--text);}
.tab-btn.active{color:var(--accent2);border-bottom-color:var(--accent);}
.tab-panel{display:none;}
.tab-panel.active{display:block;}

/* ── Cards ───────────────────────────────────────────── */
.card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:16px;}
.card-title{font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:700;color:var(--accent);letter-spacing:1px;text-transform:uppercase;margin-bottom:14px;display:flex;align-items:center;gap:8px;}
.card-desc{font-size:11px;color:var(--muted);margin-bottom:12px;line-height:1.6;}

/* ── Tables ──────────────────────────────────────────── */
.table{width:100%;border-collapse:collapse;font-size:12px;}
.table th{font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;padding:6px 8px;border-bottom:1px solid var(--border);text-align:left;}
.table td{padding:8px 8px;border-bottom:1px solid var(--border);}
.table tr:last-child td{border-bottom:none;}
.table tr:hover td{background:var(--surface2);}

/* ── Tags ────────────────────────────────────────────── */
.tag{font-family:'JetBrains Mono',monospace;font-size:10px;background:var(--surface2);border:1px solid var(--border);padding:2px 6px;border-radius:4px;}
.tag.green{border-color:var(--green);color:var(--green);}
.tag.amber{border-color:var(--amber);color:var(--amber);}
.tag.muted{color:var(--muted);}
.tag.red{border-color:var(--red);color:var(--red);}

/* ── Forms ───────────────────────────────────────────── */
.input{width:100%;background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:7px 10px;border-radius:5px;font-size:12px;outline:none;font-family:monospace;}
.input:focus{border-color:var(--accent);}
textarea.input{min-height:380px;resize:vertical;font-size:11px;line-height:1.5;}
.field{margin-bottom:12px;}
.label{font-size:11px;color:var(--muted);font-family:'JetBrains Mono',monospace;margin-bottom:4px;display:block;}
.flex{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px;}

/* ── Code box ────────────────────────────────────────── */
.code-box{background:var(--surface2);border:1px solid var(--border);border-radius:6px;padding:14px;font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--muted);line-height:2;overflow-x:auto;white-space:pre;}

/* ── Copy Button ─────────────────────────────────────── */
.copy-btn{background:var(--surface2);border:1px solid var(--border);color:var(--muted);padding:2px 7px;border-radius:4px;font-size:11px;cursor:pointer;transition:all 0.15s;}
.copy-btn:hover{border-color:var(--accent);color:var(--accent2);}
.copy-btn.copied{border-color:var(--green);color:var(--green);}
#api-table td{border-bottom:1px solid var(--border);}
#api-table tr:last-child td{border-bottom:none;}

/* ── Log box ─────────────────────────────────────────── */
.log-box{background:var(--surface2);border:1px solid var(--border);border-radius:6px;padding:12px;font-family:'JetBrains Mono',monospace;font-size:11px;line-height:1.6;overflow-y:auto;max-height:400px;color:var(--text);}
.log-info{color:var(--muted);}
.log-debug{color:var(--muted);}
.log-warn{color:var(--amber);}
.log-error{color:var(--red);}

/* ── Maintenance actions ──────────────────────────────── */
.action-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;}
.action-card{background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:16px;display:flex;flex-direction:column;gap:8px;}
.action-card-title{font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:600;color:var(--text);}
.action-card-desc{font-size:11px;color:var(--muted);line-height:1.5;flex:1;}
.action-feedback{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--green);min-height:16px;margin-top:2px;}
"""

    body = f"""
<div class="header">
  <a class="logo" href="/" style="text-decoration:none;cursor:pointer">e2<span>proxy</span></a>
  <div class="header-right">
    <a class="btn" href="/" data-i18n="nav.mainpage">← Main page</a>
  </div>
</div>

<div class="settings-wrap">

  <div class="tabs">
    <button class="tab-btn active" onclick="switchTab('config', this)">⚙ <span data-i18n="set.tab_config">Configuration</span></button>
    <button class="tab-btn" onclick="switchTab('maintenance', this)">🔧 <span data-i18n="set.tab_maint">Maintenance</span></button>
    <button class="tab-btn" onclick="switchTab('epg', this)">📅 EPG</button>
    <button class="tab-btn" onclick="switchTab('recording', this)">📹 <span data-i18n="set.tab_rec">Recordings</span></button>
    <button class="tab-btn" onclick="switchTab('usage', this)">📊 <span data-i18n="set.tab_usage">Auswertung</span></button>
    <button class="tab-btn" onclick="switchTab('help', this)">📖 <span data-i18n="set.tab_api">API</span> / <span data-i18n="nav.help">Help</span></button>
  </div>

  <!-- ── TAB: AUFNAHMEN ────────────────────────────────── -->
  <div class="tab-panel" id="tab-recording">

    <div class="card">
      <div class="card-title">📹 <span data-i18n="set.rec_settings">Recording Settings</span></div>
      <table class="table">
        <tbody>
          <tr>
            <td style="color:var(--muted);width:160px"><span data-i18n="rec.path">Recording Path</span></td>
            <td><input class="input" id="rec-path" value="{rec_path}" style="width:100%;max-width:400px;font-family:monospace;font-size:11px"></td>
          </tr>
          <tr>
            <td style="color:var(--muted)"><span data-i18n="rec.default_profile">Default Profile</span></td>
            <td><select class="select" id="rec-profile">{rec_profile_opts}</select></td>
          </tr>
          <tr>
            <td style="color:var(--muted)"><span data-i18n="rec.max_duration">Max Duration (Watchdog)</span></td>
            <td><input class="input" id="rec-max-dur" type="number" value="{rec_max_dur}" style="width:100px"> <span data-i18n="rec.seconds">seconds</span></td>
          </tr>
          <tr>
            <td style="color:var(--muted)"><span data-i18n="rec.plex_url">Plex URL</span></td>
            <td><input class="input" id="rec-plex-url" value="{rec_plex_url}" placeholder="http://localhost:32400" style="width:100%;max-width:400px;font-family:monospace;font-size:11px"></td>
          </tr>
          <tr>
            <td style="color:var(--muted)"><span data-i18n="rec.plex_token">Plex Token</span></td>
            <td>
              <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
                <input class="input" id="rec-plex-token" type="password" value="{rec_plex_token}" style="width:280px;font-family:monospace;font-size:11px">
                <button class="btn" onclick="showPlexLogin()" style="font-size:10px"><span data-i18n="rec.via_login">🔑 Generate via login</span></button>
              </div>
              <div id="plex-login-form" style="display:none;margin-top:8px;padding:10px;background:var(--surface2);border-radius:6px;border:1px solid var(--border)">
                <div style="font-family:monospace;font-size:10px;color:var(--muted);margin-bottom:6px">plex.tv Login (wird nicht gespeichert)</div>
                <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
                  <input class="input" id="plex-login-user" type="text" placeholder="E-Mail / Username" style="font-size:11px;width:180px">
                  <input class="input" id="plex-login-pass" type="password" placeholder="Passwort" style="font-size:11px;width:140px">
                  <button class="btn btn-primary" onclick="generatePlexToken()" style="font-size:10px"><span data-i18n="set.get_token">Get token</span></button>
                  <span id="plex-token-fb" style="font-family:monospace;font-size:10px;color:var(--green)"></span>
                </div>
              </div>
            </td>
          </tr>
          <tr>
            <td style="color:var(--muted);vertical-align:top;padding-top:8px"><span data-i18n="rec.plex_sections">Plex Sections</span></td>
            <td>
              <div style="display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap">
                <div id="plex-section-wrap" style="background:var(--surface2);border:1px solid var(--border);border-radius:6px;padding:8px;min-width:200px;min-height:36px;font-family:monospace;font-size:11px;color:var(--muted)">
                  {rec_plex_section_html}
                </div>
                <button class="btn" onclick="fetchPlexSections()" style="font-size:10px"><span data-i18n="rec.load_sections">📁 Load sections</span></button>
              </div>
              <input type="hidden" id="rec-plex-section" value="{rec_plex_section}">
            </td>
          </tr>
          <tr>
            <td style="color:var(--muted);vertical-align:top;padding-top:8px"><span data-i18n="rec.plex_verify">Verify in Plex</span></td>
            <td>
              <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text)">
                <input type="checkbox" id="rec-plex-verify" {rec_plex_verify_attr}>
                <span data-i18n="rec.plex_verify_hint">After recording/conversion, check via Plex token whether the file was actually indexed (logs result)</span>
              </label>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="flex" style="margin-top:12px;align-items:center;gap:10px">
        <button class="btn btn-primary" onclick="saveRecordingSettings()">💾 <span data-i18n="common.save">Save</span></button>
        <span id="rec-save-fb" style="font-size:12px;font-family:monospace;opacity:0;transition:opacity 0.2s"></span>
      </div>
    </div>

    <div class="card">
      <div class="card-title">📡 <span data-i18n="set.tuner_status">Tuner Status</span></div>
      <div id="tuner-status">Lade…</div>
      <button class="btn" onclick="loadTunerStatus()" style="margin-top:10px">↺ <span data-i18n="common.refresh">Refresh</span></button>
    </div>

    <div class="card">
      <div class="card-title">🔴 <span data-i18n="set.active_recordings">Active Recordings</span></div>
      <div id="rec-active" data-i18n="rec.no_active">No active recordings.</div>
      <button class="btn" onclick="loadRecordingStatus()" style="margin-top:10px">↺ <span data-i18n="common.refresh">Refresh</span></button>
    </div>

    <div class="card">
      <div class="card-title">▶ <span data-i18n="set.quick_rec">Quick Record</span></div>
      <p class="card-desc" data-i18n="set.quick_rec_hint">Select channel → current program shown → start recording.</p>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px">
        <select class="select" id="rec-ch-sel" onchange="onRecChannelChange()" style="min-width:180px">
          <option value="" data-i18n="rec.select_channel">— Select channel —</option>
        </select>
        <span id="rec-now-info" style="font-family:monospace;font-size:10px;color:var(--muted)"></span>
      </div>
      <div id="rec-now-detail" style="display:none;background:var(--surface2);border:1px solid var(--border);border-radius:6px;padding:10px;margin-bottom:12px">
        <div style="font-size:12px;font-weight:600" id="rec-now-title"></div>
        <div style="font-size:10px;color:var(--muted);margin-top:2px" id="rec-now-time"></div>
        <div style="font-size:10px;color:var(--muted);margin-top:4px" id="rec-now-desc"></div>
      </div>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <label style="font-size:11px;color:var(--muted)">Dauer:</label>
        <input class="input" id="rec-quick-dur" type="number" value="60" style="width:70px"> <span style="font-size:10px;color:var(--muted)" data-i18n="rec.min">min</span>
        <button class="btn btn-primary" id="rec-quick-btn" onclick="quickRecord()" disabled>▶ <span data-i18n="set.start_rec">Start recording</span></button>
        <span id="rec-quick-fb" style="font-family:monospace;font-size:11px;color:var(--green)"></span>
      </div>
      <div id="rec-quick-running" style="display:none;margin-top:10px">
        <span style="font-family:monospace;font-size:10px;color:var(--muted)">Recording ID: </span>
        <span id="rec-quick-id" style="font-family:monospace;font-size:10px;color:var(--accent2)"></span>
        <button class="btn btn-danger" onclick="quickStop()" style="margin-left:10px;font-size:10px;padding:3px 8px">⏹ Stop</button>
      </div>
    </div>

    <div class="card">
      <div class="card-title">🗜 <span data-i18n="comp.title">Compression</span></div>
      <p class="card-desc" data-i18n="comp.desc">Compresses .ts recordings to .mkv to save disk space. Runs during off-hours so it doesn't compete with live streaming for CPU.</p>
      <table class="table">
        <tbody>
          <tr>
            <td style="color:var(--muted);width:160px"><span data-i18n="comp.enabled">Enabled</span></td>
            <td>
              <label style="display:inline-flex;align-items:center;gap:6px;cursor:pointer">
                <input type="checkbox" id="comp-enabled" {comp_enabled_attr}>
                <span style="font-size:11px;color:var(--muted)" id="comp-window-status">—</span>
              </label>
            </td>
          </tr>
          <tr>
            <td style="color:var(--muted)"><span data-i18n="comp.profile">Profile</span></td>
            <td>
              <select class="select" id="comp-profile" style="font-size:12px;max-width:340px">
                <option value="fast" data-i18n="comp.profile_fast">Fast (H.264 veryfast, ~40% smaller)</option>
                <option value="balanced" data-i18n="comp.profile_balanced">Balanced (H.264 medium, ~55% smaller)</option>
                <option value="quality" data-i18n="comp.profile_quality">Quality (H.265 medium, ~65% smaller)</option>
              </select>
            </td>
          </tr>
          <tr>
            <td style="color:var(--muted)"><span data-i18n="comp.window">Time Window</span></td>
            <td>
              <input class="input" id="comp-window-start" type="text" value="{comp_window_start}" placeholder="01:00" style="width:80px;font-family:monospace">
              –
              <input class="input" id="comp-window-end" type="text" value="{comp_window_end}" placeholder="06:00" style="width:80px;font-family:monospace">
              <span style="font-size:10px;color:var(--muted);margin-left:8px" data-i18n="comp.window_hint">When compression may run</span>
            </td>
          </tr>
          <tr>
            <td style="color:var(--muted)"><span data-i18n="comp.delete_orig">Delete original</span></td>
            <td>
              <input type="checkbox" id="comp-delete-orig" {comp_delete_attr}>
            </td>
          </tr>
          <tr>
            <td style="color:var(--muted)"><span data-i18n="comp.cpu_limit">CPU limit</span></td>
            <td>
              <input class="input" id="comp-cpu-limit" type="number" min="0" max="100" step="5" value="{comp_cpu_limit}" style="width:80px;font-family:monospace">
              <span style="font-size:10px;color:var(--muted);margin-left:6px">%</span>
              <span style="font-size:10px;color:var(--muted);margin-left:8px" data-i18n="comp.cpu_hint">0 = unlimited · lower = gentler background load</span>
            </td>
          </tr>
          <tr>
            <td style="color:var(--muted)"><span data-i18n="comp.background">Run anytime</span></td>
            <td>
              <label style="display:inline-flex;align-items:center;gap:6px;cursor:pointer">
                <input type="checkbox" id="comp-ignore-window" {comp_ignore_window_attr}>
                <span style="font-size:10px;color:var(--muted)" data-i18n="comp.background_hint">Ignore the time window (use with a CPU limit)</span>
              </label>
            </td>
          </tr>
        </tbody>
      </table>
      <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn btn-primary" onclick="saveCompressionConfig()">💾 <span data-i18n="common.save">Save</span></button>
        <button class="btn" onclick="loadCompressionStatus()">↺ <span data-i18n="common.refresh">Refresh</span></button>
      </div>

      <!-- Backlog warning -->
      <div id="comp-backlog" style="display:none;margin-top:12px;padding:8px 12px;background:rgba(239,68,68,0.1);border-left:3px solid #ef4444;border-radius:4px;font-size:11px;color:#ef4444" data-i18n="comp.backlog_warn">⚠ Backlog forming — compression isn't keeping up</div>

      <!-- Current job -->
      <div id="comp-current" style="display:none;margin-top:12px;padding:10px;background:var(--surface2);border-radius:4px">
        <div style="font-size:10px;color:var(--muted);font-family:'JetBrains Mono',monospace;margin-bottom:4px"><span data-i18n="comp.current">Currently compressing</span></div>
        <div id="comp-current-file" style="font-family:monospace;font-size:11px;color:var(--accent)"></div>
        <div id="comp-current-meta" style="font-size:10px;color:var(--muted);margin-top:3px"></div>
        <div style="background:var(--surface3);border-radius:4px;height:10px;margin-top:8px;overflow:hidden">
          <div id="comp-current-bar" style="background:var(--accent);height:100%;width:0%;border-radius:4px;transition:width 0.5s"></div>
        </div>
        <div id="comp-current-eta" style="font-size:10px;color:var(--muted);margin-top:4px;font-family:monospace"></div>
        <div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">
          <button class="btn" id="comp-pause-btn" onclick="pauseCompression()" style="font-size:10px;padding:3px 10px">⏸ <span data-i18n="comp.pause">Pause</span></button>
          <button class="btn" id="comp-resume-btn" onclick="resumeCompression()" style="font-size:10px;padding:3px 10px;display:none">▶ <span data-i18n="comp.resume">Resume</span></button>
          <button class="btn btn-danger" id="comp-cancel-btn" onclick="cancelCompression()" style="font-size:10px;padding:3px 10px">⏹ <span data-i18n="comp.cancel">Cancel</span></button>
        </div>
      </div>

      <!-- Pending list -->
      <div style="margin-top:14px">
        <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:6px">
          <div style="font-size:10px;color:var(--muted);font-family:'JetBrains Mono',monospace"><span data-i18n="comp.pending">Pending</span>: <span id="comp-pending-count">—</span> · <span id="comp-pending-size">—</span></div>
          <label style="font-size:10px;color:var(--muted);display:inline-flex;align-items:center;gap:5px;cursor:pointer">
            <input type="checkbox" id="comp-select-all" onchange="toggleSelectAllPending(this.checked)">
            <span data-i18n="comp.select_all">Select all</span>
          </label>
          <button class="btn btn-primary" onclick="convertSelected()" style="font-size:10px;padding:3px 10px"><span data-i18n="comp.convert_selected">▶ Convert selected</span></button>
        </div>
        <div id="comp-pending-list" style="font-family:monospace;font-size:10px;max-height:180px;overflow-y:auto;border:1px solid var(--border);border-radius:4px;padding:6px;background:var(--surface2)"></div>
      </div>

      <!-- History -->
      <div style="margin-top:14px">
        <div style="font-size:10px;color:var(--muted);font-family:'JetBrains Mono',monospace;margin-bottom:6px" data-i18n="comp.history">Recent Runs</div>
        <div id="comp-history-list" style="font-family:monospace;font-size:10px;max-height:200px;overflow-y:auto;border:1px solid var(--border);border-radius:4px;padding:6px;background:var(--surface2)"></div>
      </div>
    </div>

  </div>

  <!-- ── TAB: KONFIGURATION ─────────────────────────── -->
  <div class="tab-panel active" id="tab-config">

    <div class="card">
      <div class="card-title">📡 <span data-i18n="set.receivers_card">Receivers</span></div>
      <table class="table" id="rx-table">
        <thead><tr><th>ID</th><th>Name</th><th>IP</th><th>Port</th><th>Stream-Port</th><th>Status</th><th>Default</th><th></th></tr></thead>
        <tbody id="rx-tbody">{rx_rows}</tbody>
      </table>
      <div class="flex" style="margin-top:10px">
        <button class="btn btn-primary" onclick="addReceiver()">+ Receiver hinzufügen</button>
      </div>
    </div>

    <div class="card">
      <div class="card-title">🎬 <span data-i18n="set.transcode_card">Transcode Profiles</span></div>
      <table class="table" id="tp-table">
        <thead><tr><th>ID</th><th>Label</th><th>Codec</th><th>Container</th><th>Bitrate</th><th>Auflösung</th><th></th></tr></thead>
        <tbody id="tp-tbody">{tp_rows}</tbody>
      </table>
      <div class="flex" style="margin-top:10px">
        <button class="btn btn-primary" onclick="addTranscodeProfile()">+ Profil hinzufügen</button>
      </div>
    </div>

    <div class="card">
      <div class="card-title">📺 <span data-i18n="set.device_card">Device Profiles</span></div>
      <table class="table" id="dp-table">
        <thead><tr><th>ID</th><th>Label</th><th>Transcode</th><th>Receiver</th><th>Kurz-URL</th><th>Playlist</th><th></th></tr></thead>
        <tbody id="dp-tbody">{dp_rows}</tbody>
      </table>
      <div class="flex" style="margin-top:10px">
        <button class="btn btn-primary" onclick="addDeviceProfile()">+ Device-Profil hinzufügen</button>
      </div>
    </div>

    <div class="card">
      <div class="card-title">🌐 <span data-i18n="set.language">Language</span></div>
      <p class="card-desc" data-i18n="set.lang_hint">Interface language. Applies immediately.</p>
      <div style="display:flex;gap:10px;align-items:center">
        <select class="select" id="lang-sel" onchange="setLang(this.value)" style="font-size:12px;max-width:200px">
          <option value="en">🇬🇧 English</option>
          <option value="de">🇩🇪 Deutsch</option>
        </select>
      </div>
    </div>

    <div class="card">
      <div class="card-title">🔑 <span data-i18n="set.api_keys">API Keys &amp; Tokens</span></div>
      <p class="card-desc" data-i18n="set.api_keys_hint">API keys for external services. Stored securely in the config.</p>
      <table class="table">
        <tbody>
          <tr>
            <td style="color:var(--muted);width:140px;font-size:11px">TMDB API-Key</td>
            <td>
              <input class="input" id="tmdb-key" type="password"
                value="{tmdb_api_key}"
                placeholder="TMDB API Key (kostenlos auf themoviedb.org)"
                style="width:100%;max-width:420px;font-family:monospace;font-size:11px">
            </td>
            <td style="width:120px">
              <button class="btn btn-primary" onclick="saveApiKey('tmdb_api_key','tmdb-key')" style="font-size:10px">💾 <span data-i18n="common.save">Save</span></button>
            </td>
          </tr>
          <tr>
            <td style="color:var(--muted);width:140px;font-size:11px">TVDB API-Key</td>
            <td>
              <input class="input" id="tvdb-key" type="password"
                value="{tvdb_api_key}"
                placeholder="TVDB API Key (kostenlos auf thetvdb.com — für Serien-Erkennung mit echten S/E)"
                style="width:100%;max-width:420px;font-family:monospace;font-size:11px">
            </td>
            <td style="width:120px">
              <button class="btn btn-primary" onclick="saveApiKey('tvdb_api_key','tvdb-key')" style="font-size:10px">💾 <span data-i18n="common.save">Save</span></button>
            </td>
          </tr>
          <tr>
            <td style="color:var(--muted);font-size:11px">e2recorder URL</td>
            <td>
              <input class="input" id="recorder-url"
                value="{recorder_url}"
                placeholder="http://localhost:8889 (leer = kein Announce)"
                style="width:100%;max-width:420px;font-family:monospace;font-size:11px">
            </td>
            <td style="width:120px">
              <button class="btn btn-primary" onclick="saveApiKey('recorder_url','recorder-url')" style="font-size:10px">💾 <span data-i18n="common.save">Save</span></button>
            </td>
          </tr>
          <tr>
            <td style="color:var(--muted);font-size:11px">Log Retention</td>
            <td>
              <div style="display:flex;align-items:center;gap:8px">
                <input class="input" id="log-retention" type="number" min="1" max="365"
                  value="{log_retention_days}"
                  style="width:70px;font-family:monospace;font-size:11px">
                <span style="font-size:11px;color:var(--muted)">Tage (Log-Dateien in <code>/data/e2proxy.log</code>)</span>
              </div>
            </td>
            <td style="width:120px">
              <button class="btn btn-primary" onclick="saveApiKey('log_retention_days','log-retention')" style="font-size:10px">💾 <span data-i18n="common.save">Save</span></button>
            </td>
          </tr>
          <tr>
            <td style="color:var(--muted);font-size:11px">Stream-Diagnose</td>
            <td>
              <label style="font-size:11px;display:flex;align-items:center;gap:8px">
                <input type="checkbox" id="diag-logging" {diag_enabled_attr} onchange="toggleDiag(this.checked)">
                <span>Erweitertes Diagnose-Logging (ffmpeg <code>verbose</code> + Durchsatz-Samples alle 15 s)</span>
              </label>
              <div style="font-size:10px;color:var(--muted);margin-top:4px">
                Stillstände (&gt;3 s ohne Daten) werden <b>immer</b> als <code>DIAG event=stall_start</code> protokolliert.
                Dieser Schalter erhöht nur die Detailtiefe — bei Bedarf einschalten, danach wieder aus (Log-Volumen).
              </div>
              <div id="diag-live" style="font-size:10px;color:var(--muted);margin-top:6px;font-family:monospace"></div>
            </td>
            <td style="width:120px">
              <button class="btn" onclick="refreshDiag()" style="font-size:10px">🔄 Status</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="card">
      <div class="card-title">📺 OpenWebif-Emulation <span style="color:var(--muted);font-size:9px;font-weight:400;margin-left:8px;">DREAM PLAYER / KODI</span></div>
      <p class="card-desc">e2proxy imitiert eine Enigma2-Box auf Standardports (OpenWebif <code>:80</code>, Stream <code>:8001</code>). Enigma2-Apps wie <b>Dream Player</b> (Android/Google TV) oder Kodi zeigen einfach auf e2proxy und glauben, mit einer echten Box zu reden. Metadaten/EPG kommen authentisch vom Receiver, Streaming wird orchestriert (freier Tuner) und standardmäßig als rohes TS durchgereicht (Passthrough, kein ffmpeg). <b>Plex bleibt unberührt.</b></p>
      <table>
        <tbody>
          <tr>
            <td style="color:var(--muted);width:160px;font-size:11px">Aktiviert</td>
            <td><label style="font-size:11px"><input type="checkbox" id="owif-enabled" {owif_enabled_attr}> Emulation aktiv</label></td>
          </tr>
          <tr>
            <td style="color:var(--muted);font-size:11px">Bind-Adresse</td>
            <td><input class="input" id="owif-bind" value="{owif_bind}" style="width:180px;font-family:monospace;font-size:11px"> <span style="font-size:10px;color:var(--muted)">0.0.0.0 = alle; oder dedizierte IP</span></td>
          </tr>
          <tr>
            <td style="color:var(--muted);font-size:11px">OpenWebif-Port</td>
            <td><input class="input" id="owif-webif-port" type="number" min="1" max="65535" value="{owif_webif_port}" style="width:90px;font-family:monospace;font-size:11px"> <span style="font-size:10px;color:var(--muted)">Enigma-Standard: 80</span></td>
          </tr>
          <tr>
            <td style="color:var(--muted);font-size:11px">Streaming-Port</td>
            <td><input class="input" id="owif-stream-port" type="number" min="1" max="65535" value="{owif_stream_port}" style="width:90px;font-family:monospace;font-size:11px"> <span style="font-size:10px;color:var(--muted)">Enigma-Standard: 8001</span></td>
          </tr>
          <tr>
            <td style="color:var(--muted);font-size:11px">Aufnahmen (Dream Player)</td>
            <td><label style="font-size:11px"><input type="checkbox" id="owif-recordings-enabled" {owif_recordings_attr}> Movielist bereitstellen</label></td>
          </tr>
          <tr>
            <td style="color:var(--muted);font-size:11px">Aufnahmen-Port</td>
            <td><input class="input" id="owif-recordings-port" type="number" min="0" max="65535" value="{owif_recordings_port}" style="width:90px;font-family:monospace;font-size:11px"> <span style="font-size:10px;color:var(--muted)">Enigma-Standard: 81 (0 = aus)</span></td>
          </tr>
          <tr>
            <td style="color:var(--muted);font-size:11px">Standard-Profil</td>
            <td><select class="input" id="owif-default-profile" style="width:180px;font-size:11px">{owif_profile_options}</select> <span style="font-size:10px;color:var(--muted)">pass = rohes TS</span></td>
          </tr>
          <tr>
            <td style="color:var(--muted);font-size:11px;vertical-align:top">Player-Overrides<br>(User-Agent)</td>
            <td>
              <textarea class="input" id="owif-ua" placeholder="ExoPlayer =&gt; remux-ac3&#10;VLC =&gt; pass" style="width:100%;max-width:420px;height:70px;font-family:monospace;font-size:11px">{owif_ua_text}</textarea>
              <div style="font-size:10px;color:var(--muted)">Je Zeile: <code>User-Agent-Text =&gt; Profil-ID</code>. Erster Treffer (case-insensitive) gewinnt. Für Player, die kein rohes TS können.</div>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="flex">
        <button class="btn btn-primary" onclick="saveOwif()">💾 <span data-i18n="common.save">Save</span></button>
        <span id="owif-fb" style="font-size:11px;color:var(--muted)"></span>
      </div>
      <p class="card-desc" style="margin-top:8px;font-size:10px">⚠ Port-Änderungen erfordern einen Service-Neustart. Standardports (80/8001) setzen voraus, dass sie auf dem Host frei sind.</p>
    </div>

    <div class="card">
      <div class="card-title">⚙ <span data-i18n="set.config_editor">Config Editor</span> <span style="color:var(--muted);font-size:9px;font-weight:400;margin-left:8px;">ADVANCED</span></div>
      <p class="card-desc" data-i18n="set.config_editor_hint">Direct editing of the full configuration as JSON. For advanced settings.</p>
      <textarea class="input" id="config-json">{cfg_json}</textarea>
      <div class="flex">
        <button class="btn btn-primary" onclick="saveConfig()">💾 <span data-i18n="common.save">Save</span></button>
        <button class="btn" onclick="resetConfig()">↺ <span data-i18n="set.reset">Reset</span></button>
      </div>
    </div>

  </div>

  <!-- ── TAB: AUSWERTUNG / USAGE ──────────────────── -->
  <div class="tab-panel" id="tab-usage">

    <div class="card">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap">
        <div class="card-title" style="margin:0">📊 <span data-i18n="usage.title">Nutzungs-Auswertung</span></div>
        <div style="display:flex;gap:8px">
          <button class="btn" onclick="loadUsage()">↺ <span data-i18n="common.refresh">Refresh</span></button>
          <button class="btn" onclick="resetUsage()">🗑 <span data-i18n="usage.reset">Zurücksetzen</span></button>
        </div>
      </div>
      <p class="card-desc" data-i18n="usage.hint">Aggregierte Nutzung seit Aufzeichnungsbeginn: welches Gerät streamt am meisten, welcher Sender wird am häufigsten geschaut, welche Schnittstellen werden verwendet.</p>
      <div id="usage-totals" style="margin-top:10px">Lade…</div>
    </div>

    <div class="card">
      <div class="card-title">📺 <span data-i18n="usage.top_channels">Meistgeschaute Sender</span></div>
      <div id="usage-channels" style="overflow-x:auto">Lade…</div>
    </div>

    <div class="card">
      <div class="card-title">💻 <span data-i18n="usage.top_devices">Geräte-Nutzung</span></div>
      <div id="usage-devices" style="overflow-x:auto">Lade…</div>
    </div>

    <div class="card">
      <div class="card-title">⏺ <span data-i18n="usage.rec_suggest">Aufnahme-Vorschläge</span></div>
      <p class="card-desc" data-i18n="usage.rec_suggest_hint">Sendungen, die mehrfach geschaut wurden — eine Aufnahme (bzw. Serien-Timer) lohnt sich hier vermutlich.</p>
      <div id="usage-programs" style="overflow-x:auto">Lade…</div>
    </div>

    <div class="card">
      <div class="card-title">🔌 <span data-i18n="usage.interfaces">Schnittstellen-Nutzung</span></div>
      <div id="usage-interfaces" style="overflow-x:auto">Lade…</div>
    </div>

    <div class="card">
      <div class="card-title">🕒 <span data-i18n="usage.by_hour">Nutzung nach Tageszeit</span></div>
      <div id="usage-hours" style="overflow-x:auto">Lade…</div>
    </div>

    <div class="card">
      <div class="card-title">⚡ <span data-i18n="set.switch_tuning">Umschalt-Tuning</span></div>
      <p class="card-desc" data-i18n="set.switch_hint">Beschleunigt das Umschalten (z.B. Plex). NoLatency startet ffmpeg mit minimalem Probing; die Zap-Wartezeit ist die Pause nach dem Umschalten. Werte gelten global als Default — pro Sender wird automatisch gelernt (siehe Tabelle).</p>
      <table style="font-size:12px;border-collapse:collapse">
        <tbody>
          <tr>
            <td style="color:var(--muted);padding:6px 12px 6px 0"><span data-i18n="set.switch_nolatency">NoLatency (global)</span></td>
            <td>
              <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text)">
                <input type="checkbox" id="sw-no-latency">
                <span data-i18n="set.switch_nolatency_hint">ffmpeg ohne großes Probing starten (schneller, lernt bei Fehlern automatisch hoch)</span>
              </label>
            </td>
          </tr>
          <tr>
            <td style="color:var(--muted);padding:6px 12px 6px 0"><span data-i18n="set.switch_zapwait">Zap-Wartezeit (s)</span></td>
            <td><input class="input" id="sw-zap-wait" type="number" step="0.1" min="0" style="width:100px"></td>
          </tr>
          <tr>
            <td style="color:var(--muted);padding:6px 12px 6px 0"><span data-i18n="set.switch_monitor">Monitor-Fenster (s)</span></td>
            <td><input class="input" id="sw-monitor" type="number" step="0.5" min="1" style="width:100px"></td>
          </tr>
          <tr>
            <td style="color:var(--muted);padding:6px 12px 6px 0"><span data-i18n="set.switch_retries">Max. Neustarts</span></td>
            <td><input class="input" id="sw-retries" type="number" step="1" min="0" style="width:100px"></td>
          </tr>
          <tr>
            <td style="color:var(--muted);padding:6px 12px 6px 0"><span data-i18n="set.switch_nolat_probe">NoLatency Probesize</span></td>
            <td><input class="input" id="sw-nolat-probe" type="number" step="100000" min="32" style="width:140px"></td>
          </tr>
          <tr>
            <td style="color:var(--muted);padding:6px 12px 6px 0"><span data-i18n="set.switch_fail_thresh">Fehler-Schwelle (Probesize↑)</span></td>
            <td><input class="input" id="sw-fail-thresh" type="number" step="1" min="1" style="width:100px"></td>
          </tr>
        </tbody>
      </table>
      <div class="flex" style="margin-top:12px;align-items:center;gap:10px">
        <button class="btn btn-primary" onclick="saveSwitchGlobal()">💾 <span data-i18n="common.save">Save</span></button>
        <span id="sw-save-fb" style="font-size:12px;font-family:monospace;opacity:0;transition:opacity 0.2s"></span>
      </div>

      <div style="margin-top:18px;display:flex;align-items:center;justify-content:space-between;gap:10px">
        <div class="card-title" style="margin:0;font-size:13px">📊 <span data-i18n="set.switch_stats">Per-Sender-Statistik</span></div>
        <div style="display:flex;gap:8px">
          <button class="btn" onclick="analyzeSwitch()">🔍 <span data-i18n="set.switch_analyze">Analyze</span></button>
          <button class="btn" onclick="loadSwitchStats()">↺ <span data-i18n="common.refresh">Refresh</span></button>
          <button class="btn" onclick="resetSwitch(null)">🗑 <span data-i18n="set.switch_reset_all">Reset alle</span></button>
        </div>
      </div>
      <div id="sw-analyze-box" style="margin-top:10px"></div>
      <div id="sw-stats" style="margin-top:10px;overflow-x:auto">Lade…</div>
    </div>

  </div>

  <!-- ── TAB: WARTUNG ──────────────────────────────── -->
  <div class="tab-panel" id="tab-maintenance">

    <div class="card">
      <div class="card-title">📡 <span data-i18n="sm.active_streams">Active streams</span>
        <button class="btn" style="margin-left:auto;font-size:10px" onclick="loadStreamInfo()">↺ <span data-i18n="common.refresh">Refresh</span></button>
      </div>
      <div id="stream-info-list" style="display:flex;flex-direction:column;gap:8px">
        <span style="color:var(--muted);font-size:11px;font-family:monospace" data-i18n="common.loading">Loading…</span>
      </div>
    </div>

    <div class="card">
      <div class="card-title">🩺 <span data-i18n="if.title">Interface status</span>
        <button class="btn" style="margin-left:auto;font-size:10px" onclick="checkInterfaces()">↺ <span data-i18n="if.check_now">Check now</span></button>
      </div>
      <div style="color:var(--muted);font-size:11px;margin-bottom:8px" data-i18n="if.hint">Probes the Plex (HDHomeRun) and DreamBox app (OpenWebif) interfaces every 60 s. Probe requests are not counted in usage statistics.</div>
      <div id="if-summary" style="font-size:12px;margin-bottom:6px"></div>
      <div style="overflow-x:auto"><table style="width:100%;font-size:11px;border-collapse:collapse" id="if-table"><tbody><tr><td style="color:var(--muted)" data-i18n="common.loading">Loading…</td></tr></tbody></table></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;align-items:center">
        <button class="btn" onclick="streamTestInterface('plex')">▶ <span data-i18n="if.test_plex">Stream test Plex</span></button>
        <button class="btn" onclick="streamTestInterface('openwebif')">▶ <span data-i18n="if.test_owif">Stream test OpenWebif</span></button>
        <span class="action-feedback" id="if-fb"></span>
      </div>
    </div>

    <div class="card">
      <div class="card-title">🔧 <span data-i18n="set.maint_actions">Maintenance Actions</span></div>
      <div class="action-grid">

        <div class="action-card">
          <div class="action-card-title">🖼 <span data-i18n="set.update_logos_card">Update logos</span></div>
          <div class="action-card-desc" data-i18n="set.update_logos_hint">Reloads all channel logos into the local cache.</div>
          <div class="action-feedback" id="fb-logos"></div>
          <button class="btn" onclick="doAction('/logos/refresh', 'fb-logos', 'Logos werden geladen…', 'Logos aktualisiert ✓')"><span data-i18n="maint.execute">Execute</span></button>
        </div>

        <div class="action-card">
          <div class="action-card-title">📋 <span data-i18n="set.reload_channels">Reload channel list</span></div>
          <div class="action-card-desc" data-i18n="set.reload_channels_hint">Reloads all channels and bouquets from the receiver.</div>
          <div class="action-feedback" id="fb-channels"></div>
          <button class="btn" onclick="reloadChannels()"><span data-i18n="maint.execute">Execute</span></button>
        </div>

        <div class="action-card">
          <div class="action-card-title">🔄 <span data-i18n="set.restart_service">Restart service</span></div>
          <div class="action-card-desc" data-i18n="set.restart_hint">Restarts the e2proxy service. Active streams will be interrupted.</div>
          <div class="action-feedback" id="fb-restart"></div>
          <button class="btn btn-danger" onclick="restartService()"><span data-i18n="maint.restart_btn">Restart</span></button>
        </div>

      </div>
    </div>

    <div class="card">
      <div class="card-title">📡 <span data-i18n="set.maint_notify">Maintenance Notifications</span></div>
      <p class="card-desc" data-i18n="set.maint_notify_hint">Sends an HTTP call to another system at a scheduled time, e.g. to trigger maintenance tasks. Optionally only when idle (no recording running and nobody watching).</p>

      <label style="display:flex;align-items:center;gap:8px;margin-bottom:14px;cursor:pointer">
        <input type="checkbox" id="mn-enabled">
        <span data-i18n="set.maint_notify_enable">Enable maintenance notifications</span>
      </label>

      <div class="field">
        <label class="label" data-i18n="set.maint_notify_url">Target URL</label>
        <div class="flex">
          <select class="select" id="mn-method" style="width:90px">
            <option value="POST">POST</option>
            <option value="GET">GET</option>
          </select>
          <input type="text" class="input" id="mn-url" placeholder="https://example.com/hook" style="flex:1">
        </div>
      </div>

      <div class="field" style="margin-top:12px">
        <label class="label" data-i18n="set.maint_notify_time">Time</label>
        <div class="flex" style="align-items:center">
          <input type="number" min="0" max="23" class="input" id="mn-hour" style="width:70px;text-align:center" value="4">
          <span style="font-family:'JetBrains Mono',monospace;color:var(--muted)">:</span>
          <input type="number" min="0" max="59" class="input" id="mn-minute" style="width:70px;text-align:center" value="0">
          <span style="font-family:'JetBrains Mono',monospace;color:var(--muted)" data-i18n="epg.oclock">h</span>
        </div>
      </div>

      <div class="field" style="margin-top:12px">
        <label class="label" data-i18n="set.maint_notify_days">Weekdays</label>
        <div id="mn-days" style="display:flex;gap:6px;flex-wrap:wrap"></div>
      </div>

      <div class="field" style="margin-top:12px">
        <label class="label" data-i18n="set.maint_notify_idle">Trigger condition</label>
        <select class="select" id="mn-idle" style="max-width:340px">
          <option value="always" data-i18n="set.maint_notify_idle_always">Always at the scheduled time</option>
          <option value="idle_only" data-i18n="set.maint_notify_idle_only">Only when idle (no recording / nobody watching)</option>
        </select>
      </div>

      <div class="flex" style="margin-top:16px">
        <button class="btn btn-primary" onclick="saveMaintNotify()">💾 <span data-i18n="common.save">Save</span></button>
        <button class="btn" onclick="testMaintNotify()">🚀 <span data-i18n="set.maint_notify_test">Send test</span></button>
        <span class="action-feedback" id="mn-fb"></span>
      </div>
    </div>

    <div class="card">
      <div class="card-title">🚨 <span data-i18n="set.rx_alert">Receiver Outage Alert</span></div>
      <p class="card-desc" data-i18n="set.rx_alert_hint">If a receiver is unreachable for longer than the configured minutes, an alert is logged and optionally an HTTP call is sent.</p>

      <label style="display:flex;align-items:center;gap:8px;margin-bottom:14px;cursor:pointer">
        <input type="checkbox" id="ra-enabled">
        <span data-i18n="set.rx_alert_enable">Send alert webhook</span>
      </label>

      <div class="field">
        <label class="label" data-i18n="set.maint_notify_url">Target URL</label>
        <div class="flex">
          <select class="select" id="ra-method" style="width:90px">
            <option value="POST">POST</option>
            <option value="GET">GET</option>
          </select>
          <input type="text" class="input" id="ra-url" placeholder="https://example.com/hook" style="flex:1">
        </div>
      </div>

      <div class="field" style="margin-top:12px">
        <label class="label" data-i18n="set.rx_alert_after">Alert after (minutes)</label>
        <input type="number" min="1" max="1440" class="input" id="ra-after" style="width:90px;text-align:center" value="10">
      </div>

      <div class="flex" style="margin-top:16px">
        <button class="btn btn-primary" onclick="saveRxAlerts()">💾 <span data-i18n="common.save">Save</span></button>
        <button class="btn" onclick="testRxAlerts()">🚀 <span data-i18n="set.maint_notify_test">Send test</span></button>
        <span class="action-feedback" id="ra-fb"></span>
      </div>
    </div>

    <div class="card">
      <div class="card-title">📋 Live Logs
        <div style="margin-left:auto;display:flex;gap:6px;align-items:center">
          <select class="select" id="log-hours" style="font-size:10px;padding:2px 6px">
            <option value="1">1h</option>
            <option value="6" selected>6h</option>
            <option value="12">12h</option>
            <option value="24">1 Tag</option>
            <option value="48">2 Tage</option>
            <option value="120">5 Tage</option>
            <option value="all">Alle</option>
          </select>
          <button class="btn" style="font-size:10px" onclick="loadLogHistory()" title="Historie aus File-Logs nachladen (oben anfügen)">⬆ Reload</button>
          <button class="btn" style="font-size:10px" onclick="refreshLogs()" title="Live-Modus neu starten">↺ Live</button>
        </div>
      </div>
      <div class="log-box" id="log-box">Logs werden geladen…</div>
    </div>

    <div class="card">
      <div class="card-title">🖼 <span data-i18n="set.fav_logos">Favorite logos</span></div>
      <p class="card-desc" data-i18n="set.fav_logos_hint">Set a custom logo for each favorite — upload an image or enter a URL. The image is converted to the correct format automatically. Reset falls back to the automatic logo.</p>
      <div class="flex" style="margin-bottom:12px">
        <button class="btn" onclick="loadFavLogos()">↻ <span data-i18n="common.refresh">Refresh</span></button>
        <span class="action-feedback" id="fav-logo-fb"></span>
      </div>
      <div id="fav-logo-list" style="display:flex;flex-direction:column;gap:8px">
        <span style="color:var(--muted);font-size:11px;font-family:monospace" data-i18n="common.loading">Loading…</span>
      </div>
    </div>

    <div id="logo-modal" onclick="closeLogoModal()" style="display:none;position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.8);align-items:center;justify-content:center;flex-direction:column;gap:12px;cursor:zoom-out">
      <img id="logo-modal-img" src="" alt="" style="max-width:80vw;max-height:75vh;object-fit:contain;background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:10px;box-shadow:0 8px 40px rgba(0,0,0,0.6)">
      <div id="logo-modal-name" style="color:#fff;font-family:monospace;font-size:13px"></div>
    </div>

  </div>

  <!-- ── TAB: EPG ───────────────────────────────────── -->
  <div class="tab-panel" id="tab-epg">

    <div class="card">
      <div class="card-title">📅 <span data-i18n="set.epg_update">EPG Update</span></div>
      <p class="card-desc">
        <span data-i18n="epg.update_hint">Fetches the program guide from both receivers, loads missing channels via zap and adds from the online source (Rytec). Runs daily automatically or manually.</span>
      </p>
      <div class="flex">
        <button class="btn btn-primary" id="epg-run-btn" onclick="startEpgRun()">▶ <span data-i18n="set.epg_run_now_btn">Update now</span></button>
        <span class="action-feedback" id="epg-run-fb"></span>
      </div>

      <div style="margin-top:16px;">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
          <span class="label"><span data-i18n="epg.progress">Progress:</span> <span id="epg-phase">—</span></span>
          <span id="epg-tmdb-counter" style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--muted)"></span>
        </div>
        <div style="background:var(--surface2);border:1px solid var(--border);border-radius:6px;height:22px;overflow:hidden;position:relative;">
          <div id="epg-bar-receiver" style="background:var(--green);height:100%;width:0%;transition:width 0.4s;position:absolute;left:0;top:0;"></div>
          <div id="epg-bar-tmdb" style="background:var(--accent);height:100%;width:0%;transition:width 0.4s;position:absolute;left:0;top:0;"></div>
          <span id="epg-bar-text" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-family:'JetBrains Mono',monospace;font-size:10px;">0%</span>
        </div>
        <div style="display:flex;gap:12px;margin-top:4px;font-family:monospace;font-size:9px;color:var(--muted)">
          <span>🟢 Receiver (0-20%)</span>
          <span>🟣 TMDB Artwork (20-100%)</span>
        </div>
      </div>

      <!-- TMDB Lookup Liste -->
      <div id="epg-tmdb-list-wrap" style="display:none;margin-top:12px;">
        <div style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--muted);margin-bottom:6px;">
          🎬 TMDB Lookups
        </div>
        <div id="epg-tmdb-list" style="background:var(--surface2);border:1px solid var(--border);border-radius:6px;padding:8px;font-family:'JetBrains Mono',monospace;font-size:10px;line-height:1.8;max-height:180px;overflow-y:auto;">
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">📡 <span data-i18n="epg.plex_dvr_title">Plex DVR Guide</span></div>
      <p class="card-desc" data-i18n="epg.plex_dvr_desc">Reloads the guide of the e2proxy DVR in Plex so new EPG data is picked up. Requires Plex URL + token (configured under Recordings). Only the e2proxy DVR is touched.</p>
      <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text);margin-bottom:12px">
        <input type="checkbox" id="epg-plex-dvr-refresh" {rec_plex_dvr_refresh_attr} onchange="savePlexDvrRefresh(this.checked)">
        <span data-i18n="epg.plex_dvr_auto">Automatically reload the Plex guide after each EPG run</span>
      </label>
      <div class="flex">
        <button class="btn btn-primary" id="plex-dvr-refresh-btn" onclick="triggerPlexDvrRefresh()">🔄 <span data-i18n="epg.plex_dvr_now">Update guide now</span></button>
        <span class="action-feedback" id="plex-dvr-refresh-fb"></span>
      </div>
    </div>

    <div class="card">
      <div class="card-title">⏰ <span data-i18n="set.schedule">Schedule</span></div>
      <p class="card-desc" data-i18n="set.schedule_hint">Time for the daily automatic EPG run. Should be before the Plex fetch (4-5 AM).</p>
      <div class="flex">
        <input type="number" min="0" max="23" class="input" id="epg-hour" style="width:80px;text-align:center;font-size:16px;" value="3">
        <span style="font-family:'JetBrains Mono',monospace;color:var(--muted);">:00 <span data-i18n="epg.oclock">h</span></span>
        <button class="btn" onclick="saveEpgSchedule()">💾 <span data-i18n="common.save">Save</span></button>
        <span class="action-feedback" id="epg-sched-fb"></span>
      </div>
    </div>

    <div class="card">
      <div class="card-title">📊 <span data-i18n="set.last_run">Last Run</span></div>
      <table class="table">
        <tbody>
          <tr><td style="color:var(--muted)"><span data-i18n="set.timestamp">Time</span></td><td id="epg-last-run">—</td></tr>
          <tr><td style="color:var(--muted)"><span data-i18n="set.duration">Duration</span></td><td id="epg-last-dur">—</td></tr>
          <tr><td style="color:var(--muted)"><span data-i18n="set.result">Result</span></td><td id="epg-last-result">—</td></tr>
        </tbody>
      </table>
    </div>

    <div class="card">
      <div class="card-title">📈 <span data-i18n="set.run_history">Run History (last 30)</span></div>
      <p class="card-desc" style="margin-bottom:10px" data-i18n="set.run_history_hint">Bars = duration in seconds. Red = outlier (>2x average).</p>
      <div id="epg-history-chart" style="min-height:80px">
        <span style="color:var(--muted);font-size:11px;font-family:monospace">Lade…</span>
      </div>
    </div>

    <div class="card">
      <div class="card-title">📋 <span data-i18n="set.epg_run_log">EPG Run Log</span></div>
      <div class="log-box" id="epg-log">Noch kein Run ausgeführt.</div>
    </div>

    <div class="card">
      <div class="card-title">🖼 <span data-i18n="set.logo_check_card">Logo Check</span></div>
      <p class="card-desc" data-i18n="set.logo_check_hint">Checks whether the favorite channel logos are reachable.</p>
      <div class="flex">
        <button class="btn" onclick="checkLogos()">🔍 Logos prüfen</button>
        <span class="action-feedback" id="logo-check-fb"></span>
      </div>
      <div id="logo-broken" style="margin-top:10px;"></div>
    </div>

  </div>

  <!-- ── TAB: HILFE ─────────────────────────────────── -->
  <div class="tab-panel" id="tab-help">

    <div class="card">
      <div class="card-title">🎨 <span data-i18n="set.appearance">Appearance</span></div>
      <p class="card-desc" data-i18n="set.appearance_hint">Switch between dark and light theme. Stored in the browser.</p>
      <button id="theme-toggle-btn" onclick="toggleTheme()" style="font-size:14px;padding:8px 16px;border-radius:6px;border:1px solid var(--border);background:var(--surface2);color:var(--text);cursor:pointer;font-family:monospace;">
        ☀ / 🌙 Theme wechseln
      </button>
    </div>

    <div class="card">
      <div class="card-title">📋 <span data-i18n="set.log_level">Log Level</span></div>
      <p class="card-desc" data-i18n="set.log_level_hint">Controls which log entries are shown. Applies immediately without restart. The RAM buffer always keeps the last 500 entries.</p>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <select class="select" id="log-level-sel" onchange="setLogLevel(this.value)" style="font-size:12px">
          <option value="DEBUG">🔍 DEBUG — Alles (inkl. SSDP, TMDB Details)</option>
          <option value="INFO" selected>ℹ INFO — Normal (EPG, Streams, Events)</option>
          <option value="WARNING">⚠ WARNING — Nur Warnungen + Fehler</option>
          <option value="ERROR">❌ ERROR — Nur Fehler</option>
        </select>
        <span id="log-level-fb" style="font-family:monospace;font-size:11px;color:var(--green)"></span>
      </div>
      <p class="card-desc" style="margin-top:8px;font-size:10px">
        <span data-i18n="log.tip" data-i18n-html="1">Tip: For normal operation we recommend <b>INFO</b> or <b>WARNING</b> — DEBUG logs a lot (SSDP requests etc.)</span>
      </p>
    </div>

    <div class="card">
      <div class="card-title">🔌 API Access Log
        <label style="margin-left:auto;display:flex;align-items:center;gap:8px;font-weight:400;cursor:pointer">
          <span style="font-size:10px;color:var(--muted)" id="api-log-status">…</span>
          <div id="api-log-toggle" onclick="toggleApiLogging()"
            style="width:36px;height:20px;border-radius:10px;background:var(--border);cursor:pointer;position:relative;transition:background 0.2s">
            <div id="api-log-thumb" style="width:16px;height:16px;border-radius:50%;background:white;position:absolute;top:2px;left:2px;transition:left 0.2s"></div>
          </div>
        </label>
      </div>
      <p class="card-desc"><span data-i18n="apilog.hint">Logs all API requests persistently in</span> <code>/data/api_access.log</code>.</p>
      <div id="api-log-table-wrap" style="display:none;margin-top:10px">
        <div style="display:flex;gap:6px;margin-bottom:8px;align-items:center;flex-wrap:wrap">
          <select class="select" id="api-log-hours" style="font-size:10px;padding:2px 6px">
            <option value="1">1h</option>
            <option value="6" selected>6h</option>
            <option value="12">12h</option>
            <option value="24">1 Tag</option>
            <option value="48">2 Tage</option>
            <option value="120">5 Tage</option>
            <option value="all">Alle</option>
          </select>
          <button class="btn" onclick="loadAccessLogHistory()" style="font-size:10px" title="Historie aus File nachladen">⬆ Reload</button>
          <button class="btn" onclick="loadAccessLog()" style="font-size:10px" title="Live neu starten">↺ Live</button>
          <button class="btn btn-danger" onclick="clearAccessLog()" style="font-size:10px">🗑 Leeren</button>
          <span style="font-size:10px;color:var(--muted);margin-left:4px">grün=OK, amber=langsam >1s, rot=Fehler</span>
        </div>
        <div class="log-box" id="api-log-box" style="height:300px"></div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">🔗 <span data-i18n="set.api_ref">API Reference</span></div>
      <table class="table" id="api-table">
        <tbody>{api_rows_html}</tbody>
      </table>
    </div>

    <div class="card">
      <div class="card-title">ℹ <span data-i18n="set.about">About e2proxy</span></div>
      <div class="card-desc" style="line-height:2.2;font-family:'JetBrains Mono',monospace;font-size:11px">
        <span style="color:var(--muted);font-size:10px">VERSION&nbsp;&nbsp;</span> <span style="color:var(--accent)">{version}</span><br>
        <span style="color:var(--muted);font-size:10px">BUILD&nbsp;&nbsp;&nbsp;&nbsp;</span> <span style="color:var(--accent2)" title="Interne Build-Kennung — wechselt bei jedem Test-Rollout">{INTERNAL_VERSION}</span><br>
        <span style="color:var(--muted);font-size:10px">PROXY&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</span> http://{proxy_host}:{proxy_port}<br>
        <span style="color:var(--muted);font-size:10px">PLEX DVR&nbsp;</span> http://{proxy_host}:{proxy_port}/plex<br>
        <span style="color:var(--muted);font-size:10px">EPG&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</span> http://{proxy_host}:{proxy_port}/epg.xml
      </div>
    </div>

  </div>

</div>

<script>
const ORIGINAL_CONFIG = {cfg_json};

// ── Usage / Auswertung ──────────────────────────────────
// Eigene Helfer, damit dieser Tab nicht von Funktionen anderer Seiten abhaengt.
function _uEsc(s) {{
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}}
function _uBytes(b) {{
  b = Number(b) || 0;
  if (b >= 1024*1024*1024) return (b/(1024*1024*1024)).toFixed(2)+' GB';
  if (b >= 1024*1024) return (b/(1024*1024)).toFixed(1)+' MB';
  if (b >= 1024) return (b/1024).toFixed(1)+' KB';
  return b+' B';
}}
function _uBar(pct) {{
  const w = Math.max(0, Math.min(100, pct || 0));
  return `<div style="background:var(--border);border-radius:3px;height:6px;width:80px;display:inline-block;vertical-align:middle">`
       + `<div style="background:var(--accent);height:6px;border-radius:3px;width:${{w}}%"></div></div>`;
}}
function _uEmpty(msg) {{
  return `<div style="color:var(--muted);font-size:12px">${{msg}}</div>`;
}}
function _uHead(cols) {{
  let h = '<thead><tr style="text-align:left;color:var(--muted);border-bottom:1px solid var(--border)">';
  for (const c of cols) h += `<th style="padding:4px 8px 4px 0;font-weight:normal">${{c}}</th>`;
  return h + '</tr></thead>';
}}

function loadUsage() {{
  fetch('/api/usage/stats').then(r=>r.json()).then(d=>{{
    const noData = t('usage.nodata');

    // Übersicht
    const tt = d.totals || {{}};
    const since = d.since ? new Date(d.since).toLocaleString() : '—';
    const tb = document.getElementById('usage-totals');
    if (tb) {{
      tb.innerHTML = `<div style="display:flex;gap:24px;flex-wrap:wrap;font-size:12px;font-family:monospace">`
        + `<div><div style="color:var(--muted)">${{t('usage.sessions')}}</div><div style="font-size:18px">${{tt.sessions||0}}</div></div>`
        + `<div><div style="color:var(--muted)">${{t('usage.watchtime')}}</div><div style="font-size:18px">${{tt.duration_human||'0m'}}</div></div>`
        + `<div><div style="color:var(--muted)">${{t('usage.volume')}}</div><div style="font-size:18px">${{tt.bytes_human||'0 B'}}</div></div>`
        + `<div><div style="color:var(--muted)">${{t('usage.since')}}</div><div style="font-size:18px">${{since}}</div></div>`
        + `</div>`;
    }}

    // Sender
    const cb = document.getElementById('usage-channels');
    if (cb) {{
      const rows = d.channels || [];
      if (!rows.length) cb.innerHTML = _uEmpty(noData);
      else {{
        let h = '<table style="width:100%;border-collapse:collapse;font-size:11px;font-family:monospace">'
          + _uHead(['#', t('usage.channel'), t('usage.watchtime'), t('usage.share'), t('usage.sessions'), t('usage.last')])
          + '<tbody>';
        rows.forEach((c,i)=>{{
          h += `<tr style="border-bottom:1px solid var(--border)">`
            + `<td style="padding:4px 8px 4px 0;color:var(--muted)">${{i+1}}</td>`
            + `<td style="padding:4px 8px 4px 0;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${{_uEsc(c.name||'')}}</td>`
            + `<td style="padding:4px 8px">${{c.duration_human||''}}</td>`
            + `<td style="padding:4px 8px">${{_uBar(c.share)}} ${{c.share}}%</td>`
            + `<td style="padding:4px 8px">${{c.sessions||0}}</td>`
            + `<td style="padding:4px 8px;color:var(--muted)">${{c.last ? new Date(c.last).toLocaleString() : '—'}}</td>`
            + `</tr>`;
        }});
        cb.innerHTML = h + '</tbody></table>';
      }}
    }}

    // Geräte
    const db = document.getElementById('usage-devices');
    if (db) {{
      const rows = d.devices || [];
      if (!rows.length) db.innerHTML = _uEmpty(noData);
      else {{
        let h = '<table style="width:100%;border-collapse:collapse;font-size:11px;font-family:monospace">'
          + _uHead([t('usage.device'), 'IP', t('usage.watchtime'), t('usage.share'), t('usage.sessions'), t('usage.profile'), t('usage.volume')])
          + '<tbody>';
        for (const v of rows) {{
          h += `<tr style="border-bottom:1px solid var(--border)">`
            + `<td style="padding:4px 8px 4px 0;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${{_uEsc(v.ua||'')}}">${{_uEsc(v.label||'?')}}</td>`
            + `<td style="padding:4px 8px;color:var(--muted)">${{_uEsc(v.ip||'')}}</td>`
            + `<td style="padding:4px 8px">${{v.duration_human||''}}</td>`
            + `<td style="padding:4px 8px">${{_uBar(v.share)}} ${{v.share}}%</td>`
            + `<td style="padding:4px 8px">${{v.sessions||0}}</td>`
            + `<td style="padding:4px 8px">${{_uEsc(v.top_profile||'—')}}</td>`
            + `<td style="padding:4px 8px">${{_uBytes(v.bytes||0)}}</td>`
            + `</tr>`;
        }}
        db.innerHTML = h + '</tbody></table>';
      }}
    }}

    // Aufnahme-Vorschläge
    const pb = document.getElementById('usage-programs');
    if (pb) {{
      const rows = d.suggestions || [];
      if (!rows.length) pb.innerHTML = _uEmpty(t('usage.no_suggest'));
      else {{
        let h = '<table style="width:100%;border-collapse:collapse;font-size:11px;font-family:monospace">'
          + _uHead([t('usage.program'), t('usage.channel'), t('usage.times'), t('usage.watchtime'), t('usage.slot')])
          + '<tbody>';
        for (const p of rows) {{
          h += `<tr style="border-bottom:1px solid var(--border)">`
            + `<td style="padding:4px 8px 4px 0;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${{_uEsc(p.title||'')}}</td>`
            + `<td style="padding:4px 8px;color:var(--muted)">${{_uEsc(p.channel||'')}}</td>`
            + `<td style="padding:4px 8px">${{p.count}}×</td>`
            + `<td style="padding:4px 8px">${{p.duration_human||''}}</td>`
            + `<td style="padding:4px 8px">${{_uEsc(p.slot||'—')}}</td>`
            + `</tr>`;
        }}
        pb.innerHTML = h + '</tbody></table>';
      }}
    }}

    // Schnittstellen
    const ib = document.getElementById('usage-interfaces');
    if (ib) {{
      const ifs = d.interfaces || [];
      const eps = d.endpoints || [];
      let h = '';
      if (ifs.length) {{
        h += `<div style="font-size:12px;color:var(--muted);margin-bottom:4px">${{t('usage.if_stream')}}</div>`;
        h += '<table style="width:100%;border-collapse:collapse;font-size:11px;font-family:monospace">'
          + _uHead([t('usage.interface'), t('usage.sessions'), t('usage.watchtime'), t('usage.volume')])
          + '<tbody>';
        for (const v of ifs) {{
          h += `<tr style="border-bottom:1px solid var(--border)">`
            + `<td style="padding:4px 8px 4px 0">${{_uEsc(v.name||'')}}</td>`
            + `<td style="padding:4px 8px">${{v.sessions||0}}</td>`
            + `<td style="padding:4px 8px">${{v.duration_human||''}}</td>`
            + `<td style="padding:4px 8px">${{_uBytes(v.bytes||0)}}</td>`
            + `</tr>`;
        }}
        h += '</tbody></table>';
      }}
      if (eps.length) {{
        h += `<div style="font-size:12px;color:var(--muted);margin:14px 0 4px">${{t('usage.if_api')}}</div>`;
        h += '<table style="width:100%;border-collapse:collapse;font-size:11px;font-family:monospace">'
          + _uHead([t('usage.endpoint'), t('usage.calls')])
          + '<tbody>';
        const maxC = Math.max.apply(null, eps.map(e=>e.count));
        for (const e of eps) {{
          h += `<tr style="border-bottom:1px solid var(--border)">`
            + `<td style="padding:4px 8px 4px 0">${{_uEsc(e.name||'')}}</td>`
            + `<td style="padding:4px 8px">${{_uBar(100*e.count/maxC)}} ${{e.count}}</td>`
            + `</tr>`;
        }}
        h += '</tbody></table>';
      }}
      ib.innerHTML = h || _uEmpty(noData);
    }}

    // Tageszeit
    const hb = document.getElementById('usage-hours');
    if (hb) {{
      const hrs = d.hours || {{}};
      let maxV = 0;
      for (let i=0;i<24;i++) maxV = Math.max(maxV, hrs[String(i)] || 0);
      if (!maxV) hb.innerHTML = _uEmpty(noData);
      else {{
        let h = '<div style="display:flex;align-items:flex-end;gap:3px;height:90px">';
        for (let i=0;i<24;i++) {{
          const v = hrs[String(i)] || 0;
          const pct = Math.round(100*v/maxV);
          const mins = Math.round(v/60);
          h += `<div style="flex:1;text-align:center" title="${{i}}:00 — ${{mins}} min">`
            + `<div style="background:var(--accent);border-radius:2px 2px 0 0;height:${{Math.max(2,pct*0.7)}}px"></div>`
            + `<div style="font-size:9px;color:var(--muted);margin-top:2px">${{i}}</div></div>`;
        }}
        hb.innerHTML = h + '</div>';
      }}
    }}
  }}).catch(e=>{{
    const b = document.getElementById('usage-totals');
    if (b) b.innerHTML = `<div style="color:var(--red)">Fehler: ${{e}}</div>`;
  }});
}}

function resetUsage() {{
  if (!confirm(t('usage.reset_confirm'))) return;
  apiPost('/api/usage/reset', {{}}).then(d=>{{
    if (d && d.ok) {{ showToast('✓ Zurückgesetzt','success'); loadUsage(); }}
    else showToast('Fehler','error');
  }}).catch(()=>showToast('Fehler','error'));
}}

function switchTab(name, btn) {{
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  const tabBtn = btn || (typeof event !== 'undefined' && event.currentTarget) || null;
  if (tabBtn && tabBtn.closest) tabBtn.closest('.tab-btn').classList.add('active');
  if (name === 'maintenance') {{ refreshLogs(); initApiLogToggle(); initMaintNotify(); loadFavLogos(); loadStreamInfo(); loadInterfaces(); }}
  else {{
    if (_logPollTimer) {{ clearInterval(_logPollTimer); _logPollTimer = null; }}
    if (_apiLogPollTimer) {{ clearInterval(_apiLogPollTimer); _apiLogPollTimer = null; }}
  }}
  if (name === 'epg') {{
    pollEpgStatus();
    loadEpgHistory();
    // Polling starten falls gerade ein Run läuft
    fetch('/api/epg/status').then(r=>r.json()).then(d=>{{
      if (d.running && !epgPollTimer) startEpgPolling();
    }});
  }}
  if (name === 'recording') {{ loadTunerStatus(); loadRecordingStatus(); loadRecChannels(); loadCompressionStatus(); }}
  if (name === 'usage') {{ loadUsage(); loadSwitchStats(); }}
}}

// ── Compression UI ──────────────────────────────────────────────────────────
function _fmtBytes(b) {{
  if (b > 1024*1024*1024) return (b/(1024*1024*1024)).toFixed(2)+' GB';
  if (b > 1024*1024) return (b/(1024*1024)).toFixed(1)+' MB';
  if (b > 1024) return (b/1024).toFixed(1)+' KB';
  return b+' B';
}}
function _fmtElapsed(s) {{
  if (s < 60) return s+'s';
  if (s < 3600) return Math.floor(s/60)+'m '+(s%60)+'s';
  return Math.floor(s/3600)+'h '+Math.floor((s%3600)/60)+'m';
}}

function loadCompressionStatus() {{
  fetch('/api/compression/status').then(r=>r.json()).then(d=>{{
    if (!d.ok) return;
    // Select profile
    const sel = document.getElementById('comp-profile');
    if (sel && d.config.profile) sel.value = d.config.profile;
    // Window status indicator
    const ws = document.getElementById('comp-window-status');
    if (ws) {{
      if (!d.config.enabled) ws.textContent = '(' + t('comp.window') + ': ' + d.config.window_start + '–' + d.config.window_end + ')';
      else if (d.in_window) ws.innerHTML = '🟢 <span data-i18n="comp.in_window">In window</span>';
      else ws.innerHTML = '⚪ <span data-i18n="comp.out_window">Outside window</span>';
    }}
    // Backlog warning
    document.getElementById('comp-backlog').style.display = d.backlog ? 'block' : 'none';
    // Current job
    const cur = document.getElementById('comp-current');
    if (d.current) {{
      cur.style.display = 'block';
      document.getElementById('comp-current-file').textContent = d.current.file.split('/').slice(-2).join('/');
      const started = new Date(d.current.started);
      const elapsed = Math.floor((Date.now() - started.getTime())/1000);
      const paused = !!d.current.paused;
      let meta = d.current.profile + ' · ' + _fmtBytes(d.current.orig_size||0) + ' · ' + _fmtElapsed(elapsed);
      if (d.current.cpu_limit) meta += ' · CPU≤' + d.current.cpu_limit + '%';
      if (paused) meta += ' · ⏸ ' + t('comp.paused');
      document.getElementById('comp-current-meta').textContent = meta;
      // Progress bar
      const pct = Math.round((d.current.progress||0) * 100);
      document.getElementById('comp-current-bar').style.width = pct + '%';
      // ETA line
      let eta = pct + '%';
      if (d.current.speed) eta += ' · ' + d.current.speed.toFixed(2) + 'x';
      if (!paused && d.current.eta_sec != null) eta += ' · ' + t('comp.eta') + ' ' + _fmtElapsed(d.current.eta_sec);
      else if (paused) eta += ' · ⏸';
      document.getElementById('comp-current-eta').textContent = eta;
      // Pause/Resume toggle
      document.getElementById('comp-pause-btn').style.display = paused ? 'none' : '';
      document.getElementById('comp-resume-btn').style.display = paused ? '' : 'none';
    }} else {{
      cur.style.display = 'none';
    }}
    // Pending count + size
    document.getElementById('comp-pending-count').textContent = d.pending_count;
    document.getElementById('comp-pending-size').textContent = _fmtBytes(d.pending_size_bytes||0);
    // Pending list (top 50)
    const pl = document.getElementById('comp-pending-list');
    // Preserve current selection across auto-refresh
    const prevChecked = new Set(Array.from(document.querySelectorAll('.comp-pending-cb:checked')).map(cb => cb.value));
    if (d.pending_files && d.pending_files.length) {{
      pl.innerHTML = d.pending_files.map(f => {{
        const name = f.path.split('/').slice(-2).join('/');
        const enc = encodeURIComponent(f.path);
        return '<label style="display:flex;align-items:center;gap:6px;padding:2px 0;color:var(--muted);cursor:pointer">'
             + '<input type="checkbox" class="comp-pending-cb" value="' + enc + '" onchange="syncSelectAllPending()">'
             + '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + name + '</span>'
             + '<span style="color:var(--accent2);flex-shrink:0">' + _fmtBytes(f.size) + '</span></label>';
      }}).join('');
      // Restore previously checked items
      document.querySelectorAll('.comp-pending-cb').forEach(cb => {{ if (prevChecked.has(cb.value)) cb.checked = true; }});
      syncSelectAllPending();
    }} else {{
      pl.innerHTML = '<div style="color:var(--muted);padding:4px" data-i18n="comp.no_pending">No files pending compression.</div>';
      const selAll = document.getElementById('comp-select-all');
      if (selAll) selAll.checked = false;
    }}
    // History
    const hl = document.getElementById('comp-history-list');
    if (d.history && d.history.length) {{
      hl.innerHTML = d.history.map(h => {{
        const icon = h.ok ? '✓' : '✗';
        const color = h.ok ? '#22c55e' : '#ef4444';
        const name = (h.ts_path||'').split('/').slice(-2).join('/');
        const when = (h.started||'').replace('T',' ').substring(0, 19);
        const manual = h.manual ? ' [manual]' : '';
        let info = '';
        if (h.ok) {{
          const ratio = h.ratio ? (h.ratio*100).toFixed(0) + '%' : '?';
          info = _fmtBytes(h.orig_size||0) + ' → ' + _fmtBytes(h.new_size||0) + ' (' + ratio + ') · ' + _fmtElapsed(h.elapsed||0);
        }} else {{
          info = (h.error||'unknown error').substring(0, 80);
        }}
        return '<div style="padding:3px 0;border-bottom:1px solid var(--border)">'
             + '<span style="color:' + color + '">' + icon + '</span> '
             + '<span style="color:var(--muted)">' + when + manual + '</span> '
             + name + '<br>'
             + '<span style="margin-left:18px;color:var(--muted);font-size:9px">' + info + '</span></div>';
      }}).join('');
    }} else {{
      hl.innerHTML = '<div style="color:var(--muted);padding:4px" data-i18n="comp.no_history">No compression runs yet.</div>';
    }}
    // Re-apply i18n to dynamically inserted elements
    if (typeof applyI18n === 'function') applyI18n();
  }}).catch(e => console.warn('comp status:', e));
}}

function saveCompressionConfig() {{
  const cfg = {{
    compression_enabled: document.getElementById('comp-enabled').checked,
    compression_profile: document.getElementById('comp-profile').value,
    compression_window_start: document.getElementById('comp-window-start').value,
    compression_window_end: document.getElementById('comp-window-end').value,
    compression_delete_original: document.getElementById('comp-delete-orig').checked,
    compression_cpu_limit: parseInt(document.getElementById('comp-cpu-limit').value) || 0,
    compression_ignore_window: document.getElementById('comp-ignore-window').checked,
  }};
  apiPost('/api/config-update', cfg).then(d => {{
    if (d.ok) {{
      showToast(t('toast.saved'), 'success');
      loadCompressionStatus();
    }} else {{
      showToast(d.message || t('toast.error'), 'error');
    }}
  }});
}}

function pauseCompression() {{
  apiPost('/api/compression/pause', {{}}).then(d=>{{
    if (d.ok) showToast(t('comp.paused'), 'success');
    setTimeout(loadCompressionStatus, 300);
  }});
}}

function resumeCompression() {{
  apiPost('/api/compression/resume', {{}}).then(d=>{{
    setTimeout(loadCompressionStatus, 300);
  }});
}}

function cancelCompression() {{
  if (!confirm(t('comp.cancel_confirm'))) return;
  apiPost('/api/compression/cancel', {{}}).then(d=>{{
    if (d.ok) showToast(t('comp.cancelled'), 'success');
    setTimeout(loadCompressionStatus, 500);
  }});
}}

function toggleSelectAllPending(checked) {{
  document.querySelectorAll('.comp-pending-cb').forEach(cb => cb.checked = checked);
}}

function syncSelectAllPending() {{
  const all = Array.from(document.querySelectorAll('.comp-pending-cb'));
  const selAll = document.getElementById('comp-select-all');
  if (!selAll) return;
  selAll.checked = all.length > 0 && all.every(cb => cb.checked);
}}

function convertSelected() {{
  const checked = Array.from(document.querySelectorAll('.comp-pending-cb:checked'));
  if (!checked.length) {{ showToast(t('comp.select_hint'), 'error'); return; }}
  const paths = checked.map(cb => decodeURIComponent(cb.value));
  apiPost('/api/compression/run', {{paths: paths}}).then(d=>{{
    if (d.ok) {{
      showToast(t('comp.started'), 'success');
      setTimeout(loadCompressionStatus, 500);
    }} else {{
      showToast(d.message || t('toast.error'), 'error');
    }}
  }});
}}

// Auto-refresh compression status when on recording tab (faster while a job runs)
let _compRefreshTick = 0;
setInterval(() => {{
  const panel = document.getElementById('tab-recording');
  if (!panel || !panel.classList.contains('active')) return;
  _compRefreshTick++;
  // Poll every 2s while a job is active, otherwise every 6s
  const active = document.getElementById('comp-current') &&
                 document.getElementById('comp-current').style.display !== 'none';
  if (active || _compRefreshTick % 3 === 0) {{
    loadCompressionStatus();
  }}
}}, 2000);

function saveConfig() {{
  let cfg;
  try {{ cfg = JSON.parse(document.getElementById('config-json').value); }}
  catch(e) {{ showToast('JSON Fehler: ' + e.message, 'error'); return; }}
  apiPost('/api/config', cfg).then(d => {{
    showToast(d.ok ? '✓ Config gespeichert!' : 'Fehler: '+(d.message||'?'), d.ok ? 'success' : 'error');
    if (d.ok) setTimeout(() => location.reload(), 1500);
  }});
}}

function saveApiKey(key, inputId) {{
  const val = document.getElementById(inputId).value.trim();
  fetch('/api/config').then(r=>r.json()).then(cfg => {{
    cfg[key] = val;
    apiPost('/api/config', cfg).then(d => {{
      showToast(d.ok ? '✓ API-Key gespeichert!' : 'Fehler', d.ok ? 'success' : 'error');
    }});
  }});
}}

function toggleDiag(on) {{
  fetch('/api/diag?on=' + (on ? '1' : '0')).then(r=>r.json()).then(d => {{
    showToast(d.ok ? (d.enabled ? '✓ Diagnose-Logging aktiv' : '✓ Diagnose-Logging aus')
                   : 'Fehler', d.ok ? 'success' : 'error');
    renderDiag(d);
  }});
}}

function refreshDiag() {{
  fetch('/api/diag').then(r=>r.json()).then(renderDiag);
}}

function renderDiag(d) {{
  const el = document.getElementById('diag-live');
  if (!el || !d || !d.ok) return;
  if (!d.streams || !d.streams.length) {{ el.textContent = 'Kein aktiver Stream.'; return; }}
  el.innerHTML = d.streams.map(s =>
    (s.channel || s.ref) + ' [' + s.mode + '] ' + s.kbps + ' kbps · ' + s.mb + ' MB · '
    + 'read-wait ' + s.read_wait_s + 's / write-wait ' + s.write_wait_s + 's · '
    + 'Stalls: ' + s.stalls + ' (' + s.stall_total_s + 's)'
    + (s.stalled_since_s !== null ? ' <b style="color:var(--danger,#e55)">STEHT seit ' + s.stalled_since_s + 's</b>' : '')
  ).join('<br>');
}}

function saveOwif() {{
  const fb = document.getElementById('owif-fb');
  const overrides = [];
  document.getElementById('owif-ua').value.split('\\n').forEach(line => {{
    const t = line.trim();
    if (!t) return;
    const idx = t.indexOf('=>');
    if (idx < 0) return;
    const match = t.slice(0, idx).trim();
    const profile = t.slice(idx + 2).trim();
    if (match && profile) overrides.push({{match: match, profile: profile}});
  }});
  const owif = {{
    enabled: document.getElementById('owif-enabled').checked,
    bind: document.getElementById('owif-bind').value.trim() || '0.0.0.0',
    webif_port: parseInt(document.getElementById('owif-webif-port').value, 10) || 80,
    stream_port: parseInt(document.getElementById('owif-stream-port').value, 10) || 8001,
    recordings_enabled: document.getElementById('owif-recordings-enabled').checked,
    recordings_port: parseInt(document.getElementById('owif-recordings-port').value, 10) || 0,
    default_profile: document.getElementById('owif-default-profile').value,
    ua_overrides: overrides
  }};
  apiPost('/api/config-update', {{openwebif_emulation: owif}}).then(d => {{
    fb.textContent = d.ok ? '✓ Gespeichert — Neustart für Port-Änderungen nötig' : '✗ Fehler';
    showToast(d.ok ? '✓ OpenWebif-Emulation gespeichert!' : 'Fehler', d.ok ? 'success' : 'error');
  }}).catch(() => {{ fb.textContent = '✗ Fehler'; }});
}}

function resetConfig() {{
  if (!confirm('Config zurücksetzen?')) return;
  document.getElementById('config-json').value = JSON.stringify(ORIGINAL_CONFIG, null, 2);
}}

function doAction(url, fbId, loadingMsg, doneMsg) {{
  const fb = document.getElementById(fbId);
  fb.textContent = loadingMsg;
  fetch(url).then(() => {{ fb.textContent = doneMsg; }}).catch(() => {{ fb.textContent = '✗ Fehler'; }});
}}

function reloadChannels() {{
  const fb = document.getElementById('fb-channels');
  fb.textContent = 'Sender werden geladen…';
  fetch('/api/channels/reload').then(r => r.json()).then(d => {{
    fb.textContent = d.ok ? (d.count + ' Sender geladen ✓') : '✗ Fehler';
  }}).catch(() => {{ fb.textContent = '✗ Fehler'; }});
}}

function restartService() {{
  if (!confirm('Service wirklich neu starten? Aktive Streams werden unterbrochen.')) return;
  const fb = document.getElementById('fb-restart');
  fb.textContent = 'Neustart wird ausgeführt…';
  fetch('/api/restart', {{method:'POST'}}).then(() => {{
    fb.textContent = 'Neustart ausgeführt — Seite lädt neu…';
    setTimeout(() => location.reload(), 4000);
  }}).catch(() => {{
    fb.textContent = 'Neustart ausgeführt — Seite lädt neu…';
    setTimeout(() => location.reload(), 4000);
  }});
}}

let _currentLogLevel = 'INFO';

// ── API Access Log ────────────────────────────────────
function initApiLogToggle() {{
  fetch('/api/access-log?n=0').then(r=>r.json()).then(d=>{{
    setApiLogUI(d.enabled);
    if (d.enabled) loadAccessLog();
  }});
}}

function setApiLogUI(enabled) {{
  const status = document.getElementById('api-log-status');
  const toggle = document.getElementById('api-log-toggle');
  const thumb  = document.getElementById('api-log-thumb');
  const wrap   = document.getElementById('api-log-table-wrap');
  if (status) status.textContent = enabled ? t('apilog.status_label') : t('apilog.status_off');
  if (toggle) toggle.style.background = enabled ? 'var(--accent)' : 'var(--border)';
  if (thumb)  thumb.style.left = enabled ? '18px' : '2px';
  if (wrap)   wrap.style.display = enabled ? 'block' : 'none';
}}

function toggleApiLogging() {{
  fetch('/api/access-log?n=0').then(r=>r.json()).then(d=>{{
    apiPost('/api/access-log/toggle', {{enabled: !d.enabled}}).then(r=>{{
      setApiLogUI(r.enabled);
      showToast(r.enabled ? '✓ ' + t('apilog.activated') : t('apilog.deactivated'), 'success');
      if (r.enabled) loadAccessLog();
    }});
  }});
}}

function loadAccessLog() {{
  // Live-Modus: letzte 100 + ab "jetzt" polling
  _apiLogSince = null;
  fetch('/api/access-log?n=100').then(r=>r.json()).then(d=>{{
    setApiLogUI(d.enabled);
    const box = document.getElementById('api-log-box');
    if (!box) return;
    _apiLogSince = d.now_unix;
    if (!d.entries || !d.entries.length) {{
      box.innerHTML = '<div style="color:var(--muted);padding:4px">Noch keine Einträge.</div>';
    }} else {{
      // entries sind reverse-chronologisch (neueste zuerst) → für Anzeige umdrehen
      const sorted = d.entries.slice().sort((a,b)=>(a.ts_unix||0)-(b.ts_unix||0));
      box.innerHTML = sorted.map(renderAccessLogLine).join('');
      box.scrollTop = box.scrollHeight;
    }}
    // Start polling
    if (_apiLogPollTimer) clearInterval(_apiLogPollTimer);
    _apiLogPollTimer = setInterval(pollAccessLog, 3000);
  }});
}}

function renderAccessLogLine(e) {{
  const sc = e.status >= 500 ? 'log-error' : e.status >= 400 ? 'log-warn' : 'log-info';
  const slow = e.ms > 1000 ? ' style="color:var(--amber)"' : '';
  return `<div class="${{sc}}">${{e.ts}} ${{(e.method||'').padEnd(4)}} ${{e.path}} — ${{e.ip}} ${{e.status}}<span${{slow}}> ${{e.ms}}ms</span></div>`;
}}

let _apiLogSince = null;
let _apiLogPollTimer = null;

function pollAccessLog() {{
  if (_apiLogSince === null) return;
  fetch('/api/access-log?since=' + _apiLogSince + '&n=all').then(r=>r.json()).then(d=>{{
    if (!d.entries || !d.entries.length) {{
      _apiLogSince = d.now_unix;
      return;
    }}
    const box = document.getElementById('api-log-box');
    if (!box) return;
    const sorted = d.entries.slice().sort((a,b)=>(a.ts_unix||0)-(b.ts_unix||0));
    const html = sorted.map(renderAccessLogLine).join('');
    if (box.querySelector('div[style*="color"]') && box.children.length === 1) {{
      box.innerHTML = html;
    }} else {{
      box.insertAdjacentHTML('beforeend', html);
    }}
    box.scrollTop = box.scrollHeight;
    _apiLogSince = d.now_unix;
  }});
}}

function loadAccessLogHistory() {{
  const hours = document.getElementById('api-log-hours').value;
  const box = document.getElementById('api-log-box');
  if (!box) return;
  box.insertAdjacentHTML('afterbegin', '<div style="color:var(--muted);padding:4px;font-style:italic">Lade Historie…</div>');
  fetch('/api/access-log/history?hours=' + hours).then(r=>r.json()).then(d=>{{
    const loading = box.querySelector('div[style*="italic"]');
    if (loading) loading.remove();
    if (!d.entries || !d.entries.length) {{
      box.insertAdjacentHTML('afterbegin', `<div style="color:var(--muted);padding:4px">Keine historischen Einträge (${{hours}}h).</div>`);
      return;
    }}
    // Filter Duplikate: nur Einträge ÄLTER als ältester aktueller
    let oldestVisible = null;
    box.querySelectorAll('div').forEach(d=>{{
      const t = d.textContent.match(/^(\\d{{4}}-\\d{{2}}-\\d{{2}} \\d{{2}}:\\d{{2}}:\\d{{2}})/);
      if (t && (!oldestVisible || t[1] < oldestVisible)) oldestVisible = t[1];
    }});
    const sorted = d.entries.slice().sort((a,b)=>(a.ts_unix||0)-(b.ts_unix||0));
    const filtered = oldestVisible ? sorted.filter(e=>e.ts < oldestVisible) : sorted;
    const html = filtered.map(renderAccessLogLine).join('');
    box.insertAdjacentHTML('afterbegin', html);
    showToast(`${{filtered.length}} historische Einträge geladen`, 'success');
  }});
}}

function clearAccessLog() {{
  if (!confirm(t('apilog.clear_confirm'))) return;
  apiPost('/api/access-log/clear', {{}}).then(d=>{{
    showToast(d.ok ? '✓ Log geleert' : 'Fehler', d.ok ? 'success' : 'error');
    loadAccessLog();
  }});
}}

let _logSince = null;
let _logPollTimer = null;

function renderLogLine(e) {{
  let cls = 'log-info';
  if (e.level === 'ERROR') cls = 'log-error';
  else if (e.level === 'WARNING') cls = 'log-warn';
  else if (e.level === 'DEBUG') cls = 'log-debug';
  const msg = String(e.msg||'').replace(/</g,'&lt;');
  return `<div class="${{cls}}">${{e.ts}} [${{e.level}}] ${{msg}}</div>`;
}}

function refreshLogs() {{
  // Live-Modus: letzte 100 + ab "jetzt"
  _logSince = null;
  fetch('/api/logs?level=' + _currentLogLevel + '&n=100').then(r => r.json()).then(d => {{
    const box = document.getElementById('log-box');
    if (!box) return;
    _logSince = d.now_unix;
    if (!d.entries || !d.entries.length) {{
      box.innerHTML = '<div style="color:var(--muted);padding:4px">Keine Logs auf Level ' + _currentLogLevel + '.</div>';
    }} else {{
      box.innerHTML = d.entries.map(renderLogLine).join('');
      box.scrollTop = box.scrollHeight;
    }}
    if (_logPollTimer) clearInterval(_logPollTimer);
    _logPollTimer = setInterval(pollLogs, 2000);
  }}).catch(() => {{ document.getElementById('log-box').textContent = 'Logs nicht verfügbar.'; }});
}}

function pollLogs() {{
  if (_logSince === null) return;
  fetch('/api/logs?level=' + _currentLogLevel + '&since=' + _logSince + '&n=all').then(r=>r.json()).then(d=>{{
    if (!d.entries || !d.entries.length) {{
      _logSince = d.now_unix;
      return;
    }}
    const box = document.getElementById('log-box');
    if (!box) return;
    const html = d.entries.map(renderLogLine).join('');
    if (box.querySelector('div[style*="muted"]') && box.children.length === 1) {{
      box.innerHTML = html;
    }} else {{
      box.insertAdjacentHTML('beforeend', html);
    }}
    box.scrollTop = box.scrollHeight;
    _logSince = d.now_unix;
  }});
}}

function loadLogHistory() {{
  const hours = document.getElementById('log-hours').value;
  const box = document.getElementById('log-box');
  if (!box) return;
  box.insertAdjacentHTML('afterbegin', '<div style="color:var(--muted);padding:4px;font-style:italic">Lade Historie…</div>');
  fetch('/api/logs/history?level=' + _currentLogLevel + '&hours=' + hours).then(r=>r.json()).then(d=>{{
    const loading = box.querySelector('div[style*="italic"]');
    if (loading) loading.remove();
    if (!d.entries || !d.entries.length) {{
      box.insertAdjacentHTML('afterbegin', `<div style="color:var(--muted);padding:4px">Keine historischen Einträge (${{hours}}h, Level ${{_currentLogLevel}}).</div>`);
      return;
    }}
    let oldestVisible = null;
    box.querySelectorAll('div').forEach(d=>{{
      const t = d.textContent.match(/^(\\d{{4}}-\\d{{2}}-\\d{{2}} \\d{{2}}:\\d{{2}}:\\d{{2}})/);
      if (t && (!oldestVisible || t[1] < oldestVisible)) oldestVisible = t[1];
    }});
    const filtered = oldestVisible ? d.entries.filter(e=>e.ts < oldestVisible) : d.entries;
    box.insertAdjacentHTML('afterbegin', filtered.map(renderLogLine).join(''));
    showToast(`${{filtered.length}} historische Einträge geladen`, 'success');
  }});
}}

function setLogLevel(level) {{
  _currentLogLevel = level;
  apiPost('/api/log/level', {{level: level}}).then(d => {{
    const fb = document.getElementById('log-level-fb');
    if (fb) {{ fb.textContent = d.ok ? ('✓ Level: ' + level) : 'Fehler'; setTimeout(()=>{{if(fb)fb.textContent='';}}, 2000); }}
    refreshLogs();
  }});
}}

// Aktuellen Log-Level beim Laden abfragen
fetch('/api/log/level').then(r=>r.json()).then(d=>{{
  if (d.level) {{
    _currentLogLevel = d.level;
    const sel = document.getElementById('log-level-sel');
    if (sel) sel.value = d.level;
  }}
}}).catch(()=>{{}});

function copyUrl(btn, url) {{
  navigator.clipboard.writeText(url).then(() => {{
    btn.textContent = '✓';
    btn.classList.add('copied');
    setTimeout(() => {{ btn.textContent = '⎘'; btn.classList.remove('copied'); }}, 1500);
  }}).catch(() => {{
    // Fallback für ältere Browser
    const el = document.createElement('textarea');
    el.value = url;
    document.body.appendChild(el);
    el.select();
    document.execCommand('copy');
    document.body.removeChild(el);
    btn.textContent = '✓';
    btn.classList.add('copied');
    setTimeout(() => {{ btn.textContent = '⎘'; btn.classList.remove('copied'); }}, 1500);
  }});
}}

// Logs beim Start laden wenn Maintenance-Tab aktiv
if (document.getElementById('tab-maintenance').classList.contains('active')) {{ refreshLogs(); initApiLogToggle(); initMaintNotify(); loadFavLogos(); loadStreamInfo(); loadInterfaces(); }}

// ── Recording ─────────────────────────────────────────
let _quickRecId = null;
let _epgChannels = [];
let _testRecId = null;

// ── Stream-Info (Wartung) ─────────────────────────────
let _streamInfoData = [];

function _siEsc(s) {{
  return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}

function _siDur(sec) {{
  sec = Math.max(0, parseInt(sec||0, 10));
  const h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60), s = sec%60;
  const p = n => String(n).padStart(2,'0');
  return h > 0 ? `${{h}}:${{p(m)}}:${{p(s)}}` : `${{m}}:${{p(s)}}`;
}}

function loadStreamInfo() {{
  const el = document.getElementById('stream-info-list');
  if (!el) return;
  fetch('/api/status').then(r=>r.json()).then(data => {{
    _streamInfoData = data.receivers || [];
    const enabled = _streamInfoData;
    if (!enabled.length) {{ el.innerHTML = `<span style="color:var(--muted);font-size:11px;font-family:monospace">${{t('sm.no_active')}}</span>`; return; }}
    el.innerHTML = enabled.map(rx => {{
      const s = rx.stream;
      const isRec = !!rx.recording;
      const busy = rx.busy && s;
      const dot = rx.locked ? 'var(--red)' : (isRec ? 'var(--red)' : (busy ? 'var(--amber)' : (rx.unhealthy ? 'var(--red)' : 'var(--green)')));
      const line = rx.locked ? '🔒 gesperrt'
        : (busy ? _siEsc((isRec?'🔴 ':'') + (s.channel_name||'?') + ' · ' + (s.client_ip||'?'))
          : (rx.unhealthy ? _siEsc('⚠ ' + t('rx.unreachable') + ' ' + (rx.unreachable_sec ? new Date(Date.now() - rx.unreachable_sec*1000).toLocaleString([], {{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}}) : '?')) : t('sm.no_stream')));
      const plusBtn = busy ? `<button class="btn" style="font-size:10px;padding:2px 7px" onclick="toggleStreamDetail('${{rx.id}}')" title="${{t('sm.expand')}}" id="si-plus-${{rx.id}}">+</button>` : '';
      const killBtn = busy ? `<button onclick="killStreamInfo('${{rx.id}}')" title="${{t('sm.stop')}}" style="background:none;border:1px solid var(--red);color:var(--red);width:22px;height:22px;border-radius:4px;cursor:pointer;font-size:11px;display:inline-flex;align-items:center;justify-content:center">✕</button>` : '';
      let detail = '';
      if (busy) {{
        let tc;
        if (s.transcode && s.transcode.active) {{
          const parts = [];
          if (s.transcode.label) parts.push(_siEsc(s.transcode.label));
          if (s.transcode.codec) parts.push(_siEsc(s.transcode.codec));
          tc = '✅ ' + t('sm.yes') + (parts.length ? ' · ' + parts.join(' · ') : '');
        }} else {{ tc = '➖ ' + t('sm.no_passthrough'); }}
        const rows = [
          [t('sm.device'), _siEsc(s.client_ip||'?')],
          [t('sm.software'), _siEsc(s.software||'?') + (s.user_agent ? ` (${{_siEsc(s.user_agent)}})` : '')],
          s.profile ? [t('sm.profile'), _siEsc(s.profile)] : null,
          [t('sm.data'), _siEsc(s.bytes_human||'0 B')],
          [t('sm.transcode'), tc],
          [t('sm.runtime'), _siDur(s.duration_sec) + (s.started ? ` (${{_siEsc(s.started)}})` : '')],
          isRec ? [t('sm.recording'), '🔴 ' + _siEsc(rx.recording.title||'')] : null,
        ].filter(Boolean);
        detail = `<div id="si-detail-${{rx.id}}" style="display:none;margin-top:8px;padding-top:8px;border-top:1px dashed var(--border);font-size:11px;font-family:monospace">` +
          rows.map(r => `<div style="display:flex;gap:8px;margin:2px 0"><span style="color:var(--muted);min-width:130px">${{r[0]}}</span><span>${{r[1]}}</span></div>`).join('') +
          `</div>`;
      }}
      return `<div style="border:1px solid var(--border);border-radius:6px;padding:8px 10px">
        <div style="display:flex;align-items:center;gap:8px">
          <span style="width:8px;height:8px;border-radius:50%;background:${{dot}};flex-shrink:0"></span>
          <b style="font-size:12px">${{_siEsc(rx.name)}}</b>
          <span style="color:var(--muted);font-size:11px;font-family:monospace;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${{line}}</span>
          ${{plusBtn}}${{killBtn}}
        </div>${{detail}}
      </div>`;
    }}).join('');
  }}).catch(()=>{{ el.textContent = 'Fehler.'; }});
}}

function toggleStreamDetail(id) {{
  const d = document.getElementById('si-detail-' + id);
  const b = document.getElementById('si-plus-' + id);
  if (!d) return;
  const open = d.style.display !== 'none' && d.style.display !== '';
  d.style.display = open ? 'none' : 'block';
  if (b) b.textContent = open ? '+' : '−';
}}

function killStreamInfo(id) {{
  if (!confirm(t('sm.confirm_stop'))) return;
  fetch('/kill?receiver=' + id).then(r=>r.json()).then(d => {{
    showToast(d.message || 'Stream stopped', d.ok ? 'success' : 'error');
    setTimeout(loadStreamInfo, 400);
  }});
}}

function loadTunerStatus() {{
  fetch('/api/tuners').then(r=>r.json()).then(d=>{{
    const el = document.getElementById('tuner-status');
    if (!el) return;
    const color = d.free > 0 ? 'var(--green)' : 'var(--red)';
    let html = `<div style="font-family:monospace;font-size:12px;margin-bottom:8px;color:${{color}}">${{d.free}} / ${{d.total}} Tuner</div>`;
    html += d.receivers.map(r => {{
      const c = r.locked ? 'var(--red)' : (r.busy ? 'var(--amber)' : 'var(--green)');
      const info = r.locked ? '🔒 gesperrt' : (r.busy ? r.channel + ' · ' + r.client_ip : 'frei');
      return `<div style="display:flex;align-items:center;gap:8px;font-size:11px;margin:3px 0"><span style="width:8px;height:8px;border-radius:50%;background:${{c}};flex-shrink:0"></span><b>${{r.name}}</b><span style="color:var(--muted)">${{info}}</span></div>`;
    }}).join('');
    el.innerHTML = html;
  }}).catch(()=>{{ if(document.getElementById('tuner-status')) document.getElementById('tuner-status').textContent='Fehler.'; }});
}}

function loadRecordingStatus() {{
  fetch('/api/record/status').then(r=>r.json()).then(d=>{{
    const el = document.getElementById('rec-active');
    if (!el) return;
    if (!d.recordings || !d.recordings.length) {{ el.textContent = t('rec.no_active'); return; }}
    el.innerHTML = d.recordings.map(rec => {{
      const pct = Math.min(100, Math.round(rec.elapsed_sec / rec.duration_sec * 100));
      const rem = Math.round(rec.remaining_sec / 60);
      return `<div style="border:1px solid var(--border);border-radius:6px;padding:10px;margin-bottom:8px"><div style="display:flex;justify-content:space-between;align-items:center"><b style="font-size:12px">${{rec.title}}</b><button class="btn btn-danger" onclick="stopRec('${{rec.id}}')" style="font-size:10px;padding:2px 8px">⏹ Stop</button></div><div style="font-family:monospace;font-size:10px;color:var(--muted);margin:4px 0">ID: ${{rec.id}} · Receiver: ${{rec.receiver}}</div><div style="background:var(--surface2);border-radius:4px;height:8px;margin-top:6px"><div style="background:var(--red);height:100%;width:${{pct}}%;border-radius:4px"></div></div><div style="font-size:10px;color:var(--muted);margin-top:3px">${{pct}}% · noch ${{rem}} Min</div></div>`;
    }}).join('');
  }});
}}

function stopRec(id) {{
  apiPost('/api/record/stop', {{recording_id: id}}).then(d=>{{
    showToast(d.ok ? '⏹ Aufnahme gestoppt' : 'Fehler: ' + d.message, d.ok ? 'success' : 'error');
    setTimeout(()=>{{ loadRecordingStatus(); loadTunerStatus(); }}, 500);
  }});
}}

// ── Kanal-Dropdown für Schnell-Aufnahme ───────────────
function loadRecChannels() {{
  fetch('/api/epg/data').then(r=>r.json()).then(d=>{{
    if (!d.ok) return;
    _epgChannels = d.channels;
    const sel = document.getElementById('rec-ch-sel');
    if (!sel) return;
    // Bestehende Optionen leeren (verhindert Duplikate beim Tab-Wechsel)
    sel.innerHTML = '<option value="" data-i18n="rec.select_channel">— Select channel —</option>';
    d.channels.forEach(ch => {{
      const opt = document.createElement('option');
      opt.value = ch.id;
      opt.textContent = ch.name;
      sel.appendChild(opt);
    }});
  }});
}}

function onRecChannelChange() {{
  const sel = document.getElementById('rec-ch-sel');
  const cid = sel.value;
  if (!cid) {{
    document.getElementById('rec-now-detail').style.display = 'none';
    document.getElementById('rec-quick-btn').disabled = true;
    return;
  }}
  const ch = _epgChannels.find(c => c.id === cid);
  if (!ch) return;
  const now = Math.floor(Date.now()/1000);
  const current = ch.events.find(e => e.start <= now && e.stop > now);
  const next    = ch.events.find(e => e.start > now);
  const detail  = document.getElementById('rec-now-detail');
  if (current) {{
    document.getElementById('rec-now-title').textContent = current.title || '(Unbekannte Sendung)';
    const rem = Math.round((current.stop - now) / 60);
    document.getElementById('rec-now-time').textContent = fmtRecTime(current.start) + ' – ' + fmtRecTime(current.stop) + ' · noch ' + rem + ' Min';
    document.getElementById('rec-now-desc').textContent = current.desc || '';
    document.getElementById('rec-quick-dur').value = rem;
    detail.style.display = 'block';
  }} else if (next) {{
    document.getElementById('rec-now-title').textContent = '→ ' + (next.title || '?');
    document.getElementById('rec-now-time').textContent = 'Nächste: ' + fmtRecTime(next.start);
    document.getElementById('rec-now-desc').textContent = '';
    detail.style.display = 'block';
  }} else {{
    detail.style.display = 'none';
  }}
  document.getElementById('rec-quick-btn').disabled = false;
}}

function fmtRecTime(ts) {{
  const d = new Date(ts * 1000);
  return d.getHours().toString().padStart(2,'0') + ':' + d.getMinutes().toString().padStart(2,'0');
}}

function quickRecord() {{
  const sel = document.getElementById('rec-ch-sel');
  const cid = sel.value;
  if (!cid) return;
  const ch = _epgChannels.find(c => c.id === cid);
  const now = Math.floor(Date.now()/1000);
  const current = ch && ch.events.find(e => e.start <= now && e.stop > now);
  const title = current ? current.title : (ch ? ch.name : 'Aufnahme');
  const durMin = parseInt(document.getElementById('rec-quick-dur').value) || 60;
  // Service-Ref aus cid zurückbauen
  const ref = cid.replace(/_/g, (m, i, s) => {{
    // Nur die ersten 6 _ sind Trennzeichen in Service-Refs
    return ':';
  }});
  // Einfacher: cid direkt übergeben, e2proxy kennt es
  apiPost('/api/record/start', {{
    ref: cid.replace(/_/g, ':'),
    title: title,
    duration: durMin * 60,
    description: current ? (current.desc || '') : '',
  }}).then(d=>{{
    const fb = document.getElementById('rec-quick-fb');
    if (d.ok) {{
      _quickRecId = d.recording_id;
      fb.textContent = '▶ Läuft: ' + d.file.split('/').pop();
      fb.style.color = 'var(--green)';
      document.getElementById('rec-quick-running').style.display = 'block';
      document.getElementById('rec-quick-id').textContent = d.recording_id;
      document.getElementById('rec-quick-btn').disabled = true;
      setTimeout(()=>{{ loadTunerStatus(); loadRecordingStatus(); }}, 500);
    }} else {{
      fb.textContent = 'Fehler: ' + d.message;
      fb.style.color = 'var(--red)';
    }}
  }});
}}

function quickStop() {{
  if (!_quickRecId) return;
  stopRec(_quickRecId);
  _quickRecId = null;
  document.getElementById('rec-quick-running').style.display = 'none';
  document.getElementById('rec-quick-btn').disabled = false;
  document.getElementById('rec-quick-fb').textContent = '';
}}

// ── Plex Token ────────────────────────────────────────
let _selectedSections = new Set(
  (document.getElementById('rec-plex-section')?.value || '').split(',').filter(Boolean)
);

function showPlexLogin() {{
  const f = document.getElementById('plex-login-form');
  f.style.display = f.style.display === 'none' ? 'block' : 'none';
}}

function generatePlexToken() {{
  const user = document.getElementById('plex-login-user').value.trim();
  const pass = document.getElementById('plex-login-pass').value;
  const fb   = document.getElementById('plex-token-fb');
  if (!user || !pass) {{ fb.textContent = 'User + Passwort fehlen'; fb.style.color='var(--red)'; return; }}
  fb.textContent = 'Verbinde mit plex.tv…';
  fb.style.color = 'var(--muted)';
  fetch(`/api/plex/token?username=${{encodeURIComponent(user)}}&password=${{encodeURIComponent(pass)}}`)
    .then(r=>r.json()).then(d=>{{
      if (d.ok) {{
        document.getElementById('rec-plex-token').value = d.token;
        fb.textContent = '✓ Token für ' + d.username;
        fb.style.color = 'var(--green)';
        document.getElementById('plex-login-form').style.display = 'none';
        document.getElementById('plex-login-pass').value = '';
      }} else {{
        fb.textContent = 'Fehler: ' + d.error;
        fb.style.color = 'var(--red)';
      }}
    }}).catch(e=>{{ fb.textContent = 'Netzwerk-Fehler'; fb.style.color='var(--red)'; }});
}}

function fetchPlexSections() {{
  const wrap = document.getElementById('plex-section-wrap');
  wrap.innerHTML = '<span style="color:var(--muted)">Lade…</span>';
  fetch('/api/plex/sections').then(r=>r.json()).then(d=>{{
    if (!d.ok) {{ wrap.innerHTML = `<span style="color:var(--red)">Fehler: ${{d.error}}</span>`; return; }}
    if (!d.sections.length) {{ wrap.innerHTML = '<span style="color:var(--muted)">Keine Sections</span>'; return; }}
    const liveIds = new Set(d.sections.map(s => s.id));
    let html = d.sections.map(s => {{
      const sel = _selectedSections.has(s.id);
      const icon = s.type === 'movie' ? '🎬' : s.type === 'show' ? '📺' : '📁';
      return `<div onclick="toggleSection('${{s.id}}')" id="sec-${{s.id}}"
        style="cursor:pointer;padding:4px 8px;border-radius:4px;margin:2px;display:inline-block;
        background:${{sel?'var(--accent)':'var(--surface3)'}};color:${{sel?'white':'var(--text)'}};user-select:none">
        ${{icon}} [${{s.id}}] ${{s.title}}</div>`;
    }}).join('');
    // Verwaiste Auswahl (Section existiert nicht mehr in Plex) entfernbar anzeigen
    const orphans = Array.from(_selectedSections).filter(id => !liveIds.has(id));
    html += orphans.map(id => `<div onclick="toggleSection('${{id}}')" id="sec-${{id}}" title="In Plex gelöscht — klicken zum Entfernen"
      style="cursor:pointer;padding:4px 8px;border-radius:4px;margin:2px;display:inline-block;
      background:var(--red);color:white;user-select:none">⚠ [${{id}}] gelöscht ✕</div>`).join('');
    wrap.innerHTML = html;
  }}).catch(()=>{{ wrap.innerHTML='<span style="color:var(--red)">Verbindung fehlgeschlagen</span>'; }});
}}

function toggleSection(id) {{
  if (_selectedSections.has(id)) {{ _selectedSections.delete(id); }}
  else {{ _selectedSections.add(id); }}
  const el = document.getElementById('sec-' + id);
  if (el) {{
    const wasOrphan = el.textContent.includes('gelöscht');
    if (wasOrphan && !_selectedSections.has(id)) {{
      // Verwaiste, jetzt abgewählte Section ausblenden
      el.remove();
    }} else {{
      el.style.background = _selectedSections.has(id) ? 'var(--accent)' : 'var(--surface3)';
      el.style.color = _selectedSections.has(id) ? 'white' : 'var(--text)';
    }}
  }}
  document.getElementById('rec-plex-section').value = Array.from(_selectedSections).join(',');
}}

function saveRecordingSettings() {{
  const fb = document.getElementById('rec-save-fb');
  const setFb = (msg, color) => {{ if (fb) {{ fb.textContent = msg; fb.style.color = color; fb.style.opacity = '1'; setTimeout(()=>{{ fb.style.opacity='0'; }}, 4000); }} }};
  setFb('Speichere…', 'var(--muted)');
  fetch('/api/config').then(r=>r.json()).then(cfg=>{{
    cfg.recordings_path        = document.getElementById('rec-path').value.trim();
    cfg.recordings_profile     = document.getElementById('rec-profile').value;
    cfg.recordings_max_duration= parseInt(document.getElementById('rec-max-dur').value)||10800;
    cfg.recordings_plex_url    = document.getElementById('rec-plex-url').value.trim();
    cfg.recordings_plex_token  = document.getElementById('rec-plex-token').value.trim();
    cfg.recordings_plex_section= document.getElementById('rec-plex-section').value.trim();
    cfg.recordings_plex_verify = document.getElementById('rec-plex-verify').checked;
    return apiPost('/api/config', cfg);
  }}).then(d=>{{
    if (d && d.ok) {{
      showToast('✓ Aufnahme-Einstellungen gespeichert', 'success');
      setFb('✓ Gespeichert', 'var(--green)');
    }} else {{
      showToast('Fehler beim Speichern', 'error');
      setFb('✕ Fehler', 'var(--red)');
    }}
  }}).catch(e=>{{
    showToast('Fehler beim Speichern', 'error');
    setFb('✕ Fehler: ' + e, 'var(--red)');
  }});
}}

// ── Umschalt-Tuning ───────────────────────────────────
function loadSwitchStats() {{
  fetch('/api/switch/stats').then(r=>r.json()).then(d=>{{
    const g = d.global || {{}};
    const set = (id,v)=>{{ const el=document.getElementById(id); if(el){{ if(el.type==='checkbox') el.checked=!!v; else el.value=v; }} }};
    set('sw-no-latency', g.no_latency);
    set('sw-zap-wait', g.zap_wait);
    set('sw-monitor', g.monitor_sec);
    set('sw-retries', g.max_retries);
    set('sw-nolat-probe', g.nolat_probesize);
    set('sw-fail-thresh', g.fail_threshold);
    const rows = d.senders || [];
    const box = document.getElementById('sw-stats');
    if (!rows.length) {{ box.innerHTML = '<div style="color:var(--muted);font-size:12px">Noch keine Daten — nach dem ersten Umschalten erscheinen hier Statistiken.</div>'; return; }}
    let html = '<table style="width:100%;border-collapse:collapse;font-size:11px;font-family:monospace">'
      + '<thead><tr style="text-align:left;color:var(--muted);border-bottom:1px solid var(--border)">'
      + '<th style="padding:4px 8px 4px 0">Sender</th><th style="padding:4px 8px">Zap ok/fail</th><th style="padding:4px 8px">⌀ms</th>'
      + '<th style="padding:4px 8px">Start ok/fail</th><th style="padding:4px 8px">Retries</th>'
      + '<th style="padding:4px 8px">NoLat-Fails</th><th style="padding:4px 8px">Probesize</th><th style="padding:4px 8px">NoLat</th><th></th></tr></thead><tbody>';
    for (const r of rows) {{
      const nl = r.no_latency===null||r.no_latency===undefined ? '—' : (r.no_latency?'an':'aus');
      const ps = r.probesize ? r.probesize.toLocaleString() : '—';
      const failColor = r.zap_fail>0 || r.start_fail>0 ? 'var(--red)' : 'var(--text)';
      html += `<tr style="border-bottom:1px solid var(--border)">`
        + `<td style="padding:4px 8px 4px 0;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${{r.name}}">${{r.name}}</td>`
        + `<td style="padding:4px 8px;color:${{failColor}}">${{r.zap_ok}}/${{r.zap_fail}}</td>`
        + `<td style="padding:4px 8px">${{r.zap_avg_ms}}</td>`
        + `<td style="padding:4px 8px;color:${{failColor}}">${{r.start_ok}}/${{r.start_fail}}</td>`
        + `<td style="padding:4px 8px">${{r.start_retries}}</td>`
        + `<td style="padding:4px 8px">${{r.nolatency_fail_total}}</td>`
        + `<td style="padding:4px 8px">${{ps}}</td>`
        + `<td style="padding:4px 8px">${{nl}}</td>`
        + `<td style="padding:4px 8px"><button class="btn" style="padding:2px 8px;font-size:10px" onclick="resetSwitch('${{encodeURIComponent(r.ref)}}')">🗑</button></td>`
        + `</tr>`;
    }}
    html += '</tbody></table>';
    box.innerHTML = html;
  }}).catch(e=>{{ const box=document.getElementById('sw-stats'); if(box) box.innerHTML='<div style="color:var(--red)">Fehler: '+e+'</div>'; }});
}}

function saveSwitchGlobal() {{
  const fb = document.getElementById('sw-save-fb');
  const setFb = (msg,color)=>{{ if(fb){{ fb.textContent=msg; fb.style.color=color; fb.style.opacity='1'; setTimeout(()=>{{fb.style.opacity='0';}},4000); }} }};
  setFb('Speichere…','var(--muted)');
  const g = {{
    no_latency: document.getElementById('sw-no-latency').checked,
    zap_wait_sec: parseFloat(document.getElementById('sw-zap-wait').value)||0,
    switch_monitor_sec: parseFloat(document.getElementById('sw-monitor').value)||10,
    switch_max_retries: parseInt(document.getElementById('sw-retries').value)||0,
    no_latency_probesize: parseInt(document.getElementById('sw-nolat-probe').value)||500000,
    nolatency_fail_threshold: parseInt(document.getElementById('sw-fail-thresh').value)||3,
  }};
  apiPost('/api/switch/settings', {{global: g}}).then(d=>{{
    if (d && d.ok) {{ showToast('✓ Umschalt-Einstellungen gespeichert','success'); setFb('✓ Gespeichert','var(--green)'); }}
    else {{ showToast('Fehler beim Speichern','error'); setFb('✕ Fehler','var(--red)'); }}
  }}).catch(e=>{{ showToast('Fehler beim Speichern','error'); setFb('✕ Fehler: '+e,'var(--red)'); }});
}}

function resetSwitch(ref) {{
  const body = ref ? {{ref: decodeURIComponent(ref)}} : {{}};
  if (!ref && !confirm('Wirklich ALLE gelernten Umschalt-Werte und Statistiken zurücksetzen?')) return;
  apiPost('/api/switch/reset', body).then(d=>{{
    if (d && d.ok) {{ showToast('✓ Zurückgesetzt','success'); loadSwitchStats(); }}
    else showToast('Fehler','error');
  }}).catch(()=>showToast('Fehler','error'));
}}

// ── Switch-Analyse (Profil-Empfehlungen) ──────────────
function renderSwitchAnalyze(d, applied) {{
  const box = document.getElementById('sw-analyze-box');
  if (!box) return;
  const recs = d.recommendations || [];
  if (applied) {{
    box.innerHTML = `<div style="padding:8px 10px;border:1px solid var(--green);border-radius:6px;color:var(--green);font-size:12px">✓ ${{d.applied}} Profil(e) angewendet.</div>`;
    return;
  }}
  if (!recs.length) {{
    box.innerHTML = `<div style="padding:8px 10px;border:1px solid var(--border);border-radius:6px;color:var(--muted);font-size:12px">Keine Verbesserungen gefunden (min. ${{d.min_samples}} Umschaltvorgänge pro Sender nötig). Alle Profile wirken passend.</div>`;
    return;
  }}
  const fmt = r => `NoLat ${{r.no_latency?'an':'aus'}}, Wait ${{r.zap_wait}}s, Probe ${{r.probesize.toLocaleString()}}`;
  let html = '<div style="border:1px solid var(--accent);border-radius:6px;padding:10px">'
    + `<div style="font-size:12px;margin-bottom:8px">🔍 <b>${{recs.length}}</b> Empfehlung(en) — Vorschau (noch nicht angewendet):</div>`
    + '<table style="width:100%;border-collapse:collapse;font-size:11px;font-family:monospace">'
    + '<thead><tr style="text-align:left;color:var(--muted);border-bottom:1px solid var(--border)">'
    + '<th style="padding:4px 8px 4px 0">Sender</th><th style="padding:4px 8px">Aktuell</th>'
    + '<th style="padding:4px 8px">Empfohlen</th><th style="padding:4px 8px">Grund</th></tr></thead><tbody>';
  for (const r of recs) {{
    html += `<tr style="border-bottom:1px solid var(--border)">`
      + `<td style="padding:4px 8px 4px 0;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${{r.name}}">${{r.name}}</td>`
      + `<td style="padding:4px 8px;color:var(--muted)">${{fmt(r.current)}}</td>`
      + `<td style="padding:4px 8px;color:var(--accent)">${{fmt(r.recommended)}}</td>`
      + `<td style="padding:4px 8px;color:var(--muted)">${{(r.reasons||[]).join('; ')}}</td>`
      + `</tr>`;
  }}
  html += '</tbody></table>'
    + '<div style="margin-top:10px;display:flex;gap:8px">'
    + '<button class="btn btn-primary" onclick="applySwitchAnalyze()">✓ <span data-i18n="set.switch_analyze_apply">Anwenden</span></button>'
    + '<button class="btn" onclick="clearSwitchAnalyze()">✕ <span data-i18n="common.cancel">Cancel</span></button>'
    + '</div></div>';
  box.innerHTML = html;
  // Dynamisch eingefuegtes Markup uebersetzen (applyI18n laeuft sonst nur beim Laden)
  if (typeof applyI18n === 'function') applyI18n();
}}

function analyzeSwitch() {{
  const box = document.getElementById('sw-analyze-box');
  if (box) box.innerHTML = '<div style="color:var(--muted);font-size:12px">Analysiere…</div>';
  apiPost('/api/switch/analyze', {{apply:false}})
    .then(d=>renderSwitchAnalyze(d, false))
    .catch(e=>{{ if(box) box.innerHTML='<div style="color:var(--red)">Fehler: '+e+'</div>'; }});
}}

function clearSwitchAnalyze() {{
  const box = document.getElementById('sw-analyze-box');
  if (box) box.innerHTML = '';
}}

function applySwitchAnalyze() {{
  apiPost('/api/switch/analyze', {{apply:true}}).then(d=>{{
    if (d && d.ok) {{ showToast('✓ '+d.applied+' Profile angewendet','success'); renderSwitchAnalyze(d, true); loadSwitchStats(); }}
    else showToast('Fehler beim Anwenden','error');
  }}).catch(()=>showToast('Fehler beim Anwenden','error'));
}}

function savePlexDvrRefresh(enabled) {{
  const fb = document.getElementById('plex-dvr-refresh-fb');
  const showFb = (msg, color) => {{
    if (!fb) return;
    fb.textContent = msg; fb.style.color = color; fb.style.opacity = '1';
    setTimeout(() => {{ fb.style.opacity = '0'; }}, 3000);
  }};
  apiPost('/api/config-update', {{recordings_plex_dvr_refresh: !!enabled}}).then(d => {{
    if (d && d.ok) showFb(enabled ? '✓ Auto-Refresh an' : '✓ Auto-Refresh aus', 'var(--green)');
    else showFb('✕ Fehler', 'var(--red)');
  }}).catch(e => showFb('✕ Fehler: ' + e, 'var(--red)'));
}}

function triggerPlexDvrRefresh() {{
  const btn = document.getElementById('plex-dvr-refresh-btn');
  const fb = document.getElementById('plex-dvr-refresh-fb');
  const showFb = (msg, color) => {{
    if (!fb) return;
    fb.textContent = msg; fb.style.color = color; fb.style.opacity = '1';
    setTimeout(() => {{ fb.style.opacity = '0'; }}, 4000);
  }};
  if (btn) btn.disabled = true;
  fetch('/api/plex/dvr-refresh').then(r => r.json()).then(d => {{
    if (d && d.ok) {{
      showToast('✓ Plex Guide-Refresh gestartet', 'success');
      showFb('✓ ' + (d.message || 'gestartet'), 'var(--green)');
    }} else {{
      showToast((d && d.message) || 'Fehler', 'error');
      showFb('✕ ' + ((d && d.message) || 'Fehler'), 'var(--red)');
    }}
  }}).catch(e => {{
    showToast('Fehler: ' + e, 'error');
    showFb('✕ Fehler: ' + e, 'var(--red)');
  }}).finally(() => {{ if (btn) btn.disabled = false; }});
}}

// ── Konfig CRUD ───────────────────────────────────────
function showModal(title, fields, onSave) {{
  let existing = document.getElementById('crud-modal');
  if (existing) existing.remove();
  const inputs = fields.map(f => {{
    if (f.type === 'checkbox') return `<label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text)"><input type="checkbox" id="f-${{f.key}}" ${{f.value ? 'checked' : ''}}> ${{f.label}}</label>`;
    if (f.type === 'select') return `<div class="field"><label class="label">${{f.label}}</label><select class="input" id="f-${{f.key}}">${{f.options.map(o => `<option value="${{o}}" ${{o===f.value?'selected':''}}>${{o}}</option>`).join('')}}</select></div>`;
    return `<div class="field"><label class="label">${{f.label}}</label><input class="input" id="f-${{f.key}}" value="${{f.value||''}}" placeholder="${{f.placeholder||''}}"></div>`;
  }}).join('');
  const modal = document.createElement('div');
  modal.id = 'crud-modal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:1000;display:flex;align-items:center;justify-content:center;';
  modal.innerHTML = `<div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:24px;width:420px;max-height:80vh;overflow-y:auto;">
    <div style="font-family:monospace;font-weight:700;font-size:13px;color:var(--accent2);margin-bottom:16px">${{title}}</div>
    ${{inputs}}
    <div style="display:flex;gap:8px;margin-top:16px">
      <button class="btn btn-primary" onclick="(async()=>{{await onSaveCallback();document.getElementById('crud-modal').remove();}})()">💾 <span data-i18n="common.save">Save</span></button>
      <button class="btn" onclick="document.getElementById('crud-modal').remove()">Abbrechen</button>
    </div>
  </div>`;
  window.onSaveCallback = onSave;
  document.body.appendChild(modal);
}}

function getVal(key, isCheck=false) {{
  const el = document.getElementById('f-'+key);
  return isCheck ? el.checked : el.value.trim();
}}

async function patchConfig(patcher) {{
  const r = await fetch('/api/config');
  const cfg = await r.json();
  patcher(cfg);
  const res = await apiPost('/api/config', cfg);
  if (res.ok) {{ showToast('✓ Gespeichert', 'success'); setTimeout(()=>location.reload(),800); }}
  else showToast('Fehler: ' + (res.message||'?'), 'error');
}}

// ── Receiver ──────────────────────────────────────────
function addReceiver() {{
  showModal('Receiver hinzufügen', [
    {{key:'id', label:'ID (eindeutig)', placeholder:'z.B. wohnzimmer'}},
    {{key:'name', label:'Name', placeholder:'z.B. Wohnzimmer'}},
    {{key:'ip', label:'IP-Adresse', placeholder:'192.168.88.xxx'}},
    {{key:'port', label:'Web-Port', placeholder:'80', value:'80'}},
    {{key:'stream_port', label:'Stream-Port', placeholder:'8001', value:'8001'}},
    {{key:'enabled', label:'Aktiv', type:'checkbox', value:true}},
    {{key:'locked', label:'Gesperrt (Tuner nicht verwenden)', type:'checkbox', value:false}},
    {{key:'default', label:'Default-Receiver', type:'checkbox', value:false}},
  ], async () => {{
    const rx = {{id:getVal('id'),name:getVal('name'),ip:getVal('ip'),port:parseInt(getVal('port')),stream_port:parseInt(getVal('stream_port')),enabled:getVal('enabled',true),locked:getVal('locked',false),default:getVal('default',true)}};
    await patchConfig(cfg => cfg.receivers.push(rx));
  }});
}}

function editReceiver(r) {{
  showModal('Receiver bearbeiten: ' + r.id, [
    {{key:'name', label:'Name', value:r.name}},
    {{key:'ip', label:'IP-Adresse', value:r.ip}},
    {{key:'port', label:'Web-Port', value:r.port||80}},
    {{key:'stream_port', label:'Stream-Port', value:r.stream_port||8001}},
    {{key:'enabled', label:'Aktiv', type:'checkbox', value:r.enabled!==false}},
    {{key:'locked', label:'Gesperrt (Tuner nicht verwenden)', type:'checkbox', value:!!r.locked}},
    {{key:'default', label:'Default-Receiver', type:'checkbox', value:!!r.default}},
  ], async () => {{
    await patchConfig(cfg => {{
      const idx = cfg.receivers.findIndex(x=>x.id===r.id);
      if (idx>=0) cfg.receivers[idx] = {{...cfg.receivers[idx], name:getVal('name'), ip:getVal('ip'), port:parseInt(getVal('port')), stream_port:parseInt(getVal('stream_port')), enabled:getVal('enabled',true), locked:getVal('locked',false), default:getVal('default',true)}};
    }});
  }});
}}

function deleteReceiver(id) {{
  if (!confirm('Receiver "'+id+'" löschen?')) return;
  patchConfig(cfg => cfg.receivers = cfg.receivers.filter(r=>r.id!==id));
}}

// ── Transcode-Profile ─────────────────────────────────
const CODECS = ['pass','remux','remux-ac3','vp8','vp9','h264'];
const CONTAINERS = ['mpegts','webm','mp4'];

function addTranscodeProfile() {{
  showModal('Transcode-Profil hinzufügen', [
    {{key:'id', label:'ID (eindeutig)', placeholder:'z.B. remux-hevc'}},
    {{key:'label', label:'Label', placeholder:'Anzeigename'}},
    {{key:'codec', label:'Codec', type:'select', options:CODECS, value:'remux'}},
    {{key:'container', label:'Container', type:'select', options:CONTAINERS, value:'mpegts'}},
    {{key:'abitrate', label:'Audio-Bitrate', placeholder:'192k', value:'192k'}},
    {{key:'vbitrate', label:'Video-Bitrate (optional)', placeholder:'z.B. 4000k'}},
    {{key:'height', label:'Höhe in Pixel (optional)', placeholder:'z.B. 720'}},
  ], async () => {{
    const id = getVal('id');
    const tp = {{label:getVal('label'),codec:getVal('codec'),container:getVal('container'),abitrate:getVal('abitrate')}};
    const vb = getVal('vbitrate'); if(vb) tp.vbitrate=vb;
    const h = getVal('height'); if(h) tp.height=parseInt(h);
    await patchConfig(cfg => cfg.transcode_profiles[id] = tp);
  }});
}}

function editTranscodeProfile(tp) {{
  showModal('Transcode-Profil: ' + tp.id, [
    {{key:'label', label:'Label', value:tp.label||tp.id}},
    {{key:'codec', label:'Codec', type:'select', options:CODECS, value:tp.codec}},
    {{key:'container', label:'Container', type:'select', options:CONTAINERS, value:tp.container}},
    {{key:'abitrate', label:'Audio-Bitrate', value:tp.abitrate||''}},
    {{key:'vbitrate', label:'Video-Bitrate', value:tp.vbitrate||''}},
    {{key:'height', label:'Höhe px', value:tp.height||''}},
  ], async () => {{
    const updated = {{label:getVal('label'),codec:getVal('codec'),container:getVal('container'),abitrate:getVal('abitrate')}};
    const vb=getVal('vbitrate'); if(vb) updated.vbitrate=vb;
    const h=getVal('height'); if(h) updated.height=parseInt(h);
    await patchConfig(cfg => cfg.transcode_profiles[tp.id] = updated);
  }});
}}

function deleteTranscodeProfile(id) {{
  if (!confirm('Profil "'+id+'" löschen?')) return;
  patchConfig(cfg => delete cfg.transcode_profiles[id]);
}}

// ── Device-Profile ────────────────────────────────────
function addDeviceProfile() {{
  fetch('/api/config').then(r=>r.json()).then(cfg => {{
    const tpOpts = Object.keys(cfg.transcode_profiles||{{}});
    showModal('Device-Profil hinzufügen', [
      {{key:'id', label:'ID (eindeutig)', placeholder:'z.B. kodi'}},
      {{key:'label', label:'Label', placeholder:'Anzeigename'}},
      {{key:'transcode_profile', label:'Transcode-Profil', type:'select', options:tpOpts, value:tpOpts[0]}},
      {{key:'short_url', label:'Kurz-URL', placeholder:'z.B. kodi'}},
      {{key:'description', label:'Beschreibung', placeholder:'optional'}},
      {{key:'chunked_http', label:'HTTP/1.1 chunked (für Plex)', type:'checkbox', value:false}},
    ], async () => {{
      const dp = {{label:getVal('label'),transcode_profile:getVal('transcode_profile'),receiver:'auto',short_url:getVal('short_url'),description:getVal('description'),chunked_http:getVal('chunked_http',true)}};
      await patchConfig(cfg2 => cfg2.device_profiles[getVal('id')] = dp);
    }});
  }});
}}

function editDeviceProfile(dp) {{
  fetch('/api/config').then(r=>r.json()).then(cfg => {{
    const tpOpts = Object.keys(cfg.transcode_profiles||{{}});
    showModal('Device-Profil: ' + dp.id, [
      {{key:'label', label:'Label', value:dp.label||dp.id}},
      {{key:'transcode_profile', label:'Transcode-Profil', type:'select', options:tpOpts, value:dp.transcode_profile}},
      {{key:'short_url', label:'Kurz-URL', value:dp.short_url||''}},
      {{key:'description', label:'Beschreibung', value:dp.description||''}},
      {{key:'chunked_http', label:'HTTP/1.1 chunked', type:'checkbox', value:!!dp.chunked_http}},
    ], async () => {{
      await patchConfig(cfg2 => {{
        cfg2.device_profiles[dp.id] = {{...cfg2.device_profiles[dp.id], label:getVal('label'), transcode_profile:getVal('transcode_profile'), short_url:getVal('short_url'), description:getVal('description'), chunked_http:getVal('chunked_http',true)}};
      }});
    }});
  }});
}}

function deleteDeviceProfile(id) {{
  if (!confirm('Device-Profil "'+id+'" löschen?')) return;
  patchConfig(cfg => delete cfg.device_profiles[id]);
}}

// ── EPG Tab ─────────────────────────────────────────
let epgPollTimer = null;

// EPG History Chart laden
function loadEpgHistory() {{
  fetch('/api/epg/history').then(r=>r.json()).then(d=>{{
    if (!d.ok || !d.runs.length) {{
      document.getElementById('epg-history-chart').innerHTML =
        '<span style="color:var(--muted);font-size:11px;font-family:monospace">Noch keine Run-Daten vorhanden.</span>';
      return;
    }}
    const runs = d.runs.slice(-30);
    const durations = runs.map(r=>r.duration_sec);
    const avg = durations.reduce((a,b)=>a+b,0) / durations.length;
    const maxD = Math.max(...durations);
    const W = 600, H = 80, PAD = 4;
    const barW = Math.floor((W - PAD*(runs.length+1)) / runs.length);
    let bars = '', labels = '';
    runs.forEach((run, i) => {{
      const isOutlier = run.duration_sec > avg * 2;
      const barH = Math.max(4, Math.round((run.duration_sec / maxD) * (H - 20)));
      const x = PAD + i * (barW + PAD);
      const y = H - 16 - barH;
      const color = isOutlier ? 'var(--red)' : 'var(--accent)';
      const date = run.ts ? run.ts.substring(5,16).replace('T',' ') : '';
      bars += `<rect x="${{x}}" y="${{y}}" width="${{barW}}" height="${{barH}}"
        fill="${{color}}" opacity="0.8" rx="2" title="${{date}}: ${{run.duration_sec}}s">
        <title>${{date}} (${{run.trigger}}): ${{run.duration_sec}}s</title></rect>`;
      // Label alle 5 Einträge
      if (i % 5 === 0 || i === runs.length-1) {{
        labels += `<text x="${{x + barW/2}}" y="${{H - 2}}" text-anchor="middle"
          font-size="7" fill="var(--muted)">${{date.substring(0,5)}}</text>`;
      }}
    }});
    // Durchschnitts-Linie
    const avgY = H - 16 - Math.round((avg / maxD) * (H - 20));
    const avgLine = `<line x1="0" y1="${{avgY}}" x2="${{W}}" y2="${{avgY}}"
      stroke="var(--amber)" stroke-width="1" stroke-dasharray="3,3" opacity="0.6"/>
      <text x="${{W-2}}" y="${{avgY-2}}" text-anchor="end" font-size="7" fill="var(--amber)">⌀${{Math.round(avg)}}s</text>`;
    const svg = `<svg viewBox="0 0 ${{W}} ${{H}}" xmlns="http://www.w3.org/2000/svg"
      style="width:100%;height:${{H}}px;display:block">
      ${{bars}}${{avgLine}}${{labels}}
    </svg>`;
    // Statistik
    const minD = Math.min(...durations);
    const outliers = durations.filter(d=>d>avg*2).length;
    const stats = `<div style="font-family:monospace;font-size:10px;color:var(--muted);margin-top:6px;display:flex;gap:16px">
      <span>Min: <b style="color:var(--green)">${{minD}}s</b></span>
      <span>Ø: <b style="color:var(--amber)">${{Math.round(avg)}}s</b></span>
      <span>Max: <b style="color:var(--red)">${{maxD}}s</b></span>
      <span>${{t('epg.outliers')}}: <b style="color:var(--red)">${{outliers}}</b></span>
      <span>Runs: <b>${{runs.length}}</b></span>
    </div>`;
    document.getElementById('epg-history-chart').innerHTML = svg + stats;
  }}).catch(()=>{{
    document.getElementById('epg-history-chart').innerHTML =
      '<span style="color:var(--muted);font-size:11px;font-family:monospace">Fehler beim Laden.</span>';
  }});
}}

// History laden wenn EPG-Tab aktiv
if (document.getElementById('tab-epg').classList.contains('active')) loadEpgHistory();

function startEpgRun() {{
  fetch('/api/epg/run').then(r => r.json()).then(d => {{
    document.getElementById('epg-run-fb').textContent = d.message || '';
    if (d.ok) startEpgPolling();
  }});
}}

function startEpgPolling() {{
  if (epgPollTimer) clearInterval(epgPollTimer);
  pollEpgStatus();
  epgPollTimer = setInterval(pollEpgStatus, 2000);
}}

function pollEpgStatus() {{
  fetch('/api/epg/status').then(r => r.json()).then(d => {{
    document.getElementById('epg-phase').textContent = d.phase || '—';
    const pct = d.progress || 0;
    document.getElementById('epg-bar-text').textContent = pct + '%';

    // Zweistufiger Fortschrittsbalken
    const receiverPct = Math.min(pct, 20);
    const tmdbPct = Math.max(0, pct - 20);
    document.getElementById('epg-bar-receiver').style.width = receiverPct + '%';
    document.getElementById('epg-bar-tmdb').style.width = (receiverPct + tmdbPct) + '%';

    const btn = document.getElementById('epg-run-btn');
    btn.disabled = d.running;
    btn.textContent = d.running ? '⏳ Läuft…' : '▶ Jetzt aktualisieren';

    // Auto-Polling starten wenn läuft aber kein Timer aktiv
    if (d.running && !epgPollTimer) {{
      epgPollTimer = setInterval(pollEpgStatus, 2000);
    }}

    // TMDB Counter + Live-Liste
    const total = d.tmdb_total || 0;
    const done = d.tmdb_done || 0;
    const counter = document.getElementById('epg-tmdb-counter');
    const listWrap = document.getElementById('epg-tmdb-list-wrap');
    const list = document.getElementById('epg-tmdb-list');

    if (total > 0 || (d.tmdb_all && d.tmdb_all.length > 0)) {{
      counter.textContent = total > 0 ? `🎬 ${{done}}/${{total}} Poster` : '';
      listWrap.style.display = 'block';

      if (d.running && d.tmdb_items && d.tmdb_items.length) {{
        // Während dem Run: letzte 10 Items live
        const visible = d.tmdb_items.slice(-10).reverse();
        list.innerHTML = visible.map(item => {{
          let icon, color;
          if (item.status === 'found')      {{ icon = '✅'; color = 'var(--green)'; }}
          else if (item.status === 'logo')  {{ icon = '📺'; color = 'var(--accent2)'; }}
          else if (item.status === 'not_found') {{ icon = '⬜'; color = 'var(--muted)'; }}
          else {{ icon = '⬜'; color = 'var(--text)'; }}
          return `<div style="color:${{color}};display:flex;gap:6px;align-items:center">
            <span style="flex-shrink:0">${{icon}}</span>
            <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${{item.title}}</span>
          </div>`;
        }}).join('');
      }} else if (!d.running && d.tmdb_all && d.tmdb_all.length > 0) {{
        // Nach dem Run: vollständige Zusammenfassung
        const all = d.tmdb_all;
        const found = all.filter(i => i.status === 'found');
        const logo  = all.filter(i => i.status === 'logo');
        const none  = all.filter(i => i.status === 'not_found');
        counter.textContent = `✅ ${{found.length}} Poster · 📺 ${{logo.length}} Sender-Logo · ⬜ ${{none.length}} nicht gefunden`;
        let html = '';
        if (found.length) {{
          html += `<div style="color:var(--green);font-weight:600;margin-bottom:4px">✅ Poster gefunden (${{found.length}})</div>`;
          html += found.map(i => `<div style="color:var(--green);padding-left:16px">✅ ${{i.title}}</div>`).join('');
        }}
        if (logo.length) {{
          html += `<div style="color:var(--accent2);font-weight:600;margin-top:8px;margin-bottom:4px">📺 Sender-Logo Fallback (${{logo.length}})</div>`;
          html += logo.map(i => `<div style="color:var(--accent2);padding-left:16px">📺 ${{i.title}}</div>`).join('');
        }}
        if (none.length) {{
          html += `<div style="color:var(--muted);font-weight:600;margin-top:8px;margin-bottom:4px">⬜ Kein Bild gefunden (${{none.length}})</div>`;
          html += none.map(i => `<div style="color:var(--muted);padding-left:16px">⬜ ${{i.title}}</div>`).join('');
        }}
        list.innerHTML = html;
      }}
    }}

    if (d.log && d.log.length) {{
      const box = document.getElementById('epg-log');
      box.innerHTML = d.log.map(l => {{
        let cls = 'log-info';
        if (l.includes('FEHLER') || l.includes('Fehler')) cls = 'log-error';
        return '<div class="' + cls + '">' + l.replace(/</g,'&lt;') + '</div>';
      }}).join('');
      box.scrollTop = box.scrollHeight;
    }}
    if (d.last_run) {{
      document.getElementById('epg-last-run').textContent = new Date(d.last_run).toLocaleString('de-DE');
      document.getElementById('epg-last-dur').textContent = d.last_duration + ' ' + t('rec.seconds');
      document.getElementById('epg-last-result').textContent = d.last_result || '—';
    }}
    if (d.schedule_hour !== undefined) {{
      const hourInput = document.getElementById('epg-hour');
      if (document.activeElement !== hourInput) hourInput.value = d.schedule_hour;
    }}
    if (!d.running && epgPollTimer) {{ clearInterval(epgPollTimer); epgPollTimer = null; }}
  }}).catch(()=>{{}});
}}

function saveEpgSchedule() {{
  const hour = parseInt(document.getElementById('epg-hour').value);
  apiPost('/api/epg/schedule', {{hour: hour}}).then(d => {{
    document.getElementById('epg-sched-fb').textContent = d.ok ? ('Gespeichert: ' + d.hour + ':00 ✓') : 'Fehler';
  }});
}}

// ── Maintenance Notifications ──────────────────────────────
let _mnInitDone = false;
function mnBuildDays(selected) {{
  const wrap = document.getElementById('mn-days');
  if (!wrap) return;
  const sel = new Set(selected || []);
  const lang = (document.documentElement.lang || navigator.language || 'en');
  // weekday() in Python: 0=Mon .. 6=Sun
  const base = new Date(Date.UTC(2024, 0, 1)); // 2024-01-01 was a Monday
  let html = '';
  for (let i = 0; i < 7; i++) {{
    const d = new Date(base.getTime() + i * 86400000);
    const label = d.toLocaleDateString(lang, {{weekday: 'short'}});
    const on = sel.has(i);
    html += '<button type="button" class="btn mn-day' + (on ? ' btn-primary' : '') + '" data-day="' + i + '" onclick="mnToggleDay(' + i + ')" style="min-width:48px">' + label + '</button>';
  }}
  wrap.innerHTML = html;
}}
function mnToggleDay(i) {{
  const btn = document.querySelector('.mn-day[data-day="' + i + '"]');
  if (btn) btn.classList.toggle('btn-primary');
}}
function mnGetDays() {{
  return Array.from(document.querySelectorAll('.mn-day.btn-primary')).map(b => parseInt(b.dataset.day));
}}
function initMaintNotify() {{
  initRxAlerts();
  if (_mnInitDone) return;
  _mnInitDone = true;
  const nc = (ORIGINAL_CONFIG && ORIGINAL_CONFIG.maintenance_notifications) || {{}};
  document.getElementById('mn-enabled').checked = !!nc.enabled;
  document.getElementById('mn-url').value = nc.url || '';
  document.getElementById('mn-method').value = (nc.method === 'GET') ? 'GET' : 'POST';
  document.getElementById('mn-hour').value = (nc.hour != null) ? nc.hour : 4;
  document.getElementById('mn-minute').value = (nc.minute != null) ? nc.minute : 0;
  document.getElementById('mn-idle').value = (nc.idle_mode === 'idle_only') ? 'idle_only' : 'always';
  mnBuildDays(Array.isArray(nc.days) ? nc.days : [0,1,2,3,4,5,6]);
}}
function saveMaintNotify() {{
  const fb = document.getElementById('mn-fb');
  fb.textContent = '…';
  const payload = {{
    enabled: document.getElementById('mn-enabled').checked,
    url: document.getElementById('mn-url').value.trim(),
    method: document.getElementById('mn-method').value,
    hour: parseInt(document.getElementById('mn-hour').value) || 0,
    minute: parseInt(document.getElementById('mn-minute').value) || 0,
    days: mnGetDays(),
    idle_mode: document.getElementById('mn-idle').value,
  }};
  apiPost('/api/maintenance/notify', payload).then(d => {{
    fb.textContent = d.ok ? '✓ Gespeichert' : ('✗ ' + (d.message || 'Fehler'));
    if (d.ok && d.config) ORIGINAL_CONFIG.maintenance_notifications = d.config;
  }}).catch(() => {{ fb.textContent = '✗ Fehler'; }});
}}
function testMaintNotify() {{
  const fb = document.getElementById('mn-fb');
  fb.textContent = 'Test wird gesendet…';
  apiPost('/api/maintenance/notify/test', {{}}).then(d => {{
    fb.textContent = (d.ok ? '✓ ' : '✗ ') + (d.message || '');
  }}).catch(() => {{ fb.textContent = '✗ Fehler'; }});
}}

let _raInitDone = false;
function initRxAlerts() {{
  if (_raInitDone || !document.getElementById('ra-enabled')) return;
  _raInitDone = true;
  const ac = (ORIGINAL_CONFIG && ORIGINAL_CONFIG.receiver_alerts) || {{}};
  document.getElementById('ra-enabled').checked = (ac.enabled !== false);
  document.getElementById('ra-url').value = ac.url || '';
  document.getElementById('ra-method').value = (ac.method === 'GET') ? 'GET' : 'POST';
  document.getElementById('ra-after').value = (ac.after_min != null) ? ac.after_min : 10;
}}
function saveRxAlerts() {{
  const fb = document.getElementById('ra-fb');
  fb.textContent = '…';
  const payload = {{
    enabled: document.getElementById('ra-enabled').checked,
    url: document.getElementById('ra-url').value.trim(),
    method: document.getElementById('ra-method').value,
    after_min: parseInt(document.getElementById('ra-after').value) || 10,
  }};
  apiPost('/api/receiver-alerts', payload).then(d => {{
    fb.textContent = d.ok ? '✓ Gespeichert' : ('✗ ' + (d.message || 'Fehler'));
    if (d.ok && d.config) ORIGINAL_CONFIG.receiver_alerts = d.config;
  }}).catch(() => {{ fb.textContent = '✗ Fehler'; }});
}}
function _ifEsc(s) {{ return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}})[c]); }}
function _ifAgo(ts) {{
  if (!ts) return '–';
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 90) return s + ' s';
  if (s < 5400) return Math.round(s / 60) + ' min';
  if (s < 172800) return Math.round(s / 3600) + ' h';
  return Math.round(s / 86400) + ' d';
}}
function renderInterfaces(d) {{
  const tb = document.querySelector('#if-table tbody');
  const sum = document.getElementById('if-summary');
  if (!tb) return;
  if (!d || d.available === false) {{
    tb.innerHTML = '<tr><td style="color:var(--muted)">' + _ifEsc(t('if.unavailable')) + '</td></tr>';
    sum.textContent = '';
    return;
  }}
  const col = {{ok:'var(--green,#3fb950)', warn:'var(--yellow,#d29922)', down:'var(--red,#f85149)', unknown:'var(--muted)'}};
  const order = {{down:0, warn:1, unknown:2, ok:3}};
  const probes = (d.probes || []).slice().sort((a, b) => (a.group || '').localeCompare(b.group || '') || 0);
  let html = '<tr style="color:var(--muted);text-align:left"><th></th><th>' + t('if.col_group') + '</th><th>' + t('if.col_probe') + '</th><th>ms</th><th>' + t('if.col_detail') + '</th><th>' + t('if.col_since') + '</th></tr>';
  for (const p of probes) {{
    html += '<tr style="border-top:1px solid var(--border)">'
      + '<td style="padding:4px"><span title="' + _ifEsc(p.state) + '" style="display:inline-block;width:9px;height:9px;border-radius:50%;background:' + (col[p.state] || col.unknown) + '"></span></td>'
      + '<td style="padding:4px;white-space:nowrap">' + _ifEsc(p.group) + '</td>'
      + '<td style="padding:4px;white-space:nowrap">' + _ifEsc(p.label) + '</td>'
      + '<td style="padding:4px;font-family:monospace">' + (p.ms != null ? p.ms : '–') + '</td>'
      + '<td style="padding:4px;font-family:monospace;word-break:break-word">' + _ifEsc(p.detail) + '</td>'
      + '<td style="padding:4px;white-space:nowrap">' + _ifAgo(p.since) + '</td></tr>';
  }}
  tb.innerHTML = html;
  const st = d.state || 'unknown';
  sum.innerHTML = '<span style="color:' + (col[st] || col.unknown) + ';font-weight:600">● ' + _ifEsc(t('if.state_' + st)) + '</span>'
    + (d.checked_at ? ' <span style="color:var(--muted)">· ' + t('if.checked') + ' ' + _ifAgo(d.checked_at) + '</span>' : '');
}}
function loadInterfaces() {{
  fetch('/api/interfaces').then(r => r.json()).then(renderInterfaces).catch(() => {{}});
}}
function checkInterfaces() {{
  const fb = document.getElementById('if-fb');
  fb.textContent = t('if.checking');
  apiPost('/api/interfaces/check', {{}}).then(d => {{
    fb.textContent = d.ok ? '' : ('✗ ' + (d.message || ''));
    if (d.interfaces) renderInterfaces(d.interfaces);
  }}).catch(() => {{ fb.textContent = '✗ Fehler'; }});
}}
function streamTestInterface(kind) {{
  if (!confirm(t('if.test_confirm'))) return;
  const fb = document.getElementById('if-fb');
  fb.textContent = t('if.testing');
  apiPost('/api/interfaces/stream-test', {{kind: kind}}).then(d => {{
    let msg = (d.ok ? '✓ ' : '✗ ') + (d.channel ? d.channel + ': ' : '') + (d.detail || '');
    if (d.ttfb_ms != null) msg += ' · TTFB ' + d.ttfb_ms + ' ms';
    if (d.kbps) msg += ' · ' + d.kbps + ' kbit/s';
    fb.textContent = msg;
  }}).catch(() => {{ fb.textContent = '✗ Fehler'; }});
}}

function testRxAlerts() {{
  const fb = document.getElementById('ra-fb');
  fb.textContent = 'Test wird gesendet…';
  apiPost('/api/receiver-alerts/test', {{}}).then(d => {{
    fb.textContent = (d.ok ? '✓ ' : '✗ ') + (d.message || '');
  }}).catch(() => {{ fb.textContent = '✗ Fehler'; }});
}}

function checkLogos() {{
  const fb = document.getElementById('logo-check-fb');
  fb.textContent = 'Prüfe Logos…';
  fetch('/api/logos/check').then(r => r.json()).then(d => {{
    fb.textContent = d.count === 0 ? 'Alle Logos OK ✓' : (d.count + ' Logo(s) mit Problemen');
    const box = document.getElementById('logo-broken');
    if (d.broken && d.broken.length) {{
      box.innerHTML = d.broken.map(b => '<div style="font-size:11px;color:var(--amber);font-family:monospace">⚠ ' + b.name + ' — ' + b.reason + '</div>').join('');
    }} else {{
      box.innerHTML = '';
    }}
  }}).catch(() => {{ fb.textContent = '✗ Fehler'; }});
}}

// ── Favoriten-Logos bearbeiten ─────────────────────────────
function loadFavLogos() {{
  const box = document.getElementById('fav-logo-list');
  if (!box) return;
  const fb = document.getElementById('fav-logo-fb');
  if (fb) fb.textContent = '';
  fetch('/api/favorites/logos').then(r => r.json()).then(d => {{
    if (!d.ok || !d.logos || !d.logos.length) {{
      box.innerHTML = '<span style="color:var(--muted);font-size:11px;font-family:monospace">' + t('set.fav_logos_empty') + '</span>';
      return;
    }}
    box.innerHTML = d.logos.map(function(l) {{
      const nm = String(l.name).replace(/"/g, '&quot;');
      const badge = l.custom ? '<span style="font-size:9px;color:var(--accent);border:1px solid var(--accent);border-radius:3px;padding:1px 4px;margin-left:6px">' + t('set.fav_logos_custom') + '</span>' : '';
      const resetBtn = l.custom ? '<button class="btn" style="font-size:10px" data-name="' + nm + '" onclick="resetFavLogo(this)">↺ ' + t('set.reset') + '</button>' : '';
      const img = l.logo_url
        ? '<img src="' + l.logo_url + '" title="' + t('set.fav_logos_zoom') + '" onclick="showLogoModal(this.src, this.dataset.nm)" data-nm="' + nm + '" style="width:52px;height:32px;object-fit:contain;background:var(--surface2);border:1px solid var(--border);border-radius:4px;cursor:zoom-in" onerror="this.style.opacity=0.2">'
        : '<div style="width:52px;height:32px;background:var(--surface2);border:1px solid var(--border);border-radius:4px"></div>';
      return '<div class="fav-logo-row" data-name="' + nm + '" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;border:1px solid var(--border);border-radius:6px;padding:6px 8px;background:var(--surface)">' +
        img +
        '<div style="min-width:150px;flex:0 0 auto;font-size:12px">' + l.name + badge + '</div>' +
        '<input type="text" class="input logo-url-inp" placeholder="' + t('set.fav_logos_url_ph') + '" style="flex:1;min-width:180px;font-size:11px">' +
        '<button class="btn" style="font-size:10px" onclick="setFavLogoUrl(this)">' + t('set.fav_logos_save_url') + '</button>' +
        '<label class="btn" style="font-size:10px;cursor:pointer">📁 ' + t('set.fav_logos_upload') + '<input type="file" accept="image/*" style="display:none" onchange="uploadFavLogo(this)"></label>' +
        resetBtn +
      '</div>';
    }}).join('');
  }}).catch(() => {{ box.innerHTML = '<span style="color:var(--amber);font-size:11px">✗ Fehler</span>'; }});
}}

function _favLogoResult(ok, msg) {{
  const fb = document.getElementById('fav-logo-fb');
  if (fb) fb.textContent = ok ? ('✓ ' + t('set.fav_logos_done')) : ('✗ ' + (msg || 'Fehler'));
  try {{ showToast(ok ? t('set.fav_logos_done') : (msg || 'Fehler'), ok ? 'success' : 'error'); }} catch(e) {{}}
}}

function showLogoModal(src, name) {{
  const m = document.getElementById('logo-modal');
  if (!m || !src) return;
  document.getElementById('logo-modal-img').src = src;
  document.getElementById('logo-modal-name').textContent = name || '';
  m.style.display = 'flex';
}}

function closeLogoModal() {{
  const m = document.getElementById('logo-modal');
  if (m) m.style.display = 'none';
  const img = document.getElementById('logo-modal-img');
  if (img) img.src = '';
}}

document.addEventListener('keydown', function(e) {{
  if (e.key === 'Escape') closeLogoModal();
}});

function setFavLogoUrl(btn) {{
  const row = btn.closest('.fav-logo-row');
  const name = row.getAttribute('data-name');
  const inp = row.querySelector('.logo-url-inp');
  const url = (inp.value || '').trim();
  if (!url) {{ _favLogoResult(false, t('set.fav_logos_need_url')); return; }}
  const fb = document.getElementById('fav-logo-fb');
  if (fb) fb.textContent = t('set.fav_logos_working');
  fetch('/api/favorites/logo', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{name: name, url: url}})}})
    .then(r => r.json()).then(d => {{ _favLogoResult(d.ok, d.message); loadFavLogos(); }})
    .catch(() => _favLogoResult(false));
}}

function uploadFavLogo(input) {{
  const row = input.closest('.fav-logo-row');
  const name = row.getAttribute('data-name');
  const file = input.files && input.files[0];
  if (!file) return;
  if (file.size > 8 * 1024 * 1024) {{ _favLogoResult(false, t('set.fav_logos_too_big')); input.value = ''; return; }}
  const fb = document.getElementById('fav-logo-fb');
  if (fb) fb.textContent = t('set.fav_logos_working');
  const reader = new FileReader();
  reader.onload = function() {{
    fetch('/api/favorites/logo', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{name: name, data: reader.result}})}})
      .then(r => r.json()).then(d => {{ _favLogoResult(d.ok, d.message); loadFavLogos(); }})
      .catch(() => _favLogoResult(false));
    input.value = '';
  }};
  reader.readAsDataURL(file);
}}

function resetFavLogo(btn) {{
  const name = btn.getAttribute('data-name');
  const fb = document.getElementById('fav-logo-fb');
  if (fb) fb.textContent = t('set.fav_logos_working');
  fetch('/api/favorites/logo/reset', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{name: name}})}})
    .then(r => r.json()).then(d => {{ _favLogoResult(d.ok, d.message); loadFavLogos(); }})
    .catch(() => _favLogoResult(false));
}}
</script>
""" 
    return html_page("Settings", body, css)

# ==============================================================================
# END WEBUI
# ==============================================================================


