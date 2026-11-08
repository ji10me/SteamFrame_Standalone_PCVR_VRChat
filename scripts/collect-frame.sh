#!/bin/sh
# Run from the Mac. Only writes local inventory; the Frame is read-only.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ "$#" -ne 1 ]; then
  echo "Usage: sh scripts/collect-frame.sh steamos@FRAME_ADDRESS" >&2
  exit 2
fi
TARGET=$1
case "$TARGET" in
  -*|*[!a-zA-Z0-9_.@:-]*) echo "Invalid SSH target" >&2; exit 2 ;;
esac
umask 077
mkdir -p "$ROOT/local/runs"
RUN=$(mktemp -d "$ROOT/local/runs/m0-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
# No host-key bypass and no password in a command, file or environment variable.
# Source is streamed into Python: no installation or config write on the headset.
if ssh -o ConnectTimeout=10 "$TARGET" 'python3 - collect' < "$ROOT/tools/framectl.py" > "$RUN/inventory.partial.json"; then
  python3 -m json.tool "$RUN/inventory.partial.json" >/dev/null
  mv "$RUN/inventory.partial.json" "$RUN/inventory.json"
  printf 'Saved: %s\n' "$RUN/inventory.json"
  python3 "$ROOT/tools/framectl.py" check "$RUN/inventory.json" > "$RUN/check.json" || {
    status=$?
    cat "$RUN/check.json"
    exit "$status"
  }
  cat "$RUN/check.json"
else
  status=$?
  printf 'SSH collection failed (exit %s). No valid inventory was recorded.\n' "$status" >&2
  exit "$status"
fi
