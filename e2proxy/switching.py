"""Per-channel switch tuning (zap wait, NoLatency, probesize) and its statistics/analysis."""
import copy
import json
import math
import os
import threading
from datetime import datetime

from e2proxy.paths import DATA_DIR
from e2proxy.logs import log
from e2proxy.config import get_config

# ── Umschalt-Tuning (Switch Time) ─────────────────────────
# Globale Defaults für schnelles Umschalten. Pro-Sender-Overrides und
# selbstlernende Werte liegen im SwitchStats-Store (switch_stats.json).

SWITCH_STATS_FILE = f"{DATA_DIR}/switch_stats.json"

# Harte Untergrenzen/Defaults, falls Config-Keys fehlen
_PROBE_DEFAULT          = 15000000   # normaler Probesize/Analyzeduration
_NOLAT_PROBE_DEFAULT    = 500000     # NoLatency: minimaler Probesize
_SWITCH_MONITOR_DEFAULT = 10.0       # s First-Data-Fenster
_SWITCH_RETRIES_DEFAULT = 2          # zusätzliche ffmpeg-Startversuche
_NOLAT_FAIL_THRESHOLD   = 3          # ab so vielen Fehlern Probesize erhöhen
_PROBE_MAX              = 15000000   # Obergrenze für gelernten Probesize

# ── Analyse (Profil-Empfehlungen aus der Statistik) ───────
# Statt einer festen Fehlerquote wird das Wilson-Score-Konfidenzintervall
# benutzt. Grund: eine rohe Quote ignoriert die Stichprobengröße — 1 Fehler
# aus 2 Versuchen (50%) sieht dramatischer aus als 4 aus 43 (9%), obwohl nur
# Letzteres belastbar ist. Das Intervall trennt Rauschen von echter Evidenz:
#   • Untergrenze hoch  → gesichert zu viele Fehler  → verschärfen
#   • Obergrenze niedrig → gesichert stabil          → lockern
_ANALYZE_MIN_STARTS    = 10          # min. Stream-Starts für Start-Regeln
_ANALYZE_MIN_ZAPS      = 10          # min. Zaps für Zap-Regeln
_ANALYZE_TIGHTEN_LO    = 0.02        # Wilson-Untergrenze, ab der verschärft wird
_ANALYZE_LOOSEN_HI     = 0.10        # Wilson-Obergrenze, unter der gelockert wird
_ANALYZE_Z             = 1.96        # 95%-Konfidenz
_ANALYZE_ZAP_WAIT_STEP = 0.5         # Schrittweite Zap-Wait
_ANALYZE_ZAP_WAIT_MAX  = 3.0         # Obergrenze empfohlener Zap-Wait
_ANALYZE_MIN_SAMPLES   = _ANALYZE_MIN_STARTS   # Rückwärtskompatibler Alias


def _fmt_ps(n):
    """Probesize kompakt und sprachneutral (die UI-Tabelle formatiert lokalisiert,
    darum hier keine Tausenderpunkte, die je nach Sprache anders aussehen)."""
    n = int(n or 0)
    if n >= 1_000_000 and n % 100_000 == 0:
        return f"{n / 1_000_000:g}M"
    if n >= 1_000 and n % 1_000 == 0:
        return f"{n // 1_000}k"
    return str(n)

def wilson_interval(fails, total, z=_ANALYZE_Z):
    """Wilson-Score-Intervall für eine Fehlerquote.

    Liefert (untergrenze, obergrenze). Bei total=0 → (0.0, 1.0), also
    'wir wissen nichts'. Robust auch bei 0 Fehlern und kleinen Stichproben
    (anders als die normale Approximation, die dort 0±0 liefert).
    """
    n = int(total or 0)
    if n <= 0:
        return 0.0, 1.0
    p = float(fails) / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return max(0.0, center - margin), min(1.0, center + margin)

