"""Interface health probes (Plex + OpenWebif) built on e2proxy.probe."""
import json
import urllib.parse
import urllib.request
from e2proxy import probe as e2probe

from e2proxy.logs import log
from e2proxy.config import (
    get_owif_config,
    get_proxy_host,
    get_proxy_port,
    get_receivers,
    is_receiver_usable,
)
from e2proxy.favorites import get_favorites
from e2proxy.receivers import get_free_receiver, is_receiver_unhealthy
from e2proxy.hdhr import build_hdhr_lineup
from e2proxy.notify import get_receiver_alert_config, send_receiver_alert
from e2proxy.openwebif import _owif_metadata_receiver
from e2proxy import receivers

# ── Schnittstellen-Health-Probes (Modul e2probe.py) ───────────────────────────
# Prüft periodisch von außen, ob Plex (HDHomeRun-Emulation) und die Enigma2-Apps
# (OpenWebif-Emulation) bedient werden können. Das Modul ist optional: fehlt die
# Datei, läuft e2proxy unverändert ohne Probes weiter.

INTERFACE_PROBE_INTERVAL = 60
_interface_monitor = None


def _probe_tuners():
    """Kann Plex gerade einen Sender einstellen? (freier, erreichbarer Tuner)"""
    usable = [r for r in get_receivers() if is_receiver_usable(r)]
    if not usable:
        return "down", "Kein Receiver aktiviert"
    healthy = [r for r in usable if not is_receiver_unhealthy(r["id"])]
    free = [r for r in healthy if receivers._receiver_state.get(r["id"]) is None]
    dead = [r["name"] for r in usable if r not in healthy]
    tail = f" · nicht erreichbar: {', '.join(dead)}" if dead else ""
    if not healthy:
        return "down", "Alle Receiver nicht erreichbar — Plex: „Sender konnte nicht eingestellt werden“"
    if not free:
        return "warn", f"Alle {len(healthy)} erreichbaren Tuner belegt{tail}"
    if dead:
        return "warn", f"{len(free)}/{len(usable)} Tuner frei{tail}"
    return "ok", f"{len(free)}/{len(usable)} Tuner frei"


def _check_health_json(obj):
    return ("ok", "API antwortet") if obj.get("ok") else ("down", "ok=false")


def _check_discover(obj):
    n = int(obj.get("TunerCount", 0) or 0)
    if n <= 0:
        return "down", "TunerCount=0 — Plex sieht keine Tuner"
    return "ok", f"{n} Tuner gemeldet"


def _check_lineup(obj):
    if not isinstance(obj, list) or not obj:
        return "down", "Senderliste leer — Plex hat nichts zum Einstellen"
    return "ok", f"{len(obj)} Sender"


def _check_owif_about(status, body):
    if not 200 <= status < 300:
        return "down", f"HTTP {status} (Metadaten-Receiver antwortet nicht)"
    r = _owif_metadata_receiver()
    return "ok", f"Metadaten über {r['name'] if r else '?'}"


def _check_owif_services(status, body):
    if not 200 <= status < 300:
        return "down", f"HTTP {status}"
    obj = json.loads(body.decode("utf-8", "replace"))
    n = len(obj.get("services") or [])
    return ("ok", f"{n} Bouquets") if n else ("down", "keine Bouquets")


