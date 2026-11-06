#!/bin/bash
# Rollout mit Self-Check-Gate.
# Die neue Datei wird NUR dann installiert, wenn der WebUI-Self-Check sauber
# durchlaeuft. Schlaegt er fehl, bleibt die laufende Version unangetastet.
set -u

SRC="${1:-$HOME/e2proxy.py}"
DST="$HOME/e2proxy/e2proxy.py"
# Optionales Probe-Modul liegt neben der Quelldatei (z.B. ~/e2probe.py)
PROBE_SRC="$(dirname "$SRC")/e2probe.py"
PROBE_DST="$HOME/e2proxy/e2probe.py"
COMPOSE="$HOME/e2proxy/docker-compose.e2proxy.yml"
SCDIR="$(mktemp -d /tmp/e2selfcheck.XXXXXX)"

cleanup() { rm -rf "$SCDIR"; }
trap cleanup EXIT

if [ ! -f "$SRC" ]; then
  echo "FEHLER: $SRC nicht gefunden" >&2
  exit 1
fi

echo "== 1/4 Syntax-Check =="
if ! python3 -m py_compile "$SRC" || { [ -f "$PROBE_SRC" ] && ! python3 -m py_compile "$PROBE_SRC"; }; then
  echo "ABBRUCH: Python-Syntaxfehler — Rollout gestoppt, alte Version laeuft weiter." >&2
  exit 2
fi

echo "== 2/4 WebUI Self-Check =="
SCOUT="$SCDIR/selfcheck.log"
if ! E2PROXY_DATA_DIR="$SCDIR" python3 "$SRC" --selfcheck >"$SCOUT" 2>&1; then
  echo "ABBRUCH: WebUI Self-Check fehlgeschlagen — Rollout gestoppt," >&2
  echo "         alte Version laeuft unveraendert weiter." >&2
  grep -v "Default-Config laden fehlgeschlagen" "$SCOUT" | tail -40 >&2
  exit 3
fi
grep -v "Default-Config laden fehlgeschlagen" "$SCOUT" | tail -3

echo "== 3/4 Installieren =="
if [ -f "$DST" ]; then
  cp "$DST" "$DST.bak.$(date +%Y%m%d-%H%M%S)"
  # nur die letzten 10 Backups behalten
  ls -1t "$DST".bak.* 2>/dev/null | tail -n +11 | xargs -r rm -f
fi
if [ -f "$PROBE_SRC" ]; then
  cp "$PROBE_SRC" "$PROBE_DST" || { echo "ABBRUCH: Kopieren e2probe.py fehlgeschlagen" >&2; exit 4; }
fi
cp "$SRC" "$DST" || { echo "ABBRUCH: Kopieren fehlgeschlagen" >&2; exit 4; }
if [ -f "$PROBE_DST" ] && ! grep -q "e2probe.py:/app/e2probe.py" "$COMPOSE"; then
  echo "WARNUNG: $COMPOSE mountet e2probe.py nicht — Probes bleiben inaktiv." >&2
  echo "         Volume '- ./e2probe.py:/app/e2probe.py:ro' ergaenzen und 'docker compose up -d' ausfuehren." >&2
fi

echo "== 4/4 Container neu starten =="
docker compose -f "$COMPOSE" restart || { echo "ABBRUCH: restart fehlgeschlagen" >&2; exit 5; }

echo "reload done"
