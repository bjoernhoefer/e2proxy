"""Application startup: wires all components and runs the HTTP server."""
import http.server
import json
import os
import signal
import sys
import threading

from e2proxy.version import INTERNAL_VERSION, VERSION
from e2proxy.paths import CONFIG_FILE, DATA_DIR
from e2proxy.logs import _setup_file_logging, log
from e2proxy.config import get_config, get_proxy_host, get_proxy_port, get_receivers, load_config
from e2proxy.channels import get_channels
from e2proxy.logos import refresh_logo_cache
from e2proxy.receivers import _init_receiver_state, receiver_watchdog_loop
from e2proxy.diag import _diag_watchdog_loop
from e2proxy.metadata import TMDB_CACHE_FILE
from e2proxy.epg import (
    apply_custom_logos_to_epg_cache,
    epg_cache,
    epg_cache_lock,
    epg_scheduler_loop,
    load_epg_from_disk,
    run_epg_update,
)
from e2proxy.usage import usage_flush_loop
from e2proxy.recordings import recording_reaper_loop
from e2proxy.compression import start_compression_scheduler
from e2proxy.notify import maintenance_notify_loop
from e2proxy.ssdp import SSDP_MCAST_ADDR, SSDP_PORT, start_ssdp_server
from e2proxy.openwebif import start_openwebif_server
from e2proxy.interfaces import start_interface_monitor
from e2proxy.webui.selfcheck import webui_selfcheck
from e2proxy.http_proxy import ProxyHandler

# ── Main ──────────────────────────────────────────────────

def _raise_open_files_limit(target=8192):
    """Hebt das Soft-Limit für offene Dateien an (Defense-in-Depth gegen
    fd-Exhaustion / "Too many open files"). Best-effort, schlägt nie hart fehl."""
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        desired = target if hard == resource.RLIM_INFINITY else min(target, hard)
        if soft < desired:
            resource.setrlimit(resource.RLIMIT_NOFILE, (desired, hard))
            log.info(f"Open-files Limit angehoben: {soft} → {desired} (hard {hard})")
    except Exception as e:
        log.warning(f"Open-files Limit konnte nicht angehoben werden: {e}")


