# e2proxy

**Enigma2 Streaming Proxy** — Stream satellite TV from Enigma2 receivers to any browser, Plex, Jellyfin, or Kodi.

Single Python file, runs in Docker, manages two SAT receivers with automatic tuner allocation, EPG, recordings, and Plex DVR integration.

## Features

- **Live TV Streaming** — Watch any channel in the browser (WebM VP8/VP9) or via M3U playlist
- **Plex DVR Integration** — HDHomeRun emulation with SSDP auto-discovery, no Threadfin needed
- **EPG Browser** — 28-hour program guide as interactive timeline grid with TMDB artwork
- **Recording System** — ffmpeg-based with Plex-compliant directory structure (`TV/Show/Season XX/`)
- **TVDB + TMDB** — Automatic series/episode detection, daily-show fallback numbering
- **Shared Tuner** — Parallel recordings on the same channel without tuner conflicts
- **Favorites** — Drag & drop channel ordering with group/category assignments
- **Bilingual UI** — English/German with browser language auto-detection
- **Dark/Light Theme** — Switchable in settings

## Quick Start

### Docker (recommended)

```yaml
# docker-compose.yml
services:
  e2proxy:
    image: python:3.11-slim
    container_name: e2proxy
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./e2proxy.py:/app/e2proxy.py:ro
      - ./data:/data
      - /mnt/nvme/recordings:/mnt/nvme/recordings
    working_dir: /app
    command: >
      bash -c "apt-get update -qq &&
               apt-get install -y -q --no-install-recommends ffmpeg &&
               python3 e2proxy.py"
    environment:
      - PYTHONUNBUFFERED=1
      - E2PROXY_DATA_DIR=/data
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8888/api/status', timeout=3)"]
      interval: 30s
      timeout: 5s
      retries: 3
```

```bash
docker compose up -d
```

Open http://your-server:8888 and configure your receivers in Settings.

### Requirements

- Python 3.11+
- ffmpeg
- Enigma2 receiver(s) on the local network
- `network_mode: host` (required for SSDP UDP multicast on port 1900)

## Architecture

```
┌──────────────┐     ┌──────────────┐
│  Browser     │     │  Plex/Kodi   │
│  EPG Browser │     │  Jellyfin    │
└──────┬───────┘     └──────┬───────┘
       │                    │
       │  HTTP :8888        │  HDHomeRun / XMLTV
       │                    │
┌──────┴────────────────────┴───────┐
│           e2proxy                  │
│  ┌─────────┐  ┌─────────┐        │
│  │ Tuner 1 │  │ Tuner 2 │        │
│  │ (alloc) │  │ (alloc) │        │
│  └────┬────┘  └────┬────┘        │
│       │             │             │
│  ┌────┴─────────────┴────┐       │
│  │   ffmpeg (transcode)  │       │
│  └───────────────────────┘       │
└──────┬────────────────────┬───────┘
       │                    │
┌──────┴───────┐    ┌───────┴──────┐
│  Receiver 1  │    │  Receiver 2  │
│  (Enigma2)   │    │  (Enigma2)   │
│  :8001 SAT   │    │  :8001 SAT   │
└──────────────┘    └──────────────┘
```

## Configuration

First-time setup via the web UI at `http://your-server:8888/settings`:

1. **Receivers** — Add your Enigma2 receiver IPs (port 80 for web, 8001 for streaming)
2. **Favorites** — Select channels for EPG and Plex DVR lineup
3. **EPG Schedule** — Set the daily EPG update time (default: 3:00 AM)
4. **TMDB API Key** — Optional, for poster artwork in EPG
5. **TVDB API Key** — Optional, for series/episode detection in recordings
6. **Plex Integration** — URL + token for automatic library refresh after recordings

All configuration stored in `/data/config.json`.

## Web UI

| Page | URL | Description |
|------|-----|-------------|
| Main | `/` | Channel list, player, tuner status |
| EPG Browser | `/epg-browser` | Interactive timeline with recording buttons |
| Favorites | `/favorites` | Drag & drop channel ordering |
| Settings | `/settings` | Configuration, maintenance, EPG, recordings |
| Help | `/help` | Feature overview, API reference, changelog |

## API

### Channels & EPG

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/channels` | GET | All channels (247+) |
| `/api/favorites` | GET/POST | Favorite channels |
| `/api/epg/data` | GET | EPG data (28h window, JSON) |
| `/api/epg/status` | GET | EPG update status |
| `/epg.xml` | GET | XMLTV for Plex/Kodi |
| `/playlist.m3u` | GET | M3U playlist |

### Recordings

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/record/start` | POST | Start recording (`{ref, title, duration, kind}`) |
| `/api/record/stop` | POST | Stop recording (`{recording_id}`) |
| `/api/record/status` | GET | Active recordings |
| `/api/recordings` | GET | Recorded files |
| `/api/recordings/delete` | DELETE | Delete recording |

