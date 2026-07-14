#!/bin/bash
# ─────────────────────────────────────────────────────────────
# demo-run.sh — マイク無しで動作確認するデモ（車両管理MTGの台本をsay音声で合成）
#   本番と同じ server.py + パイプライン(whisper→claude→data.json)を使い、
#   デモ用セッションに流し込む。ブラウザのヘッダーもそのまま操作できる。
# ─────────────────────────────────────────────────────────────
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

export RUN="${RUN:-$HOME/mtg-live}"
export MODEL="${MODEL:-$HOME/.cache/whisper-cpp/ggml-large-v3-turbo.bin}"
export CLAUDE_MODEL="${CLAUDE_MODEL:-sonnet}"
PORT="${PORT:-8777}"

# ── デモ用セッションを作成（会議データはドライブ内 meetings/ へ）──
MEET="${MEETINGS_DIR:-$SCRIPT_DIR/meetings}"
SID="demo-$(date +%Y%m%d-%H%M%S)"
export SDIR="$MEET/$SID"
export TITLE="車両管理 MTG（デモ）"
mkdir -p "$SDIR"
now=$(date '+%Y-%m-%d %H:%M')
python3 - "$SDIR/meta.json" "$SID" "$TITLE" "$now" <<'PY'
import json,sys
json.dump({"id":sys.argv[2],"title":sys.argv[3],"created":sys.argv[4],"updated":sys.argv[4]},
          open(sys.argv[1],"w",encoding="utf-8"),ensure_ascii=False,indent=2)
PY
: > "$SDIR/transcript.txt"
echo '{"updated":"デモ開始","summary":"車両管理の会議デモを開始します…","agenda":[],"points":[],"decisions":[],"todos":[],"open":[]}' > "$SDIR/data.json"

source "$SCRIPT_DIR/lib.sh"

# ── サーバが未起動なら起動 ──
if ! curl -s "http://localhost:$PORT/api/state" >/dev/null 2>&1; then
  python3 "$SCRIPT_DIR/server.py" &
  SRV=$!
  trap 'kill "$SRV" 2>/dev/null' EXIT INT TERM
  for _ in $(seq 1 20); do curl -s "http://localhost:$PORT/api/state" >/dev/null 2>&1 && break; sleep 0.3; done
fi

# ── 表示をデモ会議に切替えてブラウザを開く ──
curl -s -X POST "http://localhost:$PORT/api/switch" -H 'Content-Type: application/json' -d "{\"id\":\"$SID\"}" >/dev/null
open "http://localhost:$PORT/" 2>/dev/null
echo "表示: http://localhost:$PORT/   （デモ会議: $TITLE）"

# ── 会議台本（呼びかけ名入り。claudeが担当を推定できる）──
turns=(
"では次、車両管理の件です。佐藤部長、山田さん、今うちの営業車って全部で何台稼働してますかね。ええと、確か十五台です。ただ車検の時期とか点検記録が、まだ紙の台帳でバラバラに管理されている状態でして、正直どの車がいつ車検か、ぱっと出てこないんですよ。"
"山田さん、そこをまずデジタル化したいんだよ。誰がいつどの車を使ったか、走行距離、給油、全部エクセルでもいいから一元管理したい。あと事故やヒヤリハットの記録も、その台帳に紐づけられると安全管理の面でありがたいな。"
"賛成です。じゃあ山田さんの方で、現状の紙台帳を七月中にスプレッドシートに移してもらえますか。項目は車番、車種、車検日、担当者、走行距離で。わかりました。あとリース車と自社所有車で管理が違うので、そこは分けた方がいいですかね。"
"分けましょう。リース満了日は絶対に見落とせないので、アラートを出したい。鈴木さん、そのあたりAIで自動通知できないか検討お願いできますか。はい、車検とリース満了の三十日前に自動でリマインドする仕組み、作れます。来週たたき台を出します。"
)

for i in "${!turns[@]}"; do
  echo "▶ ターン$((i+1))/${#turns[@]} 音声合成→文字起こし→整理…"
  say -v Otoya -o "/tmp/demo_$i.aiff" "${turns[$i]}" 2>/dev/null || say -o "/tmp/demo_$i.aiff" "${turns[$i]}"
  ffmpeg -hide_banner -loglevel error -y -i "/tmp/demo_$i.aiff" -ar 16000 -ac 1 "/tmp/demo_$i.wav"
  txt=$(transcribe_and_filter "/tmp/demo_$i.wav")
  if [ -n "$txt" ]; then
    printf '%s\n' "$txt" >> "$SDIR/transcript.txt"
    echo "  文字起こし: $txt"
  fi
  update_data && echo "  → 画面を更新（$(date +%H:%M:%S)）"
  rm -f "/tmp/demo_$i.aiff" "/tmp/demo_$i.wav"
  sleep 3
done

echo "════════ デモ完了 ════════"
echo "ブラウザで「🖥 スライド化」も試せます。"
[ -n "${SRV:-}" ] && { echo "サーバ停止は Ctrl+C"; wait "$SRV"; }
