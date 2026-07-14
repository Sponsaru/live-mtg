#!/bin/bash
# ─────────────────────────────────────────────────────────────
# make-slides.sh — 会議の議事(data.json+transcript)から経営者向けスライドをワンクリック生成
#   trends/ の経営者向けデッキ(dena-namba-keiei-deck)と同じHTMLフォーマット・同じ配色を流用。
#   claudeが本文スライドを生成 → slides-template.html に流し込み → $SDIR/slides.html を出力。
# ─────────────────────────────────────────────────────────────
set -uo pipefail
: "${SDIR:?SDIR未指定}"
export TITLE="${TITLE:-会議}"
SLIDE_MODEL="${SLIDE_MODEL:-opus}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TPL="$SCRIPT_DIR/slides-template.html"
export TODAY="$(date '+%Y.%m.%d')"
export THEME="${THEME:-mainichi}"   # 画面で選択中のデザイン（mainichi / sponsaru）に追従

[ -f "$TPL" ] || { echo "テンプレが無い: $TPL" >&2; exit 1; }
[ -s "$SDIR/transcript.txt" ] || { echo "文字起こしが空です。先に録音してください。" >&2; exit 1; }

# ---- プロンプト生成（HTML例やバッククォートを含むためpythonで安全に組み立て）----
PROMPT="$SDIR/.prompt.txt"
python3 - "$SDIR" "$PROMPT" <<'PY'
import sys, os
sdir, outp = sys.argv[1], sys.argv[2]
title = os.environ.get("TITLE", "会議")
today = os.environ.get("TODAY", "")
data = open(os.path.join(sdir, "data.json"), encoding="utf-8").read()
# 清書(finalize)済みなら高精度版の文字起こし(transcript-full)を優先して使う
tp = os.path.join(sdir, "transcript-full.txt")
if not (os.path.isfile(tp) and os.path.getsize(tp) > 0):
    tp = os.path.join(sdir, "transcript.txt")
transcript = open(tp, encoding="utf-8").read()

tpl = r'''あなたは経営会議の資料デザイナーです。以下の会議「__TITLE__」の議事内容を、
**経営者向けプレゼンスライド（フルスクリーンHTML）に変換**してください。

# 出力形式（厳守）
- 出力は <div class="slide">…</div> を縦に並べたものだけ。前置き・説明・コードフェンス・<html>や<style>は一切出力しない。
- ロゴとページ番号は自動で付くので出力しない。
- 下記の既定クラスだけを使う（新しいCSSクラスやstyle属性は作らない）。マイニチブルー基調。
- 視認性最優先：本文は短く体言止め。キー数字はKPIで大きく。1スライド1メッセージ。

# 使えるスライド部品（このクラスだけ）
1) 表紙:
<div class="slide cover"><div class="cover-inner"><div class="cover-rule"></div>
<h1 class="cover-title">タイトル<br>2行可</h1>
<p class="cover-sub"><span>会議名</span> ― 一言サマリ</p>
<div class="cover-meta">__TODAY__　｜　LiveMTG 議事サマリ</div></div></div>

2) 章扉（中央大文字）:
<div class="slide center"><div class="stage"><span class="chip blue">セクション名</span>
<div class="bigstmt">大きな一文<br><span class="pink">強調</span></div>
<p class="substmt">補足の一文。<b>太字</b>で要点。</p></div></div>

3) 見出し＋パネル:
<div class="slide"><div class="head"><div class="kick">キッカー</div><h1>見出し</h1><div class="hsub">サブ見出し</div></div>
<div class="stage"><div class="panel"><h3>小見出し</h3><p>本文。<b>要点</b>。</p></div></div></div>

4) KPIバンド（数字を大きく。heroは強調1枚）:
<div class="kpis"><div class="kpi hero"><div class="v">15<small>台</small></div><div class="l">説明</div></div>
<div class="kpi"><div class="v">7<small>月</small></div><div class="l">説明</div></div></div>

5) 2カラム比較:
<div class="cols"><div class="panel"><div class="kick">左ラベル</div><h3>見出し</h3><ul><li>項目</li></ul></div>
<div class="panel"><div class="kick">右ラベル</div><h3>見出し</h3><ul><li>項目</li></ul></div></div>

6) 濃紺の強調ブロック: <div class="why">重要な論点。<b class="pink">最重要語</b>。</div>
7) 要点バー: <div class="take"><b>ポイント：</b>持ち帰りメッセージ。</div>
8) 3ステップ/フロー: <div class="flow"><div class="step"><div class="n">STEP1</div><h4>見出し</h4><p>説明</p></div>…</div>（1つに on クラスを付けて強調可）
9) ToDo/決定は 3)のpanel内 <ul><li> で「担当：やること」の形で明記。
10) 図形（Mermaid）: プロセス/相関/組織/時系列/対比が話に出たら図で見せる。stageの中に <div class="mermaid">…</div> を置く（Mermaid記法。配色は自動でマイニチブルー）。
   - フロー: flowchart LR
       A[紙台帳] --> B[スプレッドシート] --> C[自動アラート]
   - 相関/担当: flowchart LR
       小林 -->|作成| AI[AIツール]
       矢沢 -->|チェック| 成果物
   - 時系列: flowchart LR
       T1[7月: 台帳移行] --> T2[来週: 通知の叩き台] --> T3[以降: 運用]
   - 2x2やサイクルもflowchartで表現可。
   ★Mermaid記法は必ず有効なものにする（ノード名の記号・全角括弧に注意。日本語ラベルは [〜] で囲む）。1枚の図はノード6個程度まで。図スライドは head + stage + mermaid（＋必要なら take）。

# 3)〜9)の中身は必ず <div class="slide"><div class="head">…</div><div class="stage"> … </div></div> の stage の中に入れる。

# 構成（9〜13枚を目安）
1. 表紙
2. この会議の結論・要旨（summaryを1枚で。takeを使う）
3〜N. 議題ごとに1〜2枚（論点→決定→ToDo の流れ。KPI/2カラム/whyを適宜）
・全体で最低1〜2枚は図（Mermaid）を入れる。プロセスや担当の相関、時系列など図が効く所に。
末尾. 決定事項まとめ ＋ ToDo一覧（担当・やることを明記）を1〜2枚

# 厳守
- 数字（台数・日付・件数・担当者名）は議事から正確に拾う。無い数字を創作しない。
- whisperの誤変換らしき語は文脈で自然に補正。
- 事実は議事の範囲内のみ。推測で埋めない。

# 会議データ（構造化済み）
__DATA__

# 文字起こし全文
__TRANSCRIPT__
'''
prompt = (tpl.replace("__TITLE__", title)
             .replace("__TODAY__", today)
             .replace("__DATA__", data)
             .replace("__TRANSCRIPT__", transcript))
