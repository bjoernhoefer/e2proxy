"""e2probe — Health-Probes für die Client-Schnittstellen von e2proxy.

Bewusst als eigenes Modul ohne Abhängigkeit zu e2proxy.py: die Probes testen
die Schnittstellen von außen (Black-Box, per HTTP/TCP gegen localhost), so wie
Plex oder die Dream-/Enigma2-Apps sie sehen. e2proxy liefert nur die Liste der
Ziele (``targets_fn``) und reagiert auf Statuswechsel (``on_change``).

Zustände je Probe:
    ok      — antwortet korrekt und schnell
    warn    — antwortet, aber langsam oder eingeschränkt
    down    — antwortet nicht/fehlerhaft (erst nach ``fail_threshold`` Fehlern
              in Folge, damit kurze Aussetzer keinen Alarm auslösen)
    unknown — noch nicht geprüft

Ziel-Definition (dict):
    id, label, group        — Kennung/Anzeige
    kind: "http"            — url, timeout, warn_ms, check(status, body) -> (state, detail)
    kind: "tcp"             — host, port, timeout
    kind: "func"            — fn() -> (state, detail)
"""

import json
import socket
import threading
import time
import urllib.error
import urllib.request

PROBE_UA = "e2proxy-probe"
STATES = ("ok", "warn", "down", "unknown")
_MAX_BODY = 4 * 1024 * 1024


def is_probe_request(headers):
    """True, wenn der Request von e2probe stammt (für Statistik/Logs ausblenden)."""
    try:
        return (headers.get("User-Agent") or "").startswith(PROBE_UA)
    except Exception:
        return False


# ── Einzelne Probes ─────────────────────────────────────────────────────────

def http_probe(url, timeout=5.0, warn_ms=2000, check=None):
    """GET url → (state, detail, ms)."""
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": PROBE_UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            body = resp.read(_MAX_BODY)
    except urllib.error.HTTPError as e:
        status, body = e.code, b""
        try:
            body = e.read(4096)
        except Exception:
            pass
    except Exception as e:
        return "down", _err(e), int((time.time() - t0) * 1000)
    ms = int((time.time() - t0) * 1000)
    if check:
        try:
            state, detail = check(status, body)
        except Exception as e:
            state, detail = "down", f"Antwort ungültig: {_err(e)}"
    elif 200 <= status < 300:
        state, detail = "ok", f"HTTP {status}"
    else:
        state, detail = "down", f"HTTP {status}"
    if state == "ok" and warn_ms and ms > warn_ms:
        state, detail = "warn", f"{detail} · langsam ({ms} ms)"
    return state, detail, ms


def tcp_probe(host, port, timeout=3.0):
    """TCP-Connect → (state, detail, ms)."""
    t0 = time.time()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            pass
    except Exception as e:
        return "down", f"Port {port}: {_err(e)}", int((time.time() - t0) * 1000)
    return "ok", f"Port {port} offen", int((time.time() - t0) * 1000)


def stream_probe(url, seconds=8.0, first_byte_timeout=25.0, min_bytes=188 * 2000):
    """Öffnet einen echten Stream und liest ``seconds`` lang.

    Belegt einen Tuner und zappt eine Box — daher nur auf Anforderung.
    Rückgabe: dict mit ok, detail, ttfb_ms, bytes, kbps, ts_sync.
    """
    t0 = time.time()
    res = {"ok": False, "url": url, "ttfb_ms": None, "bytes": 0, "kbps": 0,
           "ts_sync": False, "detail": ""}
    req = urllib.request.Request(url, headers={"User-Agent": PROBE_UA})
    try:
        resp = urllib.request.urlopen(req, timeout=first_byte_timeout)
    except urllib.error.HTTPError as e:
        res["detail"] = f"HTTP {e.code}"
        return res
    except Exception as e:
        res["detail"] = f"Verbindung fehlgeschlagen nach {time.time() - t0:.1f}s: {_err(e)}"
        return res
    head = b""
    try:
        first = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
        if not first:
            res["detail"] = f"Stream leer (HTTP {resp.status})"
            return res
        res["ttfb_ms"] = int((time.time() - t0) * 1000)
        total = len(first)
        head = first[:188 * 4]
        t_data = time.time()
        while time.time() - t_data < seconds:
            chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if len(head) < 188 * 4:
                head += chunk[:188 * 4 - len(head)]
        dur = max(time.time() - t_data, 0.001)
        res["bytes"] = total
        res["kbps"] = int(total * 8 / 1000 / dur)
    except Exception as e:
        res["detail"] = f"Abbruch nach {res['bytes']} Bytes: {_err(e)}"
        return res
    finally:
        try:
            resp.close()
        except Exception:
            pass
    res["ts_sync"] = _looks_like_ts(head)
    res["ok"] = res["bytes"] >= min_bytes
    res["detail"] = (f"erstes Byte nach {res['ttfb_ms']} ms, "
                     f"{res['bytes'] / 1e6:.1f} MB in {seconds:.0f}s ({res['kbps']} kbit/s)"
                     f"{'' if res['ts_sync'] else ', kein MPEG-TS-Sync'}"
                     f"{'' if res['ok'] else ' — zu wenig Daten'}")
    return res


