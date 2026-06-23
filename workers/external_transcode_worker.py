#!/usr/bin/env python3
"""
e2proxy — external transcoding worker
=====================================

Pulls transcode jobs from a queue, runs ffmpeg, uploads the result and notifies
e2proxy. Pluggable transport:

  * azure     — Azure Blob + Azure Storage Queue (cloud VM or any machine)
  * filestore — a shared directory (SMB mount / local; used by the tests and for
                on-prem / home-PC setups without any cloud)

Queue semantics provide the "stuck job" safety net: a claimed message stays
invisible only for the visibility timeout; if this worker dies mid-job the
message reappears and another worker (or the next run) picks it up. A heartbeat
thread renews the lease while ffmpeg is running.

Configuration is via environment variables — see README.md.

Stdlib only for the filestore provider; the azure provider lazily imports
azure-storage-blob / azure-storage-queue (see requirements.txt).
"""

import os
import sys
import json
import time
import base64
import hashlib
import hmac
import socket
import signal
import threading
import subprocess
import urllib.request
from datetime import datetime


def log(msg):
    print(f"{datetime.now().isoformat(timespec='seconds')} {msg}", flush=True)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Config ──────────────────────────────────────────────────────────────────

class Config:
    def __init__(self):
        e = os.environ.get
        self.provider = e("E2T_PROVIDER", "azure").strip().lower()
        self.worker_id = e("E2T_WORKER_ID", socket.gethostname())
        self.visibility = int(e("E2T_VISIBILITY", "900"))      # lease seconds
        self.heartbeat = int(e("E2T_HEARTBEAT", "120"))        # renew interval
        self.poll_interval = int(e("E2T_POLL_INTERVAL", "10"))
        self.max_dequeue = int(e("E2T_MAX_DEQUEUE", "3"))
        self.notify_secret = e("E2T_NOTIFY_SECRET", "")
        self.ffmpeg = e("E2T_FFMPEG", "ffmpeg")
        self.work_dir = e("E2T_WORK_DIR", "/tmp/e2t-work")
        self.idle_exit_after = int(e("E2T_IDLE_EXIT_AFTER", "0"))  # 0 = never exit
        # azure
        self.blob_container_sas_url = e("E2T_BLOB_CONTAINER_SAS_URL", "")
        self.queue_sas_url = e("E2T_QUEUE_SAS_URL", "")
        self.azure_connection_string = e("E2T_AZURE_CONNECTION_STRING", "")
        self.blob_container = e("E2T_BLOB_CONTAINER", "")
        self.queue_name = e("E2T_QUEUE_NAME", "")
        # azure via managed identity (account URLs + container/queue names)
        self.blob_account_url = e("E2T_BLOB_ACCOUNT_URL", "")
        self.queue_account_url = e("E2T_QUEUE_ACCOUNT_URL", "")
        # filestore
        self.filestore_path = e("E2T_FILESTORE_PATH", "")
        os.makedirs(self.work_dir, exist_ok=True)


# ── Message wrapper ─────────────────────────────────────────────────────────

class Msg:
    def __init__(self, body, dequeue_count, handle):
        self.body = body                # dict
        self.dequeue_count = dequeue_count
        self.handle = handle            # provider-specific


# ── Filestore transport ─────────────────────────────────────────────────────

class FilestoreTransport:
    def __init__(self, base):
        if not base:
            raise ValueError("E2T_FILESTORE_PATH not set")
        self.base = base
        self.blob_dir = os.path.join(base, "blobs")
        self.q_avail = os.path.join(base, "queue", "available")
        self.q_lease = os.path.join(base, "queue", "leased")
        self.q_poison = os.path.join(base, "queue", "poison")
        for d in (self.blob_dir, self.q_avail, self.q_lease, self.q_poison):
            os.makedirs(d, exist_ok=True)

    def _p(self, key):
        return os.path.join(self.blob_dir, key.replace("/", os.sep))

    def get_text(self, key):
        with open(self._p(key), encoding="utf-8") as f:
            return f.read()

    def get_object(self, key, local_path):
        import shutil
        shutil.copyfile(self._p(key), local_path)

    def put_object(self, key, local_path):
        import shutil
        dst = self._p(key)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".part"
        shutil.copyfile(local_path, tmp)
        os.replace(tmp, dst)

    def put_text(self, key, text):
        dst = self._p(key)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, dst)

    def _reclaim_expired(self):
        now = time.time()
        for fn in os.listdir(self.q_lease):
            if not fn.endswith(".json"):
                continue
            fp = os.path.join(self.q_lease, fn)
            try:
                with open(fp, encoding="utf-8") as f:
                    env = json.load(f)
            except Exception:
                continue
            if env.get("lease_until", 0) < now:
                mid = env["id"]
                back = os.path.join(self.q_avail, f"{mid}.json")
                env.pop("lease_until", None)
                try:
                    with open(fp + ".tmp", "w", encoding="utf-8") as f:
                        json.dump(env, f)
                    os.replace(fp + ".tmp", back)
                    os.remove(fp)
                except OSError:
                    pass

    def receive(self, visibility):
        self._reclaim_expired()
        candidates = sorted(fn for fn in os.listdir(self.q_avail) if fn.endswith(".json"))
        for fn in candidates:
            src = os.path.join(self.q_avail, fn)
            mid = fn[:-5]
            token = base64.urlsafe_b64encode(os.urandom(6)).decode("ascii").rstrip("=")
            leased = os.path.join(self.q_lease, f"{mid}.{token}.json")
            try:
                os.rename(src, leased)   # atomic claim — only one worker wins
            except OSError:
                continue
            try:
                with open(leased, encoding="utf-8") as f:
                    env = json.load(f)
            except Exception:
                continue
            env["dequeue_count"] = int(env.get("dequeue_count", 0)) + 1
            env["lease_until"] = time.time() + visibility
            with open(leased, "w", encoding="utf-8") as f:
                json.dump(env, f)
            return Msg(env["body"], env["dequeue_count"], {"path": leased, "id": mid})
        return None

    def renew(self, msg, visibility):
        fp = msg.handle["path"]
        try:
            with open(fp, encoding="utf-8") as f:
                env = json.load(f)
            env["lease_until"] = time.time() + visibility
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(env, f)
        except Exception:
            pass

    def delete(self, msg):
        try:
            os.remove(msg.handle["path"])
        except OSError:
            pass

    def dead_letter(self, msg):
        try:
            os.replace(msg.handle["path"],
                       os.path.join(self.q_poison, f"{msg.handle['id']}.json"))
        except OSError:
            self.delete(msg)


