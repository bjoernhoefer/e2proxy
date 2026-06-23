#!/usr/bin/env python3
"""
End-to-end test for the external transcoding MVP using the `filestore` transport
(a shared directory — no cloud required) and a fake ffmpeg.

Covers:
  * e2proxy submit  → upload source + manifest + enqueue
  * worker          → claim, "transcode", upload output + done.json, notify
  * e2proxy poll    → download, verify sha256, place .mkv, cleanup remote + local
  * worker notify HMAC contract (worker → e2proxy webhook signature)

Run:  python3 tests/test_external_transcode_e2e.py
"""

import os
import sys
import json
import time
import hmac
import hashlib
import tempfile
import shutil
import stat
import threading
import unittest
import http.server
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKERS = REPO / "workers"


def _make_fake_ffmpeg(dirpath):
    p = os.path.join(dirpath, "fake_ffmpeg.sh")
    with open(p, "w") as f:
        f.write(
            "#!/bin/bash\n"
            "out=\"${@: -1}\"\n"
            "printf 'FAKE_MKV ' > \"$out\"\n"
            "head -c 40000 /dev/zero >> \"$out\"\n"
            "exit 0\n"
        )
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


class ExternalTranscodeE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="e2t-test-")
        # e2proxy must see its DATA_DIR before import
        cls.data_dir = os.path.join(cls.tmp, "data")
        os.makedirs(cls.data_dir, exist_ok=True)
        os.environ["E2PROXY_DATA_DIR"] = cls.data_dir
        sys.path.insert(0, str(REPO))
        sys.path.insert(0, str(WORKERS))
        import e2proxy
        import external_transcode_worker as worker
        cls.e2 = e2proxy
        cls.worker = worker

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _configure(self, notify_url="", secret=""):
        e2 = self.e2
        store = os.path.join(self.tmp, "store")
        rec = os.path.join(self.tmp, "recordings")
        os.makedirs(rec, exist_ok=True)
        cfg = e2.get_config()
        cfg["recordings_path"] = rec
        cfg["external_transcode"] = {
            "enabled": True,
            "provider": "filestore",
            "profile": "balanced",
            "delete_original": True,
            "filestore_path": store,
            "notify_url": notify_url,
            "notify_type": "e2proxy",
            "notify_secret": secret,
            "stuck_minutes": 60,
            "max_attempts": 2,
        }
        e2.update_config(cfg)
        return store, rec

    def _make_recording(self, rec, name="Test_Show_2026-01-01_20-00.ts"):
        path = os.path.join(rec, name)
        with open(path, "wb") as f:
            f.write(b"TS" + os.urandom(12 * 1024 * 1024))  # >10MB so it's "pending"
        return path

    def _run_worker_once(self, store, secret=""):
        os.environ.update({
            "E2T_PROVIDER": "filestore",
            "E2T_FILESTORE_PATH": store,
            "E2T_FFMPEG": _make_fake_ffmpeg(self.tmp),
            "E2T_WORKER_ID": "test-worker",
            "E2T_WORK_DIR": os.path.join(self.tmp, "work"),
            "E2T_VISIBILITY": "300",
            "E2T_HEARTBEAT": "999",
            "E2T_NOTIFY_SECRET": secret,
        })
        wcfg = self.worker.Config()
        wt = self.worker.make_transport(wcfg)
        msg = wt.receive(wcfg.visibility)
        self.assertIsNotNone(msg, "worker did not receive a queued message")
        self.worker.process(wcfg, wt, msg)
        return wt

    def test_01_full_pipeline_via_poll(self):
        e2 = self.e2
        store, rec = self._configure()
        ts_path = self._make_recording(rec)

        # 1) submit
        job_id = e2.external_transcode_submit(ts_path)
        job = e2._ext_get_job(job_id)
        self.assertEqual(job["status"], "queued")
        ft = e2.FilestoreTransport(store)
        self.assertTrue(ft.object_exists(job["source_blob"]))
        self.assertTrue(ft.object_exists(job["job_blob"]))

        # 2) worker processes the job
        self._run_worker_once(store)
        self.assertTrue(ft.object_exists(job["output_blob"]))
        self.assertTrue(ft.object_exists(job["done_blob"]))

        # 3) e2proxy poll picks up completion (webhook-independent path)
        e2._ext_poll_completions(e2.get_external_transcode_config())

        # 4) assertions: .mkv placed, .ts gone, remote cleaned, state completed
        mkv = ts_path[:-3] + ".mkv"
        self.assertTrue(os.path.exists(mkv), ".mkv was not placed")
        self.assertFalse(os.path.exists(ts_path), "original .ts was not deleted")
        for blob in (job["source_blob"], job["output_blob"],
                     job["job_blob"], job["done_blob"]):
            self.assertFalse(ft.object_exists(blob), f"remote blob not cleaned: {blob}")
        final = e2._ext_get_job(job_id)
        self.assertEqual(final["status"], "completed")
        self.assertGreater(final["new_size"], 0)

    def test_02_completion_idempotent(self):
        e2 = self.e2
        store, rec = self._configure()
        ts_path = self._make_recording(rec, "Idem_2026-02-02_21-00.ts")
        job_id = e2.external_transcode_submit(ts_path)
        self._run_worker_once(store)
        ft = e2.FilestoreTransport(store)
        done = json.loads(ft.get_text(e2._ext_get_job(job_id)["done_blob"]))

        e2._ext_handle_completion(job_id, done, "webhook")
        # second call must be a no-op (already completed) and must not raise
        e2._ext_handle_completion(job_id, done, "webhook")
        self.assertEqual(e2._ext_get_job(job_id)["status"], "completed")

    def test_03_sha_mismatch_keeps_original(self):
        e2 = self.e2
        store, rec = self._configure()
        ts_path = self._make_recording(rec, "Bad_2026-03-03_22-00.ts")
        job_id = e2.external_transcode_submit(ts_path)
        self._run_worker_once(store)
        job = e2._ext_get_job(job_id)
        ft = e2.FilestoreTransport(store)
        done = json.loads(ft.get_text(job["done_blob"]))
        done["output_sha256"] = "deadbeef" * 8  # corrupt the expected hash

        e2._ext_handle_completion(job_id, done, "webhook")
        self.assertEqual(e2._ext_get_job(job_id)["status"], "failed")
        self.assertTrue(os.path.exists(ts_path), "original must be kept on verify failure")
        self.assertFalse(os.path.exists(ts_path[:-3] + ".mkv"))

    def test_04_worker_notify_hmac(self):
        """Worker → e2proxy webhook: HMAC signature must match the shared secret."""
        secret = "s3cr3t-shared"
        captured = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                captured["body"] = self.rfile.read(n)
                captured["sig"] = self.headers.get("X-E2P-Signature", "")
                self.send_response(200)
                self.end_headers()

        srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.handle_request, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}/notify"

        payload = {"job_id": "abc123", "status": "completed", "worker": "w1"}
        self.worker.notify({"url": url, "type": "e2proxy"}, payload, secret)
        time.sleep(0.3)
        srv.server_close()

        self.assertIn("body", captured, "webhook was not called")
        expected = "sha256=" + hmac.new(secret.encode(), captured["body"],
                                        hashlib.sha256).hexdigest()
        self.assertTrue(hmac.compare_digest(expected, captured["sig"]),
                        "HMAC signature mismatch")
        self.assertEqual(json.loads(captured["body"])["status"], "completed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
