#!/usr/bin/env python3
"""Vendor canonical Slide Worker patterns with LiveMTG's own brand theme.

Run this only when Slide Worker is updated. LiveMTG never needs the shared CORE
at runtime: the canonical head, SVG sprite, browser editor, selected pattern
examples are copied into the package. Brand assets stay owned by LiveMTG.
"""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent / "Slide Worker"


def resolve_core() -> Path:
    if len(sys.argv) > 1:
        given = Path(sys.argv[1]).expanduser().resolve()
        return given.parent.parent if given.name == "slide-patterns.html" else given
    marker = WORKSPACE / ".slide-worker" / "core-path.txt"
    if marker.is_file():
        return Path(marker.read_text(encoding="utf-8").strip()).expanduser().resolve()
    raise SystemExit("Slide Worker COREを解決できません。先にsw_sync_workspaceを実行してください")


CORE = resolve_core()
SOURCE = CORE / "型と見本" / "slide-patterns.html"
DOC_SOURCE = CORE / "型と見本" / "doc-patterns.html"
if not SOURCE.is_file():
    raise SystemExit(f"Slide Worker catalog not found: {SOURCE}")
if not DOC_SOURCE.is_file():
    raise SystemExit(f"Slide Worker document catalog not found: {DOC_SOURCE}")

catalog = SOURCE.read_text(encoding="utf-8")
head = catalog.split("</head>", 1)[0]
head = re.sub(r"<title>.*?</title>", "<title>{{TITLE}}</title>", head, count=1, flags=re.S)
# check-deckが実コンテンツの未置換指示だけを検出できるよう、正典自身の説明コメントは命令形を外す。
head = head.replace("<img> に差し替え", "<img> に置換").replace("--logo-invert）に差し替えて", "--logo-invert）を使い")

theme = r'''
body[data-theme="live-mtg"] {
  --panel: #f5f6f8;
  --line: #e1e6ed;
  --accent: #0071e3;
  --accent-ink: #0066cc;
  --accent-soft: #eaf3fd;
  --accent2: #1d1d1f;
  --accent2-soft: #f1f1f3;
  --mark: #d9e8fb;
  --bar: #9ec8f0;
  --strong: #12151b;
  --bg-image: url("slide-bg.jpg");
  --logo: url("brand-logo.png");
  --logo-invert: none;
}
'''
head += "\n<style id=\"livemtg-brand-theme\">\n" + theme + "\n</style>"

sprite_match = re.search(r"<!-- ===== SVG icon sprite ===== -->\s*(<svg.*?</svg>)", catalog, flags=re.S)
if not sprite_match:
    raise SystemExit("SVG icon sprite not found")
sprite = sprite_match.group(1)

editor_match = re.search(
    r"<!-- slide-worker-browser-editor:begin -->.*?<!-- slide-worker-browser-editor:end -->",
    catalog, flags=re.S,
)
if not editor_match:
    raise SystemExit("Slide Worker browser editor not found")
editor = editor_match.group(0)

pattern_ids = (
    "P01", "P02", "P03", "P03b", "P04", "P05", "P07", "P10", "P12",
    "P17", "P22", "P23", "P31", "P33", "P33b", "P34", "P35", "P36",
    "P37", "P41", "P43", "P44", "P46", "P47", "P57",
)
markers = list(re.finditer(r"<!-- ===== (P(?:\d{2}[a-z]?))\b.*?-->", catalog))
blocks = {}
for index, marker in enumerate(markers):
    pid = marker.group(1)
    end = markers[index + 1].start() if index + 1 < len(markers) else catalog.find("<script", marker.end())
    if pid in pattern_ids and pid not in blocks:
        blocks[pid] = catalog[marker.start():end].strip()
missing = [pid for pid in pattern_ids if pid not in blocks]
if missing:
    raise SystemExit(f"Slide Worker patterns missing: {', '.join(missing)}")

controls = r'''
<style id="livemtg-slide-work-controls">
  .pt{display:none!important}
  .sw-back,.sw-pdf{position:fixed;z-index:1000;border:1px solid var(--line);border-radius:.75rem;
    background:rgba(255,255,255,.96);color:var(--ink);font:700 .9rem -apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif;
    padding:.7rem 1.1rem;text-decoration:none;box-shadow:0 .25rem 1.2rem rgba(0,0,0,.09);cursor:pointer}
  .sw-back{left:1.4rem;top:1.4rem}.sw-pdf{right:1.4rem;bottom:1.4rem}
  html.sw-render .sw-back,html.sw-render .sw-pdf{display:none!important}
  @media print{@page{size:1600px 900px;margin:0}.sw-back,.sw-pdf{display:none!important}}
</style>'''

