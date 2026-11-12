"""Receiver (tuner) state, allocation, circuit breaker, zapping and watchdog."""
import json
import socket
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

from e2proxy.logs import log
from e2proxy.config import get_receiver_by_id, get_receivers, is_receiver_usable
from e2proxy.switching import get_switch_settings, record_zap_result

# ── Receiver State Management ─────────────────────────────

receiver_lock = threading.RLock()
_receiver_state = {}   # receiver_id → None | dict(info)
# Auch ffmpeg-Prozesse können sich einen Tuner teilen (Shared Tuner mit
# Transcode-Profil) — deshalb Set statt Einzelwert: sonst überschreibt der
# zweite Stream den Eintrag des ersten, /kill erwischt den falschen Prozess und
# der andere zieht als Waise weiter Daten vom Receiver.
stream_processes = {}  # receiver_id → set(subprocess.Popen)
stream_processes_lock = threading.Lock()
# Passthrough-Streams laufen als reine Socket-Schleife ohne Subprozess. Damit ein
# /kill sie tatsächlich beendet (und nicht nur den Receiver-State freigibt), werden
# die Upstream-Sockets je Receiver hier registriert und beim Kill hart geschlossen.
# Mehrere Zuschauer können denselben Tuner teilen (Shared Tuner) — deshalb ein Set
# pro Receiver statt eines einzelnen Sockets: sonst überschreibt der zweite Stream
# den Eintrag des ersten und dessen Socket bleibt beim Kill offen (Tuner-Leak).
stream_sockets = {}    # receiver_id → set(upstream sockets, passthrough)
stream_sockets_lock = threading.Lock()

# Client dedup - prevent Kodi parallel probe requests
client_stream_lock = threading.Lock()
_active_client_streams = {}  # (client_ip, service_ref) → timestamp


def _init_receiver_state():
    global _receiver_state
    with receiver_lock:
        for r in get_receivers():
            if r["id"] not in _receiver_state:
                _receiver_state[r["id"]] = None


# ── Receiver Circuit Breaker ──────────────────────────────
# Eine Enigma2-Box kann "halb tot" sein: der TCP-Port ist offen, aber Enigma2
# antwortet nie auf HTTP (typisch nach einem Stromausfall). Jeder Zap lief dann
# in 3 × 8 s Timeout, und weil die defekte Box der Default-Receiver war, blockierte
# sie den kompletten Streaming-Pfad — obwohl der zweite Tuner frei und gesund war.
#
# Der Breaker merkt sich Fehlversuche: nach RECEIVER_FAIL_THRESHOLD Fehlern gilt
# ein Receiver für RECEIVER_DOWN_COOLDOWN Sekunden als "down" und wird bei der
# Auswahl übersprungen. Danach darf er sich automatisch wieder bewähren.

RECEIVER_FAIL_THRESHOLD = 2    # Fehlversuche in Folge bis "down"
RECEIVER_DOWN_COOLDOWN  = 60   # Sekunden, die ein Receiver als down gilt
RECEIVER_PROBE_TIMEOUT  = 3    # HTTP-Timeout des Online-Checks
RECEIVER_PROBE_TTL      = 10   # Sekunden, die ein Probe-Ergebnis gecacht wird
RECEIVER_WATCHDOG_INTERVAL = 30  # Probe-Intervall des Hintergrund-Watchdogs

_receiver_health = {}          # rid → dict(fails, down_until, probe_ts, probe_ok, fail_since)
_receiver_health_lock = threading.RLock()


def _health_entry(rid):
    with _receiver_health_lock:
        return _receiver_health.setdefault(
            rid, {"fails": 0, "down_until": 0.0, "probe_ts": 0.0, "probe_ok": None,
                  "fail_since": 0.0})


def mark_receiver_failure(rid, reason=""):
    """Zählt einen Fehlversuch und öffnet ggf. den Circuit Breaker."""
    with _receiver_health_lock:
        h = _health_entry(rid)
        if h["fails"] == 0:
            h["fail_since"] = time.time()   # Beginn des Ausfalls (für Alarm/UI)
        h["fails"] += 1
        h["probe_ts"] = 0.0          # Probe-Cache verwerfen
        h["probe_ok"] = None
        if h["fails"] >= RECEIVER_FAIL_THRESHOLD and h["down_until"] <= time.time():
            h["down_until"] = time.time() + RECEIVER_DOWN_COOLDOWN
            log.warning(f"Receiver '{rid}' als DOWN markiert für "
                        f"{RECEIVER_DOWN_COOLDOWN}s nach {h['fails']} Fehlern"
                        f"{f': {reason}' if reason else ''}")


