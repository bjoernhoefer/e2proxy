"""Structured stream diagnostics (logfmt DIAG lines for Loki/Grafana)."""
import threading
import time

from e2proxy.logs import log
from e2proxy.config import get_config
from e2proxy import receivers

# ── Stream-Diagnose (strukturierte Logs für Loki/Grafana) ─────────────
#
# Streams bleiben gelegentlich ein bis zwei Minuten stehen. Um das im Nachhinein
# analysieren zu können, misst jede Stream-Schleife getrennt, wie lange sie auf
# Upstream-Daten (Receiver bzw. ffmpeg) und wie lange sie auf den Client
# gewartet hat. Damit lässt sich ein Stillstand eindeutig zuordnen:
#   read_wait steigt  → Receiver/Transponder bzw. ffmpeg liefert nicht
#   write_wait steigt → Client liest nicht ab (Netz/Player-Buffer)
# Ein Watchdog-Thread meldet laufende Stillstände in Echtzeit, damit ein Hänger
# auch dann sichtbar wird, wenn der Stream danach nie wieder Daten liefert.
#
# Ausgabeformat ist logfmt hinter dem Marker "DIAG", damit Loki es per
# `|= "DIAG" | logfmt` ohne weitere Parser-Konfiguration zerlegen kann.

DIAG_STALL_SEC     = 3.0    # ab dieser Lücke ohne Daten gilt ein Stream als stehend
DIAG_STALL_REPEAT  = 15.0   # Wiederholintervall für "stall läuft noch"-Meldungen
DIAG_SAMPLE_SEC    = 15.0   # Durchsatz-Samples (nur im Diag-Modus)

_diag_streams = {}          # diag_id → Live-Zähler eines aktiven Streams
_diag_lock = threading.Lock()
_diag_counter = 0


def diag_enabled():
    """True, wenn der erweiterte Diagnose-Modus aktiv ist (on demand)."""
    try:
        return bool(get_config().get("diag_logging", False))
    except Exception:
        return False


def _diag_quote(v):
    s = "" if v is None else str(v)
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    if s == "" or any(c in s for c in ' ="'):
        return f'"{s}"'
    return s


def diag_log(event, level="info", **fields):
    """Schreibt eine strukturierte Diagnose-Zeile (logfmt) ins normale Log."""
    parts = [f"event={_diag_quote(event)}"]
    for k, v in fields.items():
        if v is None:
            continue
        if isinstance(v, float):
            v = f"{v:.2f}"
        parts.append(f"{k}={_diag_quote(v)}")
    line = "DIAG " + " ".join(parts)
    getattr(log, level, log.info)(line)


DIAG_ISSUE_WINDOW_SEC = 60.0   # Aggregationsfenster für wiederkehrende ffmpeg-Meldungen
_diag_issue_agg = {}           # (rid, ref, kind) → {"first","last","n","msg","logged"}
_diag_issue_lock = threading.Lock()


def diag_log_issue(rid, service_ref, channel, kind, msg):
    """Meldet ein ffmpeg-Problem — aber höchstens einmal pro Fenster und Art.

    Ein leicht gestörter Transponder erzeugt hunderte identische
    "Packet corrupt"-Meldungen pro Minute. Einzeln geloggt ersäuft das Loki und
    verdeckt die wirklich interessanten Ereignisse. Deshalb: erste Meldung
    sofort, danach nur noch eine Zusammenfassung pro Fenster mit Zähler.
    """
    now = time.monotonic()
    key = (rid, service_ref, kind)
    with _diag_issue_lock:
        e = _diag_issue_agg.get(key)
        if e is None or now - e["start"] >= DIAG_ISSUE_WINDOW_SEC:
            # Abgelaufenes Fenster vorher zusammenfassen
            summary = None
            if e is not None and e["n"] > 1:
                summary = (e["n"] - 1, now - e["start"], e["msg"])
            _diag_issue_agg[key] = {"start": now, "n": 1, "msg": msg}
            emit_first = True
        else:
            e["n"] += 1
            e["msg"] = msg
            summary = None
            emit_first = False
    if summary:
        n, span, last_msg = summary
        diag_log("ffmpeg_issue_summary", level="warning", rid=rid,
                 ref=service_ref, channel=channel or "", kind=kind,
                 count=n, window_s=span, msg=last_msg)
    if emit_first:
        diag_log("ffmpeg_issue", level="warning", rid=rid, ref=service_ref,
                 channel=channel or "", kind=kind, msg=msg)


