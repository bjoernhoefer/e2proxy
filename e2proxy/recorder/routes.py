"""Recorder HTTP API (registered via e2proxy.extensions)."""
import threading

from e2proxy.config import get_config
from e2proxy.extensions import route
import e2proxy.recorder as recorder
from e2proxy.recorder.engine import tmdb_search

EXT = "recorder"  # routes answer 404 while the recorder is disabled


def _id(req, prefix):
    return req.path[len(prefix):].strip("/")


# Settings + status stay reachable when disabled (needed to enable it).
@route("GET", "/api/recorder/settings")
def get_settings(req):
    return recorder.settings()


@route("POST", "/api/recorder/settings")
def post_settings(req):
    try:
        return {"ok": True, "settings": recorder.save_settings(req.data)}
    except (TypeError, ValueError) as e:
        return {"ok": False, "error": str(e)}, 400


@route("GET", "/api/recorder/status")
def status(req):
    if not recorder.is_enabled():
        return {"ok": True, "enabled": False}
    r = recorder.get()
    counts = {}
    for x in r.recs.all():
        counts[x["status"]] = counts.get(x["status"], 0) + 1
    return {"ok": True, "enabled": True, "scan": r.scan_status, "recordings": counts,
            "series": len(r.series.all()), "backends": r.backend_overview()}


@route("GET", "/api/recorder/series", EXT)
def list_series(req):
    return recorder.get().series.all()


@route("POST", "/api/recorder/series", EXT)
def add_series(req):
    d = req.data
    try:
        s = recorder.get().add_series(
            d.get("name"), d.get("channel_ref"), d.get("channel_name", ""),
            regex_pattern=d.get("regex_pattern", ""), keep_last=d.get("keep_last", 0),
            kind=d.get("kind", "series"), year=d.get("year"),
            tmdb_poster=d.get("tmdb_poster", ""),
            pre_offset_sec=d.get("pre_offset_sec", 0), post_offset_sec=d.get("post_offset_sec", 0))
    except ValueError as e:
        return {"ok": False, "error": str(e)}, 400
    threading.Thread(target=recorder.get().scan, daemon=True).start()
    return {"ok": True, "serie": s}


@route("PUT", "/api/recorder/series/", EXT)
def update_series(req):
    s = recorder.get().update_series(_id(req, "/api/recorder/series/"), req.data)
    return ({"ok": True, "serie": s}, 200) if s else ({"ok": False, "error": "not found"}, 404)


@route("DELETE", "/api/recorder/series/", EXT)
def delete_series(req):
    return {"ok": recorder.get().delete_series(_id(req, "/api/recorder/series/"))}


@route("GET", "/api/recorder/recordings", EXT)
def list_recordings(req):
    return sorted(recorder.get().recs.all(), key=lambda r: r.get("start_ts", 0))


@route("DELETE", "/api/recorder/recordings/", EXT)
def delete_recording(req):
    return {"ok": recorder.get().delete_rec(_id(req, "/api/recorder/recordings/"))}


@route("POST", "/api/recorder/event", EXT)
def record_event(req):
    d = req.data
    try:
        ev = {"start": int(d["start"]), "stop": int(d["stop"]), "title": d["title"],
              "sub": d.get("sub", ""), "desc": d.get("desc", "")}
        s, rec = recorder.get().record_event(
            d["channel_ref"], d.get("channel_name", ""), ev,
            kind=d.get("kind", "series"), recurring=bool(d.get("recurring")))
    except (KeyError, ValueError, TypeError) as e:
        return {"ok": False, "error": f"Ungültige Anfrage: {e}"}, 400
    return {"ok": True, "serie": s, "recording": rec}


@route("POST", "/api/recorder/skip", EXT)
def skip(req):
    r = recorder.get().skip(req.data.get("id", ""))
    return {"ok": bool(r), "recording": r}


@route("POST", "/api/recorder/unskip", EXT)
def unskip(req):
    r = recorder.get().unskip(req.data.get("id", ""))
    return {"ok": bool(r), "recording": r}


@route("POST", "/api/recorder/protect", EXT)
def protect(req):
    r = recorder.get().recs.update(req.data.get("id", ""), protected=bool(req.data.get("protected", True)))
    return {"ok": bool(r), "recording": r}


@route("POST", "/api/recorder/scan", EXT)
def scan(req):
    threading.Thread(target=recorder.get().scan, daemon=True).start()
    return {"ok": True}


@route("GET", "/api/recorder/epg-marks", EXT)
def epg_marks(req):
    return {"ok": True, "marks": recorder.get().epg_marks()}


@route("GET", "/api/recorder/tmdb", EXT)
def tmdb(req):
    q = req.arg("q").strip()
    if not q:
        return {"error": "q fehlt", "results": []}, 400
    return tmdb_search(q, get_config().get("tmdb_api_key", ""), recorder.settings()["tmdb_language"])
