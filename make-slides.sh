#!/bin/bash
# LiveMTG meeting deck generator. Design is vendored from slide-work's canonical
# slide-patterns.html; the AI selects and fills patterns but never invents CSS.
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
[ -s "$SDIR/transcript.txt" ] || { echo "文字起こしが空です。先に録音してください。" >&2; exit 1; }

PROMPT="$SDIR/.prompt.txt"
python3 - "$SDIR" "$PROMPT" "$GUIDE" "$EXAMPLES" <<'PY'
import json, os, re, sys

sdir, outp, guide_path, examples_path = sys.argv[1:]
title = os.environ.get("TITLE", "会議")
language = os.environ.get("LIVE_MTG_LANGUAGE", "ja")
today = os.environ.get("TODAY", "")
def read_optional(name, fallback=""):
    path = os.path.join(sdir, name)
    return open(path, encoding="utf-8").read() if os.path.isfile(path) else fallback

def read_json(name):
    try:
        return json.loads(read_optional(name, "{}"))
    except (TypeError, ValueError):
        return {}

def text_of(value):
    return value.get("text", "") if isinstance(value, dict) else str(value or "")

final = read_json("final.json") or read_json("data.json")
flow = read_json("meeting-flow.json")
learnings = read_optional("learnings.md")
tp = os.path.join(sdir, "transcript-full.txt")
if not (os.path.isfile(tp) and os.path.getsize(tp) > 0):
    tp = os.path.join(sdir, "transcript.txt")
transcript = open(tp, encoding="utf-8").read()
guide = open(guide_path, encoding="utf-8").read()
examples = open(examples_path, encoding="utf-8").read()
keep_patterns = {"P01", "P03", "P03b", "P05", "P07", "P10", "P17", "P22",
                 "P23", "P31", "P33", "P33b", "P34", "P35", "P37", "P41",
                 "P43", "P44", "P46", "P47", "P57"}
example_sections = re.findall(r'(<!-- ===== .*?)(?=<!-- ===== |\Z)', examples, flags=re.S)
examples = "\n".join(section for section in example_sections
                     if (m := re.search(r'id="pat-([^\"]+)', section)) and m.group(1) in keep_patterns)

agendas = []
for agenda in flow.get("agendas", []) if isinstance(flow, dict) else []:
    if not isinstance(agenda, dict):
        continue
    result = agenda.get("result") if isinstance(agenda.get("result"), dict) else {}
    agendas.append({
        "title": agenda.get("title", ""),
        "discussionStatus": agenda.get("status", ""),
        "agreementStatus": agenda.get("resolutionStatus", ""),
        "summary": agenda.get("summary", "") or text_of(result.get("summary")),
        "answers": [text_of(item) for item in result.get("answers", []) if text_of(item)],
        "decisions": [text_of(item) for item in result.get("decisions", []) if text_of(item)],
        "actions": [text_of(item) for item in result.get("actions", []) if text_of(item)],
        "unresolved": [text_of(item) for item in result.get("unresolved", []) if text_of(item)],
    })
source = {
    "summary": final.get("summary", "") if isinstance(final, dict) else "",
    "keyPoints": final.get("points", []) if isinstance(final, dict) else [],
    "decisions": final.get("decisions", []) if isinstance(final, dict) else [],
    "actions": final.get("todos", []) if isinstance(final, dict) else [],
    "openItems": final.get("open", []) if isinstance(final, dict) else [],
    "agendas": agendas,
}
source_json = json.dumps(source, ensure_ascii=False, separators=(",", ":"))
# 完了後は構造化済みの正本だけを使う。正本がまだ無い場合に限り、全文の
# 先頭・末尾を補助材料として付け、長時間会議でもプロンプトを無制限にしない。
has_structured_source = bool(agendas or source["summary"] or source["keyPoints"])
transcript_excerpt = ""
if not has_structured_source:
    transcript_excerpt = transcript if len(transcript) <= 18000 else transcript[:9000] + "\n…（中略）…\n" + transcript[-9000:]
