"""Recording engine (ffmpeg) with Plex-compliant paths and NFO files."""
import os
import subprocess as _subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid as _uuid
from datetime import datetime

from e2proxy.paths import DATA_DIR
from e2proxy.logs import log
from e2proxy.config import (
    get_config,
    get_proxy_host,
    get_proxy_port,
    get_receiver_by_id,
    get_receivers,
    is_receiver_locked,
    is_receiver_usable,
)
from e2proxy.channels import channel_cache, channel_cache_lock
from e2proxy.logos import get_logo_for_epg
from e2proxy.receivers import (
    acquire_receiver,
    get_free_receiver,
    kill_proc_robust,
    kill_stream,
    stream_info_public,
)
from e2proxy.metadata import (
    build_nfo,
    build_recording_path,
    classify_recording,
    verify_recording_metadata,
)
from e2proxy.plex import _plex_notify
from e2proxy import receivers as receivers_mod

# ── HTML Templates ────────────────────────────────────────


# ── Recording System ───────────────────────────────────────────────────────────

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
        "plex_dvr_refresh": cfg.get("recordings_plex_dvr_refresh", False),
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
        state = receivers_mod._receiver_state.get(r["id"])
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
        acquire_receiver(rid, client_ip, service_ref, title or "Aufnahme",
                         user_agent="e2proxy (Aufnahme)", profile=rec_profile,
                         transcode={"active": True, "codec": "record",
                                    "label": f"Aufnahme ({rec_profile})"})

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
        state = receivers_mod._receiver_state.get(r["id"])
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
            "stream": stream_info_public(state),
        })
    return {"total": total, "busy": busy, "free": total - busy, "receivers": result}


