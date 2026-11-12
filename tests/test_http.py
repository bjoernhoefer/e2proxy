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
