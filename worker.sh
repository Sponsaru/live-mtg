#!/bin/bash
# ─────────────────────────────────────────────────────────────
# worker.sh — 1会議ぶんの「録音→文字起こし→整理」ワーカー
#   server.py から起動/停止される。1セッション($SDIR)にだけ書き込む。
#   録音: ffmpeg(CHUNK秒chunk) → whisper-cli文字起こし → claudeでdata.json更新
#   停止: TERM/INTを受けたらffmpegも道連れで確実に終わる（孤児化バグ対策）
# ─────────────────────────────────────────────────────────────
set -uo pipefail

: "${SDIR:?SDIR(セッションフォルダ)が未指定}"
MIC="${MIC:-1}"
CHUNK="${CHUNK:-30}"
MODEL="${MODEL:-$HOME/.cache/whisper-cpp/ggml-large-v3-turbo.bin}"
CLAUDE_MODEL="${CLAUDE_MODEL:-sonnet}"
TITLE="${TITLE:-会議}"
WAVDIR="${WAVDIR:-$SDIR/wav}"   # 一時wavの置き場（server.pyはローカルを指定。ドライブ同期を汚さない）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export SDIR MODEL CLAUDE_MODEL TITLE

[ -f "$MODEL" ] || { echo "✗ モデルが無い: $MODEL" >&2; exit 1; }

mkdir -p "$SDIR" "$WAVDIR"
rm -f "$WAVDIR"/*.wav "$WAVDIR"/*.txt 2>/dev/null
[ -f "$SDIR/transcript.txt" ] || : > "$SDIR/transcript.txt"

source "$SCRIPT_DIR/lib.sh"

# ===== 録音（CHUNK秒ごとのwavセグメント）=====
ffmpeg -hide_banner -loglevel error -f avfoundation -i ":$MIC" \
  -ar 16000 -ac 1 -f segment -segment_time "$CHUNK" -reset_timestamps 1 \
  "$WAVDIR/chunk_%04d.wav" &
FFMPEG_PID=$!

cleanup(){
  kill "$FFMPEG_PID" 2>/dev/null
  wait "$FFMPEG_PID" 2>/dev/null
  # 一時wavを片付ける
  rm -f "$WAVDIR"/*.wav "$WAVDIR"/*.txt 2>/dev/null
  exit 0
}
trap cleanup INT TERM EXIT

# ===== メインループ：完成したchunkを古い順に処理（書き込み中の最新1個は除く）=====
# ※ macOS標準の bash 3.2 でも動くよう、mapfile / 連想配列(declare -A) は使わない。
#   処理済みwavは都度 rm するので、残っているwav＝未処理。重複処理管理は不要。
#   chunk_%04d.wav はパスに空白を含まないため、配列展開は安全。
while true; do
  # ffmpegが死んでいたら終了
  kill -0 "$FFMPEG_PID" 2>/dev/null || { echo "ffmpeg停止を検知。ワーカー終了" >&2; break; }
  all=( $(ls "$WAVDIR"/chunk_*.wav 2>/dev/null | sort) )
  cnt=${#all[@]}
  if [ "$cnt" -ge 2 ]; then
    i=0
    while [ "$i" -lt $((cnt-1)) ]; do
      c="${all[$i]}"
      i=$((i+1))
      [ -f "$c" ] || continue
      txt=$(transcribe_and_filter "$c")
      if [ -n "$txt" ]; then
        printf '%s\n' "$txt" >> "$SDIR/transcript.txt"
        update_data
      fi
      rm -f "$c"
    done
  fi
  sleep 2
done
