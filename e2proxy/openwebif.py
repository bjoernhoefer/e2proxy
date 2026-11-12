"""OpenWebif emulation: e2proxy impersonates an Enigma2 box for native apps."""
import http.server
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from e2proxy import probe as e2probe

from e2proxy.logs import log
from e2proxy.config import get_owif_config, get_receivers, get_transcode_profile
from e2proxy.favorites import load_favorites
from e2proxy.channels import BOUQUETS, get_channels
from e2proxy.switching import get_switch_settings
from e2proxy.receivers import (
    acquire_receiver,
    attach_receiver,
    get_free_receiver,
    is_receiver_unhealthy,
    receiver_lock,
    release_receiver,
    zap_with_failover,
)
from e2proxy.streaming import (
    ScrambledStreamError,
    build_ffmpeg_cmd,
    stream_passthrough,
    stream_transcoded,
)
from e2proxy.usage import classify_endpoint, note_endpoint, record_usage_session
from e2proxy.recordings import get_recordings_config
from e2proxy import receivers as receivers_mod

# ── OpenWebif-Emulation ("virtuelle Enigma2-Box") ─────────
# Eigener HTTP-Listener, der eine Enigma2-Box imitiert. Enigma2-Clients
# (Dream Player, Kodi, VU+-Apps) werden auf diesen Port konfiguriert und glauben,
# mit einer echten Box zu reden.
#   • Metadaten/EPG-Endpunkte (/api/*, /web/*, /picon/*, /grab, ...) → Reverse-
#     Proxy an einen erreichbaren Receiver → authentische Antworten inkl. EPG.
#   • Stream-Pfad (/<serviceRef>) → intercept: e2proxy wählt einen freien Tuner
#     (Orchestrierung) und reicht das rohe TS durch (Passthrough, kein ffmpeg);
#     per User-Agent kann ein abweichendes Transcode-Profil erzwungen werden.
# Plex bleibt davon vollständig unberührt (eigener HDHomeRun-Pfad auf Port 8888).

# Erkennung eines Enigma2-Service-Reference-Stream-Pfads: 1:0:19:EF11:421:1:...
_OWIF_SREF_RE = re.compile(r'^\d+:\d+:[0-9A-Fa-f]+:')
# Sauberer 10-Feld-Service-Reference (ohne evtl. angehängten Kanalnamen)
_OWIF_SREF_CLEAN_RE = re.compile(r'^((?:[^:]*:){10})')


def _owif_metadata_receiver():
    """Wählt den Receiver, der Metadaten/EPG-Anfragen beantwortet (Reverse-Proxy).

    Bevorzugt den konfigurierten metadata_receiver, sonst den Default-Receiver,
    sonst den ersten aktivierten. Gesperrte Receiver sind ok (nur Metadaten, kein
    Tuner nötig).

    Ausgefallene Receiver (Circuit Breaker / Fehlerserie) werden übersprungen:
    eine "halb tote" Default-Box (TCP offen, HTTP hängt) ließ sonst jede
    EPG-/Bouquet-Anfrage der Apps 20 s in den Timeout laufen. Sind alle
    Kandidaten ausgefallen, gilt wieder die konfigurierte Reihenfolge."""
    receivers = get_receivers()
    if not receivers:
        return None
    pref = get_owif_config().get("metadata_receiver", "auto")
    ordered = []
    if pref and pref != "auto":
        ordered += [r for r in receivers if r["id"] == pref and r.get("enabled", True)]
    ordered += [r for r in receivers if r.get("default") and r.get("enabled", True)]
    ordered += [r for r in receivers if r.get("enabled", True)]
    if not ordered:
        return receivers[0]
    for r in ordered:
        if not is_receiver_unhealthy(r["id"]):
            if r is not ordered[0]:
                log.debug(f"OpenWebif: Metadaten-Receiver '{ordered[0]['id']}' ausgefallen "
                          f"— weiche auf '{r['id']}' aus")
            return r
    return ordered[0]


# ── Synthetisches "Favoriten"-Bouquet (arrangierte e2proxy-Favoriten) ──────
# Wird den Enigma2-Apps (dreamEPG/Dream Player) als erstes Bouquet gezeigt und
# enthält exakt die in e2proxy per Drag&Drop arrangierten Favoriten (favorites.json)
# in ihrer Reihenfolge – statt der favourites.tv des Receivers.
_OWIF_FAV_BNAME = "Favoriten"
_OWIF_FAV_BREF = ('1:7:1:0:0:0:0:0:0:0:FROM BOUQUET '
                  '"userbouquet.e2proxy_favoriten.tv" ORDER BY bouquet')


def _owif_xml_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _owif_is_fav_bref(value):
    return "e2proxy_favoriten" in (value or "")


def _owif_fav_list():
    """Arrangierte e2proxy-Favoriten als [(ref, name), …] in Reihenfolge."""
    out = []
    for f in load_favorites():
        ref = (f.get("ref") or "").strip()
        if ref:
            out.append((ref, f.get("name") or ""))
    return out


def _owif_fetch_upstream(path):
    """GET beim Metadaten-Receiver, liefert bytes oder None."""
    r = _owif_metadata_receiver()
    if not r:
        return None
    url = f"http://{r['ip']}:{r.get('port', 80)}{path}"
    try:
        with urllib.request.urlopen(url, timeout=12) as resp:
            return resp.read()
    except Exception:
        return None


def _owif_fav_channel_objs():
    """Favoriten als getservices-Kanalobjekte (JSON) in Reihenfolge."""
    return [{"servicereference": ref, "program": 0, "servicename": name,
             "pos": i + 1} for i, (ref, name) in enumerate(_owif_fav_list())]


