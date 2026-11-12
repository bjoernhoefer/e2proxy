"""M3U playlist builder."""
import urllib.parse
import urllib.request

from e2proxy.config import get_config
from e2proxy.favorites import load_favorites
from e2proxy.logos import get_channel_logo

# ── M3U Builder ───────────────────────────────────────────

def build_m3u(channels, profile_name, favorites_only=False):
    cfg = get_config()
    proxy_host = cfg.get("proxy_host", "127.0.0.1")
    proxy_port = int(cfg.get("proxy_port", 8888))

    dp = cfg.get("device_profiles", {}).get(profile_name, {})
    tp_name = dp.get("transcode_profile", "webm-sd")
    tp = cfg.get("transcode_profiles", {}).get(tp_name, {})
    label = tp.get("label", tp_name)

    lines = [f'#EXTM3U x-tvg-url="http://{proxy_host}:{proxy_port}/epg.xml"']

    if favorites_only:
        favs = load_favorites()
        fav_map = {f["ref"]: f for f in favs}
        ch_map = {ch["ref"]: ch for ch in channels}
        ch_list = [(i+1, ch_map[f["ref"]], f) for i, f in enumerate(favs) if f["ref"] in ch_map]
    else:
        ch_list = [(i+1, ch, {}) for i, ch in enumerate(channels)]

    for chno, ch, fav in ch_list:
        ref_enc = urllib.parse.quote(ch["ref"])
        name_enc = urllib.parse.quote(ch["name"])
        name = ch["name"]
        # Gruppe: custom group aus Favoriten > Bouquet vom Receiver
        group = fav.get("group", "") or ch.get("bouquet", "TV")
        # Logo aus Datenbank
        logo = get_channel_logo(name)
        # tvg-id muss exakt der channel id im XMLTV entsprechen
        tvg_id = ch["ref"].rstrip("/").replace(":", "_")
        stream_url = (f'http://{proxy_host}:{proxy_port}/stream'
                      f'?ref={ref_enc}&profile={urllib.parse.quote(profile_name)}&name={name_enc}')
        logo_attr = f' tvg-logo="{logo}"' if logo else ''
        lines.append(
            f'#EXTINF:-1 tvg-chno="{chno}" tvg-id="{tvg_id}" tvg-name="{name}"{logo_attr} group-title="{group}",{name}'
        )
        lines.append(f'#KODIPROP:inputstream.ffmpegdirect.is_realtime_stream=true')
        lines.append(f'#KODIPROP:inputstream=inputstream.ffmpegdirect')
        lines.append(f'#KODIPROP:mimetype=video/mp2t')
        lines.append(stream_url)

    return "\n".join(lines)


