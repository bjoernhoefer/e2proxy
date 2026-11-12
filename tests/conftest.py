"""Test isolation: every test session gets its own throw-away DATA_DIR.

DATA_DIR is resolved at import time (e2proxy.paths), so the env var must be
set before anything from e2proxy is imported.
"""
import json
import os
import socket
import sys
import tempfile
import threading
import urllib.request

import pytest

_DATA = tempfile.mkdtemp(prefix="e2proxy-test-")
os.environ["E2PROXY_DATA_DIR"] = _DATA
with open(os.path.join(_DATA, "config.json"), "w") as f:
    json.dump({"receivers": [{"id": "rx1", "name": "Box 1", "ip": "127.0.0.1", "host": "127.0.0.1",
                              "port": 1, "stream_port": 2, "enabled": True}],
               "proxy_host": "127.0.0.1"}, f)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session")
def data_dir():
    return _DATA


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def server():
    """Real ProxyHandler on an ephemeral port (no background loops, no receivers)."""
    import http.server
    from e2proxy.config import load_config
    from e2proxy.receivers import _init_receiver_state
    from e2proxy.http_proxy import ProxyHandler
    import e2proxy.recorder  # noqa: F401  registers extension routes

    load_config()
    _init_receiver_state()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", _free_port()), ProxyHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def http(url, method="GET", body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
