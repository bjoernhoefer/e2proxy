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


def test_stream_requests_ask_for_stream_receiver(server, monkeypatch):
    """v5.2: live streams allocate with purpose="stream" (Settings → Receivers → Stream)."""
    import e2proxy.http_proxy as hp
    seen = []
    monkeypatch.setattr(hp, "get_free_receiver", lambda *a, **k: seen.append(k.get("purpose")))
    status, _ = http(server + "/prezap?ref=1:0:1:1:1:1:1:0:0:0:")
    assert status == 200 and seen == ["stream"]


def test_config_roundtrip_keeps_receiver_roles(server):
    _, body = http(server + "/api/config")
    cfg = json.loads(body)
    cfg["receivers"][0].update(stream=True, record=True)
    assert json.loads(http(server + "/api/config", "POST", cfg)[1])["ok"]
    try:
        rx = json.loads(http(server + "/api/config")[1])["receivers"][0]
        assert rx["stream"] is True and rx["record"] is True
        _, page = http(server + "/settings")
        assert '<td data-rx-role="stream"><span class="tag green">stream</span></td>' in page
        assert '<td data-rx-role="record"><span class="tag red">record</span></td>' in page
    finally:
        cfg["receivers"][0].update(stream=False, record=False)
        http(server + "/api/config", "POST", cfg)


def test_config_post_drops_legacy_default_flag(server):
    """v5.3: only Stream/Record exist; a posted "default" becomes the free roles."""
    cfg = json.loads(http(server + "/api/config")[1])
    cfg["receivers"][0].update(default=True, stream=False, record=False)
    assert json.loads(http(server + "/api/config", "POST", cfg)[1])["ok"]
    try:
        rx = json.loads(http(server + "/api/config")[1])["receivers"][0]
        assert "default" not in rx and rx["stream"] is True and rx["record"] is True
        assert "<th>Default</th>" not in http(server + "/settings")[1]
    finally:
        cfg["receivers"][0].update(stream=False, record=False)
        cfg["receivers"][0].pop("default", None)
        http(server + "/api/config", "POST", cfg)
