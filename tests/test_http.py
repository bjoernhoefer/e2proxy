"""HTTP smoke tests against the real request handler."""
import json

import pytest

from conftest import http


@pytest.mark.parametrize("path", ["/", "/help", "/favorites", "/epg-browser", "/settings"])
def test_pages_render(server, path):
    status, body = http(server + path)
    assert status == 200 and "<html" in body.lower()


@pytest.mark.parametrize("path", ["/api/version", "/api/config", "/discover.json",
                                  "/lineup.json", "/api/record/status", "/api/interfaces"])
def test_api_endpoints_answer(server, path):
    status, _ = http(server + path)
    assert status == 200


def test_health_is_fast_and_reports_tuners(server):
    """Health must not probe receivers (dead box once failed the container healthcheck)."""
    import time
    t0 = time.time()
    status, body = http(server + "/api/health", timeout=3)
    d = json.loads(body)
    assert status == 200 and d["ok"] and time.time() - t0 < 1.5
    assert {"receivers", "tuners_free", "uptime_sec"} <= d.keys()


def test_unknown_routes_404(server):
    assert http(server + "/api/does-not-exist", "POST", {})[0] == 404
    assert http(server + "/api/does-not-exist", "PUT", {})[0] == 404


def test_recorder_api_toggle(server, monkeypatch):
    """Recorder API exists only while enabled; settings stay reachable to enable it."""
    monkeypatch.setattr("e2proxy.extensions.start_all", lambda: None)  # no background loops
    assert http(server + "/api/recorder/series")[0] == 404
    status, body = http(server + "/api/recorder/settings", "POST", {"enabled": True, "pre_buffer_sec": "45"})
    assert status == 200 and json.loads(body)["settings"]["pre_buffer_sec"] == 45
    try:
        assert http(server + "/api/recorder/series")[0] == 200
        status, body = http(server + "/api/recorder/series", "POST", {"name": "", "channel_ref": ""})
        assert status == 400
    finally:
        http(server + "/api/recorder/settings", "POST", {"enabled": False})
    assert http(server + "/api/recorder/series")[0] == 404


def test_recording_stream_not_counted_in_usage(server, monkeypatch):
    """v5.1: the recorder's own ffmpeg (/stream?preacquired=…) showed up as a
    'ffmpeg/Lavf' device in the usage report – only real viewers count."""
    from e2proxy import http_proxy, receivers
    booked = []
    monkeypatch.setattr(http_proxy, "record_usage_session", lambda *a, **k: booked.append(a))
    monkeypatch.setattr(http_proxy, "do_zap", lambda *a, **k: False)
    monkeypatch.setattr(http_proxy, "get_device_profile", lambda name: None)
    monkeypatch.setattr(http_proxy, "get_transcode_profile", lambda name: {"codec": "pass"})
    epoch = receivers.acquire_receiver("rx1", "127.0.0.1", service_ref="1:0:1:A:")
    try:
        status, _ = http(server + "/stream?ref=1:0:1:A:&profile=pass&preacquired=rx1")
        assert status == 502 and booked == []
    finally:
        receivers.release_receiver("rx1", epoch)
