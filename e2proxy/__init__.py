"""e2proxy - Enigma2 streaming proxy.

Streams satellite TV from Enigma2 receivers to browsers, Plex (HDHomeRun
emulation), Kodi/Dream Player (OpenWebif emulation) and records programmes.

Run with ``python -m e2proxy`` (``--selfcheck`` validates the WebUI offline).
Importing the package itself is side-effect free; components are wired in
:mod:`e2proxy.app`.
"""
from e2proxy.version import INTERNAL_VERSION, VERSION

__all__ = ["VERSION", "INTERNAL_VERSION"]
