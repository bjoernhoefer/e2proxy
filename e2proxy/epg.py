"""EPG: multi-source fetch, merged XMLTV, disk cache and daily scheduler."""
import gzip
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

from e2proxy.paths import DATA_DIR
from e2proxy.logs import log
from e2proxy.config import get_config, get_receivers, is_receiver_usable
from e2proxy.favorites import load_favorites
from e2proxy.channels import BOUQUETS, channel_cache, channel_cache_lock
from e2proxy.logos import get_channel_logo_urls, get_logo_for_epg
from e2proxy.receivers import is_receiver_unhealthy
from e2proxy.metadata import (
    _load_tmdb_cache,
    _save_tmdb_cache,
    _tmdb_cache_fresh,
    _tmdb_cache_lock,
    tmdb_get_poster,
)
from e2proxy.plex import _plex_dvr_refresh_guide
from e2proxy import receivers as receivers_mod

# ── EPG Cache ─────────────────────────────────────────────
EPG_CACHE_FILE = f"{DATA_DIR}/epg_cache.xml"
# DVB Genre-IDs für Kodi (ETSI EN 300 468)
DVB_GENRE_MAP = {
    "series":       "0x30",   # Show/Game Show
    "movie":        "0x10",   # Film/Drama
    "news":         "0x20",   # Nachrichten
    "sports":       "0x40",   # Sport
    "kids":         "0x50",   # Kinder
    "talk":         "0x30",   # Show
    "reality":      "0x30",   # Show
    "documentary":  "0x90",   # Bildung/Wissenschaft
    "music":        "0x60",   # Musik
}

EPG_RUNS_FILE  = f"{DATA_DIR}/epg_runs.json"

epg_cache = {"xml": None, "last_update": 0}
epg_cache_lock = threading.Lock()

def save_epg_to_disk(xml_str):
    """Speichert EPG XML auf Disk für Persistenz über Neustarts."""
    try:
        os.makedirs(os.path.dirname(EPG_CACHE_FILE), exist_ok=True)
        with open(EPG_CACHE_FILE, "w", encoding="utf-8") as f:
            f.write(xml_str)
        log.debug(f"EPG auf Disk gespeichert: {len(xml_str)//1024} KB")
    except Exception as e:
        log.warning(f"EPG Disk-Speicherung fehlgeschlagen: {e}")

def load_epg_from_disk():
    """Lädt EPG XML vom Disk-Cache falls vorhanden."""
    try:
        if os.path.exists(EPG_CACHE_FILE):
            age = time.time() - os.path.getmtime(EPG_CACHE_FILE)
            if age < 86400:  # max 24h alt
                with open(EPG_CACHE_FILE, "r", encoding="utf-8") as f:
                    xml = f.read()
                if xml:
                    log.info(f"EPG loaded from disk: {len(xml)//1024} KB (age: {int(age/3600)}h)")
                    return xml, os.path.getmtime(EPG_CACHE_FILE)
    except Exception as e:
        log.warning(f"EPG Disk-Laden fehlgeschlagen: {e}")
    return None, 0


_EPG_CHANNEL_RE = re.compile(
    r'  <channel id="([^"]*)">\n'
    r'    <display-name>(.*?)</display-name>\n'
    r'(?:    <icon src="[^"]*"/>\n)?'
    r'  </channel>')


def apply_custom_logos_to_epg_cache():
    """Aktualisiert die <icon>-Einträge im bereits gecachten EPG-XML anhand der
    aktuellen Logo-Auflösung (Custom-Logo > lokaler Cache > URL).

    Das EPG-XML wird nur bei einem vollständigen EPG-Run neu gebaut und danach
    24 h gecached. Ohne diese Funktion würden neu hinterlegte Custom-Logos erst
    beim nächsten Run im Plex-Guide erscheinen. Der Patch ist günstig (Regex auf
    den Kanalköpfen) und lässt die Event-Daten unangetastet."""
    with epg_cache_lock:
        xml = epg_cache["xml"]
    if not xml:
        return False

    def _unescape(s):
        return s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")

    def _repl(m):
        cid, name_esc = m.group(1), m.group(2)
        logo = get_logo_for_epg(_unescape(name_esc))
        block = f'  <channel id="{cid}">\n    <display-name>{name_esc}</display-name>\n'
        if logo:
            block += f'    <icon src="{logo}"/>\n'
        block += '  </channel>'
        return block

    new_xml, n = _EPG_CHANNEL_RE.subn(_repl, xml)
    if new_xml != xml:
        with epg_cache_lock:
            epg_cache["xml"] = new_xml
        save_epg_to_disk(new_xml)
        log.info(f"EPG-Logos im Cache aktualisiert ({n} Kanäle gepatcht)")
    return True


