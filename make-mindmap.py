#!/usr/bin/env python3
"""data.jsonから、推測を足さず1画面の階層型マインドマップHTMLを生成する。"""
import html, json, os, re

sdir = os.environ.get("SDIR", "")
language = os.environ.get("LIVE_MTG_LANGUAGE", "ja")
def tr(ja, en): return en if language == "en" else ja
title = os.environ.get("TITLE", tr("会議", "Meeting"))
theme = "neutral"
script_dir = os.path.dirname(os.path.abspath(__file__))
if not sdir or not os.path.isfile(os.path.join(sdir, "data.json")):
    raise SystemExit("会議データがありません")

with open(os.path.join(sdir, "data.json"), encoding="utf-8") as f:
    data = json.load(f)

def label(value, limit=54):
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    value = value.translate(str.maketrans({"[":"［", "]":"］", "(":"（", ")":"）",
                                           "{":"｛", "}":"｝", '"':"”", "#":"＃"}))
    return value[:limit] + ("…" if len(value) > limit else "")

def full(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()

def number(value):
    try: return float(value or 0)
    except (TypeError, ValueError): return 0

def headline(value):
    value = full(value)
    first = re.split(r"[、。:：]", value, maxsplit=1)[0].strip()
    return (first or value)[:30]

def items(key, limit):
    vals = data.get(key) if isinstance(data.get(key), list) else []
    out = []
    for x in vals[:limit]:
        if isinstance(x, dict):
            text = ((x.get("who") or tr("未定", "Unassigned")) + (": " if language == "en" else "：") + (x.get("what") or "")).strip("：: ")
        else:
            text = str(x)
        if full(text): out.append(full(text))
    return out

branches = []
if isinstance(data.get("mindmap"), list) and data["mindmap"]:
    for topic in data["mindmap"][:8]:
        groups = []
        for group in (topic.get("groups") or [])[:4]:
            mapped = []
            for x in (group.get("items") or [])[:5]:
                if isinstance(x, dict):
                    mapped.append({"label": full(x.get("label")), "detail": full(x.get("detail") or x.get("label")),
                                   "status": full(x.get("status")), "source": full(x.get("source"))})
                elif full(x):
                    mapped.append({"label": headline(x), "detail": full(x), "status":"", "source":""})
            groups.append({"label": full(group.get("label")), "items": mapped})
        if label(topic.get("topic")) and groups:
            branches.append((label(topic.get("topic"), 28), groups))
else:
    summary = full(data.get("summary"))
    if summary: branches.append((tr("要旨", "Summary"), [{"label":tr("全体像", "Overview"), "items":[{"label":headline(summary), "detail":summary, "status":tr("要旨", "Summary"), "source":""}]}]))
    for heading, key, count in ((tr("主要論点", "Key points"), "points", 3), (tr("決定事項", "Decisions"), "decisions", 3),
                                ("ToDo", "todos", 3), (tr("未解決", "Open questions"), "open", 3)):
        vals = items(key, count)
        if vals: branches.append((heading, [{"label":tr("内容", "Details"), "items":[{"label":headline(x), "detail":full(x), "status":heading, "source":""} for x in vals]}]))

prep = data.get("preparation") if isinstance(data.get("preparation"), dict) else {}
if prep and not any(("事前準備" in x[0] or "Preparation" in x[0]) for x in branches):
    pgroups = []
    def prep_one(group_label, item_label, detail):
        if full(detail):
            pgroups.append({"label":group_label, "items":[{"label":item_label, "detail":full(detail), "status":tr("事前準備", "Preparation"), "source":""}]})
    prep_one(tr("構想・狙い", "Intent"), tr("事前準備で共有した構想", "Preparation brief"), prep.get("brief"))
    prep_one(tr("着地点", "Outcome"), tr("会議の成功条件", "Meeting success criteria"), prep.get("outcome"))
    prep_one(tr("相手情報", "Counterpart"), tr("相手の状況・関心", "Counterpart context"), prep.get("counterpart"))
    for glabel, key in ((tr("検証する仮説", "Hypotheses"),"hypotheses"),(tr("会議で聞くこと", "Questions to ask"),"questions"),
                        (tr("懸念・見落とし", "Risks"),"risks"),(tr("避けること", "Avoid"),"avoid")):
        vals = [full(x) for x in (prep.get(key) or []) if full(x)]
        if vals: pgroups.append({"label":glabel, "items":[{"label":headline(x),"detail":x,"status":tr("事前準備", "Preparation"),"source":""} for x in vals[:5]]})
    if pgroups: branches.insert(0, (tr("事前準備", "Preparation"), pgroups))

def node(text, cls, node_id, parent="", detail="", status="", source=""):
    p = (' data-parent="%s"' % parent) if parent else ""
    extra = ''
    if detail:
        extra = ' data-title="%s" data-detail="%s" data-status="%s" data-source="%s"' % tuple(
            html.escape(str(x), quote=True) for x in (text, detail, status, source))
    return '<div class="tree-node %s" data-node-id="%s"%s%s>%s</div>' % (
        cls, node_id, p, extra, html.escape(text))

rows = []
for i, (heading, groups) in enumerate(branches):
    bid = "branch-%d" % i
    leaves = []
    for j, group in enumerate(groups):
        gid = "%s-group-%d" % (bid, j)
        item_rows = []
        for k, x in enumerate(group["items"]):
            iid = "%s-item-%d" % (gid, k)
            detail = ((('<span class="detail-status">%s</span><br>' % html.escape(x.get("status", "")))
                       if x.get("status") else "") + html.escape(x.get("detail", "")) +
                      (('<br><small>%s%s</small>' % (tr("根拠：", "Source: "), html.escape(x.get("source", "")))) if x.get("source") else ""))
            item_rows.append('<div class="tree-detailrow">%s<div class="tree-node tree-detail" data-node-id="%s-detail" data-parent="%s">%s</div></div>' %
                             (node(x["label"], "item tone-%d" % (i % 5), iid, gid), iid, iid, detail))
        its = "".join(item_rows)
        leaves.append('<div class="tree-subbranch" data-group>%s<div class="tree-items">%s</div></div>' %
                      (node(group["label"], "group", gid, bid), its))
    rows.append('''<section class="tree-branch%s" data-branch>
      <div class="branch-head">%s</div>
      <div class="tree-leaves">%s</div>
    </section>''' % ("" if i == 0 else " collapsed", node(heading, "branch tone-%d" % (i % 5), bid, "root"), "".join(leaves)))

timeline_rows = []
for i, entry in enumerate((data.get("log") or [])[-30:]):
    if not isinstance(entry, dict):
        continue
    who, text, at = full(entry.get("who")) or tr("不明", "Unknown"), full(entry.get("text")), full(entry.get("at"))
    if text:
        timeline_rows.append('''<div class="generated-timeline-item">
          <div class="generated-timeline-at">%s</div><div class="generated-timeline-card"><b>%s</b><p>%s</p></div>
        </div>''' % (html.escape(at or str(i + 1)), html.escape(who), html.escape(text)))

diagram = str(data.get("diagram") or "").strip()
if not diagram:
    diagram = tr("flowchart LR\n  A[会話の関係を整理中]", "flowchart LR\n  A[Analyzing relationships]")

def mermaid_label(value):
    value = re.sub(r'[\r\n\[\](){}"#]+', ' ', str(value or ""))
    return re.sub(r"\s+", " ", value).strip()[:42] or tr("未整理", "Unsorted")

conversation_map = data.get("conversationMap") if isinstance(data.get("conversationMap"), dict) else {}
conversation_types = [x for x in (conversation_map.get("types") or [])
                      if isinstance(x, dict) and full(x.get("type")) and number(x.get("share")) > 0]
radial_branches = []
for row in conversation_types[:8]:
    groups = []
    for topic in (row.get("topics") or [])[:4]:
        if isinstance(topic, dict):
            topic_label = full(topic.get("label") or topic.get("topic"))
            summary = full(topic.get("summary") or topic.get("detail"))
        else:
            topic_label, summary = full(topic), ""
        if topic_label:
            groups.append({"label": topic_label, "items": [{"label": summary or tr("このタイプの会話", "Conversation of this type")} ]})
    examples = [full(x) for x in (row.get("examples") or []) if full(x)]
    if examples:
        groups.append({"label": tr("代表的な発言", "Representative remarks"),
                       "items": [{"label": x} for x in examples[:3]]})
    radial_branches.append(("%s %d%%" % (full(row.get("type")), int(row.get("share") or 0)), groups))
if not radial_branches:
    radial_branches = branches

# 論点マップと役割を分け、放射マップは会話タイプ→具体話題→代表内容を描く。
radial_lines = ["mindmap", "  root((%s))" % mermaid_label(tr("会話の構成", "Conversation mix") if conversation_types else title)]
for i, (heading, groups) in enumerate(radial_branches[:8]):
    radial_lines.append('    b%d["%s"]' % (i, mermaid_label(heading)))
    for j, group in enumerate(groups[:4]):
        radial_lines.append('      b%dg%d["%s"]' % (i, j, mermaid_label(group.get("label"))))
        for k, item in enumerate((group.get("items") or [])[:5]):
            radial_lines.append('        b%dg%di%d["%s"]' % (i, j, k, mermaid_label(item.get("label") if isinstance(item, dict) else item)))
radial_diagram = "\n".join(radial_lines)
palette = ["#1769c2", "#7552a3", "#168077", "#98600a", "#bc3d46", "#4472a8", "#7d6c9c", "#5e7d68"]
balance = ""
if conversation_types:
    segments = "".join('<span style="width:%d%%;--seg-color:%s" title="%s %d%%"></span>' %
                       (int(row.get("share") or 0), palette[i % len(palette)],
                        html.escape(full(row.get("type")), quote=True), int(row.get("share") or 0))
                       for i, row in enumerate(conversation_types))
    legend = "".join('<b style="--seg-color:%s">%s %d%%</b>' %
                     (palette[i % len(palette)], html.escape(full(row.get("type"))), int(row.get("share") or 0))
                     for i, row in enumerate(conversation_types))
    insights = "".join("<em>%s</em>" % html.escape(full(value))
                       for value in (conversation_map.get("insights") or [])[:3] if full(value))
    state = tr("清書後の推定", "Final estimate") if conversation_map.get("status") == "final" else tr("録音中の暫定値", "Live estimate")
    balance = '<div class="generated-conversation-balance"><div><strong>%s</strong><small>%s</small></div><p>%s</p><section>%s</section>%s</div>' % (
        tr("会話の配分", "Conversation balance"), state, segments, legend,
        ('<aside>%s</aside>' % insights) if insights else "")


body = '''<div class="slide mindmap-page">
  <div class="head"><div class="kick">MEETING MIND MAP</div><h1>%s</h1><div class="hsub">%s</div>
    <div class="generated-maptools"><button data-generated-map="topics">%s</button><button data-generated-map="radial">%s</button><button data-generated-map="relation">%s</button><button data-generated-map="timeline">%s</button></div>
  </div>
  <div class="stage">
    <div class="generated-map-view generated-radial" data-generated-view="radial">%s<div class="mermaid">%s</div></div>
    <div class="generated-map-view generated-relation" data-generated-view="relation"><div class="mermaid">%s</div></div>
    <div class="generated-map-view generated-topics" data-generated-view="topics"><div class="tree-map" data-tree>
    <svg class="tree-lines" aria-hidden="true"></svg>
    <div class="tree-root-wrap">%s</div>
    <div class="tree-branches">%s</div>
    </div></div>
    <div class="generated-map-view generated-timeline" data-generated-view="timeline">%s</div>
  </div>
</div>''' % (html.escape(title), tr("マインドマップ・放射マップ・会話の関係・時系列", "Mind map, radial map, relationships, and timeline"),
             tr("マインドマップ", "Mind map"), tr("放射マップ", "Radial map"), tr("会話の関係", "Conversation relationships"), tr("時系列", "Timeline"),
             balance, html.escape(radial_diagram), html.escape(diagram), node(label(title, 32), "root", "root"), "".join(rows),
             "".join(timeline_rows) or '<div class="generated-empty">%s</div>' % tr("発言を整理中です", "Organizing the conversation"))


with open(os.path.join(script_dir, "slides-template.html"), encoding="utf-8") as f:
    template = f.read()
out = (template.replace("{{TITLE}}", title + tr(" ｜ 議事マインドマップ", " | Meeting mind map"))
               .replace("{{THEME}}", theme)
               .replace("{{DATA_UPDATED}}", json.dumps(str(data.get("updated", "")), ensure_ascii=False))
               .replace("{{SLIDES}}", body))
with open(os.path.join(sdir, "mindmap.html"), "w", encoding="utf-8") as f:
    f.write(out)
print("生成:", os.path.join(sdir, "mindmap.html"), "／マインドマップ: 1画面")