def get_switch_global():
    """Globale Umschalt-Defaults aus der Config (mit Fallbacks)."""
    c = get_config()
    return {
        "no_latency":            bool(c.get("no_latency", False)),
        "zap_wait":              float(c.get("zap_wait_sec", 1.0)),
        "probe_default":         int(c.get("probe_default", _PROBE_DEFAULT)),
        "nolat_probesize":       int(c.get("no_latency_probesize", _NOLAT_PROBE_DEFAULT)),
        "nolat_analyzeduration": int(c.get("no_latency_analyzeduration", _NOLAT_PROBE_DEFAULT)),
        "monitor_sec":           float(c.get("switch_monitor_sec", _SWITCH_MONITOR_DEFAULT)),
        "max_retries":           int(c.get("switch_max_retries", _SWITCH_RETRIES_DEFAULT)),
        "fail_threshold":        int(c.get("nolatency_fail_threshold", _NOLAT_FAIL_THRESHOLD)),
    }


_switch_stats_lock = threading.Lock()
_switch_stats = None  # lazy-loaded dict: {ref: {...}}

def _norm_ref(service_ref):
    return (service_ref or "").rstrip("/")

def _load_switch_stats():
    global _switch_stats
    if _switch_stats is not None:
        return _switch_stats
    try:
        if os.path.exists(SWITCH_STATS_FILE):
            with open(SWITCH_STATS_FILE) as f:
                _switch_stats = json.load(f)
        else:
            _switch_stats = {}
    except Exception as e:
        log.warning(f"Switch-Stats laden fehlgeschlagen: {e}")
        _switch_stats = {}
    return _switch_stats

def _save_switch_stats():
    try:
        os.makedirs(os.path.dirname(SWITCH_STATS_FILE), exist_ok=True)
        tmp = SWITCH_STATS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_switch_stats, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SWITCH_STATS_FILE)
    except Exception as e:
        log.warning(f"Switch-Stats speichern fehlgeschlagen: {e}")

def _switch_entry(ref):
    """Liefert (und legt bei Bedarf an) den Store-Eintrag für einen Sender."""
    stats = _load_switch_stats()
    key = _norm_ref(ref)
    e = stats.get(key)
    if e is None:
        e = {
            "name": None,
            "no_latency": None,          # None = globalen Default nutzen
            "zap_wait": None,            # None = globalen Default nutzen
            "probesize": None,           # None = kein gelernter Override
            "nolatency_fail_streak": 0,
            "nolatency_fail_total": 0,
            "zap": {"ok": 0, "fail": 0, "last_ms": 0, "avg_ms": 0},
            "start": {"ok": 0, "fail": 0, "retries": 0},
            "last_update": None,
        }
        stats[key] = e
    return e

def get_switch_settings(service_ref):
    """Effektive Umschalt-Parameter für einen Sender (global + Per-Sender-Override)."""
    g = get_switch_global()
    with _switch_stats_lock:
        stats = _load_switch_stats()
        e = stats.get(_norm_ref(service_ref)) or {}
        no_latency = e.get("no_latency")
        zap_wait   = e.get("zap_wait")
        learned    = e.get("probesize")
    no_latency = g["no_latency"] if no_latency is None else bool(no_latency)
    zap_wait   = g["zap_wait"]   if zap_wait   is None else float(zap_wait)
    if no_latency:
        probesize = int(learned) if learned else g["nolat_probesize"]
        analyzeduration = g["nolat_analyzeduration"]
        if learned:
            analyzeduration = max(analyzeduration, int(learned))
    else:
        probesize = g["probe_default"]
        analyzeduration = g["probe_default"]
    return {
        "no_latency": no_latency,
        "zap_wait": zap_wait,
        "probesize": int(probesize),
        "analyzeduration": int(analyzeduration),
        "monitor_sec": g["monitor_sec"],
        "max_retries": g["max_retries"],
    }

