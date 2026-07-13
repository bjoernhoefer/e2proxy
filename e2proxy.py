#!/usr/bin/env python3
"""
e2proxy.py - Enigma2 Stream Proxy
==================================
Endpoints:
  GET /                              → Web-UI
  GET /favorites                     → Favoriten-Verwaltung
  GET /settings                      → Einstellungen (Settings + Config)
  GET /playlist.m3u?profile=NAME     → M3U Playlist für ein Device-Profil
  GET /stream?ref=...&profile=NAME   → Stream
  GET /api/config                    → Aktuelle Config als JSON
  POST /api/config                   → Config speichern
  GET /api/favorites                 → Favoriten als JSON
  POST /api/favorites                → Favoriten speichern
  GET /api/status                    → Receiver-Status
  GET /kill?receiver=ID              → Stream abbrechen
  GET /health                        → Health-Check
"""

import http.server
import urllib.request
import urllib.parse
import socket
import threading
import subprocess
import select
import time
import logging
import sys
import os
import re
import json
import signal
import copy
import struct
import gzip
from datetime import datetime

# ── Pfade ─────────────────────────────────────────────────
# Datenpfad: via Env-Variable überschreibbar (für Docker)
DATA_DIR       = os.environ.get("E2PROXY_DATA_DIR", "/var/lib/e2proxy")
VERSION        = "3.8"   # Versions-ID — wird bei jeder Änderung neu generiert
CONFIG_FILE    = f"{DATA_DIR}/config.json"
FAVORITES_FILE = f"{DATA_DIR}/favorites.json"

# ── Sender Logo Datenbank ─────────────────────────────────
# Jeder Sender kann mehrere Fallback-URLs haben (Liste).
# Einzelne Strings werden automatisch in Listen konvertiert.
CHANNEL_LOGOS = {
    "Das Erste HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/das-erste-hd.png",
    "Das Erste": "https://raw.githubusercontent.com/cytec/tvlogos/master/das-erste.png",
    "ZDF HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/zdf-hd.png",
    "ZDF": "https://raw.githubusercontent.com/cytec/tvlogos/master/zdf.png",
    "RTL HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/rtl-hd.png",
    "RTL Television": "https://raw.githubusercontent.com/cytec/tvlogos/master/rtl.png",
    "RTL": "https://raw.githubusercontent.com/cytec/tvlogos/master/rtl.png",
    "SAT.1 HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/sat.1-hd.png",
    "SAT.1": "https://raw.githubusercontent.com/cytec/tvlogos/master/sat.1.png",
    "ProSieben HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/pro7-hd.png",
    "ProSieben": "https://raw.githubusercontent.com/cytec/tvlogos/master/pro7.png",
    "VOX HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/vox-hd.png",
    "VOX": "https://raw.githubusercontent.com/cytec/tvlogos/master/vox.png",
    "kabel eins HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/kabel-eins-hd.png",
    "kabel eins": "https://raw.githubusercontent.com/cytec/tvlogos/master/kabel-eins.png",
    "RTLZWEI HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/rtl2-hd.png",
    "RTLZWEI": "https://raw.githubusercontent.com/cytec/tvlogos/master/rtl2.png",
    "RTL ZWEI HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/rtl2-hd.png",
    "RTL2": "https://raw.githubusercontent.com/cytec/tvlogos/master/rtl2.png",
    "ServusTV HD Oesterreich": "https://raw.githubusercontent.com/cytec/tvlogos/master/servustv-hd.png",
    "ServusTV": "https://raw.githubusercontent.com/cytec/tvlogos/master/servustv.png",
    "DMAX HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/dmax-hd.png",
    "DMAX": "https://raw.githubusercontent.com/cytec/tvlogos/master/dmax.png",
    "TLC HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/tlc.png",
    "TLC": "https://raw.githubusercontent.com/cytec/tvlogos/master/tlc.png",
    "SAT.1 Gold HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/sat.1-gold.png",
    "Sat.1 Gold HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/sat.1-gold.png",
    "SAT.1 Gold": "https://raw.githubusercontent.com/cytec/tvlogos/master/sat.1-gold.png",
    "ProSieben MAXX HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/pro7-maxx.png",
    "ProSieben MAXX": "https://raw.githubusercontent.com/cytec/tvlogos/master/pro7-maxx.png",
    "sixx HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/sixx-hd.png",
    "sixx": "https://raw.githubusercontent.com/cytec/tvlogos/master/sixx.png",
    "One HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/one.png",
    "One": "https://raw.githubusercontent.com/cytec/tvlogos/master/one.png",
    "One Terra HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/one.png",
    "ZDFneo HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/zdfneo-hd.png",
    "ZDFneo": "https://raw.githubusercontent.com/cytec/tvlogos/master/zdfneo.png",
    "ZDFinfo HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/zdfinfo-hd.png",
    "ZDFinfo": "https://raw.githubusercontent.com/cytec/tvlogos/master/zdfinfo.png",
    "3sat HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/3sat-hd.png",
    "3sat": "https://raw.githubusercontent.com/cytec/tvlogos/master/3sat.png",
    "arte HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/arte-hd.png",
    "arte": "https://raw.githubusercontent.com/cytec/tvlogos/master/arte.png",
    "Phoenix HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/phoenix-hd.png",
    "phoenix HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/phoenix-hd.png",
    "Phoenix": "https://raw.githubusercontent.com/cytec/tvlogos/master/phoenix.png",
    "ARD-alpha HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/br-alpha.png",
    "KiKA HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/kika-hd.png",
    "KiKA": "https://raw.githubusercontent.com/cytec/tvlogos/master/kika.png",
    "tagesschau24 HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/tagesschau24.png",
    "tagesschau24": "https://raw.githubusercontent.com/cytec/tvlogos/master/tagesschau24.png",
    "BR Fernsehen S\u00fcd HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/br.png",
    "WDR HD K\u00f6ln": "https://raw.githubusercontent.com/cytec/tvlogos/master/wdr-hd.png",
    "NDR FS HH HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/ndr-hd.png",
    "MDR Sachsen HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/mdr.png",
    "SWR Fernsehen HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/swr-hd.png",
    "hr-fernsehen HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/hr.png",
    "rbb HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/rbb.png",
    "n-tv HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/n-tv.png",
    "n-tv": "https://raw.githubusercontent.com/cytec/tvlogos/master/n-tv.png",
    "WELT HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/n24-hd.png",
    "WELT": "https://raw.githubusercontent.com/cytec/tvlogos/master/n24.png",
    "N24 Doku HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/n24-hd.png",
    "Sport1 HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/sport1-hd.png",
    "Sport1": "https://raw.githubusercontent.com/cytec/tvlogos/master/sport1.png",
    "Eurosport 1 HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/eurosport-hd.png",
    "Eurosport 1": "https://raw.githubusercontent.com/cytec/tvlogos/master/eurosport.png",
    "Sky Sport News HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/sky-sport-news-hd.png",
    "TELE 5 HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/tele-5-hd.png",
    "TELE 5": "https://raw.githubusercontent.com/cytec/tvlogos/master/tele-5.png",
    "Comedy Central HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/comedy-central.png",
    "MTV HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/mtv.png",
    "MTV": "https://raw.githubusercontent.com/cytec/tvlogos/master/mtv.png",
    "NICK HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/nickelodeon.png",
    "Nickelodeon": "https://raw.githubusercontent.com/cytec/tvlogos/master/nickelodeon.png",
    "Disney Channel HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/disney-channel.png",
    "CNN HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/cnn.png",
    "CNN": "https://raw.githubusercontent.com/cytec/tvlogos/master/cnn.png",
    "BBC World News HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/bbc.png",
    "Euronews HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/euronews.png",
    "Euronews": "https://raw.githubusercontent.com/cytec/tvlogos/master/euronews.png",
    "ORF 1 HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/orf-eins.png",
    "ORF 2 HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/orf-eins.png",
    "ATV HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/orf-eins.png",
    "Puls 4 HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/puls4.png",
    "Super RTL HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/super-rtl-hd.png",
    "Super RTL": "https://raw.githubusercontent.com/cytec/tvlogos/master/super-rtl.png",
    "RTL Nitro HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/rtl-nitro.png",
    "RTL Nitro": "https://raw.githubusercontent.com/cytec/tvlogos/master/rtl-nitro.png",
    "RTL Crime HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/rtl-crime-hd.png",
    "RTL Living HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/rtl-living-hd.png",
    "Kabel Eins Doku HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/kabel-eins-classics.png",
    "Kabel Eins Doku": "https://raw.githubusercontent.com/cytec/tvlogos/master/kabel-eins-classics.png",
    "History HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/history.png",
    "Discovery Channel HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/discovery-channel.png",
    "Syfy HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/syfy.png",
    "TNT Serie HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/tnt-serie.png",
    "TNT Comedy HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/tnt-comedy.png",
    "TNT Film HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/sky-cinema.png",
    "Sky Atlantic HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/sky-atlantic-hd.png",
    "National Geographic HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/nat-geo.png",
    "Nat Geo Wild HD": "https://raw.githubusercontent.com/cytec/tvlogos/master/nat-geo-wild.png",
}

LOGO_CACHE_DIR = f"{DATA_DIR}/logos"
logo_cache_lock = threading.Lock()

def get_channel_logo_urls(name):
    """Gibt Liste der konfigurierten Logo-URLs fuer einen Sender zurück."""
    val = CHANNEL_LOGOS.get(name, "")
    if not val:
        return []
    if isinstance(val, list):
        return val
    return [val]

def get_channel_logo(name):
    """Gibt die Logo-URL zurück (für M3U Playlist).
    Ein manuell hinterlegtes Custom-Logo hat Vorrang vor der Logo-Datenbank."""
    if has_custom_logo(name):
        return custom_logo_local_url(name)
    urls = get_channel_logo_urls(name)
    return urls[0] if urls else ""

def logo_cache_filename(name):
    """Dateiname für gecachtes Logo (aus Sendername)."""
    import re
    safe = re.sub(r'[^a-zA-Z0-9_.-]', '_', name)
    return f"{safe}.png"

def logo_local_url(name):
    """Gibt die lokale Proxy-URL für ein gecachtes Logo zurück."""
    host = get_proxy_host()
    port = get_proxy_port()
    fname = logo_cache_filename(name)
    return f"http://{host}:{port}/logos/{fname}"

def download_logo(name, force=False):
    """Lädt Logo für einen Sender herunter — probiert alle URLs durch.
    Gibt True zurück wenn erfolgreich, False wenn alle URLs fehlschlagen.

    Ist das Logo bereits lokal gecacht (und force=False), wird KEIN
    Netzwerk-Request gemacht. Das verhindert, dass bei jedem Neustart 100+
    HTTPS-Requests an einen externen Host (raw.githubusercontent.com) abgesetzt
    und alle PNGs neu auf die SD-Karte geschrieben werden — ein Muster, das in
    der Vergangenheit mit minutenlangen bis stundenlangen Hängern korrelierte
    (Netzwerk-/DNS-Stall, da urlopen-timeout die DNS-Auflösung nicht begrenzt).
    """
    urls = get_channel_logo_urls(name)
    if not urls:
        return False
    os.makedirs(LOGO_CACHE_DIR, exist_ok=True)
    fname = os.path.join(LOGO_CACHE_DIR, logo_cache_filename(name))
    if not force:
        try:
            if os.path.exists(fname) and os.path.getsize(fname) > 100:
                return True
        except OSError:
            pass
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "e2proxy/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            if len(data) > 100:  # Mindestgröße — kein leeres/kaputtes Bild
                with open(fname, "wb") as f:
                    f.write(data)
                log.debug(f"Logo gecacht: {name} → {fname}")
                return True
        except Exception as e:
            log.debug(f"Logo URL fehlgeschlagen ({url}): {e}")
    log.warning(f"Logo download failed: {name} (all URLs failed)")
    return False

def refresh_logo_cache(force=False):
    """Lädt konfigurierte Logos herunter und cached sie lokal.
    Läuft als Hintergrund-Thread.

    force=False (Standard, z.B. beim Start): nur fehlende Logos werden geladen,
    bereits gecachte werden übersprungen — kein Netzwerk-/SD-Sturm bei jedem
    Neustart. force=True (manueller Refresh): alle Logos werden neu geladen.
    """
    with logo_cache_lock:
        os.makedirs(LOGO_CACHE_DIR, exist_ok=True)
        total = len(CHANNEL_LOGOS)
        ok = 0
        downloaded = 0
        for name in CHANNEL_LOGOS:
            fname = os.path.join(LOGO_CACHE_DIR, logo_cache_filename(name))
            already = False
            try:
                already = os.path.exists(fname) and os.path.getsize(fname) > 100
            except OSError:
                already = False
            if download_logo(name, force=force):
                ok += 1
                if force or not already:
                    downloaded += 1
        log.info(f"Logo cache: {ok}/{total} logos available ({downloaded} downloaded, "
                 f"{ok - downloaded} from cache, force={force})")
    return ok

def get_logo_for_epg(name):
    """Gibt die URL für den EPG zurück.
    Reihenfolge: manuelles Custom-Logo > lokaler Cache > direkte URL.
    """
    if has_custom_logo(name):
        return custom_logo_local_url(name)
    fname = os.path.join(LOGO_CACHE_DIR, logo_cache_filename(name))
    if os.path.exists(fname):
        return logo_local_url(name)
    # Noch nicht gecacht — direkte URL als Fallback
    return get_channel_logo(name)


# ── Custom Sender-Logos (manuell, nur für Favoriten) ──────────────────
# Nutzer können in den Einstellungen (Wartung) pro Favorit ein eigenes Logo
# hinterlegen — entweder per Datei-Upload oder per URL. Das Bild wird mit
# ffmpeg ins richtige Format (PNG, max. 400px Breite) konvertiert und lokal
# gespeichert. Custom-Logos haben Vorrang vor der Logo-Datenbank und dem
# automatischen Cache.
CUSTOM_LOGO_DIR   = f"{DATA_DIR}/custom_logos"
CUSTOM_LOGOS_FILE = f"{DATA_DIR}/custom_logos.json"
custom_logos_lock = threading.Lock()

def load_custom_logos():
    """Mapping Sendername → Dateiname der hinterlegten Custom-Logos."""
    try:
        if os.path.exists(CUSTOM_LOGOS_FILE):
            with open(CUSTOM_LOGOS_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception as e:
        log.warning(f"Custom-Logos laden fehlgeschlagen: {e}")
    return {}

def save_custom_logos(mapping):
    try:
        os.makedirs(os.path.dirname(CUSTOM_LOGOS_FILE), exist_ok=True)
        with open(CUSTOM_LOGOS_FILE, "w") as f:
            json.dump(mapping, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        log.error(f"Custom-Logos speichern fehlgeschlagen: {e}")
        return False

def custom_logo_path(name):
    return os.path.join(CUSTOM_LOGO_DIR, logo_cache_filename(name))

def has_custom_logo(name):
    try:
        p = custom_logo_path(name)
        return os.path.exists(p) and os.path.getsize(p) > 100
    except OSError:
        return False

def custom_logo_local_url(name):
    """Lokale Proxy-URL für ein Custom-Logo (mit Cache-Buster gegen Client-Caching)."""
    host = get_proxy_host()
    port = get_proxy_port()
    fname = logo_cache_filename(name)
    try:
        ver = int(os.path.getmtime(custom_logo_path(name)))
    except OSError:
        ver = 0
    return f"http://{host}:{port}/custom_logos/{fname}?v={ver}"

def _fetch_image_bytes(url, max_bytes=8 * 1024 * 1024):
    """Lädt ein Bild von einer URL herunter (begrenzt auf max_bytes)."""
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("Nur http(s)-URLs erlaubt")
    req = urllib.request.Request(url, headers={"User-Agent": "e2proxy/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("Bild zu groß (max 8 MB)")
    if len(data) < 100:
        raise ValueError("Bild leer oder ungültig")
    return data

def convert_and_store_custom_logo(name, img_bytes):
    """Konvertiert ein beliebiges Bild via ffmpeg nach PNG (max. 400px breit,
    Seitenverhältnis erhalten) und speichert es als Custom-Logo für 'name'.
    Wirft bei Fehler eine Exception."""
    if not name:
        raise ValueError("Sendername fehlt")
    if not img_bytes or len(img_bytes) < 100:
        raise ValueError("Kein Bild empfangen")
    os.makedirs(CUSTOM_LOGO_DIR, exist_ok=True)
    out = custom_logo_path(name)
    tmp = out + ".tmp.png"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", "pipe:0",
        "-vf", "scale='if(gt(iw,400),400,iw)':-2",
        "-frames:v", "1",
        "-f", "image2", tmp,
    ]
    try:
        proc = subprocess.run(cmd, input=img_bytes,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=30)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg nicht gefunden")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Bildkonvertierung Timeout")
    if proc.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) < 100:
        err = (proc.stderr or b"").decode("utf-8", "replace")[:300]
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise RuntimeError(f"Konvertierung fehlgeschlagen: {err or 'unbekannter Fehler'}")
    os.replace(tmp, out)
    with custom_logos_lock:
        mapping = load_custom_logos()
        mapping[name] = logo_cache_filename(name)
        save_custom_logos(mapping)
    log.info(f"Custom-Logo gespeichert: {name} → {out}")
    return True

def delete_custom_logo(name):
    """Entfernt ein Custom-Logo — der Sender fällt auf das automatische Logo zurück."""
    removed = False
    try:
        p = custom_logo_path(name)
        if os.path.exists(p):
            os.remove(p)
            removed = True
    except OSError as e:
        log.warning(f"Custom-Logo löschen fehlgeschlagen ({name}): {e}")
    with custom_logos_lock:
        mapping = load_custom_logos()
        if name in mapping:
            del mapping[name]
            save_custom_logos(mapping)
            removed = True
    if removed:
        log.info(f"Custom-Logo entfernt: {name}")
    return removed

def get_favorite_logo_overview():
    """Liefert für jeden Favoriten Name, Ref, aktuelle Logo-URL, Custom-Status
    und die automatische (Datenbank-)URL — für die Bearbeitungs-UI."""
    favs = load_favorites()
    with channel_cache_lock:
        all_channels = channel_cache.get("channels", [])
    ref_to_name = {ch["ref"]: ch["name"] for ch in all_channels}
    out = []
    seen = set()
    for f in favs:
        ref = f.get("ref", "")
        name = ref_to_name.get(ref) or f.get("name") or ""
        if not name or name in seen:
            continue
        seen.add(name)
        is_custom = has_custom_logo(name)
        auto_urls = get_channel_logo_urls(name)
        out.append({
            "name": name,
            "ref": ref,
            "custom": is_custom,
            "logo_url": custom_logo_local_url(name) if is_custom else get_logo_for_epg(name),
            "auto_url": auto_urls[0] if auto_urls else "",
        })
    return out


CONFIG_DEFAULT = f"{DATA_DIR}/config_default.json"
# ──────────────────────────────────────────────────────────

# ── Log-Level Ring-Buffer ─────────────────────────────────────────────────
import collections as _collections

_LOG_BUFFER = _collections.deque(maxlen=500)  # RAM-Buffer, max 500 Einträge
_LOG_LEVEL_NAMES = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
_current_display_level = [20]  # INFO default, als Liste für mutability

class _RingBufferHandler(logging.Handler):
    """Speichert alle Log-Einträge in RAM-Buffer mit Unix-Timestamp."""
    def emit(self, record):
        try:
            _LOG_BUFFER.append({
                "ts_unix": record.created,
                "ts": self.formatter.formatTime(record, "%Y-%m-%d %H:%M:%S"),
                "level": record.levelname,
                "msg": record.getMessage(),
            })
        except Exception:
            pass

def set_log_level(level_name):
    """Setzt den Anzeige-Level ohne Restart."""
    lvl = _LOG_LEVEL_NAMES.get(level_name.upper(), 20)
    _current_display_level[0] = lvl
    log.setLevel(10)  # immer alles loggen, Filterung beim Abruf

def get_log_entries(level_name="INFO", since=None, n=100):
    """Gibt gefilterte Log-Einträge zurück.
    
    since: Unix-Timestamp — nur Einträge nach diesem Zeitpunkt
    n: Max Anzahl (None = alle)
    """
    min_lvl = _LOG_LEVEL_NAMES.get(level_name.upper(), 20)
    entries = [e for e in _LOG_BUFFER if _LOG_LEVEL_NAMES.get(e["level"], 0) >= min_lvl]
    if since is not None:
        entries = [e for e in entries if e.get("ts_unix", 0) > since]
    if n is not None:
        return entries[-n:]
    return entries

def get_log_entries_from_disk(level_name="INFO", since_unix=None, max_lines=10000):
    """Liest Log-Einträge aus den File-Logs (RAM + Disk kombiniert).
    
    since_unix: nur Einträge ab diesem Zeitpunkt
    Liest alle e2proxy.log.* Dateien die im Bereich liegen.
    """
    import re, glob, datetime as _dt
    min_lvl = _LOG_LEVEL_NAMES.get(level_name.upper(), 20)
    log_file = os.path.join(DATA_DIR, "e2proxy.log")
    pattern = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:,\d+)?\s+\[(\w+)\]\s+(.*)$")
    results = []
    # Sammle alle Log-Dateien (aktuelle + rotierte)
    files = sorted(glob.glob(log_file + "*"))
    for fp in files:
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = pattern.match(line)
                    if not m:
                        continue
                    ts_str, lvl, msg = m.groups()
                    if _LOG_LEVEL_NAMES.get(lvl, 0) < min_lvl:
                        continue
                    try:
                        ts_unix = _dt.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").timestamp()
                    except Exception:
                        continue
                    if since_unix is not None and ts_unix < since_unix:
                        continue
                    results.append({
                        "ts_unix": ts_unix,
                        "ts": ts_str,
                        "level": lvl,
                        "msg": msg.rstrip(),
                    })
                    if len(results) >= max_lines:
                        break
            if len(results) >= max_lines:
                break
        except Exception:
            continue
    return results

# Lokale Zeit in Logs (nicht UTC)
logging.Formatter.converter = time.localtime
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
# Lokale Zeit statt UTC in Logs
logging.Formatter.converter = lambda *args: __import__('datetime').datetime.now().timetuple()
log = logging.getLogger("e2proxy")
_ring_handler = _RingBufferHandler()
_ring_handler.setFormatter(logging.Formatter())
_ring_handler.setLevel(logging.DEBUG)
log.addHandler(_ring_handler)

def _setup_file_logging():
    """
    Richtet File-Logging ein: tägliche Rotation, konfigurierbare Aufbewahrung.
    Wird zusätzlich zum bestehenden stdout-Handler gehängt.
    Nach load_config() aufrufen damit retention aus Config kommt.
    """
    import logging.handlers
    log_file  = os.path.join(DATA_DIR, "e2proxy.log")
    retention = int(get_config().get("log_retention_days", 5))
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        fh = logging.handlers.TimedRotatingFileHandler(
            log_file,
            when="midnight",
            interval=1,
            backupCount=retention,
            encoding="utf-8",
        )
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        fh.suffix = "%Y-%m-%d.log"   # e2proxy.log.2026-06-10
        log.addHandler(fh)
        log.info(f"File logging: {log_file} ({retention} days retention)")
    except Exception as e:
        log.warning(f"File-Logging konnte nicht eingerichtet werden: {e}")


class ScrambledStreamError(Exception):
    pass


# ── Config Management ─────────────────────────────────────

config_lock = threading.Lock()
_config = {}

def load_config():
    global _config
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                _config = json.load(f)
                log.info(f"Config loaded: {CONFIG_FILE}")
                return
    except Exception as e:
        log.warning(f"Config laden fehlgeschlagen: {e}")
    # Default laden
    try:
        with open(CONFIG_DEFAULT, 'r') as f:
            _config = json.load(f)
        log.info("Default config loaded")
        save_config()
    except Exception as e:
        log.error(f"Default-Config laden fehlgeschlagen: {e}")
        _config = {"receivers": [], "transcode_profiles": {}, "device_profiles": {}}