open(outp, "w", encoding="utf-8").write(prompt)
PY

# ---- 選択中AIでスライド本文を生成 ----
if [ "${AI_PROVIDER:-claude}" = "codex" ]; then
  AI_OUT="$SDIR/.ai-slides.out"
  codex exec --ephemeral --sandbox read-only --skip-git-repo-check -C "$SDIR" \
    --color never -o "$AI_OUT" - < "$PROMPT" >/dev/null 2>&1
  out=$(cat "$AI_OUT" 2>/dev/null || true)
  rm -f "$AI_OUT"
else
  out=$(claude -p --model "$SLIDE_MODEL" < "$PROMPT" 2>/dev/null)
fi
rm -f "$PROMPT"
[ -z "$out" ] && { echo "AIの出力が空でした" >&2; exit 1; }
# 万一コードフェンスが付いたら剥がす
out=$(printf '%s\n' "$out" | sed -e 's/^```html[[:space:]]*//' -e 's/^```[[:space:]]*//' -e 's/```[[:space:]]*$//')

# ---- テンプレに流し込み（本文はファイル経由で渡す）----
BODY="$SDIR/.slides.body.html"
printf '%s' "$out" > "$BODY"
python3 - "$TPL" "$SDIR/slides.html" "$TITLE" "$BODY" <<'PY'
import sys, os
tpl_path, out_path, title, body_path = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
slides = open(body_path, encoding="utf-8").read()
tpl = open(tpl_path, encoding="utf-8").read()
tpl = (tpl.replace("{{TITLE}}", title + " ｜ 議事スライド")
          .replace("{{THEME}}", os.environ.get("THEME", "mainichi"))
          .replace("{{SLIDES}}", slides))
open(out_path, "w", encoding="utf-8").write(tpl)
n = slides.count('class="slide')
if n == 0:
    sys.stderr.write("スライドが生成されませんでした\n"); sys.exit(1)
print("生成:", out_path, "／スライド枚数:", n)
PY
rc=$?
rm -f "$BODY"
exit $rc
