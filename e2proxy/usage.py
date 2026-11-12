"""Usage statistics (who watches what, which interfaces are used)."""
import copy
import json
import os
import re
import threading
import time
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from e2proxy.paths import DATA_DIR
from e2proxy.logs import log
from e2proxy.switching import _norm_ref
from e2proxy.receivers import describe_client, human_bytes
from e2proxy.epg import epg_cache, epg_cache_lock

# ── Nutzungsstatistik (Usage / Auswertung) ────────────────
# Erfasst, WIE der e2proxy tatsächlich benutzt wird: welches Gerät streamt,
# welcher Sender wird geschaut, welche Sendungen wiederholt sich angesehen
# werden (→ Aufnahme-Vorschläge) und welche Schnittstellen wie oft laufen.
#
# Bewusst nur AGGREGIERTE Zähler (kein Roh-Eventlog): die Datei bleibt klein
# und es entsteht keine minutengenaue Verlaufsakte. Die Sehdauer dient
# zusätzlich als Exposure-Maß für die Umschalt-Analyse (mehr Nutzung → mehr
# absolute Fehler; erst die Normalisierung macht Sender vergleichbar).

USAGE_STATS_FILE = f"{DATA_DIR}/usage_stats.json"

_USAGE_MIN_SESSION_SEC = 5      # kürzere Zugriffe = Probe/Zapping, nicht "geschaut"
_USAGE_MAX_PROGRAMS    = 400    # Obergrenze Sendungs-Aggregat (älteste fliegen raus)

_usage_lock = threading.Lock()
_usage = None
_usage_dirty = [False]

def _usage_empty():
    return {
        "since": datetime.now().isoformat(timespec="seconds"),
        "channels": {}, "devices": {}, "interfaces": {},
        "endpoints": {}, "programs": {},
        "hours": {}, "weekday": {},
        "totals": {"sessions": 0, "seconds": 0, "bytes": 0},
    }

def _load_usage():
    global _usage
    if _usage is not None:
        return _usage
    try:
        if os.path.exists(USAGE_STATS_FILE):
            with open(USAGE_STATS_FILE, encoding="utf-8") as f:
                _usage = json.load(f)
            for k, v in _usage_empty().items():
                _usage.setdefault(k, v)
        else:
            _usage = _usage_empty()
    except Exception as e:
        log.warning(f"Usage-Stats laden fehlgeschlagen: {e}")
        _usage = _usage_empty()
    return _usage

def _save_usage():
    try:
        os.makedirs(os.path.dirname(USAGE_STATS_FILE), exist_ok=True)
        tmp = USAGE_STATS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_usage, f, ensure_ascii=False, indent=1)
        os.replace(tmp, USAGE_STATS_FILE)
        _usage_dirty[0] = False
    except Exception as e:
        log.warning(f"Usage-Stats speichern fehlgeschlagen: {e}")


def _xmltv_to_ts(digits, offset=None):
    """XMLTV-Zeitstempel ('20260729201500' + optional '+0000') → Unix-Zeit.
    e2proxy schreibt UTC — ohne Offset-Auswertung läge der Titel-Lookup
    um die lokale Zeitzone daneben."""
    dt = datetime.strptime(digits, "%Y%m%d%H%M%S")
    if offset:
        sign = 1 if offset[0] == "+" else -1
        delta = timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5]))
        return (dt.replace(tzinfo=timezone.utc) - sign * delta).timestamp()
    return dt.timestamp()


