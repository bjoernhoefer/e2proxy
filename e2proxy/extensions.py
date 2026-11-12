"""Extension interface: route registry + lifecycle hooks.

Optional components (e.g. the recorder) plug into e2proxy without touching the
legacy request handler::

    from e2proxy.extensions import route, Extension, register

    @route("GET", "/api/foo")
    def foo(req):
        return {"ok": True}                 # dict/list -> JSON 200

    @route("POST", "/api/foo/")             # trailing "/" = prefix match
    def foo_item(req):
        return {"error": "nope"}, 400       # (payload, status)

    register(Extension(name="foo", start=lambda: ..., enabled=lambda: True))

Handlers return a dict/list (JSON), a ``str`` (HTML) or ``(payload, status)``.
Registered routes are checked *before* the built-in routes. Routes of an
extension whose ``enabled()`` is false answer 404, so toggling an extension in
the config needs no restart for its API to disappear.
"""
from __future__ import annotations

import json
import threading
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable

from e2proxy.logs import log

Handler = Callable[["Request"], Any]


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    data: dict
    client_ip: str

    def arg(self, name: str, default: str = "") -> str:
        return self.query.get(name, default)


@dataclass
class Extension:
    name: str
    start: Callable[[], None] | None = None
    enabled: Callable[[], bool] = lambda: True
    started: bool = field(default=False, init=False)


_routes: dict[tuple[str, str], tuple[Handler, str | None]] = {}
_prefix_routes: list[tuple[str, str, Handler, str | None]] = []
_extensions: dict[str, Extension] = {}
_lock = threading.Lock()


def route(method: str, path: str, extension: str | None = None):
    """Register ``fn`` for ``method`` + ``path`` (prefix match if path ends with '/')."""
    def deco(fn: Handler) -> Handler:
        with _lock:
            if path.endswith("/"):
                _prefix_routes.append((method.upper(), path, fn, extension))
                _prefix_routes.sort(key=lambda r: -len(r[1]))  # longest prefix wins
            else:
                _routes[(method.upper(), path)] = (fn, extension)
        return fn
    return deco


def register(ext: Extension) -> None:
    _extensions[ext.name] = ext


def extensions() -> list[Extension]:
    return list(_extensions.values())


def start_all() -> None:
    """Start every registered, enabled extension once (errors are isolated)."""
    for ext in extensions():
        if ext.started or not ext.start:
            continue
        try:
            if ext.enabled():
                ext.start()
                ext.started = True
                log.info(f"Extension gestartet: {ext.name}")
        except Exception as e:
            log.error(f"Extension {ext.name} Start fehlgeschlagen: {e}")


def resolve(method: str, path: str) -> Handler | None:
    hit = _routes.get((method, path))
    if hit is None:
        hit = next(((fn, ext) for m, p, fn, ext in _prefix_routes
                    if m == method and path.startswith(p)), None)
    if hit is None:
        return None
    fn, ext_name = hit
    ext = _extensions.get(ext_name) if ext_name else None
    if ext is not None and not ext.enabled():
        return None
    return fn


def dispatch(handler, method: str, data: dict | None = None) -> bool:
    """Try registered routes for a BaseHTTPRequestHandler. True if handled."""
    parsed = urllib.parse.urlparse(handler.path)
    fn = resolve(method, parsed.path)
    if fn is None:
        return False
    req = Request(method, parsed.path, dict(urllib.parse.parse_qsl(parsed.query)),
                  data or {}, handler.client_address[0])
    try:
        result = fn(req)
    except Exception as e:
        log.error(f"Extension-Route {method} {parsed.path}: {e}")
        result = ({"ok": False, "error": str(e)}, 500)
    status = 200
    if isinstance(result, tuple):
        result, status = result
    if isinstance(result, str):
        handler.send_html(result, status)
    else:
        handler.send_json(result, status)
    return True


def read_json_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length", 0) or 0)
    if length <= 0:
        return {}
    try:
        data = json.loads(handler.rfile.read(length) or b"{}")
        return data if isinstance(data, dict) else {"_": data}
    except (ValueError, UnicodeDecodeError):
        return {}