def diag_flush_issues(rid=None, service_ref=None):
    """Schreibt offene Aggregate raus (z.B. beim Stream-Ende), damit der
    Zähler nicht verloren geht."""
    now = time.monotonic()
    out = []
    with _diag_issue_lock:
        for key in list(_diag_issue_agg):
            k_rid, k_ref, k_kind = key
            if rid and k_rid != rid:
                continue
            if service_ref and k_ref != service_ref:
                continue
            e = _diag_issue_agg.pop(key)
            if e["n"] > 1:
                out.append((k_rid, k_ref, k_kind, e["n"] - 1, now - e["start"], e["msg"]))
    for k_rid, k_ref, k_kind, n, span, msg in out:
        diag_log("ffmpeg_issue_summary", level="warning", rid=k_rid, ref=k_ref,
                 kind=k_kind, count=n, window_s=span, msg=msg)


def iter_ffmpeg_lines(pipe, chunk=4096, max_line=65536):
    """Liest ffmpeg-stderr und liefert einzelne Meldungen.

    Nicht zeilenweise iterieren: ffmpeg trennt Statuszeilen mit \\r. Ein
    `for line in pipe` würde alles bis zum nächsten \\n zu einer einzigen Zeile
    verkleben — echte Meldungen kämen dann mit KB an Fortschrittstext im Log an.
    Deshalb blockweise lesen und an \\r UND \\n trennen.
    """
    buf = b""
    while True:
        block = pipe.read(chunk)
        if not block:
            break
        buf += block
        buf = buf.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        *complete, buf = buf.split(b"\n")
        for raw in complete:
            s = raw.decode("utf-8", errors="replace").strip()
            if s:
                yield s
        if len(buf) > max_line:      # Schutz gegen endlose Zeile ohne Trenner
            s = buf.decode("utf-8", errors="replace").strip()
            buf = b""
            if s:
                yield s
    if buf:
        s = buf.decode("utf-8", errors="replace").strip()
        if s:
            yield s


def diag_stream_start(rid, service_ref, mode, channel=None, client=None, profile=None):
    """Registriert einen Stream für die Stillstands-Überwachung."""
    global _diag_counter
    st = receivers._receiver_state.get(rid)
    if isinstance(st, dict):
        channel = channel or st.get("channel_name")
        client = client or st.get("client_ip")
        profile = profile or st.get("profile")
    now = time.monotonic()
    with _diag_lock:
        _diag_counter += 1
        diag_id = f"s{_diag_counter:06d}"
    d = {
        "id": diag_id, "rid": rid, "ref": service_ref, "mode": mode,
        "channel": channel or "", "client": client or "", "profile": profile or "",
        "t0": now, "last_data": now, "bytes": 0,
        "read_wait": 0.0, "write_wait": 0.0,
        "stall_count": 0, "stall_total": 0.0,
        "stall_since": None, "stall_kind": None, "stall_noted": 0.0,
        "last_sample": now, "last_sample_bytes": 0,
    }
    with _diag_lock:
        _diag_streams[diag_id] = d
    diag_log("stream_start", rid=rid, sid=diag_id, ref=service_ref, mode=mode,
             channel=d["channel"], client=d["client"], profile=d["profile"])
    return d


def diag_stream_end(d, reason="client_closed"):
    """Meldet einen Stream ab und schreibt die Zusammenfassung."""
    if not d:
        return
    with _diag_lock:
        _diag_streams.pop(d["id"], None)
    dur = max(0.001, time.monotonic() - d["t0"])
    diag_log("stream_end",
             level=("warning" if d["stall_count"] else "info"),
             rid=d["rid"], sid=d["id"], ref=d["ref"], mode=d["mode"],
             channel=d["channel"], client=d["client"], reason=reason,
             dur_s=dur, mb=d["bytes"] / 1048576.0,
             kbps=(d["bytes"] * 8 / 1000.0) / dur,
             read_wait_s=d["read_wait"], write_wait_s=d["write_wait"],
             stalls=d["stall_count"], stall_s=d["stall_total"])