def record_zap_result(service_ref, ok, elapsed_ms, wait_used, channel_name=None):
    """Zap-Ergebnis (Erfolg/Fehler) + Dauer in der Statistik festhalten."""
    with _switch_stats_lock:
        e = _switch_entry(service_ref)
        if channel_name:
            e["name"] = channel_name
        z = e["zap"]
        if ok:
            z["ok"] = z.get("ok", 0) + 1
        else:
            z["fail"] = z.get("fail", 0) + 1
        z["last_ms"] = int(elapsed_ms)
        n = z["ok"] + z["fail"]
        prev = z.get("avg_ms", 0)
        z["avg_ms"] = int((prev * (n - 1) + elapsed_ms) / n) if n else int(elapsed_ms)
        e["zap_wait_last"] = float(wait_used)
        e["last_update"] = datetime.now().isoformat()
        _save_switch_stats()

def record_stream_start(service_ref, ok, attempts, no_latency, channel_name=None):
    """ffmpeg-Startergebnis erfassen. Bei NoLatency-Fehlern lernt der Store
    den Probesize für diesen Sender in kleinen Schritten hoch."""
    g = get_switch_global()
    with _switch_stats_lock:
        e = _switch_entry(service_ref)
        if channel_name:
            e["name"] = channel_name
        s = e["start"]
        s["retries"] = s.get("retries", 0) + max(0, attempts - 1)
        if ok:
            s["ok"] = s.get("ok", 0) + 1
            e["nolatency_fail_streak"] = 0
        else:
            s["fail"] = s.get("fail", 0) + 1
            if no_latency:
                e["nolatency_fail_streak"] = e.get("nolatency_fail_streak", 0) + 1
                e["nolatency_fail_total"] = e.get("nolatency_fail_total", 0) + 1
                # Schwelle überschritten → Probesize für diesen Sender anheben
                if e["nolatency_fail_streak"] >= g["fail_threshold"]:
                    cur = e.get("probesize") or g["nolat_probesize"]
                    new = min(_PROBE_MAX, int(cur * 2))
                    if new != cur:
                        e["probesize"] = new
                        log.info(f"Switch: Probesize für '{e.get('name') or service_ref[:30]}' "
                                 f"erhöht {cur} → {new} (NoLatency-Fehler)")
                    e["nolatency_fail_streak"] = 0
        e["last_update"] = datetime.now().isoformat()
        _save_switch_stats()

def get_switch_stats_snapshot():
    with _switch_stats_lock:
        return copy.deepcopy(_load_switch_stats())

def set_switch_override(service_ref, no_latency=None, zap_wait=None, probesize=None, channel_name=None):
    """Manuellen Per-Sender-Override setzen. Wert None löscht den Override."""
    with _switch_stats_lock:
        e = _switch_entry(service_ref)
        if channel_name:
            e["name"] = channel_name
        e["no_latency"] = no_latency
        e["zap_wait"] = zap_wait
        if probesize is not None:
            e["probesize"] = int(probesize) if probesize else None
        e["last_update"] = datetime.now().isoformat()
        _save_switch_stats()

def reset_switch_stats(service_ref=None):
    """Setzt gelernte Werte/Statistik zurück (ein Sender oder alle)."""
    global _switch_stats
    with _switch_stats_lock:
        stats = _load_switch_stats()
        if service_ref is None:
            _switch_stats = {}
        else:
            stats.pop(_norm_ref(service_ref), None)
        _save_switch_stats()


