"""Plex integration: library refresh and DVR guide reload."""
import json
import os
import time

from e2proxy.logs import log
from e2proxy.config import get_proxy_host, get_proxy_port
from e2proxy.hdhr import HDHR_DEVICE_ID

def _plex_sections_list(rcfg):
    """Konfigurierte Section-IDs als Liste (kommagetrennte Werte werden gesplittet)."""
    raw = rcfg.get("plex_section", "") or ""
    return [s.strip() for s in raw.split(",") if s.strip()]


def _plex_refresh(rcfg):
    """Benachrichtigt Plex über neue/geänderte Aufnahme.

    Triggert einen Library-Scan. Unterstützt mehrere (kommagetrennte) Section-IDs
    — jede Section wird einzeln aktualisiert. Ohne Section wird die gesamte
    Bibliothek gescannt.
    """
    url = rcfg.get("plex_url", "").rstrip("/")
    token = rcfg.get("plex_token", "")
    if not url or not token:
        return
    import urllib.request as _ur
    import urllib.parse as _up
    sections = _plex_sections_list(rcfg)
    targets = sections if sections else ["all"]
    for section in targets:
        try:
            req_url = f"{url}/library/sections/{section}/refresh?X-Plex-Token={_up.quote(token)}"
            req = _ur.Request(req_url, method="GET",
                              headers={"User-Agent": "e2proxy/1.0"})
            with _ur.urlopen(req, timeout=8) as r:
                log.info(f"Plex refresh section={section}: {r.status}")
        except Exception as e:
            log.warning(f"Plex refresh section={section} failed: {e}")


def _plex_dvr_refresh_guide(rcfg):
    """Stößt in Plex einen Guide-Reload für den e2proxy-DVR an.

    Anders als _plex_refresh (das eine Media-Library scannt) aktualisiert das
    die EPG-/Guide-Daten des DVR-Geräts. Wird typischerweise nach einem
    EPG-Run aufgerufen, damit Plex die neuen Programminformationen zieht.

    Es werden nur DVRs angesprochen, die zum e2proxy-Gerät gehören (Match über
    Device-UUID/URI bzw. den HDHomeRun-DeviceID). Fremde DVRs bleiben unberührt.
    """
    url = rcfg.get("plex_url", "").rstrip("/")
    token = rcfg.get("plex_token", "")
    if not url or not token:
        return
    import urllib.request as _ur
    import urllib.parse as _up

    def _plex_json(req_url):
        req = _ur.Request(req_url, headers={"Accept": "application/json",
                                            "User-Agent": "e2proxy/1.0"})
        with _ur.urlopen(req, timeout=12) as r:
            return json.loads(r.read())

    tok_q = _up.quote(token)
    try:
        data = _plex_json(f"{url}/livetv/dvrs?X-Plex-Token={tok_q}")
    except Exception as e:
        log.warning(f"Plex DVR-Liste abrufen fehlgeschlagen: {e}")
        return

    dvrs = data.get("MediaContainer", {}).get("Dvr", []) or []
    if not dvrs:
        log.info("Plex Guide-Refresh: keine DVRs in Plex gefunden")
        return

    device_id = HDHR_DEVICE_ID.lower()
    host = str(get_proxy_host()).lower()
    port = str(get_proxy_port())
    matched = []
    for dvr in dvrs:
        blob = json.dumps(dvr).lower()
        if device_id in blob or "e2proxy" in blob or (host in blob and port in blob):
            key = dvr.get("key")
            if key is not None:
                matched.append(str(key))

    if not matched:
        log.info("Plex Guide-Refresh: kein e2proxy-DVR gefunden — übersprungen")
        return

    for key in matched:
        try:
            req_url = f"{url}/livetv/dvrs/{key}/reloadGuide?X-Plex-Token={tok_q}"
            req = _ur.Request(req_url, method="POST",
                              headers={"User-Agent": "e2proxy/1.0"})
            with _ur.urlopen(req, timeout=15) as r:
                log.info(f"Plex Guide-Refresh DVR={key}: {r.status}")
        except Exception as e:
            log.warning(f"Plex Guide-Refresh DVR={key} fehlgeschlagen: {e}")


