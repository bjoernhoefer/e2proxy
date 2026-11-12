"""Maintenance notifications and receiver outage alerts (webhooks)."""
import json
import socket
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

from e2proxy.logs import log
from e2proxy.config import get_config, get_proxy_port, get_receivers
from e2proxy.receivers import RECEIVER_FAIL_THRESHOLD, _health_entry, _receiver_health_lock
from e2proxy.recordings import _active_recordings, _active_recordings_lock, get_tuner_status

# ── Maintenance Notifications ─────────────────────────────
# Sendet zu konfigurierten Zeitpunkten einen HTTP-Call an externe Systeme
# (z.B. um Wartungsarbeiten anzustoßen). Optional nur im Leerlauf.

MAINT_NOTIFY_DEFAULT = {
    "enabled": False,
    "url": "",
    "method": "POST",          # GET oder POST
    "hour": 4,                  # 0-23
    "minute": 0,               # 0-59
    "days": [0, 1, 2, 3, 4, 5, 6],  # 0=Mo .. 6=So (datetime.weekday())
    "idle_mode": "always",     # "always" | "idle_only"
}

# Verhindert, dass sich Maintenance-Notifications gegenseitig auslösen
# (Amplification/Endlosschleife → fd-/Thread-Exhaustion). Es darf zu jedem
# Zeitpunkt nur EINE Notification gleichzeitig laufen.
_maint_notify_lock = threading.Lock()

def get_maint_notify_config():
    cfg = get_config().get("maintenance_notifications", {})
    merged = dict(MAINT_NOTIFY_DEFAULT)
    if isinstance(cfg, dict):
        merged.update(cfg)
    # Defensive Normalisierung
    try:
        merged["hour"] = max(0, min(23, int(merged.get("hour", 0))))
    except (TypeError, ValueError):
        merged["hour"] = 0
    try:
        merged["minute"] = max(0, min(59, int(merged.get("minute", 0))))
    except (TypeError, ValueError):
        merged["minute"] = 0
    merged["method"] = "GET" if str(merged.get("method", "POST")).upper() == "GET" else "POST"
    if merged.get("idle_mode") not in ("always", "idle_only"):
        merged["idle_mode"] = "always"
    days = merged.get("days", [])
    if not isinstance(days, list):
        days = []
    merged["days"] = sorted({int(d) for d in days if isinstance(d, (int, float)) and 0 <= int(d) <= 6})
    merged["enabled"] = bool(merged.get("enabled", False))
    merged["url"] = str(merged.get("url", "") or "")
    return merged

def is_system_idle():
    """True, wenn aktuell keine Aufnahme läuft UND niemand fernsieht (kein Tuner belegt)."""
    with _active_recordings_lock:
        if _active_recordings:
            return False
    try:
        if get_tuner_status().get("busy", 0) > 0:
            return False
    except Exception:
        pass
    return True