def _owif_fav_channels_payload(is_json):
    favs = _owif_fav_list()
    if is_json:
        return (json.dumps({"services": _owif_fav_channel_objs()}).encode("utf-8"),
                "application/json; charset=utf-8", len(favs))
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', "<e2servicelist>"]
    for ref, name in favs:
        parts.append(
            "<e2service><e2servicereference>%s</e2servicereference>"
            "<e2servicename>%s</e2servicename></e2service>"
            % (_owif_xml_escape(ref), _owif_xml_escape(name)))
    parts.append("</e2servicelist>")
    return ("".join(parts).encode("utf-8"), "text/xml; charset=utf-8", len(favs))


def _owif_fav_bouquet_json(nested):
    """Favoriten-Bouquet-Eintrag für getservices (flach) bzw. getallservices."""
    obj = {"servicereference": _OWIF_FAV_BREF, "program": 0,
           "servicename": _OWIF_FAV_BNAME, "pos": 0}
    if nested:
        obj = {"servicereference": _OWIF_FAV_BREF,
               "subservices": _owif_fav_channel_objs(),
               "servicename": _OWIF_FAV_BNAME}
    return obj


def _owif_fav_bouquet_xml_element(nested):
    import xml.etree.ElementTree as ET
    if nested:
        el = ET.Element("e2bouquet")
    else:
        el = ET.Element("e2service")
    ref = ET.SubElement(el, "e2servicereference")
    ref.text = _OWIF_FAV_BREF
    nm = ET.SubElement(el, "e2servicename")
    nm.text = _OWIF_FAV_BNAME
    if nested:
        lst = ET.SubElement(el, "e2servicelist")
        for cref, cname in _owif_fav_list():
            svc = ET.SubElement(lst, "e2service")
            r = ET.SubElement(svc, "e2servicereference")
            r.text = cref
            n = ET.SubElement(svc, "e2servicename")
            n.text = cname
    return el


def _owif_fav_epg_payload(kind, is_json):
    """EPG (now/next) für die Favoriten zusammensetzen.
    kind: 'nownext' | 'now' | 'next'."""
    import concurrent.futures as _cf
    favs = _owif_fav_list()
    want_now = kind in ("nownext", "now")
    want_next = kind in ("nownext", "next")
    api = "api" if is_json else "web"

    def fetch(entry):
        ref = entry[0]
        q = urllib.parse.quote(ref)
        blobs = []
        if want_now:
            blobs.append(_owif_fetch_upstream(f"/{api}/epgservicenow?sRef={q}"))
        if want_next:
            blobs.append(_owif_fetch_upstream(f"/{api}/epgservicenext?sRef={q}"))
        return blobs

    with _cf.ThreadPoolExecutor(max_workers=8) as ex:
        ordered = list(ex.map(fetch, favs))  # Reihenfolge bleibt erhalten

    if is_json:
        events = []
        for blobs in ordered:
            for d in blobs:
                if not d:
                    continue
                try:
                    events.extend(json.loads(d.decode("utf-8", "replace")).get("events", []))
                except Exception:
                    pass
        return (json.dumps({"events": events}).encode("utf-8"),
                "application/json; charset=utf-8", len(events))
    import xml.etree.ElementTree as ET
    root = ET.Element("e2eventlist")
    cnt = 0
    for blobs in ordered:
        for d in blobs:
            if not d:
                continue
            try:
                for ev in ET.fromstring(d).findall("e2event"):
                    root.append(ev)
                    cnt += 1
            except Exception:
                pass
    return (ET.tostring(root, encoding="utf-8", xml_declaration=True),
            "text/xml; charset=utf-8", cnt)


# ── Aufnahmen-Movielist (OpenWebif-Emulation für Dream Player) ────────
_OWIF_REC_MEDIA_EXT = (".ts", ".mkv", ".mp4", ".m2ts")
# File-Service-Reference einer Aufnahme: 1:0:0:0:0:0:0:0:0:0:/pfad/datei.ts
_OWIF_FILE_SREF_PREFIX = "1:0:0:0:0:0:0:0:0:0:"


def _owif_parse_nfo(path):
    """Best-effort Parser für Kodi/e2proxy .nfo-Dateien.

    Liefert dict(title, plot, duration_s, ts_epoch) oder None."""
    try:
        import xml.etree.ElementTree as ET
        root = ET.parse(path).getroot()
    except Exception:
        return None

    def g(tag):
        el = root.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    title = g("title")
    plot = g("plot") or g("outline")
    dur = 0
    di = root.find(".//durationinseconds")
    if di is not None and di.text and di.text.strip().isdigit():
        dur = int(di.text.strip())
    elif g("runtime").isdigit():
        dur = int(g("runtime")) * 60
    ts = 0
    for tag in ("aired", "premiered", "dateadded"):
        v = g(tag)
        if not v:
            continue
        for fmt, ln in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d", 10)):
            try:
                ts = int(datetime.strptime(v[:ln], fmt).timestamp())
                break
            except Exception:
                pass
        if ts:
            break
    return {"title": title, "plot": plot, "duration": dur, "ts": ts}