# ── Azure transport ─────────────────────────────────────────────────────────

class AzureTransport:
    def __init__(self, cfg):
        from azure.storage.blob import ContainerClient
        from azure.storage.queue import QueueClient
        if cfg.azure_connection_string and cfg.blob_container and cfg.queue_name:
            # Auth via account connection string (account key).
            self.container = ContainerClient.from_connection_string(
                cfg.azure_connection_string, cfg.blob_container)
            self.queue = QueueClient.from_connection_string(
                cfg.azure_connection_string, cfg.queue_name)
        elif cfg.blob_container_sas_url and cfg.queue_sas_url:
            # Auth via SAS URLs (signature embedded in the URL).
            self.container = ContainerClient.from_container_url(cfg.blob_container_sas_url)
            self.queue = QueueClient.from_queue_url(cfg.queue_sas_url)
        elif (cfg.blob_account_url and cfg.queue_account_url
              and cfg.blob_container and cfg.queue_name):
            # Auth via Managed Identity (no secrets) — the ARM deployment path.
            from azure.identity import DefaultAzureCredential
            cred = DefaultAzureCredential()
            self.container = ContainerClient(
                account_url=cfg.blob_account_url,
                container_name=cfg.blob_container, credential=cred)
            self.queue = QueueClient(
                account_url=cfg.queue_account_url,
                queue_name=cfg.queue_name, credential=cred)
        else:
            raise ValueError(
                "Azure transport needs one of: "
                "E2T_AZURE_CONNECTION_STRING + E2T_BLOB_CONTAINER + E2T_QUEUE_NAME; "
                "or E2T_BLOB_CONTAINER_SAS_URL + E2T_QUEUE_SAS_URL; "
                "or E2T_BLOB_ACCOUNT_URL + E2T_QUEUE_ACCOUNT_URL + "
                "E2T_BLOB_CONTAINER + E2T_QUEUE_NAME (managed identity)")

    def get_text(self, key):
        return self.container.download_blob(key).readall().decode("utf-8", "replace")

    def get_object(self, key, local_path):
        with open(local_path, "wb") as f:
            self.container.download_blob(key).readinto(f)

    def put_object(self, key, local_path):
        with open(local_path, "rb") as f:
            self.container.upload_blob(name=key, data=f, overwrite=True)

    def put_text(self, key, text):
        self.container.upload_blob(name=key, data=text.encode("utf-8"), overwrite=True)

    def receive(self, visibility):
        msgs = self.queue.receive_messages(visibility_timeout=visibility, max_messages=1)
        for m in msgs:
            content = m.content
            try:
                body = json.loads(base64.b64decode(content))
            except Exception:
                body = json.loads(content)
            return Msg(body, m.dequeue_count, m)
        return None

    def renew(self, msg, visibility):
        updated = self.queue.update_message(msg.handle, visibility_timeout=visibility)
        # keep the fresh pop receipt for subsequent renew/delete
        try:
            msg.handle.pop_receipt = updated.pop_receipt
        except Exception:
            pass

    def delete(self, msg):
        self.queue.delete_message(msg.handle)

    def dead_letter(self, msg):
        # No separate poison queue in this MVP — just remove it.
        self.delete(msg)


def make_transport(cfg):
    if cfg.provider == "filestore":
        return FilestoreTransport(cfg.filestore_path)
    return AzureTransport(cfg)


# ── Transcode ───────────────────────────────────────────────────────────────

