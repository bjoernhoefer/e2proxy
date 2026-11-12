"""API access log (append-only, daily rotation)."""
import json
import os
from datetime import datetime

from e2proxy.paths import DATA_DIR
from e2proxy.logs import log
from e2proxy.config import get_config

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