def _plex_find_file(rcfg, filepath):
    """Prüft, ob eine Datei in Plex indexiert ist.

    Vergleicht den absoluten Pfad bzw. – als Fallback – den Dateinamen mit den
    Media-Parts der konfigurierten Sections (oder allen Sections, falls keine
    konfiguriert ist). Liefert True bei einem Treffer.
    """
    url = rcfg.get("plex_url", "").rstrip("/")
    token = rcfg.get("plex_token", "")
    if not url or not token:
        return False
    import urllib.request as _ur
    import urllib.parse as _up

    def _get_json(req_url):
        req = _ur.Request(req_url, headers={"Accept": "application/json",
                                            "User-Agent": "e2proxy/1.0"})
        with _ur.urlopen(req, timeout=12) as r:
            return json.loads(r.read())

    target_abs = os.path.abspath(filepath)
    target_base = os.path.basename(filepath)

    # Section-IDs + Typ ermitteln
    wanted = set(_plex_sections_list(rcfg))
    try:
        meta = _get_json(f"{url}/library/sections?X-Plex-Token={_up.quote(token)}")
        directories = meta.get("MediaContainer", {}).get("Directory", [])
    except Exception as e:
        log.debug(f"Plex sections list failed: {e}")
        return False

    for d in directories:
        sid = str(d.get("key", ""))
        if wanted and sid not in wanted:
            continue
        # Leaf-Typ je Library: movie=1, show→episode=4; sonst beide probieren
        stype = d.get("type", "")
        leaf_types = ["1"] if stype == "movie" else ["4"] if stype == "show" else ["1", "4"]
        for lt in leaf_types:
            try:
                q = _up.urlencode({"X-Plex-Token": token, "type": lt})
                data = _get_json(f"{url}/library/sections/{sid}/all?{q}")
            except Exception as e:
                log.debug(f"Plex find section={sid} type={lt}: {e}")
                continue
            for item in data.get("MediaContainer", {}).get("Metadata", []):
                for media in item.get("Media", []):
                    for part in media.get("Part", []):
                        pf = part.get("file", "")
                        if not pf:
                            continue
                        if os.path.abspath(pf) == target_abs or os.path.basename(pf) == target_base:
                            return True
    return False


def _plex_notify(rcfg, filepath=None, label="Aufnahme"):
    """Stößt einen Plex-Scan an und verifiziert optional, dass die Datei ankam.

    Ist die Verifikation aktiviert (recordings_plex_verify) und ein filepath
    gegeben, wird nach dem Refresh wiederholt geprüft, ob die Datei in Plex
    auftaucht — inklusive erneutem Refresh-Versuch. Das Ergebnis wird geloggt.
    """
    if not (rcfg.get("plex_url") and rcfg.get("plex_token")):
        return
    _plex_refresh(rcfg)
    if not rcfg.get("plex_verify") or not filepath:
        return
    base = os.path.basename(filepath)
    deadline = time.time() + 120
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            if _plex_find_file(rcfg, filepath):
                log.info(f"Plex verify OK ({label}): '{base}' indexiert (Versuch {attempt})")
                return
        except Exception as e:
            log.debug(f"Plex verify attempt {attempt}: {e}")
        time.sleep(8)
        if attempt % 3 == 0:
            # Scan erneut anstoßen, falls der erste nicht griff
            _plex_refresh(rcfg)
    log.warning(
        f"Plex verify FAILED ({label}): '{base}' nach 120s nicht in Plex gefunden — "
        f"Pfad-Mapping/Library prüfen (Plex muss denselben Pfad sehen)"
    )

