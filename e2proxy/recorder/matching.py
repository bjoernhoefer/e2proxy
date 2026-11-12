"""Pure helpers for series matching and broadcast identity (no I/O)."""
import re

_QUOTES = re.compile(r"['\u2018\u2019\u201c\u201d]")


def norm_ref(ref):
    """'1:0:19:EF11:...:' and '1_0_19_EF11_..._' denote the same service."""
    return (ref or "").rstrip("/").rstrip(":").rstrip("_").replace(":", "_")


def event_key(ref, start_ts):
    return f"{norm_ref(ref)}@{int(start_ts)}"


def compile_pattern(serie):
    """Series regex (case-insensitive); falls back to the escaped name if invalid."""
    name = serie.get("name", "")
    for pat in (serie.get("regex_pattern") or re.escape(name), re.escape(name)):
        try:
            return re.compile(pat, re.IGNORECASE)
        except re.error:
            continue
    return None


def title_matches(title, serie):
    rx = compile_pattern(serie)
    return bool(rx and title and rx.search(title))


def build_regex(titles):
    """Alternation of distinct titles; quotes match any char, whitespace is flexible."""
    parts, seen = [], set()
    for t in sorted({t for t in titles if t}, key=len, reverse=True):
        p = re.sub(r"\s+", r"\\s+", _QUOTES.sub(".", t.strip()))
        if p.lower() not in seen:
            seen.add(p.lower())
            parts.append(p)
    return "|".join(parts)


def broadcast_window(rec):
    """EPG times (without series offsets) so back-to-back episodes never overlap."""
    return (rec.get("epg_start_ts") or rec.get("start_ts", 0),
            rec.get("epg_stop_ts") or rec.get("stop_ts", 0))


def same_broadcast(a, b, min_overlap=60):
    """Same channel (+series) and >min_overlap s overlap = same airing (shifted EPG)."""
    if norm_ref(a.get("channel_ref")) != norm_ref(b.get("channel_ref")):
        return False
    if a.get("serie_id") and b.get("serie_id") and a["serie_id"] != b["serie_id"]:
        return False
    a0, a1 = broadcast_window(a)
    b0, b1 = broadcast_window(b)
    if not (a1 and b1):
        return False
    return min(a1, b1) - max(a0, b0) > min_overlap


def tuner_decision(late_sec, max_late_sec, busy_by_recorder, remaining_sec):
    """No free tuner: 'wait', 'skip' (own recording ends too late) or 'fail' (too late)."""
    if late_sec > max_late_sec:
        return "fail"
    if busy_by_recorder and remaining_sec is not None \
            and remaining_sec + 5 > max_late_sec - late_sec:
        return "skip"
    return "wait"