def transcode(cfg, src, dst, profile):
    cmd = [
        cfg.ffmpeg, "-y", "-hide_banner", "-loglevel", "warning", "-nostats",
        "-i", src,
        "-c:v", profile.get("vcodec", "libx265"),
        "-preset", profile.get("preset", "medium"),
        "-crf", str(profile.get("crf", 24)),
        "-c:a", "aac", "-b:a", profile.get("audio_bitrate", "192k"),
        "-c:s", "copy",
        "-map", "0", "-map", "-0:d?",
        "-f", profile.get("container", "matroska"),
        dst,
    ]
    log(f"ffmpeg: {os.path.basename(src)} → {os.path.basename(dst)} "
        f"[{profile.get('vcodec')} crf{profile.get('crf')} {profile.get('preset')}]")
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace")[-500:]
        raise RuntimeError(f"ffmpeg failed (rc={proc.returncode}): {err.strip()[:300]}")


# ── Notify ──────────────────────────────────────────────────────────────────

def notify(notify_cfg, payload, secret):
    url = (notify_cfg or {}).get("url", "")
    if not url:
        return
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "e2t-worker"}
    if secret:
        sig = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-E2P-Signature"] = sig
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            log(f"notify {url} → HTTP {r.getcode()}")
    except Exception as e:
        log(f"notify {url} failed: {e}")


# ── Job processing ──────────────────────────────────────────────────────────

def process(cfg, transport, msg):
    job_blob = msg.body.get("job_blob")
    manifest = json.loads(transport.get_text(job_blob))
    job_id = manifest["job_id"]
    short = job_id[:8]
    profile = manifest.get("profile", {})
    notify_cfg = manifest.get("notify", {})

    if msg.dequeue_count > cfg.max_dequeue:
        log(f"job={short} exceeded max dequeue ({msg.dequeue_count}) → dead-letter")
        done = {"job_id": job_id, "status": "failed",
                "error": f"exceeded max attempts ({cfg.max_dequeue})",
                "worker": cfg.worker_id, "finished": datetime.now().isoformat()}
        try:
            transport.put_text(manifest["done_blob"], json.dumps(done))
            notify(notify_cfg, done, cfg.notify_secret)
        finally:
            transport.dead_letter(msg)
        return

    work = os.path.join(cfg.work_dir, job_id)
    os.makedirs(work, exist_ok=True)
    src = os.path.join(work, "source.ts")
    dst = os.path.join(work, f"output.{profile.get('ext', 'mkv')}")

    stop = threading.Event()

    def heartbeat():
        while not stop.wait(cfg.heartbeat):
            transport.renew(msg, cfg.visibility)
            log(f"job={short} lease renewed")

    hb = threading.Thread(target=heartbeat, daemon=True)
    hb.start()
    started = time.time()
    try:
        log(f"job={short} download source {manifest['source_blob']}")
        transport.get_object(manifest["source_blob"], src)
        want = manifest.get("source_sha256")
        if want and sha256_file(src) != want:
            raise RuntimeError("source sha256 mismatch")

        transcode(cfg, src, dst, profile)

        out_size = os.path.getsize(dst)
        out_sha = sha256_file(dst)
        log(f"job={short} upload output {manifest['output_blob']} ({out_size/1024/1024:.1f} MB)")
        transport.put_object(manifest["output_blob"], dst)

        done = {
            "job_id": job_id, "status": "completed",
            "output_blob": manifest["output_blob"],
            "output_size": out_size, "output_sha256": out_sha,
            "worker": cfg.worker_id, "elapsed": int(time.time() - started),
            "finished": datetime.now().isoformat(),
        }
        transport.put_text(manifest["done_blob"], json.dumps(done))
        notify(notify_cfg, done, cfg.notify_secret)
        transport.delete(msg)
        log(f"job={short} DONE in {done['elapsed']}s")
    finally:
        stop.set()
        hb.join(timeout=5)
        for p in (src, dst):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(work)
        except OSError:
            pass


# ── Main loop ───────────────────────────────────────────────────────────────

_RUNNING = True


def _stop(*_):
    global _RUNNING
    _RUNNING = False


def main():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    cfg = Config()
    transport = make_transport(cfg)
    log(f"worker '{cfg.worker_id}' started (provider={cfg.provider}, "
        f"visibility={cfg.visibility}s)")
    idle_since = None
    while _RUNNING:
        try:
            msg = transport.receive(cfg.visibility)
        except Exception as e:
            log(f"receive error: {e}")
            time.sleep(cfg.poll_interval)
            continue
        if msg is None:
            if cfg.idle_exit_after:
                idle_since = idle_since or time.time()
                if time.time() - idle_since >= cfg.idle_exit_after:
                    log(f"idle for {cfg.idle_exit_after}s → exiting (auto-deallocate hook)")
                    break
            time.sleep(cfg.poll_interval)
            continue
        idle_since = None
        try:
            process(cfg, transport, msg)
        except Exception as e:
            log(f"job failed (will retry after visibility timeout): {e}")
    log("worker stopped")


if __name__ == "__main__":
    sys.exit(main())