def fetch_epg_from_receiver(favorites_only=False):
    """Holt EPG von allen Bouquets vom Receiver und konvertiert nach XMLTV."""
    receivers = get_receivers()
    if not receivers:
        return None
    r = receivers[0]

    # Welche Service-Refs brauchen wir?
    favs = load_favorites()
    fav_refs = {f["ref"] for f in favs}
    
    # Alle Channels aus Cache für vollständiges EPG
    with channel_cache_lock:
        all_channels = channel_cache.get("channels", [])
    all_refs = {ch["ref"] for ch in all_channels}
    
    if favorites_only:
        target_refs = fav_refs
    else:
        target_refs = all_refs

    if not target_refs:
        return None

    # EPG von allen Bouquets holen
    all_events = []
    seen_ids = set()

    for bouquet in BOUQUETS:
        try:
            bref = f'1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "{bouquet}" ORDER BY bouquet'
            encoded = urllib.parse.quote(bref)
            url = f"http://{r['ip']}:{r['port']}/api/epgmulti?bRef={encoded}"
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            for ev in data.get("events", []):
                ref = ev["sref"].rstrip("/")
                if ev["id"] not in seen_ids and ref in target_refs:
                    seen_ids.add(ev["id"])
                    all_events.append(ev)
        except Exception as e:
            log.warning(f"EPG Bouquet {bouquet} Fehler: {e}")

    log.info(f"EPG: {len(all_events)} events loaded")

    # Nach XMLTV konvertieren
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append('<tv generator-info-name="e2proxy" generator-info-url="http://github.com">')

    # Alle bekannten Kanäle als <channel> eintragen — auch ohne EPG-Events.
    # Plex matched über channel id (= service ref mit Unterstrichen).
    # display-name muss exakt mit GuideName aus lineup.json übereinstimmen.
    # Sender ohne Events: Dummy-Eintrag damit sie im Plex-Guide erscheinen.
    channel_ref_to_name = {ch["ref"].rstrip("/"): ch["name"] for ch in all_channels}
    for ref_raw, ch_name in channel_ref_to_name.items():
        safe_id = ref_raw.replace(":", "_")
        safe_name = ch_name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f'  <channel id="{safe_id}">')
        lines.append(f'    <display-name>{safe_name}</display-name>')
        logo_url = get_logo_for_epg(ch_name)
        if logo_url:
            lines.append(f'    <icon src="{logo_url}"/>')
        lines.append('  </channel>')

    # Events nach channel-ref gruppieren um Sender ohne Events zu erkennen
    seen_channels = {}
    for ev in all_events:
        sref = ev["sref"].rstrip("/")
        seen_channels.setdefault(sref, []).append(ev)

    # Sender ohne EPG-Events bekommen keinen Dummy-Eintrag.
    # Plex zeigt sie trotzdem im Guide — einfach ohne Programminformation.

    # Programmes
    for ev in all_events:
        try:
            sref = ev["sref"].rstrip("/")
            safe_id = sref.replace(":", "_")
            start_ts = int(ev["begin_timestamp"])
            stop_ts = start_ts + int(ev["duration_sec"])

            def ts_to_xmltv(ts):
                from datetime import timezone
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                return dt.strftime("%Y%m%d%H%M%S +0000")

            start = ts_to_xmltv(start_ts)
            stop = ts_to_xmltv(stop_ts)

            title = ev.get("title", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            shortdesc = ev.get("shortdesc", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            longdesc = ev.get("longdesc", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

            lines.append(f'  <programme start="{start}" stop="{stop}" channel="{safe_id}">')
            lines.append(f'    <title lang="de">{title}</title>')
            if shortdesc:
                lines.append(f'    <sub-title lang="de">{shortdesc}</sub-title>')
            if longdesc:
                lines.append(f'    <desc lang="de">{longdesc}</desc>')
            lines.append('  </programme>')
        except Exception as e:
            log.debug(f"EPG Event Fehler: {e}")

    lines.append('</tv>')
    return "\n".join(lines)



# ── EPG Multi-Source System ───────────────────────────────
# Holt EPG aus mehreren Quellen und führt sie zusammen:
#  1. Beide Receiver (EIT/OTA via api/epgmulti)
#  2. Zap-Reload für Sender ohne EPG (auf Sender schalten, waitingn, neu lesen)
#  3. Rytec XMLTV Online-Quelle als Fallback
# Aktuellste/vollständigste Daten gewinnen beim Merge.


# Online-EPG Fallback URL — muss manuell konfiguriert werden.
# Optionen: tvprofil.net (per-Sender), globetvapp/epg (GitHub)
# Leer lassen um Online-Fallback zu deaktivieren.
RYTEC_URL = ""  # deenabled bis verifizierte URL bekannt ist

# Mapping: Favoriten-Sendername → Rytec Channel-ID
# Wird für Online-Fallback verwendet wenn Receiver kein EPG liefert.
RYTEC_CHANNEL_MAP = {
    "Das Erste HD": "ARD.de",
    "ZDF HD": "ZDF.de",
    "RTL HD": "RTL.de",
    "SAT.1 HD": "Sat1.de",
    "ProSieben HD": "Pro7.de",
    "VOX HD": "VOX.de",
    "kabel eins HD": "Kabel1.de",
    "RTLZWEI HD": "RTL2.de",
    "NITRO": "RTLNitro.de",
    "RTLup HD": "RTLplus.de",
    "VOXup HD": "VOXup.de",
    "Pro7 MAXX HD": "Pro7Maxx.de",
    "SUPER RTL HD": "SuperRTL.de",
    "ServusTV HD Oesterreich": "ServusTV.at",
    "WELT HD": "WELT.de",
    "DMAX HD": "DMAX.de",
    "ATV HD": "ATV.at",
    "ATV II HD": "ATV2.at",
    "TELE 5 HD": "Tele5.de",
}

# EPG-Run State für UI-Progress
epg_run_state = {
    "running": False,
    "phase": "",
    "progress": 0,        # 0-100
    "total": 0,
    "done": 0,
    "log": [],            # Liste von Strings
    "last_run": None,     # ISO timestamp
    "last_duration": 0,   # Sekunden
    "last_result": "",    # Zusammenfassung
    "tmdb_total": 0,      # Gesamt TMDB-Lookups
    "tmdb_done": 0,       # Abgeschlossene Lookups
    "tmdb_items": [],     # Letzte 20 Items für Live-Anzeige
    "tmdb_all": [],       # Alle Items für Abschluss-Zusammenfassung
}
epg_run_lock = threading.Lock()

# EPG Scheduler Konfiguration (Default: 3:00 Uhr)
def get_epg_schedule_hour():
    cfg = get_config()
    return int(cfg.get("epg_schedule_hour", 3))

def _epg_log(msg):
    """Fügt eine Log-Zeile zum EPG-Run State hinzu."""
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"{ts} {msg}"
    with epg_run_lock:
        epg_run_state["log"].append(line)
        if len(epg_run_state["log"]) > 200:
            epg_run_state["log"] = epg_run_state["log"][-200:]
    log.info(f"EPG run: {msg}")


def fetch_epg_events_from_receiver(receiver, target_refs):
    """Holt EPG-Events von EINEM bestimmten Receiver.
    Gibt dict zurück: {ref: [events]}
    """
    events_by_ref = {}
    seen_ids = set()
    for bouquet in BOUQUETS:
        try:
            bref = f'1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "{bouquet}" ORDER BY bouquet'
            encoded = urllib.parse.quote(bref)
            url = f"http://{receiver['ip']}:{receiver['port']}/api/epgmulti?bRef={encoded}"
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            for ev in data.get("events", []):
                ref = ev["sref"].rstrip("/")
                uid = (ref, ev.get("begin_timestamp"))
                if uid not in seen_ids and ref in target_refs:
                    seen_ids.add(uid)
                    events_by_ref.setdefault(ref, []).append(ev)
        except Exception as e:
            _epg_log(f"Receiver {receiver['name']} Bouquet {bouquet}: Fehler {e}")
    return events_by_ref


def zap_and_fetch_epg(receiver, service_ref, wait_sec=30):
    """Schaltet Receiver auf Sender, waitingt, holt dann EPG für diesen Sender.
    Nur wenn Receiver frei ist (kein aktiver Stream).
    """
    rid = receiver["id"]
    # Nur zappen wenn Receiver frei
    if receivers_mod._receiver_state.get(rid) is not None:
        _epg_log(f"Receiver {receiver['name']} belegt — überspringe Zap")
        return []
    try:
        # Zap via OpenWebif
        zap_ref = urllib.parse.quote(service_ref)
        zap_url = f"http://{receiver['ip']}:{receiver['port']}/api/zap?sRef={zap_ref}"
        with urllib.request.urlopen(zap_url, timeout=10) as resp:
            resp.read()
        _epg_log(f"Zap to {service_ref[:30]}… — waiting {wait_sec}s")
        time.sleep(wait_sec)
        # EPG für diesen Sender holen
        ref_enc = urllib.parse.quote(service_ref)
        epg_url = f"http://{receiver['ip']}:{receiver['port']}/api/epgservice?sRef={ref_enc}"
        with urllib.request.urlopen(epg_url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return data.get("events", [])
    except Exception as e:
        _epg_log(f"Zap-Fetch Fehler für {service_ref[:30]}: {e}")
        return []


def fetch_rytec_epg():
    """Lädt Online-XMLTV EPG herunter. Gibt dict zurück: {channel_id: [programme_dicts]}
    Deenabled wenn RYTEC_URL leer ist.
    """
    if not RYTEC_URL:
        _epg_log("Online-Fallback deenabled (RYTEC_URL nicht konfiguriert)")
        return {}
    try:
        req = urllib.request.Request(RYTEC_URL, headers={"User-Agent": "e2proxy/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
        xml_data = gzip.decompress(raw).decode("utf-8", errors="replace")
        # Simples Parsing der <programme> Einträge
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_data)
        progs = {}
        for prog in root.findall("programme"):
            chan = prog.get("channel")
            progs.setdefault(chan, []).append(prog)
        _epg_log(f"Rytec geladen: {len(progs)} Sender")
        return progs
    except Exception as e:
        _epg_log(f"Rytec Fehler: {e}")
        return {}


def run_epg_update(triggered_by="manual"):
    """Haupt-EPG run: Multi-Source Merge mit Zap-Reload und Rytec-Fallback.
    Läuft als Hintergrund-Thread. Aktualisiert epg_run_state für UI.
    """
    with epg_run_lock:
        if epg_run_state["running"]:
            return False
        epg_run_state["running"] = True
        epg_run_state["progress"] = 0
        epg_run_state["done"] = 0
        epg_run_state["log"] = []
        epg_run_state["phase"] = "Initialisierung"

    start_time = time.time()
    _epg_log(f"EPG-Run started (Trigger: {triggered_by})")

    try:
        favs = load_favorites()
        fav_refs = {f["ref"] for f in favs}
        with channel_cache_lock:
            all_channels = channel_cache.get("channels", [])
        ref_to_name = {ch["ref"].rstrip("/"): ch["name"] for ch in all_channels}
        target_refs = {r.rstrip("/") for r in fav_refs}

        receivers = [r for r in get_receivers() if is_receiver_usable(r)]
        if not receivers:
            _epg_log("Keine Receiver verfügbar")
            return False
        # Ausgefallene Receiver überspringen (sonst läuft jedes Bouquet in den Timeout)
        healthy = [r for r in receivers if not is_receiver_unhealthy(r["id"])]
        for r in receivers:
            if r not in healthy:
                _epg_log(f"Receiver {r['name']} nicht erreichbar — übersprungen")
        if healthy:
            receivers = healthy

        with epg_run_lock:
            epg_run_state["total"] = len(target_refs)

        # ── Phase 1: EPG von allen Receivern holen ──────────
        with epg_run_lock:
            epg_run_state["phase"] = "Receiver-EPG abrufen"
        merged = {}  # ref → [events]
        for rx in receivers:
            _epg_log(f"Fetching EPG from receiver {rx['name']}")
            ev_by_ref = fetch_epg_events_from_receiver(rx, target_refs)
            for ref, evs in ev_by_ref.items():
                # Merge: mehr Events gewinnen
                if ref not in merged or len(evs) > len(merged[ref]):
                    merged[ref] = evs
        with epg_run_lock:
            epg_run_state["progress"] = 10

        # ── Phase 2: Fehlende Sender per Zap nachladen ──────
        with epg_run_lock:
            epg_run_state["phase"] = "Fehlende Sender nachladen (Zap)"
        missing = [r for r in target_refs if r not in merged or not merged[r]]
        _epg_log(f"{len(missing)} channels without EPG — trying zap reload")

        # Auf beide Receiver verteilen
        for idx, ref in enumerate(missing):
            rx = receivers[idx % len(receivers)]
            name = ref_to_name.get(ref, ref[:20])
            _epg_log(f"Reload {idx+1}/{len(missing)}: {name}")
            evs = zap_and_fetch_epg(rx, ref, wait_sec=30)
            if evs:
                merged[ref] = evs
                _epg_log(f"  → {len(evs)} events reloaded")
            with epg_run_lock:
                epg_run_state["done"] = idx + 1
                epg_run_state["progress"] = 10 + int(10 * (idx + 1) / max(len(missing), 1))

        # ── Phase 3: Rytec Online-Fallback ──────────────────
        with epg_run_lock:
            epg_run_state["phase"] = "Online-Quelle (Rytec)"
        still_missing = [r for r in target_refs if r not in merged or not merged[r]]
        rytec_progs = {}
        if still_missing:
            _epg_log(f"{len(still_missing)} Sender noch ohne EPG — lade Rytec")
            rytec_progs = fetch_rytec_epg()
        with epg_run_lock:
            epg_run_state["progress"] = 20

        # Kein einziges Event (Box aus, Netz weg): vorhandenes EPG behalten statt
        # es durch ein leeres XMLTV zu ersetzen — sonst ist der Guide bis zum
        # nächsten erfolgreichen Lauf leer (Startup-Run mit ausgeschalteter Box).
        if not any(merged.values()) and not rytec_progs:
            with epg_cache_lock:
                have_old = "<programme" in (epg_cache.get("xml") or "")
            if have_old:
                _epg_log("No EPG data received — keeping previous EPG")
                with epg_run_lock:
                    epg_run_state["phase"] = "Abgeschlossen"
                    epg_run_state["last_result"] = "no data — previous EPG kept"
                return False

        # ── Phase 4: XMLTV zusammenbauen ────────────────────
        with epg_run_lock:
            epg_run_state["phase"] = "XMLTV erzeugen"
        # TMDB nur bei manuellen/Scheduler-Runs, nicht beim Startup
        fetch_tmdb = triggered_by != "startup"
        xml = build_merged_xmltv(all_channels, merged, rytec_progs, ref_to_name, fetch_tmdb=fetch_tmdb)

        with epg_cache_lock:
            epg_cache["xml"] = xml
            epg_cache["last_update"] = time.time()
            save_epg_to_disk(xml)

        with_epg = len([r for r in target_refs if r in merged and merged[r]])
        result = f"{with_epg}/{len(target_refs)} channels with EPG, {len(xml)//1024} KB"
        _epg_log(f"Finished: {result}")
        # TMDB Cache sichern
        with _tmdb_cache_lock:
            _save_tmdb_cache()

        with epg_run_lock:
            epg_run_state["progress"] = 100
            epg_run_state["phase"] = "Abgeschlossen"
            epg_run_state["last_result"] = result

        # Plex DVR-Guide neu laden, damit die frischen EPG-Daten übernommen werden.
        # Nicht beim Startup-Run (Plex kann/soll da noch nicht angestoßen werden).
        if triggered_by != "startup":
            try:
                from e2proxy import recordings  # lazy: recordings depends on epg
                rcfg = recordings.get_recordings_config()
                if rcfg.get("plex_dvr_refresh") and rcfg.get("plex_url") and rcfg.get("plex_token"):
                    threading.Thread(target=_plex_dvr_refresh_guide, args=(rcfg,),
                                     daemon=True).start()
            except Exception as e:
                log.warning(f"Plex DVR-Guide-Refresh nach EPG-Run fehlgeschlagen: {e}")

        return True

    except Exception as e:
        _epg_log(f"FEHLER: {e}")
        return False
    finally:
        duration = int(time.time() - start_time)
        with epg_run_lock:
            epg_run_state["running"] = False
            epg_run_state["last_run"] = datetime.now().isoformat()
            epg_run_state["last_duration"] = duration
        # Laufzeit-Log schreiben
        try:
            runs = []
            if os.path.exists(EPG_RUNS_FILE):
                with open(EPG_RUNS_FILE) as _f:
                    runs = json.load(_f)
            runs.append({
                "ts": datetime.now().isoformat(),
                "trigger": triggered_by,
                "duration_sec": duration,
                "result": epg_run_state.get("last_result", ""),
            })
            runs = runs[-90:]  # max 90 Einträge behalten
            os.makedirs(os.path.dirname(EPG_RUNS_FILE), exist_ok=True)
            with open(EPG_RUNS_FILE, "w") as _f:
                json.dump(runs, _f)
        except Exception as _e:
            log.warning(f"EPG Runs Log Fehler: {_e}")


def build_merged_xmltv(all_channels, merged_events, rytec_progs, ref_to_name, fetch_tmdb=True):
    """Baut finales XMLTV aus Receiver-Events + Rytec-Fallback."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append('<tv generator-info-name="e2proxy" generator-info-url="http://github.com">')

    def esc(s):
        # Entferne invalide XML-Zeichen (inkl. Char 26 = \x1a und andere Steuerzeichen)
        s = ''.join(c for c in (s or '') if c in '\t\n\r' or (0x20 <= ord(c) <= 0xD7FF) or (0xE000 <= ord(c) <= 0xFFFD))
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def ts_to_xmltv(ts):
        from datetime import timezone
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y%m%d%H%M%S +0000")

    # Channels
    channel_ref_to_name = {ch["ref"].rstrip("/"): ch["name"] for ch in all_channels}
    for ref_raw, ch_name in channel_ref_to_name.items():
        safe_id = ref_raw.replace(":", "_")
        lines.append(f'  <channel id="{safe_id}">')
        lines.append(f'    <display-name>{esc(ch_name)}</display-name>')
        logo_url = get_logo_for_epg(ch_name)
        if logo_url:
            lines.append(f'    <icon src="{logo_url}"/>')
        lines.append('  </channel>')

    # Kategorien aus Favoriten laden (ref → category)
    ref_to_category = {}
    try:
        for fav in load_favorites():
            ref_to_category[fav["ref"].rstrip("/")] = fav.get("category", "")
    except Exception:
        pass

    # TMDB: Anzahl eligible Sendungen vorab zählen für Progress (nur wenn fetch_tmdb)
    now_ts = time.time()
    window_end = now_ts + 28 * 3600
    tmdb_eligible = []
    for ref, events in merged_events.items():
        for ev in events:
            try:
                start_ts = int(ev["begin_timestamp"])
                stop_ts = start_ts + int(ev["duration_sec"])
                duration_min = (stop_ts - start_ts) // 60
                title = ev.get("title", "")
                if title and duration_min >= 20 and start_ts <= window_end:
                    cache = _load_tmdb_cache()
                    cat = ref_to_category.get(ref.rstrip("/"), "")
                    prefix = "s" if cat in ("series", "talk", "reality", "kids") else "m"
                    cache_key = f"{prefix}:{title.lower().strip()}"
                    entry = cache.get(cache_key)
                    if not (entry and _tmdb_cache_fresh(entry)):
                        tmdb_eligible.append(title)
            except Exception:
                pass
    tmdb_total = len(tmdb_eligible)
    with epg_run_lock:
        epg_run_state["tmdb_total"] = tmdb_total if fetch_tmdb else 0
        epg_run_state["tmdb_done"] = 0
        epg_run_state["tmdb_items"] = []
        epg_run_state["tmdb_all"] = []
        epg_run_state["phase"] = f"TMDB Artwork (0/{tmdb_total})" if fetch_tmdb else "XMLTV erzeugen"

    # Programmes aus Receiver-Events
    for ref, events in merged_events.items():
        safe_id = ref.replace(":", "_")
        cat = ref_to_category.get(ref, "")
        dvb_genre = DVB_GENRE_MAP.get(cat, "")
        for ev in events:
            try:
                start_ts = int(ev["begin_timestamp"])
                stop_ts = start_ts + int(ev["duration_sec"])
                duration_min = (stop_ts - start_ts) // 60
                title = esc(ev.get("title", ""))
                raw_title = ev.get("title", "")
                shortdesc = esc(ev.get("shortdesc", ""))
                longdesc = esc(ev.get("longdesc", ""))
                lines.append(f'  <programme start="{ts_to_xmltv(start_ts)}" stop="{ts_to_xmltv(stop_ts)}" channel="{safe_id}">')
                lines.append(f'    <title lang="de">{title}</title>')
                if shortdesc:
                    lines.append(f'    <sub-title lang="de">{shortdesc}</sub-title>')
                if longdesc:
                    lines.append(f'    <desc lang="de">{longdesc}</desc>')
                if cat:
                    if dvb_genre:
                        lines.append(f'    <category genreId="{dvb_genre}" lang="en">{esc(cat)}</category>')
                    else:
                        lines.append(f'    <category lang="en">{esc(cat)}</category>')
                # TMDB Poster für längere Sendungen (>= 20 Min) im 28h-Fenster
                if fetch_tmdb and raw_title and duration_min >= 20 and start_ts <= window_end:
                    is_series = cat in ("series", "talk", "reality", "kids")
                    # Status: pending → in Liste eintragen
                    with epg_run_lock:
                        items = epg_run_state["tmdb_items"]
                        items.append({"title": raw_title, "status": "pending"})
                        if len(items) > 20:
                            items.pop(0)
                    poster = tmdb_get_poster(raw_title, is_series=is_series)
                    # Status aktualisieren
                    with epg_run_lock:
                        done = epg_run_state["tmdb_done"] + 1
                        epg_run_state["tmdb_done"] = done
                        # Letztes pending → found/not_found
                        status = "found" if (poster and not poster.startswith("/logos")) else ("logo" if (poster and poster.startswith("/logos")) else "not_found")
                        for item in reversed(epg_run_state["tmdb_items"]):
                            if item["title"] == raw_title and item["status"] == "pending":
                                item["status"] = status
                                break
                        # Alle Items für Zusammenfassung
                        epg_run_state["tmdb_all"].append({"title": raw_title, "status": status})
                        # Progress: 20% Receiver + bis zu 80% TMDB
                        if tmdb_total > 0:
                            tmdb_pct = min(80, int(done / tmdb_total * 80))
                            epg_run_state["progress"] = 20 + tmdb_pct
                        epg_run_state["phase"] = f"TMDB Artwork ({done}/{tmdb_total})"
                    if not poster:
                        # Fallback: Sender-Logo verwenden
                        ch_name = channel_ref_to_name.get(ref, "")
                        poster = get_logo_for_epg(ch_name) if ch_name else ""
                    if poster:
                        lines.append(f'    <icon src="{poster}"/>')
                lines.append('  </programme>')
            except Exception:
                pass

    # Rytec-Fallback für Sender ohne Receiver-Events
    if rytec_progs:
        import xml.etree.ElementTree as ET
        for ref_raw, ch_name in channel_ref_to_name.items():
            if ref_raw in merged_events and merged_events[ref_raw]:
                continue  # hat schon EPG
            rytec_id = RYTEC_CHANNEL_MAP.get(ch_name)
            if not rytec_id or rytec_id not in rytec_progs:
                continue
            safe_id = ref_raw.replace(":", "_")
            for prog in rytec_progs[rytec_id]:
                start = prog.get("start", "")
                stop = prog.get("stop", "")
                title_el = prog.find("title")
                title = esc(title_el.text) if title_el is not None and title_el.text else "Programm"
                lines.append(f'  <programme start="{start}" stop="{stop}" channel="{safe_id}">')
                lines.append(f'    <title lang="de">{title}</title>')
                desc_el = prog.find("desc")
                if desc_el is not None and desc_el.text:
                    lines.append(f'    <desc lang="de">{esc(desc_el.text)}</desc>')
                lines.append('  </programme>')

    lines.append('</tv>')
    return "\n".join(lines)


def check_favorite_logos():
    """Prüft ob die Logo-URLs der Favoriten erreichbar sind.
    Gibt Liste der Sender mit fehlenden/kaputten Logos zurück.
    """
    favs = load_favorites()
    with channel_cache_lock:
        all_channels = channel_cache.get("channels", [])
    ref_to_name = {ch["ref"]: ch["name"] for ch in all_channels}
    broken = []
    for f in favs:
        name = ref_to_name.get(f["ref"], "")
        if not name:
            continue
        urls = get_channel_logo_urls(name)
        if not urls:
            broken.append({"name": name, "reason": "keine Logo-URL konfiguriert"})
            continue
        ok = False
        for url in urls:
            try:
                req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "e2proxy/1.0"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    if resp.status == 200:
                        ok = True
                        break
            except Exception:
                pass
        if not ok:
            broken.append({"name": name, "reason": "Logo nicht erreichbar"})
    return broken


# ── EPG Scheduler ─────────────────────────────────────────
def epg_scheduler_loop():
    """Hintergrund-Thread: startet EPG-Run täglich zur konfigurierten Stunde."""
    last_run_day = None
    while True:
        try:
            now = datetime.now()
            target_hour = get_epg_schedule_hour()
            if now.hour == target_hour and last_run_day != now.date():
                last_run_day = now.date()
                _epg_log(f"Scheduler: Starte geplanten EPG-Run ({target_hour}:00)")
                run_epg_update(triggered_by="scheduler")
        except Exception as e:
            log.warning(f"EPG Scheduler Fehler: {e}")
        time.sleep(60)




def get_epg_xml(force_refresh=False):
    """Gibt gecachtes EPG XML zurück. Auto-Refresh nur wenn Cache leer oder >24h alt.
    Der vollständige Run (mit Zap-Reload) wird vom Scheduler bzw. Startup gemacht."""
    with epg_cache_lock:
        age = time.time() - epg_cache["last_update"]
        xml = epg_cache["xml"]
    # Kein Auto-Refresh mehr nach 1h — das würde Zap-Reload überspringen.
    # Nur refreshen wenn Cache komplett leer ist.
    if force_refresh or xml is None:
        log.info("EPG: kein Cache vorhanden, starte Run...")
        try:
            # Im Hintergrund vollständigen Run starten
            import threading as _t
            _t.Thread(target=lambda: run_epg_update("auto"), daemon=True).start()
        except Exception as e:
            log.error(f"EPG Auto-Run Fehler: {e}")
    with epg_cache_lock:
        return epg_cache["xml"]
