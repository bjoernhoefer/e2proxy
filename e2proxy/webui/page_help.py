"""WebUI: help page (feature overview, API reference, changelog)."""
from e2proxy.version import INTERNAL_VERSION, VERSION
from e2proxy.webui.base import html_page

def build_help_ui():
    css = """
.help-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:24px;}
@media(max-width:768px){.help-grid{grid-template-columns:1fr;}}
.help-card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:20px;}
.help-card-title{font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:700;color:var(--accent2);text-transform:uppercase;letter-spacing:1px;margin:0 0 12px;}
.help-card p{font-size:13px;line-height:1.7;margin:0 0 8px;color:var(--text);}
.help-card p:last-child{margin-bottom:0;}
.help-api{font-family:'JetBrains Mono',monospace;font-size:10px;background:var(--surface2);border-radius:6px;padding:12px;line-height:2;grid-column:1/-1;}
"""
    body = f"""
<div style="max-width:1100px;margin:0 auto">
  <div style="padding:24px 24px 0;display:flex;align-items:center;justify-content:space-between">
    <div>
      <h1 style="font-family:'JetBrains Mono',monospace;font-size:16px;color:var(--accent);margin:0">e2proxy — <span data-i18n="nav.help">Help</span></h1>
      <div style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--muted);margin-top:4px">
        Version <span style="color:var(--accent2)">{VERSION}</span>
        <span style="color:var(--muted)" title="Interne Build-Kennung — wechselt bei jedem Test-Rollout">&nbsp;·&nbsp;Build {INTERNAL_VERSION}</span>
      </div>
    </div>
    <a class="btn" href="/" style="font-size:11px" data-i18n="nav.mainpage">← Main page</a>
  </div>

  <div class="help-grid">

    <div class="help-card">
      <div class="help-card-title">📡 <span data-i18n="help.live_tv">Live TV Streaming</span></div>
      <p data-i18n-html="1" data-i18n="help.live_tv_desc">Select a channel on the left → stream starts in the browser. Profile (Web-SD / Web-HD) top right. Fullscreen button maximizes. Each stream uses one receiver — the header status shows who is currently streaming. Kill button (✕) ends a session.</p>
    </div>

    <div class="help-card">
      <div class="help-card-title">📺 EPG Browser</div>
      <p data-i18n-html="1" data-i18n="help.epg_desc">28-hour program guide as a timeline grid. Channel labels stay visible when scrolling horizontally (sticky). Click on a show → details + TMDB link. Automatically updated daily at 3:00 AM. Manual: <b>Settings → EPG → Update now</b>. Startup run without TMDB (fast) — nightly run with TMDB posters.</p>
    </div>

    <div class="help-card">
      <div class="help-card-title">⭐ <span data-i18n="nav.favorites">Favorites</span></div>
      <p data-i18n-html="1" data-i18n="help.fav_desc">Channel list for Plex DVR, EPG and M3U. Reorder via drag & drop. Per channel: group + EPG category (series/movie/news/sports…) for Kodi color coding in the EPG grid. Changes saved immediately.</p>
    </div>

    <div class="help-card">
      <div class="help-card-title">🔴 <span data-i18n="help.recordings">Recordings</span></div>
      <p data-i18n-html="1" data-i18n="help.rec_desc">Via API (e2recorder) or <b>Settings → Recordings → Quick Record</b>. Select channel → current show displayed → set duration → start. Structured storage: <code>Show/Season_XX/Name_SXXEXX_Date.ts</code>. Each recording gets a <code>.nfo</code> with channel logo + TMDB poster for Plex. Watchdog auto-terminates after max duration. After: automatic Plex library refresh.</p>
    </div>

    <div class="help-card">
      <div class="help-card-title">🎬 TMDB Artwork</div>
      <p data-i18n-html="1" data-i18n="help.tmdb_desc">During nightly EPG run: automatic poster search for shows ≥20 min. Similarity check (45%) prevents false matches. Fallback: channel logo. Cache: 30 days (found) / 7 days (not found). Cleared on service restart. API key: <b>Settings → Configuration → API Keys</b>.</p>
    </div>

    <div class="help-card">
      <div class="help-card-title">⚙ <span data-i18n="nav.settings">Settings</span></div>
      <p data-i18n-html="1" data-i18n="help.settings_desc"><b>Configuration:</b> Receivers, transcode profiles, device profiles, TMDB API key, e2recorder URL.<br>
      <b>Maintenance:</b> Live logs, level control (DEBUG/INFO/WARNING/ERROR) without restart, 500-entry RAM buffer.<br>
      <b>EPG:</b> Manual run, schedule, run history as bar chart, outlier detection.<br>
      <b>Recordings:</b> Path, profile, watchdog, Plex token (via plex.tv login), Plex section multi-select.</p>
    </div>

    <div class="help-card">
      <div class="help-card-title">🔌 Plex DVR Integration</div>
      <p data-i18n-html="1" data-i18n="help.plex_desc">e2proxy emulates an HDHomeRun device — Plex discovers it automatically via SSDP. In Plex: <b>Live TV & DVR → Add device</b>. EPG via <code>/epg.xml</code> (XMLTV with TMDB posters and DVB genre IDs). No Threadfin needed.</p>
    </div>

    <div class="help-card">
      <div class="help-card-title">🐳 Docker</div>
      <p data-i18n-html="1" data-i18n="help.docker_desc">Runs as a container with <code>network_mode: host</code> (required for SSDP). Data path via <code>E2PROXY_DATA_DIR=/data</code>. Script swappable live without image rebuild.<br>
      Update script: <code>scp e2proxy.py pi@server:~/e2proxy/ &amp;&amp; docker compose restart</code></p>
    </div>

    <div class="help-card help-api">
      <div class="help-card-title">🔗 <span data-i18n="help.api_overview">API Overview</span></div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:0 24px">
        <div>
          <b style="color:var(--accent2)" data-i18n="help.api_channels">Channels &amp; EPG</b><br>
          GET /api/channels<br>GET /api/favorites<br>GET /api/epg/data<br>GET /api/epg/status<br>GET /api/epg/run<br>GET /api/epg/history<br>GET /api/plex/dvr-refresh<br>GET /api/health
        </div>
        <div>
          <b style="color:var(--accent2)">Aufnahmen</b><br>
          GET  /api/tuners<br>POST /api/record/start<br>POST /api/record/stop<br>GET  /api/record/status<br>GET  /api/recordings<br>DEL  /api/recordings/delete<br>GET  /recording/stream?file=…
        </div>
        <div>
          <b style="color:var(--accent2)">System &amp; Plex</b><br>
          GET  /api/status<br>GET  /api/config<br>POST /api/config<br>GET  /api/switch/stats<br>POST /api/switch/settings<br>GET  /api/logs?level=INFO<br>POST /api/log/level<br>GET  /api/plex/token<br>GET  /api/plex/sections<br>GET  /epg.xml<br>GET  /playlist.m3u
        </div>
      </div>
    </div>

    <div class="help-card" style="grid-column:1/-1">
      <div class="help-card-title">📋 Changelog</div>
      <div style="display:flex;flex-direction:column;gap:12px">

        <div style="border-left:3px solid var(--accent);padding-left:14px">
          <b style="color:var(--accent);font-family:monospace;font-size:11px">v5.0</b>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">2026-11-12</span>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">Modulare Architektur · integrierter Serien-Recorder · automatische Pi-Rollouts</span>
          <div style="font-size:11px;margin-top:4px;color:var(--muted)"><b>Modular:</b> Die 14&nbsp;000-Zeilen-Datei <code>e2proxy.py</code> ist jetzt das Paket <code>e2proxy/</code> (ein Modul je Aufgabe, Start mit <code>python -m e2proxy</code>). Neue Funktionen docken über die Erweiterungs-Schnittstelle <code>e2proxy.extensions</code> an; Verhalten aller Seiten und APIs unverändert.<br>
          <b>Recorder (optional):</b> e2recorder ist integriert – <b>Einstellungen → Recorder</b> aktiviert ihn; im EPG werden geplante/laufende/fertige Aufnahmen markiert, mit „Einmal aufnehmen“, „Serie aufnehmen“ und „Überspringen“ im Detail-Popup. Ausgeschaltet kostet er nichts. Migration: <code>series.json</code>/<code>recordings.json</code> nach <code>/data/recorder/</code> kopieren.<br>
          <b>Rollout:</b> Fertiges arm64-Image mit ffmpeg (kein <code>apt-get</code> mehr bei jedem Start), Tests + Selbsttest in CI, automatische Verteilung auf einen oder mehrere Raspberry Pis über <code>pi-deploy</code> mit Rollback. Build-ID = Git-Commit.</div>
        </div>

        <div style="border-left:3px solid var(--accent);padding-left:14px">
          <b style="color:var(--accent);font-family:monospace;font-size:11px">v4.9</b>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">2026-11-06</span>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">Schnittstellen-Health-Probes · schnelleres Zap-Failover</span>
          <div style="font-size:11px;margin-top:4px;color:var(--muted)"><b>Schnittstellen-Status:</b> Neues Modul <code>e2probe.py</code> prüft alle 60&nbsp;s die Plex-Schnittstelle (<code>/discover.json</code>, <code>/lineup.json</code>, Tuner-Verfügbarkeit) und die DreamBox-App-Schnittstelle (OpenWebif <code>/web/about</code>, Bouquet-Liste, Stream-Port, Aufnahme-Port). Ergebnis unter <b>Einstellungen → Wartung</b> und <code>/api/interfaces</code>; Ausfälle werden nach 2 Fehlschlägen als <code>DIAG event=interface_state</code> geloggt und über den Receiver-Alarm-Webhook (<code>interface_down</code>/<code>interface_up</code>) gemeldet. Manueller Stream-Test für Plex und OpenWebif. Probe-Anfragen zählen nicht in Statistik und Access-Log.<br>
          <b>Schnelleres Failover beim Umschalten:</b> Ein bereits als ausgefallen bekannter Receiver wird bei der Tuner-Wahl übersprungen bzw. nur einmal (statt dreimal) versucht — Plex bekam vorher erst nach ~38&nbsp;s einen Stream und brach mit „Sender konnte nicht eingestellt werden“ ab.</div>
        </div>

        <div style="border-left:3px solid var(--border);padding-left:14px">
          <b style="font-family:monospace;font-size:11px">v4.8</b>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">2026-11-06</span>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">Eingefrorene Receiver: OpenWebif-Ausweichen · Ausfall-Alarm</span>
          <div style="font-size:11px;margin-top:4px;color:var(--muted)"><b>OpenWebif-Emulation weicht toten Boxen aus:</b> Metadaten-/EPG-Anfragen der Apps gingen immer an den Default-Receiver. War dieser eingefroren (Ping ok, HTTP hängt), lief jede Anfrage 20&nbsp;s in den Timeout — die Apps wirkten „hängend“. Ausgefallene Receiver (Circuit Breaker offen oder Fehlerserie) werden jetzt übersprungen; der Default bleibt unverändert und wird automatisch wieder genutzt, sobald er antwortet.<br>
          <b>Receiver-Ausfall-Alarm:</b> Ist ein Receiver länger als N Minuten (Default 10) nicht erreichbar, wird einmal pro Ausfall <code>DIAG event=receiver_alert</code> geloggt und optional ein Webhook (<b>Einstellungen → Wartung</b>) mit <code>receiver_down</code> bzw. <code>receiver_up</code> gesendet. Die Statusleiste zeigt „⚠ nicht erreichbar seit …“ statt „frei“.</div>
        </div>

        <div style="border-left:3px solid var(--border);padding-left:14px">
          <b style="font-family:monospace;font-size:11px">v4.6</b>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">2026-08-10</span>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">Stream-Stabilität: Tuner-Refcount · Jitter-Puffer · Log-Flut</span>
          <div style="font-size:11px;margin-top:4px;color:var(--muted)"><b>Abbrechende Streams behoben:</b> Ein Receiver wird jetzt per Referenzzähler verwaltet. Teilten sich mehrere Zuschauer einen Tuner (Shared Tuner, z.&nbsp;B. VLCs Probe- plus Play-Verbindung), gab der zuerst endende Stream den Tuner frei, obwohl der andere noch lief — der nächste Request nahm den vermeintlich freien Receiver und zappte ihn weg. Freigabe erfolgt erst, wenn der letzte Nutzer geht (<code>/kill</code> erzwingt weiterhin sofort). Receiver-Auswahl und -Belegung laufen unter einem Lock, damit zwei fast gleichzeitige Requests nicht denselben Tuner greifen. Die Upstream-Sockets werden pro Receiver als Menge geführt (vorher überschrieb der zweite Stream den Socket des ersten → Tuner-Leak beim Kill). Ein <code>/stream</code> mit <code>preacquired</code> hängt sich an, statt den Receiver der laufenden Aufnahme freizugeben.<br>
          <b>Jitter-Puffer für Passthrough:</b> Schwache Boxen liefern das TS schubweise mit 3–6&nbsp;s Pausen, was bisher 1:1 beim Client ankam. Ein Reader-Thread füllt nun einen gemessenen Vorpuffer (<code>passthrough_prebuffer_sec</code>, Default 2&nbsp;s, max. 24&nbsp;MB je Stream), aus dem der Client gleichmäßig bedient wird. 0 schaltet ihn ab.<br>
          <b>Zap-Robustheit:</b> Timeout auf 8&nbsp;s erhöht, bis zu 3 Versuche, und nach einem Fehlversuch wird per <code>statusinfo</code> geprüft, ob die Box längst richtig steht — ein langsamer Zap gilt nicht mehr als Fehler und löst keine Client-Retries mit parallelen Sessions mehr aus.<br>
          <b>Log-Flut beseitigt:</b> ffmpeg läuft mit <code>-nostats -hide_banner</code>, und stderr wird an <code>\r</code> <i>und</i> <code>\n</code> getrennt gelesen — bisher verklebte zeilenweises Lesen minutenlangen Fortschritt (<code>frame=… fps=…</code>) zu einer Riesenzeile, die samt echter Meldung als Warnung im Log landete. Wiederkehrende Transportstrom-Meldungen werden pro Receiver/Ref/Art in 60-s-Fenstern aggregiert: erste Meldung sofort, danach ein <code>ffmpeg_issue_summary</code> mit Zähler statt hunderter Einzelzeilen.</div>
        </div>

        <div style="border-left:3px solid var(--border);padding-left:14px">
          <b style="font-family:monospace;font-size:11px">v4.3</b>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">2026-07-29</span>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">Aufnahmen für Dream Player</span>
          <div style="font-size:11px;margin-top:4px;color:var(--muted)">OpenWebif-Emulation der Aufnahmen (Dream Player Recordings-Tab) verbessert: die Dauer (<code>e2length</code>) wird jetzt im echten OpenWebif-Format <code>MINUTEN:SS</code> geliefert (90&nbsp;min → <code>90:00</code> statt roher Sekunden), damit Dream Player die Länge korrekt anzeigt. Neu abgefangen werden <code>getlocations</code>/<code>getcurrlocation</code> (melden das e2proxy-Aufnahmeverzeichnis statt das der Box), <code>moviedelete</code> (löscht die lokale e2recorder-Aufnahme inkl. <code>.nfo</code> und leerer Ordner) und <code>moviedetails</code> (Einzel-<code>&lt;e2movie&gt;</code> vor der Wiedergabe). <code>fullmovielist</code> wird als Alias unterstützt, die JSON-Movielist um authentische Felder (<code>filename_stripped</code>, <code>filesize_readable</code>, <code>begintime</code>, <code>lastseen</code>, Bookmarks) ergänzt. Wiedergabe läuft weiterhin als rohes TS über HTTP mit Range/Seeking.</div>
        </div>

        <div style="border-left:3px solid var(--border);padding-left:14px">
          <b style="font-family:monospace;font-size:11px">v4.1</b>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">2026-07-20</span>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">Settings tab fix · WebUI self-check · WebUI consolidation</span>
          <div style="font-size:11px;margin-top:4px;color:var(--muted)">Fixed the Settings page where the <b>Maintenance / EPG / Recordings / API</b> tabs could not be opened: a stray escape in the embedded JavaScript broke the whole script block so <code>switchTab()</code> was never defined (only the Configuration tab, which is active by default, still worked). Tab switching is now also independent of the deprecated global <code>event</code> object. To stop this class of bug from ever shipping silently again, a built-in <b>WebUI self-check</b> now validates every page's embedded JavaScript at startup (logged) and via <code>python3 e2proxy.py --selfcheck</code> as a pre-deploy gate. All browser-facing code has been consolidated into one delimited <code>WEBUI</code> block with a documented extraction contract, preparing it to be split into its own service later.</div>
        </div>

        <div style="border-left:3px solid var(--border);padding-left:14px">
          <b style="font-family:monospace;font-size:11px">v3.8.0</b>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">2026-07-13</span>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">Editable favorite logos</span>
          <div style="font-size:11px;margin-top:4px;color:var(--muted)">Favorite channel logos can now be edited under <b>Settings → Maintenance</b>. For each favorite you can upload an image or enter a URL; the image is converted with ffmpeg to the correct format (PNG, max 400px wide, aspect preserved) and stored locally. Custom logos take precedence over the built-in logo database and cache for both the M3U playlist and the XMLTV EPG, and can be reset to fall back to the automatic logo. New endpoints <code>/api/favorites/logos</code>, <code>/api/favorites/logo</code> and <code>/api/favorites/logo/reset</code>; custom logos are served at <code>/custom_logos/</code> and stored in <code>/data/custom_logos/</code>. Custom logos are now also injected into the cached XMLTV EPG immediately (on change and at startup), so they show up in Plex without waiting for the next full EPG run.</div>
        </div>

        <div style="border-left:3px solid var(--border);padding-left:14px">
          <b style="font-family:monospace;font-size:11px">v3.7.0</b>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">2026-07-12</span>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">Switch Tuning · NoLatency · Self-Learning</span>
          <div style="font-size:11px;margin-top:4px;color:var(--muted)">Faster channel switching (esp. Plex): per-channel <b>NoLatency</b> mode starts ffmpeg with minimal probing + low-delay input flags, and the post-zap wait is now configurable (was a hardcoded 1s). ffmpeg is monitored during the first seconds — if no data flows the stream is transparently restarted with a larger probesize. Self-learning: repeated NoLatency failures raise a channel's probesize in small steps automatically. New per-channel statistics (zap ok/fail + avg ms, stream start ok/fail + retries) in Settings, plus <code>/api/switch/stats</code>, <code>/api/switch/settings</code> and <code>/api/switch/reset</code>.</div>
        </div>

        <div style="border-left:3px solid var(--border);padding-left:14px">
          <b style="font-family:monospace;font-size:11px">v3.4.0</b>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">2026-06-16</span>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">Compression · Progress/ETA · Pause · CPU Limit</span>
          <div style="font-size:11px;margin-top:4px;color:var(--muted)">Live progress bar with ETA and encode speed for the running conversion (ffmpeg <code>-progress</code> + ffprobe duration). Pause/Resume (SIGSTOP/SIGCONT — frees CPU instantly for streaming or a restart) and Cancel. CPU limit (% of cores → encoder threads + <code>nice</code>) plus a "run anytime" background mode that ignores the time window. Conversions now write to a <code>.mkv.part</code> temp file and are atomically renamed on success; leftover temp files from an interrupted run are cleaned up on startup so no half-finished <code>.mkv</code> is ever left behind.</div>
        </div>

        <div style="border-left:3px solid var(--border);padding-left:14px">
          <b style="font-family:monospace;font-size:11px">v3.3.2</b>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">2026-06-16</span>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">Compression · Selectable Convert List</span>
          <div style="font-size:11px;margin-top:4px;color:var(--muted)">Fixed "Convert now" JSON error (empty POST body + endpoint was in the GET handler). Pending recordings are now shown as a checkbox list with a "Select all" option — pick individual recordings or all of them and convert on demand via <code>POST /api/compression/run</code> with a <code>paths</code> list. Selection is preserved across the 5s auto-refresh.</div>
        </div>

        <div style="border-left:3px solid var(--border);padding-left:14px">
          <b style="font-family:monospace;font-size:11px">v3.1.0</b>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">2026-06-14</span>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">i18n · First Stable Release</span>
          <div style="font-size:11px;margin-top:4px;color:var(--muted)">Full English/German UI translation with client-side i18n engine (data-i18n attributes). Language selector in Settings. Browser language auto-detection (default: English). All log messages translated to English. Help page fully bilingual.</div>
        </div>

        <div style="border-left:3px solid var(--border);padding-left:14px">
          <b style="font-family:monospace;font-size:11px">v2.2.0</b>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">2026-06-12</span>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">Plex Library · TVDB · NFO</span>
          <div style="font-size:11px;margin-top:4px;color:var(--muted)">Plex-compliant directory structure: <code>Movies/&lt;Title&gt; (&lt;Year&gt;)/</code> and <code>TV/&lt;Series&gt;/Season XX/</code>. TVDB integration for real season/episode detection. Daily-show fallback with day-of-year numbering (S2026E163). NFO files for every recording + tvshow.nfo for series. Movie/series selection directly in EPG browser.</div>
        </div>

        <div style="border-left:3px solid var(--border);padding-left:14px">
          <b style="font-family:monospace;font-size:11px">v2.1.3</b>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">2026-06-12</span>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">Parallel Recordings · Logging · UI Fixes</span>
          <div style="font-size:11px;margin-top:4px;color:var(--muted)">Shared-tuner recordings bypass /stream and connect directly to receiver (port 8001) — no more 429 rejections for back-to-back shows on the same channel. Receiver only released when no other recording uses the stream. Logging system rewrite: live polling, history loading from file logs with time window selector, API access log with daily rotation + append writing, retention default 5 days.</div>
        </div>

        <div style="border-left:3px solid var(--border);padding-left:14px">
          <b style="font-family:monospace;font-size:11px">v2.1.2</b>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">2026-06-11</span>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">EPG Browser · Race Condition · Logging</span>
          <div style="font-size:11px;margin-top:4px;color:var(--muted)">EPG browser layout fix (flex:1 → min-width:max-content for events beyond viewport). Receiver race condition fix with pre-acquire + preacquired stream URL parameter. BrokenPipeError silently ignored. Log timestamps local time instead of UTC. File logging with TimedRotatingFileHandler. /api/version endpoint with build ID.</div>
        </div>

        <div style="border-left:3px solid var(--border);padding-left:14px">
          <b style="font-family:monospace;font-size:11px">v2.1.1</b>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">2026-06-06</span>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">Docker ffmpeg Fix</span>
          <div style="font-size:11px;margin-top:4px;color:var(--muted)">ffmpeg added to Docker container (python:3.11-slim + apt-get install ffmpeg) — streams and recordings failed because python:3.11-slim has no ffmpeg.</div>
        </div>

        <div style="border-left:3px solid var(--border);padding-left:14px">
          <b style="font-family:monospace;font-size:11px">v2.1.0</b>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">2026-06-05</span>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">Docker · Help Page · Plex Token/Sections · Health API</span>
          <div style="font-size:11px;margin-top:4px;color:var(--muted)">Docker migration (E2PROXY_DATA_DIR), help as standalone page, /api/health + startup announce, shared tuner detection, Plex token/sections multi-select, /recording/stream with range support, EPG grid single-scroll container.</div>
        </div>

        <div style="border-left:3px solid var(--border);padding-left:14px">
          <b style="font-family:monospace;font-size:11px">v2.0.0</b>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">2026-05-30</span>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">Recording · TMDB · EPG Fixes · Log Level</span>
          <div style="font-size:11px;margin-top:4px;color:var(--muted)">Recording system (ffmpeg, series/season/episode), TMDB artwork + similarity check (45%), EPG race condition fix, log ring buffer + level control without restart, EPG run history chart, DVB genre IDs for Kodi.</div>
        </div>

        <div style="border-left:3px solid var(--border);padding-left:14px">
          <b style="font-family:monospace;font-size:11px">v1.2.0</b>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">2026-05-20</span>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">Settings UI · EPG Browser · Favorites</span>
          <div style="font-size:11px;margin-top:4px;color:var(--muted)">Settings UI with tabs (config/maintenance/EPG/recordings), EPG browser as timeline grid, favorites with drag & drop, light/dark theme, EPG disk persistence + scheduler, channel logo cache.</div>
        </div>

        <div style="border-left:3px solid var(--border);padding-left:14px">
          <b style="font-family:monospace;font-size:11px">v1.1.0</b>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">2026-05-10</span>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">Plex DVR · HDHomeRun · XMLTV</span>
          <div style="font-size:11px;margin-top:4px;color:var(--muted)">Plex DVR without Threadfin, HDHomeRun emulation + SSDP UDP multicast, XMLTV multi-source (both receivers + zap reload), transcode profiles (remux-ac3, pass, webm).</div>
        </div>

        <div style="border-left:3px solid var(--border);padding-left:14px">
          <b style="font-family:monospace;font-size:11px">v1.0.0</b>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">2026-05-01</span>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">Initial Release</span>
          <div style="font-size:11px;margin-top:4px;color:var(--muted)">Enigma2 streaming proxy for 2 receivers, web UI with channel list + player, Jellyfin + browser streaming (WebM VP8/VP9), M3U playlist export, tuner status in header.</div>
        </div>

      </div>
    </div>

  </div>
</div>
"""
    return html_page("Help", body, css)