runtime = r'''
<script>
(function(){
  if(new URLSearchParams(location.search).has('sw-render')) document.documentElement.classList.add('sw-render');
  const slides=[...document.querySelectorAll('.slide')], total=slides.length;
  slides.forEach((slide,index)=>{
    slide.querySelectorAll('.pt').forEach(x=>x.remove());
    let page=slide.querySelector('.page');
    if(!slide.classList.contains('cover')){
      if(!page){page=document.createElement('div');page.className='page';slide.appendChild(page);}
      page.textContent=String(index+1).padStart(2,'0')+' / '+String(total).padStart(2,'0');
    }else if(page){page.remove();}
  });
})();
</script>'''

template = f'''{head}
{controls}
</head>
<body data-theme="live-mtg" data-design-system="slide-worker-canonical">
<a class="sw-back" id="livemtg-back" href="/">ダッシュボード</a>
<button class="sw-pdf" onclick="window.print()">PDF保存</button>
{sprite}
{{{{SLIDES}}}}
{runtime}
{editor}
</body>
</html>
'''

guide = f'''# LiveMTG minutes deck generation contract

Design source: Slide Worker CORE/型と見本/slide-patterns.html
Brand: LiveMTG (slide-bg.jpg / brand-logo.png / blue #0071e3)
Mode: informative / standard

- Copy one verified IS/B pattern per slide and replace only its content.
- Minutes flow: P01 cover → P35 overview → P37 objective insights → P31 next moves → P37 agenda details → P57/P37 actions → P36 relationship summary.
- Never invent layout CSS, semantic color cards, icons, or decorative structures.
- Do not repeat the same visual structure without a content-driven reason.
- Keep P37 at four rows and three lines per row; paginate instead of shrinking text.
- Keep P31 at five checks and P57 at three actions; paginate instead of overfilling.
- Keep the LiveMTG background, logo, and blue palette. Accent color is reserved for conclusions, numbers, and keywords.
- Every factual number or quotation needs a source/time. Never fabricate missing information.
- No emoji and no Mermaid in the minutes deck.
- Remove .pt labels and retain the canonical browser editor.
'''

(ROOT / "slide-work-template.html").write_text(template, encoding="utf-8")
(ROOT / "slide-work-pattern-examples.html").write_text(
    "\n\n".join(blocks[pid] for pid in pattern_ids) + "\n", encoding="utf-8"
)
(ROOT / "slide-work-guide.md").write_text(guide, encoding="utf-8")

# 議事録は投影スライドではなく、doc-work正典のA4会議ペーパーを使う。
# 正典のCSSとブラウザ編集機能だけをvendorし、LiveMTGのロゴ・配色は製品側で所有する。
doc_catalog = DOC_SOURCE.read_text(encoding="utf-8")
doc_head = doc_catalog.split("</head>", 1)[0]
doc_head = re.sub(r"<title>.*?</title>", "<title>{{TITLE}}</title>", doc_head,
                  count=1, flags=re.S)
# 正典のカタログ説明は完成紙には不要で、check-docの例文検査対象にも含めない。
doc_head = re.sub(r"<!--.*?-->", "", doc_head, flags=re.S)
doc_editor_match = re.search(
    r"<!-- slide-worker-browser-editor:begin -->.*?<!-- slide-worker-browser-editor:end -->",
    doc_catalog, flags=re.S,
)
if not doc_editor_match:
    raise SystemExit("Slide Worker document browser editor not found")

