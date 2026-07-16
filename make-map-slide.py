# -*- coding: utf-8 -*-
"""放射マップ／会話の関係を「Slide Workデザインのスライド1枚」HTMLへ決定論的に変換する。

PDF保存はこの1枚スライドを印刷する（2026-07-16 依頼者指示：マップPDFも
デッキと同じスライド様式・左下ロゴ・うっすら背景で統一）。AIは使わない。
環境変数: SDIR（必須）, TITLE, VIEW=radial|relation, LIVE_MTG_LANGUAGE
"""
import html
import json
import os
import re
import sys

SDIR = os.environ.get("SDIR", "")
if not SDIR or not os.path.isdir(SDIR):
    sys.exit("SDIR未指定")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LANG = os.environ.get("LIVE_MTG_LANGUAGE", "ja")
TITLE = os.environ.get("TITLE", "会議" if LANG != "en" else "Meeting")
VIEW = os.environ.get("VIEW", "radial")
if VIEW not in ("radial", "relation"):
    VIEW = "radial"


def tr(ja, en):
    return en if LANG == "en" else ja


def load_json(name):
    try:
        with open(os.path.join(SDIR, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# 清書版（final.json）には mindmap が無いため、マップ素材はライブ版（data.json）も併用する
final = load_json("final.json")
live = load_json("data.json")
data = final or live
if not data:
    sys.exit("議事データがありません")
if not data.get("mindmap"):
    data = dict(data, mindmap=live.get("mindmap") or [])
if not str(data.get("diagram") or "").strip():
    data = dict(data, diagram=live.get("diagram") or "")


def item_text(v):
    if isinstance(v, dict):
        return str(v.get("label") or v.get("detail") or v.get("what") or "").strip()
    return str(v or "").strip()


def mermaid_label(value):
    value = re.sub(r'[\r\n\[\](){}"#]+', " ", str(value or ""))
    return re.sub(r"\s+", " ", value).strip()[:42] or tr("未整理", "Unsorted")


if VIEW == "radial":
    topics = [t for t in (data.get("mindmap") or []) if isinstance(t, dict) and t.get("topic")]
    if not topics:
        sys.exit(tr("マインドマップの素材がまだありません", "No mind map content yet"))
    lines = ["mindmap", "  root((%s))" % mermaid_label(TITLE)]
    for i, t in enumerate(topics[:8]):
        lines.append('    b%d["%s"]' % (i, mermaid_label(t.get("topic"))))
        for j, g in enumerate((t.get("groups") or [])[:4]):
            lines.append('      b%dg%d["%s"]' % (i, j, mermaid_label(g.get("label"))))
            for k, it in enumerate((g.get("items") or [])[:5]):
                lines.append('        b%dg%di%d["%s"]' % (i, j, k, mermaid_label(item_text(it))))
    code = "\n".join(lines)
    heading = tr("放射マップ", "Radial map")
else:
    code = str(data.get("diagram") or "").strip()
    if not code:
        sys.exit(tr("会話の関係の素材がまだありません", "No relationship diagram yet"))
    heading = tr("会話の関係", "Relationships")


def esc(s):
    return html.escape(str(s or ""), quote=True)


slide = (
    '<div class="slide"><div class="corp-logo"></div>'
    '<div class="head"><div class="kick">%s</div><h1>%s</h1></div>'
    '<div class="stage mapstage"><div class="mermaid">%s</div></div></div>'
    % (esc(TITLE), esc(heading), esc(code)))

extra = """
<style>
  .mapstage { display:flex; align-items:center; justify-content:center; min-height:0; overflow:hidden; }
  .mapstage .mermaid { width:100%; height:100%; display:flex; align-items:center; justify-content:center; }
  .mapstage .mermaid svg { max-width:100% !important; max-height:100% !important; height:auto; }
</style>
<script src="../../mermaid.min.js"></script>
<script>
  mermaid.initialize({ startOnLoad:true, securityLevel:'loose', theme:'base',
    themeVariables: {
      fontFamily:'-apple-system,"SF Pro Text","Helvetica Neue","Hiragino Kaku Gothic ProN","Yu Gothic",sans-serif', fontSize:'20px',
      primaryColor:'#f5f5f7', primaryBorderColor:'#a1a1a6', primaryTextColor:'#1d1d1f',
      lineColor:'#86868b', secondaryColor:'#ffffff', tertiaryColor:'#f0f0f2',
      clusterBkg:'#fafafa', clusterBorder:'#d2d2d7'
    }});
</script>
"""

tpl = open(os.path.join(SCRIPT_DIR, "slide-work-template.html"), encoding="utf-8").read()
doc = (tpl.replace("{{TITLE}}", esc("%s ｜ %s" % (TITLE, heading)))
          .replace("{{SLIDES}}", slide))
doc = doc.replace("</body>", extra + "</body>") if "</body>" in doc else doc + extra
out = os.path.join(SDIR, "map-slide-%s.html" % VIEW)
with open(out, "w", encoding="utf-8") as f:
    f.write(doc)
print("生成:", out)
