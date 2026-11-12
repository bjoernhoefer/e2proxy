"""Recorder engine: EPG series matching and scheduled recordings.

Ported from e2recorder 1.5. Recordings are delegated to the e2proxy record API
(``/api/record/start|status|stop``) — of this process via loopback and, optionally,
of further e2proxy instances (``remote_proxies``) forming one tuner pool. Going
through the API keeps a single code path for tuner selection and shared tuners.

Recording states: scheduled → starting → recording → done | failed | skipped | missed
"""
import json
import os
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime

from e2proxy.logs import log
from e2proxy.recorder import matching as m
from e2proxy.recorder.store import JsonList

DEFAULTS = {
    "enabled": False,
    "remote_proxies": [],        # extra e2proxy base URLs (multi-host tuner pool)
    "stream_profile": "",        # "" = recording profile configured in e2proxy
    "pre_buffer_sec": 30,
    "post_buffer_sec": 60,
    "max_late_sec": 60,          # give up if no tuner frees up within this window
    "epg_scan_interval": 3600,
    "epg_lookahead_hours": 72,
    "tmdb_language": "de-DE",
}
ACTIVE = ("scheduled", "starting", "recording")
FINAL = ("done", "failed", "skipped", "missed")
HISTORY_DAYS = 30                # failed/skipped/missed entries are pruned after this


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def http_json(url, method="GET", body=None, timeout=10):
    """Returns (data, error). Never raises."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null"), None
    except Exception as e:
        return None, str(e)


class Recorder:
    def __init__(self, data_dir, settings, local_url, api=http_json,
                 sleep=time.sleep, clock=time.time):
        self.settings = settings          # () -> dict (merged with DEFAULTS)
        self.local_url = local_url        # () -> "http://127.0.0.1:<port>"
        self.api = api
        self.sleep = sleep
        self.clock = clock
        self.series = JsonList(os.path.join(data_dir, "series.json"))
        self.recs = JsonList(os.path.join(data_dir, "recordings.json"))
        self.scan_status = {"running": False, "last_run": None, "found": 0, "scheduled": 0}
        self._epg = ([], 0.0)
        self._lock = threading.Lock()

    def cfg(self, key):
        return self.settings().get(key, DEFAULTS.get(key))

    # ── Backends (e2proxy instances) ─────────────────────────────────
    def backends(self):
        remote = [u.rstrip("/") for u in self.cfg("remote_proxies") or [] if u]
        return [self.local_url()] + remote

    def tuner_status(self, url):
        data, err = self.api(f"{url}/api/health", timeout=4)
        if err or not data or not data.get("ok"):
            return None
        rx = data.get("receivers", [])
        return {"free": data.get("tuners_free", 0), "total": len(rx), "receivers": rx}

    def backend_overview(self):
        out = []
        for url in self.backends():
            st = self.tuner_status(url)
            out.append({"url": url, "online": st is not None,
                        "free": (st or {}).get("free", 0), "total": (st or {}).get("total", 0)})
        return out

    def best_backend(self):
        """(url, status) with most free tuners; busy-but-usable beats offline."""
        best = (None, None)
        for url in self.backends():
            st = self.tuner_status(url)
            if not st or st["total"] <= 0:
                continue
            if best[1] is None or st["free"] > best[1]["free"]:
                best = (url, st)
        return best

    def shared_backend(self, rec):
        """Backend already streaming this channel → recording needs no extra tuner."""
        ref = m.norm_ref(rec.get("channel_ref"))
        for r in self.recs.all():
            if (r.get("status") == "recording" and r.get("id") != rec.get("id")
                    and m.norm_ref(r.get("channel_ref")) == ref and r.get("proxy_url")):
                return r["proxy_url"]
        name = (rec.get("channel_name") or "").strip().lower()
        if name:
            for url in self.backends():
                for rx in (self.tuner_status(url) or {}).get("receivers", []):
                    if rx.get("busy") and (rx.get("channel") or "").strip().lower() == name:
                        return url
        return None

    # ── EPG ──────────────────────────────────────────────────────────
    def epg(self, max_age=300):
        chans, ts = self._epg
        if chans and self.clock() - ts < max_age:
            return chans
        for url in self.backends():
            data, err = self.api(f"{url}/api/epg/data", timeout=20)
            if data and data.get("ok") and data.get("channels"):
                self._epg = (data["channels"], self.clock())
                return data["channels"]
            if err:
                log.debug(f"Recorder EPG {url}: {err}")
        return chans

    # ── Series ───────────────────────────────────────────────────────
    def add_series(self, name, channel_ref, channel_name="", regex_pattern="",
                   keep_last=0, kind="series", once=False, once_start_ts=None,
                   year=None, tmdb_poster="", pre_offset_sec=0, post_offset_sec=0):
        name = (name or "").strip()
        if not name or not channel_ref:
            raise ValueError("name und channel_ref erforderlich")
        for s in self.series.all():
            if m.norm_ref(s["channel_ref"]) == m.norm_ref(channel_ref) and (
                    s["name"].lower() == name.lower()
                    or (regex_pattern and s.get("regex_pattern") == regex_pattern)):
                if once and not s.get("once"):
                    return s  # already covered by a recurring series
                if not once:
                    return self.series.update(s["id"], enabled=True, once=False,
                                              once_start_ts=None) or s
        s = {
            "id": uuid.uuid4().hex[:8], "name": name,
            "channel_ref": channel_ref, "channel_name": channel_name,
            "regex_pattern": regex_pattern or "", "keep_last": int(keep_last or 0),
            "enabled": True, "kind": kind if kind in ("series", "movie") else "series",
            "once": bool(once), "once_start_ts": once_start_ts, "year": year,
            "tmdb_poster": tmdb_poster, "pre_offset_sec": int(pre_offset_sec or 0),
            "post_offset_sec": int(post_offset_sec or 0), "created": _now_iso(),
        }
        self.series.upsert(s)
        log.info(f"Recorder: Serie angelegt '{name}' ({channel_name or channel_ref})")
        return s

    def update_series(self, sid, changes):
        allowed = {"name", "regex_pattern", "keep_last", "enabled", "kind",
                   "pre_offset_sec", "post_offset_sec", "year", "tmdb_poster"}
        return self.series.update(sid, **{k: v for k, v in changes.items() if k in allowed})

    def delete_series(self, sid):
        self.recs.remove(lambda r: r.get("serie_id") == sid and r.get("status") == "scheduled")
        return self.series.remove(lambda s: s["id"] == sid) > 0

    # ── Scheduling ───────────────────────────────────────────────────
    def _find_duplicate(self, rec, statuses=ACTIVE + ("done",)):
        key = m.event_key(rec["channel_ref"], rec.get("epg_start_ts") or rec["start_ts"])
        for r in self.recs.all():
            if r.get("id") == rec.get("id") or r.get("status") not in statuses:
                continue
            if r.get("key") == key or m.same_broadcast(rec, r):
                return r
        return None

    def _new_rec(self, serie, ev, channel_name):
        return {
            "id": uuid.uuid4().hex[:8],
            "key": m.event_key(serie["channel_ref"], ev["start"]),
            "serie_id": serie["id"], "serie_name": serie["name"],
            "title": ev.get("title", ""), "subtitle": ev.get("sub", ""),
            "desc": ev.get("desc", ""),
            "channel_ref": serie["channel_ref"],
            "channel_name": channel_name or serie.get("channel_name", ""),
            "start_ts": int(ev["start"]) - int(serie.get("pre_offset_sec", 0)),
            "stop_ts": int(ev["stop"]) + int(serie.get("post_offset_sec", 0)),
            "epg_start_ts": int(ev["start"]), "epg_stop_ts": int(ev["stop"]),
            "status": "scheduled", "protected": False,
            "proxy_url": None, "proxy_rec_id": None, "created_at": _now_iso(),
        }

    def plan(self, serie, ev, channel_name=""):
        """Schedule one EPG event for a series unless it is already planned/recorded."""
        if ev.get("stop", 0) <= self.clock():
            return None
        rec = self._new_rec(serie, ev, channel_name)
        dup = self._find_duplicate(rec)
        if dup:
            return None
        for r in self.recs.all():   # a previously skipped airing stays skipped
            if r.get("key") == rec["key"] and r.get("status") == "skipped":
                return None
        self.recs.upsert(rec)
        log.info(f"Recorder: eingeplant '{rec['title']}' ({rec['channel_name']}) "
                 f"{datetime.fromtimestamp(rec['epg_start_ts']):%d.%m. %H:%M}")
        return rec

    def scan(self):
        """Match enabled series against the EPG and schedule new airings."""
        with self._lock:
            if self.scan_status["running"]:
                return self.scan_status
            self.scan_status["running"] = True
        found = planned = 0
        try:
            chans = self.epg(max_age=0)
            now = self.clock()
            horizon = now + self.cfg("epg_lookahead_hours") * 3600
            for s in self.series.all():
                if not s.get("enabled", True):
                    continue
                ch = next((c for c in chans if m.norm_ref(c.get("id")) == m.norm_ref(s["channel_ref"])), None)
                if not ch:
                    continue
                for ev in ch.get("events", []):
                    if ev.get("stop", 0) <= now or ev.get("start", 0) > horizon:
                        continue
                    if s.get("once_start_ts") and int(ev["start"]) != int(s["once_start_ts"]):
                        continue
                    if not m.title_matches(ev.get("title", ""), s):
                        continue
                    found += 1
                    if self.plan(s, ev, ch.get("name", "")):
                        planned += 1
            self._prune(now)
        except Exception as e:
            log.error(f"Recorder: EPG-Scan Fehler: {e}")
        finally:
            self.scan_status.update(running=False, last_run=_now_iso(),
                                    found=found, scheduled=planned)
        if planned:
            log.info(f"Recorder: Scan {found} Treffer, {planned} neu eingeplant")
        return self.scan_status

    def record_event(self, channel_ref, channel_name, ev, kind="series", recurring=False):
        """EPG browser action: record one airing, or every airing of the title."""
        title = ev.get("title", "")
        s = self.add_series(title, channel_ref, channel_name, kind=kind,
                            once=not recurring,
                            once_start_ts=None if recurring else int(ev["start"]))
        rec = self.plan(s, ev, channel_name)
        if recurring:
            threading.Thread(target=self.scan, daemon=True).start()
        return s, rec

    def skip(self, rid):
        r = self.recs.get(rid)
        if not r or r["status"] != "scheduled":
            return None
        return self.recs.update(rid, status="skipped", skipped_at=_now_iso())

    def unskip(self, rid):
        r = self.recs.get(rid)
        if not r or r["status"] != "skipped" or r["stop_ts"] <= self.clock():
            return None
        return self.recs.update(rid, status="scheduled", error="")

    def delete_rec(self, rid):
        r = self.recs.get(rid)
        if r and r["status"] == "recording" and r.get("proxy_url") and r.get("proxy_rec_id"):
            self.api(f"{r['proxy_url']}/api/record/stop", "POST",
                     {"recording_id": r["proxy_rec_id"]})
        return self.recs.remove(lambda x: x.get("id") == rid) > 0

    def epg_marks(self):
        """{event_key: info} for the EPG grid: planned airings + series matches."""
        marks = {}
        for r in self.recs.all():
            if r.get("status") in ACTIVE + ("skipped", "done"):
                k = m.event_key(r["channel_ref"], r.get("epg_start_ts") or r["start_ts"])
                marks[k] = {"status": r["status"], "rec_id": r["id"],
                            "serie_id": r.get("serie_id"), "serie": r.get("serie_name", "")}
        series = [s for s in self.series.all() if s.get("enabled", True) and not s.get("once")]
        if series:
            for ch in self.epg():
                mine = [s for s in series if m.norm_ref(s["channel_ref"]) == m.norm_ref(ch.get("id"))]
                for ev in ch.get("events", []) if mine else []:
                    k = m.event_key(ch["id"], ev["start"])
                    if k in marks:
                        continue
                    s = next((s for s in mine if m.title_matches(ev.get("title", ""), s)), None)
                    if s:
                        marks[k] = {"status": "match", "serie_id": s["id"], "serie": s["name"]}
        return marks

    def _prune(self, now):
        cutoff = now - HISTORY_DAYS * 86400
        self.recs.remove(lambda r: r.get("status") in ("failed", "skipped", "missed")
                         and r.get("stop_ts", 0) < cutoff)

    # ── Dispatch & recording ─────────────────────────────────────────
    def due(self, now=None):
        """Scheduled recordings whose start (minus pre-buffer) has come."""
        now = self.clock() if now is None else now
        pre = self.cfg("pre_buffer_sec")
        recs = self.recs.all()
        active = [r for r in recs if r.get("status") == "recording"]
        out = []
        for r in recs:
            if r.get("status") != "scheduled":
                continue
            lead = pre
            # back-to-back on the same channel: start 2 min early to absorb drift
            if any(m.norm_ref(a["channel_ref"]) == m.norm_ref(r["channel_ref"])
                   and 0 <= r["start_ts"] - a.get("stop_ts", 0) <= 60 for a in active):
                lead = max(pre, 120)
            if now >= r["start_ts"] - lead:
                if now > r["stop_ts"]:
                    self.recs.update(r["id"], status="missed", error="Sendung bereits vorbei")
                    continue
                out.append(r)
        return out

    def dispatch_once(self):
        for r in self.due():
            if self.recs.update(r["id"], status="starting"):
                threading.Thread(target=self.record, args=(r["id"],), daemon=True,
                                 name=f"rec-{r['id']}").start()

    def _fail(self, rid, status, error):
        log.warning(f"Recorder: {status} — {error}")
        return self.recs.update(rid, status=status, error=error)

    def _acquire_backend(self, rec):
        """Wait for a tuner (or shared stream). Returns url or None (rec finalized)."""
        max_late = self.cfg("max_late_sec")
        while True:
            url = self.shared_backend(rec)
            if url:
                return url
            url, st = self.best_backend()
            if url is None:
                self._fail(rec["id"], "failed", "Kein e2proxy mit Tunern erreichbar")
                return None
            if st["free"] > 0:
                return url
            late = max(0, self.clock() - rec["start_ts"])
            status, _ = self.api(f"{url}/api/record/status", timeout=5)
            running = [x for x in (status or {}).get("recordings", []) if x.get("running")]
            remaining = min((x.get("remaining_sec", 9999) for x in running), default=None)
            decision = m.tuner_decision(late, max_late, bool(running), remaining)
            if decision != "wait":
                busy = ", ".join(f"{x.get('name')}: {x.get('channel') or '?'}"
                                 for x in st["receivers"] if x.get("busy"))
                self._fail(rec["id"], "failed" if decision == "fail" else "skipped",
                           f"Kein freier Tuner ({late:.0f}s nach Start) — {busy}")
                return None
            self.sleep(10)

    def record(self, rid):
        rec = self.recs.get(rid)
        if not rec:
            return
        try:
            if self._find_duplicate(rec, statuses=("recording",)):
                self._fail(rid, "skipped", "Duplikat einer laufenden Aufnahme")
                return
            url = self._acquire_backend(rec)
            if not url:
                return
            post = self.cfg("post_buffer_sec")
            serie = self.series.get(rec.get("serie_id")) or {}
            body = {
                "ref": rec["channel_ref"], "title": rec.get("serie_name") or rec["title"],
                "episode_title": rec.get("subtitle", ""),
                "description": rec.get("subtitle") or rec.get("desc", ""),
                "image_url": serie.get("tmdb_poster", ""),
                "duration": int(rec["stop_ts"] - max(self.clock(), rec["start_ts"]) + post),
                "kind": serie.get("kind", "series"),
            }
            if self.cfg("stream_profile"):
                body["profile"] = self.cfg("stream_profile")
            if serie.get("year"):
                body["year"] = int(serie["year"])
            resp, err = self.api(f"{url}/api/record/start", "POST", body)
            if err or not resp or not resp.get("ok"):
                self._fail(rid, "failed", f"record/start: {err or (resp or {}).get('message')}")
                return
            rec = self.recs.update(rid, status="recording", started_at=_now_iso(), proxy_url=url,
                                   proxy_rec_id=resp["recording_id"], filepath=resp.get("file", ""),
                                   receiver=resp.get("receiver", ""),
                                   shared_tuner=bool(resp.get("shared_tuner")))
            log.info(f"Recorder: Aufnahme läuft '{rec['title']}' via {url} ({rec['receiver']})")
            self._watch(rec, deadline=rec["stop_ts"] + post + 120)
        except Exception as e:
            self._fail(rid, "failed", str(e))
        finally:
            self._after(rid)

    def _watch(self, rec, deadline):
        """Poll the backend until the recording ends; stop it at the deadline."""
        errors = 0
        while True:
            self.sleep(30)
            status, err = self.api(f"{rec['proxy_url']}/api/record/status", timeout=5)
            if err:
                errors += 1
                if errors < 5:
                    continue
                break
            errors = 0
            entry = next((x for x in status.get("recordings", [])
                          if x.get("id") == rec["proxy_rec_id"]), None)
            if not entry or not entry.get("running") or self.clock() > deadline:
                break
        self.api(f"{rec['proxy_url']}/api/record/stop", "POST",
                 {"recording_id": rec["proxy_rec_id"]})
        size = 0
        try:
            size = os.path.getsize(rec.get("filepath") or "")
        except OSError:
            pass
        # Ended well before the broadcast did (ffmpeg/receiver died, backend lost):
        # a partial file must not count as an episode for keep_last.
        if self.clock() < rec["stop_ts"] - 60:
            self.recs.update(rec["id"], status="failed", finished_at=_now_iso(), filesize=size,
                             error="Aufnahme vorzeitig beendet" + (" (Backend nicht erreichbar)" if err else ""))
            log.warning(f"Recorder: '{rec['title']}' vorzeitig beendet ({size // 1048576} MB)")
            return
        self.recs.update(rec["id"], status="done", finished_at=_now_iso(), filesize=size)
        log.info(f"Recorder: fertig '{rec['title']}' ({size // 1048576} MB)")

    def _after(self, rid):
        rec = self.recs.get(rid) or {}
        serie = self.series.get(rec.get("serie_id")) or {}
        if rec.get("status") == "done" and serie.get("keep_last"):
            self.cleanup_keep_last(serie["id"])
        if serie.get("once") and rec.get("status") in ("done", "failed"):
            self.series.update(serie["id"], enabled=False)

    def cleanup_keep_last(self, sid):
        """Delete the oldest unprotected finished recordings beyond keep_last (+ .nfo)."""
        serie = self.series.get(sid)
        keep = int((serie or {}).get("keep_last") or 0)
        if keep <= 0:
            return 0
        done = sorted((r for r in self.recs.all() if r.get("serie_id") == sid
                       and r.get("status") == "done" and not r.get("protected")),
                      key=lambda r: r.get("start_ts", 0))
        victims = done[:-keep]
        for r in victims:
            fp = r.get("filepath") or ""
            for path in (fp, os.path.splitext(fp)[0] + ".nfo") if fp else ():
                try:
                    os.remove(path)
                except OSError:
                    pass
        ids = {r["id"] for r in victims}
        self.recs.remove(lambda r: r["id"] in ids)
        if ids:
            log.info(f"Recorder: keep_last={keep} '{serie['name']}' — {len(ids)} gelöscht")
        return len(ids)

    def recover(self):
        """After a restart: 'starting' → scheduled again, stale entries → missed/done."""
        now = self.clock()
        for r in self.recs.all():
            st = r.get("status")
            if st == "starting":
                st = "scheduled"
                self.recs.update(r["id"], status=st)
            if st == "scheduled" and r["stop_ts"] < now:
                self.recs.update(r["id"], status="missed", error="Service war gestoppt")
            elif st == "recording":
                url, pid = r.get("proxy_url"), r.get("proxy_rec_id")
                status, err = self.api(f"{url}/api/record/status", timeout=5) if url else (None, "?")
                running = (not err) and any(x.get("id") == pid and x.get("running")
                                            for x in (status or {}).get("recordings", []))
                if running:
                    threading.Thread(target=self._watch, daemon=True,
                                     args=(r, r["stop_ts"] + self.cfg("post_buffer_sec") + 120)).start()
                elif r["stop_ts"] < now:
                    self.recs.update(r["id"], status="done", finished_at=_now_iso())
                else:
                    self._fail(r["id"], "failed", "Aufnahme nach Neustart nicht mehr aktiv")

    # ── Background loops ─────────────────────────────────────────────
    def start(self):
        self.recover()
        threading.Thread(target=self._scan_loop, daemon=True, name="recorder-scan").start()
        threading.Thread(target=self._dispatch_loop, daemon=True, name="recorder-dispatch").start()

    def _scan_loop(self):
        self.sleep(20)  # let the EPG cache warm up first
        while True:
            if self.cfg("enabled"):
                self.scan()
            self.sleep(max(300, int(self.cfg("epg_scan_interval"))))

    def _dispatch_loop(self):
        while True:
            try:
                if self.cfg("enabled"):
                    self.dispatch_once()
            except Exception as e:
                log.error(f"Recorder Dispatcher: {e}")
            self.sleep(10)


def tmdb_search(query, api_key, language="de-DE", api=http_json):
    """TMDB lookup (tv + movie) with a regex suggestion covering German titles."""
    if not api_key:
        return {"error": "Kein TMDB API Key konfiguriert", "results": []}
    base = "https://api.themoviedb.org/3"
    q = urllib.parse.quote(query)
    results = []
    for kind in ("tv", "movie"):
        data, _ = api(f"{base}/search/{kind}?api_key={api_key}&query={q}&language={language}", timeout=8)
        for it in (data or {}).get("results", [])[:3]:
            names = {it.get("name") or it.get("title"), it.get("original_name") or it.get("original_title")}
            alt, _ = api(f"{base}/{kind}/{it['id']}/alternative_titles?api_key={api_key}", timeout=6)
            for t in (alt or {}).get("results" if kind == "tv" else "titles", []):
                if t.get("iso_3166_1") in ("DE", "AT", "CH"):
                    names.add(t.get("title") or t.get("name"))
            poster = it.get("poster_path")
            results.append({
                "tmdb_id": it.get("id"), "kind": "series" if kind == "tv" else "movie",
                "name": it.get("name") or it.get("title", ""),
                "year": (it.get("first_air_date") or it.get("release_date") or "")[:4],
                "poster": f"https://image.tmdb.org/t/p/w154{poster}" if poster else "",
                "regex_suggestion": m.build_regex(names),
            })
    return {"results": results}