doc_theme = r'''
body[data-theme="live-mtg"] {
  --ink: #12151b;
  --ink2: #343942;
  --gray: #68717d;
  --panel: #f5f8fc;
  --line: #dbe3ed;
  --accent: #0071e3;
  --accent-ink: #0066cc;
  --accent-soft: #eaf3fd;
  --accent2: #1d1d1f;
  --accent2-soft: #f1f1f3;
  --mark: #d9e8fb;
  --bar: #9ec8f0;
  --strong: #12151b;
  --bg-image: none;
  --logo: url("brand-logo.png");
  --logo-invert: none;
}
'''
doc_head += "\n<style id=\"livemtg-document-theme\">\n" + doc_theme + "\n</style>"
doc_controls = r'''
<style id="livemtg-document-controls">
  .sw-back,.sw-pdf{position:fixed;z-index:1000;border:1px solid var(--line);border-radius:.75rem;
    background:rgba(255,255,255,.96);color:var(--ink);font:700 14px -apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif;
    padding:11px 18px;text-decoration:none;box-shadow:0 4px 18px rgba(0,0,0,.09);cursor:pointer}
  .sw-back{left:20px;top:20px}.sw-pdf{right:20px;bottom:20px}
  html.sw-render .sw-back,html.sw-render .sw-pdf{display:none!important}
  @media print{.sw-back,.sw-pdf{display:none!important}}
</style>'''
doc_runtime = r'''
<script>
(function(){
  if(new URLSearchParams(location.search).has('sw-render')) document.documentElement.classList.add('sw-render');
  const sheets=[...document.querySelectorAll('.sheet')], total=sheets.length;
  sheets.forEach((sheet,index)=>{
    const page=document.createElement('div');
    page.className='doc-page-number';
    page.textContent=String(index+1)+' / '+String(total);
    sheet.appendChild(page);
  });
})();
</script>'''
doc_template = f'''{doc_head}
{doc_controls}
<style id="livemtg-minutes-paper-layout">
  .doc-page-number{{position:absolute;right:3.2rem;bottom:1.1rem;color:var(--gray);font-size:1.25rem;font-weight:600}}
  .paper-list{{list-style:none;display:grid;gap:.55rem}}
  .paper-list li{{display:grid;grid-template-columns:auto 1fr;gap:.75rem;font-size:1.75rem;line-height:1.6;color:var(--ink2)}}
  .paper-list .n{{font-weight:800;color:var(--accent-ink)}}
  .paper-list .label{{font-weight:800;color:var(--ink);margin-right:.35rem}}
  .agenda-status{{display:flex;gap:.65rem;flex-wrap:wrap}}
  .agenda-status .st-chip{{font-size:1.35rem}}
  .map-canvas{{position:relative;flex:1;min-height:0;border:1px solid var(--line);background:#fff;overflow:hidden}}
  .radial-lines{{position:absolute;inset:0;width:100%;height:100%}}
  .radial-lines line{{stroke:var(--bar);stroke-width:.45}}
  .radial-center,.radial-node{{position:absolute;transform:translate(-50%,-50%);border:1px solid var(--line);background:#fff;
    padding:.8rem 1rem;text-align:center;color:var(--ink);line-height:1.35}}
  .radial-center{{left:50%;top:50%;width:25rem;border:.25rem solid var(--accent);font-size:1.7rem;font-weight:800;background:var(--accent-soft)}}
  .radial-node{{width:23rem;font-size:1.5rem;font-weight:700}}
  .radial-node small{{display:block;margin-top:.35rem;color:var(--gray);font-size:1.25rem;font-weight:600}}
  .relation-list{{display:grid;gap:.65rem}}
  .relation-row{{display:grid;grid-template-columns:1fr 15rem 1fr;gap:.7rem;align-items:center}}
  .relation-node{{border:1px solid var(--line);padding:.65rem .8rem;font-size:1.5rem;line-height:1.4;font-weight:700;color:var(--ink)}}
  .relation-arrow{{position:relative;text-align:center;color:var(--accent-ink);font-size:1.25rem;font-weight:700;padding-bottom:.7rem}}
  .relation-arrow:after{{content:'→';position:absolute;left:0;right:0;bottom:-.35rem;font-size:2.2rem;line-height:1;color:var(--accent)}}
  @media print{{.doc-page-number{{bottom:1.1rem}}}}
</style>
</head>
<body data-theme="live-mtg" data-design-system="slide-worker-doc-canonical">
<a class="sw-back" href="/">ダッシュボード</a>
<button class="sw-pdf" onclick="window.print()">PDF保存</button>
{{{{SHEETS}}}}
{doc_runtime}
{doc_editor_match.group(0)}
</body>
</html>
'''
(ROOT / "minutes-paper-template.html").write_text(doc_template, encoding="utf-8")
print(f"Synced {len(pattern_ids)} slide patterns and the canonical A4 document system with the LiveMTG brand")
