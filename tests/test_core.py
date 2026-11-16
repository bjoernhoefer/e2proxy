"""Unit tests for core helpers + regressions documented in the changelog."""
from e2proxy import receivers
from e2proxy.openwebif import _owif_fmt_length
from e2proxy.probe import PROBE_UA, is_probe_request
from e2proxy.switching import wilson_interval


def test_owif_length_is_minutes_seconds():
    """v4.3: Dream Player expects MINUTES:SS, not raw seconds or H:MM:SS."""
    assert _owif_fmt_length(5400) == "90:00"
    assert _owif_fmt_length(61) == "1:01"
    assert _owif_fmt_length(0) == "" and _owif_fmt_length(None) == "" and _owif_fmt_length("x") == ""


def test_wilson_interval_bounds():
    assert wilson_interval(0, 0) == (0.0, 1.0)
    lo, hi = wilson_interval(0, 10)
    assert lo == 0.0 and 0 < hi < 0.35          # zero fails ≠ certainty
    lo, hi = wilson_interval(5, 10)
    assert lo < 0.5 < hi


def test_probe_requests_are_recognised():
    """v4.9: probe traffic must not pollute usage stats / access log."""
    assert is_probe_request({"User-Agent": PROBE_UA + "/1"})
    assert not is_probe_request({"User-Agent": "Plex"})
    assert not is_probe_request(None)


def test_shared_tuner_released_only_by_last_user():
    """v4.6: the first ending stream must not free a tuner another viewer uses."""
    receivers._init_receiver_state()
    epoch = receivers.acquire_receiver("rx1", "10.0.0.1", service_ref="1:0:1:A:")
    assert receivers.attach_receiver("rx1", "1:0:1:A:") == epoch
    assert receivers.attach_receiver("rx1", "1:0:1:B:") is None   # other channel
    receivers.release_receiver("rx1", epoch)
    assert receivers._receiver_state["rx1"] is not None             # still watched
    receivers.release_receiver("rx1", epoch)
    assert receivers._receiver_state["rx1"] is None


def test_stale_release_does_not_kill_new_session():
    """A late cleanup thread (old epoch) must not release a fresh session."""
    receivers._init_receiver_state()
    old = receivers.acquire_receiver("rx1", "10.0.0.1")
    receivers.release_receiver("rx1", force=True)                    # /kill
    new = receivers.acquire_receiver("rx1", "10.0.0.2")
    receivers.release_receiver("rx1", old)
    assert receivers._receiver_state["rx1"]["epoch"] == new
    receivers.release_receiver("rx1", new)


def _fake_recording_env(monkeypatch, tmp_path, free="rx1"):
    import io
    import time
    from e2proxy import recordings

    class _Proc:
        pid = 1
        stderr = io.StringIO("")

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    rx = {"id": "rx1", "name": "rx1", "ip": "127.0.0.9"}
    monkeypatch.setattr(recordings, "get_receivers", lambda: [rx])
    monkeypatch.setattr(recordings, "is_receiver_usable", lambda r: True)
    monkeypatch.setattr(recordings, "get_receiver_by_id", lambda rid: rx)
    monkeypatch.setattr(recordings, "get_free_receiver", lambda *a, **k: free)
    monkeypatch.setattr(recordings, "classify_recording", lambda *a, **k: {"kind": "movie"})
    monkeypatch.setattr(recordings, "verify_recording_metadata", lambda *a, **k: None)
    monkeypatch.setattr(recordings, "build_recording_path",
                        lambda *a, **k: str(tmp_path / "rec.ts"))
    monkeypatch.setattr(recordings, "build_nfo", lambda *a, **k: None)
    monkeypatch.setattr(recordings, "get_recordings_config",
                        lambda: {"path": str(tmp_path), "profile": "pass", "max_duration": 60})
    monkeypatch.setattr(recordings._subprocess, "Popen", lambda *a, **k: _Proc())
    import threading
    released = threading.Event()
    orig_release = recordings.release_receiver

    def release_and_signal(*a, **k):
        try:
            return orig_release(*a, **k)
        finally:
            released.set()
    monkeypatch.setattr(recordings, "release_receiver", release_and_signal)

    def run_and_wait(ref):
        rec_id = recordings.start_recording(ref, "Das perfekte Dinner")[0]
        deadline = time.time() + 5
        while rec_id in recordings._active_recordings and time.time() < deadline:
            time.sleep(0.02)
        assert rec_id not in recordings._active_recordings
        # the tuner is released right after the entry is popped – wait for it
        assert released.wait(5)
    return run_and_wait


def test_recording_end_keeps_live_viewer_on_shared_tuner(monkeypatch, tmp_path):
    """v5.x: the end of the last recording on a shared tuner force-killed the
    receiver – the Dream Player watching the same channel lost its stream."""
    receivers._init_receiver_state()
    viewer = receivers.acquire_receiver("rx1", "10.0.0.5", service_ref="1:0:1:A:")
    _fake_recording_env(monkeypatch, tmp_path, free=None)("1:0:1:A:")
    st = receivers._receiver_state["rx1"]
    assert st is not None and st["epoch"] == viewer and st["users"] == 1
    receivers.release_receiver("rx1", viewer)
    assert receivers._receiver_state["rx1"] is None


def test_recording_on_own_tuner_is_released_at_end(monkeypatch, tmp_path):
    receivers._init_receiver_state()
    receivers._receiver_state["rx1"] = None
    _fake_recording_env(monkeypatch, tmp_path)("1:0:1:B:")
    assert receivers._receiver_state["rx1"] is None


