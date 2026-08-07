# e2proxy

**Enigma2 Streaming Proxy** — Stream satellite TV from Enigma2 receivers to any browser, Plex, Jellyfin, or Kodi.

Single Python file, runs in Docker, manages two SAT receivers with automatic tuner allocation, EPG, recordings, and Plex DVR integration.

## Features

- **Live TV Streaming** — Watch any channel in the browser (WebM VP8/VP9) or via M3U playlist
- **OpenWebif Emulation** — Impersonates an Enigma2 box on standard ports (OpenWebif `:80`, streaming `:8001`, recordings `:81`) so native Enigma2 apps like **Dream Player** (Android/Google TV) or Kodi can point straight at e2proxy. Metadata/EPG are served authentically from the receiver, live streaming is orchestrated onto a free tuner and passed through as raw TS by default (no ffmpeg), and your recordings show up in the app's Recordings tab with full seeking
- **Plex DVR Integration** — HDHomeRun emulation with SSDP auto-discovery, no Threadfin needed
- **EPG Browser** — 28-hour program guide as interactive timeline grid with TMDB artwork
- **Recording System** — ffmpeg-based with Plex-compliant directory structure (`TV/Show/Season XX/`)
- **TVDB + TMDB** — Automatic series/episode detection, daily-show fallback numbering
- **Shared Tuner** — Parallel recordings on the same channel without tuner conflicts
- **Fast Switching** — Per-channel switch tuning (NoLatency probing, configurable zap wait) with self-learning probesize and zap/start statistics
- **Switch Analysis** — An *Analyze* button turns the collected zap/start statistics into concrete per-channel tuning suggestions (tighten flaky channels, loosen reliable ones), shown as a preview before you apply them
- **Usage Report** — Aggregated statistics on which device streams most, which channel is watched most, which programmes are worth recording, and which interfaces are used
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
6. **Plex Integration** — URL + token for automatic library refresh after recordings, and optional DVR guide reload after each EPG run

All configuration stored in `/data/config.json`.

## Web UI

| Page | URL | Description |
|------|-----|-------------|
| Main | `/` | Channel list, player, tuner status |
| EPG Browser | `/epg-browser` | Interactive timeline with recording buttons |
| Favorites | `/favorites` | Drag & drop channel ordering |
| Settings | `/settings` | Configuration, maintenance, EPG, recordings, usage report |
| Help | `/help` | Feature overview, API reference, changelog |

### Switch analysis & usage report

The Settings page has a **📊 Auswertung / Usage** tab combining two things.

**Switch tuning analysis.** Every zap and stream start is recorded as ok/fail per channel.
The *Analyze* button turns that history into concrete suggestions — tighten channels that
fail too often (more zap wait, larger probesize, or NoLatency off), loosen channels that are
provably reliable. Suggestions are shown as a preview table and only written when you apply
them.

Rather than a fixed failure-rate threshold, the heuristic uses a **Wilson score interval**,
so the sample size decides how much a rate is trusted: 1 failure out of 20 (lower bound 0.9 %)
does *not* trigger, 2 out of 20 (lower bound 2.8 %) does. A channel needs at least 10 starts
resp. 10 zaps before any rule fires, and zap rules and start rules are gated separately —
otherwise a channel with many zaps but few starts triggers a start rule on a 3-sample basis.
Because a channel you watch often will accumulate more failures in absolute terms, exposure
(watch sessions, watch time, failures per hour) is reported alongside the rate.

Note that a perfectly reliable channel produces *no* suggestion when the global config is
already at its most aggressive — there is simply nothing left to loosen.

**Usage report.** Stream sessions are aggregated into a persistent store (`usage_stats.json`)
answering: which device streams most, which channel is watched most, which programmes are
watched repeatedly (i.e. worth a recording timer), and which interfaces (proxy, OpenWebif,
Plex DVR, EPG, API) are actually used, plus a distribution over hours and weekdays. Programme
titles are resolved from the EPG cache at the time of viewing.

### WebUI internals (self-check + extraction boundary)

All browser-facing code lives in one clearly delimited block in `e2proxy.py`, between
the `# === WEBUI ===` / `# === END WEBUI ===` markers: the i18n engine (`I18N_JS`), base
stylesheet (`CSS_BASE`), the shared page shell (`html_page`), and the page builders
(`build_web_ui`, `build_help_ui`, `build_favorites_ui`, `build_epg_browser`,
`build_settings_ui`). The block header documents the exact external symbols it depends
on, so it can later be lifted into its own service/module as a clean cut. Keep new UI
code inside these markers.

