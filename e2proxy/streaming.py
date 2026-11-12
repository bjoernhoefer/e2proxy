"""Live streaming: raw TS passthrough with jitter buffer and ffmpeg transcoding."""
import collections
import select
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from e2proxy.logs import log
from e2proxy.config import get_config, get_receiver_by_id
from e2proxy.switching import _PROBE_MAX, get_switch_settings, record_stream_start
from e2proxy.receivers import (
    note_stream_bytes,
    stream_processes,
    stream_processes_lock,
    stream_sockets,
    stream_sockets_lock,
)
from e2proxy.diag import (
    diag_enabled,
    diag_flush_issues,
    diag_log_issue,
    diag_note_chunk,
    diag_stream_end,
    diag_stream_start,
    iter_ffmpeg_lines,
)

class ScrambledStreamError(Exception):
    pass
# ── Streaming ─────────────────────────────────────────────

CHUNK_SIZE = 65536

# ── Jitter-Puffer für Passthrough-Streams ────────────────────────────
# Vorpuffer in Sekunden; 0 = aus (direktes Durchreichen wie früher).
PASSTHRU_PREBUFFER_DEFAULT = 3.0
PASSTHRU_PREBUFFER_MAX     = 15.0
# Harte Obergrenze pro Stream, damit ein langsamer Client keinen Speicher frisst.
PASSTHRU_BUFFER_MAX_BYTES  = 24 * 1024 * 1024