def analyze_switch_stats(apply=False, min_samples=None):
    """Leitet aus der gesammelten Statistik pro Sender ein empfohlenes
    Umschalt-Profil ab (no_latency / zap_wait / probesize).

    Bewertet wird nicht die rohe Fehlerquote, sondern das Wilson-Score-
    Konfidenzintervall — dadurch zählt Evidenz statt Zufall:
      • Untergrenze ≥ 2%  → gesichert zu viele Fehler → verschärfen
        (Probesize hoch; am Limit stattdessen NoLatency aus)
      • Obergrenze ≤ 10%  → gesichert stabil → lockern
        (Probesize senken bzw. NoLatency wieder an)
    Zap- und Start-Regeln haben getrennte Mindest-Stichproben, damit eine
    Start-Regel nicht auf Basis von Zap-Zählern feuert.

    Zusätzlich wird die Nutzung (Sessions/Sehdauer) als Exposure ausgewiesen:
    wer viel schaut, sammelt zwangsläufig mehr absolute Fehler — verglichen
    werden deshalb Raten, nicht Summen.

    apply=False liefert nur Empfehlungen (Dry-Run). apply=True setzt die
    empfohlenen Per-Sender-Overrides.
    """
    g = get_switch_global()
    min_starts = int(min_samples) if min_samples else _ANALYZE_MIN_STARTS
    min_zaps   = int(min_samples) if min_samples else _ANALYZE_MIN_ZAPS
    from e2proxy import usage as usage_mod  # lazy: usage depends on switching
    usage = usage_mod.get_usage_snapshot().get("channels", {})
    recs = []
    with _switch_stats_lock:
        stats = _load_switch_stats()
        for ref, e in stats.items():
            z = e.get("zap", {}) or {}
            s = e.get("start", {}) or {}
            z_fail, s_fail = z.get("fail", 0), s.get("fail", 0)
            zt = z.get("ok", 0) + z_fail
            st = s.get("ok", 0) + s_fail

            cur_nl = g["no_latency"] if e.get("no_latency") is None else bool(e.get("no_latency"))
            cur_zw = g["zap_wait"]   if e.get("zap_wait")   is None else float(e.get("zap_wait"))
            cur_ps = e.get("probesize")
            eff_ps = int(cur_ps) if cur_ps else (g["nolat_probesize"] if cur_nl else g["probe_default"])

            s_lo, s_hi = wilson_interval(s_fail, st)
            z_lo, z_hi = wilson_interval(z_fail, zt)
            sfr = (s_fail / st) if st else 0.0
            zfr = (z_fail / zt) if zt else 0.0

            rec_nl, rec_zw, rec_ps = cur_nl, cur_zw, cur_ps
            reasons = []

            # ── NoLatency / Probesize (nur mit genug Stream-Starts) ──
            if st >= min_starts:
                if s_lo >= _ANALYZE_TIGHTEN_LO:
                    if cur_nl and eff_ps < _PROBE_MAX:
                        rec_ps = min(_PROBE_MAX, int(eff_ps * 2))
                        reasons.append(
                            f"{s_fail}/{st} Start-Fehler ({sfr*100:.0f}%, gesichert "
                            f"≥{s_lo*100:.1f}%) → Probesize {_fmt_ps(eff_ps)}→{_fmt_ps(rec_ps)}")
                    elif cur_nl:
                        rec_nl, rec_ps = False, None
                        reasons.append(
                            f"{s_fail}/{st} Start-Fehler ({sfr*100:.0f}%) bei max. "
                            f"Probesize → NoLatency aus")
                    elif rec_zw < _ANALYZE_ZAP_WAIT_MAX:
                        # NoLatency ist bereits aus und der Probesize am Limit —
                        # der einzige verbleibende Hebel ist mehr Settle-Zeit
                        # für den Receiver, bevor ffmpeg zu lesen beginnt.
                        rec_zw = min(_ANALYZE_ZAP_WAIT_MAX,
                                     round(cur_zw + _ANALYZE_ZAP_WAIT_STEP, 1))
                        reasons.append(
                            f"{s_fail}/{st} Start-Fehler ({sfr*100:.0f}%) trotz vollem "
                            f"Probing → Zap-Wait {cur_zw}→{rec_zw}s")
                elif s_hi <= _ANALYZE_LOOSEN_HI:
                    if not cur_nl:
                        rec_nl, rec_ps = True, None
                        reasons.append(
                            f"{st}× Start, {s_fail} Fehler (gesichert ≤{s_hi*100:.1f}%) "
                            f"→ NoLatency an")
                    elif cur_ps and eff_ps > g["nolat_probesize"]:
                        new = max(g["nolat_probesize"], int(eff_ps / 2))
                        rec_ps = None if new <= g["nolat_probesize"] else new
                        reasons.append(
                            f"stabil ({st}× Start, gesichert ≤{s_hi*100:.1f}% Fehler) "
                            f"→ Probesize {_fmt_ps(eff_ps)}→{_fmt_ps(new)}")

            # ── Zap-Wait (nur mit genug Zaps) ────────────────
            if zt >= min_zaps:
                if z_lo >= _ANALYZE_TIGHTEN_LO:
                    new_zw = min(_ANALYZE_ZAP_WAIT_MAX, round(cur_zw + _ANALYZE_ZAP_WAIT_STEP, 1))
                    if new_zw > cur_zw:
                        rec_zw = new_zw
                        reasons.append(
                            f"{z_fail}/{zt} Zap-Fehler ({zfr*100:.0f}%, gesichert "
                            f"≥{z_lo*100:.1f}%) → Zap-Wait {cur_zw}→{new_zw}s")
                elif z_hi <= _ANALYZE_LOOSEN_HI and cur_zw > g["zap_wait"]:
                    rec_zw = max(g["zap_wait"], round(cur_zw - _ANALYZE_ZAP_WAIT_STEP, 1))
                    reasons.append(
                        f"{zt}× Zap, {z_fail} Fehler → Zap-Wait {cur_zw}→{rec_zw}s")

            if rec_nl == cur_nl and rec_zw == cur_zw and (rec_ps or 0) == (cur_ps or 0):
                continue

            if rec_ps is not None:
                ps_apply = int(rec_ps)
            elif cur_ps:
                ps_apply = 0          # gelernten Override löschen
            else:
                ps_apply = None       # keine Änderung
            rec_eff_ps = int(rec_ps) if rec_ps else (g["nolat_probesize"] if rec_nl else g["probe_default"])

            uc = usage.get(_norm_ref(ref), {})
            watch_sec = uc.get("seconds", 0)
            recs.append({
                "ref": ref,
                "name": e.get("name") or ref[:40],
                "zap_total": zt, "start_total": st,
                "samples": max(zt, st),
                "zap_fail_rate": round(zfr, 3),
                "start_fail_rate": round(sfr, 3),
                "start_fail_lo": round(s_lo, 4), "start_fail_hi": round(s_hi, 4),
                "watch_sessions": uc.get("sessions", 0),
                "watch_seconds": watch_sec,
                "watch_human": usage_mod._fmt_duration(watch_sec),
                "fails_per_hour": (round(s_fail / (watch_sec / 3600.0), 2)
                                   if watch_sec >= 600 else None),
                "current":     {"no_latency": cur_nl, "zap_wait": cur_zw, "probesize": eff_ps},
                "recommended": {"no_latency": rec_nl, "zap_wait": rec_zw, "probesize": rec_eff_ps},
                "reasons": reasons,
                "_apply": {"no_latency": rec_nl, "zap_wait": rec_zw, "probesize": ps_apply},
            })

    recs.sort(key=lambda r: r["start_total"] + r["zap_total"], reverse=True)
    applied = 0
    if apply:
        for r in recs:
            a  = r["_apply"]
            nl = None if a["no_latency"] == g["no_latency"] else a["no_latency"]
            zw = None if abs(a["zap_wait"] - g["zap_wait"]) < 1e-6 else a["zap_wait"]
            set_switch_override(r["ref"], no_latency=nl, zap_wait=zw,
                                probesize=a["probesize"], channel_name=r["name"])
            applied += 1
        log.info(f"Switch-Analyse angewendet: {applied} Sender-Profile aktualisiert")

    for r in recs:
        r.pop("_apply", None)
    return {"min_samples": min_starts, "min_starts": min_starts, "min_zaps": min_zaps,
            "count": len(recs), "applied": applied, "recommendations": recs}
