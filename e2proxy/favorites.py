"""Favorite channels (favorites.json)."""
import json
import os
import threading

from e2proxy.paths import FAVORITES_FILE
from e2proxy.logs import log

# ── Favorites Management ──────────────────────────────────

favorites_lock = threading.Lock()

def load_favorites():
    try:
        if os.path.exists(FAVORITES_FILE):
            with open(FAVORITES_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        log.warning(f"Favoriten laden fehlgeschlagen: {e}")
    return []

def save_favorites(favs):
    try:
        os.makedirs(os.path.dirname(FAVORITES_FILE), exist_ok=True)
        with open(FAVORITES_FILE, 'w') as f:
            json.dump(favs, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        log.error(f"Favoriten speichern fehlgeschlagen: {e}")
        return False

def get_favorites():
    with favorites_lock:
        return load_favorites()