def epg_title_at(service_ref, ts):
    """Titel der Sendung, die zum Zeitpunkt `ts` auf diesem Sender lief.

    Liest den XMLTV-Cache (dieselbe Quelle wie EPG/Plex). Liefert "" wenn kein
    EPG vorliegt — die Auswertung funktioniert dann ohne Sendungsbezug weiter.
    """
    try:
        with epg_cache_lock:
            xml = epg_cache.get("xml")
        if not xml:
            return ""
        cid = _norm_ref(service_ref).replace(":", "_")
        # Nur den relevanten Ausschnitt scannen — XMLTV kann sehr groß sein.
        needle = f'channel="{cid}"'
        pos = 0
        best = ""
        while True:
            i = xml.find(needle, pos)
            if i < 0:
                break
            s = xml.rfind("<programme", 0, i)
            e = xml.find("</programme>", i)
            if s < 0 or e < 0:
                break
            pos = e
            block = xml[s:e]
            m_start = re.search(r'start="(\d{14})\s*([+-]\d{4})?', block)
            m_stop  = re.search(r'stop="(\d{14})\s*([+-]\d{4})?', block)
            if not (m_start and m_stop):
                continue
            try:
                t0 = _xmltv_to_ts(m_start.group(1), m_start.group(2))
                t1 = _xmltv_to_ts(m_stop.group(1), m_stop.group(2))
            except Exception:
                continue
            if t0 <= ts < t1:
                m_t = re.search(r"<title[^>]*>(.*?)</title>", block, re.S)
                if m_t:
                    best = re.sub(r"<[^>]+>", "", m_t.group(1)).strip()
                    best = (best.replace("&amp;", "&").replace("&lt;", "<")
                                .replace("&gt;", ">").replace("&quot;", '"'))
                break
        return best[:120]
    except Exception as e:
        log.debug(f"EPG-Titel-Lookup fehlgeschlagen: {e}")
        return ""


def _device_key(ua, client_ip):
    """Ein 'Gerät' = erkannte Software + IP. Genau genug, um Plex vom Handy zu
    trennen, ohne einzelne Requests fingerprinten zu müssen."""
    return f"{describe_client(ua)}@{client_ip or '?'}"


def record_usage_session(service_ref, channel_name, user_agent="", client_ip="",
                         profile="", source="stream", started_ts=None,
                         duration_sec=0, bytes_sent=0):
    """Eine abgeschlossene Stream-Sitzung in die Nutzungsstatistik einbuchen."""
    try:
        duration_sec = int(duration_sec or 0)
        if duration_sec < _USAGE_MIN_SESSION_SEC:
            return
        bytes_sent = int(bytes_sent or 0)
        started_ts = started_ts or time.time()
        ref = _norm_ref(service_ref)
        dt = datetime.fromtimestamp(started_ts)
        dev = _device_key(user_agent, client_ip)
        title = epg_title_at(ref, started_ts + duration_sec / 2)

        with _usage_lock:
            u = _load_usage()
            t = u["totals"]
            t["sessions"] += 1
            t["seconds"] += duration_sec
            t["bytes"]   += bytes_sent

            c = u["channels"].setdefault(ref, {"name": channel_name or ref[:40],
                                               "sessions": 0, "seconds": 0, "bytes": 0,
                                               "last": None})
            if channel_name:
                c["name"] = channel_name
            c["sessions"] += 1
            c["seconds"]  += duration_sec
            c["bytes"]    += bytes_sent
            c["last"] = dt.isoformat(timespec="seconds")

            d = u["devices"].setdefault(dev, {"label": describe_client(user_agent),
                                              "ip": client_ip or "", "ua": (user_agent or "")[:120],
                                              "sessions": 0, "seconds": 0, "bytes": 0,
                                              "last": None, "profiles": {}})
            d["sessions"] += 1
            d["seconds"]  += duration_sec
            d["bytes"]    += bytes_sent
            d["last"] = dt.isoformat(timespec="seconds")
            if profile:
                d["profiles"][profile] = d["profiles"].get(profile, 0) + 1

            i = u["interfaces"].setdefault(source, {"sessions": 0, "seconds": 0, "bytes": 0})
            i["sessions"] += 1
            i["seconds"]  += duration_sec
            i["bytes"]    += bytes_sent

            u["hours"][str(dt.hour)]      = u["hours"].get(str(dt.hour), 0) + duration_sec
            u["weekday"][str(dt.weekday())] = u["weekday"].get(str(dt.weekday()), 0) + duration_sec

            if title:
                pk = f"{ref}|{title}"
                p = u["programs"].setdefault(pk, {"ref": ref, "channel": channel_name or ref[:40],
                                                  "title": title, "count": 0, "seconds": 0,
                                                  "last": None, "slots": {}})
                p["count"]   += 1
                p["seconds"] += duration_sec
                p["last"] = dt.isoformat(timespec="seconds")
                slot = f"{dt.weekday()}-{dt.hour}"
                p["slots"][slot] = p["slots"].get(slot, 0) + 1
                if len(u["programs"]) > _USAGE_MAX_PROGRAMS:
                    oldest = sorted(u["programs"].items(),
                                    key=lambda kv: kv[1].get("last") or "")[:50]
                    for k, _ in oldest:
                        u["programs"].pop(k, None)
            _save_usage()
        log.debug(f"Usage: {channel_name} {duration_sec}s via {source} ({dev})")
    except Exception as e:
        log.debug(f"Usage-Erfassung fehlgeschlagen: {e}")


