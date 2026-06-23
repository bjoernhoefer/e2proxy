#!/usr/bin/env python3
"""
Validates e2proxy's stdlib Azure REST client (AzureRestTransport) against a small
in-memory mock that mimics the Azure Blob + Queue REST surface we use. This can't
replace a real-Azure smoke test, but it exercises exactly the request shapes the
code emits: PUT/GET/HEAD/DELETE blob (with streamed file body + Content-Length)
and POST queue message (base64 XML + messagettl).

Run:  python3 tests/test_azure_rest_transport.py
"""

import os
import sys
import json
import base64
import tempfile
import shutil
import threading
import unittest
import http.server
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class MockAzure(http.server.BaseHTTPRequestHandler):
    blobs = {}          # path -> bytes
    messages = []       # decoded dicts

    def log_message(self, *a):
        pass

    def _key(self):
        # strip leading /container or /queue and the query string
        path = self.path.split("?", 1)[0]
        return path

    def do_PUT(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        assert self.headers.get("x-ms-blob-type") == "BlockBlob", "missing blob-type header"
        MockAzure.blobs[self._key()] = body
        self.send_response(201)
        self.end_headers()

    def do_GET(self):
        data = MockAzure.blobs.get(self._key())
        if data is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_HEAD(self):
        exists = self._key() in MockAzure.blobs
        self.send_response(200 if exists else 404)
        self.end_headers()

    def do_DELETE(self):
        MockAzure.blobs.pop(self._key(), None)
        self.send_response(202)
        self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode("utf-8")
        # <QueueMessage><MessageText>BASE64</MessageText></QueueMessage>
        start = body.index("<MessageText>") + len("<MessageText>")
        end = body.index("</MessageText>")
        decoded = json.loads(base64.b64decode(body[start:end]))
        MockAzure.messages.append(decoded)
        self.send_response(201)
        self.end_headers()


class AzureRestTransportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="e2t-az-")
        os.environ["E2PROXY_DATA_DIR"] = os.path.join(cls.tmp, "data")
        os.makedirs(os.environ["E2PROXY_DATA_DIR"], exist_ok=True)
        sys.path.insert(0, str(REPO))
        import e2proxy
        cls.e2 = e2proxy
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockAzure)
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()
        base = f"http://127.0.0.1:{cls.port}"
        cls.transport = e2proxy.AzureRestTransport(
            f"{base}/container?sig=fake&sv=2021", f"{base}/queue?sig=fake&sv=2021")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_blob_roundtrip_and_streamed_upload(self):
        t = self.transport
        src = os.path.join(self.tmp, "src.bin")
        payload = os.urandom(3 * 1024 * 1024)  # 3MB to exercise chunked streaming
        with open(src, "wb") as f:
            f.write(payload)

        t.put_object("jobs/j1/source.ts", src)
        self.assertTrue(t.object_exists("jobs/j1/source.ts"))

        dst = os.path.join(self.tmp, "dl.bin")
        t.get_object("jobs/j1/source.ts", dst)
        with open(dst, "rb") as f:
            self.assertEqual(f.read(), payload, "streamed upload/download corrupted bytes")

        t.delete_object("jobs/j1/source.ts")
        self.assertFalse(t.object_exists("jobs/j1/source.ts"))

    def test_text_roundtrip(self):
        t = self.transport
        t.put_text("jobs/j1/job.json", json.dumps({"hello": "wörld"}))
        got = json.loads(t.get_text("jobs/j1/job.json"))
        self.assertEqual(got["hello"], "wörld")

    def test_queue_send(self):
        before = len(MockAzure.messages)
        self.transport.queue_send({"job_id": "j1", "job_blob": "jobs/j1/job.json"})
        self.assertEqual(len(MockAzure.messages), before + 1)
        self.assertEqual(MockAzure.messages[-1]["job_id"], "j1")

    def test_object_exists_false_on_404(self):
        self.assertFalse(self.transport.object_exists("does/not/exist"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