def _owif_fmt_length(seconds):
    """OpenWebif-e2length: 'MINUTEN:SS' — Minuten NICHT auf 59 begrenzt
    (90 min -> '90:00', nicht '1:30:00'). Leerer String, wenn unbekannt."""
    try:
        s = int(seconds or 0)
    except (TypeError, ValueError):
        s = 0
    if s <= 0:
        return ""
    return "%d:%02d" % (s // 60, s % 60)


def _owif_human_size(n):
    """Menschenlesbare Dateigröße wie OpenWebif ('3.22 GB')."""
    try:
        v = float(n or 0)
    except (TypeError, ValueError):
        v = 0.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return f"{int(v)} B" if unit == "B" else f"{v:.2f} {unit}"
        v /= 1024
    return f"{v:.2f} TB"


def _owif_bookmarks(base):
    """Unmittelbare Unterordner-Namen des Aufnahme-Basisverzeichnisses
    (OpenWebif <e2locations>/bookmarks)."""
    try:
        return sorted(d for d in os.listdir(base)
                      if os.path.isdir(os.path.join(base, d)))
    except OSError:
        return []


def _owif_sref_to_path(sref):
    """File-Service-Reference (1:0:…:0:/pfad.ts) -> lokaler Dateipfad."""
    m = _OWIF_SREF_CLEAN_RE.match(sref or "")
    if not m:
        return ""
    rest = sref[m.end():]
    return rest if rest.startswith("/") else ""


def _owif_present_path(realpath, base, vroot):
    """Bildet den realen Aufnahmepfad auf den vom Client erwarteten
    virtuellen Movie-Ordner ab (z. B. Dream Players '/media/hdd/movie').
    Ohne vroot bleibt der echte Pfad erhalten."""
    if not vroot:
        return realpath
    rel = os.path.relpath(realpath, base).replace(os.sep, "/")
    return vroot.rstrip("/") + "/" + rel


def _owif_resolve_recording(path):
    """Bildet einen (ggf. virtuellen) Movie-Pfad zurück auf die echte Datei
    unterhalb des Aufnahmeverzeichnisses ab — unabhängig vom vom Client
    konfigurierten 'Movie location'. Liefert den realen Pfad oder ''."""
    base = os.path.abspath(get_recordings_config()["path"])
    if not path:
        return ""
    real = os.path.abspath(path)
    if real.startswith(base) and os.path.isfile(real):
        return real
    # Virtueller Pfad (z. B. /media/hdd/movie/TV/…): führende Segmente
    # abschneiden, bis die Datei relativ zum echten base-Verzeichnis passt.
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    for i in range(len(parts)):
        cand = os.path.join(base, *parts[i:])
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    return ""


def _owif_movie_entry(fpath, base):
    """Baut einen OpenWebif-Movie-Eintrag für eine Aufnahmedatei.
    Metadaten kommen aus der zugehörigen .nfo-Datei."""
    try:
        size = os.path.getsize(fpath)
        mtime = int(os.path.getmtime(fpath))
    except OSError:
        return None
    root_dir = os.path.dirname(fpath)
    fname = os.path.basename(fpath)
    stem = os.path.splitext(fname)[0]
    meta = None
    for cand in (os.path.join(root_dir, stem + ".nfo"),
                 os.path.join(root_dir, "movie.nfo"),
                 os.path.join(root_dir, "tvshow.nfo")):
        if os.path.exists(cand):
            meta = _owif_parse_nfo(cand)
            if meta:
                break
    meta = meta or {}
    rel = os.path.relpath(fpath, base)
    parts = rel.split(os.sep)
    category = parts[0] if len(parts) > 1 else ""
    # TV/<Serie>/Season …/datei.ts  bzw.  Movies/<Titel>/datei.ts
    if category.lower() in ("tv", "movies") and len(parts) >= 2:
        series = parts[1]
    else:
        series = category
    ep_title = meta.get("title") or stem
    if category.lower() == "tv" and series and ep_title and ep_title != series:
        disp_title = f"{series} – {ep_title}"
    else:
        disp_title = ep_title or series
    return {
        "path": fpath,
        "title": disp_title,
        "plot": meta.get("plot", ""),
        "servicename": series or "e2proxy",
        "time": meta.get("ts") or mtime,
        "duration": meta.get("duration", 0),
        "size": size,
        "filename": fpath,
        "sref": _OWIF_FILE_SREF_PREFIX + fpath,
    }


def _owif_scan_movies():
    """Durchsucht das Aufnahme-Verzeichnis und baut eine OpenWebif-taugliche
    Movie-Liste (neueste zuerst). Metadaten kommen aus .nfo-Dateien."""
    rcfg = get_recordings_config()
    base = os.path.abspath(rcfg["path"])
    movies = []
    if not os.path.isdir(base):
        return base, movies
    for root_dir, dirs, files in os.walk(base):
        for fname in sorted(files):
            if not fname.lower().endswith(_OWIF_REC_MEDIA_EXT):
                continue
            entry = _owif_movie_entry(os.path.join(root_dir, fname), base)
            if entry:
                movies.append(entry)
    movies.sort(key=lambda m: m["time"], reverse=True)
    return base, movies


def _owif_movielist_xml(base, movies, vroot=None):
    from xml.sax.saxutils import escape
    out = ['<?xml version="1.0" encoding="UTF-8"?>', "<e2movielist>"]
    for m in movies:
        ppath = _owif_present_path(m["path"], base, vroot)
        psref = _OWIF_FILE_SREF_PREFIX + ppath
        out.append("<e2movie>")
        out.append(f"<e2servicereference>{escape(psref)}</e2servicereference>")
        out.append(f"<e2title>{escape(m['title'])}</e2title>")
        out.append(f"<e2description>{escape(m['plot'][:200])}</e2description>")
        out.append(f"<e2descriptionextended>{escape(m['plot'])}</e2descriptionextended>")
        out.append(f"<e2servicename>{escape(m['servicename'])}</e2servicename>")
        out.append(f"<e2time>{m['time']}</e2time>")
        out.append(f"<e2length>{escape(_owif_fmt_length(m['duration']))}</e2length>")
        out.append("<e2tags></e2tags>")
        out.append(f"<e2filename>{escape(ppath)}</e2filename>")
        out.append(f"<e2filesize>{m['size']}</e2filesize>")
        out.append("</e2movie>")
    out.append("</e2movielist>")
    return "\n".join(out)


def _owif_movielist_json(base, movies, directory=None, vroot=None):
    if directory is None:
        directory = base
    out_movies = []
    for m in movies:
        ppath = _owif_present_path(m["path"], base, vroot)
        psref = _OWIF_FILE_SREF_PREFIX + ppath
        out_movies.append({
            "servicereference": psref,
            "fullname": psref,
            "title": m["title"],
            "eventname": m["title"],
            "description": m["plot"][:200],
            "descriptionExtended": m["plot"],
            "servicename": m["servicename"],
            "recordingtime": m["time"],
            "begintime": _owif_begintime(m["time"]),
            "length": _owif_fmt_length(m["duration"]),
            "lastseen": 0,
            "filename": ppath,
            "filename_stripped": os.path.basename(ppath),
            "filesize": m["size"],
            "filesize_readable": _owif_human_size(m["size"]),
            "tags": "",
        })
    return {
        "directory": directory if directory.endswith("/") else directory + "/",
        "bookmarks": [],
        "recursive": False,
        "movies": out_movies,
    }


def _owif_begintime(ts):
    """Menschenlesbares Aufnahmedatum (OpenWebif 'begintime')."""
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def _owif_moviedetails_xml(m):
    from xml.sax.saxutils import escape
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<e2movie>",
        f"<e2servicereference>{escape(m['sref'])}</e2servicereference>",
        f"<e2title>{escape(m['title'])}</e2title>",
        f"<e2description>{escape(m['plot'][:200])}</e2description>",
        f"<e2descriptionextended>{escape(m['plot'])}</e2descriptionextended>",
        f"<e2servicename>{escape(m['servicename'])}</e2servicename>",
        f"<e2time>{m['time']}</e2time>",
        f"<e2length>{escape(_owif_fmt_length(m['duration']))}</e2length>",
        "<e2tags></e2tags>",
        f"<e2filename>{escape(m['filename'])}</e2filename>",
        f"<e2filesize>{m['size']}</e2filesize>",
        "</e2movie>",
    ])


