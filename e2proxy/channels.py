"""Channel list cache, fetched from the Enigma2 receivers' bouquets."""
import re
import threading
import time
import urllib.parse
import urllib.request

from e2proxy.logs import log
from e2proxy.config import get_receivers

# ── Channel Cache ─────────────────────────────────────────

channel_cache = {"channels": [], "last_update": 0}
channel_cache_lock = threading.Lock()

BOUQUETS = [
    "userbouquet.Free_TV.tv",
    "userbouquet.Free_HDTV_und_HDplus.tv",
    "userbouquet.Oesterreich_TV.tv",
    "userbouquet.Schweizer_TV.tv",
    "userbouquet.Sport_Sender.tv",
    "userbouquet.Kinder_Sender.tv",
    "userbouquet.Musik_Sender.tv",
    "userbouquet.UHD_TV.tv",
    "userbouquet.MTV.tv",
    "userbouquet.Terrestrisch.tv",
]

def fetch_channels_from_receiver():
    receivers = get_receivers()
    if not receivers:
        return []
    r = receivers[0]
    channels = []
    seen_refs = set()

    for bouquet in BOUQUETS:
        try:
            bref = f'1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "{bouquet}" ORDER BY bouquet'
            encoded = urllib.parse.quote(bref)
            url = f"http://{r['ip']}:{r['port']}/web/services.m3u?bRef={encoded}"
            with urllib.request.urlopen(url, timeout=10) as resp:
                content = resp.read().decode("utf-8", errors="replace")

            lines = content.splitlines()
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if line.startswith("#EXTINF"):
                    name_match = re.search(r',(.+)$', line)
                    name = name_match.group(1).strip() if name_match else "Unbekannt"
                    j = i + 1
                    while j < len(lines) and lines[j].strip().startswith("#"):
                        j += 1
                    if j < len(lines):
                        url_line = lines[j].strip()
                        ref_match = re.search(r':8001/(.+)$', url_line)
                        if ref_match:
                            ref = ref_match.group(1).rstrip('/')
                            if ref not in seen_refs and name and not name.startswith('.'):
                                seen_refs.add(ref)
                                channels.append({
                                    "name": name,
                                    "ref": ref,
                                    "bouquet": bouquet.replace("userbouquet.", "").replace(".tv", "")
                                })
                    i = j + 1
                    continue
                i += 1
        except Exception as e:
            log.warning(f"Bouquet {bouquet} Fehler: {e}")

    log.info(f"Channel list: {len(channels)} channels loaded")
    return channels


def get_channels(force_refresh=False):
    with channel_cache_lock:
        age = time.time() - channel_cache["last_update"]
        if force_refresh or age > 3600 or not channel_cache["channels"]:
            log.info("Updating channel list...")
            try:
                channels = fetch_channels_from_receiver()
                channel_cache["channels"] = channels
                channel_cache["last_update"] = time.time()
            except Exception as e:
                log.error(f"Senderliste Fehler: {e}")
        return channel_cache["channels"]


