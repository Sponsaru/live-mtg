#!/bin/bash
# LiveMTG learning-report deck generator. Same Slide Work vendored patterns as
# make-slides.sh, but the source is the saved learnings report (learnings.md)
# and the audience is the meeting owner reviewing their own next moves.
set -uo pipefail
: "${SDIR:?SDIR未指定}"
export TITLE="${TITLE:-会議}"
SLIDE_MODEL="${SLIDE_MODEL:-opus}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TPL="$SCRIPT_DIR/slide-work-template.html"
GUIDE="$SCRIPT_DIR/slide-work-guide.md"
EXAMPLES="$SCRIPT_DIR/slide-work-pattern-examples.html"
export TODAY="$(date '+%Y.%m.%d')"

for file in "$TPL" "$GUIDE" "$EXAMPLES"; do
  [ -s "$file" ] || { echo "Slide Workファイルが無い: $file" >&2; exit 1; }
done
[ -s "$SDIR/learnings.md" ] || { echo "学びレポートがまだありません。先に「レポートを作る」を実行してください。" >&2; exit 1; }

PROMPT="$SDIR/.learn-prompt.txt"
python3 - "$SDIR" "$PROMPT" "$GUIDE" "$EXAMPLES" <<'PY'
import os, sys

sdir, outp, guide_path, examples_path = sys.argv[1:]
title = os.environ.get("TITLE", "会議")
language = os.environ.get("LIVE_MTG_LANGUAGE", "ja")
today = os.environ.get("TODAY", "")
goal = os.environ.get("GOAL", "")
stance = os.environ.get("STANCE", "")
report = open(os.path.join(sdir, "learnings.md"), encoding="utf-8").read()
guide = open(guide_path, encoding="utf-8").read()
examples = open(examples_path, encoding="utf-8").read()

prompt = f'''あなたは振り返り資料の編集者です。会議「{title}」の学びレポートを、依頼主本人が読み返すための「学びと次の一手」デッキに変換してください。

以下の生成契約を厳守してください。

{guide}

# 今回の編集方針
- 既定モードはhybrid。最重要の気づき・締めの行動宣言はMESSAGE、気づきの一覧・見落とし・次の一手はINFORMATIVE。
- 「結論（最大の学び） → 気づき → 見落とした視点 → 次の一手」のストーリーにする。4〜8枚。
- タイトルはトピック名ではなく、そのページで伝える結論にする。
- 気づき・教訓・人名・事実はレポートに存在するものだけ。推測や一般論でページを増やさない。
- 依頼主の立場は「{stance or '未設定'}」、会議の目標は「{goal or '未設定'}」。この視点で書く。
- 表紙の日付は {today}。会社テーマ・顧客ロゴ・絵文字は使わない。
- 下記の実例から最適なパターンを選び、DOM構造とclassをそのままコピーして文言だけを差し替える。
- 最終出力では `.pt` を含めない。ページ番号はシステムが付けるので出力しなくてよい。

# 使用可能なSlide Workパターン実例
{examples}

# 学びレポート（原文）
{report}
'''
if language == "en":
    prompt += "\nIMPORTANT: Write every visible slide title, label, sentence, and SVG text in English. Keep the supplied HTML classes unchanged."
open(outp, "w", encoding="utf-8").write(prompt)
PY

if [ "${AI_PROVIDER:-claude}" = "codex" ]; then
  AI_OUT="$SDIR/.ai-learn-slides.out"
  codex exec --ephemeral --sandbox read-only --skip-git-repo-check -C "$SDIR" \
    --model "${CODEX_QUALITY_MODEL:-gpt-5.6-sol}" \
    -c "model_reasoning_effort=\"${CODEX_QUALITY_EFFORT:-high}\"" \
    --color never -o "$AI_OUT" - < "$PROMPT" >/dev/null 2>&1
  out=$(cat "$AI_OUT" 2>/dev/null || true)
  rm -f "$AI_OUT"
else
  out=$(claude -p --model "$SLIDE_MODEL" < "$PROMPT" 2>/dev/null)
fi
rm -f "$PROMPT"
[ -n "$out" ] || { echo "AIの出力が空でした" >&2; exit 1; }

BODY="$SDIR/.learn-slides.body.html"
printf '%s' "$out" > "$BODY"
python3 - "$TPL" "$SDIR/learn-slides.html" "$TITLE" "$BODY" <<'PY'
import html, os, re, sys

tpl_path, out_path, title, body_path = sys.argv[1:]
slides = open(body_path, encoding="utf-8").read().strip()
slides = re.sub(r"^```(?:html)?\s*|```\s*$", "", slides, flags=re.I | re.M).strip()
slides = re.sub(r'<div class="pt">.*?</div>', "", slides, flags=re.S)
slides = re.sub(r'<div class="page">.*?</div>', "", slides, flags=re.S)
slides = re.sub(r'<script\b.*?</script>|<style\b.*?</style>', "", slides, flags=re.I | re.S)
if "<html" in slides.lower() or "<body" in slides.lower():
    raise SystemExit("AIがスライド断片ではなくHTML全体を返しました")
n = len(re.findall(r'<div class="slide(?:\s|\")', slides))
if not 1 <= n <= 12:
    raise SystemExit(f"スライド枚数が不正です: {n}")
if slides.count('class="corp-logo"') != n:
    raise SystemExit("Slide Workパターンではない出力です（各ページのcorp-logo構造が不一致）")

tpl = open(tpl_path, encoding="utf-8").read()
suffix = " | Learnings" if os.environ.get("LIVE_MTG_LANGUAGE", "ja") == "en" else " ｜ 学びと次の一手"
document = (tpl.replace("{{TITLE}}", html.escape(title + suffix))
               .replace("{{SLIDES}}", slides))
open(out_path, "w", encoding="utf-8").write(document)
print("生成:", out_path, "／Slide Workデザイン／スライド枚数:", n)
PY
rc=$?
rm -f "$BODY"
exit $rc