def _owif_moviedetails_json(m):
    return {"result": True, "movie": {
        "servicereference": m["sref"],
        "fullname": m["sref"],
        "title": m["title"],
        "eventname": m["title"],
        "description": m["plot"][:200],
        "descriptionExtended": m["plot"],
        "servicename": m["servicename"],
        "recordingtime": m["time"],
        "begintime": _owif_begintime(m["time"]),
        "length": _owif_fmt_length(m["duration"]),
        "filename": m["filename"],
        "filename_stripped": os.path.basename(m["filename"]),
        "filesize": m["size"],
        "filesize_readable": _owif_human_size(m["size"]),
        "tags": "",
    }}


def _owif_delete_recording(filepath):
    """Löscht eine Aufnahme (+ .nfo, leere Ordner) unterhalb des
    Aufnahmeverzeichnisses. Akzeptiert auch virtuelle Client-Pfade
    (z. B. /media/hdd/movie/…). Liefert (ok, meldung)."""
    base = os.path.abspath(get_recordings_config()["path"])
    real = _owif_resolve_recording(filepath)
    if not real:
        return False, "Aufnahme nicht gefunden"
    try:
        os.remove(real)
        nfo = real.rsplit(".", 1)[0] + ".nfo"
        if os.path.exists(nfo):
            os.remove(nfo)
        dirpath = os.path.dirname(real)
        try:
            if dirpath != base and not os.listdir(dirpath):
                os.rmdir(dirpath)
                parent = os.path.dirname(dirpath)
                if parent != base and not os.listdir(parent):
                    os.rmdir(parent)
        except OSError:
            pass
        log.info(f"OpenWebif DELETE Aufnahme: {real}")
        return True, f"Aufnahme '{os.path.basename(real)}' gelöscht"
    except OSError as e:
        return False, str(e)