def _looks_like_ts(data):
    for off in range(min(188, len(data))):
        if all(off + i * 188 < len(data) and data[off + i * 188] == 0x47 for i in range(3)):
            return True
    return False


def _err(e):
    if isinstance(e, urllib.error.URLError) and getattr(e, "reason", None):
        e = e.reason
    s = str(e) or e.__class__.__name__
    return s[:160]


# ── Check-Helfer für typische Antworten ─────────────────────────────────────

def check_json(validator):
    """validator(obj) -> (state, detail). Prüft vorher HTTP 2xx + gültiges JSON."""
    def _check(status, body):
        if not 200 <= status < 300:
            return "down", f"HTTP {status}"
        return validator(json.loads(body.decode("utf-8", "replace")))
    return _check


def check_contains(needle, label=None):
    def _check(status, body):
        if not 200 <= status < 300:
            return "down", f"HTTP {status}"
        if needle.encode() not in body:
            return "down", f"'{needle}' fehlt in der Antwort"
        return "ok", label or f"HTTP {status}"
    return _check


# ── Monitor ─────────────────────────────────────────────────────────────────

class InterfaceMonitor:
    def __init__(self, targets_fn, interval=60, fail_threshold=2,
                 on_change=None, logger=None):
        self.targets_fn = targets_fn
        self.interval = interval
        self.fail_threshold = max(1, int(fail_threshold))
        self.on_change = on_change
        self.log = logger
        self._state = {}            # id → result dict
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._last_run = 0.0
        self._thread = None

    def start(self, initial_delay=30):
        if self._thread:
            return
        def _loop():
            time.sleep(initial_delay)
            while True:
                try:
                    self.run_once()
                except Exception as e:
                    if self.log:
                        self.log.debug(f"e2probe: {e}")
                time.sleep(self.interval)
        self._thread = threading.Thread(target=_loop, daemon=True, name="e2probe")
        self._thread.start()

    def _probe(self, t):
        kind = t.get("kind")
        if kind == "http":
            return http_probe(t["url"], t.get("timeout", 5.0), t.get("warn_ms", 2000),
                              t.get("check"))
        if kind == "tcp":
            return tcp_probe(t.get("host", "127.0.0.1"), t["port"], t.get("timeout", 3.0))
        if kind == "func":
            t0 = time.time()
            try:
                state, detail = t["fn"]()
            except Exception as e:
                state, detail = "down", _err(e)
            return state, detail, int((time.time() - t0) * 1000)
        return "unknown", f"unbekannter Probe-Typ {kind!r}", 0

    def run_once(self):
        """Alle Ziele parallel prüfen; gibt den Snapshot zurück."""
        with self._run_lock:
            targets = list(self.targets_fn() or [])
            raw = {}
            def _run(t):
                raw[t["id"]] = self._probe(t)
            threads = [threading.Thread(target=_run, args=(t,), daemon=True) for t in targets]
            for th in threads:
                th.start()
            for th in threads:
                th.join(timeout=60)
            now = time.time()
            changes = []
            with self._lock:
                ids = {t["id"] for t in targets}
                for gone in [i for i in self._state if i not in ids]:
                    del self._state[gone]
                for t in targets:
                    state, detail, ms = raw.get(t["id"], ("down", "Probe hängt", 0))
                    prev = self._state.get(t["id"]) or {
                        "state": "unknown", "since": now, "fails": 0}
                    fails = prev["fails"] + 1 if state == "down" else 0
                    shown = state
                    if state == "down" and fails < self.fail_threshold:
                        # Einzelner Aussetzer: noch nicht als down werten
                        shown = "warn" if prev["state"] != "down" else "down"
                        detail = f"{detail} (Fehler {fails}/{self.fail_threshold})"
                    since = prev["since"] if shown == prev["state"] else now
                    self._state[t["id"]] = {
                        "id": t["id"], "label": t.get("label", t["id"]),
                        "group": t.get("group", ""), "state": shown, "detail": detail,
                        "ms": ms, "fails": fails, "since": since, "checked_at": now,
                    }
                    was_down = prev["state"] == "down"
                    first = prev["state"] == "unknown"
                    if (first and shown == "down") or (not first and (shown == "down") != was_down):
                        changes.append((t, prev["state"], shown, dict(self._state[t["id"]])))
            self._last_run = now
        if self.on_change:
            for t, old, new, res in changes:
                try:
                    self.on_change(t, old, new, res)
                except Exception as e:
                    if self.log:
                        self.log.debug(f"e2probe on_change: {e}")
        return self.snapshot()

    def snapshot(self):
        with self._lock:
            probes = [dict(v) for v in self._state.values()]
        order = {s: i for i, s in enumerate(("down", "warn", "unknown", "ok"))}
        worst = min((p["state"] for p in probes), key=lambda s: order.get(s, 9),
                    default="unknown")
        return {
            "checked_at": self._last_run,
            "interval": self.interval,
            "state": worst,
            "ok": worst in ("ok", "warn"),
            "down": [p["id"] for p in probes if p["state"] == "down"],
            "probes": probes,
        }
