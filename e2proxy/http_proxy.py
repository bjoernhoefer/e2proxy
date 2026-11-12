"""Main HTTP API/UI handler (port 8888)."""
import http.server
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from e2proxy import probe as e2probe

from e2proxy.version import BUILD_BRANCH, BUILD_SEQ, INTERNAL_VERSION, START_TIME, VERSION
from e2proxy.paths import DATA_DIR, FAVORITES_FILE
from e2proxy.logs import (
    _LOG_LEVEL_NAMES,
    _current_display_level,
    get_log_entries,
    get_log_entries_from_disk,
    log,
    set_log_level,
)
from e2proxy.config import (
    get_config,
    get_default_profile,
    get_device_profile,
    get_proxy_host,
    get_receiver_by_id,
    get_receivers,
    get_transcode_profile,
    is_receiver_locked,
    update_config,
)
from e2proxy.favorites import favorites_lock, get_favorites, load_favorites, save_favorites
from e2proxy.channels import channel_cache, channel_cache_lock, get_channels
from e2proxy.logos import (
    CUSTOM_LOGO_DIR,
    LOGO_CACHE_DIR,
    _fetch_image_bytes,
    convert_and_store_custom_logo,
    custom_logo_local_url,
    delete_custom_logo,
    get_favorite_logo_overview,
    get_logo_for_epg,
    refresh_logo_cache,
)
from e2proxy.switching import (
    analyze_switch_stats,
    get_switch_global,
    get_switch_settings,
    get_switch_stats_snapshot,
    reset_switch_stats,
    set_switch_override,
)
from e2proxy.receivers import (
    _init_receiver_state,
    acquire_receiver,
    attach_receiver,
    do_zap,
    get_free_receiver,
    get_ref_lock,
    is_receiver_online,
    kill_stream,
    receiver_health_snapshot,
    receiver_lock,
    release_receiver,
    stream_info_public,
    zap_with_failover,
)
from e2proxy.diag import (
    DIAG_SAMPLE_SEC,
    DIAG_STALL_SEC,
    diag_active_streams,
    diag_enabled,
    diag_log,
)
from e2proxy.streaming import (
    ScrambledStreamError,
    build_ffmpeg_cmd,
    stream_passthrough,
    stream_transcoded,
)
from e2proxy.hdhr import (
    build_hdhr_device_xml,
    build_hdhr_discover,
    build_hdhr_lineup,
    build_hdhr_lineup_plex,
    build_hdhr_lineup_status,
)
from e2proxy.plex import _plex_dvr_refresh_guide
from e2proxy.epg import (
    EPG_RUNS_FILE,
    apply_custom_logos_to_epg_cache,
    check_favorite_logos,
    epg_run_lock,
    epg_run_state,
    get_epg_schedule_hour,
    get_epg_xml,
    run_epg_update,
)
from e2proxy.usage import (
    build_usage_report,
    classify_endpoint,
    note_endpoint,
    record_usage_session,
    reset_usage_stats,
)
from e2proxy.playlist import build_m3u
from e2proxy.recordings import (
    _active_recordings,
    _active_recordings_lock,
    get_recording_status,
    get_recordings_config,
    get_tuner_status,
    start_recording,
    stop_recording,
)
from e2proxy.compression import (
    COMPRESSION_PROFILES,
    _compression_lock,
    _compression_state,
    _is_in_window,
    _load_compression_history,
    cancel_compression,
    compress_next_pending,
    compress_selected,
    find_pending_compressions,
    get_compression_config,
    has_compression_backlog,
    pause_compression,
    resume_compression,
)
from e2proxy.notify import (
    MAINT_NOTIFY_DEFAULT,
    RECEIVER_ALERT_DEFAULT,
    get_maint_notify_config,
    get_receiver_alert_config,
    send_maintenance_notification,
    send_receiver_alert,
)
from e2proxy.accesslog import (
    API_ACCESS_LOG_FILE,
    is_api_logging_enabled,
    read_access_log,
    write_access_log,
)
from e2proxy.interfaces import interface_snapshot, interface_stream_test
from e2proxy.webui.page_main import build_web_ui
from e2proxy.webui.page_help import build_help_ui
from e2proxy.webui.page_favorites import build_favorites_ui
from e2proxy.webui.page_epg import build_epg_browser
from e2proxy.webui.page_settings import build_settings_ui
from e2proxy import receivers
from e2proxy import interfaces
from e2proxy import extensions

# ── HTTP Handler ──────────────────────────────────────────

