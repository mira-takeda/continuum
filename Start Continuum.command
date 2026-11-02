#!/bin/sh
# Continuum one-click launcher (macOS). Double-click to run.
cd "$(dirname "$0")" || exit 1
URL="http://127.0.0.1:8137/"
if ! command -v python3 >/dev/null 2>&1; then
  echo "[Continuum] python3 was not found."
  echo "Install Python 3.10+ (https://www.python.org/downloads/) and retry."
  exit 1
fi
if curl -sf --max-time 2 "${URL}api/ui/bootstrap" 2>/dev/null | grep -q runtime; then
  echo "[Continuum] A Continuum server is already running. Opening browser..."
  open "$URL"
  exit 0
fi
echo "[Continuum] Starting server..."
python3 -u server.py &
SRV=$!
trap 'kill "$SRV" 2>/dev/null' INT TERM EXIT
i=0
while [ "$i" -lt 30 ]; do
  if curl -sf --max-time 2 "${URL}api/ui/bootstrap" >/dev/null 2>&1; then
    break
  fi
  sleep 1
  i=$((i + 1))
done
if ! curl -sf --max-time 2 "${URL}api/ui/bootstrap" >/dev/null 2>&1; then
  echo "[Continuum] The server did not respond within 30 seconds."
  exit 1
fi
open "$URL"
echo "[Continuum] Browser opened. Press Ctrl-C here to stop the server."
wait "$SRV"
