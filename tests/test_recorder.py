"""Recorder engine + matching (ported e2recorder behaviour, incl. its bug fixes)."""
import json

import pytest

from e2proxy.recorder import matching as m
from e2proxy.recorder.engine import Recorder
from e2proxy.recorder.store import JsonList

REF = "1:0:19:EF11:421:1:C00000:0:0:0:"
CID = "1_0_19_EF11_421_1_C00000_0_0_0_"
T0 = 1_800_000_000


# ── matching ─────────────────────────────────────────────────────────
def test_ref_formats_are_equivalent():
    assert m.norm_ref(REF) == m.norm_ref(CID) == m.norm_ref(REF + "/")
    assert m.event_key(REF, 5.9) == m.event_key(CID, 5)


def test_invalid_regex_falls_back_to_name():
    s = {"name": "C++ (Doku)", "regex_pattern": "C++ (Doku"}
    assert m.title_matches("Heute: C++ (Doku)", s)
    assert not m.title_matches("", s)


def test_build_regex_flexible_quotes_and_spaces():
    rx = m.build_regex(["Grey's Anatomy", "Grey’s  Anatomy", ""])
    assert rx.count("|") == 0
    assert m.title_matches("GREY'S   anatomy", {"name": "x", "regex_pattern": rx})


def test_back_to_back_is_not_duplicate_but_shifted_start_is():
    a = {"channel_ref": REF, "serie_id": "s", "start_ts": 0, "stop_ts": 3000,
         "epg_start_ts": 100, "epg_stop_ts": 2800}            # offsets must be ignored
    nxt = {"channel_ref": CID, "serie_id": "s", "start_ts": 2800, "stop_ts": 5600}
    shifted = {"channel_ref": CID, "serie_id": "s", "start_ts": 400, "stop_ts": 3100}
    assert not m.same_broadcast(a, nxt)
    assert m.same_broadcast(a, shifted)
    assert not m.same_broadcast(a, dict(shifted, serie_id="other"))


@pytest.mark.parametrize("late,busy,remaining,expected", [
    (0, False, None, "wait"),
    (61, False, None, "fail"),
    (10, True, 20, "wait"),
    (10, True, 50, "skip"),     # own recording ends after the tolerance window
])
def test_tuner_decision(late, busy, remaining, expected):
    assert m.tuner_decision(late, 60, busy, remaining) == expected