def run():
    os.makedirs(DATA_DIR, exist_ok=True)
    _raise_open_files_limit()
    load_config()
    _setup_file_logging(get_config().get("log_retention_days", 5))
    _init_receiver_state()

    # TMDB Cache beim Start löschen — frische Lookups nach jedem Neustart
    if os.path.exists(TMDB_CACHE_FILE):
        try:
            os.remove(TMDB_CACHE_FILE)
            log.info("TMDB cache cleared (restart)")
        except Exception as e:
            log.warning(f"TMDB Cache löschen fehlgeschlagen: {e}")

    # EPG: Disk-Cache sofort laden für schnelle Verfügbarkeit
    disk_xml, disk_ts = load_epg_from_disk()
    if disk_xml:
        with epg_cache_lock:
            epg_cache["xml"] = disk_xml
            epg_cache["last_update"] = disk_ts
        log.info("EPG preloaded from disk cache")
        # Custom-Logos in den gecachten EPG einpatchen (falls seit dem letzten
        # EPG-Run neue eigene Logos hinterlegt wurden).
        try:
            apply_custom_logos_to_epg_cache()
        except Exception as e:
            log.warning(f"EPG-Logo-Patch beim Start fehlgeschlagen: {e}")

    def _announce_to_recorder():
        """Sendet Startup-Announce an e2recorder falls konfiguriert."""
        cfg = get_config()
        rec_url = cfg.get("recorder_url", "").rstrip("/")
        if not rec_url:
            return
        try:
            import urllib.request as _ur
            payload = json.dumps({
                "url":  f"http://{get_proxy_host()}:{get_proxy_port()}",
                "name": cfg.get("proxy_name", "Wien"),
            }).encode()
            req = _ur.Request(
                f"{rec_url}/api/proxy/announce",
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "e2proxy/2.1"},
                method="POST"
            )
            with _ur.urlopen(req, timeout=5) as r:
                log.info(f"Startup-Announce an e2recorder: {r.status}")
        except Exception as e:
            log.debug(f"Startup-Announce fehlgeschlagen (e2recorder nicht erreichbar): {e}")

    def startup_sequence():
        """Channels laden, dann EPG-Run — Reihenfolge wichtig für Zap-Reload."""
        log.info("Startup: Loading channel list...")
        get_channels(force_refresh=True)
        log.info("Startup: Channel list loaded — starting EPG run...")
        # Announce parallel starten (nicht blockieren)
        threading.Thread(target=_announce_to_recorder, daemon=True).start()
        run_epg_update("startup")

    threading.Thread(target=startup_sequence, daemon=True).start()
    threading.Thread(target=refresh_logo_cache, daemon=True).start()
    threading.Thread(target=epg_scheduler_loop, daemon=True).start()
    threading.Thread(target=maintenance_notify_loop, daemon=True).start()
    threading.Thread(target=recording_reaper_loop, daemon=True, name="rec-reaper").start()
    threading.Thread(target=usage_flush_loop, daemon=True, name="usage-flush").start()
    threading.Thread(target=_diag_watchdog_loop, daemon=True, name="diag-watchdog").start()
    threading.Thread(target=receiver_watchdog_loop, daemon=True, name="rx-watchdog").start()
    start_interface_monitor()
    start_compression_scheduler()

    proxy_host = get_proxy_host()
    proxy_port = get_proxy_port()

    # SSDP Discovery starten (Plex findet den Proxy automatisch)
    start_ssdp_server()

    # OpenWebif-Emulation ("virtuelle Enigma2-Box") starten, falls aktiviert
    start_openwebif_server()

    server = http.server.ThreadingHTTPServer(("0.0.0.0", proxy_port), ProxyHandler)

    log.info("=" * 55)
    log.info(f"e2proxy v{VERSION} started")
    log.info(f"Build:     {INTERNAL_VERSION}")
    log.info(f"Web-UI:    http://{proxy_host}:{proxy_port}/")
    log.info(f"Settings:  http://{proxy_host}:{proxy_port}/settings")
    log.info(f"Config:    {CONFIG_FILE}")
    log.info(f"Receiver:  {', '.join(r['name'] for r in get_receivers())}")
    log.info(f"Plex DVR:  http://{proxy_host}:{proxy_port}/discover.json")
    log.info(f"SSDP:      UDP {SSDP_MCAST_ADDR}:{SSDP_PORT} (auto-discovery)")
    log.info("=" * 55)

    # WebUI self-check — catches corrupted embedded JS before users hit a dead UI
    try:
        _ui_problems = webui_selfcheck()
        if _ui_problems:
            log.error("WebUI self-check FAILED (%d problem(s)) — the UI may be broken:", len(_ui_problems))
            for _p in _ui_problems:
                log.error("  UI: %s", _p)
        else:
            log.info("WebUI self-check: OK (all pages' JavaScript parses cleanly)")
    except Exception as _e:
        log.warning(f"WebUI self-check could not run: {_e}")

    def shutdown(sig, frame):
        log.info("Proxy stopping...")
        threading.Thread(target=server.shutdown, daemon=True).start()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Gestoppt.")




def selfcheck():
    """Offline pre-deploy check of the embedded WebUI JavaScript (no server start).

    Returns the process exit code: 0 = OK, 1 = at least one problem.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        load_config()
    except Exception:
        pass
    problems = webui_selfcheck()
    if problems:
        print(f"WebUI self-check FAILED — {len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"WebUI self-check OK — internal version {INTERNAL_VERSION}")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--selfcheck" in argv:
        sys.exit(selfcheck())
    run()