def save_config():
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(_config, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        log.error(f"Config speichern fehlgeschlagen: {e}")
        return False

def get_config():
    with config_lock:
        return copy.deepcopy(_config)

def update_config(new_config):
    global _config
    with config_lock:
        _config = new_config
        return save_config()

def get_receivers():
    return get_config().get("receivers", [])

def get_receiver_by_id(rid):
    for r in get_receivers():
        if r["id"] == rid:
            return r
    return None

def is_receiver_locked(r):
    """True, wenn der Tuner in den Einstellungen gesperrt ist."""
    return bool(r.get("locked", False))

def is_receiver_usable(r):
    """Receiver darf vom e2proxy verwendet werden: aktiviert und nicht gesperrt."""
    return r.get("enabled", True) and not r.get("locked", False)

def get_transcode_profile(name):
    return get_config().get("transcode_profiles", {}).get(name)

def get_device_profile(name):
    return get_config().get("device_profiles", {}).get(name)

def get_proxy_host():
    return get_config().get("proxy_host", "127.0.0.1")

def get_proxy_port():
    return int(get_config().get("proxy_port", 8888))

def get_zap_wait():
    return float(get_config().get("zap_wait_sec", 2.0))

def get_default_profile():
    return get_config().get("default_device_profile", "Web-SD")


# ── Umschalt-Tuning (Switch Time) ─────────────────────────
# Globale Defaults für schnelles Umschalten. Pro-Sender-Overrides und
# selbstlernende Werte liegen im SwitchStats-Store (switch_stats.json).

SWITCH_STATS_FILE = f"{DATA_DIR}/switch_stats.json"

# Harte Untergrenzen/Defaults, falls Config-Keys fehlen
_PROBE_DEFAULT          = 15000000   # normaler Probesize/Analyzeduration
_NOLAT_PROBE_DEFAULT    = 500000     # NoLatency: minimaler Probesize
_SWITCH_MONITOR_DEFAULT = 10.0       # s First-Data-Fenster
_SWITCH_RETRIES_DEFAULT = 2          # zusätzliche ffmpeg-Startversuche
_NOLAT_FAIL_THRESHOLD   = 3          # ab so vielen Fehlern Probesize erhöhen
_PROBE_MAX              = 15000000   # Obergrenze für gelernten Probesize

def get_switch_global():
    """Globale Umschalt-Defaults aus der Config (mit Fallbacks)."""
    c = get_config()
    return {
        "no_latency":            bool(c.get("no_latency", False)),
        "zap_wait":              float(c.get("zap_wait_sec", 1.0)),
        "probe_default":         int(c.get("probe_default", _PROBE_DEFAULT)),
        "nolat_probesize":       int(c.get("no_latency_probesize", _NOLAT_PROBE_DEFAULT)),
        "nolat_analyzeduration": int(c.get("no_latency_analyzeduration", _NOLAT_PROBE_DEFAULT)),
        "monitor_sec":           float(c.get("switch_monitor_sec", _SWITCH_MONITOR_DEFAULT)),
        "max_retries":           int(c.get("switch_max_retries", _SWITCH_RETRIES_DEFAULT)),
        "fail_threshold":        int(c.get("nolatency_fail_threshold", _NOLAT_FAIL_THRESHOLD)),
    }


_switch_stats_lock = threading.Lock()
_switch_stats = None  # lazy-loaded dict: {ref: {...}}

def _norm_ref(service_ref):
    return (service_ref or "").rstrip("/")

def _load_switch_stats():
    global _switch_stats
    if _switch_stats is not None:
        return _switch_stats
    try:
        if os.path.exists(SWITCH_STATS_FILE):
            with open(SWITCH_STATS_FILE) as f:
                _switch_stats = json.load(f)
        else:
            _switch_stats = {}
    except Exception as e:
        log.warning(f"Switch-Stats laden fehlgeschlagen: {e}")
        _switch_stats = {}
    return _switch_stats

def _save_switch_stats():
    try:
        os.makedirs(os.path.dirname(SWITCH_STATS_FILE), exist_ok=True)
        tmp = SWITCH_STATS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_switch_stats, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SWITCH_STATS_FILE)
    except Exception as e:
        log.warning(f"Switch-Stats speichern fehlgeschlagen: {e}")

def _switch_entry(ref):
    """Liefert (und legt bei Bedarf an) den Store-Eintrag für einen Sender."""
    stats = _load_switch_stats()
    key = _norm_ref(ref)
    e = stats.get(key)
    if e is None:
        e = {
            "name": None,
            "no_latency": None,          # None = globalen Default nutzen
            "zap_wait": None,            # None = globalen Default nutzen
            "probesize": None,           # None = kein gelernter Override
            "nolatency_fail_streak": 0,
            "nolatency_fail_total": 0,
            "zap": {"ok": 0, "fail": 0, "last_ms": 0, "avg_ms": 0},
            "start": {"ok": 0, "fail": 0, "retries": 0},
            "last_update": None,
        }
        stats[key] = e
    return e

def get_switch_settings(service_ref):
    """Effektive Umschalt-Parameter für einen Sender (global + Per-Sender-Override)."""
    g = get_switch_global()
    with _switch_stats_lock:
        stats = _load_switch_stats()
        e = stats.get(_norm_ref(service_ref)) or {}
        no_latency = e.get("no_latency")
        zap_wait   = e.get("zap_wait")
        learned    = e.get("probesize")
    no_latency = g["no_latency"] if no_latency is None else bool(no_latency)
    zap_wait   = g["zap_wait"]   if zap_wait   is None else float(zap_wait)
    if no_latency:
        probesize = int(learned) if learned else g["nolat_probesize"]
        analyzeduration = g["nolat_analyzeduration"]
        if learned:
            analyzeduration = max(analyzeduration, int(learned))
    else:
        probesize = g["probe_default"]
        analyzeduration = g["probe_default"]
    return {
        "no_latency": no_latency,
        "zap_wait": zap_wait,
        "probesize": int(probesize),
        "analyzeduration": int(analyzeduration),
        "monitor_sec": g["monitor_sec"],
        "max_retries": g["max_retries"],
    }

def record_zap_result(service_ref, ok, elapsed_ms, wait_used, channel_name=None):
    """Zap-Ergebnis (Erfolg/Fehler) + Dauer in der Statistik festhalten."""
    with _switch_stats_lock:
        e = _switch_entry(service_ref)
        if channel_name:
            e["name"] = channel_name
        z = e["zap"]
        if ok:
            z["ok"] = z.get("ok", 0) + 1
        else:
            z["fail"] = z.get("fail", 0) + 1
        z["last_ms"] = int(elapsed_ms)
        n = z["ok"] + z["fail"]
        prev = z.get("avg_ms", 0)
        z["avg_ms"] = int((prev * (n - 1) + elapsed_ms) / n) if n else int(elapsed_ms)
        e["zap_wait_last"] = float(wait_used)
        e["last_update"] = datetime.now().isoformat()
        _save_switch_stats()

def record_stream_start(service_ref, ok, attempts, no_latency, channel_name=None):
    """ffmpeg-Startergebnis erfassen. Bei NoLatency-Fehlern lernt der Store
    den Probesize für diesen Sender in kleinen Schritten hoch."""
    g = get_switch_global()
    with _switch_stats_lock:
        e = _switch_entry(service_ref)
        if channel_name:
            e["name"] = channel_name
        s = e["start"]
        s["retries"] = s.get("retries", 0) + max(0, attempts - 1)
        if ok:
            s["ok"] = s.get("ok", 0) + 1
            e["nolatency_fail_streak"] = 0
        else:
            s["fail"] = s.get("fail", 0) + 1
            if no_latency:
                e["nolatency_fail_streak"] = e.get("nolatency_fail_streak", 0) + 1
                e["nolatency_fail_total"] = e.get("nolatency_fail_total", 0) + 1
                # Schwelle überschritten → Probesize für diesen Sender anheben
                if e["nolatency_fail_streak"] >= g["fail_threshold"]:
                    cur = e.get("probesize") or g["nolat_probesize"]
                    new = min(_PROBE_MAX, int(cur * 2))
                    if new != cur:
                        e["probesize"] = new
                        log.info(f"Switch: Probesize für '{e.get('name') or service_ref[:30]}' "
                                 f"erhöht {cur} → {new} (NoLatency-Fehler)")
                    e["nolatency_fail_streak"] = 0
        e["last_update"] = datetime.now().isoformat()
        _save_switch_stats()

def get_switch_stats_snapshot():
    with _switch_stats_lock:
        return copy.deepcopy(_load_switch_stats())

def set_switch_override(service_ref, no_latency=None, zap_wait=None, probesize=None, channel_name=None):
    """Manuellen Per-Sender-Override setzen. Wert None löscht den Override."""
    with _switch_stats_lock:
        e = _switch_entry(service_ref)
        if channel_name:
            e["name"] = channel_name
        e["no_latency"] = no_latency
        e["zap_wait"] = zap_wait
        if probesize is not None:
            e["probesize"] = int(probesize) if probesize else None
        e["last_update"] = datetime.now().isoformat()
        _save_switch_stats()

def reset_switch_stats(service_ref=None):
    """Setzt gelernte Werte/Statistik zurück (ein Sender oder alle)."""
    global _switch_stats
    with _switch_stats_lock:
        stats = _load_switch_stats()
        if service_ref is None:
            _switch_stats = {}
        else:
            stats.pop(_norm_ref(service_ref), None)
        _save_switch_stats()


# ── Favorites Management ──────────────────────────────────

favorites_lock = threading.Lock()