def note_endpoint(category):
    """Schnittstellen-Nutzung zählen (billig, im Speicher; Flush periodisch)."""
    if not category:
        return
    try:
        with _usage_lock:
            u = _load_usage()
            u["endpoints"][category] = u["endpoints"].get(category, 0) + 1
            _usage_dirty[0] = True
    except Exception:
        pass


def flush_usage_stats():
    """Gepufferte Endpoint-Zähler auf Platte schreiben (periodisch/beim Beenden)."""
    with _usage_lock:
        if _usage_dirty[0] and _usage is not None:
            _save_usage()


def usage_flush_loop(interval=60):
    """Schreibt die im Speicher gezählten Endpoint-Aufrufe periodisch weg.
    Einzelne Requests sollen keine Datei-I/O auslösen."""
    while True:
        time.sleep(interval)
        try:
            flush_usage_stats()
        except Exception as e:
            log.debug(f"Usage-Flush fehlgeschlagen: {e}")


def classify_endpoint(path, source="proxy"):
    """Ordnet einen Request-Pfad einer Schnittstellen-Kategorie zu."""
    p = (path or "").split("?")[0].lower().rstrip("/")
    if source == "openwebif":
        if "/picon" in p or p.endswith((".png", ".jpg", ".jpeg", ".gif", ".ico")):
            return None                      # Bild-Rauschen nicht zählen
        if "epg" in p:
            return "OpenWebif: EPG"
        if "getservices" in p or "bouquet" in p or "getallservices" in p:
            return "OpenWebif: Senderliste"
        if "movielist" in p:
            return "OpenWebif: Aufnahmen"
        if p.startswith("/1:") or p.startswith("/live"):
            return "OpenWebif: Streaming"
        return "OpenWebif: sonstige"
    if p.startswith("/stream"):
        return "Streaming (/stream)"
    if p in ("/discover.json", "/lineup.json", "/lineup_status.json",
             "/lineup_post", "/device.xml", "/capability", "/auto"):
        return "Plex/HDHomeRun"
    if p.startswith("/playlist") or p.endswith(".m3u"):
        return "M3U-Playlist"
    if p.startswith("/epg.xml") or p.startswith("/xmltv"):
        return "EPG (XMLTV)"
    if p.startswith("/api/"):
        return "REST-API"
    if p.startswith("/picon") or p.startswith("/logos") or p.startswith("/logo") \
            or p.endswith((".png", ".jpg", ".ico", ".css", ".js")):
        return None
    if p in ("", "/settings", "/favorites", "/epg", "/help", "/usage"):
        return "Web-UI"
    return None


def get_usage_snapshot():
    with _usage_lock:
        return copy.deepcopy(_load_usage())


