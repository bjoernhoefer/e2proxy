"""Programme metadata: TMDB/TVDB lookups, classification and NFO generation."""
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

from e2proxy.paths import DATA_DIR
from e2proxy.logs import log
from e2proxy.config import get_config

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