def load_favorites():
    try:
        if os.path.exists(FAVORITES_FILE):
            with open(FAVORITES_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        log.warning(f"Favoriten laden fehlgeschlagen: {e}")
    return []

def save_favorites(favs):
    try:
        os.makedirs(os.path.dirname(FAVORITES_FILE), exist_ok=True)
        with open(FAVORITES_FILE, 'w') as f:
            json.dump(favs, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        log.error(f"Favoriten speichern fehlgeschlagen: {e}")
        return False

def get_favorites():
    with favorites_lock:
        return load_favorites()


# ── Receiver State Management ─────────────────────────────

receiver_lock = threading.Lock()
_receiver_state = {}   # receiver_id → None | dict(info)
stream_processes = {}  # receiver_id → subprocess.Popen
stream_processes_lock = threading.Lock()

# Client dedup - prevent Kodi parallel probe requests
client_stream_lock = threading.Lock()
_active_client_streams = {}  # (client_ip, service_ref) → timestamp


def _init_receiver_state():
    global _receiver_state
    with receiver_lock:
        for r in get_receivers():
            if r["id"] not in _receiver_state:
                _receiver_state[r["id"]] = None


def get_free_receiver(preferred_id=None):
    """Gibt ID des ersten freien Receivers zurück. Bevorzugt preferred_id."""
    with receiver_lock:
        receivers = get_receivers()
        # Preferred zuerst
        if preferred_id and preferred_id != "auto":
            state = _receiver_state.get(preferred_id)
            for r in receivers:
                if r["id"] == preferred_id and is_receiver_usable(r) and state is None:
                    return preferred_id
        # Default Receiver
        for r in receivers:
            if r.get("default") and is_receiver_usable(r):
                if _receiver_state.get(r["id"]) is None:
                    return r["id"]
        # Erster freier
        for r in receivers:
            if is_receiver_usable(r) and _receiver_state.get(r["id"]) is None:
                return r["id"]
    return None


# Lock pro service_ref — verhindert dass derselbe Sender gleichzeitig zweimal started wird
_stream_ref_locks = {}
_stream_ref_locks_lock = threading.Lock()

def get_ref_lock(service_ref):
    with _stream_ref_locks_lock:
        if service_ref not in _stream_ref_locks:
            _stream_ref_locks[service_ref] = threading.Lock()
        return _stream_ref_locks[service_ref]


def acquire_receiver(rid, client_ip, service_ref="", channel_name=""):
    with receiver_lock:
        _receiver_state[rid] = {
            "client_ip": client_ip,
            "service_ref": service_ref,
            "channel_name": channel_name,
            "started": datetime.now().strftime("%H:%M:%S"),
            "started_ts": time.time(),
        }
    r = get_receiver_by_id(rid)
    log.info(f"Receiver '{rid}' ({r['name'] if r else rid}) busy from {client_ip}")


def release_receiver(rid):
    with receiver_lock:
        _receiver_state[rid] = None
    r = get_receiver_by_id(rid)
    log.info(f"Receiver '{rid}' ({r['name'] if r else rid}) released")


def kill_proc_robust(proc, label="", grace=5):
    """Beendet einen Prozess zuverlässig: erst SIGTERM, nach `grace`s SIGKILL.

    Ein netzwerk-blockiertes ffmpeg reagiert oft nicht auf SIGTERM und würde
    sonst als Zombie weiterlaufen (Tuner-/Stream-Leak). Die SIGKILL-Eskalation
    garantiert, dass der Prozess wirklich verschwindet.
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=grace)
        return
    except Exception:
        pass
    try:
        proc.kill()
        log.warning(f"{label or 'Prozess'}: reagiert nicht auf SIGTERM — SIGKILL gesendet")
    except Exception:
        pass


def kill_stream(rid):
    with stream_processes_lock:
        proc = stream_processes.get(rid)
    kill_proc_robust(proc, label=f"Stream '{rid}'")
    release_receiver(rid)
    log.info(f"Stream auf Receiver '{rid}' abgebrochen")


def is_receiver_online(rid):
    r = get_receiver_by_id(rid)
    if not r:
        return False
    try:
        url = f"http://{r['ip']}:{r['port']}/web/about"
        with urllib.request.urlopen(url, timeout=3) as resp:
            return resp.status == 200
    except:
        return False


def do_zap(rid, service_ref, channel_name=None):
    r = get_receiver_by_id(rid)
    if not r:
        return False
    encoded = urllib.parse.quote(service_ref, safe=":@")
    url = f"http://{r['ip']}:{r['port']}/web/zap?sRef={encoded}"
    t0 = time.time()
    wait_used = get_switch_settings(service_ref)["zap_wait"]
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            elapsed_ms = (time.time() - t0) * 1000
            if "<e2state>True</e2state>" in body:
                log.info(f"ZAP OK → '{rid}' → {service_ref} ({elapsed_ms:.0f}ms)")
                record_zap_result(service_ref, True, elapsed_ms, wait_used, channel_name)
                return True
            log.warning(f"ZAP FAILED: {body[:100]}")
            record_zap_result(service_ref, False, elapsed_ms, wait_used, channel_name)
            return False
    except Exception as e:
        elapsed_ms = (time.time() - t0) * 1000
        log.error(f"ZAP ERROR: {e}")
        record_zap_result(service_ref, False, elapsed_ms, wait_used, channel_name)
        return False


# ── Channel Cache ─────────────────────────────────────────

channel_cache = {"channels": [], "last_update": 0}
channel_cache_lock = threading.Lock()

BOUQUETS = [
    "userbouquet.Free_TV.tv",
    "userbouquet.Free_HDTV_und_HDplus.tv",
    "userbouquet.Oesterreich_TV.tv",
    "userbouquet.Schweizer_TV.tv",
    "userbouquet.Sport_Sender.tv",
    "userbouquet.Kinder_Sender.tv",
    "userbouquet.Musik_Sender.tv",
    "userbouquet.UHD_TV.tv",
    "userbouquet.MTV.tv",
    "userbouquet.Terrestrisch.tv",
]

def fetch_channels_from_receiver():
    receivers = get_receivers()
    if not receivers:
        return []
    r = receivers[0]
    channels = []
    seen_refs = set()

    for bouquet in BOUQUETS:
        try:
            bref = f'1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "{bouquet}" ORDER BY bouquet'
            encoded = urllib.parse.quote(bref)
            url = f"http://{r['ip']}:{r['port']}/web/services.m3u?bRef={encoded}"
            with urllib.request.urlopen(url, timeout=10) as resp:
                content = resp.read().decode("utf-8", errors="replace")

            lines = content.splitlines()
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if line.startswith("#EXTINF"):
                    name_match = re.search(r',(.+)$', line)
                    name = name_match.group(1).strip() if name_match else "Unbekannt"
                    j = i + 1
                    while j < len(lines) and lines[j].strip().startswith("#"):
                        j += 1
                    if j < len(lines):
                        url_line = lines[j].strip()
                        ref_match = re.search(r':8001/(.+)$', url_line)
                        if ref_match:
                            ref = ref_match.group(1).rstrip('/')
                            if ref not in seen_refs and name and not name.startswith('.'):
                                seen_refs.add(ref)
                                channels.append({
                                    "name": name,
                                    "ref": ref,
                                    "bouquet": bouquet.replace("userbouquet.", "").replace(".tv", "")
                                })
                    i = j + 1
                    continue
                i += 1
        except Exception as e:
            log.warning(f"Bouquet {bouquet} Fehler: {e}")

    log.info(f"Channel list: {len(channels)} channels loaded")
    return channels


def get_channels(force_refresh=False):
    with channel_cache_lock:
        age = time.time() - channel_cache["last_update"]
        if force_refresh or age > 3600 or not channel_cache["channels"]:
            log.info("Updating channel list...")
            try:
                channels = fetch_channels_from_receiver()
                channel_cache["channels"] = channels
                channel_cache["last_update"] = time.time()
            except Exception as e:
                log.error(f"Senderliste Fehler: {e}")
        return channel_cache["channels"]


# ── Streaming ─────────────────────────────────────────────

CHUNK_SIZE = 65536

def write_chunked(wfile, data):
    """Schreibt Daten im HTTP/1.1 Chunked Transfer Encoding Format.
    Plex DVR Segmenter erwaitingt HTTP/1.1 mit chunked encoding —
    ohne das bricht er den Stream sofort ab (Stopping idle session).
    """
    if not data:
        return
    size = len(data)
    wfile.write(f"{size:X}\r\n".encode("ascii"))
    wfile.write(data)
    wfile.write(b"\r\n")
    wfile.flush()


def stream_passthrough(rid, service_ref, wfile, use_chunked=False):
    r = get_receiver_by_id(rid)
    encoded_ref = urllib.parse.quote(service_ref, safe=":@")
    request = (
        f"GET /{encoded_ref} HTTP/1.0\r\n"
        f"Host: {r['ip']}:{r['stream_port']}\r\n"
        f"User-Agent: E2Proxy/2.0\r\n"
        f"Connection: close\r\n\r\n"
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect((r['ip'], r['stream_port']))
    sock.sendall(request.encode())
    header_data = b""
    while b"\r\n\r\n" not in header_data:
        chunk = sock.recv(1)
        if not chunk:
            break
        header_data += chunk
    sock.settimeout(30)
    bytes_sent = 0
    try:
        while True:
            chunk = sock.recv(CHUNK_SIZE)
            if not chunk:
                break
            if use_chunked:
                write_chunked(wfile, chunk)
            else:
                wfile.write(chunk)
                wfile.flush()
            bytes_sent += len(chunk)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        sock.close()
    return bytes_sent


def build_ffmpeg_cmd(rid, service_ref, tp, probesize=None, analyzeduration=None, low_latency=False):
    """Baut ffmpeg Kommando basierend auf Transcode-Profil.

    probesize/analyzeduration: optionale Overrides (Umschalt-Tuning). low_latency
    setzt zusätzlich Input-Flags für minimales Buffering (schnelleres Umschalten)."""
    r = get_receiver_by_id(rid)
    encoded_ref = urllib.parse.quote(service_ref, safe=":@")
    input_url = f"http://{r['ip']}:{r['stream_port']}/{encoded_ref}"

    codec = tp.get("codec", "h264")
    container = tp.get("container", "mpegts")
    vbitrate = tp.get("vbitrate", "2800k")
    abitrate = tp.get("abitrate", "128k")
    height = tp.get("height", 720)
    preset = tp.get("preset", "superfast")

    ps = str(int(probesize)) if probesize else "15000000"
    ad = str(int(analyzeduration)) if analyzeduration else "15000000"
    fflags = "+discardcorrupt+genpts+nobuffer" if low_latency else "+discardcorrupt+genpts"

    base = [
        "ffmpeg", "-loglevel", "warning",
        # Stalled Receiver darf ffmpeg nicht ewig blockieren: nach 20s ohne Daten
        # bricht der HTTP-Input ab → Prozess endet → Tuner/ref_lock werden freigegeben.
        "-rw_timeout", "20000000",
        "-fflags", fflags,
        "-err_detect", "ignore_err",
    ]
    if low_latency:
        base += ["-flags", "low_delay"]
    base += [
        # Probe-Fenster: manche HD-Sender (z.B. VOX HD) liefern die Stream-Parameter
        # erst spät. Zu kleine Werte → ffmpeg erkennt Video/Audio nicht
        # ("unspecified size / unknown codec") und schreibt 0 Bytes. Im NoLatency-Modus
        # bewusst klein für schnelles Umschalten; der Store lernt bei Fehlern hoch.
        "-probesize", ps, "-analyzeduration", ad,
        "-i", input_url,
        "-map", "0:v:0", "-map", "0:a:0",
    ]

    if codec == "remux":
        # Video copy, Audio MP2→AAC — für Jellyfin Direct Stream
        video_args = ["-c:v", "copy"]
        audio_args = [
            "-c:a", "aac",
            "-b:a", abitrate,
            "-ac", "2",
            "-ar", "48000",
        ]
        fmt = ["-f", "mpegts"]

    elif codec == "remux-ac3":
        # Video copy, Audio MP2->AC3 - fuer Plex Direct Stream
        # AC3 hat explizite Channel-Metadaten, loest Plex "sample rate not set" Problem
        video_args = ["-c:v", "copy"]
        audio_args = [
            "-c:a", "ac3",
            "-b:a", abitrate,
            "-ac", "2",
            "-ar", "48000",
        ]
        fmt = ["-f", "mpegts"]

    elif codec == "h264":
        video_args = [
            "-c:v", "libx264",
            "-preset", preset,
            "-tune", "zerolatency",
            "-b:v", vbitrate,
            "-maxrate", vbitrate,
            "-bufsize", str(int(vbitrate.replace("k","")) * 2) + "k",
            "-g", "50",
            "-keyint_min", "25",
        ]
        audio_args = [
            "-c:a", "aac",
            "-b:a", abitrate,
            "-ac", "2",
            "-ar", "48000",
        ]
        fmt = ["-f", "mpegts"]

    elif codec == "vp8":
        video_args = [
            "-vf", f"scale=-2:{height}",
            "-c:v", "libvpx",
            "-b:v", vbitrate,
            "-maxrate", vbitrate,
            "-bufsize", str(int(vbitrate.replace("k","")) * 2) + "k",
            "-cpu-used", "8",
            "-deadline", "realtime",
            "-vsync", "cfr",
        ]
        audio_args = [
            "-c:a", "libvorbis",
            "-b:a", abitrate,
            "-ac", "2",
            "-async", "1",
        ]
        fmt = ["-f", "webm"]

    else:
        raise ValueError(f"Unbekannter Codec: {codec}")

    content_type = "video/webm" if container == "webm" else "video/mp2t"
    return base + video_args + audio_args + fmt + ["-"], content_type


def stream_transcoded(rid, service_ref, tp, wfile, use_chunked=False, channel_name=None):
    label = tp.get("label", "?")
    sw = get_switch_settings(service_ref)
    monitor_sec = max(1.0, sw["monitor_sec"])
    max_attempts = 1 + max(0, sw["max_retries"])
    probesize = sw["probesize"]
    analyzeduration = sw["analyzeduration"]
    no_latency = sw["no_latency"]

    proc = None
    bytes_sent = 0
    attempts = 0
    # content_type ist probe-unabhängig; sicherer Default bis erster Build
    _, content_type = build_ffmpeg_cmd(rid, service_ref, tp)

    def read_stderr(pipe, sink):
        try:
            for line in pipe:
                l = line.decode("utf-8", errors="replace").strip()
                if l:
                    sink.append(l)
                    if any(kw in l.lower() for kw in ["scrambled", "conditional access"]):
                        log.warning(f"SCRAMBLED: {l}")
                    else:
                        log.debug(f"ffmpeg: {l}")
        except Exception:
            pass

    def _kill(p):
        try:
            p.terminate()
            p.wait(timeout=3)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

    try:
        # ── Phase 1: ffmpeg starten und Fehlstart erkennen ───────────────
        # Ziel: einen *fehlgeschlagenen* Start (Prozess stirbt / liefert nichts)
        # erkennen und transparent mit größerem Probesize neu starten — solange
        # noch nichts an den Client ging. Ein noch LAUFENDES ffmpeg, das nur
        # langsam probed (großer Probesize bei hoher Bitrate), wird NICHT gekillt,
        # sonst würde der Stream nie starten.
        first_chunk = None
        while attempts < max_attempts:
            attempts += 1
            cmd, content_type = build_ffmpeg_cmd(
                rid, service_ref, tp,
                probesize=probesize, analyzeduration=analyzeduration,
                low_latency=no_latency,
            )
            log.info(f"ffmpeg START (try {attempts}/{max_attempts}, probe={probesize}): "
                     f"{service_ref} [{label}]")
            stderr_lines = []
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            with stream_processes_lock:
                stream_processes[rid] = proc
            t = threading.Thread(target=read_stderr, args=(proc.stderr, stderr_lines), daemon=True)
            t.start()

            # Monitor-Fenster: auf erste Nutzdaten oder frühen Prozess-Tod warten
            deadline = time.time() + monitor_sec
            while time.time() < deadline:
                if proc.poll() is not None:
                    break  # ffmpeg vorzeitig beendet (Fehlstart)
                remaining = max(0.0, deadline - time.time())
                rlist, _, _ = select.select([proc.stdout], [], [], min(0.5, remaining))
                if rlist:
                    c = proc.stdout.read(CHUNK_SIZE)
                    if c:
                        first_chunk = c
                        break
                    break  # EOF ohne Daten → Prozess terminiert gleich

            if first_chunk is not None:
                break  # Erfolg: Daten fließen
            if proc.poll() is None:
                # Prozess lebt noch, probed nur langsam → beibehalten und unten
                # per blockierendem Read auf die ersten Daten warten.
                break

            # Prozess ist ohne Daten gestorben → echter Fehlstart
            t.join(timeout=1)
            scrambled = any(
                any(kw in l.lower() for kw in ["scrambled", "conditional access", "not authorized"])
                for l in stderr_lines
            )
            _kill(proc)
            with stream_processes_lock:
                stream_processes.pop(rid, None)
            proc = None
            if scrambled:
                record_stream_start(service_ref, False, attempts, no_latency, channel_name)
                raise ScrambledStreamError("Channel is scrambled")
            if attempts < max_attempts:
                probesize = min(_PROBE_MAX, probesize * 2)
                analyzeduration = min(_PROBE_MAX, max(analyzeduration, probesize))
                log.warning(f"Switch: ffmpeg-Fehlstart (try {attempts}) — "
                            f"Neustart mit Probesize {probesize}")

        if proc is None:
            # Alle Versuche gestorben
            record_stream_start(service_ref, False, attempts, no_latency, channel_name)
            raise ScrambledStreamError("Stream delivered no data (scrambled or unavailable)")

        # Falls Prozess lebt aber noch keine Daten kamen (langsames Probing):
        # blockierend auf den ersten Chunk warten (rw_timeout begrenzt das ffmpeg-seitig).
        if first_chunk is None:
            first_chunk = proc.stdout.read(CHUNK_SIZE)
        if not first_chunk:
            record_stream_start(service_ref, False, attempts, no_latency, channel_name)
            raise ScrambledStreamError("Stream delivered no data (scrambled or unavailable)")

        # ── Phase 2: Daten erhalten → Erfolg werten & normal streamen ────────
        record_stream_start(service_ref, True, attempts, no_latency, channel_name)
        chunk = first_chunk
        while chunk:
            if use_chunked:
                write_chunked(wfile, chunk)
            else:
                wfile.write(chunk)
                wfile.flush()
            bytes_sent += len(chunk)
            chunk = proc.stdout.read(CHUNK_SIZE)

    except ScrambledStreamError:
        raise
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        with stream_processes_lock:
            stream_processes.pop(rid, None)
        if proc:
            _kill(proc)

    log.info(f"ffmpeg END: {service_ref} — {bytes_sent/1024/1024:.1f} MB")
    return bytes_sent, content_type



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
    if _receiver_state.get(rid) is not None:
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


# ── TMDB Artwork Cache ─────────────────────────────────────────────────────
TMDB_CACHE_FILE = f"{DATA_DIR}/tmdb_cache.json"

# ── TVDB v4 Integration ────────────────────────────────────────────────────────
TVDB_CACHE_FILE = f"{DATA_DIR}/tvdb_cache.json"
_tvdb_cache = None
_tvdb_cache_lock = threading.Lock()
_tvdb_token = [None]   # (token, expires_at_unix)
_tvdb_token_lock = threading.Lock()

TVDB_CACHE_TTL_SERIES   = 30 * 86400
TVDB_CACHE_TTL_EPISODE  = 30 * 86400
TVDB_CACHE_TTL_EMPTY    = 7 * 86400

def _load_tvdb_cache():
    global _tvdb_cache
    if _tvdb_cache is not None:
        return _tvdb_cache
    try:
        if os.path.exists(TVDB_CACHE_FILE):
            with open(TVDB_CACHE_FILE) as f:
                _tvdb_cache = json.load(f)
        else:
            _tvdb_cache = {}
    except Exception:
        _tvdb_cache = {}
    return _tvdb_cache

def _save_tvdb_cache():
    try:
        os.makedirs(os.path.dirname(TVDB_CACHE_FILE), exist_ok=True)
        with open(TVDB_CACHE_FILE, "w") as f:
            json.dump(_tvdb_cache, f, ensure_ascii=False)
    except Exception as e:
        log.debug(f"TVDB Cache speichern: {e}")

def _tvdb_login():
    """Holt ein TVDB Bearer-Token (Cache 1 Monat). Gibt Token oder None zurück."""
    with _tvdb_token_lock:
        tok, exp = _tvdb_token[0] if _tvdb_token[0] else (None, 0)
        if tok and time.time() < exp:
            return tok
        cfg = get_config()
        api_key = cfg.get("tvdb_api_key", "").strip()
        if not api_key:
            return None
        try:
            payload = json.dumps({"apikey": api_key}).encode()
            req = urllib.request.Request(
                "https://api4.thetvdb.com/v4/login",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            token = data.get("data", {}).get("token", "")
            if token:
                _tvdb_token[0] = (token, time.time() + 25 * 86400)  # ~25 Tage
                log.info("TVDB: Login erfolgreich")
                return token
        except Exception as e:
            log.debug(f"TVDB Login: {e}")
        return None

def _tvdb_api(path, params=None):
    """Generischer TVDB API Call mit Auth-Header."""
    tok = _tvdb_login()
    if not tok:
        return None
    url = f"https://api4.thetvdb.com/v4{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log.debug(f"TVDB API {path}: {e}")
        return None

def tvdb_find_series(title):
    """Sucht eine Serie auf TVDB. Cached. Gibt {id, name, year} oder None zurück."""
    cache = _load_tvdb_cache()
    key = f"series:{title.lower().strip()}"
    entry = cache.get(key)
    if entry and (time.time() - entry.get("ts", 0)) < (TVDB_CACHE_TTL_SERIES if entry.get("data") else TVDB_CACHE_TTL_EMPTY):
        return entry.get("data")
    result = _tvdb_api("/search", {"query": title, "type": "series", "language": "deu"})
    series_data = None
    if result and result.get("data"):
        hit = result["data"][0]
        series_data = {
            "id":     hit.get("tvdb_id") or hit.get("id"),
            "name":   hit.get("name") or hit.get("translations", {}).get("deu", title),
            "year":   hit.get("year"),
            "poster": hit.get("image_url", ""),
            "overview": (hit.get("overviews", {}) or {}).get("deu", "") or hit.get("overview", ""),
        }
    with _tvdb_cache_lock:
        cache[key] = {"data": series_data, "ts": time.time()}
        _save_tvdb_cache()
    return series_data

def tvdb_find_episode(series_id, episode_title, air_date=None):
    """Sucht eine Episode in einer Serie via Titel-Match. Gibt {season, episode, name, overview} oder None."""
    cache = _load_tvdb_cache()
    key = f"ep:{series_id}:{(episode_title or '').lower().strip()}"
    entry = cache.get(key)
    if entry and (time.time() - entry.get("ts", 0)) < (TVDB_CACHE_TTL_EPISODE if entry.get("data") else TVDB_CACHE_TTL_EMPTY):
        return entry.get("data")
    # Episoden mit Pagination holen
    result = _tvdb_api(f"/series/{series_id}/episodes/default/deu")
    ep_data = None
    if result and result.get("data"):
        episodes = result["data"].get("episodes", [])
        # Match by exact title (case-insensitive)
        title_lower = (episode_title or "").lower().strip()
        for ep in episodes:
            name = (ep.get("name") or "").lower().strip()
            if name and (name == title_lower or title_lower in name or name in title_lower):
                ep_data = {
                    "season":   ep.get("seasonNumber"),
                    "episode":  ep.get("number"),
                    "name":     ep.get("name"),
                    "overview": ep.get("overview", ""),
                    "aired":    ep.get("aired", ""),
                }
                break
    with _tvdb_cache_lock:
        cache[key] = {"data": ep_data, "ts": time.time()}
        _save_tvdb_cache()
    return ep_data

# ── Metadaten-Anreicherung & Klassifikation ────────────────────────────────────
def classify_recording(title, episode_title=None, force_movie=False, force_series=False, season_override=None, episode_override=None, year_override=None):
    """Klassifiziert eine Aufnahme als Film oder Serie.
    
    Returns: dict mit {
        "kind": "movie" | "series",
        "series_id": tvdb-id falls bekannt,
        "season": int,
        "episode": int,
        "episode_title": str,
        "synthetic": bool,   # True wenn S/E künstlich (Daily Show)
    }
    """
    if force_movie:
        info = tmdb_get_movie_info(title)
        if info.get("year"):
            year, year_source = info["year"], "tmdb"
        elif year_override:
            year, year_source = int(year_override), "epg"
        else:
            year, year_source = datetime.now().year, "recording"
        return {
            "kind": "movie",
            "year": year,
            "year_source": year_source,          # tmdb | epg | recording
            "tmdb_found": bool(info.get("found")),
            "tmdb_poster": info.get("poster", ""),
            "tmdb_overview": info.get("overview", ""),
            "tmdb_title": info.get("title", ""),
        }
    
    # Caller-supplied S/E has priority over TVDB lookup
    if season_override is not None and episode_override is not None:
        return {
            "kind": "series",
            "season": int(season_override),
            "episode": int(episode_override),
            "episode_title": episode_title or "",
            "synthetic": False,
        }
    
    result = {"kind": "series", "synthetic": True}
    
    # 1. TVDB-Lookup: ist es eine bekannte Serie?
    series = tvdb_find_series(title)
    if series:
        result["series_id"] = series["id"]
        result["series_name"] = series["name"]
        result["series_poster"] = series.get("poster", "")
        result["series_overview"] = series.get("overview", "")
        
        # 2. Episode finden via Titel-Match
        if episode_title:
            ep = tvdb_find_episode(series["id"], episode_title)
            if ep and ep.get("season") and ep.get("episode"):
                result["season"]  = ep["season"]
                result["episode"] = ep["episode"]
                result["episode_title"] = ep.get("name", episode_title)
                result["episode_overview"] = ep.get("overview", "")
                result["synthetic"] = False
                return result
    
    # 3. Fallback: künstliche S/E aus Datum (Tag im Jahr)
    today = datetime.now()
    result["season"]  = today.year
    result["episode"] = today.timetuple().tm_yday
    result["episode_title"] = episode_title or today.strftime("%Y-%m-%d")
    return result

def verify_recording_metadata(title, description, classification):
    """Prüft vor der Aufnahme via TMDB, ob die Metadaten vollständig sind und
    ob die EPG-Informationen ausreichen. Ergebnis wird geloggt und als dict
    zurückgegeben (blockiert die Aufnahme nicht).
    """
    result = {
        "kind": classification.get("kind"),
        "tmdb_found": classification.get("tmdb_found", False),
        "year_source": classification.get("year_source"),
        "has_description": bool(description and description.strip()),
        "warnings": [],
    }
    kind = classification.get("kind")

    if kind == "movie":
        if not classification.get("tmdb_found"):
            result["warnings"].append("TMDB: kein Treffer für Filmtitel")
        if classification.get("year_source") == "recording":
            result["warnings"].append("Jahr: nur Aufnahmejahr (kein TMDB/EPG)")
        if not classification.get("tmdb_poster") and not classification.get("tmdb_overview"):
            result["warnings"].append("TMDB: kein Poster/keine Beschreibung")
    else:
        if classification.get("synthetic"):
            result["warnings"].append("Serie: keine TVDB-Episode (Datum-Nummerierung)")

    if not result["has_description"]:
        result["warnings"].append("EPG: keine Beschreibung vorhanden")

    if result["warnings"]:
        log.warning(f"Metadaten-Check '{title}': " + "; ".join(result["warnings"]))
    else:
        log.info(f"Metadaten-Check '{title}': vollständig (Quelle Jahr: {result['year_source']})")
    return result


def sanitize_filename(name):
    """Entfernt Sonderzeichen aus Dateinamen."""
    import re
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name or "")
    name = re.sub(r"\s+", " ", name).strip()
    return name[:120]

def build_recording_path(base_dir, title, classification, ext="ts"):
    """Baut Plex-konformen Pfad für eine Aufnahme.
    
    Movies/<Title> (<Year>)/<Title> (<Year>).ts
    TV/<Series>/Season XX/<Series> - SXXEXX - <Episode>.ts
    """
    safe_title = sanitize_filename(title)
    kind = classification.get("kind", "series")
    
    if kind == "movie":
        year = classification.get("year")
        movies_dir = os.path.join(base_dir, "Movies")
        plain_folder = safe_title
        year_folder = f"{safe_title} ({year})" if year else safe_title
        plain_dir = os.path.join(movies_dir, plain_folder)
        year_dir = os.path.join(movies_dir, year_folder)

        # Standard: ohne Jahr. Jahr nur zur Auflösung von Duplikaten auf dem
        # Dateisystem verwenden (z. B. Remakes mit identischem Titel).
        if year and os.path.isdir(year_dir):
            folder = year_folder           # bereits jahres-disambiguiert vorhanden
        elif os.path.isdir(plain_dir) and year:
            folder = year_folder           # jahresloser Ordner belegt → Jahr anhängen
        else:
            folder = plain_folder          # frei → ohne Jahr
        return os.path.join(movies_dir, folder, f"{folder}.{ext}")
    
    season = classification.get("season", 1)
    episode = classification.get("episode", 1)
    ep_title = sanitize_filename(classification.get("episode_title", ""))
    
    season_folder = f"Season {season:02d}" if season < 1000 else f"Season {season}"
    season_padding = "%02d" if season < 100 else "%04d"
    ep_padding = "%02d" if episode < 100 else "%03d"
    se_code = f"S{season_padding % season}E{ep_padding % episode}"
    
    fname = f"{safe_title} - {se_code}"
    if ep_title and ep_title != safe_title:
        fname += f" - {ep_title}"
    fname += f".{ext}"
    
    return os.path.join(base_dir, "TV", safe_title, season_folder, fname)

def build_nfo(filepath, title, classification, description="", image_url="", duration_sec=0):
    """Erzeugt Plex/Kodi-kompatibles NFO neben der Aufnahme-Datei."""
    kind = classification.get("kind", "series")
    today = datetime.now().strftime("%Y-%m-%d")
    
    def x(s):
        return (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    
    if kind == "movie":
        movie_year = classification.get("year") or datetime.now().year
        movie_overview = description or classification.get("tmdb_overview", "")
        movie_poster = image_url or classification.get("tmdb_poster", "")
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<movie>
  <title>{x(title)}</title>
  <plot>{x(movie_overview)}</plot>
  <year>{movie_year}</year>
  <premiered>{today}</premiered>
  <studio>e2proxy Recording</studio>
  <runtime>{int(duration_sec // 60)}</runtime>
  <thumb aspect="poster">{x(movie_poster)}</thumb>
  <fileinfo><streamdetails><video><durationinseconds>{int(duration_sec)}</durationinseconds></video></streamdetails></fileinfo>
</movie>"""
    else:
        # Episode-NFO
        season  = classification.get("season", 1)
        episode = classification.get("episode", 1)
        ep_title = classification.get("episode_title", "")
        ep_overview = classification.get("episode_overview", description)
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<episodedetails>
  <title>{x(ep_title)}</title>
  <showtitle>{x(title)}</showtitle>
  <season>{season}</season>
  <episode>{episode}</episode>
  <plot>{x(ep_overview)}</plot>
  <aired>{today}</aired>
  <premiered>{today}</premiered>
  <studio>e2proxy Recording</studio>
  <runtime>{int(duration_sec // 60)}</runtime>
  <thumb aspect="thumb">{x(image_url)}</thumb>
  <fileinfo><streamdetails><video><durationinseconds>{int(duration_sec)}</durationinseconds></video></streamdetails></fileinfo>
</episodedetails>"""
    
    nfo_path = filepath.rsplit(".", 1)[0] + ".nfo"
    try:
        os.makedirs(os.path.dirname(nfo_path), exist_ok=True)
        with open(nfo_path, "w", encoding="utf-8") as f:
            f.write(xml)
    except Exception as e:
        log.warning(f"NFO schreiben fehlgeschlagen: {e}")
    
    # Für Serien zusätzlich tvshow.nfo im Show-Ordner
    if kind == "series":
        show_dir = os.path.dirname(os.path.dirname(nfo_path))
        show_nfo = os.path.join(show_dir, "tvshow.nfo")
        if not os.path.exists(show_nfo):
            try:
                series_overview = classification.get("series_overview", "")
                series_poster = classification.get("series_poster", image_url)
                show_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<tvshow>
  <title>{x(title)}</title>
  <plot>{x(series_overview)}</plot>
  <thumb aspect="poster">{x(series_poster)}</thumb>
  <studio>e2proxy Recording</studio>
</tvshow>"""
                with open(show_nfo, "w", encoding="utf-8") as f:
                    f.write(show_xml)
            except Exception as e:
                log.debug(f"tvshow.nfo: {e}")


_tmdb_cache = None
_tmdb_cache_lock = threading.Lock()

TMDB_CACHE_TTL_FOUND = 30 * 86400   # 30 Tage für gefundene Poster
TMDB_CACHE_TTL_EMPTY = 7  * 86400   # 7 Tage für nicht gefundene

def _load_tmdb_cache():
    global _tmdb_cache
    if _tmdb_cache is not None:
        return _tmdb_cache
    try:
        if os.path.exists(TMDB_CACHE_FILE):
            with open(TMDB_CACHE_FILE) as f:
                _tmdb_cache = json.load(f)
        else:
            _tmdb_cache = {}
    except Exception:
        _tmdb_cache = {}
    return _tmdb_cache

def _tmdb_cache_fresh(entry):
    """Prüft ob ein Cache-Eintrag noch frisch ist."""
    if not isinstance(entry, dict):
        return False  # altes Format → veraltet
    url = entry.get("url", "")
    ts  = entry.get("ts", 0)
    ttl = TMDB_CACHE_TTL_FOUND if url else TMDB_CACHE_TTL_EMPTY
    return (time.time() - ts) < ttl

def _save_tmdb_cache():
    try:
        os.makedirs(os.path.dirname(TMDB_CACHE_FILE), exist_ok=True)
        with open(TMDB_CACHE_FILE, "w") as f:
            json.dump(_tmdb_cache, f, ensure_ascii=False)
    except Exception as e:
        log.warning(f"TMDB Cache speichern fehlgeschlagen: {e}")

def tmdb_get_poster(title, is_series=False):
    """Sucht Poster-URL für Titel via TMDB API. Gibt URL oder '' zurück."""
    cfg = get_config()
    api_key = cfg.get("tmdb_api_key", "").strip()
    if not api_key:
        return ""
    cache = _load_tmdb_cache()
    cache_key = f"{'s' if is_series else 'm'}:{title.lower().strip()}"
    # Cache prüfen — nur wenn noch frisch
    if cache_key in cache and _tmdb_cache_fresh(cache[cache_key]):
        return cache[cache_key].get("url", "")
    try:
        import urllib.request as _ur
        # Für Serien zuerst TV suchen, dann Movie als Fallback
        for media_type in (["tv", "movie"] if is_series else ["movie", "tv"]):
            url = (f"https://api.themoviedb.org/3/search/{media_type}"
                   f"?api_key={api_key}&query={urllib.parse.quote(title)}&language=de-DE")
            req = _ur.Request(url, headers={"User-Agent": "e2proxy/1.0"})
            with _ur.urlopen(req, timeout=5) as r:
                data = json.loads(r.read())
            results = data.get("results", [])
            if results and results[0].get("poster_path"):
                    # Ähnlichkeits-Check: Titel müssen ähnlich genug sein
                    import difflib
                    found_title = results[0].get("name") or results[0].get("title", "")
                    similarity = difflib.SequenceMatcher(
                        None, title.lower(), found_title.lower()
                    ).ratio()
                    if similarity < 0.45:
                        log.debug(f"TMDB: '{title}' → '{found_title}' zu unähnlich ({similarity:.2f}), übersprungen")
                        continue
                    poster_url = f"https://image.tmdb.org/t/p/w300{results[0]['poster_path']}"
                    with _tmdb_cache_lock:
                        _tmdb_cache[cache_key] = {"url": poster_url, "ts": time.time(), "found": found_title}
                    log.debug(f"TMDB: '{title}' → '{found_title}' ({similarity:.2f}) {poster_url[-40:]}")
                    return poster_url
        # Kein Treffer — mit leerem Eintrag + Timestamp cachen (7 Tage TTL)
        with _tmdb_cache_lock:
            _tmdb_cache[cache_key] = {"url": "", "ts": time.time()}
        return ""
    except Exception as e:
        log.debug(f"TMDB Fehler für '{title}': {e}")
        return ""


def tmdb_get_movie_info(title):
    """Holt umfassende Film-Metadaten von TMDB.

    Returns dict: {
        "found": bool,        # ob ein passender Treffer gefunden wurde
        "year": int|None,     # Erscheinungsjahr (release_date)
        "poster": str,        # Poster-URL ('' wenn keine)
        "overview": str,      # Beschreibung ('' wenn keine)
        "title": str,         # gefundener TMDB-Titel
    }
    Ergebnisse werden im TMDB-Cache gehalten (Key-Präfix 'mi:').
    """
    empty = {"found": False, "year": None, "poster": "", "overview": "", "title": ""}
    cfg = get_config()
    api_key = cfg.get("tmdb_api_key", "").strip()
    if not api_key or not title:
        return empty

    cache = _load_tmdb_cache()
    cache_key = f"mi:{title.lower().strip()}"
    entry = cache.get(cache_key)
    if entry and _tmdb_cache_fresh_info(entry):
        return {
            "found": bool(entry.get("found")),
            "year": entry.get("year"),
            "poster": entry.get("poster", ""),
            "overview": entry.get("overview", ""),
            "title": entry.get("title", ""),
        }

    try:
        import urllib.request as _ur
        url = (f"https://api.themoviedb.org/3/search/movie"
               f"?api_key={api_key}&query={urllib.parse.quote(title)}&language=de-DE")
        req = _ur.Request(url, headers={"User-Agent": "e2proxy/1.0"})
        with _ur.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        results = data.get("results", [])
        if results:
            import difflib
            best = results[0]
            found_title = best.get("title") or best.get("name", "")
            similarity = difflib.SequenceMatcher(
                None, title.lower(), found_title.lower()
            ).ratio()
            if similarity >= 0.45:
                release = best.get("release_date", "") or ""
                year = None
                if len(release) >= 4 and release[:4].isdigit():
                    year = int(release[:4])
                poster = (f"https://image.tmdb.org/t/p/w300{best['poster_path']}"
                          if best.get("poster_path") else "")
                info = {
                    "found": True,
                    "year": year,
                    "poster": poster,
                    "overview": best.get("overview", "") or "",
                    "title": found_title,
                }
                with _tmdb_cache_lock:
                    _tmdb_cache[cache_key] = {**info, "ts": time.time()}
                log.debug(f"TMDB Film-Info: '{title}' → '{found_title}' ({year}) sim={similarity:.2f}")
                return info
        with _tmdb_cache_lock:
            _tmdb_cache[cache_key] = {**empty, "ts": time.time()}
        return empty
    except Exception as e:
        log.debug(f"TMDB Film-Info Fehler für '{title}': {e}")
        return empty


def _tmdb_cache_fresh_info(entry):
    """Frische-Prüfung für 'mi:'-Film-Info-Einträge."""
    if not isinstance(entry, dict):
        return False
    ts = entry.get("ts", 0)
    ttl = TMDB_CACHE_TTL_FOUND if entry.get("found") else TMDB_CACHE_TTL_EMPTY
    return (time.time() - ts) < ttl


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

# ── M3U Builder ───────────────────────────────────────────

def build_m3u(channels, profile_name, favorites_only=False):
    cfg = get_config()
    proxy_host = cfg.get("proxy_host", "127.0.0.1")
    proxy_port = int(cfg.get("proxy_port", 8888))

    dp = cfg.get("device_profiles", {}).get(profile_name, {})
    tp_name = dp.get("transcode_profile", "webm-sd")
    tp = cfg.get("transcode_profiles", {}).get(tp_name, {})
    label = tp.get("label", tp_name)

    lines = [f'#EXTM3U x-tvg-url="http://{proxy_host}:{proxy_port}/epg.xml"']

    if favorites_only:
        favs = load_favorites()
        fav_map = {f["ref"]: f for f in favs}
        ch_map = {ch["ref"]: ch for ch in channels}
        ch_list = [(i+1, ch_map[f["ref"]], f) for i, f in enumerate(favs) if f["ref"] in ch_map]
    else:
        ch_list = [(i+1, ch, {}) for i, ch in enumerate(channels)]

    for chno, ch, fav in ch_list:
        ref_enc = urllib.parse.quote(ch["ref"])
        name_enc = urllib.parse.quote(ch["name"])
        name = ch["name"]
        # Gruppe: custom group aus Favoriten > Bouquet vom Receiver
        group = fav.get("group", "") or ch.get("bouquet", "TV")
        # Logo aus Datenbank
        logo = get_channel_logo(name)
        # tvg-id muss exakt der channel id im XMLTV entsprechen
        tvg_id = ch["ref"].rstrip("/").replace(":", "_")
        stream_url = (f'http://{proxy_host}:{proxy_port}/stream'
                      f'?ref={ref_enc}&profile={urllib.parse.quote(profile_name)}&name={name_enc}')
        logo_attr = f' tvg-logo="{logo}"' if logo else ''
        lines.append(
            f'#EXTINF:-1 tvg-chno="{chno}" tvg-id="{tvg_id}" tvg-name="{name}"{logo_attr} group-title="{group}",{name}'
        )
        lines.append(f'#KODIPROP:inputstream.ffmpegdirect.is_realtime_stream=true')
        lines.append(f'#KODIPROP:inputstream=inputstream.ffmpegdirect')
        lines.append(f'#KODIPROP:mimetype=video/mp2t')
        lines.append(stream_url)

    return "\n".join(lines)


# ── HTML Templates ────────────────────────────────────────

# ── i18n: Übersetzungs-Dictionary + Client-Engine ──────────────────────────────
# Strings werden via data-i18n="key" Attribut im HTML markiert und client-seitig
# ersetzt. Default-Sprache: Englisch (mit Browser-Erkennung als Fallback).
I18N_JS = r"""
<script>
const I18N = {
  en: {
    // Navigation & allgemein
    "nav.home": "Home", "nav.back": "← Home", "nav.settings": "Settings",
    "nav.favorites": "Favorites", "nav.epg": "EPG Browser", "nav.help": "Help",
    "nav.mainpage": "← Main page", "nav.save": "Save", "nav.m3u_fav": "↓ M3U Favorites",
    "common.save": "Save", "common.cancel": "Cancel", "common.delete": "Delete",
    "common.add": "Add", "common.close": "Close", "common.refresh": "Refresh",
    "common.loading": "Loading…", "common.search": "Search…", "common.enabled": "Enabled",
    "common.disabled": "Disabled", "common.yes": "Yes", "common.no": "No",
    "common.channels": "channels", "common.none": "— none —",
    // Hauptseite
    "main.live_tv": "Live TV", "main.all_channels": "All Channels",
    "main.tuner_status": "Tuner Status", "main.free": "free", "main.busy": "busy",
    "main.quick_record": "Quick Record", "main.duration_min": "Duration (min)",
    "main.record": "Record", "main.stop": "Stop", "main.running": "Running",
    "main.play": "Play", "main.no_channels": "No channels found",
    "main.status": "Status:", "main.select_channel": "Select a channel…",
    "main.pick_from_list": "Pick a channel from the list",
    "main.fullscreen": "⤢ Fullscreen",
    "main.connecting": "Connecting…", "main.device_profile": "Device profile",
    // EPG Browser
    "epg.title": "EPG Browser", "epg.today": "Today", "epg.tomorrow": "Tomorrow",
    "epg.now": "▶ Now", "epg.search": "Search…", "epg.no_desc": "(No description)",
    "epg.recording": "RECORDING", "epg.record_series": "📺 Record series",
    "epg.record_movie": "🎬 Record as movie", "epg.starting": "Starting recording…",
    "epg.rec_series_hint": "Series: TVDB lookup for real S/E, daily-show fallback (S2026E<i>day</i>)",
    "epg.rec_movie_hint": "Movie: Movies/<Title>/ (year added only for duplicates)",
    "epg.search_tmdb": "🎬 Search on TMDB →", "epg.search_tvdb": "📺 Search on TVDB →",
    // Favoriten
    "fav.title": "Favorites", "fav.all_channels": "ALL CHANNELS", "fav.group": "— Group —",
    "fav.category": "— Category —", "fav.added": "Added to favorites",
    "fav.saved": "Favorites saved", "fav.already": "Already a favorite",
    "fav.add_hint": "Add", "fav.export_m3u": "↓ M3U Favorites",
    // Settings — Tabs
    "set.title": "Settings", "set.tab_config": "Configuration", "set.tab_maint": "Maintenance",
    "set.tab_epg": "EPG", "set.tab_rec": "Recordings", "set.tab_api": "API",
    // Settings — Konfiguration
    "set.receivers": "Receivers", "set.add_receiver": "Add receiver",
    "set.transcode": "Transcode Profiles", "set.device_profiles": "Device Profiles",
    "set.api_keys": "API Keys & Tokens", "set.language": "Language",
    "set.lang_hint": "Interface language. Applies immediately.",
    "set.about": "About e2proxy", "set.general": "General",
    // Settings — Wartung
    "set.maintenance": "Maintenance", "set.update_logos": "🖼 Update logos",
    "set.live_logs": "Live Logs", "set.log_level": "Log Level",
    "set.api_access_log": "API Access Log", "set.load_more": "⬆ Load more",
    "set.live": "↺ Live", "set.clear": "🗑 Clear", "set.logo_check": "Logo Check",
    "set.log_retention": "Log Retention", "set.days": "days",
    // Settings — EPG
    "set.epg_sources": "EPG Sources", "set.epg_schedule": "EPG Schedule",
    "set.epg_run_now": "Run EPG now", "set.epg_status": "EPG Status",
    "set.epg_history": "EPG Run History",
    // Settings — Aufnahmen
    "set.rec_path": "Recording Path", "set.rec_profile": "Recording Profile",
    "set.max_duration": "Max Duration", "set.plex_integration": "Plex Integration",
    "set.plex_url": "Plex URL", "set.plex_token": "Plex Token", "set.plex_sections": "Plex Libraries",
    // Toast-Meldungen
    "toast.saved": "✓ Saved", "toast.error": "Error", "toast.deleted": "✓ Deleted",
    "toast.cleared": "✓ Cleared", "toast.lang_changed": "✓ Language changed",
    // Status
    "status.online": "Online", "status.offline": "Offline",
    "status.epg_updating": "EPG updating…",
    // Settings — Detail-Strings
    "set.rec_settings": "Recording Settings", "set.tuner_status": "Tuner Status",
    "set.switch_tuning": "Switch Tuning", "set.switch_stats": "Per-Channel Statistics",
    "set.switch_hint": "Speeds up channel switching (e.g. Plex). NoLatency starts ffmpeg with minimal probing; the zap wait is the pause after switching. Values act as global defaults — each channel is learned automatically (see table).",
    "set.switch_nolatency": "NoLatency (global)",
    "set.switch_nolatency_hint": "Start ffmpeg without heavy probing (faster, auto-raises on failures)",
    "set.switch_zapwait": "Zap wait (s)", "set.switch_monitor": "Monitor window (s)",
    "set.switch_retries": "Max restarts", "set.switch_nolat_probe": "NoLatency probesize",
    "set.switch_fail_thresh": "Fail threshold (probesize↑)", "set.switch_reset_all": "Reset all",
    "set.active_recordings": "Active Recordings", "set.quick_rec": "Quick Record",
    "set.quick_rec_hint": "Select channel → current program shown → start recording.",
    "set.start_rec": "▶ Start recording", "set.receivers_card": "Receivers",
    "set.transcode_card": "Transcode Profiles", "set.device_card": "Device Profiles",
    "set.api_keys_hint": "API keys for external services. Stored securely in the config.",
    "set.config_editor": "Config Editor", "set.advanced": "ADVANCED",
    "set.config_editor_hint": "Direct editing of the full configuration as JSON. For advanced settings.",
    "set.reset": "↺ Reset", "set.maint_actions": "Maintenance Actions",
    "set.maint_notify": "Maintenance Notifications",
    "set.maint_notify_hint": "Sends an HTTP call to another system at a scheduled time, e.g. to trigger maintenance tasks. Optionally only when idle (no recording running and nobody watching).",
    "set.maint_notify_enable": "Enable maintenance notifications",
    "set.maint_notify_url": "Target URL", "set.maint_notify_time": "Time",
    "set.maint_notify_days": "Weekdays", "set.maint_notify_idle": "Trigger condition",
    "set.maint_notify_idle_always": "Always at the scheduled time",
    "set.maint_notify_idle_only": "Only when idle (no recording / nobody watching)",
    "set.maint_notify_test": "Send test",
    "set.update_logos_card": "Update logos", "set.update_logos_hint": "Reloads all channel logos into the local cache.",
    "set.reload_channels": "Reload channel list", "set.reload_channels_hint": "Reloads all channels and bouquets from the receiver.",
    "set.restart_service": "Restart service", "set.restart_hint": "Restarts the e2proxy service. Active streams will be interrupted.",
    "set.epg_update": "EPG Update", "set.epg_run_now_btn": "▶ Update now",
    "set.schedule": "Schedule", "set.schedule_hint": "Time for the daily automatic EPG run. Should be before the Plex fetch (4-5 AM).",
    "set.last_run": "Last Run", "set.run_history": "Run History (last 30)",
    "set.run_history_hint": "Bars = duration in seconds. Red = outlier (>2× average).",
    "set.epg_run_log": "EPG Run Log", "set.logo_check_card": "Logo Check",
    "set.logo_check_hint": "Checks whether the favorite channel logos are reachable.",
    "set.fav_logos": "Favorite logos", "set.fav_logos_hint": "Set a custom logo for each favorite — upload an image or enter a URL. The image is converted to the correct format automatically. Reset falls back to the automatic logo.",
    "set.fav_logos_empty": "No favorites found. Add favorites first.", "set.fav_logos_custom": "custom",
    "set.fav_logos_url_ph": "Logo URL (https://…)", "set.fav_logos_save_url": "Save URL",
    "set.fav_logos_upload": "Upload", "set.fav_logos_done": "Logo saved",
    "set.fav_logos_working": "Working…", "set.fav_logos_need_url": "Please enter a URL",
    "set.fav_logos_too_big": "Image too large (max 8 MB)",
    "set.fav_logos_zoom": "Click to enlarge",
    "set.appearance": "Appearance", "set.appearance_hint": "Switch between dark and light theme. Stored in the browser.",
    "set.log_level_hint": "Controls which log entries are shown. Applies immediately without restart. The RAM buffer always keeps the last 500 entries.",
    "set.api_log_hint": "Logs all API requests persistently in",
    "set.api_ref": "API Reference", "set.update": "↺ Update",
    "set.timestamp": "Time", "set.duration": "Duration", "set.result": "Result",
    "set.get_token": "Get token", "set.api_log_persist": "Logs all API requests persistently in",
    "set.theme_dark": "🌙 Dark", "set.theme_light": "☀ Light",
    "set.connection_failed": "Connection failed",
    "rec.path": "Recording Path", "rec.default_profile": "Default Profile",
    "rec.max_duration": "Max Duration (Watchdog)", "rec.seconds": "seconds",
    "rec.plex_url": "Plex URL", "rec.plex_token": "Plex Token", "rec.plex_sections": "Plex Sections",
    "rec.via_login": "🔑 Generate via login", "rec.load_sections": "📁 Load sections",
    "rec.plex_verify": "Verify in Plex",
    "rec.plex_verify_hint": "After recording/conversion, check via Plex token whether the file was actually indexed (logs result)",
    "rec.select_channel": "— Select channel —", "rec.min": "min",
    "rec.no_active": "No active recordings.", "rec.duration_label": "Duration:",
    "epg.update_hint": "Fetches the program guide from both receivers, loads missing channels via zap and adds from the online source (Rytec). Runs daily automatically or manually.",
    "epg.progress": "Progress:", "epg.completed": "Completed", "epg.oclock": "h",
    "epg.jetzt_aktualisieren": "Update now",
    "set.timestamp": "Time", "set.duration": "Duration", "set.result": "Result",
    "set.get_token": "Get token",
    "help.live_tv": "Live TV Streaming", "help.api_overview": "API Overview",
    "help.recordings": "Recordings",
    "maint.execute": "Execute", "maint.restart_btn": "Restart",
    "epg.outlier": "Outlier", "epg.outliers": "Outliers",
    "log.tip": "Tip: For normal operation we recommend <b>INFO</b> or <b>WARNING</b> — DEBUG logs a lot (SSDP requests etc.)",
    "apilog.status_label": "active", "apilog.status_off": "inactive",
    "apilog.hint": "Logs all API requests persistently in",
    "apilog.activated": "API Logging activated", "apilog.deactivated": "API Logging deactivated",
    "apilog.clear_confirm": "Clear API log?",
    "apilog.no_entries": "No entries yet.",
    "rec.tuner_free": "free", "rec.tuner_busy": "busy",
    "help.live_tv_desc": "Select a channel on the left → stream starts in the browser. Profile (Web-SD / Web-HD) top right. Fullscreen button maximizes. Each stream uses one receiver — the header status shows who is currently streaming. Kill button (✕) ends a session.",
    "help.epg_desc": "28-hour program guide as a timeline grid. Channel labels stay visible when scrolling horizontally (sticky). Click on a show → details + TMDB link. Automatically updated daily at 3:00 AM. Manual: <b>Settings → EPG → Update now</b>. Startup run without TMDB (fast) — nightly run with TMDB posters.",
    "help.fav_desc": "Channel list for Plex DVR, EPG and M3U. Reorder via drag & drop. Per channel: group + EPG category (series/movie/news/sports…) for Kodi color coding in the EPG grid. Changes saved immediately.",
    "help.rec_desc": "Recordings with ffmpeg (video copy + audio AC3 transcode). Structured in <code>Show/Season/Episode</code> with NFO metadata for Plex/Kodi. Watchdog terminates stuck recordings after <code>max_duration</code>. Plex library refresh after completion.",
    "help.tmdb_desc": "Posters for EPG shows ≥20 min via TMDB API. Similarity check (45%) prevents wrong matches. Cache: 30 days (found) / 7 days (not found). XMLTV categories + DVB genre IDs for Kodi.",
    "help.settings_desc": "<b>Configuration:</b> Receivers, transcode profiles, device profiles, TMDB API key, e2recorder URL.<br><b>Maintenance:</b> Live logs, log level, service restart, logo update.<br><b>EPG:</b> Manual run, schedule, run history as bar chart, outlier detection.<br><b>Recordings:</b> Path, profile, Plex integration, tuner status, quick record.",
    "help.plex_desc": "HDHomeRun emulation (SSDP UDP multicast). Plex automatically discovers e2proxy as a DVR device. No Threadfin needed.",
    "help.docker_desc": "<code>python:3.11-slim</code> + ffmpeg. Persistent data in <code>/data</code>. <code>network_mode: host</code> required for SSDP and LAN access to receivers.",
    "comp.title": "Compression",
    "comp.desc": "Compresses .ts recordings to .mkv to save disk space. Runs during off-hours so it doesn't compete with live streaming for CPU.",
    "comp.enabled": "Enabled", "comp.profile": "Profile",
    "comp.window": "Time Window", "comp.window_hint": "When compression may run",
    "comp.delete_orig": "Delete original .ts after success",
    "comp.audio_bitrate": "Audio Bitrate",
    "comp.status": "Status", "comp.pending": "Pending",
    "comp.current": "Currently compressing", "comp.history": "Recent Runs",
    "comp.run_now": "▶ Convert now", "comp.in_window": "In window",
    "comp.select_all": "Select all", "comp.convert_selected": "▶ Convert selected",
    "comp.select_hint": "Select at least one recording first.",
    "comp.pause": "Pause", "comp.resume": "Resume", "comp.cancel": "Cancel",
    "comp.paused": "Paused", "comp.eta": "ETA", "comp.cancelled": "Conversion cancelled",
    "comp.cancel_confirm": "Cancel the running conversion? The partial file will be discarded and the recording stays pending.",
    "comp.cpu_limit": "CPU limit", "comp.cpu_hint": "0 = unlimited · lower = gentler background load",
    "comp.background": "Run anytime", "comp.background_hint": "Ignore the time window (use with a CPU limit)",
    "comp.out_window": "Outside window", "comp.backlog_warn": "⚠ Backlog forming — compression isn't keeping up",
    "comp.no_pending": "No files pending compression.",
    "comp.no_history": "No compression runs yet.",
    "comp.started": "Compression started",
    "comp.profile_fast": "Fast (H.264 veryfast, ~40% smaller)",
    "comp.profile_balanced": "Balanced (H.264 medium, ~55% smaller)",
    "comp.profile_quality": "Quality (H.265 medium, ~65% smaller)",
  },
  de: {
    "nav.home": "Startseite", "nav.back": "← Startseite", "nav.settings": "Einstellungen",
    "nav.favorites": "Favoriten", "nav.epg": "EPG Browser", "nav.help": "Hilfe",
    "nav.mainpage": "← Hauptseite", "nav.save": "Speichern", "nav.m3u_fav": "↓ M3U Favoriten",
    "common.save": "Speichern", "common.cancel": "Abbrechen", "common.delete": "Löschen",
    "common.add": "Hinzufügen", "common.close": "Schließen", "common.refresh": "Aktualisieren",
    "common.loading": "Lädt…", "common.search": "Suchen…", "common.enabled": "Aktiviert",
    "common.disabled": "Deenabled", "common.yes": "Ja", "common.no": "Nein",
    "common.channels": "Sender", "common.none": "— keine —",
    "main.live_tv": "Live TV", "main.all_channels": "Alle Sender",
    "main.tuner_status": "Tuner Status", "main.free": "frei", "main.busy": "belegt",
    "main.quick_record": "Schnellaufnahme", "main.duration_min": "Dauer (Min)",
    "main.record": "Aufnehmen", "main.stop": "Stoppen", "main.running": "Läuft",
    "main.play": "Abspielen", "main.no_channels": "Keine Sender gefunden",
    "main.status": "Stand:", "main.select_channel": "Sender auswählen…",
    "main.pick_from_list": "Sender aus der Liste wählen",
    "main.fullscreen": "⤢ Vollbild",
    "main.connecting": "Verbinde…", "main.device_profile": "Device-Profil",
    "epg.title": "EPG Browser", "epg.today": "Heute", "epg.tomorrow": "Morgen",
    "epg.now": "▶ Jetzt", "epg.search": "Suche…", "epg.no_desc": "(Keine Beschreibung)",
    "epg.recording": "AUFNAHME", "epg.record_series": "📺 Serie aufnehmen",
    "epg.record_movie": "🎬 Als Film aufnehmen", "epg.starting": "Starte Aufnahme…",
    "epg.rec_series_hint": "Serie: TVDB-Lookup für echte S/E, Daily-Show-Fallback (S2026E<i>tag</i>)",
    "epg.rec_movie_hint": "Film: Movies/<Titel>/ (Jahr nur bei Duplikaten)",
    "epg.search_tmdb": "🎬 Auf TMDB suchen →", "epg.search_tvdb": "📺 Auf TVDB suchen →",
    "fav.title": "Favoriten", "fav.all_channels": "ALLE SENDER", "fav.group": "— Gruppe —",
    "fav.category": "— Kategorie —", "fav.added": "Zu Favoriten hinzugefügt",
    "fav.saved": "Favoriten gespeichert", "fav.already": "Bereits Favorit",
    "fav.add_hint": "Hinzufügen", "fav.export_m3u": "↓ M3U Favoriten",
    "set.title": "Einstellungen", "set.tab_config": "Konfiguration", "set.tab_maint": "Wartung",
    "set.tab_epg": "EPG", "set.tab_rec": "Aufnahmen", "set.tab_api": "API",
    "set.receivers": "Receiver", "set.add_receiver": "Receiver hinzufügen",
    "set.transcode": "Transcode-Profile", "set.device_profiles": "Device-Profile",
    "set.api_keys": "API-Keys & Tokens", "set.language": "Sprache",
    "set.lang_hint": "Sprache der Oberfläche. Wirkt sofort.",
    "set.about": "Über e2proxy", "set.general": "Allgemein",
    "set.maintenance": "Wartung", "set.update_logos": "🖼 Logos aktualisieren",
    "set.live_logs": "Live Logs", "set.log_level": "Log-Level",
    "set.api_access_log": "API Access Log", "set.load_more": "⬆ Reload",
    "set.live": "↺ Live", "set.clear": "🗑 Leeren", "set.logo_check": "Logo-Prüfung",
    "set.log_retention": "Log Aufbewahrung", "set.days": "Tage",
    "set.epg_sources": "EPG-Quellen", "set.epg_schedule": "EPG-Zeitplan",
    "set.epg_run_now": "EPG jetzt starten", "set.epg_status": "EPG Status",
    "set.epg_history": "EPG-Run Historie",
    "set.rec_path": "Aufnahme-Pfad", "set.rec_profile": "Aufnahme-Profil",
    "set.max_duration": "Max. Dauer", "set.plex_integration": "Plex Integration",
    "set.plex_url": "Plex URL", "set.plex_token": "Plex Token", "set.plex_sections": "Plex Mediatheken",
    "toast.saved": "✓ Gespeichert", "toast.error": "Fehler", "toast.deleted": "✓ Gelöscht",
    "toast.cleared": "✓ Geleert", "toast.lang_changed": "✓ Sprache geändert",
    "status.online": "Online", "status.offline": "Offline",
    "status.epg_updating": "EPG wird aktualisiert…",
    "set.rec_settings": "Aufnahme-Einstellungen", "set.tuner_status": "Tuner-Status",
    "set.switch_tuning": "Umschalt-Tuning", "set.switch_stats": "Per-Sender-Statistik",
    "set.switch_hint": "Beschleunigt das Umschalten (z.B. Plex). NoLatency startet ffmpeg mit minimalem Probing; die Zap-Wartezeit ist die Pause nach dem Umschalten. Werte gelten global als Default — pro Sender wird automatisch gelernt (siehe Tabelle).",
    "set.switch_nolatency": "NoLatency (global)",
    "set.switch_nolatency_hint": "ffmpeg ohne großes Probing starten (schneller, lernt bei Fehlern automatisch hoch)",
    "set.switch_zapwait": "Zap-Wartezeit (s)", "set.switch_monitor": "Monitor-Fenster (s)",
    "set.switch_retries": "Max. Neustarts", "set.switch_nolat_probe": "NoLatency Probesize",
    "set.switch_fail_thresh": "Fehler-Schwelle (Probesize↑)", "set.switch_reset_all": "Reset alle",
    "set.active_recordings": "Aktive Aufnahmen", "set.quick_rec": "Schnell-Aufnahme",
    "set.quick_rec_hint": "Kanal wählen → aktuelle Sendung wird angezeigt → Aufnahme starten.",
    "set.start_rec": "▶ Aufnahme starten", "set.receivers_card": "Receiver",
    "set.transcode_card": "Transcode-Profile", "set.device_card": "Device-Profile",
    "set.api_keys_hint": "API-Keys für externe Dienste. Werden sicher in der Config gespeichert.",
    "set.config_editor": "Config Editor", "set.advanced": "ERWEITERT",
    "set.config_editor_hint": "Direkte Bearbeitung der vollständigen Konfiguration als JSON. Für fortgeschrittene Einstellungen.",
    "set.reset": "↺ Zurücksetzen", "set.maint_actions": "Wartungs-Aktionen",
    "set.maint_notify": "Wartungs-Benachrichtigungen",
    "set.maint_notify_hint": "Sendet zu einem geplanten Zeitpunkt einen HTTP-Call an ein anderes System, z.B. um Wartungsarbeiten anzustoßen. Optional nur im Leerlauf (keine Aufnahme läuft und niemand schaut fern).",
    "set.maint_notify_enable": "Wartungs-Benachrichtigungen aktivieren",
    "set.maint_notify_url": "Ziel-URL", "set.maint_notify_time": "Uhrzeit",
    "set.maint_notify_days": "Wochentage", "set.maint_notify_idle": "Auslöse-Bedingung",
    "set.maint_notify_idle_always": "Immer zur geplanten Zeit",
    "set.maint_notify_idle_only": "Nur im Leerlauf (keine Aufnahme / niemand schaut)",
    "set.maint_notify_test": "Test senden",
    "set.update_logos_card": "Logos aktualisieren", "set.update_logos_hint": "Lädt alle Senderlogos neu in den lokalen Cache.",
    "set.reload_channels": "Senderliste neu laden", "set.reload_channels_hint": "Lädt alle Sender und Bouquets neu vom Receiver.",
    "set.restart_service": "Service neu starten", "set.restart_hint": "Startet den e2proxy Service neu. Aktive Streams werden unterbrochen.",
    "set.epg_update": "EPG-Aktualisierung", "set.epg_run_now_btn": "▶ Jetzt aktualisieren",
    "set.schedule": "Zeitplan", "set.schedule_hint": "Uhrzeit für den täglichen automatischen EPG-Run. Sollte vor dem Plex-Abruf (4-5 Uhr) liegen.",
    "set.last_run": "Letzter Run", "set.run_history": "Run-Historie (letzte 30)",
    "set.run_history_hint": "Balken = Dauer in Sekunden. Rot = Ausreißer (>2× Durchschnitt).",
    "set.epg_run_log": "EPG-Run Log", "set.logo_check_card": "Logo-Prüfung",
    "set.logo_check_hint": "Prüft ob die Senderlogos der Favoriten erreichbar sind.",
    "set.fav_logos": "Sender-Logos", "set.fav_logos_hint": "Hinterlege pro Favorit ein eigenes Logo — Bild hochladen oder URL angeben. Das Bild wird automatisch ins richtige Format konvertiert. Zurücksetzen nutzt wieder das automatische Logo.",
    "set.fav_logos_empty": "Keine Favoriten gefunden. Zuerst Favoriten anlegen.", "set.fav_logos_custom": "eigen",
    "set.fav_logos_url_ph": "Logo-URL (https://…)", "set.fav_logos_save_url": "URL speichern",
    "set.fav_logos_upload": "Hochladen", "set.fav_logos_done": "Logo gespeichert",
    "set.fav_logos_working": "Wird verarbeitet…", "set.fav_logos_need_url": "Bitte eine URL angeben",
    "set.fav_logos_too_big": "Bild zu groß (max 8 MB)",
    "set.fav_logos_zoom": "Zum Vergrößern klicken",
    "set.appearance": "Darstellung", "set.appearance_hint": "Zwischen dunklem und hellem Design wechseln. Wird im Browser gespeichert.",
    "set.log_level_hint": "Steuert welche Log-Einträge angezeigt werden. Wirkt sofort ohne Neustart. Im RAM-Buffer werden immer alle 500 letzten Einträge gespeichert.",
    "set.api_log_hint": "Protokolliert alle API-Anfragen persistent in",
    "set.api_ref": "API Referenz", "set.update": "↺ Aktualisieren",
    "set.timestamp": "Zeitpunkt", "set.duration": "Dauer", "set.result": "Ergebnis",
    "set.get_token": "Token holen", "set.api_log_persist": "Protokolliert alle API-Anfragen persistent in",
    "set.theme_dark": "🌙 Dunkel", "set.theme_light": "☀ Hell",
    "set.connection_failed": "Verbindung fehlgeschlagen",
    "rec.path": "Aufnahme-Pfad", "rec.default_profile": "Default-Profil",
    "rec.max_duration": "Max. Dauer (Watchdog)", "rec.seconds": "Sekunden",
    "rec.plex_url": "Plex URL", "rec.plex_token": "Plex Token", "rec.plex_sections": "Plex Sections",
    "rec.via_login": "🔑 Via Login generieren", "rec.load_sections": "📁 Sections laden",
    "rec.plex_verify": "In Plex verifizieren",
    "rec.plex_verify_hint": "Nach Aufnahme/Konvertierung per Plex-Token prüfen, ob die Datei tatsächlich indexiert wurde (Ergebnis im Log)",
    "rec.select_channel": "— Kanal wählen —", "rec.min": "Min",
    "rec.no_active": "Keine aktiven Aufnahmen.", "rec.duration_label": "Dauer:",
    "epg.update_hint": "Holt den Programmführer aus beiden Receivern, lädt fehlende Sender per Zap nach und ergänzt aus der Online-Quelle (Rytec). Läuft täglich automatisch oder manuell.",
    "epg.progress": "Fortschritt:", "epg.completed": "Abgeschlossen", "epg.oclock": "Uhr",
    "epg.jetzt_aktualisieren": "Jetzt aktualisieren",
    "set.timestamp": "Zeitpunkt", "set.duration": "Dauer", "set.result": "Ergebnis",
    "set.get_token": "Token holen",
    "help.live_tv": "Live TV streamen", "help.api_overview": "API Kurzübersicht",
    "help.recordings": "Aufnahmen",
    "maint.execute": "Ausführen", "maint.restart_btn": "Neu starten",
    "epg.outlier": "Ausreißer", "epg.outliers": "Ausreißer",
    "log.tip": "Tipp: Im Normalbetrieb empfehlen wir <b>INFO</b> oder <b>WARNING</b> \u2014 DEBUG loggt sehr viel (SSDP-Anfragen etc.)",
    "apilog.status_label": "aktiv", "apilog.status_off": "inaktiv",
    "apilog.hint": "Protokolliert alle API-Anfragen persistent in",
    "apilog.activated": "API Logging enabled", "apilog.deactivated": "API Logging deenabled",
    "apilog.clear_confirm": "API Log leeren?",
    "apilog.no_entries": "Noch keine Einträge.",
    "rec.tuner_free": "frei", "rec.tuner_busy": "belegt",
    "help.live_tv_desc": "Sender links wählen → Stream startet im Browser. Profil (Web-SD / Web-HD) oben rechts. Vollbild-Button maximiert. Jeder Stream belegt einen Receiver — der Header-Status zeigt wer gerade streamt. Kill-Button (✕) beendet eine Session.",
    "help.epg_desc": "28-Stunden Programmführer als Zeitstrahl-Grid. Sender-Labels bleiben beim horizontalen Scrollen sichtbar (sticky). Klick auf Sendung → Details + TMDB-Link. Täglich 3:00 Uhr automatisch aktualisiert. Manuell: <b>Settings → EPG → Jetzt aktualisieren</b>. Startup-Run ohne TMDB (schnell) — nächtlicher Run mit TMDB-Postern.",
    "help.fav_desc": "Sender-Liste für Plex DVR, EPG und M3U. Reihenfolge per Drag & Drop. Pro Sender: Gruppe + EPG-Kategorie (serie/movie/news/sports…) für Kodi-Farbkodierung im EPG-Grid. Änderungen sofort gespeichert.",
    "help.rec_desc": "Aufnahmen mit ffmpeg (Video copy + Audio AC3 Transcode). Strukturiert in <code>Sendung/Staffel/Episode</code> mit NFO-Metadaten für Plex/Kodi. Watchdog beendet hängende Aufnahmen nach <code>max_duration</code>. Plex Library Refresh nach Ende.",
    "help.tmdb_desc": "Poster für EPG-Sendungen ≥20 Min via TMDB API. Ähnlichkeits-Check (45%) verhindert falsche Zuordnung. Cache: 30 Tage (gefunden) / 7 Tage (nicht gefunden). XMLTV-Kategorien + DVB Genre-IDs für Kodi.",
    "help.settings_desc": "<b>Konfiguration:</b> Receiver, Transcode-Profile, Device-Profile, TMDB API-Key, e2recorder URL.<br><b>Wartung:</b> Live-Logs, Log-Level, Service-Neustart, Logo-Update.<br><b>EPG:</b> Manueller Run, Zeitplan, Run-Historie als Balkengrafik, Ausreißer-Erkennung.<br><b>Aufnahmen:</b> Pfad, Profil, Plex-Integration, Tuner-Status, Schnell-Aufnahme.",
    "help.plex_desc": "HDHomeRun-Emulation (SSDP UDP Multicast). Plex erkennt e2proxy automatisch als DVR-Gerät. Kein Threadfin nötig.",
    "help.docker_desc": "<code>python:3.11-slim</code> + ffmpeg. Persistente Daten in <code>/data</code>. <code>network_mode: host</code> nötig für SSDP und LAN-Zugriff auf Receiver.",
    "comp.title": "Komprimierung",
    "comp.desc": "Komprimiert .ts Aufnahmen zu .mkv um Speicherplatz zu sparen. Läuft in Off-Hours damit kein CPU-Konflikt mit Live-Streaming entsteht.",
    "comp.enabled": "Aktiviert", "comp.profile": "Profil",
    "comp.window": "Zeitfenster", "comp.window_hint": "Wann darf komprimiert werden",
    "comp.delete_orig": "Original .ts nach Erfolg löschen",
    "comp.audio_bitrate": "Audio-Bitrate",
    "comp.status": "Status", "comp.pending": "Wartend",
    "comp.current": "Wird gerade komprimiert", "comp.history": "Letzte Läufe",
    "comp.run_now": "▶ Jetzt konvertieren", "comp.in_window": "Im Zeitfenster",
    "comp.select_all": "Alle auswählen", "comp.convert_selected": "▶ Auswahl konvertieren",
    "comp.select_hint": "Bitte zuerst mindestens eine Aufnahme auswählen.",
    "comp.pause": "Pause", "comp.resume": "Fortsetzen", "comp.cancel": "Abbrechen",
    "comp.paused": "Pausiert", "comp.eta": "Restzeit", "comp.cancelled": "Konvertierung abgebrochen",
    "comp.cancel_confirm": "Laufende Konvertierung abbrechen? Die Teildatei wird verworfen und die Aufnahme bleibt wartend.",
    "comp.cpu_limit": "CPU-Limit", "comp.cpu_hint": "0 = unbegrenzt · niedriger = sanftere Hintergrundlast",
    "comp.background": "Jederzeit ausführen", "comp.background_hint": "Zeitfenster ignorieren (mit CPU-Limit verwenden)",
    "comp.out_window": "Außerhalb des Zeitfensters", "comp.backlog_warn": "⚠ Rückstand bildet sich — Komprimierung kommt nicht hinterher",
    "comp.no_pending": "Keine Dateien zur Komprimierung.",
    "comp.no_history": "Noch keine Komprimierungsläufe.",
    "comp.started": "Komprimierung gestartet",
    "comp.profile_fast": "Schnell (H.264 veryfast, ~40% kleiner)",
    "comp.profile_balanced": "Ausgewogen (H.264 medium, ~55% kleiner)",
    "comp.profile_quality": "Qualität (H.265 medium, ~65% kleiner)",
  }
};

function getLang() {
  let l = localStorage.getItem('e2proxy-lang');
  if (!l) {
    // Browser-Sprache erkennen, sonst Englisch
    const nav = (navigator.language || 'en').toLowerCase();
    l = nav.startsWith('de') ? 'de' : 'en';
  }
  return (l === 'de') ? 'de' : 'en';
}

function t(key) {
  const lang = getLang();
  return (I18N[lang] && I18N[lang][key]) || (I18N.en[key]) || key;
}

function applyI18n() {
  try {
    const lang = getLang();
    document.documentElement.lang = lang;
    const langSel = document.getElementById('lang-sel');
    if (langSel) langSel.value = lang;
    document.querySelectorAll('[data-i18n]').forEach(el => {
      try {
        const key = el.getAttribute('data-i18n');
        const val = t(key);
        if (el.getAttribute('data-i18n-html') === '1') el.innerHTML = val;
        else el.textContent = val;
      } catch(e) {}
    });
    document.querySelectorAll('[data-i18n-ph]').forEach(el => {
      try { el.setAttribute('placeholder', t(el.getAttribute('data-i18n-ph'))); } catch(e) {}
    });
    document.querySelectorAll('[data-i18n-title]').forEach(el => {
      try { el.setAttribute('title', t(el.getAttribute('data-i18n-title'))); } catch(e) {}
    });
  } catch(e) { console.warn('applyI18n:', e); }
}

function setLang(lang) {
  localStorage.setItem('e2proxy-lang', lang === 'de' ? 'de' : 'en');
  location.reload();   // Voller Reload — alle Texte inkl. dynamisch gerenderte korrekt
}

document.addEventListener('DOMContentLoaded', applyI18n);
</script>
"""

CSS_BASE = """
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@300;400;500&display=swap');
:root {
  --bg:#0a0a0f; --surface:#12121a; --surface2:#1a1a26;
  --border:#2a2a3d; --accent:#6366f1; --accent2:#818cf8;
  --text:#e2e2f0; --muted:#6b6b8a;
  --green:#22c55e; --red:#ef4444; --amber:#f59e0b;
}
[data-theme="light"] {
  --bg:#f4f5f7; --surface:#ffffff; --surface2:#f0f1f5;
  --border:#d1d5db; --accent:#4f46e5; --accent2:#4f46e5;
  --text:#1a1a2e; --muted:#6b7280;
  --green:#16a34a; --red:#dc2626; --amber:#d97706;
}
*{margin:0;padding:0;box-sizing:border-box;}
body{background:var(--bg);color:var(--text);font-family:'Inter',sans-serif;font-weight:300;min-height:100vh;}
.header{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 24px;display:flex;align-items:center;gap:12px;position:sticky;top:0;z-index:100;}
.logo{font-family:'JetBrains Mono',monospace;font-weight:700;font-size:17px;color:var(--accent2);}
.logo span{color:var(--muted);font-weight:400;}
.header-right{margin-left:auto;display:flex;gap:8px;align-items:center;flex-wrap:wrap;}
.btn{padding:6px 12px;border-radius:6px;font-size:11px;font-family:'JetBrains Mono',monospace;cursor:pointer;border:1px solid var(--border);color:var(--text);background:var(--surface2);text-decoration:none;display:inline-block;transition:all 0.15s;white-space:nowrap;}
.btn:hover{border-color:var(--accent);color:var(--accent2);}
.btn-primary{background:var(--accent);border-color:var(--accent);color:white;}
.btn-primary:hover{background:var(--accent2);border-color:var(--accent2);color:white;}
.btn-danger{color:var(--red);border-color:var(--red);}
.btn-danger:hover{background:var(--red);color:white;}
.select{background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:6px;font-family:'JetBrains Mono',monospace;font-size:11px;cursor:pointer;outline:none;}
.select:focus{border-color:var(--accent);}
.toast{position:fixed;bottom:20px;right:20px;background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:9px 14px;border-radius:7px;font-size:12px;font-family:'JetBrains Mono',monospace;opacity:0;transform:translateY(8px);transition:all 0.25s;z-index:9999;}
.toast.show{opacity:1;transform:translateY(0);}
.toast.success{border-color:var(--green);color:var(--green);}
.toast.error{border-color:var(--red);color:var(--red);}
.theme-toggle{background:none;border:1px solid var(--border);color:var(--muted);padding:4px 8px;border-radius:5px;cursor:pointer;font-size:13px;transition:all 0.15s;}
.theme-toggle:hover{border-color:var(--accent);color:var(--accent2);}
"""

def html_page(title, body, head_extra=""):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>e2proxy · {title}</title>
<script>(function(){{var t=localStorage.getItem('e2proxy-theme');if(t==='light')document.documentElement.setAttribute('data-theme','light');}})();</script>
<style>{CSS_BASE}{head_extra}</style>
{I18N_JS}
</head>
<body>
<div id="epg-status-bar" style="display:none;position:sticky;top:0;z-index:200;background:#f59e0b;color:#1a1200;padding:5px 20px;font-family:'JetBrains Mono',monospace;font-size:11px;align-items:center;gap:8px;">
  <span style="animation:pulse 1s infinite">⟳</span>
  <span id="epg-status-text" data-i18n="status.epg_updating">EPG updating…</span>
</div>
<style>@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:0.5}}}}</style>
{body}
<div class="toast" id="toast"></div>
<script>
function showToast(msg, type) {{
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show' + (type ? ' ' + type : '');
  setTimeout(() => t.className = 'toast', 3000);
}}
function apiPost(url, data) {{
  return fetch(url, {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(data)}}).then(r=>r.json());
}}
function toggleTheme() {{
  const isLight = document.documentElement.getAttribute('data-theme') === 'light';
  if (isLight) {{
    document.documentElement.removeAttribute('data-theme');
    localStorage.setItem('e2proxy-theme', 'dark');
  }} else {{
    document.documentElement.setAttribute('data-theme', 'light');
    localStorage.setItem('e2proxy-theme', 'light');
  }}
  const btn = document.getElementById('theme-toggle-btn');
  if (btn) btn.textContent = isLight ? '☀' : '🌙';
}}

