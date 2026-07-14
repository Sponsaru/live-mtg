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

body = '''<div class="slide mindmap-page">
  <div class="head"><div class="kick">MEETING MIND MAP</div><h1>%s</h1><div class="hsub">%s</div></div>
  <div class="stage"><div class="tree-map" data-tree>
    <svg class="tree-lines" aria-hidden="true"></svg>
    <div class="tree-root-wrap">%s</div>
    <div class="tree-branches">%s</div>
  </div></div>
</div>''' % (html.escape(title), tr("議論の全体像・決定・次の行動", "Discussion overview, decisions, and next actions"), node(label(title, 32), "root", "root"), "".join(rows))

with open(os.path.join(script_dir, "slides-template.html"), encoding="utf-8") as f:
    template = f.read()
out = (template.replace("{{TITLE}}", title + tr(" ｜ 議事マインドマップ", " | Meeting mind map"))
               .replace("{{THEME}}", theme)
               .replace("{{DATA_UPDATED}}", json.dumps(str(data.get("updated", "")), ensure_ascii=False))
               .replace("{{SLIDES}}", body))
with open(os.path.join(sdir, "mindmap.html"), "w", encoding="utf-8") as f:
    f.write(out)
print("生成:", os.path.join(sdir, "mindmap.html"), "／マインドマップ: 1画面")