def mark_receiver_ok(rid):
    """Setzt den Breaker zurück — der Receiver hat wieder geantwortet."""
    with _receiver_health_lock:
        h = _health_entry(rid)
        was_down = h["down_until"] > time.time()
        h["fails"] = 0
        h["fail_since"] = 0.0
        h["down_until"] = 0.0
        h["probe_ts"] = time.time()
        h["probe_ok"] = True
    if was_down:
        log.info(f"Receiver '{rid}' wieder erreichbar — DOWN aufgehoben")


def is_receiver_down(rid):
    """True, solange der Breaker für diesen Receiver offen ist."""
    with _receiver_health_lock:
        return _health_entry(rid)["down_until"] > time.time()


def is_receiver_unhealthy(rid):
    """True, wenn der Receiver aktuell als ausgefallen gilt.

    Strenger als is_receiver_down(): der Breaker ist nur 60 s am Stück offen und
    schließt sich zwischen zwei Watchdog-Probes kurz. Wer nur auf den Breaker
    schaut, landet in diesen Lücken wieder bei der toten Box. Maßgeblich ist
    deshalb zusätzlich die Fehlerserie — sie wird erst durch eine erfolgreiche
    Antwort (mark_receiver_ok) zurückgesetzt.
    """
    with _receiver_health_lock:
        h = _health_entry(rid)
        return h["down_until"] > time.time() or h["fails"] >= RECEIVER_FAIL_THRESHOLD


def receiver_health_snapshot():
    """Health-Zustand aller Receiver für API/UI."""
    now = time.time()
    out = {}
    with _receiver_health_lock:
        for rid, h in _receiver_health.items():
            unhealthy = h["down_until"] > now or h["fails"] >= RECEIVER_FAIL_THRESHOLD
            since = h.get("fail_since") or 0.0
            out[rid] = {
                "fails": h["fails"],
                "down": h["down_until"] > now,
                "down_for_sec": max(0, int(h["down_until"] - now)),
                "unhealthy": unhealthy,
                "unreachable_sec": int(now - since) if (unhealthy and since) else 0,
                "unreachable_since": (datetime.fromtimestamp(since).strftime("%Y-%m-%d %H:%M")
                                      if (unhealthy and since) else ""),
            }
    return out


def get_free_receiver(preferred_id=None, exclude=None):
    """Gibt ID des ersten freien Receivers zurück. Bevorzugt preferred_id.

    Receiver, für die der Circuit Breaker offen ist, werden übersprungen. Sind
    ALLE Kandidaten als down markiert, wird trotzdem einer zurückgegeben — eine
    Fehleinschätzung des Breakers darf das Streaming nicht dauerhaft blockieren.
    """
    exclude = set(exclude or ())
    with receiver_lock:
        receivers = get_receivers()

        # Prioritätsreihenfolge: preferred → default → Rest
        ordered = []
        if preferred_id and preferred_id != "auto":
            ordered += [r for r in receivers if r["id"] == preferred_id]
        ordered += [r for r in receivers if r.get("default")]
        ordered += receivers

        free, healthy = [], []
        seen = set()
        for r in ordered:
            rid = r["id"]
            if rid in seen or rid in exclude:
                continue
            seen.add(rid)
            if not is_receiver_usable(r) or _receiver_state.get(rid) is not None:
                continue
            free.append(rid)
            if not is_receiver_unhealthy(rid):
                healthy.append(rid)

        if healthy:
            return healthy[0]
        if free:
            log.warning(f"Alle freien Receiver sind als down markiert — "
                        f"versuche '{free[0]}' trotzdem")
            return free[0]
    return None


# Lock pro service_ref — verhindert dass derselbe Sender gleichzeitig zweimal started wird
_stream_ref_locks = {}
_stream_ref_locks_lock = threading.Lock()

def get_ref_lock(service_ref):
    with _stream_ref_locks_lock:
        if service_ref not in _stream_ref_locks:
            _stream_ref_locks[service_ref] = threading.Lock()
        return _stream_ref_locks[service_ref]