class OpenWebifHandler(http.server.BaseHTTPRequestHandler):

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    # ── Routing ──────────────────────────────────────────
    def _rest(self):
        parsed = urllib.parse.urlparse(self.path)
        return urllib.parse.unquote(parsed.path).lstrip("/")

    def _query(self):
        parsed = urllib.parse.urlparse(self.path)
        return urllib.parse.parse_qs(parsed.query)

    def _port(self):
        try:
            return self.server.server_address[1]
        except Exception:
            return "?"

    def _is_stream(self):
        return bool(_OWIF_SREF_RE.match(self._rest()))

    def do_HEAD(self):
        if self._is_stream():
            try:
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
            except Exception:
                pass
            return
        # Metadaten: einfacher 200er reicht als Verbindungstest
        try:
            self.send_response(200)
            self.send_header("Connection", "close")
            self.end_headers()
        except Exception:
            pass

    def do_GET(self):
        if not (e2probe is not None and e2probe.is_probe_request(self.headers)):
            note_endpoint(classify_endpoint(self.path, "openwebif"))
        try:
            rest = self._rest()
            low = rest.lower()
            # Aufnahmen-Übersicht (Dream Player Recordings-Tab)
            if low in ("web/movielist", "api/movielist",
                       "web/fullmovielist", "api/fullmovielist"):
                log.info(f"OpenWebif MOVIELIST :{self._port()} {self.path} "
                         f"from {self.client_address[0]} "
                         f"[{self.headers.get('User-Agent','')[:40]}]")
                self._serve_movielist(is_json=low.startswith("api/"))
                return
            # Aufnahme-Verzeichnisse (Dream Player fragt oft zuerst danach)
            if (low in ("web/getlocations", "api/getlocations",
                        "web/getcurrlocation", "api/getcurrlocation")
                    and get_owif_config().get("recordings_enabled", True)):
                log.info(f"OpenWebif LOCATIONS :{self._port()} {self.path} "
                         f"from {self.client_address[0]} "
                         f"[{self.headers.get('User-Agent','')[:40]}]")
                self._serve_locations(is_json=low.startswith("api/"),
                                      current=low.endswith("getcurrlocation"))
                return
            # Aufnahme-Details (vor Wiedergabe)
            if low in ("web/moviedetails", "api/moviedetails"):
                self._serve_moviedetails(is_json=low.startswith("api/"))
                return
            # Aufnahme löschen (Dream Player Recordings-Tab)
            if low in ("web/moviedelete", "api/moviedelete"):
                self._serve_moviedelete(is_json=low.startswith("api/"))
                return
            # OpenWebif-Datei-Download/-Stream einer Aufnahme
            if low == "file":
                self._serve_file_param()
                return
            if self._is_stream():
                self._serve_stream(rest)
                return
            # Synthetisches Favoriten-Bouquet: Kanalliste (arrangierte Favoriten)
            if self._is_fav_channel_request():
                self._serve_fav_channels()
                return
            # Synthetisches Favoriten-Bouquet: EPG now/next
            fav_epg = self._fav_epg_kind()
            if fav_epg:
                self._serve_fav_epg(fav_epg)
                return
            # Bouquet-Liste wie in der Web-UI kuratieren/sortieren
            if self._is_bouquet_list_request():
                self._serve_bouquet_list()
                return
            self._reverse_proxy(b"")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log.error(f"OpenWebif do_GET Fehler ({self.path}): {e}")
            self._fail(500, str(e))

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else b""
            self._reverse_proxy(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log.error(f"OpenWebif do_POST Fehler ({self.path}): {e}")
            self._fail(500, str(e))

    # ── Hilfen ───────────────────────────────────────────
    def _fail(self, code, msg):
        try:
            body = msg.encode("utf-8", "replace")
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    # ── Reverse-Proxy für Metadaten/EPG ──────────────────
    def _reverse_proxy(self, body):
        r = _owif_metadata_receiver()
        if not r:
            self._fail(503, "Kein Receiver konfiguriert")
            return
        url = f"http://{r['ip']}:{r.get('port', 80)}{self.path}"
        req = urllib.request.Request(url, data=(body or None), method=self.command)
        for h in ("User-Agent", "Accept", "Accept-Language", "Authorization", "Content-Type"):
            v = self.headers.get(h)
            if v:
                req.add_header(h, v)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
                status = resp.status
                ctype = resp.headers.get("Content-Type", "application/octet-stream")
        except urllib.error.HTTPError as e:
            data = e.read()
            status = e.code
            ctype = e.headers.get("Content-Type", "text/plain") if e.headers else "text/plain"
        except Exception as e:
            log.warning(f"OpenWebif Reverse-Proxy Fehler ({url}): {e}")
            self._fail(502, f"Upstream-Fehler: {e}")
            return
        # Access-Logging für Diagnose (EPG/getservices/Bouquets sichtbar machen)
        p = urllib.parse.urlparse(self.path).path
        noisy = ("/picon" in p or "/logo" in p or
                 p.endswith((".png", ".jpg", ".jpeg", ".gif", ".ico", ".js", ".css")))
        if e2probe is not None and e2probe.is_probe_request(self.headers):
            noisy = True
        (log.debug if noisy else log.info)(
            f"OpenWebif PROXY [{status}] {self.command} {self.path[:100]} "
            f"→ {r['id']} [{(self.headers.get('User-Agent', '') or '')[:36]}]")
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ── Synthetisches Favoriten-Bouquet ─────────────────
    def _is_fav_channel_request(self):
        p = urllib.parse.urlparse(self.path).path.lower().rstrip("/")
        if not p.endswith("getservices"):
            return False
        q = self._query()
        sref = (q.get("sRef") or q.get("sref") or
                q.get("bRef") or q.get("bref") or [""])[0]
        return _owif_is_fav_bref(sref)

    def _fav_epg_kind(self):
        p = urllib.parse.urlparse(self.path).path.lower()
        if "epg" not in p:
            return None
        q = self._query()
        bref = (q.get("bRef") or q.get("bref") or
                q.get("sRef") or q.get("sref") or [""])[0]
        if not _owif_is_fav_bref(bref):
            return None
        if p.endswith("epgnext"):
            return "next"
        if p.endswith("epgnow") or p.endswith("epgmulti"):
            return "now"
        return "nownext"

    def _serve_fav_channels(self):
        is_json = urllib.parse.urlparse(self.path).path.lower().startswith("/api")
        body, ctype, n = _owif_fav_channels_payload(is_json)
        log.info(f"OpenWebif FAV Kanalliste ({n} Favoriten) "
                 f"[{(self.headers.get('User-Agent','') or '')[:30]}]")
        self._send_bytes(200, ctype, body)

    def _serve_fav_epg(self, kind):
        is_json = urllib.parse.urlparse(self.path).path.lower().startswith("/api")
        try:
            body, ctype, n = _owif_fav_epg_payload(kind, is_json)
        except Exception as e:
            log.warning(f"OpenWebif FAV-EPG Fehler: {e}")
            self._reverse_proxy(b"")
            return
        log.info(f"OpenWebif FAV EPG {kind} ({n} Events) "
                 f"[{(self.headers.get('User-Agent','') or '')[:30]}]")
        self._send_bytes(200, ctype, body)

    def _send_bytes(self, status, ctype, body):
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ── Bouquet-Liste kuratieren (wie Web-UI) ────────────
    def _is_bouquet_list_request(self):
        parsed = urllib.parse.urlparse(self.path)
        p = parsed.path.lower().rstrip("/")
        # getallservices = kompletter Bouquet+Kanal-Baum (Voll-Sync der Apps)
        if p.endswith("getallservices"):
            return True
        if not p.endswith("getservices"):
            return False
        q = urllib.parse.parse_qs(parsed.query)
        sref = (q.get("sRef") or q.get("sref") or
                q.get("bRef") or q.get("bref") or [""])[0]
        low = sref.lower()
        if not sref:
            return True  # Default = TV-Bouquet-Liste
        if "userbouquet" in low or "radio" in low:
            return False  # einzelnes Bouquet bzw. Radio → unverändert durchreichen
        return "bouquets" in low

    def _filter_bouquet_payload(self, raw, is_json, ctype, is_all=False):
        rx = re.compile(r'"(userbouquet\.[^"]+?\.(?:tv|radio))"')
        order = {name: i for i, name in enumerate(BOUQUETS)}
        # Favoriten-Bouquet nur der TV-Liste voranstellen (nicht bei Radio)
        add_fav = bool(_owif_fav_list())
        if is_json:
            d = json.loads(raw.decode("utf-8", "replace"))
            keep = []
            for s in d.get("services", []):
                m = rx.search(s.get("servicereference", ""))
                if m and m.group(1) in order:
                    keep.append((order[m.group(1)], s))
            keep.sort(key=lambda t: t[0])
            services = [s for _, s in keep]
            if add_fav:
                services.insert(0, _owif_fav_bouquet_json(is_all))
            d["services"] = services
            return (json.dumps(d).encode("utf-8"),
                    ctype or "application/json; charset=utf-8", len(services))
        import xml.etree.ElementTree as ET
        root = ET.fromstring(raw)
        # getservices → <e2service>, getallservices → <e2bouquet> (mit Kanälen)
        tag = "e2bouquet" if root.find("e2bouquet") is not None else "e2service"
        services = root.findall(tag)
        keep = []
        for el in services:
            refel = el.find("e2servicereference")
            ref = refel.text if refel is not None and refel.text else ""
            m = rx.search(ref)
            if m and m.group(1) in order:
                keep.append((order[m.group(1)], el))
        for el in services:
            root.remove(el)
        ordered = [el for _, el in sorted(keep, key=lambda t: t[0])]
        if add_fav:
            root.append(_owif_fav_bouquet_xml_element(tag == "e2bouquet"))
        for el in ordered:
            root.append(el)
        body = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        return body, ctype or "text/xml; charset=utf-8", len(ordered) + (1 if add_fav else 0)

    def _serve_bouquet_list(self):
        r = _owif_metadata_receiver()
        if not r:
            self._fail(503, "Kein Receiver konfiguriert")
            return
        url = f"http://{r['ip']}:{r.get('port', 80)}{self.path}"
        req = urllib.request.Request(url, method="GET")
        ua = self.headers.get("User-Agent")
        if ua:
            req.add_header("User-Agent", ua)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "") or ""
                status = resp.status
        except Exception as e:
            log.warning(f"OpenWebif getservices Upstream-Fehler: {e}")
            self._reverse_proxy(b"")
            return
        is_json = ("json" in ctype.lower() or
                   urllib.parse.urlparse(self.path).path.lower().startswith("/api"))
        is_all = urllib.parse.urlparse(self.path).path.lower().rstrip("/").endswith("getallservices")
        try:
            out, out_ctype, n = self._filter_bouquet_payload(raw, is_json, ctype, is_all)
        except Exception as e:
            log.warning(f"OpenWebif Bouquet-Filter Fehler, reiche unverändert durch: {e}")
            out, out_ctype, n = raw, (ctype or "application/octet-stream"), -1
        (log.debug if (ua or "").startswith("e2proxy-probe") else log.info)(
            f"OpenWebif PROXY [{status}] GET {self.path[:60]} → {r['id']} "
            f"(Bouquets kuratiert: {n}) [{(ua or '')[:30]}]")
        try:
            self.send_response(200)
            self.send_header("Content-Type", out_ctype)
            self.send_header("Content-Length", str(len(out)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(out)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ── Aufnahmen: Movielist + Datei-Streaming ───────────
    def _serve_movielist(self, is_json):
        # Dream Player fragt movielist mit seinem konfigurierten "Movie
        # location" (Default /media/hdd/movie) als dirname an und zeigt nur
        # Aufnahmen, deren Pfad/Directory dazu passt. Wir spiegeln daher die
        # Aufnahmen unter genau diesen virtuellen Ordner (vroot) und mappen
        # beim Abspielen/Löschen wieder auf den echten Pfad zurück.
        vroot = (self._query().get("dirname") or [""])[0] or None
        if not get_owif_config().get("recordings_enabled", True):
            base, movies = os.path.abspath(get_recordings_config()["path"]), []
        else:
            base, movies = _owif_scan_movies()
        directory = vroot or base
        if is_json:
            payload = json.dumps(
                _owif_movielist_json(base, movies, directory=directory, vroot=vroot)
            ).encode("utf-8")
            ctype = "application/json; charset=utf-8"
        else:
            payload = _owif_movielist_xml(base, movies, vroot=vroot).encode("utf-8")
            ctype = "text/xml; charset=utf-8"
        log.info(f"OpenWebif MOVIELIST -> {len(movies)} Aufnahmen, "
                 f"{len(payload)} Bytes ({'JSON' if is_json else 'XML'}), "
                 f"dirname={vroot or '-'}")
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _serve_locations(self, is_json, current):
        """Meldet e2proxy's Aufnahme-Verzeichnis als OpenWebif-Location, damit
        Dream Player die Aufnahmen dort (statt beim echten Receiver) sucht."""
        base = os.path.abspath(get_recordings_config()["path"])
        loc = base if base.endswith("/") else base + "/"
        if is_json:
            if current:
                data = {"result": True, "location": base}
            else:
                data = {"result": True, "locations": [loc], "default": loc}
            payload = json.dumps(data).encode("utf-8")
            ctype = "application/json; charset=utf-8"
        else:
            from xml.sax.saxutils import escape
            payload = ("\n".join([
                '<?xml version="1.0" encoding="UTF-8"?>',
                "<e2locations>",
                f"<e2location>{escape(loc)}</e2location>",
                "</e2locations>",
            ])).encode("utf-8")
            ctype = "text/xml; charset=utf-8"
        self._send_bytes(200, ctype, payload)

    def _serve_moviedetails(self, is_json):
        q = self._query()
        sref = (q.get("sRef") or q.get("sref") or [""])[0]
        base = os.path.abspath(get_recordings_config()["path"])
        vpath = _owif_sref_to_path(sref)
        real = _owif_resolve_recording(vpath)
        entry = _owif_movie_entry(real, base) if real else None
        if not entry:
            self._reverse_proxy(b"")
            return
        # Dem Client denselben (virtuellen) Pfad zurückgeben, den er kennt.
        if vpath:
            entry["filename"] = vpath
            entry["sref"] = _OWIF_FILE_SREF_PREFIX + vpath
        if is_json:
            payload = json.dumps(_owif_moviedetails_json(entry)).encode("utf-8")
            ctype = "application/json; charset=utf-8"
        else:
            payload = _owif_moviedetails_xml(entry).encode("utf-8")
            ctype = "text/xml; charset=utf-8"
        self._send_bytes(200, ctype, payload)

    def _serve_moviedelete(self, is_json):
        q = self._query()
        sref = (q.get("sRef") or q.get("sref") or [""])[0]
        path = _owif_sref_to_path(sref)
        ok, msg = _owif_delete_recording(path)
        log.info(f"OpenWebif MOVIEDELETE from {self.client_address[0]}: "
                 f"{ok} ({msg})")
        if is_json:
            payload = json.dumps({"result": ok, "message": msg}).encode("utf-8")
            ctype = "application/json; charset=utf-8"
        else:
            from xml.sax.saxutils import escape
            payload = ("\n".join([
                '<?xml version="1.0" encoding="UTF-8"?>',
                "<e2simplexmlresult>",
                f"<e2state>{'True' if ok else 'False'}</e2state>",
                f"<e2statetext>{escape(msg)}</e2statetext>",
                "</e2simplexmlresult>",
            ])).encode("utf-8")
            ctype = "text/xml; charset=utf-8"
        self._send_bytes(200, ctype, payload)

    def _serve_file_param(self):
        """OpenWebif /file?file=<pfad> — Aufnahmen lokal ausliefern, sonst
        an den Receiver weiterreichen (z. B. Picons/Logs)."""
        fp = self._query().get("file", [""])[0]
        real = _owif_resolve_recording(fp)
        if real:
            self._send_file_range(real)
            return
        self._reverse_proxy(b"")

    def _serve_recording_file(self, filepath):
        real = _owif_resolve_recording(filepath)
        if not real:
            self._fail(404, "Aufnahme nicht gefunden")
            return
        log.info(f"OpenWebif RECORDING from {self.client_address[0]}: "
                 f"{os.path.basename(real)}")
        self._send_file_range(real)

    def _send_file_range(self, filepath):
        """Liefert eine Datei mit Range-Support (Seeking) aus — rohes TS,
        kein ffmpeg."""
        file_size = os.path.getsize(filepath)
        range_header = self.headers.get("Range", "")
        start, end, status = 0, file_size - 1, 200
        if range_header.startswith("bytes="):
            try:
                r = range_header[6:].split("-")
                start = int(r[0]) if r[0] else 0
                end = int(r[1]) if len(r) > 1 and r[1] else file_size - 1
                end = min(end, file_size - 1)
                status = 206
            except Exception:
                start, end, status = 0, file_size - 1, 200
        length = end - start + 1
        try:
            self.send_response(status)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Connection", "close")
            self.end_headers()
            with open(filepath, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(262144, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ── Stream-Intercept mit Tuner-Orchestrierung ────────
    def _pick_profile(self):
        owif = get_owif_config()
        ua = self.headers.get("User-Agent", "") or ""
        ua_l = ua.lower()
        profile_id = owif["default_profile"]
        for ov in owif["ua_overrides"]:
            if ov["match"].lower() in ua_l:
                profile_id = ov["profile"]
                break
        return profile_id, ua

    def _serve_stream(self, rest):
        m = _OWIF_SREF_CLEAN_RE.match(rest)
        # File-Service-Reference einer Aufnahme (…:0:/pfad.ts) → lokale Datei
        if m:
            remainder = rest[m.end():]
            if remainder.startswith("/"):
                self._serve_recording_file(remainder)
                return
        sref = m.group(1) if m else rest
        profile_id, ua = self._pick_profile()
        client_ip = self.client_address[0]

        tp = get_transcode_profile(profile_id)
        passthrough = (not tp) or tp.get("codec") == "pass"

        _ch_lookup = {ch["ref"].rstrip("/"): ch["name"] for ch in get_channels()}
        channel_name = _ch_lookup.get(sref.rstrip("/"), sref[:30])

        # Receiver wählen: bereits laufenden Tuner für denselben Sender bevorzugen
        # (Shared Tuner), sonst einen freien. Auswahl UND Belegung passieren unter
        # einem Lock — sonst greifen zwei fast gleichzeitige Requests (z.B. VLCs
        # Probe- plus Play-Verbindung) denselben "freien" Receiver.
        rid = None
        shared = False
        rcv_epoch = None
        owif_acquire_kwargs = {
            "client_ip": client_ip,
            "service_ref": sref,
            "channel_name": channel_name,
            "user_agent": ua,
            "profile": profile_id,
            "transcode": {
                "active": not passthrough,
                "codec": (tp.get("codec", "") if tp else "pass"),
                "label": (tp.get("label", profile_id) if tp else "Passthrough"),
            },
        }
        with receiver_lock:
            for r in get_receivers():
                st = receivers_mod._receiver_state.get(r["id"])
                if st and st.get("service_ref", "").rstrip("/") == sref.rstrip("/"):
                    rid = r["id"]
                    shared = True
                    break
            if not rid:
                rid = get_free_receiver()
            if rid:
                if shared:
                    # Mitzählen! Sonst gibt der zuerst endende Stream den Tuner frei,
                    # obwohl der andere noch läuft — der nächste Request zappt ihn weg.
                    rcv_epoch = attach_receiver(rid, sref)
                    shared = rcv_epoch is not None
                if not shared:
                    rcv_epoch = acquire_receiver(rid, **owif_acquire_kwargs)
        if not rid:
            self._fail(503, "All receivers busy")
            return

        log.info(f"OpenWebif STREAM [{profile_id}] from {client_ip}: {channel_name} "
                 f"[{ua[:40]}] (receiver={rid}, shared={shared})")

        _owif_t0 = time.time()
        try:
            if not shared:
                rid, rcv_epoch = zap_with_failover(
                    rid, rcv_epoch, sref, channel_name, owif_acquire_kwargs,
                    source="OpenWebif")
                if not rid:
                    self._fail(502, "Zap fehlgeschlagen")
                    return
                zap_wait = get_switch_settings(sref)["zap_wait"]
                if zap_wait > 0:
                    time.sleep(zap_wait)

            if passthrough:
                content_type = "video/mp2t"
            else:
                _, content_type = build_ffmpeg_cmd(rid, sref, tp)

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Pragma", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

            if passthrough:
                stream_passthrough(rid, sref, self.wfile, use_chunked=False)
            else:
                stream_transcoded(rid, sref, tp, self.wfile, use_chunked=False,
                                   channel_name=channel_name)
        except ScrambledStreamError:
            log.warning(f"OpenWebif SCRAMBLED: {sref}")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log.error(f"OpenWebif STREAM ERROR: {e}")
        finally:
            try:
                _bytes = 0
                if not shared:
                    _bytes = (receivers_mod._receiver_state.get(rid) or {}).get("bytes_sent", 0)
                record_usage_session(
                    sref, channel_name, user_agent=ua, client_ip=client_ip,
                    profile=profile_id, source="OpenWebif: Streaming",
                    started_ts=_owif_t0, duration_sec=time.time() - _owif_t0,
                    bytes_sent=_bytes)
            except Exception as _e:
                log.debug(f"Usage-Hook (OpenWebif) fehlgeschlagen: {_e}")
            # Immer freigeben — der Refcount entscheidet, ob der Tuner wirklich
            # frei wird. Shared-Sessions haben oben via attach_receiver erhöht.
            # rid ist None, wenn das Failover auf allen Tunern scheiterte.
            if rid:
                release_receiver(rid, epoch=rcv_epoch)


def start_openwebif_server():
    """Startet die OpenWebif-Emulation, falls aktiviert.

    Imitiert eine Enigma2-Box mit Standardports: OpenWebif auf :80 (Metadaten/EPG)
    und Streaming auf :8001. Beide Ports nutzen denselben Handler (Stream-Pfade
    werden intern erkannt), sind aber getrennt lauschbar wie bei einer echten Box.
    """
    owif = get_owif_config()
    if not owif["enabled"]:
        log.info("OpenWebif-Emulation: deaktiviert")
        return []
    bind = owif["bind"]
    ports = {owif["webif_port"], owif["stream_port"]}
    if owif.get("recordings_enabled", True) and owif.get("recordings_port"):
        ports.add(owif["recordings_port"])
    servers = []
    for p in sorted(ports):
        try:
            srv = http.server.ThreadingHTTPServer((bind, p), OpenWebifHandler)
        except Exception as e:
            log.error(f"OpenWebif-Emulation: Port {p} konnte nicht gebunden werden: {e}")
            continue
        threading.Thread(target=srv.serve_forever, daemon=True,
                         name=f"openwebif-{p}").start()
        servers.append(srv)
    if servers:
        log.info(f"OpenWebif-Emulation aktiv auf {bind} — OpenWebif:{owif['webif_port']} "
                 f"Stream:{owif['stream_port']} Recordings:{owif.get('recordings_port')} "
                 f"(Default-Profil: {owif['default_profile']}, "
                 f"{len(owif['ua_overrides'])} UA-Overrides)")
    return servers


