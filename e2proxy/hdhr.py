"""HDHomeRun emulation for Plex DVR (discover/lineup/device.xml)."""
import urllib.parse
import urllib.request

from e2proxy.config import get_proxy_host, get_proxy_port, get_receivers, is_receiver_usable
from e2proxy.favorites import get_favorites
from e2proxy.channels import get_channels

# ── HDHomeRun Emulation ───────────────────────────────────

HDHR_DEVICE_ID   = "E2PROXY1"
HDHR_DEVICE_AUTH = "e2proxy"
HDHR_FIRMWARE    = "20200521"

def build_hdhr_discover():
    """discover.json — Plex fragt das beim Setup ab."""
    host = get_proxy_host()
    port = get_proxy_port()
    base = f"http://{host}:{port}"
    tuner_count = len([r for r in get_receivers() if is_receiver_usable(r)])
    return {
        "FriendlyName":    "e2proxy (Enigma2)",
        "Manufacturer":    "e2proxy",
        "ModelNumber":     "HDHR4-2DT",
        "FirmwareName":    "hdhomerun4_dvbt",
        "FirmwareVersion": HDHR_FIRMWARE,
        "DeviceID":        HDHR_DEVICE_ID,
        "DeviceAuth":      HDHR_DEVICE_AUTH,
        "TunerCount":      tuner_count,
        "BaseURL":         base,
        "LineupURL":       f"{base}/lineup.json",
    }

def ref_to_channel_id(ref):
    """
    Konvertiert Enigma2 Service-Ref in EPG Channel-ID Format.
    '1:0:19:283D:3FB:1:C00000:0:0:0:' → '1_0_19_283D_3FB_1_C00000_0_0_0_'
    Plex verwendet diese ID als Tuning-Adresse und matcht damit die EPG Channel-ID.
    """
    return ref.replace(":", "_")

def _build_hdhr_lineup_for_profile(profile_name):
    """Interne Hilfsfunktion: lineup.json fuer ein bestimmtes Profil."""
    host = get_proxy_host()
    port = get_proxy_port()
    channels = get_channels()
    lineup = []
    for ch in channels:
        ref_enc = urllib.parse.quote(ch["ref"], safe="")
        channel_id = ref_to_channel_id(ch["ref"])
        stream_url = (
            f"http://{host}:{port}/stream"
            f"?ref={ref_enc}&profile={urllib.parse.quote(profile_name)}"
        )
        lineup.append({
            "GuideNumber": channel_id,
            "GuideName":   ch["name"],
            "URL":         stream_url,
        })
    return lineup

def build_hdhr_lineup():
    """lineup.json — liefert Favoriten mit profile=plex.
    Plex folgt immer der LineupURL aus discover.json, daher
    muss dieser Endpoint direkt die richtigen Daten liefern.
    """
    return build_hdhr_lineup_plex()

def build_hdhr_lineup_plex():
    """lineup.json fuer den /plex Endpoint.
    Nur Favoriten — verhindert verschluesselte Sender und reduziert Liste.
    Verwendet profile=plex fuer chunked HTTP/1.1 Streaming.
    """
    host = get_proxy_host()
    port = get_proxy_port()
    # Nur Favoriten
    favs = get_favorites()
    fav_refs = {f["ref"] for f in favs}
    channels = [ch for ch in get_channels() if ch["ref"] in fav_refs]
    # Reihenfolge aus Favoriten-Liste beibehalten
    fav_order = {f["ref"]: i for i, f in enumerate(favs)}
    channels.sort(key=lambda ch: fav_order.get(ch["ref"], 9999))
    lineup = []
    for idx, ch in enumerate(channels, start=1):
        ref_enc = urllib.parse.quote(ch["ref"], safe="")
        stream_url = (
            f"http://{host}:{port}/stream"
            f"?ref={ref_enc}&profile=plex"
        )
        lineup.append({
            "GuideNumber": str(idx),   # Numerisch → Plex sortiert in unserer Reihenfolge
            "GuideName":   ch["name"],
            "URL":         stream_url,
        })
    return lineup

def build_hdhr_lineup_status():
    """lineup_status.json — Plex prüft damit ob das Gerät bereit ist."""
    return {
        "ScanInProgress": 0,
        "ScanPossible":   0,
        "Source":         "Cable",
        "SourceList":     ["Cable"],
    }

def build_hdhr_device_xml():
    """device.xml — UPnP Capability-Beschreibung."""
    host = get_proxy_host()
    port = get_proxy_port()
    base = f"http://{host}:{port}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <URLBase>{base}</URLBase>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>
    <friendlyName>e2proxy (Enigma2)</friendlyName>
    <manufacturer>e2proxy</manufacturer>
    <modelName>HDHR4-2DT</modelName>
    <modelNumber>HDHR4-2DT</modelNumber>
    <serialNumber>{HDHR_DEVICE_ID}</serialNumber>
    <UDN>uuid:e2proxy-{HDHR_DEVICE_ID}</UDN>
  </device>
</root>"""


