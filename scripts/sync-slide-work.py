#!/usr/bin/env python3
"""Vendor the neutral Slide Work design system into LiveMTG.

The source project is intentionally not needed at runtime. Run this script only
when slide-work/slide-patterns.html changes, then commit the generated files.
"""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT.parents[2] / "slide-work" / "slide-patterns.html"
SOURCE = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else DEFAULT_SOURCE

if not SOURCE.is_file():
    raise SystemExit(f"Slide Work catalog not found: {SOURCE}")

catalog = SOURCE.read_text(encoding="utf-8")
head = catalog.split("</head>", 1)[0]
head = re.sub(r"<title>.*?</title>", "<title>{{TITLE}}</title>", head, count=1, flags=re.S)

# LiveMTG distribution is product-neutral. Keep Slide Work's geometry and
# typography exactly, while removing customer themes and external assets.
head = re.sub(
    r"\s*/\* ===== テーマ：マイニチ.*?body\[data-theme=\"mainichi\"\]\s*\{.*?\n\s*\}",
    "", head, count=1, flags=re.S,
)
head = re.sub(r"--bg-image:\s*url\([^;]+;", "--bg-image: none;", head, count=1)
head = re.sub(r"--logo:\s*[^;]+;", "--logo: none;", head, count=1)
head = re.sub(r"<!--.*?(?:mainichi|毎日興業).*?-->", "", head, flags=re.S | re.I)
head = re.sub(r"mainichi", "neutral", head, flags=re.I)
remaining_brand = sorted(set(re.findall(r"mainichi|毎日興業|maiidai|#00A0E9", head, flags=re.I)))
if remaining_brand:
    raise SystemExit(f"Customer theme remained in the vendored Slide Work head: {remaining_brand}")

sprite_match = re.search(
    r"<!-- ===== SVG icon sprite ===== -->\s*(<svg.*?</svg>)", catalog, flags=re.S
)
if not sprite_match:
    raise SystemExit("SVG icon sprite not found")
sprite = sprite_match.group(1)

pattern_ids = (
    "P01", "P03", "P03b", "P04", "P05", "P07", "P10", "P12",
    "P17", "P22", "P23", "P31", "P33", "P33b", "P34", "P35",
    "P37", "P41", "P43", "P44", "P46", "P47",
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
    raise SystemExit(f"Slide Work patterns missing: {', '.join(missing)}")

extra = r'''
<style id="livemtg-slide-work-controls">
  body[data-theme="neutral"]{--bg-image:none;--logo:none}
  .pt{display:none!important}
  .sw-back,.sw-pdf{position:fixed;z-index:1000;border:1px solid var(--line);border-radius:62rem;
    background:rgba(255,255,255,.94);color:var(--ink);font:700 .9rem -apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif;
    padding:.7rem 1.1rem;text-decoration:none;box-shadow:0 .25rem 1.2rem rgba(0,0,0,.09);cursor:pointer}
  .sw-back{left:1.4rem;top:1.4rem}.sw-pdf{right:1.4rem;bottom:1.4rem}
  @media print{.sw-back,.sw-pdf{display:none!important}}
</style>'''

script = r'''
<script>
(function(){
  const slides=[...document.querySelectorAll('.slide')], total=slides.length;
  slides.forEach((slide,index)=>{
    slide.querySelectorAll('.pt').forEach(x=>x.remove());
    let page=slide.querySelector('.page');
    if(!slide.classList.contains('cover')){
      if(!page){page=document.createElement('div');page.className='page';slide.appendChild(page);}
      page.textContent=String(index+1).padStart(2,'0')+' / '+String(total).padStart(2,'0');
    }else if(page){page.remove();}
  });
  let printStyle;
  window.addEventListener('beforeprint',()=>{printStyle=document.createElement('style');printStyle.textContent='@page{size:1600px 900px;margin:0}';document.head.appendChild(printStyle);});
  window.addEventListener('afterprint',()=>{if(printStyle)printStyle.remove();});
})();
</script>'''

template = f'''{head}
{extra}
</head>
<body data-theme="neutral" data-design-system="slide-work">
<a class="sw-back" id="livemtg-back" href="/">← ダッシュボード</a>
<button class="sw-pdf" onclick="window.print()">PDF保存</button>
{sprite}
{{{{SLIDES}}}}
{script}
</body>
</html>
'''

guide = '''# LiveMTG Slide Work generation contract

Design source: slide-work/slide-patterns.html. Mode: neutral / hybrid.

- Copy one supplied P-pattern per slide and replace only its content.
- Never invent CSS, classes, inline layout, logos, images, numbers, quotes, or facts.
- Use MESSAGE patterns for conclusions and decisive statements; use INFORMATIVE patterns for evidence, comparisons, decisions, and actions. Never mix M and I inside one slide.
- Default flow: P01 cover → P03/P04 conclusion → evidence patterns → P31 actions → P47/P33 close.
- Do not repeat the same pattern consecutively. Use 7–12 slides according to actual content; do not pad.
- Every slide must contain `<div class="corp-logo"></div>`; neutral theme renders no company logo.
- Remove every `.pt` pattern label from final output. Page numbers are added automatically.
- Keep body copy short. MESSAGE: one claim and one reason. INFORMATIVE: 3–5 blocks, each label plus 1–2 lines.
- No emoji. Use only the supplied SVG icon symbols. No Mermaid in Slide Work decks.
- Source and date must accompany factual numbers and direct quotations. Omit anything unverified.
- Output only consecutive `<div class="slide">...</div>` elements, without code fences, `<html>`, `<style>`, or commentary.

Pattern roles:
P01 cover; P03/P03b single message; P04 three parallel points; P05 contrast; P07 three KPIs;
P10 recommendation + evidence + action; P12 verified quote; P17 three-layer structure; P22 comparison table;
P23 milestone timeline; P31 checklist; P33/P33b call to action; P34 readable agenda; P35 detailed statistics;
P37 detailed four-row list; P41 do/don't; P43 event timeline; P44 before/after; P46 three-stage plan;
P47 three takeaways + FAQ.
'''

(ROOT / "slide-work-template.html").write_text(template, encoding="utf-8")
(ROOT / "slide-work-pattern-examples.html").write_text(
    "\n\n".join(blocks[pid] for pid in pattern_ids) + "\n", encoding="utf-8"
)
(ROOT / "slide-work-guide.md").write_text(guide, encoding="utf-8")
print(f"Synced {len(pattern_ids)} Slide Work patterns from {SOURCE}")