def reset_usage_stats():
    global _usage
    with _usage_lock:
        _usage = _usage_empty()
        _save_usage()


def _fmt_duration(sec):
    sec = int(sec or 0)
    h, m = divmod(sec // 60, 60)
    if h >= 24:
        return f"{h // 24}d {h % 24}h"
    return f"{h}h {m:02d}m" if h else f"{m} Min"


_WEEKDAYS_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

def build_usage_report(top=10):
    """Wertet die Rohzähler zu den vier Kernfragen aus."""
    u = get_usage_snapshot()
    tot = u.get("totals", {})

    channels = sorted(
        ({"ref": r, **v} for r, v in u.get("channels", {}).items()),
        key=lambda c: c.get("seconds", 0), reverse=True)
    devices = sorted(
        ({"key": k, **v} for k, v in u.get("devices", {}).items()),
        key=lambda d: d.get("seconds", 0), reverse=True)
    interfaces = sorted(
        ({"name": k, **v} for k, v in u.get("interfaces", {}).items()),
        key=lambda i: i.get("seconds", 0), reverse=True)
    endpoints = sorted(
        ({"name": k, "count": v} for k, v in u.get("endpoints", {}).items()),
        key=lambda e: e["count"], reverse=True)

    total_sec = max(1, tot.get("seconds", 0))
    for c in channels:
        c["share"]        = round(100.0 * c.get("seconds", 0) / total_sec, 1)
        c["duration_human"] = _fmt_duration(c.get("seconds", 0))
    for d in devices:
        d["share"]        = round(100.0 * d.get("seconds", 0) / total_sec, 1)
        d["duration_human"] = _fmt_duration(d.get("seconds", 0))
        d["top_profile"]  = max(d.get("profiles", {}).items(),
                                key=lambda kv: kv[1])[0] if d.get("profiles") else ""
    for i in interfaces:
        i["duration_human"] = _fmt_duration(i.get("seconds", 0))

    # Aufnahme-Vorschläge: mehrfach geschaute Sendungen, gewichtet nach
    # Häufigkeit UND Sehdauer; zusätzlich der typische Sendeplatz.
    suggestions = []
    for p in u.get("programs", {}).values():
        if p.get("count", 0) < 2:
            continue
        slots = p.get("slots", {})
        slot_txt = ""
        if slots:
            best_slot, hits = max(slots.items(), key=lambda kv: kv[1])
            try:
                wd, hr = best_slot.split("-")
                slot_txt = f"{_WEEKDAYS_DE[int(wd)]} ~{int(hr):02d}:00"
                if hits >= 2:
                    slot_txt += f" ({hits}×)"
            except Exception:
                slot_txt = ""
        suggestions.append({
            "title": p.get("title", ""), "channel": p.get("channel", ""),
            "ref": p.get("ref", ""), "count": p.get("count", 0),
            "seconds": p.get("seconds", 0),
            "duration_human": _fmt_duration(p.get("seconds", 0)),
            "slot": slot_txt, "last": p.get("last"),
            "score": round(p.get("count", 0) * (p.get("seconds", 0) / 3600.0), 2),
        })
    suggestions.sort(key=lambda s: s["score"], reverse=True)

    return {
        "since": u.get("since"),
        "totals": {
            "sessions": tot.get("sessions", 0),
            "seconds":  tot.get("seconds", 0),
            "bytes":    tot.get("bytes", 0),
            "duration_human": _fmt_duration(tot.get("seconds", 0)),
            "bytes_human":    human_bytes(tot.get("bytes", 0)),
        },
        "channels":    channels[:top],
        "devices":     devices[:top],
        "interfaces":  interfaces,
        "endpoints":   endpoints[:top],
        "suggestions": suggestions[:top],
        "hours":       u.get("hours", {}),
        "weekday":     u.get("weekday", {}),
    }