_receiver_epoch = 0


def acquire_receiver(rid, client_ip, service_ref="", channel_name="",
                     user_agent="", profile="", transcode=None):
    """Belegt einen Receiver exklusiv und gibt ein Session-Token zurück.

    Das Token MUSS an release_receiver() zurückgegeben werden. Sonst kann ein
    spät aufräumender Thread (dessen Stream längst per /kill abgeräumt wurde)
    die inzwischen NEU begonnene Session eines anderen Clients freigeben.
    """
    global _receiver_epoch
    with receiver_lock:
        _receiver_epoch += 1
        epoch = _receiver_epoch
        _receiver_state[rid] = {
            "client_ip": client_ip,
            "service_ref": service_ref,
            "channel_name": channel_name,
            "started": datetime.now().strftime("%H:%M:%S"),
            "started_ts": time.time(),
            "user_agent": user_agent or "",
            "profile": profile or "",
            "transcode": transcode or {"active": False},
            "bytes_sent": 0,
            # Referenzzähler: wie viele Streams nutzen diesen Tuner gerade?
            # Erst wenn der letzte geht, wird der Receiver wirklich freigegeben.
            "users": 1,
            "epoch": epoch,
        }
    r = get_receiver_by_id(rid)
    log.info(f"Receiver '{rid}' ({r['name'] if r else rid}) busy from {client_ip}")
    return epoch


def attach_receiver(rid, service_ref=None):
    """Hängt einen weiteren Nutzer an einen bereits belegten Receiver (Shared Tuner).

    Ohne diesen Zähler galt der Tuner als frei, sobald IRGENDEINE der Sessions
    endete — auch wenn noch jemand zuschaute. Der nächste Request nahm den
    vermeintlich freien Receiver und zappte ihn weg → laufender Stream tot.

    Gibt das Session-Token zurück (für release_receiver) oder None, wenn der
    Receiver frei ist bzw. auf einem anderen Sender steht.
    """
    with receiver_lock:
        st = _receiver_state.get(rid)
        if not isinstance(st, dict):
            return None
        if service_ref and st.get("service_ref", "").rstrip("/") != service_ref.rstrip("/"):
            return None
        st["users"] = int(st.get("users", 1)) + 1
        users = st["users"]
        epoch = st.get("epoch")
    log.debug(f"Receiver '{rid}': Stream angehängt (Shared Tuner) — {users} Nutzer")
    return epoch


def note_stream_bytes(rid, total):
    """Aktualisiert live den übertragenen Byte-Zähler eines aktiven Streams.

    Wird aus den Stream-Schleifen (passthrough/transcode) gerufen, damit die
    Stream-Info in der UI zeigen kann, wie viele Daten bereits geflossen sind.
    Bewusst ohne Lock (nur Anzeige): ein Dict-Item-Write ist unter dem GIL
    atomar; zeigt der lokale State-Verweis auf einen bereits freigegebenen
    Receiver, ist der Write harmlos.
    """
    st = _receiver_state.get(rid)
    if isinstance(st, dict):
        st["bytes_sent"] = total


def human_bytes(n):
    """Formatiert eine Byte-Zahl menschenlesbar (B/KB/MB/GB)."""
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "0 B"
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{int(n)} B"


def describe_client(ua):
    """Leitet aus dem User-Agent eine kurze, menschenlesbare Software-Kennung ab.

    Reicht für die typischen e2proxy-Clients (Browser, Kodi, VLC, Dream Player,
    Plex, Jellyfin, ffmpeg, Android). Ist nichts erkennbar, wird der erste
    Token des User-Agents bzw. "Unbekannt" zurückgegeben.
    """
    if not ua:
        return "Unbekannt"
    u = ua.lower()
    checks = [
        ("vlc", "VLC"),
        ("kodi", "Kodi"),
        ("exoplayer", "ExoPlayer (Dream Player)"),
        ("dreamplayer", "Dream Player"),
        ("plex", "Plex"),
        ("jellyfin", "Jellyfin"),
        ("threadfin", "Threadfin/Plex"),
        ("infuse", "Infuse"),
        ("mpv", "mpv"),
        ("gstreamer", "GStreamer"),
        ("lavf", "ffmpeg/Lavf"),
        ("ffmpeg", "ffmpeg"),
        ("libcurl", "libcurl"),
        ("e2proxy", "e2proxy (intern)"),
        ("stagefright", "Android"),
        ("dalvik", "Android"),
        ("edg", "Edge (Browser)"),
        ("chrome", "Chrome (Browser)"),
        ("firefox", "Firefox (Browser)"),
        ("safari", "Safari (Browser)"),
        ("mozilla", "Browser"),
    ]
    for needle, label in checks:
        if needle in u:
            return label
    return ua.split("/")[0].split()[0][:32] or "Unbekannt"


