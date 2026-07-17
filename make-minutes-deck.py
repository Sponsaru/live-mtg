# -*- coding: utf-8 -*-
"""議事録をSlide Workデザインのデッキ（minutes-deck.html）へ決定論的に変換する。

AIは使わない：議事録は「省略なしの全文」が生命線なので、確定済みデータ
（final.json、無ければ data.json）からそのまま組み立てる（要約AIを挟むと
内容が落ちる・数分待つ・ハルシネーションの3リスクを抱えるため）。
デザインは slide-work-template.html（Slide Work正典CSS）に乗せる。
環境変数: SDIR（必須）, TITLE, LIVE_MTG_LANGUAGE
"""
import html
import json
import math
import os
import re
import sys

SDIR = os.environ.get("SDIR", "")
if not SDIR or not os.path.isdir(SDIR):
    sys.exit("SDIR未指定")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LANG = os.environ.get("LIVE_MTG_LANGUAGE", "ja")
TITLE = os.environ.get("TITLE", "会議" if LANG != "en" else "Meeting")


def tr(ja, en):
    return en if LANG == "en" else ja


def load_json(name):
    try:
        with open(os.path.join(SDIR, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


data = load_json("final.json") or load_json("data.json")
meta = load_json("meta.json")
if not data:
    sys.exit("議事データがありません")


def item_text(v):
    """dict/list混在の項目を、情報を落とさず1行のテキストへ。"""
    if v is None:
        return ""
    if isinstance(v, list):
        return " ・ ".join(filter(None, (item_text(x) for x in v)))
    if isinstance(v, dict):
        first = next((str(v[k]).strip() for k in ("label", "issue", "title", "what", "text", "q")
                      if str(v.get(k) or "").strip()), "")
        detail = next((str(v[k]).strip() for k in ("detail", "description", "answer")
                       if str(v.get(k) or "").strip() and str(v[k]).strip() != first), "")
        return (first + " — " + detail) if (first and detail) else (first or detail)
    return str(v).strip()


def todo_text(t):
    if not isinstance(t, dict):
        return item_text(t)
    due = ("（%s：%s）" % (tr("期限", "due"), t["due"])) if t.get("due") else ""
    return "%s：%s%s" % (t.get("who") or tr("未定", "TBD"), t.get("what", ""), due)


# ---- ページ分割：文字数から行数を見積もり、あふれる分は「（続き）」ページへ ----
CHARS_PER_LINE = 46      # panel幅での日本語の実測目安（控えめに見積もる）
LINES_PER_PAGE = 13


def chunk_items(items):
    pages, cur, used = [], [], 0.0
    for it in items:
        lines = max(1, math.ceil(len(it) / CHARS_PER_LINE)) + 0.5   # 0.5=項目間の余白
        if cur and used + lines > LINES_PER_PAGE:
            pages.append(cur)
            cur, used = [], 0.0
        cur.append(it)
        used += lines
    if cur:
        pages.append(cur)
    return pages


def esc(s):
    return html.escape(str(s or ""), quote=True)


slides = []

# ---- 表紙 ----
created = str(meta.get("created") or "")
# 清書のspeakersには注釈（「丹野健心（…・面接官）」等）が付くことがある。
# 表紙は括弧より前の名前だけを使う（2026-07-17 依頼者指摘：注釈が生で並び表紙が崩壊した）
def clean_name(v):
    t = re.split(r"[（(]", item_text(v))[0].strip(" 　・,、")
    return t if 0 < len(t) <= 12 else ""
names = []
for sp in (data.get("speakers") or []):
    n = clean_name(sp)
    if n and n not in names:
        names.append(n)
sub = tr("議事録", "Meeting minutes")
if names:
    sub += " ｜ " + "、".join(names[:6]) + (tr(" ほか", " and others") if len(names) > 6 else "")
slides.append(
    '<div class="slide cover center"><div class="corp-logo"></div><div class="stage">'
    '<div style="width:4.5rem;height:0.3125rem;border-radius:0.1875rem;background:var(--accent);"></div>'
    '<h1 class="bigstmt">%s</h1><p class="substmt">%s</p>'
    '<div style="font-size:1.1875rem;color:var(--gray);letter-spacing:.12em;">%s</div>'
    "</div></div>" % (esc(TITLE), esc(sub), esc(created)))

# ---- 要旨 ----
summary = item_text(data.get("summary"))
if summary:
    # 要旨もセクションページと同じ見出し様式（kick＋h1）。小さなkickだけだと題が読めない（2026-07-17 依頼者指摘）
    slides.append(
        '<div class="slide"><div class="corp-logo"></div>'
        '<div class="head"><div class="kick">%s</div><h1>%s</h1></div>'
        '<div class="stage"><p style="max-width:62rem;font-size:1.35rem;line-height:2.1;color:var(--ink);">%s</p></div>'
        "</div>" % (esc(tr("議事録", "Minutes")), esc(tr("要旨", "Summary")), esc(summary)))

# ---- セクション（全文・省略なし。あふれたら続きページ） ----
SECTIONS = [
    (tr("議題", "Agenda"), [item_text(x) for x in (data.get("agenda") or [])]),
    (tr("決定事項", "Decisions"), [item_text(x) for x in (data.get("decisions") or [])]),
    ("ToDo", [todo_text(x) for x in (data.get("todos") or [])]),
    (tr("主要論点", "Key points"), [item_text(x) for x in (data.get("points") or [])]),
    (tr("未解決・要確認", "Open questions"), [item_text(x) for x in (data.get("open") or [])]),
]
for name, items in SECTIONS:
    items = [x for x in items if x]
    if not items:
        continue
    for pi, page_items in enumerate(chunk_items(items)):
        cont = tr("（続き）", " (continued)") if pi else ""
        lis = "".join("<li>%s</li>" % esc(x) for x in page_items)
        slides.append(
            '<div class="slide"><div class="corp-logo"></div>'
            '<div class="head"><div class="kick">%s</div><h1>%s%s</h1></div>'
            '<div class="stage"><div class="panel" style="height:auto;">'
            '<ul style="line-height:2.05;">%s</ul></div></div></div>'
            % (esc(tr("議事録", "Minutes")), esc(name), esc(cont), lis))

# ---- 巻末資料：放射マップ・会話の関係（2026-07-17 依頼者指示で議事録PDFへ合体） ----
def mermaid_label(value):
    value = re.sub(r'[\r\n\[\](){}"#]+', " ", str(value or ""))
    return re.sub(r"\s+", " ", value).strip()[:42] or tr("未整理", "Unsorted")


live = load_json("data.json")   # mindmap/diagramはライブ版が最新のことがある（final.jsonはdiagramのみ持つ）
mm_topics = [t for t in ((data.get("mindmap") or live.get("mindmap")) or [])
             if isinstance(t, dict) and t.get("topic")]
need_mermaid = False
if mm_topics:
    lines = ["mindmap", "  root((%s))" % mermaid_label(TITLE)]
    for i, t in enumerate(mm_topics[:8]):
        lines.append('    b%d["%s"]' % (i, mermaid_label(t.get("topic"))))
        for j, g in enumerate((t.get("groups") or [])[:4]):
            lines.append('      b%dg%d["%s"]' % (i, j, mermaid_label(g.get("label"))))
            for k, it in enumerate((g.get("items") or [])[:5]):
                lines.append('        b%dg%di%d["%s"]' % (i, j, k, mermaid_label(item_text(it))))
    slides.append(
        '<div class="slide"><div class="corp-logo"></div>'
        '<div class="head"><div class="kick">%s</div><h1>%s</h1></div>'
        '<div class="stage mapstage"><div class="mermaid">%s</div></div></div>'
        % (esc(tr("議事録", "Minutes")), esc(tr("放射マップ", "Radial map")), esc("\n".join(lines))))
    need_mermaid = True

diagram = str(data.get("diagram") or live.get("diagram") or "").strip()
if diagram:
    slides.append(
        '<div class="slide"><div class="corp-logo"></div>'
        '<div class="head"><div class="kick">%s</div><h1>%s</h1></div>'
        '<div class="stage mapstage"><div class="mermaid">%s</div></div></div>'
        % (esc(tr("議事録", "Minutes")), esc(tr("会話の関係", "Relationships")), esc(diagram)))
    slides[-1] = slides[-1].replace('<div class="mermaid">', '<div class="mermaid relsplit">', 1)
    need_mermaid = True

MERMAID_EXTRA = r"""
<style>
  .mapstage { display:flex; align-items:center; justify-content:center; min-height:0; overflow:hidden; }
  .mapstage .mermaid { width:100%; height:100%; display:flex; align-items:center; justify-content:center; }
  .mapstage .mermaid svg { max-width:100% !important; max-height:100% !important; height:auto; }
  /* 会話の関係：流れごとの縦カラムを横並びに */
  .mapstage .relflows { display:flex; gap:2.4rem; align-items:stretch; justify-content:center; width:100%; height:100%; min-height:0; }
  .relflow { flex:1; min-width:0; min-height:0; display:flex; flex-direction:column; align-items:center; gap:.5rem; }
  .rf-title { font-size:1.05rem; font-weight:800; color:var(--gray); letter-spacing:.05em; flex:none; }
  .relflow .mermaid { flex:1 1 auto; min-height:0; width:100%; display:flex; align-items:flex-start; justify-content:center; }
  .relflow .mermaid svg { max-height:100% !important; width:auto !important; }
</style>
<script src="../../mermaid.min.js"></script>
<script>
  // 会話の関係：流れ（subgraph）ごとに縦カラムへ分割して横並びに。
  // エッジがsubgraphの外に書かれる形式にも対応：両端が同じ流れに属すエッジをそのカラムへ割当て、
  // 流れをまたぐエッジがある図は分割せず従来表示（2026-07-17）
  function __splitRelFlows(host){
    if(!host)return;
    var code=(host.textContent||'').trim(), lines=code.split('\n');
    if(!/^(flowchart|graph)/.test((lines[0]||'').trim()))return;
    var flows=[],cur=null,rest=[],bad=false;
    for(var i=1;i<lines.length;i++){var raw=lines[i],t=raw.trim(); if(!t)continue;
      if(t.indexOf('subgraph')===0){ if(cur){bad=true;break;} cur={title:t.slice(8).trim(),lines:[],ids:{}}; }
      else if(t==='end'&&cur){ flows.push(cur);cur=null; }
      else if(cur){ cur.lines.push(raw); }
      else rest.push(t);
    }
    if(bad||cur||flows.length<2)return;
    var KW=['flowchart','graph','subgraph','end','direction','TD','TB','LR','RL','BT','classDef','class','style','linkStyle','click'];
    function idsIn(t){
      t=t.replace(/"[^"]*"/g,'"').replace(/\|[^|]*\|/g,'|').replace(/\[[^\]]*\]/g,'#').replace(/\([^)]*\)/g,'#').replace(/\{[^}]*\}/g,'#');
      return (t.match(/[A-Za-z][A-Za-z0-9_]*/g)||[]).filter(function(x){return KW.indexOf(x)<0;});
    }
    flows.forEach(function(f){ f.lines.forEach(function(l){ idsIn(l).forEach(function(id){ f.ids[id]=1; }); }); });
    for(var r=0;r<rest.length;r++){var t=rest[r];
      if(/^(classDef|linkStyle|style|click|direction)\b/.test(t))continue;
      var ids=idsIn(t); if(!ids.length)continue;
      var owner=-1;
      for(var k=0;k<flows.length;k++){
        var all=true;
        for(var q=0;q<ids.length;q++){ if(!flows[k].ids[ids[q]]){all=false;break;} }
        if(all){owner=k;break;}
      }
      if(owner<0)return;   // 流れをまたぐ／所属不明 → 分割しない
      flows[owner].lines.push('  '+t);
    }
    var wrap=document.createElement('div'); wrap.className='relflows';
    flows.forEach(function(f){
      var col=document.createElement('div'); col.className='relflow';
      var title=f.title,mm=title.match(/\[(.*)\]\s*$/); if(mm)title=mm[1];
      title=title.replace(/^"+|"+$/g,'');
      var h=document.createElement('div'); h.className='rf-title'; h.textContent=title; col.appendChild(h);
      var d=document.createElement('div'); d.className='mermaid'; d.textContent='flowchart TD\n'+f.lines.join('\n'); col.appendChild(d);
      wrap.appendChild(col);
    });
    host.parentNode.replaceChild(wrap,host);
  }
  __splitRelFlows(document.querySelector('.relsplit'));
  mermaid.initialize({ startOnLoad:true, securityLevel:'loose', theme:'base',
    themeVariables: {
      fontFamily:'-apple-system,"SF Pro Text","Helvetica Neue","Hiragino Kaku Gothic ProN","Yu Gothic",sans-serif', fontSize:'20px',
      primaryColor:'#f5f5f7', primaryBorderColor:'#a1a1a6', primaryTextColor:'#1d1d1f',
      lineColor:'#86868b', secondaryColor:'#ffffff', tertiaryColor:'#f0f0f2',
      clusterBkg:'#fafafa', clusterBorder:'#d2d2d7'
    }});
</script>
"""

# ---- テンプレートへ ----
tpl = open(os.path.join(SCRIPT_DIR, "slide-work-template.html"), encoding="utf-8").read()
suffix = tr(" ｜ 議事録", " | Minutes")
doc = tpl.replace("{{TITLE}}", esc(TITLE + suffix)).replace("{{SLIDES}}", "\n".join(slides))
if need_mermaid:
    doc = doc.replace("</body>", MERMAID_EXTRA + "</body>") if "</body>" in doc else doc + MERMAID_EXTRA
out = os.path.join(SDIR, "minutes-deck.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(doc)
print("生成:", out, "／議事録デッキ／ページ数:", len(slides))