// ── EPG Status Indicator ─────────────────────────────
(function() {{
  function checkEpg() {{
    fetch('/api/epg/status').then(r=>r.json()).then(d=>{{
      const el = document.getElementById('epg-status-bar');
      if (!el) return;
      if (d.running) {{
        el.style.display = 'flex';
        el.querySelector('#epg-status-text').textContent =
          (typeof t === 'function' ? t('status.epg_updating') : 'EPG updating…') + ' ' + (d.phase||'') + (d.progress ? ' ' + d.progress + '%' : '');
      }} else {{
        el.style.display = 'none';
      }}
    }}).catch(()=>{{}});
  }}
  setInterval(checkEpg, 5000);
  checkEpg();
}})();
</script>
</body>
</html>"""


# ── Recording System ───────────────────────────────────────────────────────────
import uuid as _uuid
import subprocess as _subprocess

RECORDINGS_DIR_DEFAULT = f"{DATA_DIR}/recordings"

_active_recordings = {}   # recording_id → dict
_active_recordings_lock = threading.Lock()

def get_recordings_config():
    cfg = get_config()
    return {
        "path":        cfg.get("recordings_path", RECORDINGS_DIR_DEFAULT),
        "profile":     cfg.get("recordings_profile", "remux-ac3"),
        "max_duration":cfg.get("recordings_max_duration", 10800),  # 3h default
        "plex_url":    cfg.get("recordings_plex_url", ""),
        "plex_token":  cfg.get("recordings_plex_token", ""),
        "plex_section":cfg.get("recordings_plex_section", ""),
        "plex_verify": cfg.get("recordings_plex_verify", False),
    }

def _safe_filename(title):
    """Erstellt sicheren Dateinamen aus Titel."""
    import re
    s = re.sub(r'[<>:"/\\|?*]', '', title)
    s = re.sub(r'\s+', ' ', s.strip())
    return s[:80]

def _output_path(rec_path, title, subtitle="", started_ts=None):
    """
    Erstellt strukturierten Ausgabepfad:
    rec_path/Serienname/Staffel_XX/Serienname_SXXEXX_Titel.ts
    Wenn kein Staffel/Episode erkennbar: rec_path/Titel/YYYY-MM-DD_HH-MM.ts
    """
    import re
    ts = datetime.fromtimestamp(started_ts or time.time()).strftime("%Y-%m-%d_%H-%M")
    safe_title = _safe_filename(title)

    # Versuche Season/Episode aus Subtitle zu extrahieren (z.B. "Staffel 3, Folge 12")
    season, episode = None, None
    if subtitle:
        m = re.search(r'[Ss](?:taffel\s*|eason\s*)(\d+)', subtitle, re.I)
        if m: season = int(m.group(1))
        m = re.search(r'[Ff](?:olge\s*|pisode\s*)(\d+)', subtitle, re.I)
        if m: episode = int(m.group(1))

    if season is not None and episode is not None:
        season_dir = os.path.join(rec_path, safe_title, f"Staffel_{season:02d}")
        filename = f"{safe_title}_S{season:02d}E{episode:02d}_{ts}.ts"
        return os.path.join(season_dir, filename)
    elif season is not None:
        season_dir = os.path.join(rec_path, safe_title, f"Staffel_{season:02d}")
        filename = f"{safe_title}_S{season:02d}_{ts}.ts"
        return os.path.join(season_dir, filename)
    else:
        # Kein Staffel/Episode → einfache Datei im Titelordner
        show_dir = os.path.join(rec_path, safe_title)
        filename = f"{safe_title}_{ts}.ts"
        return os.path.join(show_dir, filename)

def _plex_sections_list(rcfg):
    """Konfigurierte Section-IDs als Liste (kommagetrennte Werte werden gesplittet)."""
    raw = rcfg.get("plex_section", "") or ""
    return [s.strip() for s in raw.split(",") if s.strip()]


def _plex_refresh(rcfg):
    """Benachrichtigt Plex über neue/geänderte Aufnahme.

    Triggert einen Library-Scan. Unterstützt mehrere (kommagetrennte) Section-IDs
    — jede Section wird einzeln aktualisiert. Ohne Section wird die gesamte
    Bibliothek gescannt.
    """
    url = rcfg.get("plex_url", "").rstrip("/")
    token = rcfg.get("plex_token", "")
    if not url or not token:
        return
    import urllib.request as _ur
    import urllib.parse as _up
    sections = _plex_sections_list(rcfg)
    targets = sections if sections else ["all"]
    for section in targets:
        try:
            req_url = f"{url}/library/sections/{section}/refresh?X-Plex-Token={_up.quote(token)}"
            req = _ur.Request(req_url, method="GET",
                              headers={"User-Agent": "e2proxy/1.0"})
            with _ur.urlopen(req, timeout=8) as r:
                log.info(f"Plex refresh section={section}: {r.status}")
        except Exception as e:
            log.warning(f"Plex refresh section={section} failed: {e}")


def _plex_find_file(rcfg, filepath):
    """Prüft, ob eine Datei in Plex indexiert ist.

    Vergleicht den absoluten Pfad bzw. – als Fallback – den Dateinamen mit den
    Media-Parts der konfigurierten Sections (oder allen Sections, falls keine
    konfiguriert ist). Liefert True bei einem Treffer.
    """
    url = rcfg.get("plex_url", "").rstrip("/")
    token = rcfg.get("plex_token", "")
    if not url or not token:
        return False
    import urllib.request as _ur
    import urllib.parse as _up

    def _get_json(req_url):
        req = _ur.Request(req_url, headers={"Accept": "application/json",
                                            "User-Agent": "e2proxy/1.0"})
        with _ur.urlopen(req, timeout=12) as r:
            return json.loads(r.read())

    target_abs = os.path.abspath(filepath)
    target_base = os.path.basename(filepath)

    # Section-IDs + Typ ermitteln
    wanted = set(_plex_sections_list(rcfg))
    try:
        meta = _get_json(f"{url}/library/sections?X-Plex-Token={_up.quote(token)}")
        directories = meta.get("MediaContainer", {}).get("Directory", [])
    except Exception as e:
        log.debug(f"Plex sections list failed: {e}")
        return False

    for d in directories:
        sid = str(d.get("key", ""))
        if wanted and sid not in wanted:
            continue
        # Leaf-Typ je Library: movie=1, show→episode=4; sonst beide probieren
        stype = d.get("type", "")
        leaf_types = ["1"] if stype == "movie" else ["4"] if stype == "show" else ["1", "4"]
        for lt in leaf_types:
            try:
                q = _up.urlencode({"X-Plex-Token": token, "type": lt})
                data = _get_json(f"{url}/library/sections/{sid}/all?{q}")
            except Exception as e:
                log.debug(f"Plex find section={sid} type={lt}: {e}")
                continue
            for item in data.get("MediaContainer", {}).get("Metadata", []):
                for media in item.get("Media", []):
                    for part in media.get("Part", []):
                        pf = part.get("file", "")
                        if not pf:
                            continue
                        if os.path.abspath(pf) == target_abs or os.path.basename(pf) == target_base:
                            return True
    return False


def _plex_notify(rcfg, filepath=None, label="Aufnahme"):
    """Stößt einen Plex-Scan an und verifiziert optional, dass die Datei ankam.

    Ist die Verifikation aktiviert (recordings_plex_verify) und ein filepath
    gegeben, wird nach dem Refresh wiederholt geprüft, ob die Datei in Plex
    auftaucht — inklusive erneutem Refresh-Versuch. Das Ergebnis wird geloggt.
    """
    if not (rcfg.get("plex_url") and rcfg.get("plex_token")):
        return
    _plex_refresh(rcfg)
    if not rcfg.get("plex_verify") or not filepath:
        return
    base = os.path.basename(filepath)
    deadline = time.time() + 120
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            if _plex_find_file(rcfg, filepath):
                log.info(f"Plex verify OK ({label}): '{base}' indexiert (Versuch {attempt})")
                return
        except Exception as e:
            log.debug(f"Plex verify attempt {attempt}: {e}")
        time.sleep(8)
        if attempt % 3 == 0:
            # Scan erneut anstoßen, falls der erste nicht griff
            _plex_refresh(rcfg)
    log.warning(
        f"Plex verify FAILED ({label}): '{base}' nach 120s nicht in Plex gefunden — "
        f"Pfad-Mapping/Library prüfen (Plex muss denselben Pfad sehen)"
    )

# ── Compression Module ──────────────────────────────────────────────────────
# Compresses .ts recordings to .mkv to save disk space.
# Runs during configured off-hours, processes pending files sequentially.
# Tracks history of completed runs for backlog detection.

COMPRESSION_PROFILES = {
    "fast":     {"label": "Fast (H.264 veryfast)",  "vcodec": "libx264", "preset": "veryfast", "crf": 23, "expected_ratio": 0.60},
    "balanced": {"label": "Balanced (H.264 medium)", "vcodec": "libx264", "preset": "medium",   "crf": 22, "expected_ratio": 0.45},
    "quality":  {"label": "Quality (H.265 medium)",  "vcodec": "libx265", "preset": "medium",   "crf": 24, "expected_ratio": 0.35},
}

COMPRESSION_HISTORY_FILE = f"{DATA_DIR}/compression_history.json"
_compression_lock = threading.Lock()
_compression_state = {
    "current": None,           # {file, started, profile, pid, progress, eta_sec, speed, paused, ...}
    "scheduled": False,        # is scheduler thread running
}
_compression_proc = None       # subprocess.Popen of the running ffmpeg, for pause/resume/cancel
_compression_cancel = False    # set True to request cancellation of the running job

def get_compression_config():
    cfg = get_config()
    return {
        "enabled":      cfg.get("compression_enabled", False),
        "profile":      cfg.get("compression_profile", "balanced"),
        "window_start": cfg.get("compression_window_start", "01:00"),
        "window_end":   cfg.get("compression_window_end", "06:00"),
        "delete_original": cfg.get("compression_delete_original", True),
        "audio_bitrate":   cfg.get("compression_audio_bitrate", "192k"),
        "cpu_limit":       int(cfg.get("compression_cpu_limit", 0)),       # 0 = unlimited; else % of cores
        "ignore_window":   cfg.get("compression_ignore_window", False),    # run anytime (background mode)
    }

def _probe_duration(path):
    """Returns media duration in seconds via ffprobe, or 0 on failure."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        return float(out.stdout.decode("utf-8", errors="replace").strip())
    except Exception:
        return 0.0

