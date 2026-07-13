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
- **Fast Switching** — Per-channel switch tuning (NoLatency probing, configurable zap wait) with self-learning probesize and zap/start statistics
- **Tuner Lock** — Temporarily lock a tuner in Settings so e2proxy won't use it (handy in exceptional situations)
- **Favorites** — Drag & drop channel ordering with group/category assignments
- **Editable Logos** — Set a custom logo per favorite (upload or URL), auto-converted to PNG via ffmpeg
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

1. **Receivers** — Add your Enigma2 receiver IPs (port 80 for web, 8001 for streaming). Each receiver can be locked to temporarily exclude its tuner from use.
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
| `/api/favorites/logos` | GET | Favorite logos overview (name, ref, current + auto URL, custom flag) |
| `/api/favorites/logo` | POST | Set a custom favorite logo (`{name, url}` or `{name, data}` base64) — converted to PNG |
| `/api/favorites/logo/reset` | POST | Remove custom logo, fall back to automatic (`{name}`) |
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
| `/api/switch/stats` | GET | Switch-tuning stats (global + per-channel) |
| `/api/switch/settings` | POST | Global defaults and/or per-channel override (`{global, ref, no_latency, zap_wait, probesize}`) |
| `/api/switch/reset` | POST | Reset learned values/stats (`{ref}` or all) |
| `/api/logs` | GET | Live logs (`?level=INFO&since=<unix>&n=100`) |
| `/api/logs/history` | GET | Historical logs from disk (`?hours=6`) |

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
| `/data/switch_stats.json` | Per-channel switch tuning + zap/start statistics |
| `/data/tmdb_cache.json` | TMDB poster cache |
| `/data/tvdb_cache.json` | TVDB series cache |
| `/data/logos/` | Channel logo cache |
| `/data/custom_logos/` | Manually set favorite logos (uploaded/URL, PNG-converted) |

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
