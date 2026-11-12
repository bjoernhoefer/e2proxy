"""Filesystem locations of persistent data (overridable via E2PROXY_DATA_DIR)."""
import os



# ── Pfade ─────────────────────────────────────────────────
# Datenpfad: via Env-Variable überschreibbar (für Docker)
DATA_DIR       = os.environ.get("E2PROXY_DATA_DIR", "/var/lib/e2proxy")
CONFIG_FILE    = f"{DATA_DIR}/config.json"
FAVORITES_FILE = f"{DATA_DIR}/favorites.json"

CONFIG_DEFAULT = f"{DATA_DIR}/config_default.json"