def test_failed_epg_run_keeps_previous_epg(monkeypatch):
    """v5.0: a run with the receiver off (e.g. at start-up) replaced a valid EPG
    cache with an empty XMLTV – the guide stayed blank until the next good run."""
    from e2proxy import epg
    old = '<tv><programme start="x" channel="c"><title>Tatort</title></programme></tv>'
    monkeypatch.setitem(epg.epg_cache, "xml", old)
    monkeypatch.setattr(epg, "load_favorites", lambda: [{"ref": "1:0:1:A:", "name": "A"}])
    monkeypatch.setattr(epg, "get_receivers", lambda: [{"id": "rx1", "name": "rx1"}])
    monkeypatch.setattr(epg, "is_receiver_usable", lambda r: True)
    monkeypatch.setattr(epg, "is_receiver_unhealthy", lambda rid: False)
    monkeypatch.setattr(epg, "fetch_epg_events_from_receiver", lambda rx, refs: {})
    monkeypatch.setattr(epg, "zap_and_fetch_epg", lambda rx, ref, wait_sec=30: [])
    monkeypatch.setattr(epg, "fetch_rytec_epg", lambda: {})
    saved = []
    monkeypatch.setattr(epg, "save_epg_to_disk", saved.append)
    assert epg.run_epg_update("startup") is False
    assert epg.epg_cache["xml"] == old and saved == []


def test_free_receiver_prefers_stream_and_record_roles(monkeypatch):
    """v5.2/5.3: only Stream / Record steer the choice; busy/locked → config
    order. A leftover ``default`` flag is ignored (v5.3: no third setting)."""
    rxs = [{"id": "a"}, {"id": "b", "stream": True},
           {"id": "c", "record": True}, {"id": "z", "default": True}]
    monkeypatch.setattr(receivers, "get_receivers", lambda: rxs)
    monkeypatch.setattr(receivers, "_receiver_state", {"a": None, "b": None, "c": None, "z": None})
    monkeypatch.setattr(receivers, "is_receiver_unhealthy", lambda rid: False)
    assert receivers.get_free_receiver() == "a"
    assert receivers.get_free_receiver(purpose="stream") == "b"
    assert receivers.get_free_receiver(purpose="record") == "c"
    assert receivers.get_free_receiver("c", purpose="stream") == "c"   # explicit pref first
    receivers._receiver_state["b"] = {"epoch": 1}                        # stream box busy
    assert receivers.get_free_receiver(purpose="stream") == "a"
    rxs[2]["locked"] = True                                              # record box locked
    assert receivers.get_free_receiver(purpose="record") == "a"


def test_recording_allocates_record_receiver(monkeypatch, tmp_path):
    from e2proxy import recordings
    seen = []

    def fake_free(*a, **k):
        seen.append(k.get("purpose"))
        return None
    monkeypatch.setattr(recordings, "get_receivers", lambda: [])
    monkeypatch.setattr(recordings, "get_free_receiver", fake_free)
    monkeypatch.setattr(recordings, "get_recordings_config",
                        lambda: {"path": str(tmp_path), "profile": "pass", "max_duration": 60})
    try:
        recordings.start_recording("1:0:1:1:1:1:1:0:0:0:", "X")
    except RuntimeError:
        pass
    assert seen == ["record"]


def test_legacy_default_flag_migrates_to_stream_and_record():
    """v5.3: the old "default" flag becomes the free roles and is removed."""
    from e2proxy.config import migrate_receiver_roles
    cfg = {"receivers": [{"id": "a"}, {"id": "b", "default": True}]}
    assert migrate_receiver_roles(cfg)
    assert cfg["receivers"] == [{"id": "a"}, {"id": "b", "stream": True, "record": True}]
    cfg = {"receivers": [{"id": "a", "record": True, "default": False},
                         {"id": "b", "default": True}]}
    assert migrate_receiver_roles(cfg)                     # record already taken → only stream
    assert cfg["receivers"] == [{"id": "a", "record": True}, {"id": "b", "stream": True}]
    assert not migrate_receiver_roles(cfg)                 # idempotent
    assert not migrate_receiver_roles({})


def test_owif_metadata_receiver_follows_stream_role(monkeypatch):
    """v5.3: without a metadata receiver, OpenWebif metadata comes from the Stream
    receiver (not from a removed "default" flag)."""
    from e2proxy import openwebif
    rxs = [{"id": "a", "default": True}, {"id": "b", "stream": True}]
    monkeypatch.setattr(openwebif, "get_receivers", lambda: rxs)
    monkeypatch.setattr(openwebif, "get_owif_config", lambda: {})
    monkeypatch.setattr(openwebif, "is_receiver_unhealthy", lambda rid: False)
    assert openwebif._owif_metadata_receiver()["id"] == "b"


def test_instantly_dead_recording_is_not_left_running(monkeypatch, tmp_path):
    """v5.3: the watchdog started before the recording was registered. If ffmpeg
    died at once, cleanup ran first and the recording stayed "running" forever
    with its tuner held. Threads run synchronously here to force that order."""
    from e2proxy import recordings

    class SyncThread:
        def __init__(self, target, args=(), kwargs=None, daemon=None):
            self.run = lambda: target(*args, **(kwargs or {}))

        def start(self):
            self.run()
    receivers._init_receiver_state()
    run = _fake_recording_env(monkeypatch, tmp_path)
    monkeypatch.setattr(recordings.threading, "Thread", SyncThread)
    run("1:0:1:C:")
    assert receivers._receiver_state["rx1"] is None
