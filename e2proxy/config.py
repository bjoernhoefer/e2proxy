"""Configuration store (config.json) and typed accessors."""
import copy
import json
import os
import threading

from e2proxy.paths import CONFIG_DEFAULT, CONFIG_FILE
from e2proxy.logs import log

# ── Config Management ─────────────────────────────────────

config_lock = threading.Lock()
_config = {}

def load_config():
    global _config
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                _config = json.load(f)
                log.info(f"Config loaded: {CONFIG_FILE}")
                return
    except Exception as e:
        log.warning(f"Config laden fehlgeschlagen: {e}")
    # Default laden
    try:
        with open(CONFIG_DEFAULT, 'r') as f:
            _config = json.load(f)
        log.info("Default config loaded")
        save_config()
    except Exception as e:
        log.error(f"Default-Config laden fehlgeschlagen: {e}")
        _config = {"receivers": [], "transcode_profiles": {}, "device_profiles": {}}

def save_config():
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(_config, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        log.error(f"Config speichern fehlgeschlagen: {e}")
        return False

def get_config():
    with config_lock:
        return copy.deepcopy(_config)

def update_config(new_config):
    global _config
    with config_lock:
        _config = new_config
        return save_config()

def get_receivers():
    return get_config().get("receivers", [])

def get_receiver_by_id(rid):
    for r in get_receivers():
        if r["id"] == rid:
            return r
    return None

def is_receiver_locked(r):
    """True, wenn der Tuner in den Einstellungen gesperrt ist."""
    return bool(r.get("locked", False))

def is_receiver_usable(r):
    """Receiver darf vom e2proxy verwendet werden: aktiviert und nicht gesperrt."""
    return r.get("enabled", True) and not r.get("locked", False)

def get_transcode_profile(name):
    return get_config().get("transcode_profiles", {}).get(name)

def get_device_profile(name):
    return get_config().get("device_profiles", {}).get(name)

def get_proxy_host():
    return get_config().get("proxy_host", "127.0.0.1")

def get_proxy_port():
    return int(get_config().get("proxy_port", 8888))

def get_zap_wait():
    return float(get_config().get("zap_wait_sec", 2.0))

def get_default_profile():
    return get_config().get("default_device_profile", "Web-SD")


def get_owif_config():
    """Konfiguration der OpenWebif-Emulation ('virtuelle Enigma2-Box').

    e2proxy imitiert auf einem eigenen Port eine Enigma2-Box, sodass Enigma2-
    Clients (z.B. Dream Player, Kodi) glauben, mit einer echten Box zu reden.
    Metadaten/EPG werden per Reverse-Proxy vom Receiver geliefert (authentisch),
    Streaming wird orchestriert (freier Tuner) und standardmäßig als rohes TS
    durchgereicht. Plex bleibt davon unberührt (eigener HDHomeRun-Pfad).

    ua_overrides: Liste von {"match": <substring>, "profile": <transcode-profil>}
    — case-insensitive User-Agent-Match wählt ein abweichendes Profil (falls ein
    Player kein rohes TS kann).
    """
    c = get_config().get("openwebif_emulation", {}) or {}
    overrides = []
    for ov in c.get("ua_overrides", []) or []:
        if isinstance(ov, dict) and ov.get("match") and ov.get("profile"):
            overrides.append({"match": str(ov["match"]), "profile": str(ov["profile"])})
    # Enigma2-Standardports: OpenWebif :80, Streaming :8001 — für eine 1:1-
    # Imitation. "port" (Alt-Schlüssel) wird als webif_port akzeptiert.
    webif_port = int(c.get("webif_port", c.get("port", 80)))
    stream_port = int(c.get("stream_port", 8001))
    # Aufnahmen: Dream Player erwartet die Movielist auf einem zweiten
    # OpenWebif-Web-Port (Enigma-Standard-Alt-Port 81). 0 = deaktiviert.
    recordings_port = int(c.get("recordings_port", 81))
    return {
        "enabled": bool(c.get("enabled", False)),
        "bind": c.get("bind", "0.0.0.0"),
        "webif_port": webif_port,
        "stream_port": stream_port,
        "recordings_port": recordings_port,
        "recordings_enabled": bool(c.get("recordings_enabled", True)),
        "default_profile": c.get("default_profile", "pass"),
        "metadata_receiver": c.get("metadata_receiver", "auto"),
        "ua_overrides": overrides,
    }


