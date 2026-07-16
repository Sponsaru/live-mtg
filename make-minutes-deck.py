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
speakers = [s for s in (data.get("speakers") or []) if s]
sub = tr("議事録", "Meeting minutes")
if speakers:
    sub += " ｜ " + "、".join(speakers[:8])
slides.append(
    '<div class="slide cover center"><div class="corp-logo"></div><div class="stage">'
    '<div style="width:4.5rem;height:0.3125rem;border-radius:0.1875rem;background:var(--accent);"></div>'
    '<h1 class="bigstmt">%s</h1><p class="substmt">%s</p>'
    '<div style="font-size:1.1875rem;color:var(--gray);letter-spacing:.12em;">%s</div>'
    "</div></div>" % (esc(TITLE), esc(sub), esc(created)))

# ---- 要旨 ----
summary = item_text(data.get("summary"))
if summary:
    slides.append(
        '<div class="slide center"><div class="corp-logo"></div><div class="stage">'
        '<div class="kick">%s</div>'
        '<p class="substmt" style="max-width:60rem;text-align:left;line-height:2;">%s</p>'
        "</div></div>" % (esc(tr("要旨", "Summary")), esc(summary)))

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

# ---- テンプレートへ ----
tpl = open(os.path.join(SCRIPT_DIR, "slide-work-template.html"), encoding="utf-8").read()
suffix = tr(" ｜ 議事録", " | Minutes")
doc = tpl.replace("{{TITLE}}", esc(TITLE + suffix)).replace("{{SLIDES}}", "\n".join(slides))
out = os.path.join(SDIR, "minutes-deck.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(doc)
print("生成:", out, "／議事録デッキ／ページ数:", len(slides))
