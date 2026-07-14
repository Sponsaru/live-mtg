#!/bin/bash
# ─────────────────────────────────────────────────────────────
# live-mtg.sh — 議事ライブ整理 ランチャー
#   コントロールサーバ(server.py)を起動しブラウザを開くだけ。
#   録音の開始/停止・新規会議・会議切替・全文表示・スライド化は、すべて画面ヘッダーから操作する。
#   停止: このターミナルで Ctrl+C（サーバとワーカーを確実に終了）
# ─────────────────────────────────────────────────────────────
set -uo pipefail

MIC="${MIC:-1}"
CHUNK="${CHUNK:-30}"                      # 録音チャンク秒（差分更新なので長くしても要約コストは一定。文脈が効いて精度↑）
ASR_BACKEND="${ASR_BACKEND:-mlx}"         # 文字起こし: mlx=mlx_whisper(Mac・高精度large-v3) / cpp=whisper-cli(Windows等)
MLX_MODEL="${MLX_MODEL:-mlx-community/whisper-large-v3-mlx}"
MODEL="${MODEL:-$HOME/.cache/whisper-cpp/ggml-large-v3-turbo.bin}"   # cpp(whisper-cli)用モデル
CLAUDE_MODEL="${CLAUDE_MODEL:-opus}"      # ライブ議事整理モデル（文脈補正重視。速度優先なら sonnet/haiku）
SLIDE_MODEL="${SLIDE_MODEL:-opus}"        # スライド生成モデル（品質優先）
PORT="${PORT:-8777}"
RUN="${RUN:-$HOME/mtg-live}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export MIC CHUNK ASR_BACKEND MLX_MODEL MODEL CLAUDE_MODEL SLIDE_MODEL PORT RUN

for cmd in ffmpeg claude python3; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "✗ $cmd が見つかりません"; exit 1; }
done
if [ "$ASR_BACKEND" = "mlx" ]; then
  command -v mlx_whisper >/dev/null 2>&1 || { echo "✗ mlx_whisper が無い（pip install mlx-whisper）。または ASR_BACKEND=cpp で whisper-cli 利用"; exit 1; }
else
  command -v whisper-cli >/dev/null 2>&1 || { echo "✗ whisper-cli が見つかりません"; exit 1; }
  [ -f "$MODEL" ] || { echo "✗ モデルが無い: $MODEL"; exit 1; }
fi

# 既に同ポートで起動中なら開くだけ
if curl -s "http://localhost:$PORT/api/state" >/dev/null 2>&1; then
  echo "既にサーバが起動中です → http://localhost:$PORT/"
  open "http://localhost:$PORT/" 2>/dev/null
  exit 0
fi

mkdir -p "$RUN"
python3 "$SCRIPT_DIR/server.py" >"$RUN/server.log" 2>&1 &
SRV_PID=$!
trap 'echo; echo "停止中…"; kill "$SRV_PID" 2>/dev/null; wait "$SRV_PID" 2>/dev/null; echo "✓ 停止しました"; exit 0' INT TERM

# サーバ起動待ち
for _ in $(seq 1 20); do
  curl -s "http://localhost:$PORT/api/state" >/dev/null 2>&1 && break
  sleep 0.3
done
open "http://localhost:$PORT/" 2>/dev/null

echo "════════════════════════════════════════════"
echo " 議事ライブ整理  ｜ http://localhost:$PORT/"
echo " 操作はすべて画面ヘッダーから（録音 開始/停止・新規会議・全文・スライド化）"
echo " 会議データ: $SCRIPT_DIR/meetings/"
echo " ※録音の停止は画面の「■ 録音停止」ボタン。ここでCtrl+Cを押すとサーバごと終了します（＝画面が使えなくなる）"
echo " サーバログ: $RUN/server.log"
echo "════════════════════════════════════════════"
wait "$SRV_PID"