def diag_note_chunk(d, nbytes, read_wait, write_wait):
    """Bucht einen gelieferten Chunk inkl. der Wartezeiten davor."""
    if not d:
        return
    d["bytes"] += nbytes
    d["read_wait"] += read_wait
    d["write_wait"] += write_wait
    d["last_data"] = time.monotonic()
    # Ein laufender Stillstand ist mit dem Chunk beendet → Ende protokollieren.
    if d["stall_since"] is not None:
        gap = d["last_data"] - d["stall_since"]
        d["stall_since"] = None
        d["stall_total"] += gap
        diag_log("stall_end", level="warning", rid=d["rid"], sid=d["id"],
                 ref=d["ref"], mode=d["mode"], channel=d["channel"],
                 client=d["client"], kind=d.get("stall_kind") or "unknown",
                 gap_s=gap, mb=d["bytes"] / 1048576.0)
        d["stall_kind"] = None


def _diag_watchdog_loop():
    """Meldet Stillstände, während sie passieren, plus optionale Samples."""
    while True:
        time.sleep(1.0)
        try:
            now = time.monotonic()
            sample_on = diag_enabled()
            with _diag_lock:
                streams = list(_diag_streams.values())
            for d in streams:
                gap = now - d["last_data"]
                if gap >= DIAG_STALL_SEC:
                    if d["stall_since"] is None:
                        # Wer blockiert? Die Schleife hängt entweder im Lesen
                        # (Upstream liefert nichts) oder im Schreiben (Client
                        # nimmt nichts ab) — das zuletzt gesetzte Phasen-Flag
                        # sagt es eindeutig.
                        d["stall_since"] = d["last_data"]
                        d["stall_count"] += 1
                        d["stall_noted"] = now
                        d["stall_kind"] = ("client_backpressure"
                                           if d.get("phase") == "write"
                                           else "upstream_no_data")
                        diag_log("stall_start", level="warning", rid=d["rid"],
                                 sid=d["id"], ref=d["ref"], mode=d["mode"],
                                 channel=d["channel"], client=d["client"],
                                 kind=d["stall_kind"], gap_s=gap,
                                 mb=d["bytes"] / 1048576.0)
                    elif now - d["stall_noted"] >= DIAG_STALL_REPEAT:
                        d["stall_noted"] = now
                        diag_log("stall_ongoing", level="warning", rid=d["rid"],
                                 sid=d["id"], ref=d["ref"], mode=d["mode"],
                                 channel=d["channel"], client=d["client"],
                                 kind=d["stall_kind"],
                                 gap_s=now - d["stall_since"])
                if sample_on and now - d["last_sample"] >= DIAG_SAMPLE_SEC:
                    span = now - d["last_sample"]
                    delta = d["bytes"] - d["last_sample_bytes"]
                    d["last_sample"] = now
                    d["last_sample_bytes"] = d["bytes"]
                    diag_log("sample", rid=d["rid"], sid=d["id"], ref=d["ref"],
                             mode=d["mode"], channel=d["channel"],
                             client=d["client"],
                             kbps=(delta * 8 / 1000.0) / max(0.001, span),
                             mb=d["bytes"] / 1048576.0,
                             read_wait_s=d["read_wait"],
                             write_wait_s=d["write_wait"],
                             stalls=d["stall_count"],
                             dur_s=now - d["t0"])
        except Exception as e:
            log.debug(f"Diag-Watchdog Fehler: {e}")


def diag_active_streams():
    """Snapshot der überwachten Streams für die API/UI."""
    now = time.monotonic()
    out = []
    with _diag_lock:
        streams = list(_diag_streams.values())
    for d in streams:
        dur = max(0.001, now - d["t0"])
        out.append({
            "sid": d["id"], "rid": d["rid"], "ref": d["ref"], "mode": d["mode"],
            "channel": d["channel"], "client": d["client"],
            "duration_s": round(dur, 1),
            "mb": round(d["bytes"] / 1048576.0, 2),
            "kbps": round((d["bytes"] * 8 / 1000.0) / dur, 1),
            "read_wait_s": round(d["read_wait"], 2),
            "write_wait_s": round(d["write_wait"], 2),
            "stalls": d["stall_count"],
            "stall_total_s": round(d["stall_total"], 2),
            "stalled_since_s": (round(now - d["stall_since"], 1)
                                if d["stall_since"] is not None else None),
        })
    return out


