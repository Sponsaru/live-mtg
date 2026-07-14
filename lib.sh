#!/bin/bash
# lib.sh — live-mtg 共通処理（無音スキップ・ハルシネーション除去・whisper・claude整理）
# 呼び出し側で MODEL / SDIR(セッションフォルダ) / TITLE / CLAUDE_MODEL を定義しておくこと。
# SDIR未定義なら旧来どおり RUN にフォールバック。

MODEL="${MODEL:-$HOME/.cache/whisper-cpp/ggml-large-v3-turbo.bin}"
CLAUDE_MODEL="${CLAUDE_MODEL:-sonnet}"
SDIR="${SDIR:-${RUN:-$HOME/mtg-live}}"   # 議事録の出力先（1会議=1フォルダ）
SILENCE_DB="${SILENCE_DB:--45}"   # mean_volumeがこれ未満(dB)なら無音とみなしwhisperにかけない

# whisperが無音・雑音時に吐きやすい定型ハルシネーション句（単独行なら捨てる）
HALLU='おやすみなさい|ご視聴ありがとうございました|ご清聴ありがとうございました|最後までご覧いただきありがとうございました|チャンネル登録|高評価|バイバイ|ありがとうございました'

# wavを文字起こしし、意味のあるテキストだけを標準出力（無音/ハルシネーションなら空）
transcribe_and_filter(){
  local wav="$1" base="${1%.wav}" vol txt
  vol=$(ffmpeg -hide_banner -i "$wav" -af volumedetect -f null - 2>&1 \
        | awk -F: '/mean_volume/{gsub(/[ dB]/,"",$2);print $2+0;f=1} END{if(!f)print -99}')
  # 無音ならスキップ
  if awk "BEGIN{exit !($vol < ${SILENCE_DB})}"; then
    return 0
  fi
  whisper-cli -m "$MODEL" -f "$wav" -l ja -otxt -of "$base" --no-timestamps -np >/dev/null 2>&1
  txt=$(tr -d '\r' < "$base.txt" 2>/dev/null | sed '/^[[:space:]]*$/d')
  rm -f "$base.txt"
  # 定型ハルシネーション句のみの行を除去
  txt=$(printf '%s\n' "$txt" | grep -Ev "^[[:space:]]*(${HALLU})[[:space:]、。.!！]*$")
  # 同一フレーズの連続反復（例:「おやすみなさいおやすみなさい」）を1回に圧縮
  txt=$(printf '%s\n' "$txt" | perl -pe 's/(.{4,}?)\1{2,}/$1/g' 2>/dev/null || printf '%s\n' "$txt")
  printf '%s' "$txt"
}

# 累積transcriptをclaudeで構造化JSON化し data.json をアトミック更新
update_data(){
  local transcript now out prompt
  transcript=$(cat "$SDIR/transcript.txt")
  [ -z "$transcript" ] && return
  now=$(date +%H:%M:%S)
  prompt=$(cat <<EOF
あなたは会議「${TITLE}」のリアルタイム書記です。
以下は会議音声の文字起こし（時系列。whisperによる自動認識のため誤変換あり）です。
現時点までの内容を構造化し、**有効なJSONのみ**を出力してください。
前置き・説明・コードフェンス(\`\`\`)は一切禁止。JSONオブジェクトだけを返す。

スキーマ:
{
  "updated": "${now}",
  "summary": "今まさに何を議論しているかを1〜2文の自然文で",
  "agenda": ["扱っている/扱った議題を体言止めで"],
  "points": ["出た論点・意見・主張を短く"],
  "decisions": ["合意・決定したこと"],
  "todos": [{"who":"担当者名（山田/佐藤など。不明なら未定）","what":"やること"}],
  "open": ["未解決・保留・要確認の事項"]
}

ルール:
- 各配列は重要な順に最大8件。
- whisperの誤変換は文脈から補正する（固有名詞・数字に注意）。
- まだ情報が無い項目は空配列[]。憶測で埋めない。日本語で。

--- 文字起こし ここから ---
${transcript}
--- ここまで ---
EOF
)
  out=$(printf '%s' "$prompt" | claude -p --model "$CLAUDE_MODEL" 2>/dev/null)
  [ -z "$out" ] && return
  out=$(printf '%s\n' "$out" | sed -e 's/^```json[[:space:]]*//' -e 's/^```[[:space:]]*//' -e 's/```[[:space:]]*$//')
  if printf '%s' "$out" | python3 -c 'import sys,json;json.load(sys.stdin)' 2>/dev/null; then
    printf '%s' "$out" > "$SDIR/data.json.tmp" && mv "$SDIR/data.json.tmp" "$SDIR/data.json"
    return 0
  fi
  return 1
}
