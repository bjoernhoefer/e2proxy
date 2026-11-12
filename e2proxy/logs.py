"""Logging: stdout, in-memory ring buffer for the UI and daily-rotated file log."""
import collections as _collections
import logging
import os
import sys
import time

from e2proxy.paths import DATA_DIR

# ──────────────────────────────────────────────────────────

# ── Log-Level Ring-Buffer ─────────────────────────────────────────────────

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

def _setup_file_logging(retention_days=5):
    """
    Richtet File-Logging ein: tägliche Rotation, konfigurierbare Aufbewahrung.
    Wird zusätzlich zum bestehenden stdout-Handler gehängt.
    Nach load_config() aufrufen damit retention aus Config kommt.
    """
    import logging.handlers
    log_file  = os.path.join(DATA_DIR, "e2proxy.log")
    retention = int(retention_days)
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
