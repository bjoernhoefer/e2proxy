# e2proxy — External Transcoding Worker

Offloads `.ts → .mkv` transcoding from e2proxy (Raspberry Pi) to an external
machine: an **Azure VM on-demand** or any **home/office PC**. The worker pulls
jobs from a queue, runs ffmpeg, uploads the result and notifies e2proxy.

```
e2proxy (Pi)              Storage (Blob / shared dir)        Worker (this program)
  recording done ─upload→ jobs/<id>/source.ts + job.json
  enqueue job ──────────→ queue
                                              ←─ claim (visibility-timeout lease)
                                              transcode (ffmpeg, profile from job)
                          jobs/<id>/output.mkv ←─ upload
                          jobs/<id>/done.json  ←─ upload
  webhook  ←──── notify(job_id, status, sha256) ──────────────
  download output → verify sha256 → delete remote → delete local .ts
```

The **queue visibility timeout is the "stuck job" safety net**: a claimed job
stays invisible only for the lease duration; if the worker dies mid-job the
message reappears and is retried. A heartbeat thread renews the lease while
ffmpeg runs. After `E2T_MAX_DEQUEUE` failed attempts the job is dead-lettered
and reported back as `failed`.

## Transports

| Provider    | Use case                                   |
|-------------|--------------------------------------------|
| `azure`     | Azure Blob + Azure Storage Queue (cloud)   |
| `filestore` | A shared directory (SMB mount / local) — on-prem, no cloud |

The `filestore` provider lets a home PC act as transcoder with nothing more than
a shared folder (e.g. the same SMB share the Pi writes to).

## Install

```bash
# Requires ffmpeg on PATH and Python 3.9+
pip install -r requirements.txt          # only needed for the azure provider
sudo apt-get install -y ffmpeg           # or your platform's ffmpeg
```

## Configuration (environment variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `E2T_PROVIDER` | `azure` | `azure` or `filestore` |
| `E2T_WORKER_ID` | hostname | Identifies this worker in logs/notify |
| `E2T_VISIBILITY` | `900` | Lease seconds a claimed job stays invisible |
| `E2T_HEARTBEAT` | `120` | Lease-renew interval (must be < visibility) |
| `E2T_POLL_INTERVAL` | `10` | Idle queue poll interval |
| `E2T_MAX_DEQUEUE` | `3` | Attempts before dead-letter |
| `E2T_NOTIFY_SECRET` | – | Shared HMAC secret (must match e2proxy) |
| `E2T_FFMPEG` | `ffmpeg` | Path to ffmpeg (e.g. an NVENC build) |
| `E2T_WORK_DIR` | `/tmp/e2t-work` | Scratch dir for downloads/encoding |
| `E2T_IDLE_EXIT_AFTER` | `0` | Exit after N idle seconds (0 = never). Use on cloud VMs to trigger auto-deallocate. |
| **azure** | | |
| `E2T_AZURE_CONNECTION_STRING` | – | Storage account connection string … |
| `E2T_BLOB_CONTAINER` | – | … with container + queue name |
| `E2T_QUEUE_NAME` | – | |
| `E2T_BLOB_CONTAINER_SAS_URL` | – | …or use SAS URLs instead of the conn string |
| `E2T_QUEUE_SAS_URL` | – | |
| **filestore** | | |
| `E2T_FILESTORE_PATH` | – | Shared directory root (same one e2proxy uses) |

> The notify secret is configured on **both** sides out-of-band and is never
> written to storage. Leave e2proxy's `notify_url` empty to rely purely on its
> poll fallback (the worker still writes `done.json`).

## Run — Azure VM

```bash
export E2T_PROVIDER=azure
export E2T_AZURE_CONNECTION_STRING="DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net"
export E2T_BLOB_CONTAINER=transcode
export E2T_QUEUE_NAME=transcode-jobs
export E2T_NOTIFY_SECRET="$(cat /etc/e2t/secret)"
export E2T_IDLE_EXIT_AFTER=600          # power down after 10 min idle
python3 external_transcode_worker.py
```

A systemd service or a cloud-init `command` can start this on boot. Pair
`E2T_IDLE_EXIT_AFTER` with an Azure auto-shutdown / deallocation so the VM only
costs money while there is work.

## Run — Home PC (filestore over SMB)

```bash
export E2T_PROVIDER=filestore
export E2T_FILESTORE_PATH=/mnt/nas/transcode    # same share e2proxy writes to
export E2T_NOTIFY_SECRET="shared-secret"
python3 external_transcode_worker.py
```

## Cost / sizing notes

- A 1 h HD recording is ~6–10 GB. Uploading the **source** is the slow/expensive
  part; the converted output is much smaller. External transcoding pays off for
  **backlogs/batches** or a weak Pi, less so for a single occasional file.
- On Azure, deallocate (not just stop) the VM to halt compute billing. Spot VMs
  or Azure Container Instances/Batch are cheaper than an always-on VM.
- Use a hardware-accelerated ffmpeg (`E2T_FFMPEG`) on capable workers (NVENC/QSV).

## Testing

The repository's `tests/` validate the full pipeline against the `filestore`
provider with a fake ffmpeg, plus e2proxy's Azure REST client against a mock:

```bash
python3 tests/test_external_transcode_e2e.py
python3 tests/test_azure_rest_transport.py
```