def stream_info_public(state):
    """Baut ein angereichertes, UI-taugliches Stream-Info-Objekt aus dem State.

    Ergänzt Rohdaten (User-Agent, Bytes, Startzeit) um abgeleitete Felder:
    erkannte Software, menschenlesbare Datenmenge, Laufzeit und
    Transcode-Zusammenfassung. Gibt None zurück, wenn kein Stream aktiv ist.
    """
    if not isinstance(state, dict):
        return None
    ua = state.get("user_agent", "") or ""
    bytes_sent = state.get("bytes_sent", 0) or 0
    started_ts = state.get("started_ts")
    duration = int(time.time() - started_ts) if started_ts else 0
    tc = state.get("transcode") or {}
    active = bool(tc.get("active"))
    return {
        "channel_name": state.get("channel_name", ""),
        "client_ip": state.get("client_ip", ""),
        "started": state.get("started", ""),
        "service_ref": state.get("service_ref", ""),
        "user_agent": ua,
        "software": describe_client(ua),
        "profile": state.get("profile", ""),
        "bytes": bytes_sent,
        "bytes_human": human_bytes(bytes_sent),
        "duration_sec": duration,
        "transcode": {
            "active": active,
            "codec": tc.get("codec", "") or "",
            "label": tc.get("label", "") or "",
        },
    }


def release_receiver(rid, epoch=None, force=False):
    """Gibt einen Receiver frei — aber erst, wenn der LETZTE Nutzer geht.

    epoch: Session-Token aus acquire_receiver()/attach_receiver(). Passt es nicht
    mehr zum aktuellen State, gehört der Receiver bereits einer anderen Session
    (z.B. nach /kill + sofortigem Neuaufbau) — dann NICHTS tun. Ohne diese Prüfung
    reißt ein spät aufräumender Thread die frische Session eines anderen Clients ab.
    force=True (z.B. /kill) reißt den Receiver unabhängig vom Zähler los.
    """
    with receiver_lock:
        st = _receiver_state.get(rid)
        if (epoch is not None and isinstance(st, dict)
                and st.get("epoch") is not None and st["epoch"] != epoch):
            log.debug(f"Receiver '{rid}': verspätete Freigabe einer alten Session "
                      f"(epoch {epoch} ≠ {st['epoch']}) — ignoriert")
            return
        if epoch is not None and not isinstance(st, dict):
            return          # bereits freigegeben (z.B. durch /kill)
        users = int(st.get("users", 1)) if isinstance(st, dict) else 0
        if isinstance(st, dict) and not force:
            users -= 1
            st["users"] = users
        freed = force or users <= 0
        if freed:
            _receiver_state[rid] = None
    r = get_receiver_by_id(rid)
    name = r["name"] if r else rid
    if freed:
        log.info(f"Receiver '{rid}' ({name}) released")
    else:
        log.info(f"Receiver '{rid}' ({name}) weiterhin belegt — "
                 f"{users} Stream(s) aktiv, keine Freigabe")


