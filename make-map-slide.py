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
flow = load_json("meeting-flow.json")
data = final or live
if not data:
    sys.exit("議事データがありません")
if not data.get("mindmap"):
    data = dict(data, mindmap=live.get("mindmap") or [])
if not str(data.get("diagram") or "").strip():
    data = dict(data, diagram=live.get("diagram") or "")


def item_text(v):
    if isinstance(v, dict):
        return str(v.get("label") or v.get("detail") or v.get("what") or v.get("text") or "").strip()
    return str(v or "").strip()


def mermaid_label(value):
    value = re.sub(r'[\r\n\[\](){}"#]+', " ", str(value or ""))
    return re.sub(r"\s+", " ", value).strip()[:42] or tr("未整理", "Unsorted")


def relation_items(diagram):
    """保存済みMermaidからノードと辺だけを安全に抽出する。

    AIが生成したグループ名・ラベルに `%`、カンマ、記号等が含まれると、
    Mermaidのバージョンによってはペースに失敗する。紙面用の図は、
    生のダイアグラムをそのまま実行せず、関係の内容だけを保持して描画する。
    """
    raw = str(diagram or "")
    nodes = {}
    for node_id, label in re.findall(r"\b([A-Za-z][\w-]*)\s*\[([^\]]+)\]", raw):
        nodes[node_id] = mermaid_label(label)
    normalized = re.sub(r"\b([A-Za-z][\w-]*)\s*\[[^\]]+\]", r"\1", raw)
    edge_re = re.compile(r"\b([A-Za-z][\w-]*)\s*(-->|-\.->)\s*(?:\|([^|]+)\|\s*)?([A-Za-z][\w-]*)\b")
    edges = edge_re.findall(normalized)
    return [{"source": mermaid_label(nodes.get(source, source)),
             "target": mermaid_label(nodes.get(target, target)),
             "label": mermaid_label(label) if str(label or "").strip() else tr("つながり", "Leads to"),
             "concern": arrow == "-.->"}
            for source, arrow, label, target in edges]


if VIEW == "radial":
    # mermaid版（2026-07-17 依頼者決定：多少の重なりは許容して従来の見た目）
    topics = [t for t in (data.get("mindmap") or []) if isinstance(t, dict) and t.get("topic")]
    if not topics:
        # 現在の正本は meeting-flow.json。旧mindmap配列がない会議も、
        # 議題と整理済み結果から同じ放射構造を復元する。
        for agenda in (flow.get("agendas") or [])[:8]:
            if not isinstance(agenda, dict) or not str(agenda.get("title") or "").strip():
                continue
            result = agenda.get("result") if isinstance(agenda.get("result"), dict) else {}
            groups = []
            for key, label in (("answers", tr("確認できたこと", "Confirmed")),
                               ("decisions", tr("決まったこと", "Decisions")),
                               ("actions", tr("次の行動", "Actions")),
                               ("unresolved", tr("未解決", "Open items"))):
                items = [item_text(value) for value in (result.get(key) or []) if item_text(value)]
                if items:
                    groups.append({"label": label, "items": items[:5]})
            topics.append({"topic": str(agenda.get("title") or "").strip(), "groups": groups})
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
    raw_diagram = str(data.get("diagram") or "").strip()
    if not raw_diagram:
        sys.exit(tr("会話の関係の素材がまだありません", "No relationship diagram yet"))
    relations = relation_items(raw_diagram)
    if not relations:
        sys.exit(tr("会話の関係を解析できません", "Could not parse relationship diagram"))
    heading = tr("会話の関係", "Relationships")


def esc(s):
    return html.escape(str(s or ""), quote=True)


if VIEW == "relation":
    content = '<div class="relation-grid">%s</div>' % "".join(
        '<div class="relation-item%s"><div class="relation-node">%s</div>'
        '<div class="relation-link"><span>%s</span><b>→</b></div>'
        '<div class="relation-node">%s</div></div>' %
        (" concern" if item["concern"] else "", esc(item["source"]),
         esc(item["label"]), esc(item["target"])) for item in relations[:8])
else:
    content = '<div class="mermaid">%s</div>' % esc(code)
slide = (
    '<div class="slide"><div class="corp-logo"></div>'
    '<div class="head"><div class="kick">%s</div><h1>%s</h1></div>'
    '<div class="stage mapstage">%s</div></div>'
    % (esc(TITLE), esc(heading), content))

extra = """
<style>
  .mapstage { display:flex; align-items:center; justify-content:center; min-height:0; overflow:hidden; }
  .mapstage .mermaid { width:100%; height:100%; display:flex; align-items:center; justify-content:center; }
  .mapstage .mermaid svg { max-width:100% !important; max-height:100% !important; height:auto; }
  .relation-grid { width:100%; display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:1.2rem; }
  .relation-item { min-height:9rem; display:grid; grid-template-columns:minmax(0,1fr) 10rem minmax(0,1fr);
                   gap:.75rem; align-items:center; border:1px solid var(--line); border-radius:var(--radius-card-flat);
                   padding:1rem; background:rgba(255,255,255,.9); }
  .relation-node { min-height:5.4rem; display:flex; align-items:center; justify-content:center; text-align:center;
                   padding:.7rem .8rem; border:1px solid var(--line); background:var(--panel);
                   font-size:1.28rem; line-height:1.35; font-weight:700; color:var(--ink); }
  .relation-link { text-align:center; color:var(--accent-ink); font-size:.95rem; line-height:1.3; font-weight:700; }
  .relation-link span { display:block; }
  .relation-link b { display:block; font-size:2rem; line-height:1; color:var(--accent); }
  .relation-item.concern .relation-link b { opacity:.55; }
</style>
"""
if VIEW == "radial":
    extra += """
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