def get_passthrough_prebuffer_sec():
    """Vorpuffer-Dauer für Passthrough-Streams (konfigurierbar, geklemmt)."""
    try:
        v = float(get_config().get("passthrough_prebuffer_sec",
                                   PASSTHRU_PREBUFFER_DEFAULT))
    except (TypeError, ValueError):
        v = PASSTHRU_PREBUFFER_DEFAULT
    return max(0.0, min(PASSTHRU_PREBUFFER_MAX, v))


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
    _last_note = 0.0
    d = diag_stream_start(rid, service_ref, "passthrough")
    with stream_sockets_lock:
        stream_sockets.setdefault(rid, set()).add(sock)

    # ── Jitter-Puffer ────────────────────────────────────────────────
    # Schwache Receiver (z.B. Vu+ Zero4K) liefern das TS in Schüben und legen
    # dabei regelmäßig 3–6s Pausen ein. Ohne Puffer schlägt jede dieser Pausen
    # 1:1 auf den Client durch → Bild friert ein / Player bricht ab. Ein
    # Reader-Thread füllt deshalb einen Vorpuffer, aus dem der Client
    # gleichmäßig bedient wird. Der Puffer ist gemessen statt geraten: nach
    # `prebuffer_sec` Sekunden Füllzeit steht genau eine Sekundenmenge des
    # tatsächlichen Bitratenprofils bereit.
    prebuffer_sec = get_passthrough_prebuffer_sec()
    max_bytes = PASSTHRU_BUFFER_MAX_BYTES
    q = collections.deque()
    cv = threading.Condition()
    st = {"bytes": 0, "eof": False, "reason": None, "stop": False}

    def _set_eof(reason=None):
        with cv:
            if reason and not st["reason"]:
                st["reason"] = reason
            st["eof"] = True
            cv.notify_all()

    def _reader():
        try:
            while True:
                d["phase"] = "read"
                t_a = time.monotonic()
                chunk = sock.recv(CHUNK_SIZE)
                t_b = time.monotonic()
                if not chunk:
                    _set_eof("upstream_eof")
                    return
                # Upstream-Ankunft treibt die Stall-Erkennung — write_wait bucht
                # der Writer-Thread separat.
                diag_note_chunk(d, len(chunk), t_b - t_a, 0.0)
                with cv:
                    while st["bytes"] >= max_bytes and not st["stop"]:
                        cv.wait(0.5)
                    if st["stop"]:
                        return
                    q.append(chunk)
                    st["bytes"] += len(chunk)
                    cv.notify_all()
        except socket.timeout:
            log.warning(f"Passthrough {service_ref}: 30s ohne Daten vom Receiver — Stream beendet")
            _set_eof("upstream_timeout")
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            _set_eof(type(e).__name__)
        except Exception as e:
            _set_eof(type(e).__name__)
        finally:
            _set_eof()

    reader = threading.Thread(target=_reader, daemon=True,
                              name=f"passthru-read-{rid}")
    reader.start()

    end_reason = "client_closed"
    try:
        # Vorpuffer füllen (abbrechen, sobald Upstream endet oder Puffer voll ist)
        if prebuffer_sec > 0:
            deadline = time.monotonic() + prebuffer_sec
            with cv:
                while (time.monotonic() < deadline and not st["eof"]
                       and st["bytes"] < max_bytes):
                    cv.wait(min(0.25, max(0.01, deadline - time.monotonic())))
                filled = st["bytes"]
            log.debug(f"Passthrough {service_ref}: Vorpuffer {filled/1024:.0f} KB "
                      f"in {prebuffer_sec:.1f}s")

        while True:
            with cv:
                while not q and not st["eof"]:
                    cv.wait(1.0)
                if not q:
                    end_reason = st["reason"] or "upstream_eof"
                    break
                chunk = q.popleft()
                st["bytes"] -= len(chunk)
                cv.notify_all()

            d["phase"] = "write"
            t_b = time.monotonic()
            if use_chunked:
                write_chunked(wfile, chunk)
            else:
                wfile.write(chunk)
                wfile.flush()
            t_c = time.monotonic()
            d["phase"] = "read"
            # Nur die Client-Wartezeit buchen; die Bytes hat der Reader gemeldet.
            d["write_wait"] += (t_c - t_b)
            bytes_sent += len(chunk)
            now = time.time()
            if now - _last_note >= 1.0:
                note_stream_bytes(rid, bytes_sent)
                _last_note = now
    except (BrokenPipeError, ConnectionResetError, OSError) as e:
        end_reason = f"{type(e).__name__}"
    finally:
        with cv:
            st["stop"] = True
            cv.notify_all()
        diag_stream_end(d, end_reason)
        with stream_sockets_lock:
            socks = stream_sockets.get(rid)
            if socks is not None:
                socks.discard(sock)
                if not socks:
                    stream_sockets.pop(rid, None)
        try:
            # shutdown vor close: ein in recv() blockierender Reader-Thread wacht
            # sonst je nach Plattform erst nach dem 30s-Timeout auf.
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        # Erst joinen, DANN close(): sonst könnte der Reader nach dem close()
        # noch auf den fd zugreifen, den das OS zwischenzeitlich an eine neue
        # Verbindung vergeben hat.
        reader.join(timeout=2)
        try:
            sock.close()
        except Exception:
            pass
        note_stream_bytes(rid, bytes_sent)
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
        "ffmpeg", "-loglevel", ("verbose" if diag_enabled() else "warning"),
        # -nostats/-hide_banner: ffmpeg schreibt die Fortschrittszeile
        # ("frame=… fps=…") mit \r statt \n auf stderr. Zeilenweises Lesen fasst
        # dadurch minutenlangen Fortschritt zu EINER Riesenzeile zusammen, die
        # dann samt echter Meldung als Warnung im Log landet. Ohne Stats bleibt
        # stderr auf echte Meldungen beschränkt.
        "-nostats", "-hide_banner",
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
    my_procs = []          # alle von DIESEM Aufruf gestarteten ffmpeg-Prozesse
    bytes_sent = 0
    attempts = 0
    d = None
    end_reason = "client_closed"
    # content_type ist probe-unabhängig; sicherer Default bis erster Build
    _, content_type = build_ffmpeg_cmd(rid, service_ref, tp)

    def read_stderr(pipe, sink):
        # ffmpeg-Meldungen sind die wichtigste Quelle bei hängenden Streams.
        # Sie werden klassifiziert, damit man in Loki gezielt nach
        # Transportstrom-Problemen filtern kann statt Freitext zu greppen.
        FF_PROBLEMS = (
            ("scrambled", "scrambled"), ("conditional access", "scrambled"),
            ("not authorized", "scrambled"),
            ("non-monotonous dts", "dts"), ("non-monotonic dts", "dts"),
            ("pes packet size mismatch", "ts_corrupt"),
            ("invalid data found", "ts_corrupt"),
            ("corrupt", "ts_corrupt"),
            ("cc error", "cc_error"), ("continuity", "cc_error"),
            ("buffer underflow", "underflow"),
            ("circular buffer overrun", "overrun"),
            ("timed out", "timeout"), ("timeout", "timeout"),
            ("connection reset", "conn"), ("connection refused", "conn"),
            ("error opening input", "input_error"),
        )

        def handle(l):
            if not l:
                return
            sink.append(l)
            kind = next((k for needle, k in FF_PROBLEMS if needle in l.lower()), None)
            if kind:
                diag_log_issue(rid, service_ref, channel_name, kind, l)
            else:
                log.debug(f"ffmpeg[{label}] {service_ref}: {l}")

        try:
            for l in iter_ffmpeg_lines(pipe):
                handle(l)
        except Exception:
            pass
        finally:
            diag_flush_issues(rid, service_ref)

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
                stream_processes.setdefault(rid, set()).add(proc)
            my_procs.append(proc)
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
                procs = stream_processes.get(rid)
                if procs is not None:
                    procs.discard(proc)
                    if not procs:
                        stream_processes.pop(rid, None)
            if proc in my_procs:
                my_procs.remove(proc)
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
        d = diag_stream_start(rid, service_ref, "transcode", channel=channel_name,
                              profile=label)
        chunk = first_chunk
        _last_note = 0.0
        _read_wait = 0.0
        while chunk:
            d["phase"] = "write"
            t_b = time.monotonic()
            if use_chunked:
                write_chunked(wfile, chunk)
            else:
                wfile.write(chunk)
                wfile.flush()
            t_c = time.monotonic()
            bytes_sent += len(chunk)
            # Die Wartezeit des vorangegangenen Reads gehört zu diesem Chunk.
            diag_note_chunk(d, len(chunk), _read_wait, t_c - t_b)
            now = time.time()
            if now - _last_note >= 1.0:
                note_stream_bytes(rid, bytes_sent)
                _last_note = now
            d["phase"] = "read"
            t_a = time.monotonic()
            chunk = proc.stdout.read(CHUNK_SIZE)
            _read_wait = time.monotonic() - t_a
        end_reason = "ffmpeg_eof"

    except ScrambledStreamError:
        raise
    except (BrokenPipeError, ConnectionResetError, OSError) as e:
        end_reason = type(e).__name__
    finally:
        diag_stream_end(d, end_reason)
        with stream_processes_lock:
            procs = stream_processes.get(rid)
            if procs is not None:
                # Nur die eigenen Prozesse abmelden — ein parallel laufender
                # Shared-Tuner-Stream muss registriert bleiben.
                for p in my_procs:
                    procs.discard(p)
                if not procs:
                    stream_processes.pop(rid, None)
        if proc:
            _kill(proc)
        note_stream_bytes(rid, bytes_sent)
    log.info(f"ffmpeg END: {service_ref} — {bytes_sent/1024/1024:.1f} MB")
    return bytes_sent, content_type



