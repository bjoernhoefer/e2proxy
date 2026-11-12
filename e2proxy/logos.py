"""Channel logos: built-in database, local cache and custom (user-set) logos."""
import json
import os
import re
import subprocess
import threading
import urllib.parse
import urllib.request

from e2proxy.paths import DATA_DIR
from e2proxy.logs import log
from e2proxy.config import get_proxy_host, get_proxy_port
from e2proxy.favorites import load_favorites
from e2proxy.channels import channel_cache, channel_cache_lock

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

def _looks_like_html(data):
    head = data[:512].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<head" in head

def _fetch_image_bytes(url, max_bytes=8 * 1024 * 1024):
    """Lädt ein Bild von einer URL herunter (begrenzt auf max_bytes)."""
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("Nur http(s)-URLs erlaubt")
    req = urllib.request.Request(url, headers={"User-Agent": "e2proxy/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("Bild zu groß (max 8 MB)")
    if len(data) < 100:
        raise ValueError("Bild leer oder ungültig")
    if "text/html" in ctype or _looks_like_html(data):
        raise ValueError("URL liefert eine Webseite, kein Bild. Bitte die direkte "
                         "Bild-URL verwenden (Rechtsklick aufs Bild, Bildadresse kopieren).")
    return data

def convert_and_store_custom_logo(name, img_bytes):
    """Konvertiert ein beliebiges Bild (inkl. SVG) via ffmpeg nach PNG
    (max. 400px breit, Seitenverhältnis erhalten) und speichert es als
    Custom-Logo für 'name'. Wirft bei Fehler eine Exception."""
    if not name:
        raise ValueError("Sendername fehlt")
    if not img_bytes or len(img_bytes) < 100:
        raise ValueError("Kein Bild empfangen")
    if _looks_like_html(img_bytes):
        raise ValueError("Die Datei ist eine HTML-Seite, kein Bild.")
    os.makedirs(CUSTOM_LOGO_DIR, exist_ok=True)
    out = custom_logo_path(name)
    tmp = out + ".tmp.png"
    # ffmpeg über eine echte (seekbare) Datei statt pipe:0 füttern — nur so
    # erkennt der Demuxer Formate wie SVG zuverlässig.
    tmp_in = out + ".in"
    try:
        with open(tmp_in, "wb") as f:
            f.write(img_bytes)
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", tmp_in,
            "-vf", "scale='if(gt(iw,400),400,iw)':-2",
            "-frames:v", "1",
            "-f", "image2", tmp,
        ]
        try:
            proc = subprocess.run(cmd,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=30)
        except FileNotFoundError:
            raise RuntimeError("ffmpeg nicht gefunden")
        except subprocess.TimeoutExpired:
            raise RuntimeError("Bildkonvertierung Timeout")
        if proc.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) < 100:
            err = (proc.stderr or b"").decode("utf-8", "replace").strip()
            err_short = err.splitlines()[-1][:180] if err else "unbekannter Fehler"
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            raise RuntimeError(f"Bild konnte nicht konvertiert werden ({err_short})")
    finally:
        try:
            if os.path.exists(tmp_in):
                os.remove(tmp_in)
        except OSError:
            pass
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