def kill_proc_robust(proc, label="", grace=5):
    """Beendet einen Prozess zuverlässig: erst SIGTERM, nach `grace`s SIGKILL.

    Ein netzwerk-blockiertes ffmpeg reagiert oft nicht auf SIGTERM und würde
    sonst als Zombie weiterlaufen (Tuner-/Stream-Leak). Die SIGKILL-Eskalation
    garantiert, dass der Prozess wirklich verschwindet.
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=grace)
        return
    except Exception:
        pass
    try:
        proc.kill()
        log.warning(f"{label or 'Prozess'}: reagiert nicht auf SIGTERM — SIGKILL gesendet")
    except Exception:
        pass


def kill_stream(rid):
    with stream_processes_lock:
        procs = list(stream_processes.get(rid) or ())
    for proc in procs:
        kill_proc_robust(proc, label=f"Stream '{rid}'")
    # Passthrough-Streams haben keinen Subprozess: ALLE Upstream-Sockets dieses
    # Receivers hart schließen (bei Shared Tuner sind es mehrere), damit jede
    # recv()-Schleife bricht und der Tuner der Box wirklich frei wird.
    with stream_sockets_lock:
        socks = list(stream_sockets.get(rid) or ())
    for sock in socks:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass
    release_receiver(rid, force=True)
    log.info(f"Stream auf Receiver '{rid}' abgebrochen")


def is_receiver_online(rid):
    """Prüft, ob die Box HTTP beantwortet — mit Kurzzeit-Cache.

    Der Check hing früher an jedem /api/status-Aufruf: eine tote Box kostete
    3 s pro Request und ließ damit sogar den Container-Healthcheck (Timeout 3 s)
    fehlschlagen. Ergebnisse werden deshalb RECEIVER_PROBE_TTL Sekunden gecacht,
    und ein bereits als down bekannter Receiver wird gar nicht erst angefragt.
    """
    r = get_receiver_by_id(rid)
    if not r:
        return False

    with _receiver_health_lock:
        h = _health_entry(rid)
        if h["probe_ok"] is not None and (time.time() - h["probe_ts"]) < RECEIVER_PROBE_TTL:
            return h["probe_ok"]
        if h["down_until"] > time.time():
            return False        # Breaker offen — nicht erneut blockieren

    try:
        url = f"http://{r['ip']}:{r['port']}/web/about"
        with urllib.request.urlopen(url, timeout=RECEIVER_PROBE_TIMEOUT) as resp:
            ok = resp.status == 200
    except Exception as e:
        mark_receiver_failure(rid, f"Probe: {e}")
        return False
    if ok:
        mark_receiver_ok(rid)
    else:
        mark_receiver_failure(rid, "Probe: HTTP != 200")
    return ok


ZAP_TIMEOUT_SEC = 8      # einzelner Zap-Versuch (Boxen brauchen erwiesen bis ~7s)
ZAP_ATTEMPTS    = 3      # inkl. Erstversuch


def receiver_current_ref(rid, timeout=4):
    """Aktuell eingestellte Service-Reference der Box (oder None)."""
    r = get_receiver_by_id(rid)
    if not r:
        return None
    try:
        url = f"http://{r['ip']}:{r['port']}/api/statusinfo"
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        ref = (data.get("currservice_serviceref") or "").strip()
        return ref or None
    except Exception:
        return None


def do_zap(rid, service_ref, channel_name=None):
    """Schaltet die Box um — mit Wiederholung.

    Ein Timeout heißt NICHT, dass der Zap fehlschlug: langsame Boxen antworten
    erst nach mehreren Sekunden, haben aber längst umgeschaltet. Früher galt der
    Stream dann als gescheitert, der Client versuchte es sofort erneut und
    erzeugte parallele Sessions auf demselben Tuner. Deshalb: nach einem Fehler
    erst den Ist-Zustand der Box prüfen, dann erst erneut zappen.
    """
    r = get_receiver_by_id(rid)
    if not r:
        return False
    encoded = urllib.parse.quote(service_ref, safe=":@")
    url = f"http://{r['ip']}:{r['port']}/web/zap?sRef={encoded}"
    wait_used = get_switch_settings(service_ref)["zap_wait"]
    t0 = time.time()
    last_err = None
    # Bekannt ausgefallene Box: nur ein Versuch — sonst warten Clients wie Plex
    # 3×8 s, bevor der Failover greift, und brechen vorher selbst ab.
    attempts = 1 if is_receiver_unhealthy(rid) else ZAP_ATTEMPTS

    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=ZAP_TIMEOUT_SEC) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            elapsed_ms = (time.time() - t0) * 1000
            if "<e2state>True</e2state>" in body:
                log.info(f"ZAP OK → '{rid}' → {service_ref} ({elapsed_ms:.0f}ms"
                         f"{f', Versuch {attempt}' if attempt > 1 else ''})")
                record_zap_result(service_ref, True, elapsed_ms, wait_used, channel_name)
                mark_receiver_ok(rid)
                return True
            last_err = f"e2state != True: {body[:100]}"
        except Exception as e:
            last_err = str(e)

        # Box antwortet nicht (rechtzeitig) — steht sie trotzdem schon richtig?
        cur = receiver_current_ref(rid)
        if cur and cur.rstrip("/") == service_ref.rstrip("/"):
            elapsed_ms = (time.time() - t0) * 1000
            log.info(f"ZAP OK (verifiziert trotz '{last_err}') → '{rid}' → "
                     f"{service_ref} ({elapsed_ms:.0f}ms)")
            record_zap_result(service_ref, True, elapsed_ms, wait_used, channel_name)
            mark_receiver_ok(rid)
            return True

        if attempt < attempts:
            log.warning(f"ZAP Versuch {attempt}/{attempts} für '{rid}' "
                        f"fehlgeschlagen ({last_err}) — neuer Versuch")
            time.sleep(1.0)

    elapsed_ms = (time.time() - t0) * 1000
    log.error(f"ZAP ERROR nach {attempts} Versuch(en) auf '{rid}': {last_err}")
    record_zap_result(service_ref, False, elapsed_ms, wait_used, channel_name)
    mark_receiver_failure(rid, f"Zap: {last_err}")
    return False


def zap_with_failover(rid, epoch, service_ref, channel_name,
                      acquire_kwargs, preferred_id=None, source="Stream"):
    """Zappt den Receiver — weicht bei Ausfall auf einen anderen Tuner aus.

    Bisher beendete ein fehlgeschlagener Zap den Request sofort mit 502. Hing die
    Default-Box (Stromausfall: TCP offen, aber keine HTTP-Antwort), scheiterte
    damit jeder Stream — obwohl ein zweiter, gesunder Tuner frei war. Jetzt wird
    der defekte Receiver freigegeben, im Circuit Breaker als down markiert und
    der nächste freie Tuner probiert.

    Rückgabe: (rid, epoch) des erfolgreich gezappten Receivers, sonst (None, None).
    Bei Misserfolg sind alle belegten Receiver bereits freigegeben.
    """
    tried = []
    while rid:
        if do_zap(rid, service_ref, channel_name):
            return rid, epoch

        tried.append(rid)
        release_receiver(rid, epoch=epoch)

        # Nächsten gesunden Tuner suchen und atomar belegen
        with receiver_lock:
            nxt = get_free_receiver(preferred_id, exclude=tried)
            nxt_epoch = acquire_receiver(nxt, **acquire_kwargs) if nxt else None

        if not nxt:
            log.error(f"{source}: Zap auf allen Receivern fehlgeschlagen "
                      f"(versucht: {', '.join(tried)})")
            return None, None

        log.warning(f"{source}: Receiver '{rid}' antwortet nicht — "
                    f"Failover auf '{nxt}'")
        rid, epoch = nxt, nxt_epoch
    return None, None


def receiver_watchdog_loop():
    """Prüft periodisch die Erreichbarkeit aller Receiver.

    Ohne aktiven Probe merkt der Proxy einen Box-Ausfall erst, wenn ein Client
    streamen will — und der Breaker schließt sich erst wieder, wenn es jemand
    erneut versucht. Der Watchdog hält den Zustand aktuell und protokolliert
    Statuswechsel, sodass ein Ausfall in Loki/Grafana sichtbar wird.
    """
    time.sleep(20)   # Startup nicht ausbremsen
    last = {}
    while True:
        try:
            for r in get_receivers():
                if not is_receiver_usable(r):
                    continue
                rid = r["id"]
                # Belegte Tuner nicht unnötig anfragen — sie antworten gerade
                # ohnehin und ein Probe kostet den laufenden Stream Latenz.
                if _receiver_state.get(rid) is not None:
                    continue
                ok = is_receiver_online(rid)
                if last.get(rid) is not None and last[rid] != ok:
                    if ok:
                        log.info(f"DIAG event=receiver_state rid={rid} state=up")
                    else:
                        log.warning(f"DIAG event=receiver_state rid={rid} state=down "
                                    f"ip={r.get('ip')}")
                last[rid] = ok
        except Exception as e:
            log.debug(f"Receiver-Watchdog: {e}")
        try:
            from e2proxy import notify  # lazy: notify depends on receivers
            notify.check_receiver_alerts()
        except Exception as e:
            log.debug(f"Receiver-Alarm: {e}")
        time.sleep(RECEIVER_WATCHDOG_INTERVAL)