def _load_compression_history():
    try:
        if os.path.exists(COMPRESSION_HISTORY_FILE):
            with open(COMPRESSION_HISTORY_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return []

def _save_compression_history(hist):
    try:
        # Keep only last 100 entries
        if len(hist) > 100:
            hist = hist[-100:]
        with open(COMPRESSION_HISTORY_FILE, "w") as f:
            json.dump(hist, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log.debug(f"Compression history save: {e}")

def _add_compression_history(entry):
    """Adds an entry to compression history. Thread-safe."""
    with _compression_lock:
        hist = _load_compression_history()
        hist.append(entry)
        _save_compression_history(hist)

def find_pending_compressions():
    """Returns list of .ts files in recordings_path that have no .mkv sibling.
    Excludes files currently being recorded (in _active_recordings).
    Returns sorted list (oldest first) of (filepath, size_bytes) tuples."""
    rcfg = get_recordings_config()
    rec_root = rcfg["path"]
    if not os.path.isdir(rec_root):
        return []
    
    # Active recording paths to exclude
    active_paths = set()
    with _active_recordings_lock:
        for rec in _active_recordings.values():
            p = rec.get("filepath")
            if p:
                active_paths.add(p)
    # Also exclude the file currently being compressed
    with _compression_lock:
        cur = _compression_state["current"]
        if cur and cur.get("file"):
            active_paths.add(cur["file"])
    
    pending = []
    for dirpath, dirnames, filenames in os.walk(rec_root):
        for fn in filenames:
            if not fn.endswith(".ts"):
                continue
            full = os.path.join(dirpath, fn)
            if full in active_paths:
                continue
            mkv_sibling = full[:-3] + ".mkv"
            if os.path.exists(mkv_sibling):
                continue
            try:
                stat = os.stat(full)
                # Skip files smaller than 10MB (likely broken/incomplete)
                if stat.st_size < 10 * 1024 * 1024:
                    continue
                pending.append((full, stat.st_size, stat.st_mtime))
            except OSError:
                continue
    
    # Sort by mtime (oldest first)
    pending.sort(key=lambda x: x[2])
    return [(p, s) for p, s, m in pending]

def _is_in_window(now=None):
    """Check if current time is within the compression window."""
    cfg = get_compression_config()
    if not cfg["enabled"]:
        return False
    now = now or datetime.now()
    cur_min = now.hour * 60 + now.minute
    try:
        sh, sm = map(int, cfg["window_start"].split(":"))
        eh, em = map(int, cfg["window_end"].split(":"))
    except Exception:
        return False
    start_min = sh * 60 + sm
    end_min = eh * 60 + em
    if start_min < end_min:
        return start_min <= cur_min < end_min
    else:
        # Window crosses midnight (e.g. 22:00 - 06:00)
        return cur_min >= start_min or cur_min < end_min

def compress_file(ts_path, profile_name=None, manual=False):
    """Compresses a single .ts file to .mkv. Blocking call.
    Returns dict with result info. Writes to a .part temp file and renames on
    success so an interrupted run never leaves a half-finished .mkv behind."""
    global _compression_proc, _compression_cancel
    cfg = get_compression_config()
    profile_name = profile_name or cfg["profile"]
    profile = COMPRESSION_PROFILES.get(profile_name, COMPRESSION_PROFILES["balanced"])

    mkv_path = ts_path[:-3] + ".mkv"
    mkv_tmp  = mkv_path + ".part"
    started = time.time()
    started_iso = datetime.now().isoformat()

    try:
        orig_size = os.path.getsize(ts_path)
    except OSError as e:
        return {"ok": False, "error": f"Source not readable: {e}", "ts_path": ts_path}

    duration_sec = _probe_duration(ts_path)

    # CPU limit → encoder thread count + nice priority
    cpu_limit = cfg["cpu_limit"]
    ncpu = os.cpu_count() or 1
    thread_args, nice_prefix = [], []
    if cpu_limit and 0 < cpu_limit < 100:
        nthreads = max(1, round(ncpu * cpu_limit / 100))
        thread_args = ["-threads", str(nthreads)]
        nice_level = max(1, min(19, round(19 - (cpu_limit / 100) * 18)))
        import shutil as _sh
        if _sh.which("nice"):
            nice_prefix = ["nice", "-n", str(nice_level)]

    # Mark as in progress
    with _compression_lock:
        _compression_state["current"] = {
            "file": ts_path,
            "started": started_iso,
            "profile": profile_name,
            "orig_size": orig_size,
            "manual": manual,
            "duration_sec": round(duration_sec, 1),
            "progress": 0.0,
            "out_time_sec": 0.0,
            "eta_sec": None,
            "speed": None,
            "paused": False,
            "cpu_limit": cpu_limit,
        }
    _compression_cancel = False

    cmd = nice_prefix + [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-nostats",
        "-progress", "pipe:1",
        "-i", ts_path,
        "-c:v", profile["vcodec"],
        "-preset", profile["preset"],
        "-crf", str(profile["crf"]),
        "-c:a", "aac", "-b:a", cfg["audio_bitrate"],
        "-c:s", "copy",  # keep subtitles
        "-map", "0",     # all streams
        "-map", "-0:d?", # drop data streams (cause problems)
    ] + thread_args + [
        "-f", "matroska",
        mkv_tmp,
    ]

    cpu_note = f", cpu≤{cpu_limit}%" if cpu_limit else ""
    log.info(f"Compression START [{profile_name}{cpu_note}]: {os.path.basename(ts_path)} ({orig_size/(1024**2):.1f} MB)")

    def _parse_progress(proc):
        """Reads ffmpeg -progress key=value lines from stdout, updates state."""
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").strip()
                if "=" not in line:
                    continue
                key, _, val = line.partition("=")
                with _compression_lock:
                    cur = _compression_state["current"]
                    if not cur:
                        continue
                    if key in ("out_time_us", "out_time_ms"):
                        try:
                            out_s = int(val) / 1_000_000.0
                            cur["out_time_sec"] = round(out_s, 1)
                            if duration_sec > 0:
                                cur["progress"] = max(0.0, min(1.0, out_s / duration_sec))
                        except ValueError:
                            pass
                    elif key == "speed":
                        try:
                            sp = float(val.replace("x", "").strip())
                            cur["speed"] = sp
                            if sp > 0 and duration_sec > 0:
                                remaining = max(0.0, duration_sec - cur.get("out_time_sec", 0.0))
                                cur["eta_sec"] = int(remaining / sp)
                        except ValueError:
                            pass
        except Exception:
            pass

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _compression_proc = proc
        with _compression_lock:
            if _compression_state["current"]:
                _compression_state["current"]["pid"] = proc.pid

        # Drain stderr in a thread (avoid pipe-buffer deadlock)
        stderr_chunks = []
        def _drain_stderr():
            try:
                for line in proc.stderr:
                    stderr_chunks.append(line)
            except Exception:
                pass
        t_err = threading.Thread(target=_drain_stderr, daemon=True)
        t_prog = threading.Thread(target=_parse_progress, args=(proc,), daemon=True)
        t_err.start(); t_prog.start()

        proc.wait()
        t_err.join(timeout=5); t_prog.join(timeout=5)
        stderr = b"".join(stderr_chunks)
        elapsed = time.time() - started
        cancelled = _compression_cancel

        if cancelled or proc.returncode != 0:
            if os.path.exists(mkv_tmp):
                try: os.remove(mkv_tmp)
                except OSError: pass
            if cancelled:
                log.info(f"Compression CANCELLED: {os.path.basename(ts_path)}")
                entry = {
                    "ts_path": ts_path, "profile": profile_name, "started": started_iso,
                    "elapsed": int(elapsed), "ok": False, "cancelled": True,
                    "error": "cancelled by user", "orig_size": orig_size, "manual": manual,
                }
            else:
                err_msg = stderr.decode("utf-8", errors="replace")[-500:]
                log.warning(f"Compression FAILED: {os.path.basename(ts_path)} — {err_msg.strip()[:200]}")
                entry = {
                    "ts_path": ts_path, "profile": profile_name, "started": started_iso,
                    "elapsed": int(elapsed), "ok": False, "error": err_msg.strip()[:300],
                    "orig_size": orig_size, "manual": manual,
                }
            _add_compression_history(entry)
            return entry

        # Success — atomically promote .part to final .mkv
        os.replace(mkv_tmp, mkv_path)
        new_size = os.path.getsize(mkv_path)
        ratio = new_size / orig_size if orig_size else 0
        log.info(f"Compression OK: {os.path.basename(ts_path)} → {new_size/(1024**2):.1f} MB ({ratio*100:.0f}%, {elapsed:.0f}s)")

        # Copy NFO sibling if exists (so Plex picks up the .mkv)
        nfo_src = ts_path[:-3] + ".nfo"
        nfo_dst = mkv_path[:-4] + ".nfo"
        if os.path.exists(nfo_src) and not os.path.exists(nfo_dst):
            try:
                import shutil
                shutil.copy2(nfo_src, nfo_dst)
            except Exception as e:
                log.debug(f"NFO copy: {e}")

        # Delete original
        if cfg["delete_original"]:
            try:
                os.remove(ts_path)
                # Also remove the .ts.nfo if it exists (we copied to .mkv.nfo above)
                if os.path.exists(nfo_src):
                    os.remove(nfo_src)
                log.info(f"Original deleted: {os.path.basename(ts_path)}")
            except OSError as e:
                log.warning(f"Failed to delete original: {e}")

        # Trigger Plex refresh (+ optionale Verifikation der konvertierten .mkv)
        rcfg = get_recordings_config()
        if rcfg.get("plex_url") and rcfg.get("plex_token"):
            threading.Thread(target=_plex_notify, args=(rcfg, mkv_path),
                             kwargs={"label": "Konvertierung"}, daemon=True).start()

        entry = {
            "ts_path": ts_path,
            "mkv_path": mkv_path,
            "profile": profile_name,
            "started": started_iso,
            "elapsed": int(elapsed),
            "ok": True,
            "orig_size": orig_size,
            "new_size": new_size,
            "ratio": round(ratio, 3),
            "manual": manual,
        }
        _add_compression_history(entry)
        return entry

    except Exception as e:
        log.error(f"Compression exception: {e}")
        if os.path.exists(mkv_tmp):
            try: os.remove(mkv_tmp)
            except OSError: pass
        entry = {
            "ts_path": ts_path,
            "profile": profile_name,
            "started": started_iso,
            "elapsed": int(time.time() - started),
            "ok": False,
            "error": str(e),
            "manual": manual,
        }
        _add_compression_history(entry)
        return entry
    finally:
        _compression_proc = None
        _compression_cancel = False
        with _compression_lock:
            _compression_state["current"] = None

def compress_next_pending(manual=False):
    """Picks the oldest pending file and compresses it. Returns result or None."""
    pending = find_pending_compressions()
    if not pending:
        return None
    ts_path = pending[0][0]
    return compress_file(ts_path, manual=manual)

def compress_selected(paths, profile_name=None, manual=True):
    """Compresses a specific list of .ts files sequentially. Blocking call.
    Validates each path is under recordings_path, is a .ts file, exists and
    has no .mkv sibling. Returns list of result dicts."""
    rcfg = get_recordings_config()
    base = os.path.abspath(rcfg["path"])
    results = []
    for ts_path in paths:
        try:
            full = os.path.abspath(ts_path)
            # Security: only files under recordings root
            if not full.startswith(base + os.sep) and full != base:
                results.append({"ok": False, "error": "Invalid path", "ts_path": ts_path})
                continue
            if not full.endswith(".ts") or not os.path.exists(full):
                results.append({"ok": False, "error": "Not a valid .ts file", "ts_path": ts_path})
                continue
            if os.path.exists(full[:-3] + ".mkv"):
                results.append({"ok": False, "error": "Already converted", "ts_path": ts_path})
                continue
            results.append(compress_file(full, profile_name=profile_name, manual=manual))
        except Exception as e:
            results.append({"ok": False, "error": str(e), "ts_path": ts_path})
    return results

def _compression_scheduler_loop():
    """Background thread: every minute, check if there's work and we may run.
    Runs inside the time window, or anytime when background mode is enabled."""
    log.info("Compression scheduler started")
    while True:
        try:
            cfg = get_compression_config()
            may_run = cfg["enabled"] and (cfg["ignore_window"] or _is_in_window())
            if may_run:
                with _compression_lock:
                    busy = _compression_state["current"] is not None
                if not busy:
                    pending = find_pending_compressions()
                    if pending:
                        compress_next_pending(manual=False)
                        continue
        except Exception as e:
            log.warning(f"Compression scheduler: {e}")
        time.sleep(60)

def pause_compression():
    """Suspends the running ffmpeg process (SIGSTOP) — frees CPU instantly."""
    global _compression_proc
    proc = _compression_proc
    if not proc or proc.poll() is not None:
        return False
    try:
        proc.send_signal(signal.SIGSTOP)
        with _compression_lock:
            if _compression_state["current"]:
                _compression_state["current"]["paused"] = True
        log.info("Compression paused")
        return True
    except Exception as e:
        log.warning(f"Pause failed: {e}")
        return False

def resume_compression():
    """Resumes a paused ffmpeg process (SIGCONT)."""
    global _compression_proc
    proc = _compression_proc
    if not proc or proc.poll() is not None:
        return False
    try:
        proc.send_signal(signal.SIGCONT)
        with _compression_lock:
            if _compression_state["current"]:
                _compression_state["current"]["paused"] = False
        log.info("Compression resumed")
        return True
    except Exception as e:
        log.warning(f"Resume failed: {e}")
        return False

def cancel_compression():
    """Cancels the running job. Resumes first if paused so it can receive the kill."""
    global _compression_proc, _compression_cancel
    proc = _compression_proc
    if not proc or proc.poll() is not None:
        return False
    _compression_cancel = True
    try:
        # If paused, resume so the process can act on the terminate signal
        try: proc.send_signal(signal.SIGCONT)
        except Exception: pass
        proc.terminate()
        log.info("Compression cancel requested")
        return True
    except Exception as e:
        log.warning(f"Cancel failed: {e}")
        return False

def cleanup_orphaned_compressions():
    """On startup: remove leftover *.mkv.part temp files from an interrupted run.
    The matching .ts stays in place and is picked up again as pending."""
    rcfg = get_recordings_config()
    rec_root = rcfg.get("path", "")
    if not rec_root or not os.path.isdir(rec_root):
        return
    removed = 0
    for dirpath, dirnames, filenames in os.walk(rec_root):
        for fn in filenames:
            if fn.endswith(".mkv.part"):
                try:
                    os.remove(os.path.join(dirpath, fn))
                    removed += 1
                except OSError:
                    pass
    if removed:
        log.info(f"Cleaned up {removed} orphaned compression temp file(s)")

def start_compression_scheduler():
    """Idempotent — starts the scheduler thread once."""
    with _compression_lock:
        if _compression_state["scheduled"]:
            return
        _compression_state["scheduled"] = True
    cleanup_orphaned_compressions()
    t = threading.Thread(target=_compression_scheduler_loop, daemon=True, name="compression")
    t.start()

def has_compression_backlog():
    """Returns True if more than 10 pending files OR oldest is >7 days old."""
    pending = find_pending_compressions()
    if len(pending) > 10:
        return True
    if pending:
        oldest_path = pending[0][0]
        try:
            age_days = (time.time() - os.path.getmtime(oldest_path)) / 86400
            if age_days > 7:
                return True
        except OSError:
            pass
    return False



def _write_nfo(path, title, description="", image_url="", channel_name="", channel_logo=""):
    """Schreibt Plex-kompatible .nfo Metadaten-Datei.
    Format: <movie> für Plex Home Video / Movie Library.
    Dateiname identisch zur Videodatei, nur .nfo Endung."""
    try:
        nfo_path = path.rsplit(".", 1)[0] + ".nfo"
        lines = [
            '<?xml version="1.0" encoding="utf-8" standalone="yes"?>',
            "<movie>",
            f"  <title>{title}</title>",
            f"  <originaltitle>{title}</originaltitle>",
            f"  <sorttitle>{title}</sorttitle>",
        ]
        if description:
            lines.append(f"  <plot>{description}</plot>")
        if image_url:
            lines.append(f"  <thumb aspect=\"poster\">{image_url}</thumb>")
        if channel_logo:
            lines.append(f"  <thumb aspect=\"logo\">{channel_logo}</thumb>")
        if channel_name:
            lines.append(f"  <studio>{channel_name}</studio>")
        lines.append(f"  <aired>{datetime.now().strftime('%Y-%m-%d')}</aired>")
        lines.append("</movie>")
        with open(nfo_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        log.debug(f"NFO geschrieben: {nfo_path}")
    except Exception as e:
        log.warning(f"NFO Fehler: {e}")

def start_recording(service_ref, title, duration=None, profile=None,
                    path=None, description="", image_url="", client_ip="system",
                    kind=None, episode_title=None, season=None, episode=None, year=None):
    """Startet eine Aufnahme. Gibt recording_id zurück oder wirft Exception.
    
    kind: "movie" | "series" | None (auto-detect via TVDB)
    episode_title: separater Episodentitel (z.B. "Köche aus Köln" wenn title="Das perfekte Dinner")
    """
    rcfg = get_recordings_config()
    rec_path = path or rcfg["path"]
    rec_profile = profile or rcfg["profile"]
    max_dur = duration or rcfg["max_duration"]

    os.makedirs(rec_path, exist_ok=True)

    # Receiver holen — prüfe ob gewünschter Sender bereits läuft (Shared Tuner)
    shared_tuner = False
    rid = None
    ref_clean = service_ref.rstrip("/")
    # Suche Receiver der bereits diesen Sender streamt
    for r in get_receivers():
        if not is_receiver_usable(r):
            continue
        state = _receiver_state.get(r["id"])
        if state and state.get("service_ref", "").rstrip("/") == ref_clean:
            rid = r["id"]
            shared_tuner = True
            log.info(f"Shared tuner: Receiver '{rid}' already streaming {service_ref}")
            break
    # Kein Shared Tuner — normaler freier Receiver
    if not rid:
        rid = get_free_receiver()
    if not rid:
        raise RuntimeError("Kein freier Receiver verfügbar")

    # Receiver sofort acquiren um Race-Condition zu verhindern
    # ABER: bei shared_tuner ist Receiver schon belegt → nicht doppelt acquiren
    if not shared_tuner:
        acquire_receiver(rid, client_ip, service_ref, title or "Aufnahme")

    # Stream-URL bauen:
    # - shared_tuner: direkt vom Receiver (umgeht ref_lock 429, parallele Aufnahmen möglich)
    # - normal: über e2proxy /stream Endpoint (mit Pre-Acquire Marker)
    if shared_tuner:
        receiver_obj = get_receiver_by_id(rid)
        ref_enc_colon = urllib.parse.quote(service_ref, safe=":")
        stream_url = f"http://{receiver_obj['ip']}:{receiver_obj.get('stream_port', 8001)}/{ref_enc_colon}"
        log.info(f"Shared tuner: Recording uses receiver stream directly: {stream_url}")
    else:
        ref_enc = urllib.parse.quote(service_ref, safe="")
        host = get_proxy_host()
        port = get_proxy_port()
        stream_url = f"http://{host}:{port}/stream?ref={ref_enc}&profile={rec_profile}&preacquired={rid}"

    # Klassifikation: Movie oder Serie? Caller-Override hat Vorrang vor TVDB.
    classification = classify_recording(
        title or "Recording",
        episode_title=episode_title,
        force_movie=(kind == "movie"),
        force_series=(kind == "series"),
        season_override=season,
        episode_override=episode,
        year_override=year,
    )
    log.info(
        f"Klassifikation: {classification.get('kind')}" +
        (f" ({classification.get('year')}, Quelle: {classification.get('year_source')})" if classification.get("kind") == "movie" else "") +
        (f" S{classification.get('season')}E{classification.get('episode')}" if classification.get("kind") == "series" else "") +
        (" (TVDB)" if classification.get("kind") == "series" and not classification.get("synthetic") else "") +
        (" (Daily-Show, Datum-Nummerierung)" if classification.get("synthetic") else "")
    )

    # TMDB/EPG-Metadaten-Check vor der Aufnahme
    verify_recording_metadata(title or "Recording", description, classification)

    # Fehlendes Artwork via TMDB-Poster auffüllen
    if not image_url and classification.get("tmdb_poster"):
        image_url = classification["tmdb_poster"]

    # Plex-konformen Pfad bauen: Movies/<Titel>/<Datei>.ts oder TV/<Show>/Season XX/<Datei>.ts
    filepath = build_recording_path(rec_path, title or "Aufnahme", classification, ext="ts")
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    filename = os.path.basename(filepath)

    # ffmpeg Kommando mit Metadata-Embedding
    cmd = [
        "ffmpeg", "-y",
        "-i", stream_url,
        "-c", "copy",
        "-t", str(int(max_dur)),
        # Metadata einbetten
        "-metadata", f"title={title or 'Aufnahme'}",
        "-metadata", f"comment={description or ''}",
        "-metadata", f"date={datetime.now().strftime('%Y-%m-%d')}",
        "-metadata", "encoder=e2proxy",
    ]
    if image_url:
        cmd += ["-metadata", f"artwork={image_url}"]
    cmd.append(filepath)

    rec_id = str(_uuid.uuid4())[:8]

    try:
        proc = _subprocess.Popen(
            cmd,
            stdout=_subprocess.DEVNULL,
            stderr=_subprocess.PIPE,
            text=True
        )
    except FileNotFoundError:
        raise RuntimeError("ffmpeg nicht gefunden — bitte installieren: apt install ffmpeg")

    # Watchdog-Thread: überwacht Datenfluss (früher Abbruch bei 0-Byte/defektem
    # Stream), erzwingt die maximale Laufzeit und garantiert das Aufräumen.
    MIN_BYTES = 188 * 500          # ~94 KB: alles darunter = kein echter Stream
    EARLY_GRACE = 30               # s bis erste Datenfluss-Prüfung
    FATAL_PATTERNS = (
        "server returned 4", "server returned 5", "error opening input",
        "connection refused", "no route to host", "immediate exit requested",
        "invalid data found", "404 not found", "403 forbidden",
    )

    def watchdog():
        stderr_tail = []
        fatal = threading.Event()

        def read_stderr():
            try:
                for line in iter(proc.stderr.readline, ''):
                    line = line.rstrip()
                    if not line:
                        continue
                    stderr_tail.append(line)
                    if len(stderr_tail) > 20:
                        stderr_tail.pop(0)
                    low = line.lower()
                    if any(p in low for p in FATAL_PATTERNS):
                        fatal.set()
            except Exception:
                pass
            finally:
                try:
                    proc.stderr.close()
                except Exception:
                    pass

        threading.Thread(target=read_stderr, daemon=True).start()

        try:
            # Phase 1 — Frühe Fehlererkennung (kanal-unabhängig):
            # kein Datenfluss oder fataler Input-Fehler → sofort abbrechen,
            # statt bis zu max_dur sinnlos weiterzulaufen und Prozesse zu leaken.
            t0 = time.time()
            early_fail = False
            while time.time() - t0 < EARLY_GRACE:
                if proc.poll() is not None:
                    break            # ffmpeg bereits beendet
                if fatal.is_set():
                    early_fail = True
                    break
                time.sleep(2)
            else:
                try:
                    size = os.path.getsize(filepath)
                except OSError:
                    size = 0
                if size < MIN_BYTES:
                    early_fail = True

            if early_fail and proc.poll() is None:
                try:
                    size = os.path.getsize(filepath)
                except OSError:
                    size = 0
                reason = stderr_tail[-1] if stderr_tail else f"kein Datenfluss ({size} Bytes)"
                log.error(f"Recording {rec_id}: Abbruch nach {EARLY_GRACE}s — "
                          f"leerer/defekter Stream ({size} Bytes) — {reason}")
                kill_proc_robust(proc, label=f"Recording {rec_id}")

            # Phase 2 — Normale Laufzeit abwarten, danach robust beenden.
            if proc.poll() is None:
                try:
                    proc.wait(timeout=max_dur + 30)
                except _subprocess.TimeoutExpired:
                    log.warning(f"Recording {rec_id} Watchdog — terminating process")
                    kill_proc_robust(proc, label=f"Recording {rec_id}")
        finally:
            try:
                final_size = os.path.getsize(filepath)
            except OSError:
                final_size = 0
            if final_size < MIN_BYTES:
                with _active_recordings_lock:
                    r = _active_recordings.get(rec_id)
                    if r is not None:
                        r["failed"] = True
                log.error(f"Recording {rec_id} fehlgeschlagen: leere/zu kleine "
                          f"Datei ({final_size} Bytes)")
            _finish_recording(rec_id)

    wt = threading.Thread(target=watchdog, daemon=True)
    wt.start()

    rec = {
        "id": rec_id,
        "service_ref": service_ref,
        "title": title,
        "filename": filename,
        "filepath": filepath,
        "profile": rec_profile,
        "receiver": rid,
        "shared_tuner": shared_tuner,
        "started": datetime.now().isoformat(),
        "duration": max_dur,
        "description": description,
        "image_url": image_url,
        "client_ip": client_ip,
        "pid": proc.pid,
        "proc": proc,
    }
    with _active_recordings_lock:
        _active_recordings[rec_id] = rec

    # KEIN acquire_receiver() hier — ffmpeg akquiriert über /stream selbst.
    # Nur in der Recording-Liste merken damit _finish_recording() nichts doppelt macht.
    log.info(f"Recording started: {rec_id} — {title} → {filepath} (shared={shared_tuner})")

    # NFO Metadaten schreiben mit Sender-Logo
    ch_name_for_nfo = ""
    ch_logo_for_nfo = ""
    try:
        with channel_cache_lock:
            all_ch = channel_cache.get("channels", [])
        ref_clean = service_ref.rstrip("/")
        for ch in all_ch:
            if ch.get("ref", "").rstrip("/") == ref_clean:
                ch_name_for_nfo = ch.get("name", "")
                ch_logo_for_nfo = get_logo_for_epg(ch_name_for_nfo)
                break
    except Exception:
        pass
    # Neue NFO-Schreibung mit voller Plex-konformer Struktur
    build_nfo(filepath, title or "Aufnahme", classification, description=description,
              image_url=image_url, duration_sec=max_dur)

    return rec_id, filepath, rid, shared_tuner, classification

def stop_recording(rec_id):
    """Stoppt eine laufende Aufnahme."""
    with _active_recordings_lock:
        rec = _active_recordings.get(rec_id)
    if not rec:
        raise KeyError(f"Aufnahme {rec_id} nicht gefunden")
    proc = rec.get("proc")
    if proc and proc.poll() is None:
        kill_proc_robust(proc, label=f"Recording {rec_id}")
        log.info(f"Aufnahme gestoppt: {rec_id} — {rec['title']}")
    _finish_recording(rec_id)
    return rec["filepath"]

def _finish_recording(rec_id):
    """Räumt nach Aufnahme auf."""
    with _active_recordings_lock:
        rec = _active_recordings.pop(rec_id, None)
        # Prüfen ob noch andere Aufnahmen denselben Receiver/Sender nutzen
        rid = rec.get("receiver") if rec else None
        ref = rec.get("service_ref", "").rstrip("/") if rec else ""
        still_shared = False
        if rid:
            for other in _active_recordings.values():
                if other.get("receiver") == rid and other.get("service_ref", "").rstrip("/") == ref:
                    still_shared = True
                    break
    if not rec:
        return
    log.info(f"Recording stopped: {rec_id} — {rec['title']} → {rec['filepath']}")
    # Receiver/Stream nur freigeben wenn KEINE andere Aufnahme denselben Stream nutzt
    if rid and not still_shared:
        try:
            kill_stream(rid)
        except Exception as e:
            log.debug(f"kill_stream: {e}")
    elif still_shared:
        log.info(f"Receiver '{rid}' stays busy — another recording uses the same stream")
    # Plex benachrichtigen (+ optionale Verifikation der Aufnahme-Datei)
    rcfg = get_recordings_config()
    if rcfg.get("plex_url") and rcfg.get("plex_token"):
        threading.Thread(target=_plex_notify, args=(rcfg, rec.get("filepath")),
                         kwargs={"label": "Aufnahme"}, daemon=True).start()

def get_recording_status():
    """Gibt Status aller aktiven Aufnahmen zurück."""
    result = []
    with _active_recordings_lock:
        for rec_id, rec in _active_recordings.items():
            proc = rec.get("proc")
            running = proc and proc.poll() is None
            elapsed = int((datetime.now() - datetime.fromisoformat(rec["started"])).total_seconds())
            result.append({
                "id": rec_id,
                "title": rec["title"],
                "filename": rec["filename"],
                "receiver": rec["receiver"],
                "started": rec["started"],
                "elapsed_sec": elapsed,
                "duration_sec": rec["duration"],
                "remaining_sec": max(0, rec["duration"] - elapsed),
                "running": running,
                "profile": rec["profile"],
            })
    return result

def recording_reaper_loop():
    """Sicherheitsnetz gegen verwaiste Aufnahme-Prozesse.

    Läuft periodisch und
      1. räumt Einträge auf, deren ffmpeg bereits gestorben ist, aber noch in
         _active_recordings hängt (falls ein Watchdog-Thread ausgefallen ist),
      2. killt Aufnahmen, die deutlich über ihrer geplanten Laufzeit liegen
         (Wall-Clock-Garantie, unabhängig vom unzuverlässigen ffmpeg -t).
    """
    while True:
        try:
            time.sleep(60)
            now = datetime.now()
            dead = []
            overrun = []
            with _active_recordings_lock:
                for rec_id, rec in list(_active_recordings.items()):
                    proc = rec.get("proc")
                    if proc is None:
                        continue
                    if proc.poll() is not None:
                        dead.append(rec_id)
                        continue
                    try:
                        elapsed = (now - datetime.fromisoformat(rec["started"])).total_seconds()
                    except Exception:
                        elapsed = 0
                    if elapsed > rec.get("duration", 0) + 120:
                        overrun.append((rec_id, proc, elapsed))
            for rec_id, proc, elapsed in overrun:
                log.warning(f"Reaper: Aufnahme {rec_id} überschreitet Laufzeit "
                            f"({int(elapsed)}s) — erzwinge Stopp")
                kill_proc_robust(proc, label=f"Recording {rec_id}")
            for rec_id in dead:
                log.info(f"Reaper: verwaisten Aufnahme-Eintrag {rec_id} aufgeräumt")
                _finish_recording(rec_id)
        except Exception as e:
            log.debug(f"recording_reaper_loop: {e}")


def get_tuner_status():
    """Gibt Tuner-Belegung zurück."""
    receivers = get_receivers()
    result = []
    total = 0
    busy = 0
    for r in receivers:
        if not r.get("enabled", True):
            continue
        locked = is_receiver_locked(r)
        total += 1
        state = _receiver_state.get(r["id"])
        is_busy = state is not None
        if is_busy or locked:
            busy += 1
        result.append({
            "id": r["id"],
            "name": r["name"],
            "busy": is_busy,
            "locked": locked,
            "channel": state.get("channel_name", "") if state else "",
            "client_ip": state.get("client_ip", "") if state else "",
            "since": state.get("started", "") if state else "",
        })
    return {"total": total, "busy": busy, "free": total - busy, "receivers": result}


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
        state = _receiver_state.get(r["id"])
        online = is_receiver_online(r["id"])
        if is_receiver_locked(r):
            color = "#64748b"
            status = "🔒 gesperrt"
            kill_btn = ""
            rx_items.append(
                f'<div class="rx-item">'
                f'<span class="rx-dot" id="rx-dot-{r["id"]}" style="background:{color}"></span>'
                f'<span>{r["name"]}</span>'
                f'<span class="rx-status" id="rx-status-{r["id"]}">{status}</span>'
                f'{kill_btn}'
                f'</div>'
            )
            continue
        if state is None:
            color = "#22c55e" if online else "#ef4444"
            status = "free"
            kill_btn = ""
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
            kill_btn = f'<button class="rx-kill" onclick="killReceiver(\'{r["id"]}\')" title="Stop stream">✕</button>'
        rx_items.append(
            f'<div class="rx-item">'
            f'<span class="{dot_class if state else "rx-dot"}" id="rx-dot-{r["id"]}" style="background:{color}"></span>'
            f'<span>{r["name"]}</span>'
            f'<span class="rx-status" id="rx-status-{r["id"]}">{status}</span>'
            f'{kill_btn}'
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
.rx-status{color:var(--muted);}
.rx-kill{background:none;border:1px solid var(--red);color:var(--red);width:16px;height:16px;border-radius:3px;cursor:pointer;font-size:9px;display:inline-flex;align-items:center;justify-content:center;margin-left:2px;}
.rx-kill:hover{background:var(--red);color:white;}
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

function killReceiver(id) {{
  if (!confirm('Stream abbrechen?')) return;
  fetch('/kill?receiver=' + id).then(r=>r.json()).then(d => {{
    showToast(d.message || 'Stream stopped', d.ok ? 'success' : 'error');
    refreshStatus();
  }});
}}

function refreshStatus() {{
  fetch('/api/status').then(r=>r.json()).then(data => {{
    (data.receivers||[]).forEach(rx => {{
      const dot = document.getElementById('rx-dot-' + rx.id);
      const st  = document.getElementById('rx-status-' + rx.id);
      if (!dot || !st) return;
      // Kill-Button suchen (nächstes Geschwister-Element nach rx-status)
      const killBtn = st.nextElementSibling;
      if (rx.busy && rx.stream) {{
        const isRec = !!rx.recording;
        dot.style.background = isRec ? '#ef4444' : '#f59e0b';
        dot.className = isRec ? 'rx-dot recording' : 'rx-dot';
        const ch = rx.stream.channel_name || '?';
        const cl = rx.stream.client_ip || '?';
        const since = rx.stream.started || '?';
        const recLabel = isRec ? '🔴 REC ' + (rx.recording.title||'') + ' · ' : '';
        st.innerHTML = recLabel + ch + ' · ' + cl + ' · ' + since;
        // Kill-Button einblenden oder erstellen
        if (!killBtn || !killBtn.classList.contains('rx-kill')) {{
          const btn = document.createElement('button');
          btn.className = 'rx-kill'; btn.title = 'Stream abbrechen';
          btn.textContent = '✕';
          btn.onclick = () => killReceiver(rx.id);
          st.parentNode.appendChild(btn);
        }} else {{
          killBtn.style.display = '';
        }}
      }} else {{
        dot.style.background = rx.online ? '#22c55e' : '#ef4444';
        dot.className = 'rx-dot';
        st.textContent = t('rec.tuner_free');
        // Kill-Button entfernen
        if (killBtn && killBtn.classList.contains('rx-kill')) {{
          killBtn.remove();
        }}
      }}
    }});
  }}).catch(()=>{{}});
}}

setInterval(refreshStatus, 5000);
</script>

</script>
"""
    return html_page("Live TV", body, css)


# ── API Access Log ─────────────────────────────────────────────────────────────
API_ACCESS_LOG_FILE = f"{DATA_DIR}/api_access.log"
API_ACCESS_LOG_MAX  = 1000  # max Einträge

def is_api_logging_enabled():
    return get_config().get("api_logging", False)

def write_access_log(method, path, client_ip, status, duration_ms, params=""):
    if not is_api_logging_enabled():
        return
    try:
        # Tägliche Rotation: am Mitternacht-Wechsel rotieren
        _rotate_access_log_if_needed()
        entry = json.dumps({
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "method": method,
            "path": path,
            "params": params[:200] if params else "",
            "ip": client_ip,
            "status": status,
            "ms": duration_ms,
        }, ensure_ascii=False)
        # Reines Append — kein Read-Modify-Write
        os.makedirs(os.path.dirname(API_ACCESS_LOG_FILE), exist_ok=True)
        with open(API_ACCESS_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception as e:
        log.debug(f"Access-Log Fehler: {e}")

_access_log_last_rotate = [None]  # Datum des letzten Rotate-Checks

def _rotate_access_log_if_needed():
    """Bei Datumswechsel: aktuelle Datei in api_access.log.YYYY-MM-DD umbenennen.
    Löscht alte Dateien gemäß log_retention_days."""
    import datetime as _dt, glob
    today = _dt.date.today()
    if _access_log_last_rotate[0] == today:
        return
    _access_log_last_rotate[0] = today
    try:
        if not os.path.exists(API_ACCESS_LOG_FILE):
            return
        # Datum der ersten Zeile prüfen → wenn nicht heute, rotieren
        with open(API_ACCESS_LOG_FILE, encoding="utf-8") as f:
            first_line = f.readline().strip()
        if not first_line:
            return
        try:
            entry = json.loads(first_line)
            file_date = entry["ts"][:10]  # YYYY-MM-DD
        except Exception:
            return
        if file_date != today.strftime("%Y-%m-%d"):
            # Rotieren: aktuelle Datei → api_access.log.<file_date>
            rotated = f"{API_ACCESS_LOG_FILE}.{file_date}"
            os.rename(API_ACCESS_LOG_FILE, rotated)
            log.info(f"API access log rotated: {os.path.basename(rotated)}")
        # Alte Dateien löschen
        retention = int(get_config().get("log_retention_days", 5))
        cutoff = today - _dt.timedelta(days=retention)
        for fp in glob.glob(f"{API_ACCESS_LOG_FILE}.*"):
            try:
                date_str = fp.split(".")[-1]
                fd = _dt.datetime.strptime(date_str, "%Y-%m-%d").date()
                if fd < cutoff:
                    os.remove(fp)
                    log.info(f"API access log deleted (älter als {retention} Tage): {os.path.basename(fp)}")
            except Exception:
                pass
    except Exception as e:
        log.debug(f"Access-Log Rotation Fehler: {e}")


def read_access_log(n=200, since_unix=None):
    """Liest API Access Log inkl. rotierter Dateien. 
    
    n: max Anzahl (None = alle)
    since_unix: nur Einträge nach diesem Timestamp
    """
    import datetime as _dt, glob
    try:
        # Wenn since_unix gesetzt → alle rotierten Dateien einbeziehen
        # Sonst nur die aktuelle (für Live-Polling)
        if since_unix is not None:
            files = sorted(glob.glob(f"{API_ACCESS_LOG_FILE}*"))
        else:
            files = [API_ACCESS_LOG_FILE] if os.path.exists(API_ACCESS_LOG_FILE) else []
        all_lines = []
        for fp in files:
            try:
                with open(fp, encoding="utf-8") as f:
                    all_lines.extend(f.readlines())
            except Exception:
                continue
        entries = []
        for line in reversed(all_lines):
            try:
                e = json.loads(line.strip())
                if since_unix is not None:
                    try:
                        ts_unix = _dt.datetime.strptime(e["ts"], "%Y-%m-%d %H:%M:%S").timestamp()
                        if ts_unix < since_unix:
                            continue
                        e["ts_unix"] = ts_unix
                    except Exception:
                        continue
                else:
                    try:
                        e["ts_unix"] = _dt.datetime.strptime(e["ts"], "%Y-%m-%d %H:%M:%S").timestamp()
                    except Exception:
                        e["ts_unix"] = 0
                entries.append(e)
                if n is not None and len(entries) >= n:
                    break
            except Exception:
                pass
        return entries
    except Exception:
        return []


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
          GET /api/channels<br>GET /api/favorites<br>GET /api/epg/data<br>GET /api/epg/status<br>GET /api/epg/run<br>GET /api/epg/history<br>GET /api/health
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
          <b style="color:var(--accent);font-family:monospace;font-size:11px">v3.8.0</b>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">2026-07-13</span>
          <span style="color:var(--muted);font-size:10px;margin-left:8px">Editable favorite logos</span>
          <div style="font-size:11px;margin-top:4px;color:var(--muted)">Favorite channel logos can now be edited under <b>Settings → Maintenance</b>. For each favorite you can upload an image or enter a URL; the image is converted with ffmpeg to the correct format (PNG, max 400px wide, aspect preserved) and stored locally. Custom logos take precedence over the built-in logo database and cache for both the M3U playlist and the XMLTV EPG, and can be reset to fall back to the automatic logo. New endpoints <code>/api/favorites/logos</code>, <code>/api/favorites/logo</code> and <code>/api/favorites/logo/reset</code>; custom logos are served at <code>/custom_logos/</code> and stored in <code>/data/custom_logos/</code>.</div>
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


def build_favorites_ui(channels):
    all_json = json.dumps([{"ref": c["ref"], "name": c["name"], "bouquet": c.get("bouquet","")} for c in channels], ensure_ascii=False)
    # Auto-Repair: Favoriten-Namen aus Channel-Cache korrigieren
    # Refs normalisieren: Favoriten nutzen _ (Underscores), Channels nutzen : (Doppelpunkte)
    def _norm_ref(r):
        return r.replace(":", "_").rstrip("_").rstrip("/")
    all_by_ref = {_norm_ref(c["ref"]): c["name"] for c in channels}
    raw_favs = get_favorites()
    repaired = 0
    for fav in raw_favs:
        ref_norm = _norm_ref(fav.get("ref", ""))
        stored_name = fav.get("name", "")
        correct_name = all_by_ref.get(ref_norm)
        # Repariere wenn: Name fehlt, zu kurz, ODER nicht mit Channel-Cache übereinstimmt
        if correct_name and stored_name != correct_name and (len(stored_name) <= 3 or stored_name != correct_name):
            if correct_name != stored_name:
                fav["name"] = correct_name
                repaired += 1
    if repaired:
        save_favorites(raw_favs)
        log.info(f"Favorites names repaired: {repaired} entries corrected")
    favs_json = json.dumps(raw_favs, ensure_ascii=False)

    css = """
.main{display:flex;height:calc(100vh - 48px);overflow:hidden;}
.panel{flex:1;display:flex;flex-direction:column;border-right:1px solid var(--border);overflow:hidden;}
.panel-hdr{padding:10px 14px;background:var(--surface);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;}
.panel-title{font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:700;color:var(--accent);letter-spacing:1px;text-transform:uppercase;}
.panel-count{font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--muted);}
.search-input{flex:1;background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:5px 8px;border-radius:4px;font-size:12px;outline:none;}
.search-input:focus{border-color:var(--accent);}
.ch-list{flex:1;overflow-y:auto;padding:3px 6px;}
.ch-list::-webkit-scrollbar{width:3px;}
.ch-list::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px;}
.ch-item{display:flex;align-items:center;justify-content:space-between;padding:6px 6px;border-radius:4px;transition:background 0.1s;}
.ch-item:hover{background:var(--surface2);}
.ch-item.is-fav{opacity:0.35;}
.ch-item.hidden{display:none;}
.ch-name{font-size:12px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.ch-bouquet{font-size:9px;color:var(--muted);font-family:'JetBrains Mono',monospace;margin-left:6px;flex-shrink:0;}
.btn-add{background:none;border:1px solid var(--border);color:var(--green);width:22px;height:22px;border-radius:3px;cursor:pointer;font-size:13px;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-left:6px;transition:all 0.1s;}
.btn-add:hover{background:var(--green);color:white;border-color:var(--green);}
.btn-add:disabled{opacity:0.25;cursor:default;}
.fav-panel{width:340px;flex-shrink:0;display:flex;flex-direction:column;overflow:hidden;}
.fav-list{flex:1;overflow-y:auto;padding:3px 6px;}
.fav-list::-webkit-scrollbar{width:3px;}
.fav-list::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px;}
.fav-item{display:flex;align-items:center;gap:6px;padding:6px 6px;border-radius:4px;background:var(--surface2);margin-bottom:2px;cursor:grab;border:1px solid transparent;transition:all 0.1s;user-select:none;}
.fav-item:hover{border-color:var(--border);}
.fav-item.dragging{opacity:0.45;border-color:var(--accent);}
.fav-item.drag-over{border-color:var(--accent);background:var(--surface);}
.drag-handle{color:var(--muted);font-size:13px;cursor:grab;flex-shrink:0;}
.fav-name{font-size:12px;flex:1 1 auto;min-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.fav-num{font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--muted);width:18px;flex-shrink:0;text-align:right;}
.btn-remove{background:none;border:1px solid transparent;color:var(--red);width:20px;height:20px;border-radius:3px;cursor:pointer;font-size:11px;display:flex;align-items:center;justify-content:center;flex-shrink:0;opacity:0;transition:all 0.1s;}
.fav-group-sel{background:var(--surface);border:1px solid var(--border);color:var(--muted);padding:2px 4px;border-radius:3px;font-size:9px;font-family:'JetBrains Mono',monospace;cursor:pointer;outline:none;flex-shrink:0;width:80px;}
.fav-item:hover .btn-remove{opacity:1;}
.btn-remove:hover{background:var(--red);color:white;border-color:var(--red);}
.empty-fav{padding:30px 16px;text-align:center;color:var(--muted);font-size:12px;font-family:'JetBrains Mono',monospace;}
@media(max-width:700px){.main{flex-direction:column;height:auto;}.fav-panel{width:100%;border-top:1px solid var(--border);height:50vh;}.panel{height:50vh;}}
"""

    body = f"""
<div class="header">
  <a class="logo" href="/" style="text-decoration:none">e2<span>proxy</span></a>
  <div class="header-right">
    <a class="btn" href="/" data-i18n="nav.mainpage">← Main page</a>
    <a class="btn" href="/playlist.m3u?profile=Web-SD&list=favorites" data-i18n="fav.export_m3u">↓ M3U Favorites</a>
    <button class="btn btn-primary" onclick="saveFavorites()">💾 <span data-i18n="common.save">Save</span></button>
  </div>
</div>
<div class="main">
  <div class="panel">
    <div class="panel-hdr">
      <span class="panel-title" data-i18n="fav.all_channels">ALL CHANNELS</span>
      <span class="panel-count" id="all-count"></span>
      <input class="search-input" id="search" type="text" data-i18n-ph="common.search" placeholder="Search…" oninput="filterCh(this.value)" autocomplete="off">
    </div>
    <div class="ch-list" id="all-channels"></div>
  </div>
  <div class="fav-panel">
    <div class="panel-hdr">
      <span class="panel-title">⭐ <span data-i18n="fav.title">Favorites</span></span>
      <span class="panel-count" id="fav-count">0</span>
    </div>
    <div class="fav-list" id="fav-list">
      <div class="empty-fav" id="fav-empty">Noch keine Favoriten.<br>+ drücken zum Hinzufügen.</div>
    </div>
  </div>
</div>
<script>
const ALL = {all_json};
let favs = {favs_json};
let dragSrcIdx = null;

function getFavRefs() {{ return new Set(favs.map(f=>f.ref)); }}

function renderAll(filter) {{
  const favRefs = getFavRefs();
  const cont = document.getElementById('all-channels');
  const q = (filter||'').toLowerCase().trim();
  cont.innerHTML = '';
  let visible = 0;
  ALL.forEach(ch => {{
    if (q && !ch.name.toLowerCase().includes(q) && !ch.bouquet.toLowerCase().includes(q)) return;
    visible++;
    const isFav = favRefs.has(ch.ref);
    const div = document.createElement('div');
    div.className = 'ch-item' + (isFav ? ' is-fav' : '');
    div.dataset.ref = ch.ref;
    div.innerHTML = `<span class="ch-name">${{ch.name}}</span><span class="ch-bouquet">${{ch.bouquet.replace(/_/g,' ')}}</span><button class="btn-add" onclick="addFav('${{ch.ref}}','${{ch.name.replace(/'/g,"\\\\'")}}')" ${{isFav?'disabled title="Bereits Favorit"':'title="Hinzufügen"'}}>+</button>`;
    cont.appendChild(div);
  }});
  document.getElementById('all-count').textContent = visible + ' Sender';
}}

const GROUPS = ['', 'Hauptprogramme', '\u00d6ffentlich-Rechtlich', 'Nachrichten', 'Sport', 'Entertainment', 'Doku', 'Musik', 'Kinder', '\u00d6sterreich', 'International'];
const CATEGORIES = [
  {{v:'',       l:'— Kategorie —'}},
  {{v:'series',       l:'📺 Serie'}},
  {{v:'movie',        l:'🎬 Film'}},
  {{v:'news',         l:'📰 Nachrichten'}},
  {{v:'sports',       l:'⚽ Sport'}},
  {{v:'kids',         l:'🧒 Kinder'}},
  {{v:'talk',         l:'🎤 Talkshow'}},
  {{v:'reality',      l:'👥 Reality'}},
  {{v:'documentary',  l:'🎥 Dokumentation'}},
  {{v:'music',        l:'🎵 Musik'}},
];

function renderFavs() {{
  const list = document.getElementById('fav-list');
  Array.from(list.children).forEach(el => {{ if (el.id !== 'fav-empty') el.remove(); }});
  const empty = document.getElementById('fav-empty');
  if (favs.length === 0) {{ empty.style.display = 'block'; document.getElementById('fav-count').textContent = '0'; return; }}
  empty.style.display = 'none';
  favs.forEach((fav, idx) => {{
    const div = document.createElement('div');
    div.className = 'fav-item';
    div.draggable = true;
    const grpOpts = GROUPS.map(g => `<option value="${{g}}" ${{(fav.group||'')==g?'selected':''}}>` + (g || '\u2014 Gruppe \u2014') + `</option>`).join('');
    const catOpts = CATEGORIES.map(c => `<option value="${{c.v}}" ${{(fav.category||'')==c.v?'selected':''}}>${{c.l}}</option>`).join('');
    div.innerHTML = `<span class="drag-handle">\u2837</span><span class="fav-num">${{idx+1}}</span><span class="fav-name">${{fav.name}}</span><select class="fav-group-sel" onchange="setGroup(${{idx}},this.value)" title="Gruppe">${{grpOpts}}</select><select class="fav-group-sel" onchange="setCategory(${{idx}},this.value)" title="EPG-Kategorie" style="max-width:120px">${{catOpts}}</select><button class="btn-remove" onclick="removeFav(${{idx}})">\u2715</button>`;
    div.addEventListener('dragstart', e => {{ dragSrcIdx=idx; div.classList.add('dragging'); e.dataTransfer.effectAllowed='move'; }});
    div.addEventListener('dragend', () => div.classList.remove('dragging'));
    div.addEventListener('dragover', e => {{ e.preventDefault(); div.classList.add('drag-over'); }});
    div.addEventListener('dragleave', () => div.classList.remove('drag-over'));
    div.addEventListener('drop', e => {{
      e.preventDefault(); div.classList.remove('drag-over');
      if (dragSrcIdx !== null && dragSrcIdx !== idx) {{
        const moved = favs.splice(dragSrcIdx, 1)[0];
        favs.splice(idx, 0, moved);
        dragSrcIdx = null;
        renderFavs(); renderAll(document.getElementById('search').value);
      }}
    }});
    list.appendChild(div);
  }});
  document.getElementById('fav-count').textContent = favs.length;
}}

function setGroup(idx, group) {{
  favs[idx].group = group;
}}
function setCategory(idx, cat) {{
  favs[idx].category = cat;
}}

function addFav(ref, name) {{
  if (getFavRefs().has(ref)) return;
  favs.push({{ref, name}});
  renderFavs(); renderAll(document.getElementById('search').value);
}}
function removeFav(idx) {{
  favs.splice(idx, 1);
  renderFavs(); renderAll(document.getElementById('search').value);
}}
function filterCh(q) {{ renderAll(q); }}
function saveFavorites() {{
  apiPost('/api/favorites', favs).then(d => {{
    showToast(d.ok ? '✓ Gespeichert!' : 'Fehler: '+(d.message||'?'), d.ok ? 'success' : 'error');
  }});
}}
renderAll(); renderFavs();
</script>
"""
    return html_page("Favoriten", body, css)


def build_epg_browser():
    proxy_host = get_proxy_host()
    proxy_port = get_proxy_port()

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
</style>
{I18N_JS}
</head>
<body>
{body}
<div class="toast" id="toast"></div>
<script>
function showToast(msg,type){{const t=document.getElementById('toast');t.textContent=msg;t.className='toast show'+(type?' '+type:'');setTimeout(()=>t.className='toast',3000);}}
function apiPost(url,data){{return fetch(url,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(data)}}).then(r=>r.json());}}
function toggleTheme(){{const isLight=document.documentElement.getAttribute('data-theme')==='light';if(isLight){{document.documentElement.removeAttribute('data-theme');localStorage.setItem('e2proxy-theme','dark');}}else{{document.documentElement.setAttribute('data-theme','light');localStorage.setItem('e2proxy-theme','light');}}}}
</script>
</body>
</html>"""


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
    <button class="tab-btn active" onclick="switchTab('config')">⚙ <span data-i18n="set.tab_config">Configuration</span></button>
    <button class="tab-btn" onclick="switchTab('maintenance')">🔧 <span data-i18n="set.tab_maint">Maintenance</span></button>
    <button class="tab-btn" onclick="switchTab('epg')">📅 EPG</button>
    <button class="tab-btn" onclick="switchTab('recording')">📹 <span data-i18n="set.tab_rec">Recordings</span></button>
    <button class="tab-btn" onclick="switchTab('help')">📖 <span data-i18n="set.tab_api">API</span> / <span data-i18n="nav.help">Help</span></button>
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
          <button class="btn" onclick="loadSwitchStats()">↺ <span data-i18n="common.refresh">Refresh</span></button>
          <button class="btn" onclick="resetSwitch(null)">🗑 <span data-i18n="set.switch_reset_all">Reset alle</span></button>
        </div>
      </div>
      <div id="sw-stats" style="margin-top:10px;overflow-x:auto">Lade…</div>
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
        </tbody>
      </table>
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

  <!-- ── TAB: WARTUNG ──────────────────────────────── -->
  <div class="tab-panel" id="tab-maintenance">

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
        <span style="color:var(--muted);font-size:10px">PROXY&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</span> http://{proxy_host}:{proxy_port}<br>
        <span style="color:var(--muted);font-size:10px">PLEX DVR&nbsp;</span> http://{proxy_host}:{proxy_port}/plex<br>
        <span style="color:var(--muted);font-size:10px">EPG&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</span> http://{proxy_host}:{proxy_port}/epg.xml
      </div>
    </div>

  </div>

</div>

<script>
const ORIGINAL_CONFIG = {cfg_json};

function switchTab(name) {{
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  event.target.classList.add('active');
  if (name === 'maintenance') {{ refreshLogs(); initApiLogToggle(); initMaintNotify(); loadFavLogos(); }}
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
  if (name === 'recording') {{ loadTunerStatus(); loadRecordingStatus(); loadRecChannels(); loadCompressionStatus(); loadSwitchStats(); }}
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
if (document.getElementById('tab-maintenance').classList.contains('active')) {{ refreshLogs(); initApiLogToggle(); initMaintNotify(); loadFavLogos(); }}

// ── Recording ─────────────────────────────────────────
let _quickRecId = null;
let _epgChannels = [];
let _testRecId = null;

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
        ? '<img src="' + l.logo_url + '" title="' + t('set.fav_logos_zoom') + '" onclick="showLogoModal(this.src, this.getAttribute(\'data-nm\'))" data-nm="' + nm + '" style="width:52px;height:32px;object-fit:contain;background:var(--surface2);border:1px solid var(--border);border-radius:4px;cursor:zoom-in" onerror="this.style.opacity=0.2">'
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

# ── HDHomeRun Emulation ───────────────────────────────────

HDHR_DEVICE_ID   = "E2PROXY1"
HDHR_DEVICE_AUTH = "e2proxy"
HDHR_FIRMWARE    = "20200521"

def build_hdhr_discover():
    """discover.json — Plex fragt das beim Setup ab."""
    host = get_proxy_host()
    port = get_proxy_port()
    base = f"http://{host}:{port}"
    tuner_count = len([r for r in get_receivers() if is_receiver_usable(r)])
    return {
        "FriendlyName":    "e2proxy (Enigma2)",
        "Manufacturer":    "e2proxy",
        "ModelNumber":     "HDHR4-2DT",
        "FirmwareName":    "hdhomerun4_dvbt",
        "FirmwareVersion": HDHR_FIRMWARE,
        "DeviceID":        HDHR_DEVICE_ID,
        "DeviceAuth":      HDHR_DEVICE_AUTH,
        "TunerCount":      tuner_count,
        "BaseURL":         base,
        "LineupURL":       f"{base}/lineup.json",
    }

def ref_to_channel_id(ref):
    """
    Konvertiert Enigma2 Service-Ref in EPG Channel-ID Format.
    '1:0:19:283D:3FB:1:C00000:0:0:0:' → '1_0_19_283D_3FB_1_C00000_0_0_0_'
    Plex verwendet diese ID als Tuning-Adresse und matcht damit die EPG Channel-ID.
    """
    return ref.replace(":", "_")

def _build_hdhr_lineup_for_profile(profile_name):
    """Interne Hilfsfunktion: lineup.json fuer ein bestimmtes Profil."""
    host = get_proxy_host()
    port = get_proxy_port()
    channels = get_channels()
    lineup = []
    for ch in channels:
        ref_enc = urllib.parse.quote(ch["ref"], safe="")
        channel_id = ref_to_channel_id(ch["ref"])
        stream_url = (
            f"http://{host}:{port}/stream"
            f"?ref={ref_enc}&profile={urllib.parse.quote(profile_name)}"
        )
        lineup.append({
            "GuideNumber": channel_id,
            "GuideName":   ch["name"],
            "URL":         stream_url,
        })
    return lineup

def build_hdhr_lineup():
    """lineup.json — liefert Favoriten mit profile=plex.
    Plex folgt immer der LineupURL aus discover.json, daher
    muss dieser Endpoint direkt die richtigen Daten liefern.
    """
    return build_hdhr_lineup_plex()

def build_hdhr_lineup_plex():
    """lineup.json fuer den /plex Endpoint.
    Nur Favoriten — verhindert verschluesselte Sender und reduziert Liste.
    Verwendet profile=plex fuer chunked HTTP/1.1 Streaming.
    """
    host = get_proxy_host()
    port = get_proxy_port()
    # Nur Favoriten
    favs = get_favorites()
    fav_refs = {f["ref"] for f in favs}
    channels = [ch for ch in get_channels() if ch["ref"] in fav_refs]
    # Reihenfolge aus Favoriten-Liste beibehalten
    fav_order = {f["ref"]: i for i, f in enumerate(favs)}
    channels.sort(key=lambda ch: fav_order.get(ch["ref"], 9999))
    lineup = []
    for idx, ch in enumerate(channels, start=1):
        ref_enc = urllib.parse.quote(ch["ref"], safe="")
        stream_url = (
            f"http://{host}:{port}/stream"
            f"?ref={ref_enc}&profile=plex"
        )
        lineup.append({
            "GuideNumber": str(idx),   # Numerisch → Plex sortiert in unserer Reihenfolge
            "GuideName":   ch["name"],
            "URL":         stream_url,
        })
    return lineup

def build_hdhr_lineup_status():
    """lineup_status.json — Plex prüft damit ob das Gerät bereit ist."""
    return {
        "ScanInProgress": 0,
        "ScanPossible":   0,
        "Source":         "Cable",
        "SourceList":     ["Cable"],
    }

def build_hdhr_device_xml():
    """device.xml — UPnP Capability-Beschreibung."""
    host = get_proxy_host()
    port = get_proxy_port()
    base = f"http://{host}:{port}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <URLBase>{base}</URLBase>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>
    <friendlyName>e2proxy (Enigma2)</friendlyName>
    <manufacturer>e2proxy</manufacturer>
    <modelName>HDHR4-2DT</modelName>
    <modelNumber>HDHR4-2DT</modelNumber>
    <serialNumber>{HDHR_DEVICE_ID}</serialNumber>
    <UDN>uuid:e2proxy-{HDHR_DEVICE_ID}</UDN>
  </device>
</root>"""


# ── SSDP Discovery Server ─────────────────────────────────

SSDP_MCAST_ADDR = "239.255.255.250"
SSDP_PORT       = 1900

def start_ssdp_server():
    """
    Horcht auf UDP Multicast Port 1900 auf M-SEARCH Pakete von Plex.
    Antwortet mit LOCATION die auf device.xml auf dem e2proxy Port zeigt.
    Plex findet damit den Proxy automatisch ohne manuelle IP-Eingabe.
    Läuft als Daemon-Thread — kein root nötig (Port 1900 > 1024).
    """
    host     = get_proxy_host()
    port     = get_proxy_port()
    location = f"http://{host}:{port}/device.xml"
    usn      = f"uuid:e2proxy-{HDHR_DEVICE_ID}::upnp:rootdevice"

    ssdp_response = (
        "HTTP/1.1 200 OK\r\n"
        "CACHE-CONTROL: max-age=1800\r\n"
        "EXT:\r\n"
        f"LOCATION: {location}\r\n"
        "SERVER: Linux/1.0 UPnP/1.0 e2proxy/1.0\r\n"
        "ST: upnp:rootdevice\r\n"
        f"USN: {usn}\r\n"
        "\r\n"
    ).encode("utf-8")

    def _serve():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except AttributeError:
                pass  # SO_REUSEPORT nicht auf allen Systemen verfügbar

            sock.bind(("", SSDP_PORT))

            # Multicast Gruppe beitreten
            mreq = struct.pack("4sL",
                socket.inet_aton(SSDP_MCAST_ADDR),
                socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

            log.info(f"SSDP: Horche auf {SSDP_MCAST_ADDR}:{SSDP_PORT} — LOCATION: {location}")

            while True:
                try:
                    data, addr = sock.recvfrom(4096)
                    msg = data.decode("utf-8", errors="ignore")

                    # Nur auf M-SEARCH antworten die nach rootdevice oder all suchen
                    if "M-SEARCH" not in msg:
                        continue
                    if not any(st in msg for st in ("ssdp:all", "upnp:rootdevice")):
                        continue

                    log.debug(f"SSDP: M-SEARCH von {addr[0]} — antworte mit {location}")
                    sock.sendto(ssdp_response, addr)

                except Exception as e:
                    log.warning(f"SSDP: Fehler beim Empfangen: {e}")

        except OSError as e:
            log.error(f"SSDP: Konnte Port {SSDP_PORT} nicht binden: {e}")
            log.error("SSDP: Automatische Discovery deenabled — manuelle IP-Eingabe in Plex verwenden")

    t = threading.Thread(target=_serve, daemon=True, name="ssdp-server")
    t.start()
    return t


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

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length)) if length > 0 else {}
        except Exception:
            data = {}

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
        try:
            self._do_GET_inner()
            _dur = int((_t.time() - _t0) * 1000)
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
            self.send_json({"version": VERSION, "service": "e2proxy"})
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

        if path == "/api/health":
            # Schneller Health-Check für e2recorder (kein EPG-Overhead)
            receivers_health = []
            for r in get_receivers():
                if not r.get("enabled", True):
                    continue
                locked = is_receiver_locked(r)
                state = _receiver_state.get(r["id"])
                receivers_health.append({
                    "id":     r["id"],
                    "name":   r["name"],
                    "online": True,
                    "busy":   state is not None,
                    "locked": locked,
                    "channel": state.get("channel_name", "") if state else "",
                })
            self.send_json({
                "ok":          True,
                "version":     "2.1.0",
                "uptime_sec":  int(time.time() - _proxy_start_time),
                "receivers":   receivers_health,
                "tuners_free": sum(1 for r in receivers_health if not r["busy"] and not r["locked"]),
            })
            return

        if path == "/api/status":
            rx_status = []
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
                state = _receiver_state.get(r["id"])
                rx_status.append({
                    "id": r["id"],
                    "name": r["name"],
                    "busy": state is not None,
                    "online": is_receiver_online(r["id"]),
                    "stream": state,
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

        if path == "/api/favorites":
            self.send_json(get_favorites())
            return

        if path == "/api/channels/reload":
            channels = get_channels(force_refresh=True)
            self.send_json({"ok": True, "count": len(channels)})
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
                        from datetime import datetime, timezone
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
            preacquired = params.get("preacquired", [None])[0]
            if preacquired:
                rid = preacquired
                log.debug(f"Stream: Receiver '{rid}' bereits durch Aufnahme belegt — überspringe acquire")
            else:
                rid = get_free_receiver(receiver_pref if receiver_pref != "auto" else None)
                if not rid:
                    self.send_text("All receivers busy", 503)
                    return

            # Dedup-Lock NUR für Live-Anfragen (z.B. Jellyfin Probe + Stream, die fast
            # gleichzeitig denselben Sender öffnen). Aufnahmen (preacquired) sind bereits
            # über den Receiver koordiniert (jede belegt einen eigenen Tuner) und dürfen
            # Live-Zuschauer desselben Senders NICHT blockieren — sonst sperrt jede
            # laufende oder hängende VOX-Aufnahme das Live-Schauen von VOX (429).
            ref_lock = None
            if not preacquired:
                ref_lock = get_ref_lock(service_ref)
                if not ref_lock.acquire(blocking=False):
                    log.warning(f"Duplicate request for {service_ref} — rejected (429)")
                    self.send_text("Stream für diesen Sender läuft bereits", 429)
                    return
                acquire_receiver(rid, client_ip, service_ref, channel_name)

            try:
                if not do_zap(rid, service_ref, channel_name):
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
                release_receiver(rid)
                if ref_lock is not None:
                    ref_lock.release()
            return

        self.send_text("Not found", 404)


# ── Main ──────────────────────────────────────────────────

_proxy_start_time = time.time()

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
    global _proxy_start_time
    _proxy_start_time = time.time()
    os.makedirs(DATA_DIR, exist_ok=True)
    _raise_open_files_limit()
    load_config()
    _setup_file_logging()   # nach load_config — damit retention aus Config kommt
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
    start_compression_scheduler()

    proxy_host = get_proxy_host()
    proxy_port = get_proxy_port()

    # SSDP Discovery starten (Plex findet den Proxy automatisch)
    start_ssdp_server()

    server = http.server.ThreadingHTTPServer(("0.0.0.0", proxy_port), ProxyHandler)

    log.info("=" * 55)
    log.info(f"e2proxy v{VERSION} started")
    log.info(f"Web-UI:    http://{proxy_host}:{proxy_port}/")
    log.info(f"Settings:  http://{proxy_host}:{proxy_port}/settings")
    log.info(f"Config:    {CONFIG_FILE}")
    log.info(f"Receiver:  {', '.join(r['name'] for r in get_receivers())}")
    log.info(f"Plex DVR:  http://{proxy_host}:{proxy_port}/discover.json")
    log.info(f"SSDP:      UDP {SSDP_MCAST_ADDR}:{SSDP_PORT} (auto-discovery)")
    log.info("=" * 55)

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


if __name__ == "__main__":
    run()