def _is_self_url(url):
    """True, wenn die URL auf diese e2proxy-Instanz selbst zeigt.

    Schützt vor einer Endlosschleife: zeigt die Maintenance-URL auf den eigenen
    HTTP-Port (z.B. .../api/maintenance/notify/test), würde jeder Aufruf rekursiv
    weitere Aufrufe auslösen, bis Threads/Dateideskriptoren erschöpft sind
    ("Too many open files") und der Server hängt.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").strip().lower()
        if not host:
            return False
        try:
            proxy_port = int(get_proxy_port())
        except Exception:
            proxy_port = 8888
        target_port = parsed.port if parsed.port is not None else (
            443 if parsed.scheme == "https" else 80)

        local_addrs = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}
        try:
            hn = socket.gethostname()
            local_addrs.add(hn.lower())
            for info in socket.getaddrinfo(hn, None):
                local_addrs.add(str(info[4][0]).lower())
        except Exception:
            pass
        try:
            ph = str(get_config().get("proxy_host", "")).strip().lower()
            if ph:
                local_addrs.add(ph)
        except Exception:
            pass

        host_is_local = host in local_addrs
        if not host_is_local:
            try:
                for info in socket.getaddrinfo(host, None):
                    if str(info[4][0]).lower() in local_addrs:
                        host_is_local = True
                        break
            except Exception:
                pass

        return host_is_local and target_port == proxy_port
    except Exception:
        return False

def send_maintenance_notification(reason="scheduler"):
    """Setzt den konfigurierten HTTP-Call ab. Gibt (ok, message) zurück."""
    nc = get_maint_notify_config()
    url = nc.get("url", "").strip()
    if not url:
        return False, "Keine URL konfiguriert"
    if _is_self_url(url):
        msg = ("URL zeigt auf diese e2proxy-Instanz — abgebrochen "
               "(Endlosschleife/fd-Exhaustion verhindert)")
        log.warning(f"Maintenance-Notification abgebrochen ({url}): {msg}")
        return False, msg
    # Re-entrancy-Schutz: nie mehr als eine Notification gleichzeitig, damit ein
    # eventueller Loop sich nicht selbst verstärken kann.
    if not _maint_notify_lock.acquire(blocking=False):
        msg = "Bereits eine Notification aktiv — übersprungen"
        log.warning(f"Maintenance-Notification übersprungen ({url}): {msg}")
        return False, msg
    method = nc.get("method", "POST")
    try:
        if method == "POST":
            payload = json.dumps({
                "event": "maintenance",
                "reason": reason,
                "timestamp": datetime.now().isoformat(),
                "idle": is_system_idle(),
            }).encode("utf-8")
            req = urllib.request.Request(url, data=payload, method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "e2proxy"})
        else:
            req = urllib.request.Request(url, method="GET",
                                         headers={"User-Agent": "e2proxy"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.getcode()
        log.info(f"Maintenance-Notification gesendet ({method} {url}) → HTTP {status} [{reason}]")
        return True, f"HTTP {status}"
    except Exception as e:
        log.warning(f"Maintenance-Notification fehlgeschlagen ({method} {url}): {e}")
        return False, str(e)
    finally:
        _maint_notify_lock.release()

def maintenance_notify_loop():
    """Hintergrund-Thread: prüft jede Minute, ob ein Wartungs-Call fällig ist."""
    last_fired_slot = None   # (date, hour, minute) verhindert Doppel-Auslösung
    while True:
        try:
            nc = get_maint_notify_config()
            if nc.get("enabled") and nc.get("url"):
                now = datetime.now()
                slot = (now.date(), now.hour, now.minute)
                if (now.weekday() in nc.get("days", [])
                        and now.hour == nc.get("hour")
                        and now.minute == nc.get("minute")
                        and last_fired_slot != slot):
                    last_fired_slot = slot
                    if nc.get("idle_mode") == "idle_only" and not is_system_idle():
                        log.info("Maintenance-Notification übersprungen — System nicht im Leerlauf")
                    else:
                        send_maintenance_notification(reason="scheduler")
        except Exception as e:
            log.warning(f"Maintenance-Notify Scheduler Fehler: {e}")
        time.sleep(20)


# ── Receiver-Ausfall-Alarm ────────────────────────────────────────────────────
# Eine eingefrorene Box (Ping ok, HTTP hängt) blieb früher tagelang unbemerkt:
# der Circuit Breaker umging sie still, nur das Log füllte sich. Ist ein Receiver
# länger als `after_min` Minuten am Stück nicht erreichbar, wird EINMAL pro
# Ausfall alarmiert (Log-Event für Loki/Grafana + optionaler Webhook), und
# ebenso einmal, wenn er wieder antwortet.
RECEIVER_ALERT_DEFAULT = {
    "enabled": True,
    "after_min": 10,
    "url": "",
    "method": "POST",
}

_receiver_alerted = {}          # rid → fail_since des bereits gemeldeten Ausfalls
_receiver_alert_lock = threading.Lock()


def get_receiver_alert_config():
    cfg = get_config().get("receiver_alerts", {})
    merged = dict(RECEIVER_ALERT_DEFAULT)
    if isinstance(cfg, dict):
        merged.update(cfg)
    merged["enabled"] = bool(merged.get("enabled", True))
    try:
        merged["after_min"] = max(1, min(1440, int(merged.get("after_min", 10))))
    except (TypeError, ValueError):
        merged["after_min"] = 10
    merged["url"] = str(merged.get("url", "") or "").strip()
    merged["method"] = "GET" if str(merged.get("method", "POST")).upper() == "GET" else "POST"
    return merged


def send_receiver_alert(event, r, since_ts=0.0, test=False):
    """Schickt den Alarm-Webhook ab. event: 'receiver_down' | 'receiver_up'.

    Gibt (ok, message) zurück. Ohne konfigurierte URL wird nur geloggt."""
    ac = get_receiver_alert_config()
    url = ac["url"]
    if not url:
        return False, "Keine URL konfiguriert"
    if _is_self_url(url):
        msg = "URL zeigt auf diese e2proxy-Instanz — abgebrochen"
        log.warning(f"Receiver-Alarm abgebrochen ({url}): {msg}")
        return False, msg
    down_min = int((time.time() - since_ts) / 60) if since_ts else 0
    try:
        if ac["method"] == "POST":
            payload = json.dumps({
                "event": event,
                "test": test,
                "receiver": r.get("id", ""),
                "name": r.get("name", ""),
                "ip": r.get("ip", ""),
                "since": datetime.fromtimestamp(since_ts).isoformat() if since_ts else "",
                "down_min": down_min,
                "timestamp": datetime.now().isoformat(),
            }).encode("utf-8")
            req = urllib.request.Request(url, data=payload, method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "e2proxy"})
        else:
            q = urllib.parse.urlencode({"event": event, "receiver": r.get("id", ""),
                                        "down_min": down_min, "test": int(test)})
            req = urllib.request.Request(url + ("&" if "?" in url else "?") + q,
                                         method="GET", headers={"User-Agent": "e2proxy"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.getcode()
        log.info(f"Receiver-Alarm gesendet ({event} '{r.get('id')}') → HTTP {status}")
        return True, f"HTTP {status}"
    except Exception as e:
        log.warning(f"Receiver-Alarm fehlgeschlagen ({ac['method']} {url}): {e}")
        return False, str(e)


def check_receiver_alerts():
    """Vom Receiver-Watchdog aufgerufen: meldet lange Ausfälle und deren Ende."""
    ac = get_receiver_alert_config()
    threshold = ac["after_min"] * 60
    for r in get_receivers():
        rid = r["id"]
        if not r.get("enabled", True):
            continue
        with _receiver_health_lock:
            h = _health_entry(rid)
            since = h.get("fail_since") or 0.0
            failing = h["fails"] >= RECEIVER_FAIL_THRESHOLD and since > 0
        with _receiver_alert_lock:
            alerted_since = _receiver_alerted.get(rid)
            if failing and alerted_since != since and (time.time() - since) >= threshold:
                _receiver_alerted[rid] = since
                fire = "receiver_down"
            elif not failing and alerted_since is not None:
                _receiver_alerted.pop(rid, None)
                fire = "receiver_up"
            else:
                fire = None
        if fire == "receiver_down":
            log.warning(f"DIAG event=receiver_alert rid={rid} state=down ip={r.get('ip')} "
                        f"since=\"{datetime.fromtimestamp(since).strftime('%Y-%m-%d %H:%M:%S')}\" "
                        f"down_min={int((time.time() - since) / 60)}")
            if ac["enabled"]:
                send_receiver_alert("receiver_down", r, since)
        elif fire == "receiver_up":
            log.info(f"DIAG event=receiver_alert rid={rid} state=up ip={r.get('ip')}")
            if ac["enabled"]:
                send_receiver_alert("receiver_up", r, alerted_since or 0.0)


