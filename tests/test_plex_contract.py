"""HTTP-level regression checks for the HDHomeRun and XMLTV contract."""
import http.server
import json
import threading
import unittest
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from unittest.mock import patch

from e2proxy import epg, hdhr
from e2proxy.http_proxy import ProxyHandler


class PlexLineupContractTest(unittest.TestCase):
    def test_lineup_and_xmltv_stay_consistent_after_favorite_changes(self):
        refs = {
            "Deluxe Music": "1:0:1:100:200:300:400000:0:0:0:",
            "RTL Nitro": "1:0:1:101:200:300:400000:0:0:0:",
            "New Channel": "1:0:1:102:200:300:400000:0:0:0:",
        }
        channels = [{"name": name, "ref": ref} for name, ref in refs.items()]
        favorites = channels[:2]
        events = {
            ref: [{"begin_timestamp": 1700000000, "duration_sec": 60,
                   "title": f"{name} programme"}]
            for name, ref in refs.items()
        }

        with patch.object(epg, "get_logo_for_epg", return_value=""), \
             patch.object(epg, "load_favorites", return_value=[]):
            xml = epg.build_merged_xmltv(channels, events, {}, refs, fetch_tmdb=False)

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
        server.daemon_threads = True
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}"

        def get(path):
            with urllib.request.urlopen(base + path, timeout=5) as response:
                self.assertEqual(response.status, 200)
                return response.read()

        try:
            with patch.object(hdhr, "get_channels", return_value=channels), \
                 patch.object(hdhr, "get_favorites", side_effect=lambda: favorites), \
                 patch.object(hdhr, "get_proxy_host", return_value="127.0.0.1"), \
                 patch.object(hdhr, "get_proxy_port", return_value=server.server_port), \
                 patch("e2proxy.http_proxy.get_epg_xml", return_value=xml):
                xmltv = ET.fromstring(get("/epg.xml"))
                xml_channels = {
                    c.findtext("display-name"): c.get("id")
                    for c in xmltv.findall("channel")
                }
                programmes = {
                    p.findtext("title"): p.get("channel")
                    for p in xmltv.findall("programme")
                }

                def check_lineups(expected):
                    regular = json.loads(get("/lineup.json"))
                    plex = json.loads(get("/plex/lineup.json"))
                    self.assertEqual(regular, plex)
                    self.assertEqual([row["GuideName"] for row in regular], expected)
                    for row in regular:
                        name = row["GuideName"]
                        self.assertEqual(row["GuideNumber"], xml_channels[name])
                        self.assertEqual(row["GuideNumber"], programmes[f"{name} programme"])
                        parsed = urllib.parse.urlsplit(row["URL"])
                        self.assertEqual(parsed.path, "/stream")
                        self.assertEqual(urllib.parse.parse_qs(parsed.query),
                                         {"ref": [refs[name]], "profile": ["plex"]})
                    return {row["GuideName"]: row["GuideNumber"] for row in regular}

                before = check_lineups(["Deluxe Music", "RTL Nitro"])
                favorites[:] = [channels[2], channels[1], channels[0]]
                after = check_lineups(["New Channel", "RTL Nitro", "Deluxe Music"])
                self.assertEqual(before["Deluxe Music"], after["Deluxe Music"])
                self.assertEqual(before["RTL Nitro"], after["RTL Nitro"])
                favorites[:] = [channels[0]]
                self.assertEqual(check_lineups(["Deluxe Music"])["Deluxe Music"],
                                 before["Deluxe Music"])
                favorites[:] = [channels[1], channels[0]]
                self.assertEqual(check_lineups(["RTL Nitro", "Deluxe Music"]), before)
        finally:
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
