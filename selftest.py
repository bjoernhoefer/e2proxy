#!/usr/bin/env python3
"""Container selftest – run by CI (test.yml) and by pi-deploy on the Pi before a
rollout is accepted:  docker exec <container> python3 /app/selftest.py

Exit 0 = healthy. Checks only what must hold on every host, without a receiver:
API health, WebUI served + its embedded JS self-check, ffmpeg can transcode
with the codecs the stream profiles use.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

BASE = os.environ.get("E2PROXY_SELFTEST_URL", "http://127.0.0.1:8888")


def get(path, timeout=5):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def check_health():
    deadline = time.time() + 60
    while True:
        try:
            status, body = get("/api/health")
            assert status == 200 and json.loads(body).get("ok"), body[:200]
            return
        except Exception:
            if time.time() > deadline:
                raise
            time.sleep(2)


def check_ui():
    for path in ("/", "/settings", "/epg-browser"):
        status, body = get(path, timeout=15)
        assert status == 200 and "</html>" in body.lower(), f"{path}: {status}"


def check_webui_selfcheck():
    with tempfile.TemporaryDirectory() as d:
        r = subprocess.run([sys.executable, "-m", "e2proxy", "--selfcheck"], cwd="/app",
                           env={**os.environ, "E2PROXY_DATA_DIR": d},
                           capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, (r.stdout + r.stderr)[-1500:]


def check_transcode():
    """Same codec path as the H.264 stream profile, 2 s of test video in < 60 s."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
           "-f", "lavfi", "-i", "testsrc=duration=2:size=640x360:rate=25",
           "-f", "lavfi", "-i", "sine=duration=2",
           "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-f", "mpegts", "-"]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    assert r.returncode == 0 and len(r.stdout) > 10_000, r.stderr.decode()[-500:]
    return time.time() - t0


def main():
    checks = [("health", check_health), ("ui", check_ui),
              ("webui-selfcheck", check_webui_selfcheck), ("transcode", check_transcode)]
    failed = 0
    for name, fn in checks:
        try:
            res = fn()
            print(f"OK   {name}" + (f" ({res:.1f}s)" if isinstance(res, float) else ""))
        except Exception as e:  # report all checks, not only the first failure
            failed += 1
            print(f"FAIL {name}: {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