# ── store ────────────────────────────────────────────────────────────
def test_store_survives_corrupt_file(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{not json")
    st = JsonList(str(p))
    assert st.all() == []
    st.upsert({"id": "a"})
    assert json.loads(p.read_text()) == [{"id": "a"}]
    assert (tmp_path / "x.json.corrupt").exists()


# ── engine with a fake e2proxy backend ───────────────────────────────
class FakeProxy:
    def __init__(self):
        self.free, self.total = 1, 2
        self.events = []
        self.started, self.stopped = [], []
        self.running = {}

    def __call__(self, url, method="GET", body=None, timeout=10):
        path = url.split("8888", 1)[1]
        if path == "/api/health":
            return {"ok": True, "tuners_free": self.free,
                    "receivers": [{"name": f"rx{i}", "busy": i >= self.free, "channel": ""}
                                  for i in range(self.total)]}, None
        if path == "/api/epg/data":
            return {"ok": True, "channels": [{"id": CID, "name": "Das Erste", "events": self.events}]}, None
        if path == "/api/record/start":
            rid = f"p{len(self.started)}"
            self.started.append(body)
            self.running[rid] = True
            return {"ok": True, "recording_id": rid, "file": "/rec/x.ts", "receiver": "rx0"}, None
        if path == "/api/record/status":
            return {"recordings": [{"id": k, "running": v, "remaining_sec": 100}
                                   for k, v in self.running.items()]}, None
        if path == "/api/record/stop":
            self.stopped.append(body["recording_id"])
            self.running[body["recording_id"]] = False
            return {"ok": True}, None
        return None, "404"


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


@pytest.fixture
def env(tmp_path):
    proxy, clock = FakeProxy(), Clock(T0)
    settings = {"enabled": True}
    rec = Recorder(str(tmp_path), lambda: settings, lambda: "http://127.0.0.1:8888",
                   api=proxy, sleep=clock.sleep, clock=clock)
    return rec, proxy, clock, settings


def ev(start, title="Tatort", dur=5400):
    return {"start": start, "stop": start + dur, "title": title, "sub": "", "desc": ""}


def test_scan_plans_matches_once_and_dedupes(env):
    rec, proxy, clock, _ = env
    rec.add_series("Tatort", REF, "Das Erste")
    proxy.events = [ev(T0 + 3600), ev(T0 + 9000, "Tagesschau"), ev(T0 + 400 * 3600)]
    assert rec.scan()["scheduled"] == 1                  # 3rd is beyond lookahead
    proxy.events.append(dict(ev(T0 + 3600 + 300)))       # EPG shifted start by 5 min
    rec._epg = ([], 0)
    assert rec.scan()["scheduled"] == 0
    assert len(rec.recs.all()) == 1


def test_skipped_airing_stays_skipped_after_rescan(env):
    rec, proxy, _, _ = env
    rec.add_series("Tatort", REF)
    proxy.events = [ev(T0 + 3600)]
    rec.scan()
    rid = rec.recs.all()[0]["id"]
    assert rec.skip(rid)["status"] == "skipped"
    rec.scan()
    assert [r["status"] for r in rec.recs.all()] == ["skipped"]
    assert rec.unskip(rid)["status"] == "scheduled"


def test_once_series_records_only_its_airing(env):
    rec, proxy, _, _ = env
    proxy.events = [ev(T0 + 3600), ev(T0 + 90000)]
    s, r = rec.record_event(REF, "Das Erste", ev(T0 + 3600))
    assert s["once"] and r["status"] == "scheduled"
    rec.scan()
    assert len(rec.recs.all()) == 1


def test_due_handles_lead_time_back_to_back_and_missed(env):
    rec, _, clock, _ = env
    s = rec.add_series("A", REF)
    a = rec.plan(s, ev(T0 + 100, "A", 600))
    b = rec.plan(s, ev(T0 + 1000, "A", 600))
    assert rec.due() == []
    clock.t = T0 + 100 - 30
    assert [r["id"] for r in rec.due()] == [a["id"]]
    rec.recs.update(a["id"], status="recording", stop_ts=T0 + 960)
    clock.t = T0 + 1000 - 100                            # 2-min lead for back-to-back
    assert [r["id"] for r in rec.due()] == [b["id"]]
    clock.t = T0 + 5000
    assert rec.due() == [] and rec.recs.get(b["id"])["status"] == "missed"


def test_record_happy_path(env):
    rec, proxy, clock, _ = env
    s = rec.add_series("Tatort", REF, kind="series")
    r = rec.plan(s, ev(T0 + 60, dur=120))
    clock.t = T0 + 60
    orig_sleep = clock.sleep

    def sleep(sec):                      # recording ends on the backend after a while
        orig_sleep(sec)
        if clock.t > T0 + 200:
            proxy.running["p0"] = False
    rec.sleep = sleep
    rec.record(r["id"])
    done = rec.recs.get(r["id"])
    assert done["status"] == "done" and done["proxy_rec_id"] == "p0"
    assert proxy.started[0]["ref"] == REF and proxy.started[0]["duration"] == 180
    assert proxy.stopped == ["p0"]       # explicit stop frees the tuner


def test_record_fails_without_tuner(env):
    rec, proxy, clock, _ = env
    proxy.free = 0
    s = rec.add_series("Tatort", REF)
    r = rec.plan(s, ev(T0 + 60))
    clock.t = T0 + 60
    rec.record(r["id"])
    out = rec.recs.get(r["id"])
    assert out["status"] in ("failed", "skipped") and "Tuner" in out["error"]
    assert proxy.started == []


def test_keep_last_deletes_oldest_unprotected(env, tmp_path):
    rec, _, _, _ = env
    s = rec.add_series("A", REF, keep_last=1)
    files = []
    for i in range(3):
        f = tmp_path / f"e{i}.ts"
        f.write_text("x")
        (tmp_path / f"e{i}.nfo").write_text("x")
        files.append(f)
        rec.recs.upsert({"id": f"r{i}", "serie_id": s["id"], "status": "done", "start_ts": i,
                         "filepath": str(f), "protected": i == 0, "channel_ref": REF})
    assert rec.cleanup_keep_last(s["id"]) == 1
    assert files[0].exists() and not files[1].exists() and files[2].exists()
    assert not (tmp_path / "e1.nfo").exists()


def test_recover_after_restart(env):
    rec, proxy, clock, _ = env
    s = rec.add_series("A", REF)
    past = rec.plan(s, ev(T0 + 10, "A", 60))
    fut = rec.plan(s, ev(T0 + 9000, "A", 60))
    rec.recs.update(fut["id"], status="starting")
    rec.recs.upsert({"id": "gone", "status": "recording", "channel_ref": REF, "start_ts": T0,
                     "stop_ts": T0 + 50, "proxy_url": "http://127.0.0.1:8888", "proxy_rec_id": "nope"})
    clock.t = T0 + 500
    rec.recover()
    assert rec.recs.get(past["id"])["status"] == "missed"
    assert rec.recs.get(fut["id"])["status"] == "scheduled"
    assert rec.recs.get("gone")["status"] == "done"

