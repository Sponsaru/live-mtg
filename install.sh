#!/usr/bin/env bash
set -euo pipefail

VERSION="latest"
ONBOARD=1
DRY_RUN=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --version) VERSION="${2:?--version needs a value}"; shift 2 ;;
    --no-onboard) ONBOARD=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --help|-h)
      echo "Usage: install.sh [--version latest|<version>] [--no-onboard] [--dry-run]"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

run() {
  if [ "$DRY_RUN" = 1 ]; then printf '+ '; printf '%q ' "$@"; printf '\n'; else "$@"; fi
}

echo "LiveMTG installer"

if ! command -v node >/dev/null 2>&1; then
  if [ "$(uname -s)" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
    run brew install node
  else
    echo "Node.js 20以上を先にインストールしてください: https://nodejs.org/" >&2
    exit 1
  fi
fi

major=$(node -p 'Number(process.versions.node.split(".")[0])')
if [ "$major" -lt 20 ]; then echo "Node.js 20以上が必要です（現在: $(node -v)）" >&2; exit 1; fi

if ! command -v python3 >/dev/null 2>&1 && [ "$(uname -s)" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
  run brew install python
fi
if ! command -v ffmpeg >/dev/null 2>&1 && [ "$(uname -s)" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
  run brew install ffmpeg
fi

run npm install -g "live-mtg@${VERSION}"

if [ "$ONBOARD" = 1 ]; then
  run live-mtg onboard
else
  echo "インストール完了。初期設定: live-mtg onboard"
fi