Because the UI markup/JS is embedded in Python f-strings, a stray escape (e.g. a literal
`\n` inside a `'...'` JS string) can silently corrupt a whole `<script>` block and break
every tab/button on a page. A built-in **self-check** guards against this:

```bash
# Validate all pages' embedded JavaScript without starting the server
python3 e2proxy.py --selfcheck   # exit 0 = OK, exit 1 = broken (use as a pre-deploy gate)
```

It renders every page and reports four classes of defect that have each caused a dead UI
before:

| Check | Catches |
|-------|---------|
| unterminated string literals | a `'…`/`"…` that runs into a newline |
| nested/escaped quotes | `onclick="f('a', 'b\'c')"` — string followed by a bare identifier |
| unbalanced `()`, `[]`, `{}` | a `function x() {` declaration line lost while editing |
| unresolved references | `escHtml(…)` called on a page that never defines it, and inline `onclick="foo()"` handlers with no `foo` |

The last class is the important one: such code is *syntactically perfect* and only fails at
runtime, so a parser alone never sees it. String, template, regex and comment content is
masked out first (`${…}` inside template literals stays visible, since it is real code), then
all declarations — `function`, `class`, `const/let/var`, parameter lists, arrow parameters,
`catch (e)`, destructuring and `window.X =` — are collected and compared against every call
site. Browser globals are allow-listed. Note that helper availability differs per page
(`escHtml` exists on the main page but not on Settings), which is exactly what this catches.

The same check runs automatically at startup and logs `WebUI self-check: OK` (or a loud
`ERROR` listing the offending page/script/line). Use it as a **deploy gate** so a broken
build can never replace a running one:

```bash
python3 -m py_compile e2proxy.py                    || exit 1   # Python syntax
E2PROXY_DATA_DIR=$(mktemp -d) python3 e2proxy.py --selfcheck || exit 1   # WebUI
cp e2proxy.py "$TARGET" && docker compose restart              # only now
```

Static analysis has limits — it finds missing functions and broken syntax, not logic errors
(e.g. a wrong API field name). A browser click-through remains the final stage.

## Versioning

- **Official version** (`VERSION`) follows major/minor and is only bumped when a Pull
  Request is opened.
- **Internal build id** (`INTERNAL_VERSION = <VERSION>+<branch>.<seq>`) uniquely identifies
  the deployed branch/state during testing. Bump `BUILD_SEQ` on every test rollout; it is
  reported by `/api/version`, printed in the startup log **and shown in the UI** (Settings →
  About, and the Help page header) so you always know exactly which build is running on a host.

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
| `/api/switch/analyze` | POST | Derive tuning suggestions from the collected statistics. `{"apply": false}` returns a preview, `{"apply": true}` writes the per-channel overrides |
| `/api/usage/stats` | GET | Aggregated usage report (devices, channels, programmes, interfaces, hours/weekdays) |
| `/api/usage/reset` | POST | Clear the usage store |
| `/api/logs` | GET | Live logs (`?level=INFO&since=<unix>&n=100`) |
| `/api/logs/history` | GET | Historical logs from disk (`?hours=6`) |
| `/api/diag` | GET | Stream diagnostics: status + live per-stream counters; `?on=1`/`?on=0` toggles verbose mode |

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

## OpenWebif Emulation (Dream Player, Kodi & other Enigma2 apps)