def interface_probe_targets():
    port = get_proxy_port()
    base = f"http://127.0.0.1:{port}"
    targets = [
        {"id": "web", "group": "e2proxy", "label": "Web-UI / API", "kind": "http",
         "url": f"{base}/api/health", "timeout": 5, "warn_ms": 1500,
         "check": e2probe.check_json(_check_health_json)},
        {"id": "plex_discover", "group": "Plex", "label": "HDHomeRun discover.json",
         "kind": "http", "url": f"{base}/discover.json", "timeout": 5, "warn_ms": 1500,
         "check": e2probe.check_json(_check_discover)},
        {"id": "plex_lineup", "group": "Plex", "label": "HDHomeRun lineup.json",
         "kind": "http", "url": f"{base}/lineup.json", "timeout": 10, "warn_ms": 3000,
         "check": e2probe.check_json(_check_lineup)},
        {"id": "tuners", "group": "Plex", "label": "Tuner verfügbar", "kind": "func",
         "fn": _probe_tuners},
    ]
    owif = get_owif_config()
    if owif["enabled"]:
        wb = f"http://127.0.0.1:{owif['webif_port']}"
        targets += [
            {"id": "owif_about", "group": "OpenWebif", "label": "Metadaten (/web/about)",
             "kind": "http", "url": f"{wb}/web/about", "timeout": 8, "warn_ms": 3000,
             "check": _check_owif_about},
            {"id": "owif_bouquets", "group": "OpenWebif", "label": "Bouquets (/api/getservices)",
             "kind": "http", "url": f"{wb}/api/getservices", "timeout": 10, "warn_ms": 4000,
             "check": _check_owif_services},
            {"id": "owif_stream", "group": "OpenWebif", "label": "Stream-Port",
             "kind": "tcp", "port": owif["stream_port"]},
        ]
        if owif.get("recordings_enabled", True) and owif.get("recordings_port"):
            targets.append({"id": "owif_recordings", "group": "OpenWebif",
                            "label": "Aufnahmen-Port", "kind": "tcp",
                            "port": owif["recordings_port"]})
    return targets


def _on_interface_change(target, old, new, res):
    if new == "down":
        log.warning(f"DIAG event=interface_state id={target['id']} state=down "
                    f"group={target.get('group', '')} detail=\"{res.get('detail', '')}\"")
    else:
        log.info(f"DIAG event=interface_state id={target['id']} state=up "
                 f"group={target.get('group', '')}")
    ac = get_receiver_alert_config()
    if ac["enabled"] and ac["url"]:
        pseudo = {"id": target["id"], "name": f"{target.get('group', '')}: {target.get('label', '')}",
                  "ip": ""}
        since = res.get("since", 0.0) if new != "down" else 0.0
        send_receiver_alert("interface_down" if new == "down" else "interface_up", pseudo, since)


def start_interface_monitor():
    global _interface_monitor
    if e2probe is None:
        log.warning("Schnittstellen-Probes deaktiviert: e2probe.py nicht gefunden")
        return
    _interface_monitor = e2probe.InterfaceMonitor(
        interface_probe_targets, interval=INTERFACE_PROBE_INTERVAL,
        fail_threshold=2, on_change=_on_interface_change, logger=log)
    _interface_monitor.start(initial_delay=45)


def interface_snapshot():
    if _interface_monitor is None:
        return {"available": False, "state": "unknown", "ok": True, "down": [], "probes": []}
    snap = _interface_monitor.snapshot()
    snap["available"] = True
    return snap


def interface_stream_test(kind, ref=""):
    """Echter Stream-Test über den Plex- bzw. OpenWebif-Pfad (belegt einen Tuner)."""
    if e2probe is None:
        return {"ok": False, "detail": "e2probe.py nicht gefunden"}
    if _probe_tuners()[0] == "down" or get_free_receiver() is None:
        return {"ok": False, "detail": "Kein freier, erreichbarer Tuner — Test übersprungen"}
    favs = get_favorites()
    if not ref:
        if not favs:
            return {"ok": False, "detail": "Keine Favoriten — kein Test-Sender"}
        ref = favs[0]["ref"]
    name = next((f.get("name", "") for f in favs if f["ref"] == ref), "") or ref
    if kind == "openwebif":
        owif = get_owif_config()
        if not owif["enabled"]:
            return {"ok": False, "detail": "OpenWebif-Emulation ist deaktiviert"}
        url = f"http://127.0.0.1:{owif['stream_port']}/{ref}"
    else:
        lineup = build_hdhr_lineup()
        def _entry_ref(e):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(e.get("URL", "")).query)
            return (q.get("ref") or [""])[0]
        entry = next((e for e in lineup if _entry_ref(e) == ref), None)
        if not entry:
            return {"ok": False, "detail": f"{name} nicht in der Plex-Senderliste"}
        url = entry["URL"].replace(f"{get_proxy_host()}:{get_proxy_port()}",
                                   f"127.0.0.1:{get_proxy_port()}", 1)
    res = e2probe.stream_probe(url, seconds=8)
    res["channel"] = name
    res["kind"] = kind
    log.info(f"DIAG event=stream_test kind={kind} ok={res['ok']} channel=\"{name}\" "
             f"ttfb_ms={res.get('ttfb_ms')} bytes={res.get('bytes')} detail=\"{res['detail']}\"")
    return res


