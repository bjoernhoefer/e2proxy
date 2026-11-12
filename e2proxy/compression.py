"""Background compression of finished recordings (TS -> MKV)."""
import json
import os
import signal
import subprocess
import threading
import time
from datetime import datetime

from e2proxy.paths import DATA_DIR
from e2proxy.logs import log
from e2proxy.config import get_config
from e2proxy.plex import _plex_notify
from e2proxy.recordings import _active_recordings, _active_recordings_lock, get_recordings_config

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