### System

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/version` | GET | Version + build ID |
| `/api/health` | GET | Health check for monitoring |
| `/api/status` | GET | Proxy status |
| `/api/tuners` | GET | Tuner allocation |
| `/api/config` | GET/POST | Configuration |
| `/api/logs` | GET | Live logs (`?level=INFO&since=<unix>&n=100`) |
| `/api/logs/history` | GET | Historical logs from disk (`?hours=6`) |
| `/api/external-transcode/status` | GET | External transcode jobs + state |
| `/api/external-transcode/notify` | POST | Worker completion webhook (HMAC `X-E2P-Signature`) |

### Plex DVR (HDHomeRun Emulation)

| Endpoint | Description |
|----------|-------------|
| `/discover.json` | Device discovery |
| `/lineup.json` | Channel lineup |
| `/device.xml` | SSDP device descriptor |

## Recording Structure

```
/mnt/nvme/recordings/
├── Movies/
│   └── Casablanca (2026)/
│       ├── Casablanca (2026).ts
│       └── Casablanca (2026).nfo
└── TV/
    ├── Star Trek TNG/
    │   ├── Season 07/
    │   │   ├── Star Trek TNG - S07E04 - Gambit.ts
    │   │   └── Star Trek TNG - S07E04 - Gambit.nfo
    │   └── tvshow.nfo
    └── Das perfekte Dinner/
        └── Season 2026/
            ├── Das perfekte Dinner - S2026E163.ts
            └── Das perfekte Dinner - S2026E163.nfo
```

- **Series with TVDB match** → Real S/E numbers
- **Daily shows** → Day-of-year numbering (S2026E163 = June 12)
- **Movies** → `Movies/<Title> (<Year>)/`
- **NFO files** → Plex/Kodi-compatible metadata

## External Transcoding (offload to cloud/PC)

Optionally offload the `.ts → .mkv` conversion to an **external worker** — an
Azure VM spun up on demand, or any home/office PC — instead of doing it on the
Pi. e2proxy uploads the recording + a job manifest to storage and enqueues a
job; the worker transcodes and uploads the result; e2proxy downloads it, verifies
the sha256, then deletes the remote copy and the big local `.ts`.

When enabled, external transcoding **takes over** the pending recordings (the
local compression scheduler steps aside). A missed notification never loses a
job — e2proxy also polls `done.json` as the source of truth. Stuck jobs are
detected via the queue lease (worker side) and a `stuck_minutes` timeout
(e2proxy side, with retry up to `max_attempts`).

Two transports: **`azure`** (Blob + Storage Queue, via REST+SAS — no extra
Python deps in e2proxy) or **`filestore`** (a shared SMB/local directory, for
on-prem-only setups). The worker and its setup live in [`workers/`](workers/).

### Configuration (`/data/config.json`, key `external_transcode`)

```json
{
  "external_transcode": {
    "enabled": true,
    "provider": "azure",
    "profile": "quality",
    "delete_original": true,
    "max_active": 2,
    "stuck_minutes": 120,
    "max_attempts": 2,
    "blob_sas_url": "https://acct.blob.core.windows.net/transcode?<container-SAS>",
    "queue_sas_url": "https://acct.queue.core.windows.net/transcode-jobs?<queue-SAS>",
    "filestore_path": "",
    "notify_url": "https://your-ha/api/webhook/e2proxy-transcode",
    "notify_type": "e2proxy",
    "notify_secret": "shared-hmac-secret"
  }
}
```

- `profile` reuses the local compression profiles (`fast` / `balanced` / `quality`).
- `notify_url` is where the worker reports completion. Point it at e2proxy
  (`/api/external-transcode/notify`) or a Home Assistant webhook. The
  `notify_secret` is shared with the worker and used for an HMAC signature
  (`X-E2P-Signature`); it is never written to storage. Leaving `notify_url`
  empty falls back to e2proxy's polling.

## Companion: e2recorder

[e2recorder](https://github.com/bjoernhoefer/e2recorder) is the automated recording scheduler that works with e2proxy. It monitors the EPG and triggers recordings via the `/api/record/start` endpoint.

## Data Paths

| Path | Content |
|------|---------|
| `/data/config.json` | Configuration |
| `/data/favorites.json` | Favorite channels |
| `/data/e2proxy.log` | System log (daily rotation) |
| `/data/api_access.log` | API access log |
| `/data/epg_cache.xml` | EPG disk cache |
| `/data/tmdb_cache.json` | TMDB poster cache |
| `/data/tvdb_cache.json` | TVDB series cache |
| `/data/external_transcode_state.json` | External transcode job state |
| `/data/logos/` | Channel logo cache |

## Update

```bash
# Copy new version and restart
scp e2proxy.py user@server:~/e2proxy/
docker compose -f ~/e2proxy/docker-compose.e2proxy.yml restart

# Verify
curl -s http://server:8888/api/version
```

## License

MIT

## Credits

Built with [Claude](https://claude.ai) by Anthropic.
