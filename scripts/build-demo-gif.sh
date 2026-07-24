#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP_DIR="$(mktemp -d /private/tmp/live-mtg-demo.XXXXXX)"
FRAMES="$TMP_DIR/frames"
CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
SERVER_PID=''
CHROME_PID=''

cleanup() {
  if [ -n "$CHROME_PID" ]; then kill "$CHROME_PID" 2>/dev/null || true; wait "$CHROME_PID" 2>/dev/null || true; fi
  if [ -n "$SERVER_PID" ]; then kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true; fi
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT INT TERM
mkdir -p "$FRAMES"

python3 -m http.server 8877 --bind 127.0.0.1 --directory "$ROOT" >"$TMP_DIR/server.log" 2>&1 &
SERVER_PID=$!
"$CHROME" --headless=new --disable-gpu --hide-scrollbars --no-first-run --no-default-browser-check \
  --remote-debugging-port=9333 --user-data-dir="$TMP_DIR/chrome" http://127.0.0.1:8877/ >"$TMP_DIR/chrome.log" 2>&1 &
CHROME_PID=$!

for _ in $(seq 1 50); do
  if curl -fsS http://127.0.0.1:9333/json >/dev/null 2>&1; then break; fi
  sleep 0.2
done
curl -fsS http://127.0.0.1:9333/json >/dev/null

python3 "$ROOT/scripts/capture-demo-gif.py" "$FRAMES"
ffmpeg -hide_banner -loglevel error -y -framerate 2/5 -start_number 1 -i "$FRAMES/%02d.png" \
  -vf "fps=10,scale=960:540:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle" \
  "$ROOT/docs/demo.gif"

printf 'generated: %s\n' "$ROOT/docs/demo.gif"