map_assets = [name for name in ("minutes-map-radial.png", "minutes-map-relation.png")
              if os.path.isfile(os.path.join(sdir, name))]

prompt = f'''あなたは経営会議・商談資料の編集者です。会議「{title}」を、Slide Workの正典パターンから組み立てた意思決定用デッキに変換してください。

以下の生成契約を厳守してください。

{guide}

# 今回の編集方針
- 既定モードはhybrid。結論・重要数字・締めはMESSAGE、根拠・比較・決定・ToDoはINFORMATIVE。
- 会議の要約をただ縦に並べず、「結論 → 根拠 → 決定 → 次の行動」のストーリーにする。
- 6〜10枚を目安とし、情報が少なければ枚数を減らす。ページ数を埋めるための一般論は足さない。
- 1枚目だけでも会議の到達点が分かるようにし、学びと次の一手を2枚目に置く。
- 議題ごとの到達点、決定、次の行動、未解決を混ぜず、聞き手が説明を追える順番にする。
- タイトルはトピック名ではなく、そのページで伝える結論にする。
- 数字、社名、人名、発言、決定は入力に存在するものだけ。推測や一般論でページを増やさない。
- 資料単体で理解できる範囲を保ちつつ、各ページの文章は短くする。
- 表紙の日付は {today}。会社テーマ・顧客ロゴ・絵文字は使わない。
- 下記の実例から最適なパターンを選び、DOM構造とclassをそのままコピーして文言だけを差し替える。
- 最終出力では `.pt` を含めない。ページ番号はシステムが付けるので出力しなくてよい。
- 利用可能なマップ画像がある場合は、終盤の図版スライドで実画像をそのまま使う。図をHTMLで再構築しない。
- マップ画像は `<div class="fig fit"><div class="body"><img src="minutes-map-radial.png"></div></div>` の正典DOMで置き、containで全体を見せる。画像を `.fig.fit` の直下に置かない。

# 使用可能なSlide Workパターン実例
{examples}

# 会議データ（重複を除いた構造化済みの正本）
{source_json}

# 学びと次の一手
{learnings or "（未生成）"}

# 利用可能な図版
{", ".join(map_assets) if map_assets else "（なし）"}

# 文字起こし補助（構造化済みデータが無い場合のみ）
{transcript_excerpt or "（構造化済みデータを使用）"}
'''
if language == "en":
    prompt += "\nIMPORTANT: Write every visible slide title, label, sentence, and SVG text in English. Keep the supplied HTML classes unchanged."
prompt += """

# 応答形式（最重要）
ファイルを探索・編集・保存しないでください。説明、作業報告、Markdownコードフェンスも不要です。
最終応答は `<div class="slide` から始まる6〜10枚分のHTML断片だけにしてください。
"""
open(outp, "w", encoding="utf-8").write(prompt)
PY

if [ "${AI_PROVIDER:-claude}" = "codex" ]; then
  FALLBACK_MODEL="${CODEX_SLIDE_FALLBACK_MODEL:-${CODEX_QUALITY_MODEL:-gpt-5.6-sol}}"
  out=$(python3 "$SCRIPT_DIR/scripts/run-slide-ai.py" "$PROMPT" --provider codex \
    --model "${CODEX_QUALITY_MODEL:-gpt-5.6-sol}" --fallback-model "$FALLBACK_MODEL" \
    --effort "${CODEX_QUALITY_EFFORT:-high}" --fallback-effort medium \
    --primary-timeout 900 --fallback-timeout 300)
else
  out=$(python3 "$SCRIPT_DIR/scripts/run-slide-ai.py" "$PROMPT" --provider claude \
    --model "$SLIDE_MODEL" --fallback-model "${CLAUDE_SLIDE_FALLBACK_MODEL:-sonnet}" \
    --primary-timeout 900 --fallback-timeout 300)
fi
rm -f "$PROMPT"
[ -n "$out" ] || { echo "AIの出力が空でした" >&2; exit 1; }

BODY="$SDIR/.slides.body.html"
printf '%s' "$out" > "$BODY"
python3 - "$TPL" "$SDIR/slides.html" "$TITLE" "$BODY" <<'PY'
import html, os, re, sys

