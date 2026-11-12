"""SSDP discovery responder (Plex finds e2proxy automatically)."""
import socket
import struct
import threading

from e2proxy.logs import log
from e2proxy.config import get_proxy_host, get_proxy_port
from e2proxy.hdhr import HDHR_DEVICE_ID

# ── SSDP Discovery Server ─────────────────────────────────

SSDP_MCAST_ADDR = "239.255.255.250"
SSDP_PORT       = 1900

def start_ssdp_server():
    """
    Horcht auf UDP Multicast Port 1900 auf M-SEARCH Pakete von Plex.
    Antwortet mit LOCATION die auf device.xml auf dem e2proxy Port zeigt.
    Plex findet damit den Proxy automatisch ohne manuelle IP-Eingabe.
    Läuft als Daemon-Thread — kein root nötig (Port 1900 > 1024).
    """
    host     = get_proxy_host()
    port     = get_proxy_port()
    location = f"http://{host}:{port}/device.xml"
    usn      = f"uuid:e2proxy-{HDHR_DEVICE_ID}::upnp:rootdevice"

    ssdp_response = (
        "HTTP/1.1 200 OK\r\n"
        "CACHE-CONTROL: max-age=1800\r\n"
        "EXT:\r\n"
        f"LOCATION: {location}\r\n"
        "SERVER: Linux/1.0 UPnP/1.0 e2proxy/1.0\r\n"
        "ST: upnp:rootdevice\r\n"
        f"USN: {usn}\r\n"
        "\r\n"
    ).encode("utf-8")

    def _serve():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except AttributeError:
                pass  # SO_REUSEPORT nicht auf allen Systemen verfügbar

            sock.bind(("", SSDP_PORT))

            # Multicast Gruppe beitreten
            mreq = struct.pack("4sL",
                socket.inet_aton(SSDP_MCAST_ADDR),
                socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

            log.info(f"SSDP: Horche auf {SSDP_MCAST_ADDR}:{SSDP_PORT} — LOCATION: {location}")

            while True:
                try:
                    data, addr = sock.recvfrom(4096)
                    msg = data.decode("utf-8", errors="ignore")

                    # Nur auf M-SEARCH antworten die nach rootdevice oder all suchen
                    if "M-SEARCH" not in msg:
                        continue
                    if not any(st in msg for st in ("ssdp:all", "upnp:rootdevice")):
                        continue

                    log.debug(f"SSDP: M-SEARCH von {addr[0]} — antworte mit {location}")
                    sock.sendto(ssdp_response, addr)

                except Exception as e:
                    log.warning(f"SSDP: Fehler beim Empfangen: {e}")

        except OSError as e:
            log.error(f"SSDP: Konnte Port {SSDP_PORT} nicht binden: {e}")
            log.error("SSDP: Automatische Discovery deenabled — manuelle IP-Eingabe in Plex verwenden")

    t = threading.Thread(target=_serve, daemon=True, name="ssdp-server")
    t.start()
    return t