e2proxy can impersonate an Enigma2 receiver on its **standard ports** so any native Enigma2 client — most notably **[Dream Player](https://dreamepg.de) for Android/Google TV** — can talk to e2proxy as if it were a real box, including full EPG.

**How it works:**

- **Metadata & EPG** (`/api/*`, `/web/*`, `/picon/*`, …) are reverse-proxied to a real receiver, so the app sees authentic OpenWebif responses (device info, bouquets, EPG now/next/multi, picons). Every request is access-logged (with status + User-Agent) for easy diagnostics.
- **Bouquet list matches the web UI**: the top-level `getservices` **and** `getallservices` (full-sync) responses are curated and reordered to the same selection/order as the e2proxy web UI, so apps like Dream Player/dreamEPG don't show the receiver's full raw bouquet list. Channels within a bouquet keep the receiver's native order.
- **"Favoriten" bouquet with your arranged favorites**: a synthetic bouquet named *Favoriten* is injected as the **first** bouquet the apps see. It contains exactly the channels from your e2proxy favorites (`favorites.json`), in your drag-and-drop order — not the receiver's `favourites.tv`. Its channel list and EPG now/next are synthesized by e2proxy (per-service EPG merged from the receiver), so the apps show your curated favorites with correct programme info.
- **Streaming** (`/<serviceRef>`) is intercepted: e2proxy picks a **free tuner** (orchestration across receivers) and passes the **raw TS through unchanged** (passthrough, no ffmpeg) — fast and light, ideal for Dream Player/Kodi. Plex keeps using its own HDHomeRun path and is unaffected.
- **Recordings** (`/web/movielist`, `/api/movielist`, `/web/getlocations`, `/web/moviedelete`, `/web/moviedetails`) are served on the Enigma alt-web port `81` (Dream Player's Recordings tab). e2proxy builds an OpenWebif movie list from its own recordings directory (with title/plot/date/duration pulled from the `.nfo` files, and `e2length` formatted in the authentic OpenWebif `minutes:seconds` form) and streams the recording files back with HTTP Range support (seeking), again as raw TS. `getlocations`/`getcurrlocation` advertise the e2proxy recordings directory, and `moviedelete` removes the local e2recorder file (incl. `.nfo` and empty folders). The recordings live on the e2proxy/e2recorder storage, not on the box — so this exposes *your* recordings, not the (empty) box movie list.
- **Per-player profiles**: by default everything is passthrough. If a player can't handle raw TS, add a **User-Agent override** in Settings to force a remux/transcode profile for that player only.

**Enable** (Settings → OpenWebif Emulation, or `config.json`):

```json
"openwebif_emulation": {
  "enabled": true,
  "bind": "0.0.0.0",
  "webif_port": 80,
  "stream_port": 8001,
  "recordings_port": 81,
  "recordings_enabled": true,
  "default_profile": "pass",
  "metadata_receiver": "auto",
  "ua_overrides": [
    { "match": "ExoPlayer", "profile": "remux-ac3" }
  ]
}
```

> Standard ports `80` + `8001` (+ `81` for recordings) must be free on the host (e.g. disable any web server occupying port 80). Ports are configurable; a change requires a service restart. Set `recordings_port` to `0` to disable the recordings movie list.

**Dream Player setup:** add a device pointing at the e2proxy host IP, OpenWebif port `80`, streaming port `8001`, recordings/second web port `81`, no username/password. EPG, channels and recordings are pulled automatically.

## Companion: e2recorder

[e2recorder](https://github.com/bjoernhoefer/e2recorder) is the automated recording scheduler that works with e2proxy. It monitors the EPG and triggers recordings via the `/api/record/start` endpoint.

## Stream Diagnostics (Grafana Loki)

Live streams occasionally freeze for a minute or two. To make that analysable
after the fact, e2proxy writes structured `DIAG` lines (logfmt) that are shipped
to Grafana Cloud Loki by [Grafana Alloy](https://grafana.com/docs/alloy/).

### What is measured

Every streaming loop measures separately how long it waited for **upstream**
data (receiver / ffmpeg) and how long it waited for the **client** to accept
data. That distinguishes the two very different causes of a freeze:

| Field | Meaning |
|-------|---------|
| `read_wait_s` | time spent waiting for the receiver / ffmpeg |
| `write_wait_s` | time spent waiting for the player to read (backpressure) |
| `kind=upstream_no_data` | the tuner/transponder or ffmpeg stopped delivering |
| `kind=client_backpressure` | the player or the network stopped consuming |

A watchdog thread reports freezes **while they happen**, so a stream that never
recovers still shows up.

### Events

| Event | Level | When |
|-------|-------|------|
| `stream_start` / `stream_end` | INFO / WARN | stream opened / closed (summary: duration, MB, kbps, waits, stalls) |
| `stall_start` / `stall_ongoing` / `stall_end` | WARNING | no data for >3 s, still stalled (every 15 s), recovered |
| `ffmpeg_issue` | WARNING | classified ffmpeg message (`dts`, `ts_corrupt`, `cc_error`, `timeout`, `scrambled`, …) |
| `sample` | INFO | throughput sample every 15 s — **diagnostic mode only** |

Stall detection is always on (very low volume). The extra detail — ffmpeg at
`-loglevel verbose` plus throughput samples — is switched on demand:

```bash
curl "http://e2proxy:8888/api/diag?on=1"   # enable
curl "http://e2proxy:8888/api/diag?on=0"   # disable
curl "http://e2proxy:8888/api/diag"        # status + live stream counters
```

The same switch sits in *Settings → Stream-Diagnose*, together with a live view
of throughput, waits and stalls per active stream.

### Shipping to Grafana Cloud

`deploy/grafana/` contains the ready-made setup:

| File | Purpose |
|------|---------|
| `config.alloy` | Alloy config: metrics + Docker logs → Loki (drops SSDP noise, parses timestamp/level, strips the log prefix so `\| logfmt` works) |
| `docker-compose.alloy.yml` | Alloy container (Docker socket, persistent `--storage.path`) |
| `e2proxy-stream-diagnose.json` | Grafana dashboard — import via *Dashboards → New → Import* |

Required environment variables (e.g. in `alloy/.env`):

```
BJOERN01_USER=…            # Prometheus user id
BJOERN01_TOKEN=…           # token with metrics:write
BJOERN01_LOKI_USER=…       # Loki user id
BJOERN01_LOKI_TOKEN=…      # token with logs:write (and logs:read for querying)
BJOERN01_LOKI_URL=https://logs-prod-XXX.grafana.net/loki/api/v1/push
```

> Grafana Cloud access tokens keep the scopes they had when created — after
> changing an access policy you have to issue a **new** token.

`--storage.path=/var/lib/alloy` matters: without it Alloy forgets its read
positions on every restart and replays the whole container log, which Loki
rejects as "timestamp too old".

### Useful LogQL queries

```logql
# all freezes with cause
{container="e2proxy"} |= `DIAG event=stall_start` | logfmt

# longest freeze per channel
max by (channel) (max_over_time({container="e2proxy"} |= `DIAG event=stall_end` | logfmt | unwrap gap_s [5m]))

# throughput per stream (diagnostic mode)
avg by (channel, sid) (avg_over_time({container="e2proxy"} |= `DIAG event=sample` | logfmt | unwrap kbps [1m]))

# ffmpeg problems by type
sum by (kind) (count_over_time({container="e2proxy"} |= `DIAG event=ffmpeg_issue` | logfmt [5m]))
```

## Data Paths

| Path | Content |
|------|---------|
| `/data/config.json` | Configuration |
| `/data/favorites.json` | Favorite channels |
| `/data/e2proxy.log` | System log (daily rotation) |
| `/data/api_access.log` | API access log |
| `/data/epg_cache.xml` | EPG disk cache |
| `/data/switch_stats.json` | Per-channel switch tuning + zap/start statistics |
| `/data/usage_stats.json` | Aggregated usage report (devices, channels, programmes, interfaces) |
| `/data/tmdb_cache.json` | TMDB poster cache |
| `/data/tvdb_cache.json` | TVDB series cache |
| `/data/logos/` | Channel logo cache |
| `/data/custom_logos/` | Manually set favorite logos (uploaded/URL, PNG-converted) |

## Update

Never copy an unverified file over a running deployment — run the self-check first and
abort on failure, so a broken build cannot replace a working one:

```bash
scp e2proxy.py user@server:~/e2proxy.py

ssh user@server '
  set -e
  SC=$(mktemp -d)
  python3 -m py_compile ~/e2proxy.py
  E2PROXY_DATA_DIR="$SC" python3 ~/e2proxy.py --selfcheck
  cp ~/e2proxy/e2proxy.py ~/e2proxy/e2proxy.py.bak.$(date +%Y%m%d-%H%M%S)
  cp ~/e2proxy.py ~/e2proxy/e2proxy.py
  docker compose -f ~/e2proxy/docker-compose.e2proxy.yml restart
'

# Verify — the build id must match the one you deployed
curl -s http://server:8888/api/version
```

The build id is also visible in the UI under Settings → About and in the Help page header,
so you can confirm a rollout without shell access.

## License

MIT

## Credits

Built with [Claude](https://claude.ai) by Anthropic.
