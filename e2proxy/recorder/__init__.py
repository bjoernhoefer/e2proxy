"""Optional series recorder (formerly the standalone e2recorder).

Enable with ``"recorder": {"enabled": true}`` in config.json or in
Settings → Recorder. Data lives in ``<DATA_DIR>/recorder/``.
"""
import os

from e2proxy import extensions
from e2proxy.config import get_config, get_proxy_port, update_config
from e2proxy.paths import DATA_DIR
from e2proxy.recorder.engine import DEFAULTS, Recorder

_instance = None


def settings():
    return {**DEFAULTS, **(get_config().get("recorder") or {})}


def is_enabled():
    return bool(settings().get("enabled"))


def save_settings(changes):
    """Merge validated keys into config['recorder']; returns the new settings."""
    cur = settings()
    for k, default in DEFAULTS.items():
        if k not in changes:
            continue
        v = changes[k]
        if isinstance(default, bool):
            cur[k] = bool(v)
        elif isinstance(default, int):
            cur[k] = max(0, int(v))
        elif isinstance(default, list):
            cur[k] = [str(x).strip().rstrip("/") for x in (v or []) if str(x).strip()]
        else:
            cur[k] = str(v or "").strip()
    cfg = get_config()
    cfg["recorder"] = cur
    update_config(cfg)
    if cur["enabled"]:
        extensions.start_all()
    return cur


def get():
    global _instance
    if _instance is None:
        _instance = Recorder(
            data_dir=os.path.join(DATA_DIR, "recorder"),
            settings=settings,
            local_url=lambda: f"http://127.0.0.1:{get_proxy_port()}",
        )
    return _instance


extensions.register(extensions.Extension(
    name="recorder", enabled=is_enabled, start=lambda: get().start()))

from e2proxy.recorder import routes  # noqa: E402,F401  (registers the API routes)