tpl_path, out_path, title, body_path = sys.argv[1:]
slides = open(body_path, encoding="utf-8").read().strip()
slides = re.sub(r"^```(?:html)?\s*|```\s*$", "", slides, flags=re.I | re.M).strip()
slides = re.sub(r'<div class="pt">.*?</div>', "", slides, flags=re.S)
slides = re.sub(r'<div class="page">.*?</div>', "", slides, flags=re.S)
slides = re.sub(r'<script\b.*?</script>|<style\b.*?</style>', "", slides, flags=re.I | re.S)
if "<html" in slides.lower() or "<body" in slides.lower():
    raise SystemExit("AIがスライド断片ではなくHTML全体を返しました")

# 図版はAIのHTMLゆらぎを許さない。正典の .fig > .body > img に強制し、
# 本文領域の上端から全高を使う専用クラスを付ける。
def normalize_map_slide(match):
    block = match.group(0)
    if not re.search(r'minutes-map-(?:radial|relation)\.png', block):
        return block
    block = re.sub(
        r'(<div class="fig fit">\s*)(<img\b[^>]*minutes-map-(?:radial|relation)\.png[^>]*>)(\s*</div>)',
        r'\1<div class="body">\2</div>\3', block, flags=re.I)
    block = re.sub(r'^<div class="slide(?![^\"]*\bmap-figure-slide\b)([^\"]*)">',
                   r'<div class="slide map-figure-slide\1">', block, count=1)
    return block

slides = re.sub(r'<div class="slide(?:\s[^\"]*)?">.*?(?=<div class="slide(?:\s|\")|\Z)',
                normalize_map_slide, slides, flags=re.S)
# AIが図版指示を1枚だけ落としても、確定済みスクリーンショットは必ず収録する。
# P13（画像1枚）の正典DOMをそのまま使い、画像そのものは再構築しない。
map_defs = (("minutes-map-radial.png", "放射マップ", "議題から結論までの広がりを一望"),
            ("minutes-map-relation.png", "会話の関係", "発言から判断までのつながりを俯瞰"))
for filename, heading, subtitle in map_defs:
    if os.path.isfile(os.path.join(os.path.dirname(out_path), filename)) and filename not in slides:
        figure_slide = f'''\n<div class="slide map-figure-slide">
  <div class="corp-logo"></div>
  <div class="head"><div class="kick">会議の全体像</div><h1>{heading}</h1><div class="hsub">{subtitle}</div></div>
  <div class="stage"><div class="fig fit"><div class="body"><img src="{filename}" alt="{heading}"></div><div class="cap">会議終了時点の確定図</div></div></div>
</div>\n'''
        last_slide = slides.rfind('<div class="slide')
        slides = slides[:last_slide] + figure_slide + slides[last_slide:] if last_slide >= 0 else slides + figure_slide
n = len(re.findall(r'<div class="slide(?:\s|\")', slides))
if not 1 <= n <= 16:
    raise SystemExit(f"スライド枚数が不正です: {n}")
if slides.count('class="corp-logo"') != n:
    raise SystemExit("Slide Workパターンではない出力です（各ページのcorp-logo構造が不一致）")

tpl = open(tpl_path, encoding="utf-8").read()
suffix = " | Meeting slides" if os.environ.get("LIVE_MTG_LANGUAGE", "ja") == "en" else " ｜ 議事スライド"
document = (tpl.replace("{{TITLE}}", html.escape(title + suffix))
               .replace("{{SLIDES}}", slides))
open(out_path, "w", encoding="utf-8").write(document)
print("生成:", out_path, "／Slide Workデザイン／スライド枚数:", n)
PY
rc=$?
if [ "$rc" -eq 0 ]; then
  rm -f "$BODY" "$SDIR/.slides.last-invalid.txt"
else
  mv -f "$BODY" "$SDIR/.slides.last-invalid.txt"
  echo "AIの無効出力を保存: $SDIR/.slides.last-invalid.txt" >&2
fi
exit $rc