class ProxyHandler(http.server.BaseHTTPRequestHandler):

    # HTTP/1.1 global fuer alle Endpoints — Plex lehnt HTTP/1.0 Responses ab.
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text, status=200, content_type="text/plain"):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        """Kodi schickt HEAD Request zum Testen — wir antworten mit 200 OK."""
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body_data = self.rfile.read(length)

        try:
            data = json.loads(body_data.decode("utf-8")) if body_data.strip() else {}
        except Exception as e:
            self.send_json({"ok": False, "message": f"JSON Fehler: {e}"})
            return

        if extensions.dispatch(self, "POST", data if isinstance(data, dict) else {}):
            return

        if parsed.path == "/api/config":
            ok = update_config(data)
            _init_receiver_state()
            self.send_json({"ok": ok})
            return

        if parsed.path == "/api/config-update":
            # Partial update für einzelne Keys (z.B. default_device_profile)
            cfg = get_config()
            cfg.update(data)
            ok = update_config(cfg)
            self.send_json({"ok": ok})
            return

        if parsed.path == "/api/switch/settings":
            # Globale Umschalt-Defaults und/oder Per-Sender-Override setzen.
            g = data.get("global")
            if isinstance(g, dict):
                cfg = get_config()
                for k in ("no_latency", "zap_wait_sec", "probe_default",
                          "no_latency_probesize", "no_latency_analyzeduration",
                          "switch_monitor_sec", "switch_max_retries",
                          "nolatency_fail_threshold"):
                    if k in g and g[k] is not None:
                        cfg[k] = g[k]
                update_config(cfg)
            ref = data.get("ref")
            if ref:
                set_switch_override(
                    ref,
                    no_latency=data.get("no_latency"),
                    zap_wait=data.get("zap_wait"),
                    probesize=data.get("probesize"),
                    channel_name=data.get("name"),
                )
            self.send_json({"ok": True})
            return

        if parsed.path == "/api/switch/reset":
            reset_switch_stats(data.get("ref"))
            self.send_json({"ok": True})
            return

        if parsed.path == "/api/switch/analyze":
            result = analyze_switch_stats(
                apply=bool(data.get("apply")),
                min_samples=data.get("min_samples"),
            )
            result["ok"] = True
            self.send_json(result)
            return

        if parsed.path == "/api/usage/reset":
            reset_usage_stats()
            self.send_json({"ok": True})
            return

        if parsed.path == "/api/favorites":
            if not isinstance(data, list):
                self.send_json({"ok": False, "message": "Expected list"})
                return
            # Normalisiere alle Refs zu Doppelpunkt-Format (kompatibel mit Channel-Refs)
            for fav in data:
                ref = fav.get("ref", "")
                if "_" in ref and ":" not in ref:
                    # Underscore-Ref → Doppelpunkt-Ref + trailing :
                    fav["ref"] = ref.replace("_", ":")
                    if not fav["ref"].endswith(":"):
                        fav["ref"] += ":"
            with favorites_lock:
                ok = save_favorites(data)
            if ok:
                log.info(f"Favoriten gespeichert: {len(data)} Sender")
            self.send_json({"ok": ok, "count": len(data),
                           "message": None if ok else f"Schreiben nach {FAVORITES_FILE} fehlgeschlagen"})
            return

        if parsed.path == "/api/favorites/logo":
            try:
                name = (data.get("name") or "").strip()
                if not name:
                    self.send_json({"ok": False, "message": "Sendername fehlt"})
                    return
                url = (data.get("url") or "").strip()
                data_uri = data.get("data") or ""
                if url:
                    img_bytes = _fetch_image_bytes(url)
                elif data_uri:
                    # data:image/...;base64,<payload>  oder reines Base64
                    b64 = data_uri.split(",", 1)[1] if "," in data_uri else data_uri
                    import base64
                    img_bytes = base64.b64decode(b64)
                    if len(img_bytes) > 8 * 1024 * 1024:
                        self.send_json({"ok": False, "message": "Bild zu groß (max 8 MB)"})
                        return
                else:
                    self.send_json({"ok": False, "message": "Weder URL noch Bild angegeben"})
                    return
                convert_and_store_custom_logo(name, img_bytes)
                threading.Thread(target=apply_custom_logos_to_epg_cache, daemon=True).start()
                self.send_json({"ok": True, "logo_url": custom_logo_local_url(name)})
            except Exception as e:
                self.send_json({"ok": False, "message": str(e)})
            return

        if parsed.path == "/api/favorites/logo/reset":
            name = (data.get("name") or "").strip()
            if not name:
                self.send_json({"ok": False, "message": "Sendername fehlt"})
                return
            delete_custom_logo(name)
            threading.Thread(target=apply_custom_logos_to_epg_cache, daemon=True).start()
            self.send_json({"ok": True, "logo_url": get_logo_for_epg(name)})
            return

        if parsed.path == "/api/access-log/toggle":
            cfg = get_config()
            cfg["api_logging"] = data.get("enabled", not cfg.get("api_logging", False))
            update_config(cfg)
            log.info(f"API Logging: {'enabled' if cfg['api_logging'] else 'deenabled'}")
            self.send_json({"ok": True, "enabled": cfg["api_logging"]})
            return

        if parsed.path == "/api/access-log/clear":
            import glob
            try:
                for fp in glob.glob(f"{API_ACCESS_LOG_FILE}*"):
                    os.remove(fp)
                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/log/level":
            level = data.get("level", "INFO").upper()
            if level not in _LOG_LEVEL_NAMES:
                self.send_json({"ok": False, "message": "Ungültiger Level"})
                return
            set_log_level(level)
            log.info(f"Log-Level geändert auf {level}")
            self.send_json({"ok": True, "level": level})
            return

        if parsed.path == "/api/record/start":
            try:
                service_ref  = data.get("ref", "")
                # Normalisierung: Underscores → Doppelpunkte (Enigma2 erwaitingt Doppelpunkte)
                if service_ref and "_" in service_ref and ":" not in service_ref:
                    service_ref = service_ref.replace("_", ":")
                title        = data.get("title", "Aufnahme")
                duration     = data.get("duration")
                profile      = data.get("profile")
                path_override= data.get("path")
                description  = data.get("description", "")
                image_url    = data.get("image_url", "")
                kind         = data.get("kind")  # "movie" | "series" | None
                episode_title= data.get("episode_title", "")
                season_override = data.get("season")   # int|None — overrides TVDB
                episode_override = data.get("episode") # int|None — overrides TVDB
                year         = data.get("year")        # int|None — for movie titles
                client_ip    = self.client_address[0]
                if not service_ref:
                    self.send_json({"ok": False, "message": "ref fehlt"})
                    return
                rec_id, filepath, receiver_id, shared, classification = start_recording(
                    service_ref, title, duration, profile,
                    path_override, description, image_url, client_ip,
                    kind=kind, episode_title=episode_title,
                    season=season_override, episode=episode_override, year=year,
                )
                self.send_json({
                    "ok": True,
                    "recording_id": rec_id,
                    "file": filepath,
                    "receiver": receiver_id,
                    "shared_tuner": shared,
                    "classification": {
                        "kind": classification.get("kind"),
                        "season": classification.get("season"),
                        "episode": classification.get("episode"),
                        "synthetic": classification.get("synthetic", False),
                    },
                })
            except Exception as e:
                self.send_json({"ok": False, "message": str(e)})
            return

        if parsed.path == "/api/record/stop":
            try:
                rec_id = data.get("recording_id", "")
                if not rec_id:
                    self.send_json({"ok": False, "message": "recording_id fehlt"})
                    return
                filepath = stop_recording(rec_id)
                self.send_json({"ok": True, "file": filepath})
            except KeyError as e:
                self.send_json({"ok": False, "message": str(e)})
            except Exception as e:
                self.send_json({"ok": False, "message": str(e)})
            return

        if parsed.path == "/api/maintenance/notify":
            try:
                nc = dict(MAINT_NOTIFY_DEFAULT)
                existing = get_config().get("maintenance_notifications", {})
                if isinstance(existing, dict):
                    nc.update(existing)
                if "enabled" in data:
                    nc["enabled"] = bool(data.get("enabled"))
                if "url" in data:
                    nc["url"] = str(data.get("url") or "").strip()
                if "method" in data:
                    nc["method"] = "GET" if str(data.get("method")).upper() == "GET" else "POST"
                if "hour" in data:
                    nc["hour"] = max(0, min(23, int(data.get("hour"))))
                if "minute" in data:
                    nc["minute"] = max(0, min(59, int(data.get("minute"))))
                if "days" in data:
                    days = data.get("days") or []
                    nc["days"] = sorted({int(d) for d in days if 0 <= int(d) <= 6})
                if "idle_mode" in data:
                    nc["idle_mode"] = "idle_only" if data.get("idle_mode") == "idle_only" else "always"
                cfg = get_config()
                cfg["maintenance_notifications"] = nc
                ok = update_config(cfg)
                self.send_json({"ok": ok, "config": get_maint_notify_config()})
            except Exception as e:
                self.send_json({"ok": False, "message": str(e)})
            return

        if parsed.path == "/api/maintenance/notify/test":
            ok, msg = send_maintenance_notification(reason="test")
            self.send_json({"ok": ok, "message": msg})
            return

        if parsed.path == "/api/receiver-alerts":
            try:
                ac = dict(RECEIVER_ALERT_DEFAULT)
                existing = get_config().get("receiver_alerts", {})
                if isinstance(existing, dict):
                    ac.update(existing)
                if "enabled" in data:
                    ac["enabled"] = bool(data.get("enabled"))
                if "after_min" in data:
                    ac["after_min"] = max(1, min(1440, int(data.get("after_min") or 10)))
                if "url" in data:
                    ac["url"] = str(data.get("url") or "").strip()
                if "method" in data:
                    ac["method"] = "GET" if str(data.get("method")).upper() == "GET" else "POST"
                cfg = get_config()
                cfg["receiver_alerts"] = ac
                ok = update_config(cfg)
                self.send_json({"ok": ok, "config": get_receiver_alert_config()})
            except Exception as e:
                self.send_json({"ok": False, "message": str(e)})
            return

        if parsed.path == "/api/receiver-alerts/test":
            rcv = next(iter(get_receivers()), None) or {"id": "test", "name": "Test"}
            ok, msg = send_receiver_alert("receiver_down", rcv, time.time(), test=True)
            self.send_json({"ok": ok, "message": msg})
            return

        if parsed.path == "/api/interfaces/check":
            if interfaces._interface_monitor is None:
                self.send_json({"ok": False, "message": "e2probe.py nicht gefunden"})
                return
            interfaces._interface_monitor.run_once()
            self.send_json({"ok": True, "interfaces": interface_snapshot()})
            return

        if parsed.path == "/api/interfaces/stream-test":
            kind = "openwebif" if data.get("kind") == "openwebif" else "plex"
            res = interface_stream_test(kind, str(data.get("ref") or ""))
            self.send_json(res)
            return

        if parsed.path == "/api/epg/schedule":
            hour = data.get("hour")
            if hour is None or not (0 <= int(hour) <= 23):
                self.send_json({"ok": False, "message": "Ungueltige Stunde"})
                return
            cfg = get_config()
            cfg["epg_schedule_hour"] = int(hour)
            ok = update_config(cfg)
            self.send_json({"ok": ok, "hour": int(hour)})
            return

        if parsed.path == "/api/restart":
            self.send_json({"ok": True, "message": "Neustart wird ausgeführt"})
            # Kurz waitingn damit Response noch gesendet wird
            def do_restart():
                time.sleep(1)
                os.execv(sys.executable, [sys.executable] + sys.argv)
            threading.Thread(target=do_restart, daemon=True).start()
            return

        if parsed.path == "/api/compression/run":
            # Manually trigger compression.
            # Body: {} → next pending file; {"paths":[...]} → those files; {"all":true} → all pending
            with _compression_lock:
                if _compression_state["current"] is not None:
                    self.send_json({"ok": False, "message": "Compression already running",
                                    "current": _compression_state["current"]})
                    return
            paths = data.get("paths") if isinstance(data, dict) else None
            do_all = bool(data.get("all")) if isinstance(data, dict) else False
            if do_all:
                paths = [p for p, _ in find_pending_compressions()]
            if paths:
                def _bg_sel():
                    compress_selected(paths, manual=True)
                threading.Thread(target=_bg_sel, daemon=True, name="manual-compress").start()
                self.send_json({"ok": True, "message": "Compression started", "count": len(paths)})
            else:
                def _bg():
                    compress_next_pending(manual=True)
                threading.Thread(target=_bg, daemon=True, name="manual-compress").start()
                self.send_json({"ok": True, "message": "Compression started", "count": 1})
            return

        if parsed.path == "/api/compression/pause":
            self.send_json({"ok": pause_compression()})
            return

        if parsed.path == "/api/compression/resume":
            self.send_json({"ok": resume_compression()})
            return

        if parsed.path == "/api/compression/cancel":
            self.send_json({"ok": cancel_compression()})
            return

        self.send_json({"ok": False, "message": "Not found"}, 404)

    def do_PUT(self):
        if not extensions.dispatch(self, "PUT", extensions.read_json_body(self)):
            self.send_json({"ok": False, "message": "Not found"}, 404)

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length)) if length > 0 else {}
        except Exception:
            data = {}

        if extensions.dispatch(self, "DELETE", data if isinstance(data, dict) else {}):
            return

        if path == "/api/recordings/delete":
            # Löscht eine Aufnahme-Datei (für e2recorder)
            filepath = data.get("path", "")
            rcfg = get_recordings_config()
            base = rcfg["path"]
            if not filepath:
                self.send_json({"ok": False, "error": "path fehlt"})
                return
            # Sicherheits-Check: nur Dateien unter recordings_path löschen
            if not os.path.abspath(filepath).startswith(os.path.abspath(base)):
                self.send_json({"ok": False, "error": "Ungültiger Pfad"})
                return
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
                    # Auch .nfo löschen falls vorhanden
                    nfo = filepath.rsplit(".", 1)[0] + ".nfo"
                    if os.path.exists(nfo):
                        os.remove(nfo)
                    # Leere Verzeichnisse aufräumen
                    dirpath = os.path.dirname(filepath)
                    try:
                        if dirpath != base and not os.listdir(dirpath):
                            os.rmdir(dirpath)
                        parent = os.path.dirname(dirpath)
                        if parent != base and not os.listdir(parent):
                            os.rmdir(parent)
                    except Exception:
                        pass
                    log.info(f"Aufnahme gelöscht: {filepath}")
                    self.send_json({"ok": True, "deleted": filepath})
                else:
                    self.send_json({"ok": False, "error": "Datei nicht gefunden"})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})
            return

        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        import time as _t
        _t0 = _t.time()
        _probe = e2probe is not None and e2probe.is_probe_request(self.headers)
        if not _probe:
            note_endpoint(classify_endpoint(self.path, "proxy"))
        try:
            self._do_GET_inner()
            _dur = int((_t.time() - _t0) * 1000)
            if not _probe:
                write_access_log("GET", self.path, self.client_address[0], 200, _dur)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Client hat Verbindung getrennt — normal, kein Fehler
        except Exception as e:
            import traceback
            _dur = int((_t.time() - _t0) * 1000)
            write_access_log("GET", self.path, self.client_address[0], 500, _dur)
            log.error(f"do_GET Fehler ({self.path}): {e}\n{traceback.format_exc()}")
            try:
                self.send_response(500)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(f"Internal Server Error: {e}".encode())
            except Exception:
                pass

    def _do_GET_inner(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)
        client_ip = self.client_address[0]

        if extensions.dispatch(self, "GET"):
            return

        # ── Static Pages ──────────────────────────────────
        if path in ("/", ""):
            self.send_html(build_web_ui(get_channels()))
            return

        if path == "/help":
            self.send_html(build_help_ui())
            return

        if path == "/favorites":
            self.send_html(build_favorites_ui(get_channels()))
            return

        if path == "/epg-browser":
            self.send_html(build_epg_browser())
            return

        if path == "/settings":
            self.send_html(build_settings_ui())
            return

        if path == "/refresh":
            get_channels(force_refresh=True)
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return

        # ── API ───────────────────────────────────────────
        # ── Pre-Zap ───────────────────────────────────────
        # Kodi ruft die Stream-URL manchmal doppelt auf.
        # Wir geben beim ersten Aufruf sofort 200 + leere Daten
        # zurück damit Kodi nicht abbricht.
        if path == "/prezap":
            ref = params.get("ref", [""])[0]
            if ref:
                # Zappt im Hintergrund vor
                rid_pre = get_free_receiver()
                if rid_pre:
                    threading.Thread(
                        target=do_zap, args=(rid_pre, ref), daemon=True
                    ).start()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
            return

        # ── Short URL Routing ─────────────────────────────
        # Suche ob path einer short_url entspricht
        cfg = get_config()
        for pid, dp in cfg.get("device_profiles", {}).items():
            short = dp.get("short_url", "")
            if short and path == f"/{short}":
                proxy_host = cfg.get("proxy_host", "127.0.0.1")
                proxy_port = int(cfg.get("proxy_port", 8888))
                redirect = f"http://{proxy_host}:{proxy_port}/playlist.m3u?profile={urllib.parse.quote(pid)}&list=favorites"
                self.send_response(302)
                self.send_header("Location", redirect)
                self.end_headers()
                return

        if path == "/health":
            self.send_json({"status": "ok", "channels": len(channel_cache["channels"])})
            return

        if path == "/api/version":
            self.send_json({
                "version": VERSION,
                "internal_version": INTERNAL_VERSION,
                "branch": BUILD_BRANCH,
                "build": BUILD_SEQ,
                "service": "e2proxy",
            })
            return

        if path == "/api/logs/files":
            files = []
            try:
                for fn in sorted(os.listdir(DATA_DIR), reverse=True):
                    if not (fn.startswith("e2proxy.log") and fn != "e2proxy.log" or fn == "e2proxy.log"):
                        continue
                    fp = os.path.join(DATA_DIR, fn)
                    if os.path.isfile(fp):
                        files.append({
                            "name":     fn,
                            "size":     os.path.getsize(fp),
                            "modified": datetime.fromtimestamp(os.path.getmtime(fp)).isoformat()
                        })
            except Exception as e:
                pass
            self.send_json(files)
            return

        if path == "/api/access-log":
            # Live aus File (ab "since" Timestamp falls gesetzt)
            since = params.get("since", [None])[0]
            n_param = params.get("n", ["100"])[0]
            try:
                since_f = float(since) if since else None
            except Exception:
                since_f = None
            try:
                n_int = int(n_param) if n_param != "all" else None
            except Exception:
                n_int = 100
            self.send_json({
                "ok": True,
                "enabled": is_api_logging_enabled(),
                "entries": read_access_log(n=n_int, since_unix=since_f),
                "now_unix": time.time(),
            })
            return

        if path == "/api/access-log/history":
            # Historisch: Zeitfenster in Stunden
            hours = params.get("hours", ["6"])[0]
            if hours == "all":
                since_f = None
            else:
                try:
                    since_f = time.time() - (float(hours) * 3600)
                except Exception:
                    since_f = time.time() - 6 * 3600
            entries = read_access_log(n=None, since_unix=since_f)
            self.send_json({
                "ok": True,
                "enabled": is_api_logging_enabled(),
                "hours": hours,
                "count": len(entries),
                "entries": entries,
            })
            return

        if path == "/api/interfaces":
            self.send_json(interface_snapshot())
            return

        if path == "/api/health":
            # Schneller Health-Check für e2recorder (kein EPG-Overhead)
            receivers_health = []
            health = receiver_health_snapshot()
            _isnap = interface_snapshot()
            for r in get_receivers():
                if not r.get("enabled", True):
                    continue
                locked = is_receiver_locked(r)
                state = receivers._receiver_state.get(r["id"])
                _h = health.get(r["id"], {})
                down = _h.get("down", False)
                receivers_health.append({
                    "id":     r["id"],
                    "name":   r["name"],
                    # Kein blockierender Probe: der Circuit Breaker kennt den
                    # Zustand bereits aus echten Zap-/Probe-Ergebnissen.
                    "online": not down,
                    "down":   down,
                    "fails":  _h.get("fails", 0),
                    "unreachable_sec": _h.get("unreachable_sec", 0),
                    "busy":   state is not None,
                    "locked": locked,
                    "channel": state.get("channel_name", "") if state else "",
                })
            self.send_json({
                "ok":          True,
                "version":     "2.1.0",
                "uptime_sec":  int(time.time() - START_TIME),
                "receivers":   receivers_health,
                "tuners_free": sum(1 for r in receivers_health
                                   if not r["busy"] and not r["locked"] and not r["down"]),
                # Ergebnis der Schnittstellen-Probes (gecacht, kein Zusatz-Request)
                "interfaces_state": _isnap["state"],
                "interfaces_down": _isnap["down"],
            })
            return

        if path == "/api/status":
            rx_status = []
            health = receiver_health_snapshot()
            # Check which receivers have active recordings
            rec_by_receiver = {}
            with _active_recordings_lock:
                for rec in _active_recordings.values():
                    rid = rec.get("receiver")
                    if rid:
                        rec_by_receiver[rid] = {
                            "recording_id": rec.get("id", ""),
                            "title": rec.get("title", ""),
                            "started": rec.get("started", ""),
                        }
            for r in get_receivers():
                state = receivers._receiver_state.get(r["id"])
                _h = health.get(r["id"], {})
                rx_status.append({
                    "id": r["id"],
                    "name": r["name"],
                    "busy": state is not None,
                    "locked": is_receiver_locked(r),
                    "online": is_receiver_online(r["id"]),
                    "down": _h.get("down", False),
                    "fails": _h.get("fails", 0),
                    "unhealthy": _h.get("unhealthy", False),
                    "unreachable_sec": _h.get("unreachable_sec", 0),
                    "unreachable_since": _h.get("unreachable_since", ""),
                    "stream": stream_info_public(state),
                    "recording": rec_by_receiver.get(r["id"]),
                })
            self.send_json({"receivers": rx_status})
            return

        if path == "/api/config":
            self.send_json(get_config())
            return

        if path == "/api/switch/stats":
            g = get_switch_global()
            stats = get_switch_stats_snapshot()
            # Nach Aktivität sortiert (meiste Zaps zuerst)
            rows = []
            for ref, e in stats.items():
                z = e.get("zap", {})
                s = e.get("start", {})
                rows.append({
                    "ref": ref,
                    "name": e.get("name") or ref[:40],
                    "no_latency": e.get("no_latency"),
                    "zap_wait": e.get("zap_wait"),
                    "probesize": e.get("probesize"),
                    "nolatency_fail_streak": e.get("nolatency_fail_streak", 0),
                    "nolatency_fail_total": e.get("nolatency_fail_total", 0),
                    "zap_ok": z.get("ok", 0),
                    "zap_fail": z.get("fail", 0),
                    "zap_avg_ms": z.get("avg_ms", 0),
                    "zap_last_ms": z.get("last_ms", 0),
                    "start_ok": s.get("ok", 0),
                    "start_fail": s.get("fail", 0),
                    "start_retries": s.get("retries", 0),
                    "last_update": e.get("last_update"),
                })
            rows.sort(key=lambda r: (r["zap_ok"] + r["zap_fail"]), reverse=True)
            self.send_json({"global": g, "senders": rows})
            return

        if path == "/api/usage/stats":
            self.send_json(build_usage_report())
            return

        if path == "/api/favorites":
            self.send_json(get_favorites())
            return
        if path == "/api/channels/reload":
            channels = get_channels(force_refresh=True)
            self.send_json({"ok": True, "count": len(channels)})
            return

        if path == "/api/diag":
            # Diagnose-Modus abfragen bzw. per Query-Parameter umschalten
            # (?on=1 / ?on=0). Zusätzlich der Live-Zustand aller Streams.
            want = params.get("on", [None])[0]
            if want is not None:
                cfg = get_config()
                cfg["diag_logging"] = want in ("1", "true", "on", "yes")
                update_config(cfg)
                diag_log("diag_mode", enabled=cfg["diag_logging"])
                log.info(f"Diagnose-Modus {'aktiviert' if cfg['diag_logging'] else 'deaktiviert'}")
            self.send_json({
                "ok": True,
                "enabled": diag_enabled(),
                "stall_threshold_s": DIAG_STALL_SEC,
                "sample_interval_s": DIAG_SAMPLE_SEC,
                "streams": diag_active_streams(),
            })
            return

        if path == "/api/logs":
            # Live-Abfrage aus RAM-Buffer (für laufenden Browser)
            level = params.get("level", ["INFO"])[0]
            since = params.get("since", [None])[0]
            n = params.get("n", ["100"])[0]
            try:
                since_f = float(since) if since else None
            except Exception:
                since_f = None
            try:
                n_int = int(n) if n != "all" else None
            except Exception:
                n_int = 100
            entries = get_log_entries(level, since=since_f, n=n_int)
            self.send_json({
                "ok": True,
                "level": level,
                "entries": [{"ts": e["ts"], "ts_unix": e["ts_unix"], "level": e["level"], "msg": e["msg"]} for e in entries],
                "now_unix": time.time(),
            })
            return

        if path == "/api/logs/history":
            # Historische Abfrage aus Disk-Logs (Reload-Button)
            level = params.get("level", ["INFO"])[0]
            # hours zurück (1, 6, 12, 24, 48, 120, oder "all")
            hours = params.get("hours", ["6"])[0]
            if hours == "all":
                since_f = None
            else:
                try:
                    since_f = time.time() - (float(hours) * 3600)
                except Exception:
                    since_f = time.time() - 6 * 3600
            entries = get_log_entries_from_disk(level, since_unix=since_f, max_lines=20000)
            self.send_json({
                "ok": True,
                "level": level,
                "hours": hours,
                "count": len(entries),
                "entries": [{"ts": e["ts"], "ts_unix": e["ts_unix"], "level": e["level"], "msg": e["msg"]} for e in entries],
            })
            return

        if path == "/api/log/level":
            # GET: aktuellen Level abfragen
            lvl_names = {10:"DEBUG", 20:"INFO", 30:"WARNING", 40:"ERROR"}
            self.send_json({"ok": True, "level": lvl_names.get(_current_display_level[0], "INFO")})
            return

        if path == "/api/epg/data":
            # Liefert EPG-Events als JSON fuer den Browser
            # Parsed den XMLTV-Cache und gibt Events pro Kanal zurueck
            try:
                import xml.etree.ElementTree as ET
                import re as _re
                xml_str = get_epg_xml()
                if not xml_str:
                    self.send_json({"ok": False, "channels": []})
                    return
                # Entferne ungueltige XML-Zeichen (Steuerzeichen ausser Tab/LF/CR)
                xml_str = _re.sub(r'[--]', '', xml_str)
                # Ersetze unescapte & die nicht Teil einer Entity sind
                xml_str = _re.sub('&(?!amp;|lt;|gt;|quot;|apos;|#[0-9]+;|#x[0-9a-fA-F]+;)', '&amp;', xml_str)
                try:
                    root = ET.fromstring(xml_str)
                except ET.ParseError as pe:
                    log.warning(f"EPG XML Parse-Fehler: {pe} — versuche Fallback")
                    xml_str = ''.join(c for c in xml_str if c in '\t\n\r' or (0x20 <= ord(c) <= 0x7e) or ord(c) > 0x7f)
                    root = ET.fromstring(xml_str)
                # Channels mit Logo
                channels = {}
                for ch in root.findall("channel"):
                    cid = ch.get("id", "")
                    name_el = ch.find("display-name")
                    icon_el = ch.find("icon")
                    channels[cid] = {
                        "id": cid,
                        "name": name_el.text if name_el is not None else cid,
                        "logo": icon_el.get("src", "") if icon_el is not None else ""
                    }
                # Events pro Kanal
                now_ts = time.time()
                day_start = now_ts - (now_ts % 86400) - 3600  # grob von jetzt - 1h
                day_end = day_start + 48 * 3600  # 48h

                events_by_channel = {}
                for prog in root.findall("programme"):
                    cid = prog.get("channel", "")
                    start_str = prog.get("start", "")
                    stop_str = prog.get("stop", "")
                    if not start_str or not stop_str:
                        continue
                    try:
                        # Parse XMLTV timestamp: "20260528130000 +0000"
                        from datetime import timezone
                        def parse_xmltv_ts(s):
                            s = s.strip()
                            if " " in s:
                                dt_part, tz_part = s.rsplit(" ", 1)
                                sign = 1 if tz_part[0] == "+" else -1
                                th, tm = int(tz_part[1:3]), int(tz_part[3:5])
                                tz_offset = sign * (th * 3600 + tm * 60)
                            else:
                                dt_part, tz_offset = s, 0
                            dt = datetime.strptime(dt_part, "%Y%m%d%H%M%S")
                            return dt.replace(tzinfo=timezone.utc).timestamp() - tz_offset
                        start_ts = parse_xmltv_ts(start_str)
                        stop_ts = parse_xmltv_ts(stop_str)
                    except Exception:
                        continue
                    if stop_ts < day_start or start_ts > day_end:
                        continue
                    title_el = prog.find("title")
                    desc_el = prog.find("desc")
                    sub_el = prog.find("sub-title")
                    ev = {
                        "start": int(start_ts),
                        "stop": int(stop_ts),
                        "title": title_el.text if title_el is not None else "",
                        "desc": desc_el.text if desc_el is not None else "",
                        "sub": sub_el.text if sub_el is not None else "",
                    }
                    events_by_channel.setdefault(cid, []).append(ev)

                # Favoriten-Reihenfolge + Namen aus Favoriten bevorzugen
                favs = load_favorites()
                fav_name = {f["ref"].rstrip("/"): f["name"] for f in favs}
                with channel_cache_lock:
                    all_ch = channel_cache.get("channels", [])
                ref_to_name = {ch["ref"].rstrip("/"): ch["name"] for ch in all_ch}
                ordered = []
                for f in favs:
                    ref = f["ref"].rstrip("/")
                    cid = ref.replace(":", "_")
                    # Channel-Objekt aufbauen — Name aus Favoriten/Cache, nicht aus XMLTV
                    best_name = (fav_name.get(ref)
                                 or ref_to_name.get(ref)
                                 or (channels[cid]["name"] if cid in channels else ref))
                    logo = channels[cid].get("logo", "") if cid in channels else get_logo_for_epg(best_name)
                    ch = {
                        "id": cid,
                        "name": best_name,
                        "logo": logo,
                        "events": sorted(events_by_channel.get(cid, []), key=lambda e: e["start"])
                    }
                    ordered.append(ch)

                self.send_json({"ok": True, "channels": ordered, "now": int(time.time())})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e), "channels": []})
            return

        if path == "/api/plex/sections":
            # Holt Plex Library Sections via Plex API
            rcfg = get_recordings_config()
            plex_url = rcfg.get("plex_url", "").rstrip("/")
            token = rcfg.get("plex_token", "")
            if not plex_url or not token:
                self.send_json({"ok": False, "error": "Plex URL und Token erforderlich"})
                return
            try:
                import urllib.request as _ur
                req = _ur.Request(
                    f"{plex_url}/library/sections?X-Plex-Token={token}",
                    headers={"Accept": "application/json", "User-Agent": "e2proxy/1.0"}
                )
                with _ur.urlopen(req, timeout=8) as r:
                    data = json.loads(r.read())
                sections = []
                for d in data.get("MediaContainer", {}).get("Directory", []):
                    sections.append({
                        "id": str(d.get("key", "")),
                        "title": d.get("title", ""),
                        "type": d.get("type", ""),
                    })
                self.send_json({"ok": True, "sections": sections})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})
            return

        if path == "/api/plex/token":
            username = params.get("username", [None])[0]
            password = params.get("password", [None])[0]
            if not username or not password:
                self.send_json({"ok": False, "error": "username und password erforderlich"})
                return
            try:
                import urllib.request as _ur
                import urllib.parse as _up
                client_id = f"e2proxy-{get_proxy_host().replace('.', '-')}"
                # Plex API v2: POST mit Form-Data (nicht Basic Auth)
                body = _up.urlencode({
                    "login": username,
                    "password": password,
                    "rememberMe": "0",
                }).encode()
                req = _ur.Request(
                    "https://plex.tv/api/v2/users/signin",
                    data=body,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "X-Plex-Client-Identifier": client_id,
                        "X-Plex-Product": "e2proxy",
                        "X-Plex-Version": "2.0",
                        "Accept": "application/json",
                    }
                )
                with _ur.urlopen(req, timeout=10) as r:
                    data = json.loads(r.read())
                token = data.get("authToken") or data.get("auth_token", "")
                if token:
                    log.info(f"Plex Token generiert für {username}")
                    self.send_json({"ok": True, "token": token,
                                    "username": data.get("username", username)})
                else:
                    self.send_json({"ok": False, "error": "Kein Token in Antwort"})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})
            return

        if path == "/recording/stream":
            # Streamt eine aufgenommene Datei mit Range-Support für Browser-Seeking
            filepath = params.get("file", [""])[0]
            rcfg = get_recordings_config()
            base = os.path.abspath(rcfg["path"])
            if not filepath or not os.path.abspath(filepath).startswith(base):
                self.send_response(403)
                self.end_headers()
                return
            if not os.path.exists(filepath):
                self.send_response(404)
                self.end_headers()
                return
            file_size = os.path.getsize(filepath)
            range_header = self.headers.get("Range", "")
            start, end = 0, file_size - 1
            status = 200
            if range_header and range_header.startswith("bytes="):
                try:
                    r = range_header[6:].split("-")
                    start = int(r[0]) if r[0] else 0
                    end   = int(r[1]) if len(r) > 1 and r[1] else file_size - 1
                    end   = min(end, file_size - 1)
                    status = 206
                except Exception:
                    pass
            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.end_headers()
            try:
                with open(filepath, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        if path == "/api/tuners":
            self.send_json(get_tuner_status())
            return

        if path == "/api/record/status":
            self.send_json({"ok": True, "recordings": get_recording_status()})
            return

        if path == "/api/compression/status":
            # Returns current compression status, pending count, recent history, backlog warning
            try:
                cfg = get_compression_config()
                pending = find_pending_compressions()
                with _compression_lock:
                    current = dict(_compression_state["current"]) if _compression_state["current"] else None
                history = _load_compression_history()[-20:]  # last 20
                history.reverse()  # newest first
                self.send_json({
                    "ok": True,
                    "config": cfg,
                    "profiles": COMPRESSION_PROFILES,
                    "in_window": _is_in_window(),
                    "current": current,
                    "pending_count": len(pending),
                    "pending_size_bytes": sum(s for _, s in pending),
                    "pending_files": [{"path": p, "size": s} for p, s in pending[:50]],
                    "backlog": has_compression_backlog(),
                    "history": history,
                })
            except Exception as e:
                self.send_json({"ok": False, "message": str(e)})
            return
        
        if path == "/api/recordings":
            # Liste aller Aufnahme-Dateien auf Disk (für e2recorder)
            try:
                rcfg = get_recordings_config()
                rec_path = rcfg["path"]
                result = []
                if os.path.exists(rec_path):
                    for root_dir, dirs, files in os.walk(rec_path):
                        for fname in sorted(files):
                            if not fname.endswith((".ts", ".mkv", ".mp4")):
                                continue
                            fpath = os.path.join(root_dir, fname)
                            rel = os.path.relpath(fpath, rec_path)
                            parts = rel.split(os.sep)
                            size = os.path.getsize(fpath)
                            mtime = os.path.getmtime(fpath)
                            # Struktur: Serie / Staffel / Datei
                            series = parts[0] if len(parts) > 1 else ""
                            season = parts[1] if len(parts) > 2 else ""
                            result.append({
                                "path": fpath,
                                "relative": rel,
                                "filename": fname,
                                "series": series,
                                "season": season,
                                "size_mb": round(size / 1024 / 1024, 1),
                                "modified": datetime.fromtimestamp(mtime).isoformat(),
                            })
                self.send_json({"ok": True, "recordings": result, "base_path": rec_path})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e), "recordings": []})
            return

        if path == "/api/epg/history":
            try:
                runs = []
                if os.path.exists(EPG_RUNS_FILE):
                    with open(EPG_RUNS_FILE) as f:
                        runs = json.load(f)
                self.send_json({"ok": True, "runs": runs})
            except Exception as e:
                self.send_json({"ok": False, "runs": [], "error": str(e)})
            return

        if path == "/api/epg/status":
            with epg_run_lock:
                self.send_json({
                    "running": epg_run_state["running"],
                    "phase": epg_run_state["phase"],
                    "progress": epg_run_state["progress"],
                    "total": epg_run_state["total"],
                    "done": epg_run_state["done"],
                    "log": epg_run_state["log"][-50:],
                    "last_run": epg_run_state["last_run"],
                    "last_duration": epg_run_state["last_duration"],
                    "last_result": epg_run_state["last_result"],
                    "schedule_hour": get_epg_schedule_hour(),
                    "tmdb_total": epg_run_state["tmdb_total"],
                    "tmdb_done": epg_run_state["tmdb_done"],
                    "tmdb_items": list(epg_run_state["tmdb_items"]),
                    "tmdb_all": list(epg_run_state["tmdb_all"]),
                })
            return

        if path == "/api/epg/run":
            if epg_run_state["running"]:
                self.send_json({"ok": False, "message": "EPG-Run läuft bereits"})
            else:
                threading.Thread(target=lambda: run_epg_update("manual"), daemon=True).start()
                self.send_json({"ok": True, "message": "EPG-Run started"})
            return

        if path == "/api/plex/dvr-refresh":
            rcfg = get_recordings_config()
            if not (rcfg.get("plex_url") and rcfg.get("plex_token")):
                self.send_json({"ok": False, "message": "Plex URL/Token nicht konfiguriert"})
                return
            threading.Thread(target=_plex_dvr_refresh_guide, args=(rcfg,), daemon=True).start()
            self.send_json({"ok": True, "message": "Plex DVR guide refresh started"})
            return

        if path == "/api/logos/check":
            broken = check_favorite_logos()
            self.send_json({"ok": True, "broken": broken, "count": len(broken)})
            return

        if path == "/api/favorites/logos":
            self.send_json({"ok": True, "logos": get_favorite_logo_overview()})
            return

        if path == "/kill":
            rid = params.get("receiver", [None])[0]
            if rid and get_receiver_by_id(rid):
                kill_stream(rid)
                self.send_json({"ok": True, "message": f"Stream auf '{rid}' abgebrochen"})
            else:
                self.send_json({"ok": False, "message": f"Receiver '{rid}' nicht gefunden"})
            return

        # ── Logo Cache ────────────────────────────────────
        if path == "/logos/refresh":
            threading.Thread(target=lambda: refresh_logo_cache(force=True), daemon=True).start()
            self.send_response(302)
            self.send_header("Location", "/settings")
            self.send_header("Connection", "close")
            self.end_headers()
            return

        if path.startswith("/custom_logos/"):
            fname = path[len("/custom_logos/"):]
            if "/" in fname or ".." in fname:
                self.send_text("Not found", 404)
                return
            fpath = os.path.join(CUSTOM_LOGO_DIR, fname)
            if os.path.exists(fpath):
                with open(fpath, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_text("Not found", 404)
            return

        if path.startswith("/logos/"):
            fname = path[len("/logos/"):]
            # Sicherheit: kein path traversal
            if "/" in fname or ".." in fname:
                self.send_text("Not found", 404)
                return
            fpath = os.path.join(LOGO_CACHE_DIR, fname)
            if os.path.exists(fpath):
                with open(fpath, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_text("Not found", 404)
            return

        # ── EPG XML ───────────────────────────────────────
        if path == "/epg.xml":
            force = "refresh" in params
            xml = get_epg_xml(force_refresh=force)
            if xml:
                body = xml.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/xml; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_text("EPG nicht verfügbar", 503)
            return

        # ── EPG Refresh ───────────────────────────────────
        if path == "/epg/refresh":
            threading.Thread(target=lambda: get_epg_xml(force_refresh=True), daemon=True).start()
            self.send_response(302)
            self.send_header("Location", "/settings")
            self.end_headers()
            return

        # ── HDHomeRun Emulation (Plex DVR) ───────────────
        if path == "/discover.json":
            self.send_json(build_hdhr_discover())
            return

        if path == "/lineup_status.json":
            self.send_json(build_hdhr_lineup_status())
            return

        if path == "/lineup.json":
            # Plex-spezifischer Endpoint liefert lineup mit profile=plex
            if params.get("_src", [""])[0] == "plex":
                self.send_json(build_hdhr_lineup_plex())
            else:
                self.send_json(build_hdhr_lineup())
            return

        # ── Plex-spezifische HDHomeRun Endpoints (/plex/...) ─────────
        # Eigenstaendige Endpoints fuer Plex damit Threadfin komplett
        # aus dem Spiel ist. Plex wird mit http://IP:PORT/plex konfiguriert.
        if path == "/plex/discover.json":
            disc = build_hdhr_discover()
            disc["LineupURL"] = f"http://{disc['BaseURL'].split('//')[1]}/plex/lineup.json"
            disc["BaseURL"]   = disc["BaseURL"] + "/plex"
            self.send_json(disc)
            return

        if path == "/plex/lineup_status.json":
            self.send_json(build_hdhr_lineup_status())
            return

        if path == "/plex/lineup.json":
            self.send_json(build_hdhr_lineup_plex())
            return

        if path == "/plex/device.xml":
            body = build_hdhr_device_xml().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path in ("/device.xml", "/capability"):
            body = build_hdhr_device_xml().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # ── M3U Playlist ──────────────────────────────────
        if path == "/playlist.m3u":
            profile_name = params.get("profile", [get_default_profile()])[0]
            list_type = params.get("list", ["all"])[0]
            channels = get_channels()
            m3u = build_m3u(channels, profile_name, favorites_only=(list_type == "favorites"))
            fname = f"{profile_name.replace(' ','_')}-{'favorites' if list_type == 'favorites' else 'all'}.m3u"
            body = m3u.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/x-mpegurl")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # ── Stream ────────────────────────────────────────
        if path == "/stream":
            if "ref" not in params:
                self.send_text("Missing: ref", 400)
                return

            # Hängender Consumer (z.B. Aufnahme-ffmpeg mit Disk-Stall) darf den Handler
            # nicht dauerhaft in wfile.write() blockieren — sonst bleibt der Tuner (und
            # bei Live das ref_lock) für immer belegt. Socket-Timeout → write wirft nach
            # 60s Stillstand → Handler räumt sauber auf.
            try:
                self.connection.settimeout(60)
            except Exception:
                pass

            service_ref = params["ref"][0].strip()
            profile_name = params.get("profile", [get_default_profile()])[0]
            # Channel-Name aus Cache nachschlagen (Plex übergibt keinen name Parameter)
            _ch_lookup = {ch["ref"].rstrip("/"): ch["name"] for ch in get_channels()}
            channel_name = params.get("name", [None])[0] or _ch_lookup.get(service_ref.rstrip("/"), service_ref[:30])

            dp = get_device_profile(profile_name)
            if not dp:
                # Fallback: profile als transcode profile id behandeln (legacy)
                tp = get_transcode_profile(profile_name)
                receiver_pref = "auto"
            else:
                tp_name = dp.get("transcode_profile", "webm-sd")
                tp = get_transcode_profile(tp_name)
                receiver_pref = dp.get("receiver", "auto")

            if not tp:
                self.send_text(f"Unbekanntes Profil: {profile_name}", 400)
                return

            ua = self.headers.get("User-Agent", "?")
            log.info(f"STREAM [{profile_name}] from {client_ip}: {channel_name} [{ua[:40]}]")

            # Receiver wählen — preacquired hat Vorrang (Aufnahme hat ihn bereits belegt)
            #
            # Dedup-Lock NUR für Live-Anfragen (z.B. Jellyfin Probe + Stream, die fast
            # gleichzeitig denselben Sender öffnen). Aufnahmen (preacquired) sind bereits
            # über den Receiver koordiniert (jede belegt einen eigenen Tuner) und dürfen
            # Live-Zuschauer desselben Senders NICHT blockieren — sonst sperrt jede
            # laufende oder hängende VOX-Aufnahme das Live-Schauen von VOX (429).
            preacquired = params.get("preacquired", [None])[0]
            ref_lock = None
            if preacquired:
                rid = preacquired
                # Nicht neu acquiren, aber mitzählen: sonst gibt das finally
                # unten den Receiver der Aufnahme frei, obwohl sie weiterläuft.
                rcv_epoch = attach_receiver(rid)
                log.debug(f"Stream: Receiver '{rid}' bereits durch Aufnahme belegt — angehängt")
            else:
                ref_lock = get_ref_lock(service_ref)
                if not ref_lock.acquire(blocking=False):
                    log.warning(f"Duplicate request for {service_ref} — rejected (429)")
                    self.send_text("Stream für diesen Sender läuft bereits", 429)
                    return
                # Suchen und Belegen MÜSSEN atomar sein — sonst schnappt sich ein
                # paralleler Request denselben "freien" Receiver und zappt den
                # gerade startenden Stream weg.
                acquire_kwargs = {
                    "client_ip": client_ip,
                    "service_ref": service_ref,
                    "channel_name": channel_name,
                    "user_agent": ua,
                    "profile": profile_name,
                    "transcode": {
                        "active": tp.get("codec") != "pass",
                        "codec": tp.get("codec", ""),
                        "label": tp.get("label", profile_name),
                    },
                }
                with receiver_lock:
                    rid = get_free_receiver(receiver_pref if receiver_pref != "auto" else None)
                    if rid:
                        rcv_epoch = acquire_receiver(rid, **acquire_kwargs)
                if not rid:
                    ref_lock.release()
                    self.send_text("All receivers busy", 503)
                    return

            try:
                if preacquired:
                    zap_ok = do_zap(rid, service_ref, channel_name)
                else:
                    # Defekter Tuner darf den Stream nicht scheitern lassen,
                    # solange ein anderer Receiver frei ist.
                    rid, rcv_epoch = zap_with_failover(
                        rid, rcv_epoch, service_ref, channel_name, acquire_kwargs,
                        preferred_id=(receiver_pref if receiver_pref != "auto" else None),
                        source="Stream")
                    zap_ok = rid is not None
                if not zap_ok:
                    self.send_text("Zap fehlgeschlagen", 502)
                    return

                # Warten bis Receiver umgeschaltet hat — pro Sender konfigurierbar
                # (globaler Default zap_wait_sec, Override im Switch-Store).
                zap_wait = get_switch_settings(service_ref)["zap_wait"]
                if zap_wait > 0:
                    time.sleep(zap_wait)

                if tp.get("codec") == "pass":
                    content_type = "video/mp2t"
                else:
                    _, content_type = build_ffmpeg_cmd(rid, service_ref, tp)

                # Plex DVR Segmenter braucht HTTP/1.1 + chunked encoding —
                # sonst bricht er den Stream sofort ab ("Stopping idle session").
                # Jellyfin und andere Clients bekommen normalen HTTP/1.0 Stream.
                # Erkennung: Plex verwendet profile=Threadfin.
                # chunked_http Flag im Device-Profil steuert HTTP/1.1 chunked streaming.
                # Plex DVR braucht das, Jellyfin nicht.
                # Fallback: profile_name == "Threadfin" oder "plex" fuer Abwaertskompatibilitaet.
                use_chunked = (
                    dp.get("chunked_http", False) if dp else False
                ) or profile_name.lower() in ("threadfin", "plex")

                if use_chunked:
                    self.protocol_version = "HTTP/1.1"
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Cache-Control", "no-cache, no-store")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.send_header("X-Content-Duration", "0")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Connection", "close")
                    self.send_header("X-Content-Duration", "0")
                    self.end_headers()

                if tp.get("codec") == "pass":
                    stream_passthrough(rid, service_ref, self.wfile, use_chunked)
                else:
                    stream_transcoded(rid, service_ref, tp, self.wfile, use_chunked, channel_name)

            except ScrambledStreamError:
                log.warning(f"SCRAMBLED: {service_ref}")
                # 451 zurückgeben damit Client nicht sofort neu versucht
                try:
                    self.send_error(451, "Sender verschluesselt")
                except:
                    pass
            except Exception as e:
                log.error(f"STREAM ERROR: {e}")
            finally:
                # Nutzung erfassen, BEVOR der Receiver-State verworfen wird
                # (dort stehen Startzeit und übertragene Bytes).
                try:
                    _st = receivers._receiver_state.get(rid) or {}
                    _t0 = _st.get("started_ts")
                    if _t0:
                        record_usage_session(
                            service_ref, channel_name, user_agent=ua,
                            client_ip=client_ip, profile=profile_name,
                            source="Streaming (/stream)", started_ts=_t0,
                            duration_sec=time.time() - _t0,
                            bytes_sent=_st.get("bytes_sent", 0))
                except Exception as _e:
                    log.debug(f"Usage-Hook (stream) fehlgeschlagen: {_e}")
                # rid ist None, wenn das Failover auf allen Tunern scheiterte —
                # dann sind sie dort bereits freigegeben.
                if rid:
                    release_receiver(rid, epoch=rcv_epoch)
                if ref_lock is not None:
                    ref_lock.release()
            return

        self.send_text("Not found", 404)
